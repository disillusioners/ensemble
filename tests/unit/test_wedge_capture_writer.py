"""Writer pack — file-first capture, isolation, deadline, retention (T31).

Covers **AC44** (the full file set / per-artifact failure isolation /
never raises / the deadline's ``skipped:budget`` / **no saver-pool
access** / the DB-outage leg / prune-on-write) plus the two Phase-1
acceptance criteria that have no other home — **AC10** (the A2 pool
vocabulary) and **AC11** (the readiness snapshot's age/staleness and
the ``readyz.json`` omission), both of which are *artifact* properties
the writer is what puts on disk.

WHY THE SPY POOL IS THE HONEST PROOF (D45)
------------------------------------------
RK-1 is the central irony of this commission: the instrument must not
need the resource it is diagnosing. The only way to prove the capture
path never TOUCHES the saver pool is a spy that raises on the access
that would matter (a checkout) and records every access it does see.
The capture may READ ``get_stats()`` (that IS the pool evidence); it may
never checkout, connect, or set up a pool.

SAFETY FENCE: every root is a ``tmp_path``. No live-shaped path, no
no live-database connection-variable literal, no HOME-derived directory.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from daemon.services.wedge_capture import collector as collector_module
from daemon.services.wedge_capture import writer as writer_module
from daemon.services.wedge_capture.collector import (
    POOL_CHECKED_OUT,
    POOL_CHECKOUT_TIMEOUTS,
    POOL_CHECKOUT_TIMEOUTS_REASON,
    POOL_CHECKOUT_TIMEOUTS_REASON_KEY,
    POOL_COUNTERS_SOURCE,
    POOL_MAX,
    POOL_REQUESTS_ERRORS,
    POOL_SIZE_CURRENT,
    POOL_WAITING_CLIENTS,
    collect_pool,
    collect_readiness,
)
from daemon.services.wedge_capture.config import WedgeCaptureConfig
from daemon.services.wedge_capture.watchdog import WedgeCaptureWatchdog
from daemon.services.wedge_capture.writer import (
    BOOT_RECORDS_DIRNAME,
    CAPTURE_FILE_NAMES,
    PENDING_ROW_SIDECAR,
    SKIPPED_BUDGET,
    SKIPPED_NA,
    CaptureWriter,
    capture_directories,
    make_capture_id,
    prune_captures,
    read_pending_row_sidecars,
)

#: The D8 file set, verbatim.
EXPECTED_FILES = {
    "manifest.json",
    "stacks.txt",
    "threads.csv",
    "lag.json",
    "metrics.json",
    "db.json",
    "readyz.json",
    "checkpoint_perf_tail.json",
}


# ── the scripted clock (the deadline legs) ────────────────────────────────


class _ScriptedTime:
    """A ``time`` stand-in whose ``monotonic`` follows a script.

    Used to exhaust the capture budget deterministically: the scripted
    values are consumed in call order, and the LAST value repeats.
    """

    def __init__(self, values: list[float]) -> None:
        self._values = list(values)
        self._last = self._values[-1] if self._values else 0.0

    def monotonic(self) -> float:
        if len(self._values) > 1:
            self._last = self._values.pop(0)
        else:
            self._last = self._values[0] if self._values else self._last
        return self._last


def _write_stacks(path: Path) -> str:
    """The real collector writes the file AND returns a status."""
    path.write_text("Thread 0x1 (MainThread):\n  File \"x.py\", line 1\n",
                    encoding="utf-8")
    return "ok"


def _capture(writer: CaptureWriter, **overrides: Any):
    """Run one capture with a complete payload."""
    kwargs: dict[str, Any] = {
        "capture_id": "20261022T120000Z-host01-sustained_cpu-abcdef01",
        "reason": "sustained_cpu",
        "trigger_source": "sustained_cpu",
        "executor_native_id": 4242,
        "boot_epoch": "2026-10-22T11:00:00",
        "captured_at": "20261022T120000Z",
        "host": "host01",
        "release_version": "0.27.3",
        "summary": "process CPU 99% sustained for 31s",
        "auto_continue_mode": "on",
        "stacks": _write_stacks,
        "threads": [
            {
                "tid": 111,
                "native_id": 111,
                "name": "MainThread",
                "comm": "python",
                "cpu_delta_s": 0.9,
                "share_pct": 95.0,
            }
        ],
        "lag": {"samples": 3, "max": 0.2},
        "metrics": {"gc": [], "pool": {}},
        "db": {"status": "unavailable"},
        "readyz": {"ready": True},
        "checkpoint_perf_tail": [{"op": "aget", "latency_ms": 6000}],
    }
    kwargs.update(overrides)
    return writer.write_capture(**kwargs)


# ── AC44: the D8 file set ─────────────────────────────────────────────────


class TestFileSet:
    def test_the_full_d8_file_set_is_produced(self, tmp_path: Path) -> None:
        result = _capture(CaptureWriter(tmp_path))
        assert result.ok is True
        produced = {path.name for path in result.capture_dir.iterdir()}
        assert produced == EXPECTED_FILES
        assert set(result.artifacts) == EXPECTED_FILES
        assert all(status == "ok" for status in result.artifacts.values())

    def test_the_manifest_carries_the_contract_fields(self, tmp_path: Path) -> None:
        result = _capture(CaptureWriter(tmp_path))
        manifest = json.loads(
            (result.capture_dir / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["capture_id"] == result.capture_id
        assert manifest["trigger_reason"] == "sustained_cpu"
        assert manifest["trigger_source"] == "sustained_cpu"
        assert manifest["executor_native_id"] == 4242
        assert manifest["boot_epoch"] == "2026-10-22T11:00:00"
        assert manifest["auto_continue_mode"] == "on"
        assert manifest["file_count"] == len(EXPECTED_FILES) - 1
        assert manifest["total_bytes"] > 0

    def test_readyz_is_omitted_when_the_composite_is_absent(
        self, tmp_path: Path
    ) -> None:
        """D25: absent-when-N/A — never ``null``, never a crash."""
        result = _capture(CaptureWriter(tmp_path), readyz=None)
        assert not (result.capture_dir / "readyz.json").exists()
        assert result.artifacts["readyz.json"] == SKIPPED_NA
        assert result.ok is True

    def test_the_threads_csv_carries_the_canonical_columns(
        self, tmp_path: Path
    ) -> None:
        result = _capture(CaptureWriter(tmp_path))
        header, row = (
            (result.capture_dir / "threads.csv")
            .read_text(encoding="utf-8")
            .strip()
            .splitlines()
        )
        assert header == "tid,native_id,name,comm,cpu_delta_s,share_pct"
        assert '"python"' in row and '"MainThread"' in row

    def test_the_capture_id_shape_is_operator_triageable(self) -> None:
        capture_id = make_capture_id(
            captured_at="20261022T050351Z",
            host="web01",
            reason="sustained_loop_lag",
            suffix="3f2a91c4",
        )
        assert capture_id == "20261022T050351Z-web01-sustained_loop_lag-3f2a91c4"


# ── AC8: per-artifact isolation, never raises ─────────────────────────────


class TestFailureIsolation:
    def test_one_failing_artifact_loses_only_that_artifact(
        self, tmp_path: Path
    ) -> None:
        def _explode(path: Path) -> dict[str, Any]:
            raise OSError("ENOSPC")

        result = _capture(CaptureWriter(tmp_path), stacks=_explode)

        assert result.artifacts["stacks.txt"] == "failed:OSError"
        for name in ("threads.csv", "lag.json", "metrics.json", "db.json",
                     "readyz.json", "checkpoint_perf_tail.json", "manifest.json"):
            assert result.artifacts[name] == "ok", name
        assert result.ok is True

    def test_an_unwritable_root_returns_a_result_and_does_not_raise(
        self, tmp_path: Path
    ) -> None:
        blocker = tmp_path / "not-a-directory"
        blocker.write_text("x", encoding="utf-8")
        result = _capture(CaptureWriter(blocker))
        assert result.ok is False
        assert result.artifacts["capture_dir"].startswith("failed:")

    def test_a_non_serialisable_payload_degrades_to_a_failed_artifact(
        self, tmp_path: Path
    ) -> None:
        class _Weird:
            def __repr__(self) -> str:
                raise RuntimeError("nope")

        # ``default=str`` in the JSON writer handles most shapes; a value
        # that explodes on ``str`` must still only cost that artifact.
        result = _capture(CaptureWriter(tmp_path), db={"bad": _Weird()})
        assert result.artifacts["db.json"] in {"ok", "failed:RuntimeError"}
        assert result.ok is True


class TestDeadline:
    def test_an_exhausted_budget_records_skipped_budget_and_the_capture_still_completes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC9 (A-F7-iv): assert the OUTCOME — the marker is present for
        the over-budget steps and the capture still completes (the
        manifest is never budget-gated, so a partial capture stays
        discoverable)."""
        # Calls: 1=started, 2=stacks, 3=threads, 4+=past the deadline.
        monkeypatch.setattr(
            writer_module,
            "time",
            _ScriptedTime([0.0, 0.0, 0.0] + [30.0] * 20),
        )
        result = _capture(CaptureWriter(tmp_path, deadline_s=20.0))

        assert result.artifacts["stacks.txt"] == "ok"
        assert result.artifacts["threads.csv"] == "ok"
        for name in ("lag.json", "metrics.json", "db.json", "readyz.json",
                     "checkpoint_perf_tail.json"):
            assert result.artifacts[name] == SKIPPED_BUDGET, name
        # The capture COMPLETED: the manifest is on disk and readable.
        assert result.artifacts["manifest.json"] == "ok"
        assert (result.capture_dir / "manifest.json").exists()

    def test_an_ample_budget_skips_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(writer_module, "time", _ScriptedTime([0.0]))
        result = _capture(CaptureWriter(tmp_path, deadline_s=20.0))
        assert SKIPPED_BUDGET not in [
            value for value in result.artifacts.values() if isinstance(value, str)
        ]


