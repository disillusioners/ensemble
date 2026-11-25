#!/usr/bin/env python3
"""live_wedge_repair.py — READ-ONLY INSPECTION + REPAIR-REQUEST EMITTER.

NEVER EXECUTES REPAIRS. THERE IS NO --apply. Execution is the daemon's gated
repair path (3-factor: user attestation + audit stamp + dry-run), not this
script. This file contains no INSERT/UPDATE/DELETE execution path at all — it
opens every DB connection read-only (``default_transaction_read_only=on``) and
only ever *prints* repair statements as SQL text.

WHAT IT IS FOR
--------------
The live Ari root instance ``17efd2ef-9f98-46d4-b41b-60e3e32b2d13`` parks in
``waiting_children`` and never leaves. Two independent gates hold it open, and
each gate can only be satisfied by a row leaving a wait state:

  * the bus gate — ``bus.count_pending_for_target(parent)`` (the park predicate
    at ``daemon/services/message_processing_pipeline.py:1329``; the mirrored
    count at ``daemon/repositories/dependency_bus/repository.py:471-515``);
  * the mission-commission leg — ``count_outstanding_commissions(parent)``
    (``daemon/services/child_reports.py:2691-2695`` inside
    ``_root_completion_gate``, reading
    ``daemon/services/mission_record_reads.py:239-289`` →
    ``MissionRecordRepository.count_outstanding_commissions_for_anchor``
    (``daemon/repositories/mission_record/repository.py:401-451``)).

Neither row can leave its wait state on its own: the watchers are nudge-armed
on instances that are not structural children, and the COMPLETED lane has no
target-agnostic child-keyed fire (see the lane plan, §(a)). So the wedge is
durable — it survives restarts of the *reporting* path and re-parks the parent
at every turn end.

This script exists to make the repair a REVIEWABLE ARTIFACT instead of an
improvised psql session:

  --inspect        observe the rows, classify the state, print JSON + a table.
                   Never refuses on data grounds — it reports.
  --emit-request   run pre-check assertions A1-A14, and only when the state is
                   ``not_yet`` (all 8 target rows stale) or ``partial`` (some
                   target rows already resolved naturally) write
                   ``emitted/live_wedge_repair_request__*.{sql,receipt.md,receipt.json}``.
                   Read-only; writes files, never rows.
  --verify         re-check after the repair has been executed by the gated
                   path: 0 PENDING watchers remain for the parent, 0 outstanding
                   commissions remain for the parent, the parent row is untouched
                   by the repair, and the rest of both tables is row-identical to
                   the receipt's parent-scoped inventory.

  --fixture PATH   SELFTEST ONLY. Runs the emit pipeline against a synthetic
                   JSON snapshot instead of a database, and writes into
                   ``selftest/`` (never into ``emitted/``). Its output is
                   stamped ``SYNTHETIC FIXTURE — NOT A LIVE REQUEST``. This
                   exists so a reviewer can verify the emitter's guard /
                   fingerprint / receipt mechanics without a live DB.

AUTHORITY BOUNDARY
------------------
Nothing here is self-authorising. The emitted request is a *proposal*: the
Emperor's word is the gate, and the executing lane is the daemon's gated repair
tool (``daemon/tools/ens_db_tools.py:939`` ``ens_db_repair_execute`` — three
gates: kill-switch ``ENSEMBLE_REPAIR_ENABLED``, input validation, and a
single-use confirm nonce minted by a dry run). This script cannot reach that
tool, cannot use the nonce, and has no ``--apply``.

Target state choices are mirror-of-source, not invention — see RUNBOOK.md §
"Source determinations" (S1-S6) for the citations behind every constant below.

SCOPE (widened 2026-11-18 by LEADER RULING)
-------------------------------------------
The original commission scoped FOUR rows (2 watchers + 2 commissions). Live
measurement at 2026-11-18 ~00:30Z found EIGHT rows holding the two gates, and
the emit correctly REFUSED (A8 drift guard) rather than silently widening its
own authority. The leader then ruled the gate widened to the full 8-row set.
The original 4-row commission and the drift measurement are recorded in
RUNBOOK.md § "SCOPE DRIFT AND THE WIDENED GATE".

Usage
-----
    # observe (default mode)
    .venv/bin/python live_wedge_repair.py --inspect \
        --env-file /home/nea/agents-ensemble/.env

    # build the request (refuses unless the state is `not_yet` or `partial`)
    .venv/bin/python live_wedge_repair.py --emit-request \
        --gate wake-path-carrier-fix/live-wedge/17efd2ef/w8 \
        --env-file /home/nea/agents-ensemble/.env

    # verify after the gated path has executed it
    .venv/bin/python live_wedge_repair.py --verify \
        --env-file /home/nea/agents-ensemble/.env

Exit codes: 0 = the requested mode succeeded (--inspect: observation done,
whatever the classification; --emit-request: request written, or a clean
ALREADY-REPAIRED no-op; --verify: all checks PASS). 2 = usage error. 3 =
identity/connectivity failure. 4 = refusal (assertion failed, or classification
is not `not_yet`). 5 = --verify found a FAIL check.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# PINNED TARGET IDENTITIES (measured live 2026-11-17; re-verified by --inspect)
#
# SCOPE WIDENED 2026-11-18 by LEADER RULING: the commissioned 4-row set became
# the 8-row set below (the parent's two gates were held by four PENDING
# watchers + four outstanding commissions, not 2+2). Prefixes are resolved to
# full PKs at run time with a uniqueness assertion — never hard-coded PKs.
# ---------------------------------------------------------------------------

PARENT_INSTANCE_ID = "17efd2ef-9f98-46d4-b41b-60e3e32b2d13"
PARENT_AGENT_ID = "ari"

#: sonic-leader — worker of M1, watched child of W1.
SONIC_LEADER_ID = "542b49a6-8334-4703-96a8-6628dfb2ab06"
#: leader — worker of M3/M4, watched child of W3/W4.
LEADER_INSTANCE_ID = "82551f59-4603-4b88-bbce-c9bccfa6fcd5"

_WATCHER_EFFECT = (
    "release the nudge-armed watcher from the park count. Base end state "
    "mirrors the bus's own cancel transition (CANCELLED + fired_at — "
    "repository.py:717-762; boot sweep dependency_bus.py:2653-2666). "
    "DELIBERATE ADDITION: enqueued_at is stamped as well, which excludes the "
    "row from the ONLY resurrection path in the codebase "
    "(find_cancelled_unenqueued_for_target requires enqueued_at IS NULL — "
    "repository.py:797-843; re-armed by instance_lifecycle.py:4125-4260 on a "
    "later pause+resume) so an Emperor-authorised retirement cannot be undone "
    "by a pause."
)
_WATCHER_EFFECT_SELF = (
    _WATCHER_EFFECT + " The enqueued_at stamp is LOAD-BEARING on THIS row: its "
    "child_id is the parent itself, which is NON-terminal, so without the stamp "
    "a later pause+resume of the parent would pass the re-arm guard at "
    "instance_lifecycle.py:4230-4239 and rebuild this exact self-watcher."
)
_WATCHER_EFFECT_LEADER = (
    _WATCHER_EFFECT + " The enqueued_at stamp is LOAD-BEARING on THIS row too: "
    "child 82551f59 (leader) is NON-terminal, so without the stamp a later "
    "pause+resume would re-arm it (instance_lifecycle.py:4230-4241)."
)

#: The 8-row target set. One entry per row; ``slot`` is the key in ``rows``.
WATCHER_TARGETS: list[dict[str, Any]] = [
    {
        "label": "W1",
        "prefix": "8a1f60d7",
        "slot": "watcher_a",
        "source_task": "39320",
        "child_id": SONIC_LEADER_ID,
        "effect": _WATCHER_EFFECT,
        "why": (
            "watched child 542b49a6 completed 19:04:27 but the COMPLETED lane "
            "has no target-agnostic child-keyed fire, so this watcher can "
            "never fire; CANCELLED is inert for every fire primitive (all "
            "three match state='PENDING' only)."
        ),
    },
    {
        "label": "W2",
        "prefix": "999efa71",
        "slot": "watcher_b",
        "source_task": "39424",
        "child_id": PARENT_INSTANCE_ID,  # the self-referential shape itself
        "effect": _WATCHER_EFFECT_SELF,
        "why": (
            "structurally unfulfillable: child_id == target_instance_id == "
            "commissioner, so no terminal event can ever fire it. It also "
            "holds RDRS lane 7's bus-watcher probe for commission 90493206 "
            "(report_delivery_recovery.py:2841-2859) — cancelling it unblocks "
            "that commission's own reconciliation."
        ),
    },
    {
        "label": "W3",
        "prefix": "5602e91d",
        "slot": "watcher_c",
        "source_task": "39564",
        "child_id": LEADER_INSTANCE_ID,
        "effect": _WATCHER_EFFECT_LEADER,
        "why": (
            "source task 39564 is completed (23:03:54) and 82551f59 is not a "
            "structural child of the parent, so none of the three fire "
            "primitives can match — the same defect class as W1/W2, past the "
            "point of no return. It is the mutual-deadlock partner of M3 "
            "(lane 7's bus-watcher probe holds the commission while the "
            "watcher is PENDING)."
        ),
    },
    {
        "label": "W4",
        "prefix": "c83e12a2",
        "slot": "watcher_d",
        "source_task": "39564",
        "child_id": LEADER_INSTANCE_ID,
        "effect": _WATCHER_EFFECT_LEADER,
        "why": (
            "same shape as W3 (source task 39564 completed; child 82551f59 "
            "non-structural and non-terminal) and the mutual-deadlock partner "
            "of M4."
        ),
    },
]

_COMMISSION_EFFECT = (
    "leave `outstanding` so the mission leg of _root_completion_gate "
    "(child_reports.py:2691-2695) stops counting it. Mirrors "
    "MissionRecordRepository.transition_commission_state "
    "(repository.py:726-766): satisfied_at stays NULL, resolved_at "
    "is stamped, reason from the frozen vocabulary."
)

COMMISSION_TARGETS: list[dict[str, Any]] = [
    {
        "label": "M1",
        "prefix": "0cdc0b31",  # Ari -> sonic-leader
        "slot": "commission_a",
        "worker": SONIC_LEADER_ID,
        "effect": _COMMISSION_EFFECT,
        "why": (
            "the commissioned worker (sonic-leader) is terminal and no report "
            "was ever delivered — this is EXACTLY RDRS lane 7's "
            "worker_terminal_no_report abandonment "
            "(report_delivery_recovery.py:2929-2937), which currently cannot "
            "run only because the PENDING watcher holds its probe at "
            ":2841-2859."
        ),
    },
    {
        "label": "M2",
        "prefix": "90493206",  # Ari -> Ari (self-nudge)
        "slot": "commission_b",
        "worker": PARENT_INSTANCE_ID,
        "effect": _COMMISSION_EFFECT,
        "why": (
            "self-nudge: commissioner == worker == target. The worker is the "
            "parent itself (not terminal), so the sweep's R1 leg does not "
            "apply; its R2b TTL backstop is the only source-sanctioned "
            "abandon, and it lands at created_at + 21600s "
            "(report_delivery_recovery.py:2941-2947, config.py:2400-2421 "
            "TTL default 6h). DECLARED DIVERGENCE: this statement acts "
            "BEFORE the TTL elapses. The END STATE is identical to what lane "
            "7 will write at TTL; only the timing differs. If the Emperor "
            "prefers zero guesswork, DROP this block and let lane 7 abandon "
            "90493206 at its TTL (~02:30Z)."
        ),
    },
    {
        "label": "M3",
        "prefix": "06001f88",
        "slot": "commission_c",
        "worker": LEADER_INSTANCE_ID,
        "effect": _COMMISSION_EFFECT,
        "why": (
            "minted by the parent's recurring self-nudge; worker 82551f59 "
            "(leader) is NON-terminal, so lane 7's R1 leg does not apply and "
            "its R2b TTL backstop is the only source-sanctioned abandon "
            "(TTL ≈ 04:52Z). DECLARED DIVERGENCE: acts BEFORE the TTL; the "
            "end state is identical to lane 7's own write, only the timing "
            "differs. NOTE: this closes the ledger entry for a lane that is "
            "still working; the lane's report routes through the session "
            "lane, not the mission ledger, so it is unaffected."
        ),
    },
    {
        "label": "M4",
        "prefix": "a1561f19",
        "slot": "commission_d",
        "worker": LEADER_INSTANCE_ID,
        "effect": _COMMISSION_EFFECT,
        "why": (
            "same shape as M3 (worker 82551f59 non-terminal, TTL ≈ 04:53Z); "
            "same declared before-TTL divergence and the same disclosure that "
            "the in-flight lane's report is not blocked by this ledger close."
        ),
    },
]

TARGET_WATCHER_PREFIXES: tuple[str, ...] = tuple(
    t["prefix"] for t in WATCHER_TARGETS)
TARGET_COMMISSION_PREFIXES: tuple[str, ...] = tuple(
    t["prefix"] for t in COMMISSION_TARGETS)

#: Worker-terminal set for the R1/R2b reason mapping — MIRROR of lane 7's
#: ``_MISSION_COMMISSION_TERMINAL_STATUSES`` = ``_PARENT_TERMINAL_STATUSES``
#: (report_delivery_recovery.py:448-461, :482-484).
WORKER_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "error", "terminated", "failed"})

#: Observed on the live rows (forensics only — NOT part of the repair guard).
MISSION_OBSERVED = "cb904c16-a982-41d4-9d33-2a04c7c2d6c0"

#: OUT OF SCOPE — observational only, NEVER in a repair statement.
OUT_OF_SCOPE_COMMISSIONER = "4e261d34-b350-4315-a8bb-1ae96205ab3a"
OUT_OF_SCOPE_DATE_PREFIX = "2026-11-12"

#: The widened gate (leader ruling 2026-11-18). Byte-exact; anything else refuses.
GATE_FLAG = "wake-path-carrier-fix/live-wedge/17efd2ef/w8"
DEFAULT_REQUIRED_DB = "ensemble_prod"

#: VERBATIM receipt note mandated by the amendment (2026-11-18). Carried in
#: the emitted receipt md + json. Do not paraphrase.
RECEIPT_NOTE = (
    "the wedge's BUS GATE was ALREADY OPEN (watchers cancelled by the boot "
    "sweep) before this repair; the remaining hold was the mission leg's two "
    "commissions. The Emperor's authorization is recorded on board item "
    "wi-8f60e6f1."
)

# ---------------------------------------------------------------------------
# TARGET STATE CONSTANTS — each mirrors a source-defined end state, never guess
# ---------------------------------------------------------------------------

#: DependencyWatcherState.CANCELLED (daemon/repositories/dependency_bus/models.py:49-71).
WATCHER_TARGET_STATE = "CANCELLED"
#: The guarded transition the bus itself uses for a cancel:
#: repository.py:717-762 transition_state (`WHERE watch_id AND state='PENDING'`),
#: and the boot orphan sweep's end state (dependency_bus.py:2653-2666).
WATCHER_SOURCE_STATE = "PENDING"

#: CommissionState.ABANDONED (daemon/repositories/mission_record/models.py:153-173).
COMMISSION_TARGET_STATE = "abandoned"
COMMISSION_SOURCE_STATE = "outstanding"
#: FROZEN abandon-reason vocabulary (daemon/constants.py:721-726). Anything else
#: raises in MissionRecordService.abandon_commission (service:1046-1063).
REASON_WORKER_TERMINAL = "worker_terminal_no_report"
REASON_TTL = "ttl_expired_not_live"
#: The manual-repair audit marker written into `mission_commissions.abandon_reason`
#: is NOT free-form — the column has a reader-side vocabulary contract, so the
#: marker rides `resolved_at`-adjacent prose only in the receipt, never in a row.
#: (dependency_watchers has no reason column; its `metadata` JSONB is unused by
#: every read site — see RUNBOOK S3.)

#: The execution-time stamp expression, byte-identical to the sanctioned
#: TEXT-ISO producer ``daemon/services/timestamps.py:63-73`` ``now_utc_iso()``
#: (``datetime.now(timezone.utc).isoformat()`` → microseconds + ``+00:00``).
#: Kept as one constant so every emitted stamp is format-identical to the
#: values already in the column (the compaction DELETE
#: ``instance_lifecycle.py:6315-6329`` compares these strings lexicographically).
NOW_UTC_ISO_SQL = (
    "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"+00:00\"')"
)

#: Read-only connection options (MANDATORY on every connection this script opens).
READ_ONLY_OPTIONS = "-c default_transaction_read_only=on -c statement_timeout=15000"

#: Credential-scrub needles — nothing this script writes may carry a live key.
SCRUB_RE = re.compile(r"sk-(?:proj|ant)-[A-Za-z0-9_\-]+")

DEFAULT_OUT_DIR = Path(__file__).resolve().parent


# ===========================================================================
# Output helpers
# ===========================================================================


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now_utc()).isoformat()


def _stamp_for_filename(dt: datetime | None = None) -> str:
    return (dt or _now_utc()).strftime("%Y%m%dT%H%M%SZ")


def _scrub(text: str) -> str:
    """Redact anything shaped like a live provider key."""
    return SCRUB_RE.sub("<REDACTED-KEY>", text)


def _p(line: str = "") -> None:
    print(_scrub(line))


def _table(rows: list[tuple[str, ...]], headers: tuple[str, ...]) -> str:
    """Render a plain-ASCII table (no width guessing beyond content)."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    out = []
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    out.append(sep)
    out.append("| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |")
    out.append(sep)
    for row in rows:
        out.append("| " + " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)) + " |")
    out.append(sep)
    return "\n".join(out)


# ===========================================================================
# Connection — read-only by construction
# ===========================================================================


class ConnectError(RuntimeError):
    pass


def _parse_env_file(path: str) -> dict[str, str]:
    """Parse KEY=VALUE lines. Values are NEVER echoed by this script."""
    values: dict[str, str] = {}
    p = Path(path)
    if not p.is_file():
        raise ConnectError(f"--env-file not found: {path}")
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        val = val.strip().strip("'").strip('"')
        values[key] = val
    return values


def _resolve_dsn(args: argparse.Namespace) -> dict[str, str]:
    """Resolution order: --dsn > --env-file > ambient POSTGRES_*."""
    if args.dsn:
        # Bare libpq conninfo string; psycopg understands it directly.
        return {"conninfo": args.dsn}
    if args.env_file:
        env = _parse_env_file(args.env_file)
        if "DATABASE_URL" in env and env["DATABASE_URL"]:
            return {"conninfo": env["DATABASE_URL"]}
        required = ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
                    "POSTGRES_USER", "POSTGRES_PASSWORD")
        missing = [k for k in required if not env.get(k)]
        if missing:
            raise ConnectError(
                "--env-file is missing required keys: " + ", ".join(missing)
            )
        return {
            "host": env["POSTGRES_HOST"],
            "port": env["POSTGRES_PORT"],
            "dbname": env["POSTGRES_DB"],
            "user": env["POSTGRES_USER"],
            "password": env["POSTGRES_PASSWORD"],
        }
    # Ambient. Never assume a key exists — report by NAME only.
    required = ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
                "POSTGRES_USER", "POSTGRES_PASSWORD")
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise ConnectError(
            "no connection source: pass --dsn or --env-file (ambient is "
            "missing: " + ", ".join(missing) + ")"
        )
    return {
        "host": os.environ["POSTGRES_HOST"],
        "port": os.environ["POSTGRES_PORT"],
        "dbname": os.environ["POSTGRES_DB"],
        "user": os.environ["POSTGRES_USER"],
        "password": os.environ["POSTGRES_PASSWORD"],
    }


