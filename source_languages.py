"""Source assertions, deliberately separate from measured glyph coverage.

Google uses ISO 639-1/639-3 + ISO 15924 (ru_Cyrl, eng_Latn).
Normalize identifiers, retaining the original code and script. A subset is
only a script hint; the language filter accepts it as an approximate match,
without promoting it to declared support or measured coverage.
"""
import pycountry
from functools import lru_cache
from language_coverage import orthographies

SUBSET_SCRIPTS={'latin':'Latn','latin-ext':'Latn','cyrillic':'Cyrl','cyrillic-ext':'Cyrl','greek':'Grek','greek-ext':'Grek','arabic':'Arab','hebrew':'Hebr','devanagari':'Deva'}

@lru_cache(maxsize=1)
def language_names():
    return {row[0]:row[1] for row in orthographies()}

@lru_cache(maxsize=256)
def language_scripts(name):
    """Translate Hyperglot's primary writing-system names to ISO 15924."""
    scripts=set()
    for _,language,writing,_,_ in orthographies():
        if language==name:
            item=pycountry.scripts.get(name=writing)
            if item: scripts.add(item.alpha_4)
    return scripts

def matches_source_language(info,name):
    if any(row['name']==name for row in info['languages']): return True
    return bool(language_scripts(name)&{SUBSET_SCRIPTS.get(value) for value in info['subsets']})

def source_languages(record):
    names=language_names()
    languages=[]
    for code in record.get('declared_languages',[]):
        parts=code.split('_')
        language=pycountry.languages.get(**({'alpha_2':parts[0]} if len(parts[0])==2 else {'alpha_3':parts[0]}))
        iso=language.alpha_3 if language else parts[0]
        languages.append(dict(code=code,iso=iso,script=parts[1] if len(parts)>1 else '',name=names.get(iso,language.name if language else code)))
    subsets=[value for value in record.get('subsets',[]) if value!='menu']
    status={}
    for iso,script in [('rus','Cyrl'),('eng','Latn')]:
        declared=any(row['iso']==iso and row['script'] in {'',script} for row in languages)
        hint=any(SUBSET_SCRIPTS.get(value)==script for value in subsets)
        status[iso]='declared' if declared else 'subset' if hint else 'unknown'
    return dict(languages=languages,subsets=subsets,status=status)
