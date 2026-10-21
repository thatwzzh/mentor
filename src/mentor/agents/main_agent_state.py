"""主 Agent 的扩展状态：只保留历史摘要与产物列表。

完整消息历史由 run_events 承担；checkpoint 不再复制全量 UI 摘要。
"""

from typing import NotRequired

from deepagents import DeepAgentState


class MainAgentState(DeepAgentState):
    # 滚动历史摘要；由 run_projection 在 after_agent 中更新。
    history_summary: NotRequired[str]
    # 仍需快速访问的产物引用；完整内容在 run_events / artifact 存储。
    artifacts: NotRequired[list[str]]
