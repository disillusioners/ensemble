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

# =============================================================================
# REPLACE: statement form vs scalar function (2026-10-23 rider fix)
# =============================================================================
#
# ``REPLACE`` is dual-natured: the ``REPLACE INTO`` statement is DML, but
# ``replace(string, from, to)`` is a standard read-only scalar function.
# The generic word-boundary scan matched BOTH, so a valid
# ``SELECT replace(name, 'a', 'b') FROM t`` was falsely rejected by
# ``ens_db_postgres_select`` / ``db_postgres_dml_select``. The guard now
# matches only the statement form (``REPLACE`` NOT immediately followed by
# the function-call open paren).


class TestReplaceFunctionVsStatement:
    """The replace() scalar function is allowed; the REPLACE statement
    form is still rejected (rider fix, 2026-10-23)."""

    # ── Positive needles: legitimate function usage must PASS ──────────────

    def test_replace_function_in_projection(self):
        # The incident query shape: replace() in the SELECT list.
        _validate_select_only("SELECT replace(name, 'a', 'b') FROM projects")

    def test_replace_function_with_whitespace_before_paren(self):
        # PG allows whitespace between a function name and its paren;
        # the statement-form pattern must not fire on it either.
        _validate_select_only("SELECT replace (name, 'a', 'b') FROM projects")

    def test_replace_function_in_where(self):
        _validate_select_only(
            "SELECT id FROM tasks WHERE replace(status, ' ', '_') = 'in_progress'"
        )

    def test_replace_function_nested_with_subquery(self):
        # A scalar subquery INSIDE a replace() call is still a read.
        _validate_select_only(
            "SELECT replace((SELECT note FROM t LIMIT 1), 'x', 'y')"
        )

    def test_replace_inside_string_literal_still_invisible(self):
        # N4 layering: a string containing the whole call is stripped first.
        _validate_select_only(
            "SELECT * FROM t WHERE note = 'replace(a, b, c)'"
        )

    def test_replace_word_substring_in_identifier(self):
        # Word-boundary layering: identifiers containing replace are
        # not keyword matches at all (underscore is a word character).
        _validate_select_only("SELECT * FROM replace_log")
        _validate_select_only("SELECT replaced_at FROM events")

    # ── Negative needles: statement-form REPLACE must STILL be REJECTED ────

    def test_replace_statement_keyword_layer_rejected(self):
        # Statement-form REPLACE (no function-call paren) must hit the
        # keyword layer. The query deliberately contains NO other forbidden
        # keyword, so the raised keyword is deterministically 'REPLACE'
        # (the scan iterates the keyword frozenset in arbitrary order).
        with pytest.raises(ValueError, match="Forbidden keyword 'REPLACE'"):
            _validate_select_only("SELECT 1 WHERE REPLACE = 2")

    def test_replace_into_statement_rejected(self):
        # The realistic DML shape. Either 'REPLACE' or 'INTO' may fire
        # first depending on keyword-set iteration order — the contract
        # under test is that the statement form is REJECTED (never
        # silently allowed by the paren lookahead), so the assertion is
        # deliberately not keyword-pinned.
        with pytest.raises(ValueError, match="Forbidden keyword"):
            _validate_select_only("SELECT REPLACE INTO users SELECT 1")

    def test_bare_trailing_replace_rejected(self):
        # REPLACE not followed by any paren → statement-form pattern fires.
        # Single forbidden token → deterministic keyword in the message.
        with pytest.raises(ValueError, match="Forbidden keyword 'REPLACE'"):
            _validate_select_only("SELECT * FROM t WHERE x = REPLACE")

    # ── Other DML keywords keep the strict treatment (no function carve-out)
    #    — only REPLACE is dual-natured. ─────────────────────────────────────

    @pytest.mark.parametrize("keyword", ["INSERT", "UPDATE", "DELETE", "MERGE"])
    def test_other_dml_keywords_rejected_even_before_paren(self, keyword):
        # These have no read-only scalar namesake; the paren lookahead is
        # deliberately NOT extended to them.
        with pytest.raises(ValueError, match="Forbidden keyword"):
            _validate_select_only(f"SELECT * FROM t WHERE {keyword}(x) = 1")
