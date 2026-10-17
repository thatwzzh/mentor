"""按公开接口契约验收；全部通过真实 S3/HTTP 或真实本地文件核验，无 Mock。"""

import asyncio
import json
import os
from datetime import timedelta
from functools import wraps
from io import BytesIO
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from botocore.exceptions import ClientError

from mentor.utils.minio import CacheStrategy, ContentTypeStrategy, UploadDirResult


def live_test(test):
    """每个场景的被测客户端和独立 SDK 共用一个事件循环，无需 pytest-asyncio。"""

    @wraps(test)
    def run(*args, **kwargs):
        async def scenario():
            async with kwargs["minio_env"].connect() as live:
                await test(*args, **(kwargs | {"minio_env": live}))

        asyncio.run(scenario())

    return run


def assert_counts(result, total, success, failed):
    assert isinstance(result, UploadDirResult)
    assert (result.total_count, result.success_count, result.failure_count) == (
        total,
        success,
        failed,
    )
    assert result.total_count == result.success_count + result.failure_count
    assert result.failure_count == len(result.failures)


@live_test
async def test_bucket_lifecycle_and_idempotency(minio_env):
    client = minio_env.client()
    bucket = minio_env.new_bucket()
    assert await client.bucket_exists(bucket) is False
    await client.remove_bucket(bucket)
    await client.create_bucket(bucket, allow_public_read=False)
    assert await client.bucket_exists(bucket) is True
    assert await client.list_files(bucket) == []
    await minio_env.put(bucket, "keep.txt", b"keep")
    await client.create_bucket(bucket, allow_public_read=False)
    assert await minio_env.get(bucket, "keep.txt") == b"keep"
    with pytest.raises(ClientError) as error:
        await client.remove_bucket(bucket)
    assert error.value.response.get("Error", {}).get("Code") == "BucketNotEmpty"
    await client.remove_file(bucket_name=bucket, filename="keep.txt")
    await client.remove_bucket(bucket)
    await client.remove_bucket(bucket, force=True)
    assert await client.bucket_exists(bucket) is False


@live_test
async def test_public_read_only_and_policy_idempotency(minio_env):
    client = minio_env.client()
    bucket = minio_env.new_bucket()
    await client.create_bucket(bucket)  # 默认允许公共读。
    await minio_env.put(bucket, "public.txt", b"public")
    url = client.get_file_url(bucket_name=bucket, filename="public.txt")
    assert (await minio_env.http.get(url)).content == b"public"
    base = url.rsplit("/", 1)[0]
    for response in [
        await minio_env.http.put(url, content=b"anonymous overwrite"),
        await minio_env.http.delete(url),
        await minio_env.http.get(base, params={"list-type": "2"}),
    ]:
        assert response.status_code == 403
    assert await minio_env.get(bucket, "public.txt") == b"public"
    before = json.loads(
        (await minio_env.raw.get_bucket_policy(Bucket=bucket))["Policy"]
    )
    await client.create_bucket(bucket)
    after = json.loads((await minio_env.raw.get_bucket_policy(Bucket=bucket))["Policy"])
    assert after == before
    await client.create_bucket(bucket, allow_public_read=False)
    assert (
        json.loads((await minio_env.raw.get_bucket_policy(Bucket=bucket))["Policy"])
        == before
    )
    assert (await minio_env.http.get(url)).status_code == 200


