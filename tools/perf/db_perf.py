"""Read-only MySQL/PostgreSQL performance probes: slow SQL, pool, index usage, slow/large data.

Never runs DDL/DML. EXPLAIN is non-ANALYZE by default. Connection/pool failures are recorded as errors,
not fabricated metrics. Use --emit-sql to export probe SQL without a database.
"""
from __future__ import annotations
import argparse, json, os, re, time, argparse
from pathlib import Path
from urllib.parse import urlparse, unquote, quote_plus
from concurrent.futures import ThreadPoolExecutor, as_completed
from common import now, stats, write_json, write_csv, md_table

MYSQL={
"overview":["SELECT VERSION() AS version, NOW() AS server_time"],
"connections_status":["SHOW GLOBAL STATUS WHERE Variable_name IN ('Threads_connected','Threads_running','Max_used_connections','Aborted_connects','Connections','Slow_queries')"],
"connections_variables":["SHOW GLOBAL VARIABLES WHERE Variable_name IN ('max_connections','wait_timeout','interactive_timeout','innodb_buffer_pool_size','performance_schema')"],
"slow_config":["SHOW GLOBAL VARIABLES WHERE Variable_name IN ('slow_query_log','long_query_time','log_queries_not_using_indexes','log_output')"],
"slow_digest":["SELECT SCHEMA_NAME, DIGEST_TEXT, COUNT_STAR, ROUND(SUM_TIMER_WAIT/1e12,3) AS total_s, ROUND(AVG_TIMER_WAIT/1e9,3) AS avg_ms, ROUND(MAX_TIMER_WAIT/1e9,3) AS max_ms, SUM_ROWS_EXAMINED, SUM_ROWS_SENT, SUM_NO_INDEX_USED, SUM_CREATED_TMP_DISK_TABLES FROM performance_schema.events_statements_summary_by_digest WHERE SCHEMA_NAME IS NOT NULL ORDER BY SUM_TIMER_WAIT DESC LIMIT {top}"],
"full_scan_digest":["SELECT SCHEMA_NAME, DIGEST_TEXT, COUNT_STAR, SUM_NO_INDEX_USED, SUM_ROWS_EXAMINED FROM performance_schema.events_statements_summary_by_digest WHERE SUM_NO_INDEX_USED>0 ORDER BY SUM_ROWS_EXAMINED DESC LIMIT {top}"],
"handler_index_proxy":["SHOW GLOBAL STATUS WHERE Variable_name IN ('Handler_read_key','Handler_read_next','Handler_read_rnd','Handler_read_rnd_next','Handler_write')"],
"index_io":["SELECT OBJECT_SCHEMA, OBJECT_NAME, INDEX_NAME, COUNT_READ, COUNT_FETCH, ROUND(SUM_TIMER_READ/1e12,3) AS read_s FROM performance_schema.table_io_waits_summary_by_index_usage WHERE OBJECT_SCHEMA NOT IN ('mysql','performance_schema','sys') ORDER BY SUM_TIMER_READ DESC LIMIT {top}"],
"unused_indexes":["SELECT * FROM sys.schema_unused_indexes LIMIT {top}"],
"redundant_indexes":["SELECT * FROM sys.schema_redundant_indexes LIMIT {top}"],
"buffer_pool":["SHOW GLOBAL STATUS WHERE Variable_name IN ('Innodb_buffer_pool_reads','Innodb_buffer_pool_read_requests','Innodb_buffer_pool_pages_free','Innodb_buffer_pool_pages_total')"],
"temp_sort_locks":["SHOW GLOBAL STATUS WHERE Variable_name IN ('Created_tmp_tables','Created_tmp_disk_tables','Sort_merge_passes','Sort_rows','Innodb_row_lock_waits','Innodb_row_lock_time_avg','Table_locks_waited')"],
"large_tables":["SELECT TABLE_SCHEMA, TABLE_NAME, ENGINE, TABLE_ROWS, ROUND(DATA_LENGTH/1024/1024,2) AS data_mb, ROUND(INDEX_LENGTH/1024/1024,2) AS index_mb, ROUND((DATA_LENGTH+INDEX_LENGTH)/1024/1024,2) AS total_mb, AVG_ROW_LENGTH FROM information_schema.TABLES WHERE TABLE_SCHEMA NOT IN ('mysql','performance_schema','information_schema','sys') ORDER BY (DATA_LENGTH+INDEX_LENGTH) DESC LIMIT {top}"],
"long_running":["SELECT ID, USER, HOST, DB, COMMAND, TIME, STATE, LEFT(INFO,500) AS sql_text FROM information_schema.PROCESSLIST WHERE COMMAND<>'Sleep' AND TIME>1 ORDER BY TIME DESC LIMIT {top}"],
}
PG={
"overview":["SELECT version() AS version, now() AS server_time"],
"settings":["SELECT name, setting FROM pg_settings WHERE name IN ('max_connections','shared_buffers','work_mem','maintenance_work_mem','effective_cache_size','statement_timeout','idle_in_transaction_session_timeout')"],
"connections":["SELECT state, count(*) AS connections FROM pg_stat_activity GROUP BY state ORDER BY connections DESC"],
"long_running":["SELECT pid, usename, datname, state, now()-query_start AS duration, wait_event_type, wait_event, left(query,500) AS query FROM pg_stat_activity WHERE state<>'idle' AND now()-query_start > interval '1 second' ORDER BY duration DESC LIMIT {top}"],
"cache_hit":["SELECT datname, blks_hit, blks_read, CASE WHEN blks_hit+blks_read=0 THEN NULL ELSE round(100.0*blks_hit/(blks_hit+blks_read),3) END AS cache_hit_pct, temp_files, temp_bytes, deadlocks FROM pg_stat_database WHERE datname NOT LIKE 'template%'"],
"slow_statements":["SELECT queryid, calls, round(total_exec_time::numeric,3) AS total_ms, round(mean_exec_time::numeric,3) AS mean_ms, round(max_exec_time::numeric,3) AS max_ms, rows, shared_blks_hit, shared_blks_read, left(query,500) AS query FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT {top}"],
"seq_scan_tables":["SELECT schemaname, relname, seq_scan, seq_tup_read, idx_scan, idx_tup_fetch, n_live_tup, n_dead_tup, CASE WHEN seq_scan+idx_scan=0 THEN NULL ELSE round(100.0*idx_scan/(seq_scan+idx_scan),3) END AS idx_scan_pct, last_autovacuum, last_autoanalyze FROM pg_stat_user_tables ORDER BY seq_tup_read DESC LIMIT {top}"],
"index_usage":["SELECT schemaname, relname, indexrelname, idx_scan, idx_tup_read, idx_tup_fetch FROM pg_stat_user_indexes ORDER BY idx_scan DESC LIMIT {top}"],
"unused_indexes":["SELECT s.schemaname, s.relname, s.indexrelname, pg_size_pretty(pg_relation_size(s.indexrelid)) AS index_size FROM pg_stat_user_indexes s JOIN pg_index i ON i.indexrelid=s.indexrelid WHERE s.idx_scan=0 AND NOT i.indisunique AND NOT i.indisprimary ORDER BY pg_relation_size(s.indexrelid) DESC LIMIT {top}"],
"index_cache":["SELECT schemaname, relname, indexrelname, idx_blks_hit, idx_blks_read, CASE WHEN idx_blks_hit+idx_blks_read=0 THEN NULL ELSE round(100.0*idx_blks_hit/(idx_blks_hit+idx_blks_read),3) END AS idx_cache_hit_pct FROM pg_statio_user_indexes ORDER BY idx_blks_read DESC LIMIT {top}"],
"large_tables":["SELECT schemaname, relname, pg_size_pretty(pg_total_relation_size(relid)) AS total_size, pg_total_relation_size(relid) AS total_bytes, n_live_tup, n_dead_tup FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT {top}"],
"locks":["SELECT locktype, mode, count(*) AS locks FROM pg_locks WHERE NOT granted GROUP BY locktype, mode ORDER BY locks DESC"],
}

