"""One normalization/validation boundary used by publication and adapter checks."""
import json,re
from pathlib import Path
from urllib.parse import urlparse

def normalize(adapter,revision,records):
    source_id=adapter.ID
    if not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})',revision):
        raise ValueError('Adapter revision must be a SHA-1/SHA-256 identifier')
    ids = set()
    rows = []
    for record in records:
        # Thin adapters may return only identity/name and their evidence.
        # The socket fills unknowns; it never invents font characteristics.
        supplied_axes = 'axes' in record
        defaults = dict(source=source_id,revision=revision,author='',url=adapter.REPOSITORY,
                        license=None,category=None,date_added=None,updated=None,
                        axes=[],variants=[],subsets=[],declared_languages=[],styles=[],files=[],raw={},documents={})
        record = dict(defaults,**record)
        record.setdefault('field_states',{})
        record['field_states'].setdefault('coverage','not_analyzed')
        if not supplied_axes: record['field_states'].setdefault('type','not_provided')
        if record['source'] != source_id or not record['family'] or record['id'] in ids:
            raise ValueError('Invalid or ambiguous adapter record')
        if record['revision'] != revision: raise ValueError('Record revision differs from snapshot')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',record['id']): raise ValueError('Unsafe record ID')
        policy={host:prefix.format(revision=revision) for host,prefix in getattr(adapter,'DOWNLOAD_PREFIXES',{}).items()}
        record['download_policy']=policy
        for artifact in record['files']:
            parsed=urlparse(artifact['url'])
            name=artifact['name']
            if Path(name).name!=name or '/' in name or chr(92) in name or parsed.scheme!='https' or parsed.hostname not in policy or not parsed.path.startswith(policy[parsed.hostname]):
                raise ValueError('Unsafe adapter artifact')
        ids.add(record['id'])
        raw, documents = record.pop('raw'), record.pop('documents')
        rows.append((revision,record['id'],json.dumps(record,ensure_ascii=False),json.dumps(raw,ensure_ascii=False),json.dumps(documents,ensure_ascii=False)))
    if not rows: raise ValueError('Empty snapshot; previous index retained')
    return rows
