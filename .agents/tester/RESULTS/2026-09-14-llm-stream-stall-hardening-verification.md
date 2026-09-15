# Verification Gate — LLM Stream-Stall Hardening (2026-09-14)

**Branch:** `feature/llm-stream-stall-hardening` @ `752da6b5` (base `b5100215`, 4 commits: `e93681f3` L1 log-truth + L0 request_timeout, `02076eba` L2 watchdog, `687bee87` fakes + gzip edge tests, `752da6b5` council-review W1-W4 + S5)
**Worktree:** `agents-ensemble-wt-llm-stream-stall-hardening` (identity verified: branch + HEAD + clean tree + empty staged index)
**Range:** 18 files, +2,296/−86 (6 daemon files incl. NEW `daemon/services/llm_stream_watchdog.py` +688; 12 test files). `uv.lock` UNCHANGED in range; httpx pinned **0.28.1** (pin fact for `llm_gzip` private API).
**Gate role:** independent functional gate (third pass; two prior code reviews APPROVED). All execution via workers, `uv run python -m pytest` from worktree root, `timeout 300` wrappers, report-only (zero fixes, zero source edits on gate legs).

---

## VERDICT: ✅ SHIP (GO)

| Dimension | Result |
|---|---|
| Marquee proof (stall abort + retry routing, real sockets, independent probe) | **ALL PASS** (A/B/C/D1/D2, ran 2× identical) |
| New family `test_llm_stream_watchdog.py` | **38 passed in 3.43s**; real-socket class 3× flake-free |
| New family `test_llm_stream_stall_hardening_items_ab.py` | **19 passed in 0.18s** (count derived: 16 funcs + parametrize fan-out) |
| Touched-family regression (17 files incl. 9 in-range-modified) | **561 passed** (+ 2 known pre-existing allowed_models, excluded) |
| Gzip family + httpx pin | **26 passed in 4.33s**; httpx 0.28.1; uv.lock diff empty |
| Broader sweep `tests/unit` (9 packs, 11,281 collected) | **11,144 P / 57 F / 23 E / 57 S** → **80/80 incident nodes pre-existing at base, NEW = 0, DIVERGENT = 0** |
| Static gates (ge=10 floor, flags, env reads, fake drift) | **ALL CLEAN** |
| ensure.md Core (critical) | **4/4** |
| Branch-caused failures | **ZERO** |

Reviewer's "83/83 touched suites, 3 consecutive flake-free passes" independently corroborated: 38+19+26 = 83 on this gate's fresh runs.

---

## 1. Marquee Proof — independent real-socket probe (the user's explicit ask)

Probe: `/tmp/marquee_probe_llm_stream_stall.py` (throwaway; not in worktree), run 2× with identical results, ~15s wall, exit 0. All production symbols imported at file:line (no reimplementations): `WatchdogHTTPTransport` (`llm_stream_watchdog.py:322`), `StreamStalledError` (:101, `httpx.ReadTimeout` subclass), `STREAM_WATCHDOG_REGISTRY` (:215), `make_watchdog_httpx_client` (:583), `run_stream_watchdog_loop` (:536), `TIMEOUT_EXCEPTIONS` (`llm_error_classifier.py:483`), `make_llm_retry_strategy` (:695), `classify_llm_errors` (:890); tenacity `Retrying` mirroring `graph.py:8036-8041`. Wire shape cribbed from the repo's own `_SSETestServer` (real chunked SSE). Servers bind `127.0.0.1:0`; read deadline 600s so ONLY the watchdog can unblock.

