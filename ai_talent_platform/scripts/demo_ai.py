"""一键演示 AI 能力链路（架构组交付物，5 条链路）。

标签：[架构组] [P2]

默认离线运行：只跑不依赖大模型/向量库的链路，依赖未就绪的能力会打印「依赖未就绪」，
方便在本地无 Key、无 Milvus 的环境验收架构组代码。

    python scripts/demo_ai.py                # 离线演示（默认）
    python scripts/demo_ai.py --with-llm     # 额外跑一次真实大模型调用（需 LLM_API_KEY）

演示链路：
    1. Skills 注册与清单导出（架构组）
    2. 人岗匹配打分（纯函数算法，无外部依赖）
    3. 简历技能抽取（离线词典）
    4. NL2SQL 问数（依赖 Vanna 组，未就绪时报错提示）
    5. 对话记忆 / 工作流状态 / 事件总线 / 文件存储（架构组公共能力）
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.ai.context import clear_context, set_context  # noqa: E402
from app.config.logging_config import setup_logging  # noqa: E402
from app.config.settings import settings  # noqa: E402


def _title(text: str) -> None:
    print(f"\n{'=' * 60}\n{text}\n{'=' * 60}")


async def demo_skills() -> None:
    from app.ai.skills import bootstrap_skills, describe_skills, list_tools

    names = bootstrap_skills()
    print(f"已注册技能（{len(names)}）：{', '.join(names)}")
    print(f"清单项示例：{describe_skills()[0]['name']} - {describe_skills()[0]['description']}")
    print(f"function-calling 工具数：{len(list_tools())}")


async def demo_matching() -> None:
    from app.ai.skills import compute_match_score

    talent = {"id": 1, "name": "张三", "skill_tags": ["Python", "FastAPI", "MySQL"], "work_years": 5, "education": "本科", "potential_level": "A"}
    position = {"id": 2, "title": "后端工程师", "skill_tags": ["Python", "FastAPI"], "requirements": {"min_work_years": 3, "min_education": "本科"}}
    result = compute_match_score(talent, position)
    print(f"人才：{talent['name']}　岗位：{position['title']}")
    print(f"总分：{result['total_score']}　维度：{result['dimensions']}")
    print(f"亮点：{result['highlights']}　短板：{result['gaps']}")


async def demo_resume() -> None:
    from app.ai.skills import extract_skills_by_dictionary

    text = "熟练使用 Python、FastAPI 与 MySQL，熟悉 Docker 与 Kubernetes，具备项目管理经验"
    print(f"简历片段：{text}")
    print(f"抽取技能：{extract_skills_by_dictionary(text)}")


async def demo_nl2sql() -> None:
    from app.ai.skills import get_skill

    result = await get_skill("sql_query").run(question="上个月各部门新增人才数量")
    if result.ok:
        print(f"SQL：{result.data.get('sql')}\n行数：{result.data.get('row_count')}")
    else:
        print(f"预期内失败（Vanna 组未实现）：{result.error}")


async def demo_platform() -> None:
    from app.ai.memory.conversation_memory import ConversationMemory
    from app.ai.memory.state_manager import state_manager
    from app.infrastructure.messaging.event_bus import EVENT_TALENT_CREATED, event_bus
    from app.infrastructure.storage import file_storage
    from app.patterns.strategy import MatchStrategyFactory

    memory = ConversationMemory(conversation_id="demo", window=4)
    await memory.append({"role": "user", "content": "帮我看看张三的匹配度"})
    await memory.append({"role": "assistant", "content": "张三与后端工程师匹配度 92 分"})
    print(f"对话记忆：{[item['role'] for item in await memory.get_recent()]}")

    await state_manager.save("demo-thread", {"step": "parsed", "resume_id": 1})
    print(f"工作流状态：{await state_manager.load('demo-thread')}")

    received: list[Any] = []
    event_bus.subscribe(EVENT_TALENT_CREATED, lambda payload: received.append(payload))
    await event_bus.publish_await(EVENT_TALENT_CREATED, {"talent_id": 1})
    print(f"事件总线：收到 {len(received)} 条事件")

    path = file_storage.build_path("demo", "demo.txt")
    file_storage.write_bytes(path, b"hello ai talent platform")
    print(f"文件存储：{file_storage.relative_path(path)}（{file_storage.get_size(path)} 字节）")
    file_storage.remove(path)

    strategy = MatchStrategyFactory.create("hybrid")
    print(f"匹配策略：{strategy.name}　可用策略：{MatchStrategyFactory.available()}")


async def demo_llm() -> None:
    from app.ai.core.llm_manager import llm_manager

    print(f"LLM 状态：{llm_manager.status()}")
    try:
        result = await llm_manager.generate("用一句话介绍你自己", purpose="chat")
        print(f"模型输出：{result.content[:200]}")
    except Exception as exc:  # noqa: BLE001
        print(f"真实调用失败（检查 LLM_API_KEY / 网络）：{exc}")


async def main_async(with_llm: bool) -> None:
    # 演示用上下文（真实链路里由 Router / AgentService 调用 set_context 注入）
    set_context({"user_id": 1, "username": "demo", "roles": ["hr"], "organization_id": 1})
    _title("1/5 Skills 注册与清单（架构组）")
    await demo_skills()
    _title("2/5 人岗匹配打分（纯函数）")
    await demo_matching()
    _title("3/5 简历技能抽取（离线词典）")
    await demo_resume()
    _title("4/5 NL2SQL 问数（依赖 Vanna 组）")
    await demo_nl2sql()
    _title("5/5 记忆 / 状态 / 事件 / 存储 / 策略")
    await demo_platform()
    if with_llm:
        _title("附加：真实大模型调用")
        await demo_llm()
    clear_context()
    _title("演示结束（依赖未就绪项属预期，见 docs/已实现功能清单.md）")


def main() -> None:
    parser = argparse.ArgumentParser(description="一键演示 AI 能力链路")
    parser.add_argument("--with-llm", action="store_true", help="额外执行一次真实大模型调用")
    args = parser.parse_args()
    setup_logging()
    print(f"环境：LLM={settings.LLM_PROVIDER}/{settings.LLM_MODEL}　Milvus={settings.MILVUS_ENABLED}　Redis={settings.REDIS_ENABLED}　PgSQL={settings.PGSQL_ENABLED}")
    asyncio.run(main_async(with_llm=args.with_llm))


if __name__ == "__main__":
    main()
