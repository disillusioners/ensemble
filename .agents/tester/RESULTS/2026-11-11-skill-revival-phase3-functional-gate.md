# Functional Gate — skill-system-revival Phase 3 (skill-capture production wiring)

- **Date:** 2026-11-11 (gate ran 2026-11-10T23:38Z → 2026-11-11T05:2xZ)
- **Board:** wi-4d1fa093 · **PM:** ENSEMBLE-27 · **Gate instance:** tester `bde0ae1c`
- **Branch:** `feature/skill-capture-prod-wiring` @ `4159d46702d27d6e53691b49697aa79c4d299635`
- **Base:** `0efdf106f3f66ccb400405f4137694592b72097d` (== origin/latest at commission)
- **Council verdicts inbound:** FIX APPROVE, TEST DISCRIMINATION ADEQUATE, RUNBOOK APPROVE

## VERDICT: **GO-WITH-DISCLOSURES**

5/5 commissioned gate legs PASS. The whole-tree suite produced **2 deterministic net-new reds** — both `source_gc` git-span guards — proven by 4-arm execution to be **merge-shape artifacts** (green on a `--no-ff` merged shape on both the pinned base AND the current `origin/latest`; red only while un-landed, and red again if squash-landed). **Landing condition (binding): the giter landing MUST be a `--no-ff` merge** (house standard per MERGE conventions). Zero behavioral net-new regressions across 36.5k nodes × 2 interpreters.

## Scope Decision

Full whole-tree dual-interpreter suite **warranted**: release-gating branch (stages v0.29.16-lineage), explicitly commissioned. Recipe = the registered house **Gate A Tier-2** dual-side whole-suite FAILED-list diff (`.agents/tester/PACKS.md:656`; skills `whole-tree-base-proven-diff` + `failed-digest-equality-gate`), per-side scratch worktrees at exact SHAs, per-variable PG/dead-LLM fence on every invocation.

---

## Leg 1 — Mutation re-proof (worker `7931b0cd`, interpreter CPython 3.14.7, worktree's own venv): **PASS**

| Stage | Mutation | Result |
|---|---|---|
| Baseline | none | exit 0 — **4 passed** (3.17s) |
| Leg 1 | 3 dispatcher-binding lines commented in `_wire_skills_router_services` (`daemon/api.py:650-652`) | exit 1 — 2F/2P (`F..F`): `test_production_seam_binds_the_capture_lane_dispatcher` (`:124 assert bound is not None`) + `test_production_seam_is_idempotent` (`:210 assert first is production_dispatcher`) |
| Leg 2 | leg-1 + test-side `metrics.set_job_dispatcher(object())` injection | exit 1 — **still RED at the IDENTITY assertion** `:135 assert bound is production_dispatcher` (foreign sentinel `0x…ca50` ≠ production `0x…ca60`); presence-assert `:129` now passes → **identity-anchored, NON-DEGENERATE** |
| Leg 3 | lifespan→seam call removed (`daemon/api.py:3968`, 1 line) | exit 1 — **exactly 1F**: only `test_lifespan_still_calls_the_production_seam` (`:164 co_names` pin); other 3 green |
| Final | restored | exit 0 — 4 passed |

Restore discipline: PRE-sha256 (`api.py` `0c0b577c…61218`, test file `750584c2…42beb`) == post-restore for every leg; porcelain empty after each; closing `git diff --exit-code HEAD` = 0; `MUT-LEG` residue sweep 0 hits; HEAD unchanged; no commits/stashes.
**Disclosure:** commission's `~:636-638` pointer was approximate — actual 3-line metrics-service binding at `650-652` (router seam `640-641` untouched); failure messages name the production contract exactly.

## Leg 2 — Full suites, both interpreters, base vs head (workers `65655d3a` base / `bec2a33e` head / `c024ef7f` salvage+adjudication): **PASS after adjudication — net-new behavioral = 0, net-new guard-artifact = 2 (conditioned green)**

