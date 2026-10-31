"""Pack J — architect-amendment pins A1/A3/A5/A6 + the A2
disposition leg (wi-78967dba).

Pins (phase3-plan T3.12):
* (i) A1 — the (h) counter block is emitted whenever examined>0
  (plant: gate on reconciled>0 ⇒ RED); non-zero error_skips
  surfaces in the WARN line; escalating WARN at >=K=40 consecutive
  skips (plant: K unchecked ⇒ RED);
* (ii) A3 — every close line carries notify=<outcome>; the
  follow-up token for terminal-job-with-surviving-watchers;
* (iii) A5 — attribution shape is the explicit IN-list, never
  prefix-LIKE (static pin; plant a LIKE form ⇒ RED);
* (iv) A6 — defensive E1 pins: (a) the member-alive builder never
  re-arms fail-MISS on the discounted instance; (b) the
  childless-root fallback is never ON the (h) close path (anti
  "unify the anchor" regression);
* (v) A2 disposition — the in-txn re-check pin (recheck=True ⇒ a
  retry child minted before the UPDATE aborts the txn; planted
  no-recheck form ⇒ RED) + the DEFAULT-False byte-identity pin
  (existing callers issue the identical SQL with the flag absent —
  behavioral: default-False finalizes a row WITH an inflight task,
  exactly as before).
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.job_queue.repository import JobRepository
from daemon.services.dependency_bus import set_dependency_bus
from daemon.services.job_recovery_service import (
    ZOMBIE_CLOSE_STREAK_WARN_THRESHOLD,
)

from tests.job_queue.zombie_close_helpers import (
    insert_zombie_fixture,
    make_bus_mock,
    make_recovery_service,
    read_job_state,
)

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 12

_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_API_SRC = (_SOURCE_ROOT / "daemon" / "api.py").read_text(
    encoding="utf-8"
)
_SERVICE_SRC = (
    _SOURCE_ROOT / "daemon" / "services" / "job_recovery_service.py"
).read_text(encoding="utf-8")


@pytest.fixture
def j_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def j_bus():
    bus = make_bus_mock(pending=[])
    set_dependency_bus(bus)
    try:
        yield bus
    finally:
        set_dependency_bus(None)


class TestA1CounterBlockVisibility:
    async def test_counter_block_logs_when_examined_gt_zero_even_reconciled_0(
        self, j_engine, j_bus, caplog
    ):
        """A1: a held-only cycle (examined=1, finalized=0) MUST
        surface the (h) block — the api loop's A1 log path keys on
        ``examined > 0``, NOT ``reconciled > 0`` (static pin — the
        consumer block reads zombie_stats.examined before logging)."""
        assert re.search(
            r"zombie_stats\s*=\s*stats\.get\(\"zombie_close\"\)", _API_SRC
        ), "the loop must consume the (h) counter block"
        assert (
            re.search(
                r"if zombie_stats\.get\(\"examined\", 0\) > 0:", _API_SRC
            )
            is not None
        ), (
            "the (h) log gate must key on examined>0 alone (A1) — "
            "colon-terminated, no appended conjunct"
        )
        # Tidier fold: the forbidden form is BROADENED to any
        # `and`-conjunct following the gate predicate on the same
        # logical line — the narrow `and\s+reconciled` form missed
        # the token-between shape (e.g. an intermediary
        # `zombie_stats.get("reconciled", ...)` conjunct).
        assert (
            re.search(
                r"examined\", 0\) > 0\s+and\b[^\n]*reconciled",
                _API_SRC,
            )
            is None
        ), "the examined>0 gate must NOT be conjointed with reconciled"
        # Behavioral half: a held candidate produces examined=1,
        # finalized=0 in the stats dict (the input the loop gates on).
        ref = insert_zombie_fixture(
            j_engine, verdict="completed", age_hours=3.0
        )
        # live sibling ⇒ hold
        with j_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO instances (instance_id, agent_id, "
                    "agent_dir, status, project_id, created_at, "
                    "updated_at, version, parent_id, last_activity_at) "
                    "VALUES (:s, 'worker', 'agents/worker', 'running', "
                    "'test-project', :n, :n, 1, :p, :a)"
                ),
                {
                    "s": f"inst-j-sib-{uuid.uuid4().hex[:6]}",
                    "n": "2026-10-30 00:00:00",
                    "p": ref["instance_id"],
                    "a": "2026-10-31 00:00:00",
                },
            )
        service = make_recovery_service(j_engine)
        stats = await (
            service.reconcile_drift_states(
                min_pending_age_seconds=0, min_orphan_age_seconds=60
            )
        )
        zc = stats.get("zombie_close") or {}
        assert zc.get("examined", 0) == 1, zc
        assert zc.get("finalized", 0) == 0, zc
        assert stats.get("reconciled", 0) == 0

    async def test_error_skips_surface_in_warn_line(self):
        # Tidier fold: REQUIRED match against the actual api.py
        # wording (no `or` escape hatch).
        assert re.search(
            r"error skip\(s\)[\s\S]{0,120}?fail-closed", _API_SRC
        ), (
            "non-zero error_skips must surface in the drift-stats "
            "WARN line with the actual api.py wording (A1-ii)"
        )

    async def test_escalating_warn_at_threshold_k(self, j_engine, j_bus, caplog):
        """A1-iii: K=40 consecutive skips for the SAME job fires the
        escalating WARN exactly at the threshold. Plant form (K
        unchecked) would fire on EVERY skip ⇒ the
        below-threshold-silent assertion goes RED."""
        import logging

        ref = insert_zombie_fixture(
            j_engine, verdict="completed", age_hours=3.0
        )
        service = make_recovery_service(j_engine)
        assert ZOMBIE_CLOSE_STREAK_WARN_THRESHOLD == 40
        with caplog.at_level(
            logging.WARNING,
            logger="daemon.services.job_recovery_service",
        ):
            # 39 bumps: silent
            for _ in range(ZOMBIE_CLOSE_STREAK_WARN_THRESHOLD - 1):
                service._zombie_close_streak_bump(ref["job_id"], "hold")
            below = [
                r for r in caplog.records if "stuck-closed" in r.getMessage()
            ]
            assert not below, "below K the streak must stay silent"
            # the 40th: fires
            service._zombie_close_streak_bump(ref["job_id"], "hold")
            fired = [
                r for r in caplog.records if "stuck-closed" in r.getMessage()
            ]
            assert len(fired) == 1, fired
            # Advisory-2 ladder (K*2^i): silent 41..79, re-fires at 80
            for _ in range(39):
                service._zombie_close_streak_bump(ref["job_id"], "hold")
            mid = [
                r for r in caplog.records if "stuck-closed" in r.getMessage()
            ]
            assert len(mid) == 1, mid
            service._zombie_close_streak_bump(ref["job_id"], "hold")  # 80
            ladder = [
                r for r in caplog.records if "stuck-closed" in r.getMessage()
            ]
            assert len(ladder) == 2, ladder

    async def test_streak_resets_on_close_and_evicts_when_gone(
        self, j_engine, j_bus
    ):
        """A1/CF-3: reset-on-close + evict-when-candidate-disappears."""
        ref = insert_zombie_fixture(
            j_engine, verdict="completed", age_hours=3.0
        )
        service = make_recovery_service(j_engine)
        service._zombie_close_streaks[ref["job_id"]] = 7
        # Close it (the happy path) ⇒ streak reset.
        await (
            service.reconcile_drift_states(
                min_pending_age_seconds=0, min_orphan_age_seconds=60
            )
        )
        assert ref["job_id"] not in service._zombie_close_streaks
        # Eviction: an entry whose job left the candidate shape is
        # dropped on the next maintain pass (W1 fold: the helper is
        # async — the existence probe goes through to_thread).
        other = f"job-zc-gone-{uuid.uuid4().hex[:8]}"
        service._zombie_close_streaks[other] = 3
        await service._zombie_close_streak_maintain(set())
        assert other not in service._zombie_close_streaks


class TestA3NotifyOutcomeField:
    async def test_every_close_line_carries_notify_outcome(self):
        """Tidier fold: TWO precise anchors (the tautological `or`
        arm is dropped) — the close LOG line's format placeholder
        AND the detail record's f-string, the two surfaces A3
        requires."""
        # Anchor 1: the close log line's notify=%s placeholder.
        assert "notify=%s" in _SERVICE_SRC, (
            "the (h) close log line must carry the notify=<outcome> "
            "placeholder (A3)"
        )
        # Anchor 2: the close detail record's f-string.
        assert "notify={notify_outcome}" in _SERVICE_SRC, (
            "the (h) close detail record must carry the notify "
            "outcome (A3 — the post-commit notify-loss visibility)"
        )

    async def test_followup_token_present(self):
        """Terminal-job-with-surviving-watcher-rows routes into the
        watch_reconcile_sweep follow-up semantics — the (h) docstring
        carries the routing (A3-ii)."""
        assert "watch_reconcile_sweep" in _SERVICE_SRC


class TestA5AttributionShape:
    async def test_attribution_is_explicit_in_list_never_prefix_like(self):
        """A5: attribution/forensics use the explicit IN-list; a
        prefix-LIKE form (pg btree prefix-blind) is the planted
        defect form. Positive anchor = the registration surface
        (work_status.py) carrying the spelled-out IN-list guidance;
        the planted form replaces it with a LIKE — both directions
        pinned."""
        ws_src = (
            _SOURCE_ROOT / "daemon" / "services" / "work_status.py"
        ).read_text(encoding="utf-8")
        assert "zombie_active_completed" in ws_src
        assert "zombie_active_failed" in ws_src
        assert "zombie_active_cancelled" in ws_src
        # Tidier fold: wrap-tolerant anchor — whitespace-insensitive
        # match on the token spellings inside the IN-list guidance
        # (the prior regex depended on the comment's line wrapping).
        compact = re.sub(r"[\s#]+", "", ws_src)
        assert (
            "IN-list('zombie_active_completed','zombie_active_failed',"
            "'zombie_active_cancelled')" in compact
        ), "the explicit IN-list attribution form must be recorded"
        assert "LIKE'zombie_active" not in compact, (
            "prefix-LIKE attribution is forbidden at the registration"
        )

class TestA6DefensiveE1Pins:
    async def test_member_alive_builder_never_probes_the_discounted_instance(
        self, j_engine
    ):
        """A6(a): the member-alive companion leg's candidate builder
        probes only NON-ROOT NON-TERMINAL members (the ``tid !=
        instance_id`` root exclusion + terminal filter + stranded
        exclusion) — a discounted/root instance is never a probe
        target, and the leg returns "no hold" when the candidate set
        is empty (the childless-root shape). Static pin on the
        builder's exact conjuncts."""
        guard_src = (
            _SOURCE_ROOT
            / "daemon"
            / "services"
            / "mission_live_guard.py"
        ).read_text(encoding="utf-8")
        assert "has_live_work" in guard_src
        assert "NO live task row" in guard_src
        assert re.search(
            r"candidates = sorted\(\s*tid for tid, status in "
            r"member_status\.items\(\)\s*if tid != instance_id",
            guard_src,
        ), (
            "the member-alive candidate builder must exclude the "
            "walk root and probe non-terminal members only"
        )

    async def test_childless_root_fallback_not_on_close_path(
        self, j_engine, j_bus
    ):
        """A6(b): the anchor fallback plays NO role in the (h)
        decision — a discounted childless root with a FRESH root
        activity stamp (the fallback would hold it in E1's own
        backstop) still closes: the (h) chain never consults the
        anchor (anti-'unify the anchor' regression)."""
        ref = insert_zombie_fixture(
            j_engine, verdict="completed", age_hours=3.0
        )
        # FRESH root activity — the enqueue-refreshable vector.
        with j_engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE instances SET last_activity_at = :fresh "
                    "WHERE instance_id = :i"
                ),
                {
                    "fresh": "2026-10-31 12:00:00",
                    "i": ref["instance_id"],
                },
            )
        service = make_recovery_service(j_engine)
        await (
            service.reconcile_drift_states(
                min_pending_age_seconds=0, min_orphan_age_seconds=60
            )
        )
        st = read_job_state(j_engine, ref["job_id"])
        assert st["admission_state"] == "done", (
            "fresh ROOT activity (the anchor fallback) must NOT hold "
            f"the (h) close: {st}"
        )


