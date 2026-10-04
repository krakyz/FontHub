"""Durable original-byte validation and source-specific admission decisions.

Reports are physical (SHA-256); policy/explicit bypass is logical (hash, source).
An override never converts rejection into a pass. No fontTools parser runs here:
only bounded container-header reads precede the isolated native OTS subprocess.
"""
import json,time,struct
from pathlib import Path
import ots
import import_queue,font_validation

VERSION=ots.__version__
MODES={'block','warn','skip'}
LABELS={'unchecked':'Не проверен','queued':'Ожидает','running':'Проверяется','passed':'Пройден','rejected':'Отклонён','error':'Ошибка проверки','unsupported':'Не поддерживается'}

def initialize(con):
 con.executescript('''
 INSERT OR IGNORE INTO queue_control VALUES('ots',0);
 CREATE TABLE IF NOT EXISTS font_checks(hash TEXT PRIMARY KEY,status TEXT NOT NULL,version TEXT,checked REAL,seconds REAL,report TEXT);
 CREATE TABLE IF NOT EXISTS check_history(id INTEGER PRIMARY KEY,hash TEXT,status TEXT,version TEXT,checked REAL,seconds REAL,report TEXT);
 CREATE TABLE IF NOT EXISTS source_policy(source TEXT PRIMARY KEY,mode TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS check_overrides(hash TEXT,source TEXT,created REAL,reason TEXT,PRIMARY KEY(hash,source));
 CREATE TABLE IF NOT EXISTS admission_history(id INTEGER PRIMARY KEY,hash TEXT,source TEXT,created REAL,reason TEXT);
 CREATE INDEX IF NOT EXISTS checks_status ON font_checks(status,version);
 CREATE TABLE IF NOT EXISTS issue_resolutions(job_id INTEGER PRIMARY KEY,created REAL,reason TEXT);
 CREATE INDEX IF NOT EXISTS checks_history_hash ON check_history(hash,id);
 CREATE INDEX IF NOT EXISTS analysis_status ON analysis_state(status);
 CREATE INDEX IF NOT EXISTS work_status ON work_queue(status,kind,identity);
 ''')

def mode(con,source):
 row=con.execute('SELECT mode FROM source_policy WHERE source=?',(source,)).fetchone()
 return row[0] if row else 'block' if source=='local' else 'skip'

def enqueue(con,row,force=False):
 digest=row['hash'];now=time.time()
 current=con.execute('SELECT * FROM font_checks WHERE hash=?',(digest,)).fetchone()
 if current and current['version']==VERSION and current['status'] in {'passed','rejected','unsupported'} and not force:return
 con.execute("INSERT INTO work_queue(kind,identity,input_path,filename,source,created) VALUES('ots',?,?,?,?,?) ON CONFLICT(kind,identity) DO NOTHING",(digest,row['path'],row['name'],row['source'],now))
 job=con.execute("SELECT status FROM work_queue WHERE kind='ots' AND identity=?",(digest,)).fetchone()
 if job[0]=='running':return
 # Error jobs require manual retry unless the engine version changed.
 if current and current['status']=='error' and current['version']==VERSION and not force:return
 con.execute("UPDATE work_queue SET status='queued',phase='waiting',attempts=0,error='',started=NULL,finished=NULL WHERE kind='ots' AND identity=?",(digest,))
 con.execute("DELETE FROM issue_resolutions WHERE job_id IN (SELECT id FROM work_queue WHERE kind='ots' AND identity=?)",(digest,))
 con.execute("INSERT INTO font_checks(hash,status,version) VALUES(?,'queued',?) ON CONFLICT(hash) DO UPDATE SET status='queued',version=excluded.version",(digest,VERSION))

def allowed(con,digest,source):
 policy=mode(con,source)
 if policy in {'skip','warn'}:return True
 if con.execute('SELECT 1 FROM check_overrides WHERE hash=? AND source=?',(digest,source)).fetchone():return True
 row=con.execute('SELECT status,version FROM font_checks WHERE hash=?',(digest,)).fetchone()
 return bool(row and row['status']=='passed' and row['version']==VERSION)

def admit(con,row):
 """Schedule checking and return admission without parsing font tables."""
 if mode(con,row['source'])!='skip':enqueue(con,row)
 return allowed(con,row['hash'],row['source'])

def guard(con,job,digest):
 row=con.execute('SELECT * FROM files WHERE hash=?',(digest,)).fetchone()
 if not row:return True # Let the stage report its missing-file error.
 data=dict(row);data['source']=job['source']
 if admit(con,data):return True
 con.execute("UPDATE work_queue SET status='blocked',phase='gate',error='Обработка ожидает допуска OTS',finished=? WHERE id=?",(time.time(),job['id']))
 return False

