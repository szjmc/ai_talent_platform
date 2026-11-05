"""
模块：app/core/permissions.py
职责：RBAC 权限模型与校验工具。

标签：[架构组] [P0]（RBAC + 数据范围，安全红线）

实现思路：
1. 系统内置角色枚举 RoleEnum；每个角色映射一组权限码 PermissionEnum（字符串常量）；
2. 权限码命名规范：`模块:动作`，如 talent:create、position:read、analytics:export；
3. 超级管理员（super_admin）默认拥有全部权限（通配符 *）；
4. 提供纯函数接口，方便在 Service 或依赖中复用：
     has_permission(user_roles, required)     单个权限判断
     has_any_permission / has_all_permissions  多权限判断
     check_permission(...)                     失败抛 PermissionDeniedError
     get_data_scope(roles)                     数据范围（全部/本部门/本部门及下级/仅本人）
     check_data_scope(...)                     单条数据范围校验（越权抛 PermissionDeniedError）
     visible_organization_ids(...)             列表过滤：范围 -> 可见部门 ID 集合
5. 真正的「登录 + 权限」依赖注入在 core/dependencies.py：
     Depends(require_permission(PermissionEnum.TALENT_CREATE))

数据范围（data scope）说明：
    ALL      全部数据（超管、最高管理者）
    DEPT     本部门
    DEPT_SUB 本部门及下级部门
    SELF     仅本人创建/本人数据
Service 层在查询时把范围条件交给 Repository 落地，禁止在 Router 里写。
"""

from __future__ import annotations

from enum import StrEnum

from app.core.exceptions import PermissionDeniedError


class RoleEnum(StrEnum):
    """系统内置角色。"""

    SUPER_ADMIN = "super_admin"     # 超级管理员
    ADMIN = "admin"                 # 系统管理员
    HR = "hr"                       # 人力资源
    MANAGER = "manager"             # 部门管理者
    EMPLOYEE = "employee"           # 普通员工


class PermissionEnum(StrEnum):
    """权限码（模块:动作）。"""

    # 用户与组织
    USER_READ = "user:read"
    USER_CREATE = "user:create"
    USER_UPDATE = "user:update"
    USER_DELETE = "user:delete"
    ORG_READ = "organization:read"
    ORG_MANAGE = "organization:manage"
    # 人才与岗位
    TALENT_READ = "talent:read"
    TALENT_CREATE = "talent:create"
    TALENT_UPDATE = "talent:update"
    TALENT_DELETE = "talent:delete"
    POSITION_READ = "position:read"
    POSITION_MANAGE = "position:manage"
    # 简历 / 测评 / 培训 / 匹配
    RESUME_UPLOAD = "resume:upload"
    RESUME_PARSE = "resume:parse"
    ASSESSMENT_READ = "assessment:read"
    ASSESSMENT_MANAGE = "assessment:manage"
    TRAINING_READ = "training:read"
    TRAINING_MANAGE = "training:manage"
    MATCHING_READ = "matching:read"
    MATCHING_RUN = "matching:run"
    # 知识库 / 分析 / AI
    KNOWLEDGE_READ = "knowledge:read"
    KNOWLEDGE_MANAGE = "knowledge:manage"
    ANALYTICS_READ = "analytics:read"
    ANALYTICS_EXPORT = "analytics:export"
    AGENT_USE = "agent:use"
    # 文件中心 / 全局审计（[架构组] 平台横向服务）
    FILE_UPLOAD = "file:upload"
    FILE_DELETE = "file:delete"
    AUDIT_READ = "audit:read"


class DataScopeEnum(StrEnum):
    """数据范围。"""

    ALL = "all"
    DEPT = "dept"
    DEPT_SUB = "dept_sub"
    SELF = "self"


