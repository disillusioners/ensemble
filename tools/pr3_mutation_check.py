#!/usr/bin/env python3
"""PR3 mutation harness — the FIXED exit taxonomy (PR2 contract).

GATE 1 of the mission-records PR3 runtime gate: prove the PR3 pins KILL
when each guarantee is mutated.

Contract (copied verbatim from the PR2 harness, tools/pr2_mutation_check.py):

  * Each mutation = a targeted line-neutral edit applied to a file in the
    MUTATION CLONE (the frozen WT is never touched).
  * The pinned pytest scope is run as a SUBPROCESS against the clone tree.
  * The mutation is REVERTED immediately afterwards (``git checkout --``).
  * KILL CRITERION (strict):
        pytest exit 1  -> KILLED
        pytest exit 0  -> SURVIVED  (gate failure)
        pytest exit 2/3/4/5 -> HARNESS FAULT (NOT a kill; false-pass guard)
  * Harness exit 0 only if ALL mutations were killed.

Extra (PR3-specific):
  * A BASELINE pass runs every pin scope UNMUTATED first — all must exit 0.
  * A DISCRIMINATION PROBE points one mutation's pin at a non-existent node
    id; the harness must classify it HARNESS FAULT and FAIL overall.
  * Raw pytest exit codes are captured per mutation (instrumented proof).
  * Per-mutation + total wall timings are recorded.

Usage:
    python3 pr3_mutation_check.py                # baseline + matrix
    python3 pr3_mutation_check.py --probe        # discrimination probe only
    python3 pr3_mutation_check.py --json out.json
"""

# --------------------------------------------------------------------------
# PROVENANCE (in-tree landing 2026-10-27 — the post-gate dev-lane job; the
# matrix pointer previously lived ONLY in /tmp and a fresh harness copy
# silently dropped it, reading 19/20 with I2-alt SURVIVED):
#   * Verbatim copy of the tester's gate-1 harness
#     /tmp/mrpr3_mut/pr3_mutation_check.py. The pristine PRE-drift copy is
#     preserved beside it as pr3_mutation_check.py.orig
#     (sha256 6ea49916e3843d4b4935a3540fe8fd9fd5d5a3294cbbb1515cb770f8ef75b664).
#   * THE I2-ALT PIN POINTER IS INTENTIONAL POST-GATE DRIFT, not corruption:
#     the I2-alt mutation's `pin` was repointed from
#     tests/unit/test_mission_record_pr3_routing_flip.py::TestC3CrossParentFirewall
#     (the .orig value — a DEAD pin: the mutation SURVIVED under it) to
#     tests/unit/test_report_delivery_recovery_service.py::
#     TestNoRowBackstopQueuePrefixLedgerSkip. The post-drift value below is
#     CANONICAL — it is what turns the matrix 19/20 into 20/20. Do NOT
#     "restore" it from the .orig.
#   * The two environment constants below (REPO mutation-clone path, PYTHON
#     venv) are per-checkout facts of the gate run. A fresh checkout
#     re-points them and re-creates the scratch clone:
#         git worktree add --detach /tmp/mrpr3-mut-35df7171e <HEAD-to-mutate>
#     The clone is disposable: each mutation is applied there, reverted
#     (`git checkout --`), and the porcelain must be EMPTY before/after.
#   * Gate provenance: 20/20 killed @ d34f569c0 (feature/mission-records-pr3)
#     — see .agents/tester/PACKS.md "mission-records PR3" §mrpr3_mutations
#     and tools/pr2_mutation_check.py for the PR2-generation predecessor
#     (tree-relative; this clone-based design never touches the frozen WT).
# --------------------------------------------------------------------------

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# --- hard-coded locations (the clone is tree-relative; the PR2 harness
# --- derives REPO from __file__, which does not apply here) ---------------
REPO = Path("/tmp/mrpr3-mut-35df7171e")
PYTHON = (
    "/Users/nguyenminhkha/All/Code/opensource-projects/"
    "agents-ensemble-wt-pr3-final/.venv/bin/python"
)
TIMEOUT_BIN = "/opt/homebrew/bin/timeout"
SUBPROCESS_TIMEOUT = 300
GIT = "git"

#: The safety fence — the harness refuses to run if any of these is violated.
SAFETY_ENV = {
    "POSTGRES_DB": "ensemble_dev_test_only",
    "ENSEMBLE_SELF_ENV": "dev",
    "POSTGRES_URL": "",
    "PGPASSWORD": "",
}

