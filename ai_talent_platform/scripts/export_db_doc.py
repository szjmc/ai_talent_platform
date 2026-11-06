# -*- coding: utf-8 -*-
"""导出培训模块数据表的结构与数据为单文件 HTML 速查页。

标签：[Dify组] [P2]（培训表结构与数据导出 HTML 速查页）

用法：
    python scripts/export_db_doc.py

输出：docs/数据库结构与数据速查.html
连接参数从项目根目录 .env 读取，不在脚本里写死密码。
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "数据库结构与数据速查.html"

TABLES = [
    "position_requirements",
    "courses",
    "training_plans",
    "plan_courses",
    "learning_records",
]

BASE_COLS = {"id", "version", "created_at", "updated_at", "is_deleted",
             "deleted_at", "created_by", "updated_by"}

NULL_SPAN = '<span class="null">'

META: dict[str, dict] = {
    "position_requirements": {
        "cn": "岗位能力模型",
        "role": "标准来源",
        "desc": "每个岗位一套能力维度要求分 + 权重。计划生成时先读这张表，"
                "拿到 target 和 weight，才能算差距和排序。",
        "write": "seed_position_requirements.py（--bind 可绑岗位）",
        "read": "TrainingService._resolve_requirement_model()",
        "cols": {
            "position_id": "岗位 ID，关联 positions.id",
            "version": "模型版本，改权重时递增",
            "dimensions": "JSON：各维度要求分 {维度: {required: 分}}",
            "weights": "JSON：各维度权重，合计必须为 1.0",
            "source": "来源，manual = 人工配置",
        },
    },
    "courses": {
        "cn": "课程库",
        "role": "教材来源",
        "desc": "所有可推荐课程的目录。匹配靠 category 与 skill_tags 对能力维度，"
                "没有匹配课程时该能力项会被标记为「无课可推」。",
        "write": "seed_training_courses.py / POST /trainings/courses",
        "read": "TrainingService._build_skeleton() → match_courses()",
        "cols": {
            "title": "课程名称",
            "category": "所属能力维度，课程匹配的第一关键字",
            "level": "难度 L1–L4，需 ≥ 学员当前等级",
            "duration_hours": "课时数，参与总时长估算",
            "resource_url": "学习链接，可为空",
            "cover": "封面图地址，可为空",
            "description": "课程简介",
            "skill_tags": "JSON 技能标签，用于精准匹配",
            "is_active": "1 上架 / 0 下架，下架不参与推荐",
        },
    },
    "training_plans": {
        "cn": "培训计划",
        "role": "主体",
        "desc": "一次生成的培养计划。created_by_ai=1 说明有 LLM 参与写总结，"
                "=0 说明是纯规则兜底产出。",
        "write": "POST /trainings/plans/generate",
        "read": "/plans/{id}、/plans、/lag-scan",
        "cols": {
            "name": "计划名称，自动拼接「姓名·能力项提升计划」",
            "talent_id": "关联 talents.id",
            "goal": "目标描述",
            "start_date": "开始日期",
            "end_date": "结束日期，由总课时 ÷ 每周可投入小时推算",
            "status": "not_started / ongoing / finished",
            "created_by_ai": "1 = LLM 参与生成，0 = 纯规则兜底",
            "source_assessment_id": "来源测评 ID，可空",
        },
    },
    "plan_courses": {
        "cn": "计划课程明细",
        "role": "关联表",
        "desc": "计划里具体排了哪些课、按什么顺序学。一个计划对多门课。",
        "write": "TrainingService.create_plan()",
        "read": "get_plan() 拼装课程列表",
        "cols": {
            "plan_id": "关联 training_plans.id",
            "course_id": "关联 courses.id",
            "sort": "学习顺序，从 1 开始",
            "require_pass": "1 = 必须通过才能算完成",
        },
    },
    "learning_records": {
        "cn": "学习记录",
        "role": "过程数据",
        "desc": "每个学员每门课一条。提交成绩写这里，通过率的分母和分子都靠它。"
                "plan_id 可为空——允许先有学习行为、后归入计划。",
        "write": "POST /trainings/plans/{id}/score、/records",
        "read": "/stats/pass-rate、/plans/{id}/progress、lag-scan",
        "cols": {
            "plan_id": "关联 training_plans.id，可为空",
            "course_id": "关联 courses.id",
            "talent_id": "关联 talents.id",
            "progress": "学习进度 0–100",
            "study_minutes": "累计学习分钟数",
            "last_study_at": "最后学习时间，滞后扫描按它判断",
            "status": "not_started / learning / finished",
            "score": "考试分数，未考为空",
            "passed": "1 通过 / 0 未过，通过率的分子",
            "suspicious": "1 = 疑似刷课，不计入有效统计",
        },
    },
}


def load_env() -> dict[str, str]:
    path = ROOT / ".env"
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def connect(env: dict[str, str]) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=env.get("DB_HOST", "127.0.0.1"),
        port=int(env.get("DB_PORT", "3306")),
        user=env.get("DB_USER", "root"),
        password=env.get("DB_PASSWORD", ""),
        database=env.get("DB_NAME", "ai_talent_platform"),
        charset="utf8mb4",
    )


def fetch(conn, table: str) -> dict:
    cur = conn.cursor()
    cur.execute(f"SHOW CREATE TABLE `{table}`")
    ddl = cur.fetchone()[1]
    cur.execute(f"DESC `{table}`")
    desc = [list(r) for r in cur.fetchall()]
    cur.execute(f"SELECT * FROM `{table}` ORDER BY 1")
    rows = list(cur.fetchall())
    names = [d[0] for d in cur.description]
    return {"ddl": ddl, "desc": desc, "rows": rows, "names": names}


def esc(v) -> str:
    if v is None:
        return '<span class="null">NULL</span>'
    s = str(v)
    if s == "":
        return '<span class="null">""</span>'
    return html.escape(s)


def cell_class(value) -> str:
    if value is None:
        return "cl-null"
    s = str(value)
    if s in ("0", "1", "2") and len(s) == 1:
        return "cl-num"
    if re.fullmatch(r"-?\d+(\.\d+)?", s):
        return "cl-num"
    if s.startswith("{") or s.startswith("["):
        return "cl-json"
    return ""


def col_comment(table: str, col: str) -> str:
    custom = META[table]["cols"].get(col)
    if custom:
        return custom
    if col in BASE_COLS:
        return {
            "id": "主键",
            "version": "乐观锁版本号",
            "created_at": "创建时间",
            "updated_at": "更新时间",
            "is_deleted": "软删除标记",
            "deleted_at": "删除时间",
            "created_by": "创建人 ID",
            "updated_by": "更新人 ID",
        }[col]
    return ""


def render_table_section(table: str, data: dict) -> str:
    meta = META[table]
    n_rows, n_cols = len(data["rows"]), len(data["desc"])

    col_rows = []
    for name, ctype, nullable, key, default, extra in data["desc"]:
        note = col_comment(table, name) or '<span class="null">—</span>'
        key_badge = ""
        if key == "PRI":
            key_badge = '<span class="badge pk">PK</span>'
        elif key == "MUL":
            key_badge = '<span class="badge fk">IDX</span>'
        base = ' class="base"' if name in BASE_COLS else ""
        if key_badge:
            null_cell = key_badge
        elif nullable == "YES":
            null_cell = NULL_SPAN + "YES</span>"
        else:
            null_cell = "NO"
        default_cell = esc(default) if default is not None else NULL_SPAN + "—</span>"
        col_rows.append(
            f"<tr{base}><td class='mono'>{html.escape(name)}</td>"
            f"<td class='mono type'>{html.escape(ctype)}</td>"
            f"<td class='ctr'>{null_cell}</td>"
            f"<td class='mono'>{default_cell}</td>"
            f"<td>{note}</td></tr>"
        )

    data_head = "".join(f"<th class='mono'>{html.escape(n)}</th>" for n in data["names"])
    data_body = []
    for row in data["rows"]:
        tds = "".join(
            f"<td class='{cell_class(v)}'>{esc(v)}</td>" for v in row
        )
        data_body.append(f"<tr>{tds}</tr>")

    return f"""
