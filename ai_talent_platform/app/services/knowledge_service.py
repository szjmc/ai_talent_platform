"""
模块：app/services/knowledge_service.py
职责：知识库业务服务层。

标签：[RAG组] [P0]（上传 → 切片 → 向量化 → 入库状态机，被 router 依赖）

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

- 权限过滤必须在检索前生效（先过滤 doc 范围再检索），避免越权召回；
- 索引失败要可重试，记录 index_error。
方法清单（按顺序实现）：
    - [ ] 上传文档：校验权限范围 -> 存文件 -> 建文档记录（status=indexing）
    - [ ] 索引：加载 -> 切片 -> 向量化 -> 写 Milvus -> 回写 chunk 元数据（status=indexed）
    - [ ] 检索：权限过滤 + 向量检索 + 可选重排 -> 返回 chunk 列表
    - [ ] 问答：检索 -> 组装 Prompt -> LLM -> 返回答案 + citations
    - [ ] 删除文档：删除元数据 + 删除 Milvus 向量（保持一致性）
=========================================================================
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

# 模型 / 仓库 / Schema 实现后取消注释并接通：
# from app.repositories.user_repository import UserRepository
# from app.schemas.user import UserCreate, UserUpdate

# TODO(负责人)：按上述方法清单实现。


class KnowledgeService:
    """知识库业务服务（骨架）。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        # self.repo = KnowledgeRepository + ai/rag()
