"""``GET /api/resources/ram-guard`` — the Phase-5 read surface pack.

Covers phase5-plan.md T12 / T13 / T14 / T15 and test-plan.md §3.8
O3/O4/O5/O6 against arch §15 B23 + B5:

* the endpoint exists, is ADDITIVE, and returns the SINGLE eleven-field
  heartbeat pin + the B5 counter set — EXACTLY, in order, nothing else;
* the OFF / pre-init posture degrades to the EMPTY SHAPE (enabled:false
  + zeros/nulls), never 503 (D11 house rule);
* the response is a VERBATIM passthrough of the substrate's ordered
  surfaces (``health()`` / ``counters_snapshot()``);
* ``/control-capabilities`` is byte-identical (the same pinned shape the
  UNMODIFIED fence pack pins — re-asserted here on a bare app);
* O3/O4 — the four-surface negative space: a command's argv shape and a
  secret-marker value appear in NONE of the envelope, the event payload,
  the transition log line, and the endpoint response.

House fences: fake procfs trees only (no host /proc), the kill_census
idiom (no signal delivered), the guard never STARTED (no threads left
behind), and a bare FastAPI app (no live manager, no DB).
"""

from __future__ import annotations

import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daemon.routers.resources import (
    _CONTROL_ACTION_NAMES,
    router as resources_router,
)
from daemon.services.ram_guard.events import RamBreachEmitter  # noqa: F401  (pin importability)
from daemon.services.ram_guard.guard import COUNTER_KEYS, HEARTBEAT_KEYS
from daemon.services.ram_guard.verdict import STATE_BREACHED, STATE_KILLED
from daemon.services.ram_guard_events import make_ram_breach_emitter
from tests.unit.services.ram_guard_fixtures import FakeProc, stat_line
from tests.unit.services.test_ram_guard_guard import (
    LEADER_START,
    SYNTHETIC_PGID,
    TRACKING_ID,
    make_guard,
    register,
)

#: A distinctive ARGV SHAPE (O4 — the exact leak class) and a
#: SECRET-MARKER value (O3). Neither may appear on ANY surface.
ARGV_SHAPE = "python3 -c 'import os; os.sbrk()' --flag SECRET_VAL_7f3a9"
SECRET_MARKER = "__ENS_SECRET__0f83bcc1-secret-value"


