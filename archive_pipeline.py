"""Two durable boundaries: accepted original, then completely analyzed catalogue.

Receipt performs no font parsing. Analysis works outside the publication lock,
then commits all faces/search projections and derivative jobs atomically. An
analysis failure cannot delete a previously accepted original. Existing source
instances stay separate; physical storage and analysis remain shared by SHA-256.
"""
import hashlib,json,time
from pathlib import Path
from contextlib import nullcontext
from fontTools.ttLib import TTFont,TTCollection,TTLibError
from archive_io import archive_lock
import import_queue
import original_checks
VERSION=1

def sha256(path):
 with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def initialize(con):
 con.executescript("""
 INSERT OR IGNORE INTO queue_control VALUES('analysis',0);
 CREATE INDEX IF NOT EXISTS faces_hash ON faces(hash);
 CREATE TABLE IF NOT EXISTS analysis_state(hash TEXT PRIMARY KEY,status TEXT NOT NULL,version INTEGER,error TEXT NOT NULL DEFAULT '');
 """)
 # One migration, never a full archive scan on every worker startup.
 if not con.execute("SELECT 1 FROM queue_migrations WHERE name='pipeline-v1'").fetchone():
  con.execute("INSERT OR IGNORE INTO analysis_state SELECT hash,CASE WHEN EXISTS(SELECT 1 FROM faces WHERE faces.hash=files.hash) THEN 'ready' ELSE 'queued' END,?,'' FROM files",(VERSION,))
  for row in con.execute("SELECT files.* FROM files JOIN analysis_state ON analysis_state.hash=files.hash WHERE analysis_state.status='queued'").fetchall():enqueue(con,row)
  con.execute("INSERT INTO queue_migrations VALUES('pipeline-v1')")

def enqueue(con,row,force=False):
 con.execute("INSERT INTO work_queue(kind,identity,input_path,filename,source,created) VALUES('analysis',?,?,?,?,?) ON CONFLICT(kind,identity) DO NOTHING",(row['hash'],row['path'],row['name'],row['source'],time.time()))
 if force:
  con.execute("UPDATE work_queue SET status='queued',phase='waiting',error='',started=NULL,finished=NULL WHERE kind='analysis' AND identity=? AND status!='running'",(row['hash'],))

def heavy(data,path):
 # On large TTC/CJK fonts, serialize expensive stages across analysis/previews.
 # Small jobs retain parallelism; this is a memory admission limit, not a sandbox.
 return archive_lock(data/'heavy-font.lock') if Path(path).is_file() and Path(path).stat().st_size>=16*1024*1024 else nullcontext()

