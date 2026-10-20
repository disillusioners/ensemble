# INDEPENDENT FUNCTIONAL VERIFICATION — agent-restart commission (builder-stage)

- **Date:** 2026-10-20 (commission dated 2026-10-19) · **Tester instance:** c08c503a (Test Leader) · 14 worker dispatches, 0 re-dispatches, 0 timeouts
- **Worktree:** `/home/nea/ensemble-src-wt-agent-restart` · branch `feature/agent-restart` · **HEAD `55c45ab2a`** (verified) · base/implementation-spec commit `1fecf7c3f` (spec rev5; HEAD doc = rev5 + 22-line post-build reconciliation — 9 cite-refreshes + 3 review folds; `pinned_spec_sha` MUST stay `1fecf7c3f`, blob `1eada5670`)
- **Change surface:** 36 files, +3684/−375, 13 commits (council-r4 range 1fecf7c3f..80b6d6939 + 4 fix-fold commits incl. both must-close advisories)
- **Stage-only:** no bump / stage / merge / push / promote performed. Verification-only: ZERO source/test modifications by any worker (one kms_issue side-effect restore, disclosed §Hygiene).

## VERDICT: 🟢 PASS-WITH-NOTES — promote-ready from the testing lane at builder-stage; r2 staged-payload verification still pending (giter merge+stage first)

**Zero branch-caused failures anywhere in the independently captured scope.** All 7 commission claims + extras verified (2 with qualifications, 3 coverage gaps disclosed). Baseline-credibility independently CONFIRMED on the touched areas by executed base-vs-HEAD parity legs (47/47 failing IDs byte-identical at base).

---

## 1. Per-claim verdict table

