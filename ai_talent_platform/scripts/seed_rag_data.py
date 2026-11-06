"""为智能问答接口（POST /api/v1/text2answer）灌入可检索的调试知识数据。

标签：[RAG组] [P1]（智能问答调试语料工具）

走真实入库链路 KnowledgeService.upload：写文件对象 -> 解析分块 -> 向量化(bge-m3)
-> 写 Milvus(rag_knowledge_chunks) -> 回写 knowledge_docs / knowledge_chunks，
与 smoke 脚本不同，本脚本 **提交事务**，数据持久保留供接口调试。

前置：
    1. .env 已配置（ollama + 本地存储 + MILVUS_ENABLED=true）
    2. python scripts/init_db.py 已建库建表
    3. python scripts/seed_data.py 已建账号（默认用 admin 作为入库人）
    4. python scripts/init_rag_collection.py 已建向量集合

用法：
    python scripts/seed_rag_data.py            # 灌入全部样例文档（幂等，重复执行按内容去重）
    python scripts/seed_rag_data.py --user hr01  # 指定入库人用户名

标签：[RAG组] [P2]（灌入 RAG 样例数据的手工脚本）
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("USERNAME", "Windows")
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import select  # noqa: E402

from app.config.logging_config import setup_logging  # noqa: E402
from app.infrastructure.database.session import close_db, get_session_factory  # noqa: E402
from app.infrastructure.vector.milvus import close_milvus  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.knowledge_service import KnowledgeService  # noqa: E402

# ---------------------------------------------------------------- 样例语料
# 每篇文档聚焦一个主题，含可被问答验证的具体事实（数字 / 流程 / 标准）。
# category 取 app/core/rag_constants.py 的合法英文 slug；文档默认 permission_scope=public，
# 任何登录用户都能通过 text2answer 检索到。
DOCUMENTS: list[dict[str, str]] = [
    {
        "filename": "员工培训管理制度.txt",
        "category": "policy",
        "content": (
            "员工培训管理制度\n"
            "一、培训申请流程：员工填写《培训申请表》，经直属主管审批后提交人力资源部备案；"
            "外部付费培训需提前 10 个工作日申请，单次费用超过 5000 元的须报分管副总审批。\n"
            "二、培训报销标准：培训费凭正规发票据实报销，住宿费按每晚不超过 400 元标准执行，"
            "市内交通补贴每天 80 元，餐费补贴每天 100 元。报销须在培训结束后 30 天内提交。\n"
            "三、学时要求：每位员工每年须完成不少于 40 学时的培训，其中专业技术类不少于 24 学时，"
            "通用素质类不少于 16 学时。未完成年度学时的，当年绩效不得评为 A 级。\n"
            "四、服务期约定：公司出资超过 10000 元的外部培训，员工须签订服务期协议，"
            "服务期通常为 2 年；服务期内离职须按比例退还培训费用。\n"
            "五、内部讲师：鼓励资深员工担任内部讲师，授课学时按每小时 200 元发放课酬，"
            "并计入个人年度培训学时。"
        ),
    },
    {
        "filename": "绩效考核与晋升管理制度.txt",
        "category": "policy",
        "content": (
            "绩效考核与晋升管理制度\n"
            "一、绩效周期：公司实行季度考核与年度考核相结合的制度。季度考核占年度总评的 60%，"
            "年度考核占 40%。\n"
            "二、绩效等级：分为 A（优秀）、B（良好）、C（合格）、D（待改进）四档，"
            "原则上 A 级不超过 20%，B 级约 40%，C 级约 35%，D 级约 5%。\n"
            "三、晋升条件：员工晋升须同时满足——现岗位任职满 1 年、最近两次绩效考核均为 B 级及以上、"
            "无重大违规记录、完成对应层级的能力认证。\n"
            "四、调薪规则：年度调薪窗口为每年 4 月，A 级调薪幅度 15%~20%，B 级 8%~12%，"
            "C 级 3%~5%，D 级不调薪并进入绩效改进计划（PIP）。\n"
            "五、职级体系：技术序列分为 T1 助理工程师、T2 工程师、T3 高级工程师、"
            "T4 资深工程师、T5 技术专家、T6 首席工程师；管理序列分为 M1 主管、M2 经理、M3 总监、M4 副总。\n"
            "六、连续两次考核为 D 级的员工，公司有权调整岗位或依法解除劳动合同。"
        ),
    },
    {
        "filename": "Python后端开发培训课件.txt",
        "category": "training",
        "content": (
            "Python 后端开发培训课件\n"
            "第一讲 FastAPI 基础：FastAPI 是基于 Starlette 和 Pydantic 的现代异步 Web 框架，"
            "支持类型注解驱动的请求校验与自动生成 OpenAPI 文档。路由用 @app.get/@app.post 装饰器声明，"
            "依赖注入通过 Depends 实现，异步接口用 async def 定义。\n"
            "第二讲 异步编程：asyncio 事件循环是并发核心，I/O 密集操作用 await 挂起而非阻塞线程；"
            "数据库访问使用异步驱动（如 aiomysql、asyncpg），避免在协程中调用同步阻塞函数，"
            "必要时用 asyncio.to_thread 把 CPU 密集或阻塞调用放到线程池。\n"
            "第三讲 数据库与 ORM：SQLAlchemy 2.0 推荐全异步会话 AsyncSession，"
            "使用 select() 构造查询、_mapped_ 列做对象映射，事务通过 async with session.begin() 管理，"
            "严禁在业务代码中拼接裸 SQL 以防注入。\n"
            "第四讲 测试与质量：单元测试用 pytest，异步用例用 pytest-asyncio；"
            "接口测试覆盖正常路径与边界（参数校验、权限、异常）；"
            "代码风格遵循 PEP8，类型注解覆盖率要求不低于 80%。\n"
            "第五讲 部署：使用 uvicorn 运行 ASGI 应用，生产环境配合 gunicorn 多 worker，"
            "容器化部署时用 docker compose 编排 MySQL、Redis 等依赖服务。"
        ),
    },
    {
        "filename": "新员工入职培训手册.txt",
        "category": "training",
        "content": (
            "新员工入职培训手册\n"
            "一、入职首日流程：上午 9:00 到人力资源部报到，签署劳动合同与保密协议，"
            "领取工牌与办公设备；10:30 由 IT 部门开通企业邮箱、OA 账号和代码仓库权限；"
            "下午由直属主管介绍团队成员并指定入职导师（buddy）。\n"
            "二、试用期：试用期一般为 3 个月，最长不超过 6 个月；试用期内须完成入职培训全部课程，"
            "并在导师指导下产出至少一个可交付任务，试用期结束前进行转正答辩。\n"
            "三、企业文化：公司倡导“客户第一、坦诚沟通、持续学习、结果导向”四项核心价值观，"
            "每周五下午设有技术分享会，鼓励跨团队交流。\n"
            "四、常用工具：代码托管使用 GitLab，项目协作使用 Jira，文档沉淀使用 Confluence，"
            "即时沟通使用企业微信，CI/CD 使用 Jenkins。\n"
            "五、考勤与假期：实行弹性工作制，核心工作时间为 10:00~16:00；"
            "入职满一年享有 5 天带薪年假，司龄每增加一年年假增加 1 天，上限 15 天。"
        ),
    },
    {
        "filename": "智能招聘系统项目案例.txt",
        "category": "case",
        "content": (
            "智能招聘系统项目案例\n"
            "一、项目背景：集团每年收到简历超过 8 万份，传统人工筛选效率低、标准不统一，"
            "HR 团队希望借助 AI 实现简历自动解析与人岗智能匹配。\n"
            "二、技术栈：后端采用 FastAPI 全异步架构，业务主库为 MySQL 8，"
            "海量履历与分析宽表存放于 PostgreSQL，向量检索使用 Milvus，"
            "缓存使用 Redis，AI 编排基于 LangChain 与 LangGraph，大模型接入通义千问。\n"
            "三、核心能力：简历 OCR 解析与技能抽取、岗位画像建模、"
            "基于向量相似度的人岗匹配打分（匹配维度含技能、经验、学历、潜力），"
            "以及面向 HR 的智能问答助手。\n"
            "四、项目成果：上线 6 个月后，简历初筛人工工时下降约 55%，"
            "人岗匹配准确率提升到 87%，平均招聘周期从 42 天缩短到 28 天，"
            "系统日均处理简历约 1200 份。\n"
            "五、经验教训：早期低估了简历格式多样性，OCR 与解析规则迭代了三个版本；"
            "向量维度与嵌入模型必须全链路统一，否则召回质量明显下降。"
        ),
    },
    {
        "filename": "后端工程师人才培养方案.txt",
        "category": "development_plan",
        "content": (
            "后端工程师人才培养方案\n"
            "一、能力分级模型：\n"
            "T1 助理工程师——掌握 Python 基础语法与常用数据结构，能在导师指导下完成简单接口开发；\n"
            "T2 工程师——熟练使用 FastAPI 与 SQLAlchemy，独立完成模块开发、单元测试与联调；\n"
            "T3 高级工程师——具备异步编程、数据库优化、缓存设计能力，可负责子系统技术方案；\n"
            "T4 资深工程师——能主导跨模块架构设计，处理高并发与稳定性问题，指导初中级工程师；\n"
            "T5 技术专家——在某一技术域（如 AI 工程化、分布式存储）具备深度，主导技术选型与攻关。\n"
            "二、培养路径：新人入职后 6 个月内完成 T1→T2 认证，12~18 个月达到 T3，"
            "T3 以上每年提交一份个人发展计划（IDP），由主管与 HR 共同评审。\n"
            "三、培养方式：采用“70-20-10”法则——70% 来自项目实战，20% 来自导师辅导与同行评审，"
            "10% 来自课程培训与技术书籍阅读。\n"
            "四、认证要求：晋级 T3 须通过系统设计面试与一次线上故障复盘答辩；"
            "晋级 T4 须主导过至少一个中型项目的后端架构。\n"
            "五、激励：技术序列与管理序列双通道发展，T5 及以上享受专项技术津贴与年度培训基金 20000 元。"
        ),
    },
    {
        "filename": "高级后端工程师岗位说明书.txt",
        "category": "job_description",
        "content": (
            "高级后端工程师岗位说明书\n"
            "一、岗位名称：高级后端工程师（职级 T3~T4），所属部门：研发中心-平台后端组，"
            "汇报对象：后端技术经理。\n"
            "二、岗位职责：负责 AI 人才平台核心服务的架构设计与开发，"
            "包括异步 API、检索增强问答（RAG）、人岗匹配引擎；"
            "参与数据库设计与性能调优，保障系统高可用与可扩展性；"
            "指导初中级工程师，主导代码评审与技术文档沉淀。\n"
            "三、任职要求：计算机相关专业本科及以上学历，5 年以上后端开发经验；"
            "精通 Python，熟练使用 FastAPI、SQLAlchemy 2.0 异步 ORM；"
            "熟悉 MySQL 与 PostgreSQL，了解 Redis 缓存与 Milvus 向量检索；"
            "具备 LangChain / LangGraph 或大模型应用开发经验者优先。\n"
            "四、技能标签：Python、FastAPI、异步编程、MySQL、PostgreSQL、Redis、Milvus、"
            "LangChain、RAG、Docker、系统设计。\n"
            "五、薪资范围：月薪 30K~50K，14 薪，另设项目奖金与年度股票期权。"
        ),
    },
]


async def _resolve_user(db, username: str | None) -> User:
    """取入库人：优先按用户名，其次任一超级管理员，再退回任一启用账号。"""
    if username:
        user = await db.scalar(select(User).where(User.username == username, User.is_deleted.is_(False)))
        if user is None:
            raise SystemExit(f"未找到用户 {username}，请先运行 python scripts/seed_data.py")
        return user
    user = await db.scalar(select(User).where(User.is_superuser.is_(True), User.is_deleted.is_(False)).limit(1))
    if user is None:
        user = await db.scalar(select(User).where(User.is_deleted.is_(False)).limit(1))
    if user is None:
        raise SystemExit("数据库中没有任何用户，请先运行 python scripts/init_db.py 与 python scripts/seed_data.py")
    return user


async def main(username: str | None) -> int:
    factory = get_session_factory()
    indexed, failed = 0, 0
    try:
        async with factory() as db:
            user = await _resolve_user(db, username)
            print(f"入库人：{user.username}(id={user.id}, superuser={user.is_superuser})")
            service = KnowledgeService(db)
            for item in DOCUMENTS:
                data = item["content"].encode("utf-8")
                result = await service.upload(data, item["filename"], "text/plain", item["category"], user)
                status = result["status"]
                flag = "OK " if status == "indexed" else "FAIL"
                if status == "indexed":
                    indexed += 1
                else:
                    failed += 1
                print(
                    f"  [{flag}] {item['category']:<16} {item['filename']}"
                    f"  doc_id={result['id']} chunks={result['chunk_count']} status={status}"
                    + (f" err={result['index_error']}" if result.get("index_error") else "")
                )
            await db.commit()
            print(f"\n提交完成：成功 {indexed} 篇，失败 {failed} 篇。")
    except Exception:
        # 入库失败时回滚 MySQL；已写入的向量由 KnowledgeService 的保存点/补偿逻辑处理。
        print("发生异常，事务未提交。")
        raise
    finally:
        await close_db()
        await close_milvus()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    setup_logging()
    parser = argparse.ArgumentParser(description="灌入智能问答调试知识数据")
    parser.add_argument("--user", default=None, help="入库人用户名（默认任一超级管理员）")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.user)))