class TestA2Disposition:
    async def test_inflight_recheck_true_aborts_on_retry_child(self, j_engine):
        """A2/T1.9: with recheck_instance_inflight=True a retry child
        minted BEFORE the UPDATE aborts the txn — no finalize, row
        stays ACTIVE."""
        ref = insert_zombie_fixture(
            j_engine, verdict="failed", age_hours=3.0
        )
        # A pending retry child appears (inherited instance_id,
        # fresh work_id) — visible to the in-txn re-check but NOT to
        # the (already-run) candidate probe.
        with j_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO task (task_type, instance_id, "
                    "message_id, status, retry_count, created_at, "
                    "cancel_requested, retry_scheduled, work_id, "
                    "is_deferred, is_background, completed_at, "
                    "started_at) VALUES "
                    "('process_message', :i, NULL, 'pending', 0, "
                    ":n, False, False, :w, False, False, NULL, NULL)"
                ),
                {
                    "i": ref["instance_id"],
                    "n": "2026-10-31 00:00:00",
                    "w": f"job-zc-retrychild-{uuid.uuid4().hex[:8]}",
                },
            )
        repo = JobRepository(j_engine)
        out = repo.force_finalize_orphan(
            ref["job_id"], "zombie_active_failed", True
        )
        assert out is None, "the in-txn recheck must abort the close"
        st = read_job_state(j_engine, ref["job_id"])
        assert st["admission_state"] == "active", st
        assert st["lock_rows"] == 1, st

    async def test_default_false_is_byte_identical_no_recheck(self, j_engine):
        """T1.9 DEFAULT-False byte-identity: with the flag ABSENT an
        inflight task does NOT block the finalize — exactly the
        pre-change behavior for existing callers
        (cleanup_non_terminal_jobs)."""
        ref = insert_zombie_fixture(
            j_engine, verdict="completed", age_hours=3.0
        )
        with j_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO task (task_type, instance_id, "
                    "message_id, status, retry_count, created_at, "
                    "cancel_requested, retry_scheduled, work_id, "
                    "is_deferred, is_background, completed_at, "
                    "started_at) VALUES "
                    "('process_message', :i, NULL, 'pending', 0, "
                    ":n, False, False, :w, False, False, NULL, NULL)"
                ),
                {
                    "i": ref["instance_id"],
                    "n": "2026-10-31 00:00:00",
                    "w": f"job-zc-legacyinflight-{uuid.uuid4().hex[:8]}",
                },
            )
        repo = JobRepository(j_engine)
        out = repo.force_finalize_orphan(
            ref["job_id"], "cancelled"
        )  # NO third arg — the existing-caller shape
        assert out is not None, (
            "default-False must behave byte-identically to the "
            "pre-change seam (no recheck)"
        )
        st = read_job_state(j_engine, ref["job_id"])
        assert st["admission_state"] == "done", st

    async def test_h_leg_always_passes_true(self):
        """The (h) leg's seam call carries the recheck positionally
        (True) — static pin."""
        assert re.search(
            r"force_finalize_orphan,\s*\n\s*job_id,\s*\n\s*token,"
            r"\s*\n\s*True,",
            _SERVICE_SRC,
        ) or re.search(
            r"force_finalize_orphan,\s*job_id,\s*token,\s*True", _SERVICE_SRC
        ), "the (h) write must pass recheck_instance_inflight=True"