- **Interpreters (exact):** CPython **3.13.15** + **3.14.7** (uv-managed; canonical `.venv` never used; import-proofs inside scratch trees).
- **Census:** base 36,545 nodes / 1,590 files; head 36,549 / 1,591 (**+4 = the wiring test file** ✓); 1,261 marker-deselected; executed 36,533 / 36,537 (12 `tests/performance/test_message_api_cost.py` nodes excluded **symmetrically** — recipe-sanctioned; compensating pin = registered `regression_performance`; `[1x10k]` quarantine-deselected, never run). Accounting closure **EXACT** on all four legs.
- **Failed-ID digests (BARE-ID, LC_ALL=C):** base-313 **492** (sha `3e44faf86f08093a…`) · base-314 **491** (`b58ca829351af726…`) · head-313 **478** (`ea2069cee9433efb…`) · head-314 **476** (`41902b1389a5e93e…`). GREP-FULL forms produced; compare within-form only.
- **Raw diffs → adjudication (3× isolated serial runs per delta id, per leg; fresh `--local` clones on 3.14):**
  - 3.13: 53 base-only / 39 head-only (92-id delta) → **57 pre-existing** (red both legs ≥2/3) · **2 net-new** · 1 pre-existing flake · 3 flaky · 1 resolved-at-head · 2 daemon-log pseudo-lines · 4 whitespace-mangled digest artifacts (PASS 3/3 both legs).
  - 3.14: 41 / 26 (67-id delta) → **36 pre-existing** · **2 net-new** · 2 resolved-at-head · 2 stress-proven pre-existing flakes (6× each).
  - **Parity trap: NO-OP** — both sides carry the identical 3 `no_xdist` `test_update_memory_md_*` ids (base ran them serial, head inside xdist; digest delta 0).
- **THE 2 NET-NEW NODES (identical on both interpreters; base 0/3, head 3/3):**
  - `tests/unit/repositories/test_source_gc_repository.py::TestP11BootNoBehaviorChange::test_the_decision_lane_manager_drift_is_confined_to_decision_hunks`
  - `tests/unit/repositories/test_source_gc_repository.py::TestP11BootNoBehaviorChange::test_the_memoir_lane_manager_drift_is_confined_to_sanctioned_lane_hunks`
- **Mechanism (git-proven):** git-span guards over `rev-list --first-parent --merges -1 HEAD` → `9d63d2968` on both legs; base span has an **empty** `daemon/manager.py` diff (vacuous green); head span carries the branch's +30/5− wiring hunk with no lane marker → red **by design on any feature tip touching manager.py**.
- **4-arm merged-shape execution proof (worker `a9230d16`, CPython 3.14.7, throwaway clones, never pushed):**
  | Arm | Shape | Result |
  |---|---|---|
  | A | pinned base `0efdf106f` + `--no-ff` merge (`69143c259`, 2 parents, span diff **0 bytes**) | **6 passed, exit 0** (2 guards + 4 wiring tests) |
  | B | feature tip `4159d4670` (control) | 2 failed / 4 passed — flip is discriminating |
  | C | **current** `origin/latest` `6ca643ad7` + `--no-ff` merge (`85518f48`) | **6 passed, exit 0** |
  | D | squash landing counter-case (`4b952b66`, non-merge) | 2 failed / 4 passed — **squash keeps them RED** |
- **Named pre-existing reds confirmed at base (and head — pre-existing):** `test_api_router_extraction.py::TestApiModuleSize::test_api_module_is_small` (api.py-size pin) · `::TestRouteRegistration::test_http_methods_are_correct` · 3 observability pins `test_skill_capture_observability.py::{test_d_pin_admission_and_execution_refusals_are_never_merged, test_d_pin_every_refusal_site_increments_its_counter, test_m2_pin_refused_admission_records_carry_a_run_id}`.
- **Wiring file at head:** collected ✓ and **4/4 GREEN on BOTH interpreters** (standalone 4-6s + in-shard grep-verified). Zero ids for the file in any digest.

## Leg 3 — Targeted wiring file: **PASS** (folded into Leg 1 baseline/final + Leg 2 head legs)

## Leg 4 — Canonical landing audit (worker `a5f52e86`, strictly read-only): **PASS 7/7**

Canonical HEAD `0138952876bb456f63417e040884ab28761c9835` (detached confirmed) unmoved; dirty shape intact (staged **6** `scripts/`-scoped M/A incl. `scripts/audit/borrow_census.py`; unstaged-modified **15** = 4 skill files + 3 test files + 2 `tidier[v2]` + 6 `.agents/` docs); `diff --cached` exactly the 6 entries; runbook **3-way sha256 parity** `1b0d7f48be7d7a1d6cdcf3130618c09df7324a16b9b5decd0ca0d9bb228d879e` (canonical copy == branch copy == committed blob) + tracked on branch; no index ops (HEAD/index/reflog unchanged, no lock files).

## Leg 5 — Runbook §8.1 gate-band reconciliation (worker `dbea18df`, code-truth, no DB): **RECONCILED ×2, no drift**

- **(a)** candidate band `selected AND task_succeeded AND feedback_applied IS NULL` + STOP-if-0 — sound against code: predicate complement at `repository.py:1753-1755`; count 0 ⇒ `has_genuinely_evolved_for_instance` True ∀ instances ⇒ Gate 4 refuses everywhere (`skill_metrics_service.py:765-769`, twin `skill_evolution_service.py:1716-1717`) ⇒ lane can never admit.
- **(b)** blocker band `IS NOT NULL` — **monitor-only confirmed** (zero `feedback_applied`/`genuinely*` refs in `routers/metrics.py` / `skill_capture_observability.py`; nothing raises/stops/gates on the count; runbook itself states the per-instance refusal at :719-721).
- Doc-level refinements (non-blocking): **D2** §8.1(a) counts rows while the gate is per-instance existence — carry the code's "upper bound, never a forecast" wording into §8.1; **D3** citation `repository.py:1699-1719` is the docstring, executable predicate is `:1749-1758`; **D4** blocker refusal is silent (no log/metric — `None` return only); **D1** candidate band is prose-only in code (runbook discloses).