| Leg | Proof | Result |
|---|---|---|
| **A — stall abort** | threshold 2.5s/tick 0.25s; server sends headers + 1 heartbeat then silence; reader sees `StreamStalledError` (also `httpx.ReadTimeout`), msg "stream stalled: no bytes for…"; **elapsed 2.63s ∈ [2.5, 6.0]** — includes the LOWER bound the repo test lacks (no premature abort) and proves threshold-scaled (NOT the 600s/610s deadline); registry drained | **PASS** |
| **B — heartbeat no-abort** | 8 beats @0.5s over 4.06s total (> threshold) → clean completion, all beats + data received, registry drained | **PASS** |
| **C — non-SSE pass-through** | `application/json`, half body → 4.01s silence (> threshold) → rest; full body received, NO abort, registry empty throughout (SSE-only gate held) | **PASS** |
| **D1 — routing statics** | `isinstance(StreamStalledError(...), TIMEOUT_EXCEPTIONS)` True; retry predicate → **timeout bucket** (`counts(timeout=1, transient=0)`), decision True on attempt ≤ budget; classifier re-raises SAME exception with the new mid-stream log line `[LLM] HTTP timeout (mid-stream, retryable via timeout budget): …` | **PASS** |
| **D2 — retry re-invokes** | stall-once/succeed-on-twice server + real watchdog client + tenacity `Retrying(stop_after_attempt(3), reraise=True)` w/ classifier predicate → **exactly 2 invocations**, final body = recovered response (`recovered-after-stall`), registry drained — stall is NOT terminal | **PASS** |

Cleanup: 0 non-daemon threads leaked; all sockets/clients/loops closed. Probe deviations (documented, benign): fresh `make_watchdog_httpx_client(use_gzip=False)` per leg instead of the process-wide cached singleton (singleton close would poison later legs — identical transport composition); watchdog loop started before the retry callable in D2 (required for tenacity to observe the failure).

**Chain verified end-to-end:** stall → per-byte wall-clock stamps → watchdog sweep (`sock.shutdown(SHUT_RDWR)` force-unblock) → `StreamStalledError(httpx.ReadTimeout)` → `TIMEOUT_EXCEPTIONS` → timeout retry budget (tenacity) → re-invoke → recovery; on exhaustion → agent_node catch tuple (new `httpx.TimeoutException` member, `graph.py:7469-7473`) → loud-ERROR handler.

## 2. Stall-test scrutiny (repo test quality — mock-shortcut audit)

`tests/unit/test_llm_stream_watchdog.py` read in full (960 lines):
- Real-socket tests are GENUINE: raw TCP server thread (`socket.AF_INET`, `127.0.0.1:0`), real `httpx.Client(transport=WatchdogHTTPTransport(httpx.HTTPTransport()))`, real httpcore read path, `timeout=httpx.Timeout(600.0, connect=5.0)` ensures only the watchdog can unblock; reader-block proof via `rt.join(15)` + `assert not rt.is_alive()` (a hang hard-fails). The production loop function itself is used (`run_stream_watchdog_loop(reg, 0.8, …, interval_seconds=0.1)` — sub-10s direct-arg thresholds legitimately bypass the config-layer `ge=10` floor, which is separately tested in `TestConfigKnob`).
- No mock shortcuts on the wire-abort proof: `MockTransport` confined to semantic unit tests; hand-built streams only for exception/stamping semantics; singleton identity pinned (`registry is STREAM_WATCHDOG_REGISTRY`).
- Heartbeat reset pinned at unit level (`registry.touch` on every chunk incl. comments, `llm_stream_watchdog.py:254`) AND real-socket level; non-SSE gate pinned incl. case-insensitivity.
- **Report-only divergences (test-debt, none gate-blocking):** (1) api.py lifespan wiring pinned by file-text grep, not import ("importing api.py boots the app factory" — deliberate but grep-pin false-confidence class); (2) abort upper bound `elapsed < 8.0` is ~10× the 0.8s threshold — proves "not 610s" but tolerates multi-second tick regressions (probe leg A closed this gap with a tight band incl. lower bound); (3) no lower bound in repo test (abort-before-threshold undetected — probe closed).

## 3. New families — independent runs + flake budget

