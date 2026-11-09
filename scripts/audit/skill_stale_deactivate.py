#!/usr/bin/env python3
"""Operator script for E4 — the stale/shadow DB skill deactivation.

WHY THIS EXISTS (skill-system-revival Phase 1, workstream E4, plan §9 E4.2)

The lever is TWO COLUMNS THAT CAN DESYNC: ``skills.is_active`` (bool) and
``skills.status`` (varchar). The Phase-0 census proved the desync:
``(True,'active')=538 · (True,'ab_testing')=52 · (False,'inactive')=39 ·
(False,'ab_testing')=1`` — the last group is a half-dead row. Any
deactivation MUST set BOTH columns; the house writer
(:meth:`SkillRepository.deactivate`) does exactly that.

This script is the OPERATOR half of the mechanism (the plan's option (c)):
the pre-flight verification tool AND the reversal path. The shipped
boot-time reconciliation (E4.1) is the self-healing half; the injection-side
filter (E4.3) is the belt that holds even when this script has never run.

HOUSE PRECEDENT

``scripts/audit/skill_counter_reconcile.py`` — dry-run by default,
``--apply`` to commit, a printed receipt, and a hard refusal to guess a
connection (``--db-url`` is REQUIRED).

WHAT IT DOES

1. DRY-RUN (the default): enumerates the rule-matched candidates, prints
   the plan (touched / refused / observed) and writes NOTHING — no row is
   touched and no receipt is written.
2. ``--apply``: performs the writes through the SANCTIONED writer (both
   columns, one call) and emits an audit receipt that records, per touched
   row, the PRIOR ``(is_active, status)`` pair — so the operation is
   exactly reversible.
3. ``--reactivate <receipt.json>``: restores the recorded BEFORE pair (BOTH
   columns) and records ``reactivated_at``. The receipt KEEPS the
   suppression, so the next boot does not undo the reversal.

It NEVER hard-deletes a skill row, NEVER re-activates a row on its own,
and NEVER declares an A/B winner: a row whose A/B test is unresolved is
REFUSED and the refusal is enumerated in the receipt with the blocking
test id.

USAGE

    # Dry run (DEFAULT) — the pre-flight. Writes nothing.
    python scripts/audit/skill_stale_deactivate.py --db-url URL

    # Apply the plan and write the receipt.
    python scripts/audit/skill_stale_deactivate.py --db-url URL --apply

    # Reverse a previously applied run from its receipt.
    python scripts/audit/skill_stale_deactivate.py --db-url URL \
        --reactivate <receipt.json>

    # Use an explicit receipt location (default: <data_dir>/skill_stale_deactivation_receipt.json).
    python scripts/audit/skill_stale_deactivate.py --db-url URL \
        --apply --receipt /tmp/skill_stale_receipt.json

The receipt path resolution follows the house chain
``ENSEMBLE_DATA_DIR > DATA_DIR > ./data`` — a per-boot write into a tracked
tree (the repo / the planning dir) was RULED OUT.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# Allow ``python scripts/audit/skill_stale_deactivate.py`` from the repo
# root without an installed package.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlmodel import create_engine  # noqa: E402

from daemon.repositories.skill.repository import (  # noqa: E402
    SkillABTestRepository,
    SkillRepository,
)
from daemon.services.skill_deactivation import (  # noqa: E402
    SkillStaleDeactivator,
    resolve_receipt_path,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser (dry-run by default)."""
    parser = argparse.ArgumentParser(
        description=(
            "E4 stale/shadow skill deactivation — dry-run by default; "
            "--apply writes; --reactivate reverses from a receipt."
        ),
    )
    parser.add_argument(
        "--db-url",
        required=True,
        help=(
            "Database URL. REQUIRED — the script hard-refuses to guess a "
            "connection so it can never reach a database by accident."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute the plan (the default is a dry run that writes nothing).",
    )
    parser.add_argument(
        "--reactivate",
        metavar="RECEIPT",
        default=None,
        help=(
            "Reverse a previous run from its receipt: restores the recorded "
            "BEFORE (is_active, status) pair for every touched row."
        ),
    )
    parser.add_argument(
        "--receipt",
        metavar="PATH",
        default=None,
        help=(
            "Receipt path. Defaults to "
            "<data_dir>/skill_stale_deactivation_receipt.json "
            "(data_dir = ENSEMBLE_DATA_DIR > DATA_DIR > ./data)."
        ),
    )
    parser.add_argument(
        "--data-dir",
        metavar="DIR",
        default=None,
        help=(
            "Explicit data dir override for the receipt location "
            "(default: the house env chain)."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the machine-readable report instead of the human summary.",
    )
    return parser


def _human_summary(report: Any) -> str:
    lines: list[str] = []
    mode = "DRY-RUN (nothing written)" if report.dry_run else "APPLIED"
    lines.append(f"[E4] mode={mode} run_id={report.run_id}")
    lines.append(f"[E4] receipt: {report.receipt_path}")
    lines.append(
        f"[E4] touched={report.touched_count} "
        f"refused={report.refused_count} "
        f"observed={len(report.observations)} "
        f"suppressed={len(report.suppressed)}"
    )
    for entry in report.entries:
        lines.append(
            f"[E4]   TOUCHED {entry.rule_id} id={entry.skill_id} "
            f"name={entry.name!r} "
            f"before=({entry.before.is_active},{entry.before.status!r}) "
            f"after=({entry.after.is_active},{entry.after.status!r})"
        )
    for refusal in report.refusals:
        lines.append(
            f"[E4]   REFUSED {refusal.rule_id} id={refusal.skill_id} "
            f"name={refusal.name!r} reason={refusal.reason} "
            f"blocking_test={refusal.blocking_test_id} "
            f"blocking_group={refusal.blocking_test_group}"
        )
    for observation in report.observations:
        lines.append(
            f"[E4]   OBSERVED {observation.rule_id} id={observation.skill_id} "
            f"name={observation.name!r} outcome={observation.outcome}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    args = build_parser().parse_args(argv)

    receipt_path = (
        Path(args.receipt)
        if args.receipt
        else resolve_receipt_path(args.data_dir)
    )

    engine = create_engine(args.db_url, future=True)
    skill_repo = SkillRepository(engine)
    ab_test_repo = SkillABTestRepository(engine)

    deactivator = SkillStaleDeactivator(
        skill_repo=skill_repo,
        ab_test_repo=ab_test_repo,
        data_dir=args.data_dir,
        receipt_path=receipt_path,
    )

    if args.reactivate:
        results = deactivator.reactivate(
            Path(args.reactivate), apply=args.apply
        )
        if args.json:
            print(json.dumps(results, indent=2, sort_keys=True))
        else:
            for item in results:
                print(
                    f"[E4] {item.get('outcome')} id={item.get('skill_id')} "
                    f"name={item.get('name')!r}"
                )
        return 0

    report = deactivator.reconcile(dry_run=not args.apply)
    if args.json:
        print(json.dumps(report.to_json(), indent=2, sort_keys=True))
    else:
        print(_human_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
