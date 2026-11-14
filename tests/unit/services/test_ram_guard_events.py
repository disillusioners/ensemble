"""The RAM-guard EVENT EMITTER pack (Phase 5) — the bridge contract.

Covers phase5-plan.md T7 and test-plan.md §3.7 N9 / §3.8 O1 against
arch §4.5 + §15 B7:

* the interface is the B7 alias verbatim
  (``RamBreachEmitter = Callable[[str, Mapping[str, Any]], bool]``);
* the bridge uses ``manager._get_event_loop()`` — NEVER
  ``MainLoopBridge``, NEVER a blocking ``.result()`` (source-level
  pins + behavioral legs);
* the emitted event is ``EventKind.RAM_LIMIT_EXCEEDED`` with
  ``data == dict(payload)`` (the §15 B3 producer's output untouched);
* NON-BLOCKING: the emitter returns before the bus coroutine runs;
* drop semantics are VISIBLE and never raise: no live loop ⇒
  ``False``; bus absent ⇒ ``False``; a closed loop ⇒ ``False``; a
  mid-flight failure is LOGGED by the done-callback, never re-raised;
* the N9 integration leg: a guard wired to a no-loop emitter counts
  ``ram_events_dropped`` and STILL writes the ``WATCHING→KILLED``
  transition log line (allow-listed payload, ``ram-guard:`` prefix);
* the O1 integration leg: a guard wired to a live-loop emitter counts
  exactly ONE emitted event per kill.

House fences: the ``kill_census`` idiom (no signal is ever delivered),
fake procfs trees (no host /proc reads), bounded busy-waits only
(never an unbounded sleep), and every background loop/thread is torn
down in ``finally``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any

import pytest

from daemon.repositories.event.models import EventKind
from daemon.services.ram_guard import ladder as ladder_mod
from daemon.services.ram_guard.events import RamBreachEmitter
from daemon.services.ram_guard.verdict import (
    STATE_BREACHED,
    STATE_KILLED,
)
from daemon.services.ram_guard_events import (
    _log_ram_event_future_exception,
    make_ram_breach_emitter,
)
from tests.unit.services.ram_guard_fixtures import (
    FakeProc,
    stat_line,
    write_proc_tree,
)

from tests.unit.services.test_ram_guard_guard import (  # the house helpers
    CEILING_BYTES,
    LEADER_START,
    SYNTHETIC_PGID,
    TRACKING_ID,
    make_guard,
    register,
)

_REPO_ROOT = None  # set lazily (the pack avoids import-time cwd assumptions)


def _repo_root():
    global _REPO_ROOT
    if _REPO_ROOT is None:
        _REPO_ROOT = _repo_root_path()
    return _REPO_ROOT


def _repo_root_path():
    from pathlib import Path

    return Path(__file__).resolve().parents[3]


def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.01) -> bool:
    """A BOUNDED busy-wait — never an unbounded sleep."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ── House idiom: the census (no signal is ever delivered) ──────────────────


@pytest.fixture
def kill_census(monkeypatch):
    """Record ``(pgid, sig)`` without delivering — nothing is signalled."""
    calls: list[tuple[int, int]] = []

    def _spy_kill(pid, sig):  # noqa: ANN001
        if sig != 0:
            raise AssertionError(f"NON-PROBE os.kill: ({pid}, {sig})")

    def _rec_killpg(pgid, sig):  # noqa: ANN001
        calls.append((pgid, sig))

    monkeypatch.setattr(ladder_mod.os, "kill", _spy_kill)
    monkeypatch.setattr(ladder_mod.os, "killpg", _rec_killpg)
    monkeypatch.setattr(ladder_mod.time, "sleep", lambda s: None)
    return calls


# ── Loop-thread helper (a REAL live loop for the scheduling legs) ──────────


class _LoopThread:
    """A real event loop on a background thread (started/stopped bounded)."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        assert _wait_until(self.loop.is_running), "loop failed to start"

    def stop(self) -> None:
        try:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=2.0)
        finally:
            self.loop.close()


class _FakeManager:
    """Records the hardened accessor's use; optionally raises from it."""

    def __init__(self, loop=None, *, fail: bool = False) -> None:
        self._loop = loop
        self._fail = fail
        self.accessor_calls = 0

    def _get_event_loop(self):
        self.accessor_calls += 1
        if self._fail:
            raise RuntimeError("no live event loop available")
        return self._loop


