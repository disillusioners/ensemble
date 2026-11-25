#!/usr/bin/env python3
"""Operator probe for the nightly pg_dumpall dump lane — READ-ONLY, fail-closed.

WHY THIS EXISTS
---------------
The live VM's database is backed up by a **cluster-side** nightly ``pg_dumpall``
CronJob (k8s psql18 behind the ``10.44.0.2:5432`` LB; db ``ensemble_prod``,
user ``ensemble`` — see ``docs/deployment.md`` §topology). The daemon on the VM
**cannot see kubernetes**, and the dump lane has no failure signal that reaches
the VM: when the CronJob stops running, stops succeeding, or starts failing,
**nothing on the VM goes red**. That is exactly the silent-outage shape this
probe closes — a backup lane that can be dead for weeks while every VM-side
health check stays green.

So the signal belongs on the **maintenancer / devops PROBE side**, not in the
daemon. This script is that probe: a small, read-only operator tool that
answers one question — *when did the nightly dump last SUCCEED?* — and answers
it LOUDLY when the answer is bad.

THE MARKER
----------
The last-success timestamp comes from a **heartbeat row the CronJob side
writes**, which a NON-k8s probe can observe over the ONE channel that already
exists between the cluster and the VM: PostgreSQL. The CronJob upserts exactly
ONE row (``lane='pgdumpall'``, ``last_success_at timestamptz``) and ONLY
AFTER a successful ``pg_dumpall`` exit 0. The cluster-side step is an
OPERATOR-ACTIONABLE change (it is a CronJob edit + a table DDL) — it is NOT
daemon code and is NOT implemented here; the runbook
``docs/runbooks/dump-lane-health-probe.md`` carries the copy-paste SQL and the
CronJob edit.

TWO PROVIDERS, ONE STATE MACHINE
---------------------------------
``--provider pg``    read-only ``SELECT`` of the heartbeat row (the production
                     shape; a non-k8s probe reaching the cluster's database).
``--provider file``  the mtime of a marker/artifact file the operator points at
                     with ``--marker-file`` (the immediately-usable fallback,
                     and the hermetic test shape).

Both providers feed the SAME state machine and the SAME exit-code contract.

EXIT CODES (the contract — documented in the design doc AND the runbook)
-------------------------------------------------------------------------
    0   FRESH          — the last success is within the threshold.
    75  NO_MARKER      — the health gate could NOT be established: the marker
                         is absent, unreadable, the row is missing, or the
                         database was unreachable. (Absence of evidence.)
    76  STALE          — the last success is OLDER than the threshold.
    78  CONFIG         — a configuration refusal (missing ``--db-url``,
                         missing ``--marker-file``, a bad ``--max-age-hours``).
                         This is the house exit-78 idiom, NOT a lane verdict.

FAIL-CLOSED
-----------
An unreadable / unreachable / absent marker **NEVER reports FRESH**. The
probe never guesses a connection (the DSN is always explicit — there is no
env fallback), never writes to any database (the only SQL it issues is a
``SELECT``, inside a session pinned READ-ONLY), and never writes any file.

USAGE
-----
    # Production shape (pg provider) — the DSN is EXPLICIT, never guessed.
    .venv/bin/python scripts/audit/dump_lane_staleness.py \
        --db-url "postgresql+psycopg://ensemble@10.44.0.2:5432/ensemble_prod"

    # Immediately-usable fallback (file provider) — a marker/artifact file.
    .venv/bin/python scripts/audit/dump_lane_staleness.py \
        --provider file --marker-file /var/lib/ensemble/dump-marker

    # Override the threshold (default: 8 days = 691200 s).
    .venv/bin/python scripts/audit/dump_lane_staleness.py --db-url "$DSN" \
        --max-age-hours 192

HOUSE PRECEDENT
---------------
``scripts/audit/skill_stale_deactivate.py`` — argparse, a REQUIRED explicit
``--db-url`` (the probe never guesses a connection), DSN redaction in EVERY
error path, ``main(argv) -> int`` returning the exit code, and a printed
receipt. The receipt here is the ONE-LINE ``RESULT:`` line on stdout.
"""

from __future__ import annotations

import argparse
import os
import re
import stat as stat_mod
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# ── the contract constants ───────────────────────────────────────────────────

DEFAULT_LANE = "pgdumpall"

