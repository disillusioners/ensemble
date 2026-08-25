# Test Report: Follow-up Gate at FINAL HEAD 4db97e3c (streaming CF 524 fix)

**Date:** 2026-08-25 · **Branch:** `fix/llm-streaming-cf-125s` @ **4db97e3c** (clean tree) · Prior gate: RESULTS/2026-08-25-streaming-cf-125s-verification.md (stopped at c70092ad)
**Workers:** f-pack-activation d3be45c8, f-pack-wireverify 2e8f2b46, f-pack-trio 14ad139e, f-pack-graphretry 2c317fb8, f-pack-failover e5be5ee2, f-pack-failover2adv d81ca96c, f-w2-behavior be855279, f-wire-analysis 560d7fab, f-live-probe2 25813d17

## Overall: ✅ READY FOR MERGE at 4db97e3c

## 1. W-2/S1 closure — ✅ CLOSED (behavior-verified, not just tests)

`_coerce_streaming_empty_to_default` (daemon/config.py:176-208): None → True; any strip-empty string (`""`, `" "`, `"\t "`) → True; real values passthrough to pydantic coercion. **10/10 clean-env subprocess matrix rows correct, zero ValidationErrors** (unset/""/" "/"\t "/false/False/FALSE/0/1/true). Opt-out end-to-end confirmed: `OPENAI_STREAMING=false` → LLMConfig False → class var False → clean_llm_config streaming False (no hardcoded-True bypass).

**Nuance finding:** YAML `${OPENAI_STREAMING:-true}` interpolation does NOT treat empty as unset (implementation substitutes only when `None` — deviates from POSIX `:-`). **The pydantic validator is the sole operative empty-guard** (also for direct-env deployments bypassing YAML). Fine as-is; recorded so nobody deletes the validator assuming YAML covers it.

3 S1 tests map to the fix (empty-string, YAML-null, false-passthrough). Untested-but-behavior-verified rows: whitespace, case variants, string digits, load_config-with-empty-env.

## 2. W-1/stream_options — ✅ CONFIRMED GREEN FOR THE RIGHT REASON

V1a asserts `stream_options == {"include_usage": True}` on the **captured serialized POST body** (httpx handler `json.loads(request.content)` — genuine wire boundary). **Teeth proven:** scratch run with `stream_usage` stripped post-chokepoint → no `stream_options` key → assertion would fail. No leak on opt-outs (streaming=False and stream_usage=False bodies carry no `stream_options` — backend-400 hazard avoided). Injection is clobber-safe (`if "stream_usage" not in cleaned`).

## 3. Combined suites @ 4db97e3c — 6/6 PASS, zero regressions vs c70092ad baseline

| Pack | HEAD | Baseline (c70092ad) | Delta |
|---|---|---|---|
| llm_streaming_activation (19 activation + 2 graph) | ✅ 21/21, 1.09s | 17/17 | +4 expected (C2 + 3×S1) |
| llm_streaming_wire_verify | ✅ 17/17, 1.37s | 16/16 | +1 expected (refresh) |
| reasoning_content_regression | ✅ 43/43, 0.67s | 43/43 | baseline-exact |
| graph_retry | ✅ 19/19, 0.71s | 19/19 | baseline-exact |
| llm_failover | ✅ 64/64, 10.76s | 64/64 | baseline-exact |
| llm_failover_v2_adversarial | ✅ 48/48, 1.19s | 48/48 | baseline-exact |

## 4. C2 round-trip legitimacy — ✅ LEGIT

Real `clean_llm_config` chokepoint (line 550) → real `.invoke()` (line 563) → `\n\n`-separated SSE frames + `[DONE]` via httpx.MockTransport → four aggregate assertions (content fragments, reasoning_content merge, tool_calls name/id/args, usage_metadata non-null). **Would have caught C1:** decode path (`_convert_chunk_to_generation_chunk`, graph.py:2042/2080) raises NameError before any assertion evaluates — empirically confirmed by scratch namespace removal. Docstring explicitly names the gap it closes.

Minor (non-blocking, listed for backlog): 2-fragment tool-args split (docstring says 3 — cosmetic); single tool_call, no interleaving; happy-path only.

## 5. Live probe @ HEAD with stream_options — ✅ DONE

llm.ensem.dev (CF-proxied): **HTTP 200, TTFB 0.169s, 18 chunks, usage-bearing final chunk present (`completion_tokens:20, prompt_tokens:17, total_tokens:37`), clean `[DONE]`, no buffering.** Backend ACCEPTS the new `stream_options` field — no merge blocker. Anomaly (non-blocking): usage folded into the `finish_reason` chunk rather than a separate empty-choices chunk (functionally equivalent; ensemble parser tolerates both shapes — wire-verify fixtures cover both).

## Follow-ups (backlog, none blocking)

- 🟢 Add symmetric wire test for `stream_usage` clobber-safety (mirrors V6's streaming test)
- 🟢 Add stream_options assertion to V1b/V1c (currently only V1a)
- 🟢 Add one-line tests for whitespace/case-variant env rows + load_config-empty path
- 🟢 C2 fixture: split tool args into 3 fragments to match docstring claim
- 🟢 Tooling note: `grep_files` returned a false "no matches" on this repo once — cross-check with bash grep before trusting negatives

## Code Changes Summary

No tester code changes this round (all commits dev's 4db97e3c; tester commits remain 23c031af/5bfdaebd/c70092ad from prior round).

## Documentation Updated

- [x] PACKS.md — 6 pack entries updated to 4db97e3c results
- [x] RESULTS/2026-08-25-streaming-cf-125s-head-4db97e3c-followup.md (this file)
