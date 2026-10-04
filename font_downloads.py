"""Durable source downloads shared by user attachments and archive imports.

Jobs pin the adapter record/revision and artifact, never a user-provided URL.
One network worker limits concurrency to one, including per-source concurrency.
User cancellation is cooperative; rate-limit cooldowns survive process restarts.
"""
import hashlib,json,time,shutil
from pathlib import Path
from urllib.error import HTTPError
from email.utils import parsedate_to_datetime
from flask import jsonify,abort,send_file,render_template,request
from archive_io import publish,archive_lock
import import_queue

class Cancelled(Exception): pass

# Returned to the UI as a byte detail; work_queue remains the common lifecycle.
# A missing input is expected for downloads, unlike parsing/conversion jobs.

def initialize(con):
 con.executescript("""
 INSERT OR IGNORE INTO queue_control VALUES('download',0);
 CREATE TABLE IF NOT EXISTS download_tasks(job_id INTEGER PRIMARY KEY,record TEXT NOT NULL,artifact TEXT NOT NULL,want_import INTEGER DEFAULT 0,imported INTEGER DEFAULT 0,cancel_requested INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS download_cooldowns(source TEXT PRIMARY KEY,until REAL NOT NULL);
 """)

def enqueue(con,folder,row,artifact,want_import=False):
 if Path(artifact['name']).name!=artifact['name'] or '/' in artifact['name'] or '\\' in artifact['name']:raise ValueError('Invalid artifact filename')
 identity=hashlib.sha256(json.dumps([row['source'],row['id'],row['revision'],artifact['name']]).encode()).hexdigest()
 path=folder/row['id']/row['revision']/artifact['name']
 con.execute("INSERT INTO work_queue(kind,identity,input_path,filename,source,created) VALUES('download',?,?,?,?,?) ON CONFLICT(kind,identity) DO NOTHING",(identity,str(path),artifact['name'],row['source'],time.time()))
 job=con.execute("SELECT * FROM work_queue WHERE kind='download' AND identity=?",(identity,)).fetchone()
 con.execute('INSERT OR IGNORE INTO download_tasks(job_id,record,artifact) VALUES(?,?,?)',(job['id'],json.dumps(row),json.dumps(artifact)))
 task=con.execute('SELECT * FROM download_tasks WHERE job_id=?',(job['id'],)).fetchone()
 if path.is_file() and not want_import and job['status']=='queued' and (not task['want_import'] or task['imported']):
  import_queue.finish(con,job['id'])
 if want_import: con.execute('UPDATE download_tasks SET want_import=1 WHERE job_id=?',(job['id'],))
 # Completed bytes can acquire an import intent later; the worker reuses cache.
 if job['status'] in ('cancelled','done') and (not path.is_file() or job['status']=='cancelled' or want_import and not task['imported']):
  con.execute("UPDATE work_queue SET status='queued',phase='waiting',error='',started=NULL,finished=NULL,available_at=0 WHERE id=?",(job['id'],))
  con.execute('UPDATE download_tasks SET cancel_requested=0 WHERE job_id=?',(job['id'],))
 return job['id']

