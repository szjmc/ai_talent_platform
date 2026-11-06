"""
模块：app/routers/matching_router.py
职责：人岗匹配接口层（执行匹配 / 结果查询 / 历史 / 解释）。

标签：[Agent组] [P1]（当前 501 占位）

============================ 实现思路与代码要点 ============================
统一写法：
    router = APIRouter(prefix="/matchings", tags=["人岗匹配"], responses=COMMON_ERROR_RESPONSES)

接口清单（本阶段实现）：
    POST   /matchings/run                执行匹配（返回 batch_id + 结果）
    POST   /matchings/reverse            双向匹配（人才找岗位，只读试算不落库）
    POST   /matchings/warnings/scan      触发储备预警扫描（幂等 + 自动闭环）
    GET    /matchings/warnings           预警分页（类型/岗位/解决状态过滤）
    POST   /matchings/warnings/{id}/resolve  人工解决预警
    GET    /matchings                    匹配记录分页（按批次时间倒序）
    GET    /matchings/positions/{id}     某岗位最新匹配结果
    GET    /matchings/talents/{id}       某人才历史匹配记录
    POST   /matchings/explain            指定岗位 × 人才生成匹配解释
    POST   /matchings/candidates/compare 多候选人横向对比（只读快照）
    POST   /matchings/batches/{batch_id}/talents/{talent_id}/review  HR 复核决策
    GET    /matchings/reviews            复核记录分页
    GET    /matchings/reviews/{id}/events 复核动作流水（审计时间线）
    GET    /matchings/positions/{id}/funnel 岗位复核漏斗

实现要点：
- Router 不写 SQL、不写业务规则；参数校验交给 Schema，业务校验交给 Service；
- 每个接口挂权限：读 matching:read，执行 matching:run；
- 解释接口不落库（只读计算），因此用 matching:read；
- 响应统一 {code,message,data}，data 结构见 app/schemas/matching.py。

影响范围：替换原占位接口；router.py 中把 matchings 从占位路由切换为本路由。
=========================================================================
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_permission
from app.core.pagination import PageParams, build_page_result
from app.core.permissions import PermissionEnum
from app.core.response import PageResult, success
from app.models.user import User
from app.schemas.common import COMMON_ERROR_RESPONSES, SuccessResponse
from app.schemas.matching import (
    MatchCompareRequest,
    MatchCompareResponse,
    MatchExplainRequest,
    MatchExplanation,
    MatchHistoryItem,
    MatchReverseRequest,
    MatchReverseResponse,
    MatchRunRequest,
    MatchRunResponse,
)
from app.schemas.matching_review import (
    MatchingReviewDecideResponse,
    MatchingReviewEventItem,
    MatchingReviewItem,
    MatchingReviewUpsertRequest,
    ReviewFunnelResponse,
    ReviewStatusLiteral,
)
from app.schemas.warning import (
    ReserveWarningItem,
    WarningResolveRequest,
    WarningScanRequest,
    WarningScanResponse,
)
from app.services.matching_review_service import MatchingReviewService
from app.services.matching_service import MatchingService
from app.services.reserve_warning_service import ReserveWarningService

router = APIRouter(
    prefix="/matchings",
    tags=["人岗匹配"],
    responses=COMMON_ERROR_RESPONSES,
)


def get_service(db: AsyncSession = Depends(get_db)) -> MatchingService:
    """MatchingService 依赖。"""
    return MatchingService(db)


@router.get(
    "/batches/{batch_id}", response_model=SuccessResponse[MatchRunResponse],
    summary="按批次查询匹配快照（含零结果）",
)
async def get_batch_snapshot(
    batch_id: str, service: MatchingService = Depends(get_service),
    _: User = Depends(require_permission(PermissionEnum.MATCHING_READ)),
):
    return success(data=await service.get_batch_snapshot(batch_id), message="OK")


def get_warning_service(db: AsyncSession = Depends(get_db)) -> ReserveWarningService:
    """ReserveWarningService 依赖。"""
    return ReserveWarningService(db)


# *kwq* 新增：储备预警扫描（需求模块五）。
# 说明：幂等扫描，重复调用不产生重复预警；过期预警自动解决（数据闭环）。
@router.post(
    "/warnings/scan",
    summary="触发储备预警扫描",
    response_model=SuccessResponse[WarningScanResponse],
)
async def scan_warnings(
    payload: WarningScanRequest,
    service: ReserveWarningService = Depends(get_warning_service),
    current_user: User = Depends(require_permission(PermissionEnum.MATCHING_RUN)),
):
    """扫描开放岗位：零产出 / 储备不足 / 长期空缺三类预警，返回闭环计数。"""
    result = await service.scan(payload, current_user_id=current_user.id)
    return success(data=result, message="扫描完成")


# *kwq* 新增：预警分页查询。
@router.get(
    "/warnings",
    summary="储备预警列表",
    response_model=SuccessResponse[PageResult[ReserveWarningItem]],
)
async def list_warnings(
    page: PageParams = Depends(),
    warning_type: str | None = Query(None, max_length=32, description="按预警类型过滤"),
    position_id: int | None = Query(None, ge=1, description="按岗位过滤"),
    resolved: bool | None = Query(None, description="按解决状态过滤"),
    service: ReserveWarningService = Depends(get_warning_service),
    _: User = Depends(require_permission(PermissionEnum.MATCHING_READ)),
):
    """分页查询预警记录（按产生时间倒序）。"""
    result = await service.page_list(
        page, warning_type=warning_type, position_id=position_id, resolved=resolved
    )
    return success(data=result, message="OK")


# *kwq* 新增：人工解决预警。
@router.post(
    "/warnings/{warning_id}/resolve",
    summary="解决预警",
    response_model=SuccessResponse[ReserveWarningItem],
)
async def resolve_warning(
    warning_id: int,
    payload: WarningResolveRequest | None = None,
    service: ReserveWarningService = Depends(get_warning_service),
    current_user: User = Depends(require_permission(PermissionEnum.MATCHING_RUN)),
):
    """标记预警为已解决（记录解决人与备注）。"""
    note = payload.note if payload else None
    result = await service.resolve(
        warning_id, note=note, current_user_id=current_user.id
    )
    return success(data=result, message="OK")


# *kwq* 新增：执行匹配（同步返回结果）。
# 说明：当前候选池规模可控（SQL 召回上限 2000），先同步返回；
#      若后续接入向量召回导致耗时上升，再改为「返回 batch_id + 轮询」。
@router.post(
    "/run",
    summary="执行人岗匹配",
    response_model=SuccessResponse[MatchRunResponse],
)
async def run_matching(
    payload: MatchRunRequest,
    service: MatchingService = Depends(get_service),
    current_user: User = Depends(require_permission(PermissionEnum.MATCHING_RUN)),
):
    """按岗位画像执行匹配，返回候选人排序、四态明细与淘汰原因。"""
    result = await service.run(payload, current_user_id=current_user.id)
    return success(data=result, message="匹配完成")


# *kwq* 新增：双向匹配（人才找岗位，需求 §06）。
# 说明：只读试算不落库（与 explain 同策略），权限用 matching:run —— 它会跑完整
#      打分链路，代价与一次单岗位 run 相当，不能放宽到 matching:read。
@router.post(
    "/reverse",
    summary="双向匹配（人才找岗位）",
    response_model=SuccessResponse[MatchReverseResponse],
)
async def reverse_matching(
    payload: MatchReverseRequest,
    service: MatchingService = Depends(get_service),
    current_user: User = Depends(require_permission(PermissionEnum.MATCHING_RUN)),
):
    """给定人才，反查适合的开放岗位；打分口径与正向匹配完全一致，淘汰岗位带逐条原因。"""
    result = await service.run_reverse(payload, current_user_id=current_user.id)
    return success(data=result, message="双向匹配完成")


# *kwq* 新增（CMP-001）：多候选人横向对比（只读快照，不产生新批次）。
# 说明：用 matching:read —— 不重算、不落库，仅读取已有批次快照，代价低于一次 run。
@router.post(
    "/candidates/compare",
    summary="多候选人横向对比（只读快照，含 HR 复核状态）",
    response_model=SuccessResponse[MatchCompareResponse],
)
async def compare_candidates(
    payload: MatchCompareRequest,
    service: MatchingService = Depends(get_service),
    _: User = Depends(require_permission(PermissionEnum.MATCHING_READ)),
):
    """同一批次内 2~8 名候选人的综合分、维度分、关键条件、缺口与复核状态对照。

    - 全部数值原样取自批次快照，不重算、不落库、不产生新批次；
    - 复核状态（`review`）一次批量查回，未建档的候选人为 null（不是「待复核」）；
    - `gap_stats` / `common_gaps` 是缺口概览，始终返回；逐条明细受 include_gap 控制。
    """
    result = await service.compare(payload)
    return success(data=result, message=f"已对比 {len(result.rows)} 名候选人")


# *kwq* 新增：匹配记录分页。
@router.get(
    "",
    summary="匹配记录列表",
    response_model=SuccessResponse[PageResult[MatchHistoryItem]],
)
async def list_matchings(
    page: PageParams = Depends(),
    # *kwq* 新增（MATCH-021）：补上实际生效的过滤参数。
    # 原因：接口原先只接受分页参数，前端传 ?position_id=1 会被 FastAPI 静默忽略
    #      （不报错也不过滤），实测 total=43 与不加参数完全一样 —— 调用方以为筛过了，
    #      实际拿到全表数据，属于「静默错误」。这里显式声明并用白名单字段过滤。
    position_id: int | None = Query(None, ge=1, description="按岗位过滤"),
    talent_id: int | None = Query(None, ge=1, description="按人才过滤"),
    min_score: float | None = Query(None, ge=0, le=100, description="最低综合分过滤"),
    service: MatchingService = Depends(get_service),
    _: User = Depends(require_permission(PermissionEnum.MATCHING_READ)),
):
    """按批次时间倒序分页查询匹配记录。"""
    result = await service.page_list(
        page, position_id=position_id, talent_id=talent_id, min_score=min_score
    )
    return success(data=result, message="OK")


# *kwq* 新增：某岗位最新匹配结果。
@router.get(
    "/positions/{position_id}",
    summary="岗位最新匹配结果",
    response_model=SuccessResponse[MatchRunResponse],
)
async def get_latest_by_position(
    position_id: int,
    service: MatchingService = Depends(get_service),
    _: User = Depends(require_permission(PermissionEnum.MATCHING_READ)),
):
    """返回该岗位最近一次跑批的结果（从库中还原，不重算）。"""
    result = await service.get_latest(position_id)
    return success(data=result, message="OK")


# *kwq* 新增：某人才历史匹配记录。
@router.get(
    "/talents/{talent_id}",
    summary="人才历史匹配记录",
    response_model=SuccessResponse[list[MatchHistoryItem]],
)
async def get_history_by_talent(
    talent_id: int,
    limit: int = 50,
    service: MatchingService = Depends(get_service),
    _: User = Depends(require_permission(PermissionEnum.MATCHING_READ)),
):
    """返回该人才在各岗位上的历史匹配记录。"""
    if limit < 1 or limit > 200:
        limit = 50
    items = await service.get_history_by_talent(talent_id, limit=limit)
    return success(data=items, message="OK")


# *kwq* 新增：匹配解释（重算 + 校验，不落库）。
@router.post(
    "/explain",
    summary="生成匹配解释（含证据校验）",
    response_model=SuccessResponse[MatchExplanation],
)
async def explain_matching(
    payload: MatchExplainRequest,
    service: MatchingService = Depends(get_service),
    _: User = Depends(require_permission(PermissionEnum.MATCHING_READ)),
):
    """对指定岗位 × 人才生成解释；即使被硬条件淘汰也能调用（用于回答「为什么没出现」）。"""
    explanation = await service.explain(payload)
    return success(data=explanation, message="OK")


# ============================================================================
# *kwq* 新增（REVIEW-001）：HR 人工复核流程。
# 需求依据：匹配跑完只能看不能记，决策无留痕、权重无法用真实结果校准。
# 权限划分：写决策用 matching:review（新建权限码），读用 matching:read ——
#           部门管理者可以看匹配结果，但不应有权决定谁被淘汰/录用。
# ============================================================================
def get_review_service(db: AsyncSession = Depends(get_db)) -> MatchingReviewService:
    """MatchingReviewService 依赖。"""
    return MatchingReviewService(db)


@router.post(
    "/batches/{batch_id}/talents/{talent_id}/review",
    summary="提交 HR 复核决策（幂等 upsert + 乐观锁）",
    response_model=SuccessResponse[MatchingReviewDecideResponse],
)
async def decide_review(
    batch_id: str,
    talent_id: int,
    payload: MatchingReviewUpsertRequest,
    service: MatchingReviewService = Depends(get_review_service),
    current_user: User = Depends(require_permission(PermissionEnum.MATCHING_REVIEW)),
):
    """把「这个人入围/面试/淘汰/录用」记下来。

    - 同一 (批次, 人才) 重复提交是更新当前状态，不会堆出多条记录；
    - 目标状态与当前相同时返回 changed=false，不写流水（重试安全）；
    - 带 expected_version 时，并发冲突返回 409，避免互相覆盖；
    - 硬性条件 FAIL 仍推进属于人工放行，必须填 note，流水会标记 overridden。
    """
    result = await service.decide(
        batch_id=batch_id,
        talent_id=talent_id,
        payload=payload,
        operator_id=current_user.id,
    )
    return success(data=result, message=result.message or "OK")


@router.get(
    "/reviews",
    summary="复核记录分页",
    response_model=SuccessResponse[PageResult[MatchingReviewItem]],
)
async def list_reviews(
    page: PageParams = Depends(),
    batch_id: str | None = Query(None, max_length=32, description="按批次过滤"),
    position_id: int | None = Query(None, ge=1, description="按岗位过滤"),
    talent_id: int | None = Query(None, ge=1, description="按人才过滤"),
    review_status: ReviewStatusLiteral | None = Query(
        None, description="按复核状态过滤：pending/shortlisted/interviewing/rejected/hired"
    ),
    service: MatchingReviewService = Depends(get_review_service),
    _: User = Depends(require_permission(PermissionEnum.MATCHING_READ)),
):
    """复核工作台列表；未建档的候选人不会凭空出现（明细与漏斗口径一致）。"""
    result = await service.page_reviews(
        page,
        batch_id=batch_id,
        position_id=position_id,
        talent_id=talent_id,
        review_status=review_status,
    )
    return success(data=result, message="OK")


@router.get(
    "/reviews/{review_id}/events",
    summary="复核动作流水（审计时间线）",
    response_model=SuccessResponse[list[MatchingReviewEventItem]],
)
async def list_review_events(
    review_id: int,
    limit: int = Query(100, ge=1, le=200, description="返回条数上限"),
    service: MatchingReviewService = Depends(get_review_service),
    _: User = Depends(require_permission(PermissionEnum.MATCHING_READ)),
):
    """谁在什么时候把它从 A 改成了 B、为什么 —— 合规追溯的唯一依据。"""
    items = await service.list_events(review_id=review_id, limit=limit)
    return success(data=items, message="OK")


@router.get(
    "/positions/{position_id}/funnel",
    summary="岗位复核漏斗",
    response_model=SuccessResponse[ReviewFunnelResponse],
)
async def review_funnel(
    position_id: int,
    service: MatchingReviewService = Depends(get_review_service),
    _: User = Depends(require_permission(PermissionEnum.MATCHING_READ)),
):
    """待复核 / 入围 / 面试中 / 淘汰 / 录用 各阶段人数与录用率。

    口径：统计「当前所处状态」，不是历史累计进入人数；响应里带 caliber_note
    明示口径，避免报表各说各话。
    """
    return success(data=await service.funnel(position_id=position_id), message="OK")
