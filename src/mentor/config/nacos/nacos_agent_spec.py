"""从 Nacos 读取 Agent 规格，并组装 Prompt、MCP 与 Skill 后返回 AgentSpec。"""

import asyncio
import json
import logging

import httpx
from pydantic import ValidationError

from mentor.config.nacos.nacos_access_token import get_access_token
from mentor.config.nacos.nacos_mcp import get_mcps
from mentor.config.nacos.nacos_prompt import get_prompts
from mentor.config.nacos.nacos_schemas import AgentSpec
from mentor.config.nacos_settings import nacos_settings
from mentor.utils.func_tools import func_cache

# Nacos 3.x 在资源不存在时可能返回 HTTP 404，也可能返回业务码 404/40400。
_NOT_FOUND_CODES = {404, 40400}


class AgentSpecError(RuntimeError):
    """Agent 规格读取失败的基类。"""


class AgentSpecNotFound(AgentSpecError):
    """Nacos 中不存在对应的 Agent 规格。"""


class AgentSpecUnavailable(AgentSpecError):
    """Nacos 不可用、鉴权失败或返回了非法内容。"""


def _error_code(payload: dict) -> int:
    try:
        return int(payload.get("code", -1))
    except (TypeError, ValueError):
        return -1


def _raise_for_payload(payload: dict, agent_name: str) -> None:
    """把 Nacos 业务错误码映射成明确的异常，避免静默返回空规格。"""
    code = _error_code(payload)
    if code == 0:
        return
    message = str(payload.get("message") or payload.get("msg") or "")
    if code in _NOT_FOUND_CODES:
        raise AgentSpecNotFound(f"Nacos 中不存在 Agent 规格: {agent_name}")
    raise AgentSpecUnavailable(
        f"读取 Nacos Agent 规格失败: {agent_name}, code={code}, message={message}"
    )


# TTL 来自 NACOS_TTL；lazy=True 表示到期后先返回旧值，再在后台刷新。
@func_cache(ttl=nacos_settings.ttl, lazy=True)
async def get_agent_spec(agent_name: str) -> AgentSpec:
    access_token = await get_access_token()
    try:
        async with httpx.AsyncClient(
            base_url=nacos_settings.server_address,
            headers={"accessToken": access_token},
            timeout=10,
        ) as client:
            response = await client.get(
                "/nacos/v3/client/ai/agentspecs",
                params={
                    "namespaceId": nacos_settings.namespace_id,
                    "name": agent_name,
                    "label": "production",
                },
            )
    except httpx.HTTPError as exc:
        raise AgentSpecUnavailable(f"请求 Nacos Agent 规格失败: {agent_name}") from exc

    if response.status_code == 404:
        raise AgentSpecNotFound(f"Nacos 中不存在 Agent 规格: {agent_name}")
    if response.status_code >= 500:
        raise AgentSpecUnavailable(
            f"Nacos 返回 HTTP {response.status_code}: {agent_name}"
        )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # 401/403 等 4xx 同样不能降级：没有鉴权就不知道配置是否真的存在。
        raise AgentSpecUnavailable(
            f"Nacos 返回 HTTP {response.status_code}: {agent_name}"
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise AgentSpecUnavailable(f"Nacos 返回非法 JSON: {agent_name}") from exc
    if not isinstance(payload, dict):
        raise AgentSpecUnavailable(f"Nacos 返回非法响应: {agent_name}")
    _raise_for_payload(payload, agent_name)

    data = payload.get("data")
    if not isinstance(data, dict):
        raise AgentSpecUnavailable(f"Nacos 响应缺少 data: {agent_name}")

    raw_config = data.get("content", {})
    try:
        config = json.loads(raw_config) if isinstance(raw_config, str) else raw_config
    except json.JSONDecodeError as exc:
        raise AgentSpecUnavailable(
            f"Nacos Agent 配置不是合法 JSON: {agent_name}"
        ) from exc
    if not isinstance(config, dict):
        raise AgentSpecUnavailable(f"Nacos Agent 配置必须是对象: {agent_name}")

    if "mcps" in config:  # 转换 config 中 mcps 的格式
        config["mcps"] = await get_mcps(config["mcps"])

    # Nacos 把 AGENTS.md 作为 Agent 资源保存，资源键格式固定为 instruction_<name>/AGENTS__MD。
    system_prompt = (
        (data.get("resource") or {})
        .get(f"instruction_{agent_name}/AGENTS__MD", {})
        .get("content", "")
    )
    config["system_prompt"] = system_prompt
    config["name"] = agent_name
    link_prompts = config.get("link_prompts") or []
    # link_prompts 指向 Nacos Prompt 模板，按声明顺序拼接在主 Prompt 之后。
    prompt = await get_prompts(link_prompts)
    config["system_prompt"] = system_prompt + "\n\n" + prompt

    try:
        return AgentSpec.model_validate(config)
    except ValidationError as exc:
        raise AgentSpecUnavailable(
            f"Nacos Agent 配置校验失败: {agent_name}"
        ) from exc


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    asyncio.run(get_agent_spec("main_agent"))
