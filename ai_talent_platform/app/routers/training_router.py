"""
模块：app/routers/training_router.py
职责：培训管理接口层（智能培训赋能 · 需求 2.4）。

标签：[Dify组] [P1]（培训 REST 接口，11 条路径 / 13 个方法）

============================ 实现思路与代码要点 ============================
统一写法：
    router = APIRouter(prefix="/trainings", tags=["培训管理"], responses=COMMON_ERROR_RESPONSES)

    def get_service(db: AsyncSession = Depends(get_db)) -> TrainingService:
        return TrainingService(db)

    每个写接口都挂权限依赖：dependencies=[Depends(require_permission(PermissionEnum.XXX))]

接口清单（11 条路径 / 13 个方法）：
    POST   /trainings/plans/generate        生成方案（节点 1~4 主入口）  manage
    GET    /trainings/plans                 计划列表（含完成率）          read
    GET    /trainings/plans/{id}            计划详情（课表 + 方案说明）    read
    GET    /trainings/plans/{id}/progress   进度统计（节点 5）            read
    POST   /trainings/plans/{id}/score      提交考核（节点 6）            manage
    GET    /trainings/stats/pass-rate       考核通过率统计                read
    POST   /trainings/records               写回学习进度                  read
    POST   /trainings/lag-scan              滞后扫描（定时任务入口）      manage
    GET    /trainings/courses               课程列表                      read
    POST   /trainings/courses               新建课程                      manage
    PUT    /trainings/courses/{id}          更新课程                      manage
    DELETE /trainings/courses/{id}          下架课程（软删）              manage
    POST   /trainings/recommend             按短板推荐课程                read

职责边界：
- Router 不写 SQL、不写业务规则；参数校验交给 Schema，业务校验交给 Service；
- 列表统一分页（PageParams + build_page_result）；
- 长耗时任务（本模块的 AI 排期）由 Service 内部做超时与回退，Router 不感知模型存在。
=========================================================================
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser, get_db, require_permission
from app.core.exceptions import ParamError, PermissionDeniedError
from app.core.pagination import PageParams, build_page_result
from app.core.permissions import PermissionEnum, has_any_permission
from app.core.response import PageResult, success
from app.schemas.common import COMMON_ERROR_RESPONSES, SuccessResponse
from app.schemas.training import (
    CourseCreate,
    CourseResponse,
    CourseUpdate,
    LagScanResult,
    LearningRecordUpdate,
    NotificationBrief,
    PassRateStatsResponse,
    ProgressResponse,
    ScoreSubmitRequest,
    ScoreSubmitResponse,
    TrainingNotificationResponse,
    TrainingPlanBrief,
    TrainingPlanGenerateRequest,
    TrainingPlanResponse,
    TrainingRecommendRequest,
    TrainingRecommendResult,
)
from app.services.notification_service import TrainingNotificationService
from app.services.training_service import TrainingService

router = APIRouter(
    prefix="/trainings",
    tags=["培训管理"],
    responses=COMMON_ERROR_RESPONSES,
)


def get_service(db: AsyncSession = Depends(get_db)) -> TrainingService:
    """TrainingService 依赖。"""
    return TrainingService(db)


def get_notification_service(db: AsyncSession = Depends(get_db)) -> TrainingNotificationService:
    """站内信服务依赖。"""
    return TrainingNotificationService(db)


def _is_manager(user: object) -> bool:
    """是否有「代他人操作」的权限（training:manage 或超管）。"""
    if getattr(user, "is_superuser", False):
        return True
    roles = getattr(user, "role_codes", None) or []
    return has_any_permission(list(roles), [PermissionEnum.TRAINING_MANAGE])


def _ensure_self_scope(user: object, talent_id: int) -> None:
    """自助上报的归属校验。

    只有 training:manage（HR / 管理员）可以替别人操作；其余角色持
    training:report 权限，只能操作 `users.talent_id` 绑定的本人。
    没有绑定就直接拒绝，而不是放行——否则「自助上报」等于「任意人能改任意人」。
    """
    if _is_manager(user):
        return
    own = getattr(user, "talent_id", None)
    if own is None or int(own) != int(talent_id):
        raise PermissionDeniedError(
            "只能操作本人的学习记录：当前账号未绑定人才档案（users.talent_id 为空），"
            "或本次提交的人才不是本人；如需代他人操作，请使用具备 training:manage 的账号"
        )


# ============================================================ 课程库
@router.post(
    "/courses",
    summary="新建课程",
    response_model=SuccessResponse[CourseResponse],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(PermissionEnum.TRAINING_MANAGE))],
)
async def create_course(
    data: CourseCreate,
    user: CurrentUser,
    service: TrainingService = Depends(get_service),
) -> Any:
    """新建课程（同名课程会被拒绝，避免重复入库导致排课混乱）。

    课程 `category` 必须与能力维度名一字不差——排课靠它匹配，
    差一个字不会报错，只会让该维度静默降级为「无课可推」。
    """
    course = await service.create_course(data, operator_id=user.id)
    return success(data=CourseResponse.model_validate(course), message="课程已创建")


@router.get(
    "/courses",
    summary="课程列表",
    response_model=SuccessResponse[PageResult[CourseResponse]],
    dependencies=[Depends(require_permission(PermissionEnum.TRAINING_READ))],
)
async def list_courses(
    page: PageParams = Depends(),
    keyword: str | None = Query(None, max_length=64, description="按名称/简介模糊搜索"),
    category: str | None = Query(None, max_length=32, description="能力维度"),
    level: str | None = Query(None, max_length=20, description="难度层级 L1~L4"),
    is_active: bool | None = Query(None, description="是否上架"),
    service: TrainingService = Depends(get_service),
) -> Any:
    """课程分页列表。"""
    result = await service.page_courses(
        page, keyword=keyword, category=category, level=level, is_active=is_active
    )
    return success(data=build_page_result(result.items, result.total, page))


@router.put(
    "/courses/{course_id}",
    summary="更新课程",
    response_model=SuccessResponse[CourseResponse],
    dependencies=[Depends(require_permission(PermissionEnum.TRAINING_MANAGE))],
)
async def update_course(
    course_id: int,
    data: CourseUpdate,
    user: CurrentUser,
    service: TrainingService = Depends(get_service),
) -> Any:
    """更新课程信息（只改传入的字段，未传的保持原值）。"""
    course = await service.update_course(course_id, data)
    return success(data=CourseResponse.model_validate(course), message="课程已更新")


@router.delete(
    "/courses/{course_id}",
    summary="下架课程（软删）",
    response_model=SuccessResponse[dict],
    dependencies=[Depends(require_permission(PermissionEnum.TRAINING_MANAGE))],
)
async def delete_course(
    course_id: int,
    user: CurrentUser,
    service: TrainingService = Depends(get_service),
) -> Any:
    """下架课程：走软删除，历史计划里的课程名不会因此丢失。"""
    await service.delete_course(course_id)
    return success(data={"id": course_id}, message="课程已下架")


# ============================================================ 计划（节点 1~4）
@router.post(
    "/plans/generate",
    summary="生成培训方案（AI 排期 + 真实课程匹配）",
    response_model=SuccessResponse[TrainingPlanResponse],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(PermissionEnum.TRAINING_MANAGE))],
)
async def generate_plan(
    data: TrainingPlanGenerateRequest,
    user: CurrentUser,
    service: TrainingService = Depends(get_service),
) -> Any:
    """由能力短板生成 4 周计划：诊断 → 排期 → 匹配真实课程 → 落库推送。

    响应里的三个溯源字段用于判断本次结果的来源：
    - `generated_by`：`llm` 表示模型参与排期，`rule` 表示纯规则；
    - `summary_source`：方案说明是模型写的还是模板生成的；
    - `requirement_source`：目标分与权重取自岗位模型 / 调用方 / 默认值。
    """
    result = await service.generate_plan(data, operator_id=user.id)
    return success(data=result, message="培训方案已生成")


@router.get(
    "/plans",
    summary="计划列表（含完成率）",
    response_model=SuccessResponse[PageResult[TrainingPlanBrief]],
    dependencies=[Depends(require_permission(PermissionEnum.TRAINING_READ))],
)
async def list_plans(
    page: PageParams = Depends(),
    talent_id: int | None = Query(None, gt=0, description="按人才筛选"),
    status: str | None = Query(None, max_length=20, description="draft/ongoing/finished"),
    service: TrainingService = Depends(get_service),
) -> Any:
    """计划分页列表（完成率批量聚合，避免 N+1）。"""
    result = await service.page_plans(page, talent_id=talent_id, status=status)
    return success(data=build_page_result(result.items, result.total, page))


@router.get(
    "/plans/{plan_id}",
    summary="计划详情（课表 + 方案说明）",
    response_model=SuccessResponse[TrainingPlanResponse],
    dependencies=[Depends(require_permission(PermissionEnum.TRAINING_READ))],
)
async def get_plan(plan_id: int, service: TrainingService = Depends(get_service)) -> Any:
    """计划详情：含逐项课表、课程来源与方案说明。"""
    return success(data=await service.get_plan(plan_id))


# ============================================================ 节点 5：进度
@router.post(
    "/records",
    summary="写回学习进度",
    response_model=SuccessResponse[dict],
    # 写操作，但不再只认 MANAGE：员工是培训的执行主体，不能连自己的进度都上报不了。
    # REPORT 只放行「本人」，归属校验在 _ensure_self_scope；MANAGE 才可代他人操作。
    dependencies=[
        Depends(require_permission(PermissionEnum.TRAINING_REPORT, PermissionEnum.TRAINING_MANAGE))
    ],
)
async def update_progress(
    data: LearningRecordUpdate,
    user: CurrentUser,
    service: TrainingService = Depends(get_service),
) -> Any:
    """上报学习进度：进度只允许单调递增，学时与进度不符会被标记 suspicious。"""
    _ensure_self_scope(user, data.talent_id)
    result = await service.update_progress(data, operator_id=user.id)
    return success(data=result, message="学习进度已更新")


@router.get(
    "/plans/{plan_id}/progress",
    summary="进度统计（时间进度 vs 完成学时）",
    response_model=SuccessResponse[ProgressResponse],
    dependencies=[Depends(require_permission(PermissionEnum.TRAINING_READ))],
)
async def get_progress(
    plan_id: int, service: TrainingService = Depends(get_service)
) -> Any:
    """按「时间进度 vs 完成学时」判定正常 / 提醒 / HR 预警。

    注意：落后 ≥20% 发 `training.lag.remind`，≥40% 发 `training.lag.alert`。
    """
    return success(data=await service.get_progress(plan_id))


@router.post(
    "/lag-scan",
    summary="滞后扫描（定时任务入口）",
    response_model=SuccessResponse[LagScanResult],
    dependencies=[Depends(require_permission(PermissionEnum.TRAINING_MANAGE))],
)
async def scan_lag(service: TrainingService = Depends(get_service)) -> Any:
    """由定时任务（cron / APScheduler）调用：扫描进行中计划并产出预警。

    独立成接口而不是嵌在业务流程里：可重入、可手工补扫、可单独测试。
    """
    return success(data=await service.scan_lag())


# ============================================================ 节点 6：考核
@router.post(
    "/plans/{plan_id}/score",
    summary="提交结课考核",
    response_model=SuccessResponse[ScoreSubmitResponse],
    # 主管要给下属判考核、员工要给自己登记结课成绩：都给 REPORT，
    # 但非管理者只能提交本人计划的成绩（归属校验在 Service 内按 actor_talent_id 做）。
    dependencies=[
        Depends(require_permission(PermissionEnum.TRAINING_REPORT, PermissionEnum.TRAINING_MANAGE))
    ],
)
async def submit_score(
    plan_id: int,
    data: ScoreSubmitRequest,
    user: CurrentUser,
    service: TrainingService = Depends(get_service),
) -> Any:
    """提交结课考核：达标则发 training.retest.required 事件回流测评 Agent。

    不传 `course_id` 时按「整计划考核」处理，会把成绩写到该计划下每门课——
    否则通过率的分母永远是 0。

    已结课的计划默认拒绝重复打分（防止把已通过的结论覆盖掉），
    确需改判请传 `force=true`。
    """
    payload = data.model_copy(update={"plan_id": plan_id})
    actor_talent_id = None
    if not _is_manager(user):
        # 非管理者：把「本人绑定的人才」传下去，由 Service 与计划所属人才比对
        actor_talent_id = getattr(user, "talent_id", None)
        if actor_talent_id is None:
            raise PermissionDeniedError(
                "只能提交本人培训计划的考核成绩：当前账号未绑定人才档案"
                "（users.talent_id 为空）；如需代他人操作，请使用具备 training:manage 的账号"
            )
    result = await service.submit_score(payload, actor_talent_id=actor_talent_id)
    return success(data=result, message="考核成绩已提交")


@router.get(
    "/stats/pass-rate",
    summary="考核通过率统计（整体 + 按课程）",
    response_model=SuccessResponse[PassRateStatsResponse],
    dependencies=[Depends(require_permission(PermissionEnum.TRAINING_READ))],
)
async def pass_rate(
    start: datetime | None = Query(None, description="统计起始时间（含）"),
    end: datetime | None = Query(None, description="统计结束时间（含）"),
    course_id: int | None = Query(None, gt=0, description="按课程筛选"),
    plan_id: int | None = Query(None, gt=0, description="按计划筛选"),
    service: TrainingService = Depends(get_service),
) -> Any:
    """统计考核通过率（整体 + 按课程拆分）。

    口径：分母只算**已出考核结论**的记录。没考的不进分母，
    否则「还没组织考试」会被算成「不通过」。
    """
    return success(
        data=await service.pass_rate(
            start=start, end=end, course_id=course_id, plan_id=plan_id
        )
    )


# ============================================================ 轻量推荐
@router.post(
    "/recommend",
    summary="按短板推荐课程（不建计划）",
    response_model=SuccessResponse[TrainingRecommendResult],
    dependencies=[Depends(require_permission(PermissionEnum.TRAINING_READ))],
)
async def recommend(
    data: TrainingRecommendRequest,
    service: TrainingService = Depends(get_service),
) -> Any:
    """只推荐课程、不建计划（HR 先看再决定时的轻量接口）。"""
    return success(data=await service.recommend(data))


# ============================================================ 推送落地（站内信）
@router.get(
    "/notifications",
    summary="培训站内信（推送记录，可查未读）",
    response_model=SuccessResponse[NotificationBrief],
    dependencies=[Depends(require_permission(PermissionEnum.TRAINING_READ))],
)
async def list_notifications(
    user: CurrentUser,
    page: PageParams = Depends(),
    talent_id: int | None = Query(None, gt=0, description="按收件人筛选（管理者用）"),
    is_read: bool | None = Query(None, description="按已读状态筛选"),
    event_type: str | None = Query(None, max_length=48, description="按事件类型筛选"),
    service: TrainingNotificationService = Depends(get_notification_service),
) -> Any:
    """查询培训推送记录（计划下发 / 落后提醒 / HR 预警 / 复测要求 / 计划调整）。

    非管理者只能看自己的：不传 talent_id 时自动取本人绑定的人才；
    未绑定人才档案则返回空列表，绝不退回「看全公司」。
    """
    if not _is_manager(user):
        own = getattr(user, "talent_id", None)
        if own is None:
            return success(data=NotificationBrief(total=0, unread=0, items=[]))
        talent_id = int(own)

    result = await service.page(
        page, talent_id=talent_id, is_read=is_read, event_type=event_type
    )
    unread = await service.unread_count(talent_id) if talent_id else 0
    return success(
        data=NotificationBrief(
            total=result.total,
            unread=unread,
            items=[TrainingNotificationResponse.model_validate(item) for item in result.items],
        )
    )


@router.post(
    "/notifications/read-all",
    summary="全部标记已读",
    response_model=SuccessResponse[dict],
    dependencies=[Depends(require_permission(PermissionEnum.TRAINING_READ))],
)
async def read_all_notifications(
    user: CurrentUser,
    talent_id: int | None = Query(None, gt=0, description="管理者可指定收件人"),
    service: TrainingNotificationService = Depends(get_notification_service),
) -> Any:
    """把培训站内信全部标记已读。

    非管理者只能操作本人（未绑定人才档案时无可更新项）；
    管理者不传 talent_id 时作用于其可见的全部站内信 —— 与
    GET /notifications 不传 talent_id 的「管理者看全部」口径一致，
    否则前端「全部已读」按钮对管理员永远报错。
    """
    if not _is_manager(user):
        own = getattr(user, "talent_id", None)
        if own is None:
            return success(data={"updated": 0}, message="当前账号未绑定人才档案，无可更新通知")
        talent_id = int(own)
    updated = await service.mark_all_read(talent_id=talent_id)
    return success(
        data={"updated": updated, **({"talent_id": int(talent_id)} if talent_id else {})},
        message="已全部标记已读",
    )


@router.post(
    "/notifications/{notification_id}/read",
    summary="标记单条站内信已读",
    response_model=SuccessResponse[TrainingNotificationResponse],
    dependencies=[Depends(require_permission(PermissionEnum.TRAINING_READ))],
)
async def read_notification(
    notification_id: int,
    user: CurrentUser,
    service: TrainingNotificationService = Depends(get_notification_service),
) -> Any:
    """标记单条已读；非管理者只能操作自己的站内信。"""
    scope = None if _is_manager(user) else getattr(user, "talent_id", None)
    if scope is None and not _is_manager(user):
        raise PermissionDeniedError("当前账号未绑定人才档案，无法确认站内信归属")
    row = await service.mark_read(notification_id, talent_id=scope)
    return success(data=TrainingNotificationResponse.model_validate(row), message="已标记已读")
