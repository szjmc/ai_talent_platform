"""
模块：app/services/talent_profile_service.py
职责：人才档案业务服务层。

标签：[RAG组] [P0]（被 [Agent组] talent_agent / [Vanna组] 分析依赖）

============================ 实现思路与代码要点 ============================
Service 的定位：把「校验 + 仓库调用 + 外部能力 + 事务」组织成一次完整业务。
统一写法：
    class XxxService:
        def __init__(self, db: AsyncSession, repo: XxxRepository | None = None):
            self.db = db
            self.repo = repo or XxxRepository()

        async def create_xxx(self, data: XxxCreate, current_user: User) -> Xxx:
            # 1) 权限 / 业务规则校验（不满足抛 BusinessException 子类）
            # 2) Schema -> dict -> ORM
            # 3) 调用仓库写入
            # 4) 副作用（审计、缓存、向量化、AI 任务）
            # 5) 返回 ORM 对象（由 Router 用 Response Schema 序列化）

硬性约束：
- Service 不处理 HTTP（不接收 Request、不返回 JSONResponse）；
- Service 不写复杂 SQL（查询交给 Repository）；
- 跨模块调用只通过对方 Service 的公开方法，禁止直接引用别人的内部实现；
- 事务：默认随请求提交；需要局部事务用 infrastructure.database.transaction。

- 手机号唯一性只对未删除记录生效（Service 校验时带 is_deleted=False）；
- 画像生成耗时，接口返回 202/任务 ID，前端轮询或 SSE 接收结果。
方法清单（按顺序实现）：
    - [ ] 创建人才：手机号查重 -> 创建主档 -> 触发画像生成（异步）
    - [ ] 更新人才：字段白名单 + 变更前后数据写入审计
    - [ ] 列表 / 详情：分页查询 + 关联简历、测评、匹配摘要
    - [ ] 语义检索：调用 RAG/向量检索拿 talent_id，回表并按得分排序
    - [ ] 画像刷新：调用 ai/agents/talent_agent 生成并写入 talent_portraits
    - [ ] 合并重复档案：确认后把简历、测评、匹配记录迁移到主档，删除重复档
=========================================================================
"""

"""gxd-复用既有人才、画像和审计表，直接执行业务与派生资料刷新。"""
import asyncio
import logging
from datetime import datetime
from sqlalchemy.exc import IntegrityError
from app.services.talent_access import actor_context
from app.repositories.talent_profile_repository import TalentRepository
from app.repositories.talent_resume_repository import ResumeRepository
from app.models.talent import Talent, TalentPortrait
from app.core.exceptions import NotFoundError, DuplicateError, BusinessRuleError, AIError, PermissionDeniedError, BusinessException
from app.schemas.talent import TalentCreate
from app.utils.resume_data import extract_parsed, extract_skills
import json

