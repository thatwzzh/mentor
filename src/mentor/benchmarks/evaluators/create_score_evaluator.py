from langfuse import Evaluation, get_client
from mentor.utils.create_model import create_model
from mentor.benchmarks.evaluators.schemas import EvaluationResult
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.messages import HumanMessage


def create_score_evaluator(prompt_name):

    async def content_similarity(
        *, input: str, output: str, expected_output: str, **kwargs
    ):
        langfuse = get_client()
        prompt = langfuse.get_prompt(prompt_name)
        model_config = prompt.config.get("model_config", {})
        eval_name = prompt.config.get("name", "no-name")
        if not output:
            return Evaluation(
                name=eval_name,
                value=0,
                comment="没有实际输出",
                data_type="NUMERIC",
            )
        model = create_model(**model_config)
        agent = create_agent(
            model=model, response_format=ToolStrategy(EvaluationResult)
        )
        prompt_content = prompt.compile(
            input=input, output=output, expected_output=expected_output
        )
        result = await agent.ainvoke(input={"messages": [HumanMessage(prompt_content)]})
        score = result["structured_response"].score
        reason = result["structured_response"].reason
        return Evaluation(
            name=eval_name, value=score, comment=reason, data_type="NUMERIC"
        )

    return content_similarity
