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
    synthetic_handler_load,
    wait_for_capture,
)
from seed import SeedScale, seed  # noqa: E402
import modes  # noqa: E402
from modes import (  # noqa: E402
    MODE_BASE,
    MODE_HEAD,
    MODES,
    SIMULATE_HEAD,
    SIMULATIONS,
    SIMULATE_NONE,
)


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
                             report: dict,
                             raw_bodies: dict[str, bytes] | None = None) -> None:
    """Deliverables (1) floor + (3) round-trip formula table — serialized.

    REPAIR 2026-10-30: also records the per-request response
    ``body_sha256``/``body_len`` (from the harness middleware) and keeps
    the FIRST response body per shape — the G5 response-bytes gate needs
    a byte artifact to diff, and the pre-repair harness recorded neither.
    """
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
                if r is not None and r.status_code == 200 and raw_bodies is not None:
                    raw_bodies.setdefault(shape, r.content)
                records.append({
                    "shape": shape, "seq": seq, "tag": tag,
                    "status": getattr(r, "status_code", None),
                    "dur_s": round(dur, 4), "error": err, **facts,
                })
        n4 = child.counter.snapshot()["per_request"]
        body_meta = dict(child.body_meta)

    per_shape: dict[str, dict[str, object]] = {}
    for shape in SHAPES:
        recs = [r for r in records if r["shape"] == shape]
        durs = [float(r["dur_s"]) for r in recs if r.get("status") == 200]
        rt = [n4[r["tag"]]["count"] for r in recs if r["tag"] in n4]
        onloop = [n4[r["tag"]]["on_loop"] for r in recs if r["tag"] in n4]
        # REPAIR 2026-10-30: the per-shape aggregation now keeps the
        # ON-LOOP count as well as the total.  The architect's G9 fence
        # (§6.3) is stated over the on-loop SHAPE-SET, which the pre-repair
        # {shape: int} aggregate could not express.
        shapes_seen: dict[str, dict[str, int]] = {}
        for r in recs:
            if r["tag"] in n4:
                for s, v in n4[r["tag"]]["by_shape"].items():
                    e = shapes_seen.setdefault(s, {"count": 0, "on_loop": 0})
                    e["count"] += v["count"]
                    e["on_loop"] += int(v.get("on_loop", 0))
        bodies = [
            body_meta[r["tag"]][0] for r in recs
            if r["tag"] in body_meta and body_meta[r["tag"]]
        ]
        per_shape[shape] = {
            "samples": len(recs),
            "ok_samples": len(durs),
            "body_sha256_distinct": sorted({b["sha256"] for b in bodies}),
            "body_len": bodies[0]["len"] if bodies else None,
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


def formula_table(per_shape: dict, seed_result) -> dict:
    """The BASE census formula validation (Q2/Q3) — PURE computation.

    REPAIR 2026-10-30: the fences moved OUT of this function.  They are
    MODE-dependent (BASE asserts the census EXACTLY; HEAD asserts the
    O(1)/bounded targets) and must run AFTER the optional HEAD-fix
    simulation has transformed the measurements — see
    ``gate_formula_table``.  Keeping them here would have asserted the
    BASE census before the simulation could run, which is the whole
    defect this repair removes.
    """
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
    return table


def gate_formula_table(table: dict, per_shape: dict, mode: str,
                       fences: Fences) -> None:
    """The MODE-AWARE round-trip gate (G9's count half).

    BASE mode asserts the BASE census EXACTLY (unchanged from the
    committed harness — the positive control that the defect is
    measurable).

    HEAD mode asserts the plan's O(1)/bounded targets
    (``plan-overview.md`` §(d) table):
      * flat <= 6, BFS <= 12, work <= 12, jobs recent <= 25, jobs page <= 30;
      * defer-blocked and missions are UNCHANGED (asserted against the
        same census formula).

    WHY A BOUND AND NOT THE PLAN'S BAND: the architect's §6.3 fence
    defers the totals to the plan's §(d) table, whose values are
    projections written with "~" anchors.  The SCIENTIFIC claim is
    O(N) -> O(1) (the per-row N+1 collapses), which is an UPPER bound.
    A lower bound would (a) forbid the fix from landing better than
    projected and (b) false-red on the projection's own disagreement
    with itself — exactly the failure class the architect corrected for
    G2 in §6.2.  The projection band is still REPORTED per shape.
    """
    if mode == MODE_BASE:
        for name, row in table.items():
            fences.check(
                f"formula[{name}]", bool(row["match"]),
                f"expected={row['expected']} measured={row['measured_mean']}",
            )
        return

    # HEAD mode — the bounded targets.
    census = {
        "instances_flat_badge": table["instances_flat_badge"]["expected"],
        "instances_bfs_home": table["instances_bfs_home"]["expected"],
        "defer_blocked": table["defer_blocked"]["expected"],
        "missions_badge": table["missions_badge"]["expected"],
    }
    for shape in ("instances_flat_badge", "instances_bfs_home", "work",
                  "jobs_recent_badge", "jobs_page"):
        row = per_shape.get(shape) or {}
        rt = (row.get("round_trips") or {}).get("mean")
        bound = modes.head_round_trip_expectation(shape)["max"]
        fences.check(
            f"head_round_trips_bounded[{shape}]",
            rt is not None and bound is not None and rt <= bound,
            f"measured={rt} bound<={bound} "
            f"(plan projection band: see HEAD_ROUND_TRIP_MAX)",
        )
    for shape in modes.HEAD_COUNT_UNCHANGED:
        row = per_shape.get(shape) or {}
        rt = (row.get("round_trips") or {}).get("mean")
        expected = census.get(shape)
        fences.check(
            f"head_round_trips_unchanged[{shape}]",
            rt is not None and expected is not None and rt == expected,
            f"measured={rt} unchanged-expected={expected}",
        )


ORPHAN_LOOKUP_SHAPE = "SELECT instances"
#: statement_shape() of the jobs-enrichment orphan `_lookup_instance`
#: single-row lookups: verb SELECT, table set EXACTLY {instances} (the
#: joined instances endpoints render other table sets and are NOT this
#: N+1).  See child.statement_shape.


def jobs_time_share(profile_result: dict, tag_prefixes: tuple[str, ...]) -> dict:
    """Precomputed §6 time-share: orphan `_lookup_instance` vs all statements.

    One rule for BOTH aggregation windows, so future readers never
    re-derive (and never disagree about) the filter — the committed
    full baseline was hand-derived as 92.3% (page window) vs
    89.6–89.9% per-request (badge window) from the same data.

    Window = the N4 per-request bookkeeping of ONE profile result,
    restricted to tags starting with ``tag_prefixes`` (empty tuple =
    every tag).  Share = orphan-lookup total_s / window total_s.
    ``per_request_share_{min,max}`` reproduces the per-request spread.
    """
    per_req = (profile_result.get("n4") or {}).get("per_request") or {}
    by_shape: dict[str, dict[str, float]] = {}
    per_request_shares: list[float] = []
    for tag, v in per_req.items():
        if tag_prefixes and not tag.startswith(tag_prefixes):
            continue
        req_total = 0.0
        req_orphan = 0.0
        for sh, sv in (v.get("by_shape") or {}).items():
            e = by_shape.setdefault(sh, {"count": 0, "total_s": 0.0})
            e["count"] += sv.get("count", 0)
            e["total_s"] += sv.get("total_s", 0.0)
            req_total += sv.get("total_s", 0.0)
            if sh == ORPHAN_LOOKUP_SHAPE:
                req_orphan += sv.get("total_s", 0.0)
        if req_total > 0:
            per_request_shares.append(req_orphan / req_total)
    total_s = sum(e["total_s"] for e in by_shape.values())
    orphan = by_shape.get(ORPHAN_LOOKUP_SHAPE, {"count": 0, "total_s": 0.0})

    def _share(num: float) -> float | None:
        return round(num / total_s, 4) if total_s else None

    return {
        "by_shape": {
            sh: {"count": e["count"], "total_s": round(e["total_s"], 4),
                 "share": _share(e["total_s"])}
            for sh, e in sorted(by_shape.items(),
                                key=lambda kv: -kv[1]["total_s"])
        },
        "total_s": round(total_s, 4),
        "orphan_lookup_shape": ORPHAN_LOOKUP_SHAPE,
        "orphan_lookup_count": orphan["count"],
        "orphan_lookup_total_s": round(orphan["total_s"], 4),
        "orphan_lookup_share": _share(orphan["total_s"]),
        "per_request_share_min": round(min(per_request_shares), 4)
        if per_request_shares else None,
        "per_request_share_max": round(max(per_request_shares), 4)
        if per_request_shares else None,
        "requests_in_window": len(per_request_shares),
    }


def collect_captures(capture_root: Path) -> list[dict]:
    """Parse every wedge-capture into a record (SECTIONS kept in memory).

    REPAIR 2026-10-30 — the pre-repair analyser was THREAD-AGNOSTIC: it
    computed the family pin over EVERY thread section
    (``main.py:410-415``) and stored it under a key that CLAIMED the main
    thread (``:416``), while the main-thread identification it computed
    (``:385-401``) was dead code.  The architect (§6.1) proved the
    consequence: at HEAD the BRIDGE thread's own instance-repo frames
    trip the pin, so the harness false-reds on a correct fix.  The
    analyser now applies the architect's LITERAL predicate to the LOOP
    thread only, and the thread-agnostic count is kept as DIAGNOSTIC
    context (never as the assertion).
    """
    dirs = sorted(
        d for d in capture_root.iterdir()
        if d.is_dir() and d.name != "boot-records"
    ) if capture_root.is_dir() else []
    records: list[dict] = []
    for d in dirs:
        stacks_p = d / "stacks.txt"
        manifest_p = d / "manifest.json"
        entry: dict[str, object] = {"capture_id": d.name}
        if manifest_p.is_file():
            with contextlib.suppress(Exception):
                entry["manifest"] = json.loads(manifest_p.read_text())
        if not stacks_p.is_file():
            entry["stacks"] = "missing"
            entry["sections"] = []
            entry["facts"] = modes.analyze_capture_sections([])
            records.append(entry)
            continue
        sections = modes.parse_sections(stacks_p.read_text(errors="replace"))
        entry["sections"] = sections
        entry["facts"] = modes.analyze_capture_sections(sections)
        records.append(entry)
    return records


def _public_capture(rec: dict) -> dict:
    """The JSON-safe view (the parsed stack sections never reach the report)."""
    return {k: v for k, v in rec.items() if k != "sections"}


def gate_captures(records: list[dict], fences: Fences, report: dict,
                  mode: str, profile: str) -> None:
    """G1/G3 — the MODE-AWARE wedge-capture gate.

    Non-vacuity is TWO fences, in BOTH modes:
      * ``captures_collected`` — the capture phase produced >= 1 capture;
      * ``capture_loop_section_collected`` — at least one capture
        actually CONTAINS the loop thread.  A capture taken while the
        dump could not see the loop thread cannot clear (or convict)
        anything; the pre-repair harness would have read "0 violations"
        off an empty section set and called it a pass (architect §6.1:
        "G1 is INCONCLUSIVE (not PASS) if the capture phase collected 0
        captures").
      * ``predicate_discriminator_selftest`` — a tree-independent proof
        that the predicates TELL BROKEN FROM FIXED.  Without it, HEAD
        mode's "0 violations" is unfalsifiable.
    """
    n = len(records)
    fences.check("captures_collected", n >= 1, f"{n} capture dir(s)")

    loop_seen = sum(
        1 for r in records if (r.get("facts") or {}).get("loop_sections", 0) >= 1
    )
    fences.check(
        "capture_loop_section_collected", loop_seen >= 1,
        f"{loop_seen}/{n} capture(s) contain the loop thread "
        f"({modes.LOOP_THREAD_MARKER})",
    )

    st = modes.discriminator_selftest()
    report["predicate_selftest"] = st
    fences.check(
        "predicate_discriminator_selftest", bool(st["ok"]),
        "the G1 predicates separate the synthetic BROKEN stack (fires) from "
        "the synthetic FIXED stack (silent, bridge-thread frames ignored) "
        "and ignore the D-9 UI-prefs residual",
    )

    fam_loop = sum(
        1 for r in records if (r.get("facts") or {}).get("family_violation_loop_thread")
    )
    narrow_loop = sum(
        1 for r in records if (r.get("facts") or {}).get("narrow_violation_loop_thread")
    )
    fam_any = sum(
        1 for r in records if (r.get("facts") or {}).get("family_violation_any_thread")
    )
    narrow_any = sum(
        1 for r in records if (r.get("facts") or {}).get("narrow_violation_any_thread")
    )
    summary = {
        "captures": n,
        "captures_with_loop_section": loop_seen,
        "family_violation_loop_thread": fam_loop,
        "narrow_violation_loop_thread": narrow_loop,
        "family_violation_any_thread_DIAGNOSTIC": fam_any,
        "narrow_violation_any_thread_DIAGNOSTIC": narrow_any,
        # W-5 (round-2 ride-along): per-capture, not just the aggregate —
        # one HEAD capture had loop_sections=0 and must be visible as
        # "no evidence" rather than folded into a clean aggregate.
        "captures_without_loop_section": [
            r.get("capture_id") for r in records
            if (r.get("facts") or {}).get("loop_sections", 0) < 1
        ],
        "predicate": (
            f"loop section contains {modes.FAMILY_REPO_PATH!r} AND "
            f"{modes.SQLALCHEMY!r} (architect §6.1, literal)"
        ),
    }
    report["capture_gate"] = summary

    if mode == MODE_BASE:
        fences.check(
            "stack_pin_family[loop thread: sync-DB instances path]",
            fam_loop >= 1,
            f"{fam_loop}/{n} capture(s) show the instances-path DB frames on "
            f"the LOOP thread (diagnostic: {fam_any}/{n} across ALL threads)",
        )
        if profile == "full":
            fences.check(
                "stack_pin_narrow[loop thread: list_child_ids_permanent + router]",
                narrow_loop >= 1,
                f"{narrow_loop}/{n} capture(s) carry the narrow pin on the "
                f"LOOP thread (diagnostic: {narrow_any}/{n} across ALL threads)",
            )
        else:
            print(
                f"  [fence:INFO] stack_pin_narrow (smoke: report-only — the "
                f"narrow pin is scale-dependent) — {narrow_loop}/{n} on the "
                f"loop thread"
            )
    else:
        fences.check(
            "stack_pin_family_absent[loop thread: sync-DB instances path]",
            fam_loop == 0,
            f"{fam_loop}/{n} capture(s) still show the instances-path DB frames "
            f"on the LOOP thread (diagnostic: {fam_any}/{n} across ALL threads "
            f"— a bridge-thread hit is NOT a violation)",
        )
        fences.check(
            "stack_pin_narrow_absent[loop thread: list_child_ids_permanent + router]",
            narrow_loop == 0,
            f"{narrow_loop}/{n} capture(s) carry the narrow pin on the LOOP "
            f"thread (diagnostic: {narrow_any}/{n} across ALL threads)",
        )


def gate_g9_on_loop(per_shape: dict, fences: Fences, report: dict,
                    mode: str) -> None:
    """G9's ON-LOOP half — the architect's §6.3 per-shape SET fence.

    The pre-repair harness had NO on-loop shape gate, and the plan's
    wording ("instances-path on-loop 0 in every shape") false-reds on the
    ROUTED-OUT missions shape because the harness classifies statements by
    TABLE SET: the missions census legitimately issues ``SELECT
    instances`` on the loop (§6.3, census #4 — explicitly exempt).

    COLLECTION FENCE FIRST: every censused shape must be present with
    samples, or the per-shape assertion below is vacuous (the
    false-green family this harness exists to prevent).
    """
    missing = [
        shape for shape in SHAPES
        if not (per_shape.get(shape) or {}).get("samples")
    ]
    fences.check(
        "g9_shapes_collected", not missing,
        f"{len(SHAPES) - len(missing)}/{len(SHAPES)} censused shape(s) measured"
        + (f"; MISSING: {missing}" if missing else ""),
    )

    # UNIT NORMALIZATION AT THE GATE BOUNDARY (round-2 Blocking 1).
    # ``statements_by_shape`` is SAMPLE-SUMMED; every rule below is
    # PER-REQUEST.  The pre-repair gate compared summed against per-request
    # constants — the council measured missions {20,10} over 10 samples vs
    # the pinned (2,1), a shape this lane never touches failing
    # deterministically forever.  One normalization site, one path, shared
    # by real and simulated runs alike.
    derived: dict[str, dict[str, int]] = {}
    for shape, row in per_shape.items():
        by_shape = modes.per_request_by_shape(row)
        on_loop = {
            s: int(sv.get("on_loop", 0))
            for s, sv in by_shape.items() if int(sv.get("on_loop", 0)) > 0
        }
        derived[shape] = on_loop
    report["g9_on_loop_shape_sets"] = derived
    report["g9_unit_class"] = modes.declared_unit_classes()

    for shape in SHAPES:
        got = derived.get(shape, {})
        if mode == MODE_BASE:
            row = per_shape.get(shape) or {}
            total = (row.get("round_trips") or {}).get("mean")
            on_loop_total = (row.get("on_loop_round_trips") or {}).get("mean")
            if shape in modes.BASE_ON_LOOP_NONE:
                fences.check(
                    f"g9_on_loop_base[{shape}]", on_loop_total == 0,
                    f"the BASE census puts NOTHING on the loop for this shape "
                    f"(asyncio.to_thread); measured on-loop={on_loop_total} "
                    f"of {total}",
                )
            elif shape in modes.BASE_ON_LOOP_ALL:
                fences.check(
                    f"g9_on_loop_base[{shape}]",
                    on_loop_total is not None and total is not None
                    and on_loop_total == total,
                    f"the BASE census puts EVERY round-trip on the loop; "
                    f"measured on-loop={on_loop_total} of {total}",
                )
            continue

        rule = modes.HEAD_ON_LOOP_RULES.get(shape)
        if rule is None:
            fences.check(
                f"g9_on_loop_head[{shape}]", not got,
                f"on-loop {got or '{} (empty)'} — every shape not carrying a "
                f"disclosed residual must be EMPTY on the loop",
            )
        elif "exact" in rule:
            exp = rule["exact"]
            ok = set(got) == set(exp) and all(
                lo <= got[s] <= hi for s, (lo, hi) in exp.items()
            )
            fences.check(
                f"g9_on_loop_head[{shape}]", ok,
                f"on-loop {got or '{} (empty)'} vs exact {exp} — {rule['why']}",
            )
        else:
            forbid = tuple(rule.get("forbid_exact", ()))
            allow = tuple(rule.get("allow_prefixes", ()))
            ceiling = rule.get("max_on_loop")
            bad_forbidden = [s for s in got if s in forbid]
            bad_outside = [
                s for s in got if allow and not str(s).startswith(allow)
            ]
            total = sum(got.values())
            ok = (not bad_forbidden and not bad_outside
                  and (ceiling is None or total <= ceiling))
            fences.check(
                f"g9_on_loop_head[{shape}]", ok,
                f"on-loop {got or '{} (empty)'}; forbidden-hit="
                f"{bad_forbidden or 'none'}, outside-allowlist="
                f"{bad_outside or 'none'}, total={total}"
                + (f"<={ceiling}" if ceiling is not None else "")
                + f" — {rule['why']}",
            )


async def g5_bodies_phase(child: DaemonChild, fences: Fences,
                           report: dict) -> None:
    """G5 EVIDENCE COLLECTION — the response bodies, deterministically.

    Fetches the EIGHT censused shapes plus ONE ``/api/instances/{id}``
    DETAIL request (the plan's §(e) requires it: the detail path shares the
    ordered children source), serialized and unloaded, one request each, so
    every count below is PER-REQUEST BY CONSTRUCTION.

    Records per shape: the RAW body sha256, the NORMALIZED sha256
    (children-array order normalized away — the ONE permitted difference
    class — then re-serialized with canonical separators), the
    ``children_order`` facts (the §5.2 positive assertion), and the
    per-request round-trips.
    """
    import hashlib
    import httpx

    names: list[tuple[str, str]] = [("shape:" + k, v) for k, v in SHAPES.items()]

    async with httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{child.port}"
    ) as client:
        # The detail id, chosen DETERMINISTICALLY from the BFS response:
        # the row with the most children, tie-broken by instance_id.  The
        # fixture is seeded and its clock pinned, so this is stable across
        # runs and across BASE/HEAD.
        detail_id: str | None = None
        child.counter.reset_requests()
        try:
            r = await client.get(
                SHAPES["instances_bfs_home"],
                headers={"X-Evloop-Tag": "g5:pick-detail"},
                timeout=180.0,
            )
            if r.status_code == 200:
                insts = (r.json() or {}).get("instances") or []
                with_kids = [
                    i for i in insts
                    if isinstance(i, dict) and i.get("children")
                ]
                if with_kids:
                    detail_id = max(
                        with_kids,
                        key=lambda i: (len(i["children"]), i.get("instance_id", "")),
                    ).get("instance_id")
        except Exception:  # noqa: BLE001
            detail_id = None
        if detail_id:
            names.append(("detail:/api/instances/{id}",
                          f"/api/instances/{detail_id}"))
        report["g5_detail_id"] = detail_id
        fences.check(
            "g5_detail_shape_resolved", detail_id is not None,
            f"detail id resolved deterministically from the BFS response: "
            f"{detail_id}",
        )

        child.counter.reset_requests()
        rows: dict[str, dict] = {}
        for name, url in names:
            t0 = time.perf_counter()
            try:
                r = await client.get(
                    url, headers={"X-Evloop-Tag": f"g5:{name}"}, timeout=180.0
                )
                err = None
            except Exception as exc:  # noqa: BLE001
                r, err = None, f"{type(exc).__name__}: {exc}"
            if r is None or r.status_code != 200:
                rows[name] = {
                    "status": getattr(r, "status_code", None), "error": err,
                }
                continue
            raw = r.content
            try:
                payload = r.json()
            except Exception:  # noqa: BLE001
                payload = None
            rows[name] = {
                "status": r.status_code,
                "dur_s": round(time.perf_counter() - t0, 4),
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
                "body_len": len(raw),
                "normalized_sha256": (
                    hashlib.sha256(
                        modes.normalize_children_order(payload).encode()
                    ).hexdigest() if payload is not None else None
                ),
                "children_order": (
                    modes.children_order_canonical(payload)
                    if payload is not None else None
                ),
            }
        n4 = child.counter.snapshot()["per_request"]
        for name, url in names:
            row = rows.get(name) or {}
            tag = f"g5:{name}"
            if tag in n4:
                row["round_trips"] = n4[tag]["count"]
                row["on_loop_round_trips"] = n4[tag]["on_loop"]
    report["g5_bodies"] = rows
    fences.check(
        "g5_shapes_collected",
        len(rows) == len(names) and all(
            (v or {}).get("status") == 200 for v in rows.values()
        ),
        f"{len(rows)}/{len(names)} shape(s) fetched, all-200="
        f"{all((v or {}).get('status') == 200 for v in rows.values())}",
    )


