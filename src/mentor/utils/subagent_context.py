"""子代理运行上下文：深度限制与 read-only 标记。"""

from contextvars import ContextVar

subagent_depth: ContextVar[int] = ContextVar("subagent_depth", default=0)


def get_subagent_depth() -> int:
    return subagent_depth.get()


def set_subagent_depth(value: int):
    return subagent_depth.set(value)


def reset_subagent_depth(token) -> None:
    subagent_depth.reset(token)
