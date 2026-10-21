"""4.2.1 回归测试：Agent 编译缓存、spec_hash 版本切换与按请求注入会话头。"""

import asyncio

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableLambda
from pydantic import SecretStr

from mentor.agents import agent_factory as agent_factory_module
from mentor.agents.agent_cache import AgentCache
from mentor.agents.agent_factory import AgentFactory
from mentor.agents.subagent_registry import get_subagent_factory
from mentor.config.nacos.nacos_schemas import AgentSpec
from mentor.utils.reasoning_chat_model import ReasoningChatModel
from mentor.utils.session_context import litellm_session_id
from mentor.utils.trace_wrapper import _merge_trace_metadata


class DummyFactory(AgentFactory):
    """不访问 Nacos/模型的测试工厂，只统计 create_agent 被调用次数。"""

    def __init__(self) -> None:
        super().__init__()
        self.build_count = 0

    @property
    def name(self) -> str:
        return "dummy"

    def _get_model(self, agent_config: AgentSpec):
        return None

    async def _get_tools(self, agent_config: AgentSpec):
        return []

    async def create_agent(
        self,
        *,
        model,
        system_prompt: str,
        tools,
        subagents,
        skills,
        spec_hash: str,
    ):
        self.build_count += 1
        return RunnableLambda(lambda state: state).with_config(
            metadata={"agent_spec_hash": spec_hash}
        )


def test_agent_cache_lru_and_per_agent_limit():
    cache = AgentCache(max_versions_per_agent=2)
    a = RunnableLambda(lambda state: "a")
    b = RunnableLambda(lambda state: "b")
    c = RunnableLambda(lambda state: "c")

    cache.put("agent", "v1", a)
    cache.put("agent", "v2", b)
    cache.put("agent", "v3", c)

    # v1 最久未使用，被淘汰；v2/v3 保留。
    assert cache.get("agent", "v1") is None
    assert cache.get("agent", "v2") is b
    assert cache.get("agent", "v3") is c
    assert cache.stats() == {"entries": 2, "agents": {"agent": 2}}

    # 另一个 Agent 的缓存不受影响。
    cache.put("other", "v1", a)
    assert cache.get("other", "v1") is a


def test_spec_hash_changes_with_prompt_and_subagent():
    base = AgentSpec(name="main", system_prompt="v1")
    same = AgentSpec(name="main", system_prompt="v1")
    changed = AgentSpec(name="main", system_prompt="v2")
    subagent = AgentSpec(name="web", system_prompt="web")

    base_hash = AgentFactory._spec_hash(base, [])
    assert base_hash == AgentFactory._spec_hash(same, [])
    assert base_hash != AgentFactory._spec_hash(changed, [])
    assert base_hash != AgentFactory._spec_hash(base, [subagent])


def test_factory_reuses_compiled_graph_and_switches_versions(monkeypatch):
    spec = AgentSpec(name="dummy", system_prompt="v1")

    async def fake_get_agent_spec(
        name: str, *, override: dict | None = None, label: str | None = None
    ) -> AgentSpec:
        return spec

    async def fake_ensure_skills(name: str, skills: list[str]) -> None:
        return None

    monkeypatch.setattr(agent_factory_module, "get_agent_spec", fake_get_agent_spec)
    monkeypatch.setattr(agent_factory_module, "ensure_skills", fake_ensure_skills)

    async def scenario():
        factory = DummyFactory()
        first = await factory.factory()
        # 验收要求：连续 100 次请求只构建 1 次。
        for _ in range(99):
            assert await factory.factory() is first
        assert factory.build_count == 1

        # 规格变化 → 新 hash → 重新编译。
        spec.system_prompt = "v2"
        third = await factory.factory()
        assert third is not first
        assert factory.build_count == 2
        assert factory.cache_stats()["entries"] == 2

        # 回到旧规格 → 命中旧版本，不重新编译。
        spec.system_prompt = "v1"
        fourth = await factory.factory()
        assert fourth is first
        assert factory.build_count == 2

    asyncio.run(scenario())


def test_factory_concurrent_requests_build_once(monkeypatch):
    spec = AgentSpec(name="dummy", system_prompt="v1")

    async def fake_get_agent_spec(
        name: str, *, override: dict | None = None, label: str | None = None
    ) -> AgentSpec:
        return spec

    async def fake_ensure_skills(name: str, skills: list[str]) -> None:
        return None

    monkeypatch.setattr(agent_factory_module, "get_agent_spec", fake_get_agent_spec)
    monkeypatch.setattr(agent_factory_module, "ensure_skills", fake_ensure_skills)

    async def scenario():
        factory = DummyFactory()
        results = await asyncio.gather(*[factory.factory() for _ in range(20)])
        assert len({id(result) for result in results}) == 1
        assert factory.build_count == 1

    asyncio.run(scenario())


def test_builtin_web_subagent_is_registered():
    # mentor.agents.__init__ 会导入 web 模块，装饰器应完成 web_agent 注册。
    assert get_subagent_factory("web_agent") is not None


def test_trace_metadata_merge_preserves_spec_hash():
    agent = RunnableLambda(lambda state: state).with_config(
        metadata={"agent_spec_hash": "abc"}
    )
    merged = _merge_trace_metadata(
        agent,
        {"langfuse_user_id": "user-1"},
        callbacks=["test-callback"],
    )
    metadata = getattr(merged, "config", {})["metadata"]
    assert metadata["agent_spec_hash"] == "abc"
    assert metadata["langfuse_user_id"] == "user-1"
    assert getattr(merged, "config", {})["callbacks"] == ["test-callback"]


def test_reasoning_model_injects_session_header_per_request():
    model = ReasoningChatModel(
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://localhost:4000/v1",
    )
    token = litellm_session_id.set("thread-1")
    try:
        payload = model._get_request_payload([HumanMessage(content="hi")])
        assert payload["extra_headers"]["x-litellm-session-id"] == "thread-1"
    finally:
        litellm_session_id.reset(token)

    # 离开请求上下文后不再带会话头，避免图缓存把首个 thread 固定下来。
    payload = model._get_request_payload([HumanMessage(content="hi")])
    headers = payload.get("extra_headers") or {}
    assert "x-litellm-session-id" not in headers
