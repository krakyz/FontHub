"""Run a trusted adapter without publishing or downloading font binaries.

Fixture mode allows only URLs explicitly captured in a JSON response map;
any uncaptured request fails. --live is required for actual acquisition.
This shares the publication validator, but cross-source ID collisions are
checked only when publishing against an existing indexer database.
"""
import argparse,importlib,json,tempfile
from pathlib import Path
from urllib.parse import urlparse
from .adapters import ADAPTERS
from .transport import Context
from .validation import normalize

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('adapter',help='Registered ID or trusted Python module')
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--fixture',type=Path,help='JSON map: exact HTTPS URL -> JSON/text response')
    group.add_argument('--live',action='store_true',help='Explicitly allow upstream metadata acquisition')
    parser.add_argument('--repository',help='Optional local Git fixture/repository override')
    args=parser.parse_args()
    adapter=ADAPTERS.get(args.adapter) or importlib.import_module(args.adapter)
    if not getattr(adapter,'ACTIVE',True): parser.error('Adapter is inactive')
    with tempfile.TemporaryDirectory() as data:
        context=Context(data,adapter.ID,'check',adapter.HOSTS,args.repository)
        if args.fixture:
            responses=json.loads(args.fixture.read_text(encoding='utf-8'))
            def text(url):
                parsed=urlparse(url)
                if parsed.scheme!='https' or parsed.hostname not in adapter.HOSTS or url not in responses:
                    raise ValueError('Uncaptured/unapproved fixture request: '+url)
                value=responses[url]
                return value if isinstance(value,str) else json.dumps(value)
            context.text=text
            def checkout(*args,**kwargs): raise ValueError('Git fixture checks use --live --repository LOCAL_PATH')
            context.checkout=checkout
        revision,records=adapter.collect(context)
        rows=normalize(adapter,revision,records)
        print(json.dumps(dict(adapter=adapter.ID,revision=revision,records=len(rows),status='valid'),ensure_ascii=False))

if __name__=='__main__': main()
