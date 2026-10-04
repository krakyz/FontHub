
# Direct execution from the repository root keeps application imports available.
import sys as _test_sys
from pathlib import Path as _TestPath
_test_sys.path.insert(0,str(_TestPath(__file__).resolve().parents[1]))
import io
import os
import shutil
import tempfile
import time
from pathlib import Path

with tempfile.TemporaryDirectory() as folder:
    os.environ['FONTHUB_DATA'] = folder
    import app
    source = Path('C:/Windows/Fonts/arial.ttf')
    assert source.exists(), 'For this local smoke test, Arial must be installed'
    original = source.read_bytes()
    incoming = app.INBOX / 'arial.ttf'
    shutil.copyfile(source, incoming)
    os.utime(incoming, (time.time()-10, time.time()-10))
    app.scan()
    client = app.app.test_client()
    assert client.get('/').status_code == 200
    catalog = client.get('/api/catalog').json
    assert len(catalog['faces']) == 1, catalog
    face = catalog['faces'][0]
    assert face['source'] == 'local'
    assert client.post('/api/fonts/' + face['id'] + '/source', json={'source': 'unknown'}).status_code == 400
    changed = client.post('/api/fonts/' + face['id'] + '/source', json={'source': 'google-fonts'})
    assert changed.status_code == 200
    changed_id = changed.json['url'].rsplit('/', 1)[1]
    assert client.get('/api/search?source=google-fonts').json['total'] == 1
    assert client.get('/api/search?source=internet').json['total'] == 1
    assert client.get('/api/search?source=local').json['total'] == 0
    app.reanalyze_file(face['id'].split('~')[0].rsplit('-', 1)[0])
    assert client.get('/api/catalog').json['faces'][0]['source'] == 'google-fonts'
    assert client.post('/api/fonts/' + changed_id + '/source', json={'source': 'local'}).status_code == 200
    app.check_source = lambda source: dict(available=True, updated='2026-10-03T00:00:00Z', error='')
    assert client.post('/api/sources/google-fonts/check').json['available']
    assert next(s for s in client.get('/api/sources').json['sources'] if s['id']=='google-fonts')['status']['updated'] == '2026-10-03T00:00:00Z'
    assert client.get('/sources').status_code == 200
    assert 'Русский' in face['languages']
    assert client.get('/download/' + face['id']).data == original
    assert client.get('/download-family/' + face['id']).data == original
    import zipfile
    single_zip = client.get('/download-family/' + face['id'] + '?zip=1')
    assert single_zip.mimetype == 'application/zip'
    with zipfile.ZipFile(io.BytesIO(single_zip.data)) as archive:
        assert archive.read('arial.ttf') == original
    while app.process_preview(): pass
    assert client.get('/font/' + face['id']).data[:4] == b'wOF2'
    shutil.copyfile(source, incoming)
    os.utime(incoming, (time.time()-10, time.time()-10))
    app.scan()
    assert len(client.get('/api/catalog').json['faces']) == 1
    assert not incoming.exists()
    from fontTools.ttLib import TTFont
    source_folder = app.INBOX / 'google-fonts'
    source_folder.mkdir()
    converted = source_folder / 'arial.woff2'
    with TTFont(source) as font:
        font.flavor = 'woff2'
        font.save(converted)
    converted_bytes = converted.read_bytes()
    os.utime(converted, (time.time()-10, time.time()-10))
    app.scan()
    faces = client.get('/api/catalog').json['faces']
    assert len(faces) == 2
    assert {f['format'] for f in faces} == {'TTF', 'WOFF2'}
    assert client.get('/api/search').json['total'] == 2
    assert {tuple(f['formats']) for f in client.get('/api/search').json['faces']} == {('TTF',), ('WOFF2',)}
    assert client.get('/api/search?q=arial+regular').json['total'] == 2
    assert client.get('/api/search?q=arial&format=WOFF2').json['total'] == 1
    assert client.get('/api/search?coverage=1&text=🦄').json['total'] == 0
    woff = next(f for f in faces if f['format'] == 'WOFF2')
    assert woff['source'] == 'google-fonts'
    assert client.get('/api/search?source=google-fonts').json['faces'][0]['formats'] == ['WOFF2']
    assert {tuple(f['sources']) for f in client.get('/api/search').json['faces']} == {('local',), ('google-fonts',)}
    assert client.get('/download/' + woff['id']).data == converted_bytes
    import zipfile
    response = client.get('/download-family/' + face['id'] + '?zip=1')
    assert response.mimetype == 'application/zip'
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        assert len(archive.namelist()) == 1
        assert archive.read('arial.ttf') == original

    assert client.get('/download-family/no-such-font').status_code == 404

    detail = client.get('/fonts/' + face['id'])
    assert detail.status_code == 200
    assert detail.get_data(as_text=True).count('Скачать всё в ZIP') == 2
    assert b'arial.woff2' not in detail.data  # Other source is not merged into this family table.
    assert client.get('/fonts/missing').status_code == 404
    broken = app.INBOX / 'broken.ttf'
    broken.write_bytes(b'not a font')
    os.utime(broken, (time.time()-10, time.time()-10))
    app.scan()
    assert not broken.exists()
    with app.db() as con:
        assert con.execute("SELECT COUNT(*) FROM font_checks WHERE status='unsupported'").fetchone()[0]
    assert not list((app.DATA / 'quarantine').iterdir())
    assert client.post('/api/upload', data={'files': (io.BytesIO(b'x'), '../bad.exe')}).status_code == 400
    assert client.post('/api/upload', data={'files': (io.BytesIO(original), '../arial.ttf')}).status_code == 200
    assert client.get('/download/no-such-font').status_code == 404
    import json
    with app.db() as con:
        con.execute("INSERT INTO files(hash,name,path,source) VALUES('test','test.ttf',?,'local')", (str(source),))
        con.execute("INSERT INTO origins VALUES('test','local',NULL)")
        for index in range(26):
            extra = dict(face, id=f'test-{index}', family=f'Test Family {index:02}')
            con.execute('INSERT INTO faces VALUES(?,?,?)', (extra['id'], 'test', json.dumps(extra)))
    first = client.get('/api/search').json
    second = client.get('/api/search?page=2').json
    assert first['total'] == 28 and len(first['faces']) == 10 and first['pages'] == 3
    assert len(second['faces']) == 10
    assert not ({f['id'] for f in first['faces']} & {f['id'] for f in second['faces']})
    assert client.get('/api/search?page=999').json['page'] == 3
    import struct
    from font_analysis import recover_format4
    # Two segments: A/B reference one actual entry; the sentinel is unmapped.
    raw = struct.pack('>8H', 4, 34, 0, 4, 4, 1, 0, 66)
    raw += struct.pack('>9H', 65535, 0, 65, 65535, 0, 1, 4, 0, 1)
    recovered, omitted = recover_format4(raw, ['.notdef', 'A'])
    assert recovered == {65: 'A'} and omitted == 1
    try:
        recover_format4(raw[:20], ['.notdef', 'A'])
        raise AssertionError('Truncated arrays accepted')
    except app.TTLibError:
        pass
    from language_coverage import analyze_languages, orthographies
    russian = next(row for row in orthographies() if row[0] == 'rus')[3]
    full = next(row for row in analyze_languages(tuple(sorted(russian)))['languages'] if row['iso'] == 'rus')
    assert full['full'] and full['percent'] == 100 and full['missing'] == []
    partial = next(row for row in analyze_languages(tuple(sorted(russian - {ord('Ё')})))['languages'] if row['iso'] == 'rus')
    assert not partial['full'] and partial['percent'] < 100 and 'Ё' in partial['missing']
    japanese = analyze_languages(tuple(range(0x3041, 0x3097)))
    assert japanese['kana'][0]['percent'] == 100 and japanese['kana'][1]['percent'] == 0
    assert any(row['code'] == 'Hira' for row in japanese['scripts'])
    # Equal bytes from another source create a logical instance, not a new file.
    duplicate_from_web = source_folder / 'arial.ttf'
    duplicate_from_web.write_bytes(original)
    app.import_file(duplicate_from_web)
    with app.db() as con:
        digest = face['id'].split('~')[0].rsplit('-', 1)[0]
        assert con.execute('SELECT COUNT(*) FROM origins WHERE hash=?', (digest,)).fetchone()[0] == 2
        assert con.execute('SELECT COUNT(*) FROM files WHERE hash=?', (digest,)).fetchone()[0] == 1
    assert client.get('/api/search?q=arial&source=google-fonts').json['total'] == 1
    rows = client.get('/api/search?scope=archive').json['faces']
    assert {row['source'] for row in rows if row['family'] == 'Arial'} == {'local', 'google-fonts'}
    # Archive search never depends on a running indexer or its cached metadata.
    from unittest.mock import patch
    with patch.object(app.google_fonts, 'index', side_effect=RuntimeError('Offline')):
        assert client.get('/api/search?scope=archive').status_code == 200
    google_face = next(f for f in client.get('/api/catalog').json['faces'] if f['source']=='google-fonts' and f['format']=='TTF')
    assert client.get('/download/' + google_face['id']).data == original
    with zipfile.ZipFile(io.BytesIO(client.get('/download-family/' + google_face['id']).data)) as archive:
        assert set(archive.namelist()) == {'arial.ttf', 'arial.woff2'}
    duplicate_from_web.write_bytes(original)
    app.import_file(duplicate_from_web)
    with app.db() as con:
        assert con.execute('SELECT COUNT(*) FROM origins WHERE hash=?', (digest,)).fetchone()[0] == 2
    print('PASS: import, metadata, preview, duplicates, separate formats, search, detail pages, quarantine, upload validation')
