"""
模块：tests/test_report_generation.py
职责：报告生成链路测试（渲染器 + 存储 + Service），不依赖数据库与真实模型。

标签：[Vanna组] [P0]（智能数据解读与报告生成：Word / PDF / 下载）

============================ 实现思路与代码要点 ============================
覆盖点：
1. PDF 渲染：文件头是 %PDF，包含中文标题；
2. Word 渲染：是合法 zip 包，document.xml 合式且包含标题；
3. 图表 PNG：合法 PNG，雷达/热力等新类型不抛异常；
4. 存储：保存 / 读取 / 列表 / 非法 ID；
5. Service：用假适配器跑通「生成 -> 列表 -> 下载」，并校验越权下载被拒。
6. NULL 对齐/断线、正负值坐标；图表失败返回业务错误且不保存缺图报告。
7. 渲染与保存期间事件循环继续响应；PDF/Word清楚说明表格范围和截断。
说明：
- 环境可能没有 pytest-asyncio，异步用例统一用 asyncio.run 包裹；
- 通过 settings_override 把 UPLOAD_DIR 指到临时目录，不污染真实 data/。
=========================================================================
"""
from __future__ import annotations

import asyncio
import builtins
import io
import shutil
import threading
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

import pytest

from app.config.settings import settings
from app.core.exceptions import FileError, NotFoundError, PermissionDeniedError
from app.core.pagination import PageParams
from app.schemas.analytics import (
    ChartConfig, ChartOptions, InsightDetail, NL2SQLResponse, ReportRequest,
)
from app.services import report_store
from app.services import report_renderer
from app.services import analytics_service
from app.services.analytics_service import AnalyticsService
from app.services.report_renderer import ReportPayload, render_chart_png, render_report


# ============================================================== 测试替身
class FakeUser:
    """最小可用用户：Service 只用到 id / 角色 / 部门 / 显示名。"""

    id = 7
    username = "tester"
    real_name = "测试员"
    is_superuser = True
    organization_id = 3
    role_codes = ["admin"]


class OtherUser(FakeUser):
    """另一个普通用户，用来验证越权下载。"""

    id = 8
    username = "other"
    real_name = "其他人"
    is_superuser = False
    role_codes = ["hr"]


