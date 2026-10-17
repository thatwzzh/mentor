"""Deploy 公开接口的真实集成验收；不使用 xfail 掩盖失败。"""

import asyncio
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlsplit

import pytest
from langchain_core.tools import ToolException
from pydantic import ValidationError

from deploy_support import live_deploy_test


ROOT = "/tmp/deploy-integration/workspace"


def assert_counts(result, total, success, failure):
    assert (
        result["total_count"],
        result["success_count"],
        result["failure_count"],
    ) == (total, success, failure)


async def assert_objects(live, user_id, target, files):
    prefix = f"{user_id}/{target}/"
    keys = await live.minio.keys(live.bucket)
    assert [key for key in keys if key.startswith(prefix)] == sorted(
        prefix + name for name in files
    )
    for name, content in files.items():
        assert await live.minio.get(live.bucket, prefix + name) == content


async def assert_urls(live, result, user_id, target, entries, files):
    assert len(result["urls"]) == len(entries)
    for entry, url in zip(entries, result["urls"], strict=True):
        parsed = urlsplit(url)
        host = urlsplit(
            ("https://" if live.settings.secure else "http://") + live.settings.endpoint
        )
        assert parsed.scheme == host.scheme and parsed.netloc == host.netloc
        assert unquote(parsed.path) == f"/{live.bucket}/{user_id}/{target}/{entry}"
        response = await live.minio.http.get(url)
        assert response.status_code == 200
        assert response.content == files[entry]


async def assert_rejected(live, user_id, **args):
    before = await live.minio.keys(live.bucket)
    try:
        message = await live.invoke(user_id, **args)
    except (
        ToolException,
        ValueError,
        ValidationError,
        FileNotFoundError,
        PermissionError,
    ):
        pass
    else:
        assert (
            message.status == "error"
        ), f"应返回 Agent 可识别的错误，实际为 {message.content!r}"
        assert message.content
    assert await live.minio.keys(live.bucket) == before


@pytest.mark.parametrize(
    "content",
    [b"", "中文报告\n".encode(), bytes(range(256)), b"x" * (6 * 1024 * 1024)],
    ids=["empty", "utf8", "binary", "6MiB"],
)
@live_deploy_test
async def test_single_file_and_real_entry_url(deploy_env, content):
    live = deploy_env
    user_id = await live.user()
    source = ROOT + "/artifact.bin"
    assert not Path(source).exists(), "源文件必须仅存在于沙箱"
    await live.files(user_id, {source: content})
    result = await live.deploy(
        user_id, path=source, target_dirname="report", entry_files=["artifact.bin"]
    )
    assert_counts(result, 1, 1, 0)
    assert result["failures"] == {}
    assert live.commands, "应通过真实沙箱执行上传"
    files = {"artifact.bin": content}
    await assert_objects(live, user_id, "report", files)
    await assert_urls(live, result, user_id, "report", ["artifact.bin"], files)
    assert await live.users[user_id].files.read_bytes(source) == content


@live_deploy_test
async def test_recursive_directory_relative_paths_hidden_and_special_names(deploy_env):
    live = deploy_env
    user_id = await live.user()
    files = {
        "index.html": b"<html>hello</html>",
        "static/app.js": b"console.log(1)",
        "a/same.txt": b"a",
        "b/same.txt": b"b",
        ".config": b"hidden",
        "empty": b"",
        "中文 空格/a+?#%.txt": "特殊字符".encode(),
        "quote'$(touch INJECTED);.txt": b"literal filename",
    }
    source = ROOT + "/dist"
    await live.files(
        user_id, {source + "/" + name: data for name, data in files.items()}
    )
    await live.command(
        user_id, f"import pathlib; pathlib.Path({source + '/empty-dir'!r}).mkdir()"
    )
    entries = ["中文 空格/a+?#%.txt", "index.html", "static/app.js"]
    result = await live.deploy(
        user_id, path=source, target_dirname="中文 report", entry_files=entries
    )
    assert_counts(result, len(files), len(files), 0)
    await assert_objects(live, user_id, "中文 report", files)
    await assert_urls(live, result, user_id, "中文 report", entries, files)
    await live.command(
        user_id, "import pathlib; assert not pathlib.Path('INJECTED').exists()"
    )


