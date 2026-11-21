"""P3 T5 — dependency getter-at-tick + dead escalation + boot wiring assertion."""

from __future__ import annotations

import time

import pytest

from daemon.services.engine.guardrails import (
    DEP_MISSING_DEAD_FACTOR,
    EXTEND_WEDGE_FACTOR,
    DepMissingTracker,
    DependencyRef,
    ExtendWedgeTracker,
    WiringReport,
    assert_dependencies_resolvable,
    escalate_dead_log_once,
    resolve_at_due,
)


def test_resolve_at_due_calls_getter_at_due_time():
    calls = {"n": 0}

    def getter():
        calls["n"] += 1
        return "dep"

    ref = DependencyRef(label="bus", getter=getter)
    r1 = resolve_at_due([ref])
    r2 = resolve_at_due([ref])
    assert r1.resolved == {"bus": "dep"}
    assert r2.resolved == {"bus": "dep"}
    assert calls["n"] == 2  # once per due-evaluation


def test_resolve_at_due_none_is_missing():
    ref = DependencyRef(label="bus", getter=lambda: None)
    r = resolve_at_due([ref])
    assert r.missing == ["bus"]
    assert r.resolved == {}
    assert not r.ok


def test_dependency_becomes_available_after_start_no_restart():
    state = {"bus": None}

    def getter():
        return state["bus"]

    ref = DependencyRef(label="bus", getter=getter)
    r1 = resolve_at_due([ref])
    assert r1.missing == ["bus"]
    state["bus"] = "live"
    r2 = resolve_at_due([ref])
    assert r2.resolved == {"bus": "live"}


def test_assert_dependencies_resolvable_pure_and_names():
    ref = DependencyRef(label="dep-a", getter=lambda: None)
    import copy

    before = copy.deepcopy  # just a marker
    report = assert_dependencies_resolvable([("job-1", [ref])])
    assert isinstance(report, WiringReport)
    assert report.unresolved == {"job-1": ["dep-a"]}
    assert not report.ok
    # pure: calling again is deterministic
    report2 = assert_dependencies_resolvable([("job-1", [ref])])
    assert report2.unresolved == report.unresolved


def test_assert_dependencies_resolvable_ok_when_resolved():
    ref = DependencyRef(label="dep-a", getter=lambda: "ok")
    report = assert_dependencies_resolvable([("job-1", [ref])])
    assert report.ok
    assert report.unresolved == {}


def test_dep_missing_tracker_escalates_after_2x_interval():
    tr = DepMissingTracker()
    interval = 10.0
    t0 = 1000.0
    tr.record_missing("j", t0)
    assert not tr.should_escalate("j", interval, t0 + 5.0)
    assert not tr.should_escalate("j", interval, t0 + 19.0)
    assert tr.should_escalate("j", interval, t0 + 20.0)
    assert tr.is_escalated("j")
    # once escalated, stays
    assert not tr.should_escalate("j", interval, t0 + 100.0)


def test_dep_missing_tracker_resets_on_resolve():
    tr = DepMissingTracker()
    t0 = 1000.0
    tr.record_missing("j", t0)
    tr.record_resolved("j")
    assert not tr.is_escalated("j")
    assert not tr.should_escalate("j", 10.0, t0 + 100.0)


def test_escalate_dead_log_once_single():
    esc = set()
    assert escalate_dead_log_once("j", esc, level="error", message="boom") is True
    assert escalate_dead_log_once("j", esc, level="error", message="boom") is False
    assert "j" in esc


def test_extend_wedge_tracker_escalates_after_2x():
    tr = ExtendWedgeTracker()
    t0 = 1000.0
    tr.record_hold_start("j", t0, max_runtime_s=5.0)
    assert not tr.check_escalate("j", t0 + 9.0)
    assert tr.check_escalate("j", t0 + 10.0)
    # once escalated
    assert not tr.check_escalate("j", t0 + 100.0)


def test_extend_wedge_tracker_clears_on_release():
    tr = ExtendWedgeTracker()
    t0 = 1000.0
    tr.record_hold_start("j", t0, max_runtime_s=5.0)
    tr.clear_hold("j")
    assert not tr.check_escalate("j", t0 + 100.0)


def test_factors():
    assert DEP_MISSING_DEAD_FACTOR == 2
    assert EXTEND_WEDGE_FACTOR == 2
