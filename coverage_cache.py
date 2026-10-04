"""Share complete coverage reports between faces, workers and restarts.

Keyed by exact codepoint set and explicit analysis/database version. The report
contains percentages, missing required letters, scripts and auxiliary coverage;
no data needed by search is deferred. A version bump invalidates old entries.
"""
import hashlib,json,struct
from language_coverage import analyze_languages
VERSION='hyperglot-0.8.1-primary-preliminary-report-1'

def initialize(con):
 con.execute('CREATE TABLE IF NOT EXISTS coverage_reports(key TEXT PRIMARY KEY,version TEXT NOT NULL,report TEXT NOT NULL)')

def report(con,chars,store=False):
 chars=tuple(sorted(set(chars)))
 key=hashlib.sha256(VERSION.encode()+b''.join(struct.pack('>I',c) for c in chars)).hexdigest()
 row=con.execute('SELECT report FROM coverage_reports WHERE key=?',(key,)).fetchone()
 if row:return json.loads(row[0])
 value=analyze_languages(chars)
 if store:con.execute('INSERT OR IGNORE INTO coverage_reports VALUES(?,?,?)',(key,VERSION,json.dumps(value,ensure_ascii=False)))
 return value
