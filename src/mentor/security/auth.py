"""LangGraph 鉴权：校验 Bearer Token，按资源动作授权，并注入资源 owner。"""

from typing import Never

from langgraph_sdk import Auth
# Demo 用户表：生产环境应替换为用户中心/JWT claims，避免明文 token。
from mentor.utils.mock_user import MOCK_USER

auth = Auth()


def _raise_invalid_token() -> Never:
    raise Auth.exceptions.HTTPException(status_code=401, detail="invalid token")


async def get_permissions_by_user_id(user_id: str) -> list[str]:
    """按用户 ID 查询当前权限。

    生产环境应改为查询用户中心；cron 调度时仅保存 user_id，因此会通过此处
    重新获取权限，而不是信任客户端提交的权限数据。
    """
    for user in MOCK_USER.values():
        # 线性扫描仅适用于 Demo；真实实现应按 user_id 建索引或调用用户中心。
        if user["identity"] == user_id:
            permissions = list(user.get("permissions", []))
            return permissions
    return []


async def get_current_permissions(ctx: Auth.types.AuthContext) -> list[str]:
    """优先使用当前请求的权限；后台 cron 则按用户 ID 重新查询。"""
    if ctx.permissions:
        return list(ctx.permissions)
    return await get_permissions_by_user_id(ctx.user.identity)


@auth.authenticate
async def authenticate(
    authorization: str | None,
) -> Auth.types.MinimalUserDict:
    """验证 Authorization: Bearer <token> 并返回当前用户。"""
    if not authorization:
        _raise_invalid_token()
    schema, sep, token = authorization.partition(" ")
    # 当前只按空格切分，没有校验 schema 必须为 Bearer；生产实现需补上。
    if not token:
        _raise_invalid_token()
    # 解析token，通常要访问后端web服务（用户中心）
    user = MOCK_USER.get(token)
    if user is None:
        _raise_invalid_token()

    return {
        **user,
        "permissions": await get_permissions_by_user_id(user["identity"]),
    }


@auth.on.store
async def on_store(ctx: Auth.types.AuthContext, value: dict):
    permission = f"{ctx.resource}:{ctx.action}"
    if permission not in await get_current_permissions(ctx):
        return False
    namespace = tuple(value["namespace"]) if value["namespace"] else ()
    # Store 按命名空间做租户隔离：第一段必须等于当前用户；内部命名空间需另行白名单。
    if not namespace or namespace[0] != ctx.user.identity:
        return False
    return True


@auth.on.assistants
async def on_assistants(ctx: Auth.types.AuthContext, value: dict):
    # 只校验动作权限，没有按 owner/tenant 过滤；多租户场景需要补上。
    permission = f"{ctx.resource}:{ctx.action}"
    if permission not in await get_current_permissions(ctx):
        return False
    return True


@auth.on
async def authorization(ctx: Auth.types.AuthContext, value: dict):
    permission = f"{ctx.resource}:{ctx.action}"
    if permission not in await get_current_permissions(ctx):
        return False
    metadata = value.setdefault("metadata", {})
    metadata["owner"] = ctx.user.identity

    # 返回过滤条件：LangGraph 会把 owner 追加到资源查询条件中。
    return {"owner": ctx.user.identity}
