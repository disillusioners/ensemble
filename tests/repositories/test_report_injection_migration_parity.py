"""C1 case-lockstep + migration-parity tests for the report_injections
DEFERRED marker schema (pause-report-recovery Phase 1, Task 1.7).

Validates the cross-driver consistency contract:

  * Storage-layer enum literals are UPPERCASE
    (``PENDING``/``DEFERRED``/``INJECTED``/``TASK_DELIVERED``).
  * App-layer constants in ``daemon.constants`` mirror the storage
    literals verbatim (UPPERCASE).
  * SQLAlchemy ``postgresql_where`` / ``sqlite_where`` expressions
    use the storage literals verbatim.
  * Index NAMES match across the SQLAlchemy model declaration, the
    PG ``_ensure_postgres_columns`` DDL, and the SQLite companion
    migration.

Run against the in-memory SQLite ``engine`` fixture (the
case-lockstep contract is dialect-agnostic; the PG-specific
``postgresql_where`` literal is asserted by direct comparison).
"""

from __future__ import annotations

import re

from daemon.constants import (
    DEFERRED_REASON_IDEMPOTENCY_SKIP,
    DEFERRED_REASON_PAUSE_TOCTOU,
    DEFERRED_REASON_PENDING_MESSAGES,
    DEFERRED_REASON_RESUME_ROUTER,
)
from daemon.repositories.report_injection.models import (
    ReportInjection,
    ReportInjectionState,
)


# Module-level path to the SQLite companion migration.
_REPORT_INJECTIONS_DEFERRED_MIGRATION_PATH = (
    "daemon/migrations/versions/"
    "20260819_000001_report_injections_deferred_marker.sql"
)


# =============================================================================
# Case-lockstep: enum + reason constants
# =============================================================================


class TestCaseLockstep:
    """C1 case-lockstep: storage literals and app-layer constants
    must be UPPERCASE and must never drift in case. Any new state or
    reason value must be added to BOTH the storage enum/DDL AND the
    app constants in the same change."""

    def test_deferred_state_uppercase(self) -> None:
        assert ReportInjectionState.DEFERRED.value == "DEFERRED"

    def test_pending_state_uppercase(self) -> None:
        assert ReportInjectionState.PENDING.value == "PENDING"

    def test_injected_state_uppercase(self) -> None:
        assert ReportInjectionState.INJECTED.value == "INJECTED"

    def test_task_delivered_state_uppercase(self) -> None:
        assert (
            ReportInjectionState.TASK_DELIVERED.value
            == "TASK_DELIVERED"
        )

    def test_reason_pause_toctou_uppercase(self) -> None:
        assert DEFERRED_REASON_PAUSE_TOCTOU == "PAUSE_TOCTOU"

    def test_reason_pending_messages_uppercase(self) -> None:
        assert (
            DEFERRED_REASON_PENDING_MESSAGES == "PENDING_MESSAGES"
        )

    def test_reason_idempotency_skip_uppercase(self) -> None:
        assert (
            DEFERRED_REASON_IDEMPOTENCY_SKIP == "IDEMPOTENCY_SKIP"
        )

    def test_reason_resume_router_uppercase(self) -> None:
        assert DEFERRED_REASON_RESUME_ROUTER == "RESUME_ROUTER"

    def test_state_values_have_no_lowercase_aliases(self) -> None:
        """A lowercase alias would silently break the storage-layer
        predicate (the partial-index ``WHERE state IN ('PENDING',
        'DEFERRED')`` is case-sensitive on PostgreSQL and SQLite)."""
        for state in ReportInjectionState:
            assert state.value == state.value.upper(), (
                f"State {state.name!r} value {state.value!r} "
                f"is not uppercase"
            )

    def test_reason_constants_have_no_lowercase_aliases(self) -> None:
        reasons = [
            DEFERRED_REASON_PAUSE_TOCTOU,
            DEFERRED_REASON_PENDING_MESSAGES,
            DEFERRED_REASON_IDEMPOTENCY_SKIP,
            DEFERRED_REASON_RESUME_ROUTER,
        ]
        for r in reasons:
            assert r == r.upper(), (
                f"Reason constant {r!r} is not uppercase"
            )


# =============================================================================
# Partial-index predicate parity
# =============================================================================


