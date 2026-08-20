# Test Report: x-proxy-interleaved-thinking header addition (all LLM request sites)
Date: 2026-08-20
Branch: `feature/llm-interleaved-thinking-header` @ `104d62cd` (worktree verified clean at start)
Instance IDs: 89ec8fbb (gate) · c633f78b, 24d4afc0, 208b881c, c341b35b, bea4ec7e, 581d5588, d19393bd, a027e7ae, 1ab7c7e1 (pack wave)

## Verdict: PASS ✅ (SHIP)

### Summary
- Total: 483 tests | Passed: 483 | Failed: 0 | Errors: 0 | Timeouts: 0
- Packs: 9/9 PASS + 1 read-only gate (0 discrepancies)
- Quick Fixes Applied: 0 (none needed)
- Quarantined: 0 skipped
- ensure.md: Core in-scope 4/4 PASS (see below); Release Gate NOT triggered (no job/task/queue files touched)

### Scope Decision
> Change requested as "focused unit pass"; blast radius = 6 `default_headers` dict literals in LLM config construction (graph, compaction, title_generation, keyword_extraction, child_reports ×2) + 1 test file. Ran 9 scoped packs (483 tests, all < 5 min, 8 of 9 < 2 min) + static gate. Full suite (~209 unit packs) NOT warranted. Skipped: remaining ~200 unrelated packs + E2E Release Gate (change touches no job/task/queue/lifecycle file — the critical-note trigger for full e2e does not apply).

### Static verification (gate worker, read-only)
| Check | Result | Evidence |
|---|---|---|
| Worktree identity | ✅ | HEAD `104d62cd8545` on `feature/llm-interleaved-thinking-header`, `git status` clean |
| Completeness: 6 value-bearing sites | ✅ 6/6 | `daemon/graph.py:5204`, `daemon/compaction.py:595`, `daemon/services/title_generation.py:98`, `daemon/services/keyword_extraction.py:371`, `daemon/services/child_reports.py:669`, `daemon/services/child_reports.py:1294`. Raw grep count 7 = 6 sites + 1 comment at `graph.py:5199`; every `x-proxy-app` site pairs with the new header — no unpaired site |
| Value exactness | ✅ | All 6 use string `"True"`; 0 boolean-True, 0 casing variants |
| `clean_llm_config` passthrough | ✅ static | `graph.py:2085-2114`: shallow-copy comprehension strips ONLY `model_vision`/`base_url_backup`; `default_headers` retained. `daemon/services/llm_failover.py` contains zero header processing (grep no hits); reuses same helper at line 633 |
| Focus tests present | ✅ | `TestProxyHeaderInjection.test_proxy_header_injected` (asserts both headers, lines 647-668) + `TestProxyHeaderInjectionOtherSites.test_context_compactor_includes_both_proxy_headers` (lines 694-695) |

### Unit test results (9 packs, parallel wave)
| Pack | Result | Counts | Runtime | Notes |
|---|---|---|---|---|
| graph_retry_unit_test | ✅ PASS | 19/19 | 0.73s | Both proxy-header classes collected & passing (verified via --collect-only); baseline was 18 → +1 = new OtherSites test from this commit |
| llm_failover_unit_test | ✅ PASS | 64/64 | 11s | Baseline 64/64 exact |
| llm_failover_adversarial_unit_test | ✅ PASS | 36/36 | 1.54s | Baseline 36/36 exact; zero-behavior-change gate green |
| llm_failover_v2_unit_test | ✅ PASS | 45/45 | 87.2s | Baseline 45/45 exact; header passthrough + shared-config non-mutation hold with extended dict |
| llm_failover_v2_adversarial_unit_test | ✅ PASS | 48/48 | 1.14s | Zero-drift suite covering ALL 9 secondary sites — strongest signal |
| llm_failover_v2_resilience_unit_test | ✅ PASS | 20/20 | 1.16s | All 5 AST structural pins on the modified secondary-site functions held with the two added header lines |
| compaction_unit_test | ✅ PASS | 207/207 | 1.11s | Baseline 206 → +1 = the new OtherSites compactor-header test; compaction.py:594-595 confirmed stamped |
| child_reports_unit_test | ✅ PASS | 15/15 | 1.20s | Baseline 12 → +3 = upstream test additions; both sites (669, 1294) green, no strict-equality trips |
| title_generation_trigger_test | ✅ PASS | 29/29 | 1.15s | **Beat stale baseline 21P/8F** — the 8 pre-existing `_maybe_store_initiative_message` failures were fixed by ancestor commit `8c71b862` (relaxed call-count assertions); 0 new failures |

All warnings across packs (2-3× `PytestConfigWarning: Unknown config option: timeout/timeout_method` per pack) are pre-existing environmental noise (pytest-timeout plugin not registered in this venv), present at baseline, unrelated to the change.

### Verification objectives vs evidence
1. **Completeness (6 sites)** — PASS: gate grep 6/6 paired sites, 0 unpaired `x-proxy-app`.
2. **Header passthrough intact** — PASS: `clean_llm_config` statics + 4 failover packs (213 tests) green with extended header dict, including `x-proxy-app` passthrough and shared-config non-mutation assertions.
3. **Test suites** — PASS: all 9 scoped packs green, zero new failures vs baselines (three packs BEAT stale baselines; deltas all attributable: +1 new test from this commit ×2, +3 upstream tests, 8 stale-baseline failures fixed by ancestor `8c71b862`).
4. **Value exactness** — PASS: string `"True"` at all 6 sites (gate exact-line evidence) + asserted by `TestProxyHeaderInjection` (line 668) and `TestProxyHeaderInjectionOtherSites` (line 695).

### ensure.md Validation (blast-radius scoped)
- **Critical**:
  - No regressions in changed packs: ✅ PASS (9/9 scoped packs green)
  - Deadlock/concurrency integrity (`concurrency_atomic_unit_test`): N/A-scope — no concurrency code touched (6 dict literals); pack not in change-set blast radius
  - No sync DB calls on asyncio loop: N/A-scope — no DB code touched
  - `dev.sh` includes `--timeout-graceful-shutdown 10`: ✅ PASS (static grep, gate worker, dev.sh:102)
- **Important/Nice-to-have**: N/A-scope (await-caller list and dead-code checks target async-conversion/deletion changes; none here)
- **Release Gate**: NOT triggered — change is LLM-config construction only, no job/task/queue/lifecycle touchpoints (matches the e2e convention carve-out noted in the task)
- Contradictions: none — all ensure.md methods compatible with pack/timeout rules

### Gaps
None. All 10 dispatched nodes completed; no re-dispatches needed.

### Action Needed
None for this change. Observation (non-blocking, 🟢): `PACKS.md` baseline notes for `title_generation_trigger_test` (21P/8F) and `child_reports_unit_test` (12/12) were stale — refreshed this run (29/29, 15/15). Also the `llm_failover_v2_resilience_unit_test.sh` header comment says its target file "does not exist yet" — it does; cosmetic script-comment drift only.

### Documentation Updated
- [x] PACKS.md — last-run/status refreshed for 9 packs + new summary line
- [x] RESULTS/2026-08-20-llm-interleaved-thinking-header.md — this report
- [x] rules/ensure.md — no changes (user-maintained, read-only)
- [x] MOCK_TESTS.md / QUARANTINE.md / COVERAGE.md — no changes needed (no mock tests, no quarantine, no coverage-structure change)

### Code Changes Summary
None — 0 code modifications during this session (no quick fixes required). Nothing to commit.
