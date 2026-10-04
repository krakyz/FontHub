"""HTTP views for physical checks and explicit source admission resolutions."""
import json,time
from pathlib import Path
from flask import render_template,request,jsonify,abort,send_file
import original_checks,import_queue

def register(app,db,data,sources):
 @app.get('/api/checks/status')
 def check_status():
  with db() as con:
   counts={r[0]:r[1] for r in con.execute("SELECT COALESCE(c.status,'unchecked'),COUNT(*) FROM files f LEFT JOIN font_checks c ON c.hash=f.hash GROUP BY COALESCE(c.status,'unchecked')")}
   paused=bool(con.execute("SELECT paused FROM queue_control WHERE kind='ots'").fetchone()[0])
  return jsonify(counts=counts,paused=paused,labels=original_checks.LABELS)
 @app.get('/issues')
 def issues():
  view=request.args.get('view','issues');status=request.args.get('status','');digest=request.args.get('hash','');resolved=request.args.get('resolved')=='1'
  try:page=max(1,int(request.args.get('page',1)))
  except ValueError:abort(400)
  with db() as con:
   if view=='checks':
    if status and status not in original_checks.LABELS:abort(400)
    conditions=[];args=[]
    if status:conditions.append("COALESCE(c.status,'unchecked')=?");args.append(status)
    if digest:conditions.append('f.hash=?');args.append(digest)
    where=' WHERE '+' AND '.join(conditions) if conditions else ''
    base=' FROM files f LEFT JOIN font_checks c ON c.hash=f.hash'+where
    total=con.execute('SELECT COUNT(*)'+base,args).fetchone()[0]
    page=min(page,max(1,(total+24)//25))
    rows=[dict(r) for r in con.execute("SELECT f.hash,f.name,f.source,c.status,c.version,c.checked,c.seconds,c.report"+base+' ORDER BY f.rowid DESC LIMIT 25 OFFSET ?',[*args,(page-1)*25])]
    for row in rows:
     repair=con.execute('SELECT status,error FROM repair_candidates WHERE hash=?',(row['hash'],)).fetchone()
     row['repair_status']=repair['status'] if repair else ''
     row['repair_error']=repair['error'] if repair else ''
     row['repair_auto']=[dict(r) for r in con.execute('SELECT source,status,reason,candidate_hash FROM repair_auto_origins WHERE hash=?',(row['hash'],))]
     row['report']=json.loads(row['report'] or '{}')
     row['origins']=[dict(source=r[0],mode=original_checks.mode(con,r[0]),override=bool(con.execute('SELECT 1 FROM check_overrides WHERE hash=? AND source=?',(row['hash'],r[0])).fetchone())) for r in con.execute('SELECT source FROM origins WHERE hash=?',(row['hash'],)).fetchall()]
   else:
    condition="status IN ('error','blocked') AND "+('' if resolved else 'NOT ')+"EXISTS(SELECT 1 FROM issue_resolutions r WHERE r.job_id=work_queue.id)"
    if not resolved:condition+=" AND NOT EXISTS(SELECT 1 FROM repair_candidates r JOIN repair_auto_origins a ON a.hash=r.hash WHERE a.status='pending' AND r.status IN ('queued','running','ready') AND a.source=work_queue.source AND r.hash=substr(work_queue.identity,1,64))"
    total=con.execute('SELECT COUNT(*) FROM work_queue WHERE '+condition).fetchone()[0]
    page=min(page,max(1,(total+24)//25))
    rows=[dict(r) for r in con.execute('SELECT * FROM work_queue WHERE '+condition+' ORDER BY id DESC LIMIT 25 OFFSET ?',((page-1)*25,))]
   counts={r[0]:r[1] for r in con.execute("SELECT COALESCE(c.status,'unchecked'),COUNT(*) FROM files f LEFT JOIN font_checks c ON c.hash=f.hash GROUP BY COALESCE(c.status,'unchecked')")}
   paused=bool(con.execute("SELECT paused FROM queue_control WHERE kind='ots'").fetchone()[0])
  return render_template('issues.html',view=view,rows=rows,labels=original_checks.LABELS,counts=counts,paused=paused,page=page,pages=max(1,(total+24)//25),total=total,status=status,digest=digest,resolved=resolved,source_names=sources)

 @app.post('/api/checks/catalog')
 def check_catalog():
  # Batch SQL scheduling is cheap; actual native checks run only in the worker.
  with db() as con:
   count=original_checks.seed(con,250)
  return jsonify(message=f'Добавлено проверок: {count}. Остальные будут поставлены постепенно.')

 @app.post('/api/checks/<digest>/retry')
 def retry_check(digest):
  with db() as con:
   row=con.execute('SELECT * FROM files WHERE hash=?',(digest,)).fetchone()
   if not row:abort(404)
   original_checks.enqueue(con,row,force=True)
  return jsonify(message='Проверка поставлена в очередь.')

 @app.get('/api/checks/<digest>')
 def check_info(digest):
  with db() as con:
   if not con.execute('SELECT 1 FROM files WHERE hash=?',(digest,)).fetchone():abort(404)
   row=con.execute('SELECT * FROM font_checks WHERE hash=?',(digest,)).fetchone()
   history=[dict(r) for r in con.execute('SELECT * FROM check_history WHERE hash=? ORDER BY id DESC LIMIT 20',(digest,))]
   admissions=[dict(r) for r in con.execute('SELECT * FROM admission_history WHERE hash=? ORDER BY id DESC LIMIT 20',(digest,))]
   for item in history:item['report']=json.loads(item['report'])
  result=dict(row) if row else dict(hash=digest,status='unchecked')
  result['report']=json.loads(result.get('report') or '{}');result['history']=history;result['admissions']=admissions
  return jsonify(result)

 @app.post('/api/checks/<digest>/override')
 def override_check(digest):
  payload=request.get_json(silent=True) or {};source=payload.get('source');reason=payload.get('reason','')
  if not isinstance(reason,str) or not reason.strip():return jsonify(error='Укажите причину разрешения анализа.'),400
  with db() as con:
   if not con.execute('SELECT 1 FROM origins WHERE hash=? AND source=?',(digest,source)).fetchone():abort(404)
   con.execute('INSERT OR REPLACE INTO check_overrides VALUES(?,?,?,?)',(digest,source,time.time(),reason[:1000]))
   con.execute('INSERT INTO admission_history(hash,source,created,reason) VALUES(?,?,?,?)',(digest,source,time.time(),reason[:1000]))
   original_checks.release(con,digest)
   for job in con.execute("SELECT id FROM work_queue WHERE kind='ots' AND identity=? AND source=? AND status='error'",(digest,source)).fetchall():
    con.execute('INSERT OR REPLACE INTO issue_resolutions VALUES(?,?,?)',(job[0],time.time(),'Анализ разрешён вручную: '+reason[:1000]))
  return jsonify(message='Анализ разрешён для выбранного источника. Результат OTS не изменён.')

 @app.post('/api/issues/<int:id>/resolve')
 def resolve_issue(id):
  reason=(request.get_json(silent=True) or {}).get('reason','')
  if not isinstance(reason,str) or not reason.strip():return jsonify(error='Укажите причину решения.'),400
  with db() as con:
   row=con.execute('SELECT status FROM work_queue WHERE id=?',(id,)).fetchone()
   if not row:abort(404)
   if row[0] not in {'error','blocked'}:abort(409)
   con.execute('INSERT OR REPLACE INTO issue_resolutions VALUES(?,?,?)',(id,time.time(),reason[:1000]))
  return jsonify(message='Отмечено как рассмотренное. Файл и ошибка сохранены; допуск OTS не изменён.')

 @app.post('/api/sources/<source>/ots-policy')
 def policy(source):
  if source not in sources:abort(404)
  mode=(request.get_json(silent=True) or {}).get('mode')
  if mode not in original_checks.MODES:abort(400)
  with db() as con:
   con.execute('INSERT OR REPLACE INTO source_policy VALUES(?,?)',(source,mode))
   original_checks.release_batch(con)
  return jsonify(message='Политика OTS сохранена.',mode=mode)

 @app.get('/issues/jobs/<int:id>/file')
 def job_file(id):
  with db() as con:row=con.execute('SELECT * FROM work_queue WHERE id=?',(id,)).fetchone()
  if not row or row['status'] not in {'error','blocked'} or not Path(row['input_path']).is_file():abort(404)
  return send_file(row['input_path'],as_attachment=True,download_name=row['filename'])
