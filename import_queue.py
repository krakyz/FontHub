"""Durable local work queue, independent of font parsing and HTTP rendering.

Import identity is a staging path; preview identity is a physical face ID, shared
by all source instances. Completed paths can be reused when size/mtime changes.
Claims and pause checks run in one SQLite write transaction. One OS-locked worker
per kind owns restart recovery; starting another server never resets live jobs.
Pausing stops new claims; an already running file completes its safe boundary.
"""
import time
from pathlib import Path


def initialize(con):
    con.executescript('''
    CREATE TABLE IF NOT EXISTS work_queue(
      id INTEGER PRIMARY KEY,kind TEXT NOT NULL,identity TEXT NOT NULL,
      input_path TEXT NOT NULL,filename TEXT NOT NULL,source TEXT NOT NULL,
      size INTEGER,mtime INTEGER,status TEXT NOT NULL DEFAULT 'queued',
      phase TEXT NOT NULL DEFAULT 'waiting',detail TEXT NOT NULL DEFAULT '',
      error TEXT NOT NULL DEFAULT '',attempts INTEGER NOT NULL DEFAULT 0,
      created REAL NOT NULL,started REAL,finished REAL,available_at REAL DEFAULT 0,
      result TEXT,UNIQUE(kind,identity));
    CREATE INDEX IF NOT EXISTS work_pending ON work_queue(kind,status,available_at,created,id);
    CREATE TABLE IF NOT EXISTS queue_control(kind TEXT PRIMARY KEY,paused INTEGER NOT NULL DEFAULT 0);
    INSERT OR IGNORE INTO queue_control VALUES('import',0);
    INSERT OR IGNORE INTO queue_control VALUES('preview',0);
    INSERT OR IGNORE INTO queue_control VALUES('convert',0);
    CREATE TABLE IF NOT EXISTS queue_migrations(name TEXT PRIMARY KEY);
    ''')


def enqueue_import(con,path,source,filename=None,failed=False,error=''):
    path=Path(path).resolve();stat=path.stat();now=time.time()
    old=con.execute("SELECT * FROM work_queue WHERE kind='import' AND identity=?",(str(path),)).fetchone()
    if old:
        # Discovery must never reset a claimed job or erase its quarantine path.
        if old['status'] in {'queued','running'}: return old['id']
        if (old['size'],old['mtime'])==(stat.st_size,stat.st_mtime_ns): return old['id']
        con.execute("UPDATE work_queue SET input_path=?,filename=?,source=?,size=?,mtime=?,status='queued',phase='waiting',error='',detail='',attempts=0,created=?,started=NULL,finished=NULL,available_at=0,result=NULL WHERE id=?",(str(path),filename or path.name,source,stat.st_size,stat.st_mtime_ns,now,old['id']))
        return old['id']
    cursor=con.execute('''INSERT INTO work_queue(kind,identity,input_path,filename,source,size,mtime,status,error,created)
        VALUES('import',?,?,?,?,?,?,?,?,?)''',(str(path),str(path),filename or path.name,source,stat.st_size,stat.st_mtime_ns,'error' if failed else 'queued',error,now))
    return cursor.lastrowid


def enqueue_preview(con,face_id,path,filename,source,force=False):
    con.execute("""INSERT INTO work_queue(kind,identity,input_path,filename,source,created)
        VALUES('preview',?,?,?,?,?) ON CONFLICT(kind,identity) DO NOTHING""",(face_id,str(path),filename,source,time.time()))
    if force:
        con.execute("UPDATE work_queue SET status='queued',phase='waiting',error='',detail='',started=NULL,finished=NULL,available_at=0 WHERE kind='preview' AND identity=? AND status!='running'",(face_id,))


