"""5.11 沙箱 profile：解析、资源映射、步数/工具/输出护栏。"""

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from langchain_core.messages import ToolMessage

from mentor.backends.sandbox_backend import SandboxBackend
from mentor.config.nacos.nacos_schemas import OpenSandboxConfig, SandboxProfile
from mentor.config.sandbox_profiles import (
    profile_from_config,
    to_open_config,
)
from mentor.middlewares.sandbox_profile import (
    SandboxProfileGuardMiddleware,
    SandboxProfileLimitError,
)
from mentor.middlewares.tool_executor import ToolExecutorMiddleware
from mentor.tools.specs import ToolRegistry, ToolSpec


def test_profile_merges_base_and_explicit_fields():
    base = OpenSandboxConfig(
        image="base:1",
        cpu="1",
        memory="2Gi",
        timeout_seconds=600,
        ready_timeout_seconds=180,
        command_timeout_seconds=90,
    )
    profile = SandboxProfile(
        cpu="4",
        memory_mb=8192,
        command_timeout_seconds=300,
        max_tool_calls=8,
    )
    merged = profile_from_config(base, profile)
    assert merged.image == "base:1"
    assert merged.cpu == "4"
    assert merged.memory_mb == 8192
    assert merged.command_timeout_seconds == 300
    assert merged.max_tool_calls == 8
    config = to_open_config(merged)
    assert config.memory == "8192Mi"
    assert config.cpu == "4"


def test_backend_uses_profile_timeout():
    profile = SandboxProfile(command_timeout_seconds=321)
    backend = SandboxBackend(profile=profile)
    assert backend.profile_config is not None
    assert backend.profile_config.command_timeout_seconds == 321


def test_guard_limits_steps_and_tool_calls():
    profile = SandboxProfile(max_steps=1, max_tool_calls=1)
    guard = SandboxProfileGuardMiddleware(profile)
    asyncio.run(guard.abefore_agent({}, cast(Any, None)))

    async def handler(request):
        return ToolMessage(content="ok", tool_call_id="c1", name="echo")

    request = cast(
        Any,
        SimpleNamespace(tool_call={"name": "echo", "id": "c1"}),
    )

    async def scenario():
        first = await guard.awrap_model_call(request, handler)
        assert first is not None
        with pytest.raises(SandboxProfileLimitError):
            await guard.awrap_model_call(request, handler)
        ok = await guard.awrap_tool_call(request, handler)
        assert ok.status != "error"
        limited = await guard.awrap_tool_call(request, handler)
        assert limited.status == "error"
        assert "上限" in limited.content

    asyncio.run(scenario())


def test_tool_executor_caps_output_by_profile():
    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="echo", tool=None, risk="read", max_output_chars=8000)
    )
    middleware = ToolExecutorMiddleware(
        registry, writer=lambda event: None, max_output_chars=5
    )
    request = cast(
        Any,
        SimpleNamespace(tool_call={"name": "echo", "id": "c1", "args": {}}),
    )

    async def handler(request):
        return ToolMessage(content="x" * 20, tool_call_id="c1", name="echo")

    result = asyncio.run(middleware.awrap_tool_call(request, handler))
    assert isinstance(result, ToolMessage)
    assert result.artifact["truncated"] is True
    assert "artifact://" in str(result.content)
