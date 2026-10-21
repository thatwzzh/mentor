"""每请求会话上下文：用 ContextVar 把 thread_id 传给模型请求层。

图会被 AgentFactory 跨请求缓存，因此 x-litellm-session-id 不能在构建模型时写死；
ReasoningChatModel 在每次请求构造 payload 时读取这里的最新值。
"""

from contextvars import ContextVar

litellm_session_id: ContextVar[str] = ContextVar(
    "litellm_session_id", default=""
)
