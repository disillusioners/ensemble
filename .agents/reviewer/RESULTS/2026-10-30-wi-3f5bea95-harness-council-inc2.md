# Harness Council Pass — R1 Instrument Verdict (increment #2)

- **Instrument:** `.agents/tester/harness/eventloop-phase0/` at tip `0d8693820`, branch `fix/eventloop-sync-db-hotpaths`
- **Commits:** `3e976696b` (round-2 B1/B2/B3+G5) · `dd6d1ed12` (round-2b g4 KeyError + snapshotter) · `7ac87d8a5` (repair note + 2×2/BASE-red evidence) · `0d8693820` (LESSONS)
- **Identity:** claimed `harness_tree_sha 4961609a65f27c6a9bd18b3fc82f7e027e984ef8` = **`dd6d1ed12`'s** harness tree; HEAD's harness tree is **`ff02c7fc…`** (same code + the 5 committed evidence files). Cite `harness_tree_sha` + branch tip at run (A2).
- **Council:** governor `c13a667f`, 2 councilors (`agentic` + `coding`), `code-review` skill, independent runs
- **Baseline closed against:** `.agents/reviewer/RESULTS/2026-10-30-wi-3f5bea95-round2-delta-repair-review.md` Part B
- **VERDICT: ✅ ACCEPT-WITH-NOTES — approved for the HEAD functional run.** 0 CRITICAL · 2 WARNING (doc accuracy, non-blocking) · 6 ADVISORY

The REJECT bar ("the instrument lies / its non-vacuity proof is unsound") is **not met** — both councilors reached this independently, verifying against committed code rather than closure claims.

---

## Round-2 blockers — CLOSED (code-level)

| Blocker | Evidence |
|---|---|
| **B1** G9 summed-vs-per-request | ONE normalization site at the gate boundary: `modes.per_request_by_shape` (`modes.py:582-602`); **sole** gate consumer `main.py:606`; single collector write `main.py:214` (no second divergent site — the round-1 mechanism is structurally impossible). `samples <= 0 → {}` (`modes.py:590-594`) — loud, fails shape-count fences. Simulation writes the **summed** form back (`modes.py:817`) so real and simulated converge on one path. Mutation legs reproduce the exact bug (`selftest.py:252-334`: summed `(20,10)` vs pinned `(2,1)`; flat prefs 60 > ceiling 2) plus `zero_samples_is_loud`. Unit classes → `report.g9_unit_class` + README + beside bounds. |
| **B2** G4 reference provenance | `reference_provenance()` (`modes.py:416-447`) requires `mode=="base"`, `simulation.applied` falsy, `repo_head` recorded; **asserted in the gate path** (`main.py:1023-1033`), not record-only. 5 `prov.*` selftest legs cover both simulated artifacts, head-mode, and missing `repo_head`. Cell-2 evidence shows `{ok:true, reference_mode:"base", reference_simulated:false, repo_head:3e976696b…}`. |
| **B3** truth-table overclaim | §8 filled from measurement; 4 cell reports + MANIFEST committed (`git diff --stat dd6d1ed12 7ac87d8a5` = exactly 5 files, **no code change**). Counts independently re-derived from the JSONs: **52/52 · 58/63 · 63/63 · 48/57**, red-lists matching the doc. |

## 2×2 truth table — every cell supported

- **Cell 1** base-on-BASE: PASS 52/52, census 13/248/1873/89/7/1439/1874/3, on-loop 100% instances/work/defer/missions, 0% jobs, capture 8/8, family pin on loop thread.
- **Cell 2** head-on-real-in-flight-HEAD: FAIL 58/63 — production redness (see correction below).
- **Cell 3** head-on-simulated-fixed: PASS 63/63, verdict string `PASS (SIMULATED — not gate evidence)`, G2 0.0865 s, G5 9/9.
- **Cell 4** base-on-simulated-fixed: FAIL 48/57 with **exactly 9** reds, **all BASE assertions** (`formula[flat] 13≠4`, `formula[BFS] 248≠8`, four `g9_on_loop_base`, `stack_pin_family 0/8`, `stack_pin_narrow 0/8`, `g2_band[base]`) — **zero instrument errors among the 9 ⇒ non-vacuity proof sound**.

## ⚠️ CORRECTION to my own check assumption (carry into the gate narrative)

My brief asked councilors to confirm cell 2's reds are "the instances-path family." **They are not.** The `agentic` councilor enumerated all five and the governor adjudicated in its favour:

- instances-path family went **GREEN** at HEAD: `formula[flat]` 13→4 (on-loop 1), `formula[BFS]` 248→8 (on-loop 1);
- the five reds are: `floor_samples[work]` 0/10 · `g5_shapes_collected` (all-200=False) · `head_round_trips_bounded[work]` None · `g9_on_loop_head[defer_blocked]` 88 + 1 on-loop · `g2_band[head]` 0.9687 s.

