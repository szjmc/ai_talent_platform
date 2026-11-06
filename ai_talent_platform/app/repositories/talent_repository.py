# 人才数据访问契约由仓库层承接，业务范围判断由调用链负责。
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

* 实装说明（匹配 Agent）：
- 本文件原先仅为骨架（model 未指定，任何方法都会抛 NotImplementedError），
  匹配链路无法读取真实人才档案，只能靠 Mock —— 这是本次实装的原因；
- 只补齐匹配所需的最小读能力（按 ID 批量、按状态召回），不改表结构；
- 召回上限由调用方传入，仓库不擅自截断，避免「悄悄少算人」。
=========================================================================
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.talent import Talent
from app.repositories.base import BaseRepository


class TalentRepository(BaseRepository[Talent]):
    """人才档案仓库。"""

    model = Talent

    # *kwq* 新增：字段白名单（精确筛选），只放业务查询真正会用到的列。
    filterable_fields: set[str] = {
        "status",
        "education",
        "organization_id",
        "source",
        "potential_level",
    }
    # *kwq* 新增：模糊搜索字段（人才列表按姓名/现职位查人）。
    searchable_fields: set[str] = {"name", "current_position", "current_company"}
    sortable_fields: set[str] = {
        "id",
        "name",
        "work_years",
        "created_at",
        "updated_at",
    }
    default_order = "-id"

    # ---------------------------------------------------------------- 匹配专用
    # *kwq* 新增：批量按 ID 回表。
    # 用途：匹配请求指定 talent_ids 时，一次查询取回全部候选人（避免 N+1）。
    # 约束：返回顺序不保证与入参一致，调用方需按 ID 建索引自行排序。
    async def get_by_ids(
        self,
        db: AsyncSession,
        talent_ids: Sequence[int],
        *,
        include_deleted: bool = False,
    ) -> list[Talent]:
        """按 ID 批量查询人才（空列表直接返回空，不发 SQL）。"""
        ids = [int(item) for item in talent_ids or [] if item is not None]
        if not ids:
            return []
        stmt = select(Talent).where(Talent.id.in_(ids))
        if not include_deleted:
            stmt = stmt.where(Talent.is_deleted.is_(False))
        return list((await db.execute(stmt)).scalars().all())

    # *kwq* 新增：匹配候选池查询（SQL 召回通道）。
    # 原因：Milvus 未部署时必须有一条「真实读库」的召回路径，
    #      且要明确只召回可参与匹配的人（在职/储备，未删除），
    #      不能把离职人员也算进分母。
    # [岗位匹配接入] 召回在职/储备人才；指定ID也执行状态约束。
    async def list_candidates(
        self,
        db: AsyncSession,
        *,
        talent_ids: Sequence[int] | None = None,
        statuses: Sequence[str] = ("active", "reserve"),
        limit: int | None = None,
    ) -> list[Talent]:
        """查询可参与匹配的候选人。

        参数：
            talent_ids: 指定候选人（为空表示全库）
            statuses:   可参与匹配的档案状态（默认 active/reserve，排除 left）
            limit:      召回上限（None 表示不限制；由 Service 按配置传入）
        """
        stmt = select(Talent).where(Talent.is_deleted.is_(False))
        # *kwq* 修改（MATCH-008）：状态过滤对「指定 ID」同样生效。
        # 修改原因：原实现只在全库召回时过滤状态，显式传 talent_ids 时不做状态判断，
        #          会把离职人员（status=left）拉进来参与匹配 —— 与「离职不参与匹配」的
        #          业务口径冲突，也会让人力分母虚高。
        if statuses:
            stmt = stmt.where(and_(Talent.status.in_(list(statuses))))
        if talent_ids:
            ids = [int(item) for item in talent_ids if item is not None]
            if not ids:
                return []
            stmt = stmt.where(Talent.id.in_(ids))
        stmt = stmt.order_by(Talent.id.asc())
        if limit:
            stmt = stmt.limit(int(limit))
        return list((await db.execute(stmt)).scalars().all())

    # *kwq* 新增：按技能筛选（供向量召回不可用时的关键词召回扩展）。
    # 说明：MySQL 8 的 JSON_OVERLAPS 在部分托管库不可用，这里用「先取候选池再 Python 判断」，
    #      保证跨库可移植；调用方需控制 candidate_pool 上限。
    @staticmethod
    def filter_by_skills(candidates: Sequence[Any], skills: Sequence[str]) -> list[Any]:
        """从候选池中筛出技能有交集的人（纯函数，便于单测）。"""
        wanted = {str(item).strip().lower() for item in skills or [] if str(item).strip()}
        if not wanted:
            return list(candidates)

        result = []
        for talent in candidates:
            raw = talent.get("skill_tags") if isinstance(talent, dict) else getattr(talent, "skill_tags", None)
            owned = {str(item).strip().lower() for item in (raw or []) if str(item).strip()}
            if owned & wanted:
                result.append(talent)
        return result


__all__ = ["TalentRepository"]
