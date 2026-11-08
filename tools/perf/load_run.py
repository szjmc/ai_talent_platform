"""Per-endpoint HTTP load runner and report generator.

Modes:
  --plan       Generate per-endpoint execution plans/reports without traffic (default when no --base-url).
  --self-test  Validate runner math/IO against an in-process MockTransport (synthetic, not project evidence).
  --base-url   Run real traffic against an authorized test environment.

Safety: read-only by default; writes/destructive/stream require explicit flags.
"""
from __future__ import annotations
import argparse, asyncio, json, os, re, time, sys, shutil
from pathlib import Path
from datetime import datetime, timezone
import httpx
from common import now, slug, stats, write_json, write_csv, md_table, allowed, classify

UNRESOLVED=re.compile(r"\{\{[^}]+\}\}")

def subst(obj, fixtures):
    if isinstance(obj,str):
        def repl(m):
            key=m.group(0)[2:-2].strip()
            val=fixtures.get(key, os.environ.get(key))
            return str(val) if val is not None else m.group(0)
        return UNRESOLVED.sub(repl,obj)
    if isinstance(obj,list): return [subst(x,fixtures) for x in obj]
    if isinstance(obj,dict): return {k:subst(v,fixtures) for k,v in obj.items()}
    return obj

def unresolved(obj):
    if isinstance(obj,str): return bool(UNRESOLVED.search(obj))
    if isinstance(obj,list): return any(unresolved(x) for x in obj)
    if isinstance(obj,dict): return any(unresolved(v) for v in obj.values())
    return False

def business_ok(status, body):
    if not (200<=status<300): return False
    if isinstance(body,dict) and "code" in body:
        try: return int(body["code"]) in (0,200)
        except Exception: return False
    return True

async def login(client, base, fixtures):
    u=fixtures.get("username") or os.environ.get("PERF_USERNAME"); p=fixtures.get("password") or os.environ.get("PERF_PASSWORD")
    if not u or not p: return fixtures.get("token") or os.environ.get("PERF_TOKEN")
    r=await client.post(base+"/api/v1/auth/login",json={"username":u,"password":p,"remember_me":False})
    data=r.json() if r.headers.get("content-type","").startswith("application/json") else {}
    tok=(data.get("data") or {}).get("access_token") if isinstance(data,dict) else None
    if tok: fixtures["token"]=tok
    return tok

async def one_request(client, sc, base, fixtures, timeout):
    path=sc["path"]
    for k,v in (sc.get("path_params") or {}).items(): path=path.replace("{"+k+"}",str(v))
    url=base.rstrip("/")+path
    headers=dict(sc.get("headers") or {})
    if sc.get("auth",True):
        tok=fixtures.get("token") or os.environ.get("PERF_TOKEN")
        if tok: headers["Authorization"]="Bearer "+str(tok)
    kwargs={"params":sc.get("query") or None,"headers":headers,"timeout":timeout}
    if sc.get("json") is not None: kwargs["json"]=sc["json"]
    if sc.get("form") is not None: kwargs["data"]=sc["form"]
    start=time.perf_counter(); err=None; status=None; body=None; size=0; trace=None; proc=None; events=0; ttfb=None
    try:
        if sc.get("stream") and sc["method"]!="WEBSOCKET":
            async with client.stream(sc["method"],url,**kwargs) as r:
                status=r.status_code; trace=r.headers.get("X-Trace-Id"); proc=r.headers.get("X-Process-Time-Ms")
                ttfb=(time.perf_counter()-start)*1000
                async for _ in r.aiter_lines():
                    events+=1
                    if events>=int(fixtures.get("max_stream_events",50)): break
                size=events
        else:
            r=await client.request(sc["method"],url,**kwargs)
            status=r.status_code; trace=r.headers.get("X-Trace-Id"); proc=r.headers.get("X-Process-Time-Ms")
            size=len(r.content or b"")
            try: body=r.json()
            except Exception: body=None
    except Exception as e:
        err=f"{type(e).__name__}: {e}"
    lat=(time.perf_counter()-start)*1000
    ok=err is None and business_ok(status,body)
    return {"ts":datetime.now(timezone.utc).isoformat(timespec="milliseconds"),"latency_ms":round(lat,3),
            "status":status,"ok":ok,"error":err,"bytes":size,"trace_id":trace,"process_time_ms":proc,
            "stream_events":events,"ttfb_ms":round(ttfb,3) if ttfb is not None else None}

