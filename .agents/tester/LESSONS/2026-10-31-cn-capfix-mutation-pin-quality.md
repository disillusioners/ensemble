# Lesson: mutation-gate pin-quality findings — double-cover seams and cap-value-agnostic boundary nodes

**Date:** 2026-10-31 · **Gate:** critical-notes cap-fix @ c8252f916 (RESULTS/2026-10-31-critical-notes-capfix-verification.md)

## 1. Double-cover seam (M4): tool pin green ≠ pin absent — it may be redundant

Reverting the resolver routing INSIDE `remove_critical_note` (`resolved = entry_id` bypass) made the REPOSITORY pins go red (7/13) while the tool-level class stayed 5/5 green. Mechanism: `project_cn_remove` first calls `repo.get_critical_note(entry_id)` — which is itself resolver-routed — then removes by the already-resolved id. The tool seam therefore cannot observe a remove-internal routing loss; the repository seam covers it.

**Rule:** when a mutation is proven at seam A but the task predicted seam B, read the call graph before declaring a gap — redundant coverage is a valid kill, but the evidence must NAME the covering path (quote the get-first call). If independent detection at seam B is wanted, the pin must bypass A (e.g., construct the repository result directly / mock the get).

## 2. Cap-value-agnostic boundary nodes (M5): `range(_MAX_ENTRIES)` pins survive a cap mutation

`test_cap_boundary_at_exact_cap_rejected` and `test_cap_boundary_one_below_accepted` build their fixtures with `range(_MAX_ENTRIES)`, so flipping `_MAX_ENTRIES` 200→50 leaves them green BY CONSTRUCTION. The actual killers were `test_max_entries_is_200` (value pin) and `test_cap_regression_51_preexisting_accepted` (legacy-count regression pin).

**Rule:** boundary pins that derive their fixture from the constant under test are tautological w.r.t. that constant's VALUE. A boundary pin should either hard-code the boundary count (build 199/200 rows literally) or be paired with an explicit value pin — which this suite has, so coverage is real but the boundary nodes' names overpromise.

## 3. Registry drift caught by pack-existence checks

`cn_injection_unit_test` referenced a file deleted pre-base (`eeef8845a`); `int4_repositories` carried a 2026-09--era count (116 vs 227-at-base / 242-at-HEAD). Both found only because the gate ran an explicit pack-target existence + count-at-base check. Cheap check, high yield — keep it in every gate's inventory step.
