"""F1-a pin — the published audit record (db-pool-idle-txn).

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``final-implementation-spec.md`` §1 F1.1 and §5.2.

What this pin asserts: ``audit-findings.md`` exists and carries the
**AST baseline** — the scanner-independent total **779** with the family
counts (A=613 / B=175 / C=4 / D=148 / E=14 / F=0) and **0 unclosed
borrows** — cites ``audit-method.md`` by filename, and marks the
retired **≈689** aggregate NON-CANONICAL.

Plantable mutation: delete ``audit-findings.md``, or restore the
retired "≈689" figure instead of the AST baseline ⇒ RED.

Run (house form)::

    cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
        tests/unit/tools/test_dbpool_audit_record.py --tb=short -q
"""
from __future__ import annotations

import re
from pathlib import Path

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 6

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLAN_DIR = _REPO_ROOT / ".agents" / "shared" / "planning" / "db-pool-idle-txn"
_AUDIT_PATH = _PLAN_DIR / "audit-findings.md"
_METHOD_PATH = _PLAN_DIR / "audit-method.md"


class TestF1aAuditRecord:
    """F1-a — the audit record exists and carries the AST baseline."""

    def test_audit_record_exists(self):
        assert _AUDIT_PATH.exists(), (
            "F1-a: the audit record (audit-findings.md) is missing — the "
            "census headline must be published as an artifact"
        )

    def test_record_carries_the_ast_baseline_total(self):
        text = _AUDIT_PATH.read_text()
        assert "779" in text, (
            "F1-a: the audit record must cite the scanner-independent AST "
            "total (779)"
        )
        assert re.search(r"0\s+unclosed\s+borrows", text, re.IGNORECASE), (
            "F1-a: the audit record must state the 0-unclosed-borrows "
            "finding"
        )

    def test_record_carries_the_family_counts(self):
        text = _AUDIT_PATH.read_text()
        for family, count in (
            ("A", 613),
            ("B", 175),
            ("C", 4),
            ("D", 148),
            ("E", 14),
            ("F", 0),
        ):
            assert str(count) in text, (
                f"F1-a: the family count for family {family} ({count}) is "
                "missing from the audit record"
            )

    def test_record_marks_the_retired_aggregate_non_canonical(self):
        text = _AUDIT_PATH.read_text()
        assert "689" in text, (
            "F1-a: the record must address the retired aggregate "
            "explicitly (it is the figure a reader would otherwise cite)"
        )
        assert re.search(
            r"NON-CANONICAL|non-canonical|RETIRED", text
        ), (
            "F1-a: the ~689 aggregate must be marked NON-CANONICAL / "
            "RETIRED (DR-6) — it is not scanner-independent"
        )

    def test_record_cites_the_method_by_filename(self):
        text = _AUDIT_PATH.read_text()
        assert "audit-method.md" in text, (
            "F1-a: the audit record must cite the method by filename "
            "(audit-method.md)"
        )
        assert _METHOD_PATH.exists(), (
            "F1-a: the cited method artifact (audit-method.md) is missing"
        )

    def test_record_labels_the_five_backend_reconciliation_as_inference(self):
        text = _AUDIT_PATH.read_text()
        assert "INFERENCE" in text.upper(), (
            "F1-a: the five-backend reconciliation must be labelled "
            "INFERENCE — a reproduced fingerprint places the STATEMENT "
            "in the window, not a leak at that site"
        )
        assert re.search(r"audited and CLEAN|audited-and-clean", text, re.IGNORECASE), (
            "F1-a: RCA sites B and C must be recorded as audited-and-clean "
            "with their premise corrections"
        )
