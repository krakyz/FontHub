"""Shared acquisition: bounded requests, raw evidence and metadata-only Git.

Adapters must use this context, never their own network client or database.
Each successful response is retained before parsing, allowing offline replay.
"""
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

class Context:
    def __init__(self, data, source, job, hosts, repository=None):
        self.data, self.source, self.repository = Path(data), source, repository
        self.hosts, self.last = hosts, 0
        self.evidence = self.data / 'responses' / source / job
        self.evidence.mkdir(parents=True, exist_ok=True)

    def text(self, url):
        """Bound retries to transient errors; long Retry-After fails explicitly."""
        if urlparse(url).scheme != 'https' or urlparse(url).hostname not in self.hosts:
            raise ValueError('Adapter requested an unapproved metadata URL')
        for attempt in range(2):
            time.sleep(max(0, .3 - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            try:
                with urlopen(Request(url, headers={'User-Agent':'FontHub/1.0'}), timeout=15) as response:
                    if urlparse(response.url).hostname not in self.hosts:
                        raise ValueError('Unexpected metadata redirect')
                    body = response.read(16*1024*1024+1)
                    key = hashlib.sha256(url.encode()).hexdigest()
                    (self.evidence / (key+'.body')).write_bytes(body)
                    (self.evidence / (key+'.json')).write_text(json.dumps(dict(url=url, final_url=response.url, status=response.status, headers=dict(response.headers), acquired=time.time())), encoding='utf-8')
                    if len(body)>16*1024*1024: raise ValueError('Metadata exceeds 16 MiB')
                    if not body: raise ValueError('Source returned an empty document (possibly access protection)')
                    return body.decode('utf-8-sig')
            except HTTPError as error:
                key=hashlib.sha256(url.encode()).hexdigest()+'-error-'+str(attempt)
                (self.evidence/(key+'.body')).write_bytes(error.read(16*1024*1024))
                (self.evidence/(key+'.json')).write_text(json.dumps(dict(url=url,status=error.code,headers=dict(error.headers),acquired=time.time())),encoding='utf-8')
                delay = error.headers.get('Retry-After','1')
                if attempt or error.code not in {429,500,502,503,504} or not delay.isdigit() or int(delay)>5:
                    raise
                time.sleep(max(1,int(delay)))

    def json(self, url):
        return json.loads(self.text(url))

    def git(self, *args):
        process=subprocess.run(['git','-C',str(self.repo),*args], capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180,env=dict(os.environ,GIT_TERMINAL_PROMPT='0'))
        if process.returncode: raise RuntimeError(process.stderr[-2000:] or 'Git failed')
        return process.stdout.strip()

    def checkout(self, repository, patterns, legacy=False, key=None, branch="main"):
        """Hydrate only adapter-declared metadata paths, pin an exact revision."""
        self.repo=self.data/'repository' if legacy else self.data/'repositories'/self.source/(key or 'catalogue')
        repository=self.repository or repository
        if not (self.repo/'.git').exists():
            self.repo.parent.mkdir(parents=True,exist_ok=True)
            result=subprocess.run(['git','clone','--filter=blob:none','--no-checkout','--depth','1',repository,str(self.repo)],capture_output=True,text=True,timeout=180,env=dict(os.environ,GIT_TERMINAL_PROMPT='0'))
            if result.returncode:raise RuntimeError(result.stderr[-2000:])
        else:self.git('fetch','--depth','1','origin',branch)
        revision=self.git('rev-parse','origin/main' if branch=='main' else 'FETCH_HEAD' if (self.repo/'.git'/'FETCH_HEAD').exists() else 'origin/HEAD')
        self.git('sparse-checkout','set','--no-cone',*patterns)
        self.git('checkout','--force','--detach',revision)
        # Retain original Git-hydrated metadata alongside HTTP evidence. The
        # working checkout may advance; these per-job observations do not.
        for relative in self.git('ls-files').splitlines():
            path=self.repo/relative
            if path.is_file() and not path.is_symlink():
                key=hashlib.sha256((repository+'/'+revision+'/'+relative).encode()).hexdigest()
                (self.evidence/(key+'.body')).write_bytes(path.read_bytes())
                (self.evidence/(key+'.json')).write_text(json.dumps(dict(repository=repository,revision=revision,path=relative,acquired=time.time())),encoding='utf-8')
        return self.repo,revision