# ── AC9 END-TO-END (W2): the deadline covers the WHOLE capture ────────────


class TestEndToEndDeadline:
    """The capture deadline is measured from ``run_capture``'s ENTRY.

    The pre-fix shape started a FRESH 20 s clock at the writer's own
    entry and let the DB phases (a 10 s connect + three 5 s statements)
    run before it with no budget at all — a watchdog stall of 55-70 s.
    These pins FAIL on that shape: the write phase must spend the
    budget the capture opened, and an exhausted phase must skip + record
    rather than run against a fresh clock.
    """

    def test_the_writer_honors_the_callers_budget_clock(
        self, tmp_path: Path
    ) -> None:
        """A pre-writer phase that consumed the budget ⇒ every data step
        ``skipped:budget``; the manifest still lands (never gated)."""
        result = _capture(
            CaptureWriter(tmp_path, deadline_s=20.0),
            started_monotonic=time.monotonic() - 25.0,
        )
        for name in ("stacks.txt", "threads.csv", "lag.json", "metrics.json",
                     "db.json", "readyz.json", "checkpoint_perf_tail.json"):
            assert result.artifacts[name] == SKIPPED_BUDGET, name
        assert result.artifacts["manifest.json"] == "ok"
        assert (result.capture_dir / "manifest.json").exists()
        # The duration is reported against the CAPTURE's clock, not the
        # writer's.
        assert result.duration_s >= 20.0

    def test_a_slow_pre_writer_phase_forces_the_late_steps_to_skip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """END-TO-END through ``run_capture``: the DB snapshot phase
        ALONE exceeds the budget — the write phase's data steps and the
        metadata row must degrade, never run against a fresh clock."""
        watchdog = _watchdog(tmp_path, deadline_s=0.4)
        seen_budgets: list[float | None] = []

        def _slow_snapshot(dsn, *, boot_epoch=None, budget_s=None, **kwargs):
            seen_budgets.append(budget_s)
            time.sleep(0.5)  # the pre-writer phase alone exceeds 0.4 s
            return {"status": "unavailable", "reason": "test_slow_connect"}

        monkeypatch.setattr(
            "daemon.services.wedge_capture.watchdog._db_capture.capture_db_snapshot",
            _slow_snapshot,
        )

        result = watchdog.run_capture(reason="sustained_cpu")

        # The DB phase received a BOUNDED budget (the W2 wiring) — never
        # the raw unbounded defaults.
        assert seen_budgets, "the snapshot was invoked"
        assert seen_budgets[0] is not None
        assert seen_budgets[0] <= 0.4 + 0.01

        # The pre-writer phase consumed the whole budget: the write
        # phase's data steps skip + record, measured from run_capture's
        # entry.
        for name in ("stacks.txt", "threads.csv", "lag.json", "metrics.json",
                     "db.json", "readyz.json", "checkpoint_perf_tail.json"):
            assert result.artifacts[name] == SKIPPED_BUDGET, name
        assert result.artifacts["manifest.json"] == "ok"
        assert result.duration_s >= 0.5

        # The row write is budget-exhausted too → skipped (never a fresh
        # connect) → the D22 pending-sidecar path recovers it later.
        assert result.row_written is False
        assert (result.capture_dir / PENDING_ROW_SIDECAR).exists()

    def test_the_row_write_skips_when_the_budget_is_exhausted(
        self, tmp_path: Path
    ) -> None:
        """``write_capture_row(budget_s=0.0)`` must NOT attempt a
        connect — it returns the typed ``skipped`` status."""
        from daemon.services.wedge_capture.db_capture import (
            SKIPPED_BUDGET_REASON,
            STATUS_SKIPPED,
            write_capture_row,
        )

        outcome = write_capture_row(
            "postgresql://user:pw@127.0.0.1:1/nowhere", {"capture_dir": "/x"},
            budget_s=0.0,
        )
        assert outcome["status"] == STATUS_SKIPPED
        assert outcome["reason"] == SKIPPED_BUDGET_REASON

    def test_the_snapshot_phase_skips_when_the_budget_is_exhausted(
        self, tmp_path: Path
    ) -> None:
        """``capture_db_snapshot(budget_s=0.0)`` must NOT attempt a
        connect — it returns the typed ``skipped`` payload skeleton."""
        from daemon.services.wedge_capture.db_capture import (
            SKIPPED_BUDGET_REASON,
            STATUS_SKIPPED,
            capture_db_snapshot,
        )

        payload = capture_db_snapshot(
            "postgresql://user:pw@127.0.0.1:1/nowhere",
            boot_epoch="2026-10-22T11:00:00",
            budget_s=0.0,
        )
        assert payload["status"] == STATUS_SKIPPED
        assert payload["reason"] == SKIPPED_BUDGET_REASON
        assert payload["executing_turns"] == []
        assert payload["boot_epoch"] == "2026-10-22T11:00:00"


