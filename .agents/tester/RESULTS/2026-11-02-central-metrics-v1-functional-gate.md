# central-metrics-v1 — TESTER FUNCTIONAL GATE (Tier 2)

**Date:** 2026-11-02 · **Tester instance:** this lane · **Lane:** `feature/central-metrics-v1` @ `f8d891d79` (base `941cb6321` = origin/latest, 68+ commits)
**Tier:** **2 — mechanical** (the lane diff touches `daemon/api.py`, a core-seam prefix; escalation is never a judgment call).

## VERDICT: **FAIL — 10 of 11 legs PASS; the Tier-2 whole-tree leg found 5 lane-caused net-new reds. NOT ceremony-ready until they land + a scoped re-verify.**

Everything else is green and evidenced below. The five reds are **lane-side repairable** (pin
updates + an evidence-file supersession note + a family-F audit) — but three of them encode a
question only the lane owner can answer (is the new `admission_state` writer legitimate? is the new
one-shot `psycopg.connect` site audited-correct?), so the tester lane routes them rather than
papering over them.

---

## Per-gate results

| # | Gate | Verdict | Evidence |
|---|---|---|---|
| 1 | **PACK REGISTRATION** (9/9 declared ids + widened derived compositions + D1 16-module rows + 2 `test/packs/metrics_*` scripts + FE e2e + ND-5 mutation row + the 38→41 pagination delta) | ✅ PASS | PACKS.md block (commits `ae350d340`, `dfc28b47a`). Registered compositions are the **derived** blast-radius sets, not the plan's short declarations (P2 +1 file · P3 +13 files · P4 4→21 files · P5 138→1801/1791). |
| 2 | **HOUSE CANON (R-v)** — fixed comparator adopted into the skills dir + FAIL-LOUD empty-verdict guard + `LC_ALL=C` on both sort+comm + files-first→log + FORM tags | ✅ PASS | `.agents/tester/skills/whole-tree-base-proven-diff/sweep_digest_verdict.sh` (+ the recipe's new §8). The guard adds a **third** clause the lane's repaired version lacked: empty verdict block ⇒ exit 2 VACUOUS, never 0. |
| 3 | **ATOMIC two-part fix** — contract check-site citation `metrics_census.py:340 → :355` + the pack's literal pin, ONE commit | ✅ PASS | `1c6798f07` (2 files, +2/−2). Pack 21/21 **before and after** (correct: the defect was a lying citation, not a red pin). `metrics_listener.py:419` verified **not** stale — no third part. |
| 4 | **D1 unit corpus** (`tests/unit/metrics/`, 16 modules) | ✅ PASS | **388/388, 0 skipped, 0 errors**, exit 0. Every registered count reconciled; the three family files first-counted (18/20/11). Node set byte-identical `5c753ef0f`↔`ae350d340`. |
| 5 | **D2 P2+P3 (+scope-cage)** dual-side | ✅ PASS net-new **EMPTY** | P2 326/326 (digest `d78b09cf…` identical); P3 498 unit + 30 integration both sides; scope-cage 12/12 exact. Single shared red = base-proven RDRS drift. |
| 6 | **D2 P4 W1/W2/W3** dual-side (21-file derived set) | ✅ PASS net-new **EMPTY** | D2-only 365 == 365 collection both sides; comparator exit 0. Surfaced the sampler order-dependence (FIXED, below). |
| 7 | **FE dual-side** — D1 jest · D2 declared · D2 derived · scoped typecheck · **both AM-23 build legs** · `package.json` diff | ✅ PASS | Drift-free re-run at `ae350d340` (4m16s). Both builds exit 0; typecheck 0 errors; `package.json` **0 lines** both sides; D1 141/141 (the +22 attributed to `cd06d2a0b`); D2 derived 1799/2 vs 1789/2 — same 2 ids (pre-existing post-DST settings rot), net-new EMPTY. **Zero lane surfaces over the 8 kB budget** (all 10 warnings pre-existing). |
| 8 | **FE END-TO-END** (TrueAuto) | ✅ PASS | Part A 2/2 (STEP assertion green). Part B **6/6 on live data** against a fenced `:8079` daemon: 78/78 series drawn, STEP re-proven on live data, stale-degradation never blank (35 rows retained + STALE badge), supersede + tab-hide proven by request counts, and **real** 403 (minted wrong-scope bearer) / **real** 503 (auth-OFF boot) / network copy — all truthful, forbidden string absent. |
| 9 | **Real-PG census + ND-micro ORDER pin** | ✅ PASS | 10 collected == 10 registered; 9 passed + 1 **pre-existing** red proven byte-identical at base. Real census evidence (application_name, 5s statement_timeout, raw psycopg never a pool, real `pg_stat_activity` snapshot). ND-micro mutation matrix **5/5**: GREEN → planted inverted order → RED on exactly ONE discriminating node → byte-identical restore → GREEN. |
| 10 | **FENCES + POSTURE** | ✅ PASS | 7/8 clean: migrations **119==119** (zero diff), pyproject+uv.lock **empty diff**, FE deps 0-line, no version bump anywhere, nothing pushed/tagged/promoted, **LIVE untouched** (`current→releases/v0.28.4`, livez 200 `0.28.4`, state.json sha byte-stable, NRestarts=0, ~21.6h continuous uptime). The 8th sub-check adjudicated: the flagged worktree was this gate's own in-flight BASE leg, removed at teardown. |
| 11 | **TIER-2 WHOLE-TREE** dual-side | ❌ **FAIL — net-new = 5 lane-caused ids** (119 raw → 113 composition artefacts + 1 pre-existing flake + 5 real) | Full tables below. Evidence: `.agents/tester/EVIDENCE/2026-11-02-central-metrics-v1-whole-tree/` |

### In-lane test-architecture fixes (gate-mandated, all test-code-only, mutation-verified)

| Commit | What |
|---|---|
| `3eb52bc86` | Sampler identity-dedupe pin immunised against a leaked process-global cadence provider (order-dependent false red). Pinned cadence + explicit precondition assertion; mutation-verified (cadence `0.0` ⇒ RED). |
| `4f3bac7f` | The leak **closed at source**: the listener-registry fixture now restores BOTH process-global providers (canonical `_restore_providers` pattern). **Class-closure proven** — contaminator-first pair green with the consumer's own fix REMOVED. |

---

## Gate 11 — the whole-tree detail (the FAIL)

**Collections:** HEAD `5c753ef0f` **32,838** / BASE `941cb6321` **32,450** (BASE matches the lane's own
P3 leg exactly). Deselected **1,145 both legs — symmetric**. Delta +388 = lane-added tests.

**Execution:** 9 file-boundary shards (~4,000 nodes), measured rate 762 nodes/min, shard-0
re-projection held (projected ~43 min, actual HEAD 39.3 min). One BASE-shard death diagnosed
structurally (5 lane-added ids absent at base ⇒ pytest aborted the shard) and resolved per recipe.
LLM fence identical both legs. Accounting closure **exact both sides** (32,838; 463).

**Comparator (house canon, non-vacuous both forms):** BASE unique 344 / HEAD unique 463 ·
base-only **0** · **head-only 119** · digests `99653321…` / `40bcd856…`. Verdict: NET-NEW = 119.

**Adjudication (3× isolation per side):** 113/119 pass 3× at HEAD ⇒ sweep composition artefacts
(leaked-global contamination under xdist batch order — the same class the concurrent fixes closed);
1 flaky **both** sides (pre-existing); **5 real**:

| id | Root cause | Fix shape (lane-owned) |
|---|---|---|
| `job_state/test_constitution_drift.py::test_known_admission_state_writers_matches_source_exactly_no_drift` | the lane's new `admission_state` writer `daemon/services/metrics_query/wiring/w2.py:196` is NOT in the static `KNOWN_ADMISSION_STATE_WRITERS` list | **the lane must answer whether a metrics-query wiring module should write `admission_state` at all.** If legitimate ⇒ register it in the known-list; if not ⇒ it is a product defect. Do NOT register it blindly. |
| `job_queue/test_defer_self_witness_watchdog.py::…::test_constitution_drift_test_passes` | same drift, second pin site | same ruling |
| `test_no_env_flag_remnants.py::…[CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE]` | the lane's OWN evidence file `evidence/p3-adjudication/BASE.collected-nodes.txt` references a retired flag with no supersession note (the guard scans `.agents/`) | add the supersession note to that evidence file (docs-only) |
| `…[CHECKPOINT_BLOB_PRUNE_DRY_RUN]` | same file, second param | same |
| `tools/test_dbpool_census_families.py::…::test_family_f_is_the_audited_site…` | family-F raw `psycopg.connect` census drifted **2→3** — the lane's census one-shot connect site is new | **individually audit the new connect site** (one-shot per tick, no pool — if that is the adjudicated design) then add it to `FAMILY_F_SITES` |

All 5 confirmed red at the current tip `f8d891d79` (1,1,1) and green at base (0,0,0) — deterministic,
not flakes. **Scoped re-verify after the lane lands fixes: the 5 ids ×3 at HEAD only (~4 min)** — a
full re-sweep is not required (the other 114 deltas are adjudicated non-findings).

**Budget — loud breach:** ~109 min vs the 90-min advisory (+19). Drivers: the mandated ≥1,000-node
probe phase (~9 min), one wasted unbounded-parallel adjudication pass (~13 min, redone at `-P8`),
and the full 119×3×2 ladder. Core sweep was ~63 min. No verdict was rendered before the shard-0
re-projection checkpoint; the breach surfaced after re-projection held.

---

## Scope decision
Full tier-2 scope was warranted (core seam `daemon/api.py`, +82k lines over 68 commits). No
reduction applied. Every leg ran dual-side where D2 applies.

## Disclosures (all material)
1. **Shared-worktree concurrency** (the dominant hazard of this gate): my uncommitted PACKS.md/canon
   were reverted by a worker's `git checkout --` (recovered + committed + LESSON); a torn read of my
   own in-flight citation fix manufactured a transient false red (mechanically reproduced and
   disproven); HEAD advanced 6 commits mid-sweep — all docs/test-only, zero daemon lines, verdicts
   re-anchored at tip.
2. **`data/ensemble.json` silently defeats the worktree `.env` fence** — the gitignored file pinned
   `"database":"sqlite"` and OUTRANKS `POSTGRES_DB=ensemble_dev`; one unsourced boot auto-writes it
   and poisons every later boot. Repointed to `ensemble_dev` during the FE e2e leg (backup
   `/tmp/ensemble.json.orig.2078076`); **lane owner should decide keep-vs-restore.** KB-recorded.
3. Source-mode dev boots abort on ambient `RAG_IS_REQUIRED=1`; the exposition listener silently
   degrades when the live daemon owns `:9798`; livez threw one transient timeout then 200 (uptime
   continuous — watch item, not a breach).
4. **The plan's declared compositions were short of the derived blast radius five times** — the
   registered packs now carry the derived sets, and PACKS.md records the mechanical counts (the
   frozen contract's figures are P2/P3-time snapshots: FE D1 119→141, P4 494→626, api_wiring
   13→24, …). Doctrine note: **a frozen contract may freeze interfaces and invariants; it must not
   freeze node counts.**
5. The PG-census pack's 1 red (`test_the_fence_rejects_a_disallowed_db_name_before_connecting`) is
   **pre-existing at base** (shared autouse fence shadows the module marker) — routed to the owning
   lane with a fix shape.
6. Monitor heuristics misfired twice on the whole-tree child (busy-slow advisory, hang notice) —
   correct for LLM-tool workloads, wrong for deliberately-sized 25–30-min shards. Doctrine note:
   judge a Tier-2 child on shard-file mtime + `progress.tsv` growth, not LLM last-activity. One FE
   e2e worker wedged (dead turn, no live processes) and was replaced once per the escape valve.

## Known-and-routed (verified still disclosed, NOT gated)
R-i census gated polarity (metrics-collection lane) · R-ii F1/F3 capture_active unwired · R-iii F7
histogram publish · R-iv stale_pause_sweep publication · FE `registry_read_ok` mirror (next lane).
The FE e2e live leg observed all three availability reasons (`capture_off`, `series_absent`,
`census_gated`) on real data — consistent with the disclosed dark-family set.

## Commits made by this gate (nothing pushed)
`1c6798f07` (atomic fix) · `ae350d340` (PACKS + canon + lesson) · `3eb52bc86` (sampler fix) ·
`dfc28b47a` (count reconciliation) · `4f3bac7f` (leak class-closure) · `f8d891d79` (lesson) ·
+ this RESULTS commit. All `.agents/tester/**`-scoped or test-code-only; **zero `daemon/` lines.**

## What the ceremony should know
- **Do not stage until the 5 whole-tree reds land + the scoped re-verify passes.** Three encode a
  lane-owned product-intent question (the `admission_state` writer), two are mechanical (evidence
  supersession note, family-F audit).
- Fences/posture are otherwise clean: zero migrations (119==119), zero new deps, no version,
  nothing pushed/tagged/promoted, LIVE v0.28.4 byte-stable with continuous uptime.
- The R-v canon and the PACKS registration are durable in-repo; the whole-tree raw evidence is
  preserved under `.agents/tester/EVIDENCE/2026-11-02-central-metrics-v1-whole-tree/`.
- Budget breach (109 vs 90 min) is disclosed above with its drivers.