class _FakeBus:
    """Records created events (the coroutine body runs on the loop)."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def create_event(
        self, *, instance_id: str, kind, data=None, message_id=None
    ) -> None:
        self.events.append(
            {
                "instance_id": instance_id,
                "kind": kind,
                "data": dict(data or {}),
            }
        )


SAMPLE_PAYLOAD: dict[str, Any] = {
    "ok": False,
    "code": "ram_limit_exceeded",
    "state": "KILLED",
    "tracking_id": TRACKING_ID,
    "limit_bytes": CEILING_BYTES,
    "observed_peak_bytes": CEILING_BYTES + 1,
}


# ── The B7 interface pin ────────────────────────────────────────────────────


class TestB7Interface:
    def test_the_alias_line_is_verbatim_in_the_package(self):
        """§15 B7, char-for-char, lives in the package's type module."""
        source = (
            _repo_root()
            / "daemon"
            / "services"
            / "ram_guard"
            / "events.py"
        ).read_text(encoding="utf-8")
        assert "RamBreachEmitter = Callable[[str, Mapping[str, Any]], bool]" in source

    def test_the_factory_returns_a_callable_matching_the_alias(self):
        emitter = make_ram_breach_emitter(manager=_FakeManager(), event_bus=None)
        assert callable(emitter)
        # Behavioral contract: (str, Mapping) → bool — False on a dead bus.
        assert emitter("i-1", SAMPLE_PAYLOAD) is False
        assert isinstance(emitter("i-1", SAMPLE_PAYLOAD), bool)


# ── The bridge's structural pins (NOT MainLoopBridge, NOT blocking) ────────


def _code_uses(source: str, name: str) -> bool:
    """AST-level check: does the CODE (not the docstring prose) USE a
    name? The module docstring legitimately explains what NOT to do —
    the pin is on actual identifier usage."""
    import ast

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name:
            return True
        if isinstance(node, ast.Attribute) and node.attr == name:
            return True
    return False


def _code_calls_method(source: str, method: str) -> bool:
    """AST-level check: is ``<x>.<method>(...)`` CALLED in the code?"""
    import ast

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == method:
                return True
    return False


class TestBridgeStructure:
    def test_the_module_never_names_main_loop_bridge(self):
        source = (
            _repo_root() / "daemon" / "services" / "ram_guard_events.py"
        ).read_text(encoding="utf-8")
        assert not _code_uses(source, "MainLoopBridge")

    def test_the_module_never_blocks_on_a_result(self):
        source = (
            _repo_root() / "daemon" / "services" / "ram_guard_events.py"
        ).read_text(encoding="utf-8")
        assert not _code_calls_method(source, "result")
        assert _code_calls_method(source, "run_coroutine_threadsafe")
        assert _code_calls_method(source, "add_done_callback")
        assert _code_calls_method(source, "_get_event_loop")

    def test_the_emitter_uses_the_hardened_accessor(self):
        """The loop comes from ``manager._get_event_loop()`` — the fake
        manager records the accessor call."""
        lt = _LoopThread()
        try:
            manager = _FakeManager(loop=lt.loop)
            bus = _FakeBus()
            emitter = make_ram_breach_emitter(manager=manager, event_bus=bus)
            assert emitter("i-1", SAMPLE_PAYLOAD) is True
            assert manager.accessor_calls == 1
        finally:
            lt.stop()


# ── The success path (kind + data + non-blocking) ──────────────────────────


class TestSuccessPath:
    def test_the_event_lands_with_the_ram_kind_and_untouched_payload(self):
        lt = _LoopThread()
        try:
            bus = _FakeBus()
            emitter = make_ram_breach_emitter(
                manager=_FakeManager(loop=lt.loop), event_bus=bus
            )
            assert emitter("i-1", SAMPLE_PAYLOAD) is True
            assert _wait_until(lambda: bool(bus.events)), "event never landed"
            (event,) = bus.events  # EXACTLY one event per emit
            assert event["instance_id"] == "i-1"
            assert event["kind"] is EventKind.RAM_LIMIT_EXCEEDED
            assert event["kind"].value == "ram_limit_exceeded"
            assert event["data"] == SAMPLE_PAYLOAD
            assert list(event["data"].keys()) == list(SAMPLE_PAYLOAD.keys())
        finally:
            lt.stop()

    def test_scheduling_is_non_blocking(self):
        """The emitter returns BEFORE the bus coroutine runs (a slow bus
        must never delay the caller — the lane)."""
        lt = _LoopThread()
        try:
            release = threading.Event()
            calls: list[dict[str, Any]] = []

            class _SlowBus:
                async def create_event(self, *, instance_id, kind, data=None, message_id=None):
                    release.wait(timeout=5.0)
                    calls.append({"instance_id": instance_id, "kind": kind})

            emitter = make_ram_breach_emitter(
                manager=_FakeManager(loop=lt.loop), event_bus=_SlowBus()
            )
            started = time.monotonic()
            assert emitter("i-1", SAMPLE_PAYLOAD) is True
            elapsed = time.monotonic() - started
            assert elapsed < 0.5, f"the emitter blocked the caller {elapsed:.3f}s"
            assert calls == []  # the coroutine has NOT run yet
            release.set()
            assert _wait_until(lambda: bool(calls)), "slow bus never completed"
        finally:
            release.set()
            lt.stop()


