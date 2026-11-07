"""测评 Agent 数据完整性演示入口。

标签：[Agent组] [P2]

旧版脚本用内置试卷/分数模拟生产链路，已废弃。现在直接运行离线回归，
验证数据库事实来源、权限、评分范围、持久化失败与通知不伪成功等契约。
真实 MySQL/Dify/LLM 端到端验收请使用 scripts/smoke_assessment_integration.py。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> None:
    import pytest

    raise SystemExit(pytest.main(["-q", str(ROOT / "tests" / "test_business_data_integrity.py")]))


if __name__ == "__main__":
    main()
