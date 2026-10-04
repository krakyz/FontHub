"""Indexed SQLite projection of immutable face metadata.

JSON remains the authoritative analysis. Triggers maintain cheap searchable
columns and language membership in the same transaction as imports/reanalysis.
Only ten selected families load their full JSON; character checks are a separate
candidate stage. Unicode casefold preserves the previous substring semantics.
"""
import json
import struct
from bisect import bisect_right
import coverage_cache

def register(con):
    con.create_function('unicode_fold',1,lambda value:(value or '').casefold(),deterministic=True)
    con.create_function('search_haystack',1,lambda value:(' '.join(str(json.loads(value).get(k,'')) for k in ['family','style','author','filename','version','format'])+' '+' '.join(json.loads(value).get('features',[]))).casefold(),deterministic=True)
    con.create_function('style_signature',1,lambda value:json.dumps([json.loads(value)['style'],json.loads(value)['weight'],json.loads(value)['axes']],sort_keys=True),deterministic=True)
    def weight(value):
        face=json.loads(value);axis=next((item for item in face['axes'] if item['tag']=='wght'),None)
        return f"{axis['min']:g}–{axis['max']:g}" if axis else str(face['weight'])
    con.create_function('search_weight',1,weight,deterministic=True)
    def ranges(value):
        chars=sorted(set(json.loads(value)['chars']));pairs=[]
        for char in chars:
            if pairs and char==pairs[-1][1]+1: pairs[-1][1]=char
            else: pairs.append([char,char])
        return b''.join(struct.pack('>II',start,end) for start,end in pairs)
    def order(value):
        face=json.loads(value)
        return f"{face['weight']:06d}!"+face['style'].encode('utf-8').hex()+'!'+face['format'].encode('utf-8').hex()+'!'+face['id']
    con.create_function('char_ranges',1,ranges,deterministic=True)
    # id is stored in the faces table, not normally included in its JSON.
    con.create_function('face_order',2,lambda value,id:order(json.dumps(dict(json.loads(value),id=id))),deterministic=True)
    con.create_function('order_id',1,lambda value:value.rsplit('!',1)[-1],deterministic=True)

PROJECTION="""SELECT {row}.id,{row}.hash,json_extract({row}.metadata,'$.family'),
json_extract({row}.metadata,'$.author'),unicode_fold(json_extract({row}.metadata,'$.family')),
json_extract({row}.metadata,'$.style'),json_extract({row}.metadata,'$.weight'),
json_extract({row}.metadata,'$.format'),json_array_length(json_extract({row}.metadata,'$.axes'))>0,
search_haystack({row}.metadata),style_signature({row}.metadata),search_weight({row}.metadata),char_ranges({row}.metadata),face_order({row}.metadata,{row}.id)"""

