"""Google Fonts adapter: textproto mapping only; shared transport owns Git."""
from pathlib import Path
from urllib.parse import quote
from ..textproto import parse
ID = 'google-fonts'
REPOSITORY = 'https://github.com/google/fonts.git'
HOSTS = {'github.com', 'raw.githubusercontent.com'}
DOCUMENTS = ['METADATA.pb', 'DESCRIPTION.en_us.html', 'OFL.txt', 'LICENSE.txt', 'UFL.txt']
CAPABILITIES = ['catalogue', 'search', 'documents', 'download', 'preview']

def collect(context):
    repo, revision = context.checkout(REPOSITORY, ['/*/*/' + name for name in DOCUMENTS], legacy=True)
    tree = context.git('ls-tree', '-r', '--name-only', revision).splitlines()
    tree_paths = set(tree)
    paths = [path for path in tree if path.count('/') == 2 and path.split('/')[0] in {'ofl', 'apache', 'ufl'} and path.endswith('/METADATA.pb')]
    rows = []
    for relative in paths:
        text = (repo / relative).read_text(encoding='utf-8')
        raw = parse(text)
        one = lambda key, default=None: raw.get(key, [default])[0]
        name = one('name')
        fonts = raw.get('fonts', [])
        if not name or not fonts:
            raise ValueError('Incomplete family metadata: ' + relative)
        base = str(Path(relative).parent).replace('\\', '/')
        slug = Path(base).name
        files = []
        for font in fonts:
            filename = font.get('filename', [''])[0]
            if Path(filename).name != filename or '/' in filename or '\\' in filename or not filename.lower().endswith(('.ttf', '.otf')):
                raise ValueError('Unsafe/unsupported font filename in ' + relative)
            if base + '/' + filename not in tree_paths:
                raise ValueError('Font artifact missing from snapshot: ' + filename)
            files.append(dict(name=filename, url='https://raw.githubusercontent.com/google/fonts/' + revision + '/' + quote(base + '/' + filename), format=filename.rsplit('.', 1)[1].upper()))
        documents = {path.name: path.read_text(encoding='utf-8') for path in (repo / base).iterdir() if path.name in DOCUMENTS and path.is_file()}
        axes = [{k: v[0] for k, v in axis.items()} for axis in raw.get('axes', [])]
        normalized = dict(id='gf-' + slug, slug=slug, family=name, author=one('designer', ''), source='google-fonts',
                          license=one('license'), category=one('category'), date_added=one('date_added'), updated=None,
                          axes=axes, variants=[str(f.get('weight', [400])[0]) + ('i' if f.get('style', ['normal'])[0] == 'italic' else '') for f in fonts],
                          subsets=raw.get('subsets', []), declared_languages=raw.get('languages', []), files=files, revision=revision,
                          url='https://github.com/google/fonts/tree/' + revision + '/' + base,
                          field_states={'coverage': 'not_analyzed', 'updated': 'not_provided'})
        rows.append(dict(normalized, raw=raw, documents=documents))
    return revision, rows

DOWNLOAD_PREFIXES={'raw.githubusercontent.com': '/google/fonts/{revision}/'}
