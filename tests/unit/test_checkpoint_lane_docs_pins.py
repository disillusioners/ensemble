"""Pins for the checkpoint lane's two runbooks (SCOPE (c), ITEM 3 + ITEM 4).

``docs/runbooks/checkpoint-volume-bytes.md`` (ITEM 3) — the Longhorn
CRD read path for the weekly DB-VOLUME lane (externally armed, NOT
daemon-core kubectl).

``docs/runbooks/checkpoint-escalation-lane.md`` (ITEM 4) — the proposal
for a SAFE reclaim, with the BINDING promise wording ("relieve growth",
never "return the space") and the measured lock-stall facts that make
the lane operator-nudged rather than automatic.

The pins are mostly DOCUMENT pins because the CONTENT is the
deliverable: a runbook whose thresholds or promise wording drift is
exactly the failure this file exists to catch.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# tests/unit/<file>.py → parents[0]=tests/unit, [1]=tests, [2]=repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
VOLUME_DOC = REPO_ROOT / "docs" / "runbooks" / "checkpoint-volume-bytes.md"
ESCALATION_DOC = REPO_ROOT / "docs" / "runbooks" / "checkpoint-escalation-lane.md"


@pytest.fixture(scope="module")
def volume_doc() -> str:
    assert VOLUME_DOC.exists(), f"missing {VOLUME_DOC}"
    return VOLUME_DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def escalation_doc() -> str:
    assert ESCALATION_DOC.exists(), f"missing {ESCALATION_DOC}"
    return ESCALATION_DOC.read_text(encoding="utf-8")


class TestVolumeBytesRunbook:
    def test_the_primary_signal_is_the_actual_size_ratio(self, volume_doc):
        """``actualSize`` vs ``spec.size`` ≥ 90 % is the PRIMARY signal —
        ``df`` is a secondary corroboration only (the measured 98.4 %
        allocated volume that ``df`` reported as 12G free)."""
        assert "actualSize" in volume_doc
        assert "spec.size" in volume_doc
        assert "90" in volume_doc
        assert "PRIMARY" in volume_doc

    def test_the_abort_thresholds_are_present(self, volume_doc):
        assert "4 GiB" in volume_doc or "4GB" in volume_doc
        assert "99" in volume_doc
        assert "ABORT" in volume_doc

    def test_the_dedicated_minimal_sa_is_documented(self, volume_doc):
        """The lane's SA is READ-ONLY and scoped to ``longhorn-system``
        — no cluster-admin, no ``kubectl exec``."""
        assert "ServiceAccount" in volume_doc
        assert "longhorn-system" in volume_doc
        assert "get" in volume_doc and "list" in volume_doc
        assert "cluster-admin" in volume_doc

    def test_the_doc_states_the_daemon_never_shells_out_to_kubectl(
        self, volume_doc,
    ):
        assert "NEVER shells out to `kubectl`" in volume_doc
        assert "EXTERNALLY armed" in volume_doc

    def test_the_daemon_source_contains_no_kubectl_call(self):
        """NO daemon-side kubectl: the daemon source must not invoke it
        (the lane is the platform's business, not the daemon's)."""
        offenders: list[str] = []
        for path in (REPO_ROOT / "daemon").rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if "kubectl" in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))
        assert offenders == [], (
            f"daemon-side kubectl found in {offenders} — the volume lane "
            "is EXTERNALLY armed"
        )


class TestEscalationLaneProposal:
    def test_the_promise_is_relieve_growth_never_return_the_space(
        self, escalation_doc,
    ):
        """THE BINDING WORDING. The lane's promise is "relieve growth";
        it is NEVER "return the space"."""
        assert "relieve growth" in escalation_doc.lower()
        assert "never" in escalation_doc.lower()
        assert '"return the space"' in escalation_doc or (
            "return the space" in escalation_doc.lower()
        )
        # The forbidden promise must appear only in a NEGATED context.
        for match in re.finditer(r"return the space", escalation_doc.lower()):
            window = escalation_doc[
                max(0, match.start() - 120) : match.end() + 40
            ].lower()
            assert (
                "never" in window
                or "not " in window
                or "only" in window
                or "claim" in window
            ), (
                "the phrase 'return the space' must never appear as an "
                f"un-negated promise: …{window}…"
            )

    def test_the_lock_stall_facts_are_cited(self, escalation_doc):
        """The measured lock-stall facts are cited: no statement/lock
        timeout on the save path, the saver's single asyncio.Lock, the
        pool's max10 + 5 s acquire, and the ACCESS EXCLUSIVE stall."""
        assert "statement_timeout" in escalation_doc
        assert "lock_timeout" in escalation_doc
        assert "asyncio.Lock" in escalation_doc
        assert "ACCESS EXCLUSIVE" in escalation_doc
        assert "checkpoint-lock-stall-behavior" in escalation_doc

    def test_the_decision_reference_is_present(self, escalation_doc):
        assert "decisions.md" in escalation_doc
        assert "§D-4" in escalation_doc

    def test_the_measured_no_downtime_proof_is_recorded(self, escalation_doc):
        """The plain-VACUUM proof: completed 19:32:01Z, the file NOT
        shrunk, the checkpoint writes continuing throughout."""
        assert "19:32:01Z" in escalation_doc
        assert "NOT shrunk" in escalation_doc or "not shrunk" in escalation_doc
        assert "throughout" in escalation_doc.lower()

    def test_the_reclaim_candidates_and_their_status(self, escalation_doc):
        """Plain VACUUM (no-downtime, no shrink promise), pg_repack (the
        no-downtime rewrite path; the install is the Emperor's
        decision), VACUUM FULL (EXCLUDED)."""
        assert "pg_repack" in escalation_doc
        assert "Emperor" in escalation_doc
        assert "VACUUM FULL" in escalation_doc
        assert "EXCLUDED" in escalation_doc

    def test_the_honest_inventory_is_present(self, escalation_doc):
        """GROWTH = retention + churn (this lane); SPACE = one-time
        rewrite (Emperor); VISIBILITY = bytes floor/alert + honest
        receipts."""
        assert "GROWTH" in escalation_doc
        assert "SPACE" in escalation_doc
        assert "VISIBILITY" in escalation_doc
        assert "honest receipts" in escalation_doc.lower()

    def test_the_lane_is_operator_nudged_never_automatic(self, escalation_doc):
        assert "OPERATOR-NUDGED" in escalation_doc or "operator-nudged" in escalation_doc
        assert "never automatic" in escalation_doc.lower()

    def test_the_visibility_signal_is_bytes_not_dead_tuples(
        self, escalation_doc,
    ):
        """The bloat class is dead-tuple-BLIND — the signal is bytes."""
        assert "n_dead_tup" in escalation_doc
        assert "pg_total_relation_size" in escalation_doc
        assert "reltoastrelid" in escalation_doc
