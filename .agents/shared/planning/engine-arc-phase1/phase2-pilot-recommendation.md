# Engine Arc Phase-1 — Phase-2 Pilot Recommendation

**Status:** recommendation document for the first post-dormant activation ("pick one"). Planning only — no code, no commits, no live contact. The pilot itself is OUT of Phase-1 scope; this doc is the recommendation the Emperor/architect stage acts on, and Phase-2 sizing derives from what Phase-1's dormant observatory reveals.
**Framing note:** decision-report §C.2 labels this the "Phase 1 pilot"; the Emperor's phasing ("we build the engine first but don't use yet, then pick one by one") treats it as the first post-dormant activation — i.e. the Phase-2 pilot. The module and criteria are identical either way; this doc uses the latter framing.

---

## 1. Recommendation

**Pilot module: `OrphanWatcherSweepService`, post-fix (late-bind).** Single module, single family (reconcilers), `MAIN_LOOP` concurrency class, 90 s cadence, 30 s grace. It is the exact motivating specimen for the born-dead trap the engine's dependency-resolution-at-first-tick primitive was built to make impossible — and, after the fast-lane fix, it runs on a healthy baseline so the pilot isolates engine behaviour from defect.

Activation shape (per decision-report §B.2 / §C.3): `SERVICES_ORPHAN_WATCHER_USE_ENGINE` flag OFF→ON flips execution from the legacy loop to the engine; OFF again restores legacy (one flag, both paths compiled; engine-wide OFF is the mass-rollback lever; engine failure posture is fail-to-legacy, never fail-stuck). No module migrates in Phase 1 — the Phase-1 boot smoke uses a synthetic canary only.

---

## 2. Evidence basis

**Census (`census-periodic.md`):** row #4 `OrphanWatcherSweepService`, `orphan_watcher_sweep.py:85 / :272-296`, trigger FTI (but see depth row A1), cadence 90 s (`orphan_watcher_sweep_interval_seconds`, config.py:2695), grace 30 s (`:82`), fail L&C (`:280-290`), kill switch **NONE** (api.py:1906-1911), topology AL (main asyncio loop). Depth row A1: **`dependency_bus` permanently None in prod** — the born-dead wiring trap.

**Liveness evidence (`liveness-evidence.md` row 3):** **DORMANT-TRAPPED** 🔴 — 07:17:58/07:19:28/07:20:58 ticks 215–217, every tick: "dependency_bus is not wired (legacy test fixture / pre-wiring lifespan) — relying on the startup sweep"; counter 216 counts no-op ticks; live orphan+husk `9c985fc7` unhandled. The only would-be inter-boot watcher-orphan coverage is dead.

**Research anchors (`research-anchors.md`):** OrphanWatcher wiring trap verified intact at this tree — ctor capture `orphan_watcher_sweep.py:131-138`, construct `api.py:1910-1931`, `init_dependency_bus` `api.py:2974`, `set_dependency_bus` `api.py:5712`. The fast-lane branch `fix/phase1-live-defects` carries the born-dead fix (late-bind via getter, not construction capture) + bash-reaper retention fix (shared meta kv: `wt.active.fix/phase1-live-defects.engine-arc-p1`, status active, base `b71742fe3`).

**Decision-report:** §A.1 family 1 (reconcilers) = ENGINE-ELIGIBLE; §A.6 names OrphanWatcher as the born-dead trap (verified intact at live); §C.0.1 mandates the late-bind fix; §C.2 selects OrphanWatcher as the pilot; §C.3 orders it first among the migration tail.

---

## 3. Reasoning (why this module, why first)

**Single-service blast radius.** One module, one family (reconcilers), one topology (main-loop asyncio task), 90 s cadence, 30 s grace. No DB-offload or dedicated-executor complexity. A failure during the pilot is contained to watcher-orphan coverage, which is already dead today — the pilot cannot make a live capability worse than it already is (it can only restore coverage or fail back to the current dead state).

**Concurrency_class fit: MAIN_LOOP.** The dependency bus is an in-memory singleton; the sweep's work is pure in-memory watcher reconciliation (no DB touch on the hot path). This is the cleanest fit for the engine's main-loop class and avoids exercising `DB_OFFLOAD(to_thread)` / `DEDICATED_EXECUTOR` topology, which should be proven on later migrants (bash reaper, maintenance tier) where the topology actually demands it. Piloting on the simplest class first is deliberate: if the engine cannot run a main-loop job correctly, nothing later matters.

