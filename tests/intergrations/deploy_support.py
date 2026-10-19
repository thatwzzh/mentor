"""Deploy 验收环境：真实 ToolNode、OpenSandbox、MinIO，不替换上传结果。"""

import asyncio
import importlib
import json
import os
import shlex
from pathlib import PurePosixPath
from contextlib import AsyncExitStack
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import timedelta
from functools import wraps
from typing import Any, cast
from uuid import uuid4

import pytest
from aiobotocore.client import AioBaseClient
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.store.memory import InMemoryStore
from opensandbox import Sandbox
from opensandbox.config import ConnectionConfig

from mentor.backends.sandbox_backend import SandboxBackend
from mentor.config.nacos.nacos_schemas import OpenSandboxConfig
from mentor.config.minio_settings import MinioSettings
from mentor.config.sandbox_settings import sandbox_settings
from mentor.utils import sandbox as sandbox_utils
from mentor.utils.minio import MinIO

_IN_DEPLOY = ContextVar("deploy_integration_call", default=False)


def forbid_relay_during_deploy(original):
    async def guarded(*args, **kwargs):
        if _IN_DEPLOY.get():
            raise AssertionError("Deploy 必须在沙箱内直传，禁止宿主下载/上传产物")
        return await original(*args, **kwargs)

    return guarded


def forbid_stream_during_deploy(original):
    async def guarded(*args, **kwargs):
        if _IN_DEPLOY.get():
            raise AssertionError("Deploy 必须在沙箱内直传，禁止宿主流式下载产物")
        async for chunk in original(*args, **kwargs):
            yield chunk

    return guarded


@dataclass
class LiveDeploy:
    minio: Any
    bucket: str
    settings: MinioSettings
    store: InMemoryStore
    stack: AsyncExitStack
    guard: pytest.MonkeyPatch
    users: dict[str, Sandbox] = field(default_factory=dict)
    guarded_classes: set[type] = field(default_factory=set)
    commands: list[str] = field(default_factory=list)

    async def user(self):
        user_id = f"deploy-it-{uuid4().hex}"
        sandbox = await Sandbox.create(
            image=os.environ["DEPLOY_SANDBOX_IMAGE"],
            connection_config=ConnectionConfig(
                domain=sandbox_settings.domain,
                api_key=sandbox_settings.api_key,
                use_server_proxy=True,
            ),
            timeout=timedelta(minutes=10),
            ready_timeout=timedelta(seconds=180),
            # 故意不使用项目 _create：不创建 deleteOnSandboxTermination=False 的卷。
            volumes=[],
        )
        self.stack.push_async_callback(sandbox.close)
        self.stack.push_async_callback(sandbox.kill)
        self.users[user_id] = sandbox
        filesystem_type = type(sandbox.files)
        if filesystem_type not in self.guarded_classes:
            for name in ("read_file", "read_bytes"):
                self.guard.setattr(
                    filesystem_type,
                    name,
                    forbid_relay_during_deploy(getattr(filesystem_type, name)),
                )
            self.guard.setattr(
                filesystem_type,
                "read_bytes_stream",
                forbid_stream_during_deploy(filesystem_type.read_bytes_stream),
            )
            self.guarded_classes.add(filesystem_type)
        commands_type = type(sandbox.commands)
        if commands_type not in self.guarded_classes:
            original_run = commands_type.run

            async def audited_run(service, command, *args, **kwargs):
                if _IN_DEPLOY.get():
                    self.commands.append(command)
                return await original_run(service, command, *args, **kwargs)

            self.guard.setattr(commands_type, "run", audited_run)
            self.guarded_classes.add(commands_type)
        await self.store.aput(("__sandbox__",), user_id, {"sandbox_id": sandbox.id})
        return user_id

    async def command(self, user_id, script):
        result = await self.users[user_id].commands.run(
            "python -c " + shlex.quote(script)
        )
        assert result.exit_code == 0, str(result)

    async def files(self, user_id, files):
        # 字节只用于测试数据准备；Deploy 执行期间禁止宿主下载产物。
        parents = sorted({str(PurePosixPath(path).parent) for path in files})
        await self.command(
            user_id,
            f"""
import pathlib
for name in {parents!r}:
    pathlib.Path(name).mkdir(parents=True, exist_ok=True)
""",
        )
        for path, data in files.items():
            await self.users[user_id].files.write_file(path, data)

    async def invoke(self, user_id, **args):
        module = importlib.import_module("mentor.tools.deploy")
        graph = StateGraph(MessagesState)
        graph.add_node("tools", ToolNode([module.deploy], handle_tool_errors=False))
        graph.add_edge(START, "tools")
        graph.add_edge("tools", END)
        app = graph.compile(store=self.store)
        config: RunnableConfig = {"configurable": {"thread_id": uuid4().hex}}
        if user_id is not None:
            config["configurable"]["langgraph_auth_user_id"] = user_id

        token = _IN_DEPLOY.set(True)
        try:
            output = await app.ainvoke(
                {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": module.deploy.name,
                                    "args": args,
                                    "id": uuid4().hex,
                                    "type": "tool_call",
                                }
                            ],
                        )
                    ]
                },
                config=config,
            )
        finally:
            _IN_DEPLOY.reset(token)
        message = output["messages"][-1]
        assert isinstance(message, ToolMessage)
        # 预签名 URL 的 X-Amz-Credential 会包含 access key id（签名协议的一部分），
        # 但 secret key 绝不能出现在工具结果里。
        content = str(message.content)
        secret = self.settings.secret_key
        if len(secret) >= 8:
            assert secret not in content, "工具结果不得泄露 MinIO secret key"
        return message

    async def deploy(self, user_id, **args):
        message = await self.invoke(user_id, **args)
        return self.result(message)

    def result(self, message):
        assert message.status == "success", message.content
        assert isinstance(message.content, str)
        result = json.loads(message.content)
        assert isinstance(result, dict), "Deploy 尚未实现或未返回 DeployOutput"
        assert set(result) == {
            "total_count",
            "success_count",
            "failure_count",
            "failures",
            "urls",
        }
        for key in ("total_count", "success_count", "failure_count"):
            assert type(result[key]) is int and result[key] >= 0
        assert (
            result["total_count"] == result["success_count"] + result["failure_count"]
        )
        assert isinstance(result["failures"], dict)
        assert len(result["failures"]) == result["failure_count"]
        assert isinstance(result["urls"], list)
        assert all(isinstance(url, str) for url in result["urls"])
        return result


