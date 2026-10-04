"""Font Squirrel listing adapter. Access challenges fail, never bypassed.
The legacy JSON endpoint is not assumed operational; listing URLs are offers
for manual acquisition, not asserted binary manifests or licenses.
"""
from .html_catalogue import pages
ID='font-squirrel'
ACTIVE=False
INACTIVE_REASON='Адаптер не завершён: получение каталога и файлов не подтверждено.'
REPOSITORY='https://www.fontsquirrel.com/'
HOSTS={'www.fontsquirrel.com','fontsquirrel.com'}
CAPABILITIES=['catalogue','search','external_page']

def collect(context):
    return pages(context,'https://www.fontsquirrel.com/fonts/list/find_fonts',lambda path:path.startswith('/fonts/') and len(path.strip('/').split('/'))==2 and path.split('/')[-1] not in {'list','download'},lambda url:url.hostname in HOSTS and url.path.startswith('/fonts/list/find_fonts'),ID,'fs-')
