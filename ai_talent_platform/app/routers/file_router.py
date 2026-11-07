"""
模块：app/routers/file_router.py
职责：文件中心接口（/files）：上传 / 批量上传 / 列表 / 详情 / 下载 / 删除 / 统计。

标签：[架构组] [P1]（文件管理模块的真实实现，替换原 501 占位路由）

背景（本轮修复的「接口分错组」问题）：
    /api/v1/files 之前只有 `placeholder_router` 生成的 `GET /files` 占位（返回 501），
    而真正可用的上传能力挂在了知识库分组下（/knowledge/docs/upload），
    旧地址 `/files/upload` 只是一条 `include_in_schema=False` 的兼容跳转，
    Swagger 上「文件管理」分组对外不可用 —— 前端 FilesPage 只能提示「待实现」。

契约（与 docs/api_document.md §十八 一致）：
    POST   /files/upload          上传（multipart: file + biz_type + biz_id + is_public）
    POST   /files/batch-upload    批量上传（逐份结果，部分失败不撤销成功项）
    GET    /files                 分页列表（keyword / biz_type / uploader_id / content_type）
    GET    /files/stats           概览（总数 / 总占用 / 分类计数）
    GET    /files/{file_id}       详情（含 download_url）
    GET    /files/{file_id}/download  下载（私有文件按归属人鉴权）
    DELETE /files/{file_id}       删除（软删元数据 + 延迟清理对象）

权限：
    上传：file:upload；删除：file:delete；查看 / 下载：登录 + 归属校验（公开文件人人可读）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File as FastAPIFile, Form, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser, get_db, require_permission
from app.core.pagination import PageParams, build_page_result
from app.core.permissions import PermissionEnum
from app.core.response import ApiResponse, PageResult, success
from app.schemas.common import COMMON_ERROR_RESPONSES
from app.schemas.file import (
    FileBatchUploadResponse,
    FileBizType,
    FileDeleteResponse,
    FileResponse,
    FileStatsResponse,
)
from app.services.file_center_service import FileCenterService

router = APIRouter(prefix="/files", tags=["文件管理"], responses=COMMON_ERROR_RESPONSES)


def get_file_service(db: AsyncSession = Depends(get_db)) -> FileCenterService:
    """文件中心服务依赖（请求级会话）。"""
    return FileCenterService(db)


def _content_disposition(filename: str) -> str:
    """中文文件名下载头（RFC 5987：filename* + ASCII 兜底）。"""
    from urllib.parse import quote

    ascii_name = filename.encode("ascii", "ignore").decode() or "download"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


@router.post(
    "/upload",
    summary="上传文件",
    response_model=ApiResponse[FileResponse],
    dependencies=[Depends(require_permission(PermissionEnum.FILE_UPLOAD))],
)
async def upload_file(
    user: CurrentUser,
    file: Annotated[UploadFile, FastAPIFile(..., description="待上传文件，单次一份（多份请用 batch-upload）")],
    biz_type: Annotated[FileBizType, Form(description="业务类型：resume/knowledge/avatar/export/interview/other")] = "other",
    biz_id: Annotated[int | None, Form(gt=0, description="关联业务 ID（可选）")] = None,
    is_public: Annotated[bool, Form(description="是否公开访问（默认私有）")] = False,
    service: FileCenterService = Depends(get_file_service),
):
    """上传单个文件；相同摘要同业务类型命中去重（duplicated=true，不重复落盘）。"""
    content = await file.read()
    data = await service.upload(
        filename=file.filename,
        content=content,
        content_type=file.content_type,
        biz_type=biz_type,
        biz_id=biz_id,
        is_public=is_public,
        user=user,
    )
    return success(data=data, message="文件已上传（已存在相同文件，直接复用）" if data.duplicated else "上传成功")


@router.post(
    "/batch-upload",
    summary="批量上传文件",
    response_model=ApiResponse[FileBatchUploadResponse],
    dependencies=[Depends(require_permission(PermissionEnum.FILE_UPLOAD))],
)
async def batch_upload_files(
    user: CurrentUser,
    files: Annotated[list[UploadFile], FastAPIFile(..., description="待上传文件（可多选）")],
    biz_type: Annotated[FileBizType, Form(description="整批共用的业务类型")] = "other",
    biz_id: Annotated[int | None, Form(gt=0, description="关联业务 ID（整批共用）")] = None,
    is_public: Annotated[bool, Form(description="是否公开访问")] = False,
    service: FileCenterService = Depends(get_file_service),
):
    """批量上传：逐份校验与落库，返回成功 / 失败明细（部分失败不撤销已成功文件）。"""
    payload = [(item.filename, await item.read(), item.content_type) for item in files]
    data = await service.batch_upload(
        payload, biz_type=biz_type, biz_id=biz_id, is_public=is_public, user=user
    )
    return success(
        data=data,
        message=f"上传完成：成功 {data.success_count} 份，失败 {data.failed_count} 份",
    )


@router.get(
    "/stats",
    summary="文件中心概览",
    response_model=ApiResponse[FileStatsResponse],
)
async def file_stats(
    user: CurrentUser, service: FileCenterService = Depends(get_file_service)
):
    """文件总数 / 总占用 / 分类计数（普通用户仅统计本人可见范围）。"""
    return success(data=await service.stats(user=user), message="OK")


@router.get(
    "",
    summary="文件列表（分页）",
    response_model=ApiResponse[PageResult[FileResponse]],
)
async def list_files(
    user: CurrentUser,
    page: PageParams = Depends(),
    keyword: str | None = Query(None, max_length=64, description="文件名 / 哈希模糊搜索"),
    biz_type: FileBizType | None = Query(None, description="按业务类型筛选"),
    biz_id: int | None = Query(None, gt=0, description="按关联业务 ID 筛选"),
    uploader_id: int | None = Query(None, gt=0, description="按上传人筛选（管理员）"),
    content_type: str | None = Query(None, max_length=128, description="按 MIME 前缀筛选，如 image/"),
    service: FileCenterService = Depends(get_file_service),
):
    """分页查询文件；普通用户仅本人上传（管理可见全部，公开文件对所有登录用户可见）。"""
    result = await service.page_list(
        page,
        biz_type=biz_type,
        biz_id=biz_id,
        uploader_id=uploader_id,
        content_type=content_type,
        keyword=keyword,
        user=user,
    )
    return success(
        data=build_page_result(result.items, result.total, page), message="查询成功"
    )


@router.get(
    "/{file_id}",
    summary="文件详情",
    response_model=ApiResponse[FileResponse],
)
async def get_file(
    file_id: int, user: CurrentUser, service: FileCenterService = Depends(get_file_service)
):
    """文件元数据（含 download_url / preview_url）。"""
    return success(data=await service.get(file_id, user=user), message="OK")


@router.get("/{file_id}/download", summary="下载文件")
async def download_file(
    file_id: int, user: CurrentUser, service: FileCenterService = Depends(get_file_service)
):
    """下载文件内容；私有文件仅上传人与管理员可下载。"""
    record, content = await service.prepare_download(file_id, user=user)
    return Response(
        content=content,
        media_type=record.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": _content_disposition(record.file_name),
            "Content-Length": str(len(content)),
            "X-Content-SHA256": record.file_hash or "",
        },
    )


@router.delete(
    "/{file_id}",
    summary="删除文件",
    response_model=ApiResponse[FileDeleteResponse],
    dependencies=[Depends(require_permission(PermissionEnum.FILE_DELETE))],
)
async def delete_file(
    file_id: int, user: CurrentUser, service: FileCenterService = Depends(get_file_service)
):
    """删除文件（软删元数据，物理对象由定时任务延迟清理）。"""
    await service.delete(file_id, user=user)
    return success(
        data=FileDeleteResponse(id=file_id, deleted=True, message="文件已删除（对象延迟清理）"),
        message="删除成功",
    )
