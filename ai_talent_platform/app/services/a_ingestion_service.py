# 该服务承接旧上传联调流程。当前对外上传地址的实际行为以 Router 绑定为准。
"""A角色兼容门面，直接复用业务Service，不再HTTP自调用或新建存储连接。
标签：[RAG组] [P1]。"""
class A_IngestionService:
    async def upload_and_notify(self,file,category,user,service):
        from app.config.settings import settings
        data=await file.read(settings.MAX_UPLOAD_SIZE_MB*1024*1024+1)
        return await service.upload(data,file.filename or 'document.txt',file.content_type,category,user)
a_ingestion_service=A_IngestionService()
