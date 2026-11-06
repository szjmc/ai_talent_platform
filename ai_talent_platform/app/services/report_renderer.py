"""
模块：app/services/report_renderer.py
职责：把一次问数结果渲染成 PDF / Word，并把图表配置画成 PNG。

标签：[Vanna组] [P0]（智能数据解读与报告生成：图表图片 + 报告文件）

============================ 实现思路与代码要点 ============================
1. 输入统一为 ReportPayload（问题 + SQL + 表格 + 图表配置 + 结构化解读），
   渲染层只负责“排版”，不重新查库、不调模型；
2. 图表：Pillow 直接绘制 PNG（柱状/折线/饼图/雷达/热力），不依赖浏览器和 Chrome；
   中文字体按 Windows / Linux 常见路径查找，找不到时退化为默认字体，不中断报告；
3. PDF：reportlab + 内置中文 CID 字体 STSong-Light，无需外部字体文件；
4. Word：按 OOXML 规范手写 document.xml 并打包成 .docx，避免新增 python-docx 依赖；
5. 无图表配置时允许纯文本报告；已有图表绘制失败时抛公共 FileError，不能静默缺图。
=========================================================================
"""
from __future__ import annotations

import io
import math
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from xml.sax.saxutils import escape

from app.core.exceptions import FileError
from app.schemas.analytics import ChartConfig, InsightDetail

# 图表默认配色（与前端 ECharts 默认色系接近）
_PALETTE = (
    "#4E79A7", "#F28E2B", "#E15759", "#76B7B2",
    "#59A14F", "#EDC948", "#B07AA1", "#FF9DA7",
)

# 报告里最多展示的数据行数，避免几十页表格
_MAX_TABLE_ROWS = 30
# 报告里最多展示的列数
_MAX_TABLE_COLS = 8

# 常见中文字体：Windows 优先，其次 Linux / macOS
_CJK_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)


@dataclass
class ReportPayload:
    """渲染报告所需的全部数据；由 AnalyticsService 组装，渲染层只读。"""

    title: str
    question: str
    sql: str
    columns: list[str]
    rows: list[list]
    chart: ChartConfig | None = None
    insight: InsightDetail | None = None
    warnings: list[str] = field(default_factory=list)
    author: str = "系统用户"
    scope: str = "all"
    generated_at: datetime = field(default_factory=datetime.now)


# ============================================================== 对外入口
def render_report(payload: ReportPayload, fmt: str) -> bytes:
    """按格式渲染报告，返回文件字节。fmt 只接受 pdf / docx。"""
    if fmt == "docx":
        return render_docx(payload)
    return render_pdf(payload)


