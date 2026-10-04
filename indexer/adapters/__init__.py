"""Discover trusted local adapter modules. Helpers without ID are ignored.
Drop a module into this package and restart the indexer; FontHub discovers it
through /sources. Adapter installation executes Python and is admin-only.
"""
from importlib import import_module
from pkgutil import iter_modules
ADAPTERS={}
for info in iter_modules(__path__):
    module=import_module(__name__+'.'+info.name)
    if hasattr(module,'ID'):
        import re
        if not re.fullmatch('[a-z0-9-]{1,64}',module.ID):raise ValueError('Invalid adapter ID')
        for field in ['REPOSITORY','HOSTS','CAPABILITIES','collect']:
            if not hasattr(module,field):raise ValueError('Adapter '+module.ID+' missing '+field)
        if module.ID in ADAPTERS:raise ValueError('Duplicate adapter ID: '+module.ID)
        ADAPTERS[module.ID]=module
# Keep the established source first for compatibility clients; others by ID.
ADAPTERS=dict(sorted(ADAPTERS.items(),key=lambda item:(item[0]!='google-fonts',item[0])))
