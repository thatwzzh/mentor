"""情绪相似度评测器：用 LLM Judge 比较输出与期望的情绪一致性。"""

from mentor.benchmarks.evaluators.create_score_evaluator import create_score_evaluator

emotional_similarity = create_score_evaluator("evaluator/emotional_similarity")
