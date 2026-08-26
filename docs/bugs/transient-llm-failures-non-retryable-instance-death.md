# Bug: Transient LLM/Proxy Failures Classified Non-Retryable — Instance Death Cascade

**Date:** 2026-08-26
**Status:** Open
**Severity:** High (94% of instance ERROR deaths in a 7-day window; causes replacement storms during provider outages)
**Affected versions:** Current (`latest` as of 2026-08-26); symptoms start 2026-08-19 (proxy ultimate-model routing rollout)
**Related:** [`docs/retry-architecture.md`](../retry-architecture.md) (§9, gap #6/7) · [`docs/plans/rate-limit-episode-parking.md`](../plans/rate-limit-episode-parking.md) (DRAFT — does not cover these channels) · commit `d4b7b8a4` (payload logging, first step)

---

## Summary

The LLM error classifier (`daemon/llm_error_classifier.py:606-608`) funnels every exception
that is not an `APIStatusError`-with-retryable-status into a single generic
`except Exception` branch logged as `[LLM] Unexpected error (will not retry)`. The
LLM supervisor proxy delivers its transient failures through **four channels other than
status codes**, so they all land in that branch and kill the turn with **zero L1 retries**,
despite the configured budget of 10 transient attempts. The instance dies `ERROR`, the
parent receives a misleading `RECOVERY_GUIDANCE_HINT` ("revive once, spawn replacement"),
and during provider-wide outages this produces replacement storms.

Additionally, the proxy's **ultimate-model escalation** (3rd identical request by message
hash → route to high-cost "ultimate" model) invalidates the L1 retry budget: the openai SDK
default `max_retries=2` resends identical messages sub-second inside a single tenacity
attempt, reaching the escalation threshold in ~1–2 seconds, before tenacity's exponential
backoff ever produces a meaningful wait.

## Fatality corpus (2026-08-19 → 2026-08-26, logs `ensemble.log` → `ensemble.log.3`)

47 instance-fatal events, ~40 distinct instances, **all `job_id=none`** (observer lane —
no L4 retry, jobs strand). Aggregated by terminal error:

| # | Terminal error | Channel | Events | Sample instances (date, time) | error_type | Should be retryable? |
|---|---|---|---|---|---|---|
| 1 | `All models rate limited` (bare `openai.APIError`, no status code) | bare APIError message | 21 | `e6babb76`×3, `5a726b79`×2, `4f7809f8`×2, `81ed809a`, `a0e538d4`, `820fb1da`, `3892accb`, `054c3dc5`, `7e80e8d3`, `99600114`, `4fea1e32`, `4821b5a7`, `811a4796`, `1228586e`, `8bd71ff2`, `522359ed` (Aug 26, 06:10–06:54) | `execution_error` | **Yes** (rate limit) |
| 2 | `{'type': 'ultimate_model_retry_exhausted', 'code': 'exhausted'}` (`ValueError`) | HTTP 200 dict body, parsed by LangChain | 8 | `61b8cb98` (Aug 26 11:55), `a2f3f3ab`, `cb6dab84`, `9ff40663`, `4cd7972f`, `cba0bb16`, `9f6d5109`, `073aaaee`, `80890dd7` (Aug 19–26) | `invalid_data` | **Yes** (proxy gave up after escalation; one backed-off retry typically succeeds) |
| 3 | `peer closed connection without sending complete message body` (`httpx.RemoteProtocolError`) | mid-stream disconnect | 7 | `9c86a449`, `48b65ed2`, `c1068cd9`, `df7d0870`, `547dfe49`, `5510ea54`, `bfd09236` (spread across all 7 days) | `execution_error` | **Yes** (transient network) |
| 4 | `No generations found in stream.` (`ValueError` from LangChain) | HTTP 200, SSE stream closed with zero chunks | 4 | `bfbf89be` (04:47), `ed855baa` (12:07), `47f60fff` (12:38), `a368e28c` (21:55) — all Aug 26 | `invalid_data` | **Yes** (transient stream) |
| 5 | `context deadline exceeded (Client.Timeout ...)` relayed via proxy (upstream minimax) | bare APIError message | 2 | `81ed809a`, `5d41ff64` (Aug 26 13:29) | `execution_error` | **Yes** (upstream timeout) |
| 6 | `Token Plan usage limit reached (2056)` | bare APIError message | 1 | `e6babb76` (Aug 26 06:10) | `execution_error` | No (quota — terminal is correct, but should be a distinct reason) |
| 7 | `tool call result does not follow tool call (2013)` | APIError | 1 | `8b6fd0cf` (Aug 25 23:22) | `execution_error` | No (genuine bad params) |
| 8 | `'str' object has no attribute 'model_dump'` | 200 str body (pre-type-guard) | 1 | `f10b7694` (Aug 15) | `execution_error` | Historical — fixed by `MalformedLLMResponseError` guard (`daemon/graph.py:2001`) |
| 9 | `Instance not found` / `Invalid JSON body` | infra / client bug | 2 | `354a591a`, `ce1370da` | `instance_not_found` / `bad_request` | No (not LLM) |

**44 of 47 events (94%) are transient failures that received zero retries.**

## The replacement storm (headline incident)

