"""
模块：app/services/resume_service.py
职责：简历处理业务服务层。

标签：[RAG组] [P0]（被 [架构组] resume_* 技能 / [Agent组] 简历链路依赖）

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

- 文件校验失败抛 FileError；解析失败保留 parse_error 并置 status=failed；
- 解析在工作流中执行，接口不要同步等待 LLM（超时风险）。
方法清单（按顺序实现）：
    - [ ] 上传：校验扩展名/大小 -> 计算 sha256 -> 秒传判断 -> 存文件 -> 建 resume 记录
    - [ ] 解析：把 status 置为 parsing -> 调用 ai/workflows/resume_workflow -> 写回 parsed_data
    - [ ] 解析结果查询：返回结构化 JSON 与 warnings
    - [ ] 绑定人才：解析成功后绑定或新建人才档案
    - [ ] 重新解析：force=True 时覆盖旧结果并记录版本
=========================================================================
"""

"""gxd-简历上传、请求内解析、结果查询与档案绑定业务。"""
import asyncio
import tempfile
from datetime import datetime
from pathlib import Path
from hashlib import sha256
from sqlalchemy.exc import IntegrityError
from app.services.talent_access import actor_context
from app.core.exceptions import NotFoundError, DuplicateError, BusinessRuleError, BusinessException, FileError
from app.models.resume import Resume
from app.repositories.talent_resume_repository import ResumeRepository
from app.schemas.resume import ResumeResponse
from app.services.file_service import FileService
from app.utils.resume_data import extract_parsed, extract_warnings

