"""
模块：tests/test_code_tags.py
职责：代码标签合规校验（CI 守门，离线、零第三方依赖）。

标签：[架构组] [P1]（CI 守门：全部代码文件必须带 组标签 + 优先级）

校验规则（与 docs/plan/05-代码标签与优先级总表.md 一致）：
1. app/ scripts/ tests/ 下每个 .py 文件（含 main.py）的模块 docstring 中
   必须有一行以「标签：」开头，格式：
       「标签：[组] [P0|P1|P2]」（备注可选）
       「标签：[组A]+[组B] [P1]  依赖：……」（跨组共建文件用双标签）
2. 组标签只允许：架构组 / RAG组 / Agent组 / Vanna组 / Dify组 / 业务组；
3. 根目录 4 个配置文件（requirements.txt / docker-compose.yml /
   .env.example / pytest.ini）前 10 行内必须有「# 标签：」注释；
4. 新增代码文件漏打标签 => 本测试失败，禁止合并。

运行：pytest tests/test_code_tags.py -q
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

GROUPS = ("架构组", "RAG组", "Agent组", "Vanna组", "Dify组", "业务组")
_GROUP = r"\[(?:" + "|".join(GROUPS) + r")\]"
TAG_LINE_RE = re.compile(rf"^标签：{_GROUP}(?:\+{_GROUP})* \[P[012]\]")

CONFIG_FILES = (
    "requirements.txt",
    "docker-compose.yml",
    ".env.example",
    "pytest.ini",
)


def _iter_code_files():
    """全部 Python 代码文件：app / scripts / tests / main.py。"""
    for folder in ("app", "scripts", "tests"):
        yield from sorted((ROOT / folder).rglob("*.py"))
    yield ROOT / "main.py"


def _tag_lines(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    doc = ast.get_docstring(ast.parse(source))
    if not doc:
        return []
    return [line.strip() for line in doc.splitlines() if line.strip().startswith("标签：")]


def test_every_python_file_has_valid_tag():
    """每个 .py 文件的模块 docstring 必须带合法「标签：[组] [Px]」。"""
    problems: list[str] = []
    for path in _iter_code_files():
        rel = path.relative_to(ROOT)
        tags = _tag_lines(path)
        if not tags:
            problems.append(f"{rel}: 模块 docstring 缺少「标签：」行")
        elif len(tags) > 1:
            problems.append(f"{rel}: 「标签：」行重复（{len(tags)} 行）")
        elif not TAG_LINE_RE.match(tags[0]):
            problems.append(f"{rel}: 标签格式非法 -> {tags[0]!r}")
    assert not problems, (
        "代码标签合规校验失败（规范见 docs/plan/05-代码标签与优先级总表.md）：\n"
        + "\n".join(problems)
    )


def test_root_config_files_have_tag_comment():
    """根目录配置文件前 10 行内必须带「# 标签：」注释。"""
    problems: list[str] = []
    for name in CONFIG_FILES:
        path = ROOT / name
        head = path.read_text(encoding="utf-8").splitlines()[:10]
        if not any(line.startswith("# 标签：") for line in head):
            problems.append(f"{name}: 前 10 行缺少「# 标签：」注释")
    assert not problems, "配置文件标签缺失：\n" + "\n".join(problems)


def test_tag_summary_printable():
    """标签可被机器统计（组 / 优先级），供架构组集成报告使用。"""
    by_group: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    for path in _iter_code_files():
        tags = _tag_lines(path)
        assert tags, f"{path.relative_to(ROOT)} 无标签"
        m = TAG_LINE_RE.match(tags[0])
        assert m, f"{path.relative_to(ROOT)} 标签非法"
        for group in re.findall(r"\[([^\]]+)\]", tags[0].split("依赖：")[0]):
            if group in GROUPS:
                by_group[group] = by_group.get(group, 0) + 1
        pri = re.search(r"\[(P[012])\]", tags[0]).group(1)
        by_priority[pri] = by_priority.get(pri, 0) + 1
    # 六个组都应有文件（双标签按两组各计一次）
    assert set(by_group) == set(GROUPS), f"存在没有任何文件的组：{set(GROUPS) - set(by_group)}"
    assert set(by_priority) == {"P0", "P1", "P2"}