| # | Claim | Verdict | Evidence (worker → verbatim anchor) |
|---|---|---|---|
| 1 | genuine-user turn → SCHEDULED (receipt `run_id=…`; deferred ordering; inherited variant; demo stays free) | ✅ PASS | sim `6bbc8792` 12/12 (receipt `RESTART SCHEDULED — run_id=r-20261020-023228-5940 … executes_after_turn: yes / trigger: post-turn-callback armed`); unit `05881bc2`: `test_live_direct_window_schedules` (`assert "RESTART SCHEDULED" in out`), `test_live_inherited_window_schedules` (`class=inherited`), `test_env_scope_demo_arms_without_any_window` (demo free, no window seeded) |
| 2 | agent-initiated / no user turn → refused `no-user-provenance` (incl. inheritance chain, wake-turn no-mint, mixed-kind coalesce) | ✅ PASS | unit: `test_live_restart_agent_initiated_refused_no_provenance` (+ `pending_op is None`, lock absent ⇒ ZERO journal mutation), `test_t0d_restart_wake_mints_no_user_window` (`RESTART_WAKE_SOURCE == "system:post_restart_arm_notify"`), `test_second_restart_on_wake_turn_refused`, `test_mixed_kind_coalesce_partition` (`len(groups)` 1↔2, coalesce cap), `test_internal_invoke_and_wait_never_inherits` (N1) |
| 3 | promote-in-flight → refused `pipeline-busy` (interlock family) | ✅ PASS (2 tokens doc-pin-only — gap G-3.2) | unit ×4: `test_pipeline_busy_in_flight_txn_names_run_id` (`reason=pipeline-busy`, run_id echoed), `test_second_arm_while_active_pipeline_busy`, lock-held/pending-op variants. `adoption-in-progress` + `pipeline-not-settled`: pinned in `RESTART_TOKENS` doc set only — no behavioral invocation (mechanism mirrors the promote interlock that IS tested) |
| 4 | cooldown/budget/min-uptime refused; budget survives restart; abandoned arms consume | ✅ PASS (2 pins absent — gaps G-4.1/G-4.2) | unit: `test_min_uptime_epoch_unavailable_fails_closed` (`restart-too-soon`), `test_budget_cap_refused_and_window_sliding` (cap-of-3), `test_budget_window_boundary` (86390s→2, 86460s→0 sliding), `test_budget_increment_first_in_arm_envelope` (`order.index("budget") < order.index("op")`), `test_cooldown_active_refused` + `test_cooldown_unparseable_fails_closed` (`cooldown-active` both) |
| 5 | post-restart notify contents (outcome, version, per-gate livez/readyz, APPLIED overrides, None≠empty) | ✅ PASS-QUALIFIED | unit: `test_wake_body_contract` (`livez=pass`+`readyz=pass` from journal detail; `stamped_at=…`/`expires`; derived-scope hatch `systemctl` present, `restart_current` absent), `test_supervision_scope_both_slice_fixtures` (`sudo systemctl restart ensemble-main.service`, `--user` absent on system slice), `test_restarts_section_renders_rows` (`limits.worker_pool_size: 5→7 (applied)` EXACT). sim runtime: `restart_gate` detail `livez=FAIL latency=6.0s budget=5s` + `restart_gate` BEFORE `halt` terminal coexistence. QUALIFICATIONS: (a) None≠zero-dict pinned only as disjunction `"unavailable" in body or "applied" in body` (G-5.1); (b) sim's readyz-green is boot-grace-suppressed, not a true probe-green (sim F-7 — the FAIL path is truthfully rendered; the pass path's false-green is a sim-assertion weakness); (c) sim did not exercise a populated overrides capture (F-5 — MagicMock manager short-circuits to `[]`); the unit pins cover the render |
| 6 | deferred execution ordering (receipt immediate; execution post-turn; daemonized script) | ✅ PASS | unit: `test_armed_returns_scheduled_no_spawn` (+ demo mirror `no_spawn == []`); sim runtime: arm 02:33:20Z → executor refusal 02:33:25Z (no minted env) → execution 02:33:37Z; `systemd-run` unit-prefix argv pinned in `test_restart_kind_argv_lock_adoption_and_unit_prefix` |
| 7 | REAL restart-sim (`tests/test_agent_restart_sim.sh`) + independent fence review | ✅ PASS 12/12, 22.02s | sim worker: 12/12 assertions (arm scheduled, `provenance_ok=True` carrier, run_id extract, lock re-owned, guard rc=78 w/o env, `live-restart-refused` journaled, `ENSEMBLE_UPGRADE_LIVE` never set, guard accepts env, gate-before-halt, per-gate detail, readyz recorded, pending wake `kind=restart`). FENCE: **guard-effective** (pattern-matches resolved paths, unconditional pre-write, exit 99); mktemp FAKE_HOME + `export HOME` makes `resolve_env live` resolve the FAKE install (`dir=/tmp/ens-restart-sim.*/agents-ensemble port=39713`); ports 9797/7979/8079 refused loudly; live-system safety PROVEN before/after (live journal sha256 identical, live pids/ports unchanged, 0/400 sim-port listeners, no sim-owned PG conns). Sim defects F-1..F-8 in §4 (fixture/hardening, none block the verdict) |
| E1 | dry_run verdict-only / zero-mutation | ✅ PASS (2 pins absent — G-E1a/G-E1b) | unit: `test_dry_run_live_full_verdict_zero_mutation` (starts `RESTART VERDICT (dry-run, zero mutation)`, renders full rail verdict, journal bytes byte-equal, no marker) + `_refusal_reason(out) is None` (never calls `_refusal()`) |
| E2 | typed token stability (×8) | ✅ PASS | isolation `de8a96f8` + unit pin `TestRefusalTaxonomy` (`RESTART_TOKENS` = 16 entries; the 8 restart-only = exactly {adoption-in-progress, no-user-provenance, pipeline-not-settled, reason-required, restart-budget-exceeded, restart-too-soon, restart-under-burst-abort, unknown-mode}); `live-restart-refused` re-homed as script-guard journal event (lib.sh:737-749), absent from tool vocabulary. NOTE: pin home is TEST-side (`test_upgrade_tools.py:3521-3542`), doc-presence class |
| E3 | journal audit fields (who/what/why, armed/executed, boot outcome, applied overrides) | ✅ PASS | sim verbatim `restart_audits[r-…]`: `provenance_kind=direct, provenance_expires_at, armed_at, executed_at=null, outcome=pending, budget_after_increment=1, wake_source=system:post_restart_arm_notify`; `pending_wakes`: `provenance_class/depth/root_message_id/root_source/stamped_at/expires_at/abandon_after/kind/env/status` — spec §6.2 field-for-field |
| E4 | seal discipline (maintenancer rule.md + constants.py re-pinned same-merge; 7 base-era rows remain giter carry) | ✅ PASS | isolation: both rows MATCH at HEAD (`df41a67f…` / `66e91a67…`); re-pin commits provenance-labeled (`01be4f713` explicitly closes council Finding 1 as branch-introduced; `74e9168a2` maintenancer); 28/35 baseline rows match; 7 mismatches ALL base-era (ancestor-proven last-touch `f62bbb2e4`/`fafbcdef9`/`b3f37a59c`), ZERO branch-introduced. Executed: audit tripwire 46/46 green; e2e phase-D fails ONLY on the 7 pre-existing rows (base-parity proven, §2) |
| E5 | isolation (lib.sh guard, ENSEMBLE_UPGRADE_LIVE, factor-2) | ✅ PASS | isolation: `require_live_guard` function sha `b434b88c01c5…` byte-identical base↔HEAD (lib.sh delta = ONE +26-line hunk = the new sibling only, sole call site restart.sh:99); `classify_user_origin` sha `fc220f3b…` + armed 3-factor gate block (167 lines) sha `67facda3…` byte-identical; `ENSEMBLE_UPGRADE_LIVE` ZERO hits in restart.sh/stop-ensemble.sh/sweep — every hit promote-lane-gated or doc; sole `ENSEMBLE_RESTART_LIVE` mint = manager.py:5123 gated `spec["provenance_ok"]`; `EXECUTOR_ENV_ALLOWLIST` strips ambient; `_TERMINAL_EVENTS` original 9 untouched (C-3 trap avoided), kind-aware reader verified |
| E6 | no migration (N8 / rollback_safe basis) | ✅ PASS | `git diff --name-only base..HEAD -- daemon/migrations/versions/` EMPTY; 105==105 both revs |