def render_chart_png(chart: ChartConfig | None, width: int = 900, height: int = 480) -> bytes | None:
    """把配置画成 PNG；仅无配置时返回 None，依赖/绘制失败抛 FileError。"""
    if chart is None:
        return None
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise FileError("报告图表生成失败：缺少 Pillow，请检查项目运行环境") from exc
    try:
        image = Image.new("RGB", (width, height), "#FFFFFF")
        draw = ImageDraw.Draw(image)
        font_title = _font(20)
        font_body = _font(14)
        font_small = _font(12)
        # 标题居中放在最上方
        _text(draw, (width // 2, 24), _clip(chart.title, 40), font_title, "#222222")
        drawer = {
            "bar": _draw_bar,
            "line": _draw_line,
            "pie": _draw_pie,
            "radar": _draw_radar,
            "heatmap": _draw_heatmap,
        }.get(chart.type)
        if drawer is None:
            raise FileError("报告图表生成失败：不支持的图表类型")
        drawer(draw, chart, width, height, font_body, font_small)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    except FileError:
        raise
    except Exception as exc:
        # 公共异常处理器返回统一错误；不把数据或底层依赖细节暴露给用户。
        raise FileError("报告图表生成失败，请检查图表数据后重试") from exc


# ============================================================== PDF
def render_pdf(payload: ReportPayload) -> bytes:
    """用 reportlab 生成 PDF；中文使用内置 CID 字体，不依赖系统字体文件。"""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    font_name = _register_pdf_font(pdfmetrics, UnicodeCIDFont)

    title_style = ParagraphStyle("rpt_title", fontName=font_name, fontSize=18, leading=26,
                                 alignment=1, spaceAfter=6)
    head_style = ParagraphStyle("rpt_head", fontName=font_name, fontSize=12.5, leading=20,
                                spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#1F4E79"))
    body_style = ParagraphStyle("rpt_body", fontName=font_name, fontSize=10.5, leading=16,
                                wordWrap="CJK")
    small_style = ParagraphStyle("rpt_small", fontName=font_name, fontSize=9, leading=14,
                                 textColor=colors.HexColor("#666666"))
    sql_style = ParagraphStyle("rpt_sql", fontName=font_name, fontSize=8.5, leading=13,
                               wordWrap="CJK", backColor=colors.HexColor("#F5F5F5"),
                               borderPadding=4)

    story: list = []
    story.append(Paragraph(escape(payload.title), title_style))
    story.append(Paragraph(
        escape(f"生成时间：{payload.generated_at:%Y-%m-%d %H:%M}　"
               f"生成人：{payload.author}　数据范围：{_scope_text(payload.scope)}"), small_style))
    story.append(Spacer(1, 4))

    story.append(Paragraph("一、分析问题", head_style))
    story.append(Paragraph(escape(payload.question), body_style))

    story.append(Paragraph("二、执行 SQL", head_style))
    story.append(Paragraph(escape(_clip(payload.sql, 3000)), sql_style))

    story.append(Paragraph("三、数据结果", head_style))
    story.append(_pdf_table(payload, font_name, body_style, small_style, Paragraph, Table, TableStyle, colors))
    story.append(Paragraph(escape(_table_notice(payload)), small_style))

    if payload.chart is not None:
        png = render_chart_png(payload.chart)
        if png:
            story.append(Paragraph("四、数据图表", head_style))
            img_width = 165 * mm
            img_height = img_width * 480 / 900
            story.append(Image(io.BytesIO(png), width=img_width, height=img_height))

    story.append(Paragraph("五、数据解读", head_style))
    for paragraph in _insight_paragraphs(payload.insight):
        story.append(Paragraph(escape(paragraph), body_style))

    if payload.warnings:
        story.append(Paragraph("生成说明", head_style))
        for warning in payload.warnings:
            story.append(Paragraph(escape("· " + warning), small_style))

    story.append(Spacer(1, 6))

    def _footer(canvas, doc) -> None:
        """页脚：免责声明 + 页码（放在页面底部，避免单独占一页）。"""
        canvas.saveState()
        canvas.setFont(font_name, 7.5)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawString(18 * mm, 9 * mm,
                          "本报告由 AI 根据当前查询结果自动生成，仅代表查询时的授权范围与筛选条件；"
                          "成因与风险为待验证推断。")
        canvas.drawRightString(192 * mm, 9 * mm, f"第 {doc.page} 页")
        canvas.restoreState()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
        title=payload.title, author=payload.author,
    )
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()


def _register_pdf_font(pdfmetrics, unicode_cid_font) -> str:
    """注册 reportlab 内置中文字体，返回可用的字体名。"""
    for name in ("STSong-Light", "HeiseiMin-W3"):
        try:
            pdfmetrics.registerFont(unicode_cid_font(name))
            return name
        except Exception:
            continue
    return "Helvetica"


def _pdf_table(payload, font_name, body_style, small_style, Paragraph, Table, TableStyle, colors):
    """生成受行列上限约束的表格，范围说明由调用方通过 _table_notice 添加。"""
    columns = payload.columns[:_MAX_TABLE_COLS]
    rows = payload.rows[:_MAX_TABLE_ROWS]
    header = [Paragraph(escape(str(name)), small_style) for name in columns]
    data = [header]
    for row in rows:
        data.append([
            Paragraph(escape(_clip(cell, 60)), small_style)
            for cell in list(row)[:_MAX_TABLE_COLS]
        ])
    if not data[1:]:
        return Paragraph(escape("当前查询没有返回数据行。"), body_style)

    count = max(1, len(columns))
    widths = [165.0 / count] * count
    table = Table(data, colWidths=[w * 2.8346 for w in widths], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAF1F8")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BBBBBB")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return table


# ============================================================== Word（OOXML）
def render_docx(payload: ReportPayload) -> bytes:
    """手写 OOXML 生成 .docx，避免依赖 python-docx；结构见 ECMA-376。"""
    png = render_chart_png(payload.chart) if payload.chart is not None else None

    body: list[str] = []
    body.append(_docx_p(payload.title, size=36, bold=True, align="center"))
    body.append(_docx_p(
        f"生成时间：{payload.generated_at:%Y-%m-%d %H:%M}　生成人：{payload.author}　"
        f"数据范围：{_scope_text(payload.scope)}", size=18, color="666666"))

    body.append(_docx_p("一、分析问题", size=26, bold=True, color="1F4E79"))
    body.append(_docx_p(payload.question))
    body.append(_docx_p("二、执行 SQL", size=26, bold=True, color="1F4E79"))
    body.append(_docx_p(_clip(payload.sql, 3000), size=17, color="333333"))
    body.append(_docx_p("三、数据结果", size=26, bold=True, color="1F4E79"))
    body.append(_docx_table(payload))
    body.append(_docx_p(_table_notice(payload), size=18, color="666666"))

    rels: list[str] = [
        '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    ]
    if png:
        body.append(_docx_p("四、数据图表", size=26, bold=True, color="1F4E79"))
        body.append(_docx_image(png, "rIdChart"))
        rels.append(
            '<Relationship Id="rIdChart" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/image" Target="media/chart.png"/>'
        )

    body.append(_docx_p("五、数据解读", size=26, bold=True, color="1F4E79"))
    for paragraph in _insight_paragraphs(payload.insight):
        body.append(_docx_p(paragraph))

    if payload.warnings:
        body.append(_docx_p("生成说明", size=26, bold=True, color="1F4E79"))
        for warning in payload.warnings:
            body.append(_docx_p("· " + warning, size=18, color="666666"))

    body.append(_docx_p(
        "说明：本报告由 AI 根据当前查询结果自动生成，数据仅代表查询时的授权范围与筛选条件；"
        "解读中的成因与风险为待验证推断，不构成最终结论。", size=17, color="666666"))

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document ' + _DOCX_NS + '><w:body>' + "".join(body) +
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" '
        'w:header="851" w:footer="992" w:gutter="0"/></w:sectPr>'
        '</w:body></w:document>'
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _docx_content_types(png is not None))
        archive.writestr("_rels/.rels", _docx_root_rels())
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", _DOCX_STYLES)
        archive.writestr("word/_rels/document.xml.rels",
                         '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         + "".join(rels) + '</Relationships>')
        if png:
            archive.writestr("word/media/chart.png", png)
    return buffer.getvalue()


_DOCX_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"'
)

_DOCX_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:docDefaults><w:rPrDefault><w:rPr>'
    '<w:rFonts w:ascii="Calibri" w:eastAsia="微软雅黑" w:hAnsi="Calibri"/>'
    '<w:sz w:val="21"/><w:szCs w:val="21"/>'
    '</w:rPr></w:rPrDefault>'
    '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="300" w:lineRule="auto"/></w:pPr></w:pPrDefault>'
    '</w:docDefaults></w:styles>'
)


def _docx_p(text: str, *, size: int = 21, bold: bool = False,
            align: str | None = None, color: str | None = None) -> str:
    """生成一个段落；size 单位是半点（21 = 五号 10.5pt）。"""
    props = ['<w:spacing w:after="120"/>']
    if align:
        props.append(f'<w:jc w:val="{align}"/>')
    # 注意：w:rPr 的子元素必须按 OOXML 规定顺序排列（b -> color -> sz），否则严格校验会报错
    run_props: list[str] = []
    if bold:
        run_props.append("<w:b/>")
    if color:
        run_props.append(f'<w:color w:val="{color}"/>')
    run_props.append(f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>')
    return (
        "<w:p><w:pPr>" + "".join(props) + "</w:pPr>"
        "<w:r><w:rPr>" + "".join(run_props) + "</w:rPr>"
        f'<w:t xml:space="preserve">{escape(str(text))}</w:t></w:r></w:p>'
    )


def _docx_table(payload: ReportPayload) -> str:
    """生成数据表格；列宽按列数均分（总宽约 6.5 英寸 = 9360 twips）。"""
    columns = [str(name) for name in payload.columns[:_MAX_TABLE_COLS]]
    rows = payload.rows[:_MAX_TABLE_ROWS]
    if not rows:
        return _docx_p("当前查询没有返回数据行。")

    total_width = 9360
    cell_width = max(600, total_width // max(1, len(columns)))
    grid = "".join(f'<w:gridCol w:w="{cell_width}"/>' for _ in columns)
    borders = "".join(
        f'<w:{edge} w:val="single" w:sz="4" w:space="0" w:color="BBBBBB"/>'
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV")
    )
    header = "".join(_docx_cell(name, cell_width, header=True) for name in columns)
    body_rows = "".join(
        "<w:tr>" + "".join(_docx_cell(cell, cell_width) for cell in list(row)[:len(columns)]) + "</w:tr>"
        for row in rows
    )
    return (
        "<w:tbl><w:tblPr>"
        f'<w:tblW w:w="{total_width}" w:type="dxa"/><w:tblBorders>{borders}</w:tblBorders>'
        "</w:tblPr>"
        f"<w:tblGrid>{grid}</w:tblGrid>"
        f'<w:tr><w:trPr><w:tblHeader/></w:trPr>{header}</w:tr>'
        f"{body_rows}</w:tbl>"
    )


def _docx_cell(value, width: int, *, header: bool = False) -> str:
    """生成单元格；表头加灰底加粗。"""
    shade = '<w:shd w:val="clear" w:color="auto" w:fill="EAF1F8"/>' if header else ""
    bold = "<w:b/>" if header else ""
    text = _clip(value, 60 if not header else 20)
    return (
        f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shade}</w:tcPr>'
        '<w:p><w:pPr><w:spacing w:after="0"/></w:pPr>'
        f'<w:r><w:rPr>{bold}<w:sz w:val="18"/><w:szCs w:val="18"/></w:rPr>'
        f'<w:t xml:space="preserve">{escape(str(text))}</w:t></w:r></w:p></w:tc>'
    )


def _docx_image(png: bytes, rel_id: str) -> str:
    """插入图片：按 6 英寸宽等比缩放（1 英寸 = 914400 EMU）。"""
    width_emu = 5486400
    ratio = 480 / 900
    try:
        from PIL import Image
        with Image.open(io.BytesIO(png)) as image:
            ratio = image.height / image.width
    except Exception:
        pass
    height_emu = int(width_emu * ratio)
    return (
        '<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:drawing>'
        '<wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{width_emu}" cy="{height_emu}"/>'
        '<wp:docPr id="1" name="chart"/>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic><pic:nvPicPr><pic:cNvPr id="1" name="chart.png"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{rel_id}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{width_emu}" cy="{height_emu}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
        '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>'
    )


def _docx_content_types(has_image: bool) -> str:
    """声明包内各部件的内容类型。"""
    image_default = '<Default Extension="png" ContentType="image/png"/>' if has_image else ""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        + image_default +
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.wordprocessingml.styles+xml"/>'
        '</Types>'
    )


def _docx_root_rels() -> str:
    """包级关系：指向 word/document.xml。"""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>'
    )


