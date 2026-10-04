"""Live Google Fonts smoke test in a disposable catalogue."""

# Direct execution from the repository root keeps application imports available.
import sys as _test_sys
from pathlib import Path as _TestPath
_test_sys.path.insert(0,str(_TestPath(__file__).resolve().parents[1]))
import os
import tempfile

with tempfile.TemporaryDirectory() as folder:
    os.environ['FONTHUB_DATA'] = folder
    import app
    client = app.app.test_client()
    # The independent service must already have a published snapshot. This
    # test never mutates its index and imports binaries only into temp data.
    assert app.google_fonts.index(app.GOOGLE_DATA)['families'], 'Start and sync the indexer first'
    result = client.get('/api/search?scope=remote&q=ABeeZee').json
    assert result['total'] == 1, result
    remote = result['faces'][0]
    assert remote['remote'] and 'coverage' not in remote
    assert client.get('/api/search?scope=remote&coverage=1&text=ABC').json['total'] == 0
    # Viewing metadata must not implicitly download a binary or invent coverage.
    from unittest.mock import patch
    with patch.object(app.google_fonts, 'binary', side_effect=AssertionError('Implicit download')):
        page = client.get('/sources/google-fonts/fonts/' + remote['id'])
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'class="font-layout"' in html and 'class="info-column"' in html
    assert 'aria-disabled="true"' in html and 'id="show-remote-preview"' in html
    assert '@font-face' not in html and 'id="coverage-filter"' not in html
    record=app.google_fonts.family(app.GOOGLE_DATA,remote['id'])
    downloaded=client.get('/sources/google-fonts/fonts/'+remote['id']+'/download/0?revision='+record['revision'])
    assert downloaded.status_code==200 and 'downloadJob=' in downloaded.get_data(as_text=True)
    import font_downloads
    while font_downloads.process(app.db,app.DATA,app.GOOGLE_DATA,app.google_fonts):pass
    downloaded.close()
    downloaded=client.get('/sources/google-fonts/fonts/'+remote['id']+'/download/0?revision='+record['revision'])
    assert downloaded.status_code==200 and 'attachment' in downloaded.headers['Content-Disposition']
    downloaded.close()
    assert client.get('/sources/google-fonts/fonts/'+remote['id']+'/download/0?revision=old').status_code==409
    assert client.get('/font/' + remote['id']).status_code == 200
    response = client.post('/api/sources/google-fonts/import/' + remote['id'])
    assert response.status_code == 202, response.json
    assert client.get(response.json['url']).status_code == 200
    while font_downloads.process(app.db,app.DATA,app.GOOGLE_DATA,app.google_fonts):pass
    while app.process_import(): pass
    while app.process_analysis(): pass
    faces = client.get('/api/catalog').json['faces']
    assert len(faces) == 2 and all(face['source'] == 'google-fonts' for face in faces)
    assert client.get('/api/search?scope=all&q=ABeeZee').json['total'] == 1
    assert client.post('/api/sources/google-fonts/import/gf-not-real').status_code == 404
    print('PASS: Google Fonts live index, search, preview, family import and provenance')
