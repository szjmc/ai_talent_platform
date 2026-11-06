"""简历 PDF 的栏位和图片区域解析。
标签：[RAG组] [P1]。
"""
from app.config.settings import settings


def resume_page_text(page):
    """仅在正文存在清晰纵向栏间距时，按左栏、右栏读取。"""
    words = page.extract_words()
    for ratio in (0.32, 0.40, 0.50, 0.60, 0.68):
        divider = page.bbox[0] + page.width * ratio
        margin = page.width * 0.015
        left = [word for word in words if word['x1'] <= divider - margin]
        right = [word for word in words if word['x0'] >= divider + margin]
        if min(len(left), len(right)) < 8 or len(left) + len(right) != len(words):
            continue
        if max(min(word['top'] for word in side) for side in (left, right)) >= min(
            max(word['bottom'] for word in side) for side in (left, right)
        ):
            continue
        x0, top, x1, bottom = page.bbox
        columns = [page.crop(bounds).extract_text() or '' for bounds in (
            (x0, top, divider, bottom), (divider, top, x1, bottom),
        )]
        return '\n'.join(columns)
    return page.extract_text() or ''


def resume_image_regions(page, recognize):
    """对文字页上的较大图片做局部 OCR，返回文字及 PDF 坐标。"""
    scale = settings.OCR_PDF_SCALE
    if page.width * page.height * scale ** 2 > settings.OCR_MAX_PIXELS:
        return []
    regions = []
    page_left, page_top, page_right, page_bottom = page.bbox
    for image in page.images:
        bounds = (max(page_left, image['x0']), max(page_top, image['top']),
                  min(page_right, image['x1']), min(page_bottom, image['bottom']))
        if bounds[2] - bounds[0] >= 40 and bounds[3] - bounds[1] >= 30 and bounds not in regions:
            regions.append(bounds)
    if not regions:
        return []
    output = []
    for bounds in regions[:8]:
        with page.crop(bounds).to_image(resolution=72 * scale).original as bitmap:
            text = recognize(bitmap)
        if text.strip():
            output.append((text, bounds))
    return output
# 版面处理负责组织简历内容的阅读顺序，下游结构化解析消费整理后的文本。