def parse_dsn(dsn, default_driver):
    if not dsn: return None
    if "://" not in dsn: dsn=default_driver+"://"+dsn
    u=urlparse(dsn)
    return {"host":u.hostname or "127.0.0.1","port":u.port or (3306 if default_driver=="mysql" else 5432),
            "user":unquote(u.username or ""),"password":unquote(u.password or ""),"database":unquote((u.path or "").lstrip("/")) or None,
            "driver":u.scheme.split("+")[0]}

def mysql_conn(cfg):
    import pymysql
    return pymysql.connect(host=cfg["host"],port=cfg["port"],user=cfg["user"],password=cfg["password"],
                           database=cfg.get("database") or "information_schema",charset="utf8mb4",
                           cursorclass=pymysql.cursors.DictCursor,connect_timeout=5,read_timeout=30,write_timeout=30,autocommit=True)

def pg_conn(cfg):
    import psycopg2, psycopg2.extras
    return psycopg2.connect(host=cfg["host"],port=cfg["port"],user=cfg["user"],password=cfg["password"],
                            dbname=cfg.get("database") or "postgres",connect_timeout=5,options="-c default_transaction_read_only=on")

def query(conn,sql,is_pg=False):
    if is_pg:
        import psycopg2.extras
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql); return [dict(r) for r in cur.fetchall()]
    with conn.cursor() as cur:
        cur.execute(sql); return [dict(r) for r in cur.fetchall()]

