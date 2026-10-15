from langfuse.langchain import CallbackHandler
from langchain_core.runnables import RunnableConfig
from langfuse.types import TraceContext
from langgraph_sdk.runtime import ServerRuntime
from langfuse import propagate_attributes
from contextlib import asynccontextmanager


def trace_wrapper(agent_factory):
    @asynccontextmanager
    async def _trace(config: RunnableConfig, runtime: ServerRuntime):
        metadata = config.get("metadata", {})
        trace_id: str = metadata.get("trace_id", "")
        parent_span_id: str = metadata.get("parent_span_id", "")
        trace_context: TraceContext | None = None
        if trace_id and parent_span_id:
            trace_context = {"trace_id": trace_id, "parent_span_id": parent_span_id}

        trace_handler = CallbackHandler(trace_context=trace_context)
        agent = await agent_factory()
        thread_id = config.get("configurable", {}).get("thread_id")
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
