#!/usr/bin/env python3
"""Phase 2.5 cleanup: reconcile orphaned ``processing`` ``message_queue``
rows for already-stuck instances (Bug B historical unstick).

The Phase 2 plan (§Phase 2.5) requires a dry-run-first, one-shot
remediation utility for instances that were orphaned BEFORE the
Phase 2 fix shipped. UPDATE 4 + Phase 2.B only prevent the issue
going forward; the cleanup is the unstick path for historical
incidents.

USAGE

    # Dry run (default — does NOT modify the DB)
    python scripts/remediate_pause_report_orphans.py \\
        --instance-id <UUID> --project-dir /path/to/project

    # Apply (commits changes)
    python scripts/remediate_pause_report_orphans.py \\
        --instance-id <UUID> --project-dir /path/to/project --apply

    # Force-drop PENDING ReportInjection (data-loss acknowledgment)
    python scripts/remediate_pause_report_orphans.py \\
        --instance-id <UUID> --project-dir /path/to/project \\
        --apply --force-drop

    # Force-rearm PENDING ReportInjection (preserve, drain on next turn)
    python scripts/remediate_pause_report_orphans.py \\
        --instance-id <UUID> --project-dir /path/to/project \\
        --apply --force-rearm

    # Approve the instance status transition (WAITING_CHILDREN → COMPLETED)
    python scripts/remediate_pause_report_orphans.py \\
        --instance-id <UUID> --project-dir /path/to/project \\
        --apply --complete-instance

REPORTINJECTION CONSUMPTION CHECK (Task 19)

Before any ``message_queue`` UPDATE in the apply path, the script
queries ``report_injection`` for rows matching
``(parent_instance_id, message_id)``:

  - PENDING       → refuse by default. Re-arm with ``--force-rearm``
                     (preserve row, drain on next graph turn) or
                     drop with ``--force-drop`` (data loss).
  - INJECTED      → safely drop (consumed by live agent-node drain).
  - TASK_DELIVERED → safely drop (consumed by fallback task).
  - absent        → refuse unless ``--force-drop``.

DATA-LOSS RISK

This script is the manual unstick path for stuck production
instances. The apply path performs an operator-approved direct
status write — it does NOT emit normal completion side effects
(SSE ``status_change``, ``CompletionRegistry.complete``). The
script prints that caveat prominently.

Reference: ``.agents/shared/planning/fix-pause-report-turn-orphan/phase2-plan.md``
Phase 2.5 (§Phase 2.5 + Task 16 + Task 19 + Data-loss risk
acknowledgment section).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional

# ─── Path setup (so the script can be run from any cwd) ───────────────────


def _add_daemon_to_path() -> None:
    """Add the ``daemon/`` parent to ``sys.path`` so we can
    import ``daemon.repositories.*`` without an installed package.
    """
    repo_root = os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


_add_daemon_to_path()

# Register tables before metadata.create_all().
import daemon.repositories.dependency_bus.models  # noqa: E402
import daemon.repositories.instance.models  # noqa: E402
import daemon.repositories.message_queue.models  # noqa: E402
import daemon.repositories.report_injection.models  # noqa: E401
import daemon.repositories.task.models  # noqa: E402

from daemon.repositories.instance.models import Instance, InstanceStatus  # noqa: E402
from daemon.repositories.message_queue.models import (  # noqa: E402
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.report_injection.models import (  # noqa: E402
    ReportInjection,
    ReportInjectionState,
)
from daemon.repositories.task.models import Task, TaskStatus  # noqa: E402

from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402


# ─── Helpers ───────────────────────────────────────────────────────────────


def _find_project_db(project_dir: str) -> str:
    """Return the SQLite DB URL for the given project directory.

    Phase 5 ensemble default: the SQLite DB lives at
    ``<project_dir>/.ensemble/ensemble.db``. The user can
    override via ``--db-url`` for direct PostgreSQL or
    alternate SQLite paths.
    """
    candidate = os.path.join(project_dir, ".ensemble", "ensemble.db")
    if os.path.exists(candidate):
        return f"sqlite:///{candidate}"
    raise FileNotFoundError(
        f"No ensemble DB found at {candidate}. "
        f"Pass --db-url to override."
    )


def _create_engine(db_url: str) -> Engine:
    return create_engine(db_url, future=True)


def _load_session(engine: Engine):
    """Yield a Session and ensure the schema is built.

    The cleanup script runs against a live project DB; we trust
    the schema is already there (created by the daemon at
    startup) and do NOT call ``create_all``. That avoids
    accidentally extending a project DB the user did not
    intend to modify.
    """
    return Session(engine)


# ─── Read-only inspection ──────────────────────────────────────────────────


def _read_message_rows(engine: Engine, instance_id: str) -> list[MessageQueue]:
    with Session(engine) as s:
        return list(
            s.exec(
                select(MessageQueue).where(
                    MessageQueue.instance_id == instance_id,
                    MessageQueue.type == MessageType.COMPLETION_REPORT.value,
                    MessageQueue.status.in_([
                        MessageStatus.PROCESSING.value,
                        MessageStatus.RETRYING.value,
                    ]),
                )
            )
        )


def _read_correlated_tasks(
    engine: Engine, message_id: str
) -> list[Task]:
    with Session(engine) as s:
        return list(
            s.exec(
                select(Task).where(Task.message_id == message_id)
            )
        )


def _read_report_injection(
    engine: Engine, parent_instance_id: str, message_id: str
) -> Optional[ReportInjection]:
    with Session(engine) as s:
        return s.exec(
            select(ReportInjection).where(
                ReportInjection.parent_instance_id == parent_instance_id,
                ReportInjection.report_message_id == message_id,
            )
        ).first()


def _read_instance(engine: Engine, instance_id: str) -> Optional[Instance]:
    with Session(engine) as s:
        return s.get(Instance, instance_id)


# ─── Eligibility decision (the predicate equivalent for the script) ───────


def _evaluate_row(
    engine: Engine, row: MessageQueue
) -> dict[str, Any]:
    """Evaluate the eligibility of a single ``message_queue`` row.

    Mirrors the shared predicate at
    ``daemon/repositories/message_queue/predicates.py`` but
    in plain Python (no SQLAlchemy inside a per-row loop):

      * READY      → always counts (but READY is already filtered
        out by the caller's base status filter, so this is
        unreachable here).
      * PROCESSING / RETRYING:
        - If no correlated Task at all → counts (preserved).
        - If at least one correlated Task is PENDING/RUNNING/PAUSED
          → counts (preserved).
        - Otherwise (all terminal) → eligible for reconciliation.
    """
    correlated = _read_correlated_tasks(engine, row.message_id)
    if not correlated:
        return {
            "decision": "preserve",
            "reason": "no_correlated_task",
            "correlated_work_ids": [],
        }
    live = [
        t
        for t in correlated
        if t.status in (
            TaskStatus.PENDING.value,
            TaskStatus.RUNNING.value,
            TaskStatus.PAUSED.value,
        )
    ]
    work_ids = [str(t.work_id) for t in correlated]
    if live:
        return {
            "decision": "preserve",
            "reason": "live_work_present",
            "correlated_work_ids": work_ids,
            "live_work_ids": [str(t.work_id) for t in live],
        }
    return {
        "decision": "reconcile",
        "reason": "terminal_only",
        "correlated_work_ids": work_ids,
    }


# ─── Dry run ───────────────────────────────────────────────────────────────


def _dry_run(engine: Engine, instance_id: str) -> int:
    """Print the candidate rows and ReportInjection states. No
    writes.

    Returns:
        0 on success, 1 on no candidates, 2 on instance not found.
    """
    instance = _read_instance(engine, instance_id)
    if instance is None:
        print(
            f"ERROR: instance {instance_id[:8]}... not found in DB",
            file=sys.stderr,
        )
        return 2

    print(
        f"=== DRY RUN: instance {instance_id[:8]}... "
        f"(status={instance.status}) ==="
    )
    print(
        f"Phase 2.5 cleanup: reconcile orphaned "
        f"``completion_report`` rows whose backing work is terminal."
    )
    print()

    rows = _read_message_rows(engine, instance_id)
    if not rows:
        print("No candidate rows. Nothing to reconcile.")
        return 1

    reconcile_count = 0
    preserve_count = 0
    pending_count = 0
    for row in rows:
        evaluation = _evaluate_row(engine, row)
        ri = _read_report_injection(
            engine, instance_id, row.message_id
        )
        ri_state = ri.state if ri is not None else "absent"

        report = {
            "message_id": row.message_id,
            "type": row.type,
            "status": row.status,
            "processing_task_id": row.processing_task_id,
            "correlated_work_ids": evaluation["correlated_work_ids"],
            "decision": evaluation["decision"],
            "decision_reason": evaluation["reason"],
            "report_injection_state": ri_state,
        }
        print(json.dumps(report, indent=2, default=str))
        if evaluation["decision"] == "reconcile":
            reconcile_count += 1
        else:
            preserve_count += 1
        if ri_state == ReportInjectionState.PENDING.value:
            pending_count += 1

    print()
    print(
        f"Summary: {reconcile_count} eligible for reconciliation, "
        f"{preserve_count} preserved (live work / no Task), "
        f"{pending_count} with PENDING ReportInjection "
        f"(requires --force-rearm or --force-drop)"
    )
    if pending_count > 0:
        print(
            "WARNING: PENDING ReportInjection rows will refuse the "
            "apply by default. Re-arm with --force-rearm (preserve) "
            "or --force-drop (data loss)."
        )
    print()
    print(
        "This was a dry run. Pass --apply to commit changes. "
        "Instance status transition is gated behind --complete-instance."
    )
    return 0


# ─── Apply ─────────────────────────────────────────────────────────────────


def _apply(
    engine: Engine,
    instance_id: str,
    *,
    force_drop: bool,
    force_rearm: bool,
    complete_instance: bool,
) -> int:
    """Apply the reconciliation in one transaction. Refuses
    PENDING ReportInjection rows unless ``--force-rearm`` or
    ``--force-drop`` is supplied.
    """
    instance = _read_instance(engine, instance_id)
    if instance is None:
        print(
            f"ERROR: instance {instance_id[:8]}... not found in DB",
            file=sys.stderr,
        )
        return 2

    rows = _read_message_rows(engine, instance_id)
    if not rows:
        print("No candidate rows. Nothing to reconcile.")
        return 1

    # Phase 2.5: evaluate ReportInjection consumption per row
    # BEFORE we touch the message_queue rows. Refuse PENDING unless
    # forced.
    refused_pending: list[str] = []
    candidates_for_apply: list[MessageQueue] = []
    for row in rows:
        evaluation = _evaluate_row(engine, row)
        if evaluation["decision"] != "reconcile":
            # Skip preserved rows.
            continue
        ri = _read_report_injection(
            engine, instance_id, row.message_id
        )
        ri_state = ri.state if ri is not None else "absent"
        if ri_state == ReportInjectionState.PENDING.value:
            if force_rearm:
                # Re-arm: keep the ReportInjection as PENDING;
                # the message_queue row stays at processing (NOT
                # reconciled). This lets the next graph turn
                # drain the ReportInjection naturally.
                print(
                    f"  [rearm] {row.message_id[:8]}... "
                    f"ReportInjection remains PENDING; "
                    f"message_queue row left at processing"
                )
                continue
            elif force_drop:
                # Force-drop: explicit data-loss acknowledgment.
                print(
                    f"  [force-drop] {row.message_id[:8]}... "
                    f"PENDING ReportInjection DROPPED with --force-drop"
                )
                # Fall through to apply reconciliation
                candidates_for_apply.append(row)
            else:
                refused_pending.append(row.message_id)
                continue
        elif ri_state in (
            ReportInjectionState.INJECTED.value,
            ReportInjectionState.TASK_DELIVERED.value,
        ):
            print(
                f"  [safe-drop] {row.message_id[:8]}... "
                f"ReportInjection state={ri_state} → safe to drop"
            )
            candidates_for_apply.append(row)
        elif ri_state == "absent":
            if force_drop:
                print(
                    f"  [force-drop] {row.message_id[:8]}... "
                    f"absent ReportInjection DROPPED with --force-drop"
                )
                candidates_for_apply.append(row)
            else:
                refused_pending.append(row.message_id)
                continue
        else:
            # Unknown state — refuse by default.
            refused_pending.append(row.message_id)
            continue

    if refused_pending and not force_drop and not force_rearm:
        print(
            f"\nERROR: refusing to drop PENDING ReportInjection "
            f"content for {len(refused_pending)} row(s) without "
            f"--force-rearm or --force-drop. Aborting.",
            file=sys.stderr,
        )
        for mid in refused_pending:
            print(f"  - {mid}", file=sys.stderr)
        return 3

    print()
    print(
        f"Applying reconciliation for {len(candidates_for_apply)} row(s) "
        f"in instance {instance_id[:8]}..."
    )
    now_iso = datetime.now(timezone.utc).isoformat()
    now_dt = datetime.now(timezone.utc)
    error_suffix = (
        "manual-Phase2.5-unstick: orphaned completion_report; "
        "terminal backing work after pause/resume (operator-approved)"
    )

    # Phase 2.5 apply SQL: mirror the production reconciliation
    # but apply per-row via individual statements (one transaction).
    with Session(engine) as session:
        try:
            for row in candidates_for_apply:
                session.execute(
                    text(
                        """
                        UPDATE message_queue
                           SET status = :completed_status,
                               completed_at = :now_iso,
                               last_activity_at = :now_iso,
                               processing_task_id = NULL,
                               error_message = COALESCE(
                                   error_message, ''
                               ) || :error_suffix
                         WHERE message_id = :message_id
                           AND type = :completion_report_type
                           AND status IN (
                               :processing_status, :retrying_status
                           )
                         RETURNING message_id
                        """
                    ),
                    {
                        "completed_status": MessageStatus.COMPLETED.value,
                        "now_iso": now_iso,
                        "error_suffix": error_suffix,
                        "message_id": row.message_id,
                        "completion_report_type": (
                            MessageType.COMPLETION_REPORT.value
                        ),
                        "processing_status": (
                            MessageStatus.PROCESSING.value
                        ),
                        "retrying_status": (
                            MessageStatus.RETRYING.value
                        ),
                    },
                )
            # Optionally transition the instance.
            if complete_instance:
                # Phase 2.5: report remaining queue work, pending
                # Tasks, and pending DependencyBus watchers so the
                # operator can confirm there is no legitimate work
                # outstanding.
                remaining = session.exec(
                    select(MessageQueue).where(
                        MessageQueue.instance_id == instance_id,
                        MessageQueue.status.in_([
                            MessageStatus.READY.value,
                            MessageStatus.PROCESSING.value,
                            MessageStatus.RETRYING.value,
                        ]),
                    )
                ).all()
                if remaining:
                    print(
                        f"\nWARNING: refusing to transition instance — "
                        f"{len(remaining)} queue row(s) still in "
                        f"non-terminal base status. Apply the "
                        f"reconciliation first, then re-run with "
                        f"--complete-instance.",
                        file=sys.stderr,
                    )
                    return 4
                session.execute(
                    text(
                        """
                        UPDATE instances
                           SET status = :completed_status,
                               updated_at = :now_iso,
                               last_activity_at = :now_iso,
                               version = COALESCE(version, 1) + 1
                         WHERE instance_id = :instance_id
                           AND status = :waiting_children_status
                         RETURNING instance_id
                        """
                    ),
                    {
                        "completed_status": (
                            InstanceStatus.COMPLETED.value
                        ),
                        "now_iso": now_iso,
                        "instance_id": instance_id,
                        "waiting_children_status": (
                            InstanceStatus.WAITING_CHILDREN.value
                        ),
                    },
                )
                print(
                    f"  [complete] {instance_id[:8]}... "
                    f"transitioned WAITING_CHILDREN → COMPLETED"
                )
            session.commit()
        except Exception as e:
            session.rollback()
            print(f"Apply failed: {type(e).__name__}: {e}", file=sys.stderr)
            return 5
    print()
    print(
        "Caveat: the direct status write skipped normal completion "
        "side effects (SSE status_change, CompletionRegistry.complete). "
        "These resolve on the next UI poll / interaction."
    )
    return 0


# ─── CLI ───────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 2.5 cleanup: reconcile orphaned ``processing`` "
            "``message_queue`` rows for already-stuck instances."
        )
    )
    parser.add_argument(
        "--instance-id",
        required=True,
        help=(
            "The instance ID to inspect/remediate. REQUIRED. "
            "The script never scans the whole project."
        ),
    )
    parser.add_argument(
        "--project-dir",
        default=".",
        help=(
            "Project directory (default: current dir). The script "
            "looks for ``<project-dir>/.ensemble/ensemble.db``."
        ),
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help=(
            "Override the DB URL (e.g. ``postgresql+psycopg://...``). "
            "Default: SQLite at ``<project-dir>/.ensemble/ensemble.db``."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Apply the remediation. Default is dry-run only. "
            "PENDING ReportInjection rows are refused unless "
            "--force-rearm or --force-drop is supplied."
        ),
    )
    parser.add_argument(
        "--force-rearm",
        action="store_true",
        help=(
            "On PENDING ReportInjection: keep the row PENDING and "
            "do NOT reconcile the message_queue row. The "
            "ReportInjection drains naturally on the next graph turn."
        ),
    )
    parser.add_argument(
        "--force-drop",
        action="store_true",
        help=(
            "On PENDING or absent ReportInjection: drop the orphan "
            "content. EXPLICIT DATA-LOSS ACKNOWLEDGMENT."
        ),
    )
    parser.add_argument(
        "--complete-instance",
        action="store_true",
        help=(
            "After reconciliation, transition the instance "
            "WAITING_CHILDREN → COMPLETED. Skipped by default. "
            "Direct status write — does NOT emit normal side effects."
        ),
    )
    args = parser.parse_args()

    if args.force_rearm and args.force_drop:
        print(
            "ERROR: --force-rearm and --force-drop are mutually exclusive.",
            file=sys.stderr,
        )
        return 6

    db_url = args.db_url or _find_project_db(args.project_dir)
    engine = _create_engine(db_url)

    if not args.apply:
        return _dry_run(engine, args.instance_id)
    return _apply(
        engine,
        args.instance_id,
        force_drop=args.force_drop,
        force_rearm=args.force_rearm,
        complete_instance=args.complete_instance,
    )


if __name__ == "__main__":
    sys.exit(main())
