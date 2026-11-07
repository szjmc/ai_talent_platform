"""人才邀约（笔试/测评/面试）与笔试会话模型。

标签：[Agent组] [P1]

说明：
- models/* 原有文件为冻结文件；本文件为**新增**（追加式扩展，遵循
  app/models/__init__.py 中的扩展流程：新增模型文件 → 此处导入 → 加入 __all__）。
- talent_id 可空：人才卡片可能来自前端演示数据（库中无档案行），
  此时仅落 talent_snapshot（姓名/手机/邮箱快照），不建外键。
- 题库：题目取自 assessments / assessment_questions（Vanna组），
  邀约创建时按解析出的题量随机抽题，题目 ID 快照进 ExamSession.question_ids，
  提交时按快照阅卷，answer 字段永不返回前端。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class TalentInvitation(BaseModel):
    """人才邀约：笔试 / 测评 / 面试邀请（含链接 token 与发送渠道记录）。"""

    __tablename__ = "talent_invitations"
    __table_args__ = (
        Index("ix_talent_invitations_talent_status", "talent_id", "status"),
        {"comment": "人才邀约表（笔试/测评/面试）"},
    )

    talent_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("talents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="人才 ID（FK -> talents.id，可空：演示数据无档案时为 NULL）",
    )
    talent_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, comment="创建时的人才快照：name/mobile/email"
    )
    type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="written_test",
        server_default=text("'written_test'"),
        comment="类型：written_test 笔试 / assessment 测评 / interview 面试",
    )
    token: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True, comment="公开访问链接 token（secret）"
    )
    title: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", server_default=text("''"), comment="邀约标题（展示用）"
    )
    channels: Mapped[list[Any] | None] = mapped_column(
        JSON, nullable=True, comment="发送渠道列表：sms / email"
    )
    send_results: Mapped[list[Any] | None] = mapped_column(
        JSON, nullable=True, comment="各渠道发送结果：{channel, ok, mode, error}"
    )
    question_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="笔试题量（创建时按简历解析结果计算/覆盖）"
    )
    duration_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=60,
        server_default=text("60"),
        comment="笔试时长（分钟，自首次打开起算，不超过 expires_at）",
    )
    interview_time: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="面试约定时间（type=interview）"
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True, comment="链接过期时间"
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="created",
        server_default=text("'created'"),
        comment="状态：created 已创建 / sent 已发送 / opened 候选人已打开 / submitted 已交卷 / "
        "finished 面试完成 / expired 已过期 / cancelled 已取消",
    )
    created_by: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="创建人（FK -> users.id）",
    )


class ExamSession(BaseModel):
    """笔试会话：一个邀约对应一次作答（题快照、答案、作弊违规、阅卷结果）。"""

    __tablename__ = "exam_sessions"
    __table_args__ = (
        Index("ix_exam_sessions_status_deadline", "status", "deadline"),
        {"comment": "笔试作答会话表"},
    )

    invitation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("talent_invitations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="邀约 ID（FK -> talent_invitations.id，一会约一卷）",
    )
    token: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True, comment="邀约 token 冗余（公开接口直接查询）"
    )
    # MySQL 禁止 JSON / TEXT / BLOB 列带「字面量默认值」（ERR 1101）：
    #   DEFAULT '[]'         -> 建表失败（历史事故：自动建表在这里中断）
    #   DEFAULT ('[]')       -> 仅 MySQL 8.0.13+ 支持，5.7 / MariaDB 仍不支持
    # 因此这里只用 ORM 侧 default=list（INSERT 时由 SQLAlchemy 补值），
    # 不声明 server_default；守门用例见 tests/test_mysql_ddl_defaults.py。
    question_ids: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=list, comment="抽题快照（有序 question_id 列表）"
    )
    answers: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, comment="作答内容：{question_id: answer}；answer 结构见阅卷逻辑"
    )
    violations: Mapped[list[Any] | None] = mapped_column(
        JSON,
        nullable=True,
        default=list,
        comment="作弊检测违规记录：[{ts, type, detail}]（YOLO/人脸回退检测）",
    )
    proctor_state: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, comment="监考连续状态机：{no_face_streak, frames, multi_person, ...}"
    )
    candidate_ip: Mapped[str | None] = mapped_column(
        String(45), nullable=True, comment="交卷/首帧来源 IP（审计）"
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="in_progress",
        server_default=text("'in_progress'"),
        comment="状态：in_progress 作答中 / submitted 已交卷 / expired 超时 / cancelled 关联邀约被取消",
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="首次打开时间")
    deadline: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True, comment="作答截止（min(首次打开+时长, 邀约过期)）"
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="交卷时间")
    score: Mapped[float | None] = mapped_column(
        Numeric(6, 2), nullable=True, comment="客观题自动得分（文本题需人工，未计入）"
    )
    total_score: Mapped[float | None] = mapped_column(
        Numeric(6, 2), nullable=True, comment="卷面总分（快照题目分值合计）"
    )
    score_detail: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, comment="逐题阅卷：{question_id: {correct, got, score, manual}}"
    )
