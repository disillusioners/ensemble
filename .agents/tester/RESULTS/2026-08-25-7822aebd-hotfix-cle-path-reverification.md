# Independent Re-Verification — Hotfix 7822aebd (CLE retry-path pairing guard)

Date: 2026-08-25 (UTC) | Mode: independent confirmation, VALIDATION-ONLY (no fixes, no new tests — quick-deploy)
Branch: `fix/tool-pairing-cle-path` @ `7822aebde32d965cd6bd366cce064930f9303649` (base `75f2487f`, stacked on baseline `84fd8018`)
Commit: "fix(graph): run tool-pairing guard on reactive-compaction retry path" — daemon/graph.py ONLY, +17/−0
Workers: b9b930cd (gate + coverage), a508fe98 (pairing), 99e45b38 (inj-graph), 8d91c195 (graph_retry), 299f76ad (compaction), 476ef7cd (nudge), a3d84730 (context), a75f9061 (bundle). 8 dispatches, 0 direct executions.

## Verdict

**✅ PASS — ZERO DELTA vs 84fd8018 baseline. Hotfix independently confirmed: no regression.**

## State Gate (worker b9b930cd) — 7/7 PASS

| Check | Result |
|---|---|
| Branch | `fix/tool-pairing-cle-path` ✅ |
| HEAD | `7822aebde32d965cd6bd366cce064930f9303649` ✅ |
| Tree | clean ✅ |
| Stat | 1 file: daemon/graph.py +17, 0 deletions ✅ |
| Diff scope | single hunk `@@ -3254,6 +3254,23 @@` in create_agent_node: comment + `pairing_synthesized_msgs.extend(_ensure_tool_result_pairing(compact_messages, instance_short))` — reuses 84fd8018 helper, nothing else ✅ |
| Lineage | 84fd8018..HEAD = [75f2487f (docs-only: my validation artifacts), 7822aebd]; ancestor_exit=0 ✅ |
| py_compile | exit 0 ✅ |

Guard position (verified by reading graph.py ~3229-3338): CLE raised → compact → aupdate_state → re-read (`aget_state`) → **guard call** → C3 re-appends (injections/report/ephemeral) → retry invoke. Synthesized placeholders accumulate into `pairing_synthesized_msgs` (C2 persistence).

## Pack Results — baseline-exact across the board

| Pack | @84fd8018 baseline | @7822aebd | Delta | Runtime |
|---|---|---|---|---|
| injection_tool_pairing (new suite) | 16/16 | **16/16** | 0 | 0.47s |
| injection_graph | 11/11 | **11/11** | 0 | 0.43s |
| graph_retry | 19/19 | **19/19** | 0 | 0.86s |
| compaction (CLE-path pack) | 207/207 | **207/207** | 0 | 1.32s |
| nudge | 40/40 | **40/40** | 0 | 0.69s |
| context_graph | 20/20 | **20/20** | 0 | 1.05s |
| injection bundle (6 files) | 93/97 (4 pre-existing) | **93/97 (4 pre-existing)** | 0 | 2.52s |
| **TOTAL** | **410: 406P/4F** | **410: 406P/4F** | **ZERO** | |

The 4 pre-existing `_ManagerStub` failures (test_injection_slot.py:262/:284/:297, test_injection_cleanup.py:143 — manager.py:3488 `_deferred_watchover_terminate` AttributeError) match the baseline by test ID, line number, AND exception signature. No fifth failure. QUARANTINE.md rows remain valid; 2-line stub fix still not applied (validation-only).

## CRITICAL CHECK — CLE retry-path coverage (leader task #4)

**`CLE retry path with poisoned tail: NOT EXERCISED by any existing test — confirmed coverage gap.**

Evidence (worker b9b930cd, full per-test table in session log):
- Grep `ContextLengthExceeded` across tests/ → 4 files; per-test verdicts all DOES-NOT-EXERCISE:
  - test_llm_error_classifier.py — classifier units, never touch agent_node
  - test_graph_retry_integration.py — drives the handler but histories are plain Human/AI (`[HumanMessage("Hello"), AIMessage("Hi there!")]`); no `tool_calls`
  - test_context_injection_integration.py — rebuild-layout/classifier units, no agent_node
  - test_injection_graph.py (`test_injection_re_appended_after_reactive_compaction` + multi-entry) — closest: genuinely drives create_agent_node CLE retry, but `_StubGraph(state_messages=[HumanMessage("history")])`: plain, no `AIMessage(tool_calls)`
- Decisive cross-check: the ONLY file in tests/ containing both `ContextLengthExceeded` and `tool_calls` is test_llm_error_classifier.py, and its sole `tool_calls` occurrence is `mock_response.tool_calls = None` (:284) — a mock attribute, not a message history.
- Per quick-deploy constraint: coverage-gap statement delivered, NO test invented.

Interpretation: the +17 guard line is verified by (a) helper-level suite 16/16, (b) all CLE-path regression drivers green, (c) py_compile + single-hunk scope proof — but the poisoned-tail-at-CLE-retry scenario itself has no direct test. First candidate for the next non-quick-deploy test pass.

## Scope Decision

> Identical pack set to the 84fd8018 pre-deploy run (7 packs), per leader instruction — no expansion. Release Gate not triggered (graph.py message-shape only; no job/task/queue touch). concurrency/dev.sh checks scoped out (unchanged from baseline reasoning; diff vs baseline is the single guard call).

## Overall Status

- 7/7 packs baseline-exact · zero new failures · zero count drift · signatures stable
- **Testing Complete: ✅ READY — hotfix 7822aebd independently confirmed for quick deploy**
- Follow-ups (non-blocking): poisoned-tail CLE-retry test (known gap); _ManagerStub 2-line fix + un-quarantine; PACKS.md stale-baseline re-registration pass