<section class="table-block" id="t-{table}">
  <div class="tb-head">
    <div>
      <h2>{meta['cn']} <span class="mono tname">{table}</span></h2>
      <p class="desc">{meta['desc']}</p>
    </div>
    <div class="tb-stats">
      <span class="stat"><b>{n_rows}</b> 行</span>
      <span class="stat"><b>{n_cols}</b> 列</span>
      <span class="role">{meta['role']}</span>
    </div>
  </div>

  <div class="io">
    <div><span class="io-k">写入</span><span class="mono">{html.escape(meta['write'])}</span></div>
    <div><span class="io-k">读取</span><span class="mono">{html.escape(meta['read'])}</span></div>
  </div>

  <h3>列定义</h3>
  <div class="scroll">
    <table class="grid">
      <thead><tr><th>字段</th><th>类型</th><th class="ctr">键 / 可空</th><th>默认</th><th>含义</th></tr></thead>
      <tbody>{''.join(col_rows)}</tbody>
    </table>
  </div>

  <h3>现有数据 <span class="sub">{n_rows} 行</span></h3>
  <div class="scroll data-wrap">
    <table class="grid data">
      <thead><tr>{data_head}</tr></thead>
      <tbody>{''.join(data_body)}</tbody>
    </table>
  </div>

  <details>
    <summary>建表语句 DDL</summary>
    <pre class="mono">{html.escape(data['ddl'])}</pre>
  </details>
