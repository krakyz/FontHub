"""Copy into indexer/adapters, replace URLs, then check before enabling.

This example is intentionally outside the registry. It contains no real source
and never becomes an installed adapter merely by running its fixture check.
"""
import hashlib,json
ID='example-json'
REPOSITORY='https://example.org/catalogue.json'
HOSTS={'example.org'}
CAPABILITIES=['catalogue','search','download']
DOWNLOAD_PREFIXES={'example.org':'/fonts/'}

def collect(context):
    raw=context.json(REPOSITORY)
    revision=hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest()
    records=[]
    for family in raw['families']:
        records.append(dict(id=ID+'-'+family['id'],family=family['name'],author=family.get('designer',''),
                            declared_languages=family.get('languages',[]),subsets=family.get('subsets',[]),
                            files=[dict(name=file['name'],format=file['format'],url=file['url']) for file in family['files']],raw=family))
    return revision,records