class _RecordingEmitter:
    """The bus-backed emitter's double: records; returns True."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, instance_id: str, payload: dict) -> bool:
        self.calls.append((instance_id, dict(payload)))
        return True


@pytest.fixture
def kill_census(monkeypatch):
    """The house census idiom — record ``(pgid, sig)``, never deliver."""
    from daemon.services.ram_guard import ladder as ladder_mod

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


def _client() -> TestClient:
    """A bare app + the resources router.

    ``app.state.manager`` is set to an engine-less namespace — WITHOUT
    it the repo conftest fabricates a MagicMock manager whose engine
    makes ``policy_rows`` a meaningless ``int(MagicMock) == 1``; the
    namespace simulates the degraded boot deterministically (the
    seam-pack's own degrade idiom).
    """
    from types import SimpleNamespace

    app = FastAPI()
    app.include_router(resources_router, prefix="/api")
    app.state.manager = SimpleNamespace()  # no .engine ⇒ the read is skipped
    return TestClient(app)


def _drive_kill(guard):
    record = register(guard)
    guard._tick_once()
    assert record.state == STATE_BREACHED
    guard._lane_once()
    assert record.state == STATE_KILLED
    return record


# ── O6 — the endpoint shape ─────────────────────────────────────────────────


class TestEndpointShape:
    def test_the_absent_guard_degrades_to_the_exact_empty_shape(self):
        client = _client()
        response = client.get("/api/resources/ram-guard")
        assert response.status_code == 200
        body = response.json()
        assert list(body.keys()) == list(HEARTBEAT_KEYS) + list(COUNTER_KEYS)
        assert body["enabled"] is False
        assert body["thread_alive"] is False
        assert body["last_tick_at"] is None
        assert body["resolved_ceiling_bytes"] is None
        assert body["ceiling_source"] is None
        assert all(body[key] == 0 for key in COUNTER_KEYS)

    def test_a_constructed_guard_is_reported_verbatim(self, tmp_path):
        guard, _root = make_guard(tmp_path)
        client = _client()
        client.app.state.ram_guard = guard
        response = client.get("/api/resources/ram-guard")
        assert response.status_code == 200
        body = response.json()
        assert list(body.keys()) == list(HEARTBEAT_KEYS) + list(COUNTER_KEYS)
        assert body == {**guard.health(), **guard.counters_snapshot()}
        assert body["enabled"] is True
        assert body["ceiling_source"] == "derived-meminfo"  # the injected resolved pair
        assert body["resolved_ceiling_bytes"] == 3072 * 1024 * 1024

    def test_the_b23_eleven_fields_are_the_pin_verbatim(self):
        assert list(HEARTBEAT_KEYS) == [
            "enabled",
            "thread_alive",
            "last_tick_at",
            "missed_samples",
            "ticks",
            "registry_depth",
            "lane_alive",
            "lane_pending",
            "last_lane_progress_at",
            "resolved_ceiling_bytes",
            "ceiling_source",
        ]

    def test_the_b5_counters_are_the_pin_verbatim(self):
        assert list(COUNTER_KEYS) == [
            "ram_kills",
            "ram_breaches_detected",
            "ram_kill_refusals_p0",
            "ram_kill_refusals_p1",
            "ram_kill_refusals_p2",
            "ram_kill_refusals_p3",
            "ram_kill_refusals_p4",
            "ram_kill_refusals_p5",
            "ram_kill_refusals_p7",
            "ram_overcounted",
            "ram_rearms",
            "ram_confirm_failures",
            "ram_events_emitted",
            "ram_events_dropped",
            "ownership_unverified",
            "tree_incomplete",
        ]
        assert "ram_kill_refusals_p8" not in COUNTER_KEYS

    def test_a_killed_breach_visible_on_the_endpoint(
        self, tmp_path, kill_census
    ):
        emitter = _RecordingEmitter()
        guard, _root = make_guard(tmp_path, emitter=emitter)
        record = _drive_kill(guard)
        assert record.reason.startswith("killed:")
        counters = guard.counters_snapshot()
        assert counters["ram_kills"] == 1
        assert counters["ram_events_emitted"] == 1

        client = _client()
        client.app.state.ram_guard = guard
        body = client.get("/api/resources/ram-guard").json()
        assert body["ram_kills"] == 1
        assert body["ram_events_emitted"] == 1
        assert body["ram_events_dropped"] == 0
        assert body["ticks"] >= 1


# ── O5 — /control-capabilities stays byte-identical ────────────────────────


class TestControlCapabilitiesByteIdentity:
    def test_the_shape_is_exactly_the_pinned_static_advertisement(self):
        client = _client()
        response = client.get("/api/resources/control-capabilities")
        assert response.status_code == 200
        assert response.json() == {
            "enforcement": "observe-only",
            "actions": [
                {"name": name, "user_gated": True, "available": False}
                for name in _CONTROL_ACTION_NAMES
            ],
            "policy_rows": 0,
        }
        assert list(_CONTROL_ACTION_NAMES) == ["alert", "throttle", "kill"]


# ── O3/O4 — the four-surface negative space ────────────────────────────────


class TestFourSurfaceNegativeSpace:
    def test_no_argv_and_no_secret_on_any_surface(
        self, tmp_path, kill_census, caplog
    ):
        """The envelope, the event payload, the log line and the endpoint
        response are scanned for the command's argv shape AND a secret
        marker value — zero occurrences in all four."""
        emitter = _RecordingEmitter()
        guard, _root = make_guard(tmp_path, emitter=emitter)

        # The drive happens INSIDE the capture window so the transition
        # log line is in the scanned surface.
        with caplog.at_level(logging.INFO):
            record = register(guard)
            guard._tick_once()
            assert record.state == STATE_BREACHED
            guard._lane_once()
            assert record.state == STATE_KILLED
        log_text = caplog.text

        # Surface 1 — the ENVELOPE (the bash lane's return string).
        from daemon.services.ram_guard.errors import format_ram_envelope

        envelope = format_ram_envelope(record)

        # Surface 2 — the EVENT payload (the producer's output).
        from daemon.services.ram_guard.errors import build_ram_event_payload

        event = build_ram_event_payload(record)

        # Surface 4 — the ENDPOINT response.
        client = _client()
        client.app.state.ram_guard = guard
        response = client.get("/api/resources/ram-guard")
        endpoint_text = json.dumps(response.json())

        surfaces = (
            ("envelope", envelope),
            ("event", json.dumps(event, default=str)),
            ("log", log_text),
            ("endpoint", endpoint_text),
        )
        for surface_name, surface_text in surfaces:
            assert ARGV_SHAPE not in surface_text, f"argv leaked into {surface_name}"
            assert "SECRET_VAL_7f3a9" not in surface_text, f"argv secret leaked into {surface_name}"
            assert SECRET_MARKER not in surface_text, f"secret marker leaked into {surface_name}"
            assert "sbrk" not in surface_text, f"argv fragment leaked into {surface_name}"

        # The transition log line IS present (the scan is over a real
        # surface, not an empty capture).
        assert "ram-guard:" in log_text

        # The event carries USAGE NUMBERS + FIXED TEXT only: NO nested
        # structure anywhere (a dict/list value would be an env or a
        # member list — exactly the leak class the allow-list forbids).
        for key, value in event.items():
            assert not isinstance(value, (dict, list, tuple)), (
                f"nested structure in the event payload: {key}"
            )
