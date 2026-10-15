from typing import NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph

from mentor.utils.cleanup_expired_traces import cleanup_expired_traces


class DeleteOldTraceState(TypedDict):
    submitted_count: NotRequired[int]


def delete_old_trace(state: DeleteOldTraceState) -> DeleteOldTraceState:
    return {"submitted_count": cleanup_expired_traces()}
    # return {"submitted_count": 5}


builder = StateGraph(DeleteOldTraceState)
builder.add_node("delete_old_trace", delete_old_trace)
builder.add_edge(START, "delete_old_trace")
builder.add_edge("delete_old_trace", END)

graph = builder.compile()
