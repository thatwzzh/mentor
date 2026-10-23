# 数据集与离线实验

## 职责与入口

- `dataset.py` 分页读取 Langfuse 数据集（从第 1 页收敛到 `total_pages`），`get_items` 取单数据集条目。
- `experiment.py:run_experiment` 配置实验并逐条执行；`benchmark.py` 提供单数据集与全量入口，实验名前缀固定为 `实验数据集:`。
- 评分细节见 `evaluators/AGENTS.md`；实验也可由 `app/experiment_webhook.py` 触发。

## 执行与追踪契约

- 每条 DatasetItem 的 `input` 作为单条用户消息，经 LangGraph SDK `runs.wait` 发给 `BENCHMARK_AGENT_SERVER` 上的 `BENCHMARK_GRAPH_ID`；`thread_id=None`，不污染真实会话。
- 任务把当前 `trace_id`、`parent_span_id` 和 `langfuse.environment` 写进 run metadata，由 `utils/trace_wrapper.py` 建立父子关联；丢掉这些字段会让 Agent span 与实验 trace 断开。
- `BENCHMARK_USER_ID` 被直接当作 Bearer token 使用，名称不代表它能绕过鉴权；当前 `MOCK_USER` 中对应的键是 `test_user`。
- 输出取返回消息列表最后一条的 `content`；空输出由评分器记 0 分，不调用评审模型。
- `run_benchmark` 是同步函数，被 `BackgroundTasks` 直接调用；全量入口逐个执行，单个数据集失败向上抛，不吞异常。

## 回归标准

- 真实回归入口为 `tests/benchmarks/test_benchmark.py`，用 `make test-benchmark DS=<名称>` 或 `DS=all` 触发，未设置数据集名会直接失败。
- 门槛 0.8：每条用例必须有且只有一条 `总体相似度` 评分，取值有限且落在 0–1，单条与平均分都要达标。
- 输出缺失、评分缺失或重复、评分无效、实验未完成、空数据集都判失败，不用平均分掩盖单条失败。
- 不把模型调用失败当成质量分，也不通过跳过用例让报告通过；改评分维度、权重、Prompt 或门槛时说明对历史可比性的影响。

## 修改与验证

运行实验会调用真实 Agent 与评审模型并写入 Langfuse，产生费用与远程记录，先从单个数据集开始。缺少服务或凭据时只能验证参数组装，需明确说明质量回归未运行。纯文档改动不要启动实验。
