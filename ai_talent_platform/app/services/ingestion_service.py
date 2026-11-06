# 兼容入口先核对文件记录、对象键及用户权限，再复用知识库能力。逻辑删除后的远程清理由恢复任务处理。
"""合并代码的兼容入口，统一委托既有KnowledgeService。
标签：[RAG组] [P0]。"""
from app.services.knowledge_service import KnowledgeService
from app.repositories.file_repository import FileRepository
from app.core.exceptions import PermissionDeniedError,ParamError
from app.config.settings import settings
class IngestionService(KnowledgeService):
    async def process_document(self,request,user):
        if not request.file_id.isdecimal():raise ParamError('file_id必须使用文件中心返回的MySQL文件ID')
        file=await FileRepository().get_by_id_or_404(self.db,int(request.file_id))
        if not user.is_superuser and file.uploader_id!=user.id:raise PermissionDeniedError('无权读取该文件')
        if request.bucket_name!=settings.MINIO_BUCKET or request.object_name!=file.file_path:
            raise ParamError('桶名或对象键与文件元数据不一致')
        data=await self.files.read_object(file)
        return await self.upload(data,file.file_name,file.content_type,request.category,user)

    async def delete_document(self,file_id,category,user):
        from app.repositories.rag_ingest_job import IngestJob
        from app.core.exceptions import NotFoundError
        if not file_id.isdecimal():raise ParamError('file_id必须为MySQL文件ID')
        doc=await self.repo.by_file(self.db,int(file_id))
        if doc is None or doc.category!=category:raise NotFoundError('文档不存在')
        if not user.is_superuser and doc.created_by!=user.id:raise PermissionDeniedError('无权删除此文档')
        # 先持久记录，再提交逻辑删除；恢复worker确认提交后清理向量，不提前删除远程数据。
        job=await IngestJob.start('delete:'+str(doc.id))
        self.ingest_jobs.append(job)
        await job.record(doc_id=doc.id,outcome='failed',operation='delete')
        doc.soft_delete()
        await self.db.flush()
        return {'doc_id':doc.id,'status':'deletion_pending'}
