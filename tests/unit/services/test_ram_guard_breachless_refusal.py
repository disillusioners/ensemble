"""ENSEMBLE-32 item 3/6b: breach-less refusal emits refusal line only."""

import pytest

from daemon.services.ram_guard.errors import (
    PROC_BREACH_OBSERVED_LINE,
    PROC_REFUSAL_LINE,
    format_ram_proc_lines,
    breach_observed,
)
from daemon.services.ram_guard.verdict import STATE_REFUSED
from daemon.services.ram_guard.ladder import REASON_PLATFORM_UNSUPPORTED, REASON_LEADER_UNREADABLE


class _Rec:
    def __init__(self, reason, breach_at=None, state=STATE_REFUSED):
        self.reason = reason
        self.breach_at = breach_at
        self.state = state
        self.is_terminal = False
        self.observed_peak_bytes = 100


@pytest.mark.parametrize("reason", [REASON_PLATFORM_UNSUPPORTED, REASON_LEADER_UNREADABLE])
def test_errors_surface_no_breach_observed_when_breach_at_none(reason):
    rec = _Rec(reason=reason, breach_at=None)
    lines = format_ram_proc_lines(rec)
    assert any("ram_kill_refused:" in ln for ln in lines)
    assert PROC_BREACH_OBSERVED_LINE not in lines
    assert breach_observed(rec) is False


def test_errors_surface_emits_breach_observed_when_breach_at_set():
    rec = _Rec(reason=REASON_PLATFORM_UNSUPPORTED, breach_at=123)
    # breach_at set but reason refused => refusal branch; breach line only if breach_observed
    lines = format_ram_proc_lines(rec)
    # refusal branch emits breach line only when breach_observed
    assert PROC_BREACH_OBSERVED_LINE in lines


def test_breach_observed_false_for_none():
    assert breach_observed(None) is False
    assert breach_observed(_Rec(REASON_PLATFORM_UNSUPPORTED, breach_at=None)) is False
