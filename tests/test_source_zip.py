"""Bounded archive handling; path traversal members are never extracted by path."""

# Direct execution from the repository root keeps application imports available.
import sys as _test_sys
from pathlib import Path as _TestPath
_test_sys.path.insert(0,str(_TestPath(__file__).resolve().parents[1]))
import tempfile,zipfile
from pathlib import Path
from unittest.mock import patch
import indexer_client
with tempfile.TemporaryDirectory() as folder:
    archive=Path(folder)/'release.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('../../escape.ttf',Path('C:/Windows/Fonts/arial.ttf').read_bytes())
        z.writestr('README.txt','not a font')
    with patch.object(indexer_client,'resolve',return_value={'artifacts':[{'name':'release.zip','container':'zip'}]}),patch.object(indexer_client,'binary',return_value=archive):
        paths=indexer_client.font_paths(Path(folder),{'id':'fixture'})
    assert len(paths)==1 and paths[0].parent==Path(folder)/'extracted'
    assert not (Path(folder).parent/'escape.ttf').exists()
print('PASS: explicit ZIP font extraction and member path isolation')
