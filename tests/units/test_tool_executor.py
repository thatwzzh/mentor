"""4.2.4 工具执行器测试：清单、超时、截断落盘、错误分类、deferred 工具。"""

import asyncio
import json
import tempfile
from types import SimpleNamespace
from typing import Any, cast

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from langchain.tools import tool

from mentor.agents import agent_factory as agent_factory_module
from mentor.agents.agent_factory import AgentFactory
from mentor.config.nacos.nacos_schemas import MCPServerConfig
from mentor.middlewares.tool_executor import ToolExecutorMiddleware
from mentor.tools import ToolBundle, get_tool_bundle
from mentor.tools.artifacts import ArtifactStore, MinioArtifactMirror
from mentor.tools.executor import ToolExecutor
from mentor.tools.specs import ToolRegistry, ToolSpec


def test_tool_bundle_includes_read_artifact_and_risk():
    bundle = get_tool_bundle(["get_current_time"])
    names = {tool.name for tool in bundle.direct_tools}
    assert "get_current_time" in names
    # 截断输出需要 read_artifact 才能被消费，始终自动加入。
    assert "read_artifact" in names
    spec = bundle.registry.get("get_current_time")
    assert spec is not None and spec.risk == "read"


def test_execute_tool_truncates_and_offloads():
    @tool
    async def big_tool() -> str:
        """返回超长文本。"""
        return "x" * 20

    with tempfile.TemporaryDirectory() as tmp:
        executor = ToolExecutor(ArtifactStore(tmp))
        spec = ToolSpec(
            name="big_tool",
            tool=big_tool,
            risk="read",
            timeout_s=0.01,
            max_output_chars=5,
        )
        result = asyncio.run(executor.execute_tool(spec, {}))
        assert result.ok and result.truncated
        assert result.artifacts
        assert result.data is not None and "artifact://" in result.data
        assert executor.artifacts.read(result.artifacts[0]) == "x" * 20


def test_execute_tool_error_classification():
    @tool
    async def bad_input() -> str:
        """输入错误。"""
        raise ValueError("bad input")

    @tool
    async def denied() -> str:
        """策略拒绝。"""
        raise PermissionError("denied")

    @tool
    async def system_error() -> str:
        """系统错误。"""
        raise RuntimeError("system down")

    @tool
    async def slow() -> str:
        """超时。"""
        await asyncio.sleep(0.2)
        return "late"

    executor = ToolExecutor()

    async def scenario():
        input_result = await executor.execute_tool(
            ToolSpec(name="bad_input", tool=bad_input, risk="read"), {}
        )
        assert input_result.error_kind == "input"
        policy_result = await executor.execute_tool(
            ToolSpec(name="denied", tool=denied, risk="read"), {}
        )
        assert policy_result.error_kind == "policy"
        timeout_result = await executor.execute_tool(
            ToolSpec(name="slow", tool=slow, risk="read", timeout_s=0.01), {}
        )
        assert timeout_result.error_kind == "timeout"
        with pytest.raises(RuntimeError, match="system down"):
            await executor.execute_tool(
                ToolSpec(name="system_error", tool=system_error, risk="read"), {}
            )

    asyncio.run(scenario())


def test_middleware_wraps_success_error_and_timeout():
    @tool
    async def big_tool() -> str:
        """返回超长文本。"""
        return "y" * 20

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="big_tool",
            tool=big_tool,
            risk="read",
            timeout_s=0.01,
            max_output_chars=5,
        )
    )
    event_log = []
    middleware = ToolExecutorMiddleware(registry, writer=event_log.append)
    request = cast(
        Any,
        SimpleNamespace(
            tool_call={"name": "big_tool", "id": "call-1", "args": {}},
            tool=big_tool,
            state=None,
            runtime=None,
        ),
    )

    async def success_handler(request):
        return ToolMessage(content="y" * 20, tool_call_id="call-1", name="big_tool")

    async def error_handler(request):
        raise ValueError("bad input")

    async def timeout_handler(request):
        await asyncio.sleep(0.2)
        return ToolMessage(content="late", tool_call_id="call-1", name="big_tool")

    async def scenario():
        success = await middleware.awrap_tool_call(request, success_handler)
        assert isinstance(success, ToolMessage)
        assert success.artifact["ok"] is True
        assert success.artifact["truncated"] is True
        assert "artifact://" in str(success.content)

        failure = await middleware.awrap_tool_call(request, error_handler)
        assert isinstance(failure, ToolMessage)
        assert failure.status == "error"
        assert failure.artifact["error_kind"] == "input"

        timeout = await middleware.awrap_tool_call(request, timeout_handler)
        assert isinstance(timeout, ToolMessage)
        assert timeout.artifact["error_kind"] == "timeout"
        # 工具副作用前后各写一条事件。
        assert [event.kind for event in event_log].count("tool_call") == 3
        assert [event.kind for event in event_log].count("tool_result") == 3

    asyncio.run(scenario())


