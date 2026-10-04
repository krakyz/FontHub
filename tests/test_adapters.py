"""Offline adapter checks, using captured Noto/Adobe responses and HTML fixtures.
These prove parsing contracts, not live availability of the two HTML sources.
"""

# Direct execution from the repository root keeps application imports available.
import sys as _test_sys
from pathlib import Path as _TestPath
_test_sys.path.insert(0,str(_TestPath(__file__).resolve().parents[1]))
import tempfile
from pathlib import Path
from unittest.mock import patch
from indexer.adapters import noto,adobe,font_library,font_squirrel
from indexer.adapters.html_catalogue import pages
FIXTURES=Path(__file__).parent/'fixtures'
class Fixture:
    hosts={'fontlibrary.org','www.fontsquirrel.com'}
    def checkout(self,repository,patterns,**kwargs):
        return FIXTURES/'git-metadata','a'*40
    def git(self,*args):
        return 'TTF/SourceSans3-Regular.ttf\nLICENSE.md'
    def text(self,url):
        if 'fontlibrary' in url:return '<a href="/en/font/test-font">Test Font</a>'
        return '<a href="/fonts/test-font">Test Font</a>'
revision,rows=noto.collect(Fixture())
assert len(rows)==2 and rows[0]['source']=='noto'
assert len({file['name'] for file in rows[0]['files']})==len(rows[0]['files'])
assert all(revision in file['url'] for row in rows for file in row['files'])
assert rows[0]['raw']['project_metadata']['unknown']=='preserved'
assert rows[0]['field_states']['type']=='not_provided'
with patch.object(adobe,'discover',return_value={'source-sans':{}}):
    revision,rows=adobe.collect(Fixture())
assert len(rows)==1 and rows[0]['documents']['LICENSE.md']=='Fixture license'
assert all(file['format']=='TTF' for file in rows[0]['files']) and rows[0]['updated'] is None
assert rows[0]['styles']==[] # asset names are not a list of verified font styles
# Organization discovery must include page two, retain metadata, and reject a
# changed/access-protected listing instead of publishing a partial catalogue.
import json
class Organization:
    def text(self,url):
        page=int(url.rsplit('=',1)[-1])
        return '<script type="application/json">'+json.dumps({'payload':{'orgReposPageRoute':{'pageCount':2,'repositories':[{'name':'font-'+str(page),'owner':'adobe-fonts','unknown':'retained'}]}}})+'</script>'
assert set(adobe.discover(Organization()))=={'font-1','font-2'}
assert adobe.discover(Organization())['font-2']['unknown']=='retained'
try:adobe.discover(Fixture());raise AssertionError('Accepted invalid organization listing')
except ValueError:pass
from urllib.error import HTTPError
class MixedProjects(Fixture):
    def checkout(self,repository,patterns,**kwargs):
        self.project=kwargs['key']
        return super().checkout(repository,patterns,**kwargs)
    def git(self,*args):
        return super().git(*args) if self.project=='new-font' else 'README.md'
with patch.object(adobe,'discover',return_value={'new-font':{'license':'OFL','lastUpdated':{'timestamp':'2026-10-04'}},'tool':{}}), patch('indexer.adapters.github_release.release_assets',side_effect=HTTPError('https://github.com',404,'No release',{},None)):
    _, discovered=adobe.collect(MixedProjects())
assert [row['id'] for row in discovered]==['adobe-new-font']
assert discovered[0]['license']=='OFL' and discovered[0]['updated']=='2026-10-04'
from indexer.adapters.github_release import release_assets
class NoReleases:
    def text(self,url):return '<h2>There aren’t any releases here</h2>'
assert release_assets(NoReleases(),'https://github.com/adobe-fonts/source-only')[0]==[]
for adapter in [font_library,font_squirrel]:
    revision,rows=adapter.collect(Fixture())
    assert len(rows)==1 and rows[0]['source']==adapter.ID and not rows[0]['files']
    assert rows[0]['field_states']['download']=='external_page'
class Blocked(Fixture):
    def text(self,url):return '<html>Access challenge</html>'
for adapter in [font_library,font_squirrel]:
    try:adapter.collect(Blocked());raise AssertionError('Published blocked response')
    except ValueError:pass
# Publishing independently must preserve another source's active snapshot.
from indexer.service import create_app
import time,os
os.environ['FONT_INDEXER_MIN_INTERVAL']='0'
with tempfile.TemporaryDirectory() as folder, patch('indexer.service.Context',lambda *args,**kwargs:Fixture()):
    client=create_app(folder).test_client()
    assert client.post('/api/v1/sources/font-library/sync').status_code==409
    assert client.post('/api/v1/sources/font-squirrel/sync').status_code==409
    for source in ['noto','adobe','font-library','font-squirrel']:
        with patch.object(adobe,'discover',return_value={'source-sans':{}}),patch.object(font_library,'ACTIVE',True),patch.object(font_squirrel,'ACTIVE',True):
            job=client.post('/api/v1/sources/'+source+'/sync').json['job_id']
            for _ in range(200):
                status=client.get('/api/v1/jobs/'+job).json
                if status['status'] in {'completed','failed'}:break
                time.sleep(.02)
            assert status['status']=='completed',status
        assert client.get('/api/v1/search?source='+source).json['total']>0
    assert client.get('/api/v1/search').json['total']==5
    assert client.get('/api/v1/search?source=noto&type=static').json['total']==0
    # A custom adapter needs only identity/name; optional fields are socket work.
    # Reusing another source's revision must not overwrite its snapshot count.
    from types import SimpleNamespace
    from indexer.adapters import ADAPTERS
    custom=SimpleNamespace(ID='custom',REPOSITORY='https://example.org',HOSTS=set(),CAPABILITIES=['catalogue','search'],collect=lambda context:('a'*40,[{'id':'custom-one','family':'Fixture'}]))
    with patch.dict(ADAPTERS,{'custom':custom}):
        job=client.post('/api/v1/sources/custom/sync').json['job_id']
        for _ in range(200):
            status=client.get('/api/v1/jobs/'+job).json
            if status['status'] in {'completed','failed'}:break
            time.sleep(.02)
        assert status['status']=='completed',status
        record=client.get('/api/v1/families/custom-one').json
        assert record['source']=='custom' and record['license'] is None and record['axes']==[]
        sources={item['id']:item for item in client.get('/api/v1/sources').json['sources']}
        assert sources['custom']['snapshot']['count']==1 and sources['noto']['snapshot']['count']==2
os.environ.pop('FONT_INDEXER_MIN_INTERVAL')
print('PASS: Noto, Adobe, Font Library, Font Squirrel adapters; unknown fields; blocked HTML; independent snapshots')