Aug 26, 06:51:26–06:54:39 — one provider-wide rate-limit window killed **15 instances in
4 minutes** via channel #1. Parents following `RECOVERY_GUIDANCE_HINT` revived/spawned
replacements that hit the same rate limit and died identically (repeat victims:
`e6babb76` ×4 total, `5a726b79` ×2, `4f7809f8` ×2, `81ed809a` ×2). This is the
"replacement storm" failure mode `docs/retry-architecture.md` §9 warns about, with log
evidence.

## Root causes

### RC1 — Classifier generic branch is the main killer

`_run_with_classification` (`daemon/llm_error_classifier.py:550-608`) only treats
`APIStatusError` with retryable status, `APIConnectionError`, `APITimeoutError`, raw socket
errors, and the typed validation exceptions as retryable. Channels #1–#5 above never match
an explicit branch → generic `except Exception` → non-retryable → turn dead.

Note the irony of channel #1: the message `All models rate limited` **matches the parking
plan's default body pattern** `['all models rate limited']` — but the plan's detection site
(§4.1) only inspects 429 `APIStatusError` bodies, so it never fires on the bare `APIError`
shape.

### RC2 — Ultimate-model escalation defeats the L1 budget (timing)

Proxy behavior (confirmed by owner): 1st request on a message-hash fails → remembered;
2nd identical → counted; 3rd identical → routed to the high-cost ultimate model (which
itself gets max 2 attempts). The openai SDK default `max_retries=2` (never overridden on
the `ThinkingChatOpenAI` choke point, `daemon/graph.py:2265`) resends identical messages
with sub-second backoff (`Retrying request ... in 0.4-0.9 seconds` in logs), so escalation
happens **inside tenacity attempt #1, within ~2 seconds**. When the ultimate model is also
rate-limited, the terminal `ultimate_model_retry_exhausted` arrives as a 200 body (channel
#2) → non-retryable → effective retry count = **0** of the configured 10.

Consequences:
- L1's exponential-jitter backoff never runs before escalation.
- Cost inversion: everyday transient blips (429/524/500, malformed responses) escalate to
  the high-cost model automatically; tenacity attempts #2+ (same hash) presumably stay on
  ultimate routing.
- Checkpoint resume (`is_retry=True`) and the revive path resend the same message list →
  same hash → straight to ultimate routing, if the proxy's hash counter does not decay.

### RC3 — Same exhausted error delivered in two body shapes, handled oppositely

In 6 of 8 channel-#2 incidents, the invocation first logs
`[LLM] Malformed response (retryable): expected dict or object with model_dump(), got str`
(`MalformedLLMResponseError`, retryable-but-futile: same hash, next attempt is the fatal
dict shape), then dies 1–2 s later on the dict-shaped `ValueError` (non-retryable). The
str payload was **not logged** (exception message carries only the type name) — fixed in
commit `d4b7b8a4` (INFO-level `repr(response)[:300]` at the guard raise site,
`daemon/graph.py:2001-2008`); no hits yet as of 2026-08-26 22:06.

## Fix proposal (priority order)

1. **RC1 (widest impact):** classify transient shapes in the classifier — add branches
   mapping to `TransientAPIError` (or `ProviderRateLimitError` per the parking plan):
   - bare `openai.APIError` message-pattern matching (`All models rate limited`, rate-limit
     markers; NOT quota `Token Plan usage limit` / bad-params codes),
   - `httpx.RemoteProtocolError` / `httpx.ReadTimeout` into `TRANSIENT_EXCEPTIONS`,
   - `No generations found in stream.` `ValueError` (stream-empty) as retryable,
   - `ultimate_model_retry_exhausted` ValueError (intercept in
     `ThinkingChatOpenAI._create_chat_result`, re-raise typed) as retryable/transient.
   Converts 44/47 historical fatalities into L1 retries.
2. **RC2:** set `max_retries=0` on the `ThinkingChatOpenAI` construction choke point so
   only tenacity (real backoff spacing) retries — **requires confirming the proxy hash
   counter decays**; if it never decays, the escalation trigger must change proxy-side
   (windowed counting, or don't count rate-limit failures).
3. **RC3:** pattern-match the str-body `MalformedLLMResponseError` payloads (evidence
   accumulating via `d4b7b8a4`) and map exhausted/rate-limit payloads to the episode path
   instead of blind retry.
4. **Parking plan amendments** (see `docs/retry-architecture.md` §9 comparison table):
   widen detection to all four channels; fix clear-on-success under ultimate routing
   (successes during an outage come from the ultimate model — require normal-route success
   before clearing episode state); re-dispatch on a *mutated* message list or rely on hash
   decay so parked wake-ups don't ride ultimate routing.

## Evidence anchors

- Fatal-error extraction: `grep "handle_message_processing_error" ensemble.log*` (47 events).
- Non-retry funnel: `grep "Unexpected error (will not retry)" ensemble.log*`.
- Storm window: Aug 26 06:51:26–06:54:39, 15 events, all `All models rate limited`.
- SDK sub-second retry bursts: e.g. Aug 26 11:56:55–11:57:03, 22:04–22:06.
- Escalation incidents: `ultimate_model_retry_exhausted` × 10 lines per event (classifier →
  graph → instance_messaging → task_processor → message_processing_errors fan-out), 8
  distinct hashes across 4 log files.
