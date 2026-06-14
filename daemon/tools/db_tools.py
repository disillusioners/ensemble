"""Database tools for external database connection management and querying.

This module is the **tool-layer** integration point of the Database Tool
Category feature. It connects Phase 1 (:class:`DbConnectionRepository`,
the CRUD registry) and Phase 2 (:class:`ConnectionPoolManager`, the
singleton asyncpg pool owner) into agent-usable LangChain tools
registered under the ``"db"`` category.

Architecture boundaries (do not cross):

* **Encryption / decryption boundary** — the tool layer encrypts the
  password at registration time (N1) and the pool manager decrypts at
  pool-creation time. The repository stores opaque strings only.
* **Factory boundary** — :func:`create_db_tools` receives the shared
  repository and pool manager as parameters (C3, D5). It does **not**
  instantiate its own ``CredentialManager``, ``DbConnectionRepository``,
  or ``ConnectionPoolManager``. This prevents pool proliferation across
  instances.
* **SELECT-only enforcement** — :func:`_validate_select_only` is a
  defense-in-depth guard, not a security boundary. It strips string
  literals (N4) to avoid false positives on keywords that appear inside
  string values (e.g. ``WHERE msg LIKE '%INTO%'``).
* **Error sanitization** — every tool catches exceptions and returns an
  error string instead of raising (N9). The pool manager's
  ``_sanitize_error`` strips credentials from any messages that bubble
  out of asyncpg.

Tool functions created by this module:

* ``db_conn_add`` — Register a named connection (encrypts the password).
* ``db_conn_delete`` — Remove a connection and dispose its pool.
* ``db_conn_list`` — List all connections without secrets.
* ``db_conn_test`` — Test a connection via ``SELECT 1``.
* ``db_postgres_dml_select`` — Run a read query with a SELECT guard.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from langchain_core.tools import tool
from sqlalchemy.exc import IntegrityError

from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from daemon.manager import InstanceManager
    from daemon.repositories.db_connection.repository import DbConnectionRepository
    from daemon.services.db_pool_manager import ConnectionPoolManager
    from daemon.sources.credentials import CredentialManager


logger = logging.getLogger(__name__)


CATEGORY_NAME = "Database"
CATEGORY_DOC = """\
Manage external database connections and run queries.

**Connection Management:**
- `db_conn_add` — Register a named database connection
- `db_conn_delete` — Remove a connection and dispose its pool
- `db_conn_list` — List all saved connections
- `db_conn_test` — Test that a connection works

