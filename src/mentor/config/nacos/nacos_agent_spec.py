from mentor.config.nacos.nacos_access_token import get_access_token
from mentor.config.nacos_settings import nacos_settings
from mentor.config.nacos.nacos_mcp import get_mcps
from mentor.utils.func_tools import func_cache
from mentor.config.nacos.nacos_schemas import AgentSpec
import asyncio
import json
import httpx


@func_cache(ttl=nacos_settings.ttl, lazy=True)
async def get_agent_spec(agent_name: str) -> AgentSpec:
    access_token = await get_access_token()
    async with httpx.AsyncClient(
        base_url=nacos_settings.server_address, headers={"accessToken": access_token}
    ) as client:
        response = await client.get(
            "/nacos/v3/client/ai/agentspecs",
            params={
                "namespaceId": nacos_settings.namespace_id,
                "name": agent_name,
                "label": "production",
            },
        )
        response = response.json()
    if response["code"] != 0:
        return AgentSpec()

    response = response["data"]
    config = response.get("content", {})
    config = json.loads(config)
    if "mcps" in config:  # 转换config的mcps的格式
        config["mcps"] = await get_mcps(config["mcps"])

    system_prompt = (
        response.get("resource", {})
        .get(f"instruction_{agent_name}/AGENTS__MD", {})
        .get("content", "")
    )
    config["system_prompt"] = system_prompt
    return AgentSpec.model_validate(config)


if __name__ == "__main__":

    async def main():
        await get_agent_spec("main_agent")

    asyncio.run(main())
