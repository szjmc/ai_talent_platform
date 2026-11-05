"""
模块：app/ai/skills/resume_skill.py
职责：简历处理技能（解析 / 技能抽取 / 查重），供 Agent 调用。

标签：[架构组] [P0]  依赖：[RAG组] [P0] ResumeService + talent_vectors

============================ 实现思路与代码要点 ============================
技能方法：
    parse_resume(file_id) -> dict          解析简历，返回结构化数据
    extract_skills(text) -> list[str]      技能抽取（用于打标签）
    dedupe_resume(parsed) -> bool          与库中人才查重（向量相似度）
要点：
- Skill 是 Agent 的「工具」，内部调用 Service（保持权限与事务一致）；
- 每个技能需声明：名称 / 描述 / 入参 Schema（供 LLM function calling 使用）；
- `extract_skills` 做成**可离线运行**的词典 + 规则实现（不依赖 LLM），
  LLM 未配置时技能依然可用，配置后可叠加语义抽取（use_llm=True）。
已完成：
    - [x] 三个技能 + JSON Schema 工具描述 + 词典版技能抽取（无 LLM 依赖）
待实现（依赖方）：
    - [ ] ResumeService.parse_resume / 简历向量查重（RAG 组）
=========================================================================
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.ai.skills.base import (
    BaseSkill,
    import_dependency,
    load_skill_context,
    require_dependency,
)
from app.core.exceptions import ParamError
from app.core.permissions import PermissionEnum
from app.patterns.registry import skill_registry

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ 技能词典
# 说明：离线词典版抽取（命中即认领），LLM 可用时再叠加语义抽取。
SKILL_DICTIONARY: dict[str, tuple[str, ...]] = {
    # 编程语言
    "Python": ("python", "py"),
    "Java": ("java",),
    "JavaScript": ("javascript", "js", "es6"),
    "TypeScript": ("typescript", "ts"),
    "Go": ("golang",),
    "Rust": ("rust",),
    "C++": ("c\\+\\+",),
    "C#": ("c#", "csharp"),
    "SQL": ("sql",),
    # 后端与框架
    "FastAPI": ("fastapi",),
    "Django": ("django",),
    "Flask": ("flask",),
    "Spring Boot": ("spring boot", "springboot"),
    "MySQL": ("mysql",),
    "PostgreSQL": ("postgres", "postgresql", "pgsql"),
    "Redis": ("redis",),
    "MongoDB": ("mongodb", "mongo"),
    "Milvus": ("milvus",),
    "Elasticsearch": ("elasticsearch", "es 检索", "elk"),
    "Kafka": ("kafka",),
    "Docker": ("docker",),
    "Kubernetes": ("kubernetes", "k8s"),
    # AI / 数据
    "机器学习": ("机器学习", "machine learning"),
    "深度学习": ("深度学习", "deep learning"),
    "LLM": ("大模型", "llm", "大语言模型"),
    "LangChain": ("langchain",),
    "LangGraph": ("langgraph",),
    "RAG": ("rag", "检索增强"),
    "PyTorch": ("pytorch", "torch"),
    "TensorFlow": ("tensorflow",),
    "NLP": ("nlp", "自然语言处理"),
    "数据分析": ("数据分析", "data analysis"),
    "Pandas": ("pandas",),
    # 前端
    "Vue": ("vue",),
    "React": ("react",),
    "HTML": ("html",),
    "CSS": ("css",),
    # 能力与软技能
    "项目管理": ("项目管理", "pmp"),
    "团队协作": ("团队协作", "跨部门协作"),
    "沟通能力": ("沟通能力", "沟通技巧"),
}

def _keyword_pattern(keyword: str) -> str:
    """英文关键词加词边界，避免 MySQL 命中 SQL、Django 命中 Go 这类子串误伤。"""
    escaped = re.escape(keyword)
    if keyword.isascii() and re.match(r"^[A-Za-z0-9]", keyword):
        return rf"(?<![A-Za-z0-9_+#.]){escaped}(?![A-Za-z0-9_+])"
    return escaped


# 按关键词长度降序匹配，长词优先（如 Spring Boot 优先于 Java 之外的同名词）
_SKILL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        skill,
        re.compile(
            "|".join(sorted((_keyword_pattern(p) for p in patterns), key=len, reverse=True)),
            re.IGNORECASE,
        ),
    )
    for skill, patterns in SKILL_DICTIONARY.items()
]


def extract_skills_by_dictionary(text: str, limit: int = 30) -> list[str]:
    """词典 + 正则抽取技能标签（不依赖 LLM，纯本地计算）。"""
    if not text:
        return []
    found = [skill for skill, pattern in _SKILL_PATTERNS if pattern.search(text)]
    return sorted(found)[:limit]


async def extract_skills_by_llm(text: str, limit: int = 30) -> list[str] | None:
    """LLM 语义抽取（未配置 / 未实现时返回 None，由调用方静默退化）。"""
    try:
        manager_cls = import_dependency("app.ai.core.llm_manager", "LLMManager", hint="架构组未实现 LLMManager")
        from app.adapters.llm.provider_config import provider_configured
        from app.config.settings import settings

        # 供应商感知：DeepSeek / OpenAI / 通义各自有专属密钥配置，本地 Ollama 无需密钥
        if not provider_configured(settings.LLM_PROVIDER):
            return None
        manager = manager_cls()
        result = manager.chat(
            messages=[
                {"role": "system", "content": "你是技能抽取助手，只输出 JSON 数组，如 [\"Python\",\"MySQL\"]"},
                {"role": "user", "content": f"从下面文本中抽取技能标签（最多 {limit} 个）：\n{text[:4000]}"},
            ],
            purpose="resume",
            temperature=0.0,
        )
        if hasattr(result, "__await__"):
            result = await result
        content = getattr(result, "content", None) or (result or "")
        from app.ai.utils.output_parser import parse_json

        parsed = parse_json(str(content))
        values = parsed if isinstance(parsed, list) else parsed.get("skills", [])
        return [str(item).strip() for item in values if str(item).strip()][:limit]
    except Exception:  # noqa: BLE001 - LLM 侧任何问题都不阻塞主流程
        logger.debug("LLM 技能抽取不可用，回退词典版", exc_info=True)
        return None


# ------------------------------------------------------------------ 技能 1：解析简历
@skill_registry.register("resume_parse")
class ResumeParseSkill(BaseSkill):
    """解析简历文件，返回结构化数据。"""

    name = "resume_parse"
    description = "解析指定简历文件，返回结构化的基本信息/教育/工作经历/技能"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_id": {"type": "integer", "description": "简历文件 ID（files 表）"},
            "resume_id": {"type": "integer", "description": "简历记录 ID（resumes 表，可选）"},
        },
        "required": [],
    }

    category = "business"
    version = "1.0.0"
    required_permission = str(PermissionEnum.RESUME_PARSE)
    requires = ("app.services.resume_service.ResumeService",)
    timeout_seconds = 120.0  # 解析链路较长（OCR / LLM）

    async def execute(self, file_id: int | None = None, resume_id: int | None = None, **_: Any) -> dict[str, Any]:
        context = load_skill_context()
        if file_id is None and resume_id is None:
            raise ParamError("file_id 与 resume_id 至少提供一个")

        service_cls = import_dependency(
            "app.services.resume_service",
            "ResumeService",
            hint="RAG 组未实现简历解析服务",
        )
        service = service_cls()
        method = getattr(service, "parse_resume", None) or getattr(service, "parse", None)
        require_dependency(method is not None, "ResumeService.parse_resume", hint="RAG 组未实现简历解析")

        result = method(file_id=file_id, resume_id=resume_id, context=context)
        if hasattr(result, "__await__"):
            result = await result
        return self._shape(result, file_id, resume_id)

    @staticmethod
    def _shape(result: Any, file_id: int | None, resume_id: int | None) -> dict[str, Any]:
        if isinstance(result, dict):
            data = dict(result)
        else:
            data = {
                "basic_info": getattr(result, "basic_info", None),
                "education": getattr(result, "education", None),
                "work_experience": getattr(result, "work_experience", None),
                "projects": getattr(result, "projects", None),
                "skills": getattr(result, "skills", None),
                "accuracy": getattr(result, "accuracy", None),
            }
        parsed = data.get("parsed") if isinstance(data.get("parsed"), dict) else data
        skills = (parsed or {}).get("skills") or []
        text_blob = str(parsed or data)
        if not skills:
            skills = extract_skills_by_dictionary(text_blob)
        return {
            "file_id": file_id,
            "resume_id": resume_id,
            "parsed": parsed or data,
            "skills": skills,
        }


# ------------------------------------------------------------------ 技能 2：技能抽取
@skill_registry.register("resume_skill_extract")
class ResumeSkillExtractSkill(BaseSkill):
    """从简历文本中抽取技能标签（本地词典 + 可选 LLM 增强）。"""

    name = "resume_skill_extract"
    description = "从简历文本中抽取技能标签（如 Python / MySQL / 项目管理），用于人才打标签"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "简历原文或项目经历文本", "maxLength": 8000},
            "limit": {"type": "integer", "description": "最多返回多少个技能", "default": 30, "minimum": 1, "maximum": 50},
            "use_llm": {"type": "boolean", "description": "是否叠加 LLM 语义抽取", "default": False},
        },
        "required": ["text"],
    }

    category = "business"
    version = "1.0.0"
    required_permission = str(PermissionEnum.RESUME_PARSE)
    requires = ()  # 无外部依赖：词典版永远可用，LLM 为可选增强
    timeout_seconds = 60.0

    async def execute(self, text: str, limit: int = 30, use_llm: bool = False, **_: Any) -> list[str]:
        if not (text or "").strip():
            raise ParamError("待抽取文本不能为空")

        skills = extract_skills_by_dictionary(text, limit=limit)
        if use_llm:
            llm_skills = await extract_skills_by_llm(text, limit=limit)
            if llm_skills:
                merged = {item.lower(): item for item in skills}
                for item in llm_skills:
                    merged.setdefault(item.lower(), item)
                skills = sorted(merged.values())
        return skills[:limit]


# ------------------------------------------------------------------ 技能 3：简历查重
@skill_registry.register("resume_dedupe")
class ResumeDedupeSkill(BaseSkill):
    """简历查重：判断库中是否已存在同一人才（向量相似度）。"""

    name = "resume_dedupe"
    description = "判断简历是否与库中已有人才重复，返回最相似的人才与相似度"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "resume_id": {"type": "integer", "description": "简历记录 ID"},
            "text": {"type": "string", "description": "简历文本（不传 resume_id 时使用）", "maxLength": 8000},
            "threshold": {"type": "number", "description": "判定为重复的相似度阈值 0-1", "default": 0.92, "minimum": 0, "maximum": 1},
        },
        "required": [],
    }

    category = "business"
    version = "1.0.0"
    required_permission = str(PermissionEnum.RESUME_PARSE)
    requires = ("app.services.resume_service.ResumeService.dedupe", "Milvus: talent_vectors")
    timeout_seconds = 30.0

    async def execute(
        self,
        resume_id: int | None = None,
        text: str | None = None,
        threshold: float = 0.92,
        **_: Any,
    ) -> dict[str, Any]:
        context = load_skill_context()
        if resume_id is None and not (text or "").strip():
            raise ParamError("resume_id 与 text 至少提供一个")

        service_cls = import_dependency(
            "app.services.resume_service",
            "ResumeService",
            hint="RAG 组未实现简历查重（依赖 Milvus talent_vectors）",
        )
        service = service_cls()
        method = getattr(service, "dedupe_resume", None) or getattr(service, "dedupe", None)
        require_dependency(
            method is not None,
            "ResumeService.dedupe_resume",
            hint="RAG 组未就绪 talent_vectors 集合或未实现 ResumeService.dedupe_resume",
        )
        result = method(resume_id=resume_id, text=text, threshold=threshold, context=context)
        if hasattr(result, "__await__"):
            result = await result
        return self._shape(result, threshold)

    @staticmethod
    def _shape(result: Any, threshold: float) -> dict[str, Any]:
        if isinstance(result, bool):
            return {"duplicated": result, "matches": [], "threshold": threshold}
        if isinstance(result, dict):
            data = dict(result)
            data.setdefault("threshold", threshold)
            return data
        score = float(getattr(result, "score", 0) or 0)
        return {
            "duplicated": score >= threshold,
            "threshold": threshold,
            "matches": [
                {
                    "talent_id": getattr(result, "talent_id", None),
                    "name": getattr(result, "name", None),
                    "score": round(score, 4),
                }
            ],
        }


# ------------------------------------------------------------------ 函数式入口
async def parse_resume(file_id: int | None = None, resume_id: int | None = None) -> dict[str, Any]:
    return await ResumeParseSkill().execute(file_id=file_id, resume_id=resume_id)


async def extract_skills(text: str, limit: int = 30, use_llm: bool = False) -> list[str]:
    return await ResumeSkillExtractSkill().execute(text=text, limit=limit, use_llm=use_llm)


async def dedupe_resume(
    resume_id: int | None = None, text: str | None = None, threshold: float = 0.92
) -> dict[str, Any]:
    return await ResumeDedupeSkill().execute(resume_id=resume_id, text=text, threshold=threshold)