---

## Disclosures (bind the GO)

1. **Landing condition:** `--no-ff` merge required (Arm D). Post-landing proof pair: `git rev-list --first-parent --merges -1 HEAD` == HEAD **and** `git diff <that>..HEAD -- daemon/manager.py` empty, then the 2 guards green. Merged-shape green is span-empty-vacuous by design (stated for the record).
2. **`origin/latest` moved during the gate:** `0efdf106f` → **`6ca643ad7`** (`test: v0.29.16 release-cut gate evidence (first-build + staged) — SHIP`, another lane). Feature is un-landed (merge-base = `0efdf106f`); `merge-tree 6ca643ad7 4159d4670` → clean, zero conflicts; verdict re-proven on the current base (Arm C). **The landing lane must re-census the version slot** (KV "next free = v0.29.16" is stale).
3. **Whole-tree baseline reality:** ~492/491 pre-existing reds under Gate A (the commission's "5 known reds" are the scoped-run subset — all present). The gate criterion is the DIFF; it is adjudicated to 0 behavioral net-new.
4. **Methodology:** the two sides' legs overlapped on one 16-CPU host (forced by daemon-restart recovery; recipe forbids it) → ~90 load-flake delta ids, ALL cleared by 3× isolated serial adjudication (+6× stress on 2 borderline). Future whole-tree gates: serialize legs.
5. **Perf corpus:** `test_message_api_cost.py` (12 nodes) recipe-excluded both sides (compensating pin registered); `[100x10k-variance]` unschedulable under 300s cap (124 both legs, verbose-confirmed), `[1x10k]` quarantine-deselected.
6. **`p7` interpreter asymmetry** (`test_skill_sink_retention_shared_path_pins.py::test_p7…`: 3.13 HANG / 3.14 clean FAILED) — pre-existing, file byte-identical at base, not lane-caused. Shard-killer context: 241s module setup in `test_skill_sink_class_containment_pins.py` (drives 124s; isolated legs pass).
7. **Infra events during gate:** 3 daemon restarts (03:35Z, ~03:43Z, ~03:55Z windows); workers self-recovered via checkpoint resume (both "zombie" lanes delivered complete evidence). One wedge (unbounded background-orchestration bash, 1h54m) terminated → anomaly `4d45310d`. **`pause_instance` on post-restart zombie-running instances correlated with daemon crashes 2-for-2** → anomaly `ed3f806a` (major; do not pause such instances; monitor-and-wait instead).
8. Digest hygiene: GREP-FULL suffix normalization required before compare (form mismatch = 9-11 phantom ids); whitespace-parametrized ids split by `sort`/`comm` (4 artifacts, pass on direct re-run).

## Instance ledger

| Worker | Lane | Outcome |
|---|---|---|
| `7931b0cd` | mutations + targeted | PASS, restored byte-identical |
| `a5f52e86` | canonical audit | PASS 7/7, read-only proven |
| `dbea18df` | runbook reconciliation | RECONCILED ×2 |
| `8848baad` | suites v1 | terminated (wedge; anomaly `4d45310d`) — 1 re-dispatch used |
| `65655d3a` | suites base side | delivered post-restart self-recovery |
| `bec2a33e` | suites head side | delivered post-restart self-recovery |
| `c024ef7f` | salvage + adjudication + diffs | delivered (incl. self-caught vacuous-digest bug, fixed pre-report) |
| `a9230d16` | merged-shape 4-arm verify | delivered, no shared-state writes |

## Evidence (session-scoped, `/tmp`): `gate3-base-results/` · `gate3-head-digests/` · `gate3-salvage/{results,evidence}/` · `gate3-mut-backup/`
Digest sha256s + counts above are the durable fingerprints. All scratch worktrees removed (`git worktree list` gate3-free); feature worktree porcelain 0; canonical untouched; no commits/pushes/tags by any lane except this RESULTS/LESSONS commit.

## Overall Status
- Mutations: ✅ · Whole-tree dual-interp: ✅ (0 behavioral net-new; 2 guard artifacts conditioned on `--no-ff`) · Targeted: ✅ · Canonical: ✅ · Runbook: ✅
- **Testing Complete: READY — GO-WITH-DISCLOSURES** (conditions: disclosure 1 landing shape + disclosure 2 slot re-census).