**Query Execution:**
- `db_postgres_dml_select` — Run a SELECT query (read-only)
"""


# Default per-query timeout in seconds (used by db_postgres_dml_select).
DEFAULT_QUERY_TIMEOUT: int = 30

# Default row cap for db_postgres_dml_select results.
DEFAULT_MAX_ROWS: int = 1000


# Forbidden SQL keywords (defense-in-depth, not a security boundary).
# ``INTO`` is included to block ``SELECT ... INTO`` which creates a table
# (C1). The remaining keywords are DML/DDL/admin operations that an
# agent-facing read tool must never execute.
_FORBIDDEN_KEYWORDS: frozenset[str] = frozenset({
    "INSERT", "INTO", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
    "TRUNCATE", "GRANT", "REVOKE", "MERGE", "REPLACE", "CALL",
    "EXEC", "EXECUTE", "VACUUM", "REINDEX", "REFRESH",
})


# Pre-compiled regexes for the SELECT-only guard. Compiling once at
# import time is cheaper than rebuilding on every call.
_RE_SINGLE_LINE_COMMENT = re.compile(r"--[^\n]*")
_RE_MULTI_LINE_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
# Single-quoted string literals, including the '' SQL escape sequence.
# N4: stripped before keyword scanning to prevent false positives on
# keywords that appear inside string values.
_RE_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")


def _validate_select_only(query: str) -> None:
    """Validate that a SQL query is SELECT-only.

    Defense-in-depth guard. Not a complete SQL parser — it cannot catch
    every possible DML/DDL construct (e.g. exotic Postgres extensions
    or PL/pgSQL blocks). The trust boundary is the user account the
    connection authenticates as, not this guard.

    Checks (in order):

    1. Strip ``--`` single-line and ``/* */`` multi-line comments.
    2. Strip trailing semicolons / surrounding whitespace.
    3. First keyword must be ``SELECT`` or ``WITH`` (for CTEs).
    4. Strip single-quoted string literals (N4) so that keywords
       appearing inside string values do not produce false positives
       (e.g. ``WHERE msg LIKE '%INTO%'``).
    5. Reject any remaining semicolons — this blocks multi-statement
       queries such as ``SELECT 1; SELECT 2`` or ``SELECT 1; DROP TABLE
       x``. The check runs AFTER string-literal stripping so that a
       semicolon inside a string value (e.g. ``WHERE x = ';'``) is not
       treated as a statement separator. The trade-off — checking
       before string-literal stripping would be slightly "safer" if
       the string-literal regex ever failed — is accepted because the
       trust boundary is the database user, not this guard, and
       rejecting legitimate queries is a worse outcome than the
       theoretical bypass.
    6. Scan the string-stripped query for forbidden DML/DDL keywords
       using word-boundary, case-insensitive matching.

    Args:
        query: The SQL query text to validate.

    Raises:
        ValueError: If the query is empty, does not start with
            ``SELECT``/``WITH``, contains multiple statements, or
            contains a forbidden keyword.
    """
    if not query or not query.strip():
        raise ValueError("Query cannot be empty")

    # 1 + 2: Strip comments, then trailing semicolons and whitespace.
    cleaned = _RE_SINGLE_LINE_COMMENT.sub("", query)
    cleaned = _RE_MULTI_LINE_COMMENT.sub("", cleaned)
    cleaned = cleaned.strip().rstrip(";").strip()

    if not cleaned:
        raise ValueError("Query is empty after stripping comments")

    # 3: First non-whitespace token must be SELECT or WITH.
    first_word = cleaned.split()[0].upper()
    if first_word not in ("SELECT", "WITH"):
        raise ValueError(
            f"Only SELECT queries are allowed. "
            f"Query starts with '{first_word}'."
        )

    # 4: Strip string literals (N4) before checking for multi-statement
    # AND before scanning for keywords. This prevents false positives
    # on semicolons and keywords that appear inside string values.
    cleaned_no_strings = _RE_STRING_LITERAL.sub("''", cleaned)

    # 5: Block multi-statement queries (defense-in-depth).
    # Runs after string-literal stripping so that ``WHERE x = ';'`` is
    # not treated as ``WHERE x = '';`` plus an empty statement. The
    # alternative — checking before string-literal stripping — would
    # reject legitimate queries that contain semicolons inside string
    # literals, which is a worse trade-off for agent usability.
    if ";" in cleaned_no_strings:
        raise ValueError(
            "Multiple statements are not allowed. "
            "Submit one statement at a time."
        )

    # 6: Word-boundary scan for forbidden keywords.
    for keyword in _FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{keyword}\b", cleaned_no_strings, re.IGNORECASE):
            raise ValueError(
                f"Forbidden keyword '{keyword}' detected. "
                f"Only SELECT queries are allowed."
            )


def _format_conn_row(row: dict[str, Any]) -> str:
    """Format a single public-dict connection row as a markdown table row.

    Args:
        row: The output of ``DbConnectionConfig.to_public_dict()``.

    Returns:
        A ``| col1 | col2 | ... |`` line.
    """
    cells = [
        str(row.get("connection_name", "")),
        str(row.get("db_type", "")),
        str(row.get("host", "")),
        "" if row.get("port") is None else str(row.get("port")),
        str(row.get("database") or ""),
        str(row.get("username") or ""),
        "yes" if row.get("has_password") else "no",
        str(row.get("ssl_mode", "")),
    ]
    return "| " + " | ".join(cells) + " |"


def create_db_tools(
    manager: "InstanceManager",
    current_instance_id: str,
    repository: "DbConnectionRepository",
    pool_manager: "ConnectionPoolManager",
) -> list:
    """Create database tools with injected shared services.

    The factory receives the shared ``DbConnectionRepository`` and
    :class:`ConnectionPoolManager` from the :class:`InstanceManager`
    (C3, D5). It does **not** instantiate its own
    ``CredentialManager``, ``DbConnectionRepository``, or
    ``ConnectionPoolManager`` — those are constructed once at the
    manager level and passed in. This prevents pool proliferation
    across instances (C3) and keeps the Fernet key handle shared.

    The factory also pulls :class:`CredentialManager` from the manager
    (``manager.credential_manager``) for the ``db_conn_add`` tool's
    N1 encryption step. The pool manager already holds a private
    reference to the same credential manager for pool creation.

    Args:
        manager: The :class:`InstanceManager` instance. Used for its
            ``credential_manager`` property (N1/D8: injected from
            ``app.state``).
        current_instance_id: The current instance ID. Accepted for
            parity with other factories but unused by these tools
            (the pool manager and repository are process-level).
        repository: Shared :class:`DbConnectionRepository` from
            ``manager.db_connection_repository``.
        pool_manager: Shared :class:`ConnectionPoolManager` from
            ``manager.db_pool_manager``.

    Returns:
        A list of 5 tool functions:
        ``[db_conn_add, db_conn_delete, db_conn_list, db_conn_test,
        db_postgres_dml_select]``.
    """
    # N1: pull the shared credential manager from the manager for
    # in-tool encryption. The pool manager holds a private reference
    # to the same object for decryption, so they are guaranteed to
    # use the same Fernet key.
    credential_manager: "CredentialManager" = manager.credential_manager

    @register_tool_category("db")
    @tool
    async def db_conn_add(
        connection_name: str,
        db_type: str,
        host: str,
        port: int | None = None,
        database: str | None = None,
        username: str | None = None,
        password: str | None = None,
        ssl_mode: str = "prefer",
    ) -> str:
        """Register a named database connection. Passwords are encrypted at the tool layer."""
        try:
            # F1: refuse to register a connection with a password when
            # Fernet encryption is not configured. Storing plaintext
            # credentials defeats the whole point of the encrypted column.
            credentials: str | None = None
            if password:
                if not credential_manager.is_encryption_available():
                    return (
                        "ERROR: Credential encryption is not configured. "
                        "Set SOURCE_CREDENTIAL_KEY and install the cryptography "
                        "package before registering connections with passwords."
                    )
                # N1: Encrypt the password HERE in the tool before it ever
                # touches the repository. The repository receives an opaque
                # encrypted string and never sees the plaintext.
                credentials = credential_manager.encrypt({"password": password})

            config = repository.create(
                connection_name=connection_name,
                db_type=db_type,
                host=host,
                port=port,
                database=database,
                username=username,
                credentials=credentials,
                ssl_mode=ssl_mode,
            )

            # Confirmation message NEVER includes the password. The
            # ``has_password`` flag tells the caller whether a credential
            # was registered without leaking its value.
            return (
                f"Created db connection: name={config.connection_name}, "
                f"db_type={config.db_type}, host={config.host}, "
                f"has_password={config.credentials is not None}."
            )
        except IntegrityError:
            # F2: the SQLAlchemy IntegrityError message includes the bound
            # parameters, which would expose the plaintext password in the
            # tool output. Replace it with a fixed, safe message.
            return f"ERROR: A db connection named '{connection_name}' already exists."
        except Exception as exc:
            # N9: catch all exceptions and return an error string. The
            # plaintext password must not appear in any error message —
            # asyncpg / SQLAlchemy error text generally does not include
            # tool input, but the explicit omission is documented in the
            # constraint. Use only the exception class name — bound
            # parameters can leak credentials via str(exc).
            return (
                f"ERROR: Failed to add db connection '{connection_name}' "
                f"({type(exc).__name__})."
            )

    db_conn_add._full_doc_ = """Register a named database connection.

