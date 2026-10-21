"""从 Nacos 读取 YAML 配置，并按 Pydantic 模型解析。

读取失败分两类：配置不存在抛 ConfigNotFoundError，Nacos 不可用/内容非法抛
ConfigUnavailableError。调用方不应再用默认值静默吞掉第二类错误。
"""

import asyncio
from typing import Literal

import httpx
import yaml
from pydantic import BaseModel, ValidationError

from mentor.config.errors import ConfigNotFoundError, ConfigUnavailableError
from mentor.config.nacos.nacos_access_token import get_access_token
from mentor.config.nacos.nacos_schemas import OpenSandboxConfig
from mentor.config.nacos_settings import nacos_settings
from mentor.utils.func_tools import func_cache

# 允许读取的配置名；新增配置时需要同时扩展此 Literal 和对应 Pydantic 模型。
type ConfigName = Literal["sandbox_config"]
# Nacos 侧统一使用 mentor 分组，dataId 即配置名。
_GROUP_NAME = "mentor"
_NOT_FOUND_CODES = {404, 40400}


@func_cache(ttl=nacos_settings.ttl, lazy=True)
async def _get_config_content(config_name: ConfigName) -> str:
    access_token = await get_access_token()
    try:
        async with httpx.AsyncClient(
            base_url=nacos_settings.server_address,
            headers={"accessToken": access_token},
            timeout=5,
        ) as client:
            response = await client.get(
                "/nacos/v3/client/cs/config",
                params={
                    "namespaceId": nacos_settings.namespace_id,
                    "groupName": _GROUP_NAME,
                    "dataId": config_name,
                },
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise ConfigNotFoundError(f"Nacos 配置不存在: {config_name}") from exc
        raise ConfigUnavailableError(
            f"Nacos 配置读取失败: {config_name}, HTTP {exc.response.status_code}"
        ) from exc
    except httpx.HTTPError as exc:
        raise ConfigUnavailableError(
            f"Nacos 配置请求失败: {config_name}"
        ) from exc
    except ValueError as exc:
        raise ConfigUnavailableError(
            f"Nacos 配置返回非法 JSON: {config_name}"
        ) from exc

    code = payload.get("code", -1) if isinstance(payload, dict) else -1
    if code in _NOT_FOUND_CODES:
        raise ConfigNotFoundError(f"Nacos 配置不存在: {config_name}")
    if code != 0:
        raise ConfigUnavailableError(
            f"Nacos 配置读取失败: {config_name}, code={code}"
        )
    data = payload.get("data")
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ConfigNotFoundError(f"Nacos 配置内容为空: {config_name}")
    return content


async def get_config[T: BaseModel](config_name: ConfigName, schema: type[T]) -> T:
    content = await _get_config_content(config_name)
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ConfigUnavailableError(
            f"Nacos 配置不是合法 YAML: {config_name}"
        ) from exc
    if not isinstance(data, dict):
        raise ConfigUnavailableError(f"Nacos 配置必须是对象: {config_name}")
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise ConfigUnavailableError(
            f"Nacos 配置校验失败: {config_name}"
        ) from exc


if __name__ == "__main__":
    result = asyncio.run(get_config("sandbox_config", OpenSandboxConfig))
    import rich

    rich.print(result)