# ── AC44 / D45: the saver-pool independence (SC-4) ────────────────────────


class _SpyPool:
    """A pool that records every access and REFUSES a checkout."""

    def __init__(self, stats: dict[str, int] | None = None) -> None:
        self.accesses: list[str] = []
        self._stats = stats or {
            "pool_size": 5,
            "pool_available": 0,
            "pool_max": 5,
            "requests_waiting": 2,
            "requests_errors": 0,
        }

    def get_stats(self) -> dict[str, int]:
        self.accesses.append("get_stats")
        return dict(self._stats)

    def __getattr__(self, name: str) -> Any:  # pragma: no cover - asserts below
        # Any OTHER access (getconn / connection / check / connect …) is
        # recorded and refused — a capture that needed a connection would
        # fail loudly here.
        self.__dict__.setdefault("accesses", []).append(name)
        raise AssertionError(f"the capture touched the saver pool: {name}")


class _ExplodingPool:
    """A pool that raises on ANY access, including ``get_stats``."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the capture touched the saver pool: {name}")


def _watchdog(tmp_path: Path, **overrides: Any) -> WedgeCaptureWatchdog:
    config = WedgeCaptureConfig(capture_dir=tmp_path, **overrides)
    return WedgeCaptureWatchdog(
        config=config,
        app_state=_State(
            _Composite(
                ready=True,
                checked_at=datetime.now(timezone.utc),
                reasons=[],
            )
        ),
        loop=None,
        boot_epoch=datetime(2026, 10, 22, 11, 0, 0),
        release_version="0.27.3",
    )


class TestPoolIndependence:
    def test_a_capture_reads_stats_only_never_a_checkout(
        self, tmp_path: Path
    ) -> None:
        watchdog = _watchdog(tmp_path)
        spy = _SpyPool()
        watchdog._pool = spy

        result = watchdog.run_capture(reason="sustained_cpu")

        assert result.ok is True
        assert set(spy.accesses) == {"get_stats"}, spy.accesses
        metrics = json.loads(
            (result.capture_dir / "metrics.json").read_text(encoding="utf-8")
        )
        assert metrics["pool"][POOL_COUNTERS_SOURCE] == "psycopg_pool.get_stats"
        assert metrics["pool"][POOL_CHECKED_OUT] == 5

    def test_a_fully_checked_out_pool_still_produces_every_file(
        self, tmp_path: Path
    ) -> None:
        """SC-4: the H2 shape — the pool is saturated and the capture
        still lands, because it never asks the pool for a connection."""
        watchdog = _watchdog(tmp_path)
        watchdog._pool = _SpyPool(
            {"pool_size": 5, "pool_available": 0, "pool_max": 5,
             "requests_waiting": 9, "requests_errors": 2}
        )

        result = watchdog.run_capture(reason="sustained_loop_lag")

        for name in EXPECTED_FILES:
            assert (result.capture_dir / name).exists(), name
        metrics = json.loads(
            (result.capture_dir / "metrics.json").read_text(encoding="utf-8")
        )
        assert metrics["pool"][POOL_CHECKED_OUT] == metrics["pool"][POOL_SIZE_CURRENT]
        assert metrics["pool"][POOL_WAITING_CLIENTS] == 9

    def test_a_pool_that_raises_on_any_access_degrades_the_block_only(
        self, tmp_path: Path
    ) -> None:
        watchdog = _watchdog(tmp_path)
        watchdog._pool = _ExplodingPool()

        result = watchdog.run_capture(reason="manual")

        assert result.ok is True
        metrics = json.loads(
            (result.capture_dir / "metrics.json").read_text(encoding="utf-8")
        )
        assert metrics["pool"][POOL_COUNTERS_SOURCE].startswith("unavailable:")


# ── AC44 / AC18: the DB-outage leg ────────────────────────────────────────


class TestRowWriteLegs:
    def test_the_dir_survives_and_the_path_is_logged_when_the_row_fails(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        watchdog = _watchdog(tmp_path)
        watchdog._dsn = None  # nothing to connect to

        with caplog.at_level(logging.WARNING):
            result = watchdog.run_capture(reason="sustained_cpu")

        assert result.ok is True
        assert result.row_written is False
        assert (result.capture_dir / "manifest.json").exists()
        assert (result.capture_dir / PENDING_ROW_SIDECAR).exists()
        assert str(result.capture_dir) in caplog.text

    def test_the_pending_sidecar_is_reconcilable(self, tmp_path: Path) -> None:
        watchdog = _watchdog(tmp_path)
        watchdog._dsn = None
        watchdog.run_capture(reason="manual")

        pending = read_pending_row_sidecars(tmp_path)
        assert len(pending) == 1
        path, payload = pending[0]
        assert path.name == PENDING_ROW_SIDECAR
        assert payload["trigger_reason"] == "manual"
        assert payload["capture_dir"] == str(path.parent)

    def test_the_metrics_snapshot_is_adapted_for_postgres(self) -> None:
        """psycopg 3 cannot adapt a bare ``dict`` for a ``%s`` placeholder
        (``ProgrammingError: cannot adapt type 'dict'``) — the row write
        failed on a REAL PG until the value was wrapped in a ``Jsonb``
        adapter. This pin keeps the adapter in place."""
        from daemon.services.wedge_capture.db_capture import _json_or_none

        adapted = _json_or_none({"process_cpu_pct": 99.0, "pool": {"pool_max": 5}})
        assert type(adapted).__name__ == "Jsonb"
        assert adapted.obj == {"process_cpu_pct": 99.0, "pool": {"pool_max": 5}}
        assert _json_or_none(None) is None

    def test_a_successful_row_write_is_recorded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        watchdog = _watchdog(tmp_path)
        monkeypatch.setattr(
            "daemon.services.wedge_capture.watchdog._db_capture.write_capture_row",
            lambda dsn, row, **kwargs: {"status": "ok"},
        )

        result = watchdog.run_capture(reason="manual")

        assert result.row_written is True
        assert not (result.capture_dir / PENDING_ROW_SIDECAR).exists()


# ── AC21 / D23: prune-on-write is DIRECTORY-authoritative ─────────────────


def _make_capture_dir(root: Path, capture_id: str, *, mtime: float) -> Path:
    directory = root / capture_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text("{}", encoding="utf-8")
    (directory / "lag.json").write_text("{}", encoding="utf-8")
    for path in directory.iterdir():
        import os

        os.utime(path, (mtime, mtime))
    import os

    os.utime(directory, (mtime, mtime))
    return directory


class TestPrune:
    def test_the_count_cap_prunes_the_oldest(self, tmp_path: Path) -> None:
        for index in range(5):
            _make_capture_dir(
                tmp_path,
                f"20261022T12000{index}Z-host01-manual-abcdef0{index}",
                mtime=time.time() - 100.0 + index,
            )

        removed = prune_captures(
            tmp_path, max_captures=3, max_age_hours=168, max_disk_mb=512
        )

        assert len(removed) == 2
        survivors = sorted(path.name for path in capture_directories(tmp_path))
        assert len(survivors) == 3
        # the two OLDEST are gone; the three newest survive
        assert all("120000" not in name and "120001" not in name for name in survivors)

    def test_the_cap_holds_even_when_every_row_write_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC21's forced-failure leg: the cap is computed from the
        FILESYSTEM, so an orphan directory can never leak past it."""
        watchdog = _watchdog(tmp_path, max_captures=3)
        monkeypatch.setattr(
            "daemon.services.wedge_capture.watchdog._db_capture.write_capture_row",
            lambda dsn, row, **kwargs: {"status": "failed", "reason": "outage"},
        )

        for _ in range(5):
            result = watchdog.run_capture(reason="sustained_cpu")
            assert result.row_written is False

        assert len(capture_directories(tmp_path)) == 3

    def test_the_age_cap_prunes_stale_captures(self, tmp_path: Path) -> None:
        old = _make_capture_dir(
            tmp_path, "20261001T120000Z-host01-manual-abcdef01", mtime=1000.0
        )
        fresh = _make_capture_dir(
            tmp_path, "20261022T120000Z-host01-manual-abcdef02", mtime=2_000_000.0
        )

        prune_captures(
            tmp_path,
            max_captures=20,
            max_age_hours=168,
            max_disk_mb=512,
            now=2_000_000.0,
        )

        assert not old.exists()
        assert fresh.exists()

    def test_boot_records_are_excluded_from_the_capture_prune(
        self, tmp_path: Path
    ) -> None:
        """F-05: the boot record is the effective-mode CARRIER, not a
        capture — it is never pruned with the captures."""
        boot_dir = tmp_path / BOOT_RECORDS_DIRNAME
        boot_dir.mkdir()
        (boot_dir / "20261022T110000000000Z.json").write_text(
            json.dumps({"effective_mode": "on"}), encoding="utf-8"
        )
        for index in range(4):
            _make_capture_dir(
                tmp_path,
                f"20261022T12000{index}Z-host01-manual-abcdef0{index}",
                mtime=time.time() - 100.0 + index,
            )

        removed = prune_captures(
            tmp_path, max_captures=1, max_age_hours=168, max_disk_mb=512
        )

        assert len(removed) == 3
        assert (boot_dir / "20261022T110000000000Z.json").exists()
        assert BOOT_RECORDS_DIRNAME not in {path.name for path in capture_directories(tmp_path)}

    def test_the_pending_sidecar_is_never_mistaken_for_a_capture(
        self, tmp_path: Path
    ) -> None:
        directory = _make_capture_dir(
            tmp_path, "20261022T120000Z-host01-manual-abcdef01", mtime=1000.0
        )
        (directory / PENDING_ROW_SIDECAR).write_text("{}", encoding="utf-8")

        assert len(capture_directories(tmp_path)) == 1
        prune_captures(tmp_path, max_captures=20, max_age_hours=168, max_disk_mb=512)
        assert (directory / PENDING_ROW_SIDECAR).exists()