def live_deploy_test(test):
    @wraps(test)
    def run(*args, **kwargs):
        async def scenario():
            async with AsyncExitStack() as stack:
                minio = await stack.enter_async_context(kwargs["deploy_env"].connect())
                bucket = await minio.private_bucket()
                # 测试仅在进程内替换部署模块专用桶，不修改生产设置。
                config_module = importlib.import_module("mentor.config.minio_settings")
                tool_module = importlib.import_module("mentor.tools.deploy")
                settings = MinioSettings(**config_module.minio_settings.model_dump())
                backend_module = importlib.import_module(
                    "mentor.backends.sandbox_backend"
                )

                async def sandbox_config(name, schema):
                    assert name == "sandbox_config" and schema is OpenSandboxConfig
                    return OpenSandboxConfig()

                async def forbid_new_persistent_sandbox(**_kwargs):
                    raise AssertionError(
                        "工具未使用登记到 graph store 的用户沙箱；禁止创建持久卷"
                    )

                original_api_call = cast(Any, AioBaseClient)._make_api_call

                async def guard_s3_upload(client, operation_name, *args, **kwargs):
                    if (
                        _IN_DEPLOY.get()
                        and client.meta.service_model.service_name == "s3"
                    ):
                        assert operation_name not in {
                            "PutObject",
                            "CreateMultipartUpload",
                            "UploadPart",
                            "CompleteMultipartUpload",
                        }, "禁止通过宿主 S3 SDK 中转上传"
                    return await original_api_call(
                        client, operation_name, *args, **kwargs
                    )

                with pytest.MonkeyPatch.context() as config:
                    config.setattr(config_module, "minio_settings", settings)
                    config.setattr(tool_module, "minio_settings", settings)
                    config.setattr(tool_module, "_BUCKET_NAME", bucket)
                    config.setattr(sandbox_utils, "get_config", sandbox_config)
                    config.setattr(
                        sandbox_utils, "_create", forbid_new_persistent_sandbox
                    )
                    config.setattr(backend_module, "get_config", sandbox_config)
                    config.setattr(AioBaseClient, "_make_api_call", guard_s3_upload)
                    for cls, names in (
                        (SandboxBackend, ("adownload_files",)),
                        (MinIO, ("upload_file", "upload_dir", "write_file")),
                    ):
                        for name in names:
                            config.setattr(
                                cls,
                                name,
                                forbid_relay_during_deploy(getattr(cls, name)),
                            )
                    live = LiveDeploy(
                        minio, bucket, settings, InMemoryStore(), stack, config
                    )
                    await test(*args, **(kwargs | {"deploy_env": live}))

        asyncio.run(scenario())

    return run