## 2. Independent baseline-credibility check (council-mandated) — CONFIRMED on touched areas

Developer claim: whole-tree 17166P/106F/23E/63S, ALL failures pre-existing at base; tools+services parity 6692P base ≡ HEAD. **Not accepted on trust — independently executed.**

Method: HEAD failing-set capture + fenced BASE leg (`/tmp/agent-restart-base-1fecf7c3f`, detached @ `1fecf7c3f`, `uv sync --frozen`, Python 3.14.7 / pytest 9.0.2 — interpreter byte-matched to the HEAD leg; `daemon` resolved inside each worktree, verified). Identical commands both legs: `timeout 300 env -u POSTGRES_{DB,HOST,PORT,USER,PASSWORD} -u DATABASE_URL .venv/bin/pytest <scope> -q --tb=short -p no:randomly [-n 8]`.

| Leg | HEAD @55c45ab2a | BASE @1fecf7c3f | Set-diff | Verdict |
|---|---|---|---|---|
| targeted 10 files (branch-touched + chart) | 10F/430P/4D (2 packs: 367P+3F, 97P+7F) | 10F/433P/4D, 25.5s | **10/10 identical, 0 extra, 0 missing** | ✅ pre-existing |
| tests/unit/tools (dir) | 13F/3787P/7S = 3807, 135s | 13F/3756P/7S = 3776, 159s | **13/13 identical**; Δpass = +31 = exactly the new green `test_agent_restart.py` (31/31) | ✅ pre-existing |
| tests/unit/services (dir) | 9F/2904P = 2913, 93s | 9F/2904P = 2913, 145s | **9/9 identical** | ✅ pre-existing |
| tests/job_queue (dir) | 15F/2056P/38S = 2109 sel, 99s | 15F/2056P/38S = 2109 sel, 143s | **15/15 identical**; both branch-modified files 15/15 GREEN on BOTH legs | ✅ pre-existing |

- **47/47 failing IDs byte-identical at base — ZERO branch-introduced failures.** Every set arithmetically reconciled; zero collection errors; zero xdist anomalies.
- tools+services HEAD passes = 6691 ≈ developer's 6692 (±1 scope rounding) with IDENTICAL failing sets — the parity claim is corroborated, not just the count.
- Failure families (all pre-existing, cross-ref QUARANTINE.md standing rows where present): memory.md ×9 (2026-10-14 archive sweep `3c307e8d4` deleted `agents/maintenancer/memory.md`); stale seal rows ×1 (7 base-era rows); job_queue_proxy_phase1 status ×7; scheduling time-bomb ×1; service_tool pid_dead ×1; inner_soul identity ×3; knowledge model-kwarg ×2; planner subtree allow ×2; prompt md/fence integrity ×5; snapshot gate drift ×1; job_queue ×15 (MagicMock-JSON ×3, watch_events drift ×3, fake_sync arity ×3, census staleness ×2, tool-count/macOS-path ×2, tool-ordering/event-loop ×2).
- **Residual (disclosed):** whole-tree 17k re-run NOT performed (infeasible under the 5-min pack discipline); failures OUTSIDE the captured scope (~59F+23E by developer count) remain developer-asserted with per-suite base-checkout evidence — the touched-areas slice, which is what this commission can regress, is fully proven. My legs ran Python 3.14.7 (developer baselines were 3.13-lineage; both legs of every comparison used the SAME interpreter, so parity is internally valid).

