"""会话投影：把消息历史投影成 run_events，并把 checkpoint 修剪到可控大小。

投影是纯函数，HTTP 路由、前端契约和中间件共用同一套实现，避免两套口径。
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)

from mentor.events.models import RunEvent, RunEventRole, RunSummary

_ARTIFACT_RE = re.compile(r"artifact://[A-Za-z0-9._-]+")


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                else:
                    parts.append(json.dumps(block, ensure_ascii=False, default=str))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False, default=str)


def _role(msg: BaseMessage) -> RunEventRole | None:
    if isinstance(msg, HumanMessage):
        return "user"
    if isinstance(msg, AIMessage):
        return "assistant"
    if isinstance(msg, ToolMessage):
        return "tool"
    if isinstance(msg, SystemMessage):
        return "system"
    return None


def _event_key(index: int, kind: str, suffix: str = "") -> str:
    return f"{index}:{kind}:{suffix}"


def _artifacts_in(text: str) -> list[str]:
    return list(dict.fromkeys(_ARTIFACT_RE.findall(text)))


def project_messages_to_events(
    messages: Sequence[BaseMessage],
    *,
    run_id: str,
    thread_id: str = "",
    user_id: str = "",
) -> list[RunEvent]:
    """把消息历史投影成 UI/审计共用事件；不写 state。"""
    events: list[RunEvent] = []
    for index, msg in enumerate(messages):
        if isinstance(msg, RemoveMessage):
            continue
        role = _role(msg)
        content = _text(msg.content) if getattr(msg, "content", None) else ""
        if isinstance(msg, AIMessage):
            usage = getattr(msg, "usage_metadata", None)
            if content:
                events.append(
                    RunEvent(
                        run_id=run_id,
                        thread_id=thread_id,
                        user_id=user_id,
                        event_key=f"message:{msg.id or index}",
                        kind="message",
                        role="assistant",
                        content=content,
                        data={"token_usage": usage} if usage else {},
                    )
                )
            for tool_index, tool_call in enumerate(msg.tool_calls or []):
                name = tool_call.get("name", "") if isinstance(tool_call, dict) else ""
                call_id = tool_call.get("id", "") if isinstance(tool_call, dict) else ""
                args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
                events.append(
                    RunEvent(
                        run_id=run_id,
                        thread_id=thread_id,
                        user_id=user_id,
                        event_key=f"tool_call:{call_id or f'{index}:{tool_index}'}",
                        kind="tool_call",
                        role="assistant",
                        name=name or None,
                        content=None,
                        data={"args": args, "tool_call_id": call_id},
                    )
                )
            continue

        if isinstance(msg, ToolMessage):
            kind = "approval" if msg.name == "ask_user_question" else "tool_result"
            events.append(
                RunEvent(
                    run_id=run_id,
                    thread_id=thread_id,
                    user_id=user_id,
                    event_key=f"{kind}:{msg.tool_call_id or msg.id or index}",
                    kind=kind,
                    role="tool",
                    name=msg.name,
                    content=content,
                    data={
                        "tool_call_id": msg.tool_call_id,
                        "status": msg.status,
                        "artifacts": _artifacts_in(content),
                    },
                )
            )
            for artifact in _artifacts_in(content):
                events.append(
                    RunEvent(
                        run_id=run_id,
                        thread_id=thread_id,
                        user_id=user_id,
                        event_key=f"artifact:{artifact}",
                        kind="artifact",
                        role="event",
                        name=artifact,
                        data={"ref": artifact},
                    )
                )
            continue

        if content:
            events.append(
                RunEvent(
                    run_id=run_id,
                    thread_id=thread_id,
                    user_id=user_id,
                    event_key=f"message:{msg.id or index}",
                    kind="message",
                    role=role,
                    content=content,
                )
            )
    return events


def estimate_tokens(messages: Sequence[BaseMessage]) -> int:
    """粗略 token 估算：4 字符 ≈ 1 token；用于压缩日志和大小保护。"""
    total = 0
    for msg in messages:
        total += max(1, len(_text(getattr(msg, "content", ""))) // 4)
        tool_calls = getattr(msg, "tool_calls", None) or []
        if tool_calls:
            total += max(1, len(json.dumps(tool_calls, default=str)) // 4)
    return total


def aggregate_token_usage(messages: Sequence[BaseMessage]) -> dict[str, int]:
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for msg in messages:
        usage = getattr(msg, "usage_metadata", None) or {}
        if not isinstance(usage, dict):
            continue
        for key in totals:
            value = usage.get(key, 0)
            if isinstance(value, int):
                totals[key] += value
    return totals


@dataclass
class ProjectedState:
    """after_agent 要写回 checkpoint 的最小状态。"""

    messages: list[BaseMessage]
    history_summary: str
    artifacts: list[str]
    compression: dict[str, Any] = field(default_factory=dict)
    trimmed: bool = False


def _summarize_old(messages: Sequence[BaseMessage]) -> str:
    parts: list[str] = []
    for msg in messages:
        role = _role(msg) or "event"
        content = _text(getattr(msg, "content", ""))
        if isinstance(msg, ToolMessage):
            content = f"{msg.name or 'tool'}: {content[:200]}"
        elif content:
            content = content[:300]
        if content:
            parts.append(f"{role}: {content}")
    return "\n".join(parts)


def project_state(
    messages: Sequence[BaseMessage],
    *,
    history_summary: str = "",
    artifacts: list[str] | None = None,
    keep_last_turns: int | None = None,
    max_tool_chars: int | None = None,
    summary_max_chars: int | None = None,
) -> ProjectedState:
    """保留最近 N 轮 + 历史摘要 + 产物列表；剔除工具输出全文。"""
    from mentor.config.event_settings import event_settings

    keep = keep_last_turns or event_settings.keep_last_turns
    max_tool = max_tool_chars or event_settings.max_tool_chars
    max_summary = summary_max_chars or event_settings.summary_max_chars

    human_indices = [
        index for index, msg in enumerate(messages) if isinstance(msg, HumanMessage)
    ]
    keep_start = human_indices[-keep] if len(human_indices) > keep else 0
    old_messages = messages[:keep_start]
    recent = messages[keep_start:]

    new_summary = history_summary
    if old_messages:
        addition = _summarize_old(old_messages)
        if addition:
            new_summary = f"{history_summary}\n{addition}".strip()
    new_summary = new_summary[-max_summary:]

    trimmed_recent: list[BaseMessage] = []
    changed = bool(old_messages)
    for msg in recent:
        if isinstance(msg, ToolMessage):
            text = _text(msg.content)
            if len(text) > max_tool:
                refs = _artifacts_in(text)
                preview = text[:max_tool].rstrip()
                suffix = "\n[工具输出已修剪；完整内容见 run_events]"
                if refs and refs[-1] not in preview:
                    suffix += f" {refs[-1]}"
                msg = msg.model_copy(update={"content": preview + suffix})
                changed = True
        trimmed_recent.append(msg)

    found_artifacts = list(artifacts or [])
    for msg in messages:
        for ref in _artifacts_in(_text(getattr(msg, "content", ""))):
            if ref not in found_artifacts:
                found_artifacts.append(ref)
    found_artifacts = found_artifacts[-100:]

    before_tokens = estimate_tokens(messages)
    after_tokens = estimate_tokens(trimmed_recent)
    summary_hash = hashlib.sha256(new_summary.encode("utf-8")).hexdigest()[:16]
    compression = {
        "before_tokens": before_tokens,
        "after_tokens": after_tokens,
        "kept_messages": len(trimmed_recent),
        "summary_hash": summary_hash,
        "token_usage": aggregate_token_usage(messages),
    }
    return ProjectedState(
        messages=trimmed_recent,
        history_summary=new_summary,
        artifacts=found_artifacts,
        compression=compression,
        trimmed=changed or bool(old_messages),
    )


def build_summary(events: list[RunEvent]) -> RunSummary:
    """把事件投影成前端摘要；UI 卡片的唯一数据源。"""
    summary: list[dict[str, Any]] = []
    artifacts: list[str] = []
    compression: list[dict[str, Any]] = []
    token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for event in events:
        content = event.content or ""
        summary.append(
            {
                "seq": event.seq,
                "kind": event.kind,
                "role": event.role,
                "name": event.name,
                "content": content[:500],
                "data": event.data,
            }
        )
        if event.kind == "artifact":
            ref = event.data.get("ref") or event.name
            if ref and ref not in artifacts:
                artifacts.append(ref)
        if event.kind == "compression":
            compression.append(event.data)
        usage = event.data.get("token_usage")
        if isinstance(usage, dict):
            for key in token_usage:
                value = usage.get(key, 0)
                if isinstance(value, int):
                    token_usage[key] += value
    first = events[0] if events else None
    started = any(event.kind == "run_started" for event in events)
    finished = any(event.kind == "run_finished" for event in events)
    calls: dict[str, dict[str, Any]] = {}
    completed: dict[str, dict[str, Any]] = {}
    last_completed: dict[str, Any] | None = None
    for event in events:
        call_id = str(event.data.get("tool_call_id") or "")
        if event.kind == "tool_call":
            calls[call_id or str(event.seq)] = {
                "seq": event.seq,
                "name": event.name,
                "tool_call_id": call_id or None,
            }
        elif event.kind in {"tool_result", "approval"}:
            key = call_id or str(event.seq)
            completed[key] = {
                "seq": event.seq,
                "name": event.name,
                "tool_call_id": call_id or None,
                "status": event.data.get("status"),
            }
            last_completed = completed[key]
    pending = [value for key, value in calls.items() if key not in completed]
    status = "finished" if finished else "incomplete" if started else "unknown"
    return RunSummary(
        run_id=first.run_id if first else "",
        thread_id=first.thread_id if first else "",
        user_id=first.user_id if first else "",
        events=events,
        summary=summary,
        artifacts=artifacts,
        token_usage=token_usage,
        compression=compression,
        status=status,
        last_completed_tool=last_completed,
        pending_tools=pending,
    )
