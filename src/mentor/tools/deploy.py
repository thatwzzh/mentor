"""产物部署工具：在沙箱内把文件/目录直传 MinIO，并返回入口 URL。"""

import base64
import json
import shlex
from datetime import timedelta
from pathlib import PurePosixPath
from typing import TypedDict, cast

from langchain.tools import tool
from langchain_core.tools import ToolException
from pydantic import BaseModel, Field

from mentor.backends.sandbox_backend import SandboxBackend
from mentor.config.minio_settings import minio_settings
from mentor.utils.minio import MinIO
from mentor.utils.shared import get_user_id


# 部署模块专用桶；其他上传模块自行定义各自的桶名。
_BUCKET_NAME = minio_settings.bucket
# 沙箱脚本用该前缀输出唯一一行 JSON 结果，宿主只解析这一行，忽略 stderr/日志。
_RESULT_MARKER = "__DEPLOY_RESULT__="

# 此脚本由镜像自带的 Python 环境运行，文件内容始终留在沙箱内。
_UPLOAD_SCRIPT = '''
import asyncio
import base64
import json
import mimetypes
from dataclasses import asdict
from pathlib import Path
from minio import ContentTypeStrategy, MinIO

config = json.loads(base64.b64decode(CONFIG_DATA))

class DeployInputError(Exception):
    """模型可以通过修改路径或入口参数修复的错误。"""

async def main():
    source = Path(config["path"])
    if source.is_symlink():
        raise DeployInputError("产物路径不能是符号链接")
    if not source.exists():
        raise DeployInputError(f"产物路径不存在: {source}")
    if not source.is_dir() and not source.is_file():
        raise DeployInputError("产物路径必须是普通文件或目录")

    # 提前检查入口，避免交付不存在或被目录上传跳过的文件。
    for entry in config["entry_files"]:
        candidate = source / entry if source.is_dir() else source
        if source.is_file() and entry != source.name:
            raise DeployInputError(f"单文件部署的入口必须是 {source.name}")
        if not candidate.is_file():
            raise DeployInputError(f"入口文件不存在: {entry}")
        if any(p.is_symlink() for p in (candidate, *candidate.parents)):
            raise DeployInputError(f"入口文件路径不能包含符号链接: {entry}")

    mimetypes.init()
    types = {extension.lower(): content_type
             for extension, content_type in mimetypes.types_map.items()}
    types.update({".html": "text/html; charset=utf-8",
                  ".css": "text/css; charset=utf-8",
                  ".js": "text/javascript; charset=utf-8",
                  ".mjs": "text/javascript; charset=utf-8"})
    client = MinIO(
        endpoint=config["endpoint"],
        access_key=config["access_key"],
        secret_key=config["secret_key"],
        secure=config["secure"],
        content_type_strategy=ContentTypeStrategy(extensions=types),
    )
    bucket = config["bucket_name"]
    # 产物桶保持私有；访问通过宿主生成的预签名 URL 完成。
    await client.create_bucket(bucket, allow_public_read=False)
    if source.is_dir():
        # 目录部署采用整体替换：先清空目标目录，再上传新产物。
        # 必须带末尾 /，避免误删同一用户下名称相似的其他目录。
        # 删除失败会停止部署；后续上传失败不会恢复已删除的旧文件。
        directory_prefix = config["prefix"] + "/"
        for filename in await client.list_files(bucket, prefix=directory_prefix):
            await client.remove_file(bucket_name=bucket, filename=filename)
        uploaded = await client.upload_dir(
            bucket_name=bucket, source_dir=str(source), prefix=config["prefix"],
        )
        result = asdict(uploaded)
        result["failures"] = {
            str(source / relative): reason
            for relative, reason in uploaded.failures.items()
        }
        successful_entries = [
            entry for entry in config["entry_files"]
            if entry not in uploaded.failures
        ]
    else:
        failures = {}
        try:
            await client.upload_file(
                bucket_name=bucket,
                filename=f'{config["prefix"]}/{source.name}',
                source_file=str(source),
            )
        except Exception as exc:
            failures[str(source)] = f"{type(exc).__name__}: {exc}"
        result = {"total_count": 1, "success_count": 1 - len(failures),
                  "failure_count": len(failures), "failures": failures}
        successful_entries = [] if failures else config["entry_files"]
    result["successful_entries"] = successful_entries
    return result

try:
    payload = {"result": asyncio.run(main())}
except DeployInputError as exc:
    payload = {"error_kind": "input", "error": str(exc)}
except Exception as exc:
    payload = {"error_kind": "system", "error": f"{type(exc).__name__}: {exc}"}
print("__DEPLOY_RESULT__=" + json.dumps(payload, ensure_ascii=False))
'''


