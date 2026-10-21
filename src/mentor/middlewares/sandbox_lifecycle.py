"""Agent 结束钩子：关闭当前 run 的 Sandbox 连接。

SandboxBackend 在一次 run 内按 (user, thread) 复用连接；run 正常结束或异常退出后
由这里统一 close，避免连接泄漏。真正的容器/PVC 生命周期仍由 OpenSandbox TTL 管理。
"""

from typing import Any

from langchain.agents.middleware import after_agent

from mentor.backends.sandbox_backend import SandboxBackend


def sandbox_lifecycle_middleware(backend: SandboxBackend):
    """生成绑定到具体 backend 实例的 after_agent 中间件。"""

    @after_agent
    async def close_sandbox(state: Any, runtime: Any) -> None:
        await backend.aclose_run()

    return close_sandbox
