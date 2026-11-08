"""
脚本：scripts/verify_rag_resume_ingest.py
职责：本地端到端验收「RAG 上传 -> 人才自动入库」链路（无需外部 LLM / MySQL / Milvus 服务）。

标签：[RAG组] [P0]（上传自动入库验收脚本）

============================ 验收口径（一次跑完，不点任何按钮） ============================
    1. revision_id -> source_file_id -> resume_id -> talent_id 全部可解析；
    2. 同一 talent_id 在 MySQL（此处用 SQLite 代跑同一套 ORM）有画像行、
       在 Milvus（Milvus Lite 单文件）有一条复合画像向量；
    3. 再上传字节完全相同的文件 -> 复用既有简历与人才，不重复建模；
    4. 疑似重复（手机号命中已有档案）-> 进入待复核，不自动合并。

外部依赖的替代方式（只在验收脚本里使用，业务代码不变）：
    - LLM 简历结构化：用固定结构代替（避免联网与随机性）；
    - 画像 Agent：写入一条测试画像（维度/标签结构真实）；
    - Embedding：用确定性哈希向量（维度取 settings.EMBEDDING_DIM）。

使用：
    .venv/bin/python scripts/verify_rag_resume_ingest.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

WORKDIR = Path(tempfile.mkdtemp(prefix="rag-resume-verify-"))
os.environ.setdefault("DB_DRIVER", "sqlite")
os.environ["DB_SQLITE_FILE"] = str(WORKDIR / "verify.db")
os.environ["STORAGE_BACKEND"] = "local"
os.environ["MILVUS_ENABLED"] = "true"
# 注意：不要设置环境变量 MILVUS_URI —— pymilvus 自己会读取该变量并只接受 http(s)://，
# 本地单文件 URI 在代码里赋给 settings.MILVUS_URI 即可。
os.environ["EMBEDDING_DIM"] = "64"

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    """打印一条验收结论；失败时记录下来，最后统一退出码非 0。"""
    print(("  ✅ " if ok else "  ❌ ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        FAILURES.append(name)


def fake_vector(text: str, dim: int) -> list[float]:
    """确定性哈希向量：同文本同向量，不同文本高概率不同方向（仅用于链路验收）。"""
    seed = hashlib.sha256(text.encode("utf-8")).digest()
    raw = [b / 255.0 - 0.5 for b in seed]
    while len(raw) < dim:
        raw += raw
    raw = raw[:dim]
    norm = sum(x * x for x in raw) ** 0.5 or 1.0
    return [x / norm for x in raw]


async def patch_externals(monkeypatch_ns: SimpleNamespace) -> None:
    """替换 LLM / 画像 / Embedding 三类外部调用，其余代码全部走真实实现。"""
    import app.services.talent_retrieval_service as retrieval

    async def embed_texts(texts, *args, **kwargs):
        return [fake_vector(t, 64) for t in texts]

    async def embed_query(text, *args, **kwargs):
        return fake_vector(text, 64)

    retrieval.embed_texts = embed_texts
    retrieval.embed_query = embed_query

    # 简历结构化：固定结构，避免调外部模型
    from app.ai.agents.resume_agent import ResumeAgent

    async def fake_agent_run(self, payload):
        text = payload.get("text") or ""
        basic = {"name": "林测试", "mobile": "13800001111", "work_years": 3}
        if "第二份" in text:
            basic = {"name": "林测试", "mobile": "13800001111", "work_years": 4}
        if "重复候选" in text:
            basic = {"name": "林测试", "mobile": "13900002222", "work_years": 5}
        return {"ok": True, "result": {"basic_info": basic, "skills": ["Python"], "work_experience": []},
                "model": "verify-stub"}

    ResumeAgent.run = fake_agent_run

    # 画像 Agent：写一条真实结构的画像（维度、标签、潜力等级）
    from app.ai.agents.talent_agent import TalentAgent

    async def fake_portrait_run(self, payload):
        return {"ok": True, "model": "verify-stub", "result": {
            "summary": "3年Python后端经验，参与过数据平台建设",
            "dimensions": {"skill_depth": 0.7},
            "labels": [{"category": "quality", "name": "工程能力", "evidence": "参与数据平台建设"}],
            "strengths": ["Python"], "weaknesses": ["缺少团队管理经验"],
            "suggested_positions": ["后端工程师"], "potential_level": "B", "warnings": []}}

    TalentAgent.run = fake_portrait_run


async def main() -> int:
    print(f"工作目录：{WORKDIR}")
    from app.config.settings import settings

    settings.DB_DRIVER = "sqlite"
    settings.DB_SQLITE_FILE = str(WORKDIR / "verify.db")
    settings.STORAGE_BACKEND = "local"
    settings.MILVUS_ENABLED = True
    settings.MILVUS_URI = str(WORKDIR / "milvus.db")
    settings.EMBEDDING_DIM = 64
    # 让团队 Schema 的约定维度与本次替身 embedding 一致，避免「dim mismatch」干扰验收
    from app.config.talent_config import talent_settings

    talent_settings.embedding_dim = 64

    await patch_externals(SimpleNamespace())

    from app.adapters.vector_store.talent_vector_store import reset_client
    from app.infrastructure.database.session import get_engine, session_scope
    import app.models  # noqa: F401 - 注册全部模型后建表
    from app.models.base import Base
    from app.models.file import File
    from app.models.knowledge import KnowledgeDoc
    from app.models.knowledge_revision import KnowledgeRevision
    from app.models.organization import Organization
    from app.models.role import Role
    from app.models.user import User

    reset_client()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from app.services.file_service import FileService
    from app.services.knowledge_revision_service import KnowledgeRevisionService
    from app.services.talent_resume_service import ResumeService
    from app.ai.context import AIContext

    # ---------------------------------------------------------------- 准备账号
    async with session_scope() as db:
        org = Organization(name="验收部门", code="verify-org", path="/1/", level=1)
        db.add(org)
        await db.flush()
        role = Role(name="管理员", code="admin", description="验收用管理员角色")
        db.add(role)
        await db.flush()
        user = User(username="verify-admin", password_hash="x", real_name="验收管理员",
                    organization_id=org.id, is_active=True, is_superuser=False)
        user.roles = [role]
        db.add(user)
        await db.flush()
        user_id = user.id
        doc = KnowledgeDoc(title="林测试简历", category="resume", source_type="file",
                           status="draft", created_by=user_id)
        db.add(doc)
        await db.flush()
        doc_id = doc.id

    ctx = AIContext(user_id=user_id, roles=["admin"], organization_id=1)
    operator = SimpleNamespace(id=user_id, role_codes=["admin"], is_superuser=False,
                               organization_id=1, username="verify-admin")

    async def upload_resume(text: str, filename: str) -> dict:
        """走真实的知识库上传链路（原件 -> 版本记录，提交后返回 revision_id）。"""
        async with session_scope() as db:
            service = KnowledgeRevisionService(db)
            result = await service.upload(text.encode("utf-8"), filename, "text/plain",
                                          "resume", filename, operator, doc_id)
            await db.commit()
            return result

    async def ingest(revision_id: int) -> dict:
        """后台任务同款入口：独立会话 + 可信身份，调用人才模块。"""
        async with session_scope() as db:
            return await ResumeService(db, context=ctx).process_rag_revision(revision_id)

    # ---------------------------------------------------------------- 1) 首份简历
    print("\n[1] RAG 上传一份全新简历 -> 自动进入人才链路")
    first = await upload_resume("林测试，Python工程师，3年工作经验。", "林测试-简历.txt")
    revision_id, source_file_id = first["revision_id"], first["source_file_id"]
    check("上传返回 revision_id 与 source_file_id", bool(revision_id and source_file_id),
          f"revision={revision_id} file={source_file_id}")

    outcome = await ingest(revision_id)
    resume_id, talent_id = outcome.get("resume_id"), outcome.get("talent_id")
    check("原件 -> 简历 -> 人才 全链路可解析", bool(resume_id and talent_id),
          f"resume={resume_id} talent={talent_id}")
    check("首次处理不是复用", outcome.get("reused") is False)
    check("画像+索引均成功（complete）",
          (outcome.get("refresh") or {}).get("status") == "complete",
          json.dumps((outcome.get("refresh") or {}).get("status"), ensure_ascii=False))

    async with session_scope() as db:
        from sqlalchemy import select
        from app.models.talent import Talent, TalentPortrait
        from app.models.resume import Resume

        resume = await db.get(Resume, resume_id)
        talent = await db.get(Talent, talent_id)
        portraits = list((await db.execute(
            select(TalentPortrait).where(TalentPortrait.talent_id == talent_id))).scalars())
        file_row = await db.get(File, source_file_id)
        revision_row = await db.get(KnowledgeRevision, revision_id)
        ingest_meta = (resume.parsed_data or {}).get("ingest") or {}

    check("revision.source_file_id == 原件文件 ID", revision_row.source_file_id == source_file_id)
    check("resume.file_id 指向同一原件", resume.file_id == source_file_id and file_row.id == source_file_id)
    check("resume.talent_id 指向人才档案", resume.talent_id == talent_id)
    check("MySQL 中存在该人才的画像行", len(portraits) == 1, f"portrait_id={portraits[0].id}")
    check("简历记录里留痕了自动入库状态", ingest_meta.get("status") == "succeeded",
          json.dumps(ingest_meta, ensure_ascii=False))

    # ---------------------------------------------------------------- 2) 向量检查
    print("\n[2] 同一 talent_id 在 Milvus 有一条复合画像向量")
    from app.services.talent_retrieval_service import TalentRetrievalService

    async with session_scope() as db:
        retrieval = TalentRetrievalService(db, context=ctx)
        found = await retrieval.read_index(talent_id)
        rows = await retrieval.vector.query(
            retrieval_cfg_collection(), f"talent_id == {talent_id}",
            output_fields=["talent_id", "version", "kind"], limit=10)
        talent_version = (await db.get(Talent, talent_id)).version

    check("Milvus 中该 talent_id 恰好一条向量", len(rows) == 1, f"rows={rows}")
    check("主键就是 MySQL talent_id", bool(rows) and int(rows[0]["talent_id"]) == talent_id)
    check("向量版本与 MySQL 档案版本一致",
          bool(rows) and int(rows[0]["version"]) == int(talent_version),
          f"milvus={rows[0]['version'] if rows else None} mysql={talent_version}")
    check("读回接口与写入同一字段（indexed=True）", found.get("indexed") is True)

    # 详情接口的索引状态（前端可直接看到 complete）
    async with session_scope() as db:
        from app.services.talent_profile_service import TalentService

        detail = await TalentService(db, context=ctx).detail(talent_id)
    check("人才详情 index.status=current", detail["index"]["status"] == "current",
          json.dumps(detail["index"], ensure_ascii=False))
    check("人才详情 complete=True", detail["complete"] is True)

    # ---------------------------------------------------------------- 3) 重复上传
    print("\n[3] 再上传字节完全相同的文件 -> 复用既有档案，不重复建模")
    again = await upload_resume("林测试，Python工程师，3年工作经验。", "林测试-简历.txt")
    replay = await ingest(again["revision_id"])
    async with session_scope() as db:
        from sqlalchemy import func, select
        from app.models.resume import Resume
        from app.models.talent import Talent

        resume_count = (await db.execute(select(func.count()).select_from(Resume))).scalar_one()
        talent_count = (await db.execute(select(func.count()).select_from(Talent))).scalar_one()
    check("第二次判定为复用", replay.get("reused") is True)
    check("复用同一个 resume_id", replay.get("resume_id") == resume_id)
    check("复用同一个 talent_id", replay.get("talent_id") == talent_id)
    check("简历与人才都没有新增重复行", resume_count == 1 and talent_count == 1,
          f"resumes={resume_count} talents={talent_count}")

    # ---------------------------------------------------------------- 4) 疑似重复
    print("\n[4] 疑似重复（手机号已存在）-> 待复核，不自动合并")
    suspect = await upload_resume("林测试，第二份简历，手机号相同，需要人工确认。", "林测试-简历-2.txt")
    review = await ingest(suspect["revision_id"])
    async with session_scope() as db:
        from app.models.resume import Resume

        review_row = await db.get(Resume, review["resume_id"])
    check("疑似重复进入 needs_review", review.get("processing_status") == "needs_review",
          json.dumps(review.get("processing_status"), ensure_ascii=False))
    check("没有自动绑定人才", review.get("talent_id") is None and review_row.talent_id is None)
    check("候选重复档案与解析结果一并留存",
          bool(review.get("duplicates")) and bool((review_row.parsed_data or {}).get("duplicates")),
          f"duplicates={len(review.get('duplicates') or [])}")

    # ---------------------------------------------------------------- 5) 回查接口
    print("\n[5] 按 revision_id 回查整条链路（前端无需点任何按钮）")
    async with session_scope() as db:
        chain = await ResumeService(db, context=ctx).revision_ingest_status(revision_id)
    check("回查链路 revision -> 原件 -> 简历 -> 人才",
          (chain["source_file_id"], chain["resume_id"], chain["talent_id"]) == (source_file_id, resume_id, talent_id),
          json.dumps({k: chain[k] for k in ("source_file_id", "resume_id", "talent_id")}, ensure_ascii=False))
    check("回查结果 complete=True", chain["complete"] is True)
    check("回查结果带画像与向量状态",
          chain["portrait"]["status"] == "ready" and chain["index"]["status"] == "current",
          json.dumps({"portrait": chain["portrait"], "index": chain["index"]}, ensure_ascii=False))

    # ---------------------------------------------------------------- 6) 索引重试
    print("\n[6] 索引失败后重试：档案保留，只补索引")
    from app.adapters.vector_store.talent_vector_store import TalentVectorStore

    original_upsert = TalentVectorStore.upsert

    async def broken_upsert(self, *args, **kwargs):
        raise RuntimeError("milvus down")

    TalentVectorStore.upsert = broken_upsert
    try:
        async with session_scope() as db:
            from app.services.talent_profile_service import TalentService

            failed = await TalentService(db, context=ctx).refresh_derived(talent_id, portrait=False)
    finally:
        TalentVectorStore.upsert = original_upsert
    check("索引失败时返回 partial/failed 而非 complete",
          failed.get("status") in {"partial", "failed"}, json.dumps(failed.get("status"), ensure_ascii=False))
    check("索引失败给出可重试提示", bool(failed.get("message")), failed.get("message", ""))

    # 模拟档案被编辑：版本 +1 后向量落后，属于「只补索引」的场景
    async with session_scope() as db:
        from sqlalchemy import func, select
        from app.models.talent import Talent, TalentPortrait

        talent = await db.get(Talent, talent_id)
        talent.version += 1
        stale_version = talent.version
        await db.flush()

    async with session_scope() as db:
        from app.services.talent_profile_service import TalentService

        retried = await TalentService(db, context=ctx).ensure_derived(talent_id)
        portraits_after = (await db.execute(
            select(func.count()).select_from(TalentPortrait)
            .where(TalentPortrait.talent_id == talent_id))).scalar_one()
    check("重试只补索引（actions 仅 index）", retried.get("actions") == ["index"],
          json.dumps(retried.get("actions"), ensure_ascii=False))
    check("重试没有重新生成画像", portraits_after == 1, f"portraits={portraits_after}")
    check("重试后恢复 complete", retried.get("status") == "complete")
    check("重试后向量版本追上档案版本",
          int(retried["index"].get("version") or 0) == int(stale_version),
          f"index={retried['index'].get('version')} talent={stale_version}")

    # ---------------------------------------------------------------- 收尾
    print("\n" + "=" * 70)
    if FAILURES:
        print(f"❌ 验收未通过：{len(FAILURES)} 项")
        for item in FAILURES:
            print("   - " + item)
        return 1
    print("✅ 全部验收通过")
    return 0


def retrieval_cfg_collection() -> str:
    from app.config.talent_config import talent_settings

    return talent_settings.collection


if __name__ == "__main__":
    try:
        code = asyncio.run(main())
    finally:
        if os.getenv("KEEP_VERIFY_WORKDIR") != "1":
            shutil.rmtree(WORKDIR, ignore_errors=True)
    raise SystemExit(code)
