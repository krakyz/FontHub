"""Font Library listing adapter; does not invent files from CSS or page URLs."""
from .html_catalogue import pages
ID='font-library'
ACTIVE=False
INACTIVE_REASON='Адаптер не завершён: получение каталога и файлов не подтверждено.'
REPOSITORY='https://fontlibrary.org/'
HOSTS={'fontlibrary.org','www.fontlibrary.org'}
CAPABILITIES=['catalogue','search','external_page']

def collect(context):
    return pages(context,'https://fontlibrary.org/en/catalogue',lambda path:'/font/' in path,lambda url:url.hostname=='fontlibrary.org' and url.path.rstrip('/')=='/en/catalogue',ID,'fl-')