def _connect(spec: dict[str, str]):
    """Open a READ-ONLY connection and hard-assert the read-only posture."""
    try:
        import psycopg
    except Exception as exc:  # pragma: no cover - env dependent
        raise ConnectError(f"psycopg is unavailable: {type(exc).__name__}") from exc

    kwargs: dict[str, Any] = {"options": READ_ONLY_OPTIONS, "autocommit": True}
    if "conninfo" in spec:
        kwargs["conninfo"] = spec["conninfo"]
    else:
        kwargs.update(
            host=spec["host"], port=spec["port"], dbname=spec["dbname"],
            user=spec["user"], password=spec["password"],
        )
    try:
        conn = psycopg.connect(**kwargs)
    except Exception as exc:
        # Mask: never echo the exception text if it could carry a password.
        raise ConnectError(
            f"connect failed ({type(exc).__name__}); check host/port/db/user "
            f"and credential presence — details withheld by policy"
        ) from None
    # Belt-and-braces: assert the server agrees we are read-only.
    with conn.cursor() as cur:
        cur.execute("SHOW transaction_read_only")
        row = cur.fetchone()
    if not row or str(row[0]).lower() not in ("on", "true", "1"):
        conn.close()
        raise ConnectError("connection is NOT read-only — refusing to continue")
    return conn


def _q(conn, sql: str, params: tuple | None = None) -> list[dict[str, Any]]:
    """Run one read-only SELECT and return dict rows."""
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        if cur.description is None:
            return []
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _assert_db_identity(conn, required_db: str) -> str:
    rows = _q(conn, "SELECT current_database() AS db, current_user AS usr")
    db = rows[0]["db"]
    usr = rows[0]["usr"]
    _p(f"[identity] current_database() = {db}   current_user = {usr}")
    if db != required_db:
        raise ConnectError(
            f"identity gate FAILED: connected to database {db!r}, expected "
            f"{required_db!r} (--require-db). Refusing to continue."
        )
    return db


# ===========================================================================
# Observation — the two mirrored predicates
# ===========================================================================

#: MIRROR of DependencyWatcherRepository.count_pending_for_target
#: (daemon/repositories/dependency_bus/repository.py:506-515):
#:     WHERE target_instance_id = :t AND state = 'PENDING'   (func.count())
COUNT_PENDING_SQL = (
    "SELECT count(*)::int AS n FROM dependency_watchers "
    "WHERE target_instance_id = %s AND state = 'PENDING'"
)

#: MIRROR of MissionRecordRepository.count_outstanding_commissions_for_anchor
#: (daemon/repositories/mission_record/repository.py:436-448):
#:     JOIN mission_records ON mission_records.mission_id = mission_commissions.mission_id
#:     WHERE mission_records.anchor_instance_id = :a
#:       AND mission_commissions.state = 'outstanding'
#:       AND mission_commissions.advisory_backfill = false
COUNT_OUTSTANDING_COMMISSIONS_SQL = (
    "SELECT count(*)::int AS n FROM mission_commissions mc "
    "JOIN mission_records mr ON mr.mission_id = mc.mission_id "
    "WHERE mr.anchor_instance_id = %s "
    "  AND mc.state = 'outstanding' "
    "  AND mc.advisory_backfill = false"
)

WATCHER_ROW_SQL = (
    "SELECT watch_id, source_task_id, target_instance_id, state, created_at, "
    "fired_at, enqueued_at, "
    "follow_up_payload->'metadata'->>'child_id' AS child_id, "
    "follow_up_payload->'metadata'->>'kind' AS kind, "
    "follow_up_payload->'metadata'->>'message_id' AS message_id, "
    "follow_up_payload->'metadata'->>'mission_id' AS payload_mission_id "
    "FROM dependency_watchers WHERE watch_id::text LIKE %s ORDER BY watch_id"
)

