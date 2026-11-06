"""
标签：[架构组] [P1]
模块：app/infrastructure/notify/email_client.py
职责：邮件推送客户端（测评邀约通知，SMTP 发送）。

============================ 实现说明 ============================
零门槛方案：个人邮箱（QQ/163/企业邮箱）SMTP + 授权码即可，无需企业认证。
- EMAIL_ENABLED=false 或 SMTP 配置缺失 -> 拒绝发送并返回 ok=False，业务记录保留；
- EMAIL_ENABLED=true 且配置齐全 -> 通过 smtplib 真实发送（HTML + 纯文本双格式）；
- 本地语义模板名（assessment_invite / interview_invite）映射到邮件主题与正文模板。

使用示例：
    await send_email("candidate@example.com", template="assessment_invite",
                     params={"name": "候选人", "paper": "高级Java工程师",
                             "url": "https://exam.example.com/s/abc"})
返回：
    {"ok": bool, "channel": "email", "simulated": bool, "msg": str}
==================================================================
"""

from __future__ import annotations

import asyncio
import html
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)

# 邮件正文统一样式（内联 CSS，兼容主流邮箱客户端）
_STYLE = (
    "font-family:'Microsoft YaHei',Arial,sans-serif;max-width:600px;"
    "margin:0 auto;padding:24px;color:#333;line-height:1.7"
)
_BTN_STYLE = (
    "display:inline-block;margin-top:16px;padding:12px 28px;background:#2b5aed;"
    "color:#fff;text-decoration:none;border-radius:6px;font-size:16px"
)

# 本地模板名 -> 邮件模板（subject / body 使用 str.format 占位符）
_TEMPLATES: dict[str, dict[str, str]] = {
    "assessment_invite": {
        "subject": "【人才测评】笔试邀约通知",
        "body": """
        <div style="{style}">
          <h3 style="margin:0 0 16px;color:#1f2d3d">{name} 您好：</h3>
          <p>恭喜您通过简历初筛，现已进入 <b>{paper}</b> 岗位测评环节。</p>
          <p>请在邀约有效期内登录测评系统完成笔试测评，逾期视为自动放弃。</p>
          {url_block}
          <p style="margin-top:20px;color:#888;font-size:13px">本邮件由系统自动发送，请勿直接回复。</p>
        </div>
        """,
    },
    "interview_invite": {
        "subject": "【人才测评】面试邀约通知",
        "body": """
        <div style="{style}">
          <h3 style="margin:0 0 16px;color:#1f2d3d">{name} 您好：</h3>
          <p>恭喜您通过笔试测评，现正式邀请您参加面试。</p>
          <p>具体面试时间与地点请留意后续通知，并保持手机畅通。</p>
          <p style="margin-top:20px;color:#888;font-size:13px">本邮件由系统自动发送，请勿直接回复。</p>
        </div>
        """,
    },
}


async def send_email(to_email: str, *, template: str, params: dict[str, Any]) -> dict:
    """发送邮件；未配置或发送失败均返回 ok=False，绝不模拟成功。

    :param to_email: 收件人邮箱
    :param template: 本地语义模板名（assessment_invite / interview_invite）
    :param params: 模板变量（name / paper / url 等）
    :return: {"ok", "channel", "simulated", "msg"}
    """
    to_email = (to_email or "").strip()
    if not to_email or "@" not in to_email:
        return {"ok": False, "channel": "email", "simulated": False,
                "msg": "未提供有效的收件邮箱"}

    tpl = _TEMPLATES.get(template)
    if tpl is None:
        return {"ok": False, "channel": "email", "simulated": False,
                "msg": f"未知邮件模板标识：{template}"}

    if not _ready():
        logger.warning("邮件未发送：EMAIL_ENABLED=false 或 SMTP 配置不完整 template=%s", template)
        return {"ok": False, "channel": "email", "simulated": False,
                "msg": "邮件发送未启用或 SMTP 配置不完整"}

    try:
        msg = _build_message(to_email, tpl, params)
        await asyncio.to_thread(_send_sync, msg)
        logger.info("[email] 发送成功 template=%s", tpl["subject"])
        return {"ok": True, "channel": "email", "simulated": False, "msg": "发送成功"}
    except Exception as exc:  # noqa: BLE001 - 邮件失败不阻断业务，向上返回失败结果
        logger.warning("邮件真实发送失败 type=%s", type(exc).__name__)
        return {"ok": False, "channel": "email", "simulated": False, "msg": "邮件服务发送失败，请检查 SMTP 配置"}


def _ready() -> bool:
    """真实发送是否具备完整配置。"""
    return bool(
        settings.EMAIL_ENABLED
        and settings.SMTP_HOST
        and settings.SMTP_USER
        and settings.SMTP_PASSWORD
    )


def _build_message(to_email: str, tpl: dict[str, str], params: dict[str, Any]) -> EmailMessage:
    """组装 HTML + 纯文本双格式邮件（用户输入一律转义防注入）。"""
    # 转义所有变量，防止姓名/岗位等含 HTML 特殊字符破坏邮件结构
    safe = {k: html.escape(str(v)) for k, v in params.items()}

    url = safe.get("url", "")
    url_block = (
        f'<p style="text-align:center"><a href="{url}" style="{_BTN_STYLE}">立即开始笔试</a></p>'
        f'<p style="font-size:13px;color:#666">如按钮无法点击，请复制以下链接到浏览器打开：<br>'
        f'<a href="{url}" style="color:#2b5aed;word-break:break-all">{url}</a></p>'
        if url else ""
    )
    html_body = tpl["body"].format(style=_STYLE, url_block=url_block, **safe)

    # 纯文本备选用原始值（text/plain 原样显示，不做 HTML 转义）
    plain_body = f"【人才测评】{str(params.get('name', '候选人'))} 您好，请查收您的测评通知。"
    raw_url = str(params.get("url", "") or "")
    if raw_url:
        plain_body += f"笔试链接：{raw_url}"

    msg = EmailMessage()
    # QQ/163 等 SMTP 严格要求「发件地址 == 登录账号」，否则返回 502 Invalid input。
    # 因此发件地址统一取 SMTP_USER（SMTP_FROM 仅作历史兼容，不再作为实际发件地址）。
    msg["From"] = formataddr((settings.EMAIL_FROM_NAME, settings.SMTP_USER))
    msg["To"] = to_email
    msg["Subject"] = tpl["subject"]
    msg.set_content(plain_body)
    msg.add_alternative(html_body, subtype="html")
    return msg


def _send_sync(msg: EmailMessage) -> None:
    """同步发送（在 asyncio.to_thread 中执行，避免阻塞事件循环）。"""
    if settings.SMTP_USE_SSL:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT,
                              timeout=15, context=context) as server:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)
    else:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
            if not settings.SMTP_TLS:
                raise RuntimeError("SMTP 未启用 TLS/SSL，拒绝明文发送凭据")
            server.starttls(context=ssl.create_default_context())
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)


__all__ = ["send_email"]
