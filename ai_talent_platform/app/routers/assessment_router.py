"""
标签：[Agent组] [P0]
模块：app/routers/assessment_router.py
职责：人才测评接口层（简历驱动 -> RAG组卷 -> 短信 -> 笔试 -> 面试 -> 代码直出综合报告）。

接口（统一前缀 /api/v1）：
    POST /assessments                       创建测评：简历RAG组卷 + 短信推送
    GET  /assessments                       测评分页列表
    GET  /assessments/{id}/paper            取笔试试卷（不含答案）
    POST /assessments/{id}/submit           提交笔试答案；达标自动发面试邀约
    POST /assessments/{id}/interview/record 面试官拍照上传面试记录表（AI解析 -> MySQL + 向量库）
    GET  /assessments/{id}/interview/records 面试记录表列表
    POST /assessments/{id}/interview/finish 结束面试 -> 代码直出个性化五维报告（不调 Dify）
    GET  /assessments/{id}/report           查询最终个性化报告
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.core.dependencies import DbSession, require_permission
from app.core.pagination import PageParams
from app.core.permissions import PermissionEnum
from app.core.response import PageResult, success
from app.infrastructure.notify.email_client import send_email
from app.models.user import User
from app.schemas.assessment import AnswerSubmitRequest, AssessmentCreateRequest
from app.schemas.common import COMMON_ERROR_RESPONSES, SuccessResponse
from app.services.assessment_service import AssessmentService
from app.utils.exam_token import build_exam_url

router = APIRouter(
    prefix="/assessments",
    tags=["人才测评"],
    responses=COMMON_ERROR_RESPONSES,
)

P = PermissionEnum


def _svc(db: AsyncSession) -> AssessmentService:
    return AssessmentService(db)


def _exam_base_url(request: Request) -> tuple[str, bool]:
    """解析邮件里笔试入口链接的基址。

    优先级：FRONTEND_URL（前端考试页） > PUBLIC_BASE_URL（后端内置考试页的公网地址）
           > request.base_url（仅调试兜底）。
    返回 (base_url, is_fallback)：is_fallback=True 表示用到了请求 Host 兜底，
    生产环境该地址通常点不开，需要在响应里显式提示配置 PUBLIC_BASE_URL。
    """
    if settings.FRONTEND_URL:
        return settings.FRONTEND_URL, False
    if settings.PUBLIC_BASE_URL:
        return settings.PUBLIC_BASE_URL, False
    return str(request.base_url), True


# 笔试达标线（百分制），达到后自动发面试邀约
INTERVIEW_THRESHOLD = 60.0


# ------------------------------------------------------------------ 1. 创建测评（仅传 file_id，自动解析简历 -> 组卷 -> 邮件推送）
@router.post("", summary="创建测评：仅需 file_id，自动解析简历并按邮箱推送笔试",
             response_model=SuccessResponse[dict])
async def create_assessment(body: AssessmentCreateRequest, request: Request, db: DbSession,
                            user: User = Depends(require_permission(P.ASSESSMENT_MANAGE))):
    service = _svc(db)

    # 1) 读简历原件 -> 解析关键字（应聘岗位/等级/学历/工作年限/邮箱/技能）-> 按难度组卷 -> 自动建档
    result = await service.create_assessment_from_file(
        file_id=body.file_id, title=body.title,
        question_count=body.question_count, user=user)
    assessment = result["assessment"]
    kw = result["keywords"]
    difficulty = result["difficulty"]
    questions = result["questions"]
    parse_mode = result["parse_mode"]

    # 2) 用解析出的邮箱推送笔试（链接恒不为空：前端站点 > 后端公网地址 > 请求 Host 兜底）
    base_url, is_fallback = _exam_base_url(request)
    exam_url = build_exam_url(base_url, assessment.id)
    candidate_email = (kw.get("email") or "").strip()
    email_result = await send_email(
        candidate_email, template="assessment_invite",
        params={"name": kw.get("name") or "候选人",
                "paper": f"{difficulty}{kw.get('position_title') or '岗位'}",
                "url": exam_url})
    data = {
        "id": assessment.id,
        "talent_id": assessment.talent_id,
        "difficulty": difficulty,
        "parse_mode": parse_mode,          # agent=大模型解析成功 / rule=已降级本地正则
        "resume_keywords": kw,
        "question_count": len(questions),
        "exam_url": exam_url,
        "email": {**email_result, "to": candidate_email},
    }
    if is_fallback:
        data["exam_url_warning"] = (
            "当前未配置 FRONTEND_URL / PUBLIC_BASE_URL，笔试链接基于请求 Host 生成，"
            "候选人可能无法从外网打开。请在 .env 配置 PUBLIC_BASE_URL（后端公网地址）。"
        )
    return success(data=data, message="试卷已生成" if email_result.get("ok") else "试卷已生成，邮件未发送")


# ------------------------------------------------------------------ 2. 列表
@router.get("", summary="测评分页列表", response_model=SuccessResponse[PageResult],
            dependencies=[Depends(require_permission(P.ASSESSMENT_READ))])
async def list_assessments(page: PageParams = Depends(), talent_id: int | None = None,
                           status: str | None = None, db: DbSession = None):
    service = _svc(db)
    filters = {k: v for k, v in {"talent_id": talent_id, "status": status}.items() if v is not None}
    result = await service.repo.paginate(service.db, page, filters=filters)
    # ORM 对象无法被 Pydantic 直接序列化，转成可序列化的 dict 再返回
    result.items = [{
        "id": a.id,
        "talent_id": a.talent_id,
        "position_id": a.position_id,
        "title": a.title,
        "status": a.status,
        "question_count": a.question_count,
        "total_score": float(a.total_score) if a.total_score is not None else None,
        "deadline": a.deadline.isoformat() if a.deadline else None,
        "created_by_ai": a.created_by_ai,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    } for a in result.items]
    return success(data=result)


# ------------------------------------------------------------------ 2.1 测评详情
@router.get("/{assessment_id}", summary="测评详情（含简历关键字与考试链接）",
            response_model=SuccessResponse[dict],
            dependencies=[Depends(require_permission(P.ASSESSMENT_READ))])
async def get_assessment(assessment_id: int, request: Request, db: DbSession = None):
    service = _svc(db)
    a = await service.repo.get_by_id_or_404(db, assessment_id, message="测评不存在")
    talent = a.talent
    # 简历关键字（创建测评时解析后自动建档，供 HR 回看）
    resume_keywords = {}
    if talent is not None:
        resume_keywords = {
            "name": getattr(talent, "name", "") or "",
            "email": getattr(talent, "email", "") or "",
            "education": getattr(talent, "education", "") or "",
            "work_years": int(getattr(talent, "work_years", 0) or 0),
            "current_position": getattr(talent, "current_position", "") or "",
            "skills": list(getattr(talent, "skill_tags", None) or []),
        }
    base_url, is_fallback = _exam_base_url(request)
    data = {
        "id": a.id,
        "talent_id": a.talent_id,
        "position_id": a.position_id,
        "title": a.title,
        "status": a.status,
        "question_count": a.question_count,
        "total_score": float(a.total_score) if a.total_score is not None else None,
        "deadline": a.deadline.isoformat() if a.deadline else None,
        "started_at": a.started_at.isoformat() if a.started_at else None,
        "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None,
        "created_by_ai": a.created_by_ai,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "resume_keywords": resume_keywords,
        "exam_url": build_exam_url(base_url, a.id),
    }
    if is_fallback:
        data["exam_url_warning"] = (
            "当前未配置 FRONTEND_URL / PUBLIC_BASE_URL，考试链接基于请求 Host 生成，"
            "候选人可能无法从外网打开。请在 .env 配置 PUBLIC_BASE_URL（后端公网地址）。"
        )
    return success(data=data)


# ------------------------------------------------------------------ 3. 取试卷
@router.get("/{assessment_id}/paper", summary="取笔试试卷（不含答案）",
            response_model=SuccessResponse[dict],
            dependencies=[Depends(require_permission(P.ASSESSMENT_READ))])
async def get_paper(assessment_id: int, db: DbSession = None):
    service = _svc(db)
    questions = await service.repo.list_questions(db, assessment_id)
    return success(data={
        "assessment_id": assessment_id,
        "questions": [{"question_id": q.id, "type": q.type, "content": q.content,
                       "options": q.options, "score": float(q.score or 0),
                       "dimension": q.dimension} for q in questions],
    })


# ------------------------------------------------------------------ 4. 开始测评（pending -> in_progress）
@router.post("/{assessment_id}/start", summary="开始测评：状态置为作答中并下发试卷",
             response_model=SuccessResponse[dict],
             dependencies=[Depends(require_permission(P.ASSESSMENT_MANAGE))])
async def start(assessment_id: int, db: DbSession = None):
    data = await _svc(db).start_assessment(assessment_id)
    return success(data=data, message="测评已开始")


# ------------------------------------------------------------------ 5. 提交笔试（达标自动邀约）
@router.post("/{assessment_id}/submit", summary="提交笔试答案；达标自动发面试邀约",
             response_model=SuccessResponse[dict],
             dependencies=[Depends(require_permission(P.ASSESSMENT_MANAGE))])
async def submit(assessment_id: int, body: AnswerSubmitRequest, db: DbSession = None):
    service = _svc(db)
    answers = [{"question_id": a.question_id, "answer": a.answer} for a in body.answers]
    result = await service.submit_answers(assessment_id, answers)
    rule_total = result.get("rule_total_score", 0.0)

    # 达标 -> 自动发面试邀约邮件
    invited = False
    if rule_total >= INTERVIEW_THRESHOLD:
        assessment = await service.repo.get_by_id_or_404(db, assessment_id)
        talent_email = getattr(assessment.talent, "email", "") if assessment.talent else ""
        email_result = await send_email(talent_email or "", template="interview_invite",
                                         params={"name": assessment.talent.name})
        invited = bool(email_result.get("ok"))
    return success(data={**result, "interview_invited": invited,
                         "threshold": INTERVIEW_THRESHOLD},
                   message="笔试已提交")


# ------------------------------------------------------------------ 5. 拍照上传面试记录表
@router.post("/{assessment_id}/interview/record",
             summary="面试官拍照上传面试记录表（AI解析 -> MySQL + 向量库）",
             response_model=SuccessResponse[dict],
             dependencies=[Depends(require_permission(P.ASSESSMENT_MANAGE))])
async def upload_interview_record(
    assessment_id: int,
    image: UploadFile | None = File(None, description="面试记录表照片（jpg/png/webp）"),
    fallback_text: str = Form("", description="兜底文本（视觉模型不可用时用于正则解析）"),
    session_id: int | None = Form(None, description="面试会话 ID（可空）"),
    db: DbSession = None,
):
    image_bytes = await image.read() if (image is not None and image.filename) else b""
    result = await _svc(db).save_uploaded_record(
        assessment_id=assessment_id,
        image_bytes=image_bytes,
        image_filename=image.filename if image else "",
        fallback_text=fallback_text,
        session_id=session_id,
    )
    return success(data=result, message="面试记录表已解析并存档")


# ------------------------------------------------------------------ 7.1 面试记录表列表
@router.get("/{assessment_id}/interview/records", summary="面试记录表列表",
            response_model=SuccessResponse[PageResult],
            dependencies=[Depends(require_permission(P.ASSESSMENT_READ))])
async def list_interview_records(assessment_id: int, page: PageParams = Depends(), db: DbSession = None):
    result = await _svc(db).list_interview_records(assessment_id, page)
    result.items = [{
        "id": r.id,
        "assessment_id": r.assessment_id,
        "session_id": r.session_id,
        "talent_id": r.talent_id,
        "position": r.position,
        "interview_score": float(r.interview_score) if r.interview_score is not None else None,
        "comprehensive_evaluation": r.comprehensive_evaluation,
        "highlights": r.highlights or [],
        "weaknesses": r.weaknesses or [],
        "parse_status": r.parse_status,
        "vector_id": r.vector_id,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in result.items]
    return success(data=result)


# ------------------------------------------------------------------ 8. 结束面试 -> 代码直出综合报告
@router.post("/{assessment_id}/interview/finish",
             summary="结束面试 -> 代码直出个性化五维报告（结合笔试+面试记录+简历，不调 Dify）",
             response_model=SuccessResponse[dict],
             dependencies=[Depends(require_permission(P.ASSESSMENT_MANAGE))])
async def interview_finish(assessment_id: int, session_id: int | None = None,
                           interview_score: float | None = None, db: DbSession = None):
    service = _svc(db)
    assessment = await service.repo.get_by_id_or_404(db, assessment_id)

    # 可选：结束会话并回填面试总分（记录表已携带得分时可不传）
    if session_id:
        await service.repo.finish_interview_session(
            db, session_id, interview_score or 0.0, summary={}, assessment_id=assessment_id)

    # 生成综合报告（笔试 + 向量库面试记录 + 简历 -> 五维量化）
    report = await service.generate_report(assessment_id)

    # 落库
    await service.repo.save_result(
        db, assessment_id=assessment_id,
        total_score=report.get("total_score"),
        dimension_scores=report.get("radar"),
        level=report.get("level"),
        report=report,
        suggestions=report.get("suggestions"),
    )
    await service.repo.update_status(db, assessment_id, "scored",
                                     total_score=report.get("total_score"))
    return success(data=report, message="综合报告已生成")


# ------------------------------------------------------------------ 9. 查报告
@router.get("/{assessment_id}/report", summary="查询最终个性化报告",
            response_model=SuccessResponse[dict],
            dependencies=[Depends(require_permission(P.ASSESSMENT_READ))])
async def get_report(assessment_id: int, db: DbSession = None):
    service = _svc(db)
    return success(data=await service.get_result(assessment_id))


__all__ = ["router"]