@live_deploy_test
async def test_existing_objects_are_overwritten_without_deleting_extra_objects(
    deploy_env,
):
    live = deploy_env
    uid = await live.user()
    source = ROOT + "/index.html"
    await live.files(uid, {source: b"v1"})
    args = dict(path=source, target_dirname="site", entry_files=["index.html"])
    assert_counts(await live.deploy(uid, **args), 1, 1, 0)
    await live.minio.put(live.bucket, f"{uid}/site/keep.txt", b"keep")
    await live.files(uid, {source: b"v2"})
    result = await live.deploy(uid, **args)
    assert_counts(result, 1, 1, 0)
    await assert_objects(live, uid, "site", {"index.html": b"v2", "keep.txt": b"keep"})
    await assert_urls(live, result, uid, "site", ["index.html"], {"index.html": b"v2"})


@live_deploy_test
async def test_concurrent_users_use_authenticated_id_and_their_own_sandbox(deploy_env):
    live = deploy_env
    users = [await live.user(), await live.user()]
    source = ROOT + "/index.html"
    for uid in users:
        await live.files(uid, {source: uid.encode()})
    results = await asyncio.gather(
        *(
            live.deploy(
                uid,
                path=source,
                target_dirname="same-project",
                entry_files=["index.html"],
            )
            for uid in users
        )
    )
    for uid, result in zip(users, results, strict=True):
        assert_counts(result, 1, 1, 0)
        await assert_objects(live, uid, "same-project", {"index.html": uid.encode()})
        await assert_urls(
            live,
            result,
            uid,
            "same-project",
            ["index.html"],
            {"index.html": uid.encode()},
        )
    assert len(await live.minio.keys(live.bucket)) == 2


@pytest.mark.parametrize(
    "target",
    ["", "/", "..", "../other-user", "a/../../other-user", "/absolute", "bad\x00name"],
)
@live_deploy_test
async def test_invalid_target_cannot_escape_user_prefix(deploy_env, target):
    live = deploy_env
    uid = await live.user()
    source = ROOT + "/index.html"
    await live.files(uid, {source: b"private"})
    await assert_rejected(
        live, uid, path=source, target_dirname=target, entry_files=["index.html"]
    )


@pytest.mark.parametrize(
    "entries",
    [
        [],
        [""],
        ["../secret"],
        ["/index.html"],
        ["a/../../secret"],
        ["missing.html"],
        ["index.html", "missing.html"],
        ["static"],
    ],
)
@live_deploy_test
async def test_invalid_or_missing_entries_fail_before_upload(deploy_env, entries):
    live = deploy_env
    uid = await live.user()
    source = ROOT + "/dist"
    await live.files(
        uid, {source + "/index.html": b"hello", source + "/static/a.txt": b"a"}
    )
    await assert_rejected(
        live, uid, path=source, target_dirname="site", entry_files=entries
    )


@pytest.mark.parametrize(
    "source",
    ["", "relative/file", ROOT + "/missing", ROOT + "/empty-dir", ROOT + "/fifo"],
)
@live_deploy_test
async def test_invalid_missing_empty_or_nonregular_source(deploy_env, source):
    live = deploy_env
    uid = await live.user()
    await live.command(
        uid,
        f"""
import pathlib, os
pathlib.Path({ROOT!r}).mkdir(parents=True)
pathlib.Path({ROOT + '/empty-dir'!r}).mkdir()
os.mkfifo({ROOT + '/fifo'!r})
""",
    )
    await assert_rejected(
        live, uid, path=source, target_dirname="site", entry_files=["index.html"]
    )


