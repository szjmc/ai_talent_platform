"""
功能：岗位画像解析、人工确认与版本管理。
输入：岗位ID、JD或人工修订画像、期望版本。
输出：画像草稿或带确认信息的版本视图。
流程：解析草稿 → HR确认 → 写版本 → 同步岗位要求。
边界：冲突拒绝确认；缺失项保留待复核。同步时保留评分权重。

标签：[Agent组] [P1]（岗位画像构建与版本管理）
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError, NotFoundError, ParamError
from app.models.position import Position, PositionRequirement
from app.repositories.position_repository import PositionRepository
from app.schemas.position import JobProfileConfirmRequest, JobProfileParsed, JobProfileView
from app.services.jd_parser import normalize_job_profile, parse_jd

# *kwq* 新增：确认元数据在 dimensions JSON 中的保留键。
# 说明：以下划线开头，与 JobProfileParsed 字段不会冲突；读取时会先剥离再校验。
_META_KEY = "_meta"


class PositionProfileService:
    """岗位画像服务（解析草稿 / 确认生效 / 版本查询）。"""

    def __init__(self, db: AsyncSession, repo: PositionRepository | None = None) -> None:
        self.db = db
        self.repo = repo or PositionRepository()

    # ---------------------------------------------------------------- 内部
    async def _get_position(self, position_id: int) -> Position:
        """读取岗位，不存在抛 NotFoundError。"""
        position = await self.repo.get_by_id(self.db, position_id)
        if position is None:
            raise NotFoundError(f"岗位不存在（id={position_id}）")
        return position

    @staticmethod
    def _profile_from_dimensions(dimensions: dict[str, Any] | None) -> tuple[JobProfileParsed, dict[str, Any]]:
        """从 dimensions JSON 还原画像与确认元数据（剥离 _meta 后再校验）。"""
        data = dict(dimensions or {})
        meta = data.pop(_META_KEY, {}) or {}
        if not isinstance(meta, dict):
            meta = {}
        profile = JobProfileParsed.model_validate(data)
        return profile, meta

    @staticmethod
    def _serialize(profile: JobProfileParsed, meta: dict[str, Any]) -> dict[str, Any]:
        """画像 -> 可入库 JSON（保留 _meta）。"""
        data = profile.model_dump(mode="json")
        data[_META_KEY] = meta
        return data

    async def _resolve_version(
        self, position_id: int, version: int | None
    ) -> PositionRequirement | None:
        """取指定版本或最新版本。"""
        if version is not None:
            return await self.repo.get_requirement(self.db, position_id, version)
        return await self.repo.latest_requirement(self.db, position_id)

    # ---------------------------------------------------------------- 解析（草稿）
    # *kwq* 新增：JD 解析（不落库）。
    # 原因：解析结果必须经 HR 修订后才确认；直接落库会绕过「未确认不得匹配」的规则。
    async def parse(self, position_id: int, jd_text: str, job_title: str | None = None):
        """解析 JD，返回 (画像草稿, 实际通道)。岗位必须存在且未关闭。"""
        position = await self._get_position(position_id)
        if position.status == "closed":
            raise BusinessRuleError("岗位已关闭，不能解析新的岗位画像")
        title_hint = job_title or position.title
        return await parse_jd(jd_text, job_title=title_hint)

    # ---------------------------------------------------------------- 确认生效
    # *kwq* 新增：确认画像并生成新版本。
    # 说明：
    #   1) expected_version 与当前最新版本不一致时拒绝（防止覆盖他人确认）；
    #   2) 再次调用 normalize 做一次标准化，避免前端提交未校验的脏字段；
    #   3) 任何缺失/冲突都不阻止确认？—— 阻止。
    #      业务要求「画像可确认」的前提是 HR 已补齐；带冲突直接确认会把矛盾写进判定依据，
    #      因此这里对 conflicts 直接拒绝，对 missing_fields 允许确认但强制 review_required。
    async def confirm(
        self,
        position_id: int,
        payload: JobProfileConfirmRequest,
        *,
        user_id: int | None,
    ) -> JobProfileView:
        """
        功能：把HR核对后的画像确认为新版本。
        输入：岗位ID、画像、期望版本、确认用户。
        输出：JobProfileView。
        步骤：标准化 → 拒绝冲突/检查版本 → 写版本与元数据 → 同步岗位并保留权重。
        """
        position = await self._get_position(position_id)
        if position.status == "closed":
            raise BusinessRuleError("岗位已关闭，不能确认新的岗位画像")

        raw = payload.profile.model_dump(mode="json")
        profile = normalize_job_profile(
            raw,
            job_title_hint=position.title,
            source=raw.get("source") or "manual",
            prompt_version=raw.get("prompt_version") or "manual-v1",
        )
        if profile.conflicts:
            raise ParamError(
                "画像存在冲突，请先修正后再确认：" + "；".join(profile.conflicts)
            )

        latest = await self.repo.latest_requirement(self.db, position_id)
        current_version = int(latest.version) if latest else 0
        if payload.expected_version is not None and payload.expected_version != current_version:
            raise BusinessRuleError(
                f"画像版本已变化（期望 {payload.expected_version}，当前 {current_version}），请刷新后重试"
            )

        version = await self.repo.next_requirement_version(self.db, position_id)
        meta = {
            "confirmed": True,
            "confirmed_by": user_id,
            "confirmed_at": datetime.now().isoformat(timespec="seconds"),
            "note": payload.note,
        }
        await self.repo.add_requirement(
            self.db,
            position_id=position_id,
            version=version,
            dimensions=self._serialize(profile, meta),
            weights=(position.requirements or {}).get("weights") if isinstance(position.requirements, dict) else None,
            source="ai_parse" if profile.source == "dify" else "manual",
        )

        # 同步岗位主表：让不读版本表的既有代码也能拿到最新已确认画像
        # 步骤：同步画像时保留评分权重，避免确认后静默退回默认。
        previous_weights = (position.requirements or {}).get("weights") if isinstance(position.requirements, dict) else None
        synced = profile.model_dump(mode="json")
        if previous_weights is not None:
            synced["weights"] = previous_weights
        synced.update(
            {
                "profile_confirmed": True,
                "requirement_version": version,
                "confirmed_by": user_id,
                "confirmed_at": meta["confirmed_at"],
            }
        )
        position.requirements = synced
        position.skill_tags = list(profile.required_skills)
        await self.db.flush()

        return JobProfileView(
            position_id=position_id,
            position_title=position.title,
            version=version,
            confirmed=True,
            profile=profile,
            confirmed_by=user_id,
            confirmed_at=meta["confirmed_at"],
        )

    # ---------------------------------------------------------------- 查询
    async def get_profile(self, position_id: int, version: int | None = None) -> JobProfileView:
        """查询指定 / 最新版本的岗位画像。"""
        position = await self._get_position(position_id)
        record = await self._resolve_version(position_id, version)
        if record is None:
            raise NotFoundError(f"岗位尚未生成画像（position_id={position_id}）")
        profile, meta = self._profile_from_dimensions(record.dimensions)
        return JobProfileView(
            position_id=position_id,
            position_title=position.title,
            version=int(record.version),
            confirmed=bool(meta.get("confirmed")),
            profile=profile,
            confirmed_by=meta.get("confirmed_by"),
            confirmed_at=meta.get("confirmed_at"),
        )

    # *kwq* 新增：匹配前置校验（未确认即拒绝）。
    # 返回：(画像, 版本号)
    async def require_confirmed(
        self, position_id: int, version: int | None = None
    ) -> tuple[JobProfileParsed, int]:
        """
        功能：匹配前取得有效画像依据。
        输入：岗位ID、可选版本。
        输出：画像和版本号。
        步骤：读取版本 → 检查存在与确认状态；不自动确认草稿。
        """
        record = await self._resolve_version(position_id, version)
        if record is None:
            # *kwq* 修改（MATCH-020）：区分「版本不存在」与「画像未确认」两种情形。
            # 修改原因：实测指定 requirement_version=999 时返回 400「该岗位尚未确认岗位画像」，
            #          但该岗位其实有 v7 且已确认 —— 报错信息指向了错误的方向，排查会被带偏。
            #          指定版本查不到属于「资源不存在」，应按 404 语义返回并带上版本号。
            if version is not None:
                raise NotFoundError(
                    f"岗位画像版本不存在（position_id={position_id}, version={version}）"
                )
            raise BusinessRuleError(
                "该岗位尚未确认岗位画像，不能执行正式匹配（请先解析并确认 JD）"
            )
        profile, meta = self._profile_from_dimensions(record.dimensions)
        if not meta.get("confirmed"):
            raise BusinessRuleError(
                f"岗位画像 v{record.version} 尚未确认，不能执行正式匹配（未确认画像不得作为匹配依据）"
            )
        return profile, int(record.version)


__all__ = ["PositionProfileService"]
