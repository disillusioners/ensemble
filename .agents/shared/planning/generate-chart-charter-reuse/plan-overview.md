# Plan Overview: `generate_chart` Iterative Refinement via Per-Caller Charter Reuse

Date: 2026-09-10 12:55 UTC
Author: planner[v2] via plan-creation worker
Status: **Adjudicated (2026-09-10)** — P1–P9 resolved by `architecture-recommendation.md`: 8 of 9 defaults CONFIRMED; **P2 flipped to pure query-discovery** (Approach A confirmed as the reuse mechanism). This plan is SELF-CONTAINED — final decisions are inlined below and in `decisions.md` Part B (adjudication verdicts); the approver needs no architect notes.
Branch: `feature/generate-chart-charter-reuse` (verified via `.git/HEAD`; plan-writing only — no commits, no source edits by this worker)

## Objective

Successive `generate_chart` calls from the SAME caller reach the SAME charter agent instance — reusing the existing service-side revive-on-send + LangGraph checkpoint machinery — instead of always spawning a fresh charter; a deliberate backward-compatible `fresh` escape hatch preserves single-shot behavior. One testable completion sentence: *a caller's second `generate_chart` call returns its output from the caller's first charter instance id (no new spawn), while `fresh=True` explicitly spawns a new one, with all existing `tests/test_chart_tools.py` pins passing unmodified.*

## Scope

### In Scope

- Reuse core in `daemon/tools/chart_tools.py` only: discovery of the caller's prior charter child, a chart_tools-local `register → enqueue → wait_for → unregister` wait helper (mirroring `daemon/utils.py:672-740`), a `fresh` kwarg, an ERROR/FAILED revive policy, a concurrent-reuse busy guard, and a single greppable mode log line.
- Carrier documentation update: `agents/_prompt_system/innate-skills/chart/skill.md` (signature table :41-46 + "Refine, don't hand-edit" :62), run through the prompt-integrity gate.
- Optional, gated charter prompt delta (`agents/charter/{rule,soul,workflow}.md` or a message-shape note) — only if the architect selects options (b)/(c) of D-charter-prompt-delta.
- Tests: new unit-test class in `tests/test_chart_tools.py` (reuse branch), integration/acceptance tests (real-dispatch revive path, busy-reject, error policy, cap/ReviveGuard non-interaction, fan-out), and full regression of existing pins.

### Out of Scope

- **Chart caching / content dedup** — no caching of diagram output; each call runs a real charter turn. (Different feature; reuse is about instance identity, not memoization.)
- **Cross-session persistence beyond what reuse requires** — NO new durable store at all: tracking is pure query-discovery over existing `instances` rows (adjudicated P2); no session abstraction (none exists, per analysis Axis 3c).
- **Frontend work** — none; chart rendering is unchanged.
- **Changes to `invoke_agent_and_wait`'s public signature** (`daemon/utils.py:588-599`) — `explore` and `explain_image` pin it (`tests/test_image_tools.py:730-909`); they stay fresh-spawn tools (MADE decision M4; architect may flip via P1).
- **Schema migrations** in the proposed design (MADE M5; flagged caveat if P2 flips to a new column).
- **`send_message` tool / ReviveGuard changes** — the agent-tool guard (`daemon/tools/instance.py:2978-2984`) and its counter (`manager.py:773, 2780-2899`) are untouched; the chart reuse path is a separate programmatic surface that never consults or bumps them.
- **Sister tools** (`explore`, `explain_image`) — no behavior change.

## Approach Summary

Working design = the Consolidated PROPOSED Design from `technical-analysis.md`, **as adjudicated 2026-09-10 by `architecture-recommendation.md`** (Approach A confirmed; verdicts inlined per item):

