"""Agent 结束时的会话投影：写 run_events，并把 checkpoint 修剪到最近 N 轮。

checkpoint 只保留运行所需的最小状态；UI/审计读取事件表，不再从 state 里反复
复制全量消息。
"""

import logging
import uuid
from typing import Any

from langchain.agents.middleware import Runtime, after_agent
from langchain_core.messages import RemoveMessage
from langgraph.config import get_config
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from mentor.agents.main_agent_state import MainAgentState
from mentor.events.models import RunEvent
from mentor.events.projection import (
    project_messages_to_events,
    project_state,
)
from mentor.events.store import get_run_event_store
from mentor.utils.shared import get_thread_id, get_user_id

logger = logging.getLogger(__name__)


def _runtime_ids() -> tuple[str, str, str]:
    """读取 run_id/thread_id/user_id；脱离图时返回可用的本地兜底。"""
    try:
        config = get_config() or {}
    except RuntimeError:
        config = {}
    configurable = config.get("configurable", {}) or {}
    run_id = str(
        config.get("run_id")
        or configurable.get("run_id")
        or configurable.get("thread_id")
        or uuid.uuid4().hex
    )
    thread_id = str(configurable.get("thread_id") or "")
    try:
        user_id = get_user_id()
    except RuntimeError:
        user_id = "anonymous"
    return run_id, thread_id, user_id


@after_agent
async def run_projection(state: MainAgentState, runtime: Runtime):
    """投影事件并返回最小 checkpoint 更新；事件表失败不影响 run。"""
    messages = list(state.get("messages", []) or [])
    run_id, thread_id, user_id = _runtime_ids()
    store = None
    try:
        store = get_run_event_store()
        events = project_messages_to_events(
            messages, run_id=run_id, thread_id=thread_id, user_id=user_id
        )
        store.append_many(events)
    except Exception as exc:
        # 审计失败不应让业务 run 失败；保留完整日志供补偿任务重放。
        logger.warning("run_events 写入失败 run_id=%s: %s", run_id, exc)

    projected = project_state(
        messages,
        history_summary=str(state.get("history_summary", "") or ""),
        artifacts=list(state.get("artifacts", []) or []),
    )
    if store is not None:
        try:
            store.append_many(
                [
                    RunEvent(
                        run_id=run_id,
                        thread_id=thread_id,
                        user_id=user_id,
                        event_key=f"compression:{run_id}:{projected.compression['summary_hash']}",
                        kind="compression",
                        role="event",
                        data=projected.compression,
                    )
                ]
            )
        except Exception as exc:
            logger.warning("compression 事件写入失败 run_id=%s: %s", run_id, exc)

    logger.info(
        "run_projection run_id=%s before_tokens=%s after_tokens=%s kept_messages=%s summary_hash=%s",
        run_id,
        projected.compression["before_tokens"],
        projected.compression["after_tokens"],
        projected.compression["kept_messages"],
        projected.compression["summary_hash"],
    )

    update: dict[str, Any] = {}
    if projected.trimmed:
        # messages 是 DeltaChannel；REMOVE_ALL_MESSAGES 才能替换而不是追加。
        update["messages"] = [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            *projected.messages,
        ]
    if projected.history_summary != str(state.get("history_summary", "") or ""):
        update["history_summary"] = projected.history_summary
    if projected.artifacts != list(state.get("artifacts", []) or []):
        update["artifacts"] = projected.artifacts
    return update or None
