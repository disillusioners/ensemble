#!/usr/bin/env python3
"""Mutation checks — kill-proof the PR2 guards (one mutation per guard).

Each mutation breaks ONE guarded condition in the production code and
asserts that the pinned test goes RED. The mutation is reverted
immediately afterwards (the working tree is restored byte-for-byte).

Usage:  python3 tools/pr2_mutation_check.py
Exit code 0 = every mutation was killed by its pinned test.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# (name, file, old, new, test-file, test-node-id)
MUTATIONS = [
    (
        "census-union",
        "daemon/services/attestation_gate.py",
        """            attestation_required = bool(
                mission_census or delegation_scan.delegation_since_last_user
            )""",
        """            attestation_required = bool(mission_census)""",
        "tests/unit/test_mission_record_pr2_gate_reads.py",
        "TestCensusFlip::test_the_census_never_un_arms_a_real_delegation",
    ),
    (
        "census-record-arm",
        "daemon/services/attestation_gate.py",
        """        if mission_census is None:
            attestation_required = delegation_scan.delegation_since_last_user""",
        """        if mission_census is None:
            attestation_required = False""",
        "tests/unit/test_mission_record_pr2_gate_reads.py",
        "TestCensusFlip::test_the_legacy_scan_is_the_fallback_leg",
    ),
    (
        "source-u-anchor",
        "daemon/services/attestation_gate.py",
        """                mission_anchor_index = find_message_index_by_id(
                    messages, _founding_message_id
                )""",
        """                mission_anchor_index = None""",
        "tests/unit/test_mission_record_pr2_gate_reads.py",
        "TestAnchorFlip::test_source_u_is_the_record_anchor",
    ),
    (
        "r2-unknown-sentinel",
        "daemon/services/attestation_gate.py",
        """                elif outstanding_commissions < 0:
                    outstanding_commissions = MISSION_READ_UNKNOWN""",
        """                elif outstanding_commissions < 0:
                    outstanding_commissions = 0""",
        "tests/unit/test_mission_record_pr2_gate_reads.py",
        "TestFifthR2Input::test_the_unknown_sentinel_fails_the_evaluation_open",
    ),
    (
        "r2-pending-arm",
        "daemon/services/attestation_gate.py",
        """        or live_descendants > 0
        or outstanding_commissions > 0
    ):""",
        """        or live_descendants > 0
    ):""",
        "tests/unit/test_mission_record_pr2_gate_reads.py",
        "TestFifthR2Input::test_an_outstanding_commission_holds_the_mission",
    ),
    (
        "quiet-predicate",
        "daemon/services/attestation_resolver_activation.py",
        """        and c_signals.outstanding_commissions == 0
    )""",
        """    )""",
        "tests/unit/test_mission_record_pr2_gate_reads.py",
        "TestQuietPredicate::test_quiet_requires_zero_outstanding_commissions",
    ),
    (
        "l3-mission-leg",
        "daemon/services/child_reports.py",
        """            if mission_commissions > 0:
                return False, f"mission_commissions={mission_commissions}", False""",
        """            if mission_commissions < 0:
                return False, f"mission_commissions={mission_commissions}", False""",
        "tests/unit/test_mission_record_pr2_l3_ledger.py",
        "TestL3MissionLeg::test_an_outstanding_commission_holds_the_publish",
    ),
    (
        "f1-open-epoch-uniqueness",
        "daemon/repositories/mission_record/models.py",
        """        Index(
            "ux_mission_records_anchor_open",
            "anchor_instance_id",
            unique=True,
            sqlite_where=text(OPEN_EPOCH_STATUS_SQL),
            postgresql_where=text(OPEN_EPOCH_STATUS_SQL),
        ),""",
        """        Index(
            "ux_mission_records_anchor_open",
            "anchor_instance_id",
            unique=False,
            sqlite_where=text(OPEN_EPOCH_STATUS_SQL),
            postgresql_where=text(OPEN_EPOCH_STATUS_SQL),
        ),""",
        "tests/unit/test_mission_record_pr2_carries.py",
        "TestF1OpenEpochUniqueness::test_a_second_open_epoch_is_rejected",
    ),
    (
        "f1-open-epoch-index-name",
        "daemon/repositories/mission_record/models.py",
        """            "ux_mission_records_anchor_open",""",
        """            "ux_mission_records_anchor_open_renamed",""",
        "tests/unit/test_mission_record_pr2_carries.py",
        "TestF1ThreeSitePin::test_the_model_declares_the_same_index_and_predicate",
    ),
    (
        "f2-amend-guard",
        "daemon/repositories/mission_record/repository.py",
        """            .where(MissionRecord.mission_id == mission_id)
            .where(MissionRecord.status == MissionStatus.OPEN.value)
            .values(founding_message_id=founding_message_id)""",
        """            .where(MissionRecord.mission_id == mission_id)
            .values(founding_message_id=founding_message_id)""",
        "tests/unit/test_mission_record_pr2_carries.py",
        "TestF2AmendGuard::test_the_amendment_only_touches_an_open_epoch",
    ),
    (
        "amend-at-delivery",
        "daemon/services/instance_messaging.py",
        """        if not is_p1_human or not message_id or not instance_id:
            return""",
        """        if True:
            return""",
        "tests/unit/test_mission_record_pr2_tap_amend.py",
        "TestAmendAtDelivery::test_a_p1_human_delivery_amends_the_anchor",
    ),
    (
        "epoch-boundary-clear",
        "daemon/services/mission_record_service.py",
        """        if not mission_records_enforce_enabled():
            return False
        if not anchor_instance_id:
            return False""",
        """        if True:
            return False
        if not anchor_instance_id:
            return False""",
        "tests/unit/test_mission_record_pr2_l3_ledger.py",
        "TestEpochBoundaryLedgerClear::test_a_new_epoch_clears_the_ledger",
    ),
    (
        "epoch-tag-shape",
        "daemon/repositories/instance/repository.py",
        """            if mission_id:
                seen_epochs.append(
                    {"epoch": denial_epoch, "mission_id": mission_id}
                )""",
        """            if False:
                seen_epochs.append(
                    {"epoch": denial_epoch, "mission_id": mission_id}
                )""",
        "tests/unit/test_mission_record_pr2_l3_ledger.py",
        "TestEpochTaggedDenyLedger::test_a_replay_within_the_epoch_is_swallowed",
    ),
    (
        "ledger-alias-forwarding",
        "daemon/repositories/instance/repository.py",
        """        return self.increment_attestation_denied_count(
            instance_id, denial_epoch, mission_id
        )""",
        """        return self.increment_attestation_denied_count(
            instance_id, denial_epoch
        )""",
        "tests/unit/test_mission_record_pr2_ledger_forwarding.py",
        "TestTheAliasForwardsTheEpochTag::test_safe_increment_with_a_mission_tag_writes_the_tagged_entry",
    ),
    (
        "mode-gate-single-definition",
        "daemon/services/mission_record_reads.py",
        """# The mode ladder has ONE owner: ``mission_record_service``. The gate""",
        """def mission_records_enforce_enabled() -> bool:
    return False


# The mode ladder has ONE owner: ``mission_record_service``. The gate""",
        "tests/unit/test_mission_record_pr2_ledger_forwarding.py",
        "TestTheModeGateHasOneDefinition::test_the_reads_module_does_not_shadow_the_gate",
    ),
    (
        "amend-not-nested-in-the-tap-guard",
        "daemon/services/instance_messaging.py",
        """        await self._amend_mission_anchor_at_delivery(
            instance_id=instance_id,
            message_id=message_id,
            is_p1_human=is_fresh_episode_user_message,
            mission_id=mission_id,
        )""",
        """        if self._manager.message_metadata_repo is not None:
            await self._amend_mission_anchor_at_delivery(
                instance_id=instance_id,
                message_id=message_id,
                is_p1_human=is_fresh_episode_user_message,
                mission_id=mission_id,
            )""",
        "tests/unit/test_mission_record_pr2_tap_amend.py",
        "TestTheAmendmentIsNotNestedInTheTapGuard::test_the_amend_call_is_not_under_the_metadata_repo_guard",
    ),
    (
        "amend-anchor-instance-guard",
        "daemon/services/instance_messaging.py",
        """            if anchor_instance_id != instance_id:""",
        """            if False:""",
        "tests/unit/test_mission_record_pr2_amend_guard.py",
        "TestTheAnchorInstanceGuard::test_a_foreign_mission_does_not_re_found_the_epoch",
    ),
    (
        "c-union-commission-leg",
        "daemon/services/attestation_resolver_activation.py",
        """            tree_ids = manager.get_tree_ids_permanent(instance_id)
            tree_ids = [str(i) for i in (tree_ids or []) if i]
            if not tree_ids:
                return rows""",
        """            tree_ids = manager.get_tree_ids_permanent(instance_id)
            tree_ids = [str(i) for i in (tree_ids or []) if i]
            if not tree_ids:
                return []""",
        "tests/unit/test_mission_record_pr2_gate_reads.py",
        "TestSourceCCommissionUnion::test_the_out_of_tree_commission_is_visible_in_c",
    ),
    (
        "c-union-fail-open-log",
        "daemon/services/attestation_resolver_activation.py",
        """        logger.warning(
            "attestation resolver: SOURCE C commission union failed for "
            "%s… (%s) — fail-OPEN (counts-only C evidence)",
            (instance_id or "?")[:8],
            type(union_exc).__name__,
        )
        return []""",
        """        return []""",
        "tests/unit/test_mission_record_pr2_gate_reads.py",
        "TestSourceCCommissionUnion::test_a_union_read_failure_is_logged_and_fails_open",
    ),
    (
        "l3-fail-open-log",
        "daemon/services/child_reports.py",
        """            logger.warning(
                "root_completion_gate: mission-commission count failed for "
                "%s… (%s) — fail-OPEN, the publish proceeds (the leg "
                "cannot hold on an unknown count)",
                instance_id[:8],
                type(count_exc).__name__,
            )
            return -1""",
        """            return -1""",
        "tests/unit/test_mission_record_pr2_l3_ledger.py",
        "TestL3MissionLeg::test_the_fail_open_is_logged",
    ),
    (
        "facade-getter-forwarding",
        "daemon/manager.py",
        """        return _read_mission_anchor_instance_id(self._engine, mission_id)""",
        """        return _read_mission_anchor_instance_id(self._engine, "other")""",
        "tests/unit/test_mission_record_pr2_amend_guard.py",
        "TestTheManagerFacadeGetters::test_get_mission_anchor_instance_id_forwards_its_argument",
    ),
    (
        "epoch-tag-scope",
        "daemon/repositories/instance/repository.py",
        """            if entry.get("mission_id") == mission_id:
                return True""",
        """            if True:
                return True""",
        "tests/unit/test_mission_record_pr2_l3_ledger.py",
        "TestEpochTaggedDenyLedger::test_the_same_fingerprint_under_a_new_epoch_counts",
    ),
]


def _run(args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        args,
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=600,
    )
    return proc.returncode, proc.stdout + proc.stderr


def main() -> int:
    failures: list[str] = []
    for name, rel_path, old, new, test_file, node in MUTATIONS:
        path = REPO / rel_path
        original = path.read_text()
        if original.count(old) != 1:
            failures.append(
                f"{name}: the mutation anchor is NOT unique in {rel_path} "
                f"(count={original.count(old)}) — the mutation did not run"
            )
            continue
        path.write_text(original.replace(old, new))
        try:
            code, output = _run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    f"{test_file}::{node}",
                    "-q",
                    "--no-header",
                    "-p",
                    "no:cacheprovider",
                ]
            )
        finally:
            path.write_text(original)
        if code == 0:
            failures.append(
                f"{name}: the mutation SURVIVED — {test_file}::{node} "
                f"still passed"
            )
            print(f"  ✗ {name}: NOT KILLED")
        else:
            print(f"  ✓ {name}: killed by {test_file}::{node}")
    print()
    if failures:
        print("MUTATION CHECK FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"MUTATION CHECK PASSED — {len(MUTATIONS)}/{len(MUTATIONS)} killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
