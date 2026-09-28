"""U7+S15 wave-3 regression pins (2026-09-28).

Wave-3 incident: a 6h5m mission episode (mission 1034286a) where the
mission-live guard's 6h backstop was anchored to ``task_completed_at``
fired 73s pre-terminal on a LIVE row, consuming the exactly-once
notify slot with a stale ``Diagnosis lanes running`` payload. The
fix has two parts:

* **U7** — re-anchor the backstop to **tree activity** (the freshest
  ``last_activity_at`` across the permanent lineage, computed during
  the existing legs (b)/(c) tree walk). Live trees hold the row;
  genuinely-quiet trees fire.
* **S15** — fix the ``notify_work_watchers`` claim-before-enqueue
  hazard with per-watch exception handling + add_watch UPSERT
  compensation. Pre-S15 an enqueue throw silently dropped every
  claimed row whose enqueue hadn't completed.

This pack pins the wave-3 shape:

* W1: 6h+ episode with LIVE tree (member active 36s ago) → backstop
  HOLDS (the U7 tick-85/86 discrimination). Asserts no fire,
  watcher row preserved.
* W2: genuinely-quiet >6h tree → backstop FIRES with the
  ``orphan_released`` token (the new observability counter).
* W3: true terminal after a held window → delivery via the sweep
  with FRESH payload (resolver-fallback at fire time, NOT a stale
  cached value from the hold time).
* W4: S15 — enqueue-throw on claim-delivered row → compensation
  UPSERT re-inserts the dropped row; the next sweep tick can re-fire.
* W5: hook (b) external-seat delivery ~1s on COMPLETED flip incl.
  ``no_job`` shape (lifecycle-COMPLETED carrier from
  ``child_reports.py``).
* W6: negative control — IDLE tree member → leg (b)/(c) still
  counts as live (the IDLE canonicalizes-to-live resolver
  contract).

Each pin focuses on ONE observable invariant; the W5b follow-up
(real-seat acceptance suite) authors the natural-path integration
shape separately.

Size rationale (TIDIER, 2026-09-28): the pack is intentionally one
file — six wave-3 regression pins share the engine + fixture
scaffold and the all-terminal discriminator; per-pin extraction
would force a six-way helper fork with no test-clarity win.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlmodel import SQLModel

from daemon.repositories.instance.repository import (
    SQLModelInstanceRepository,
)
from daemon.repositories.instance.models import Instance
from daemon.repositories.job_queue.watcher_repository import (
    JobWatcherRepository,
)
from daemon.repositories.task.repository import TaskRepository
from daemon.services import mission_live_guard as _mlg
from daemon.services.job_queue_service import JobQueueService
from daemon.services.mission_live_guard import (
    MissionLiveVerdict,
    evaluate_mission_live,
)
from daemon.services.timestamps import now_utc_naive
from daemon.services.work_resolver import WorkRecord, WorkResolverService


# ─────────────────────────────────────────────────────────────────────────────
# Engine + fixture helpers (file-local; mirror tests/job_queue/test_u1_*.py
# shape so each pin exercises a real repository + bridge the helpers from
# mission_live_guard through to a real JQS notify chain).
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def u7_engine(tmp_path):
    db_path = tmp_path / "u7_wave3.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    yield eng


@pytest.fixture
def components(u7_engine):
    """Bundle mirroring test_u1_slice_commission_pins — minimum surface
    the wave-3 pins need: repository, watcher_repo, instance_repo,
    resolver, instance_manager mock carrying enqueue_message,
    JobQueueService wired."""
    watcher_repo = JobWatcherRepository(u7_engine)
    task_repo = TaskRepository(u7_engine)
    instance_repo = SQLModelInstanceRepository(u7_engine)
    resolver = WorkResolverService(task_repo, _NoOpJobRepo(), instance_repo)
    instance_manager = MagicMock()
    instance_manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-u7-wave3")
    )
    instance_manager._instance_repository = instance_repo
    instance_manager._task_repo = task_repo

    jqs = JobQueueService(
        repository=_NoOpJobRepo(),
        lock_manager=_NoOpLockManager(),
        queue_repo=_NoOpQueueRepo(),
        instance_manager=instance_manager,
    )
    jqs.set_watcher_repo(watcher_repo)
    jqs.set_work_resolver(resolver)
    return {
        "engine": u7_engine,
        "watcher_repo": watcher_repo,
        "task_repo": task_repo,
        "instance_repo": instance_repo,
        "resolver": resolver,
        "instance_manager": instance_manager,
        "jqs": jqs,
    }


# ── Stand-in repos (test-only; same shape as u1_slice fixtures) ──


class _NoOpJobRepo:
    def get(self, _job_id):
        return None

    def __getattr__(self, name):
        raise AttributeError(f"_NoOpJobRepo: unknown attribute {name!r}")


class _NoOpLockManager:
    def __getattr__(self, name):
        raise AttributeError(f"_NoOpLockManager: unknown attribute {name!r}")


class _NoOpQueueRepo:
    def __getattr__(self, name):
        raise AttributeError(f"_NoOpQueueRepo: unknown attribute {name!r}")


def _insert_tree_member(
    engine,
    *,
    instance_id: str,
    status: str = "running",
    parent_id: str | None = None,
    last_activity_at: datetime | None = None,
    project_id: str = "test-project",
) -> None:
    """Insert (or update) an instance row carrying
    ``last_activity_at`` for the U7 anchor.

    Mirrors ``tests/job_queue/test_mission_live_guard._insert_instance``
    (the file-local helper there): the SQLite storage round-trips
    ``last_activity_at`` as ISO strings (the ``DateTime`` column
    accepts only ``datetime`` at the Python ORM layer; the test
    fixture writes through a raw ``text()`` INSERT so the ``bind
    param`` machinery formats datetime → ISO)."""
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    activity_iso = (
        last_activity_at.isoformat()
        if last_activity_at is not None
        else None
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO instances
                    (instance_id, agent_id, agent_dir, status, project_id,
                     created_at, updated_at, last_activity_at, version,
                     parent_id)
                VALUES
                    (:instance_id, :agent_id, :agent_dir, :status,
                     :project_id, :created_at, :updated_at,
                     :last_activity_at, 1, :parent_id)
                """
            ),
            {
                "instance_id": instance_id,
                "agent_id": "developer",
                "agent_dir": "/tmp/agents/developer",
                "status": status,
                "project_id": project_id,
                "created_at": now_iso,
                "updated_at": now_iso,
                "last_activity_at": activity_iso,
                "parent_id": parent_id,
            },
        )


