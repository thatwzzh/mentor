"""主 Agent 的扩展状态：在 DeepAgentState 上增加回合摘要字段。"""

from typing import Any

from deepagents import DeepAgentState


class MainAgentState(DeepAgentState):
    # 前端按回合渲染用的可见消息摘要，由 turn_summary 中间件写入；
    # 它不替代 checkpoint，也不参与模型上下文。
    turn_summary: list[Any]
