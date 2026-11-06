"""邀约通知发送：邮件（SMTP 配置则真发，否则 mock 记日志）+ 短信（mock）。

标签：[Agent组] [P1]

降级策略与 Dify 适配一致：默认配置即可离线运行——
- email: settings.SMTP_HOST 为空 -> mode="mock"，正文与链接写入日志，返回 ok=True
  （不阻断邀约创建；send_results 中回显 mode 供前端提示"演示发送"）；
- sms: 仅日志（预留 SMS_PROVIDER 真实网关接入位）；
真实 SMTP 异常也绝不向上抛：ok=False + error，由调用方落库记录。
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)


def build_invite_payload(
    *, type_: str, title: str, candidate_name: str, link: str, interview_time: str | None
) -> tuple[str, str]:
    """生成 (subject, body)。type_: written_test/assessment/interview。"""
    kind = {"written_test": "笔试", "assessment": "测评", "interview": "面试"}.get(type_, "邀约")
    subject = f"【AI 人才平台】{kind}邀约：{title or kind}"
    lines = [
        f"{candidate_name or '您好'}，您好：",
        "",
        f"诚邀您参加{kind}。请点击下方链接进入：",
        link,
    ]
    if interview_time:
        lines.append(f"面试时间：{interview_time}")
    lines += [
        "",
        "1. 链接在有效期内仅可本人使用，请勿转发；",
        "2. 笔试需打开摄像头，系统将进行作弊检测；",
        "3. 面试请提前 5 分钟进入面试间，测试摄像头与麦克风。",
        "",
        "—— AI 人才平台",
    ]
    return subject, "\n".join(lines)


async def send_email(to: str, subject: str, body: str) -> dict[str, Any]:
    """发送邮件；SMTP 未配置走 mock，任何异常都降级为记录结果。"""
    if not to:
        return {"channel": "email", "ok": False, "mode": "mock", "error": "无收件邮箱"}
    if not settings.SMTP_HOST:
        logger.info("[mock-email] to=%s subject=%s body=\n%s", to, subject, body)
        return {"channel": "email", "ok": True, "mode": "mock", "error": None}
    try:

        def _sync_send() -> None:
            msg = EmailMessage()
            msg["From"] = settings.SMTP_FROM or settings.SMTP_USER
            msg["To"] = to
            msg["Subject"] = subject
            msg.set_content(body)
            if settings.SMTP_TLS:
                with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as client:
                    client.starttls()
                    if settings.SMTP_USER:
                        client.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                    client.send_message(msg)
            else:
                with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as client:
                    if settings.SMTP_USER:
                        client.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                    client.send_message(msg)

        await asyncio.to_thread(_sync_send)
        return {"channel": "email", "ok": True, "mode": "smtp", "error": None}
    except Exception as exc:  # noqa: BLE001 —— 发送失败不阻断业务
        logger.warning("邮件发送失败 to=%s: %s", to, exc)
        return {"channel": "email", "ok": False, "mode": "smtp", "error": str(exc)}


async def send_sms(to: str, body: str) -> dict[str, Any]:
    """发送短信；当前默认 mock（记录日志），预留真实网关（SMS_PROVIDER）。"""
    if not to:
        return {"channel": "sms", "ok": False, "mode": "mock", "error": "无手机号"}
    logger.info("[mock-sms] to=%s provider=%s body=\n%s", to, settings.SMS_PROVIDER, body)
    return {"channel": "sms", "ok": True, "mode": "mock", "error": None}
