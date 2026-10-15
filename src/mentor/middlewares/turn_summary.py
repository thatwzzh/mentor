from langchain.agents.middleware import Runtime, after_agent
from deepagents import DeepAgentState
from langchain_core.messages import BaseMessage, AIMessage, ToolMessage, HumanMessage


def _is_visible_msg(msg: BaseMessage):
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
    messages = state["messages"]
    summary = []
    # 完成列表的追加
    for msg in messages:
        if _is_visible_msg(msg):
            summary.append({"role": msg.type, "content": msg.content})

    return {"turn_summary": summary}