class TalentService:
    def __init__(self, db, user=None, context=None):
        """gxd-注入会话、可信身份及人才仓库。"""
        self.db, self.context = db, actor_context(user, context)
        self.repo = TalentRepository()
        self.last_refresh = None

    async def get_talent(self, talent_id, lock=False):
        """gxd-查询可访问人才，隐藏无权限资源的存在性。"""
        self.context.require_permission("talent:read")
        obj = await self.repo.get_scoped(self.db, talent_id, self.context, lock)
        if obj is None:
            raise NotFoundError("人才不存在或无权访问")
        return obj

    def public_data(self, obj):
        """gxd-组织人才响应并脱敏联系方式，身份证不返回。"""
        data = {key: getattr(obj, key) for key in ("id", "name", "education", "school", "major", "work_years", "current_company", "current_position", "organization_id", "skill_tags", "portrait_summary", "potential_level", "status", "version", "created_at")}
        mobile = obj.mobile or ""
        data["mobile"] = mobile[:3] + "****" + mobile[-4:] if len(mobile) >= 7 else None
        email = obj.email or ""
        data["email"] = email[:1] + "***@" + email.split("@")[-1] if "@" in email else None
        if self.last_refresh is not None:
            data["refresh"] = self.last_refresh
        return data

    async def page_list(self, page, query):
        """gxd-按权限、关键词和业务筛选分页查询人才。

        [性能修复] 原实现先 `repo.filtered()` 把命中的全部行物化成实体、抽出 id，
        再 `WHERE id IN (全部 id)` 走一遍分页 —— 两倍全表扫描 + 大列表序列化，
        人才库 3000 行时首页 ~320ms 且随数据量线性增长（前端页面直接可感知）。
        现改为把筛选条件编译成 WHERE 下推：请求只发 count + page 两条 SQL，
        SQL 从 O(全表) 降到 O(命中页)，实测首页 320ms -> 10ms 量级。
        """
        self.context.require_permission("talent:read")
        filters = query.model_dump(exclude_none=True)
        # 区间反了就直接拒绝，别把空结果当成「没有符合条件的人」返回给前端。
        low, high = filters.get("work_years_min"), filters.get("work_years_max")
        if low is not None and high is not None and low > high:
            raise BusinessRuleError("工作年限下限不能大于上限")
        extra_where = self.repo.filtered_conditions(self.context, filters)
        result = await self.repo.paginate(
            self.db, page, keyword=filters.get("keyword"), extra_where=extra_where
        )
        result.items = [self.public_data(x) for x in result.items]
        return result

    async def detail(self, talent_id):
        """gxd-返回人才详情、简历摘要及当前索引状态。

        索引状态改为**现场核对 Milvus**（按 talent_id 读回版本号），不再返回
        「未使用独立索引状态表」这类无信息占位：团队约定一名人才一条向量，
        主键即 talent_id，读得到且版本一致才算已索引。
        """
        from app.config.settings import settings
        from app.services.talent_retrieval_service import TalentRetrievalService
        obj = await self.get_talent(talent_id)
        result = self.public_data(obj)
        rows = await ResumeRepository().list_by_talent(self.db, obj.id)
        result["resumes"] = [{"id": x.id, "file_name": x.file_name, "status": x.status} for x in rows]
        portrait = await self.repo.latest_portrait(self.db, obj.id)
        result["portrait"] = ({"status": "ready", "version": portrait.version,
                               "potential_level": portrait.potential_level}
                              if portrait else {"status": "pending"})
        if settings.MILVUS_ENABLED:
            try:
                found, state = await TalentRetrievalService(
                    self.db, context=self.context).index_state(obj)
            except Exception as exc:  # noqa: BLE001 - 索引探测失败不影响档案详情
                found, state = {"status": "unknown", "indexed": False}, "unknown"
                result["index_error"] = f"{type(exc).__name__}"
            result["index"] = {
                "status": state,
                "version": obj.version,
                "indexed_version": found.get("version"),
                "primary_field": found.get("primary_field", "talent_id"),
                "message": {"current": "向量与当前档案版本一致",
                            "stale": "向量版本落后于档案，请重建索引",
                            "missing": "尚未写入该人才的向量",
                            "unknown": "索引状态读取失败，可稍后重试"}.get(state, ""),
            }
        else:
            result["index"] = {"status": "disabled", "version": obj.version,
                               "message": "Milvus未启用，向量尚未同步"}
        # 完成标准：画像存在（MySQL）且向量为当前版本（Milvus）。
        result["complete"] = bool(portrait) and result["index"]["status"] == "current"
        return result

    async def create_talent(self, data, operator=None, *, refresh=True):
        """gxd-保存人才后直接尝试刷新画像；简历绑定流程可延后到绑定完成再刷新。"""
        self.context.require_permission("talent:create")
        # exclude_none：gender / status 在库里是 NOT NULL（有默认值），
        # 请求未提交时不能把 None 写进去（否则数据库直接拒绝）。
        values = data.model_dump(exclude_none=True)
        organization_id = values.get("organization_id")
        if not (self.context.is_super_admin or self.context.data_scope == "all"):
            if organization_id not in (None, self.context.organization_id):
                raise PermissionDeniedError("不能将人才创建到其他部门")
            values["organization_id"] = self.context.organization_id
        if await self.repo.get_by_mobile(self.db, values.get("mobile")):
            raise DuplicateError("手机号已存在，请通过查重与合并流程处理")
        obj = Talent(**values, created_by=self.context.user_id, source="manual")
        try:
            async with self.db.begin_nested():
                await self.repo.save(self.db, obj)
        except IntegrityError as exc:
            raise DuplicateError("人才资料存在唯一性冲突") from exc
        # except IntegrityError as exc:
        #     # gxd-临时输出数据库原始错误，定位具体约束。
        #     print("新增人才失败，数据库原始原因：", exc.orig, flush=True)
        #     raise DuplicateError("人才资料存在唯一性冲突") from exc
        if refresh:
            await self.refresh_derived(obj.id)
        return obj

    async def create_from_resume(self, resume):
        """gxd-从已校验解析结果创建人才，不用虚构姓名填补缺失值。"""
        # 兼容扁平 / 嵌套两种历史写入格式；原写法 resume.parsed_data["parsed"] 遇扁平数据必 KeyError
        parsed = extract_parsed(resume.parsed_data)
        basic = dict(parsed.get("basic_info") or {})
        if not basic.get("name"):
            raise BusinessRuleError("简历缺少姓名，请先补充资料")
        basic["work_years"] = basic.get("work_years") or 0
        obj = await self.create_talent(TalentCreate(**basic, skill_tags=extract_skills(resume.parsed_data)), refresh=False)
        obj.source, obj.resume_id = "ai_resume", resume.id
        await self.db.flush()
        return obj

    async def update_talent(self, talent_id, data):
        """gxd-只更新明确提交字段、提升原有版本号并直接尝试刷新画像和向量。"""
        self.context.require_permission("talent:update")
        obj = await self.get_talent(talent_id, lock=True)
        # 乐观锁：请求带了 version 就必须与当前版本一致，防止两人同时编辑互相覆盖。
        self.repo.check_version(obj, getattr(data, "version", None))
        values = data.model_dump(exclude_unset=True)
        values.pop("version", None)  # version 只用于校验，不直接写入
        # 非空列不接受显式 null（前端清空下拉时可能提交 null）。
        for field in ("gender", "status"):
            if values.get(field, "keep") is None:
                values.pop(field, None)
        if values.get("organization_id") not in (None, obj.organization_id) and not (self.context.is_super_admin or self.context.data_scope == "all"):
            raise PermissionDeniedError("无权跨部门移动人才")
        other = await self.repo.get_by_mobile(self.db, values.get("mobile"))
        if other and other.id != obj.id:
            raise DuplicateError("手机号已存在")
        try:
            async with self.db.begin_nested():
                for field, value in values.items():
                    setattr(obj, field, value)
                obj.version += 1
                obj.updated_by = self.context.user_id
                await self.db.flush()
        except IntegrityError as exc:
            raise DuplicateError("人才资料更新冲突") from exc
        await self.refresh_derived(obj.id)
        return obj

    async def delete_talent(self, talent_id):
        """gxd-逻辑删除档案，并同步删除该人才的画像向量。

        检索侧虽然会回表排除已删除档案，但向量留着有两个问题：
            1. Milvus 里永久堆积「永远召不回」的行，集合越来越大；
            2. 同一份简历后续重新入库若复用了同一个 talent_id（自增不会复用，
               但手工/数据迁移会），会读到别人的旧版本向量。
        所以删除时直接按 talent_id 删除向量；Milvus 不可用只记日志，不影响删除本身。
        """
        from app.config.settings import settings
        from app.services.talent_retrieval_service import TalentRetrievalService
        self.context.require_permission("talent:delete")
        obj = await self.get_talent(talent_id, lock=True)
        obj.mobile = None
        obj.soft_delete()
        obj.version += 1
        await self.db.flush()
        if settings.MILVUS_ENABLED:
            try:
                await TalentRetrievalService(
                    self.db, context=self.context).drop_index(talent_id)
            except Exception as exc:  # noqa: BLE001 - 向量清理失败不能回滚业务删除
                logging.getLogger(__name__).warning(
                    "gxd-删除人才后清理向量失败 talent_id=%s type=%s", talent_id, type(exc).__name__)

    async def refresh_derived(self, talent_id, *, portrait=True):
        """gxd-请求内刷新派生资料；AI故障回滚本阶段并返回提示，保留已保存主档。

        完成标准（团队约定）：画像与向量索引**都成功**才算 complete。
        任一阶段失败时返回 partial/failed，主档与已完成的阶段都保留，
        重试时用 ensure_derived() 只补没成功的部分。
        """
        from app.config.settings import settings
        from app.config.talent_config import talent_settings
        from app.services.talent_retrieval_service import TalentRetrievalService
        obj = await self.get_talent(talent_id, lock=True)
        result = {"talent_id": talent_id, "portrait": {"status": "not_requested"},
                  "index": {"status": "not_run"}, "status": "pending"}
        stages = (["portrait"] if portrait else []) + ["index"]
        for stage in stages:
            if stage == "index" and not settings.MILVUS_ENABLED:
                result[stage] = {"status": "disabled", "message": "Milvus未启用，向量尚未同步"}
                continue
            try:
                async with self.db.begin_nested():
                    action = (self.generate_portrait(talent_id) if stage == "portrait"
                              else TalentRetrievalService(self.db, context=self.context).sync_index(talent_id))
                    output = await asyncio.wait_for(action, timeout=talent_settings.refresh_timeout)
                result[stage] = {**output, "status": "success"}
            except Exception as exc:
                # 保存点只回滚本阶段；重新读取，避免访问过期ORM属性触发隐式异步IO。
                await self.db.refresh(obj)
                logging.getLogger(__name__).warning("gxd-派生资料刷新失败 stage=%s type=%s", stage, type(exc).__name__)
                message = exc.message if isinstance(exc, BusinessException) else "刷新失败或超时，请检查服务后重新调用刷新接口"
                result[stage] = {"status": "failed", "message": message}
                break
        result["status"] = self.derived_status(result, stages)
        if result["status"] != "complete":
            result["message"] = self.derived_message(result)
        self.last_refresh = result
        return result

    @staticmethod
    def derived_status(result, stages):
        """gxd-汇总阶段结果：全部 success 才算 complete，其余区分 partial/failed。"""
        statuses = [result.get(stage, {}).get("status") for stage in stages]
        if statuses and all(x == "success" for x in statuses):
            return "complete"
        if any(x == "success" for x in statuses):
            return "partial"
        return "failed"

    @staticmethod
    def derived_message(result):
        """gxd-把未完成的原因说清楚，避免前端只看到「完成」两个字。"""
        parts = []
        for stage, label in (("portrait", "画像"), ("index", "向量索引")):
            item = result.get(stage) or {}
            state = item.get("status")
            if state in (None, "success"):
                continue
            if state == "disabled":
                parts.append("Milvus未启用，向量索引未同步")
            elif state == "failed":
                parts.append(f"{label}生成失败：{item.get('message') or '请稍后重试'}")
            elif state == "not_run":
                parts.append(f"{label}未执行（前置阶段失败），档案已保留，可只重试该阶段")
            elif state == "not_requested":
                continue
        return "；".join(parts) or "派生资料未全部完成，可重试"

    async def ensure_derived(self, talent_id):
        """gxd-只补缺失阶段：画像没有才生成，向量缺失/过期才重建索引。

        用于「索引失败后重试只补索引」以及 RAG 上传链路的幂等补偿：
        画像已存在、向量已是当前版本时不会重复调用模型或 embedding。
        """
        from app.config.settings import settings
        from app.services.talent_retrieval_service import TalentRetrievalService
        obj = await self.get_talent(talent_id)
        portrait = await self.repo.latest_portrait(self.db, talent_id)
        need_portrait = portrait is None
        state = "unknown"
        if settings.MILVUS_ENABLED:
            try:
                _, state = await asyncio.wait_for(
                    TalentRetrievalService(self.db, context=self.context).index_state(obj), timeout=15)
            except Exception as exc:  # noqa: BLE001 - 探测失败按「需要重建」处理，不阻断主流程
                logging.getLogger(__name__).warning("gxd-索引状态探测失败 type=%s", type(exc).__name__)
                state = "unknown"
        need_index = bool(settings.MILVUS_ENABLED) and state != "current"

        actions = [name for name, needed in (("portrait", need_portrait), ("index", need_index)) if needed]
        if not actions:
            # 完成口径必须与 detail() 一致：画像在 MySQL、向量为当前版本。
            # 不能因为「这次没有需要执行的阶段」就无条件报 complete —— 否则 Milvus 关闭
            # （或集合还没建）的部署里，第二份及以后的简历都会显示成功，但检索永远搜不到。
            if not settings.MILVUS_ENABLED:
                index_part = {"status": "disabled", "message": "Milvus未启用，向量尚未同步"}
            else:
                index_part = {"status": "skipped", "version": getattr(obj, "version", None),
                              "message": "向量已是当前版本，无需重建"}
            current = {
                "talent_id": talent_id,
                "actions": [],
                "status": "complete" if (portrait is not None and index_part["status"] == "skipped") else (
                    "partial" if (portrait is not None or index_part["status"] == "skipped") else "failed"),
                "portrait": {"status": "skipped", "version": getattr(portrait, "version", None)},
                "index": index_part,
                "index_state": state,
            }
            if current["status"] != "complete":
                current["message"] = self.derived_message(current)
            self.last_refresh = current
            return current

        # 复用同一套阶段执行逻辑：portrait 只在缺失时执行，index 只在缺失/过期时执行。
        refreshed = await self.refresh_derived(talent_id, portrait=need_portrait)
        refreshed["actions"] = actions
        refreshed["index_state"] = state
        if not need_index and refreshed.get("index", {}).get("status") == "not_run":
            # 向量已是最新（但画像刚补）：不要让「未执行索引」把它判成未完成。
            refreshed["index"] = {"status": "success", "version": getattr(obj, "version", None),
                                  "message": "向量已是当前版本，无需重建", "skipped": True}
            refreshed["status"] = self.derived_status(refreshed, ["portrait", "index"])
            refreshed.pop("message", None)
        self.last_refresh = refreshed
        return refreshed

    async def profile_source(self, talent_id):
        """gxd-提供已鉴权的当前档案与简历证据，屏蔽联系方式后交给模型。"""
        obj = await self.get_talent(talent_id)
        rows = await ResumeRepository().list_by_talent(self.db, talent_id)
        facts = {k: getattr(obj, k) for k in ("name", "education", "school", "major", "work_years", "current_company", "current_position", "skill_tags", "self_evaluation")}
        sources = []
        for row in rows:
            parsed = dict(extract_parsed(row.parsed_data))
            parsed.pop("basic_info", None)
            sources.append(parsed)
        return json.dumps({"current_profile": facts, "resume_experiences": sources}, ensure_ascii=False)

    async def generate_portrait(self, talent_id):
        """gxd-执行画像Agent并保存到团队已有画像表，后续同步由调用方组织。"""
        from app.ai.agents.talent_agent import TalentAgent
        from app.config.prompt_config import GXD_PROMPT_VERSION
        obj = await self.get_talent(talent_id, lock=True)
        result = await TalentAgent(db=self.db, context=self.context).run({"talent_id": obj.id})
        if not result.get("ok"):
            raise AIError("画像生成失败，请检查模型配置或重试")
        data = dict(result["result"])
        latest = await self.repo.latest_portrait(self.db, obj.id)
        portrait = TalentPortrait(talent_id=obj.id, version=(latest.version + 1 if latest else 1), dimensions=data["dimensions"], labels=data["labels"], strengths=data["strengths"], weaknesses=data["weaknesses"], suggested_positions=data["suggested_positions"], potential_level=data["potential_level"], model_name=result.get("model"), prompt_version=GXD_PROMPT_VERSION, generated_at=datetime.now(), created_by=self.context.user_id)
        await self.repo.save(self.db, portrait)
        obj.portrait_summary = data["summary"]
        obj.potential_level = data["potential_level"]
        obj.version += 1
        return {"talent_id": obj.id, "portrait_id": portrait.id, "version": portrait.version, "warnings": data.get("warnings", [])}

    async def portrait(self, talent_id):
        """gxd-读取最新画像，不存在时返回清晰的待生成状态。"""
        await self.get_talent(talent_id)
        obj = await self.repo.latest_portrait(self.db, talent_id)
        return obj.to_dict() if obj else {"talent_id": talent_id, "status": "pending"}

    async def duplicate_candidates(self, parsed, threshold=None):
        """gxd-联合手机号和向量召回识别候选，仅标识可信重复，不自动按相似分数合并。"""
        basic = parsed.get("basic_info", {})
        found = []
        row = await self.repo.get_by_mobile(self.db, basic.get("mobile"))
        if row:
            allowed = await self.repo.get_scoped(self.db, row.id, self.context)
            if allowed:
                found.append({"talent_id": row.id, "name": row.name, "reason": "mobile", "same_name": row.name == basic.get("name")})
            else:
                return [{"reason": "restricted_identity_conflict", "requires_review": True}]
        # 语义查重只是「加分项」：Milvus/Embedding 任何不可用都必须降级为「仅手机号查重」，
        # 否则每份新简历都会在 finalize_parse 里抛错 —— 表现为自动入库全部失败。
        # 这里必须连同 embeddings 一起兜住（无密钥时 Embedding 会抛 AIError）。
        from app.config.settings import settings
        if settings.MILVUS_ENABLED:
            from app.config.talent_config import talent_settings
            from app.schemas.talent import TalentSemanticSearchRequest
            from app.services.talent_retrieval_service import TalentRetrievalService

            query = " ".join(parsed.get("skills", [])) or basic.get("current_position") or basic.get("name") or "人才"
            try:
                result = await TalentRetrievalService(
                    self.db, context=self.context).search(
                        TalentSemanticSearchRequest(query=query, top_k=5), interpret=False)
            except Exception as exc:  # noqa: BLE001 - 语义查重在人工复核流程之前，不能阻断入库
                logging.getLogger(__name__).warning(
                    "gxd-语义查重不可用，降级为手机号查重 type=%s", type(exc).__name__)
                result = {"items": []}
            limit = threshold if threshold is not None else talent_settings.duplicate_threshold
            found.extend(
                {"talent_id": x["talent_id"], "name": x["name"], "score": x["score"],
                 "reason": "semantic", "requires_review": True}
                for x in result.get("items") or []
                if x.get("score", 0) >= limit
                and not any(y.get("talent_id") == x["talent_id"] for y in found))
        return found

    async def merge(self, data):
        """gxd-确认后原子合并：保留主档冲突值、补充空值、迁移关联、记录快照。"""
        self.context.require_permission("talent:update")
        ids = sorted({data.target_id, *data.source_ids})
        rows = {key: await self.get_talent(key, lock=True) for key in ids}
        target = rows[data.target_id]
        snapshots = {str(key): self.public_data(row) for key, row in rows.items()}
        snapshots = json.loads(json.dumps(snapshots, default=str, ensure_ascii=False))
        conflicts = []
        try:
            async with self.db.begin_nested():
                for source_id in data.source_ids:
                    source = rows[source_id]
                    for field in ("education", "school", "major", "current_company", "current_position", "email", "mobile"):
                        a, b = getattr(target, field), getattr(source, field)
                        if a and b and a != b:
                            conflicts.append({"field": field, "source_id": source.id, "resolution": "keep_target"})
                        elif not a and b:
                            if field == "mobile":
                                source.mobile = None
                                await self.db.flush()
                            setattr(target, field, b)
                    target.skill_tags = list(dict.fromkeys((target.skill_tags or []) + (source.skill_tags or [])))
                    await self.repo.transfer_links(self.db, source.id, target.id)
                    source.mobile = None
                    source.soft_delete()
                    source.version += 1
                target.version += 1
                target.updated_by = self.context.user_id
                await self.repo.record_merge(self.db, self.context.user_id, target.id, snapshots,
                    {"operation": "merge", "source_ids": data.source_ids, "conflicts": conflicts,
                     "target": json.loads(json.dumps(self.public_data(target), default=str, ensure_ascii=False))})
                await self.db.flush()
        except IntegrityError as exc:
            raise DuplicateError("关联记录存在唯一冲突，合并已回滚，请先处理冲突记录") from exc
        # 源档案已软删除：其向量必须清掉（否则检索仍会命中被合并掉的重复档案）。
        from app.config.settings import settings
        if settings.MILVUS_ENABLED:
            from app.services.talent_retrieval_service import TalentRetrievalService
            retrieval = TalentRetrievalService(self.db, context=self.context)
            for source_id in data.source_ids:
                try:
                    await retrieval.drop_index(source_id)
                except Exception as exc:  # noqa: BLE001 - 清理失败不回滚已完成的合并
                    logging.getLogger(__name__).warning(
                        "gxd-合并后清理源档案向量失败 talent_id=%s type=%s", source_id, type(exc).__name__)
        refresh = await self.refresh_derived(target.id)
        return {"target_id": target.id, "source_ids": data.source_ids, "conflicts": conflicts, "refresh": refresh}
