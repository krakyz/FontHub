"""Offline cross-platform smoke test with an original, generated font fixture."""
import os,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
with tempfile.TemporaryDirectory() as folder:
 os.environ['FONTHUB_DATA']=folder
 import app,original_checks
 app.google_fonts.index=lambda _:dict(families=[],sources=[],status={})
 builder=FontBuilder(1000,isTTF=True)
 builder.setupGlyphOrder(['.notdef','space','A']);builder.setupCharacterMap({32:'space',65:'A'})
 glyphs={}
 for name in ['.notdef','space','A']:
  pen=TTGlyphPen(None)
  if name!='space':
   pen.moveTo((0,0));pen.lineTo((200,700));pen.lineTo((400,0));pen.closePath()
  glyphs[name]=pen.glyph()
 builder.setupGlyf(glyphs);builder.setupHorizontalMetrics({g:(600,0) for g in glyphs})
 builder.setupHorizontalHeader(ascent=800,descent=-200)
 builder.setupNameTable(dict(familyName='FontHub Fixture',styleName='Regular',uniqueFontIdentifier='FontHubFixture-Regular',fullName='FontHub Fixture Regular',psName='FontHubFixture-Regular',version='Version 1.000'))
 builder.setupOS2(sTypoAscender=800,sTypoDescender=-200,usWinAscent=800,usWinDescent=200);builder.setupPost();builder.setupMaxp()
 incoming=app.INBOX/'fixture.ttf';builder.save(incoming);original=incoming.read_bytes()
 digest=app.pipeline.accept(incoming)
 assert original_checks.process(app.db,app.DATA)
 assert app.process_analysis()
 client=app.app.test_client();catalog=client.get('/api/catalog').json
 assert len(catalog['faces'])==1
 face=catalog['faces'][0];assert face['family']=='FontHub Fixture'
 assert client.get('/download/'+face['id']).data==original
 while app.process_preview():pass
 assert client.get('/font/'+face['id']).data[:4]==b'wOF2'
 for path in ['/','/imports','/issues','/settings','/fonts/'+face['id'],'/api/search?q=Fixture']:
  assert client.get(path).status_code==200,path
 assert client.get('/api/search?q=Fixture').json['total']==1
 print('PASS: generated font, OTS admission, analysis, original download, WOFF2 preview and pages')
