"""PostgreSQL backend census (metrics-collection P0 T7 / T10).

THE DISCIPLINE (catalog §9 / MA-13, arch §5 A8)
-------------------------------------------------

The census answers ONE question: *which backends are idle-in-
transaction, for how long, at which site?* It exists because the
October 26/28 incident leaked ``idle in transaction`` connections and
the only diagnosis was a hand-run ``pg_stat_activity`` census.

The rules that make the census trustworthy:

* **A dedicated, NON-POOLED connection** — a direct ``psycopg.connect``,
  never the manager's pool and never the checkpoint saver's pool. A
  probe that consumes pool slots is the exact failure mode it exists
  to detect (the readiness-probe amplifier lesson). The pattern is the
  wedge-capture's one-shot connect
  (``daemon/services/wedge_capture/db_capture.py``).
* ``application_name='ensemble-metrics-census'`` — the census's own
  connection is identifiable in ``pg_stat_activity``.
* ``statement_timeout = 5 s`` on every census query (< ⅓ of the 15 s
  cadence) and ``connect_timeout = 3 s``.
* **Self-exclusion**: the census excludes ONLY its own
  ``application_name``. ``wedge-capture`` rows are counted under
  ``other`` — excluding them would transiently break the sum-ties
  invariant
  ``sum(ensemble_pg_leaked_txn) == ensemble_pg_backends{state="idle_in_transaction"}``.
* **The fragment→site map is evaluated SERVER-SIDE** (one round trip,
  a ``CASE``/``LIKE`` inside the query) — not in Python over fetched
  query text. The query text NEVER leaves PostgreSQL: the SELECT list
  carries aggregates only, so no statement text is exported anywhere.
* **The ``other`` residual is never dropped** — the census total always
  equals the sum of its sites. A new leak site lands in ``other`` and
  the residual grows — that is the signal to extend the closed set (a
  deliberate, reviewed change).
* **Failure posture**: a census failure NEVER raises into the daemon.
  No series are rendered and the last-success timestamp stays STALE
  (absent ≠ zero).
* **Execution home**: a DEDICATED daemon thread
  (``time.monotonic`` tick loop) — not the listener thread (that one is
  request-driven ⇒ no series between scrapes) and not the event loop
  (it would die in a wedge, defeating the census's whole purpose).
* **The kill-switch gates the DB work**: with
  ``ENSEMBLE_METRICS_ENABLED`` off the sampler performs NO database
  work at all.

P0 SCOPE DISCIPLINE
-------------------

P0 ships the SAMPLER + its in-memory snapshot ONLY. The F6
``ensemble_pg_*`` families are P1a — this module deliberately registers
NO metric families. The snapshot is a frozen dataclass holding the
per-state counts, the per-site idle-in-transaction counts + ages and
the last-success timestamp.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)

#: The census connection's ``application_name`` — the self-exclusion
#: marker (catalog §9.1).
CENSUS_APPLICATION_NAME = "ensemble-metrics-census"

#: The per-statement timeout (MILLISECONDS) — 5 s, < ⅓ of the 15 s
#: cadence (arch §5 A8 point 2).
CENSUS_STATEMENT_TIMEOUT_MS = 5000

#: The connect timeout (SECONDS) — 3 s (arch §5 A8 point 2).
CENSUS_CONNECT_TIMEOUT_S = 3

#: The default census cadence (SECONDS). The floor is 15 s — the census
#: is NEVER faster than the resource monitor.
DEFAULT_CENSUS_INTERVAL_S = 15
MIN_CENSUS_INTERVAL_S = 15
MAX_CENSUS_INTERVAL_S = 300

#: The CLOSED fragment→site set (catalog §9.2). The order is the
#: evaluation order in the SQL ``CASE`` — the most specific pattern
#: first (``instance_parent_lookup`` before ``instance_read``, which
#: would otherwise swallow it).
CENSUS_SITES: tuple[str, ...] = (
    "instance_parent_lookup",
    "instance_read",
    "job_queue_read",
    "checkpoint_saver",
    "agent_db_tool",
    "other",
)

#: The residual bucket — NEVER dropped, so the census total always
#: equals the sum of its sites.
CENSUS_RESIDUAL_SITE = "other"

#: The pinned fragment patterns (catalog §9.2). Each entry is
#: ``(site, [like patterns])`` — the SQL ``CASE`` is generated from
#: THIS tuple, so the map lives in the module (NOT in a config file)
#: and the label set cannot drift.
CENSUS_SITE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "instance_parent_lookup",
        ("%from instances where instances.id =%",),
    ),
    (
        "instance_read",
        ("%from instance_hierarchy%", "%from instances%"),
    ),
    (
        "job_queue_read",
        ("%from job_queue_items%",),
    ),
    (
        "checkpoint_saver",
        ("%from checkpoint_blobs%", "%from checkpoints%"),
    ),
    (
        "agent_db_tool",
        ("%repair_log%",),
    ),
)


def _build_census_sql() -> str:
    """Build the ONE-round-trip census SQL.

    The fragment→site map is evaluated SERVER-SIDE (a ``CASE`` over
    ``ILIKE``) and the SELECT list carries AGGREGATES ONLY — the
    backend's statement text is never selected, so it can never be
    exported anywhere.
    """
    branches: list[str] = []
    for site, patterns in CENSUS_SITE_PATTERNS:
        condition = " OR ".join(
            f"query ILIKE {pattern!r}" for pattern in patterns
        )
        branches.append(f"WHEN {condition} THEN {site!r}")
    site_case = (
        "CASE " + " ".join(branches) + f" ELSE {CENSUS_RESIDUAL_SITE!r} END"
    )
    return f"""
