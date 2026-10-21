"""启动前配置体检：检查 Nacos、AgentSpec 必填项、模型网关、MinIO 桶与沙箱配置。

CLI 入口见 ``mentor config doctor``。检查只读，不会创建桶或启动沙箱；因此可以安全
放在部署流水线或本地启动脚本里。任何一项失败都会返回非 0 退出码。
"""

from dataclasses import dataclass

import httpx

from mentor.config.minio_settings import minio_settings
from mentor.config.nacos.nacos_agent_spec import get_agent_spec
from mentor.config.nacos.nacos_config import get_config
from mentor.config.nacos.nacos_schemas import AgentSpec, OpenSandboxConfig
from mentor.config.openai_settings import openai_settings
from mentor.utils.minio import MinIO


@dataclass(frozen=True)
class CheckResult:
    """单个体检项的结果。"""

    name: str
    ok: bool
    detail: str


def check_spec(spec: AgentSpec) -> list[CheckResult]:
    """检查 AgentSpec 的最小必填字段；空 Prompt 视为不可上线。"""
    return [
        CheckResult("spec.name", bool(spec.name.strip()), spec.name or "空"),
        CheckResult(
            "spec.model.name",
            bool(spec.model.name.strip()),
            spec.model.name or "空",
        ),
        CheckResult(
            "spec.system_prompt",
            bool(spec.system_prompt.strip()),
            f"{len(spec.system_prompt)} 字符",
        ),
    ]


async def check_nacos(agent_name: str) -> list[CheckResult]:
    """读取一次 AgentSpec，并顺带校验必填字段。"""
    try:
        spec = await get_agent_spec(agent_name)
    except Exception as exc:
        return [
            CheckResult(
                "nacos.agentspec",
                False,
                f"{type(exc).__name__}: {exc}",
            )
        ]
    return [
        CheckResult("nacos.agentspec", True, f"{agent_name} 可读"),
        *check_spec(spec),
    ]


async def check_model_gateway() -> list[CheckResult]:
    """请求 LiteLLM/OpenAI 兼容网关的 /models，确认地址与鉴权可用。"""
    if not openai_settings.base_url:
        return [CheckResult("model.gateway", False, "OPENAI_BASE_URL 未配置")]
    url = f"{openai_settings.base_url.rstrip('/')}/models"
    headers = {}
    if openai_settings.api_key:
        headers["Authorization"] = f"Bearer {openai_settings.api_key}"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
    except Exception as exc:
        return [CheckResult("model.gateway", False, f"{type(exc).__name__}: {exc}")]
    return [CheckResult("model.gateway", True, url)]


async def check_minio_bucket() -> list[CheckResult]:
    """确认产物桶可列出；不创建桶，避免体检产生副作用。"""
    client = MinIO(
        endpoint=minio_settings.endpoint,
        access_key=minio_settings.access_key,
        secret_key=minio_settings.secret_key,
        secure=minio_settings.secure,
    )
    try:
        files = await client.list_files(minio_settings.bucket)
    except Exception as exc:
        return [CheckResult("minio.bucket", False, f"{type(exc).__name__}: {exc}")]
    return [
        CheckResult(
            "minio.bucket",
            True,
            f"{minio_settings.bucket} 可读，{len(files)} 个对象",
        )
    ]


async def check_sandbox_config() -> list[CheckResult]:
    """读取 sandbox_config，检查镜像与超时字段是否可用。"""
    try:
        config = await get_config("sandbox_config", OpenSandboxConfig)
    except Exception as exc:
        return [CheckResult("sandbox.config", False, f"{type(exc).__name__}: {exc}")]
    return [
        CheckResult(
            "sandbox.config",
            bool(config.image),
            f"image={config.image}, command_timeout={config.command_timeout_seconds}s",
        )
    ]


async def run_doctor(agent_name: str = "main_agent") -> list[CheckResult]:
    """按固定顺序执行全部检查；单项失败不阻断后续检查。"""
    results: list[CheckResult] = []
    results.extend(await check_nacos(agent_name))
    results.extend(await check_model_gateway())
    results.extend(await check_minio_bucket())
    results.extend(await check_sandbox_config())
    return results
