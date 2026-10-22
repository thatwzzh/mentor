"""5.5 单 run 预算测试：预检查、usage 回填、超限中止、模型超时配置。"""

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage, HumanMessage

from mentor.budget.run_budget import (
    BudgetExceeded,
    RunBudget,
    reset_run_budget,
    set_run_budget,
)
from mentor.middlewares.budget import BudgetMiddleware


def test_run_budget_rejects_projected_overspend():
    budget = RunBudget(max_tokens=1000, max_output_tokens=200)
    budget.check_before_model(500)
    budget.add_usage(300)
    with pytest.raises(BudgetExceeded):
        budget.check_before_model(600)  # 300 + 600 + 200 > 1000


def test_budget_middleware_records_usage(monkeypatch):
    budget = RunBudget(max_tokens=1000, max_output_tokens=100)
    token = set_run_budget(budget)
    events = []
    middleware = BudgetMiddleware()
    request = cast(
        Any,
        SimpleNamespace(messages=[HumanMessage(content="x" * 40, id="h1")]),
    )

    async def handler(request):
        return ModelResponse(
            result=[
                AIMessage(
                    content="ok",
                    id="a1",
                    usage_metadata={
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "total_tokens": 15,
                    },
                )
            ]
        )

    try:
        asyncio.run(middleware.awrap_model_call(request, handler))
        assert budget.used_tokens == 15
    finally:
        reset_run_budget(token)


def test_create_model_sets_explicit_timeout_and_no_agent_retry(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from mentor.config.nacos.nacos_schemas import AgentModelConfig
    from mentor.utils.create_model import create_model

    model = create_model(AgentModelConfig(name="test-model"))
    assert model.request_timeout == 120
    assert model.max_retries == 0
