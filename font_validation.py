"""Bounded native OTS primitive for original and browser-artifact checks.

Run the bundled native tool in a bounded subprocess. We use validation-only CLI
mode: OTS internally sanitizes/serializes for checking, but cannot overwrite the
original or our candidate. A pass is not a guarantee of shaping correctness or
compatibility with every browser's different OTS version.
"""
import subprocess,tempfile,time,os
import ots

class ValidationError(ValueError): pass

def check(path,index=None,destination=None):
 started=time.monotonic()
 # TemporaryFile bounds memory even for very verbose malformed-font diagnostics.
 with tempfile.TemporaryFile() as output:
  try:
   # CLI requires a destination before a collection index. Keep extracted
   # sanitized output private and delete it; never rewrite the archived bytes.
   with tempfile.TemporaryDirectory() as folder:
    command=[ots.OTS_SANITIZE,str(path)]
    if destination is not None:command.append(str(destination))
    if index is not None:
     if destination is None:command.append(os.path.join(folder,'face.ttf'))
     command.append(str(index))
    result=subprocess.run(command,stdout=output,stderr=subprocess.STDOUT,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
  except subprocess.TimeoutExpired as exc:raise ValidationError('OpenType Sanitizer: превышено время проверки (60 секунд)') from exc
  output.seek(0);message=output.read(8192).decode('utf-8','replace').strip()
 if result.returncode:raise ValidationError('OpenType Sanitizer отклонил файл: '+message)
 return dict(status='passed',engine='OpenType Sanitizer',version=ots.__version__,seconds=round(time.monotonic()-started,3),message=message)
