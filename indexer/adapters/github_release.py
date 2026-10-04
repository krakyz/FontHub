"""GitHub release asset discovery through public HTML, without API quota.
This helper uses the shared transport so documents, retries and host policy
remain consistent. Missing asset fragments fail instead of guessing URLs.
"""
import re
from .html_catalogue import Links

def release_assets(context, repository):
    text=context.text(repository+'/releases/latest')
    if 'There aren’t any releases here' in text or "There aren't any releases here" in text:
        return [],dict(page=text,url=repository+'/releases/latest',state='no_releases')
    fragments=re.findall(r'https://github\.com/[^"\s<>]+/releases/expanded_assets/[^"\s<>]+',text)
    if not fragments:raise ValueError('GitHub release asset fragment not found')
    fragment=fragments[0]
    if not fragment.startswith(repository+'/releases/expanded_assets/'):raise ValueError('Unexpected release fragment')
    assets=context.text(fragment)
    files=[]
    for url,label in Links(assets,repository).links:
        if url.startswith(repository+'/releases/download/') and url.lower().endswith(('.zip','.ttf','.otf','.ttc','.otc','.woff','.woff2')):
            name=url.rsplit('/',1)[-1]
            files.append(dict(name=name,url=url,format='ZIP' if name.lower().endswith('.zip') else name.rsplit('.',1)[-1].upper(),container='zip' if name.lower().endswith('.zip') else 'font'))
    return files,dict(page=text,assets=assets,url=fragment)
