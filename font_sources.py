import json
from urllib.request import Request, urlopen
from email.utils import parsedate_to_datetime

SOURCES = [
    dict(id='local', name='Локальный', url='', repo='', download='Папка inbox'),
    dict(id='google-fonts', name='Google Fonts', url='https://github.com/google/fonts', repo='google/fonts', download='ZIP репозитория или Git'),
    dict(id='noto', name='Noto', url='https://notofonts.github.io/', repo='notofonts/notofonts.github.io', download='Файлы и архивы семейств'),
    dict(id='adobe', name='Открытые шрифты Adobe', url='https://github.com/adobe-fonts', repo='', download='Releases выбранного семейства'),
    dict(id='font-library', name='Font Library', url='https://fontlibrary.org/', repo='', download='Пакет со страницы семейства'),
    dict(id='font-squirrel', name='Font Squirrel', url='https://www.fontsquirrel.com/', repo='', download='Пакет или сайт автора'),
]
SOURCE_NAMES = {row['id']: row['name'] for row in SOURCES}


def check_source(source):
    result = dict(available=False, updated=None, error='')
    try:
        with urlopen(Request(source['url'], headers={'User-Agent': 'FontHub/1.0'}, method='HEAD'), timeout=10) as response:
            result['available'] = 200 <= response.status < 400
            modified = response.headers.get('Last-Modified')
            if modified:
                result['updated'] = parsedate_to_datetime(modified).isoformat()
    except Exception as exc:
        result['error'] = str(exc)[:200]
    if result['available'] and source['repo']:
        try:
            url = 'https://api.github.com/repos/' + source['repo'] + '/commits?per_page=1'
            with urlopen(Request(url, headers={'User-Agent': 'FontHub/1.0'}), timeout=10) as response:
                result['updated'] = json.load(response)[0]['commit']['committer']['date']
        except Exception:
            pass
    return result
