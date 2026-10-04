"""FontHub client for the independent indexer and explicit binary downloads.

Metadata comes only from the versioned indexer API. This module never indexes
an upstream site. Binaries and documents are cached under immutable revisions.
See wiki/Indexer.md and wiki/Import-and-duplicates.md.
"""
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError

_cache = None
_cache_until = 0


def api(path, method='GET'):
    """Call the configured indexer; API keys never enter browser responses."""
    root = os.environ.get('FONTHUB_INDEXER_URL', 'http://127.0.0.1:8766').rstrip('/')
    headers = {'User-Agent': 'FontHub/1.0'}
    token = os.environ.get('FONTHUB_INDEXER_TOKEN')
    if token:
        headers['X-Api-Key'] = token
    try:
        with urlopen(Request(root + '/api/v1' + path, headers=headers, method=method), timeout=10) as response:
            return json.load(response)
    except HTTPError as error:
        # Preserve the service's actionable reason instead of an opaque HTTP code.
        try:
            payload = json.load(error)
        except (ValueError, OSError):
            raise RuntimeError(f'Indexer HTTP {error.code}') from error
        message = payload.get('error', f'Indexer HTTP {error.code}')
        if error.code == 429:
            message = f"Повторите обновление через {payload.get('retry_after', 60)} с"
        raise RuntimeError(message) from error


def invalidate_index():
    """Force the next metadata read after a completed publication."""
    global _cache_until
    _cache_until = 0


def sync(folder, source_id='google-fonts'):
    """Start a durable background sync job; callers poll its returned job ID."""
    invalidate_index()
    return api('/sources/'+source_id+'/sync', 'POST')


def index(folder):
    """Local HTTP metadata only; keep archive search usable if service is offline."""
    global _cache, _cache_until
    if time.monotonic() < _cache_until and _cache is not None:
        return _cache
    try:
        sources = api('/sources')['sources']
        source = sources[0]
        snapshot = next((item['snapshot'] for item in sources if item.get('snapshot')), None)
        if not snapshot:
            result = dict(updated=None, families=[], status=source, sources=sources)
        else:
            rows = []
            while True:
                page = api('/search?limit=2000&offset=' + str(len(rows)))
                rows.extend(page['items'])
                if len(rows) >= page['total'] or not page['items']:
                    break
            from datetime import datetime, timezone
            result = dict(updated=datetime.fromtimestamp(snapshot['created'], timezone.utc).isoformat(), families=rows, status=source, sources=sources)
        _cache, _cache_until = result, time.monotonic() + 5
        return result
    except Exception as exc:
        # Cached results remain searchable, but staleness is explicit.
        return dict(_cache or dict(updated=None, families=[]), error='Индексатор недоступен: ' + str(exc)[:200])


def family(folder, family_id):
    return next((row for row in index(folder)['families'] if row['id'] == family_id), None)


def resolve(folder, row):
    """Fetch complete metadata/documents for the search result's exact revision."""
    revision = row['revision']
    if not re.fullmatch('[0-9a-f]{40,64}', revision):
        raise ValueError('Invalid indexer revision')
    cache = folder / row['id'] / revision
    cache.mkdir(parents=True, exist_ok=True)
    manifest = cache / 'manifest.json'
    if manifest.exists():
        cached=json.loads(manifest.read_text(encoding='utf-8'))
        if 'download_policy' in cached.get('family',{}): return cached
    data = api('/families/' + row['id'] + '?revision=' + revision)
    for name, text in data['documents'].items():
        if Path(name).name != name or '/' in name or chr(92) in name:
            raise ValueError('Unsafe document filename')
        (cache / name).write_text(text, encoding='utf-8')
    result = dict(files=[file['name'] for file in data['files']], artifacts=data['files'], revision=revision, family=data, url=data['url'])
    temporary = manifest.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    temporary.replace(manifest)
    return result


def binary(folder, row, name, progress=None):
    """Download only a selected artifact, validate it and cache immutable bytes."""
    manifest = resolve(folder, row)
    artifact = next((file for file in manifest['artifacts'] if file['name'] == name), None)
    if artifact is None or Path(name).name != name or '/' in name or chr(92) in name:
        raise ValueError('Invalid font artifact')
    parsed = urlparse(artifact['url'])
    prefixes=manifest['family'].get('download_policy',{})
    if parsed.scheme != 'https' or parsed.hostname not in prefixes or not parsed.path.startswith(prefixes[parsed.hostname]):
        raise ValueError('Artifact URL does not belong to the configured source')
    path = folder / row['id'] / manifest['revision'] / name
    if not path.exists():
        with urlopen(Request(artifact['url'], headers={'User-Agent': 'FontHub/1.0'}), timeout=30) as response:
            if urlparse(response.url).scheme != 'https' or urlparse(response.url).hostname not in {'raw.githubusercontent.com','github.com','release-assets.githubusercontent.com'}:
                raise ValueError('Unexpected download redirect')
            total=int(response.headers.get('Content-Length') or 0);chunks=[];received=0
            while True:
                chunk=response.read(65536)
                if not chunk: break
                received+=len(chunk)
                if received>100*1024*1024: raise ValueError('Font exceeds 100 MiB')
                chunks.append(chunk)
                if progress: progress(received,total)
            raw=b''.join(chunks)
        if len(raw) > 100 * 1024 * 1024:
            raise ValueError('Font exceeds 100 MiB')
        from fontTools.ttLib import TTFont
        import io
        import uuid
        if artifact.get('container') == 'zip':
            import zipfile
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if sum(item.file_size for item in archive.infolist()) > 500*1024*1024:
                    raise ValueError('Archive exceeds extraction budget')
        else:
            with TTFont(io.BytesIO(raw), fontNumber=0):
                pass
        temporary = path.with_name(name + '.' + uuid.uuid4().hex + '.tmp')
        temporary.write_bytes(raw)
        try:
            if progress: progress(len(raw),len(raw))
            temporary.replace(path)
        finally: temporary.unlink(missing_ok=True)
    return path


def font_paths(folder, row, first=False, names=None):
    """Explicit bounded ZIP extraction; member paths never become filesystem paths."""
    import hashlib, zipfile, io
    from fontTools.ttLib import TTFont
    paths=[]
    for artifact in resolve(folder,row)['artifacts']:
        if names is not None and artifact['name'] not in names: continue
        path=binary(folder,row,artifact['name'])
        if artifact.get('container')!='zip':
            paths.append(path)
            if first:return paths
            continue
        with zipfile.ZipFile(path) as archive:
            members=[item for item in archive.infolist() if not item.is_dir() and Path(item.filename).suffix.lower() in {'.ttf','.otf','.woff','.woff2','.ttc','.otc'}]
            if len(members)>4000:raise ValueError('Too many font files in ZIP')
            for item in members:
                if item.file_size>100*1024*1024:raise ValueError('ZIP member exceeds 100 MiB')
                target=path.parent/'extracted'/(hashlib.sha256((artifact['name']+'/'+item.filename).encode()).hexdigest()[:8]+'-'+Path(item.filename).name)
                target.parent.mkdir(exist_ok=True)
                if not target.exists():
                    raw=archive.read(item)
                    with TTFont(io.BytesIO(raw),fontNumber=0):pass
                    target.write_bytes(raw)
                paths.append(target)
                if first:return paths
    if not paths:raise ValueError('Source does not provide downloadable font artifacts')
    return paths
