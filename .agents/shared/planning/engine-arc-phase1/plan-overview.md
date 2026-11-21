# Engine Arc Phase 1 — Implementation Plan (Overview)

**Commission:** Central Polling/Event-Management Engine — BUILD (Emperor overruled gate-hold; scores gate 3.80 vs build 2.65, both recorded on ledger ENSEMBLE-38, Discord 2026-11-21).
**Pipeline stage:** 1 of 4 (planner → **architect** → reviewer → approver). This directory is the planner-stage deliverable set.
**Worktree / base:** `/home/nea/ensemble-src-wt-engine-arc-p1` · branch `feature/engine-arc-phase1` · HEAD `aed3eabce` = base `b71742fe3` (v0.31.23) + Phase-0 study docs.
**Binding design:** `../central-polling-engine-study/decision-report.md` §B.2 (frozen engine design — ALL 10 components), §B.1 primitives as engine internals, §B.3 event catalog. Census anchors taken @ `bf4bfde6e`; ALL code anchors in this plan re-verified at `b71742fe3` (18 drifted — see `research-anchors.md` §Q9).
**Date:** 2026-11-21. **Rulings verbatim:** recorded in `decisions.md` Part A.

---

## 1. Objective

Build the central engine (frozen §B.2, all 10 components) **plus** the Emperor-ruled event registry (registration + observability layer only), with self-describing registration (Emperor addendum), shipping **DORMANT** — registered, observing, executing nothing — so the Phase-2 pilot (recommended: OrphanWatcher post-fix) is a single flag flip. Boot SLO 120s (boot_epoch→readyz-green, Emperor-set, CLOSED) is the acceptance gate for the boot-phase component. No module migrates in Phase 1; no existing periodic mechanism is refactored.

## 2. The Five Pillars → Deliverables

| Pillar | Scope | Primary deliverable |
|---|---|---|
| 1. Engine (frozen §B.2) | JobSpec+registration, `engine_jobs`+`engine_runs` (own .sql each), execution classes, single-flight, honest state machine, trap-impossibility, kill switches (FAIL-TO-LEGACY), boot-phase classes + engine-independent watchdog BUILT AND DRILLED, SERVICES_* continuity + dead-knob cleanup, dual-path harness | `phase1..4-plan.md`, `phase6-plan.md`, `phase7-plan.md` |
| 2. Event registry | Kind catalog (§B.3 classes 1–4; class 5 deferred_v2), structured emission w/ ids, per-kind metrics (firing counts, consumers, lag); NO fan-out unification, NO queue-depth triggers (seam named only) | `phase5-plan.md` |
| 3. Emperor addendum | Self-describing register(): service_tag/service_name, category, module_info, description, extensible metadata; metrics labels service_tag+category; ANTICIPATES maintenancer tool + /metrics UI tab (design-for, not build) | folded into P1 (schema/API), P6 (read substrate) |
| 4. Dormant boot | Registered, observing, executing NOTHING by default; dual-path harness makes Phase-2 pilot a flag flip | `phase2-pilot-recommendation.md`, P3/P4 |
| 5. Boot SLO | Measure current vs 120s bar; wire acceptance gate | `boot-slo-measurement.md`, P4 |

## 3. Phase Decomposition (detail in phaseN-plan.md; authority: those files)

