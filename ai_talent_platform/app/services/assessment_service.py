"""
模块：app/services/assessment_service.py
职责：人才测评业务服务层（校验 + 仓库调用 + 外部能力 + 事务）。

标签：[Vanna组] [P1]

对外方法：
    # 笔试
    - create_assessment()      按岗位/层级组卷 -> 落库 Assessment + 题目
    - start_assessment()       校验状态 -> 返回题目(不含答案) -> 记 started_at
    - submit_answers()         校验超时 -> 存答题 -> 客观题规则判分 -> 置 submitted
    - get_result()             分数 + 维度分 + 等级 + AI 报告
    - stats_by_position()      岗位平均分统计
    # 面试记录表（拍照上传 -> 向量库）
    - save_uploaded_record()   校验 -> 存图 -> AI 解析 -> 落 MySQL -> 向量化入 interview_record_vectors
    - list_interview_records() 记录表分页（走 Repository 白名单）
    # 综合报告（代码直出，不调 Dify）
    - generate_report()        笔试 + 向量库面试记录 + 简历 -> 五维量化报告 + 提升建议

分层约束（公共层手册 §十二 / FAQ Q9）：
    - 不处理 HTTP（Router 只做参数接收与响应包装）；
    - 不写裸 SQL（查询全部走 Repository）；
    - 客观题规则判分在本层（rule_score），主观题语义打分交 AssessmentAgent；
    - 标准答案绝不随 start_assessment 返回给前端；
    - 向量库走 BaseVectorStore 统一接口（Milvus 优先，local 降级）。

五维雷达（radar 字段供前端五维图渲染，均 0-100）：
    comprehensive 综合素质 / professional 专业能力 / position_fit 岗位适配度 /
    education 学历背景 / potential 发展潜力
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.vector_store.interview_vector_store import get_vector_store, new_vector_id
from app.ai.agents.interview_record_parser import (
    INTERVIEW_RECORD_COLLECTION,
    parse_record_image,
)
from app.ai.rag.record_embedding import embed_query
from app.config.logging_config import get_logger
from app.config.settings import settings
from app.core.exceptions import BusinessRuleError, NotFoundError, ParamError
from app.models.assessment import Assessment, AssessmentQuestion, InterviewRecordSheet
from app.repositories.assessment_repository import (
    AssessmentRepository,
    InterviewRecordRepository,
)

logger = get_logger(__name__)

# 状态机允许的流转
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"in_progress"},
    "in_progress": {"submitted"},
    "submitted": {"scored"},
}

# 上传图片白名单
_ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}

# 学历层级 -> 学历背景分（0-100）
_EDU_SCORE = {5: 92, 4: 85, 3: 75, 2: 62, 1: 50}

# 五维雷达（顺序即前端渲染顺序）
RADAR_DIMS = ["comprehensive", "professional", "position_fit", "education", "potential"]
RADAR_LABELS = {
    "comprehensive": "综合素质", "professional": "专业能力", "position_fit": "岗位适配度",
    "education": "学历背景", "potential": "发展潜力",
}


class AssessmentService:
    """人才测评业务服务（笔试 + 面试记录表 + 综合报告）。"""

    def __init__(
        self,
        db: AsyncSession,
        repo: AssessmentRepository | None = None,
        record_repo: InterviewRecordRepository | None = None,
    ) -> None:
        self.db = db
        self.repo = repo or AssessmentRepository()
        self.record_repo = record_repo or InterviewRecordRepository()

    # ================================================================ 笔试：按 file_id 提取简历文本
    async def extract_resume_text_from_file(self, file_id: int) -> str:
        """按前端上传简历返回的 file_id 读取原件并提取纯文本（PDF/Word/图片 OCR）。

        供「人才测评分析 Agent」创建测评时使用：前端只需传 file_id，
        后端自动完成原件读取 -> 文本提取 -> 简历解析 -> 组卷的闭环。
        """
        import asyncio
        import tempfile
        from pathlib import Path

        from app.ai.rag.document_loader import load
        from app.repositories.file_repository import FileRepository
        from app.services.file_service import FileService

        record = await FileRepository().get_by_id_or_404(self.db, file_id, message="简历文件不存在")
        data = await FileService(self.db).read(record)
        with tempfile.TemporaryDirectory(prefix="assess-resume-") as temp:
            path = Path(temp) / record.stored_name
            await asyncio.to_thread(path.write_bytes, data)
            docs = await asyncio.to_thread(load, str(path), {"file_id": file_id})
        text = "\n".join(d.page_content for d in docs).strip()
        if len(text) < 10:
            raise BusinessRuleError("简历文本提取失败或内容过少，请检查文件后重新上传")
        logger.info("[assessment] file_id=%s 简历文本提取完成，长度=%s", file_id, len(text))
        return text

    # ================================================================ 笔试：按 file_id 全自动组卷
    async def create_assessment_from_file(
        self,
        *,
        file_id: int,
        title: str = "人才能力测评",
        question_count: int = 5,
        user: Any = None,
    ) -> dict[str, Any]:
        """仅凭 file_id 完成「读简历 -> 解析关键字 -> 组卷 -> 自动建档」全流程。

        返回 {assessment, keywords, difficulty, questions}，供 Router 组装响应并推送邮件。
        """
        from app.ai.agents.resume_parser import (
            infer_difficulty,
            parse_resume_with_mode,
            retrieve_questions_rag,
        )

        resume_text = await self.extract_resume_text_from_file(file_id)
        kw, parse_mode = await parse_resume_with_mode(self.db, user, resume_text)
        difficulty = infer_difficulty(kw)
        questions = await retrieve_questions_rag(
            self.db, user, difficulty, kw.get("skills", []), count=question_count)

        # 自动创建人才档案（Assessment.talent_id 非空，且便于后续画像/报告回查）
        talent = await self._ensure_talent(kw, file_id, user)

        assessment = await self.repo.create(self.db, {
            "talent_id": talent.id,
            "position_id": None,
            "title": title,
            "status": "pending",
            "question_count": len(questions),
            "created_by_ai": True,
        })
        for i, q in enumerate(questions):
            self.db.add(AssessmentQuestion(
                assessment_id=assessment.id,
                type=q.get("type", "single"),
                content=q["content"],
                options=q.get("options"),
                answer=q.get("answer"),
                score=Decimal(str(q.get("score", 0))),
                dimension=q.get("dimension"),
                sort=i,
            ))
        await self.db.flush()
        logger.info("file_id=%s 自动组卷完成 assessment_id=%s difficulty=%s 题数=%s parse_mode=%s",
                    file_id, assessment.id, difficulty, len(questions), parse_mode)
        return {
            "assessment": assessment,
            "keywords": kw,
            "difficulty": difficulty,
            "questions": questions,
            "parse_mode": parse_mode,
        }

    async def _ensure_talent(self, kw: dict[str, Any], file_id: int, user: Any) -> Any:
        """根据解析出的简历关键字自动创建人才档案（供测评挂靠）。"""
        from app.models.talent import Talent

        education = (kw.get("education") or "").strip() or None
        if education in ("未识别", "未知"):
            education = None
        name = (kw.get("name") or "").strip()
        if not name:
            raise BusinessRuleError("简历未识别出姓名，请人工补充后再生成测评")
        talent = Talent(
            name=name,
            email=(kw.get("email") or "").strip() or None,
            education=education,
            work_years=int(float(kw.get("years") or 0)),
            current_position=(kw.get("position_title") or "").strip() or None,
            skill_tags=kw.get("skills") or [],
            source="ai_resume",
            created_by=getattr(user, "id", None),
        )
        self.db.add(talent)
        await self.db.flush()
        logger.info("自动创建人才档案 id=%s source_file_id=%s", talent.id, file_id)
        return talent

    # ================================================================ 笔试：创建测评（组卷）
    async def create_assessment(
        self,
        *,
        talent_id: int,
        position_id: int | None,
        title: str,
        job_level: str = "中级",
        profession: str = "数字化",
        grade: str = "中级",
        question_count: int = 5,
        user: Any = None,
    ) -> Assessment:
        """从授权数据库题库组卷并落库；模型/调用方不能提供题目事实或标准答案。"""
        from app.ai.agents.resume_parser import retrieve_questions_rag

        keywords = await self.repo.get_talent_keywords(self.db, talent_id)
        if not keywords:
            raise NotFoundError("人才不存在，无法创建测评")
        questions = await retrieve_questions_rag(
            self.db, user, job_level, keywords.get("skills") or [], count=question_count,
        )
        if not questions:
            raise BusinessRuleError("数据库题库未返回题目，不能创建空测评")
        assessment = await self.repo.create(
            self.db,
            {
                "talent_id": talent_id,
                "position_id": position_id,
                "title": title,
                "status": "pending",
                "question_count": len(questions),
                "created_by_ai": True,
            },
        )
        # 批量落题目（标准答案一起入库，绝不返回前端）
        for i, q in enumerate(questions):
            self.db.add(AssessmentQuestion(
                assessment_id=assessment.id,
                type=q.get("type", "single"),
                content=q["content"],
                options=q.get("options"),
                answer=q.get("answer"),           # 标准答案
                score=Decimal(str(q.get("score", 0))),
                dimension=q.get("dimension"),
                sort=i,
            ))
        await self.db.flush()
        logger.info("创建测评 id=%s 题数=%s", assessment.id, len(questions))
        return assessment

    # ================================================================ 笔试：开始测评
    async def start_assessment(self, assessment_id: int) -> dict[str, Any]:
        """开始测评：状态校验 -> 返回题目(剥离答案) -> 记 started_at。

        幂等设计：pending 首次开考 -> in_progress；in_progress 重复取卷（刷新页面）
        直接放行，避免候选人刷新后无法再次加载试卷；已交卷/已评分等终态拒绝取卷。
        """
        assessment = await self.repo.get_by_id_or_404(self.db, assessment_id, message="测评不存在")
        if assessment.status == "pending":
            self._guard_transition(assessment.status, "in_progress")
            await self.repo.update_status(
                self.db, assessment_id, "in_progress", started_at=datetime.now()
            )
        elif assessment.status != "in_progress":
            # 已交卷（submitted）/已评分（scored）等终态，禁止再次取卷
            self._guard_transition(assessment.status, "in_progress")

        questions = await self.repo.list_questions(self.db, assessment_id)
        return {
            "assessment_id": assessment_id,
            "title": assessment.title,
            "expire_at": assessment.deadline.isoformat() if assessment.deadline else None,
            # 关键：不返回 answer（标准答案）
            "questions": [
                {
                    "question_id": q.id,
                    "type": q.type,
                    "content": q.content,
                    "options": q.options,
                    "score": float(q.score or 0),
                    "dimension": q.dimension,
                    "sort": q.sort,
                }
                for q in questions
            ],
        }

    # ================================================================ 笔试：提交答案
    async def submit_answers(self, assessment_id: int, answers: list[dict]) -> dict[str, Any]:
        """提交答案：存答题 + 客观题规则判分 + 置 submitted。"""
        assessment = await self.repo.get_by_id_or_404(
            self.db, assessment_id, message="测评不存在", lock=True,
        )
        self._guard_transition(assessment.status, "submitted")

        # 取出题目标准答案，用于规则判分
        questions = {q.id: q for q in await self.repo.list_questions(self.db, assessment_id)}
        if not questions:
            raise BusinessRuleError("测评试卷为空，无法提交答案")
        deadline = assessment.deadline
        if deadline is not None:
            now = datetime.now()
            if deadline.tzinfo is None:
                now = now.replace(tzinfo=None)
            if now > deadline:
                raise BusinessRuleError("测评已截止，不能继续提交答案")
        submitted_ids = [int(item["question_id"]) for item in answers]
        if len(submitted_ids) != len(set(submitted_ids)):
            raise ParamError("同一道题不能重复提交")
        unknown = set(submitted_ids) - set(questions)
        if unknown:
            raise ParamError("答案包含不属于本次测评的题目")
        rule_total = 0.0
        dimension_scores: dict[str, float] = {}
        for item in answers:
            qid = int(item["question_id"])
            question = questions[qid]
            # 客观题规则判分；主观题(text)留给 Agent 语义打分
            if question.type in ("single", "multiple"):
                got = self._normalize_choice(item.get("answer"))
                std = self._normalize_choice(question.answer)
                sc = float(question.score) if std and got == std else 0.0
                scored_by = "rule"
                rule_total += sc
                dim = question.dimension or "未分类"
                dimension_scores[dim] = round(dimension_scores.get(dim, 0.0) + sc, 2)
            else:
                sc = None
                scored_by = "ai"
            await self.repo.save_answer(
                self.db,
                assessment_id=assessment_id,
                question_id=qid,
                answer=item.get("answer"),
                score=sc,
                scored_by=scored_by,
            )

        await self.repo.update_status(
            self.db, assessment_id, "submitted", submitted_at=datetime.now()
        )
        return {
            "assessment_id": assessment_id,
            "rule_total_score": round(rule_total, 2),
            "dimension_rule_scores": dimension_scores,
            "answered": len(submitted_ids),
            # 主观题数量提示 Agent 走语义打分
            "need_ai_score": sum(1 for q in questions.values() if q.type == "text"),
        }

    async def load_analysis_state(self, assessment_id: int) -> dict[str, Any]:
        """从数据库读取已提交试卷和答案，禁止用调用方提供的分数替代事实。"""
        assessment = await self.repo.get_by_id_or_404(
            self.db, assessment_id, message="测评不存在",
        )
        if assessment.status not in ("submitted", "scored"):
            raise BusinessRuleError("仅已提交的测评可以执行评分分析")
        questions = await self.repo.list_questions(self.db, assessment_id)
        if not questions:
            raise BusinessRuleError("测评试卷为空，无法评分")
        stored = {answer.question_id: answer for answer in await self.repo.list_answers(self.db, assessment_id)}
        answers = [{
            "question_id": question.id,
            "type": question.type,
            "content": question.content,
            "answer": stored[question.id].answer if question.id in stored else None,
            "standard_answer": question.answer,
            "score": float(question.score),
            "dimension": question.dimension or "未分类",
        } for question in questions]
        talent = await self.repo.get_talent_keywords(self.db, assessment.talent_id)
        interview = await self._interview_evidence(assessment_id)
        return {
            "assessment_id": assessment.id,
            "talent_id": assessment.talent_id,
            "position_id": assessment.position_id,
            "job_level": assessment.position.level if assessment.position else "中级",
            "profession": assessment.position.title if assessment.position else "人才测评",
            "answers": answers,
            "education": talent.get("education") or None,
            "work_years": talent.get("years"),
            "interview_score": interview.get("interview_score"),
            "interview_records": interview.get("records") or [],
            "dimension_rule_scores": await self._written_dimension_scores(assessment_id),
        }

    async def save_ai_evaluation(self, assessment_id: int, result: dict[str, Any]) -> None:
        """评分结果与状态在同一个保存点内写入，失败不返回伪持久化成功。"""
        report = result.get("report") or {}
        try:
            async with self.db.begin_nested():
                assessment = await self.repo.get_by_id_or_404(self.db, assessment_id, lock=True)
                if assessment.status not in ("submitted", "scored"):
                    raise BusinessRuleError("测评状态已变化，不能写入评分结果")
                await self.repo.save_result(
                    self.db, assessment_id=assessment_id,
                    total_score=result.get("total_score"),
                    dimension_scores=result.get("dimension_scores"),
                    level=result.get("level"), report=report,
                    suggestions=result.get("suggestions"),
                    model_name=result.get("model_name"),
                    prompt_version=result.get("prompt_version"),
                )
                await self.repo.update_status(
                    self.db, assessment_id, "scored", total_score=result.get("total_score"),
                )
        except Exception as exc:
            if isinstance(exc, BusinessRuleError):
                raise
            raise BusinessRuleError("测评结果保存失败，请稍后重试") from exc

    # ================================================================ 笔试：结果查询
    async def get_result(self, assessment_id: int) -> dict[str, Any]:
        result = await self.repo.get_result(self.db, assessment_id)
        if result is None:
            raise NotFoundError("测评结果尚未生成")
        return result.to_dict()

    async def stats_by_position(self, position_id: int) -> dict[str, Any]:
        return await self.repo.stats_avg_score(self.db, position_id)

    # ================================================================ 面试记录表：拍照上传
    async def save_uploaded_record(
        self,
        *,
        assessment_id: int,
        image_bytes: bytes = b"",
        image_filename: str = "",
        fallback_text: str = "",
        session_id: int | None = None,
    ) -> dict[str, Any]:
        """面试官拍照上传记录表完整流程：校验 -> 存图 -> AI 解析 -> 落库 -> 向量化。

        解析字段：面试得分 / 岗位 / 综合评价 / 亮点 / 不足。
        :return: {record_id, parse_status, position, interview_score,
                  comprehensive_evaluation, highlights, weaknesses,
                  vector_id, vector_warning}
        """
        assessment = await self.repo.get_by_id_or_404(self.db, assessment_id, message="测评不存在")

        # 1) 校验并保存原图存档
        image_path, mime = None, "image/jpeg"
        if image_filename:
            if not image_bytes:
                raise BusinessRuleError("图片文件内容为空，请重新上传")
            ext = os.path.splitext(image_filename)[1].lower()
            if ext not in _ALLOWED_IMAGE_EXT:
                raise BusinessRuleError("仅支持 jpg/png/webp 图片")
            mime = "image/png" if ext == ".png" else "image/jpeg"
            if len(image_bytes) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
                raise BusinessRuleError(f"图片不能超过 {settings.MAX_UPLOAD_SIZE_MB}MB")
            await asyncio.to_thread(os.makedirs, settings.UPLOAD_DIR, exist_ok=True)
            image_path = os.path.join(
                settings.UPLOAD_DIR,
                f"interview_record_{assessment_id}_{int(time.time())}{ext}")
            await asyncio.to_thread(Path(image_path).write_bytes, image_bytes)

        if not image_bytes and not fallback_text:
            raise BusinessRuleError("请上传面试记录表照片或提供记录文本")
        if session_id is not None:
            await self.repo.require_interview_session(self.db, session_id, assessment_id)

        # 2) AI 解析（视觉模型 -> 兜底文本）
        parsed = await parse_record_image(image_bytes, mime, fallback_text)
        if parsed.get("parse_status") == "failed":
            raise BusinessRuleError("记录表解析失败，请重拍或手工填写记录文本后重试")

        # 3) 落 MySQL 存档
        row = InterviewRecordSheet(
            assessment_id=assessment_id,
            session_id=session_id,
            talent_id=assessment.talent_id,
            position=parsed.get("position"),
            image_path=image_path,
            interview_score=parsed.get("interview_score"),
            comprehensive_evaluation=parsed.get("comprehensive_evaluation"),
            highlights=parsed.get("highlights") or [],
            weaknesses=parsed.get("weaknesses") or [],
            raw_text=parsed.get("raw_text"),
            parse_status=parsed.get("parse_status", "parsed"),
        )
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)

        # 4) 向量化写入向量库（失败不阻断上传，返回 warning 供前端提示）
        vector_id, vector_warning = None, None
        try:
            vector_id = await self._upsert_interview_vector(
                record_id=row.id,
                talent_id=assessment.talent_id,
                assessment_id=assessment_id,
                parsed=parsed,
            )
            row.vector_id = vector_id
            await self.db.flush()
        except Exception as exc:  # noqa: BLE001 - 向量能力降级不阻断业务
            vector_warning = str(exc)[:200]
            logger.warning("面试记录向量化失败（不影响上传）：%s", exc)

        logger.info("面试记录表已存档 record_id=%s parse_status=%s",
                    row.id, parsed.get("parse_status"))
        return {
            "record_id": row.id,
            "parse_status": parsed.get("parse_status"),
            "position": parsed.get("position"),
            "interview_score": parsed.get("interview_score"),
            "comprehensive_evaluation": parsed.get("comprehensive_evaluation"),
            "highlights": parsed.get("highlights") or [],
            "weaknesses": parsed.get("weaknesses") or [],
            "vector_id": vector_id,
            "vector_warning": vector_warning,
        }

    async def list_interview_records(self, assessment_id: int, page: Any) -> Any:
        """某测评的面试记录表分页（走 Repository 白名单）。"""
        return await self.record_repo.paginate(
            self.db, page, filters={"assessment_id": assessment_id})

    # ================================================================ 综合报告（代码直出，不调 Dify）
    async def generate_report(self, assessment_id: int) -> dict[str, Any]:
        """汇总笔试 + 面试记录（向量库召回）+ 简历，生成五维量化报告。"""
        assessment = await self.repo.get_by_id_or_404(self.db, assessment_id, message="测评不存在")

        if assessment.status != "scored" or assessment.total_score is None:
            raise BusinessRuleError("请先完成笔试评分，再生成综合报告")
        written_score = float(assessment.total_score)
        written_dims = await self._written_dimension_scores(assessment_id)
        interview = await self._interview_evidence(assessment_id)
        resume = await self._resume_keywords(assessment.talent_id)

        radar = self._compute_radar(written_score, written_dims, interview, resume)
        available = [radar[key] for key in RADAR_DIMS if radar[key] is not None]
        if not available:
            raise BusinessRuleError("没有可用于综合评价的真实评分证据")
        total = round(sum(available) / len(available), 1)
        level = self._level(total)

        report = {
            "talent_id": assessment.talent_id,
            "position_id": assessment.position_id,
            "written_score": written_score,
            "interview_score": interview["interview_score"],
            "radar": radar,
            "radar_labels": RADAR_LABELS,
            "total_score": total,
            "level": level,
            "conclusion": self._conclusion(total, level, radar, interview),
            "strengths": self._strengths(radar, interview),
            "weaknesses": self._weaknesses(radar),
            "suggestions": self._suggestions(radar, interview),
            "evidence": {
                "written_dimension_scores": written_dims,
                "interview_records": interview["records"][:3],
                "qa_count": interview["qa_count"],
                "resume_keywords": resume,
            },
            "generator": f"builtin-rules/{settings.APP_VERSION}",  # 代码直生成，可追溯
        }
        # 可选：LLM 润色结论文案（失败不影响结构化结果）
        return await self._maybe_polish(report)

    # ------------------------------------------------ 报告：数据采集（查询全走 Repository）
    async def _written_dimension_scores(self, assessment_id: int) -> dict[str, float]:
        """笔试各维度得分率 -> 0-100。"""
        questions = {q.id: q for q in await self.repo.list_questions(self.db, assessment_id)}
        answers = await self.repo.list_answers(self.db, assessment_id)
        got: dict[str, float] = {}
        full: dict[str, float] = {}
        for a in answers:
            q = questions.get(a.question_id)
            if q is None or a.score is None:
                continue
            dim = q.dimension or "未分类"
            got[dim] = got.get(dim, 0.0) + float(a.score)
            full[dim] = full.get(dim, 0.0) + float(q.score or 0)
        return {d: round(got[d] / full[d] * 100, 1) for d in got if full.get(d)}

    async def _interview_evidence(self, assessment_id: int) -> dict[str, Any]:
        """面试证据：MySQL 记录表 + 向量库语义召回 + 会话问答统计。"""
        records = await self.repo.list_interview_records(self.db, assessment_id)

        # 向量库语义召回（作为报告分析上下文，失败降级不影响报告）
        vector_hits: list[dict[str, Any]] = []
        if records:
            query = " ".join(filter(None, [r.comprehensive_evaluation or "" for r in records[:1]])) \
                or "面试综合评价"
            try:
                vec = await embed_query(query)
                store = get_vector_store()
                await store.create_collection(INTERVIEW_RECORD_COLLECTION, len(vec))
                talent_id = records[0].talent_id
                expr = (f"talent_id == {int(talent_id)} and assessment_id == {int(assessment_id)}"
                        if talent_id else f"assessment_id == {int(assessment_id)}")
                vector_hits = await store.search(INTERVIEW_RECORD_COLLECTION, vec, top_k=3, expr=expr)
            except Exception as exc:  # noqa: BLE001
                logger.warning("向量召回面试记录失败（不影响报告生成）：%s", exc)

        scores = [float(r.interview_score) for r in records if r.interview_score is not None]
        qa_rows = await self.repo.list_session_answers(self.db, assessment_id)
        qa_scores = [float(a.score) for a in qa_rows if a.score is not None]

        return {
            "interview_score": round(sum(scores) / len(scores), 1) if scores else None,
            "records": [{
                "position": r.position,
                "score": float(r.interview_score or 0),
                "evaluation": r.comprehensive_evaluation,
                "highlights": r.highlights or [],
                "weaknesses": r.weaknesses or [],
                "parse_status": r.parse_status,
            } for r in records],
            "vector_hits": vector_hits,
            "qa_count": len(qa_rows),
            "qa_avg": round(sum(qa_scores) / len(qa_scores), 1) if qa_scores else None,
        }

    async def _resume_keywords(self, talent_id: int | None) -> dict[str, Any]:
        """从人才档案取简历关键字（失败返回空，不阻断报告）。"""
        if not talent_id:
            return {}
        try:
            return await self.repo.get_talent_keywords(self.db, talent_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取简历关键字失败：%s", exc)
            return {}

    # ------------------------------------------------ 报告：五维评分（口径可解释）
    def _compute_radar(self, written: float, written_dims: dict[str, float],
                       interview: dict[str, Any], resume: dict[str, Any]) -> dict[str, float | None]:
        """只使用真实证据；缺少学历时返回 None，不默认本科或虚构面试表现。"""
        professional_src = [value for dimension, value in written_dims.items()
                            if any(key in dimension for key in ("专业", "基础", "架构"))]
        professional = sum(professional_src) / len(professional_src) if professional_src else written
        interview_score = interview.get("interview_score")
        qa = interview.get("qa_avg")
        parts = [value for value in (interview_score, qa) if value is not None]
        comprehensive = sum(parts) / len(parts) if parts else written
        position_fit = round(interview_score * 0.6 + written * 0.4, 1) if interview_score is not None else written
        education_name = str(resume.get("education") or "")
        tier = next((tier for tier, keyword in ((5, "博士"), (4, "硕士"), (4, "研究生"),
                     (3, "本科"), (2, "大专"), (2, "专科"), (1, "高中")) if keyword in education_name), None)
        education = _EDU_SCORE.get(tier)
        potential = None
        if education is not None:
            years = float(resume.get("years") or 0)
            skill_breadth = min(len(resume.get("skills") or []) * 8, 30)
            potential = min(education * 0.3 + comprehensive * 0.4 + written * 0.2
                            + skill_breadth * 0.3 + (2 if years >= 3 else 0), 100)
        values = {"comprehensive": comprehensive, "professional": professional,
                  "position_fit": position_fit, "education": education, "potential": potential}
        return {key: round(max(0.0, min(float(value), 100.0)), 1) if value is not None else None
                for key, value in values.items()}

    @staticmethod
    def _level(total: float) -> str:
        if total >= 90:
            return "专家"
        if total >= 75:
            return "高级"
        if total >= 60:
            return "中级"
        return "初级"

    def _conclusion(self, total: float, level: str, radar: dict[str, float],
                    interview: dict[str, Any]) -> str:
        best = max((key for key in radar if radar[key] is not None), key=lambda key: radar[key])
        worst = min((key for key in radar if radar[key] is not None), key=lambda key: radar[key])
        n_records = len(interview.get("records") or [])
        return (
            f"综合得分 {total}（{level}）。五维中「{RADAR_LABELS[best]}」表现最突出"
            f"（{radar[best]}分），「{RADAR_LABELS[worst]}」相对薄弱（{radar[worst]}分）。"
            + (f"本次共纳入 {n_records} 份面试记录表" +
               (f"，面试综合 {interview['interview_score']} 分" if interview.get("interview_score") else "")
               + "。" if n_records else "暂无面试记录表，结论主要基于笔试与简历。")
        )

    @staticmethod
    def _strengths(radar: dict[str, float], interview: dict[str, Any]) -> list[str]:
        strengths = [f"{RADAR_LABELS[d]}：{v} 分" for d, v in
                     sorted(((key, value) for key, value in radar.items() if value is not None), key=lambda item: item[1], reverse=True)[:2]]
        for rec in interview.get("records") or []:
            strengths.extend(f"面试亮点：{h}" for h in (rec.get("highlights") or [])[:3])
        return strengths[:6]

    @staticmethod
    def _weaknesses(radar: dict[str, float]) -> list[dict[str, Any]]:
        out = []
        for d, v in sorted(((key, value) for key, value in radar.items() if value is not None), key=lambda item: item[1])[:2]:
            if v < 80:
                out.append({"dimension": RADAR_LABELS[d], "score": v,
                            "gap": f"低于优秀线 80 分 {round(80 - v, 1)} 分"})
        return out

    @staticmethod
    def _suggestions(radar: dict[str, float], interview: dict[str, Any]) -> list[str]:
        tips = {
            "comprehensive": "加强结构化表达与压力场景演练，提升面试综合表现",
            "professional": "系统学习岗位核心技术栈，补齐专业深度（建议 1-2 个实战项目落地）",
            "position_fit": "针对性了解目标岗位业务域，强化岗位关键能力对标",
            "education": "可通过行业认证 / 在职学历提升补强背景项",
            "potential": "建立个人成长档案，定期复盘并跟踪学习曲线",
        }
        out = [tips[d] for d, v in sorted(((key, value) for key, value in radar.items() if value is not None), key=lambda item: item[1])[:2] if v < 85]
        for rec in interview.get("records") or []:
            out.extend(f"针对「{w}」制定专项提升计划" for w in (rec.get("weaknesses") or [])[:2])
        if not out:
            out.append("整体表现优秀，建议进入下一轮/发放 Offer，并规划高阶培养路径")
        return out[:6]

    @staticmethod
    async def _maybe_polish(report: dict[str, Any]) -> dict[str, Any]:
        """LLM 可用时润色 conclusion（失败静默返回规则版）。"""
        from app.adapters.llm.provider_config import provider_configured
        if not provider_configured(settings.LLM_PROVIDER):
            return report
        try:
            from app.ai.core.llm_manager import llm_manager
            result = await llm_manager.chat(
                [{"role": "system", "content": "仅根据给定真实评分，用3至4句中文改写结论；不得增加数字或事实。"},
                 {"role": "user", "content": str({key: report[key] for key in ('radar', 'total_score', 'level', 'written_score', 'interview_score')})}],
                purpose="report_polish", temperature=0.3, max_tokens=300,
            )
            text = (result.content or "").strip()
            if text:
                report["conclusion"] = text
                report["generator"] = f"llm-polish/{result.model}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("报告 LLM 润色失败，保留规则版：%s", exc)
        return report

    # ------------------------------------------------ 面试记录表：向量写入
    @staticmethod
    def _build_record_doc(parsed: dict[str, Any]) -> str:
        """记录表关键字段拼成向量化文本。"""
        return " ".join(filter(None, [
            f"岗位：{parsed.get('position') or ''}",
            f"面试得分：{parsed.get('interview_score') or ''}",
            f"综合评价：{parsed.get('comprehensive_evaluation') or ''}",
            "亮点：" + "；".join(parsed.get("highlights") or []),
            "不足：" + "；".join(parsed.get("weaknesses") or []),
        ])) or "面试记录"

    async def _upsert_interview_vector(self, *, record_id: int, talent_id: int | None,
                                       assessment_id: int, parsed: dict[str, Any]) -> str:
        """写入向量库集合 interview_record_vectors，返回向量主键。"""
        doc = self._build_record_doc(parsed)
        vector = await embed_query(doc)
        store = get_vector_store()
        await store.create_collection(INTERVIEW_RECORD_COLLECTION, len(vector))
        vid = new_vector_id()
        await store.upsert(
            INTERVIEW_RECORD_COLLECTION, [vid], [vector],
            metadata=[{
                "assessment_id": assessment_id,
                "talent_id": talent_id,
                "record_id": record_id,
                "position": parsed.get("position") or "",
                "interview_score": float(parsed.get("interview_score") or 0),
            }],
            docs=[doc],
        )
        return vid

    # ================================================================ 内部工具
    @staticmethod
    def _guard_transition(current: str, target: str) -> None:
        allowed = _ALLOWED_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise BusinessRuleError(f"测评状态不允许 {current} -> {target}")

    @staticmethod
    def _normalize_choice(answer: Any) -> str:
        """把答案统一成可比较字符串（支持 "A"/["A"]/{"key":"A"} 等形态）。"""
        if answer is None:
            return ""
        if isinstance(answer, str):
            return answer.strip().upper()
        if isinstance(answer, (list, tuple)):
            return ",".join(sorted(str(a).strip().upper() for a in answer))
        if isinstance(answer, dict):
            return str(answer.get("key", "")).strip().upper()
        return str(answer).strip().upper()


__all__ = ["AssessmentService", "RADAR_DIMS", "RADAR_LABELS"]