# ── The drop paths (N9 — visible, never raised, never retried) ─────────────


class TestDropPaths:
    def test_no_live_loop_drops_with_a_warning(self, caplog):
        manager = _FakeManager(fail=True)
        bus = _FakeBus()
        emitter = make_ram_breach_emitter(manager=manager, event_bus=bus)
        with caplog.at_level(logging.WARNING):
            assert emitter("i-1", SAMPLE_PAYLOAD) is False
        assert bus.events == []
        assert "DROPPED" in caplog.text

    def test_an_absent_bus_drops_without_touching_the_loop(self):
        manager = _FakeManager(fail=True)
        emitter = make_ram_breach_emitter(manager=manager, event_bus=None)
        assert emitter("i-1", SAMPLE_PAYLOAD) is False
        assert manager.accessor_calls == 0  # the bus check short-circuits

    def test_an_empty_instance_id_is_defensive_drop(self):
        manager = _FakeManager(fail=True)
        emitter = make_ram_breach_emitter(manager=manager, event_bus=_FakeBus())
        assert emitter("", SAMPLE_PAYLOAD) is False
        assert manager.accessor_calls == 0

    def test_a_closed_loop_drops(self):
        loop = asyncio.new_event_loop()
        loop.close()
        bus = _FakeBus()
        emitter = make_ram_breach_emitter(
            manager=_FakeManager(loop=loop), event_bus=bus
        )
        assert emitter("i-1", SAMPLE_PAYLOAD) is False
        assert bus.events == []

    def test_a_raising_create_event_scheduling_drops(self):
        class _ExplodingBus:
            def create_event(self, **_kwargs):
                raise ValueError("bus exploded")

        lt = _LoopThread()
        try:
            emitter = make_ram_breach_emitter(
                manager=_FakeManager(loop=lt.loop), event_bus=_ExplodingBus()
            )
            assert emitter("i-1", SAMPLE_PAYLOAD) is False
        finally:
            lt.stop()

    def test_a_mid_flight_delivery_failure_is_logged_never_raised(self, caplog):
        """The done-callback logs a failed future; it never re-raises
        into the loop (and a cancelled future stays silent)."""
        loop = asyncio.new_event_loop()
        try:
            failed = loop.create_future()
            failed.set_exception(RuntimeError("loop died mid-flight"))
            cancelled = loop.create_future()
            cancelled.cancel()
            with caplog.at_level(logging.WARNING):
                _log_ram_event_future_exception(failed)  # must not raise
                _log_ram_event_future_exception(cancelled)
            assert "delivery FAILED" in caplog.text
        finally:
            loop.close()

    def test_a_successful_future_is_silent(self, caplog):
        loop = asyncio.new_event_loop()
        try:
            ok = loop.create_future()
            ok.set_result(None)
            with caplog.at_level(logging.WARNING):
                _log_ram_event_future_exception(ok)
            assert "delivery FAILED" not in caplog.text
        finally:
            loop.close()


# ── The composition-root wiring pin (T7 / §15 B15) ─────────────────────────


