"""真实 MinIO 测试环境；不启动服务，不操作任何已有业务桶。"""

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx
import pytest
from aiobotocore.config import AioConfig
from aiobotocore.session import get_session

from mentor.config.minio_settings import minio_settings
from mentor.utils.minio import MinIO
from mentor.config.sandbox_settings import sandbox_settings


@dataclass
class LiveMinio:
    config: dict
    raw: Any
    http: httpx.AsyncClient
    buckets: list[str] = field(default_factory=list)

    def client(self, **overrides):
        return MinIO(**(self.config | overrides))

    def new_bucket(self):
        # 先登记再创建，即使模块在创建中途报错，仍能独立清理。
        name = f"minio-it-{uuid4().hex}"
        self.buckets.append(name)
        return name

    async def private_bucket(self):
        name = self.new_bucket()
        await self.raw.create_bucket(Bucket=name)
        return name

    async def put(self, bucket, key, content=b"fixture"):
        await self.raw.put_object(Bucket=bucket, Key=key, Body=content)

    async def get(self, bucket, key):
        response = await self.raw.get_object(Bucket=bucket, Key=key)
        async with response["Body"] as body:
            return await body.read()

    async def keys(self, bucket):
        keys = []
        async for page in self.raw.get_paginator("list_objects_v2").paginate(
            Bucket=bucket
        ):
            keys.extend(item["Key"] for item in page.get("Contents", []))
        return sorted(keys)

    async def cleanup(self):
        # 不依赖被测 remove_bucket；只清理本次登记的随机桶及其全部版本。
        from botocore.exceptions import ClientError

        errors = []
        for bucket in self.buckets:
            try:
                objects = []
                async for page in self.raw.get_paginator(
                    "list_object_versions"
                ).paginate(Bucket=bucket):
                    for item in page.get("Versions", []) + page.get(
                        "DeleteMarkers", []
                    ):
                        objects.append(
                            {"Key": item["Key"], "VersionId": item["VersionId"]}
                        )
                for start in range(0, len(objects), 1000):
                    result = await self.raw.delete_objects(
                        Bucket=bucket, Delete={"Objects": objects[start : start + 1000]}
                    )
                    if result.get("Errors"):
                        raise RuntimeError(
                            f"清理测试桶 {bucket} 失败: {result['Errors']}"
                        )
                await self.raw.delete_bucket(Bucket=bucket)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") != "NoSuchBucket":
                    errors.append(exc)
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise ExceptionGroup("测试桶清理失败，请按错误中的桶名手动检查", errors)


@dataclass
class MinioEnvironment:
    config: dict

    @asynccontextmanager
    async def connect(self):
        endpoint_url = (
            "https://" if self.config["secure"] else "http://"
        ) + self.config["endpoint"]
        async with (
            get_session().create_client(
                "s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=self.config["access_key"],
                aws_secret_access_key=self.config["secret_key"],
                region_name="us-east-1",
                config=AioConfig(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                    connect_timeout=5,
                    read_timeout=15,
                    retries={"max_attempts": 0},
                ),
            ) as raw,
            httpx.AsyncClient(timeout=15, trust_env=False) as http,
        ):
            env = LiveMinio(self.config, raw, http)
            try:
                yield env
            finally:
                await env.cleanup()


@pytest.fixture
def minio_env():
    if os.environ.get("MINIO_INTEGRATION") != "1":
        pytest.skip("真实 MinIO 集成测试需显式启用：make test-integrations")
    if not minio_settings.access_key or not minio_settings.secret_key:
        pytest.fail("请在 .env 或环境变量中设置 MINIO_ACCESS_KEY / MINIO_SECRET_KEY")
    return MinioEnvironment(
        dict(
            endpoint=minio_settings.endpoint,
            access_key=minio_settings.access_key,
            secret_key=minio_settings.secret_key,
            secure=minio_settings.secure,
        )
    )


@pytest.fixture
def deploy_env(request):
    if os.environ.get("DEPLOY_INTEGRATION") != "1":
        pytest.skip(
            "Deploy 真实沙箱集成测试需设置 DEPLOY_INTEGRATION=1 和 MINIO_INTEGRATION=1"
        )
    if not sandbox_settings.domain or not sandbox_settings.api_key:
        pytest.fail("请配置 OPEN_SANDBOX_DOMAIN / OPEN_SANDBOX_API_KEY")
    if not minio_settings.endpoint_in_sandbox:
        pytest.fail("请配置沙箱可达的 MINIO_ENDPOINT_IN_SANDBOX")
    if not os.environ.get("DEPLOY_SANDBOX_IMAGE"):
        pytest.fail(
            "请用 docker/sandbox/Dockerfile 构建镜像并配置 DEPLOY_SANDBOX_IMAGE"
        )
    return request.getfixturevalue("minio_env")