def process(db,data,folder,client):
 # Cooldown scheduling is per source; another source may proceed immediately.
 with db() as con:
  con.execute("UPDATE work_queue SET available_at=MAX(available_at,COALESCE((SELECT until FROM download_cooldowns WHERE source=work_queue.source),0)) WHERE kind='download' AND status='queued'")
  for waiting in con.execute("SELECT id,input_path FROM work_queue WHERE kind='download' AND status='queued' AND available_at>?",(time.time(),)).fetchall():
   if Path(waiting['input_path']).is_file():con.execute('UPDATE work_queue SET available_at=0 WHERE id=?',(waiting['id'],))
 with db() as con: job=import_queue.claim(con,'download')
 if not job:return False
 with db() as con: task=dict(con.execute('SELECT * FROM download_tasks WHERE job_id=?',(job['id'],)).fetchone())
 row=json.loads(task['record']);artifact=json.loads(task['artifact']);last=0
 def progress(received=0,total=0):
  nonlocal last
  with db() as con:
   if con.execute('SELECT cancel_requested FROM download_tasks WHERE job_id=?',(job['id'],)).fetchone()[0]:raise Cancelled()
   if time.monotonic()-last>.25 or received==total:
    import_queue.progress(con,job['id'],'downloading',f'{received} / {total} байт' if total else f'{received} байт');last=time.monotonic()
 try:
  progress();path=client.binary(folder,row,artifact['name'],progress=progress);progress(path.stat().st_size,path.stat().st_size)
  # Re-read intent under the writer lock. A request arriving during download
  # either joins this completion or requeues a cache-only import handoff.
  with archive_lock(data/'import.lock'),db() as con:
   con.execute('BEGIN IMMEDIATE')
   current=con.execute('SELECT * FROM download_tasks WHERE job_id=?',(job['id'],)).fetchone()
   if current['cancel_requested']:raise Cancelled()
   if current['want_import'] and not current['imported']:
    paths=client.font_paths(folder,row,names=[artifact['name']])
    staging=data/'sources'/'staging';staging.mkdir(parents=True,exist_ok=True)
    for file in paths:
     target=staging/(job['identity']+'-'+file.name);partial=target.with_suffix('.part');shutil.copyfile(file,partial);publish(partial,target)
     import_queue.enqueue_import(con,target,row['source'],filename=file.name)
    con.execute('UPDATE download_tasks SET imported=1 WHERE job_id=?',(job['id'],))
   import_queue.finish(con,job['id'])
 except Cancelled:
  with db() as con: import_queue.finish(con,job['id'],'cancelled')
 except HTTPError as exc:
  if exc.code in (429,503) and job['attempts']<2:
   value=exc.headers.get('Retry-After','60')
   try: delay=max(1,min(86400,float(value)))
   except ValueError:
    try:delay=max(1,min(86400,parsedate_to_datetime(value).timestamp()-time.time()))
    except Exception:delay=60
   until=time.time()+delay
   with db() as con:
    con.execute('INSERT INTO download_cooldowns VALUES(?,?) ON CONFLICT(source) DO UPDATE SET until=MAX(until,excluded.until)',(job['source'],until))
    con.execute("UPDATE work_queue SET status='queued',phase='waiting',available_at=?,detail=?,error=? WHERE id=?",(until,'Ожидание лимита источника',str(exc)[:300],job['id']))
  else:
   with db() as con: import_queue.finish(con,job['id'],'error',str(exc)[:1000])
 except Exception as exc:
  with db() as con: import_queue.finish(con,job['id'],'error',str(exc)[:1000])
 return True

def register(app,db,data,folder,client):
 @app.get('/api/downloads/<int:id>')
 def status(id):
  with db() as con: job=con.execute("SELECT * FROM work_queue WHERE id=? AND kind='download'",(id,)).fetchone()
  if not job:abort(404)
  result=dict(job);result.pop('input_path',None)
  result['url']='/downloads/'+str(id)+'/file' if Path(job['input_path']).is_file() else None
  return jsonify(result)
 @app.get('/downloads/<int:id>/file')
 def attachment(id):
  with db() as con: job=con.execute("SELECT * FROM work_queue WHERE id=? AND kind='download'",(id,)).fetchone()
  if not job or not Path(job['input_path']).is_file():abort(404)
  return send_file(job['input_path'],as_attachment=True,download_name=job['filename'])
 @app.post('/api/downloads/<int:id>/cancel')
 def cancel(id):
  with db() as con:
   con.execute('BEGIN IMMEDIATE')
   job=con.execute("SELECT * FROM work_queue WHERE id=? AND kind='download'",(id,)).fetchone()
   if not job:abort(404)
   if job['status'] not in ('queued','running'):return jsonify(error='Загрузка уже завершена'),409
   con.execute('UPDATE download_tasks SET cancel_requested=1,want_import=0 WHERE job_id=?',(id,))
   if job['status']=='queued':import_queue.finish(con,id,'cancelled')
  return jsonify(message='Загрузка отменена; текущий сетевой запрос остановится при получении следующего блока.')
 @app.get('/sources/<source_id>/fonts/<family_id>/download/<int:index>')
 def source_attachment(source_id,family_id,index):
  row=client.family(folder,family_id)
  if row is None or row['source']!=source_id:abort(404)
  if request.args.get('revision')!=row['revision']:return jsonify(error='Индекс обновился. Обновите страницу шрифта.'),409
  try: artifacts=client.resolve(folder,row)['artifacts']
  except Exception as exc:return jsonify(error=str(exc)[:200]),502
  if index>=len(artifacts):abort(404)
  with db() as con:
   id=enqueue(con,folder,row,artifacts[index]);job=con.execute('SELECT * FROM work_queue WHERE id=?',(id,)).fetchone()
  if Path(job['input_path']).is_file():return send_file(job['input_path'],as_attachment=True,download_name=job['filename'])
  return render_template('download_wait.html',id=id,filename=job['filename'])
