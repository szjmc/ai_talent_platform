# 简历记录和解析经历的持久化集中在仓库层，由服务层协调状态。
"""简历仓库，复用CRUD，不提交事务。
标签：[RAG组] [P0]。"""
from sqlalchemy import select,delete
from app.models.resume import Resume,ResumeExperience
from app.repositories.base import BaseRepository
class ResumeRepository(BaseRepository):
    model=Resume
    filterable_fields={'status','talent_id'}
    searchable_fields={'file_name'}
    sortable_fields={'id','created_at','updated_at'}
    async def get_by_hash(self,db,file_hash):
        # 包含软删记录：哈希为全局唯一，不能再次插入相同哈希。
        return await db.scalar(select(Resume).where(Resume.file_hash==file_hash))
    async def lock(self,db,resume_id):
        return await db.scalar(self._base_query().where(Resume.id==resume_id).with_for_update())
    async def replace_experiences(self,db,resume_id,experiences):
        await db.execute(delete(ResumeExperience).where(ResumeExperience.resume_id==resume_id))
        db.add_all([ResumeExperience(resume_id=resume_id,sort=i,**item.model_dump()) for i,item in enumerate(experiences)])
        await db.flush()
