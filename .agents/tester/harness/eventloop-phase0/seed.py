"""Deterministic prod-shaped fixture for the eventloop-phase0 harness.

NOT SHIPPED — test-support only.  Seeded DIRECTLY against the scratch
engine (SQLAlchemy Core bulk inserts through the daemon's real table
metadata) after the daemon's ``create_all`` has materialised the schema.

SHAPE (full profile, prod-shaped where feasible — the accepted census
(f) inventory; ``smoke`` divides every count by 10):

* ~3.3k instances — 150+ roots with a deep/wide mix of subtrees
  (branching 2-3, depth 2-5) plus a few extra-deep chains (depth 10)
  so the BFS level count is exercised; ~85% terminal-ish, plus a live
  population for the missions page and the defer-gate witnesses;
* ~12 KB-agent rows (experiencer / kb-importer) — ``exclude_kb=true``
  must actually exclude something;
* ~4.0k JobItems mostly done (``admission_state='done'`` with a
  ``terminal_reason`` mix) tied to REAL instance ids;
* ~1.25k JobItems with ORPHAN ``instance_id`` values (no instances
  row) — the ``_lookup_instance`` degradation population;
* a few active/queued JobItems so the badge's active leg is non-empty;
* ~15 defer-gate witnesses: paused instances with a ``task`` row
  (``status='paused'``, ``is_deferred=false``) and running instances
  with a ``running`` task row — the ``/api/queues/defer-blocked``
  holder population.

DETERMINISM: fixed seed (``SEED``) + stable id prefixes — rebuilding
on a fresh scratch database reproduces byte-identical row sets.  The
run is idempotent on a fresh DB by construction (every run provisions
a NEW scratch database and drops it at teardown).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

SEED = 20261029

#: agent population (agent_id → agent_dir); mirrors the live fleet mix.
AGENTS: tuple[tuple[str, str], ...] = (
    ("worker", "agents/worker"),
    ("coder", "agents/coder"),
    ("tester", "agents/tester"),
    ("leader", "agents/leader"),
    ("jober", "agents/jober"),
    ("ari", "agents/ari"),
    ("giter", "agents/giter"),
    ("planner", "agents/planner"),
)
KB_AGENTS: tuple[tuple[str, str], ...] = (
    ("experiencer", "agents/experiencer"),
    ("kb-importer", "agents/kb-importer"),
)

@dataclass
class SeedScale:
    """The size knobs; ``smoke`` divides the full-profile counts by 10."""

    n_deep_roots: int = 60          # branching 2, depth 3   → ~15 rows each
    n_wide_roots: int = 40          # branching 3, depth 3   → ~40 rows each
    n_flat_roots: int = 40          # branching 3, depth 2   → ~13 rows each
    n_chain_roots: int = 10         # branching 1, depth 10  → deep level count
    n_loose: int = 150              # standalone leaves (no children)
    n_running: int = 60             # live population (missions: processing)
    n_paused: int = 20              # missions: paused + defer witnesses
    n_orphan_jobs: int = 1250       # JobItems with orphan instance_ids
    n_done_jobs: int = 4000         # done JobItems on real instances
    n_active_jobs: int = 30         # admission_state='active'
    n_queued_jobs: int = 50         # admission_state='queued'
    n_kb: int = 12                  # KB rows exclude_kb must exclude

    @classmethod
    def smoke(cls) -> "SeedScale":
        """Every path exercised at ~1/10th scale."""
        return cls(
            n_deep_roots=6,
            n_wide_roots=4,
            n_flat_roots=4,
            n_chain_roots=2,
            n_loose=15,
            n_running=6,
            n_paused=3,
            n_orphan_jobs=125,
            n_done_jobs=400,
            n_active_jobs=3,
            n_queued_jobs=5,
            n_kb=2,
        )


@dataclass
class SeedResult:
    """What actually landed (the N5 expected-count fence input)."""

    instances: int = 0
    roots: int = 0
    max_depth: int = 0
    kb_rows: int = 0
    jobitems_done: int = 0
    jobitems_orphan: int = 0
    jobitems_active: int = 0
    jobitems_queued: int = 0
    tasks_witness: int = 0
    paused_instances: int = 0
    running_instances: int = 0
    instance_ids: list[str] = field(default_factory=list)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def build_tree_plan(scale: SeedScale, rng: random.Random) -> tuple[list[dict], int]:
    """Plan the instance rows (parents before children, stable ids)."""
    now = datetime.now(timezone.utc)
    rows: list[dict] = []
    max_depth = 0

    def add(iid: str, agent_id: str, parent: str | None, status: str, ts: datetime,
            source: str = "api", project: str | None = None) -> None:
        agent_dir = f"agents/{agent_id}"
        rows.append({
            "instance_id": iid,
            "project_id": project,
            "agent_id": agent_id,
            "agent_dir": agent_dir,
            "agent_name": agent_id,
            "parent_id": parent,
            "status": status,
            "metadata": {"title": f"fixture {iid}"} if rng.random() < 0.3 else {},
            "version": 1,
            "last_activity_at": ts,
            "created_at": _iso(ts - timedelta(minutes=rng.random() * 60)),
            "updated_at": _iso(ts),
            "paused_at": _iso(ts) if status == "paused" else None,
            "source_type": source,
        })

    def subtree(root_iid: str, branching: int, depth: int, ts: datetime) -> int:
        """BFS-build one subtree; returns the number of rows added."""
        nonlocal max_depth
        counter = 0
        level = [root_iid]
        depth_seen = 0
        add(root_iid, rng.choice(AGENTS)[0], None, "completed", ts)
        counter += 1
        while level and depth_seen < depth:
            nxt: list[str] = []
            for parent in level:
                for k in range(branching):
                    counter += 1
                    iid = f"{root_iid}-d{depth_seen}-c{k}-{counter}"
                    add(iid, rng.choice(AGENTS)[0], parent, "completed", ts)
                    nxt.append(iid)
            level = nxt
            depth_seen += 1
            max_depth = max(max_depth, depth_seen)
        return counter

    seq = 0
    for kind, count, branching, depth in (
        ("deep", scale.n_deep_roots, 2, 3),
        ("wide", scale.n_wide_roots, 3, 3),
        ("flat", scale.n_flat_roots, 3, 2),
        ("chain", scale.n_chain_roots, 1, 10),
    ):
        for i in range(count):
            seq += 1
            ts = now - timedelta(days=rng.random() * 28)
            subtree(f"fx-{kind}-{seq:05d}", branching, depth, ts)

    # Loose leaves — flat-poll padding (mixed statuses, mostly idle).
    for i in range(scale.n_loose):
        seq += 1
        add(
            f"fx-loose-{seq:05d}",
            rng.choice(AGENTS)[0],
            None,
            rng.choice(["idle", "completed", "terminated", "error", "waiting"]),
            now - timedelta(days=rng.random() * 28),
            source=rng.choice(["api", "telegram", "discord", "scheduler"]),
        )

    # KB rows (roots — exclude_kb must exclude them from the page).
    for i in range(scale.n_kb):
        agent_id, agent_dir = KB_AGENTS[i % len(KB_AGENTS)]
        seq += 1
        rows.append({
            "instance_id": f"fx-kb-{seq:05d}",
            "project_id": None,
            "agent_id": agent_id,
            "agent_dir": agent_dir,
            "agent_name": agent_id,
            "parent_id": None,
            "status": "idle",
            "metadata": {},
            "version": 1,
            "last_activity_at": now,
            "created_at": _iso(now),
            "updated_at": _iso(now),
            "paused_at": None,
            "source_type": "api",
        })

    # Live population — RECENT timestamps so the missions page
    # (last_activity DESC) surfaces them on page 1.
    for i in range(scale.n_running):
        seq += 1
        add(
            f"fx-run-{seq:05d}",
            rng.choice(AGENTS)[0],
            None,
            rng.choice(["running", "idle", "waiting", "queued"]),
            now - timedelta(minutes=rng.random() * 30),
        )
    for i in range(scale.n_paused):
        seq += 1
        add(f"fx-paused-{seq:05d}", rng.choice(AGENTS)[0], None, "paused",
            now - timedelta(minutes=rng.random() * 30))

    return rows, max_depth


def seed(engine, scale: SeedScale) -> SeedResult:
    """Bulk-insert the fixture through the daemon's real table metadata."""
    from sqlalchemy import insert
    from daemon.repositories.instance.models import Instance, InstanceHierarchy
    from daemon.repositories.task.models import Task
    from daemon.repositories.job_queue.models import JobItem

    rng = random.Random(SEED)
    result = SeedResult()

    rows, max_depth = build_tree_plan(scale, rng)
    result.max_depth = max_depth
    result.kb_rows = sum(1 for r in rows if r["agent_id"] in {a for a, _ in KB_AGENTS})
    result.paused_instances = sum(1 for r in rows if r["status"] == "paused")
    result.running_instances = sum(
        1 for r in rows if r["status"] in {"running", "idle", "waiting", "queued"}
    )
    result.roots = sum(1 for r in rows if r["parent_id"] is None)

    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(insert(Instance), rows)
        # The permanent parent_id record is what every display path walks;
        # mirror it into the transient working set for shape parity.
        conn.execute(
            insert(InstanceHierarchy),
            [
                {
                    "parent_id": r["parent_id"],
                    "child_id": r["instance_id"],
                    "created_at": r["created_at"],
                }
                for r in rows
                if r["parent_id"] is not None
            ],
        )

        # ── JobItems ────────────────────────────────────────────────
        real_ids = [r["instance_id"] for r in rows]
        rng.shuffle(real_ids)

        done_rows = []
        terminal_mix = ["completed", "settled", "failed", "cancelled"]
        for i in range(scale.n_done_jobs):
            ts = now - timedelta(days=rng.random() * 28)
            done_rows.append({
                "job_id": f"fxj-done-{i:06d}",
                "agent_id": rng.choice(AGENTS)[0],
                "agent_dir": rng.choice(AGENTS)[1],
                "message": f"fixture done job {i}",
                "source": "api",
                "project_id": None,
                "queue_id": None,
                "priority": 5,
                "admission_state": "done",
                "created_at": _iso(ts),
                "instance_id": real_ids[i % len(real_ids)],
                "job_metadata": {},
                "deleted_at": None,
                "job_type": rng.choice(["task", "message"]),
                "retry_count": 0,
                "max_retries": None,
                "idempotency_key": None,
                "next_retry_at": None,
                "failed_at": None,
                "terminal_reason": rng.choice(terminal_mix),
                "version": 0,
            })

        orphan_rows = []
        for i in range(scale.n_orphan_jobs):
            ts = now - timedelta(days=rng.random() * 28)
            orphan_rows.append({
                "job_id": f"fxj-orphan-{i:06d}",
                "agent_id": rng.choice(AGENTS)[0],
                "agent_dir": rng.choice(AGENTS)[1],
                "message": f"fixture orphan job {i}",
                "source": "api",
                "project_id": None,
                "queue_id": None,
                "priority": 5,
                "admission_state": "done",
                "created_at": _iso(ts),
                "instance_id": f"fx-orphan-inst-{i:06d}",  # NO instances row
                "job_metadata": {},
                "deleted_at": None,
                "job_type": rng.choice(["task", "message"]),
                "retry_count": 0,
                "max_retries": None,
                "idempotency_key": None,
                "next_retry_at": None,
                "failed_at": None,
                "terminal_reason": rng.choice(terminal_mix),
                "version": 0,
            })

        live_rows = []
        live_ids = [r["instance_id"] for r in rows if r["status"] in
                    {"running", "idle", "waiting", "queued", "paused"}]
        for i in range(scale.n_active_jobs + scale.n_queued_jobs):
            ts = now - timedelta(hours=rng.random() * 12)
            live_rows.append({
                "job_id": f"fxj-live-{i:06d}",
                "agent_id": rng.choice(AGENTS)[0],
                "agent_dir": rng.choice(AGENTS)[1],
                "message": f"fixture live job {i}",
                "source": "api",
                "project_id": None,
                "queue_id": None,
                "priority": 5,
                "admission_state": "active" if i < scale.n_active_jobs else "queued",
                "created_at": _iso(ts),
                "instance_id": live_ids[i % len(live_ids)] if live_ids else None,
                "job_metadata": {},
                "deleted_at": None,
                "job_type": "message",
                "retry_count": 0,
                "max_retries": None,
                "idempotency_key": None,
                "next_retry_at": None,
                "failed_at": None,
                "terminal_reason": None,
                "version": 0,
            })

        conn.execute(insert(JobItem), done_rows + orphan_rows + live_rows)
        result.jobitems_done = len(done_rows)
        result.jobitems_orphan = len(orphan_rows)
        result.jobitems_active = scale.n_active_jobs
        result.jobitems_queued = scale.n_queued_jobs

        # ── defer-gate witness Task rows ────────────────────────────
        # Paused instances (kind=paused holders) + a few running ones
        # (kind=live holders) — the /api/queues/defer-blocked surface.
        witness_rows = []
        paused_ids = [r["instance_id"] for r in rows if r["status"] == "paused"]
        running_ids = [r["instance_id"] for r in rows if r["status"] == "running"]
        holder_ids = paused_ids + running_ids[: max(1, len(running_ids) // 8)]
        for i, iid in enumerate(holder_ids):
            witness_rows.append({
                "work_id": f"fxt-witness-{i:05d}",
                "task_type": "process_message",
                "instance_id": iid,
                "message_id": None,
                "status": "paused" if iid in set(paused_ids) else "running",
                "suspension_reason": None,
                "resume_target_turn_id": None,
                "worker_id": None,
                "retry_count": 0,
                "next_retry_at": None,
                "cancel_requested": False,
                "cancel_requested_at": None,
                "retry_scheduled": False,
                "is_deferred": False,
                "is_background": False,
                "result": None,
                "error": None,
                "created_at": now - timedelta(hours=1),
                "started_at": now - timedelta(minutes=50),
                "completed_at": None,
                "last_heartbeat_at": now - timedelta(minutes=10),
                "auto_continued_at": None,
                "version": 0,
            })
        if witness_rows:
            conn.execute(insert(Task), witness_rows)
        result.tasks_witness = len(witness_rows)

        total = conn.execute(
            __import__("sqlalchemy").text("SELECT count(*) FROM instances")
        ).scalar_one()

    result.instances = int(total)
    result.instance_ids = sorted(r["instance_id"] for r in rows)
    return result
