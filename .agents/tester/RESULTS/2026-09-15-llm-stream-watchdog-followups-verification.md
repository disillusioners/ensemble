# Verification Gate — LLM Stream-Watchdog Follow-Ups, Round 2 (2026-09-15)

**Branch:** `feature/llm-stream-watchdog-followups` @ `9d59fdfe` (base `2b36e398` = v0.13.0; commits `3a8b45cf` main + `9d59fdfe` W-R2-1 text fixes)
**Scope (leader-mandated FOCUSED):** 3 files — `daemon/services/llm_stream_watchdog.py`, `daemon/config.py`, `tests/unit/test_llm_stream_watchdog.py`. Actual diff **+278/−35** (brief said +281/−38 — cosmetic 3-line drift in the brief, same file set). No 11k sweep rerun (per dispatch).
**Worktree:** `agents-ensemble-wt-llm-stream-watchdog-followups` (identity verified: branch + HEAD + staged-index empty; only expected dirty path during the gate was the tester's own PACKS.md anchor edit).

---

## VERDICT: ✅ SHIP (GO)

| Dimension | Result |
|---|---|
| Watchdog family (grew 38→45) | **`45 passed in 3.46s`** (count derived); real-socket class 3×: 2.13/2.14/2.14 s — no flake |
| items_ab regression guard (new base) | **`19 passed in 0.19s`** — exact match, zero reds on 2b36e398 |
| gzip + httpx pin | **`26 passed in 4.30s`**; httpx 0.28.1 / httpcore 1.0.9; uv.lock diff empty |
| Token census (claim 3) | `[LLM-WATCHDOG-STALL]` ONLY on attempt (:497) + success (:507) [+1 doc comment]; `[StreamWatchdog]` ONLY on fallback-failure ×5 + lifecycle ×2 + close-debug ×1; **zero bleed**, zero orphan logger calls |
| D1 claim-then-abandon (independent probe, 3 legs) | **ALL PASS** — no leak, close attempted, original exception unmasked, D3 order `attempt→failed` |
| D2 revert-sensitivity (live mutation) | **PROVED** — token mutation → `1 failed, 44 passed` (exact pin); message-split mutation → `1 failed, 44 passed` (exact pin); restores green `45 passed`; byte-identical |
| Timing band | `0.8 ≤ elapsed < 2.4` coded-in w/ documented lower-bound derivation (upper = 3× threshold, tightened from 8 s) |
| Config | default=45 + ge=10 INTACT; probe numbers in comment; **zero** new ENSEMBLE_* flags; **zero** new env reads |
| ensure.md Core (scoped to changed packs) | PASS (changed packs all green; no lock/dev.sh surface touched — out of blast radius) |

---

## 1. Independent pack runs

| Pack | Verbatim summary |
|---|---|
| `tests/unit/test_llm_stream_watchdog.py` | `45 passed in 3.46s` |
| ×3 real-socket (`TestRealSocketStallAbort`: timing-band + heartbeat-negative) | `2 passed in 2.13s` / `2.14s` / `2.14s` |
| `tests/unit/test_llm_stream_stall_hardening_items_ab.py` | `19 passed in 0.19s` |
| `tests/unit/test_llm_request_gzip.py` | `26 passed in 4.30s` |

Count derivations: watchdog `--collect-only` = 45 (growth 38→45 = 7 new tests: TestW3FallbackMessagesPinned ×4, TestPostClaimErrorBestEffortClose ×2, +1 AST lifespan pin); items_ab 16 funcs + parametrize ×4 = 19; gzip 26. 45+19+26 = 90 focused-family green.

## 2. Claim verification (fresh eyes)

### 2.1 Token census (grep -F — bracket-literal greps required; plain regex silently misses tokens)
- `[LLM-WATCHDOG-STALL]`: :494 doc comment, :497 **abort-attempt** WARNING, :507 **forced-shutdown success** WARNING. No other emission.
- `[StreamWatchdog]`: :521 shutdown-OSError fallback, :529 socket-unresolvable fallback, :535 force-unblock catch-all (D1), :545 post-error close-debug, :583 loop-started, :593 sweep-tick guard, :594 loop-stopped.
- Tests pin the tokens legitimately (caplog filters/docstrings); `daemon/config.py` carries none. **Zero bleed either direction.**

### 2.2 D1 — claim-then-abandon probe (`/tmp/d1_claim_then_abandon_probe.py`, in-process, production symbols only)
- **Leg 1 (abandon, close succeeds):** claim wins at :489 (BEFORE socket resolution :502 — ordering read from source), `_find_network_sock` raises → sweep returns 0; **registry empty** (`_entries` popped + `force_closed=True` — no leak); `response.close` called exactly once; ERROR record `[StreamWatchdog] force-unblock failed` carries the ORIGINAL `probe-claim-then-abandon` in exc_info; WARNING `[LLM-WATCHDOG-STALL]` attempt record precedes it (`records=[attempt@0→failed@1]`).
- **Leg 2 (close also raises):** no exception escapes; registry still empty; BOTH failures logged distinctly (original ERROR + DEBUG `[StreamWatchdog] post-error response.close() also failed` w/ close-exception text) — neither masks the other.
- **Leg 3 (lost race, negative control):** pre-claimed entry → `_force_unblock` returns False at :489-490 **before any socket/close touch** (`close.call_count == 0`).
- Harness notes (benign, documented): real signature `_force_unblock(entry, threshold_seconds, registry)`; probe set logger level DEBUG for leg-2 record capture.

### 2.3 D2 — revert-sensitivity (live mutation → FAIL → restore → green; source byte-identical after)
| Mutation (1-occurrence exact replace) | Result | Biting pin |
|---|---|---|
| Success-line token `[LLM-WATCHDOG-STALL]` → `[StreamWatchdog]` (:507) | **`1 failed, 44 passed in 3.55s`** | `TestW3FallbackMessagesPinned::test_success_line_carries_token_and_full_content` — "success line must carry the [LLM-WATCHDOG-STALL] token" |
| `socket unresolvable` → `shutdown() failed` (:529, W3 split collapse) | **`1 failed, 44 passed in 3.49s`** | `TestW3FallbackMessagesPinned::test_unresolvable_socket_message` — DISTINCT-message assert |
| Restore + rerun (both phases) | `45 passed in 3.51s` / `3.47s` | — |

Final integrity: `git diff -- daemon/services/llm_stream_watchdog.py` EMPTY; HEAD `9d59fdfe`; only dirty path = tester's PACKS.md anchor (1+/1−). **The pins are real regression gates, not paper assertions.** (Transient index.lock during Phase A restore — 3 s wait + retry per conventions §(h), no data loss.)

### 2.4 Timing band
`0.8s ≤ elapsed < 2.4s` asserted in the real-socket stall test (threshold 0.8 s, tick 0.1 s, read deadline 600 s so only the watchdog can unblock). Lower bound = threshold (premature abort would false-kill healthy streams; derivation documented in-test); upper = 3× threshold (tightened from round-1's 8 s / 10×). 3× runs stable to 0.01 s.

### 2.5 Config
Field UNCHANGED from base: `stream_stall_threshold_seconds: int = Field(default=45, ge=10, …)`. New comment + description carry the probe evidence (cadence ~5.0 s both proxies, max legit gap ~3.9 s — conservative ≥ max observed 3.8 s, 45 s ≈ 9× cadence). Zero new `ENSEMBLE_*` flags, zero new `os.environ/getenv` reads (diff-scoped greps empty). Tuning-only via existing `OPENAI_STREAM_STALL_THRESHOLD_SECONDS` env-prefix.

## 3. Probe evidence table (dev report, recorded in config comment)

| Proxy | Sample window | Heartbeat cadence (avg/max) | Max legitimate inter-content gap | 45 s default vs cadence |
|---|---|---|---|---|
| llm.ensem.dev | 87.7 s | 5.0 s / 5.8 s | 3.8 s | ≈ 9.0× |
| llm.daoduc.org | 77.3 s | 5.0 s / 5.1 s | 2.8 s | ≈ 9.0× |

Probe ran against PROD proxies (leader-adjudicated accepted). Config comment's "~3.9 s max legit gap" is a conservative rounding ≥ the max observed 3.8 s — consistent.

## 4. Living anchor sync (W-R2-2) — landed

`.agents/tester/PACKS.md` round-1 banner soak anchor updated: old `[StreamWatchdog] stream stalled` → **`[LLM-WATCHDOG-STALL]` stall-abort attempt+success on real incidents; `[StreamWatchdog]` fallback-failure + lifecycle only** (split as of `9d59fdfe`; round-1 wording retired; history preserved in RESULTS/2026-09-14). Historical RESULTS 2026-09-14 doc deliberately NOT rewritten. Round-2 banner added atop PACKS.md (this gate).

## 5. ensure.md Core (round-2 scope)

- Changed packs (llm-stream-watchdog family + regression guards): ALL PASS → Core "no regressions in changed packs" **PASS**.
- Concurrency/dev.sh/await surfaces untouched by the 3-file diff (config comment + watchdog logging/except path only) → out of blast radius, not re-run (round-1 covered them at this lineage).
- No contradictions with ensure.md methods. Release Gate not triggered (focused follow-up).

## 6. Follow-ups (non-blocking)

1. 🟢 Round-1 test-debt list unchanged (see RESULTS/2026-09-14 §9 + QUARANTINE consolidated row) — not re-adjudicated this round per focused scope.
2. 🟢 D1 probe pattern (claim-ordering + unmasked-exception record assertions) is a good candidate to fold into the repo family if the D1 hunk is ever touched again.
3. 🟢 Brief diff-stat drift (+281/−38 claimed vs +278/−35 actual) — cosmetic; noted so future gates cite the derived number.

## 7. Dispatch & evidence ledger

R2W1 forensics `753dff50` · R2W2 watchdog pack `3006910a` · R2W3 items_ab `ea973338` · R2W4 gzip `ca1f693a` · R2W5 D1 probe `c2c8d5f9` · R2W6 D2 revert `85ee8060` (preflight stop on tester's own PACKS.md edit → dispatcher-approved Option A; index-lock retry per §(h)). All runs `uv run python -m pytest` from worktree root with `timeout 300` wrappers; probes in `/tmp`; zero unintended worktree mutations (byte-identical restore proven).
