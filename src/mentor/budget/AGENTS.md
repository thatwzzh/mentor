# 单 run 预算

## 职责与入口

`run_budget.py:RunBudget` 是纯数据对象，`budget/__init__.py` 只做导出；使用方是 `middlewares/budget.py:BudgetMiddleware`，本目录不依赖 LangGraph。

## 预算契约

- 三个上限：`max_tokens`、`max_seconds`、`max_output_tokens`，来自 `RUN_BUDGET_*`；前两项为 0 表示不限制。
- `from_settings` 支持 `configurable.run_budget` 覆盖，只作用于本次 run，不写回配置。
- `check_before_model` 先查 deadline，再按 `used + 估算输入 + max_output_tokens` 判断是否会超；不足则抛 `BudgetExceeded`，直接终止 run。
- 调用后 `add_usage` 只累加正的 `total_tokens`；`remaining_tokens` 在无限额时返回 `None`。
- 预算存在 `ContextVar` 里，由 `utils/trace_wrapper.py` 每次图调用设置并在 finally 重置；子 Agent 与异步子代理沿同一份对象，因此父子共享用量。
- 只统计模型 token，不含工具耗时或存储占用；超时判定用单调时钟，不受系统时间调整影响。

## 修改与验证

- 新增预算维度时同步 `config/budget_settings.py`、`.env.example`、`RunBudget` 与检查点位置，不要只在中间件里加判断。
- 单测 `tests/units/test_budget.py` 覆盖超支拒绝、用量回填，以及 `create_model` 的超时与 `max_retries=0`。真实用量口径以 LiteLLM 计费为准，本地估算不能作为账单依据。
