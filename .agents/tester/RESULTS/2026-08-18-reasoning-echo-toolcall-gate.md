# Test Report: feature/reasoning-echo-toolcall-gate
Date: 2026-08-18T04:20+00:00
Branch: `feature/reasoning-echo-toolcall-gate` @ `9deb9121` (+ test-only fix `c80e0232`), parent `e81941ac`
Instance IDs: recon f67117b4 · direct 3dc76b7c · sweep 11491dcc · gate-verify cb77cc84 · ensure 3a54916a

### Summary
- Total executed: 708 tests (24 direct + 612 sweep + 29 concurrency-pack re-verified tests + probe scenarios) | Failed: 0 final | Quick fixes: 1 (test-only, `c80e0232`)
- Verdict: **SHIP**
- Change under test: `daemon/graph.py` `ThinkingChatOpenAI._get_request_payload()` — `reasoning_content` echo now gated on `bool(tool_calls or tool_call_chunks)` in addition to model-match + reasoning-presence (DeepSeek thinking-mode spec: echo only valid/required in tool-call rounds).

### Scope Decision
> Change touches 1 production file (`daemon/graph.py`, one echo-gate hunk in the LLM request payload path) + 2 test files. Scoped run: dedicated echo suites + graph/LLM-adjacent unit sweep + independent gate probe + ensure.md Core (concurrency pack). Full e2e Release Gate NOT triggered — no job/task/queue files (checked against the ensure.md critical-note list: claim_pending_task, turn_transitions, reconcile_turn_mirror, job_processor, job_locks — none touched). Full suite not warranted.

### What Was Run (5 dispatches)
| Pack | Worker | Result |
|------|--------|--------|
| Recon (branch/diff/test-listing/invocation-mechanism/dev.sh grep) | f67117b4 | Branch clean @ 9deb9121; gate hunk confirmed; TestReasoningEchoToolCallGate invokes REAL `_get_request_payload()` (no mock-bypass); dev.sh `--timeout-graceful-shutdown 10` present (line 102) |
| reasoning_echo_direct_unit_test (roundtrip 16 + echo_config 8, two independent invocations) | 3dc76b7c | ✅ PASS 16/16 + 8/8 (0.71s / 0.45s) |
| reasoning_echo_sweep_unit_test (`tests/unit -k "graph or llm or reasoning or thinking"`, 612 collected) | 11491dcc | ✅ PASS 612/612 (102.55s) after fix; first run 603P/**9F** (stale-contract tests) |
| reasoning_echo_gate_verify_test (real-class probe, HEAD + pre-fix worktree discrimination) | cb77cc84 | ✅ PASS 5/5 HEAD; pre-fix e81941ac reproduces exact symptom (3/5) → probe discriminates |
| concurrency_atomic_unit_test (ensure.md Core Critical, canonical 13 files) | 3a54916a | ✅ PASS 91P/74S/0F in 6.90s — baseline-exact vs df40ba3a + 2fca56ae |

### Behavioral Verification of the Original Symptom (3 independent signals)
1. **Dedicated suites**: `TestReasoningEchoToolCallGate` (8 tests) all PASS via the real code path (real `ThinkingChatOpenAI(model=...)`, real `_get_request_payload()`, payload-dict inspection; recon verified zero mock/patch of the method). Plus original roundtrip class (8) + echo_config (8).
2. **Independent probe** (throwaway, deleted): 5/5 at HEAD — final-answer turn → `reasoning_content` ABSENT from payload; tool-call round → echoed; mixed history → only tool-call assistant echoed; chunk variant echoes. **Pre-fix discrimination**: same probe at `e81941ac` (git worktree) fails exactly the symptom scenarios A + C2 (echo present on final-answer turns) — proves the probe and the fix.
3. **Sibling suites**: 603/612 zero product failures across failover v1/v2, classifier, graph-retry, adversarial, resilience, config_override, watchover, nudge — no collateral blast radius.

### Failure Found & Fixed (stale tests, not product)
First sweep run: 603 passed / **9 failed** — all in the two sibling reasoning files the developer did not update (last touched `768cbae7`, pre-gate era):
- `test_reasoning_content_fallback.py` ×6 (TestReasoningEchoGating: default/case-insensitive/custom-list/empty-string/non-assistant/with_tool_calls) — fixtures had no tool_calls but asserted echo.
- `test_reasoning_content_edge_cases.py` ×3 — plain conversational turns asserted echo.

All 9 encoded the OLD contract ("echo every DeepSeek assistant turn"). Disposition via quick fix `c80e0232`:
- 5 positive tests: **tool_calls added to fixtures** — positive echo coverage preserved under the gate (incl. empty-string echo).
- `test_echo_with_tool_calls`: final-answer leg flipped to assert NO echo (tool-call leg unchanged).
- 3 edge-case tests: **updated-to-negative** with honest docstrings (plain conversational scenarios — inventing tool calls would misrepresent them; positive coverage lives in fallback.py fixtures).
- Net delta +36/−16 across 2 files; required gate-reference comment at every changed site; no deletions/renames/trivial weakening.

### ensure.md Validation Results (Core, blast-radius scoped)
- **Critical**: 4/4 PASS
  - ✅ No regressions in changed packs (direct 24/24, sweep 612/612 post-fix, probe PASS)
  - ✅ Deadlock/concurrency integrity — concurrency_atomic 91P/74S/0F baseline-exact
  - ✅ No sync DB calls on event loop — same pack PASS (thread-identity tests)
  - ✅ dev.sh `--timeout-graceful-shutdown 10` — static grep PASS
- Release Gate: NOT triggered (no job/task/queue change; small isolated change).
- Quarantined: 1 test (pre-existing SQLite migration issue, not in any run pack) — no impact.

### Warnings (benign, pre-existing)
- `PytestConfigWarning: Unknown config option: timeout/timeout_method` — pytest-timeout not installed in this venv (env gap, predates branch).
- sqlite3 datetime adapter DeprecationWarnings (Py3.13/SQLA 2.x).
- Note: pytest-timeout 2.4.0 was reported registered during the 2026-08-15 LLM-HA polish campaign — this venv appears to have lost/never had it; flag for devops, not this branch.

### Code Changes Summary (all committed)
- `tests/unit/test_reasoning_content_fallback.py`, `tests/unit/test_reasoning_content_edge_cases.py` — stale-contract alignment (test-only)
- Commit: `c80e0232` "test: align reasoning-echo fallback/edge-case suites to tool-call gate contract (3949b8a7)"
- Production `daemon/graph.py`: NOT modified by testing (verified clean).

### Process Note
Sweep worker hit a mid-fix hazard worth recording: batch string-replace matched an ambiguous 8-space fixture block shared by three negative model-gate tests, arming them with tool calls and missing the real targets (12-space indentation inside `try:`). Recovered via `git checkout --` + position-anchored unique-context re-apply. → LESSONS/2026-08-18-reasoning-echo-test-contract-drift.md.

### Overall Status
- Direct suites: ✅ PASS (16/16 + 8/8)
- Regression sweep: ✅ PASS (612/612 after c80e0232; 9 stale tests aligned)
- Behavioral gate probe: ✅ PASS (5/5 + pre-fix discrimination)
- ensure.md Core: ✅ PASS (4/4 Critical)
- **Testing Complete: ✅ READY — Verdict SHIP**
