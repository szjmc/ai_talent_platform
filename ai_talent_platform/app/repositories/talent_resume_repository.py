"""
模块：app/repositories/resume_repository.py
职责：简历数据访问层。

标签：[RAG组] [P0]（get_by_hash / list_by_talent / update_parse_result）

============================ 实现思路与代码要点 ============================
本仓库继承 BaseRepository，只需声明：
    model = <Model>
    filterable_fields = {...}      # 允许精确筛选的字段（白名单，防注入）
    searchable_fields = {...}      # 关键字模糊搜索字段
    sortable_fields = {...}        # 可排序字段白名单
    default_order = "-id"

- parsed_data 为 JSON 列，结构与 ResumeParseResult.parsed 一致；
- 解析是异步流程：上传时 status=uploaded，工作流开始时=parsing，结束=success/failed；
- 同一 file_hash 重复上传直接复用已有记录（秒传）。
方法清单（按顺序实现，勾选表示已完成）：
    - [ ] get_by_hash(db, file_hash)                     秒传 / 查重
    - [ ] list_by_talent(db, talent_id)                  某人才的全部简历
    - [ ] update_parse_result(db, resume_id, parsed, accuracy)  写回解析结果
    - [ ] update_status(db, resume_id, status, error)    更新解析状态机
    - [ ] count_by_status(db)                            各状态数量（监控用）

实现约束：
- 不写业务判断（是否允许操作由 Service 决定）；
- 不 commit / rollback（事务由 core 的依赖与 transaction 管理）；
- 聚合统计用 SQLAlchemy func，不要在 Python 中循环统计；
- 返回 ORM 对象，序列化交给 Schema。
=========================================================================
"""

"""gxd-简历记录、原文和解析结果的数据访问。"""
from sqlalchemy import select, or_, false, delete
from app.models.resume import Resume, ResumeExperience
from app.models.talent import Talent
from app.models.file import File
from app.repositories.base import BaseRepository
from app.repositories.talent_profile_repository import TalentRepository

class ResumeRepository(BaseRepository[Resume]):
    model = Resume
    filterable_fields = {"status", "talent_id", "file_id", "file_hash"}
    searchable_fields = {"file_name"}
    sortable_fields = {"id", "created_at", "updated_at"}

    def scope(self, context):
        """gxd-未绑定简历仅上传者可见，绑定后同时遵循人才数据范围。"""
        if not context.user_id:
            return false()
        if context.is_super_admin or context.data_scope == "all":
            return Resume.id > 0
        allowed = select(Talent.id).where(Talent.is_deleted.is_(False), TalentRepository().scope(context))
        return or_(Resume.created_by == context.user_id, Resume.talent_id.in_(allowed))

    async def get_scoped(self, db, resume_id, context, lock=False):
        """gxd-按权限查询简历，必要时加行锁。"""
        stmt = self._base_query().where(Resume.id == resume_id, self.scope(context))
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return (await db.execute(stmt)).scalar_one_or_none()

    async def get_by_hash(self, db, file_hash, *, lock=False):
        """gxd-查询文件哈希，包括删除记录以符合现有唯一约束。"""
        stmt = select(Resume).where(Resume.file_hash == file_hash)
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return (await db.execute(stmt)).scalar_one_or_none()

    async def list_by_talent(self, db, talent_id):
        """gxd-查询人才的有效简历。"""
        return list((await db.execute(self._base_query().where(Resume.talent_id == talent_id).order_by(Resume.id.desc()))).scalars())

    async def list_by_talents(self, db, ids):
        """gxd-批量取得人才原始资料，减少RAG和画像查询次数。"""
        return list((await db.execute(self._base_query().where(Resume.talent_id.in_(ids)).order_by(Resume.id.desc()))).scalars())

    async def get_file(self, db, file_id, *, lock=False):
        """gxd-读取文件元数据，权限由已经鉴权的简历记录限定。"""
        stmt = select(File).where(File.id == file_id, File.is_deleted.is_(False))
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return (await db.execute(stmt)).scalar_one_or_none()

    async def save(self, db, obj):
        """gxd-保存简历或文件对象，不自行提交事务。"""
        db.add(obj)
        await db.flush()
        return obj

    async def update_parse_result(self, db, resume_id, parsed, accuracy=None):
        """gxd-写回解析结果；accuracy留空，不能将模型信心冒充实测准确率。"""
        from datetime import datetime
        obj = await self.get_by_id_or_404(db, resume_id)
        obj.parsed_data = parsed
        obj.accuracy = accuracy
        obj.status = "success"
        obj.parse_error = None
        obj.parsed_at = datetime.now()
        return await self.save(db, obj)

    async def update_status(self, db, resume_id, status, error=None):
        """gxd-记录解析阶段及可公开的失败提示。"""
        obj = await self.get_by_id_or_404(db, resume_id)
        obj.status, obj.parse_error = status, error
        return await self.save(db, obj)

    # ---------------------------------------------------------- 合并补充
    # 历史上 app/repositories/resume_repository.py 另有一份精简 ResumeRepository
    # （lock / replace_experiences），两处同名导致调用方各导一半、运行期 AttributeError。
    # 现统一到本类，精简文件改为 re-export 兼容。
    async def lock(self, db, resume_id):
        """按主键加行锁读取（解析流程防并发覆盖）。"""
        return await db.scalar(
            self._base_query().where(Resume.id == resume_id).with_for_update()
        )

    async def replace_experiences(self, db, resume_id, experiences):
        """整段替换简历工作经历（先删后插，不提交事务）。"""
        await db.execute(
            delete(ResumeExperience).where(ResumeExperience.resume_id == resume_id)
        )
        db.add_all([
            ResumeExperience(resume_id=resume_id, sort=i, **item.model_dump())
            for i, item in enumerate(experiences)
        ])
        await db.flush()
