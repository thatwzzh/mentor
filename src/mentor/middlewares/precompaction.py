"""回答结束后的异步预压缩：下一轮注入摘要，避免在用户提问后等摘要模型。"""

import asyncio
import hashlib
import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from mentor.config.context_settings import context_settings
from mentor.config.event_settings import event_settings
from mentor.events.context import runtime_ids
from mentor.events.context_summary import (
    ContextSummaryStoreProtocol,
    PendingSummary,
    get_context_summary_store,
)
from mentor.events.models import RunEvent
from mentor.events.projection import estimate_tokens
from mentor.events.writer import EventWriter, append_event
from mentor.middlewares.context import context_window_for

logger = logging.getLogger(__name__)


def _trim_for_summary(messages: list[Any], max_tokens: int) -> list[Any]:
    """保留最靠近当前轮的旧消息，避免把整个超长历史塞给摘要模型。"""
    selected: list[Any] = []
    used = 0
    for msg in reversed(messages):
        cost = estimate_tokens([msg])
        if selected and used + cost > max_tokens:
            break
        selected.append(msg)
        used += cost
    return list(reversed(selected))


class PostTurnCompactionMiddleware(AgentMiddleware):
    """after_agent 检查水位，命中后后台生成摘要；不在请求路径等待。"""

    def __init__(
        self,
        summarization: Any,
        *,
        store: ContextSummaryStoreProtocol | None = None,
        writer: EventWriter = append_event,
    ) -> None:
        super().__init__()
        self.summarization = summarization
        self.store = store
        self.writer = writer
        self._tasks: set[asyncio.Task] = set()

    def _store(self) -> ContextSummaryStoreProtocol | None:
        if self.store is not None:
            return self.store
        try:
            self.store = get_context_summary_store()
            return self.store
        except Exception as exc:
            logger.warning("预压缩摘要存储不可用: %s", exc)
            return None

    async def aafter_agent(self, state: Any, runtime: Any) -> None:
        try:
            messages = list(state.get("messages", []) or [])
            human_indexes = [
                index
                for index, msg in enumerate(messages)
                if isinstance(msg, HumanMessage)
            ]
            keep = event_settings.keep_last_turns
            if len(human_indexes) <= keep:
                return None
            run_id, thread_id, user_id = runtime_ids()
            if not thread_id:
                return None
            window = context_window_for(getattr(self.summarization, "model", None))
            token_count = self.summarization._count_tokens(messages, None, None)
            if token_count < int(window * context_settings.precompact_ratio):
                return None
            store = self._store()
            if store is None or store.get(thread_id) is not None:
                return None
            old_messages = messages[: human_indexes[-keep]]
            selected = _trim_for_summary(
                old_messages, context_settings.trim_tokens_to_summarize
            )
            if not selected:
                return None
            task = asyncio.create_task(
                self._compact(
                    selected=selected,
                    run_id=run_id,
                    thread_id=thread_id,
                    user_id=user_id,
                    token_before=token_count,
                )
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        except Exception as exc:
            # 预压缩失败不能影响已完成的回答。
            logger.warning("预压缩调度失败: %s", exc)
        return None

    async def _compact(
        self,
        *,
        selected: list[Any],
        run_id: str,
        thread_id: str,
        user_id: str,
        token_before: int,
    ) -> None:
        try:
            summary = await self.summarization._acreate_summary(selected)
            if not summary.strip():
                return
            store = self._store()
            if store is None:
                return
            store.upsert(
                PendingSummary(
                    thread_id=thread_id,
                    run_id=run_id,
                    user_id=user_id,
                    summary=summary,
                    token_before=token_before,
                )
            )
            summary_hash = hashlib.sha256(summary.encode("utf-8")).hexdigest()[:16]
            self.writer(
                RunEvent(
                    run_id=run_id,
                    thread_id=thread_id,
                    user_id=user_id,
                    event_key=f"compression:precompact:{thread_id}:{summary_hash}",
                    kind="compression",
                    role="event",
                    data={
                        "source": "precompact",
                        "token_before": token_before,
                        "summary_hash": summary_hash,
                        "summary": summary[:2000],
                    },
                )
            )
        except Exception as exc:
            logger.warning("后台预压缩失败 thread_id=%s: %s", thread_id, exc)


class PendingCompactionMiddleware(AgentMiddleware):
    """下一轮开始前注入预压缩摘要；只做 DB 读取和消息替换，不调摘要模型。"""

    def __init__(
        self,
        *,
        store: ContextSummaryStoreProtocol | None = None,
    ) -> None:
        super().__init__()
        self.store = store

    def _store(self) -> ContextSummaryStoreProtocol | None:
        if self.store is not None:
            return self.store
        try:
            self.store = get_context_summary_store()
            return self.store
        except Exception as exc:
            logger.warning("预压缩摘要读取失败: %s", exc)
            return None

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        _, thread_id, _ = runtime_ids()
        if not thread_id:
            return None
        store = self._store()
        if store is None:
            return None
        try:
            pending = store.get(thread_id)
        except Exception as exc:
            logger.warning("预压缩摘要读取失败 thread_id=%s: %s", thread_id, exc)
            return None
        if pending is None:
            return None
        messages = list(state.get("messages", []) or [])
        # 已经注入过同一摘要时避免重复。
        if messages and isinstance(messages[0], HumanMessage):
            if messages[0].additional_kwargs.get("lc_source") == "summarization":
                store.delete(thread_id)
                return None
        summary_message = HumanMessage(
            content=pending.summary,
            additional_kwargs={"lc_source": "summarization"},
        )
        store.delete(thread_id)
        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                summary_message,
                *messages,
            ]
        }
