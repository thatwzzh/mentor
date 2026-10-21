"""4.2.2 配置层测试：request override、label、JSON Schema、CLI 与 doctor 纯检查。"""

import json
import os
import tempfile

import pytest
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError

from mentor.agents.agent_cache import AgentCache
from mentor.cli import main
from mentor.config.doctor import check_spec
from mentor.config.errors import ConfigUnavailableError
from mentor.config.layers import merge_agent_spec, resolve_label
from mentor.config.nacos.nacos_agent_spec import AgentSpecUnavailable
from mentor.config.nacos.nacos_schemas import AgentModelConfig, AgentSpec


def test_merge_agent_spec_overrides_only_explicit_fields():
    base = AgentSpec(
        name="main",
        system_prompt="base prompt",
        tools=["read"],
        model=AgentModelConfig(name="model-a", temperature=0.5),
    )
    merged = merge_agent_spec(
        base,
        {
            "system_prompt": "override prompt",
            "tools": ["write"],
            "model": {"temperature": 0.1},
        },
    )

    assert merged.system_prompt == "override prompt"
    assert merged.tools == ["write"]
    # 只覆盖显式传入的 temperature，保留 Nacos 中的模型名。
    assert merged.model.name == "model-a"
    assert merged.model.temperature == 0.1
    # 原对象不被修改，缓存 hash 才能稳定。
    assert base.system_prompt == "base prompt"
    assert base.model.temperature == 0.5


def test_override_rejects_unknown_field():
    base = AgentSpec(name="main")
    with pytest.raises(ValidationError):
        merge_agent_spec(base, {"unknown_field": 1})


def test_resolve_label_accepts_staging_and_rejects_other():
    assert resolve_label("staging") == "staging"
    assert resolve_label("production") == "production"
    with pytest.raises(ValueError):
        resolve_label("dev")


def test_agent_spec_schema_and_error_hierarchy():
    schema = AgentSpec.model_json_schema()
    assert "name" in schema["required"]
    assert "model" in schema["properties"]
    assert "AgentModelConfig" in schema.get("$defs", {})
    assert issubclass(AgentSpecUnavailable, ConfigUnavailableError)


def test_check_spec_reports_missing_required_fields():
    valid = AgentSpec(
        name="main", system_prompt="prompt", model=AgentModelConfig(name="model-a")
    )
    assert all(result.ok for result in check_spec(valid))

    invalid = AgentSpec(name="", system_prompt="", model=AgentModelConfig(name=""))
    assert not all(result.ok for result in check_spec(invalid))


def test_cli_validate_file_ok_and_invalid(capsys):
    def write_temp(payload: dict) -> str:
        # 避开 pytest tmp_path：当前宿主的目录 broker 对已存在的 pytest 临时目录会抛 EEXIST。
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        try:
            json.dump(payload, handle)
            return handle.name
        finally:
            handle.close()

    valid_path = write_temp(
        {"name": "main", "system_prompt": "p", "model": {"name": "model-a"}}
    )
    invalid_path = write_temp({"name": 1})
    try:
        assert main(["config", "validate", "--file", valid_path]) == 0
        assert "OK:" in capsys.readouterr().out

        assert main(["config", "validate", "--file", invalid_path]) == 1
        assert "INVALID:" in capsys.readouterr().err
    finally:
        os.unlink(valid_path)
        os.unlink(invalid_path)


def test_cli_schema_outputs_agent_spec(capsys):
    assert main(["config", "schema"]) == 0
    assert '"title": "AgentSpec"' in capsys.readouterr().out


def test_agent_cache_versions_support_rollback_lookup():
    cache = AgentCache(max_versions_per_agent=3)
    cache.put("main", "v1", RunnableLambda(lambda state: state))
    cache.put("main", "v2", RunnableLambda(lambda state: state))
    assert cache.versions("main") == ["v1", "v2"]
