"""Kill real import processes at durable boundaries and replay their journals.

This verifies process interruption, not a hardware power-loss simulation.
Every crash uses an isolated archive; the user's inbox is never touched.
"""

# Direct execution from the repository root keeps application imports available.
import sys as _test_sys
from pathlib import Path as _TestPath
_test_sys.path.insert(0,str(_TestPath(__file__).resolve().parents[1]))
import os,subprocess,tempfile,shutil,sqlite3,hashlib
from pathlib import Path
python=str(Path('.venv/Scripts/python.exe').resolve())
font=Path('C:/Windows/Fonts/arial.ttf')
for boundary in ['copy','analysis','commit']:
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder);(root/'inbox').mkdir();incoming=root/'inbox'/'original.ttf';shutil.copyfile(font,incoming)
        env=dict(os.environ,FONTHUB_DATA=folder);env.pop('FONTHUB_INBOX',None)
        code='''import app,os
from pathlib import Path
source=app.INBOX/'original.ttf'
boundary=os.environ['CRASH_BOUNDARY']
if boundary=='copy':
 original=app.copy_original
 def crash(*args): original(*args);os._exit(73)
 app.copy_original=crash
elif boundary=='analysis':
 original=app.metadata
 def crash(*args): original(*args);os._exit(73)
 app.metadata=crash
else:
 original=Path.unlink
 def crash(path,*args,**kwargs):
  if path==source: os._exit(73)
  return original(path,*args,**kwargs)
 Path.unlink=crash
app.import_file(source)
'''
        result=subprocess.run([python,'-c',code],env=dict(env,CRASH_BOUNDARY=boundary),capture_output=True,text=True)
        assert result.returncode==73,result.stderr
        result=subprocess.run([python,'-c',"import app,import_queue,original_checks;app.recover_imports();\nwith app.db() as con:import_queue.reset_running(con,'analysis')\nwith app.db() as con:import_queue.reset_running(con,'ots')\nwhile original_checks.process(app.db,app.DATA):pass\nwhile app.process_analysis():pass"],env=env,capture_output=True,text=True)
        assert result.returncode==0,result.stderr
        con=sqlite3.connect(root/'catalog.sqlite')
        assert con.execute('SELECT COUNT(*) FROM files').fetchone()[0]==1
        assert con.execute('SELECT COUNT(*) FROM faces').fetchone()[0]==1
        assert con.execute('SELECT COUNT(*) FROM import_jobs').fetchone()[0]==0
        target=Path(con.execute('SELECT path FROM files').fetchone()[0]);con.close()
        assert hashlib.sha256(target.read_bytes()).digest()==hashlib.sha256(font.read_bytes()).digest()
        assert not incoming.exists() and not list((root/'previews').glob('*.part'))
        print('PASS: recovery after',boundary,flush=True)
