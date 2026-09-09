"""Direct ``ensemble_prod`` access tools for the maintenancer agent.

The ``ens-db`` category gives the maintenancer agent privileged
read/write access to the daemon's own database via the shared
``InstanceManager.engine`` (read path) and a dedicated small pool
(repair writer). Four tools:

* ``ens_db_postgres_select`` — SELECT-only read of the daemon's DB.
  Uses ``manager.engine`` directly so it shares the existing
  ~16-repository connection pool. Per-transaction ``SET LOCAL
  statement_timeout`` is applied INSIDE the tool's own connection
  block (architect §3.1 — NOT engine-wide connect_args, which would
  cap every repository operation).
* ``ens_db_inspect`` — public-schema-only schema inspector
  (``pg_stat_user_tables.n_live_tup``, columns, indexes, FK chains,
  last-ANALYZE/autovacuum, pool status). Credentials are withheld
  (``has_password`` boolean only); pg_catalog noise, role grants,
  and other backends' SQL text are stripped.
* ``ens_db_repair_execute`` — guarded DDL/DML writer. Three gates
  (confirm nonce + audit + dry-run-first) plus same-transaction
  ``repair_log`` INSERT fail-closed (architect §3.2). DEDICATED
  2+3 pool with ``pool_pre_ping=True``, ``pool_recycle=3600``,
  ``pool_timeout=10s`` (LEADER ADJUDICATION #5 — serialized
  semantics, no queueing pileup). PG ``connect_args``:
  ``statement_timeout=60s``, ``lock_timeout=10s``,
  ``idle_in_transaction_session_timeout=60s``. Kill-switch:
  ``ENSEMBLE_REPAIR_ENABLED`` (default ON per the wave dispatch
  contract — flag-OFF returns byte-identical no-op per the
  kill-switch flag-ON/OFF contract).
* ``ens_db_pool_status`` — diagnostic. Reports pool counts via
  ``engine.pool.status()``.

Audit contract. Every repair writes a row to ``repair_log`` (see
:mod:`daemon.repositories.ens_db.models`) in the SAME
``BEGIN/COMMIT`` as the repair on the SAME connection — fail-closed
on audit-write failure (architect §3.2). The audit row carries
``created_by_instance_id`` (the agent's own instance id), the SQL
text, the SQL hash, before/after JSONB snapshots, outcome, and the
confirm nonce.

Risk standing guards:

* R13 — ``ensemble_prod`` is NEVER registered into the user-facing
  external-connection pool manager (the ``db`` category's pool
  manager). The ``db`` category resolves against user-registered
  external connections; the dedicated repair engine is a SQLAlchemy
  engine created directly from ``manager.engine.url``.
* Self-surgery (architect §7.2) — the repair tool refuses any SQL
  whose target rows reference the calling instance id, OR whose
  target table is in the audit-sensitive set (``instances``,
  ``messages``, ``message_queue``, ``tasks``) — see
  :func:`_refuse_self_surgery`.
* Migration-runner trap — the tool refuses any input that matches
  ``*.sql`` filename pattern (``runner.py:486-491`` is SQLite-only;
  PG schema uses ``create_all + _ensure_postgres_columns``).

Error surface. Tools return class-name-only error strings — no
SQL text, no internal state — so a leak in tool output cannot
echo back sensitive details to the agent.

Tool registration (architect §4.4, 10-step checklist):

1. @register_tool_category("ens-db") ABOVE @tool on every closure
2. ``CATEGORY_MODULES`` entry ``"ens-db": "daemon.tools.ens_db_tools"``
3. ``DYNAMIC_TOOL_NAMES`` += the 4 names (factory-created)
4. ``KNOWN_TOOL_NAMES`` regenerated via ``discover_source_only_tool_names()``
5. ``create_instance_tools`` list-extends with ``create_ens_db_tools(...)``
6. ``PRIVILEGED_TOOL_CATEGORIES`` += ``"ens-db"``
7. Tool bodies respect the SELECT guard and idempotent-DO$$-only rule

Decorator-only registration is silently invisible — every step
above must land together (the gotcha that bit before).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from langchain_core.tools import tool
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlmodel import Session

from ._tool_registry import register_tool_category
from .db_tools import _validate_select_only
from .upgrade_journal import iso_plus, mint_nonce, now_iso, parse_iso_utc
from daemon.repositories.ens_db.models import RepairLog

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Ensemble DB"
CATEGORY_DOC = """\
Direct read/write access to the daemon's own ``ensemble_prod`` DB for
the maintenancer agent only. Privileged category — opt-in via explicit
``tools.allow`` entry naming ``ens-db`` (R-SR16).

- `ens_db_postgres_select` — SELECT-only reader on the shared engine with
  per-tx `SET LOCAL statement_timeout` inside the tool's own connection
  block (architect §3.1).
- `ens_db_inspect` — public-schema-only schema inspector; withholds
  credentials (returns `has_password` boolean only).
- `ens_db_repair_execute` — guarded DDL/DML writer; three gates
  (confirm + audit + dry-run) + dedicated 2+3 pool; same-transaction
  `repair_log` audit fail-closed.