#: 8 days = 691200 s. The dump is DAILY and the check cadence is WEEKLY, so
#: the threshold carries one day of slack over the dump period AND is longer
#: than the 7-day check period (one missed check cannot flap the verdict).
DEFAULT_MAX_AGE_SECONDS = 691_200

DEFAULT_CONNECT_TIMEOUT_S = 5

EXIT_FRESH = 0
EXIT_NO_MARKER = 75
EXIT_STALE = 76
EXIT_CONFIG = 78

STATE_FRESH = "FRESH"
STATE_STALE = "STALE"
STATE_NO_MARKER = "NO_MARKER"
STATE_CONFIG_REFUSED = "CONFIG_REFUSED"

#: The heartbeat table the cluster-side CronJob writes into. This is an
#: OPERATOR-created table in the PostgreSQL instance — NOT a daemon-managed
#: SQLModel table, so it carries NO ``daemon/migrations/versions/*.sql`` file
#: (the daemon's migration runner is SQLite-only and owns a different ledger).
HEARTBEAT_TABLE = "public.dump_lane_heartbeat"

#: The ONLY row-producing statement in this file. A bound parameter — the
#: lane name is never interpolated into SQL. Written as a plain literal (not
#: an f-string) so the no-write AST pin can read it directly; the table name
#: is pinned against :data:`HEARTBEAT_TABLE` by the unit test, so the two
#: cannot drift apart.
MARKER_SELECT_SQL = (
    "SELECT last_success_at FROM public.dump_lane_heartbeat WHERE lane = :lane"
)

#: Session hardening: the probe pins the session READ-ONLY before it reads.
#: The statement is COMMITTED (not rolled back) so it becomes session-durable;
#: the SELECT then runs in a transaction PostgreSQL itself opened READ-ONLY,
#: so a write would be refused SERVER-SIDE, not merely by our good manners.
READ_ONLY_SESSION_SQL = "SET default_transaction_read_only = on"

#: Reason vocabulary (the ``reason=`` field of the RESULT line). Closed set —
#: the runbook maps every slug to an operator action.
REASON_WITHIN_THRESHOLD = "within_threshold"
REASON_OLDER_THAN_THRESHOLD = "older_than_threshold"
REASON_MARKER_FILE_MISSING = "marker_file_missing"
REASON_MARKER_FILE_UNREADABLE = "marker_file_unreadable"
REASON_DRIVER_UNAVAILABLE = "driver_unavailable"
REASON_DB_UNREACHABLE = "db_unreachable"
REASON_DB_QUERY_ERROR = "db_query_error"
REASON_MARKER_ROW_MISSING = "marker_row_missing"
REASON_MISSING_DB_URL = "missing_db_url"
REASON_MISSING_MARKER_FILE = "missing_marker_file"
REASON_BAD_MAX_AGE = "bad_max_age_hours"
REASON_BAD_NOW = "bad_now"
REASON_BAD_ARGUMENT = "bad_argument"


# ── the value objects ───────────────────────────────────────────────────────


@dataclass
class MarkerObservation:
    """What a provider saw. Exactly one of the two fields is meaningful."""

    last_success_at: Optional[datetime] = None
    error_reason: Optional[str] = None
    detail: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error_reason is None and self.last_success_at is not None


@dataclass
class Verdict:
    """The state-machine output. ONE verdict shape for BOTH providers."""

    state: str
    exit_code: int
    reason: str
    lane: str
    provider: str
    max_age_seconds: int
    evaluated_at: datetime
    last_success_at: Optional[datetime] = None
    age_seconds: Optional[int] = None
    detail: Optional[str] = None
    notes: list[str] = field(default_factory=list)


class ConfigRefusal(Exception):
    """An argument-level refusal. Exit 78 (EX_CONFIG) — never a health verdict."""


class _RefusingParser(argparse.ArgumentParser):
    """argparse that REFUSES (exit 78) instead of exiting 2.

    ``argparse``'s default failure path raises ``SystemExit(2)`` — a usage
    error, which is NOT the house exit-78 idiom. Every argument-level refusal
    in this script must land on 78 so an operator can tell "I mis-configured
    the probe" apart from "the dump lane is stale" (76) and "the dump lane is
    unobservable" (75).
    """

    def error(self, message: str) -> None:  # noqa: D102 - argparse override
        raise ConfigRefusal(message)


