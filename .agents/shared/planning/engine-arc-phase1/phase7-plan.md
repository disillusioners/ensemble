# Phase 7 — Test pack buildout, registration, and the cross-cutting gates

**Depends on:** P1-P6 (packs are authored inside each phase; this phase owns the **buildout contract**, the registration, and the cross-cutting gates).
**Feeds:** P8.

> **The detailed test strategy lives in `test-strategy.md`** (a sibling worker's deliverable, written in parallel). This phase **does not duplicate it**: it fixes *what must exist*, *where it registers*, and *which cross-cutting gates are release-blocking*. When `test-strategy.md` lands, its per-leg detail is authoritative for the legs named here; the leg **names** below are the interface between the two documents.

---

## 1. Objective

Turn six phases of authored packs into **one registered, mutation-verified, release-blocking gate set**:

1. Every pack the phases authored is **registered** in `.agents/tester/PACKS.md` with its invocation, scope, last run, and status (the house registration format).
2. The **cross-cutting gates** that no single phase can own are built here: the estate-wide fence sweep, the dormant-posture proof, the boot-SLO rehearsal, the "engine touched nothing it did not own" diff audit, and the mutation matrix.
3. Nothing in the pack set may be **vacuous**: every gate carries at least one mutation that turns it RED.

---

## 2. Scope

**In:** `.agents/tester/PACKS.md` registration blocks; `test/packs/engine_*_test.sh` wrapper scripts; the cross-cutting legs (§4.2); the mutation matrix; the orphan/mutation-proof bookkeeping.

**Out:** the per-phase legs' *content* (authored in each phase, detailed in `test-strategy.md`); any live-install contact; any promote; performance benchmarking (the engine is not a performance play — see phase2 §1).

---

## 3. Decisions

| ID | Decision | Rationale (anchor) | Rejected |
|---|---|---|---|
| D7.1 | Packs live in **two live trees**, and the choice per pack is registered with its exact path: `test/packs/` (**484** files at this tree — `.sh` wrappers and `.py` harnesses both present) and `tests/packs/` (**50** files — likewise mixed). Recent lane registrations use **both** (e.g. `bash test/packs/prompt_reload_fresh_swap_e2e.sh`; `bash tests/packs/upgrade_refusal_vocabulary_census_test.sh`; `bash test/packs/fe_home_poll_interval_unit_test.sh`). **Rule for this program:** the `.sh` wrapper of each engine pack goes in **`test/packs/`** (the dominant convention for boot/drill-shaped lanes) and any mutation-matrix `.py` goes in **`tests/packs/`** (the house home for matrices — `ram_guard_phase6_mutation_matrix.py`, `psl_fix_cycle_mutation_matrix.py`). Every PACKS.md row names the EXACT command, so the tree choice is never ambiguous. Where `test-strategy.md` §1.2 names `tests/packs/` for the `.sh` wrappers, **the registered invocation wins** and this plan's split is authoritative for placement | Both trees exist and are in active use across the recorded lane blocks; a mixed convention is the observed status quo, so the only safe rule is "register the exact command" | inventing a third location; asserting one tree is canonical (the evidence contradicts both single-tree claims) |
| D7.2 | Every engine pack wrapper is **env-fenced at the top**, in the exact shape the estate's packs use: per-variable `POSTGRES_*` scrub (`env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL`), `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **pinned explicitly** (never `env -u`), `ENSEMBLE_SELF_ENV=dev`, `PYTHONDONTWRITEBYTECODE=1`, `timeout N` | Three standing critical notes: (a) `env -u POSTGRES_*` is a **silent NO-OP** in bash — each variable must be scrubbed individually; (b) `env -u` on the boot-sweep var **strips the ambient guard** and the sweep kills live processes (2026-10-31 incident) — it must be **pinned `0`**; (c) test scripts must never resolve HOME-derived live-install paths (2026-10-12 double deletion). The helper `tests/helpers/ram_guard_fence.py` (`:8`, `:30`) encodes exactly this and is the reference implementation | a bare `pytest` invocation (picks up ambient `POSTGRES_DB=ensemble_prod`); `env -u POSTGRES_*` (a no-op) |
| D7.3 | The engine's **dormant posture** is itself a release-blocking gate | The Emperor's phase rule is the single most important behavioral property of this release ("build … but don't use yet"). A gate that fails if the engine ever touches the estate is worth more than any unit test | treating dormancy as "obviously true" (it is one config default away from false) |
| D7.4 | The **diff audit** gate: the branch's `daemon/` diff must touch ONLY the paths this plan's ownership map (§phase1 §0.3) declares | The engine is a NEW subsystem; an accidental edit to an existing sweep is the highest-risk failure mode (it would change live behavior in a release that claims to change none). The house precedent is the metrics lane's allowlist pin (`tests/unit/metrics/test_metrics_family_adjudication.py:530-556`, with its "can it fail?" self-proof at `:590+`) | a prose promise ("we did not touch anything") |
| D7.5 | The **mutation matrix** is a first-class artifact, one mutation per acceptance criterion, each recorded with the observed RED | House standard: mutation-verified pins (`decision-report` §C.3 "mutation-verified regression pins for the state-machine transitions"; the PACKS.md blocks carry explicit mutation evidence, e.g. the maintenance-idle-starvation block's "Part A mutation-proven"). A pack with no mutation is unverified | "the test passes" as evidence (a vacuous test also passes) |
| D7.6 | Registration happens **per pack**, in the house block format (a `## <NAME> — <Lane> (<date>; branch/tip, base)` header, a `> What it gates` note, a Pack/Invocation/Scope/Last run/Status table) | `.agents/tester/PACKS.md` blocks are all in this shape; a reviewer greps the file for the lane name | a single "engine tests" row (loses per-leg status granularity) |
| D7.7 | **No pack calls a real LLM, a real network endpoint, or a live install.** Scratch SQLite + scratch PG only; the engine is dormant so nothing needs live data | House fence (every PACKS.md block states "zero PG / zero network / zero live-install contact" or its exact carve-out). A dormant engine needs no live anything | any live-coupled leg |
| D7.8 | The boot-SLO rehearsal leg (P4's SLO-3) runs against a **scratch boot**, not live | The live number belongs to `boot-slo-measurement.md` (sibling). The pack proves the *method* is wired; the number is a separate record | a live boot in a pack (unacceptable contact) |

---

## 4. Task decomposition

| ID | Task | Outcome | Files / anchors |
|---|---|---|---|
| **T7.1** | Register the ten packs (§4.1) in `.agents/tester/PACKS.md` under the lane's begin/end markers | ten blocks in the house 5-column format; `Last run` = `—` until a real run; `Status` states `PENDING — authored at planning` or the real result | `.agents/tester/PACKS.md`; format precedent — the maintenance-idle-starvation and pok-family-key blocks |
| **T7.2** | Author the ten `.sh` wrappers with the F-21 preamble | every wrapper scrubs `POSTGRES_*` per variable, pins `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`, sets `ENSEMBLE_SELF_ENV=dev`, `PYTHONDONTWRITEBYTECODE=1`, `timeout` | `test/packs/engine_*_test.sh`; reference implementation `tests/helpers/ram_guard_fence.py:8, :30` |
| **T7.3** | Author the mutation matrix harness | one entry per AC; each runs the smallest containing pack against a planted defect; GREEN-on-defect is a blocking finding | `tests/packs/engine_mutation_matrix.py`; style `tests/packs/ram_guard_phase6_mutation_matrix.py:27, :299` |
| **T7.4** | Author Leg D (dormant posture) | spy-verified zero `execute_fn` invocation across ≥3 intervals with the boot block's N observer specs registered (zero executables — the D4.11 posture); mutation turns it RED | as §4.2 |
| **T7.5** | Author Leg E (diff audit) | branch `daemon/` diff ⊆ the ownership map; self-proof that the assertion can fail | as §4.2; model `tests/unit/metrics/test_metrics_family_adjudication.py:373-387` |
| **T7.6** | Author Leg G (fence sweep) | the 16 fence files run as ONE leg so a regression is attributable in one place **[count recounted — approver iteration-002 (N-h): the Leg G enumeration below names 16 files — `test_daemon_settings_api.py` + `test_settings_resolution.py` are two files joined by "+" in one bullet; the earlier "15" miscounted the join]** | as §4.2 |
| **T7.7** | Author Leg H (dual-driver materialization) | scratch SQLite + scratch PG; both tables; second boot no-op; zero live contact | as §4.2 |
| **T7.8** | Run the matrix and record the RED/GREEN evidence per leg | every mutation RED; every restore sha256-identical; the evidence quoted into the PACKS.md Status cells | `.agents/tester/PACKS.md` + a results file |

### 4.1 Registration (the deliverable surface)

| # | Pack | Owner phase | Gates |
|---|---|---|---|
| 1 | `engine_substrate_unit_test` | P1 | two migrations + IF NOT EXISTS + TEXT + DOWN; migration delta == +2 unique prefixes; model/index 3-site lockstep; `EngineConfig` resolution + yaml/env precedence; `JobSpec` validation (9 error classes); `register()` version bump / no-op echo; addendum fields persisted |
| 2 | `engine_execution_unit_test` | P2 | state-machine 8 transitions + stamp-on-every-terminal; single-flight overlap; timeout both policies; retry arithmetic; off-loop classes; restart-clock hydration |
| 3 | `engine_guardrails_unit_test` | P3 | getter-at-tick; born-dead escalation; fail-to-legacy polarity; dual-path flip; supervisor re-entry; re-registration cancel |
| 4 | `engine_boot_unit_test` | P4 | boot classes + deadlines; jitter determinism; watchdog **drill**; wiring posture line; shutdown order |
| 5 | `engine_boot_slo_gate` | P4 + this phase | SLO-1 (block ≤250ms dormant), SLO-2 (structural ordering pin), SLO-3 (rehearsal number recorded) |
| 6 | `event_registry_unit_test` | P5 | catalog validation; ring drop-counting; four bridges; posture labels; consumer liveness; lag honesty; **zero metrics-family delta** |
| 7 | `engine_observability_unit_test` | P6 | view key sets; convergence union; scope matrix; paging; method-set freeze; deferral record |
| 8 | `engine_dormant_posture_test` | this phase | §4.2 leg D |
| 9 | `engine_diff_audit_test` | this phase | §4.2 leg E |
| 10 | `engine_mutation_matrix` | this phase | §4.2 leg F |

### 4.2 The cross-cutting legs (owned here)

**Leg D — dormant posture (release-blocking).**
A scratch boot with the shipped defaults must show: **N observer specs registered, zero executables (`execute_fn` withheld), shadow rows present** — `engine_jobs` carries the boot block's registered observer specs (D4.11/AMD-1); `engine_runs` carries ONLY `outcome=SKIPPED_SHADOW` rows (one per due job per interval, per D2.13); all `SERVICES_*_USE_ENGINE` unset; the ONE posture INFO line with `registrations=N observer specs`; and — the strong half — an **invocation spy** on the engine's dispatch proving `execute_fn` count == 0 across ≥3 tick intervals. Mutation: flip `ENSEMBLE_ENGINE_SHADOW_MODE` default to `false` **and** register a fixture job ⇒ the spy count becomes > 0 ⇒ RED (proof the leg can fail). **Non-vacuity disclosure (compound mutation — approver iteration-002, N-e):** the mutation is deliberately COMPOUND — each half alone is structurally inert, not a weaker test: flipping the shadow default alone leaves zero executables (dispatch is structurally impossible regardless of polarity — D4.11), so the spy stays 0; registering a fixture job alone leaves the shipped `shadow_mode=true` guard in place (D2.13), so the spy stays 0. Only the pair produces an observable violation, and the pair is ONE planted defect class (guard+registration wiring reverted to a pre-dormant shape), restored together with a single sha256 pair-check in the matrix record — this disclosure exists so the compound is read as required detection power, not as a vacuity gap.

**Leg E — diff audit (release-blocking).**
`git diff --name-only <base>..<HEAD>` filtered to `daemon/` must be a **subset** of the ownership map in phase1 §0.3. Any path outside it is RED, naming the path and the owning phase it should have belonged to. Self-proof: an in-memory mutation adds a path to the expected set and the assertion demonstrably fails (the "can it fail?" discipline, `tests/unit/metrics/test_metrics_family_adjudication.py:590+`).

**Leg F — mutation matrix (evidence, not a gate).**
One mutation per AC (≈34 across phases), each with the observed RED digest and the restored sha256. The matrix is a ledger in `tests/packs/engine_mutation_matrix.py` + a PACKS.md row quoting the RED count. **Non-vacuity rule:** a mutation that produces GREEN is itself a finding (the AC is unverified) and blocks the pack.

**Leg G — fence sweep (release-blocking).**
The full set of files this program must leave green, run as one leg so a regression is attributable in one place:
`tests/test_resource_control_seam.py` (`:294-346`) · `tests/test_opt_o8_stale_subprocess_sweep.py` (`:464-527` + the sweep behavior pins) · `tests/test_health_probes.py` · `tests/unit/tools/test_readyz_grace_detail.py` · `tests/unit/services/test_boot_epoch.py` · `tests/unit/metrics/test_metrics_family_adjudication.py` · `tests/unit/test_maintenance_idle_starvation.py` · `tests/test_daemon_settings_api.py` + `tests/test_settings_resolution.py` (the `19` count) · `tests/test_settings_source_order_pin.py` · `tests/integration/test_vscode_routing.py` (catch-all last) · `tests/migration/test_migration_dup_prefix_guard.py` · `tests/unit/test_phase5_jobs_router.py` · `tests/unit/test_memoir_v2_persona_router.py` · `tests/test_custom_agents_router_authz.py` · `tests/unit/metrics/test_central_metrics_contract_doc.py`.

**Leg H — dual-driver materialization.**
Scratch SQLite boot (migrations apply, both tables exist, second boot no-op) + scratch PG boot (`create_all` materializes both, `_ensure_postgres_columns` adds nothing, second boot no-op). Real PG only, per-variable scrub, `PG_TEST_DB` naming a scratch DB (never `ensemble_prod` / never the dev DB's tables).

---

## 5. Effort

| Bucket | Low | High | Basis |
|---|---|---|---|
| PACKS.md registration (10 blocks) | 2 | 4 | |
| Pack wrappers + env fences (10 `.sh`) | 2 | 3 | one shared fence preamble |
| Leg D dormant posture | 2 | 4 | spy + scratch boot |
| Leg E diff audit | 1 | 2 | |
| Leg F mutation matrix bookkeeping + the matrix RUN (T7.3, T7.8) | 3 | 6 | ~34 mutations × run/record/restore |
| Leg G fence sweep wiring | 1 | 2 | |
| Leg H dual-driver materialization | 1 | 2 | scratch PG is the expensive half |
| **Phase total** | **12** | **23** | ~**17 mid** |

> Note: the **per-phase pack authoring** effort is carried in P1-P6 (each phase's last task), so this total is the buildout + cross-cutting half only — not a duplication. **[Low column recounted — approver iteration-002 (N-h): the buckets above sum to 2+2+2+1+3+1+1 = 12, not the previously-pinned 13; the package totals (phase1-plan §0.2, plan-overview §3, decisions D-10) are co-corrected to 136/222 (≈176 mid) on the same recount.]**

---

## 6. Fences that constrain this phase

**F-21 — the pack wrappers are themselves fenced artifacts (three standing incidents).**
(a) `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **pinned explicitly**; `env -u` on it strips the ambient guard and a frozen binary's boot sweep SIGTERM/SIGKILL'd live processes (2026-10-31 10:18:44Z staged smoke: live launcher + `ensemble-prod` pair + code-server + context7 children killed). (b) `env -u POSTGRES_*` is a **silent NO-OP** — bash does not glob env names, so an ambient `POSTGRES_DB=ensemble_prod` survives a "fenced" run (2026-10-21). (c) Test scripts must never resolve HOME-derived install dirs — two live-install deletions on 2026-10-12 from `export HOME="$HOME"` + `rm -rf $HOME/agents-ensemble`.
**Constraint:** D7.2's preamble is mandatory and is asserted by the fence sweep (a wrapper without it REDs).

**F-22 — `.agents/tester/PACKS.md` is a shared, append-only registry.**
Twelve-plus lane blocks already exist; the file is edited by many lanes. **Constraint:** append blocks between `<!-- <lane>-begin -->` / `<!-- <lane>-end -->` markers (the file's own convention), never restructure an existing block.

**F-23 — `tests/unit/metrics/test_metrics_family_adjudication.py:530-556` runs `git diff --name-only LANE_BASE` (a two-dot diff against a FIXED base `644c2bfaaa` **[TREE-TRUTH RECONCILE — 2026-11-21 (P6 review #3): LANE_BASE 644c2bfaa → 0b5624dc9 (verified at tree 2f36e8aed94c: `LANE_BASE = “0b5624dc9d57fe1c8ec49415382cacb2cbd55f25”` at `:74`; re-pointed at the 2026-11-21 tester gate — old anchor reddened on 157 inherited foreign paths, true base yields 0 offenders); the planning-time figure predates the landed F-23 record maintenance]**)** and asserts `_assert_no_new_daemon_offenders`. **Constraint:** this program's engine files will appear as changed daemon paths in that diff. Because the pin's own recorded set is maintained by the changing lane (its comment: "THE RECORD IS MAINTAINED BY THE CHANGING LANE"), the engine lane must either sit inside an already-allowed path or record its paths in `PRE_EXISTING_DAEMON_OFFENDERS`. **This is exactly P6 T6.3's unfencing path** — and it is a fence-sweep RED otherwise. Recorded here as a **known, planned** cost of the program (not a surprise at release).

---

## 7. Dependencies

- **Phase:** all of P1-P6 (registration last).
- **External:** `test-strategy.md` (sibling) — the leg detail.
- **Blocks:** P8.

## 8. Parallelism

- Registration + wrappers can start as soon as the pack NAMES are fixed (they are, in §4.1).
- Legs D/E/F require a complete tree (run last).
- The mutation matrix is the long pole (≈34 × run/restore). It runs after the diffs freeze.
- Worktree-lane parallelizable: yes (a tester lane can own this phase end-to-end).

## 9. Risks / mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| A pack is vacuous | a false green on a release-blocking property | D7.5's non-vacuity rule: a mutation that stays GREEN is a blocking finding |
| The mutation matrix eats the schedule | 34 mutations × a full pack each | mutate ONE assertion at a time, run the SMALLEST pack that contains it, batch by pack |
| A wrapper leaks a live env var | accidental live DB contact | F-21's mandatory preamble + the fence sweep leg asserting the preamble is present in every wrapper |
| Registration duplicates a sibling lane's row | a confusing PACKS.md | F-22's marker convention + a grep for the lane name before appending |
| Leg E is too strict and blocks a legitimate edit | friction | the expected set is data (a literal in the pack), not derived from the diff — and a legitimate new path is a one-line, reviewed addition |
| The census fence (F-23) REDs at release | a blocked release | P6 T6.3 records the decision; if the family is deferred, the engine's daemon paths still must be recorded in the census's allowlist/offender set — T7.1 includes that record, and the fence sweep proves it |

## 10. Acceptance gates (measurable)

| ID | Gate |
|---|---|
| AC1 | Ten packs registered in `.agents/tester/PACKS.md` under the lane's own markers, each with invocation, scope, last run, status |
| AC2 | Every wrapper carries the F-21 preamble (asserted mechanically: the fence sweep greps all ten) |
| AC3 | **Leg D** green: dormant posture proven with an invocation spy; the mutation (shadow-off + fixture job) turns it RED |
| AC4 | **Leg E** green: `daemon/` diff ⊆ the ownership map; the self-proof mutation fails the assertion |
| AC5 | **Leg F** complete: ≈34 mutations recorded; every one RED; zero GREEN-survivors; every restored file sha256-identical |
| AC6 | **Leg G** green: all 16 fence files pass unchanged (except where this plan explicitly records a change — P6 T6.3) **[count recounted — approver iteration-002 (N-h): 16, per the Leg G enumeration]** |
| AC7 | **Leg H** green: scratch SQLite + scratch PG materialize both tables; second boot no-op on both; zero live contact |
| AC8 | **Migration census:** the release carries exactly **+2** `.sql` files (P1), **+0** from every other phase; prefix uniqueness asserted on the delta, never as an absolute count |