def _relative_path(value: str, name: str) -> str:
    parts = value.split("/")
    if not value or "\x00" in value or any(part in {"", ".", ".."} for part in parts):
        raise ToolException(f"{name} 必须是非空相对路径，不能包含空段、. 或 ..")
    return value


class DeployInput(BaseModel):
    path: str = Field(
        description="""
        沙箱内的产物路径，它可以是文件夹，也可以是文件。
        这些路径表示要最终交付给用户的产物。
        比如以下都是有效的 path 值：
        /root/workspace/my-vue-app/dist
        /root/workspace/my-vue-app/assets/charts.png
        /root/workspace/my-vue-app/other.tar.gz
        如果指定的是目录，该工具会自动递归读取该目录下的所有文件进行部署
        """
    )
    target_dirname: str = Field(
        description="""
        部署的目标目录名称。
        该工具会把 path 参数指定的产物放到云存储的某个存储路径中。
        存储路径是： <前缀>/<target_dirname>/。
        前缀是什么你无需关心，你只需要指定 target_dirname 即可。
        建议该值直接取用 workspace 中的工程名字。
        如果 path 是目录，会先删除该目标目录中的全部旧文件，再上传新目录。
        如果 path 是单文件，只覆盖目标目录中的同名文件，保留其他文件。
        """
    )
    entry_files: list[str] = Field(
        min_length=1,
        description="""
        指定部署完成过后，要访问的入口文件名称。
        比如：["index.html"、"static/chart.png"]
        该工具会返回带签名的临时访问 URL，最终交付给用户访问（默认 1 小时有效）
        """,
    )


class DeployOutput(TypedDict):
    # 部署的总文件数量
    total_count: int
    # 成功部署的文件数量
    success_count: int
    # 失败的文件数量
    failure_count: int
    # 失败的原因，键为沙箱内文件的绝对路径，值是错误描述
    failures: dict[str, str]
    # 用户访问产物的入口地址集合
    urls: list[str]


