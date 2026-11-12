#!/usr/bin/env python3
"""vsss one-shot backfill: stamp ``snapshots.agent_space_key`` on legacy rows.

version-scoped snapshot spaces (vsss, 2026-11-12) partition the
automatic warm-start search into per-version spaces keyed by the
canonical versioned agent identity
(``daemon.registry.compose_agent_space_key`` over the TARGET
instance's persisted ``instances.agent_id`` + ``instances.agent_tag``
— e.g. ``'developer[v2]'``). Rows captured before the column existed
carry ``agent_space_key IS NULL`` and — by the ratified fail-closed
ruling — match NO version's automatic warm-start until they are
stamped.

USAGE

    # Dry run (default — does NOT modify the DB; prints counts)
    python scripts/backfill_snapshot_agent_space.py --db-url <url>

    # Apply (commits the stamps)
    python scripts/backfill_snapshot_agent_space.py --db-url <url> --apply

    # Chunked apply (rows per transaction; default 200)
    python scripts/backfill_snapshot_agent_space.py --db-url <url> \
        --apply --batch 500

FAIL-CLOSED RULING (ratified)

* Only rows whose ``target_instance_id`` joins a LIVE ``instances``
  row are stamped (key composed from that row's ``agent_id`` +
  ``agent_tag`` — never meta.json ``version``).
* Rows whose target instance is DELETED (no ``instances`` row)
  stay NULL FOREVER — version-unattributable, invisible to every
  version's automatic warm-start. The explicit ``snapshot_id``
  path (version-permissive) still reaches them.

IDEMPOTENT — SAFE TO RE-RUN POST-ACTIVATION

The stamp write is CAS-gated (``SnapshotRepository.stamp_agent_space_key``
only lands when the column is still NULL), and the daemon after
restart stamps every NEW capture at insert time — so a post-activation
re-run of this script is a no-op on healthy rows (the live daemon
mints NULL rows only for unresolvable targets, which this script
also refuses to stamp). Re-run until ``still-null`` stabilizes; a
non-zero ``still-null`` that only contains deleted-instance rows is
the expected steady state.

Exit codes: 0 = run completed (dry or apply); 2 = bad arguments /
DB unreachable; 3 = daemon not activated yet
(snapshots.agent_space_key missing from the database — activate
first, then re-run); 1 = unexpected internal error.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

# ─── Path setup (so the script can be run from any cwd) ──────────────────


def _add_repo_root_to_path() -> None:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


_add_repo_root_to_path()

from sqlalchemy import inspect  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlmodel import Session, create_engine  # noqa: E402

from daemon.registry import compose_agent_space_key  # noqa: E402
from daemon.repositories.instance.models import Instance  # noqa: E402
from daemon.repositories.snapshot.models import Snapshot  # noqa: E402
from daemon.repositories.snapshot.repository import (  # noqa: E402
    SnapshotRepository,
)

DEFAULT_BATCH = 200


def resolve_space_key_for_row(
    engine: Engine,
    row: Snapshot,
) -> str | None:
    """Resolve the canonical space key for one NULL row (read-only).

    Joins the row's ``target_instance_id`` to the LIVE ``instances``
    row and composes ``registry.compose_agent_space_key(agent_id,
    agent_tag)``. Returns ``None`` when the instance row is gone
    (deleted instance — stays NULL forever) or carries no usable
    ``agent_id``. Never raises into the caller for a missing row.
    """
    with Session(engine) as session:
        instance = session.get(Instance, row.target_instance_id)
    if instance is None:
        return None
    return compose_agent_space_key(
        getattr(instance, "agent_id", None),
        getattr(instance, "agent_tag", None),
    )


def run_backfill(
    engine: Engine,
    *,
    apply: bool,
    batch: int = DEFAULT_BATCH,
) -> dict[str, Any]:
    """Drive one idempotent backfill pass.

    A pass scans ALL currently-NULL rows once (single read — no
    pagination loop: a dry run never shrinks the NULL set, so page
    re-reads would loop forever), resolves each row's key against the
    live ``instances`` table, and (apply mode) stamps via the
    CAS-gated repo write in ``batch``-sized chunks.

    Args:
        engine: The ensemble engine (PG or SQLite).
        apply: ``False`` = dry run (count only, no writes);
            ``True`` = stamp resolved rows.
        batch: Stamp-commit chunk size (apply mode only).

    Returns:
        ``{"scanned", "resolved", "stamped", "still_null", "resolved_map"}``
        where ``resolved_map`` maps snapshot_id → key for the rows
        this pass CAN stamp (dry run) or DID stamp (apply).
    """
    repo = SnapshotRepository(engine)
    rows = repo.rows_missing_agent_space()
    scanned = len(rows)
    resolved_map: dict[str, str] = {}
    still_null = 0
    pending: list[tuple[str, str]] = []
    for row in rows:
        key = resolve_space_key_for_row(engine, row)
        if key:
            resolved_map[row.id] = key
            if apply:
                pending.append((row.id, key))
        else:
            still_null += 1
    stamped = 0
    if apply:
        for start in range(0, len(pending), max(int(batch), 1)):
            for snapshot_id, key in pending[start : start + batch]:
                if repo.stamp_agent_space_key(snapshot_id, key):
                    stamped += 1
    return {
        "scanned": scanned,
        "resolved": len(resolved_map),
        "stamped": stamped,
        "still_null": still_null,
        "resolved_map": resolved_map,
    }


def _build_engine(db_url: str) -> Engine:
    return create_engine(db_url, future=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "vsss one-shot backfill: stamp snapshots.agent_space_key "
            "on legacy rows whose target instance still exists "
            "(deleted-instance rows stay NULL forever — fail-closed)."
        )
    )
    parser.add_argument(
        "--db-url",
        required=True,
        help=(
            "SQLAlchemy database URL (e.g. "
            "postgresql+psycopg://ensemble@localhost/ensemble_dev or "
            "sqlite:///<path>/ensemble.db)."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit the stamps (default: dry run, count only).",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=DEFAULT_BATCH,
        help=(
            "Rows per stamp-commit chunk in --apply mode "
            f"(default {DEFAULT_BATCH}). Scanning is a single read; "
            "this does not chunk the fetch."
        ),
    )
    args = parser.parse_args(argv)

    try:
        engine = _build_engine(args.db_url)
        with engine.connect():
            pass
    except Exception as exc:  # noqa: BLE001 — operator-facing CLI
        print(f"ERROR: cannot open database {args.db_url!r}: {exc}", file=sys.stderr)
        return 2

    # Pre-flight probe: the backfill writes snapshots.agent_space_key.
    # On a database the daemon has not activated against yet (no
    # create_all / no migration applied), the column does not exist
    # and the pass would die with an opaque "no such column" error
    # mid-scan. Refuse cleanly instead.
    try:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        has_space_column = "snapshots" in table_names and any(
            col.get("name") == "agent_space_key"
            for col in inspector.get_columns("snapshots")
        )
    except Exception as exc:  # noqa: BLE001 — operator-facing CLI
        print(f"ERROR: cannot inspect schema of {args.db_url!r}: {exc}", file=sys.stderr)
        return 2
    if not has_space_column:
        print(
            "ERROR: snapshots.agent_space_key is missing from "
            f"{args.db_url!r} — activate the daemon first (boot runs "
            "create_all plus the SQLite migration, or the PG "
            "_ensure_postgres_columns entries), then re-run this "
            "backfill.",
            file=sys.stderr,
        )
        return 3

    try:
        result = run_backfill(engine, apply=bool(args.apply), batch=args.batch)
    except Exception as exc:  # noqa: BLE001 — operator-facing CLI
        print(f"ERROR: backfill pass failed: {exc}", file=sys.stderr)
        return 1

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(
        f"[vsss-backfill:{mode}] scanned={result['scanned']} "
        f"resolved={result['resolved']} "
        f"stamped={result['stamped']} "
        f"still_null={result['still_null']}"
    )
    if result["still_null"]:
        print(
            "[vsss-backfill] still_null rows are version-unattributable "
            "(target instance deleted / no agent_id) — they stay NULL "
            "forever by the fail-closed ruling and match NO version's "
            "automatic warm-start. Expected steady state; not an error."
        )
    if not args.apply and result["resolved"]:
        print(
            "[vsss-backfill] dry run only — re-run with --apply to stamp "
            f"the {result['resolved']} resolvable row(s)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
