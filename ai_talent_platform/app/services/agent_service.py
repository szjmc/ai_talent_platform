"""
模块：app/services/agent_service.py
职责：AI Agent 调度业务服务层。

============================ 实现思路与代码要点 ============================
Service 的定位：把「校验 + 仓库调用 + 外部能力 + 事务」组织成一次完整业务。
统一写法：
    class XxxService:
        def __init__(self, db: AsyncSession, repo: XxxRepository | None = None):
            self.db = db
            self.repo = repo or XxxRepository()

        async def create_xxx(self, data: XxxCreate, current_user: User) -> Xxx:
            # 1) 权限 / 业务规则校验（不满足抛 BusinessException 子类）
            # 2) Schema -> dict -> ORM
            # 3) 调用仓库写入
            # 4) 副作用（审计、缓存、向量化、AI 任务）
            # 5) 返回 ORM 对象（由 Router 用 Response Schema 序列化）

硬性约束：
- Service 不处理 HTTP（不接收 Request、不返回 JSONResponse）；
- Service 不写复杂 SQL（查询交给 Repository）；
- 跨模块调用只通过对方 Service 的公开方法，禁止直接引用别人的内部实现；
- 事务：默认随请求提交；需要局部事务用 infrastructure.database.transaction。

- Agent 不得绕过 Service 直接访问数据库（复用业务能力，保持权限一致）；
- SSE 接口注意设置 Cache-Control: no-cache 与 X-Accel-Buffering: no。
方法清单（按顺序实现）：
    - [ ] 对话入口：加载会话记忆 -> 选择 Agent -> 执行 -> 保存消息与 token 统计
    - [ ] 任务执行：按 agent_type 分派到对应 Agent（resume/talent/matching/assessment/training）
    - [ ] 流式输出：SSE 逐 token 推送（StreamingResponse）
    - [ ] Agent 结果落库：结构化结果写入业务表，原始输出写入会话消息
    - [ ] 配额控制：按用户统计 token，超出配额抛 AIError
=========================================================================
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

# 模型 / 仓库 / Schema 实现后取消注释并接通：
# from app.repositories.user_repository import UserRepository
# from app.schemas.user import UserCreate, UserUpdate

# TODO(负责人)：按上述方法清单实现。


class AgentService:
    """AI Agent 调度业务服务（骨架）。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        # self.repo = ai/agents/* + ConversationRepository()
