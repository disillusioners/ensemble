# Phase 3: Integration & Acceptance Tests

## Objective

Prove the reuse feature at the seams unit tests mock away: a REAL-dispatch integration walk of the revive path (real `manager.enqueue_message` → `InstanceMessagingService` revive block → real `CompletionRegistry`, on file-backed SQLite), budget/lifecycle non-interaction pins (spawn cap + agent-tool ReviveGuard), the ERROR/FAILED policy end-to-end, fan-out scoping, and the explicit regression gate over every existing pin the feature must not disturb.

Conventions baked in (repo Testing & QC):
- Execution ONLY via `uv run python -m pytest` from the worktree root (bare `pytest` = broken Homebrew install).
- File-backed SQLite with `tmp_path` + `NullPool` + WAL — **never in-memory StaticPool** (real-routing precedent: `tests/test_governor_recursion_acceptance_walk.py:570-603` — V5 imports REAL `invoke_agent_and_wait` and walks real components; mirror that shape for the reuse path).
- `completion_registry` patching targets the `daemon.services.completion_registry` MODULE attribute (lazy-import gotcha — `tests/test_finalize_instance.py:116-125`).
- Regression attribution: verify pins at pre-change base FIRST if any fail (worktree regression-proof convention); pre-existing failures are quarantined/attributed, never absorbed silently.
- No LLM is invoked anywhere: the charter's "turn" is simulated by completing the registry entry from a side task (the feature under test is the dispatch/revive/wait plumbing, not charter's output quality).

File targets (new tests only):
- `tests/test_chart_tools_reuse_integration.py` (new, integration lane) — or, if the file-backed-SQLite fixture set lives module-local in the governor acceptance file's style, a sibling following it.
- Existing files touched ONLY by the regression gate running against them (no edits expected).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | T1: real-dispatch revive walk (requirement 1) | Phase 1 | 2nd call reuses SAME instance id via real enqueue_message + real registry; charter row COMPLETED→RUNNING→terminal |
| 2 | T2: budget & lifecycle non-interaction pins (requirement 4) | Phase 1 | ReviveGuard count stays 0; spawn cap headroom not consumed by reuse |
| 3 | T3: ERROR/FAILED policy end-to-end (requirement 4) | Phase 1 T6 | 1st call revives ERROR charter; 2nd call respawns fresh |
| 4 | T4: regression gate — existing single-shot behavior + pins (requirement 7) | Phase 1, 2 | All legacy lanes green unmodified; helper signature lane green; message pins byte-identical |
| 5 | T5: fan-out scoping acceptance (requirement 3) | Phase 1 | Sibling callers → distinct charters; same caller → same charter |
| 6 | T6: conditional facade-forwarding verification | P1/P2 flips | Grep evidence no manager/service kwarg added; conditional task fires only on a flip |

### T1 — real-dispatch revive walk

