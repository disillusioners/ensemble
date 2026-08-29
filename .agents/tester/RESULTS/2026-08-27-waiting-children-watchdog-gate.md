# Independent Verification Gate — #8 WAITING_CHILDREN Hang Watchdog

- **Branch**: `feature/waiting-children-watchdog` @ `606b1bed` (range `85ae6e72..606b1bed`, 4 commits: ea902bb8 → fe076043 → eb69d98d → 606b1bed)
- **Date**: 2026-08-27
- **Diff verified**: 7 files (5M + 2A), +2597/−2 — config.yaml, daemon/api.py, daemon/config.py, daemon/repositories/instance/repository.py, daemon/services/instance_messaging.py, daemon/services/waiting_children_watchdog.py (NEW, 728 lines), tests/unit/services/test_waiting_children_watchdog.py (NEW, 47 tests). (Leader's "~8 files / −216" estimate was off — the set_injection→enqueue_message rework collapsed to net-additive; cosmetic.)
- **Dispatches**: 21 workers (1 P0, 1 targeted, 1 pure-hang audit, 1 shared-surface, 1 probe, 1 mockfid, 1 jobs-cleanup, 6 packs, 7 sweeps, 2 base-evidence), 0 direct executions.

## VERDICT: ✅ PASS — watchdog gate CLOSED; #8 CLEARED FOR MERGE to `latest`
**Zero new failures attributable to this branch.** One 🟠 ESCALATION (below): 53 base-inherited failures from the CF-125s `buffer_response_header` fixture drift — not this branch's debt, but it will dirty every future gate until the owning lineage fixes it.

---

## 1. 🟠 ESCALATION (not a blocker — base-inherited): `buffer_response_header` fixture drift, 53 tests

- **Family**: production reads `self._config.llm.buffer_response_header` at 3 sites (`instance_lifecycle.py:916`, `title_generation.py:104`, `child_reports.py:766`); 5 test files build config mocks/SimpleNamespaces lacking the attribute → AttributeError.
- **Files/counts**: `test_llm_config_override.py` ×15, `test_llm_failover_v2.py` ×6, `test_llm_failover_v2_adversarial.py` ×11, `test_llm_failover_v2_resilience.py` ×4 (unit-ir sweep, 36F) + `test_llm_load_balance_integration.py` ×17 (top-ir sweep, +17 vs d808297e baseline).
- **Base-evidence (both worktree runs)**: ALL 53 reproduce **identically at `85ae6e72`** — and pickaxe `-S buffer_response_header` shows the introducer IS `85ae6e72` itself ("feat(llm): send X-LLMProxy-Buffer-Response header on all proxy LLM requests"). Branch range: 0 references; none of the 3 failing production files touched; config.py diff contains no llm/buffer keywords.
- **Disposition**: pre-existing-at-base → not this gate's blocker. **QUARANTINE.md family row added** so future gates classify it. **Routing**: the fix (add `buffer_response_header=False` to the 5 files' config mocks, or `getattr(..., False)` hardening) belongs to the CF-125s lineage owner — ~53 red tests will greet every branch cut after this commit until fixed.

## 2. Verification results (all GREEN)

### P0 statics — every contract point cited
Interval default 3600 + `ge=1` fail-fast (config.py:891-907); threshold 3600 + `ge=0` (:909-928) + constructor belt-and-braces; predicate strict `>` (repository.py:2158) + NULL/paused/WC-parked/terminal exclusions each cited + pinned; cooldown `set[tuple[parent,child]]` (:328), episode = until purge; dual purge (parent-left-WC :606-615; child-terminal-any-path :625-652) + scanned_ok-gated sweep (:574-586) with **scanned_ok preserved** (transient scan error never clears cooldown — pinned by TestEpisodeEndScanErrorPreservesCooldown); lifespan task api.py:551-557 with disabled→no-task at TWO levels + construction-fail try/except (:533-548); 3-layer exception isolation (per-parent :549-563, per-cycle :699-716, shutdown CancelledError contract); dialect SQL PG EXTRACT vs SQLite julianday (repository.py:2092-2097) with parity tests. Wake primitive = `manager.enqueue_message` (:512-525, source `system:watchdog`, priority 0, structured metadata) riding the real single-transaction wake path (MessageQueue+Task one commit, WC→RUNNING :1533-1538, notify_work :1711-1712). **NOT set_injection** — the v1 flaw.

### Pure-hang acceptance vacuousness audit — **REGRESSION-CAUGHT (the lock is REAL)**
Real `InstanceMessagingService` + real engine; **zero service-boundary mocks** (12-double inventory: all infrastructure/spy/time-config). The v1 regression (set_injection) trips at `notices_enqueued==1` via AttributeError (service has no set_injection); a RAM-FIFO re-plumb trips 4 independent asserts. 5-point checklist: 4.5/5 — WC→RUNNING ✓ (re-read from DB), source+watchdog_notice+hung_children ✓, PENDING Task via real single-tx ✓, notify_work ✓ (decision in real code, spy at the edge), set_injection NOT called ✓. Second-tick runs TWO REAL ticks (enumeration-exit) + unit twin pins cooldown-retention-while-parked. Minor: `hang_threshold_seconds`/`age_seconds` unasserted on the durable row (kwarg-level only; verbatim pass-through confirmed statically); StaticPool in-memory engine (convention deviation — corruption pattern structurally absent here).

