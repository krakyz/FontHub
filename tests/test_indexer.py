"""Deterministic indexer checks using a local Git remote, without upstream I/O."""

# Direct execution from the repository root keeps application imports available.
import sys as _test_sys
from pathlib import Path as _TestPath
_test_sys.path.insert(0,str(_TestPath(__file__).resolve().parents[1]))
import os
import subprocess
import tempfile
import time
from pathlib import Path
from indexer.service import create_app
from indexer.textproto import parse


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.DEVNULL).decode().strip()


def wait(client, job):
    for _ in range(400):
        result = client.get('/api/v1/jobs/' + job).json
        if result['status'] in {'failed', 'completed'}:
            return result
        time.sleep(.05)
    raise AssertionError('Indexer job timed out')


assert parse('name: "Тест" unknown { value: 2 } flags: [true, false]')['unknown'][0]['value'] == [2]
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    repo = root / 'remote'
    repo.mkdir()
    git(repo, 'init', '-b', 'main')
    git(repo, 'config', 'user.name', 'Indexer test')
    git(repo, 'config', 'user.email', 'indexer-test@example.invalid')
    folder = repo / 'ofl' / 'fixture'
    folder.mkdir(parents=True)
    metadata = folder / 'METADATA.pb'
    metadata.write_text('name: "Fixture" designer: "Test Author" license: "OFL" fonts { filename: "Fixture.ttf" weight: 400 style: "normal" } unknown { future: "kept" }', encoding='utf-8')
    (folder / 'Fixture.ttf').write_bytes(b'not downloaded by indexer')
    (folder / 'OFL.txt').write_text('Fixture license', encoding='utf-8')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'Valid fixture')
    app = create_app(root / 'data', str(repo))
    client = app.test_client()
    assert client.get('/api/v1/search').json['total'] == 0
    response = client.post('/api/v1/sources/google-fonts/sync')
    assert response.status_code == 202
    complete = wait(client, response.json['job_id'])
    assert complete['status'] == 'completed', complete
    assert not (root / 'data' / 'repository' / 'ofl' / 'fixture' / 'Fixture.ttf').exists()
    record = client.get('/api/v1/families/gf-fixture').json
    assert record['raw']['unknown'][0]['future'] == ['kept']
    assert record['documents']['OFL.txt'] == 'Fixture license'
    assert complete['revision'] in record['files'][0]['url']
    assert client.get('/api/v1/search?q=test+author').json['total'] == 1
    assert client.get('/api/v1/search?limit=bad').status_code == 400
    assert client.post('/api/v1/sources/google-fonts/sync').status_code == 429
    # An invalid new snapshot must not remove the published family or documents.
    metadata.write_text('name: "Broken" fonts {', encoding='utf-8')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'Broken fixture')
    os.environ['FONT_INDEXER_MIN_INTERVAL'] = '0'
    # Expected corruption is asserted without emitting a fake CI error annotation.
    from unittest import TestCase
    with TestCase().assertLogs('font-indexer',level='ERROR') as captured:
        failed = wait(client, client.post('/api/v1/sources/google-fonts/sync').json['job_id'])
    assert 'Unclosed metadata message' in '\n'.join(captured.output)
    assert failed['status'] == 'failed'
    assert client.get('/api/v1/families/gf-fixture').json['revision'] == complete['revision']
    assert client.get('/api/v1/search').json['total'] == 1
    # Restart recovery records interruption instead of pretending the job finished.
    import sqlite3
    with sqlite3.connect(root / 'data' / 'index.sqlite') as con:
        con.execute("INSERT INTO jobs(id,status,phase,created) VALUES('interrupted','running','Fetch',?)", (time.time(),))
    con.close()
    restarted = create_app(root / 'data', str(repo)).test_client()
    assert restarted.get('/api/v1/jobs/interrupted').json['status'] == 'failed'
    os.environ.pop('FONT_INDEXER_MIN_INTERVAL', None)
    os.environ['FONT_INDEXER_TOKEN'] = 'fixture-secret'
    assert restarted.get('/api/v1/health').status_code == 401
    assert restarted.get('/api/v1/health', headers={'X-Api-Key': 'fixture-secret'}).status_code == 200
    os.environ.pop('FONT_INDEXER_TOKEN')
print('PASS: raw metadata, pinned artifacts, no binary checkout, atomic failure, cooldown, restart recovery and API key')
