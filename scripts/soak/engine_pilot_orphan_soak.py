#!/usr/bin/env python3
"""Soak harness for the OrphanWatcher engine pilot — STRICTLY READ-ONLY.

WHAT IT IS
----------
The 48-hour observation harness for the Emperor's flip ceremony. The clock
starts at the CEREMONY (the flip), not here: this script builds and
validates the MACHINERY, and it is run (repeatedly) over the ceremony
window to produce the verdict.

READ-ONLY CONTRACT (non-negotiable)
------------------------------------
* The connection MUST use a strictly read-only role (prod: the
  ``ensemble_prod_ro`` role — NEVER the daemon's write credentials).
* ENFORCEMENT (F1, review #5 — 2026-11-23): the engine is opened with
  ``connect_args={"options": "-c default_transaction_read_only=on"}`` —
  a **connection-startup option** the server applies BEFORE any
  transaction starts, so every transaction is read-only and a write is
  refused by PostgreSQL itself. (An earlier implementation issued
  ``SET default_transaction_read_only = on`` from a ``connect`` event
  hook; with psycopg3 that statement is defeated by the driver's
  implicit ``BEGIN`` ordering and the guard was **INERT** — the
  reviewer's probe returned "WRITE ALLOWED". The probe transcript is
  ``.agents/tester/RESULTS/2026-11-23-engine-pilot-ro-guard-probe.txt``.)
* ROLE ASSERTION (F2, review #6): after connecting, the harness reads
  ``SELECT current_user`` and **REFUSES LOUDLY** to proceed when the
  target database is prod-shaped (its name contains ``prod``) and the
  connected role is not read-only-shaped (``*_ro`` / ``ro`` / ``readonly``).
  A write-capable production role is never silently accepted.
* No DDL, no temp tables, no LOCKs, no INSERT/UPDATE/DELETE — ever.
* No flag flips, no restarts, no writes to the live install.

EVIDENCE SOURCES (the engine publishes NO events — registry→engine is
one-way, so the DB + the log are the only evidence):
  * ``engine_runs``   — append-only per-dispatch rows (outcome, timings).
  * ``engine_jobs``   — the mutable job row (last_outcome, dead, cadence).
  * ``dependency_watchers`` — the swept-over table (functional parity proxy).
  * daemon log lines  — the engine boot posture line (ownership evidence)
    and the boot-epoch / readiness lines (boot-SLO evidence).

VERDICT RULE (exact)
--------------------
The harness emits exactly ONE of:

``VIOLATION``
    At least one rule below fired. Any VIOLATION dominates: a violation
    with partial data is still a violation.

``INCONCLUSIVE``
    No rule fired, but the DATA SUFFICIENCY gate did not pass — the
    window does not contain enough evidence to judge. NEVER reported as
    PASS. The report states exactly which evidence is missing.

``PASS``
    Every rule had data, every rule passed.

MISSING DATA ⇒ INCONCLUSIVE, NEVER A SILENT PASS.

RULES
-----
R1 SUFFICIENCY  — enough ``engine_runs`` rows for the pilot job in the
                  window (≥ 1 per interval, with slack). Else INCONCLUSIVE.
R2 CADENCE      — the gap between consecutive executions must not exceed
                  ``interval * 2 + grace`` (tolerance: ONE missed tick).
                  Else VIOLATION (the lane stalled).
R3 STUCK RUN    — a run with ``started_at`` and no ``finished_at`` older
                  than ``max_runtime`` is a wedge. Else VIOLATION.
R4 OVERLAP      — two runs whose execution windows OVERLAP mean the
                  sweep ran twice concurrently (double execution).
                  Else VIOLATION.
R5 OUTCOME      — FAILED_BACKOFF / TIMED_OUT / TIMEOUT_PENDING_EXTEND on
                  the pilot job. Else VIOLATION.
R6 DEAD FLAG    — ``engine_jobs.dead = 1`` for the pilot job (the CS-9
                  observer carve-out must not fire on a live lane).
                  Else VIOLATION.
R7 OWNERSHIP    — the parity/ownership detector: executions observed while
                  the boot posture says the engine owns NOTHING
                  (``executable_specs=0``) ⇒ the engine ran a sweep the
                  ownership contract says it must not run (double
                  execution with the legacy loop). Else VIOLATION.
R8 FUNCTIONAL   — PENDING ``dependency_watchers`` older than the grace
                  window must not accumulate past the bound (the sweep
                  stopped doing its job). Else VIOLATION.
R9 BOOT-SLO     — every boot in the window must reach readyz-green within
                  the Emperor's 120 s SLO. A BOOT with NO measurement is
                  INCONCLUSIVE (never a silent pass on that boot).

Usage
-----
    scripts/soak/engine_pilot_orphan_soak.py \
        --dsn "postgresql+psycopg://ensemble_prod_ro:***@127.0.0.1:5432/ensemble_prod" \
        --window-hours 48 \
        --log-dir /var/log/ensemble \
        [--boot-telemetry boot-measurements.jsonl] \
        [--json-out soak-report.json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

PILOT_JOB_ID = "orphan-watcher"
PILOT_SERVICE_TAG = "orphan-watcher-sweep"
PILOT_FLAG = "SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE"

#: The Emperor's binding boot SLO (boot_epoch → readyz-green).
BOOT_SLO_SECONDS = 120.0

#: The sweep's grace window (default) — a PENDING watcher younger than
#: this is protected from the sweep.
DEFAULT_GRACE_SECONDS = 30

#: The swept-over table's tolerance: how many PENDING-older-than-grace
#: watchers may accumulate before the sweep is judged "not doing its job".
#: Generous by design (the table also holds watchers whose source task is
#: still ACTIVE — those are correctly preserved), but bounded.
DEFAULT_STALE_WATCHER_BOUND = 50

#: Tolerance for the cadence rule: ONE missed tick is tolerated.
CADENCE_MISSED_TICK_FACTOR = 2

#: The boot posture line (the ownership evidence). The count field is the
#: number of specs with an executor ATTACHED.
POSTURE_RE = re.compile(
    r"engine boot posture: enabled=(?P<enabled>\w+) shadow=(?P<shadow>\w+) "
    r"registrations=(?P<registrations>\d+) .*?use_engine_flags=(?P<flags>[^;]*);"
    r"(?:\s*executable_specs=(?P<executables>\d+)(?:\s*\((?P<job_ids>[^)]*)\))?)?"
)
#: F4 (review #9) — a PRE-PROMOTE daemon (a binary without this lane's
#: ``executable_specs=`` field) produces a posture line the regex still
#: matches, but with NO executable count. The R7 ownership rule then has
#: no evidence and MUST degrade to INCONCLUSIVE — never a false VIOLATION.
POSTURE_LEGACY_SHAPE = "pre-promote posture line (no executable_specs field)"
BOOT_EPOCH_RE = re.compile(
    r"Boot epoch captured \(DB clock\):\s*(?P<ts>\S+)"
)

VERDICT_PASS = "PASS"
VERDICT_VIOLATION = "VIOLATION"
VERDICT_INCONCLUSIVE = "INCONCLUSIVE"


# ──────────────────────────────────────────────────────────────────────
# Evidence model
# ──────────────────────────────────────────────────────────────────────


@dataclass
class Evidence:
    """Everything the rules read. Populated from the DB + the log files."""

    window_start: datetime
    window_end: datetime
    runs: list[dict[str, Any]] = field(default_factory=list)
    job_row: dict[str, Any] | None = None
    stale_pending_watchers: int | None = None
    postures: list[dict[str, Any]] = field(default_factory=list)
    boot_measurements: list[dict[str, Any]] = field(default_factory=list)
    role_facts: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    @property
    def executions(self) -> list[dict[str, Any]]:
        """The runs that actually EXECUTED (not skips)."""
        return [r for r in self.runs if r.get("outcome") == "SUCCEEDED"]


@dataclass
class RuleResult:
    rule: str
    verdict: str  # "PASS" | "VIOLATION" | "INCONCLUSIVE"
    detail: str
    data: dict[str, Any] = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────
# DB access — READ ONLY, enforced server-side
# ──────────────────────────────────────────────────────────────────────


def _ro_engine(dsn: str):
    """A SQLAlchemy engine whose sessions are READ-ONLY SERVER-SIDE.

    F1 (review #5) — the connect-event ``SET default_transaction_read_only``
    is **INERT** with psycopg3: the driver's implicit ``BEGIN`` is issued
    before/around the event hook's statement, so the session default never
    reaches the transaction that matters. The reviewer's probe returned
    "WRITE ALLOWED" against the old implementation.

    The fix is the **connection-startup option**: ``options=-c
    default_transaction_read_only=on`` is applied by the server as a
    session parameter **before any transaction starts**, so every
    transaction this engine opens is read-only. A write is then refused by
    PostgreSQL itself ("cannot execute INSERT in a read-only transaction").

    The role is asserted separately (F2 — review #6) by
    :func:`_assert_read_only_role`.
    """
    from sqlalchemy import create_engine

    return create_engine(
        dsn,
        pool_pre_ping=True,
        future=True,
        connect_args={"options": "-c default_transaction_read_only=on"},
    )


def _assert_read_only_role(engine, dsn: str) -> dict[str, Any]:
    """F2 (review #6) — assert the CONNECTED ROLE, and refuse a write-capable
    role when the target is prod-shaped.

    The harness accepts any DSN, which meant a write-capable role was
    silently accepted. It is now:
      * ASSERTED — the role is read from the live connection
        (``SELECT current_user``), not from the DSN string;
      * REFUSED LOUDLY for a prod-shaped target when the role is not
        read-only-shaped (``*_ro`` / ``ro`` / ``readonly``).

    A non-prod target (dev/scratch) is allowed with ANY role — the harness
    never writes there either, but the RO policy is a prod-grade control —
    and the role is recorded in the report either way.
    """
    from sqlalchemy import text

    with engine.connect() as conn:
        role = conn.execute(text("SELECT current_user")).scalar()
    role = str(role or "")

    try:
        from sqlalchemy.engine import make_url

        db_name = (make_url(dsn).database or "").lower()
    except Exception:  # noqa: BLE001 — an unparsable DSN must not be fatal here
        db_name = ""

    prod_shaped = "prod" in db_name
    ro_shaped = (
        role.endswith("_ro")
        or role.lower() in ("ro", "readonly", "read_only", "read-only")
    )

    if prod_shaped and not ro_shaped:
        raise RuntimeError(
            "REFUSING to run: the target database "
            f"{db_name!r} is PROD-SHAPED but the connected role {role!r} is "
            "not read-only-shaped (expected a *_ro / ro role). Pass the "
            "read-only role's DSN (e.g. ensemble_prod_ro) — the harness "
            "must never be pointed at a write-capable production role."
        )

    return {
        "role": role,
        "database": db_name,
        "prod_shaped": prod_shaped,
        "role_is_read_only": ro_shaped,
        "role_asserted": True,
    }


def collect_db_evidence(dsn: str, window_start: datetime, window_end: datetime) -> Evidence:
    """Read the engine's own tables for the window. WRITES NEVER."""
    from sqlalchemy import text

    ev = Evidence(window_start=window_start, window_end=window_end)
    engine = _ro_engine(dsn)
    try:
        # F2 — assert the CONNECTED ROLE before reading anything.
        ev.role_facts = _assert_read_only_role(engine, dsn)
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT job_id, started_at, finished_at, outcome, "
                    "skip_reason, duration_seconds, error_class "
                    "FROM engine_runs "
                    "WHERE job_id = :job AND started_at >= :start "
                    "AND started_at <= :end "
                    "ORDER BY started_at"
                ),
                {
                    "job": PILOT_JOB_ID,
                    "start": window_start.isoformat(),
                    "end": window_end.isoformat(),
                },
            ).fetchall()
            ev.runs = [dict(r._mapping) for r in rows]

            job = conn.execute(
                text(
                    "SELECT id, service_tag, last_run_at, last_outcome, "
                    "dead, interval_seconds, max_runtime_s, description "
                    "FROM engine_jobs WHERE id = :job"
                ),
                {"job": PILOT_JOB_ID},
            ).fetchone()
            if job is not None:
                ev.job_row = dict(job._mapping)

            stale = conn.execute(
                text(
                    "SELECT count(*) FROM dependency_watchers "
                    "WHERE state = 'PENDING' AND created_at < :cutoff"
                ),
                {
                    "cutoff": (
                        window_end - timedelta(seconds=DEFAULT_GRACE_SECONDS)
                    ).isoformat()
                },
            ).scalar()
            ev.stale_pending_watchers = int(stale or 0)
    finally:
        engine.dispose()
    return ev


# ──────────────────────────────────────────────────────────────────────
# Log evidence
# ──────────────────────────────────────────────────────────────────────


def collect_log_evidence(log_dir: Path, evidence: Evidence) -> None:
    """Parse the daemon log for the posture line and the boot epoch."""
    if not log_dir or not log_dir.exists():
        evidence.missing.append(f"log dir not found: {log_dir}")
        return
    files = sorted(p for p in log_dir.rglob("*") if p.is_file())
    if not files:
        evidence.missing.append(f"no log files under {log_dir}")
        return
    for path in files:
        try:
            with path.open("r", errors="replace") as fh:
                for line in fh:
                    if "engine boot posture:" in line:
                        m = POSTURE_RE.search(line)
                        if m:
                            executables = m.group("executables")
                            evidence.postures.append(
                                {
                                    "file": str(path),
                                    "enabled": m.group("enabled"),
                                    "shadow": m.group("shadow"),
                                    "registrations": int(m.group("registrations")),
                                    "use_engine_flags": m.group("flags").strip(),
                                    # F4 — None means a PRE-PROMOTE binary:
                                    # the executable count is UNKNOWN, and
                                    # the R7 rule must then be INCONCLUSIVE.
                                    "executable_specs": (
                                        int(executables)
                                        if executables is not None
                                        else None
                                    ),
                                    "legacy_shape": executables is None,
                                    "executor_job_ids": (
                                        m.group("job_ids") or ""
                                    ).strip(),
                                    "raw": line.strip()[:400],
                                }
                            )
                    elif "Boot epoch captured" in line:
                        m = BOOT_EPOCH_RE.search(line)
                        if m:
                            evidence.boot_measurements.append(
                                {
                                    "kind": "boot_epoch",
                                    "ts": m.group("ts"),
                                    "file": str(path),
                                    "raw": line.strip()[:300],
                                }
                            )
        except OSError:
            continue


def load_boot_telemetry(path: Path, evidence: Evidence) -> None:
    """Load D6 boot-SLO measurements (JSONL) into the evidence set."""
    if not path or not path.exists():
        evidence.missing.append(f"boot telemetry not found: {path}")
        return
    with path.open("r", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                evidence.missing.append(
                    f"unparsable boot telemetry line in {path}: {line[:120]}"
                )
                continue
            record.setdefault("kind", "boot_slo_measurement")
            evidence.boot_measurements.append(record)


# ──────────────────────────────────────────────────────────────────────
# The rules
# ──────────────────────────────────────────────────────────────────────


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        ts = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def evaluate(evidence: Evidence, *, window_hours: float) -> list[RuleResult]:
    """Apply every rule. Returns the per-rule results (in rule order)."""
    results: list[RuleResult] = []
    job = evidence.job_row or {}
    interval = float(job.get("interval_seconds") or 90.0)
    max_runtime = float(job.get("max_runtime_s") or max(interval, 60.0))

    # ── R1 SUFFICIENCY ────────────────────────────────────────────────
    # The window may contain periods with NO daemon running (the engine
    # only ticks while a daemon is up), so the expectation is measured
    # against the SPAN THE EVIDENCE COVERS, not the whole window — with a
    # hard floor: the evidence must span at least TWO intervals, and its
    # density inside that span must be at least half the expected density.
    expected_min = max(1, int((window_hours * 3600.0) / (interval * 2)))
    observed = len(evidence.runs)
    stamps = [t for t in (_parse_ts(r.get("started_at")) for r in evidence.runs) if t]
    covered_seconds = 0.0
    if len(stamps) >= 2:
        covered_seconds = (max(stamps) - min(stamps)).total_seconds()
    expected_within_cover = (
        int(covered_seconds / (interval * 2)) if covered_seconds > 0 else 0
    )
    floor_seconds = interval * 2
    if observed < 2 or covered_seconds < floor_seconds:
        evidence.missing.append(
            f"engine_runs evidence for {PILOT_JOB_ID}: {observed} row(s) "
            f"spanning {covered_seconds:.0f}s — less than the {floor_seconds:.0f}s "
            "(2 intervals) floor"
        )
        results.append(
            RuleResult(
                "R1 sufficiency",
                VERDICT_INCONCLUSIVE,
                f"{observed} engine_runs rows spanning {covered_seconds:.0f}s "
                f"(floor {floor_seconds:.0f}s / 2 intervals)",
                {
                    "observed": observed,
                    "covered_seconds": covered_seconds,
                    "floor_seconds": floor_seconds,
                    "expected_in_window": expected_min,
                },
            )
        )
    elif observed < max(2, expected_within_cover // 2):
        evidence.missing.append(
            f"engine_runs density for {PILOT_JOB_ID} inside the covered span: "
            f"{observed} rows vs ~{expected_within_cover} expected "
            f"(covered {covered_seconds:.0f}s)"
        )
        results.append(
            RuleResult(
                "R1 sufficiency",
                VERDICT_INCONCLUSIVE,
                f"{observed} engine_runs rows in {covered_seconds:.0f}s covered "
                f"(~{expected_within_cover} expected at the {interval}s cadence)",
                {
                    "observed": observed,
                    "covered_seconds": covered_seconds,
                    "expected_within_cover": expected_within_cover,
                },
            )
        )
    else:
        results.append(
            RuleResult(
                "R1 sufficiency",
                VERDICT_PASS,
                f"{observed} engine_runs rows spanning {covered_seconds:.0f}s "
                f"(~{expected_within_cover} expected at the {interval}s cadence)",
                {
                    "observed": observed,
                    "covered_seconds": covered_seconds,
                    "expected_within_cover": expected_within_cover,
                },
            )
        )

    # ── R2 CADENCE ────────────────────────────────────────────────────
    execs = evidence.executions
    if len(execs) < 2:
        results.append(
            RuleResult(
                "R2 cadence",
                VERDICT_INCONCLUSIVE,
                f"only {len(execs)} execution(s) in window — cadence needs >= 2",
                {"executions": len(execs)},
            )
        )
    else:
        gap_limit = interval * CADENCE_MISSED_TICK_FACTOR + DEFAULT_GRACE_SECONDS
        gaps = []
        worst = 0.0
        prev = None
        for run in execs:
            ts = _parse_ts(run.get("started_at"))
            if ts is None:
                continue
            if prev is not None:
                gap = (ts - prev).total_seconds()
                gaps.append(gap)
                worst = max(worst, gap)
            prev = ts
        breaches = [g for g in gaps if g > gap_limit]
        if breaches:
            results.append(
                RuleResult(
                    "R2 cadence",
                    VERDICT_VIOLATION,
                    f"{len(breaches)} gap(s) exceed the tolerance "
                    f"{gap_limit:.1f}s (worst {worst:.1f}s)",
                    {"worst_gap_seconds": worst, "limit_seconds": gap_limit},
                )
            )
        else:
            results.append(
                RuleResult(
                    "R2 cadence",
                    VERDICT_PASS,
                    f"worst gap {worst:.1f}s <= {gap_limit:.1f}s "
                    f"over {len(gaps)} intervals",
                    {"worst_gap_seconds": worst, "limit_seconds": gap_limit},
                )
            )

    # ── R3 STUCK RUN ──────────────────────────────────────────────────
    stuck = []
    for run in evidence.runs:
        started = _parse_ts(run.get("started_at"))
        finished = _parse_ts(run.get("finished_at"))
        if started and finished is None:
            if (evidence.window_end - started).total_seconds() > max_runtime:
                stuck.append(run.get("started_at"))
    if stuck:
        results.append(
            RuleResult(
                "R3 stuck run",
                VERDICT_VIOLATION,
                f"{len(stuck)} run(s) never finished (started, no finished_at)",
                {"stuck_started_at": stuck[:10]},
            )
        )
    else:
        results.append(
            RuleResult(
                "R3 stuck run",
                VERDICT_PASS,
                "every run carries a finished_at",
                {},
            )
        )

    # ── R4 OVERLAP ────────────────────────────────────────────────────
    intervals = []
    for run in evidence.runs:
        started = _parse_ts(run.get("started_at"))
        finished = _parse_ts(run.get("finished_at"))
        if started and finished:
            intervals.append((started, finished))
    intervals.sort()
    overlaps = []
    for i in range(1, len(intervals)):
        if intervals[i][0] < intervals[i - 1][1]:
            overlaps.append(
                {
                    "a": intervals[i - 1][0].isoformat(),
                    "b": intervals[i][0].isoformat(),
                }
            )
    if overlaps:
        results.append(
            RuleResult(
                "R4 overlap",
                VERDICT_VIOLATION,
                f"{len(overlaps)} overlapping run window(s) — double execution",
                {"overlaps": overlaps[:10]},
            )
        )
    else:
        results.append(
            RuleResult(
                "R4 overlap", VERDICT_PASS, "no overlapping run windows", {}
            )
        )

    # ── R5 OUTCOME ────────────────────────────────────────────────────
    bad = [
        {
            "started_at": r.get("started_at"),
            "outcome": r.get("outcome"),
            "error_class": r.get("error_class"),
        }
        for r in evidence.runs
        if r.get("outcome")
        in ("FAILED_BACKOFF", "TIMED_OUT", "TIMEOUT_PENDING_EXTEND")
    ]
    if bad:
        results.append(
            RuleResult(
                "R5 outcome",
                VERDICT_VIOLATION,
                f"{len(bad)} non-success outcome(s) on the pilot job",
                {"rows": bad[:10]},
            )
        )
    else:
        results.append(
            RuleResult(
                "R5 outcome",
                VERDICT_PASS,
                "no FAILED_BACKOFF / TIMED_OUT / TPE rows",
                {},
            )
        )

    # ── R6 DEAD FLAG ──────────────────────────────────────────────────
    if not job:
        evidence.missing.append("engine_jobs row for the pilot job not found")
        results.append(
            RuleResult(
                "R6 dead flag",
                VERDICT_INCONCLUSIVE,
                "no engine_jobs row for the pilot job",
                {},
            )
        )
    elif job.get("dead"):
        results.append(
            RuleResult(
                "R6 dead flag",
                VERDICT_VIOLATION,
                "engine_jobs.dead = 1 — the CS-9 observer carve-out fired on a "
                "live lane (false dead)",
                {"job_row": job},
            )
        )
    else:
        results.append(
            RuleResult(
                "R6 dead flag", VERDICT_PASS, "engine_jobs.dead is not set", {}
            )
        )

    # ── R7 OWNERSHIP (the parity/ownership detector) ──────────────────
    postures = evidence.postures
    if not postures:
        evidence.missing.append(
            "no engine boot posture line found in the logs — the ownership "
            "evidence is missing"
        )
        results.append(
            RuleResult(
                "R7 ownership",
                VERDICT_INCONCLUSIVE,
                "no posture line — cannot attribute executions to an owner",
                {},
            )
        )
    else:
        latest = postures[-1]
        executables = latest.get("executable_specs")
        flag_on = PILOT_FLAG in str(latest.get("use_engine_flags", ""))
        # F4 (review #9) — a PRE-PROMOTE binary has no executable_specs
        # field: the ownership evidence is UNKNOWN, so the rule degrades to
        # INCONCLUSIVE. It must NEVER produce a false VIOLATION.
        if executables is None:
            evidence.missing.append(
                POSTURE_LEGACY_SHAPE
                + " — the R7 ownership rule has no executable-count evidence"
            )
            results.append(
                RuleResult(
                    "R7 ownership",
                    VERDICT_INCONCLUSIVE,
                    POSTURE_LEGACY_SHAPE
                    + " — cannot attribute executions to an owner; run the "
                    "harness against the FLIPPED release (one carrying "
                    "executable_specs=)",
                    {"posture": latest.get("raw", "")[:300]},
                )
            )
        else:
            violations = []
            if executables == 0 and execs:
                violations.append(
                    {
                        "why": "the engine EXECUTED the sweep while the boot "
                        "posture says executable_specs=0 (the engine owns "
                        "nothing → the legacy loop also runs → double "
                        "execution)",
                        "posture": latest.get("raw", "")[:300],
                        "executions": len(execs),
                    }
                )
            if not flag_on and execs:
                violations.append(
                    {
                        "why": f"{PILOT_FLAG} is not set in the boot posture, "
                        "yet the engine executed the sweep",
                        "posture": latest.get("raw", "")[:300],
                        "executions": len(execs),
                    }
                )
            if violations:
                results.append(
                    RuleResult(
                        "R7 ownership",
                        VERDICT_VIOLATION,
                        f"{len(violations)} ownership violation(s)",
                        {"violations": violations},
                    )
                )
            else:
                results.append(
                    RuleResult(
                        "R7 ownership",
                        VERDICT_PASS,
                        f"posture executable_specs={executables}, "
                        f"{len(execs)} execution(s) — consistent",
                        {
                            "executable_specs": executables,
                            "executions": len(execs),
                        },
                    )
                )

    # ── R8 FUNCTIONAL (the sweep is doing its job) ────────────────────
    stale = evidence.stale_pending_watchers
    if stale is None:
        results.append(
            RuleResult(
                "R8 functional",
                VERDICT_INCONCLUSIVE,
                "stale watcher count unavailable",
                {},
            )
        )
    elif stale > DEFAULT_STALE_WATCHER_BOUND:
        results.append(
            RuleResult(
                "R8 functional",
                VERDICT_VIOLATION,
                f"{stale} PENDING watchers older than the grace window "
                f"(bound {DEFAULT_STALE_WATCHER_BOUND}) — the sweep is not "
                "keeping up",
                {"stale_pending_watchers": stale},
            )
        )
    else:
        results.append(
            RuleResult(
                "R8 functional",
                VERDICT_PASS,
                f"{stale} stale PENDING watchers (bound "
                f"{DEFAULT_STALE_WATCHER_BOUND})",
                {"stale_pending_watchers": stale},
            )
        )

    # ── R9 BOOT-SLO ───────────────────────────────────────────────────
    measurements = [
        m for m in evidence.boot_measurements if m.get("kind") == "boot_slo_measurement"
    ]
    epochs = [
        m for m in evidence.boot_measurements if m.get("kind") == "boot_epoch"
    ]
    if not measurements:
        evidence.missing.append(
            "no boot→readyz-green measurement in the window — supply one with "
            "--boot-telemetry (D6 script) or the log evidence is insufficient"
        )
        results.append(
            RuleResult(
                "R9 boot-SLO",
                VERDICT_INCONCLUSIVE,
                f"no boot measurement ({len(epochs)} boot epoch(s) seen only)",
                {"boot_epochs": len(epochs)},
            )
        )
    else:
        breaches = []
        bases: list[str] = []
        for m in measurements:
            # F3 (review #7) — the SLO-ANCHORED number is the daemon-reported
            # one (boot_epoch -> the readiness composite's own checked_at).
            # The harness's stopwatch number (launch -> readyz) is an UPPER
            # BOUND on it and is used only when the daemon number is absent.
            elapsed = m.get("daemon_reported_boot_to_readyz_seconds")
            basis = "daemon-reported (boot_epoch -> readyz checked_at)"
            if elapsed is None:
                elapsed = m.get("boot_to_readyz_seconds")
                basis = str(m.get("slo_basis") or "launch->readyz (UPPER BOUND)")
            if elapsed is None:
                elapsed = m.get("elapsed_seconds")
                basis = "legacy field (elapsed_seconds)"
            try:
                elapsed = float(elapsed)
            except (TypeError, ValueError):
                evidence.missing.append(
                    f"boot measurement without a usable elapsed value: {m}"
                )
                continue
            bases.append(basis)
            if elapsed > BOOT_SLO_SECONDS:
                breaches.append(
                    {"measurement": m, "elapsed_seconds": elapsed, "basis": basis}
                )
        if breaches:
            results.append(
                RuleResult(
                    "R9 boot-SLO",
                    VERDICT_VIOLATION,
                    f"{len(breaches)} boot(s) exceeded the {BOOT_SLO_SECONDS}s "
                    "boot SLO",
                    {"breaches": breaches[:10]},
                )
            )
        else:
            results.append(
                RuleResult(
                    "R9 boot-SLO",
                    VERDICT_PASS,
                    f"{len(measurements)} boot measurement(s), all within "
                    f"{BOOT_SLO_SECONDS}s (basis: "
                    f"{', '.join(sorted(set(bases))) or 'n/a'})",
                    {"measurements": len(measurements), "bases": sorted(set(bases))},
                )
            )

    return results


def build_report(
    evidence: Evidence,
    results: Iterable[RuleResult],
    *,
    window_hours: float,
    dsn: str,
) -> dict[str, Any]:
    results = list(results)
    if any(r.verdict == VERDICT_VIOLATION for r in results):
        verdict = VERDICT_VIOLATION
    elif any(r.verdict == VERDICT_INCONCLUSIVE for r in results):
        verdict = VERDICT_INCONCLUSIVE
    else:
        verdict = VERDICT_PASS
    return {
        "verdict": verdict,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": {
            "hours": window_hours,
            "start": evidence.window_start.isoformat(),
            "end": evidence.window_end.isoformat(),
        },
        "pilot": {
            "job_id": PILOT_JOB_ID,
            "service_tag": PILOT_SERVICE_TAG,
            "flag": PILOT_FLAG,
        },
        "dsn_host": _redact_dsn(dsn),
        "role_assertion": evidence.role_facts,
        "evidence_summary": {
            "engine_runs_rows": len(evidence.runs),
            "executions": len(evidence.executions),
            "job_row_present": evidence.job_row is not None,
            "stale_pending_watchers": evidence.stale_pending_watchers,
            "posture_lines": len(evidence.postures),
            "boot_measurements": len(evidence.boot_measurements),
        },
        "rules": [
            {
                "rule": r.rule,
                "verdict": r.verdict,
                "detail": r.detail,
                "data": r.data,
            }
            for r in results
        ],
        "missing_evidence": evidence.missing,
        "missing_evidence_note": (
            "MISSING DATA ⇒ INCONCLUSIVE, never a silent PASS."
        ),
    }


def _redact_dsn(dsn: str) -> str:
    return re.sub(r"://([^:@/]+):[^@]*@", r"://\1:***@", dsn)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only soak harness for the OrphanWatcher engine pilot."
        )
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get("ENGINE_SOAK_DSN", ""),
        help=(
            "READ-ONLY PostgreSQL DSN (use a read-only role, e.g. "
            "ensemble_prod_ro). Never the daemon's write credentials."
        ),
    )
    parser.add_argument("--window-hours", type=float, default=48.0)
    parser.add_argument(
        "--end",
        default="",
        help="window end (ISO). Default: now.",
    )
    parser.add_argument(
        "--log-dir",
        default="",
        help="daemon log directory (posture line + boot epoch evidence)",
    )
    parser.add_argument(
        "--boot-telemetry",
        default="",
        help="JSONL of D6 boot→readyz-green measurements",
    )
    parser.add_argument("--json-out", default="")
    parser.add_argument(
        "--warn-only",
        action="store_true",
        help="always exit 0 (the verdict is still reported)",
    )
    args = parser.parse_args(argv)

    if not args.dsn:
        print(
            "FATAL: no --dsn (and no ENGINE_SOAK_DSN) — a read-only DSN is "
            "required",
            file=sys.stderr,
        )
        return 2

    window_end = _parse_ts(args.end) if args.end else datetime.now(timezone.utc)
    window_start = window_end - timedelta(hours=args.window_hours)

    try:
        evidence = collect_db_evidence(args.dsn, window_start, window_end)
    except Exception as exc:  # noqa: BLE001 — a failed read is INCONCLUSIVE
        report = {
            "verdict": VERDICT_INCONCLUSIVE,
            "error": f"{type(exc).__name__}: {exc}",
            "missing_evidence": ["the database could not be read"],
            "missing_evidence_note": (
                "MISSING DATA ⇒ INCONCLUSIVE, never a silent PASS."
            ),
        }
        print(json.dumps(report, indent=2, default=str))
        return 0 if args.warn_only else 1

    if args.log_dir:
        collect_log_evidence(Path(args.log_dir), evidence)
    if args.boot_telemetry:
        load_boot_telemetry(Path(args.boot_telemetry), evidence)

    results = evaluate(evidence, window_hours=args.window_hours)
    report = build_report(
        evidence, results, window_hours=args.window_hours, dsn=args.dsn
    )

    print(json.dumps(report, indent=2, default=str))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2, default=str))

    if report["verdict"] == VERDICT_VIOLATION:
        return 0 if args.warn_only else 3
    if report["verdict"] == VERDICT_INCONCLUSIVE:
        return 0 if args.warn_only else 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
