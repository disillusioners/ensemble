# Phase 1: Reuse Core in `chart_tools.py`

## Objective

Deliver the working reuse path inside `daemon/tools/chart_tools.py` ONLY: discovery of the caller's prior charter, a chart_tools-local register/enqueue/wait helper that rides the service-side revive, the backward-compatible `fresh` kwarg, the ERROR/FAILED revive policy, the concurrent-reuse busy guard, the mode log line — plus unit tests in `tests/test_chart_tools.py` proving every requirement-1/2/4 behavior while keeping ALL existing pins green.

File targets (all verified on branch `feature/generate-chart-charter-reuse`):
- `daemon/tools/chart_tools.py` (158 lines today): signature `:59-63`, docstring `:65-87`, message build `:91-96`, invoke `:101-110`, error collapse `:117-119`, `_full_doc_` `:121-156`, module logger already exists `:21`, imports `:11-22`.
- `daemon/constants.py:373-377` — source-prefix doc block (one added line, M7).
- `tests/test_chart_tools.py` (221 lines): fixture `_make_manager()` `:29-41`; legacy invocation lanes `TestGenerateChartInvocation :105-221` (message pins `:150-153`).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Extend `_make_manager()` fixture with deterministic stubs | none | All legacy lanes pass unmodified; discovery is deterministic |
| 2 | T1: `_find_reusable_charter()` discovery helper (SOLE store — adjudicated P2) | 1 | Returns latest charter child or None; deterministic ordering; unit-testable |
| 3 | ~~T2: SharedMetaKV pointer~~ — **DELETED (adjudicated P2)** | — | See tombstone below |
| 4 | T3: `_reuse_charter()` register→enqueue→wait→unregister helper | 2 | Same instance id revived; tuple semantics; no orphan-terminate; no semaphore |
| 5 | T4: `fresh: bool = False` kwarg + docstring + `_full_doc_` | 2 | fresh=True spawns new id; next discovery call finds it (latest `last_activity_at`); existing calls byte-identical |
| 6 | T5: busy-reject guard for concurrent reuse (check BEFORE register) | 4 | Second concurrent reuse call gets the busy error with NO register side-effect; no deadlock |
| 7 | T6: ERROR/FAILED one-revive counter | 4 | ERROR/FAILED consumes; 2nd failure respawns fresh; COMPLETED/TERMINATED free |
| 8 | T7: single-line mode log | 4 | Every call emits exactly one greppable line |
| 9 | T8: `TestGenerateChartReuse` unit tests | 1–8 | All new + legacy tests green via `uv run python -m pytest` |
| C1 | CONDITIONAL (dormant): message refinement note (one trailing line) | P6=(b) post-merge A/B only | New pin added; legacy pins :150-153 still pass (substring-only) |

> **T2 tombstone (adjudication record):** the original T2 (SharedMetaKV pointer read/write-through with DB-wins cross-check) was DELETED by the architect's P2 ruling (2026-09-10): pure query-discovery is the sole store — no KV row, no read, no write-through, no cross-check, no self-heal code. Decisive rationale: the ambient-KV render (merge 02cf770a, default ON) would surface the pointer row in every caller turn; the KV saves only one indexed lookup (N≈1; the cross-check re-queries anyway); the staleness class disappears; restart-safe by construction. T1 below is the complete tracking mechanism.

### Task 1 (fixture) — `tests/test_chart_tools.py:29-41`

