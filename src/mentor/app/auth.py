"""自定义 HTTP 路由的 Bearer 身份校验。"""

from fastapi import HTTPException

from mentor.security.auth import authenticate


async def require_user(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="invalid authorization")
    try:
        user = await authenticate(authorization)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc
    identity = user.get("identity")
    if not identity:
        raise HTTPException(status_code=401, detail="invalid token")
    return str(identity)