| Pack | Command shape | Verbatim summary |
|---|---|---|
| watchdog | `timeout 300 uv run python -m pytest tests/unit/test_llm_stream_watchdog.py --tb=short -q` | `38 passed in 3.43s` |
| watchdog stall subset ×3 (`-k TestRealSocketStallAbort`) | same wrapper | `2 passed, 36 deselected in 2.08s` / `2.06s` / `2.09s` |
| items A/B | same wrapper | `19 passed in 0.18s` |

Flake verdict: **none** — 30ms (pytest) / 123ms (wall) swings across 3 runs; no pass/fail flips. items_ab is pure unit (mocks + `caplog` + `inspect.getsource` pins; no sleeps/sockets) — 3× re-run correctly not applicable. Count derivation for 19: 16 test functions + `test_all_timeout_family_members_take_the_branch` parametrized ×4.

## 4. Touched-family regression (18-file pack)

`timeout 300 uv run python -m pytest <18 files> --tb=short -q -rf -p no:cacheprovider` → **`2 failed, 561 passed in 13.75s`**. The 2 failures are exactly the known pre-existing `test_llm_allowed_models_precedence.py` pair (`coding2` surplus shape) — see §6 adjudication. All 9 in-range-modified test files and all 8 classifier/retry/guard-family files green.

| Family | File | Status |
|---|---|---|
| P1 in-range | test_llm_failover.py, test_llm_request_gzip_edge_cases.py, test_symptom_repair_engine.py, test_symptom_repair_engine_failover_e2e.py, test_watcher_context_builder.py, test_watchover_decision.py, test_watchover_edge_cases.py, test_watchover_integration.py, test_watchover_phase5.py | PASS |
| P2 classifier/retry | test_llm_error_classifier.py, test_llm_failover_adversarial.py, test_graph_retry_integration.py, test_malformed_llm_response_guard.py, test_symptom_repair_ladder.py, test_empty_response_guard.py, test_compaction_empty_guard_fallback.py, services/test_title_generation_empty_guard.py | PASS |

Gzip pin: `git diff b5100215..752da6b5 -- uv.lock` EMPTY; uv.lock `httpx==0.28.1` → `tests/unit/test_llm_request_gzip.py` `26 passed in 4.33s`.

## 5. Static gates

| Check | Evidence | Verdict |
|---|---|---|
| `stream_stall_threshold_seconds` floor | `daemon/config.py:174-186` `Field(default=45, ge=10)`; live repro: value 5 → pydantic `ValidationError` (greater_than_equal), value 10 → accepted, default → 45 | PASS |
| No new `ENSEMBLE_*` flags | `git diff b5100215..752da6b5 -- daemon/ \| grep -E '^\+.*ENSEMBLE_'` → no hits (tuning knob via pydantic env-prefix on `LLMConfig`, not a flag) | PASS |
| No new env reads | same diff `\| grep -E '^\+.*(os\.environ\|getenv)'` → **zero hits** | PASS |
| Fake drift (item 7) | 7 fake files gained `default_request_timeout = 610`; spot-check ×2 (`_StubClient`, `_FakeChatClient`) MIRROR real semantics: `ThinkingChatOpenAI.default_request_timeout: ClassVar[int] = 610` (`graph.py:3368`), inject-if-absent in `clean_llm_config` (`graph.py:3773-3774`, explicit `None` preserved — pinned by test), startup wiring both entry points (`api.py:291`, `__main__.py:256`). Attr is load-bearing (patched fakes without it raise AttributeError). `test_llm_failover.py:1342-1355` uses the documented `http_client` opt-out — correct, not drift | PASS |
| Watchdog composition | `make_watchdog_httpx_client` (:583): `httpx.Client(transport=WatchdogHTTPTransport(inner))` with `inner = GzipRequestTransport(httpx.HTTPTransport())` when gzip ON — **watchdog OUTERMOST confirmed**; partial-override contract unchanged (either `http_client`/`http_async_client` key opts out) | PASS |
| Item A log-truth | classifier explicit `except httpx.TimeoutException` branch (:1030) precedes catch-all, log-wording only, re-raises unchanged; `agent_node` catch tuple gains `httpx.TimeoutException` (`graph.py:7469-7473`); `issubclass(httpx.TimeoutException, TIMEOUT_EXCEPTIONS)` confirmed (member pre-existing at :483) | PASS |

