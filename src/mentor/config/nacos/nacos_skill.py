"""技能下发：把 Nacos 中的技能包按内容版本落到共享沙箱卷。

目录约定：

    /shared/skills/<agent>/
      current -> <version>          # 原子切换的符号链接
      <version>/.manifest.json      # 技能名、各技能解压后的内容哈希、更新时间
      <version>/<skill>/SKILL.md    # 解压后的技能包

Agent 只读 ``current``；刷新在后台完成，失败时继续使用上一个可用版本。
"""

import asyncio
import json
import logging
import re
import time
from datetime import timedelta

from opensandbox.models.execd import Execution, RunCommandOpts

from mentor.config.nacos.nacos_access_token import get_access_token
from mentor.config.nacos_settings import nacos_settings
from mentor.utils.sandbox import get_shared_sandbox

logger = logging.getLogger(__name__)

_SKILL_ROOT = "/shared/skills"
_RESULT_MARKER = "__SKILL_RESULT__="
# 宿主进程每隔多久向共享沙箱确认一次 manifest；避免每个请求都进沙箱。
_CHECK_TTL_SECONDS = 60
# manifest 的新鲜期；超过后重新下载并生成新版本。
_REFRESH_TTL_SECONDS = 300
_REFRESH_TIMEOUT = timedelta(seconds=600)
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# 在共享沙箱内完成下载、哈希、解压和原子切换；技能包不经过 Agent 进程。
_REFRESH_SCRIPT = r"""
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

root = Path(os.environ["SKILL_ROOT"])
names = sorted(set(json.loads(os.environ["SKILL_NAMES"])))
base = os.environ["SKILL_BASE_URL"]
token = os.environ["NACOS_ACCESS_TOKEN"]
namespace = os.environ["NACOS_NAMESPACE_ID"]
ttl = int(os.environ.get("SKILL_REFRESH_TTL", "300"))
root.mkdir(parents=True, exist_ok=True)
current = root / "current"
manifest_path = current / ".manifest.json"


def emit(payload):
    print("__SKILL_RESULT__=" + json.dumps(payload, ensure_ascii=False))


def tree_hash(path):
    # 对目录内文件路径和内容做哈希，忽略 zip 时间戳等打包元数据。
    entries = []
    for item in sorted(path.rglob("*")):
        if item.is_file():
            relative = item.relative_to(path).as_posix()
            entries.append(
                (relative, hashlib.sha256(item.read_bytes()).hexdigest())
            )
    return hashlib.sha256(
        json.dumps(entries, separators=(",", ":")).encode()
    ).hexdigest()


def is_fresh():
    try:
        manifest = json.loads(manifest_path.read_text())
        return (
            sorted(manifest.get("skills", [])) == names
            and time.time() - float(manifest.get("updated_at", 0)) < ttl
        )
    except Exception:
        return False


if is_fresh():
    manifest = json.loads(manifest_path.read_text())
    emit({"status": "fresh", "version": manifest.get("version"), "has_current": True})
    sys.exit(0)

lock_file = (root / ".refresh.lock").open("w")
fcntl.flock(lock_file, fcntl.LOCK_EX)
try:
    # 等锁期间可能已有其他进程完成刷新。
    if is_fresh():
        manifest = json.loads(manifest_path.read_text())
        emit({"status": "fresh", "version": manifest.get("version"), "has_current": True})
        sys.exit(0)

    staging = root / f".staging-{uuid.uuid4().hex}"
    staging.mkdir(parents=True)
    artifacts = {}
    try:
        for name in names:
            query = urlencode(
                {"namespaceId": namespace, "name": name, "label": "production"}
            )
            url = f"{base}?{query}"
            zip_path = staging / f"{name}.zip"
            partial = zip_path.with_name(zip_path.name + ".partial")
            request = urllib.request.Request(url, headers={"accessToken": token})
            try:
                with (
                    urllib.request.urlopen(request, timeout=300) as response,
                    partial.open("wb") as target,
                ):
                    while chunk := response.read(1024 * 1024):
                        target.write(chunk)
                partial.replace(zip_path)
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"下载技能 {name} 失败: HTTP {exc.code} {body}"
                ) from None
            finally:
                partial.unlink(missing_ok=True)

            extract_dir = staging / f".extract-{name}"
            extract_dir.mkdir()
            completed = subprocess.run(
                ["unzip", "-o", str(zip_path), "-d", str(extract_dir)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            if completed.returncode != 0:
                detail = completed.stderr.decode("utf-8", errors="replace")
                raise RuntimeError(f"解压技能 {name} 失败: {detail}")

            artifacts[name] = tree_hash(extract_dir)
            # 多个技能解压到同一个 staging 根目录，保持原有“共享技能目录”的语义。
            shutil.copytree(extract_dir, staging, dirs_exist_ok=True)
            shutil.rmtree(extract_dir)
            zip_path.unlink()

        version = hashlib.sha256(
            json.dumps(sorted(artifacts.items()), separators=(",", ":")).encode()
        ).hexdigest()[:16]
        manifest = {
            "skills": names,
            "version": version,
            "updated_at": time.time(),
            "artifacts": artifacts,
        }
        manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2)
        version_dir = root / version
        if version_dir.exists():
            shutil.rmtree(staging)
        else:
            (staging / ".manifest.json").write_text(manifest_text)
            staging.replace(version_dir)
        # 复用已有版本时也刷新 manifest 时间，避免每个请求都重新下载。
        (version_dir / ".manifest.json").write_text(manifest_text)

        # 兼容旧版本遗留的真实目录：只在首次迁移时删除。
        if current.exists() and not current.is_symlink():
            shutil.rmtree(current)
        tmp_link = root / f".current-{uuid.uuid4().hex}"
        os.symlink(version, tmp_link)
        os.replace(tmp_link, current)

        # 只保留最近两个版本，防止共享卷无限增长。
        versions = sorted(
            [
                p
                for p in root.iterdir()
                if p.is_dir() and not p.is_symlink() and not p.name.startswith(".")
            ],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in versions[2:]:
            shutil.rmtree(old, ignore_errors=True)

        emit({"status": "refreshed", "version": version, "has_current": True})
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        emit(
            {
                "status": "error",
                "error": str(exc),
                "has_current": current.exists(),
            }
        )
        sys.exit(1)
finally:
    fcntl.flock(lock_file, fcntl.LOCK_UN)
    lock_file.close()
"""


