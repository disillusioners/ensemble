#!/usr/bin/env python3
"""R8 census cross-check — READ-ONLY reviewer helper.

Discipline mirrors live_wedge_repair.py exactly:
  * connection options: -c default_transaction_read_only=on -c statement_timeout=15000
  * SHOW transaction_read_only asserted ON before any query
  * identity gate: current_database() must equal ensemble_prod
  * SELECT / SHOW only — no DML of any kind is executed
  * the .env file is parsed in-process; NO credential value is ever printed

Nothing is written outside stdout.
"""
from __future__ import annotations

import sys
from pathlib import Path

READ_ONLY_OPTIONS = "-c default_transaction_read_only=on -c statement_timeout=15000"
PARENT = "17efd2ef-9f98-46d4-b41b-60e3e32b2d13"
EXTRA_WATCHERS = ("5602e91d", "c83e12a2")
EXTRA_COMMISSIONS = ("06001f88", "a1561f19")


def parse_env(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        values[key] = val.strip().strip("'").strip('"')
    return values


def main() -> int:
    import psycopg

    env = parse_env(sys.argv[1])
    conn = psycopg.connect(
        host=env["POSTGRES_HOST"], port=env["POSTGRES_PORT"],
        dbname=env["POSTGRES_DB"], user=env["POSTGRES_USER"],
        password=env["POSTGRES_PASSWORD"],
        options=READ_ONLY_OPTIONS, autocommit=True,
    )
    with conn.cursor() as cur:
        cur.execute("SHOW transaction_read_only")
        ro = cur.fetchone()[0]
        assert str(ro).lower() in ("on", "true", "1"), f"NOT READ-ONLY: {ro}"
        print(f"[identity] current_database()={env['POSTGRES_DB']}  "
              f"transaction_read_only={ro}  (env-file db name only, no DSN)")
        cur.execute("SELECT current_database(), current_user")
        db, usr = cur.fetchone()
        print(f"[identity] server says current_database()={db} current_user={usr}")
        assert db == "ensemble_prod", f"identity gate FAILED: {db}"

        def q(sql, params=()):
            with conn.cursor() as cur2:
                cur2.execute(sql, params)
                cols = [d[0] for d in cur2.description]
                return [dict(zip(cols, r)) for r in cur2.fetchall()]

        # ---- R8 (a): the two extra watchers target the parent -------------
        print("\n=== R8a: extra PENDING watchers — target_instance_id ===")
        rows = q(
            "SELECT watch_id, source_task_id, target_instance_id, state, "
            "created_at, fired_at, enqueued_at, "
            "follow_up_payload->'metadata'->>'child_id' AS child_id "
            "FROM dependency_watchers WHERE watch_id::text LIKE %s "
            "OR watch_id::text LIKE %s ORDER BY watch_id",
            (EXTRA_WATCHERS[0] + "%", EXTRA_WATCHERS[1] + "%"),
        )
        for r in rows:
            ok = r["target_instance_id"] == PARENT
            print(f"  {r['watch_id'][:8]} target={r['target_instance_id']} "
                  f"match_parent={ok} state={r['state']} "
                  f"source_task={r['source_task_id']} child={r['child_id']} "
                  f"created={r['created_at']} fired_at={r['fired_at']} "
                  f"enqueued_at={r['enqueued_at']}")
        print(f"  -> both extras target the parent: "
              f"{all(r['target_instance_id'] == PARENT for r in rows)} "
              f"(rows={len(rows)})")

        # ---- R8 (b): the two extra commissions — commissioner -------------
        print("\n=== R8b: extra outstanding commissions — commissioner ===")
        rows = q(
            "SELECT commission_id, mission_id, commissioner_instance_id, "
            "worker_instance_id, lane, state, created_at, advisory_backfill "
            "FROM mission_commissions WHERE commission_id::text LIKE %s "
            "OR commission_id::text LIKE %s ORDER BY commission_id",
            (EXTRA_COMMISSIONS[0] + "%", EXTRA_COMMISSIONS[1] + "%"),
        )
        for r in rows:
            ok = r["commissioner_instance_id"] == PARENT
            print(f"  {r['commission_id'][:8]} commissioner={r['commissioner_instance_id']} "
                  f"match_parent={ok} worker={r['worker_instance_id']} "
                  f"lane={r['lane']} state={r['state']} created={r['created_at']} "
                  f"advisory_backfill={r['advisory_backfill']}")
        print(f"  -> both extras are commissioned BY the parent: "
              f"{all(r['commissioner_instance_id'] == PARENT for r in rows)} "
              f"(rows={len(rows)})")

        # ---- R4(a): the sweep predicate evaluated live, read-only ---------
        print("\n=== R4a: boot-sweep predicate applied to ALL 4 parent watchers ===")
        rows = q(
            "SELECT w.watch_id, w.source_task_id, w.state, w.created_at, "
            "w.enqueued_at, "
            "t.status AS task_status, "
            "(w.created_at < to_char(now() AT TIME ZONE 'UTC' - interval '30 seconds', "
            "  'YYYY-MM-DD\"T\"HH24:MI:SS.US\"+00:00\"')) "
            "  AS older_than_grace, "
            "(w.source_task_id NOT IN (SELECT CAST(id AS TEXT) FROM task "
            "  WHERE status IN ('running','pending','paused'))) AS orphan_by_predicate "
            "FROM dependency_watchers w "
            "LEFT JOIN task t ON CAST(t.id AS TEXT) = w.source_task_id "
            "WHERE w.target_instance_id = %s AND w.state = 'PENDING' "
            "ORDER BY w.created_at",
            (PARENT,),
        )
        for r in rows:
            would = r["older_than_grace"] and r["orphan_by_predicate"]
            print(f"  {r['watch_id'][:8]} source_task={r['source_task_id']} "
                  f"task_status={r['task_status']} "
                  f"older_than_30s={r['older_than_grace']} "
                  f"orphan_by_predicate={r['orphan_by_predicate']} "
                  f"=> WOULD_BE_CANCELLED_AT_BOOT={would} "
                  f"enqueued_at={r['enqueued_at']}")
        n_would = sum(1 for r in rows
                      if r["older_than_grace"] and r["orphan_by_predicate"])
        print(f"  -> watchers the boot sweep WOULD cancel: {n_would} of {len(rows)}")

        # ---- R4(d): child liveness of the re-arm candidates ---------------
        print("\n=== R4d: child-instance liveness of the sweep-cancelled rows ===")
        rows = q(
            "SELECT DISTINCT w.follow_up_payload->'metadata'->>'child_id' AS child_id, "
            "i.status AS child_status, i.parent_id AS child_parent_id "
            "FROM dependency_watchers w LEFT JOIN instances i "
            "  ON i.instance_id = w.follow_up_payload->'metadata'->>'child_id' "
            "WHERE w.target_instance_id = %s AND w.state = 'PENDING'",
            (PARENT,),
        )
        for r in rows:
            cid = r["child_id"] or ""
            terminal = r["child_status"] in (
                "completed", "terminated", "error", "failed")
            is_parent = cid == PARENT
            print(f"  child={cid[:8]} status={r['child_status']} "
                  f"terminal={terminal} is_the_parent={is_parent} "
                  f"=> RE_ARM_ELIGIBLE_on_pause+resume="
                  f"{(not terminal) and r['child_status'] is not None}")

        # ---- R4(b): the two gates right now -------------------------------
        print("\n=== R4b: the two gates ===")
        pend = q("SELECT count(*)::int AS n FROM dependency_watchers "
                 "WHERE target_instance_id = %s AND state = 'PENDING'", (PARENT,))
        outst = q("SELECT count(*)::int AS n FROM mission_commissions mc "
                  "JOIN mission_records mr ON mr.mission_id = mc.mission_id "
                  "WHERE mr.anchor_instance_id = %s AND mc.state = 'outstanding' "
                  "AND mc.advisory_backfill = false", (PARENT,))
        print(f"  bus gate  count_pending_for_target(parent)          = {pend[0]['n']}")
        print(f"  mission gate count_outstanding_commissions(parent) = {outst[0]['n']}")

        # ---- parent row ---------------------------------------------------
        rows = q("SELECT instance_id, agent_id, status, parent_id, updated_at, "
                 "version FROM instances WHERE instance_id = %s", (PARENT,))
        print("\n=== parent instance row ===")
        for r in rows:
            print(f"  {r}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
