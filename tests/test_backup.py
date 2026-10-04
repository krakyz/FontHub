"""Restore committed WAL data and font bytes; reject corruption and overwrite."""

# Direct execution from the repository root keeps application imports available.
import sys as _test_sys
from pathlib import Path as _TestPath
_test_sys.path.insert(0,str(_TestPath(__file__).resolve().parents[1]))
import hashlib
import os
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from contextlib import closing
from archive_backup import backup,restore

with tempfile.TemporaryDirectory() as temporary:
    root=Path(temporary).resolve();data=root/'data';data.mkdir();(data/'originals').mkdir()
    content=b'immutable font fixture';checksum=hashlib.sha256(content).hexdigest()
    original=data/'originals'/(checksum+'.ttf');original.write_bytes(content)
    # Leave a WAL connection open: copying catalog.sqlite alone would miss rows.
    con=sqlite3.connect(data/'catalog.sqlite');con.execute('PRAGMA journal_mode=WAL')
    con.execute('CREATE TABLE files(hash TEXT,path TEXT)');con.execute('INSERT INTO files VALUES(?,?)',(checksum,str(original)));con.commit()
    archive=root/'backup.zip';backup(data,archive)
    restored=root/'restored';restore(archive,restored)
    with closing(sqlite3.connect(restored/'catalog.sqlite')) as check:
        assert Path(check.execute('SELECT path FROM files').fetchone()[0]).resolve()==(restored/'originals'/original.name).resolve()
    assert (restored/'originals'/original.name).read_bytes()==content
    try: restore(archive,restored)
    except ValueError: pass
    else: raise AssertionError('Overwrite accepted')
    damaged=root/'damaged.zip'
    with zipfile.ZipFile(archive) as source,zipfile.ZipFile(damaged,'w') as target:
        for name in source.namelist(): target.writestr(name,b'bad font' if name.startswith('originals/') else source.read(name))
    try: restore(damaged,root/'damaged')
    except ValueError: pass
    else: raise AssertionError('Corrupt backup accepted')
    assert not (root/'damaged').exists()
    con.close()
    print('PASS: WAL backup, portable restore, SHA-256 verification, overwrite protection')