SELECT
    'state'::text AS kind,
    COALESCE(state, 'unknown')::text AS key,
    COUNT(*)::bigint AS n,
    0::double precision AS max_age
FROM pg_stat_activity
WHERE datname = current_database()
  AND COALESCE(application_name, '') <> {CENSUS_APPLICATION_NAME!r}
GROUP BY 2
UNION ALL
SELECT
    'site'::text AS kind,
    {site_case} AS key,
    COUNT(*)::bigint AS n,
    COALESCE(MAX(EXTRACT(EPOCH FROM (now() - state_change))), 0)::double precision AS max_age
FROM pg_stat_activity
WHERE datname = current_database()
  AND COALESCE(application_name, '') <> {CENSUS_APPLICATION_NAME!r}
  AND state = 'idle in transaction'
GROUP BY 2
""".strip()


#: The census SQL — ONE round trip, server-side site mapping.
CENSUS_SQL = _build_census_sql()


def _strip_driver(dsn: str) -> str:
    """``postgresql+psycopg://`` → ``postgresql://`` (raw psycopg).

    Mirrors ``daemon/services/wedge_capture/db_capture.py`` — the
    census speaks raw psycopg, never SQLAlchemy.
    """
    if "://" not in dsn:
        return dsn
    scheme, _, rest = dsn.partition("://")
    if "+" in scheme:
        scheme = scheme.split("+", 1)[0]
    return f"{scheme}://{rest}"


def resolve_dsn(engine: Any = None) -> str | None:
    """The DSN for the census's own connection.

    Preference order (mirrors the wedge-capture's ``resolve_dsn``):

    1. the RUNNING engine's URL (the process's truth);
    2. ``POSTGRES_URL``;
    3. the ``POSTGRES_*`` parts.

    Returns ``None`` when the deployment is not PostgreSQL (a SQLite
    backend ⇒ the census is simply unavailable).
    """
    if engine is not None:
        url = getattr(engine, "url", None)
        if url is not None:
            try:
                rendered = url.render_as_string(hide_password=False)
            except Exception:  # pragma: no cover — defensive
                rendered = None
            if rendered:
                return _strip_driver(rendered)
    direct = os.environ.get("POSTGRES_URL")
    if direct:
        return _strip_driver(direct)
    host = os.environ.get("POSTGRES_HOST")
    db = os.environ.get("POSTGRES_DB")
    if host and db:
        from urllib.parse import quote_plus

        user = os.environ.get("POSTGRES_USER", "")
        password = os.environ.get("POSTGRES_PASSWORD", "")
        port = os.environ.get("POSTGRES_PORT", "5432")
        auth = f"{quote_plus(user)}:{quote_plus(password)}@" if user else ""
        return f"postgresql://{auth}{host}:{port}/{db}"
    return None


def connect_census(dsn: str) -> Any:
    """Open the census's own one-shot connection.

    A DIRECT ``psycopg.connect`` — never a pool. The timeouts and the
    ``application_name`` are the census's identity.
    """
    import psycopg

    return psycopg.connect(
        dsn,
        connect_timeout=CENSUS_CONNECT_TIMEOUT_S,
        application_name=CENSUS_APPLICATION_NAME,
        options=f"-c statement_timeout={CENSUS_STATEMENT_TIMEOUT_MS}",
    )


@dataclass(frozen=True)
class CensusSnapshot:
    """The latest census reading (P0: in-memory only — no F6 families).

    Attributes:
        state_counts: backend count per ``pg_stat_activity.state``.
        idle_in_txn_by_site: idle-in-transaction backend count per site.
        idle_in_txn_max_age_by_site: the OLDEST idle-in-transaction
            backend's age (seconds) per site.
        last_success_epoch: the wall-clock stamp of the last SUCCESSFUL
            census (``None`` before the first success). A failure leaves
            it STALE — absent ≠ zero.
    """

    state_counts: Mapping[str, int] = field(default_factory=dict)
    idle_in_txn_by_site: Mapping[str, int] = field(default_factory=dict)
    idle_in_txn_max_age_by_site: Mapping[str, float] = field(default_factory=dict)
    last_success_epoch: float | None = None

    @property
    def idle_in_txn_total(self) -> int:
        """The idle-in-transaction total (the sum of the sites)."""
        return sum(self.idle_in_txn_by_site.values())


class CensusSampler:
    """The census sampler (P0 T7 / T10).

    Args:
        interval_s: the tick cadence (SECONDS; floor 15 s).
        dsn: the census DSN. When ``None`` it is resolved from
            ``engine`` / the environment.
        engine: an optional SQLAlchemy engine (DSN source only — the
            census NEVER borrows its pool).
        gate_enabled: the kill-switch callable (defaults to the shared
            ``metrics_gate``).
        connect: the connection factory (injectable for tests).
    """

    def __init__(
        self,
        *,
        interval_s: int = DEFAULT_CENSUS_INTERVAL_S,
        dsn: str | None = None,
        engine: Any = None,
        gate_enabled: Callable[[], bool] | None = None,
        connect: Callable[[str], Any] | None = None,
    ) -> None:
        self._interval_s = max(
            MIN_CENSUS_INTERVAL_S, min(MAX_CENSUS_INTERVAL_S, int(interval_s))
        )
        self._dsn = dsn
        self._engine = engine
        self._gate_enabled = gate_enabled
        self._connect = connect or connect_census
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot: CensusSnapshot | None = None
        self._last_success_monotonic: float | None = None
        self._started = False

    # ─── introspection ────────────────────────────────────────────────

    @property
    def interval_s(self) -> int:
        return self._interval_s

    @property
    def snapshot(self) -> CensusSnapshot | None:
        """The latest successful snapshot (``None`` before the first)."""
        return self._snapshot

    @property
    def running(self) -> bool:
        return self._started

    @property
    def dsn(self) -> str | None:
        """The effective DSN (resolved lazily)."""
        if self._dsn:
            return self._dsn
        self._dsn = resolve_dsn(self._engine)
        return self._dsn

    # ─── the gate ─────────────────────────────────────────────────────

    def _gate(self) -> bool:
        if self._gate_enabled is not None:
            return bool(self._gate_enabled())
        from daemon.services import metrics_gate

        # A PURE read — the census never refreshes the managed tier
        # (that is the listener's job; the census must not touch the
        # DB for a boolean).
        return metrics_gate.metrics_enabled()

    # ─── the census ───────────────────────────────────────────────────

    def collect(self) -> CensusSnapshot | None:
        """Run ONE census.

        Returns the snapshot, or ``None`` when the census failed (the
        failure never raises into the daemon and the last-success
        timestamp stays stale).
        """
        dsn = self.dsn
        if not dsn:
            return None
        try:
            rows = self._query(dsn)
        except Exception as exc:  # noqa: BLE001 — never raises
            logger.warning(
                "[MetricsCensus] census failed (%s: %s) — the last-success "
                "timestamp stays stale",
                type(exc).__name__,
                exc,
            )
            return None
        state_counts: dict[str, int] = {}
        by_site: dict[str, int] = {}
        age_by_site: dict[str, float] = {}
        for row in rows:
            kind, key, count, max_age = row
            if kind == "state":
                state_counts[str(key)] = int(count)
            elif kind == "site":
                by_site[str(key)] = int(count)
                age_by_site[str(key)] = float(max_age or 0.0)
        # The residual is never dropped — an absent site simply has no
        # rows in this reading.
        for site in CENSUS_SITES:
            by_site.setdefault(site, 0)
            age_by_site.setdefault(site, 0.0)
        self._last_success_monotonic = time.monotonic()
        snapshot = CensusSnapshot(
            state_counts=state_counts,
            idle_in_txn_by_site=by_site,
            idle_in_txn_max_age_by_site=age_by_site,
            last_success_epoch=time.time(),
        )
        # A SUCCESSFUL census replaces the stored snapshot; a failure
        # leaves the previous one (and its last-success stamp) STALE.
        self._snapshot = snapshot
        return snapshot

    def _query(self, dsn: str) -> list[tuple[Any, ...]]:
        """Run the ONE-round-trip census query (never exports query text)."""
        conn = self._connect(dsn)
        try:
            with conn.cursor() as cur:
                cur.execute(CENSUS_SQL)
                rows = cur.fetchall()
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 — defensive
                pass
        return list(rows or [])

    # ─── the tick loop ────────────────────────────────────────────────

    def _run(self) -> None:
        """The tick loop — ``time.monotonic`` cadence, daemon thread."""
        while not self._stop_event.is_set():
            # The kill-switch gates the DB WORK: with the switch off the
            # sampler performs NO database work at all.
            if self._gate():
                # ``collect`` stores the snapshot on success; a failure
                # leaves the previous one stale.
                self.collect()
            self._stop_event.wait(timeout=self._interval_s)

    def start(self) -> bool:
        """Start the sampler thread (idempotent)."""
        if self._started:
            return True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="metrics-census",
            daemon=True,
        )
        self._thread.start()
        self._started = True
        logger.info(
            "[MetricsCensus] started: interval=%ds application_name=%s",
            self._interval_s,
            CENSUS_APPLICATION_NAME,
        )
        return True

    def stop(self) -> threading.Thread | None:
        """Stop the sampler — DETACHED (never blocks the event loop).

        The stop event is set and the join happens on a DETACHED daemon
        thread (the house precedent: ``daemon/api.py:3411-3433``).
        """
        self._stop_event.set()
        thread = self._thread
        if thread is None:
            self._started = False
            return None

        def _join() -> None:
            try:
                thread.join(timeout=5.0)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[MetricsCensus] join error: %s", exc)

        try:
            detached = threading.Thread(
                target=_join, name="metrics-census-shutdown", daemon=True
            )
            detached.start()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[MetricsCensus] detached join failed: %s", exc)
            detached = None
        self._started = False
        return detached
