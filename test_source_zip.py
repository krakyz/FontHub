"""Bounded archive handling; path traversal members are never extracted by path."""
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
