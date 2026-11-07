"""
模块：app/schemas/file.py
职责：文件中心（/files）请求 / 响应结构。

标签：[架构组] [P1]（文件中心契约）

契约要点（与 docs/api_document.md §十八 一致）：
- 上传：multipart/form-data，字段 `file` + `biz_type`（resume / knowledge / avatar / export）；
- 响应含 `download_url` 与 `duplicated`（相同 SHA-256 秒传时返回已有记录）；
- 列表 / 详情返回元数据，**不返回**存储路径与对象键（file_path 属于内部字段）；
- 分页统一 PageResult（items / total / page / page_size / pages）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.schemas.common import BaseSchema, COMMON_ERROR_RESPONSES, ORMModel

FileBizType = Literal["resume", "knowledge", "avatar", "export", "interview", "other"]
"""业务类型白名单。

说明：数据库 files.biz_type 是自由字符串，但对外只接受白名单值，
避免前端随便传导致列表筛选与权限判断失效（历史数据里出现过 "tmp"/"test" 之类的脏值）。
"""

FILE_ERROR_RESPONSES = {
    **COMMON_ERROR_RESPONSES,
    413: {"description": "文件超过大小限制（code=5003）"},
}


class FileUploadQuery(BaseSchema):
    """上传附加参数（作为 multipart 表单字段传递，故不用 JSON body）。"""

    biz_type: FileBizType = Field("other", description="业务类型")
    biz_id: int | None = Field(None, gt=0, description="关联业务 ID（多态）")
    is_public: bool = Field(False, description="是否公开访问（默认私有，按归属人鉴权）")


class FileResponse(ORMModel):
    """文件元数据（对外视图）。"""

    id: int
    file_name: str = Field(..., description="原始文件名")
    file_size: int = Field(0, description="字节数")
    content_type: str | None = Field(None, description="MIME 类型")
    extension: str | None = Field(None, description="扩展名（含点）")
    file_hash: str | None = Field(None, description="SHA-256，可用于秒传与查重")
    storage_type: str = Field("local", description="存储后端：local/minio/oss/s3")
    biz_type: str = Field(..., description="业务类型")
    biz_id: int | None = Field(None, description="关联业务 ID")
    uploader_id: int | None = Field(None, description="上传人用户 ID")
    uploader_name: str | None = Field(None, description="上传人用户名（列表页批量补齐）")
    is_public: bool = Field(False, description="是否公开访问")
    download_count: int = Field(0, description="下载次数")
    download_url: str = Field(..., description="下载地址（后端生成，前端无需拼接）")
    preview_url: str | None = Field(None, description="预览地址（可直接内联展示时给出）")
    duplicated: bool = Field(False, description="本次上传是否命中去重（秒传）")
    created_at: datetime | None = Field(None, description="上传时间")
    updated_at: datetime | None = Field(None, description="最后修改时间")


class FileQuery(BaseSchema):
    """文件列表筛选条件。"""

    biz_type: FileBizType | None = Field(None, description="按业务类型筛选")
    biz_id: int | None = Field(None, gt=0, description="按关联业务 ID 筛选")
    uploader_id: int | None = Field(None, gt=0, description="按上传人筛选")
    content_type: str | None = Field(None, max_length=128, description="按 MIME 类型前缀筛选，如 image/")
    keyword: str | None = Field(None, max_length=64, description="文件名 / 哈希模糊搜索")


class FileBatchUploadItem(BaseSchema):
    """批量上传的单条结果（成功给元数据，失败给原因，不中断其余文件）。"""

    file_name: str = Field(..., description="原始文件名")
    ok: bool = Field(..., description="是否成功")
    file: FileResponse | None = Field(None, description="成功时的文件元数据")
    error: str | None = Field(None, description="失败原因")
    code: int | None = Field(None, description="失败业务码")


class FileBatchUploadResponse(BaseSchema):
    """批量上传汇总。"""

    total: int = Field(0, description="提交文件数")
    success_count: int = Field(0, description="成功数")
    failed_count: int = Field(0, description="失败数")
    items: list[FileBatchUploadItem] = Field(default_factory=list, description="逐份结果")


class FileStatsResponse(BaseSchema):
    """文件中心概览（首页卡片 / 列表页统计条）。"""

    total_files: int = Field(0, description="文件总数")
    total_size: int = Field(0, description="总占用字节数")
    by_biz_type: dict[str, int] = Field(default_factory=dict, description="按业务类型计数")


class FileDeleteResponse(BaseSchema):
    """删除结果（软删元数据；物理对象延迟清理）。"""

    id: int
    deleted: bool = Field(True, description="是否已删除")
    message: str = Field("", description="补充说明")


class FileUploadForm(BaseSchema):
    """仅供文档/测试引用：multipart 表单字段声明（FastAPI 用 Form 显式声明）。"""

    biz_type: FileBizType = Field("other")
    biz_id: int | None = Field(None, gt=0)
    is_public: bool = Field(False)

    @field_validator("biz_type", mode="before")
    @classmethod
    def _normalize(cls, value: object) -> object:
        """空串按 other 处理（表单未选择时浏览器会传空串）。"""
        if value is None or (isinstance(value, str) and not value.strip()):
            return "other"
        return value
