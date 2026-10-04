"""Real format coverage plus gate, deduplication, resolution and policy tests."""
import os,tempfile,shutil,json
from pathlib import Path
from unittest.mock import patch
from fontTools.ttLib import TTFont,TTCollection
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.t2CharStringPen import T2CharStringPen

with tempfile.TemporaryDirectory() as folder:
 os.environ['FONTHUB_DATA']=folder
 import app,original_checks,import_queue,font_exports,font_validation
 client=app.app.test_client();regular=Path('C:/Windows/Fonts/arial.ttf')
 # Build both outline families and containers; never use the user's inbox.
 builder=FontBuilder(1000,isTTF=False);builder.setupGlyphOrder(['.notdef','A']);builder.setupCharacterMap({65:'A'})
 outlines={}
 for name in ['.notdef','A']:
  pen=T2CharStringPen(600,None);pen.moveTo((0,0));pen.lineTo((200,700));pen.lineTo((400,0));pen.closePath();outlines[name]=pen.getCharString()
 builder.setupCFF('ChecksCFF',{'FullName':'Checks CFF','FamilyName':'Checks CFF','Weight':'Regular'},outlines,{})
 builder.setupHorizontalMetrics({g:(600,0) for g in outlines});builder.setupHorizontalHeader(ascent=800,descent=-200)
 builder.setupNameTable({'familyName':'Checks CFF','styleName':'Regular','uniqueFontIdentifier':'ChecksCFF','fullName':'Checks CFF','psName':'ChecksCFF'})
 builder.setupOS2(sTypoAscender=800,sTypoDescender=-200,usWinAscent=800,usWinDescent=200);builder.setupPost()
 otf=Path(folder)/'fixture.otf';builder.save(otf)
 fixtures={'ttf':regular,'otf':otf}
 for flavor in ['woff','woff2']:
  path=Path(folder)/('fixture.'+flavor)
  with TTFont(regular) as font:font.flavor=flavor;font.save(path)
  fixtures[flavor]=path
 for extension,source in [('ttc',regular),('otc',otf)]:
  path=Path(folder)/('fixture.'+extension);collection=TTCollection();collection.fonts=[TTFont(source),TTFont(source)];collection.save(path);collection.close();fixtures[extension]=path
 for extension,path in fixtures.items():
  before=path.read_bytes();status,report=original_checks.inspect(path)
  assert status=='passed',(extension,status,report)
  assert path.read_bytes()==before
  if extension in {'ttc','otc'}:assert len(report['faces'])==2 and all(f['status']=='passed' for f in report['faces'])
 print('PASS: real OTS TTF/OTF/WOFF/WOFF2/TTC/OTC and all collection faces')

 path=app.INBOX/'a.ttf';shutil.copyfile(regular,path)
 with patch.object(app,'metadata',side_effect=AssertionError('must wait for OTS')):
  digest=app.pipeline.accept(path);assert not app.process_analysis()
 assert client.get('/originals/'+digest).data==regular.read_bytes()
 client.post('/api/imports/control',json={'kind':'ots','paused':True})
 assert not original_checks.process(app.db,app.DATA)
 client.post('/api/imports/control',json={'kind':'ots','paused':False})
 with patch.object(original_checks,'inspect',return_value=('rejected',{'message':'fixture rejection'})):
  assert original_checks.process(app.db,app.DATA)
 assert not app.process_analysis()
 assert client.get('/api/checks/'+digest).json['status']=='rejected'
 assert client.post('/api/checks/'+digest+'/override',json={'source':'local'}).status_code==400
 assert client.post('/api/checks/'+digest+'/override',json={'source':'local','reason':'Readable fixture; explicitly allow analysis'}).status_code==200
 assert app.process_analysis()
 assert client.get('/api/checks/'+digest).json['status']=='rejected'
 assert client.get('/api/search?scope=archive&ots=rejected').json['total']==1
 assert client.get('/api/search?scope=archive&ots=passed').json['total']==0
 assert client.get('/issues').status_code==200 and client.get('/issues?view=checks').status_code==200
 assert client.get('/issues?resolved=1').status_code==200
 assert client.get('/issues?view=checks&hash='+digest).status_code==200
 # Recheck keeps history; duplicates from another origin reuse physical results.
 client.post('/api/checks/'+digest+'/retry');assert original_checks.process(app.db,app.DATA)
 assert len(client.get('/api/checks/'+digest).json['history'])==2
 path=app.INBOX/'b.ttf';shutil.copyfile(regular,path);app.pipeline.accept(path,'google-fonts')
 with app.db() as con:assert con.execute("SELECT COUNT(*) FROM work_queue WHERE kind='ots' AND identity=?",(digest,)).fetchone()[0]==1
 # Warning mode continues; a blocking mode must guard queued derivative work.
 with app.db() as con:
  con.execute('DELETE FROM check_overrides')
  con.execute("UPDATE font_checks SET status='rejected'")
 assert app.process_preview()
 with app.db() as con:assert con.execute("SELECT status FROM work_queue WHERE kind='preview'").fetchone()[0]=='blocked'
 assert client.post('/api/sources/local/ots-policy',json={'mode':'warn'}).status_code==200
 assert app.process_preview()
 # Timeouts, unsupported formats and checksum failures never pass.
 with patch.object(font_validation,'check',side_effect=font_validation.ValidationError('60 секунд')):
  assert original_checks.inspect(regular)[0]=='error'
 # A new blocking origin cannot inherit a trusted origin's publication before
 # its own admission. The physical analysis/check remains shared.
 path=app.INBOX/'trusted-bold.ttf';shutil.copyfile('C:/Windows/Fonts/arialbd.ttf',path)
 bold_hash=app.pipeline.accept(path,'google-fonts');assert app.process_analysis()
 client.post('/api/sources/local/ots-policy',json={'mode':'block'})
 path=app.INBOX/'local-bold.ttf';shutil.copyfile('C:/Windows/Fonts/arialbd.ttf',path)
 app.pipeline.accept(path,'local')
 assert client.get('/fonts/'+bold_hash+'-0~local').status_code==404
 assert client.get('/fonts/'+bold_hash+'-0~google-fonts').status_code==200
 assert original_checks.process(app.db,app.DATA)
 assert client.get('/fonts/'+bold_hash+'-0~local').status_code==200
 # Decisions and reports survive portable backup/restore.
 import archive_backup,sqlite3
 archive=Path(folder)/'checks.zip';restored=Path(folder)/'restored'
 archive_backup.backup(app.DATA,archive);archive_backup.restore(archive,restored)
 with sqlite3.connect(restored/'catalog.sqlite') as con:
  assert con.execute('SELECT COUNT(*) FROM check_history').fetchone()[0]>=2
  assert con.execute("SELECT mode FROM source_policy WHERE source='local'").fetchone()[0]=='block'
  assert con.execute('SELECT COUNT(*) FROM admission_history').fetchone()[0]==1
 con.close()
 unknown=app.INBOX/'unknown.ttf';unknown.write_bytes(b'unknown')
 unknown_hash=app.pipeline.accept(unknown)
 assert original_checks.process(app.db,app.DATA)
 assert client.get('/api/checks/'+unknown_hash).json['status']=='unsupported'
 with app.db() as con:
  row=con.execute('SELECT * FROM files WHERE hash=?',(digest,)).fetchone();original_checks.enqueue(con,row,force=True)
 Path(row['path']).write_bytes(b'corruption')
 assert original_checks.process(app.db,app.DATA)
 assert client.get('/api/checks/'+digest).json['status']=='error'
 print('PASS: gate, pause, original preservation, rejection/override/history, source dedup, filter, guarded preview, warning policy, timeout and integrity failure')
