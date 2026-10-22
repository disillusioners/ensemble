"""Read-back pack — list / fetch / status + the MANUAL-CAPTURE contract (T33).

Covers **AC47** (the list shape, the full fetch payload, ``not_found``,
the path-traversal refusal, the unauthorized-agent refusal and the LIST
filesystem fallback) and **AC27 / AC28** — the manual-capture contract
that makes the off-loop design *provable*.

THE TWO REDUNDANT INLINE KILLERS (B2)
-------------------------------------
Assertion **(2)** is the discriminator: the handler must have RETURNED
before the capture completed, observed with an explicit timeout so a
wrong implementation FAILS rather than hangs. An implementation that
runs the capture inside the request handler cannot return the 202 until
the capture is done, so (2) goes RED for it and GREEN only for the
enqueue-to-the-watchdog-thread design.

Assertion **(3)** is the independent second killer: the capture's
manifest must pin ``executor_native_id`` to the WATCHDOG thread's OS
tid. An inline implementation would stamp the request handler's thread
instead.

SAFETY FENCE: every root is a ``tmp_path``. No live-shaped path, no
no live-database connection-variable literal, no HOME-derived directory.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daemon.routers.wedge_capture import router as wedge_capture_router
from daemon.services.wedge_capture.config import WedgeCaptureConfig
from daemon.services.wedge_capture.watchdog import (
    REFUSAL_CAPTURE_BUSY,
    REFUSAL_MANUAL_RATE_LIMITED,
    REFUSAL_WATCHDOG_DISABLED,
    WedgeCaptureWatchdog,
)
from daemon.services.wedge_capture.writer import PENDING_ROW_SIDECAR
from daemon.tools.wedge_capture import create_wedge_capture_tools

CAPTURE_ID = "20261022T120000Z-host01-sustained_cpu-abcdef01"
EPOCH = datetime(2026, 10, 22, 11, 0, 0)


# ── the harness ────────────────────────────────────────────────────────────


def _watchdog(tmp_path: Path, **overrides: Any) -> WedgeCaptureWatchdog:
    """An enabled watchdog whose thresholds cannot fire on their own."""
    config = WedgeCaptureConfig(
        capture_dir=tmp_path,
        sample_interval_s=0.05,
        cpu_pct=1e9,
        cpu_sustain_s=0.0,
        lag_s=1e9,
        lag_sustain_s=0.0,
        thread_concentration_pct=1e9,
        **overrides,
    )
    return WedgeCaptureWatchdog(
        config=config,
        app_state=None,
        loop=None,
        boot_epoch=EPOCH,
        release_version="0.27.3",
    )


class _NoEngineManager:
    """A manager with no engine — a SQLite-free / repo-less deployment."""

    engine = None
    is_write_paused = False


def _app(watchdog: Any, tmp_path: Path) -> FastAPI:
    app = FastAPI()
    app.state.wedge_capture_watchdog = watchdog
    app.state.data_dir = str(tmp_path)
    # The tree's autouse ``_ensure_app_state_manager`` fixture replaces a
    # MISSING/None ``app.state.manager`` with a MagicMock, so this pack
    # installs an explicit manager whose ``engine`` is None — that is the
    # only shape where ``_repository()`` honestly returns None and the
    # filesystem fallback (D12) is exercised.
    app.state.manager = _NoEngineManager()
    app.include_router(wedge_capture_router, prefix="/api")
    return app


def _client(watchdog: Any, tmp_path: Path) -> TestClient:
    return TestClient(_app(watchdog, tmp_path))


def _write_capture(root: Path, capture_id: str = CAPTURE_ID) -> Path:
    """A minimal but complete capture directory on disk."""
    directory = root / capture_id
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {
        "capture_id": capture_id,
        "trigger_reason": "sustained_cpu",
        "trigger_source": "sustained_cpu",
        "captured_at": "20261022T120000Z",
        "boot_epoch": "2026-10-22T11:00:00",
        "host": "host01",
        "summary": "process CPU 99% sustained for 31s",
        "auto_continue_mode": "on",
        "file_count": 2,
        "total_bytes": 42,
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (directory / "lag.json").write_text('{"samples": 3}', encoding="utf-8")
    (directory / PENDING_ROW_SIDECAR).write_text("{}", encoding="utf-8")
    return directory


class _FakeResult:
    def as_dict(self) -> dict[str, Any]:
        return {"capture_id": CAPTURE_ID}


# ── AC47: the list ─────────────────────────────────────────────────────────


class TestList:
    def test_the_list_falls_back_to_the_filesystem(
        self, tmp_path: Path
    ) -> None:
        _write_capture(tmp_path)
        response = _client(_watchdog(tmp_path), tmp_path).get("/api/wedge-captures")
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "filesystem"
        assert body["count"] == 1
        row = body["captures"][0]
        for field in (
            "capture_id", "captured_at", "trigger_reason", "boot_epoch",
            "host", "summary", "auto_continue_mode", "file_count", "total_bytes",
        ):
            assert field in row, field
        assert row["capture_id"] == CAPTURE_ID

    def test_an_unreachable_table_falls_back_too(self, tmp_path: Path) -> None:
        _write_capture(tmp_path)

        class _ExplodingRepo:
            def list_recent(self, limit: int):
                raise RuntimeError("table unreachable")

        watchdog = _watchdog(tmp_path)
        watchdog._repository = _ExplodingRepo()
        body = _client(watchdog, tmp_path).get("/api/wedge-captures").json()
        assert body["source"] == "filesystem"
        assert body["count"] == 1

    def test_an_empty_root_is_an_empty_list_not_an_error(
        self, tmp_path: Path
    ) -> None:
        body = _client(_watchdog(tmp_path), tmp_path).get("/api/wedge-captures").json()
        assert body == {"captures": [], "count": 0, "source": "filesystem"}


# ── AC47: the fetch ────────────────────────────────────────────────────────


class TestFetch:
    def test_the_full_payload_is_returned(self, tmp_path: Path) -> None:
        _write_capture(tmp_path)
        response = _client(_watchdog(tmp_path), tmp_path).get(
            f"/api/wedge-captures/{CAPTURE_ID}"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["capture_id"] == CAPTURE_ID
        assert body["manifest"]["trigger_reason"] == "sustained_cpu"
        assert body["manifest"]["auto_continue_mode"] == "on"
        assert body["artifacts"]["lag.json"]["json"] == {"samples": 3}
        assert body["auto_continue_mode"] == "on"

    def test_the_sidecar_is_never_part_of_the_payload(self, tmp_path: Path) -> None:
        """MR-5: the reconcile sidecar is not capture evidence."""
        _write_capture(tmp_path)
        body = _client(_watchdog(tmp_path), tmp_path).get(
            f"/api/wedge-captures/{CAPTURE_ID}"
        ).json()
        assert PENDING_ROW_SIDECAR not in body["artifacts"]
        assert "stacks.txt" not in body["artifacts"]  # never written here

    def test_an_unknown_id_returns_the_not_found_envelope(
        self, tmp_path: Path
    ) -> None:
        response = _client(_watchdog(tmp_path), tmp_path).get(
            "/api/wedge-captures/20261022T120000Z-host01-manual-aaaaaaaa"
        )
        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "not_found"

    def test_a_malformed_id_is_refused_without_touching_the_filesystem(
        self, tmp_path: Path
    ) -> None:
        # A slash-bearing id never reaches the handler (the router has
        # no such route) — a plain 404, never a file read.
        traversal = _client(_watchdog(tmp_path), tmp_path).get(
            "/api/wedge-captures/..%2F..%2Fetc%2Fpasswd"
        )
        assert traversal.status_code == 404
        assert "passwd" not in traversal.text

        # A ``..`` id DOES reach the handler and is refused by the
        # pattern guard with the house envelope.
        dotted = _client(_watchdog(tmp_path), tmp_path).get(
            "/api/wedge-captures/%2E%2E"
        )
        assert dotted.status_code == 404
        assert dotted.json()["detail"]["error"] == "not_found"

    def test_a_table_row_pointing_outside_the_root_is_refused(
        self, tmp_path: Path
    ) -> None:
        """The resolved-path guard: a row whose ``capture_dir`` escapes
        the capture root must not be served."""
        outside = tmp_path.parent / (tmp_path.name + "-outside")
        outside.mkdir(exist_ok=True)
        (outside / "manifest.json").write_text("{}", encoding="utf-8")

        class _Row:
            capture_dir = str(outside)

        class _Repo:
            def get(self, capture_id: str):
                return _Row()

            def row_to_dict(self, row):  # pragma: no cover - not reached
                return {}

        watchdog = _watchdog(tmp_path)
        watchdog._repository = _Repo()

        response = _client(watchdog, tmp_path).get(f"/api/wedge-captures/{CAPTURE_ID}")
        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "not_found"

    def test_the_status_endpoint_reports_the_effective_mode(
        self, tmp_path: Path
    ) -> None:
        from daemon.services.wedge_capture.boot_record import write_boot_record

        write_boot_record(tmp_path, EPOCH, effective_mode="off", raw_env_value="0")
        body = _client(_watchdog(tmp_path), tmp_path).get(
            "/api/wedge-captures/status"
        ).json()
        assert body["auto_continue_mode"] == "off"
        assert body["enabled"] is True
        assert "pool_size_total" not in json.dumps(body)
        assert body["pool"]["pool_checkout_timeouts"] is None

    def test_the_status_endpoint_without_a_watchdog_is_not_an_error(
        self, tmp_path: Path
    ) -> None:
        body = _client(None, tmp_path).get("/api/wedge-captures/status").json()
        assert body["enabled"] is False
        assert body["auto_continue_mode"] == "unknown"


# ── AC27 (2): THE FIRST INLINE KILLER — returned before completion ────────


class TestManualCaptureReturnsBeforeCompletion:
    def test_the_202_arrives_before_the_capture_completes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An inline implementation CANNOT pass this: it would have to
        finish the capture (which the test withholds) before returning.

        The observation of the capture's START uses an explicit timeout,
        so a wrong implementation FAILS rather than hanging the suite.
        """
        watchdog = _watchdog(tmp_path)
        entered = threading.Event()
        release = threading.Event()
        stamps: dict[str, float] = {}
        calls: list[dict[str, Any]] = []

        def _spy(**kwargs: Any) -> _FakeResult:
            calls.append(kwargs)
            stamps["entered"] = time.monotonic()
            entered.set()
            if not release.wait(10.0):  # pragma: no cover - the test controls it
                raise AssertionError("the test never released the capture")
            stamps["finished"] = time.monotonic()
            return _FakeResult()

        monkeypatch.setattr(watchdog, "run_capture", _spy)
        watchdog.start()
        try:
            response = _client(watchdog, tmp_path).post("/api/wedge-captures/capture")
            stamps["responded"] = time.monotonic()

            assert response.status_code == 202
            body = response.json()
            assert body["status"] == "accepted"
            assert body["enqueued"] is True
            assert body["capture_id"], "the 202 must carry a PRE-ALLOCATED id"
            assert body["capture_dir"].endswith(body["capture_id"])

            # explicit timeout — fail, never hang
            assert entered.wait(5.0), (
                "the watchdog never serviced the manual request — the "
                "enqueue/servicing contract is broken"
            )
            assert "finished" not in stamps, (
                "the capture COMPLETED before the 202 was observed: the "
                "handler ran it inline (RK-38 — this is the defect the "
                "contract exists to forbid)"
            )

            release.set()
            assert entered.is_set()
            deadline = time.monotonic() + 5.0
            while "finished" not in stamps and time.monotonic() < deadline:
                time.sleep(0.02)
            assert "finished" in stamps
        finally:
            release.set()
            watchdog.stop()

        assert stamps["responded"] < stamps["finished"]
        assert calls[0]["trigger_source"] == "manual"
        assert calls[0]["capture_id"] == body["capture_id"]


