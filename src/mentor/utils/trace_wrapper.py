"""追踪包装器：为每次 Agent 调用挂载 Langfuse CallbackHandler 并传播 trace 上下文。"""

from contextlib import asynccontextmanager
from typing import Any

from langchain_core.runnables import Runnable, RunnableConfig
from langfuse import propagate_attributes
from langfuse.langchain import CallbackHandler
from langfuse.types import TraceContext
from langgraph_sdk.runtime import ServerRuntime

from .session_context import litellm_session_id
from .shared import get_thread_id


def _merge_trace_metadata(
    agent: Runnable,
    metadata: dict[str, Any],
    *,
    callbacks: list[Any] | None = None,
) -> Runnable:
    """把 Langfuse 字段合并到图已有的 metadata。

    with_config 的 metadata 是覆盖语义；不先取旧值会丢掉 AgentFactory 写入的
    agent_spec_hash。callbacks 与 metadata 一次设置，避免二次 with_config 覆盖。
    """
    base = dict(getattr(agent, "config", {}).get("metadata", {}) or {})
    config: dict[str, Any] = {"metadata": {**base, **metadata}}
    if callbacks is not None:
        config["callbacks"] = callbacks
    return agent.with_config(**config)


def trace_wrapper(agent_factory):
    """把 Agent 工厂包成 LangGraph 可调用的图工厂，并为每次调用挂载追踪。"""

    @asynccontextmanager
    async def _trace(config: RunnableConfig, runtime: ServerRuntime):
        metadata = config.get("metadata", {})
        # 评测任务会通过 metadata 传入既有 trace_id/parent_span_id，使 Agent 调用挂在实验 trace 下。
        trace_id: str = metadata.get("trace_id", "")
        parent_span_id: str = metadata.get("parent_span_id", "")
        trace_context: TraceContext | None = None
        if trace_id and parent_span_id:
            trace_context = {"trace_id": trace_id, "parent_span_id": parent_span_id}

        trace_handler = CallbackHandler(trace_context=trace_context)
        # request override / label 从 LangGraph configurable 透传；不持久化到 Nacos。
        configurable = config.get("configurable", {}) or {}
        override = configurable.get("agent_spec_override")
        if override is not None and not isinstance(override, dict):
            raise ValueError("agent_spec_override 必须是对象")
        label = configurable.get("agent_label")
        # 每次图调用都会重新执行工厂；工厂内部按 (agent, spec_hash) 返回缓存图。
        agent = await agent_factory(override=override, label=label)
        thread_id = get_thread_id()
        assert runtime.user is not None
        # 保留图自带的 metadata（如 agent_spec_hash），再合并 Langfuse 字段。
        agent = _merge_trace_metadata(
            agent,
            {
                "langfuse_session_id": thread_id,
                "langfuse_user_id": runtime.user.identity,
            },
            callbacks=[trace_handler],
        )

        env = metadata.get("trace_environment", "")
        # 把当前 thread 写进 ContextVar，供 ReasoningChatModel 在每次请求时注入
        # x-litellm-session-id；图是跨请求缓存的，不能把首个 thread 的 header 固定下来。
        session_token = litellm_session_id.set(thread_id or "")
        try:
            if env:
                with propagate_attributes(environment=env):
                    yield agent
            else:
                yield agent
        finally:
            litellm_session_id.reset(session_token)

    return _trace
