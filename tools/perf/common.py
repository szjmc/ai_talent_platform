"""Shared helpers for performance scripts. No fabricated metrics."""
from __future__ import annotations
import csv, json, math, re
from pathlib import Path
from datetime import datetime, timezone

def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")

def pct(values, p):
    if not values: return None
    s=sorted(values); k=(len(s)-1)*p/100; f=math.floor(k); c=math.ceil(k)
    if f==c: return round(s[int(k)],3)
    return round(s[f]*(c-k)+s[c]*(k-f),3)

def stats(latencies):
    if not latencies: return {"count":0}
    return {"count":len(latencies),"min":round(min(latencies),3),"avg":round(sum(latencies)/len(latencies),3),
            "p50":pct(latencies,50),"p75":pct(latencies,75),"p90":pct(latencies,90),"p95":pct(latencies,95),
            "p99":pct(latencies,99),"max":round(max(latencies),3)}

def slug(method,path):
    s=re.sub(r'[^A-Za-z0-9]+','_',method+"_"+path).strip('_').lower()
    return s[:120] or "endpoint"

def norm_path(path):
    path=re.sub(r'/\d+(?=/|$)','/{id}',path)
    path=re.sub(r'/[0-9a-fA-F]{8}-[0-9a-fA-F-]{20,}(?=/|$)','/{uuid}',path)
    path=re.sub(r'/[0-9a-fA-F]{16,}(?=/|$)','/{hash}',path)
    return path

def write_json(path,obj):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")

def write_csv(path,rows,fields=None):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    if not rows:
        path.write_text("",encoding="utf-8-sig"); return
    fields=fields or list(rows[0].keys())
    with path.open("w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore"); w.writeheader(); w.writerows(rows)

def md_table(headers,rows):
    out=["| "+" | ".join(headers)+" |","|"+"|".join(["---"]*len(headers))+"|"]
    for r in rows: out.append("| "+" | ".join(str(x).replace("|","\\|").replace("\n"," ") for x in r)+" |")
    return "\n".join(out)

def classify(method,path,summary=""):
    m=method.upper(); p=path.lower(); s=summary.lower()
    if m=="WEBSOCKET": return "stream"
    if m in {"GET","HEAD","OPTIONS"}: return "read"
    if m=="DELETE": return "destructive"
    if any(x in p for x in ["/login","/register","/refresh","/logout","/change-password","/reset-password"]): return "auth"
    if any(x in p for x in ["/retrieve","/search","/nl2sql","/explain","/compare","/distribution","/trend","/summary"]): return "query"
    if any(x in p for x in ["/parse","/run","/scan","/report","/confirm","/review","/resolve","/upload","/stream","/chat","/tasks"]): return "side_effect"
    if m in {"POST","PUT","PATCH"}: return "write"
    return "write"

def allowed(safety, args):
    if safety in {"read"}: return True
    if safety=="query": return not args.read_only
    if safety in {"write","side_effect","auth"}: return args.include_writes
    if safety=="destructive": return args.include_destructive and args.confirm_destructive=="YES"
    if safety=="stream": return args.include_stream
    return False
