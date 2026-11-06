"""
模块：app/services/dify_service.py
职责：Dify 业务编排层——把业务请求组织成对 DifyAdapter 的调用，
      不处理 HTTP、不承载连接细节，只做参数校验与结果整理。

标签：[Dify组] [P0]
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.dify.dify_adapter import DifyAdapter
from app.core.exceptions import ParamError, DuplicateError, ExternalServiceError

from datetime import datetime
from typing import Any

from app.core.pagination import PageParams
from app.core.response import PageResult
from app.models.dify import WorkflowInfo
from app.repositories.dify_repository import WorkflowInfoRepository
from app.schemas.dify import (
    WORKFLOW_UPDATABLE_FIELDS,
    WorkflowInfoCreate,
    WorkflowInfoResponse,
    WorkflowInfoUpdate,
)

logger = logging.getLogger(__name__)


class DifyService:
    """Dify 业务编排（工作流 + 聊天，阻塞 / 流式）。"""

    def __init__(self, db: AsyncSession | None = None) -> None:
        # 调用工作流需用 db 从 workflow_info 取密钥；db 为 None 时无法解析工作流
        self.db = db

    async def _resolve_workflow(
        self, app: str, variables: dict[str, object]
    ) -> WorkflowInfo:
        """按名称取未删除的工作流记录，并按 wf_var 校验必填变量。"""
        if self.db is None:
            raise ExternalServiceError("Dify 调用缺少数据库会话，无法读取工作流配置")
        repo = WorkflowInfoRepository()
        try:
            wf = await repo.get_by_field(self.db, "workflow_name", app)
        except SQLAlchemyError as exc:
            logger.exception("读取 workflow_info 失败 app=%s", app)
            raise ExternalServiceError(f"工作流配置读取失败：{app}") from exc
        if wf is None or not wf.wf_apikey:
            raise ExternalServiceError(f"未登记或已删除的 Dify 工作流: {app}")
        self._check_required(app, wf.wf_var, variables)
        return wf

    @staticmethod
    def _check_required(
        app: str,
        var_specs: object,
        variables: dict[str, object],
    ) -> None:
        """根据 wf_var 的 required 标记校验必填项；缺失/空白/空容器抛 ParamError。"""
        if not isinstance(var_specs, dict):
            return
        missing = []
        for name, spec in var_specs.items():
            if not (isinstance(spec, dict) and spec.get("required")):
                continue
            value = variables.get(name)
            if value is None:
                missing.append(name)
            elif isinstance(value, str) and not value.strip():
                missing.append(name)
            elif isinstance(value, (list, dict)) and len(value) == 0:
                missing.append(name)
        if missing:
            logger.warning("Dify 工作流必填校验失败 app=%s 缺失=%s", app, missing)
            raise ParamError(f"缺少必填参数：{', '.join(missing)}")

    # ------------------------------------------------------- 工作流
    async def run_workflow(
        self,
        inputs: dict[str, object],
        user: str = "backend",
        *,
        app: str = "prompt",
    ) -> dict[str, object]:
        """运行工作流（阻塞），直接返回业务结果 outputs。"""
        self._validate_inputs(inputs)
        wf = await self._resolve_workflow(app, inputs)
        adapter = DifyAdapter(wf.wf_apikey, app=app)
        data = await adapter.run_workflow(inputs, user=user)
        outputs = data.get("outputs") or {}
        logger.info("Dify 工作流业务调用成功 app=%s 返回字段=%s", app, list(outputs.keys()))
        return outputs

    async def run_workflow_stream(
        self,
        inputs: dict[str, object],
        user: str = "backend",
        *,
        app: str = "prompt",
    ) -> AsyncIterator[dict[str, object]]:
        """运行工作流（流式），返回事件流，最终事件 event=workflow_finished。"""
        self._validate_inputs(inputs)
        wf = await self._resolve_workflow(app, inputs)
        adapter = DifyAdapter(wf.wf_apikey, app=app)
        return adapter.run_workflow_stream(inputs, user=user)

    # ------------------------------------------------------- 聊天
    async def chat(
        self,
        query: str,
        user: str = "backend",
        *,
        app: str = "prompt",
        conversation_id: str = "",
        inputs: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """聊天（阻塞），返回 answer / conversation_id / message_id。"""
        query = self._validate_query(query)
        effective_inputs = {**(inputs or {}), "query": query}
        wf = await self._resolve_workflow(app, effective_inputs)
        adapter = DifyAdapter(wf.wf_apikey, app=app)
        body = await adapter.chat(
            query,
            user=user,
            conversation_id=conversation_id,
            inputs=inputs,
        )
        return {
            "answer": body.get("answer", ""),
            "conversation_id": body.get("conversation_id", ""),
            "message_id": body.get("message_id", ""),
        }

    async def chat_stream(
        self,
        query: str,
        user: str = "backend",
        *,
        app: str = "prompt",
        conversation_id: str = "",
        inputs: dict[str, object] | None = None,
    ) -> AsyncIterator[dict[str, object]]:
        """聊天（流式），返回事件流，event=message_end 表示结束。"""
        query = self._validate_query(query)
        effective_inputs = {**(inputs or {}), "query": query}
        wf = await self._resolve_workflow(app, effective_inputs)
        adapter = DifyAdapter(wf.wf_apikey, app=app)
        return adapter.chat_stream(
            query,
            user=user,
            conversation_id=conversation_id,
            inputs=inputs,
        )

    # ------------------------------------------------------- 校验
    @staticmethod
    def _validate_inputs(inputs: dict[str, object]) -> None:
        if not isinstance(inputs, dict) or not inputs:
            raise ParamError("工作流入参 inputs 不能为空")

    @staticmethod
    def _validate_query(query: str) -> str:
        text = (query or "").strip()
        if not text:
            raise ParamError("提问内容不能为空")
        return text


def mask_apikey(key: str | None) -> str:
    """密钥脱敏：保留前 3 位与后 4 位。"""
    if not key:
        return ""
    if len(key) <= 8:
        return key[0] + "*" * (len(key) - 1)
    return f"{key[:3]}{'*' * 8}{key[-4:]}"


class WorkflowInfoService:
    """Dify 工作流配置服务层（与 DifyService 的「调用 Dify」职责分离）。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = WorkflowInfoRepository()

    @staticmethod
    def _to_response(obj: WorkflowInfo, *, reveal: bool = False) -> WorkflowInfoResponse:
        resp = WorkflowInfoResponse.model_validate(obj)
        if not reveal:
            resp.wf_apikey = mask_apikey(obj.wf_apikey)
        return resp

    @staticmethod
    def _validate_id(record_id: int) -> None:
        if not isinstance(record_id, int) or record_id <= 0:
            raise ParamError(f"无效的记录标识：{record_id}")

    async def create(
        self,
        data: WorkflowInfoCreate,
        *,
        operator: int | None = None,
        reveal: bool = False,
    ) -> WorkflowInfoResponse:
        if await self.repo.name_exists(self.db, data.workflow_name):
            raise DuplicateError(f"工作流名称已存在：{data.workflow_name}")
        owner = data.created_by or operator
        obj = await self.repo.create(
            self.db,
            WorkflowInfo(
                workflow_name=data.workflow_name,
                wf_apikey=data.wf_apikey,
                wf_explanation=data.wf_explanation,
                wf_var=data.wf_var,
                created_by=owner,
                updated_by=owner,
            ),
        )
        logger.info("新增工作流配置 id=%s name=%s", obj.id, obj.workflow_name)
        return self._to_response(obj, reveal=reveal)

    async def get_detail(
        self,
        record_id: int,
        *,
        include_deleted: bool = False,
        reveal: bool = False,
    ) -> WorkflowInfoResponse:
        self._validate_id(record_id)
        obj = await self.repo.get_by_id_or_404(
            self.db,
            record_id,
            include_deleted=include_deleted,
            message=f"工作流配置不存在（id={record_id}）",
        )
        return self._to_response(obj, reveal=reveal)


    async def page_list(
        self,
        page: PageParams,
        *,
        keyword: str | None = None,
        workflow_name: str | None = None,
        include_deleted: bool = False,
        reveal: bool = False,
    ) -> PageResult[WorkflowInfoResponse]:
        result = await self.repo.paginate(
            self.db,
            page,
            filters={"workflow_name": workflow_name},
            keyword=keyword,
            include_deleted=include_deleted,
        )
        result.items = [self._to_response(o, reveal=reveal) for o in result.items]
        return result

    async def update(
        self,
        record_id: int,
        data: WorkflowInfoUpdate,
        *,
        operator: int | None = None,
        reveal: bool = False,
    ) -> WorkflowInfoResponse:
        self._validate_id(record_id)
        obj = await self.repo.get_by_id_or_404(
            self.db, record_id, message=f"工作流配置不存在（id={record_id}）"
        )
        self.repo.check_version(obj, data.version)

        payload = {
            k: v
            for k, v in data.model_dump(exclude_unset=True, exclude={"version"}).items()
            if k in WORKFLOW_UPDATABLE_FIELDS
        }
        if not payload:
            raise ParamError("没有可更新的字段")

        new_name = payload.get("workflow_name")
        if new_name and new_name != obj.workflow_name:
            if await self.repo.name_exists(self.db, new_name, exclude_id=record_id):
                raise DuplicateError(f"工作流名称已存在：{new_name}")

        if "updated_by" not in payload and operator is not None:
            payload["updated_by"] = operator

        # BaseRepository.update 自动 version +1、updated_at 由 ORM onupdate 刷新
        await self.repo.update(self.db, obj, payload)
        logger.info("更新工作流配置 id=%s fields=%s", record_id, sorted(payload))
        return self._to_response(obj, reveal=reveal)

    async def delete(
        self,
        record_id: int,
        *,
        operator: int | None = None,
        hard: bool = False,
    ) -> dict[str, Any]:
        self._validate_id(record_id)
        obj = await self.repo.get_by_id_or_404(
            self.db, record_id, message=f"工作流配置不存在（id={record_id}）"
        )
        if hard:
            await self.repo.hard_delete(self.db, obj)
        else:
            payload: dict[str, Any] = {"is_deleted": True, "deleted_at": datetime.now()}
            if operator is not None:
                payload["updated_by"] = operator
            await self.repo.update(self.db, obj, payload)  # 逻辑删除同样 version +1
        logger.info("删除工作流配置 id=%s hard=%s", record_id, hard)
        return {"id": record_id, "hard": hard}


__all__ = ["DifyService"]
