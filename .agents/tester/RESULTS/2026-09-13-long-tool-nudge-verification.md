# Long Tool Call Nudge — Final Merge Gate Verification

**Date:** 2026-09-13
**Target:** `feature/long-tool-call-nudge` @ `eb1c4a94` (base `0acd3afa`, 11 commits)
**Worktree:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-ltn`
**Gate class:** Release gate (final pre-merge verification) — full-suite census warranted per ensure.md release-gate class.
**Tester instance:** this agent (Test Leader); execution delegated to 4 workers + 1 follow-up (IDs in §Workers).
**Tree pins:** every worker recorded timestamped `rev-parse` + `status --porcelain` at START and END; all pins clean, HEAD `eb1c4a94944fde225142978592f36ecd10807ed3`, zero tracked-file drift across the whole gate (16:14:32Z → 16:33:11Z observed window).

---

## Executive Summary

| Item | Verdict |
|---|---|
| 1. Full-suite census regression (dual-leg A/B) | ✅ **PASS — GATE-CLEAN (NEW = 0)** |
| 2. Incident-fidelity replay (U2B + belt trio + async-attach trio) | ✅ **PASS** (mock-shape audit: production async bound-method shape mirrored; no 0-nudge blind spot) |
| 3. End-to-end delivery (integration-tier, real seams) | ✅ **PASS** (PAUSED / terminal ×4 / parentless, real `enqueue_message` over real SQLite `MessageQueue`) |
| 4. Kill-switch (`LONG_TOOL_NUDGE_ENABLED=0`) + boot resolution | ✅ **PASS** (boot resolution evidence: §4.3) |
| 5. Threshold contract | ✅ **PASS** (full precedence chain + strict `>` + floor 60 + bounds 60–1800) |
| 6. Lifespan smoke (in-process boot, 4 scenarios) | ✅ **PASS** 4/4 |
| 7. Restart boundary (I3 pins) | ✅ **PASS** — pins are REAL-BOUNDARY, not tautologies |

**Overall recommendation: SHIP** — zero branch-caused failures, zero 🔴/🟠 blocking findings. Seven 🟡 informational findings (§Findings), none merge-blocking.

---

## Workers

| Worker | Instance ID | Scope |
|---|---|---|
| ltn-census | `beac877f` | Item 1 dual-leg census (no load_skill; census infrastructure run) |
| ltn-family | `5e0b3ceb` | Items 2/4/5 family evidence pack (`test-pack-execution`) + config-file follow-up pack |
| ltn-integ | `8d758be5` | Items 3/7 (`integration-test`) |
| ltn-lifespan | `dc3a9d8e` | Item 6 (`e2e-test`) |

All runs: `uv run python -m pytest` from worktree root only (bare `pytest` is a broken foreign install — repo convention (l)). No repo files modified by any worker; no commits until this RESULTS file (path-scoped).

---

## Item 1 — Full-Suite Census Regression (dual-leg A/B)

**Method:** identical invocation at branch (`eb1c4a94`, feature worktree) and at base (`0acd3afa`, detached `/tmp` worktree `baseleg`, `uv sync` + same command), then set-diff of failing nodeids. This is stronger than doc-diffing: the baseline is measured, not remembered.

```
timeout 3600 uv run python -m pytest tests/unit -q -rEf --tb=no
```

### Per-leg counts

| Leg | HEAD | Passed | Failed | Errors | Skipped | Wall | Exit |
|-----|------|--------|--------|--------|---------|------|------|
| Branch | `eb1c4a94` | **10,760** | **108** | **23** | 57 | 334.56s | 1 |
| Base | `0acd3afa` | 10,604 | 109 | 23 | 57 | 340.10s | 1 |

Branch delta: **+156 passing, −1 failing, ±0 errors** — composition: 154 new unit-family tests (12 new test files + 1 helper, all passing) + 1 healed test + 1 micro-delta consistent with a skip-composition swap (skipped total unchanged at 57; failing set at branch is a strict subset of base's, so no regression can hide in the delta). New files: `tests/unit/test_long_tool_nudge.py`, `tests/unit/test_long_tool_nudge_config.py`, `tests/unit/services/test_long_tool_nudge_{belt,detector,hand_off,loop,observability,registry,resolve,threshold,wrapper}.py`, `tests/unit/tools/test_set_instance_tunable.py`, `tests/helpers/long_tool_nudge.py`.

### Failing-set diff (the gate criterion)

```
common  = 131 nodeids (fail/error in BOTH legs)
NEW     = 0     (branch-only failing names — gate criterion)   → GATE-CLEAN
HEALED  = 1     (base-only failing name — informational)
```

**NEW names (verbatim):** — *(empty list; `/tmp/ltn-census.7Tvg/new.names` is 0 bytes)*

**HEALED name (verbatim):**
```
tests/unit/test_filesystem_workdir.py::TestIsWithinWorkdir::test_dotdot_traversal_blocked
```
(the branch's environment makes the dotdot-traversal guard assertion pass where it failed at base — a net improvement, not a regression).

Flake adjudication: vacuous — NEW = 0, nothing required a solo re-run. No port/DB interference between sibling workers; no daemon booted; `git worktree list` confirms the shared worktree untouched.

### Family mapping of the 131 in-common failing nodes — 100% baseline/quarantine

| Family | Nodes | Attribution |
|---|---:|---|
| Watchover mock cascade | 47 | QUARANTINE row (re-verified 2026-08-30 @ `7a484afb`) + 09-12 charter-reuse P-7 |
| Mock-gap setup errors (builtin MCP: slash_commands ×17, context7 ×4, webfetch ×2) | 23 (all E) | QUARANTINE InstanceManager blueprint-fixture family — the "23E MCP-bootstrap error class" |
| `find_near_instance` stale 2-tuple mocks | 13 | 09-12 charter-reuse P-5 (stale vs 3-tuple `repo.list()`, prod correct) |
| `job_queue_proxy_phase1` map-gap | 7 | LCA-gate 2026-09-06 addendum |
| TestAccessMemoryArchive | 5 | QUARANTINE (2026-08-20) |
| `coder_developer_migration` stale mocks | 5 | 09-12 charter-reuse / hoisting-gate §4b |
| `paused_auto_resume_fallback` MagicMock-await | 5 | LCA-gate 2026-09-06 addendum |
| `job_processor_status_guard` | 4 | stale-contract singles (`5e4b867a`) |
| `devops_agent` meta-drift | 3 | W1-gate 2026-09-09 |
| `llm_allowed_models_precedence` | 2 | stale-contract singles (`061bba10`) |
| frozen-tool-name / tool-config-boot | 2 | stale-contract singles |
| `test_project_manager_agent` | 2 | release-tag pin drift v0.12.5 |
| `test_wanderer_agent` tools_allow | 2 | W1-gate |
| terminal_reason_mirror / validate_agent_id / test_vision / test_coder_agent / api-router-extraction / phase4_manager_decomposition / test_b1_wc_durable_send / TestAutoTestRagNotConfigured / test_mcp_tool_timeout / test_maintenancer_kb_coverage / test_models_split | 1 each | documented singles/quarantine rows (see census worker report) |
| **TOTAL** | **131** | **100% baseline census or QUARANTINE.md** |

### Reconciliation vs 09-12 census docs

- **charter-reuse 09-12** (~18,774P / 216F+63E full sweep): this gate's `tests/unit` 131 F+E nodes are a member-for-member subset of that base-attributed set.
- **empty-response-guard 09-12** (17,547P / 213F / 33E): the 10E delta vs this gate is the `/api`, `/integration`, `/postgres`, `/opencode` setup-error families outside `tests/unit` (settings_api ×12, vscode C1/routing/security, …) — all pre-existing per QUARANTINE rows 33/37/40/41. The 23E unit subset matches that doc's P-4 mock-gap errors row exactly.
- Branch-leg counts (108F / 23E) land exactly on the leader's stated census class ("108F baseline class + quarantined list").

**Item 1 verdict: ✅ PASS — zero new failing names at branch vs measured base leg.**

---

## Item 2 — Incident-Fidelity Replay

### 2a. `test_u2b_wedge_episode_5_sequential_long_calls_one_nudge` (`tests/unit/test_long_tool_nudge.py:175`) — ✅ PASS

Ran -v (0.11s). Assertion audit against the incident shape (coder `bd4b36ef`: 5 sequential long bash calls, loop-breaker blind):

| Required element | Evidence | Status |
|---|---|---|
| 5 DISTINCT sequential `tool_call_id`s | `:182-186` loop `for i in range(1,6): call_id = f"call-long-{i}"` — mechanically distinct; no explicit set-size assert | pinned (structurally; see finding F-2) |
| Inter-batch gaps | indirectly pinned — a gap-discard regression would break `enqueue_message.await_count == 1` (`:198`) | pinned (indirectly; see F-2) |
| NO intervening healthy completion | `:188` every stamp aged `started_at = monotonic() - 1200` (>900s); no fast stamp inserted | explicit |
| `close_episode` called ZERO times | `:177` handler appends to `closes`; `:197` `assert closes == []` | explicit |
| EXACTLY 1 nudge to parent | `:198` `assert manager.enqueue_message.await_count == 1`; `:199` 5th crossing suppressed (`stats["fired"] == 0`); `:200` episode survives in `_active_episodes` | explicit |

### 2b. Belt trio (`tests/unit/services/test_long_tool_nudge_belt.py`) — ✅ PASS (3/3, 0.12s)

- **survives-gap** — `test_orphan_active_episode_survives_inter_batch_gap` → `:161-162` episode survives + `orphan_episodes_discarded == 0`.
- **discarded-after-TTL** — `test_orphan_active_episode_discarded_after_ttl` → `:186-188` episode gone + `orphan_episodes_discarded == 1`.
- **anchor-refresh** — `test_episode_anchor_refreshed_when_child_has_live_stamps` → `:226-228` `_episode_last_seen == approx(fake_clock.t)`.

### 2c. Async-attach trio (`tests/unit/services/test_long_tool_nudge_wrapper.py::TestWrappedToolsNodeAsyncParentLookupAttach`) — ✅ PASS (4/4, 0.20s)

**Mock-shape audit (the prior-cycle 0-nudge blind spot):**
- Production: `daemon/services/long_tool_nudge.py:998` `async def read_parent_id(self, child_id) -> Optional[str]` (ASYNC BOUND METHOD); dispatcher `:513-514` `if inspect.iscoroutinefunction(lookup): parent_id = await lookup(child_id)`; attach site `daemon/api.py:830-831` `LONG_TOOL_REGISTRY.attach_parent_lookup(long_tool_nudge_scanner.read_parent_id)`.
- Tests: all three mocks are `async def` free functions with `(child_id: str) -> Optional[str]` (`:461-462`, `:498-499`, `:526-527`) — `iscoroutinefunction` returns True, production's await branch triggers. Decisive regression pin `:475-478`: `assert isinstance(parent_id, str), "parent_id must be a plain string (async-attach shape)…"`.
- **Conclusion: production shape mirrored; no sync-lambda mask.** A sync-lambda regression would fail the isinstance pin.

**Item 2 verdict: ✅ PASS.**

---

## Item 3 — End-to-End Delivery (integration-tier)

**Production seam map (file:line):** `daemon/graph.py:7893` (wrapped tools node, registry=LONG_TOOL_REGISTRY) → `long_tool_nudge.py:307` `record_start` stamps every `tool_call_id` on batch entry → `daemon/api.py:791-810` lifespan wires scanner → `:824-832` attaches close/resolve/read_parent_id → `:834-840` loop task (60s tick) → `long_tool_nudge.py:1231-1340` `run_once` → `:1033-1207` `deliver_long_tool_nudge`: pre-read parent → status gate → **`enqueue_message(source=LONG_TOOL_NUDGE_SOURCE="system:long-tool-nudge", priority=0)` at `:1142-1148`** (source literal confirmed `:167`) → best-effort `worker_pool.notify_work()` at `:1180-1198` (A5 double-notify defense-in-depth, pinned by `test_u8/u9/u10`).

| Sub-case | Verdict | Evidence (strongest first) |
|---|---|---|
| 3a PAUSED parent: skip + no delivery + retries next tick + fires after resume | ✅ PASS (REAL) | `tests/integration/test_long_tool_nudge_e2e.py:375 test_i4_paused_parent_no_nudge` (real `InstanceMessagingService` over real in-memory SQLite MessageQueue): `assert _messages_for(engine, parent) == []` + no status flip. Ad-hoc real-seam script ×3 runs: tick1/tick2 `fired=0 rows=0`, tick3 (post-resume) `fired=1 rows=1 source='system:long-tool-nudge' priority=0`. Unit MOCKs: `test_u5_paused_parent_skipped`, `test_refused_then_retry_eligible_on_resume`. |
| 3b Terminal parent × {COMPLETED, TERMINATED, ERROR, FAILED}: skip + WARN + NO revive | ✅ PASS (REAL via ad-hoc) | `test_u5t_terminal_parent_skipped` (parametrized ×4, MOCK): `enqueue_message.assert_not_awaited()` each; WARN content asserted. Ad-hoc real-seam ×3 runs/status: `fired=0 rows=0` AND status stayed terminal (no RUNNING flip). Status gate `:1114` vs `TERMINAL_INSTANCE_STATUSES` (`daemon/constants.py:129`). |
| 3c Parentless: log-once-per-episode (not per-tick) | ✅ PASS (REAL via ad-hoc) | `TestParentNotFoundLogOncePerEpisode::test_warn_emitted_once_across_ticks` (`:698`): 1 WARN across two ticks; `:726` re-arms after `close_episode`. Ad-hoc real-seam ×3: exactly 1 WARNING, 0 MQ rows; line `[LongToolNudge] parent ... not found — nudge skipped`. Mechanism: `_missing_parent_warned` set keyed `(parent_id, child_id)` (`:953`), cleared in `close_episode` (`:1227`). |

Ad-hoc script: `/tmp/ltn-integ.HAHA/ltn_integration_seam.py` — real `LongToolNudgeScanner` → real `InstanceMessagingService.enqueue_message` over real in-memory SQLite; SIGALRM 120s self-timeout; no network ports; 3/3 identical runs.

**Item 3 verdict: ✅ PASS — every sub-case has at least one REAL-seam leg; MOCK-only residuals documented (F-5).**

---

## Item 4 — Kill-Switch (`LONG_TOOL_NUDGE_ENABLED=0`)

| Sub-item | Verdict | Evidence |
|---|---|---|
| 4.1 Scanner loop stops | ✅ PASS | `tests/unit/services/test_long_tool_nudge_loop.py:38 test_disabled_loop_returns_immediately` → `:50` `run_once.await_count == 0` + sleep-trap assert. |
| 4.2 `set_instance_tunable` → `FEATURE_DISABLED`, zero metadata writes | ✅ PASS | `tests/unit/tools/test_set_instance_tunable.py:103 test_disabled_returns_feature_disabled_and_writes_nothing` → `:107` `error_code == "FEATURE_DISABLED"`, **`:109` `manager.set_metadata_many.assert_not_called()`** (repo mock untouched — requested assertion present). |
| 4.3 `TOOL_COMPLETED` still emitted; stamps still recorded | ✅ PASS | `tests/unit/services/test_long_tool_nudge_observability.py:187 test_completed_log_emitted_when_kill_switch_off` → `:213-217` exactly 1 TOOL_COMPLETED record; `:226` `snapshot() == {}` proves record→clear lifecycle ran. |
| 4.4 Boot config resolution: default ON; invalid env → fail-fast | ✅ PASS | Default ON pinned by `tests/unit/test_long_tool_nudge_config.py::TestLongToolNudgeConfigDefaults::test_defaults` (`enabled is True`, `interval_seconds == 60`, `default_threshold_seconds == 900`). Invalid env: **live-boot proof** — lifespan smoke S3 below (`LONG_TOOL_NUDGE_ENABLED='maybe'` → `ValidationError` at `daemon/config.py:3226` inside `load_config()`, MRO-confirmed subclass of `ValueError`, boot aborts before wiring at `daemon/api.py:245`). Config-file unit pack (dedicated follow-up run, closes the pack-glob scope gap): `tests/unit/test_long_tool_nudge_config.py` — **7/7 PASS** (0.13s): default ON `enabled is True` + `interval_seconds == 60` + `default_threshold_seconds == 900` (`:27-31`); env overrides all three fields (`:35-42`); zero-interval fail-fast `pytest.raises(ValidationError)` (`:46-49`); above-max-threshold fail-fast (`:51-54`); hard-max 1800 inclusive (`:56-59`); constants identity `HARD_MAX=1800/MIN=60/STALE_TTL=7200` (`:65-70`); top-level `Config.model_fields` wiring (`:72-77`). Exception-type verdict: `issubclass(ValidationError, ValueError) == True` (pydantic 2.12.5, MRO verified in-venv) — leader's "ValueError at boot" wording literally satisfied. Specificity gap: no unit pin for an INVALID `LONG_TOOL_NUDGE_ENABLED` value (only the valid OFF path `"false"` is pinned, `:36/:40`) — the invalid-value case is proven by the S3 live boot instead; unit-pin gap recorded as F-8. Family count reconciliation: 147 (pack glob) + 7 (config) = **154 unit-family tests**; + 8 `tests/integration/test_long_tool_nudge_e2e.py` = 162 total vs the task's "~165" estimate (estimate tolerance). |

**Item 4 verdict: ✅ PASS.**

---

## Item 5 — Threshold Contract

Verdict: ✅ **PASS** — 20/20 threshold-family tests (0.14s). Chain links with real assertions:

- **Per-child metadata rung:** `test_long_tool_nudge_threshold.py:41` metadata 1200 honored; metadata `0`/`None`/`"900"`-string/`True`-bool all fall back (`:49,:53,:59,:64`) — bool-trap covered.
- **Env default 900:** asserted at `:49/53/59/64/89/97/101/105/109`.
- **Clamp `min(·,1800)`:** `:45` metadata 3600 → capped at `HARD_MAX_THRESHOLD_SECONDS` (1800).
- **STRICT `>` firing:** `test_long_tool_nudge_detector.py:93` age 901 → `fired == 1`; `:101-102` age 900 → `fired == 0`; `:110` age 899 → `fired == 0`.
- **Floor 60, scanner side (hand-edited <60 → treated as default):** `:89` metadata 30 → resolves 900; `:93` metadata 60 → honored as 60.
- **`set_instance_tunable` bounds 60–1800, loud reject:** `test_set_instance_tunable.py:140-145` parametrized `[59, 0, -5, 1801, 18000]` → `pytest.raises(ValueError, match=r"must be in \[60, 1800\]")` + `set_metadata_many.assert_not_called()`.
- **Normalized error dicts `{"error","error_code"}` — all three return-dict branches:** `FEATURE_DISABLED` (`:107-109`), `UNKNOWN_KEY` (`:132-135`), `NOT_FOUND` (`:211-212`); type/range branches are the loud-raise variant (`:125`, `:145`) with `assert_not_called`. Residual: NOT_FOUND branch lacks an explicit `assert_not_called` (F-3, 🟡).

**Item 5 verdict: ✅ PASS.**

---

## Item 6 — Lifespan Smoke (in-process boot, real `daemon.api.lifespan`)

Method: `async with dapi.lifespan(app)` (the `test_v4_path2_daemon_api_lifespan_wiring` pattern), halted after the long-tool-nudge block via sentinel-raise in `JobQueueService.reconcile_terminal_watches` (shutdown path of the nudge block at `daemon/api.py:1349-1365` IS exercised — `__aexit__` runs after startup raises). DB: disposable PG14 on port 15432 (`ensemble_test`), boot discriminator `Creating PostgreSQL engine: 127.0.0.1:15432/ensemble_test`; prod `POSTGRES_*` NOT inherited (explicitly overridden per invocation).

| Scenario | Verdict | Evidence |
|---|---|---|
| S1 default boot | ✅ PASS (2.225s) | Boot line verbatim: `Long-tool-nudge scanner started: interval=60s, default_threshold=900s`; `app.state.long_tool_nudge_task` present, `isinstance(asyncio.Task)`, name `long-tool-nudge`; shutdown: `task.done()`, `exception() is None` — clean return (`CancelledError` swallowed at `long_tool_nudge.py:1467-1468`), no leak. |
| S2 `LONG_TOOL_NUDGE_ENABLED=0` | ✅ PASS (1.946s) | Boot line verbatim: `Long-tool-nudge scanner disabled by config`; no start line; `app.state.long_tool_nudge_task is None` (set at `daemon/api.py:849`). |
| S3 invalid env `'maybe'` | ✅ PASS (0.940s) | `ValidationError` (MRO-confirmed ⊂ `ValueError`) at `daemon/config.py:3226` in `load_config()`, raised from `daemon/api.py:245` BEFORE wiring — fail-fast boot abort. Traceback: `Input should be a valid boolean … [type=bool_parsing, input_value='maybe']`. |
| S4 no-double-start | ✅ PASS (1.909s) | Exactly ONE scanner task across lifespan. Guard = lifespan-once + `reload=False` hardcode (`daemon/__main__.py:314`); no explicit "already started" guard exists — design-level reliance documented. No test pin for a reload double-start (design assumption; noted in F-6). |

**Item 6 verdict: ✅ PASS 4/4.**

---

## Item 7 — Restart Boundary (I3 pins)

**Production persistence model (audited):** stamps/`_fired_episodes`/`_active_episodes` are RAM-only, die with the process (AD-14, `long_tool_nudge.py:100-109`); registry is a module singleton re-instantiated per import (`:566`); scanner + dedup state fresh per lifespan (`api.py:797-810`); threshold overrides persist in instance metadata and are re-read every tick (`tunables.py:42-58`, `resolve_threshold` `:971-996`); the nudge itself is durable the instant `enqueue_message` returns (AD-15).

| Pin | Verdict |
|---|---|
| `test_i3_daemon_restart_resets_dedup` (`e2e:251`) | **REAL-BOUNDARY** — fresh scanner `b` (`:272-274`); asserts duplicate nudge post-restart accepted (`:278` `len(rows) == 2`). Registry-shared fixture is a simulation artifact (prod registry is fresher → guarantee still ≤1). |
| `test_i3_restart_no_stamps_no_nudges` (`e2e:281`) | **GOLD STANDARD** — exact production cold-restart shape (empty registry, no stamps): `fired == 0`, `instances_scanned == 0`, zero MQ rows (`:309-312`). |
| `test_i3_restart_one_duplicate_upper_bound` (`e2e:315`) | **REAL-BOUNDARY** — fresh scanner + one post-restart stamp → exactly 2 rows (1 pre + 1 dup) (`:362-365`), then 3 follow-up ticks hold at 2 (no phantom). The ≤1 bound is enforced in production by nudge-level dedup (`_active_episodes` gate, `:1123`), which is the mechanism the pin exercises. |

Real-world restart reasoning: post-restart scanner has empty `_active_episodes`; in-flight calls were cancelled by restart; a fresh `tool_start` crosses threshold → fires once → 1 new nudge max per `(parent, child)`. **Not tautological — the pins reconstruct post-restart state and the dedup mechanism is the real one.**

**Item 7 verdict: ✅ PASS.**

---

## Findings Register (defects / coverage gaps)

| # | Severity | Location | Finding |
|---|---|---|---|
| F-1 | 🟠 → resolved | `tests/unit/test_long_tool_nudge_config.py` | Boot-config tests initially outside the family pack glob (dispatcher glob missed the top-level file). **Closed by dedicated config-pack run** — §4.4 / <<CONFIG-PACK-PENDING>>. Not a code defect; a pack-scoping correction. |
| F-2 | 🟡 | `tests/unit/test_long_tool_nudge.py:182-200` | U2B: "5 distinct ids" and "inter-batch gaps" are structurally/implicitly pinned, not explicit asserts (a call-id reuse or dropped-tick regression could pass). Recommend an explicit `len({call_ids}) == 5` + gap-presence assert in a follow-up. Non-blocking: the decisive `await_count == 1` + `closes == []` pins hold. |
| F-3 | 🟡 | `tests/unit/tools/test_set_instance_tunable.py:206-212` | NOT_FOUND branch missing explicit `set_metadata_many.assert_not_called()` (no-write invariant implicit only). One-line follow-up. |
| F-4 | 🟡 | `daemon/api.py:1358` | Shutdown `await long_tool_nudge_task` can delay graceful shutdown by up to one interval (60s default) if mid-tick. Bounded, intentional, same pattern as `waiting_children_watchdog` / `EligiblePendingSweepService`. Awareness only. |
| F-5 | 🟡 | `test_u5_paused_parent_skipped` / `test_u5t_terminal_parent_skipped` | MOCK-only skip unit tests; real-DB legs exist via `test_i4` (PAUSED) and the ad-hoc real-seam script (terminal ×4). Residual: no permanent repo-resident real-DB terminal-skip integration test. Non-blocking. |
| F-6 | 🟡 | `daemon/api.py:833-841` / `daemon/__main__.py:314` | No-double-start rests on lifespan-once + `reload=False`; no dedicated test pin for a reload-shaped double entry. Design is sound under current runner; documented assumption. |
| F-7 | 🟡 | `tests/integration/test_long_tool_nudge_e2e.py:251,:315` | Registry fixture shared across scanner a/b in restart pins (simulation artifact; assertion truth unaffected). Suggest fixture rename/comment for clarity. |
| F-8 | 🟡 | `tests/unit/test_long_tool_nudge_config.py` | No unit pin for an INVALID `LONG_TOOL_NUDGE_ENABLED` value (only valid OFF `"false"` pinned). Invalid-value fail-fast is proven by the S3 live boot (`ValidationError ⊂ ValueError` at `daemon/config.py:3226`); suggest a one-test unit pin (`enabled="maybe"` → `pytest.raises(ValidationError)`) for fast regression coverage. |

No 🔴 critical, no 🟠 blocking findings remain.

---

## Quarantine / Skips

- 57 skipped on both legs (unchanged base→branch) — includes QUARANTINE-deselected families (e.g. TestAccessMemoryArchive via pack scripts).
- Quarantined pre-existing failures (task-note list) all re-observed in the baseline set, none attributed to this branch: TestAutoTestRagNotConfigured (ordering flake), TestWatchoverEvaluatorEvaluate (rotating flake, inside Watchover cascade family), TestAccessMemoryArchive ×5, `test_b1_wc_durable_send` (hardcoded-path grep), 23E MCP-bootstrap class.

## Scope Decision

Full-suite census run — warranted: final merge gate (release-gate class), leader-mandated. Executed as measured dual-leg A/B rather than remembered-doc diff; all targeted verification scoped to the feature family + wiring sites.

---

## Artifacts

- Census legs: `/tmp/ltn-census.7Tvg/{branch.out,base.out,branch.names,base.names,new.names,healed.names,*.summary,*-test-files.list}`
- Real-seam integration script: `/tmp/ltn-integ.HAHA/ltn_integration_seam.py`
- Lifespan smoke: disposable PG14 (port 15432) — stopped and removed; scratch `/tmp/ltn-life.Q7Xx/` removed.
- Family/config pack runs: worker transcripts in instance `5e0b3ceb`.

*(Scratch paths are ephemeral /tmp artifacts; the durable record is this file.)*

---

## Overall

- Item 1: ✅ PASS (NEW = 0, measured base leg)
- Items 2–7: ✅ PASS (details above)
- Blocking findings: **0**
- **Recommendation: SHIP**

Path-scoped commit of this file follows on `feature/long-tool-call-nudge` (commit sha appended below after commit).

Commit: <<COMMIT-SHA-PENDING>>
