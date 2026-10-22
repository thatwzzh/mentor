"""按模型能力档位解析沙箱 profile，并提供 run 级 contextvar。

profile 选择只看 AgentModelConfig.profile，不按模型名 fork Backend。
"""

from contextvars import ContextVar
from typing import Any

from mentor.config.nacos.nacos_config import get_config
from mentor.config.nacos.nacos_schemas import (
    AgentSpec,
    OpenSandboxConfig,
    SandboxProfile,
)

_current_profile: ContextVar[SandboxProfile | None] = ContextVar(
    "current_sandbox_profile", default=None
)


def get_current_sandbox_profile() -> SandboxProfile | None:
    return _current_profile.get()


def set_current_sandbox_profile(profile: SandboxProfile):
    return _current_profile.set(profile)


def reset_current_sandbox_profile(token) -> None:
    _current_profile.reset(token)


def _memory_mb(value: str | None) -> int | None:
    if not value:
        return None
    text = value.strip().lower()
    try:
        if text.endswith("gi"):
            return int(float(text[:-2]) * 1024)
        if text.endswith("mi"):
            return int(float(text[:-2]))
        if text.endswith("g"):
            return int(float(text[:-1]) * 1000)
        if text.endswith("m"):
            return int(float(text[:-1]))
        return int(float(text))
    except ValueError:
        return None


def profile_from_config(
    base: OpenSandboxConfig, profile: SandboxProfile | None
) -> SandboxProfile:
    """把 profile 的未显式字段合并到全局 sandbox_config。"""
    if profile is None:
        return SandboxProfile(
            image=base.image,
            cpu=base.cpu,
            memory_mb=_memory_mb(base.memory),
            timeout_seconds=base.timeout_seconds,
            ready_timeout_seconds=base.ready_timeout_seconds,
            command_timeout_seconds=base.command_timeout_seconds,
            network_default_action=base.network_default_action,
            network_allow=list(base.network_allow),
        )
    explicit = profile.model_fields_set
    data: dict[str, Any] = profile.model_dump()
    if "image" not in explicit or not data.get("image"):
        data["image"] = base.image
    if "cpu" not in explicit or not data.get("cpu"):
        data["cpu"] = base.cpu
    if "memory_mb" not in explicit or not data.get("memory_mb"):
        data["memory_mb"] = _memory_mb(base.memory)
    if "timeout_seconds" not in explicit:
        data["timeout_seconds"] = base.timeout_seconds
    if "ready_timeout_seconds" not in explicit:
        data["ready_timeout_seconds"] = base.ready_timeout_seconds
    if "command_timeout_seconds" not in explicit:
        data["command_timeout_seconds"] = base.command_timeout_seconds
    if "network_default_action" not in explicit:
        data["network_default_action"] = base.network_default_action
    if "network_allow" not in explicit:
        data["network_allow"] = list(base.network_allow)
    return SandboxProfile.model_validate(data)


def to_open_config(profile: SandboxProfile) -> OpenSandboxConfig:
    return OpenSandboxConfig(
        image=profile.image or "python:3.12-slim",
        timeout_seconds=profile.timeout_seconds,
        ready_timeout_seconds=profile.ready_timeout_seconds,
        command_timeout_seconds=profile.command_timeout_seconds,
        cpu=profile.cpu,
        memory=f"{profile.memory_mb}Mi" if profile.memory_mb else None,
        network_default_action=profile.network_default_action,
        network_allow=list(profile.network_allow),
    )


async def resolve_sandbox_profile(agent_config: AgentSpec) -> SandboxProfile:
    """按 model.profile 选 profile；未声明时把 sandbox_config 转成默认 profile。"""
    base = await get_config("sandbox_config", OpenSandboxConfig)
    name = agent_config.model.profile or "balanced"
    selected = agent_config.sandbox_profiles.get(name)
    return profile_from_config(base, selected)
