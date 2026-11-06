"""
模块：app/services/training_service.py
职责：培训管理业务服务层（智能培训赋能 Agent 的 6 个节点编排）。

标签：[Dify组] [P1]

============================ 实现思路与代码要点 ============================
节点与方法的对应关系：
    节点1 短板清单接收与诊断   diagnose
    节点2 培训方案排期         generate_plan（内部 _build_skeleton）
    节点3 课程智能匹配         generate_plan（内部 L.match_courses）
    节点4 计划推送与存档       generate_plan（落库 + 推送记录 + 事件）
    节点5 学习进度跟踪         update_progress / get_progress / scan_lag（定时调用）
    节点6 培训效果考核与复测   submit_score（发复测事件回流测评 Agent）

硬性约束（与团队规范一致）：
- Service 不处理 HTTP、不写复杂 SQL（查询交给 Repository）、不 commit（事务交给 get_db）；
- 跨模块只依赖测评模块的「只读」结果（AssessmentGapReader），绝不动它的代码与数据；
- 课程实体只能来自课程库：排期阶段不接触课程池，从信息结构上杜绝编造课程；
- 迭代原则：第 5、6 节点是「长周期 + 定时触发」，不放在图里的 while 循环中。
=========================================================================
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents import training_logic as L
from app.ai.agents.training_agent import TrainingAgent
from app.core.exceptions import (
    BusinessRuleError,
    DataConflictError,
    NotFoundError,
    ParamError,
    PermissionDeniedError,
)
from app.core.pagination import PageParams
from app.core.response import PageResult
from app.infrastructure.messaging.event_bus import (
    EVENT_TRAINING_PLAN_CREATED,
    event_bus,
)
from app.models.training import Course, TrainingPlan
from app.repositories.training_repository import TrainingRepository
from app.schemas.training import (
    CourseCreate,
    CourseUpdate,
    LearningRecordUpdate,
    ScoreSubmitRequest,
    TrainingPlanGenerateRequest,
    TrainingRecommendRequest,
)
from app.services.notification_service import TrainingNotificationService

logger = logging.getLogger(__name__)

# 结课考核默认及格线（与 training_logic.PASS_SCORE 保持一致）
DEFAULT_PASS_SCORE = L.PASS_SCORE
# 反作弊阈值：实际学时低于「按进度应付学时」的 30% 视为异常
SUSPICIOUS_RATIO = 0.30


class TrainingService:
    """培训管理业务服务。"""

    def __init__(self, db: AsyncSession, repo: TrainingRepository | None = None) -> None:
        self.db = db
        self.repo = repo or TrainingRepository()
        # 推送落地：站内信与事件在同一次业务事务里写，避免「事件发了、库里没有」
        self.notification = TrainingNotificationService(db, self.repo)
        # 诊断时用到的「标准来源」，供 generate_plan 回写进响应，便于前端解释"为什么这么排课"
        self._requirement_source: str = "default"
        self._requirement_position_id: int | None = None
        self._requirement_version: int | None = None

    # ============================================================ 节点 1：短板诊断
    async def diagnose(
        self,
        talent_id: int,
        *,
        assessment_id: int | None = None,
        gaps: Sequence[Any] | None = None,
        position_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """读取并诊断能力短板：剔除已达标项、算优先级与预估学时。

        :param gaps: 显式短板（来自测评 Agent 的契约字段）；不传则回退读测评结果。
        :param position_id: 目标岗位 ID。传了就按该岗位的能力模型定标准；
                            不传则尝试用人才「现职位」名称匹配岗位，仍匹配不到就用默认值。
        """
        raw: list[dict[str, Any]] = []
        if gaps:
            for item in gaps:
                raw.append(item.model_dump() if hasattr(item, "model_dump") else dict(item))
            # 显式传单里若已自带完整标准（required + weight），那就是调用方的口径，尊重它；
            # 只有「只给了当前分」时才去岗位模型补标准，避免测评 Agent 的单子被悄悄改写。
            has_standard = [
                L.to_float(item.get("required")) is not None
                or L.to_float(item.get("weight")) is not None
                for item in raw
            ]
            complete = all(has_standard)
            partial = any(has_standard)
            if complete:
                self._requirement_source = "explicit"
            else:
                profile = await self._resolve_requirement_model(talent_id, position_id)
                raw, filled = L.merge_requirement_model(
                    raw,
                    profile["dimensions"] if profile else None,
                    profile["weights"] if profile else None,
                )
                if profile is not None and filled > 0:
                    self._requirement_source = "position_model"
                    self._requirement_position_id = profile["position_id"]
                    self._requirement_version = profile.get("version")
                elif partial:
                    self._requirement_source = "explicit"
                else:
                    # 一条标准都没给、岗位模型也没补到 → 最终用的是诊断时的默认值
                    self._requirement_source = "default"
        else:
            raw = await self._load_gaps_from_assessment(
                talent_id, assessment_id, position_id=position_id
            )

        diagnosed = L.diagnose_gaps(raw)
        logger.info(
            "节点1 短板诊断：talent=%s 原始 %s 项 → 真短板 %s 项（标准来源=%s，岗位=%s）",
            talent_id,
            len(raw),
            len(diagnosed),
            self._requirement_source,
            self._requirement_position_id or "-",
        )
        return diagnosed

    async def _resolve_requirement_model(
        self, talent_id: int, position_id: int | None
    ) -> dict[str, Any] | None:
        """确定「按哪个岗位的标准」来诊断，返回岗位模型快照或 None。

        解析顺序（能拿到就用，拿不到就明确降级，绝不编造）：
            1. 调用方显式传的 position_id —— 最准，优先用；
            2. 人才表里的现职位文本 → 去 positions 表按 title 精确匹配；
            3. 都拿不到 → 返回 None，由调用方回退默认目标分/权重。
        """
        target = position_id
        if target is None:
            current = await self.repo.talent.current_position(self.db, talent_id)
            if current:
                target = await self.repo.position.position_id_by_title(self.db, current)
                if target is None:
                    logger.info(
                        "人才 %s 的现职位「%s」未匹配到岗位，回退默认标准", talent_id, current
                    )
                    return None
        if target is None:
            return None

        profile = await self.repo.position.profile(self.db, target)
        if profile is None:
            logger.info("岗位 %s 尚未维护能力模型，回退默认标准", target)
            return None
        return profile

    async def _load_gaps_from_assessment(
        self, talent_id: int, assessment_id: int | None, *, position_id: int | None = None
    ) -> list[dict[str, Any]]:
        """从测评结果读取维度分，并用岗位能力模型补上「目标分 / 权重」。"""
        scores: dict[str, float] = {}
        if assessment_id:
            scores = await self.repo.assessment.dimension_scores(self.db, assessment_id)
        else:
            latest = await self.repo.assessment.latest_scored_assessment(self.db, talent_id)
            if latest is not None:
                scores = await self.repo.assessment.dimension_scores(self.db, latest.id)
        if not scores:
            raise BusinessRuleError(
                "未找到可用能力短板：请传 gaps，或确保该人才有一次状态为 scored 的测评"
            )

        profile = await self._resolve_requirement_model(talent_id, position_id)
        raw, hits = L.merge_requirement_model(
            [{"dimension": dim, "current": score} for dim, score in scores.items()],
            profile["dimensions"] if profile else None,
            profile["weights"] if profile else None,
        )

        if profile is not None and hits > 0:
            self._requirement_source = "position_model"
            self._requirement_position_id = profile["position_id"]
            self._requirement_version = profile.get("version")
            logger.info(
                "节点1 采用岗位能力模型：position=%s v%s，命中 %s/%s 个维度",
                profile["position_id"],
                profile.get("version"),
                hits,
                len(scores),
            )
        else:
            self._requirement_source = "default"
            self._requirement_position_id = profile["position_id"] if profile else None
            self._requirement_version = profile.get("version") if profile else None

        return raw

    # ============================================================ 节点 2~4：生成计划
    async def generate_plan(
        self,
        req: TrainingPlanGenerateRequest,
        *,
        operator_id: int | None = None,
    ) -> dict[str, Any]:
        """节点 2/3/4：排期 → 匹配真实课程 → 落库 + 推送记录 + 事件。"""
        gaps = await self.diagnose(
            req.talent_id,
            assessment_id=req.assessment_id,
            gaps=req.gaps,
            position_id=req.position_id,
        )
        if not gaps:
            # 全达标是「最好的结果」，不是异常；但绝不能返回 id=0 冒充真实计划_id，
            # 前端拿 0 去查详情会 404。改为 id=None + no_training_needed=True。
            return {
                "id": None,
                "no_training_needed": True,
                "name": "无需培训",
                "talent_id": req.talent_id,
                "status": "finished",
                "total_weeks": req.max_weeks,
                "total_hours": 0.0,
                "completion_rate": 0.0,
                "items": [],
                "assessment": None,
                "summary": "各维度均已达标，本期无需安排培训",
                "degrade": False,
                "external_count": 0,
                "library_rate": 1.0,
                "generated_by": "rule",
                "requirement_source": self._requirement_source,
                "requirement_position_id": self._requirement_position_id,
                "requirement_version": self._requirement_version,
            }

        skeleton, generated_by, model_name = await self._build_skeleton(gaps, req)
        return await self.create_plan(
            skeleton,
            gaps,
            req,
            generated_by=generated_by,
            model_name=model_name,
            operator_id=operator_id,
        )

    async def create_plan(
        self,
        skeleton: list[dict[str, Any]],
        gaps: list[dict[str, Any]],
        req: TrainingPlanGenerateRequest,
        *,
        generated_by: str = "rule",
        model_name: str | None = None,
        operator_id: int | None = None,
    ) -> dict[str, Any]:
        """节点 3/4：课程匹配 → 落库 → 推送记录 → 事件。

        独立成方法，既给 generate_plan 复用，也给 Agent.persist 复用
        （Agent 拿 LLM 排好的 skeleton 直接落库，不必再走一遍 LLM）。
        """
        scheduled_hours = round(sum(float(row["hours"]) for row in skeleton), 1)

        # 前置校验：方案必须挂在真实人才上，否则数据库外键会抛 IntegrityError（500）
        if not await self.repo.talent.exists(self.db, req.talent_id):
            raise NotFoundError(
                f"人才不存在（id={req.talent_id}）：培训方案必须关联真实人才档案，"
                f"请先由简历解析 / 人才档案模块建档"
            )

        # 前置校验：同一人才同时只允许一份「进行中 / 草稿」的计划。
        # 没有这道检查时，POST /plans/generate 点几次就建几份同名计划，
        # 计划列表出现重复项、同一门课在两条计划下各算各的完成率（本地库已真实出现）。
        active = await self.repo.plan.has_active_plan(self.db, req.talent_id)
        if active is not None:
            if not req.replace_existing:
                raise DataConflictError(
                    f"人才 {req.talent_id} 已有进行中的培训计划（id={active.id}，"
                    f"名称「{active.name}」，状态 {active.status}）："
                    f"如确需重新生成，请传 replace_existing=true（旧计划将被置为已取消），"
                    f"或先完成 / 取消该计划"
                )
            # 替换：旧计划置为 cancelled（保留历史，不再参与滞后扫描与推送）
            await self.repo.plan.cancel(self.db, active)
            logger.info("节点2 替换旧计划：talent=%s old_plan=%s", req.talent_id, active.id)

        # ---- 节点 3：课程匹配（课程实体只能来自课程库）
        active_courses = await self.repo.course.list_active(self.db)
        course_dicts = [self._course_dict(course) for course in active_courses]
        learned = await self.repo.record.learned_course_ids(self.db, req.talent_id)
        items = L.match_courses(skeleton, gaps, course_dicts, learned)
        external_count = sum(1 for item in items if not item["is_platform"])
        library_rate = L.library_rate(items)

        # ---- 节点 4：落库
        # 调用方没传姓名时读真实姓名：方案名称和 AI 说明都要用，
        # 否则模型会把 "人才2" 这种 ID 写进文案，看起来像没接数据库。
        talent_name = req.talent_name or await self.repo.talent.name(self.db, req.talent_id)
        talent_name = talent_name or f"人才{req.talent_id}"
        start = req.start_date or date.today()
        end = start + timedelta(weeks=req.max_weeks) - timedelta(days=1)
        name = f"{talent_name}·{gaps[0]['dimension']}等{len(gaps)}项能力提升计划"

        plan = TrainingPlan(
            name=name,
            talent_id=req.talent_id,
            goal=req.goal or f"{req.max_weeks} 周内补齐 {len(gaps)} 项能力短板",
            start_date=start,
            end_date=end,
            # status 只由 draft 决定；auto_push 只决定「要不要推送」。
            # 早先两者共用 auto_push，导致「不推送」被迫等于「不启动」，语义错位。
            status="draft" if req.draft else "ongoing",
            created_by_ai=generated_by == "llm",
            source_assessment_id=req.assessment_id,
            created_by=operator_id,
        )
        self.db.add(plan)
        await self.db.flush()

        # 计划课程：按排期顺序去重，并把「周次」一起落库（详情要还原课表）
        ordered_course_ids: list[int] = []
        ordered_weeks: list[int] = []
        seen: set[int] = set()
        for item in items:
            course_id = item.get("course_id")
            if course_id and int(course_id) not in seen:
                seen.add(int(course_id))
                ordered_course_ids.append(int(course_id))
                ordered_weeks.append(int(item.get("week") or 1))
        await self.repo.plan_course.replace(
            self.db, plan.id, ordered_course_ids, weeks=ordered_weeks
        )

        # 初始化学习记录（进度 0），保证后续进度统计有基线
        for course_id in ordered_course_ids:
            await self.repo.record.upsert(
                self.db,
                plan_id=plan.id,
                course_id=course_id,
                talent_id=req.talent_id,
                progress=0,
                study_minutes=0,
            )

        # 总学时统一用「已落库课程的实际学时」：
        # 早先这里返回的是排期学时（skeleton 求和），而详情接口返回课程学时，
        # 同名字段在生成与详情两个接口给出不同数字（实测 15.0 vs 15.5），
        # 且进度计算用的是课程学时——三处口径必须同源，以落库值为准。
        total_hours = round(await self.repo.plan.total_hours(self.db, plan.id), 1)

        summary = L.summarize(
            talent_name,
            gaps,
            items,
            req.max_weeks,
            total_hours,
            external_count,
            req.pass_score,
        )
        summary_source = "rule"

        # 节点 4 增强：AI 排期时让模型把方案讲成人话，失败一律回退规则文案。
        # 为什么只在 generated_by == "llm" 时调用：规则模式下多打一次模型既费钱又没意义，
        # 更重要的是会破坏「零 Key 也能离线跑通全流程」这个能力。
        if generated_by == "llm" and self._llm_ready():
            try:
                summary_text = await TrainingAgent(db=self.db).explain_plan(
                    {
                        "人才": talent_name,
                        "总周数": req.max_weeks,
                        "总学时": round(float(total_hours), 1),
                        "结课考核及格分": req.pass_score,
                        "能力短板": [
                            {
                                "维度": gap["dimension"],
                                "当前分": gap["current"],
                                "目标分": gap["required"],
                                "优先级": gap["priority"],
                                "建议学时": gap["est_hours"],
                            }
                            for gap in gaps
                        ],
                        "课表": [
                            {
                                "周次": item["week"],
                                "维度": item["dimension"],
                                "学时": item["hours"],
                                "课程": item.get("title"),
                            }
                            for item in items
                        ],
                        "平台无课程数": external_count,
                    }
                )
                if len(summary_text) >= 20:
                    summary, summary_source = summary_text[:500], "llm"
                else:
                    logger.warning("方案说明过短，改用规则文案：%r", summary_text[:60])
            except Exception as exc:  # noqa: BLE001 - 文案失败不能影响主流程
                logger.warning("方案说明生成失败，改用规则文案：%s", exc)

        # 事件 + 推送落地：草稿或不推送时**什么都不做**，
        # 否则「存个草稿」也会给学员发一条站内信。
        if req.auto_push and not req.draft:
            plan_event = {
                "type": EVENT_TRAINING_PLAN_CREATED,
                "level": "正常",
                "plan_id": plan.id,
                "talent_id": req.talent_id,
                "plan_name": name,
                "total_hours": total_hours,
                "total_weeks": req.max_weeks,
                "title": "新的培训计划已下发",
                "content": (
                    f"「{name}」已生成：{len(items)} 门课程 / {total_hours:g} 学时 / "
                    f"{req.max_weeks} 周，结课考核及格线 {req.pass_score:g} 分。"
                ),
                "channels": ["inapp"],
            }
            # 站内信与业务同事务写入：事件是异步派发的，只靠订阅者会「事件发了、信没发」
            try:
                await self.notification.record(plan_event)
            except Exception as exc:  # noqa: BLE001 - 推送失败不影响计划本身
                logger.warning("培训计划站内信写入失败：%s", exc)
            try:
                event_bus.publish(EVENT_TRAINING_PLAN_CREATED, plan_event)
            except Exception as exc:  # noqa: BLE001 - 事件失败不影响主流程
                logger.warning("培训计划创建事件发布失败：%s", exc)

        logger.info(
            "节点2-4 完成：plan=%s 生成方式=%s 周数=%s 学时=%s 来自库=%s",
            plan.id,
            generated_by,
            req.max_weeks,
            total_hours,
            f"{library_rate:.0%}" if library_rate is not None else "N/A",
        )

        return {
            "id": plan.id,
            "no_training_needed": False,
            "name": plan.name,
            "talent_id": plan.talent_id,
            "goal": plan.goal,
            "start_date": plan.start_date,
            "end_date": plan.end_date,
            "status": plan.status,
            "created_by_ai": plan.created_by_ai,
            "source_assessment_id": plan.source_assessment_id,
            "total_weeks": req.max_weeks,
            "total_hours": total_hours,
            "scheduled_hours": scheduled_hours,
            "completion_rate": 0.0,
            "items": items,
            "assessment": {
                "week": req.max_weeks,
                "type": "结课考核",
                "pass_score": req.pass_score,
            },
            "summary": summary,
            "summary_source": summary_source,
            "degrade": external_count > 0,
            "external_count": external_count,
            "library_rate": library_rate,
            "generated_by": generated_by,
            "model_name": model_name,
            "requirement_source": self._requirement_source,
            "requirement_position_id": self._requirement_position_id,
            "requirement_version": self._requirement_version,
            "created_at": plan.created_at,
            "updated_at": plan.updated_at,
        }

    async def _build_skeleton(
        self, gaps: list[dict[str, Any]], req: TrainingPlanGenerateRequest
    ) -> tuple[list[dict[str, Any]], str, str | None]:
        """节点 2：先算规则基线（顺带校验容量），再尝试让 LLM 优化排期。"""
        try:
            baseline = L.merge_skeleton(
                L.rule_schedule(gaps, req.max_weeks, req.max_hours_per_week)
            )
        except L.CapacityExceeded as exc:
            # 容量不足必须显式报错，不允许把学时偷偷塞进最后一周
            raise BusinessRuleError(str(exc)) from exc

        if not req.use_ai:
            return baseline, "rule", None

        # 没有可用模型凭据时不发真实请求：否则要等 3 次重试 + 跨模型降级链（本机实测 5~8 秒），
        # 日志还会刷一屏 401。规则排期本身是完整可用的基线，直接用它更快也更干净。
        if not self._llm_ready():
            logger.info("未配置可用的 LLM 凭据，节点2 直接使用规则排期（generated_by=rule）")
            return baseline, "rule", None

        try:
            agent = TrainingAgent(db=self.db)
            ai_rows, model_name = await agent.suggest_skeleton(
                gaps,
                max_weeks=req.max_weeks,
                max_hours_per_week=req.max_hours_per_week,
            )
        except Exception as exc:  # noqa: BLE001 - LLM 不可用一律回退规则
            logger.warning("LLM 排期失败，回退规则排期：%s", exc)
            return baseline, "rule", None

        if ai_rows and L.legal_skeleton(
            ai_rows, [gap["dimension"] for gap in gaps], req.max_weeks, req.max_hours_per_week
        ):
            return L.merge_skeleton(ai_rows), "llm", model_name
        if ai_rows:
            logger.warning("LLM 排期结构不合法，已回退规则排期")
        return baseline, "rule", None

    # ============================================================ 计划查询
    @staticmethod
    def _llm_ready() -> bool:
        """是否具备调用大模型的条件（未配 Key 时不做无谓重试）。"""
        from app.config.settings import settings

        if (settings.LLM_PROVIDER or "").lower() == "ollama":
            return True  # 本地模型不需要 Key
        return bool((settings.LLM_API_KEY or "").strip())

    async def get_plan(self, plan_id: int) -> dict[str, Any]:
        """计划详情（含课程明细、完成率、方案说明）。"""
        plan = await self.repo.plan.get_with_courses(self.db, plan_id)
        if plan is None:
            raise NotFoundError(f"培训计划不存在（id={plan_id}）")

        items = [
            {
                # 周次用落库时的 sort（= 排期周次），而不是 enumerate 出来的序号：
                # 一门课占多周 / 同周多门课时，序号会把第 3 周的课显示成第 2 周。
                "week": link.sort,
                "dimension": link.course.category if link.course else "综合",
                "hours": float(link.course.duration_hours or 0) if link.course else 0.0,
                "course_id": link.course_id,
                "title": link.course.title if link.course else None,
                "level": L.parse_level(link.course.level) if link.course else None,
                "duration_hours": float(link.course.duration_hours or 0) if link.course else 0.0,
                "is_platform": link.course is not None,
                "reason": "计划课程",
            }
            for link in plan.plan_courses
        ]
        completion = await self.repo.plan.completion_rate(self.db, plan_id)
        total_hours = await self.repo.plan.total_hours(self.db, plan_id)
        library_rate = L.library_rate(items)
        # 周数按计划起止日期算（生成时按 max_weeks 排，详情必须给出同一个口径）；
        # 日期缺失时才退回「课表里出现过的最大周次」。
        weeks_from_dates = self._weeks_between(plan.start_date, plan.end_date)
        total_weeks = weeks_from_dates or (max((link.sort for link in plan.plan_courses), default=0) or 1)

        return {
            "id": plan.id,
            "name": plan.name,
            "talent_id": plan.talent_id,
            "goal": plan.goal,
            "start_date": plan.start_date,
            "end_date": plan.end_date,
            "status": plan.status,
            "created_by_ai": plan.created_by_ai,
            "source_assessment_id": plan.source_assessment_id,
            "total_weeks": total_weeks,
            "total_hours": round(total_hours, 1),
            "completion_rate": completion,
            "items": items,
            "assessment": {
                "week": total_weeks,
                "type": "结课考核",
                "pass_score": DEFAULT_PASS_SCORE,
            },
            "summary": (
                f"共 {len(items)} 门课程 / {total_hours:g} 学时，当前完成率 {completion:.0%}。"
            ),
            "degrade": False,
            "external_count": 0,
            "library_rate": library_rate,
            "created_at": plan.created_at,
            "updated_at": plan.updated_at,
        }

    async def page_plans(
        self,
        page: PageParams,
        *,
        talent_id: int | None = None,
        status: str | None = None,
    ) -> PageResult:
        """计划分页列表（批量算完成率，避免逐行查询）。"""
        filters = {"talent_id": talent_id, "status": status}
        result = await self.repo.plan.paginate(self.db, page, filters=filters)
        ids = [row.id for row in result.items]
        rates = await self.repo.plan.completion_rates(self.db, ids) if ids else {}
        items = [
            {
                "id": row.id,
                "name": row.name,
                "talent_id": row.talent_id,
                "goal": row.goal,
                "start_date": row.start_date,
                "end_date": row.end_date,
                "status": row.status,
                "created_by_ai": row.created_by_ai,
                "completion_rate": rates.get(row.id, 0.0),
                "created_at": row.created_at,
            }
            for row in result.items
        ]
        result.items = items
        return result

    # ============================================================ 节点 5：进度跟踪
    async def update_progress(
        self,
        data: LearningRecordUpdate,
        *,
        operator_id: int | None = None,
    ) -> dict[str, Any]:
        """写回学习进度：进度单调递增 + 学时与进度做反作弊校验。"""
        plan = await self.repo.plan.get_by_id(self.db, data.plan_id)
        if plan is None or plan.is_deleted:
            raise NotFoundError(f"培训计划不存在（id={data.plan_id}）")

        course = await self.repo.course.get_by_id(self.db, data.course_id)
        if course is None or course.is_deleted:
            raise NotFoundError(f"课程不存在（id={data.course_id}）")

        # 归属校验：学习记录必须落在「该计划的该人才」名下。
        # 不校验的两个后果都是线上事故：
        #   1) 传别人的 talent_id → 外键不存在的 500，或（更糟）把进度记到另一个真实人才名下；
        #   2) 传计划外的课程 → 记录照样写入，但 plan_courses 里没有它，
        #      完成率的分母被稀释、通过率按课程拆分时出现「幽灵课程」。
        if data.talent_id != plan.talent_id:
            raise ParamError(
                f"人才与计划不匹配：计划 {data.plan_id} 属于人才 {plan.talent_id}，"
                f"本次上报的人才为 {data.talent_id}"
            )
        if not await self.repo.plan_course.has_course(self.db, data.plan_id, data.course_id):
            raise ParamError(
                f"课程 {data.course_id} 不属于计划 {data.plan_id}："
                f"请先由计划生成 / 调整接口把该课程加入计划再上报进度"
            )

        record = await self.repo.record.get_record(
            self.db, data.plan_id, data.course_id, data.talent_id
        )
        old_progress = record.progress if record else 0
        if data.progress < old_progress:
            raise ParamError(
                f"学习进度不允许回退：当前 {old_progress}%，本次提交 {data.progress}%"
            )

        # 反作弊：按进度应付学时 × 30% 作为下限，明显不足则标记 suspicious（不阻断）
        duration_hours = float(course.duration_hours or 8)
        expected_minutes = duration_hours * 60 * (data.progress / 100)
        suspicious = bool(
            data.progress > 0
            and data.study_minutes < expected_minutes * SUSPICIOUS_RATIO
        )

        # 注意：这里**不写 passed**。progress=100 只代表「学完了」（完成率），
        # 不等于「考过了」（通过率）。早期版本在这里写 passed=True，
        # 会让没参加考核的记录进入通过率分母且计为通过，通过率虚高；
        # 通过结论只由 submit_score（节点 6）产生。
        saved = await self.repo.record.upsert(
            self.db,
            plan_id=data.plan_id,
            course_id=data.course_id,
            talent_id=data.talent_id,
            progress=data.progress,
            study_minutes=data.study_minutes,
            suspicious=suspicious,
        )

        if suspicious:
            logger.warning(
                "疑似刷课：plan=%s course=%s progress=%s%% 仅学 %s 分钟（应约 %.0f 分钟）",
                data.plan_id,
                data.course_id,
                data.progress,
                data.study_minutes,
                expected_minutes,
            )

        return {
            "id": saved.id,
            "plan_id": saved.plan_id,
            "course_id": saved.course_id,
            "talent_id": saved.talent_id,
            "progress": saved.progress,
            "study_minutes": saved.study_minutes,
            "last_study_at": saved.last_study_at,
            "status": saved.status,
            "score": saved.score,
            "passed": saved.passed,
            "suspicious": saved.suspicious,
        }

    async def get_progress(self, plan_id: int, *, publish: bool = False) -> dict[str, Any]:
        """按「时间进度 vs 完成学时」判定正常 / 提醒 / HR 预警。

        `publish` 默认 **False**：这是查询侧计算，不是推送侧动作。
        曾经默认 True，导致 GET /plans/{id}/progress 每被调用一次就发一次事件——
        前端轮询、页面刷新、HR 多点几下都会重复推送，一旦有了订阅者就是轰炸。
        推送只由 POST /lag-scan（定时任务）触发。
        """
        plan = await self.repo.plan.get_by_id(self.db, plan_id)
        if plan is None or plan.is_deleted:
            raise NotFoundError(f"培训计划不存在（id={plan_id}）")

        total_hours = await self.repo.plan.total_hours(self.db, plan_id)
        minutes = await self.repo.record.sum_minutes(self.db, plan_id)
        elapsed = self._elapsed_ratio(plan.start_date, plan.end_date)
        progress = L.compute_progress(total_hours, elapsed, minutes / 60)

        event = L.lag_event(progress["alert"], plan_id, progress)
        if event and publish:
            try:
                event_bus.publish(event["type"], event)
            except Exception as exc:  # noqa: BLE001
                logger.warning("滞后事件发布失败：%s", exc)

        return {
            "plan_id": plan_id,
            "total_hours": round(total_hours, 1),
            **progress,
            "event": event,
        }

    async def scan_lag(self, *, publish: bool = True) -> dict[str, Any]:
        """节点 5 的定时入口：扫描进行中计划，产出预警清单。

        由调度器（cron / APScheduler / 外部定时任务）按天调用，
        不写在 Agent 图里循环——进程重启不会丢进度。
        """
        plans = await self.repo.plan.list_ongoing(self.db)
        if not plans:
            logger.info("节点5 滞后扫描：无进行中计划")
            return {"scanned": 0, "alerts": [], "reminded": 0, "warned": 0}

        # 批量聚合（两条 SQL 拿全部计划的总学时与累计分钟），替代逐计划 3~4 次查询。
        # 原先 N 个计划要跑 3N~4N 次查询，计划一多扫描就变慢，还会拖长事务。
        totals = await self.repo.plan.batch_progress(self.db, [plan.id for plan in plans])

        alerts: list[dict[str, Any]] = []
        reminded = warned = 0
        for plan in plans:
            total_hours, minutes = totals.get(plan.id, (0.0, 0))
            try:
                elapsed = self._elapsed_ratio(plan.start_date, plan.end_date)
                progress = L.compute_progress(total_hours, elapsed, minutes / 60)
            except Exception as exc:  # noqa: BLE001 - 单条失败不影响整体扫描
                logger.warning("计划 %s 进度计算失败：%s", plan.id, exc)
                continue

            if progress["alert"] == "HR预警":
                warned += 1
            elif progress["alert"] == "提醒":
                reminded += 1
            if progress["alert"] not in ("HR预警", "提醒"):
                continue

            event = L.lag_event(progress["alert"], plan.id, progress)
            if event is not None:
                # 补齐通知订阅者需要的信息：事件里没有收件人和人话文案，
                # 订阅方要么再查一次库，要么只能写一条没头没脑的站内信。
                event.update(
                    {
                        "talent_id": plan.talent_id,
                        "plan_name": plan.name,
                        "title": (
                            f"培训进度{('严重滞后' if progress['alert'] == 'HR预警' else '落后提醒')}"
                            f"：{plan.name}"
                        ),
                        "content": (
                            f"{event['detail']}；计划总学时 {total_hours:g}h，"
                            f"当前完成 {progress['completed_h']:g}h。"
                        ),
                    }
                )
                if publish:
                    # 推送两件事一起做：落站内信（可查、可已读）+ 发事件（供其他订阅方）。
                    # 站内信按 (计划, 类型, 日期) 幂等，扫描重跑不会重复轰炸。
                    try:
                        await self.notification.record(event)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("滞后站内信写入失败：%s", exc)
                    try:
                        event_bus.publish(event["type"], event)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("滞后事件发布失败：%s", exc)
            alerts.append(
                {
                    "plan_id": plan.id,
                    "talent_id": plan.talent_id,
                    "name": plan.name,
                    "total_hours": round(total_hours, 1),
                    "completed_h": progress["completed_h"],
                    "completion": progress["completion"],
                    "lag_ratio": progress["lag_ratio"],
                    "alert": progress["alert"],
                    "event": event,
                }
            )
        logger.info("节点5 滞后扫描：计划 %s 个，预警 %s 个，提醒 %s 个", len(plans), warned, reminded)
        return {"scanned": len(plans), "alerts": alerts, "reminded": reminded, "warned": warned}

    # ============================================================ 节点 6：考核复测
    async def submit_score(
        self,
        data: ScoreSubmitRequest,
        *,
        actor_talent_id: int | None = None,
    ) -> dict[str, Any]:
        """提交结课考核成绩：判定是否通过，达标则发复测事件回流测评 Agent。

        :param actor_talent_id: 非管理者（仅持 training:report）操作时，由路由传入
                                「本人绑定的人才 ID」；与计划所属人才不一致即拒绝，
                                否则「自助上报」等于「任意人能提交任意人的考核」。
        """
        plan_id = data.plan_id
        if plan_id is None:
            raise ParamError("缺少培训计划 ID（plan_id）：请在路径 /trainings/plans/{plan_id}/score 中指定")
        plan = await self.repo.plan.get_by_id(self.db, plan_id)
        if plan is None or plan.is_deleted:
            raise NotFoundError(f"培训计划不存在（id={plan_id}）")

        # 已结课的计划不允许默默重打分：重复提交会把「已通过」覆盖成「不通过」，
        # 连带改掉已归档的通过率统计（历史报表数字会变）。
        # 确需改判（复议 / 补考）由调用方显式传 force=true。
        if plan.status == "finished" and not data.force:
            raise DataConflictError(
                f"培训计划 {plan_id} 已结课（finished），不允许重复打分："
                f"如确需改判（复议 / 补考），请传 force=true"
            )

        # 人才以「计划所属人才」为准：前端漏传则自动取，传了但不一致直接拒绝，
        # 否则会把 A 的成绩写到 B 的学习记录上（统计全乱且无法追溯）。
        talent_id = data.talent_id if data.talent_id is not None else plan.talent_id
        if talent_id != plan.talent_id:
            raise ParamError(
                f"人才与计划不匹配：计划 {plan_id} 属于人才 {plan.talent_id}，"
                f"本次提交的人才为 {talent_id}"
            )
        # 自助口径：非管理者只能给自己打分
        if actor_talent_id is not None and int(actor_talent_id) != int(plan.talent_id):
            raise PermissionDeniedError(
                f"只能提交本人的考核成绩：计划 {plan_id} 属于人才 {plan.talent_id}，"
                f"当前账号绑定的人才为 {actor_talent_id}"
            )

        dimensions = list(data.dimensions)
        if not dimensions:
            dimensions = await self._plan_dimensions(plan_id)

        result = L.assess(
            data.score,
            pass_score=DEFAULT_PASS_SCORE,
            auto_retest=data.auto_retest,
            plan_id=plan_id,
            talent_id=talent_id,
            dimensions=dimensions,
        )

        # 写回考核结果：指定课程就落到该课程；整计划结课考核则落到计划下每门课的记录上。
        # 为什么整计划也要落库：分数若只存在 plan.status 里，通过率就永远统计不到（分母恒为 0）。
        if data.course_id:
            course_ids = [data.course_id]
            course = await self.repo.course.get_by_id(self.db, data.course_id)
            if course is None or course.is_deleted:
                raise NotFoundError(f"课程不存在（id={data.course_id}）")
            if not await self.repo.plan_course.has_course(self.db, plan_id, data.course_id):
                raise ParamError(f"课程 {data.course_id} 不属于计划 {plan_id}，无法计入该计划考核")
        else:
            links = await self.repo.plan_course.list_by_plan(self.db, plan_id)
            course_ids = [link.course_id for link in links]

        # 写回考核结果。这里**不传 study_minutes**：
        # upsert 对 study_minutes 是覆盖写，传 0 不是「不改」而是「改成 0」。
        # 旧实现传了 study_minutes=0，等于每次结课考核都把整份计划的学习时长清零，
        # 之后 GET /progress 永远返回「完成 0h / 落后 100%」，
        # 未通过时计划仍是 ongoing，下一次 lag-scan 会把刚学完 4 周的学员判成 HR 预警。
        # 同时传 touch_study_time=False：登记成绩不是学习行为，不该刷新「最近学习时间」。
        examined_at = datetime.now()
        for course_id in course_ids:
            await self.repo.record.upsert(
                self.db,
                plan_id=plan_id,
                course_id=course_id,
                talent_id=talent_id,
                progress=100,
                score=data.score,
                passed=result["passed"],
                examined_at=examined_at,
                touch_study_time=False,
                # 结课考核场景显式纠正疑似刷课标记：考完就代表学完，
                # 此前若因学时不足被打标，不应继续挂在学员档案上。
                suspicious=False,
            )
        await self.db.flush()

        # 只有「整计划结课考核」才改变计划状态。
        # 单门课考核通过就把整份计划标成 finished 是错的：4 门课里考过 1 门，
        # 计划就再也不会被滞后扫描，后面的课等于自动豁免——这是真的漏培。
        if data.course_id:
            logger.info(
                "节点6 单课考核：plan=%s course=%s 结果=%s（不改变计划状态）",
                plan_id,
                data.course_id,
                result["assessment"],
            )
        else:
            plan.status = "finished" if result["passed"] else "ongoing"

        # 事件：复测回流测评 Agent / 计划调整提醒（事件而非直接调用，降低耦合、HR 可关）
        for event in result["events"]:
            # 补齐收件人与计划名：事件里带不上，订阅方写站内信时只能写一条没头没尾的通知
            event.setdefault("talent_id", talent_id)
            event.setdefault("plan_name", plan.name)
            try:
                await self.notification.record(event)
            except Exception as exc:  # noqa: BLE001 - 推送失败不影响考核结论
                logger.warning("考核站内信写入失败：%s", exc)
            try:
                event_bus.publish(event["type"], event)
            except Exception as exc:  # noqa: BLE001
                logger.warning("考核事件发布失败：%s", exc)

        logger.info(
            "节点6 考核：plan=%s talent=%s 分数=%s 结论=%s 事件=%s",
            plan_id,
            talent_id,
            data.score,
            result["assessment"],
            [event["type"] for event in result["events"]],
        )

        return {
            "plan_id": plan_id,
            "talent_id": talent_id,
            "score": float(data.score),
            "passed": result["passed"],
            "assessment": result["assessment"],
            "plan_status": plan.status,
            "need_human": result["need_human"],
            "events": result["events"],
        }

    async def _plan_dimensions(self, plan_id: int) -> list[str]:
        """计划覆盖的能力维度（取计划课程的分类）。"""
        links = await self.repo.plan_course.list_by_plan(self.db, plan_id)
        course_ids = [link.course_id for link in links]
        courses = await self.repo.course.list_by_ids(self.db, course_ids)
        dims = [course.category for course in courses if course.category]
        return list(dict.fromkeys(dims))

    async def pass_rate(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        course_id: int | None = None,
        plan_id: int | None = None,
    ) -> dict[str, Any]:
        """节点 6 的「统计通过率」：按课程与整体汇总考核结果。

        与「完成率」的区别（答辩常被追问）：
        - 完成率 = 学完了没有（进度），衡量**过程**；
        - 通过率 = 考过了没有（passed），衡量**结果**。
        只统计已出结论的记录，没考的不进分母。
        """
        stats = await self.repo.record.pass_rate_stats(
            self.db, start=start, end=end, course_id=course_id, plan_id=plan_id
        )
        return {
            "scope": {
                "start": start.isoformat() if start else None,
                "end": end.isoformat() if end else None,
                "course_id": course_id,
                "plan_id": plan_id,
            },
            **stats,
        }

    # ============================================================ 课程管理
    async def create_course(self, data: CourseCreate, *, operator_id: int | None = None) -> Course:
        """创建课程（同名去重，避免重复入库导致排课混乱）。"""
        exists = await self.repo.course.get_by_title(self.db, data.title)
        if exists is not None:
            raise BusinessRuleError(f"课程已存在：{data.title}")
        await self._warn_unknown_dimension(data.category)
        course = Course(**data.model_dump(), created_by=operator_id)
        self.db.add(course)
        await self.db.flush()
        return course

    async def update_course(self, course_id: int, data: CourseUpdate) -> Course:
        """更新课程（只改传入字段；改名同样要做重名校验）。"""
        course = await self.repo.course.get_by_id(self.db, course_id)
        if course is None or course.is_deleted:
            raise NotFoundError(f"课程不存在（id={course_id}）")
        payload = data.model_dump(exclude_unset=True)

        # 改名必须查重：create 有校验、update 曾经没有，可以把课程 B 改成与 A 同名，
        # 之后按 title 幂等导入（seed 脚本）会命中 id 较小那条，更新到错误的课程上。
        new_title = payload.get("title")
        if isinstance(new_title, str) and new_title.strip() and new_title != course.title:
            dup = await self.repo.course.get_by_title(self.db, new_title)
            if dup is not None and dup.id != course.id:
                raise BusinessRuleError(
                    f"课程名称已存在：{new_title}（id={dup.id}），不能改成重名课程"
                )
        if "category" in payload:
            await self._warn_unknown_dimension(payload["category"])

        for field, value in payload.items():
            setattr(course, field, value)
        await self.db.flush()
        return course

    async def _warn_unknown_dimension(self, category: str | None) -> None:
        """维度不在库内已有集合里时只告警、不阻断。

        为什么不阻断：新维度确实可能先于课程库建立（业务上允许开新课）。
        但要留痕——节点 3 匹配是 `category` 一字不差，拼错一个字不会报错，
        只会静默降级成「库内暂无该维度课程」，事后极难排查。
        """
        if not category or not str(category).strip():
            return
        known = await self.repo.course.list_categories(self.db)
        if known and str(category).strip() not in known:
            logger.warning(
                "课程维度「%s」不在库内已有维度中（现有：%s）："
                "节点3 按维度一字不差匹配，该课程可能永远不会被排进课表",
                category,
                "、".join(sorted(known)) or "无",
            )

    async def delete_course(self, course_id: int) -> None:
        """下架课程：走软删除。

        为什么不物理删除：历史计划（plan_courses）与学习记录都引用 course_id，
        硬删会让老计划的课表出现空洞、通过率统计丢分母。
        软删后新排课不再选它（list_active 过滤 is_active），历史数据保持完整。
        """
        course = await self.repo.course.get_by_id(self.db, course_id)
        if course is None or course.is_deleted:
            raise NotFoundError(f"课程不存在（id={course_id}）")
        await self.repo.course.delete(self.db, course, soft=True)

    async def page_courses(
        self,
        page: PageParams,
        *,
        keyword: str | None = None,
        category: str | None = None,
        level: str | None = None,
        is_active: bool | None = None,
    ) -> PageResult:
        """课程分页列表。"""
        filters = {"category": category, "level": level, "is_active": is_active}
        return await self.repo.course.paginate(self.db, page, filters=filters, keyword=keyword)

    # ============================================================ 课程推荐
    async def recommend(
        self,
        req: TrainingRecommendRequest,
        *,
        gaps: Sequence[Any] | None = None,
    ) -> dict[str, Any]:
        """按能力短板推荐课程（每个维度取最贴合层级的前 top_k 门）。"""
        diagnosed = await self.diagnose(req.talent_id, gaps=req.gaps or gaps)
        if not diagnosed:
            return {"talent_id": req.talent_id, "recommendations": [], "summary": "各维度均已达标"}

        active = await self.repo.course.list_active(self.db)
        learned = await self.repo.record.learned_course_ids(self.db, req.talent_id)
        pool = [self._course_dict(course) for course in active]

        recommendations: list[dict[str, Any]] = []
        for gap in diagnosed:
            dimension = gap["dimension"]
            target = L.target_level(gap.get("required"))
            candidates = [
                course
                for course in pool
                if L.course_match_dimension(course, dimension)
                and int(course.get("course_id") or 0) not in learned
            ]
            scored = []
            for course in candidates:
                level = L.parse_level(course.get("level"))
                fit = max(0.0, 100.0 - abs(level - target) * 15.0)
                scored.append((fit, course))
            scored.sort(key=lambda pair: (-pair[0], -float(pair[1].get("duration_hours") or 0)))

            for fit, course in scored[: req.top_k]:
                recommendations.append(
                    {
                        "course_id": int(course["course_id"]),
                        "title": course["title"],
                        "level": L.parse_level(course.get("level")),
                        "duration_hours": L.to_float(course.get("duration_hours"), 0.0),
                        "gap_dimension": dimension,
                        "reason": f"补齐「{dimension}」缺口 {gap['delta']:g} 分（{gap['priority']}）",
                        "score": round(fit, 1),
                    }
                )

        return {
            "talent_id": req.talent_id,
            "recommendations": recommendations,
            "summary": (
                f"围绕 {len(diagnosed)} 项短板共推荐 {len(recommendations)} 门课程"
                f"（全部来自平台课程库）。"
            ),
        }

    # ============================================================ 内部工具
    @staticmethod
    def _course_dict(course: Course) -> dict[str, Any]:
        """ORM 课程转纯字典（交给算法层，避免算法依赖 ORM）。"""
        tags = course.skill_tags if isinstance(course.skill_tags, list) else []
        return {
            "course_id": course.id,
            "title": course.title,
            "category": course.category,
            "level": course.level,
            "duration_hours": float(course.duration_hours or 0) if course.duration_hours is not None else 0.0,
            "skill_tags": tags,
        }

    @staticmethod
    def _weeks_between(start: date | None, end: date | None) -> int:
        """计划跨越的自然周数（含首尾）；日期缺失返回 0 表示「无法判定」。"""
        if not start or not end:
            return 0
        days = (end - start).days + 1
        if days <= 0:
            return 0
        return -(-days // 7)  # 向上取整，避免 import math

    @staticmethod
    def _elapsed_ratio(start: date | None, end: date | None, today: date | None = None) -> float:
        """计划周期已过去的比例（0~1），无日期返回 0。"""
        if not start or not end:
            return 0.0
        today = today or date.today()
        total_days = (end - start).days
        if total_days <= 0:
            return 1.0 if today >= end else 0.0
        if today <= start:
            return 0.0
        if today >= end:
            return 1.0
        return round((today - start).days / total_days, 4)


__all__ = ["TrainingService", "DEFAULT_PASS_SCORE", "SUSPICIOUS_RATIO"]
