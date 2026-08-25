# Test Report: Streaming Activation Fix for Cloudflare 125s Kill (CF 524)

**Date:** 2026-08-25
**Branch:** `fix/llm-streaming-cf-125s` — gated at **c70092ad** (dd43a7f1 dev fix → 23c031af activation pack → 5bfdaebd wire-verify suite → c70092ad W-1 fix)
**Worker instances:** pack-stream-new dfb89662, pack-failover a6edfffe, pack-failover-v2-adv b34e97d0, pack-failover-v2 781f4b87, pack-reasoning-trio 0cccf47b, pack-echo-targeted d4412d4f, pack-config-override 8b44aa36, pack-graph-retry f71ff62c, recon-fidelity e8f55fa4, live-probe a61d448a, wire-verify-sse e976a237, worktree-audit b9f53eea, head-reverify 229d15b6

## Summary

- **Packs run:** 10 (8 regression + 2 streaming-specific, one tester-authored) — **all PASS**
- **Blocker found & fixed:** W-1 missing `ChatGenerationChunk` import — every streamed response crashed; fixed in c70092ad, verified 6-red → 16/16 green
- **Live evidence:** CF-proxied backend streams first byte at **0.229s** with 22 incremental SSE chunks (non-streaming: zero bytes until completion — the exact original-symptom mechanism)
- **ensure.md Core:** 3/3 critical in scope — PASS
- **Quarantined:** 0 new (existing quarantine untouched)

## Scope Decision

> Change touches 9 files on the LLM payload path only (graph.py, config.py, __main__.py, api.py, config.yaml + 4 test files); no job/task/queue system files → E2E Release Gate NOT triggered (per critical-note convention). Ran: 10 LLM-path packs + wire-level supplemental suite + live probe. Skipped: full suite (~24 packs), E2E gate. Full suite not warranted: zero delta in job/task/queue per diff recon.

## Per-Suite Results

| Suite | Result | Baseline | Notes |
|---|---|---|---|
| test_llm_streaming_activation.py (NEW, 15) + test_graph.py (2) | **17/17 PASS** (1.34s @ c70092ad isolated) | dev-claimed | clean invocation, not dev's cached run |
| test_llm_streaming_wire_verify.py (NEW, tester-authored, 16) | **16/16 PASS** post-fix (1.70s isolated) | n/a | was **6/16 FAIL** by design pre-fix (W-1) |
| test_llm_failover.py (64, branch-modified) | **64/64 PASS** (10.0s) | 64 @ 104d62cd | streaming=False opt-outs sound |
| test_llm_failover_v2_adversarial.py (48, branch-modified) | **48/48 PASS** (0.96s) | 48 | count unshifted |
| test_llm_failover_v2.py (45) | **45/45 PASS** (90.8s) | 45 @ 87.2s | +3.6s = env noise; clean_llm_config semantics held |
| reasoning trio (43) | **43/43 PASS** (0.73s) | 43 baseline-exact | |
| echo targeted (51) | **51/51 PASS** (0.81s) | 51 | no payload-assertion coupling broke |
| llm_config_override (31) | **31/31 PASS** (0.94s) | 31 | LLMConfig.streaming compatible |
| graph_retry (19) | **19/19 PASS** (0.70s) | 19 | agent_node wiring undisturbed |

## Mock Fidelity Verdict (test-plan item 3)

**FAKE-SHAPED at the streaming-decode level.** All 15 dev tests classify CONFIG/payload-dict tier: 10 pure config transforms, 3 `_get_request_payload(...)` dict assertions (never a serialized POST body), 1 MagicMock delegation. No SSE-shaped mock, no transport, no `.invoke()` round trip — the suite's own docstring concedes it tests the build step, not the wire step. The failover `streaming=False` opt-outs themselves are correctly scoped (fixture/helper-level, no conftest hack — verified).

**Consequence materialized:** the config-level suite was structurally blind to W-1 (below). Tester-authored tier-4 suite (real client + httpx.MockTransport + real SSE frames) now guards it permanently.

## W-1: Blocker Found → Quick-Fixed → Verified (c70092ad)

`daemon/graph.py` used `ChatGenerationChunk` at :2041/:2079 without importing it → **every streamed response raised NameError after the request left**. Latent since 37f39c8b (decode path never entered until streaming=True default). **Dev's 15/15-green suite missed it; wire-verify suite caught it on first run.** Fix: 1-line import (+comment). Verified: wire pack 16/16, activation 17/17, class-var hygiene OK.

