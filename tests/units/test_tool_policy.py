"""5.1 工具策略与审批测试：deny/prompt/allow、审批审计、单 run 上限。"""

import asyncio
from types import SimpleNamespace
from typing import Any, cast

from fastapi import HTTPException
from fastapi.testclient import TestClient
from langchain_core.messages import ToolMessage

from mentor.events.approvals import ApprovalEvent, InMemoryApprovalStore
from mentor.events.models import RunEvent
from mentor.middlewares.tool_policy import ToolPolicyMiddleware
from mentor.policy.tool_policy import PolicyRule, ToolPolicy, ToolPolicyConfig
from mentor.tools.specs import ToolRegistry, ToolSpec


def _policy(max_prompts: int = 3, enabled: bool = True) -> ToolPolicy:
    return ToolPolicy(
        ToolPolicyConfig(
            enabled=enabled,
            max_prompts_per_run=max_prompts,
            defaults={
                "read": "allow",
                "write": "allow",
                "external": "allow",
                "destructive": "prompt",
            },
            rules=[
                PolicyRule(
                    tool="execute",
                    pattern=r'rm\s+-rf\s+/(?=[\s"]|$)',
                    decision="deny",
                    reason="root delete",
                ),
                PolicyRule(
                    tool="execute",
                    pattern=r"rm\s+-rf",
                    decision="prompt",
                    reason="recursive delete",
                ),
                PolicyRule(tool="deploy", decision="prompt", reason="deploy"),
            ],
        )
    )


def test_policy_loads_repository_yaml():
    policy = ToolPolicy.load("config/tool_policy.yaml")
    assert policy.decide("execute", {"command": "rm -rf /"}, "destructive").decision == "deny"
    assert policy.decide("deploy", {}, "destructive").decision == "prompt"
    assert policy.decide("read_file", {"path": "/root/a"}, "read").decision == "allow"


def test_policy_decision_order_and_defaults():
    policy = _policy()
    assert (
        policy.decide("execute", {"command": "rm -rf /"}, "destructive").decision
        == "deny"
    )
    assert (
        policy.decide("execute", {"command": "rm -rf /tmp/x"}, "destructive").decision
        == "prompt"
    )
    assert policy.decide("deploy", {}, "destructive").decision == "prompt"
    assert policy.decide("read_file", {"path": "/root/a"}, "read").decision == "allow"
    assert policy.decide("write_file", {"path": "/root/a"}, "write").decision == "allow"
    disabled = _policy(enabled=False)
    assert disabled.decide("execute", {"command": "rm -rf /"}, "destructive").decision == "allow"


def _middleware(
    policy: ToolPolicy,
) -> tuple[ToolPolicyMiddleware, InMemoryApprovalStore, list[RunEvent]]:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="deploy", tool=None, risk="destructive", timeout_s=10)
    )
    registry.register(
        ToolSpec(name="execute", tool=None, risk="destructive", timeout_s=10)
    )
    registry.register(
        ToolSpec(name="read_file", tool=None, risk="read", timeout_s=10)
    )
    approvals = InMemoryApprovalStore()
    events: list[RunEvent] = []
    middleware = ToolPolicyMiddleware(
        registry,
        policy=policy,
        approvals=approvals,
        writer=events.append,
    )
    return middleware, approvals, events


def _request(name: str, call_id: str, args: dict[str, Any]):
    return cast(
        Any,
        SimpleNamespace(
            tool_call={"name": name, "id": call_id, "args": args},
            tool=None,
            state=None,
            runtime=None,
        ),
    )


def test_policy_middleware_denies_without_calling_handler(monkeypatch):
    middleware, approvals, events = _middleware(_policy())
    monkeypatch.setattr(
        "mentor.middlewares.tool_policy.runtime_ids",
        lambda: ("run-1", "thread-1", "user-1"),
    )
    called = False

    async def handler(request):
        nonlocal called
        called = True
        return ToolMessage(content="executed", tool_call_id="c1", name="execute")

    result = asyncio.run(
        middleware.awrap_tool_call(
            _request("execute", "c1", {"command": "rm -rf /"}), handler
        )
    )
    assert called is False
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.artifact["error_kind"] == "policy"
    assert approvals.list("run-1", user_id="user-1")[0].decision == "deny"
    assert events[-1].kind == "approval"


def test_policy_middleware_prompts_and_records_allow(monkeypatch):
    middleware, approvals, _ = _middleware(_policy())
    monkeypatch.setattr(
        "mentor.middlewares.tool_policy.runtime_ids",
        lambda: ("run-2", "thread-2", "user-1"),
    )
    monkeypatch.setattr(
        "mentor.middlewares.tool_policy.interrupt",
        lambda payload: "Approve",
    )
    called = False

    async def handler(request):
        nonlocal called
        called = True
        return ToolMessage(content="deployed", tool_call_id="c2", name="deploy")

    result = asyncio.run(
        middleware.awrap_tool_call(_request("deploy", "c2", {"target": "x"}), handler)
    )
    assert called is True
    assert isinstance(result, ToolMessage) and result.status != "error"
    records = approvals.list("run-2", user_id="user-1")
    assert [record.decision for record in records] == ["requested", "allow"]


def test_policy_middleware_cap_denies_prompt(monkeypatch):
    middleware, approvals, _ = _middleware(_policy(max_prompts=0))
    monkeypatch.setattr(
        "mentor.middlewares.tool_policy.runtime_ids",
        lambda: ("run-3", "thread-3", "user-1"),
    )
    called = False

    async def handler(request):
        nonlocal called
        called = True
        return ToolMessage(content="deployed", tool_call_id="c3", name="deploy")

    result = asyncio.run(
        middleware.awrap_tool_call(_request("deploy", "c3", {}), handler)
    )
    assert called is False
    assert isinstance(result, ToolMessage) and result.status == "error"
    assert approvals.list("run-3", user_id="user-1")[0].decision == "deny"


def test_policy_middleware_allows_read_tool():
    middleware, approvals, _ = _middleware(_policy())
    called = False

    async def handler(request):
        nonlocal called
        called = True
        return ToolMessage(content="ok", tool_call_id="c4", name="read_file")

    result = asyncio.run(
        middleware.awrap_tool_call(_request("read_file", "c4", {"path": "a"}), handler)
    )
    assert called is True
    assert isinstance(result, ToolMessage)
    assert approvals.count("run-4") == 0


def test_approval_query_route_filters_user(monkeypatch):
    from mentor.app import approvals as approvals_module
    from mentor.app.main import app

    store = InMemoryApprovalStore()
    store.append(
        ApprovalEvent(
            run_id="run-a",
            user_id="user-a",
            tool="deploy",
            args_hash="hash",
            decision="deny",
            reason="test",
        )
    )

    async def fake_require_user(authorization: str | None) -> str:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="invalid")
        return "user-a"

    monkeypatch.setattr(approvals_module, "require_user", fake_require_user)
    monkeypatch.setattr(approvals_module, "get_approval_store", lambda: store)
    client = TestClient(app)

    assert client.get("/runs/run-a/approvals").status_code == 401
    response = client.get(
        "/runs/run-a/approvals", headers={"Authorization": "Bearer token"}
    )
    assert response.status_code == 200
    assert response.json()["approvals"][0]["decision"] == "deny"
    missing = client.get(
        "/runs/other/approvals", headers={"Authorization": "Bearer token"}
    )
    assert missing.status_code == 404
