"""
模块：app/repositories/talent_repository.py
职责：人才档案数据访问层。

标签：[RAG组] [P0]（get_by_mobile / search_by_skills / count_by_organization / stats_*）

============================ 实现思路与代码要点 ============================
本仓库继承 BaseRepository，只需声明：
    model = <Model>
    filterable_fields = {...}      # 允许精确筛选的字段（白名单，防注入）
    searchable_fields = {...}      # 关键字模糊搜索字段
    sortable_fields = {...}        # 可排序字段白名单
    default_order = "-id"

- 向量检索流程：Milvus 召回 talent_id 列表 -> 本仓库 get_by_ids 回表 -> 保持得分排序；
- 统计类方法返回 dict 列表（键名与 schemas/analytics.py 对应）；
- 敏感字段（手机号/身份证）在 Schema 层脱敏，不在仓库层处理。
方法清单（按顺序实现，勾选表示已完成）：
    - [ ] get_by_mobile(db, mobile)                      手机号查重
    - [ ] get_by_ids(db, ids)                            批量查询（向量检索回表）
    - [ ] search_by_skills(db, skills)                   技能包含筛选（JSON）
    - [ ] count_by_organization(db, org_ids)             部门人才分布统计
    - [ ] stats_by_education(db)                         学历分布（分组统计）
    - [ ] stats_by_month(db, year)                       月度新增趋势

实现约束：
- 不写业务判断（是否允许操作由 Service 决定）；
- 不 commit / rollback（事务由 core 的依赖与 transaction 管理）；
- 聚合统计用 SQLAlchemy func，不要在 Python 中循环统计；
- 返回 ORM 对象，序列化交给 Schema。
=========================================================================
"""

"""gxd-人才档案、画像、权限范围与合并的数据访问；不提交事务。"""
from sqlalchemy import select, or_, false, func, update
from app.models.talent import Talent, TalentPortrait
from app.models.organization import Organization
from app.models.resume import Resume
from app.models.audit import AuditLog
from app.repositories.base import BaseRepository

class TalentRepository(BaseRepository[Talent]):
    model = Talent
    filterable_fields = {"education", "organization_id", "status", "mobile"}
    searchable_fields = {"name", "mobile", "current_position"}
    sortable_fields = {"id", "created_at", "work_years", "name"}

    def scope(self, context):
        """gxd-将可信身份转为数据库过滤条件，匿名拒绝、部门含下级按组织路径过滤。"""
        if not context.user_id:
            return false()
        if context.is_super_admin or context.data_scope == "all":
            return Talent.id > 0
        if context.data_scope == "self":
            return Talent.created_by == context.user_id
        if not context.organization_id:
            return false()
        if context.data_scope == "dept":
            return Talent.organization_id == context.organization_id
        path = select(Organization.path).where(Organization.id == context.organization_id, Organization.is_deleted.is_(False)).scalar_subquery()
        ids = select(Organization.id).where(Organization.is_deleted.is_(False), or_(Organization.id == context.organization_id, Organization.path.like(func.concat(path, str(context.organization_id), "/%"))))
        return Talent.organization_id.in_(ids)

    async def get_scoped(self, db, obj_id, context, lock=False):
        """gxd-在权限范围内查询人才，可锁定记录防止并发覆盖。"""
        stmt = self._base_query().where(Talent.id == obj_id, self.scope(context))
        if lock:
            stmt = stmt.with_for_update()
        return (await db.execute(stmt)).scalar_one_or_none()

    async def get_by_ids(self, db, ids, context=None):
        """gxd-批量回表并过滤已删除档案，可附加用户数据范围。"""
        stmt = self._base_query().where(Talent.id.in_(ids))
        if context:
            stmt = stmt.where(self.scope(context))
        return list((await db.execute(stmt)).scalars())

    async def get_by_mobile(self, db, mobile):
        """gxd-按手机号查询有效档案，用于唯一性检查，不向外直接返回跨范围资料。"""
        if not mobile:
            return None
        return (await db.execute(self._base_query().where(Talent.mobile == mobile))).scalar_one_or_none()

    async def filtered(self, db, context, filters, ids=None):
        """gxd-按可信范围及结构化条件筛选人才；不使用向量分数替代年限条件。"""
        stmt = self._base_query().where(self.scope(context))
        if ids is not None:
            stmt = stmt.where(Talent.id.in_(ids))
        for name in ("education", "organization_id", "status"):
            if filters.get(name) is not None:
                stmt = stmt.where(getattr(Talent, name) == filters[name])
        if filters.get("work_years_min") is not None:
            stmt = stmt.where(Talent.work_years >= filters["work_years_min"])
        if filters.get("work_years_max") is not None:
            stmt = stmt.where(Talent.work_years <= filters["work_years_max"])
        if filters.get("skill"):
            stmt = stmt.where(func.json_contains(Talent.skill_tags, func.json_quote(filters["skill"])))
        return list((await db.execute(stmt.order_by(Talent.id))).scalars())

    async def save(self, db, obj):
        """gxd-保存业务对象并刷新主键，事务由调用方公共会话管理。"""
        db.add(obj)
        await db.flush()
        return obj

    async def latest_portrait(self, db, talent_id):
        """gxd-获取最新有效画像版本。"""
        return (await db.execute(select(TalentPortrait).where(TalentPortrait.talent_id == talent_id, TalentPortrait.is_deleted.is_(False)).order_by(TalentPortrait.version.desc()).limit(1))).scalar_one_or_none()

    async def record_merge(self, db, user_id, target_id, before, after):
        """gxd-复用现有审计表保存脱敏合并记录，不新增表或列。"""
        return await self.save(db, AuditLog(
            user_id=user_id, action="update", module="talent",
            target_type="talent", target_id=target_id,
            before_data=before, after_data=after, status="success",
            created_by=user_id,
        ))

    async def transfer_links(self, db, source_id, target_id):
        """gxd-迁移简历、测评、培训和匹配关联；唯一冲突交给Service回滚整个合并。"""
        from app.models.assessment import Assessment
        from app.models.training import TrainingPlan, LearningRecord
        from app.models.matching import MatchingRecord
        for model in (Resume, Assessment, TrainingPlan, LearningRecord, MatchingRecord):
            await db.execute(update(model).where(model.talent_id == source_id).values(talent_id=target_id))
        # 旧画像保留原人才编号作为历史，不伪装成主档的新画像。
        await db.flush()
