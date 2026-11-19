#!/usr/bin/env python3
"""classify_selftest.py — OFFLINE proof of the 8-row classifier.

Drives ``observe()`` through a fake read-only connection whose SQL answers are
synthetic. No database is opened, nothing is written outside the console.

Cases:
  1. all 8 stale                    -> not_yet
  2. one commission already abandoned -> partial
  3. one watcher CANCELLED, enqueued_at NULL -> partial + needs_backfill
  4. UNKNOWN extra PENDING watcher  -> unexpected (A8 drift guard names it)
  5. parent mid-wake (`running`)    -> unexpected (A1/A9 strict)
  6. all 8 resolved                 -> already_repaired
  7. one target row missing         -> unexpected

Run:  .venv/bin/python classify_selftest.py
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import live_wedge_repair as L  # noqa: E402

PARENT = L.PARENT_INSTANCE_ID
SONIC = L.SONIC_LEADER_ID
LEADER = L.LEADER_INSTANCE_ID


def _w(wid, task, child, state="PENDING", enq=None, fired=None):
    return {
        "watch_id": wid, "source_task_id": task, "target_instance_id": PARENT,
        "state": state, "created_at": "2026-11-17T18:06:08+00:00",
        "fired_at": fired, "enqueued_at": enq, "child_id": child,
        "kind": "nudge_complete", "message_id": None,
        "payload_mission_id": L.MISSION_OBSERVED,
    }


def _c(cid, worker, state="outstanding"):
    return {
        "commission_id": cid, "mission_id": L.MISSION_OBSERVED,
        "commissioner_instance_id": PARENT, "worker_instance_id": worker,
        "work_id": None, "lane": "instance_nudge", "state": state,
        "created_at": "2026-11-17T18:06:08+00:00",
        "resolved_at": None, "satisfied_at": None,
        "abandon_reason": None, "advisory_backfill": False,
    }


def _inst(iid, status, agent="ari"):
    return {"instance_id": iid, "agent_id": agent, "agent_name": agent,
            "status": status, "parent_id": None,
            "created_at": "2026-11-14T21:54:59+00:00",
            "updated_at": "2026-11-18T00:04:47+00:00", "version": 98}


def base_state():
    parent_row = _inst(PARENT, "waiting_children")
    return {
        "parent": parent_row,
        "watch": {
            "8a1f60d7": [_w("deadbeef-0000-4000-8000-000000000001", "39320", SONIC)],
            "999efa71": [_w("deadbeef-0000-4000-8000-000000000002", "39424", PARENT)],
            "5602e91d": [_w("deadbeef-0000-4000-8000-000000000005", "39564", LEADER)],
            "c83e12a2": [_w("deadbeef-0000-4000-8000-000000000006", "39564", LEADER)],
        },
        "comm": {
            "0cdc0b31": [_c("feedface-0000-4000-8000-000000000003", SONIC)],
            "90493206": [_c("feedface-0000-4000-8000-000000000004", PARENT)],
            "06001f88": [_c("feedface-0000-4000-8000-000000000007", LEADER)],
            "a1561f19": [_c("feedface-0000-4000-8000-000000000008", LEADER)],
        },
        "workers": {
            PARENT: parent_row,  # SAME object: mutating the parent mutates both
            SONIC: _inst(SONIC, "completed", "sonic-leader"),
            LEADER: _inst(LEADER, "waiting_children", "leader"),
        },
        "tasks": {
            "39320": {"id": 39320, "instance_id": PARENT, "status": "completed",
                      "task_type": "process_message", "created_at": "x", "completed_at": "y"},
            "39424": {"id": 39424, "instance_id": PARENT, "status": "completed",
                      "task_type": "process_message", "created_at": "x", "completed_at": "y"},
            "39564": {"id": 39564, "instance_id": PARENT, "status": "completed",
                      "task_type": "process_message", "created_at": "x", "completed_at": "y"},
        },
        "extra_pending": [],
        "extra_outstanding": [],
    }


class _Cur:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self._rows = self.conn.answer(sql, params or ())

    @property
    def description(self):
        if not self._rows:
            return None
        return [(k,) for k in self._rows[0]]

    def fetchall(self):
        return [tuple(r.values()) for r in self._rows]

    def fetchone(self):
        return tuple(self._rows[0].values()) if self._rows else None


class FakeConn:
    def __init__(self, state):
        self.state = state

    def cursor(self):
        return _Cur(self)

    def answer(self, sql, params):
        st = self.state
        if "current_database()" in sql and "current_user" in sql:
            return [{"db": "ensemble_prod", "usr": "fake"}]
        if "FROM dependency_watchers WHERE watch_id::text LIKE" in sql:
            return st["watch"].get(params[0].rstrip("%"), [])
        if "FROM mission_commissions WHERE commission_id::text LIKE" in sql:
            return st["comm"].get(params[0].rstrip("%"), [])
        if "FROM instances WHERE instance_id" in sql:
            row = st["workers"].get(params[0])
            return [row] if row else []
        if "FROM task WHERE id" in sql:
            row = st["tasks"].get(str(params[0]))
            return [row] if row else []
        if "count(*)::int AS n FROM dependency_watchers" in sql:
            return [{"n": sum(1 for v in st["watch"].values()
                              for r in v if r["state"] == "PENDING")}]
        if "count(*)::int AS n FROM mission_commissions" in sql:
            return [{"n": sum(1 for v in st["comm"].values()
                              for r in v if r["state"] == "outstanding")}]
        if "FROM dependency_watchers WHERE target_instance_id" in sql \
                and "state = 'PENDING'" in sql:
            rows = [r for v in st["watch"].values() for r in v
                    if r["state"] == "PENDING" and r["target_instance_id"] == PARENT]
            return rows + list(st["extra_pending"])
        if "JOIN mission_records mr" in sql and "mc.state = 'outstanding'" in sql \
                and "ORDER BY mc.created_at" in sql:
            rows = [r for v in st["comm"].values() for r in v
                    if r["state"] == "outstanding"]
            return rows + list(st["extra_outstanding"])
        if "commissioner_instance_id = %s" in sql:
            return []
        raise AssertionError("fake conn: unknown SQL: " + sql[:90])


def run_case(name, mutate, expect):
    st = base_state()
    mutate(st)
    obs = L.observe(FakeConn(st), required_db="ensemble_prod")
    ok = obs["classification"] == expect
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: "
          f"classification={obs['classification']} (expected {expect})")
    for d in obs["divergences"][:6]:
        print(f"        - {d}")
    t = {x["label"]: x for x in obs["targets"]}
    print("        dispositions: " + ", ".join(
        f"{k}={v['disposition']}" for k, v in t.items()))
    print(f"        needs_backfill: "
          f"{[k for k, v in t.items() if v.get('needs_backfill')]}")
    return ok


def main() -> int:
    results = []

    results.append(run_case("1 all 8 stale", lambda s: None, "not_yet"))

    def m2(s):
        s["comm"]["90493206"][0]["state"] = "abandoned"
    results.append(run_case("2 one commission already abandoned",
                            m2, "partial"))

    def m3(s):
        s["watch"]["999efa71"][0].update(
            state="CANCELLED", fired="2026-11-18T00:10:00+00:00", enqueued_at=None)
    results.append(run_case("3 boot-sweep cancel (needs backfill)", m3, "partial"))

    def m4(s):
        s["extra_pending"] = [_w("feedface-0000-4000-8000-00000000beef",
                                 "39700", LEADER)]
    results.append(run_case("4 UNKNOWN extra PENDING watcher (A8)", m4, "unexpected"))

    def m5(s):
        s["parent"]["status"] = "running"
    results.append(run_case("5 parent mid-wake running (A1/A9)", m5, "unexpected"))

    def m6(s):
        for v in s["watch"].values():
            v[0].update(state="CANCELLED", fired="t", enqueued_at="t")
        for v in s["comm"].values():
            v[0].update(state="abandoned")
        s["parent"]["status"] = "running"
    results.append(run_case("6 all 8 resolved (parent already woke)",
                            m6, "already_repaired"))

    def m7(s):
        s["watch"]["5602e91d"] = []
    results.append(run_case("7 target row missing", m7, "unexpected"))

    print()
    print(f"{sum(results)}/{len(results)} cases PASS")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