### Shared-surface drive (instance_messaging.py:2291-2329) — STRICTLY ADDITIVE ✓
+26/−2; `is_system_origin` OR-ed into the internal-report branch; warning gated on `is_internal_report`. **17 dispatch scenarios + 4 E2E enqueues on a real service + file-backed engine**: every production source (api, api_resume_fallback, scheduler, cascade_resume, telegram/discord/slack, agent:*, internal_agent:* incl. job_event + unknown, internal_report:*, internal_error_report:*) **byte-identical to base**; `system:watchdog` → original_source lookup (no stamp, no false warning), content-shape-immune; **MessageType.HUMAN confirmed** at enqueue; `is_completion_report` block byte-identical/untouched; child-report delivery unaffected. Only `system:` producer in the codebase is the watchdog. Latent note (not a defect): JQ-side `_dispatch_completed` (message_processing_pipeline.py:700-708) doesn't mirror the guard — unreachable for watchdog traffic (messaging path), F2-backlog-adjacent.

### Behavioral probe (real service objects, real engine) — 16/16
Hung e2e: parent WC→RUNNING + row source/metadata + PENDING task + notify + no set_injection; second-tick no-dup (re-parked); healthy no-op; strict `>` boundary (−1s/+5s); NULL/paused/terminal×4/WC-parked exclusions; paused-parent skip; dual purge + episode-2 re-notice; per-parent isolation (2×2); disabled → zero-stat no-op; construction-fail ValueErrors caught; SQLite julianday parity (0.0011s delta vs Python); 3-layer exception isolation (repo raise / per-parent raise with sibling succeeding / loop raise with next tick). **Zero defects.**

### Targeted + mock fidelity
Suite **47/47** in 1.46s (17 classes; acceptance ×2, ge-bounds 3/3, dialect parity 3/3). Mock-fid **CLEAN/0 divergent** — two-layer split (mocked-boundary units + real-path acceptance) exactly as sanctioned; dialect parity honestly scoped (string-render + real SQLite execution); TOCTOU test flips status via real UPDATE; no patch-target hazards; real clock+backdated inserts (no frozen time). INFO gaps: G1 PG-branch never executed on real PG (matches the excluded PG-drill-smoke backlog item); G2 exact-equality (age==threshold) untested at ±1s; G4 JQ-guard mirror; G5 lifespan wiring untested in-suite (probe covered loop+construction-fail behavior); G6 StaticPool; G7/G8 cosmetic.

### Regression packs & sweeps
tools 991/986P/0F/5-deselect · api 213P/8S exact · concurrency 98P/74S exact · msg-regression 28/28 · msg-routing 16/16 — **instance_messaging.py in direct blast radius: green.** jobs_cleanup **41/41** (stale failure claim REJECTED — council right). Sweeps ×7: subdirs 8F = **proxy_phase1 isolation confirmed exactly** (+47 collected/+47 passed = watchdog tests, zero new); unit-ah 15F+4E known; unit-ir 44F = 8 known + 36 = §1 family; unit-sz 50F+2E known (watchover 47); top-ah 3F known; top-ir 91F = 74 baseline + 17 = §1 family; top-sz 12F known + **spawn_team_members 44/44 holds**. api_router_extraction family grew 1846→1933 lines (same family, drift note).

## 3. Recommended follow-ups (non-blocking)
1. **[Route to CF-125s owner]** Fix the 53-test `buffer_response_header` fixture drift (5 files, add attr to config mocks or getattr-harden the reads).
2. Acceptance-test hardening (~4 lines): assert `hang_threshold_seconds` + `age_seconds` on the durable row.
3. JQ-side `system:*` mirror decision (F2 backlog family) + boundary validation at enqueue.
4. Equality-boundary test (age==threshold, ±1s) + lifespan-wiring unit test.
5. StaticPool→file-backed migration for the new fixtures (convention).

## 4. Worker instances
eebd2373 (P0), 2d55ebf0 (suite), 39910142 (pure-hang), d5f1851f (shared), 20f73ac9 (probe), b62583af (mockfid), b26df2f4 (jobs-cleanup), bf19e14b (tools), 58b1c83f (api), 7dd0786e (conc), e5b6bfb9 (msg-reg), 228e2927 (msg-route), 4735b16b/f2adf79f/f2a1ab97/85bf6c9b/8ff465b6/46d07217/d6952d22 (sweeps ×7), 8fc0a75d + 9503327f (base-evidence ×2).
