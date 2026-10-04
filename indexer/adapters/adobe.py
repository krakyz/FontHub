"""Discover Adobe's public repositories without per-family GitHub API calls.
Only metadata files are checked out. Artifact paths come from the pinned Git
 tree, not guessed filenames. Type/styles remain unknown until font analysis.
"""
import hashlib,json,re
from urllib.error import HTTPError
from pathlib import Path
from urllib.parse import quote
ID='adobe'
REPOSITORY='https://github.com/adobe-fonts'
HOSTS={'github.com','raw.githubusercontent.com'}
CAPABILITIES=['catalogue','search','documents','download','preview']
DISPLAY_NAMES={'source-sans':'Source Sans','source-serif':'Source Serif','source-code-pro':'Source Code Pro','source-han-sans':'Source Han Sans','source-han-serif':'Source Han Serif'}
DOWNLOAD_PREFIXES={'raw.githubusercontent.com':'/adobe-fonts/','github.com':'/adobe-fonts/'}

def discover(context):
    """Read the public organization listing, including every pagination page.

    GitHub embeds structured repository metadata in HTML. This avoids the
    unauthenticated API quota. A missing/changed payload fails the job rather
    than silently publishing a truncated catalogue. Repository metadata is
    retained in each record; HTTP originals are retained by Context.
    """
    projects={}
    page=1
    while True:
        html=context.text('https://github.com/orgs/adobe-fonts/repositories?type=all&page='+str(page))
        payload=None
        for value in re.findall(r'<script\b[^>]*>(.*?)</script>',html,re.S):
            try: data=json.loads(value)
            except ValueError: continue
            payload=data.get('payload',{}).get('orgReposPageRoute')
            if payload is not None: break
        if not payload or not isinstance(payload.get('repositories'),list):
            raise ValueError('GitHub organization repository listing is unavailable or changed')
        count=payload.get('pageCount')
        if not isinstance(count,int) or not 1<=count<=100:
            raise ValueError('Invalid GitHub repository pagination')
        for repo in payload['repositories']:
            name=repo['name']
            if repo.get('owner')!='adobe-fonts' or not re.fullmatch(r'[a-zA-Z0-9_.-]+',name):
                raise ValueError('Unexpected Adobe repository identity')
            projects[name]=repo
        if page>=count: break
        page+=1
    if not projects: raise ValueError('Adobe organization listing is empty')
    return projects

def collect(context):
    rows=[]
    for project,metadata in sorted(discover(context).items()):
        # Actual distributable artifacts decide inclusion, not repository names
        # or GitHub's sometimes ambiguous license classification ("Other").
        family=DISPLAY_NAMES.get(project,project.replace('-',' ').title())
        repo,commit=context.checkout(REPOSITORY+'/'+project+'.git',['/LICENSE*','/OFL*','/README*','/package.json'],key=project,branch='HEAD')
        paths=context.git('ls-tree','-r','--name-only',commit).splitlines()
        files=[dict(name=path.replace('/','--'),filename=path.rsplit('/',1)[-1],url='https://raw.githubusercontent.com/adobe-fonts/'+project+'/'+commit+'/'+quote(path),format=path.rsplit('.',1)[-1].upper(),artifact_revision=commit) for path in paths if path.lower().endswith(('.ttf','.otf','.woff','.woff2','.ttc','.otc'))]
        release=None
        if not files:
            from .github_release import release_assets
            try: files,release=release_assets(context,REPOSITORY+'/'+project)
            except HTTPError as error:
                if error.code!=404: raise
        if not files: continue # Tools, documentation and source-only projects.
        documents={path.name:path.read_text(encoding='utf-8') for path in repo.iterdir() if path.is_file() and path.name.startswith(('LICENSE','OFL','README'))}
        rows.append(dict(id='adobe-'+project,source=ID,family=family,author='Adobe',license=metadata.get('license'),category=None,date_added=None,updated=(metadata.get('lastUpdated') or {}).get('timestamp'),axes=[],variants=[],subsets=[],declared_languages=[],files=files,url=REPOSITORY+'/'+project+'/tree/'+commit,styles=[],raw={'project':project,'repository':metadata,'commit':commit,'tree':paths,'release':release},documents=documents,field_states={'coverage':'not_analyzed','styles':'not_provided','type':'not_provided'}))
    if not rows: raise ValueError('No downloadable Adobe font projects found')
    revision=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
    for row in rows:row['revision']=revision
    return revision,rows