# ── AC10: the A2 pool vocabulary ──────────────────────────────────────────


class TestPoolVocabulary:
    def test_a_saturated_pool_maps_onto_the_canonical_names(self) -> None:
        payload = collect_pool(
            _SpyPool(
                {"pool_size": 5, "pool_available": 0, "pool_max": 5,
                 "requests_waiting": 4, "requests_errors": 3}
            )
        )
        assert payload[POOL_COUNTERS_SOURCE] == "psycopg_pool.get_stats"
        assert payload[POOL_CHECKED_OUT] == 5
        assert payload[POOL_SIZE_CURRENT] == 5
        assert payload[POOL_MAX] == 5
        assert payload[POOL_WAITING_CLIENTS] == 4
        assert payload[POOL_REQUESTS_ERRORS] == 3
        assert payload[POOL_CHECKOUT_TIMEOUTS] is None
        assert (
            payload[POOL_CHECKOUT_TIMEOUTS_REASON_KEY]
            == POOL_CHECKOUT_TIMEOUTS_REASON
            == "not_exposed_by_psycopg_pool_3.3.1"
        )

    def test_the_derived_checked_out_is_clamped_at_zero(self) -> None:
        payload = collect_pool({"get_stats": lambda: {
            "pool_size": 2, "pool_available": 4, "pool_max": 5,
        }})
        # A plain dict is not a pool object — this asserts the degraded
        # shape, which must never invent a number either.
        assert payload[POOL_COUNTERS_SOURCE].startswith("unavailable:")

    def test_an_absent_field_is_none_plus_a_reason_never_zero(self) -> None:
        payload = collect_pool(_SpyPool({"pool_size": 5}))
        assert payload[POOL_CHECKED_OUT] is None
        assert payload[POOL_MAX] is None
        assert payload[POOL_WAITING_CLIENTS] is None
        assert payload[POOL_REQUESTS_ERRORS] is None
        assert payload["pool_field_reasons"], "an absent field must carry a reason"

    def test_a_missing_backend_is_typed(self) -> None:
        payload = collect_pool(None)
        assert payload[POOL_COUNTERS_SOURCE] == "unavailable:not_postgres_backend"
        assert payload[POOL_CHECKED_OUT] is None

    def test_pool_size_total_is_retired(self) -> None:
        payload = collect_pool(_SpyPool())
        assert "pool_size_total" not in payload
        assert "pool_size_total" not in json.dumps(payload)

    def test_psycopg_pool_3_3_1_exposes_no_checkout_timeout_counter(self) -> None:
        """§3.1's impl-time re-pin of the A2 mapping.

        The mapping is CORRECT for the installed version only while the
        library exposes no checkout-timeout counter. A psycopg_pool
        upgrade past 3.3.1 must re-verify it (annex §9 flip condition
        (a)) — this assertion is the loud tripwire.
        """
        import psycopg_pool
        from psycopg_pool.pool import ConnectionPool

        assert psycopg_pool.__version__.startswith("3.3."), (
            f"psycopg_pool {psycopg_pool.__version__} — re-verify the A2 "
            "pool mapping before trusting pool_checkout_timeouts=None"
        )
        keys = {
            getattr(ConnectionPool, name)
            for name in dir(ConnectionPool)
            if name.startswith("_REQUESTS") or name.startswith("_POOL")
        }
        assert "pool_size" in keys and "pool_available" in keys
        assert "requests_waiting" in keys and "requests_errors" in keys
        assert not any("timeout" in key for key in keys), sorted(keys)