def _add_watch(
    engine,
    *,
    work_id: str,
    instance_id: str,
    watch_events: list[str],
) -> None:
    """Insert a JobWatcher row. The watcher's ``instance_id`` has a
    FK to ``instances.instance_id`` so the row must exist in the
    instances table first (auto-seeds a minimal stub here for
    call-site convenience)."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        # Stub the watcher instance — minimal rows, just enough to
        # satisfy the FK constraint. Production code paths (where
        # the watcher is a real running instance) supply a
        # pre-existing row; the stub is a test-only convenience so
        # the test does not have to coordinate the seeder for
        # every watch row.
        conn.execute(
            text(
                """
                INSERT OR IGNORE INTO instances
                    (instance_id, agent_id, agent_dir, status,
                     project_id, created_at, updated_at, version,
                     parent_id)
                VALUES
                    (:instance_id, :agent_id, :agent_dir, :status,
                     :project_id, :created_at, :updated_at, 1,
                     :parent_id)
                """
            ),
            {
                "instance_id": instance_id,
                "agent_id": "watcher",
                "agent_dir": "/tmp/agents/watcher",
                "status": "running",
                "project_id": "test-project",
                "created_at": now_iso,
                "updated_at": now_iso,
                "parent_id": None,
            },
        )
        # Now insert the watcher row.
        conn.execute(
            text(
                """
                INSERT INTO job_watchers
                    (watch_id, job_id, instance_id, watch_events,
                     created_at)
                VALUES
                    (:watch_id, :job_id, :instance_id,
                     :watch_events, :created_at)
                """
            ),
            {
                "watch_id": str(uuid4()),
                "job_id": work_id,
                "instance_id": instance_id,
                "watch_events": json.dumps(watch_events),
                "created_at": now_iso,
            },
        )


def _patch_resolver_terminal(
    resolver,
    *,
    work_id: str,
    instance_id: str,
    result_summary: str | None = "U7_DELIVERED_FRESH_AT_FIRE_TIME",
) -> Any:
    """Return a real WorkRecord with ``status='completed'`` for the
    natural notify path. ``result_summary`` represents the FRESH
    payload cached at fire time — S15 verification relies on the
    resolver-fallback inside ``notify_work_watchers`` to surface
    this content (NOT any stale value snapshotted at hold time)."""
    record = WorkRecord(
        work_id=work_id,
        kind="report",
        status="completed",
        instance_id=instance_id,
        project_id="test-project",
        agent_id="developer",
        result_summary=result_summary,
        error=None,
        created_at=datetime.now(timezone.utc),
        job_type=None,
        mission_liveness=None,
    )
    original = resolver.resolve_work
    resolver.resolve_work = MagicMock(return_value=record)
    return original


# ─────────────────────────────────────────────────────────────────────────────
# W1 — 6h+ episode, LIVE tree → backstop HOLDS
# ─────────────────────────────────────────────────────────────────────────────


class TestW1LiveTreeHoldsBackstop:
    """W1 (2026-09-28) — wave-3 evidence replay.

    The tick-85/86 discrimination: a 6h+ mission episode whose tree
    shows recent activity (member active 36s ago — analog: a leader
    mid-LLM call) MUST hold the row even though the work settled
    hours ago. Pre-U7 the ``task_completed_at`` anchor would have
    fired the backstop in this exact shape; post-U7 the freshest
    ``last_activity_at`` anchor keeps the row in defer until true
    terminal."""

    @pytest.mark.asyncio
    async def test_w1_backstop_holds_live_tree_36s_activity(
        self, components,
    ):
        engine = components["engine"]
        repo = components["instance_repo"]

        # Tree shape: root (settled) + descendant (active 36s ago)
        recent = now_utc_naive() - timedelta(seconds=36)
        _insert_tree_member(
            engine, instance_id="root-w1",
            status="completed",
            last_activity_at=recent,
        )
        _insert_tree_member(
            engine, instance_id="desc-w1",
            status="running",  # live descendant — leg (b) holds
            parent_id="root-w1",
            last_activity_at=recent,
        )

        verdict = await evaluate_mission_live(
            instance_repository=repo,
            instance_id="root-w1",
            bus_pending_count=None,
        )

        assert verdict.live, (
            "W1: live tree MUST hold the row even with a stale "
            "task_completed_at anchor — U7 re-anchored the backstop "
            "to tree activity (the freshest last_activity_at across "
            "the permanent lineage)"
        )
        assert not verdict.timed_out, (
            "W1: backstop MUST NOT fire on a live tree — the wave-3 "
            "evidence replay"
        )

    @pytest.mark.asyncio
    async def test_w1_idle_descendant_still_counts_live(
        self, components,
    ):
        """W6 — negative-control sibling: an IDLE descendant still
        canonicalizes-to-live per the resolver's IDLE → ``processing``
        mapping. IDLE is NOT in ``TERMINAL_INSTANCE_STATUSES`` so leg
        (b) holds.

        Per the mission-resolver contract (and the
        ``TERMINAL_INSTANCE_STATUSES`` import in mission_live_guard),
        IDLE counts LIVE for the defer-window decision."""
        engine = components["engine"]
        repo = components["instance_repo"]

        _insert_tree_member(
            engine, instance_id="root-w6",
            status="completed",
            last_activity_at=now_utc_naive() - timedelta(seconds=10),
        )
        _insert_tree_member(
            engine, instance_id="idle-w6",
            status="idle",  # negative control — non-terminal
            parent_id="root-w6",
            last_activity_at=now_utc_naive() - timedelta(seconds=10),
        )

        verdict = await evaluate_mission_live(
            instance_repository=repo,
            instance_id="root-w6",
            bus_pending_count=None,
        )

        assert verdict.live
        assert not verdict.timed_out


# ─────────────────────────────────────────────────────────────────────────────
# W2 — genuinely-quiet >6h tree → backstop FIRES with orphan_released
# ─────────────────────────────────────────────────────────────────────────────


class TestW2QuietTreeFiresBackstop:
    """W2 (FIXBACK, 2026-09-28) — the legitimate zombie-backstop path.

    FIXBACK (council verdict, fixback cycle 1): the backstop's ONLY
    legitimate use is the zombie-break on a NON-TERMINAL tree that has
    gone quiet (≥6h since the freshest ``last_activity_at``). The
    predecessor's ``test_w2_backstop_fires_quiet_tree_with_orphan_released``
    pinned an all-terminal shape — under the FIXBACK that shape
    finalizes immediately via the natural path and emits
    ``orphan_released=0`` (the anchor plays no role on all-terminal
    trees). The FIXBACK W2 reshapes the fixture to a NON-TERMINAL
    descendant + stale anchor so the assertions pin the LEGITIMATE
    zombie-break path.

    The observability emissions (``timed_out=True`` →
    ``orphan_released`` counter +1 + ``ORPHAN_RELEASED`` log token)
    fire IFF the verdict carries ``timed_out=True`` — and under the
    FIXBACK that flag is reserved for the non-terminal-stale shape.
    """

    @pytest.mark.asyncio
    async def test_w2_backstop_fires_quiet_tree_with_orphan_released(
        self, components,
    ):
        engine = components["engine"]
        repo = components["instance_repo"]
        jqs = components["jqs"]

        quiet_time = now_utc_naive() - timedelta(
            seconds=_mlg.MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS + 300,
        )
        work_id = f"wid-w2-{uuid4().hex[:8]}"
        # FIXBACK: root terminal + descendant NON-TERMINAL — the
        # tree LOOKS live (descendant is non-terminal) but every
        # observed activity is older than the window. That is the
        # only shape on which the backstop now fires.
        root_instance_id = "root-w2-quiet"
        descendant_instance_id = "desc-w2-zombie"
        watcher_id = "watcher-w2-quiet"

        _insert_tree_member(
            engine, instance_id=root_instance_id,
            status="completed",
            last_activity_at=quiet_time,
        )
        _insert_tree_member(
            engine, instance_id=descendant_instance_id,
            status="waiting_children",  # NON-terminal
            parent_id=root_instance_id,
            last_activity_at=quiet_time,
        )
        _add_watch(
            engine, work_id=work_id,
            instance_id=watcher_id,
            watch_events=["mission_terminal"],
        )

        # Patch the resolver to return a terminal work record so the
        # sweep's ``_work_status_is_terminal`` check passes.
        original = _patch_resolver_terminal(
            components["resolver"],
            work_id=work_id,
            instance_id=root_instance_id,
        )
        try:
            result = (
                await jqs.reconcile_held_watches_for_instance(
                    instance_id=None,
                )
            )
        finally:
            components["resolver"].resolve_work = original

        # W2 invariants: orphan_released counter +1 (true zombie:
        # non-terminal + stale anchor → backstop fires), watcher row
        # CAS-claimed, exactly-once delivered (enqueue_message called).
        assert result["orphan_released"] == 1, (
            f"FIXBACK W2: a non-terminal tree with every activity "
            f"older than the window is a TRUE zombie — the backstop "
            f"MUST fire orphan_released=1 via the helper's return-"
            f"key counter; got result={result}"
        )
        assert result["fired"] == 1
        assert len(
            components["watcher_repo"].get_watchers_for_job(work_id),
        ) == 0, (
            "W2: the orphan-released row MUST be CAS-claimed and "
            "removed from the DB after delivery"
        )

    def test_w2_mission_live_verdict_carries_timed_out(self, components):
        """W2 sentinel — the verdict itself must carry ``timed_out``
        so the helper can branch on it. Directly exercises the guard
        with the FIXBACK zombie shape: NON-TERMINAL descendant +
        every ``last_activity_at`` older than the window."""
        engine = components["engine"]
        repo = components["instance_repo"]

        quiet_time = now_utc_naive() - timedelta(
            seconds=_mlg.MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS + 300,
        )
        _insert_tree_member(
            engine, instance_id="root-w2-sentinel",
            status="completed",
            last_activity_at=quiet_time,
        )
        _insert_tree_member(
            engine, instance_id="desc-w2-sentinel",
            status="waiting_children",  # FIXBACK: non-terminal
            parent_id="root-w2-sentinel",
            last_activity_at=quiet_time,
        )

        async def _drive():
            return await evaluate_mission_live(
                instance_repository=repo,
                instance_id="root-w2-sentinel",
                bus_pending_count=None,
            )

        verdict = asyncio.run(_drive())
        assert isinstance(verdict, MissionLiveVerdict)
        assert not verdict.live
        assert verdict.timed_out
        assert not verdict.error

    @pytest.mark.asyncio
    async def test_w2_minor2_caplog_pin_orphan_released_reaches_sink(
        self, components, caplog,
    ):
        """MINOR-2 (FIXBACK, 2026-09-28) — the caplog pin.

        The ORPHAN_RELEASED log token is the operator-grep identity for
        true zombie-backstop fires (non-terminal + stale anchor).
        Round-0 missed exactly this assertion: the format string at
        ``job_queue_service.py:971-984`` had 4 ``%`` placeholders but
        only 3 args, so the warning raised ``TypeError`` BEFORE it
        could reach the sink — the counter incremented (orphan_released
        += 1) but the log line never landed, and the caplog assertion
        was missing entirely. This test pins:

        * the format op executes without raising (the format-string
          arity fix);
        * the resulting log record carries the canonical
          ``ORPHAN_RELEASED`` prefix the operator-grep workflow is
          bound to;
        * the warning reaches the log sink (``caplog.records``).
        """
        caplog.set_level(
            logging.WARNING,
            logger="daemon.services.job_queue_service",
        )
        engine = components["engine"]
        jqs = components["jqs"]

        quiet_time = now_utc_naive() - timedelta(
            seconds=_mlg.MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS + 300,
        )
        # True zombie shape — root terminal + non-terminal descendant.
        _insert_tree_member(
            engine, instance_id="root-w2-caplog",
            status="completed",
            last_activity_at=quiet_time,
        )
        _insert_tree_member(
            engine, instance_id="desc-w2-caplog",
            status="waiting_children",
            parent_id="root-w2-caplog",
            last_activity_at=quiet_time,
        )

        work_id = f"wid-w2-caplog-{uuid4().hex[:8]}"
        watcher_id = "watcher-w2-caplog"
        _add_watch(
            engine, work_id=work_id,
            instance_id=watcher_id,
            watch_events=["mission_terminal"],
        )

        original = _patch_resolver_terminal(
            components["resolver"],
            work_id=work_id,
            instance_id="root-w2-caplog",
        )
        try:
            result = (
                await jqs.reconcile_held_watches_for_instance(
                    instance_id=None,
                )
            )
        finally:
            components["resolver"].resolve_work = original

        # Counter incremented → backstop fired:
        assert result["orphan_released"] == 1, (
            f"MINOR-2: orphan_released counter MUST increment "
            f"(backstop fired); got result={result}"
        )

        # Caplog pin: at least one WARNING with the ORPHAN_RELEASED
        # prefix reached the sink. The rename "held_since_swEEP_TICK"
        # → "window_s" is operator-visible too — assert the renamed
        # token is the one present (round-0 had the miscapitalized
        # token; this is the discriminant that proves the format
        # string is the FIXBACK one, not a leftover copy).
        matching = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and "ORPHAN_RELEASED" in r.getMessage()
        ]
        assert matching, (
            f"MINOR-2: the ORPHAN_RELEASED WARNING MUST reach the "
            f"log sink — round-0 the format-string arity mismatch "
            f"raised TypeError before the warning landed. caplog "
            f"saw: {[r.getMessage()[:80] for r in caplog.records]}"
        )
        # The misnamed token must NOT be present (rename pin).
        assert not any(
            "held_since_swEEP_TICK" in r.getMessage()
            for r in caplog.records
        ), (
            "MINOR-2: the FIXBACK renamed ``held_since_swEEP_TICK`` "
            "→ ``window_s``; the old token must not appear in any "
            "ORPHAN_RELEASED log line (operator grep is bound to "
            "window_s)."
        )
        # And the new token MUST be present.
        assert any(
            "window_s=" in r.getMessage()
            for r in caplog.records
        ), (
            "MINOR-2: the FIXBACK renamed token ``window_s=`` MUST "
            "appear in the ORPHAN_RELEASED log line."
        )


# ─────────────────────────────────────────────────────────────────────────────
# W3 — true terminal after held window → delivery with FRESH payload
# ─────────────────────────────────────────────────────────────────────────────


class TestW3FreshPayloadAtFireTime:
    """W3 (2026-09-28) — true terminal after a held window.

    The wave-3 stale-payload class: pre-S15 the sweep re-fired with
    whatever ``result_summary`` was cached at hold time (a 6h-old
    snapshot of the receipt's checkpoint). S15 changes the contract:
    the sweep re-fetches ``work_record.result_summary`` at fire time
    (resolver-fallback inside ``notify_work_watchers``), so the
    delivered envelope carries the CURRENT state at emission."""

    @pytest.mark.asyncio
    async def test_w3_delivered_envelope_carries_fresh_payload(
        self, components,
    ):
        engine = components["engine"]
        repo = components["instance_repo"]
        watcher_repo = components["watcher_repo"]
        instance_manager = components["instance_manager"]
        jqs = components["jqs"]
        resolver = components["resolver"]

        # Tree: settled root + settled descendant (genuinely quiet
        # so the backstop fires).
        quiet_time = now_utc_naive() - timedelta(
            seconds=_mlg.MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS + 300,
        )
        _insert_tree_member(
            engine, instance_id="root-w3",
            status="completed",
            last_activity_at=quiet_time,
        )

        work_id = f"wid-w3-{uuid4().hex[:8]}"
        watcher_id = "watcher-w3"

        _add_watch(
            engine, work_id=work_id,
            instance_id=watcher_id,
            watch_events=["mission_terminal"],
        )

        # The FRESH sentinel — distinct from any stale text. The
        # sweep must surface this in the delivered envelope via
        # the resolver-fallback.
        original = _patch_resolver_terminal(
            resolver,
            work_id=work_id,
            instance_id="root-w3",
            result_summary="W3_FRESH_PAYLOAD_AT_FIRE_TIME_2026_09_28",
        )
        try:
            result = await jqs.reconcile_held_watches_for_instance(
                instance_id=None,
            )
        finally:
            resolver.resolve_work = original

        assert result["fired"] == 1
        # Examine the delivered envelope via the
        # ``enqueue_message`` mock.
        delivered = instance_manager.enqueue_message.await_args_list
        assert len(delivered) == 1, (
            f"W3: expected exactly ONE delivery (the orphan-release), "
            f"got {len(delivered)}"
        )
        delivered_message = delivered[0].kwargs["message"]
        assert "W3_FRESH_PAYLOAD_AT_FIRE_TIME_2026_09_28" in delivered_message, (
            "W3: the delivered envelope MUST carry the fresh payload "
            "fetched at fire time — NOT any stale value snapshotted at "
            "the time the row was inserted"
        )
        # Row CAS-claimed exactly-once.
        assert len(watcher_repo.get_watchers_for_job(work_id)) == 0


# ─────────────────────────────────────────────────────────────────────────────
# W4 — S15: enqueue-throw after claim → compensation UPSERT re-inserts
# ─────────────────────────────────────────────────────────────────────────────


class TestW4S15CompensationRecoversDroppedRows:
    """W4 (2026-09-28) — S15 hazard closure.

    Pre-S15 an enqueue throw mid-loop silently DROPPED every claimed
    row whose enqueue hadn't completed (the outer ``except Exception``
    caught, returned 0, but the CAS-claim DELETE was already
    committed). The fix: per-watch try/except, accumulate failed
    watchers, run ``add_watch`` UPSERT compensation AFTER the loop.
    Success path: exactly-once (the CAS remains the only transition).
    Failure path: at-least-once via compensation — the next sweep
    tick OR a future terminal re-fire can deliver."""

    @pytest.mark.asyncio
    async def test_w4_s15_enqueue_throw_preserves_row_via_compensation(
        self, components,
    ):
        engine = components["engine"]
        repo = components["instance_repo"]
        watcher_repo = components["watcher_repo"]
        instance_manager = components["instance_manager"]
        resolver = components["resolver"]

        quiet_time = now_utc_naive() - timedelta(
            seconds=_mlg.MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS + 300,
        )
        _insert_tree_member(
            engine, instance_id="root-w4",
            status="completed",
            last_activity_at=quiet_time,
        )

        work_id = f"wid-w4-{uuid4().hex[:8]}"
        watcher_id = "watcher-w4"

        _add_watch(
            engine, work_id=work_id,
            instance_id=watcher_id,
            watch_events=["mission_terminal"],
        )

        # Inject a transient enqueue failure on the only watcher.
        instance_manager.enqueue_message = AsyncMock(
            side_effect=RuntimeError("simulated transport hiccup"),
        )

        original = _patch_resolver_terminal(
            resolver,
            work_id=work_id,
            instance_id="root-w4",
        )
        try:
            # Direct call to notify_work_watchers (the S15 fix lives
            # here). The hook layer translates orphan-released into a
            # fire via this path.
            from daemon.services.work_notifier import notify_work_watchers
            notified = await notify_work_watchers(
                work_id=work_id,
                status="completed",
                instance_manager=instance_manager,
                work_resolver=resolver,
                watcher_repo=watcher_repo,
                error=None,
            )
        finally:
            resolver.resolve_work = original

        # W4 invariants: notify returned 0 (no successful deliveries),
        # BUT the row is BACK in the DB (compensation UPSERT
        # re-inserted it after the loop).
        assert notified == 0, (
            "W4: the throw path must NOT silently claim success — "
            "the function correctly reports 0 deliveries"
        )
        remaining = watcher_repo.get_watchers_for_job(work_id)
        assert len(remaining) == 1, (
            f"W4: S15 compensation MUST re-insert the claimed-and-"
            f"dropped watcher row so the next sweep tick (or future "
            f"terminal re-fire) can deliver. Pre-S15 this row was "
            f"permanently lost; the compensation UPSERT is the "
            f"at-least-once recovery. Got remaining={remaining}"
        )
        # Compensation restored the watch_events list verbatim —
        # the next sweep must see the same opt-ins.
        survivor = remaining[0]
        assert survivor.instance_id == watcher_id
        assert "mission_terminal" in (survivor.watch_events or [])

    @pytest.mark.asyncio
    async def test_w4_s15_partial_failure_compensates_only_failed(
        self, components,
    ):
        """W4 partial-failure sibling: 2 watchers, the first enqueue
        succeeds, the second throws. Pre-S15 the row for the second
        watcher was silently lost AND the function returned a partial
        count (it never reached the second one because the throw
        propagated to the outer except which returned 0). S15: first
        watcher notified, second watched is COMPENSATED (BACK in DB
        via add_watch UPSERT), the function returns 1 (only the
        successful delivery)."""
        engine = components["engine"]
        repo = components["instance_repo"]
        watcher_repo = components["watcher_repo"]
        instance_manager = components["instance_manager"]
        resolver = components["resolver"]

        quiet_time = now_utc_naive() - timedelta(
            seconds=_mlg.MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS + 300,
        )
        _insert_tree_member(
            engine, instance_id="root-w4b",
            status="completed",
            last_activity_at=quiet_time,
        )

        work_id = f"wid-w4b-{uuid4().hex[:8]}"
        watcher_a = "watcher-w4b-success"
        watcher_b = "watcher-w4b-fail"
        _add_watch(
            engine, work_id=work_id,
            instance_id=watcher_a,
            watch_events=["mission_terminal"],
        )
        _add_watch(
            engine, work_id=work_id,
            instance_id=watcher_b,
            watch_events=["mission_terminal"],
        )

        # Enqueue succeeds for watcher_a, throws for watcher_b.
        # Use a counter so the second call throws.
        _call_count = {"n": 0}

        async def _flaky_enqueue(**_kwargs):
            _call_count["n"] += 1
            if _call_count["n"] == 1:
                return MagicMock(message_id="msg-success")
            raise RuntimeError("simulated mid-loop hiccup")

        instance_manager.enqueue_message = AsyncMock(
            side_effect=_flaky_enqueue,
        )

        original = _patch_resolver_terminal(
            resolver,
            work_id=work_id,
            instance_id="root-w4b",
        )
        try:
            from daemon.services.work_notifier import notify_work_watchers
            notified = await notify_work_watchers(
                work_id=work_id,
                status="completed",
                instance_manager=instance_manager,
                work_resolver=resolver,
                watcher_repo=watcher_repo,
                error=None,
            )
        finally:
            resolver.resolve_work = original

        # First watcher delivered (1 success); second threw → its row
        # is re-inserted via compensation, the function returned 1.
        assert notified == 1, (
            f"W4b: success path partial delivery MUST return 1 "
            f"(only the first watcher was notified); got "
            f"notified={notified}"
        )
        # The failed watcher is BACK in the DB.
        remaining = watcher_repo.get_watchers_for_job(work_id)
        assert len(remaining) == 1, (
            f"W4b: the failed watcher MUST be re-inserted via "
            f"compensation; got remaining={remaining}"
        )
        survivor = remaining[0]
        assert survivor.instance_id == watcher_b, (
            "W4b: the compensation UPSERT must have restored exactly "
            "the failed-claimed row (NOT the successful one — that "
            "one was correctly CAS-deleted)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# W5 — hook (b) external-seat delivery on COMPLETED flip incl. no_job
# ─────────────────────────────────────────────────────────────────────────────


class TestW5HookBNoJobAndExternalSeat:
    """W5 (2026-09-28) — hook (b) carrier closure.

    Pre-U7 hook (b) at ``job_feedback_observer.py:2430`` was scoped
    to the per-instance axis (``instance_id=instance_id``), AND it
    only ran inside ``_finalize_job`` (the COMPLETED/ERROR outbox
    that fires only when a JobItem exists). The carrier gap:

    * external seats (watcher whose parent_id is NULL or outside
      the job's instance subtree) — closed by globalizing to
      ``instance_id=None``;
    * ``no_job`` linkage turn-ends (no JobItem to finalize) —
      closed by adding a second hook trigger at the
      lifecycle-COMPLETED event in ``child_reports.py`` (which
      fires on every per-turn flip regardless of any Task /
      JobItem presence).

    W5 verifies both closures: a held watcher row paired with an
    external-seat watcher (parent_id=NULL) for the same work_id
    fires via the hook-call surfaced from the helper; the
    no_job-side carrier is exercised structurally by patching the
    observer's hook (b) hook-call shape."""

    @pytest.mark.asyncio
    async def test_w5_hook_b_global_scan_visits_external_seat(
        self, components,
    ):
        """W5 — external-seat watcher (parent_id=NULL) is visited by
        hook (b)'s global scan (U7 globalization). Pre-U7 the per-
        instance filter would have missed an external-seat watcher
        whose ``instance_id`` did not match the completing
        instance's id (the U1 cycle-4 c7f59aaf evidence class)."""
        engine = components["engine"]
        repo = components["instance_repo"]
        watcher_repo = components["watcher_repo"]
        instance_manager = components["instance_manager"]
        resolver = components["resolver"]
        jqs = components["jqs"]

        quiet_time = now_utc_naive() - timedelta(
            seconds=_mlg.MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS + 300,
        )
        # The "completing" instance + an EXTERNAL watcher whose
        # parent_id is NULL (the U1 cycle-4 dominant production
        # topology).
        _insert_tree_member(
            engine, instance_id="root-w5",
            status="completed",
            last_activity_at=quiet_time,
        )
        work_id = f"wid-w5-{uuid4().hex[:8]}"
        external_watcher = "external-watcher-parent-null"
        _add_watch(
            engine, work_id=work_id,
            instance_id=external_watcher,
            watch_events=["mission_terminal"],
        )

        original = _patch_resolver_terminal(
            resolver,
            work_id=work_id,
            instance_id="root-w5",
        )
        try:
            # Hook (b) — global scan (``instance_id=None``).
            # Pre-U7 globalize this would have filtered to
            # ``instance_id='root-w5'`` AND missed the external
            # watcher. Post-U7 the global scan visits every held
            # row, regardless of the watcher's tree position.
            result = (
                await jqs.reconcile_held_watches_for_instance(
                    instance_id=None,
                )
            )
        finally:
            resolver.resolve_work = original

        assert result["fired"] == 1, (
            f"W5: external-seat watcher (parent_id=NULL) MUST be "
            f"visited by the global-scan hook (b) and delivered — "
            f"got result={result}"
        )
        # FIXBACK: under the new contract, all-terminal trees
        # finalize immediately via the natural path — the backstop
        # (and orphan_released tag) is reserved for the non-terminal
        # zombie shape. The W5 invariant is that the external-seat
        # watcher is reached by the global scan, NOT that the
        # backstop fired.
        assert result["orphan_released"] == 0, (
            f"FIXBACK W5: all-terminal shape finalizes via natural "
            f"path (anchor plays no role); orphan_released MUST be 0. "
            f"Got result={result}"
        )
        assert len(watcher_repo.get_watchers_for_job(work_id)) == 0
        # Verify the enqueue reached the EXTERNAL watcher (NOT
        # the instance the helper was scoped to).
        delivered = instance_manager.enqueue_message.await_args_list
        assert len(delivered) == 1
        delivered_instance_id = delivered[0].kwargs["instance_id"]
        assert delivered_instance_id == external_watcher, (
            f"W5: delivery must have reached the external watcher "
            f"{external_watcher}; got {delivered_instance_id}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# W6 — additional negative controls (IDLE / holding shape preserved)
# ─────────────────────────────────────────────────────────────────────────────


# Helper used by W6 only — runs notify via the full chain.
async def _notify_via_helper(components) -> int:
    jqs = components["jqs"]
    return (
        await jqs.reconcile_held_watches_for_instance(
            instance_id=None,
        )
    ).get("fired", 0)


class TestW6NegativeControls:
    """W6 (2026-09-28) — negative controls that the U7 re-anchor must
    NOT regress."""

    @pytest.mark.asyncio
    async def test_w6_missing_anchor_arm_preserved(self, components):
        """Pre-U7 missing-anchor disarm contract: a tree where no
        member has any ``last_activity_at`` cannot fire the
        backstop (data gap, not evidence of a zombie). U7
        preserves this contract — the missing-anchor branch in
        ``_evaluate_legs`` now explicitly returns
        ``live=False`` with a disarm reason in the description."""
        engine = components["engine"]
        repo = components["instance_repo"]

        # Tree with NO last_activity_at anywhere. Insert via raw
        # text() so the bind param machinery handles DateTime →
        # ISO round-trip (mirrors ``_insert_tree_member`` above).
        with engine.begin() as conn:
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                text(
                    """
                    INSERT INTO instances
                        (instance_id, agent_id, agent_dir, status,
                         project_id, created_at, updated_at,
                         last_activity_at, version, parent_id)
                    VALUES
                        (:instance_id, :agent_id, :agent_dir, :status,
                         :project_id, :created_at, :updated_at,
                         :last_activity_at, 1, :parent_id)
                    """
                ),
                {
                    "instance_id": "root-w6-missing",
                    "agent_id": "developer",
                    "agent_dir": "/tmp/agents/developer",
                    "status": "completed",
                    "project_id": "test-project",
                    "created_at": now_iso,
                    "updated_at": now_iso,
                    "last_activity_at": None,
                    "parent_id": None,
                },
            )

        verdict = await evaluate_mission_live(
            instance_repository=repo,
            instance_id="root-w6-missing",
            bus_pending_count=None,
            timeout_seconds=_mlg.MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
        )
        # W6 invariant: dead (live=False) but the backstop did NOT
        # fire (timed_out=False) — the legs decided, the backstop
        # was disarmed.
        assert not verdict.live, (
            "W6: a tree with no last_activity_at is dead (legs said "
            "terminal); the backstop is disarmed so the outcome is "
            "decided by leg verdict alone, NOT the timeout anchor"
        )
        assert not verdict.timed_out, (
            "W6: missing anchor MUST disarm the backstop (data gap, "
            "not evidence of a zombie)"
        )

    @pytest.mark.asyncio
    async def test_w6_watch_row_preserved_when_held_for_mission(
        self, components,
    ):
        """W6 sibling: a held ``mission_terminal`` row whose mission
        is still live MUST survive a ``notify_work_watchers`` call
        (the partition routes it to ``held_for_mission`` and skips
        the CAS). The pre-existing behavior is preserved post-U7."""
        engine = components["engine"]
        repo = components["instance_repo"]
        watcher_repo = components["watcher_repo"]
        instance_manager = components["instance_manager"]
        resolver = components["resolver"]

        # Tree with LIVE descendant + row held for mission.
        recent = now_utc_naive() - timedelta(seconds=20)
        _insert_tree_member(
            engine, instance_id="root-w6-held",
            status="completed",
            last_activity_at=recent,
        )
        _insert_tree_member(
            engine, instance_id="live-w6-held",
            status="running",
            parent_id="root-w6-held",
            last_activity_at=recent,
        )

        work_id = f"wid-w6-held-{uuid4().hex[:8]}"
        _add_watch(
            engine, work_id=work_id,
            instance_id="watcher-w6-held",
            watch_events=["mission_terminal"],
        )

        # Pre-fix: the helper consults evaluate_mission_live which
        # returns live=True (live descendant) → partition routes
        # to held_for_mission → row preserved.
        original = _patch_resolver_terminal(
            resolver,
            work_id=work_id,
            instance_id="root-w6-held",
        )
        try:
            notified = await _notify_via_helper(components)
        finally:
            resolver.resolve_work = original

        assert notified == 0, (
            "W6 held-row preservation: a held watcher row whose "
            "mission is still live MUST NOT be notified — the row "
            "waits for the eventual terminal flip"
        )
        assert len(watcher_repo.get_watchers_for_job(work_id)) == 1

