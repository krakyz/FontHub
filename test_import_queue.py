"""Exercise real font bytes in a disposable archive; no upstream requests.

Verify that parsing never compresses, pause is durable, a running import commits,
preview failures preserve catalogue/originals, retries recover, claims are unique
and restored queue paths follow the new installation root.
"""
import hashlib,json,os,shutil,tempfile,time,sqlite3
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from contextlib import closing

with tempfile.TemporaryDirectory() as folder:
    os.environ['FONTHUB_DATA']=str(Path(folder)/'archive')
    import app,import_queue,original_checks
    client=app.app.test_client();font=Path('C:/Windows/Fonts/arial.ttf');bold=Path('C:/Windows/Fonts/arialbd.ttf')
    def incoming(name,source=font):
        path=app.INBOX/name;shutil.copyfile(source,path);os.utime(path,(time.time()-10,time.time()-10));return path
    first=incoming('regular.ttf');second=incoming('bold.ttf',bold);app.discover_inbox()
    assert client.get('/imports').status_code==200
    assert client.post('/api/imports/control',json={'kind':'import','paused':True}).status_code==200
    assert not app.process_import() and client.get('/api/imports').json['counts']['import']['queued']==2
    with app.db() as con: import_queue.reset_running(con,'import')
    assert client.get('/api/imports').json['controls']['import'] is True
    client.post('/api/imports/control',json={'kind':'import','paused':False})
    original=app.copy_original
    def pause_during_file(*args):
        client.post('/api/imports/control',json={'kind':'import','paused':True})
        return original(*args)
    with patch.object(app,'copy_original',side_effect=pause_during_file),patch.object(app.TTFont,'save',side_effect=AssertionError('Import compressed a preview')):
        assert app.process_import()
    assert not app.process_import()
    assert not client.get('/api/catalog').json['faces']
    assert original_checks.process(app.db,app.DATA)
    assert app.process_analysis()
    faces=client.get('/api/catalog').json['faces'];assert len(faces)==1 and not faces[0]['preview_available']
    assert client.get('/api/imports').json['counts']['preview']['queued']==1
    assert client.get('/download/'+faces[0]['id']).data==(bold if faces[0]['filename']=='bold.ttf' else font).read_bytes()
    client.post('/api/imports/control',json={'kind':'import','paused':False})
    assert app.process_import();assert original_checks.process(app.db,app.DATA);assert app.process_analysis();assert len(client.get('/api/catalog').json['faces'])==2
    client.post('/api/imports/control',json={'kind':'preview','paused':True});assert not app.process_preview()
    client.post('/api/imports/control',json={'kind':'preview','paused':False})
    with patch.object(app.TTFont,'save',side_effect=RuntimeError('Simulated compression failure')): assert app.process_preview()
    failed=client.get('/api/imports?status=error').json['jobs'][0]
    assert failed['kind']=='preview' and len(client.get('/api/catalog').json['faces'])==2
    assert client.post('/api/imports/'+str(failed['id'])+'/retry',json={}).status_code==200
    while app.process_preview(): pass
    assert all(face['preview_available'] for face in client.get('/api/catalog').json['faces'])
    assert client.post('/api/imports/'+str(failed['id'])+'/retry',json={}).status_code==409

    # A parsing failure retains the accepted bytes and exposes explicit retry.
    path=incoming('broken.ttf');path.write_bytes(b'broken');os.utime(path,(time.time()-10,time.time()-10))
    app.scan();failed=client.get('/api/imports?kind=ots&status=error').json['jobs'][0]
    assert Path(failed['input_path']).parent==app.DATA/'originals'
    assert client.get('/originals/'+failed['identity']).data==b'broken'
    assert client.post('/api/imports/'+str(failed['id'])+'/retry',json={}).status_code==200
    assert original_checks.process(app.db,app.DATA)
    assert Path(failed['input_path']).read_bytes()==b'broken'

    incoming('duplicate-one.ttf');incoming('duplicate-two.ttf');app.discover_inbox()
    def claim():
        with app.db() as con: return import_queue.claim(con,'import')
    with ThreadPoolExecutor(max_workers=2) as pool: claims=list(pool.map(lambda _:claim(),range(2)))
    assert claims[0]['id']!=claims[1]['id']
    with app.db() as con: import_queue.reset_running(con,'import')
    assert client.get('/api/imports?status=queued').json['counts']['import']['running']==0

    # Explicit source download stages immediately runnable queue jobs, even paused.
    client.post('/api/imports/control',json={'kind':'import','paused':True})
    with patch.object(app.google_fonts,'family',return_value={'id':'fixture','source':'google-fonts','revision':'a'*40}),patch.object(app.google_fonts,'resolve',return_value={'artifacts':[{'name':'arial.ttf'}]}),patch.object(app.google_fonts,'font_paths',return_value=[font]):
        response=client.post('/api/sources/google-fonts/import/fixture')
    assert response.status_code==202 and response.json['queued'] and response.json['url']=='/imports'
    assert not app.process_import()

    from archive_backup import backup,restore
    archive=Path(folder)/'queue.zip';restored=Path(folder)/'restored';backup(app.DATA,archive);restore(archive,restored)
    with closing(sqlite3.connect(restored/'catalog.sqlite')) as con:
        assert con.execute("SELECT paused FROM queue_control WHERE kind='import'").fetchone()[0]==1
        for kind,identity,path in con.execute("SELECT kind,identity,input_path FROM work_queue WHERE status='queued'"):
            assert Path(path).is_relative_to(restored)
            if kind=='import': assert identity==path
    print('PASS: independent stages, durable pause, in-flight commit, failure/retry, quarantine, concurrent claims, source staging, portable restore')
