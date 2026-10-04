"""Explicit repair candidates: immutable originals, comparison, user publication.

Native OTS serialization is tried first. A refusal may be followed by fontTools
reserialization with the existing tolerant cmap repair, only after an explicit
repair request. Neither path discards arbitrary tables to force a pass. The
candidate must pass OTS again; rejected partial output is never downloadable.
"""
import json,time,hashlib
from pathlib import Path
from flask import request,jsonify,abort,send_file
from fontTools.ttLib import TTFont,TTCollection
from font_analysis import usable_cmap
from archive_io import publish,archive_lock
import original_checks,font_validation,import_queue

def initialize(con):
 con.executescript('''
 INSERT OR IGNORE INTO queue_control VALUES('repair',0);
 CREATE TABLE IF NOT EXISTS repair_candidates(hash TEXT PRIMARY KEY,status TEXT,report TEXT,path TEXT,error TEXT,created REAL);
 CREATE TABLE IF NOT EXISTS repair_auto_origins(hash TEXT,source TEXT,status TEXT DEFAULT 'pending',candidate_hash TEXT,reason TEXT,PRIMARY KEY(hash,source));
 CREATE TABLE IF NOT EXISTS repair_choices(id INTEGER PRIMARY KEY,original_hash TEXT,candidate_hash TEXT,source TEXT,choice TEXT,created REAL);
 ''')

def schedule_auto(con,digest):
 """Only newly held block-policy origins enter automatic recovery, never backfill."""
 sources=[r[0] for r in con.execute('SELECT source FROM held_origins WHERE hash=?',(digest,)) if original_checks.mode(con,r[0])=='block']
 check=con.execute('SELECT status FROM font_checks WHERE hash=?',(digest,)).fetchone()
 if not sources or not check or check[0]!='rejected':return
 new=False
 for source in sources:
  new=bool(con.execute("INSERT OR IGNORE INTO repair_auto_origins(hash,source) VALUES(?,?)",(digest,source)).rowcount) or new
 if not new:return
 row=con.execute('SELECT * FROM files WHERE hash=?',(digest,)).fetchone()
 existing=con.execute('SELECT status FROM repair_candidates WHERE hash=?',(digest,)).fetchone()
 if existing and existing[0] in {'queued','running'}:return
 running=con.execute("SELECT status FROM work_queue WHERE kind='repair' AND identity=?",(digest,)).fetchone()
 if running and running[0]=='running':return
 if existing and existing[0]=='error':
  con.execute("UPDATE repair_auto_origins SET status='review',reason='Предыдущая попытка восстановления не удалась' WHERE hash=? AND status='pending'",(digest,));return
 con.execute("INSERT INTO repair_candidates(hash,status,created) VALUES(?,'queued',?) ON CONFLICT(hash) DO NOTHING",(digest,time.time()))
 con.execute("INSERT INTO work_queue(kind,identity,input_path,filename,source,created) VALUES('repair',?,?,?,?,?) ON CONFLICT(kind,identity) DO UPDATE SET status='queued',error='',attempts=0",(digest,row['path'],row['name'],sources[0],time.time()))


def safety_reason(report):
 """Strict lossless admission. Unknown or changed data requires human review."""
 if not report.get('original_faces') or report['original_faces']!=report['candidate_faces']:return 'Не удалось подтвердить сохранность всех начертаний'
 for face in report['faces']:
  old,new=face['original'],face['candidate']
  if not old or not old['complete'] or not new['complete']:return 'Покрытие прочитано не полностью'
  if any(old.get(field) is None or new.get(field) is None for field in ('mapping','table_hashes')):return 'Недостаточно данных для проверки сохранности'
  for field in ('family','style','glyphs','chars','tables','features','mapping','table_hashes'):
   if old.get(field)!=new.get(field):return 'Изменились данные начертания: '+field
 return ''


