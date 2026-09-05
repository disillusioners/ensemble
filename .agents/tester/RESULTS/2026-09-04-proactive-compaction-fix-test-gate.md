# TEST GATE — feature/proactive-compaction-fix

**Date:** 2026-09-04 · **Gate leader:** tester (24 workers + 1 revive)
**Branch:** `feature/proactive-compaction-fix` · **Acceptance base:** `71bb09ab` (range `673270ec..71bb09ab` = **8 commits** — the task's "7" counted code commits; 8th is docs-only `e58f88fe`)
**Gate-owned test-infra commits:** `6ba33f90` (FE noop_reason render specs, +41/1 file) · `36d30c45` (original-symptom acceptance test) · **Final HEAD: `36d30c45`**

## FINAL VERDICT: ✅ SHIP — 0 branch-caused regressions

---

## §1 Original-Symptom Closure — ✅ CLOSED (chain-WRITTEN + PASS)

**No existing single test chained all links** (branch splits them: T5 resume-lane uses a spy with no gate/engine; T5GateArbitration runs the gate with a mocked engine and no dispatch; P2ResumeHandleIntegrity has real graph+persist but stubbed engine, no threshold math, no dispatch; guard/multi-reuse use `_MockGraphState`; executor is the /compact lane).

**Acceptance test WRITTEN and PASSING:** `tests/unit/services/test_proactive_compaction_symptom_acceptance.py::TestOriginalSymptomAcceptanceChain::test_resume_dispatch_above_threshold_compacts_and_persists_end_to_end` (+ negative control `test_proactive_flag_off_same_scenario_does_not_compact`). `2 passed in 1.84s`. Commit `36d30c45`.

Per-link evidence (file:line @ 36d30c45):
- **(i) PRE:** `:355` `st_pre.next == ()` quiescent between-turn · `:378` status `waiting_children` ∉ reject-set · `:365` engine's own estimator > 1600-token trigger (window anchored 2000, ~10k real tiktoken tokens ≈ 6× trigger) · `:358` `compacted_at is None`
- **(ii) RESUME DISPATCH:** `:397-410` real `_process_message_with_tracking(..., is_retry=True, message_source="cascade_resume")` — the exact lane the old `if not is_retry` blanket-skip excluded (L2)
- **(iii) FIRE:** `:426` real `ContextCompactor.compact_state` invoked exactly once (counting spy delegating to real engine; flag→status→shape→engine chain all real)
- **(iv) PERSIST:** `:435` reload through FRESH AsyncSqliteSaver connection: `:442` next==() preserved · `:452-456` `compaction-global-*` SystemMessage present · `:466-476` compacted span gone / preserved tail survives / no foreign ids · `:483` `compacted_at` durable · `:490` post-tokens < trigger (symptom relieved) · `:498-516` exactly 2 ordered `aupdate_state` writes, NO `as_node`, REMOVE_ALL_MESSAGES sentinel at element 0
- **Negative control:** `:596` `proactive_enabled=False` → 0 engine calls, 0 writes, still above trigger

Machinery: real `StateGraph` + real file-backed `AsyncSqliteSaver`; ONLY `daemon.graph.ThinkingChatOpenAI` stubbed. Disclosed warning: post-trigger dispatch tail raises a captured mock-surface TypeError (out of chain scope; durable-state proof is self-certifying — compaction artifacts can only exist if dispatch→trigger→engine→seam all ran).

## §2 Acceptance Suite — ✅ ALL EXACT

| Pack | Expected | Actual | Result |
|---|---|---|---|
| Trilogy (p1 42 / p1b 28 / p2 13) | 83 | **83** (42/28/13 exact) | PASS, 2.32s |
| Services quartet (executor 75 / dispatcher 76 / guard 12 / multi-reuse 11) | 174 | **174** (exact per file) | PASS, 8.87s |
| Compaction core (test_compaction 129 [+3 vs 126 prior] + model_config 31) | 160 | **160** | PASS, 9.04s |
| Canonical tripwire | 1 | `TestAuditBaseline::test_terminal_instance_statuses_constant_exists` PASS | PASS (canonical 4-element frozenset intact; sibling COMPACT_REJECT_STATUSES used, not canonical mutation) |
| Pack c2 (`c2_messaging_lifecycle_unit_test.sh`) | — | **62P / 14S** | PASS, RESULT: PASS |

Named items, all green: T4/T4-ext refire (TestT4NumeratorBudgetAntiRefire 5 · TestSharedSeamStampOnlyPath 2 · TestPreCall95MultiCallRefire 3 · TestUserFacingNoopSkipsSeam 3 + real-engine control) · 95% boundaries (5 anchors: 569000/571000/569940/570060/570000 exact-boundary + force_false fire) · estimator pre-filter (6, incl. count-growth re-estimate, sub-80 single estimate) · NoopReason set-equality mapping guard · P2 resume-handle on real graph (2) · window anchors (TestWindowMathFollowsCompactionModel 5 · TestWindowGatedAtSessionWindow 6) · AST gate 11 (p1 T3 4 + p2 GuardRemoval 4 + executor 3 — distributed set, no single file) · resume-lane reach incl. watchover fallback (T5 2).

## §3 Kill-Switch — ✅ 6/6 matrix + CLE armed

| env | yaml | resolved | both auto-gates | status |
|---|---|---|---|---|
| unset | absent | ON | armed | PASS |
| `=0` | absent | OFF | **both disabled** | PASS |
| `=1` | absent | ON | armed | PASS |
| `=""` | absent | ON | armed, **no boot crash** | PASS |
| `=0` | yaml true | **OFF (env wins)** | both disabled | PASS |
| `=1` | yaml false | **ON (env wins)** | armed | PASS |

Mechanism (real `load_config(config_path=…)`): resolver `_resolve_proactive_enabled` config.py:2147-2215 · loader call site config.py:2359 · gates: instance_messaging.py:1221 (proactive) + graph.py:2836 (95% hook, called from agent_node :3875) · **CLE handler graph.py:3900-3994 does NOT consult the flag** (grep-verified zero matches in block) and empirically: CLE-path subset 5/5 PASS with `ENSEMBLE_PROACTIVE_COMPACTION=0` (incl. `test_reactive_compaction_success` on real `create_agent_node`). Tier-2 alias `COMPACTION_PROACTIVE_ENABLED` honored; permissive bool vocab (`0/false/no/off` vs `1/true/yes/on`); invalid values raise clear ValueError at boot; empty-string normalization (config.py:1951, :2194). Artifacts: /tmp/pcfg-killswitch/.

## §4 Mock-Discipline Audit — ✅ CLEAN (0 🔴)

Every load-bearing claim verified REAL against production signatures: W-1/W-2 tests call real `load_config` (8 sites, resolver never mocked) · p2 real LangGraph via `_RealLangGraph` conftest-swap + real file-backed AsyncSqliteSaver + instrumentation-only `_GraphWrapper` · seam kwargs asserted by CONTENT (`mid_turn`/`abort_policy`/`force`) matching real signatures (zero invented kwarg names) · patch sites verified intercept-effective (lazy imports / module-attr) · vacuous-green spot check: status gate, shape polarity, is_retry removal, 95% backstop, NoopReason set-equality, AST gate — none stay green on inversion. **3 🟠 non-blocking:** `p2:390` vacuous `or True` assert · `p1:768-807` hard-coded REPO_ROOT fallback · `next=None` checkpoint edge unpinned (production treats as quiescent; gate proceeds).

## §5 Full Regression — ✅ 0 NEW (12 partitions, 16,410 outcomes)

| P | Pack | Collected | P/F/E/S | NEW |
|---|---|---|---|---|
| 1 | regression_unit_tools | 1,104 exec (+5 quarantine-deselect) | 1,101/2/0/1 | 0 — upgrade_registration ×2 ledger-identical; **TestAccessMemoryArchive ×5 correctly pack-deselected (leader-expected family)** |
| 2 | regression_unit_services | 1,277 (+99 exactly reconciled: 83 new + 14 param + 2 gate-owned) | 1,270/7/0/0 | 0 — proxy_phase1 ×7 ledger-identical |
| 3 | regression_unit_smaller_subdirs_routers | 591 | 590/1/0/0 | **1 flagged → exonerated (Item A)** |
| 4 | regression_unit_loose_a_d | 1,050 | 1,017/10/21/2 | 0 — exact parity (api_module_is_small + misc ×9 + slash/blueprint fixture-drift ×21E) |
| 5 | regression_unit_loose_e_l | 1,116 | 1,105/11/0/0 | 0 — llm ×2 + hide_kb ×5 + job_processor ×4 ledger-identical |
| 6 | regression_unit_loose_m_r | 1,890 | 1,842/8/0/40 | **1 flagged → exonerated (Item B)** |
| 7 | regression_unit_loose_s_z | 1,036 | 971/52/2/11 | 0 — watchover 47-family + 5 singles exact; zero compaction-shaped signatures in family |
| 8 | regression_top_level_a_h | 1,082 | 1,006/19/3/54 | 0 — test_api ×2 + jsonb 1F+3E (same-family fixture) + misc ×16 |
| 9 | regression_top_level_i_q | 2,447 (+4 organic P) | 2,313/61/0/73 | 0 — **18/18 test_progressive_dispatch fresh-SQLite, count+signature exact**; injection ×27; memory_integration ×10; singles ledgered |
| 10 | regression_top_level_r_z_misc | 2,311 (−20 lineage drift, outcomes identical) | 2,259/13/0/34+5xf | 0 — sqlite ×9 + skill_evo ×2 + terminal_orphan + dequeue-flake |
| 11 | regression_job_queue | 1,674 | 1,629/7/0/38 | 0 — settled-rename 7F node-for-node QUARANTINE row 1 |
| 12 | regression_integration_opencode_e2e | 832 outcomes | 806/16/8/2 | 0 — 16F byte-identical ledger; 8E env-blocked (daemon down, expected); **facade-forwarding guard 3/3 GREEN** |

**Attribution (worktree A/B @ 673270ec, solo 3× each side):**
- **Item A** `test_slash_commands_router.py::TestRunningAckOrdering::test_ack_returns_and_waiting_emits_while_pause_blocked` — ack 533ms vs <500ms under 23-worker parallel load. **NOT branch-caused**: solo 3/3 PASS at HEAD (mean 174ms) AND base (175ms); mechanism refuted — the branch's `asyncio.to_thread` (instance_messaging.py:1161) sits in the bg-task body post-ack (command_dispatcher.py:1115 `record_start → create_task → return`). QUARANTINE.md WATCH row added.
- **Item B** `test_plane_sync.py::TestEdgeCaseConcurrentSync::test_service_concurrent_calls_dont_crash` — `assert 1 == 2` threading race under xdist. **Partition-context flake**: 3/3 PASS solo both sides; TestDequeueAtomicClaim-family twin. QUARANTINE.md row added.
- **Item C (leader-expected 18×)** — at base 673270ec worktree: `18 failed, 14 passed`, ALL 18 carry `Migration 20260714_000001 … sqlite3.OperationalError near "CONSTRAINT"` — base attribution sealed.

Totals: 15,909 P / 207 F / 34 E / 255 S (+5 xfail); every F/E ledger-attributed. Δ reconciliations: P2 +99 exact; P9 +4 organic; P10 −20 lineage collection drift with identical outcomes; P12 +1 env-variance.

## §6 FE — ✅ PASS

3 specs added to `chat-interface.component.spec.ts` (plain-logic via component instance — house style): `injections_dominate` → "All messages are injections; nothing to compact" (component :412) · `preserved_within_threshold` → "Preserved groups still fit within the threshold" (:420) · legacy `below_floor` adjacent guard. **Jest 18/18** (15 pre + 3 new), **tsc --noEmit exit 0**. Commit `6ba33f90`. No defects.

## ensure.md Validation

- **Critical:** changed packs all PASS ✅ · concurrency/deadlock integrity — in-partition green (P4 exact parity incl. deadlock/cascade files; zero unledgered failures) ✅ · dev.sh `--timeout-graceful-shutdown 10` present (dev.sh:102, executable line) ✅
- **Important:** awaits discipline — 8/8 call sites awaited across `_get_system_prompt_tokens` / `_compute_context_usage` / `get_queue_stats`; facade seam awaited (manager.py:8433) ✅
- **Nice-to-have:** not validated (informational)
- **Release Gate:** NOT RUN — outside this gate's mandate (leader scoped §1–§6); E2E requires live daemon + real LLM; feature activates only after daemon restart. Recommend post-deploy soak + one manual /compact verification. No contradictions with ensure.md methods; no improvement notices.

## Code Changes (gate-owned, test files only — both committed locally, NO push)

- `frontend/src/app/components/chat-interface/chat-interface.component.spec.ts` (+41) — commit `6ba33f90`
- `tests/unit/services/test_proactive_compaction_symptom_acceptance.py` (NEW) — commit `36d30c45`
- No production code modified by the gate.

## Follow-ups (non-blocking)

1. Audit 🟠 trio: delete `or True` (p2:390); portable REPO_ROOT (p1:768-807); pin `next=None` quiescent edge.
2. Daemon restart required to activate the fix in prod (flag default ON).
3. Quarantine count +2 (1 context-flake family row + 1 WATCH row) — rising flake-family count is a quality signal.
4. §1 acceptance-test post-trigger mock-surface TypeError — cosmetic, out of chain scope; could be silenced in a follow-up test-infra commit.

Full worker logs: /tmp/pcfg-p{1..12}.log, /tmp/pcfg-killswitch/, /tmp/pcfg-base/ (base worktree).
