"""Promote-time backup-artifact validation — the verdict module
(promote-db-preflight-validation, phase 1 / T1).

NORMATIVE SOURCE: ``.agents/shared/planning/promote-db-preflight-validation/``
— ``architecture-recommendation.md`` §3-§5, §10 (the design of record) as
AMENDED by ``decisions.md`` §PREDICATE LEDGER (APPROVED, approver iteration
003) and ``phase1-plan.md``. The ledger strings are the contract and are
reproduced here character-for-character; nothing in this module restates a
predicate loosely.

WHAT THIS IS. The ceremony no longer dumps the live database per promote
(the Emperor's ruling: "ceremony's preflight is only a validation … check
the latest backup, remember it, if that backup already checked, skip").
Instead, at arm time the daemon reads the dump lane's OWN registry table
(``public.dump_lane_artifacts`` — externally owned, written by the
cluster-side read-only publisher) over the sanctioned pooled PostgreSQL
channel and asserts, on the NEWEST artifact row for lane ``pgdumpall``: the
publisher's own ``integrity_ok`` verdict, a non-zero byte count, and a
``finished_at`` inside the freshness bound. The bytes are NEVER read,
checksummed or restored — this is a metadata validation, never a byte-level
one, and it never claims to be more.

PINS COVERED (phase1-plan.md §TEST MATRIX):

* (a) MASKING — the newest row OVERALL is selected regardless of
  ``integrity_ok`` (``ORDER BY finished_at DESC, artifact DESC LIMIT 1`` on
  the TABLE), then ``integrity_ok IS TRUE`` is asserted on THAT row. The
  heartbeat VIEW is never substituted: it filters ``WHERE integrity_ok``
  and would mask a broken newest artifact.
* (b) SKIP + STAMP-ADVANCE (D4 AMENDMENT) — ``skip ⇔ (identity_unchanged
  AND finished_at_age_s <= DB_PREFLIGHT_MAX_AGE_S AND integrity_ok IS TRUE
  AND bytes > 0)``, evaluated on the FETCHED row. EVERY gate runs on EVERY
  invocation; NOTHING is short-circuited — ``integrity_ok`` is the one
  registry field the publisher can flip under an unchanged identity.
* (c) LOUD FAILURE — relation absent / unreadable / query failed /
  connection refused / pool exhausted / either-side timeout / a
  future-dated row beyond the skew tolerance ⇒
  ``db-preflight-registry-unavailable``; zero rows ⇒
  ``db-preflight-no-backup``. NULL columns on the externally-owned schema
  refuse BY NAME before any comparison (never a ``TypeError`` inside the
  held lock).
* (d) OPT-IN (D6) — ``full-dump`` ⇒ ``db-preflight-full-dump-unavailable``;
  an unrecognised mode ⇒ ``db-preflight-mode-unknown``; absent ⇒
  validation-only. The gate applies in EVERY environment and at BOTH the
  dry-run and the arm — :func:`mode_gate_refusal` is the ONE implementation
  both sites call, so the token can never drift between them.
* (e) TRANSACTION — the arm-path caller (``upgrade_tools.system_upgrade``)
  runs :func:`evaluate_db_preflight` under the held journal lock, before
  the nonce burn, inside the LOCK-RELEASE BELT. This module never takes
  the lock, never mutates the journal and never spawns a process.
* (f) DURABILITY — the remembered identity is the journal's top-level
  ``db_preflight`` key (D3): ONE compact object, written by the caller
  through ``upgrade_journal.record_db_preflight`` ONLY on a
  ``validated``/``skipped`` verdict, never on a refusal.

ENVIRONMENT GATE (M2): the registry validation RUNS only when
``self_env == "live"`` — the registry lives in the live database and
dev/demo/sandbox have no such relation, so a non-live check would ALWAYS
refuse. On non-live the result is ``skipped-by-env``: NO DB read, NO
refusal, NO journal row, NO record write; the caller SURFACES the skip in
the armed output (never a silent pass). There is deliberately NO
kill-switch: the direct shell path is already the documented escape (F1).

`db_preflight` (this module — promote-time backup-artifact validation) is UNRELATED to the boot-time `_boot_db_preflight` (`daemon/__main__.py:215`, a DB-connectivity probe) — name proximity only, no shared state.

TORN JOURNALS: reading the remembered record goes through
``upgrade_journal.journal_read`` — a torn (or absent, or unparseable)
journal raises ``upgrade_journal.JournalTorn`` OUT of
:func:`evaluate_db_preflight`; the CALLER maps it to
``db-preflight-journal-torn``, the one refusal that writes NO journal row
(the journal is the unavailable substrate on that path). This module never
returns that token itself.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.exc import (
    DBAPIError,
    InterfaceError,
    OperationalError,
    ProgrammingError,
    SQLAlchemyError,
)
from sqlalchemy.exc import TimeoutError as SATimeoutError

from daemon.tools import upgrade_journal as uj
from daemon.tools.ens_db_tools import (
    SELECT_CLIENT_TIMEOUT_S,
    SELECT_TX_STATEMENT_TIMEOUT_MS,
)

if TYPE_CHECKING:
    from pathlib import Path

    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)


# ── Constants (decisions.md §PREDICATE LEDGER — verbatim) ────────────────────

DB_PREFLIGHT_LANE = "pgdumpall"
DB_PREFLIGHT_MAX_AGE_S = uj._env_int("ENSEMBLE_PROMOTE_DB_PREFLIGHT_MAX_AGE_S", 172800)
DB_PREFLIGHT_MODES = {"validation-only", "full-dump"}
DB_PREFLIGHT_CLOCK_SKEW_TOLERANCE_S = 300

# The EIGHT-token refusal family (one greppable kebab-case family). The
# dropped ninth token (the artifact-name-vs-finished_at conflict) is
# deliberately ABSENT — see decisions.md §DELETION RECORD.
TOK_REGISTRY_UNAVAILABLE = "db-preflight-registry-unavailable"
TOK_NO_BACKUP = "db-preflight-no-backup"
TOK_ARTIFACT_BROKEN = "db-preflight-artifact-broken"
TOK_ARTIFACT_EMPTY = "db-preflight-artifact-empty"
TOK_ARTIFACT_STALE = "db-preflight-artifact-stale"
TOK_JOURNAL_TORN = "db-preflight-journal-torn"
TOK_FULL_DUMP_UNAVAILABLE = "db-preflight-full-dump-unavailable"
TOK_MODE_UNKNOWN = "db-preflight-mode-unknown"
DB_PREFLIGHT_TOKENS: frozenset[str] = frozenset(
    {
        TOK_REGISTRY_UNAVAILABLE,
        TOK_NO_BACKUP,
        TOK_ARTIFACT_BROKEN,
        TOK_ARTIFACT_EMPTY,
        TOK_ARTIFACT_STALE,
        TOK_JOURNAL_TORN,
        TOK_FULL_DUMP_UNAVAILABLE,
        TOK_MODE_UNKNOWN,
    }
)

# The journal-ALWAYS row names (decisions.md §Journal-always row names).
EVENT_VALIDATED = "db_preflight_validated"
EVENT_SKIPPED = "db_preflight_skipped"
EVENT_REFUSED = "db_preflight_refused"

# The verdict names.
VERDICT_VALIDATED = "validated"
VERDICT_SKIPPED = "skipped"
VERDICT_SKIPPED_BY_ENV = "skipped-by-env"
VERDICT_REFUSED = "refused"

# The selection predicate (decisions.md §Selection predicate — verbatim).
_SELECT_NEWEST_SQL = text(
    "SELECT lane, artifact, bytes, finished_at, integrity_ok,\n"
    "       first_seen_at, last_seen_at\n"
    "FROM public.dump_lane_artifacts\n"
    "WHERE lane = :lane\n"
    "ORDER BY finished_at DESC, artifact DESC\n"
    "LIMIT 1"
)

# The columns the verdict needs (the rest of the row is diagnostics only).
_REQUIRED_COLUMNS = ("lane", "artifact", "bytes", "finished_at", "integrity_ok")

# PG SQLSTATE → operator-facing cause. The detail NEVER carries the raw
# driver message (it can carry host/port text and the detail lands in the
# journal AND the ceremony output) — only the class name + the code.
_PG_SQLSTATE_HINTS = {
    "42P01": "relation absent",
    "42501": "insufficient privilege",
    "42703": "schema changed (column absent)",
    "3F000": "schema absent",
    "57014": "statement timeout",
}


def _sqlstate(exc: BaseException) -> str | None:
    """The PG SQLSTATE of a wrapped DBAPI error, when the driver exposes one."""
    orig = getattr(exc, "orig", None)
    if orig is None:
        return None
    raw = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return str(raw) if raw else None


def _code(sqlstate: str | None) -> str:
    return f" sqlstate={sqlstate}" if sqlstate else ""


def _iso_z(moment: datetime) -> str:
    """``<ISO-8601 Z>`` — the persisted timestamp form (microseconds kept)."""
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def identity_fields(
    lane: str, artifact: str, bytes: int, finished_at: datetime
) -> tuple[datetime, str, str]:
    """The normalised identity triple: ``(finished_at_utc, finished_at_iso, fingerprint_sha256)``.

    The three lines below are decisions.md §Identity tuple + fingerprint
    formula VERBATIM — the fingerprint is the contract, not a paraphrase.
    """
    finished_at_utc = finished_at.astimezone(timezone.utc) if finished_at.tzinfo else finished_at.replace(tzinfo=timezone.utc)
    finished_at_iso = finished_at_utc.isoformat().replace("+00:00", "Z")
    fingerprint_sha256 = sha256(json.dumps([lane, artifact, bytes, finished_at_iso], separators=(",", ":")).encode()).hexdigest()
    return finished_at_utc, finished_at_iso, fingerprint_sha256


@dataclass(frozen=True)
class ArtifactRow:
    """The newest-overall registry row, normalised (every gate input non-NULL).

    Built from the raw fetched mapping AFTER the NULL guards — and directly
    constructible as the fixture shape a stubbed seam returns:
    ``ArtifactRow(lane="pgdumpall", artifact="<name>", bytes=1,
    finished_at=<aware datetime>, integrity_ok=True)``. The derived fields
    (``finished_at_utc`` / ``finished_at_iso`` / ``fingerprint_sha256``)
    compute themselves.

    ``first_seen_at`` / ``last_seen_at`` ride along as DIAGNOSTICS ONLY —
    they measure the publisher's cadence, never the backup's age, and are
    never a freshness input.
    """

    lane: str
    artifact: str
    bytes: int
    finished_at: datetime
    integrity_ok: bool
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    finished_at_utc: datetime = field(init=False, repr=False)
    finished_at_iso: str = field(init=False)
    fingerprint_sha256: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        utc, iso, fingerprint = identity_fields(
            self.lane, self.artifact, self.bytes, self.finished_at
        )
        object.__setattr__(self, "finished_at_utc", utc)
        object.__setattr__(self, "finished_at_iso", iso)
        object.__setattr__(self, "fingerprint_sha256", fingerprint)


@dataclass(frozen=True)
class DbPreflightVerdict:
    """The structured result of :func:`evaluate_db_preflight`.

    ``verdict`` is ONE of:

    * ``validated`` — a fresh validation passed; ``record`` is the D3
      object to write (and the caller writes it);
    * ``skipped`` — the identity is unchanged AND every gate still passed;
      ``record`` mutates ONLY ``last_checked_at`` + ``verdict``;
    * ``skipped-by-env`` — non-live (M2): NO DB read, NO refusal, NO
      journal row, NO record write; the caller SURFACES the skip;
    * ``refused`` — ``token`` is one of the eight-token family and
      ``detail`` carries the refusal detail (the M7 format).

    ``detail`` is the JOURNAL detail string in both directions — for a
    refusal the artifact-bearing ``token=<t> artifact=<n> age=<s>s
    bound=<s>s`` or the artifact-less ``token=<t> reason=<t> <free-text>``;
    for a pass ``artifact=<n> finished_at=<iso> age=<s>s bound=<s>s
    verdict=<v>``.
    """

    verdict: str
    token: str | None = None
    detail: str = ""
    record: dict[str, Any] | None = None
    artifact: str | None = None
    finished_at_iso: str | None = None
    age_s: int = 0
    bound_s: int = 0
    fingerprint_sha256: str | None = None
    identity_unchanged: bool | None = None

    @property
    def refused(self) -> bool:
        """True iff this verdict is a refusal."""
        return self.verdict == VERDICT_REFUSED

    @classmethod
    def refusal(cls, token: str, detail: str) -> DbPreflightVerdict:
        """A refusal verdict carrying the token and the M7 detail."""
        return cls(verdict=VERDICT_REFUSED, token=token, detail=detail)

    @classmethod
    def validated(cls, row: ArtifactRow) -> DbPreflightVerdict:
        """A PASSING ``validated`` verdict for ``row`` — the fresh-validation
        shape, and the shape a stubbed seam returns."""
        return _pass_verdict(
            row, VERDICT_VALIDATED, None, datetime.now(timezone.utc)
        )


def _artifact_bearing(token: str, artifact: str, age_s: int, bound_s: int) -> str:
    """The artifact-bearing refusal detail (decisions.md §Refusal detail format)."""
    return f"token={token} artifact={artifact} age={age_s}s bound={bound_s}s"


def _artifact_less(token: str, free_text: str) -> str:
    """The artifact-less refusal detail — the ``reason=`` value LEADS with the
    token (post-approval note N1: ``upgrade_journal._reason_token`` extracts
    the FIRST run after ``reason=``, so a free-text first word would hide the
    real token)."""
    return f"token={token} reason={token} {free_text}"


def _pass_verdict(
    row: ArtifactRow,
    verdict: str,
    remembered: dict[str, Any] | None,
    now_utc: datetime,
) -> DbPreflightVerdict:
    """Build a ``validated``/``skipped`` verdict + its D3 record.

    On a skip the record mutation is LIMITED to ``last_checked_at`` +
    ``verdict`` — the validated tuple and the fingerprint are left untouched
    (the identity is unchanged by definition).
    """
    age_s = int(max((now_utc - row.finished_at_utc).total_seconds(), 0))
    bound_s = int(DB_PREFLIGHT_MAX_AGE_S)
    if verdict == VERDICT_SKIPPED and isinstance(remembered, dict):
        record = dict(remembered)
    else:
        record = {
            "lane": row.lane,
            "artifact": row.artifact,
            "bytes": row.bytes,
            "finished_at": row.finished_at_iso,
            "fingerprint_sha256": row.fingerprint_sha256,
        }
    record["last_checked_at"] = _iso_z(now_utc)
    record["verdict"] = verdict
    suffix = "" if verdict == VERDICT_VALIDATED else " identity=unchanged"
    return DbPreflightVerdict(
        verdict=verdict,
        detail=(
            f"artifact={row.artifact} finished_at={row.finished_at_iso} "
            f"age={age_s}s bound={bound_s}s verdict={verdict}{suffix}"
        ),
        record=record,
        artifact=row.artifact,
        finished_at_iso=row.finished_at_iso,
        age_s=age_s,
        bound_s=bound_s,
        fingerprint_sha256=row.fingerprint_sha256,
        identity_unchanged=(verdict == VERDICT_SKIPPED),
    )


def mode_gate_refusal(mode: str | None) -> DbPreflightVerdict | None:
    """The D6 MODE gate — the ONE implementation both the dry-run and the arm
    path call, so the token can never drift between the two sites.

    ``full-dump`` ⇒ ``db-preflight-full-dump-unavailable`` (no VM-side dump
    leg exists — never a silent fallback); an unrecognised mode ⇒
    ``db-preflight-mode-unknown``; absent ⇒ validation-only proceeds (None).
    The gate applies in EVERY environment — a refused mode never reaches the
    registry, so it never depends on it.
    """
    if mode is None:
        return None
    if mode == "full-dump":
        return DbPreflightVerdict.refusal(
            TOK_FULL_DUMP_UNAVAILABLE,
            _artifact_less(
                TOK_FULL_DUMP_UNAVAILABLE,
                "full-dump requested but no VM-side dump leg exists — the "
                "ceremony validates the newest daily backup artifact instead "
                "(metadata validation; never a silent fallback)",
            ),
        )
    if mode not in DB_PREFLIGHT_MODES:
        return DbPreflightVerdict.refusal(
            TOK_MODE_UNKNOWN,
            _artifact_less(
                TOK_MODE_UNKNOWN,
                f"unrecognised db_preflight mode {mode!r} — the recognised "
                "modes are validation-only (the default when absent) and "
                "full-dump",
            ),
        )
    return None


def registry_failure_detail(exc: BaseException) -> str:
    """The free text naming WHY the registry could not be read — ONE DISTINCT
    string per cause (the two connection causes never share a detail).

    The raw driver message is deliberately NOT echoed: it can carry
    host/port text and this detail lands in the journal AND the ceremony
    output. The exception CLASS and the PG SQLSTATE are enough to act on.
    """
    if isinstance(exc, asyncio.TimeoutError):
        return (
            f"select timed out after {SELECT_CLIENT_TIMEOUT_S}s (client belt; "
            f"the server-side statement_timeout={int(SELECT_TX_STATEMENT_TIMEOUT_MS)}ms "
            "remains the real cap)"
        )
    if isinstance(exc, SATimeoutError):
        return (
            f"pool exhausted ({type(exc).__name__}) — the pooled engine had no "
            "connection to check out"
        )
    sqlstate = _sqlstate(exc)
    if isinstance(exc, InterfaceError):
        return f"connect refused ({type(exc).__name__}{_code(sqlstate)})"
    if isinstance(exc, OperationalError):
        if sqlstate == "57014":
            return (
                f"statement timeout ({type(exc).__name__}{_code(sqlstate)} — the "
                "server-side statement_timeout fired)"
            )
        if (
            sqlstate is None
            or sqlstate.startswith("08")
            or sqlstate in ("53300", "57P03")
        ):
            return f"connect refused ({type(exc).__name__}{_code(sqlstate)})"
        return f"query failed ({type(exc).__name__}{_code(sqlstate)})"
    if isinstance(exc, ProgrammingError):
        hint = _PG_SQLSTATE_HINTS.get(sqlstate or "", "relation absent or unreadable")
        return f"{hint} ({type(exc).__name__}{_code(sqlstate)})"
    if isinstance(exc, (DBAPIError, SQLAlchemyError)):
        return f"query failed ({type(exc).__name__}{_code(sqlstate)})"
    return f"query failed ({type(exc).__name__})"


def _run_select(manager: InstanceManager) -> dict[str, Any] | None:
    """The SYNC body of the registry read — it runs on the ``to_thread``
    worker. Returns the newest-overall row as a plain dict, or ``None`` for
    zero rows.

    The pooled ENGINE is reused (never a second engine) — but reusing the
    ENGINE is not a promise that no NEW physical connection is opened:
    ``engine.begin()`` checks a connection OUT of the pool and may open one.
    That is why a refused connection and a pool-exhaustion checkout are TWO
    distinct causes (see :func:`registry_failure_detail`).
    """
    engine = manager.engine
    with engine.begin() as conn:
        # Per-tx statement_timeout inside this transaction only (NOT
        # engine-wide connect_args) — PostgreSQL only; SQLite has no
        # ``SET LOCAL statement_timeout`` (the ens_db_tools precedent).
        if engine.url.get_backend_name().startswith("postgres"):
            conn.execute(
                text(f"SET LOCAL statement_timeout = {int(SELECT_TX_STATEMENT_TIMEOUT_MS)}")
            )
        row = conn.execute(_SELECT_NEWEST_SQL, {"lane": DB_PREFLIGHT_LANE}).mappings().first()
    return dict(row) if row is not None else None


async def fetch_newest_artifact(manager: InstanceManager) -> dict[str, Any] | None:
    """ONE parameterised SELECT against ``public.dump_lane_artifacts``.

    The house ``ens_db_postgres_select`` bridge: the sync engine call runs
    on ``asyncio.to_thread`` under ``asyncio.wait_for(SELECT_CLIENT_TIMEOUT_S)``
    — the client belt cancels the awaitable but does NOT abort the blocking
    driver call, so the server-side ``statement_timeout`` is the real cap.

    Exceptions PROPAGATE (the caller classifies them by class — see
    :func:`registry_failure_detail`); ``None`` means zero rows. NO
    test-awareness: an engine that yields no readable row is a REFUSAL,
    never a pass.
    """
    return await asyncio.wait_for(
        asyncio.to_thread(_run_select, manager), timeout=SELECT_CLIENT_TIMEOUT_S
    )


def _remembered_identity(remembered: Any) -> tuple[Any, ...] | None:
    """The remembered record as the NORMALISED identity tuple, or ``None``
    when it is absent/malformed (⇒ the identity is NOT unchanged ⇒ the
    artifact is re-validated).

    The comparison is made on the NORMALISED forms on BOTH sides:
    ``finished_at`` as ``finished_at_iso`` (the remembered value is a
    STRING — a raw datetime never equals its own stored string) and
    ``bytes`` as ``int``.
    """
    if not isinstance(remembered, dict):
        return None
    try:
        return (
            str(remembered["lane"]),
            str(remembered["artifact"]),
            int(remembered["bytes"]),
            str(remembered["finished_at"]),
            str(remembered["fingerprint_sha256"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


async def evaluate_db_preflight(
    manager: InstanceManager,
    install_dir: Path,
    *,
    mode: str | None = None,
    self_env: str = "live",
) -> DbPreflightVerdict:
    """THE INJECTABLE SEAM — the arm path calls ``udp.evaluate_db_preflight(...)``
    (a MODULE-ATTRIBUTE lookup at call time, so a test patches THIS
    attribute).

    The order is load-bearing: (d) the mode gate (every environment) →
    (M2) the environment gate → read the remembered record (a torn journal
    raises ``JournalTorn`` OUT of here) → (c) the registry read → (0b) the
    NULL guards → (3) integrity → (4) bytes → (5b) the clock skew → (5) the
    freshness bound → the identity comparison → ``validated`` | ``skipped``.

    READS ONLY: it writes nothing, takes no lock and spawns nothing. The
    caller journals, records and refuses.
    """
    mode_refusal = mode_gate_refusal(mode)
    if mode_refusal is not None:
        return mode_refusal
    if self_env != "live":
        # (M2) The registry lives in the live database only — dev/demo/
        # sandbox have no such relation, so the validation is a documented
        # no-op there (never a refusal, never a silent pass: the caller
        # surfaces the skip).
        return DbPreflightVerdict(
            verdict=VERDICT_SKIPPED_BY_ENV,
            detail=(
                f"registry validation skipped (self_env={self_env}; the "
                "backup-artifact registry lives in the live database only)"
            ),
        )

    data = uj.journal_read(install_dir)  # JournalTorn propagates (caller maps it)
    remembered = data.get("db_preflight")
    remembered = remembered if isinstance(remembered, dict) else None

    try:
        raw = await fetch_newest_artifact(manager)
    except Exception as exc:  # noqa: BLE001 — classified below, never a pass
        logger.warning(
            "upgrade_db_preflight: registry read FAILED (%s: %s) — refusing "
            "db-preflight-registry-unavailable",
            type(exc).__name__,
            exc,
        )
        return DbPreflightVerdict.refusal(
            TOK_REGISTRY_UNAVAILABLE,
            _artifact_less(TOK_REGISTRY_UNAVAILABLE, registry_failure_detail(exc)),
        )
    if raw is None:
        return DbPreflightVerdict.refusal(
            TOK_NO_BACKUP,
            _artifact_less(
                TOK_NO_BACKUP,
                f"zero rows for lane {DB_PREFLIGHT_LANE} in "
                "public.dump_lane_artifacts",
            ),
        )

    # (0b) NULL GUARDS — the schema is EXTERNALLY OWNED with no repo
    # contract, so a nullable column or a publisher change must refuse
    # loudly, never raise inside the held lock. They sit UPSTREAM of every
    # comparison and change NONE of the ledger predicates.
    missing = [column for column in _REQUIRED_COLUMNS if column not in raw]
    if missing:
        return DbPreflightVerdict.refusal(
            TOK_REGISTRY_UNAVAILABLE,
            _artifact_less(
                TOK_REGISTRY_UNAVAILABLE,
                f"registry row unreadable (columns absent from the fetched "
                f"newest row: {', '.join(missing)})",
            ),
        )
    for column in ("artifact", "bytes", "finished_at"):
        if raw[column] is None:
            return DbPreflightVerdict.refusal(
                TOK_REGISTRY_UNAVAILABLE,
                _artifact_less(
                    TOK_REGISTRY_UNAVAILABLE,
                    f"{column} is NULL on the newest row (externally-owned "
                    "schema violation)",
                ),
            )
    lane = raw["lane"]
    artifact = raw["artifact"]
    size_bytes = raw["bytes"]
    finished_at = raw["finished_at"]
    for column, value, expected in (
        ("lane", lane, str),
        ("artifact", artifact, str),
        ("finished_at", finished_at, datetime),
    ):
        if not isinstance(value, expected):
            return DbPreflightVerdict.refusal(
                TOK_REGISTRY_UNAVAILABLE,
                _artifact_less(
                    TOK_REGISTRY_UNAVAILABLE,
                    f"{column} has an unexpected type ({type(value).__name__}) "
                    "on the newest row (externally-owned schema change)",
                ),
            )
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int):
        return DbPreflightVerdict.refusal(
            TOK_REGISTRY_UNAVAILABLE,
            _artifact_less(
                TOK_REGISTRY_UNAVAILABLE,
                f"bytes has an unexpected type ({type(size_bytes).__name__}) "
                "on the newest row (externally-owned schema change)",
            ),
        )

    row = ArtifactRow(
        lane=lane,
        artifact=artifact,
        bytes=size_bytes,
        finished_at=finished_at,
        # Only the literal True passes the IS TRUE assertion — a NULL never
        # passes (and a truthy non-boolean never passes either).
        integrity_ok=raw["integrity_ok"] is True,
        first_seen_at=raw.get("first_seen_at"),
        last_seen_at=raw.get("last_seen_at"),
    )

    # The freshness bound — measured on the artifact's OWN ``finished_at``,
    # normalised (the SAME normalisation the fingerprint applies, so the age
    # and the identity can never disagree about the timezone), never on
    # ``first_seen_at`` / ``last_seen_at``.
    # The freshness-bound predicate (decisions.md §Freshness-bound predicate —
    # VERBATIM):
    #   now_utc = datetime.now(timezone.utc)
    #   raw_age_s = (now_utc - finished_at_utc).total_seconds()
    #   raw_age_s < -DB_PREFLIGHT_CLOCK_SKEW_TOLERANCE_S (default 300) ⇒
    #       refuse db-preflight-registry-unavailable
    #   finished_at_age_s = max(raw_age_s, 0)
    #   finished_at_age_s <= DB_PREFLIGHT_MAX_AGE_S (default 172800)
    finished_at_utc = row.finished_at_utc
    now_utc = datetime.now(timezone.utc)
    raw_age_s = (now_utc - finished_at_utc).total_seconds()
    age_s = int(max(raw_age_s, 0))

    integrity_ok = row.integrity_ok
    size_bytes = row.bytes

    # (3) the newest OVERALL row must carry the publisher's own verdict.
    if integrity_ok is not True:
        return DbPreflightVerdict.refusal(
            TOK_ARTIFACT_BROKEN,
            _artifact_bearing(
                TOK_ARTIFACT_BROKEN, row.artifact, age_s, int(DB_PREFLIGHT_MAX_AGE_S)
            ),
        )
    # (4) non-empty bytes.
    if size_bytes <= 0:
        return DbPreflightVerdict.refusal(
            TOK_ARTIFACT_EMPTY,
            _artifact_bearing(
                TOK_ARTIFACT_EMPTY, row.artifact, age_s, int(DB_PREFLIGHT_MAX_AGE_S)
            ),
        )
    # (5b) the clock-skew guard — a future-dated row BEYOND the tolerance is
    # refused (fail-closed, no new token); a negative age WITHIN the
    # tolerance is clamped to 0 for the bound comparison ONLY.
    if raw_age_s < -DB_PREFLIGHT_CLOCK_SKEW_TOLERANCE_S:
        return DbPreflightVerdict.refusal(
            TOK_REGISTRY_UNAVAILABLE,
            _artifact_less(
                TOK_REGISTRY_UNAVAILABLE,
                "finished_at in the future beyond skew tolerance "
                f"(age={int(raw_age_s)}s tolerance={DB_PREFLIGHT_CLOCK_SKEW_TOLERANCE_S}s)",
            ),
        )
    finished_at_age_s = max(raw_age_s, 0)
    # (5) the freshness bound.
    if finished_at_age_s > DB_PREFLIGHT_MAX_AGE_S:
        return DbPreflightVerdict.refusal(
            TOK_ARTIFACT_STALE,
            _artifact_bearing(
                TOK_ARTIFACT_STALE, row.artifact, age_s, int(DB_PREFLIGHT_MAX_AGE_S)
            ),
        )

    # The identity comparison — on the FETCHED row, on the NORMALISED forms
    # (decisions.md §Skip predicate — VERBATIM):
    #   identity_unchanged ⇔ (lane, artifact, bytes, finished_at,
    #                         fingerprint_sha256) == remembered
    # `finished_at` is compared as `finished_at_iso` on BOTH sides (the
    # remembered value is persisted as the ISO-8601 Z string, so a raw
    # datetime — which never equals its own stored string — must never be
    # compared to it directly) and `bytes` compares as `int`.
    identity_unchanged = _remembered_identity(remembered) == (
        row.lane,
        row.artifact,
        row.bytes,
        row.finished_at_iso,
        row.fingerprint_sha256,
    )
    # The skip predicate (decisions.md §Skip predicate — VERBATIM):
    #   skip ⇔ (identity_unchanged AND finished_at_age_s <=
    #           DB_PREFLIGHT_MAX_AGE_S AND integrity_ok IS TRUE AND bytes > 0)
    # The three gate conjuncts are already proven by the refusals above (every
    # gate runs on every invocation; nothing is short-circuited) — they are
    # restated here so the predicate reads exactly as the ledger states it.
    skip = (
        identity_unchanged
        and finished_at_age_s <= DB_PREFLIGHT_MAX_AGE_S
        and integrity_ok is True
        and size_bytes > 0
    )
    return _pass_verdict(
        row,
        VERDICT_SKIPPED if skip else VERDICT_VALIDATED,
        remembered,
        now_utc,
    )