</section>"""


RELATION_SVG = """
<svg viewBox="0 0 680 200" width="100%" role="img" aria-label="五张表的关联关系">
  <defs>
    <marker id="a" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6"
      orient="auto-start-reverse">
      <path d="M2 1L8 5L2 9" fill="none" stroke="#888780" stroke-width="1.5"
        stroke-linecap="round" stroke-linejoin="round"/>
    </marker>
  </defs>

  <g>
    <rect x="40" y="30" width="150" height="46" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
    <text x="115" y="47" text-anchor="middle" font-size="13" font-weight="500" fill="#0C447C" font-family="system-ui,sans-serif" dominant-baseline="central">position_requirements</text>
    <text x="115" y="63" text-anchor="middle" font-size="11" fill="#185FA5" font-family="system-ui,sans-serif" dominant-baseline="central">3 行 · 标准</text>
  </g>

  <path d="M190 53 L232 53" stroke="#888780" stroke-width="1.5" fill="none" marker-end="url(#a)"/>

  <g>
    <rect x="235" y="26" width="150" height="54" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
    <text x="310" y="46" text-anchor="middle" font-size="13" font-weight="500" fill="#04342C" font-family="system-ui,sans-serif" dominant-baseline="central">training_plans</text>
    <text x="310" y="64" text-anchor="middle" font-size="11" fill="#0F6E56" font-family="system-ui,sans-serif" dominant-baseline="central">4 行 · 主体</text>
  </g>

  <path d="M385 53 L427 53" stroke="#888780" stroke-width="1.5" fill="none" marker-end="url(#a)"/>

  <g>
    <rect x="430" y="26" width="150" height="54" rx="8" fill="#FAEEDA" stroke="#854F0B" stroke-width="0.5"/>
    <text x="505" y="46" text-anchor="middle" font-size="13" font-weight="500" fill="#412402" font-family="system-ui,sans-serif" dominant-baseline="central">plan_courses</text>
    <text x="505" y="64" text-anchor="middle" font-size="11" fill="#854F0B" font-family="system-ui,sans-serif" dominant-baseline="central">13 行 · 明细</text>
  </g>

  <path d="M505 80 L505 118" stroke="#888780" stroke-width="1.5" fill="none" marker-end="url(#a)"/>

  <g>
    <rect x="430" y="120" width="150" height="54" rx="8" fill="#F1EFE8" stroke="#5F5E5A" stroke-width="0.5"/>
    <text x="505" y="140" text-anchor="middle" font-size="13" font-weight="500" fill="#2C2C2A" font-family="system-ui,sans-serif" dominant-baseline="central">courses</text>
    <text x="505" y="158" text-anchor="middle" font-size="11" fill="#5F5E5A" font-family="system-ui,sans-serif" dominant-baseline="central">19 行 · 教材</text>
  </g>

  <path d="M310 80 L310 118" stroke="#888780" stroke-width="1.5" fill="none" marker-end="url(#a)"/>

  <g>
    <rect x="235" y="120" width="150" height="54" rx="8" fill="#FBEAF0" stroke="#993556" stroke-width="0.5"/>
    <text x="310" y="140" text-anchor="middle" font-size="13" font-weight="500" fill="#4B1528" font-family="system-ui,sans-serif" dominant-baseline="central">learning_records</text>
    <text x="310" y="158" text-anchor="middle" font-size="11" fill="#993556" font-family="system-ui,sans-serif" dominant-baseline="central">39 行 · 过程</text>
  </g>

  <path d="M385 147 L427 147" stroke="#888780" stroke-width="1.5" fill="none" marker-end="url(#a)"/>
