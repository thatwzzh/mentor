"""DeepAgents 沙箱后端：把文件读写、命令执行和删除映射到 OpenSandbox。

一次 run 内按 (user, thread) 复用同一个 sandbox 连接；Agent 结束时由
sandbox_lifecycle 中间件调用 aclose_run 关闭。删除路径只允许 /root 下，
并通过文件 API 删除，避免 shell 拼接注入。
"""

import asyncio
import hashlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import PurePosixPath

from deepagents.backends.protocol import (
    DeleteResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from opensandbox import Sandbox
from opensandbox.exceptions import SandboxApiException
from opensandbox.models.execd import RunCommandOpts

from mentor.config.nacos.nacos_config import get_config
from mentor.config.nacos.nacos_schemas import OpenSandboxConfig, SandboxProfile
from mentor.config.sandbox_profiles import to_open_config
from mentor.config.sandbox_settings import sandbox_settings
from mentor.utils.sandbox import SHARED_VOLUME_NAME, VolumeConfig, get_sandbox
from mentor.utils.shared import get_thread_id, get_user_id

logger = logging.getLogger(__name__)

type IdGetter = Callable[[], str]


@dataclass
class _SandboxEntry:
    """线程级沙箱缓存条目；last_used_at 用于空闲回收。"""

    sandbox: Sandbox
    last_used_at: float


def _validate_delete_path(file_path: str) -> str:
    """只允许删除沙箱内 /root 下的普通工作区路径。

    这里是词法校验；父目录若通过符号链接指向 /root 外，由沙箱内 realpath 再拦一次。
    """
    if not isinstance(file_path, str) or not file_path or "\x00" in file_path:
        raise ValueError("删除路径必须是非空字符串")
    if "\n" in file_path or "\r" in file_path:
        raise ValueError("删除路径不能包含换行符")
    path = PurePosixPath(file_path)
    if not path.is_absolute():
        raise ValueError("删除路径必须是绝对路径")
    if ".." in path.parts:
        raise ValueError("删除路径不能包含 ..")
    if path.parts[:2] != ("/", "root") or path == PurePosixPath("/root"):
        raise ValueError("只允许删除 /root 下的工作区路径")
    return str(path)


class SandboxBackend(BaseSandbox):

    id_getter: IdGetter

    def __init__(
        self,
        id_getter: IdGetter | None = None,
        profile: SandboxProfile | None = None,
    ) -> None:
        super().__init__()
        if not id_getter:
            id_getter = get_user_id
        self.id_getter = id_getter
        self.profile = profile
        self.profile_config = to_open_config(profile) if profile else None
        # key = (user_id, thread_id)；在事件循环线程内读写，dict 操作只用轻量锁保护。
        self._sandboxes: dict[tuple[str, str], _SandboxEntry] = {}
        self._cache_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._cache_guard = threading.Lock()

    @property
    def id(self) -> str:
        return self.id_getter()

    def _cache_key(self) -> tuple[str, str]:
        # thread_id 缺失时用空串，保证脚本/测试仍能落到一个稳定 key。
        return (self.id, get_thread_id() or "")

    def _get_volume_configs(self) -> list[VolumeConfig]:
        # /root 是该用户的持久工作区；/shared 只读挂载公共技能与依赖。
        # PVC 名使用 tenant + user_id 哈希，避免下划线/特殊字符碰撞。
        digest = hashlib.sha256(self.id.encode("utf-8")).hexdigest()[:12]
        return [
            VolumeConfig(
                mount_path="/root",
                readonly=False,
                name=f"{sandbox_settings.tenant}-{digest}",
            ),
            VolumeConfig(mount_path="/shared", readonly=True, name=SHARED_VOLUME_NAME),
        ]

    async def _get_or_create_sandbox(self) -> Sandbox:
        """按 (user, thread) 复用连接；首次创建用 per-key lock 防并发重复创建。"""
        key = self._cache_key()
        with self._cache_guard:
            entry = self._sandboxes.get(key)
            if entry is not None:
                entry.last_used_at = time.monotonic()
                return entry.sandbox

        lock = self._cache_locks.setdefault(key, asyncio.Lock())
        async with lock:
            with self._cache_guard:
                entry = self._sandboxes.get(key)
                if entry is not None:
                    entry.last_used_at = time.monotonic()
                    return entry.sandbox

            await self._close_idle()
            # sandbox_id 仍按 user_id 存入 Store，兼容已有工作区与集成测试。
            sandbox = await get_sandbox(
                self.id,
                volume_configs=self._get_volume_configs(),
                sandbox_config=self.profile_config,
            )
            with self._cache_guard:
                self._sandboxes[key] = _SandboxEntry(
                    sandbox=sandbox, last_used_at=time.monotonic()
                )
            return sandbox

    async def _close_sandbox(self, sandbox: Sandbox) -> None:
        try:
            await sandbox.close()
        except Exception as exc:
            # 清理失败不应覆盖原始工具异常；交给 OpenSandbox TTL 兜底。
            logger.warning("关闭沙箱连接失败: %s", exc)

    async def _close_idle(self) -> None:
        timeout = sandbox_settings.idle_timeout_seconds
        if timeout <= 0:
            return
        now = time.monotonic()
        with self._cache_guard:
            idle = [
                (key, entry)
                for key, entry in self._sandboxes.items()
                if now - entry.last_used_at >= timeout
            ]
            for key, _ in idle:
                self._sandboxes.pop(key, None)
        for _, entry in idle:
            await self._close_sandbox(entry.sandbox)

    async def aclose_run(self) -> None:
        """关闭当前 (user, thread) 的连接；Agent 结束钩子调用。"""
        key = self._cache_key()
        with self._cache_guard:
            entry = self._sandboxes.pop(key, None)
        if entry is not None:
            await self._close_sandbox(entry.sandbox)

    async def aclose_all(self) -> None:
        """关闭当前 backend 的所有缓存连接，供测试/运维使用。"""
        with self._cache_guard:
            entries = list(self._sandboxes.values())
            self._sandboxes.clear()
        for entry in entries:
            await self._close_sandbox(entry.sandbox)

    # BackendProtocol 的同步接口；asyncio.run 只能在非事件循环线程中调用。
    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return asyncio.run(self.aexecute(command, timeout=timeout))

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return asyncio.run(self.aupload_files(files))

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return asyncio.run(self.adownload_files(paths))

    async def aexecute(
        self, command: str, *, timeout: int | None = None
    ) -> ExecuteResponse:
        # sandbox_config 由 Nacos 下发并经 func_cache 缓存；timeout<0 表示使用服务端默认值。
        config = self.profile_config or await get_config(
            "sandbox_config", OpenSandboxConfig
        )
        if timeout is None or timeout < 0:
            timeout = config.command_timeout_seconds
        sandbox = await self._get_or_create_sandbox()
        try:
            execution = await sandbox.commands.run(
                command, opts=RunCommandOpts(timeout=timedelta(seconds=timeout))
            )
        except Exception:
            # 命令通道异常时丢弃当前连接，避免坏连接被后续工具复用。
            await self.aclose_run()
            raise
        return ExecuteResponse(
            output=str(execution), exit_code=execution.exit_code, truncated=False
        )

    async def aupload_files(
        self, files: list[tuple[str, bytes]]
    ) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        sandbox = await self._get_or_create_sandbox()
        for path, data in files:
            try:
                await sandbox.files.write_file(path, data)
                responses.append(FileUploadResponse(path=path))
            except Exception as exec:
                responses.append(FileUploadResponse(path=path, error=str(exec)))
        return responses

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        sandbox = await self._get_or_create_sandbox()
        for path in paths:
            try:
                content = await sandbox.files.read_bytes(path)
                responses.append(FileDownloadResponse(path=path, content=content))
            except Exception as exec:
                responses.append(FileDownloadResponse(path=path, error=str(exec)))
        return responses

    async def adelete(self, file_path: str) -> DeleteResult:
        try:
            safe_path = _validate_delete_path(file_path)
        except ValueError as exc:
            return DeleteResult(error=str(exc))

        sandbox = await self._get_or_create_sandbox()
        # 只解析父目录：父级若是指向 /root 外的符号链接，解析后会被下面的前缀检查拦住；
        # 最后一段保持原样，删除符号链接本身而不是它的目标。
        parent = PurePosixPath(safe_path).parent
        resolved = await sandbox.commands.run(["realpath", "-m", "--", str(parent)])
        canonical_parent = "".join(
            message.text for message in resolved.logs.stdout
        ).strip()
        if resolved.exit_code != 0 or not canonical_parent:
            return DeleteResult(error=f"无法解析删除路径的父目录: {safe_path}")
        canonical = str(PurePosixPath(canonical_parent) / PurePosixPath(safe_path).name)
        if not canonical.startswith("/root/") or canonical == "/root":
            return DeleteResult(error=f"拒绝删除 /root 之外的路径: {canonical}")

        try:
            infos = await sandbox.files.get_file_info([canonical])
        except SandboxApiException as exc:
            if "not found" in str(exc).lower():
                # 删除不存在的路径保持幂等，与原 rm -rf 行为一致。
                return DeleteResult(path=safe_path)
            return DeleteResult(error=f"删除路径{safe_path}失败: {exc}")

        info = infos.get(canonical)
        try:
            if info is not None and info.entry_type == "directory":
                await sandbox.files.delete_directories([canonical])
            else:
                await sandbox.files.delete_files([canonical])
        except SandboxApiException as exc:
            return DeleteResult(error=f"删除路径{safe_path}失败: {exc}")
        return DeleteResult(path=safe_path)
