# 评分器与聚合

## 职责与入口

- `create_score_evaluator.py` 按 Langfuse Prompt 创建 LLM Judge；`content_similarity.py`、`emotional_similarity.py` 分别绑定 `evaluator/content_similarity`、`evaluator/emotional_similarity`。
- `schemas.py:EvaluationResult` 约束结构化输出，`weighted_average.py` 输出总体评分。

## 评分契约

- 评分器接收 input / output / expected_output，编译 Prompt 后用 `ToolStrategy(EvaluationResult)` 约束模型输出，返回 Langfuse NUMERIC `Evaluation`。
- `score` 限定 0–1，`reason` 至少一个字符；数值与理由分离，不从自然语言里再解析分数。
- `output` 为空时直接返回 0 分和「没有实际输出」，不调用评审模型；名称仍取 Prompt 配置。
- 评分名称与模型配置都来自 Prompt 的 `config`：`name` 决定 `Evaluation.name`，缺省为 `no-name`，必须与聚合权重表的键精确一致。
- Prompt 的 `model_config` 先经 `AgentModelConfig.model_validate` 校验再传给 `create_model`，不展开成关键字参数；非对象的配置直接抛 `ValueError`，不静默回退默认模型。
- 聚合权重为「内容相似度」0.4、「情绪相似度」0.6，输出名「总体相似度」；按名称建字典后取权重，缺维度抛 `KeyError` 让实验失败，重复名称会互相覆盖。

## 修改与验证

- 新维度同步 Prompt 配置、`experiment.py` 的 `evaluators` 列表、权重表与回归门槛；改权重或名称要说明历史分数不可直接比较。
- 验证空输出、分数边界、结构化响应、名称匹配与聚合公式；本地固定结果只能证明计算，不能证明评审模型可靠。
- 真实验证走父目录的单数据集 Benchmark，依赖 Langfuse、Agent Server 与模型；当前没有独立的评分器单测。