| Phase | Name | Objective | Est-h (low/high) | Depends |
|---|---|---|---|---|
| P1 | Substrate | 2 migration files + models/repo + EngineConfig + self-describing JobSpec/register() + SERVICES_* continuity + dead-knob cleanup | 22/35 | — |
| P2 | Execution core | Tick loop, 3 concurrency classes, single-flight, honest state machine (incl. SHADOW skip), durable run record | 28/38 | P1 |
| P3 | Trap-impossibility + controls | Getter-at-tick deps, LOUD boot assertion, dead=true, kill switches fail-to-legacy, dual-path harness | 22/37 | P2 |
| P4 | Boot phase + watchdog | Boot-phase classes + jitter, engine-independent watchdog built AND drilled, boot-SLO ≤120s wire, the ONE api.py lifespan edit | 20/33 | P3 |
| P5 | Event registry v1 | Kind catalog over existing substrates, bounded emission ring, per-kind observability, labeled failure postures | 18/31 | P1 |
| P6 | Observability shape-readiness | `/api/engine/*` read substrate (3 GETs), read-time union with `job_metrics()`, GATED metrics-family ceremony (default deferred) | 9/15 | P2,P3,P5 |
| P7 | Test pack buildout | 10 pack registrations, mutation matrix, dormant/diff-audit/fence-sweep legs | 13/23 | P1–P6 |
| P8 | Staged release | Slot derived live at stage time (W9), delta-only rollback adjudication, dormant disclosure, NO promote | 5/10 | P7 |
| **Total** | | | **137/222 (≈180 mid)** | |

**Band verdict: INSIDE 118–222.** ≈11% above the 162h anchor; excess priced in `phase1-plan.md` §0.2a: 26-site dead-knob cleanup (≈5h), watchdog drill (≈4h), diff-census record (≈1h) — carrying costs the anchor folded in. Test estimate cross-check: `test-strategy.md` 28/48h vs P7 13/23h + per-phase AC test effort (reconciled: P7 is pack assembly; component tests ride each phase).

## 4. Dependency & Parallelism

```mermaid
flowchart LR
    P1[P1 Substrate] --> P2[P2 Execution core]
    P2 --> P3[P3 Traps + controls]
    P3 --> P4[P4 Boot + watchdog + SLO]
    P1 --> P5[P5 Event registry]
    P2 --> P6[P6 Observability]
    P3 --> P6
    P5 --> P6
    P4 --> P7[P7 Test pack]
    P6 --> P7
    P7 --> P8[P8 Staged release]
```

Parallel lanes: **P4 ∥ P5** after P3/P1 respectively; P6 fans in. With the fast lane (`fix/phase1-live-defects`) running concurrently: file overlap is **`daemon/api.py` only** (live-defects regions `:1226-1272`/`:3018-3041`/`:4532-4547`; engine P4 lifespan ~`:3740` + shutdown <`:4600`, P6 include_router ~`:6359`) — textually disjoint, either merge order auto-merges; P4 must additionally avoid the strings `tests/test_settings_source_order_pin.py` anchors on.

## 5. Research Base (2 explorers, both HIGH confidence)

