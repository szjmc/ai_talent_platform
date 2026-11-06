"""
标签：[Agent组] [P1]
模块：app/models/matching_review.py
职责：HR 人工复核的「当前状态 + 动作流水」两张表（本阶段新建）。

============================ 新建表说明 ============================
【新建】matching_reviews      复核当前状态表（一人一批一条，可更新）
【新建】matching_review_events 复核动作流水表（append-only，只增不改）

为什么要两张表（不能合并）：
1. 只留状态表 → 每次改状态都会覆盖上一次，「谁在什么时候把入围改成淘汰」
   永久丢失，招聘合规审计无法追溯；
2. 只留流水表 → 查「这条匹配现在什么状态」必须扫全量事件算最新一条，
   列表页（待复核筛选、漏斗统计）会慢且易算错；
3. 职责分离后：状态表承担并发写（乐观锁 + 唯一键），流水表承担审计与时序
   分析（待复核→入围耗时、各阶段转化率）。

与既有四态的关系（重要，别混淆）：
- `HardStatus.NEEDS_REVIEW` = 数据有缺口待核实，属于「引擎算出来的判定」；
- 本表的 `review_status` = HR 的业务决策进展，属于「人做出的决策」。
两者独立，不要互相映射。

边界：本表只存「引用 ID + 决策」，不复制人才/岗位档案内容，不改其他模块的表。
新增表需执行迁移（Alembic），本文件只定义结构，不建表。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BaseModel, TimestampMixin

# ------------------------------------------------------------------ 状态常量
REVIEW_PENDING = "pending"
REVIEW_SHORTLISTED = "shortlisted"
REVIEW_INTERVIEWING = "interviewing"
REVIEW_REJECTED = "rejected"
REVIEW_HIRED = "hired"

#: 全部合法状态（顺序即漏斗顺序，前端按此渲染阶段）
REVIEW_STATUSES: tuple[str, ...] = (
    REVIEW_PENDING,
    REVIEW_SHORTLISTED,
    REVIEW_INTERVIEWING,
    REVIEW_REJECTED,
    REVIEW_HIRED,
)

#: 状态中文名（消息与文案统一出处，避免前后端各写一套）
REVIEW_STATUS_LABELS: dict[str, str] = {
    REVIEW_PENDING: "待复核",
    REVIEW_SHORTLISTED: "入围",
    REVIEW_INTERVIEWING: "面试中",
    REVIEW_REJECTED: "淘汰",
    REVIEW_HIRED: "录用",
}

#: 合法流转：当前状态 -> 允许进入的下一个状态
#: 设计依据（业务规则，需架构确认后可调整）：
#:   - pending 可以直跳面试（跳过入围，现实里很常见）；
#:   - interviewing 才能录用（没面过直接录用属于违规操作，必须拦）；
#:   - rejected 可撤回重判（HR 点错了要能改）；
#:   - hired 只能转到 rejected（候选人放弃 offer），不能直接跳别的阶段。
REVIEW_TRANSITIONS: dict[str, frozenset[str]] = {
    REVIEW_PENDING: frozenset({REVIEW_SHORTLISTED, REVIEW_INTERVIEWING, REVIEW_REJECTED}),
    REVIEW_SHORTLISTED: frozenset({REVIEW_INTERVIEWING, REVIEW_REJECTED, REVIEW_PENDING}),
    REVIEW_INTERVIEWING: frozenset({REVIEW_HIRED, REVIEW_REJECTED, REVIEW_SHORTLISTED}),
    REVIEW_REJECTED: frozenset({REVIEW_PENDING}),
    REVIEW_HIRED: frozenset({REVIEW_REJECTED}),
}

#: 进入这些状态必须填写 reason_code（合规要求：淘汰/撤回必须有据可查）
REVIEW_REASON_REQUIRED: frozenset[str] = frozenset({REVIEW_REJECTED})

#: 淘汰原因码白名单（限定取值，否则统计口径会散掉）
REVIEW_REASON_CODES: tuple[str, ...] = (
    "skill_gap",        # 技能不匹配
    "experience_gap",   # 经验/年限不足
    "education",        # 学历不符
    "salary_mismatch",  # 薪资预期偏差
    "availability",     # 到岗时间不合适
    "better_candidate", # 有更优候选人
    "withdraw",         # 候选人主动放弃
    "other",            # 其他（需在 note 说明）
)


class MatchingReview(BaseModel):
    """【新建】HR 复核当前状态：同一 (batch_id, talent_id) 只有一条有效记录。

    为什么用唯一键而不是"每次决策插一条"：
        决策是「幂等 upsert」语义——同一人同一批次重复提交应是更新当前状态，
        而不是堆出一堆互相矛盾的历史行；历史交给流水表承担。
    为什么存 score_snapshot / rank_snapshot：
        分数会随权重调整、画像改版而重算；决策时看到的是哪个分数必须冻结，
        否则事后审计会出现"HR 说按 82 分录用的，库里现在是 76 分"的扯皮。
    """

    __tablename__ = "matching_reviews"
    __table_args__ = (
        UniqueConstraint("batch_id", "talent_id", name="uq_matching_reviews_batch_talent"),
        # 复核工作台主查询：某岗位下按状态筛「还有多少人没看」
        Index("ix_matching_reviews_position_status", "position_id", "review_status"),
        Index("ix_matching_reviews_batch_id", "batch_id"),
    )

    batch_id: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="匹配批次 ID（MatchingBatch.batch_id）"
    )
    position_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True, comment="岗位 ID（冗余，便于按岗位聚合漏斗）"
    )
    talent_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True, comment="人才 ID"
    )
    review_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=REVIEW_PENDING, server_default=REVIEW_PENDING,
        comment="复核状态：pending/shortlisted/interviewing/rejected/hired",
    )
    reason_code: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="决策原因码（淘汰必填，取值见 REVIEW_REASON_CODES）"
    )
    note: Mapped[str | None] = mapped_column(
        String(512), nullable=True, comment="备注（其他原因需在此说明）"
    )
    score_snapshot: Mapped[float | None] = mapped_column(
        Numeric(6, 2), nullable=True, comment="决策时的匹配分快照（防重算后对不上账）"
    )
    rank_snapshot: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="决策时的名次快照"
    )
    decided_by: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="最后一次决策人用户 ID"
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="最后一次决策时间"
    )


class MatchingReviewEvent(Base, TimestampMixin):
    """【新建】复核动作流水（append-only：只插入，永不更新/删除）。

    为什么不用 BaseModel：BaseModel 自带 is_deleted / updated_by / version，
    对 append-only 表是纯噪音，还会让「有人改过流水」变得技术上可能——
    审计表的价值恰恰在于不可变。

    为什么存 from_status：漏斗时序分析需要「A→B」的边，而不是只有终点。
    """

    __tablename__ = "matching_review_events"
    __table_args__ = (
        Index("ix_matching_review_events_review_id", "review_id", "id"),
        Index("ix_matching_review_events_position", "position_id", "id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键 ID"
    )
    review_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="所属复核记录 ID（matching_reviews.id）"
    )
    batch_id: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="批次 ID（冗余，便于不 join 直接按批次查流水）"
    )
    position_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="岗位 ID（冗余）"
    )
    talent_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="人才 ID（冗余）"
    )
    from_status: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="变更前状态；首次决策为 NULL"
    )
    to_status: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="变更后状态"
    )
    reason_code: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="本次决策原因码"
    )
    note: Mapped[str | None] = mapped_column(
        String(512), nullable=True, comment="本次决策备注"
    )
    operator_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="操作人用户 ID（谁做的这次决策）"
    )
    # *kwq* 新增：标记「HR 绕过了硬性条件」。
    # 为什么单独存一列而不是塞进 note：审计时需要能直接统计「有多少次入围/录用是
    # 硬条件未通过却被人为放行的」——这是合规风险指标，靠文本关键词匹配不可靠。
    overridden: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0"),
        comment="是否绕过硬性条件（匹配判定 FAIL 仍入围/面试/录用）",
    )


__all__ = [
    "MatchingReview",
    "MatchingReviewEvent",
    "REVIEW_STATUSES",
    "REVIEW_STATUS_LABELS",
    "REVIEW_TRANSITIONS",
    "REVIEW_REASON_REQUIRED",
    "REVIEW_REASON_CODES",
    "REVIEW_PENDING",
    "REVIEW_SHORTLISTED",
    "REVIEW_INTERVIEWING",
    "REVIEW_REJECTED",
    "REVIEW_HIRED",
]
