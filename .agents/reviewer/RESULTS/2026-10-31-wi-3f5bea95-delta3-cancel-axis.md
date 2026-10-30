# Delta-3 Pass — Cancel-Propagation Axis @ `09ce9d369` (wi-3f5bea95)

- **Branch/worktree:** `fix/eventloop-sync-db-hotpaths` · **Date:** 2026-10-31
- **Worker:** `1977e8ac` (`code-review`, agentic), single-worker delta per reviewer's own delta-2 scoping; all evidence independently reproduced in /tmp (`/tmp/d3-f4fa`, `/tmp/d3-probe/d3_probe2.py`); worktree porcelain identical pre/post
- **Baseline:** `.agents/reviewer/RESULTS/2026-10-31-wi-3f5bea95-code-council-delta-fix2.md`
- **VERDICT: ✅ APPROVE-WITH-ADVISORIES on the axis** — 0 CRITICAL · 1 off-axis WARNING (pre-stage) · 3 suggestions

## Axis verification (all reproduced, claims not read as evidence)

**1. Diff exactly two lines — CONFIRMED.** `git show 09ce9d369 -- daemon/services/db_offload.py` = the two `return`→`raise` edits (:207 annotated, :209) only; `git diff f4fa4cc8b..09ce9d369 -- daemon/` = this file only; handler/token bookkeeping otherwise untouched; commit = 3 files.

**2. Delta-2 probe at HEAD — SYMPTOM DEAD** (W=2, holder-executes-nothing so breaches count only gate-admitted work; 20 trials/mode across the release-tick family):

| Leg | propagated | callee submitted | W-breach | max_running | accounting |
|---|---|---|---|---|---|
| BUG `f4fa4cc8b` | 0/20 (swallowed 100%) | 20/20 | 20/20 | **3** | `inflight=0 permits=2` while a unit runs; **sum invariant arithmetically perfect throughout** (0+2+0=2) |
| FIX `09ce9d369` | 20/20 | 0/20 | 0/20 | **2** | consistent; 2nd fresh unit correctly PARKS |

Reproduces delta-2's exact signature (permits freed while a unit runs → 3-vs-W=2, invisible to the sum). Pre/post modes correct on BOTH legs.

**3. Pin discrimination — CONFIRMED both directions, byte-identical pin text:** RED at `f4fa4cc8b` (`AssertionError: CancelledError did NOT propagate` — red for the RIGHT reason, not AttributeError); GREEN at `09ce9d369` (26/26). **Exclusive:** at the bug tree, only the new pin fails (17/18 pass). Asserts all four prescribed properties: propagation, callee-not-submitted (`running["n"] == 0`), real-occupancy `_inflight == running["n"]`, getattr sum invariant.

**4. Cleanups — CONFIRMED.** `getattr(gate, 'in_transit', 0)` at `:343`/`:436`; detail pin exercises `list_child_ids_for_parents(["p1","p2"])` (batch, `:5115` shape) vs `(["p1"])` (single, `:5145` shape) — the argument-shape distinction IS the real divergence since both sites share one repo method; direction regex on every captured statement.

**5. Non-regression sweep — ALL INTACT:** in_transit bookkeeping (acquire `:193/:202/:220`, do_release `:242/:246`); W-1 fresh-task-above-`do_release` (`:295-298`); W-2 `parents[3]` + real LIMITS chain; W-3 constants at all four sites; W-4 `cancel_futures` zero code-usage in the seam (`:301` is a comment); static ORDER BY pin green; C1 forwarding (`:1839-1840`); byte-identity machinery untouched. **Fences:** 0 `.agents/tester/` files; no migrations/scripts/readiness; no uncommitted coder work.

## 🟡 Off-axis WARNING — resolve before stage

**Silent removal of a registered gate fence.** `09ce9d369` deleted `test_collected_node_count_fence` + `EXPECTED_NODE_NAMES` from `tests/unit/test_instance_children_order_repository.py` (was ~:180-230) — the mutation-verified collected-node-set fence added by the tester lane in `5f22bfb08` and registered in `.agents/tester/PACKS.md` (pack documented as **9 nodes incl. the fence**, with skip/deselect/rename tripwires). Not in the cycle-3 requirements, not in the commit body, not disclosed, and not forced (the fence would have stayed green; nothing in the surviving 8 was loosened). Pack now collects 8 vs registered 9.
**Mitigant:** the tester's own tripwires will catch the 8-vs-9 drift at the functional gate — it cannot pass silently.
**Fix:** restore the fence updated to the 8-name set + `count == 8`, or route a pack-evolution re-registration through the tester lane; minimally disclose in the commit/plan delta. If an off-repo leader ruling authorized it, downgrade to advisory upon disclosure.

## Suggestions (tidier)

- `test_instance_children_order_repository.py:167` — dead expression `[...] for inst in []] or ["p1","p2"]` → plain `["p1","p2"]` (disclosed; value-neutral, confirmed).
- Nomenclature: the single shape is `list_child_ids_for_parents([id])`, not `list_child_ids_permanent` (which survives at `repository.py:417` with four other live consumers — no dead code). Correct future references.
- `test_static_sql_order_by_clause` docstring carries two near-duplicate MULTISET DISCLOSURE blocks (pre-existing) — fold one.

## Disposition

**Code-review phase: CLOSED for this lane** on the strength of this pass — every council finding across three cycles is now verified closed, the last defect is cured with a discriminating pin, and non-regression holds. ONE pre-stage condition rides the close: restore/reroute the deleted collected-node-count fence (test-only change; tidier/tester-lane eligible — but it MUST land before the functional gate runs, or the pack tripwire will red it there anyway).
