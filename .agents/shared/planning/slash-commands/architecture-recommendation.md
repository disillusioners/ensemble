# Architecture Recommendation: Slash-Command Subsystem + On-Demand `/compact`

Date: 2026-08-31
Branch: `feature/slash-commands`
Status: **Complete** — all baselines ratified or corrected; all 13 open questions + 9 tech questions decided
Produced by: Architect (controller) — aggregation of 4 skill-equipped worker analyses:
`1efd887c` (trade-off-analysis, seam/dispatch) · `5651dee5` (structural-design, reuse/registry) · `5bf8d086` (resilience-design, timeout/fallback/terminal) · `c205ebcf` (data-flow-design, wire contract/concurrency)
Inputs: plan-overview.md, decisions.md (D-B1…D-B12), technical-analysis.md (Q1–Q9), phase1-plan.md, phase2-plan.md, research-findings.md

---

## 0. Executive Summary

The plan's composite baseline is **architecturally sound and is ratified** with **two corrections to the adaptive-timeout design**, **one narrowing of the `force`-flag scope**, and **two binding decisions** (Q5, O-B4). The dominant finding across all four analyses: the plan's evidence base is accurate — every load-bearing claim (binding cap at `compaction.py:1038`, terminal-guard at `instance_messaging.py:1146-1150`, gate-only checkpoint defense, SSE no-replay) was verified against the code. The plan's own baselines were correct on 10 of 13 open questions; the three that change are O-B5 (force scope narrowed), plus two engineering gaps the plan missed entirely (partial-summary loss on mid-chunk timeout; per-prompt token estimation).

**Top decisions (details in §1–§7, full register in §8):**

