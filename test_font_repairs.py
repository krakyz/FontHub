"""Real repair output, refused candidates, selection and portable lineage."""
import os,tempfile,shutil,sqlite3
from pathlib import Path
from unittest.mock import patch
with tempfile.TemporaryDirectory() as folder:
 os.environ['FONTHUB_DATA']=folder
 import app,font_repairs,font_validation,archive_backup
 client=app.app.test_client();path=app.INBOX/'arial.ttf';shutil.copyfile('C:/Windows/Fonts/arial.ttf',path)
 digest=app.import_file(path);before=client.get('/originals/'+digest).data
 assert client.post('/api/repairs/'+digest).status_code==202
 assert client.post('/api/repairs/'+digest+'/choose',json={'source':'local','choice':'candidate'}).status_code==409
 assert font_repairs.process(app.db,app.DATA)
 result=client.get('/api/repairs/'+digest).json;assert result['status']=='ready'
 assert result['report']['faces'][0]['removed_chars']==[]
 assert result['report']['validation']['status']=='passed'
 assert client.get('/repairs/'+digest+'/file').status_code==200
 assert client.post('/api/repairs/'+digest+'/choose',json={'source':'bad','choice':'original'}).status_code==404
 assert client.post('/api/repairs/'+digest+'/choose',json={'source':'local','choice':'candidate'}).status_code==200
 assert client.post('/api/repairs/'+digest+'/choose',json={'source':'local','choice':'original'}).status_code==200
 assert client.get('/originals/'+digest).data==before
 archive=Path(folder)/'backup.zip';restored=Path(folder)/'restored';archive_backup.backup(app.DATA,archive);archive_backup.restore(archive,restored)
 con=sqlite3.connect(restored/'catalog.sqlite')
 candidate=Path(con.execute('SELECT path FROM repair_candidates').fetchone()[0]);assert candidate.is_relative_to(restored) and candidate.is_file()
 assert con.execute('SELECT COUNT(*) FROM repair_choices').fetchone()[0]==2;con.close()
 # A failed sanitization cannot produce an accepted/downloadable candidate.
 with app.db() as con:
  con.execute("UPDATE repair_candidates SET status='error' WHERE hash=?",(digest,))
 client.post('/api/repairs/'+digest)
 with patch.object(font_validation,'check',side_effect=font_validation.ValidationError('fixture refusal')):assert font_repairs.process(app.db,app.DATA)
 assert client.get('/api/repairs/'+digest).json['status']=='error'
 assert client.get('/repairs/'+digest+'/file').status_code==404
 assert client.post('/api/repairs/'+digest+'/choose',json={'source':'local','choice':'candidate'}).status_code==409
 assert client.get('/originals/'+digest).data==before
 print('PASS: immutable original, checked comparison, explicit choices, source validation, candidate refusal and backup/restore')
 # Real malformed cmap: normal OTS refuses, tolerant reserialization produces
 # a new valid copy while keeping the limited comparison explicitly marked.
 import struct
 from types import MethodType
 from fontTools.ttLib import TTFont
 raw=struct.pack('>8H',4,34,0,4,4,1,0,66)+struct.pack('>9H',65535,0,65,65535,0,1,4,0,1)
 table=struct.pack('>HHHHI',0,1,3,1,12)+raw
 path=app.INBOX/'damaged.ttf'
 with TTFont('C:/Windows/Fonts/arial.ttf') as font:
  font['cmap'].compile=MethodType(lambda self,font:table,font['cmap']);font.save(path)
 damaged=app.pipeline.accept(path);original=client.get('/originals/'+damaged).data
 import original_checks
 while original_checks.process(app.db,app.DATA):pass
 response=client.post('/api/repairs/catalog');assert response.status_code==200
 assert client.get('/api/repairs/'+damaged).json['status']=='queued'
 assert '0.' in client.post('/api/repairs/catalog').json['message']
 assert font_repairs.process(app.db,app.DATA)
 report=client.get('/api/repairs/'+damaged).json
 assert report['status']=='ready'
 assert report['report']['faces'][0]['original']['complete'] is False
 assert report['report']['faces'][0]['candidate']['complete'] is True
 assert client.get('/originals/'+damaged).data==original
 print('PASS: real rejected cmap repair, repeat OTS and incomplete-original warning')
