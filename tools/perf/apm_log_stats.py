"""APM-like statistics from application access logs or historical diagnose logs.

Supported formats:
  app       app.access: TS | LEVEL | trace_id | logger | METHOD PATH -> STATUS | 12.3ms | ip=...
  diagnose  [PASS] METHOD PATH HTTP STATUS ... (historical functional check; no latency)
  auto      detect per line

Outputs JSON/CSV/Markdown and optional Word. Does not invent latency when absent.
"""
from __future__ import annotations
import argparse, json, re
from pathlib import Path
from collections import defaultdict
from common import now, norm_path, stats, write_json, write_csv, md_table

APP_RE=re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*\|\s*(?P<level>[A-Z]+)\s*\|\s*(?P<trace>[^|]*?)\s*\|\s*(?P<logger>[^|]*?)\s*\|\s*(?P<method>[A-Z]+)\s+(?P<path>\S+)\s+->\s+(?P<status>\d{3})\s*\|\s*(?P<ms>[0-9.]+)ms\s*\|\s*ip=(?P<ip>.*)$")
DIAG_RE=re.compile(r"^\[(?P<result>PASS|FAIL|占位)\]\s+(?P<method>[A-Z]+)\s+(?P<path>\S+).*?HTTP\s+(?P<status>\d{3})")
UVICORN_RE=re.compile(r'(?P<ip>[\d.]+):\d+ - "(?P<method>[A-Z]+) (?P<path>\S+) HTTP/[\d.]+" (?P<status>\d{3})')

def parse_line(line, fmt):
    if fmt in {"auto","app"}:
        m=APP_RE.match(line.strip())
        if m:
            d=m.groupdict(); d["format"]="app"; d["latency_ms"]=float(d.pop("ms")); d["status"]=int(d["status"]); return d
    if fmt in {"auto","diagnose"}:
        m=DIAG_RE.match(line.strip())
        if m:
            d=m.groupdict(); d["format"]="diagnose"; d["status"]=int(d["status"]); d["latency_ms"]=None; d["trace"]=None; d["ts"]=None; return d
    if fmt in {"auto","uvicorn"}:
        m=UVICORN_RE.search(line)
        if m:
            d=m.groupdict(); d["format"]="uvicorn"; d["status"]=int(d["status"]); d["latency_ms"]=None; d["trace"]=None; d["ts"]=None; return d
    return None

def analyze(paths, fmt, slow_ms):
    records=[]; unparsed=0
    for p in paths:
        for line in Path(p).read_text(encoding="utf-8",errors="replace").splitlines():
            r=parse_line(line,fmt)
            if r: r["source"]=str(p); records.append(r)
            elif line.strip(): unparsed+=1
    groups=defaultdict(list)
    for r in records: groups[(r["method"],norm_path(r["path"]))].append(r)
    endpoints=[]
    for (method,path),rs in sorted(groups.items()):
        lat=[r["latency_ms"] for r in rs if r.get("latency_ms") is not None]
        statuses=defaultdict(int)
        for r in rs: statuses[str(r["status"])]+=1
        errors=sum(1 for r in rs if r["status"]>=400 or r.get("result")=="FAIL")
        slow=sorted([r for r in rs if r.get("latency_ms") is not None and r["latency_ms"]>=slow_ms],key=lambda x:-x["latency_ms"])[:10]
        endpoints.append({"method":method,"path":path,"count":len(rs),"errors":errors,"error_rate":round(errors/len(rs),4),
                          "latency_ms":stats(lat),"has_latency":bool(lat),"status_distribution":dict(statuses),
                          "formats":sorted({r["format"] for r in rs}),"slow_traces":[{k:r.get(k) for k in ["ts","trace","latency_ms","status","source"]} for r in slow]})
    minutes=defaultdict(lambda:{"count":0,"errors":0,"lat":[]})
    for r in records:
        if r.get("ts"):
            k=r["ts"][:16]; minutes[k]["count"]+=1; minutes[k]["errors"]+=1 if r["status"]>=400 else 0
            if r.get("latency_ms") is not None: minutes[k]["lat"].append(r["latency_ms"])
    ts=[{"minute":k,"count":v["count"],"errors":v["errors"],"latency_ms":stats(v["lat"])} for k,v in sorted(minutes.items())]
    all_lat=[r["latency_ms"] for r in records if r.get("latency_ms") is not None]
    return {"generated_at":now(),"sources":[str(p) for p in paths],"format":fmt,"records":len(records),"unparsed_lines":unparsed,
            "overall_latency_ms":stats(all_lat),"endpoints":endpoints,"time_series":ts,
            "note":"diagnose/uvicorn 无耗时字段时不计算延迟；历史日志不是本次压测证据。"}

def to_md(rep, slow_ms):
    lines=["# APM / 日志统计","",f"- 生成时间：{rep['generated_at']}",f"- 来源：{', '.join(rep['sources'])}",
           f"- 解析记录：{rep['records']}；未解析行：{rep['unparsed_lines']}",f"- 慢请求阈值：{slow_ms} ms","",
           "## 总体延迟", "```json", json.dumps(rep["overall_latency_ms"],ensure_ascii=False), "```","",
           "## 按接口", md_table(["方法","路径","次数","错误","错误率","P50","P95","P99","有耗时","格式"],[[e["method"],e["path"],e["count"],e["errors"],e["error_rate"],(e["latency_ms"] or {}).get("p50"),(e["latency_ms"] or {}).get("p95"),(e["latency_ms"] or {}).get("p99"),e["has_latency"],",".join(e["formats"])] for e in rep["endpoints"]]),"",
           "## 分钟级趋势", md_table(["分钟","次数","错误","P95"],[[t["minute"],t["count"],t["errors"],(t["latency_ms"] or {}).get("p95")] for t in rep["time_series"]]),"",
           "> "+rep["note"]]
    return "\n".join(lines)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("logs",nargs="+"); ap.add_argument("--format",choices=["auto","app","diagnose","uvicorn"],default="auto")
    ap.add_argument("--slow-ms",type=float,default=1000); ap.add_argument("--out",default="deliverables/perf/apm"); ap.add_argument("--docx",action="store_true")
    a=ap.parse_args(); rep=analyze(a.logs,a.format,a.slow_ms); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    write_json(out/"apm_stats.json",rep); write_csv(out/"endpoints.csv",[{k:e.get(k) for k in ["method","path","count","errors","error_rate","has_latency"]} | {f"latency_{k}":(e["latency_ms"] or {}).get(k) for k in ["avg","p50","p95","p99","max"]} for e in rep["endpoints"]])
    (out/"apm_stats.md").write_text(to_md(rep,a.slow_ms),encoding="utf-8")
    if a.docx:
        from report_docx import _doc,_table
        d=_doc("APM / 日志统计报告"); d.add_paragraph(f"来源：{', '.join(rep['sources'])}；记录：{rep['records']}")
        d.add_paragraph(rep["note"]); _table(d,["方法","路径","次数","错误","错误率","P95","有耗时"],[[e["method"],e["path"],e["count"],e["errors"],e["error_rate"],(e["latency_ms"] or {}).get("p95"),e["has_latency"]] for e in rep["endpoints"]])
        d.save(out/"APM日志统计.docx")
    print(json.dumps({"records":rep["records"],"endpoints":len(rep["endpoints"]),"out":str(out)},ensure_ascii=False))

if __name__=="__main__": main()
