"""Offline authoring command: valid fixture, empty data, URL policy, inactivity."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

module='examples.adapters.example_json'
fixture=json.loads(Path('examples/adapters/responses.json').read_text(encoding='utf-8'))

def run(path,adapter=module):
    return subprocess.run([sys.executable,'-m','indexer.check',adapter,'--fixture',str(path)],capture_output=True,text=True)

assert run('examples/adapters/responses.json').returncode==0
with tempfile.TemporaryDirectory() as directory:
    root=Path(directory);path=root/'response.json'
    # No captured request is allowed to fall through to the network.
    path.write_text('{}');assert run(path).returncode!=0
    empty={url:{'families':[]} for url in fixture}
    path.write_text(json.dumps(empty));assert run(path).returncode!=0
    invalid=json.loads(json.dumps(fixture))
    # The example's metadata URL remains approved; its advertised font host is not.
    original=json.dumps(invalid)
    path.write_text(original.replace('https://example.org/fonts/','https://unapproved.invalid/fonts/'),encoding='utf-8')
    assert run(path).returncode!=0
    assert run('examples/adapters/responses.json','font-library').returncode!=0
print('PASS: adapter CLI, captured-only requests, artifact host policy and inactive rejection')