## 3. ensure.md Core mapping (blast-radius scoped)

| Requirement | Status | Evidence |
|---|---|---|
| Critical: no regressions in changed packs | ✅ PASS | every restart-surface pack green at HEAD (sim 12/12; targeted: `test_agent_restart` 31/31, `test_upgrade_tools` 173/173, arm-notify journal 40/40 + sweep 33/33, source_reservation 38/38, job_queue routing/edge 15/15, chart audit 46/46, maintenancer e2e resolution 13/13); the only reds in pack files are the 9 memory.md family + 1 stale-seal — base-parity proven §2, quarantined |
| Critical: concurrency/deadlock integrity (`concurrency_atomic_unit_test`) | ✅ PASS | 99P/0F/74S **baseline-exact**, 83s @ HEAD — `manager.py` funnel change regression-free in that lane |
| Critical: no sync DB calls on event loop | ✅ PASS | same pack (thread-identity tests included) |
| Critical: `dev.sh --timeout-graceful-shutdown 10` | ✅ PASS | dev.sh:142 (static) |
| Important: await-caller grep | N/A — out of change set | the three named functions untouched by this branch |
| Release Gate | NOT triggered — deferred to r2 | builder-stage, stage-only; no promote in this round; restart-sim is the feature-level E2E equivalent and PASSED |
| Contradictions / Improvement Notices | NONE | all validations ran as packs with dual-layer timeouts; ensure.md respected as written |

## 4. Findings (report-only; developer owns fixes)

**Sim/test-fixture defects (in `tests/test_agent_restart_sim.sh` — pack still PASSes 12/12):**
- **SF-1 🟠** stop-stub writes `$INSTALL/stub-stop.log` but `$INSTALL` never reaches that shell → attempts `/stub-stop.log` (EACCES as `nea`; would succeed as root). TERM-record leg silently degraded + out-of-temp write attempted. (:205-210)
- **SF-2 🟡** empty-`FAKE_HOME` (mktemp failure) degenerates the escape-guard pattern to `/*` — guard would accept any absolute path. (:63-85)
- **SF-3 🟡** port `8088` (ensemble self-system) not in the sim's port-refusal set (only 9797/7979/8079).
- **SF-4 🟢** no script-internal self-timeout; internal bounds are per-probe budgets (5s) — dual-layer held via the command-level 300s wrapper.
- **SF-5 🟠 (coverage)** applied-override capture unexercised in the sim (MagicMock manager → `[]`); renderer collapses None/`{}`/non-dict → indistinguishable "no overrides". Unit pins cover the populated render (`5→7 (applied)`) but the None-discriminator is a disjunction only (G-5.1).
- **SF-6 🟡 (fidelity)** armed op records `scripts_dir=/home/nea/ensemble-src/scripts/upgrade` (ambient `ENSEMBLE_UPGRADE_SCRIPTS_DIR` wins rung 1) — pre-existing known residual (tool-lane scripts-dir), shell leg sidesteps by copying the worktree bundle.
- **SF-7 🟠 (assertion weakness)** `readyz=pass(one-shot)` is boot-grace-suppressed, not a probe-green; assertion 11 only checks presence — cannot catch a false-green readyz. (`/livez=FAIL` IS truthfully rendered.)
- **SF-8 🟢** 41 pre-existing `/tmp/ens-fake-home-*` dirs (2026-10-19/20) — developer housekeeping.

**Coverage gaps (behavior holds; pins absent — improve-lane candidates):**
- G-3.2 `adoption-in-progress`, `pipeline-not-settled` (and `reason-required`) — `RESTART_TOKENS` doc-pin only, no behavioral invocation.
- G-4.1 min-uptime `600s` numeric constant unpinned (only the fail-closed path is).
- G-4.2 budget-survives-a-REAL-restart unpinned (file-store structurally durable + exercised at file level; no restart-boundary test).
- G-5.1 None ≠ zero-dict discriminator (disjunction only).
- G-E1a dry-run emits no `run_id` — unpinned (behavior holds). G-E1b dry-run `overrides_pending` render — unpinned.
- G-misc kind-partition coalescing behavioral pin lives in `test/drills/wake_drill_driver.py:423-441` (outside pytest corpus) + sweep unit (33/33 green) — noted for completeness.

