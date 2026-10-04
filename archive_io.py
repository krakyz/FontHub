"""Publish complete files before committing archive references in SQLite.

fsync makes the file durable before rename. POSIX also flushes the containing
directory. Windows does not expose that directory operation through os.open;
hardware/filesystem power-loss guarantees still apply, so keep backups.
"""
import os
import shutil
from contextlib import contextmanager

@contextmanager
def archive_lock(path):
    """Serialize scanner, API imports and recovery across OS processes.

    The OS releases advisory byte/file locks on process death; the persistent
    empty lock file is not a lease and must not be deleted as stale state.
    """
    with path.open('a+b') as stream:
        if os.name=='nt':
            import msvcrt,time
            stream.seek(0,os.SEEK_END)
            if stream.tell()==0: stream.write(b'0');stream.flush()
            stream.seek(0)
            while True:
                try: msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1);break
                except OSError: time.sleep(.1)
            try: yield
            finally: stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(stream,fcntl.LOCK_EX)
            try: yield
            finally: fcntl.flock(stream,fcntl.LOCK_UN)

def publish(temporary,target):
    with temporary.open('r+b') as stream: stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,target)
    if os.name!='nt':
        descriptor=os.open(target.parent,os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)

def copy_original(source,target,digest):
    import hashlib
    temporary=target.with_suffix('.tmp')
    shutil.copyfile(source,temporary)
    if hashlib.sha256(temporary.read_bytes()).hexdigest()!=digest:
        raise ValueError('Не совпала контрольная сумма копии')
    publish(temporary,target)