async def run_scenario(client, sc, base, fixtures, args):
    samples=[]; deadline=time.perf_counter()+args.duration; sem=asyncio.Semaphore(args.concurrency)
    interval=1.0/args.rps if args.rps>0 else 0; last=0.0; count=0
    async def worker():
        nonlocal count,last
        while True:
            if args.requests and count>=args.requests: return
            if not args.requests and time.perf_counter()>=deadline: return
            async with sem:
                if interval:
                    wait=last+interval-time.perf_counter()
                    if wait>0: await asyncio.sleep(wait)
                    last=time.perf_counter()
                if args.requests and count>=args.requests: return
                count+=1
                samples.append(await one_request(client,sc,base,fixtures,args.timeout))
    workers=[asyncio.create_task(worker()) for _ in range(max(1,args.concurrency))]
    await asyncio.gather(*workers)
    return samples

def summarize(sc, samples, mode, status, reason=""):
    lat=[s["latency_ms"] for s in samples if s.get("latency_ms") is not None]
    ok=sum(1 for s in samples if s.get("ok")); errs=len(samples)-ok
    statuses={}
    for s in samples: statuses[str(s.get("status"))]=statuses.get(str(s.get("status")),0)+1
    dur=(max((s["ts"] for s in samples),default=None),min((s["ts"] for s in samples),default=None))
    return {"id":sc["id"],"method":sc["method"],"path":sc["path"],"summary":sc.get("summary",""),"safety":sc.get("safety"),
            "mode":mode,"status":status,"reason":reason,"samples":len(samples),"ok":ok,"errors":errs,
            "error_rate":round(errs/len(samples),4) if samples else None,"latency_ms":stats(lat),
            "status_distribution":statuses,"slow_samples":sorted([s for s in samples if not s.get("ok") or (s.get("latency_ms") or 0)>=args_slow],key=lambda x:-(x.get("latency_ms") or 0))[:5],
            "evidence":"synthetic" if mode=="self_test" else ("live" if mode=="run" else "none")}
args_slow=1000

def endpoint_md(res, sc):
    lat=res.get("latency_ms") or {}
    lines=[f"# {res['method']} {res['path']}", "", f"- 摘要：{res.get('summary','')}", f"- 安全分类：{res.get('safety')}",
           f"- 模式：{res['mode']}；状态：{res['status']}；证据：{res['evidence']}", f"- 原因/阻塞：{res.get('reason') or '无'}", "",
           "## 结果", md_table(["样本","成功","错误","错误率","Avg ms","P50","P95","P99","Max"],[[res.get("samples",0),res.get("ok",0),res.get("errors",0),res.get("error_rate"),lat.get("avg"),lat.get("p50"),lat.get("p95"),lat.get("p99"),lat.get("max")]]), "",
           "## 状态码分布", "```json", json.dumps(res.get("status_distribution",{}),ensure_ascii=False), "```", "",
           "## 场景", "```json", json.dumps(sc,ensure_ascii=False,indent=2), "```", "",
           "## 复跑命令", "```bash", f"python tools/perf/load_run.py --base-url <TEST_BASE_URL> --scenarios <scenarios.json> --fixtures <fixtures.json> --only {res['id']} --concurrency 10 --duration 30", "```", ""]
    if res["mode"]!="run": lines += ["> 本文件不是实测压测结论；需接入授权测试环境后重跑。", ""]
    return "\n".join(lines)