class FakeAdapter:
    """假适配器：返回固定结果，不调用 Vanna / 模型 / 数据库。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def ask(self, question, *, user_scope=None, max_rows=200,
                  with_insight=True, chart_type="auto", chart_options=None):
        self.calls.append({
            "question": question, "max_rows": max_rows,
            "with_insight": with_insight, "chart_type": chart_type,
            "chart_options": chart_options,
        })
        chart = ChartConfig(
            type="bar", title=question[:80], x_field="department", y_field="cnt",
            x_labels=["技术部", "市场部"],
            series=[{"name": "cnt", "data": [
                {"name": "技术部", "value": 12}, {"name": "市场部", "value": 8},
            ]}],
        )
        insight = InsightDetail(
            summary="技术部人数最多，占已返回样本的 60%。",
            anomalies=["技术部人数明显高于市场部"],
            causes=["招聘资源集中在技术条线（待验证）"],
            risks=["人才结构可能失衡"],
            suggestions=["补充市场条线招聘"],
        )
        return NL2SQLResponse(
            question=question,
            sql="SELECT department, count(*) AS cnt FROM public.fact_talent_snapshot GROUP BY 1 LIMIT 200",
            columns=["department", "cnt"],
            rows=[["技术部", 12], ["市场部", 8]],
            chart=chart, insight=insight.summary, insight_detail=insight,
            elapsed_ms=12,
        )


def _payload(chart: ChartConfig | None = None, *, with_chart: bool = True) -> ReportPayload:
    """构造渲染用的报告数据；with_chart=False 时不放图表。"""
    if with_chart and chart is None:
        chart = _bar_chart()
    return ReportPayload(
        title="各部门人才数量分析",
        question="各部门人才数量是多少？",
        sql="SELECT department, count(*) AS cnt FROM public.fact_talent_snapshot GROUP BY 1",
        columns=["department", "cnt"],
        rows=[["技术部", 12], ["市场部", 8]],
        chart=chart,
        insight=InsightDetail(
            summary="技术部人数最多。",
            anomalies=["技术部占比偏高"],
            causes=["招聘集中（待验证）"],
            risks=["结构失衡"],
            suggestions=["补充市场条线"],
        ),
        warnings=["测试提示"],
        author="测试员",
        scope="all",
        generated_at=datetime(2026, 11, 3, 23, 0),
    )


def _bar_chart() -> ChartConfig:
    return ChartConfig(
        type="bar", title="各部门人才数量分析", x_field="department", y_field="cnt",
        x_labels=["技术部", "市场部"],
        series=[{"name": "cnt", "data": [
            {"name": "技术部", "value": 12}, {"name": "市场部", "value": 8},
        ]}],
    )


# ============================================================== 渲染
def test_pdf_renders():
    """PDF 文件头正确，且体积正常（说明确实写入了内容）。"""
    data = render_report(_payload(), "pdf")
    assert data[:4] == b"%PDF"
    assert len(data) > 2000


def test_pdf_without_chart():
    """没有图表时也能生成 PDF（图表是增强项，不是必需项）。"""
    data = render_report(_payload(with_chart=False), "pdf")
    assert data[:4] == b"%PDF"


def test_docx_renders():
    """Word 是合法 zip 包，document.xml 合式且包含标题与解读。"""
    data = render_report(_payload(), "docx")
    assert zipfile.is_zipfile(io.BytesIO(data))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        assert "word/document.xml" in names
        assert "[Content_Types].xml" in names
        xml = archive.read("word/document.xml").decode("utf-8")
        ElementTree.fromstring(xml)  # 解析失败会直接抛异常
        assert "各部门人才数量分析" in xml
        assert "技术部人数最多" in xml
        # 有图表时应该带上图片部件
        assert "word/media/chart.png" in names


def test_docx_without_chart():
    """没有图表时不写 media 部件，文档仍然合法。"""
    data = render_report(_payload(with_chart=False), "docx")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert "word/media/chart.png" not in archive.namelist()
        ElementTree.fromstring(archive.read("word/document.xml"))


@pytest.mark.parametrize("chart_type", ["bar", "line", "pie", "radar", "heatmap"])
def test_chart_png_for_all_types(chart_type):
    """五类图表都能画成 PNG，不抛异常。"""
    chart = _chart_of(chart_type)
    png = render_chart_png(chart)
    assert png is not None
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_chart_png_none():
    """没有图表配置时返回 None。"""
    assert render_chart_png(None) is None


def _chart_of(chart_type: str) -> ChartConfig:
    if chart_type == "pie":
        return ChartConfig(type="pie", title="占比", x_field="d", y_field="c",
                           series=[{"name": "c", "data": [
                               {"name": "技术部", "value": 12}, {"name": "市场部", "value": 8}]}])
    if chart_type == "radar":
        return ChartConfig(type="radar", title="能力画像",
                           indicators=[{"name": "沟通", "max": 100}, {"name": "协作", "max": 100},
                                       {"name": "技术", "max": 100}],
                           series=[{"name": "指标", "data": [
                               {"name": "沟通", "value": 80}, {"name": "协作", "value": 90},
                               {"name": "技术", "value": 70}]}])
    if chart_type == "heatmap":
        return ChartConfig(type="heatmap", title="部门层级分布", x_field="dept",
                           y_field="level", value_field="cnt",
                           x_labels=["技术部", "市场部"], y_labels=["初级", "高级"],
                           values=[[0, 0, 10], [0, 1, 5], [1, 0, 8], [1, 1, 3]])
    return ChartConfig(type=chart_type, title="趋势", x_field="d", y_field="c",
                       x_labels=["2026-05", "2026-06"],
                       series=[{"name": "c", "data": [
                           {"name": "2026-05", "value": 12}, {"name": "2026-06", "value": 8}]}])


# ============================================================== 存储
@pytest.fixture
def upload_dir():
    """报告落盘目录放在仓库内（沙箱只允许写工作区），用例结束自动清理。"""
    root = Path(__file__).resolve().parent.parent / ".pytest_tmp"
    path = root / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
        # 顺手收掉空的父目录，避免测试跑完在仓库里留下 .pytest_tmp（已加入 .gitignore 兜底）
        try:
            root.rmdir()
        except OSError:
            pass


def test_store_roundtrip(upload_dir, monkeypatch):
    """保存后能按 ID 读回元数据，列表能查到，文件可解析。"""
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(upload_dir))
    report_id = report_store.new_report_id()
    meta = report_store.build_meta(
        report_id=report_id, title="测试报告", fmt="pdf", question="各部门人数",
        user=FakeUser(), scope="all", author="测试员",
    )
    saved = report_store.save_report(b"%PDF-1.4 test", "pdf", meta)
    assert saved["size"] == len(b"%PDF-1.4 test")

    loaded = report_store.load_meta(report_id)
    assert loaded["title"] == "测试报告"
    assert report_store.report_file(loaded).exists()

    metas = report_store.list_metas(FakeUser.id)
    assert [item["report_id"] for item in metas] == [report_id]


def test_store_rejects_bad_id(upload_dir, monkeypatch):
    """非法报告 ID（路径穿越）必须拒绝。"""
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(upload_dir))
    with pytest.raises(NotFoundError):
        report_store.load_meta("../../etc/passwd")


# ============================================================== Service 全链路
def test_create_and_download(upload_dir, monkeypatch):
    """生成 -> 读取 -> 下载三件事能串起来，且越权下载被拒。"""
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(upload_dir))
    service = AnalyticsService(adapter=FakeAdapter())

    item = asyncio.run(service.create_report(
        ReportRequest(question="各部门人才数量是多少", format="pdf", title="人才数量报告"),
        FakeUser(),
    ))
    assert item.report_id
    assert item.format == "pdf"
    assert item.title == "人才数量报告"
    assert item.url.endswith(f"/analytics/reports/{item.report_id}/download")
    assert item.size > 0

    path, filename = asyncio.run(service.get_report_file(item.report_id, FakeUser()))
    assert path.exists()
    assert filename.endswith(".pdf")
    assert "人才数量报告" in filename

    with pytest.raises(PermissionDeniedError):
        asyncio.run(service.get_report_file(item.report_id, OtherUser()))


def test_list_reports_pagination(upload_dir, monkeypatch):
    """列表接口按归属过滤并分页。"""
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(upload_dir))
    service = AnalyticsService(adapter=FakeAdapter())
    for index in range(3):
        asyncio.run(service.create_report(
            ReportRequest(question=f"第 {index} 次问数", format="pdf"), FakeUser(),
        ))

    page = asyncio.run(service.list_reports(PageParams(page=1, page_size=2), FakeUser()))
    assert page.total == 3
    assert len(page.items) == 2
    assert page.pages == 2

    # 其他用户看不到别人的报告
    other_page = asyncio.run(service.list_reports(PageParams(page=1, page_size=2), OtherUser()))
    assert other_page.total == 0


def test_create_docx_report(upload_dir, monkeypatch):
    """Word 格式同样能生成并落盘。"""
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(upload_dir))
    service = AnalyticsService(adapter=FakeAdapter())
    item = asyncio.run(service.create_report(
        ReportRequest(question="各部门人才数量", format="docx"), FakeUser(),
    ))
    path, filename = asyncio.run(service.get_report_file(item.report_id, FakeUser()))
    assert filename.endswith(".docx")
    assert zipfile.is_zipfile(path)


def test_chart_options_are_forwarded():
    """图表的主题/尺寸/配色必须透传到适配器，不能被服务层吞掉。"""
    adapter = FakeAdapter()
    service = AnalyticsService(adapter=adapter)
    options = ChartOptions(theme="dark", width=1000, height=500)
    asyncio.run(service.nl2sql_ask(
        "各部门人数", FakeUser(), chart_type="pie", chart_options=options,
    ))
    assert adapter.calls[-1]["chart_type"] == "pie"
    assert adapter.calls[-1]["chart_options"] is options


@pytest.mark.parametrize("fmt", ["pdf", "docx"])
def test_required_chart_failure_stops_report(fmt, monkeypatch):
    """绘图报错时两种格式都必须失败，不能返回缺图的成功文件。"""
    def fail(*args):
        raise ValueError("private test data")

    monkeypatch.setattr(report_renderer, "_draw_bar", fail)
    with pytest.raises(FileError, match="报告图表生成失败") as error:
        render_report(_payload(), fmt)
    assert "private test data" not in error.value.message
    assert error.value.code == 5003


def test_missing_pillow_is_explicit_error(monkeypatch):
    """缺少已有绘图库时提示运行环境错误；不是静默跳过，也不自动安装。"""
    original = builtins.__import__

    def missing_pillow(name, *args, **kwargs):
        if name == "PIL":
            raise ImportError("Pillow unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_pillow)
    with pytest.raises(FileError, match="Pillow"):
        render_chart_png(_bar_chart())


@pytest.mark.parametrize("fmt", ["pdf", "docx"])
def test_render_failure_does_not_save_report(fmt, monkeypatch):
    """Service必须透传公共业务异常，并在失败时不保存报告或元数据。"""
    saved = []

    def fail(*args):
        raise ValueError("drawing failed")

    monkeypatch.setattr(report_renderer, "_draw_bar", fail)
    monkeypatch.setattr(report_store, "save_report", lambda *args: saved.append(args))
    with pytest.raises(FileError):
        asyncio.run(AnalyticsService(adapter=FakeAdapter()).create_report(
            ReportRequest(question="各部门人数", format=fmt), FakeUser(),
        ))
    assert saved == []


@pytest.mark.parametrize("kind", ["bar", "line"])
@pytest.mark.parametrize("values", [[10, -10], [-10, -5], [0, 0], [None, -5, 10, None]])
def test_signed_and_missing_values_render_real_png(kind, values):
    """使用真实Pillow解码PNG，覆盖正负混合、全负、全零和首尾NULL。"""
    from PIL import Image
    from app.adapters.nl2sql.chart_builder import build_chart

    chart, _ = build_chart(
        "人数变化", ["部门", "变化"], [[str(i), value] for i, value in enumerate(values)],
        chart_type=kind,
    )
    with Image.open(io.BytesIO(render_chart_png(chart))) as image:
        image.load()
        assert image.size == (900, 480)
        assert image.format == "PNG"


def test_signed_bars_extend_from_zero_in_correct_directions():
    """检查实际像素：正柱只在零线上方，负柱只在零线下方。"""
    from PIL import Image
    from app.adapters.nl2sql.chart_builder import build_chart

    chart, _ = build_chart("增长", ["部门", "增长"], [["A", 10], ["B", -10]])
    with Image.open(io.BytesIO(render_chart_png(chart))) as image:
        assert image.getpixel((250, 140)) == (78, 121, 167)
        assert image.getpixel((650, 310)) == (78, 121, 167)
        assert image.getpixel((250, 310)) != (78, 121, 167)
        assert image.getpixel((650, 140)) != (78, 121, 167)


def test_line_keeps_null_gap_and_axis_positions(monkeypatch):
    """真实绘图记录证明不跨NULL连线，第三点仍位于第三个横轴位置。"""
    from PIL import ImageDraw
    from app.adapters.nl2sql.chart_builder import build_chart

    connections, points = [], []
    original_line = ImageDraw.ImageDraw.line
    original_ellipse = ImageDraw.ImageDraw.ellipse

    def record_line(draw, xy, *args, **kwargs):
        if kwargs.get("width") == 3:
            connections.append(xy)
        return original_line(draw, xy, *args, **kwargs)

    def record_point(draw, xy, *args, **kwargs):
        points.append(((xy[0] + xy[2]) / 2, (xy[1] + xy[3]) / 2))
        return original_ellipse(draw, xy, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "line", record_line)
    monkeypatch.setattr(ImageDraw.ImageDraw, "ellipse", record_point)
    chart, _ = build_chart(
        "折线图", ["月份", "数量"], [["一月", 10], ["二月", None], ["三月", 20]],
    )
    assert render_chart_png(chart)
    assert connections == []
    assert len(points) == 2
    assert points[0][0] == 70
    assert points[1][0] == 870


def test_sparse_legacy_series_align_by_label():
    """旧稀疏配置按标签补空洞，避免把三月的值移给二月。"""
    chart = _bar_chart()
    chart.x_labels = ["一月", "二月", "三月"]
    chart.series = [{"name": "人数", "data": [
        {"name": "一月", "value": 10}, {"name": "三月", "value": 20},
    ]}]
    _, series = report_renderer._xy(chart)
    assert series[0]["values"] == [10, None, 20]
    assert render_chart_png(chart)


def test_complete_duplicate_labels_keep_distinct_observations():
    """完整配置中同名标签是不同观测，不能按名称合并掉。"""
    chart = _bar_chart()
    chart.x_labels = ["同名部门", "同名部门"]
    chart.series = [{"name": "人数", "data": [
        {"name": "同名部门", "value": 10}, {"name": "同名部门", "value": 20},
    ]}]
    _, series = report_renderer._xy(chart)
    assert series[0]["values"] == [10, 20]


def test_ambiguous_sparse_series_is_rejected():
    """稀疏数据遇到重复横轴时拒绝猜测，返回可见错误。"""
    chart = _bar_chart()
    chart.x_labels = ["同名部门", "同名部门"]
    chart.series = [{"name": "人数", "data": [{"name": "同名部门", "value": 10}]}]
    with pytest.raises(FileError, match="无法与横轴标签对齐"):
        render_chart_png(chart)


@pytest.mark.parametrize("kind", ["bar", "line", "pie", "radar", "heatmap"])
def test_empty_required_chart_is_rejected(kind):
    """有配置却无有效图表数据时不能生成只有标题的空白图。"""
    with pytest.raises(FileError):
        render_chart_png(ChartConfig(type=kind, title="空配置"))


@pytest.mark.parametrize("fmt", ["pdf", "docx"])
@pytest.mark.parametrize("kind", ["bar", "line"])
def test_report_embeds_chart_with_null_and_negative_values(fmt, kind):
    """真实渲染完整报告，空值/负值不能导致图表部件消失。"""
    from PIL import Image
    from app.adapters.nl2sql.chart_builder import build_chart

    rows = [["一月", 10], ["二月", None], ["三月", -5]]
    chart, _ = build_chart("月度增长", ["月份", "增长"], rows, chart_type=kind)
    payload = _payload(chart)
    payload.columns = ["月份", "增长"]
    payload.rows = rows
    data = render_report(payload, fmt)
    if fmt == "pdf":
        assert data.startswith(b"%PDF")
        assert b"/Subtype /Image" in data
    else:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            with Image.open(io.BytesIO(archive.read("word/media/chart.png"))) as image:
                image.load()
                assert image.size == (900, 480)


@pytest.mark.parametrize("fmt", ["pdf", "docx"])
def test_report_api_returns_chart_error_in_common_response(fmt, monkeypatch):
    """沿实际Router/Service/全局处理器验证前端收到错误，而不是成功下载地址。"""
    from httpx import ASGITransport, AsyncClient
    from app.core.application import create_application
    from app.core.dependencies import get_current_user
    from app.routers.analytics_router import get_service

    def fail(*args):
        raise ValueError("private drawing data")

    monkeypatch.setattr(report_renderer, "_draw_bar", fail)
    app = create_application()
    app.dependency_overrides[get_current_user] = lambda: FakeUser()
    app.dependency_overrides[get_service] = lambda: AnalyticsService(adapter=FakeAdapter())

    async def request():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(
                f"{settings.API_PREFIX}/analytics/report",
                json={"question": "各部门人数", "format": fmt},
            )

    response = asyncio.run(request())
    assert response.status_code == FileError.status_code
    body = response.json()
    assert body["code"] == 5003
    assert "报告图表生成失败" in body["message"]
    assert "private drawing data" not in response.text
    assert not body.get("data")


@pytest.mark.parametrize("phase", ["render", "save"])
def test_report_work_does_not_block_event_loop(phase, upload_dir, monkeypatch):
    """同步渲染/写盘等待时，事件循环仍能唤醒其他协程，避免只验证to_thread调用。"""
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(upload_dir))
    entered, released = threading.Event(), threading.Event()
    target, name = (
        (analytics_service, "render_report") if phase == "render"
        else (report_store, "save_report")
    )
    original = getattr(target, name)

    def blocking_work(*args):
        entered.set()
        if not released.wait(timeout=3):
            raise AssertionError("报告同步工作阻塞了事件循环")
        return original(*args)

    monkeypatch.setattr(target, name, blocking_work)

    async def scenario():
        task = asyncio.create_task(AnalyticsService(adapter=FakeAdapter()).create_report(
            ReportRequest(question="各部门人数", format="docx"), FakeUser(),
        ))
        try:
            # 只有事件循环没被报告阻塞，才能执行到set来释放工作线程。
            assert await asyncio.to_thread(entered.wait, 3)
            released.set()
            return await asyncio.wait_for(task, timeout=5)
        finally:
            released.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    assert asyncio.run(scenario()).size > 0


def test_concurrent_reports_keep_separate_files(upload_dir, monkeypatch):
    """并发使用真实渲染器与临时存储，报告内容/元数据不能互相覆盖。"""
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(upload_dir))
    service = AnalyticsService(adapter=FakeAdapter())
    formats = ["docx", "pdf", "docx", "pdf"]

    async def scenario():
        return await asyncio.gather(*(
            service.create_report(
                ReportRequest(question=f"各部门人数{i}", title=f"独立报告{i}", format=fmt),
                FakeUser(),
            )
            for i, fmt in enumerate(formats)
        ))

    items = asyncio.run(scenario())
    assert len({item.report_id for item in items}) == 4
    for i, item in enumerate(items):
        path, _ = asyncio.run(service.get_report_file(item.report_id, FakeUser()))
        assert report_store.load_meta(item.report_id)["title"] == f"独立报告{i}"
        if item.format == "pdf":
            assert path.read_bytes().startswith(b"%PDF")
        else:
            with zipfile.ZipFile(path) as archive:
                assert f"独立报告{i}" in archive.read("word/document.xml").decode("utf-8")


@pytest.mark.parametrize("fmt", ["pdf", "docx"])
@pytest.mark.parametrize("row_count,column_count,truncated", [
    (30, 8, False), (31, 8, True), (30, 9, True), (31, 9, True), (0, 2, False),
])
def test_report_discloses_table_limits(fmt, row_count, column_count, truncated, monkeypatch):
    """两种格式在边界内外/空结果都说明真实展示范围，不误称业务全量。"""
    payload = _payload(with_chart=False)
    payload.columns = [f"C{c}" for c in range(column_count)]
    payload.rows = [[f"R{r}C{c}" for c in range(column_count)] for r in range(row_count)]
    payload.warnings = ["原始查询提示"]
    if fmt == "pdf":
        from reportlab import platypus

        paragraphs = []
        original = platypus.Paragraph

        def paragraph(text, *args, **kwargs):
            paragraphs.append(text)
            return original(text, *args, **kwargs)

        # 捕获送入真实PDF排版的文字，避免增加PDF解析库依赖。
        monkeypatch.setattr(platypus, "Paragraph", paragraph)
        assert render_report(payload, fmt).startswith(b"%PDF")
        text = "\n".join(paragraphs)
    else:
        with zipfile.ZipFile(io.BytesIO(render_report(payload, fmt))) as archive:
            xml = ElementTree.fromstring(archive.read("word/document.xml"))
        text = "".join(xml.itertext())
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        table_rows = xml.findall(".//w:tbl/w:tr", ns)
        if row_count:
            assert len(table_rows) == min(row_count, 30) + 1
            assert all(len(row.findall("w:tc", ns)) == min(column_count, 8) for row in table_rows)
        else:
            assert table_rows == []

    assert f"本次查询返回 {row_count} 行、{column_count} 列" in text
    assert "不代表业务库全量数据" in text
    if not row_count:
        assert "没有返回数据行，未生成数据表格" in text
    elif truncated:
        assert f"表格仅展示前 {min(row_count, 30)} 行、前 {min(column_count, 8)} 列" in text
        assert "未展示其余行列" in text
    else:
        assert "表格已展示全部返回行列" in text
    if row_count > 30:
        assert "R30C0" not in text
    if column_count > 8:
        assert "R0C8" not in text
    assert "原始查询提示" in text
    assert payload.warnings == ["原始查询提示"]
    assert len(payload.rows) == row_count
    assert len(payload.columns) == column_count