def test_deferred_tools_add_search_and_call():
    @tool
    async def mcp_echo(text: str) -> str:
        """回显文本。"""
        return text

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="docs_echo",
            tool=mcp_echo,
            risk="external",
            description="echo docs",
            exposure="deferred",
            timeout_s=1,
            source="mcp",
            server="docs",
        )
    )
    bundle = ToolBundle(registry=registry, direct_tools=[])
    bundle.add_deferred_tools()
    names = {tool.name for tool in bundle.direct_tools}
    assert {"tool_search", "call_tool"} <= names

    search = next(tool for tool in bundle.direct_tools if tool.name == "tool_search")
    call = next(tool for tool in bundle.direct_tools if tool.name == "call_tool")

    async def scenario():
        found = await search.ainvoke({"query": "docs"})
        assert "docs_echo" in found
        output = await call.ainvoke(
            {"name": "docs_echo", "arguments": {"text": "hello"}}
        )
        payload = json.loads(output)
        assert payload["ok"] is True
        assert payload["data"] == "hello"

    asyncio.run(scenario())


class DummyFactory(AgentFactory):
    @property
    def name(self) -> str:
        return "dummy"


def test_mcp_tools_apply_auth_timeout_allowed_and_deferred(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, connections, tool_name_prefix):
            captured["connections"] = connections
            captured["prefix"] = tool_name_prefix

        async def get_tools(self):
            def make(name):
                def fn() -> str:
                    return name

                return StructuredTool.from_function(func=fn, name=name, description=name)

            return [make("srv_a"), make("srv_b"), make("srv_c")]

    monkeypatch.setattr(agent_factory_module, "MultiServerMCPClient", FakeClient)
    factory = DummyFactory()
    config = MCPServerConfig(
        name="srv",
        url="http://mcp.local/mcp",
        auth="Bearer token",
        timeout_seconds=7,
        allowed_tools=["a", "b"],
        disabled_tools=["b"],
        exposure="deferred",
        risk="external",
    )

    specs = asyncio.run(factory._get_mcp_tools("srv", config))
    assert [spec.name for spec in specs] == ["srv_a"]
    assert specs[0].exposure == "deferred"
    assert specs[0].timeout_s == 7
    assert specs[0].server == "srv"
    assert captured["connections"]["srv"]["headers"] == {
        "Authorization": "Bearer token"
    }
    assert captured["connections"]["srv"]["timeout"] == 7
    assert captured["prefix"] is True


def test_large_tool_output_keeps_head_and_tail():
    text = "HEAD" + ("x" * 20_000) + "TAIL"

    @tool
    async def large_tool() -> str:
        """返回超长文本。"""
        return text

    with tempfile.TemporaryDirectory() as tmp:
        executor = ToolExecutor(
            ArtifactStore(tmp), minio=MinioArtifactMirror(enabled=False)
        )
        spec = ToolSpec(
            name="large_tool",
            tool=large_tool,
            risk="read",
            timeout_s=10,
            max_output_chars=8_000,
        )
        result = asyncio.run(executor.execute_tool(spec, {}))
        assert result.truncated
        assert result.data is not None
        assert "HEAD" in result.data
        assert "TAIL" in result.data
        assert "artifact://" in result.data
        assert result.minio_ref is None
        assert executor.artifacts.read(result.artifacts[0], limit=len(text)) == text
