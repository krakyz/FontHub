"""Kill preview processes before/after atomic publication; original stays safe."""

# Direct execution from the repository root keeps application imports available.
import sys as _test_sys
from pathlib import Path as _TestPath
_test_sys.path.insert(0,str(_TestPath(__file__).resolve().parents[1]))
import hashlib,os,shutil,subprocess,sys,tempfile,sqlite3
from pathlib import Path

font=Path('C:/Windows/Fonts/arial.ttf')
for boundary in ['partial','published']:
    with tempfile.TemporaryDirectory() as temporary:
        root=Path(temporary);(root/'inbox').mkdir();incoming=root/'inbox'/'fixture.ttf';shutil.copyfile(font,incoming)
        env=dict(os.environ,FONTHUB_DATA=temporary,PREVIEW_CRASH=boundary)
        env.pop('FONTHUB_INBOX',None)
        code='''import app,os
app.import_file(app.INBOX/'fixture.ttf')
if os.environ['PREVIEW_CRASH']=='partial':
 original=app.TTFont.save
 def crash(*args,**kwargs): original(*args,**kwargs);os._exit(73)
 app.TTFont.save=crash
else:
 original=app.publish
 def crash(*args,**kwargs): original(*args,**kwargs);os._exit(73)
 app.publish=crash
app.process_preview()
'''
        result=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True)
        assert result.returncode==73,result.stderr
        con=sqlite3.connect(root/'catalog.sqlite')
        assert con.execute('SELECT COUNT(*) FROM files').fetchone()[0]==1
        assert con.execute("SELECT COUNT(*) FROM work_queue WHERE status='running'").fetchone()[0]==1
        original=Path(con.execute('SELECT path FROM files').fetchone()[0]);con.close()
        assert original.read_bytes()==font.read_bytes() and not incoming.exists()
        code='''import app,os,import_queue
with app.db() as con: import_queue.reset_running(con,'preview')
for path in (app.DATA/'previews').glob('*.queue.part'): path.unlink()
if os.environ['PREVIEW_CRASH']=='published':
 def fail(*args,**kwargs): raise AssertionError('Published preview was compressed again')
 app.TTFont.save=fail
assert app.process_preview()
with app.db() as con:
 assert con.execute("SELECT COUNT(*) FROM work_queue WHERE kind='preview' AND status='done'").fetchone()[0]==1
 assert con.execute("SELECT COUNT(*) FROM work_queue WHERE status='error'").fetchone()[0]==0
'''
        result=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True)
        assert result.returncode==0,result.stderr
        assert not list((root/'previews').glob('*.part'))
        print('PASS: preview recovery after',boundary,flush=True)
