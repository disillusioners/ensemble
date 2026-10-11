# Phase 0 Probe Evidence — GO/NO-GO (architecture-decision.md v1.2 §6.0)

**Probe:** one live `stream:true` chat completion on the **vision** model through the EXISTING live production proxy. No deploy, no restart, no config change; neither worktree's code touched. Run by Worker (Phase-0 commission) at **2026-10-10T21:21–21:25 UTC**.
**Script:** `/tmp/od_probe_phase0.py` (throwaway, not repo code). Raw per-chunk timings: `/tmp/od_probe_phase0_timings.json`.
**Client construction mirrors `generate.py` exactly:** `_resolve_llm_config` env resolution (:641-687) + `_do_chat_call` factory (:713-762) — fresh `openai.OpenAI(max_retries=0, default_headers={"x-proxy-app": "ensemble", "x-proxy-interleaved-thinking": "True"}, base_url=<env>)` → `client.chat.completions.create(...)`; openai SDK 2.24.0 from the main checkout venv (`/home/nea/ensemble-src/.venv`).

## Verdict

**GO** — all §6.0 assertions PASS. Option C (consumer-side streaming) reliance is GATED-ON. Flip assumption §5.4 #1 ("vision upstream cannot stream") is empirically REFUTED; §5.4 #2 mitigation (global STREAM_DEADLINE raise) is NOT triggered (TTFB 2.92s ≪ 110s).

## Config summary (REDACTED)

| Env var (NAMES only) | Value |
|---|---|
| `OPENAI_BASE_URL` | `https://llm.dev/v1`-shaped documented endpoint (§3) — non-secret, set |
| `OPENAI_MODEL_VISION` | `vision` |
| `OPENAI_API_KEY` | ***REDACTED*** (present=True; never echoed anywhere) |
| `OPENAI_BASE_URL_BACKUP` | unset in `.env` (failover backup inert for this single attempt) |

Request params: `stream=True`, `stream_options={"include_usage": True}`, `max_tokens=8000` (≥8000 per §6.0), `temperature=0.7` (production knob, `generate.py:944`), `timeout=420.0` (facade wall-clock shape; deliberately NOT the legacy 120s inner cap), system+user messages mirroring `_do_chat_call`.

## Assertions (§6.0)

| # | Assertion | Result |
|---|---|---|
| 1 | SSE chunks arrive | **PASS** — 7996 data chunks |
| 2 | Terminal `finish_reason` chunk arrives (Gate-2 load-bearing, `generate.py:520-522`) | **PASS** — `finish_reason="length"` (max_tokens cap; terminal chunk delivered) |
| 3 | Terminal `usage` chunk arrives, plausible numbers (proves `include_usage` passthrough; kills stream_options-400 risk) | **PASS** — `{"prompt_tokens": 198, "completion_tokens": 8000, "total_tokens": 8198}` |
| 4 | Record finish_reason + usage | `finish_reason="length"`; usage above |
| — | Transport errors | None (no 4xx; stream accepted) |

## Metrics

| Metric | Value | Bound (context, non-verdict-blocking per plan) |
|---|---|---|
| TTFB — response head | **0.322s** | — |
| TTFB — first data chunk | **2.923s** | 110s `StreamDeadline` — **2.7% of bound**, huge margin |
| Max inter-chunk gap (chunk→chunk) | **2.235s** (at chunk idx 6146) | 120s `IdleTerminationTimeout` — **1.9% of bound** |
| Max gap any received line (incl. heartbeats) | 2.601s | client-visible byte gap; heartbeats cap it at ~5s by construction |
| Total duration | **153.995s** | **> the ~120s CF read window — survived only because streamed** (buffered path would 524'd mid-flight; this is Option C's thesis demonstrated live) |
| Chunk count | 7996 data chunks (+31 comment lines + `[DONE]`) | ~52 chunks/sec sustained |

## Raw per-chunk timing summary

