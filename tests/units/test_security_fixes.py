"""S1/S2/S4/S5 修复的回归测试：路径校验、Nacos 错误映射、桶策略匹配与技能路径。"""

import pytest

from mentor.backends.sandbox_backend import _validate_delete_path
from mentor.config.nacos.nacos_agent_spec import (
    AgentSpecNotFound,
    AgentSpecUnavailable,
    _raise_for_payload,
)
from mentor.config.nacos.nacos_skill import _validate_names, skill_path
from mentor.utils.minio import _is_public_read_statement


@pytest.mark.parametrize(
    "path",
    [
        "/root/workspace/a.txt",
        "/root/workspace/project/dist",
        "/root/.cache/pip",
    ],
)
def test_validate_delete_path_accepts_workspace(path):
    assert _validate_delete_path(path) == path


@pytest.mark.parametrize(
    "path",
    [
        "",
        "relative/path",
        "/etc/passwd",
        "/root",
        "/root/",
        "/root/../etc/passwd",
        "/root/workspace/../../etc",
        "/root/workspace/a\nb",
    ],
)
def test_validate_delete_path_rejects_escape(path):
    with pytest.raises(ValueError):
        _validate_delete_path(path)


def test_nacos_payload_code_mapping():
    _raise_for_payload({"code": 0}, "main_agent")

    with pytest.raises(AgentSpecNotFound):
        _raise_for_payload({"code": 404}, "main_agent")

    with pytest.raises(AgentSpecNotFound):
        _raise_for_payload({"code": 40400}, "main_agent")

    with pytest.raises(AgentSpecUnavailable):
        _raise_for_payload({"code": 500, "message": "boom"}, "main_agent")

    with pytest.raises(AgentSpecUnavailable):
        _raise_for_payload({}, "main_agent")


def test_skill_names_and_path():
    assert _validate_names(["pptx", "docx", "pptx"]) == ["docx", "pptx"]
    assert skill_path("main_agent") == "/shared/skills/main_agent/current"

    for name in ["", "..", "../escape", "a/b", "a\nb"]:
        with pytest.raises(ValueError):
            _validate_names([name])


def test_public_read_statement_matching():
    resource = "arn:aws:s3:::agent-artifacts/*"
    public = {
        "Effect": "Allow",
        "Principal": {"AWS": ["*"]},
        "Action": ["s3:GetObject"],
        "Resource": [resource],
    }
    assert _is_public_read_statement(public, resource)

    conditioned = {**public, "Condition": {"IpAddress": {"aws:SourceIp": "10.0.0.0/8"}}}
    assert not _is_public_read_statement(conditioned, resource)

    write = {**public, "Action": ["s3:PutObject"]}
    assert not _is_public_read_statement(write, resource)

    other_resource = {**public, "Resource": ["arn:aws:s3:::other/*"]}
    assert not _is_public_read_statement(other_resource, resource)
