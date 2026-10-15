from pydantic import BaseModel, Field


class EvaluationResult(BaseModel):
    score: float = Field(ge=0, le=1, description="当前维度的评分")
    reason: str = Field(min_length=1, description="用一句话说明评分的主要原因")
