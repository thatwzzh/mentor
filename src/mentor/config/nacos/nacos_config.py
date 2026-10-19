"""从 Nacos 读取 YAML 配置，并按 Pydantic 模型解析。"""

from typing import Literal
from pydantic import BaseModel
from mentor.config.nacos.nacos_schemas import OpenSandboxConfig
from mentor.utils.func_tools import func_cache
from mentor.config.nacos_settings import nacos_settings
import asyncio
import httpx
from mentor.config.nacos.nacos_access_token import get_access_token
import yaml

# 允许读取的配置名；新增配置时需要同时扩展此 Literal 和对应 Pydantic 模型。
type ConfigName = Literal["sandbox_config"]
# Nacos 侧统一使用 mentor 分组，dataId 即配置名。
_GROUP_NAME = "mentor"


@func_cache(ttl=nacos_settings.ttl, lazy=True)
async def _get_config_content(config_name: ConfigName) -> str | None:
    access_token = await get_access_token()
    async with httpx.AsyncClient(
        base_url=nacos_settings.server_address, headers={"accessToken": access_token}
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
        response = response.json()
    if response["code"] != 0:
        return None
    response = response["data"]["content"]
    return response


async def get_config[T: BaseModel](config_name: ConfigName, schema: type[T]) -> T:
    content = await _get_config_content(config_name)
    # 配置缺失时返回模型默认值，保证调用方始终拿到一个可用对象。
    if content is None:
        return schema()
    data = yaml.safe_load(content)
    return schema.model_validate(data)


if __name__ == "__main__":
    result = asyncio.run(get_config("sandbox_config", OpenSandboxConfig))
    import rich

    rich.print(result)