## 6. Broader sweep + full adjudication (NEW = 0 proof)

9 partition packs (S1-S9), all `timeout 300 … -q -rf -p no:cacheprovider`, each pre-verified via `--collect-only` (zero-selected guard):

| Pack | Scope | Collected | Verbatim summary |
|---|---|---|---|
| S1 | `tests/unit/test_[a-f]*.py` | 1,770 | `23 failed, 1724 passed, 2 skipped, 8 warnings, 21 errors in 30.52s` |
| S2 | `tests/unit/test_[g-l]*.py` | 1,094 | `6 failed, 1088 passed, 19 warnings in 125.58s` |
| S3 | `tests/unit/test_[m-r]*.py` | 2,114 | `10 failed, 2064 passed, 40 skipped, 91 warnings in 76.27s` |
| S4 | `tests/unit/test_[s-z]*.py` | 1,257 | `5 failed, 1239 passed, 11 skipped, 28 warnings, 2 errors in 26.46s` |
| S5 | `tests/unit/services/test_[a-l]*.py` | 1,125 | `8 failed, 1117 passed, 21 warnings in 28.11s` |
| S6 | `tests/unit/services/test_[m-z]*.py` | 554 | `554 passed, 96 warnings in 11.66s` |
| S7 | `tests/unit/tools/test_[a-l]*.py` | 868 | `5 failed, 859 passed, 4 skipped, 50 warnings in 22.36s` |
| S8 | `tests/unit/tools/test_[m-z]*.py` | 1,748 | `1748 passed, 3 warnings in 12.03s` |
| S9 | 8 small subdirs (routers/job_queue/rag/checkpoint_adapter/graph/job_state/persistence/repositories) | 751 | `751 passed, 11 warnings in 23.32s` |
| **Total** | | **11,281** | **11,144 P / 57 F / 23 E / 57 S** (arithmetic cross-check exact) |

Adjudication: 3 base legs on detached worktree `agents-ensemble-baseverify-b5100215` @ `b5100215` (HEAD verified, clean, `.env` parity SHA-matched, `uv.lock` SHA-identical, `daemon.__file__` resolution-proven worktree-isolated):

| Leg | Files | Base result | Verdict |
|---|---|---|---|
| 1 | test_llm_allowed_models_precedence.py | `2 failed, 25 passed in 0.65s` — node ids ≡ branch leg | 2/2 PRE-EXISTING-IDENTICAL |
| 2 | services/test_job_queue_proxy_phase1.py | `7 failed, 11 passed in 0.59s` — node ids ≡ branch leg | 7/7 PRE-EXISTING-IDENTICAL |
| 3 | 18 files (all remaining unknowns + 3 airtightness families) | `42 failed, 578 passed, 2 warnings, 23 errors in 4.84s` — failing/error sets ≡ branch-leg sets per family | 65/65 PRE-EXISTING-IDENTICAL |

**80/80 incident nodes adjudicated:** 74 base A/B-identical + 5 `TestAccessMemoryArchive` (triple-attributed pre-branch, QUARANTINE.md) + 1 `test_b1_wc_durable_send` hardcoded-worktree env defect (active QUARANTINE row). **Branch-caused: 0. Divergent: 0.**

Pre-existing red families at base `b5100215` (documented, consolidated QUARANTINE row added): find_near 2-tuple mocks ×13; builtin_mcp `slash_commands` mock-gap ×17; context7/webfetch `blueprint` mock-gap ×6; job_queue_proxy_phase1 instance-derived-status ×7 (JobItem mirror 'pending' overrides Instance.status; canonical map missing `error→failed`, `waiting_children→processing`); job_processor_status_guard `complete_job` 0-calls ×4; coder_developer_migration ×5; devops meta drift ×3; paused_auto_resume MagicMock-await ×5; api_module_size 2489>1600 ×1; models_split LivezResponse ×1; release-tag pin v0.12.4≠v0.12.10 ×1; phase4 `cascade_to_root` kwarg-pin ×1; project_manager prompt cross-refs ×2; terminal_reason MIRROR_SET ×1; validate_agent_id `get_registry` ×1; vision AsyncMock ×1; wanderer tools_allow ×2; coder_agent prompt ×1; allowed_models coding2 ×2; access_memory ×5; b1_wc ×1.

