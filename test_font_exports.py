"""Exercise genuine export bytes and durable controls in a disposable archive."""
import os,tempfile,shutil,json
from pathlib import Path
from unittest.mock import patch
from fontTools.ttLib import TTFont
with tempfile.TemporaryDirectory() as folder:
 os.environ['FONTHUB_DATA']=folder
 import app,font_exports,import_queue
 path=app.INBOX/'regular.ttf';shutil.copyfile('C:/Windows/Fonts/arial.ttf',path)
 digest=app.import_file(path);face=digest+'-0~local';client=app.app.test_client()
 original=Path(folder)/'originals';before={p.name:p.read_bytes() for p in original.iterdir()}
 rows=client.get('/api/fonts/'+face+'/exports').json['rows'];assert next(r for r in rows if r['id']=='otf')['status']=='unavailable'
 assert client.post('/api/fonts/'+face+'/exports/otf').status_code==422
 assert client.post('/api/fonts/'+face+'/exports/../../bad').status_code==404
 assert client.post('/api/imports/control',json={'kind':'convert','paused':True}).status_code==200
 assert client.post('/api/fonts/'+face+'/exports/woff').status_code==202
 assert client.post('/api/fonts/'+face+'/exports/woff').status_code==202
 assert not font_exports.process(app.db,app.DATA)
 with app.db() as con: assert con.execute("SELECT COUNT(*) FROM work_queue WHERE kind='convert'").fetchone()[0]==1
 client.post('/api/imports/control',json={'kind':'convert','paused':False})
 assert font_exports.process(app.db,app.DATA)
 result=client.get('/api/fonts/'+face+'/exports').json
 url=next(r for r in result['rows'] if r['id']=='woff')['url'];assert url
 import io
 with TTFont(io.BytesIO(client.get(url).data)) as font: assert font.flavor=='woff' and len(font.getBestCmap())>100
 assert {p.name:p.read_bytes() for p in original.iterdir()}==before
 assert client.post('/api/fonts/'+face+'/exports/woff').status_code==200
 # Recovery reuses atomically published bytes rather than compressing again.
 with app.db() as con: con.execute("UPDATE work_queue SET status='queued' WHERE kind='convert'")
 with patch.object(TTFont,'save',side_effect=AssertionError('must reuse')): assert font_exports.process(app.db,app.DATA)
 assert client.post('/api/fonts/'+face+'/exports/woff2').status_code==202
 with patch.object(TTFont,'save',side_effect=RuntimeError('test compression failure')): app.process_preview()
 with app.db() as con: job=con.execute("SELECT * FROM work_queue WHERE kind='preview' AND status='error'").fetchone();assert job
 assert client.post('/api/imports/'+str(job['id'])+'/retry').status_code==200
 assert app.process_preview()
 assert client.get('/download/'+face).data==next(iter(before.values()))
 assert client.get('/fonts/'+face).status_code==200
 # Collections export the selected face; the original stays a collection.
 from fontTools.ttLib import TTCollection
 collection=TTCollection();collection.fonts=[TTFont('C:/Windows/Fonts/arial.ttf'),TTFont('C:/Windows/Fonts/arialbd.ttf')]
 collection_path=app.INBOX/'family.ttc';collection.save(collection_path)
 for font in collection.fonts: font.close()
 collection.close();collection_hash=app.import_file(collection_path);selected=collection_hash+'-1~local'
 assert client.post('/api/fonts/'+selected+'/exports/ttf').status_code==202
 font_exports.process(app.db,app.DATA)
 exported=client.get('/download-export/'+selected+'/ttf')
 with TTFont(io.BytesIO(exported.data)) as font: assert font['OS/2'].usWeightClass==700
 exported.close()
 # A minimal CFF face proves OTF stays OTF inside WOFF and can be restored.
 from fontTools.fontBuilder import FontBuilder
 from fontTools.pens.t2CharStringPen import T2CharStringPen
 builder=FontBuilder(1000,isTTF=False);builder.setupGlyphOrder(['.notdef','A']);builder.setupCharacterMap({65:'A'})
 outlines={}
 for glyph in ['.notdef','A']:
  pen=T2CharStringPen(600,None);pen.moveTo((0,0));pen.lineTo((200,700));pen.lineTo((400,0));pen.closePath();outlines[glyph]=pen.getCharString()
 builder.setupCFF('TestCFF',{'FullName':'Test CFF','FamilyName':'Test CFF','Weight':'Regular'},outlines,{})
 builder.setupHorizontalMetrics({g:(600,0) for g in outlines});builder.setupHorizontalHeader(ascent=800,descent=-200)
 builder.setupNameTable({'familyName':'Test CFF','styleName':'Regular','uniqueFontIdentifier':'TestCFF','fullName':'Test CFF','psName':'TestCFF'})
 builder.setupOS2(sTypoAscender=800,sTypoDescender=-200,usWinAscent=800,usWinDescent=200);builder.setupPost()
 cff_path=app.INBOX/'cff.otf';builder.save(cff_path);cff_hash=app.import_file(cff_path);cff_id=cff_hash+'-0~local'
 assert client.post('/api/fonts/'+cff_id+'/exports/ttf').status_code==422
 client.post('/api/fonts/'+cff_id+'/exports/woff');font_exports.process(app.db,app.DATA)
 cff_download=client.get('/download-export/'+cff_id+'/woff')
 with TTFont(io.BytesIO(cff_download.data)) as font: assert 'CFF ' in font and font.sfntVersion=='OTTO'
 cff_download.close()
 # Export cache and the conversion pause survive portable backup/restore.
 import archive_backup,sqlite3
 client.post('/api/imports/control',json={'kind':'convert','paused':True})
 with tempfile.TemporaryDirectory() as backup_folder:
  zipped=Path(backup_folder)/'export.zip';restored=Path(backup_folder)/'restored'
  archive_backup.backup(app.DATA,zipped);archive_backup.restore(zipped,restored)
  assert len(list((restored/'exports').glob('*.woff')))>=2
  with sqlite3.connect(restored/'catalog.sqlite') as con:
   assert con.execute("SELECT paused FROM queue_control WHERE kind='convert'").fetchone()[0]==1
   assert all(str(restored) in r[0] for r in con.execute("SELECT input_path FROM work_queue WHERE kind='convert'"))
  con.close()
 print('PASS: native formats, original preservation, durable pause, deduplication, export download, published recovery, failure and retry')
