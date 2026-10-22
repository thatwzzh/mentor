"""5.7 异步子代理：后台执行、结果、超时、深度限制。"""

import asyncio

import pytest
from langchain_core.messages import AIMessage

from mentor.agents.async_subagent import (
    AsyncSubAgentRunner,
    SubAgentSpec,
)
from mentor.agents.subagent_registry import register_subagent
from mentor.utils.subagent_context import (
    get_subagent_depth,
    reset_subagent_depth,
    set_subagent_depth,
)


class FakeRunnable:
    def __init__(self, content: str = "subagent result", delay: float = 0) -> None:
        self.content = content
        self.delay = delay

    async def ainvoke(self, state, config=None):
        await asyncio.sleep(self.delay)
        return {"messages": [AIMessage(content=self.content)]}


@register_subagent("test_async")  # type: ignore[arg-type]
async def fake_factory(*, override=None, label=None):
    return FakeRunnable()


@register_subagent("slow_async")  # type: ignore[arg-type]
async def slow_factory(*, override=None, label=None):
    return FakeRunnable(delay=0.1)


def test_async_subagent_runs_in_background_and_writes_events(monkeypatch):
    events = []
    runner = AsyncSubAgentRunner(writer=events.append)
    monkeypatch.setattr(
        "mentor.agents.async_subagent.runtime_ids",
        lambda: ("run-1", "thread-1", "user-1"),
    )

    async def scenario():
        job = await runner.spawn(
            "test_async",
            "research task",
            spec=SubAgentSpec(name="test_async", timeout_seconds=10),
        )
        assert job.status == "running"
        task = job.task_handle
        assert task is not None
        await task
        return job

    job = asyncio.run(scenario())
    assert job.status == "succeeded"
    assert job.result == "subagent result"
    assert [event.kind for event in events] == [
        "subagent_started",
        "subagent_finished",
    ]
    assert events[-1].data["job_id"] == job.job_id
    assert events[-1].data["result"] == "subagent result"


def test_async_subagent_timeout(monkeypatch):
    events = []
    runner = AsyncSubAgentRunner(writer=events.append)
    monkeypatch.setattr(
        "mentor.agents.async_subagent.runtime_ids",
        lambda: ("run-2", "thread-1", "user-1"),
    )

    async def scenario():
        job = await runner.spawn(
            "slow_async",
            "slow task",
            spec=SubAgentSpec(name="slow_async", timeout_seconds=0.01),
        )
        task = job.task_handle
        assert task is not None
        await task
        return job

    job = asyncio.run(scenario())
    assert job.status == "failed"
    assert "timeout" in (job.error or "")
    assert events[-1].kind == "subagent_failed"


def test_async_subagent_depth_limit(monkeypatch):
    runner = AsyncSubAgentRunner(writer=lambda event: None)
    monkeypatch.setattr(
        "mentor.agents.async_subagent.runtime_ids",
        lambda: ("run-3", "thread-1", "user-1"),
    )
    token = set_subagent_depth(1)
    try:
        with pytest.raises(RuntimeError, match="深度"):
            asyncio.run(
                runner.spawn(
                    "test_async",
                    "nested",
                    spec=SubAgentSpec(name="test_async", max_depth=1),
                )
            )
        assert get_subagent_depth() == 1
    finally:
        reset_subagent_depth(token)


def test_subagent_read_only_policy(monkeypatch):
    from langchain_core.messages import ToolMessage
    from mentor.middlewares.tool_policy import ToolPolicyMiddleware
    from mentor.policy.tool_policy import ToolPolicy, ToolPolicyConfig
    from mentor.tools.specs import ToolRegistry, ToolSpec
    from types import SimpleNamespace
    from typing import Any, cast

    registry = ToolRegistry()
    registry.register(ToolSpec(name="write_file", tool=None, risk="write"))
    middleware = ToolPolicyMiddleware(
        registry,
        policy=ToolPolicy(ToolPolicyConfig()),
        writer=lambda event: None,
    )
    token = set_subagent_depth(1)
    called = False

    async def handler(request):
        nonlocal called
        called = True
        return ToolMessage(content="ok", tool_call_id="c1", name="write_file")

    request = cast(
        Any,
        SimpleNamespace(
            tool_call={"name": "write_file", "id": "c1", "args": {"path": "x"}},
            state=None,
            runtime=None,
        ),
    )
    try:
        result = asyncio.run(middleware.awrap_tool_call(request, handler))
        assert called is False
        assert result.status == "error"
        assert "只读" in result.content
    finally:
        reset_subagent_depth(token)


def test_agent_with_subagents_gets_background_tools():
    from mentor.agents.main import main_agent_factory
    from mentor.config.nacos.nacos_schemas import AgentSpec

    bundle = asyncio.run(
        main_agent_factory._get_tools(AgentSpec(name="main_agent", subagents=["web_agent"]))
    )
    names = {tool.name for tool in bundle.direct_tools}
    assert {"spawn_subagent", "check_subagent", "cancel_subagent"} <= names


def test_async_subagent_prompt_marks_suitable_and_unsuitable_cases():
    from mentor.agents.async_subagent_prompt import (
        ASYNC_SUBAGENT_GUIDE,
        with_async_subagent_guide,
    )

    assert "适合" in ASYNC_SUBAGENT_GUIDE
    assert "不适合" in ASYNC_SUBAGENT_GUIDE
    prompt = with_async_subagent_guide("base prompt")
    assert "spawn_subagent" in prompt
    assert "只读" in prompt