# ── helpers ─────────────────────────────────────────────────────────────────


def redact_dsn(dsn: str) -> str:
    """Mask the password in a DSN before it is echoed anywhere.

    Handles BOTH shapes: the URL form
    (``postgresql+psycopg://user:pw@host/db``) and the libpq keyword form
    (``host=... password=pw``). Non-DSN input passes through unchanged.
    """
    text = str(dsn)
    text = re.sub(
        r"(postgres(?:ql)?(?:\+[A-Za-z0-9_]+)?://[^:@/\s]+):[^@/\s]+@",
        r"\1:***@",
        text,
    )
    text = re.sub(r"(?i)\b(password\s*=\s*)\S+", r"\1***", text)
    return text


def dsn_secrets(dsn: str) -> list[str]:
    """The secret substrings carried by a DSN (the password, if any).

    Used to scrub an ERROR MESSAGE: a driver exception may echo the
    connection string, and the credential must not survive into a receipt, a
    transcript, or a stderr banner.
    """
    text = str(dsn)
    secrets: list[str] = []
    url_match = re.search(
        r"postgres(?:ql)?(?:\+[A-Za-z0-9_]+)?://[^:@/\s]*:([^@/\s]+)@", text
    )
    if url_match:
        secrets.append(url_match.group(1))
    kw_match = re.search(r"(?i)\bpassword\s*=\s*([^\s]+)", text)
    if kw_match:
        secrets.append(kw_match.group(1))
    # Never let an empty/placeholder secret become a scrub pattern.
    return [s for s in secrets if s and s != "***"]


def scrub_secrets(text: str, dsn: str) -> str:
    """Remove every password occurrence from an arbitrary text."""
    scrubbed = str(text)
    for secret in dsn_secrets(dsn):
        scrubbed = scrubbed.replace(secret, "***")
    return scrubbed


def _iso_z(moment: datetime) -> str:
    """UTC ISO-8601 with a ``Z`` suffix (the receipt format)."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sync_pg_url(dsn: str) -> str:
    """Normalize a bare ``postgresql://`` DSN to the repo's psycopg3 driver.

    SQLAlchemy defaults a bare ``postgresql://`` URL to the psycopg2 driver,
    which is NOT a dependency of this project (the daemon uses
    ``postgresql+psycopg``). House precedent:
    ``scripts/audit/skill_stale_deactivate.py``.
    """
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://") :]
    return dsn


def _parse_now(raw: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 evaluation instant. ``None`` means "wall clock"."""
    if raw is None:
        return None
    text = str(raw).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


# ── the providers ───────────────────────────────────────────────────────────


def read_file_marker(marker_file: str) -> MarkerObservation:
    """Provider "file": the mtime of the marker/artifact file.

    The mtime is the fallback shape: an operator can point the probe at a
    dump artifact (or a touch-file the dump job maintains) without any
    cluster-side change at all.
    """
    try:
        info = os.stat(marker_file)
    except FileNotFoundError:
        return MarkerObservation(error_reason=REASON_MARKER_FILE_MISSING)
    except OSError as exc:
        return MarkerObservation(
            error_reason=REASON_MARKER_FILE_UNREADABLE,
            detail=f"{type(exc).__name__}: {exc}",
        )
    if not stat_mod.S_ISREG(info.st_mode):
        return MarkerObservation(
            error_reason=REASON_MARKER_FILE_UNREADABLE,
            detail="not a regular file",
        )
    return MarkerObservation(
        last_success_at=datetime.fromtimestamp(info.st_mtime, tz=timezone.utc)
    )


def _open_pg_engine(dsn: str):
    """Open the engine. Separated so a test can inject a fake (no DB)."""
    from sqlmodel import create_engine  # noqa: PLC0415 - lazy: file provider is stdlib-only

    return create_engine(
        _sync_pg_url(dsn),
        future=True,
        connect_args={"connect_timeout": DEFAULT_CONNECT_TIMEOUT_S},
    )


