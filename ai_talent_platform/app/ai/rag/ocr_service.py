# 先限制像素并按 EXIF 调整方向，再由进程内复用的 OCR 引擎识别。低置信度行交给阈值过滤。
"""本地 RapidOCR，图片/扫描PDF共用，不上传图片到外部服务。
标签：[RAG组] [P1]。"""
from functools import lru_cache
from threading import Lock
from app.config.settings import settings
from app.core.exceptions import FileError
_lock = Lock()

@lru_cache(maxsize=1)
def _engine():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR(intra_op_num_threads=2, inter_op_num_threads=2)

def recognize(image):
    """低置信度行过滤；无文字时返回空串，由 Loader 判断是否失败。"""
    import numpy as np
    from PIL import ImageOps
    if image.width * image.height > settings.OCR_MAX_PIXELS:
        raise FileError('图片像素超过 OCR 上限')
    image = ImageOps.exif_transpose(image).convert('RGB')
    with _lock:
        result, _ = _engine()(np.asarray(image))
    return '\n'.join(line[1] for line in (result or []) if float(line[2]) >= settings.OCR_MIN_CONFIDENCE)
