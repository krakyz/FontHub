"""Kill real original-validation workers; recover without changing bytes."""

# Direct execution from the repository root keeps application imports available.
import sys as _test_sys
from pathlib import Path as _TestPath
_test_sys.path.insert(0,str(_TestPath(__file__).resolve().parents[1]))
import os,subprocess,tempfile,shutil,sqlite3,sys
from pathlib import Path
with tempfile.TemporaryDirectory() as folder:
 root=Path(folder);(root/'inbox').mkdir();original=Path('C:/Windows/Fonts/arial.ttf');shutil.copyfile(original,root/'inbox'/'fixture.ttf')
 env=dict(os.environ,FONTHUB_DATA=folder);env.pop('FONTHUB_INBOX',None)
 code="""import app,original_checks,os
app.pipeline.accept(app.INBOX/'fixture.ttf')
real=original_checks.inspect
def crash(*args):real(*args);os._exit(73)
original_checks.inspect=crash
original_checks.process(app.db,app.DATA)
"""
 result=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True);assert result.returncode==73,result.stderr
 code="""import app,original_checks,import_queue
with app.db() as con:import_queue.reset_running(con,'ots')
assert original_checks.process(app.db,app.DATA)
assert app.process_analysis()
"""
 result=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True);assert result.returncode==0,result.stderr
 with sqlite3.connect(root/'catalog.sqlite') as con:
  assert con.execute("SELECT status FROM font_checks").fetchone()[0]=='passed'
  assert con.execute('SELECT COUNT(*) FROM check_history').fetchone()[0]==1
  assert con.execute('SELECT COUNT(*) FROM faces').fetchone()[0]==1
  path=Path(con.execute('SELECT path FROM files').fetchone()[0])
 con.close()
 assert path.read_bytes()==original.read_bytes()
 print('PASS: interrupted native original check recovers once; exact original preserved')