class SkillRefreshError(RuntimeError):
    """技能刷新失败；``has_current`` 表示旧版本仍可继续使用。"""

    def __init__(self, message: str, *, has_current: bool = False) -> None:
        super().__init__(message)
        self.has_current = has_current


# 进程内状态：agent -> (确认时间, 技能名, 版本)。
_state: dict[str, tuple[float, tuple[str, ...], str]] = {}
_locks: dict[str, asyncio.Lock] = {}
_refreshing: set[str] = set()
_tasks: set[asyncio.Task] = set()


def _validate_names(skill_names: list[str]) -> list[str]:
    names = sorted(set(skill_names))
    for name in names:
        if not _NAME_RE.fullmatch(name) or name in {".", ".."}:
            raise ValueError(f"非法技能名: {name!r}")
    return names


def _skill_base_url() -> str:
    address = nacos_settings.server_address_in_sandbox
    if not address:
        raise RuntimeError(
            "请配置 NACOS_SERVER_ADDRESS_IN_SANDBOX，指定沙箱可直连的 Nacos 地址"
        )
    return f"{address}/nacos/v3/client/ai/skills"


def skill_path(agent_name: str) -> str:
    """Agent 读取技能的稳定路径；指向当前可用版本。"""
    return f"{_SKILL_ROOT}/{agent_name}/current"


def _parse_result(output: str) -> dict | None:
    for line in reversed(output.splitlines()):
        if line.startswith(_RESULT_MARKER):
            try:
                payload = json.loads(line[len(_RESULT_MARKER) :])
            except json.JSONDecodeError:
                return None
            return payload if isinstance(payload, dict) else None
    return None


def _execution_output(result: Execution) -> str:
    """把 OpenSandbox 的 stdout/stderr 消息拼成文本。"""
    logs = getattr(result, "logs", None)
    if logs is None:
        return str(result)
    messages = [*logs.stdout, *logs.stderr]
    return "".join(message.text for message in messages) or str(result)