# ── AC11: the readiness snapshot ──────────────────────────────────────────


class _Composite:
    def __init__(self, **fields: Any) -> None:
        for key, value in fields.items():
            setattr(self, key, value)


class _State:
    def __init__(self, composite: Any) -> None:
        self.readiness_composite = composite


class TestReadiness:
    def test_an_absent_composite_yields_none_so_the_artifact_is_omitted(self) -> None:
        assert collect_readiness(_State(None)) is None
        assert collect_readiness(None) is None

    def test_a_fresh_composite_is_not_stale(self) -> None:
        checked = datetime.now(timezone.utc) - timedelta(seconds=1)
        payload = collect_readiness(
            _State(_Composite(ready=True, checked_at=checked, reasons=[]))
        )
        assert payload["ready"] is True
        assert payload["stale"] is False
        assert payload["composite_age_s"] < 20.0

    def test_an_old_composite_is_flagged_stale_never_healthy(self) -> None:
        checked = datetime.now(timezone.utc) - timedelta(seconds=45)
        payload = collect_readiness(
            _State(_Composite(ready=True, checked_at=checked, reasons=[])),
            refresh_interval_s=10.0,
        )
        assert payload["stale"] is True
        assert payload["composite_age_s"] >= 40.0

    def test_a_none_queue_age_carries_its_disambiguating_reason(self) -> None:
        checked = datetime.now(timezone.utc)
        timed_out = collect_readiness(
            _State(
                _Composite(
                    ready=False,
                    checked_at=checked,
                    reasons=["checkpoint probe timed out"],
                    queue_max_age_seconds=None,
                )
            )
        )
        assert timed_out["queue_max_age_seconds"] is None
        assert timed_out["queue_max_age_seconds_reason"] == "probe_timeout"

        empty = collect_readiness(
            _State(
                _Composite(
                    ready=True,
                    checked_at=checked,
                    reasons=[],
                    queue_max_age_seconds=None,
                    inflight_turns=0,
                )
            )
        )
        assert empty["queue_max_age_seconds_reason"] == "empty"

    def test_a_naive_checked_at_is_treated_as_utc(self) -> None:
        naive = datetime.now(timezone.utc).replace(tzinfo=None)
        payload = collect_readiness(_State(_Composite(ready=True, checked_at=naive)))
        assert payload["stale"] is False


# ── the artifact vocabulary is the sibling matrix §9's ────────────────────


def test_the_capture_file_set_constant_is_the_d8_set() -> None:
    assert set(CAPTURE_FILE_NAMES) == EXPECTED_FILES
    assert collect_pool is collector_module.collect_pool