class ResumeService:
    def __init__(self, db=None, user=None, context=None):
        """gxd-注入会话与可信身份；允许Skill无会话构造后在公开方法内建立任务会话。"""
        self.db = db
        self.context = actor_context(user, context)
        self.repo = ResumeRepository()
        self.last_refresh = None

    async def get_resume(self, resume_id, lock=False):
        """gxd-查询权限范围内的简历，不存在或无权时统一返回404。"""
        row = await self.repo.get_scoped(self.db, resume_id, self.context, lock)
        if row is None:
            raise NotFoundError("简历不存在或无权访问")
        return row

    async def process_uploaded_file(self, file_id: int):
        """gxd-RAG对接点1：简历文件及元数据提交后，以可信上传人身份自动处理一份简历。"""
        self.context.require_permission("resume:parse")
        if self.db is None:
            # gxd-上传成功后调用 await ResumeService(context=可信上下文).process_uploaded_file(file_id)。
            from app.infrastructure.database.session import session_scope
            async with session_scope() as db:
                return await ResumeService(db, context=self.context).process_uploaded_file(file_id)

        file = await self.repo.get_file(self.db, file_id, lock=True)
        if file is None or file.biz_type != "resume":
            raise NotFoundError("简历文件不存在或无权处理")
        if file.uploader_id != self.context.user_id and not self.context.is_super_admin:
            raise NotFoundError("简历文件不存在或无权处理")
        result = await self.ingest_file(file)
        await self.record_ingest(result)
        return result

    async def process_rag_revision(self, revision_id: int, *, context=None):
        """gxd-RAG对接点2：知识库原件（category=resume）提交后，按 revision_id 自动进入人才链路。

        与 process_uploaded_file 的差别只在**入口校验**：
            - 原件的 biz_type 是 knowledge_revision（不是 resume），storage_type 可以是 local；
            - 归属按「版本原件上传人」判断，不接受请求体传入的身份或角色。

        其余步骤（哈希秒传/查重、原件读取、解析工作流、画像与向量）完全复用同一份实现，
        不另写一套解析器，避免两条链路各自演化。

        调用约定：
            - 只在「原件 + 版本记录已提交 MySQL」之后调用一次；
            - 预览 / 发布 / 恢复等后续操作不得再次触发（由 file_router 的调度点保证）。
        """
        self.context.require_permission("resume:parse")
        if self.db is None:
            from app.infrastructure.database.session import session_scope
            async with session_scope() as db:
                return await ResumeService(
                    db, context=context or self.context).process_rag_revision(revision_id)

        revision = await self.revision_row(revision_id)
        if revision is None:
            raise NotFoundError("文档版本不存在")
        if revision.category != "resume":
            raise BusinessRuleError("该版本不是简历分类，未触发人才处理")
        if not revision.source_file_id:
            raise NotFoundError("版本缺少来源原件")
        file = await self.repo.get_file(self.db, revision.source_file_id, lock=True)
        if file is None:
            raise NotFoundError("版本原件不存在")
        if file.uploader_id != self.context.user_id and not self.context.is_super_admin:
            # 不泄露他人原件是否存在
            raise NotFoundError("版本原件不存在或无权处理")

        result = await self.ingest_file(file)
        result["revision_id"] = revision.id
        result["source_file_id"] = revision.source_file_id
        result["doc_id"] = revision.doc_id
        await self.record_ingest(result)
        return result

    async def revision_row(self, revision_id):
        """gxd-读取知识库版本记录（入口事实来源：category / source_file_id）。"""
        from app.repositories.knowledge_revision_repository import KnowledgeRevisionRepository
        return await KnowledgeRevisionRepository().revision(self.db, revision_id)

    async def ingest_file(self, file):
        """gxd-原件 -> 简历 -> 人才 的唯一实现，由两个入口共用。

        同一内容哈希必然落到同一条 resumes 记录（MySQL 唯一键），因此
        「重复上传字节完全相同的文件」只会复用既有简历与人才档案，不会重复建模。
        """
        files = FileService(self.db)
        extension = files.resume_extension(file.file_name)
        digest = file.file_hash
        if not digest:
            # gxd-元数据缺少哈希时，以原件字节计算；只写简历自己的记录。
            digest = sha256(await files.read_original(file)).hexdigest()
        digest = digest.lower()
        row = await self.repo.get_by_hash(self.db, digest)
        if row is None:
            row = Resume(file_id=file.id, file_name=file.file_name, file_size=file.file_size,
                         file_type=extension.lstrip("."), file_hash=digest, status="uploaded",
                         created_by=file.uploader_id or self.context.user_id)
            try:
                async with self.db.begin_nested():
                    await self.repo.save(self.db, row)
            except IntegrityError as exc:
                # gxd-不同文件编号可能内容相同；只在 MySQL 唯一键冲突时尝试复用。
                if exc.orig.args[:1] != (1062,):
                    raise
                row = await self.repo.get_by_hash(self.db, digest, lock=True)
                if row is None:
                    raise

        row = await self.get_resume(row.id, lock=True)
        # 已绑定人才但派生资料没补齐（例如上次索引失败）：只补缺失阶段，不重复解析。
        if row.status == "success" and row.talent_id:
            result = self.parse_result_data(row)
            result["processing_status"] = "succeeded"
            result["reused"] = True
            result["refresh"] = await self.ensure_derived(row.talent_id)
            result["file_id"] = file.id
            return result
        if row.status == "success":
            result = self.parse_result_data(row)
            result["processing_status"] = "needs_review"
            result["reused"] = True
        else:
            result = await self.parse_resume(resume_id=row.id)
            result["reused"] = False
        result["file_id"] = file.id
        return result

    async def ensure_derived(self, talent_id):
        """gxd-只在画像缺失 / 向量过期时补齐（索引失败后重试只补索引）。"""
        from app.services.talent_profile_service import TalentService
        try:
            self.last_refresh = await TalentService(
                self.db, context=self.context).ensure_derived(talent_id)
        except BusinessException as exc:
            # 派生资料失败不影响已保存的档案与简历；错误随结果返回，可稍后重试。
            self.last_refresh = {"talent_id": talent_id, "status": "failed", "message": exc.message}
        return self.last_refresh

    async def record_ingest(self, result):
        """gxd-把本次自动处理的结果写回简历行，便于按 revision_id 追溯整条链路。

        不新增表：parsed_data 已是 JSON 列，ingest 段只记录状态、时间与版本，
        便于前端/运维在「不点任何按钮」的情况下查到处理进度。
        """
        resume_id = result.get("resume_id")
        if not resume_id:
            return
        row = await self.get_resume(resume_id, lock=True)
        data = dict(row.parsed_data or {})
        refresh = result.get("refresh") or {}
        data["ingest"] = {
            "status": result.get("processing_status") or "unknown",
            "revision_id": result.get("revision_id"),
            "source_file_id": result.get("source_file_id"),
            "file_id": result.get("file_id"),
            "talent_id": result.get("talent_id"),
            "reused": bool(result.get("reused")),
            "derived_status": refresh.get("status"),
            "duplicates": len(result.get("duplicates") or []),
            "processed_at": datetime.now().isoformat(timespec="seconds"),
        }
        row.parsed_data = data
        await self.db.flush()

    async def revision_ingest_status(self, revision_id):
        """gxd-按 revision_id 回查整条链路：原件 -> 简历 -> 人才 -> 画像/向量。

        验收口径（不点任何按钮）：
            revision_id -> source_file_id -> resume_id -> talent_id，
            同一 talent_id 在 MySQL 有画像、在 Milvus 有一条同版本向量。
        """
        self.context.require_permission("talent:read")
        revision = await self.revision_row(revision_id)
        if revision is None:
            raise NotFoundError("文档版本不存在")
        file = await self.repo.get_file(self.db, revision.source_file_id) if revision.source_file_id else None
        chain = {
            "revision_id": revision.id,
            "doc_id": revision.doc_id,
            "category": revision.category,
            "revision_status": revision.status,
            "source_file_id": revision.source_file_id,
            "file_name": getattr(file, "file_name", None),
            "storage_type": getattr(file, "storage_type", None),
            "resume_id": None,
            "resume_status": None,
            "talent_id": None,
            "processing_status": "not_started",
            "duplicates": [],
            "portrait": {"status": "unknown"},
            "index": {"status": "unknown"},
            "complete": False,
        }
        if file is None:
            return chain
        rows = await self.repo.list(
            self.db, filters={"file_id": file.id}, extra_where=[self.repo.scope(self.context)], limit=1)
        if not rows:
            chain["processing_status"] = "not_ingested"
            return chain
        row = rows[0]
        ingest = (row.parsed_data or {}).get("ingest") or {}
        chain.update(
            resume_id=row.id,
            resume_status=row.status,
            talent_id=row.talent_id,
            processing_status=ingest.get("status") or ("succeeded" if row.talent_id else row.status),
            duplicates=(row.parsed_data or {}).get("duplicates") or [],
            processed_at=ingest.get("processed_at"),
        )
        if row.talent_id:
            from app.services.talent_profile_service import TalentService
            detail = await TalentService(self.db, context=self.context).detail(row.talent_id)
            chain["portrait"] = detail.get("portrait", {"status": "unknown"})
            chain["index"] = detail.get("index", {"status": "unknown"})
            chain["complete"] = bool(detail.get("complete"))
            chain["needs_review"] = chain["processing_status"] == "needs_review"
        return chain

    async def upload(self, filename, content, content_type=None):
        """gxd-按文件哈希幂等上传，同文件跨权限范围不泄露已有记录。"""
        self.context.require_permission("resume:upload")
        digest = sha256(content).hexdigest()
        existing = await self.repo.get_by_hash(self.db, digest)
        if existing:
            if existing.is_deleted or not await self.repo.get_scoped(self.db, existing.id, self.context):
                raise DuplicateError("文件已存在，当前账户不能复用；请联系管理员")
            return self.upload_result(existing, True)
        try:
            async with self.db.begin_nested():
                file = await FileService(self.db).save_resume(filename, content, content_type, self.context.user_id)
                row = Resume(file_id=file.id, file_name=file.file_name, file_size=file.file_size, file_type=file.extension.lstrip("."), file_hash=digest, status="uploaded", created_by=self.context.user_id)
                await self.repo.save(self.db, row)
                file.biz_id = row.id
                await self.db.flush()
        except IntegrityError as exc:
            raise DuplicateError("相同文件正在上传，请重新查询") from exc
        return self.upload_result(row)

    @staticmethod
    def upload_result(row, duplicated=False):
        """gxd-生成不暴露磁盘路径的上传响应。"""
        return {"resume_id": row.id, "file_id": row.file_id, "file_name": row.file_name, "file_size": row.file_size, "status": row.status, "duplicated": duplicated}

    async def page_list(self, page, **filters):
        """gxd-按权限分页查询简历元数据。"""
        self.context.require_permission("talent:read")
        result = await self.repo.paginate(self.db, page, keyword=filters.pop("keyword", None), filters=filters, extra_where=[self.repo.scope(self.context)])
        result.items = [ResumeResponse.model_validate(x).model_dump(mode="json") for x in result.items]
        return result

    async def result(self, resume_id):
        """gxd-返回解析内容及质量问题；不将模型置信度报告为实测准确率。"""
        row = await self.get_resume(resume_id)
        return self.parse_result_data(row)

    @staticmethod
    def parse_result_data(row):
        """gxd-用当前已鉴权的简历行生成结果，重复通知复用持锁读取的最新状态。"""
        data = row.parsed_data or {}
        # extract_parsed 兼容扁平 / 嵌套两种历史写入格式（原写法只认嵌套，遗留链写入的简历读成空）
        return {"resume_id": row.id, "status": row.status, "parsed": extract_parsed(data) or None, "warnings": extract_warnings(data), "duplicates": data.get("duplicates", []), "accuracy": None, "parsed_at": row.parsed_at, "parse_error": row.parse_error, "talent_id": row.talent_id}

    async def source_text(self, resume_id):
        """gxd-从鉴权文件提取原文，临时文件自动清理，同步解析在线程执行。"""
        row = await self.get_resume(resume_id)
        file, content = await FileService(self.db).read(row.file_id)
        if not file.file_hash and sha256(content).hexdigest() != row.file_hash:
            raise FileError("文件内容已变化，请重新提交新版本简历")
        from app.ai.rag.document_loader import load
        from app.ai.utils.resume_normalizer import clean_text
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "source" + FileService.resume_extension(file.file_name))
            await asyncio.to_thread(path.write_bytes, content)
            docs = await asyncio.to_thread(load, str(path))
        # LangChain 的 Document 正文是 page_content（旧代码写 .content 会 AttributeError，
        # 导致每次解析都在 extract 阶段失败）。
        pages = [{"content": clean_text(d.page_content), "page": d.metadata.get("page"), "source": row.file_name} for d in docs]
        data = dict(row.parsed_data or {})
        data["source_pages"] = pages
        row.parsed_data = data
        await self.db.flush()
        return "\n".join(x["content"] for x in pages)

    async def upload_batch(self, files):
        """gxd-复用现有简历表逐份上传并解析；请求内返回结果，不创建批次或任务表。"""
        from app.config.settings import settings
        from app.config.talent_config import talent_settings
        self.context.require_permission("resume:upload")
        self.context.require_permission("resume:parse")
        accepted, rejected = [], []
        try:
            if not 1 <= len(files) <= talent_settings.batch_limit:
                raise BusinessRuleError(f"每批允许1至{talent_settings.batch_limit}个文件")
            for file in files:
                filename = file.filename or ""
                try:
                    content = await file.read(settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024 + 1)
                    async with self.db.begin_nested():
                        uploaded = await self.upload(filename, content, file.content_type)
                    # 上传已保存在外层事务；解析用自己的保存点，失败不会撤掉该文件记录。
                    parsed = await self.parse_resume(resume_id=uploaded["resume_id"])
                    accepted.append({**uploaded, "status": parsed["status"], "result": parsed})
                except BusinessException as exc:
                    rejected.append({"file_name": filename[:255], "message": exc.message})
            return {"total": len(files), "accepted": accepted, "rejected": rejected}
        finally:
            for file in files:
                await file.close()

    async def parse_resume(self, file_id=None, resume_id=None, context=None, *, force=False, talent_id=None):
        """gxd-直接执行解析并保留原Skill调用签名，解析故障保存到原有状态和错误字段。"""
        from app.infrastructure.database.session import session_scope
        from app.config.talent_config import talent_settings
        from app.ai.workflows.resume_talent_workflow import run_resume_workflow
        if self.db is None:
            async with session_scope() as db:
                return await ResumeService(db, context=context or self.context).parse_resume(
                    file_id=file_id, resume_id=resume_id, force=force, talent_id=talent_id)
        self.context.require_permission("resume:parse")
        if resume_id is None:
            rows = await self.repo.list(self.db, filters={"file_id": file_id}, extra_where=[self.repo.scope(self.context)], limit=1)
            if not rows:
                raise NotFoundError("未找到对应简历")
            resume_id = rows[0].id
        row = await self.get_resume(resume_id, lock=True)
        if talent_id:
            from app.services.talent_profile_service import TalentService
            self.context.require_permission("talent:update")
            await TalentService(self.db, context=self.context).get_talent(talent_id)
            if row.talent_id and row.talent_id != talent_id:
                raise BusinessRuleError("简历已有绑定，请使用档案合并流程")
        try:
            async with self.db.begin_nested():
                processed = await asyncio.wait_for(
                    run_resume_workflow(self.db, resume_id, self.context, force=force, talent_id=talent_id),
                    timeout=talent_settings.parse_timeout,
                )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("gxd-简历解析失败 resume_id=%s type=%s", resume_id, type(exc).__name__)
            message = exc.message if isinstance(exc, BusinessException) else "解析失败或超时，请检查模型、文件及连接后重试"
            await self.repo.update_status(self.db, resume_id, "failed", message[:512])
            processed = {"status": "failed", "message": message}
        result = await self.result(resume_id)
        result["processing_status"] = processed.get("status")
        result["refresh"] = processed.get("refresh")
        if processed.get("message"):
            result["message"] = processed["message"]
        result["skills"] = (result.get("parsed") or {}).get("skills", [])
        return result

    async def save_parsed(self, resume_id, parsed, meta):
        """gxd-保留原文并保存校验后的解析结果，记录模型与提示词版本。"""
        row = await self.get_resume(resume_id)
        data = dict(row.parsed_data or {})
        data.update(parsed=parsed, warnings=parsed.get("warnings", []), analysis_meta=meta)
        return await self.repo.update_parse_result(self.db, row.id, data)

    async def bind(self, resume_id, talent_id=None, create_talent=False):
        """gxd-显式绑定或创建档案；不会用空字段自动覆盖已有档案。"""
        from app.services.talent_profile_service import TalentService
        row = await self.get_resume(resume_id, lock=True)
        if not extract_parsed(row.parsed_data):
            raise BusinessRuleError("简历尚未完成解析")
        service = TalentService(self.db, context=self.context)
        if row.talent_id:
            if talent_id and row.talent_id != talent_id:
                raise BusinessRuleError("已绑定档案，请先通过档案合并处理")
            return await service.get_talent(row.talent_id)
        if create_talent:
            self.context.require_permission("talent:create")
            talent = await service.create_from_resume(row)
        else:
            self.context.require_permission("talent:update")
            talent = await service.get_talent(talent_id, lock=True)
        row.talent_id = talent.id
        talent.version += 1
        await self.db.flush()
        self.last_refresh = await service.refresh_derived(talent.id)
        return talent

    async def correct_result(self, resume_id, data):
        """gxd-保存人工确认数据并标记来源，已绑定档案直接刷新但不覆盖主档字段。"""
        self.context.require_permission("talent:update")
        row = await self.get_resume(resume_id, lock=True)
        if not extract_parsed(row.parsed_data):
            raise BusinessRuleError("请先完成简历解析")
        await self.save_parsed(resume_id, data.model_dump(mode="json"), {"source":"user_confirmed", "user_id":self.context.user_id})
        if row.talent_id:
            from app.services.talent_profile_service import TalentService
            service = TalentService(self.db, context=self.context)
            obj = await service.get_talent(row.talent_id, lock=True)
            obj.version += 1
            self.last_refresh = await service.refresh_derived(obj.id)
        result = await self.result(resume_id)
        result["refresh"] = self.last_refresh
        return result

    async def dedupe_resume(self, resume_id=None, text=None, threshold=0.92, context=None):
        """gxd-兼容简历查重Skill，复用人才权限和向量检索，返回候选而非自动合并。"""
        from app.infrastructure.database.session import session_scope
        from app.services.talent_profile_service import TalentService
        from app.services.talent_retrieval_service import TalentRetrievalService
        from app.schemas.talent import TalentSemanticSearchRequest
        self.context.require_permission("resume:parse")
        if self.db is None:
            async with session_scope() as db:
                return await ResumeService(db, context=context or self.context).dedupe_resume(resume_id, text, threshold)
        if not 0 <= threshold <= 1:
            raise BusinessRuleError("相似度阈值必须在0至1之间")
        if resume_id:
            row = await self.get_resume(resume_id)
            parsed = extract_parsed(row.parsed_data) or None
            if not parsed:
                raise BusinessRuleError("请先解析简历")
            matches = await TalentService(self.db, context=self.context).duplicate_candidates(parsed, threshold=threshold)
            matches = [x for x in matches if x.get("talent_id") != row.talent_id or x.get("talent_id") is None]
        else:
            result = await TalentRetrievalService(self.db, context=self.context).search(TalentSemanticSearchRequest(query=text or "", top_k=10), interpret=False)
            matches = [x for x in result["items"] if x["score"] >= threshold]
        return {"duplicated":bool(matches), "matches":matches, "threshold":threshold, "requires_review":bool(matches)}

    async def prepare_parse(self, resume_id, force=False):
        """gxd-锁定简历并判断是否复用已完成结果，工作流不直接操作ORM。"""
        self.context.require_permission("resume:parse")
        row = await self.get_resume(resume_id, lock=True)
        if row.status == "success" and not force and row.talent_id:
            return {"resume_id":row.id, "talent_id":row.talent_id, "status":"succeeded", "reused":True}
        row.status = "parsing"
        await self.db.flush()
        return None

    async def finalize_parse(self, resume_id, parsed, meta, talent_id=None):
        """gxd-保存解析、查询重复、创建或绑定档案，并将冲突留待明确确认。"""
        from app.services.talent_profile_service import TalentService
        talents = TalentService(self.db, context=self.context)
        row = await self.save_parsed(resume_id, parsed, meta)
        candidates = await talents.duplicate_candidates(parsed)
        row.parsed_data = {**row.parsed_data, "duplicates":candidates}
        result = {"resume_id":row.id, "status":"needs_review", "duplicates":candidates, "warnings":parsed.get("warnings", [])}
        if row.talent_id:
            if talent_id and talent_id != row.talent_id:
                raise BusinessRuleError("简历已有绑定，请使用档案合并流程")
            target = await talents.get_talent(row.talent_id, lock=True)
            target.version += 1
            self.last_refresh = await talents.refresh_derived(target.id)
            result.update(talent_id=target.id, status="succeeded")
        elif talent_id:
            target = await self.bind(row.id, talent_id=talent_id)
            result.update(talent_id=target.id, status="succeeded")
        elif not candidates and parsed["basic_info"].get("name"):
            target = await self.bind(row.id, create_talent=True)
            result.update(talent_id=target.id, status="succeeded")
        else:
            result["message"] = "存在重复候选或缺少必要字段，请核对后绑定档案；不会自动覆盖冲突信息"
        result["refresh"] = self.last_refresh
        await self.db.flush()
        return result
