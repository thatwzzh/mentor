"""从 Nacos 读取 Prompt 模板，并拼接多个关联 Prompt。"""

from mentor.config.nacos.nacos_access_token import get_access_token
from mentor.config.nacos_settings import nacos_settings
from mentor.utils.func_tools import func_cache
import asyncio
import httpx


@func_cache(ttl=nacos_settings.ttl, lazy=True)
async def get_prompt(prompt_name: str) -> str:
    access_token = await get_access_token()
    async with httpx.AsyncClient(
        base_url=nacos_settings.server_address, headers={"accessToken": access_token}
    ) as client:
        response = await client.get(
            "/nacos/v3/client/ai/prompt",
            params={
                "namespaceId": nacos_settings.namespace_id,
                "promptKey": prompt_name,
                "label": "production",
            },
        )
        response = response.json()
        if response["code"] != 0:
            return ""

    response = response["data"]
    return response["template"]


async def get_prompts(prompt_names: list[str]) -> str:
    # 按声明顺序并发读取；缺失的 Prompt 返回空串，仍会以空行形式参与拼接。
    tasks = [get_prompt(prompt_name) for prompt_name in prompt_names]
    prompts = await asyncio.gather(*tasks)
    return "\n\n".join(prompts)


if __name__ == "__main__":

    async def main():
        result = await get_prompts(["sandbox"])
        print(type(result), result)

    asyncio.run(main())