REPO_FILES = "daemon/repositories/mission_record/repository.py"
RDRS = "daemon/services/report_delivery_recovery.py"
SVC = "daemon/services/mission_record_service.py"
RECIP = "daemon/services/report_recipient.py"
RINJ = "daemon/repositories/report_injection/repository.py"
MGR = "daemon/manager.py"
GATE = "daemon/services/attestation_gate.py"

# --------------------------------------------------------------------------
# THE MATRIX
#
# Each entry:
#   id          — the mutation id (the report's row key)
#   desc        — the one-line, verbatim description of the edit
#   edits       — [(relpath, old, new), ...]  (applied atomically; each `old`
#                 MUST occur EXACTLY ONCE in the file, else ANCHOR MISMATCH)
#   pin         — the pytest node id (or "file::Class" scope)
# --------------------------------------------------------------------------
MUTATIONS: list[dict] = [
    # ---------------------------------------------------------------- A ---
    {
        "id": "A1",
        "desc": (
            "A1 f2-split: the DIAGNOSTIC legs authorise satisfaction — the "
            "caller now consumes `_note_diagnostic_delivery_evidence`'s "
            "return and ORs it into `has_evidence` "
            "(report_delivery_recovery.py ~:2475-2488)"
        ),
        "edits": [
            (
                RDRS,
                """        self._note_diagnostic_delivery_evidence(
            commissioner_id, worker_id, commission, out
        )""",
                """        diagnostic_evidence = self._note_diagnostic_delivery_evidence(
            commissioner_id, worker_id, commission, out
        )""",
            ),
            (
                RDRS,
                """            has_evidence = self._commission_bound_delivery_evidence(
                commissioner_id, worker_id, commission
            )""",
                """            has_evidence = self._commission_bound_delivery_evidence(
                commissioner_id, worker_id, commission
            ) or diagnostic_evidence""",
            ),
        ],
        "pin": (
            "tests/unit/test_mission_reconciliation_sweep.py::"
            "TestTheDiagnosticEvidenceLegs::"
            "test_an_earlier_commissions_queue_enqueue_does_not_satisfy_"
            "a_fresh_commission"
        ),
    },
    {
        "id": "A2",
        "desc": (
            "A2 f2-split: LEG-1 authority removed — "
            "`_commission_bound_delivery_evidence` never confirms "
            "(return True -> return False) (report_delivery_recovery.py ~:2847)"
        ),
        "edits": [
            (
                RDRS,
                """        if delivered_rows and self._delivered_row_is_this_commission(
            commission, delivered_rows
        ):
            return True
        return False""",
                """        if delivered_rows and self._delivered_row_is_this_commission(
            commission, delivered_rows
        ):
            return False
        return False""",
            ),
        ],
        "pin": (
            "tests/unit/test_mission_reconciliation_sweep.py::"
            "TestTheDiagnosticEvidenceLegs::"
            "test_a_leg_one_delivered_commission_still_satisfies"
        ),
    },
    # ---------------------------------------------------------------- B ---
    {
        "id": "B",
        "desc": (
            "B cross-epoch hold: re-introduce the OPEN-epoch-only filter on "
            "the anchor-scoped count — `.where(MissionRecord.status == "
            "MissionStatus.OPEN.value)` (mission_record/repository.py ~:424)"
        ),
        "edits": [
            (
                REPO_FILES,
                """            .where(MissionRecord.anchor_instance_id == anchor_instance_id)
            .where(MissionCommission.state == CommissionState.OUTSTANDING.value)
        )""",
                """            .where(MissionRecord.anchor_instance_id == anchor_instance_id)
            .where(MissionRecord.status == MissionStatus.OPEN.value)
            .where(MissionCommission.state == CommissionState.OUTSTANDING.value)
        )""",
            ),
        ],
        "pin": (
            "tests/unit/test_mission_record_pr3_cross_epoch_hold.py::"
            "TestCrossEpochHold::"
            "test_the_orphaned_commission_on_a_superseded_epoch_still_holds"
            "_the_publish"
        ),
    },
    # ---------------------------------------------------------------- C ---
    {
        "id": "C",
        "desc": (
            "C routing ORDER BY discriminator (F8): the routing query's "
            "`created_at ASC` tie-break is FLIPPED to `created_at DESC` "
            "(mission_record/repository.py ~:173)"
        ),
        "edits": [
            (
                REPO_FILES,
                """            .where(
                MissionCommission.advisory_backfill.is_(False)  # type: ignore[union-attr]
            )
            .order_by(MissionCommission.created_at.asc())  # type: ignore[attr-defined]
            .limit(1)""",
                """            .where(
                MissionCommission.advisory_backfill.is_(False)  # type: ignore[union-attr]
            )
            .order_by(MissionCommission.created_at.desc())  # type: ignore[attr-defined]
            .limit(1)""",
            ),
        ],
        "pin": (
            "tests/unit/test_mission_record_pr3_routing_flip.py::"
            "TestTheRoutingFlip::test_multi_commissioner_takes_the_oldest_"
            "outstanding"
        ),
    },
    # ---------------------------------------------------------------- D ---
    {
        "id": "D",
        "desc": (
            "D terminator guard: `AND state='outstanding'` REMOVED from the "
            "guarded commission UPDATE (mission_record/repository.py ~:611-627)"
        ),
        "edits": [
            (
                REPO_FILES,
                """        stmt = (
            update(MissionCommission)
            .where(MissionCommission.commission_id == commission_id)
            .where(MissionCommission.state == CommissionState.OUTSTANDING.value)
            .values(**values)
        )""",
                """        stmt = (
            update(MissionCommission)
            .where(MissionCommission.commission_id == commission_id)
            .values(**values)
        )""",
            ),
        ],
        "pin": (
            "tests/unit/test_mission_record_pr3_terminator.py::"
            "TestSatisfyFailOpenWrapper::test_lost_race_is_counted_never_"
            "retried"
        ),
    },
    # ---------------------------------------------------------------- E ---
    {
        "id": "E",
        "desc": (
            "E sweep mode ladder: DRY mode PERFORMS WRITES — the abandon and "
            "satisfy writers' `enforcing = mode == ENFORCE` gate is hard-wired "
            "to True (report_delivery_recovery.py :3181 / :3257)"
        ),
        "edits": [
            (
                RDRS,
                """        enforcing = mode == MISSION_RECORDS_MODE_ENFORCE
        if enforcing:
            service = self._resolve_mission_record_service()
            if service is None:
                # No writer wired""",
                """        enforcing = True
        if enforcing:
            service = self._resolve_mission_record_service()
            if service is None:
                # No writer wired""",
            ),
            (
                RDRS,
                """        enforcing = mode == MISSION_RECORDS_MODE_ENFORCE
        if enforcing:
            service = self._resolve_mission_record_service()
            if service is None:
                logger.warning(""",
                """        enforcing = True
        if enforcing:
            service = self._resolve_mission_record_service()
            if service is None:
                logger.warning(""",
            ),
        ],
        "pin": (
            "tests/unit/test_mission_reconciliation_sweep.py::"
            "TestTheModeLadder::test_dry_detects_but_never_writes"
        ),
    },
    # ---------------------------------------------------------------- F ---
    {
        "id": "F",
        "desc": (
            "F TTL knob: the env read is SHADOWED — `raw` is hard-coded empty "
            "so ENSEMBLE_MISSION_COMMISSION_TTL_SECONDS never applies "
            "(mission_record_service.py :341)"
        ),
        "edits": [
            (
                SVC,
                """    raw = (os.environ.get(MISSION_COMMISSION_TTL_ENV) or "").strip()""",
                """    raw = ""  # MUTATION: the env read is shadowed""",
            ),
        ],
        "pin": (
            "tests/unit/test_mission_reconciliation_sweep.py::"
            "TestTheTtlKnob::test_the_env_knob_overrides_the_configured_default"
        ),
    },
    # ---------------------------------------------------------------- G ---
    {
        "id": "G",
        "desc": (
            "G kill-switch gate: the MASTER kill-switch check in `recover_now` "
            "is neutralised (`if not self._enabled` -> `if not self._enabled "
            "and False`) so the sweep runs while disabled "
            "(report_delivery_recovery.py :989-992)"
        ),
        "edits": [
            (
                RDRS,
                """        if not self._enabled:
            logger.info(
                "ReportDeliveryRecoveryService disabled by config; "
                "recover_now is a no-op (no lane ran)"
            )
            return SweepResult()""",
                """        if not self._enabled and False:
            logger.info(
                "ReportDeliveryRecoveryService disabled by config; "
                "recover_now is a no-op (no lane ran)"
            )
            return SweepResult()""",
            ),
        ],
        "pin": (
            "tests/unit/test_mission_reconciliation_sweep.py::"
            "TestTheMasterKillSwitch::test_a_disabled_service_runs_no_lane"
        ),
    },
    # ---------------------------------------------------------------- H ---
    {
        "id": "H1",
        "desc": (
            "H1 advisory exclusion (routing read): "
            "`find_routing_commission_for_worker` drops "
            "`.where(advisory_backfill.is_(False))` "
            "(mission_record/repository.py :171)"
        ),
        "edits": [
            (
                REPO_FILES,
                """            .where(
                MissionCommission.advisory_backfill.is_(False)  # type: ignore[union-attr]
            )
            .order_by(MissionCommission.created_at.asc())  # type: ignore[attr-defined]
            .limit(1)""",
                """            .order_by(MissionCommission.created_at.asc())  # type: ignore[attr-defined]
            .limit(1)""",
            ),
        ],
        "pin": (
            "tests/unit/test_mission_record_pr3_routing_flip.py::"
            "TestTheRoutingFlip::test_advisory_backfill_rows_never_route"
        ),
    },
    {
        "id": "H2",
        "desc": (
            "H2 advisory exclusion (sweep admission read): "
            "`find_outstanding_commissions` drops the advisory predicate "
            "(mission_record/repository.py :222)"
        ),
        "edits": [
            (
                REPO_FILES,
                """        if not include_advisory:
            stmt = stmt.where(
                MissionCommission.advisory_backfill.is_(False)  # type: ignore[union-attr]
            )
        stmt = stmt.order_by(
            MissionCommission.created_at.asc()  # type: ignore[attr-defined]
        ).limit(max(1, int(limit)))""",
                """        stmt = stmt.order_by(
            MissionCommission.created_at.asc()  # type: ignore[attr-defined]
        ).limit(max(1, int(limit)))""",
            ),
        ],
        "pin": (
            "tests/unit/test_mission_reconciliation_sweep.py::"
            "TestTheAdvisoryExclusion::test_an_advisory_row_is_never_admitted"
        ),
    },
    {
        "id": "H3",
        "desc": (
            "H3 advisory exclusion (census count read): "
            "`count_commissions_for_mission` drops the advisory predicate "
            "(mission_record/repository.py :352)"
        ),
        "edits": [
            (
                REPO_FILES,
                """        if states:
            stmt = stmt.where(MissionCommission.state.in_(states))
        if not include_advisory:
            stmt = stmt.where(
                MissionCommission.advisory_backfill.is_(False)  # type: ignore[union-attr]
            )
        if session is not None:
            return len(list(session.exec(stmt)))""",
                """        if states:
            stmt = stmt.where(MissionCommission.state.in_(states))
        if session is not None:
            return len(list(session.exec(stmt)))""",
            ),
        ],
        "pin": (
            "tests/unit/test_mission_record_pr3_backfill_commit.py::"
            "TestAdvisoryExclusionEverywhere::"
            "test_committed_backfill_rows_are_excluded_from_every_read"
        ),
    },
    {
        "id": "H4",
        "desc": (
            "H4 advisory exclusion (anchor-scoped count read): "
            "`count_outstanding_commissions_for_anchor` drops the advisory "
            "predicate (mission_record/repository.py :424)"
        ),
        "edits": [
            (
                REPO_FILES,
                """            .where(MissionRecord.anchor_instance_id == anchor_instance_id)
            .where(MissionCommission.state == CommissionState.OUTSTANDING.value)
        )
        if not include_advisory:
            stmt = stmt.where(
                MissionCommission.advisory_backfill.is_(False)  # type: ignore[union-attr]
            )
        if session is not None:
            return len(list(session.exec(stmt)))""",
                """            .where(MissionRecord.anchor_instance_id == anchor_instance_id)
            .where(MissionCommission.state == CommissionState.OUTSTANDING.value)
        )
        if session is not None:
            return len(list(session.exec(stmt)))""",
            ),
        ],
        "pin": (
            "tests/unit/test_mission_record_pr3_cross_epoch_hold.py::"
            "TestAdvisoryExclusionOnTheAnchorScopedRead::"
            "test_an_advisory_backfill_row_never_holds_across_epochs"
        ),
    },
    {
        "id": "H5",
        "desc": (
            "H5 advisory exclusion (SOURCE C list read): "
            "`list_commissions_for_mission` drops the advisory predicate "
            "(mission_record/repository.py :453)"
        ),
        "edits": [
            (
                REPO_FILES,
                """        if states:
            stmt = stmt.where(MissionCommission.state.in_(states))
        if not include_advisory:
            stmt = stmt.where(
                MissionCommission.advisory_backfill.is_(False)  # type: ignore[union-attr]
            )
        stmt = stmt.order_by(MissionCommission.created_at.asc())  # type: ignore[attr-defined]""",
                """        if states:
            stmt = stmt.where(MissionCommission.state.in_(states))
        stmt = stmt.order_by(MissionCommission.created_at.asc())  # type: ignore[attr-defined]""",
            ),
        ],
        "pin": (
            "tests/unit/test_mission_record_pr3_backfill_commit.py::"
            "TestAdvisoryExclusionEverywhere::"
            "test_committed_backfill_rows_are_excluded_from_every_read"
        ),
    },
    # ---------------------------------------------------------------- I ---
    {
        "id": "I1",
        "desc": (
            "I1 D1 routing flip: the COMMISSION-FIRST leg is demoted — "
            "`resolve_report_recipient_with_commission` never consults the "
            "commission (report_recipient.py :309/:327)"
        ),
        "edits": [
            (
                RECIP,
                """    commission = _resolve_commission_leg(engine, child_instance_id)
    if commission is not None:""",
                """    commission = None  # MUTATION: leg 1 (commission-first) demoted
    if commission is not None:""",
            ),
        ],
        "pin": (
            "tests/unit/test_mission_record_pr3_routing_flip.py::"
            "TestTheRoutingFlip::test_enforce_mode_routes_to_the_mission_"
            "commissioner"
        ),
    },
    {
        "id": "I2",
        "desc": (
            "I2 C3 backstop: the CHILD-KEYED admission is removed — "
            "`find_completed_children_without_delivery` drops the "
            "`~has_delivery_row` / `~has_injection_row` guards so a "
            "mission-routed delivery is re-admitted "
            "(report_injection/repository.py ~:1345)"
        ),
        "edits": [
            (
                RINJ,
                """                .where(~has_delivery_row)
                .where(~has_injection_row)
                .where(~has_fired_watcher)""",
                """                .where(~has_fired_watcher)""",
            ),
        ],
        "pin": (
            "tests/unit/test_mission_record_pr3_routing_flip.py::"
            "TestC3CrossParentFirewall"
        ),
    },
    {
        "id": "I2-alt",
        "desc": (
            "I2-alt (SUPPLEMENTARY, literal task anchor): the "
            "`skipped_already_reported` admission is removed from "
            "`_recover_one_no_row` (the queue-side PREFIX ledger branch) — "
            "report_delivery_recovery.py :1856"
        ),
        "edits": [
            (
                RDRS,
                """        if callable(queue_evidence_fn) and queue_evidence_fn(
            parent_id, child_id
        ):
            result.skipped_already_reported += 1
            logger.debug(
                "sweep no_row_backstop skipped_already_reported "
                "(queue-side PREFIX ledger match) "
                f"parent={parent_id[:8]}..., child={child_id[:8]}..."
            )
            return""",
                """        if callable(queue_evidence_fn) and queue_evidence_fn(
            parent_id, child_id
        ):
            logger.debug(
                "sweep no_row_backstop skipped_already_reported "
                "(queue-side PREFIX ledger match) "
                f"parent={parent_id[:8]}..., child={child_id[:8]}..."
            )""",
            ),
        ],
        "pin": (
            "tests/unit/test_report_delivery_recovery_service.py::"
            "TestNoRowBackstopQueuePrefixLedgerSkip"
        ),
    },
    # ---------------------------------------------------------------- J ---
    {
        "id": "J1",
        "desc": (
            "J1 mode surface: the boot mode line is DERANKED — `logger.info` "
            "-> `logger.debug` (manager.py :1054-1058)"
        ),
        "edits": [
            (
                MGR,
                """        if _mission_records_mode != MissionRecordMode.OFF:
            logger.info(
                "mission_records: mode resolved (mode=%s)",
                _mission_records_mode,
            )""",
                """        if _mission_records_mode != MissionRecordMode.OFF:
            logger.debug(
                "mission_records: mode resolved (mode=%s)",
                _mission_records_mode,
            )""",
            ),
        ],
        "pin": (
            "tests/unit/test_mission_record_pr3_backfill_commit.py::"
            "TestTheBootModeLine::test_the_boot_line_is_emitted_at_the_INFO_"
            "level"
        ),
    },
    {
        "id": "J2",
        "desc": (
            "J2 default-dry: the mode ladder's ship default is flipped — "
            "`MissionRecordMode.DRY` -> `MissionRecordMode.ENFORCE` "
            "(mission_record_service.py :228)"
        ),
        "edits": [
            (
                SVC,
                """        if not raw:
            _MISSION_RECORDS_MODE = MissionRecordMode.DRY""",
                """        if not raw:
            _MISSION_RECORDS_MODE = MissionRecordMode.ENFORCE""",
            ),
        ],
        "pin": (
            "tests/unit/test_mission_record_service.py::"
            "TestModeLadder::test_default_is_dry"
        ),
    },
    # ---------------------------------------------------------------- K ---
    {
        "id": "K1",
        "desc": (
            "K1 PR2 carry-over (f2-amend-guard): `_guarded_epoch_update` "
            "amends from (OPEN, SETTLED) instead of (OPEN) "
            "(mission_record/repository.py ~:675)"
        ),
        "edits": [
            (
                REPO_FILES,
                """        return self._guarded_epoch_update(
            mission_id=mission_id,
            values={"founding_message_id": founding_message_id},
            from_statuses=(MissionStatus.OPEN.value,),
            session=session,
        )""",
                """        return self._guarded_epoch_update(
            mission_id=mission_id,
            values={"founding_message_id": founding_message_id},
            from_statuses=(MissionStatus.OPEN.value, MissionStatus.SETTLED.value),
            session=session,
        )""",
            ),
        ],
        "pin": (
            "tests/unit/test_mission_record_pr2_carries.py::"
            "TestF2AmendGuard::test_the_amendment_only_touches_an_open_epoch"
        ),
    },
    {
        "id": "K2",
        "desc": (
            "K2 PR2 carry-over (mission-read-unknown): the MISSION_READ_UNKNOWN "
            "sentinel is replaced by 0 (attestation_gate.py :1546)"
        ),
        "edits": [
            (
                GATE,
                """                elif outstanding_commissions < 0:
                    outstanding_commissions = MISSION_READ_UNKNOWN""",
                """                elif outstanding_commissions < 0:
                    outstanding_commissions = 0""",
            ),
        ],
        "pin": (
            "tests/unit/test_mission_record_pr2_gate_reads.py::"
            "TestFifthR2Input::"
            "test_the_unknown_sentinel_fails_the_evaluation_open"
        ),
    },
]


