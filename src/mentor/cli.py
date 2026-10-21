"""mentor 命令行入口：配置 Schema 导出、Spec 校验与启动前 config doctor。

用法：
    mentor config schema
    mentor config validate --file path/to/agent_spec.json
    mentor config validate --agent main_agent
    mentor config doctor [--agent main_agent]
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from mentor.config.doctor import run_doctor
from mentor.config.errors import ConfigError
from mentor.config.nacos.nacos_agent_spec import get_agent_spec
from mentor.config.nacos.nacos_schemas import AgentSpec


def _load_spec_file(path: str) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    if path.endswith(".json"):
        return json.loads(text)
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("配置文件必须是 JSON/YAML 对象")
    return data


def _cmd_schema(_: argparse.Namespace) -> int:
    print(json.dumps(AgentSpec.model_json_schema(), ensure_ascii=False, indent=2))
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        if args.file:
            data = _load_spec_file(args.file)
        else:
            spec = asyncio.run(get_agent_spec(args.agent))
            data = spec.model_dump()
        AgentSpec.model_validate(data)
    except (OSError, ValueError, yaml.YAMLError, ValidationError, ConfigError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {args.file or args.agent}")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    results = asyncio.run(run_doctor(args.agent))
    for result in results:
        status = "OK  " if result.ok else "FAIL"
        print(f"{status} {result.name}: {result.detail}")
    return 0 if all(result.ok for result in results) else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mentor")
    config = parser.add_subparsers(dest="command", required=True).add_parser("config")
    config_sub = config.add_subparsers(dest="config_command", required=True)

    schema = config_sub.add_parser("schema", help="输出 AgentSpec JSON Schema")
    schema.set_defaults(func=_cmd_schema)

    validate = config_sub.add_parser("validate", help="校验本地文件或 Nacos Spec")
    source = validate.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", help="本地 JSON/YAML 文件")
    source.add_argument("--agent", help="从 Nacos 读取的 Agent 名称")
    validate.set_defaults(func=_cmd_validate)

    doctor = config_sub.add_parser("doctor", help="启动前配置体检")
    doctor.add_argument("--agent", default="main_agent", help="要检查的 Agent 名称")
    doctor.set_defaults(func=_cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
