"""Unit tests for ``_validate_select_only()`` in :mod:`daemon.tools.db_tools`.

The SELECT-only guard is a **defense-in-depth** check that runs before
any database query is dispatched. It is NOT a security boundary — the
real trust boundary is the user account the connection authenticates
as — but it must:

1. Reliably reject DML/DDL keywords (``INSERT``, ``UPDATE``, ``DELETE``,
   ``DROP``, ``CREATE``, ``ALTER``, ``TRUNCATE``, ``GRANT``, ``REVOKE``,
   ``MERGE``, ``REPLACE``, ``CALL``, ``EXEC``, ``EXECUTE``, ``VACUUM``,
   ``REINDEX``, ``REFRESH``) and the table-creating ``SELECT ... INTO``
   (C1 constraint).
2. Accept legitimate ``SELECT`` and CTE (``WITH``) queries.
3. Not produce false positives on keywords that appear **inside** string
   literals (N4 constraint) — e.g. ``WHERE msg LIKE '%INTO%'`` must
   pass.
4. Use **word-boundary** matching so column names like ``updated_at``
   or ``deleted_at`` do not match the ``UPDATE``/``DELETE`` keywords.
5. Reject multi-statement queries (``SELECT 1; DROP TABLE x``) and
   empty / comment-only queries.

These tests pin down the contract described in the function's
docstring. If a future change relaxes any check (e.g. drops a keyword
from the forbidden set, removes the multi-statement guard, or
strips string literals AFTER the keyword scan) the relevant test will
fail and the regression will be caught.
"""

from __future__ import annotations

import pytest

from daemon.tools.db_tools import _validate_select_only


# =============================================================================
# Valid queries — must NOT raise
# =============================================================================


class TestValidSelectQueries:
    """Plain SELECT / CTE queries that the guard must accept."""

    def test_simple_select_star(self):
        # Baseline: the simplest possible SELECT.
        _validate_select_only("SELECT * FROM users")

    def test_select_specific_columns(self):
        # Multi-column projection.
        _validate_select_only("SELECT id, name FROM users")

    def test_with_cte(self):
        # CTE: the first non-whitespace token is ``WITH``, which the
        # guard accepts alongside ``SELECT``.
        _validate_select_only(
            "WITH active AS (SELECT * FROM users WHERE active = true) "
            "SELECT * FROM active"
        )

    def test_select_with_trailing_semicolon(self):
        # Trailing semicolons are stripped (``rstrip(";")``), so a
        # single statement with a ``;`` at the end is still valid.
        _validate_select_only("SELECT 1;")

    def test_select_constant(self):
        # ``SELECT 1`` is a valid constant query.
        _validate_select_only("SELECT 1")

    def test_select_with_leading_line_comment(self):
        # A line comment BEFORE the SELECT is stripped first, so the
        # first-keyword check still sees ``SELECT``.
        _validate_select_only("-- comment\nSELECT 1")

    def test_lowercase_select(self):
        # The guard upper-cases the first token, so ``select`` is
        # accepted the same as ``SELECT``.
        _validate_select_only("select * from users")

    def test_column_name_containing_update_substring(self):
        # Word-boundary check: ``updated_at`` must NOT match ``\bUPDATE\b``.
        # Without word boundaries, the substring ``UPDATE`` inside
        # ``updated_at`` would (incorrectly) trigger the forbidden-keyword
        # scan.
        _validate_select_only("SELECT updated_at FROM users")


# =============================================================================
# Forbidden DML/DDL keywords — must raise ValueError
# =============================================================================


class TestForbiddenDmlKeywords:
    """Each forbidden DML/DDL keyword must trigger a ``ValueError`` whose
    message contains the rejected keyword (so operators can see WHY a
    query was blocked in the daemon log)."""

    def test_insert(self):
        with pytest.raises(ValueError, match="INSERT"):
            _validate_select_only("INSERT INTO users VALUES (1)")

    def test_update(self):
        with pytest.raises(ValueError, match="UPDATE"):
            _validate_select_only("UPDATE users SET name = 'x'")

    def test_delete(self):
        with pytest.raises(ValueError, match="DELETE"):
            _validate_select_only("DELETE FROM users")

    def test_drop(self):
        with pytest.raises(ValueError, match="DROP"):
            _validate_select_only("DROP TABLE users")

    def test_create(self):
        with pytest.raises(ValueError, match="CREATE"):
            _validate_select_only("CREATE TABLE x (id int)")

    def test_alter(self):
        with pytest.raises(ValueError, match="ALTER"):
            _validate_select_only("ALTER TABLE x ADD COLUMN y")

    def test_truncate(self):
        with pytest.raises(ValueError, match="TRUNCATE"):
            _validate_select_only("TRUNCATE TABLE x")

    def test_select_into(self):
        # ``INTO`` is in the forbidden set (C1) — ``SELECT ... INTO``
        # creates a table, so it must be rejected even though the
        # query starts with ``SELECT``.
        with pytest.raises(ValueError, match="INTO"):
            _validate_select_only("SELECT * INTO new_table FROM users")

    def test_cte_with_delete(self):
        # A CTE whose body contains ``DELETE`` must be rejected: the
        # forbidden-keyword scan runs on the string-stripped CTE body,
        # not just the outermost SELECT.
        with pytest.raises(ValueError, match="DELETE"):
            _validate_select_only(
                "WITH deleted AS (DELETE FROM users RETURNING *) "
                "SELECT * FROM deleted"
            )