class Pipeline:
 def __init__(self,db,data,inbox,metadata,event,copy_original,sources,discover):
  self.db,self.data,self.inbox=db,data,inbox
  self.metadata,self.event,self.copy=metadata,event,copy_original
  self.sources,self.discover=sources,discover
 def accept(self,source,source_id=None,filename=None,progress=None):
  source=Path(source);progress=progress or (lambda *a:None);before=source.stat()
  if source_id is None:
   parts=source.relative_to(self.inbox).parts;source_id=parts[0] if len(parts)>1 and parts[0] in self.sources else 'local'
  if source_id not in self.sources:self.discover()
  if source_id not in self.sources:raise ValueError('Неизвестный источник')
  filename=filename or source.name;progress('hashing');digest=sha256(source)
  # Hashing runs without the global publication lock. Check copying stability
  # before journaling so a changing inbox file is deferred, not archived as final.
  if (source.stat().st_size,source.stat().st_mtime_ns)!=(before.st_size,before.st_mtime_ns):raise ValueError('Файл изменяется')
  with archive_lock(self.data/'import.lock'):
   with self.db() as con:
    exists=con.execute('SELECT * FROM files WHERE hash=?',(digest,)).fetchone()
    target=Path(exists['path']) if exists else self.data/'originals'/(digest+source.suffix.lower())
    con.execute('INSERT OR REPLACE INTO import_jobs VALUES(?,?,?,?,?,?)',(digest,source_id,str(source.resolve()),filename,str(target),'copying'))
   progress('copying')
   if not target.is_file() or sha256(target)!=digest:self.copy(source,target,digest)
   with self.db() as con:
    con.execute("INSERT OR IGNORE INTO files(hash,name,path,added_at,source) VALUES(?,?,?,datetime('now'),?)",(digest,filename,str(target),source_id))
    prior_origin=con.execute('SELECT 1 FROM origins WHERE hash=? AND source=?',(digest,source_id)).fetchone()
    con.execute("INSERT OR IGNORE INTO origins VALUES(?,?,datetime('now'))",(digest,source_id))
    con.execute("INSERT OR IGNORE INTO analysis_state VALUES(?,'queued',?, '')",(digest,VERSION))
    state=con.execute('SELECT status FROM analysis_state WHERE hash=?',(digest,)).fetchone()[0]
    admitted=original_checks.admit(con,dict(hash=digest,path=str(target),name=filename,source=source_id))
    if not prior_origin and not admitted:con.execute('INSERT OR IGNORE INTO held_origins VALUES(?,?)',(digest,source_id))
    if not admitted:
     from font_repairs import schedule_auto
     schedule_auto(con,digest)
    if state!='ready' and admitted:enqueue(con,dict(hash=digest,path=str(target),name=filename,source=source_id))
    if state!='ready' and not admitted:con.execute("UPDATE analysis_state SET status='blocked' WHERE hash=?",(digest,))
    con.execute("UPDATE import_jobs SET phase='accepted' WHERE hash=? AND source=?",(digest,source_id))
   progress('cleanup')
   if source.resolve()!=target.resolve() and source.exists() and (source.stat().st_size,source.stat().st_mtime_ns)==(before.st_size,before.st_mtime_ns):source.unlink()
   with self.db() as con:con.execute('DELETE FROM import_jobs WHERE hash=? AND source=?',(digest,source_id))
  self.event(filename,'duplicate' if exists else 'accepted','Оригинал сохранён; '+('анализ готов' if state=='ready' else 'ожидает анализа'))
  return digest
 def analyze(self,job):
  path=Path(job['input_path']);digest=job['identity'];fonts=[]
  def progress(phase,detail=''):
   with self.db() as con:import_queue.progress(con,job['id'],phase,detail)
  try:
   with self.db() as con:
    if not original_checks.guard(con,job,digest):return
   with self.db() as con:con.execute("UPDATE analysis_state SET status='running',error='' WHERE hash=?",(digest,))
   with heavy(self.data,path):
    progress('analysis');
    if sha256(path)!=digest:raise ValueError('Контрольная сумма оригинала не совпала')
    fonts=TTCollection(path,lazy=True).fonts if path.suffix.lower() in {'.ttc','.otc'} else [TTFont(path,lazy=True)]
    faces=[]
    for index,font in enumerate(fonts):
     id=digest+'-'+str(index);progress('analysis',f'Начертание {index+1} из {len(fonts)}')
     info=self.metadata(font,id);info.update(id=id,filename=job['filename'],format=path.suffix[1:].upper(),size=path.stat().st_size)
     faces.append((id,digest,json.dumps(info,ensure_ascii=False)))
    if not faces:raise TTLibError('Файл не содержит начертаний')
    for font in fonts:font.close()
    fonts.clear()
   progress('cataloguing')
   with archive_lock(self.data/'import.lock'),self.db() as con:
    # Readers see no partially analyzed collections. Search triggers receive
    # full Unicode/coverage metadata in this same transaction.
    con.execute('DELETE FROM faces WHERE hash=?',(digest,))
    con.executemany('INSERT INTO faces VALUES(?,?,?)',faces)
    for id,_,value in faces:
     if json.loads(value)['preview_status']=='queued':import_queue.enqueue_preview(con,id,path,job['filename'],job['source'])
    con.execute("UPDATE analysis_state SET status='ready',version=?,error='' WHERE hash=?",(VERSION,digest))
    original_checks.release(con,digest)
    import_queue.finish(con,job['id'],result=faces[0][0]+'~'+job['source'])
   self.event(job['filename'],'ok',f'Анализ готов: {len(faces)} начертаний')
  except Exception as exc:
   with self.db() as con:
    con.execute("UPDATE analysis_state SET status='error',error=? WHERE hash=?",(str(exc)[:2000],digest))
    import_queue.finish(con,job['id'],'error',str(exc)[:2000])
   self.event(job['filename'],'error','Анализ: '+str(exc)[:400])
   raise
  finally:
   for font in fonts:font.close()
 def process(self):
  with self.db() as con:job=import_queue.claim(con,'analysis')
  if not job:return False
  try:self.analyze(job)
  except Exception:pass # Failed job is durable; the worker must continue.
  return True
 def inline(self,digest):
  # Compatibility for maintenance/tests; runtime receipt never calls this.
  with self.db() as con:
   con.execute('BEGIN IMMEDIATE')
   state=con.execute('SELECT status FROM analysis_state WHERE hash=?',(digest,)).fetchone()
   if state and state[0]=='ready':return
   file=con.execute('SELECT * FROM files WHERE hash=?',(digest,)).fetchone()
   if not original_checks.admit(con,file):
    pending=True
   else:pending=False
  if pending:
   while True:
    with self.db() as con:check=con.execute('SELECT status FROM font_checks WHERE hash=?',(digest,)).fetchone()
    if not check or check[0] not in {'queued','running'}:break
    if not original_checks.process(self.db,self.data):break
   with self.db() as con:
    if not original_checks.allowed(con,digest,file['source']):return
  with self.db() as con:
   con.execute('BEGIN IMMEDIATE')
   enqueue(con,file)
   row=con.execute("SELECT * FROM work_queue WHERE kind='analysis' AND identity=?",(digest,)).fetchone()
   if row['status']=='running':raise RuntimeError('Анализ уже выполняется')
   con.execute("UPDATE work_queue SET status='running',attempts=attempts+1,started=? WHERE id=?",(time.time(),row['id']))
  self.analyze(dict(row))
 def recover(self):
  with self.db() as con:jobs=[dict(r) for r in con.execute("SELECT * FROM import_jobs WHERE phase<>'failed'")]
  for job in jobs:
   source=Path(job['input_path']);target=Path(job['target'])
   try:
    candidate=source if source.is_file() and sha256(source)==job['hash'] else target
    if not candidate.is_file() or sha256(candidate)!=job['hash']:raise ValueError('Нет проверенной копии оригинала')
    self.accept(candidate,job['source'],job['filename'])
    target.with_suffix('.tmp').unlink(missing_ok=True)
    with self.db() as con:
     con.execute("UPDATE work_queue SET status='done',phase='complete',result=?,finished=? WHERE kind='import' AND input_path=?",(job['hash'],time.time(),job['input_path']))
   except Exception as exc:self.event(job['filename'],'error','Восстановление приёма: '+str(exc)[:400])