def gate_g5(report: dict, fences: Fences, mode: str,
            reference: dict | None) -> None:
    """G5 — the response-bytes gate (two-part, plan §(e) + architect §5.2).

    Part (i)  NORMALIZED-hash equality vs the paired reference: exact bytes
              everywhere EXCEPT the children-array order (the ONE disclosed
              normalization, applied POST-PROCESSING to the parsed payload —
              the middleware hashes raw bytes and has no hook).
    Part (ii) the POSITIVE children-order assertion: HEAD's children order
              must be exactly ``parent_id ASC, instance_id ASC``.  At BASE
              the order is heap order (``repository.py:432-437`` has no
              ORDER BY) ⇒ this FAILS at BASE for exactly the reason the fix
              removes — that is the gate's BASE-side positive control.
    Non-vacuity: a shape-count fence (9 = 8 shapes + the detail request) and
    a children-arrays-present fence (the detail + BFS must actually carry
    children arrays, else part (ii) is vacuous).
    """
    rows = report.get("g5_bodies") or {}
    ref_rows = (reference or {}).get("g5_bodies") or {}

    # ── non-vacuity ───────────────────────────────────────────────────
    with_arrays = {
        n for n, v in rows.items()
        if (v.get("children_order") or {}).get("arrays", 0) > 0
    }
    fences.check(
        "g5_children_arrays_present",
        any(n.startswith("detail:") for n in with_arrays)
        and any("instances_bfs_home" in n for n in with_arrays),
        f"{len(with_arrays)} shape(s) carry children arrays: "
        f"{sorted(with_arrays)[:4]}",
    )

    # ── part (i): normalized byte equality vs the reference ───────────
    # ROUND-3 FIX (wi-3f5bea95, residual §9b item 9).  The round-2 fence
    # rendered ``len(rows) - len(diffs)`` over ``len(rows)``: a shape with NO
    # normalized hash (a 500 carries no body) was EXCLUDED by the
    # ``is not None`` guard and STILL counted in the denominator, so a real
    # run printed "9/9 ... byte-identical" while only 8 shapes had ever been
    # compared — the vacuous-green class.  An uncomparable shape is a
    # COMPARISON GAP: it is NAMED in the fence line with its reason and it
    # FAILS.  There is no path on which an excluded shape reads as a pass.
    # ``g5_shapes_collected``'s all-200 clause remains the hard red carrier.
    if ref_rows:
        compared: list[str] = []
        excluded: list[str] = []
        for n, v in rows.items():
            rv = ref_rows.get(n) or {}
            if v.get("normalized_sha256") is None:
                excluded.append(
                    f"{n} (this side: status {v.get('status')}, "
                    f"no normalized_sha256"
                    + (f", error={v.get('error')!r}" if v.get("error") else "")
                    + ")"
                )
            elif rv.get("normalized_sha256") is None:
                excluded.append(
                    f"{n} (reference side: no normalized_sha256)"
                )
            else:
                compared.append(n)
        diffs = sorted(
            n for n in compared
            if (ref_rows.get(n) or {}).get("normalized_sha256")
            != rows[n].get("normalized_sha256")
        )
        detail = f"COMPARED {len(compared)}/{len(rows)} shape(s)"
        detail += (" — all byte-identical after the children-order "
                   "normalization" if not diffs else f" — DIFFER: {diffs}")
        if excluded:
            detail += (
                f"; EXCLUDED — NOT COMPARABLE: {sorted(excluded)} "
                f"→ an excluded shape is NOT evidence of byte-identity"
            )
        fences.check(
            "g5_normalized_bytes_equal_vs_reference",
            not diffs and not excluded,
            detail,
        )
    else:
        print("  [fence:INFO] g5_normalized_bytes_equal_vs_reference "
              "(no reference: report-only)")

    # ── part (ii): the children-order positive assertion ──────────────
    for name, v in sorted(rows.items()):
        co = v.get("children_order") or {}
        if not co.get("arrays"):
            continue
        canonical = bool(co.get("canonical"))
        if mode == MODE_BASE:
            # MEASURED RETRACTION (round 2): the BASE-side "unordered"
            # positive control is IMPOSSIBLE on this host.  The query has
            # NO order_by (repository.py:434-437, verified statically) yet
            # the planner returns the rows in primary-key order, so the
            # children arrive ALREADY sorted — measured 130/130 BFS arrays
            # and 1/1 detail array canonical at BASE, under BOTH ascending
            # and REVERSED sibling insertion orders (the seed.py G5
            # discriminator experiment).  The order is therefore
            # contractually unspecified but behaviorally sorted today:
            # asserting "unordered" would red a tree that is not defective.
            # The instrument for the unspecified-order half is §5.2
            # correction 3's STATIC SQL ORDER BY pin (the pin pack), not
            # this harness.  Reported here, never asserted at BASE.
            print(
                f"  [fence:INFO] g5_children_order (BASE: report-only — "
                f"measured ALREADY canonical on this host/planner; the "
                f"contract-unspecified half is the static ORDER BY pin) — "
                f"{name}: {co.get('ordered')}/{co.get('arrays')} ordered"
            )
        else:
            fences.check(
                f"g5_children_order[head: parent_id ASC, instance_id ASC][{name}]",
                canonical,
                f"{co.get('ordered')}/{co.get('arrays')} arrays ordered — "
                f"offenders: {co.get('offender_paths')}",
            )

    # ── jobs_active_badge: the ENUMERATED RESIDUAL ─────────────────────
    # Included in the 8-shape canonical set (the plan froze 7).  The caller
    # sends no limit param, so C-4 cannot bound it without a semantics
    # change: gated on NON-REGRESSION ONLY (HEAD <= BASE, per-request),
    # never on an absolute bound.  Unit class: PER-REQUEST.
    ja = "shape:jobs_active_badge"
    head_rt = (rows.get(ja) or {}).get("round_trips")
    if mode == MODE_HEAD and ref_rows:
        base_rt = (ref_rows.get(ja) or {}).get("round_trips")
        fences.check(
            "g5_jobs_active_non_regression[enumerated residual]",
            head_rt is not None and base_rt is not None and head_rt <= base_rt,
            f"HEAD {head_rt} <= BASE {base_rt} round-trips per request "
            f"(unit: per-request; NOT absolutely bounded — the caller sends "
            f"no limit param, so C-4 cannot bound it)",
        )
    else:
        print(f"  [fence:INFO] g5_jobs_active_non_regression — measured "
              f"{head_rt} round-trips/request (report-only without a HEAD "
              f"reference)")


