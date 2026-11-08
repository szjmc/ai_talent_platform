"""Static endpoint inventory from FastAPI router source (no app import / no DB)."""
from __future__ import annotations
import ast, json, argparse
from pathlib import Path
from common import classify, write_json, now

def const(node):
    try: return ast.literal_eval(node)
    except Exception: return ast.unparse(node)

def collect(app_dir: Path):
    out=[]
    for p in sorted((app_dir/"app/routers").glob("*.py")):
        if p.name == "_stub.py":
            continue
        tree=ast.parse(p.read_text(encoding="utf-8")); prefixes={}
        for n in ast.walk(tree):
            if isinstance(n,ast.Assign) and isinstance(n.value,ast.Call):
                f=n.value.func
                if (isinstance(f,ast.Name) and f.id=="APIRouter") or (isinstance(f,ast.Attribute) and f.attr=="APIRouter"):
                    prefix=""
                    for k in n.value.keywords:
                        if k.arg=="prefix":
                            try: prefix=ast.literal_eval(k.value)
                            except Exception: prefix=""
                    for t in n.targets:
                        if isinstance(t,ast.Name): prefixes[t.id]=prefix
        for n in ast.walk(tree):
            if not isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)): continue
            for d in n.decorator_list:
                if not (isinstance(d,ast.Call) and isinstance(d.func,ast.Attribute) and isinstance(d.func.value,ast.Name)): continue
                obj=d.func.value.id; method=d.func.attr.upper()
                if obj not in prefixes or method not in {"GET","POST","PUT","PATCH","DELETE","WEBSOCKET"} or not d.args: continue
                try: path=ast.literal_eval(d.args[0])
                except Exception: continue
                if not isinstance(path,str) or "{" in prefix: continue
                summary=""
                for k in d.keywords:
                    if k.arg=="summary": summary=const(k.value)
                full=("/api/v1"+prefixes[obj]+path).replace("//","/")
                params=sorted(set(__import__('re').findall(r'{([^}]+)}',full)))
                out.append(dict(method=method,path=full,summary=summary or n.name,handler=n.name,
                                source=str(p.relative_to(app_dir)),line=n.lineno,path_params=params,
                                safety=classify(method,full,summary or n.name)))
    out += [dict(method="GET",path="/health",summary="存活探针",handler="health",source="app/core/application.py",line=144,path_params=[],safety="read"),
            dict(method="GET",path="/health/ready",summary="就绪探针",handler="readiness",source="app/core/application.py",line=157,path_params=[],safety="read")]
    return sorted(out,key=lambda x:(x["path"],x["method"]))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--app-dir",default="ai_talent_platform"); ap.add_argument("--out",default="deliverables/perf/endpoints.json")
    a=ap.parse_args(); rows=collect(Path(a.app_dir))
    write_json(a.out,{"generated_at":now(),"app_dir":a.app_dir,"count":len(rows),"endpoints":rows})
    print(json.dumps({"count":len(rows),"out":a.out},ensure_ascii=False))

if __name__=="__main__": main()
