"""Persistence and bounded public transport, adapted from the (1) collector."""
from __future__ import annotations
import csv, hashlib, io, json, os, sys, tempfile, time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
SKILL_DIR = Path(__file__).resolve().parent.parent
CST = timezone(timedelta(hours=8))
if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf8")
def now(): return datetime.now(CST).isoformat(timespec="seconds")
def sha256_bytes(b): return hashlib.sha256(b).hexdigest()
def short_hash(text,n=20): return sha256_bytes(text.encode("utf8"))[:n]
def digest_file(p): return sha256_bytes(Path(p).read_bytes())
def json_bytes(value): return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+"\n").encode("utf8")
def read_json(p): return json.loads(Path(p).read_text("utf-8-sig"))
CONFIG = read_json(SKILL_DIR/"assets/config.json")
def atomic_bytes(p,raw):
    p=Path(p); p.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix="."+p.name,suffix=".tmp",dir=p.parent)
    try:
        with os.fdopen(fd,"wb") as f: f.write(raw); f.flush(); os.fsync(f.fileno())
        # Windows readers/indexers can briefly hold the destination without delete sharing.
        # Retry only that bounded transient case; never truncate the old checkpoint.
        for attempt in range(6):
            try:
                os.replace(tmp,p);break
            except PermissionError as e:
                if getattr(e,'winerror',None) not in (5,32,33) or attempt==5:raise
                time.sleep(.05*(attempt+1))
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
def write_json(p,value): atomic_bytes(p,json_bytes(value))
def read_csv(p):
    with open(p,encoding="utf-8-sig",newline="") as f: return list(csv.DictReader(f))
def write_csv(p,rows,fields):
    buf=io.StringIO(newline=""); w=csv.DictWriter(buf,fieldnames=fields,extrasaction="raise")
    w.writeheader(); w.writerows(rows); atomic_bytes(p,buf.getvalue().encode("utf-8-sig"))
def write_rows(p,rows,fields=None):
    write_csv(p,rows,fields or list(dict.fromkeys(k for r in rows for k in r)) or ["状态"])
def log(data,event,**kw):
    p=Path(data)/"logs/采集日志.jsonl"; p.parent.mkdir(parents=True,exist_ok=True)
    with p.open("a",encoding="utf8") as f:
        f.write(json.dumps({"time":now(),"event":event,**kw},ensure_ascii=False)+"\n"); f.flush(); os.fsync(f.fileno())
def task_path(data,relative):
    root=Path(data).resolve(); p=(root/relative).resolve()
    if not p.is_relative_to(root): raise ValueError("path outside task directory")
    return p
@contextmanager
def task_lock(data):
    Path(data).mkdir(parents=True,exist_ok=True)
    with (Path(data)/".run.lock").open("a+b") as f:
        f.seek(0); f.write(b"0"); f.flush(); f.seek(0)
        try:
            if os.name=="nt":
                import msvcrt; msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl; fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as e: raise RuntimeError("task already running") from e
        try: yield
        finally:
            f.seek(0)
            if os.name=="nt": msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else: fcntl.flock(f.fileno(),fcntl.LOCK_UN)
class AccessBlocked(RuntimeError): pass
class InvalidResponse(RuntimeError): pass
class IntegrityError(RuntimeError): pass
class Http:
    def __init__(self,cfg=None):
        c=(cfg or CONFIG)["http"]; self.s=requests.Session(); self.s.trust_env=c["use_system_proxy"]
        self.s.headers["User-Agent"]=c["user_agent"]; self.cfg=c; self.last=0.; self.observer=None
    def request(self,method,url,**kw):
        for attempt in range(self.cfg["retries"]):
            delay=self.cfg["min_interval_seconds"]-(time.monotonic()-self.last)
            if delay>0: time.sleep(delay)
            started=now(); self.last=time.monotonic()
            try:
                r=self.s.request(method,url,timeout=self.cfg["timeout_seconds"],allow_redirects=False,**kw)
                if self.observer: self.observer(r,started,attempt)
                if r.status_code in (401,403,429,430): raise AccessBlocked(f"HTTP {r.status_code}")
                if r.status_code>=500:
                    if attempt+1==self.cfg["retries"]: raise InvalidResponse(f"HTTP {r.status_code}")
                elif r.status_code!=200: raise InvalidResponse(f"HTTP {r.status_code}")
                else: return r
            except requests.RequestException as e:
                if attempt+1==self.cfg["retries"]: raise InvalidResponse(type(e).__name__) from e
            time.sleep(min(2**attempt,8))
        raise InvalidResponse("retry exhausted")
