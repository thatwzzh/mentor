"""5.9 golden session 回放：不调真实 LLM/网络。"""

from pathlib import Path

import pytest

from mentor.golden import load_case, replay_case
from mentor.golden.models import GoldenExpected

ROOT = Path(__file__).resolve().parents[2]
SESSIONS = sorted((ROOT / "tests/golden/sessions").glob("*.json"))


@pytest.mark.parametrize("path", SESSIONS, ids=lambda path: path.stem)
def test_golden_sessions_replay(path):
    replay_case(load_case(path))


def test_golden_replay_detects_event_drift():
    case = load_case(SESSIONS[0])
    assert case.expected is not None
    broken = case.model_copy(
        update={
            "expected": GoldenExpected(
                event_kinds=["run_started", "wrong"],
                event_names=[None, None],
                final=case.expected.final,
            )
        }
    )
    with pytest.raises(AssertionError, match="event_kinds"):
        replay_case(broken)
