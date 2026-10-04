"""Metadata assertions must never be presented as measured coverage."""

# Direct execution from the repository root keeps application imports available.
import sys as _test_sys
from pathlib import Path as _TestPath
_test_sys.path.insert(0,str(_TestPath(__file__).resolve().parents[1]))
from source_languages import source_languages
assert source_languages({'declared_languages':['ru_Cyrl','en_Latn'],'subsets':['menu','latin']})['status']=={'rus':'declared','eng':'declared'}
assert source_languages({'declared_languages':['rus_Cyrl','eng_Latn']})['status']['rus']=='declared'
assert source_languages({'subsets':['cyrillic','latin-ext','menu']})['status']=={'rus':'subset','eng':'subset'}
assert source_languages({})['status']=={'rus':'unknown','eng':'unknown'}
assert source_languages({'declared_languages':['ru_Latn']})['status']['rus']=='unknown'
assert source_languages({'declared_languages':['ru_Cyrl']})['languages'][0]['name']=='Русский'
assert source_languages({'subsets':['menu']})['subsets']==[]
print('PASS: ISO normalization, script-aware declarations, subset hints, unknown data and menu exclusion')

import os,tempfile
from unittest.mock import patch
with tempfile.TemporaryDirectory() as folder:
    os.environ['FONTHUB_DATA']=folder
    import app
    base=dict(source='google-fonts',author='',variants=[],axes=[],files=[],revision='a'*40)
    rows=[dict(base,id='declared',family='Declared',declared_languages=['ru_Cyrl']),dict(base,id='hint',family='Hint',subsets=['cyrillic'])]
    with patch.object(app.google_fonts,'index',return_value={'families':rows,'sources':[]}),patch.object(app.google_fonts,'font_paths',side_effect=AssertionError('Implicit binary download')):
        client=app.app.test_client()
        all_rows=client.get('/api/search?scope=remote').json
        assert all_rows['total']==2
        filtered=client.get('/api/search?scope=remote&language=Русский').json
        assert [row['id'] for row in filtered['faces']]==['declared','hint']
        assert client.get('/api/search?scope=remote&language=English').json['total']==0
        assert client.get('/api/search?scope=remote&coverage=1').json['total']==0
print('PASS: language filter accepts declarations and matching subset scripts; exact text coverage excludes remote fonts; no binary download')