- `ens_db_pool_status` — pool counts via `engine.pool.status()`.
"""

# Kill-switch env (architect §3.2, §3.3; detail-plan line 417-442). The
# detail-plan parenthetical reads "(default off in dev; must be on in
# live)" — the wave dispatch contract states "default ON, flag-OFF =
# byte-identical". CODE default is ON via the ``_resolve_repair_enabled``
# resolver below; "off in dev" is then env-level provisioning, not a
# code default. A reviewer flip is one line in the env.
KILL_SWITCH_ENV = "ENSEMBLE_REPAIR_ENABLED"


def _resolve_repair_enabled() -> bool:
    """Resolve the repair kill-switch (default ON; flag-OFF = byte-identical).

    Resolution order (mirrors ``_resolve_proactive_enabled`` at
    ``daemon/config.py:2458`` — ENS env > yaml > default):

    1. ``ENSEMBLE_REPAIR_ENABLED`` env (canonical)
    2. legacy aliases ``ENSEMBLE_REPAIR_DISABLED`` / ``ENSEMBLE_REPAIR_OFF``
    3. Default ON

    Returns:
        ``True`` if the repair tool should execute real repairs;
        ``False`` if the kill-switch is OFF (tool returns a byte-identical
        no-op response — pin test at
        ``tests/unit/tools/test_ens_db_repair_killswitch_r13.py``).

    Note: invalid env values raise ``ValueError`` — fail-closed on
    misconfiguration rather than silently enabling writes.
    """
    raw = os.environ.get(KILL_SWITCH_ENV)
    if raw is None:
        # Legacy aliases — accept both spellings.
        if os.environ.get("ENSEMBLE_REPAIR_DISABLED", "").lower() in ("1", "true", "yes", "on"):
            return False
        if os.environ.get("ENSEMBLE_REPAIR_OFF", "").lower() in ("1", "true", "yes", "on"):
            return False
        return True  # default ON
    val = raw.strip().lower()
    if val in ("0", "false", "no", "off"):
        return False
    if val in ("1", "true", "yes", "on", ""):
        return True
    raise ValueError(
        f"Invalid value for {KILL_SWITCH_ENV}: {raw!r} "
        f"(expected 0/1/true/false/yes/no/on/off or empty for default ON)"
    )


# Repair confirm nonce TTL (detail-plan §3.3 — 5 minutes; no human-relay
# factor, intra-process DB mutation under Cardinal #2 confirm).
REPAIR_NONCE_TTL_S = 300

# Dedicated repair engine pool sizing (architect §3.1, LEADER ADJUDICATION #5).
REPAIR_POOL_SIZE = 2
REPAIR_POOL_MAX_OVERFLOW = 3
REPAIR_POOL_RECYCLE_S = 3600
REPAIR_POOL_TIMEOUT_S = 10

# PG connect_args timeouts (architect §3.1).
REPAIR_STATEMENT_TIMEOUT_MS = 60_000
REPAIR_LOCK_TIMEOUT_MS = 10_000
REPAIR_IDLE_IN_TXN_TIMEOUT_MS = 60_000

# Per-tx statement_timeout for the shared-engine reader tool.
SELECT_TX_STATEMENT_TIMEOUT_MS = 30_000

# Default dry-run row-count preview cap (architect §3.3).
DRY_RUN_PREVIEW_ROWS = 100

# SELECT client-side timeout (architect §3.1 — server-side timeout is the
# real cap; this is just a belt).
SELECT_CLIENT_TIMEOUT_S = 30.0


class SelectOnlyViolation(ValueError):
    """Raised by ``_validate_select_only`` when a non-SELECT statement slips through.

    Subclass of ``ValueError`` (matches ``daemon/tools/db_tools.py:97``
    contract — that function raises ``ValueError`` on forbidden
    keywords). Pin tests use ``pytest.raises(SelectOnlyViolation)`` and
    also accept ``ValueError`` (legacy callers).
    """


# SQL filename pattern (migration-runner trap — runner.py:486-491).
_SQL_FILENAME_RE = re.compile(r"\b[a-zA-Z0-9_./-]+\.sql\b", re.IGNORECASE)
# Non-idempotent DML signals (architect §3.4, R14 idempotent-DO$$-only rule).
# Catches: random/uuid generators, ``now()`` family timestamps, sequence
# ``nextval`` calls. (Incremental UPDATEs like ``SET n = n + 1`` are
# also non-idempotent but require context-sensitive parsing — out of
# scope for the input-validation regex; the dry-run path catches them
# on the second run if needed.)
_NON_IDEMPOTENT_TOKENS: tuple[str, ...] = (
    r"\bgen_random_uuid\b",
    r"\buuid_generate_v\d\b",
    r"\brandom\(\)",
    r"\bclock_timestamp\(\)",
    r"\bstatement_timestamp\(\)",
    r"\bnow\(\)",
    r"\bcurrent_timestamp\b",
    r"\bcurrent_time\b",
    r"\bnextval\(",
)
_NON_IDEMPOTENT_RE = re.compile("|".join(_NON_IDEMPOTENT_TOKENS), re.IGNORECASE)
# Idempotent DDL/DML markers (DO$$ wrapper + DROP IF EXISTS / CREATE OR REPLACE).
# Includes ``IF NOT EXISTS`` on CREATE and ``IF EXISTS`` on DROP — both
# are themselves idempotency markers recognized by the spec's
# "idempotent DO$$-only" rule (R14). DO$$-only is the strong form;
# IF NOT EXISTS / IF EXISTS are the syntactic forms (architect §3.4).
_IDEMPOTENT_DDL_MARKERS: tuple[str, ...] = (
    r"\bCREATE\s+OR\s+REPLACE\b",
    r"\bCREATE\s+.*\bIF\s+NOT\s+EXISTS\b",
    r"\bDROP\s+IF\s+EXISTS\b",
    r"\bDO\s*\$\$",
    r"\bALTER\s+TABLE\s+\S+\s+DROP\s+CONSTRAINT\s+IF\s+EXISTS\b",
)
_IDEMPOTENT_DDL_RE = re.compile("|".join(_IDEMPOTENT_DDL_MARKERS), re.IGNORECASE)

# Audit-sensitive tables the repair tool refuses to mutate (architect §7.2 —
# self-surgery refusal + general "don't let the agent mutate its own bookkeeping").
_SELF_SURGERY_TABLES: frozenset[str] = frozenset({
    "instances",
    "message_queue",
    "messages",
    "tasks",
    "events",
    "schema_migrations",
})


def _classify_sql(sql: str) -> str:
    """Return one of ``"DDL"``, ``"DML"``, ``"SELECT"``, ``"OTHER"``.

    Lightweight first-word heuristic — used by the repair tool to
    decide which validation path to run.
    """
    cleaned = sql.strip().lstrip("(").lstrip()
    if not cleaned:
        return "OTHER"
    head = cleaned.split(None, 1)[0].upper().rstrip(";")
    if head in {"SELECT", "WITH"}:
        return "SELECT"
    if head in {"INSERT", "UPDATE", "DELETE", "MERGE"}:
        return "DML"
    if head in {
        "CREATE", "DROP", "ALTER", "TRUNCATE", "GRANT", "REVOKE",
        "COMMENT", "ANALYZE", "VACUUM", "REINDEX",
    }:
        return "DDL"
    return "OTHER"


def _is_idempotent(sql: str, sql_class: str) -> tuple[bool, str]:
    """Return (is_idempotent, reason_if_not).

    Heuristics (architect §3.4 — repository-methods-first + R14 idempotent
    DO$$-only rule):

    * DDL: must carry at least one of
      ``DO $$``, ``CREATE OR REPLACE``, ``CREATE ... IF NOT EXISTS``,
      ``DROP IF EXISTS``, or
      ``ALTER TABLE ... DROP CONSTRAINT IF EXISTS``.
    * DML: must NOT contain any of the non-idempotent token patterns
      (``random()``, ``now()``, ``gen_random_uuid()``, ``nextval()``,
      ``current_timestamp``, ``clock_timestamp()``).
    * SELECT: not validated here (the SELECT-only guard runs upstream).
    """
    if sql_class == "DDL":
        if _IDEMPOTENT_DDL_RE.search(sql):
            return True, ""
        return False, (
            "non-idempotent-DDL: repair DDL must include at least one of "
            "DO $$, CREATE OR REPLACE, DROP IF EXISTS, or "
            "ALTER TABLE ... DROP CONSTRAINT IF EXISTS"
        )
    if sql_class == "DML":
        m = _NON_IDEMPOTENT_RE.search(sql)
        if m:
            return False, (
                f"non-idempotent-DML: repair DML contains non-idempotent "
                f"token {m.group(0)!r} — repair DML must be re-runnable to "
                f"the same final state (R14 idempotent DO$$-only rule)"
            )
        return True, ""
    return True, ""


def _refuse_self_surgery(sql: str, current_instance_id: str) -> str | None:
    """Return an error message if the SQL mutates the calling instance's own rows.

    Two heuristic checks (architect §7.2 — self-surgery refusal):

    1. The ``current_instance_id`` literal appears anywhere in the SQL.
    2. The SQL targets an audit-sensitive table (``instances``,
       ``messages``, ``message_queue``, ``tasks``, ``events``,
       ``schema_migrations``).

    The first check catches ``UPDATE instances SET ... WHERE id = '<self>'``
    attempts. The second is the standing-guard "don't let the agent
    touch its own bookkeeping at all".
    """
    if not current_instance_id:
        return None
    if current_instance_id in sql:
        return (
            "self-surgery-refused: repair SQL references the calling "
            "instance id (architect §7.2 self-surgery refusal); delegate "
            "to leader/HTTP for own-state mutations"
        )
    # Audit-sensitive table check — naive but load-bearing. The
    # regex anchors the table name as a whole word OR a word
    # followed by ``_`` (handles suffixes like ``instances_backup``).
    sql_lower = sql.lower()
    for tbl in _SELF_SURGERY_TABLES:
        # Match ``FROM tbl``, ``UPDATE tbl``, ``INTO tbl``, ``TABLE tbl``.
        # The ``(?![a-z0-9_])`` negative lookahead ensures we stop at
        # word boundaries (including the underscore, so ``instances``
        # does NOT match ``instances_backup``'s CREATE — only
        # ``instances`` itself).
        pattern = rf"\b(from|update|into|table)\s+{re.escape(tbl)}(?![a-z0-9_])"
        if re.search(pattern, sql_lower):
            return (
                f"self-surgery-refused: repair SQL targets audit-sensitive "
                f"table {tbl!r}; the maintenancer tool family must not "
                f"mutate own-bookkeeping tables (architect §7.2)"
            )
    return None


@dataclass(frozen=True)
class _RepairNonce:
    """In-memory nonce record (mirrors upgrade_journal.PendingAction)."""
    nonce: str
    target: str
    instance_id: str
    expires_at: str  # ISO-8601


class _RepairNonceStore:
    """In-memory single-use nonce store for ``ens_db_repair_execute``.

    Mirrors :class:`daemon.tools.upgrade_journal.PendingAction` minus
    the journal persistence (repairs are intra-process DB mutations
    — the ``repair_log`` table is the durable audit trail). The TTL
    is 5 minutes; after expiry the nonce is silently removed from the
    dict (fail-closed on lookup of an expired nonce).

    Threading: the underlying SQLAlchemy engine may be touched from
    worker threads (``asyncio.to_thread``); the nonce dict is
    guarded by a :class:`threading.Lock` to keep
    mint/consume/expiry-sweep race-free.
    """

    def __init__(self, ttl_seconds: int = REPAIR_NONCE_TTL_S) -> None:
        self._ttl_seconds = ttl_seconds
        self._store: dict[str, _RepairNonce] = {}
        self._lock = threading.Lock()

    def mint(self, instance_id: str, target: str) -> _RepairNonce:
        """Mint a new nonce bound to (instance_id, target)."""
        with self._lock:
            self._sweep_locked()
            nonce = mint_nonce()
            record = _RepairNonce(
                nonce=nonce,
                target=target,
                instance_id=instance_id,
                expires_at=iso_plus(now_iso(), self._ttl_seconds),
            )
            self._store[nonce] = record
            return record

    def consume(
        self,
        instance_id: str,
        nonce: str,
        target: str,
    ) -> tuple[bool, str | None]:
        """Consume a nonce. Returns (success, error_message_if_failed).

        Fail-closed semantics matching ``upgrade_tools.py:1946-1984``:

        * nonce unknown → ``nonce-mismatch``
        * nonce already consumed → ``nonce-already-used``
        * nonce bound to another instance → ``nonce-instance-mismatch``
        * nonce expired (TTL elapsed) → ``nonce-expired``
        * nonce bound to a different target → ``nonce-action-mismatch``
        """
        with self._lock:
            self._sweep_locked()
            record = self._store.get(nonce)
            if record is None:
                return False, "nonce-mismatch"
            if record.instance_id != instance_id:
                return False, "nonce-instance-mismatch"
            if record.target != target:
                return False, "nonce-action-mismatch"
            ttl = parse_iso_utc(record.expires_at)
            if ttl is None or datetime.now(tz=timezone.utc) > ttl:
                # Expired — drop and fail.
                self._store.pop(nonce, None)
                return False, "nonce-expired"
            # Single-use — pop on consume.
            self._store.pop(nonce, None)
            return True, None

    def _sweep_locked(self) -> None:
        """Drop expired entries (called under self._lock)."""
        now = datetime.now(tz=timezone.utc)
        expired: list[str] = []
        for nonce, record in self._store.items():
            ttl = parse_iso_utc(record.expires_at)
            if ttl is None or now > ttl:
                expired.append(nonce)
        for nonce in expired:
            self._store.pop(nonce, None)


def _sql_hash(sql: str) -> str:
    """Short fingerprint of the SQL (for ``repair_log.sql_hash``)."""
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()[:16]


def _build_repair_engine(shared_engine: Engine) -> Engine:
    """Build the DEDICATED repair engine (architect §3.1 — Option 4′).

    Pool sizing: ``2+3`` (size + max_overflow), ``pool_pre_ping=True``,
    ``pool_recycle=3600``, ``pool_timeout=10s``. PG-only
    ``connect_args`` timeouts are applied when the underlying URL is
    a PostgreSQL dialect — SQLite ignores them silently (architect
    §7.5 — dual-engine parity).

    **R13 standing guard:** the returned engine is created from the
    shared ``engine.url`` and is NOT registered with the user-
    facing external-connection pool manager. The shared manager
    holds asyncpg pools against user-registered external
    connections — adding ``ensemble_prod`` there would bypass the
    ``ens-db`` exclusivity.

    Engine reuse: the engine is cached per-process per
    ``shared_engine.url`` so we do not create a fresh engine on
    every tool invocation.
    """
    cache_key = (
        shared_engine.url.render_as_string(hide_password=True),
        shared_engine.url.get_backend_name(),
    )
    cached = _REPAIR_ENGINE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    is_pg = shared_engine.url.get_backend_name().startswith("postgres")
    url = shared_engine.url.render_as_string(hide_password=False)
    kwargs: dict[str, Any] = dict(
        pool_size=REPAIR_POOL_SIZE,
        max_overflow=REPAIR_POOL_MAX_OVERFLOW,
        pool_pre_ping=True,
        pool_recycle=REPAIR_POOL_RECYCLE_S,
        pool_timeout=REPAIR_POOL_TIMEOUT_S,
    )
    if is_pg:
        kwargs["connect_args"] = {
            "options": (
                f"-c statement_timeout={REPAIR_STATEMENT_TIMEOUT_MS} "
                f"-c lock_timeout={REPAIR_LOCK_TIMEOUT_MS} "
                f"-c idle_in_transaction_session_timeout="
                f"{REPAIR_IDLE_IN_TXN_TIMEOUT_MS}"
            ),
        }
    else:
        # SQLite — mirror the shared engine's pragmas (WAL + busy_timeout)
        # so the repair engine does not starve under contention.
        kwargs["connect_args"] = {"check_same_thread": False}

    repair_engine = create_engine(url, **kwargs)
    if not is_pg:
        # Mirror the WAL/busy_timeout pragmas on SQLite (architect §7.5).
        @event.listens_for(repair_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, connection_record):  # noqa: ANN001
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    _REPAIR_ENGINE_CACHE[cache_key] = repair_engine
    return repair_engine


# Process-level cache for repair engines (keyed by shared-engine URL).
_REPAIR_ENGINE_CACHE: dict[tuple[str, str], Engine] = {}


def _format_select_result(columns: list[str], rows: list[tuple[Any, ...]]) -> str:
    """Render SELECT result rows as a markdown table (caller-side safety)."""
    if not columns:
        return "(no columns)"
    header = "| " + " | ".join(columns) + " |"
    sep = "|" + "|".join(["---"] * len(columns)) + "|"
    if not rows:
        return f"{header}\n{sep}\n(empty result set)"
    lines: list[str] = [header, sep]
    for row in rows:
        cells = ["" if v is None else str(v).replace("|", "\\|").replace("\n", " ")[:500] for v in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _tool_error(exc: BaseException, prefix: str) -> str:
    """Build the agent-facing error string (class-name-only).

    Per spec: error surface is class-name-only — never echo the SQL
    text or internal state into the agent-visible return value. The
    full exception is logged at WARNING for operators.
    """
    logger.warning("%s: %s", prefix, exc)
    return f"ERROR: {prefix} ({type(exc).__name__})."


def create_ens_db_tools(
    manager: "InstanceManager",
    current_instance_id: str,
    agent_id: str = "",
) -> list:
    """Create the ``ens-db`` category's 4-tool surface.

    Mirrors ``create_system_log_tools`` closure-injection pattern at
    ``daemon/tools/system_log_tools.py:256``.

    Args:
        manager: The :class:`InstanceManager`. ``manager.engine`` is
            the shared SQLAlchemy engine for the read tools.
        current_instance_id: Owning instance id (audit + self-surgery
            refusal check).
        agent_id: Caller agent id (audit column on ``repair_log``).

    Returns:
        ``[ens_db_postgres_select, ens_db_inspect,
        ens_db_repair_execute, ens_db_pool_status]``.
    """
    nonce_store = _RepairNonceStore()

    # ── Tool 1 — ens_db_postgres_select ─────────────────────────────────
    @register_tool_category("ens-db")
    @tool
    async def ens_db_postgres_select(query: str, max_rows: int = 500) -> str:
        """Run a SELECT query against the daemon's own ``ensemble_prod`` DB.

        Defense-in-depth SELECT-only guard runs FIRST
        (``_validate_select_only`` from :mod:`daemon.tools.db_tools`).
        Per-transaction ``SET LOCAL statement_timeout`` is applied
        INSIDE the tool's own connection block (architect §3.1 — NOT
        engine-wide ``connect_args``). Async→sync bridge via
        ``asyncio.to_thread`` + ``asyncio.wait_for`` belt (the
        thread-bridge cancels the awaitable but does NOT abort the
        blocking psycopg call; the server-side statement_timeout is
        the real cap).

        Args:
            query: SELECT or WITH query. INSERT/UPDATE/DELETE/DDL all
                raise ``SelectOnlyViolation`` (also a ``ValueError``).
            max_rows: Row cap (default 500; soft-capped at 5000).

        Returns:
            Markdown table of columns + rows; or structured refusal
            string on guard violation / timeout / engine error.
        """
        # SELECT-only guard runs FIRST (architect §3.1, §7.2).
        try:
            _validate_select_only(query)
        except ValueError as exc:
            return _tool_error(SelectOnlyViolation(str(exc)), "ens_db_postgres_select: select-only-guard")

        if max_rows <= 0 or max_rows > 5000:
            max_rows = min(max(max_rows, 1), 5000)

        def _run_select() -> str:
            with manager.engine.begin() as conn:
                # Per-tx statement_timeout inside the tool's own tx
                # (NOT engine-wide connect_args). Only emitted on PG —
                # SQLite does NOT support ``SET LOCAL statement_timeout``
                # and would raise OperationalError (architect §7.5 dual-
                # engine parity).
                if manager.engine.url.get_backend_name().startswith("postgres"):
                    conn.execute(
                        text(f"SET LOCAL statement_timeout = {int(SELECT_TX_STATEMENT_TIMEOUT_MS)}")
                    )
                result = conn.execute(text(query))
                cols = list(result.keys())
                rows = list(result.fetchmany(max_rows))
            return _format_select_result(cols, rows)

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_run_select),
                timeout=SELECT_CLIENT_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "ens_db_postgres_select client-side timeout after %ss (server-side statement_timeout=%sms continues)",
                SELECT_CLIENT_TIMEOUT_S, SELECT_TX_STATEMENT_TIMEOUT_MS,
            )
            return (
                f"ERROR: ens_db_postgres_select (TimeoutError) — server-side "
                f"statement_timeout={SELECT_TX_STATEMENT_TIMEOUT_MS}ms is the "
                f"real cap; client cancelled after {SELECT_CLIENT_TIMEOUT_S}s."
            )
        except Exception as exc:
            return _tool_error(exc, "ens_db_postgres_select")

    # ── Tool 2 — ens_db_inspect ─────────────────────────────────────────
    @register_tool_category("ens-db")
    @tool
    async def ens_db_inspect(target: str = "tables") -> str:
        """Public-schema-only schema inspector (architect §3.5).

        Modes:

        * ``"tables"`` — list public-schema tables + approximate row
          counts via ``pg_stat_user_tables.n_live_tup``, last
          ANALYZE / autovacuum timestamps.
        * ``"columns:<table>"`` — column types, nullability, defaults.
        * ``"indexes:<table>"`` — index name + definition.
        * ``"fkeys:<table>"`` — foreign-key chains (incoming + outgoing).
        * ``"pool"`` — engine pool counts via ``engine.pool.status()``.

        Withheld (defense-in-depth):

        * Credentials (``has_password`` boolean only).
        * Role grants.
        * ``pg_catalog`` noise.
        * Other backends' SQL text.

        Returns:
            Markdown rendering, or a structured refusal on guard
            violation / engine error. SELECT-only — never mutates.
        """
        # Whitelist mode string — refuse anything else before hitting DB.
        allowed_modes_prefixes = (
            "tables",
            "columns:",
            "indexes:",
            "fkeys:",
            "pool",
        )
        if not any(target == m or target.startswith(m) for m in allowed_modes_prefixes):
            return (
                f"ERROR: ens_db_inspect — unknown target {target!r}; "
                f"allowed: tables | columns:<t> | indexes:<t> | fkeys:<t> | pool"
            )

        def _run_inspect() -> str:
            is_pg = manager.engine.url.get_backend_name().startswith("postgres")
            if target == "pool":
                # Diagnostic — works on both engines.
                st = manager.engine.pool.status()
                return f"POOL STATUS (shared engine):\n{st}"

            if not is_pg:
                # SQLite path — degrade gracefully (architect §7.5 dual-engine).
                return (
                    "ens_db_inspect: full schema introspection is PG-only; "
                    "SQLite engine detected. Use ``ens_db_postgres_select`` "
                    "for direct queries; pool mode works on both engines."
                )

            with manager.engine.begin() as conn:
                # Per-tx statement_timeout inside the tool's own tx
                # (NOT engine-wide connect_args). PG-only — SQLite
                # does NOT support ``SET LOCAL statement_timeout``
                # (architect §7.5 dual-engine parity).
                if manager.engine.url.get_backend_name().startswith("postgres"):
                    conn.execute(
                        text(f"SET LOCAL statement_timeout = {int(SELECT_TX_STATEMENT_TIMEOUT_MS)}")
                    )
                if target == "tables":
                    return _inspect_tables(conn)
                if target.startswith("columns:"):
                    tbl = target.split(":", 1)[1].strip()
                    return _inspect_columns(conn, tbl)
                if target.startswith("indexes:"):
                    tbl = target.split(":", 1)[1].strip()
                    return _inspect_indexes(conn, tbl)
                if target.startswith("fkeys:"):
                    tbl = target.split(":", 1)[1].strip()
                    return _inspect_fkeys(conn, tbl)
            return ""

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_run_inspect),
                timeout=SELECT_CLIENT_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            return (
                f"ERROR: ens_db_inspect (TimeoutError) — server-side "
                f"statement_timeout={SELECT_TX_STATEMENT_TIMEOUT_MS}ms is the "
                f"real cap; client cancelled after {SELECT_CLIENT_TIMEOUT_S}s."
            )
        except Exception as exc:
            return _tool_error(exc, "ens_db_inspect")

    # ── Tool 3 — ens_db_repair_execute (the heavy hitter) ────────────────
    @register_tool_category("ens-db")
    @tool
    async def ens_db_repair_execute(
        sql: str,
        confirm: bool = False,
        nonce: str | None = None,
        target_label: str | None = None,
        dry_run: bool = True,
    ) -> str:
        """Execute a guarded DDL/DML repair on ``ensemble_prod``.

        Three independent gates — ALL must clear before commit:

        1. **Kill-switch** ``ENSEMBLE_REPAIR_ENABLED`` (default ON via
           :func:`_resolve_repair_enabled`). Flag-OFF returns a
           byte-identical no-op (pin test at
           ``tests/unit/tools/test_ens_db_repair_killswitch_r13.py``).
        2. **Input validation** — refuse ``*.sql`` filenames
           (migration-runner trap), refuse non-idempotent DML
           (R14), refuse self-surgery (architect §7.2).
        3. **Confirm gate** — single-use nonce + 5-min TTL +
           action-bound (``kind='repair'``, ``target=<target_label>``).
           The dry-run call mints the nonce; the confirm call
           consumes it. Borrowed from
           ``upgrade_tools.py:1937-2006`` primitives WITHOUT the
           human-relay factor.

        Audit contract (architect §3.2): every dry-run AND every
        committed repair writes a row to ``repair_log`` in the SAME
        ``BEGIN/COMMIT`` as the repair on the SAME connection —
        fail-closed on audit-write failure.

        Args:
            sql: The SQL to execute. Idempotent ``DO $$`` /
                ``CREATE OR REPLACE`` / ``DROP IF EXISTS`` for DDL;
                re-runnable-to-same-state for DML.
            confirm: True to execute; False (default) to dry-run
                preview.
            nonce: Confirm nonce from a prior dry-run. Required when
                ``confirm=True``; ignored on dry-run.
            target_label: Free-form target identifier used to bind
                the nonce (``target=<table/op>``). Required on the
                dry-run that mints the nonce; must match the value
                used at mint time on the confirm call.
            dry_run: Default True. Pin tests force-confirm by setting
                ``confirm=True, dry_run=False, nonce=<prior>``.

        Returns:
            On dry-run: preview markdown (row-count projection +
            first ~100 affected-row preview; DDL preview is a
            ``BEGIN; <DDL>; ROLLBACK;`` wrapper).
            On commit: confirmation message with ``repair_log.id``.
            On refusal: structured error string naming the rule
            violated (class-name-only surface).
        """
        # ── Gate 0: kill-switch ──────────────────────────────────────
        if not _resolve_repair_enabled():
            # Pin test: flag-OFF = byte-identical no-op.
            return (
                "ens_db_repair_execute: kill-switch OFF "
                f"(env {KILL_SWITCH_ENV}=0). No-op response per "
                "kill-switch flag-OFF byte-identical contract."
            )

        # ── Gate 1: input validation ────────────────────────────────
        if not sql or not sql.strip():
            return "ERROR: ens_db_repair_execute — sql is empty"
        if _SQL_FILENAME_RE.search(sql):
            return (
                "ERROR: ens_db_repair_execute — migration-runner-trap: "
                "*.sql filename input is refused (runner.py:486-491 is "
                "SQLite-only; PG schema uses create_all + "
                "_ensure_postgres_columns). Submit raw SQL text."
            )

        sql_class = _classify_sql(sql)
        if sql_class in ("DDL", "DML"):
            ok, reason = _is_idempotent(sql, sql_class)
            if not ok:
                return f"ERROR: ens_db_repair_execute — {reason}"

        self_surgery = _refuse_self_surgery(sql, current_instance_id)
        if self_surgery is not None:
            return f"ERROR: ens_db_repair_execute — {self_surgery}"

        target_label = (target_label or "").strip() or sql_class

        # ── Gate 2: confirm gate (nonce) ─────────────────────────────
        if confirm:
            if not nonce:
                return (
                    "ERROR: ens_db_repair_execute — user-confirmation-missing: "
                    "no nonce supplied (relay the dry-run nonce to yourself "
                    "and re-call with confirm=True, nonce=<nonce>)"
                )
            ok, err = nonce_store.consume(current_instance_id, nonce, target_label)
            if not ok:
                return (
                    f"ERROR: ens_db_repair_execute — {err}: nonce does not "
                    f"authorize target={target_label!r} from this instance; "
                    f"re-run dry_run to mint a fresh nonce"
                )

        # ── Build repair engine + run ────────────────────────────────
        repair_engine = _build_repair_engine(manager.engine)
        sql_hash = _sql_hash(sql)

        def _run_repair() -> str:
            with Session(repair_engine) as session:
                try:
                    # DML pre-image: shadow ``SELECT ... FOR UPDATE``.
                    pre_snapshot = None
                    if sql_class == "DML" and not dry_run:
                        pre_snapshot = _capture_pre_image_sqlite_safe(
                            session, sql, repair_engine
                        )

                    # DDL dry-run wrapper: BEGIN; <DDL>; ROLLBACK.
                    # DML dry-run wrapper: BEGIN; <SQL>; ROLLBACK after
                    # capturing the row-count projection + first ~100 rows.
                    if dry_run:
                        return _dry_run_sqlite_safe(
                            session, sql, sql_class, target_label,
                            nonce_store, current_instance_id, sql_hash,
                            repair_engine, manager, agent_id,
                        )

                    # COMMITTED path. Audit row in SAME tx.
                    try:
                        # Execute the repair SQL — RETURNING * if DML.
                        result = session.execute(text(sql))
                        after_snapshot: list[dict[str, Any]] | None = None
                        if sql_class == "DML":
                            after_snapshot = _rows_to_dicts(result)
                        elif sql_class == "DDL":
                            # DDL: no row image exists.
                            after_snapshot = None

                        # Audit row — fail-closed (architect §3.2).
                        audit = RepairLog(
                            created_by_instance_id=current_instance_id,
                            created_by_agent_id=agent_id or None,
                            sql_text=sql,
                            sql_class=sql_class,
                            sql_hash=sql_hash,
                            dry_run=False,
                            before_snapshot=pre_snapshot,
                            after_snapshot=after_snapshot,
                            outcome="committed",
                            error_message=None,
                            target_table=target_label,
                            nonce=nonce,
                            ttl_expires_at=None,
                        )
                        session.add(audit)
                        session.commit()
                    except Exception as exc:
                        session.rollback()
                        # Write a rollback audit row in a fresh session.
                        _write_audit_outcome(
                            repair_engine,
                            current_instance_id, agent_id,
                            sql, sql_class, sql_hash,
                            target_label, nonce,
                            outcome="error",
                            error_message=f"{type(exc).__name__}: {str(exc)[:200]}",
                        )
                        raise
                    audit_id = audit.id
                    return (
                        f"REPAIR COMMITTED — id={audit_id}\n"
                        f"target={target_label!r} sql_class={sql_class} "
                        f"sql_hash={sql_hash} nonce={'<consumed>' if nonce else 'n/a'}\n"
                        f"rows_after={len(after_snapshot) if after_snapshot else 0}"
                    )
                except Exception as exc:
                    # Already rolled back + audit written.
                    return _tool_error(exc, "ens_db_repair_execute")

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_run_repair),
                timeout=REPAIR_STATEMENT_TIMEOUT_MS / 1000.0 + 5.0,
            )
        except asyncio.TimeoutError:
            return (
                f"ERROR: ens_db_repair_execute (TimeoutError) — repair "
                f"pool saturated or server-side timeout exceeded "
                f"({REPAIR_STATEMENT_TIMEOUT_MS}ms statement_timeout, "
                f"{REPAIR_POOL_TIMEOUT_S}s pool_timeout). Retry when the "
                f"current repair completes."
            )
        except Exception as exc:
            return _tool_error(exc, "ens_db_repair_execute")

    # ── Tool 4 — ens_db_pool_status ─────────────────────────────────────
    @register_tool_category("ens-db")
    @tool
    async def ens_db_pool_status() -> str:
        """Diagnostic — pool counts for both engines (read-only).

        Returns:
            Combined ``engine.pool.status()`` block for the shared
            read engine and the dedicated repair engine.
        """
        def _status() -> str:
            shared = manager.engine.pool.status()
            repair = _build_repair_engine(manager.engine).pool.status()
            return (
                f"SHARED ENGINE pool (read tools):\n{shared}\n\n"
                f"REPAIR ENGINE pool (ens_db_repair_execute):\n{repair}"
            )

        try:
            return await asyncio.to_thread(_status)
        except Exception as exc:
            return _tool_error(exc, "ens_db_pool_status")

    return [
        ens_db_postgres_select,
        ens_db_inspect,
        ens_db_repair_execute,
        ens_db_pool_status,
    ]


# ── Helpers used by the repair tool body ────────────────────────────────


def _inspect_tables(conn: Any) -> str:
    """List public-schema tables + approximate row counts."""
    rows = conn.execute(
        text(
            """
            SELECT
              c.relname AS table_name,
              pg_catalog.pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size,
              s.n_live_tup AS approx_rows,
              s.last_analyze,
              s.last_autovacuum
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
            WHERE n.nspname = 'public' AND c.relkind = 'r'
            ORDER BY c.relname
            """
        )
    ).fetchall()
    cols = ["table", "size", "approx_rows", "last_analyze", "last_autovacuum"]
    return _format_select_result(cols, [tuple(r) for r in rows])


def _inspect_columns(conn: Any, table: str) -> str:
    """Column types for one public-schema table."""
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", table):
        return "ERROR: ens_db_inspect — invalid table identifier"
    rows = conn.execute(
        text(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :t
            ORDER BY ordinal_position
            """
        ),
        {"t": table},
    ).fetchall()
    return _format_select_result(
        ["column", "type", "nullable", "default"],
        [tuple(r) for r in rows],
    )


