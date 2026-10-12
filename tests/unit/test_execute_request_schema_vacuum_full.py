"""Cluster 2.1 — ``CheckpointCleanupExecuteRequest`` gains the additive
optional ``vacuum_full: bool | None`` escalation flag (v1.1).

Contract surface (decisions.md §D-2 + §D-7.1 + be-vacuum-full.md
task 2.1):

* the field EXISTS and is OPTIONAL (default ``None`` — absent and
  explicit-``None`` are byte-identical; existing clients keep working
  byte-identically — NO server-side default ``True`` regression);
* the field accepts ``True`` / ``False`` / ``None`` (explicit ``False``
  is allowed for symmetry — the operator can untick the FE checkbox);
* the FROZEN 3 fields (``dry_run_run_id``, ``expected_bytes``,
  ``confirm``) are UNCHANGED in type and required-ness;
* the FROZEN docblock carries the v1.1 amendment note.

The 4 tests per test-plan.md §2.4 (cluster 5 owns the FE mirror of the
same contract; this file is the BE half).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from daemon.routers.schemas import CheckpointCleanupExecuteRequest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = REPO_ROOT / "daemon" / "routers" / "schemas.py"


class TestVacuumFullFieldPresentAndOptional:
    def test_vacuum_full_field_present_and_optional(self):
        """The field exists, defaults to ``None`` (absent == explicit
        None), and the model validates both with all 4 fields set and
        with only the 3 original fields."""
        # Absent → default None, model validates.
        bare = CheckpointCleanupExecuteRequest()
        assert bare.vacuum_full is None
        assert "vacuum_full" in CheckpointCleanupExecuteRequest.model_fields

        # All 4 fields set → validates.
        full = CheckpointCleanupExecuteRequest(
            dry_run_run_id="ckpt-20260927_032000123456-1f4a8c2e",
            expected_bytes=268435456,
            confirm=True,
            vacuum_full=True,
        )
        assert full.vacuum_full is True
        assert full.dry_run_run_id == "ckpt-20260927_032000123456-1f4a8c2e"
        assert full.expected_bytes == 268435456
        assert full.confirm is True


class TestVacuumFullAcceptsBoolTrueFalseNone:
    def test_vacuum_full_accepts_bool_true_false_none(self):
        """``True`` / ``False`` / ``None`` all validate — the explicit
        ``False`` arm is the operator-unticked symmetry case (§D-2)."""
        for value, expected in (
            (True, True),
            (False, False),
            (None, None),
        ):
            got = CheckpointCleanupExecuteRequest(vacuum_full=value)
            assert got.vacuum_full is expected


class TestFrozenFieldsUnchanged:
    def test_frozen_fields_unchanged(self):
        """The 3 FROZEN fields keep their types and required-ness —
        the v1.1 field is purely additive (existing clients keep
        working byte-identically)."""
        fields = CheckpointCleanupExecuteRequest.model_fields
        # Types (annotation source text) unchanged.
        assert "str" in str(fields["dry_run_run_id"].annotation)
        assert "int" in str(fields["expected_bytes"].annotation)
        assert "bool" in str(fields["confirm"].annotation)
        # Required-ness unchanged: all four fields optional (None or
        # explicit default) — the router emits the frozen 400 codes,
        # never FastAPI's bare 422.
        for name in (
            "dry_run_run_id",
            "expected_bytes",
            "confirm",
            "vacuum_full",
        ):
            assert not fields[name].is_required(), (
                f"field {name!r} became required — the FROZEN contract "
                "forbids requiredness changes on CheckpointCleanupExecuteRequest"
            )
        # Defaults unchanged.
        assert fields["dry_run_run_id"].default is None
        assert fields["expected_bytes"].default is None
        assert fields["confirm"].default is False
        assert fields["vacuum_full"].default is None


class TestFrozenDocblockHasV11Note:
    def test_frozen_docblock_has_v1_1_note(self):
        """The class docblock carries the v1.1 amendment note — the
        FROZEN marker stays and the escalation semantics are spelled
        out where the API contract is read (decisions.md §D-2)."""
        tree = ast.parse(SCHEMAS.read_text(encoding="utf-8"))
        docblock: str | None = None
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ClassDef)
                and node.name == "CheckpointCleanupExecuteRequest"
            ):
                docblock = ast.get_docstring(node)
                break
        assert docblock is not None, (
            "CheckpointCleanupExecuteRequest lost its docblock — the "
            "FROZEN contract documentation is load-bearing"
        )
        assert "v1.1" in docblock, (
            "CheckpointCleanupExecuteRequest docblock lost the v1.1 "
            "amendment note (decisions.md §D-2 — the additive optional "
            "vacuum_full escalation flag must be documented in place)"
        )
        assert "vacuum_full" in docblock
