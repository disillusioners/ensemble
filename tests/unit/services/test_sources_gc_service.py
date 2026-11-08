"""Unit tests for ``SourcesGcService`` (source-gc lane).

Pins P8, P12, P13, P14, P15 at the service layer per
``.agents/shared/planning/source-gc/phase4-plan.md`` §7, each with its
named mutation leg.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

import pytest

from daemon.services.sources_gc_service import SourcesGcService
from daemon.repositories.source.repository import GcCandidate


def _make_repo_mock(*, candidates=(), skipped_unparseable=0, error_rows=0):
    repo = MagicMock()
    repo.gc_find_candidates = MagicMock(return_value=(list(candidates), skipped_unparseable))
    repo.gc_count_error_rows = MagicMock(return_value=error_rows)
    return repo



def _res_deleted(sid):
    r = MagicMock()
    r.deleted = True
    r.dry_run = False
    r.archived_config = True
    r.archived_executions = 0
    r.deleted_mappings = 0
    r.deleted_processed = 0
    r.deleted_executions = 0
    r.skipped_reason = None
    r.error = None
    r.source_id = sid
    return r


def _cand(sid, age_days=20.0):
    return GcCandidate(source_id=sid, name=sid, status="stopped",
                       updated_at="2026-10-18T00:00:00+00:00",
                       age_days=age_days, config_json='{"run_at": "2026-01-01T00:00:00+00:00"}')


# ---------------------------------------------------------------------------
# P13 — kill-switch no-op
# ---------------------------------------------------------------------------

class TestP13KillSwitch:
    def test_disabled_start_logs_and_schedules_no_task(self, caplog):
        repo = _make_repo_mock()
        svc = SourcesGcService(repo, interval_seconds=3600, retention_days=14,
                               batch_cap=25, dry_run=False, enabled=False)
        with caplog.at_level(logging.INFO):
            svc.start()
        assert svc._task is None
        msgs = [r.getMessage() for r in caplog.records]
        assert any("disabled (SERVICES_SOURCES_GC_ENABLED=false)" in m for m in msgs)
        assert not any("started:" in m for m in msgs)

    def test_enabled_start_schedules_task(self, monkeypatch):
        repo = _make_repo_mock()
        svc = SourcesGcService(repo, interval_seconds=3600, retention_days=14,
                               batch_cap=25, dry_run=False, enabled=True)
        # avoid spawning a real task loop
        monkeypatch.setattr(asyncio, "create_task", lambda coro, name=None: (coro.close(), MagicMock())[1])
        svc.start()
        assert svc._task is not None


# ---------------------------------------------------------------------------
# P14 — first-tick-immediate
# ---------------------------------------------------------------------------

class TestP14FirstTickImmediate:
    def test_run_sweeps_before_first_sleep(self, monkeypatch):
        repo = _make_repo_mock(candidates=[_cand("s1")])
        svc = SourcesGcService(repo, interval_seconds=3600, retention_days=14,
                               batch_cap=25, dry_run=True, enabled=True)
        order = []

        async def one_shot_sweep():
            order.append("sweep")
            svc._stopping = True
            return {"ok": True}

        async def fake_sleep(seconds):
            order.append("sleep")
            return None

        monkeypatch.setattr(svc, "sweep_once", one_shot_sweep)
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        asyncio.run(svc._run())
        assert order and order[0] == "sweep", order
        assert "sweep" in order

# P8 — dry-run non-destructiveness + remainder + observed_error_rows in output
# ---------------------------------------------------------------------------

class TestP8DryRun:
    def test_dry_run_writes_nothing_and_reports(self):
        cand = _cand("s1")
        repo = _make_repo_mock(candidates=[cand], skipped_unparseable=2, error_rows=3)
        # gc_find_candidates called twice (capped + probe); both return same
        repo.gc_find_candidates = MagicMock(side_effect=[
            ([cand], 2),   # capped window
            ([cand], 2),   # total probe
        ])
        repo.gc_count_error_rows = MagicMock(return_value=3)
        svc = SourcesGcService(repo, interval_seconds=3600, retention_days=14,
                               batch_cap=25, dry_run=True, enabled=True)
        rep = asyncio.run(svc.sweep_once())
        assert rep["dry_run"] is True
        assert rep["skipped_unparseable"] == 2
        assert rep["observed_error_rows"] == 3
        assert "remainder" in rep
        # gc_archive_and_delete must NEVER be called in dry-run
        assert not hasattr(repo, "gc_archive_and_delete") or not repo.gc_archive_and_delete.called

    def test_dry_run_repo_never_called(self):
        repo = _make_repo_mock(candidates=[])
        repo.gc_find_candidates = MagicMock(return_value=([], 0))
        repo.gc_count_error_rows = MagicMock(return_value=0)
        repo.gc_archive_and_delete = MagicMock()
        svc = SourcesGcService(repo, interval_seconds=3600, retention_days=14,
                               batch_cap=25, dry_run=True, enabled=True)
        asyncio.run(svc.sweep_once())
        repo.gc_archive_and_delete.assert_not_called()


# ---------------------------------------------------------------------------
# P15 — log discipline
# ---------------------------------------------------------------------------

class TestP15LogDiscipline:
    def test_noop_tick_silent(self, caplog):
        repo = _make_repo_mock(candidates=[])
        repo.gc_find_candidates = MagicMock(return_value=([], 0))
        repo.gc_count_error_rows = MagicMock(return_value=0)
        svc = SourcesGcService(repo, interval_seconds=3600, retention_days=14,
                               batch_cap=25, dry_run=False, enabled=True)
        with caplog.at_level(logging.INFO, logger="daemon.services.sources_gc_service"):
            rep = asyncio.run(svc.sweep_once())
        info_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        # no work line on a no-op tick
        assert not any(m.startswith("[SourcesGC] collected=") for m in info_msgs)
        assert not any("first destructive sweep performed" in m for m in info_msgs)
        assert rep["collected"] == 0

    def test_first_destructive_marker_once(self, caplog):
        cands = [_cand("a"), _cand("b")]
        repo = _make_repo_mock()
        # tick1: 2 candidates collected
        repo.gc_find_candidates = MagicMock(side_effect=[
            (cands, 0),   # main find
            ([], 0),      # post-probe
            ([], 0),      # tick2 main find (empty)
            ([], 0),      # tick2 post-probe
        ])
        repo.gc_count_error_rows = MagicMock(return_value=0)
        res = MagicMock()
        res.deleted = True
        res.dry_run = False
        res.archived_config = True
        res.archived_executions = 0
        res.deleted_mappings = 0
        res.deleted_processed = 0
        res.deleted_executions = 0
        res.skipped_reason = None
        res.error = None
        res.source_id = "x"
        repo.gc_archive_and_delete = MagicMock(return_value=res)
        # fix source_id per candidate
        def side_effect(source_id, now, retention_days, dry_run):
            r = MagicMock()
            r.deleted = True
            r.dry_run = False
            r.archived_config = True
            r.archived_executions = 0
            r.deleted_mappings = 0
            r.deleted_processed = 0
            r.deleted_executions = 0
            r.skipped_reason = None
            r.error = None
            r.source_id = source_id
            return r
        repo.gc_archive_and_delete = MagicMock(side_effect=side_effect)

        svc = SourcesGcService(repo, interval_seconds=3600, retention_days=14,
                               batch_cap=25, dry_run=False, enabled=True)
        with caplog.at_level(logging.INFO, logger="daemon.services.sources_gc_service"):
            asyncio.run(svc.sweep_once())
            asyncio.run(svc.sweep_once())
        markers = [r for r in caplog.records if "first destructive sweep performed" in r.getMessage()]
        assert len(markers) == 1, len(markers)

    def test_work_iff_work_log_line(self, caplog):
        cands = [_cand("a")]
        repo = _make_repo_mock()
        repo.gc_find_candidates = MagicMock(side_effect=[
            (cands, 0),
            ([], 0),
        ])
        repo.gc_count_error_rows = MagicMock(return_value=0)
        def side_effect(source_id, now, retention_days, dry_run):
            r = MagicMock()
            r.deleted = True
            r.dry_run = False
            r.archived_config = True
            r.archived_executions = 0
            r.deleted_mappings = 0
            r.deleted_processed = 0
            r.deleted_executions = 0
            r.skipped_reason = None
            r.error = None
            r.source_id = source_id
            return r
        repo.gc_archive_and_delete = MagicMock(side_effect=side_effect)
        svc = SourcesGcService(repo, interval_seconds=3600, retention_days=14,
                               batch_cap=25, dry_run=False, enabled=True)
        with caplog.at_level(logging.INFO, logger="daemon.services.sources_gc_service"):
            asyncio.run(svc.sweep_once())
        msgs = [r.getMessage() for r in caplog.records]
        assert any(m.startswith("[SourcesGC] collected=") for m in msgs)


# ---------------------------------------------------------------------------
# P12 — knob fail-fast (config-level, smoke)
# ---------------------------------------------------------------------------

class TestP12KnobFailFast:
    def test_ge_one_validation(self):
        from pydantic import ValidationError
        from daemon.config import ServicesConfig
        for kwargs in ({"sources_gc_interval_seconds": 0},
                       {"sources_gc_terminal_retention_days": 0},
                       {"sources_gc_batch_cap": 0}):
            try:
                ServicesConfig(**kwargs)
                raise AssertionError(f"accepted invalid {kwargs}")
            except ValidationError:
                pass


# ---------------------------------------------------------------------------
# P18 service-level — (collision handling lives in repo; service surfaces it)
# ---------------------------------------------------------------------------

class TestServiceCollisionSurface:
    def test_collision_row_surfaces_skipped_reason(self):
        cand = _cand("collide")
        repo = _make_repo_mock()
        repo.gc_find_candidates = MagicMock(side_effect=[
            ([cand], 0),
            ([], 0),
        ])
        repo.gc_count_error_rows = MagicMock(return_value=0)
        res = MagicMock()
        res.deleted = False
        res.dry_run = False
        res.archived_config = False
        res.archived_executions = 0
        res.deleted_mappings = 0
        res.deleted_processed = 0
        res.deleted_executions = 0
        res.skipped_reason = "archive_id_collision"
        res.error = "IntegrityError"
        res.source_id = "collide"
        repo.gc_archive_and_delete = MagicMock(return_value=res)
        svc = SourcesGcService(repo, interval_seconds=3600, retention_days=14,
                               batch_cap=25, dry_run=False, enabled=True)
        rep = asyncio.run(svc.sweep_once())
        assert rep["rows"][0]["skipped_reason"] == "archive_id_collision"
        assert rep["collected"] == 0


# ---------------------------------------------------------------------------
# F2 — AM-10 to_thread off-loop contract + P15 marker-latch strengthening
# ---------------------------------------------------------------------------

import threading


class TestToThreadOffLoop:
    """AM-10: BOTH repo calls from the sweep execute OFF the event-loop thread.

    Captures ``threading.get_ident()`` inside the mocked repo calls; the
    sweep runs under ``asyncio.run`` (loop on the test main thread), so a
    captured ident equal to the loop's ident means the call ran ON the
    loop — the exact class v0.27.14 cured and AM-10 forbids.
    Discriminating: replacing either ``asyncio.to_thread`` with a direct
    call REDs (the ident collapses onto the loop's).
    """

    def _run(self, repo, dry_run):
        svc = SourcesGcService(repo, interval_seconds=3600, retention_days=14,
                               batch_cap=25, dry_run=dry_run, enabled=True)
        self._svc = svc
        find_idents = []
        count_idents = []
        row_idents = []
        real_find = repo.gc_find_candidates
        real_count = repo.gc_count_error_rows
        real_del = repo.gc_archive_and_delete

        def find_wrap(*a, **kw):
            find_idents.append(threading.get_ident())
            return real_find(*a, **kw)

        def count_wrap():
            count_idents.append(threading.get_ident())
            return real_count()

        def del_wrap(*a, **kw):
            row_idents.append(threading.get_ident())
            return real_del(*a, **kw)

        repo.gc_find_candidates = find_wrap
        repo.gc_count_error_rows = count_wrap
        repo.gc_archive_and_delete = del_wrap
        svc._find_idents = find_idents
        svc._count_idents = count_idents
        svc._row_call_idents = row_idents
        asyncio.run(svc.sweep_once())
        # the "captured ident" shape: whichever call actually fired
        all_idents = find_idents + count_idents + row_idents
        return all_idents[-1] if all_idents else None

    def test_destructive_path_repo_calls_off_loop(self):
        from daemon.repositories.source.repository import GcCandidate
        cand = GcCandidate(source_id="a", name="a", status="stopped",
                           updated_at="x", age_days=20.0, config_json="{}")
        repo = MagicMock()
        repo.gc_find_candidates = MagicMock(return_value=([cand], 0))
        repo.gc_count_error_rows = MagicMock(return_value=0)
        # post-probe find returns empty (tick drained everything)
        repo.gc_find_candidates.side_effect = [([cand], 0), ([], 0)]
        res = MagicMock()
        res.deleted = True
        res.dry_run = False
        res.archived_config = True
        res.archived_executions = 0
        res.deleted_mappings = 0
        res.deleted_processed = 0
        res.deleted_executions = 0
        res.skipped_reason = None
        res.error = None
        res.source_id = "a"
        repo.gc_archive_and_delete = MagicMock(return_value=res)

        loop_ident = threading.get_ident()
        repo_ident = self._run(repo, dry_run=False)
        assert repo_ident is not None
        assert repo_ident != loop_ident, (
            "repo call ran ON the event-loop thread (AM-10 violation)"
        )
        # The DESTRUCTIVE per-row call must ALSO be off-loop (AM-10 wraps
        # BOTH calls; a single-thread pin could miss a direct destructive
        # call when the find call still runs in a worker).
        row_idents = self._svc._row_call_idents
        assert row_idents, "destructive per-row call never fired"
        assert all(i != loop_ident for i in row_idents), (
            "gc_archive_and_delete ran ON the event-loop thread (AM-10 violation)"
        )

    def test_dry_run_repo_calls_off_loop(self):
        repo = MagicMock()
        repo.gc_find_candidates = MagicMock(return_value=([], 0))
        repo.gc_count_error_rows = MagicMock(return_value=0)
        loop_ident = threading.get_ident()
        repo_ident = self._run(repo, dry_run=True)
        assert repo_ident is not None
        assert repo_ident != loop_ident, (
            "repo call ran ON the event-loop thread (AM-10 violation)"
        )
        # BOTH dry-run calls off-loop (find + count).
        assert self._svc._find_idents, "find call never fired"
        assert all(i != loop_ident for i in self._svc._find_idents)
        assert self._svc._count_idents, "count call never fired"
        assert all(i != loop_ident for i in self._svc._count_idents)


class TestFirstDestructiveMarkerLatch:
    """P15 strengthening: the marker latch must hold across ticks.

    Removing ``self._first_destructive_logged = True`` (or the guard)
    makes the marker emit on EVERY destructive work tick — this test
    asserts the discriminating observable: exactly ONE marker across
    multiple work-carrying ticks. Proved by mutation: latch removal ->
    2 markers -> RED.
    """

    def test_marker_exactly_once_across_three_work_ticks(self, caplog):
        repo = MagicMock()
        repo.gc_count_error_rows = MagicMock(return_value=0)
        # 3 ticks, each carrying work (1 candidate each) + drained probe
        repo.gc_find_candidates = MagicMock(side_effect=[
            ([_cand("a")], 0), ([], 0),
            ([_cand("b")], 0), ([], 0),
            ([_cand("c")], 0), ([], 0),
        ])
        repo.gc_archive_and_delete = MagicMock(side_effect=[
            _res_deleted(sid) for sid in ("a", "b", "c")
        ])
        svc = SourcesGcService(repo, interval_seconds=3600, retention_days=14,
                               batch_cap=25, dry_run=False, enabled=True)
        with caplog.at_level(logging.INFO, logger="daemon.services.sources_gc_service"):
            asyncio.run(svc.sweep_once())
            asyncio.run(svc.sweep_once())
            asyncio.run(svc.sweep_once())
        markers = [r for r in caplog.records
                   if "first destructive sweep performed" in r.getMessage()]
        assert len(markers) == 1, (
            f"marker must emit exactly once; got {len(markers)} "
            "(latch removed or never set)"
        )