| # | Decision | Verdict |
|---|----------|---------|
| 1 | Intercept seam | **A: router intercept inside POST /messages (:240→:243) + service-layer dispatcher** — weighted 4.35 vs 3.30 (dedicated endpoint) / 3.00 (hybrid). Dominant axis: Maintainability |
| 2 | Reuse mechanism | **Additive `force: bool = False` on `compact_state`** — bypasses **threshold only** (narrowed from plan's dedup+min-messages+threshold); executor owns recency + noop-floor pre-checks |
| 3 | Adaptive timeout | **Per-call cap at :1038 + facade margin at :1011 + whole-op budget between LLM calls** — with 2 corrections: token estimate per-**prompt** (not `context.messages`); per-chunk try/except preserving partial summaries |
| 4 | Q5 fallback variant | **DECIDED: plain `_truncate_fallback` + `failure_kind` + ONE id-deterministic marker line inside the existing function.** The LLM summary-line variant is rejected (re-triggers the failure being escaped) |
| 5 | O-B4 terminal instances | **DECIDED: REJECT with `reason=terminal_instance` + user guidance.** Revive-then-compact is unsafe: revive does not un-terminalize the checkpoint; bypassing the guard bricks revive-on-send (`:1132-1140` documented collapse) |
| 6 | Wire contract | **Normative schema adopted** (§7): `phase_seq`, `elapsed_ms`, `eta_ms`, `ttl_seconds`, rejection-reason enum, `GET /commands/active` with `{exists:false}` restart semantics, 400-vs-200 split |

---

## 1. Command Subsystem Architecture (Focus 1 — seam + dispatch)

### Verdict: Approach A — router intercept + service-layer dispatcher (ratifies D-B2)

Full 5-axis × 3-approach matrix in `approach-comparison.md`. Summary:

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Weighted | Recommendation |
|----------|------------|-------------|-----------------|------|------|----------|----------------|
| **A: Router intercept + service dispatcher** (baseline) | 4 | 5 | **4** | 4 | 5 | **4.35** | ✅ **Adopt** — one transport, zero router edits per future command, one FE contract |
| B: Dedicated `POST /commands` endpoint | 3 | 4 | **2** | 4 | 4 | 3.30 | Reject — per-client parser duplication, two error-shape contracts, FE slash-detection logic |
| C: Hybrid (intercept + endpoint, shared registry) | 2 | 4 | 3 | 3 | 3 | 3.00 | Reject — two transports, two test matrices, drift at the transport layer despite shared registry |

- **Dominant axis: Maintainability.** A single router transport keeps the source of truth at the registry layer AND the transport layer; B forces every non-FE client to re-implement the FE's slash parser; C preserves registry-level truth but doubles transport contracts.
- **Blast radius is acceptable**: the intercept is a 4–6-line check between validation end (:240) and status capture (:243); the four status-branch bodies (`:252/:402/:483-500`) are untouched. **Requirement:** a regression test asserting non-command traffic through `messages.py:243-500` is byte-identical (existing router suites + explicit marker test).
- **Flip conditions** (recorded, not expected): strict `POST /messages` byte-identity required for compliance/audit → C; a second programmatic (non-FE) client lands and mandates an addressable endpoint → C as bridge.

### Registry / dispatch design (ratifies Q8, extends it)

- `CommandSpec` frozen dataclass + `CommandRegistry` + `CommandDispatcher` in a new `daemon/services/command_dispatcher.py`, **mirroring the proven `daemon/sources/registry.py:47-159` pattern** (register/get/list, duplicate-raise).
- `CommandSpec` fields: `name`, `description`, `availability: Callable[[InstanceContext], Awaitable[bool]]` (O-B6 hook), `rate_limit_per_instance`, `handler: Callable[..., Awaitable[CommandResult]]`. Future command = one registration, **zero router edits**.
- Dispatcher ordering (load-bearing): `//`-escape check → parse → registry lookup → availability → **rate limit (BEFORE ExecutionGate acquisition — acquiring the gate then releasing without work leaks latency)** → ack → background task.
- Anti-patterns: router-level `_COMMANDS` dict (invisible to non-FE clients, requires spinning FastAPI to test); plugin-class ABC hierarchy (ceremony, scattered metadata).

---

## 2. Compaction Reuse Strategy (Focus 2 — force flag)

### Verdict: additive keyword param (Option a), scope NARROWED to threshold-only

Worker B verified the plan's central claim by reading `compaction.py:608-1130`: the engine is **not** helper-factored — injected/regular partitioning (:627-640) feeds min-messages, threshold, emergency, and replacement; the emergency path reuses `preserved` from :676; C3 re-attach (:750/:757) operates on the engine-built replacement before return; `CompactionResult` (:233) is one typed contract. Option C (refactor to helpers + new orchestrator) would re-implement D2/D3/D5/D6 invariants — the plan's rejection is **sound**. Option B (`compact_state_forced()`) duplicates the result contract and splits the Q7 timeout work across two methods.

**Refinement — what `force=True` bypasses (final ruling):**

| Gate | Location | force=True? | Rationale |
|------|----------|-------------|-----------|
| Threshold check | :659-664 | ✅ **bypassed** (the ONLY bypass) | The whole point of on-demand: user compacts below threshold |
| Min-messages | :645-651 | ❌ retained | Superseded in practice by the executor noop-floor; keeping it in-engine minimizes bypass surface |
| D9 60s dedup | :618-620 | ❌ retained **in-engine**; executor pre-checks recency | Second /compact within 60s has ~zero benefit; executor pre-check (read `compacted_at`, no mutation) returns `success + noop + reason=recently_compacted` WITHOUT invoking the engine — precise reason, no wasted LLM spend, safety net intact |
| D2/D3/D5/D6/D12/D13, pairing guard, terminal guard | various | ❌ NEVER | Persistence/invariant layer — outside force semantics |

- **Result-shape compatibility confirmed**: `@dataclass` defaults on `CompactionResult` keep all existing construction sites working; the reactive caller's `result is None or result.replacement_messages is None` check (graph.py:3514-3516) is unaffected. New fields: `forced: bool = False`, `failure_kind: str | None = None`. Test must assert `forced=False` on both auto paths (anti-drift).
- **Noop floor (Q4→Q6) lives at the EXECUTOR, not the engine**: below ~5% of the resolved per-instance window (D-B11 model resolution), return `success + noop + reason=below_floor` instead of generating a tiny noisy summary. Engine's `None` means "CAN'T compact"; below-floor means "WOULD but SHOULDN'T" — different semantics, and the engine stays single-purpose. Knob: `SLASH_COMMANDS_NOOP_FLOOR_RATIO` (default 0.05; 5% is a guess — expect tuning).
- **O-B7 (ephemeral) ratified with a structural seam**: `command_id` + `handler: Callable` already decouple execution from persistence; a future durable variant wraps `handler` in a `JobItem('command')` enqueue without touching `CommandSpec`. Nothing built now.
- **Terminal-guard anti-drift**: extract `_is_terminal_checkpoint(...)` as a module-private helper used by BOTH the proactive site (instance_messaging.py:1146-1150) and the compact executor (🟡 — prevents the two sites diverging).

---

## 3. Adaptive Timeout Design (Focus 3)

### Verdict: three-layer design ratified — with TWO corrections the plan missed

| Layer | Site | Value | Role |
|-------|------|-------|------|
| Per-call `asyncio.wait_for` | `compaction.py:1038` (binding) | `min(300, 90 + tokens/100k · 60)` | Cancels the inner task; preserves today's "site fires first" property |
| Facade `wall_clock_cap_s` | `:1011` (currently dead 45s) | **per-call cap + 5s margin** | Wraps cleanly after the inner cancel; tenacity retries stay INSIDE the cap (`llm_failover.py:559-568`); margin keeps :1038 binding by design |
| Whole-op budget | INSIDE `compact_state`, **between LLM calls only** | `COMPACTION_OPERATION_BUDGET_S=300` default | Bounds chunked N-call runs; on exhaustion the engine proceeds to the existing truncate fallback for the remainder |

**Correction 1 — token estimate source (changes D-B4's input).** The formula input must be the **prompt being sent** (the chunk's conversation text), not `context.messages`. `_call_summarization_llm` is reached from three sites (`:900` single-batch, `:939` merge, `:971` condense) with very different prompt sizes; `context.messages` (full conversation incl. preserved + injected) over-estimates every call after the first chunk and massively over-estimates merge/condense calls (tiny prompts) — burning budget headroom and delaying fallback. Compute `estimate_messages_tokens(prompt)` inline at the call site (estimator at `daemon/loader.py:465` unchanged).

**Correction 2 — mid-chunk timeout must preserve partial summaries (engine gap, benefits ALL paths).** Today the outer catch at `:753-772` fires on ANY batch failure and **all previously successful chunk summaries are discarded** — the run collapses to full truncate. Required change: per-chunk `try/except TimeoutError` inside the `:838-840` loop; a timed-out batch's raw messages flow into the replacement (preserved-or-dropped) while earlier summaries survive. Resulting checkpoint = "summaries of completed batches + raw messages of timed-out batches + preserved tail + injected" — coherent, D3-safe, and strictly less destructive than today on the proactive/reactive paths too.

**O-B8 (proactive latency): ACCEPT as designed.** Today's worst case is unbounded (N chunks × 30s each, stalling turn start for minutes); with the budget it is ≤300s then a *completing* truncate. Per-path overrides would dilute requirement #4; fire-and-forget would run turns against un-compacted history. Monitor via `context_usage` SSE.

**Cancellation discipline (unchanged, restated as binding):** NO outer timeout around `compact_state` — cancellation may only trip between engine LLM calls, never between the two `aupdate_state` calls (:1190-:1202). An outer cancel tears the D3 recipe.

---

## 4. Q5 — Fallback Variant: DECIDED

**Adopt: plain `_truncate_fallback` (compaction.py:1081-1111) + `failure_kind` outcome reporting + ONE marker line added INSIDE the existing function. Reject the LLM summary-line variant.**

- The summary-line half of the tech-preliminary variant is rejected decisively: generating it requires an LLM call — re-triggering the exact failure (LLM outage/timeout) the fallback exists to escape. It is also a second fallback implementation in spirit (violates D-B1/D-B5's no-parallel-path rule).
- The marker half is adopted **inside** the existing function (not a wrapper): one line before the preserved-groups loop — `replacement.append(SystemMessage(content="[Earlier messages trimmed to fit context]", id=f"truncation-marker-{uuid4()}"))`. Id-deterministic → the `add_messages` reducer de-dups on re-compaction; no collision with the synthetic system message (`persistence.py:404-449` prepend stays independent); pairing guard untouched (RemoveMessage + preserved only).
- Executor outcome mapping: `failure_kind="timeout"` → phases `timed_out → fallback_applied`; `failure_kind="error"` → `failed` (+ fallback note if fallback also applied). Distinct, user-honest, zero new engine surface beyond the two additive result fields.
- **Flip condition:** only if a general "verbose compaction annotation" feature is added later — then lift the marker into that feature, not into a fallback variant.

## 5. O-B4 — Terminal-Instance Handling: DECIDED

**Adopt: REJECT with `reason=terminal_instance` + `detail="Send a message to start a new turn, then /compact."` (plan baseline confirmed; the revive-then-compact tension is resolved AGAINST revive).**

Code-verified chain (worker C):
1. The terminal guard (`instance_messaging.py:1146-1150`, `if not state.next: return`) is load-bearing: `aupdate_state` on a checkpoint with `next=()` clears the run-state so a subsequent `astream` returns instantly — the `:1132-1140` comment documents the COMPLETED→RUNNING→COMPLETED collapse that bricks revive-on-send.
2. Auto-revive (`:1646-1682`) sets `instance.status=RUNNING` and bumps version but **does not call `aupdate_state`** — the checkpoint stays terminal until a real graph run reaches an agent node. So revive-then-compact either (a) no-ops at the guard, or (b) requires bypassing the guard = the documented brick.
3. Post-compact the instance would sit in `RUNNING` with no pending turn — a zombie state.
4. The user's actual goal (a finished conversation they want trimmed before reuse) is fully served by: send any message (revive + new turn) → then `/compact` on the now-active checkpoint. The rejection detail says exactly this.

**Flip condition:** only if a dedicated compact-on-terminal lifecycle is built later that deliberately restores `next` to non-terminal before summarization and back to terminal after, with regression tests proving revive-on-send still works. That is a separate lifecycle refactor, not part of this feature.

---

## 6. Concurrency & Lifecycle Model (Focus 5)

Matrix validated with corrections (worker D). **ExecutionGate acquisition mandatory for every execution (D-B3 ratified); rate-limit check ordered BEFORE gate acquisition.**

| Instance state at command time | Path | Verdict / correction |
|---|---|---|
| IDLE | quiescence probe (`timeout=0`) + `has_instance_busy=False` → gate → compact | ✅ + **correction:** re-read `has_instance_busy` under gate acquisition with retry-once (probe result is stale by read time; a turn may have started between probe and gate) |
| RUNNING | `waiting` SSE **first** (D-B9/F3) → pause-first (`pause_instance_cascade` :2685) → quiescence ≤30s (:3362-3431) → gate → compact → resume (:2971) | ✅ + **addition:** pause/quiesce failure → `rejected + reason=quiescence_timeout` (never crash the async task; never hang the ack'd command) |
| PAUSED, no frozen task | direct: gate → compact (blocks concurrent auto-resume turn) | ✅ |
| PAUSED **with** frozen task | **MISSING ROW in plan** — treat as PAUSED (frozen task holds no live astream; checkpoint frozen at node boundary) → gate → compact | ➕ added; **implementation check required:** verify the gate is released when pause cancels the graph task, and that resume (`is_retry=True`) re-acquires through the gate — worker D could not fully verify resume-path gate coverage (🟡) |
| Terminal (COMPLETED/TERMINATED/ERROR/FAILED) | **reject** `reason=terminal_instance` | ✅ per §5 |

**Interaction rulings:**
- **Queued messages during compaction**: their next turn blocks on the gate until compaction finishes — safe (serialized), bounded (≤300s), but user-visible latency. Document in FE copy; no change.
- **Second command mid-command**: rate-limit answers it (`rejected + reason=busy` if in-flight, `rate_limited` if inside min-interval). Idempotency ruling: every POST gets a NEW `command_id`; no idempotency-key machinery (compaction itself converges — recency pre-check makes a duplicate a cheap noop).
- **Instance terminated/deleted mid-command**: executor catches the persistence failure and emits terminal `failed` — never hangs, never leaves the in-memory registry entry active.
- **Daemon restart mid-command**: registry lost by design (D-B8 ephemeral). `GET /commands/active` returns `{exists:false}`; FE clears the card **silently** (no error toast). This is a required FE behavior (phase2 Task 4 addition).
- **Crash between the two `aupdate_state` calls**: benign (summary present, `compacted_at` missing) — same exposure window as the existing proactive path (:1190-:1202). Ratified.
- **Revive-on-send racing `/compact` on a terminal instance**: impossible post-§5 (terminal = rejected before any task starts).

---

## 7. Wire Contract (Focus 6 — normative schema)

Split rule (reconciles A's O-B9/O-B10 verdicts with D's schema): **parse-time errors → HTTP 4xx; post-parse semantic refusals → 200 command envelope with `state:"rejected"` + reason.**

- **Unknown command** → **400** `ErrorResponse{code:"UNKNOWN_COMMAND", detail:{available:[...]}}` — mirrors the existing `:222-229` validation-400 shape; FE toast path already exists. (Removed from the 200-rejected enum.)
- **Valid-but-refused** → **200** ack with `state:"rejected"` + reason: `terminal_instance | busy | rate_limited | pending_injections | compaction_disabled | quiescence_timeout`.

```ts
// POST /api/instances/{id}/messages → command ack (sync, ≤500ms)
type CommandAck = {
  status: "command"; command: string;          // "compact"
  command_id: string | null;                   // UUIDv4 — correlates ALL events (null on rejected acks)
  state: "accepted" | "rejected";
  reason?: RejectionReason;                    // when rejected
  detail?: string;                             // human guidance (e.g. terminal-instance hint)
  timestamp: string;                           // ISO8601
  ttl_seconds: number;                         // GET-fallback memory window (default 600)
};

// SSE event_type="command_progress" (LiveEventHub.stream_message, live-only, no replay)
type CommandProgressEvent = {
  instance_id: string; command_id: string;
  phase: "waiting" | "in_progress" | "success" | "timed_out" | "fallback_applied" | "failed";
  phase_seq: number;                           // monotonic per command — FE dedup/reorder guard
  timestamp: string; elapsed_ms: number;       // server clock = FE elapsed-timer source of truth
  eta_ms?: number;                             // advisory, in_progress only
  detail?: {
    tokens_before?: number; tokens_after?: number;
    compacted_type?: "summary" | "truncation" | "noop";
    failure_kind?: "timeout" | "error" | null;
    noop_reason?: "below_floor" | "recently_compacted" | "too_few_messages";
    checkpoint_id?: string; reason?: string;
  };
};
// Heartbeat: re-emit in_progress every 10s (phase_seq+1, fresh timestamp/elapsed_ms).

// GET /api/instances/{id}/commands/active   (fallback for SSE loss; auth mirrors GET /messages)
type GetActiveResponse = { exists: false } | { exists: true; command: CommandProgressEvent };
// Daemon restart ⇒ {exists:false} ⇒ FE clears card silently. Poll ~5s while card active AND SSE dead.
```

FE contract notes for phase2 (P1-1…P1-6 deltas): ack `accepted` → SSE `waiting` transition is normal (≤30s on RUNNING pause path); post-terminal refetch of `GET /messages` is triggered by the terminal event; SSE emission stays best-effort (D-B10); F3 rule (emit `waiting` BEFORE any pause mutation, D-B9) is binding. Verify the SSE transport emits keepalives on idle (proxy idle-timeout 🟢).

---

## 8. Full Verdict Register — O-B1…O-B13 and Q1–Q9

| Q | Verdict | Basis |
|---|---------|-------|
| **Q1** seam | ✅ **Adopt baseline** — BE-side router intercept | 5-axis matrix (§1): Maintainability dominant |
| **Q2** availability | ✅ Adopt with matrix corrections (§6): PAUSED-with-task row added; IDLE re-check under gate | Worker D |
| **Q3** sync/async | ✅ **Adopt** pure async (ack + SSE + GET) | 300s cap makes sync untenable; grace window buys nothing |
| **Q4** concurrency | ✅ Adopt pause-first; ADD reject-on-quiesce-failure (`quiescence_timeout`) | Workers C+D |
| **Q5** fallback | 🔨 **DECIDED: plain `_truncate_fallback` + failure_kind + marker line inside it; NO LLM summary-line** | §4 |
| **Q6** force design | 🔨 **DECIDED: keyword param + additive fields; bypasses THRESHOLD ONLY; executor owns floor/recency pre-checks** | §2 (narrowed vs plan) |
| **Q7** timeout | ✅ Adopt WITH 2 corrections: per-prompt token estimate; per-chunk try/except preserving partial summaries | §3 |
| **Q8** subsystem shape | ✅ Adopt: service dispatcher + CommandSpec + thin router hook; sources/registry.py pattern | §1 |
| **Q9** progress | ✅ Adopt 6 phases + 10s heartbeat, upgraded with phase_seq/elapsed_ms/eta_ms/ttl (§7) | Worker D |
| **O-B1** `//` escape | ✅ **Adopt `//`** — check `//` BEFORE `/`; strip one slash, deliver as text | Slack convention, cheap, testable |
| **O-B2** async confirm | ✅ **Pure async** — no grace window | Worker A |
| **O-B3** RUNNING | ✅ **Pause-first** (Watchover precedent watchover_service.py:1004) | Worker C |
| **O-B4** terminal | 🔨 **REJECT terminal instances** — `reason=terminal_instance` + guidance; revive-then-compact unsafe (§5) | Worker C (code-verified) |
| **O-B5** force detail | ✅ Option (a) keyword + extended result — **scope per Q6** | Workers B+C |
| **O-B6** availability scope | ✅ Global now; `availability` predicate hook in CommandSpec for later per-agent policy | Worker B |
| **O-B7** durability | ✅ **Stay ephemeral** (JAFP); `command_id`+`handler` seam keeps a future durable wrap open | Worker B |
| **O-B8** proactive latency | ✅ **Accept as designed** — bounded 300s vs today's unbounded; no per-path override | Worker C |
| **O-B9** unknown cmd | ✅ **400 UNKNOWN_COMMAND** + available-commands detail (parse-time client error) | Worker A |
| **O-B10** refusals | ✅ **200 `state:"rejected"` + reason enum** (semantic refusals) — with §7 split rule | Worker A |
| **O-B11** pending injections | ✅ **Reject** `reason=pending_injections` — drain couples injection delivery to compaction persistence; retry is cheap | Worker C |
| **O-B12** constants mirror | ✅ **Update mirror in same PR** (option a); schedule mirror DELETION as a separate tidy PR (6-month drift argues for removal) | Workers B+A |
| **O-B13** rate limiting | ✅ **Adopt**: 1 in-flight per instance + 10s min-interval per instance (config); **checked BEFORE gate acquisition**; this is the ONLY abuse guard (force no longer bypasses dedup concerns — executor pre-check) | Workers A+C |

---

## 9. Required Plan Changes (deltas vs current plan docs)

**phase1-plan.md / decisions.md:**
1. **D-B4 correction**: token-estimate input = per-prompt at the `_call_summarization_llm` call site (three call origins :900/:939/:971), NOT `context.messages`.
2. **NEW engine change (WS-3 addition)**: per-chunk `try/except` in the `:838-840` loop preserving partial summaries on mid-run timeout (today all are discarded at `:753-772`). Benefits proactive/reactive too — add regression test.
3. **D-B1/O-B5 narrowing**: `force` bypasses threshold ONLY. Executor pre-checks: `compacted_at` recency (<60s → `success+noop+recently_compacted`) and noop floor (<5% window → `success+noop+below_floor`, knob `SLASH_COMMANDS_NOOP_FLOOR_RATIO`). Engine dedup + min-messages stay untouched for auto paths.
4. **D-B5 refinement (Q5)**: add the single id-deterministic marker line inside `_truncate_fallback`; outcome mapping via `failure_kind`.
5. **O-B4 resolution**: terminal = reject; extract shared `_is_terminal_checkpoint` helper used by proactive site + executor (anti-drift).
6. **WS-5 replacement**: adopt the §7 normative schema verbatim (phase2's P1-1…P1-6 assumptions become pinned: reason enum, phase_seq, elapsed_ms, ttl_seconds, 400/200 split, `{exists:false}` restart semantics).
7. **WS-6 additions**: PAUSED-with-task row; IDLE re-check-under-gate; rate-limit-before-gate ordering; quiesce-failure → `rejected+quiescence_timeout`; instance-deleted-mid-command → terminal `failed`; verify gate release on pause-cancel + resume-path gate coverage (🟡 open verification).
8. **Facade**: `wall_clock_cap_s` = per-call cap + 5s margin at :1011 (replaces the "adaptive + margin" vagueness; keeps :1038 binding).
9. **O-B12**: constants.py mirror update in-PR + follow-up tidy PR to delete it.

**phase2-plan.md:**
10. Elapsed timer sources from server `elapsed_ms` (not FE-local clock) — survives reconnect.
11. Restart/card-clear rule: `GET /commands/active` → `{exists:false}` → clear card silently.
12. Terminal-instance UX: render the rejection `detail` guidance ("send a message, then /compact").
13. Poll cadence: ~5s while card active AND SSE dead; stop on terminal/`exists:false`.

## 10. Risks (deduped, severity-ordered)

- 🔴 **Checkpoint write race vs astream commit** — the ExecutionGate is the ONLY defense (execution_gate.py:108-143 blocks graph runs, not arbitrary checkpoint writes). Any future code path writing checkpoints without the gate breaks BOTH compaction paths. Mitigation: code comment at the gate layer + WS-6 verification items.
- 🔴 **Terminal-instance mutation** (O-B4) — resolved by rejection; the brick mode (aupdate_state on `next=()`) stays guarded. Note: no regression test currently pins the brick behavior itself (worker C unverified item) — add one to make the guard's load-bearing status testable.
- 🟡 **Mid-chunk partial-summary loss** — engine gap fixed by plan change #2; until landed, timeout on chunk ≥2 discards all prior summaries.
- 🟡 **Resume-path gate coverage** — unverified whether all resume entry points acquire the gate; must be verified before WS-6 is considered done.
- 🟡 **Facade behavior at ~305s wall clock** — tenacity retry semantics at high caps read but not load-tested (worker C unverified item).
- 🟡 **Rate-limit rapid-click race** — needs an integration test (double-POST within min-interval).
- 🟢 Noop-floor 5% ratio is a tuning guess; 🟢 SSE keepalive-on-idle unverified; 🟢 GET-fallback auth must mirror `GET /messages` auth gates.

## 11. Confidence

**High** on: seam choice, registry shape, Q5, O-B4, async model, force-flag mechanism, wire-contract schema. These rest on direct code verification by two or more workers independently.
**Medium** on: noop-floor ratio (tuning), facade-at-305s behavior, resume-gate coverage — all bounded by verification tasks in §9.7/§10.
**Assumption that would flip the headline recommendation:** if `POST /messages` byte-identity becomes a compliance requirement, the seam flips to hybrid (C) — everything else stands.

## Gaps

None — all four dispatched analyses completed and were evidence-cited. One protocol note: the data-flow worker omitted the `Skill loaded:` first-line confirmation (deviation noted; report quality and file:line evidence density indicate the skill and code were both genuinely engaged — no re-dispatch warranted).
---

# Post-Review Adjudication (C1 + O8–O13)

Date: 2026-08-31 (post reviewer-council NEEDS_CHANGES)
Scope: C1 partial-summary semantics contradiction + six spec pins (O8–O13). This section amends §3/§4/§7 where stated; where silent, prior sections stand.

## C1 — DECIDED: Hybrid = option (i)'s distinct wire value **with** option (ii)'s trim semantics for the timed-out span

**The contradiction, confirmed.** §3 correction-2 (per-chunk try/except preserving completed summaries) and §4's D-B5 mapping (timeout → `_truncate_fallback` → `timed_out_fallback`) cannot both hold on a mid-run timeout: with partials preserved, `_truncate_fallback` never fires, no trim happened, the marker (pinned inside `_truncate_fallback`) never emits, and FE copy "compacted via trimming" is false. The reviewer is right; §4's mapping as written only covers the zero-partials case.

**Decision semantics (binding, engine-level):**

On any mid-run stop (per-chunk timeout caught in-loop at `:838-840`, OR whole-op budget exhaustion between LLM calls), let S = completed batch summaries, B = batches not successfully summarized (in-flight failed + un-attempted):

1. **|S| ≥ 1 → partial path**: replacement = summaries(S) + **truncation marker** + preserved tail + injected (existing D2/D3 assembly rules). **B's messages are DROPPED — true trim of the un-summarized span.** Rationale: the user's original requirement is fallback = TRIM ("hard truncation of oldest… so the user is never stuck"). Keeping B raw would leave reduction unbounded (timeout on batch 1 of 10 ≈ zero shrink = still stuck) and produce a result that is neither summary nor trim — the exact incoherence C1 flags. Completed summaries are kept because discarding them is pure waste (pre-correction-2 behavior). Bounded shrink is guaranteed: the context always loses at least the un-summarized span. Data-loss class for B = identical to today's destructive trim; honestly reported via the wire value below.
2. **|S| = 0 → existing whole-fallback path fires unchanged**: `_truncate_fallback` (`:744-772` → `:1081-1111`), `compacted_type="truncation"`, marker emitted there, executor mapping per original §4. **This is the single-batch-timeout edge case: a first-batch timeout degrades to exactly today's behavior** — no new machinery, no new mapping.
3. **`compacted_at` stamps on BOTH paths** (D12) — a partial result is a completed compaction, not a failure.

**Marker exactly-once:** extract the single marker line from §4 into `_append_truncation_marker(replacement)`; called by `_truncate_fallback` AND by the partial path's assembly. The two construction paths are mutually exclusive per result → exactly one marker per result, always.

**Engine API change (WS-3.4, now specified):** `_summarize_chunked` returns a typed outcome instead of raising through to `:753-772` on per-chunk failure:
`ChunkedOutcome(summaries: list, failed_batches: list[Message], stop_reason: "completed" | "timeout" | "error" | "budget")`.
The outer `:744-772` handler branches: `stop_reason != "completed"` and `summaries` non-empty → partial assembly; empty → `_truncate_fallback`. No per-caller branching — **proactive/reactive paths get identical semantics** (they consume the new `partial_summary` enum value; any `== "summary"` checks in auto-path code are intentionally NOT matched by partials — that is correct semantics, and auto-path tests must assert the new value only under timeout scenarios).

**WS-3.4 acceptance (replaces prior task text):** (a) first-batch timeout → `truncation`-typed result WITH marker, no summaries; (b) ≥2 batches, batch-2 timeout → `partial_summary` result: batch-1 summary present, batch-2 messages absent, marker present exactly once; (c) budget exhaustion mid-run → same assertions as (b) with stop_reason="budget"; (d) proactive + reactive callers observe identical outcome semantics on the same tests.

**Exact §7 amendment (sign-off):**

> In `CommandProgressEvent.detail`: `compacted_type` enum becomes **`"summary" | "partial_summary" | "truncation" | "noop"`**. Terminal outcome classes:
>
> | compacted_type | SSE phases | FE terminal copy (Task 6) |
> |---|---|---|
> | `"summary"` | → `success` | "Context compacted" |
> | `"partial_summary"` | `timed_out` → `fallback_applied` | "Compaction timed out partway — kept the summarized sections, trimmed the un-summarized older section" |
> | `"truncation"` | `timed_out` → `fallback_applied` | "Compaction timed out — history was trimmed without a summary" |
> | `"noop"` | → `success` (+ `noop_reason`) | "Nothing to compact" |
>
> The **phase machine is unchanged** (no new phases — partial is a detail-level distinction, keeping the phase2 logic-mirror stable); FE copy branches on `compacted_type`. `failure_kind` remains `"timeout" | "error" | null` (budget exhaustion reports `"timeout"`; `detail.reason` free-form may say `budget_exhausted`). Executor mapping count = three: summary→success; partial_summary and truncation→`timed_out→fallback_applied` (distinct `compacted_type`); noop→success.

**Propagation:** WS-3.4 (acceptance above), WS-4 4.1 (marker via shared `_append_truncation_marker`; exactly-once test), WS-4 4.2 (three-way mapping per amendment), S-13/S-5 (assert acceptance (a)–(d) above; S-5's "user never stuck" criterion now provable: reduction ≥ un-summarized span), WS-8 (add table-driven partial/budget/marker-exactly-once/auto-path-enum tests), phase2 Task 6 (copy table above).

## O8–O13 — one-line verdicts

- **O8** — New small module `daemon/services/_checkpoint_utils.py` hosting `_is_terminal_checkpoint` (refactored out of `instance_messaging.py:1146-1150`); imported by both instance_messaging and compact_executor; **compaction.py stays free of checkpoint-state semantics** (engine reuse boundary preserved).
- **O9** — One try/except around pause→quiesce; ANY failure (timeout or raised exception) → `rejected + reason=quiescence_timeout` with the exception class in `detail` (single FE rendering, honest diagnosability — do NOT add a second enum value); if pause half-succeeded, best-effort `resume_instance_cascade` in a finally before emitting (never leave a rejected command having mutated instance state; if resume itself fails, `detail` records left-paused); async task never crashes.
- **O10** — Registry owned by `CommandDispatcher` (`daemon/services/command_dispatcher.py`): one active slot per instance + daemon-wide terminal ring **LRU ≤ 20, TTL = `ttl_seconds` (600 default)**; eviction on terminal event, TTL expiry, and instance delete/terminate mirroring the `_pending_injections` cleanup path; keyed by `instance_id` (not FE session) so FE instance-switching mid-command loses nothing — FE re-syncs via `GET /commands/active` on re-mount.
- **O11** — Executor resolves the model via the **manager session-model accessor — the same source the summarization LLM client already uses (compaction.py:997-1008)** — then `context_window_overrides` (config.py:715-749); global `config.llm.model` only as a **WARNING-logged** fallback (never silent), so window/floor math is auditable.
- **O12** — `GET /api/instances/{id}/commands/active` lives in `daemon/routers/instances.py` (instance-scoped state; auth mirrors `GET /messages`); **mounted unconditionally — with `slash_commands.enabled=false` it returns uniform 200 `{exists:false}`** so the FE contract is invariant across config flips (no route-surface change when disabled).
- **O13** — Confirmed **additive, not mirror**: keep the existing `:222-229` `{code, message}` envelope, add `code:"UNKNOWN_COMMAND"` + new `detail:{available:[...]}` field; FE toasts on `code`, and `detail.available` later feeds slash autocomplete without a contract change.

## Reconciliation note

§9 plan-change list: item 2 (per-chunk preservation) and item 4 (marker) are superseded by C1 above; item 6 (WS-5 normative schema) is amended by the §7 amendment text. All other §9 items stand.

---

## FE Q2/Q3 verdicts (approver-note-6 gate — architect, 2026-08-31)

Approver note 6 required architect confirmation of the two remaining FE-open items (`phase2-plan.md` §Open Questions) before phase2 Tasks 5/6. Verdicts — both CONFIRM; this addendum supersedes the "OPEN" tags on Q2/Q3:

- **Q2 — does an active command block the input? → CONFIRM: NO hard block.** The pinned contract is already built for a non-blocking client: §6 interaction rulings document queued-during-compaction messages as safe/serialized/bounded ("document in FE copy; no change"), and the §7 refusal enum (`busy` / `rate_limited`, checked before gate acquisition per O-B13) exists precisely to answer duplicates authoritatively — a composer hard-block would be redundant against a 300s hard cap and contradicts the phase2 Objective ("message input stays usable"). The Task 5 duplicate-command guard remains the advisory soft layer; BE refusals authoritative. The "soft-block slash input only" branch needs no separate mechanism — it IS the duplicate-command guard.
- **Q3 — in-timeline provisional bubble vs out-of-timeline card? → CONFIRM: out-of-timeline card** (`.pending-injection-card` pattern); no in-timeline provisional bubble. §7 itself speaks in card terms ("FE clears the card **silently**" on `{exists:false}`; "poll ~5s while card active"); the terminal-SSE-triggered `GET /messages` refetch would destroy a bubble anyway; and a provisional timeline row would collide with the echo/merge pipeline (`echo_id` optimistic append) that command acks must bypass entirely (phase2 R4). The command never becomes a message (BE intercept at `messages.py:240→:243`), so the timeline stays messages-only.

**§7 conflict check: none.** The pinned schema is input-blocking-agnostic on Q2 (its refusal enum presumes duplicate POSTs are possible — i.e., a non-blocking client is the intended posture), and its FE notes presuppose the card on Q3 (incl. O10 re-mount re-sync and O12 restart semantics). The post-review §7 amendment (`compacted_type` + `partial_summary` + per-value copy table) touches Task 6 terminal copy only; neither verdict is affected. **Tasks 5/6 cleared to proceed as written — no plan deltas.**
