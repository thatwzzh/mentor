from typing import Any

from deepagents import DeepAgentState


class MainAgentState(DeepAgentState):
    turn_summary: list[Any]
