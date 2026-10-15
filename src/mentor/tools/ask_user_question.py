from langchain.tools import tool
from langgraph.types import interrupt
from pydantic import BaseModel, Field, field_validator


class QuestionOption(BaseModel):
    """用户可选择的单选项。"""

    title: str = Field(description="选项标题，也是用户选择后返回给模型的值")
    description: str = Field(description="选项的简短说明，帮助用户理解选择的影响")
    recommended: bool = Field(
        default=False,
        description="是否为推荐选项；一个问题最多只能有一个推荐选项",
    )


class AskUserQuestionInput(BaseModel):
    """ask_user_question 工具的输入。"""

    question: str = Field(description="需要询问用户的一个明确问题")
    options: list[QuestionOption] = Field(
        min_length=2,
        max_length=5,
        description=(
            "提供给用户的2到5个互斥选项。最多只能有一个推荐选项。"
            "不要添加‘其他’选项，界面会自动允许用户输入其他答案"
        ),
    )

    # options 字段校验完成后，再检查选项之间的组合关系
    @field_validator("options")
    @classmethod
    def validate_options(cls, options: list[QuestionOption]) -> list[QuestionOption]:
        # 推荐的总数大于1时验证失败
        if sum([option.recommended for option in options]) > 1:
            # 抛出的ValueError会被Pydantic捕获，然后封装成统一的ValidationError
            # ToolNode 会将该字段校验错误封装成 ToolMessage 返回给模型
            raise ValueError("一个问题最多只能有一个推荐选项")

        # 标题重复时验证失败
        titles = [option.title for option in options]
        if len(titles) != len(set(titles)):
            raise ValueError("选项标题不能重复")

        return options


@tool(args_schema=AskUserQuestionInput)
def ask_user_question(question: str, options: list[QuestionOption]) -> str:
    """
    当缺少必要信息、需要向用户提问时，向用户提出一个单选问题并暂停执行。
    用户可以选择一个已有选项，也可以输入自己的答案。
    """
    answer = interrupt(
        {
            "type": "ask_user_question",
            "question": question,
            "options": [option.model_dump() for option in options],
        }
    )
    answer = str(answer)
    if not answer:
        # 这个错误会导致图被终止，无法重试
        raise ValueError(f'对于问题"{question}"请给予你的选择')
    return answer
