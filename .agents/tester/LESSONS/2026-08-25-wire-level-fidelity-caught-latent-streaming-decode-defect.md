# W-1 Latent Streaming-Decode Defect — Config-Level Suites Cannot Catch It

**Date:** 2026-08-25
**Branch:** fix/llm-streaming-cf-125s (dd43a7f1 → 23c031af → 5bfdaebd → c70092ad)
**Related:** RESULTS/2026-08-25-streaming-cf-125s-verification.md

## What happened

The dev's new suite `tests/unit/test_llm_streaming_activation.py` (15 tests, all green, dev-reported 15/15)
verified the streaming activation fix at THREE tiers — config-dict transforms, `_get_request_payload(...)`
payload-dict assertions, and a MagicMock `.invoke()` delegation check. A fidelity audit classified all 15 as
CONFIG/payload-dict level: **zero tests exercised a real HTTP transport, zero fed SSE bytes through the
streaming decode path, zero ran `.invoke()` end-to-end.**

A supplemental wire-level suite (`tests/unit/test_llm_streaming_wire_verify.py`, httpx.MockTransport,
real `clean_llm_config → ThinkingChatOpenAI → .invoke()`) went 6-red immediately:

- **W-1 (blocker):** `daemon/graph.py` referenced `ChatGenerationChunk` at :2041/:2079
  (`_convert_chunk_to_generation_chunk`) **without importing it**. Every streamed response raised
  `NameError` AFTER the request went out. Latent since commit 37f39c8b — the decode path was never
  entered until `streaming=True` became the default. The branch as committed (dd43a7f1) broke every
  daemon LLM invoke at runtime while its own test suite reported 15/15 green.
- Fix: one-line import (+6-line comment) → commit c70092ad; wire suite flipped 6-red → 16/16 green.

## Root cause of the miss

The wire-flag assertions called `_get_request_payload(...)` — a Python dict the SDK *intends* to send.
The dict is correct; the failure lived one layer down (streaming decode of the response). Any test that
stops at request-shape or config-dict level is blind to defects in response handling — and streaming
activation is precisely a change that MOVES execution into a previously-dead code path.

## Lesson (pattern to reuse)

1. **When a change activates a previously-dormant code path, gate on that path's END-TO-END behavior,
   not its entry conditions.** Default-flip changes (streaming, retries, fallbacks) need a mock-transport
   round trip: real client code + httpx.MockTransport returning REAL wire shapes (SSE `data:` frames,
   chunked deltas, `[DONE]` terminator), asserting the final aggregated AIMessage.
2. **Mock fidelity tiers:** config-dict < payload-dict < real-transport wire capture < full-decode
   round trip. A suite can be 100% green at tier 1–2 while tier 3–4 is broken. Classify new suites by
   tier before trusting them (see recon workflow below).
3. **Wire-level verification is cheap:** the supplemental suite runs in 1.5s, in-process, zero ports,
   zero network (httpx.MockTransport). There is no excuse to skip it on LLM-path changes.
4. **Dev self-reported test counts are not evidence** until the suite's fidelity tier is known. Ask:
   does ANY test in the new suite exercise the transport? If not, dispatch a wire-verify suite before
   believing the fix.

## Coordination hazard (secondary lesson)

Mid-verification, uncommitted dev WIP (+219 test / +37 prod lines, "W1" stream_usage follow-up) appeared
in the SHARED worktree while tester packs were running against it. Attribution required a forensic audit
(timestamps + symbol diff + paired prod/test changes). Mitigation used: **verify committed HEAD in an
isolated `git worktree add /tmp/...` checkout** — non-destructive to dev's in-flight work, gives clean
gate numbers. Pack scripts hard-code relative `.venv/bin/pytest`; run the main repo's absolute venv
pytest with the pack's exact flags from the temp worktree instead.

## Files

- Wire-verify suite (regression guard): tests/unit/test_llm_streaming_wire_verify.py (commit 5bfdaebd)
- Pack: test/packs/llm_streaming_wire_verify_unit_test.sh
- W-1 fix: daemon/graph.py import (commit c70092ad)
- Activation pack: test/packs/llm_streaming_activation_unit_test.sh (commit 23c031af)