Key findings that shaped the plan (full: `research-anchors.md`, `research-bootslo-raw.md`):
- **Anchor drift is real:** 18 census anchors moved bf4bfde6e→b71742fe3 (all old→new listed); event-surface anchors unchanged; both live traps (OrphanWatcher born-dead ctor-capture `orphan_watcher_sweep.py:131-138`; JobLockSweep Template-B PEXIT `:241-250`) verified INTACT.
- **Migrations:** 127 .sql files (blueprint's "119" stale); IF NOT EXISTS mandatory (create_all runs first — bare CREATE silently no-ops); `engine_jobs`+`engine_runs` get separate files.
- **Awaited boot passes: 29** (+7 create_task +1 thread cluster), enumerated per-line in phase4 §4.1 — the census "~11" was a subset. `MANAGED_KEYS` = 19 live (AST-counted, pinned) → engine switches stay env/config tier in Phase 1. RDRS interval read site corrected: `pool_orchestrator.py:370` (no `worker_pool/` package).
- **Fences (30 surveyed; 5 material — see §7 Risks):** zero existing fences on `engine_jobs`/`engine_runs`/`ENSEMBLE_ENGINE`/`job_metrics` (names unclaimed); closed metrics census forces zero-family registration with a gated ceremony; `job_metrics()` byte-shape pin forces read-time union, never edit.
- **Boot telemetry:** no dedicated duration field exists; gate `READYZ_BUDGET_S=120` clocks a DIFFERENT window (post-handback); never subtract `boot_epoch` (DB clock) from `detail.checked_at` (host clock) — ~4s offset.

## 6. Boot SLO — Verdict (detail: `boot-slo-measurement.md`)

**Typical boots are COMFORTABLY UNDER 120s.** 19 live promote boots: median 13.1s / p90 22.8s / max 23.2s (= 19.3% of budget, ~5× headroom); hard-reboot drill 13–15s; worst cold-DB (sandbox fresh-PG) 67s ≈ 1.8× headroom. Near/over cases are **PRE-EXISTING conditions, not Phase-1 regressions**: (a) 67s sandbox cold-DB shape; (b) 2026-10-09 v0.18.5 DB-outage promote — readyz never green in budget (probe-timeout-under-load class, predates this lane ~6 weeks). Inherited "302s"/"~54s" figures unverifiable in files — marked not-counted.

**Gate wiring (dispatcher adjudication):** host-frame gate — start anchor owned by whatever launches the boot (test harness / promote lane; `boot_smoke.sh:496-524` already polls, raise `POLL_TIMEOUT` 45→≥120s) → poll `/readyz` `status=="ready"` → delta ≤120s. Because unit-start precedes the `boot_epoch` stamp, a host-frame pass implies the Emperor's boot_epoch-frame SLO **a fortiori**. Literal-`boot_epoch` binding remains available as durable telemetry (add `first_ready_db_clock` to the boot record, `boot_record.py:269`) — routed follow-up, not gate-blocking. Gate scope: all boot classes this lane can produce (dev cold-DB included); live-promote stays the binding reference class.

## 7. Top Risks (full registers in phase docs)

1. **F-23 diff-census record (~27 entries, release-blocking)** — `git diff --name-only` vs fixed LANE_BASE with enumerated allowlists; the record is maintained by the changing lane. Single biggest hidden cost the fence check surfaced; owned by P6.
2. **F-29 MagicMock(spec=ServicesConfig) fixture** (`tests/conftest.py:535`) — dead-knob deletion explodes at fixture construction unless all 26 call sites are cleaned atomically (P1 T1.12 = ONE task, not two).
3. **`daemon/api.py` shared file with the fast lane** — textually disjoint today, but both lanes land in one file; explicit-pathspec commits + merge-order check at finalize (house concurrent-commit rules).
4. **Clock-frame trap in boot measurement** — any future gate that mixes DB/host clocks inflates by ~4s and can false-fail near the bar; the a-fortiori host-frame design eliminates the dependency.
5. **Shadow-evidence dependency of the pilot greenlight** — if the engine ships default-OFF (not observing), `would_fire`/`dead`/shadow evidence never accumulates and the Phase-2 greenlight criteria become unsatisfiable (see adjudication D-3).
6. **Watchdog must be DRILLED, not assumed** — evidence artifact required (P4 + `test-strategy.md` §4: stall-leg + cancel-leg drills with independent-recovery assertion).

## 8. Cross-Document Reconciliation (dispatcher adjudications — record in decisions.md Part C)

- **D-1 Test authority:** `test-strategy.md` owns test FILE names (its §1.1 = stable contract) and the mutation matrix (23 legs = authority; phase docs' "≈34 per-AC" = upper envelope).
- **D-2 Boot-SLO frame:** host-frame gate (b) adopted — see §6. `test-strategy.md` §5's timed-probe carrier is (b)-shaped (harness owns the start anchor); no inconsistency survives this ruling.
- **D-3 `ENSEMBLE_ENGINE_ENABLED` polarity: ship `enabled=true` + `shadow_mode=true`.** Pillar 4 verbatim is "REGISTERED, OBSERVING, EXECUTING NOTHING" — observing requires the tick loop to run and record shadow evidence; default-OFF would make the Phase-2 greenlight criteria (≥2 cycles shadow, zero false-dead) unsatisfiable and violate the pillar's "OBSERVING". Execution remains impossible by construction: no module carries `SERVICES_<MODULE>_USE_ENGINE=1` in Phase 1. `ENSEMBLE_ENGINE_ENABLED=false` stays the one-flag full-off. (Phase docs flagged this fork; dispatcher rules; architect may veto in review.)
- **D-4 Numbering:** Emperor's frame adopted — build = Phase 1, first migration (pilot) = Phase 2. Study §C.2's "Phase 1 — pilot" predates the BUILD ruling; mapping recorded.
- **D-5 RDRS path correction:** `pool_orchestrator.py:370` (there is no `daemon/services/worker_pool/` package).

## 9. Fast Lane & Release Strategy (facts from phase docs; recommendation owned by decisions.md)

Coupling facts: file overlap `daemon/api.py` only, textually disjoint (auto-merge either order); the Phase-2 pilot **structurally depends** on the OrphanWatcher fix (`fix/phase1-live-defects`) landing in `latest` — the pilot's parity baseline is unanswerable while the service is born-dead; no duplicate work (fast-lane late-bind fix = one service; engine `dead=true` contract = the general detector — complementary). Release slots derived LIVE at release-prep per W9; NO live promote (Emperor ceremony).

**Dispatcher recommendation (for decisions.md to adopt/refine): TWO staged releases — defects-first.** The two live-defect fixes are small, independently verifiable, and ready long before the 137–222h engine build completes; holding them for one combined release delays live remediation (OrphanWatcher born-dead + 70MB bash-reaper leak) for no coupling benefit, since the api.py regions are disjoint and the pilot only needs the fix in `latest` by Phase-2 time, not by engine-merge time.

## 10. Phase-2 Pilot Recommendation (detail: `phase2-pilot-recommendation.md`)

**OrphanWatcherSweepService, post-fix** — reconcilers family, MAIN_LOOP class, 90s cadence. Strongest reasons: (1) exact motivating specimen for the getter-at-first-tick primitive (ctor-capture born-dead verified intact at this tree; 216 no-op ticks live); (2) smallest single-topology blast radius, and the fast-lane fix isolates engine behavior from defect; (3) gains a kill switch it never had, with a dormant-shadow before/after baseline. Greenlight criteria are measurable (≥2 release cycles shadow with zero false-dead; fix landed+verified; boot-SLO gate green dormant; watchdog drill evidence; mutation pins in PACKS.md). Alternatives assessed and rejected-as-first with reasoning.

## 11. Out of Scope (explicit)

Migration tail (~20 eligible modules, ≈4–10h/module, each its own gated commission — one-by-one rule) · fan-out primitive unification (GAP-15) · queue-depth triggers (GAP-14, seam named) · wakeup churn (§B.4, separate commission) · families 8–9 + scheduler substrate (standalone permanently) · any live contact beyond read-only telemetry · NO refactoring of existing periodic mechanisms (dormant boot).

## 12. Open Questions Carried Upward (architect stage; none Emperor-blocking)

1. **Shadow duration before pilot flip** — ≥2 release cycles (conservative) vs early flip on OrphanWatcher's own health? (`phase2-pilot-recommendation.md` presents both.)
2. **Second migrant ordering** — healthy-sweep (parity proof) vs PEXIT-class (JobLock/SourcesGc)? Phase-2+ concern; registry evidence will inform.
3. **D-3 polarity ratification** — dispatcher ruled enabled+shadow; architect review may veto.
4. **Start-anchor build-side emit** — confirm the harness/promote-lane owns it (dispatcher ruling) or route a daemon-side emit (deferred-task seam `api.py:1082-1093`) if promote-lane capture proves brittle.

## 13. Provenance

Produced by the planner-stage dispatch: 2 explorers (`research-anchors.md` — anchors/fences/hook points; `research-bootslo-raw.md` — live read-only boot telemetry) + 3 workers (phases w/ `grep-fence-check-before-design-substrate` skill applied — 30 fences surveyed; tests; slo+pilot) + this synthesis (planner, aggregator boundary). All workers planning-only: zero code, zero test runs, zero git mutations; commit lands via the decisions lane. Census anchor caveat: re-grep at use time — 18 anchors already drifted one base-version apart.
