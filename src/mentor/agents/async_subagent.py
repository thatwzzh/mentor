"""异步子代理：父 run 只拿 job_id，后台执行并写完成事件。

这是进程内最小实现：不做消息板、角色市场、跨进程 ACP。
任务状态保存在进程内；进程退出后未完成任务丢失，但事件和 run 审计仍可查。
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage

from mentor.utils.subagent_context import (
    get_subagent_depth,
    reset_subagent_depth,
    set_subagent_depth,
)
from mentor.agents.subagent_registry import get_subagent_factory
from mentor.config.nacos.nacos_agent_spec import get_agent_spec
from mentor.config.subagent_settings import subagent_settings
from mentor.events.context import runtime_ids
from mentor.events.models import RunEvent, RunEventKind
from mentor.events.writer import EventWriter, append_event

logger = logging.getLogger(__name__)

SubAgentStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


@dataclass(frozen=True)
class SubAgentSpec:
    """子代理声明；默认只读、最大深度 1、180 秒超时。"""

    name: str
    description: str = ""
    max_depth: int = 1
    timeout_seconds: float = 180
    result_schema: dict[str, Any] | None = None
    read_only: bool = True


@dataclass
class SubAgentJob:
    job_id: str
    spec: SubAgentSpec
    task: str
    parent_run_id: str
    parent_thread_id: str
    user_id: str
    status: SubAgentStatus = "queued"
    result: Any = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    task_handle: asyncio.Task | None = field(default=None, repr=False)


class AsyncSubAgentRunner:
    """启动/查询/取消后台子代理；主 run 不等待子代理完成。"""

    def __init__(self, writer: EventWriter = append_event) -> None:
        self.writer = writer
        self._jobs: dict[str, SubAgentJob] = {}

    async def spawn(
        self,
        agent_name: str,
        task: str,
        *,
        spec: SubAgentSpec | None = None,
    ) -> SubAgentJob:
        spec = spec or await self._load_spec(agent_name)
        if get_subagent_depth() >= spec.max_depth:
            raise RuntimeError(
                f"子代理深度超过上限 {spec.max_depth}，禁止递归委派"
            )
        if get_subagent_factory(agent_name) is None:
            raise ValueError(f"子代理未注册: {agent_name}")
        run_id, thread_id, user_id = runtime_ids()
        job = SubAgentJob(
            job_id=uuid.uuid4().hex,
            spec=spec,
            task=task,
            parent_run_id=run_id,
            parent_thread_id=thread_id,
            user_id=user_id,
        )
        self._jobs[job.job_id] = job
        job.status = "running"
        self._write(job, "subagent_started")
        job.task_handle = asyncio.create_task(self._run(job))
        return job

    async def _load_spec(self, agent_name: str) -> SubAgentSpec:
        spec = await get_agent_spec(agent_name)
        return SubAgentSpec(
            name=spec.name,
            description=spec.description,
            max_depth=subagent_settings.max_depth,
            timeout_seconds=subagent_settings.timeout_seconds,
            result_schema=None,
            read_only=True,
        )

    async def _run(self, job: SubAgentJob) -> None:
        depth_token = set_subagent_depth(get_subagent_depth() + 1)
        try:
            factory = get_subagent_factory(job.spec.name)
            if factory is None:
                raise ValueError(f"子代理未注册: {job.spec.name}")
            runnable = await factory(label=None)
            result = await asyncio.wait_for(
                runnable.ainvoke(
                    {"messages": [HumanMessage(content=job.task)]},
                    config={
                        "configurable": {
                            "thread_id": f"{job.parent_thread_id}:sub:{job.job_id}",
                            "langgraph_auth_user_id": job.user_id,
                        }
                    },
                ),
                timeout=job.spec.timeout_seconds,
            )
            job.result = self._extract_result(result)
            job.status = "succeeded"
            self._write(job, "subagent_finished")
        except asyncio.CancelledError:
            job.status = "cancelled"
            job.error = "cancelled"
            self._write(job, "subagent_cancelled")
            raise
        except asyncio.TimeoutError:
            job.status = "failed"
            job.error = f"timeout after {job.spec.timeout_seconds}s"
            self._write(job, "subagent_failed")
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc) or type(exc).__name__
            self._write(job, "subagent_failed")
        finally:
            reset_subagent_depth(depth_token)

    @staticmethod
    def _extract_result(result: Any) -> Any:
        if isinstance(result, dict):
            structured = result.get("structured_response")
            if structured is not None:
                return structured
            messages = result.get("messages") or []
        else:
            messages = getattr(result, "messages", []) or []
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                return msg.content
        return None

    def _write(self, job: SubAgentJob, kind: RunEventKind) -> None:
        try:
            self.writer(
                RunEvent(
                    run_id=job.parent_run_id,
                    thread_id=job.parent_thread_id,
                    user_id=job.user_id,
                    event_key=f"{kind}:{job.job_id}",
                    kind=kind,
                    role="event",
                    name=job.spec.name,
                    content=str(job.result)[:500] if job.result is not None else None,
                    data={
                        "job_id": job.job_id,
                        "agent": job.spec.name,
                        "task": job.task[:500],
                        "status": job.status,
                        "error": job.error,
                        "result": job.result,
                        "timeout_seconds": job.spec.timeout_seconds,
                        "max_depth": job.spec.max_depth,
                    },
                )
            )
        except Exception as exc:
            logger.warning("子代理事件写入失败 job=%s: %s", job.job_id, exc)

    def get(self, job_id: str) -> SubAgentJob | None:
        return self._jobs.get(job_id)

    async def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.task_handle is None:
            return False
        if job.task_handle.done():
            return False
        job.task_handle.cancel()
        job.status = "cancelled"
        job.error = "cancelled by caller"
        self._write(job, "subagent_cancelled")
        return True

    def snapshot(self, job: SubAgentJob) -> dict[str, Any]:
        # 父级只拿摘要，不把子代理完整 transcript 塞回主上下文。
        result = job.result
        if isinstance(result, str) and len(result) > 2_000:
            result = result[:2_000] + "...[truncated]"
        return {
            "job_id": job.job_id,
            "agent": job.spec.name,
            "task": job.task[:500],
            "status": job.status,
            "result": result,
            "error": job.error,
            "created_at": job.created_at.isoformat(),
        }


_runner: AsyncSubAgentRunner | None = None


def get_async_subagent_runner() -> AsyncSubAgentRunner:
    global _runner
    if _runner is None:
        _runner = AsyncSubAgentRunner()
    return _runner


def json_job(job: SubAgentJob) -> str:
    return json.dumps(get_async_subagent_runner().snapshot(job), ensure_ascii=False)