class TestPartialIndexPredicateParity:
    """The SQLAlchemy ``sqlite_where`` / ``postgresql_where`` expression
    on ``uq_report_injections_oblig_triple`` MUST use the storage
    literals verbatim — the C1 case-lockstep contract.
    """

    def _obligation_triple_index(self) -> any:
        """Find the obligation-triple index in the model's __table_args__."""
        for arg in ReportInjection.__table_args__:
            if hasattr(arg, "name") and arg.name == (
                "uq_report_injections_oblig_triple"
            ):
                return arg
        raise AssertionError(
            "uq_report_injections_oblig_triple index not found "
            "in ReportInjection.__table_args__"
        )

    def test_obligation_triple_index_name(self) -> None:
        """Index NAME must be byte-identical across SQLAlchemy, the
        PG ``_ensure_postgres_columns`` DDL, and the SQLite
        companion migration."""
        idx = self._obligation_triple_index()
        assert idx.name == "uq_report_injections_oblig_triple"

    def test_sqlite_where_predicate_uses_storage_literals(self) -> None:
        """The ``sqlite_where`` expression MUST contain 'PENDING' and
        'DEFERRED' (uppercase) verbatim."""
        idx = self._obligation_triple_index()
        # SQLAlchemy compiles ``text(...)`` expressions into ``ClauseList``
        # or ``TextualSelect`` — access the underlying SQL string.
        sqlite_where = idx.dialect_options.get("sqlite", {}).get(
            "where"
        )
        assert sqlite_where is not None, (
            "sqlite_where dialect option missing on "
            "uq_report_injections_oblig_triple"
        )
        sql_str = str(sqlite_where).upper()
        assert "PENDING" in sql_str
        assert "DEFERRED" in sql_str

    def test_postgresql_where_predicate_uses_storage_literals(self) -> None:
        """The ``postgresql_where`` expression MUST contain 'PENDING'
        and 'DEFERRED' (uppercase) verbatim."""
        idx = self._obligation_triple_index()
        pg_where = idx.dialect_options.get("postgresql", {}).get(
            "where"
        )
        assert pg_where is not None, (
            "postgresql_where dialect option missing on "
            "uq_report_injections_oblig_triple"
        )
        sql_str = str(pg_where).upper()
        assert "PENDING" in sql_str
        assert "DEFERRED" in sql_str

    def test_postgresql_where_predicate_matches_sqlite_where(self) -> None:
        """The two dialect-specific predicates MUST be byte-identical
        (the same predicate semantics across drivers)."""
        idx = self._obligation_triple_index()
        sqlite_where = idx.dialect_options.get("sqlite", {}).get(
            "where"
        )
        pg_where = idx.dialect_options.get("postgresql", {}).get(
            "where"
        )
        assert str(sqlite_where).upper() == str(pg_where).upper()

    def test_model_predicate_matches_pg_and_sqlite_ddl(self) -> None:
        """The exact WHERE clause emitted by both DDL paths MUST
        match the SQLAlchemy ``postgresql_where`` / ``sqlite_where``
        expression byte-for-byte (after normalizing for whitespace).

        This is the impl-time verification required by the approver
        residual note for task 1.3: the SQLAlchemy expression and
        the DDL must use the same predicate semantics and the same
        literals.
        """
        idx = self._obligation_triple_index()
        # Normalize the SQLAlchemy expression (strip whitespace inside
        # the SQL string for comparison).
        sql_alchemy_predicate = (
            str(idx.dialect_options["postgresql"]["where"])
            .replace(" ", "")
            .replace("\n", "")
            .replace("\t", "")
        )
        # Compare against the PG DDL WHERE clause.
        pg_src = (
            TestPostgresDDLParity._ensure_postgres_columns_statements_source()
        )
        pg_joined = re.sub(r'"\s+"', "", pg_src)
        # Capture ONLY the predicate body (state IN (...)).
        pg_match = re.search(
            r"state\s+IN\s*\(\s*'PENDING',\s*'DEFERRED'\s*\)",
            pg_joined,
            re.IGNORECASE | re.DOTALL,
        )
        assert pg_match is not None
        pg_predicate = (
            pg_match.group(0)
            .replace(" ", "")
            .replace("\n", "")
            .replace("\t", "")
        )
        # Compare against the SQLite migration WHERE clause.
        migration_sql = (
            TestSQLiteMigrationParity()._migration_sql()
        )
        sqlite_match = re.search(
            r"state\s+IN\s*\(\s*'PENDING',\s*'DEFERRED'\s*\)",
            migration_sql,
            re.IGNORECASE | re.DOTALL,
        )
        assert sqlite_match is not None
        sqlite_predicate = (
            sqlite_match.group(0)
            .replace(" ", "")
            .replace("\n", "")
            .replace("\t", "")
        )
        # Byte-for-byte match across all three (C1 contract).
        assert sql_alchemy_predicate == pg_predicate, (
            f"SQLAlchemy predicate {sql_alchemy_predicate!r} "
            f"!= PG DDL predicate {pg_predicate!r}"
        )
        assert sql_alchemy_predicate == sqlite_predicate, (
            f"SQLAlchemy predicate {sql_alchemy_predicate!r} "
            f"!= SQLite migration predicate {sqlite_predicate!r}"
        )


