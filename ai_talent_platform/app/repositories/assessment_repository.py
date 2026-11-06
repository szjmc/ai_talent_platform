"""
模块：app/repositories/assessment_repository.py
职责：测评数据访问层（ORM，禁止裸 SQL）。

方法清单：
    - get_active_by_talent(db, talent_id)        进行中的测评
    - list_questions(db, assessment_id)          题目列表
    - save_answer(db, ...)                        单题答题 upsert
    - list_answers(db, assessment_id)            答题列表
    - get_result(db, assessment_id)              测评结果（1:1）
    - save_result(db, ...)                        结果 upsert
    - stats_avg_score(db, position_id)           岗位平均分统计

约束：不写业务判断、不 commit/rollback、聚合用 SQLAlchemy func。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import (
    Assessment,
    AssessmentAnswer,
    AssessmentQuestion,
    AssessmentResult,
    InterviewRecordSheet,
)
from app.repositories.base import BaseRepository


class AssessmentRepository(BaseRepository):
    """测评仓库。"""

    model = Assessment
    filterable_fields: set[str] = {"talent_id", "position_id", "status", "title"}
    searchable_fields: set[str] = {"title"}
    sortable_fields: set[str] = {"id", "created_at", "updated_at", "deadline"}
    default_order = "-id"

    # ------------------------------------------------------------ 查询
    async def get_active_by_talent(self, db: AsyncSession, talent_id: int) -> Assessment | None:
        """取人才当前进行中的测评（pending / in_progress）。"""
        stmt = (
            select(Assessment)
            .where(Assessment.talent_id == talent_id)
            .where(Assessment.status.in_(["pending", "in_progress"]))
            .order_by(Assessment.id.desc())
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def list_questions(self, db: AsyncSession, assessment_id: int) -> Sequence[AssessmentQuestion]:
        """取测评题目（含标准答案；Service 下发给前端前必须剔除 answer 字段）。"""
        stmt = (
            select(AssessmentQuestion)
            .where(AssessmentQuestion.assessment_id == assessment_id)
            .order_by(AssessmentQuestion.sort.asc(), AssessmentQuestion.id.asc())
        )
        return (await db.execute(stmt)).scalars().all()

    async def list_answers(self, db: AsyncSession, assessment_id: int) -> Sequence[AssessmentAnswer]:
        """取某次测评的全部答题。"""
        stmt = (
            select(AssessmentAnswer)
            .where(AssessmentAnswer.assessment_id == assessment_id)
            .order_by(AssessmentAnswer.question_id.asc())
        )
        return (await db.execute(stmt)).scalars().all()

    async def get_result(self, db: AsyncSession, assessment_id: int) -> AssessmentResult | None:
        """取测评结果（1:1）。"""
        stmt = select(AssessmentResult).where(AssessmentResult.assessment_id == assessment_id)
        return (await db.execute(stmt)).scalar_one_or_none()

    # ------------------------------------------------------------ 写入
    async def save_answer(
        self,
        db: AsyncSession,
        *,
        assessment_id: int,
        question_id: int,
        answer: Any,
        score: float | None = None,
        scored_by: str | None = None,
    ) -> AssessmentAnswer:
        """单题答题 upsert（同一题重复提交覆盖）。"""
        existing = await self.get_one(
            db,
            {"assessment_id": assessment_id, "question_id": question_id},
        )
        if existing is not None:
            existing.answer = answer
            if score is not None:
                existing.score = score
            if scored_by is not None:
                existing.scored_by = scored_by
            if score is not None:
                existing.scored_at = datetime.utcnow()
            await db.flush()
            return existing

        obj = AssessmentAnswer(
            assessment_id=assessment_id,
            question_id=question_id,
            answer=answer,
            score=score,
            scored_by=scored_by,
            scored_at=datetime.utcnow() if score is not None else None,
        )
        db.add(obj)
        await db.flush()
        return obj

    async def save_result(self, db: AsyncSession, *, assessment_id: int, **fields: Any) -> AssessmentResult:
        """结果 upsert（assessment_id 唯一，1:1）。"""
        existing = await self.get_result(db, assessment_id)
        if existing is not None:
            for k, v in fields.items():
                if hasattr(existing, k) and k != "assessment_id":
                    setattr(existing, k, v)
            await db.flush()
            return existing

        obj = AssessmentResult(assessment_id=assessment_id, **fields)
        db.add(obj)
        await db.flush()
        return obj

    async def update_status(
        self, db: AsyncSession, assessment_id: int, status: str, **extra: Any
    ) -> Assessment:
        """更新测评状态机（pending->in_progress->submitted->scored）。"""
        obj = await self.get_by_id_or_404(db, assessment_id, message=f"测评不存在（id={assessment_id}）")
        obj.status = status
        for k, v in extra.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        await db.flush()
        return obj

    # ------------------------------------------------------------ 统计
    async def stats_avg_score(self, db: AsyncSession, position_id: int) -> dict[str, Any]:
        """岗位平均分 / 最高 / 最低 / 样本数（SQL 聚合，不在 Python 循环里统计）。"""
        stmt = (
            select(
                func.count(Assessment.id).label("total"),
                func.avg(Assessment.total_score).label("avg_score"),
                func.max(Assessment.total_score).label("max_score"),
                func.min(Assessment.total_score).label("min_score"),
            )
            .where(Assessment.position_id == position_id)
            .where(Assessment.status == "scored")
        )
        row = (await db.execute(stmt)).one()
        return {
            "position_id": position_id,
            "total": int(row.total or 0),
            "avg_score": round(float(row.avg_score or 0), 2),
            "max_score": round(float(row.max_score or 0), 2),
            "min_score": round(float(row.min_score or 0), 2),
        }

    # ------------------------------------------------------------------ 面试
    async def finish_interview_session(self, db: AsyncSession, session_id: int,
                                       interview_score: float, summary: dict) -> None:
        from app.models.assessment import InterviewSession
        row = await db.get(InterviewSession, session_id)
        if row:
            row.interview_score = interview_score
            row.summary = summary
            row.finished_at = datetime.now(timezone.utc)
            await db.flush()

    async def list_interview_records(self, db: AsyncSession, assessment_id: int) -> Sequence[Any]:
        """某测评的全部面试记录表（按 id 倒序，最新在前）。"""
        from app.models.assessment import InterviewRecordSheet
        stmt = (
            select(InterviewRecordSheet)
            .where(InterviewRecordSheet.assessment_id == assessment_id)
            .order_by(InterviewRecordSheet.id.desc())
        )
        return (await db.execute(stmt)).scalars().all()

    async def list_session_answers(self, db: AsyncSession, assessment_id: int) -> Sequence[Any]:
        """某测评面试会话下的全部问答（join 会话表限定范围）。"""
        from app.models.assessment import InterviewAnswer, InterviewSession
        stmt = (
            select(InterviewAnswer)
            .join(InterviewSession, InterviewAnswer.session_id == InterviewSession.id)
            .where(InterviewSession.assessment_id == assessment_id)
        )
        return (await db.execute(stmt)).scalars().all()

    async def get_talent_keywords(self, db: AsyncSession, talent_id: int) -> dict[str, Any]:
        """取人才档案的简历关键字（学历/年限/技能，供报告综合分析）。"""
        from app.models.talent import Talent
        talent = (await db.execute(select(Talent).where(Talent.id == talent_id))).scalar_one_or_none()
        if talent is None:
            return {}
        return {
            "school": getattr(talent, "school", "") or "",
            "education": getattr(talent, "education", "") or "",
            "years": float(getattr(talent, "work_years", 0) or 0),
            "skills": list(getattr(talent, "skills", None) or []),
            "school_tier": int(getattr(talent, "school_tier", 0) or 0),
        }


class InterviewRecordRepository(BaseRepository):
    """面试记录表仓库（拍照上传存档表，白名单同公共层约定）。"""

    model = InterviewRecordSheet
    filterable_fields: set[str] = {"assessment_id", "talent_id", "position", "parse_status"}
    searchable_fields: set[str] = {"comprehensive_evaluation"}
    sortable_fields: set[str] = {"id", "created_at", "interview_score"}
    default_order = "-id"


__all__ = ["AssessmentRepository", "InterviewRecordRepository"]
