from langfuse import Evaluation

SCORE_WEIGHT = {"内容相似度": 0.4, "情绪相似度": 0.6}


def weighted_average_evaluator(
    *, evaluations: list[Evaluation], **kwargs
) -> list[Evaluation]:
    scores = {eval.name: float(eval.value) for eval in evaluations}
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
