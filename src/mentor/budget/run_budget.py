"""单 run 预算：token 上限与 deadline，跨主 Agent/子 Agent 共享。"""

import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


class BudgetExceeded(RuntimeError):
    """单 run token/时间预算超限。"""


@dataclass
class RunBudget:
    max_tokens: int = 0
    max_seconds: float = 0
    max_output_tokens: int = 4096
    used_tokens: int = 0
    started_at: float = field(default_factory=time.monotonic)

    @classmethod
    def from_settings(cls, overrides: dict[str, Any] | None = None) -> "RunBudget":
        from mentor.config.budget_settings import budget_settings

        values = {
            "max_tokens": budget_settings.max_tokens,
            "max_seconds": budget_settings.max_seconds,
            "max_output_tokens": budget_settings.max_output_tokens,
        }
        if isinstance(overrides, dict):
            for key in values:
                if key in overrides:
                    values[key] = overrides[key]
        return cls(
            max_tokens=max(int(values["max_tokens"] or 0), 0),
            max_seconds=max(float(values["max_seconds"] or 0), 0),
            max_output_tokens=max(int(values["max_output_tokens"] or 4096), 1),
        )

    def check_deadline(self) -> None:
        if self.max_seconds and time.monotonic() - self.started_at > self.max_seconds:
            raise BudgetExceeded(
                f"run 超过时间预算 {self.max_seconds}s"
            )

    def check_before_model(self, estimated_input_tokens: int) -> None:
        self.check_deadline()
        if not self.max_tokens:
            return
        projected = self.used_tokens + max(estimated_input_tokens, 0) + self.max_output_tokens
        if self.used_tokens >= self.max_tokens or projected > self.max_tokens:
            raise BudgetExceeded(
                f"run token 预算不足: used={self.used_tokens}, "
                f"estimated_input={estimated_input_tokens}, "
                f"reserve={self.max_output_tokens}, max={self.max_tokens}"
            )

    def add_usage(self, total_tokens: int) -> None:
        if total_tokens > 0:
            self.used_tokens += total_tokens

    @property
    def remaining_tokens(self) -> int | None:
        if not self.max_tokens:
            return None
        return max(self.max_tokens - self.used_tokens, 0)


_current_budget: ContextVar[RunBudget | None] = ContextVar(
    "current_run_budget", default=None
)


def set_run_budget(budget: RunBudget) -> Any:
    return _current_budget.set(budget)


def reset_run_budget(token: Any) -> None:
    _current_budget.reset(token)


def get_run_budget() -> RunBudget | None:
    return _current_budget.get()
