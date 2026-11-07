# 旧上传 URL 兼容层已下线：/files/upload 改由文件中心（app/routers/file_router.py）提供。
"""
旧文件上传地址（/files/upload）兼容层 —— 已移除。

标签：[RAG组] [P1]。

下线原因（接口分组治理）：
    历史上这里把 `POST /files/upload` 隐藏映射到知识库上传处理函数
    （参数是 category，返回 doc/revision 结构），而文件中心自己的
    `POST /files/upload`（参数 biz_type，返回文件元数据）在后注册，
    被这条**先注册的隐藏路由覆盖** —— 表现为：
        · Swagger「文件管理」分组只有 501 占位；
        · 按文档调用 /files/upload 传 biz_type 会 422（要有 category）；
        · 前端文件中心无法上传。
    同路径两套契约不可能共存，因此保留「文件中心」这一套（与 docs/api_document.md
    §十八 一致），知识库上传请直接用 `/api/v1/knowledge/docs/upload`。

迁移指引：
    旧：POST /api/v1/files/upload   form: file + category
    新：POST /api/v1/knowledge/docs/upload   form: file + category（行为完全一致）
    文件中心通用上传：POST /api/v1/files/upload   form: file + biz_type
"""

from __future__ import annotations

from fastapi import APIRouter

# 兼容 router 对象保留（router.py 仍按模块导入），但不再注册任何路由：
# 直接删掉会导致 `from app.routers.a_rag_router import router` 失败，
# 且历史调用方拿到的将是 ImportError 而不是清晰的 404/迁移提示。
router = APIRouter(prefix="/files", tags=["知识库"], include_in_schema=False)

__all__ = ["router"]
