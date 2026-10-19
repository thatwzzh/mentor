"""追踪包装器：为每次 Agent 调用挂载 Langfuse CallbackHandler 并传播 trace 上下文。"""

from langfuse.langchain import CallbackHandler
from langchain_core.runnables import RunnableConfig
from langfuse.types import TraceContext
from langgraph_sdk.runtime import ServerRuntime
from langfuse import propagate_attributes
from contextlib import asynccontextmanager
from .shared import get_thread_id


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
        # 每次图调用都会重新执行工厂；当前没有按规格缓存编译结果。
        agent = await agent_factory()
        thread_id = get_thread_id()
        assert runtime.user is not None
        agent = agent.with_config(
            callbacks=[trace_handler],
            metadata={
                "langfuse_session_id": thread_id,
                "langfuse_user_id": runtime.user.identity,
            },
        )

        env = metadata.get("trace_environment", "")
        if env:
            with propagate_attributes(environment=env):
                yield agent
        else:
            yield agent

    return _trace
