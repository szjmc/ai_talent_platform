"""
gxd-人才模块可信身份构造与权限校验，不接受请求体提供角色。

标签：[RAG组] [P1]（人才数据访问与权限边界）
"""
from app.ai.context import AIContext, get_context
from app.core.exceptions import AuthError

def actor_context(user=None, context=None):
    """gxd-从认证用户或内部上下文构造身份，匿名访问业务数据时拒绝。"""
    if user is not None:
        roles = list(user.role_codes)
        if user.is_superuser and "super_admin" not in roles:
            roles.append("super_admin")
        context = AIContext(user_id=user.id, roles=roles, organization_id=user.organization_id)
    result = context or get_context()
    if not result.user_id:
        raise AuthError()
    return result