**Repo hygiene (pre-existing, out of commission scope):**
- `concurrency_atomic_unit_test.sh` under `set -euo pipefail` never reaches its `RESULT: FAIL` echo on pytest failure (exits 1 bare) — consumers must read exit codes, not grep RESULT.
- daemon import at collection time appends `kms_issue` lines to git-TRACKED `.agents/shared/planning/designer-agent/install-audit.jsonl` (observed on 2 legs; restored; fires non-deterministically).
- `SEALED_SHA_BASELINES` lives in `test_chart_image_delivery_e2e.py` (audit file references by name); the 7 stale rows predate the branch — the approver/v2 row pair appears swapped in the original map (pre-existing defect, disclosed, untouched).
- Spec-cite drift: factor-2 verify block actually at upgrade_tools.py:3480-3646 (spec ~:3432-3501); `RESTART_TOKENS` is test-side not tool-source. Hash-proven byte-identity subsumes the line drift.

## 5. Scope decision

Full whole-tree suite NOT re-run (17k+ tests; infeasible under the 5-min-per-pack hard cap — would need ~30+ packs for marginal signal). Independent scope = the entire touched surface: 11 branch test files + 3 containing directories (tools/services/job_queue = 8,829 collected) × both legs + sim + concurrency pack + statics. Release Gate deferred to r2 (staged-payload round). Blast radius of a tool-gate + journal + sweep change is fully covered by this scope.

## 6. Promote-readiness statement

**Builder-stage: GO.** Zero branch-caused failures across 8,829×2 collected tests + sim + concurrency; upgrade nonce-gate isolation byte-proven at every seam; seals disciplined; provenance anti-spoof matrix pinned; migration delta empty (rollback_safe basis holds). Conditions: (1) this verdict covers the BUILDER stage only — giter must still merge + stage, and the r2 STAGED-PAYLOAD verification (binary sha, staged tree, boot smoke on the staged release) remains outstanding; (2) NO promote is performed or recommended by this round — the user's 3-factor nonce ceremony owns promote; (3) SF-1/SF-5/SF-7 and the 47 pre-existing reds (now quarantine-ledgered) are developer/house follow-ups, none gating.

## 7. Worker roster & artifacts

| Worker | Instance | Pack | Result |
|---|---|---|---|
| restart-sim | 6bbc8792 | `tests/test_agent_restart_sim.sh` + fence review | PASS 12/12, 22.02s |
| restart-discovery | bc50cf27 | inventory/spec-drift/sizing/venv | report (§header + PACKS) |
| restart-isolation | de8a96f8 | 9 static checks | PASS 9/9 |
| restart-concurrency | 8adbe9f2 | `concurrency_atomic_unit_test` | PASS baseline-exact 99P/0F/74S, 83s |
| restart-targeted-heavy | 05881bc2 | 6 files (370) | 367P/3F (pre-existing) |
| restart-targeted-light | e6131e5b | 5 files (108) | 97P/7F (pre-existing) |
| restart-dirs-tools | d7467035 | tools dir HEAD | 13F captured (135s) |
| restart-dirs-services | c1bb284b | services dir HEAD | 9F captured (93s) |
| restart-dirs-jobqueue | 127234b3 | job_queue dir HEAD | 15F captured (99s) |
| restart-base-bootstrap | 7c27312e | base worktree+venv | READY (3.14.7 matched) |
| restart-base-targeted | 7e2509c6 | 10 files @base | 10F — parity 10/10 |
| restart-base-tools | 1c1abdf1 | tools @base | 13F — parity 13/13 |
| restart-base-services | bfe62532 | services @base | 9F — parity 9/9 |
| restart-base-jobqueue | 4a227058 | job_queue @base | 15F — parity 15/15 |

Logs: `/tmp/restart_head_tools.{log,failed.ids,failing-set.txt}`, `/tmp/pack_head_services.log`, `/tmp/rt-verify/head-job_queue.log`, `/tmp/base_tools_leg.log`, `/tmp/base_jq_run.log`, `/tmp/base_services_run.log` (retained in /tmp; base worktree removed post-verification).