## Wire-Flag Verification (test-plan items 4-6)

- **(a) plain call:** captured POST body `"stream": true` ✅
- **(b) reasoning model (glm-5.3 fixture):** `"stream": true` ✅
- **(c) tool-calling (bind_tools):** `"stream": true` + tools schema on wire ✅
- **Opt-out:** `OPENAI_STREAMING=false` → class var False via **full main() body** (path 1) AND **real lifespan entry** (path 2) → chokepoint False → payload `stream: false` ✅
- **Edge cases:** streaming absent → injected ✅; explicit True survives ✅; explicit False NOT clobbered (wire `stream: false`) ✅; explicit True beats class-var False ✅

## Semantic Equivalence (test-plan item 7)

**FULLY EQUIVALENT** (streaming vs non-streaming, same logical completion): content ✅, tool_calls (3-fragment args → exact `{'city':'Hanoi','unit':'c'}`) ✅, usage_metadata ✅ (preserved from usage-bearing final chunk), reasoning_content captured in additional_kwargs ✅.

## Live >125s Test (test-plan item 8)

- **llm.ensem.dev (CF-proxied, the actual 524 path): DONE** — stream:true TTFB **0.229s**, 22 incremental SSE chunks (no proxy buffering), clean `[DONE]`; comparison stream:false TTFB 1.582s = zero bytes until completion. Byte-flow precondition for surviving CF 125s **validated on the production path**.
- **localhost:4123: SKIPPED** — connection refused, backend offline. Honest skip.
- Not attempted: an actual >125s generation (no need — TTFB + incremental flow is the mechanism-level proof).

## Findings Requiring Developer Attention

1. 🔴 **W-1 (FIXED c70092ad, verify before merge):** missing ChatGenerationChunk import — branch as originally committed (dd43a7f1) broke every daemon LLM invoke at runtime. One-line fix applied by tester under quick-fix authority; regression-guarded by tests/unit/test_llm_streaming_wire_verify.py.
2. 🟠 **W-2 (open, policy decision):** `OPENAI_STREAMING=""` (empty) → pydantic **ValidationError at boot**, not fallback-to-True. Recommend `env_ignore_empty=True` on LLMConfig or document the failure mode.
3. 🟡 **Dev WIP observed mid-verification (informational):** uncommitted matched pair — `stream_usage=True` injection in clean_llm_config + `TestStreamingInvokeEndToEnd` E2E class. WIP class passes 1/1 at dev's dirty state. Not part of this gate (gate = committed c70092ad); will need its own verification when committed. Growing set of dirty files observed; shared-worktree coordination hazard (see LESSONS).

## ensure.md Validation Results (Core, blast-radius scoped)

- ✅ Critical: no regressions in changed packs (10/10 PASS)
- ✅ Critical: dev.sh `--timeout-graceful-shutdown 10` present (dev.sh:102)
- ➖ Critical: concurrency_atomic — not in change set (no job/task/queue/concurrency files touched); pack not run
- Release Gate: NOT triggered (no job/task/queue delta)

## ensure.md Improvement Notices

None — no contradictions this run.

## Code Changes Summary (this session)

| Commit | What |
|---|---|
| 23c031af | test pack llm_streaming_activation_unit_test (worker) |
| 5bfdaebd | wire-verify suite + pack, 16 tests (worker) |
| c70092ad | W-1 fix: ChatGenerationChunk import (worker quick-fix) |

Working tree: `.agents/tester/PACKS.md` + LESSONS + this RESULTS file (tester docs); dev's separate WIP left untouched.

## Documentation Updated

- [x] PACKS.md — 9 pack statuses updated; 2 new packs registered
- [x] LESSONS/2026-08-25-wire-level-fidelity-caught-latent-streaming-decode-defect.md
- [x] RESULTS/2026-08-25-streaming-cf-125s-verification.md (this file)

## Overall Status

- Unit/pack tests: ✅ PASS (10/10, 345 tests)
- Wire-level verification: ✅ PASS (16/16 post-fix)
- Mock fidelity: dev suite fake-shaped → superseded by tester tier-4 suite
- Live evidence: ✅ (CF path) / SKIPPED (local backend offline)
- ensure.md Core: ✅ 3/3 in scope
- **Testing Complete: ✅ READY — conditional on c70092ad (W-1 fix) being included in the merge. DO NOT merge dd43a7f1 alone.**
