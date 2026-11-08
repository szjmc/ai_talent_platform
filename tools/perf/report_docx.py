"""Word report builders for load/APM/DB outputs."""
from __future__ import annotations
from pathlib import Path
import json
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.oxml.ns import qn
from common import md_table

def _doc(title):
    d=Document(); s=d.sections[0]; s.left_margin=s.right_margin=Cm(1.8); s.top_margin=s.bottom_margin=Cm(1.8)
    st=d.styles['Normal']; st.font.name='Calibri'; st.font.size=Pt(10)
    st.element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),'宋体')
    d.add_heading(title,0); return d

def _table(d,headers,rows):
    if not rows: rows=[["无"]*len(headers)]
    t=d.add_table(rows=1,cols=len(headers)); t.style='Light Grid Accent 1'
    for i,h in enumerate(headers): t.rows[0].cells[i].text=str(h)
    for row in rows:
        cells=t.add_row().cells
        for i,v in enumerate(row): cells[i].text=str(v)
    return t

def build_load_docx(path,summary,scenarios=None):
    d=_doc("AI 数字化人才平台接口压测报告")
    d.add_paragraph(f"生成时间：{summary['generated_at']}；模式：{summary['mode']}；目标：{summary.get('base_url') or '未提供'}")
    d.add_paragraph("声明：plan/self_test 模式不是实测压测结论；真实结果必须来自授权测试环境。未执行/阻塞接口不得填写虚构指标。")
    _table(d,["接口数","执行","计划","跳过","阻塞","错误"],[[summary['endpoint_count'],summary['executed'],summary['planned'],summary['skipped'],summary['blocked'],summary['errors']]])
    d.add_heading("每接口结果/计划",1)
    rows=[]
    for r in summary["results"]:
        lat=r.get("latency_ms") or {}
        rows.append([r["method"],r["path"],r["safety"],r["status"],r["samples"],r["error_rate"],lat.get("p95"),r.get("reason","")])
    _table(d,["方法","路径","分类","状态","样本","错误率","P95 ms","原因"],rows)
    d.add_heading("每接口明细",1)
    scmap={s["id"]:s for s in (scenarios or [])}
    for r in summary["results"]:
        d.add_heading(f"{r['method']} {r['path']}",2)
        lat=r.get("latency_ms") or {}
        d.add_paragraph(f"状态：{r['status']}；分类：{r['safety']}；证据：{r['evidence']}；原因：{r.get('reason') or '无'}")
        _table(d,["样本","成功","错误","错误率","Avg","P50","P95","P99","Max"],[[r.get("samples",0),r.get("ok",0),r.get("errors",0),r.get("error_rate"),lat.get("avg"),lat.get("p50"),lat.get("p95"),lat.get("p99"),lat.get("max")]])
        sc=scmap.get(r["id"])
        if sc:
            p=d.add_paragraph("场景："); p.style=d.styles['Normal']
            d.add_paragraph(json.dumps(sc,ensure_ascii=False))
    d.add_heading("复跑方式",1)
    d.add_paragraph("python tools/perf/load_run.py --base-url <TEST_BASE_URL> --fixtures <fixtures.json> --concurrency 10 --duration 30 --allow-remote")
    d.add_paragraph("写接口/破坏性接口/流式接口需显式开关，并准备清理脚本；默认只读。")
    Path(path).parent.mkdir(parents=True,exist_ok=True); d.save(path)

def build_db_docx(path,report):
    d=_doc("AI 数字化人才平台数据库性能报告")
    d.add_paragraph(f"生成时间：{report.get('generated_at')}；模式：{report.get('mode')}")
    d.add_paragraph("声明：未连接数据库时仅输出探针脚本/连接检查；不得将探针SQL或历史文档当作实测性能结论。")
    for target in report.get("targets",[]):
        d.add_heading(target.get("name","target"),1)
        d.add_paragraph(f"状态：{target.get('status')}；原因：{target.get('error') or '无'}")
        for sec,rows in (target.get("sections") or {}).items():
            d.add_heading(sec,2)
            if isinstance(rows,list) and rows and isinstance(rows[0],dict):
                _table(d,list(rows[0].keys()),[[v for v in r.values()] for r in rows[:100]])
            else:
                d.add_paragraph(str(rows))
    Path(path).parent.mkdir(parents=True,exist_ok=True); d.save(path)
