"""回合结束异步抽取长记忆候选；敏感信息直接跳过，不写入。"""

import asyncio
import json
import logging
import re
from typing import Any, cast

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import BaseMessage, HumanMessage

from mentor.config.memory_settings import memory_settings
from mentor.events.context import runtime_ids
from mentor.memory.embedding import get_memory_embedder
from mentor.memory.models import MemoryKind, MemoryRecord
from mentor.memory.store import MemoryStoreProtocol, get_memory_store
from mentor.utils.shared import get_tenant_id

logger = logging.getLogger(__name__)

_EXTRACT_PROMPT = """你是记忆抽取器。只抽取用户长期有效、可复用的事实/偏好/决策/TODO。
不要抽取一次性寒暄、临时状态、工具输出或推测。
返回 JSON 数组，每项：
{"kind":"preference|fact|decision|todo", "key":"稳定短键", "content":"事实", "confidence":0.0-1.0}
没有可抽取内容返回 []。只输出 JSON，不要 Markdown。
"""

_SENSITIVE = re.compile(
    r"(api[_-]?key|password|secret|access[_-]?token|bearer\s+[a-z0-9._-]+|"
    r"sk-[a-z0-9]{8,}|-----BEGIN)",
    re.IGNORECASE,
)


def _messages_text(messages: list[BaseMessage]) -> str:
    parts = []
    for message in messages[-12:]:
        content = getattr(message, "content", "")
        if isinstance(content, str) and content.strip():
            parts.append(f"{message.type}: {content}")
    return "\n".join(parts)[-6000:]


def _parse_candidates(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


class MemoryExtractor:
    """用主模型异步抽取候选；失败只告警。"""

    def __init__(
        self,
        model: Any,
        store: MemoryStoreProtocol | None = None,
    ) -> None:
        self.model = model
        self.store = store

    def _store(self) -> MemoryStoreProtocol | None:
        if self.store is not None:
            return self.store
        try:
            self.store = get_memory_store()
            return self.store
        except Exception as exc:
            logger.warning("记忆抽取存储不可用: %s", exc)
            return None

    async def extract(self, messages: list[BaseMessage]) -> int:
        text = _messages_text(messages)
        if len(text) < memory_settings.extract_min_chars:
            return 0
        response = await self.model.ainvoke(
            [HumanMessage(content=f"{_EXTRACT_PROMPT}\n\n对话:\n{text}")]
        )
        content = getattr(response, "content", "")
        candidates = _parse_candidates(str(content))[: memory_settings.max_candidates]
        store = self._store()
        if store is None:
            return 0
        _, thread_id, user_id = runtime_ids()
        tenant_id = get_tenant_id()
        written = 0
        for item in candidates:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            fact = str(item.get("content") or "").strip()
            if not key or not fact:
                continue
            if _SENSITIVE.search(f"{key} {fact}"):
                # 第一版不落敏感信息；要求用户显式确认后再写入。
                continue
            kind_value: MemoryKind = cast(
                MemoryKind,
                item.get("kind")
                if item.get("kind") in {"preference", "fact", "decision", "todo"}
                else "fact",
            )
            try:
                confidence = float(item.get("confidence", 0.7))
            except (TypeError, ValueError):
                confidence = 0.7
            if confidence < memory_settings.min_confidence:
                continue
            embedding = await get_memory_embedder().embed(f"{key}: {fact}")
            store.write(
                MemoryRecord(
                    tenant_id=tenant_id,
                    embedding=embedding,
                    user_id=user_id,
                    kind=kind_value,
                    key=key[:120],
                    content=fact[:2000],
                    confidence=min(max(confidence, 0.0), 1.0),
                    source_thread_id=thread_id,
                )
            )
            written += 1
        return written


class MemoryExtractionMiddleware(AgentMiddleware):
    """回答结束后异步抽取候选，不阻塞主 run。"""

    def __init__(self, model: Any) -> None:
        super().__init__()
        self.extractor = MemoryExtractor(model)
        self._tasks: set[asyncio.Task] = set()

    async def aafter_agent(self, state: Any, runtime: Any) -> None:
        if not memory_settings.auto_extract:
            return None
        messages = list(state.get("messages", []) or [])
        if not messages:
            return None
        task = asyncio.create_task(self.extractor.extract(messages))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return None