</svg>
"""


CSS = """
:root{
  --bg:#F7F7F5; --card:#FFFFFF; --ink:#1F1F1E; --ink2:#5C5C58; --ink3:#8E8E88;
  --line:#E4E4DE; --line2:#D3D3CB; --accent:#185FA5; --accent-bg:#E6F1FB;
  --teal:#0F6E56; --warn:#854F0B; --null:#A8A8A0;
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#1A1A19; --card:#232322; --ink:#EDEDE8; --ink2:#B4B4AD; --ink3:#8A8A84;
    --line:#33332F; --line2:#42423C; --accent:#85B7EB; --accent-bg:#1B2C3D;
    --teal:#5DCAA5; --warn:#EF9F27; --null:#6B6B64;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
  font-size:14px;line-height:1.65;-webkit-font-smoothing:antialiased}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px}
.wrap{max-width:1120px;margin:0 auto;padding:40px 28px 80px}

header.top{border-bottom:1px solid var(--line);padding-bottom:22px;margin-bottom:28px}
header.top h1{margin:0 0 6px;font-size:22px;font-weight:500;letter-spacing:-.01em}
.meta-line{color:var(--ink2);font-size:13px;display:flex;gap:18px;flex-wrap:wrap}
.meta-line b{font-weight:500;color:var(--ink)}

.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px 22px;margin-bottom:22px}

.toc{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:26px}
.toc a{display:inline-flex;align-items:center;gap:7px;padding:7px 13px;border-radius:8px;
  border:1px solid var(--line2);color:var(--ink2);text-decoration:none;font-size:13px;background:var(--card)}
.toc a:hover{border-color:var(--accent);color:var(--accent)}
.toc a .n{color:var(--ink3);font-size:11.5px;font-family:ui-monospace,monospace}

.table-block{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:22px;margin-bottom:24px;scroll-margin-top:20px}
.tb-head{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;
  padding-bottom:16px;border-bottom:1px solid var(--line);margin-bottom:14px}
.tb-head h2{margin:0 0 7px;font-size:17px;font-weight:500}
.tname{color:var(--accent);background:var(--accent-bg);padding:2px 7px;border-radius:5px;margin-left:6px}
.desc{margin:0;color:var(--ink2);font-size:13.5px;max-width:640px}
.tb-stats{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end;flex-shrink:0}
.stat{font-size:12.5px;color:var(--ink2);background:var(--bg);border:1px solid var(--line);
  padding:3px 9px;border-radius:6px}
.stat b{font-weight:500;color:var(--ink)}
.role{font-size:12.5px;color:var(--teal);border:1px solid currentColor;padding:3px 9px;border-radius:6px}

.io{display:flex;gap:26px;flex-wrap:wrap;margin-bottom:6px;font-size:12.5px;color:var(--ink2)}
.io-k{display:inline-block;min-width:34px;color:var(--ink3);margin-right:8px}

h3{font-size:13.5px;font-weight:500;margin:22px 0 10px;color:var(--ink)}
h3 .sub{color:var(--ink3);font-weight:400;font-size:12px;margin-left:6px}

.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:8px}
table.grid{border-collapse:collapse;width:100%;font-size:12.5px}
table.grid th{text-align:left;padding:9px 12px;background:var(--bg);color:var(--ink2);
  font-weight:500;border-bottom:1px solid var(--line2);white-space:nowrap;
  position:sticky;top:0;z-index:1}
table.grid td{padding:8px 12px;border-bottom:1px solid var(--line);vertical-align:top;
  max-width:420px;word-break:break-word}
