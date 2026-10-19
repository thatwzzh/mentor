"""从 Nacos 读取 MCP 服务地址与工具启停信息。"""

from mentor.config.nacos.nacos_schemas import MCPServerConfig
from mentor.config.nacos.nacos_access_token import get_access_token
from mentor.config.nacos_settings import nacos_settings
from mentor.utils.func_tools import func_cache
import asyncio
import httpx


@func_cache(ttl=nacos_settings.ttl, lazy=True)
async def get_mcp_config(mcp_name: str) -> MCPServerConfig | None:

    access_token = await get_access_token()
    async with httpx.AsyncClient(
        base_url=nacos_settings.server_address, headers={"accessToken": access_token}
    ) as client:
        response = await client.get(
            # 注意：这里调用的是 admin 接口，需要管理员权限；只读场景可改用 client 接口。
            "/nacos/v3/admin/ai/mcp",
            params={"namespaceId": nacos_settings.namespace_id, "mcpName": mcp_name},
        )
        response = response.json()
        if response["code"] != 0:
            return None

    response = response["data"]
    endpoints = response.get("backendEndpoints", [])
    if len(endpoints) == 0:
        return None
    # 当前只取第一个后端端点；多实例负载均衡由 MCP 网关负责，这里不做选择。
    backend = endpoints[0]
    url = f"{backend["protocol"]}://{backend["address"]}:{backend["port"]}{backend["path"]}"

    tool_spec = response.get("toolSpec") or {}
    tools_meta = tool_spec.get("toolsMeta") or {}
    # toolsMeta 里 enabled=false 的工具会进入禁用列表，由 AgentFactory 过滤。
    disabled_tools = [
        name for name, value in tools_meta.items() if not value.get("enabled", True)
    ]
    return MCPServerConfig.model_validate(
        {"url": url, "disabled_tools": disabled_tools, "name": mcp_name}
    )


async def get_mcps(mcp_names: list[str]) -> dict[str, MCPServerConfig]:
    tasks = [get_mcp_config(mcp_name) for mcp_name in mcp_names]
    mcp_configs = await asyncio.gather(*tasks)
    return {config.name: config for config in mcp_configs if config is not None}


if __name__ == "__main__":

    async def main():
        await get_mcps(["langchain-docs", "microsoft-docs"])

    asyncio.run(main())