def claim(con,kind):
    # BEGIN IMMEDIATE closes the select/update race, including with pause requests.
    con.execute('BEGIN IMMEDIATE')
    if con.execute('SELECT paused FROM queue_control WHERE kind=?',(kind,)).fetchone()[0]: return None
    row=con.execute("SELECT * FROM work_queue WHERE kind=? AND status='queued' AND available_at<=? ORDER BY created,id LIMIT 1",(kind,time.time())).fetchone()
    if row is None: return None
    con.execute("UPDATE work_queue SET status='running',phase='starting',error='',detail='',attempts=attempts+1,started=?,finished=NULL WHERE id=?",(time.time(),row['id']))
    return dict(row)


def progress(con,id,phase,detail=''):
    con.execute('UPDATE work_queue SET phase=?,detail=? WHERE id=? AND status=\'running\'',(phase,detail,id))


def finish(con,id,status='done',error='',result=None,path=None):
    con.execute("UPDATE work_queue SET status=?,phase=?,error=?,detail='',result=COALESCE(?,result),input_path=COALESCE(?,input_path),finished=? WHERE id=?",(status,'complete' if status=='done' else 'failed',error,result,str(path) if path else None,time.time(),id))


def defer_changed(con,job,path):
    stat=Path(path).stat()
    con.execute("UPDATE work_queue SET status='queued',phase='waiting',detail='Файл изменяется; ожидание завершения копирования',size=?,mtime=?,available_at=?,started=NULL WHERE id=?",(stat.st_size,stat.st_mtime_ns,time.time()+3,job['id']))


def reset_running(con,kind):
    # Called only under the lifetime OS lock for this queue. Bound repeated
    # native-process crashes; manual retry explicitly starts a fresh attempt set.
    con.execute("UPDATE work_queue SET status=CASE WHEN attempts>=3 THEN 'error' ELSE 'queued' END,phase=CASE WHEN attempts>=3 THEN 'failed' ELSE 'waiting' END,error=CASE WHEN attempts>=3 THEN 'Рабочий процесс прервался три раза; требуется ручной повтор' ELSE error END,detail='Продолжение после перезапуска',started=NULL WHERE kind=? AND status='running'",(kind,))


def snapshot(con,kind='',status='active',page=1):
    # Keep counters, pagination and running jobs in one SQLite read snapshot.
    # WAL lets workers continue writing while this short transaction is read.
    if not con.in_transaction: con.execute('BEGIN')
    controls={row['kind']:bool(row['paused']) for row in con.execute('SELECT * FROM queue_control')}
    counts={name:{state:0 for state in ['queued','running','done','error','cancelled','blocked']} for name in controls}
    for row in con.execute('SELECT kind,status,COUNT(*) AS count FROM work_queue GROUP BY kind,status'): counts[row['kind']][row['status']]=row['count']
    conditions=[];parameters=[]
    if kind: conditions.append('kind=?');parameters.append(kind)
    if status=='active': conditions.append("status IN ('queued','running','error','blocked')")
    elif status: conditions.append('status=?');parameters.append(status)
    where=' WHERE '+' AND '.join(conditions) if conditions else ''
    total=con.execute('SELECT COUNT(*) FROM work_queue'+where,parameters).fetchone()[0];pages=max(1,(total+24)//25);page=max(1,min(page,pages))
    rows=[dict(row) for row in con.execute("SELECT * FROM work_queue"+where+" ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 WHEN 'error' THEN 2 ELSE 3 END,created,id LIMIT 25 OFFSET ?",[*parameters,(page-1)*25])]
    for row in rows:
        if row['kind'] in {'preview','convert'}:
            physical=row['identity'].split('|')[0]
            face=con.execute('SELECT family,style FROM search_faces WHERE id=?',(physical,)).fetchone()
            if face: row.update(family=face['family'],style=face['style'])
    running=[dict(row) for row in con.execute("SELECT * FROM work_queue WHERE status='running' ORDER BY kind")]
    return dict(controls=controls,counts=counts,jobs=rows,running=running,total=total,page=page,pages=pages,now=time.time())
