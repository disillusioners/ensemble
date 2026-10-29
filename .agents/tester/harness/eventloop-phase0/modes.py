"""MODE-AWARE GATE EXPECTATIONS for the eventloop-phase0 harness.

NOT SHIPPED — test-support only.

WHY THIS MODULE EXISTS
-----------------------
The harness as committed (``d42ba2976``) asserts the defect pins
POSITIVE: it can only pass on a tree where the sync-DB hot paths are
still ON the event loop.  The architect ruled that this is a BLOCKING
GATE DEFECT (``.agents/shared/planning/eventloop-sync-db-hotpaths/
architecture-recommendation.md`` §6) and issued LITERAL corrected
fences.  This module encodes:

* the two mutually-exclusive MODES (``base`` / ``head``);
* the architect's LITERAL predicates (§6.1) — thread-scoped, not
  thread-agnostic;
* the per-mode expectation tables (what "the defect is present" means
  in BASE mode, what "the defect is absent" means in HEAD mode);
* the G2 one-sided band (§6.2) and the G9 per-shape on-loop fence
  (§6.3);
* the G4 paired loop-lag comparison (§6.4);
* a DISCRIMINATOR self-test — a synthetic stack pair proving the
  predicates tell the BROKEN state from the FIXED state (the
  non-vacuity proof that does not depend on the tree under test);
* the EVIDENCE-LEVEL HEAD-FIX SIMULATION (``--simulate=head``).

THE MODES ARE MUTUALLY EXCLUSIVE AND EXPLICIT
----------------------------------------------
``base``  — the defect IS present.  Every defect pin is asserted
            POSITIVE (the family/narrow frame pins must fire, the
            round-trip formulas must equal the BASE census, the
            on-loop attribution must equal the BASE census).
``head``  — the defect is ABSENT.  Every defect pin is asserted
            NEGATIVE (no instances-path frame on the LOOP thread, the
            round-trips are O(1)/bounded, the on-loop set equals the
            disclosed residual).

A mode never changes WHAT is measured — only which side of the
defect the assertions expect.  The predicates are shared, so the
same instrument decides both verdicts.

THE SIMULATION IS A SIMULATION
------------------------------
``--simulate=head`` transforms the MEASURED EVIDENCE of the tree
under test into the evidence the FIXED tree would produce, using the
fix's published semantics (the plan's §(d) decomposition).  It is NOT
a production fix and NOT a measurement: it exists only so the HEAD
mode can be exercised BEFORE the developer's fix lands.  Every
transformed quantity carries its rule + derivation in the report, and
the run is loudly labelled.  The simulation can never be mistaken for
gate evidence: the report records ``simulation.applied`` and the
output file name is suffixed ``.simulated``.
"""

from __future__ import annotations

import copy
import re
from typing import Any

# ════════════════════════════════════════════════════════════════════════
# The modes
# ════════════════════════════════════════════════════════════════════════

MODE_BASE = "base"
MODE_HEAD = "head"
MODES = (MODE_BASE, MODE_HEAD)

SIMULATE_NONE = "none"
SIMULATE_HEAD = "head"
SIMULATIONS = (SIMULATE_NONE, SIMULATE_HEAD)


def validate_mode(mode: str) -> str:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r} (expected one of {MODES})")
    return mode


# ════════════════════════════════════════════════════════════════════════
# §6.1 — the architect's LITERAL corrected G1 fence
#
#   "A capture FAILS G1 iff the LOOP-thread section of stacks.txt
#    contains the substring `daemon/repositories/instance/repository.py`
#    AND the substring `sqlalchemy` (case-insensitive).  The loop thread
#    is the section whose stack contains `asyncio/base_events.py` (do NOT
#    match the faulthandler header against threads.csv native_id — that is
#    the kernel TID, a different id space from faulthandler's pthread id).
#    The router file `daemon/routers/instances.py` ALONE IS NOT a
#    violation — dropping it is exactly what excludes the disclosed
#    UI-prefs residual (instances.py:496 -> instance_ui_prefs/repository.py),
#    which never contains daemon/repositories/instance/repository.py."
# ════════════════════════════════════════════════════════════════════════

LOOP_THREAD_MARKER = "asyncio/base_events.py"
FAMILY_REPO_PATH = "daemon/repositories/instance/repository.py"
SQLALCHEMY = "sqlalchemy"
#: The narrow pin's two markers (BASE positive control).
NARROW_FUNCTION = "list_child_ids_permanent"
ROUTER_INSTANCES = "daemon/routers/instances.py"
#: The DB-call-chain frame markers (the run of frames the fix off-loads).
DB_FRAME_MARKERS = ("sqlalchemy", "psycopg", "sqlmodel")


