"""
模块：app/ai/proctoring/__init__.py
职责：在线监考能力出口（YOLO / Haar / disabled 多级降级检测器）。

标签：[Agent组] [P1]（在线笔试监考：目标检测与人脸回退）
"""

from app.ai.proctoring.detector import evaluate_proctor, frame_luma_stats, get_detector

__all__ = ["evaluate_proctor", "frame_luma_stats", "get_detector"]