def read_pg_marker(dsn: str, lane: str) -> MarkerObservation:
    """Provider "pg": a READ-ONLY ``SELECT`` of the heartbeat row."""
    try:
        engine = _open_pg_engine(dsn)
    except Exception as exc:  # noqa: BLE001 - any failure is "unobservable"
        return MarkerObservation(
            error_reason=REASON_DB_UNREACHABLE,
            detail=scrub_secrets(f"{type(exc).__name__}: {exc}", dsn),
        )
    try:
        from sqlalchemy import text  # noqa: PLC0415 - lazy, stdlib-only file provider

        with engine.connect() as conn:
            conn.execute(text(READ_ONLY_SESSION_SQL))
            # COMMIT (not rollback): a plain SET is transactional, so it only
            # becomes session-durable when the transaction commits. The next
            # statement then opens a NEW transaction, and PostgreSQL starts it
            # READ-ONLY because the session default says so.
            conn.commit()
            row = conn.execute(text(MARKER_SELECT_SQL), {"lane": lane}).first()
        if row is None:
            return MarkerObservation(error_reason=REASON_MARKER_ROW_MISSING)
        last_success_at = row[0]
        if last_success_at is None:
            return MarkerObservation(error_reason=REASON_MARKER_ROW_MISSING)
        if not isinstance(last_success_at, datetime):
            return MarkerObservation(
                error_reason=REASON_DB_QUERY_ERROR,
                detail=f"unexpected column type: {type(last_success_at).__name__}",
            )
        if last_success_at.tzinfo is None:
            last_success_at = last_success_at.replace(tzinfo=timezone.utc)
        return MarkerObservation(
            last_success_at=last_success_at.astimezone(timezone.utc)
        )
    except Exception as exc:  # noqa: BLE001 - any failure is "unobservable"
        return MarkerObservation(
            error_reason=REASON_DB_QUERY_ERROR,
            detail=scrub_secrets(f"{type(exc).__name__}: {exc}", dsn),
        )
    finally:
        try:
            engine.dispose()
        except Exception:  # noqa: BLE001, S110 - dispose is best-effort
            pass


def observe_marker(
    provider: str,
    *,
    dsn: Optional[str],
    marker_file: Optional[str],
    lane: str,
) -> MarkerObservation:
    """Dispatch to the provider. Both providers return the SAME observation."""
    if provider == "file":
        return read_file_marker(marker_file or "")
    return read_pg_marker(dsn or "", lane)


# ── THE state machine (one, shared by both providers) ───────────────────────


def evaluate(
    observation: MarkerObservation,
    *,
    now: datetime,
    max_age_seconds: int,
    lane: str,
    provider: str,
) -> Verdict:
    """Turn one observation into one verdict. Fail-closed by construction."""
    if not observation.ok:
        return Verdict(
            state=STATE_NO_MARKER,
            exit_code=EXIT_NO_MARKER,
            reason=observation.error_reason or REASON_DB_QUERY_ERROR,
            lane=lane,
            provider=provider,
            max_age_seconds=max_age_seconds,
            evaluated_at=now,
            detail=observation.detail,
        )

    last_success_at = observation.last_success_at
    assert last_success_at is not None  # noqa: S101 - narrowed by MarkerObservation.ok
    # The REPORTED age IS the COMPARED age (one integer, no rounding drift
    # between the verdict and the number the receipt carries).
    age_seconds = int((now - last_success_at).total_seconds())
    if age_seconds > max_age_seconds:
        state, exit_code, reason = (
            STATE_STALE,
            EXIT_STALE,
            REASON_OLDER_THAN_THRESHOLD,
        )
    else:
        state, exit_code, reason = (
            STATE_FRESH,
            EXIT_FRESH,
            REASON_WITHIN_THRESHOLD,
        )
    notes: list[str] = []
    if age_seconds < 0:
        notes.append(
            "marker timestamp is in the FUTURE relative to the evaluation "
            "instant — suspect clock skew on the writer side"
        )
    return Verdict(
        state=state,
        exit_code=exit_code,
        reason=reason,
        lane=lane,
        provider=provider,
        max_age_seconds=max_age_seconds,
        evaluated_at=now,
        last_success_at=last_success_at,
        age_seconds=age_seconds,
        notes=notes,
    )


# ── the receipt ─────────────────────────────────────────────────────────────


def format_result(verdict: Verdict) -> str:
    """The ONE-LINE structured RESULT — what the weekly receipt carries."""
    parts = [
        f"state={verdict.state}",
        f"lane={verdict.lane}",
        f"provider={verdict.provider}",
        f"last_success_at="
        + (_iso_z(verdict.last_success_at) if verdict.last_success_at else "none"),
        f"age_seconds="
        + ("none" if verdict.age_seconds is None else str(verdict.age_seconds)),
        f"max_age_seconds={verdict.max_age_seconds}",
        f"evaluated_at={_iso_z(verdict.evaluated_at)}",
        f"reason={verdict.reason}",
    ]
    return "RESULT: " + " ".join(parts)