# ============================================================== 图表绘制
def _draw_bar(draw, chart: ChartConfig, width: int, height: int, font, small) -> None:
    """柱状图：多系列并排，NULL 留空，正负值分别从零基线向两侧绘制。"""
    labels, series = _xy(chart)
    if not labels or not series:
        raise FileError("报告图表生成失败：柱状图没有可绘制的数据")
    left, right, top, bottom = 70, 30, 60, 80
    plot_w, plot_h = width - left - right, height - top - bottom
    minimum, maximum = _value_range(series)
    _draw_axes(draw, left, top, plot_w, plot_h, minimum, maximum, font)
    zero_y = top + plot_h * maximum / (maximum - minimum)
    group_width = plot_w / max(1, len(labels))
    bar_width = group_width * 0.7 / max(1, len(series))
    for index, label in enumerate(labels):
        base_x = left + index * group_width + group_width * 0.15
        for s_index, item in enumerate(series):
            value = item["values"][index]
            if value is None:
                continue
            value_y = top + plot_h * (maximum - value) / (maximum - minimum)
            x0 = base_x + s_index * bar_width
            draw.rectangle([x0, min(zero_y, value_y), x0 + bar_width * 0.9, max(zero_y, value_y)],
                           fill=_color(s_index))
    _draw_x_labels(draw, labels, left, top + plot_h + 8, group_width, small)