def initialize(con):
    coverage_cache.initialize(con)
    con.execute('CREATE TABLE IF NOT EXISTS held_origins(hash TEXT,source TEXT,PRIMARY KEY(hash,source))')
    con.executescript('''
    CREATE TABLE IF NOT EXISTS search_faces(id TEXT PRIMARY KEY,hash TEXT,family TEXT,author TEXT,family_sort TEXT,style TEXT,weight INTEGER,format TEXT,variable INTEGER,haystack TEXT,style_signature TEXT,weight_label TEXT,characters BLOB,face_order TEXT);
    CREATE INDEX IF NOT EXISTS search_family ON search_faces(family_sort,hash);
    CREATE INDEX IF NOT EXISTS search_family_members ON search_faces(family,author,hash);
    CREATE INDEX IF NOT EXISTS search_face_hash ON search_faces(hash);
    CREATE INDEX IF NOT EXISTS search_format_type ON search_faces(format,variable);
    CREATE INDEX IF NOT EXISTS search_type ON search_faces(variable);
    CREATE INDEX IF NOT EXISTS origin_source ON origins(source,hash);
    CREATE TABLE IF NOT EXISTS search_languages(face_id TEXT,language TEXT,PRIMARY KEY(face_id,language));
    CREATE INDEX IF NOT EXISTS search_language ON search_languages(language,face_id);
    CREATE TABLE IF NOT EXISTS search_schema(version INTEGER);
    ''')
    columns={row['name'] for row in con.execute('PRAGMA table_info(search_faces)')}
    for name,type in [('characters','BLOB'),('face_order','TEXT')]:
        if name not in columns: con.execute(f'ALTER TABLE search_faces ADD COLUMN {name} {type}')
    con.execute('CREATE INDEX IF NOT EXISTS search_group_order ON search_faces(family,author,face_order,hash)')
    version=con.execute('SELECT version FROM search_schema').fetchone()
    migrate=not version or version[0]!=3
    if migrate:
        for operation in ['insert','update']: con.execute('DROP TRIGGER IF EXISTS face_search_'+operation)
    # Trigram FTS narrows substring candidates; instr verifies exact semantics.
    # Older SQLite builds keep a correct (slower) instr fallback.
    had_fts=bool(con.execute("SELECT 1 FROM sqlite_master WHERE name='search_text'").fetchone())
    try:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS search_text USING fts5(haystack,content='search_faces',content_rowid='rowid',tokenize='trigram case_sensitive 1')")
        fts=True
    except __import__('sqlite3').OperationalError: fts=False
    if fts:
        con.executescript('''
        CREATE TRIGGER IF NOT EXISTS search_text_insert AFTER INSERT ON search_faces BEGIN INSERT INTO search_text(rowid,haystack) VALUES(new.rowid,new.haystack); END;
        CREATE TRIGGER IF NOT EXISTS search_text_delete AFTER DELETE ON search_faces BEGIN INSERT INTO search_text(search_text,rowid,haystack) VALUES('delete',old.rowid,old.haystack); END;
        ''')
    for operation in ['INSERT','UPDATE']:
        name='face_search_'+operation.lower()
        con.execute(f'''CREATE TRIGGER IF NOT EXISTS {name} AFTER {operation} ON faces BEGIN
        DELETE FROM search_faces WHERE id=new.id;
        DELETE FROM search_languages WHERE face_id=new.id;
        INSERT INTO search_faces {PROJECTION.format(row='new')};
        INSERT OR IGNORE INTO search_languages SELECT new.id,value FROM json_each(new.metadata,'$.languages'); END''')
    con.execute('''CREATE TRIGGER IF NOT EXISTS face_search_delete AFTER DELETE ON faces BEGIN
    DELETE FROM search_faces WHERE id=old.id;DELETE FROM search_languages WHERE face_id=old.id; END''')
    if migrate:
        # All derived rows and migration marker commit together. Restarting a
        # terminated migration safely rebuilds it from authoritative JSON.
        con.execute('DELETE FROM search_faces');con.execute('DELETE FROM search_languages')
        con.execute('INSERT INTO search_faces '+PROJECTION.format(row='faces')+' FROM faces')
        con.execute("INSERT OR IGNORE INTO search_languages SELECT faces.id,value FROM faces,json_each(faces.metadata,'$.languages')")
        con.execute('DELETE FROM search_schema');con.execute('INSERT INTO search_schema VALUES(3)')
    if fts and not had_fts and not migrate:
        # A restored database may have been built by SQLite without FTS5.
        con.execute("INSERT INTO search_text(search_text) VALUES('rebuild')")

def prepare(con,query,language,kind,source,file_format,required,ots_status=''):
    conditions=[];parameters=[]
    conditions.append('NOT EXISTS(SELECT 1 FROM held_origins h WHERE h.hash=f.hash AND h.source=o.source)')
    if ots_status=='unchecked':conditions.append('NOT EXISTS(SELECT 1 FROM font_checks c WHERE c.hash=f.hash)')
    elif ots_status:
        conditions.append('f.hash IN (SELECT hash FROM font_checks WHERE status=?)');parameters.append(ots_status)
    if source:
        conditions.append("o.source<>'local'" if source=='internet' else 'o.source=?')
        if source!='internet': parameters.append(source)
    if file_format: conditions.append('f.format=?');parameters.append(file_format)
    if kind: conditions.append('f.variable=?');parameters.append(int(kind=='variable'))
    if language:
        conditions.append('f.id IN (SELECT face_id FROM search_languages WHERE language=?)');parameters.append(language)
    terms=query.casefold().split()
    fts=con.execute("SELECT 1 FROM sqlite_master WHERE name='search_text'").fetchone()
    long_terms=[term for term in terms if len(term)>=3]
    if fts and long_terms:
        conditions.append('f.rowid IN (SELECT rowid FROM search_text WHERE search_text MATCH ?)')
        parameters.append(' AND '.join('"'+term.replace('"','""')+'"' for term in long_terms))
    for term in terms: conditions.append('instr(f.haystack,?)>0');parameters.append(term)
    if required:
        def contains(blob):
            intervals=list(struct.iter_unpack('>II',blob));starts=[item[0] for item in intervals]
            for char in required:
                index=bisect_right(starts,char)-1
                if index<0 or intervals[index][1]<char: return 0
            return 1
        con.create_function('has_chars',1,contains)
        conditions.append('has_chars(f.characters)=1')
    where=' AND '.join(conditions) or '1'
    base=f'FROM search_faces f JOIN origins o ON o.hash=f.hash WHERE {where}'
    con.execute('''CREATE TEMP TABLE current_groups(face_id TEXT,family TEXT,author TEXT,source TEXT,family_sort TEXT,added_at TEXT,remote INTEGER,rank_weight INTEGER,rank_style TEXT,rank_format TEXT,metadata TEXT)''')
    con.execute(f'''INSERT INTO current_groups
    WITH grouped AS (SELECT f.family,f.author,o.source,f.family_sort,MAX(COALESCE(o.added_at,'')) AS added_at,MIN(f.face_order) AS first {base} GROUP BY f.family,f.author,o.source)
    SELECT f.id,g.family,g.author,g.source,g.family_sort,g.added_at,0,f.weight,f.style,f.format,NULL
    FROM grouped g JOIN search_faces f ON f.id=order_id(g.first)''',parameters)
    return base,parameters