class TestLifespanWiring:
    """The lifespan is the composition root — the wiring facts are
    pinned at source level (the house anchor-grep idiom): the factory
    is wired, both app-state postures exist, the OFF INFO line exists,
    the facade is installed/cleared, the shutdown joins detached, and
    the block sits AFTER the resource monitor's."""

    @pytest.fixture
    def api_source(self):
        return (_repo_root() / "daemon" / "api.py").read_text(encoding="utf-8")

    def test_the_emitter_factory_is_wired_at_the_composition_root(self, api_source):
        assert "make_ram_breach_emitter(" in api_source
        assert "manager._event_bus" in api_source  # the bus source

    def test_both_app_state_postures_exist(self, api_source):
        assert api_source.count("app.state.ram_guard = None") >= 2  # OFF + shutdown
        assert "app.state.ram_guard = _ram_guard" in api_source  # ON

    def test_the_off_info_line_exists(self, api_source):
        assert "[RamGuard] OFF" in api_source

    def test_the_facade_is_installed_and_cleared(self, api_source):
        assert "set_ram_guard(_ram_guard)" in api_source
        assert "set_ram_guard(None)" in api_source

    def test_the_shutdown_is_a_detached_join(self, api_source):
        assert '"ram-guard-shutdown"' in api_source
        assert "ram_guard" in api_source.split("app.state.resource_monitor = None")[-1]

    def test_the_wiring_sits_after_the_resource_monitor(self, api_source):
        rm = api_source.index("[ResourceMonitor] started")
        rg = api_source.index("[RamGuard] started")
        assert rg > rm

    # ── The DRIVEN composition-root legs (cycle 2 — NO source-grep) ──

    def _boot_config(self, *, on: bool):
        from types import SimpleNamespace
        from daemon.config import RamGuardConfig

        return SimpleNamespace(
            ram_guard_derived_switch=on,
            ram_guard=RamGuardConfig(ceiling_mib=3072),
        )

    def test_driven_the_off_boot_starts_nothing_and_resets_the_state(self):
        """DRIVEN (no source-grep): the OFF composition root RESETS
        ``app.state.ram_guard`` to None and starts NO thread."""
        from types import SimpleNamespace

        from fastapi import FastAPI

        from daemon.api import _boot_ram_guard
        from daemon.services.ram_guard import set_ram_guard

        app = FastAPI()
        app.state.ram_guard = "sentinel-from-a-previous-life"
        before = {t.name for t in threading.enumerate() if t.name.startswith("ram-guard")}
        try:
            guard = _boot_ram_guard(app, manager=SimpleNamespace(), config=self._boot_config(on=False))
            assert guard is None
            assert app.state.ram_guard is None
            after = {t.name for t in threading.enumerate() if t.name.startswith("ram-guard")}
            assert after == before, "the OFF boot must start zero guard threads"
        finally:
            set_ram_guard(None)

    def test_driven_the_on_boot_starts_both_threads_and_wires_the_facade(self):
        """DRIVEN (no source-grep): the ON composition root constructs the
        guard, starts BOTH threads, wires the app state AND the facade."""
        from types import SimpleNamespace

        from fastapi import FastAPI

        from daemon.api import _boot_ram_guard
        from daemon.services.ram_guard import get_ram_guard, set_ram_guard

        app = FastAPI()
        try:
            guard = _boot_ram_guard(app, manager=SimpleNamespace(), config=self._boot_config(on=True))
            assert guard is not None
            assert app.state.ram_guard is guard
            assert get_ram_guard() is guard, "the facade is wired at the composition root"
            health = guard.health()
            assert health["thread_alive"] is True, "the tick thread EXISTS"
            assert health["lane_alive"] is True, "the lane thread EXISTS"
        finally:
            if app.state.ram_guard is not None:
                app.state.ram_guard.stop()
                app.state.ram_guard.join(timeout=3.0)
            set_ram_guard(None)


# ── The integration legs (guard hook ⇄ this emitter) ───────────────────────


def _drive_kill(guard, register_record=True):
    """One tick (detect) + one lane pass (kill) — the deterministic drive."""
    record = register(guard) if register_record else None
    guard._tick_once()
    assert record.state == STATE_BREACHED
    guard._lane_once()
    assert record.state == STATE_KILLED
    return record