def collect_target(name,cfg,queries,top,time_queries):
    target={"name":name,"status":"not_run","sections":{},"errors":[]}
    if not cfg:
        target["status"]="skipped"; target["error"]="未提供连接配置"; return target
    is_pg=name.startswith("PostgreSQL")
    try:
        conn=pg_conn(cfg) if is_pg else mysql_conn(cfg)
    except Exception as e:
        target["status"]="connection_failed"; target["error"]=f"{type(e).__name__}: {e}"; return target
    target["status"]="ok"
    probes=PG if is_pg else MYSQL
    try:
        for sec,sqls in probes.items():
            rows=[]; sec_err=[]
            for sql in sqls:
                sql=sql.format(top=top)
                try: rows.extend(query(conn,sql,is_pg))
                except Exception as e: sec_err.append({"sql":sql,"error":f"{type(e).__name__}: {e}"})
            target["sections"][sec]=rows
            if sec_err: target["errors"].extend(sec_err)
        # derived ratios
        if not is_pg:
            st={r.get("Variable_name"):r.get("Value") for r in target["sections"].get("buffer_pool",[])}
            try:
                reads=float(st.get("Innodb_buffer_pool_reads",0)); req=float(st.get("Innodb_buffer_pool_read_requests",0))
                target["sections"]["derived"]=[{"innodb_buffer_pool_hit_pct":round(100*(1-reads/req),4) if req else None}]
            except Exception: pass
            hs={r.get("Variable_name"):r.get("Value") for r in target["sections"].get("handler_index_proxy",[])}
            try:
                key=float(hs.get("Handler_read_key",0)); rnd=float(hs.get("Handler_read_rnd_next",0)); nxt=float(hs.get("Handler_read_next",0))
                target["sections"].setdefault("derived",[]).append({"handler_index_read_proxy_pct":round(100*key/(key+rnd+nxt),4) if key+rnd+nxt else None,"note":"代理指标，非精确索引命中率"})
            except Exception: pass
        for q in queries:
            if q.get("target","both") not in {"both", "pg" if is_pg else "mysql"}: continue
            sql=q["sql"]; name_q=q.get("name","query"); res={"name":name_q,"sql":sql}
            try:
                explain=("EXPLAIN (FORMAT JSON) " if is_pg else "EXPLAIN FORMAT=JSON ")+sql
                res["explain"]=query(conn,explain,is_pg)
                if time_queries:
                    t=time.perf_counter(); rows=query(conn,sql,is_pg); res["elapsed_ms"]=round((time.perf_counter()-t)*1000,3); res["row_count"]=len(rows)
            except Exception as e: res["error"]=f"{type(e).__name__}: {e}"
            target["sections"].setdefault("query_plans",[]).append(res)
    finally:
        try: conn.close()
        except Exception: pass
    return target

