import hashlib
import io
import json
import os
import shutil
import sqlite3
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file, abort
from fontTools.ttLib import TTFont, TTCollection, TTLibError
from font_analysis import usable_cmap
from archive_io import publish, copy_original, archive_lock
from language_coverage import analyze_languages, orthographies
from source_languages import source_languages, matches_source_language
from font_sources import SOURCES, SOURCE_NAMES, check_source
import indexer_client as google_fonts
import catalog_search
import import_queue
import font_exports
import font_downloads
import archive_pipeline
import coverage_cache
import font_validation
import original_checks
import check_routes
import font_repairs

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('FONTHUB_DATA', ROOT / 'data')).resolve()
INBOX = Path(os.environ.get('FONTHUB_INBOX', DATA / 'inbox')).resolve()
for folder in [DATA, INBOX, DATA / 'originals', DATA / 'previews', DATA / 'quarantine']:
    folder.mkdir(parents=True, exist_ok=True)
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024
lock = threading.Lock()
google_lock = threading.Lock()
GOOGLE_DATA = DATA / 'sources' / 'google-fonts'
EXTENSIONS = {'.ttf', '.otf', '.woff', '.woff2', '.ttc', '.otc'}
ALPHABETS = {'Русский': 'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя', 'English': 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'}


@contextmanager
def db():
    con = sqlite3.connect(DATA / 'catalog.sqlite', timeout=30)
    con.row_factory = sqlite3.Row
    catalog_search.register(con)
    con.execute("PRAGMA synchronous=FULL")
    try:
        with con:
            yield con
    finally:
        con.close()


with db() as con:
    con.execute('PRAGMA journal_mode=WAL')
    con.executescript('''
    CREATE TABLE IF NOT EXISTS files (hash TEXT PRIMARY KEY, name TEXT, path TEXT);
    CREATE TABLE IF NOT EXISTS faces (id TEXT PRIMARY KEY, hash TEXT, metadata TEXT);
    CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, name TEXT, status TEXT, message TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
    ''')
    if 'added_at' not in {row['name'] for row in con.execute('PRAGMA table_info(files)')}:
        con.execute('ALTER TABLE files ADD COLUMN added_at TEXT')
        con.execute("UPDATE files SET added_at=(SELECT MIN(created) FROM events WHERE events.name=files.name AND status='ok')")
    if 'source' not in {row['name'] for row in con.execute('PRAGMA table_info(files)')}:
        con.execute("ALTER TABLE files ADD COLUMN source TEXT NOT NULL DEFAULT 'local'")
    con.execute('CREATE TABLE IF NOT EXISTS source_checks (id TEXT PRIMARY KEY, result TEXT, checked TEXT)')
    con.execute('CREATE TABLE IF NOT EXISTS source_settings (id TEXT PRIMARY KEY, enabled INTEGER NOT NULL)')
    con.execute('CREATE TABLE IF NOT EXISTS import_jobs(hash TEXT, source TEXT, input_path TEXT, filename TEXT, target TEXT, phase TEXT, PRIMARY KEY(hash,source))')
    # One immutable binary may have independent catalogue instances per source.
    # Legacy files.source is retained for schema compatibility; origins owns identity.
    con.execute('CREATE TABLE IF NOT EXISTS origins (hash TEXT, source TEXT, added_at TEXT, PRIMARY KEY(hash,source))')
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='origin_migration'").fetchone():
        con.execute('INSERT OR IGNORE INTO origins SELECT hash,source,added_at FROM files')
        con.execute('CREATE TABLE origin_migration(done INTEGER)')
    catalog_search.initialize(con)
    import_queue.initialize(con)
    font_downloads.initialize(con)
    coverage_cache.initialize(con)
    archive_pipeline.initialize(con)
    original_checks.initialize(con)
    font_repairs.initialize(con)


def origin_faces(con):
    """Expose logical instances while reusing physical metadata and WOFF2 files."""
    return [dict(json.loads(row['metadata']), id=row['id'] + '~' + row['source'], source=row['source'], added_at=row['added_at'])
            for row in con.execute('SELECT faces.id, faces.metadata, origins.source, origins.added_at FROM faces JOIN origins ON origins.hash=faces.hash WHERE NOT EXISTS(SELECT 1 FROM held_origins h WHERE h.hash=origins.hash AND h.source=origins.source)')]


def resolve_face(con, public_id):
    """Resolve source-qualified IDs; legacy IDs select their surviving origin."""
    base, _, source_id = public_id.partition('~')
    row = con.execute('SELECT faces.metadata, origins.source, origins.added_at FROM faces JOIN origins ON origins.hash=faces.hash WHERE faces.id=? AND NOT EXISTS(SELECT 1 FROM held_origins h WHERE h.hash=origins.hash AND h.source=origins.source) AND (? = \'\' OR origins.source=?) ORDER BY origins.source LIMIT 1', (base, source_id, source_id)).fetchone()
    if row is None:
        abort(404)
    return dict(json.loads(row['metadata']), id=base + '~' + row['source'], source=row['source'], added_at=row['added_at'])



def event(name, status, message):
    with db() as con:
        con.execute('INSERT INTO events(name,status,message) VALUES(?,?,?)', (name, status, message))


def metadata(font, face_id):
    names = font['name']
    def name(*ids):
        for number in ids:
            value = names.getDebugName(number)
            if value:
                return value
        return ''
    try:
        chars, warnings, coverage_complete = usable_cmap(font)
    except TTLibError as exc:
        chars, coverage_complete = [], False
        warnings = ['Не удалось прочитать таблицу символов cmap: ' + str(exc)]
    os2 = font.get('OS/2')
    axes = []
    if 'fvar' in font:
        axes = [{'tag': a.axisTag, 'min': a.minValue, 'max': a.maxValue, 'default': a.defaultValue} for a in font['fvar'].axes]
    features = set()
    for tag in ['GSUB', 'GPOS']:
        try:
            if tag in font and font[tag].table.FeatureList:
                features.update(f.FeatureTag for f in font[tag].table.FeatureList.FeatureRecord)
        except TTLibError as exc:
            warnings.append('Не удалось прочитать OpenType-функции: ' + str(exc))
    # Catalogue analysis does not compress WOFF2. Existing verified previews may
    # be reused on reanalysis/recovery; new ones are independent durable jobs.
    preview = DATA / 'previews' / (face_id + '.woff2')
    preview_available = False
    if preview.is_file():
        try:
            with TTFont(preview) as verified:
                preview_available = sorted((verified.getBestCmap() or {}).keys()) == chars
        except Exception:
            pass
    with db() as con: coverage=coverage_cache.report(con,chars,store=True)
    return {'family': name(16, 1) or 'Без названия', 'style': name(17, 2) or 'Regular', 'version': name(5), 'author': name(9, 8), 'license': name(13), 'copyright': name(0), 'weight': getattr(os2, 'usWeightClass', 400), 'chars': chars, 'glyphs': font['maxp'].numGlyphs, 'languages': [row['name'] for row in coverage['languages'] if row['full']], 'axes': axes, 'features': sorted(features), 'warnings': warnings, 'preview_available': preview_available, 'preview_status': 'ready' if preview_available else 'queued' if chars else 'unavailable', 'coverage_complete': coverage_complete}


def import_file(source, source_id=None, original_name=None, progress=None):
    """Synchronous maintenance helper; production receipt uses accept directly."""
    digest=pipeline.accept(source,source_id,original_name,progress)
    pipeline.inline(digest)
    return digest


def recover_imports():
    pipeline.recover()


def process_analysis():
    return pipeline.process()


def reanalyze_file(file_hash):
    with db() as con:
        row=con.execute('SELECT * FROM files WHERE hash=?',(file_hash,)).fetchone()
        if not row: raise ValueError('Файл не найден в архиве')
        archive_pipeline.enqueue(con,row,force=True)
        con.execute("UPDATE analysis_state SET status='queued' WHERE hash=?",(file_hash,))
    pipeline.inline(file_hash)


def discover_inbox():
    """Discover stable files in one short transaction; parsing is separate.

    Files still being copied never become runnable. .part and symlinks remain
    excluded. Recheck the observed size/mtime again immediately before importing.
    """
    candidates=[]
    for path in INBOX.rglob('*'):
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in EXTENSIONS: continue
        try:
            stat=path.stat()
            if time.time()-stat.st_mtime<3: continue
            parts=path.relative_to(INBOX).parts
            source=parts[0] if len(parts)>1 and parts[0] in SOURCE_NAMES else 'local'
            candidates.append((path,source))
        except OSError: continue
    with db() as con:
        for path,source in candidates:
            try: import_queue.enqueue_import(con,path,source)
            except FileNotFoundError: pass  # Another completed job may remove it.


def process_import():
    with db() as con: job=import_queue.claim(con,'import')
    if not job: return False
    path=Path(job['input_path'])
    def progress(phase,detail=''):
        with db() as con: import_queue.progress(con,job['id'],phase,detail)
    try:
        stat=path.stat()
        if (stat.st_size,stat.st_mtime_ns)!=(job['size'],job['mtime']) or (path.is_relative_to(INBOX) and time.time()-stat.st_mtime<3):
            with db() as con: import_queue.defer_changed(con,job,path)
            return True
        digest=pipeline.accept(path,job['source'],job['filename'],progress)
        with db() as con: import_queue.finish(con,job['id'],result=digest)
    except Exception as exc:
        # Quarantine only the file version we actually claimed. An ongoing copy
        # is deferred rather than moving/deleting newly arriving bytes.
        if path.exists() and (path.stat().st_size,path.stat().st_mtime_ns)!=(job['size'],job['mtime']):
            with db() as con: import_queue.defer_changed(con,job,path)
            return True
        destination=path
        if path.exists() and path.parent!=DATA/'quarantine':
            destination=DATA/'quarantine'/(uuid.uuid4().hex+'-'+job['filename'])
            shutil.move(str(path),destination)
        with db() as con: import_queue.finish(con,job['id'],'error',str(exc)[:1000],path=destination)
        event(job['filename'],'error',str(exc)[:500])
    return True


def process_preview():
    """Compress outside the archive lock; publish and metadata commit inside it.

    A slow font blocks only this preview worker. The immutable original is already
    catalogued and downloadable. Partial output is private to this job and is
    removed on failure/restart, never confused with the published WOFF2.
    """
    with db() as con: job=import_queue.claim(con,'preview')
    if not job: return False
    with db() as con:
        if not original_checks.guard(con,job,job['identity'][:64]):return True
    with archive_pipeline.heavy(DATA,Path(job['input_path'])):
        return preview_job(job)


def preview_job(job):
    face_id=job['identity'];preview=DATA/'previews'/(face_id+'.woff2')
    partial=DATA/'previews'/(face_id+'.queue.part')
    def progress(phase):
        with db() as con: import_queue.progress(con,job['id'],phase)
    try:
        progress('analysis')
        with db() as con:
            row=con.execute('SELECT faces.metadata,files.path,faces.hash FROM faces JOIN files ON files.hash=faces.hash WHERE faces.id=?',(face_id,)).fetchone()
        if not row: raise ValueError('Начертание отсутствует в каталоге')
        info=json.loads(row['metadata']);target=Path(row['path'])
        if hashlib.sha256(target.read_bytes()).hexdigest()!=row['hash']: raise ValueError('Контрольная сумма оригинала не совпала')
        reusable=False
        if preview.is_file():
            try:
                with TTFont(preview) as verified: reusable=sorted((verified.getBestCmap() or {}).keys())==info['chars']
            except Exception: pass
        if not reusable:
            with TTFont(target,fontNumber=int(face_id.rsplit('-',1)[1])) as font:
                # Reapply the same tolerant cmap analysis to this fresh reader.
                # metadata() previously repaired only its own in-memory font;
                # preserving that policy prevents a preview regression for TTC.
                chars,_,_=usable_cmap(font)
                if chars!=info['chars']: raise TTLibError('Анализ символов изменился')
                progress('preview');font.flavor='woff2';font.save(partial)
            progress('verification')
            with TTFont(partial) as verified:
                if sorted((verified.getBestCmap() or {}).keys())!=info['chars']: raise TTLibError('Предпросмотр не сохранил проверенные символы')
        progress('sanitizing')
        validation=font_validation.check(preview if reusable else partial)
        progress('publishing')
        with archive_lock(DATA/'import.lock'):
            with db() as con:
                current=json.loads(con.execute('SELECT metadata FROM faces WHERE id=?',(face_id,)).fetchone()[0])
                if current['chars']!=info['chars']: raise ValueError('Анализ изменился; повторите подготовку предпросмотра')
                if not reusable: publish(partial,preview)
                current.update(preview_available=True,preview_status='ready',browser_validation=validation);current.pop('preview_error',None)
                con.execute('UPDATE faces SET metadata=? WHERE id=?',(json.dumps(current,ensure_ascii=False),face_id))
                import_queue.finish(con,job['id'],result=face_id+'~'+job['source'])
        event(job['filename'],'preview','Предпросмотр готов')
    except Exception as exc:
        partial.unlink(missing_ok=True)
        with db() as con:
            con.execute("UPDATE faces SET metadata=json_set(metadata,'$.preview_available',json('false'),'$.preview_status','error','$.preview_error',?) WHERE id=?",(str(exc)[:1000],face_id))
            import_queue.finish(con,job['id'],'error',str(exc)[:1000])
        event(job['filename'],'preview_error','Предпросмотр: '+str(exc)[:400])
    return True


def scan():
    """Synchronous import-only drain for maintenance/tests; preview stays queued."""
    discover_inbox()
    while process_import(): pass
    while original_checks.process(db,DATA): pass
    while process_analysis(): pass


def discover_sources():
    """Merge trusted indexer registrations; new adapters need no FontHub edits.
    Discovery is local HTTP, cached by the client; archive API does not use it.
    Existing display names and sources remain available during service outages.
    """
    for item in google_fonts.index(GOOGLE_DATA).get('sources',[]):
        if item['id'] not in SOURCE_NAMES:
            source=dict(id=item['id'],name=item.get('name',item['id']),url=item['repository'],repo='',download='Indexer')
            SOURCES.append(source)
            SOURCE_NAMES[item['id']]=source['name']


@app.get('/')
def index():
    discover_sources()
    return render_template('index.html', inbox=str(INBOX), sources=[source for source in source_states(google_fonts.index(GOOGLE_DATA)) if source['enabled']],source_names=SOURCE_NAMES)


def source_states(data):
    """Source activation is a FontHub preference, independent of index jobs.

    Existing published indices remain usable after a failed refresh. A source
    without a snapshot cannot be activated. Turning it off preserves its index
    and archived originals; it removes external offers and filter choices only.
    """
    indexed={item['id']:item for item in data.get('sources',[])}
    available={row['source'] for row in data.get('families',[])}
    with db() as con:
        saved={row['id']:bool(row['enabled']) for row in con.execute('SELECT * FROM source_settings')}
    result=[]
    for source in SOURCES:
        item=indexed.get(source['id'])
        active=source['id']=='local' or (item.get('active',True) if item else source['id'] not in {'font-library','font-squirrel'})
        ready=active and (source['id']=='local' or bool(item and item.get('snapshot') and item['snapshot']['count']) or source['id'] in available)
        with db() as con:policy=original_checks.mode(con,source['id'])
        result.append(dict(source,ots_policy=policy,indexer=item,active=active,inactive_reason=(item or {}).get('inactive_reason','Адаптер не завершён'),ready=ready,can_enable=ready and not data.get('error'),enabled=ready and saved.get(source['id'],True)))
    return result


@app.post('/api/sources/<source_id>/enabled')
def set_source_enabled(source_id):
    discover_sources()
    if source_id not in SOURCE_NAMES or source_id=='local': abort(404)
    enabled=(request.get_json(silent=True) or {}).get('enabled')
    if not isinstance(enabled,bool): return jsonify(error='Передайте enabled: true или false'),400
    source=next(item for item in source_states(google_fonts.index(GOOGLE_DATA)) if item['id']==source_id)
    if enabled and not source['can_enable']:
        return jsonify(error='Источник нельзя включить: сначала получите доступный непустой индекс.'),409
    with db() as con:
        con.execute('INSERT INTO source_settings(id,enabled) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled',(source_id,int(enabled)))
    return jsonify(enabled=enabled)


def write_import_status(filename):
    status = DATA / 'import-status.json'
    temporary = status.with_suffix('.tmp')
    temporary.write_text(json.dumps({'filename': filename, 'updated': time.time()}, ensure_ascii=False), encoding='utf-8')
    temporary.replace(status)


@app.get('/api/status')
def import_status():
    with db() as con:
        events = [dict(row) for row in con.execute('SELECT * FROM events ORDER BY id DESC LIMIT 30')]
        total = con.execute('SELECT COUNT(*) FROM files').fetchone()[0]
        language_options = [row[0] for row in con.execute('SELECT DISTINCT language FROM search_languages ORDER BY language')]
        queued=con.execute("SELECT COUNT(*) FROM work_queue WHERE kind='import' AND status IN ('queued','running')").fetchone()[0]
        current=con.execute("SELECT filename FROM work_queue WHERE kind='import' AND status='running' LIMIT 1").fetchone()
        paused=bool(con.execute("SELECT paused FROM queue_control WHERE kind='import'").fetchone()[0])
    return jsonify(events=events, pending=queued, current=current[0] if current else '', paused=paused, imported=total, language_options=language_options, language_names={row[0]: row[1] for row in orthographies()})


@app.get('/imports')
def imports_page():
    return render_template('imports.html',inbox=str(INBOX),source_names=SOURCE_NAMES)


@app.get('/api/imports')
def imports_snapshot():
    kind=request.args.get('kind','');status=request.args.get('status','active')
    if kind not in {'','import','ots','repair','analysis','preview','convert','download'} or status not in {'','active','queued','running','done','error','cancelled','blocked'}: abort(400)
    try: page=int(request.args.get('page',1))
    except ValueError: abort(400)
    with db() as con: result=import_queue.snapshot(con,kind,status,page)
    return jsonify(result)


@app.post('/api/imports/control')
def imports_control():
    data=request.get_json(silent=True) or {};kind=data.get('kind');paused=data.get('paused')
    if kind not in {'all','import','ots','repair','analysis','preview','convert','download'} or not isinstance(paused,bool): abort(400)
    with db() as con:
        for name in ['import','ots','repair','analysis','preview','convert','download'] if kind=='all' else [kind]:
            con.execute('UPDATE queue_control SET paused=? WHERE kind=?',(int(paused),name))
    return jsonify(paused=paused,kind=kind)


@app.post('/api/imports/<int:id>/retry')
def retry_import_job(id):
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        row=con.execute('SELECT * FROM work_queue WHERE id=?',(id,)).fetchone()
        if not row: abort(404)
        if row['status']!='error': return jsonify(error='Повтор доступен только для завершённых ошибок.'),409
        con.execute('DELETE FROM issue_resolutions WHERE job_id=?',(id,))
        path=Path(row['input_path'])
        if row['kind']!='download' and not path.is_file(): return jsonify(error='Исходный файл отсутствует. Загрузите его снова.'),409
        stat=path.stat() if path.is_file() else None
        if row['kind']=='download':con.execute('UPDATE download_tasks SET cancel_requested=0 WHERE job_id=?',(id,))
        con.execute("UPDATE work_queue SET status='queued',phase='waiting',detail='',error='',attempts=0,size=?,mtime=?,started=NULL,finished=NULL,available_at=0 WHERE id=?",(stat.st_size if stat else None,stat.st_mtime_ns if stat else None,id))
        if row['kind']=='analysis':
            con.execute("UPDATE analysis_state SET status='queued',error='' WHERE hash=?",(row['identity'],))
        if row['kind']=='ots':
            con.execute("UPDATE font_checks SET status='queued' WHERE hash=?",(row['identity'],))
        if row['kind']=='repair':
            con.execute("UPDATE repair_candidates SET status='queued',error='' WHERE hash=?",(row['identity'],))
        if row['kind']=='preview':
            con.execute("UPDATE faces SET metadata=json_remove(json_set(metadata,'$.preview_status','queued'),'$.preview_error') WHERE id=?",(row['identity'],))
    return jsonify(message='Задание возвращено в очередь.')


@app.get('/api/fonts/<face_id>/preview-status')
def preview_status(face_id):
    with db() as con: face=resolve_face(con,face_id)
    return jsonify(available=bool(face.get('preview_available')),status=face.get('preview_status','ready' if face.get('preview_available') else 'unavailable'),error=face.get('preview_error',''))


@app.get('/health')
def health():
    with db() as con: con.execute('SELECT 1').fetchone()
    return jsonify(status='ok')


@app.get('/api/catalog')
def catalog():
    with db() as con:
        faces = origin_faces(con)
        events = [dict(row) for row in con.execute('SELECT * FROM events ORDER BY id DESC LIMIT 30')]
    language_options = sorted({name for face in faces for name in face['languages']})
    return jsonify(faces=faces, events=events, inbox=str(INBOX), language_options=language_options)


@app.get('/fonts/<face_id>')
def font_page(face_id):
    with db() as con:
        face = resolve_face(con, face_id)
        candidates = origin_faces(con)
    # Matching names are a navigation aid, not proof that two files are identical.
    related = [f for f in candidates if (f['family'], f['author'], f['source']) == (face['family'], face['author'], face['source'])]
    with db() as con: coverage = coverage_cache.report(con,face['chars'])
    face['languages'] = [row['name'] for row in coverage['languages'] if row['full']]
    with db() as con:checked=con.execute('SELECT * FROM font_checks WHERE hash=?',(face_id[:64],)).fetchone()
    checked=dict(checked) if checked else dict(status='unchecked')
    checked['report']=json.loads(checked.get('report') or '{}')
    with db() as con:repair_origin=con.execute("SELECT original_hash,choice FROM repair_choices WHERE candidate_hash=? AND source=? AND original_hash<>candidate_hash ORDER BY id DESC LIMIT 1",(face_id[:64],face['source'])).fetchone()
    return render_template('detail.html', face=face, related=related, coverage=coverage, sources=SOURCES,ots_check=checked,ots_labels=original_checks.LABELS,repair_origin=repair_origin)


@app.get('/converter')
def converter_page():
    return render_template('tool.html', title='Конвертер')


@app.get('/sources')
def sources_page():
    discover_sources()
    return render_template('sources.html', inbox=str(INBOX), sources=SOURCES)


@app.get('/api/sources')
def sources_status():
    discover_sources()
    with db() as con:
        checks = {row['id']: dict(json.loads(row['result']), checked=row['checked']) for row in con.execute('SELECT * FROM source_checks')}
    data = google_fonts.index(GOOGLE_DATA)
    return jsonify(sources=[dict(source,status=checks.get(source['id'])) for source in source_states(data)],indexer_error=data.get('error'),google=dict(updated=data['updated'],count=sum(row['source']=='google-fonts' for row in data['families']),job=data.get('status',{}).get('job'),error=data.get('error')))


@app.post('/api/sources/<source_id>/sync')
def google_sync(source_id):
    discover_sources()
    if source_id not in SOURCE_NAMES or source_id == 'local': abort(404)
    if not google_lock.acquire(blocking=False):
        return jsonify(error='Операция с источником уже выполняется'), 409
    try:
        data = google_fonts.sync(GOOGLE_DATA, source_id)
        return jsonify(data), 202
    except Exception as exc:
        return jsonify(error='Не удалось обновить индекс: ' + str(exc)[:200]), 502
    finally:
        google_lock.release()


@app.get('/api/sources/<source_id>/jobs/<job_id>')
def google_job(source_id, job_id):
    try:
        job=google_fonts.api('/jobs/' + job_id)
        if job['status']=='completed': google_fonts.invalidate_index()
        return jsonify(job)
    except Exception as exc:
        return jsonify(error=str(exc)[:200]), 502


@app.get('/sources/<source_id>/fonts/<family_id>')
def google_family_page(source_id, family_id):
    discover_sources()
    row = google_fonts.family(GOOGLE_DATA, family_id)
    if row is None or row['source'] != source_id:
        abort(404)
    # Pinned metadata is read from the local indexer; no font binary is fetched.
    # File properties remain unknown until archive import and analysis.
    record = google_fonts.api('/families/' + family_id + '?revision=' + row['revision'])
    styles = record.get('styles',[]) or [dict(style=item.get('style', ['Не указано'])[0], weight=item.get('weight', [None])[0], filename=item.get('filename', [''])[0]) for item in record['raw'].get('fonts', [])]
    if not styles: styles=[dict(style=file.get('filename',file['name']),weight=None,filename=file['name']) for file in record['files']]
    record['source_name']=SOURCE_NAMES[source_id]
    return render_template('remote_font.html', font=record, styles=styles, face=dict(axes=[]), source_coverage=source_languages(record))


@app.post('/api/sources/<source_id>/import/<family_id>')
def google_import(source_id, family_id):
    discover_sources()
    row = google_fonts.family(GOOGLE_DATA, family_id)
    if row is None or row['source'] != source_id:
        abort(404)
    try:
        manifest=google_fonts.resolve(GOOGLE_DATA,row)
        if not manifest['artifacts']:return jsonify(error='Источник не предоставляет файлов'),422
        with db() as con:
            jobs=[font_downloads.enqueue(con,GOOGLE_DATA,row,artifact,want_import=True) for artifact in manifest['artifacts']]
        return jsonify(url='/imports',queued=True,jobs=jobs,message='Семейство добавлено в очередь загрузки. После скачивания начнётся импорт.'),202
    except Exception as exc:
        return jsonify(error='Не удалось поставить загрузку в очередь: '+str(exc)[:200]),502


@app.post('/api/sources/<source_id>/check')
def source_check(source_id):
    source = next((row for row in SOURCES if row['id'] == source_id and source_id != 'local'), None)
    if source is None:
        abort(404)
    result = check_source(source)
    with db() as con:
        con.execute("INSERT OR REPLACE INTO source_checks VALUES(?,?,datetime('now'))", (source_id, json.dumps(result)))
    return jsonify(result)


@app.post('/api/fonts/<face_id>/source')
def set_font_source(face_id):
    source_id = (request.get_json(silent=True) or {}).get('source')
    if source_id not in SOURCE_NAMES:
        return jsonify(error='Неизвестный источник'), 400
    with db() as con:
        face = resolve_face(con, face_id)
        base = face['id'].split('~')[0]
        file_hash = con.execute('SELECT hash FROM faces WHERE id=?', (base,)).fetchone()['hash']
        if source_id != face['source'] and con.execute('SELECT 1 FROM origins WHERE hash=? AND source=?', (file_hash, source_id)).fetchone():
            return jsonify(error='Экземпляр этого источника уже существует'), 409
        con.execute('UPDATE origins SET source=? WHERE hash=? AND source=?', (source_id, file_hash, face['source']))
    return jsonify(source=source_id, url='/fonts/' + base + '~' + source_id)


@app.get('/comparison')
def comparison_page():
    return render_template('tool.html', title='Сравнение')


@app.get('/settings')
def settings():
    from hyperglot.languages import Languages
    database = Languages(validity='preliminary')
    languages = sorted({(row[0], database[row[0]]['name'], row[1]) for row in orthographies()}, key=lambda row: row[1])
    return render_template('settings.html', languages=languages)


@app.get('/api/search')
def search():
    query=request.args.get('q','').strip().casefold()
    language=request.args.get('language','');kind=request.args.get('type','')
    source_filter=request.args.get('source','');file_format=request.args.get('format','')
    ots_status=request.args.get('ots','')
    if ots_status and ots_status not in original_checks.LABELS:abort(400)
    coverage=request.args.get('coverage')=='1';text=request.args.get('text','')
    required={ord(char) for char in text if not char.isspace()} if coverage else set()
    active=bool(query or language or kind or source_filter or file_format or ots_status or (coverage and text))
    scope=request.args.get('scope','archive')
    remote_index=google_fonts.index(GOOGLE_DATA) if scope in {'remote','all'} else None
    enabled_sources={item['id'] for item in source_states(remote_index) if item['enabled']} if remote_index else set()
    if remote_index and source_filter not in {'','local','internet'}:
        selected=next((item for item in remote_index.get('sources',[]) if item['id']==source_filter),None)
        if selected and not selected.get('snapshot'):
            job=selected.get('job') or {}
            remote_index=dict(remote_index,error='Индекс источника не получен: '+(job.get('error') or 'обновите его в разделе «Источники»'))
        elif selected and source_filter not in enabled_sources:
            remote_index=dict(remote_index,error='Источник отключён. Включите его в разделе «Источники».')
    with db() as con:
        base,parameters=catalog_search.prepare(con,query,language,kind,source_filter,file_format,required,ots_status)
        if scope=='remote': con.execute('DELETE FROM current_groups')
        if remote_index and source_filter!='local' and not coverage and not ots_status:
            for row in remote_index['families']:
                if row['source'] not in enabled_sources or source_filter not in {'','internet',row['source']}: continue
                declared=source_languages(row)
                if language and not matches_source_language(declared,language): continue
                if file_format and file_format not in {file['format'] for file in row['files']}: continue
                if kind and (row.get('field_states',{}).get('type')=='not_provided' or bool(row['axes'])!=(kind=='variable')): continue
                if not all(term in (row['family']+' '+row['author']+' '+' '.join(row['variants'])).casefold() for term in query.split()): continue
                record=dict(row,remote=True,sources=[row['source']],formats=sorted({file['format'] for file in row['files']}),style_count=len(row['variants']) or '—',added_at='',preview_available=False,source_coverage=declared)
                catalog_search.add_remote(con,record)
        result=catalog_search.page(con,base,parameters,request.args.get('page',1),request.args.get('sort','name'))
    return jsonify(**result,active=active,remote_indexed=bool(remote_index and any(row['source'] in enabled_sources for row in remote_index['families'])),remote_error=remote_index.get('error') if remote_index else None)


@app.post('/api/upload')
def upload():
    files = request.files.getlist('files')
    if not files:
        return jsonify(error='Выберите файлы'), 400
    for item in files:
        basename = Path((item.filename or '').replace('\\', '/')).name
        if Path(basename).suffix.lower() not in EXTENSIONS:
            return jsonify(error=f'Неподдерживаемый формат: {basename}'), 400
    for item in files:
        basename = Path(item.filename.replace('\\', '/')).name
        destination = INBOX / (uuid.uuid4().hex[:8] + '-' + basename)
        temporary = destination.with_suffix('.part')
        item.save(temporary)
        temporary.replace(destination)
    return jsonify(message='Файлы приняты. Импорт начнётся автоматически.')


@app.post('/api/scan')
def trigger_scan():
    discover_inbox()
    return jsonify(message='Входящая папка проверена. Очередь обновлена.')


@app.get('/font/<face_id>')
def preview_font(face_id):
    if '~' not in face_id and not __import__('re').fullmatch(r'[0-9a-f]{64}-[0-9]+',face_id):
        row = google_fonts.family(GOOGLE_DATA, face_id)
        if row is None:
            abort(404)
        try:
            manifest = google_fonts.resolve(GOOGLE_DATA, row)
            path = google_fonts.font_paths(GOOGLE_DATA, row, first=True)[0]
            return send_file(path, mimetype={'.otf':'font/otf','.woff':'font/woff','.woff2':'font/woff2'}.get(path.suffix.lower(),'font/ttf'))
        except Exception:
            abort(502)
    with db() as con:
        face = resolve_face(con, face_id)
    path = DATA / 'previews' / (face['id'].split('~')[0] + '.woff2')
    if not path.is_file() or not face.get('preview_available',True):
        abort(404)
    return send_file(path, mimetype='font/woff2')


@app.get('/download/<face_id>')
def download(face_id):
    with db() as con:
        face = resolve_face(con, face_id)
        row = con.execute('SELECT files.* FROM files JOIN faces ON files.hash=faces.hash WHERE faces.id=?', (face['id'].split('~')[0],)).fetchone()
    if not row:
        abort(404)
    return send_file(row['path'], as_attachment=True, download_name=row['name'])


@app.get('/download-family/<face_id>')
def download_family(face_id):
    with db() as con:
        face = resolve_face(con, face_id)
        files = {}
        for row in con.execute('SELECT files.*, faces.metadata FROM files JOIN faces ON files.hash=faces.hash JOIN origins ON origins.hash=files.hash WHERE origins.source=?', (face['source'],)):
            candidate = json.loads(row['metadata'])
            if (candidate['family'], candidate['author']) == (face['family'], face['author']):
                files[row['hash']] = row
    if len(files) == 1 and request.args.get('zip') != '1':
        row = next(iter(files.values()))
        return send_file(row['path'], as_attachment=True, download_name=row['name'])
    archive = io.BytesIO()
    names = set()
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as output:
        for file_hash, row in files.items():
            name = Path(row['name']).name
            if name.casefold() in names:
                name = f'{Path(name).stem}-{file_hash}{Path(name).suffix}'
            names.add(name.casefold())
            output.write(row['path'], arcname=name)
    archive.seek(0)
    name = ''.join(c for c in face['family'] if c not in '<>:"/\\|?*' and ord(c) >= 32).strip('. ') or 'fonts'
    return send_file(archive, mimetype='application/zip', as_attachment=True, download_name=name + '.zip')


def migrate_queue_history():
    with db() as con:
        if con.execute("SELECT 1 FROM queue_migrations WHERE name='legacy'").fetchone(): return
        for path in (DATA/'quarantine').iterdir():
            if not path.is_file() or path.suffix.lower() not in EXTENSIONS: continue
            prefix,separator,name=path.name.partition('-')
            if not separator or len(prefix)!=32: name=path.name
            previous=con.execute("SELECT message FROM events WHERE name=? AND status='error' ORDER BY id DESC LIMIT 1",(name,)).fetchone()
            import_queue.enqueue_import(con,path,'local',filename=name,failed=True,error=previous[0] if previous else 'Ошибка прежнего импорта; файл сохранён в карантине')
        for row in con.execute('SELECT faces.id,faces.metadata,files.path,files.name,files.source FROM faces JOIN files ON files.hash=faces.hash').fetchall():
            info=json.loads(row['metadata'])
            if info.get('chars') and not info.get('preview_available',True):
                import_queue.enqueue_preview(con,row['id'],row['path'],row['name'],row['source'])
                con.execute("UPDATE faces SET metadata=json_set(metadata,'$.preview_status','queued') WHERE id=?",(row['id'],))
        con.execute("INSERT INTO queue_migrations VALUES('legacy')")


def worker(kind='import'):
    # A lifetime OS lock prevents duplicate workers and makes resetting interrupted
    # claims safe. Server/API processes never reset another worker's live jobs.
    with archive_lock(DATA/(kind+'-worker.lock')):
        if kind=='import': migrate_queue_history()
        with db() as con:
            import_queue.reset_running(con,kind)
            if kind=='analysis':
                con.execute("UPDATE analysis_state SET status=(SELECT status FROM work_queue WHERE kind='analysis' AND identity=analysis_state.hash),error=COALESCE((SELECT error FROM work_queue WHERE kind='analysis' AND identity=analysis_state.hash),'') WHERE status='running'")
            if kind=='ots':
                con.execute("UPDATE font_checks SET status=COALESCE((SELECT status FROM work_queue WHERE kind='ots' AND identity=font_checks.hash),'error') WHERE status='running'")
            if kind=='repair':
                con.execute("UPDATE repair_candidates SET status=COALESCE((SELECT status FROM work_queue WHERE kind='repair' AND identity=repair_candidates.hash),'error') WHERE status='running'")
                con.execute("UPDATE repair_auto_origins SET status='review',reason='Восстановление прервано; требуется ручное повторение' WHERE status='pending' AND hash IN (SELECT hash FROM repair_candidates WHERE status='error')")
        if kind=='convert':
            for path in (DATA/'exports').glob('*.export.part'): path.unlink(missing_ok=True)
        if kind=='preview':
            for path in (DATA/'previews').glob('*.queue.part'): path.unlink(missing_ok=True)
        recovered=False;last_discovery=0
        while True:
            try:
                with db() as con: paused=con.execute('SELECT paused FROM queue_control WHERE kind=?',(kind,)).fetchone()[0]
                if kind=='import' and not paused and not recovered:
                    recover_imports();recovered=True
                if kind=='import' and time.monotonic()-last_discovery>=5:
                    discover_inbox();last_discovery=time.monotonic()
                if kind=='ots' and not paused and time.monotonic()-last_discovery>=5:
                    with db() as con:
                        original_checks.seed(con)
                        original_checks.release_batch(con)
                    last_discovery=time.monotonic()
                busy=process_import() if kind=='import' else font_repairs.process(db,DATA,pipeline) if kind=='repair' else original_checks.process(db,DATA) if kind=='ots' else process_analysis() if kind=='analysis' else process_preview() if kind=='preview' else font_exports.process(db,DATA) if kind=='convert' else font_downloads.process(db,DATA,GOOGLE_DATA,google_fonts)
                if not busy: time.sleep(1)
            except Exception as exc:
                import traceback
                traceback.print_exc();event('Очередь '+kind,'error',str(exc)[:500]);time.sleep(5)


# Late-bound callbacks keep the pipeline independent of Flask and preserve
# maintenance/test instrumentation without a second implementation of import.
pipeline=archive_pipeline.Pipeline(db,DATA,INBOX,lambda *a:metadata(*a),event,lambda *a:copy_original(*a),SOURCE_NAMES,discover_sources)

@app.get('/originals/<digest>')
def accepted_original(digest):
    if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest): abort(404)
    with db() as con: row=con.execute('SELECT path,name FROM files WHERE hash=?',(digest,)).fetchone()
    if not row: abort(404)
    return send_file(row['path'],as_attachment=True,download_name=row['name'])