@live_test
async def test_preserve_unrelated_bucket_policy(minio_env):
    bucket = await minio_env.private_bucket()
    unrelated = {
        "Sid": "KeepLocationPermission",
        "Effect": "Allow",
        "Principal": {"AWS": ["*"]},
        "Action": ["s3:GetBucketLocation"],
        "Resource": [f"arn:aws:s3:::{bucket}"],
    }
    await minio_env.raw.put_bucket_policy(
        Bucket=bucket,
        Policy=json.dumps({"Version": "2012-10-17", "Statement": [unrelated]}),
    )
    before = json.loads(
        (await minio_env.raw.get_bucket_policy(Bucket=bucket))["Policy"]
    )
    await minio_env.client().create_bucket(bucket)
    after = json.loads((await minio_env.raw.get_bucket_policy(Bucket=bucket))["Policy"])
    assert all(statement in after["Statement"] for statement in before["Statement"])
    await minio_env.put(bucket, "new", b"readable")
    url = minio_env.client().get_file_url(bucket_name=bucket, filename="new")
    response = await minio_env.http.get(url)
    assert response.status_code == 200
    assert response.content == b"readable"


@pytest.mark.parametrize("content", [b"", b"\x00\xffbinary\r\n", "中文内容".encode()])
@live_test
async def test_write_read_overwrite_and_delete(minio_env, content):
    bucket = await minio_env.private_bucket()
    client = minio_env.client()
    key = "nested/data.bin"
    stream = BytesIO(b"skip" + content)
    stream.seek(4)
    await client.write_file(bucket_name=bucket, filename=key, stream=stream)
    assert not stream.closed
    assert await minio_env.get(bucket, key) == content
    assert await client.read_file(bucket_name=bucket, filename=key) == content
    assert await client.file_exists(bucket_name=bucket, filename=key) is True
    await client.write_file(
        bucket_name=bucket, filename=key, stream=BytesIO(b"replacement")
    )
    assert await minio_env.get(bucket, key) == b"replacement"
    await client.remove_file(bucket_name=bucket, filename=key)
    await client.remove_file(bucket_name=bucket, filename=key)
    assert await client.file_exists(bucket_name=bucket, filename=key) is False
    assert await minio_env.keys(bucket) == []
    with pytest.raises(ClientError) as error:
        await client.read_file(bucket_name=bucket, filename=key)
    assert error.value.response.get("Error", {}).get("Code") == "NoSuchKey"


