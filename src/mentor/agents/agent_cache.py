"""Agent 编译缓存：按 (agent_name, spec_hash) 复用已编译图，并用 LRU 限制版本数。

缓存只保存图对象，不负责关闭被淘汰的图；正在运行的请求仍持有自己的引用，
因此淘汰旧版本不会中断 in-flight run。缓存读写都在事件循环线程内完成，
不需要额外加锁；并发首次编译由 AgentFactory 的 per-agent lock 串行化。
"""

import time
from collections import OrderedDict
from dataclasses import dataclass

from langchain_core.runnables import Runnable


@dataclass
class _CacheEntry:
    """一个已编译规格版本的缓存条目。"""

    agent: Runnable
    spec_hash: str
    created_at: float
    last_used_at: float
    hits: int = 0


class AgentCache:
    """进程内 LRU 缓存，按 Agent 限制保留的规格版本数。"""

    def __init__(self, max_versions_per_agent: int = 3) -> None:
        if max_versions_per_agent < 1:
            raise ValueError("max_versions_per_agent 必须大于等于 1")
        self._max_versions = max_versions_per_agent
        self._entries: OrderedDict[tuple[str, str], _CacheEntry] = OrderedDict()

    def get(self, agent_name: str, spec_hash: str) -> Runnable | None:
        """命中时刷新 LRU 顺序与使用统计，未命中返回 None。"""
        key = (agent_name, spec_hash)
        entry = self._entries.get(key)
        if entry is None:
            return None
        entry.last_used_at = time.monotonic()
        entry.hits += 1
        self._entries.move_to_end(key)
        return entry.agent

    def put(self, agent_name: str, spec_hash: str, agent: Runnable) -> None:
        """写入或替换一个版本，并按 Agent 淘汰最久未使用的旧版本。"""
        key = (agent_name, spec_hash)
        now = time.monotonic()
        if key in self._entries:
            entry = self._entries[key]
            entry.agent = agent
            entry.last_used_at = now
            entry.hits = 0
            self._entries.move_to_end(key)
        else:
            self._entries[key] = _CacheEntry(
                agent=agent,
                spec_hash=spec_hash,
                created_at=now,
                last_used_at=now,
            )
        self._prune(agent_name)

    def _prune(self, agent_name: str) -> None:
        # 只淘汰同一 Agent 的旧版本，避免一个 Agent 的更新挤掉其他 Agent 的缓存。
        keys = [key for key in self._entries if key[0] == agent_name]
        while len(keys) > self._max_versions:
            oldest = keys.pop(0)
            self._entries.pop(oldest, None)

    def clear(self, agent_name: str | None = None) -> None:
        """清空全部缓存，或只清空指定 Agent 的缓存。"""
        if agent_name is None:
            self._entries.clear()
            return
        for key in [key for key in self._entries if key[0] == agent_name]:
            self._entries.pop(key, None)

    def stats(self) -> dict[str, object]:
        """返回缓存条目数与每个 Agent 的版本数，供日志/健康检查使用。"""
        per_agent: dict[str, int] = {}
        for agent_name, _ in self._entries:
            per_agent[agent_name] = per_agent.get(agent_name, 0) + 1
        return {"entries": len(self._entries), "agents": per_agent}
