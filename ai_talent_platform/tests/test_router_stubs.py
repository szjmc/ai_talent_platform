"""
模块：tests/test_router_stubs.py
职责：占位路由与权限码一致性守门测试。

标签：[架构组] [P1]（5 组垂直归口的占位模块 owner 校验）

为什么需要：
1. 前 3 个模块已实现，其余模块以 501 占位路由暴露；占位说明必须以「[主责研发组]」开头，
   否则前端/联调方无法判断该找哪个组对接（历史上出现过 404 与"负责人"口径不一）；
2. 文档（api_document.md 权限矩阵 / requirements_specification.md §3.2）声明的权限码
   必须真实存在于 PermissionEnum，且 `audit:read` 等只能授予管理员，防止文档先行、代码缺失；
3. 全部用例离线运行，不依赖 MySQL / Redis / Milvus / 真实 LLM。

实现思路：
- 直接读取 app/routers/router.py 源码中的 _STUB_MODULES 元组（避免 import FastAPI 应用）；
- 校验 owner 属于 5 个研发组，且与 group_boundaries_and_tasks.md 的垂直归口一致；
- 校验每个文档声明的权限码都在 PermissionEnum 中定义。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from app.core.permissions import ROLE_PERMISSIONS, PermissionEnum, RoleEnum

ROOT = Path(__file__).resolve().parent.parent
GROUPS = ("架构组", "RAG组", "Agent组", "Vanna组", "Dify组")

# 5 组垂直归口（与 docs/group_boundaries_and_tasks.md §一 一致）
EXPECTED_OWNER = {
    "positions": "Agent组",
    "matchings": "Agent组",
    "agents": "Agent组",
    "talents": "RAG组",
    "resumes": "RAG组",
    "knowledge": "RAG组",
    "assessments": "Vanna组",
    "analytics": "Vanna组",
    "trainings": "Dify组",
    "files": "架构组",
    "audit": "架构组",
}


def _stub_modules() -> list[tuple[str, str, str]]:
    """从 router.py 源码中取出 _STUB_MODULES 字面量（不 import FastAPI）。"""
    source = (ROOT / "app/routers/router.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "_STUB_MODULES":
            return ast.literal_eval(node.value)
    raise AssertionError("router.py 中未找到 _STUB_MODULES 定义")


def test_stub_modules_declare_valid_owner_group():
    """每个占位模块的说明必须以 5 组之一的「[组]」开头，且与垂直归口一致。"""
    problems: list[str] = []
    stubs = _stub_modules()
    prefixes = {p for p, _, _ in stubs}
    implemented_modules = {"talents", "analytics"}
    missing = set(EXPECTED_OWNER) - prefixes - implemented_modules
    if missing:
        problems.append(f"router.py 缺少占位模块注册：{sorted(missing)}")

    for prefix, tag, description in stubs:
        matched = re.match(r"^\[([^\]]+)\]", description)
        if not matched:
            problems.append(f"{prefix}（{tag}）：占位说明未以「[主责研发组]」开头 -> {description!r}")
            continue
        owner = matched.group(1)
        if owner not in GROUPS:
            problems.append(f"{prefix}（{tag}）：owner={owner!r} 不属于 5 个研发组")
        elif prefix in EXPECTED_OWNER and owner != EXPECTED_OWNER[prefix]:
            problems.append(
                f"{prefix}（{tag}）：owner={owner} 与垂直归口 {EXPECTED_OWNER[prefix]} 不一致"
            )
    assert not problems, "占位路由归属校验失败：\n" + "\n".join(problems)


def test_documented_permissions_exist_in_code():
    """SRS §3.2 声明的权限码必须全部在 PermissionEnum 中真实存在。"""
    srs = (ROOT / "docs/requirements_specification.md").read_text(encoding="utf-8")
    start = srs.index("### 3.2 ")
    end = srs.index("### 3.3 ")
    documented = set(re.findall(r"`([a-z]+:[a-z]+)`", srs[start:end]))
    defined = {item.value for item in PermissionEnum}
    assert documented, "SRS §3.2 未解析到任何权限码"
    assert not documented - defined, f"文档声明但代码缺失的权限码：{sorted(documented - defined)}"


def test_file_and_audit_permissions_granted_to_admin_only():
    """文件删除与审计查询属于敏感能力：仅 admin（及以上）可持有。"""
    admin = ROLE_PERMISSIONS[RoleEnum.ADMIN]
    for permission in (PermissionEnum.AUDIT_READ, PermissionEnum.FILE_DELETE):
        assert permission in admin, f"admin 缺少 {permission}"
        for role in (RoleEnum.HR, RoleEnum.MANAGER, RoleEnum.EMPLOYEE):
            assert permission not in ROLE_PERMISSIONS[role], f"{role} 不应持有 {permission}"
