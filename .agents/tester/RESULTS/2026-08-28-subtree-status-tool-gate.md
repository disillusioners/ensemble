# Independent Verification Gate — #5 subtree_status Tool (`feature/subtree-status-tool`)

- **Branch**: `feature/subtree-status-tool` @ `1d14f451` (range `31981df5..1d14f451`, 3 commits: 9b566b43 tool → 7561f910 orphan-guard fix → 1d14f451 review polish; 9 files +1806/−4 — "~+1850" approx confirmed). Base 31981df5 verified to contain the injection-marker branch (83a1a8b7).
- **Date**: 2026-08-28
- **Dispatches**: 24 workers (3 recon incl. falsification + 8 committed packs + 7 sweeps + 1 new-tests bundle + 2 probes + 1 red-proof + 1 base A/B + 1 un-quarantine), 0 direct executions.
- **Rider commit (gate-authorized, disclosed)**: `d663ec9a` — job_continue ×4 un-quarantine (pack deselects removed; 80c/80P/0F/0-des stable ×2). Branch tip at gate close: `d663ec9a`.

## VERDICT: ✅ PASS — subtree_status gate CLOSED; #3+#5 BATCH CLEARED FOR MERGE to `latest`
**Zero new regressions attributable to this branch** (8/8 packs baseline-exact; 6/7 sweeps baseline-exact; subdirs sweep's httpx-error cluster proven PRE-EXISTING at base via worktree A/B). All 6 verification scopes closed.

---

## 1. Scope mapping (leader's 6 items)

| # | Scope item | Result | Evidence |
|---|---|---|---|
| 1 | Full regression vs baseline | ✅ 0 NEW | §3 packs all exact; §4 sweeps 6/7 exact + subdirs pre-existing (§5) |
| 2 | Orphan-guard on real rows | ✅ 27/27 | probe: DONE+pending→0, DEAD→0, active/queued→counted, no-JobItem→counted (JAFP), soft-deleted-mirror→COUNTED (guard ignores deleted_at-set mirrors), cross-instance asymmetrics (work-id pairing wins, JobItem.instance_id ignored), window closes INSTANTLY at query time (2.3ms, no reconciler), facade pass-through + degraded `{}` path. **Drift tests non-vacuous**: red/green pair on identical tests — pre-fix production 3 RED (verbatim assert diffs in /tmp/subtree_status_drift_red_pre_fix.log), 1 deliberate safety-pin green both sides, post-fix 7/7 |
| 3 | Tool behavior end-to-end (real fn) | ✅ 59/59 | probe: true-size header, caller-first (caller sorts LAST alphabetically — proven), iid[:8]+agent≤24(23+…) rows, statuses/ages/pending match seeds, cap 50 default + clamp 200 (250-tree → exactly 200; default → 50; notice names TRUE size 250/4), max≤0 → ERROR text, drill-down subtree-scoped + caller-first within it, cross-subtree refusal verbatim (no partial output), outside-instance absent, get_instance_info bad-row skip, registration seam pinned by shipped test (real AgentRegistry discover + get_version/get_resolved) + registry pack zero unknown-tool warnings |
| 4 | Token safety | ✅ | NO message content: get_messages NEVER called across all invocations; planted SECRET string never surfaced; 250-tree@cap200 = 12,402 chars < 16k ceiling; every row ≤59 chars; ceiling math (200×69+overhead=14,240) unreachable with normal input — defense-in-depth only |
| 5 | Mock fidelity (new tests) | ✅ CLEAN | 17/17 patch targets exist at HEAD; mock shapes match production (sync/async, dict[str,int]); drift tests use REAL SQL via real SQLModelSession (zero mocks); no bypass-seeds; StaticPool antipattern present in the pre-existing conftest (low-risk single-threaded; flagged, not disqualifying) |
| 6 | Read-model soundness ("cancel is the only orphan path") | ⚠️ FALSIFIED as stated — **guard remains SOUND** | 13 writer paths enumerated; 5 non-cancel creators: F1 poll-reconciliation (30s cadence), F2 restart `_fail_orphaned_job` (no F5-style cancel), F3/F4 dispatch/wake-failure completes, F7 observer ACTIVE-mirror finalize (C1 gate protects QUEUED branch only), F5/F6/W4/W12 transient/historical. ALL land in the same transient state class healed by drift reconciler Pattern (d) ≤~10min; pending Tasks non-actionable (terminal/failed binding) except marginal F7 revive edge. No permanent false negative. 2 doc corrections + 1 latent DEAD heal-gap routed |

## 2. Statics (P0) — all confirmed
Guard SQL verbatim: correlated NOT EXISTS (`job_id == work_id`, admission_state ∈ {DONE,DEAD}, deleted_at IS NULL), pending-only predicate, `.in_()` parameterized (no injection), SQLAlchemy-abstracted (dialect-safe), empty-input short-circuit, one batched query (no N+1). Contracts (a)-(g) all cited: cap 50/clamp 200/true-size notice (:970,~3554,~3597), filter lowercased EXACT == (:3448-3456), caller-first `(x != caller, x)` (:3410), `_validate_subtree_target` single chokepoint (:3399, def :860), 16k ceiling (:976,~3600), no-message-read (grep-zero in tool body), row format (:~3594). Registration: opt-in exactly leader+planner+tester (3 meta.json, single-entry in-place; zero other agents), KNOWN_TOOL_NAMES (:664), production seam get_version/get_resolved (manager.py:1187-1188). Out-of-scope scan: none. ensure.md statics: dev.sh:102 ✓; async call sites untouched ✓.

## 3. Committed packs — 8/8 baseline-exact

| Pack | Baseline | This gate | Δ |
|---|---|---|---|
| tools_suite | 993c/988P/0F/5-des | 1023P/0F/5-des (27.3s) | +30c/+35P = new subtree_status tests, 0F |
| api_unit | 213P/8S | 213P/8S | exact |
| concurrency_atomic (ensure.md Critical) | 98P/74S | 98P/74S | exact |
| instance_messaging_regression | 28/28 | 28/28 | exact |
| instance_messaging_queue_routing | 16/16 | 16/16 | exact |
| job_queue_tools | 80c/76P/0F/4-des | **80c/80P/0F/0-des** | job_continue ×4 re-enabled (un-quarantine d663ec9a) |
| registry_validation | 140/140 | 140/140, ZERO unknown-tool warnings | exact |
| child_reports | 15/15 | 15/15 | exact |

New-tests bundle (2 files, ONE process): **253/253 PASS** (count claims reconciled: "42" = 35+7 new tests; "253" = parametrized collection; instance_tools collects 180 items). No cross-file pollution.

## 4. Full-tree sweeps ×7
unit-ah 15F+4E exact · unit-ir 44F exact (loop_repairer clean) · unit-sz 50F+2E exact (watchover 47 root-caused to clean_llm_config default_streaming ClassVar) · top-ah 3F exact · top-ir 90F exact (17 buffer + 67 migration + 6 family; job_create confirmed fixed in base; 3 spawn retry-dupes explained) · top-sz 12F exact, **spawn_team_members 44/44 holds** · subdirs → §5.

## 5. Subdirs sweep — httpx cluster attributed PRE-EXISTING (base worktree A/B)
Branch run: 48F + 108E (107 httpx `TypeError: object.__new__()`). Base 31981df5 full-partition run (same 328 files, same invocation): **opencode/test_client ×43 setup-errors + ×5 body failures EXACT; vscode_routing ×8 EXACT; vscode_security ×8 same signature; atomic_status ×1 pre-existing** — 51/107 branch httpx-errors exactly reproduced at base, remainder = order-variance within the same pre-existing class (api-file subset; base run shifted some to body-failures — base 67F/79E vs branch 48F/108E is churn inside the polluted family, no net new). Branch touches NONE of these files; every affected file passes in isolation. **My prior-gate note "httpx ABSENT post-83a1a8b7" was an extrapolation error** (7-file subset clean ≠ full sweep clean) — corrected in QUARANTINE + LESSONS. Polluter hunt routed (pair-bisection per the isolation-triage skill). Not a branch regression; not a blocker.

## 6. Gate actions
1. **Un-quarantine EXECUTED**: job_continue ×4 (4 consecutive green runs post has_instance_busy fix) — pack deselects removed (commit `d663ec9a`), 80/80 stable ×2, QUARANTINE.md rows → Resolved.
2. Red-on-pre-fix gap CLOSED with artifact (was narrative-only): /tmp/subtree_status_drift_red_pre_fix.log.

## 7. ensure.md
Core Critical 4/4, Important 2/2 (statics + concurrency pack + scoped packs). Release Gate: full non-integration coverage via packs+sweeps, all failures attributed; E2E daemon/LLM items NOT TRIGGERED (read-only tool + repo query; no job/task/queue semantics changed). No contradictions.

## 8. Follow-ups (routed, non-blocking)
1. [Dev] Filter discriminator tests (Gap A/B): seed a `waiting`-vs-`waiting_children` pair + `RunNing` case (my probe pins the behavior; tests should too — ~4 lines).
2. [Dev/docs] Guard docstring "60s loop" → actual 300s interval (window ≤~10min); Pattern (d) comment claiming dead_letter coverage is wrong (DONE-only match — latent DEAD heal gap; cleanup bucket 4/startup reconcile cover it).
3. [Dev/backlog] F7 observer ACTIVE-mirror finalize lacks the C1 pending-gate (QUEUED branch only) — marginal revive-claim edge; W-poll (job_processor 30s) + Pattern (d) heal it.
4. [Infra] httpx shared-process polluter hunt in full-subdirs sweeps (pre-existing; pair-bisection from opencode/test_client backward).
5. [Hygiene] Migrate new repo-test conftest off StaticPool (convention).
6. [Dev] deferred follow-ups already parked by review (pending→queued rename, sync-cancel fix, composite index, ceiling/at-cap tests).

## 9. Worker instances
bdcfcfc5 (diff) · 5146768d (test audit) · ed68b50c (falsify) · 58b9f117 tools · 25319e97 api · a69c5a38 conc · 3c4b07c0 msg-reg · ed368df2 msg-route · ebf7a134 jqt · 3b207035 registry · 44eec015 child-reports · 23405788 newtests · 5952a6c3/d6eb9107/668e5d02/7d859508/e99f36bf/d69d7a35/bb8a29bb sweeps ×7 · 0083799d probe-orphan · cbca5235 probe-tool · 83364a56 red-proof · 4da5293b base-httpx A/B · 3551a351 un-quarantine.
