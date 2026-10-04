"""Noto distribution JSON adapter. All advertised build variants are retained."""
import re
from urllib.parse import quote
ID='noto'
REPOSITORY='https://github.com/notofonts/notofonts.github.io'
HOSTS={'api.github.com','raw.githubusercontent.com','github.com'}
CAPABILITIES=['catalogue','search','download','preview']

def collect(context):
    repo,revision=context.checkout(REPOSITORY+'.git',['/state.json','/fontrepos.json'])
    root='https://raw.githubusercontent.com/notofonts/notofonts.github.io/'+revision+'/'
    import json
    states=json.loads((repo/'state.json').read_text(encoding='utf-8'))
    projects=json.loads((repo/'fontrepos.json').read_text(encoding='utf-8'))
    rows=[]
    for project,state in states.items():
        for family,raw in state.get('families',{}).items():
            files=[]
            for path in raw.get('files',[]):
                if not path.startswith('fonts/') or '..' in path.split('/'):raise ValueError('Unsafe Noto path')
                if path.lower().endswith(('.ttf','.otf','.woff','.woff2')):
                    files.append(dict(name=path.removeprefix('fonts/').replace('/','--'),filename=path.rsplit('/',1)[-1],url=root+quote(path),format=path.rsplit('.',1)[-1].upper(),build='/'.join(path.split('/')[2:-1])))
            if not files:continue
            release=raw.get('latest_release',{})
            slug=re.sub('[^a-z0-9]+','-',family.casefold()).strip('-')
            rows.append(dict(id='noto-'+slug,source=ID,family=family,author='Noto project',license=None,category=None,date_added=None,updated=release.get('published'),axes=[],variants=[],subsets=[],declared_languages=[],files=files,revision=revision,url=release.get('url') or 'https://github.com/notofonts/'+project,styles=[],raw=dict(project=project,family=raw,project_metadata=projects.get(project),state=state),documents={'RELEASE.txt':release.get('notes','')},field_states={'coverage':'not_analyzed','styles':'not_provided','type':'not_provided'}))
    return revision,rows

DOWNLOAD_PREFIXES={'raw.githubusercontent.com': '/notofonts/notofonts.github.io/{revision}/'}
