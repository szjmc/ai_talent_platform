"""
标签：[Agent组] [P0]
模块：app/routers/exam_router.py
职责：候选人笔试公开入口（邮件链接免登录考试）。

接口：
    GET  /api/v1/exam/{id}/paper?token=   取笔试试卷（不含答案，首次访问自动开考）
    POST /api/v1/exam/{id}/submit?token=  提交笔试答案；达标自动发面试邀约
    GET  /exam/{id}?token=                内置在线考试页（无前端站点时的兜底入口，不挂 /api/v1）
    POST /api/v1/exam/{id}/behaviour?token=  行为监考心跳（切屏/全屏/多显示器/远程控制，公开）
    GET  /api/v1/exam/{id}/behaviour?token=  行为监考状态（是否被阻断，公开）

安全说明：
- 公开接口一律校验 HMAC 令牌（app/utils/exam_token.py），令牌随邮件链接下发；
- 试卷接口绝不返回标准答案；
- 提交接口复用 AssessmentService 的状态机（pending -> in_progress -> submitted）；
- 行为监考（切屏 / 远程控制 / 投屏 / 多显示器）判定一律在服务端完成，前端只上报
  「发生了什么」，内置考试页改脚本也无法伪造分数。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from app.config.settings import settings
from app.core.dependencies import DbSession
from app.core.exceptions import PermissionDeniedError
from app.core.response import success
from app.infrastructure.notify.email_client import send_email
from app.schemas.assessment import AnswerSubmitRequest
from app.schemas.common import SuccessResponse
from app.schemas.proctoring import ProctorHeartbeatRequest
from app.services.assessment_service import AssessmentService
from app.services.proctoring_service import ProctoringService
from app.utils.exam_token import verify_exam_token

# 笔试达标线（百分制），与 assessment_router 保持一致：达标自动发面试邀约
INTERVIEW_THRESHOLD = 60.0

router = APIRouter(prefix="/exam", tags=["笔试考试(公开)"])

# 内置考试页挂在应用根路径（不带 /api/v1），在 application.py 中注册
exam_page_router = APIRouter(include_in_schema=False)


def _check_token(assessment_id: int, token: str) -> None:
    if not verify_exam_token(assessment_id, token):
        raise PermissionDeniedError("笔试链接无效或已失效，请联系 HR 重新获取")


@router.get("/{assessment_id}/paper", summary="取笔试试卷（公开，令牌访问，不含答案）",
            response_model=SuccessResponse[dict])
async def exam_paper(assessment_id: int, db: DbSession,
                     token: str = Query(..., description="邮件链接中的访问令牌")):
    _check_token(assessment_id, token)
    # start_assessment 内部完成 pending -> in_progress 并剥离标准答案
    return success(data=await AssessmentService(db).start_assessment(assessment_id))


@router.post("/{assessment_id}/submit", summary="提交笔试答案（公开，令牌访问）",
             response_model=SuccessResponse[dict])
async def exam_submit(assessment_id: int, body: AnswerSubmitRequest, db: DbSession,
                      token: str = Query(..., description="邮件链接中的访问令牌")):
    _check_token(assessment_id, token)
    service = AssessmentService(db)
    answers = [{"question_id": a.question_id, "answer": a.answer} for a in body.answers]
    result = await service.submit_answers(assessment_id, answers)
    rule_total = result.get("rule_total_score", 0.0)

    # 达标 -> 自动发面试邀约邮件（与管理端提交行为一致）
    invited = False
    if rule_total >= INTERVIEW_THRESHOLD:
        assessment = await service.repo.get_by_id_or_404(db, assessment_id)
        talent_email = getattr(assessment.talent, "email", "") if assessment.talent else ""
        email_result = await send_email(talent_email or "", template="interview_invite",
                                         params={"name": assessment.talent.name})
        invited = bool(email_result.get("ok"))
    return success(data={**result, "interview_invited": invited,
                         "threshold": INTERVIEW_THRESHOLD},
                   message="笔试已提交")


# ------------------------------------------------------------------ 行为监考（内置考试页）
@router.post(
    "/{assessment_id}/behaviour",
    summary="行为监考心跳（切屏 / 全屏 / 多显示器 / 远程控制；公开，令牌访问）",
    response_model=SuccessResponse[dict],
)
async def exam_behaviour_heartbeat(
    assessment_id: int,
    body: ProctorHeartbeatRequest,
    request: Request,
    db: DbSession,
    token: str = Query(..., description="邮件链接中的访问令牌"),
):
    """内置考试页的心跳入口：与前端 InvitePage 共用同一套监考引擎。

    区别只在于「对象定位」：这里用 assessment_id + HMAC 令牌，不走 session token。
    """
    _check_token(assessment_id, token)
    forwarded = request.headers.get("x-forwarded-for")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else None)
    ack = await ProctoringService(db).heartbeat(
        "assessment", token, body, client_ip=ip, subject_id=assessment_id
    )
    return success(data=ack.model_dump(mode="json"))


@router.get(
    "/{assessment_id}/behaviour",
    summary="行为监考状态（公开，令牌访问）",
    response_model=SuccessResponse[dict],
)
async def exam_behaviour_state(
    assessment_id: int,
    db: DbSession,
    token: str = Query(..., description="邮件链接中的访问令牌"),
):
    _check_token(assessment_id, token)
    data = await ProctoringService(db).state("assessment", token)
    return success(data=data.model_dump(mode="json"))


# ------------------------------------------------------------------ 内置在线考试页（兜底入口）
_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>在线笔试 - AI人才测评中心</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Microsoft YaHei',Arial,sans-serif;background:#f3f5f9;color:#1f2d3d;line-height:1.7}}
.wrap{{max-width:760px;margin:0 auto;padding:24px 16px 64px}}
.card{{background:#fff;border-radius:10px;padding:24px;box-shadow:0 2px 10px rgba(0,0,0,.06);margin-bottom:16px}}
h1{{font-size:20px;margin-bottom:8px}}
.meta{{color:#888;font-size:13px;margin-bottom:4px}}
.q{{border-top:1px solid #eee;padding:16px 0}}
.q:first-of-type{{border-top:none}}
.q-title{{font-weight:600;margin-bottom:10px}}
.q-title .score{{color:#2b5aed;font-weight:400;font-size:13px;margin-left:6px}}
.opt{{display:block;padding:8px 12px;border:1px solid #e0e4ec;border-radius:6px;margin-bottom:8px;cursor:pointer}}
.opt:hover{{border-color:#2b5aed}}
.opt input{{margin-right:8px}}
textarea{{width:100%;min-height:110px;padding:10px;border:1px solid #e0e4ec;border-radius:6px;font-size:14px;font-family:inherit;resize:vertical}}
.btn{{display:inline-block;padding:12px 36px;background:#2b5aed;color:#fff;border:none;border-radius:6px;font-size:16px;cursor:pointer}}
.btn:disabled{{background:#9db2f0;cursor:not-allowed}}
.center{{text-align:center;margin-top:24px}}
.msg-err{{color:#d4380d}}
.msg-ok{{color:#0d8a3d}}
.result{{text-align:center;padding:32px 0}}
.result .score-num{{font-size:44px;color:#2b5aed;font-weight:700}}
.loading{{text-align:center;color:#888;padding:48px 0}}
.proctor{{background:#fff7e6;border:1px solid #ffe0a3;color:#8c5a00;border-radius:8px;padding:10px 12px;font-size:13px;margin-bottom:12px;line-height:1.6}}
.proctor.warn{{background:#fff1f0;border-color:#ffa39e;color:#a8071a}}
.proctor .tag{{display:inline-block;border-radius:4px;padding:0 6px;margin-left:6px;font-size:12px;background:rgba(0,0,0,.06)}}
.blocked{{text-align:center;padding:40px 0}}
.blocked h2{{color:#cf1322;margin-bottom:10px}}
.blocked p{{color:#666;font-size:14px;line-height:1.8}}
</style>
</head>
<body>
<div class="wrap">
  <div class="card" id="head" style="display:none">
    <h1 id="title"></h1>
    <p class="meta" id="expire"></p>
    <p class="meta">请独立作答，提交后不可修改。</p>
  </div>
  <div id="proctor" class="proctor" style="display:none"></div>
  <div id="body" class="card"><div class="loading">试卷加载中…</div></div>
</div>
<script>
const API = "{api_prefix}";
const AID = {assessment_id};
const TOKEN = new URLSearchParams(location.search).get("token") || "";
const body = document.getElementById("body");

function esc(s) {{
  return String(s ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
}}
function fail(msg) {{
  body.innerHTML = '<div class="result"><p class="msg-err">' + esc(msg) + "</p></div>";
}}
async function loadPaper() {{
  if (!TOKEN) {{ fail("链接缺少访问令牌，请通过邮件中的完整链接进入。"); return; }}
  let resp;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 15000);
  try {{
    resp = await fetch(API + "/exam/" + AID + "/paper?token=" + encodeURIComponent(TOKEN),
                       {{signal: ctrl.signal}});
  }} catch (e) {{
    clearTimeout(timer);
    fail(e && e.name === "AbortError"
        ? "试卷加载超时（15 秒无响应），请检查网络后刷新重试。"
        : "网络异常，无法连接考试服务，请稍后重试。");
    return;
  }}
  clearTimeout(timer);
  const json = await resp.json().catch(() => null);
  if (!resp.ok || !json || json.code !== 200) {{
    fail((json && json.message) || ("试卷加载失败（" + resp.status + "）"));
    return;
  }}
  render(json.data);
}}
let PAPER_QUESTIONS = [];
function render(data) {{
  document.getElementById("head").style.display = "";
  document.getElementById("title").textContent = data.title || "在线笔试";
  document.getElementById("expire").textContent =
    data.expire_at ? ("截止时间：" + new Date(data.expire_at).toLocaleString()) : "";
  PAPER_QUESTIONS = (data.questions || []).map(q => ({{id: q.question_id, type: q.type}}));
  let html = "";
  (data.questions || []).forEach((q, i) => {{
    html += '<div class="q"><p class="q-title">' + (i + 1) + ". " + esc(q.content) +
            '<span class="score">（' + esc(q.score) + " 分）</span></p>";
    if (q.type === "single" || q.type === "multiple") {{
      const it = q.type === "single" ? "radio" : "checkbox";
      (q.options || []).forEach(o => {{
        html += '<label class="opt"><input type="' + it + '" name="q' + q.question_id +
                '" value="' + esc(o.key) + '">' + esc(o.key) + ". " + esc(o.text) + "</label>";
      }});
    }} else {{
      html += '<textarea name="q' + q.question_id + '" placeholder="请在此作答…"></textarea>';
    }}
    html += "</div>";
  }});
  html += '<div class="center"><button class="btn" id="submitBtn" onclick="submitAnswers()">提交试卷</button></div>';
  body.innerHTML = html;
  // [防作弊] 试卷渲染后启动行为监考（切屏 / 全屏 / 多显示器 / 远程控制线索）
  proctorStart(window.__PROCTOR_HEARTBEAT__ || 15);
}}

/* ------------------------------------------------------------------ 行为监考
 * 内置兜底页没有前端框架，这里用原生 JS 采集「画面外」的作弊信号：
 *   切屏/最小化(visibilitychange)、失焦(blur)、退出全屏、复制粘贴、开发者工具、
 *   多显示器(resize + screen 尺寸启发式)、设备接入(devicechange)。
 * 判定完全在服务端（POST /exam/[id]/behaviour），页面只负责如实上报 + 展示阻断结论。
 */
const PROCTOR = {{ enabled: false, blocked: false, level: "clean", timer: null, queue: [] }};

function proctorPush(type, detail) {{
  if (!PROCTOR.enabled) return;
  const now = Date.now();
  const tail = PROCTOR.queue[PROCTOR.queue.length - 1];
  if (tail && tail.type === type && now - tail.ts < 800) return;
  PROCTOR.queue.push({{ type: type, detail: detail || "", ts: now }});
  if (PROCTOR.queue.length > 40) PROCTOR.queue = PROCTOR.queue.slice(-40);
}}

function displayCount() {{
  try {{
    if (typeof screen.isExtended === "boolean") return screen.isExtended ? 2 : 1;
    return (screen.width > 3200 || screen.height > 2000) ? 2 : 1;
  }} catch (e) {{ return 1; }}
}}

function proctorPanel(state) {{
  const el = document.getElementById("proctor");
  if (!el) return;
  if (!state || state.enabled === false) {{ el.style.display = "none"; return; }}
  const lv = state.risk_level || "clean";
  const warn = lv !== "clean";
  el.className = "proctor" + (warn ? " warn" : "");
  el.style.display = "";
  el.innerHTML = "行为监考进行中" +
    '<span class="tag">风险 ' + esc(lv) + "</span>" +
    '<span class="tag">' + (document.fullscreenElement ? "全屏" : "未全屏") + "</span>" +
    '<span class="tag">显示器 ' + (state.display_count || 1) + "</span>" +
    '<div style="margin-top:6px">' +
    esc(state.warning || "请保持全屏作答，勿切换标签页/窗口；禁止远程控制、投屏、扩展屏与虚拟摄像头。") +
    "</div>" +
    (state.requirements && state.requirements.fullscreen && !document.fullscreenElement
      ? '<button class="btn" style="padding:6px 14px;font-size:13px;margin-top:8px" onclick="proctorFullscreen()">进入全屏</button>'
      : "");
}}

async function proctorHeartbeat() {{
  if (!PROCTOR.enabled || PROCTOR.blocked) return;
  const events = PROCTOR.queue.splice(0, PROCTOR.queue.length);
  let resp;
  try {{
    resp = await fetch(API + "/exam/" + AID + "/behaviour?token=" + encodeURIComponent(TOKEN), {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{
        events: events,
        screen_shared: false,
        display_count: displayCount(),
        timezone: (Intl.DateTimeFormat().resolvedOptions() || {{}}).timeZone || "",
        screen_width: screen.width,
        screen_height: screen.height,
        platform: navigator.platform || ""
      }})
    }});
  }} catch (e) {{
    PROCTOR.queue = events.concat(PROCTOR.queue).slice(-40);
    return;
  }}
  const json = await resp.json().catch(() => null);
  if (!resp.ok || !json || json.code !== 200) return;
  const st = json.data.state;
  proctorPanel(st);
  if (st.blocked && !PROCTOR.blocked) {{
    PROCTOR.blocked = true;
    if (PROCTOR.timer) clearInterval(PROCTOR.timer);
    body.innerHTML = '<div class="blocked"><h2>本次作答已结束</h2><p>' +
      esc(st.block_reason || "监考检测到严重异常行为（切屏 / 远程控制 / 投屏 / 非本人作答）。") +
      "</p><p>如有疑问请联系 HR 申请复核。</p></div>";
  }} else if (json.data.new_violations && json.data.new_violations.length) {{
    const last = json.data.new_violations[json.data.new_violations.length - 1];
    proctorPush.__notice = true;
    const el = document.getElementById("proctor");
    if (el) el.innerHTML += '<div style="margin-top:4px">· ' + esc(last.label || last.detail || "检测到异常行为") + "</div>";
  }}
}}

function proctorFullscreen() {{
  const root = document.documentElement;
  if (root.requestFullscreen) root.requestFullscreen().catch(() => undefined);
}}

function proctorStart(seconds) {{
  PROCTOR.enabled = true;
  document.addEventListener("visibilitychange", () => {{
    if (document.hidden) proctorPush("tab_hidden", "页面切到后台");
    else proctorPush("focus_returned", "返回考试页面");
  }});
  window.addEventListener("blur", () => proctorPush("window_blur", "考试窗口失去焦点"));
  document.addEventListener("fullscreenchange", () => {{
    if (!document.fullscreenElement) proctorPush("fullscreen_exit", "退出全屏");
  }});
  document.addEventListener("copy", () => proctorPush("copy_paste", "复制页面内容"));
  document.addEventListener("paste", (e) => {{
    const t = (e.clipboardData && e.clipboardData.getData("text")) || "";
    proctorPush("copy_paste", "粘贴 " + t.length + " 个字符");
    if (t.length > 400) proctorPush("clipboard_burst", "一次性粘贴 " + t.length + " 个字符");
  }});
  document.addEventListener("keydown", (e) => {{
    const devtools = e.key === "F12" || ((e.ctrlKey || e.metaKey) && e.shiftKey && ["i", "j", "c"].indexOf(e.key.toLowerCase()) >= 0);
    if (devtools) proctorPush("proctor_disabled", "尝试打开开发者工具");
  }});
  window.addEventListener("resize", () => {{
    if (displayCount() >= 2) proctorPush("multi_display", "检测到扩展屏 / 多显示器");
  }});
  if (navigator.mediaDevices && navigator.mediaDevices.addEventListener) {{
    navigator.mediaDevices.addEventListener("devicechange", () => proctorPush("device_added", "音视频设备接入/拔出"));
  }}
  window.addEventListener("beforeunload", (e) => {{
    proctorPush("tab_hidden", "尝试关闭或刷新考试页面");
    e.preventDefault(); e.returnValue = "";
  }});
  proctorFullscreen();
  proctorHeartbeat();
  PROCTOR.timer = setInterval(proctorHeartbeat, Math.max(5, seconds || 15) * 1000);
}}

async function submitAnswers() {{
  const questions = PAPER_QUESTIONS;
  const btn = document.getElementById("submitBtn");
  const answers = [];
  for (const q of questions) {{
    if (q.type === "single") {{
      const el = document.querySelector('input[name="q' + q.id + '"]:checked');
      answers.push({{question_id: q.id, answer: el ? el.value : ""}});
    }} else if (q.type === "multiple") {{
      const els = document.querySelectorAll('input[name="q' + q.id + '"]:checked');
      answers.push({{question_id: q.id, answer: Array.from(els).map(e => e.value)}});
    }} else {{
      const el = document.querySelector('textarea[name="q' + q.id + '"]');
      answers.push({{question_id: q.id, answer: el ? el.value.trim() : ""}});
    }}
  }}
  btn.disabled = true; btn.textContent = "提交中…";
  let resp;
  try {{
    resp = await fetch(API + "/exam/" + AID + "/submit?token=" + encodeURIComponent(TOKEN), {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{answers}})
    }});
  }} catch (e) {{
    btn.disabled = false; btn.textContent = "提交试卷";
    alert("网络异常，请稍后重试"); return;
  }}
  const json = await resp.json().catch(() => null);
  if (!resp.ok || !json || json.code !== 200) {{
    btn.disabled = false; btn.textContent = "提交试卷";
    alert((json && json.message) || ("提交失败（" + resp.status + "）")); return;
  }}
  const d = json.data || {{}};
  body.innerHTML = '<div class="result"><p class="msg-ok">提交成功，感谢作答！</p>' +
    '<p class="score-num">' + esc(d.rule_total_score ?? "-") + '</p>' +
    '<p class="meta">客观题得分（满分 100），主观题将由 AI 综合评阅</p>' +
    (d.interview_invited ? '<p class="msg-ok">恭喜您已达到面试标准，面试邀约已发送至您的邮箱。</p>' : "") +
    "</div>";
  document.getElementById("head").style.display = "none";
}}
loadPaper();
</script>
</body>
</html>
"""


@exam_page_router.get("/exam/{assessment_id}", response_class=HTMLResponse,
                      summary="内置在线考试页（无前端站点时的兜底入口）")
async def exam_page(assessment_id: int):
    html = _PAGE.format(api_prefix=settings.API_PREFIX, assessment_id=assessment_id)
    return HTMLResponse(content=html)


__all__ = ["router", "exam_page_router"]