def parse_sections(text: str) -> list[tuple[str, str]]:
    """Split a faulthandler ``stacks.txt`` into (header, body) sections.

    The section header is the ``Thread 0x… (most recent call first):``
    line; the body is every frame line up to the next header.
    """
    sections: list[tuple[str, str]] = []
    current: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        if line.startswith(("Thread ", "Current thread ")):
            if current is not None:
                sections.append((current, "\n".join(buf)))
            current = line.strip()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections.append((current, "\n".join(buf)))
    return sections


def is_loop_section(body: str) -> bool:
    """True iff the section is the EVENT-LOOP thread (architect §6.1)."""
    return LOOP_THREAD_MARKER in body


def family_violation(body: str) -> bool:
    """The architect's LITERAL G1 predicate (loop-thread scoped)."""
    return FAMILY_REPO_PATH in body and SQLALCHEMY in body.lower()


def narrow_violation(body: str) -> bool:
    """The BASE positive-control pin: the N+1 callee + the router, together."""
    return NARROW_FUNCTION in body and ROUTER_INSTANCES in body


def db_chain_run(lines: list[str], repo_index: int) -> tuple[int, int]:
    """The contiguous DB-call-chain run ENDING at ``repo_index``.

    The stack dump is most-recent-call-first, so the DB frames sit
    ABOVE (earlier in the list than) the repository frame that called
    into them.  Returns the ``[start, end)`` slice of ``lines`` that the
    production fix moves off the event loop.
    """
    start = repo_index
    while start - 1 >= 0 and any(
        m in lines[start - 1].lower() for m in DB_FRAME_MARKERS
    ):
        start -= 1
    return start, repo_index + 1


def analyze_capture_sections(
    sections: list[tuple[str, str]],
) -> dict[str, Any]:
    """Per-capture facts under the architect's LITERAL predicates."""
    loop_secs = [(h, b) for h, b in sections if is_loop_section(b)]
    fam_any = [h for h, b in sections if family_violation(b)]
    fam_loop = [h for h, b in loop_secs if family_violation(b)]
    narrow_any = [h for h, b in sections if narrow_violation(b)]
    narrow_loop = [h for h, b in loop_secs if narrow_violation(b)]
    return {
        "threads_total": len(sections),
        "loop_sections": len(loop_secs),
        "loop_headers": [h for h, _ in loop_secs],
        "family_violation_any_thread": len(fam_any),
        "family_violation_loop_thread": len(fam_loop),
        "narrow_violation_any_thread": len(narrow_any),
        "narrow_violation_loop_thread": len(narrow_loop),
        "sections_with_both_any_thread": sum(
            1 for _, b in sections if narrow_violation(b)
        ),
    }


# ════════════════════════════════════════════════════════════════════════
# THE DISCRIMINATOR SELF-TEST (the non-vacuity proof)
#
# The HEAD mode asserts "no instances-path frame on the LOOP thread".
# A gate that cannot DETECT the violation would pass that assertion on
# a broken tree (a false green).  The self-test therefore exercises the
# predicates against a synthetic stack pair that mirrors the REAL
# captured shapes:
#
#   BROKEN  — the loop thread sits inside the sync-DB instances path
#             (exactly the measured BASE capture shape);
#   FIXED   — the same DB work runs on the bridge thread while the loop
#             thread is free (exactly the shape the production fix
#             produces, and exactly the shape that used to false-red the
#             thread-agnostic predicate).
# ════════════════════════════════════════════════════════════════════════

_BROKEN_STACK = """Thread 0x00007cbe16249740 [python] (most recent call first):
  File "/venv/site-packages/psycopg/connection.py", line 484 in wait
  File "/venv/site-packages/sqlalchemy/engine/default.py", line 952 in do_execute
  File "/venv/site-packages/sqlalchemy/orm/session.py", line 2351 in execute
  File "/venv/site-packages/sqlmodel/orm/session.py", line 75 in exec
  File "/repo/daemon/repositories/instance/repository.py", line 433 in list_child_ids_permanent
  File "/repo/daemon/services/instance_lifecycle.py", line 5115 in list_instances
  File "/repo/daemon/manager.py", line 14787 in list_instances
  File "/repo/daemon/routers/instances.py", line 476 in list_instances
  File "/usr/lib/python3.14/asyncio/base_events.py", line 2057 in _run_once
  File "/usr/lib/python3.14/asyncio/base_events.py", line 678 in run_forever
"""