def publish_candidate(pipeline,data,path,name,source):
 """Normal durable intake; preserve both original and comparison artifact."""
 import shutil,tempfile
 with Path(path).open('rb') as stream:signature=stream.read(4)
 extension='.ttc' if signature==b'ttcf' else '.otf' if signature==b'OTTO' else '.ttf'
 with tempfile.TemporaryDirectory(dir=data) as folder:
  stage=Path(folder)/(Path(name).stem+'-processed'+extension)
  shutil.copyfile(path,stage)
  return pipeline.accept(stage,source,stage.name)


def publish_auto(db,data,pipeline,digest):
 if pipeline is None:return
 with db() as con:
  candidate=con.execute('SELECT * FROM repair_candidates WHERE hash=?',(digest,)).fetchone()
  origins=con.execute("SELECT source FROM repair_auto_origins WHERE hash=? AND status='pending'",(digest,)).fetchall()
  original=con.execute('SELECT * FROM files WHERE hash=?',(digest,)).fetchone()
 if not origins:return
 report=json.loads(candidate['report'] or '{}');reason=safety_reason(report)
 if reason:
  with db() as con:con.execute("UPDATE repair_auto_origins SET status='review',reason=? WHERE hash=? AND status='pending'",(reason,digest))
  return
 from archive_pipeline import sha256
 if sha256(candidate['path'])!=report['sha256']:raise ValueError('Контрольная сумма исправления изменилась')
 for origin in origins:
  source=origin[0]
  # Recheck current source policy and held origin before publishing. A manual
  # override or policy change while repairing must not create another version.
  with db() as con:
   if original_checks.allowed(con,digest,source):
    con.execute("UPDATE repair_auto_origins SET status='skipped',reason='Оригинал уже допущен' WHERE hash=? AND source=?",(digest,source));continue
  result=publish_candidate(pipeline,data,candidate['path'],original['name'],source)
  with db() as con:
   con.execute("UPDATE repair_auto_origins SET status='published',candidate_hash=?,reason='' WHERE hash=? AND source=?",(result,digest,source))
   if not con.execute("SELECT 1 FROM repair_choices WHERE original_hash=? AND candidate_hash=? AND source=? AND choice='automatic'",(digest,result,source)).fetchone():
    con.execute('INSERT INTO repair_choices(original_hash,candidate_hash,source,choice,created) VALUES(?,?,?,?,?)',(digest,result,source,'automatic',time.time()))
   for job in con.execute("SELECT id FROM work_queue WHERE source=? AND status IN ('error','blocked') AND substr(identity,1,64)=? AND kind IN ('ots','analysis','preview','convert')",(source,digest)).fetchall():
    con.execute('INSERT OR REPLACE INTO issue_resolutions VALUES(?,?,?)',(job[0],time.time(),'Автоматически восстановлен: '+result))


def finish_repair(con,job,pipeline):
 # An origin may arrive during publication. Keep the same durable job queued
 # until every pending origin has a decision, without resetting a live claim.
 import_queue.finish(con,job['id'])
 if pipeline is not None and con.execute("SELECT 1 FROM repair_auto_origins WHERE hash=? AND status='pending'",(job['identity'],)).fetchone():
  con.execute("UPDATE work_queue SET status='queued',error='',attempts=0 WHERE id=?",(job['id'],))


def fonts(path):
 with Path(path).open('rb') as stream:collection=stream.read(4)==b'ttcf'
 return TTCollection(path,lazy=True).fonts if collection else [TTFont(path,lazy=True)]