The password (if supplied) is encrypted at the tool layer via the
shared CredentialManager before the row is persisted. The repository
stores an opaque encrypted string and never sees the plaintext.

Args:
    connection_name: Unique name for the connection (e.g.
        "analytics_warehouse"). Re-using an existing name raises.
    db_type: Database driver type. Currently only "postgres" is
        supported by the pool manager.
    host: Database host.
    port: Optional database port (default 5432 for postgres).
    database: Optional database / schema name.
    username: Optional database username.
    password: Optional database password. Encrypted before storage.
    ssl_mode: SSL mode string. Defaults to "prefer".

Returns:
    Confirmation string with connection metadata. The password is
    NEVER included in the response.
"""

    @register_tool_category("db")
    @tool
    async def db_conn_delete(connection_name: str) -> str:
        """Delete a connection and dispose its pool. Use tool_help("db_conn_delete") for details."""
        try:
            deleted = repository.delete(connection_name)
            # Dispose the pool even if the repository delete returned
            # False (no row): if a pool somehow lingers for a stale
            # config row, dispose is idempotent and safe.
            await pool_manager.dispose(connection_name)
            if not deleted:
                return (
                    f"No db connection named '{connection_name}' to delete. "
                    f"Pool dispose attempted (no-op if not present)."
                )
            return f"Deleted db connection: {connection_name} (pool disposed)."
        except Exception as exc:
            return f"ERROR: Failed to delete db connection '{connection_name}': {exc}"

    db_conn_delete._full_doc_ = """Delete a named database connection and dispose its pool.

