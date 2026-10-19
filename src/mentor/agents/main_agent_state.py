"""主 Agent 的扩展状态：在 DeepAgentState 上增加回合摘要字段。"""

from typing import Any

from deepagents import DeepAgentState


class MainAgentState(DeepAgentState):
    turn_summary: list[Any]