**Observability baseline available from Phase-1 dormant registry.** The Phase-1 dormant shadow records, per job, `would_fire_at / actually_fired / skip_reason / duration / dead-flag` plus dependency-assertion results at boot (decision-report §C.1). OrphanWatcher is the perfect specimen: today its born-dead state is invisible to any per-module counter (its own tick counter climbs to 216 while `runs_total=0` and `dead=true`). The dormant registry would have surfaced it as `dead=true` after 2×interval with zero runs — exactly the falsification signal the Phase-0 shadow exists to produce. Before/after evidence is therefore available without new instrumentation.

**Dual-path flag mechanics.** `SERVICES_ORPHAN_WATCHER_USE_ENGINE` (to be added; today there is no kill switch at all — api.py:1906-1911, always-on). OFF → engine unregisters the job, the module's legacy loop starts; ON → engine owns execution, legacy loop suppressed (single-executor). Rollback = one flag, not a code change. Engine-wide OFF is the mass-rollback lever. Engine failure posture = fail-to-legacy, never fail-stuck. This is the cleanest possible dual-path proof because the legacy loop is the *current* dead loop — "engine runs it" vs "engine broke it" is answerable by comparing `dead=true`/`runs_total` before and after the flip.

**Dependency on the fast-lane fix.** The pilot must run on a healthy baseline: the C.0.1 late-bind fix (resolve the bus at first tick via getter, not construction capture; boot assertion after `init_dependency_bus`) must land and be verified first (branch `fix/phase1-live-defects`). Piloting an UNFIXED born-dead service would conflate defect with engine behaviour — the engine would inherit a dead dependency and the pilot could neither prove nor falsify the engine's dependency-resolution primitive. With the fix landed, OrphanWatcher becomes a *working, small, previously-trapped* module — the motivating specimen restored to health, then migrated. This sequencing is the whole point of choosing it.

**Kill-switch coverage.** Today OrphanWatcher has no kill switch (always-on hard policy, like JobLockSweep). Under the engine it gains one (`SERVICES_ORPHAN_WATCHER_USE_ENGINE`), which is strictly an improvement and a clean test of the engine's per-job kill-switch contract. (Contrast with modules that already have switches — those test nothing new about the switch machinery.)

**What the pilot proves (for the ~20 eligible modules tail):**
- The dependency-resolution-at-first-tick primitive (getter, never ctor capture) + loud boot wiring assertion + `dead=true` after 2×interval.
- The honest-state machine (`IDLE → DUE → RUNNING → {SUCCEEDED, SKIPPED_<reason>, FAILED_BACKOFF, TIMED_OUT}`) with stamp-on-every-terminal-transition, generalized from the landed `job_metrics` telemetry.
- The per-job kill switch + dual-path (flag) rollback, end-to-end, on a real module (not just the synthetic canary).
- The dormant→active flip on a real module (not just the canary), including single-executor suppression of the legacy loop.
- The observability family's ability to detect born-dead on the exact specimen it was designed for.

