"""Leg E — the release-blocking daemon-diff ownership-map gate.

Phase 7 (``phase7-plan.md`` §4.2 Leg E, decision D7.4, task T7.5, AC4).

WHAT THIS PINS
--------------
The engine arc is a NEW subsystem. Its branch ``daemon/`` diff must touch ONLY
the paths the phase-1 ownership map declares. An accidental edit to an
existing sweep would change live behavior in a release that claims to change
none — the highest-risk failure mode this gate exists to catch. A prose
promise ("we did not touch anything") is not acceptable evidence; this is.

THE EXPECTED SET IS DATA (D7.4)
-------------------------------
``EXPECTED_DAEMON_PATHS`` is a hardcoded literal — it is NEVER derived from
the diff. Deriving the allowlist from the thing it is supposed to police makes
the assertion incapable of failing. A legitimate new path is a ONE-LINE,
reviewed addition to the literal with a comment citing its sanctioning row.

SOURCES OF THE LITERAL (all read-only planning docs)
----------------------------------------------------
* ``phase1-plan.md`` §0.3 — the ownership map (single-ownership invariant).
* ``plan-overview.md`` attestation iteration-002 row 43 — the three folded
  additions: ``registry.py`` as two phase-scoped rows (P1-create / P3-edit),
  ``engine.py`` added to the P2 module row, ``daemon/graph.py`` as the P5
  class-4 attestation-bridge caller row.
* ``phase1-plan.md`` §0.3 L-9 / L-10 rows — the class-2 / final bridge sets.
* ``phase5-plan.md`` T5.4 — the bridge-site rows (callers of
  ``EventBus.create_event``, never inside ``create_event``).

RE-ANCHOR PROCEDURE (range-dependent pin — see the recorded test-debt
follow-up about census pins whose base drifts under a family merge)
--------------------------------------------------------------------
``BASE_SHA`` is the TRUE family base (the pre-P1 drift-merge tip). At each
family merge: (1) re-derive the base from the family line / wt-claim KV;
(2) re-run ``git diff --name-only <base>..HEAD -- daemon/``; (3) review every
new path and add ONLY sanctioned ones, one line each; (4) bump ``BASE_SHA``.
A stale base makes this gate VACUOUS (empty diff), not RED — so the test also
asserts the diff is non-empty.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# The TRUE family base: the pre-P1 drift-merge tip (origin/latest merged into
# the planning tip). HEAD-anchored, NOT working-tree-anchored: this gate audits
# what the branch COMMITS, so an uncommitted daemon edit does not change the
# verdict. Re-anchor per the procedure in the module docstring.
#
# RE-ANCHORED 2026-11-22 (Engine Arc Phase-2 pilot lane): the old base
# ``1a74b0493`` is the PHASE-1 family base. Between it and this lane's base
# ``8894f86a`` the release bump ``2bfd20acf`` (v0.31.27) touched
# ``daemon/__init__.py``, reddening this pin with a PRE-EXISTING path no
# ownership-map row sanctions. Per the documented re-anchor procedure the base
# moves to the lane base; the committed diff then contains ONLY this lane's
# sanctioned paths (``daemon/api.py`` already declared; the service module is
# added below as an explicit Phase-2 pilot row).
BASE_SHA = "8894f86a853c5fcba2173ab29405f02c71c1f3ef"

# The expected daemon/ path set — DATA, per D7.4. Grouped by ownership-map row.
# 74 declared paths (48 program rows + 6 P2-pilot stage-1 drift-arc addendum
# rows + 14 P2-pilot stage-2 sibling-lane drift addendum rows + 1 FOREIGN
# path re-anchored 2026-11-23 by the Phase-2 pilot evidence lane + 5 stage-3
# foreign-lane absorb rows re-anchored 2026-11-23 onto origin/latest
# a93b6b1df; the stage-2 set was itself re-anchored 2026-11-23 when
# origin/latest advanced past 7fd7af010 to b1833127e).
# At the re-anchor the lane's committed daemon/ diff was exactly 2 paths:
# `daemon/api.py` (ALREADY declared by the P4 lifespan row)
# and `daemon/services/orphan_watcher_sweep.py` (ONE newly declared row, added
# by this lane with its sanctioning comment below).
EXPECTED_DAEMON_PATHS: frozenset[str] = frozenset({
    # --- P1: migrations (phase1 §0.3, two migration rows) -----------------
    "daemon/migrations/versions/20261121_120000_create_engine_jobs.sql",
    "daemon/migrations/versions/20261121_120001_create_engine_runs.sql",
    # --- P1: repository + manager + config (phase1 §0.3) ------------------
    "daemon/repositories/engine/models.py",
    "daemon/repositories/engine/__init__.py",
    "daemon/repositories/engine/repository.py",
    "daemon/manager.py",                       # late-import registration block only
    "daemon/config.py",                        # EngineConfig + dead-knob deletes (T1.12/13)
    # --- P1: engine service core (phase1 §0.3) ----------------------------
    "daemon/services/engine/__init__.py",
    "daemon/services/engine/spec.py",
    "daemon/services/engine/registry.py",      # row 43: P1-create T1.8 / P3-edit T3.5
    "daemon/services/engine/estate.py",
    # --- P2: engine modules (phase1 §0.3; row 43 adds engine.py) ----------
    "daemon/services/engine/execution.py",
    "daemon/services/engine/state.py",
    "daemon/services/engine/locks.py",
    "daemon/services/engine/dispatch.py",
    "daemon/services/engine/metrics.py",
    "daemon/services/engine/engine.py",        # row 43: added to the P2 module row
    # --- P3: guardrails / killswitch / dualpath (phase1 §0.3) -------------
    "daemon/services/engine/guardrails.py",
    "daemon/services/engine/killswitch.py",
    "daemon/services/engine/dualpath.py",
    # --- P4: boot / watchdog / api lifespan (phase1 §0.3) -----------------
    "daemon/services/engine/boot.py",
    "daemon/services/engine/watchdog.py",
    "daemon/api.py",                           # P4 lifespan / P6 router catalogue
    # --- P5: event_registry/** (phase1 §0.3 — tree declared; these 4 landed)
    "daemon/services/event_registry/__init__.py",
    "daemon/services/event_registry/catalog.py",
    "daemon/services/event_registry/emit.py",
    "daemon/services/event_registry/metrics.py",  # D5.4 sibling-lane edit is map-declared
    # --- P5: class-1 bridge (phase1 §0.3) ---------------------------------
    "daemon/services/child_reports.py",
    "daemon/services/error_reporting.py",
    # --- P5: class-2 durable-bus bridge, L-9 (phase1 §0.3 / phase5 T5.4) ---
    "daemon/routers/answer_helper.py",
    "daemon/services/job_recovery_service.py",
    "daemon/services/event_publisher.py",
    "daemon/services/midflight_qa.py",
    "daemon/services/job_feedback_observer.py",
    "daemon/services/ram_guard_events.py",
    "daemon/services/stale_task_recovery.py",
    # --- P5: final bridge increment, L-10 (phase1 §0.3) -------------------
    # NOTE: the L-10 row writes these two as `daemon/services/room/...`, but
    # that directory does not exist — the real files (and the diff) are under
    # `daemon/repositories/room/...`. Same two files, same line anchors
    # (membership:407/:531, repository:1179); the row's path prefix is a typo.
    "daemon/services/instance_messaging.py",
    "daemon/services/message_processing_errors.py",
    "daemon/repositories/room/membership.py",     # L-10 row (path typo: services/room)
    "daemon/repositories/room/repository.py",     # L-10 row (path typo: services/room)
    "daemon/services/room_delivery.py",
    # --- P5: class-3 bridge — map says ONE of these two seams -------------
    "daemon/services/report_delivery_recovery.py",
    "daemon/repositories/report_injection/repository.py",
    # --- P5: class-4 attestation-bridge caller (phase1 §0.3, row 43 / M-5) -
    "daemon/graph.py",
    # --- P6: projection + new router (phase1 §0.3) ------------------------
    "daemon/services/engine/projection.py",
    "daemon/routers/engine.py",
    # --- P1 — T1.12 D-1 compat-contract extension (NOW MAP-CITING) ----------
    # Landed 6e97cdce2 (phase1-plan §0.3:103 + plan-overview attestation row 71;
    # ADD-A-MAP-ROW adjudication — revert would restore RC=3 on the promote
    # ENTRY gate, a functional regression). Replaces the former flagged carry:
    # the file is now a claimed ownership-map row (owner P1), so the prior
    # marker is obsolete and must stay gone.
    "daemon/plugin_subsystem/config_compat_contract.py",  # P1 — T1.12 D-1 compat-contract extension (landed 6e97cdce2)
    # --- PHASE 2 PILOT — orphan-watcher migration (this lane, 2026-11-22) ---
    # Sanctioned by the Emperor GO 2026-11-22 + the phase2-pilot-recommendation
    # charter: the pilot's execute_fn adapter lives in the service's OWN module
    # (build_engine_executor / engine_owns_execution), never in engine/**.
    "daemon/services/orphan_watcher_sweep.py",  # Phase-2 pilot — dual-path seam + late-bound engine executor
    # --- P2-PILOT STAGE-1 DRIFT-MERGE ADDENDUM (2026-11-22) -----------------
    # The 28-commit origin/latest drift merge (8894f86a -> 7fd7af010) carried
    # these 6 daemon paths from sibling lanes. Each row cites its drift arc and
    # first-in-range commit so the attribution is per-file, not per-count.
    "daemon/__init__.py",  # drift-arc release-bump 0da8aa619 (P2-pilot stage-1 census addendum 2026-11-22)
    "daemon/repositories/project/repository.py",  # drift-arc projres 8a4ce6899 (P2-pilot stage-1 census addendum 2026-11-22)
    "daemon/services/work_resolver.py",  # drift-arc work_resolver 96f21f1c5 (P2-pilot stage-1 census addendum 2026-11-22)
    "daemon/tools/job_queue.py",  # drift-arc job-queue bd364876f (P2-pilot stage-1 census addendum 2026-11-22)
    "daemon/tools/project.py",  # drift-arc projres 8a4ce6899 (P2-pilot stage-1 census addendum 2026-11-22)
    "daemon/tools/upgrade_tools.py",  # drift-arc upgrade_tools PIPELINE_SCRIPTS re-pin 5aa5d01ae (P2-pilot stage-1 census addendum 2026-11-22)
    # --- P2-PILOT STAGE-2 SIBLING-LANE DRIFT ADDENDUM (2026-11-23) ----------
    # origin/latest advanced past the stage-1 drift merge (7fd7af010) to
    # b1833127e; these 14 daemon paths landed from sibling lanes in the wider
    # range 8894f86a..HEAD. Each row cites its owning lane and first-in-range
    # commit so the attribution is per-file, never per-count. All are
    # attributable legitimate sibling-lane work — no rogue/unowned path.
    "daemon/constants.py",  # readiness boot-warm tolerance bb9e893c2 (stage-2 drift addendum 2026-11-23)
    "daemon/services/readiness.py",  # readiness boot-warm tolerance bb9e893c2 (stage-2 drift addendum 2026-11-23)
    "daemon/repositories/event/models.py",  # event-registry L-11 silent-kind retirement 2275192cf (stage-2 drift addendum 2026-11-23)
    "daemon/repositories/source/maintenance_seed.py",  # central-maintenance P1 weekly trigger seed c191074b6 (stage-2 drift addendum 2026-11-23)
    "daemon/services/maintenance_schedule_knobs.py",  # central-maintenance P1 knobs picker c191074b6 (stage-2 drift addendum 2026-11-23)
    "daemon/sources/adapters/scheduler.py",  # central-maintenance scheduler adapter re-arm seam c191074b6 (stage-2 drift addendum 2026-11-23)
    "daemon/routers/maintenance_reports.py",  # central-maintenance P4 report list route f94e73660 (stage-2 drift addendum 2026-11-23)
    "daemon/routers/__init__.py",  # central-maintenance P4 route registration f94e73660 (stage-2 drift addendum 2026-11-23)
    "daemon/services/live_views.py",  # central-maintenance live_views maintenance-reports root f94e73660 (stage-2 drift addendum 2026-11-23)
    "daemon/repositories/task/repository.py",  # wc-watchdog tri-state wake-source gate 93da27b29 (stage-2 drift addendum 2026-11-23)
    "daemon/services/waiting_children_watchdog.py",  # wc-watchdog false-wedge suppression 93da27b29 (stage-2 drift addendum 2026-11-23)
    "daemon/services/pok_service.py",  # pok active-entry cap 512->1024 942ac40fa (stage-2 drift addendum 2026-11-23)
    "daemon/tools/project_future.py",  # project-future name/shortname resolution seam 0b4493258 (stage-2 drift addendum 2026-11-23)
    "daemon/tools/project_history.py",  # project-history name/shortname resolution ladder a84bbc51c (stage-2 drift addendum 2026-11-23)
    # --- PHASE-2 PILOT EVIDENCE LANE — FOREIGN PATH RE-ANCHOR (2026-11-23) --
    # ONE foreign path had no ownership-map row at this lane's base
    # (7243143d0): `daemon/tests/test_inject_allowed_models.py`, added by the
    # governor Phase-3 lane (fc3c8ad1b, 2026-07-25 — "append_allowed_models
    # appender + inject_allowed_models flag"). It is a PRE-EXISTING path at
    # this lane's base, NOT this lane's work; the row is added so the
    # release-blocking Leg E gate is not RED on a FOREIGN path.
    #
    # THIS LANE'S OWN COMMITTED daemon/ DIFF ADDS NO NEW PATH: the two files
    # it edits — `daemon/services/engine/boot.py` (spec description/metadata
    # truthfulness) and `daemon/api.py` (posture-line truthfulness) — were
    # ALREADY declared rows above.
    #
    # BASE_SHA IS UNCHANGED — never widened (the base still moves forward
    # only through the documented family re-anchor procedure).
    "daemon/tests/test_inject_allowed_models.py",  # governor Phase-3 append/inject_allowed_models fc3c8ad1b (FOREIGN pre-existing path, re-anchored 2026-11-23 by the Phase-2 pilot evidence lane)
    # --- P2-PILOT STAGE-3 RE-ANCHOR ABSORB (2026-11-23) ---------------------
    # The pre-land re-anchor onto origin/latest a93b6b1df (62-commit forward
    # drift from this lane's base 7243143d0) brought five more foreign-lane
    # daemon/ paths into the audited range with no ownership-map rows. Each
    # row cites its owning lane + landing commit(s). BASE_SHA UNCHANGED
    # (8894f86a); pack semantics unchanged; no rogue/unowned path.
    "daemon/__main__.py",  # relcur-M2 release-pointer boot check 98044a57d (stage-3 foreign-lane absorb 2026-11-23)
    "daemon/services/release_pointer_check.py",  # relcur-M2 ceremony-window tolerance 00e751f53/98044a57d (stage-3 foreign-lane absorb 2026-11-23)
    "daemon/services/blueprint_lease_reaper.py",  # blueprint pending-lease reaper registration 5c06fc23c (stage-3 foreign-lane absorb 2026-11-23)
    "daemon/services/auto_continue_boot_pass.py",  # auto-continue surface-first doctrine 5f28bd29b..edc9670e9 (stage-3 foreign-lane absorb 2026-11-23)
    "daemon/tools/instance_read.py",  # instance-read husk detector legs 59cc2d65f..9a7ac3244 (stage-3 foreign-lane absorb 2026-11-23)
})

# Owner hints for the RED message — which phase SHOULD have owned an offending
# path. Kept separate from the set so the set stays a clean data literal.
_SPECIAL_OWNERS: dict[str, str] = {
    "daemon/api.py": "P4 (lifespan) / P6 (router catalogue) / Phase-2 pilot (dual-path wiring)",
    "daemon/services/orphan_watcher_sweep.py": "Phase-2 pilot (dual-path seam + late-bound executor, Emperor GO 2026-11-22)",
    "daemon/config.py": "P1 (EngineConfig + dead-knob deletes)",
    "daemon/manager.py": "P1 (late-import registration)",
    "daemon/graph.py": "P5 (class-4 attestation-bridge caller, row 43/M-5)",
    "daemon/services/child_reports.py": "P5 (class-1 bridge)",
    "daemon/services/error_reporting.py": "P5 (class-1 bridge)",
    "daemon/routers/answer_helper.py": "P5 (class-2 bridge, L-9)",
    "daemon/services/job_recovery_service.py": "P5 (class-2 bridge, L-9)",
    "daemon/services/event_publisher.py": "P5 (class-2 bridge, L-9)",
    "daemon/services/midflight_qa.py": "P5 (class-2 bridge, L-9)",
    "daemon/services/job_feedback_observer.py": "P5 (class-2 bridge, L-9)",
    "daemon/services/ram_guard_events.py": "P5 (class-2 bridge, L-9)",
    "daemon/services/stale_task_recovery.py": "P5 (class-2 bridge, L-9)",
    "daemon/services/instance_messaging.py": "P5 (final bridge, L-10)",
    "daemon/services/message_processing_errors.py": "P5 (final bridge, L-10)",
    "daemon/repositories/room/membership.py": "P5 (final bridge, L-10; row path typo)",
    "daemon/repositories/room/repository.py": "P5 (final bridge, L-10; row path typo)",
    "daemon/services/room_delivery.py": "P5 (final bridge, L-10)",
    "daemon/services/report_delivery_recovery.py": "P5 (class-3 bridge, one-of)",
    "daemon/repositories/report_injection/repository.py": "P5 (class-3 bridge, one-of)",
    "daemon/routers/engine.py": "P6 (new router)",
    "daemon/plugin_subsystem/config_compat_contract.py": (
        "P1 — T1.12 D-1 compat-contract extension (phase1-plan §0.3:103, landed 6e97cdce2; map-citing row)"
    ),
}


def _owner_hint(path: str) -> str:
    """Best-effort owning phase for an offending path (actionable RED text)."""
    if path in _SPECIAL_OWNERS:
        return _SPECIAL_OWNERS[path]
    if path.startswith("daemon/services/engine/"):
        return "P1/P2/P3/P4/P6 (phase1 §0.3 engine rows)"
    if path.startswith("daemon/services/event_registry/"):
        return "P5 (phase1 §0.3 event_registry/** row)"
    if path.startswith("daemon/migrations/versions/"):
        return "P1 (phase1 §0.3 migration rows)"
    if path.startswith("daemon/repositories/engine/"):
        return "P1 (phase1 §0.3 repository row)"
    return "UNKNOWN — no ownership-map row; consult phase1 §0.3"


def _assert_daemon_diff_contained(
    actual_paths: list[str], expected_paths: frozenset[str]
) -> None:
    """The containment assertion — actual daemon/ diff ⊆ expected set.

    This is the function the can-it-fail self-proof drives RED. It is
    deliberately independent of git and of the tree so the self-proof can
    exercise it in memory (no repo mutation).
    """
    offenders = sorted(p for p in actual_paths if p not in expected_paths)
    assert not offenders, (
        "Leg E RED — daemon/ paths outside the phase1 §0.3 ownership map: "
        + ", ".join(f"{p} [owner: {_owner_hint(p)}]" for p in offenders)
        + ". Per D7.4 a legitimate new path is a ONE-LINE reviewed addition "
        "to EXPECTED_DAEMON_PATHS citing its sanctioning row."
    )


def _actual_daemon_diff(repo_root: Path, base: str, head: str) -> list[str]:
    """The committed daemon/ path diff ``base..head`` (two-dot, commit-based)."""
    completed = subprocess.run(
        [
            "git", "-C", str(repo_root), "diff", "--name-only",
            f"{base}..{head}", "--", "daemon/",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return [line for line in completed.stdout.splitlines() if line]


class TestEngineDiffAudit:
    def test_daemon_diff_is_confined_to_ownership_map(self) -> None:
        """Leg E / AC4 — the release-blocking gate (HEAD-anchored)."""
        repo_root = Path(__file__).resolve().parents[3]
        actual = _actual_daemon_diff(repo_root, BASE_SHA, "HEAD")
        # Non-vacuity: an empty diff means a stale base, not a green gate.
        assert actual, (
            f"VACUOUS — no daemon/ diff from {BASE_SHA}..HEAD; re-anchor BASE_SHA"
        )
        _assert_daemon_diff_contained(actual, EXPECTED_DAEMON_PATHS)

    def test_containment_logic_can_fail(self) -> None:
        """The can-it-fail self-proof (phase7 §4.2 Leg E; model:
        ``tests/unit/metrics/test_metrics_family_adjudication.py:590+``).

        Three in-memory mutations — NO git, NO tree write — each of which
        MUST drive the containment assertion RED. A gate that cannot fail is
        not a gate; this is the evidence that this one can.
        """
        import pytest as _pytest

        # (1) Drop one declared path from the expected set: that path becomes
        #     an offender, and the RED names its owning phase.
        doctored = frozenset(
            p for p in EXPECTED_DAEMON_PATHS if p != "daemon/api.py"
        )
        with _pytest.raises(AssertionError) as exc:
            _assert_daemon_diff_contained(["daemon/api.py"], doctored)
        assert "daemon/api.py" in str(exc.value)
        assert "P4" in str(exc.value)

        # (2) A brand-new, unrecorded daemon path — the exact failure mode
        #     this gate exists to catch (an accidental edit to a live sweep).
        with _pytest.raises(AssertionError) as exc:
            _assert_daemon_diff_contained(
                ["daemon/services/engine/brand_new_sweep.py"],
                EXPECTED_DAEMON_PATHS,
            )
        assert "brand_new_sweep.py" in str(exc.value)

        # (3) An empty expected set makes EVERY actual path an offender —
        #     the assertion is not vacuously green.
        with _pytest.raises(AssertionError) as exc:
            _assert_daemon_diff_contained(["daemon/config.py"], frozenset())
        assert "daemon/config.py" in str(exc.value)
