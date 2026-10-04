from collections import Counter
from functools import lru_cache

from fontTools.unicodedata import script, script_name
from hyperglot.languages import Languages
from hyperglot.orthography import Orthography

NAMES = {'rus': 'Русский', 'eng': 'English', 'ukr': 'Украинский', 'bel': 'Белорусский', 'kaz': 'Казахский', 'pol': 'Польский', 'deu': 'Немецкий', 'fra': 'Французский', 'spa': 'Испанский', 'tur': 'Турецкий', 'ara': 'Арабский', 'ell': 'Греческий', 'heb': 'Иврит'}
SCRIPT_NAMES = {'Latn': 'Латиница', 'Cyrl': 'Кириллица', 'Grek': 'Греческое письмо', 'Arab': 'Арабское письмо', 'Hebr': 'Еврейское письмо', 'Hira': 'Хирагана', 'Kana': 'Катакана', 'Hani': 'Иероглифы Han', 'Hang': 'Хангыль', 'Deva': 'Деванагари'}


@lru_cache(maxsize=1)
def orthographies():
    database = Languages(validity='preliminary')
    result = []
    for iso in database:
        language = database[iso]
        if language.get('status') != 'living':
            continue
        for item in language.get('orthographies', []):
            if item.get('status') != 'primary':
                continue
            orthography = Orthography(item)
            required = {ord(c) for c in orthography.get_chars() if len(c) == 1}
            auxiliary = {ord(c) for c in orthography.get_chars('aux') if len(c) == 1}
            if required:
                result.append((iso, NAMES.get(iso, language['name']), item.get('script', ''), required, auxiliary))
    return result


@lru_cache(maxsize=256)
def analyze_languages(codepoints):
    covered = set(codepoints)
    counts = Counter(script(chr(c)) for c in covered)
    scripts = [{'code': code, 'name': SCRIPT_NAMES.get(code, script_name(code)), 'count': count} for code, count in counts.items() if code not in {'Zyyy', 'Zinh', 'Zzzz'}]
    scripts.sort(key=lambda row: -row['count'])
    reports = []
    for iso, name, writing, required, auxiliary in orthographies():
        found = len(required & covered)
        missing = sorted(required - covered)
        reports.append({'iso': iso, 'name': name, 'script': writing, 'found': found, 'total': len(required), 'percent': round(found / len(required) * 100, 1), 'missing': [chr(c) for c in missing], 'aux_found': len(auxiliary & covered), 'aux_total': len(auxiliary), 'full': not missing})
    reports.sort(key=lambda r: (r['iso'] not in NAMES, -r['percent'], r['name']))
    kana = []
    for name, start, end in [('Хирагана: основные знаки', 0x3041, 0x3096), ('Катакана: основные знаки', 0x30A1, 0x30FA)]:
        required = set(range(start, end + 1))
        found = len(required & covered)
        if counts.get('Hira') or counts.get('Kana') or counts.get('Hani'):
            kana.append({'name': name, 'found': found, 'total': len(required), 'percent': round(found / len(required) * 100, 1), 'full': required <= covered, 'missing': [chr(c) for c in sorted(required-covered)]})
    return {'scripts': scripts, 'languages': reports, 'kana': kana, 'han_count': counts.get('Hani', 0), 'source': 'Hyperglot 0.8.1 · primary orthographies · preliminary+', 'shaping_checked': False}