async def async_main(args):
    global args_slow
    args_slow=args.slow_ms
    out=Path(args.output)
    if args.clean and out.exists(): shutil.rmtree(out)
    out.mkdir(parents=True,exist_ok=True)
    endpoints=json.loads(Path(args.endpoints).read_text(encoding="utf-8")) if args.endpoints else None
    scdata=json.loads(Path(args.scenarios).read_text(encoding="utf-8")) if args.scenarios else None
    scenarios=scdata["scenarios"] if isinstance(scdata,dict) else scdata
    if scenarios is None:
        from scenarios import build
        scenarios=build(endpoints["endpoints"] if isinstance(endpoints,dict) else endpoints)
    fixtures=json.loads(Path(args.fixtures).read_text(encoding="utf-8")) if args.fixtures and Path(args.fixtures).exists() else {}
    for k,v in os.environ.items():
        if k.startswith("PERF_"): fixtures.setdefault(k[5:].lower(),v)
    mode="plan"; base=args.base_url; transport=None
    if args.self_test:
        mode="self_test"; base="http://self-test.invalid"
        def handler(request):
            time.sleep(0.001)
            return httpx.Response(200,json={"code":200,"message":"success","data":{"synthetic":True},"trace_id":"self-test"},headers={"X-Trace-Id":"self-test","X-Process-Time-Ms":"1.0"})
        transport=httpx.MockTransport(handler)
        scenarios=[{"id":"self_health","method":"GET","path":"/health","summary":"runner self-test","safety":"read","auth":False,"path_params":{},"query":{},"json":None},
                   {"id":"self_users","method":"GET","path":"/api/v1/users","summary":"runner self-test","safety":"read","auth":False,"path_params":{},"query":{"page":1},"json":None}]
    elif not base:
        mode="plan"
    else:
        mode="run"
        if not (base.startswith("http://127.0.0.1") or base.startswith("http://localhost") or args.allow_remote):
            print("拒绝非本地目标：压测远程环境必须显式 --allow-remote 并获得授权",file=sys.stderr); return 2
    results=[]; epdir=out/"endpoints"; epdir.mkdir(exist_ok=True)
    limits=httpx.Limits(max_connections=max(10,args.concurrency*2),max_keepalive_connections=args.concurrency)
    async with httpx.AsyncClient(transport=transport,limits=limits,verify=not args.insecure,follow_redirects=False) as client:
        if mode=="run" and not fixtures.get("token"):
            try: await login(client,base,fixtures)
            except Exception as e: print("登录失败，需要 PERF_TOKEN 或可用账号：",e,file=sys.stderr)
        for sc in scenarios:
            if args.only and sc["id"] not in args.only and sc["path"] not in args.only: continue
            sc=subst(sc,fixtures)
            status="planned"; reason=""; samples=[]
            safety=sc.get("safety") or classify(sc["method"],sc["path"])
            if mode=="plan":
                status="planned"; reason="未提供 --base-url；仅生成执行计划"
            elif not allowed(safety,args):
                status="skipped"; reason=f"安全分类 {safety} 未启用对应开关"
            elif sc["method"]=="WEBSOCKET":
                status="blocked"; reason="WebSocket 需要专用压测客户端，本脚本默认不执行"
            elif unresolved(sc):
                status="blocked"; reason="存在未解析夹具变量 {{...}}"
            elif sc.get("auth",True) and not (fixtures.get("token") or os.environ.get("PERF_TOKEN")) and sc["path"]!="/api/v1/auth/login":
                status="blocked"; reason="缺少 PERF_TOKEN 或登录凭据"
            elif sc["method"] in {"POST","PUT","PATCH"} and sc.get("json") is None and sc.get("form") is None and sc.get("files") is None:
                status="blocked"; reason="缺少请求体夹具"
            else:
                status="executed"
                try: samples=await run_scenario(client,sc,base,fixtures,args)
                except Exception as e:
                    status="error"; reason=f"{type(e).__name__}: {e}"
            res=summarize(sc,samples,mode,status,reason)
            d=epdir/sc["id"]; d.mkdir(parents=True,exist_ok=True)
            write_json(d/"result.json",res); write_csv(d/"samples.csv",samples)
            (d/"report.md").write_text(endpoint_md(res,sc),encoding="utf-8")
            results.append(res)
            print(f"{status:9s} {sc['method']:6s} {sc['path']} samples={len(samples)}")
    summary={"generated_at":now(),"mode":mode,"base_url":base if mode=="run" else None,"endpoint_count":len(results),
             "executed":sum(1 for r in results if r["status"]=="executed"),"planned":sum(1 for r in results if r["status"]=="planned"),
             "skipped":sum(1 for r in results if r["status"]=="skipped"),"blocked":sum(1 for r in results if r["status"]=="blocked"),
             "errors":sum(1 for r in results if r["status"]=="error"),"args":{k:v for k,v in vars(args).items() if k not in {"fixtures"}},
             "results":results}
    write_json(out/"summary.json",summary)
    rows=[{k:r.get(k) for k in ["id","method","path","safety","mode","status","reason","samples","ok","errors","error_rate"]} | {f"latency_{k}":(r.get("latency_ms") or {}).get(k) for k in ["avg","p50","p95","p99","max"]} for r in results]
    write_csv(out/"summary.csv",rows)
    md=["# 接口压测汇总","",f"- 生成时间：{summary['generated_at']}",f"- 模式：{mode}",f"- 目标：{summary['base_url'] or '未提供（计划模式）'}",
        f"- 接口数：{len(results)}；执行：{summary['executed']}；计划：{summary['planned']}；跳过：{summary['skipped']}；阻塞：{summary['blocked']}；错误：{summary['errors']}","",
        md_table(["ID","方法","路径","分类","状态","样本","错误率","P95 ms","原因"],[[r["id"],r["method"],r["path"],r["safety"],r["status"],r["samples"],r["error_rate"],(r.get("latency_ms") or {}).get("p95"),r.get("reason","")] for r in results]),"",
        "> plan/self_test 模式不是生产/测试环境实测结论。真实结果需 --base-url 授权环境重跑。"]
    (out/"summary.md").write_text("\n".join(md),encoding="utf-8")
    try:
        from report_docx import build_load_docx
        build_load_docx(out/"接口压测报告.docx",summary,scenarios)
    except Exception as e:
        print("Word报告生成跳过：",e,file=sys.stderr)
    return 0

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--base-url"); ap.add_argument("--endpoints",default="deliverables/perf/endpoints.json")
    ap.add_argument("--scenarios",default="deliverables/perf/scenarios.json"); ap.add_argument("--fixtures")
    ap.add_argument("--output",default="deliverables/perf/load"); ap.add_argument("--only",nargs="*")
    ap.add_argument("--concurrency",type=int,default=5); ap.add_argument("--duration",type=float,default=3.0)
    ap.add_argument("--requests",type=int,default=0,help="固定请求数；>0 时优先于 duration")
    ap.add_argument("--rps",type=float,default=0.0); ap.add_argument("--timeout",type=float,default=30.0)
    ap.add_argument("--slow-ms",type=float,default=1000.0); ap.add_argument("--read-only",action="store_true")
    ap.add_argument("--include-writes",action="store_true"); ap.add_argument("--include-destructive",action="store_true")
    ap.add_argument("--confirm-destructive",default=""); ap.add_argument("--include-stream",action="store_true")
    ap.add_argument("--allow-remote",action="store_true"); ap.add_argument("--insecure",action="store_true")
    ap.add_argument("--self-test",action="store_true"); ap.add_argument("--plan",action="store_true"); ap.add_argument("--clean",action="store_true")
    args=ap.parse_args()
    if args.plan: args.base_url=None; args.self_test=False
    raise SystemExit(asyncio.run(async_main(args)))

if __name__=="__main__": main()