class TestGuardIntegration:
    def test_n9_no_loop_counts_the_drop_and_still_logs_the_transition(
        self, tmp_path, kill_census, caplog
    ):
        """The drop is VISIBLE: ``ram_events_dropped++`` AND the
        ``WATCHING→KILLED`` transition log line still carries the
        allow-listed payload."""
        emitter = make_ram_breach_emitter(
            manager=_FakeManager(fail=True), event_bus=_FakeBus()
        )
        guard, _root = make_guard(tmp_path, emitter=emitter)
        with caplog.at_level(logging.INFO):
            record = _drive_kill(guard)
        counters = guard.counters_snapshot()
        assert counters["ram_events_dropped"] == 1
        assert counters["ram_events_emitted"] == 0
        assert counters["ram_kills"] == 1
        # The transition log line — the journal fallback for the drop.
        transition_lines = [
            rec for rec in caplog.records
            if rec.levelno == logging.INFO and "ram-guard:" in rec.getMessage()
        ]
        assert transition_lines, "the WATCHING→KILLED transition line is missing"
        body = transition_lines[0].getMessage()
        payload = json.loads(body.split("ram-guard:", 1)[1].strip())
        assert payload["code"] == "ram_limit_exceeded"
        assert payload["state"] == "KILLED"
        assert payload["tracking_id"] == TRACKING_ID

    def test_o1_a_live_loop_emits_exactly_one_event(self, tmp_path, kill_census):
        lt = _LoopThread()
        try:
            bus = _FakeBus()
            emitter = make_ram_breach_emitter(
                manager=_FakeManager(loop=lt.loop), event_bus=bus
            )
            guard, _root = make_guard(tmp_path, emitter=emitter)
            record = _drive_kill(guard)
            counters = guard.counters_snapshot()
            assert counters["ram_events_emitted"] == 1
            assert counters["ram_events_dropped"] == 0
            assert counters["ram_kills"] == 1
            assert _wait_until(lambda: len(bus.events) == 1), "event never landed"
            assert record.instance_id == bus.events[0]["instance_id"]
            assert bus.events[0]["data"]["tracking_id"] == TRACKING_ID
            # No second lane pass ⇒ no second event (exactly once).
            guard._lane_once()
            time.sleep(0.05)
            assert len(bus.events) == 1
        finally:
            lt.stop()

    def test_a_refusal_emits_no_event(self, tmp_path, kill_census):
        """§17.2 — refusals emit NO event, with the real emitter wired.
        The house RECYCLED-PGID shape (the recycle verdict is P5's —
        P5a RETIRED, §18.3): a real refusal with a real claim (the
        empty-group shape never breaches, so it would prove nothing)."""
        lt = _LoopThread()
        try:
            bus = _FakeBus()
            emitter = make_ram_breach_emitter(
                manager=_FakeManager(loop=lt.loop), event_bus=bus
            )
            guard, root = make_guard(tmp_path, emitter=emitter)
            record = register(guard)
            guard._tick_once()
            assert record.claim_token is not None  # the breach was claimed
            # The leader pid is recycled: a NEW process, different starttime.
            recycled = FakeProc(
                pid=SYNTHETIC_PGID,
                pgrp=SYNTHETIC_PGID,
                ppid=1,
                starttime=LEADER_START + 999,
                rss_kb=1024,
                pss_kb=512,
                environ=b"ENSEMBLE_BASH_TRACKING_ID=t-1\x00",
            )
            (root / str(SYNTHETIC_PGID) / "stat").write_text(
                stat_line(recycled) + "\n"
            )
            guard._lane_once()
            # Ruling A (§18.1): a permanent refusal terminates the KILL
            # DECISION only — the reason is the verdict; the state is
            # REFUSED-but-watching (no release at refusal time).
            assert record.reason == "refused:pgid-recycled"
            assert record.state in ("REFUSED", "RELEASED")
            counters = guard.counters_snapshot()
            assert counters["ram_events_emitted"] == 0
            assert counters["ram_events_dropped"] == 0
            assert counters["ram_kills"] == 0
            time.sleep(0.05)
            assert bus.events == []
        finally:
            lt.stop()


class TestScheduleFailureClosesCoro:
    """Micro-fix pin: a scheduling failure (closed loop) closes the
    coroutine, drops visibly, and emits NO unawaited-coroutine warning."""

    def test_a_closed_loop_schedules_nothing_and_closes_the_coro(self, caplog):
        import warnings

        loop = asyncio.new_event_loop()
        loop.close()
        bus = _FakeBus()
        emitter = make_ram_breach_emitter(
            manager=_FakeManager(loop=loop), event_bus=bus
        )
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            with caplog.at_level(logging.WARNING):
                assert emitter("i-1", SAMPLE_PAYLOAD) is False
            import gc
            gc.collect()
        assert bus.events == []
        assert "DROPPED" in caplog.text
        unawaited = [
            w for w in captured
            if issubclass(w.category, RuntimeWarning)
            and "never awaited" in str(w.message)
        ]
        assert unawaited == [], (
            "the scheduling-failure path must close the coroutine — "
            "no unawaited-coroutine warning may escape"
        )


import pytest

@pytest.fixture(autouse=True)
def _ensemble32_linux_platform(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