_FIXED_STACK = """Thread 0x00007cbe16249760 [db-offload] (most recent call first):
  File "/venv/site-packages/psycopg/connection.py", line 484 in wait
  File "/venv/site-packages/sqlalchemy/engine/default.py", line 952 in do_execute
  File "/venv/site-packages/sqlalchemy/orm/session.py", line 2351 in execute
  File "/venv/site-packages/sqlmodel/orm/session.py", line 75 in exec
  File "/repo/daemon/repositories/instance/repository.py", line 433 in list_child_ids_permanent

Thread 0x00007cbe16249740 [python] (most recent call first):
  File "/repo/daemon/services/instance_lifecycle.py", line 5115 in list_instances
  File "/repo/daemon/manager.py", line 14787 in list_instances
  File "/repo/daemon/routers/instances.py", line 476 in list_instances
  File "/usr/lib/python3.14/asyncio/base_events.py", line 2057 in _run_once
  File "/usr/lib/python3.14/asyncio/base_events.py", line 678 in run_forever
"""

#: A prefs-only stack: the D-9 UI-prefs residual (router frame, but NOT
#: the instances repository) — the architect's explicit exclusion.
_PREFS_STACK = """Thread 0x00007cbe16249740 [python] (most recent call first):
  File "/venv/site-packages/psycopg/connection.py", line 484 in wait
  File "/venv/site-packages/sqlalchemy/engine/default.py", line 952 in do_execute
  File "/repo/daemon/repositories/instance_ui_prefs/repository.py", line 12 in get_prefs
  File "/repo/daemon/routers/instances.py", line 496 in list_instances
  File "/usr/lib/python3.14/asyncio/base_events.py", line 2057 in _run_once
"""


def discriminator_selftest() -> dict[str, Any]:
    """Prove the predicates separate BROKEN from FIXED (tree-independent)."""
    broken = analyze_capture_sections(parse_sections(_BROKEN_STACK))
    fixed = analyze_capture_sections(parse_sections(_FIXED_STACK))
    prefs = analyze_capture_sections(parse_sections(_PREFS_STACK))
    checks = {
        "broken_family_on_loop": broken["family_violation_loop_thread"] == 1,
        "broken_narrow_on_loop": broken["narrow_violation_loop_thread"] == 1,
        "fixed_family_on_loop": fixed["family_violation_loop_thread"] == 0,
        "fixed_narrow_on_loop": fixed["narrow_violation_loop_thread"] == 0,
        "fixed_family_off_loop_is_ignored": fixed["family_violation_any_thread"] == 1,
        "prefs_residual_is_not_a_violation": prefs["family_violation_loop_thread"] == 0,
        "prefs_residual_router_alone_is_not_a_violation":
            prefs["narrow_violation_loop_thread"] == 0,
        "loop_thread_is_detected": broken["loop_sections"] == 1 and fixed["loop_sections"] == 1,
    }
    return {
        "checks": checks,
        "ok": all(checks.values()),
        "broken": broken,
        "fixed": fixed,
        "prefs_residual": prefs,
    }


# ════════════════════════════════════════════════════════════════════════
# §6.2 — G2, the ONE-SIDED loaded-flat band
#
#   "LOADED flat(10) p95: p95 <= 0.230 s -> PASS; 0.230 s < p95 <=
#    0.510 s -> FAIL (degraded band); p95 > 0.510 s -> FAIL
#    (BASE-equivalent).  A measured p95 BELOW 0.067 s PASSES — record it
#    as an improvement."
# ════════════════════════════════════════════════════════════════════════

G2_PASS_MAX_S = 0.230
G2_DEGRADED_MAX_S = 0.510
G2_BASE_ANCHOR_S = 0.067
G2_EXPECTED_HEALTHY_S = (0.02, 0.10)

G2_VERDICT_PASS = "PASS"
G2_VERDICT_DEGRADED = "FAIL_DEGRADED"
G2_VERDICT_BASE_EQUIVALENT = "FAIL_BASE_EQUIVALENT"


def g2_verdict(p95_s: float | None) -> str:
    """The one-sided band verdict (a measured p95 BELOW the anchor PASSES)."""
    if p95_s is None:
        return "FAIL_NO_MEASUREMENT"
    if p95_s <= G2_PASS_MAX_S:
        return G2_VERDICT_PASS
    if p95_s <= G2_DEGRADED_MAX_S:
        return G2_VERDICT_DEGRADED
    return G2_VERDICT_BASE_EQUIVALENT


