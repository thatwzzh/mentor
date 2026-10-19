import asyncio
from urllib.parse import urlencode

from opensandbox import Sandbox
from opensandbox.models.execd import RunCommandOpts

from mentor.config.nacos.nacos_access_token import get_access_token
from mentor.config.nacos_settings import nacos_settings
from mentor.utils.sandbox import get_shared_sandbox


# 在共享沙箱内下载并解压，技能包不再经过 agent。
_INSTALL_SKILL = r"""
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

url = os.environ["SKILL_URL"]
token = os.environ["NACOS_ACCESS_TOKEN"]
zip_path = Path(os.environ["SKILL_ZIP_PATH"])
skill_dir = Path(os.environ["SKILL_DIR"])

zip_path.parent.mkdir(parents=True, exist_ok=True)
skill_dir.mkdir(parents=True, exist_ok=True)
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
    partial.unlink(missing_ok=True)
    body = exc.read().decode("utf-8", errors="replace")
    raise SystemExit(f"下载技能失败: HTTP {exc.code} {body}") from None
except Exception:
    partial.unlink(missing_ok=True)
    raise

completed = subprocess.run(["unzip", "-o", str(zip_path), "-d", str(skill_dir)])
if completed.returncode != 0:
    raise SystemExit(completed.returncode)
"""


def _skill_url(skill_name: str) -> str:
    address = nacos_settings.server_address_in_sandbox
    if not address:
        raise RuntimeError(
            "请配置 NACOS_SERVER_ADDRESS_IN_SANDBOX，指定沙箱可直连的 Nacos 地址"
        )
    query = urlencode(
        {
            "namespaceId": nacos_settings.namespace_id,
            "name": skill_name,
            "label": "production",
        }
    )
    return f"{address}/nacos/v3/client/ai/skills?{query}"


async def _save_skill(
    *, skill_name: str, sandbox: Sandbox, zip_path: str, skill_dir: str
):
    access_token = await get_access_token()
    result = await sandbox.commands.run(
        ["python3", "-c", _INSTALL_SKILL],
        opts=RunCommandOpts(
            envs={
                "NACOS_ACCESS_TOKEN": access_token,
                "SKILL_URL": _skill_url(skill_name),
                "SKILL_ZIP_PATH": zip_path,
                "SKILL_DIR": skill_dir,
            }
        ),
    )
    if result.exit_code != 0:
        raise RuntimeError(f"保存技能 {skill_name} 失败: {result}")


async def save_skills(agent_name: str, skill_names: list[str] | None = None):
    "将指定的skills保存到共享卷中"
    if skill_names is None:
        skill_names = []
    sandbox = await get_shared_sandbox()
    skill_dir = f"/shared/skills/{agent_name}"
    await sandbox.commands.run(["rm", "-rf", skill_dir])
    await asyncio.gather(
        *[
            _save_skill(
                skill_name=skill_name,
                sandbox=sandbox,
                zip_path=f"/shared/skill_zips/{agent_name}/{skill_name}.zip",
                skill_dir=skill_dir,
            )
            for skill_name in skill_names
        ]
    )


if __name__ == "__main__":

    async def main():
        sandbox = await get_shared_sandbox()
        await _save_skill(
            skill_name="pptx",
            sandbox=sandbox,
            zip_path="/shared/skill_zips/main_agent/pptx.zip",
            skill_dir="/shared/skills/main_agent",
        )

    asyncio.run(main())
