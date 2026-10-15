from langfuse.api import DatasetItem
from langfuse import get_client
from mentor.benchmarks.dataset import get_items
from langgraph_sdk import get_client as get_agent_client
from mentor.benchmarks.evaluators.weighted_average import weighted_average_evaluator
from mentor.config.benchmark_settings import benchmark_settings
from opentelemetry import trace
from mentor.benchmarks.evaluators.content_similarity import content_similarity
from mentor.benchmarks.evaluators.emotional_similarity import emotional_similarity

langfuse = get_client()


async def experiment_task(*, item: DatasetItem, **kwargs):
    trace_id = langfuse.get_current_trace_id()  # 获取traceid
    parent_span_id = langfuse.get_current_observation_id()  # 获取当前的parent_span_id
    span_attributes = getattr(trace.get_current_span(), "attributes", {})
    env = span_attributes.get("langfuse.environment")
    async with get_agent_client(
        url=benchmark_settings.agent_server,
        headers={"Authorization": f"Bearer {benchmark_settings.user_id}"},
    ) as client:
        result = await client.runs.wait(
            thread_id=None,
            assistant_id=benchmark_settings.graph_id,
            input={"messages": [{"role": "user", "content": item.input}]},
            metadata={
                "trace_id": trace_id,
                "parent_span_id": parent_span_id,
                "trace_environment": env,
            },
        )
    last_message = result.get("messages")[-1]  # type: ignore
    return last_message["content"]


def run_experiment(name: str, dataset_name: str, description: str | None = ""):
    items = get_items(dataset_name)
    # 运行实验
    return langfuse.run_experiment(
        # 给实验取个名字
        name=name,
        # 如果不配置，则使用name+当前时间自动生成
        run_name=None,
        description=description,
        # 此次实验针对哪些数据项
        data=items,
        evaluators=[content_similarity, emotional_similarity],
        composite_evaluator=weighted_average_evaluator,
        task=experiment_task,  # type: ignore
    )