# ════════════════════════════════════════════════════════════════════════
# §6.3 — G9, the PER-SHAPE on-loop fence
#
#   "instances flat(10) => exactly {"SELECT instance_ui_prefs": 2} (the
#    D-9 residual; NO `SELECT instances` on-loop); instances BFS(100) =>
#    same; missions(20) => exactly {"SELECT instances": 2, "SELECT
#    job_queue_items": 1} (routed-out census #4 — explicitly exempt, not
#    a failure); every other shape => on-loop = 0."
#
# The prefs COUNT is a RANGE (1-2) here, not the literal 2: the plan
# (plan-overview.md §(d)) pins the D-9 residual as "1-2, pinned from the
# developer's HEAD measurement".  The architect's own §6.3 rationale is
# the SHAPE-SET (no `SELECT instances` on-loop), and the range preserves
# it exactly — asserting the literal 2 would false-red a legitimate
# single-pref merge.  The missions shape keeps the architect's literal
# exact counts (2 and 1): that census is unchanged by the lane.
# ════════════════════════════════════════════════════════════════════════

SHAPE_INSTANCES_FLAT = "instances_flat_badge"
SHAPE_INSTANCES_BFS = "instances_bfs_home"
SHAPE_WORK = "work"
SHAPE_DEFER = "defer_blocked"
SHAPE_JOBS_ACTIVE = "jobs_active_badge"
SHAPE_JOBS_RECENT = "jobs_recent_badge"
SHAPE_JOBS_PAGE = "jobs_page"
SHAPE_MISSIONS = "missions_badge"

#: The D-9 UI-prefs residual's statement-shape PREFIX.  MEASURED, not
#: assumed: the committed BASE baseline records the prefs work as TWO
#: statement shapes — ``SELECT instance_ui_prefs`` AND
#: ``SELECT instance_ui_prefs+instances`` (a prefs JOIN over instances) —
#: one of each per request (``raw/BASELINE.full.json``
#: per_endpoint.instances_flat_badge.statements_by_shape = 30 + 30 over 30
#: samples).  The architect's §6.3 literal writes the residual as
#: ``{"SELECT instance_ui_prefs": 2}``: the COUNT (2) is exactly right,
#: the SHAPE name is a transcription of one half of the measured pair.
#: The gate therefore asserts the architect's INTENT — "the on-loop
#: shape-set EQUALS the disclosed prefs residual, with NO
#: ``SELECT instances`` on-loop" — against the MEASURED shape names.
PREF_SHAPE_PREFIX = "SELECT instance_ui_prefs"

#: HEAD-mode on-loop RULES per shape.  Two forms:
#:   ``exact``  — the full shape->count mapping must match (used where the
#:                census is unchanged by the lane and its shapes are known);
#:   ``allow``  — an allow-list of shape prefixes + a forbidden exact shape
#:                + a total ceiling (used where the fix's own shape names
#:                are not yet pinned).
#: Every shape NOT listed here must have an EMPTY on-loop set.
HEAD_ON_LOOP_RULES: dict[str, dict[str, Any]] = {
    SHAPE_INSTANCES_FLAT: {
        "allow_prefixes": (PREF_SHAPE_PREFIX,),
        "forbid_exact": ("SELECT instances",),
        "max_on_loop": 2,
        "why": "the D-9 prefs residual only (architect §6.3); NO instances-path "
               "statement may stay on the loop",
    },
    SHAPE_INSTANCES_BFS: {
        "allow_prefixes": (PREF_SHAPE_PREFIX,),
        "forbid_exact": ("SELECT instances",),
        "max_on_loop": 2,
        "why": "same residue as flat(10) (architect §6.3)",
    },
    SHAPE_MISSIONS: {
        "exact": {"SELECT instances": (2, 2), "SELECT job_queue_items": (1, 1)},
        "why": "ROUTED OUT (census #4) — the missions census legitimately keeps "
               "SELECT instances on the loop and is EXPLICITLY EXEMPT "
               "(architect §6.3)",
    },
}

#: The BASE-mode expected on-loop FRACTION per shape (the census):
#: the instances/missions shapes issue EVERY round-trip on the loop;
#: the jobs shapes issue NONE (they run via asyncio.to_thread).
BASE_ON_LOOP_ALL: tuple[str, ...] = (
    SHAPE_INSTANCES_FLAT, SHAPE_INSTANCES_BFS, SHAPE_WORK,
    SHAPE_DEFER, SHAPE_MISSIONS,
)
BASE_ON_LOOP_NONE: tuple[str, ...] = (
    SHAPE_JOBS_ACTIVE, SHAPE_JOBS_RECENT, SHAPE_JOBS_PAGE,
)