The repository row is removed first, then the connection pool (if
any) is closed via ``pool_manager.dispose``. ``dispose`` is
idempotent — calling it on an unknown name is a no-op.

Args:
    connection_name: The unique name of the connection to remove.

Returns:
    Confirmation string. If no row existed, the message says so
    and reports that the pool dispose was a no-op.
"""

    @register_tool_category("db")
    @tool
    async def db_conn_list() -> str:
        """List all saved database connections. Secrets are omitted. Use tool_help("db_conn_list") for details."""
        try:
            rows = repository.list_public()
            if not rows:
                return "No db connections registered. Use db_conn_add to create one."

            # Render as a markdown-style table. Columns: name, type,
            # host, port, database, username, has_password, ssl_mode.
            header = (
                "| connection_name | db_type | host | port | database | "
                "username | has_password | ssl_mode |"
            )
            divider = "|---|---|---|---|---|---|---|---|"
            lines = [
                f"Registered db connections ({len(rows)}):",
                "",
                header,
                divider,
            ]
            for row in rows:
                lines.append(_format_conn_row(row))
            return "\n".join(lines)
        except Exception as exc:
            return f"ERROR: Failed to list db connections: {exc}"

    db_conn_list._full_doc_ = """List all registered database connections.

The result is rendered as a markdown table. The ``credentials`` field
is never included — only the boolean ``has_password`` flag is shown
to indicate whether a password is set.

Returns:
    A markdown table with one row per connection. If no connections
    are registered, returns a guidance message.
"""

    @register_tool_category("db")
    @tool
    async def db_conn_test(connection_name: str) -> str:
        """Test a database connection. Use tool_help("db_conn_test") for details."""
        try:
            # test_connection is non-throwing — it always returns a dict.
            # The pool manager sanitizes the message before returning it
            # (defense-in-depth against credential leaks from asyncpg /
            # libpq error text).
            result = await pool_manager.test_connection(connection_name)
            success = result.get("success", False)
            message = result.get("message", "no message returned")
            if success:
                return f"OK: {message}"
            return f"FAIL: {message}"
        except Exception as exc:
            # N9: catch any unexpected exception (e.g. ValueError for
            # unknown connection name) and return an error string. The
            # pool manager's own message for the unknown-name case is
            # "Connection '...' not found", so this branch should be
            # rare in practice.
            return f"ERROR: Failed to test db connection '{connection_name}': {exc}"

    db_conn_test._full_doc_ = """Test a database connection by running ``SELECT 1``.

The pool manager runs the probe and returns a structured
``{success, message}`` dict. The tool renders the message as either
``OK: ...`` or ``FAIL: ...`` and the message has been sanitized to
strip any credentials that may have leaked from asyncpg / libpq.

Args:
    connection_name: The name of the connection to test.

Returns:
    A status string starting with ``OK:`` or ``FAIL:``. Never raises.