**What the pilot does NOT prove (deliberately deferred to later migrants):**
- PEXIT supervision (JobLockSweep / SourcesGc carry the `supervised()` primitive test — OrphanWatcher's fail mode is L&C, not permanent exit).
- `DB_OFFLOAD(to_thread)` topology (maintenance tier; bash reaper).
- Event-trigger integration (terminalization hook, durable EventBus) — v1 catalog, not v1-blocking.
- High-frequency telemetry cadence (10–60 s class — telemetry/memoir are ordered last).
- Maintenance-tier parity (its predicate fix already landed; as a runtime migrant it was always the worst first pick — "engine runs it" vs "engine broke it" unanswerable with no parity baseline).
- The ~20-module tail's heterogeneity — the pilot proves the contract on one shape; the tail proves generality. That is the point of "one by one."

---

## 4. Alternatives considered (and why not first)

| Candidate | Evidence | Why not first |
|---|---|---|
| **Maintenance tier** (checkpoint-cleanup + vacuum + 7 jobs) | liveness rows 4–7; census #12–#16; predicate fix already landed v0.31.11 (max_staleness escape hatch + `job_metrics` skip telemetry) | Its honest-state repair already shipped; as a *runtime* migrant it was always the worst first pick (never ran → no parity baseline). Carries only the verification/convergence task (C.0.3), which needs no engine. Piloting it would conflate "engine runs it" with "engine broke it." |
| **Bash reaper (post-fix)** | census #55 (bash boot reconcile + retention prune, boot-only); liveness row 31 (194k-row registry, 3,604 spawns unreaped); fast-lane branch carries the retention fix | It is a *piggyback* cadence-gated mechanism (family 7, inherits host cadence) with no own loop — piloting it tests the engine on the weakest scheduling shape (inherit-host-cadence), and proves nothing about deadline/interval ownership. Better as a later migrant (C.3 orders "bash reaper" at (3)). |
| **SourcesGc** | census #38; INSUFFICIENT-EVIDENCE; its own PEXIT (`:256-262`) | Dark module (zero maintenance_runs audit rows) — piloting it would prove observability on a dark module, which is useful but not the motivating specimen. Decision-report names it as the *second* migrant (parity proof), not the first. |
| **Telemetry-family service** (resource sampler/flusher, metrics census sampler) | liveness rows 12–14 (EXECUTES / DORMANT flag-off); census #39–#40, #44 | High-frequency (10–60 s), mostly thread-based (TH) topology; decision-report orders telemetry/memoir *last*. Piloting it first would exercise the hardest cadence and the wrong topology before the contract is proven on the simplest class. |
| **Healthy simple sweep (TmpImageCleanup / SourcesGc)** | decision-report §C.2 (c) | Clean parity proof, lowest drama — but proves nothing about repairing broken lanes, and even the "healthy" candidates are partially dark (SourcesGc is INSUFFICIENT-EVIDENCE with its own PEXIT). Good *second* migrant (parity proof), not the first. |
| **JobLockSweepService** | census #1; PEXIT verified intact at live (row 241 catch → row 250 return); "healthy = invisible" (zero durable evidence) | Carries the `supervised()` primitive test (PEXIT class) — valuable, but it is the *second* shape to prove (after the dependency-resolution specimen). Decision-report §C.3 orders JobLock at (1) in the Phase-2+ tail, after the OrphanWatcher pilot. |

**Conclusion:** OrphanWatcher post-fix is the only candidate that is (i) the exact motivating specimen for a primitive the engine exists to make trap-impossible, (ii) small and single-topology, (iii) restorable to health by a fast-lane fix already in flight, (iv) gaining a kill switch it never had, and (v) carrying a before/after observability baseline from the Phase-1 dormant shadow. The alternatives are better as *subsequent* migrants; none is a better *first*.

---

## 5. GREENLIGHT CRITERIA (measurable)

All must hold before the flip. None is satisfied by Phase-1 code alone — they are the Phase-2 entry gates.

1. **Shadow evidence maturity (RE-WORDED per architect ruling (a), architecture-recommendation.md §1(a) — verbatim; AMD-1(c); supersedes the ceremony-gated "≥2 release cycles" framing AND the "converged `job_metrics` family" dependency, which D-6 explicitly rejects):** *"≥2 independent boots of shadow evidence on the priority targets (one of which is the staged-release boot smoke), AND long-cadence maturity: every registered target has been shadow-evaluated through ≥2× its interval, AND a ≥48h live soak post-promote before the pilot flip."* Priority targets per decision-report §A.5 ranks 1–6 (JobLock + SourcesGc, maintenance tier, bash reaper, RDRS, strand-healer/drift, healthy tail). The recording fields (`would_fire_at / actually_fired / skip_reason / duration / dead-flag`) live in the engine's own in-process snapshot (D2.14) — no metrics family is minted for them.
2. **Zero false shadow-dead on healthy mechanisms** (AMD-1(c) semantics: for observer specs the dead-flag is shadow-evaluation liveness — continuous `SKIPPED_SHADOW` evidence) during shadow — specifically WatchReconcile (the healthy 300 s backstop, 65/65 cycles at exactly 300 s live) must never show `dead=true`. A false dead on a healthy mechanism falsifies the engine's scheduling and blocks the flip.
3. **Skip-reason distributions sane** — no skip-storms from the honest-state helper (`IDLE_GATED, KILL_SWITCHED, DEP_MISSING, ERROR_BACKOFF, OVERLAP, TIMEOUT`); the cure must not be worse than the disease.
4. **Boot wiring assertions present for every adopter** — dependency-assertion results at boot recorded; OrphanWatcher's assertion must pass (bus resolved at first tick, not ctor capture).
5. **OrphanWatcher fast-lane fix landed + verified (legacy-side; AMD-1(c) split)** — fix-landed verification stays legacy-side (the fast-lane lane's own evidence); "runs_total>0 for OrphanWatcher **under engine execution**" moves to the pilot's post-flip acceptance evidence. Branch `fix/phase1-live-defects` late-bind fix merged and verified on a live (or fenced dev) boot; the pilot must run on a healthy baseline. The dormant shadow cannot show `runs_total>0` for a job the engine never executes — it is NOT a pre-flip greenlight signal.
6. **Boot-SLO gate green with engine registered (dormant)** — boot_epoch→readyz-green ≤120 s with the engine registered-dormant (per `boot-slo-measurement.md`); any engine-attributable delta measured, not assumed. (≈97 s worst-observed headroom on live class; ≈53 s on cold-DB class.)
7. **Watchdog DRILL evidence captured** — cursor-anchored T_restart→T_readyz (13/14/15 s precedent), detect→log→recover chain present with watcher liveness throughout; evidence artifact in `.txt` (not `.log`).
8. **Kill switches armed and tested** — `ENSEMBLE_ENGINE_ENABLED`, `ENSEMBLE_ENGINE_SHADOW_MODE`, and `SERVICES_ORPHAN_WATCHER_USE_ENGINE` all functional; flag-off boot proves legacy loop resumes (rollback = one flag, end-to-end, in the same run).
9. **Mutation-verified regression pins** for the state-machine transitions (`IDLE → DUE → RUNNING → terminal`) registered in `PACKS.md`; conformance pack green.
10. **Promote ceremony** (tag + 4-way sha + rollback_safe) with livez/soak gates, per house standard (C.3).

**Falsification (would DISCONFIRM the pilot choice):** (i) a false-dead/false-healthy flag on a healthy mechanism (falsifies the observability thesis — the family becomes noise); (ii) skip-reason storms from the honest-state helper; (iii) adoption stalls below ~30 % of priority targets in 2 cycles (voluntary adoption fails → enforcement-by-registration wins → build B now **[corroborates MIGRATION ESCALATION, not build-conditionality — see the §6 SUPERSEDED note]**); (iv) the Emperor's actual pain turns out to be event-trigger generality, not machinery health (thin buys ~zero there); (v) live verification shows the v0.31.11 max_staleness escape hatch never fires under real load (the starvation class is NOT closed by point fixes → the structural-guarantee case strengthens).

---

## 6. Scope fence

> **[SUPERSEDED IN PART — review fold F-7, 2026-11-21:** the "engine is built only if the gate fires" bullet below is a stale pre-ruling remnant contradicting Emperor ruling #1. The §E.3 gate governs MIGRATION ESCALATION only, never the build (build is unconditional per Emperor ruling #1 — "Build the engine."; gate-hold 3.80 vs build 2.65, Emperor overruled the gate-hold) — so a Phase-2 commission cannot re-litigate a settled ruling. The gate-fire list is retained below as the migration-escalation criteria it actually governs. The other fences in this section stand.**]**

- **The pilot is OUT of Phase-1 scope.** Phase 1 ships the C layer (primitives + observability convergence) in dormant shadow posture plus the C.0 live-defect fixes. No execution transfer, no module migration, no flag flips on real modules (the Phase-1 boot smoke uses a synthetic canary only).
- This doc is the **recommendation** the Emperor/architect stage acts on. It does not authorize build work.
- **Phase-2 sizing derives from Phase-1's dormant observatory** — the shadow evidence (contention windows, PEXIT deaths under `supervised()`, boot wall-clock SLO, event-substrate mass) is precisely what feeds the escalation gate (decision-report §E.3). The pilot's own scope (single module, single flag) is small by design; the tail (~20 eligible modules) is sized later, ordered by §A.5 pain×simplicity.
- **B (subset engine) stays behind the falsification gate** regardless of this recommendation. This doc recommends the *first migrant*, not a runtime decision. ~~The engine is built only if the gate fires~~ **[SUPERSEDED — see the section note above; the build is unconditional per Emperor ruling #1]**. The gate-fire list (contention ≥2× off-window baseline; a live PEXIT death of a `supervised()`-wrapped loop; boot wall-clock breaching the Emperor's SLO; event-substrate mass making per-consumer polling measurably wasteful) governs **migration escalation**, not build.
- **Standalone forever (not pilot candidates):** ram-guard, wedge/loop-lag detectors, readiness/livez, the scheduler substrate (DST domain), thread-death-as-signal heartbeats (decision-report §A.6).

---

## 7. Open questions (decision-level, Emperor/architect)

1. **Numbering ratification:** decision-report §C.2 calls this the "Phase 1 pilot"; the Emperor's phasing calls it the first post-dormant activation (Phase-2). Ratify one numbering so downstream docs (phaseN-plan.md, PACKS.md) reference a single label. (Module and criteria are unaffected.)
2. **Whether the pilot runs as soon as greenlight criteria 1–5 hold, or waits for the full 2-cycle shadow (criterion 1) even if OrphanWatcher itself is healthy earlier.** Trading speed against falsifiable evidence — the Emperor's call (decision-report §E.4 Decision 2).
3. **Whether `SERVICES_ORPHAN_WATCHER_USE_ENGINE` is the right flag name** vs a generic `ENGINE_PILOT_ORPHAN_WATCHER_ENABLED`. The decision-report §B.2 convention is `SERVICES_<MODULE>_USE_ENGINE`; consistency argues for the former.
4. **Second migrant after OrphanWatcher:** decision-report §C.2 names a healthy sweep (TmpImage or JobLock) for parity proof. Confirm the Emperor wants parity-proof-as-second (proves the contract on a healthy baseline) vs PEXIT-class second (JobLock/SourcesGc, which additionally gains a kill switch + healthy trace). Either is defensible; the choice shapes the Phase-2 acceptance evidence. [RESOLVED-BY-RULING (b): JobLockSweepService is the ruled second migrant; the healthy-vs-PEXIT framing is superseded by primitive-proven-second]
5. **Event-trigger generality vs machinery health:** if the Emperor's actual pain turns out to be event-trigger generality (not machinery health), the thin (C-layer) path buys ~zero there and B or a substrate program is justified now. Confirm which pain is primary before committing Phase-2 scope (decision-report §E.3 disconfirmation (iv)).

---

## 8. References

- `../central-polling-engine-study/decision-report.md` §A.1 (families/taxonomy), §A.5 (pain×simplicity ordering), §A.6 (standalone adjudications), §B.2 (engine design, boot ordering, kill switches, dual-path flags), §C.0 (immediate wave), §C.1 (dormant shadow), §C.2 (pilot selection rationale), §C.3 (ordering/gates/rollback), §E (recommendation + gate).
- `../central-polling-engine-study/liveness-evidence.md` (row 3 OrphanWatcher DORMANT-TRAPPED; live identity block).
- `../central-polling-engine-study/census-periodic.md` (row #4 OrphanWatcher; depth row A1; families; §A.2 overlaps; §A.3 gaps).
- `research-anchors.md` (this directory) — OrphanWatcher wiring trap (ctor capture `:131-138`, construct `api.py:1910-1931`, `init_dependency_bus` `:2974`, `set_dependency_bus` `:5712`); fast-lane branch facts.
- `research-bootslo-raw.md` + `boot-slo-measurement.md` (this directory) — boot-SLO baseline + gate wiring (greenlight criterion 6).
- `test-strategy.md` (this directory) — Phase-1 acceptance packs (dormant boot smoke, watchdog drill, mutation pins) that the greenlight criteria reference.
- Shared meta kv: `wt.active.fix/phase1-live-defects.engine-arc-p1` (fast-lane branch, active, base `b71742fe3`).
- Code anchors: `daemon/services/orphan_watcher_sweep.py:85, :131-138, :272-296, :82 (grace)`; `daemon/api.py:1906-1911 (kill switch NONE), :1910-1931 (construct), :2974 (init_dependency_bus), :5712 (set_dependency_bus)`; `daemon/config.py:2695 (orphan_watcher_sweep_interval_seconds)`.
- Liveness row 3 evidence: ticks 215–217, "dependency_bus is not wired … relying on the startup sweep", counter 216 no-op ticks, orphan+husk `9c985fc7` unhandled.
