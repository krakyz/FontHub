"""Repeatable synthetic archive benchmark. No real fonts or user data changed.

Four faces per family, mixed types, two sources, ASCII glyph coverage. Results
describe metadata search only; large CJK fonts and import parsing need separate
measurements. Timings use medians; the first read is reported as cold latency.
"""
import argparse,json,os,statistics,tempfile,time,tracemalloc
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
parser=argparse.ArgumentParser();parser.add_argument('--sizes',nargs='+',type=int,default=[10000,30000,100000]);parser.add_argument('--output',required=True);args=parser.parse_args()
def resident_bytes():
    """Current process RSS includes Python, SQLite and loaded libraries.

    Seeding has already occurred, so retained allocator arenas are included;
    this is a process snapshot, not isolated incremental search memory.
    """
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in ['PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage']]
        counters=Counters();counters.cb=ctypes.sizeof(counters)
        ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(-1),ctypes.byref(counters),counters.cb)
        return counters.WorkingSetSize
    if os.path.exists('/proc/self/statm'):
        return int(open('/proc/self/statm').read().split()[1])*os.sysconf('SC_PAGE_SIZE')
    return None

results=[]
with tempfile.TemporaryDirectory() as folder:
    os.environ['FONTHUB_DATA']=folder
    import app
    client=app.app.test_client()
    for size in args.sizes:
        with app.db() as con:
            con.execute('DELETE FROM origins');con.execute('DELETE FROM faces');con.execute('DELETE FROM files')
            faces=[];files=[];origins=[]
            for n in range(size):
                family=n//4;digest=f'{n:064x}';source='local' if family%2 else 'google-fonts'
                info=dict(family=f'Family {family:06}',author='Synthetic',style=['Regular','Medium','Bold','Black'][n%4],weight=[400,500,700,900][n%4],format='TTF',axes=[] if family%3 else [{'tag':'wght','min':100,'max':900}],features=['liga'],languages=['English'],chars=list(range(32,127)),preview_available=False,coverage_complete=True,filename='synthetic.ttf',version='1.0',glyphs=95,warnings=[],license='',copyright='',size=1000)
                faces.append((digest+'-0',digest,json.dumps(info)))
                files.append((digest,'synthetic.ttf','synthetic.ttf','2026-01-01 00:00:00',source));origins.append((digest,source,'2026-01-01 00:00:00'))
            con.executemany('INSERT INTO files(hash,name,path,added_at,source) VALUES(?,?,?,?,?)',files)
            con.executemany('INSERT INTO faces VALUES(?,?,?)',faces);con.executemany('INSERT INTO origins VALUES(?,?,?)',origins)
        del faces,files,origins
        entry={'faces':size,'families':(size+3)//4,'cases':{}}
        for case,query in [('all',''),('substring','q=0001'),('filtered','source=local&type=static'),('page','page=100&sort=name_desc'),('characters','coverage=1&text=abc')]:
            times=[]
            for repeat in range(4):
                started=time.perf_counter();response=client.get('/api/search?scope=archive&'+query);times.append(time.perf_counter()-started);assert response.status_code==200
            entry['cases'][case]={'first_seconds':round(times[0],4),'median_seconds':round(statistics.median(times[1:]),4),'total':response.json['total']}
        # Measure Python allocation separately so instrumentation does not distort timings.
        tracemalloc.start();client.get('/api/search?scope=archive');current,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
        entry['python_search_peak_bytes']=peak
        entry['process_resident_bytes']=resident_bytes()
        results.append(entry);print(json.dumps(entry),flush=True)
from pathlib import Path
Path(args.output).parent.mkdir(parents=True,exist_ok=True);Path(args.output).write_text(json.dumps(results,indent=2),encoding='utf-8')