# 角色 -> 权限集合（可按业务继续扩展，或改为数据库配置）
ROLE_PERMISSIONS: dict[str, set[str]] = {
    RoleEnum.SUPER_ADMIN: {"*"},
    RoleEnum.ADMIN: {
        PermissionEnum.USER_READ, PermissionEnum.USER_CREATE,
        PermissionEnum.USER_UPDATE, PermissionEnum.USER_DELETE,
        PermissionEnum.ORG_READ, PermissionEnum.ORG_MANAGE,
        PermissionEnum.TALENT_READ, PermissionEnum.TALENT_CREATE,
        PermissionEnum.TALENT_UPDATE, PermissionEnum.TALENT_DELETE,
        PermissionEnum.POSITION_READ, PermissionEnum.POSITION_MANAGE,
        PermissionEnum.RESUME_UPLOAD, PermissionEnum.RESUME_PARSE,
        PermissionEnum.ASSESSMENT_READ, PermissionEnum.ASSESSMENT_MANAGE,
        PermissionEnum.TRAINING_READ, PermissionEnum.TRAINING_MANAGE,
        PermissionEnum.MATCHING_READ, PermissionEnum.MATCHING_RUN,
        PermissionEnum.KNOWLEDGE_READ, PermissionEnum.KNOWLEDGE_MANAGE,
        PermissionEnum.ANALYTICS_READ, PermissionEnum.ANALYTICS_EXPORT,
        PermissionEnum.AGENT_USE,
        PermissionEnum.FILE_UPLOAD, PermissionEnum.FILE_DELETE,
        PermissionEnum.AUDIT_READ,
    },
    RoleEnum.HR: {
        PermissionEnum.USER_READ,
        PermissionEnum.ORG_READ,
        PermissionEnum.TALENT_READ, PermissionEnum.TALENT_CREATE,
        PermissionEnum.TALENT_UPDATE, PermissionEnum.TALENT_DELETE,
        PermissionEnum.POSITION_READ, PermissionEnum.POSITION_MANAGE,
        PermissionEnum.RESUME_UPLOAD, PermissionEnum.RESUME_PARSE,
        PermissionEnum.ASSESSMENT_READ, PermissionEnum.ASSESSMENT_MANAGE,
        PermissionEnum.TRAINING_READ, PermissionEnum.TRAINING_MANAGE,
        PermissionEnum.MATCHING_READ, PermissionEnum.MATCHING_RUN,
        PermissionEnum.KNOWLEDGE_READ,
        PermissionEnum.ANALYTICS_READ,
        PermissionEnum.AGENT_USE,
        PermissionEnum.FILE_UPLOAD,
    },
    RoleEnum.MANAGER: {
        PermissionEnum.USER_READ,
        PermissionEnum.ORG_READ,
        PermissionEnum.TALENT_READ,
        PermissionEnum.POSITION_READ,
        PermissionEnum.ASSESSMENT_READ,
        PermissionEnum.TRAINING_READ,
        PermissionEnum.MATCHING_READ,
        PermissionEnum.KNOWLEDGE_READ,
        PermissionEnum.ANALYTICS_READ,
        PermissionEnum.AGENT_USE,
    },
    RoleEnum.EMPLOYEE: {
        PermissionEnum.ORG_READ,
        PermissionEnum.POSITION_READ,
        PermissionEnum.TRAINING_READ,
        PermissionEnum.KNOWLEDGE_READ,
        PermissionEnum.AGENT_USE,
    },
}

# 角色 -> 默认数据范围
ROLE_DATA_SCOPE: dict[str, str] = {
    RoleEnum.SUPER_ADMIN: DataScopeEnum.ALL,
    RoleEnum.ADMIN: DataScopeEnum.ALL,
    RoleEnum.HR: DataScopeEnum.ALL,
    RoleEnum.MANAGER: DataScopeEnum.DEPT_SUB,
    RoleEnum.EMPLOYEE: DataScopeEnum.SELF,
}

WILDCARD = "*"


# ============================================================== 纯函数
def get_permissions_by_roles(roles: list[str] | set[str] | None) -> set[str]:
    """汇总角色对应的全部权限。"""
    perms: set[str] = set()
    for role in roles or []:
        perms |= set(ROLE_PERMISSIONS.get(str(role), set()))
    return perms