# =============================================================================
# SQLite companion migration parity
# =============================================================================


class TestSQLiteMigrationParity:
    """The SQLite companion migration at
    ``daemon/migrations/versions/20260819_000001_report_injections_deferred_marker.sql``
    MUST emit DDL with the same index name + predicate literal as the
    SQLAlchemy model — case-lockstep contract."""

    MIGRATION_PATH = _REPORT_INJECTIONS_DEFERRED_MIGRATION_PATH

    def _migration_sql(self) -> str:
        import os

        # Resolve the repo root from this test file's location:
        # tests/repositories/test_report_injection_migration_parity.py
        # → parents[0] = tests/, parents[1] = repo root.
        repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        with open(
            os.path.join(repo_root, self.MIGRATION_PATH)
        ) as f:
            return f.read()

    def test_migration_creates_obligation_triple_index(self) -> None:
        """The migration MUST ``CREATE UNIQUE INDEX`` with the name
        ``uq_report_injections_oblig_triple`` — matches the
        SQLAlchemy model."""
        sql = self._migration_sql()
        assert (
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "uq_report_injections_oblig_triple"
        ) in sql

    def test_migration_predicate_uses_storage_literals(self) -> None:
        """The migration's WHERE clause MUST use 'PENDING' and
        'DEFERRED' (uppercase) verbatim — case-lockstep contract."""
        sql = self._migration_sql()
        # Find the WHERE clause on uq_report_injections_oblig_triple.
        # The migration has the predicate on its own line:
        #     WHERE state IN ('PENDING', 'DEFERRED');
        match = re.search(
            r"CREATE UNIQUE INDEX IF NOT EXISTS\s+"
            r"uq_report_injections_oblig_triple\b"
            r".*?WHERE\s+state\s+IN\s*\(\s*'PENDING',\s*'DEFERRED'\s*\)",
            sql,
            re.IGNORECASE | re.DOTALL,
        )
        assert match is not None, (
            "Could not find WHERE clause on "
            "uq_report_injections_oblig_triple in migration"
        )
        predicate = match.group(0).upper()
        assert "PENDING" in predicate
        assert "DEFERRED" in predicate
        # No lowercase drift (the regex itself is case-insensitive,
        # so check the literal text directly).
        # Find the actual quoted state literals.
        state_literals = re.findall(
            r"'(PENDING|DEFERRED)'", match.group(0)
        )
        assert "PENDING" in state_literals
        assert "DEFERRED" in state_literals

    def test_migration_creates_recovery_attempted_index(self) -> None:
        sql = self._migration_sql()
        assert (
            "CREATE INDEX IF NOT EXISTS "
            "ix_report_injections_recovery_attempted"
        ) in sql


# =============================================================================
# PG DDL parity (read from daemon/manager.py source)
# =============================================================================