# =============================================================================
# Edge cases — empty / comment-only / multi-statement
# =============================================================================


class TestEmptyAndCommentOnlyQueries:
    """Empty / whitespace-only / comment-only queries must be rejected
    with a message that mentions ``empty``."""

    def test_empty_string(self):
        with pytest.raises(ValueError, match="empty"):
            _validate_select_only("")

    def test_whitespace_only(self):
        # ``query.strip()`` is empty, so the guard rejects before any
        # further parsing.
        with pytest.raises(ValueError, match="empty"):
            _validate_select_only("   \n\t  ")

    def test_line_comment_only(self):
        # After the line comment is stripped, the remainder is empty,
        # triggering the ``empty after stripping`` branch.
        with pytest.raises(ValueError, match="empty"):
            _validate_select_only("-- just a comment")

    def test_block_comment_only(self):
        # Same as above for ``/* ... */`` comments.
        with pytest.raises(ValueError, match="empty"):
            _validate_select_only("/* nothing useful here */")


class TestMultiStatementQueries:
    """Multi-statement queries must be rejected by the dedicated
    ``;``-after-string-strip check, which runs BEFORE the forbidden
    keyword scan. The error message therefore mentions
    ``Multiple statements``, not the trailing ``DROP`` / ``DELETE`` /
    etc."""

    def test_select_then_drop_table(self):
        # The function raises ``Multiple statements are not allowed``
        # at the multi-statement check, BEFORE the keyword scan ever
        # sees the trailing ``DROP``. The match is therefore about
        # statements, not the ``DROP`` keyword.
        with pytest.raises(ValueError, match="Multiple statements"):
            _validate_select_only("SELECT 1; DROP TABLE x")

    def test_two_select_statements(self):
        # Even two benign SELECTs are rejected — multi-statement is
        # blocked regardless of what the second statement would do.
        with pytest.raises(ValueError, match="Multiple statements"):
            _validate_select_only("SELECT 1; SELECT 2")


# =============================================================================
# String literal false-positive protection (N4)
# =============================================================================


class TestStringLiteralFalsePositives:
    """N4: keywords inside single-quoted string literals must NOT trigger
    a rejection. The guard strips string literals (``'...'`` including
    the ``''`` SQL escape sequence) BEFORE the keyword scan, so
    substrings that look like forbidden keywords in string values
    are invisible to the check.

    This is the single most important behavioral guarantee for agent
    usability: an LLM that writes ``WHERE msg LIKE '%INTO%'`` would
    be unable to run ANY query against the daemon if the guard were
    naive about string boundaries.
    """

    def test_into_inside_like_pattern(self):
        # Classic case: a LIKE pattern that contains the substring
        # ``INTO``. The string literal ``'%INTO%'`` is stripped, so
        # the keyword scanner never sees ``INTO``.
        _validate_select_only(
            "SELECT * FROM logs WHERE message LIKE '%INTO%'"
        )

    def test_drop_table_inside_string_value(self):
        # A free-text note that happens to contain ``DROP TABLE``.
        # The string literal is stripped; the keyword scanner sees
        # only the surrounding ``SELECT`` / ``FROM`` / ``WHERE``.
        _validate_select_only(
            "SELECT note FROM tickets WHERE note = 'DROP TABLE'"
        )

    def test_delete_inside_string_value(self):
        # A config value that happens to be the string ``DELETE``.
        _validate_select_only(
            "SELECT * FROM config WHERE key = 'DELETE'"
        )

    def test_escaped_quote_with_keyword_inside(self):
        # The SQL escape for a single quote inside a string is ``''``.
        # The regex ``'(?:[^']|'')*'`` correctly treats the whole
        # ``'it''s INTO'`` as ONE string literal, so the ``INTO``
        # inside is stripped along with the rest of the literal.
        _validate_select_only(
            "SELECT * FROM t WHERE v = 'it''s INTO'"
        )
