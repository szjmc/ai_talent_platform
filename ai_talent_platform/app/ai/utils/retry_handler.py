"""
模块：app/ai/utils/retry_handler.py
职责：AI 调用重试（指数退避 + 超时 + 熔断保护）。

============================ 实现思路与代码要点 ============================
1. 装饰器 @ai_retry(max_attempts=3, base_delay=0.5, retry_on=(TimeoutError, ExternalServiceError))；
2. 只重试「可恢复」错误（超时、429、5xx），业务错误（4xx/校验失败）不重试；
3. 熔断：连续失败 N 次后短时间内快速失败，避免雪崩（进程内计数即可）；
4. 每次重试记录 warning 日志（trace_id 贯通）。
待实现清单：
    - [ ] ai_retry 装饰器
    - [ ] SimpleCircuitBreaker
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