## 7. ensure.md Core validation

| Requirement | Validation | Result |
|---|---|---|
| No regressions in changed packs | §3/§4/§6 packs (scoped to LLM/stream/classifier/config change set) | **PASS** (0 branch-caused suite-wide) |
| Deadlock/concurrency integrity + no sync DB on event loop | registered pack `test/packs/concurrency_atomic_unit_test.sh` via `timeout 300 bash` | **PASS** — `98 passed, 74 skipped, 44 warnings in 7.32s`, `RESULT: PASS` (matches canonical figure) |
| `dev.sh --timeout-graceful-shutdown 10` | grep `dev.sh:102` | **PASS** (present) |
| Important: await discipline (static) | no changed callers in range (diff touches LLM transport/classifier/config only) | **PASS by scope** (out of blast radius) |

Release Gate: NOT triggered — LLM-transport-scoped change, not cross-module architecture (mission scoped to unit sweep; no E2E/integration requested). ensure.md Improvement Notices: none this gate (all methods compatible with pack discipline).

## 8. Scope Decision

Mission-defined scope (leader dispatch): new families + touched families + broader unit sweep + static gates + base adjudication. Honored in full; `tests/integration`+`tests/e2e` intentionally not run (not requested; unit-sweep gate). Sweep sized from forensics counts (11,281) into 9 packs, all ≤ 5-min cap (slowest: S2 at 125.58s).

## 9. Follow-ups (leader-routing; none gate-blocking)

1. 🟠 **Test debt (pre-existing, 80 nodes):** consolidated QUARANTINE row added 2026-09-14; largest coherent fixables: job_queue_proxy_phase1 ×7 (real production deficit candidate — Instance-status canonical map missing entries + mirror override), mock-gap families (slash_commands ×17 / blueprint ×6 @ `manager.py:1138`/`:1045`), find_near ×13 (2-tuple mocks), MagicMock-await ×5+1.
2. 🟢 Watchdog repo-test tightening: add lower-bound timing assertion + tighten `elapsed < 8.0` toward threshold; import-level lifespan wiring pin (currently grep-pinned).
3. 🟢 Activation: daemon restart required post-merge (watchdog thread + ClassVar wiring boot at lifespan). Post-restart soak anchors: watchdog thread boot line in api lifespan; `[StreamWatchdog] stream stalled …` only on real incidents; `stream_stall_threshold_seconds` effective value in boot log if logged.
4. 🟢 Probe retention: `/tmp/marquee_probe_llm_stream_stall.py` is throwaway; the tight-band (lower+upper bound) pattern from leg A is the pattern to adopt in the repo test (follow-up 2).

## 10. Dispatch & evidence ledger

W1 forensics `d40ce3f0` · W2 watchdog pack `c978b921` · W3 items_ab `439261b0` · W4 gzip `30019a97` · W5 marquee `f3526218` · W6 touched pack `eaf2e008` · W7 concurrency `c23e5b0c` · W8 base legs ×3 `29366f56` · S1-S9 `007d44f4`/`0dce930b`/`ca2771db`/`5c51b452`/`ee74a634`/`c2193e31`/`530924ba`/`c17d0cd8`/`84b9e96b`. All runs `uv run python -m pytest` from worktree root; `timeout 300` command-level on every invocation; pyproject per-test timeout as inner layer; report-only legs (no fixes/commits by workers). Base adjudication worktree `agents-ensemble-baseverify-b5100215` (clean, detached @ b5100215) — removed after gate.