- 578 distinct arrival timestamps across 153.99s — transport-level read coalescing (~79 chunks per read burst); per-chunk emission timing below read granularity is not client-observable.
- Arrival profile is near-linear (sustained, no stalls): 10% by T+17.8s, 20% by T+32.4s, 30% T+48.5s, 40% T+62.6s, 50% T+76.3s, 60% T+89.9s, 70% T+104.2s, 80% T+121.7s, 90% T+137.3s, 100% T+154.0s. Cumulative: 462 chunks by T+10s, 3025 by T+60s, 6302 by T+120s.
- Gap stats (chunk→chunk, n=7995): max 2.235s; typical sub-second.
- Heartbeats: **30 × `: heartbeat`** at 5.0s cadence, T+5.32s → T+150.32s, plus **`: connected`** at T+0.322s (matches §2.2 streamed-path machinery). `[DONE]` sentinel observed.

## Anomalies / observations

1. **Upstream model identity: `MiniMax-M3`** (from chunk `model` field) — an interleaved-thinking model. With `x-proxy-interleaved-thinking: True` riding (production headers), content chunks arrive as standard OpenAI deltas but split across **`delta.reasoning_content`** (thinking tokens) and **`delta.content`** (answer tokens). A tiny 40-token forensic call confirmed both fields on the same stream (also `delta.role`, `choice.finish_reason`).
2. **The 8000-token probe budget was consumed entirely by `reasoning_content`** (thinking-only run; `finish_reason="length"`, zero `delta.content` chars). Implication for Phase 1: the SSE join must accumulate `reasoning_content` AND `content`, and `max_tokens` budgeting must cover thinking + answer — consistent with §6.2's budget-chain reconciliation to the 200k vision target. Also **pre-verifies the SSE-emission condition gating §10 Q4** (reasoning_content accumulator): emission is confirmed on this lane.
3. Usage accounting: `completion_tokens` includes reasoning tokens (8000 thinking tokens → completion 8000).
4. `usage` terminal chunk arrived after the `finish_reason` chunk, before `[DONE]` — standard shape.

## Files

- Probe script: `/tmp/od_probe_phase0.py`
- Per-chunk timings JSON: `/tmp/od_probe_phase0_timings.json`
- This evidence file: written 2026-10-10T21:29Z, **NOT git-added/committed** (per commission — a later instance commits)

## Addendum — mid-flight dispatch findings relayed to the implementation lane (2026-10-11)

Dispatcher confirmed the GO verdict relayed with four load-bearing findings; recorded here so they live next to the evidence:

1. **§10 Q4 condition satisfied** — `reasoning_content` emission verified (this file, Anomalies 1–2). Accumulator builds per §6.1: join BOTH fields in SSE consumption; envelope invariant strict — `choices[0].message.content` = the `delta.content` join ONLY; `reasoning_content` accumulates separately, not consumed by extraction today.
2. **Thinking-only truncation profile** — this probe IS the shape: an 8000-token budget consumed entirely by `reasoning_content` (`finish_reason="length"`, zero answer chars). §7.2/§7.10 truncation-path tests must model truncation-with-zero-answer-content — the exact profile the 200k budget (§6.2) and Phase-3 retry exist to handle.
3. **§7.5 heartbeat pin realism** — `: connected` at T+0.322s; `: heartbeat` at exactly 5.0s cadence (30 observed over 154s); max data-chunk gap 2.235s; TTFB 2.923s; 7996 chunks / 154s near-linear arrival, zero stalls.
4. **Env nuance for the §8 comment-rot fix (`generate.py:634-639`, `_OD_FAILOVER_INACTIVE_NOTE`)** — this probe read the CHECKOUT `.env` (`/home/nea/ensemble-src/.env`): `OPENAI_BASE_URL_BACKUP` **unset there**. Plan §3/§8 describe the RUNTIME daemon env, where it IS set (→ failover live). Both statements are true of different envs. Corrected comment wording must be env-scoped — e.g. *"failover is active when `OPENAI_BASE_URL_BACKUP` is set at runtime; the production daemon env has it set (a checkout `.env` may not)"* — never a per-env falsehood. Plan §8 fix-item text left as-is (runtime-accurate); nuance recorded here for the implementer.
