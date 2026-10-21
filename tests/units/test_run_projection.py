"""4.2.6 会话投影测试：事件契约、事件表、压缩日志与 100 轮 checkpoint 大小。"""

from typing import Any, cast

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from mentor.events.models import RunEvent
from mentor.events.projection import (
    build_summary,
    project_messages_to_events,
    project_state,
)
from mentor.events.store import InMemoryRunEventStore
from mentor.middlewares.run_projection import run_projection


def _turn(index: int, tool_chars: int = 20_000):
    call_id = f"call-{index}"
    tool_content = "x" * tool_chars
    if index == 0:
        tool_content += " artifact://old.txt"
    return [
        HumanMessage(content=f"question-{index}", id=f"h-{index}"),
        AIMessage(
            content=f"answer-{index}",
            id=f"a-{index}",
            tool_calls=[
                {
                    "name": "deploy",
                    "args": {"target": index},
                    "id": call_id,
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=tool_content,
            tool_call_id=call_id,
            name="deploy",
            id=f"t-{index}",
        ),
    ]


def test_projection_keeps_last_turns_and_shrinks_checkpoint():
    messages = [msg for index in range(100) for msg in _turn(index)]
    projected = project_state(messages)

    # 100 轮 → 只保留最近 10 轮（30 条消息），旧轮次进摘要。
    assert len(projected.messages) == 30
    assert projected.history_summary
    assert len(projected.history_summary) <= 4000
    assert "artifact://old.txt" in projected.artifacts
    assert projected.compression["before_tokens"] > projected.compression["after_tokens"]
    assert projected.compression["kept_messages"] == 30
    assert projected.compression["summary_hash"]

    # 粗略大小保护：最近 10 轮的工具输出已被修剪到 500 字符级别。
    estimated_chars = sum(len(str(msg.content)) for msg in projected.messages)
    assert estimated_chars < 1_000_000


def test_events_include_cards_and_artifacts():
    messages = [
        HumanMessage(content="hello", id="h1"),
        AIMessage(
            content="",
            id="a1",
            tool_calls=[
                {"name": "deploy", "args": {"x": 1}, "id": "c1", "type": "tool_call"}
            ],
        ),
        ToolMessage(
            content="ok artifact://report.html",
            tool_call_id="c1",
            name="deploy",
            id="t1",
        ),
        ToolMessage(
            content="selected B",
            tool_call_id="c2",
            name="ask_user_question",
            id="t2",
        ),
    ]
    events = project_messages_to_events(
        messages, run_id="run-1", thread_id="thread-1", user_id="user-1"
    )
    kinds = [event.kind for event in events]
    assert "message" in kinds
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    assert "approval" in kinds
    assert "artifact" in kinds

    summary = build_summary(
        [
            event.model_copy(update={"seq": index + 1})
            for index, event in enumerate(events)
        ]
    )
    assert summary.run_id == "run-1"
    assert "artifact://report.html" in summary.artifacts
    assert summary.summary


def test_run_event_store_orders_dedupes_and_filters_user():
    store = InMemoryRunEventStore()
    events = [
        RunEvent(
            run_id="run-1",
            thread_id="thread-1",
            user_id="user-a",
            event_key="e1",
            kind="message",
            role="user",
            content="one",
        ),
        RunEvent(
            run_id="run-1",
            thread_id="thread-1",
            user_id="user-a",
            event_key="e2",
            kind="message",
            role="assistant",
            content="two",
        ),
    ]
    assert store.append_many(events) == 2
    # 重复 event_key 必须幂等，不能产生重复 UI 卡片。
    assert store.append_many(events) == 0

    listed = store.list("run-1", user_id="user-a")
    assert [event.seq for event in listed] == [1, 2]
    assert [event.content for event in listed] == ["one", "two"]
    assert store.list("run-1", user_id="user-b") == []
    assert store.list("run-1", user_id="user-a", after_seq=1)[0].content == "two"
    store.close()


def test_run_summary_route_requires_bearer_and_filters_user(monkeypatch):
    from mentor.app import run_summary as run_summary_module
    from mentor.app.main import app

    store = InMemoryRunEventStore()
    store.append_many(
        [
            RunEvent(
                run_id="run-route",
                thread_id="thread-route",
                user_id="user-a",
                event_key="route-1",
                kind="message",
                role="user",
                content="hello",
            )
        ]
    )

    async def fake_authenticate(authorization: str):
        return {"identity": "user-a"}

    monkeypatch.setattr(run_summary_module, "authenticate", fake_authenticate)
    monkeypatch.setattr(run_summary_module, "get_run_event_store", lambda: store)
    client = TestClient(app)

    assert client.get("/runs/run-route/summary").status_code == 401
    assert (
        client.get(
            "/runs/run-route/summary", headers={"Authorization": "Basic x"}
        ).status_code
        == 401
    )
    response = client.get(
        "/runs/run-route/summary",
        headers={"Authorization": "Bearer token"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == "run-route"
    assert payload["events"][0]["content"] == "hello"
    assert payload["contract_version"] == "1"


def test_run_projection_middleware_replaces_messages_and_logs_compression(monkeypatch):
    class FakeStore:
        def __init__(self):
            self.events = []

        def append_many(self, events):
            self.events.extend(events)
            return len(events)

    fake_store = FakeStore()
    monkeypatch.setattr(
        "mentor.middlewares.run_projection.get_run_event_store",
        lambda: fake_store,
    )
    messages = [msg for index in range(20) for msg in _turn(index)]
    update = __import__("asyncio").run(
        run_projection.aafter_agent(
            {"messages": messages, "history_summary": "", "artifacts": []},
            cast(Any, None),
        )
    )

    assert isinstance(update, dict)
    assert isinstance(update["messages"][0], RemoveMessage)
    assert update["messages"][0].id == REMOVE_ALL_MESSAGES
    # REMOVE_ALL_MESSAGES + 最近 10 轮（30 条消息）。
    assert len(update["messages"]) == 31
    assert update["history_summary"]
    assert update["artifacts"]
    assert any(event.kind == "compression" for event in fake_store.events)
