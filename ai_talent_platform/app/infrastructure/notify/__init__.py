"""
模块：app/infrastructure/notify/__init__.py
职责：通知渠道（当前为邮件 SMTP）包出口。

标签：[架构组] [P1]（通知渠道包出口）

说明：与 app/ai/proctoring/__init__.py 同一原因补加 ——
`.gitignore` 的 `_*.py` 规则把 `__init__.py` 一并忽略了，干净克隆里本包是
命名空间包（无 __init__.py），`from app.infrastructure.notify.email_client import
send_email` 在部分导入顺序下会失败。
"""

from __future__ import annotations

from app.infrastructure.notify.email_client import send_email

__all__ = ["send_email"]
