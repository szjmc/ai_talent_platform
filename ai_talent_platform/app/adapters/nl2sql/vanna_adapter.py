"""Vanna 问数适配器：训练记忆 → 生成 → 校验/权限 → 受控只读执行 → 图表/解读。

标签：[Vanna组] [P0]  依赖：SQLValidator + PgSQL 只读连接（ask -> SQL -> 只读执行 -> 图表）

流程：
    用户问题
      -> Vanna generate_sql(question)
      -> sql_validator.validate_select_sql（必须，见 adapters/nl2sql/sql_validator）
      -> 注入行级数据范围（非管理员，build_scope_clause，fail-closed）
      -> pg_session 只读执行（强制 LIMIT）
      -> infer_chart 图表配置（时间->折线、枚举->柱状、占比->饼图）
      -> insight 数据解读（启发式；LLM 解读由调用方按需追加）

实现要点：
- 训练数据（DDL、示例 SQL、业务术语）在 scripts/train_vanna.py 中导入；
- 分析类大查询应走 PgSQL 分析库（settings 增加 PGSQL_* 配置），不要打 MySQL 主库；
- 图表配置统一输出为 {type, x_field, y_field, title, series}，前端用 ECharts 渲染；
- 数据权限：生成 SQL 后追加角色数据范围约束，杜绝越权查询。
待实现清单：
    - [ ] VannaAdapter.ask(question, user_scope) -> NL2SQLResponse
    - [ ] 训练数据导入脚本
    - [ ] 图表类型推断（时间->折线、枚举->柱状、占比->饼图）
=========================================================================
Vanna 0.7 的同步检索/训练在线程执行，LLM 经项目异步门面调用；不使用
Vanna.ask、run_sql 或生成的 Python/Plotly 代码。外部依赖延迟加载。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import time
from concurrent.futures import TimeoutError as FutureTimeout
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.adapters.nl2sql.chart_builder import build_chart
from app.adapters.nl2sql.sql_validator import (
    checked_scope, inject_data_scope, inject_limit, validate_select_sql,
)
from app.ai.core.llm_manager import llm_manager
from app.config.prompt_config import NL2SQL_INITIAL_PROMPT, NL2SQL_INSIGHT_PROMPT
from app.config.settings import settings
from app.core.exceptions import AIError, BusinessException, ParamError
from app.infrastructure.database.analytics_query import ensure_query_enabled, execute_readonly
from app.schemas.analytics import ChartConfig, ChartOptions, InsightDetail, NL2SQLResponse

logger = logging.getLogger(__name__)
# 同一进程最多允许 5 个问数请求进入生成/查询/解读流程，避免同时压满外部资源。
_QUERY_SLOTS = asyncio.Semaphore(5)


class LocalTextEmbedding:
    """离线字符二元组哈希向量：MVP 的可重复词面召回，不下载模型/调用付费嵌入。"""

    def __call__(self, input):
        """接收文本列表，逐条生成 256 维单位向量，供 Chroma 比较词面相似度。

        这是确定性的本地哈希映射，不是大模型训练得到的语义嵌入。
        """
        vectors = []
        for text in input:
            vector = [0.0] * 256
            # 忽略大小写和空白，让同样内容得到一致的检索向量。
            text = re.sub(r"\s+", "", text.lower())
            # 按相邻两个字符切片，中文也可直接参与；每个切片映射到一个固定桶。
            for token in ([text[i:i + 2] for i in range(max(1, len(text) - 1))]):
                value = int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big")
                vector[value % 256] += 1.0
            # 归一化使文本长度不直接决定向量大小；空文本用 1 避免除零。
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / norm for value in vector])
        return vectors

    @staticmethod
    def name():
        """向 Chroma 标识嵌入算法版本；更换算法后应重新建立训练记忆。"""
        return "vanna-mvp-local-bigrams-v1"

    def embed_query(self, input):
        """查询侧接口：把当前问题映射到与训练语料相同的向量空间。"""
        return self(input)

    def embed_documents(self, input):
        """文档侧接口：把训练语料映射成检索向量。"""
        return self(input)


@lru_cache(maxsize=1)
def _vanna_engine(memory_path: str):
    """按记忆目录创建 Vanna + Chroma，并缓存最近使用的一个实例。

    memory_path 是语料持久化目录，训练和问数必须指向同一位置。
    延迟导入使未部署 Vanna 的环境仍能启动应用，实际调用时再报依赖错误。
    """
    try:
        from vanna.chromadb import ChromaDB_VectorStore
    except ImportError as exc:
        raise AIError("NL2SQL 依赖未就绪：请安装项目声明的 vanna / chromadb") from exc

    class ProjectVanna(ChromaDB_VectorStore):
        """复用 Vanna 检索/提示词组装，将模型请求转接到项目 LLMManager。"""

        # 实现 Vanna 消息协议：分别表示系统规则、用户输入和历史回答。
        def system_message(self, message):
            return {"role": "system", "content": message}

        def user_message(self, message):
            return {"role": "user", "content": message}

        def assistant_message(self, message):
            return {"role": "assistant", "content": message}

        def log(self, message, title="Info"):
            """覆盖默认日志，避免打印完整提示词与查询内容。"""
            # Vanna 默认会打印完整 prompt/SQL；不记录用户数据。
            return None

        def submit_prompt(self, prompt, **kwargs):
            """同步 Vanna 的模型回调：把请求交给主事件循环，等待模型文本。

            由 _generate 的工作线程调用；主事件循环继续处理异步模型请求。
            不能在主事件循环线程直接调用此同步等待路径。
            """
            future = asyncio.run_coroutine_threadsafe(llm_manager.chat(
                prompt, purpose="sql", user_id=kwargs.get("user_id"),
                trace_id=kwargs.get("trace_id"),
            ), kwargs["event_loop"])
            try:
                return future.result(timeout=settings.NL2SQL_TIMEOUT_SECONDS).content
            except FutureTimeout as exc:
                future.cancel()
                raise AIError("SQL 生成超时，请重试") from exc

    # PostgreSQL 决定生成语法；每类语料最多召回 5 条，提示词放在公共配置中。
    return ProjectVanna(config={
        "path": memory_path, "dialect": "PostgreSQL", "language": "Chinese",
        "embedding_function": LocalTextEmbedding(), "n_results": 5,
        "initial_prompt": NL2SQL_INITIAL_PROMPT,
    })


class VannaAdapter:
    """单轮问数；可注入 engine/executor 用于离线契约测试。"""

    def __init__(self, *, engine: Any = None, executor=None) -> None:
        """保存可替换的引擎/执行器，默认使用本地训练记忆和 PgSQL 只读执行器。"""
        self._injected_engine = engine
        self._executor = executor or execute_readonly

    def _engine(self):
        """优先取注入引擎，否则惰性创建；初始化失败统一转为业务错误。"""
        try:
            return self._injected_engine if self._injected_engine is not None else _vanna_engine(
                str(Path(settings.VANNA_MEMORY_PATH).resolve())
            )
        except BusinessException:
            raise
        except Exception as exc:
            raise AIError("Vanna 记忆库初始化失败，请检查依赖和持久化目录") from exc

    async def ask(self, question: str, *, user_scope=None, max_rows: int = 200,
                  with_insight: bool = True, chart_type: str = "auto",
                  chart_options: ChartOptions | None = None) -> NL2SQLResponse:
        """问数总控：生成 → 校验 → 权限过滤 → 限行 → 执行 → 图表/解读。

        question：自然语言问题；user_scope：后端认证用户的身份/角色/部门字典。
        max_rows：请求行数，与服务端上限取较小值；with_insight：是否额外解读。
        chart_type：图表类型，auto 表示交给 chart_builder 按数据推断。
        chart_options：主题/尺寸/配色等外观选项，只透传，不影响取数。
        返回 NL2SQLResponse；参数、权限、SQL 或依赖失败时抛对应业务异常。
        SQL 失败即终止，不绕过校验器重试执行，也不改查 MySQL。
        """
        # 1. 先验证输入、可信身份和分析库配置，避免无效请求进入模型调用。
        question = (question or "").strip()
        if not 1 <= len(question) <= 500:
            raise ParamError("问题长度必须为 1—500 个字符")
        if type(max_rows) is not int or not 1 <= max_rows <= 1000:
            raise ParamError("max_rows 必须在 1—1000 之间")
        scope = checked_scope(user_scope)
        ensure_query_enabled()
        # 客户端只可收紧行数，不能突破服务端统一设置的上限。
        max_rows = min(max_rows, settings.NL2SQL_MAX_ROWS)
        started = time.perf_counter()
        try:
            # 总超时还包含等待并发名额的时间，不能让请求无期限排队。
            async with asyncio.timeout(settings.NL2SQL_TIMEOUT_SECONDS):
                async with _QUERY_SLOTS:
                    # 2. 只生成 SQL，不允许 Vanna 自行探测数据库或执行绘图代码。
                    sql = await self._generate(question, scope)
                    # 3. 模型输出不可信：先检查结构，再给每个表引用注入权限范围。
                    sql = validate_select_sql(sql)
                    sql = inject_data_scope(sql, scope)
                    # 4. 最外层限制结果行数，并对改写后的完整 SQL 再校验一次。
                    sql = inject_limit(sql, max_rows)
                    sql = validate_select_sql(sql)
                    # 5. 执行器只接收已受控 SQL；columns 与 rows 的列序对应。
                    columns, rows = await self._executor(sql, max_rows)
                    # 6. 图表仅依据已授权查询结果推断，不再次访问数据库。
                    chart, chart_warnings = self._infer_chart(
                        question, columns, rows, chart_type=chart_type,
                        options=chart_options, sql=sql,
                    )
                    result = NL2SQLResponse(question=question, sql=sql, columns=columns,
                                            rows=rows, chart=chart,
                                            warnings=list(chart_warnings))
                    if with_insight:
                        # 解读失败仍保留已成功查得的表格/图表，响应显式标明。
                        try:
                            result.insight_detail = await self._summarize(question, columns, rows, scope)
                            result.insight = _insight_text(result.insight_detail)
                        except BusinessException:
                            result.warnings.append("数据查询成功，AI 解读暂不可用")
                    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
                    return result
        except TimeoutError as exc:
            raise AIError("问数超时，请缩小时间/数据范围后重试") from exc
        except BusinessException:
            raise
        except Exception as exc:
            raise AIError("问数服务暂不可用，请检查训练记忆和分析库") from exc
        finally:
            # 成功和失败都记录身份、范围和耗时；不记录问题、SQL 或数据内容。
            logger.info("NL2SQL user_id=%s scope=%s elapsed_ms=%s",
                        scope["user_id"], scope["data_scope"], int((time.perf_counter() - started) * 1000))

    async def _generate(self, question, scope):
        """检索训练记忆并生成 SQL 文本；返回值尚未校验，不能直接执行。

        同步检索在线程运行；event_loop 供 submit_prompt 回调异步模型门面。
        """
        loop = asyncio.get_running_loop()
        def generate():
            """工作线程入口：检索记忆并生成 SQL，不执行数据库查询。"""
            engine = self._engine()
            # 空记忆不进入真实模型调用，避免把占位配置误当作可用服务。
            if engine.get_training_data().empty:
                raise AIError("Vanna 依赖未就绪：请先导入分析训练语料")
            # False 禁止 Vanna 为生成最终 SQL 而先读取数据库的中间结果。
            return engine.generate_sql(question=question, allow_llm_to_see_data=False,
                                       event_loop=loop, user_id=scope["user_id"])
        # 避免 Chroma 检索等同步操作阻塞 FastAPI 主事件循环。
        return await asyncio.to_thread(generate)

    @staticmethod
    def _infer_chart(question, columns, rows, *, chart_type: str = "auto",
                     options: ChartOptions | None = None,
                     sql: str = "") -> tuple[ChartConfig | None, list[str]]:
        """推断图表配置：适配层对外入口，具体规则委托 chart_builder。

        返回 (配置, 警告列表)；配置为 None 表示数据不适合画图，由前端降级成表格。
        规则与优先级见 chart_builder.build_chart，本方法只负责保持适配层职责边界。
        """
        return build_chart(question, columns, rows, chart_type=chart_type,
                           options=options, sql=sql)

    async def _summarize(self, question, columns, rows, scope) -> InsightDetail:
        """用授权结果的前 20 行生成结构化中文解读，每格最多发送 200 个字符。

        空结果直接返回说明；样本不代表全量。本方法不重查库或扩大权限。
        模型按要求输出 JSON；解析失败时降级成纯文本总结，不影响数据返回。
        """
        if not rows:
            return InsightDetail(summary="当前权限及筛选范围内没有符合条件的数据。")
        # 只发送权限过滤后的有限样本；禁止把截断样本推断为全量结论。
        sample = [[str(cell)[:200] for cell in row] for row in rows[:20]]
        result = await llm_manager.chat([
            {"role": "system", "content": NL2SQL_INSIGHT_PROMPT},
            {"role": "user", "content": json.dumps({"question": question, "columns": columns,
                "sample_rows": sample, "returned_rows": len(rows), "sample_only": True}, ensure_ascii=False)},
        ], purpose="summary", user_id=scope["user_id"])
        return _parse_insight(result.content)

    def train(self, corpus: dict, *, reset: bool = False) -> dict:
        """把 DDL、口径/术语、问题-SQL 示例导入本地 Vanna 检索记忆。

        corpus：训练脚本构造的语料；reset=True 会清空当前目录的三个集合。
        返回各类导入数量；这是语料入库，不是模型微调，也不会同步业务数据。
        先验证全部示例，再允许清空/写入记忆，避免校验失败后已丢失旧记忆。
        """
        examples = corpus.get("examples") or []
        if not corpus.get("ddl") or not corpus.get("documentation") or not examples:
            raise ParamError("训练语料必须包含 DDL、口径/术语和示例问答")
        validated = [(item["question"], validate_select_sql(item["sql"])) for item in examples]
        engine = self._engine()
        # 仅显式重建时清空集合，普通导入不主动删除之前的记忆。
        if reset:
            for collection in ("sql", "ddl", "documentation"):
                engine.remove_collection(collection)
        engine.train(ddl=corpus["ddl"])
        for document in corpus["documentation"]:
            engine.train(documentation=document)
        for question, sql in validated:
            engine.train(question=question, sql=sql)
        return {"ddl": 1, "documentation": len(corpus["documentation"]), "examples": len(validated)}


# ============================================================== 解读结果解析
def _parse_insight(content: str | None) -> InsightDetail:
    """把模型输出解析成结构化解读；不是合法 JSON 时降级为纯文本总结。"""
    text = (content or "").strip()
    if not text:
        return InsightDetail(summary="模型未返回解读内容。")
    payload = _load_json_object(text)
    if payload is None:
        # 模型没按格式输出：保留原文，其余字段留空，保证前端仍有内容可展示
        return InsightDetail(summary=text[:2000])
    return InsightDetail(
        summary=str(payload.get("summary") or "").strip()[:2000],
        anomalies=_as_text_list(payload.get("anomalies")),
        causes=_as_text_list(payload.get("causes")),
        risks=_as_text_list(payload.get("risks")),
        suggestions=_as_text_list(payload.get("suggestions")),
    )


def _load_json_object(text: str) -> dict | None:
    """从模型输出里取出 JSON 对象：兼容 ```json 包裹和前后多余说明。"""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    candidate = text[start:end + 1]
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        # 模型偶尔输出尾逗号、单引号等不规范 JSON，用 json_repair 兜底
        try:
            import json_repair
            payload = json_repair.loads(candidate)
        except Exception:
            return None
    return payload if isinstance(payload, dict) else None


def _as_text_list(value: Any) -> list[str]:
    """把任意值规整成字符串列表，过滤空项，最多保留 10 条。"""
    if not isinstance(value, list):
        return []
    items = [str(item).strip() for item in value if str(item).strip()]
    return items[:10]


def _insight_text(detail: InsightDetail) -> str:
    """把结构化解读拼回一段纯文本，兼容只读 insight 字段的旧前端。"""
    parts: list[str] = []
    if detail.summary:
        parts.append(detail.summary)
    for label, values in (("异常", detail.anomalies), ("成因", detail.causes),
                          ("风险", detail.risks), ("建议", detail.suggestions)):
        if values:
            parts.append(f"{label}：" + "；".join(values))
    return "\n".join(parts)[:2000]