def add_remote(con,record):
    # Match archive presence by source + family, preserving separate sources.
    con.execute('''INSERT INTO current_groups SELECT ?,?,?,?,?,?,1,0,'','',?
    WHERE NOT EXISTS(SELECT 1 FROM search_faces f JOIN origins o ON o.hash=f.hash WHERE o.source=? AND f.family_sort=?)''',
    (record['id'],record['family'],record['author'],record['source'],record['family'].casefold(),'',json.dumps(record),record['source'],record['family'].casefold()))

def page(con,base,parameters,page_number,sort):
    total=con.execute('SELECT COUNT(*) FROM current_groups').fetchone()[0];pages=(total+9)//10
    try: number=min(max(1,int(page_number)),max(1,pages))
    except (TypeError,ValueError): number=1
    order="family_sort DESC" if sort=='name_desc' else 'family_sort'
    if sort in {'newest','oldest'}: order="(added_at=''),added_at "+('DESC' if sort=='newest' else 'ASC')+',family_sort'
    selected=con.execute('SELECT * FROM current_groups ORDER BY '+order+',remote,rank_weight,rank_style,rank_format,face_id LIMIT 10 OFFSET ?',((number-1)*10,)).fetchall()
    results=[]
    for group in selected:
        if group['remote']: results.append(json.loads(group['metadata']));continue
        # A selected family usually has very few members. Force that narrow
        # lookup rather than letting source/type filters scan thousands of rows
        # again for each of the ten displayed families (notably before ANALYZE).
        members=base.replace('FROM search_faces f ','FROM search_faces f INDEXED BY search_family_members ')
        rows=con.execute('SELECT a.metadata,f.id,o.added_at '+members.replace(' WHERE ',' JOIN faces a ON a.id=f.id WHERE ',1)+' AND f.family=? AND f.author IS ? AND o.source=?',[*parameters,group['family'],group['author'],group['source']]).fetchall()
        faces=[dict(json.loads(row['metadata']),id=row['id']+'~'+group['source'],source=group['source'],added_at=row['added_at']) for row in rows]
        face=next(item for item in faces if item['id'].split('~')[0]==group['face_id'])
        result={key:value for key,value in face.items() if key not in {'chars','license','copyright'}}
        weights=set()
        for item in faces:
            axis=next((axis for axis in item['axes'] if axis['tag']=='wght'),None)
            weights.add(f"{axis['min']:g}–{axis['max']:g}" if axis else str(item['weight']))
        result.update(formats=sorted({item['format'] for item in faces}),weights=sorted(weights),languages=sorted({name for item in faces for name in item['languages']}),sources=[group['source']],count=len(faces),style_count=len({(item['style'],item['weight'],json.dumps(item['axes'],sort_keys=True)) for item in faces}),added_at=group['added_at'])
        reports=coverage_cache.report(con,face['chars'])['languages']
        result['coverage']={iso:next((row['percent'] for row in reports if row['iso']==iso),0) for iso in ['rus','eng']}
        result['coverage_missing']={iso:next((row['missing'] for row in reports if row['iso']==iso),[]) for iso in ['rus','eng']}
        result['coverage_complete']=face.get('coverage_complete',True)
        results.append(result)
    return dict(total=total,page=number,pages=pages,faces=results)