# ── AC27 (3): THE SECOND KILLER — the executor is the watchdog thread ─────


class TestManualCaptureExecutorIdentity:
    def test_the_manifest_pins_manual_provenance_and_the_watchdog_tid(
        self, tmp_path: Path
    ) -> None:
        watchdog = _watchdog(tmp_path)
        watchdog.start()
        try:
            expected_tid = watchdog._thread.native_id  # noqa: SLF001 - the pin
            response = _client(watchdog, tmp_path).post("/api/wedge-captures/capture")
            assert response.status_code == 202
            body = response.json()
            capture_dir = Path(body["capture_dir"])

            deadline = time.monotonic() + 15.0
            manifest_path = capture_dir / "manifest.json"
            while time.monotonic() < deadline and not manifest_path.exists():
                time.sleep(0.02)
            assert manifest_path.exists(), (
                f"the enqueued capture never landed at {capture_dir}"
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        finally:
            watchdog.stop()

        assert manifest["trigger_source"] == "manual"
        assert manifest["trigger_reason"] == "manual"
        assert manifest["executor_native_id"] == expected_tid, (
            "the capture must execute on the WATCHDOG thread — an inline "
            "implementation would stamp the request handler's thread"
        )
        assert manifest["capture_id"] == body["capture_id"]


# ── AC28: the three typed refusals ────────────────────────────────────────


class TestRefusals:
    def test_the_kill_switch_off_refuses_both_the_method_and_the_route(
        self, tmp_path: Path
    ) -> None:
        watchdog = _watchdog(tmp_path, enabled=False)

        accepted, token, _ = watchdog.enqueue_manual_capture()
        assert accepted is False
        assert token == REFUSAL_WATCHDOG_DISABLED

        response = _client(watchdog, tmp_path).post("/api/wedge-captures/capture")
        assert response.status_code == 503
        assert response.json()["detail"]["error"] == REFUSAL_WATCHDOG_DISABLED

    def test_a_stopped_watchdog_refuses(self, tmp_path: Path) -> None:
        watchdog = _watchdog(tmp_path)  # enabled, never started

        accepted, token, detail = watchdog.enqueue_manual_capture()
        assert accepted is False
        assert token == REFUSAL_WATCHDOG_DISABLED
        assert detail["running"] is False

        response = _client(watchdog, tmp_path).post("/api/wedge-captures/capture")
        assert response.status_code == 503
        assert response.json()["detail"]["error"] == REFUSAL_WATCHDOG_DISABLED

    def test_a_full_slot_refuses_with_capture_busy_and_queues_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """B3: ``put_nowait`` → ``queue.Full`` ⇒ ``capture_busy``. No
        coalescing: the FIRST request survives, the second is refused."""
        watchdog = _watchdog(tmp_path)
        # isolate the SLOT mechanics from thread timing
        monkeypatch.setattr(watchdog, "_is_running", lambda: True)

        first_ok, _, first_detail = watchdog.enqueue_manual_capture()
        assert first_ok is True

        second_ok, token, _ = watchdog.enqueue_manual_capture()
        assert second_ok is False
        assert token == REFUSAL_CAPTURE_BUSY

        # the refused request was NOT coalesced into the accepted one
        queued = watchdog.manual_slot.get_nowait()
        assert queued is not None
        assert queued.capture_id == first_detail["capture_id"]
        assert watchdog.manual_slot.get_nowait() is None

    def test_the_router_returns_409_for_capture_busy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        watchdog = _watchdog(tmp_path)
        monkeypatch.setattr(watchdog, "_is_running", lambda: True)
        assert watchdog.enqueue_manual_capture()[0] is True

        response = _client(watchdog, tmp_path).post("/api/wedge-captures/capture")
        assert response.status_code == 409
        assert response.json()["detail"]["error"] == REFUSAL_CAPTURE_BUSY

    def test_the_third_request_inside_a_minute_is_refused_at_enqueue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """F-16 — the cap applies BEFORE anything is queued."""
        watchdog = _watchdog(tmp_path)
        monkeypatch.setattr(watchdog, "_is_running", lambda: True)

        assert watchdog.enqueue_manual_capture()[0] is True
        watchdog.manual_slot.get_nowait()  # drain: the slot is free again
        assert watchdog.enqueue_manual_capture()[0] is True

        accepted, token, _ = watchdog.enqueue_manual_capture()
        assert accepted is False
        assert token == REFUSAL_MANUAL_RATE_LIMITED
        # drain the SECOND (accepted) request, then prove the third was
        # never queued
        assert watchdog.manual_slot.get_nowait() is not None
        assert watchdog.manual_slot.get_nowait() is None, "nothing may be queued"

    def test_the_router_returns_409_for_manual_rate_limited(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        watchdog = _watchdog(tmp_path)
        monkeypatch.setattr(watchdog, "_is_running", lambda: True)
        assert watchdog.enqueue_manual_capture()[0] is True
        watchdog.manual_slot.get_nowait()
        assert watchdog.enqueue_manual_capture()[0] is True
        watchdog.manual_slot.get_nowait()

        response = _client(watchdog, tmp_path).post("/api/wedge-captures/capture")
        assert response.status_code == 409
        assert response.json()["detail"]["error"] == REFUSAL_MANUAL_RATE_LIMITED


# ── AC30 / AC31: the agent-tool surface ───────────────────────────────────


class _FakeManager:
    def __init__(self, root: Path) -> None:
        class _Config:
            capture_dir = root

        class _Watchdog:
            _config = _Config()

        self._wedge_capture_watchdog = _Watchdog()


def _tools(root: Path, agent_id: str) -> dict[str, Any]:
    tools = create_wedge_capture_tools(
        _FakeManager(root), current_instance_id="iid", agent_id=agent_id
    )
    return {tool.name: tool for tool in tools}


@pytest.mark.asyncio
async def test_an_unauthorized_agent_gets_a_typed_refusal(tmp_path: Path) -> None:
    _write_capture(tmp_path)
    tools = _tools(tmp_path, "coder")
    listed = await tools["wedge_capture_list"].ainvoke({"limit": 10})
    fetched = await tools["wedge_capture_get"].ainvoke({"capture_id": CAPTURE_ID})
    assert "REFUSED" in listed and "not-authorized" in listed
    assert "REFUSED" in fetched and "not-authorized" in fetched


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_id", ["ari", "leader", "maintenancer"])
async def test_the_three_holders_read_the_store(
    tmp_path: Path, agent_id: str
) -> None:
    _write_capture(tmp_path)
    tools = _tools(tmp_path, agent_id)
    listed = json.loads(await tools["wedge_capture_list"].ainvoke({"limit": 10}))
    assert listed["count"] == 1
    fetched = json.loads(
        await tools["wedge_capture_get"].ainvoke({"capture_id": CAPTURE_ID})
    )
    assert fetched["capture_id"] == CAPTURE_ID
    assert PENDING_ROW_SIDECAR not in fetched["artifacts"]


@pytest.mark.asyncio
async def test_the_reads_have_no_side_effect(tmp_path: Path) -> None:
    """AC31: a read writes nothing, deletes nothing, triggers nothing."""
    _write_capture(tmp_path)
    before = {path.name: path.stat().st_mtime for path in tmp_path.rglob("*")}
    tools = _tools(tmp_path, "ari")
    await tools["wedge_capture_list"].ainvoke({"limit": 10})
    await tools["wedge_capture_get"].ainvoke({"capture_id": CAPTURE_ID})
    after = {path.name: path.stat().st_mtime for path in tmp_path.rglob("*")}
    assert before == after


@pytest.mark.asyncio
async def test_a_malformed_id_is_refused_by_the_tool(tmp_path: Path) -> None:
    tools = _tools(tmp_path, "ari")
    result = await tools["wedge_capture_get"].ainvoke({"capture_id": "../../etc/passwd"})
    assert "REFUSED" in result and "not-found" in result
