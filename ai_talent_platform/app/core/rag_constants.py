# 入库和检索共享分类映射。修改分类或集合配置时需要同时考虑历史向量的可检索性。
# app/constants/rag.py
r"""RAG 知识类别常量：A / B / C 三方共用的唯一事实来源。

注意：category 会写入 Milvus 并被正则 ^[a-zA-Z0-9_\-]+$ 校验，
因此必须是英文 slug，中文名称只用于前端展示。

标签：[RAG组] [P1]
"""
from typing import Literal

# 人才库只存简历；岗位描述属于可复用的岗位知识，放入知识库。
TALENT_CATEGORIES = ("resume",)
# 测评库
ASSESSMENT_CATEGORIES = ("assessment",)
# 知识库（岗位描述 / 制度 / 课件 / 案例 / 培养方案）
KNOWLEDGE_CATEGORIES = ("job_description", "policy", "training", "case", "development_plan")

# 全部合法 category
ALL_CATEGORIES = TALENT_CATEGORIES + ASSESSMENT_CATEGORIES + KNOWLEDGE_CATEGORIES

# 给 Pydantic Literal 用
CategoryLiteral = Literal[
    "resume", "job_description",
    "assessment",
    "policy", "training", "case", "development_plan",
]

# slug → 中文展示名（供接口返回、日志、前端下拉使用）
CATEGORY_LABELS: dict[str, str] = {
    "resume": "人才简历库",
    "job_description": "岗位说明书库",
    "assessment": "测评标准",
    "policy": "企业人才制度",
    "training": "培训课件",
    "case": "历史项目案例",
    "development_plan": "人才培养方案",
}


def collection_for_category(category: str) -> str:
    """存储和检索共用的唯一分类映射，集合名来自配置。"""
    from app.config.settings import settings
    from app.core.exceptions import ParamError
    if category in TALENT_CATEGORIES:
        return settings.MILVUS_COLLECTION_TALENT
    if category in ASSESSMENT_CATEGORIES:
        return settings.MILVUS_COLLECTION_ASSESSMENT
    if category in KNOWLEDGE_CATEGORIES:
        return settings.MILVUS_COLLECTION_KNOWLEDGE
    raise ParamError('不支持的文档分类，请使用约定的7种分类标识')
