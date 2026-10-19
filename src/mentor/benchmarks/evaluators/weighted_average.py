"""加权汇总评测器：把多个维度分数合成总体相似度。"""

from langfuse import Evaluation

# 键必须与各评测器返回的 Evaluation.name 完全一致，权重之和应为 1.0。
SCORE_WEIGHT = {"内容相似度": 0.4, "情绪相似度": 0.6}


def weighted_average_evaluator(
    *, evaluations: list[Evaluation], **kwargs
) -> list[Evaluation]:
    scores = {eval.name: float(eval.value) for eval in evaluations}
    # 任一维度缺失会抛 KeyError，由 Langfuse 标记本次实验失败，而不是给出偏低总分。
    score = sum(scores[name] * weight for name, weight in SCORE_WEIGHT.items())

    return [
        Evaluation(
            name="总体相似度",
            value=score,
            comment=",".join(
                [f"{name}权重为{weight*100}%" for name, weight in SCORE_WEIGHT.items()]
            ),
            data_type="NUMERIC",
        )
    ]
