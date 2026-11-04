"""
模块：tests/test_docs_diagrams.py
职责：数据链路 / 鉴权文档与流程图的完整性守门测试。

标签：[架构组] [P2]（文档资产校验：图片存在、链接可解析、与代码顺序一致）

为什么需要：
1. 手册与 7 张 SVG 流程图是联调、评审与新人上手的入口，链接失效或图缺失会直接误导读者；
2. 中间件顺序是「后注册先执行」，代码一改文档就必须同步，这里用代码反推执行顺序做交叉校验；
3. 全部用例只读文件，不依赖数据库 / Redis / Milvus，可离线执行。

实现思路：
- 解析 Markdown 中的相对链接与图片，断言目标文件真实存在；
- 断言 7 张 SVG 结构完整（<svg> 包裹 + viewBox）；
- 断言单页版 HTML 内联了全部 7 张图（7 个 <svg> + 7 个锚点）；
- 从 app/core/middleware.py 解析 add_middleware 顺序，与手册声明的执行顺序比对。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
MANUAL = DOCS / "数据链路与鉴权实现手册.md"
HTML = DOCS / "数据链路与鉴权流程图.html"
DIAGRAM_DIR = DOCS / "diagrams"

DIAGRAMS = (
    "01-请求全链路.svg",
    "02-中间件洋葱与执行顺序.svg",
    "03-登录鉴权时序.svg",
    "04-请求鉴权与权限决策.svg",
    "05-事务与会话生命周期.svg",
    "06-公共组件地图.svg",
    "07-AI与事件总线链路.svg",
)

ANCHORS = ("pipeline", "middleware", "login", "authz", "tx", "components", "ai")


def test_diagrams_exist_and_are_valid_svg() -> None:
    """7 张流程图必须存在且结构完整（离线可渲染，无外部依赖）。"""
    for name in DIAGRAMS:
        path = DIAGRAM_DIR / name
        assert path.is_file(), f"流程图缺失：{path}"
        content = path.read_text(encoding="utf-8").strip()
        assert content.startswith("<svg"), f"{name} 不是合法 SVG"
        assert content.endswith("</svg>"), f"{name} SVG 未闭合"
        assert 'viewBox="0 0 ' in content, f"{name} 缺少 viewBox（无法自适应缩放）"
        assert "http://" not in content.replace('xmlns="http://www.w3.org/2000/svg"', ""), (
            f"{name} 引用了外部资源，离线打开会失效"
        )


def test_manual_relative_links_resolve() -> None:
    """手册中的相对链接（手册 / 图片）必须都能解析到真实文件。"""
    assert MANUAL.is_file(), "数据链路与鉴权实现手册.md 缺失"
    text = MANUAL.read_text(encoding="utf-8")
    targets = re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", text)
    assert targets, "手册里没有任何链接，疑似内容被截断"

    missing: list[str] = []
    for target in targets:
        target = target.strip()
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        path = (DOCS / target.split("#")[0]).resolve()
        if not path.exists():
            missing.append(target)
    assert not missing, f"手册中的相对链接无法解析：{missing}"


def test_manual_covers_core_topics() -> None:
    """关键实现必须被手册覆盖，避免「文档写了但没写重点」。"""
    text = MANUAL.read_text(encoding="utf-8")
    keywords = (
        "register_middlewares",
        "TraceIdMiddleware",
        "auto_error=False",
        "get_current_user",
        "require_permission",
        "get_data_scope",
        "get_db()",
        "commit",
        "rollback",
        "X-Trace-Id",
        "trace_id_ctx",
        "ApiResponse",
        "PageParams",
    )
    missing = [k for k in keywords if k not in text]
    assert not missing, f"手册缺少关键实现说明：{missing}"


def test_middleware_order_matches_code() -> None:
    """文档声明的执行顺序必须与 app/core/middleware.py 的注册顺序互为反序。"""
    source = (ROOT / "app/core/middleware.py").read_text(encoding="utf-8")
    registered = re.findall(r"app\.add_middleware\(\s*(\w+)", source)
    assert registered == [
        "RateLimitMiddleware",
        "SecurityHeadersMiddleware",
        "AccessLogMiddleware",
        "CORSMiddleware",
        "TraceIdMiddleware",
    ], f"中间件注册顺序发生变化：{registered}"

    expected_exec = " → ".join(
        {"CORSMiddleware": "CORS", "TraceIdMiddleware": "TraceId"}.get(name, name.replace("Middleware", ""))
        for name in reversed(registered)
    )
    text = MANUAL.read_text(encoding="utf-8")
    assert expected_exec in text, f"手册未同步中间件执行顺序：{expected_exec}"


def test_html_embeds_all_diagrams() -> None:
    """单页版 HTML 必须内联 7 张图，且带 7 个可跳转锚点。"""
    assert HTML.is_file(), "数据链路与鉴权流程图.html 缺失"
    html = HTML.read_text(encoding="utf-8")
    assert html.count("<svg") == len(DIAGRAMS), "单页版内联的 SVG 数量与流程图数量不一致"
    for anchor in ANCHORS:
        assert f'id="{anchor}"' in html, f"单页版缺少锚点：{anchor}"
    assert "数据链路与鉴权实现手册.md" in html, "单页版缺少指向文字版手册的链接"