def pool_test(target,cfg,args):
    res={"target":target,"status":"not_run"}
    if not cfg: res["status"]="skipped"; res["error"]="未提供连接配置"; return res
    from sqlalchemy import create_engine, text, event
    scheme="postgresql+psycopg2" if target=="pg" else "mysql+pymysql"
    url=f"{scheme}://{quote_plus(cfg['user'])}:{quote_plus(cfg['password'])}@{cfg['host']}:{cfg['port']}/{cfg.get('database') or ('postgres' if target=='pg' else 'information_schema')}"
    try:
        engine=create_engine(url,pool_size=args.pool_size,max_overflow=args.pool_max_overflow,pool_timeout=args.pool_timeout,pool_recycle=args.pool_recycle,pool_pre_ping=True)
    except Exception as e:
        res["status"]="error"; res["error"]=str(e); return res
    lat=[]; acq=[]; errors=[]; snapshots=[]
    def probe():
        t0=time.perf_counter()
        try:
            with engine.connect() as c:
                t1=time.perf_counter(); c.execute(text("SELECT 1")); t2=time.perf_counter()
                acq.append((t1-t0)*1000); lat.append((t2-t1)*1000)
        except Exception as e: errors.append(f"{type(e).__name__}: {e}")
    try:
        with engine.connect() as c: c.execute(text("SELECT 1"))
        deadline=time.perf_counter()+args.pool_duration; n=0
        with ThreadPoolExecutor(max_workers=args.pool_concurrency) as ex:
            futs=[]
            while time.perf_counter()<deadline and n<args.pool_requests:
                futs.append(ex.submit(probe)); n+=1
                if len(futs)>=args.pool_concurrency*4:
                    for f in as_completed(futs[:args.pool_concurrency]): f.result()
                    futs=futs[args.pool_concurrency:]
                snapshots.append(engine.pool.status())
            for f in as_completed(futs): f.result()
        res.update({"status":"ok","requests":n,"errors":len(errors),"error_samples":errors[:5],
                    "acquisition_ms":stats(acq),"query_ms":stats(lat),"pool_status_last":snapshots[-1] if snapshots else engine.pool.status(),
                    "config":{"pool_size":args.pool_size,"max_overflow":args.pool_max_overflow,"pool_timeout":args.pool_timeout,"pool_recycle":args.pool_recycle,"concurrency":args.pool_concurrency}})
    except Exception as e:
        res["status"]="connection_failed"; res["error"]=f"{type(e).__name__}: {e}"
    finally:
        try: engine.dispose()
        except Exception: pass
    return res