def release(con,digest):
 """Resume pending publication and held derivatives only for admitted origins."""
 from archive_pipeline import enqueue as analyze
 row=con.execute('SELECT * FROM files WHERE hash=?',(digest,)).fetchone()
 if not row:return
 state=con.execute('SELECT status FROM analysis_state WHERE hash=?',(digest,)).fetchone()
 for origin in con.execute('SELECT source FROM origins WHERE hash=?',(digest,)).fetchall():
  source=origin[0]
  if not allowed(con,digest,source):continue
  con.execute('DELETE FROM held_origins WHERE hash=? AND source=?',(digest,source))
  if state and state[0] not in {'ready','running','error'}:
   data=dict(row);data['source']=source;analyze(con,data)
   # Physical analysis is shared. An admitted origin may take over a held
   # job originally created for a different, still blocked source.
   con.execute("UPDATE work_queue SET source=?,status='queued',phase='waiting',error='' WHERE kind='analysis' AND identity=? AND status IN ('queued','blocked')",(source,digest))
   con.execute("UPDATE analysis_state SET status='queued' WHERE hash=?",(digest,))
  con.execute("UPDATE work_queue SET status='queued',phase='waiting',error='',started=NULL,finished=NULL WHERE status='blocked' AND source=? AND ((kind='analysis' AND identity=?) OR (kind IN ('preview','convert') AND substr(identity,1,64)=?))",(source,digest,digest))

def inspect(path):
 """Check the original container, then each TTC/OTC face. Bound header counts."""
 deadline=time.monotonic()+180
 if Path(path).stat().st_size>256*1024*1024:return 'unsupported',{'message':'Размер превышает лимит проверки OTS 256 MiB'}
 with Path(path).open('rb') as stream:header=stream.read(12)
 signatures={b'\x00\x01\x00\x00',b'OTTO',b'true',b'typ1',b'wOFF',b'wOF2',b'ttcf'}
 if header[:4] not in signatures:return 'unsupported',{'message':'Неизвестный контейнер шрифта'}
 if header[:4]==b'ttcf':
  if len(header)!=12:return 'rejected',{'message':'Обрезанный заголовок коллекции'}
  count=struct.unpack('>I',header[8:12])[0]
  if not 1<=count<=512:return 'unsupported',{'message':'Число начертаний вне лимита проверки 1–512'}
 def run(index=None):
  try:return font_validation.check(path,index)
  except font_validation.ValidationError as exc:
   message=str(exc);return {'status':'error' if '60 секунд' in message else 'rejected','message':message}
 report=run()
 if header[:4]==b'ttcf':
  faces=[]
  for index in range(count):
   if time.monotonic()>deadline:
    faces.append({'index':index,'status':'error','message':'Превышено общее время проверки коллекции'});break
   value=run(index);faces.append(dict(value,index=index))
   if value['status']=='error':break
  report['faces']=faces
  statuses=[report['status']]+[f['status'] for f in faces]
  return ('error' if 'error' in statuses else 'rejected' if 'rejected' in statuses else 'passed'),report
 return ('passed' if report['status']=='passed' else report['status']),report

def process(db,data):
 from archive_pipeline import sha256,heavy
 with db() as con:job=import_queue.claim(con,'ots')
 if not job:return False
 started=time.monotonic();digest=job['identity']
 with db() as con:con.execute("UPDATE font_checks SET status='running' WHERE hash=?",(digest,))
 try:
  if sha256(job['input_path'])!=digest:raise ValueError('Контрольная сумма оригинала не совпала')
  with heavy(data,Path(job['input_path'])):status,report=inspect(job['input_path'])
 except Exception as exc:status,report='error',{'message':str(exc)[:8192]}
 checked=time.time();seconds=time.monotonic()-started;encoded=json.dumps(report,ensure_ascii=False)
 with db() as con:
  con.execute('INSERT INTO check_history(hash,status,version,checked,seconds,report) VALUES(?,?,?,?,?,?)',(digest,status,VERSION,checked,seconds,encoded))
  con.execute('UPDATE font_checks SET status=?,version=?,checked=?,seconds=?,report=? WHERE hash=?',(status,VERSION,checked,seconds,encoded,digest))
  import_queue.finish(con,job['id'],'done' if status=='passed' else 'error',report.get('message','') if status!='passed' else '',result=digest)
  release(con,digest)
  if status=='rejected':
   from font_repairs import schedule_auto
   schedule_auto(con,digest)
 return True

def seed(con,limit=25):
 """Gradual backfill; never requeue terminal same-version checks automatically."""
 rows=con.execute("SELECT f.* FROM files f LEFT JOIN font_checks c ON c.hash=f.hash WHERE c.hash IS NULL OR c.version<>? LIMIT ?",(VERSION,limit)).fetchall()
 for row in rows:enqueue(con,row)
 return len(rows)

def release_batch(con,limit=50):
 # Policy changes may affect a large archive. Resume in bounded transactions,
 # avoiding a long write lock in the source-settings HTTP request.
 rows=con.execute("""WITH pending(hash) AS (
 SELECT hash FROM analysis_state WHERE status='blocked'
 UNION SELECT substr(identity,1,64) FROM work_queue WHERE status='blocked'
 UNION SELECT hash FROM held_origins)
 SELECT p.hash FROM pending p WHERE EXISTS(SELECT 1 FROM origins o LEFT JOIN source_policy s ON s.source=o.source
 WHERE o.hash=p.hash AND (COALESCE(s.mode,CASE WHEN o.source='local' THEN 'block' ELSE 'skip' END)<>'block'
 OR EXISTS(SELECT 1 FROM check_overrides a WHERE a.hash=p.hash AND a.source=o.source)
 OR EXISTS(SELECT 1 FROM font_checks c WHERE c.hash=p.hash AND c.status='passed' AND c.version=?))) LIMIT ?""",(VERSION,limit)).fetchall()
 for row in rows:release(con,row[0])
