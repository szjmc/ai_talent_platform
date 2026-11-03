"""
模块：app/models/audit.py
职责：操作审计日志表（安全合规必需）。

============================ 实现思路与代码要点 ============================
表设计：audit_logs
- user_id、username、action(create/update/delete/login/export/ai_call)、
  module(talent/position/...)、target_type、target_id、
  request_method、request_path、request_ip、user_agent、trace_id、
  before_data(JSON)、after_data(JSON)、status(success/fail)、error_message、created_at
关键思路：
- 只增不改：不提供 update/delete 接口，按月归档；
- 敏感字段（密码、身份证）入库前必须脱敏；
- AI 调用也记审计（action=ai_call），记录 model、token 消耗、耗时；
- 写入审计不要阻塞主流程：可在 Service 中异步写入（事件总线/后台任务）。
待实现清单：
- [ ] 模型字段与索引（user_id、created_at、module）
- [ ] AuditRepository：list_with_filters、count_by_action
- [ ] AuditService：log(...) 统一入口 + 中间件自动记录关键接口
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现 AuditLog 模型，并在 app/models/__init__.py 注册。
