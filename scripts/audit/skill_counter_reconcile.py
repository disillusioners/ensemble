#!/usr/bin/env python3
"""One-time, audited, reversible ``skills.total_applied`` reconciliation.

WHY THIS EXISTS (skill-system-revival Phase 1, workstream A4, plan §5)

``record_feedback(applied=True)`` used to bump ``skills.total_applied`` on
EVERY call — including repeat filings against a usage row that was already
``applied=True``. The ledger (``skill_usage_records`` rows with
``applied = TRUE``) counts ROWS; the counter counted CALLS. Every duplicate
filing therefore added +1 to the counter and +0 to the ledger: the observed
monotonic, never-shrinking drift (``+423 -> +480`` over five days).

The code-side leak is fixed in ``daemon/services/skill_metrics_service.py``
(the bump is now transition-based: ``applied_bool is True and not
prev_applied``). This script repairs the drift ALREADY accumulated.

WHAT IT DOES

1. Recomputes, per skill, the ledger value ``count(applied = TRUE usage
   rows for that skill)``.
2. Compares it with the stored ``skills.total_applied`` counter.
3. In DRY-RUN (the default) it only REPORTS: nothing is written.
4. With ``--apply`` it writes the recomputed value for every drifting skill
   and emits an audit receipt (JSON) that records, per touched row, the
   ``skill_id``, the BEFORE value and the AFTER value — plus a run-level
   summary so a reader can verify the arithmetic without a second query.
5. With ``--revert <receipt>`` it restores the recorded BEFORE values,
   which is what makes the repair reversible.

It never creates, deletes or deactivates a skill; the only column it ever
writes is ``skills.total_applied``. It hard-refuses to guess a connection:
``--db-url`` is REQUIRED, so a bare invocation can never reach a database by
accident (and in Phase 1 nothing here is ever pointed at a live database).

USAGE

    # Measurement only — the canonical instrument (see MEASUREMENT below).
    python scripts/audit/skill_counter_reconcile.py --db-url URL --measure

    # Dry run (DEFAULT): report drift, write nothing.
    python scripts/audit/skill_counter_reconcile.py --db-url URL

    # Apply the repair and record the receipt.
    python scripts/audit/skill_counter_reconcile.py --db-url URL \
        --apply --receipt /tmp/skill_counter_receipt.json

    # Reverse a previously applied repair from its receipt.
    python scripts/audit/skill_counter_reconcile.py --db-url URL \
        --revert /tmp/skill_counter_receipt.json

MEASUREMENT (plan §5 A4 acceptance 8 — the SAME instrument that reported
+480): the canonical query is ``phase0-facts-extract.md`` §A3

    select clock_timestamp(),
           (select coalesce(sum(total_applied), 0) from skills) as counter_total,
           (select count(*) from skill_usage_records where applied is true)
               as ledger_total;

plus the per-skill mismatch query (``total_applied`` vs that skill's own
``applied = TRUE`` rows). This module runs BOTH — ``clock_timestamp()`` on
PostgreSQL, ``strftime`` on SQLite (the runner is SQLite-only for the
migrations, but this script must be runnable against a scratch SQLite too,
which is exactly how its own pack proves it).

``--measure`` ALSO carries the A4 within-identity-key divergence gauge —
DELEGATED, not re-implemented, to
``SkillRepository.within_identity_key_divergence`` (one source of truth;
see ``divergence_reading``).  That is the reading behind the A4 claim:
"reports 32 divergent ``(name, project_id)`` keys on production (630
rows), the 27 stranded A/B pairs named as the dominant cause, the 0
target BLOCKED-ON the A/B resolution decision (Ruling E)".

CONVERGENCE (plan §5 A4 acceptance 7) needs TWO measurements at least 24 h
apart, so ``--measure`` can emit a machine-readable reading and
``--compare a.json b.json`` evaluates convergence only when the two readings
are far enough apart in time; otherwise it reports
``insufficient_elapsed`` instead of claiming convergence. A single Phase-1
run CANNOT satisfy the 24 h leg — the script says so rather than pretending.

EXIT CODES

    0  success (dry run reported, repair applied, revert applied, measure OK)
    1  failure (unreadable DB, receipt mismatch, nothing to revert, ...)
    2  usage error / missing --db-url
    3  comparison inconclusive (the two readings are closer than 24 h)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

#: The minimum spacing between the two measurement points that may support a
#: convergence claim (plan §5 A4: ">= 24 h apart").
CONVERGENCE_MIN_ELAPSED = timedelta(hours=24)

#: The only column this script ever writes.
WRITABLE_COLUMN = "total_applied"


# ─────────────────────────────────────────────────────────────────────────────
# dialect-safe helpers
# ─────────────────────────────────────────────────────────────────────────────


def _is_postgres(engine: Engine) -> bool:
    return engine.dialect.name.startswith("postgres")


def _now_expression(engine: Engine) -> str:
    """The dialect's wall-clock expression (the canonical instrument's clock)."""
    if _is_postgres(engine):
        return "clock_timestamp()"
    # SQLite has no clock_timestamp(); strftime gives the same millisecond
    # ISO-8601 UTC shape the canonical reading records.
    return "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


def _read_clock(engine: Engine) -> str:
    with engine.connect() as conn:
        return str(conn.execute(text(f"select {_now_expression(engine)}")).scalar())


# ─────────────────────────────────────────────────────────────────────────────
# the readings
# ─────────────────────────────────────────────────────────────────────────────


def ledger_by_skill(engine: Engine) -> dict[str, int]:
    """``{skill_id: count(applied = TRUE usage rows)}`` — the ledger."""
    sql = text(
        "select skill_id, count(*) as applied_rows "
        "from skill_usage_records where applied is true group by skill_id"
    )
    with engine.connect() as conn:
        return {str(row[0]): int(row[1]) for row in conn.execute(sql)}


def counters_by_skill(engine: Engine) -> dict[str, int]:
    """``{skill_id: skills.total_applied}`` — the stored counter."""
    with engine.connect() as conn:
        return {
            str(row[0]): int(row[1] or 0)
            for row in conn.execute(text("select id, total_applied from skills"))
        }


def drift_rows(engine: Engine) -> list[dict[str, Any]]:
    """Every skill whose counter disagrees with its own applied-row ledger."""
    ledger = ledger_by_skill(engine)
    counters = counters_by_skill(engine)
    out: list[dict[str, Any]] = []
    for skill_id, before in sorted(counters.items()):
        after = ledger.get(skill_id, 0)
        if before != after:
            out.append(
                {
                    "skill_id": skill_id,
                    "before": int(before),
                    "after": int(after),
                    "delta": int(after) - int(before),
                }
            )
    return out


def divergence_reading(engine: Engine) -> dict[str, Any]:
    """The A4 within-identity-key divergence reading — ONE source of truth.

    Delegates to ``SkillRepository.within_identity_key_divergence``; the
    gauge is deliberately NOT re-implemented here (a second copy of the
    grouping SQL is exactly the duplication the gauge exists to end —
    tidier M2).  The import is DEFERRED into this function so every other
    path of this script (the dry run, ``--apply``, ``--revert``) never pays
    the daemon-package import cost; only ``--measure``, the read-only
    reporting path, does.  The import needs the ``ensemble`` package to be
    importable from this interpreter — true for the worktree venv's
    editable install and for any normal install of the project.

    See the repository gauge's docstring for the measure itself, the
    measured production baseline (32 divergent keys over 630 rows), the
    27-pair A/B cause, and why the 0 target is BLOCKED-ON the A/B
    resolution decision (Ruling E).
    """
    from daemon.repositories.skill.repository import SkillRepository

    return SkillRepository(engine).within_identity_key_divergence()


def measure_snapshot(engine: Engine) -> dict[str, Any]:
    """The canonical reading (the SAME instrument that reported +480).

    ``counter_total`` and ``ledger_total`` are the two global sums; the
    ``drift`` is their difference, and ``mismatched_skills`` is the per-skill
    census behind it.  ``divergence`` is the A4 within-identity-key gauge
    reading (``SkillRepository.within_identity_key_divergence``, delegated —
    never re-implemented here), which is what makes the A4 claim ("reports
    32 on production, the 27-pair cause named") operator-observable.
    """
    ledger = ledger_by_skill(engine)
    counters = counters_by_skill(engine)
    counter_total = sum(counters.values())
    ledger_total = sum(ledger.values())
    rows = drift_rows(engine)
    divergence = divergence_reading(engine)
    return {
        "taken_at": _read_clock(engine),
        "dialect": engine.dialect.name,
        "instrument": "skill_counter_reconcile.measure_snapshot (phase0-facts-extract §A3 canonical)",
        "counter_total": counter_total,
        "ledger_total": ledger_total,
        "drift": counter_total - ledger_total,
        "skills_total": len(counters),
        "mismatched_skills": len(rows),
        "mismatch_rows": rows,
        "divergence": divergence,
    }


# ─────────────────────────────────────────────────────────────────────────────
# the repair
# ─────────────────────────────────────────────────────────────────────────────


def apply_repair(
    engine: Engine,
    rows: list[dict[str, Any]],
    *,
    apply: bool,
) -> dict[str, Any]:
    """Write ``total_applied = ledger`` for every drifting skill.

    Refuses (returns ``applied=False``) unless ``apply`` is True — the
    dry-run default is enforced HERE, at the only write site, so no caller
    can reach the UPDATE by accident.

    Returns the receipt-shaped dict; under ``apply`` it is the audit record.
    """
    receipt: dict[str, Any] = {
        "instrument": "skill_counter_reconcile",
        "writable_column": WRITABLE_COLUMN,
        "dry_run": not apply,
        "written_at": _read_clock(engine),
        "dialect": engine.dialect.name,
        "touched": [],
        "touched_count": 0,
        "delta_total": 0,
    }
    if not apply:
        receipt["touched"] = list(rows)
        receipt["touched_count"] = len(rows)
        receipt["delta_total"] = sum(row["delta"] for row in rows)
        return receipt

    update = text("update skills set total_applied = :after where id = :skill_id")
    with engine.begin() as conn:
        for row in rows:
            conn.execute(
                update,
                {"after": int(row["after"]), "skill_id": row["skill_id"]},
            )
            receipt["touched"].append(
                {
                    "skill_id": row["skill_id"],
                    "before": int(row["before"]),
                    "after": int(row["after"]),
                    "delta": int(row["delta"]),
                }
            )
    receipt["touched_count"] = len(receipt["touched"])
    receipt["delta_total"] = sum(item["delta"] for item in receipt["touched"])
    return receipt


def verify_convergence(engine: Engine) -> dict[str, Any]:
    """Post-repair check: every skill's counter equals its own ledger."""
    ledger = ledger_by_skill(engine)
    counters = counters_by_skill(engine)
    residual = {
        skill_id: {"counter": counter, "ledger": ledger.get(skill_id, 0)}
        for skill_id, counter in counters.items()
        if counter != ledger.get(skill_id, 0)
    }
    return {
        "converged": not residual,
        "residual_count": len(residual),
        "residual": residual,
        "counter_total": sum(counters.values()),
        "ledger_total": sum(ledger.values()),
    }


def revert_repair(engine: Engine, receipt: dict[str, Any]) -> dict[str, Any]:
    """Restore the BEFORE values recorded in a receipt (the reversal path)."""
    touched = receipt.get("touched") or []
    update = text("update skills set total_applied = :before where id = :skill_id")
    restored = 0
    with engine.begin() as conn:
        for entry in touched:
            conn.execute(
                update,
                {"before": int(entry["before"]), "skill_id": entry["skill_id"]},
            )
            restored += 1
    return {"reverted_count": restored}


# ─────────────────────────────────────────────────────────────────────────────
# convergence evaluation (the >= 24 h leg)
# ─────────────────────────────────────────────────────────────────────────────


def _parse_reading_time(reading: dict[str, Any]) -> datetime:
    raw = str(reading.get("taken_at") or "")
    # Normalise the SQLite 'Z' suffix; PostgreSQL returns "+00:00".
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def evaluate_convergence(
    first: dict[str, Any],
    second: dict[str, Any],
) -> dict[str, Any]:
    """Decide whether two readings can support a convergence claim.

    Convergence requires BOTH readings to show ``drift == 0`` AND the two
    readings to be at least :data:`CONVERGENCE_MIN_ELAPSED` apart. Anything
    else is reported honestly — a single-run or same-instant comparison
    NEVER yields ``converged: True`` (a claim without elapsed time is not a
    proof that the drift stays at zero).
    """
    elapsed = _parse_reading_time(second) - _parse_reading_time(first)
    both_zero = int(first.get("drift", -1)) == 0 and int(second.get("drift", -1)) == 0
    if elapsed < CONVERGENCE_MIN_ELAPSED:
        return {
            "converged": False,
            "status": "insufficient_elapsed",
            "elapsed_seconds": int(elapsed.total_seconds()),
            "required_seconds": int(CONVERGENCE_MIN_ELAPSED.total_seconds()),
            "both_readings_zero_drift": both_zero,
            "reason": (
                "the two readings are closer than the 24 h the plan requires; "
                "convergence must be re-measured >= 24 h later"
            ),
        }
    return {
        "converged": both_zero,
        "status": "converged" if both_zero else "drift_persists",
        "elapsed_seconds": int(elapsed.total_seconds()),
        "required_seconds": int(CONVERGENCE_MIN_ELAPSED.total_seconds()),
        "both_readings_zero_drift": both_zero,
        "first": {"taken_at": first.get("taken_at"), "drift": first.get("drift")},
        "second": {"taken_at": second.get("taken_at"), "drift": second.get("drift")},
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skill_counter_reconcile.py",
        description=(
            "Reconcile skills.total_applied against each skill's own "
            "applied=True usage rows. Dry-run by default."
        ),
    )
    parser.add_argument(
        "--db-url",
        required=True,
        help=(
            "SQLAlchemy URL of the database to read/repair. REQUIRED — the "
            "script never guesses a connection, so a bare invocation cannot "
            "reach a database by accident."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Commit the repair (default is a dry run: report only, no writes).",
    )
    mode.add_argument(
        "--measure",
        action="store_true",
        help=(
            "Print the canonical drift reading plus the A4 within-identity-key "
            "divergence gauge (no writes)."
        ),
    )
    mode.add_argument(
        "--revert",
        metavar="RECEIPT_JSON",
        help="Restore the BEFORE values recorded in a receipt.",
    )
    parser.add_argument(
        "--receipt",
        metavar="PATH",
        help="Where to write the audit receipt under --apply (default: stdout only).",
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("FIRST_JSON", "SECOND_JSON"),
        help="Evaluate convergence between two saved --measure readings.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON on stdout.",
    )
    return parser


def _emit(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    for key in (
        "taken_at",
        "counter_total",
        "ledger_total",
        "drift",
        "skills_total",
        "mismatched_skills",
        "dry_run",
        "touched_count",
        "delta_total",
        "converged",
        "status",
        "residual_count",
        "reverted_count",
    ):
        if key in payload:
            print(f"{key}: {payload[key]}")
    for row in payload.get("mismatch_rows") or payload.get("touched") or []:
        print(
            f"  {row['skill_id']}: {row['before']} -> {row['after']} "
            f"(delta {row['delta']:+d})"
        )
        if (
            payload.get("touched_count", 0) > 20
            and (payload.get("touched") or []).index(row) == 19
        ):
            print("  ... (truncated for display; the receipt carries every row)")
            break
    divergence = payload.get("divergence")
    if isinstance(divergence, dict):
        print(
            f"divergence: {divergence.get('diverging_keys')} divergent "
            f"(name, project_id) keys over "
            f"{divergence.get('rows_in_diverging_groups')} rows of "
            f"{divergence.get('total_skills')} skills"
        )
        for key in divergence.get("keys") or []:
            print(
                f"  key {key['name']!r} @ project {key['project_id']!r}: "
                f"{key['distinct_contents']} distinct contents over "
                f"{key['rows']} rows"
            )
        if divergence.get("truncated"):
            print("  ... (key sample truncated; the JSON reading carries it)")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.compare:
        try:
            first = json.loads(Path(args.compare[0]).read_text(encoding="utf-8"))
            second = json.loads(Path(args.compare[1]).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — a bad path must be loud
            print(f"ERROR: cannot read the comparison readings: {exc}", file=sys.stderr)
            return 1
        verdict = evaluate_convergence(first, second)
        _emit(verdict, as_json=args.json)
        if verdict["status"] == "converged":
            return 0
        if verdict["status"] == "insufficient_elapsed":
            return 3
        return 1

    try:
        engine = create_engine(args.db_url, future=True)
        # A cheap liveness probe so a bad URL fails HERE, loudly, and not
        # halfway through a write loop.
        _read_clock(engine)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: cannot open {args.db_url!r}: {exc}", file=sys.stderr)
        return 1

    if args.measure:
        payload = measure_snapshot(engine)
        _emit(payload, as_json=args.json)
        return 0

    if args.revert:
        try:
            receipt = json.loads(Path(args.revert).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: cannot read receipt {args.revert!r}: {exc}", file=sys.stderr)
            return 1
        if not receipt.get("touched"):
            print("ERROR: the receipt records no touched rows — nothing to revert", file=sys.stderr)
            return 1
        outcome = revert_repair(engine, receipt)
        outcome["receipt"] = args.revert
        check = verify_convergence(engine)
        outcome.update(
            {"post_revert_drift": check["counter_total"] - check["ledger_total"]}
        )
        _emit(outcome, as_json=args.json)
        return 0

    rows = drift_rows(engine)
    receipt = apply_repair(engine, rows, apply=bool(args.apply))
    if args.apply:
        check = verify_convergence(engine)
        receipt["post_repair_converged"] = check["converged"]
        receipt["post_repair_residual_count"] = check["residual_count"]
        if args.receipt:
            Path(args.receipt).write_text(
                json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8"
            )
            receipt["receipt"] = args.receipt
        if not check["converged"]:
            _emit(receipt, as_json=args.json)
            print(
                "ERROR: the repair did not converge the counter to the ledger",
                file=sys.stderr,
            )
            return 1
    _emit(receipt, as_json=args.json)
    return 0


if __name__ == "__main__":  # pragma: no cover — CLI entry
    raise SystemExit(main())