def _human_age(seconds: int) -> str:
    return f"{seconds / 86400.0:.2f} days ({seconds} s)"


def format_banner(verdict: Verdict) -> str:
    """The LOUD stderr banner. One line for FRESH; a full banner otherwise."""
    if verdict.state == STATE_FRESH:
        return (
            f"[DUMP-LANE] state=FRESH lane={verdict.lane} "
            f"provider={verdict.provider} "
            f"last_success_at={_iso_z(verdict.last_success_at)} "
            f"age={_human_age(verdict.age_seconds or 0)} "
            f"threshold={_human_age(verdict.max_age_seconds)}"
        )

    rule = "=" * 78
    lines = [rule]
    if verdict.state == STATE_STALE:
        lines.append(
            "[DUMP-LANE] *** STALE *** — the nightly pg_dumpall lane has NOT "
            "reported a success inside the threshold"
        )
    elif verdict.state == STATE_NO_MARKER:
        lines.append(
            "[DUMP-LANE] *** NO-MARKER *** — the dump-lane health gate could "
            "NOT be established"
        )
    else:
        lines.append(
            "[DUMP-LANE] *** CONFIG REFUSAL *** — the probe refused to run"
        )
    lines.append(
        f"[DUMP-LANE] lane={verdict.lane} provider={verdict.provider} "
        f"reason={verdict.reason}"
    )
    if verdict.last_success_at is not None:
        lines.append(
            f"[DUMP-LANE] last_success_at={_iso_z(verdict.last_success_at)} "
            f"age={_human_age(verdict.age_seconds or 0)} "
            f"threshold={_human_age(verdict.max_age_seconds)}"
        )
    if verdict.detail:
        lines.append(f"[DUMP-LANE] detail: {verdict.detail}")
    if verdict.state == STATE_NO_MARKER:
        lines.append(
            "[DUMP-LANE] Absence of evidence is a FAILURE — this probe NEVER "
            "reports FRESH without a reachable marker source."
        )
    if verdict.state == STATE_STALE:
        lines.append(
            "[DUMP-LANE] The last successful pg_dumpall exit-0 is OLDER than "
            "the threshold. The lane is silent, not necessarily broken — "
            "verify before assuming data loss."
        )
    for note in verdict.notes:
        lines.append(f"[DUMP-LANE] note: {note}")
    if verdict.state == STATE_CONFIG_REFUSED:
        lines.append(
            "[DUMP-LANE] ACTION: fix the invocation and re-run. This is a "
            "PROBE-CONFIGURATION error, not a dump-lane verdict."
        )
        lines.append(
            "[DUMP-LANE] RECEIPT: never record this as a dump-lane failure — "
            "no lane health was established. Re-run the probe before reading "
            "the dump lane's health."
        )
    else:
        lines.append(
            "[DUMP-LANE] ACTION: inspect the cluster-side pg_dumpall CronJob "
            "and its heartbeat upsert (runbook: "
            "docs/runbooks/dump-lane-health-probe.md)."
        )
        lines.append(
            "[DUMP-LANE] RECEIPT: this result MUST land as a "
            "needs_emperor_word recommendation — a stale or unobservable dump "
            "lane is a user-decision item, not an auto-fix."
        )
    lines.append(rule)
    return "\n".join(lines)