# --------------------------------------------------------------------------
# plumbing
# --------------------------------------------------------------------------
def child_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(SAFETY_ENV)
    return env


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [GIT, "-C", str(REPO), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def porcelain() -> str:
    return git("status", "--porcelain").stdout.strip()


def run_pytest(node: str) -> tuple[int, str]:
    """Run one pytest scope against the CLONE tree. Returns (exit_code, out)."""
    cmd = [
        TIMEOUT_BIN,
        str(SUBPROCESS_TIMEOUT),
        PYTHON,
        "-m",
        "pytest",
        node,
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT + 60,
        env=child_env(),
    )
    return proc.returncode, (proc.stdout + proc.stderr)


def classify(code: int) -> str:
    if code == 1:
        return "KILLED"
    if code == 0:
        return "SURVIVED"
    return "HARNESS FAULT"


def apply_mutation(m: dict) -> list[str]:
    """Apply every edit; return the list of touched relpaths. Raises on
    ANCHOR MISMATCH (a non-unique or absent anchor is a harness fault,
    never a kill)."""
    touched: list[str] = []
    for rel, old, new in m["edits"]:
        path = REPO / rel
        text = path.read_text()
        count = text.count(old)
        if count != 1:
            raise RuntimeError(
                f"ANCHOR MISMATCH in {rel}: anchor occurs {count}x "
                f"(must be exactly 1) — {old.splitlines()[0][:70]!r}"
            )
        path.write_text(text.replace(old, new, 1))
        touched.append(rel)
    return touched


def revert(paths: list[str]) -> None:
    if paths:
        git("checkout", "--", *paths)


def run_mutation(m: dict, pin_override: str | None = None) -> dict:
    node = pin_override or m["pin"]
    touched: list[str] = []
    started = time.monotonic()
    try:
        touched = apply_mutation(m)
    except RuntimeError as exc:
        return {
            "id": m["id"],
            "desc": m["desc"],
            "pin": node,
            "exit_code": None,
            "verdict": "HARNESS FAULT",
            "detail": str(exc),
            "seconds": round(time.monotonic() - started, 2),
        }
    try:
        code, out = run_pytest(node)
    except subprocess.TimeoutExpired:
        code, out = 124, "HARNESS TIMEOUT (subprocess exceeded the wall budget)"
    finally:
        revert(touched)
    residue = porcelain()
    detail = out[-1200:]
    if residue:
        return {
            "id": m["id"],
            "desc": m["desc"],
            "pin": node,
            "exit_code": code,
            "verdict": "HARNESS FAULT",
            "detail": f"REVERT RESIDUE: {residue}\n{detail}",
            "seconds": round(time.monotonic() - started, 2),
        }
    return {
        "id": m["id"],
        "desc": m["desc"],
        "pin": node,
        "exit_code": code,
        "verdict": classify(code),
        "detail": detail,
        "seconds": round(time.monotonic() - started, 2),
    }


def baseline() -> list[dict]:
    """Run EVERY pin scope unmutated. All must exit 0."""
    seen: dict[str, None] = {}
    for m in MUTATIONS:
        seen.setdefault(m["pin"], None)
    out = []
    for pin in seen:
        started = time.monotonic()
        code, text = run_pytest(pin)
        out.append(
            {
                "pin": pin,
                "exit_code": code,
                "ok": code == 0,
                "seconds": round(time.monotonic() - started, 2),
                "detail": text[-600:] if code != 0 else "",
            }
        )
    return out


def table(rows: list[dict]) -> str:
    head = (
        "| mutation-id | pinned node id | exit code | verdict | seconds |\n"
        "|---|---|---|---|---|\n"
    )
    body = "".join(
        f"| {r['id']} | `{r['pin']}` | {r['exit_code']} | "
        f"**{r['verdict']}** | {r['seconds']} |\n"
        for r in rows
    )
    return head + body


def main(argv: list[str]) -> int:
    probe_only = "--probe" in argv
    probe_matrix = "--probe-matrix" in argv
    json_out = None
    if "--json" in argv:
        json_out = argv[argv.index("--json") + 1]

    if probe_matrix:
        # The DISCRIMINATION PROBE, full-matrix form: ONE mutation's pin is
        # deliberately pointed at a NON-EXISTENT node id. The harness must
        # classify it HARNESS FAULT (pytest exit 4/5) — NOT a kill — and the
        # OVERALL harness must FAIL (non-zero exit).
        bad_pin = (
            "tests/unit/test_mission_record_pr3_routing_flip.py::"
            "TestTheRoutingFlip::test_this_node_does_not_exist_probe"
        )
        print(
            f"DISCRIMINATION PROBE (matrix form): mutation "
            f"{MUTATIONS[0]['id']}'s pin is OVERRIDDEN with:\n  {bad_pin}"
        )
        rows = [run_mutation(m) for m in MUTATIONS]
        rows[0] = run_mutation(MUTATIONS[0], pin_override=bad_pin)
        print(table(rows))
        killed = [r for r in rows if r["verdict"] == "KILLED"]
        survived = [r for r in rows if r["verdict"] == "SURVIVED"]
        faults = [r for r in rows if r["verdict"] == "HARNESS FAULT"]
        print(
            f"KILLED={len(killed)}  SURVIVED={len(survived)}  "
            f"HARNESS FAULT={len(faults)}  (of {len(rows)})"
        )
        probe_ok = rows[0]["exit_code"] in (4, 5) and rows[0]["verdict"] == (
            "HARNESS FAULT"
        )
        print(
            f"probe behaved as required (HARNESS FAULT, not a kill): {probe_ok}"
        )
        print("the overall harness MUST now FAIL — expected exit 1")
        post = porcelain()
        print(f"porcelain AFTER        : {post!r}  (must be EMPTY)")
        if json_out:
            Path(json_out).write_text(
                json.dumps(
                    {"probe_matrix": rows, "probe_ok": probe_ok}, indent=2
                )
            )
        if probe_ok and rows[0]["exit_code"] in (4, 5):
            print("PROBE PASSED — the broken pin is a HARNESS FAULT and the "
                  "harness fails the matrix")
            return 1  # <- the harness FAILS overall, as required
        print("PROBE FAILED")
        return 2

    report: dict = {}

    # ---- porcelain baseline -------------------------------------------
    pre = porcelain()
    print("=" * 78)
    print("PR3 MUTATION HARNESS — the FIXED exit taxonomy (PR2 contract)")
    print("=" * 78)
    print(f"REPO (mutation clone) : {REPO}")
    print(f"PYTHON                 : {PYTHON}")
    print(f"SAFETY ENV             : {SAFETY_ENV}")
    print(f"porcelain BEFORE       : {pre!r}  (must be EMPTY)")
    if pre:
        print("!! clone is dirty before the run — aborting")
        return 2
    report["porcelain_before"] = pre

    # ---- discrimination probe ----------------------------------------
    if probe_only:
        target = MUTATIONS[0]
        bad_pin = (
            "tests/unit/test_mission_record_pr3_routing_flip.py::"
            "TestTheRoutingFlip::test_this_node_does_not_exist_probe"
        )
        print()
        print("### DISCRIMINATION PROBE (harness integrity) ###")
        print(f"mutation {target['id']} pinned at a NON-EXISTENT node id:")
        print(f"  {bad_pin}")
        res = run_mutation(target, pin_override=bad_pin)
        print(f"  raw pytest exit code : {res['exit_code']}")
        print(f"  verdict              : {res['verdict']}")
        print(f"  seconds              : {res['seconds']}")
        ok = res["verdict"] == "HARNESS FAULT" and res["exit_code"] in (4, 5)
        print(f"  probe behaves as required: {ok}")
        report["probe"] = res
        report["probe_ok"] = ok
        post = porcelain()
        print(f"porcelain AFTER        : {post!r}  (must be EMPTY)")
        report["porcelain_after"] = post
        if json_out:
            Path(json_out).write_text(json.dumps(report, indent=2))
        if not ok or post:
            print("PROBE FAILED")
            return 1
        print("PROBE PASSED — a non-existent pin is a HARNESS FAULT, not a kill")
        return 0

    # ---- baseline (unmutated green proof) ------------------------------
    print()
    print("### BASELINE — every pin scope UNMUTATED (all must exit 0) ###")
    base = baseline()
    all_green = True
    for b in base:
        flag = "OK " if b["ok"] else "BAD"
        if not b["ok"]:
            all_green = False
        print(
            f"  {flag} exit={b['exit_code']} {b['seconds']:>6}s  {b['pin']}"
        )
        if not b["ok"]:
            print("      " + b["detail"].replace("\n", "\n      "))
    report["baseline"] = base
    report["baseline_all_green"] = all_green
    if not all_green:
        print("\nBASELINE FAILED — a pin scope is RED before mutation; the "
              "matrix would be meaningless.")
        if json_out:
            Path(json_out).write_text(json.dumps(report, indent=2))
        return 1
    print(f"  baseline: {len(base)}/{len(base)} pin scopes GREEN")

    # ---- the matrix ----------------------------------------------------
    print()
    print("### THE MATRIX ###")
    t0 = time.monotonic()
    rows = [run_mutation(m) for m in MUTATIONS]
    total = time.monotonic() - t0
    print(table(rows))

    killed = [r for r in rows if r["verdict"] == "KILLED"]
    survived = [r for r in rows if r["verdict"] == "SURVIVED"]
    faults = [r for r in rows if r["verdict"] == "HARNESS FAULT"]
    print(
        f"KILLED={len(killed)}  SURVIVED={len(survived)}  "
        f"HARNESS FAULT={len(faults)}  (of {len(rows)})"
    )
    for r in survived:
        print(f"  SURVIVED: {r['id']} — {r['pin']}")
    for r in faults:
        print(f"  HARNESS FAULT: {r['id']} — {r['detail'][:400]}")

    post = porcelain()
    print(f"porcelain AFTER        : {post!r}  (must be EMPTY)")
    report["matrix"] = rows
    report["totals"] = {
        "killed": len(killed),
        "survived": len(survived),
        "harness_fault": len(faults),
        "total": len(rows),
        "matrix_seconds": round(total, 2),
    }
    report["porcelain_after"] = post

    if json_out:
        Path(json_out).write_text(json.dumps(report, indent=2))
        print(f"json report            : {json_out}")

    if survived or faults or post:
        print("\nMUTATION CHECK FAILED")
        return 1
    print(f"\nMUTATION CHECK PASSED — {len(killed)}/{len(rows)} killed "
          f"(matrix wall {total:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
