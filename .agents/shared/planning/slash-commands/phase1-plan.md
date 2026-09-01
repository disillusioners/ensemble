# Phase 1 Plan (Backend): Slash-Command Subsystem + `/compact` On-Demand Compaction

Date: 2026-08-31 04:45 UTC (revised 2026-08-31 — architect verdict incorporated; revised 2026-08-31 — post-review adjudication (C1 + O8–O13) folded in)
Branch: feature/slash-commands @ 5e16f791
Author: plan-creation worker (backend phase) — decisions in `decisions.md` (D-B1…D-B12, O-B1…O-B13), research record in `research-findings.md`
Status: Final — architect-ratified + post-review adjudication folded (C1 + O8–O13, 2026-08-31); implementation-ready
Companion docs: `phase2-plan.md` (FE, other owner) pins to §WS-5 contract below. `technical-analysis.md` (other owner). **`architecture-recommendation.md` is the authoritative decision source (§1–§7 analyses, §7 normative wire schema, §8 verdict register, §9 required changes)** — all architect deltas below carry "(architect 2026-08-31)".

---

## Objective

A user types `/compact` in the FE chat box and the **selected instance's** conversation is compacted **on demand** through the **existing** `ContextCompactor` engine (force-bypassed **threshold** — the only bypass (architect 2026-08-31)), with an **adaptive LLM timeout** (base 90s, +60s/100k prompt tokens, cap 300s) that improves **both** existing compaction paths, a **trim-based fallback** so the user is never stuck, and an explicit **POST-ack + SSE progress contract** the FE phase can pin to.

Testable completion sentence: *On an IDLE instance, POSTing `/compact` returns a `command` ack within 500ms, compaction (or its trim fallback) completes within the adaptive cap, the next `GET /messages` shows `[Conversation Summary]` + preserved tail, and `command_progress` SSE events walked the FE through waiting → in_progress → success / timed_out → fallback_applied.*

## Scope

### In Scope (backend only)
1. **WS-1** — Router-level slash-command intercept (POST /messages) + extensible command registry (parse → validate → dispatch) + unknown-command ack + `//` escape passthrough.
2. **WS-2** — `/compact` executor service reusing `ContextCompactor.compact_state` with a `force` flag; per-instance model resolution; D3 persistence recipe; `compacted_at`; post-compact `emit_context_usage_for_instance`.
3. **WS-3** — Adaptive timeout replacing the 30s literal (compaction.py:1038), `wall_clock_cap_s` threading (:1011), whole-operation budget for chunked runs — general improvement to BOTH paths.
4. **WS-4** — Fallback semantics (existing `_truncate_fallback` + outcome reporting) + checkpoint-consistency requirements.
5. **WS-5** — SSE `command_progress` event + POST command-ack schema + GET fallback endpoint (the FE-pinned contract).
6. **WS-6** — Concurrency strategy (ExecutionGate, pause-first for RUNNING, terminal/injection/rate guards).
7. **WS-7** — Config knobs.
8. **WS-8** — Test strategy (unit + router integration; FE web-automation handoff note).

