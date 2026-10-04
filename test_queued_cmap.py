"""Malformed format 4 remains readable in both asynchronous stages."""
import os,struct,tempfile,hashlib
from pathlib import Path
from types import MethodType
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

with tempfile.TemporaryDirectory() as directory:
    os.environ['FONTHUB_DATA']=directory
    import app
    builder=FontBuilder(1000,isTTF=True);builder.setupGlyphOrder(['.notdef','A','B'])
    builder.setupCharacterMap({65:'A',66:'B'})
    glyphs={}
    for name in ['.notdef','A','B']:
        pen=TTGlyphPen(None);pen.moveTo((0,0));pen.lineTo((200,0));pen.lineTo((100,500));pen.closePath();glyphs[name]=pen.glyph()
    builder.setupGlyf(glyphs);builder.setupHorizontalMetrics({name:(500,0) for name in glyphs});builder.setupHorizontalHeader(ascent=800,descent=-200)
    builder.setupNameTable(dict(familyName='Broken cmap fixture',styleName='Regular',uniqueFontIdentifier='Queue fixture',fullName='Broken cmap fixture',psName='BrokenCmapFixture'))
    builder.setupOS2(sTypoAscender=800,sTypoDescender=-200,usWinAscent=800,usWinDescent=200);builder.setupPost();builder.setupMaxp()
    # B points exactly beyond the single glyph-index entry. A remains valid.
    raw=struct.pack('>8H',4,34,0,4,4,1,0,66)+struct.pack('>9H',65535,0,65,65535,0,1,4,0,1)
    table=struct.pack('>HHHHI',0,1,3,1,12)+raw
    builder.font['cmap'].compile=MethodType(lambda self,font:table,builder.font['cmap'])
    path=app.INBOX/'fixture.ttf';builder.save(path);original=path.read_bytes()
    digest=app.pipeline.accept(path)
    client=app.app.test_client()
    client.post('/api/checks/'+digest+'/override',json={'source':'local','reason':'Explicitly test tolerant cmap analysis of this known fixture'})
    app.pipeline.inline(digest);face=app.app.test_client().get('/api/catalog').json['faces'][0]
    assert face['chars']==[65] and face['coverage_complete'] is False and not face['preview_available']
    assert app.process_preview()
    face=app.app.test_client().get('/api/catalog').json['faces'][0]
    assert face['preview_available'] and face['preview_status']=='ready'
    assert app.app.test_client().get('/download/'+face['id']).data==original
    print('PASS: tolerant cmap is reapplied by preview worker; original unchanged')
