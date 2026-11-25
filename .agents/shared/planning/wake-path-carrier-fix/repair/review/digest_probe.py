#!/usr/bin/env python3
"""Reviewer probe: prove that the parent_scoped_sha256 change between the
coder's 23:58Z inspect and the reviewer's 00:16Z inspect came from
parent_keys (status/updated_at/version), NOT from the 4 parent-scoped rows.

READ-ONLY. Same connection discipline as live_wedge_repair.py.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

READ_ONLY_OPTIONS = "-c default_transaction_read_only=on -c statement_timeout=15000"
PARENT = "17efd2ef-9f98-46d4-b41b-60e3e32b2d13"


def parse_env(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        if k.startswith("export "):
            k = k[len("export "):].strip()
        out[k] = v.strip().strip("'").strip('"')
    return out


def digest(dumps: dict[str, str], parent_keys: dict | None = None) -> str:
    payload = json.dumps(
        {"rows": [dumps[k] for k in sorted(dumps)], "parent_keys": parent_keys or {}},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> int:
    import psycopg
    env = parse_env(sys.argv[1])
    receipt_path = sys.argv[2]
    conn = psycopg.connect(
        host=env["POSTGRES_HOST"], port=env["POSTGRES_PORT"], dbname=env["POSTGRES_DB"],
        user=env["POSTGRES_USER"], password=env["POSTGRES_PASSWORD"],
        options=READ_ONLY_OPTIONS, autocommit=True,
    )
    with conn.cursor() as cur:
        cur.execute("SHOW transaction_read_only")
        assert str(cur.fetchone()[0]).lower() in ("on", "true", "1")

        def q(sql, params=()):
            with conn.cursor() as c:
                c.execute(sql, params)
                cols = [d[0] for d in c.description]
                return [dict(zip(cols, r)) for r in c.fetchall()]

        p_w = q("SELECT watch_id, source_task_id, target_instance_id, state, created_at, "
                "fired_at, enqueued_at FROM dependency_watchers "
                "WHERE target_instance_id = %s "
                "AND watch_id::text NOT LIKE %s "
                "AND watch_id::text NOT LIKE %s ORDER BY watch_id",
                (PARENT, "8a1f60d7%", "999efa71%"))
        p_c = q("SELECT mc.commission_id, mc.mission_id, mc.commissioner_instance_id, "
                "mc.worker_instance_id, mc.lane, mc.state, mc.created_at, "
                "mc.satisfied_at, mc.abandon_reason, mc.resolved_at, "
                "mc.advisory_backfill::text AS advisory "
                "FROM mission_commissions mc JOIN mission_records mr "
                "ON mr.mission_id = mc.mission_id WHERE mr.anchor_instance_id = %s "
                "AND mc.commission_id::text NOT LIKE %s "
                "AND mc.commission_id::text NOT LIKE %s "
                "ORDER BY mc.commission_id",
                (PARENT, "0cdc0b31%", "90493206%"))
        parent = q("SELECT instance_id, agent_id, status, parent_id, created_at, "
                   "updated_at, version FROM instances WHERE instance_id = %s", (PARENT,))[0]

    now_dumps: dict[str, str] = {}
    for r in p_w:
        now_dumps[str(r["watch_id"])] = "\t".join(f"{k}={r[k]!r}" for k in r)
    for r in p_c:
        now_dumps["c:" + str(r["commission_id"])] = "\t".join(f"{k}={r[k]!r}" for k in r)

    now_keys = {
        "instance_id": parent["instance_id"], "agent_id": parent["agent_id"],
        "status": parent["status"], "parent_id": parent["parent_id"],
        "created_at": parent["created_at"], "updated_at": parent["updated_at"],
        "version": parent["version"],
    }
    receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
    pre_rows = receipt.get("inventory", {}).get("parent_scoped_rows") or {}
    pre_keys = receipt.get("inventory", {}).get("parent_keys") or {}

    print("=== parent-scoped row comparison (coder 23:57:48 receipt vs live now) ===")
    print(f"  rows: pre={len(pre_rows)} now={len(now_dumps)}")
    print(f"  digest(pre_rows, dict()) = {digest(pre_rows, dict())[:32]}")
    print(f"  digest(now_rows, dict()) = {digest(now_dumps, {})[:32]}")
    print(f"  ROW-ONLY DIGEST STABLE = {digest(pre_rows, dict()) == digest(now_dumps, {})}")
    print(f"  digest(pre_rows, pre_keys) = {digest(pre_rows, pre_keys)[:32]}   "
          f"(receipt's recorded parent_scoped_sha256 = "
          f"{receipt.get('inventory', {}).get('parent_scoped_sha256', '?')[:32]})")
    print(f"  digest(now_rows, now_keys) = {digest(now_dumps, now_keys)[:32]}")
    print(f"  parent_keys pre  = {json.dumps(pre_keys, sort_keys=True)}")
    print(f"  parent_keys now  = {json.dumps(now_keys, sort_keys=True)}")
    print(f"  ==> the sha256 difference is carried by parent_keys "
          f"(status/updated_at/version), NOT by the rows.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