def _draw_line(draw, chart: ChartConfig, width: int, height: int, font, small) -> None:
    """折线图：支持负值；NULL 保持横轴位置并断开连线，不补零、不跨空洞连接。"""
    labels, series = _xy(chart)
    if not labels or not series:
        raise FileError("报告图表生成失败：折线图没有可绘制的数据")
    left, right, top, bottom = 70, 30, 60, 80
    plot_w, plot_h = width - left - right, height - top - bottom
    minimum, maximum = _value_range(series)
    _draw_axes(draw, left, top, plot_w, plot_h, minimum, maximum, font)
    step = plot_w / max(1, len(labels) - 1) if len(labels) > 1 else plot_w
    for s_index, item in enumerate(series):
        previous = None
        for index, value in enumerate(item["values"]):
            if value is None:
                previous = None
                continue
            x = left + index * step
            y = top + plot_h * (maximum - value) / (maximum - minimum)
            if previous is not None:
                draw.line([previous, (x, y)], fill=_color(s_index), width=3)
            draw.ellipse([x - 4, y - 4, x + 4, y + 4], fill=_color(s_index))
            previous = (x, y)
    _draw_x_labels(draw, labels, left, top + plot_h + 8, step, small, centered=False)


def _draw_pie(draw, chart: ChartConfig, width: int, height: int, font, small) -> None:
    """饼图：左侧饼 + 右侧图例（名称 + 百分比）。"""
    if not chart.series:
        raise FileError("报告图表生成失败：饼图没有可绘制的数据")
    data = chart.series[0].get("data") or []
    if not data:
        raise FileError("报告图表生成失败：饼图没有可绘制的数据")
    values = [float(item.get("value") or 0) for item in data]
    total = sum(values) or 1
    size = min(width, height) - 120
    box = [(60, (height - size) // 2), (60 + size, (height + size) // 2)]
    start = 0.0
    for index, item in enumerate(data):
        extent = 360 * float(item.get("value") or 0) / total
        draw.pieslice(box, start=start, end=start + extent,
                      fill=_color(index), outline="#FFFFFF")
        start += extent
    legend_x = 60 + size + 40
    for index, item in enumerate(data[:12]):
        y = 90 + index * 26
        draw.rectangle([legend_x, y, legend_x + 14, y + 14], fill=_color(index))
        percent = 100 * float(item.get("value") or 0) / total
        _text(draw, (legend_x + 22, y + 7), _clip(item.get("name"), 12), small, "#333333", anchor="lm")
        _text(draw, (legend_x + 190, y + 7), f"{percent:.1f}%", small, "#666666", anchor="lm")


def _draw_radar(draw, chart: ChartConfig, width: int, height: int, font, small) -> None:
    """雷达图：按 indicators 画轴，单系列画多边形。"""
    indicators = chart.indicators or []
    if len(indicators) < 3 or not chart.series:
        raise FileError("报告图表生成失败：雷达图数据不完整")
    points = chart.series[0].get("data") or []
    center_x, center_y = width // 2, height // 2 + 10
    radius = min(width, height) // 2 - 80
    count = len(indicators)
    axes = []
    for index, indicator in enumerate(indicators):
        angle = -math.pi / 2 + 2 * math.pi * index / count
        axes.append((math.cos(angle), math.sin(angle)))
        draw.line([center_x, center_y, center_x + radius * math.cos(angle),
                   center_y + radius * math.sin(angle)], fill="#DDDDDD")
        label_x = center_x + (radius + 26) * math.cos(angle)
        label_y = center_y + (radius + 26) * math.sin(angle)
        _text(draw, (label_x, label_y), _clip(indicator.get("name"), 8), small, "#333333")
    polygon = []
    for index, indicator in enumerate(indicators):
        maximum = float(indicator.get("max") or 100) or 100
        value = float(points[index].get("value") or 0) if index < len(points) else 0.0
        ratio = min(1.0, value / maximum)
        polygon.append((center_x + radius * ratio * axes[index][0],
                        center_y + radius * ratio * axes[index][1]))
    if len(polygon) >= 3:
        draw.polygon(polygon, outline=_color(0), fill=None)
        for x, y in polygon:
            draw.ellipse([x - 4, y - 4, x + 4, y + 4], fill=_color(0))


def _draw_heatmap(draw, chart: ChartConfig, width: int, height: int, font, small) -> None:
    """热力图：按 values 的三元组画色块，颜色由浅蓝到深红。"""
    x_labels = chart.x_labels or []
    y_labels = chart.y_labels or []
    values = chart.values or []
    if not x_labels or not y_labels or not values:
        raise FileError("报告图表生成失败：热力图数据不完整")
    left, right, top, bottom = 110, 30, 60, 70
    cell_w = (width - left - right) / len(x_labels)
    cell_h = (height - top - bottom) / len(y_labels)
    numbers = [float(item[2]) for item in values if len(item) >= 3]
    low, high = min(numbers, default=0), max(numbers, default=1)
    span = (high - low) or 1
    for item in values:
        if len(item) < 3:
            continue
        x_index, y_index, value = int(item[0]), int(item[1]), float(item[2])
        x0 = left + x_index * cell_w
        y0 = top + y_index * cell_h
        ratio = (value - low) / span
        draw.rectangle([x0, y0, x0 + cell_w, y0 + cell_h],
                       fill=_heat_color(ratio), outline="#FFFFFF")
        _text(draw, (x0 + cell_w / 2, y0 + cell_h / 2), f"{value:g}", small, "#333333")
    for index, label in enumerate(x_labels):
        _text(draw, (left + index * cell_w + cell_w / 2, top + len(y_labels) * cell_h + 10),
              _clip(label, 8), small, "#333333")
    for index, label in enumerate(y_labels):
        _text(draw, (left - 8, top + index * cell_h + cell_h / 2), _clip(label, 8),
              small, "#333333", anchor="rm")


def _value_range(series: list[dict]) -> tuple[float, float]:
    """计算所有系列共用的数值域并包含零；全零时用 0—1 避免除零。"""
    values = [value for item in series for value in item["values"] if value is not None]
    if not values:
        raise FileError("报告图表生成失败：没有可绘制的数值")
    minimum, maximum = min(0, min(values)), max(0, max(values))
    return (minimum, maximum) if minimum != maximum else (0, 1)


def _draw_axes(draw, left: int, top: int, plot_w: int, plot_h: int,
               minimum: float, maximum: float, font) -> None:
    """画分类轴、数值轴、网格及独立零基线，使正负数坐标可解释。"""
    draw.line([left, top, left, top + plot_h], fill="#999999", width=2)
    draw.line([left, top + plot_h, left + plot_w, top + plot_h], fill="#999999", width=2)
    for step in range(5):
        value = minimum + (maximum - minimum) * step / 4
        y = top + plot_h - plot_h * step / 4
        if step:
            draw.line([left, y, left + plot_w, y], fill="#EEEEEE")
        _text(draw, (left - 8, y), f"{value:g}", font, "#666666", anchor="rm")
    zero_y = top + plot_h * maximum / (maximum - minimum)
    draw.line([left, zero_y, left + plot_w, zero_y], fill="#999999", width=2)


def _draw_x_labels(draw, labels, left, y, step, font, *, centered: bool = True) -> None:
    """画横轴标签：标签过多时隔几个显示一个，避免重叠。"""
    skip = max(1, math.ceil(len(labels) / 12))
    for index, label in enumerate(labels):
        if index % skip:
            continue
        offset = step / 2 if centered else 0
        _text(draw, (left + step * index + offset, y), _clip(label, 8), font, "#333333", anchor="ma")


def _xy(chart: ChartConfig) -> tuple[list[str], list[dict]]:
    """按横轴对齐系列；兼容无空值占位的旧配置，缺失点保留 None。"""
    labels = list(chart.x_labels or [])
    if not labels:
        labels = list(dict.fromkeys(
            str(point.get("name")) for item in chart.series for point in item.get("data") or []
        ))
    series = []
    for item in chart.series:
        data = item.get("data") or []
        names = [str(point.get("name")) for point in data]
        if names == labels:
            # 完整配置按位置读取，也保留同名标签的不同观测值。
            values = [point.get("value") for point in data]
        else:
            # 稀疏旧配置只能在标签唯一时按名称补齐；歧义不得静默错配。
            if (
                len(set(names)) != len(names)
                or len(set(labels)) != len(labels)
                or not set(names) <= set(labels)
            ):
                raise FileError("报告图表生成失败：系列数据无法与横轴标签对齐")
            by_name = {str(point.get("name")): point.get("value") for point in data}
            values = [by_name.get(label) for label in labels]
        series.append({
            "name": item.get("name"),
            "values": values,
        })
    return labels, series


def _heat_color(ratio: float) -> str:
    """热力色阶：0 -> 浅蓝，1 -> 深红（每个通道都夹在 0-255，避免非法色值）。"""
    ratio = max(0.0, min(1.0, ratio))
    red = min(255, int(20 + 235 * ratio))
    green = min(255, int(40 + 160 * (1 - ratio)))
    blue = min(255, int(30 + 190 * (1 - ratio)))
    return f"#{red:02x}{green:02x}{blue:02x}"


# ============================================================== 通用工具
def _table_notice(payload: ReportPayload) -> str:
    """说明返回结果与表格摘要的区别，不把 LIMIT 后的结果宣称为业务全量。"""
    row_count, column_count = len(payload.rows), len(payload.columns)
    if not row_count:
        shown = "当前查询没有返回数据行，未生成数据表格。"
    elif row_count > _MAX_TABLE_ROWS or column_count > _MAX_TABLE_COLS:
        shown = (
            f"表格仅展示前 {min(row_count, _MAX_TABLE_ROWS)} 行、"
            f"前 {min(column_count, _MAX_TABLE_COLS)} 列，未展示其余行列。"
        )
    else:
        shown = "表格已展示全部返回行列。"
    return (
        f"本次查询返回 {row_count} 行、{column_count} 列；{shown}"
        "超长单元格以省略号缩略。查询结果受权限和行数上限限制，"
        "不代表业务库全量数据；图表与解读的范围以各自说明为准。"
    )


def _font(size: int):
    """加载中文字体；找不到时退回 Pillow 默认字体（英文正常、中文可能显示方框）。"""
    from PIL import ImageFont

    for path in _CJK_FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _text(draw, xy, text, font, fill, anchor: str = "mm") -> None:
    """写文字；个别字体不支持 anchor 时退化为按左上角绘制。"""
    try:
        draw.text(xy, str(text), font=font, fill=fill, anchor=anchor)
    except Exception:
        draw.text((xy[0], xy[1]), str(text), font=font, fill=fill)


def _color(index: int) -> str:
    """按序号取配色。"""
    return _PALETTE[index % len(_PALETTE)]


def _clip(value, limit: int) -> str:
    """截断过长文本，保持表格与图表可读。"""
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _scope_text(scope: str) -> str:
    """数据范围的中文说明。"""
    return {"all": "全部数据", "dept_sub": "本部门及下级", "dept": "本部门", "self": "仅本人"}.get(
        scope, scope or "全部数据")


def _insight_paragraphs(insight: InsightDetail | None) -> list[str]:
    """把结构化解读拆成若干段文字；没有解读时给一句说明。"""
    if insight is None:
        return ["本次未生成数据解读。"]
    paragraphs: list[str] = []
    if insight.summary:
        paragraphs.append(insight.summary)
    for title, values in (("异常点", insight.anomalies), ("可能成因", insight.causes),
                          ("潜在风险", insight.risks), ("优化建议", insight.suggestions)):
        if values:
            paragraphs.append(f"{title}：" + "；".join(values))
    return paragraphs or ["本次未生成数据解读。"]
