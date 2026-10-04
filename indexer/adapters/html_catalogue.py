"""Small HTML utilities shared by catalogue adapters (stdlib only)."""
from html.parser import HTMLParser
from urllib.parse import urljoin
class Links(HTMLParser):
    def __init__(self,text,base):
        super().__init__();self.links=[];self.active=None;self.base=base;self.feed(text)
    def handle_starttag(self,tag,attrs):
        if tag=='a':
            attrs=dict(attrs);self.active=[urljoin(self.base,attrs.get('href','')),[]]
    def handle_data(self,data):
        if self.active:self.active[1].append(data)
    def handle_endtag(self,tag):
        if tag=='a' and self.active:
            url,parts=self.active;label=' '.join(''.join(parts).split());self.links.append((url,label));self.active=None

def pages(context,start,is_family,is_page,source,prefix):
    """Traverse listing pages only; an incomplete/blocked page fails the snapshot.
    A catalogue link is not a verified downloadable file. Detail enrichment is
    deliberately advertised as unavailable until a source provides a manifest.
    """
    import hashlib,json,re
    from urllib.parse import urlparse
    queue=[start];visited=set();families={};documents={}
    while queue:
        url=queue.pop(0)
        if url in visited:continue
        if len(visited)>=200:raise ValueError('Catalogue exceeds 200 page budget; no partial snapshot published')
        text=context.text(url);visited.add(url);links=Links(text,url).links
        found=[(u.split('#')[0].split('?')[0],label) for u,label in links if label and urlparse(u).hostname in context.hosts and is_family(urlparse(u).path)]
        if not found:raise ValueError('No families in listing: source layout changed or access blocked')
        documents[url]=text
        for u,label in found:
            slug=urlparse(u).path.rstrip('/').split('/')[-1]
            if not re.fullmatch('[A-Za-z0-9_-]{1,150}',slug):slug=hashlib.sha256(u.encode()).hexdigest()[:16]
            families[u]=dict(id=prefix+slug,source=source,family=label,author='',license=None,category=None,date_added=None,updated=None,axes=[],variants=[],subsets=[],declared_languages=[],files=[],styles=[],url=u,raw={'listing_url':url,'family_url':u,'label':label},documents={},field_states={'coverage':'not_analyzed','type':'not_provided','styles':'not_provided','download':'external_page'})
        for u,label in links:
            if is_page(urlparse(u)) and u not in visited and u not in queue:queue.append(u)
    revision=hashlib.sha256(json.dumps(documents,sort_keys=True).encode()).hexdigest()
    for row in families.values():row['revision']=revision
    return revision,list(families.values())
