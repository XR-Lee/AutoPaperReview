from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,tempfile,os
P=Path(__file__).resolve().parent;W=P.parent
def read(p):return json.loads(p.read_text())
def dig(v):return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def hashfile(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,v):
    p.parent.mkdir(parents=True,exist_ok=True)
    if p.exists():
        if read(p)!=v:raise ValueError('immutable record differs: '+str(p))
        return
    with tempfile.NamedTemporaryFile(mode='w',dir=p.parent,delete=False) as f:
        json.dump(v,f,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False);f.write('\n');n=f.name
    os.replace(n,p)
def lock(name,**facts):
    p=P/(name+'.json');meta=read(p) if p.exists() else dict(utc=datetime.now(timezone.utc).isoformat(),facts=facts)
    if meta['facts']!=facts:raise ValueError(name+' changed after lock')
    put(p,meta)
def rows(p):return [json.loads(line) for line in p.open()]
