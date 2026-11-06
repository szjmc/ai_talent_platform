"""
测试硅基流动 LLM 是否正常调用。
用法：python scripts/test_llm.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai.core.llm_manager import llm_manager
from app.config.settings import settings


async def main():
    print(f"当前 LLM_PROVIDER = {settings.LLM_PROVIDER}")
    print(f"当前 LLM_MODEL     = {settings.LLM_MODEL}")
    print(f"当前 BASE_URL      = {settings.LLM_BASE_URL}")
    print(f"API_KEY 是否已填   = {'是' if settings.LLM_API_KEY and 'sk-' in settings.LLM_API_KEY else '否（仍是占位）'}")
    print("-" * 50)

    try:
        result = await llm_manager.chat(
            [{"role": "user", "content": "你好，请用一句话介绍你自己。"}],
            purpose="test", temperature=0.1, max_tokens=100,
        )
        print("✅ LLM 调用成功！")
        print(f"模型: {result.model}")
        print(f"回复: {result.content}")
        print(f"耗时: {result.elapsed_ms}ms")
    except Exception as e:
        print("❌ LLM 调用失败：")
        print(f"   {type(e).__name__}: {e}")
        print("排查：1).env 的 LLM_API_KEY 是否填了真实 key  2).网络能否访问 api.siliconflow.cn")


if __name__ == "__main__":
    asyncio.run(main())