@live_test
async def test_upload_file_and_large_binary_download(minio_env, tmp_path):
    bucket = await minio_env.private_bucket()
    client = minio_env.client()
    content = bytes(range(256)) * (5 * 1024 * 1024 // 256 + 1)
    source = tmp_path / "source.bin"
    source.write_bytes(content)
    await client.upload_file(
        bucket_name=bucket, filename="large.bin", source_file=str(source)
    )
    assert await minio_env.get(bucket, "large.bin") == content
    target = tmp_path / "new" / "nested" / "download.bin"
    await client.download_file(
        bucket_name=bucket, filename="large.bin", target_file=str(target)
    )
    assert target.read_bytes() == content
    assert source.read_bytes() == content
    source.unlink()


@live_test
async def test_download_overwrite_errors_and_failure_cleanup(minio_env, tmp_path):
    bucket = await minio_env.private_bucket()
    client = minio_env.client()
    await minio_env.put(bucket, "data", b"new")
    target = tmp_path / "target"
    target.write_bytes(b"old")
    with pytest.raises(FileExistsError):
        await client.download_file(
            bucket_name=bucket, filename="data", target_file=str(target)
        )
    assert target.read_bytes() == b"old"
    with pytest.raises(ClientError):
        await client.download_file(
            bucket_name=bucket,
            filename="missing",
            target_file=str(target),
            overwrite=True,
        )
    assert target.read_bytes() == b"old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["target"]
    missing_target = tmp_path / "missing-target"
    with pytest.raises(ClientError):
        await client.download_file(
            bucket_name=bucket, filename="missing", target_file=str(missing_target)
        )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["target"]
    with pytest.raises(IsADirectoryError):
        await client.download_file(
            bucket_name=bucket,
            filename="data",
            target_file=str(tmp_path),
            overwrite=True,
        )
    await client.download_file(
        bucket_name=bucket, filename="data", target_file=str(target), overwrite=True
    )
    assert target.read_bytes() == b"new"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["target"]
    await minio_env.put(bucket, "empty", b"")
    await client.download_file(
        bucket_name=bucket, filename="empty", target_file=str(target), overwrite=True
    )
    assert target.read_bytes() == b""


@live_test
async def test_object_key_normalization_and_url_encoding(minio_env):
    bucket = await minio_env.private_bucket()
    client = minio_env.client()
    key = "中文 空格/+?#%/nested/data.bin"
    await client.write_file(
        bucket_name=bucket, filename="///" + key, stream=BytesIO(b"key")
    )
    assert await minio_env.keys(bucket) == [key]
    assert await client.read_file(bucket_name=bucket, filename="/" + key) == b"key"
    assert await client.file_exists(bucket_name=bucket, filename="/" + key) is True
    assert await client.list_files(bucket, prefix="/中文 空格/") == [key]
    # URL 必须保留键中的 .. 和重复斜线；服务端是否接受该键由服务端决定。
    literal = client.get_file_url(bucket_name=bucket, filename="/a/../b//c")
    assert urlsplit(literal).path == f"/{bucket}/a/../b//c"
    url_key = "中文 空格/+?#%/data.bin"
    await minio_env.put(bucket, url_key, b"encoded")
    url = client.get_file_url(bucket_name=bucket, filename="///" + url_key)
    parsed = urlsplit(url)
    assert parsed.scheme == ("https" if minio_env.config["secure"] else "http")
    assert parsed.netloc == minio_env.config["endpoint"]
    assert (
        parsed.path
        == f"/{bucket}/%E4%B8%AD%E6%96%87%20%E7%A9%BA%E6%A0%BC/%2B%3F%23%25/data.bin"
    )
    assert parsed.query == parsed.fragment == ""
    signed = await client.presigned_get_file_url(
        bucket_name=bucket, filename="/" + url_key
    )
    assert (await minio_env.http.get(signed)).content == b"encoded"
    await client.remove_file(bucket_name=bucket, filename="///" + key)
    assert await minio_env.keys(bucket) == [url_key]


@pytest.mark.parametrize(
    "method",
    [
        "write_file",
        "read_file",
        "file_exists",
        "remove_file",
        "upload_file",
        "download_file",
        "list_files",
    ],
)
@live_test
async def test_missing_bucket_errors_do_not_create_bucket(minio_env, tmp_path, method):
    bucket = minio_env.new_bucket()
    source = tmp_path / "source"
    source.write_bytes(b"data")
    kwargs: dict[str, Any] = dict(bucket_name=bucket, filename="data")
    if method == "write_file":
        kwargs["stream"] = BytesIO(b"data")
    elif method == "upload_file":
        kwargs["source_file"] = str(source)
    elif method == "download_file":
        kwargs["target_file"] = str(tmp_path / "target")
    elif method == "list_files":
        kwargs.pop("filename")
    with pytest.raises(ClientError):
        await getattr(minio_env.client(), method)(**kwargs)
    if method == "write_file":
        assert not kwargs["stream"].closed
    assert await minio_env.client().bucket_exists(bucket) is False


@pytest.mark.parametrize(
    "method",
    [
        "bucket_exists",
        "file_exists",
        "list_files",
        "read_file",
        "write_file",
        "remove_file",
        "remove_bucket",
        "create_bucket",
    ],
)
@live_test
async def test_invalid_credentials_propagate_service_errors(minio_env, method):
    bucket = await minio_env.private_bucket()
    await minio_env.put(bucket, "data")
    client = minio_env.client(
        access_key="invalid-" + bucket, secret_key="invalid-secret"
    )
    kwargs = dict(bucket_name=bucket)
    if method in {"file_exists", "read_file", "write_file", "remove_file"}:
        kwargs["filename"] = "data"
    if method == "write_file":
        kwargs["stream"] = BytesIO(b"bad")
    with pytest.raises(ClientError) as error:
        await getattr(client, method)(**kwargs)
    assert error.value.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 403
    assert await minio_env.get(bucket, "data") == b"fixture"


@pytest.mark.parametrize("prefix", ["", "/", "assets", "/assets///"])
@live_test
async def test_upload_dir_structure_hidden_files_links_and_overwrite(
    minio_env, tmp_path, prefix
):
    bucket = await minio_env.private_bucket()
    source = tmp_path / "tree"
    (source / "images").mkdir(parents=True)
    (source / "empty").mkdir()
    files = {
        "hello.txt": b"hello",
        "images/图片.PNG": b"image",
        ".hidden": b"hidden",
        "zero": b"",
    }
    for key, content in files.items():
        (source / key).write_bytes(content)
    (source / "file-link").symlink_to(source / "hello.txt")
    (source / "dir-link").symlink_to(source / "images", target_is_directory=True)
    (source / "broken-link").symlink_to(source / "absent")
    (source / "cycle").symlink_to(source, target_is_directory=True)
    os.mkfifo(source / "pipe")
    normalized = prefix.strip("/")
    base = normalized + "/" if normalized else ""
    await minio_env.put(bucket, base + "hello.txt", b"old")
    await minio_env.put(bucket, "unrelated", b"keep")
    result = await minio_env.client().upload_dir(
        bucket_name=bucket, source_dir=str(source), prefix=prefix, max_concurrency=2
    )
    assert_counts(result, 4, 4, 0)
    assert result.failures == {}
    assert await minio_env.keys(bucket) == sorted(
        [base + key for key in files] + ["unrelated"]
    )
    for key, content in files.items():
        assert await minio_env.get(bucket, base + key) == content
        assert (source / key).read_bytes() == content
    assert await minio_env.get(bucket, "unrelated") == b"keep"


@live_test
async def test_upload_dir_empty_and_local_path_errors(minio_env, tmp_path):
    bucket = await minio_env.private_bucket()
    client = minio_env.client()
    (tmp_path / "empty").mkdir()
    result = await client.upload_dir(bucket_name=bucket, source_dir=str(tmp_path))
    assert_counts(result, 0, 0, 0)
    assert result.failures == {}
    path = tmp_path / "file"
    path.write_bytes(b"data")
    with pytest.raises(NotADirectoryError):
        await client.upload_dir(bucket_name=bucket, source_dir=str(path))
    with pytest.raises(FileNotFoundError):
        await client.upload_dir(
            bucket_name=bucket, source_dir=str(tmp_path / "missing")
        )
    with pytest.raises(FileNotFoundError):
        await client.upload_file(
            bucket_name=bucket, filename="data", source_file=str(tmp_path / "missing")
        )
    with pytest.raises(OSError):
        await client.upload_file(
            bucket_name=bucket, filename="data", source_file=str(tmp_path)
        )
    assert await minio_env.keys(bucket) == []


@pytest.mark.parametrize("concurrency", [0, -1, 1.5, True])
@live_test
async def test_upload_dir_invalid_concurrency_has_no_remote_writes(
    minio_env, tmp_path, concurrency
):
    bucket = await minio_env.private_bucket()
    (tmp_path / "file").write_bytes(b"data")
    with pytest.raises(ValueError):
        await minio_env.client().upload_dir(
            bucket_name=bucket, source_dir=str(tmp_path), max_concurrency=concurrency
        )
    assert await minio_env.keys(bucket) == []


@live_test
async def test_upload_dir_missing_bucket_records_each_failure(minio_env, tmp_path):
    bucket = minio_env.new_bucket()
    (tmp_path / "nested").mkdir()
    for key in ["a.txt", "nested/b.txt"]:
        (tmp_path / key).write_bytes(b"data")
    result = await minio_env.client().upload_dir(
        bucket_name=bucket, source_dir=str(tmp_path), prefix="assets", max_concurrency=2
    )
    assert_counts(result, 2, 0, 2)
    assert set(result.failures) == {"a.txt", "nested/b.txt"}
    for reason in result.failures.values():
        assert reason
    assert await minio_env.client().bucket_exists(bucket) is False


@live_test
async def test_upload_dir_partial_read_failure_continues_other_files(
    minio_env, tmp_path
):
    bucket = await minio_env.private_bucket()
    (tmp_path / "nested").mkdir()
    blocked = tmp_path / "nested" / "blocked.txt"
    blocked.write_bytes(b"blocked")
    (tmp_path / "good.txt").write_bytes(b"good")
    blocked.chmod(0)
    try:
        if os.access(blocked, os.R_OK):
            pytest.skip("当前用户可绕过 chmod 权限，无法制造真实的单文件读取失败")
        result = await minio_env.client().upload_dir(
            bucket_name=bucket,
            source_dir=str(tmp_path),
            prefix="assets",
            max_concurrency=2,
        )
        assert_counts(result, 2, 1, 1)
        assert set(result.failures) == {"nested/blocked.txt"}
        assert "PermissionError" in result.failures["nested/blocked.txt"]
        assert "Permission denied" in result.failures["nested/blocked.txt"]
        assert await minio_env.keys(bucket) == ["assets/good.txt"]
        assert await minio_env.get(bucket, "assets/good.txt") == b"good"
    finally:
        blocked.chmod(0o600)


@live_test
async def test_upload_dir_scan_error_starts_no_uploads(minio_env, tmp_path):
    bucket = await minio_env.private_bucket()
    (tmp_path / "good.txt").write_bytes(b"good")
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (blocked / "file.txt").write_bytes(b"blocked")
    blocked.chmod(0)
    try:
        if os.access(blocked, os.R_OK | os.X_OK):
            pytest.skip("当前用户可绕过目录权限，无法制造真实的目录扫描失败")
        with pytest.raises(PermissionError):
            await minio_env.client().upload_dir(
                bucket_name=bucket, source_dir=str(tmp_path)
            )
        assert await minio_env.keys(bucket) == []
    finally:
        blocked.chmod(0o700)


@pytest.mark.parametrize("upload_method", ["write_file", "upload_file", "upload_dir"])
@live_test
async def test_cache_and_content_type_metadata_use_target_keys(
    minio_env, tmp_path, upload_method
):
    bucket = await minio_env.private_bucket()
    client = minio_env.client(
        cache_strategy=[
            CacheStrategy([".PNG", ".gz"], max_age=120),
            CacheStrategy([".png"], max_age=3600),
            CacheStrategy([".txt"], max_age=0),
            CacheStrategy([".css"]),
            CacheStrategy([], max_age=99),
        ],
        content_type_strategy=ContentTypeStrategy(
            default_content_type="application/x-test-default",
            extensions={
                ".PnG": "image/png",
                ".gz": "application/gzip",
                ".txt": "text/plain; charset=utf-8",
            },
        ),
    )
    cases = {
        "image.PNG": ("max-age=3600", "image/png"),
        "archive.tar.gz": ("max-age=120", "application/gzip"),
        "text.txt": ("no-store", "text/plain; charset=utf-8"),
        "style.css": ("max-age=7776000", "application/x-test-default"),
        "unknown.xyz": ("no-store", "application/x-test-default"),
        "no-extension": ("no-store", "application/x-test-default"),
    }
    if upload_method == "upload_dir":
        for key in cases:
            (tmp_path / key).write_bytes(b"metadata")
        result = await client.upload_dir(
            bucket_name=bucket, source_dir=str(tmp_path), prefix="assets"
        )
        assert_counts(result, len(cases), len(cases), 0)
    else:
        # 本地后缀和对象后缀故意不同，禁止通过源文件名推断 MIME。
        source = tmp_path / "source.html"
        source.write_bytes(b"metadata")
        for key in cases:
            kwargs = dict(bucket_name=bucket, filename="assets/" + key)
            if upload_method == "write_file":
                await client.write_file(**kwargs, stream=BytesIO(b"metadata"))
            else:
                await client.upload_file(**kwargs, source_file=str(source))
    for key, (cache, mime) in cases.items():
        head = await minio_env.raw.head_object(Bucket=bucket, Key="assets/" + key)
        assert head["CacheControl"] == cache
        assert head["ContentType"] == mime
        assert await minio_env.get(bucket, "assets/" + key) == b"metadata"


@pytest.mark.parametrize("strategies", [None, []])
@live_test
async def test_default_metadata_and_new_strategy_only_affects_future_uploads(
    minio_env, strategies
):
    bucket = await minio_env.private_bucket()
    initial = minio_env.client(cache_strategy=strategies)
    await initial.write_file(
        bucket_name=bucket, filename="data.png", stream=BytesIO(b"old")
    )
    before = await minio_env.raw.head_object(Bucket=bucket, Key="data.png")
    assert before["CacheControl"] == "no-store"
    assert before["ContentType"] == "application/octet-stream"
    updated = minio_env.client(
        cache_strategy=[CacheStrategy([".png"], max_age=60)],
        content_type_strategy=ContentTypeStrategy(extensions={".png": "image/png"}),
    )
    after = await minio_env.raw.head_object(Bucket=bucket, Key="data.png")
    assert after["CacheControl"] == before["CacheControl"]
    assert after["ContentType"] == before["ContentType"]
    await updated.write_file(
        bucket_name=bucket, filename="data.png", stream=BytesIO(b"new")
    )
    head = await minio_env.raw.head_object(Bucket=bucket, Key="data.png")
    assert head["CacheControl"] == "max-age=60"
    assert head["ContentType"] == "image/png"
    assert await minio_env.get(bucket, "data.png") == b"new"


@live_test
async def test_list_files_all_pages_sorted_and_string_prefixes(minio_env):
    bucket = await minio_env.private_bucket()
    semaphore = asyncio.Semaphore(20)
    # 保留字符串前缀匹配场景，避免 a 同时作为对象和 a/b 的父路径。
    keys = [f"nested/{i:04d}.txt" for i in range(1005)] + [
        "a.txt",
        "ab.txt",
        "a/b",
        "中文.txt",
    ]

    async def put(key):
        async with semaphore:
            await minio_env.put(bucket, key)

    await asyncio.gather(*(put(key) for key in reversed(keys)))
    client = minio_env.client()
    assert await client.list_files(bucket) == sorted(keys)
    assert await client.list_files(bucket, prefix="/nested/") == sorted(keys[:1005])
    assert await client.list_files(bucket, prefix="/a") == ["a.txt", "a/b", "ab.txt"]
    assert await client.list_files(bucket, prefix="absent/") == []
    assert await client.list_files(bucket, prefix="/") == sorted(keys)


@live_test
async def test_force_delete_unversioned_bucket_all_pages(minio_env):
    bucket = await minio_env.private_bucket()
    semaphore = asyncio.Semaphore(20)

    async def put(index):
        async with semaphore:
            await minio_env.put(bucket, f"nested/{index:04d}")

    await asyncio.gather(*(put(index) for index in range(1005)))
    await minio_env.client().remove_bucket(bucket, force=True)
    assert await minio_env.client().bucket_exists(bucket) is False


@live_test
async def test_versioning_delete_markers_and_force_cleanup_all_pages(minio_env):
    bucket = await minio_env.private_bucket()
    await minio_env.raw.put_bucket_versioning(
        Bucket=bucket, VersioningConfiguration={"Status": "Enabled"}
    )
    client = minio_env.client()
    await client.write_file(
        bucket_name=bucket, filename="history", stream=BytesIO(b"v1")
    )
    await client.write_file(
        bucket_name=bucket, filename="history", stream=BytesIO(b"v2")
    )
    versions = (await minio_env.raw.list_object_versions(Bucket=bucket))["Versions"]
    assert len(versions) == 2
    assert len({item["VersionId"] for item in versions}) == 2
    assert await client.read_file(bucket_name=bucket, filename="history") == b"v2"
    await client.remove_file(bucket_name=bucket, filename="history")
    response = await minio_env.raw.list_object_versions(Bucket=bucket)
    assert len(response["Versions"]) == 2
    assert len(response["DeleteMarkers"]) == 1
    assert await client.file_exists(bucket_name=bucket, filename="history") is False
    assert await client.list_files(bucket) == []
    with pytest.raises(ClientError):
        await client.remove_bucket(bucket)
    # 增加足够多的版本记录，验证强制清理不能只处理第一页。
    semaphore = asyncio.Semaphore(20)

    async def put_and_delete(index):
        async with semaphore:
            key = f"versions/{index:04d}"
            await minio_env.put(bucket, key)
            await minio_env.raw.delete_object(Bucket=bucket, Key=key)

    await asyncio.gather(*(put_and_delete(index) for index in range(501)))
    response = await minio_env.raw.list_object_versions(Bucket=bucket, MaxKeys=1000)
    assert response["IsTruncated"] is True
    await client.remove_bucket(bucket, force=True)
    assert await client.bucket_exists(bucket) is False


@live_test
async def test_force_delete_suspended_versioning_including_null_versions(minio_env):
    bucket = await minio_env.private_bucket()
    await minio_env.put(bucket, "null-version", b"before enabled")
    await minio_env.raw.put_bucket_versioning(
        Bucket=bucket, VersioningConfiguration={"Status": "Enabled"}
    )
    await minio_env.put(bucket, "null-version", b"versioned")
    await minio_env.raw.put_bucket_versioning(
        Bucket=bucket, VersioningConfiguration={"Status": "Suspended"}
    )
    await minio_env.put(bucket, "other", b"suspended")
    await minio_env.raw.delete_object(Bucket=bucket, Key="null-version")
    await minio_env.client().remove_bucket(bucket, force=True)
    assert await minio_env.client().bucket_exists(bucket) is False


@live_test
async def test_private_bucket_presigned_get_put_and_anonymous_permissions(minio_env):
    bucket = minio_env.new_bucket()
    client = minio_env.client(
        cache_strategy=[CacheStrategy([".txt"], max_age=3600)],
        content_type_strategy=ContentTypeStrategy(extensions={".txt": "text/plain"}),
    )
    await client.create_bucket(bucket, allow_public_read=False)
    key = "中文 空格/upload+?#%.txt"
    permanent = client.get_file_url(bucket_name=bucket, filename=key)
    assert (await minio_env.http.get(permanent)).status_code == 403
    put_url = await client.presigned_put_file_url(
        bucket_name=bucket, filename="/" + key
    )
    assert await minio_env.keys(bucket) == []
    query = parse_qs(urlsplit(put_url).query)
    assert query["X-Amz-Expires"] == ["3600"]
    assert query["X-Amz-SignedHeaders"] == ["host"]
    response = await minio_env.http.put(put_url, content=b"first")
    response.raise_for_status()
    assert await client.read_file(bucket_name=bucket, filename=key) == b"first"
    head = await minio_env.raw.head_object(Bucket=bucket, Key=key)
    assert head.get("CacheControl") != "max-age=3600"
    assert head["ContentType"] != "text/plain"
    # 同一 URL 可重复使用；调用方负责传二进制和需要的元数据。
    response = await minio_env.http.put(
        put_url,
        content=b"second",
        headers={"Content-Type": "text/plain", "Cache-Control": "max-age=60"},
    )
    response.raise_for_status()
    assert await minio_env.get(bucket, key) == b"second"
    head = await minio_env.raw.head_object(Bucket=bucket, Key=key)
    assert head["ContentType"] == "text/plain"
    assert head["CacheControl"] == "max-age=60"
    get_url = await client.presigned_get_file_url(bucket_name=bucket, filename=key)
    assert parse_qs(urlsplit(get_url).query)["X-Amz-Expires"] == ["3600"]
    response = await minio_env.http.get(get_url)
    assert response.status_code == 200 and response.content == b"second"
    assert (await minio_env.http.get(permanent)).status_code == 403
    with pytest.raises(ClientError) as error:
        await minio_env.raw.get_bucket_policy(Bucket=bucket)
    assert error.value.response.get("Error", {}).get("Code") == "NoSuchBucketPolicy"
    # 签名篡改不能读取私有对象。
    assert (
        await minio_env.http.get(
            get_url.replace("X-Amz-Expires=3600", "X-Amz-Expires=3601")
        )
    ).status_code == 403


@pytest.mark.parametrize("seconds", [1, 604800])
@live_test
async def test_presigned_expiration_boundaries(minio_env, seconds):
    bucket = await minio_env.private_bucket()
    await minio_env.put(bucket, "data")
    for method in ["presigned_get_file_url", "presigned_put_file_url"]:
        url = await getattr(minio_env.client(), method)(
            bucket_name=bucket, filename="data", expires=timedelta(seconds=seconds)
        )
        assert parse_qs(urlsplit(url).query)["X-Amz-Expires"] == [str(seconds)]
    # 不用等待 7 天，也不依赖 1 秒有效期内完成网络请求。


@pytest.mark.parametrize(
    "expires",
    [
        timedelta(0),
        timedelta(seconds=-1),
        timedelta(seconds=604801),
        timedelta(microseconds=1500000),
    ],
)
@live_test
async def test_invalid_presigned_expiration_has_no_side_effects(minio_env, expires):
    bucket = await minio_env.private_bucket()
    for method in ["presigned_get_file_url", "presigned_put_file_url"]:
        with pytest.raises(ValueError):
            await getattr(minio_env.client(), method)(
                bucket_name=bucket, filename="data", expires=expires
            )
    assert await minio_env.keys(bucket) == []


@live_test
async def test_presigning_missing_objects_and_buckets_does_not_create_them(minio_env):
    client = minio_env.client()
    bucket = minio_env.new_bucket()
    for method in ["presigned_get_file_url", "presigned_put_file_url"]:
        url = await getattr(client, method)(bucket_name=bucket, filename="data")
        assert parse_qs(urlsplit(url).query)["X-Amz-Signature"]
    assert await client.bucket_exists(bucket) is False
    await minio_env.raw.create_bucket(Bucket=bucket)
    url = await client.presigned_get_file_url(bucket_name=bucket, filename="missing")
    assert (await minio_env.http.get(url)).status_code == 404
    assert await minio_env.keys(bucket) == []


@pytest.mark.parametrize("filename", ["", "/", "///"])
@live_test
async def test_invalid_object_keys_do_not_write_or_delete_data(
    minio_env, tmp_path, filename
):
    bucket = await minio_env.private_bucket()
    await minio_env.put(bucket, "keep", b"keep")
    source = tmp_path / "source"
    source.write_bytes(b"source")
    client = minio_env.client()
    calls = {
        "write_file": {"stream": BytesIO(b"data")},
        "upload_file": {"source_file": str(source)},
        "download_file": {"target_file": str(tmp_path / "target")},
        "read_file": {},
        "file_exists": {},
        "remove_file": {},
        "presigned_get_file_url": {},
        "presigned_put_file_url": {},
    }
    for method, kwargs in calls.items():
        with pytest.raises(ValueError):
            await getattr(client, method)(
                bucket_name=bucket, filename=filename, **kwargs
            )
    with pytest.raises(ValueError):
        client.get_file_url(bucket_name=bucket, filename=filename)
    assert await minio_env.keys(bucket) == ["keep"]
    assert await minio_env.get(bucket, "keep") == b"keep"
    assert not (tmp_path / "target").exists()