def gate_g2_band(report: dict, fences: Fences, mode: str,
                 profile: str) -> None:
    """G2 — the ONE-SIDED loaded-flat band (architect §6.2).

    The pre-repair plan asserted a TWO-SIDED 0.067-0.23 s band.  The
    architect measured that a HEALTHY HEAD lands at 0.02-0.10 s — BELOW
    the lower anchor — so the two-sided band false-reds a working lane.
    The corrected fence is one-sided and re-purposes the floor: anything
    at or below 0.230 s PASSES, and a value below 0.067 s is recorded as
    an IMPROVEMENT.
    """
    badge = report.get("profile_badge") or {}
    lat = badge.get("latencies") or {}
    flat = lat.get("instances_flat_badge") or {}
    p95_ms = flat.get("p95_ms")
    p95_s = (float(p95_ms) / 1000.0) if p95_ms is not None else None
    verdict = modes.g2_verdict(p95_s)

    reqs = [r for r in (badge.get("requests") or [])
            if r.get("shape") == "instances_flat_badge"]
    all_200 = bool(reqs) and all(r.get("status") == 200 for r in reqs)
    flat_rts = ((report.get("per_endpoint") or {})
                .get("instances_flat_badge", {})
                .get("round_trips", {}).get("mean"))

    report["g2"] = {
        "loaded_flat_p95_s": p95_s,
        "verdict": verdict,
        "one_sided_bands_s": {
            "pass_max": modes.G2_PASS_MAX_S,
            "degraded_max": modes.G2_DEGRADED_MAX_S,
            "base_anchor": modes.G2_BASE_ANCHOR_S,
            "expected_healthy": list(modes.G2_EXPECTED_HEALTHY_S),
        },
        "flat_samples": len(reqs),
        "flat_round_trips": flat_rts,
        "below_anchor_is_improvement": (
            p95_s is not None and p95_s < modes.G2_BASE_ANCHOR_S
        ),
    }

    # Non-vacuity (architect §6.2): >=5 badge ticks and every flat leg 200.
    # SCALE-DEPENDENT by construction: the smoke window is 16 s at an 8 s
    # cadence => 2 ticks, so the >=5 requirement can only be MET on the full
    # profile.  Smoke reports it instead of reding a run that structurally
    # cannot satisfy it (the same discipline as the scale-dependent narrow
    # pin in BASE mode) — the requirement itself is unchanged on `full`.
    g2_nonvac_ok = len(reqs) >= 5 and all_200 and p95_s is not None
    if profile == "full":
        fences.check(
            "g2_non_vacuity_flat_measurement", g2_nonvac_ok,
            f"{len(reqs)} flat leg request(s) (>=5 required), "
            f"all-200={all_200}, p95={p95_s}",
        )
    else:
        print(
            f"  [fence:INFO] g2_non_vacuity_flat_measurement (smoke: "
            f"report-only — the smoke badge window is 2 ticks, the architect "
            f"requires >=5) — {len(reqs)} request(s), all-200={all_200}"
        )
    # SCALE-DEPENDENT: G2's signal is the flat leg QUEUING BEHIND the loop
    # blockers.  At 1/10 scale (smoke: 350 instances, a 16 s badge window)
    # that queueing does not reproduce — the smoke run measured a loaded
    # flat p95 of 0.139 s against 1.1027 s at full scale — so the band is
    # asserted on the `full` profile and REPORTED on smoke.  The defect
    # pins that DO discriminate at every scale (the round-trips, the
    # on-loop attribution and the stack pin) stay asserted on both.
    name = ("g2_band[base: the defect is measurable]" if mode == MODE_BASE
            else "g2_band[head: p95 <= 0.230 s, one-sided]")
    want_ok = (verdict != modes.G2_VERDICT_PASS if mode == MODE_BASE
               else verdict == modes.G2_VERDICT_PASS)
    detail = (
        f"loaded flat p95={p95_s} s -> {verdict}; "
        + ("BASE mode EXPECTS the loaded leg OUTSIDE the healthy band "
           "(the committed full-scale BASELINE measured 1.1027 s)"
           if mode == MODE_BASE else "HEAD mode REQUIRES the healthy band")
    )
    if profile == "full":
        fences.check(name, want_ok, detail)
    else:
        print(f"  [fence:INFO] {name} (smoke: report-only — the loop-queueing "
              f"inflation G2 measures does not reproduce at 1/10 scale) — "
              f"{detail}")


