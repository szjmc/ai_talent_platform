# 技能入口复用知识检索能力，调用链须携带当前用户上下文。
"""
模块：app/ai/skills/knowledge_skill.py
职责：知识库检索技能（RAG 检索能力对 Agent / LLM 的唯一出口）。

标签：[架构组] [P0]  依赖：[RAG组] [P0] `app/ai/rag/retriever.py`

============================ 实现思路与代码要点 ============================
技能方法：
    search_knowledge(question, top_k) -> list[{content, source, score}]
要点：
- 复用 ai/rag/retriever，权限过滤使用当前用户上下文（不信任 LLM 传入的参数）；
- 返回片段需附带来源（doc_id / doc_title / page / chunk_index / milvus_pk），供答案溯源；
- 检索未命中时返回空列表并附提示，由 Agent 决定是否走 LLM 兜底，不在技能里编造内容；
- 依赖（Retriever / Milvus）未实现或未启用时抛 SkillUnavailableError，
  让上层明确提示「知识库检索暂不可用」，而不是返回空结果假装没数据。
已完成：
    - [x] search_knowledge（知识检索技能 + 权限过滤 + 结果整形 + 溯源字段）
待实现（依赖方）：
    - [ ] Retriever.hybrid_search / semantic_search（RAG 组）
=========================================================================
"""

from __future__ import annotations

import logging
from typing import Any

from app.ai.skills.base import (
    BaseSkill,
    SkillContext,
    import_dependency,
    load_skill_context,
    require_dependency,
)
from app.config.settings import settings
from app.core.permissions import PermissionEnum
from app.patterns.registry import skill_registry

logger = logging.getLogger(__name__)


def _load_retriever() -> Any:
    """延迟导入检索器：RAG 组实现后自动接通，未实现时给出明确错误。"""
    retriever_cls = import_dependency(
        "app.ai.rag.retriever",
        "Retriever",
        hint="RAG 组未实现，或 MILVUS_ENABLED=False",
    )
    return retriever_cls()


@skill_registry.register("knowledge_search")
class KnowledgeSearchSkill(BaseSkill):
    """企业知识库检索（返回可溯源片段）。"""

    name = "knowledge_search"
    description = "在企业知识库中检索与问题相关的制度/流程/政策片段，返回带来源的原文片段"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "要检索的问题或关键词",
                "maxLength": 500,
            },
            "top_k": {
                "type": "integer",
                "description": "返回片段数量",
                "default": 5,
                "minimum": 1,
                "maximum": 20,
            },
            "category": {
                "type": "string",
                "description": "限定知识分类（可选，如 制度/流程/岗位说明书）",
            },
        },
        "required": ["question"],
    }

    category = "retrieval"
    version = "1.0.0"
    required_permission = str(PermissionEnum.KNOWLEDGE_READ)
    requires = ("app.ai.rag.retriever.Retriever", "Milvus: knowledge_vectors")
    timeout_seconds = 15.0
    score_threshold = 0.0  # 低于该分数的片段丢弃（RAG 组可按效果调高）

    async def execute(self, question: str, top_k: int = 5, category: str | None = None, **_: Any) -> list[dict[str, Any]]:
        """检索知识库片段。"""
        context: SkillContext = load_skill_context()
        question = (question or "").strip()
        if not question:
            from app.core.exceptions import ParamError

            raise ParamError("检索问题不能为空")

        if not settings.MILVUS_ENABLED:
            require_dependency(
                False,
                "Milvus 向量库",
                hint="MILVUS_ENABLED=False，知识检索依赖向量库",
            )

        from app.infrastructure.database.session import session_scope
        from app.repositories.user_repository import UserRepository
        from app.ai.rag.retriever import Retriever
        require_dependency(context.user_id is not None,'认证上下文',hint='依赖未就绪：缺少登录用户')
        async with session_scope() as db:
            user=await UserRepository().get_by_id_or_404(db,context.user_id)
            from app.core.permissions import has_permission
            require_dependency(user.is_active and not user.is_deleted and (user.is_superuser or has_permission(user.role_codes,PermissionEnum.KNOWLEDGE_READ)), '知识库权限', hint='用户不可访问知识库')
            chunks=await Retriever(db).search(question,user,top_k=top_k,category=category,mode='hybrid')
        return self._shape(chunks,top_k,context)

    # ---------------------------------------------------------------- 内部实现
    async def _search(
        self,
        retriever: Any,
        question: str,
        top_k: int,
        context: SkillContext,
        category: str | None,
    ) -> list[Any]:
        """优先混合检索，退化到语义检索（不同实现暴露的方法不同，逐个适配）。"""
        scope = {
            "user_id": context.user_id,
            "roles": context.roles,
            "organization_id": context.organization_id,
            "data_scope": context.data_scope,
            "category": category,
        }
        for method_name in ("hybrid_search", "semantic_search", "search"):
            method = getattr(retriever, method_name, None)
            if method is None:
                continue
            try:
                result = method(question, top_k=top_k, scope=scope)
            except TypeError:
                result = method(question, top_k)
            if hasattr(result, "__await__"):
                result = await result
            return list(result or [])
        require_dependency(False, "Retriever.hybrid_search/semantic_search", hint="RAG 组未实现检索方法")
        return []

    def _shape(self, chunks: list[Any], top_k: int, context: SkillContext) -> list[dict[str, Any]]:
        """统一输出结构：字段精简（控上下文长度）+ 带来源（可溯源）。"""
        shaped: list[dict[str, Any]] = []
        seen: set[tuple[Any, Any]] = set()
        for chunk in chunks:
            item = self._to_dict(chunk)
            score = float(item.get("score") or 0)
            if score < self.score_threshold:
                continue
            key = (item.get("doc_id"), item.get("chunk_id", item.get("chunk_index")))
            if key in seen:
                continue
            seen.add(key)
            item["score"] = round(score, 4)
            shaped.append(item)
        shaped.sort(key=lambda item: item["score"], reverse=True)
        shaped = shaped[:top_k]
        logger.info(
            "knowledge_search 命中 %s 条（user_id=%s, org_id=%s）",
            len(shaped),
            context.user_id,
            context.organization_id,
        )
        return shaped

    @staticmethod
    def _to_dict(chunk: Any) -> dict[str, Any]:
        """兼容 ORM 对象 / dataclass / dict 三种返回形态。"""
        if isinstance(chunk, dict):
            data = dict(chunk)
        else:
            data = {
                "content": getattr(chunk, "content", "") or "",
                "doc_id": getattr(chunk, "doc_id", None),
                "doc_title": getattr(chunk, "doc_title", None)
                or getattr(chunk, "title", None),
                "page": getattr(chunk, "page", None) or getattr(chunk, "page_no", None),
                "chunk_index": getattr(chunk, "chunk_index", None),
                "milvus_pk": getattr(chunk, "milvus_pk", None),
                "score": getattr(chunk, "score", 0) or 0,
            }
        content = str(data.get("content") or "")
        if len(content) > 1200:  # 单片段截断，控制 LLM 上下文长度
            data["content"] = f"{content[:1200]}…"
            data["truncated"] = True
        return data


# 面向编排侧的便捷函数（Agent 直接调用，无需关心注册表）
async def search_knowledge(question: str, top_k: int = 5, category: str | None = None) -> list[dict[str, Any]]:
    """函数式入口：等价于 KnowledgeSearchSkill().execute(...)。"""
    return await KnowledgeSearchSkill().execute(question=question, top_k=top_k, category=category)
