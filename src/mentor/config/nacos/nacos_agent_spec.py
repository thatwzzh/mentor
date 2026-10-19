"""从 Nacos 读取 Agent 规格，并组装 Prompt、MCP 与 Skill 后返回 AgentSpec。"""

from mentor.config.nacos.nacos_access_token import get_access_token
from mentor.config.nacos_settings import nacos_settings
from mentor.config.nacos.nacos_mcp import get_mcps
from mentor.utils.func_tools import func_cache
from mentor.config.nacos.nacos_schemas import AgentSpec
from mentor.config.nacos.nacos_prompt import get_prompts
from mentor.config.nacos.nacos_skill import save_skills
import asyncio
import json
import httpx


# TTL 来自 NACOS_TTL；lazy=True 表示到期后先返回旧值，再在后台刷新。
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
    # 非 0 时当前实现退化为空规格（仅保留 name），调用方会得到一个无 Prompt/无工具的 Agent。
    if response["code"] != 0:
        return AgentSpec(name=agent_name)

    response = response["data"]
    config = response.get("content", {})
    config = json.loads(config)
    if "mcps" in config:  # 转换config的mcps的格式
        config["mcps"] = await get_mcps(config["mcps"])

    # Nacos 把 AGENTS.md 作为 Agent 资源保存，资源键格式固定为 instruction_<name>/AGENTS__MD。
    system_prompt = (
        response.get("resource", {})
        .get(f"instruction_{agent_name}/AGENTS__MD", {})
        .get("content", "")
    )
    config["system_prompt"] = system_prompt
    config["name"] = agent_name
    link_prompts = config.get("link_prompts", [])
    # link_prompts 指向 Nacos Prompt 模板，按声明顺序拼接在主 Prompt 之后。
    prompt = await get_prompts(link_prompts)
    config["system_prompt"] = system_prompt + "\n\n" + prompt
    skills = config.get("skills", [])
    # 注意：这是读路径上的副作用；每次缓存未命中都会重刷共享卷中的技能目录。
    await save_skills(agent_name, skills)
    return AgentSpec.model_validate(config)


if __name__ == "__main__":

    async def main():
        await get_agent_spec("main_agent")

    asyncio.run(main())