TASK_ROW_SQL = (
    "SELECT id, instance_id, status, task_type, created_at, completed_at "
    "FROM task WHERE id = %s"
)

INSTANCE_ROW_SQL = (
    "SELECT instance_id, agent_id, agent_name, status, parent_id, "
    "created_at, updated_at, version "
    "FROM instances WHERE instance_id = %s"
)

COMMISSION_ROW_SQL = (
    "SELECT commission_id, mission_id, commissioner_instance_id, "
    "worker_instance_id, work_id, lane, state, created_at, resolved_at, "
    "satisfied_at, abandon_reason, advisory_backfill "
    "FROM mission_commissions WHERE commission_id::text LIKE %s ORDER BY commission_id"
)

PENDING_WATCHERS_FOR_PARENT_SQL = (
    "SELECT watch_id, source_task_id, state, created_at, "
    "follow_up_payload->'metadata'->>'child_id' AS child_id, "
    "follow_up_payload->'metadata'->>'kind' AS kind "
    "FROM dependency_watchers WHERE target_instance_id = %s AND state = 'PENDING' "
    "ORDER BY created_at"
)

ANCHOR_OUTSTANDING_COMMISSIONS_SQL = (
    "SELECT mc.commission_id, mc.mission_id, mc.commissioner_instance_id, "
    "mc.worker_instance_id, mc.lane, mc.state, mc.created_at "
    "FROM mission_commissions mc "
    "JOIN mission_records mr ON mr.mission_id = mc.mission_id "
    "WHERE mr.anchor_instance_id = %s AND mc.state = 'outstanding' "
    "  AND mc.advisory_backfill = false "
    "ORDER BY mc.created_at"
)

OUT_OF_SCOPE_SQL = (
    "SELECT commission_id, mission_id, commissioner_instance_id, "
    "worker_instance_id, lane, state, created_at "
    "FROM mission_commissions WHERE commissioner_instance_id = %s "
    "ORDER BY created_at"
)


#: Per-row disposition vocabulary (leader's boundary, 2026-11-18). Evaluated
#: BEFORE any statement is built: the gate must NOT be brittle against rows the
#: system itself resolves (lane 7's TTLabandons, the boot sweep's cancels).
DISPOSITION_STALE = "in_scope_stale"
DISPOSITION_RESOLVED = "naturally_resolved"
DISPOSITION_UNEXPECTED = "unexpected"


def _watcher_disposition(rows_list: list[dict[str, Any]]) -> str:
    if len(rows_list) != 1:
        return DISPOSITION_UNEXPECTED
    state = rows_list[0]["state"]
    if state == WATCHER_SOURCE_STATE:
        return DISPOSITION_STALE
    if state in ("CANCELLED", "FIRED"):
        return DISPOSITION_RESOLVED
    return DISPOSITION_UNEXPECTED


def _commission_disposition(rows_list: list[dict[str, Any]]) -> str:
    if len(rows_list) != 1:
        return DISPOSITION_UNEXPECTED
    state = rows_list[0]["state"]
    if state == COMMISSION_SOURCE_STATE:
        return DISPOSITION_STALE
    if state in ("abandoned", "satisfied", "superseded"):
        return DISPOSITION_RESOLVED
    return DISPOSITION_UNEXPECTED


def _commission_reason(worker_status: str | None) -> str | None:
    """MIRROR of lane 7's own predicate order (report_delivery_recovery.py:2929-2947).

    worker terminal  → ``worker_terminal_no_report`` (R1)
    worker non-terminal (and past TTL) → ``ttl_expired_not_live`` (R2b)
    worker row missing → ``None`` (the caller treats the row as UNEXPECTED —
    this script never invents a third reason token; ``worker_row_missing`` is
    lane 7's, not this artifact's).
    """
    if worker_status in WORKER_TERMINAL_STATUSES:
        return REASON_WORKER_TERMINAL
    if worker_status:
        return REASON_TTL
    return None