1. **Mechanism (Axis 1, ADJUDICATED P1 = CONFIRMED):** chart_tools-local private helper that does `CompletionRegistry.register → manager.enqueue_message → re-register race check → wait_for → finally unregister` against an EXISTING terminal charter `instance_id` — mirroring `daemon/utils.py:672-740`, including the buffered-completion re-register (`:683-685`) and the `finally` unregister (`:735-739`). Service-side revive is the load-bearing seam: `enqueue_message` flips `COMPLETED|TERMINATED|ERROR|FAILED → RUNNING` and reloads the checkpoint (`daemon/services/instance_messaging.py:1931-1966`; PAUSED excluded `:1939-1943`). `invoke_agent_and_wait` is NOT extended; no pre-emptive shared-helper extraction (extract only when a second consumer appears).
2. **Tracking (Axis 2, ADJUDICATED P2 = FLIPPED to pure query-discovery):** discovery IS the store — `manager._instance_repository.get_children(caller_id)` (`daemon/repositories/instance/repository.py:424-429`) filtered to `agent_id == "charter"` + `instance_metadata["invoked_as_tool"]` (`instance_lifecycle.py:1798-1799`), ordered `last_activity_at` desc (`models.py:72`), tie-break `created_at` then id (deterministic). **No SharedMetaKV pointer row, no KV read/write/cross-check/self-heal code.** Rationale: ambient-KV render (merge 02cf770a, default ON) would surface the pointer row in every caller turn; the KV saves only one indexed lookup (N≈1 charter children per caller; the cross-check re-queries anyway); the staleness class disappears; restart-safe by construction.
3. **Scoping (Axis 3, ADJUDICATED P3 = CONFIRMED):** per-caller-instance — the "key" IS `current_instance_id` as the `get_children` argument (the context_key concept drops away under P2=(a)). Fan-out is safe by construction — sibling children each get their own charter.
4. **Fresh-start API (Axis 4, ADJUDICATED P4 = CONFIRMED):** additive kwarg `fresh: bool = False`; default = reuse (deliberate semantics change, documented in the chart skill — today's always-fresh default contradicts the documented "Refine, don't hand-edit" contract at `chart/skill.md:62`). `fresh=True` skips discovery and spawns via the existing `invoke_agent_and_wait` path; the next reuse call discovers the new charter automatically (latest by `last_activity_at`).
5. **ERROR/FAILED policy (Axis 5, ADJUDICATED P5 = CONFIRMED):** local per-charter revive counter (module-level dict, mirroring the `_agent_tool_revive_counts` precedent at `manager.py:773`): ERROR/FAILED consumes an attempt; after 1 failed revive the next call respawns fresh. COMPLETED/TERMINATED revives are free. M8 (no terminate on reuse-timeout) and M14 (PAUSED busy-reject) stand.
6. **Concurrency (Axis 6):** sequential by default (single LangGraph event loop per instance); a second reuse call while the charter is in-flight is rejected with `"Error: Charter busy; pass fresh=True for parallel charts."` (mirrors the queue-busy guard pattern at `daemon/tools/instance.py:2930-2936`). The in-flight check fires BEFORE `CompletionRegistry.register()` (P9 addition — event-coalescing hazard, `completion_registry.py:79-81`).
7. **Charter prompt (Axis 7, ADJUDICATED P6 = CONFIRMED):** no prompt/message change in v1 — `rule.md:31` isolation is filesystem-scoped (concurrent temp files), not conversation-scoped; the message-note/prompt-delta A/B is a gated post-merge follow-up (Phase 1 C1 stays dormant).
8. **Observability (Axis 8):** one INFO line in chart_tools: `generate_chart: caller=<caller[:8]> charter=<id[:8]|spawn> mode=<fresh|reuse|reuse-respawn-after-failure|busy-reject> prior_status=<status>`.

What stays untouched: message construction (`chart_tools.py:91-96` — pins at `tests/test_chart_tools.py:150-153`), the `invoke_agent_and_wait` fresh path and signature, tool registration/authorization (`_tool_registry.py:496`, `_auth.py:35-40`), lifecycle spawn machinery, and all sister tools.

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 1 | Reuse core in chart_tools | Discovery + reuse wait helper + `fresh` kwarg + error/busy policy + logging, with unit tests | 8 | tight with Phase 2 (kwarg name + message shape feed the skill doc) and Phase 3 (test surfaces) | pending |
| 2 | Carrier docs + gated prompt delta | Document new default semantics for the ~15 chart-skill carriers; run integrity gate; charter delta dormant (P6=(a)); conventions.md note CONFIRMED | 4 | tight with Phase 1 (kwarg name, default semantics); independent of Phase 3 | pending |
| 3 | Integration & acceptance tests | Real-dispatch revive walk, budget/lifecycle pins, error policy, fan-out, regression gate | 6 | tight with Phase 1 (asserts its surfaces); consumes Phase 2 only for doc-truth checks | pending |

Dependency: Phase 1 → Phase 3 (Phase 3 tests Phase 1's surfaces). Phase 2 can start once Phase 1's T4 (kwarg) lands; Phase 2 T2 (charter delta) is dormant under adjudicated P6=(a) (verified no-op); Phase 2 T3 (conventions.md note) is CONFIRMED by the architect (default-semantics change is Certain for ~15 carriers).

## Coupling Map

| | Phase 1 | Phase 2 | Phase 3 |
|---|---|---|---|
| Phase 1 | — | tight (kwarg name, default semantics, message shape) | tight (test surfaces: helper names, log format, error strings) |
| Phase 2 | tight | — | loose (Phase 3 T4 doc-truth sweep reads skill.md) |
| Phase 3 | tight | loose | — |

Cross-phase risk (resolved): the P4 api-shape ruling has landed (CONFIRMED `fresh: bool = False`, default reuse) — Phase 2 wording proceeds directly against the final kwarg; no decision-neutral phasing needed.

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Register/wait/unregister block duplicated from `utils.py:672-740` drifts (analysis R1.1) | Low | Medium | MADE M13: mirror the block exactly; document the contract in the helper docstring; extract to a shared private helper only when a second consumer appears (P1 option c) |
| 2 | Programmatic reuse path is unguarded by the agent-tool ReviveGuard (`manager.py:2832-2834`) — unbounded revive thrash on persistent failure (analysis R1.2) | Medium | Certain (by design) | Local per-charter counter bounds failure loops (P5 default: one revive, then respawn); integration pin asserts ReviveGuard counter stays 0 |
| 3 | Reuse-path timeout kills or races a still-running shared charter (analysis R1.3) | Medium | Low | MADE M8: reuse path does NOT call `_try_terminate_orphan`; timeout returns an error and leaves the charter alone; a genuinely-running orphan is caught by the busy guard on the next call |
| 4 | Default-semantics change surprises the 15 chart-skill carriers (analysis R4.1) | Medium | Certain | Phase 2 T1 explicit skill.md update + integrity gate; optional conventions.md migration note (Phase 2 T3); carriers needing independence pass `fresh=True` |
| 5 | MagicMock fixture ambiguity: auto-created manager attributes make discovery truthy/nondeterministic in EXISTING tests | Medium | High (if unaddressed) | MADE M3: `_make_manager()` gains deterministic stubs (`get_children → []`, `enqueue_message` AsyncMock); regression pins :114-218 must pass unmodified except the fixture |
| 6 | Existing message-content pins break if a message note is added under P6=(b) (pins `tests/test_chart_tools.py:150-153` are substring-only but new text may still surprise) | Low | Low | MADE M9: message unchanged in v1 (P6 adjudicated (a); C1 dormant); option (b) carries its own new pin task if ever activated |
| 7 | Charter produces a duplicate-of-prior diagram because refinement intent isn't signaled (analysis R7.1) | Low | Medium | Accepted for v1 under adjudicated P6=(a); caller can re-describe; gated post-merge A/B follow-up |
| 8 | Silent partial-write when Phase 1 touches multiple files | Low | Low | MADE M12: `git diff --stat` + read-back grep after every write batch (test-execution + verification conventions) |
| 9 | Decision drift after adjudication (plan contradicting the architect ruling) | Low | Low | RESOLVED this pass: all P1–P9 verdicts inlined (decisions.md Part B); plan swept for stale KV-pointer content |

### Residual Risks (post-adjudication, carried from `architecture-recommendation.md`)

| # | Risk | Severity | Mitigation / Notes |
|---|------|----------|--------------------|
| R1 | **Wedged charter (true LLM hang)**: orphaned RUNNING instance, no reaper; accumulates silently until operator terminates | 🟡 | Observable via persistent `busy-reject` mode log. Operator ladder (skill.md documents steps 2–3): **(1)** persistent `busy-reject` signals a likely-hung prior turn; **(2)** caller escapes with `fresh=True` — discovery determinism (latest `last_activity_at`) routes all subsequent reuse to the NEW charter; **(3)** operator manually `terminate_instance` via the daemon API to stop the hung turn / free the orphan's compute — termination does NOT retire the charter from discovery (TERMINATED is in the free-revive set, `instance_messaging.py:1944-1953`; ReviveGuard consumes only ERROR/FAILED, `manager.py:2876-2886`), retirement comes from discovery determinism in step 2; **(4)** a daemon restart clears the in-memory busy/counter locks (M8/M14 state). NO charter-terminate tool surface (explicitly rejected — blast radius for a rare event). Self-heals without operator action if the charter is merely slow (completes → terminal → next call revives). |
| R2 | **Compaction fidelity cliff at extreme refine counts (~50+ iterations)**: mermaid-bearing AIMessages ARE selectable (`compaction.py:160-219`), but the L1/L2 thresholds — 0.80× and 0.95× of `DEFAULT_CONTEXT_LIMIT` (700k, `compaction.py:1090`) — sit far above a 10–150 KB realistic refine history | 🟡 | Effectively unreachable at realistic loop sizes (2–10 refines); skill.md guidance: switch to `fresh=True` if compaction is ever observed mid-loop. |
| R3 | Two-waiter CompletionRegistry event coalescing if busy guard misordered | 🟢 mitigated | Ordering requirement (check BEFORE register) + dedicated unit pin (P9 addition 1, Phase 1 T8.6). |
| R4 | Local revive counter lost on daemon restart | 🟢 | Precedent-accepted (`manager.py:2792-2793`). |
| R5 | Callers with legacy pre-feature multiple charter children: discovery picks latest `last_activity_at` deterministically; older charters orphan silently | 🟢 | Same last-write-wins semantics the pointer would have had; no pointer/DB disagreement possible under pure discovery. |

## Success Criteria

- [ ] First `generate_chart` call from a caller with no prior charter spawns fresh (`mode=fresh` log; `invoke_agent_and_wait` awaited once).
- [ ] Second call from the SAME caller with a discovered terminal charter does NOT call `invoke_agent_and_wait`; enqueues to the SAME charter instance id; returns the charter output verbatim; the charter row transitions COMPLETED→RUNNING→(terminal) via the service-side revive path.
- [ ] `fresh=True` spawns a NEW charter id and leaves the prior charter untouched; the next reuse call discovers the new charter (latest by `last_activity_at`).
- [ ] Discovered ERROR/FAILED charter: first call revives once; the next call respawns fresh (`mode=reuse-respawn-after-failure`) per adjudicated P5.
- [ ] A concurrent second reuse call against an in-flight charter gets `"Error: Charter busy; pass fresh=True for parallel charts."`, and the rejected call observes NO `CompletionRegistry.register()` side-effect (busy-guard-before-register ordering, P9 addition 1).
- [ ] Discovery determinism: with multiple charter children, the latest `last_activity_at` wins (tie-break `created_at`, then id) — unit-pinned (Phase 1 T8.8) and echoed in the Phase 3 real-routing walk.
- [ ] Reuse path never bumps `manager.get_agent_tool_revive_count` (stays 0) and never consumes `max_children_per_instance` headroom (spawn-cap counts transient `instance_hierarchy` rows only; completed charters don't consume it).
- [ ] Existing `tests/test_chart_tools.py:114-218` pins pass with only the `_make_manager()` fixture extension (M3); message pins :150-153 byte-identical; `tests/test_image_tools.py:730-783` signature lane passes.
- [ ] `chart/skill.md` documents the new default + escape hatch; `uv run python -m pytest tests/unit/tools/test_prompt_section_reference_integrity.py` green.
- [ ] Single INFO log line greppable for `mode=reuse|fresh|reuse-respawn-after-failure|busy-reject` on every call.
- [ ] All tests executed via `uv run python -m pytest` from the worktree root (dev-invocation gate).

## Requirements Traceability

| # | Requirement | Phase / Task | Test |
|---|-------------|--------------|------|
| 1 | Reuse semantics (follow-up call reuses caller's existing charter via revive machinery) | P1 T1 (discovery = sole store), T3 (reuse helper) | P1 T8.2/T8.3 (unit: same id, no spawn); P3 T1 (integration: real `enqueue_message` revive COMPLETED→RUNNING, same id) |
| 2 | Fresh-start escape hatch, backward-compatible signature, existing callers unaffected | P1 T4 (`fresh` kwarg + `_full_doc_`) | P1 T8.4 (fresh=True → new id, next call discovers it); P3 T4 (regression: existing pins + signature lanes unmodified) |
| 3 | Scoping/keying + fan-out (multiple children of one caller) | P1 T1 (key = `current_instance_id` as get_children arg, adjudicated P3) | P3 T5 (two sibling callers → distinct charters, no cross-talk; same caller → same charter); P1 T8.8 (determinism across multiple charter children, NULL-safe) |
| 4 | Lifecycle & budget (cleanup, instance-limit interaction, ERROR/FAILED vs COMPLETED revive cost) | P1 T3 (no orphan-terminate on timeout), T6 (error counter); P3 T2 (cap + ReviveGuard non-interaction pins, BOTH COMPLETED- and ERROR-revive) | P3 T2, P3 T3; P1 T8.4/T8.5/T8.6/T8.7 (+ T8.11 completed-charter counter, T8.13 TERMINATED revives free) |
| 5 | Charter agent side (minimal prompt delta; `rule.md:31` is filesystem-scoped) | P2 T2 (adjudicated P6=(a): verified no-op; C1 dormant) + verification grep | P2 T2 acceptance; integrity gate run (P2 T4 / T1) |
| 6 | Observability (greppable revived-vs-spawned) | P1 T7 (log line) | P1 T8.9 (log assertion); P3 T4 sweep |
| 7 | Tests: first-call spawns; second-call revives SAME id; explicit fresh spawns new; ERROR/FAILED behavior; regression of single-shot + pins (`tests/test_chart_tools.py:114-218`, esp. :150-153) | P1 T8 + P3 T1–T5 | P3 T4 is the explicit regression gate (pins unmodified); P1 T8.2–8.5; P3 T1/T3 |

## Research Insights

Key findings from the three research docs + technical analysis that shaped this plan (file:line in repo):

- `invoke_agent_and_wait` always pre-generates a fresh UUID (`daemon/utils.py:650`) and couples spawn+register+enqueue+wait as one unit (`:656-740`) — there is NO reuse seam anywhere on the delegate path today (`research-chart-tool.md` §5); `child_instance_id` is unpacked and dropped (`chart_tools.py:101-119`).
- Service-side revive-on-send flips terminal→RUNNING with implicit checkpoint reuse (`instance_messaging.py:1931-1966`; PAUSED deliberately excluded `:1939-1943`) — the reuse mechanism rides `manager.enqueue_message`, NOT the `send_message` tool, so the agent-tool ReviveGuard (`instance.py:2978-2984`, counter `manager.py:773`) never applies (`manager.py:2832-2834` explicit).
- `instances.parent_id` is permanent across revive; `instance_hierarchy` rows are transient (deleted at completion) — so `get_children` (`repository.py:424-429`) sees completed charters while the spawn cap (`count_children`, `instance_lifecycle.py:1563-1570`, default 50 `config.py:469`) does not count them. Completed charters cost zero cap headroom.
- No DB reaper exists for completed instances/checkpoints (`research-lifecycle-revive.md` §4) — revive depends on exactly this persistence.
- Charter prompts actively reinforce isolation (`rule.md:6,31` per-instance temp files for CONCURRENT instances) but say nothing about conversation continuity; refinement exists only as a caller-side re-invocation convention (`rule.md:12`, `workflow.md:43`); the "Refine, don't hand-edit" contract lives in `chart/skill.md:62` (carried by ~15 agents), not in charter prompts (`research-charter-tests.md` §1, §3).
- Test conventions: `MagicMock` manager + module-level `patch("daemon.tools.chart_tools.invoke_agent_and_wait", AsyncMock)` + `await tools[0].coroutine(...)` (`tests/test_chart_tools.py:29-41, 117-153`); completion-registry patching must target the `daemon.services.completion_registry` MODULE attribute (lazy import — `tests/test_finalize_instance.py:116-125`); real-routing integration precedent `tests/test_governor_recursion_acceptance_walk.py:570-603` (real components + file-backed SQLite, never in-memory StaticPool); tests ONLY via `uv run python -m pytest`.
- Prompt-integrity gate `tests/unit/tools/test_prompt_section_reference_integrity.py:80-117` covers `agents/_prompt_system/innate-skills/*` AND `agents/<agent>/{soul,rule,workflow,tools_note,memory}.md`; convention v2 section references; `chart/skill.md:80` cross-references charter soul.md's `## My Expertise` heading — keep it stable.
- `enqueue_message` service signature has NO `context`/`load_skill` kwargs (`instance_messaging.py:2070-2083`) — the reuse path passes only existing kwargs; no facade-forwarding surface is touched (MADE M11).

## Open Questions

**All architecture-critical decisions RESOLVED** — adjudicated 2026-09-10 (Approach A confirmed; verdicts with rationale inlined in `decisions.md` Part B): P1 mechanism (a) · P2 tracking (a) pure query-discovery [FLIPPED] · P3 scoping (a) · P4 api (a) `fresh: bool = False` · P5 error policy (b) · P6 prompt delta (a) · P7 doc surface (a) · P8 counter location (a) · P9 test pattern + 2 additions. `Gates:` annotations throughout the phase files record what each decision resolved to.

Non-blocking, post-merge only:
- P6 A/B empirics: does the charter reliably infer refinement intent from checkpoint history alone? Decide with real traffic (gated Phase 1 C1 / P6=(b) message note stays dormant).
- Spawn-vs-revive latency delta unmeasured — immaterial to the mechanism choice; record a number in Phase 3 T1 test notes.
- `_resolve_scope_key()` seam (Phase 1 T1): trivial under P2=(a) (the key IS `current_instance_id`) — keep or drop at implementer's discretion (architect P3 note).
