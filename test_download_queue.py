"""Offline queue invariants with real font bytes, no upstream traffic."""
import os,json,shutil,tempfile,time
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
with tempfile.TemporaryDirectory() as folder:
 os.environ['FONTHUB_DATA']=folder
 import app,font_downloads,import_queue
 client=app.app.test_client();row={'id':'fixture','source':'google-fonts','revision':'a'*40};artifact={'name':'arial.ttf'}
 calls=[]
 def binary(folder,record,name,progress=None):
  calls.append(name);path=folder/record['id']/record['revision']/name;path.parent.mkdir(parents=True,exist_ok=True)
  if progress:progress(100,200)
  shutil.copyfile('C:/Windows/Fonts/arial.ttf',path)
  if progress:progress(path.stat().st_size,path.stat().st_size)
  return path
 def run():return font_downloads.process(app.db,app.DATA,app.GOOGLE_DATA,app.google_fonts)
 with patch.object(app.google_fonts,'family',return_value=row),patch.object(app.google_fonts,'resolve',return_value={'artifacts':[artifact]}),patch.object(app.google_fonts,'binary',side_effect=binary),patch.object(app.google_fonts,'font_paths',side_effect=lambda folder,record,names=None:[folder/record['id']/record['revision']/names[0]]):
  url='/sources/google-fonts/fonts/fixture/download/0?revision='+row['revision']
  wait=client.get(url);assert b'downloadJob=' in wait.data and not calls
  with app.db() as con:job=dict(con.execute("SELECT * FROM work_queue WHERE kind='download'").fetchone())
  client.post('/api/imports/control',json={'kind':'download','paused':True});assert not run()
  response=client.post('/api/sources/google-fonts/import/fixture');assert response.status_code==202 and response.json['jobs']==[job['id']] and not calls
  client.post('/api/imports/control',json={'kind':'import','paused':True})
  client.post('/api/imports/control',json={'kind':'download','paused':False});assert run() and len(calls)==1
  assert not app.process_import()
  status=client.get('/api/downloads/'+str(job['id'])).json;assert status['url']
  download=client.get(status['url']);assert download.data==Path('C:/Windows/Fonts/arial.ttf').read_bytes();download.close()
  ready=client.get(url);assert 'attachment' in ready.headers['Content-Disposition'];ready.close();assert len(calls)==1
  client.post('/api/imports/control',json={'kind':'import','paused':False});assert app.process_import();assert app.process_analysis()
  faces=client.get('/api/catalog').json['faces'];assert faces[0]['source']=='google-fonts'
  client.post('/api/sources/google-fonts/import/fixture');assert not run()
  # Cancel queued jobs without network; a new explicit request may requeue.
  with app.db() as con:id=font_downloads.enqueue(con,app.GOOGLE_DATA,row,{'name':'cancel.ttf'},want_import=True)
  assert client.post('/api/downloads/'+str(id)+'/cancel').status_code==200
  assert client.get('/api/downloads/'+str(id)).json['status']=='cancelled'
  with app.db() as con:assert con.execute('SELECT want_import FROM download_tasks WHERE job_id=?',(id,)).fetchone()[0]==0
  assert not run()
  with app.db() as con:assert font_downloads.enqueue(con,app.GOOGLE_DATA,row,{'name':'cancel.ttf'})==id
  def cancel_running(folder,record,name,progress=None):
   client.post('/api/downloads/'+str(id)+'/cancel');progress(1,10);raise AssertionError('must stop')
  with patch.object(app.google_fonts,'binary',side_effect=cancel_running):assert run()
  assert client.get('/api/downloads/'+str(id)).json['status']=='cancelled'
  # Retry a failed job despite no input file: downloads create that input.
  with app.db() as con:failed=font_downloads.enqueue(con,app.GOOGLE_DATA,row,{'name':'error.ttf'})
  with patch.object(app.google_fonts,'binary',side_effect=OSError('network failed')):assert run()
  assert client.post('/api/imports/'+str(failed)+'/retry').status_code==200
  assert run() and client.get('/api/downloads/'+str(failed)).json['status']=='done'
  # Source cooldown is durable, and bounded automatic retries avoid storms.
  with app.db() as con:limited=font_downloads.enqueue(con,app.GOOGLE_DATA,row,{'name':'limited.ttf'})
  with patch.object(app.google_fonts,'binary',side_effect=HTTPError('https://example.invalid',429,'limited',{'Retry-After':'120'},None)):
   assert run()
   assert not run()
   for _ in range(2):
    with app.db() as con:
     con.execute('UPDATE download_cooldowns SET until=0');con.execute("UPDATE work_queue SET available_at=0 WHERE id=?",(limited,))
    assert run()
  state=client.get('/api/downloads/'+str(limited)).json;assert state['status']=='error' and state['attempts']==3
  # Claim recovery retains the request and import intent after a process restart.
  with app.db() as con:
   con.execute("UPDATE work_queue SET status='running' WHERE id=?",(limited,));import_queue.reset_running(con,'download')
   assert con.execute('SELECT COUNT(*) FROM download_tasks WHERE job_id=?',(limited,)).fetchone()[0]==1
 # Portable backups preserve the pinned record and import intent.
 import archive_backup,sqlite3
 from contextlib import closing
 with tempfile.TemporaryDirectory() as backup_folder:
  zipped=Path(backup_folder)/'downloads.zip';restored=Path(backup_folder)/'restored'
  archive_backup.backup(app.DATA,zipped);archive_backup.restore(zipped,restored)
  with closing(sqlite3.connect(restored/'catalog.sqlite')) as con:
   assert con.execute('SELECT COUNT(*) FROM download_tasks').fetchone()[0]>=4
   assert all(str(restored) in r[0] for r in con.execute("SELECT input_path FROM work_queue WHERE kind='download'"))
   saved=json.loads(con.execute('SELECT record FROM download_tasks LIMIT 1').fetchone()[0]);assert saved['revision']==row['revision']
 # The real transport reports bounded chunks and cancellation never publishes.
 import io,indexer_client
 raw=Path('C:/Windows/Fonts/arial.ttf').read_bytes()
 class Response(io.BytesIO):
  url='https://raw.githubusercontent.com/google/fonts/pinned/arial.ttf'
  headers={'Content-Length':str(len(raw))}
 transport_manifest={'artifacts':[{'name':'stream.ttf','url':Response.url}], 'revision':row['revision'],'family':{'download_policy':{'raw.githubusercontent.com':'/google/fonts/'}}}
 progress=[]
 with patch.object(indexer_client,'resolve',return_value=transport_manifest),patch.object(indexer_client,'urlopen',side_effect=lambda *a,**k:Response(raw)):
  destination=indexer_client.binary(app.GOOGLE_DATA,row,'stream.ttf',progress=lambda received,total:progress.append((received,total)))
  assert destination.read_bytes()==raw and len(progress)>2 and progress[0][0]==65536
  destination.unlink()
  def stop(received,total):raise font_downloads.Cancelled()
  try:indexer_client.binary(app.GOOGLE_DATA,row,'stream.ttf',progress=stop)
  except font_downloads.Cancelled:pass
  else:raise AssertionError('Cancellation ignored')
  assert not destination.exists() and not list(destination.parent.glob('stream.ttf.*.tmp'))
 print('PASS: no inline downloads, shared intents, import handoff, cache, pause, cancellation, retry, cooldown and recovery')