def emit_sql(out,top):
    lines=["-- Read-only performance probes. Review before running; requires privileges on performance_schema/sys/pg_stat_*.", "-- MySQL"]
    for sec,sqls in MYSQL.items():
        lines.append(f"\n-- [{sec}]")
        lines += [s.format(top=top)+";" for s in sqls]
    (out/"mysql_probes.sql").write_text("\n".join(lines),encoding="utf-8")
    lines=["-- PostgreSQL read-only performance probes"]
    for sec,sqls in PG.items():
        lines.append(f"\n-- [{sec}]")
        lines += [s.format(top=top)+";" for s in sqls]
    (out/"pgsql_probes.sql").write_text("\n".join(lines),encoding="utf-8")
    example=[{"target":"mysql","name":"talents_page","sql":"SELECT id, name, status FROM talents WHERE is_deleted=0 ORDER BY id DESC LIMIT 20"},
             {"target":"mysql","name":"talents_deep_offset","sql":"SELECT id, name FROM talents WHERE is_deleted=0 ORDER BY id DESC LIMIT 20 OFFSET 10000"},
             {"target":"mysql","name":"matching_records_by_position","sql":"SELECT * FROM matching_records WHERE position_id=1 ORDER BY created_at DESC LIMIT 20"},
             {"target":"pg","name":"analytics_distribution","sql":"SELECT education, count(*) FROM dim_talent_wide GROUP BY education"},
             {"target":"pg","name":"analytics_trend","sql":"SELECT snapshot_month, count(*) FROM fact_talent_snapshot GROUP BY snapshot_month ORDER BY snapshot_month"}]
    write_json(out/"db_queries.example.json",example)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--target",choices=["both","mysql","pg"],default="both")
    ap.add_argument("--mysql-dsn",default=os.environ.get("PERF_MYSQL_DSN")); ap.add_argument("--pg-dsn",default=os.environ.get("PERF_PG_DSN"))
    ap.add_argument("--mysql-host",default=os.environ.get("DB_HOST","127.0.0.1")); ap.add_argument("--mysql-port",type=int,default=int(os.environ.get("DB_PORT","3306")))
    ap.add_argument("--mysql-user",default=os.environ.get("DB_USER","perf_probe")); ap.add_argument("--mysql-password",default=os.environ.get("DB_PASSWORD","")); ap.add_argument("--mysql-db",default=os.environ.get("DB_NAME","information_schema"))
    ap.add_argument("--pg-host",default=os.environ.get("PGSQL_HOST","127.0.0.1")); ap.add_argument("--pg-port",type=int,default=int(os.environ.get("PGSQL_PORT","5432")))
    ap.add_argument("--pg-user",default=os.environ.get("PGSQL_USER","perf_probe")); ap.add_argument("--pg-password",default=os.environ.get("PGSQL_PASSWORD","")); ap.add_argument("--pg-db",default=os.environ.get("PGSQL_DB","postgres"))
    ap.add_argument("--out",default="deliverables/perf/db"); ap.add_argument("--top",type=int,default=20)
    ap.add_argument("--emit-sql",action="store_true"); ap.add_argument("--check",action="store_true"); ap.add_argument("--pool-test",action="store_true")
    ap.add_argument("--queries"); ap.add_argument("--time-queries",action="store_true")
    ap.add_argument("--pool-concurrency",type=int,default=10); ap.add_argument("--pool-duration",type=float,default=5.0); ap.add_argument("--pool-requests",type=int,default=200)
    ap.add_argument("--pool-size",type=int,default=5); ap.add_argument("--pool-max-overflow",type=int,default=10); ap.add_argument("--pool-timeout",type=float,default=5.0); ap.add_argument("--pool-recycle",type=int,default=1800)
    a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    if a.emit_sql: emit_sql(out,a.top)
    mysql_cfg=parse_dsn(a.mysql_dsn,"mysql") if a.mysql_dsn else {"host":a.mysql_host,"port":a.mysql_port,"user":a.mysql_user,"password":a.mysql_password,"database":a.mysql_db}
    pg_cfg=parse_dsn(a.pg_dsn,"postgresql") if a.pg_dsn else {"host":a.pg_host,"port":a.pg_port,"user":a.pg_user,"password":a.pg_password,"database":a.pg_db}
    queries=json.loads(Path(a.queries).read_text(encoding="utf-8")) if a.queries else []
    report={"generated_at":now(),"mode":"check" if a.check else ("emit_sql" if a.emit_sql and not a.check else "probe"),
            "read_only":True,"targets":[],"pool":[],"note":"未连接/失败时不生成性能结论；探针SQL不是慢查询实测结果。"}
    if a.check or (not a.emit_sql):
        if a.target in {"both","mysql"}: report["targets"].append(collect_target("MySQL",mysql_cfg,queries,a.top,a.time_queries))
        if a.target in {"both","pg"}: report["targets"].append(collect_target("PostgreSQL",pg_cfg,queries,a.top,a.time_queries))
    if a.pool_test:
        if a.target in {"both","mysql"}: report["pool"].append(pool_test("mysql",mysql_cfg,a))
        if a.target in {"both","pg"}: report["pool"].append(pool_test("pg",pg_cfg,a))
    write_json(out/"db_perf.json",report)
    rows=[]
    for t in report["targets"]:
        for sec,data in (t.get("sections") or {}).items():
            if isinstance(data,list):
                for r in data[:100]:
                    if isinstance(r,dict): rows.append({"target":t["name"],"section":sec,**{k:str(v)[:500] for k,v in r.items()}})
    write_csv(out/"db_sections.csv",rows)
    md=["# 数据库性能探针报告","",f"- 生成时间：{report['generated_at']}",f"- 模式：{report['mode']}；只读：是","", "## 连接/采集状态",
        md_table(["目标","状态","原因"],[[t["name"],t["status"],t.get("error") or ""] for t in report["targets"]] or [["未执行","-","仅导出SQL"]]),"",
        "## 连接池", md_table(["目标","状态","请求","错误","获取P95 ms","查询P95 ms","原因"],[[p["target"],p["status"],p.get("requests"),p.get("errors"),(p.get("acquisition_ms") or {}).get("p95"),(p.get("query_ms") or {}).get("p95"),p.get("error") or ""] for p in report["pool"]] or [["未执行","-","-","-","-","-","使用 --pool-test"]]),"",
        "## 探针覆盖","MySQL：连接数/慢查询digest/全扫描/索引IO/未使用冗余索引/Buffer Pool命中/临时表排序锁/大表长查询。","PostgreSQL：连接/长查询/缓存命中/pg_stat_statements/顺序扫描与索引使用/未使用索引/索引缓存/大表锁。","",
        "> "+report["note"]]
    (out/"db_perf.md").write_text("\n".join(md),encoding="utf-8")
    try:
        from report_docx import build_db_docx
        build_db_docx(out/"数据库性能报告.docx",report)
    except Exception as e: print("Word报告跳过：",e)
    print(json.dumps({"out":str(out),"targets":[{k:t.get(k) for k in ["name","status","error"]} for t in report["targets"]],"pool":[{k:p.get(k) for k in ["target","status","error"]} for p in report["pool"]]},ensure_ascii=False))

if __name__=="__main__": main()
