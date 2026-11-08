"""Generate per-endpoint load scenarios/fixtures skeleton. Unknown payloads stay blocked."""
from __future__ import annotations
import json, argparse
from pathlib import Path
from common import write_json, now, slug

KNOWN={
 ("POST","/api/v1/auth/login"): {"json":{"username":"{{username}}","password":"{{password}}","remember_me":False},"auth":False,"safety_override":"auth"},
 ("POST","/api/v1/knowledge/retrieve"): {"json":{"query":"{{knowledge_query}}","top_k":5,"mode":"hybrid"},"safety_override":"query"},
 ("POST","/api/v1/analytics/nl2sql"): {"json":{"question":"{{analytics_question}}","max_rows":50,"with_insight":False},"safety_override":"query"},
 ("POST","/api/v1/matchings/explain"): {"json":{"position_id":"{{position_id}}","talent_id":"{{talent_id}}"},"safety_override":"query"},
 ("POST","/api/v1/matchings/candidates/compare"): {"json":{"position_id":"{{position_id}}","talent_ids":["{{talent_id}}"]},"safety_override":"query"},
 ("POST","/api/v1/analytics/compare"): {"json":{"left":"{{snapshot_left}}","right":"{{snapshot_right}}"},"safety_override":"query"},
 ("GET","/api/v1/users"): {"query":{"page":1,"page_size":10}},
 ("GET","/api/v1/talents"): {"query":{"page":1,"page_size":10}},
 ("GET","/api/v1/positions"): {"query":{"page":1,"page_size":10}},
 ("GET","/api/v1/matchings"): {"query":{"page":1,"page_size":10}},
 ("GET","/api/v1/knowledge/docs"): {"query":{"page":1,"page_size":10}},
 ("GET","/api/v1/analytics/distribution"): {"query":{"type":"education"}},
 ("GET","/api/v1/analytics/trend"): {"query":{"start_month":"{{start_month}}","end_month":"{{end_month}}"}},
}

def build(endpoints):
    scenarios=[]
    for e in endpoints:
        key=(e["method"],e["path"]); sc={"id":slug(*key),"method":e["method"],"path":e["path"],"summary":e.get("summary",""),
            "safety":e.get("safety","write"),"auth":True,"path_params":{p:"{{"+p+"}}" for p in e.get("path_params",[])},
            "query":{},"headers":{},"json":None,"form":None,"files":None,"stream":e["method"]=="WEBSOCKET",
            "cleanup":None,"notes":""}
        if key in KNOWN:
            sc.update(KNOWN[key])
            if "safety_override" in sc: sc["safety"]=sc.pop("safety_override")
        if e.get("path_params") and not sc["path_params"]: sc["notes"]="需要路径参数夹具"
        if e["method"] in {"POST","PUT","PATCH"} and sc["json"] is None and sc["form"] is None and sc["files"] is None:
            sc["notes"]=(sc["notes"]+"；需要请求体夹具，默认阻塞").strip("；")
        scenarios.append(sc)
    return scenarios

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--endpoints",required=True); ap.add_argument("--out",default="deliverables/perf/scenarios.json")
    a=ap.parse_args(); data=json.loads(Path(a.endpoints).read_text(encoding="utf-8")); eps=data["endpoints"] if isinstance(data,dict) else data
    sc=build(eps); write_json(a.out,{"generated_at":now(),"count":len(sc),"scenarios":sc})
    fixtures={"username":"{{PERF_USERNAME}}","password":"{{PERF_PASSWORD}}","token":"{{PERF_TOKEN}}",
              "position_id":1,"talent_id":1,"organization_id":1,"user_id":1,"doc_id":1,"batch_id":"{{batch_id}}",
              "knowledge_query":"人才制度","analytics_question":"统计各部门人才数量","start_month":"2026-01","end_month":"2026-11",
              "snapshot_left":"{{snapshot_left}}","snapshot_right":"{{snapshot_right}}"}
    write_json(Path(a.out).with_name("fixtures.example.json"),fixtures)
    print(json.dumps({"scenarios":len(sc),"out":a.out},ensure_ascii=False))

if __name__=="__main__": main()