table.grid tr:last-child td{border-bottom:none}
table.grid tr.base{opacity:.62}
table.grid .ctr{text-align:center}
.type{color:var(--ink2);white-space:nowrap}
.badge{font-size:10.5px;padding:1px 5px;border-radius:4px;font-family:ui-monospace,monospace}
.badge.pk{background:var(--accent-bg);color:var(--accent)}
.badge.fk{background:var(--bg);color:var(--ink3);border:1px solid var(--line2)}

.data-wrap{max-height:460px;overflow:auto}
table.data td{white-space:nowrap;max-width:340px}
table.data td.cl-json{white-space:normal;font-family:ui-monospace,monospace;font-size:11.5px;
  color:var(--ink2);min-width:220px}
.cl-num{font-family:ui-monospace,monospace;text-align:right}
.cl-null{color:var(--null)}
.null{color:var(--null);font-style:normal}

details{margin-top:20px;border:1px solid var(--line);border-radius:8px;padding:0 14px}
summary{cursor:pointer;padding:11px 0;font-size:12.5px;color:var(--ink2);user-select:none}
summary:hover{color:var(--accent)}
details pre{margin:0 0 14px;padding:14px;background:var(--bg);border-radius:6px;
  overflow-x:auto;font-size:12px;line-height:1.6;color:var(--ink)}

.tips{font-size:13px;color:var(--ink2)}
.tips li{margin:5px 0}
.tips code{background:var(--bg);border:1px solid var(--line);padding:1px 5px;border-radius:4px;
  font-family:ui-monospace,monospace;font-size:12px}
footer{color:var(--ink3);font-size:12px;text-align:center;margin-top:36px;padding-top:20px;
  border-top:1px solid var(--line)}
"""


def main() -> None:
    env = load_env()
    conn = connect(env)
    try:
        data = {t: fetch(conn, t) for t in TABLES}
    finally:
        conn.close()

    total_rows = sum(len(d["rows"]) for d in data.values())
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    toc = "".join(
        f'<a href="#t-{t}"><span class="n">{len(data[t]["rows"])}</span>{META[t]["cn"]}</a>'
        for t in TABLES
    )
    body = "".join(render_table_section(t, data[t]) for t in TABLES)

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>数据库结构与数据速查 · 培训模块</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">

<header class="top">
  <h1>数据库结构与数据速查</h1>
  <div class="meta-line">
    <span>MySQL <b>8.0.27</b></span>
    <span><b>{env.get('DB_HOST','127.0.0.1')}:{env.get('DB_PORT','3306')}</b></span>
    <span>库 <b>{env.get('DB_NAME','ai_talent_platform')}</b></span>
    <span><b>{len(TABLES)}</b> 张表 / <b>{total_rows}</b> 行</span>
    <span>生成于 {now}</span>
  </div>
</header>

<div class="card">
  <h3 style="margin-top:0">关联网页</h3>
  {RELATION_SVG}
</div>

<nav class="toc">{toc}</nav>

{body}

<div class="card">
  <h3 style="margin-top:0">几个容易踩的点</h3>
  <ul class="tips">
    <li><code>position_requirements.weights</code> 各维度权重<b>必须合计 1.0</b>，
        seed 脚本会校验；手工改库绕过校验会导致优先级排序错乱。</li>
    <li><code>learning_records.plan_id</code> 允许为空，表示「先有学习行为、后归入计划」。
        统计通过率时不要用 plan_id 做内连接，否则这部分数据会被漏掉。</li>
    <li>这 5 张表都有 <code>is_deleted</code> 软删除字段，查询一律带
        <code>is_deleted = 0</code>，物理删除不要用。</li>
    <li>培训事件不落库，走 <code>event_bus</code> 内存总线，重启即丢。
        需要留痕要另外落表。</li>
  </ul>
</div>

<footer>本页由 scripts/export_db_doc.py 生成，可随时重跑以核对结构是否漂移</footer>
</div>
</body>
</html>"""

    OUT.write_text(doc, encoding="utf-8")
    print(f"[OK] 已生成 {OUT}")
    print(f"     5 张表 / {total_rows} 行")


if __name__ == "__main__":
    main()