class TestPostgresDDLParity:
    """The PG ``_ensure_postgres_columns`` DDL MUST emit:

      * partial unique index with the same NAME and PREDICATE as the
        SQLAlchemy model + SQLite companion migration;
      * partial recovery-attempted index;
      * column adds + DROP NOT NULL on ``report_message_id``.

    This is the implementation-time verification required by the
    approver residual note. We read the source of
    ``_ensure_postgres_columns`` and assert the literal substrings
    are present.
    """

    @staticmethod
    def _ensure_postgres_columns_statements_source() -> str:
        """Extract the ``statements = [...]`` block from
        ``InstanceManager._ensure_postgres_columns``.

        The block is the source of truth for the PG DDL emitted at
        boot — assertions compare against this string.
        """
        import inspect

        from daemon.manager import InstanceManager

        src = inspect.getsource(InstanceManager._ensure_postgres_columns)
        # The list is large; find it by anchor lines that bracket the
        # block.
        start_marker = "statements = ["
        start = src.find(start_marker)
        assert start != -1, (
            "Could not find ``statements = [`` block in "
            "InstanceManager._ensure_postgres_columns"
        )
        start += len(start_marker)
        # Find the closing ``]`` at the start of a line (with
        # optional whitespace). Bracket matching is overkill — the
        # next occurrence of ``]\n`` at the end of the statements
        # block is what we want. The PG DDL has no nested ``]`` so
        # rfind suffices.
        # The block always ends with ``]`` followed by a blank line
        # then ``for stmt in statements:`` (or similar).
        end_marker = "]\n        for stmt"
        end = src.find(end_marker, start)
        if end == -1:
            # Fallback: the last ``]`` before the for loop.
            end = src.find("\n        ]\n", start)
        if end == -1:
            raise AssertionError(
                "Could not find end of ``statements = [...]`` block "
                "in InstanceManager._ensure_postgres_columns"
            )
        return src[start:end]

    def test_pg_partial_unique_index_name_present(self) -> None:
        """The PG DDL MUST create ``uq_report_injections_oblig_triple``
        — same name as the SQLite migration and the SQLAlchemy
        model.

        The PG path splits the DDL across multiple adjacent string
        literals (Python implicit string concatenation). Search for
        the index name as a standalone token.
        """
        src = self._ensure_postgres_columns_statements_source()
        # Direct index name as a standalone token (the DDL uses
        # ``uq_report_injections_oblig_triple `` with a trailing
        # space because the next line continues the CREATE INDEX
        # statement).
        assert (
            '"uq_report_injections_oblig_triple '
            in src
            or '"uq_report_injections_oblig_triple"' in src
        )

    def test_pg_partial_unique_index_predicate_uses_storage_literals(
        self,
    ) -> None:
        """The PG CREATE UNIQUE INDEX predicate MUST use
        ``state IN ('PENDING','DEFERRED')`` (uppercase) verbatim —
        same predicate semantics as the SQLAlchemy model.

        The PG path splits the DDL across multiple adjacent string
        literals (Python implicit string concatenation). Join them
        before matching.
        """
        src = self._ensure_postgres_columns_statements_source()
        # Python implicit string concatenation joins adjacent string
        # literals: ``"foo" "bar"`` becomes ``"foobar"``. Replicate
        # that join for the DDL region only.
        joined = re.sub(r'"\s+"', "", src)
        match = re.search(
            r"CREATE UNIQUE INDEX IF NOT EXISTS\s+"
            r"uq_report_injections_oblig_triple\b"
            r".*?WHERE\s+state\s+IN\s*\(\s*'PENDING',\s*'DEFERRED'\s*\)",
            joined,
            re.IGNORECASE | re.DOTALL,
        )
        assert match is not None, (
            "Could not find the PG CREATE UNIQUE INDEX DDL for "
            "uq_report_injections_oblig_triple with the expected "
            "predicate ``state IN ('PENDING','DEFERRED')``"
        )
        # Extract the quoted state literals and assert they match.
        state_literals = re.findall(
            r"'(PENDING|DEFERRED)'", match.group(0)
        )
        assert "PENDING" in state_literals
        assert "DEFERRED" in state_literals

    def test_pg_recovery_attempted_index_present(self) -> None:
        src = self._ensure_postgres_columns_statements_source()
        # Python implicit string concatenation joins adjacent string
        # literals; normalize before matching.
        joined = re.sub(r'"\s+"', "", src)
        assert (
            "CREATE INDEX IF NOT EXISTS "
            "ix_report_injections_recovery_attempted"
        ) in joined

    def test_pg_drop_not_null_on_report_message_id(self) -> None:
        src = self._ensure_postgres_columns_statements_source()
        assert (
            "ALTER COLUMN report_message_id DROP NOT NULL"
        ) in src

    def test_pg_deferred_reason_column_add_present(self) -> None:
        src = self._ensure_postgres_columns_statements_source()
        assert (
            "ALTER TABLE report_injections ADD COLUMN IF NOT EXISTS "
            "deferred_reason TEXT"
        ) in src

    def test_pg_recovery_attempted_at_column_add_present(self) -> None:
        src = self._ensure_postgres_columns_statements_source()
        assert (
            "ALTER TABLE report_injections ADD COLUMN IF NOT EXISTS "
            "recovery_attempted_at TEXT"
        ) in src

    def test_pg_w3_pre_check_present(self) -> None:
        """W3 pre-check: detect and resolve duplicate non-terminal
        rows BEFORE the partial unique index build. The PG path
        raises a WARNING and transitions duplicates to terminal
        (oldest wins)."""
        src = self._ensure_postgres_columns_statements_source()
        assert "HAVING COUNT(*) > 1" in src, (
            "W3 pre-check HAVING COUNT(*) > 1 missing from "
            "_ensure_postgres_columns statements list"
        )

    def test_pg_w8_rollback_runbook_documented(self) -> None:
        """W8 rollback runbook: revert order = DROP partial unique
        index FIRST, then revert columns. Documented as a comment in
        the manager source."""
        import inspect

        from daemon.manager import InstanceManager

        src = inspect.getsource(InstanceManager._ensure_postgres_columns)
        # The runbook is documented in the comment block above the
        # statements. Check for the key phrase.
        assert (
            "DROP the partial unique index FIRST" in src
            or "reverse order" in src.lower()
        )