Extend `_make_manager()` with:
- `manager._instance_repository.get_children = MagicMock(return_value=[])` — keeps discovery empty and deterministic (a bare MagicMock auto-attribute would make `get_children(...)` return a truthy MagicMock; its magic-`__iter__` default is empty, but relying on that is fragile).
- `manager.enqueue_message = AsyncMock(return_value=MagicMock())` — needed once the reuse path exists; the fresh path never touches it.
- NO `shared_meta_kv_repo` stubs — adjudicated P2=(a) removed the KV pointer entirely; the repo is never touched by the reuse path.
- Acceptance: `uv run python -m pytest tests/test_chart_tools.py -q` — every legacy test passes WITHOUT body edits; `git diff` shows only the fixture hunk (+ this phase's additions later).
- **Gates:** P9 ADJUDICATED CONFIRMED (M3 shape, KV stubs dropped per P2 flip). If P9 had flipped to a separate fixture factory, only this hunk would move.

### T1 — discovery helper (`daemon/tools/chart_tools.py`, new private function) — THE SOLE STORE (adjudicated P2)

- `_find_reusable_charter(manager, caller_id: str) -> Instance | None`:
  - rows = `manager._instance_repository.get_children(caller_id)` (repository walk `daemon/repositories/instance/repository.py:424-429`; permanent across revive).
  - **Determinism spec (W2 — implementation-mandatory):** `get_children` has NO `ORDER BY` (plain `parent_id ==` select) and `last_activity_at` is NULLABLE (`models.py:72`) — ordering MUST be done Python-side, never relied upon from SQL. Sort key: `last_activity_at` desc with **NULL treated as oldest** (a NULL-activity charter never silently wins and never causes silent fresh-spawn degradation), then tie-break `created_at` desc, then row id — total, deterministic order. Return first or None.
  - filter: `row.agent_id == "charter"` AND `(row.instance_metadata or {}).get("invoked_as_tool")` (flag stored at `instance_lifecycle.py:1798-1799`).
  - wrap in try/except → None on repository error (discovery failure degrades to fresh spawn, never raises to the LLM; pinned by T8.12).
- `_resolve_scope_key()` seam: trivial under adjudicated P3=(a) — the "key" IS `current_instance_id` as the `get_children` argument; keep or drop at implementer's discretion (architect note).
- Acceptance: pure-function test with fabricated rows (SimpleNamespace with `agent_id`/`instance_metadata`/`last_activity_at`/`status`) picks the latest charter by `last_activity_at` (tie-break `created_at`, then id) and ignores non-charter / non-tool children.
- **Gates:** P2 ADJUDICATED = (a) pure query-discovery — T1 IS the store (former pointer/cross-check layer deleted). P3 ADJUDICATED = (a) per-caller-instance.

### T3 — reuse wait helper (`daemon/tools/chart_tools.py`, new private coroutine)

> P1 ADJUDICATED CONFIRMED (a): this block lives chart_tools-locally — `invoke_agent_and_wait` is NOT extended, no pre-emptive shared-helper extraction.

- `_reuse_charter(manager, charter_id, message, caller_id, pid, timeout=600.0) -> tuple[str, str]`:
  1. Status pre-check via `manager._instance_repository.get(charter_id)`: `COMPLETED|TERMINATED` → proceed (free); `ERROR|FAILED` → consult T6 counter; `RUNNING` → busy error (T5); `PAUSED` → busy-reject with a paused-specific message (enqueue would sit PENDING until resume and the 600s wait would burn — MADE M14).
  2. `registry = get_completion_registry()` — lazy import identical to `daemon/utils.py:641`; register BEFORE enqueue (buffered completion covers the race, `utils.py:672-674`).
  3. `await manager.enqueue_message(instance_id=charter_id, message=message, source=f"internal_chart_reuse:{caller_id}", metadata={"chart_reuse": True})` — service-side revive flips terminal→RUNNING and reloads the checkpoint (`instance_messaging.py:1931-1966`); ONLY existing kwargs (no `context`/`load_skill` exists at this layer, `:2070-2083`).
  4. Re-register if consumed (`if not registry.is_registered(charter_id): registry.register(charter_id)` — mirrors `utils.py:683-685`).
  5. `result = await registry.wait_for(charter_id, timeout=timeout)` (`:688`).
  6. Mapping: `None` → return `("Error: Charter timed out after {timeout}s...", charter_id)` and **do NOT terminate** (M8 — the instance is shared/durable; `_try_terminate_orphan` is deliberately NOT mirrored here); `result.is_error` → `("Error: Agent failed. {result.content}", charter_id)`; success → `(result.content or "", charter_id)`.
  7. `finally: registry.unregister(charter_id)` (mirrors `utils.py:735-739` — analysis R6.3).
- NO `_invoke_semaphore` acquire on this path (it doesn't spawn; analysis R6.1 — no contention with `explore`/`explain_image`).
- Add the `internal_chart_reuse:` prefix doc line adjacent to `daemon/constants.py:375` (M7).
- Acceptance: unit test drives register→enqueue→complete via a stub registry; asserts same-id enqueue kwargs, tuple return, unregister in finally (incl. exception path), and that `manager.terminate_instance` is NEVER awaited on the reuse path.
- **Gates:** P1 ADJUDICATED CONFIRMED (a) — the block stays chart_tools-local (mirror `utils.py:672-740` exactly); M8 no-terminate policy stands per adjudicated P5.

### T4 — `fresh` kwarg (backward-compatible signature change)

- `generate_chart(description: str, diagram_type: str = "flowchart", project_id: str | None = None, fresh: bool = False)` (`chart_tools.py:59-63`); docstring Args entry; `_full_doc_` (`:121-156`) gains the reuse/fresh paragraph (what reuses, what `fresh=True` does, that the default CHANGED).
- Flow: `fresh=False` → T1 discovery → hit → T3; miss → existing `invoke_agent_and_wait` call (`:101-110`) unchanged, NO pointer write (adjudicated P2 — discovery auto-finds the newest charter next call via `last_activity_at`). `fresh=True` → skip discovery → fresh spawn; the next reuse call discovers the new charter (latest `last_activity_at`).
- Existing callers (no kwarg) keep working; the DEFAULT semantics change from always-fresh to reuse (this is the feature). Adjudicated P4 sharpened the justification: today's always-fresh default contradicts the documented "Refine, don't hand-edit" contract (`chart/skill.md:62`) — the current default is the bug; default-reuse fixes documented behavior.
- Acceptance: unit tests — no-kwarg call with empty discovery hits `invoke_agent_and_wait` exactly as today (byte-identical kwargs, `tests/test_chart_tools.py:117-153` pins green); `fresh=True` call with an existing child STILL spawns fresh and the NEXT call discovers the new charter.
- **Gates:** P4 ADJUDICATED CONFIRMED (a) `fresh: bool = False`, default reuse — no flip contingency remains.

### T5 — busy-reject guard

- Module-level `_inflight_reuse: set[str]` keyed by charter id; add before register, discard in `finally` (T3); a second reuse attempt on an in-flight id returns `"Error: Charter busy; pass fresh=True for parallel charts."` without enqueuing (mirrors the queue-busy guard pattern `daemon/tools/instance.py:2930-2936`).
- **ORDERING REQUIREMENT (P9 addition 1):** the in-flight check MUST fire BEFORE `CompletionRegistry.register()` — not merely before enqueue. Two waiters on the same `instance_id` share one `asyncio.Event`; a second waiter that registers would wake on the FIRST completion (event coalescing, `completion_registry.py:79-81`) and return a stale result for its own message. The check-then-register sequence must be atomic with respect to the in-flight set — **no `await` between the busy check and the set add** (F10: a yield there would let a second caller pass the check).
- Acceptance: `asyncio.gather` two reuse calls on the same charter — exactly one registers AND enqueues; the other gets the busy string and observes NO register side-effect (`registry.is_registered` untouched by the rejected call); registry ends clean.
- **Gates:** P4 ADJUDICATED (error text final); P9 ADJUDICATED addition 1 (ordering pin — see T8.6). Sequential same-charter calls (non-concurrent) are unaffected.

### T6 — ERROR/FAILED one-revive counter

- Module-level `_reuse_revive_attempts: dict[str, int]` (per-charter-instance key — matches precedent `manager.py:773`).
- Discovery hit with `status in {ERROR, FAILED}`: if `_reuse_revive_attempts.get(charter_id, 0) >= 1` → treat as MISS (fresh spawn, log `mode=reuse-respawn-after-failure`); else increment and revive. `COMPLETED|TERMINATED` never touches the counter (scope mirrors `manager.py:2876-2886`).
- Code comment MUST state this is a SEPARATE mechanism from the agent-tool ReviveGuard — programmatic paths never call `note_agent_tool_revive` (`manager.py:2832-2834`; analysis R5.3).
- Acceptance: unit tests — ERROR charter: 1st call revives, 2nd call spawns fresh with a new id; COMPLETED charter: unlimited revives, counter untouched.
- **Gates:** P5 ADJUDICATED CONFIRMED (b) — one revive then respawn; M8 (no terminate) + M14 (PAUSED busy-reject) stand. P8 ADJUDICATED CONFIRMED (a) — counter stays `chart_tools.py` module-level; the mandatory ReviveGuard-separation comment stays.

### T7 — observability log line

- One `logger.info("generate_chart: caller=%s charter=%s mode=%s prior_status=%s", caller_id[:8], charter_id[:8] or "spawn", mode, prior_status)` immediately after the discovery/policy decision resolves; modes: `fresh` | `reuse` | `reuse-respawn-after-failure` | `busy-reject` (busy may be `logger.warning` — pin the level in the test). Logger exists at `chart_tools.py:21`.
- Acceptance: caplog assertion per mode; exactly one line per call.
- **Gates:** M6 (made — format is stable); text embeds no decision-sensitive tokens.

### T8 — unit tests (`tests/test_chart_tools.py`, new class `TestGenerateChartReuse`)

Fabricate charter rows as SimpleNamespace (`agent_id="charter"`, `instance_metadata={"invoked_as_tool": True}`, `status`, `last_activity_at`, `created_at`, `id` — including one NULL-`last_activity_at` row for the determinism pin).
1. `test_first_call_spawns_fresh` — discovery empty → `invoke_agent_and_wait` awaited once with legacy kwargs; log `mode=fresh`.
2. `test_second_call_reuses_same_instance` — discovery returns terminal COMPLETED charter → `manager.enqueue_message` awaited with that id; `invoke_agent_and_wait` NOT awaited; content returned verbatim; log `mode=reuse`.
3. `test_explicit_fresh_spawns_new_instance` — existing child + `fresh=True` → `invoke_agent_and_wait` awaited (new id); the NEXT call discovers the new charter (latest `last_activity_at`).
4. `test_error_charter_one_revive_then_respawn` — ERROR charter: call 1 revives (counter 1), call 2 spawns fresh; log `mode=reuse-respawn-after-failure`.
5. `test_busy_reject_on_concurrent_reuse` — in-flight id → busy string, no enqueue.
6. `test_busy_guard_fires_before_register` — **P9 addition 1:** in-flight check ordering pin — two concurrent reuse calls → exactly one calls `CompletionRegistry.register()`/enqueue; the REJECTED call observes NO register side-effect (`registry.is_registered(charter_id)` state and register call count prove the check precedes register — guards the two-waiter event-coalescing hazard, `completion_registry.py:79-81`).
7. `test_reuse_timeout_does_not_terminate` — `wait_for → None` → error string; `manager.terminate_instance` not awaited.
8. `test_discovery_picks_latest_charter` — **discovery-determinism pin (adjudicated P2, W2-hardened):** multiple charter children for one caller → the row with the latest `last_activity_at` wins; equal timestamps tie-break by `created_at`, then id; **fixture includes a NULL-`last_activity_at` row (treated as oldest — never wins) and reverse-ordered input rows (proves Python-side sort, since `get_children` ships no ORDER BY)**; non-charter/non-tool rows excluded regardless of recency. (Fixture-based ordering; echoed on real rows in Phase 3 T1.)
9. `test_log_line_mode_field` — caplog format pin.
10. `test_busy_reject_on_paused_charter` — **W6 pin:** discovered charter in PAUSED status → exact string `"Error: Charter is paused; resume it or pass fresh=True for a new charter."` (source of truth documented in Phase 2 T1 item 4); no enqueue, no register.
11. `test_completed_charter_does_not_consume_counter` — **W6 pin:** repeated COMPLETED-charter revives never increment `_reuse_revive_attempts` (free-revive scope, `manager.py:2876-2886`) — distinct from T8.4 (which pins the ERROR consume/respawn path).
12. `test_discovery_repository_error_degrades_to_fresh` — **W6 pin:** `get_children` raising → discovery returns None → fresh-spawn path taken; the exception never reaches the LLM (T1 try/except contract).
13. `test_terminated_charter_revives_free` — **W3 pin:** discovered TERMINATED charter → reuse proceeds (TERMINATED is in the terminal-revival set, `instance_messaging.py:1944-1953`), counter untouched, log `mode=reuse` — grounds the operator ladder (termination stops a hung turn but does NOT block later discovery; see plan-overview R1).
Regression guard: legacy classes `TestCreateChartToolsFactory`, `TestChartToolRegistration`, `TestGenerateChartInvocation` pass WITHOUT body modification (only the fixture hunk differs); `:150-153` message pins byte-identical in `git diff`.
Run: `uv run python -m pytest tests/test_chart_tools.py -q` from worktree root (dev-invocation gate — bare `pytest` is the broken Homebrew install).
- **Gates:** P9 ADJUDICATED CONFIRMED + additions 1–3 (M3 fixture shape with KV stubs dropped; ordering pin T8.6; determinism pin T8.8; P4/P5/P1 all adjudicated — test expectations are final).

### C1 — CONDITIONAL (DORMANT) message note (only if the post-merge P6 A/B demands it)

- P6 ADJUDICATED CONFIRMED (a): no prompt/message change in v1; C1 stays dormant unless real traffic shows the charter missing refinement intent (A/B post-merge, one variable at a time per M9).
- If ever activated: append one trailing line to the built message (`chart_tools.py:91-96`): `Note: this may be a refinement of a prior diagram.` — legacy pins assert substrings (`:150-153`), so they survive; ADD a new pin asserting the note appears ONLY on reuse-path messages (keeps fresh-spawn messages stable as the A/B control).
- **Gates:** P6 ADJUDICATED (a) — dormant by ruling; activation is a post-merge, evidence-gated decision.

## Coupling

- **Tight with Phase 2** — kwarg name (P4 adjudicated: `fresh: bool = False`, default reuse), default semantics, and the busy-error string are quoted verbatim in `chart/skill.md`; Phase 2 proceeds directly against the final shape.
- **Tight with Phase 3** — helper names, log format, error strings, and the fixture shape are Phase 3's assertion targets.
- **Independent of** — lifecycle spawn internals, `send_message` tool, ReviveGuard, sister tools.

## Risks

- Duplicated wait-block drift vs `utils.py:672-740` (Low/Medium) — M13 mirror + docstring contract; extract on second consumer (P1 adjudicated: no pre-emptive extraction).
- Unguarded programmatic revive (Medium/Certain) — bounded by T6 counter + T3 status pre-check + Phase 3 non-interaction pins (both COMPLETED- and ERROR-revive asserted, P9 addition 2).
- Fixture ambiguity breaking legacy tests (Medium/High if skipped) — Task 1 stubs; regression pin in T8.
- Multi-file partial write (Low/Low) — M12: after writing chart_tools.py + tests + constants.py, run `git diff --stat` and read-back grep each hunk; expected: 3 files, hunks at the documented anchors.

## Exit Criterion

`uv run python -m pytest tests/test_chart_tools.py tests/test_image_tools.py -q` fully green (new reuse lanes + untouched legacy pins + `invoke_agent_and_wait` signature lane), `git diff --stat` shows exactly the three planned files, and every task's Gates annotation records its ADJUDICATED verdict (P1–P9 resolved 2026-09-10 — the implemented shape IS the final shape).
