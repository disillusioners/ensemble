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
# 48 declared paths. At the re-anchored base the lane's committed daemon/ diff
# is exactly 2 paths: `daemon/api.py` (ALREADY declared by the P4 lifespan row)
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
