"""Automatic recovery gate: real rejection, lossless publication and review."""

# Direct execution from the repository root keeps application imports available.
import sys as _test_sys
from pathlib import Path as _TestPath
_test_sys.path.insert(0,str(_TestPath(__file__).resolve().parents[1]))
import os,tempfile,struct,json
from pathlib import Path
from fontTools.ttLib import TTFont
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.t2CharStringPen import T2CharStringPen
from types import MethodType
with tempfile.TemporaryDirectory() as folder:
 os.environ['FONTHUB_DATA']=folder
 import app,original_checks,font_repairs
 client=app.app.test_client()
 builder=FontBuilder(1000,isTTF=False);builder.setupGlyphOrder(['.notdef','A']);builder.setupCharacterMap({65:'A'})
 outlines={}
 for name in ['.notdef','A']:
  pen=T2CharStringPen(600,None);pen.moveTo((0,0));pen.lineTo((200,700));pen.lineTo((400,0));pen.closePath();outlines[name]=pen.getCharString()
 builder.setupCFF('ChecksCFF',{'FullName':'Checks CFF','FamilyName':'Checks CFF','Weight':'Regular'},outlines,{})
 builder.setupHorizontalMetrics({g:(600,0) for g in outlines});builder.setupHorizontalHeader(ascent=800,descent=-200)
 builder.setupNameTable({'familyName':'Checks CFF','styleName':'Regular','uniqueFontIdentifier':'ChecksCFF','fullName':'Checks CFF','psName':'ChecksCFF','version':'Version 1.000'})
 builder.setupOS2(sTypoAscender=800,sTypoDescender=-200,usWinAscent=800,usWinDescent=200);builder.setupPost()
 otf=Path(folder)/'fixture.otf';builder.save(otf)

 # Invalid format-4 searchRange is readable, but OTS refuses its structure.
 damaged=app.INBOX/'range.otf'
 with TTFont(otf) as font:
  table=font['cmap'];raw=bytearray(table.compile(font))
  for i in range(struct.unpack_from('>H',raw,2)[0]):
   offset=struct.unpack_from('>I',raw,4+i*8+4)[0]
   if struct.unpack_from('>H',raw,offset)[0]==4:struct.pack_into('>H',raw,offset+8,0)
  table.compile=MethodType(lambda self,font:bytes(raw),table);font.save(damaged)
 assert original_checks.inspect(damaged)[0]=='rejected'
 digest=app.pipeline.accept(damaged);before=client.get('/originals/'+digest).data
 assert original_checks.process(app.db,app.DATA)
 with app.db() as con:
  assert con.execute('SELECT status FROM repair_auto_origins WHERE hash=?',(digest,)).fetchone()[0]=='pending'
  assert con.execute("SELECT COUNT(*) FROM faces WHERE hash=?",(digest,)).fetchone()[0]==0
 assert client.get('/issues').status_code==200
 assert font_repairs.process(app.db,app.DATA,app.pipeline)
 report=client.get('/api/repairs/'+digest).json['report']
 with app.db() as con:
  row=con.execute('SELECT * FROM repair_auto_origins WHERE hash=?',(digest,)).fetchone()
  assert row['status']=='published',dict(row)
  candidate=row['candidate_hash'];assert candidate!=digest
 assert client.get('/originals/'+digest).data==before
 while original_checks.process(app.db,app.DATA):pass
 while app.process_analysis():pass
 assert client.get('/api/catalog').status_code==200
 with app.db() as con:
  assert con.execute('SELECT COUNT(*) FROM faces WHERE hash=?',(candidate,)).fetchone()[0]==1
  assert con.execute('SELECT COUNT(*) FROM faces WHERE hash=?',(digest,)).fetchone()[0]==0
 assert 'Исправлен при импорте' in client.get('/fonts/'+candidate+'-0~local').get_data(as_text=True)
 # Lost mapping or changed critical table must never auto-publish.
 from copy import deepcopy
 changed=deepcopy(report);changed['faces'][0]['original']['complete']=False
 assert font_repairs.safety_reason(changed)
 changed=deepcopy(report);changed['faces'][0]['candidate']['table_hashes']['GDEF']='changed'
 assert font_repairs.safety_reason(changed)
 print('PASS: automatic lossless recovery, preserved original, normal gate and lineage; unsafe comparisons require review')

 # A partial readable cmap may be repaired, but its preservation is unproven.
 raw=struct.pack('>8H',4,34,0,4,4,1,0,66)+struct.pack('>9H',65535,0,65,65535,0,1,4,0,1)
 table=struct.pack('>HHHHI',0,1,3,1,12)+raw
 path=app.INBOX/'partial.ttf'
 with TTFont('C:/Windows/Fonts/arial.ttf') as font:
  font['cmap'].compile=MethodType(lambda self,font:table,font['cmap']);font.save(path)
 h=app.pipeline.accept(path)
 while original_checks.process(app.db,app.DATA):pass
 assert font_repairs.process(app.db,app.DATA,app.pipeline)
 with app.db() as con:
  assert con.execute('SELECT status FROM repair_auto_origins WHERE hash=?',(h,)).fetchone()[0]=='review'
  assert con.execute('SELECT COUNT(*) FROM repair_choices WHERE original_hash=?',(h,)).fetchone()[0]==0
 assert 'partial.ttf' in client.get('/issues').get_data(as_text=True)
 # A failed repair reveals the issue and cannot publish anything.
 from unittest.mock import patch
 with app.db() as con:
  con.execute("DELETE FROM repair_candidates WHERE hash=?",(h,))
  con.execute('DELETE FROM repair_auto_origins WHERE hash=?',(h,))
  font_repairs.schedule_auto(con,h)
 with patch.object(font_repairs.font_validation,'check',side_effect=font_repairs.font_validation.ValidationError('GDEF fixture refusal')):
  assert font_repairs.process(app.db,app.DATA,app.pipeline)
 with app.db() as con:
  assert con.execute('SELECT status FROM repair_candidates WHERE hash=?',(h,)).fetchone()[0]=='error'
  assert con.execute('SELECT status FROM repair_auto_origins WHERE hash=?',(h,)).fetchone()[0]=='review'
 assert 'partial.ttf' in client.get('/issues').get_data(as_text=True)
 print('PASS: incomplete comparison and failed GDEF repair stay in quarantine without automatic publication')