def snapshot(path):
 loaded=[]
 try:
  loaded=fonts(path);rows=[]
  for font in loaded:
   chars,warnings,complete=usable_cmap(font);features=set()
   for tag in ('GSUB','GPOS'):
    table=font.get(tag)
    if table and table.table.FeatureList:features.update(f.FeatureTag for f in table.table.FeatureList.FeatureRecord)
   table_hashes={}
   for tag in font.keys():
    if tag in {'GlyphOrder','cmap'}:continue
    if tag=='name':
     # OTS may reorder/deduplicate name storage; preserve every logical record.
     records=sorted(set((n.platformID,n.platEncID,n.langID,n.nameID,n.toBytes().hex()) for n in font['name'].names))
     raw=json.dumps(records).encode()
    else:raw=font.getTableData(tag)
    if tag=='head':raw=raw[:8]+b'\0'*4+raw[12:28]+b'\0'*8+raw[36:] # checksum and modified timestamp
    table_hashes[tag]=hashlib.sha256(raw).hexdigest()
   # Compare all character maps, including variation selectors, by digest.
   # Reports remain compact even for large CJK collections.
   maps=sorted((t.platformID,t.platEncID,t.format,getattr(t,'language',0),sorted(getattr(t,'cmap',{}).items()),sorted(getattr(t,'uvsDict',{}).items())) for t in font['cmap'].tables) if complete else None
   mapping=hashlib.sha256(json.dumps(maps,sort_keys=True).encode()).hexdigest() if maps is not None else None
   rows.append(dict(family=font['name'].getDebugName(16) or font['name'].getDebugName(1),style=font['name'].getDebugName(17) or font['name'].getDebugName(2),glyphs=font['maxp'].numGlyphs,chars=chars,tables=sorted(tag for tag in font.keys() if tag!='GlyphOrder'),features=sorted(features),warnings=warnings,complete=complete,mapping=mapping,table_hashes=table_hashes))
  return rows
 finally:
  for font in loaded:font.close()

def process(db,data,pipeline=None):
 from archive_pipeline import sha256,heavy
 with db() as con:job=import_queue.claim(con,'repair')
 if not job:return False
 with db() as con:cached=con.execute('SELECT status FROM repair_candidates WHERE hash=?',(job['identity'],)).fetchone()
 if cached and cached[0]=='ready':
  try:
   publish_auto(db,data,pipeline,job['identity'])
   with db() as con:finish_repair(con,job,pipeline)
  except Exception as exc:
   with db() as con:
    con.execute("UPDATE repair_auto_origins SET status='review',reason=? WHERE hash=? AND status='pending'",(str(exc),job['identity']))
    import_queue.finish(con,job['id'],'error',str(exc))
  return True
 with db() as con:con.execute("UPDATE repair_candidates SET status='running' WHERE hash=?",(job['identity'],))
 digest=job['identity'];folder=data/'repairs';folder.mkdir(exist_ok=True)
 candidate=folder/(digest+'.candidate');partial=folder/(digest+'.part');normalized=folder/(digest+'.normalized')
 try:
  original=Path(job['input_path'])
  if sha256(original)!=digest:raise ValueError('Контрольная сумма оригинала не совпала')
  # Apply the same bounded header/size eligibility checks before native repair.
  with heavy(data,original):
   eligibility,_=original_checks.inspect(original)
   if eligibility in {'unsupported','error'}:raise ValueError('Файл не подходит для автоматической попытки исправления; см. результат OTS')
   try:before=snapshot(original);before_error=''
   except Exception as exc:before=[];before_error=str(exc)[:1000]
   method='Пересборка OpenType Sanitizer';first_error=''
   try:font_validation.check(original,destination=partial)
   except font_validation.ValidationError as exc:
    first_error=str(exc);partial.unlink(missing_ok=True);loaded=[]
    try:
     loaded=fonts(original)
     for font in loaded:usable_cmap(font);font.flavor=None
     if len(loaded)>1:
      collection=TTCollection();collection.fonts=loaded;collection.save(normalized)
     else:loaded[0].save(normalized)
    finally:
     for font in loaded:font.close()
    method='Пересборка fontTools и восстановление читаемой cmap, затем OTS'
    font_validation.check(normalized,destination=partial)
   status,validation=original_checks.inspect(partial)
   if status!='passed':raise ValueError('Обработанная копия не прошла повторную проверку OTS: '+validation.get('message',''))
   after=snapshot(partial);changes=[]
   for index,row in enumerate(after):
    old=before[index] if index<len(before) else None
    changes.append(dict(index=index,original=old,candidate=row,removed_chars=sorted(set(old['chars'])-set(row['chars'])) if old else None,added_chars=sorted(set(row['chars'])-set(old['chars'])) if old else None,removed_tables=sorted(set(old['tables'])-set(row['tables'])) if old else None,removed_features=sorted(set(old['features'])-set(row['features'])) if old else None))
   report=dict(method=method,initial_error=first_error,original_error=before_error,original_faces=len(before) if before else None,candidate_faces=len(after),original_size=original.stat().st_size,candidate_size=partial.stat().st_size,sha256=sha256(partial),validation=validation,faces=changes)
   with archive_lock(data/'import.lock'):publish(partial,candidate)
  with db() as con:
   con.execute("UPDATE repair_candidates SET status='ready',report=?,path=?,error='' WHERE hash=?",(json.dumps(report,ensure_ascii=False),str(candidate),digest))
  publish_auto(db,data,pipeline,digest)
  with db() as con:finish_repair(con,job,pipeline)
 except Exception as exc:
  partial.unlink(missing_ok=True)
  with db() as con:
   con.execute("UPDATE repair_candidates SET status='error',error=? WHERE hash=?",(str(exc)[:8192],digest))
   con.execute("UPDATE repair_auto_origins SET status='review',reason=? WHERE hash=? AND status='pending'",(str(exc)[:8192],digest))
   import_queue.finish(con,job['id'],'error',str(exc)[:8192])
 finally:normalized.unlink(missing_ok=True)
 return True