@tool(args_schema=DeployInput)
async def deploy(
    path: str, target_dirname: str, entry_files: list[str]
) -> DeployOutput:
    """
    将沙箱内的文件或目录部署到云存储，返回部署统计、失败原因和入口访问地址。
    使用当前认证用户的沙箱，在沙箱内直接上传到 MinIO 的私有产物桶。
    工具会先撤销该桶上历史遗留的匿名公共读策略，再返回短期预签名访问地址。
    目录部署：先删除当前用户目标目录下的全部旧文件，再递归上传新目录，
    本次产物中不再包含的旧文件也会被删除。
    单文件部署：只覆盖目标目录下的同名文件，保留其他文件。
    目录替换不是原子操作；删除失败时停止部署，上传失败时不恢复已删除的旧文件。
    参数或产物路径错误会返回可修复的工具错误；认证、配置或服务错误会终止图运行。
    单文件上传失败仍计入 failure_count 和 failures。

    部署前注意：静态页面位于子目录中，JS、CSS、图片等资源不能默认以 / 开头，
    否则浏览器会从站点根目录请求，导致资源无法加载。构建前请配置相对资源路径
    （例如 Vite 的 base: "./"），或设置与实际部署子目录一致的访问前缀；
    同时检查页面中手写的资源引用。本工具只上传文件，不会改写产物中的资源路径。

    参数：
        path: 沙箱内产物的路径；目录会递归部署其中的所有文件。
        target_dirname: 云存储中的目标目录名称，存储路径为 <前缀>/<target_dirname>/。
        entry_files: 要交付给用户的入口文件路径，相对于部署后的目标目录。

    返回：
        DeployOutput 字典，包含以下字段：
        total_count: 本次部署的总文件数量。
        success_count: 成功部署的文件数量。
        failure_count: 部署失败的文件数量；为 0 表示所有文件部署成功。
        failures: 失败文件及原因，键为沙箱内文件的绝对路径，值为错误描述。
        urls: 上传成功的 entry_files 对应的预签名访问地址列表，有效期由 MINIO_PRESIGN_EXPIRE_SECONDS 控制。

    调用示例（目录部署，全部成功）：

    deploy(
        "/root/workspace/vue-app/dist",
        "vue-app",
        ["index.html", "static/chart.png"]
    )

    得到结果示例：
    {
        "total_count": 5,
        "success_count": 5,
        "failure_count": 0,
        "failures": {},
        "urls": [
            "<预签名 URL，默认 1 小时有效>",
            "<预签名 URL，默认 1 小时有效>"
        ]
    }

    调用示例（单文件部署，全部成功）：

    deploy(
        "/root/workspace/deep-research/dist/reporter.zip",
        "deep-research",
        ["reporter.zip"]
    )

    得到结果示例：

    {
        "total_count": 1,
        "success_count": 1,
        "failure_count": 0,
        "failures": {},
        "urls": [
            "<预签名 URL，默认 1 小时有效>"
        ]
    }
    """
    # 用户身份来自运行上下文，模型无法通过改工具参数修复，异常直接向图传播。
    user_id = get_user_id()
    if (
        not isinstance(user_id, str)
        or not user_id
        or "/" in user_id
        or "\x00" in user_id
        or user_id in {".", ".."}
    ):
        raise RuntimeError("当前认证用户 ID 不合法，不能部署产物")
    target_dirname = _relative_path(target_dirname, "target_dirname")
    if not entry_files:
        raise ToolException("entry_files 至少需要指定一个入口文件")
    entry_files = [_relative_path(entry, "entry_files") for entry in entry_files]
    source = PurePosixPath(path)
    if not source.is_absolute() or "\x00" in path or ".." in source.parts:
        raise ToolException("path 必须是沙箱内不含 .. 的绝对路径")
    if not minio_settings.endpoint_in_sandbox:
        raise RuntimeError(
            "请配置 MINIO_ENDPOINT_IN_SANDBOX，指定沙箱可直连的 MinIO 地址"
        )
    if not minio_settings.access_key or not minio_settings.secret_key:
        raise RuntimeError("请配置 MINIO_ACCESS_KEY 和 MINIO_SECRET_KEY")

    prefix = f"{user_id}/{target_dirname}"
    config = {
        "path": str(source),
        "prefix": prefix,
        "entry_files": entry_files,
        "endpoint": minio_settings.endpoint_in_sandbox,
        "access_key": minio_settings.access_key,
        "secret_key": minio_settings.secret_key,
        "secure": minio_settings.secure,
        "bucket_name": _BUCKET_NAME,
    }
    encoded = base64.b64encode(json.dumps(config).encode()).decode()
    script = f"CONFIG_DATA = {encoded!r}\n" + _UPLOAD_SCRIPT
    command = "cd /sandbox_utils && uv run --locked python -c " + shlex.quote(script)

    def redact(message: str) -> str:
        # 沙箱输出可能回显配置或密钥；返回模型/日志前统一打码。
        for secret in (encoded, minio_settings.access_key, minio_settings.secret_key):
            message = message.replace(secret, "[REDACTED]")
        return message

    backend = SandboxBackend(lambda: user_id)

    # 先把历史遗留的匿名公共读策略撤掉；失败时不继续上传，避免产物再次公开。
    minio = MinIO(
        endpoint=minio_settings.endpoint,
        access_key=minio_settings.access_key,
        secret_key=minio_settings.secret_key,
        secure=minio_settings.secure,
    )
    try:
        await minio.set_bucket_private(_BUCKET_NAME)
    except Exception as exc:
        raise RuntimeError(
            f"部署失败: 无法确认产物桶为私有，请检查 MinIO 凭据的桶策略权限: {redact(str(exc))}"
        ) from None

    try:
        execution = await backend.aexecute(command)
    except Exception as exc:
        raise RuntimeError(f"沙箱部署命令执行失败: {redact(str(exc))}") from None
    if execution.exit_code != 0 or execution.truncated:
        raise RuntimeError(
            f"沙箱部署命令未正常完成（退出码 {execution.exit_code}）: "
            + redact(execution.output)
        )
    # 后端输出可能同时包含 stderr；仅解析带标记的完整结果行。
    result_line = next(
        (
            line[len(_RESULT_MARKER) :]
            for line in reversed(execution.output.splitlines())
            if line.startswith(_RESULT_MARKER)
        ),
        None,
    )
    if result_line is None:
        raise RuntimeError("沙箱未返回部署结果，无法确认上传是否完成")
    try:
        payload = json.loads(result_line)
    except json.JSONDecodeError as exc:
        raise RuntimeError("沙箱返回的部署结果不是有效 JSON") from exc
    if "error" in payload:
        message = f"部署失败: {redact(payload['error'])}"
        if payload.get("error_kind") == "input":
            raise ToolException(message)
        raise RuntimeError(message)
    result = payload["result"]
    successful_entries = result.pop("successful_entries")
    result["failures"] = {
        filename: redact(reason) for filename, reason in result["failures"].items()
    }
    # 桶是私有的，入口地址必须使用短期预签名 URL，不能拼接永久公开链接。
    expires = timedelta(seconds=minio_settings.presign_expire_seconds)
    result["urls"] = [
        await minio.presigned_get_file_url(
            bucket_name=_BUCKET_NAME,
            filename=f"{prefix}/{entry}",
            expires=expires,
        )
        for entry in successful_entries
    ]
    return cast(DeployOutput, result)


# 工具错误（参数/产物路径问题）回给模型自行修复；RuntimeError 视为系统错误，终止当前图。
deploy.handle_tool_error = True
# Pydantic 的参数校验同样属于模型可修复错误，返回详细原因供模型调整参数。
deploy.handle_validation_error = lambda exc: str(exc)
