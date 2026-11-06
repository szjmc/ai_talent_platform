"""
标签：[架构组] [P0]
模块：app/utils/exam_token.py
职责：笔试公开链接令牌（邮件笔试入口的免登录访问凭证）。

实现思路：
- 令牌 = HMAC-SHA256(JWT_SECRET_KEY, "exam:{assessment_id}") 的前 32 位十六进制；
- 无状态、可确定性校验，不需要落库；
- 候选人只能通过邮件里的完整链接（id + token）访问对应试卷，无法遍历他人测评。
"""

from __future__ import annotations

import hashlib
import hmac

from app.config.settings import settings


def make_exam_token(assessment_id: int) -> str:
    """生成指定测评的笔试公开访问令牌。"""
    digest = hmac.new(
        settings.JWT_SECRET_KEY.encode("utf-8"),
        f"exam:{assessment_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest[:32]


def verify_exam_token(assessment_id: int, token: str) -> bool:
    """校验笔试公开访问令牌（恒定时间比较，防时序攻击）。"""
    if not token:
        return False
    return hmac.compare_digest(make_exam_token(assessment_id), token)


def build_exam_url(base_url: str, assessment_id: int) -> str:
    """拼接笔试入口链接（前端站点或后端内置考试页均可，统一携带 token）。"""
    base = (base_url or "").rstrip("/")
    return f"{base}/exam/{assessment_id}?token={make_exam_token(assessment_id)}"


__all__ = ["make_exam_token", "verify_exam_token", "build_exam_url"]
