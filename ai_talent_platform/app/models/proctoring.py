"""
模块：app/models/proctoring.py
职责：通用在线监考（面试 / 笔试 / 测评共用）的异常行为信号与事件 ORM 模型。

标签：[Agent组] [P1]（防作弊：跨环节监考信号、事件、可疑快照留痕）

================================ 实现思路 ================================
1. 与具体业务解耦：`subject_type` + `subject_id` + `subject_token` 三件套定位监考对象，
   目前覆盖 ai_interview / exam / interview / assessment 四类，
   避免为「AI 面试」「笔试」各写一套重复表（历史教训：同功能多套表最终口径打架）。
2. 三层数据落位：
   - proctoring_sessions：会话级汇总（风险分 / 等级 / 硬性命中 / 心跳时间 / 环境基线），
     HR 列表页只查这张表，O(1) 拿到「谁可疑」；
   - proctoring_events：事件级明细（切屏、远程控制、投屏、多显示器……），
     用于时间线复盘与模型迭代，超量时由 Service 侧做条数裁剪；
   - proctoring_snapshots：可疑时刻的摄像头/屏幕快照（存对象存储，库里只存路径），
     供 HR 人工复核 —— 默认只存「违规发生前后」的抽样帧，不是全程录像。
3. JSON 列不使用 server_default（MySQL 对 JSON 禁止字面量默认值，
   见 tests/test_mysql_ddl_defaults.py），统一 ORM 侧 default=list/dict。
4. 所有表都带 BaseModel（逻辑删除 + 乐观锁 + 操作人），与平台其余表保持一致。
=========================================================================
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel

# 监考对象类型：与 router / service 中的枚举保持一致
SUBJECT_TYPES = ("ai_interview", "exam", "interview", "assessment")


class ProctoringSession(BaseModel):
    """监考会话（一个面试 / 一份笔试卷对应一行）。"""

    __tablename__ = "proctoring_sessions"
    __table_args__ = (
        Index("ix_proctoring_sessions_subject", "subject_type", "subject_id"),
        Index("ix_proctoring_sessions_token", "subject_token"),
        Index("ix_proctoring_sessions_risk", "risk_level", "risk_score"),
        {"comment": "在线监考会话表（切屏/远程/投屏等行为风险汇总）"},
    )

    subject_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ai_interview",
        comment="监考对象类型：ai_interview / exam / interview / assessment",
    )
    subject_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="对象主键（ai_interview_sessions.id / exam_sessions.id 等）"
    )
    subject_token: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="候选人公开 token（免登录溯源，便于按链接反查）"
    )
    candidate_name: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="候选人姓名快照（HR 列表直接展示）"
    )
    position_title: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="应聘岗位名称快照"
    )

    # ------------------------------------------------------------ 风险汇总
    risk_score: Mapped[float] = mapped_column(
        Numeric(6, 2), nullable=False, default=0, comment="风险分（0-100，由 integrity 引擎累加）"
    )
    risk_level: Mapped[str] = mapped_column(
        String(16), nullable=False, default="clean",
        comment="风险等级：clean / watch / review / serious / void",
    )
    risk_advice: Mapped[str] = mapped_column(
        String(32), nullable=False, default="clean",
        comment="处理建议：clean / keep / review / escalate / void",
    )
    event_counts: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="各类事件累计次数 {tab_hidden: 3, remote_control: 1}"
    )
    hard_violations: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="命中的硬性风险类型（远程控制 / 投屏 / 代考）"
    )
    state: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="完整状态机快照（baseline / flagged / timeline 尾部）"
    )

    # ------------------------------------------------------------ 采集状态
    strict_mode: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="是否严格模式（命中硬性风险即阻断）"
    )
    require_screen_share: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="是否要求屏幕共享（未共享即计风险）"
    )
    screen_shared: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="当前是否已共享屏幕"
    )
    remote_control: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="当前是否检测到远程控制会话"
    )
    display_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="最近一次上报的显示器数量"
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="最近一次监考心跳时间（用于判定断流）"
    )
    heartbeat_lost: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="是否已判定心跳丢失"
    )
    blocked_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="因风险超阈值被阻断的时间（严格模式）"
    )
    event_total: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="累计上报事件条数（含未入库的截断部分）"
    )

    # ------------------------------------------------------------ 时间
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="监考开始时间")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="监考结束时间")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ProctoringSession id={self.id} subject={self.subject_type}:{self.subject_id} "
            f"level={self.risk_level} score={self.risk_score}>"
        )


class ProctoringEvent(BaseModel):
    """监考明细事件（每条可复核的异常行为）。"""

    __tablename__ = "proctoring_events"
    __table_args__ = (
        Index("ix_proctoring_events_session", "session_ref_id", "occurred_at"),
        Index("ix_proctoring_events_type", "event_type"),
        {"comment": "在线监考事件明细表（切屏/远程/投屏/设备变更等）"},
    )

    session_ref_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="监考会话 ID（proctoring_sessions.id，逻辑关联）"
    )
    subject_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ai_interview", comment="对象类型冗余（便于跨模块查询）"
    )
    subject_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="对象主键冗余"
    )
    event_type: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="规范事件类型：tab_hidden / remote_control / casting ..."
    )
    label: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="事件中文标签（报告直接引用）"
    )
    detail: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="事件详情（客户端原始描述，已截断）"
    )
    weight: Mapped[float] = mapped_column(
        Numeric(6, 2), nullable=False, default=0, comment="本次事件计入的风险分"
    )
    score_after: Mapped[float] = mapped_column(
        Numeric(6, 2), nullable=False, default=0, comment="事件发生后会话累计风险分"
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="client", comment="来源：client 前端上报 / server 服务端推导"
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=True, comment="事件发生时间（客户端时钟，已做合理性校正）"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ProctoringEvent id={self.id} session={self.session_ref_id} type={self.event_type}>"


class ProctoringSnapshot(BaseModel):
    """可疑时刻快照（人工复核用，文件存对象存储，库里只存键）。"""

    __tablename__ = "proctoring_snapshots"
    __table_args__ = (
        Index("ix_proctoring_snapshots_session", "session_ref_id", "captured_at"),
        {"comment": "在线监考可疑快照表（摄像头/屏幕抽样帧，隐私最小化留存）"},
    )

    session_ref_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="监考会话 ID（proctoring_sessions.id）"
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="camera", comment="来源：camera 摄像头 / screen 屏幕"
    )
    storage_backend: Mapped[str] = mapped_column(
        String(16), nullable=False, default="local", comment="存储后端：local / minio / oss / s3"
    )
    storage_key: Mapped[str] = mapped_column(
        String(255), nullable=False, default="", comment="对象存储键（不含域名）"
    )
    file_size: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="文件字节数"
    )
    trigger_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default="", comment="触发该快照的事件类型（如 remote_control）"
    )
    captured_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="拍摄时间"
    )
    note: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="备注（复核结论）")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ProctoringSnapshot id={self.id} session={self.session_ref_id} trigger={self.trigger_type}>"


__all__ = ["SUBJECT_TYPES", "ProctoringEvent", "ProctoringSession", "ProctoringSnapshot"]
