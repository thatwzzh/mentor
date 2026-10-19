"""Nacos 访问令牌管理：带缓存的登录与提前 30 秒刷新。"""

import time
import asyncio
import httpx
from ..nacos_settings import nacos_settings

_access_token: str | None = None  # 缓存的 token
_expires_at = 0  # 基于 time.monotonic() 的过期时刻，避免系统时钟回拨
_token_lock = asyncio.Lock()


async def get_access_token() -> str:
    """获取 Nacos accessToken；命中缓存时直接返回，过期时串行刷新。"""
    global _access_token, _expires_at
    if _access_token and time.monotonic() < _expires_at:
        return _access_token

    # 双重检查锁：首个协程刷新 token，其余协程在锁内直接复用新值。
    async with _token_lock:
        if _access_token and time.monotonic() < _expires_at:
            return _access_token
        # 重新获取access_token
        _start_at = time.monotonic()
        async with httpx.AsyncClient(
            base_url=nacos_settings.server_address, timeout=10
        ) as client:
            response = await client.post(
                "/nacos/v3/auth/user/login",
                data={
                    "username": nacos_settings.username,
                    "password": nacos_settings.password,
                },
            )
            response.raise_for_status()
            login_result = response.json()
        token = login_result["accessToken"]
        ttl = float(login_result["tokenTtl"])
        _access_token = token
        _expires_at = _start_at + ttl - 30  # 过期时间留点余量保证可用
        return token