def gate_g4_paired_lag(report: dict, fences: Fences, mode: str,
                       reference: dict | None) -> None:
    """G4 — the PAIRED loop-lag comparison (architect §6.4).

    The bare p95 comparison is forbidden: the probe's healthy value IS the
    watchdog tick (~0.5 s), so the minimum detectable effect of a p95
    comparison (~30 ms) sits BELOW the noise floor, and p95 is invisible
    to a regression affecting <5% of ticks.  The fence compares STALL
    COUNT, MAX (no tolerance) and p95 + one tick, per profile, against a
    paired BASE reference run.
    """
    profiles = ("profile_badge", "profile_home", "profile_jobs_page")
    out: dict[str, object] = {}
    # Initialize FIRST, on every path — the provenance block below writes
    # into report["g4"] and the reference-present branch previously reached
    # it before any assignment (KeyError; measured in the round-2b truth
    # table, cell 2: exit 1 with NO report written).
    report.setdefault("g4", {})
    if reference is None:
        report["g4"] = {
            "paired": False,
            "note": ("no --reference supplied: the paired comparison is the "
                     "gate, not an absolute constant"),
        }
        if mode == MODE_HEAD:
            fences.check(
                "g4_paired_reference_present", False,
                "HEAD mode REQUIRES --reference=<BASE BASELINE JSON>: G4 is a "
                "paired BASE<->HEAD comparison, and a HEAD run without its "
                "reference cannot assert the non-vacuity condition "
                "(samples >= 0.5 x BASE) — failing loudly rather than passing "
                "vacuously",
            )
        else:
            print("  [fence:INFO] g4 paired comparison (BASE mode, no "
                  "reference: report-only)")
        return

    # BLOCKING 2 (round 2): the reference's PROVENANCE is asserted before
    # G4 consumes it.  Pointing --reference at a SIMULATED output (lag
    # clamped to the tick floor by rule S4) made every G4 condition pass on
    # a broken tree.  A mismatched or unverifiable reference is a FAIL, not
    # a silent pass.
    prov = modes.reference_provenance(reference)
    report["g4"]["reference_provenance"] = prov
    fences.check(
        "g4_reference_provenance[base-mode, not simulated, tree recorded]",
        bool(prov.get("ok")),
        "; ".join(prov.get("problems") or [])
        or f"mode={prov.get('reference_mode')} simulated="
           f"{prov.get('reference_simulated')} tree="
           f"{str(prov.get('reference_repo_head'))[:12]}",
    )

    ok_all = True
    for key in profiles:
        head_prof = report.get(key) or {}
        base_prof = reference.get(key) or {}
        head_series = [float(v) for v in ((head_prof.get("lag") or {}).get("series") or [])]
        base_series = [float(v) for v in ((base_prof.get("lag") or {}).get("series") or [])]
        verdict = modes.g4_profile_verdict(head_series, base_series)
        out[key] = verdict
        ok_all = ok_all and bool(verdict.get("ok"))
        fences.check(
            f"g4_paired[{key}]", bool(verdict.get("ok")),
            verdict.get("reason")
            or (f"stalls {verdict.get('head_stalls')}<={verdict.get('base_stalls')}, "
                f"max {verdict.get('head_max_s')}<={verdict.get('base_max_s')}, "
                f"p95 {verdict.get('head_p95_s')}<={verdict.get('base_p95_s')}+"
                f"{modes.G4_TICK_S}, samples {verdict.get('head_samples')}>="
                f"0.5*{verdict.get('base_samples')}"),
        )
    # UPDATE, never reassign: the provenance record written above must
    # survive into the report (a bare assignment dropped it — caught by the
    # regression guard added with this fix).
    report["g4"].update({"paired": True, "profiles": out, "ok": ok_all})


