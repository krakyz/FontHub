"""Source-neutral snapshot API and durable jobs.

Adapters only acquire/map source evidence through the shared Context. The API
owns validation, atomic per-source publication and recovery. FontHub owns font
binaries, previews and analysis. See wiki/Adapters.md for the authoring contract.
"""
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse
import re

from flask import Flask, abort, jsonify, request
from .adapters import ADAPTERS
from .transport import Context
from .validation import normalize

LOG = logging.getLogger('font-indexer')


def create_app(data=None, repository=None, repositories=None):
    """Create an isolated service; data location is configurable for tests/hosting."""
    data = Path(data or os.environ.get('FONT_INDEXER_DATA', 'data/indexer')).resolve()
    data.mkdir(parents=True, exist_ok=True)
    database = data / 'index.sqlite'
    app = Flask(__name__)
    guard = threading.Lock()
    overrides = dict(repositories or {})
    if repository: overrides['google-fonts'] = repository  # Legacy fixture argument.

    @contextmanager
    def connect():
        """Commit/rollback transactions and always release the SQLite handle."""
        con = sqlite3.connect(database, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    with connect() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS snapshots(revision TEXT PRIMARY KEY, created REAL, count INTEGER);
        CREATE TABLE IF NOT EXISTS families(revision TEXT, id TEXT, normalized TEXT, raw TEXT, documents TEXT, PRIMARY KEY(revision,id));
        CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, status TEXT, phase TEXT, created REAL, finished REAL, error TEXT, revision TEXT);
        ''')
        for table in ['snapshots', 'jobs']:
            if 'source' not in {row['name'] for row in con.execute('PRAGMA table_info('+table+')')}:
                con.execute("ALTER TABLE "+table+" ADD COLUMN source TEXT NOT NULL DEFAULT 'google-fonts'")
        # Two sources may legitimately observe the same repository revision.
        # Preserve old rows while upgrading snapshot identity to source+revision.
        if not next(row['pk'] for row in con.execute('PRAGMA table_info(snapshots)') if row['name']=='source'):
            con.execute('CREATE TABLE snapshots_new(source TEXT, revision TEXT, created REAL, count INTEGER, PRIMARY KEY(source,revision))')
            con.execute('INSERT INTO snapshots_new SELECT source,revision,created,count FROM snapshots')
            con.execute('DROP TABLE snapshots')
            con.execute('ALTER TABLE snapshots_new RENAME TO snapshots')
        con.execute("INSERT OR IGNORE INTO state SELECT 'active:google-fonts',value FROM state WHERE key='active'")
        con.execute("UPDATE jobs SET status='failed', error='Service restarted before completion', finished=? WHERE status IN ('queued','running')", (time.time(),))

    def phase(job, text):
        with connect() as con:
            con.execute("UPDATE jobs SET status='running', phase=? WHERE id=?", (text, job))
        LOG.info('job=%s phase=%s', job, text)

    def synchronize(job, source_id):
        """Build privately, commit all rows and the active pointer in one transaction."""
        try:
            phase(job, 'Acquiring source metadata')
            adapter = ADAPTERS[source_id]
            context = Context(data, source_id, job, adapter.HOSTS, overrides.get(source_id))
            revision, records = adapter.collect(context)
            rows=normalize(adapter,revision,records)
            ids={row[1] for row in rows}
            phase(job, 'Publishing snapshot')
            with connect() as con:
                for record_id in ids:
                    if con.execute("SELECT 1 FROM families WHERE id=? AND json_extract(normalized,'$.source')<>? LIMIT 1",(record_id,source_id)).fetchone():
                        raise ValueError('Adapter ID collides with another source: '+record_id)
                con.executemany('INSERT OR REPLACE INTO families VALUES(?,?,?,?,?)', rows)
                con.execute('INSERT OR REPLACE INTO snapshots(revision,created,count,source) VALUES(?,?,?,?)', (revision, time.time(), len(rows), source_id))
                con.execute("INSERT OR REPLACE INTO state VALUES(?,?)", ('active:'+source_id,revision))
                con.execute("UPDATE jobs SET status='completed', phase='Complete', finished=?, revision=? WHERE id=?", (time.time(), revision, job))
            LOG.info('job=%s published revision=%s families=%s', job, revision, len(rows))
        except Exception as exc:
            LOG.exception('job=%s failed; previous snapshot remains active', job)
            with connect() as con:
                con.execute("UPDATE jobs SET status='failed', finished=?, error=? WHERE id=?", (time.time(), str(exc)[:2000], job))
        finally:
            guard.release()

    @app.before_request
    def authorization():
        token = os.environ.get('FONT_INDEXER_TOKEN')
        if token:
            import hmac
            if not hmac.compare_digest(request.headers.get('X-Api-Key', ''), token):
                abort(401)

    @app.get('/api/v1/health')
    def health():
        return jsonify(status='ok', api_version=1)

    @app.get('/api/v1/sources')
    def sources():
        result = []
        with connect() as con:
            for source_id, adapter in ADAPTERS.items():
                snapshot = con.execute("SELECT * FROM snapshots WHERE source=? AND revision=(SELECT value FROM state WHERE key=?)", (source_id,'active:'+source_id)).fetchone()
                job = con.execute('SELECT * FROM jobs WHERE source=? ORDER BY created DESC LIMIT 1',(source_id,)).fetchone()
                result.append(dict(id=source_id,name=getattr(adapter,'NAME',source_id),repository=adapter.REPOSITORY,snapshot=dict(snapshot) if snapshot else None,job=dict(job) if job else None,capabilities=adapter.CAPABILITIES,active=getattr(adapter,'ACTIVE',True),inactive_reason=getattr(adapter,'INACTIVE_REASON','')))
        return jsonify(sources=result)

    @app.post('/api/v1/sources/<source_id>/sync')
    def sync(source_id):
        if source_id not in ADAPTERS: abort(404)
        if not getattr(ADAPTERS[source_id],'ACTIVE',True):
            return jsonify(error=getattr(ADAPTERS[source_id],'INACTIVE_REASON','Adapter inactive')),409
        if not guard.acquire(blocking=False):
            return jsonify(error='Sync already running'), 409
        with connect() as con:
            previous = con.execute('SELECT created FROM jobs WHERE source=? ORDER BY created DESC LIMIT 1',(source_id,)).fetchone()
            interval = int(os.environ.get('FONT_INDEXER_MIN_INTERVAL', '60'))
            if previous and time.time() - previous['created'] < interval:
                guard.release()
                return jsonify(error='Sync cooldown', retry_after=interval), 429
            job = uuid.uuid4().hex
            con.execute("INSERT INTO jobs(id,status,phase,created,source) VALUES(?,'queued','Queued',?,?)", (job, time.time(),source_id))
        threading.Thread(target=synchronize, args=(job,source_id), daemon=True).start()
        return jsonify(job_id=job), 202

    @app.get('/api/v1/jobs/<job_id>')
    def job_status(job_id):
        with connect() as con:
            row = con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            abort(404)
        return jsonify(dict(row))

    @app.get('/api/v1/search')
    def search():
        """No network calls: search only the active local snapshot."""
        with connect() as con:
            records = con.execute("SELECT normalized FROM families WHERE revision IN (SELECT value FROM state WHERE key LIKE 'active:%')").fetchall()
        terms = request.args.get('q', '').casefold().split()
        rows = [json.loads(row[0]) for row in records]
        rows = [row for row in rows if all(term in (row['family'] + ' ' + row['author'] + ' ' + ' '.join(row['variants'])).casefold() for term in terms)]
        if request.args.get('source'):
            rows = [row for row in rows if row['source'] == request.args['source']]
        kind = request.args.get('type', '')
        if kind:
            rows = [row for row in rows if row.get('field_states',{}).get('type') != 'not_provided' and bool(row['axes']) == (kind == 'variable')]
        if request.args.get('format'):
            rows = [row for row in rows if request.args['format'] in {file['format'] for file in row['files']}]
        rows.sort(key=lambda row: row['family'].casefold(), reverse=request.args.get('sort') == 'name_desc')
        try:
            limit = min(2000, max(1, int(request.args.get('limit', 10))))
            offset = max(0, int(request.args.get('offset', 0)))
        except ValueError:
            abort(400)
        return jsonify(total=len(rows), items=rows[offset:offset + limit])

    @app.get('/api/v1/families/<family_id>')
    def family(family_id):
        with connect() as con:
            row = con.execute("SELECT * FROM families WHERE id=? AND (revision=? OR (? IS NULL AND revision IN (SELECT value FROM state WHERE key LIKE 'active:%')))", (family_id, request.args.get('revision'), request.args.get('revision'))).fetchone()
        if not row:
            abort(404)
        record=json.loads(row['normalized'])
        adapter=ADAPTERS[record['source']]
        record.setdefault('download_policy',{host:prefix.format(revision=record['revision']) for host,prefix in getattr(adapter,'DOWNLOAD_PREFIXES',{}).items()})
        return jsonify(**record, raw=json.loads(row['raw']), documents=json.loads(row['documents']))

    return app


if __name__ == '__main__':
    from waitress import serve
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    serve(create_app(), host=os.environ.get('FONT_INDEXER_HOST', '127.0.0.1'), port=int(os.environ.get('FONT_INDEXER_PORT', '8766')))