# ── the CLI ─────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser. Every refusal lands on exit 78."""
    parser = _RefusingParser(
        prog="dump_lane_staleness.py",
        description=(
            "READ-ONLY probe: is the nightly pg_dumpall lane healthy? "
            "Exit codes: 0 FRESH / 75 NO-MARKER / 76 STALE / 78 CONFIG "
            "refusal. The probe never guesses a connection and never writes "
            "to any database."
        ),
    )
    parser.add_argument(
        "--provider",
        choices=["pg", "file"],
        default="pg",
        help=(
            "Marker source: 'pg' (read-only SELECT of the heartbeat row — "
            "the production shape) or 'file' (the mtime of --marker-file — "
            "the immediately-usable fallback). Default: pg."
        ),
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help=(
            "Database URL for the 'pg' provider. REQUIRED for that provider — "
            "the probe never guesses a connection (no env fallback, no "
            "default). Prefer a password-free DSN and let libpq read "
            "PGPASSWORD / ~/.pgpass."
        ),
    )
    parser.add_argument(
        "--marker-file",
        default=None,
        help=(
            "Marker/artifact file for the 'file' provider; its mtime is the "
            "last-success instant. REQUIRED for that provider."
        ),
    )
    parser.add_argument(
        "--lane",
        default=DEFAULT_LANE,
        help=f"Lane name in the heartbeat table. Default: {DEFAULT_LANE}.",
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=DEFAULT_MAX_AGE_SECONDS / 3600.0,
        help=(
            "Staleness threshold in hours. Default: 192 (= 691200 s = 8 days "
            "— one day of slack over the daily dump, and longer than the "
            "weekly check period so one missed check cannot flap)."
        ),
    )
    parser.add_argument(
        "--now",
        default=None,
        help=(
            "Evaluation instant (ISO-8601, UTC assumed when no offset is "
            "given). Defaults to the wall clock. An operator/test affordance: "
            "the RESULT line always records the instant actually used."
        ),
    )
    return parser


def _refuse(
    reason: str,
    detail: str,
    *,
    lane: str,
    provider: str,
    max_age_seconds: int,
    now: datetime,
) -> int:
    """Emit a CONFIG refusal (banner + RESULT line) and return exit 78."""
    verdict = Verdict(
        state=STATE_CONFIG_REFUSED,
        exit_code=EXIT_CONFIG,
        reason=reason,
        lane=lane,
        provider=provider,
        max_age_seconds=max_age_seconds,
        evaluated_at=now,
        detail=detail,
    )
    print(format_banner(verdict), file=sys.stderr)
    print(format_result(verdict))
    return EXIT_CONFIG


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except ConfigRefusal as exc:
        now = datetime.now(timezone.utc)
        return _refuse(
            REASON_BAD_ARGUMENT,
            str(exc),
            lane=DEFAULT_LANE,
            provider="none",
            max_age_seconds=DEFAULT_MAX_AGE_SECONDS,
            now=now,
        )

    lane = args.lane or DEFAULT_LANE
    provider = args.provider or "none"

    # The evaluation instant: the wall clock, unless --now overrides it.
    wall_now = datetime.now(timezone.utc)
    parsed_now = _parse_now(args.now)
    if args.now is not None and parsed_now is None:
        return _refuse(
            REASON_BAD_NOW,
            f"could not parse --now={args.now!r} as ISO-8601",
            lane=lane,
            provider=provider,
            max_age_seconds=DEFAULT_MAX_AGE_SECONDS,
            now=wall_now,
        )
    now = parsed_now or wall_now

    # The threshold.
    max_age_seconds = DEFAULT_MAX_AGE_SECONDS
    if args.max_age_hours is None or args.max_age_hours <= 0:
        return _refuse(
            REASON_BAD_MAX_AGE,
            f"--max-age-hours must be > 0 (got {args.max_age_hours!r})",
            lane=lane,
            provider=provider,
            max_age_seconds=max_age_seconds,
            now=now,
        )
    max_age_seconds = int(round(args.max_age_hours * 3600))

    # The provider's own REQUIRED argument — the probe never guesses.
    if provider == "pg" and not args.db_url:
        return _refuse(
            REASON_MISSING_DB_URL,
            "--db-url is REQUIRED for --provider pg: the probe never guesses "
            "a connection (no env fallback, no default).",
            lane=lane,
            provider=provider,
            max_age_seconds=max_age_seconds,
            now=now,
        )
    if provider == "file" and not args.marker_file:
        return _refuse(
            REASON_MISSING_MARKER_FILE,
            "--marker-file is REQUIRED for --provider file.",
            lane=lane,
            provider=provider,
            max_age_seconds=max_age_seconds,
            now=now,
        )

    observation = observe_marker(
        provider,
        dsn=args.db_url,
        marker_file=args.marker_file,
        lane=lane,
    )
    verdict = evaluate(
        observation,
        now=now,
        max_age_seconds=max_age_seconds,
        lane=lane,
        provider=provider,
    )

    print(format_banner(verdict), file=sys.stderr)
    print(format_result(verdict))
    return verdict.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
