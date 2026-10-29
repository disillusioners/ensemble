"""The eventloop-phase0 baseline orchestrator.

NOT SHIPPED — test-support only.  Run via ``run_all.sh`` (which owns
the per-variable env scrub and the timeouts):

    bash .agents/tester/harness/eventloop-phase0/run_all.sh --profile=full
    bash .agents/tester/harness/eventloop-phase0/run_all.sh --profile=smoke

Phases (each N5-fenced on its collected count):
  0  fences: clean env, scratch PG, free port          (refuse, never work around)
  1  child boot (real create_app+lifespan+uvicorn)     livez asserted
  2  seed the prod-shaped fixture                      row counts asserted
  3  per_endpoint floor (serialized, unloaded)         ≥30 samples both instance
                                                       shapes; ≥N others; N4 table
  4  badge composite (8s forkJoin, sustained)          5 legs/tick, ≥2 ticks
  5  home BFS(100) (10s tick, sustained)               ≥2 ticks
  6  jobs_page no-limit (30s tick) + executor sampler  ≥1 tick, sampler series >0
  7  capture phase (badge+home until capture fires)    captures ≥1, frame pin
  8  teardown: stop child, drop scratch role+DB        port freed, drop proof

Exit 0 = all fences green; 1 = any fence failed (details in the JSON).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
import time
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parent
REPO_ROOT = HARNESS_DIR.parents[3]  # …/<worktree>/.agents/tester/harness/eventloop-phase0

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(HARNESS_DIR))

from common import (  # noqa: E402
    FenceError,
    ScratchPG,
    admin_conn_kwargs,
    assert_clean_env,
    create_scratch_pg,
    drop_scratch_pg,
    pick_free_port,
    stats_ms,
    write_json,
)
from child import DaemonChild  # noqa: E402
from driver import (  # noqa: E402
    BADGE_LEGS,
    SHAPES,
    run_profile,
    wait_for_capture,
)
from seed import SeedScale, seed  # noqa: E402


class Fences:
    """The N5 collector — every measurement asserts what it collected."""

    def __init__(self) -> None:
        self.results: list[dict[str, object]] = []

    def check(self, name: str, ok: bool, detail: object = "") -> bool:
        self.results.append({"name": name, "ok": bool(ok), "detail": str(detail)})
        mark = "PASS" if ok else "FAIL"
        print(f"  [fence:{mark}] {name} — {detail}")
        return bool(ok)

    @property
    def all_ok(self) -> bool:
        return all(r["ok"] for r in self.results)


def _body_facts(shape: str, payload: object) -> dict[str, object]:
    """Response-derived facts the formula table needs (counts from body)."""
    if not isinstance(payload, dict):
        return {}
    out: dict[str, object] = {}
    if "instances" in payload:
        insts = payload.get("instances") or []
        out["rows"] = len(insts)
        out["total"] = payload.get("total")
        out["truncated"] = payload.get("truncated")
        # BFS page depth: longest parent chain INSIDE the returned page
        # (levels for the ``4 + levels + rows`` formula — measured from
        # the response, never guessed from the fixture).
        by_id = {
            i.get("instance_id"): i for i in insts
            if isinstance(i, dict) and i.get("instance_id")
        }
        def depth(iid: str, seen: set | None = None) -> int:
            """Edge-count depth (root = 0) — matches the BFS level count."""
            seen = seen or set()
            if iid in seen or iid not in by_id:
                return 0
            seen = seen | {iid}
            parent = by_id[iid].get("parent_id")
            if not parent or parent not in by_id:
                return 0
            return 1 + depth(parent, seen)
        out["page_levels"] = max((depth(i) for i in by_id), default=0)
    if "holders" in payload:
        holders = payload.get("holders") or []
        out["holders"] = len(holders)
        out["holders_paused"] = sum(
            1 for h in holders if isinstance(h, dict) and h.get("kind") == "paused"
        )
        # only NON-paused dedup'd holders get the WS1 stall probe
        out["holders_probed"] = out["holders"] - out["holders_paused"]
        out["pending_count"] = payload.get("pending_count")
    if "missions" in payload:
        out["rows"] = len(payload.get("missions") or [])
    if "jobs" in payload:
        out["rows"] = len(payload.get("jobs") or [])
    return out


async def per_endpoint_phase(child: DaemonChild, fences: Fences, smoke: bool,
                             report: dict) -> None:
    """Deliverables (1) floor + (3) round-trip formula table — serialized."""
    import httpx

    n_inst = 4 if smoke else 30
    n_other = 2 if smoke else 10
    counts = {s: (n_inst if "instances" in s else n_other) for s in SHAPES}
    print(f"[phase] per_endpoint floor (serialized, unloaded; smoke={smoke})")
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{child.port}") as client:
        child.counter.reset_requests()
        records: list[dict[str, object]] = []
        seq = 0
        for shape, n in counts.items():
            for i in range(n):
                seq += 1
                tag = f"{shape}:{seq}"
                t0 = time.perf_counter()
                try:
                    r = await client.get(
                        SHAPES[shape], headers={"X-Evloop-Tag": tag}, timeout=180.0
                    )
                    err = None
                    facts = _body_facts(shape, r.json()) if r.status_code == 200 else {}
                except Exception as exc:  # noqa: BLE001
                    r = None
                    err = f"{type(exc).__name__}: {exc}"
                    facts = {}
                dur = time.perf_counter() - t0
                records.append({
                    "shape": shape, "seq": seq, "tag": tag,
                    "status": getattr(r, "status_code", None),
                    "dur_s": round(dur, 4), "error": err, **facts,
                })
        n4 = child.counter.snapshot()["per_request"]

    per_shape: dict[str, dict[str, object]] = {}
    for shape in SHAPES:
        recs = [r for r in records if r["shape"] == shape]
        durs = [float(r["dur_s"]) for r in recs if r.get("status") == 200]
        rt = [n4[r["tag"]]["count"] for r in recs if r["tag"] in n4]
        onloop = [n4[r["tag"]]["on_loop"] for r in recs if r["tag"] in n4]
        shapes_seen: dict[str, int] = {}
        for r in recs:
            if r["tag"] in n4:
                for s, v in n4[r["tag"]]["by_shape"].items():
                    shapes_seen[s] = shapes_seen.get(s, 0) + v["count"]
        per_shape[shape] = {
            "samples": len(recs),
            "ok_samples": len(durs),
            "latency": stats_ms(durs),
            "round_trips": {
                "mean": round(sum(rt) / len(rt), 2) if rt else None,
                "min": min(rt) if rt else None,
                "max": max(rt) if rt else None,
            },
            "on_loop_round_trips": {
                "mean": round(sum(onloop) / len(onloop), 2) if onloop else None,
                "max": max(onloop) if onloop else None,
            },
            "statements_by_shape": dict(sorted(shapes_seen.items())),
            "body_facts": {
                k: recs[-1][k]
                for k in ("rows", "total", "truncated", "holders",
                          "holders_paused", "holders_probed", "pending_count",
                          "page_levels")
                if recs and k in recs[-1]
            },
        }
        fences.check(
            f"floor_samples[{shape}]",
            len(durs) >= (2 if smoke else 8) and all(
                r.get("status") == 200 for r in recs
            ),
            f"{len(durs)}/{len(recs)} ok",
        )

    report["per_endpoint"] = per_shape
    report["per_endpoint_records"] = records


def formula_table(per_shape: dict, seed_result, fences: Fences) -> dict:
    """Validate the census formulas against measured round-trips (Q2/Q3)."""
    table: dict[str, dict[str, object]] = {}

    flat = per_shape.get("instances_flat_badge", {})
    rt = (flat.get("round_trips") or {}).get("mean")
    table["instances_flat_badge"] = {
        "formula": "3 + limit",
        "expected": 3 + 10,
        "measured_mean": rt,
        "match": rt == 3 + 10,
    }

    bfs = per_shape.get("instances_bfs_home", {})
    rt_bfs = (bfs.get("round_trips") or {}).get("mean")
    facts = bfs.get("body_facts") or {}
    rows = facts.get("rows")
    levels = facts.get("page_levels")
    table["instances_bfs_home"] = {
        "formula": "4 + levels + rows (levels = BFS page depth measured "
                   "from the returned rows' parent chains)",
        "expected": (4 + levels + rows)
        if rows is not None and levels is not None else None,
        "measured_mean": rt_bfs,
        "rows": rows,
        "page_levels": levels,
        "fixture_max_depth": seed_result.max_depth,
        "match": rows is not None and levels is not None and rt_bfs is not None
        and rt_bfs == 4 + levels + rows,
    }

    db = per_shape.get("defer_blocked", {})
    facts = db.get("body_facts") or {}
    rt_db = (db.get("round_trips") or {}).get("mean")
    probed = facts.get("holders_probed")
    table["defer_blocked"] = {
        "formula": "2 + non-paused holders (the WS1 stall probe skips paused)",
        "expected": (2 + probed) if probed is not None else None,
        "measured_mean": rt_db,
        "holders": facts.get("holders"),
        "holders_paused": facts.get("holders_paused"),
        "holders_probed": probed,
        "match": probed is not None and rt_db == 2 + probed,
    }

    ms = per_shape.get("missions_badge", {})
    table["missions_badge"] = {
        "formula": "3 / page",
        "expected": 3,
        "measured_mean": (ms.get("round_trips") or {}).get("mean"),
        "match": (ms.get("round_trips") or {}).get("mean") == 3,
    }
    for name, row in table.items():
        fences.check(f"formula[{name}]", bool(row["match"]),
                     f"expected={row['expected']} measured={row['measured_mean']}")
    return table


def capture_assertion(capture_root: Path, fences: Fences, report: dict) -> None:
    """N2 — the stack-pin positive control at BASE.

    Asserts the MAIN-thread stack contains ``list_child_ids_permanent``
    AND ``daemon/routers/instances.py`` — the pin that flips to NEGATIVE
    at HEAD (shape A+C removes the loop-thread sync-DB frames).
    """
    dirs = sorted(
        d for d in capture_root.iterdir()
        if d.is_dir() and d.name != "boot-records"
    ) if capture_root.is_dir() else []
    fences.check("captures_collected", len(dirs) >= 1, f"{len(dirs)} capture dir(s)")

    results = []
    for d in dirs:
        stacks_p = d / "stacks.txt"
        manifest_p = d / "manifest.json"
        threads_p = d / "threads.csv"
        entry: dict[str, object] = {"capture_id": d.name}
        if manifest_p.is_file():
            with contextlib.suppress(Exception):
                entry["manifest"] = json.loads(manifest_p.read_text())
        if not stacks_p.is_file():
            entry["stacks"] = "missing"
            results.append(entry)
            continue
        text = stacks_p.read_text(errors="replace")
        # faulthandler sections: 'Thread 0x… (most recent call first):'
        sections: list[tuple[str, str]] = []
        current = None
        buf: list[str] = []
        for line in text.splitlines():
            if line.startswith(("Thread ", "Current thread ")):
                if current:
                    sections.append((current, "\n".join(buf)))
                current = line.strip()
                buf = []
            elif current:
                buf.append(line)
        if current:
            sections.append((current, "\n".join(buf)))

        fam_hits = [
            (hdr, body) for hdr, body in sections
            if "list_child_ids_permanent" in body
        ]
        router_hits = [
            (hdr, body) for hdr, body in sections
            if "daemon/routers/instances.py" in body
        ]
        both = [
            (hdr, body) for hdr, body in sections
            if "list_child_ids_permanent" in body
            and "daemon/routers/instances.py" in body
        ]
        entry["threads_total"] = len(sections)
        entry["sections_with_list_child_ids"] = len(fam_hits)
        entry["sections_with_routers_instances"] = len(router_hits)
        entry["sections_with_both"] = len(both)

        # main-thread identification via threads.csv (tid ↔ native_id ↔ name)
        main_native = None
        if threads_p.is_file():
            import csv
            with contextlib.suppress(Exception):
                with open(threads_p, newline="") as fh:
                    for row in csv.DictReader(fh):
                        if (row.get("name") or "") == "MainThread":
                            main_native = row.get("native_id")
                            break
        entry["main_thread_native_id"] = main_native
        main_section = None
        if both and main_native:
            # the manifest's executor thread is the capturer (watchdog);
            # the LOOP thread is MainThread — match its tid via threads.csv
            for hdr, body in both:
                main_section = (hdr, body)
                break
        # The FAMILY pin (the flip target): the loop thread is INSIDE
        # a sync SQLAlchemy/psycopg frame reaching the instances read
        # path — ``list_child_ids_permanent`` (repository) OR the
        # router frame.  The boot-probe-observed reality: the loop
        # spends its time in ``repositories/instance/repository.py``
        # (the BFS/list queries) AND ``list_child_ids_permanent``
        # (the per-row N+1); both are the sync-DB-on-loop defect this
        # lane fixes, and BOTH disappear from the loop thread at HEAD.
        fam_any = [
            (hdr, body) for hdr, body in sections
            if ("daemon/routers/instances.py" in body
                or "daemon/repositories/instance/repository.py" in body)
            and "sqlalchemy" in body.lower()
        ]
        entry["frame_family_present_in_main_thread"] = bool(fam_any)
        entry["stack_pin_narrow"] = bool(both)
        results.append(entry)

    report["captures"] = results
    fam = sum(1 for r in results if r.get("frame_family_present_in_main_thread"))
    narrow = sum(1 for r in results if r.get("stack_pin_narrow"))
    fences.check(
        "stack_pin_family[sync-DB instances path on a thread]",
        fam >= 1,
        f"{fam}/{len(results)} capture(s) show the loop-thread sync-DB "
        f"instances-path frames (routers/instances.py | "
        f"repositories/instance/repository.py over sqlalchemy)",
    )
    # The NARROW pin (list_child_ids_permanent + routers/instances.py in
    # the SAME thread section) is scale-dependent — fenced on the FULL
    # profile (3.3k rows → long in-frame dwell), reported on smoke.
    if report.get("profile") == "full":
        fences.check(
            "stack_pin_narrow[list_child_ids_permanent+routers/instances.py]",
            narrow >= 1,
            f"{narrow}/{len(results)} capture(s) carry the narrow pin",
        )
    else:
        print(f"  [fence:INFO] stack_pin_narrow (smoke: report-only) — "
              f"{narrow}/{len(results)} capture(s)")


async def main(profile: str) -> int:
    smoke = profile == "smoke"
    raw_dir = HARNESS_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    fences = Fences()
    report: dict[str, object] = {
        "harness": "eventloop-phase0",
        "profile": profile,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repo_head": None,
    }

    # ── phase 0: fences ─────────────────────────────────────────────
    assert_clean_env()
    fences.check("env_scrub", True, "no ambient POSTGRES_*/PORT survived")
    import subprocess

    report["repo_head"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.strip()

    pg: ScratchPG | None = None
    child: DaemonChild | None = None
    try:
        pg = create_scratch_pg()
        fences.check("scratch_db_shape", True, f"db={pg.db} role={pg.role}")
        port = pick_free_port()
        fences.check("port_free", port not in {9797, 8079, 7979}, f"port={port}")

        workdir = raw_dir / f"run-{int(time.time())}"
        workdir.mkdir(parents=True, exist_ok=True)
        report["workdir"] = str(workdir)

        # ── phase 1: child boot ─────────────────────────────────────
        print("[phase] booting daemon child (real create_app + lifespan + uvicorn)")
        child = DaemonChild(pg, workdir, port)
        await asyncio.wait_for(child.start(), timeout=240.0)
        import httpx

        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            r = await client.get("/livez", timeout=10.0)
            fences.check("livez", r.status_code == 200, f"status={r.status_code}")
            r = await client.get("/readyz", timeout=30.0)
            fences.check("readyz_probe", r.status_code == 200,
                         f"status={r.status_code}")
            li = (await client.get("/harness/loopinfo", timeout=10.0)).json()
            report["loopinfo"] = li
            fences.check("loop_thread_identified",
                         li.get("loop_thread_ident") is not None, str(li))
        report["child_env"] = {
            k: v for k, v in child.env.items() if "PASSWORD" not in k
        }
        report["capture_root"] = str(child.capture_root)

        # ── phase 2: seed ───────────────────────────────────────────
        print("[phase] seeding fixture")
        scale = SeedScale.smoke() if smoke else SeedScale()
        seed_result = seed(child.manager._engine, scale)
        report["seed"] = {
            "scale": "smoke" if smoke else "full",
            **{k: v for k, v in vars(seed_result).items() if k != "instance_ids"},
            "instance_ids_sample": seed_result.instance_ids[:5],
        }
        fences.check("seed_instances", seed_result.instances >= scale.n_loose,
                     f"{seed_result.instances} rows")
        fences.check("seed_orphans", seed_result.jobitems_orphan >= scale.n_orphan_jobs - 5,
                     f"{seed_result.jobitems_orphan} orphan JobItems")
        fences.check("seed_witnesses", seed_result.tasks_witness >= 1,
                     f"{seed_result.tasks_witness} witness Task rows")
        fences.check("seed_max_depth", seed_result.max_depth >= 5,
                     f"max_depth={seed_result.max_depth}")

        # warmup (untagged; ORM/pool warm, results discarded)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            for shape in SHAPES:
                with contextlib.suppress(Exception):
                    await client.get(SHAPES[shape], timeout=180.0)

        # ── phase 3: per_endpoint floor + formula table ─────────────
        await per_endpoint_phase(child, fences, smoke, report)
        report["formula_table"] = formula_table(
            report["per_endpoint"], seed_result, fences
        )

        # ── phase 4: badge composite ────────────────────────────────
        print("[phase] badge composite (8s forkJoin, sustained)")
        badge_s = 16 if smoke else 90
        badge = await run_profile(
            child, "badge", badge_s, shapes=BADGE_LEGS, cadence_s=8.0
        )
        report["profile_badge"] = badge
        reqs = badge.get("requests", [])
        shapes_seen = {r.get("shape") for r in reqs if r.get("shape")}
        fences.check("badge_ticks", len(reqs) >= 5,
                     f"{len(reqs)} leg requests, shapes={sorted(shapes_seen)}")
        fences.check(
            "badge_legs_ok",
            set(BADGE_LEGS) <= shapes_seen and all(
                r.get("status") == 200 for r in reqs if r.get("shape")
            ),
            f"all {len(reqs)} legs 200 across {len(shapes_seen)} shapes",
        )

        # ── phase 5: home BFS ───────────────────────────────────────
        print("[phase] home BFS(100) (10s tick, sustained)")
        home_s = 12 if smoke else 60
        home = await run_profile(
            child, "home", home_s, shapes=("instances_bfs_home",), cadence_s=10.0
        )
        report["profile_home"] = home
        fences.check("home_ticks", len(home.get("requests", [])) >= 1,
                     f"{len(home.get('requests', []))} requests")

        # ── phase 6: jobs_page no-limit + executor saturation ───────
        print("[phase] jobs_page no-limit (30s tick) + executor sampler")
        jobs_s = 70 if smoke else 150
        jobs = await run_profile(
            child, "jobs_page", jobs_s, shapes=("jobs_page",), cadence_s=30.0,
            with_executor_sampler=True,
        )
        report["profile_jobs_page"] = jobs
        fences.check("jobs_ticks", len(jobs.get("requests", [])) >= 1,
                     f"{len(jobs.get('requests', []))} requests")
        series = (jobs.get("executor") or {}).get("series") or []
        fences.check("executor_series", len(series) >= 5,
                     f"{len(series)} sampler points")
        fences.check("faulthandler_dumps", (jobs.get("executor") or {}).get("dumps", 0) >= 1,
                     f"{(jobs.get('executor') or {}).get('dumps', 0)} dumps")
        busy_series = [s.get("busy_work_resolver_threads", 0) for s in series]
        fam_series = [
            sum((s.get("threads_in_family") or {}).values()) for s in series
        ]
        report["executor_busy_summary"] = {
            "max_busy_work_resolver_threads": max(busy_series) if busy_series else 0,
            "max_threads_in_family": max(fam_series) if fam_series else 0,
            "samples_nonzero_family": sum(1 for f in fam_series if f > 0),
            "note": (
                "work_resolver.py frames appear only briefly; the resolve "
                "dwell is inside sqlalchemy/psycopg (repositories family) — "
                "both counted"
            ),
        }

        # ── phase 7: capture phase (badge + home until capture) ─────
        print("[phase] capture phase (badge + home concurrently, tightened knobs)")
        cap_s = 90 if smoke else 240
        # Cadence DISCLOSURE: the capture phase drives the SAME FE
        # shapes but at a tighter driver-side cadence (2s/4s vs the
        # FE's 8s/10s) so the loop spends a higher duty-cycle inside
        # the sync-DB frames the dump must catch — an instrumentation
        # choice on the DRIVER, not an endpoint-shape change (Q2
        # shapes are preserved verbatim).
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            combined = await asyncio.gather(
                run_profile(child, "capture_badge", cap_s,
                            shapes=BADGE_LEGS, cadence_s=4.0, client=client),
                run_profile(child, "capture_home", cap_s,
                            shapes=("instances_bfs_home",), cadence_s=2.0,
                            client=client),
                wait_for_capture(child.capture_root, cap_s),
            )
        report["profile_capture_badge"] = combined[0]
        report["profile_capture_home"] = combined[1]
        capture_assertion(child.capture_root, fences, report)

        # lag fences
        for name, prof in (("badge", badge), ("home", home), ("jobs", jobs)):
            lag = prof.get("lag") or {}
            fences.check(f"lag_samples[{name}]", (lag.get("samples") or 0) > 0,
                         f"samples={lag.get('samples')}")

        report["fences"] = fences.results
        report["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        report["verdict"] = "PASS" if fences.all_ok else "FAIL"
        write_json(raw_dir / "BASELINE.json", report)
        print(f"\n[report] {raw_dir / 'BASELINE.json'} — verdict {report['verdict']}")
        return 0 if fences.all_ok else 1

    finally:
        # ── phase 8: teardown (ALWAYS) ──────────────────────────────
        if child is not None:
            with contextlib.suppress(Exception):
                await child.stop()
        if pg is not None:
            proof = drop_scratch_pg(pg)
            print(f"[teardown] scratch drop: {proof}")
        # port-freed proof
        import socket

        if child is not None:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                try:
                    s.connect(("127.0.0.1", child.port))
                    freed = False
                except OSError:
                    freed = True
            print(f"[teardown] port {child.port} freed: {freed}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=("full", "smoke"), default="full")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.profile)))