@live_deploy_test
async def test_symlinks_and_fifo_are_not_uploaded_or_followed(deploy_env):
    live = deploy_env
    uid = await live.user()
    source = ROOT + "/dist"
    await live.files(
        uid, {source + "/index.html": b"index", ROOT + "/secret": b"secret"}
    )
    await live.command(
        uid,
        f"""
import pathlib, os
root = pathlib.Path({source!r})
(root / 'outside').symlink_to({ROOT + '/secret'!r})
(root / 'loop').symlink_to(root, target_is_directory=True)
(root / 'broken').symlink_to(root / 'missing')
os.mkfifo(root / 'fifo')
""",
    )
    result = await live.deploy(
        uid, path=source, target_dirname="site", entry_files=["index.html"]
    )
    assert_counts(result, 1, 1, 0)
    await assert_objects(live, uid, "site", {"index.html": b"index"})


@live_deploy_test
async def test_missing_authentication_does_not_upload(deploy_env):
    live = deploy_env
    uid = await live.user()
    source = ROOT + "/index.html"
    await live.files(uid, {source: b"secret"})
    with pytest.raises(RuntimeError, match="langgraph_auth_user_id"):
        await live.invoke(
            None, path=source, target_dirname="site", entry_files=["index.html"]
        )
    assert await live.minio.keys(live.bucket) == []


@live_deploy_test
async def test_sandbox_unreachable_minio_endpoint_reports_failure(deploy_env):
    live = deploy_env
    uid = await live.user()
    source = ROOT + "/index.html"
    await live.files(uid, {source: b"index"})
    # 沙箱的 loopback，与宿主的可用 MINIO_ENDPOINT 故意不同，防止退回宿主上传。
    live.settings.endpoint_in_sandbox = "127.0.0.1:1"
    with pytest.raises(RuntimeError, match="部署失败"):
        await live.invoke(
            uid, path=source, target_dirname="site", entry_files=["index.html"]
        )
    assert await live.minio.keys(live.bucket) == []


def test_agent_schema_keeps_user_identity_out_of_model_arguments():
    from mentor.tools.deploy import deploy

    tool_schema = deploy.tool_call_schema
    schema = (
        tool_schema
        if isinstance(tool_schema, dict)
        else cast(Any, tool_schema).model_json_schema()
    )
    assert set(schema["properties"]) == {"path", "target_dirname", "entry_files"}
    assert set(schema["required"]) == {"path", "target_dirname", "entry_files"}


@live_deploy_test
async def test_same_user_different_projects_do_not_overwrite_each_other(deploy_env):
    live = deploy_env
    uid = await live.user()
    # 目录名同时包含引号和 shell 元字符，检查源路径的正确传参。
    source = ROOT + "/中文 '$(touch INJECTED);/index.html"
    for target, data in [("first", b"first"), ("second", b"second")]:
        await live.files(uid, {source: data})
        result = await live.deploy(
            uid, path=source, target_dirname=target, entry_files=["index.html"]
        )
        assert_counts(result, 1, 1, 0)
        await assert_urls(
            live, result, uid, target, ["index.html"], {"index.html": data}
        )
    await assert_objects(live, uid, "first", {"index.html": b"first"})
    await assert_objects(live, uid, "second", {"index.html": b"second"})
    await live.command(
        uid, "import pathlib; assert not pathlib.Path('INJECTED').exists()"
    )


@live_deploy_test
async def test_invalid_minio_credentials_cannot_report_success(deploy_env):
    live = deploy_env
    uid = await live.user()
    source = ROOT + "/index.html"
    await live.files(uid, {source: b"index"})
    live.settings.secret_key = "deliberately-invalid-deploy-signing-secret"
    with pytest.raises(RuntimeError, match="部署失败"):
        await live.invoke(
            uid, path=source, target_dirname="site", entry_files=["index.html"]
        )
    assert await live.minio.keys(live.bucket) == []
