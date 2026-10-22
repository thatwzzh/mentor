"""4.2.3 沙箱生命周期测试：run 级复用、异常关闭、严格 Store、PVC 命名与中间件挂载。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.runnables import RunnableLambda

from mentor.agents import agent_factory as agent_factory_module
from mentor.agents.agent_factory import AgentFactory
from mentor.backends import sandbox_backend as backend_module
from mentor.backends.sandbox_backend import SandboxBackend
from mentor.config.nacos.nacos_schemas import OpenSandboxConfig
from mentor.tools.specs import ToolRegistry
from mentor.utils import sandbox as sandbox_utils


class FakeSandbox:
    """只记录 close/renew 的假沙箱，避免测试依赖 OpenSandbox。"""

    def __init__(self, command_error: Exception | None = None) -> None:
        self.closed = 0
        self.renew = AsyncMock()
        self.commands = SimpleNamespace(run=AsyncMock(side_effect=command_error))

    async def close(self) -> None:
        self.closed += 1


class DummyFactory(AgentFactory):
    @property
    def name(self) -> str:
        return "dummy"


def test_backend_reuses_one_sandbox_for_concurrent_tools(monkeypatch):
    fake = FakeSandbox()
    calls: list[str] = []

    async def fake_get_sandbox(key: str, volume_configs=None):
        calls.append(key)
        return fake

    monkeypatch.setattr(backend_module, "get_sandbox", fake_get_sandbox)
    monkeypatch.setattr(backend_module, "get_thread_id", lambda: "thread-1")
    monkeypatch.setattr(
        backend_module,
        "sandbox_settings",
        SimpleNamespace(tenant="tenant-a", idle_timeout_seconds=1800),
    )

    backend = SandboxBackend(lambda: "user-1")

    async def scenario():
        results = await asyncio.gather(
            *[backend._get_or_create_sandbox() for _ in range(10)]
        )
        assert all(result is fake for result in results)
        assert calls == ["user-1"]
        await backend.aclose_run()
        assert fake.closed == 1

    asyncio.run(scenario())


def test_backend_pvc_name_uses_tenant_and_user_hash(monkeypatch):
    monkeypatch.setattr(
        backend_module,
        "sandbox_settings",
        SimpleNamespace(tenant="tenant-a", idle_timeout_seconds=1800),
    )
    backend = SandboxBackend(lambda: "user_id_001")
    volumes = backend._get_volume_configs()
    assert volumes[0].name.startswith("tenant-a-")
    assert len(volumes[0].name.rsplit("-", 1)[-1]) == 12
    assert volumes[1].name == "sandbox-shared-data"


def test_enter_sandbox_closes_on_exception(monkeypatch):
    fake = FakeSandbox()

    async def fake_get_sandbox(*args, **kwargs):
        return fake

    async def fake_get_config(*args, **kwargs):
        return OpenSandboxConfig()

    monkeypatch.setattr(sandbox_utils, "get_sandbox", fake_get_sandbox)
    monkeypatch.setattr(sandbox_utils, "get_config", fake_get_config)

    async def scenario():
        with pytest.raises(RuntimeError, match="boom"):
            async with sandbox_utils.enter_sandbox("key"):
                raise RuntimeError("boom")
        assert fake.renew.await_count == 2
        assert fake.closed == 1

    asyncio.run(scenario())


def test_get_sandbox_rejects_memory_store(monkeypatch):
    def no_runtime():
        raise RuntimeError("no runtime")

    monkeypatch.setattr(sandbox_utils, "get_runtime", no_runtime)
    monkeypatch.setattr(
        sandbox_utils,
        "sandbox_settings",
        SimpleNamespace(allow_memory_store=False),
    )

    with pytest.raises(RuntimeError, match="持久 Store"):
        asyncio.run(sandbox_utils.get_sandbox("key"))


def test_aexecute_closes_sandbox_on_command_error(monkeypatch):
    fake = FakeSandbox(command_error=RuntimeError("channel down"))

    async def fake_get_sandbox(key: str, volume_configs=None):
        return fake

    async def fake_get_config(*args, **kwargs):
        return OpenSandboxConfig()

    monkeypatch.setattr(backend_module, "get_sandbox", fake_get_sandbox)
    monkeypatch.setattr(backend_module, "get_config", fake_get_config)
    monkeypatch.setattr(backend_module, "get_thread_id", lambda: "thread-1")
    monkeypatch.setattr(
        backend_module,
        "sandbox_settings",
        SimpleNamespace(tenant="tenant-a", idle_timeout_seconds=1800),
    )
    backend = SandboxBackend(lambda: "user-1")

    async def scenario():
        with pytest.raises(RuntimeError, match="channel down"):
            await backend.aexecute("echo hi")
        assert fake.closed == 1

    asyncio.run(scenario())


def test_create_agent_attaches_sandbox_lifecycle_middleware(monkeypatch):
    captured = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return RunnableLambda(lambda state: state)

    monkeypatch.setattr(
        agent_factory_module, "create_deep_agent", fake_create_deep_agent
    )
    factory = DummyFactory()

    async def scenario():
        await factory.create_agent(
            model=None,
            system_prompt="prompt",
            tools=[],
            tool_registry=ToolRegistry(),
            subagents=[],
            skills=[],
            spec_hash="hash",
        )

    asyncio.run(scenario())
    assert isinstance(captured["backend"], SandboxBackend)
    assert len(captured["middleware"]) == 3
