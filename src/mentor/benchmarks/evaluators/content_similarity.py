"""内容相似度评测器：用 LLM Judge 比较实际输出与期望输出。"""

from mentor.benchmarks.evaluators.create_score_evaluator import create_score_evaluator

# 提示词放在 Langfuse Prompt 中，名称固定；修改评分标准不需要改代码。
content_similarity = create_score_evaluator("evaluator/content_similarity")
