"""
模块：app/ai/proctoring/__init__.py
职责：笔试监考包的对外出口（检测器 + 违规状态机）。

标签：[Agent组] [P1]（监考检测器与违规状态机统一出口）

为什么需要这个文件：
    `.gitignore` 里的 `_*.py` 规则本意是忽略「本地调试用的 _xxx.py 临时脚本」，
    但它同时匹配到了所有 `__init__.py`。历史上人们用 `git add -f` 逐个手工加回，
    结果本包（app/ai/proctoring）的 `__init__.py` 一直没被提交：
    干净克隆里 `import app.ai.proctoring` 直接抛
        ImportError: cannot import name 'evaluate_proctor' from 'app.ai.proctoring'
    应用连启动都做不到。

    `.gitignore` 已在本轮修复（`__init__.py` 不再被 `_*.py` 误伤），
    本文件仅做显式导出，不改变 detector.py 的任何行为。
"""

from __future__ import annotations

from app.ai.proctoring.detector import (
    BaseProctorDetector,
    Detection,
    HaarFaceDetector,
    NullDetector,
    YoloDetector,
    evaluate_proctor,
    frame_luma_stats,
    get_detector,
)

__all__ = [
    "BaseProctorDetector",
    "Detection",
    "HaarFaceDetector",
    "NullDetector",
    "YoloDetector",
    "evaluate_proctor",
    "frame_luma_stats",
    "get_detector",
]
