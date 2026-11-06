"""笔试监考：YOLO 目标检测主路径 + OpenCV 人脸回退 + 禁用降级 + 违规状态机。

标签：[Agent组] [P1]

引擎选型（get_detector，进程级单例，导入失败全部优雅降级）：
1. settings.PROCTOR_YOLO_MODEL 指向的 .onnx 存在，且 onnxruntime 可导入
   -> YoloDetector：yolov8n.onnx（COCO 预训练），检出 person / cell phone /
      laptop / book 等目标；
2. 否则 opencv-python(-headless) 可导入 -> HaarFaceDetector：人脸计数回退
   （多张人脸≈多人；无法识别人体姿态与违规物品）；
3. 均不可用 -> NullDetector：enabled=False，监考接口照常工作，
   返回 engine="disabled"，前端仅提示未启用（不阻塞考试）。

不引入 torch/ultralytics/opencv 硬依赖：requirements 中 opencv-python-headless
为可选增强（人脸回退），YOLO 权重为环境配置（模型文件不入库）。

违规判定 evaluate_proctor(state, detections, luma, ts) 为**纯状态机**：
    - no_face：连续 no_face_grace 帧无人（重置后再犯可再次记账）
    - multi_person：检出 >1 人
    - camera_blocked：画面亮度异常（近纯黑/近纯白/近无方差，疑似遮挡）
    - phone / laptop / book：仅 YOLO 引擎（Haar 回退检出不了物品）
每类违规用 flagged 位防刷屏：触发一次记一条，恢复后再次触发才再记。
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 仅关注的 COCO 类别 -> 违规类型
_VIOLATION_LABELS: dict[str, str] = {
    "cell phone": "phone",
    "phone": "phone",
    "laptop": "laptop",
    "book": "book",
}

# COCO 80 类（yolov8n 标准顺序）
COCO_CLASSES: list[str] = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
]


@dataclass(frozen=True)
class Detection:
    """单个检出目标。"""

    label: str
    conf: float
    box: tuple[float, float, float, float]  # x1,y1,x2,y2（原图像素）


class BaseProctorDetector:
    """检测器接口。"""

    engine: str = "disabled"
    enabled: bool = False

    def detect(self, image_bytes: bytes) -> list[Detection]:  # pragma: no cover - 接口
        raise NotImplementedError


class NullDetector(BaseProctorDetector):
    """不可用降级：不检测，仅记录审计。"""

    engine = "disabled"
    enabled = False

    def detect(self, image_bytes: bytes) -> list[Detection]:
        return []


class HaarFaceDetector(BaseProctorDetector):
    """OpenCV Haar 人脸回退：只支持人数（人脸）估计。"""

    engine = "haar_face"
    enabled = True

    def __init__(self) -> None:
        import cv2  # noqa: PLC0415 —— 惰性导入（可选依赖）

        cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        self._cv2 = cv2
        self._cascade = cv2.CascadeClassifier(str(cascade_path))
        if self._cascade.empty():  # pragma: no cover - 安装异常时降级
            raise RuntimeError(f"haar cascade 加载失败: {cascade_path}")

    def detect(self, image_bytes: bytes) -> list[Detection]:
        import numpy as np  # noqa: PLC0415

        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = self._cv2.imdecode(arr, self._cv2.IMREAD_COLOR)
        if img is None:
            return []
        gray = self._cv2.cvtColor(img, self._cv2.COLOR_BGR2GRAY)
        faces = self._cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(48, 48))
        return [
            Detection(label="face", conf=1.0, box=(float(x), float(y), float(x + w), float(y + h)))
            for (x, y, w, h) in faces
        ]


class YoloDetector(BaseProctorDetector):
    """yolov8n.onnx（COCO 预训练）目标检测：onnxruntime 推理，PIL 解码，numpy 后处理。"""

    engine = "yolo_onnx"
    enabled = True

    def __init__(self, model_path: Path, *, conf_threshold: float = 0.35, iou_threshold: float = 0.45) -> None:
        import numpy as np  # noqa: PLC0415
        import onnxruntime as ort  # noqa: PLC0415

        self._np = np
        self._conf = conf_threshold
        self._iou = iou_threshold
        self._input_size = 640
        self._session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self._input_name = self._session.get_inputs()[0].name

    # ------------------------------------------------------------------ 预处理
    def _preprocess(self, image_bytes: bytes) -> tuple[Any, float, float]:
        """letterbox 到 640x640，返回 (NCHW float32, scale, (pad_x, pad_y))。"""
        from PIL import Image  # noqa: PLC0415

        np = self._np
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        w, h = img.size
        scale = min(self._input_size / w, self._input_size / h)
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        resized = img.resize((nw, nh), Image.BILINEAR)
        canvas = Image.new("RGB", (self._input_size, self._input_size), (114, 114, 114))
        pad_x = (self._input_size - nw) // 2
        pad_y = (self._input_size - nh) // 2
        canvas.paste(resized, (pad_x, pad_y))
        arr = np.asarray(canvas, dtype=np.float32) / 255.0
        chw = arr.transpose(2, 0, 1)[None]  # 1x3x640x640
        return chw, scale, (float(pad_x), float(pad_y))

    # ------------------------------------------------------------------ 后处理
    def _nms(self, boxes: Any, scores: Any) -> list[int]:
        np = self._np
        x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        areas = (x2 - x1).clip(min=0) * (y2 - y1).clip(min=0)
        order = scores.argsort()[::-1]
        keep: list[int] = []
        while order.size > 0:
            i = int(order[0])
            keep.append(i)
            if order.size == 1:
                break
            rest = order[1:]
            xx1 = np.maximum(x1[i], x1[rest])
            yy1 = np.maximum(y1[i], y1[rest])
            xx2 = np.minimum(x2[i], x2[rest])
            yy2 = np.minimum(y2[i], y2[rest])
            inter = (xx2 - xx1).clip(min=0) * (yy2 - yy1).clip(min=0)
            iou = inter / (areas[i] + areas[rest] - inter + 1e-9)
            order = rest[iou <= self._iou]
        return keep

    def detect(self, image_bytes: bytes) -> list[Detection]:
        np = self._np
        try:
            inp, scale, (pad_x, pad_y) = self._preprocess(image_bytes)
            raw = self._session.run(None, {self._input_name: inp})[0]  # (1, 84, 8400)
        except Exception as exc:  # pragma: no cover - 解码/推理异常不阻断考试
            logger.warning("yolo 推理失败(降级为无人检出): %s", exc)
            return []
        pred = raw[0] if raw.ndim == 3 else raw  # (84, 8400)
        if pred.shape[0] < pred.shape[1]:  # 布局容错 (8400,84)
            pred = pred.T
        boxes_cxcywh = pred[:4, :].T  # (8400,4) 归一化到 640
        class_scores = pred[4:, :].T  # (8400,80)
        cls_idx = class_scores.argmax(axis=1)
        confs = class_scores[np.arange(class_scores.shape[0]), cls_idx]
        mask = confs >= self._conf
        if not mask.any():
            return []
        keep_idx = np.where(mask)[0]
        sel = boxes_cxcywh[keep_idx] * self._input_size  # cx,cy,w,h @640
        cx, cy, bw, bh = sel[:, 0], sel[:, 1], sel[:, 2], sel[:, 3]
        boxes_xyxy = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], axis=1)
        # 反 letterbox
        boxes_xyxy[:, [0, 2]] = (boxes_xyxy[:, [0, 2]] - pad_x) / scale
        boxes_xyxy[:, [1, 3]] = (boxes_xyxy[:, [1, 3]] - pad_y) / scale
        sel_confs = confs[keep_idx]
        sel_cls = cls_idx[keep_idx]
        # 按类别分组 NMS
        results: list[Detection] = []
        for c in np.unique(sel_cls):
            pos = np.where(sel_cls == c)[0]
            k = self._nms(boxes_xyxy[pos], sel_confs[pos])
            name = COCO_CLASSES[int(c)] if int(c) < len(COCO_CLASSES) else str(int(c))
            for j in k:
                i = pos[j]
                results.append(
                    Detection(
                        label=name,
                        conf=float(sel_confs[i]),
                        box=tuple(float(v) for v in boxes_xyxy[i]),
                    )
                )
        return results


# --------------------------------------------------------------------------- 选型
_DETECTOR: BaseProctorDetector | None = None


def _resolve_model_path() -> Path | None:
    from app.config.settings import settings  # noqa: PLC0415

    raw = (settings.PROCTOR_YOLO_MODEL or "").strip()
    candidates: list[Path] = []
    if raw:
        p = Path(raw)
        candidates.append(p if p.is_absolute() else Path.cwd() / p)
    # 常见落点：仓库根/项目根 models/ 下
    here = Path(__file__).resolve()
    for root in (Path.cwd(), *here.parents[:5]):
        candidates.append(root / "models" / "yolov8n.onnx")
    for c in candidates:
        if c.is_file():
            return c
    return None


def get_detector(*, reset: bool = False) -> BaseProctorDetector:
    """返回进程级单例检测器；任何依赖缺失/加载失败都降级，绝不抛错。"""
    global _DETECTOR
    if _DETECTOR is not None and not reset:
        return _DETECTOR
    # 1) YOLO
    try:
        model_path = _resolve_model_path()
        if model_path is not None:
            _DETECTOR = YoloDetector(model_path)
            logger.info("监考引擎: yolo_onnx (%s)", model_path)
            return _DETECTOR
    except Exception as exc:
        logger.warning("YOLO 检测器加载失败，尝试回退: %s", exc)
    # 2) Haar 人脸
    try:
        _DETECTOR = HaarFaceDetector()
        logger.info("监考引擎: haar_face（回退：仅人数估计）")
        return _DETECTOR
    except Exception as exc:
        logger.info("Haar 人脸回退不可用(opencv 未安装?): %s", exc)
    # 3) 禁用
    _DETECTOR = NullDetector()
    logger.info("监考引擎: disabled（不检测，考试流程不受影响）")
    return _DETECTOR


# ----------------------------------------------------------------------- 违规状态机
def frame_luma_stats(image_bytes: bytes) -> tuple[float, float]:
    """返回 (亮度均值, 亮度标准差)，用于 camera_blocked 判定；异常返回 (128, 64)。"""
    try:
        import numpy as np  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415

        img = Image.open(io.BytesIO(image_bytes)).convert("L").resize((64, 64))
        arr = np.asarray(img, dtype=np.float32)
        return float(arr.mean()), float(arr.std())
    except Exception:  # pragma: no cover - 解码失败不阻断
        return 128.0, 64.0


def evaluate_proctor(
    state: dict[str, Any] | None,
    detections: list[Detection],
    luma: tuple[float, float],
    ts: float,
    *,
    no_face_grace: int = 6,
    cap: int = 50,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """纯状态机：输入上一帧状态 + 本帧检出 + 亮度，返回 (新状态, 本帧新增违规)。

    不做任何 IO，可离线单测。flagged 位保证同类违规恢复前只记 1 条。
    """
    st: dict[str, Any] = dict(state or {})
    st.setdefault("frames", 0)
    st.setdefault("no_face_streak", 0)
    st.setdefault("flagged", {})
    flagged: dict[str, bool] = dict(st.get("flagged") or {})
    st["frames"] = int(st["frames"]) + 1
    out: list[dict[str, Any]] = []

    def hit(vtype: str, detail: str, clear: bool = False) -> None:
        if clear:
            flagged[vtype] = False
            return
        if not flagged.get(vtype):
            flagged[vtype] = True
            out.append({"ts": ts, "type": vtype, "detail": detail})

    labels = [d.label for d in detections]
    person_like = sum(1 for l in labels if l in ("person", "face"))

    # 无人连续帧
    if person_like == 0:
        streak = int(st.get("no_face_streak") or 0) + 1
        st["no_face_streak"] = streak
        if streak >= no_face_grace:
            hit("no_face", f"连续 {streak} 帧未检测到人员")
    else:
        st["no_face_streak"] = 0
        flagged.pop("no_face", None)

    # 多人
    if person_like > 1:
        hit("multi_person", f"画面检测到 {person_like} 个人")
    else:
        flagged.pop("multi_person", None)

    # 画面遮挡/断流（近纯黑、近纯白、近无方差）
    mean, std = luma
    if mean < 18.0 or mean > 240.0 or std < 5.0:
        hit("camera_blocked", f"画面亮度异常(mean={mean:.0f},std={std:.0f})，疑似遮挡")
    else:
        flagged.pop("camera_blocked", None)

    # 违规物品（仅 YOLO 能检出）
    for lab in labels:
        vtype = _VIOLATION_LABELS.get(lab)
        if vtype:
            hit(vtype, f"检测到疑似违规物品: {lab}")
    for vtype in ("phone", "laptop", "book"):
        if not any(l in _VIOLATION_LABELS and _VIOLATION_LABELS[l] == vtype for l in labels):
            flagged.pop(vtype, None)

    st["flagged"] = flagged
    st["person_like"] = person_like
    # 截断历史交给调用方（session.violations 整体 cap）
    return st, out[:cap]
