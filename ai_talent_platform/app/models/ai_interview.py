"""
模块：app/models/ai_interview.py
职责：AI 数字人面试（会话 + 逐轮问答）ORM 模型。

标签：[Agent组] [P1]（AI 面试：数字人面试官会话与对话留痕）

================================ 实现思路 ================================
1. 一个邀约/一个候选人 = 一个 AiInterviewSession；候选人凭随机 token 免登录进入
   公开面试页（/ai-interview/{token}），token 只落库、不在日志中输出明文之外的内容。
2. 面试计划（question plan）在创建会话时一次性生成并快照进 plan 字段：
   [{id, dimension, text, intent, keywords, follow_up, weight}]，
   此后题库/画像变更不影响已开始的面试，保证过程可复现。
3. 逐轮问答落 AiInterviewTurn：面试官问（greeting/main/follow_up/closing）与
   候选人答（answer）按 seq 顺序存档；评分、结构化信号、反馈均记在候选人行上，
   HR 复盘时可以还原完整对话与每一题的给分依据。
4. 评分与报告全部可重算：session 上冗余 total_score / dimension_scores / level /
   recommendation / report 便于列表排序与快速查询，原始事实仍以 turns 为准。
5. JSON 列一律不使用 server_default（MySQL 不允许 JSON/TEXT 字面量默认值，
   见 tests/test_mysql_ddl_defaults.py），统一用 ORM 侧 default=list/dict。
=========================================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel


class AiInterviewSession(BaseModel):
    """AI 数字人面试会话。"""

    __tablename__ = "ai_interview_sessions"
    __table_args__ = (
        Index("ix_ai_interview_sessions_status", "status"),
        Index("ix_ai_interview_sessions_talent", "talent_id"),
        {"comment": "AI 数字人面试会话表"},
    )

    # ------------------------------------------------------------ 关联对象
    talent_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("talents.id", ondelete="SET NULL"),
        nullable=True,
        comment="候选人 ID（FK -> talents.id，可空：允许手工录入候选人）",
    )
    position_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("positions.id", ondelete="SET NULL"),
        nullable=True,
        comment="应聘岗位 ID（FK -> positions.id）",
    )
    invitation_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("talent_invitations.id", ondelete="SET NULL"),
        nullable=True,
        comment="来源面试邀约 ID（FK -> talent_invitations.id，可空）",
    )
    assessment_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("assessments.id", ondelete="SET NULL"),
        nullable=True,
        comment="关联测评 ID（FK -> assessments.id，可空：笔试通过后自动建面试）",
    )

    # ------------------------------------------------------------ 公开访问
    token: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        comment="候选人公开面试链接 token（随机、唯一、免登录）",
    )

    # ------------------------------------------------------------ 展示信息
    title: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", server_default=text("''"), comment="面试标题"
    )
    candidate_name: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=text("''"), comment="候选人姓名"
    )
    candidate_email: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="候选人邮箱（用于发送面试链接）"
    )
    position_title: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="应聘岗位名称（快照）"
    )

    # ------------------------------------------------------------ 数字人面试官
    interviewer_name: Mapped[str] = mapped_column(
        String(64), nullable=False, default="晓岚", server_default=text("'晓岚'"),
        comment="数字人面试官姓名",
    )
    interviewer_title: Mapped[str] = mapped_column(
        String(64), nullable=False, default="AI 面试官", server_default=text("'AI 面试官'"),
        comment="数字人面试官头衔",
    )
    interviewer_persona: Mapped[str] = mapped_column(
        String(32), nullable=False, default="friendly_hr", server_default=text("'friendly_hr'"),
        comment="人设：friendly_hr / tech_lead / strict_expert / global_interviewer",
    )
    interviewer_style: Mapped[str] = mapped_column(
        String(32), nullable=False, default="warm", server_default=text("'warm'"),
        comment="风格：warm 亲和 / professional 专业 / rigorous 严谨 / global 国际化",
    )
    avatar_theme: Mapped[str] = mapped_column(
        String(32), nullable=False, default="aurora", server_default=text("'aurora'"),
        comment="数字人形象主题：aurora / cyan / violet / amber",
    )
    voice_hint: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=text("''"),
        comment="浏览器语音合成音色提示（如 female-zh），空表示跟随系统默认",
    )
    photo_url: Mapped[str | None] = mapped_column(
        String(512), nullable=True, comment="数字人面试官真人肖像照片 URL；空则使用默认头像",
    )
    language: Mapped[str] = mapped_column(
        String(16), nullable=False, default="zh-CN", server_default=text("'zh-CN'"),
        comment="面试语言：zh-CN / en-US",
    )

    # ------------------------------------------------------------ 面试配置
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=text("'pending'"),
        comment="状态：pending 待开始 / in_progress 进行中 / completed 已完成 / expired 已过期 / cancelled 已取消",
    )
    difficulty: Mapped[str] = mapped_column(
        String(16), nullable=False, default="middle", server_default=text("'middle'"),
        comment="难度：junior / middle / senior",
    )
    question_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="计划问题数量"
    )
    duration_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, server_default=text("30"), comment="面试时长（分钟）"
    )
    plan: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list, comment="问题计划快照（有序）"
    )
    current_index: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="当前主问题下标"
    )
    current_question_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="main", server_default=text("'main'"),
        comment="当前问题类型：main / follow_up",
    )
    follow_up_used: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0"),
        comment="当前主问题是否已追问（每题最多一次）",
    )
    answered_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="已作答主问题数"
    )

    # ------------------------------------------------------------ 结果
    total_score: Mapped[float | None] = mapped_column(
        Numeric(5, 2), nullable=True, comment="综合得分（0-100，未完成时为空）"
    )
    dimension_scores: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="维度得分（雷达图数据源）"
    )
    level: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="等级")
    recommendation: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="建议：strongly_recommend/recommend/consider/not_recommend/insufficient"
    )
    report: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="完整评估报告")
    generator: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="报告生成方式：rule / rule+llm:<model>"
    )

    # ------------------------------------------------------------ 时间
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="开始时间")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="结束时间")
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True, comment="链接过期时间"
    )

    # ------------------------------------------------------------ 关系
    turns: Mapped[list["AiInterviewTurn"]] = relationship(
        "AiInterviewTurn",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="AiInterviewTurn.seq",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AiInterviewSession id={self.id} status={self.status} candidate={self.candidate_name}>"


class AiInterviewTurn(BaseModel):
    """AI 面试逐轮问答（面试官 / 候选人共用）。"""

    __tablename__ = "ai_interview_turns"
    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_ai_interview_turns_session_seq"),
        Index("ix_ai_interview_turns_session_kind", "session_id", "kind"),
        {"comment": "AI 数字人面试逐轮问答表"},
    )

    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("ai_interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
        comment="会话 ID（FK -> ai_interview_sessions.id，CASCADE）",
    )
    seq: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="会话内顺序号（从 1 开始递增）"
    )
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="角色：interviewer 面试官 / candidate 候选人"
    )
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="main", server_default=text("'main'"),
        comment="轮次类型：greeting / main / follow_up / answer / closing",
    )
    question_index: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="对应 plan 中主问题下标（追问与回答沿用同一下标）"
    )
    dimension: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="考察维度")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="对话文本")
    input_mode: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="候选人作答方式：voice / text"
    )
    duration_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="候选人作答耗时（秒）"
    )
    score: Mapped[float | None] = mapped_column(
        Numeric(5, 2), nullable=True, comment="本题规则得分（0-100，仅候选人行）"
    )
    signals: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="结构化信号：长度/结构词/关键词命中/数据点"
    )
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True, comment="本题反馈")

    # ------------------------------------------------------------ 关系
    session: Mapped["AiInterviewSession"] = relationship(
        "AiInterviewSession", back_populates="turns"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AiInterviewTurn id={self.id} role={self.role} kind={self.kind}>"
