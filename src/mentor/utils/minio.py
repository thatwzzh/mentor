"""基于 aiobotocore 的 MinIO 异步客户端，其余功能仅依赖标准库。"""

from __future__ import annotations

import asyncio
import errno
import ipaddress
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import PurePosixPath
from typing import Any, BinaryIO
from urllib.parse import quote, urlsplit

from aiobotocore.config import AioConfig
from aiobotocore.session import get_session
from botocore.exceptions import ClientError

# 默认缓存时间为 90 天，单位为秒。
_DEFAULT_MAX_AGE = 7776000
_CHUNK_SIZE = 1024 * 1024


def _extension(value: str) -> str:
    if not isinstance(value, str) or len(value) < 2 or not value.startswith("."):
        raise ValueError("文件后缀必须以点开头且非空")
    return value.lower()


def _content_type(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or "\r" in value
        or "\n" in value
    ):
        raise ValueError("Content-Type 必须是非空且不含换行的字符串")
    return value


def _bucket(value: str) -> str:
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", value)
        or ".." in value
        or ".-" in value
        or "-." in value
    ):
        raise ValueError("桶名必须是 3 至 63 位的小写字母、数字、点或连字符")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    raise ValueError("桶名不能是 IP 地址")


def _key(value: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError("对象键或前缀必须是字符串")
    value = value.lstrip("/")
    if not allow_empty and not value:
        raise ValueError("对象键不能为空")
    return value


def _expires(value: timedelta) -> int:
    if not isinstance(value, timedelta):
        raise ValueError("expires 必须是 timedelta")
    seconds = value.total_seconds()
    if not seconds.is_integer() or not 1 <= seconds <= 604800:
        raise ValueError("expires 必须是 1 秒至 7 天之间的整数秒")
    return int(seconds)


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


@dataclass
class CacheStrategy:
    """按文件后缀配置上传对象的 Cache-Control。

    extensions: 后缀列表，例如 [".png", ".jpg"]；匹配时忽略大小写。
        空列表不匹配任何文件。后缀必须以点开头且非空。
    max_age: 缓存秒数，必须大于等于 0；0 表示 no-store，
        大于 0 表示 max-age=<秒数>。默认值为 90 天。
    """

    extensions: list[str]
    max_age: int = _DEFAULT_MAX_AGE

    def __post_init__(self) -> None:
        if not isinstance(self.extensions, list):
            raise ValueError("extensions 必须是后缀列表")
        for extension in self.extensions:
            _extension(extension)
        if type(self.max_age) is not int or self.max_age < 0:
            raise ValueError("max_age 必须是大于等于 0 的整数")


@dataclass
class ContentTypeStrategy:
    """配置上传对象的默认 Content-Type 和文件后缀映射。

    default_content_type: 未匹配后缀或没有后缀时使用的非空 Content-Type，
        默认为 application/octet-stream。
    extensions: 后缀到非空 Content-Type 的映射，例如
        {".html": "text/html; charset=utf-8", ".png": "image/png"}。
        后缀必须以点开头且非空；匹配时忽略大小写，
        忽略大小写后重复的后缀视为不合法配置。

    write_file、upload_file 和 upload_dir 均按目标对象键最后一段的最后一个后缀匹配，
    例如 archive.tar.gz 匹配 .gz；映射优先于默认值。
    不根据本地文件名、文件内容或系统 MIME 配置自动推断类型。
    """

    default_content_type: str = "application/octet-stream"
    extensions: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _content_type(self.default_content_type)
        if not isinstance(self.extensions, dict):
            raise ValueError("extensions 必须是后缀到 Content-Type 的字典")
        seen: set[str] = set()
        for extension, content_type in self.extensions.items():
            normalized = _extension(extension)
            if normalized in seen:
                raise ValueError(f"后缀忽略大小写后重复: {extension}")
            seen.add(normalized)
            _content_type(content_type)


@dataclass
class UploadDirResult:
    """目录上传完成后的汇总结果。

    total_count: 本次需要上传的普通文件总数，不包含跳过的文件或目录。
    success_count: 上传成功的文件数量。
    failure_count: 上传失败的文件数量。
    failures: 每个失败文件的原因；键为相对于 source_dir 的本地路径，
        分隔符统一为 /，不包含目标 prefix；值为包含异常类型和错误消息的字符串。

    total_count = success_count + failure_count；failure_count = len(failures)。
    无可上传文件时三个数量均为 0，failures 为 {}。
    """

    total_count: int
    success_count: int
    failure_count: int
    failures: dict[str, str]


class MinIO:
    """使用 aiobotocore 实现的 MinIO 异步操作接口。

    通用约定：
        - bucket_name 遵循服务端桶命名规则。
        - filename 是非空对象键，例如 "a/b/c/1.html"，不包含桶名。
          开头的 / 会被移除；不解析 .. 或合并中间的 /。
          去除开头的 / 后仍为空时，抛出 ValueError。
        - 除 create_bucket 外，方法不自动创建桶或修改访问权限。
        - 参数不合法抛出 ValueError；本地文件错误保留对应 OSError。
          服务端和网络错误原样向调用方传播，不转换为成功或空结果；
          upload_dir 的单文件错误例外，记录到汇总结果中。
        - 每次操作独立创建并关闭客户端，不持有跨事件循环的连接。
    """

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        secure: bool = True,
        cache_strategy: list[CacheStrategy] | None = None,
        content_type_strategy: ContentTypeStrategy | None = None,
    ) -> None:
        """保存并校验客户端配置；构造阶段不连接服务端。

        Args:
            endpoint: API 地址，例如 localhost:9002；不含协议、路径或查询参数。
            access_key: 非空访问密钥。
            secret_key: 非空秘密密钥。
            secure: True 使用 HTTPS，False 使用 HTTP。
            cache_strategy: None 或空列表表示所有上传文件均 no-store。
                未匹配的文件也使用 no-store；多个策略匹配时后者覆盖前者。
                策略仅影响此客户端后续上传的对象，不修改已存在对象。
            content_type_strategy: 上传时使用的 Content-Type 策略。
                None 等同于默认策略，即所有文件均使用 application/octet-stream。
                策略仅影响后续上传的对象，不修改已存在对象。

        Raises:
            ValueError: 地址、密钥、缓存策略或 Content-Type 策略不合法。
        """
        if (
            not isinstance(endpoint, str)
            or not endpoint
            or any(c.isspace() for c in endpoint)
        ):
            raise ValueError("endpoint 必须是不含协议的主机地址，可附带端口")
        try:
            parsed = urlsplit(f"//{endpoint}")
            port = parsed.port
            host = parsed.hostname
        except ValueError as exc:
            raise ValueError("endpoint 地址或端口不合法") from exc
        if (
            not host
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
            or "/" in endpoint
            or "?" in endpoint
            or "#" in endpoint
            or endpoint.endswith(":")
            or (port is not None and port == 0)
        ):
            raise ValueError("endpoint 不得包含协议、路径、凭据或查询参数")
        if ":" in host:
            try:
                ipaddress.IPv6Address(host)
            except ValueError as exc:
                raise ValueError("endpoint 的 IPv6 地址不合法") from exc
        elif not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", host):
            raise ValueError("endpoint 主机名不合法")
        for name, value in (("access_key", access_key), ("secret_key", secret_key)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} 不能为空")
        if type(secure) is not bool:
            raise ValueError("secure 必须是布尔值")
        if cache_strategy is not None and not isinstance(cache_strategy, list):
            raise ValueError("cache_strategy 必须是策略列表或 None")
        self._cache: dict[str, int] = {}
        for strategy in cache_strategy or []:
            if not isinstance(strategy, CacheStrategy):
                raise ValueError("cache_strategy 中的元素必须是 CacheStrategy")
            strategy.__post_init__()
            for extension in strategy.extensions:
                self._cache[_extension(extension)] = strategy.max_age
        if content_type_strategy is None:
            content_type_strategy = ContentTypeStrategy()
        if not isinstance(content_type_strategy, ContentTypeStrategy):
            raise ValueError("content_type_strategy 必须是 ContentTypeStrategy 或 None")
        content_type_strategy.__post_init__()
        self._default_content_type = content_type_strategy.default_content_type
        self._content_types = {
            _extension(key): value
            for key, value in content_type_strategy.extensions.items()
        }
        self._endpoint_url = f"{'https' if secure else 'http'}://{endpoint}"
        self._access_key = access_key
        self._secret_key = secret_key

    def _client(self) -> Any:
        # botocore 是 aiobotocore 自带的依赖，不引入额外的 SDK。
        return get_session().create_client(
            "s3",
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name="us-east-1",
            config=AioConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def _metadata(self, filename: str) -> dict[str, str]:
        extension = PurePosixPath(filename.rsplit("/", 1)[-1]).suffix.lower()
        max_age = self._cache.get(extension, 0)
        return {
            "CacheControl": f"max-age={max_age}" if max_age else "no-store",
            "ContentType": self._content_types.get(
                extension, self._default_content_type
            ),
        }

    @staticmethod
    async def _bucket_exists(client: Any, bucket_name: str) -> bool:
        try:
            await client.head_bucket(Bucket=bucket_name)
        except ClientError as exc:
            if _error_code(exc) in {"404", "NoSuchBucket", "NotFound"}:
                return False
            raise
        return True

    async def create_bucket(
        self, bucket_name: str, allow_public_read: bool = True
    ) -> None:
        """确保桶存在，并应用公共读约定。

        Args:
            bucket_name: 桶名；已存在时跳过建桶，仍应用公共读约定。
            allow_public_read: True 时允许任何人匿名读取桶内所有对象，
                不授予匿名写入、删除或列举权限，并保留无关的策略语句。
                False 时不新增公共读权限，也不撤销已有策略；不保证桶私有。

        建桶和设置策略不是原子操作；策略设置失败时桶可能已经创建，
        错误向调用方传播。重复调用不重复添加同一公共读策略。
        """
        bucket_name = _bucket(bucket_name)
        if type(allow_public_read) is not bool:
            raise ValueError("allow_public_read 必须是布尔值")
        async with self._client() as client:
            if not await self._bucket_exists(client, bucket_name):
                try:
                    await client.create_bucket(Bucket=bucket_name)
                except ClientError as exc:
                    # 另一调用可能已创建同一个桶，其他冲突仍向外传播。
                    if _error_code(exc) != "BucketAlreadyOwnedByYou":
                        raise
            if not allow_public_read:
                return
            try:
                response = await client.get_bucket_policy(Bucket=bucket_name)
                policy = json.loads(response["Policy"])
            except ClientError as exc:
                if _error_code(exc) != "NoSuchBucketPolicy":
                    raise
                policy = {"Version": "2012-10-17", "Statement": []}
            statements = policy.setdefault("Statement", [])
            if isinstance(statements, dict):
                statements = [statements]
                policy["Statement"] = statements
            resource = f"arn:aws:s3:::{bucket_name}/*"
            for statement in statements:
                if (
                    statement.get("Effect") == "Allow"
                    and statement.get("Principal")
                    in ("*", {"AWS": "*"}, {"AWS": ["*"]})
                    and statement.get("Action") in ("s3:GetObject", ["s3:GetObject"])
                    and statement.get("Resource") in (resource, [resource])
                    and not statement.get("Condition")
                ):
                    return
            statements.append(
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": ["*"]},
                    "Action": ["s3:GetObject"],
                    "Resource": [resource],
                }
            )
            await client.put_bucket_policy(
                Bucket=bucket_name, Policy=json.dumps(policy)
            )

    async def bucket_exists(self, bucket_name: str) -> bool:
        """桶不存在时返回 False，存在时返回 True；权限和网络错误仍抛出。"""
        bucket_name = _bucket(bucket_name)
        async with self._client() as client:
            return await self._bucket_exists(client, bucket_name)

    async def remove_bucket(self, bucket_name: str, force: bool = False) -> None:
        """删除桶；桶不存在时忽略。

        Args:
            bucket_name: 要删除的桶名。
            force: False 时仅删除空桶，非空时抛出服务端错误。
                True 时先删除桶内全部对象、历史版本和删除标记，再删除桶，
                无论桶是否为空。已删除的数据不可恢复。

        强制删除不是原子操作，失败时可能已经删除部分数据，不回滚。
        权限不足、对象保留限制或并发写入等导致删除失败时，向调用方抛出错误；
        force 不绕过服务端权限或保留规则，也不将失败视为成功。
        """
        bucket_name = _bucket(bucket_name)
        if type(force) is not bool:
            raise ValueError("force 必须是布尔值")
        async with self._client() as client:
            try:
                if force:
                    # 未启用版本控制的对象也会以 null 版本出现在版本列表中。
                    paginator = client.get_paginator("list_object_versions")
                    async for page in paginator.paginate(Bucket=bucket_name):
                        objects = [
                            {"Key": item["Key"], "VersionId": item["VersionId"]}
                            for collection in ("Versions", "DeleteMarkers")
                            for item in page.get(collection, [])
                        ]
                        for start in range(0, len(objects), 1000):
                            response = await client.delete_objects(
                                Bucket=bucket_name,
                                Delete={
                                    "Objects": objects[start : start + 1000],
                                    "Quiet": True,
                                },
                            )
                            errors = response.get("Errors", [])
                            if errors:
                                # 批量删除即使 HTTP 成功，仍可能存在逐对象错误。
                                raise ClientError(
                                    {
                                        "Error": {
                                            "Code": errors[0].get(
                                                "Code", "DeleteFailed"
                                            ),
                                            "Message": json.dumps(
                                                errors, ensure_ascii=False
                                            ),
                                        },
                                        "ResponseMetadata": response.get(
                                            "ResponseMetadata", {}
                                        ),
                                    },
                                    "DeleteObjects",
                                )
                await client.delete_bucket(Bucket=bucket_name)
            except ClientError as exc:
                if _error_code(exc) != "NoSuchBucket":
                    raise

    async def write_file(
        self,
        *,
        bucket_name: str,
        filename: str,
        stream: BinaryIO,
    ) -> None:
        """从二进制流当前位置读取到结尾，上传为指定对象。

        同名对象覆盖当前内容；开启版本控制时由服务端生成新版本。
        应用匹配的缓存策略。空流允许上传为零字节对象。
        Content-Type 由构造器中的 content_type_strategy 按 filename 决定。
        调用方负责关闭流；不回到流起点、不关闭流，失败后不保证流位置。
        文本应先编码为字节。
        """
        bucket_name, filename = _bucket(bucket_name), _key(filename)
        if not callable(getattr(stream, "read", None)):
            raise ValueError("stream 必须是可读取的二进制流")
        # 暂存为可重试的请求体，同时支持不可 seek 的流和非零起始位置。
        # 大于 8 MiB 时自动落到系统临时目录；从不关闭调用方的 stream。
        with tempfile.SpooledTemporaryFile(
            max_size=8 * _CHUNK_SIZE, mode="w+b"
        ) as body:
            while True:
                chunk = stream.read(_CHUNK_SIZE)
                if not isinstance(chunk, bytes):
                    raise ValueError("stream.read() 必须返回 bytes")
                if not chunk:
                    break
                body.write(chunk)
            body.seek(0)
            async with self._client() as client:
                await client.put_object(
                    Bucket=bucket_name,
                    Key=filename,
                    Body=body,
                    **self._metadata(filename),
                )

    async def upload_file(
        self,
        *,
        bucket_name: str,
        filename: str,
        source_file: str,
    ) -> None:
        """上传单个本地文件；覆盖和缓存约定与 write_file 相同。

        source_file 为本地路径，必须指向可读的普通文件。
        Content-Type 由构造器中的 content_type_strategy 按目标 filename 决定。
        本方法负责打开并关闭本地文件，不修改本地内容。
        """
        bucket_name, filename = _bucket(bucket_name), _key(filename)
        source_file = self._local_path(source_file)
        mode = os.stat(source_file).st_mode
        if stat.S_ISDIR(mode):
            raise IsADirectoryError(
                errno.EISDIR, os.strerror(errno.EISDIR), source_file
            )
        if not stat.S_ISREG(mode):
            raise OSError(errno.EINVAL, "source_file 必须是普通文件", source_file)
        with open(source_file, "rb") as body:
            async with self._client() as client:
                await client.put_object(
                    Bucket=bucket_name,
                    Key=filename,
                    Body=body,
                    **self._metadata(filename),
                )

    @staticmethod
    def _local_path(value: str) -> str:
        try:
            path = os.fspath(value)
        except TypeError as exc:
            raise ValueError("本地路径必须是非空字符串或 PathLike") from exc
        if not isinstance(path, str) or not path or "\x00" in path:
            raise ValueError("本地路径必须是非空且不含空字符的字符串")
        return path

    @staticmethod
    def _scan_directory(source_dir: str) -> list[tuple[str, str]]:
        mode = os.stat(source_dir).st_mode
        if not stat.S_ISDIR(mode):
            raise NotADirectoryError(
                errno.ENOTDIR, os.strerror(errno.ENOTDIR), source_dir
            )
        files: list[tuple[str, str]] = []
        directories = [(source_dir, "")]
        while directories:
            directory, relative = directories.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.is_symlink():
                        continue
                    name = f"{relative}/{entry.name}" if relative else entry.name
                    mode = entry.stat(follow_symlinks=False).st_mode
                    if stat.S_ISDIR(mode):
                        directories.append((entry.path, name))
                    elif stat.S_ISREG(mode):
                        files.append((name, entry.path))
        return sorted(files)

    async def upload_dir(
        self,
        *,
        bucket_name: str,
        source_dir: str,
        prefix: str = "",
        max_concurrency: int = 10,
    ) -> UploadDirResult:
        """递归上传本地目录中的普通文件，保留相对目录结构。

        Args:
            bucket_name: 已存在的目标桶名。
            source_dir: 本地目录路径；不存在时抛出 FileNotFoundError，
                不是目录时抛出 NotADirectoryError。
            prefix: 目标对象键前缀，默认为空；移除首尾的 / 后，
                非空时通过一个 / 与文件相对路径连接。
            max_concurrency: 同时进行的文件上传数量上限，默认为 10，
                必须为正整数，否则抛出 ValueError。

        例如 source_dir 下的 images/logo.png，在 prefix="assets" 时
        上传为 assets/images/logo.png；不包含 source_dir 自身的目录名。
        相对路径的分隔符统一为 /。包含隐藏文件，跳过空目录和符号链接；
        其他非普通文件也跳过，无可上传文件时返回空汇总结果。
        每个文件的覆盖、缓存和 Content-Type 约定与 upload_file 相同，
        均按最终对象键匹配策略。不删除桶内未包含在本地目录中的对象。
        不修改本地内容；负责关闭本次上传打开的文件。
        上传前完成目录扫描，确定本次需要上传的文件集合。
        参数校验或目录扫描失败时直接抛出异常，不开始上传。
        并发上传时，单个文件的读取、服务端或网络错误均记录到 failures，
        不影响其他文件继续上传；等待全部文件处理完成后返回汇总结果。
        例如 images/logo.png 上传失败时，failures 的键为 "images/logo.png"，
        即使 prefix="assets"，也不包含 "assets/"。
        调用方取消任务时仍传播取消，不作为单文件失败处理。
        已完成的上传不回滚，整个目录上传不是原子操作。

        Returns:
            UploadDirResult：总数量、成功数量、失败数量及逐文件失败原因。
        """
        bucket_name = _bucket(bucket_name)
        source_dir = self._local_path(source_dir)
        prefix = _key(prefix, allow_empty=True).rstrip("/")
        if type(max_concurrency) is not int or max_concurrency <= 0:
            raise ValueError("max_concurrency 必须是正整数")
        files = await asyncio.to_thread(self._scan_directory, source_dir)
        failures: dict[str, str] = {}
        pending = iter(files)

        async def worker() -> None:
            for relative, local in pending:
                filename = f"{prefix}/{relative}" if prefix else relative
                try:
                    await self.upload_file(
                        bucket_name=bucket_name,
                        filename=filename,
                        source_file=local,
                    )
                except Exception as exc:
                    failures[relative] = f"{type(exc).__name__}: {exc}"

        # 固定数量的工作任务，避免为大目录中的每个文件创建一个任务。
        async with asyncio.TaskGroup() as group:
            for _ in range(min(max_concurrency, len(files))):
                group.create_task(worker())
        return UploadDirResult(
            total_count=len(files),
            success_count=len(files) - len(failures),
            failure_count=len(failures),
            failures=dict(sorted(failures.items())),
        )

    async def read_file(self, *, bucket_name: str, filename: str) -> bytes:
        """读取对象的全部内容为 bytes；空对象返回 b""。

        对象不存在时抛出服务端错误。返回前释放响应连接；
        全量读取会占用与对象大小相应的内存。
        """
        bucket_name, filename = _bucket(bucket_name), _key(filename)
        async with self._client() as client:
            response = await client.get_object(Bucket=bucket_name, Key=filename)
            async with response["Body"] as body:
                return await body.read()

    async def download_file(
        self,
        *,
        bucket_name: str,
        filename: str,
        target_file: str,
        overwrite: bool = False,
    ) -> None:
        """下载对象到本地 target_file，自动创建缺失的父目录。

        目标已存在且 overwrite=False 时抛出 FileExistsError；
        overwrite=True 时仅在下载完整成功后替换目标文件。
        失败时保留原目标文件，清理本次下载的临时文件并抛出异常。
        目标为目录时抛出 IsADirectoryError。
        """
        bucket_name, filename = _bucket(bucket_name), _key(filename)
        target_file = self._local_path(target_file)
        if type(overwrite) is not bool:
            raise ValueError("overwrite 必须是布尔值")
        if os.path.isdir(target_file):
            raise IsADirectoryError(
                errno.EISDIR, os.strerror(errno.EISDIR), target_file
            )
        if not overwrite and os.path.lexists(target_file):
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), target_file)
        target_file = os.path.abspath(target_file)
        parent = os.path.dirname(target_file)
        os.makedirs(parent, exist_ok=True)
        temporary: str | None = None
        try:
            # 临时文件和目标在同一目录，完整下载后原子发布。
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=parent, delete=False
            ) as output:
                temporary = output.name
                async with self._client() as client:
                    response = await client.get_object(Bucket=bucket_name, Key=filename)
                    async with response["Body"] as body:
                        while chunk := await body.read(_CHUNK_SIZE):
                            output.write(chunk)
            if overwrite:
                os.replace(temporary, target_file)
                temporary = None
            else:
                # link 的独占创建语义同时处理下载期间目标被其他进程创建的情况。
                os.link(temporary, target_file)
        finally:
            if temporary is not None:
                os.unlink(temporary)

    async def file_exists(self, *, bucket_name: str, filename: str) -> bool:
        """对象不存在时返回 False；桶不存在、权限或网络错误仍抛出。"""
        bucket_name, filename = _bucket(bucket_name), _key(filename)
        async with self._client() as client:
            try:
                await client.head_object(Bucket=bucket_name, Key=filename)
            except ClientError as exc:
                code = _error_code(exc)
                if code == "NoSuchKey":
                    return False
                if code in {"404", "NotFound"}:
                    # HEAD 的通用 404 无法区分桶不存在与对象不存在。
                    await client.head_bucket(Bucket=bucket_name)
                    return False
                raise
            return True

    async def list_files(self, bucket_name: str, prefix: str = "") -> list[str]:
        """返回匹配前缀的全部对象键，按对象键升序排列。

        prefix 允许为空，开头的 / 会被移除，采用字符串前缀匹配。
        包含所有分页及嵌套前缀，返回键不加开头的 /，无匹配返回 []。
        仅列举当前可见对象，不列举历史版本或删除标记。
        桶不存在时抛出服务端错误。
        """
        bucket_name = _bucket(bucket_name)
        prefix = _key(prefix, allow_empty=True)
        async with self._client() as client:
            paginator = client.get_paginator("list_objects_v2")
            files: list[str] = []
            async for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
                files.extend(item["Key"] for item in page.get("Contents", []))
            return sorted(files)

    async def remove_file(self, *, bucket_name: str, filename: str) -> None:
        """删除指定对象；对象不存在时忽略，桶不存在时抛出服务端错误。

        开启版本控制时遵循服务端默认删除语义，不清除历史版本。
        """
        bucket_name, filename = _bucket(bucket_name), _key(filename)
        async with self._client() as client:
            try:
                await client.delete_object(Bucket=bucket_name, Key=filename)
            except ClientError as exc:
                if _error_code(exc) != "NoSuchKey":
                    raise

    def get_file_url(self, *, bucket_name: str, filename: str) -> str:
        """返回未签名的永久对象 URL，对对象键进行 URL 编码并保留 /。

        根据 secure 选择协议，不发起网络请求、不检查对象是否存在。
        URL 不授予权限；匿名访问需要桶策略允许公共读。
        """
        bucket_name, filename = _bucket(bucket_name), _key(filename)
        return f"{self._endpoint_url}/{quote(bucket_name, safe='')}/{quote(filename, safe='/')}"

    async def presigned_get_file_url(
        self,
        *,
        bucket_name: str,
        filename: str,
        expires: timedelta = timedelta(hours=1),
    ) -> str:
        """返回临时 GET 预签名 URL，不检查对象是否存在。

        expires 必须是 1 秒至 7 天之间的整数秒，否则抛出 ValueError。
        持有 URL 的人可在签名及签名所用凭据有效期间读取对象，
        实际有效期受凭据有效期和服务端权限约束；不修改桶策略。
        """
        return await self._presigned_url("get_object", bucket_name, filename, expires)

    async def presigned_put_file_url(
        self,
        *,
        bucket_name: str,
        filename: str,
        expires: timedelta = timedelta(hours=1),
    ) -> str:
        """返回临时 PUT 预签名 URL，用于直接上传指定对象。

        expires 必须是 1 秒至 7 天之间的整数秒，否则抛出 ValueError。
        不检查桶或对象是否存在，不创建桶、不上传内容、不修改桶策略。
        持有 URL 的人可在签名及签名所用凭据有效期间上传该对象，
        实际有效期受凭据有效期和服务端写入权限约束。
        调用方使用 HTTP PUT，将文件的二进制内容作为请求体，
        不使用 multipart/form-data；同名对象覆盖和版本约定与 write_file 相同。
        URL 在有效期内可重复使用，不是一次性上传凭证。
        不签入 Content-Type 或 Cache-Control，也不自动应用构造器中的策略；
        需要这些元数据时，由调用方在 PUT 请求中设置对应请求头。
        """
        return await self._presigned_url("put_object", bucket_name, filename, expires)

    async def _presigned_url(
        self,
        operation: str,
        bucket_name: str,
        filename: str,
        expires: timedelta,
    ) -> str:
        bucket_name, filename = _bucket(bucket_name), _key(filename)
        seconds = _expires(expires)
        async with self._client() as client:
            return await client.generate_presigned_url(
                operation,
                Params={"Bucket": bucket_name, "Key": filename},
                ExpiresIn=seconds,
            )