Fixture (mirror the governor acceptance file's structure): file-backed SQLite engine + real instance repository + real `InstanceMessagingService` (or manager facade wired to it) + REAL `daemon.services.completion_registry` singleton (reset between tests).

1. Spawn a real caller row and a real charter child via the lifecycle service (`spawn_instance(..., invoked_as_tool=True)`, `instance_lifecycle.py:1317`) — cap check included, 1 of 50 used.
2. Force the charter row terminal COMPLETED (direct repo status write — the reuse path treats any terminal status as revive-eligible, `instance_messaging.py:1948-1953`).
3. Call `generate_chart` (real tool from `create_chart_tools`) with discovery pointed at the real repository; from a side `asyncio` task, complete the registry entry with the charter's canned output (`registry.complete(charter_id, ...)`) — simulating the revived turn finishing.
4. Assert: returned content == canned output; NO second spawn (charter row count for caller still 1); charter row was flipped to RUNNING at enqueue time and returns to terminal after completion; `invoke_agent_and_wait` never entered (assert via the spawn counter or by monkeypatching `daemon.utils.spawn`-side with a tripwire — spawn of a NEW instance is the failure signal).
5. Repeat the call a third time (revive-of-revived) — still the same instance id; hierarchy rows stay absent between calls (transience — `repository.py:251-257`), proving cap headroom is never consumed.
6. Discovery-determinism echo (cheap, real rows): fixture seeds TWO charter children for the caller with staggered `last_activity_at` → the walk reuses the LATEST (echoes the Phase 1 T8.8 unit pin on real repository rows); a legacy pre-feature older charter is silently orphaned (accepted residual, 🟢).
7. Record the spawn-vs-revive latency delta in the test notes (architect open question — immaterial to the mechanism, but worth a number).
- **Gates:** P1 ADJUDICATED CONFIRMED (a) (helper stays in chart_tools — this walk exercises it as-is); P2 ADJUDICATED = (a) **discovery-only setup** (no pointer pre-seeding — seed real `instances` rows only).

### T2 — budget & lifecycle non-interaction pins (requirements 4)

1. ReviveGuard non-interaction (**P9 addition 2 — assert for BOTH statuses**): after a full COMPLETED-revive cycle (T1 walk), `manager.get_agent_tool_revive_count(charter_id) == 0`, AND after a full ERROR-revive cycle (T3), the same count is still 0 — the programmatic path must never call `note_agent_tool_revive` in either case (`manager.py:2832-2834`); the local counter is a separate mechanism (P5).
2. Spawn-cap non-consumption: with `limits.max_children_per_instance` pinned low in the test config, a caller at the cap can STILL reuse its completed charter (reuse enqueues; it never spawns) — asserting the cap counts transient `instance_hierarchy` rows only (`instance_lifecycle.py:1563-1570`, `count_children` → hierarchy, `repository.py:439-452`), while `get_children` (permanent) still finds the charter.
3. Cleanup realism: completed charter row + checkpoint persist (no reaper — `research-lifecycle-revive.md` §4); the reuse path neither deletes nor archives anything (scope guard — "NO cross-session persistence beyond what reuse requires").
- **Gates:** none — these pins are decision-independent (asserted under the adjudicated P1–P9 verdicts; they hold regardless).

### T3 — ERROR/FAILED policy end-to-end

1. Charter row in ERROR status → call 1: revives (status ERROR→RUNNING→terminal; `_reuse_revive_attempts[id] == 1`; log `mode=reuse`).
2. Make the second turn fail again (registry completion with `is_error=True`), then call 2: spawns a FRESH charter (new id, `mode=reuse-respawn-after-failure`); the NEXT discovery call finds the new charter (latest `last_activity_at`); the old ERROR charter is left untouched (no terminate — M8).
3. COMPLETED-charter control: N successive revives never touch the counter (free, mirroring `manager.py:2876-2886` scope).
- **Gates:** P5 ADJUDICATED CONFIRMED (b) — one revive then respawn; expectations are final.

### T4 — regression gate (requirement 7 — explicit)

1. `uv run python -m pytest tests/test_chart_tools.py -q` — ALL classes green; `git diff` proves `TestCreateChartToolsFactory` / `TestChartToolRegistration` / `TestGenerateChartInvocation` bodies unmodified (only the `_make_manager()` fixture hunk from Phase 1 Task 1); message pins `:150-153` byte-identical.
2. `uv run python -m pytest tests/test_image_tools.py -q` — `invoke_agent_and_wait` backward-compat signature lane (`:730-783`) and explain_image kwarg pins (`:798-909`) green: the shared helper was NOT extended (M4 holds unless P1=(b) flipped — if flipped, this lane is the first thing to re-audit).
3. `uv run python -m pytest tests/unit/tools/test_prompt_section_reference_integrity.py -q` — skill/charter doc edits from Phase 2 pass the gate.
4. Pattern-descendant sanity: `tests/test_todo_tools.py` + `tests/test_system_log_tools.py` (chart-pattern descendants, `research-charter-tests.md` §5) green — the fixture-shape change must not silently diverge the mirrored pattern.
5. Attribution discipline: any failure in the above is attributed against the pre-change base (worktree regression-proof) before being called a feature regression; staged-index check (`git diff --cached`) before declaring results (multi-actor worktree convention).
- **Gates:** P1(b) flip widens step 2's blast radius; otherwise stable.

### T5 — fan-out scoping acceptance (requirement 3)

1. Unit-grade: two callers (different `current_instance_id` closures) each with their own prior charter child → caller A's refine hits charter A only; caller B's first call spawns charter B — distinct ids, zero cross-talk (per-caller-instance scoping, P3 ADJUDICATED CONFIRMED).
2. Same-caller continuity: caller A's second and third calls hit the SAME charter A id across repeated invocations (and across a caller-level terminal→revive cycle: mark caller COMPLETED, revive via enqueue on the caller row — the mapping key is the SAME UUID, so charter reuse continues; analysis R2.2/R3.1).
3. Acceptance: ids asserted distinct/identical exactly as above; no cross-enqueues (each `enqueue_message` call args captured and matched to the right charter).
- **Gates:** P3 ADJUDICATED CONFIRMED (a) — expectations final; under P2=(a) the "key" is simply the `get_children` argument, so the `_resolve_scope_key()` seam is trivial (keep/drop at implementer's discretion).

### T6 — conditional facade-forwarding verification

1. Verification (always run): `grep` the Phase 1 diff for added kwargs on any `manager.*` / `InstanceMessagingService` / repository method — the proposed design adds NONE (`enqueue_message` called with existing kwargs only, `instance_messaging.py:2070-2083`); record the grep as evidence (facade-forwarding discipline satisfied vacuously).
2. Conditional (fires ONLY if the implementer introduces a new kwarg on a manager/daemon-service method — none exists in the adjudicated design): per Core-Architecture blueprint — grep `daemon/manager.py` for the kwarg forwarding + add a real-dispatch integration test asserting the intended exception/behavior at the `InstanceManager.enqueue_message` seam (guards: `tests/unit/test_manager_enqueue_message_work_id_required.py` pattern + `tests/integration/test_job_driven_enqueue_work_id_facade.py` pattern).
- **Gates:** adjudication removed the flip triggers (P1(b)/P2(d) rejected); step 1 always runs, step 2 is pure insurance.

## Coupling

- **Tight with Phase 1** — every assertion targets Phase 1 surfaces (helper names, module dicts, log format, error strings, fixture stubs).
- **Loose with Phase 2** — T4 step 3 runs the gate over Phase 2's doc edits; no other dependency.
- **Independent of** — frontend, sister tools, `send_message` tool path.

## Risks

- Integration fixture weight (real engine + real services) slows the suite (Low/Medium) — one module, shared session-scoped engine fixture, mirror the governor acceptance file's fixture economy; keep the lane ≤ ~10 tests.
- Flaky side-task completion race in T1 (Medium/Low) — deterministic ordering: enqueue await → side task completes registry → assert; buffered-completion semantics (`utils.py:684-686` mirror) absorb ordering; no sleeps.
- Regression failures misattributed to the feature (Medium/Low) — base-attribution + staged-index check before reporting (T4 step 5).
- Multi-file write partiality when adding the new test module (Low/Low) — `git status` + read-back grep of the new file after write (M12).

## Exit Criterion

All Phase 3 suites green via `uv run python -m pytest` (integration + pins + regression + fan-out), requirements 1–7 each traceable to at least one PASSING test in this phase or Phase 1 T8, facade-forwarding evidence recorded, and the full touched-scope command (`uv run python -m pytest tests/test_chart_tools.py tests/test_image_tools.py tests/test_todo_tools.py tests/test_system_log_tools.py tests/unit/tools/test_prompt_section_reference_integrity.py -q`) green — the feature is merge-ready: P1–P9 are ADJUDICATED (2026-09-10) and the tested shape IS the final shape.