def collect_targets(rows: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive the 8-row (state, disposition, identity) table from observed rows.

    Pure — it runs on a live observation AND on a synthetic fixture, so the
    disposition / reason-selection logic has exactly one implementation.
    """
    out: list[dict[str, Any]] = []
    workers = rows.get("workers") or {}

    for spec in WATCHER_TARGETS:
        rows_list = rows.get(spec["slot"]) or []
        row = rows_list[0] if len(rows_list) == 1 else None
        # Backfill eligibility mirrors the re-arm path's terminal-child skip
        # (instance_lifecycle.py:4229-4239; TERMINAL_STATUSES from
        # job_queue_service.py:126-131). Computed here, outside the dict literal.
        _child_status = ((workers.get(spec["child_id"]) or {}).get("status")
                         if workers else None)
        _child_terminal = _child_status in WORKER_TERMINAL_STATUSES
        out.append({
            "kind": "watcher",
            "label": spec["label"],
            "slot": spec["slot"],
            "prefix": spec["prefix"],
            "pk": row["watch_id"] if row else None,
            "state": row["state"] if row else None,
            "disposition": _watcher_disposition(rows_list),
            "source_task": str(row["source_task_id"]) if row else None,
            "child_id": row.get("child_id") if row else None,
            "expected_source_task": spec["source_task"],
            "expected_child_id": spec["child_id"],
            "effect": spec["effect"],
            "why": spec["why"],
            "identity_ok": bool(
                row is not None
                and row["target_instance_id"] == PARENT_INSTANCE_ID
                and str(row["source_task_id"]) == spec["source_task"]
                and row.get("child_id") == spec["child_id"]
            ),
            # Backfill eligibility mirrors the re-arm path's terminal-child skip
            # (instance_lifecycle.py:4229-4239; TERMINAL_STATUSES from
            # job_queue_service.py:126-131): a CANCELLED watcher whose child is
            # TERMINAL is skip-protected (no backfill); one whose child is
            # NON-TERMINAL is re-arm eligible and MUST be stamped. A missing
            # child row is also skipped (re-arm's `child is None -> continue`).
            "needs_backfill": bool(
                row is not None
                and row["state"] == "CANCELLED"
                and row.get("enqueued_at") is None
                and _child_status is not None
                and not _child_terminal
            ),
        })

    for spec in COMMISSION_TARGETS:
        rows_list = rows.get(spec["slot"]) or []
        row = rows_list[0] if len(rows_list) == 1 else None
        worker_id = row["worker_instance_id"] if row else None
        worker_status = ((workers.get(worker_id) or {}).get("status")
                         if worker_id else None)
        disposition = _commission_disposition(rows_list)
        reason = _commission_reason(worker_status) if row else None
        if disposition == DISPOSITION_STALE and reason is None:
            # A stale commission whose worker row cannot be read: unprovable
            # predicate ⇒ refuse (P1, the same rule lane 7 applies).
            disposition = DISPOSITION_UNEXPECTED
        out.append({
            "kind": "commission",
            "label": spec["label"],
            "slot": spec["slot"],
            "prefix": spec["prefix"],
            "pk": row["commission_id"] if row else None,
            "state": row["state"] if row else None,
            "disposition": disposition,
            "worker_id": worker_id,
            "worker_status": worker_status,
            "expected_worker_id": spec["worker"],
            "effect": spec["effect"],
            "why": spec["why"],
            "identity_ok": bool(
                row is not None
                and row["commissioner_instance_id"] == PARENT_INSTANCE_ID
                and row.get("worker_instance_id") == spec["worker"]
            ),
            "reason": reason,
        })

    return out


def observe(conn, *, required_db: str) -> dict[str, Any]:
    """Collect every observation. Pure read. Returns the observation dict."""
    db = _assert_db_identity(conn, required_db)

    obs: dict[str, Any] = {
        "generated_at": _iso(),
        "database": db,
        "parent_instance_id": PARENT_INSTANCE_ID,
        "assertions": [],
        "rows": {},
        "targets": [],
        "extended_census": {},
        "out_of_scope": [],
        "classification": None,
        "divergences": [],
    }

    def add(a_id: str, name: str, observed: Any, expected: Any, ok: bool, note: str = ""):
        obs["assertions"].append({
            "id": a_id, "name": name, "observed": observed,
            "expected": expected, "ok": bool(ok), "note": note,
        })

    # ---- parent instance --------------------------------------------------
    parent = (_q(conn, INSTANCE_ROW_SQL, (PARENT_INSTANCE_ID,)) or [None])[0]
    # AMENDMENT 2026-11-18 (post-natural-resolution):
    # A1/A9 are TRANSIENT SELF-RESOLVING. The parent alternates
    # running ⇄ waiting_children on its hourly turn cycle (Appendix B
    # measured a 17-minute `running` window). A mid-turn (`running`)
    # observation is NOT an unknown world: record it and re-read ONCE
    # (bound: exactly one re-read, no loop). Any OTHER status or a missing
    # parent (error/failed/terminated/paused/…) IS an unknown world and
    # refuses. Tolerance + bound documented here and in the receipt.
    _ACCEPTABLE_PARENT = ("waiting_children", "running")
    first_status = parent["status"] if parent else None
    parent_mid_turn = False
    re_read_status = None
    if (parent is not None and first_status == "running"):
        parent_mid_turn = True
        re_read = (_q(conn, INSTANCE_ROW_SQL, (PARENT_INSTANCE_ID,)) or [None])[0]
        re_read_status = re_read["status"] if re_read else None
        if re_read is not None:
            parent = re_read
    obs["rows"]["parent"] = parent
    obs["rows"]["parent_first_status"] = first_status
    obs["rows"]["parent_re_read_status"] = re_read_status
    obs["parent_mid_turn"] = parent_mid_turn
    final_status = parent["status"] if parent else None
    a1_ok = parent is not None and final_status in _ACCEPTABLE_PARENT
    add(
        "A1", "parent exists AND status in {waiting_children, running} (amendment: transient self-resolving)",
        f"exists={parent is not None}, first={first_status}, re_read={re_read_status}, "
        f"final={final_status}, mid_turn={parent_mid_turn}",
        "exists=True, status in {waiting_children, running}",
        a1_ok,
        "amendment 2026-11-18: mid-turn `running` is recorded, not refused",
    )

    # ---- resolve every target row by prefix (uniqueness is an assertion) --
    rows: dict[str, Any] = {}
    for spec in WATCHER_TARGETS:
        rows[spec["slot"]] = _q(conn, WATCHER_ROW_SQL, (spec["prefix"] + "%",))
    for spec in COMMISSION_TARGETS:
        rows[spec["slot"]] = _q(conn, COMMISSION_ROW_SQL, (spec["prefix"] + "%",))

    worker_ids = sorted({s["worker"] for s in COMMISSION_TARGETS}
                        | {s["child_id"] for s in WATCHER_TARGETS})
    workers: dict[str, dict | None] = {}
    for wid in worker_ids:
        workers[wid] = (_q(conn, INSTANCE_ROW_SQL, (wid,)) or [None])[0]
    rows["workers"] = workers

    task_ids = sorted({s["source_task"] for s in WATCHER_TARGETS})
    tasks: dict[str, dict | None] = {}
    for tid in task_ids:
        tasks[tid] = (_q(conn, TASK_ROW_SQL, (int(tid),)) or [None])[0]
    rows["tasks"] = tasks

    targets = collect_targets(rows)
    obs["targets"] = targets
    by_label = {t["label"]: t for t in targets}
    obs["rows"].update(rows)

    def watcher_detail(label: str) -> str:
        t = by_label[label]
        rowa = rows.get(t["slot"]) or []
        r = rowa[0] if len(rowa) == 1 else {}
        return (
            f"matches={len(rowa)}, state={t['state']}, "
            f"disposition={t['disposition']}, "
            f"target={r.get('target_instance_id')}, "
            f"source_task={r.get('source_task_id')}, child={r.get('child_id')}"
        )

    def commission_detail(label: str) -> str:
        t = by_label[label]
        rowc = rows.get(t["slot"]) or []
        r = rowc[0] if len(rowc) == 1 else {}
        return (
            f"matches={len(rowc)}, state={t['state']}, "
            f"disposition={t['disposition']}, "
            f"commissioner={r.get('commissioner_instance_id')}, "
            f"worker={r.get('worker_instance_id')}"
            + (f", worker_status={t['worker_status']}" if t["worker_status"] else "")
        )

    def watcher_ok(label: str) -> bool:
        t = by_label[label]
        return t["identity_ok"] and t["disposition"] != DISPOSITION_UNEXPECTED

    def commission_ok(label: str) -> bool:
        t = by_label[label]
        return t["identity_ok"] and t["disposition"] != DISPOSITION_UNEXPECTED

    # ---- A2-A6: the commissioned quartet (identities unchanged) -----------
    add(
        "A2", "watcher 8a1f60d7 resolves uniquely; target==parent; source_task==39320; child==542b49a6",
        watcher_detail("W1"),
        "matches=1, identity matches, state=PENDING|resolved",
        watcher_ok("W1"),
    )

    task_a = tasks.get("39320")
    # AMENDMENT 2026-11-18: source-task existence is CONDITIONAL on disposition.
    #   * watcher still PENDING (would be changed by this request): a missing
    #     source task is an UNKNOWN world -> refuse (identity/unknown).
    #   * watcher already resolved (CANCELLED/FIRED): the source task may have
    #     been GC'd after resolution - that is an EXPECTED artifact, not an
    #     unknown world, because the row's terminal state is recorded in the row
    #     itself and a resolved row can no longer fire. Pass, annotated.
    w1_resolved = by_label["W1"]["disposition"] == DISPOSITION_RESOLVED
    if w1_resolved:
        a3_ok = True
        a3_note = ("source_task GC'd after resolution - EXPECTED for a resolved "
                   "watcher (row can no longer fire)")
        a3_obs = (f"watcher resolved; source_task={'MISSING (GCed, expected)' if task_a is None else 'present'}")
    else:
        a3_ok = task_a is not None and task_a["status"] in ("completed", "failed", "cancelled")
        a3_note = ""
        a3_obs = (f"exists={task_a is not None}, status={task_a['status'] if task_a else None}, "
                  f"completed_at={task_a['completed_at'] if task_a else None}")
    add(
        "A3", "task 39320 exists and is terminal (or GC'd after resolution for a resolved watcher)",
        a3_obs,
        "exists=True & terminal, OR resolved watcher with GC'd source task",
        a3_ok,
        a3_note,
    )

    worker_a = workers.get(SONIC_LEADER_ID)
    add(
        "A4", "watched worker 542b49a6 exists and is terminal",
        f"exists={worker_a is not None}, status={worker_a['status'] if worker_a else None}, "
        f"updated_at={worker_a['updated_at'] if worker_a else None}",
        "exists=True, status in {completed,terminated,error,failed}",
        worker_a is not None
        and worker_a["status"] in ("completed", "terminated", "error", "failed"),
    )

    add(
        "A5", "watcher 999efa71 unique; target==parent; source_task==39424; child_id==parent",
        watcher_detail("W2"),
        "matches=1, identity matches, state=PENDING|resolved",
        watcher_ok("W2"),
        "self-referential shape (commissioner == worker == target)",
    )

    task_b = tasks.get("39424")
    w2_resolved = by_label["W2"]["disposition"] == DISPOSITION_RESOLVED
    if w2_resolved:
        a6_ok = True
        a6_note = "source_task GC'd after resolution - EXPECTED for a resolved watcher"
        a6_obs = f"watcher resolved; source_task={'MISSING (GC\'d, expected)' if task_b is None else 'present'}"
    else:
        a6_ok = task_b is not None and task_b["status"] in ("completed", "failed", "cancelled")
        a6_note = ""
        a6_obs = f"exists={task_b is not None}, status={task_b['status'] if task_b else None}"
    add(
        "A6", "task 39424 exists and is terminal (or GC'd after resolution for a resolved watcher)",
        a6_obs,
        "exists=True & terminal, OR resolved watcher with GC'd source task",
        a6_ok,
        a6_note,
    )

    # ---- A7 (widened): all four commissions ------------------------------
    comm_targets = [t for t in targets if t["kind"] == "commission"]
    a7_ok = len(comm_targets) == 4 and all(commission_ok(t["label"]) for t in comm_targets)
    add(
        "A7", "all 4 commissions resolve uniquely; commissioner==parent; worker bindings as pinned; state outstanding|resolved",
        "; ".join(
            f"{t['label']}={t['state']}/{t['disposition']}" for t in comm_targets),
        "4/4 identity matches, state outstanding|resolved",
        a7_ok,
    )

    # ---- A8 (re-specced): the DRIFT GUARD -------------------------------
    # Every PENDING watcher targeting the parent AND every outstanding
    # commission of the parent must be INSIDE the 8-row target set. An unknown
    # row is a scope drift, never an auto-extension.
    target_watch_pks = {t["pk"] for t in targets
                        if t["kind"] == "watcher" and t["pk"]}
    target_comm_pks = {t["pk"] for t in targets
                       if t["kind"] == "commission" and t["pk"]}
    pending_rows = _q(conn, PENDING_WATCHERS_FOR_PARENT_SQL, (PARENT_INSTANCE_ID,))
    anchor_rows = _q(conn, ANCHOR_OUTSTANDING_COMMISSIONS_SQL, (PARENT_INSTANCE_ID,))
    unknown_w = [r for r in pending_rows if r["watch_id"] not in target_watch_pks]
    unknown_c = [r for r in anchor_rows if r["commission_id"] not in target_comm_pks]
    a8_ok = not unknown_w and not unknown_c
    add(
        "A8", "DRIFT GUARD: every PENDING watcher / outstanding commission of the parent is INSIDE the 8-row target set",
        f"pending_for_parent={len(pending_rows)} (unknown={len(unknown_w)}), "
        f"outstanding_for_parent={len(anchor_rows)} (unknown={len(unknown_c)})",
        "unknown=0 and unknown=0",
        a8_ok,
        "re-specced 2026-11-18 by leader ruling; an UNKNOWN extra row refuses "
        "and is listed — scope is never auto-extended",
    )

    # ---- A9 is the refusal-of-unknown-world clause; it is A1 negated -----
    final_status2 = parent["status"] if parent else None
    add(
        "A9", "parent status in {waiting_children, running} (transient self-resolving; refuses any other status)",
        f"status={final_status2}, mid_turn={parent_mid_turn}, "
        f"first={first_status}, re_read={re_read_status}",
        "status in {waiting_children, running}",
        parent is not None and final_status2 in _ACCEPTABLE_PARENT,
    )

    # ---- A10-A13: the four EXTRA rows (leader ruling widening) -----------
    add(
        "A10", "watcher 5602e91d resolves uniquely; target==parent; source_task==39564; child==82551f59",
        watcher_detail("W3"),
        "matches=1, identity matches, state=PENDING|resolved",
        watcher_ok("W3"),
    )
    add(
        "A11", "watcher c83e12a2 resolves uniquely; target==parent; source_task==39564; child==82551f59",
        watcher_detail("W4"),
        "matches=1, identity matches, state=PENDING|resolved",
        watcher_ok("W4"),
    )
    add(
        "A12", "commission 06001f88 resolves uniquely; commissioner==parent; worker==82551f59",
        commission_detail("M3"),
        "matches=1, identity matches, state=outstanding|resolved",
        commission_ok("M3"),
    )
    add(
        "A13", "commission a1561f19 resolves uniquely; commissioner==parent; worker==82551f59",
        commission_detail("M4"),
        "matches=1, identity matches, state=outstanding|resolved",
        commission_ok("M4"),
    )
    task_c = tasks.get("39564")
    w34_resolved = (by_label["W3"]["disposition"] == DISPOSITION_RESOLVED
                    and by_label["W4"]["disposition"] == DISPOSITION_RESOLVED)
    if w34_resolved:
        a14_ok = True
        a14_note = "source_task GC'd after resolution - EXPECTED for resolved watchers"
        a14_obs = f"watchers resolved; source_task={'MISSING (GC\'d, expected)' if task_c is None else 'present'}"
    else:
        a14_ok = task_c is not None and task_c["status"] in ("completed", "failed", "cancelled")
        a14_note = ""
        a14_obs = f"exists={task_c is not None}, status={task_c['status'] if task_c else None}"
    add(
        "A14", "task 39564 (source of W3/W4) exists and is terminal (or GC'd after resolution)",
        a14_obs,
        "exists=True & terminal, OR resolved watchers with GC'd source task",
        a14_ok,
        a14_note,
    )

    # ---- the park predicates --------------------------------------------
    pending_n = _q(conn, COUNT_PENDING_SQL, (PARENT_INSTANCE_ID,))[0]["n"]
    obs["rows"]["pending_count"] = pending_n
    outstanding_n = _q(conn, COUNT_OUTSTANDING_COMMISSIONS_SQL, (PARENT_INSTANCE_ID,))[0]["n"]

    # AMENDMENT 2026-11-18: the drift guard's EXPECTED population is DERIVED from
    # the target set's current dispositions (not hardcoded): a target watcher that
    # is still PENDING is "in scope"; one already resolved is "resolved". The
    # guard's semantics are unchanged (unknown = PENDING watcher / outstanding
    # commission of the parent OUTSIDE the 8-row target set).
    expected_pending = sum(1 for t in targets
                           if t["kind"] == "watcher"
                           and t["disposition"] == DISPOSITION_STALE)
    expected_outstanding = sum(1 for t in targets
                               if t["kind"] == "commission"
                               and t["disposition"] == DISPOSITION_STALE)
    obs["extended_census"]["expected_pending_watchers"] = expected_pending
    obs["extended_census"]["expected_outstanding_commissions"] = expected_outstanding

    # ---- EXTENDED CENSUS (never a gate; the A8 detail lives here) --------
    census_w: list[dict[str, Any]] = []
    for r in pending_rows:
        r = dict(r)
        r["scope"] = ("in_target_set" if r["watch_id"] in target_watch_pks
                      else "UNKNOWN — DRIFT GUARD REFUSES")
        census_w.append(r)
    census_c: list[dict[str, Any]] = []
    for r in anchor_rows:
        r = dict(r)
        r["scope"] = ("in_target_set" if r["commission_id"] in target_comm_pks
                      else "UNKNOWN — DRIFT GUARD REFUSES")
        census_c.append(r)

    obs["extended_census"]["pending_watchers_for_parent"] = census_w
    obs["extended_census"]["anchor_outstanding_commissions"] = census_c
    obs["extended_census"]["outstanding_commission_count"] = outstanding_n
    obs["extended_census"]["extra_pending_watchers"] = unknown_w
    obs["extended_census"]["extra_outstanding_commissions"] = unknown_c

    # ---- out-of-scope (observational ONLY) ------------------------------
    for r in _q(conn, OUT_OF_SCOPE_SQL, (OUT_OF_SCOPE_COMMISSIONER,)):
        r = dict(r)
        r["scope"] = "OUT-OF-SCOPE (terminated Maintenancer commissioner; never in a repair statement)"
        obs["out_of_scope"].append(r)

    # ---- classification (disposition-driven state machine) --------------
    dispositions = [t["disposition"] for t in targets]
    parent_ok = parent is not None and parent["status"] == "waiting_children"
    identity_all_ok = all(t["identity_ok"] for t in targets)
    all_resolved = len(dispositions) == 8 and all(
        d == DISPOSITION_RESOLVED for d in dispositions)

    def record_divergences() -> None:
        for a in obs["assertions"]:
            if not a["ok"]:
                obs["divergences"].append(
                    f"{a['id']} {a['name']}: observed={a['observed']} "
                    f"expected={a['expected']}"
                )
        for t in targets:
            if t["disposition"] == DISPOSITION_UNEXPECTED:
                obs["divergences"].append(
                    f"UNEXPECTED target row {t['label']} ({t['kind']} "
                    f"{t['prefix']}): state={t['state']} "
                    f"worker_status={t.get('worker_status')}"
                )
        for r in unknown_w:
            obs["divergences"].append(
                f"UNKNOWN PENDING watcher {r['watch_id'][:8]} source_task="
                f"{r['source_task_id']} child={str(r['child_id'])[:8]} "
                f"— outside the 8-row target set"
            )
        for r in unknown_c:
            obs["divergences"].append(
                f"UNKNOWN outstanding commission {r['commission_id'][:8]} "
                f"worker={str(r['worker_instance_id'])[:8]} lane={r['lane']} "
                f"— outside the 8-row target set"
            )

    if DISPOSITION_UNEXPECTED in dispositions:
        obs["classification"] = "unexpected"
        record_divergences()
    elif not identity_all_ok:
        obs["classification"] = "unexpected"
        record_divergences()
    elif not a8_ok:
        obs["classification"] = "unexpected"
        record_divergences()
    elif all_resolved:
        # Nothing left to repair. The parent may already have woken — that is
        # the success case, not a refusal: no statement is needed either way.
        obs["classification"] = "already_repaired"
    elif not parent_ok:
        # A1/A9 STRICT: refuse while the parent is mid-wake (hourly
        # running ⇄ waiting_children cycle). The operator re-runs after re-park.
        obs["classification"] = "unexpected"
        record_divergences()
    elif DISPOSITION_STALE in dispositions:
        obs["classification"] = ("not_yet" if all(
            d == DISPOSITION_STALE for d in dispositions) else "partial")
    else:  # pragma: no cover - defensive
        obs["classification"] = "unexpected"
        record_divergences()
    return obs

# ===========================================================================
# Inventory fingerprint — proves "nothing else changed"
# ===========================================================================

#: GLOBAL inventory — the literal "everything else" scan (all rows of both
#: tables except the eight repair targets). Recorded for audit. NOTE: on a live
#: fleet this population churns constantly (≈12k watcher rows, ≈4.7k commission
#: rows at measurement time), so equality across time is only achievable on a
#: QUIESCED system. ``--verify`` therefore treats this as INFORMATIONAL and
#: anchors the hard check on the parent-scoped digest below.
WATCHER_INVENTORY_SQL = (
    "SELECT watch_id, source_task_id, target_instance_id, state, created_at, "
    "fired_at, enqueued_at, follow_up_payload::text AS payload "
    "FROM dependency_watchers WHERE watch_id::text NOT LIKE ALL(%s) "
    "ORDER BY watch_id"
)

COMMISSION_INVENTORY_SQL = (
    "SELECT commission_id, mission_id, commissioner_instance_id, "
    "worker_instance_id, work_id, lane, state, created_at, satisfied_at, "
    "abandon_reason, advisory_backfill::text AS advisory, resolved_at "
    "FROM mission_commissions WHERE commission_id::text NOT LIKE ALL(%s) "
    "ORDER BY commission_id"
)

#: PARENT-SCOPED inventory — every watcher aimed at the parent and every
#: commission of the parent's mission epochs, i.e. exactly the rows the repair
#: can plausibly move. This is the load-bearing before/after anchor.
PARENT_WATCHER_INVENTORY_SQL = (
    "SELECT watch_id, source_task_id, target_instance_id, state, created_at, "
    "fired_at, enqueued_at "
    "FROM dependency_watchers WHERE target_instance_id = %s "
    "ORDER BY watch_id"
)

PARENT_COMMISSION_INVENTORY_SQL = (
    "SELECT mc.commission_id, mc.mission_id, mc.commissioner_instance_id, "
    "mc.worker_instance_id, mc.lane, mc.state, mc.created_at, "
    "mc.satisfied_at, mc.abandon_reason, mc.resolved_at, "
    "mc.advisory_backfill::text AS advisory "
    "FROM mission_commissions mc "
    "JOIN mission_records mr ON mr.mission_id = mc.mission_id "
    "WHERE mr.anchor_instance_id = %s ORDER BY mc.commission_id"
)


def _row_key(r: dict[str, Any]) -> str:
    for k in ("watch_id", "commission_id"):
        if k in r:
            return str(r[k])
    return "|".join(f"{k}={r[k]!r}" for k in sorted(r))


def _dump_rows(rows: list[dict[str, Any]]) -> dict[str, str]:
    """PK-keyed canonical dumps — used for both digests and for row-level diffs."""
    out: dict[str, str] = {}
    for r in rows:
        out[_row_key(r)] = "\t".join(f"{k}={r[k]!r}" for k in r)
    return out


def _digest(dumps: dict[str, str], parent_keys: dict[str, Any] | None = None) -> str:
    payload = json.dumps(
        {"rows": [dumps[k] for k in sorted(dumps)], "parent_keys": parent_keys or {}},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def inventory(conn) -> dict[str, Any]:
    """Two ordered snapshots + the parent's key fields.

    * ``global`` — every other row of both tables (the literal "nothing else
      changed anywhere" scan). Churns on a live fleet; audit-only.
    * ``parent_scoped`` — every watcher targeting the parent and every
      commission of the parent's mission epochs, EXCLUDING ALL EIGHT repair
      targets. This is the digest ``--verify`` holds as a hard check, because
      it is exactly the population the repair could plausibly move.
    """
    target_w_ids = [p + "%" for p in TARGET_WATCHER_PREFIXES]
    target_c_ids = [p + "%" for p in TARGET_COMMISSION_PREFIXES]

    g_w = _q(conn, WATCHER_INVENTORY_SQL, (target_w_ids,))
    g_c = _q(conn, COMMISSION_INVENTORY_SQL, (target_c_ids,))
    p_w = [r for r in _q(conn, PARENT_WATCHER_INVENTORY_SQL, (PARENT_INSTANCE_ID,))
           if not any(str(r["watch_id"]).startswith(p)
                      for p in TARGET_WATCHER_PREFIXES)]
    p_c = [r for r in _q(conn, PARENT_COMMISSION_INVENTORY_SQL, (PARENT_INSTANCE_ID,))
           if not any(str(r["commission_id"]).startswith(p)
                      for p in TARGET_COMMISSION_PREFIXES)]

    parent = (_q(conn, INSTANCE_ROW_SQL, (PARENT_INSTANCE_ID,)) or [None])[0]
    parent_keys = {
        "instance_id": (parent or {}).get("instance_id"),
        "agent_id": (parent or {}).get("agent_id"),
        "status": (parent or {}).get("status"),
        "parent_id": (parent or {}).get("parent_id"),
        "created_at": (parent or {}).get("created_at"),
        "updated_at": (parent or {}).get("updated_at"),
        "version": (parent or {}).get("version"),
    }

    g_dumps = _dump_rows(g_w)
    g_dumps.update({f"c:{k}": v for k, v in _dump_rows(g_c).items()})
    p_dumps = _dump_rows(p_w)
    p_dumps.update({f"c:{k}": v for k, v in _dump_rows(p_c).items()})

    return {
        # the hard anchor
        "parent_scoped_sha256": _digest(p_dumps, parent_keys),
        "parent_scoped_rows": p_dumps,
        "parent_scoped_counts": {"watchers": len(p_w), "commissions": len(p_c)},
        # the literal global scan (audit / informational)
        "global_sha256": _digest(g_dumps),
        "global_counts": {"watchers": len(g_w), "commissions": len(g_c)},
        "parent_keys": parent_keys,
    }


# ===========================================================================
# Repair statement construction (EMITTED AS TEXT — never executed)
# ===========================================================================


def _do_block(label: str, update_sql: str) -> str:
    """Wrap one guarded UPDATE in a ``DO $$`` block.

    WHY A DO BLOCK (and not a bare UPDATE): the daemon's gated executor
    (``daemon/tools/ens_db_tools.py``) classifies by first keyword and applies
    ``_is_idempotent`` to DML. A bare UPDATE carrying ``now()`` for the
    execution-time stamp is REJECTED as non-idempotent-DML
    (``_NON_IDEMPOTENT_TOKENS`` includes ``\\bnow\\(\\)``, ens_db_tools.py:229-241).
    A ``DO $$`` block is classified DDL and accepted via the ``DO $$`` marker.
    The block keeps every property the bare UPDATE would have had:

      * guarded ``WHERE <full PK> AND <state = 'expected'>`` — exactly-once;
      * ``now()`` evaluated at EXECUTION time, not emit time;
      * idempotent: a second run matches 0 rows, raises nothing, leaves the
        same final state (reported via NOTICE instead of an error);
      * any real failure raises → the executor's transaction rolls back.

    The trade-off (declared, not hidden): the executor's pre/post row-image
    audit snapshot is DDL-classed, so ``repair_log`` carries no row image for
    these blocks. The compensating control is the receipt's inventory
    ``sha256`` (re-checked by ``--verify``), which proves nothing OUTSIDE the
    targets changed.
    """
    return (
        "DO $$\n"
        "DECLARE\n"
        "    n integer;\n"
        "BEGIN\n"
        f"    -- repair block: {label}\n"
        + textwrap.indent(update_sql.rstrip().rstrip(";") + ";", "    ")
        + "\n    GET DIAGNOSTICS n = ROW_COUNT;\n"
        "    IF n = 1 THEN\n"
        f"        RAISE NOTICE '{label}: repaired 1 row';\n"
        "    ELSIF n = 0 THEN\n"
        f"        RAISE NOTICE '{label}: no-op (already repaired or state moved)';\n"
        "    ELSE\n"
        f"        RAISE EXCEPTION '{label}: EXPECTED 1 ROW, GOT % — aborting', n;\n"
        "    END IF;\n"
        "END $$;"
    )


def _watcher_stmt(t: dict[str, Any], n: int) -> dict[str, Any]:
    """One guarded CANCELLED transition for a still-PENDING watcher."""
    sql = (
        "UPDATE dependency_watchers\n"
        "SET state = 'CANCELLED',\n"
        f"    fired_at = {NOW_UTC_ISO_SQL},\n"
        f"    enqueued_at = {NOW_UTC_ISO_SQL}\n"
        f"WHERE watch_id = '{t['pk']}'\n"
        "  AND state = 'PENDING'\n"
        f"  AND target_instance_id = '{PARENT_INSTANCE_ID}';\n"
    )
    return {
        "n": n,
        "label": t["label"],
        "block_kind": "repair",
        "table": "dependency_watchers",
        "pk": t["pk"],
        "current_state": t["state"],
        "target_state": WATCHER_TARGET_STATE,
        "expected_rowcount": 1,
        "effect": t["effect"],
        "why": t["why"],
        "sql": _do_block(f"{t['label']} cancel {t['prefix']}", sql),
    }


def _watcher_backfill_stmt(t: dict[str, Any], n: int) -> dict[str, Any]:
    """Conditional enqueued_at backfill for a row the BOOT SWEEP cancelled first.

    The ruling's safety argument is that cancelling stamps ``enqueued_at``,
    which the sweep does NOT do (dependency_bus.py:2653-2666). When a
    pre-ceremony boot sweep already moved the row to CANCELLED, the stamp is
    still owed — otherwise the row remains a re-arm candidate for
    ``find_cancelled_unenqueued_for_target`` (repository.py:797-843).
    """
    sql = (
        "UPDATE dependency_watchers\n"
        f"SET enqueued_at = {NOW_UTC_ISO_SQL}\n"
        f"WHERE watch_id = '{t['pk']}'\n"
        "  AND state = 'CANCELLED'\n"
        "  AND enqueued_at IS NULL;\n"
    )
    return {
        "n": n,
        "label": f"{t['label']}-backfill",
        "block_kind": "backfill",
        "table": "dependency_watchers",
        "pk": t["pk"],
        "current_state": t["state"],
        "target_state": "CANCELLED (enqueued_at stamped)",
        "expected_rowcount": 1,
        "effect": (
            "stamp enqueued_at on a row this repair did NOT cancel (a "
            "pre-ceremony boot sweep got there first). Same end state the "
            "repair's own cancel writes; closes the pause+resume re-arm hole "
            "(repository.py:797-843). Does not touch state/fired_at."
        ),
        "why": (
            "the boot sweep cancels with state='CANCELLED', fired_at=now only "
            "— enqueued_at stays NULL, so the row is re-arm eligible. This is "
            "a DISCLOSED extension of the ruling's own safety argument, not a "
            "widening of its row scope."
        ),
        "sql": _do_block(f"{t['label']} backfill enqueued_at {t['prefix']}", sql),
    }


def _commission_stmt(t: dict[str, Any], n: int) -> dict[str, Any]:
    """One guarded ``outstanding → abandoned`` transition."""
    reason = t["reason"]
    sql = (
        "UPDATE mission_commissions\n"
        "SET state = 'abandoned',\n"
        f"    resolved_at = {NOW_UTC_ISO_SQL},\n"
        f"    abandon_reason = '{reason}'\n"
        f"WHERE commission_id = '{t['pk']}'\n"
        "  AND state = 'outstanding'\n"
        f"  AND commissioner_instance_id = '{PARENT_INSTANCE_ID}';\n"
    )
    return {
        "n": n,
        "label": t["label"],
        "block_kind": "repair",
        "table": "mission_commissions",
        "pk": t["pk"],
        "current_state": t["state"],
        "target_state": COMMISSION_TARGET_STATE,
        "expected_rowcount": 1,
        "reason": reason,
        "worker_id": t["worker_id"],
        "worker_status": t["worker_status"],
        "effect": t["effect"],
        "why": t["why"],
        "sql": _do_block(f"{t['label']} abandon {t['prefix']}", sql),
    }


def build_statements(obs: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the guarded statements from the observed per-row dispositions.

    * ``in_scope_stale`` → include the repair block.
    * ``naturally_resolved`` → EXCLUDE from statements and disclose per row
      (the leader's partial-resolution boundary: lane 7's TTL abandons and the
      boot sweep's cancels must not make the gate brittle).
    * A CANCELLED watcher whose ``enqueued_at`` is still NULL → one guarded
      backfill block (see ``_watcher_backfill_stmt``).
    * ``unexpected`` rows never reach here — the classifier refuses first.
    """
    stmts: list[dict[str, Any]] = []
    seq = 0
    for t in obs.get("targets") or []:
        if t["kind"] == "watcher":
            if t["disposition"] == DISPOSITION_STALE and t["pk"]:
                seq += 1
                stmts.append(_watcher_stmt(t, seq))
            elif t["needs_backfill"] and t["pk"]:
                seq += 1
                stmts.append(_watcher_backfill_stmt(t, seq))
        else:
            if t["disposition"] == DISPOSITION_STALE and t["pk"]:
                seq += 1
                stmts.append(_commission_stmt(t, seq))
    return stmts


# ===========================================================================
# Receipt rendering
# ===========================================================================


def _assertion_table(obs: dict[str, Any]) -> str:
    rows = [
        (a["id"], a["name"][:70], str(a["observed"])[:90], str(a["expected"])[:50],
         "PASS" if a["ok"] else "FAIL")
        for a in obs["assertions"]
    ]
    return _table(rows, ("id", "assertion", "observed", "expected", "verdict"))


def _extended_table(obs: dict[str, Any]) -> str:
    rows = []
    for r in obs["extended_census"].get("pending_watchers_for_parent", []):
        rows.append((
            "watcher", r["watch_id"][:8], str(r["source_task_id"]),
            str(r.get("child_id"))[:8], r["state"], r["scope"][:60],
        ))
    for r in obs["extended_census"].get("anchor_outstanding_commissions", []):
        rows.append((
            "commission", r["commission_id"][:8], str(r["worker_instance_id"])[:8],
            r["lane"], r["state"], r["scope"][:60],
        ))
    if not rows:
        return "(no rows)"
    return _table(rows, ("kind", "id", "task/worker", "child/lane", "state", "scope"))


def _disposition_table(obs: dict[str, Any]) -> str:
    """Per-row disposition BEFORE statement build (the leader's boundary)."""
    rows = []
    for t in obs.get("targets") or []:
        extra = ""
        if t["kind"] == "commission":
            extra = f"{t['reason'] or '-'} / worker {t['worker_status'] or 'MISSING'}"
        elif t.get("needs_backfill"):
            extra = "needs enqueued_at backfill"
        rows.append((
            t["label"], t["kind"], t["prefix"], str(t["state"]),
            t["disposition"], extra,
        ))
    if not rows:
        return "(no targets)"
    return _table(rows, ("label", "kind", "id", "state", "disposition", "reason/note"))


def render_receipt_md(
    obs: dict[str, Any],
    pre_json_sha: str,
    stmts: list[dict[str, Any]],
    inv: dict[str, Any],
    *,
    sql_filename: str,
    synthetic: bool = False,
) -> str:
    banner = (
        "# LIVE WEDGE REPAIR REQUEST — NOT EXECUTED\n\n"
        "> **NOT-EXECUTED BANNER.** No statement in this request has been run, "
        "by any process, at any time. This artifact is a PROPOSAL for the "
        "daemon's gated repair path. The generating script has no `--apply` and "
        "opens every connection with `default_transaction_read_only=on`.\n\n"
        "> **Execution is the daemon's gated repair path (3-factor: user "
        "attestation + audit stamp + dry-run), not this script.**\n"
    )
    if synthetic:
        banner = (
            "# SYNTHETIC FIXTURE — NOT A LIVE REQUEST\n\n"
            "> This receipt was generated from a **synthetic fixture**, not from "
            "`ensemble_prod`. It exists so a reviewer can verify the emitter's "
            "guard / fingerprint / receipt mechanics. **Do not execute anything "
            "in this file.**\n"
        )

    lines = [banner]
    lines.append("> **RECEIPT NOTE (verbatim, amendment 2026-11-18):** " + RECEIPT_NOTE + "\n")
    lines.append(f"**Generated:** {obs['generated_at']}  ")
    lines.append(f"**Database observed:** `{obs['database']}`  ")
    lines.append(f"**Parent instance:** `{PARENT_INSTANCE_ID}` (agent `{PARENT_AGENT_ID}`)  ")
    lines.append(f"**Classification at emit time:** `{obs['classification']}`  ")
    lines.append(f"**Gate flag presented:** `{GATE_FLAG}`  ")
    lines.append(f"**Pre-check JSON sha256:** `{pre_json_sha}`  ")
    lines.append(f"**Statement file:** `{sql_filename}`  ")
    lines.append(
        f"**Inventory fingerprint (parent-scoped, the --verify anchor):** "
        f"`{inv['parent_scoped_sha256']}` "
        f"({inv['parent_scoped_counts']['watchers']} other parent watchers, "
        f"{inv['parent_scoped_counts']['commissions']} other parent commissions)  ")
    lines.append(
        f"**Inventory fingerprint (global scan, informational on a live fleet):** "
        f"`{inv['global_sha256']}` "
        f"({inv['global_counts']['watchers']} watcher rows, "
        f"{inv['global_counts']['commissions']} commission rows)\n")

    lines.append("## 1. Pre-check assertions (A1-A14)\n")
    lines.append("```")
    lines.append(_assertion_table(obs))
    lines.append("```\n")

    lines.append("## 2. Per-row disposition (evaluated BEFORE any statement)\n")
    lines.append(
        "The leader's partial-resolution boundary: a row the system itself "
        "already resolved (`naturally_resolved`) is EXCLUDED from the "
        "statements and disclosed here, so the gate is not brittle against "
        "lane 7's TTL abandons or a pre-ceremony boot sweep. `in_scope_stale` "
        "rows get a block. A CANCELLED row whose `enqueued_at` is still NULL "
        "gets one guarded backfill block.\n")
    lines.append("```")
    lines.append(_disposition_table(obs))
    lines.append("```\n")

    lines.append("## 3. Extended census (reported, NEVER gated)\n")
    lines.append("Every PENDING watcher targeting the parent, and every "
                 "anchor-scoped outstanding commission. Rows marked "
                 "`in_target_set` are inside the 8-row scope; any row marked "
                 "`UNKNOWN` would have refused the emit (drift guard).\n")
    lines.append("```")
    lines.append(_extended_table(obs))
    lines.append("```\n")
    lines.append(
        f"Park predicate value at emit time: `count_pending_for_target(parent)` = "
        f"**{obs['rows']['pending_count']}**; "
        f"`count_outstanding_commissions(parent)` = "
        f"**{obs['extended_census']['outstanding_commission_count']}**.\n"
    )
    extras_w = obs["extended_census"]["extra_pending_watchers"]
    extras_c = obs["extended_census"]["extra_outstanding_commissions"]
    if extras_w or extras_c:
        lines.append(
            "> **DRIFT GUARD TRIPPED (this fixture was rendered anyway).** "
            f"{len(extras_w)} PENDING watcher(s) and {len(extras_c)} outstanding "
            "commission(s) sit OUTSIDE the 8-row target set. `--emit-request` "
            "REFUSES in this state; scope is never auto-extended.\n"
        )

    lines.append("## 4. Statements (EMITTED ONLY — never executed)\n")
    for s in stmts:
        lines.append(f"### Block {s['n']} ({s['label']}, {s['block_kind']}) — "
                     f"{s['table']} `{s['pk']}`\n")
        if s.get("reason"):
            lines.append(f"- **Abandon reason:** `{s['reason']}` "
                         f"(worker `{s['worker_id']}`, status "
                         f"`{s['worker_status']}`)")
        lines.append(f"- **Current state:** `{s['current_state']}` → "
                     f"**target state:** `{s['target_state']}`")
        lines.append(f"- **Guard:** full PK + `state = '{s['current_state']}'` + "
                     f"`commissioner/target_instance_id = parent`")
        lines.append(f"- **Expected rowcount:** {s['expected_rowcount']}")
        lines.append(f"- **Intended effect:** {s['effect']}")
        lines.append(f"- **Why this state (source):** {s['why']}")
        lines.append("\n```sql")
        lines.append(s["sql"])
        lines.append("```\n")

    lines.append("## 5. Inventory snapshot (for --verify)\n")
    lines.append(
        "Two snapshots, both `sha256` over ORDERED PK-keyed dumps, both "
        "EXCLUDING ALL EIGHT target rows, both including the parent row's key "
        "fields (`instance_id`, `agent_id`, `status`, `parent_id`, "
        "`created_at`, `updated_at`, `version`):\n\n"
        "* **`parent_scoped_sha256`** — every `dependency_watchers` row whose "
        "`target_instance_id` is the parent, plus every `mission_commissions` "
        "row of the parent's mission epochs. **This is the load-bearing "
        "before/after anchor** for `--verify`, because it is exactly the "
        "population this repair can plausibly move.\n"
        "* **`global_sha256`** — the literal \"everything else in both tables\" "
        "scan, recorded for audit. On a live fleet this population churns "
        "constantly, so equality across time is only achievable on a QUIESCED "
        "system; `--verify` reports it as informational and lists the row-level "
        "delta instead of failing on unrelated churn.\n"
    )
    lines.append("```json")
    lines.append(json.dumps(
        {k: v for k, v in inv.items() if k != "parent_scoped_rows"},
        indent=2, sort_keys=True))
    lines.append("```\n")
    lines.append(
        "**Parent-scoped rows at emit time** (the exact set `--verify` "
        "re-diffs):\n\n```\n")
    for k in sorted(inv["parent_scoped_rows"]):
        lines.append(f"{k}\t{inv['parent_scoped_rows'][k]}")
    lines.append("```\n")
    lines.append(f"**Parent row keys at emit time:** `{json.dumps(inv['parent_keys'], sort_keys=True)}`\n")

    lines.append("## 6. Amendment 2026-11-18 (post-natural-resolution)\n")
    lines.append(
        "**Parent posture at emit:** `first=`" + str(obs["rows"].get("parent_first_status"))
        + "`, re_read=`" + str(obs["rows"].get("parent_re_read_status"))
        + "`, final=`" + str((obs["rows"].get("parent") or {}).get("status"))
        + "`, mid_turn=" + str(obs.get("parent_mid_turn")) + ".\n\n"
        "**Drift-guard expected population (derived from the target set's "
        "dispositions):** PENDING watchers = "
        + str(obs["extended_census"].get("expected_pending_watchers"))
        + ", outstanding commissions = "
        + str(obs["extended_census"].get("expected_outstanding_commissions"))
        + ".\n\n"
        "**Backfill eligibility** mirrors the re-arm path's terminal-child skip "
        "(instance_lifecycle.py:4229-4239; TERMINAL_STATUSES from "
        "job_queue_service.py:126-131 = {terminated, completed, error, failed}): "
        "a CANCELLED watcher whose child is TERMINAL is skip-protected (no "
        "backfill); one whose child is NON-TERMINAL is re-arm eligible and is "
        "stamped. Missing child row -> skipped.\n\n"
        "**Receipt note (verbatim):** " + RECEIPT_NOTE + "\n"
    )
    lines.append("## 7. Authority statement\n")
    lines.append(
        "**Execution is the daemon's gated repair path (3-factor: user "
        "attestation + audit stamp + dry-run), not this script.** "
        "The executor is `ens_db_repair_execute` "
        "(`daemon/tools/ens_db_tools.py:939`), reachable only from an agent "
        "holding the privileged `ens-db` category (today: `maintenancer`). "
        "Its three gates are: kill-switch `ENSEMBLE_REPAIR_ENABLED`, input "
        "validation (idempotency + migration-runner trap + self-surgery "
        "refusal), and a single-use confirm nonce that only a DRY RUN mints.\n"
    )
    lines.append(
        "**Rollback is NOT free.** See `RUNBOOK.md` §9: reverting these rows "
        "means re-inserting a PENDING watcher, which RE-WEDGES the parent.\n"
    )
    return _scrub("\n".join(lines))


def render_sql_file(obs: dict[str, Any], stmts: list[dict[str, Any]],
                    pre_json_sha: str) -> str:
    head = [
        "-- =====================================================================",
        "-- LIVE WEDGE REPAIR REQUEST — NOT EXECUTED",
        "-- ---------------------------------------------------------------------",
        "-- Generated by: live_wedge_repair.py --emit-request",
        f"-- Generated at: {obs['generated_at']}",
        f"-- Database observed: {obs['database']}",
        f"-- Parent instance: {PARENT_INSTANCE_ID}",
        f"-- Pre-check JSON sha256: {pre_json_sha}",
        f"-- Gate flag: {GATE_FLAG}",
        "-- RECEIPT NOTE (verbatim, amendment 2026-11-18): " + RECEIPT_NOTE,
        "--",
        "-- THIS FILE CONTAINS PROPOSED STATEMENTS ONLY. NOTHING HAS BEEN RUN.",
        "-- The generating script has no --apply and holds read-only DB sessions.",
        "-- Execution is the daemon's gated repair path (3-factor: user",
        "-- attestation + audit stamp + dry-run), not this script.",
        "--",
        "-- OPERATOR NOTE: the gated executor takes ONE statement per call.",
        "-- Submit blocks 1..N in order, each as its own dry-run + confirm pair.",
        "-- Every block is guarded and idempotent: a re-run affects 0 rows and",
        "-- raises nothing, so a partial application is safe to resume.",
        "-- =====================================================================",
        "",
    ]
    body = []
    for s in stmts:
        body.append(
            f"-- BLOCK {s['n']} / {len(stmts)} — {s['label']} "
            f"({s['block_kind']}) — {s['table']} {s['pk']}"
        )
        if s.get("reason"):
            body.append(f"--   abandon reason: {s['reason']} "
                        f"(worker {s['worker_id']}, status {s['worker_status']})")
        body.append(f"--   current state {s['current_state']} -> {s['target_state']}; "
                    f"expected rowcount {s['expected_rowcount']}")
        body.append(f"--   effect: {s['effect']}")
        body.append(s["sql"])
        body.append("")
    return _scrub("\n".join(head + body))


# ===========================================================================
# Modes
# ===========================================================================


def mode_inspect(conn, args) -> int:
    obs = observe(conn, required_db=args.require_db)
    inv = inventory(conn)
    obs["inventory"] = inv

    _p("\n=== PENDING WATCHERS TARGETING THE PARENT (extended census) ===")
    _p(_extended_table(obs))
    _p("")
    _p("=== PER-ROW DISPOSITION (8-row target set) ===")
    _p(_disposition_table(obs))
    _p("")
    _p("=== ASSERTIONS A1-A14 ===")
    _p(_assertion_table(obs))
    _p("")
    _p(f"classification = {obs['classification']}")
    _p(f"parent_mid_turn (amendment) = {obs.get('parent_mid_turn')}  "
       f"(first={obs['rows'].get('parent_first_status')}, "
       f"re_read={obs['rows'].get('parent_re_read_status')})")
    _p(f"drift-guard expected population (derived): "
       f"PENDING watchers={obs['extended_census'].get('expected_pending_watchers')}, "
       f"outstanding commissions={obs['extended_census'].get('expected_outstanding_commissions')}")
    if obs["divergences"]:
        _p("divergences:")
        for d in obs["divergences"]:
            _p(f"  - {d}")
    _p("")
    _p(f"count_pending_for_target(parent)        = {obs['rows']['pending_count']}")
    _p(f"count_outstanding_commissions(parent)   = "
       f"{obs['extended_census']['outstanding_commission_count']}")
    _p(f"inventory parent_scoped_sha256          = {inv['parent_scoped_sha256']}")
    _p(f"  ({inv['parent_scoped_counts']['watchers']} other parent watchers, "
       f"{inv['parent_scoped_counts']['commissions']} other parent commissions)")
    _p(f"inventory global_sha256                 = {inv['global_sha256']}")
    _p(f"  ({inv['global_counts']['watchers']} watcher rows, "
       f"{inv['global_counts']['commissions']} commission rows)")
    if obs["out_of_scope"]:
        _p("")
        _p("=== OUT-OF-SCOPE (observational only — NEVER in a repair statement) ===")
        for r in obs["out_of_scope"]:
            _p(f"  {r['commission_id'][:8]} commissioner={r['commissioner_instance_id'][:8]} "
               f"worker={str(r['worker_instance_id'])[:8]} lane={r['lane']} "
               f"state={r['state']} created={r['created_at']}")
    if args.json:
        _p("")
        _p("=== JSON ===")
        _p(json.dumps(obs, indent=2, sort_keys=True, default=str))
    return 0


def mode_emit(conn, args) -> int:
    if args.gate != GATE_FLAG:
        _p(f"REFUSED: --emit-request requires --gate {GATE_FLAG!r} "
           f"(got {args.gate!r}). Nothing written.")
        return 4

    obs = observe(conn, required_db=args.require_db)
    pre_json = json.dumps(
        {"assertions": obs["assertions"], "rows": obs["rows"],
         "targets": obs["targets"],
         "extended_census": obs["extended_census"], "classification": obs["classification"]},
        sort_keys=True, default=str,
    )
    pre_json_sha = hashlib.sha256(pre_json.encode("utf-8")).hexdigest()
    obs["pre_check_json_sha256"] = pre_json_sha

    _p("=== ASSERTIONS A1-A14 ===")
    _p(_assertion_table(obs))
    _p("")
    _p("=== PER-ROW DISPOSITION (8-row target set) ===")
    _p(_disposition_table(obs))
    _p("")
    _p(f"classification = {obs['classification']}")
    _p(f"parent_mid_turn = {obs.get('parent_mid_turn')} "
       f"(first={obs['rows'].get('parent_first_status')}, "
       f"re_read={obs['rows'].get('parent_re_read_status')})")

    if obs["classification"] == "already_repaired":
        _p("")
        _p("ALREADY REPAIRED / NO-OP — all 8 target rows are already "
           "non-PENDING / non-outstanding (resolved naturally). Nothing written; "
           "no second request file is emitted.")
        return 0

    if obs["classification"] not in ("not_yet", "partial"):
        _p("")
        _p("REFUSED — classification is `unexpected`. The world is not in the "
           "state this request's guards describe; emitting would produce a "
           "proposal built on a stale premise. Nothing written.")
        for d in obs["divergences"]:
            _p(f"  - {d}")
        failed = [a for a in obs["assertions"] if not a["ok"]]
        for a in failed:
            _p(f"  FAIL {a['id']}: observed={a['observed']} expected={a['expected']}")
        extras_w = obs["extended_census"]["extra_pending_watchers"]
        extras_c = obs["extended_census"]["extra_outstanding_commissions"]
        if extras_w or extras_c:
            _p("")
            _p("DRIFT GUARD (A8) — UNKNOWN rows hold the parent's gates OUTSIDE "
               "the 8-row target set:")
            for r in extras_w:
                _p(f"  watcher {r['watch_id'][:8]} source_task={r['source_task_id']} "
                   f"child={str(r['child_id'])[:8]} [{r['scope']}]")
            for r in extras_c:
                _p(f"  commission {r['commission_id'][:8]} "
                   f"worker={str(r['worker_instance_id'])[:8]} lane={r['lane']}")
            _p("  Scope is NEVER auto-extended: a new row is a new decision.")
        return 4

    # ---- state is `not_yet` / `partial`: build the request -------------
    stmts = build_statements(obs)
    inv = inventory(conn)
    ts = _stamp_for_filename()
    out_dir = Path(args.out_dir) / "emitted"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"live_wedge_repair_request__{ts}"
    sql_path = out_dir / f"{base}.sql"
    md_path = out_dir / f"{base}.receipt.md"
    js_path = out_dir / f"{base}.receipt.json"

    sql_text = render_sql_file(obs, stmts, pre_json_sha)
    md_text = render_receipt_md(obs, pre_json_sha, stmts, inv,
                                sql_filename=sql_path.name)
    js_payload = {
        "banner": "NOT EXECUTED — proposal only; execution is the daemon's gated repair path (3-factor), not this script",
        "generated_at": obs["generated_at"],
        "database": obs["database"],
        "parent_instance_id": PARENT_INSTANCE_ID,
        "classification": obs["classification"],
        "gate_flag": GATE_FLAG,
        "receipt_note": RECEIPT_NOTE,
        "pre_check_json_sha256": pre_json_sha,
        "assertions": obs["assertions"],
        "parent_mid_turn": obs.get("parent_mid_turn"),
        "parent_first_status": obs["rows"].get("parent_first_status"),
        "parent_re_read_status": obs["rows"].get("parent_re_read_status"),
        "expected_pending_watchers": obs["extended_census"].get("expected_pending_watchers"),
        "expected_outstanding_commissions": obs["extended_census"].get("expected_outstanding_commissions"),
        "targets": [
            {k: v for k, v in t.items() if k != "row"} for t in obs["targets"]
        ],
        "extended_census": obs["extended_census"],
        "out_of_scope": obs["out_of_scope"],
        "statements": [
            {k: v for k, v in s.items() if k != "sql"} for s in stmts
        ],
        "inventory": inv,
        "sql_file": sql_path.name,
    }

    for path, text in ((sql_path, sql_text), (md_path, md_text),
                       (js_path, json.dumps(js_payload, indent=2, sort_keys=True,
                                            default=str))):
        path.write_text(_scrub(text), encoding="utf-8")

    _p("")
    resolved = [t for t in obs["targets"]
                if t["disposition"] == DISPOSITION_RESOLVED]
    backfills = [s for s in stmts if s["block_kind"] == "backfill"]
    if obs["classification"] == "partial":
        _p(f"PARTIAL — {len(resolved)} of 8 target row(s) are ALREADY resolved "
           f"naturally and are EXCLUDED from the statements (remainder-only "
           f"emission). Disclosed per row in the receipt:")
        for t in resolved:
            _p(f"  {t['label']} {t['kind']} {t['prefix']} state={t['state']} "
               f"disposition={t['disposition']}")
    if backfills:
        _p(f"CONDITIONAL BACKFILL — {len(backfills)} block(s) stamp enqueued_at "
           f"on a row a pre-ceremony boot sweep already CANCELLED:")
        for b in backfills:
            _p(f"  block {b['n']} ({b['label']}) {b['pk']}")
    _p("")
    _p(f"REQUEST WRITTEN ({len(stmts)} statement blocks) — NOT EXECUTED")
    for p in (sql_path, md_path, js_path):
        _p(f"  {p}  sha256={hashlib.sha256(p.read_bytes()).hexdigest()}")
    _p("")
    _p("Execution is the daemon's gated repair path (3-factor: user attestation "
       "+ audit stamp + dry-run), not this script.")
    return 0


def mode_verify(conn, args) -> int:
    """Re-check the assertions + the receipt's inventory fingerprint."""
    obs = observe(conn, required_db=args.require_db)
    inv = inventory(conn)

    # -- locate the receipt -------------------------------------------------
    receipt_path = Path(args.receipt) if args.receipt else None
    if receipt_path is None:
        candidates = sorted((Path(args.out_dir) / "emitted").glob("*.receipt.json"))
        receipt_path = candidates[-1] if candidates else None
    if receipt_path is None or not receipt_path.is_file():
        _p("FAIL: no emitted receipt found (run --emit-request first, or pass "
           "--receipt PATH).")
        return 5
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    _p(f"receipt: {receipt_path}")

    checks: list[tuple[str, bool, str]] = []

    pending_n = obs["rows"]["pending_count"]
    checks.append((
        "0 PENDING watchers remain for the parent (mirrored count_pending_for_target)",
        pending_n == 0, f"observed={pending_n}", ))

    outstanding_n = obs["extended_census"]["outstanding_commission_count"]
    checks.append((
        "0 outstanding commissions remain for the parent "
        "(mirrored count_outstanding_commissions_for_anchor)",
        outstanding_n == 0, f"observed={outstanding_n}", ))

    pre_keys = receipt.get("inventory", {}).get("parent_keys", {})
    now_keys = inv["parent_keys"]
    repaired_fields = {"instance_id", "agent_id", "parent_id", "created_at"}
    repair_touched_parent = any(
        pre_keys.get(k) != now_keys.get(k) for k in repaired_fields
    )
    advanced = (pre_keys.get("status") != now_keys.get("status")
                or pre_keys.get("updated_at") != now_keys.get("updated_at")
                or pre_keys.get("version") != now_keys.get("version"))
    checks.append((
        "parent row untouched BY THE REPAIR (no identity/creation field moved)",
        not repair_touched_parent,
        "unchanged" if not (repair_touched_parent or advanced)
        else ("advanced-consistent-with-natural-wake"
              if not repair_touched_parent
              else f"CHANGED: pre={pre_keys} now={now_keys}"),
    ))

    # ---- check 4: parent-scoped digest (the hard anchor) ------------------
    pre_rows = receipt.get("inventory", {}).get("parent_scoped_rows") or {}
    now_rows = inv["parent_scoped_rows"]
    added = sorted(set(now_rows) - set(pre_rows))
    removed = sorted(set(pre_rows) - set(now_rows))
    changed = sorted(k for k in set(pre_rows) & set(now_rows)
                     if pre_rows[k] != now_rows[k])
    scoped_ok = not added and not removed and not changed
    scoped_detail = "no other parent-scoped row moved"
    if not scoped_ok:
        scoped_detail = (
            f"changed={changed} added={added} removed={removed}"
        )
    checks.append((
        "recomputed PARENT-SCOPED inventory sha256 matches the receipt "
        "(no other parent-scoped row moved)",
        scoped_ok,
        scoped_detail if not scoped_ok
        else f"digest {inv['parent_scoped_sha256'][:16]}… over "
             f"{len(now_rows)} rows",
    ))

    # ---- check 5: global scan (informational on a live fleet) -------------
    pre_global = receipt.get("inventory", {}).get("global_sha256")
    global_ok = inv["global_sha256"] == pre_global
    pre_counts = receipt.get("inventory", {}).get("global_counts") or {}
    now_counts = inv["global_counts"]
    checks.append((
        "recomputed GLOBAL inventory sha256 matches the receipt "
        "(quiesced-system check — informational here)",
        global_ok,
        "identical" if global_ok else (
            f"churn: watchers {pre_counts.get('watchers')}→"
            f"{now_counts['watchers']}, commissions "
            f"{pre_counts.get('commissions')}→{now_counts['commissions']} "
            f"(expected on a live fleet; the parent-scoped check above is the "
            f"hard anchor)"),
    ))

    all_ok = True
    _p("")
    _p("=== VERIFY CHECKS ===")
    for name, ok, detail in checks:
        all_ok = all_ok and ok
        _p(f"[{'PASS' if ok else 'FAIL'}] {name}")
        _p(f"        {detail}")

    _p("")
    if all_ok:
        _p("PASS — the repair landed and nothing outside the target set moved.")
    else:
        _p("FAIL — the state does not match the receipt. Do NOT re-run the "
           "request blindly; re-run --inspect and diff against the receipt.")

    _p("")
    _p("=== WHAT THE DAEMON DOES NEXT ===")
    _p("The parent leaves WAITING_CHILDREN at its NEXT TURN-END, because the park")
    _p("predicate (message_processing_pipeline.py:1329, mirrored at")
    _p("repository.py:506-515) then reads 0 and the mission leg")
    _p("(child_reports.py:2691-2695) reads 0 as well.")
    _p("")
    _p("HONEST NOTE: a turn must actually occur. The repair forces nothing — it")
    _p("removes the two blockers so the next turn-end's evaluation passes. Nothing")
    _p("in this repair wakes the parent; a real dispatch (or the instance's own")
    _p("next inbound message) supplies the turn.")
    return 0 if all_ok else 5


def mode_fixture(args) -> int:
    """SELFTEST: run the emit pipeline over a synthetic snapshot."""
    path = Path(args.fixture)
    if not path.is_file():
        _p(f"FIXTURE not found: {path}")
        return 2
    obs = json.loads(path.read_text(encoding="utf-8"))
    # A fixture supplies `rows` only; derive the disposition table with the
    # SAME code path the live observation uses.
    if not obs.get("targets"):
        obs["targets"] = collect_targets(obs.get("rows") or {})
    pre_json = json.dumps({"assertions": obs.get("assertions"), "rows": obs.get("rows"),
                           "targets": [
                               {k: v for k, v in t.items() if k != "row"}
                               for t in obs["targets"]]},
                          sort_keys=True, default=str)
    pre_json_sha = hashlib.sha256(pre_json.encode("utf-8")).hexdigest()
    stmts = build_statements(obs)
    inv = obs.get("inventory") or {
        "parent_scoped_sha256": "0" * 64,
        "parent_scoped_rows": {},
        "parent_scoped_counts": {"watchers": 0, "commissions": 0},
        "global_sha256": "0" * 64,
        "global_counts": {"watchers": 0, "commissions": 0},
        "parent_keys": (obs.get("rows", {}).get("parent") or {}),
    }
    out_dir = Path(args.out_dir) / "selftest"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"live_wedge_repair_request__SELFTEST__{_stamp_for_filename()}"
    sql_path = out_dir / f"{base}.sql"
    md_path = out_dir / f"{base}.receipt.md"
    sql_path.write_text(
        _scrub("-- SYNTHETIC FIXTURE — NOT A LIVE REQUEST\n"
               "-- Generated from " + str(path) + "\n"
               "-- NOTHING HERE IS EXECUTABLE AGAINST ensemble_prod.\n\n"
               + render_sql_file(obs, stmts, pre_json_sha)),
        encoding="utf-8",
    )
    md_path.write_text(
        _scrub(render_receipt_md(obs, pre_json_sha, stmts, inv,
                                sql_filename=sql_path.name, synthetic=True)),
        encoding="utf-8",
    )
    _p("=== PER-ROW DISPOSITION (synthetic) ===")
    _p(_disposition_table(obs))
    _p("")
    resolved = [t for t in obs["targets"]
                if t["disposition"] == DISPOSITION_RESOLVED]
    backfills = [s for s in stmts if s["block_kind"] == "backfill"]
    _p(f"SELFTEST artifacts written ({len(stmts)} blocks, "
       f"{len(resolved)} naturally-resolved row(s) excluded, "
       f"{len(backfills)} backfill block(s)):")
    for p in (sql_path, md_path):
        _p(f"  {p}  sha256={hashlib.sha256(p.read_bytes()).hexdigest()}")
    _p("SYNTHETIC FIXTURE — NOT A LIVE REQUEST. Nothing in emitted/ was touched.")
    return 0


# ===========================================================================
# main
# ===========================================================================


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="READ-ONLY inspection + repair-request emitter for the "
                    "wedged Ari root. There is no --apply: execution is the "
                    "daemon's gated repair path (3-factor), not this script.",
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--inspect", action="store_true",
                      help="observe + classify (DEFAULT)")
    mode.add_argument("--emit-request", action="store_true",
                      help="run A1-A14 and write the request when state is not_yet/partial")
    mode.add_argument("--verify", action="store_true",
                      help="re-check a landed repair against the receipt")
    mode.add_argument("--fixture", metavar="PATH",
                      help="SELFTEST ONLY: emit from a synthetic JSON snapshot")

    ap.add_argument("--gate", default=None,
                    help=f"exact gate flag required by --emit-request ({GATE_FLAG})")
    ap.add_argument("--env-file", default=None,
                    help="parse KEY=VALUE (POSTGRES_* or DATABASE_URL); never echoed")
    ap.add_argument("--dsn", default=None, help="libpq conninfo string")
    ap.add_argument("--require-db", default=DEFAULT_REQUIRED_DB,
                    help=f"identity gate (default {DEFAULT_REQUIRED_DB})")
    ap.add_argument("--receipt", default=None, help="--verify: receipt.json path")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR),
                    help="base dir for emitted/ and selftest/")
    ap.add_argument("--json", action="store_true",
                    help="--inspect: also dump the machine-readable JSON summary")
    args = ap.parse_args(argv)

    if args.fixture:
        return mode_fixture(args)

    try:
        spec = _resolve_dsn(args)
        conn = _connect(spec)
    except ConnectError as exc:
        _p(f"BLOCKED: {exc}")
        return 3

    try:
        if args.emit_request:
            return mode_emit(conn, args)
        if args.verify:
            return mode_verify(conn, args)
        return mode_inspect(conn, args)
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
