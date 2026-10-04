"""Source activation is persisted and guarded by a usable local index."""
import os,tempfile
from unittest.mock import patch
with tempfile.TemporaryDirectory() as folder:
    os.environ['FONTHUB_DATA']=folder
    import app
    row=dict(id='gf-fixture',source='google-fonts',family='Fixture',author='',files=[],axes=[],variants=[],declared_languages=[],subsets=[])
    data=dict(updated=None,families=[row],sources=[dict(id='google-fonts',snapshot={'count':1}),dict(id='font-library',snapshot=None)],status={})
    with patch.object(app.google_fonts,'index',return_value=data),patch.object(app.google_fonts,'sync',side_effect=AssertionError('Unexpected sync')):
        client=app.app.test_client()
        assert client.post('/api/sources/font-library/enabled',json={'enabled':True}).status_code==409
        assert client.post('/api/sources/google-fonts/enabled',json={'enabled':'true'}).status_code==400
        assert client.get('/api/search?scope=remote').json['total']==1
        assert client.post('/api/sources/google-fonts/enabled',json={'enabled':False}).status_code==200
        assert client.get('/api/search?scope=remote').json['total']==0
        page=client.get('/').data
        assert b'name="source" value="google-fonts"' not in page
        assert b'name="source" value="font-library"' not in page
        with app.db() as con:
            assert con.execute('SELECT enabled FROM source_settings WHERE id=?',('google-fonts',)).fetchone()[0]==0
        assert client.post('/api/sources/google-fonts/enabled',json={'enabled':True}).status_code==200
        assert client.get('/api/search?scope=remote').json['total']==1
        assert b'name="source" value="google-fonts"' in client.get('/').data
        # A failed refresh does not invalidate a previously published snapshot.
        data['sources'][0]['job']={'status':'failed','error':'Fixture failure'}
        assert next(item for item in app.source_states(data) if item['id']=='google-fonts')['can_enable']
        data['error']='Indexer offline'
        assert client.post('/api/sources/google-fonts/enabled',json={'enabled':True}).status_code==409
        assert client.post('/api/sources/google-fonts/enabled',json={'enabled':False}).status_code==200
print('PASS: guarded activation, persisted preferences, disabled results/filter choices, previous snapshot preservation and offline disable')