"""

    @register_tool_category("db")
    @tool
    async def db_postgres_dml_select(
        connection_name: str,
        query: str,
        timeout: int = DEFAULT_QUERY_TIMEOUT,
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> str:
        """Run a read-only SELECT query against a PostgreSQL connection. Use tool_help("db_postgres_dml_select") for details."""
        try:
            # SELECT-only guard runs BEFORE any network I/O so that a
            # forbidden query never reaches the database.
            _validate_select_only(query)
        except ValueError as exc:
            return f"ERROR: {exc}"

        try:
            result = await pool_manager.execute_select(
                connection_name=connection_name,
                query=query,
                timeout=timeout,
                max_rows=max_rows,
            )
        except Exception as exc:
            # N9: any exception from the pool manager (unknown name,
            # non-postgres db_type, pool creation failure, query
            # timeout, server error) is caught here and rendered as
            # an error string. The pool manager's message is already
            # sanitized, but we add a prefix so the agent knows the
            # tool layer observed the failure.
            return f"ERROR: Query failed on '{connection_name}': {exc}"

        columns: list[str] = result.get("columns", []) or []
        rows: list[dict[str, Any]] = result.get("rows", []) or []
        row_count: int = int(result.get("row_count", len(rows)))
        truncated: bool = bool(result.get("truncated", False))

        # Empty result set — still a success, but the table format
        # below would be a divider row on its own. Return a friendlier
        # message instead.
        if row_count == 0:
            return (
                f"Query executed successfully on '{connection_name}'. "
                f"No rows returned."
            )

        # Markdown table: column widths are sized to the longer of
        # the header text or the longest cell value in that column.
        # ``str(...)`` is used so non-string values (int, decimal,
        # datetime) coerce cleanly. None renders as the empty string
        # to avoid the literal "None" leaking into agent output.
        widths: list[int] = [len(col) for col in columns]
        str_rows: list[list[str]] = []
        for row in rows:
            cells = ["" if row.get(col) is None else str(row.get(col)) for col in columns]
            str_rows.append(cells)
            for i, cell in enumerate(cells):
                if len(cell) > widths[i]:
                    widths[i] = len(cell)

        def _render(cells: list[str]) -> str:
            return "| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(cells)) + " |"

        header_line = _render(columns)
        divider = "| " + " | ".join("-" * w for w in widths) + " |"
        body_lines = [_render(cells) for cells in str_rows]

        lines = [
            f"Query executed successfully on '{connection_name}'.",
            "",
            header_line,
            divider,
            *body_lines,
        ]

        if truncated:
            # ``row_count`` here is the number of rows in the truncated
            # result. ``max_rows + 1`` is what the pool manager fetched
            # before slicing, but a more useful hint is the configured
            # cap. The wording mirrors the plan's example output.
            lines.append("")
            lines.append(
                f"Rows: {row_count} (TRUNCATED — use the max_rows parameter to "
                f"increase the limit above the current {max_rows})."
            )
        else:
            lines.append("")
            lines.append(f"Rows: {row_count}")

        return "\n".join(lines)

    db_postgres_dml_select._full_doc_ = """Run a read-only SELECT query against a registered PostgreSQL connection.

The query is first passed through a SELECT-only guard that strips
comments and string literals, then checks for forbidden DML/DDL
keywords. Allowed queries are dispatched through the shared
:class:`ConnectionPoolManager` which fetches rows as dicts and
truncates to ``max_rows``.

Args:
    connection_name: Name of a connection registered via ``db_conn_add``.
    query: A SQL query. Must start with ``SELECT`` or ``WITH`` (CTE).
        DML/DDL keywords (``INSERT``, ``UPDATE``, ``DELETE``, ``DROP``,
        ``CREATE``, ``SELECT ... INTO``, etc.) are rejected before the
        query is sent to the database.
    timeout: Maximum wall-clock seconds to wait for the query
        (default 30).
    max_rows: Maximum number of rows to return (default 1000). The
        underlying fetch is bounded to ``max_rows + 1`` rows so the
        truncation flag is reliable.

Returns:
    A markdown table of the result set, prefixed with a status line
    and followed by a row-count summary. Returns an ``ERROR: ...``
    string if the query is rejected by the guard, the connection is
    unknown / not postgres, the pool cannot be created, or the
    server reports an error. Never raises.
"""

    return [
        db_conn_add,
        db_conn_delete,
        db_conn_list,
        db_conn_test,
        db_postgres_dml_select,
    ]