async def refresh_skills(agent_name: str, skill_names: list[str]) -> dict:
    """立即刷新技能，返回 ``fresh`` 或 ``refreshed`` 结果。"""
    names = _validate_names(skill_names)
    if not names:
        return {"status": "empty", "version": "", "has_current": False}

    sandbox = await get_shared_sandbox()
    token = await get_access_token()
    result = await sandbox.commands.run(
        ["python3", "-c", _REFRESH_SCRIPT],
        opts=RunCommandOpts(
            timeout=_REFRESH_TIMEOUT,
            envs={
                "SKILL_ROOT": f"{_SKILL_ROOT}/{agent_name}",
                "SKILL_NAMES": json.dumps(names),
                "SKILL_BASE_URL": _skill_base_url(),
                "NACOS_ACCESS_TOKEN": token,
                "NACOS_NAMESPACE_ID": nacos_settings.namespace_id,
                "SKILL_REFRESH_TTL": str(_REFRESH_TTL_SECONDS),
            },
        ),
    )
    payload = _parse_result(_execution_output(result))
    if result.exit_code != 0:
        output = _execution_output(result)
        error = (payload or {}).get("error") or output.strip()
        raise SkillRefreshError(
            f"刷新技能 {agent_name} 失败: {error}",
            has_current=bool((payload or {}).get("has_current")),
        )
    if not payload or payload.get("status") not in {"fresh", "refreshed"}:
        raise SkillRefreshError(
            f"刷新技能 {agent_name} 返回非法结果: {_execution_output(result)}"
        )
    return payload


async def _refresh_locked(
    agent_name: str, names: tuple[str, ...]
) -> None:
    lock = _locks.setdefault(agent_name, asyncio.Lock())
    async with lock:
        # 等锁期间可能已有其他协程完成刷新，先复查一次进程内状态。
        state = _state.get(agent_name)
        if (
            state
            and state[1] == names
            and time.monotonic() - state[0] < _CHECK_TTL_SECONDS
        ):
            _refreshing.discard(agent_name)
            return
        try:
            payload = await refresh_skills(agent_name, list(names))
        except SkillRefreshError as exc:
            if exc.has_current or agent_name in _state:
                logger.warning("技能刷新失败，继续使用当前版本: %s", exc)
                return
            raise
        except Exception as exc:
            if agent_name in _state:
                logger.warning("技能刷新失败，继续使用当前版本: %s", exc)
                return
            raise SkillRefreshError(f"刷新技能 {agent_name} 失败: {exc}") from exc
        finally:
            _refreshing.discard(agent_name)
        if payload.get("status") == "refreshed":
            logger.info(
                "技能已刷新: agent=%s version=%s", agent_name, payload.get("version")
            )
        _state[agent_name] = (
            time.monotonic(),
            names,
            str(payload.get("version") or ""),
        )


def _schedule_refresh(agent_name: str, names: tuple[str, ...]) -> None:
    if agent_name in _refreshing:
        return
    _refreshing.add(agent_name)
    task = asyncio.create_task(_refresh_locked(agent_name, names))
    _tasks.add(task)

    def _done(_: asyncio.Task) -> None:
        _tasks.discard(task)
        _refreshing.discard(agent_name)

    task.add_done_callback(_done)


async def ensure_skills(agent_name: str, skill_names: list[str] | None) -> None:
    """确保 Agent 有可用技能，同时把下载从请求路径上移开。

    - 首次构建或技能列表变化：等待刷新完成。
    - 同一列表且缓存新鲜：直接返回。
    - 同一列表但缓存过期：后台刷新，本次继续使用当前版本。
    """
    names = tuple(_validate_names(skill_names or []))
    if not names:
        return

    state = _state.get(agent_name)
    if state and state[1] == names:
        if time.monotonic() - state[0] < _CHECK_TTL_SECONDS:
            return
        _schedule_refresh(agent_name, names)
        return

    await _refresh_locked(agent_name, names)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(asyncio.run(refresh_skills("main_agent", ["pptx"])))
