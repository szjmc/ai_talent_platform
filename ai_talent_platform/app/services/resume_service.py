# 上传原件、后台解析和绑定人才分别处理。解析状态用于轮询，绑定目标必须是已有档案。
"""简历业务编排：上传、访问隔离、解析状态及人才绑定。
标签：[RAG组] [P0]。"""
import asyncio,tempfile,logging
from pathlib import Path
from datetime import datetime
from app.repositories.resume_repository import ResumeRepository
from app.models.resume import Resume
from app.models.talent import Talent
from app.services.file_service import FileService
from app.utils.file_utils import get_file_hash
from app.utils.resume_data import extract_parsed, extract_skills, merge_skill_tags
from sqlalchemy.exc import IntegrityError
from app.core.exceptions import PermissionDeniedError,NotFoundError,DuplicateError,DataConflictError
from app.ai.rag.document_loader import load
from app.schemas.resume import ParsedResume,ResumeParseResult

logger=logging.getLogger(__name__)

class ResumeService:
    def __init__(self,db):
        self.db=db;self.repo=ResumeRepository();self.files=FileService(db)
    @staticmethod
    def check_owner(resume,user):
        # 管理员可管理全部；其他角色只访问自己的上传，不凭file_hash泄露他人简历。
        if not user.is_superuser and 'admin' not in user.role_codes and resume.created_by!=user.id:
            raise PermissionDeniedError('无权访问该简历')
    async def get(self,resume_id,user):
        row=await self.repo.get_by_id_or_404(self.db,resume_id)
        self.check_owner(row,user)
        return row
    async def upload(self,data,filename,content_type,user):
        existing=await self.repo.get_by_hash(self.db,get_file_hash(data))
        if existing:
            self.check_owner(existing,user)
            if existing.is_deleted: raise DuplicateError('相同简历已归档，请联系管理员恢复')
            return existing,True
        file=await self.files.upload(data,filename,content_type,user.id,'resume',biz_type='resume')
        row=Resume(file_id=file.id,file_name=file.file_name,file_type=file.extension,file_size=len(data),file_hash=file.file_hash,created_by=user.id,status='uploaded')
        try:
            self.db.add(row);await self.db.flush()
        except Exception as exc:
            from app.adapters.storage.base_storage import get_storage
            await get_storage(file.storage_type).delete(file.file_path)
            if isinstance(exc, IntegrityError):
                raise DuplicateError("相同简历正在上传或已经存在") from exc
            raise
        return row,False
    async def request_parse(self,resume_id,user,force):
        row=await self.repo.lock(self.db,resume_id)
        if row is None: raise NotFoundError('简历不存在')
        self.check_owner(row,user)
        if row.status=='parsing': raise DataConflictError('简历正在解析')
        if row.status=='success' and not force:return row,False
        row.status='parsing';row.parse_error=None;row.version+=1
        await self.db.flush()
        return row,True
    async def parse_worker(self,resume_id):
        # 独立事务读取已提交的parsing状态；工作流失败也要提交failed供前端查询。
        row=await self.repo.lock(self.db,resume_id)
        if row is None or row.status!='parsing':return
        try:
            async with self.db.begin_nested():
                if row.file is None:raise NotFoundError('原文件不存在')
                data=await self.files.read_object(row.file)
                with tempfile.TemporaryDirectory(prefix='resume-') as temp:
                    path=Path(temp)/row.file.stored_name
                    await asyncio.to_thread(path.write_bytes,data)
                    docs=await asyncio.to_thread(load,str(path),{'file_id':row.file_id})
                    text='\n'.join(d.page_content for d in docs)
                from app.ai.workflows.resume_workflow import run_resume_workflow
                parsed=await run_resume_workflow(text)
                await self.repo.replace_experiences(self.db,row.id,parsed.work_experience)
                row.parsed_data=parsed.model_dump(mode='json');row.status='success';row.parsed_at=datetime.now();row.accuracy=None
        except Exception as exc:
            row.status='failed';row.parse_error=type(exc).__name__+': 简历解析失败，请检查文件或模型配置'
        await self.db.flush()
        # 已绑定档案的简历重解析后同步技能标签：匹配召回读的是 Talent.skill_tags，
        # 此处失败不影响解析结果本身，只记日志（不影响已提交的解析数据）。
        if row.status=='success' and row.talent_id:
            try:
                talent=await self.db.get(Talent,row.talent_id)
                if talent is not None and self._apply_skills(talent,row.parsed_data):
                    await self.db.flush()
            except Exception:
                logger.warning('简历解析后同步人才技能标签失败 resume_id=%s talent_id=%s',row.id,row.talent_id,exc_info=True)
    @staticmethod
    def _apply_skills(talent,parsed_data):
        """把解析出的技能合并进人才档案（保留已有标签，去重追加）；有变化返回 True。"""
        skills=extract_skills(parsed_data)
        if not skills:return False
        merged=merge_skill_tags(talent.skill_tags,skills)
        if merged==list(talent.skill_tags or []):return False
        talent.skill_tags=merged;talent.version+=1
        return True
    async def result(self,resume_id,user):
        row=await self.get(resume_id,user)
        # extract_parsed 兼容扁平 / 嵌套两种历史写入格式（原写法遇嵌套格式会校验失败）
        data=extract_parsed(row.parsed_data) if row.status=="success" and row.parsed_data else {}
        parsed=ParsedResume.model_validate(data) if data else None
        return ResumeParseResult(resume_id=row.id,status=row.status,parsed=parsed,accuracy=row.accuracy,warnings=parsed.warnings if parsed else [],parsed_at=row.parsed_at,error=row.parse_error)
    async def bind(self,resume_id,talent_id,user):
        row=await self.get(resume_id,user)
        if row.status!='success':raise DataConflictError('仅解析成功的简历可绑定人才')
        # 校验目标人才存在且当前用户可访问：原调用的 get_for_resume_binding 在仓库中不存在（必 500）。
        # 改用现行 talent_profile_service.TalentService.get_talent（内含 talent:read 权限 + 数据范围过滤）。
        from app.services.talent_profile_service import TalentService
        talent=await TalentService(self.db,user).get_talent(talent_id)
        row.talent_id=talent.id
        # 绑定即补齐技能标签：否则解析出的技能永远进不了匹配召回（P0-04 主链断点）
        self._apply_skills(talent,row.parsed_data)
        await self.db.flush()
        return row
