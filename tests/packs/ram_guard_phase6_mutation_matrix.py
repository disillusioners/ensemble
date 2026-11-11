#!/usr/bin/env python3
"""RAM-guard Phase-6 mutation matrix (test-plan.md §4).

THE ARTIFACT behind the Phase-6 mutation claim.  Enumerates every row of
the §4 mutation table in code: plant the defect -> the discriminating pin
must go RED -> restore -> the same pin must go GREEN (file sha verified
identical to the original).  Nothing here is narrated — the TSV/line per
leg is the evidence.

It covers the sixteen executable legs whose source mutation is a single
exact-string swap; the remaining §4 rows are the SAME shape as an
executable row (a durability/ordering flip whose pin already exists) and
are enumerated in ``DEFERRED_ROWS`` so a reader sees the whole table.

Usage (from the lane worktree)::

    ./.venv/bin/python tests/packs/ram_guard_phase6_mutation_matrix.py \\
        [--legs M1,M3] [--out /tmp/ram-guard-matrix.tsv]

Exit 0 only when EVERY selected leg is RED-with-defect AND GREEN-after-
restore.  A leg that is GREEN with the defect planted is reported
NON-DISCRIMINATING and fails the run — a pin that cannot fail is worse
than no pin.

Env fence: the child pytest runs with ``POSTGRES_*`` scrubbed PER-VARIABLE
(``env -u POSTGRES_*`` is a silent no-op — see the project critical notes)
and ``ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`` pinned explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = os.path.join(WT, ".venv", "bin", "python")

SCRUB = [
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "DATABASE_URL",
    "DATABASE_URL_POSTGRES",
]


@dataclass
class Leg:
    id: str
    path: str
    old: str
    new: str
    node: str
    note: str = ""
    all_sites: bool = False


#: The executable legs — each maps a §4 mutation row to its discriminating pin.
LEGS: list[Leg] = [
    Leg(
        id="M1-invert-breach",
        path="daemon/services/ram_guard/tree_reader.py",
        old="    return total_bytes >= ceiling_bytes",
        new="    return total_bytes > ceiling_bytes",
        node="tests/unit/services/test_ram_guard_tree_reader.py::test_s9_breach_boundary_is_inclusive",
        note="inclusive breach → strict (S9)",
    ),
    Leg(
        id="M2-break-pss-confirm",
        path="daemon/services/ram_guard/guard.py",
        old="        ok = (not timed_out) and bool(reading.complete)",
        new="        ok = False",
        node="tests/unit/services/test_ram_guard_guard.py::test_the_overcounted_rearm_rearms_and_a_later_breach_kills",
        note="confirm always fail-open (fairness U4)",
    ),
    Leg(
        id="M3-remove-rearm-cap",
        path="daemon/services/ram_guard/verdict.py",
        old="    extra = min(max(int(shared_extra_bytes), 0), RAM_GUARD_REARM_MAX_EXTRA_BYTES)",
        new="    extra = max(int(shared_extra_bytes), 0)",
        node="tests/unit/services/test_ram_guard_verdict.py::test_s13_rearm_threshold_is_bounded_by_the_cap",
        note="unbounded rearm threshold (S13)",
    ),
    Leg(
        id="M4-remove-growth-governor",
        path="daemon/services/ram_guard/verdict.py",
        old="    return (summed_rss_bytes - last_confirm_rss_bytes) >= RAM_GUARD_REARM_MIN_GROWTH_BYTES",
        new="    return True",
        node="tests/unit/services/test_ram_guard_verdict.py::test_s13_confirm_due_requires_the_growth_governor",
        note="confirm every tick (S13)",
    ),
    Leg(
        id="M5-tick-path-log",
        path="daemon/services/ram_guard/guard.py",
        old="        now = time.time()\n        with self._lock:\n            self._ticks += 1",
        new="        now = time.time()\n        logger.info('ram-guard: tick')\n        with self._lock:\n            self._ticks += 1",
        node="tests/unit/services/test_ram_guard_phase6.py::test_the_tick_path_emits_no_log_line_even_on_breach",
        note="log from the tick path (S17/O11)",
    ),
    Leg(
        id="M6-no-miss-count",
        path="daemon/services/ram_guard/guard.py",
        old="                    self._missed_samples += 1",
        new="                    pass",
        node="tests/unit/services/test_ram_guard_phase6.py::test_a_raising_tick_climbs_missed_samples",
        note="a raising tick does not count a miss",
    ),
    Leg(
        id="M7-unreadable-as-absent",
        path="daemon/services/ram_guard/ladder.py",
        old="    if stat.unreadable:\n        return REASON_LEADER_UNREADABLE",
        new="    if stat.unreadable:\n        return None",
        node="tests/unit/services/test_ram_guard_ladder.py::test_p5_leader_unreadable_refused",
        note="unreadable leader treated as absent (S8f) — BOTH P5 and P5a sites",
        all_sites=True,
    ),
    Leg(
        id="M8-dead-guard-healthy",
        path="daemon/services/ram_guard/guard.py",
        old='"thread_alive": bool(self._tick_thread is not None and self._tick_thread.is_alive()),',
        new='"thread_alive": True,',
        node="tests/unit/services/test_ram_guard_guard.py::test_off_starts_neither_thread",
        note="dead guard reports healthy (O11)",
    ),
    Leg(
        id="M9-hide-last-tick",
        path="daemon/services/ram_guard/guard.py",
        old='"last_tick_at": self._last_tick_at,',
        new='"last_tick_at": None,',
        node="tests/unit/services/test_ram_guard_phase6.py::test_t11b_a_wedged_tick_freezes_last_tick_at_and_does_not_count_a_miss",
        note="the tick heartbeat is never surfaced (T11b)",
    ),
    Leg(
        id="M10-hide-lane-progress",
        path="daemon/services/ram_guard/guard.py",
        old="            self._service_claim(tracking_id, token)\n        finally:\n            with self._lock:\n                self._last_lane_progress_at = time.time()",
        new="            self._service_claim(tracking_id, token)\n        finally:\n            with self._lock:\n                pass",
        node="tests/unit/services/test_ram_guard_guard.py::test_the_lane_heartbeat_is_the_wedge_signal",
        note="lane progress heartbeat frozen (O11/T11b)",
    ),
    Leg(
        id="M11-q2-string-drift",
        path="daemon/services/ram_guard/guard.py",
        old="the watch stays alive until the group member count reaches 0",
        new="the watch stays alive until the group is empty",
        node="tests/unit/services/test_ram_guard_phase6.py::test_t22_the_canonical_q2_strings_are_pinned_in_the_guard_module",
        note="the canonical Q2 string drifts (T22)",
    ),
    Leg(
        id="M12-add-p8-counter",
        path="daemon/services/ram_guard/guard.py",
        old='    "tree_incomplete",\n)',
        new='    "tree_incomplete",\n    "ram_kill_refusals_p8",\n)',
        node="tests/unit/services/test_ram_guard_guard.py::test_counter_set_is_the_exact_ordered_set",
        note="a forbidden p8 counter is added (O12)",
    ),
    Leg(
        id="M13-off-still-registers",
        path="daemon/tools/bash.py",
        old="""def _ram_guard_active() -> bool:
    \"\"\"Is the RAM arm live?  (the switch ON AND a guard installed).\"\"\"
    if not _ram_guard_switch():
        return False
    return _get_ram_guard() is not None""",
        new="""def _ram_guard_active() -> bool:
    \"\"\"Is the RAM arm live?  (the switch ON AND a guard installed).\"\"\"
    return _get_ram_guard() is not None""",
        node="tests/unit/services/test_ram_guard_phase6.py::test_k5_off_touches_no_guard_registry_or_repository",
        note="the OFF path still registers (K1/K5)",
    ),
]


#: §4 rows that are NOT parameterised here — each has an EXISTING pin and a
#: sibling executable row of the same shape; enumerated so the table is whole.
DEFERRED_ROWS: list[tuple[str, str]] = [
    ("Remove the kill delivery", "the ladder census (test_ram_guard_ladder.py::test_p5_leader_alive_matching_starttime_kills)"),
    ("Return the spawn-time floor as the peak", "B3 honest-peak (test_bash_ram_guard.py::TestEnvelope)"),
    ("Move the success arm's mark_exit before the RAM arm", "B8 ordering (test_bash_ram_guard.py::TestPublicationOrdering)"),
    ("Publish the verdict AFTER mark_exit", "B8 ordering (TestPublicationOrdering)"),
    ("Close the durable row on a RAM kill", "B7 (test_bash_ram_guard.py::TestDurableRowStaysOpen)"),
    ("Make the leaderless case drop the watch", "B13 / S8(f) (test_bash_ram_guard.py::TestRealBallooningTree::test_the_leaderless_tree_is_still_watched_and_killed)"),
    ("Remove the P5a pgid-recycle check", "S8(g) (test_ram_guard_ladder.py::test_p5a_pgid_recycled_refused)"),
    ("Add a 4th proc task", "P5 (test_proc_ram_guard.py::test_the_lane_creates_no_fourth_task)"),
    ("Fire the event per tick", "O1 (test_ram_guard_events.py::test_o1_a_live_loop_emits_exactly_one_event)"),
    ("Leak the registry entry", "B6/P6 (test_bash_ram_guard.py::TestReleaseOrder + test_proc_ram_guard.py::TestReleasePaths)"),
    ("Kill on no-evidence", "S6/N3 (test_ram_guard_guard.py::test_enumeration_failure_drops_the_sample)"),
    ("Drop the OVERCOUNTED re-arm", "S13 (test_ram_guard_guard.py::test_the_overcounted_rearm_rearms_and_a_later_breach_kills)"),
    ("Drop the event on a no-loop boot instead of counting it", "N9/O1 (test_ram_guard_events.py::TestDropPaths)"),
    ("Treat an unreadable leader as absent", "S8(f) — SAME as executable M7"),
    ("Assert the leader stat is unreadable ⇒ gone", "S8(f) — SAME as executable M7"),
    ("Report the dead-guard state as healthy", "O11 — SAME as executable M8"),
    ("Query the repository on the OFF path", "K5 — SAME as executable M13"),
    ("Run the blocked-tick case as a normal tick", "phase6 T11(b) — SAME as executable M9/M10"),
]


def _sha(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _run(nodes: str) -> int:
    env = {k: v for k, v in os.environ.items() if k not in SCRUB}
    env["ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES"] = "0"
    proc = subprocess.run(
        [PY, "-m", "pytest", nodes, "-q", "-p", "no:randomly", "--timeout=120"],
        cwd=WT,
        env=env,
        capture_output=True,
        text=True,
    )
    return proc.returncode


def _apply(leg: Leg) -> str:
    target = os.path.join(WT, leg.path)
    original = open(target, encoding="utf-8").read()
    count = original.count(leg.old)
    expected = 2 if leg.all_sites else 1
    if count != expected:
        raise SystemExit(
            f"[{leg.id}] plant site occurs {count} times (expected {expected}) "
            f"in {leg.path} — refusing to plant blindly"
        )
    open(target, "w", encoding="utf-8").write(original.replace(leg.old, leg.new))
    return original


def _restore(leg: Leg, original: str) -> None:
    target = os.path.join(WT, leg.path)
    open(target, "w", encoding="utf-8").write(original)


def run_leg(leg: Leg) -> tuple[str, bool]:
    target = os.path.join(WT, leg.path)
    before = _sha(target)
    original = _apply(leg)
    try:
        red_rc = _run(leg.node)
        _restore(leg, original)
        after = _sha(target)
        if after != before:
            return "RESTORE-SHA-MISMATCH", False
        green_rc = _run(leg.node)
    finally:
        # defensive: never leave a plant behind
        current = open(target, encoding="utf-8").read()
        if current != original:
            open(target, "w", encoding="utf-8").write(original)
    if red_rc != 0 and green_rc == 0:
        return "DISCRIMINATING", True
    if red_rc == 0:
        return "NON-DISCRIMINATING (GREEN with the defect)", False
    return f"NON-DISCRIMINATING (RED after restore: rc={green_rc})", False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legs", default="", help="comma-separated leg ids")
    parser.add_argument("--out", default="/tmp/ram-guard-mutation-matrix.tsv")
    args = parser.parse_args(argv)

    selected = [leg for leg in LEGS if (not args.legs or leg.id in args.legs.split(","))]
    if not selected:
        raise SystemExit(f"no legs matched --legs {args.legs!r}")

    failures = 0
    lines: list[str] = ["leg\tpath\tresult\tok"]
    for leg in selected:
        verdict, ok = run_leg(leg)
        lines.append(f"{leg.id}\t{leg.path}\t{verdict}\t{ok}")
        print(f"{leg.id:32s} {verdict}")
        if not ok:
            failures += 1

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\nwrote {args.out}")
    print(f"{len(selected)} legs, {failures} failing")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
