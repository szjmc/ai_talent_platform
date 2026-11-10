"""邀约通知发送：真实 SMTP 发送与短信网关状态回传。

标签：[Agent组] [P1]

未启用或配置不完整时返回 ok=False，不输出包含链接令牌的模拟通知。
真实 SMTP 异常不向上抛，由调用方持久化发送结果。
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
    """生成 (subject, body)。type_: written_test/assessment/interview/ai_interview。"""
    kind = {
        "written_test": "笔试", "assessment": "测评",
        "interview": "视频面试", "ai_interview": "AI 数字人面试",
    }.get(type_, "邀约")
    subject = f"【AI 人才平台】{kind}邀约：{title or kind}"
    lines = [
        f"{candidate_name or '您好'}，您好：",
        "",
        f"诚邀您参加{kind}。请点击下方链接进入：",
        link,
    ]
    if interview_time:
        lines.append(f"面试时间：{interview_time}")
    if type_ == "ai_interview":
        lines += [
            "",
            "1. 链接在有效期内可随时开始，仅限本人使用，请勿转发；",
            "2. 建议使用 Chrome / Edge，并允许浏览器使用麦克风；",
            "3. 进入后按 AI 数字人面试官引导作答，支持语音或文字回答。",
        ]
    else:
        lines += [
            "",
            "1. 链接在有效期内仅可本人使用，请勿转发；",
            "2. 笔试需打开摄像头，系统将进行作弊检测；",
            "3. 面试请提前 5 分钟进入面试间，测试摄像头与麦克风。",
        ]
    lines += ["", "—— AI 人才平台"]
    return subject, "\n".join(lines)


async def send_email(to: str, subject: str, body: str) -> dict[str, Any]:
    """真实 SMTP 发送；未启用或缺少配置时失败，不记录邀约令牌或模拟成功。"""
    if not to:
        return {"channel": "email", "ok": False, "mode": "smtp", "error": "无收件邮箱"}
    if not settings.EMAIL_ENABLED or not settings.SMTP_HOST or not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        return {"channel": "email", "ok": False, "mode": "smtp", "error": "邮件发送未启用或 SMTP 配置不完整"}
    try:
        def _sync_send() -> None:
            msg = EmailMessage()
            msg["From"] = settings.SMTP_USER
            msg["To"] = to
            msg["Subject"] = subject
            msg.set_content(body)
            if settings.SMTP_USE_SSL:
                import ssl
                with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT,
                                      timeout=10, context=ssl.create_default_context()) as client:
                    client.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                    client.send_message(msg)
            else:
                with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as client:
                    if not settings.SMTP_TLS:
                        raise RuntimeError("SMTP 未启用 TLS/SSL，拒绝明文发送凭据")
                    import ssl
                    client.starttls(context=ssl.create_default_context())
                    client.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                    client.send_message(msg)

        await asyncio.to_thread(_sync_send)
        return {"channel": "email", "ok": True, "mode": "smtp", "error": None}
    except Exception as exc:  # noqa: BLE001
        logger.warning("邮件发送失败 type=%s", type(exc).__name__)
        return {"channel": "email", "ok": False, "mode": "smtp", "error": "邮件服务发送失败，请检查 SMTP 配置"}


async def send_sms(to: str, body: str) -> dict[str, Any]:
    """当前未接入真实短信网关，明确返回未发送，禁止 mock 成功。"""
    return {"channel": "sms", "ok": False, "mode": "unconfigured",
            "error": "无手机号" if not to else "短信网关尚未配置，未发送短信"}
