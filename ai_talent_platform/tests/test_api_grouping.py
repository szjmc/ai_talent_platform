"""
模块：tests/test_api_grouping.py
职责：接口分组归属守门测试（防止「已实现接口挂错业务分组 / 遗留 501 占位」再次发生）。

标签：[架构组] [P1]（接口分组治理：文件管理、审计日志、岗位管理、人才邀约的归属与可达性）

为什么需要：
    多团队并行开发时，接口先随手挂在「知识库」下，事后没人搬 —— 于是出现
    「文件管理功能已完成，接口却躺在知识库分组，/files 分组只有 501 占位」。
    Swagger 是联调方的唯一契约来源，分组错位等于接口不存在。

本用例守住三件事：
    1. 每个业务模块的接口路径前缀与 Swagger 分组标签一一对应（禁止跨组挂载）；
    2. 曾经只挂占位的模块（files/audit/positions/invites）必须返回真实实现，不得是 501；
    3. 同一 path + method 不得重复注册（重复注册会让后注册者被静默覆盖，
       历史上 /files/upload 就是这样被知识库的隐藏别名吃掉的）。

全部离线运行：只读 OpenAPI，不连接 MySQL / Redis / Milvus / 真实 LLM。
"""

from __future__ import annotations

import json
from collections import defaultdict

import pytest

from app.config.settings import settings
from app.core.application import create_application

# 业务前缀 -> 期望的 Swagger 分组（唯一归属，禁止跨组）
EXPECTED_GROUP = {
    "auth": "认证",
    "users": "用户管理",
    "organizations": "组织架构",
    "talents": "人才档案",
    "resumes": "简历管理",
    "knowledge": "知识库",
    "ingestion": "知识入库",
    "positions": "岗位管理",
    "matchings": "人岗匹配",
    "trainings": "培训管理",
    "assessments": "人才测评",
    "invites": "人才邀约",
    "files": "文件管理",
    "audit": "审计日志",
    "analytics": "数据分析",
}

# 本轮从「只有 501 占位」切换为真实实现的模块
FORMERLY_STUBBED = ("files", "audit", "positions", "invites")


@pytest.fixture(scope="module")
def paths() -> dict:
    return create_application().openapi()["paths"]


def _iter_operations(paths: dict):
    for path, item in paths.items():
        for method, operation in item.items():
            if method.lower() in {"get", "post", "put", "patch", "delete", "head", "options"}:
                yield path, method.upper(), operation


def test_business_prefix_maps_to_expected_group(paths):
    """每个业务前缀只能出现在自己的分组里（跨组挂载即失败）。"""
    problems: list[str] = []
    for path, method, operation in _iter_operations(paths):
        relative = path.removeprefix(settings.API_PREFIX).lstrip("/")
        prefix = relative.split("/")[0]
        expected = EXPECTED_GROUP.get(prefix)
        if expected is None:
            continue
        tags = operation.get("tags") or []
        if tags != [expected]:
            problems.append(f"{method} {path} 分组为 {tags}，期望 ['{expected}']")
    assert not problems, "接口分组归属错误：\n" + "\n".join(problems)


def test_formerly_stubbed_modules_are_implemented(paths):
    """原占位模块必须有真实接口，且不再存在 501 占位入口。

    不假设「前缀根路径一定有路由」：占位路由是 `GET /{prefix}`，真实实现可以只提供
    子路径（如 /audit/logs、/audit/stats）。因此这里只校验「前缀下存在真实操作，
    且没有任何操作自称占位或声明 501」。
    """
    for prefix in FORMERLY_STUBBED:
        base = f"{settings.API_PREFIX}/{prefix}"
        operations = [
            operation for path, _method, operation in _iter_operations(paths)
            if path == base or path.startswith(base + "/")
        ]
        assert operations, f"{prefix} 仍未注册任何接口"
        for operation in operations:
            summary = operation.get("summary") or ""
            description = operation.get("description") or ""
            assert "占位" not in summary + description, f"{prefix} 仍是 501 占位：{summary}"
            assert operation.get("responses", {}).get("501") is None, f"{prefix} 仍声明 501 响应"


def test_no_duplicate_path_method_registration(paths):
    """同一 path + method 只能有一个实现（重复注册会静默覆盖先注册者）。"""
    # OpenAPI 天然去重，因此直接检查应用路由表，才能发现被覆盖的重复注册。
    app = create_application()
    seen: dict[tuple[str, str], int] = defaultdict(int)
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            seen[(getattr(route, "path", ""), method)] += 1
    duplicates = {key: count for key, count in seen.items() if count > 1}
    assert not duplicates, f"重复注册的路由：{json.dumps(duplicates, ensure_ascii=False)}"


def test_file_upload_belongs_to_file_module_only(paths):
    """`/files/upload` 必须只属于文件管理（不能既做知识上传又做通用上传）。"""
    operation = paths[f"{settings.API_PREFIX}/files/upload"]["post"]
    assert operation["tags"] == ["文件管理"]
    body_ref = json.dumps(operation.get("requestBody", {}), ensure_ascii=False)
    assert "biz_type" in body_ref or "multipart" in body_ref
    # 知识库上传保持自己的路径，两者参数契约不同（category vs biz_type），不可混用
    knowledge = paths[f"{settings.API_PREFIX}/knowledge/docs/upload"]["post"]
    assert knowledge["tags"] == ["知识库"]
