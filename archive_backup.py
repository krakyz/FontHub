"""Portable verified backups; never overwrite an existing installation.

Archive imports/reanalysis are held by the same OS lock while the database and
immutable files are captured. SQLite's backup API includes committed WAL data.
Indexer snapshots are wholly inside SQLite; its response files are diagnostic
evidence and may still grow during sync, so stop sync for a complete evidence set.
Git checkouts are regenerable and deliberately excluded. External inbox folders
are outside this backup: stop incoming transfers and back them up separately.
"""
import argparse
import hashlib
import json
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import nullcontext,closing
from pathlib import Path, PurePosixPath
from archive_io import archive_lock,publish


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def backup(data, destination, kind='archive'):
    data=Path(data).resolve();destination=Path(destination).resolve()
    database='catalog.sqlite' if kind=='archive' else 'index.sqlite'
    if not (data/database).is_file(): raise ValueError('Database does not exist')
    if destination.exists(): raise ValueError('Backup destination already exists')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with archive_lock(data/'import.lock') if kind=='archive' else nullcontext():
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);snapshot=root/database
            with closing(sqlite3.connect(f'{(data/database).as_uri()}?mode=ro',uri=True)) as source:
                with closing(sqlite3.connect(snapshot)) as target: source.backup(target)
            members=[(snapshot,database)]
            folders=['originals','previews','exports','repairs','quarantine','inbox','sources'] if kind=='archive' else ['responses']
            for folder in folders:
                members.extend((path,path.relative_to(data).as_posix()) for path in sorted((data/folder).rglob('*')) if path.is_file() and path.suffix not in {'.tmp','.part'})
            if kind=='archive':
                members.extend((path,path.name) for path in data.glob('*.json'))
            manifest={'version':1,'kind':kind,'data_root':str(data),'files':{}}
            # Hash exactly the bytes that enter the ZIP, including mutable JSON
            # settings/evidence. Reading twice would race a concurrently written file.
            partial=destination.with_name(destination.name+'.part')
            try:
                with zipfile.ZipFile(partial,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as output:
                    for path,name in members:
                        checksum=hashlib.sha256()
                        with path.open('rb') as source,output.open(name,'w',force_zip64=True) as target:
                            while block:=source.read(1024*1024): checksum.update(block);target.write(block)
                        manifest['files'][name]=checksum.hexdigest()
                    output.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
                publish(partial,destination)
            finally: partial.unlink(missing_ok=True)
    return manifest


def restore(archive, destination):
    destination=Path(destination).resolve()
    if destination.exists(): raise ValueError('Restore requires a new directory')
    destination.parent.mkdir(parents=True,exist_ok=True)
    # Verify in a sibling staging folder; a damaged ZIP never becomes an archive.
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        root=Path(temporary)/'restored';root.mkdir()
        with zipfile.ZipFile(archive) as source:
            manifest=json.loads(source.read('manifest.json'))
            if manifest.get('version')!=1 or manifest.get('kind') not in {'archive','indexer'}: raise ValueError('Unsupported backup')
            if set(source.namelist())!=set(manifest['files'])|{'manifest.json'} or len(source.namelist())!=len(manifest['files'])+1: raise ValueError('Unexpected ZIP members')
            for name,checksum in manifest['files'].items():
                relative=PurePosixPath(name)
                if relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name: raise ValueError('Unsafe backup path')
                path=root.joinpath(*relative.parts);path.parent.mkdir(parents=True,exist_ok=True)
                with source.open(name) as input,path.open('wb') as output: shutil.copyfileobj(input,output)
                if digest(path)!=checksum: raise ValueError('Checksum mismatch: '+name)
        database='catalog.sqlite' if manifest['kind']=='archive' else 'index.sqlite'
        with closing(sqlite3.connect(root/database)) as con,con:
            if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('Database integrity check failed')
            if manifest['kind']=='archive':
                # Database paths are installation-specific. Filenames are content
                # addressed and portable between Windows and Linux.
                old_root=manifest['data_root'].replace('\\','/').rstrip('/')
                def relocate(value):
                    normalized=value.replace('\\','/')
                    if normalized.startswith(old_root+'/'): return str(destination/normalized[len(old_root)+1:])
                    return value  # External inbox: original path is still explicit.
                for hash,path in con.execute('SELECT hash,path FROM files').fetchall():
                    filename=path.replace('\\','/').rsplit('/',1)[-1]
                    original=root/'originals'/filename
                    if not original.is_file() or digest(original)!=hash: raise ValueError('Original missing or corrupt: '+hash)
                    con.execute('UPDATE files SET path=? WHERE hash=?',(str(destination/'originals'/filename),hash))
                if con.execute("SELECT 1 FROM sqlite_master WHERE name='import_jobs'").fetchone():
                    for hash,origin,input_path,target in con.execute('SELECT hash,source,input_path,target FROM import_jobs').fetchall():
                        con.execute('UPDATE import_jobs SET input_path=?,target=? WHERE hash=? AND source=?',(relocate(input_path),relocate(target),hash,origin))
                if con.execute("SELECT 1 FROM sqlite_master WHERE name='work_queue'").fetchone():
                    for id,kind,identity,path in con.execute('SELECT id,kind,identity,input_path FROM work_queue').fetchall():
                        con.execute('UPDATE work_queue SET identity=?,input_path=? WHERE id=?',(relocate(identity) if kind=='import' else identity,relocate(path),id))
                if con.execute("SELECT 1 FROM sqlite_master WHERE name='repair_candidates'").fetchone():
                    for repair_hash,path in con.execute('SELECT hash,path FROM repair_candidates WHERE path IS NOT NULL').fetchall():
                        con.execute('UPDATE repair_candidates SET path=? WHERE hash=?',(relocate(path),repair_hash))
        root.rename(destination)
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    command=commands.add_parser('backup');command.add_argument('--data',required=True);command.add_argument('--output',required=True);command.add_argument('--kind',choices=['archive','indexer'],default='archive')
    command=commands.add_parser('restore');command.add_argument('--input',required=True);command.add_argument('--data',required=True)
    args=parser.parse_args()
    try:
        result=backup(args.data,args.output,args.kind) if args.command=='backup' else restore(args.input,args.data)
        print(json.dumps({'status':'ok','kind':result['kind'],'files':len(result['files'])}))
    except (ValueError,OSError,zipfile.BadZipFile) as error: parser.exit(1,str(error)+'\n')
