"""
模块：app/services/agent_service.py
职责：Agent 服务层（对话 / SSE 流式 / 会话查询 / 任务派发 / 总路由信息）。

标签：[Agent组] [P0]（Dify 编排分支为 [Agent组]+[Dify组] [P1] 联合评审）

============================ 实现思路与代码要点 ============================
设计（唯一调度入口链路：Router → 本服务 → supervisor_workflow → dispatch → Agent）：
1. 入口必调 set_context + TokenManager().check_quota（配额超限 → AIError 5002）；
2. 对话消息行由本服务统一落库（dispatch 期间 supervisor 在 AI 上下文打
   suppress_conversation_persist 标记，避免 BaseAgent 内部记忆双写）；
3. 连接池红线：会话/消息读写完成后 release_connection(db) 再进入图执行（LLM 调用），
   图返回后再写助手消息（session 随请求结束由 get_db 提交）；
4. chat      = run_supervisor(mode="full")（LangGraph 完整路由 → dispatch → 聚合）
   chat_stream = run_supervisor(mode="route") 先拿路由结果，再 dispatch_stream 出 token；
5. Dify 分支：settings.DIFY_APP_ID_CHAT 非空且 DifyAdapter 可用才走
   _chat_via_dify；任何失败回退本地引擎（双模式，Dify 组实现后联合评审）；
6. 任务：async=true 后台执行（session_scope 独立会话 + 上下文重建），
   否则内联执行；状态存进程表 + state_manager 断点，TTL 24h。
========================================================================
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.ai.agent import AGENT_TYPES, dispatch_stream
from app.ai.context import get_context, set_context, update_context
from app.ai.core.llm_manager import llm_manager
from app.ai.core.token_manager import TokenManager
from app.adapters.llm.base_llm import ChatMessage
from app.ai.memory.conversation_memory import ConversationMemory
from app.ai.memory.state_manager import state_manager
from app.ai.workflows.graph_builder import LANGGRAPH_AVAILABLE
from app.ai.workflows.supervisor_workflow import ROUTE_KEYWORDS, run_supervisor
from app.config.settings import settings
from app.core.exceptions import AIError, BusinessException, NotFoundError, PermissionDeniedError
from app.core.pagination import PageParams, build_page_result
from app.infrastructure.database.session import release_connection, session_scope
from app.repositories.conversation_repository import ConversationRepository
from app.schemas.agent import (
    AgentChatRequest,
    AgentChatResponse,
    AgentTaskRequest,
    AgentTaskResponse,
    TokenUsage,
)

logger = logging.getLogger(__name__)

__all__ = ["AgentService"]

_TASK_TTL_SECONDS = 24 * 3600
_TASKS: dict[str, dict[str, Any]] = {}


@dataclass
class _ChatSession:
    """一次对话的准备态（prepare/open 产物，流式与非流式共用）。"""

    conversation_id: int | None
    conversation_key: str              # 对外 conversation_id（字符串）
    message_id: int | None             # 用户消息 ID（start 事件用）
    agent_type: str
    agent: str = ""                    # 路由决策结果
    route_reason: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    thread_id: str = ""
    memory: ConversationMemory | None = None
    trace_id: str = "-"
    user: Any = None
    started_perf: float = 0.0


class AgentService:
    """Agent 服务层：所有 Agent 对外能力的唯一业务入口。"""

    def __init__(self, db: Any = None) -> None:
        self.db = db
        self.repo = ConversationRepository()

    # ================================================================ 工具
    @staticmethod
    def _validate_agent_type(agent_type: str | None) -> str:
        """归一 agent_type；不支持的类型按 dispatch 契约抛 NotFoundError。"""
        normalized = str(agent_type or "").strip().lower() or "assistant"
        if normalized in ("auto",):
            return "assistant"
        if normalized not in AGENT_TYPES:
            raise NotFoundError(f"不支持的 Agent 类型：{normalized}，支持：{list(AGENT_TYPES)}")
        return normalized

    @staticmethod
    def _conversation_key(conversation_id: int | None) -> str:
        return str(conversation_id) if conversation_id is not None else f"local-{uuid.uuid4().hex}"

    def _should_use_dify(self, agent_type: str) -> bool:
        """Dify 双模式开关：仅对话类 + Dify 应用 ID 已配置 + Adapter 可导入。"""
        if agent_type not in ("assistant", "chat"):
            return False
        if not str(settings.DIFY_APP_ID_CHAT or "").strip():
            return False
        try:
            from app.adapters.dify.dify_adapter import DifyAdapter  # noqa: F401

            return True
        except Exception:  # noqa: BLE001 - Dify 组未实现前一律本地
            return False

    async def _chat_via_dify(self, body: AgentChatRequest, current_user: Any) -> AgentChatResponse | None:
        """Dify 对话分支；不可用 / 异常 → 返回 None 回退本地 LangGraph 引擎。"""
        try:
            from app.adapters.dify import dify_adapter as dify_module

            adapter_cls = getattr(dify_module, "DifyAdapter", None)
            if adapter_cls is None:
                return None
            adapter = adapter_cls()
            chat_fn = getattr(adapter, "chat", None)
            if chat_fn is None:
                return None
            started = time.perf_counter()
            raw = await chat_fn(
                message=body.message,
                conversation_id=body.conversation_id,
                app_id=settings.DIFY_APP_ID_CHAT,
                user=str(getattr(current_user, "id", "") or ""),
            )
            if raw is None:
                return None
            if isinstance(raw, dict):
                answer = str(raw.get("answer") or raw.get("text") or "")
                citations = list(raw.get("citations") or [])
                conversation_id = str(raw.get("conversation_id") or body.conversation_id or "")
            else:
                answer, citations, conversation_id = str(raw), [], str(body.conversation_id or "")
            return AgentChatResponse(
                conversation_id=conversation_id,
                message_id=None,
                answer=answer,
                agent="dify",
                route_reason="dify",
                citations=citations,
                usage=TokenUsage(),
                elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        except Exception as exc:  # noqa: BLE001 - 双模式：Dify 失败回退本地
            logger.warning("Dify 分支失败，回退本地引擎：%s", exc)
            return None

    # ================================================================ 对话
    async def chat(self, body: AgentChatRequest, current_user: Any) -> AgentChatResponse:
        """非流式对话：LangGraph 总路由完整执行（route → dispatch → 聚合）。"""
        started = time.perf_counter()
        agent_type = self._validate_agent_type(body.agent_type)
        set_context(user=current_user)
        await TokenManager().check_quota(getattr(current_user, "id", None))

        if self._should_use_dify(agent_type):
            dify_result = await self._chat_via_dify(body, current_user)
            if dify_result is not None:
                return dify_result

        session = await self._open_chat(body, current_user, agent_type=agent_type)
        final = await run_supervisor(
            session.payload,
            db=self.db,
            user=current_user,
            thread_id=session.thread_id,
            mode="full",
        )
        return await self._finish_chat(session, final, started)

    async def prepare_chat(self, body: AgentChatRequest, current_user: Any) -> _ChatSession:
        """流式入口准备：配额 / 会话 / 用户消息落库 / 连接归还 / 路由决策（HTTP 层错误在此抛出）。"""
        agent_type = self._validate_agent_type(body.agent_type)
        set_context(user=current_user)
        await TokenManager().check_quota(getattr(current_user, "id", None))

        session = await self._open_chat(body, current_user, agent_type=agent_type)
        final = await run_supervisor(
            session.payload,
            db=self.db,
            user=current_user,
            thread_id=session.thread_id,
            mode="route",
        )
        if final.get("status") == "failed":
            raise AIError("; ".join(final.get("errors") or []) or "路由决策失败")
        session.agent = str(final.get("agent") or "assistant")
        session.route_reason = str(final.get("route_reason") or "")
        return session

    async def stream_events(self, session: _ChatSession) -> AsyncIterator[dict[str, Any]]:
        """SSE 事件流（字典形态，由 Router 序列化）：start / delta / done / error。

        - delta.data 同时带 text 与 delta 两个键（前端读 data.text，设计文档写 delta）；
        - start.data 附带 agent / route_reason 便于前端展示「AI 将自动选择智能体」；
        - 引用 citation 事件仅在结构化 Agent 可产出 citations 时出现（流式原始
          token 不解析，暂由非流式接口返回 citations）。
        """
        yield {
            "event": "start",
            "data": {
                "conversation_id": session.conversation_key,
                "message_id": session.message_id,
                "trace_id": session.trace_id,
                "agent": session.agent,
                "route_reason": session.route_reason,
            },
        }

        text_parts: list[str] = []
        try:
            # 1) 已注册 Agent：dispatch_stream（唯一调度入口）；未注册 → 降级通用流
            not_registered = False
            stream = dispatch_stream(
                session.agent,
                session.payload,
                db=self.db,
                user=session.user,
                context=None,
            )
            iterator = stream.__aiter__()
            while True:
                try:
                    chunk = await iterator.__anext__()
                except StopAsyncIteration:
                    break
                except NotFoundError:
                    not_registered = True
                    break
                if chunk is None:
                    continue
                if isinstance(chunk, dict) and "event" in chunk:
                    yield chunk
                    if chunk.get("event") == "delta":
                        text_parts.append(str((chunk.get("data") or {}).get("text") or ""))
                    continue
                text = str(chunk)
                text_parts.append(text)
                yield {"event": "delta", "data": {"text": text, "delta": text}}

            # 2) 降级：通用 LLM 流式应答
            if not_registered:
                async for text in self._fallback_stream(session):
                    text_parts.append(text)
                    yield {"event": "delta", "data": {"text": text, "delta": text}}

            answer = "".join(text_parts)
            await self._persist_assistant(session, answer, citations=[], usage=TokenUsage())
            yield {
                "event": "done",
                "data": {
                    "conversation_id": session.conversation_key,
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "model": ""},
                    "elapsed_ms": round((time.perf_counter() - session.started_perf) * 1000, 2),
                    "agent": session.agent,
                    "route_reason": session.route_reason,
                },
            }
        except BusinessException as exc:
            yield {
                "event": "error",
                "data": {"code": getattr(exc, "code", 5002), "message": str(exc.message)},
            }
        except Exception as exc:  # noqa: BLE001 - 流内未知错误 → error 事件
            logger.exception("Agent 流式对话失败")
            yield {"event": "error", "data": {"code": 5000, "message": str(exc)}}

    async def _fallback_stream(self, session: _ChatSession) -> AsyncIterator[str]:
        """Agent 未注册时的通用 LLM 流（与 supervisor 降级应答同一提示词）。"""
        from app.ai.workflows.supervisor_workflow import _DEGRADED_SYSTEM

        messages: list[ChatMessage] = [ChatMessage(role="system", content=_DEGRADED_SYSTEM)]
        messages.extend(ChatMessage.from_any(item) for item in session.history)
        ctx = get_context()
        async for text in llm_manager.stream(
            messages,
            purpose="chat",
            user_id=ctx.user_id,
            conversation_id=ctx.conversation_id,
            trace_id=ctx.trace_id,
        ):
            if text:
                yield str(text)

    # ================================================================ 内部
    async def _open_chat(
        self,
        body: AgentChatRequest,
        current_user: Any,
        *,
        agent_type: str,
    ) -> _ChatSession:
        """会话建立 + 用户消息落库 + 短期记忆 + 连接归还 + 组装图负载。"""
        conversation = None
        if body.conversation_id not in (None, ""):
            conversation = (
                await self.repo.get_by_session_key_or_id(
                    self.db, body.conversation_id, user_id=getattr(current_user, "id", None)
                )
                if self.db is not None
                else None
            )
            if conversation is None:
                if self.db is None:
                    pass  # 离线模式：忽略传入会话，走本地合成 key
                else:
                    raise NotFoundError("会话不存在")
        elif self.db is not None:
            conversation = await self.repo.create_conversation(
                self.db,
                user_id=current_user.id,
                agent_type=agent_type,
                title=str(body.message or "")[:60],
            )

        conversation_id = getattr(conversation, "id", None)
        conversation_key = self._conversation_key(conversation_id)
        if conversation_id is not None:
            update_context(conversation_id=str(conversation_id))

        message_id = None
        if self.db is not None and conversation_id is not None:
            row = await self.repo.append_message(
                self.db,
                obj_in={
                    "conversation_id": conversation_id,
                    "role": "user",
                    "content": body.message,
                },
            )
            message_id = getattr(row, "id", None)

        memory = ConversationMemory(conversation_key, window=settings.CONVERSATION_WINDOW)
        # 短期记忆立即可见（DB 行已由本服务落库，这里只写进程/Redis 记忆）
        await memory.append({"role": "user", "content": body.message}, db=None)

        # 连接池红线：进入 LLM/图执行前归还连接
        if self.db is not None:
            await release_connection(self.db)

        history = await memory.get_recent(db=None, limit=settings.CONVERSATION_WINDOW)
        trace_id = str(get_context().trace_id or "-")
        return _ChatSession(
            conversation_id=conversation_id,
            conversation_key=conversation_key,
            message_id=message_id,
            agent_type=agent_type,
            history=list(history),
            payload={
                "agent_type": agent_type,
                "message": body.message,
                "conversation_id": str(conversation_id) if conversation_id is not None else None,
                "history": history,
                "trace_id": trace_id,
            },
            thread_id=f"chat:{uuid.uuid4().hex}",
            memory=memory,
            trace_id=trace_id,
            user=current_user,
            started_perf=time.perf_counter(),
        )

    async def _finish_chat(
        self,
        session: _ChatSession,
        final: dict[str, Any],
        started: float,
    ) -> AgentChatResponse:
        """图执行完成：失败折叠为业务错误；成功则落库助手消息并组装响应。"""
        status = str(final.get("status") or "")
        errors = list(final.get("errors") or [])
        if status != "done":
            raise AIError("; ".join(errors) or "Agent 执行失败")

        answer = str(final.get("answer") or "")
        citations = list(final.get("citations") or [])
        usage_raw = dict(final.get("usage") or {})
        usage = TokenUsage(
            prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
            completion_tokens=int(usage_raw.get("completion_tokens") or 0),
            model=str(usage_raw.get("model") or ""),
        )
        message_id = await self._persist_assistant(session, answer, citations=citations, usage=usage)
        if session.memory is not None:
            await session.memory.summarize_if_needed()

        return AgentChatResponse(
            conversation_id=session.conversation_key,
            message_id=message_id,
            answer=answer,
            agent=str(final.get("agent") or session.agent or session.agent_type),
            route_reason=str(final.get("route_reason") or ""),
            citations=citations,
            usage=usage,
            degraded=bool(final.get("degraded")),
            elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    async def _persist_assistant(
        self,
        session: _ChatSession,
        answer: str,
        *,
        citations: list[dict[str, Any]],
        usage: TokenUsage,
    ) -> int | None:
        """助手消息落库（本服务统一持久化；记忆同步只写短期，避免双写）。"""
        message_id = None
        if self.db is not None and session.conversation_id is not None:
            row = await self.repo.append_message(
                self.db,
                obj_in={
                    "conversation_id": session.conversation_id,
                    "role": "assistant",
                    "content": answer,
                    "citations": citations,
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "model_name": usage.model,
                    "elapsed_ms": 0.0,
                },
            )
            message_id = getattr(row, "id", None)
        if session.memory is not None:
            await session.memory.append(
                {
                    "role": "assistant",
                    "content": answer,
                    "meta": {"citations": citations},
                },
                db=None,
            )
        return message_id

    # ================================================================ 查询
    async def list_conversations(self, current_user: Any, page_params: PageParams) -> Any:
        """当前用户的会话分页。"""
        if self.db is None:
            return build_page_result([], 0, page_params)
        return await self.repo.list_by_user(
            self.db, getattr(current_user, "id", 0), page_params
        )

    async def list_messages(
        self,
        conversation_id: Any,
        current_user: Any,
        page_params: PageParams,
    ) -> Any:
        """会话消息分页（越权由 Repository 校验 → 403/2004）。"""
        if self.db is None:
            return build_page_result([], 0, page_params)
        return await self.repo.paginate_messages(
            self.db, conversation_id, page_params, user_id=getattr(current_user, "id", None)
        )

    async def describe_routes(self) -> dict[str, Any]:
        """智能体清单与总路由表（GET /agents）。"""
        from app.ai.agent import list_agents

        registered = list_agents()  # 触发 bootstrap_ai，保证读到已注册 Agent
        return {
            "agents": sorted(AGENT_TYPES),
            "registered": list(registered),
            "route_keywords": {agent: list(keywords) for agent, keywords in ROUTE_KEYWORDS},
            "llm_route_enabled": bool(settings.AGENT_ROUTE_LLM),
            "graph_backend": "langgraph" if LANGGRAPH_AVAILABLE else "fallback",
            "max_steps": int(settings.AGENT_MAX_STEPS),
        }

    # ================================================================ 任务
    async def submit_task(self, body: AgentTaskRequest, current_user: Any) -> AgentTaskResponse:
        """任务派发：async=true 后台执行，否则内联等待结果。"""
        agent_type = self._validate_agent_type(body.agent_type)
        set_context(user=current_user)
        await TokenManager().check_quota(getattr(current_user, "id", None))

        task_id = f"task-{uuid.uuid4().hex}"
        payload = {**(body.payload or {}), "agent_type": agent_type}
        record: dict[str, Any] = {
            "task_id": task_id,
            "agent_type": agent_type,
            "user_id": getattr(current_user, "id", None),
            "status": "pending",
            "payload": payload,
            "result": None,
            "error": None,
            "created_at": datetime.now().isoformat(),
            "finished_at": None,
            "elapsed_ms": 0.0,
        }
        await self._put_task(record)

        if body.is_async:
            snapshot = {
                "user_id": getattr(current_user, "id", None),
                "username": getattr(current_user, "username", None),
                "roles": list(getattr(current_user, "role_codes", None) or []),
                "organization_id": getattr(current_user, "organization_id", None),
            }
            asyncio.create_task(self._run_task(task_id, payload, snapshot))
        else:
            await self._execute_task(record, payload, current_user, db=self.db)
            await self._put_task(record)
        return self._task_response(record)

    async def get_task_status(self, task_id: str, current_user: Any) -> AgentTaskResponse:
        """任务状态查询（越权 → 403/2004；不存在 → 404/3001）。"""
        record = await self._get_task(str(task_id or ""))
        if record is None:
            raise NotFoundError("任务不存在")
        owner_id = record.get("user_id")
        if owner_id is not None and owner_id != getattr(current_user, "id", None):
            raise PermissionDeniedError("无权访问他人任务")
        return self._task_response(record)

    async def _execute_task(
        self,
        record: dict[str, Any],
        payload: dict[str, Any],
        user: Any,
        *,
        db: Any,
    ) -> None:
        """执行一个任务并回写状态（Supervisor 全量图，thread_id=task:<id> 便于断点续跑）。"""
        record["status"] = "running"
        started = time.perf_counter()
        final = await run_supervisor(
            payload,
            db=db,
            user=user,
            thread_id=f"task:{record['task_id']}",
            mode="full",
            use_checkpoint=True,
        )
        record["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
        record["finished_at"] = datetime.now().isoformat()
        if str(final.get("status")) == "done":
            answers = list(final.get("answers") or [])
            last_result = dict((answers[-1].get("result") if answers else {}) or {})
            record["status"] = "success"
            record["result"] = {
                "answer": final.get("answer") or "",
                "usage": final.get("usage") or {},
                "agent": final.get("agent") or payload.get("agent_type"),
                "dispatch": last_result,
            }
            record["error"] = None
        else:
            record["status"] = "failed"
            record["error"] = "; ".join(final.get("errors") or []) or "Agent 执行失败"
            record["result"] = None

    async def _run_task(self, task_id: str, payload: dict[str, Any], user_snapshot: dict[str, Any]) -> None:
        """后台任务：独立会话 + 显式重建上下文（context 不跨任务自动传播身份）。"""
        record = _TASKS.get(task_id)
        if record is None:
            return
        try:
            set_context(user=user_snapshot)
            async with session_scope() as db:
                await self._execute_task(record, payload, None, db=db)
            await self._put_task(record)
        except Exception as exc:  # noqa: BLE001 - 后台失败只落状态，不能抛
            logger.exception("后台任务执行失败（%s）", task_id)
            record["status"] = "failed"
            record["error"] = str(exc)
            record["finished_at"] = datetime.now().isoformat()
            await self._put_task(record)

    # ================================================================ 任务存储
    @staticmethod
    def _task_response(record: dict[str, Any]) -> AgentTaskResponse:
        return AgentTaskResponse(
            task_id=str(record.get("task_id") or ""),
            agent_type=str(record.get("agent_type") or ""),
            status=str(record.get("status") or "pending"),
            result=record.get("result") if isinstance(record.get("result"), dict) else None,
            error=record.get("error"),
            created_at=_parse_dt(record.get("created_at")),
            finished_at=_parse_dt(record.get("finished_at")),
            elapsed_ms=float(record.get("elapsed_ms") or 0.0),
        )

    @staticmethod
    async def _put_task(record: dict[str, Any]) -> None:
        """写入任务记录：进程表 + state_manager 镜像（跨重启可查），并清理过期项。"""
        now = time.time()
        expired = [
            key
            for key, item in _TASKS.items()
            if now - float(item.get("_ts") or now) > _TASK_TTL_SECONDS
        ]
        for key in expired:
            _TASKS.pop(key, None)
        record["_ts"] = now
        _TASKS[str(record["task_id"])] = record
        try:
            await state_manager.save(f"agent-task:{record['task_id']}", record, ttl=_TASK_TTL_SECONDS)
        except Exception as exc:  # noqa: BLE001 - 镜像失败不影响主流程
            logger.warning("任务状态镜像保存失败：%s", exc)

    @staticmethod
    async def _get_task(task_id: str) -> dict[str, Any] | None:
        record = _TASKS.get(task_id)
        if record is None:
            try:
                loaded = await state_manager.load(f"agent-task:{task_id}")
                if isinstance(loaded, dict) and loaded.get("task_id"):
                    record = loaded
            except Exception as exc:  # noqa: BLE001
                logger.warning("任务状态读取失败：%s", exc)
        if record is None:
            return None
        ts = float(record.get("_ts") or 0)
        if ts and time.time() - ts > _TASK_TTL_SECONDS:
            _TASKS.pop(task_id, None)
            return None
        return record


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None
