import time
import asyncio
import httpx
from urllib3 import response
from ..nacos_settings import nacos_settings

_access_token: str | None = None  # 缓存的token
_expires_at = 0  # 过期时间
_token_lock = asyncio.Lock()


async def get_access_token() -> str:
    "调用函数，获取access_token"
    global _access_token, _expires_at
    if _access_token and time.monotonic() < _expires_at:  # 有值且没过期
        return _access_token

    # 同一时刻只能有一个协程进入所获取token
    async with _token_lock:
        if _access_token and time.monotonic() < _expires_at:  # 有值且没过期
            return _access_token
        # 重新获取access_token
        _start_at = time.monotonic()
        async with httpx.AsyncClient(
            base_url=nacos_settings.server_address, timeout=10
        ) as client:
            response = await client.post(
                "/nanos/v3/user/login",
                data={
                    "username": nacos_settings.username,
                    "password": nacos_settings.password,
                },
            )
            response.raise_for_status()
            login_result = response.json()
        token = login_result["access_token"]
        ttl = float(login_result["tokenTtl"])
        _access_token = token
        _expires_at = _start_at + ttl - 30  # 过期时间留点余量保证可用
        return token
