"""异步子代理工具：spawn / check / cancel。"""

import json

from langchain.tools import tool

from mentor.agents.async_subagent import (
    SubAgentSpec,
    get_async_subagent_runner,
)
from mentor.config.subagent_settings import subagent_settings
from mentor.tools.specs import ToolSpec, register_tool


@register_tool(risk="external", timeout_s=10)
@tool
async def spawn_subagent(agent_name: str, task: str) -> str:
    """启动一个只读后台子代理，立即返回 job_id。

    仅用于独立、耗时、可稍后取结果的只读任务（调研、分析、评审、摘要）。
    不要用于写入/部署/删除、强顺序任务、必须当前轮拿结果的任务。
    启动后保留 job_id，稍后用 check_subagent 查询。
    """
    runner = get_async_subagent_runner()
    job = await runner.spawn(
        agent_name,
        task,
        spec=SubAgentSpec(
            name=agent_name,
            description="",
            max_depth=subagent_settings.max_depth,
            timeout_seconds=subagent_settings.timeout_seconds,
        ),
    )
    return json.dumps(runner.snapshot(job), ensure_ascii=False)


@register_tool(risk="read", timeout_s=5)
@tool
async def check_subagent(job_id: str) -> str:
    """查询后台子代理状态与结果；结果最多返回 2,000 字符摘要。"""
    runner = get_async_subagent_runner()
    job = runner.get(job_id)
    if job is None:
        return json.dumps({"job_id": job_id, "status": "not_found"}, ensure_ascii=False)
    return json.dumps(runner.snapshot(job), ensure_ascii=False)


@register_tool(risk="external", timeout_s=5)
@tool
async def cancel_subagent(job_id: str) -> str:
    """取消一个仍在运行的后台子代理；已结束的任务返回 cancelled=false。"""
    runner = get_async_subagent_runner()
    cancelled = await runner.cancel(job_id)
    return json.dumps(
        {"job_id": job_id, "cancelled": cancelled}, ensure_ascii=False
    )


def async_subagent_tool_specs() -> list[ToolSpec]:
    from mentor.tools.specs import app_specs

    specs = app_specs()
    return [
        spec
        for name in ("spawn_subagent", "check_subagent", "cancel_subagent")
        if (spec := specs.get(name)) is not None
    ]