#: The HEAD-mode round-trip BOUNDS (plan-overview.md §(d) table — the
#: O(1) claim: the per-row N+1 must collapse to a bounded count).
#: The plan's values are projections written with "~" anchors; the GATE
#: asserts the ORDER-OF-MAGNITUDE bound (O(N) -> O(1)), which is the
#: scientific claim, and REPORTS the projection band separately.  An
#: UPPER bound also cannot false-red a fix that lands BETTER than
#: projected — the same failure class the architect corrected in §6.2.
HEAD_ROUND_TRIP_MAX: dict[str, int] = {
    SHAPE_INSTANCES_FLAT: 6,     # plan "~5" / architect "<=6"
    SHAPE_INSTANCES_BFS: 12,     # plan "~8"
    SHAPE_WORK: 12,              # plan "~6-10"
    SHAPE_JOBS_RECENT: 25,       # plan "~10-25"
    SHAPE_JOBS_PAGE: 30,         # plan "~15-30"
    SHAPE_DEFER: 0,              # sentinel: UNCHANGED (see below)
    SHAPE_MISSIONS: 0,           # sentinel: UNCHANGED (see below)
}
#: Shapes whose round-trip COUNT is unchanged by the lane (the plan says
#: defer-blocked "89 - UNCHANGED" and missions "3 - unchanged"): the gate
#: asserts the EXACT BASE formula, not a bound.
HEAD_COUNT_UNCHANGED: tuple[str, ...] = (SHAPE_DEFER, SHAPE_MISSIONS)


def head_round_trip_expectation(shape: str) -> dict[str, Any]:
    """The HEAD-mode round-trip expectation for one shape."""
    if shape in HEAD_COUNT_UNCHANGED:
        return {"kind": "unchanged", "max": None}
    return {"kind": "bounded", "max": HEAD_ROUND_TRIP_MAX.get(shape)}


# ════════════════════════════════════════════════════════════════════════
# §6.4 — G4, the PAIRED loop-lag comparison
#
#   "TICK = 0.5 s, STALL = samples > TICK + 0.25 s (0.75 s).  G4 PASS
#    iff, per profile: (a) HEAD's STALL count <= BASE's STALL count;
#    (b) HEAD's max <= BASE's max (no tolerance); (c) HEAD's p95 <=
#    BASE's p95 + one TICK — the bare p95 comparison is forbidden;
#    (d) non-vacuity: each profile's sample count >= 0.5 x BASE's."
# ════════════════════════════════════════════════════════════════════════

G4_TICK_S = 0.5
G4_STALL_MARGIN_S = 0.25


def g4_stall_count(series: list[float]) -> int:
    """Samples above the stall threshold (TICK + margin)."""
    threshold = G4_TICK_S + G4_STALL_MARGIN_S
    return sum(1 for v in series if v > threshold)


def g4_profile_verdict(head_series: list[float], base_series: list[float]) -> dict[str, Any]:
    """The per-profile paired verdict (architect §6.4)."""
    from common import percentile  # local import: avoids a cycle at module load

    if not head_series or not base_series:
        return {
            "ok": False,
            "reason": "empty series (non-vacuity failure)",
            "head_samples": len(head_series),
            "base_samples": len(base_series),
        }
    head_stalls = g4_stall_count(head_series)
    base_stalls = g4_stall_count(base_series)
    head_max = max(head_series)
    base_max = max(base_series)
    head_p95 = percentile(head_series, 95)
    base_p95 = percentile(base_series, 95)
    checks = {
        "stall_count_not_worse": head_stalls <= base_stalls,
        "max_not_worse": head_max <= base_max,
        "p95_within_one_tick": head_p95 <= base_p95 + G4_TICK_S,
        "samples_non_vacuous": len(head_series) >= 0.5 * len(base_series),
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "head_samples": len(head_series),
        "base_samples": len(base_series),
        "head_stalls": head_stalls,
        "base_stalls": base_stalls,
        "head_max_s": round(head_max, 4),
        "base_max_s": round(base_max, 4),
        "head_p95_s": round(head_p95, 4),
        "base_p95_s": round(base_p95, 4),
    }


# ════════════════════════════════════════════════════════════════════════
# THE HEAD-FIX SIMULATION (evidence level, rule-derived)
#
# Each rule below is the PRODUCTION FIX's published semantics (the plan's
# §(d) decomposition + the architect's D-9 residual disclosure).  The
# OUTPUT is always computed from the MEASURED input — never written as a
# constant — and every transform records its rule + derivation so a
# reviewer can check the rule against the plan instead of against the
# assertion.
# ════════════════════════════════════════════════════════════════════════

SIMULATION_DISCLAIMER = (
    "SIMULATED HEAD-FIX EVIDENCE — the tree under test was NOT fixed. "
    "This run transformed the MEASURED BASE evidence with the production "
    "fix's published semantics. It is NOT a measurement and NOT gate "
    "evidence; it exists only to exercise the HEAD mode before the "
    "developer's fix lands."
)


