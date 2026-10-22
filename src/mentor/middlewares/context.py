"""显式配置 DeepAgents 压缩：按模型上下文计算 trigger，keep 由配置决定。"""

from typing import Any

from deepagents.middleware.summarization import (
    SummarizationMiddleware,
    SummarizationToolMiddleware,
)

from mentor.config.context_settings import context_settings


def context_window_for(model: Any) -> int:
    """优先读模型 profile 的最大输入 token，否则使用配置回退值。"""
    profile = getattr(model, "profile", None)
    if isinstance(profile, dict):
        for key in ("max_input_tokens", "context_window", "max_context_tokens"):
            value = profile.get(key)
            if isinstance(value, int) and value > 0:
                return value
    return context_settings.window_tokens


def build_summarization_middleware(model: Any, backend: Any) -> SummarizationMiddleware:
    """构建显式 trigger/keep 的压缩中间件；同名替换 DeepAgents 默认实现。"""
    window = context_window_for(model)
    trigger_tokens = (
        max(1, int(window * context_settings.trigger_ratio))
        if context_settings.auto_compact
        else window * 10
    )
    return SummarizationMiddleware(
        model=model,
        backend=backend,
        trigger=("tokens", trigger_tokens),
        keep=("messages", context_settings.keep_messages),
        trim_tokens_to_summarize=context_settings.trim_tokens_to_summarize,
    )


def build_compact_tool_middleware(
    summarization: SummarizationMiddleware,
) -> SummarizationToolMiddleware:
    """注册 compact_conversation 工具；/compact 命令可直接触发它。"""
    return SummarizationToolMiddleware(
        summarization,
        system_prompt=(
            "当用户输入 /compact 或明确要求压缩上下文时，"
            "调用 compact_conversation 工具，然后继续原任务。"
        ),
    )
