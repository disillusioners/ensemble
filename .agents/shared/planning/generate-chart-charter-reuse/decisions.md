# Decision Record: `generate_chart` Charter Reuse

Date: 2026-09-10
Author: planner[v2] via plan-creation worker
Input: `research-chart-tool.md`, `research-lifecycle-revive.md`, `research-charter-tests.md`, `technical-analysis.md` (§ Consolidated PROPOSED Design = working default; its § Open Questions Q1–Q7 mirrored below), **`architecture-recommendation.md` + `approach-comparison.md` (architect adjudication, 2026-09-10)**.
Status convention: **MADE** = decided at plan level, implementation-safe. **PENDING-ARCHITECTURE** entries below carry their final **ADJUDICATED (architect, 2026-09-10)** verdicts inline — Approach A CONFIRMED as the reuse mechanism; 8 of 9 planner defaults confirmed; **P2 flipped to pure query-discovery**. This record is self-contained for the approver.

---

## Part A — MADE Decisions

### M1 — Plan structure: three phases (core → docs → integration)
- **Decision:** Phase 1 = reuse core in `daemon/tools/chart_tools.py` + unit tests; Phase 2 = carrier docs (`chart/skill.md`) + gated charter delta; Phase 3 = integration/acceptance + regression gate.
- **Rationale:** Phase 1 is the only code-bearing unit and everything else asserts or documents it; docs (Phase 2) must follow the ruled API shape; integration tests (Phase 3) exercise real seams unit mocks hide.
- **Alternatives:** single mega-phase (loses reviewability + flip isolation); four phases splitting unit from integration tests (no dependency benefit — both test Phase 1's surfaces).

### M2 — Test home: extend `tests/test_chart_tools.py` (new class), not a new unit file
- **Decision:** unit tests for the reuse branch live in `tests/test_chart_tools.py` as a new `TestGenerateChartReuse` class alongside the legacy lanes.
- **Rationale:** that file IS the contract home (three established lanes, docstring-documented pattern, `:114-218` pins); pattern descendants (`test_todo_tools.py:3`, `test_system_log_tools.py:46`, `test_image_tools.py:26-28`) mirror it — keeping the family together preserves mirror-parity review; regression is one file.
- **Alternatives:** new `tests/unit/tools/test_chart_reuse.py` (splits the contract; descendants drift); inside `test_image_tools.py` (wrong tool).
- **Consequence:** legacy lanes must stay body-unmodified (regression proof via `git diff`).

### M3 — Fixture shape for the reuse branch (answers analysis Q5 direction)
- **Decision:** extend `_make_manager()` (`tests/test_chart_tools.py:29-41`) with two deterministic stubs: `_instance_repository.get_children → []`, `enqueue_message → AsyncMock`. Reuse-path tests mock manager methods + patch `daemon.services.completion_registry` at the MODULE attribute (lazy-import gotcha, `tests/test_finalize_instance.py:116-125`); fresh-path tests keep `patch("daemon.tools.chart_tools.invoke_agent_and_wait", AsyncMock)`. Fabricated charter rows are SimpleNamespace with `agent_id`/`instance_metadata`/`status`/`last_activity_at`/`created_at`/`id`.
- **Post-adjudication update (P2 flip):** the originally-planned `shared_meta_kv_repo` stubs (`get → None`, `set_kv → True`) are DROPPED — the reuse path never touches the KV repo under pure query-discovery.
- **Rationale:** bare `MagicMock()` auto-attributes make discovery truthy/nondeterministic (a MagicMock's `get()` returns a truthy MagicMock that would explode in JSON parsing; `get_children` returns an iterable-empty MagicMock only by magic-method accident). Deterministic stubs keep ALL legacy lanes green without body edits.
- **Alternatives:** per-test local patching (repetitive, drift-prone); a separate reuse-only fixture module (splits the pattern family).
- **Status vs analysis Q5:** this CONFIRMS "pattern stays consistent, patched surface moves" — carried as P9 for the architect's formal sign-off (since adjudicated CONFIRMED, with 2 test additions).

### M4 — NO signature change to `invoke_agent_and_wait`
- **Decision:** the shared helper `daemon/utils.py:588-740` keeps its exact public signature; reuse is implemented chart_tools-locally (P1 default).
- **Rationale:** `explore` (`knowledge_tools.py:797-807`) and `explain_image` (`image_tools.py:513-523`) pin the signature (`tests/test_image_tools.py:730-783` backward-compat lane); the reuse semantics ("same caller, same delegate") exist only for chart; keeping blast radius at one file matches the analysis Axis 1a reasoning.
- **Alternatives:** `reuse_instance_id` kwarg on the helper (Axis 1b — touches two sister tools' pinned surface for zero benefit to them); hybrid private-helper extraction (Axis 1c — deferred until a second consumer exists, per Q4 recommendation).

### M5 — NO schema migration (DORMANT caveat under adjudicated P2)
- **Decision:** zero DB schema changes; tracking is pure query-discovery over existing `instances` rows (adjudicated P2 — the former SharedMetaKV-pointer option and the new-column option are both off the table for v1).
- **Rationale:** caller→latest-charter is a derived relationship re-derivable by one indexed query (`get_children`, N≈1 children) — no pointer row and no column needed.
- **CAVEAT (dormant — zero schema interaction under adjudicated P2=(a); kept for the record):** IF a future revision ever adds a column/metadata-key store, the migration runner is **NO-OP on PostgreSQL** (PG schema = `create_all` + `_ensure_postgres_columns`) — such a change must go through `models.py` + `_ensure_postgres_columns`, never a SQL migration file; a SQL migration would silently never run on prod PG.

### M6 — Observability: single INFO log line, fixed format
- **Decision:** one line per `generate_chart` call from the chart_tools module logger (exists at `chart_tools.py:21`):
  `generate_chart: caller=<caller_id[:8]> charter=<charter_id[:8]|"spawn"> mode=<fresh|reuse|reuse-respawn-after-failure|busy-reject> prior_status=<status|none>`
  (`busy-reject` at WARNING level).
- **Rationale:** single greppable surface (analysis Axis 8a); correlates caller+charter in one line; matches the log precedents at `instance_messaging.py:1961-1966` (reactivation) and `manager.py:2847-2848` (ReviveGuard grant). Volume: 1 line per call — noise against a 600s-budget delegate.
- **Alternatives:** two-line split tool+helper (harder correlation); relying on the service-side `Reactivating terminal instance` line alone (invisible for the fresh branch).

### M7 — Reuse enqueue source prefix: `internal_chart_reuse:{caller_id}`
- **Decision:** the reuse path enqueues with `source=f"internal_chart_reuse:{caller_id}"` and adds one doc line to the source-prefix block at `daemon/constants.py:373-377` (next to the `internal_invoke_and_wait:` entry).
- **Rationale:** distinct, greppable provenance for incident review (requirement 6); follows the documented-prefix precedent; keeps `internal_invoke_and_wait:` semantics honest (that prefix means "went through the spawn helper").
- **Alternatives:** reuse the `internal_invoke_and_wait:` prefix (misleading — no spawn happened); bare `internal_agent:` (loses tool provenance).

### M8 — Reuse-path timeout does NOT terminate the charter
- **Decision:** on `wait_for → None`, the reuse helper returns a timeout error string and does NOT call `_try_terminate_orphan` (contrast `utils.py:691-697` on the fresh path).
- **Rationale:** the fresh path's fire-and-forget terminate assumes a disposable instance; the reused charter is shared/durable — terminating it (a) kills a possibly in-flight turn racing the timeout (analysis R1.3) and (b) poisons the next refine with a TERMINATED revival. Leaving it running is safe: the busy guard rejects a still-running charter, and buffered completion absorbs a late finish.
- **Alternatives:** mirror the terminate (rejected — see above); synchronous cancel (new machinery, out of scope).

### M9 — Message construction unchanged in v1
- **Decision:** the charter message (`chart_tools.py:91-96`) keeps its exact shape on both paths; no refinement note (Phase 1 conditional task C1 exists only under P6=(b)).
- **Rationale:** pins `tests/test_chart_tools.py:150-153` (`"User authentication flow" in message`, `"sequence" in message`) are substring-shaped, but byte-stable messages make the A/B question (does the charter need a refinement hint?) clean — one variable at a time; charter is a functional agent that answers the request as written (`rule.md:11`).
- **Alternatives:** unconditional note (contaminates fresh spawns; breaks A/B cleanliness).

### M10 — Test execution gate
- **Decision:** all suites run via `uv run python -m pytest` from the worktree root; bare `pytest` is forbidden (foreign broken Homebrew install, `_console_main` ImportError).
- **Rationale:** repo dev-invocation convention; fresh worktrees lack `.venv`.

### M11 — Facade-forwarding: vacuously satisfied in the proposed design; conditional task reserved
- **Decision:** no kwarg is added to any `manager`/`InstanceMessagingService`/repository method (reuse calls `enqueue_message` with existing kwargs only — `instance_messaging.py:2070-2083` has no `context`/`load_skill` at this layer). Phase 3 T6 step 1 records the grep evidence; step 2 (facade-forwarding check in `daemon/manager.py` + real-dispatch integration test per the blueprint guards) fires ONLY if an architect flip adds such a kwarg.
- **Rationale:** the manual-forwarding facade bug class (AsyncMock-passing, real-dispatch-failing) can't trigger without a new kwarg; still pinned so the check isn't forgotten on a flip.

### M12 — Multi-edit verification protocol
- **Decision:** after every multi-file write batch in implementation: `git diff --stat` (tracked) / `git status` + read-back grep (new files), expected-hunk audit against the anchors named in the phase files; staged-index check (`git diff --cached`) before declaring test results in the shared worktree.
- **Rationale:** `edit_file`/`write_file` silent partial-write trap; parallel same-file edits = lost update (re-confirmed 2026-09-09) — sequential edits or one atomic read→replace→write.

### M13 — Flip-robustness annotation convention
- **Decision:** every phase task carries a `**Gates:**` line recording the decision(s) it depends on and their resolution; Phase 1 isolates decision-sensitive behavior behind named seams (`_find_reusable_charter`, `_reuse_charter`, `_resolve_scope_key` — trivial and optional under adjudicated P2=(a)/P3=(a)) so any future revision remains a bounded edit.
- **Rationale:** the plan must survive P1–P9 rulings without re-planning (dispatcher constraint).

### M14 — PAUSED charter is busy-rejected, not enqueued
- **Decision:** discovery hit on a PAUSED charter returns a distinct busy-style error (enqueue would sit PENDING until resume and burn the 600s tool wait — `instance_messaging.py:1939-1943, 2115-2122`).
- **Rationale:** revive-on-send deliberately excludes PAUSED; a hanging tool call is worse than a fast, actionable error.
- **Alternatives:** enqueue anyway (hangs the caller); auto-resume (dangerous — pause is an operator state).

---

## Part B — Architecture Decisions (P1–P9) — ALL ADJUDICATED (architect, 2026-09-10)

Approach A (chart_tools-local revive-seam helper) CONFIRMED as the reuse mechanism; each entry below carries its final verdict + rationale inline (source: `architecture-recommendation.md`). The original planner options/defaults are retained as the decision record.

### P1 — D-reuse-mechanism (Q4: pre-emptive helper extraction?) → **ADJUDICATED (architect, 2026-09-10): CONFIRMED (a) chart_tools-local private helper**
- **Verdict rationale:** Approach A rides existing machinery with zero invariant violations — revive-on-send (`instance_messaging.py:1931-1966`), permanent `parent_id` walk (`repository.py:424-429`), no reaper required; do NOT extend `invoke_agent_and_wait` (pinned sister-tool surface, zero benefit to them) and do NOT pre-extract a shared helper (premature abstraction; extract only when a second consumer appears).
- **Question:** where does the register→enqueue→wait→unregister block live?
- **Options:** (a) chart_tools-local private helper [default]; (b) extend `invoke_agent_and_wait` with `reuse_instance_id: str | None = None`; (c) hybrid — extract a private shared helper in `daemon/utils.py` used by both paths.
- **PROPOSED:** (a). Chart-specific semantics; sister tools (`explore`, `explain_image`) have pinned signature surfaces (`tests/test_image_tools.py:730-783`); duplication is ≤30 LOC (M4).
- **Flip impact:** (b) → Phase 1 T3 moves into `daemon/utils.py:656-740`; M4 inverted; `tests/test_image_tools.py` lane re-audit + new optional-kwarg lane required; M8/M14 policies travel with it. (c) → T3 becomes a call into the extracted helper; Phase 3 T1's "real" surface shifts. (b)/(c) do NOT trigger M11's conditional (helper ≠ manager method).

### P2 — D-tracking-store → **ADJUDICATED (architect, 2026-09-10): FLIPPED — (a) PURE QUERY-DISCOVERY (KV pointer rejected)**
- **Verdict rationale (one line):** the SharedMetaKV pointer is rejected — the ambient-KV render (merge 02cf770a, default ON) would surface the pointer row as visible internal plumbing in every caller turn (decisive), it saves only one indexed lookup (N≈1 charter children per caller; the DB-wins cross-check re-queries anyway), the staleness class disappears entirely, and restart-safety is by construction.
- **Final shape:** discovery = `get_children(caller_id)` filtered to `agent_id=="charter"` + `instance_metadata["invoked_as_tool"]`, ordered `last_activity_at` desc (tie-break `created_at`, then id). No SharedMetaKV row; no KV read, write, cross-check, or self-heal code.
- **Question:** how is the caller's prior charter discovered/tracked?
- **Options:** (a) pure query-discovery each call (`get_children` + filters — stateless, restart-safe) **[ADJUDICATED]**; (b) in-memory manager dict (restart-lost); (c) SharedMetaKV pointer + query cross-check (planner default — REJECTED by adjudication); (d) new `instances` column / metadata key.
- **Planner PROPOSED (superseded):** (c) with (a) as the cross-check (analysis Axis 2) — one indexed lookup, restart-safe, self-healing on KV failure (R2.3). The architect's flip overruled this: see verdict rationale above.
- **Applied edits (per architect's bounded-edit list):** Phase 1 T2 deleted (T1 is the sole store); fixture KV stubs dropped; T8 pointer tests deleted; Phase 3 T1 discovery-only; plan-overview success criterion reworded. The (d) caveat below is retained only as dormant M5 context: migration runner is NO-OP on PG (schema = `create_all` + `_ensure_postgres_columns`) — a hypothetical column store would be an ORM + ensure-columns change, never a SQL migration file.

### P3 — D-scoping-key (Q7: fan-out semantics) → **ADJUDICATED (architect, 2026-09-10): CONFIRMED (a) per-caller-instance**
- **Verdict rationale:** fan-out is safe by construction — siblings each own their charter, cross-talk impossible; per-tree-root would couple sibling refine loops to ONE charter's busy-guard surface (strictly worse under the wedge analysis); per-session has no primitive. Under P2=(a) the context_key concept drops away — the "key" IS `current_instance_id` as the `get_children` argument.
- **Question:** what is the scope of "SAME caller"?
- **Options:** (a) per-caller-instance (`context_key = current_instance_id`) [default]; (b) per-tree-root / context_key (siblings share); (c) per-session/user (no primitive exists — `instances` rows carry no user id).
- **PROPOSED:** (a). Exact "same caller" semantics; fan-out is safe by construction — a leader's children each get their own charter; the leader's own calls share one; cross-talk impossible (analysis Axis 3a; Q7 recommendation = yes).
- **Flip impact:** (b) → key derivation moves into `_resolve_scope_key()` (seam reserved in Phase 1 T1); Phase 3 T5 sibling expectations INVERT (A and B share); cross-talk risk (sibling B sees A's diagram history) must be accepted in writing. (c) → blocked: requires new identity plumbing (out of scope).

### P4 — D-api-shape + default semantics (Q1: default reuse or opt-in?) → **ADJUDICATED (architect, 2026-09-10): CONFIRMED (a) `fresh: bool = False`, default = reuse**
- **Verdict rationale:** default-reuse IS the feature; the boolean matches existing kwarg style (`is_deferred`, `is_background`); sharpened justification — today's always-fresh default makes the documented "Refine, don't hand-edit" contract (`chart/skill.md:62`) a blind re-derivation, i.e. the current default is the bug and default-reuse fixes documented behavior.
- **Question:** kwarg shape, name, and whether reuse is the default.
- **Options:** (a) `fresh: bool = False` (default reuse) [default]; (b) `reuse: bool = True` (opt-in; default stays fresh); (c) `mode: Literal["reuse","fresh"] = "reuse"`; (d) separate `generate_chart_fresh()` tool.
- **PROPOSED:** (a) — the task states "successive calls … reach the SAME charter" as the goal, making default-reuse the feature; the default-semantics change is deliberate and documented (Phase 2), not a regression (analysis Axis 4a; R4.1). `fresh` matches existing boolean-kwarg style (`is_deferred`, `is_background`).
- **Flip impact:** (b) → every Phase 1 branch order inverts, Phase 2 wording inverts, T3's default path becomes the miss path, Phase 3 first/second-call expectations swap, conventions note (Phase 2 T3) dropped (no silent change). (c) → type/name changes ripple to T4/T5 error text + skill.md table. (d) → registration surface changes (`_tool_registry`), new tool-name pins; rejected at analysis level for discoverability but architect-owned.

### P5 — D-error-policy (ERROR/FAILED charters) → **ADJUDICATED (architect, 2026-09-10): CONFIRMED (b) one local revive attempt, then respawn**
- **Verdict rationale:** the counter's scope semantics exactly mirror the vetted v1-scope fix (`manager.py:2876-2886`) — recovers transient LLM/mmdc flakes while bounding thrash; a healthy-charter refine loop is not thrash (each iteration is context-grounded, vs today's blind fresh spawn); M8 (no terminate on reuse-timeout) and M14 (PAUSED busy-reject) both stand.
- **Question:** what happens when the discovered charter is ERROR/FAILED?
- **Options:** (a) always respawn fresh (never reuse a failed charter); (b) one revive attempt, then respawn [default]; (c) mirror the agent-tool ReviveGuard exactly (cumulative counter, same scope); (d) unguarded — always reuse any terminal status.
- **PROPOSED:** (b) via a local per-charter counter (module dict, precedent `manager.py:773`): ERROR/FAILED consumes; COMPLETED/TERMINATED free; after 1 consumed attempt → fresh spawn (`mode=reuse-respawn-after-failure`). Recovers transient LLM/mmdc flakes (`workflow.md:153-161` retry budget) while bounding thrash; counter lost on restart (accepted, precedent `manager.py:2792-2793`).
- **Flip impact:** (a)/(d) → Phase 1 T6 deleted (T3's status policy simplifies); Phase 3 T3 expectations change. (c) → counter moves/reshapes (see P8); semantics text in M7's log modes unchanged. All variants keep M8 (no terminate) and the ReviveGuard non-interaction pins (Phase 3 T2) — the agent-tool counter is never touched from this path (`manager.py:2832-2834`).

### P6 — D-charter-prompt-delta + A/B timing (Q6) → **ADJUDICATED (architect, 2026-09-10): CONFIRMED (a) no prompt/message change in v1; A/B post-merge**
- **Verdict rationale:** refinement works via checkpoint history alone (it carries the literal prior mermaid, reasoning, NEEDS-MORE-INFO rounds, mmdc warnings, style memory); the empirical one-variable-at-a-time A/B (M9) stays post-merge — Phase 1 C1 remains dormant; hybrid mini-history ideas are redundant under A.
- **Question:** does the charter need a refinement signal, and when is that decided?
- **Options:** (a) no prompt change in v1 [default]; (b) one-line message note on reuse calls (chart_tools message construction); (c) charter prompt edit (`rule.md`/`soul.md`/`workflow.md`).
- **PROPOSED:** (a) now; (b) as the first follow-up if empirical use shows the LLM missing refinement intent; A/B **post-merge** (analysis Q6 recommendation) — one variable at a time (M9 keeps fresh-spawn messages stable as the control).
- **Flip impact:** (b) → Phase 1 conditional task C1 activates (message trailing line + new pin; legacy `:150-153` substring pins survive). (c) → Phase 2 T2(c) activates: minimal edit, headings byte-stable (skill.md `:80` references charter soul.md `## My Expertise`), single-fenced-block contract untouched (`rule.md:24`, `workflow.md:165-207`), full integrity-gate run. `rule.md:31` isolation is filesystem-scoped and stays untouched in every branch.

### P7 — D-carrier-doc-surface (Q2) → **ADJUDICATED (architect, 2026-09-10): CONFIRMED (a) `chart/skill.md` only**
- **Verdict rationale:** the documented usage contract lives in the innate skill (auto-loaded by `innate_skills: ["chart"]` carriers); no other carrier prompt mentions `generate_chart`; the conventions.md note (Phase 2 T3) is CONFIRMED, and Phase 2 T1 additionally carries the wedged-charter operator guidance (busy-reject → `fresh=True` ladder; NO charter-terminate tool surface).
- **Question:** is `chart/skill.md` the only carrier-facing documentation update?
- **Options:** (a) skill.md only [default]; (b) skill.md + per-carrier prompt files (`agents/{name}/rule.md` for the ~15 carriers).
- **PROPOSED:** (a) — the documented usage contract lives in the innate skill (auto-loaded by `innate_skills: ["chart"]` carriers); grep shows no other carrier prompt mentions `generate_chart` (only `project-manager/tools_note.md:59`, a denial note, unaffected).
- **Flip impact:** (b) → Phase 2 T1 scope expands ×15 files, each through the integrity gate; risk of contradictory per-agent wording; recommend against unless a carrier-specific override exists.

### P8 — D-counter-location (Q3) → **ADJUDICATED (architect, 2026-09-10): CONFIRMED (a) `chart_tools.py` module-level dict**
- **Verdict rationale:** co-locating mechanism + policy in one file; manager centralization would push chart-specific semantics into the daemon core for a single consumer; the mandatory code comment cross-referencing `manager.py:773` ("SEPARATE mechanism from agent-tool ReviveGuard — programmatic paths never call `note_agent_tool_revive`, `manager.py:2832-2834`") stays.
- **Question:** where does the P5 revive counter live?
- **Options:** (a) `chart_tools.py` module-level dict [default]; (b) `manager.py` beside `_agent_tool_revive_counts` (`:773`).
- **PROPOSED:** (a) — tight blast radius; the counter is chart-path-only; co-locating mechanism + policy in one file (analysis Axis 1×5 interaction). Q3's discoverability concern is met with a code comment cross-referencing `manager.py:773` and a "SEPARATE mechanism" note (R5.3).
- **Flip impact:** (b) → attribute home moves; manager grows one dict + accessor (facade-neutral — attribute, not method kwarg, so M11 stays vacuous); Phase 1 T6/Phase 3 T3 follow.

### P9 — D-reuse-test-pattern (Q5) → **ADJUDICATED (architect, 2026-09-10): CONFIRMED (M3 shape) + 2 test additions + P2-fallout**
- **Question:** does the reuse branch keep the established test pattern (module-level patch surface moving from `invoke_agent_and_wait` to manager methods + completion-registry module attr)?
- **PROPOSED (now CONFIRMED):** yes — M3 is the concrete shape; the fresh path keeps patching `daemon.tools.chart_tools.invoke_agent_and_wait` (legacy parity), the reuse path patches `manager.enqueue_message`/repo stubs and the `daemon.services.completion_registry` MODULE attribute (lazy-import gotcha). Real-routing coverage moves to Phase 3 T1 (file-backed SQLite, `tests/test_governor_recursion_acceptance_walk.py:570-603` precedent — never in-memory StaticPool).
- **Adjudicated additions (incorporated into the phase files):**
  1. **Busy-guard-before-register ordering pin** (NEW unit test, Phase 1 T8.6): the in-flight check fires BEFORE `CompletionRegistry.register()` — two concurrent reuse calls → exactly one registers/enqueues; the rejected call observes NO register side-effect (two-waiter event-coalescing hazard, `completion_registry.py:79-81`).
  2. **ReviveGuard non-interaction pin** (Phase 3 T2): `manager.get_agent_tool_revive_count(charter_id) == 0` after a full reuse cycle, asserted for BOTH a COMPLETED-revive and an ERROR-revive.
  3. **Discovery-determinism test** (caller-flagged; Phase 1 T8.8): latest `last_activity_at` wins across multiple charter children (tie-break `created_at`, then id); real-routing echo in Phase 3 T1 step 6.
  4. From P2: fixture drops `shared_meta_kv_repo` stubs; the two KV pointer tests are deleted.

---

## Traceability snapshot

All nine architecture decisions ADJUDICATED (architect, 2026-09-10). Verdicts: P1 ✅ CONFIRMED · P2 🔀 FLIPPED to (a) pure query-discovery · P3 ✅ CONFIRMED · P4 ✅ CONFIRMED · P5 ✅ CONFIRMED · P6 ✅ CONFIRMED · P7 ✅ CONFIRMED · P8 ✅ CONFIRMED · P9 ✅ CONFIRMED + 2 test additions.

| Decision | Mirrors | Verdict | Gates (phase.task) |
|---|---|---|---|
| P1 | Q4 | CONFIRMED (a) | 1.T3, 3.T1, 3.T4.2 |
| P2 | Axis 2 (extra, beyond Q1–Q7) | **FLIPPED → (a)** | 1.T1 (sole store), 1.Task1 (stubs dropped), 1.T8 (pointer tests deleted), 3.T1 (discovery-only) |
| P3 | Q7 | CONFIRMED (a) | 1.T1, 3.T5 |
| P4 | Q1 | CONFIRMED (a) | 1.T4, 1.T5, 2.T1, 2.T3 |
| P5 | Axis 5 | CONFIRMED (b) | 1.T6, 3.T3 |
| P6 | Q6 | CONFIRMED (a) | 1.C1 (dormant), 2.T2 |
| P7 | Q2 | CONFIRMED (a) | 2.T1 (+ operator-guidance sentence) |
| P8 | Q3 | CONFIRMED (a) | 1.T6, 3.T3 |
| P9 | Q5 | CONFIRMED + additions | 1.Task1, 1.T8.6/T8.8, 1.T5, 3.T1, 3.T2 |
| M1–M14 | plan-level | stand (M3/M5 adjusted for P2 flip) | all phases (conventions) |