def _move_db_chain_off_loop(
    sections: list[tuple[str, str]],
) -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
    """Move the instances-path DB chain OUT of the loop-thread section.

    Rule S1 (the fix's semantics): the sync DB work moves to the bridge
    pool, so the loop thread's stack no longer carries the
    instances-repository frame nor the DB frames above it.  The loop
    frames (and the router frame) STAY on the loop.
    """
    out: list[tuple[str, str]] = []
    moved: list[dict[str, Any]] = []
    for header, body in sections:
        if not is_loop_section(body):
            out.append((header, body))
            continue
        lines = body.splitlines()
        repo_indexes = [
            i for i, ln in enumerate(lines) if FAMILY_REPO_PATH in ln
        ]
        keep: list[str] = []
        drop: set[int] = set()
        for idx in repo_indexes:
            start, end = db_chain_run(lines, idx)
            drop.update(range(start, end))
            moved.append({
                "from": header,
                "frames": len(range(start, end)),
                "repo_frame": lines[idx].strip(),
            })
        out.append((header, "\n".join(ln for i, ln in enumerate(lines) if i not in drop)))
    return out, moved


def simulate_captures(captures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply rule S1 to the parsed capture sections."""
    out = []
    for cap in captures:
        cap = dict(cap)
        sections = cap.get("sections")
        if not sections:
            out.append(cap)
            continue
        new_sections, moved = _move_db_chain_off_loop(sections)
        cap["sections"] = new_sections
        cap["facts"] = analyze_capture_sections(new_sections)
        cap["simulated_moved_frames"] = moved
        out.append(cap)
    return out


def norm_by_shape(raw: Any) -> dict[str, dict[str, int]]:
    """Normalize ``statements_by_shape`` to ``{shape: {count, on_loop}}``.

    Tolerates the PRE-REPAIR aggregate (``{shape: int}``, no on-loop
    split) so the simulation can be validated against the committed
    historical baseline artifact.
    """
    out: dict[str, dict[str, int]] = {}
    for shape, value in (raw or {}).items():
        if isinstance(value, dict):
            out[shape] = {
                "count": int(value.get("count", 0)),
                "on_loop": int(value.get("on_loop", 0)),
            }
        else:
            out[shape] = {"count": int(value or 0), "on_loop": 0}
    return out


def simulate_shape_round_trips(
    shape: str,
    total: int,
    by_shape: dict[str, dict[str, Any]],
    body_facts: dict[str, Any],
) -> dict[str, Any]:
    """Rule S2 — the per-shape round-trip count after the fix.

    * instances family (flat / BFS): the per-row children N+1 collapses
      to ONE batched select  =>  count' = count - children_calls + 1,
      where children_calls = the number of rows whose children array was
      fetched (MEASURED from the response body).
    * work / jobs family: the per-row instance lookups collapse into the
      batched selects of the plan's A12/D-B decomposition ("one job_ids
      IN-select + one instance batch + one mission batch")
      =>  count' = (count - instances_selects) + 3.
    * defer-blocked / missions: the count is UNCHANGED by the lane (the
      plan says so explicitly).
    """
    instances_selects = int((by_shape.get("SELECT instances") or {}).get("count", 0))
    if shape in (SHAPE_INSTANCES_FLAT, SHAPE_INSTANCES_BFS):
        children_calls = int(body_facts.get("rows") or 0)
        new_total = max(0, total - children_calls + 1)
        return {
            "total": new_total,
            "by_shape": _scale_instances_selects(by_shape, children_calls),
            "rule": (
                f"S2.instances: count' = {total} - {children_calls} children "
                f"(rows, measured) + 1 batched children select = {new_total}"
            ),
        }
    # NOTE: jobs_active is EXCLUDED — its enrichment already runs through
    # the batched Job branch at BASE (BASELINE §2: 7 rts, 0 on-loop), so the
    # lane does not collapse a per-row N+1 there.
    if shape in (SHAPE_WORK, SHAPE_JOBS_RECENT, SHAPE_JOBS_PAGE):
        new_total = max(0, (total - instances_selects) + 3)
        return {
            "total": new_total,
            "by_shape": _collapse_instance_selects(by_shape, instances_selects, 2),
            "rule": (
                f"S2.batch: count' = ({total} - {instances_selects} per-row "
                f"instance lookups) + 3 batched selects (job_ids IN-select + "
                f"instance batch + mission batch) = {new_total}"
            ),
        }
    return {
        "total": total,
        "by_shape": dict(by_shape),
        "rule": f"S2.unchanged: count' = {total} (the lane does not change it)",
    }


def _scale_instances_selects(
    by_shape: dict[str, dict[str, Any]], children_calls: int
) -> dict[str, dict[str, Any]]:
    """The instances-path selects survive as the page/level selects + 1 batch."""
    out: dict[str, dict[str, Any]] = {}
    for shape, sv in by_shape.items():
        count = int(sv.get("count", 0))
        if shape == "SELECT instances":
            count = max(0, count - children_calls + 1)
        out[shape] = {**sv, "count": count}
    return out


def _collapse_instance_selects(
    by_shape: dict[str, dict[str, Any]], instances_selects: int, keep: int
) -> dict[str, dict[str, Any]]:
    """The per-row instance lookups collapse into ``keep`` batched selects."""
    out: dict[str, dict[str, Any]] = {}
    for shape, sv in by_shape.items():
        count = int(sv.get("count", 0))
        if shape == "SELECT instances":
            count = keep
        out[shape] = {**sv, "count": count}
    return out


def simulate_on_loop(
    shape: str,
    by_shape: dict[str, dict[str, Any]],
    total: int,
) -> dict[str, Any]:
    """Rule S3 — the on-loop attribution after the fix.

    The instances-path DB work is off-loaded; the D-9 UI-prefs merge and
    the routed-out missions census STAY on the loop (the disclosed
    residuals).  Every other shape ends with NOTHING on the loop.
    """
    if shape in (SHAPE_MISSIONS, SHAPE_JOBS_ACTIVE, SHAPE_JOBS_RECENT,
                 SHAPE_JOBS_PAGE):
        measured = sum(int(sv.get("on_loop", 0)) for sv in by_shape.values())
        return {
            "on_loop": measured,
            "by_shape": {s: dict(sv) for s, sv in by_shape.items()},
            "rule": (
                "S3.routed-out: the on-loop attribution is unchanged "
                f"({measured} statement(s) stay on the loop, as MEASURED — "
                f"the fix does not touch this shape)"
            ),
        }
    on_loop_by_shape: dict[str, int] = {
        s: int(sv.get("count", 0))
        for s, sv in by_shape.items() if s.startswith(PREF_SHAPE_PREFIX)
    }
    return {
        "on_loop": sum(on_loop_by_shape.values()),
        "by_shape": {
            s: dict(sv, on_loop=on_loop_by_shape.get(s, 0))
            for s, sv in by_shape.items()
        },
        "rule": (
            "S3.offload: every instances-path statement moves off the loop; "
            "the D-9 UI-prefs merge stays on it"
            + (f" ({sum(on_loop_by_shape.values())} prefs statement(s): "
               f"{sorted(on_loop_by_shape)})"
               if on_loop_by_shape else " (no prefs statement in this shape)")
        ),
    }


def simulate_lag_series(series: list[float]) -> tuple[list[float], str]:
    """Rule S4 — the loop-lag series after the fix.

    The probe measures the interval between successive loop-callback
    executions.  With no handler holding the loop, that interval IS the
    watchdog tick period — which the BASE series' p50 already measures
    (the tick lattice).  The fix therefore REMOVES the handler-blocking
    component: every sample is clamped to the measured tick floor.
    """
    if not series:
        return list(series), "S4.lag: empty series (nothing to clamp)"
    floor = sorted(series)[len(series) // 2]
    clamped = [min(v, floor) for v in series]
    return clamped, (
        f"S4.lag: every sample clamped to the measured tick floor "
        f"p50={floor:.4f}s (the watchdog tick lattice) — the "
        f"handler-blocking component removed"
    )


def simulate_report(report: dict[str, Any]) -> dict[str, Any]:
    """Apply S2/S3/S4 to the collected report (in place, returns it)."""
    prov: dict[str, Any] = {"rules": []}

    per_shape = report.get("per_endpoint") or {}
    for shape, row in per_shape.items():
        facts = row.get("body_facts") or {}
        rt = row.get("round_trips") or {}
        # PER-REQUEST units.  ``round_trips.mean`` is already per request,
        # but ``statements_by_shape`` is SUMMED over the phase's samples —
        # mixing the two units silently produced a 10-30x error in the
        # first dry-run of this repair (caught before any gate ran).
        samples = max(1, int(row.get("samples") or 1))
        total = int(round(float(rt.get("mean") or 0)))
        by_shape = {
            k: {
                "count": int(round(v["count"] / samples)),
                "on_loop": int(round(v["on_loop"] / samples)),
            }
            for k, v in norm_by_shape(row.get("statements_by_shape")).items()
        }
        sim_total = simulate_shape_round_trips(shape, total, by_shape, facts)
        sim_on = simulate_on_loop(shape, sim_total["by_shape"], sim_total["total"])
        row["round_trips"] = {
            "mean": sim_total["total"], "min": sim_total["total"],
            "max": sim_total["total"],
        }
        row["on_loop_round_trips"] = {
            "mean": sim_on["on_loop"], "max": sim_on["on_loop"],
        }
        row["statements_by_shape"] = sim_on["by_shape"]
        row["simulated"] = {
            "count_rule": sim_total["rule"],
            "on_loop_rule": sim_on["rule"],
        }
        prov["rules"].append({
            "shape": shape,
            "count": sim_total["rule"],
            "on_loop": sim_on["rule"],
        })

    # G2: the LOADED flat leg's p95 was inflated purely by loop queueing
    # (BASELINE §3); with the loop free it falls back to the MEASURED
    # unloaded floor of the same run.
    badge = report.get("profile_badge") or {}
    badge_lat = badge.get("latencies") or {}
    floor_row = (per_shape.get(SHAPE_INSTANCES_FLAT) or {}).get("latency") or {}
    flat_loaded = (badge_lat.get(SHAPE_INSTANCES_FLAT) or {})
    floor_p95_ms = floor_row.get("p95_ms")
    if flat_loaded and floor_p95_ms is not None:
        before = flat_loaded.get("p95_ms")
        flat_loaded["p95_ms"] = floor_p95_ms
        flat_loaded["p50_ms"] = floor_row.get("p50_ms")
        flat_loaded["max_ms"] = floor_row.get("max_ms")
        flat_loaded["simulated"] = True
        prov["rules"].append({
            "shape": "G2.loaded-flat-band",
            "count": (
                f"S2.g2: the loaded flat-leg p95 {before} ms is replaced by "
                f"the MEASURED unloaded floor p95 {floor_p95_ms} ms of the "
                f"same run (the BASE inflation was loop queueing, BASELINE §3)"
            ),
        })

    # G4: the lag series per profile.
    for key in ("profile_badge", "profile_home", "profile_jobs_page",
                "profile_capture_badge", "profile_capture_home"):
        prof = report.get(key) or {}
        lag = prof.get("lag") or {}
        series = lag.get("series") or []
        if not series:
            continue
        clamped, rule = simulate_lag_series([float(v) for v in series])
        lag["series"] = [round(v, 4) for v in clamped]
        lag["p50_s"], lag["p95_s"], lag["max_s"] = _lag_stats(clamped)
        lag["simulated"] = True
        prov["rules"].append({
            "shape": f"G4.lag[{key}]",
            "count": rule,
        })

    report["simulation"] = {
        "applied": True,
        "kind": "head",
        "disclaimer": SIMULATION_DISCLAIMER,
        "provenance": prov,
    }
    return report


def _lag_stats(series: list[float]) -> tuple[float, float, float]:
    from common import percentile

    return (
        round(percentile(series, 50), 4),
        round(percentile(series, 95), 4),
        round(max(series), 4),
    )


def deepcopy_report(report: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(report)


# ════════════════════════════════════════════════════════════════════════
# Per-mode expectation summary (for the report + the README)
# ════════════════════════════════════════════════════════════════════════

MODE_SUMMARY: dict[str, dict[str, str]] = {
    MODE_BASE: {
        "G1_family": "the LOOP thread carries the instances-path DB frames (>=1 capture)",
        "G1_narrow": "the N+1 callee + the router share the LOOP thread (>=1 capture, full profile)",
        "G2_band": "the loaded flat leg is NOT in the healthy band (the defect is measurable)",
        "G4_lag": "paired comparison against the BASE reference run",
        "G9_on_loop": "the instances/missions shapes issue EVERY round-trip on the loop; the jobs shapes issue NONE",
        "G9_counts": "the BASE census formulas hold EXACTLY (3+limit, 4+levels+rows, 2+holders, 3)",
    },
    MODE_HEAD: {
        "G1_family": "NO instances-path DB frame on the LOOP thread (0 captures)",
        "G1_narrow": "the N+1 callee is NOT on the LOOP thread (0 captures)",
        "G2_band": "the loaded flat leg p95 <= 0.230 s (a value BELOW 0.067 s passes as an improvement)",
        "G4_lag": "paired comparison against the BASE reference run (STALL/max/p95+1 tick)",
        "G9_on_loop": "per-shape on-loop SET equals the disclosed residual; every other shape is empty",
        "G9_counts": "the per-request round-trips are O(1)/bounded (the plan's §(d) upper anchors)",
    },
}


def mode_summary(mode: str) -> dict[str, str]:
    return dict(MODE_SUMMARY[mode])


_NAME_RE = re.compile(r"^[a-z0-9_]+$")


def is_shape_name(name: str) -> bool:
    return bool(_NAME_RE.match(name))