async def main(profile: str, mode: str, simulate: str,
               reference_path: str | None, capture_bodies: bool) -> int:
    """Collect, (optionally) simulate, then apply the MODE's gate set.

    ORDER IS LOAD-BEARING: every measurement is collected FIRST, the
    optional HEAD-fix simulation transforms the collected evidence, and
    ONLY THEN do the mode-dependent assertions run.  The pre-repair
    harness asserted the BASE census inside the collection phase, so no
    simulation and no mode switch could reach it (the blocking defect).
    """
    smoke = profile == "smoke"
    simulate_head = simulate == SIMULATE_HEAD
    raw_dir = HARNESS_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    fences = Fences()
    report: dict[str, object] = {
        "harness": "eventloop-phase0",
        "harness_repair": "2026-10-30-head-mode",
        "profile": profile,
        "mode": mode,
        "simulation_requested": simulate,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repo_head": None,
    }

    # ── phase 0: fences ─────────────────────────────────────────────
    assert_clean_env()
    fences.check("env_scrub", True, "no ambient POSTGRES_*/PORT survived")
    fences.check(
        "mode_declared", mode in MODES,
        f"mode={mode!r} (BASE asserts the defect PRESENT; HEAD asserts it ABSENT)",
    )
    report["mode_expectations"] = modes.mode_summary(mode)
    if simulate_head:
        print("\n" + "!" * 72)
        print("!! " + modes.SIMULATION_DISCLAIMER)
        print("!" * 72 + "\n")
        report["simulated"] = True
    import subprocess

    report["repo_head"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.strip()

    reference: dict | None = None
    if reference_path:
        reference = json.loads(Path(reference_path).read_text())
        report["reference_path"] = reference_path
        report["reference_mode"] = reference.get("mode")
        report["reference_repo_head"] = reference.get("repo_head")

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

        # ── phase 3: per_endpoint floor + body hashes (COLLECTION) ──
        raw_bodies: dict[str, bytes] = {}
        await per_endpoint_phase(child, fences, smoke, report, raw_bodies)

        # ── phase 3b: G5 response-body evidence (9 shapes, deterministic) ──
        print("[phase] G5 response bodies (8 shapes + detail, serialized)")
        await g5_bodies_phase(child, fences, report)

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

        # ── precomputed §6 jobs time-share (windows labeled) ────────
        report["jobs_time_share"] = {
            "definition": (
                "share = orphan `_lookup_instance` statement seconds "
                f"(shape '{ORPHAN_LOOKUP_SHAPE}': SELECT over exactly the "
                "instances table) / ALL statement seconds in the window; "
                "both windows computed by main.jobs_time_share()"
            ),
            "page_window": {
                "window": "the §6 jobs_page profile (all its requests)",
                **jobs_time_share(jobs, ()),
            },
            "badge_window": {
                "window": "the §4 badge composite, jobs legs only "
                          "(jobs_active_badge + jobs_recent_badge)",
                **jobs_time_share(
                    badge, ("jobs_active_badge", "jobs_recent_badge")
                ),
            },
            "badge_window_recent_legs": {
                "window": "§4 badge, jobs_recent_badge legs only — the leg "
                          "the baseline narrative's '89.9% in the badge "
                          "window' cites",
                **jobs_time_share(badge, ("jobs_recent_badge",)),
            },
            "badge_window_active_legs": {
                "window": "§4 badge, jobs_active_badge legs only "
                          "(active/queued jobs sit on real instances)",
                **jobs_time_share(badge, ("jobs_active_badge",)),
            },
        }

        # ── phase 7: capture phase (badge + home until capture) ─────
        print("[phase] capture phase (badge + home concurrently, tightened knobs)")
        cap_s = 90 if smoke else 240
        # The SYNTHETIC HANDLER-LOAD LEG (architect §6.1 non-vacuity): the
        # capture fires on a CPU-sustained thread, so the loop's duty cycle
        # inside the sync-DB frames is a few percent and the loop-thread pin
        # is sampled by LUCK (measured: 0/8 captures in one full run, 4/5 in
        # another).  This leg holds the loop inside those frames for the whole
        # window, so the BASE positive control is deterministic.
        load_s = min(cap_s, 45 if smoke else 120)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            combined = await asyncio.gather(
                run_profile(child, "capture_badge", cap_s,
                            shapes=BADGE_LEGS, cadence_s=4.0, client=client),
                run_profile(child, "capture_home", cap_s,
                            shapes=("instances_bfs_home",), cadence_s=2.0,
                            client=client),
                wait_for_capture(child.capture_root, cap_s),
                synthetic_handler_load(child, load_s, concurrency=4),
            )
        report["profile_capture_badge"] = combined[0]
        report["profile_capture_home"] = combined[1]
        report["profile_capture_load"] = combined[3]
        fences.check(
            "capture_load_leg_active",
            (combined[3].get("requests") or 0) >= 10,
            f"{combined[3].get('requests')} synthetic handler-load request(s) "
            f"over {combined[3].get('duration_s')} s at concurrency "
            f"{combined[3].get('concurrency')} — the BASE positive control's "
            f"sampling target",
        )

        # ══ COLLECTION DONE — parse the captures, then (optionally) ══
        # ══ simulate the fixed state, THEN run the MODE's gates.   ══
        captures = collect_captures(child.capture_root)

        if capture_bodies and raw_bodies:
            bodies_dir = raw_dir / f"bodies-{profile}-{mode}"
            bodies_dir.mkdir(parents=True, exist_ok=True)
            for shape, body in raw_bodies.items():
                (bodies_dir / f"{shape}.json").write_bytes(body)
            report["bodies_dir"] = str(bodies_dir)
            print(f"[bodies] wrote {len(raw_bodies)} raw response body(ies) to "
                  f"{bodies_dir}")

        if simulate_head:
            report = modes.simulate_report(report)
            captures = modes.simulate_captures(captures)
            # the simulation must re-derive the profile-lag summaries it
            # rewrote so downstream readers see one consistent number set.
            print("[simulate] HEAD-fix evidence transformation applied "
                  "(SIMULATED — not a measurement)")

        # ── the mode's gate set ─────────────────────────────────────
        table = formula_table(report["per_endpoint"], seed_result)
        report["formula_table"] = table
        gate_formula_table(table, report["per_endpoint"], mode, fences)
        gate_g9_on_loop(report["per_endpoint"], fences, report, mode)
        gate_captures(captures, fences, report, mode, profile)
        gate_g2_band(report, fences, mode, profile)
        gate_g4_paired_lag(report, fences, mode, reference)
        gate_g5(report, fences, mode, reference)

        # lag collection fences (the probe series must be non-empty)
        for name, prof in (("badge", badge), ("home", home), ("jobs", jobs)):
            lag = prof.get("lag") or {}
            fences.check(f"lag_samples[{name}]", (lag.get("samples") or 0) > 0,
                         f"samples={lag.get('samples')}")

        report["captures"] = [_public_capture(r) for r in captures]
        report["fences"] = fences.results
        report["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # W-4 (round-2 ride-along): a simulated run must never print a bare
        # PASS — the README's "can never be mistaken for gate evidence" is
        # now enforced in the verdict string itself.
        base_verdict = "PASS" if fences.all_ok else "FAIL"
        report["verdict"] = (
            f"{base_verdict} (SIMULATED — not gate evidence)"
            if simulate_head else base_verdict
        )
        # MODE-SUFFIXED output name (repair 2026-10-30): a mode-aware run
        # can never overwrite the committed PRE-REPAIR baseline artifact
        # (raw/BASELINE.full.json, the 34/34 evidence of the old fence
        # set) nor a run of the OTHER mode.  A simulated run carries an
        # extra suffix so it can never be mistaken for gate evidence.
        suffix = f"{profile}.{mode}" + (".simulated" if simulate_head else "")
        out_path = raw_dir / f"BASELINE.{suffix}.json"
        write_json(out_path, report)
        print(f"\n[report] {out_path} — mode={mode} "
              f"simulate={simulate} verdict {report['verdict']}")
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
    ap = argparse.ArgumentParser(
        description="eventloop-phase0 harness — MODE-AWARE (repair 2026-10-30)"
    )
    ap.add_argument("--profile", choices=("full", "smoke"), required=True)
    ap.add_argument(
        "--mode", choices=MODES, required=True,
        help="base = the defect IS present (pins positive); "
             "head = the defect is ABSENT (pins negative). REQUIRED and "
             "mutually exclusive — a default would let a base-mode run be "
             "mistaken for a HEAD gate run.",
    )
    ap.add_argument(
        "--simulate", choices=SIMULATIONS, default=SIMULATE_NONE,
        help="head = transform the MEASURED evidence into the fixed state "
             "(SIMULATION — never gate evidence)",
    )
    ap.add_argument(
        "--reference", default=None,
        help="path to the paired BASE BASELINE JSON (REQUIRED in head mode: "
             "G4 is a paired comparison)",
    )
    ap.add_argument(
        "--capture-bodies", action="store_true",
        help="write one raw response body per shape (the G5 byte-diff "
             "artifact); off by default to keep the tree small",
    )
    args = ap.parse_args()
    raise SystemExit(
        asyncio.run(
            main(args.profile, args.mode, args.simulate, args.reference,
                 args.capture_bodies)
        )
    )
