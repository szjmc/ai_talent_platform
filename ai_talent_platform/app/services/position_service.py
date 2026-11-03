"""
模块：app/services/position_service.py
职责：岗位管理业务服务层。

标签：[业务组] [P0]（被 [架构组] position_search / candidate_list 技能依赖）

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

- 岗位向量化失败不应阻塞主流程：记录 warning 并提供补偿任务。
方法清单（按顺序实现）：
    - [ ] 创建岗位：code 唯一校验 + 结构化 requirements 校验
    - [ ] 更新岗位：字段白名单 + 状态流转校验（closed 不允许直接改回 open 需先 publish）
    - [ ] 岗位详情：返回结构化要求与技能标签
    - [ ] 关闭岗位：close_at 置为当前时间，停止参与匹配
    - [ ] 向量化：创建/更新后调用 ai/skills 或 adapters/vector_store 写入岗位向量（异步）
=========================================================================
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

# 模型 / 仓库 / Schema 实现后取消注释并接通：
# from app.repositories.user_repository import UserRepository
# from app.schemas.user import UserCreate, UserUpdate

# TODO(负责人)：按上述方法清单实现。


class PositionService:
    """岗位管理业务服务（骨架）。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        # self.repo = PositionRepository()
