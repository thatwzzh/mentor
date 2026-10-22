"""单 run 预算中间件：模型调用前检查 token/deadline，调用后回填 usage。"""

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, BaseMessage

from mentor.budget.run_budget import get_run_budget
from mentor.events.projection import estimate_tokens


def _response_messages(response: Any) -> list[BaseMessage]:
    response = getattr(response, "model_response", response)
    result = getattr(response, "result", None)
    if isinstance(result, list):
        return result
    if isinstance(response, BaseMessage):
        return [response]
    return []


class BudgetMiddleware(AgentMiddleware):
    """预算在模型调用外层；超限抛 BudgetExceeded，终止 run。"""

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        budget = get_run_budget()
        if budget is None:
            return await handler(request)
        budget.check_before_model(
            estimate_tokens(list(getattr(request, "messages", []) or []))
        )
        response = await handler(request)
        raw_response = response
        messages = _response_messages(response)
        last = next(
            (msg for msg in reversed(messages) if isinstance(msg, AIMessage)),
            None,
        )
        usage = getattr(last, "usage_metadata", None) if last else None
        if isinstance(usage, dict):
            budget.add_usage(int(usage.get("total_tokens", 0) or 0))
        return raw_response