I.e. 3 × `/api/work` HTTP-500 consequences + `defer_blocked` (C-3 unlanded) + G2 queueing behind them. Same "unfinished work" class, **different shape**. The `coding` councilor's conclusion was right; its "2 instances-path family" label was not.

## The five judged items

1. **G5 retraction — HONEST and MEASURED.** Discriminator exists (`seed.py:178-187` inserts siblings in reversed id order, parents-before-children preserved); the "no ORDER BY" claim is statically true in the committed BASE tree (`repository.py:434-437`). Reproduced 130/130 BFS + 1/1 detail canonical at BASE live; cell 1 shows 47/47 + 1/1 at full scale. **HEAD-side assertion remains meaningful** — `gate_g5` fences canonical order per shape (`main.py:860-875`), passes in cell 2, and would red a reordering HEAD. BASE-side control impossible on this host and correctly **reported, never asserted** (`main.py:838-858`).
2. **jobs_active_badge 7 rts/req — consistent** with D-68's residual framing (non-regression only; HEAD 7 ≤ BASE 7).
3. **Instrument purity — CLEAN.** All tester commits path-scoped; `git diff --name-only <c>^ <c> -- daemon/ tests/ frontend/ plugins/` = **0 at every commit**; harness porcelain empty; `raw/BASELINE.full.json` blob `a830f933…` unchanged.
4. **Near-miss rm-glob — acceptable disclosed process event.** Caught by post-command status, restored via `git checkout --`, blob identity proven before==after, LESSONS committed (`0d8693820`). No corrupted state ever committed.
5. **Reproduction — done.** Selftest **62/62 reproduced independently by both councilors**. One truth-table leg reproduced under the fences (`git archive 3e976696b` → /tmp → `run_all.sh --mode=base --profile=smoke` → exit 0, PASS, `formula[flat] 13==13`, `g9_on_loop_base` exact, `stack_pin_family 8/8` on loop thread, children 130/130 + 1/1, scratch DB dropped `db_rows_after: 0`, port freed, no live contact, no repo writes).

## Warnings — fold before the gate report cites §9

- **W1** `RESULTS/2026-10-30-eventloop-harness-head-mode-repair.md` §9 bullet 1 claims G5's byte-diff "flags `shape:work` as the one differing body." The actual mechanism: work returned **HTTP 500 → `normalized_sha256: null` → excluded**; the byte-diff fence passed (9/9). The red carrier is `g5_shapes_collected` (all-200=False). Conclusion right, mechanism wrong — and that sentence is what a reader would cite as instrument behaviour. **Fix:** name `g5_shapes_collected` as the red carrier; tighten the fence detail to "compared N/M (skipped unhashable: shape:work)" so an errored body can never read as byte-identical coverage.
- **W2** Same note §5 prints `selftest.py → 43/43` (round-1 count) while §0, README and the run say **62/62**. A stale self-test count inside an evidence-integrity document is exactly the round-2 defect class. **Fix:** 62/62 with command + date.

## Advisories

- **A1** `selftest.py:284-304` re-implements the G9 exact-rule predicate inline rather than invoking the real consumer (`main.py:600-620`) — gate/selftest drift would go uncaught. Extract the per-shape derivation into a helper and pin it.
- **A2** MANIFEST `instrument_commit: a677bdcaf` is a production commit; harness identity rides `harness_tree_sha`. Rename to `branch_tip_at_run` or annotate the two identities. Also note `4961609a` = `dd6d1ed12`'s tree; HEAD's is `ff02c7fc…`.
- **A3** Repair note `:390/:392` duplicated `## 8. The 2×2 truth table` heading.
- **A4** `gate_g5` BASE branch has no BASE-side children-order positive control on this host; the discriminating load sits on the **§5.2 static ORDER BY pin — an artifact OUTSIDE this instrument. Verify that pin pack exists and is green at the HEAD functional run.**
- **A5** `modes.reference_provenance` trusts artifact-declared fields; prevents mis-wiring, not deliberate forgery — one-line README note.
- **A6** Keep the LESSONS rule extension.

## Unverified / disclosed

- Full-profile reproduction not run (cost); the smoke leg reproduces structure, per-request census and the children-order measurement, not full-scale values.
- Cell 2's `/api/work` 500 root cause belongs to the developer lane (the code council independently found it as C1 — `list_work` kwarg mismatch). Instrument red is trustworthy; cause not assessed here.
- §5.2 static ORDER BY pin pack (out-of-instrument backstop) not inspected — see A4.

## Recommendation

**Proceed to the HEAD functional run with the instrument as committed at `0d8693820`.** Fold W1 + W2 (doc text only, no measured result changes) before the gate report cites §9. A1–A6 are post-run hygiene. Cite instrument identity as `harness_tree_sha` + branch tip (A2) and verify the §5.2 ORDER BY pin is green at gate time (A4).
