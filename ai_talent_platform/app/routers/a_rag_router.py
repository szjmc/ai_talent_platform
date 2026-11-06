# 旧上传 URL 复用知识库上传处理函数，Swagger 由知识库入口统一展示。
"""旧文件上传地址的兼容转接，统一使用知识库草稿流程。
标签：[RAG组] [P1]。"""
from fastapi import APIRouter
from app.routers.knowledge_router import upload_document
router=APIRouter(prefix='/files',tags=['知识库'])
# 保留旧URL供调用方迁移；复用相同处理函数、权限和事务依赖。
# Swagger只展示知识库的/docs/upload，避免两个行为不同的上传入口。
router.add_api_route('/upload', upload_document, methods=['POST'], include_in_schema=False)
