"""回合摘要中间件：在 Agent 结束后提取用户可见消息，写入 turn_summary。"""

from langchain.agents.middleware import Runtime, after_agent
from deepagents import DeepAgentState
from langchain_core.messages import BaseMessage, AIMessage, ToolMessage, HumanMessage


def _is_visible_msg(msg: BaseMessage):
    """判断一条消息是否应进入前端可见的回合摘要。

    工具调用与普通工具结果不进入摘要，避免把中间过程暴露给用户；
    只保留用户消息、无工具调用的助手回复，以及结构化输出的结果。
    """
    if isinstance(msg, HumanMessage):
        return True
    if isinstance(msg, AIMessage):
        # 没有工具调用，返回True
        if isinstance(msg.content, str):
            return True
        if isinstance(msg.content, list):
            has_tool_call = any(c.get("type") == "tool_use" for c in msg.content)  # type: ignore
            if not has_tool_call:
                return True
    if isinstance(msg, ToolMessage):
        if str(msg.content).startswith("Returning structured response:"):
            return True
    return False


@after_agent
async def turn_summary(state: DeepAgentState, runtime: Runtime):
    # 每次 Agent 结束都从完整消息历史重算一遍，写入 state 的 turn_summary 字段。
    messages = state["messages"]
    summary = []
    for msg in messages:
        if _is_visible_msg(msg):
            summary.append({"role": msg.type, "content": msg.content})

    return {"turn_summary": summary}
