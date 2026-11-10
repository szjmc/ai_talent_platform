"""
模块：app/services/ai_interview_service.py
职责：AI 数字人面试业务服务（创建会话 / 生成计划 / 逐轮对话 / 评估报告）。

标签：[Agent组] [P1]（AI 面试业务编排）

================================ 流程 ================================
1. 创建：解析人才 / 岗位 / 邀约 -> 生成问题计划（大模型可选，失败规则降级）
   -> 落库并生成候选人免登录链接 -> 可选发送邮件。
2. 开始：pending -> in_progress，记录开场白与第一题（幂等，刷新可恢复）。
3. 作答：候选人回答落库并即时规则评分；回答过短/证据不足时最多追问一次；
   追问结束或无需追问则进入下一题；最后一题答完自动生成报告。
4. 报告：规则引擎先给出可解释的逐题分 / 维度分 / 综合分（事实口径），
   大模型仅在可用时润色 summary 与逐题评语，**不修改分数**；
   未作答题目不按 0 分计入综合分，完成率单独展示。
5. 生命周期：completed / cancelled / expired；过期或超时自动收尾，不丢已答内容。

分层约束：
- 本层不写 SQL（走 Repository），不拼 HTTP 响应（Router 包装 success）；
- token 只作为公开访问凭证，日志不打印完整 token；
- 候选人公开接口只返回对话与结果摘要，不返回规则权重 / signals / 关键词。
======================================================================
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm.provider_config import provider_configured
from app.ai.core.llm_manager import llm_manager
from app.ai.prompts.interview_prompt import (
    FEEDBACK_PROMPT_NAME,
    PLAN_PROMPT_NAME,
    prompt_registry,
)
from app.ai.utils.output_parser import parse_json
from app.config.settings import settings
from app.core.exceptions import BusinessRuleError, NotFoundError
from app.core.middleware import request_base_url_ctx
from app.core.pagination import PageParams
from app.core.response import PageResult
from app.models.ai_interview import AiInterviewSession, AiInterviewTurn
from app.repositories.ai_interview_repository import (
    AiInterviewSessionRepository,
    AiInterviewTurnRepository,
)
from app.repositories.invitation_repository import TalentInvitationRepository
from app.repositories.position_repository import PositionRepository
from app.repositories.talent_repository import TalentRepository
from app.schemas.ai_interview import AiInterviewCreateRequest
from app.services import ai_interview_engine as engine
from app.services.invite_notify import send_email as send_plain_email

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"completed", "cancelled", "expired"}
ALLOWED_DIMENSIONS = set(engine.DIMENSIONS)


def build_ai_interview_url(token: str) -> str:
    """候选人面试链接：优先显式配置的公开基址，其次请求 Host，最后相对路径。"""
    base = settings.INVITE_BASE_URL or settings.FRONTEND_URL or settings.PUBLIC_BASE_URL
    if not base:
        base = request_base_url_ctx.get() or ""
    path = f"/ai-interview/{token}"
    return f"{base.rstrip('/')}{path}" if base else path


def _as_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


class AiInterviewService:
    """AI 数字人面试服务。"""

    def __init__(
        self,
        db: AsyncSession,
        session_repo: AiInterviewSessionRepository | None = None,
        turn_repo: AiInterviewTurnRepository | None = None,
        talent_repo: TalentRepository | None = None,
        position_repo: PositionRepository | None = None,
        invite_repo: TalentInvitationRepository | None = None,
    ) -> None:
        self.db = db
        self.repo = session_repo or AiInterviewSessionRepository()
        self.turn_repo = turn_repo or AiInterviewTurnRepository()
        self.talent_repo = talent_repo or TalentRepository()
        self.position_repo = position_repo or PositionRepository()
        self.invite_repo = invite_repo or TalentInvitationRepository()

    # ================================================================ 创建
    async def create(self, payload: AiInterviewCreateRequest, user: Any) -> dict[str, Any]:
        """创建面试会话并返回链接 / 邮件发送结果。"""
        talent = None
        invite = None
        if payload.invitation_id:
            invite = await self.invite_repo.get_by_id_or_404(
                self.db, payload.invitation_id, message="面试邀约不存在"
            )
            if invite.type != "interview":
                raise BusinessRuleError("所选邀约不是面试类型，无法创建 AI 面试")
        if payload.talent_id:
            talent = await self.talent_repo.get_by_id(self.db, payload.talent_id)
            if talent is None:
                raise NotFoundError("人才档案不存在")
        elif invite is not None and invite.talent_id:
            talent = await self.talent_repo.get_by_id(self.db, invite.talent_id)

        position = None
        if payload.position_id:
            position = await self.position_repo.get_by_id(self.db, payload.position_id)
            if position is None:
                raise NotFoundError("岗位不存在")

        snapshot = invite.talent_snapshot if invite is not None and isinstance(invite.talent_snapshot, dict) else {}
        name = (payload.candidate_name or (talent.name if talent else "") or snapshot.get("name") or "").strip()
        email = (payload.candidate_email or (talent.email if talent else "") or snapshot.get("email") or "").strip() or None
        position_title = (
            (position.title if position else "")
            or (talent.current_position if talent else "")
            or (invite.title if invite else "")
            or None
        )

        persona_key = engine.resolve_persona(payload.interviewer_persona)
        persona = engine.PERSONAS[persona_key]
        language = (payload.language or persona.get("language") or "zh-CN").strip()
        profile = self._profile(talent, invite)
        position_info = self._position_info(position, profile, position_title)
        plan, plan_source = await self._build_plan(
            profile=profile,
            position=position_info,
            count=payload.question_count,
            difficulty=payload.difficulty,
            language=language,
            persona=persona,
        )
        if not plan:
            raise BusinessRuleError("未能生成面试问题，请稍后重试")

        token = await self.repo.mint_token(self.db)
        expires_at = datetime.now() + timedelta(hours=payload.expires_hours)
        title = payload.title or f"{name} · {position_title or 'AI 数字人面试'}"
        session = AiInterviewSession(
            talent_id=getattr(talent, "id", None),
            position_id=getattr(position, "id", None),
            invitation_id=getattr(invite, "id", None),
            assessment_id=payload.assessment_id,
            token=token,
            title=title,
            candidate_name=name,
            candidate_email=email,
            position_title=position_title,
            interviewer_name=(payload.interviewer_name or str(persona["name"])).strip(),
            interviewer_title=str(persona["title"]),
            interviewer_persona=persona_key,
            interviewer_style=str(persona["style"]),
            avatar_theme=str(persona["avatar_theme"]),
            voice_hint=str(persona["voice_hint"]),
            language=language,
            status="pending",
            difficulty=payload.difficulty,
            question_count=len(plan),
            duration_minutes=payload.duration_minutes,
            plan=plan,
            expires_at=expires_at,
            created_by=getattr(user, "id", None),
            updated_by=getattr(user, "id", None),
        )
        self.db.add(session)
        await self.db.flush()
        await self.db.refresh(session)

        url = build_ai_interview_url(token)
        email_result = None
        if payload.send_email and email:
            email_result = await self._send_link_email(session, url)
        logger.info(
            "AI 面试已创建 id=%s candidate=%s 题数=%s plan_source=%s",
            session.id, name, len(plan), plan_source,
        )
        data = self._session_brief(session)
        data["plan_source"] = plan_source
        data["email"] = email_result
        return data

    def _profile(self, talent: Any, invite: Any) -> dict[str, Any]:
        """候选人画像（只用真实档案字段，不补充臆测信息）。"""
        if talent is None:
            return {}
        return {
            "name": talent.name,
            "work_years": talent.work_years,
            "education": talent.education,
            "school": talent.school,
            "major": talent.major,
            "current_company": talent.current_company,
            "current_position": talent.current_position,
            "skills": list(talent.skill_tags or []),
            "self_evaluation": talent.self_evaluation,
            "portrait_summary": talent.portrait_summary,
            "potential_level": talent.potential_level,
        }

    def _position_info(
        self, position: Any, profile: dict[str, Any], position_title: str | None
    ) -> dict[str, Any]:
        skills: list[Any] = []
        requirements: dict[str, Any] = {}
        if position is not None:
            skills = list(position.skill_tags or [])
            if isinstance(position.requirements, dict):
                requirements = position.requirements
                skills = skills or list(requirements.get("skills") or [])
        return {
            "title": position_title or "",
            "level": getattr(position, "level", None) or "",
            "skills": skills,
            "requirements": requirements,
            "description": (getattr(position, "description", None) or "")[:1200],
        }

    # ---------------------------------------------------------------- 问题计划
    async def _build_plan(
        self,
        *,
        profile: dict[str, Any],
        position: dict[str, Any],
        count: int,
        difficulty: str,
        language: str,
        persona: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], str]:
        """规则计划兜底 + 可选大模型个性化；任何模型异常都回落到规则计划。"""
        rule_plan = engine.build_interview_plan(
            profile, position, count=count, difficulty=difficulty, language=language
        )
        if not settings.AI_INTERVIEW_LLM_PLAN or not provider_configured(settings.LLM_PROVIDER):
            return rule_plan, "rule"
        try:
            prompt = prompt_registry.render(
                PLAN_PROMPT_NAME,
                persona=f"{persona.get('name')}（{persona.get('title')}，{persona.get('style')}）",
                profile=json.dumps(profile, ensure_ascii=False),
                position=json.dumps(position, ensure_ascii=False),
                difficulty=difficulty,
                count=count,
                language=language,
            )
            result = await asyncio.wait_for(
                llm_manager.generate(
                    prompt,
                    system="你是结构化面试设计专家。只输出符合要求的 JSON，不编造候选人事实。",
                    purpose="interview_plan",
                    temperature=0.4,
                    max_tokens=1800,
                ),
                timeout=settings.AI_INTERVIEW_LLM_TIMEOUT_SECONDS,
            )
            data = parse_json(result.content)
            raw_items = data.get("questions") if isinstance(data, dict) else data
            items = self._normalize_llm_plan(raw_items, rule_plan, count)
            if len(items) < 3:
                raise ValueError("模型返回有效问题不足 3 道")
            logger.info("AI 面试问题计划由模型生成：model=%s count=%s", result.model, len(items))
            return items, f"llm:{result.model}"
        except Exception as exc:  # noqa: BLE001 - 模型不可用必须降级而不是阻断创建
            logger.warning("AI 面试问题计划模型生成失败，回落规则计划：%s", type(exc).__name__)
            return rule_plan, "rule"

    def _normalize_llm_plan(
        self, raw_items: Any, fallback: list[dict[str, Any]], count: int
    ) -> list[dict[str, Any]]:
        """清洗模型输出：限制长度、校验维度、补齐数量、去除重复。"""
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        if not isinstance(raw_items, list):
            return fallback[:count]
        for index, raw in enumerate(raw_items[: max(count, 1)]):
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text") or raw.get("question") or "").strip()
            if len(text) < 5 or len(text) > 500 or text in seen:
                continue
            dimension = str(raw.get("dimension") or "communication").strip()
            if dimension not in ALLOWED_DIMENSIONS:
                dimension = "communication"
            keywords = raw.get("keywords")
            keywords = [str(k).strip()[:30] for k in keywords if str(k).strip()] if isinstance(keywords, list) else []
            items.append(
                {
                    "id": len(items) + 1,
                    "dimension": dimension,
                    "text": text,
                    "intent": str(raw.get("intent") or "").strip()[:200],
                    "keywords": keywords or engine.DEFAULT_DIMENSION_KEYWORDS[dimension],
                    "follow_up": str(raw.get("follow_up") or "").strip()[:300],
                    "weight": 1.0,
                }
            )
            seen.add(text)
        # 模型问题不足时用规则题补齐（跳过语义重复的题面）
        for fallback_item in fallback:
            if len(items) >= count:
                break
            if fallback_item["text"] in seen:
                continue
            items.append({**fallback_item, "id": len(items) + 1})
            seen.add(fallback_item["text"])
        return items[:count]

    # ---------------------------------------------------------------- 邮件
    async def _send_link_email(self, session: AiInterviewSession, url: str) -> dict[str, Any]:
        if not session.candidate_email:
            return {"channel": "email", "ok": False, "mode": "smtp", "error": "候选人未提供邮箱"}
        subject = f"【AI 面试】{session.title}"
        body = "\n".join(
            [
                f"{session.candidate_name or '候选人'}，您好：",
                "",
                f"诚邀您参加「{session.position_title or session.title}」的 AI 数字人面试。",
                f"面试官：{session.interviewer_name}（{session.interviewer_title}）",
                f"预计时长：约 {session.duration_minutes} 分钟，共 {session.question_count} 个问题",
                f"面试链接（{_as_naive(session.expires_at)} 前有效）：",
                url,
                "",
                "参加提示：",
                "1. 建议使用 Chrome / Edge 浏览器，并允许麦克风权限；",
                "2. 可以使用语音或文字作答，回答越具体越有助于评估；",
                "3. 链接仅限本人使用，请勿转发。",
                "",
                "—— AI 数字化人才平台",
            ]
        )
        try:
            result = await send_plain_email(session.candidate_email, subject, body)
        except Exception as exc:  # noqa: BLE001 - 邮件失败不阻断创建
            logger.warning("AI 面试链接邮件发送失败：%s", type(exc).__name__)
            return {"channel": "email", "ok": False, "mode": "smtp", "error": "邮件发送失败"}
        return result

    # ================================================================ host 查询
    async def page_list(
        self,
        page: PageParams,
        *,
        status: str | None = None,
        keyword: str | None = None,
        talent_id: int | None = None,
        position_id: int | None = None,
    ) -> PageResult:
        filters = {
            key: value
            for key, value in {
                "status": status,
                "talent_id": talent_id,
                "position_id": position_id,
            }.items()
            if value is not None
        }
        result = await self.repo.paginate(self.db, page, filters=filters or None, keyword=keyword)
        result.items = [self._session_brief(row) for row in result.items]
        return result

    async def detail(self, session_id: int) -> dict[str, Any]:
        row = await self.repo.get_by_id_or_404(self.db, session_id, message="AI 面试不存在")
        return await self._session_detail(row)

    async def report(self, session_id: int) -> dict[str, Any]:
        row = await self.repo.get_by_id_or_404(self.db, session_id, message="AI 面试不存在")
        if row.status != "completed" or not row.report:
            raise BusinessRuleError("面试尚未结束，暂无评估报告")
        return {**row.report, "session_id": row.id, "candidate_name": row.candidate_name}

    async def cancel(self, session_id: int, user: Any) -> dict[str, Any]:
        row = await self.repo.get_by_id_or_404(self.db, session_id, message="AI 面试不存在")
        if row.status in TERMINAL_STATUSES:
            raise BusinessRuleError("面试已结束，无法取消")
        row.status = "cancelled"
        row.finished_at = datetime.now()
        row.updated_by = getattr(user, "id", None)
        await self.db.flush()
        return self._session_brief(row)

    # ================================================================ 候选人公开接口
    async def public_state(self, token: str) -> dict[str, Any]:
        row = await self._get_by_token(token)
        turns = await self.turn_repo.list_by_session(self.db, row.id)
        return self._public_state(row, turns)

    async def start(self, token: str) -> dict[str, Any]:
        row = await self._get_by_token(token, lock=True)
        if row.status in {"cancelled", "expired"}:
            raise BusinessRuleError("面试已取消或链接已过期，请联系 HR")
        if row.status == "completed":
            turns = await self.turn_repo.list_by_session(self.db, row.id)
            return self._public_state(row, turns)
        if row.status == "pending":
            plan = row.plan or []
            if not plan:
                raise BusinessRuleError("面试问题尚未生成，请联系 HR")
            now = datetime.now()
            row.status = "in_progress"
            row.started_at = now
            row.current_index = 0
            row.current_question_kind = "main"
            row.follow_up_used = False
            row.answered_count = 0
            greeting = self._greeting(row)
            await self._add_turn(row, role="interviewer", kind="greeting", content=greeting)
            first = plan[0]
            await self._add_turn(
                row,
                role="interviewer",
                kind="main",
                content=str(first.get("text") or ""),
                question_index=0,
                dimension=str(first.get("dimension") or ""),
            )
            await self.db.flush()
            logger.info("AI 面试开始 id=%s", row.id)
        turns = await self.turn_repo.list_by_session(self.db, row.id)
        return self._public_state(row, turns)

    async def answer(
        self,
        token: str,
        *,
        answer: str,
        duration_seconds: int = 0,
        input_mode: str = "text",
    ) -> dict[str, Any]:
        row = await self._get_by_token(token, lock=True)
        if row.status == "completed":
            turns = await self.turn_repo.list_by_session(self.db, row.id)
            return {
                "accepted": False,
                "completed": True,
                "timed_out": False,
                "interviewer_reply": "",
                "follow_up": False,
                "next_question": None,
                "progress": self._progress(row),
                "state": self._public_state(row, turns),
                "result": self._candidate_result(row),
            }
        if row.status != "in_progress":
            raise BusinessRuleError("面试尚未开始或已结束")

        deadline = self._deadline(row)
        if deadline is not None and datetime.now() > deadline:
            await self._finalize(row, timed_out=True)
            turns = await self.turn_repo.list_by_session(self.db, row.id)
            return {
                "accepted": False,
                "completed": True,
                "timed_out": True,
                "interviewer_reply": "",
                "follow_up": False,
                "next_question": None,
                "progress": self._progress(row),
                "state": self._public_state(row, turns),
                "result": self._candidate_result(row),
            }

        plan = row.plan or []
        index = max(0, min(int(row.current_index or 0), len(plan) - 1))
        question = plan[index] if plan else {}
        kind = row.current_question_kind if row.current_question_kind in {"main", "follow_up"} else "main"
        language = row.language or "zh-CN"

        scored = engine.score_answer(
            question, answer, duration_seconds=duration_seconds, language=language
        )
        await self._add_turn(
            row,
            role="candidate",
            kind="answer",
            content=answer,
            question_index=index,
            dimension=str(question.get("dimension") or ""),
            input_mode="voice" if input_mode == "voice" else "text",
            duration_seconds=max(0, int(duration_seconds or 0)),
            score=scored.score,
            signals=scored.signals,
            feedback=scored.feedback,
        )

        reply = engine.acknowledge(scored.score, language)
        follow_up = False
        next_question: dict[str, Any] | None = None
        completed = False

        if kind == "main":
            row.answered_count = int(row.answered_count or 0) + 1
            if not row.follow_up_used and engine.should_follow_up(question, answer, scored.score, language):
                follow_up = True
                row.follow_up_used = True
                row.current_question_kind = "follow_up"
                follow_text = engine.follow_up_text(question, language)
                content = f"{reply} {follow_text}".strip()
                await self._add_turn(
                    row,
                    role="interviewer",
                    kind="follow_up",
                    content=content,
                    question_index=index,
                    dimension=str(question.get("dimension") or ""),
                )
                next_question = {
                    "index": index,
                    "text": follow_text,
                    "kind": "follow_up",
                    "dimension": str(question.get("dimension") or "communication"),
                    "dimension_label": engine.DIMENSION_LABELS.get(
                        str(question.get("dimension") or "communication"), "综合"
                    ),
                    "intent": str(question.get("intent") or ""),
                }
                reply = content
            else:
                advanced = await self._advance(row, language)
                reply = f"{reply} {advanced['reply']}".strip()
                next_question = advanced["next_question"]
                completed = advanced["completed"]
        else:
            advanced = await self._advance(row, language)
            reply = f"{reply} {advanced['reply']}".strip()
            next_question = advanced["next_question"]
            completed = advanced["completed"]

        await self.db.flush()
        turns = await self.turn_repo.list_by_session(self.db, row.id)
        return {
            "accepted": True,
            "completed": completed,
            "timed_out": False,
            "interviewer_reply": reply,
            "follow_up": follow_up,
            "next_question": next_question,
            "progress": self._progress(row),
            "state": self._public_state(row, turns),
            "result": self._candidate_result(row) if completed else None,
        }

    async def repeat(self, token: str) -> dict[str, Any]:
        """重复当前问题（数字人重读一遍）。"""
        row = await self._get_by_token(token)
        if row.status == "completed":
            return {"text": engine.closing_text(row.candidate_name, row.language), "completed": True}
        turns = await self.turn_repo.list_by_session(self.db, row.id)
        for turn in reversed(turns):
            if turn.role == "interviewer" and turn.kind in {"greeting", "main", "follow_up", "closing"}:
                return {"text": turn.content, "completed": False}
        if row.plan:
            return {"text": str(row.plan[0].get("text") or ""), "completed": False}
        return {"text": "", "completed": False}

    async def finish(self, token: str) -> dict[str, Any]:
        """候选人提前结束面试（已答内容照常评估）。"""
        row = await self._get_by_token(token, lock=True)
        if row.status == "completed":
            turns = await self.turn_repo.list_by_session(self.db, row.id)
            return self._public_state(row, turns)
        if row.status != "in_progress":
            raise BusinessRuleError("面试尚未开始")
        await self._finalize(row, timed_out=False)
        turns = await self.turn_repo.list_by_session(self.db, row.id)
        return self._public_state(row, turns)

    # ================================================================ 内部：推进 / 收尾
    async def _advance(self, row: AiInterviewSession, language: str) -> dict[str, Any]:
        """推进到下一个主问题；没有下一题则收尾并出报告。"""
        plan = row.plan or []
        next_index = int(row.current_index or 0) + 1
        row.follow_up_used = False
        row.current_question_kind = "main"
        if next_index >= len(plan):
            await self._finalize(row, timed_out=False)
            return {"completed": True, "next_question": None, "reply": ""}
        row.current_index = next_index
        question = plan[next_index]
        text = str(question.get("text") or "")
        await self._add_turn(
            row,
            role="interviewer",
            kind="main",
            content=text,
            question_index=next_index,
            dimension=str(question.get("dimension") or ""),
        )
        return {
            "completed": False,
            "reply": text,
            "next_question": {
                "index": next_index,
                "text": text,
                "kind": "main",
                "dimension": str(question.get("dimension") or "communication"),
                "dimension_label": engine.DIMENSION_LABELS.get(
                    str(question.get("dimension") or "communication"), "综合"
                ),
                "intent": str(question.get("intent") or ""),
            },
        }

    async def _finalize(self, row: AiInterviewSession, *, timed_out: bool) -> dict[str, Any]:
        """生成报告并落库（幂等：已完成直接返回）。"""
        if row.status == "completed":
            return row.report or {}
        turns = await self.turn_repo.list_by_session(self.db, row.id)
        report = engine.evaluate_session(row.plan or [], turns, language=row.language or "zh-CN")
        if timed_out:
            report["summary"] = f"{report.get('summary', '')}（本次面试已超时自动收尾）".strip()
            report["timed_out"] = True
        if settings.AI_INTERVIEW_LLM_FEEDBACK and provider_configured(settings.LLM_PROVIDER):
            report = await self._polish_report(row, report, turns)
        row.status = "completed"
        row.finished_at = datetime.now()
        row.total_score = float(report.get("total_score") or 0)
        row.dimension_scores = report.get("dimension_scores") or {}
        row.level = str(report.get("level") or "")
        row.recommendation = str(report.get("recommendation") or "")
        row.report = report
        row.generator = str(report.get("scoring_method") or "rule")
        closing = engine.closing_text(row.candidate_name, row.language)
        if not any(turn.role == "interviewer" and turn.kind == "closing" for turn in turns):
            await self._add_turn(row, role="interviewer", kind="closing", content=closing)
        await self.db.flush()
        logger.info(
            "AI 面试结束 id=%s 得分=%s 建议=%s 生成方式=%s",
            row.id, row.total_score, row.recommendation, row.generator,
        )
        return report

    async def _polish_report(
        self, row: AiInterviewSession, report: dict[str, Any], turns: list[AiInterviewTurn]
    ) -> dict[str, Any]:
        """大模型润色：只改 summary / 逐题评语 / 观察点，分数保持规则引擎结果。"""
        try:
            payload = {
                "language": row.language,
                "position": row.position_title,
                "completion_rate": report.get("completion_rate"),
                "total_score": report.get("total_score"),
                "level": report.get("level"),
                "recommendation": report.get("recommendation"),
                "dimension_scores": report.get("dimension_scores"),
                "questions": [
                    {
                        "index": item.get("index"),
                        "question": item.get("question"),
                        "dimension": item.get("dimension"),
                        "score": item.get("score"),
                        "answer": (item.get("answer") or "")[:1200],
                        "signals": {
                            key: (item.get("signals") or {}).get(key)
                            for key in ("chinese_chars", "word_count", "keywords_hit", "numbers", "structure_hits")
                        },
                    }
                    for item in report.get("per_question") or []
                ],
            }
            prompt = prompt_registry.render(
                FEEDBACK_PROMPT_NAME, payload=json.dumps(payload, ensure_ascii=False)
            )
            result = await asyncio.wait_for(
                llm_manager.generate(
                    prompt,
                    system="你是面试评估复核专家。只依据给定回答写评语，禁止修改分数或编造事实。",
                    purpose="interview_feedback",
                    temperature=0.2,
                    max_tokens=1500,
                ),
                timeout=settings.AI_INTERVIEW_LLM_TIMEOUT_SECONDS,
            )
            data = parse_json(result.content)
            if not isinstance(data, dict):
                return report
            summary = str(data.get("summary") or "").strip()
            if summary:
                report["summary"] = summary[:1200]
            feedback_map = {
                int(item.get("index")): str(item.get("feedback") or "").strip()[:500]
                for item in (data.get("per_question") or [])
                if isinstance(item, dict) and str(item.get("index", "")).lstrip("-").isdigit()
            }
            for item in report.get("per_question") or []:
                feedback = feedback_map.get(int(item.get("index", -1)))
                if feedback and item.get("score") is not None:
                    item["feedback"] = feedback
            observations = [
                str(item).strip()[:200] for item in (data.get("observations") or []) if str(item).strip()
            ]
            if observations:
                report["observations"] = observations[:5]
            report["model_name"] = result.model
            report["scoring_method"] = f"rule+llm:{result.model}"
            return report
        except Exception as exc:  # noqa: BLE001 - 润色失败不影响规则报告
            logger.warning("AI 面试报告润色失败，保留规则版：%s", type(exc).__name__)
            return report

    # ================================================================ 内部：查询 / 序列化
    async def _get_by_token(self, token: str, *, lock: bool = False) -> AiInterviewSession:
        if not token or len(token) < 8:
            raise NotFoundError("面试链接无效")
        row = (
            await self.repo.get_by_token_for_update(self.db, token)
            if lock
            else await self.repo.get_by_token(self.db, token)
        )
        if row is None or row.is_deleted:
            raise NotFoundError("面试链接无效或面试不存在")
        if row.expires_at is not None and datetime.now() > _as_naive(row.expires_at) and row.status in {
            "pending",
            "in_progress",
        }:
            raise BusinessRuleError("面试链接已过期，请联系 HR 重新获取")
        return row

    async def _add_turn(
        self,
        row: AiInterviewSession,
        *,
        role: str,
        kind: str,
        content: str,
        question_index: int | None = None,
        dimension: str | None = None,
        input_mode: str | None = None,
        duration_seconds: int = 0,
        score: float | None = None,
        signals: dict[str, Any] | None = None,
        feedback: str | None = None,
    ) -> AiInterviewTurn:
        seq = await self.turn_repo.next_seq(self.db, row.id)
        turn = AiInterviewTurn(
            session_id=row.id,
            seq=seq,
            role=role,
            kind=kind,
            question_index=question_index,
            dimension=dimension or None,
            content=content,
            input_mode=input_mode,
            duration_seconds=max(0, int(duration_seconds or 0)),
            score=score,
            signals=signals,
            feedback=feedback,
        )
        self.db.add(turn)
        await self.db.flush()
        return turn

    def _deadline(self, row: AiInterviewSession) -> datetime | None:
        candidates: list[datetime] = []
        if row.expires_at:
            candidates.append(_as_naive(row.expires_at))  # type: ignore[arg-type]
        if row.started_at:
            candidates.append(_as_naive(row.started_at) + timedelta(minutes=int(row.duration_minutes or 30)))  # type: ignore[operator]
        return min(candidates) if candidates else None

    def _progress(self, row: AiInterviewSession) -> dict[str, Any]:
        total = int(row.question_count or len(row.plan or []) or 0)
        answered = int(row.answered_count or 0)
        percent = round(answered / total * 100) if total else 0
        if row.status == "completed":
            percent = 100
            answered = min(answered, total)
        remaining = None
        if row.status == "in_progress":
            deadline = self._deadline(row)
            if deadline is not None:
                remaining = max(0, int((deadline - datetime.now()).total_seconds()))
        return {
            "answered": answered,
            "total": total,
            "percent": percent,
            "current_index": int(row.current_index or 0),
            "remaining_seconds": remaining,
        }

    def _greeting(self, row: AiInterviewSession) -> str:
        return engine.greeting_text(
            name=row.candidate_name,
            interviewer=row.interviewer_name,
            position_title=row.position_title,
            question_count=int(row.question_count or len(row.plan or [])),
            duration_minutes=int(row.duration_minutes or 30),
            language=row.language or "zh-CN",
        )

    def _interviewer(self, row: AiInterviewSession) -> dict[str, Any]:
        persona = engine.persona_profile(row.interviewer_persona)
        return {
            "key": persona["key"],
            "name": row.interviewer_name or persona["name"],
            "title": row.interviewer_title or persona["title"],
            "style": row.interviewer_style or persona["style"],
            "avatar_theme": row.avatar_theme or persona["avatar_theme"],
            "voice_hint": row.voice_hint or persona["voice_hint"],
            "language": row.language or persona["language"],
            "tagline": persona.get("tagline") or "",
        }

    def _session_brief(self, row: AiInterviewSession) -> dict[str, Any]:
        recommendation = row.recommendation
        return {
            "id": row.id,
            "talent_id": row.talent_id,
            "position_id": row.position_id,
            "invitation_id": row.invitation_id,
            "assessment_id": row.assessment_id,
            "token": row.token,
            "url": build_ai_interview_url(row.token),
            "title": row.title,
            "candidate_name": row.candidate_name,
            "candidate_email": row.candidate_email,
            "position_title": row.position_title,
            "status": row.status,
            "difficulty": row.difficulty,
            "question_count": int(row.question_count or 0),
            "duration_minutes": int(row.duration_minutes or 0),
            "language": row.language,
            "interviewer": self._interviewer(row),
            "answered_count": int(row.answered_count or 0),
            "total_score": float(row.total_score) if row.total_score is not None else None,
            "level": row.level,
            "recommendation": recommendation,
            "recommendation_label": engine.RECOMMENDATION_LABELS.get(recommendation or ""),
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "expires_at": row.expires_at,
            "created_at": row.created_at,
        }

    async def _session_detail(self, row: AiInterviewSession) -> dict[str, Any]:
        turns = await self.turn_repo.list_by_session(self.db, row.id)
        data = self._session_brief(row)
        data.update(
            {
                "plan": row.plan or [],
                "turns": [
                    {
                        "id": turn.id,
                        "seq": turn.seq,
                        "role": turn.role,
                        "kind": turn.kind,
                        "question_index": turn.question_index,
                        "dimension": turn.dimension,
                        "content": turn.content,
                        "input_mode": turn.input_mode,
                        "duration_seconds": turn.duration_seconds,
                        "score": float(turn.score) if turn.score is not None else None,
                        "signals": turn.signals,
                        "feedback": turn.feedback,
                        "created_at": turn.created_at,
                    }
                    for turn in turns
                ],
                "report": row.report,
            }
        )
        return data

    def _current_question(self, row: AiInterviewSession, turns: list[AiInterviewTurn]) -> dict[str, Any] | None:
        if row.status == "completed":
            return None
        for turn in reversed(turns):
            if turn.role == "interviewer" and turn.kind in {"main", "follow_up"}:
                plan_item = {}
                if row.plan and turn.question_index is not None and 0 <= turn.question_index < len(row.plan):
                    plan_item = row.plan[turn.question_index] or {}
                dimension = turn.dimension or str(plan_item.get("dimension") or "communication")
                # 追问轮次的 content 含过渡语，前端展示以 plan 中的追问原文为准
                text = turn.content
                if turn.kind == "follow_up":
                    configured = str(plan_item.get("follow_up") or "").strip()
                    if configured and configured in turn.content:
                        text = configured
                return {
                    "index": int(turn.question_index if turn.question_index is not None else 0),
                    "text": text,
                    "kind": turn.kind if turn.kind in {"main", "follow_up"} else "main",
                    "dimension": dimension,
                    "dimension_label": engine.DIMENSION_LABELS.get(dimension, dimension),
                    "intent": str(plan_item.get("intent") or ""),
                }
        if row.status == "pending" and row.plan:
            item = row.plan[0]
            dimension = str(item.get("dimension") or "communication")
            return {
                "index": 0,
                "text": str(item.get("text") or ""),
                "kind": "main",
                "dimension": dimension,
                "dimension_label": engine.DIMENSION_LABELS.get(dimension, dimension),
                "intent": str(item.get("intent") or ""),
            }
        return None

    def _public_state(self, row: AiInterviewSession, turns: list[AiInterviewTurn]) -> dict[str, Any]:
        greeting = next(
            (turn.content for turn in turns if turn.role == "interviewer" and turn.kind == "greeting"),
            self._greeting(row),
        )
        transcript = [
            {
                "seq": turn.seq,
                "role": turn.role,
                "kind": turn.kind,
                "content": turn.content,
                "created_at": turn.created_at,
            }
            for turn in turns
            if turn.kind != "greeting"
        ]
        return {
            "token": row.token,
            "title": row.title,
            "status": row.status,
            "candidate_name": row.candidate_name,
            "position_title": row.position_title,
            "language": row.language,
            "difficulty": row.difficulty,
            "duration_minutes": int(row.duration_minutes or 0),
            "question_count": int(row.question_count or 0),
            "interviewer": self._interviewer(row),
            "greeting": greeting,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "deadline": self._deadline(row),
            "progress": self._progress(row),
            "current_question": self._current_question(row, turns),
            "transcript": transcript,
            "result": self._candidate_result(row),
            "server_time": datetime.now(),
        }

    def _candidate_result(self, row: AiInterviewSession) -> dict[str, Any] | None:
        """候选人可见的结果：只给分数、维度与建议，不暴露对 HR 的录用建议与逐题内部信号。"""
        report = row.report if isinstance(row.report, dict) else None
        if not report:
            return None
        summary = str(report.get("summary") or "")
        # 报告 summary 末尾会拼接「推荐：建议进入下一轮」等 HR 结论，候选人侧必须剥离。
        for label in engine.RECOMMENDATION_LABELS.values():
            summary = summary.replace(f"{label}。", "").replace(label, "")
        summary = summary.replace("候选人", "你").strip()
        # 发展建议只保留对候选人有价值的成长项，过滤「安排下一轮/人工复核」等招聘决策语句。
        hr_only = ("进入下一轮", "人工复核", "补充完整面试")
        suggestions = [s for s in (report.get("suggestions") or []) if not any(k in s for k in hr_only)]
        if not suggestions:
            suggestions = ["继续用「情境—行动—结果」沉淀项目案例，并补充可量化的业务结果。"]
        return {
            "total_score": report.get("total_score"),
            "level": report.get("level"),
            "dimension_scores": report.get("dimension_scores") or {},
            "dimension_labels": report.get("dimension_labels") or engine.DIMENSION_LABELS,
            "answered_count": report.get("answered_count"),
            "question_count": report.get("question_count"),
            "completion_rate": report.get("completion_rate"),
            "summary": summary.strip(),
            "strengths": report.get("strengths") or [],
            "weaknesses": report.get("weaknesses") or [],
            "suggestions": suggestions,
            "per_question": [
                {
                    "index": item.get("index"),
                    "question": item.get("question"),
                    "dimension_label": item.get("dimension_label"),
                    "score": item.get("score"),
                    "feedback": item.get("feedback"),
                }
                for item in report.get("per_question") or []
            ],
            "scoring_method": report.get("scoring_method"),
        }


__all__ = ["AiInterviewService", "build_ai_interview_url"]
