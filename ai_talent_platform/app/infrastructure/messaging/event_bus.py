"""
模块：app/infrastructure/messaging/event_bus.py
职责：进程内事件总线（解耦「业务主流程」与「副作用」）。

============================ 实现思路与代码要点 ============================
使用场景：
- 人才创建成功 -> 触发画像生成、向量化、审计
- 简历解析完成 -> 触发人才档案更新、通知
- 测评提交 -> 触发 AI 研判、培训推荐

接口设计：
    subscribe(event_name, handler)          注册处理器
    publish(event_name, payload)            发布事件（异步、非阻塞）
    publish_background(event_name, payload) 通过 BackgroundTasks 在当前请求结束后执行

实现要点：
- 处理器异常必须被捕获并记录，绝不能影响主流程；
- 单进程内实现（asyncio.create_task）；多实例场景后续替换为 Redis Stream / Kafka；
- 事件名统一常量：EVENT_TALENT_CREATED / EVENT_RESUME_PARSED / ...

待实现清单：
    - [ ] EventBus 类（subscribe / publish / publish_background）
    - [ ] 全局 event_bus 单例
    - [ ] 事件常量定义
=========================================================================
"""

from __future__ import annotations

# TODO(负责人)：按上述设计实现。