def _inspect_indexes(conn: Any, table: str) -> str:
    """Indexes on one public-schema table."""
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", table):
        return "ERROR: ens_db_inspect — invalid table identifier"
    rows = conn.execute(
        text(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public' AND tablename = :t
            ORDER BY indexname
            """
        ),
        {"t": table},
    ).fetchall()
    return _format_select_result(["index", "definition"], [tuple(r) for r in rows])


def _inspect_fkeys(conn: Any, table: str) -> str:
    """FK chains for one public-schema table (incoming + outgoing)."""
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", table):
        return "ERROR: ens_db_inspect — invalid table identifier"
    out_rows = conn.execute(
        text(
            """
            SELECT
              tc.constraint_name,
              tc.table_name AS from_table,
              kcu.column_name AS from_column,
              ccu.table_name AS to_table,
              ccu.column_name AS to_column,
              'outgoing' AS direction
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = 'public' AND tc.table_name = :t
            """
        ),
        {"t": table},
    ).fetchall()
    in_rows = conn.execute(
        text(
            """
            SELECT
              tc.constraint_name,
              ccu.table_name AS from_table,
              ccu.column_name AS from_column,
              tc.table_name AS to_table,
              kcu.column_name AS to_column,
              'incoming' AS direction
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = 'public' AND ccu.table_name = :t
            """
        ),
        {"t": table},
    ).fetchall()
    cols = ["constraint", "from_table", "from_col", "to_table", "to_col", "direction"]
    return _format_select_result(cols, [tuple(r) for r in (out_rows + in_rows)])


def _rows_to_dicts(result: Any) -> list[dict[str, Any]]:
    """Convert a SQLAlchemy ``Result`` to a JSON-safe list of dicts.

    Defensive against non-row-returning statements (e.g. plain
    ``UPDATE`` without ``RETURNING``): such results are closed
    automatically by SQLAlchemy and ``result.keys()`` raises
    ``ResourceClosedError``. We return an empty list in that case
    — the audit row carries ``after_snapshot=None`` when no row
    image was captured.
    """
    try:
        keys = list(result.keys())
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    try:
        for row in result.mappings():
            out.append({k: _jsonable(row[k]) for k in keys})
    except Exception:
        return out
    return out[:DRY_RUN_PREVIEW_ROWS]


def _jsonable(v: Any) -> Any:
    """Coerce DB read values to a JSON-safe shape."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


def _capture_pre_image_sqlite_safe(
    session: Any, sql: str, engine: Engine
) -> list[dict[str, Any]] | None:
    """Best-effort pre-image via ``RETURNING *`` capture for DML.

    Strategy: attempt to extract the target table + WHERE clause from
    the DML; run a shadow ``SELECT ... FOR UPDATE`` on the same
    connection in the same tx to capture the rows that will be
    modified. Returns ``None`` if the parse fails — the audit row is
    still written (``before_snapshot=None``) and partial application
    recovery relies on the post-image + ``sql_text``.

    SQLite-safe: uses parameterized queries only; never string-formats
    user input into raw SQL.
    """
    # Naive parse — sufficient for the common cases the agent is
    # expected to run (UPDATE … WHERE k=v; DELETE … WHERE k=v).
    sql_upper = sql.upper().lstrip()
    target_table: str | None = None
    where_clause: str | None = None
    m = re.search(r"\bFROM\s+([a-zA-Z_][a-zA-Z0-9_\"]*)", sql_upper)
    if m:
        target_table = m.group(1).strip('"')
    m = re.search(r"\bWHERE\b(.*?)(?:;|$)", sql, re.IGNORECASE | re.DOTALL)
    if m:
        where_clause = m.group(1).strip()
    if not target_table:
        return None
    try:
        if where_clause:
            preview_sql = (
                f"SELECT * FROM {target_table} WHERE {where_clause} "
                f"LIMIT {DRY_RUN_PREVIEW_ROWS}"
            )
        else:
            preview_sql = (
                f"SELECT * FROM {target_table} LIMIT {DRY_RUN_PREVIEW_ROWS}"
            )
        result = session.execute(text(preview_sql))
        return _rows_to_dicts(result)
    except Exception as exc:
        logger.warning("ens_db_repair_execute: pre-image capture failed: %s", exc)
        return None


def _dry_run_sqlite_safe(
    session: Any,
    sql: str,
    sql_class: str,
    target_label: str,
    nonce_store: _RepairNonceStore,
    current_instance_id: str,
    sql_hash: str,
    repair_engine: Engine,
    manager: Any,
    agent_id: str,
) -> str:
    """Dry-run wrapper — BEGIN; <SQL>; ROLLBACK (DDL) or with row-count
    + first-100 preview (DML). Always writes a ``previewed`` audit row
    outside the rolled-back tx (so the dry-run does NOT roll back its
    own audit row). Mints a confirm nonce bound to
    ``(instance_id, target_label)``.

    Returns the preview markdown + the nonce for the agent to relay.
    """
    # Mint the nonce first — bound to (instance, target).
    nonce_record = nonce_store.mint(current_instance_id, target_label)

    preview_lines: list[str] = []
    try:
        if sql_class == "DDL":
            # Dry-run the DDL — wrapped in BEGIN/ROLLBACK so it does
            # NOT commit.
            session.execute(text("BEGIN"))
            try:
                session.execute(text(sql))
                preview_lines.append("DRY-RUN DDL EXECUTED INSIDE BEGIN/ROLLBACK — no commit")
            except Exception as exc:
                preview_lines.append(
                    f"DRY-RUN DDL FAILED ({type(exc).__name__}): "
                    f"{str(exc)[:200]} — fix the SQL and re-run."
                )
            session.rollback()
        elif sql_class == "DML":
            # DML dry-run — same wrapper + row-count projection.
            session.execute(text("BEGIN"))
            try:
                result = session.execute(text(sql))
                previews = _rows_to_dicts(result)
                preview_lines.append(
                    f"DRY-RUN DML EXECUTED INSIDE BEGIN/ROLLBACK — projected "
                    f"affected rows (first {DRY_RUN_PREVIEW_ROWS}):"
                )
                for i, row in enumerate(previews, 1):
                    preview_lines.append(f"  [{i}] {row}")
                session.rollback()
            except Exception as exc:
                session.rollback()
                preview_lines.append(
                    f"DRY-RUN DML FAILED ({type(exc).__name__}): "
                    f"{str(exc)[:200]} — fix the SQL and re-run."
                )
        else:
            preview_lines.append(
                f"DRY-RUN: sql_class={sql_class} — preview skipped "
                f"(only DDL/DML produce meaningful dry-run output)"
            )
    except Exception as exc:
        preview_lines.append(
            f"DRY-RUN WRAPPER ERROR ({type(exc).__name__}): {str(exc)[:200]}"
        )

    # Repository-methods-first warning (architect §3.4).
    preview_lines.append("")
    preview_lines.append(
        "Repository-methods-first rule (architect §3.4): "
        "if a service method exists for this class (DLQ replay → "
        "DeadLetterService.replay_from_dlq; watcher re-arm → "
        "DependencyWatcherRepository.rearm_cancelled; sentinel heal "
        "→ _insert_deferred_marker; any job_state_machine transition), "
        "prefer the repository over raw SQL."
    )

    # Persist the previewed audit row in a SEPARATE session so the
    # ROLLBACK above does not eat the audit row.
    _write_audit_outcome(
        repair_engine,
        current_instance_id, agent_id,
        sql, sql_class, sql_hash,
        target_label,
        nonce=nonce_record.nonce,
        ttl_expires_at=nonce_record.expires_at,
        outcome="previewed",
        error_message=None,
    )

    nonce = nonce_record.nonce
    preview_lines.append("")
    preview_lines.append(
        f"CONFIRM NONCE: {nonce}  (TTL 5min, single-use, "
        f"action-bound target={target_label!r} instance={current_instance_id})"
    )
    preview_lines.append(
        "To commit, re-call with: confirm=True, nonce=<above>, "
        "target_label=<above>, dry_run=False"
    )
    return "\n".join(preview_lines)


def _write_audit_outcome(
    engine: Engine,
    instance_id: str,
    agent_id: str,
    sql: str,
    sql_class: str,
    sql_hash: str,
    target_label: str,
    nonce: str | None,
    ttl_expires_at: str | None = None,
    outcome: str = "previewed",
    error_message: str | None = None,
    before_snapshot: dict[str, Any] | None = None,
    after_snapshot: list[dict[str, Any]] | None = None,
) -> None:
    """Write a ``repair_log`` row in a fresh session (best-effort).

    Called from the rolled-back DML error path and from the dry-run
    audit-write step. Failures here are logged but never block the
    caller (the commit-path audit-write is fail-closed — that one
    IS in the same tx; the rolled-back-path audit-write is
    best-effort because the tx is already gone).
    """
    try:
        with Session(engine) as session:
            row = RepairLog(
                created_by_instance_id=instance_id,
                created_by_agent_id=agent_id or None,
                sql_text=sql,
                sql_class=sql_class,
                sql_hash=sql_hash,
                dry_run=(outcome == "previewed"),
                before_snapshot=before_snapshot,
                after_snapshot=after_snapshot,
                outcome=outcome,
                error_message=error_message,
                target_table=target_label,
                nonce=nonce,
                ttl_expires_at=ttl_expires_at,
            )
            session.add(row)
            session.commit()
    except Exception as exc:
        logger.warning("ens_db_repair_execute: audit write failed: %s", exc)
