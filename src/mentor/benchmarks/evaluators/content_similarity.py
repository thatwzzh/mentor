"""内容相似度评测器：用 LLM Judge 比较实际输出与期望输出。"""

from mentor.benchmarks.evaluators.create_score_evaluator import create_score_evaluator

content_similarity = create_score_evaluator("evaluator/content_similarity")