def is_super_admin(roles: list[str] | set[str] | None) -> bool:
    return RoleEnum.SUPER_ADMIN in {str(r) for r in (roles or [])}


def has_permission(roles: list[str] | set[str] | None, required: str | PermissionEnum) -> bool:
    """判断角色集合是否具备某个权限。"""
    perms = get_permissions_by_roles(roles)
    if WILDCARD in perms:
        return True
    return str(required) in perms


def has_any_permission(
    roles: list[str] | set[str] | None, required: list[str | PermissionEnum]
) -> bool:
    return any(has_permission(roles, p) for p in required)


def has_all_permissions(
    roles: list[str] | set[str] | None, required: list[str | PermissionEnum]
) -> bool:
    return all(has_permission(roles, p) for p in required)


def check_permission(roles: list[str] | set[str] | None, required: str | PermissionEnum) -> None:
    """校验权限，不足时抛 PermissionDeniedError。"""
    if not has_permission(roles, required):
        raise PermissionDeniedError(f"缺少权限：{required}")


def get_data_scope(roles: list[str] | set[str] | None) -> str:
    """取最高数据范围（多个角色取最宽的那个）。"""
    priority = [DataScopeEnum.ALL, DataScopeEnum.DEPT_SUB, DataScopeEnum.DEPT, DataScopeEnum.SELF]
    scopes = {ROLE_DATA_SCOPE.get(str(r), DataScopeEnum.SELF) for r in (roles or [])}
    for scope in priority:
        if scope in scopes:
            return scope
    return DataScopeEnum.SELF


def check_data_scope(
    roles: list[str] | set[str] | None,
    *,
    owner_id: int | None,
    current_user_id: int,
    dept_in_scope: bool = True,
) -> None:
    """数据范围校验（单条数据场景）。

    :param owner_id: 数据归属人 ID
    :param current_user_id: 当前登录用户 ID
    :param dept_in_scope: 该数据所属部门是否在当前用户可管部门范围内（由 Service 计算）
    """
    scope = get_data_scope(roles)
    if scope == DataScopeEnum.ALL:
        return
    if scope == DataScopeEnum.SELF:
        if owner_id != current_user_id:
            raise PermissionDeniedError("无权访问他人数据")
        return
    if not dept_in_scope:
        raise PermissionDeniedError("无权访问其他部门数据")


def visible_organization_ids(
    roles: list[str] | set[str] | None,
    organization_id: int | None,
    subtree_org_ids: set[int] | list[int] | None = None,
) -> set[int] | None:
    """行级数据范围 -> 可见部门 ID 集合（列表 / 详情查询的过滤条件来源）。

    :param roles: 当前用户角色（多角色取最宽范围）
    :param organization_id: 当前用户所属部门 ID
    :param subtree_org_ids: 本部门及下级的部门 ID 集合（Service 用
        organization_repository.list_subtree 预先取出；dept 范围不使用）
    :return:
        None        -> 不限制（ALL，含 super_admin 的角色配置）
        set[int]    -> 仅这些部门可见（dept = {本部门}；dept_sub = 本部门 + 下级）
        set()       -> 无可见部门（SELF 范围，或 dept/dept_sub 但本人无部门），
                       调用方应退化为「仅本人数据」

    典型用法（Service）：
        org_ids = visible_organization_ids(user.role_codes, user.organization_id, subtree)
        if org_ids is None:
            ...不过滤
        elif org_ids:
            extra_where.append(Model.organization_id.in_(org_ids))
        else:
            extra_where.append(<仅本人条件>)
    """
    scope = get_data_scope(roles)
    if scope == DataScopeEnum.ALL:
        return None
    if scope == DataScopeEnum.SELF:
        return set()
    if not organization_id:
        return set()
    if scope == DataScopeEnum.DEPT:
        return {int(organization_id)}
    # DEPT_SUB：本部门 + 下级（subtree 未提供时至少保证本部门可见）
    ids = {int(organization_id)}
    ids.update(int(i) for i in (subtree_org_ids or ()))
    return ids
