"""情绪相似度评测器：用 LLM Judge 比较输出与期望的情绪一致性。"""

from mentor.benchmarks.evaluators.create_score_evaluator import create_score_evaluator

# 与内容相似度共用评分工厂，仅 Langfuse Prompt 名称不同。
emotional_similarity = create_score_evaluator("evaluator/emotional_similarity")