def register(app,db,data,pipeline):
 @app.post('/api/repairs/catalog')
 def repair_catalog():
  # One explicit batch request schedules every rejected physical file once.
  # Existing attempts, including failures, are retained rather than rerun.
  with db() as con:
   rows=con.execute("SELECT f.* FROM files f JOIN font_checks c ON c.hash=f.hash LEFT JOIN repair_candidates r ON r.hash=f.hash WHERE c.status='rejected' AND r.hash IS NULL").fetchall()
   for row in rows:
    con.execute("INSERT INTO repair_candidates(hash,status,created) VALUES(?,'queued',?)",(row['hash'],time.time()))
    con.execute("INSERT INTO work_queue(kind,identity,input_path,filename,source,created) VALUES('repair',?,?,?,?,?) ON CONFLICT(kind,identity) DO NOTHING",(row['hash'],row['path'],row['name'],row['source'],time.time()))
  return jsonify(message=f'Поставлено на восстановление: {len(rows)}. Результаты появятся в таблице проверок. Выбор версии остаётся за вами.')

 @app.post('/api/repairs/<digest>')
 def request_repair(digest):
  with db() as con:
   row=con.execute('SELECT * FROM files WHERE hash=?',(digest,)).fetchone()
   if not row:abort(404)
   old=con.execute('SELECT status FROM repair_candidates WHERE hash=?',(digest,)).fetchone()
   if old and old[0] in {'ready','queued','running'}:return jsonify(status=old[0]),202
   con.execute("INSERT INTO repair_candidates(hash,status,created) VALUES(?,'queued',?) ON CONFLICT(hash) DO UPDATE SET status='queued',error=''",(digest,time.time()))
   con.execute("INSERT INTO work_queue(kind,identity,input_path,filename,source,created) VALUES('repair',?,?,?,?,?) ON CONFLICT(kind,identity) DO UPDATE SET status='queued',error='',attempts=0",(digest,row['path'],row['name'],row['source'],time.time()))
  return jsonify(status='queued'),202

 @app.get('/api/repairs/<digest>')
 def repair_status(digest):
  with db() as con:
   row=con.execute('SELECT * FROM repair_candidates WHERE hash=?',(digest,)).fetchone()
   origins=[r[0] for r in con.execute('SELECT source FROM origins WHERE hash=?',(digest,))]
  if not row:abort(404)
  result=dict(row);result.pop('path',None);result['report']=json.loads(result.get('report') or '{}');result['sources']=origins
  return jsonify(result)

 @app.get('/repairs/<digest>/file')
 def repair_file(digest):
  with db() as con:row=con.execute('SELECT * FROM repair_candidates WHERE hash=?',(digest,)).fetchone()
  if not row or row['status']!='ready':abort(404)
  with Path(row['path']).open('rb') as stream:signature=stream.read(4)
  extension='.ttc' if signature==b'ttcf' else '.otf' if signature==b'OTTO' else '.ttf'
  return send_file(row['path'],as_attachment=True,download_name='processed-'+digest[:12]+extension)

 @app.post('/api/repairs/<digest>/choose')
 def choose(digest):
  from archive_pipeline import sha256
  payload=request.get_json(silent=True) or {};source=payload.get('source');choice=payload.get('choice')
  if choice not in {'original','candidate'}:abort(400)
  with db() as con:
   if not con.execute('SELECT 1 FROM origins WHERE hash=? AND source=?',(digest,source)).fetchone():abort(404)
   row=con.execute('SELECT * FROM files WHERE hash=?',(digest,)).fetchone()
   candidate=con.execute('SELECT * FROM repair_candidates WHERE hash=?',(digest,)).fetchone()
  result_hash=digest
  if choice=='candidate':
   if not candidate or candidate['status']!='ready':return jsonify(error='Проверенная копия пока не готова.'),409
   report=json.loads(candidate['report']);path=Path(candidate['path'])
   if sha256(path)!=report['sha256']:return jsonify(error='Копия изменилась; повторите исправление.'),409
   # Admission performs no parsing. Choose extension from the checked container.
   with path.open('rb') as stream:signature=stream.read(4)
   extension='.ttc' if signature==b'ttcf' else '.otf' if signature==b'OTTO' else '.ttf'
   name=Path(row['name']).stem+'-processed'+extension
   # Preserve the comparison artifact. Intake cleans only its staging copy.
   import shutil,tempfile
   with tempfile.TemporaryDirectory(dir=data) as folder:
    stage=Path(folder)/name;shutil.copyfile(path,stage);result_hash=pipeline.accept(stage,source,name)
  else:
   with db() as con:
    reason='Пользователь выбрал оригинал в сравнении исправления'
    con.execute('INSERT OR REPLACE INTO check_overrides VALUES(?,?,?,?)',(digest,source,time.time(),reason))
    con.execute('INSERT INTO admission_history(hash,source,created,reason) VALUES(?,?,?,?)',(digest,source,time.time(),reason))
    state=con.execute('SELECT status FROM analysis_state WHERE hash=?',(digest,)).fetchone()
    if state and state[0]=='error':
     from archive_pipeline import enqueue
     entry=dict(row);entry['source']=source;enqueue(con,entry,force=True)
     con.execute("UPDATE analysis_state SET status='queued',error='' WHERE hash=?",(digest,))
    original_checks.release(con,digest)
  with db() as con:
   con.execute('INSERT INTO repair_choices(original_hash,candidate_hash,source,choice,created) VALUES(?,?,?,?,?)',(digest,result_hash,source,choice,time.time()))
   reason='В сравнении выбрана '+('обработанная копия '+result_hash if choice=='candidate' else 'исходная версия')
   for job in con.execute("SELECT id FROM work_queue WHERE source=? AND status IN ('error','blocked') AND ((kind='ots' AND identity=?) OR (kind='analysis' AND identity=? AND status='blocked'))",(source,digest,digest)).fetchall():
    con.execute('INSERT OR REPLACE INTO issue_resolutions VALUES(?,?,?)',(job[0],time.time(),reason))
  return jsonify(message='Выбранная версия сохранена; анализ выполняется через очереди.',hash=result_hash,url='/imports')
