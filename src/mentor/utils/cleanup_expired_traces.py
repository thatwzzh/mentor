"""按保留天数提交过期 Langfuse Trace 的删除请求。"""

from datetime import datetime, timedelta, timezone

from langfuse import Langfuse

from mentor.config.langfuse_settings import langfuse_settings


def cleanup_expired_traces() -> int:
    """删除早于保留期的 Trace，返回已提交删除请求的数量。

    单次最多处理 500 条，以免一个计划任务持续运行过久。Langfuse 异步完成删除，
    因此返回值不代表数据已经从存储中消失。
    """
    days = langfuse_settings.trace_retention_days
    if days is None or days <= 0:
        days = 30

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    client = Langfuse(
        public_key=langfuse_settings.public_key,
        secret_key=langfuse_settings.secret_key,
        host=langfuse_settings.base_url,
    )

    # 先收集本次要删的 ID，再提交删除，避免异步删除影响分页结果。
    trace_ids: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        observations = client.api.observations.get_many(
            limit=50,
            cursor=cursor,
            to_start_time=cutoff,
            is_root_observation=True,
            fields="core",
        )
        for observation in observations.data:
            if observation.trace_id is not None:
                trace_ids.append(observation.trace_id)
        cursor = observations.meta.cursor
        if not cursor:
            break

    trace_ids = list(dict.fromkeys(trace_ids))
    for start in range(0, len(trace_ids), 50):
        client.api.trace.delete_multiple(trace_ids=trace_ids[start : start + 50])

    return len(trace_ids)
