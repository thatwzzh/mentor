"""统一工具执行器：限时 → 执行 → 截断/落盘 → 错误分类 → 结果信封。

中间件（ToolExecutorMiddleware）覆盖所有工具调用，包括 DeepAgents 内置工具；
deferred MCP 工具由 call_tool 走 execute_tool，同样复用这里的限时与结果处理。
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import ToolException
from langgraph.errors import GraphInterrupt
from pydantic import BaseModel, ValidationError

from mentor.tools.artifacts import ArtifactStore, artifact_store
from mentor.tools.specs import ToolSpec

ToolErrorKind = Literal["input", "policy", "timeout", "system"]


class ToolPolicyError(PermissionError):
    """策略层拒绝执行；由执行器归类为 policy。"""


@dataclass
class ToolResult:
    """执行结果；data 为模型可见内容，artifacts 保存被截断的完整输出。"""

    ok: bool
    data: Any = None
    error_kind: ToolErrorKind | None = None
    truncated: bool = False
    artifacts: list[str] = field(default_factory=list)
    duration_ms: int = 0
    error: str | None = None
    message: ToolMessage | Any | None = None

    def envelope(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "data": self.data,
            "error_kind": self.error_kind,
            "truncated": self.truncated,
            "artifacts": self.artifacts,
            "duration_ms": self.duration_ms,
        }


def _serialize(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump_json()
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


class ToolExecutor:
    """所有工具执行的唯一入口。"""

    def __init__(self, artifacts: ArtifactStore | None = None) -> None:
        self.artifacts = artifacts or artifact_store

    def _result(
        self,
        *,
        text: str,
        spec: ToolSpec,
        tool_call_id: str,
        started: float,
        message: ToolMessage,
    ) -> ToolResult:
        preview, artifacts = self.artifacts.maybe_offload(
            text,
            max_chars=spec.max_output_chars,
            tool_name=spec.name,
            call_id=tool_call_id,
        )
        result = ToolResult(
            ok=True,
            data=preview,
            truncated=bool(artifacts),
            artifacts=artifacts,
            duration_ms=int((time.monotonic() - started) * 1000),
            message=message.model_copy(update={"content": preview, "artifact": None}),
        )
        updated = message.model_copy(update={"content": preview, "artifact": result.envelope()})
        result.message = updated
        return result

    def _failure(
        self,
        *,
        error_kind: ToolErrorKind,
        error: str,
        tool_name: str,
        tool_call_id: str,
        started: float,
    ) -> ToolResult:
        result = ToolResult(
            ok=False,
            error_kind=error_kind,
            error=error,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        message = ToolMessage(
            content=f"[{error_kind}] {error}",
            tool_call_id=tool_call_id,
            name=tool_name,
            status="error",
            artifact=result.envelope(),
        )
        result.message = message
        return result

    @staticmethod
    def _classify(exc: BaseException) -> ToolErrorKind:
        if isinstance(exc, TimeoutError):
            return "timeout"
        if isinstance(exc, (ToolPolicyError, PermissionError)):
            return "policy"
        if isinstance(exc, (ToolException, ValidationError, ValueError)):
            return "input"
        return "system"

    async def execute_handler(
        self,
        spec: ToolSpec,
        request: Any,
        handler: Any,
    ) -> ToolResult:
        """中间件路径：执行 handler，并统一处理超时/截断/错误。"""
        started = time.monotonic()
        tool_call = getattr(request, "tool_call", {}) or {}
        tool_name = tool_call.get("name", spec.name)
        tool_call_id = tool_call.get("id", "")
        try:
            if spec.timeout_s is None:
                result = await handler(request)
            else:
                result = await asyncio.wait_for(
                    handler(request), timeout=spec.timeout_s
                )
        except GraphInterrupt:
            # interrupt/暂停必须原样抛给 LangGraph，不能被当成工具错误。
            raise
        except BaseException as exc:
            kind = self._classify(exc)
            if kind == "system":
                raise
            return self._failure(
                error_kind=kind,
                error=str(exc) or type(exc).__name__,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                started=started,
            )

        if isinstance(result, ToolMessage):
            text = _serialize(result.content)
            if result.status == "error":
                failure = self._failure(
                    error_kind="input",
                    error=text,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    started=started,
                )
                # 保留 MCP/ToolNode 原始 status 与 metadata，只补 artifact 信封。
                failure.message = result.model_copy(
                    update={
                        "artifact": failure.envelope(),
                        "content": text,
                        "status": "error",
                    }
                )
                return failure
            return self._result(
                text=text,
                spec=spec,
                tool_call_id=tool_call_id,
                started=started,
                message=result,
            )
        # Command 等非 ToolMessage 结果原样透传。
        return ToolResult(
            ok=True,
            duration_ms=int((time.monotonic() - started) * 1000),
            message=result,
        )

    async def execute_tool(self, spec: ToolSpec, arguments: dict[str, Any]) -> ToolResult:
        """deferred 工具路径：call_tool 直接执行目标工具。"""
        if spec.tool is None:
            raise RuntimeError(f"工具 {spec.name} 没有可执行实现")
        started = time.monotonic()
        try:
            if spec.timeout_s is None:
                raw = await spec.tool.ainvoke(arguments)
            else:
                raw = await asyncio.wait_for(
                    spec.tool.ainvoke(arguments), timeout=spec.timeout_s
                )
        except GraphInterrupt:
            raise
        except BaseException as exc:
            kind = self._classify(exc)
            if kind == "system":
                raise
            return ToolResult(
                ok=False,
                error_kind=kind,
                error=str(exc) or type(exc).__name__,
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        text = _serialize(raw)
        preview, artifacts = self.artifacts.maybe_offload(
            text,
            max_chars=spec.max_output_chars,
            tool_name=spec.name,
            call_id="deferred",
        )
        return ToolResult(
            ok=True,
            data=preview,
            truncated=bool(artifacts),
            artifacts=artifacts,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
