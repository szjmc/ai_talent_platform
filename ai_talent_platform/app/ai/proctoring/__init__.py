"""考试监考视觉检测（新增包）。

标签：[Agent组] [P1]

YOLO(.onnx) 主路径 + OpenCV Haar 人脸回退 + 禁用降级，
导入与选型见 get_detector()；违规状态机为纯函数 evaluate_proctor()，离线可测。
"""

from app.ai.proctoring.detector import (
    Detection,
    BaseProctorDetector,
    evaluate_proctor,
    frame_luma_stats,
    get_detector,
)

__all__ = [
    "Detection",
    "BaseProctorDetector",
    "evaluate_proctor",
    "frame_luma_stats",
    "get_detector",
]
