"""工具策略中间件：执行前 allow / prompt / deny，并写 approval_events。"""

import hashlib
import json
import logging
import time
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import interrupt

from mentor.events.approvals import (
    ApprovalDecision,
    ApprovalEvent,
    ApprovalStoreProtocol,
    get_approval_store,
)
from mentor.utils.subagent_context import get_subagent_depth
from mentor.events.context import runtime_ids
from mentor.events.models import RunEvent
from mentor.events.writer import EventWriter, append_event
from mentor.policy.tool_policy import ToolPolicy, get_tool_policy
from mentor.tools.specs import ToolRegistry

logger = logging.getLogger(__name__)


def _args_hash(args: dict[str, Any]) -> str:
    raw = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _approved(answer: Any) -> bool:
    if isinstance(answer, bool):
        return answer
    if isinstance(answer, str):
        return answer.strip().lower() in {
            "approve",
            "approved",
            "allow",
            "yes",
            "同意",
            "批准",
        }
    if isinstance(answer, dict):
        if "approved" in answer:
            return bool(answer["approved"])
        decision = str(answer.get("decision") or "").lower()
        return decision in {"allow", "approve", "approved", "yes"}
    return False


def _error_message(
    name: str, tool_call_id: str, reason: str
) -> ToolMessage:
    return ToolMessage(
        content=f"[policy] {reason}",
        tool_call_id=tool_call_id,
        name=name,
        status="error",
        artifact={
            "ok": False,
            "data": None,
            "error_kind": "policy",
            "truncated": False,
            "artifacts": [],
            "duration_ms": 0,
        },
    )


class ToolPolicyMiddleware(AgentMiddleware):
    """策略位于 ToolExecutor 外层：deny/prompt 不会触发工具副作用。"""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        policy: ToolPolicy | None = None,
        approvals: ApprovalStoreProtocol | None = None,
        writer: EventWriter = append_event,
    ) -> None:
        super().__init__()
        self.registry = registry
        self.policy = policy or get_tool_policy()
        self.approvals = approvals
        self.writer = writer

    def _store(self) -> ApprovalStoreProtocol | None:
        if self.approvals is not None:
            return self.approvals
        try:
            self.approvals = get_approval_store()
            return self.approvals
        except Exception as exc:
            logger.warning("approval store 不可用，prompt 将 fail-closed: %s", exc)
            return None

    def _record(
        self,
        *,
        run_id: str,
        thread_id: str,
        user_id: str,
        name: str,
        args: dict[str, Any],
        decision: ApprovalDecision,
        reason: str,
        tool_call_id: str,
    ) -> None:
        store = self._store()
        approval_id = 0
        if store is not None:
            try:
                record = store.append(
                    ApprovalEvent(
                        run_id=run_id,
                        thread_id=thread_id,
                        user_id=user_id,
                        tool=name,
                        args_hash=_args_hash(args),
                        decision=decision,
                        reason=reason,
                    )
                )
                approval_id = record.id
            except Exception as exc:
                logger.warning("approval_events 写入失败 run_id=%s: %s", run_id, exc)
        try:
            self.writer(
                RunEvent(
                    run_id=run_id,
                    thread_id=thread_id,
                    user_id=user_id,
                    event_key=f"approval:{tool_call_id}:{decision}",
                    kind="approval",
                    role="event",
                    name=name,
                    content=reason,
                    data={
                        "tool_call_id": tool_call_id or None,
                        "decision": decision,
                        "reason": reason,
                        "args_hash": _args_hash(args),
                        "approval_id": approval_id,
                    },
                )
            )
        except Exception as exc:
            logger.warning("approval 事件写入失败 run_id=%s: %s", run_id, exc)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        tool_call = getattr(request, "tool_call", {}) or {}
        name = str(tool_call.get("name") or "")
        args = tool_call.get("args") or {}
        if not isinstance(args, dict):
            args = {"value": args}
        call_id = str(tool_call.get("id") or f"{name}-{time.monotonic_ns()}")
        run_id, thread_id, user_id = runtime_ids()
        spec = self.registry.get(name) or self.registry.default(name)
        if get_subagent_depth() > 0 and spec.risk in {"write", "destructive"}:
            reason = "子代理默认只读，禁止 write/destructive 工具"
            self._record(
                run_id=run_id,
                thread_id=thread_id,
                user_id=user_id,
                name=name,
                args=args,
                decision="deny",
                reason=reason,
                tool_call_id=call_id,
            )
            return _error_message(name, call_id, reason)
        decision = self.policy.decide(name, args, spec.risk)
        if decision.decision == "allow":
            return await handler(request)

        if decision.decision == "deny":
            self._record(
                run_id=run_id,
                thread_id=thread_id,
                user_id=user_id,
                name=name,
                args=args,
                decision="deny",
                reason=decision.reason,
                tool_call_id=call_id,
            )
            return _error_message(name, call_id, f"拒绝执行: {decision.reason}")

        # prompt：先检查单 run 审批上限，再暂停等待人工决策。
        store = self._store()
        if store is None:
            self._record(
                run_id=run_id,
                thread_id=thread_id,
                user_id=user_id,
                name=name,
                args=args,
                decision="deny",
                reason=f"审批存储不可用: {decision.reason}",
                tool_call_id=call_id,
            )
            return _error_message(name, call_id, "审批存储不可用，fail-closed")

        if store.count(run_id) >= self.policy.max_prompts_per_run:
            self._record(
                run_id=run_id,
                thread_id=thread_id,
                user_id=user_id,
                name=name,
                args=args,
                decision="deny",
                reason=f"单 run 审批次数超过上限 {self.policy.max_prompts_per_run}",
                tool_call_id=call_id,
            )
            return _error_message(name, call_id, "审批次数超过上限，已拒绝")

        self._record(
            run_id=run_id,
            thread_id=thread_id,
            user_id=user_id,
            name=name,
            args=args,
            decision="requested",
            reason=decision.reason,
            tool_call_id=call_id,
        )
        answer = interrupt(
            {
                "type": "tool_approval",
                "run_id": run_id,
                "tool_call_id": call_id,
                "tool": name,
                "args": args,
                "risk": spec.risk,
                "reason": decision.reason,
                "options": ["Approve", "Deny"],
            }
        )
        approved = _approved(answer)
        final = "allow" if approved else "deny"
        self._record(
            run_id=run_id,
            thread_id=thread_id,
            user_id=user_id,
            name=name,
            args=args,
            decision=final,
            reason=decision.reason,
            tool_call_id=call_id,
        )
        if approved:
            return await handler(request)
        return _error_message(name, call_id, f"人工拒绝: {decision.reason}")