font_exports.register(app,db,DATA,resolve_face,google_fonts,GOOGLE_DATA)
font_downloads.register(app,db,DATA,GOOGLE_DATA,google_fonts)
check_routes.register(app,db,DATA,SOURCE_NAMES)
font_repairs.register(app,db,DATA,pipeline)
app.jinja_env.filters['check_date']=lambda value:time.strftime('%d.%m.%Y %H:%M',time.localtime(value))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--reanalyze', metavar='SHA256', help='Повторный анализ оригинала по хешу')
    args = parser.parse_args()
    if args.reanalyze:
        reanalyze_file(args.reanalyze)
        print('Повторный анализ завершён', flush=True)
        raise SystemExit(0)
    from waitress import serve
    from multiprocessing import Process
    processes=[Process(target=worker,args=(kind,),daemon=True) for kind in ['import','ots','repair','analysis','preview','convert','download']]
    for process in processes: process.start()
    def supervise():
        # Native font parsing/compression can terminate a child. Only its own
        # replacement recovers running claims after acquiring the same OS lock.
        while True:
            time.sleep(3)
            for index,process in enumerate(processes):
                if process.is_alive(): continue
                process.join()
                kind=['import','ots','repair','analysis','preview','convert','download'][index]
                event('Очередь '+kind,'error','Рабочий процесс завершился; запуск замены')
                replacement=Process(target=worker,args=(kind,),daemon=True)
                replacement.start();processes[index]=replacement
    threading.Thread(target=supervise,daemon=True).start()
    print(f'FontHub: http://127.0.0.1:8765 | Inbox: {INBOX}', flush=True)
    serve(app, host=os.environ.get('FONTHUB_HOST', '127.0.0.1'), port=int(os.environ.get('FONTHUB_PORT', '8765')))
