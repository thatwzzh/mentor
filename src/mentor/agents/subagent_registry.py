"""子 Agent 工厂注册表：用装饰器登记名称与异步工厂。

AgentFactory 只依赖注册表，不再维护「名称 → 模块路径」的硬编码字典；
新增子 Agent 时在自己的模块里用 @register_subagent("name") 登记即可。
"""

from collections.abc import Awaitable, Callable

from langchain_core.runnables import Runnable

SubAgentFactory = Callable[..., Awaitable[Runnable]]

_factories: dict[str, SubAgentFactory] = {}


def register_subagent(name: str):
    """把一个异步工厂登记为可委派子 Agent；重复名称直接报错。"""

    def decorator(factory: SubAgentFactory) -> SubAgentFactory:
        if name in _factories and _factories[name] is not factory:
            raise ValueError(f"子 Agent 已注册: {name}")
        _factories[name] = factory
        return factory

    return decorator


def get_subagent_factory(name: str) -> SubAgentFactory | None:
    """按名称取工厂；未注册返回 None，由调用方决定跳过还是报错。"""
    return _factories.get(name)


def registered_subagent_names() -> tuple[str, ...]:
    """返回已注册名称，便于启动时做配置校验或健康检查。"""
    return tuple(_factories)
