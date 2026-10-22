"""Golden session 的脚本化执行器。

record：运行脚本化模型 + mock 工具，生成 expected。
replay：不调真实 LLM/网络，只重跑同一脚本并断言事件序列与最终输出。
"""

import asyncio
import json
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.checkpoint.memory import InMemorySaver

from mentor.events.approvals import InMemoryApprovalStore
from mentor.events.models import RunEvent
from mentor.golden.models import GoldenCase, GoldenExpected
from mentor.middlewares.budget import BudgetMiddleware
from mentor.middlewares.run_events import RunEventsMiddleware
from mentor.middlewares.token_meter import TokenMeterMiddleware
from mentor.middlewares.tool_executor import ToolExecutorMiddleware
from mentor.middlewares.tool_policy import ToolPolicyMiddleware
from mentor.policy.tool_policy import PolicyRule, ToolPolicy, ToolPolicyConfig
from mentor.tools.specs import ToolRegistry, ToolSpec


class ScriptedChatModel(BaseChatModel):
    """按 golden step 返回固定 AIMessage；不访问网络。"""

    responses: list[AIMessage]
    index: int = 0
    @property
    def _llm_type(self) -> str:
        return "golden-scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        return self

    def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any):
        if self.index >= len(self.responses):
            raise RuntimeError("golden model script exhausted")
        response = self.responses[self.index]
        self.index += 1
        return ChatResult(generations=[ChatGeneration(message=response)])

    async def _agenerate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ):
        return self._generate(messages, stop, run_manager, **kwargs)


def _make_step(step: Any, index: int) -> AIMessage:
    kwargs: dict[str, Any] = {"content": step.content, "id": f"golden-{index}"}
    if step.tool_calls:
        kwargs["tool_calls"] = step.tool_calls
    if step.input_tokens or step.output_tokens:
        kwargs["usage_metadata"] = {
            "input_tokens": step.input_tokens,
            "output_tokens": step.output_tokens,
            "total_tokens": step.input_tokens + step.output_tokens,
        }
    return AIMessage(**kwargs)


def _make_tool(tool_spec: Any) -> BaseTool:
    async def fn(**kwargs: Any) -> str:
        if tool_spec.delay_s:
            await asyncio.sleep(tool_spec.delay_s)
        return tool_spec.result

    return StructuredTool.from_function(
        coroutine=fn,
        name=tool_spec.name,
        description=f"golden mock tool {tool_spec.name}",
    )


def _middleware(
    registry: ToolRegistry,
    policy: ToolPolicy,
    events: list[RunEvent],
) -> list[Any]:
    return [
        RunEventsMiddleware("golden", writer=events.append),
        TokenMeterMiddleware(writer=events.append),
        BudgetMiddleware(),
        ToolPolicyMiddleware(
            registry,
            policy=policy,
            approvals=InMemoryApprovalStore(),
            writer=events.append,
        ),
        ToolExecutorMiddleware(registry, writer=events.append),
    ]


async def _run(case: GoldenCase) -> tuple[list[RunEvent], str]:
    events: list[RunEvent] = []
    registry = ToolRegistry()
    tools: list[BaseTool] = []
    for tool_spec in case.tools:
        tool = _make_tool(tool_spec)
        tools.append(tool)
        registry.register(
            ToolSpec(
                name=tool_spec.name,
                tool=tool,
                risk=tool_spec.risk,
                timeout_s=tool_spec.timeout_s,
            )
        )
    policy = ToolPolicy(
        ToolPolicyConfig(
            rules=[
                PolicyRule(
                    tool=rule.tool,
                    decision=rule.decision,
                    reason=rule.reason,
                )
                for rule in case.policy_rules
            ]
        )
    )
    model = ScriptedChatModel(
        responses=[_make_step(step, index) for index, step in enumerate(case.steps)]
    )
    agent = create_deep_agent(
        model=model,
        tools=tools,
        system_prompt="golden session agent; use tools when asked.",
        middleware=_middleware(registry, policy, events),
        checkpointer=InMemorySaver(),
    )
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=case.input)]},
        config={
            "configurable": {
                "thread_id": f"golden:{case.name}",
                "langgraph_auth_user_id": "golden-user",
            }
        },
    )
    messages: list[BaseMessage] = result.get("messages", [])
    final = ""
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            final = message.content if isinstance(message.content, str) else str(message.content)
            break
    return events, final


def _expected(events: list[RunEvent], final: str) -> GoldenExpected:
    return GoldenExpected(
        event_kinds=[event.kind for event in events],
        event_names=[event.name for event in events],
        final=final,
    )


def record_case(case: GoldenCase) -> GoldenCase:
    events, final = asyncio.run(_run(case))
    return case.model_copy(update={"expected": _expected(events, final)})


def replay_case(case: GoldenCase) -> None:
    if case.expected is None:
        raise ValueError(f"golden case 没有 expected: {case.name}")
    events, final = asyncio.run(_run(case))
    actual = _expected(events, final)
    if actual.event_kinds != case.expected.event_kinds:
        raise AssertionError(
            f"{case.name} event_kinds mismatch:\n"
            f"expected={case.expected.event_kinds}\nactual={actual.event_kinds}"
        )
    if actual.event_names != case.expected.event_names:
        raise AssertionError(
            f"{case.name} event_names mismatch:\n"
            f"expected={case.expected.event_names}\nactual={actual.event_names}"
        )
    if actual.final != case.expected.final:
        raise AssertionError(
            f"{case.name} final mismatch:\n"
            f"expected={case.expected.final!r}\nactual={actual.final!r}"
        )


def load_case(path: str | Path) -> GoldenCase:
    return GoldenCase.model_validate_json(Path(path).read_text(encoding="utf-8"))


def save_case(case: GoldenCase, path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(case.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
