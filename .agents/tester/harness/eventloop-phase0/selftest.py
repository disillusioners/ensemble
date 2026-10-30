#!/usr/bin/env python3
"""selftest.py — the FAST instrument check (no daemon boot, seconds).

Proves the repaired gate machinery is non-vacuous WITHOUT running the
harness.  This is the "does the instrument discriminate?" check the
functional gate can re-run any time; the slower end-to-end truth table
(``run_truth_table.sh``) is the run-level version of the same claim.

Checks
------
 1. the DISCRIMINATOR fixture: the synthetic BROKEN stack fires the G1
    predicates, the synthetic FIXED stack does not (and its bridge-thread
    frames are correctly IGNORED), and the D-9 UI-prefs residual is not a
    violation;
 2. the predicates applied to the REAL committed capture evidence —
    every capture's loop-thread family/narrow counts are recomputed from
    ``raw/evidence/*/stacks.txt`` (a BASE tree MUST trip them);
 3. the same predicates after the HEAD-fix SIMULATION — the loop-thread
    violations must go to ZERO while the bridge-side frames survive;
 4. the G2 one-sided band table (including the sub-anchor improvement
    case the two-sided band used to false-red);
 5. the G4 paired comparison (a better FIXED pair passes, a worse HEAD
    pair fails, an empty series fails non-vacuity);
 6. the G9 per-shape expectation tables are mutually exclusive between
    the modes (no shape can be expected on-loop in head mode and
    "unchanged" in the same breath).

Exit 0 iff every check passes.  This script touches nothing: read-only
over the committed artifacts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESS_DIR))

import modes  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} — {detail}")


def check_discriminator() -> None:
    print("\n[1] discriminator fixture (tree-independent)")
    st = modes.discriminator_selftest()
    for name, ok in st["checks"].items():
        check(f"discriminator.{name}", ok)


def _capture_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        d for d in root.iterdir() if d.is_dir() and d.name != "boot-records"
    )


def check_real_evidence() -> None:
    print("\n[2] the REAL committed capture evidence (BASE tree)")
    root = HARNESS_DIR / "raw" / "evidence"
    dirs = [d for d in _capture_dirs(root) if (d / "stacks.txt").is_file()]
    check("evidence.present", len(dirs) >= 1, f"{len(dirs)} capture(s) under {root}")
    if not dirs:
        return
    totals = {"fam_loop": 0, "narrow_loop": 0, "fam_any": 0, "loop_seen": 0}
    for d in dirs:
        secs = modes.parse_sections((d / "stacks.txt").read_text(errors="replace"))
        facts = modes.analyze_capture_sections(secs)
        totals["fam_loop"] += facts["family_violation_loop_thread"]
        totals["narrow_loop"] += facts["narrow_violation_loop_thread"]
        totals["fam_any"] += facts["family_violation_any_thread"]
        totals["loop_seen"] += 1 if facts["loop_sections"] else 0
        print(
            f"    {d.name[:48]:<48} loop_secs={facts['loop_sections']} "
            f"family(loop)={facts['family_violation_loop_thread']} "
            f"narrow(loop)={facts['narrow_violation_loop_thread']} "
            f"family(any)={facts['family_violation_any_thread']}"
        )
    # A BASE tree MUST trip the loop-thread family pin.
    check("evidence.base_tree_trips_family_pin", totals["fam_loop"] >= 1,
          f"{totals['fam_loop']}/{len(dirs)} captures")
    check("evidence.loop_thread_visible", totals["loop_seen"] >= 1,
          f"{totals['loop_seen']}/{len(dirs)} captures contain the loop thread")


def check_simulation_flips_the_pin() -> None:
    print("\n[3] the HEAD-fix SIMULATION flips the pin (loop-scoped only)")
    root = HARNESS_DIR / "raw" / "evidence"
    dirs = [d for d in _capture_dirs(root) if (d / "stacks.txt").is_file()]
    if not dirs:
        check("simulation.evidence_present", False, "no capture evidence")
        return
    records = []
    for d in dirs:
        secs = modes.parse_sections((d / "stacks.txt").read_text(errors="replace"))
        records.append({"capture_id": d.name, "sections": secs})
    before = [modes.analyze_capture_sections(r["sections"]) for r in records]
    after = modes.simulate_captures(records)
    after_facts = [r["facts"] for r in after]
    moved = sum(len(r.get("simulated_moved_frames") or []) for r in after)
    check(
        "simulation.loop_violations_go_to_zero",
        sum(f["family_violation_loop_thread"] for f in after_facts) == 0
        and sum(f["narrow_violation_loop_thread"] for f in after_facts) == 0,
        f"before: family(loop)={sum(f['family_violation_loop_thread'] for f in before)}, "
        f"narrow(loop)={sum(f['narrow_violation_loop_thread'] for f in before)}; "
        f"after: 0 / 0",
    )
    check(
        "simulation.bridge_side_frames_survive",
        sum(f["family_violation_any_thread"] for f in after_facts) >= 1,
        f"{sum(f['family_violation_any_thread'] for f in after_facts)} section(s) "
        f"still carry the family frames OUTSIDE the loop thread — they must NOT "
        f"count (the architect's §6.1 false-red)",
    )
    check("simulation.moved_frames_present", moved >= 1,
          f"{moved} DB-call-chain run(s) moved off the loop thread")
    # the loop thread must remain IDENTIFIABLE after the move (a repair that
    # destroyed the loop marker would make the absence assertion vacuous)
    check(
        "simulation.loop_thread_still_identifiable",
        all(f["loop_sections"] >= 1 for f in after_facts
            if f["loop_sections"] >= 1 or True)
        and sum(f["loop_sections"] for f in after_facts) >= 1,
        f"{sum(f['loop_sections'] for f in after_facts)} loop section(s) survive",
    )
    check(
        "simulation.is_not_silent",
        all(s in modes.SIMULATION_DISCLAIMER for s in ("NOT a measurement",)),
        modes.SIMULATION_DISCLAIMER[:60] + "…",
    )


def check_g2_band() -> None:
    print("\n[4] G2 one-sided band")
    cases = [
        (0.020, modes.G2_VERDICT_PASS, "the architect's healthy floor (below the old anchor)"),
        (0.040, modes.G2_VERDICT_PASS, "the architect's point estimate"),
        (0.067, modes.G2_VERDICT_PASS, "the old lower anchor — now PASSES (the §6.2 fix)"),
        (0.100, modes.G2_VERDICT_PASS, "the top of the healthy range"),
        (0.230, modes.G2_VERDICT_PASS, "the corrected ceiling"),
        (0.231, modes.G2_VERDICT_DEGRADED, "just over the ceiling"),
        (0.510, modes.G2_VERDICT_DEGRADED, "the top of the degraded band"),
        (0.914, modes.G2_VERDICT_BASE_EQUIVALENT, "the measured BASE loaded p95"),
        (None, "FAIL_NO_MEASUREMENT", "no measurement is a FAIL, never a pass"),
    ]
    for p95, want, why in cases:
        got = modes.g2_verdict(p95)
        check(f"g2[{p95}]", got == want, f"{got} (want {want}) — {why}")
    check(
        "g2.two_sided_band_would_have_false_redded",
        modes.g2_verdict(0.040) == modes.G2_VERDICT_PASS
        and not (modes.G2_BASE_ANCHOR_S <= 0.040 <= modes.G2_PASS_MAX_S),
        "a healthy 0.040 s sits BELOW the old 0.067 s anchor ⇒ the old "
        "two-sided band would have FAILED a working lane",
    )


def check_g4_pair() -> None:
    print("\n[5] G4 paired comparison")
    base = [0.5] * 100 + [0.9, 1.4, 2.2]
    better = [0.5] * 105 + [0.55]
    worse = [0.5] * 100 + [0.9, 1.4, 3.9]
    ok = modes.g4_profile_verdict(better, base)
    check("g4.fixed_pair_passes", ok["ok"],
          f"stalls {ok['head_stalls']}<={ok['base_stalls']}, "
          f"max {ok['head_max_s']}<={ok['base_max_s']}")
    bad = modes.g4_profile_verdict(worse, base)
    check("g4.worse_max_fails", not bad["ok"],
          f"max {bad['head_max_s']} > {bad['base_max_s']} ⇒ FAIL "
          f"(max has NO tolerance)")
    empty = modes.g4_profile_verdict([], base)
    check("g4.empty_series_fails_non_vacuity", not empty["ok"], empty["reason"])
    floor = modes.g4_profile_verdict([0.5] * 10, base)
    check("g4.sample_deficit_fails_non_vacuity",
          not floor["ok"], f"samples 10 vs 0.5x103 = 51.5 ⇒ "
          f"{floor.get('checks')}")


def check_g9_tables() -> None:
    print("\n[6] G9 per-shape expectation tables")
    base_all = set(modes.BASE_ON_LOOP_ALL)
    base_none = set(modes.BASE_ON_LOOP_NONE)
    check("g9.base_sets_disjoint", not (base_all & base_none),
          f"all={sorted(base_all)} none={sorted(base_none)}")
    check(
        "g9.routed_out_missions_is_exempt_but_not_zero",
        "SELECT instances" in modes.HEAD_ON_LOOP_RULES[modes.SHAPE_MISSIONS]["exact"],
        "the missions shape keeps SELECT instances on-loop (census #4) — the "
        "plan's 'on-loop 0 in every shape' wording false-reds it (§6.3)",
    )
    for shape in (modes.SHAPE_INSTANCES_FLAT, modes.SHAPE_INSTANCES_BFS):
        rule = modes.HEAD_ON_LOOP_RULES[shape]
        check(
            f"g9.no_instances_select_on_loop[{shape}]",
            "SELECT instances" in rule["forbid_exact"],
            f"forbidden={rule['forbid_exact']}, allow_prefixes="
            f"{rule['allow_prefixes']}, ceiling={rule['max_on_loop']}",
        )
        check(
            f"g9.prefs_prefix_is_measured[{shape}]",
            rule["allow_prefixes"] == (modes.PREF_SHAPE_PREFIX,),
            "the residual's allow-list is the MEASURED prefs prefix — the "
            "committed baseline records the prefs work as TWO statement "
            "shapes (instance_ui_prefs + instance_ui_prefs+instances), so a "
            "single-shape literal would false-red",
        )
    check(
        "g9.bounded_shapes_have_positive_bounds",
        all(v > 0 for k, v in modes.HEAD_ROUND_TRIP_MAX.items()
            if k not in modes.HEAD_COUNT_UNCHANGED),
        f"only the unchanged shapes carry the 0 sentinel — bounds: "
        f"{ {k: v for k, v in modes.HEAD_ROUND_TRIP_MAX.items() if k not in modes.HEAD_COUNT_UNCHANGED} }",
    )
    check(
        "g9.on_loop_zero_for_every_unlisted_shape",
        all(s not in modes.HEAD_ON_LOOP_RULES
            for s in modes.BASE_ON_LOOP_NONE),
        "the jobs shapes expect an EMPTY on-loop set in head mode",
    )
    check(
        "g9.unchanged_shapes_have_exact_expectation",
        all(modes.head_round_trip_expectation(s)["kind"] == "unchanged"
            for s in modes.HEAD_COUNT_UNCHANGED),
        f"unchanged: {modes.HEAD_COUNT_UNCHANGED}",
    )
    check("g9.modes_declared", set(modes.MODES) == {"base", "head"},
          "the mode switch is exactly two mutually exclusive values")


def check_unit_normalization() -> None:
    """Round-2 Blocking 1 — the gate-boundary unit normalization.

    The council measured the bug on the committed artifact: missions
    {SELECT instances: 20, SELECT job_queue_items: 10} over 10 samples vs
    the pinned per-request (2,1); flat prefs 60 over 30 vs max_on_loop 2.
    The leg is MUTATION-MEANINGFUL: feeding the SUMMED counts straight into
    the per-request rules (the round-1 behaviour) must FAIL both checks,
    and the normalized counts must PASS them.
    """
    print("\n[8] unit normalization at the gate boundary (Blocking 1)")
    missions = {
        "samples": 10,
        "statements_by_shape": {
            "SELECT instances": {"count": 20, "on_loop": 20},
            "SELECT job_queue_items": {"count": 10, "on_loop": 10},
        },
    }
    flat = {
        "samples": 30,
        "statements_by_shape": {
            "SELECT instance_ui_prefs": {"count": 30, "on_loop": 30},
            "SELECT instance_ui_prefs+instances": {"count": 30, "on_loop": 30},
            "SELECT instances": {"count": 330, "on_loop": 330},
        },
    }
    m_pr = modes.per_request_by_shape(missions)
    f_pr = modes.per_request_by_shape(flat)
    check("units.missions_per_request", m_pr["SELECT instances"]["count"] == 2
          and m_pr["SELECT job_queue_items"]["count"] == 1,
          f"{m_pr}")
    check("units.flat_prefs_per_request",
          sum(v["on_loop"] for k, v in f_pr.items()
              if k.startswith(modes.PREF_SHAPE_PREFIX)) == 2,
          f"prefs on-loop total = 2 (the architect's §6.3 count)")
    check("units.zero_samples_is_loud",
          modes.per_request_by_shape({"samples": 0,
                                      "statements_by_shape": {"x": 1}}) == {},
          "a zero sample count returns EMPTY (never a silent divide)")

    # MUTATION legs: the round-1 behaviour (summed straight into the rules)
    def exact_ok(counts: dict) -> bool:
        exp = modes.HEAD_ON_LOOP_RULES[modes.SHAPE_MISSIONS]["exact"]
        return set(counts) == set(exp) and all(
            lo <= counts.get(s, 0) <= hi for s, (lo, hi) in exp.items())

    summed = {"SELECT instances": 20, "SELECT job_queue_items": 10}
    normalized = {"SELECT instances": 2, "SELECT job_queue_items": 1}
    check("units.mutation_summed_fails_the_rule", not exact_ok(summed),
          f"summed {summed} vs pinned (2,1) — the round-1 bug, still failing")
    check("units.normalized_passes_the_rule", exact_ok(normalized),
          f"normalized {normalized} matches the pinned rule")

    rule = modes.HEAD_ON_LOOP_RULES[modes.SHAPE_INSTANCES_FLAT]
    prefs_summed = sum(v["on_loop"] for k, v in flat["statements_by_shape"].items()
                       if k.startswith(modes.PREF_SHAPE_PREFIX))
    check("units.mutation_flat_summed_exceeds_ceiling",
          prefs_summed > rule["max_on_loop"],
          f"summed prefs on-loop {prefs_summed} > ceiling "
          f"{rule['max_on_loop']} — the round-1 false red, reproduced")
    check("units.normalized_flat_within_ceiling",
          sum(v["on_loop"] for k, v in f_pr.items()
              if k.startswith(modes.PREF_SHAPE_PREFIX)) <= rule["max_on_loop"],
          "normalized 2 <= 2")

    # the simulation must write the SUMMED form back (one gate path)
    sim = modes.simulate_report({
        "per_endpoint": {"missions_badge": {
            "samples": 10,
            "round_trips": {"mean": 3.0},
            "on_loop_round_trips": {"mean": 3.0},
            "body_facts": {"rows": 20},
            "statements_by_shape": {
                "SELECT instances": {"count": 20, "on_loop": 20},
                "SELECT job_queue_items": {"count": 10, "on_loop": 10},
            },
        }},
    })
    sim_row = sim["per_endpoint"]["missions_badge"]
    gate_view = modes.per_request_by_shape(sim_row)
    check("units.simulation_writes_summed",
          sim_row["statements_by_shape"]["SELECT instances"]["count"] == 20,
          f"simulated statements_by_shape stays sample-summed "
          f"({sim_row['statements_by_shape']['SELECT instances']['count']})")
    check("units.simulated_report_survives_the_gate_path",
          gate_view["SELECT instances"]["count"] == 2
          and gate_view["SELECT job_queue_items"]["count"] == 1,
          f"the SAME normalization path yields the pinned (2,1): {gate_view}")

    uc = modes.declared_unit_classes()
    check("units.every_quantity_declares_a_class", len(uc) >= 9,
          f"{len(uc)} declared: statements_by_shape="
          f"{uc.get('statements_by_shape')}")


def check_reference_provenance() -> None:
    print("\n[9] G4 reference provenance (Blocking 2)")
    real_base = {"mode": "base", "repo_head": "abc123",
                 "simulation": {"applied": False}}
    simulated = {"mode": "head", "repo_head": "abc123",
                 "simulation": {"applied": True}}
    head_mode = {"mode": "head", "repo_head": "abc123"}
    no_head = {"mode": "base"}
    check("prov.real_base_accepted",
          modes.reference_provenance(real_base)["ok"], str(real_base))
    check("prov.simulated_reference_refused",
          not modes.reference_provenance(simulated)["ok"],
          "a SIMULATED reference (lag clamped to the tick floor) must never "
          "satisfy G4")
    check("prov.head_mode_reference_refused",
          not modes.reference_provenance(head_mode)["ok"],
          "the reference must be the BASE leg of the pair")
    check("prov.unattributed_reference_refused",
          not modes.reference_provenance(no_head)["ok"],
          "a reference without a repo_head is an unknown tree")
    check("prov.missing_reference_refused",
          not modes.reference_provenance(None)["ok"], "no reference at all")


def check_g5_helpers() -> None:
    print("\n[10] G5 helpers (children-order normalization + positive order)")
    payload = {"instances": [
        {"instance_id": "b", "children": [
            {"instance_id": "z"}, {"instance_id": "a"}]},
        {"instance_id": "a", "children": [
            {"instance_id": "m"}, {"instance_id": "c"}]},
    ]}
    co = modes.children_order_canonical(payload)
    check("g5.unordered_payload_is_not_canonical", not co["canonical"],
          f"{co['ordered']}/{co['arrays']} ordered — BASE heap order")
    norm1 = modes.normalize_children_order(payload)
    # NB: the ROW order is identical; ONLY the order inside each children
    # array differs — the single permitted difference class.
    norm2 = modes.normalize_children_order({
        "instances": [
            {"instance_id": "b", "children": [
                {"instance_id": "a"}, {"instance_id": "z"}]},
            {"instance_id": "a", "children": [
                {"instance_id": "c"}, {"instance_id": "m"}]},
        ]})
    check("g5.normalization_is_the_only_difference", norm1 == norm2,
          "two payloads differing ONLY in children order normalize to "
          "identical bytes")
    co2 = modes.children_order_canonical({
        "instances": [{"instance_id": "a", "children": [
            {"instance_id": "c"}, {"instance_id": "m"}]}]})
    check("g5.ordered_payload_is_canonical", co2["canonical"],
          f"{co2['ordered']}/{co2['arrays']} — the fixed tree's shape")
    check("g5.no_children_arrays_is_reported",
          modes.children_order_canonical({"x": 1})["arrays"] == 0,
          "a payload with no arrays carries no assertion (the shape-count "
          "fence handles vacuity)")


def check_mode_docs() -> None:
    print("\n[7] the mode contract is documented and total")
    for mode in modes.MODES:
        summary = modes.mode_summary(mode)
        check(f"modes.summary[{mode}]", bool(summary) and len(summary) >= 5,
              f"{len(summary)} documented expectation(s)")
    try:
        modes.validate_mode("nonsense")
        check("modes.invalid_mode_rejected", False, "accept badly-named mode")
    except ValueError:
        check("modes.invalid_mode_rejected", True, "ValueError on an unknown mode")


def main() -> int:
    print("═" * 72)
    print("eventloop-phase0 INSTRUMENT SELFTEST (no daemon boot)")
    print("═" * 72)
    check_discriminator()
    check_real_evidence()
    check_simulation_flips_the_pin()
    check_g2_band()
    check_g4_pair()
    check_g9_tables()
    check_unit_normalization()
    check_reference_provenance()
    check_g5_helpers()
    check_mode_docs()

    failed = [r for r in RESULTS if not r[1]]
    print("\n" + "═" * 72)
    print(f"SELFTEST: {len(RESULTS) - len(failed)}/{len(RESULTS)} checks PASS")
    if failed:
        for name, _, detail in failed:
            print(f"  FAILED: {name} — {detail}")
        return 1
    print("SELFTEST: PASS — the instrument discriminates BROKEN from FIXED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