### Out of Scope (with reasons)
- **FE rendering/UX** — phase2-plan.md (other owner); we only pin the wire contract.
- **plan-overview.md / technical-analysis.md** — other instances own those artifacts.
- **Durable command execution (new JobItem type)** — JAFP: commands are ephemeral UI-triggered maintenance. Note: contrary to the prior plan wording, `enqueue_job` ACCEPTS `job_type='message'` (job_queue_service.py:574, special-cased at :646/:652/:773/:779 — the row is a pure mirror, PG trigger skips `job_locks`); the ephemeral precedent for THIS feature is RAM injection / question packs (transient internal ops that don't deserve a JobItem), not the message-mirror class. O-B7 ratified ephemeral WITH a structural seam (`command_id` + `handler` callable — task 2.9) so a future durable wrap needs no `CommandSpec` change (architect 2026-08-31).
- **Per-instance compaction config** — today ALL-GLOBAL (research §1); per-agent command policy hook is designed-in (predicate) but not populated (O-B6).
- **New compaction triggers for other surfaces** — watchover dormant summary stays dormant (watchover_service.py:173-207).
- **Async/fire-and-forget proactive compaction redesign** — out of scope; latency tension flagged O-B8.

---

## Work-Streams, Tasks, File-Level Targets

### WS-1: Slash-command subsystem (extensible)

**New file `daemon/services/command_dispatcher.py`** — registry + dispatcher + parser mirror the proven `daemon/sources/registry.py:47-159` pattern (register/get/list, duplicate-raise) (architect 2026-08-31, Q8 adopted):

| # | Task | Depends | Target / Evidence | Acceptance |
|---|------|---------|-------------------|------------|
| 1.1 | Define `CommandSpec` frozen dataclass: `name`, `description`, `availability: Callable[[InstanceContext], Awaitable[bool]]` (the O-B6 per-agent-policy hook), `rate_limit_per_instance`, `handler: Callable[..., Awaitable[CommandResult]]`; `CommandRegistry` + `CommandDispatcher` (register/get/list, duplicate-raise) | none | command_dispatcher.py; pattern source `daemon/sources/registry.py:47-159` | Registry resolves case-insensitively; unknown → None; duplicate registration raises |
| 1.2 | `parse_slash_command(text) -> ParsedCommand \| None`: **`//` escape checked BEFORE `/`** (O-B1 ratified — Slack convention): leading `//` → strip one `/`, return None (plain message); leading `/` → token split (`/compact [args]`) → ParsedCommand | 1.1 | command_dispatcher.py; architect §1/§8 O-B1 | `/compact` → ParsedCommand; `//etc/hosts` → None + sanitized text `/etc/hosts`; `hello` → None |
| 1.3 | Router intercept in `daemon/routers/messages.py` **after :240 (images validation), BEFORE :243 status capture** (4–6-line check): parse → if command: dispatcher → command ack; if None: fall through untouched. Status-branch bodies (:252/:402/:483-500) untouched | 1.1, 1.2 | messages.py:240-243 seam (verified on branch) | **Regression test asserting non-command traffic through messages.py:243-500 is byte-identical** (existing router suites + explicit marker test — architect §1 requirement) |
| 1.4 | Dispatcher ordering is LOAD-BEARING (architect §1): `//`-escape check → parse → registry lookup → availability → **rate limit BEFORE ExecutionGate acquisition** (acquiring the gate then releasing without work leaks latency) → ack → background task | 1.1, WS-6 | command_dispatcher.py | Ordering test: rate-limited request never acquires the gate |
| 1.5 | Unknown-command ack: 400 `ErrorResponse{code: UNKNOWN_COMMAND, detail: {available: [...]}}` (parse-time client error — §7 split rule). **O13 spec pin, 2026-08-31:** this is **additive, not a mirror** — keep the existing `messages.py:222-229` `{code, message}` envelope, add `code:"UNKNOWN_COMMAND"` + a NEW `detail:{available:[...]}` field. FE toasts on `code`, and `detail.available` later feeds slash autocomplete without a contract change. | 1.3 | messages.py; shape superset of :222-229 | `/foo` → 400 with available list; shape pinned in §WS-5 |
| 1.6 | Master switch: `slash_commands.enabled=False` → parse layer disabled entirely (`/x` flows as normal text) | 1.3 | config (WS-7) | Disabled mode passes existing tests unmodified |

**Anti-patterns (architect §1 — do not use):** router-level `_COMMANDS` dict (invisible to non-FE clients, requires spinning FastAPI to test); plugin-class ABC hierarchy (ceremony, scattered metadata).

**Rejected seams (recorded, do not revisit without architect):** dedicated `POST /commands` endpoint (approach B, weighted 3.30 — per-client parser duplication, two error-shape contracts); hybrid intercept+endpoint (approach C, 3.00 — two transports, two test matrices); enqueue_message_job payload sniff (defined manager.py:6428 — misses the RUNNING injection lane entirely); job-processor admission (post-commit redirect, queue latency); language_check_node pre-LLM drain (graph.py:2593-2679 — line numbers corrected 2026-08-31; the command would execute inside the turn whose checkpoint it rewrites — incoherent). Full rationale: decisions D-B2 + architect §1 matrix. **Flip conditions (recorded, not expected):** compliance-mandated POST byte-identity → C; a second programmatic client mandating an addressable endpoint → C as bridge.

**Extension contract for future commands:** new command = one `CommandSpec` registration (+ availability predicate + handler); zero router edits, zero dispatcher edits. Registry knows nothing about compaction.

### WS-2: `/compact` executor service (reuse + improve)

**New file `daemon/services/compact_executor.py`**; engine change in `daemon/compaction.py`:

| # | Task | Depends | Target / Evidence | Acceptance |
|---|------|---------|-------------------|------------|
| 2.1 | Add `force: bool = False` to `ContextCompactor.compact_state` (compaction.py:608-664): when True bypasses the **threshold check (:659-664) ONLY** (architect 2026-08-31 §2 — narrowed from dedup+min-messages+threshold). Min-messages (:645-651) and D9 60s dedup (:618-620) stay in-engine untouched. NEVER bypasses boundary groups (D2), D3 sentinel persistence, pairing guard, or terminal guard. `CompactionResult` gains additive dataclass defaults `forced: bool = False`, `failure_kind: str \| None = None` ("timeout"/"error") — all existing construction sites keep working; reactive None-check (graph.py:3514-3516) unaffected | 3.1 | compaction.py:608-664, CompactionResult (:234 — corrected 2026-08-31 from the prior `:233` near-miss); architect §2 gate table | Force compacts below threshold (the ONLY bypass); dedup + min-messages still apply; **anti-drift test: `forced=False` on BOTH auto paths** (proactive instance_messaging.py:1179, reactive graph.py:3513); default False → automatic paths byte-identical |
| 2.2 | Executor `execute_compact(instance_id, command_id) -> CompactOutcome`: resolve instance + graph config via InstanceManager (pattern: `emit_context_usage_for_instance` instance_messaging.py:1061-1114); status gating per WS-6; emit SSE phases per WS-5. **Executor pre-checks BEFORE any engine call** (architect §2): (a) read `compacted_at` — recency <60s → `success + noop + reason=recently_compacted` (no engine invocation, no LLM spend); (b) noop floor — estimated tokens < `SLASH_COMMANDS_NOOP_FLOOR_RATIO` (default 0.05) of the resolved per-instance window → `success + noop + reason=below_floor`. Engine `None` = "CAN'T compact"; below-floor = "WOULD but SHOULDN'T" — different semantics, engine stays single-purpose | 1.1, 2.1, WS-6 | compact_executor.py; knob WS-7 | Unit tests per pre-check row: noop results carry `compacted_type="noop"` + `noop_reason`; engine mock never invoked |
| 2.3 | Persistence recipe — REPLICATE proactive path exactly (instance_messaging.py:1190-1202): `aupdate_state({'messages': result.replacement_messages}, as_node='agent')` **then** `aupdate_state({'compacted_at': iso})`. D3: sentinels + summary in ONE messages call (direct list assignment CONCATENATES under add_messages — prior-plan decisions.md:40-63) | 2.2 | compact_executor.py; graph.py:2433-2438 (compacted_at declared schema field — D12, unknown keys silently dropped) | Integration test asserts exactly 2 aupdate calls, first carries RemoveMessage set + summary, second carries compacted_at |
| 2.4 | **Terminal-instance guard via shared helper (O-B4 DECIDED — architect §5; O8 spec pin, 2026-08-31)**: extract module-private `_is_terminal_checkpoint(...)` into a NEW small module `daemon/services/_checkpoint_utils.py`; the helper is imported by BOTH the proactive site (`instance_messaging.py:1146-1150`) AND the compact executor (anti-drift — prevents the two sites diverging). **Engine-reuse boundary preserved:** `compaction.py` stays free of checkpoint-state semantics; only `instance_messaging.py` and `compact_executor.py` import the helper. Executor: terminal → **REJECT `reason=terminal_instance`, `detail="Send a message to start a new turn, then /compact."`** — code-verified chain: terminal guard is load-bearing (aupdate on `next=()` clears run-state → astream instant-return → the :1132-1140 documented COMPLETED→RUNNING→COMPLETED collapse bricks revive-on-send); auto-revive (:1646-1682) does NOT call aupdate_state; revive-then-compact either no-ops or bricks; post-compact zombie RUNNING state. Flip condition (architect §5): dedicated compact-on-terminal lifecycle = separate refactor with regression tests proving revive-on-send still works | 2.2 | new file `daemon/services/_checkpoint_utils.py` + `instance_messaging.py:1146-1150` + `compact_executor.py` (compaction.py untouched) | Terminal-instance test: aupdate_state never invoked; rejection carries guidance detail; **shared helper used by both sites (source-level assert)**: `grep` `from daemon.services._checkpoint_utils import` finds exactly the two expected import sites |
| 2.5 | **Revive-brick regression test (architect §10 🔴 — currently unpinned)**: pin the guard's load-bearing behavior itself — `aupdate_state` on a `next=()` checkpoint → subsequent `astream` returns instantly (documented collapse :1132-1140). Makes the terminal guard's status testable and prevents future "harmless-looking" bypasses. **O17 implementation note, 2026-08-31:** the fixture MUST drive a real graph run (not mocks) — the brick mode is a property of the live checkpointer + aupdate_state + astream interaction; mocks can't reproduce the documented collapse. Use file-backed SQLite (tmp_path) — never StaticPool/in-memory | 2.4 | tests/unit (compaction or services suite) | Test reproduces the brick deterministically (real graph run, not mocks) and asserts the guard prevents it |
| 2.6 | **Per-instance model resolution for window math + O11 spec pin, 2026-08-31**: executor resolves the **instance's session model via the manager session-model accessor — the same source the summarization LLM client already uses (compaction.py:997-1008)** — then layers `context_window_overrides` (config.py:715-749) on top; window/floor math uses the resolved window (and the 2.2(b) noop floor measures against that window). **Global `config.llm.model` is used ONLY as a WARNING-logged fallback** (never silent — the warning carries instance_id + resolved window so auditability is preserved). Today proactive passes GLOBAL `config.llm.model` (instance_messaging.py:1160); per-model windows exist via `context_window_overrides` (config.py:715-749) | 2.2 | compact_executor.py | Test: instance on override-model compactes against the override window, not global; WARNING-logged fallback test asserts the fallback path logs a structured warning carrying instance_id + resolved window |
| 2.7 | Post-compact call `emit_context_usage_for_instance(instance_id)` (instance_messaging.py:1061-1114) — FE gets an immediate token-drop refresh (today the ONLY compaction-adjacent SSE) | 2.2 | compact_executor.py | SSE test: context_usage event observed after command_progress success |
| 2.8 | `compaction.enabled=False` (`_compactor` None, manager.py:409) → reject with `reason=compaction_disabled` | 2.2 | compact_executor.py | Test: rejected, engine never touched |
| 2.9 | **Durability seam (O-B7 ratified ephemeral)**: `command_id` + `handler: Callable` keep execution decoupled from persistence — a future durable variant wraps `handler` in a `JobItem('command')` enqueue without touching `CommandSpec`. Nothing built now | 1.1 | command_dispatcher.py / compact_executor.py | Code-review note; no queue-schema work in this phase |

### WS-3: Adaptive LLM timeout (general improvement, both paths)

All in `daemon/compaction.py` `_call_summarization_llm` (:980-1042) + `daemon/config.py`:

| # | Task | Depends | Target / Evidence | Acceptance |
|---|------|---------|-------------------|------------|
| 3.1 | Replace `timeout=30.0` literal (compaction.py:1027-1039; literal at :1038, verified) with `_summarization_timeout_s(prompt)`: `min(cap, base + (estimate_messages_tokens(prompt) / 100_000) * per_100k)`. **O17 implementation note, 2026-08-31:** extract the helper as a single named function `_summarization_timeout_s(prompt)` so the three call origins (:900 single-batch / :939 merge / :971 condense) share one definition (prior plan duplicated the inline expression three times). **Input = the PROMPT BEING SENT, computed inline at the `_call_summarization_llm` call site** (architect 2026-08-31 §3 Correction 1 — supersedes the plan's original `context.messages` input, which over-estimates every call after the first chunk and massively over-estimates merge/condense calls). Estimator: `estimate_messages_tokens` **daemon/loader.py:465** (tiktoken cl100k_base — unchanged; corrected path, see research-findings.md §correction) | none | compaction.py:1038 site + :900/:939/:971 call sites | Table-driven formula tests: 0→90s; 50k→120s; 100k→150s; 250k→240s; ≥350k→300s (cap); **per-origin tests: merge/condense prompts (tiny) get base timeout, not conversation-sized timeouts**; helper shared-by-three-sites test (single source of truth) |
| 3.2 | Thread `wall_clock_cap_s=<per-call cap + 5s margin>` into `wrap_langchain_failover` at :1011 (today capless → dead default 45s, llm_failover.py:529/:623/:706). **O17 implementation note, 2026-08-31:** `wall_clock_cap_s` is **PINNED to `inner_cap + 5`** — the 5 s margin is the difference between the inner per-call cap and the outer tenacity facade. Margin pinned at **+5s** (architect §9.8 — replaces the plan's "+15s" guess): wraps cleanly after the inner cancel; tenacity retries stay INSIDE the cap (llm_failover.py:559-568); keeps :1038 binding by design | 3.1 | compaction.py:1011 | Test: facade receives cap = site cap + 5 (PINNED); site TimeoutError still the first tripped |
| 3.3 | Whole-operation budget `COMPACTION_OPERATION_BUDGET_S` (default 300s): cumulative clock across chunk calls (batches of 20 groups :819-840; merges :846/:939/:958/:971) — today unbounded ≈ N×30s, after 3.1 would be N×300s. On exhaustion: stop issuing chunks, engine proceeds to existing truncate fallback for the remainder. Trips BETWEEN LLM calls ONLY — never between the two aupdate persistence calls (torn-write guard, D-B5/D-B6) | 3.1 | compaction.py chunk loop | Test: stubbed slow LLM, budget exhausts after k calls, fallback applies, result consistent; no aupdate interleaving possible |
| 3.4 | **NEW engine change — per-chunk timeout preservation + C1 typed outcome (architect §3 Correction 2 + post-review adjudication C1, 2026-08-31)**: replace the prior per-chunk `try/except TimeoutError` at `:838-840` (the plan's intermediate form) with a typed return: `_summarize_chunked` now returns `ChunkedOutcome(summaries, failed_batches, stop_reason ∈ "completed" | "timeout" | "error" | "budget")` instead of raising through to `:753-772` on per-chunk failure. Outer `:744-772` handler branches on `summaries` empty vs non-empty; no per-caller branching — **proactive/reactive paths get identical semantics**. Mid-run stop semantics (binding): (i) `|S| ≥ 1` → **partial path**: replacement = summaries(S) + truncation marker + preserved tail + injected (D2/D3 assembly rules). **B's messages are DROPPED — true trim of the un-summarized span** (bounded shrink guaranteed; reduction ≥ un-summarized span so the "user never stuck" criterion is provable). (ii) `|S| = 0` → existing `_truncate_fallback` fires unchanged (single-batch-timeout edge case — today's behavior, no new machinery). (iii) `compacted_at` stamps on BOTH paths (D12) — a partial is a completed compaction, not a failure. The per-chunk `except` is narrowed to `(TimeoutError, asyncio.TimeoutError)` (O14); an auto-path byte-identity anti-drift test asserts proactive/reactive still emit `forced=False` and the new `compacted_type` value only under timeout scenarios (`== "summary"` checks intentionally do NOT match partials — correct semantics). **WS-3.4 acceptance (a)–(d) — replaces prior task text (adjudication, 2026-08-31):** (a) first-batch timeout → `truncation`-typed result WITH marker, no summaries; (b) ≥2 batches, batch-2 timeout → `partial_summary` result: batch-1 summary present, batch-2 messages absent, marker present exactly once; (c) budget exhaustion mid-run → same assertions as (b) with stop_reason="budget"; (d) proactive + reactive callers observe identical outcome semantics on the same tests. **Benefits proactive AND reactive paths too.** | 3.1 | compaction.py:838-840 loop; today's loss site :753-772 | Regression + acceptance (a)–(d) tests; multi-batch run; proactive + reactive variants; auto-path byte-identity anti-drift |
| 3.5 | Verify both paths inherit the improvement (shared call site by construction: proactive instance_messaging.py:1179, reactive graph.py:3513 → `_call_summarization_llm`) | 3.1, 3.4 | test asserting captured timeout + per-chunk behavior from both callers | Two tests: proactive-context compaction and reactive-context compaction each observe adaptive timeout + partial-summary preservation |
| 3.6 | Config knobs (WS-7) + **O-B12 two-PR plan (architect §8) + O19 implementation note, 2026-08-31**: update constants.py:80-86 mirror in the SAME PR **ONLY for the existing knobs that already have a mirror entry**; the NEW `COMPACTION_TIMEOUT_*` knobs introduced by this feature (3.1, 3.2) do **NOT** require a new constants.py mirror entry — the mirror-deletion tidy PR targets the existing :80-86 entries only. The engine reads only config (`daemon/config.py`); adding mirror entries for new knobs would just create more dead duplicates that the deletion tidy PR would need to find and remove separately. Net effect: this feature's new knobs are added to `CompactionConfig` (config.py:706-715) with the env `COMPACTION_*` prefix and NO constants.py counterpart | 3.1 | config.py CompactionConfig; constants.py:80-86 unchanged for new knobs; follow-up tidy PR | Knobs documented in table below; **the existing mirror (constants.py:80-86) is touched ONLY for entries that existed before this feature**; new knobs appear ONLY in config.py; tidy PR filed |

**Latency ruling (O-B8 ACCEPTED as designed — architect §3):** Path A runs before the user's turn (instance_messaging.py:3570-3571) — turn-start may now wait up to the operation budget (≤300s, then a *completing* truncate) vs today's unbounded N×30s stall. Per-path overrides REJECTED (would dilute requirement #4); fire-and-forget rejected (turns would run against un-compacted history). Monitor via `context_usage` SSE. **Cancellation discipline (binding):** NO outer timeout around `compact_state`; cancellation only trips between engine LLM calls, never between the two `aupdate_state` calls (:1190-1202).

### WS-4: Trim fallback + checkpoint-consistency requirements

| # | Task | Depends | Target / Evidence | Acceptance |
|---|------|---------|-------------------|------------|
| 4.1 | Rely on EXISTING in-engine fallback: TimeoutError → compaction.py:753-772 → `_truncate_fallback` (:1081-1111) → `compacted_type="truncation"` + `summarization_error`. NO second fallback at executor layer (would double-trim and duplicate invariants — decision D-B5). **Q5 DECIDED (architect 2026-08-31 §4) + marker exactly-once (post-review adjudication C1, 2026-08-31)**: extract the single marker line into a shared helper `_append_truncation_marker(replacement)`; call it from BOTH `_truncate_fallback` AND the partial-path assembly (C1 mid-run stop with `|S| ≥ 1`). The two construction paths are mutually exclusive per result → exactly one marker per result, always. Marker line: `replacement.append(SystemMessage(content="[Earlier messages trimmed to fit context]", id=f"truncation-marker-{uuid4()}"))` — id-deterministic → `add_messages` reducer de-dups on re-compaction; no collision with the synthetic system message (persistence.py:404-449 prepend stays independent); pairing guard untouched (RemoveMessage + preserved only). **LLM summary-line variant REJECTED** — generating it requires an LLM call, re-triggering the exact failure (LLM outage/timeout) the fallback exists to escape; also a second fallback implementation in spirit. **Marker-side effect (O15):** because the marker now fires from BOTH `_truncate_fallback` AND the partial assembly, the auto-path truncation output changes (the auto paths previously had no marker line at all). Acknowledge explicitly + add a regression test pinning "marker present in `compacted_type='truncation'` auto-path output" so the cross-path behavior change is testable. | 2.1, 3.4 | compaction.py:1081-1111 + new helper; architect §4 + C1 | Marker present in `truncation` and `partial_summary` fallback output exactly once each; id starts `truncation-marker-`; re-compaction de-dups (no duplicate markers); synthetic system prepend unaffected; auto-path truncation now carries the marker (regression test, O15) |
| 4.2 | Executor maps engine result → outcome (post-review adjudication §7 amendment, 2026-08-31; supersedes the architect §4 two-row split). Three-way mapping per amendment (count = three: summary → success; partial_summary + truncation → `timed_out → fallback_applied` distinct by `compacted_type`; noop → success). See WS-5 §7 amendment table for the per-value copy. `failure_kind="timeout"` reports under `compacted_type ∈ {partial_summary, truncation}`; `failure_kind="error"` → `failed` (+ fallback note if fallback also applied); noop pre-check results → `success` with `compacted_type="noop"` + `noop_reason` (`below_floor` / `recently_compacted` / `too_few_messages`); truncation impossible (nothing compactable) → `failed`. | 2.1, 2.2 | compact_executor.py | Unit tests per mapping row incl. both noop reasons + the three terminal classes of the amendment |
| 4.3 | NO outer timeout around `compact_state` in executor — outer cancel could tear the two aupdate calls. The ONLY legal cancellation points are inside the engine between LLM calls (3.3 budget) | 3.3 | compact_executor.py | Code-review checklist item + test that executor task has no wait_for wrapper |
| 4.4 | Consistency checklist (test assertions): D3 sentinel single-write (one messages aupdate); pairing intact post-compact (no orphan tool_calls — engine D2 boundary groups :627-640 guarantee; cheap assert after persistence); terminal guard honored (2.4); summary id `compaction-<uuid4>` fresh per run (:903-906) — consumers relying on id stability see new ids (risk R-5); NO `truncated-<uuid4>` renames on the normal fallback path (rename only on emergency path :707-710) | 2.2 | tests | Checklist encoded as integration asserts |
| 4.5 | Synthetic-system-message safety: GET /messages prepends synthetic system independently (persistence.py:404-449) — compaction summary (a channel-value SystemMessage) must NOT clobber it. Assert channel_values after compaction contain summary but synthetic prepend untouched | 2.2 | persistence.py:404-449 | Regression test on GET /messages shape post-compact |
| 4.6 | Mirror rows: executor writes NO reconcile_turn_mirror rows → suppression rule (task/repository.py:780-827) not triggered; document in code comment | 2.2 | compact_executor.py | Comment + no mirror writes in tests |

### WS-5: SSE + API contract (**ARCHITECT-PINNED normative schema — §7 of architecture-recommendation.md, copied verbatim; changes require architect sign-off**)

**Split rule (reconciles O-B9/O-B10, architect §7): parse-time errors → HTTP 4xx; post-parse semantic refusals → 200 command envelope with `state:"rejected"` + reason.**

```ts
// POST /api/instances/{id}/messages → command ack (sync, ≤500ms)
type CommandAck = {
  status: "command"; command: string;          // "compact"
  command_id: string;                          // UUIDv4 — correlates ALL events
  state: "accepted" | "rejected";
  reason?: RejectionReason;                    // when rejected
  detail?: string;                             // human guidance (e.g. terminal-instance hint)
  timestamp: string;                           // ISO8601
  ttl_seconds: number;                         // GET-fallback memory window (default 600)
};
// RejectionReason = "terminal_instance" | "busy" | "rate_limited" |
//                    "pending_injections" | "compaction_disabled" | "quiescence_timeout"

// Unknown command → 400 (parse-time client error)
// ErrorResponse{code:"UNKNOWN_COMMAND", detail:{available:[...]}} — ADDITIVE (O13, 2026-08-31)
// over the existing :222-229 {code, message} envelope, not a mirror; FE toasts on code,
// detail.available later feeds slash autocomplete without a contract change.
```

```ts
// SSE event_type="command_progress" (LiveEventHub.stream_message, live-only, no replay)
type CommandProgressEvent = {
  instance_id: string; command_id: string;
  phase: "waiting" | "in_progress" | "success" | "timed_out" | "fallback_applied" | "failed";
  phase_seq: number;                           // monotonic per command — FE dedup/reorder guard
  timestamp: string; elapsed_ms: number;       // server clock = FE elapsed-timer source of truth
  eta_ms?: number;                             // advisory, in_progress only
  detail?: {
    tokens_before?: number; tokens_after?: number;
    compacted_type?: "summary" | "partial_summary" | "truncation" | "noop";
    failure_kind?: "timeout" | "error" | null;
    noop_reason?: "below_floor" | "recently_compacted" | "too_few_messages";
    checkpoint_id?: string; reason?: string;
  };
};
// Heartbeat: re-emit in_progress every 10s (phase_seq+1, fresh timestamp/elapsed_ms).

// §7 amendment (post-review adjudication, 2026-08-31): compacted_type enum gains
// "partial_summary". Three-way executor mapping + per-value FE copy table below.
// The phase machine is UNCHANGED — partial is a detail-level distinction;
// FE copy branches on compacted_type. failure_kind remains "timeout" | "error" | null.
// Budget exhaustion reports "timeout"; detail.reason may say "budget_exhausted".

// Three-way executor mapping (count = three):
//   summary              → success
//   partial_summary      → timed_out → fallback_applied  (distinct compacted_type)
//   truncation           → timed_out → fallback_applied  (distinct compacted_type)
//   noop                 → success (+ noop_reason)

// Per-value FE copy table (Task 6):
//   compacted_type      | SSE phases                          | FE terminal copy
//   --------------------+-------------------------------------+----------------------------------------------------
//   "summary"           | → success                           | "Context compacted"
//   "partial_summary"   | timed_out → fallback_applied        | "Compaction timed out partway — kept the summarized
//                       |                                     |  sections, trimmed the un-summarized older section"
//   "truncation"        | timed_out → fallback_applied        | "Compaction timed out — history was trimmed without
//                       |                                     |  a summary"
//   "noop"              | → success (+ noop_reason)           | "Nothing to compact"
```

```ts
// GET /api/instances/{id}/commands/active   (fallback for SSE loss; auth mirrors GET /messages)
type GetActiveResponse = { exists: false } | { exists: true; command: CommandProgressEvent };
// Daemon restart ⇒ {exists:false} ⇒ FE clears card silently. Poll ~5s while card active AND SSE dead.
```

**Phase machine:** `waiting` (accepted; F3: emitted BEFORE any pause mutation, D-B9) → `in_progress` (gate acquired / engine running; heartbeat re-emits every 10s) → terminal one-of: `success` | `timed_out` then `fallback_applied` | `failed`. Rejections are answerable at ack time (`state:"rejected"`); `timed_out → fallback_applied` maps from `failure_kind="timeout"` (WS-4 4.2).
**Emission discipline:** `_emit_injection_sse` pattern (messages.py:114-156) — flat payload, additive correlation id, try/except WARNING-swallow (best-effort, never fails API or compaction; D-B10).

**FE contract notes for phase2 (architect §7, P1-1…P1-6 deltas now pinned):** ack `accepted` → SSE `waiting` transition is normal (≤30s on RUNNING pause path); post-terminal refetch of `GET /messages` triggered by the terminal event; elapsed timers run off server `elapsed_ms` (survives reconnect); restart → `{exists:false}` → clear card silently (no error toast); F3 binding. 🟢 **Verify the SSE transport emits keepalives on idle** (proxy idle-timeout — see Risks R-16).

| # | Task | Depends | Target | Acceptance |
|---|------|---------|--------|------------|
| 5.1 | Implement CommandAck envelope + 400/200 split rule in messages.py intercept | 1.3, 1.5, 2.2 | messages.py | Schema test; unknown → 400 UNKNOWN_COMMAND + available list; refusals → 200 rejected + reason enum |
| 5.2 | Implement CommandProgressEvent phase machine + emission + 10s heartbeat in compact_executor.py | 2.2 | compact_executor.py | Order test incl. F3; heartbeat increments phase_seq monotonic; elapsed_ms server-clock |
| 5.3 | GET `/commands/active` endpoint + in-memory registry. **O10 spec pin, 2026-08-31:** the registry is owned by `CommandDispatcher` (`daemon/services/command_dispatcher.py`), with **one active slot per instance** + a **daemon-wide terminal ring LRU ≤ 20, TTL = `ttl_seconds` (600 default, mirrors ack field)**. Eviction triggers: terminal event, TTL expiry, and instance delete/terminate mirroring the existing `_pending_injections` cleanup path. **Keyed by `instance_id` (NOT FE session)** so FE instance-switching mid-command loses nothing — FE re-syncs via `GET /commands/active` on re-mount. Endpoint lives in `daemon/routers/instances.py` (O12); auth mirrors `GET /messages`; mounted **unconditionally** so `slash_commands.enabled=false` returns uniform 200 `{exists:false}` (FE contract invariant across config flips — no route-surface change when disabled). | 5.2 | `daemon/routers/instances.py` (GET `/commands/active`), `daemon/services/command_dispatcher.py` (registry) | Active-command test; `exists:false` when none/restart **and when `slash_commands.enabled=false`**; auth mirrors GET /messages (R-17); TTL eviction test (entry beyond TTL returns `exists:false`); LRU eviction test (daemon-wide ring caps at 20 terminal entries); instance-delete eviction test (mirrors `_pending_injections` cleanup) |

### WS-6: Concurrency strategy (decision D-B3; matrix corrected per architect 2026-08-31 §6)

| Status at command time | Path | Verdict / correction |
|---|---|---|
| IDLE | quiescence probe (`wait_for_instance_quiescent(timeout=0)`, registry, fast, never raises — manager.py:3362-3431) + `has_instance_busy` False → **take ExecutionGate** (execution_gate.py:108-143, run :118) → emit `in_progress` → compact. Gate blocks a turn starting mid-compaction | ✅ + **correction (architect §6): re-read `has_instance_busy` UNDER gate acquisition with retry-once** — the probe result is stale by read time; a turn may have started between probe and gate |
| WAITING_CHILDREN | child workers (sub-tasks) are still active. **O16 split, 2026-08-31:** treat as a quiescence PROBE only — call `wait_for_instance_quiescent(timeout=0)`; if quiescent, drop straight into IDLE path. **Do NOT** `pause_instance_cascade` and do NOT `graph_task.cancel()` (the children are legitimate work — cancelling them would orphan sub-tokens and violate the N1 sub-tokens invariant, see `.agents/shared/critical-notes`). The probe must be cheap (≤0 timeout) and never raise | ✅ |
| RUNNING | **pause-first**: emit `waiting` (F3) → `pause_instance_cascade` (instance_lifecycle.py:2685-2691; cascade_to_root default True — do not flip, 5 internal callers) → `wait_for_instance_quiescent(timeout=30)` → take gate → emit `in_progress` → compact → `resume_instance_cascade` (:2971, DB-only PAUSED→RUNNING; next dispatch is_retry=True replays checkpoint). Cancelled task stays PROCESSING, CancellationReason=pause (proven Watchover path, watchover_service.py:1004) | ✅ + **O9 spec pin, 2026-08-31:** wrap the **entire pause → quiesce sequence in ONE `try/except`**. ANY failure (timeout OR raised exception) → `rejected + reason=quiescence_timeout` with the exception **class name in `detail`** (single FE rendering, honest diagnosability — **do NOT add a second enum value** for raised exceptions). If pause half-succeeded, attempt best-effort `resume_instance_cascade` in a `finally` block **before** emitting the rejection (never leave a rejected command having mutated instance state; if resume itself fails, `detail` records `left-paused`). The async task **must never crash** — exceptions are swallowed into the rejected envelope. |
| **User-pauses-instance mid-compaction (O16 new row, 2026-08-31)** | the executor's 30 s quiescence wait may span a *user-initiated* pause that arrives while we still hold the gate. **The 30 s `wait_for_instance_quiescent` wait covers `asyncio.to_thread` workers** (those do NOT receive `CancelledError` — the wait itself is the cancel vector). Behavior: the wait returns when the instance actually quiesces (or times out → `quiescence_timeout` per O9). No new code path required; test reproduces the race: instance paused externally while executor is mid-quiesce → executor still proceeds once quiescence observed (or rejects per O9) | ➕ new row |
| PAUSED, no frozen task | effectively quiescent (checkpoint frozen; SQL claim gate `claim_pending_task` at task/repository.py:1146 excludes paused/terminated — no worker steals the turn). **Stale-busy note (O4):** the companion `has_instance_busy` (task/repository.py:543) counts PAUSED rows, so a "False at probe → True at gate-acquire" window is possible (benign: re-check under the gate with retry-once per the IDLE row). → take gate (blocks concurrent auto-resume turn, messages.py:252-378) → compact. Instance stays PAUSED (no state change we didn't make) | ✅ |
| PAUSED **with** frozen task | treat as PAUSED (frozen task holds no live astream; checkpoint frozen at node boundary) → gate → compact | ➕ **row was MISSING in the plan — added (architect §6)**; implementation check required → verification task V-1 below |
| Terminal (COMPLETED/ERROR/FAILED/TERMINATED) | **REJECT** `reason=terminal_instance`, `detail="Send a message to start a new turn, then /compact."` — O-B4 DECIDED (architect §5, code-verified chain; see task 2.4) | ✅ decided — was flagged, now binding |

**Interaction rulings (architect §6):**
- **Queued messages during compaction**: their next turn blocks on the gate until compaction finishes — safe (serialized), bounded (≤300s), but user-visible latency. Document in FE copy; no code change.
- **Second command mid-command**: rate-limit answers it — `rejected + reason=busy` if in-flight, `rate_limited` if inside min-interval. **Idempotency ruling: every POST gets a NEW `command_id`; no idempotency-key machinery** (compaction converges — the recency pre-check 2.2(a) makes a duplicate a cheap noop).
- **Instance terminated/deleted mid-command**: executor catches the persistence failure and emits terminal `failed` — never hangs, never leaves the in-memory registry entry active.
- **Daemon restart mid-command**: registry lost by design (D-B8 ephemeral). `GET /commands/active` returns `{exists:false}`; FE clears the card silently (phase2 pins this).
- **Crash between the two `aupdate_state` calls**: benign (summary present, `compacted_at` missing) — same exposure window as the existing proactive path (:1190-1202). Ratified.
- **Revive-on-send racing `/compact` on a terminal instance**: impossible post-O-B4 (terminal = rejected before any task starts).

Additional guards:
- **RAM injection queue** non-empty (`_pending_injections` — defined manager.py:643, accessed manager.py:2462+, :2528, :3638; corrected 2026-08-31 from the prior `:2398` near-miss) → reject `reason=pending_injections` (O-B11 ratified: drain would couple injection delivery to compaction persistence; retry is cheap).
- **Rate limiting (O-B13 ratified — the ONLY abuse guard)**: 1 in-flight per instance (`busy`) + 10s min-interval per instance (`rate_limited`), config-tunable, **checked BEFORE gate acquisition** (dispatcher ordering, task 1.4). The executor recency pre-check (2.2a) handles duplicate-value protection; the engine's 60s dedup (:1113-1130) stays untouched for auto paths.

**Verification tasks (architect §9.7 + §10 🟡 — WS-6 EXIT CRITERIA):**

| # | Task | Depends | Target | Acceptance |
|---|------|---------|--------|------------|
| 6.1 | Status-gating matrix above in executor (incl. PAUSED-with-frozen-task row, IDLE re-check-under-gate, quiescence_timeout) | 2.2 | compact_executor.py | Integration tests per row |
| 6.2 | Injection-queue, in-flight (`busy`), min-interval (`rate_limited`) guards, ordered before gate | 1.4, 2.2 | command_dispatcher.py, compact_executor.py | Guard tests; **rapid-click race integration test (double-POST within min-interval — architect §10 🟡)** |
| 6.3 | Mid-command termination/deletion handling → terminal `failed` + registry cleanup | 2.2 | compact_executor.py | Test: instance deleted mid-engine-call → `failed` event, no hang, registry entry gone |
| **V-1** | **Verify ExecutionGate release on pause-cancel + resume-path gate coverage (architect §9.7 🟡)**: confirm the gate is released when `pause_instance_cascade` cancels the graph task, AND that ALL resume entry points (`is_retry=True`) re-acquire through the gate. Worker D could not fully verify resume-path gate coverage | 6.1 | execution_gate.py, instance_lifecycle.py, task_processor.py | Written verification note in this file (or findings doc); if a resume entry point bypasses the gate → fix + test BEFORE WS-6 exit |
| **V-2** | **Load-check tenacity facade behavior at ~305s wall clock (architect §10 🟡)**: retry semantics at high caps were read (llm_failover.py:559-568) but not load-tested — confirm a cap+5s facade call retries/cancels sanely at the new ceiling | 3.2 | llm_failover.py; test with stubbed slow LLM | Documented behavior at ~305s; no retry storm, no unbounded overrun past cap+5s |

### WS-7: Config

`daemon/config.py` — extend `CompactionConfig` (env `COMPACTION_*`, pattern verified config.py:706-715) + new `SlashCommandConfig` (env `SLASH_COMMANDS_*`):

| Knob | Default | Purpose |
|---|---|---|
| `COMPACTION_TIMEOUT_BASE_S` | 90 | adaptive base (3.1) |
| `COMPACTION_TIMEOUT_PER_100K_TOKENS_S` | 60 | scaling (3.1) |
| `COMPACTION_TIMEOUT_CAP_S` | 300 | hard per-call cap (3.1) |
| `COMPACTION_TIMEOUT_FACADE_MARGIN_S` | 5 | wall_clock_cap_s margin (3.2) — **pinned +5s per architect §9.8** |
| `COMPACTION_OPERATION_BUDGET_S` | 300 | whole-op budget across chunk calls (3.3) |
| `SLASH_COMMANDS_ENABLED` | true | master switch (1.6); false = `/x` is plain text |
| `SLASH_COMMANDS_ESCAPE_PREFIX` | `//` | escape convention (O-B1 ratified — checked BEFORE `/`) |
| `SLASH_COMMANDS_MIN_INTERVAL_S` | 10 | per-instance rate limit (O-B13) |
| `SLASH_COMMANDS_NOOP_FLOOR_RATIO` | 0.05 | executor noop floor as fraction of resolved per-instance window (2.2b) — **5% is a tuning guess, expect adjustment (architect §2)** |
| `SLASH_COMMANDS_STATE_TTL_S` | 600 | GET-fallback registry retention (`ttl_seconds` in ack) |
| `SLASH_COMMANDS_MAX_STATE_PER_INSTANCE` | 20 | registry bound |

Also mirror the **existing** compaction defaults into constants.py:80-86 **in the same PR**, and schedule mirror DELETION as a separate follow-up tidy PR (O-B12 architect ruling — 6-month drift argues for removal). **O19 (2026-08-31):** the NEW `COMPACTION_TIMEOUT_*` knobs from this feature (table above) do NOT require a constants.py mirror entry — the engine reads only config. The two-PR deletion applies ONLY to the existing :80-86 entries.

### WS-8: Test strategy

**Unit (extend `tests/unit/test_compaction.py`; new `tests/unit/services/test_compact_executor.py`, `tests/unit/services/test_command_dispatcher.py`):**
- **Patch discipline:** lazy imports live in function body (:994-995) — patch **`daemon.graph`** for `ThinkingChatOpenAI`/`clean_llm_config`, **NEVER** `daemon.compaction`.
- **DB discipline:** file-backed SQLite (tmp_path) — never StaticPool/in-memory (repo write-corruption hazard; production PG unaffected).
- **Force flag (narrowed — architect §2):** bypasses threshold (:659-664) ONLY; dedup (:618-620) and min-messages (:645-651) still apply under force; **anti-drift: `forced=False` asserted on BOTH auto paths** (proactive instance_messaging.py:1179, reactive graph.py:3513); None-result semantics unchanged for reactive re-raise (graph.py:3514-3516); default False → automatic paths byte-identical.
- **Executor pre-checks:** `compacted_at` recency <60s → `success + noop + reason=recently_compacted` (engine mock NOT invoked); below-floor (<5% resolved window) → `success + noop + reason=below_floor`; knob-driven ratio.
- **Adaptive timeout:** 3.1 formula table (0→90s, 50k→120s, 100k→150s, 250k→240s, ≥350k→300s); **per-origin tests — merge (:939)/condense (:971) prompts get base-scale timeouts, not conversation-sized**; 3.2 facade cap = site cap + 5s; 3.3 budget → fallback, no torn persistence; **V-2 load-check at ~305s wall clock**.
- **Per-chunk preservation + C1 acceptance (3.4 + adjudication, 2026-08-31):** table-driven suite covering acceptance (a) first-batch timeout → `truncation` + marker + no summaries; (b) ≥2 batches, batch-2 timeout → `partial_summary` + batch-1 summary + batch-2 raw messages absent + marker exactly once; (c) budget exhaustion → same as (b) with stop_reason="budget"; (d) proactive + reactive identical outcome semantics. D3-safe; proactive + reactive variants.
- **Marker exactly-once (4.1 + adjudication, 2026-08-31):** marker present in `truncation` AND `partial_summary` outputs, exactly once each, id starts `truncation-marker-`; re-compaction de-dups via `add_messages`; auto-path truncation now carries the marker (regression, O15).
- **Auto-path enum (O14, 2026-08-31):** byte-identity anti-drift test — proactive + reactive still emit `forced=False` and `compacted_type != "partial_summary"` under no-timeout scenarios; the new `partial_summary` value is asserted only under timeout/budget scenarios.
- **Timeout→fallback transition:** TimeoutError → `compacted_type="truncation"` + `failure_kind="timeout"` → executor maps `timed_out`→`fallback_applied`; `failure_kind="error"` → `failed`.
- **Truncation marker (Q5):** marker line present exactly once in fallback output; id `truncation-marker-*`; re-compaction de-dups via add_messages (no duplicate markers).
- **Terminal guard + revive brick (2.4/2.5):** `_is_terminal_checkpoint` shared by proactive site + executor (source-level assert); **revive-brick regression: `aupdate_state` on `next=()` checkpoint → subsequent `astream` instant-return** (pins the guard's load-bearing status — architect §10 🔴 unpinned item).
- D3/D12 regressions: sentinel single-write (direct-list CONCATENATES — prior decisions.md:40-63); `compacted_at` persisted via declared schema field (graph.py:2433-2438).
- Parse/dispatch layer: `/compact`, args, `//`-BEFORE-`/` passthrough, case-insensitivity, unknown → None; duplicate registration raises (sources/registry pattern).

**Router integration (pattern: `tests/unit/routers/test_message_status_endpoint.py`; extend or sibling file):**
- **Byte-identity regression: non-command traffic through messages.py:243-500 unchanged** (architect §1 requirement — explicit marker test).
- Intercept on EVERY status branch: IDLE (enqueue seam replaced by command ack), RUNNING (injection seam), PAUSED incl. with-frozen-task (auto-resume seam), terminal (revive seam) — command must short-circuit before each.
- `waiting` emitted BEFORE pause mutation (F3 order); phase sequence waiting→in_progress→success; **heartbeat: in_progress re-emitted at ~10s with phase_seq+1; phase_seq strictly monotonic across the whole command**.
- **Rate-limit ordering: rate-limited request NEVER acquires the ExecutionGate** (dispatcher ordering test); **rapid-click race: double-POST within min-interval → exactly one accepted, second `busy`/`rate_limited`** (architect §10 🟡).
- GET `/commands/active`: authoritative state after SSE "loss"; **`exists:false` after daemon restart (registry lost by design)**; auth mirrors GET /messages (R-17).
- 400/200 split: unknown command → 400 UNKNOWN_COMMAND + available list; `//path` passthrough reaches normal branch.
- Guards: pending-injections reject; compaction-disabled reject; **terminal reject carries guidance detail** ("Send a message to start a new turn, then /compact."); aupdate_state never called in any reject case (mock count 0).
- Quiesce-failure path: pause/quiesce timeout → `rejected + reason=quiescence_timeout`, async task alive, ack not hung.

**Existing suites to keep green (no behavioral change when disabled/not-forced):** tests/unit/test_compaction.py, tests/unit/test_compaction_multimodal.py, tests/unit/services/test_execution_gate.py, tests/unit/services/test_compact_fired_watchers_deliver_before_compact.py.

**FE web-automation handoff (for tester, phase 2):** frontend/e2e Playwright exists; NEVER networkidle (notification stream stays open) — use domcontentloaded + waitForSelector; model on send-pause-button.spec.ts; Jest logic-mirror units without TestBed.

---

## Coupling Map

| | WS-1 | WS-2 | WS-3 | WS-4 | WS-5 | WS-6 | WS-7 | WS-8 |
|---|---|---|---|---|---|---|---|---|
| WS-1 | — | **tight (dispatcher ordering is load-bearing: rate-limit BEFORE gate — task 1.4 ↔ WS-6; handler seam → executor)** | independent | loose | tight (ack schema) | **tight (ordering 1.4)** | tight (enabled knob) | — |
| WS-2 | tight | — | tight (force flag + failure_kind + adaptive timeout) | **tight (`_is_terminal_checkpoint` shared helper 2.4; noop outcomes → 4.2 mapping)** | tight (phases) | tight (gating) | **tight (NOOP_FLOOR_RATIO knob)** | — |
| WS-3 | indep. | tight | — | **tight (per-chunk try/except :838-840 ↔ fallback :753-772 → marker line — 3.4 ↔ 4.1)** | loose | **tight (V-1/V-2 verification tasks)** | tight (knobs) | — |
| WS-5 | tight | tight | loose | loose | — | loose (F3 ordering) | loose (ttl knob) | — |

Cross-phase (FE): **WS-5 is the only FE-facing surface** — the §7 normative schema (ack, progress event, GET active) is ARCHITECT-PINNED; FE (phase2-plan.md) builds against it; backend change to any field = architect sign-off.

Follow-ups register (architect 2026-08-31): **O-B12 second PR** — constants.py compaction-mirror DELETION (tidy PR after the feature PR); WS-6 V-1/V-2 verification notes land in this directory before implementation sign-off.

Sequencing: 3.1–3.4 (WS-3) can start immediately (pure engine improvement — incl. per-chunk preservation); WS-1 skeleton parallel; WS-2 after 2.1 (force flag + additive fields) and 2.4 (terminal helper); WS-4 after 2.1/3.4; WS-5/WS-6 after WS-2; WS-8 tracks each; V-1/V-2 gate WS-6 exit.

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| R-1 | � Checkpoint race: compaction writes while a turn's astream commits → silent overwrite / corrupt message list (no merge exists). **The ExecutionGate is the ONLY defense** — it blocks graph runs, not arbitrary checkpoint writes; any future path writing checkpoints without the gate breaks BOTH compaction paths (architect §10) | High | Medium (High without gating) | ExecutionGate mandatory (D-B3); status gating per WS-6; integration tests per status branch; quiescence probes before gate + re-check under gate (task 6.1); **code comment at the gate layer** warning future checkpoint writers (architect §10 mitigation); V-1 verification. **O18 implementation note, 2026-08-31:** R-1 is **non-interaction safe by construction** — two `/compact` POSTs cannot race the gate (O-B13 rate-limit answers the second as `busy` or `rate_limited` BEFORE the gate is acquired; dispatcher ordering task 1.4). A user pause mid-compaction is covered by the WAITING_CHILDREN/O16 row; a daemon crash mid-compaction is covered by D-B8 (registry lost by design). The ExecutionGate is the single serialization point that makes this risk category impossible at the wire — verify with the WS-6 integration tests per status branch |
| R-2 | Long-running op (≤300s budget): HTTP proxies/FE drop; SSE disconnects lose events | Medium | High | Async ack (D-B7) — POST returns in ms; GET fallback authoritative state; command_id correlation survives reconnects; FE reconnect-refetch contract |
| R-3 | LLM outage → destructive trim with NO summary (pre-existing :753-772 behavior, now user-triggered) | Medium | Medium | Distinct `timed_out`/`fallback_applied` reporting + detail text; preserve preserved-window invariants (D5); O-B8 monitors; trim is still better than an over-context turn that fails outright |
| R-4 | Pause-first freezes a RUNNING turn mid-node (user-visible pause; queued turn replays after) | Medium | Medium | Bounded quiescence 30s; F3 `waiting` before pause; proven Watchover precedent; resume is checkpoint-exact; alternative reject-when-running flagged O-B3 |
| R-5 | ID drift: summary ids fresh per run (`compaction-<uuid4>` :903-906); RemoveMessage needs ids (:1060-1066); consumers keying on message ids see churn | Low | Low | Ids unchanged for preserved tail; only compacted range replaced — same as automatic path today; no new consumer contract |
| R-6 | SSE event loss on reconnect → FE stuck in `in_progress` | Medium | Medium | GET fallback endpoint + FE reconnect-refetch rule (§WS-5); TTL-bounded registry |
| R-7 | Command flood / LLM cost abuse | Medium | Low | Rate limiting ratified (O-B13): 1 in-flight per instance (`busy`) + 10s min-interval (`rate_limited`), checked BEFORE gate; **the ONLY abuse guard** — force no longer raises dedup concerns (force bypasses threshold only; executor recency pre-check makes duplicate spam a cheap noop, architect §8); rapid-click race covered by integration test (R-12) |
| R-8 | `compaction.enabled=False` → `_compactor` None (manager.py:409) → NPE risk in executor | Low | High (if unhandled) | Explicit reject `compaction_disabled` + test (2.7) |
| R-9 | Proactive path turn-start latency rises (30s → 90–300s budget) on big contexts | Medium | Medium | Operation budget bounds it; knobs tunable; flagged O-B8 with options; data-integrity tradeoff is requirement #4's intent |
| R-10 | Torn persistence: outer cancel between the two aupdate calls | Medium | Low | NO executor-level outer timeout (4.3); engine budget trips only between LLM calls (3.3); residual crash-window exposure accepted (D-B8) |
| R-11 | Silent write failure of planning/code artifacts (repo hazard) | Medium | Medium | Post-write grep-verify + `git diff` as ground truth; python read-modify-write with count==1 assertions for multi-edits (repo convention) |
| R-12 | 🟡 Rate-limit rapid-click race: concurrent double-POST within min-interval | Medium | Medium | Integration test: double-POST within min-interval → exactly one accepted, second `busy`/`rate_limited` (WS-8; architect §10) |
| R-13 | 🟡 Resume-path gate coverage unverified: whether ALL resume entry points (`is_retry=True`) re-acquire the ExecutionGate | High (if a bypass exists) | Medium | **Verification task V-1** gates WS-6 exit (architect §9.7): verify gate release on pause-cancel + every resume path; fix + test if a bypass is found |
| R-14 | 🟡 Facade tenacity behavior at ~305s wall clock (high caps read but not load-tested) | Medium | Medium | **Verification task V-2** gates WS-6 exit: stubbed slow-LLM load-check at cap+5s; no retry storm, no unbounded overrun |
| R-15 | 🟢 Noop-floor ratio (5% of resolved window) is a tuning guess — too high silently refuses small compactions, too low generates noisy tiny summaries | Low | Medium | Knob `SLASH_COMMANDS_NOOP_FLOOR_RATIO` (architect §2: "expect tuning"); noop results carry `noop_reason=below_floor` so tuning is observable in logs |
| R-16 | 🟢 SSE keepalive-on-idle unverified: proxies may idle-timeout the stream during a long in_progress stretch | Low | Medium | Verify transport keepalives (architect §7 note); 10s heartbeat re-emits give liveness signal; GET `/commands/active` fallback unaffected |
| R-17 | 🟢 GET-fallback auth must mirror `GET /messages` auth gates — a weaker gate would leak command/instance state | Medium | Low | Task 5.3 acceptance: auth parity asserted in tests (architect §7) |

---

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| S-1 | POST `/compact` on IDLE quiescent instance acks immediately | integration test timing around POST | 200 `{status:"command", state:"accepted", command_id}` < 500ms |
| S-2 | Compaction completes within adaptive cap | integration test, stubbed LLM, ~100k-token fixture context | success (or fallback_applied) ≤ 150s simulated; wall-clock test < 5s with stub |
| S-3 | Post-compact GET /messages shape | integration test | synthetic system msg intact + `[Conversation Summary]` system msg + preserved tail; preserved-tail count ≤ recent_message_window groups (D5) |
| S-4 | FE-visible token drop | `emit_context_usage_for_instance` called post-compact (2.6) | context_usage event after success, tokens_after < tokens_before |
| S-5 | Timeout → fallback semantics | unit + integration, slow/timeout stub LLM | phases exactly `waiting→in_progress→timed_out→fallback_applied`; result consistent (pairing intact, sentinels single-write); **"user never stuck" criterion now provable (post-review adjudication C1, 2026-08-31): reduction ≥ un-summarized span under the partial-summary path**; for the first-batch edge case (|S|=0) `compacted_type="truncation"` is emitted with the marker (acceptance (a)) |
| S-6 | Adaptive timeout improves BOTH paths | test capturing timeout at shared call site from proactive + reactive fixtures | both observe formula value, not 30s |
| S-7 | Force flag bypasses THRESHOLD ONLY, automatic paths untouched | unit tests | force executes below threshold (:659-664); dedup (:618-620) + min-messages (:645-651) still apply under force; **`forced=False` asserted on both auto paths**; existing suites green (default False) |
| S-8 | Unknown + escape handling | router tests | `/foo` → 400 UNKNOWN_COMMAND with available list; `//path` delivered as literal `/path` |
| S-9 | Unsafe targets refused, zero checkpoint mutation | router+executor tests, aupdate_state mock | terminal → rejected **with guidance detail**; pending-injections → rejected; compaction-disabled → rejected; rate-limit second call → `busy`/`rate_limited`; aupdate call count 0 in all reject cases |
| S-10 | F3 ordering | SSE emission-order test | `waiting` event recorded before any pause_instance_cascade invocation |
| S-11 | No regression when feature off | full unit suite with slash_commands.enabled=False | 100% of pre-existing tests pass unmodified |
| S-12 | Formula table | 3.1 table-driven tests | 0→90s, 50k→120s, 100k→150s, 250k→240s, ≥350k→300s; merge/condense origins get base-scale timeouts (per-prompt input) |
| S-13 | **Partial-summary preservation + C1 acceptance (a)–(d)** (post-review adjudication, 2026-08-31) | multi-batch fixture; proactive + reactive variants; batch-2 timeout + budget-exhaustion scenarios | batch-1 summary present in replacement + batch-2 raw messages absent + marker present exactly once (acceptance (b)); budget exhaustion → same assertions with stop_reason="budget" (acceptance (c)); proactive + reactive observe identical outcome semantics (acceptance (d)); auto-path byte-identity anti-drift: `forced=False` and `compacted_type != "partial_summary"` under no-timeout scenarios (O14) |
| S-14 | Terminal reject with guidance (O-B4) | router test on terminal instance | 200 ack `state:"rejected"`, `reason=terminal_instance`, detail = "Send a message to start a new turn, then /compact."; aupdate_state never invoked; revive-brick regression test green (2.5) |
| S-15 | Restart semantics | integration test: command in flight → simulated restart → GET `/commands/active` | `{exists:false}` returned; no stale registry hit |
| S-16 | Noop paths (architect §2) | executor unit tests | recency <60s → `success + noop + recently_compacted` (engine untouched); below-floor → `success + noop + below_floor`; both surface `compacted_type="noop"` in SSE detail |
| S-17 | Wire-schema conformance | schema tests vs §7 | ack carries `phase_seq`-correlated `command_id` UUIDv4 + `ttl_seconds`; progress events carry monotonic `phase_seq` + server-clock `elapsed_ms`; heartbeat at ~10s with `phase_seq+1`; rejection reason enum exactly §7's six values |
| S-18 | Verification tasks closed (architect 🟡) | V-1 + V-2 evidence in this directory | resume-gate coverage verified (all `is_retry=True` entry points acquire gate); facade behavior at ~305s documented + load-checked |

---

## Decisions & Open Items

All 13 open questions (O-B1…O-B13) are **DECIDED by the architect (2026-08-31)** — verdicts recorded in `decisions.md` §"Decided by architect" with the §8 basis; no open decision remains from the original register. What remains open is VERIFICATION, not decision (architect §10):

- **V-1 (🟡)** ExecutionGate release on pause-cancel + resume-path (`is_retry=True`) gate coverage — worker D could not fully verify.
- **V-2 (🟡)** Tenacity facade behavior at ~305s wall clock (high caps load-checked).
- **Revive-brick regression test (🔴→task)** — pin `aupdate_state` on `next=()` → `astream` instant-return (task 2.5).
- 🟢 tracked risks: noop-floor tuning (R-15), SSE keepalive (R-16), GET-fallback auth parity (R-17), rapid-click race test (R-12).

## Exit Criterion

All WS-1…WS-8 tasks acceptance-checked; S-1…S-18 green; **V-1 and V-2 verification evidence filed (WS-6 exit criteria)**; §WS-5 ARCHITECT-PINNED contract published unchanged into the architect's plan-overview for phase2 (FE) to build against; constants.py-mirror-deletion tidy PR registered as a follow-up (O-B12).
