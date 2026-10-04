"""Receipt/analysis publication boundaries, OTS isolation and shared artifacts."""

# Direct execution from the repository root keeps application imports available.
import sys as _test_sys
from pathlib import Path as _TestPath
_test_sys.path.insert(0,str(_TestPath(__file__).resolve().parents[1]))
import os,tempfile,shutil,json
from pathlib import Path
from unittest.mock import patch
from fontTools.ttLib import TTFont,TTCollection
with tempfile.TemporaryDirectory() as folder:
 os.environ['FONTHUB_DATA']=folder
 import app,import_queue,font_validation,original_checks
 client=app.app.test_client()
 path=app.INBOX/'regular.ttf';shutil.copyfile('C:/Windows/Fonts/arial.ttf',path)
 with patch.object(app,'metadata',side_effect=AssertionError('receipt must not parse')):
  digest=app.pipeline.accept(path)
 assert not path.exists() and client.get('/originals/'+digest).data==Path('C:/Windows/Fonts/arial.ttf').read_bytes()
 assert not client.get('/api/catalog').json['faces']
 assert original_checks.process(app.db,app.DATA)
 assert app.process_analysis()
 face=client.get('/api/catalog').json['faces'][0];assert face['languages'] and face['chars']
 with app.db() as con: assert con.execute('SELECT COUNT(*) FROM coverage_reports').fetchone()[0]==1
 # OTS rejects only the browser artifact. Original and search remain usable.
 with patch.object(font_validation,'check',side_effect=font_validation.ValidationError('fixture rejection')): assert app.process_preview()
 job=client.get('/api/imports?kind=preview&status=error').json['jobs'][0]
 assert len(client.get('/api/catalog').json['faces'])==1
 assert client.get('/originals/'+digest).status_code==200
 assert client.post('/api/imports/'+str(job['id'])+'/retry').status_code==200
 assert app.process_preview()
 with app.db() as con:
  metadata=json.loads(con.execute('SELECT metadata FROM faces').fetchone()[0])
  assert metadata['browser_validation']['status']=='passed'
  assert metadata['browser_validation']['version']=='9.2.0'
 # All-or-nothing TTC publication, including failure on the second face.
 collection=TTCollection();collection.fonts=[TTFont('C:/Windows/Fonts/arial.ttf'),TTFont('C:/Windows/Fonts/arialbd.ttf')]
 path=app.INBOX/'family.ttc';collection.save(path);collection.close()
 digest=app.pipeline.accept(path);assert original_checks.process(app.db,app.DATA);original=app.metadata;calls=[]
 def fail_second(*args):
  calls.append(1)
  if len(calls)==2:raise ValueError('second face failure')
  return original(*args)
 with patch.object(app,'metadata',side_effect=fail_second):assert app.process_analysis()
 with app.db() as con: assert con.execute('SELECT COUNT(*) FROM faces WHERE hash=?',(digest,)).fetchone()[0]==0
 job=client.get('/api/imports?kind=analysis&status=error').json['jobs'][0]
 assert client.post('/api/imports/'+str(job['id'])+'/retry').status_code==200
 assert app.process_analysis()
 with app.db() as con: assert con.execute('SELECT COUNT(*) FROM faces WHERE hash=?',(digest,)).fetchone()[0]==2
 selected=digest+'-1~local'
 assert client.post('/api/fonts/'+selected+'/exports/woff2').status_code==202
 with app.db() as con:assert not con.execute("SELECT 1 FROM work_queue WHERE kind='convert'").fetchone()
 # Crash loop protection is durable and scoped to the recovering queue kind.
 with app.db() as con:
  con.execute("UPDATE work_queue SET status='running',attempts=3 WHERE kind='preview'")
  import_queue.reset_running(con,'preview')
  assert not con.execute("SELECT 1 FROM work_queue WHERE kind='preview' AND status='queued'").fetchone()
 print('PASS: parse-free receipt, original download, complete publication, OTS isolation, real OTS, atomic TTC retry, shared WOFF2, bounded crash recovery')
