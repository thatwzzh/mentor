"""DeepAgents 沙箱后端：把文件读写、命令执行和删除映射到 OpenSandbox。"""

import asyncio
from collections.abc import Callable
from datetime import timedelta
from pathlib import PurePosixPath

from deepagents.backends.protocol import (
    DeleteResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from opensandbox.exceptions import SandboxApiException
from opensandbox.models.execd import RunCommandOpts

from mentor.config.nacos.nacos_config import get_config
from mentor.config.nacos.nacos_schemas import OpenSandboxConfig
from mentor.utils.sandbox import SHARED_VOLUME_NAME, VolumeConfig, enter_sandbox
from mentor.utils.shared import get_user_id

type IdGetter = Callable[[], str]


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

    def __init__(self, id_getter: IdGetter | None = None) -> None:
        super().__init__()
        if not id_getter:
            id_getter = get_user_id
        self.id_getter = id_getter

    @property
    def id(self) -> str:
        return self.id_getter()

    def _get_volume_configs(self) -> list[VolumeConfig]:
        # /root 是该用户的持久工作区；/shared 只读挂载公共技能与依赖，避免用户间互相污染。
        return [
            VolumeConfig(
                mount_path="/root", readonly=False, name=self.id.replace("_", "-")
            ),
            VolumeConfig(mount_path="/shared", readonly=True, name=SHARED_VOLUME_NAME),
        ]

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
        config = await get_config("sandbox_config", OpenSandboxConfig)
        if timeout is None or timeout < 0:
            timeout = config.command_timeout_seconds
        async with enter_sandbox(
            self.id, volume_configs=self._get_volume_configs()
        ) as sandbox:
            execution = await sandbox.commands.run(
                command, opts=RunCommandOpts(timeout=timedelta(seconds=timeout))
            )
        return ExecuteResponse(
            output=str(execution), exit_code=execution.exit_code, truncated=False
        )

    async def aupload_files(
        self, files: list[tuple[str, bytes]]
    ) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        async with enter_sandbox(
            self.id, volume_configs=self._get_volume_configs()
        ) as sandbox:
            for path, data in files:
                try:
                    await sandbox.files.write_file(path, data)
                    responses.append(FileUploadResponse(path=path))
                except Exception as exec:
                    responses.append(FileUploadResponse(path=path, error=str(exec)))
        return responses

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        async with enter_sandbox(
            self.id, volume_configs=self._get_volume_configs()
        ) as sandbox:
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

        async with enter_sandbox(
            self.id, volume_configs=self._get_volume_configs()
        ) as sandbox:
            # 只解析父目录：父级若是指向 /root 外的符号链接，解析后会被下面的前缀检查拦住；
            # 最后一段保持原样，删除符号链接本身而不是它的目标。
            parent = PurePosixPath(safe_path).parent
            resolved = await sandbox.commands.run(
                ["realpath", "-m", "--", str(parent)]
            )
            canonical_parent = "".join(
                message.text for message in resolved.logs.stdout
            ).strip()
            if resolved.exit_code != 0 or not canonical_parent:
                return DeleteResult(
                    error=f"无法解析删除路径的父目录: {safe_path}"
                )
            canonical = str(PurePosixPath(canonical_parent) / PurePosixPath(safe_path).name)
            if not canonical.startswith("/root/") or canonical == "/root":
                return DeleteResult(
                    error=f"拒绝删除 /root 之外的路径: {canonical}"
                )

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
