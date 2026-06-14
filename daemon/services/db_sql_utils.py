"""Shared SQL utilities for database tools and pool manager.

Single source of truth for SQL parsing patterns and query constants
used by both :mod:`daemon.tools.db_tools` and
:mod:`daemon.services.db_pool_manager`.

The three regex patterns below are the same patterns previously
duplicated as ``_RE_SINGLE_LINE_COMMENT`` / ``_RE_MULTI_LINE_COMMENT``
/ ``_RE_STRING_LITERAL`` in ``db_tools.py`` and as
``_RE_LIMIT_LINE_COMMENT`` / ``_RE_LIMIT_BLOCK_COMMENT`` /
``_RE_LIMIT_STRING_LITERAL`` in ``db_pool_manager.py``. They define
"what is SQL noise" in a single place so both guards share the same
notion of "inside a string" and "inside a comment".

The constants are likewise single-sourced: both modules need the same
default per-query timeout and default row cap.
"""

from __future__ import annotations

import re


# ── SQL comment and string-literal patterns ──────────────────────────────
# Used for stripping noise before keyword analysis (defense-in-depth).
# These are NOT a complete SQL parser — see module docstring caveats
# in the call sites (db_tools._validate_select_only and
# db_pool_manager._has_limit_clause).

# Single-line SQL comment: ``--`` to end of line.
_RE_SINGLE_LINE_COMMENT = re.compile(r"--[^\n]*")

# Multi-line SQL comment: ``/* ... */`` (DOTALL so ``.`` matches newlines).
_RE_MULTI_LINE_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

# Single-quoted SQL string literal, including the ``''`` SQL escape
# sequence (N4: stripped before keyword scanning to prevent false
# positives on keywords that appear inside string values such as
# ``WHERE msg LIKE '%INTO%'``).
_RE_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")


# ── Query constants ───────────────────────────────────────────────────────

#: Default per-query timeout in seconds (used by
#: :func:`daemon.tools.db_tools.db_postgres_dml_select` and
#: :meth:`daemon.services.db_pool_manager.ConnectionPoolManager.execute_select`).
DEFAULT_QUERY_TIMEOUT: int = 30

#: Default cap on the number of rows returned by ``execute_select``.
#: Callers may override per call.
DEFAULT_MAX_ROWS: int = 1000


def strip_sql_noise(query: str) -> str:
    """Strip SQL comments and single-quoted string literals from a query.

    This is a defense-in-depth helper used before keyword analysis.
    It is NOT a complete SQL parser — exotic constructs (PL/pgSQL
    blocks, dollar-quoted strings, exotic Postgres extensions) are
    not handled. The trust boundary for query safety is the database
    user account, not this helper.

    Strips, in order:

    1. Single-line comments (``--`` to end of line).
    2. Multi-line comments (``/* ... */``).
    3. Single-quoted string literals (``'...'`` including ``''``
       SQL escape sequence), replaced with the literal token ``''``
       so subsequent whitespace tokenisation is preserved.

    Args:
        query: Raw SQL query string.

    Returns:
        Query with comments removed and string literals replaced by
        the empty token ``''``.
    """
    cleaned = _RE_SINGLE_LINE_COMMENT.sub("", query)
    cleaned = _RE_MULTI_LINE_COMMENT.sub("", cleaned)
    cleaned = _RE_STRING_LITERAL.sub("''", cleaned)
    return cleaned
