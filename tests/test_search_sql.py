"""Compare SQL results with an independent Python reference on a small corpus.

Exercise Unicode substring semantics, short queries, punctuation, per-face
filtering, family aggregation, source separation and all pagination orders.
"""

# Direct execution from the repository root keeps application imports available.
import sys as _test_sys
from pathlib import Path as _TestPath
_test_sys.path.insert(0,str(_TestPath(__file__).resolve().parents[1]))
import json,os,tempfile,itertools

def reference(faces,query):
    terms=query.get('q','').casefold().split();required={ord(c) for c in query.get('text','') if not c.isspace()} if query.get('coverage')=='1' else set()
    selected=[]
    for face in faces:
        haystack=(' '.join(str(face.get(k,'')) for k in ['family','style','author','filename','version','format'])+' '+' '.join(face['features'])).casefold()
        source=query.get('source','')
        if source and not (face['source']!='local' if source=='internet' else face['source']==source): continue
        if not all(term in haystack for term in terms): continue
        if query.get('language') and query['language'] not in face['languages']: continue
        if query.get('type') and bool(face['axes'])!=(query['type']=='variable'): continue
        if query.get('format') and query['format']!=face['format']: continue
        if not required<=set(face['chars']): continue
        selected.append(face)
    selected.sort(key=lambda face:(face['family'].casefold(),face['weight'],face['style'],face['format'],face['id']))
    groups={}
    for face in selected: groups.setdefault((face['family'],face['author'],face['source']),[]).append(face)
    results=[]
    for members in groups.values():
        result=dict(members[0]);result['added_at']=max(face['added_at'] or '' for face in members)
        result['formats']=sorted({face['format'] for face in members});result['count']=len(members)
        result['style_count']=len({(face['style'],face['weight'],json.dumps(face['axes'],sort_keys=True)) for face in members})
        results.append(result)
    results.sort(key=lambda face:face['family'].casefold(),reverse=query.get('sort')=='name_desc')
    if query.get('sort') in {'newest','oldest'}:
        known=[row for row in results if row['added_at']];unknown=[row for row in results if not row['added_at']]
        results=sorted(known,key=lambda row:row['added_at'],reverse=query['sort']=='newest')+unknown
    pages=(len(results)+9)//10;page=min(max(1,int(query.get('page',1))),max(1,pages))
    return dict(total=len(results),pages=pages,page=page,faces=results[(page-1)*10:page*10])

def check():
    with tempfile.TemporaryDirectory() as folder:
        os.environ['FONTHUB_DATA']=folder
        import app
        with app.db() as con:
            for n in range(96):
                digest=f'{n:064x}';name=['Straße','Кириллица','O"Brien','A-B'][n//4%4]+f' {n//4:02}'
                info=dict(id=digest+'-0',family=name,author='Author',style=['Regular','Medium','Bold','Black'][n%4],weight=[400,500,700,900][n%4],format='TTF' if n%2 else 'OTF',axes=[] if n%3 else [{'tag':'wght','min':100,'max':900}],features=['liga'],languages=['English'] if n%2 else ['Русский'],chars=[65,66,67] if n%2 else [65,67,0x1f984],preview_available=False,filename='Test.ttf',version='1',coverage_complete=True)
                con.execute('INSERT INTO files(hash,name,path) VALUES(?,?,?)',(digest,'Test.ttf','unused'))
                con.execute('INSERT INTO faces VALUES(?,?,?)',(info['id'],digest,json.dumps(info)))
                con.execute('INSERT INTO origins VALUES(?,?,?)',(digest,'local' if n//4%2 else 'google-fonts',f'2026-01-{n//4+1:02} 00:00:00'))
            faces=app.origin_faces(con)
        client=app.app.test_client()
        cases=[{}]+[{key:value} for key,values in {'q':['STRASSE','рил','O"B','A-B','a','"','Test.ttf','лиг','Regular Author'],'language':['English','Русский'],'type':['static','variable'],'source':['local','internet','google-fonts'],'format':['TTF','OTF'],'sort':['name_desc','oldest','newest'],'page':['2','999']}.items() for value in values]
        cases += [{'coverage':'1','text':text} for text in ['ABC','AC','🦄','B🦄','']]
        cases += [dict(q=q,source=source,type=kind,sort=sort,page='2') for q,source,kind,sort in itertools.product(['','a'],['local','internet'],['static','variable'],['name','name_desc'])]
        for query in cases:
            actual=client.get('/api/search',query_string=query).json;expected=reference(faces,query)
            assert actual is not None,query
            for key in ['total','pages','page']: assert actual[key]==expected[key],(query,key,actual[key],expected[key])
            for actual_face,expected_face in zip(actual['faces'],expected['faces']):
                for key in ['id','family','formats','count','style_count','added_at']: assert actual_face[key]==expected_face[key],(query,key,actual_face,expected_face)
        # Projection maintenance is transactional, including deletion and updates.
        with app.db() as con:
            value=dict(json.loads(con.execute('SELECT metadata FROM faces LIMIT 1').fetchone()[0]),family='Renamed')
            id=con.execute('SELECT id FROM faces LIMIT 1').fetchone()[0]
            con.execute('UPDATE faces SET metadata=? WHERE id=?',(json.dumps(value),id))
        assert client.get('/api/search?q=Renamed').json['total']==1
        with app.db() as con: con.execute('DELETE FROM faces WHERE id=?',(id,))
        assert client.get('/api/search?q=Renamed').json['total']==0
        # Simulate a restored projection made on SQLite without trigram FTS.
        # Enabling FTS later must index existing rows, not only new imports.
        with app.db() as con:
            con.execute('DROP TRIGGER search_text_insert');con.execute('DROP TRIGGER search_text_delete')
            con.execute('DROP TABLE search_text');app.catalog_search.initialize(con)
        assert client.get('/api/search?q=STRASSE').json['total']==6
        print('PASS:',len(cases),'SQL/reference cases; Unicode/punctuation; aggregation; filters; pagination; update/delete triggers')

if __name__=='__main__': check()
