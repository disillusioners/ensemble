# Technical Analysis: `/compact` Slash-Command Subsystem (First Command)

Date: 2026-08-31
Author: planner[v2] via technical-analysis worker
Analysis depth: deep-dive (multiple architecture-shaping decisions + first-of-subsystem)
Status: Draft — for architect enrichment and downstream phase planning
Workdir: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble` (branch `feature/slash-commands`)

---

## Executive Summary

**Feature.** A user types `/compact` in the chat box for a selected instance → backend intercepts the `/`-prefixed message and runs on-demand context compaction on that instance via the existing `ContextCompactor`. First command of an extensible slash-command subsystem. Adaptive LLM timeout (base 90s + ~60s/100k context tokens, hard cap 300s) is a general improvement to compaction that benefits proactive and reactive paths too. On timeout/failure the command falls back to trim-only (hard truncation of oldest). FE states (waiting → in progress → success / timed-out → fallback) stream via SSE; FE is Angular 21 with signals.

**The 9 open architecture questions.**

| # | Question | One-line preliminary verdict | Confidence |
|---|----------|----------------------------|------------|
| Q1 | FE-side vs BE-side intercept | BE-side, single POST endpoint with router-level command-detection (FE keeps a thin awareness layer for input UX) | High |
| Q2 | Per-instance vs global command availability | IDLE always; RUNNING via pause-first-then-quiesce; PAUSED direct (no resume); terminal rejected (terminal-checkpoint guard risk) | Medium |
| Q3 | Sync-in-POST vs async | Async: ack 202 + SSE progress + REST fallback; sync rejected by 300s cap and proxy timeouts | High |
| Q4 | Concurrency on a busy instance | Pause-first-then-quiesce (convention-compliant) with a documented "reject on persist-failure" fallback | Medium |
| Q5 | Trim-fallback checkpoint consistency | Trim + sentinel marker, smaller recent window than auto, paired with a tiny summary line that includes the truncation reason | Medium |
| Q6 | Force-flag design | Extend `compact_state(force=True)` with named-knob bypass; do NOT bypass dedup unless explicitly requested by command | Medium |
| Q7 | Adaptive timeout placement & scope | Replace the 30s literal at compaction.py:1038 + thread `wall_clock_cap_s` at :1011 + add a whole-operation cap; SAME formula for proactive/reactive/on-demand | High |
| Q8 | Subsystem shape | Service-layer dispatcher with a Command dataclass + router-level table for input parsing; literal `'/'` escape via `//` to forward as plain text | Medium |
| Q9 | Progress event granularity | 5 phases (waiting / in_progress / success / timed_out / fallback_applied / failed) with a 10s heartbeat when in_progress; FE reuses pending-injection card template | Medium |

**Architect to confirm** every "preliminary verdict" and turn the table into final decisions. This document is an options map, not a decision register.

---

## Cross-Cutting Dependencies & Interactions

The 9 questions are not independent. Mapping dependencies so downstream phase planners can sequence work correctly.

| Pair | Interaction | Implication |
|------|-------------|-------------|
| **Q1 → Q3** | BE-side intercept makes the Q3 async shape trivial (one router method, one SSE event_type). FE-side intercept forces a second endpoint surface and a separate cancel channel. | Pick Q1 first; Q3 follows mechanically. |
| **Q1 → Q9** | BE-side intercept lets SSE `command_progress` events use the existing `LiveEventHub.stream_message(event_type=...)` zero-overhead path (`daemon/services/live_event_hub.py:150-173`). FE-side would force the FE to consume a different channel. | Locks the SSE contract. |
| **Q3 → Q4** | Async execution lives entirely on the daemon; Q4 pause-first happens inside the async background coroutine started from the ack-202. Cancelling the FE-side request does NOT cancel the daemon coroutine (we don't have an HTTP-cancel hook into the background task without a kill endpoint). | A `command_id` GET fallback doubles as a soft-cancel surface (UI can mark as "abandoned"); the daemon keeps running but the FE no longer renders progress. |
| **Q4 → Q5** | Pause-first means we hold the ExecutionGate; if compaction fails on LLM, we must release cleanly before resuming. Trim-fallback runs under the SAME gate so the trim and the original failure both land before the resume. | The fallback must produce a checkpoint that resume does NOT have to reconcile manually. |
| **Q4 → Q6** | Force flag bypasses threshold. Pause-first + force-bypass can compact a near-empty conversation, generating a tiny summary that adds noise. | Consider a "force" sub-mode that suppresses compaction when tokens are below e.g. 5% of window — return ack=success with `noop: true`. |
| **Q4 → Q7** | Adaptive timeout (300s cap) intersects with `wait_for_instance_quiescent(30s)`. If pause-first times out the quiescence wait, we fail before timing out compaction. | The quiescence timeout must be the SAME unit as the lower bound of the adaptive timeout (or slightly less). Recommend 30s quiescence, then 300s operation. |
| **Q5 → Q6** | Force-bypass + trim-fallback: if forced compaction triggers emergency_truncate because there's nothing to summarize, the result is indistinguishable from `_truncate_fallback` minus a marker. | Forced compaction should NEVER enter the destructive emergency path; the orchestrator must guard `compactable` empty. |
| **Q5 → Q7** | Fallback runs on timeout. The timeout decision is "did any single summarization call exceed its per-call cap, OR did the whole operation exceed the budget?" — different fallback shapes. | Whole-op timeout → fallback to trim with a summary-of-last-successful-batch if any batch succeeded (architect C1, 2026-08-31: distinct wire value `"partial_summary"` with explicit drop of the un-summarized span — bounded shrink guaranteed, see WS-3.4 acceptance (a)–(d)). Per-call timeout → ~~resume operation but skip the failed batch (already current behavior at `:753-772`)~~ **STRIKEN 2026-08-31 — that "already current behavior" claim was FALSE; current behavior at `:753-772` discards ALL completed chunk summaries and full-truncates.** The corrected per-call behavior is the partial-summary preservation added in 3.4 (now superseded by C1's typed `ChunkedOutcome`). |
| **Q6 → Q7** | Force flag + adaptive timeout = new code path; if we add them together the blast radius is large. | Land force-flag + extend `compact_state` first; then adaptive timeout as a general compaction improvement (which benefits Q4's safety margin). |
| **Q8 → Q9** | Command definition object shape (Q8) drives the event schema (Q9). | Fix Q8's event-schema field before Q9's phase names. |
| **Q7 ↔ Q1** | Adaptive timeout applies to compaction LLM calls regardless of caller. Q1's intercept point doesn't care which timeout applies. | Q7 is independent of Q1. |

---

## Per-Question Analysis

Each question follows: **Context → Options → Trade-offs → Evidence → Preliminary Recommendation + Confidence → Decision owner note.**

---

### Q1. FE-side vs BE-side intercept of slash commands

**Context.** Slash commands could be detected in two places: (a) the FE inspects the textbox value and never sends `/`-prefixed messages through `POST /messages`, instead calling a dedicated `POST /commands` endpoint; (b) the FE always sends text, and the BE router detects `/` prefixes and routes to a command handler. The architect should weigh transport simplicity, future non-FE clients (curl/CLI/agent-tool/inbound-source adapters), and the existing Phase 2 / message-display-latency work that already minted `echo_id` and an SSE echo at POST time.

**Options.**

| Option | Description | Caller surface | Transport | Cancel |
|--------|-------------|----------------|-----------|--------|
| **A. FE-only intercept** | FE parses `/` and dispatches to a dedicated `POST /commands` endpoint. `/`-prefixed text never reaches `POST /messages`. | 2 endpoints (FE knows which) | Per-command endpoint | Per-command (kill endpoint per command) |
| **B. BE-only intercept (router-level)** | FE always uses `POST /messages`; BE router detects `/` prefix and routes. Command dispatch lives in a service. | 1 endpoint (BE decides) | Same `POST /messages` | Tied to message ack + SSE |
| **C. Hybrid (FE-aware rendering + BE intercept)** | BE intercepts (option B). FE inspects input for UX cues (slash palette, dim the message bubble, show "compaction in progress" badge). | 1 endpoint, FE renders | Same `POST /messages` | Tied to ack + SSE |

**Trade-offs.**

| Criterion | A. FE-only | B. BE-only | C. Hybrid |
|-----------|-----------|-----------|-----------|
| **Single transport** | Two endpoints (`/messages` + `/commands/{name}`); drift risk on auth/headers | One; FE never knows about commands | One; same as B |
| **Non-FE clients** | Each must re-implement FE logic | Free for any HTTP client | Free for any HTTP client |
| **FE input-clear contract** | New contract per command; current `clearInput() only on API success` (`message-input.component.ts:163-190`) still works but each command needs a success shape | Single contract — POST returns ack, FE clears | Same as B |
| **Error UX latency** | Sync — FE knows it's a command and renders error inline without chat-history roundtrip | Sync for unknown command (BE returns 400 with command-not-found code); async progress for in-flight commands | Same as B + FE palette previews |
| **Future non-FE command surfaces** | Every new client reimplements command parser | One source of truth (BE registry) | One source of truth |
| **Discriminator cost** | FE must own `'/'` parsing | BE must add a single line at `daemon/routers/messages.py:243` (current_status capture point) | Same as B + FE palette |
| **Consistency with wc-wake-report-integrity** | Adds a parallel surface that bypasses the injection_pending SSE pattern | Plays directly into `set_injection` + `injection_pending` SSE (lines :419-430) | Same as B |

**Evidence.**

- FE `clearInput` contract: `frontend/src/app/components/message-input/message-input.component.ts:184-190` ("clear only on API success").
- FE send flow: `frontend/src/app/pages/chat/chat.component.ts:1225-1362` — `api.sendMessage` + `response.message_id` optimistic-append; works for any 2xx.
- BE injection path: `daemon/routers/messages.py:402-482` shows the 202-Accepted + `injection_pending` SSE + `echo_id` pattern that already uses LiveEventHub without a new hub method.
- Phase 1 message-display-latency verdict (per `decisions.md` and the merge at `5e16f791`): BE router is the canonical interceptor for content-aware routing.
- Rejected seams (per explorer report, HIGH confidence):
  - `enqueue_message_job` sniff — misses RUNNING injection lane (`daemon/routers/messages.py:402-500`).
  - `job-processor` admission — post-commit redirect, can't 4xx synchronously.
  - `agent_node` drain — command would run INSIDE the turn whose checkpoint it rewrites (unsafe).

**Preliminary recommendation: B → C (BE-side intercept with FE-aware UX).** A pure BE-only is the minimum viable; the FE overlay layer (slash palette, in-flight badge) is a separate UX concern.

**Confidence: High** that BE-side intercept is correct. The rejected-seams analysis is rigorous and the FE clearInput contract composes. **Medium** on the magnitude of the FE UX surface — depends on how many subsequent commands share the same UX pattern.

**Decision owner note.** Architect to confirm:
- Is the FE slash-palette in scope for this feature or deferred? (Current "first command" framing implies the minimum — FE only needs to send `/compact` and render progress.)
- Is the literal `/` escape (`//` to send plain text) in scope? Recommend yes — single-character escape, FE just strips one `/` if input starts with `//`.

---

### Q2. Per-instance vs global command availability

**Context.** Slash commands must declare which instance statuses they accept. `/compact` is the first; subsequent commands may diverge (e.g. `/reset` might reject terminal, `/status` might accept everything). This question concerns the predicate surface (an `applicability` field on the command definition) and the four-instance-status taxonomy: RUNNING / WAITING_CHILDREN / PAUSED / IDLE / terminal (COMPLETED / ERROR / FAILED / TERMINATED). The complicating factors are:

1. The terminal-checkpoint guard at `daemon/services/instance_messaging.py:1146-1150` (`if not state.next: return`) — a known issue: calling `aupdate_state(as_node='agent')` on a finished graph clears `next=()` and bricks the subsequent `astream` so `RUNNING → <100ms → COMPLETED`.
2. The terminal revive-on-send path at `daemon/services/instance_messaging.py:1486-1510` (and `:1648-1653`) — sending a message to a terminal instance auto-transitions to RUNNING.
3. The PAUSED auto-resume at `daemon/routers/messages.py:252-378` — POST to PAUSED already resumes.

**Options (status taxonomy × first-command semantics).**

| Status | A. Default-open | B. Status-gated + pause-first | C. Status-gated + reject |
|--------|------------------|--------------------------------|--------------------------|
| **IDLE** | Allow | Allow (no quiescence wait — already quiescent) | Allow |
| **QUEUED** | Allow (drains naturally) | Allow | Allow |
| **RUNNING** | Allow — RAISES terminal-checkpoint issue because we hold the ExecutionGate and the in-flight astream may overwrite our aupdate_state | Pause → wait_for_instance_quiescent(30) → compact → resume | Reject with 409 Conflict |
| **WAITING_CHILDREN** | Allow — RAM injection slot still drained; same astream-overwrite risk | Same pause-first as RUNNING | Reject |
| **PAUSED** | Allow (no resume) — already quiescent at graph-task level (claim gate `claim_pending_task` blocks workers per `daemon/repositories/task/repository.py:1146`; corrected 2026-08-31 from the prior `:646-671` anchor — that range was a near-miss on `has_instance_busy`, the BROADER PENDING/RUNNING/PAUSED predicate at :543, which is the wrong gate for PAUSED-quiescence) | Allow | Allow |
| **TERMINAL (COMPLETED/ERROR/FAILED/TERMINATED)** | Allow with revive — but if checkpoint is terminal AND we mutate, the brick-on-resume risk appears | Reject (terminal needs explicit revival + replumb of resume handle per `decisions.md` reconcile_turn_mirror rules) | Reject with 410 Gone |

**Trade-offs.**

| Criterion | A. Default-open | B. Status-gated + pause-first | C. Status-gated + reject |
|-----------|------------------|--------------------------------|--------------------------|
| **UX simplicity** | User always sees success | User sees brief pause, then success | User sees error mid-task — must pause first themselves |
| **Safety** | Worst — all the unsafe paths live here | Best for state mutation; matches watchover pattern | Safe but unusable for the common case (RUNNING is the default working state) |
| **Implementation cost** | Same | Higher (orchestrate pause→quiesce→compact→resume) | Lowest |
| **Interaction with reconcile_turn_mirror** | Mirror rows suppressed while RUNNING; if we mutate mid-turn, mirror becomes inconsistent on resume | Pause flips status; mirror is in a quiescent state; compaction under quiescence produces a clean re-base | No state mutation |
| **Future command shape** | Reusability of orchestration for any mutating command | Same | Same |

**Evidence.**

- Pause-first convention: pause → quiescence → mutate → resume, first proven consumer is `WatchoverService.activate_watchover` (`daemon/services/instance_lifecycle.py:2685-2971`, `daemon/services/instance_lifecycle.py:2685-3093`).
- `wait_for_instance_quiescent(timeout=30)` best-effort barrier (`daemon/manager.py:3362-3431`) — `True` on quiescent, `False` on timeout, never raises.
- astream-overwrite: in-flight `astream` commits overwrite external checkpoint writes at node boundaries (per explorer report, HIGH confidence). Compaction MUST NOT race an active turn.
- Terminal-checkpoint guard: `daemon/services/instance_messaging.py:1146-1150`.
- Turn-reconciler pattern: `reconcile_turn_mirror(work_id)` authoritative, 8 mirror tables, named transitions declare MIRROR_SET (`decisions.md`, phase 4b/4c deferred).
- Claim gate: `daemon/repositories/task/repository.py:1146` (`claim_pending_task` — corrected 2026-08-31 from the prior `:646-671` anchor). PAUSED-no-task is effectively quiescent because workers block on the claim gate. **Note:** `has_instance_busy` at `:543` is the BROADER PENDING/RUNNING/PAUSED predicate and counts PAUSED rows — using it for quiescence probes yields a stale-busy read between probe and gate-acquire (benign: retry-once under the gate).

**Preliminary recommendation: B (pause-first for RUNNING/WAITING_CHILDREN, allow IDLE/QUEUED/PAUSED, reject terminal).**

- **IDLE/QUEUED/PAUSED**: direct execution under ExecutionGate.
- **RUNNING/WAITING_CHILDREN**: pause → quiesce → compact → resume.
- **TERMINAL**: reject 410. Future work — terminal revival + checkpoint re-anchoring is orthogonal and lives in the reconcile_turn_migration backlog.

**Confidence: Medium.** The pause-first path is well-established (watchover, resume-handle migration in flight). The terminal story is fragile and explicitly deferred; recommending rejection avoids a silent brick.

**Decision owner note.** Architect to confirm:
- Is WAITING_CHILDREN accepted under the wc-wake-report-integrity kill-switch? Today WC under flag-OFF falls through to injection (per `daemon/routers/messages.py:402-405`); under flag-ON, WC is enqueue+durable. The command semantics should match — recommend pause-first treats WC identically to RUNNING.
- Should the command's applicability be expressed as a predicate `Callable[[InstanceStatus, bool], bool]` returning accept/reject/pause-first? This keeps the registry pluggable.

---

### Q3. Execution model: synchronous-in-POST vs async

**Context.** Compaction can take up to 300s (cap from adaptive timeout — see Q7). Holding an HTTP request open for 300s is hostile to uvicorn/FastAPI default proxy timeouts and any production HTTP intermediary (Cloudflare's anycast read timeout is ~125s per `daemon/config.py:164`). SSE is in-memory only with NO replay (`daemon/services/live_event_hub.py:175-196`), so a tab close loses the stream. A REST fallback (GET `/instances/{id}/injection` pattern at `daemon/routers/messages.py:572+`) is the durable answer. The question is whether the POST holds the request open or returns ack immediately and runs in the background.

**Options.**

| Option | Description | HTTP lifecycle | Cancellation | Restart durability |
|--------|-------------|----------------|--------------|--------------------|
| **A. Sync (hold POST open for up to 300s)** | Server streams progress via chunked transfer or waits for completion | Single request, slow | Client disconnect = server may continue | Daemon restart kills the operation |
| **B. Async ack + background coroutine + SSE + REST fallback** | POST returns 202 + command_id + state="waiting" immediately; command runs as daemon-internal background task; SSE `command_progress` events; GET fallback | Fast ack, separate stream + REST | Disconnect = daemon continues; UI shows "abandoned" | Daemon restart kills the operation (no durability) |
| **C. Async + durable (job_type='command' JobItem)** | Same as B but command is enqueued as a JobItem; survives daemon restart | Same | Same | Durable across daemon restart |

**Trade-offs.**

| Criterion | A. Sync | B. Async + SSE + REST | C. Durable JobItem |
|-----------|---------|-----------------------|---------------------|
| **Proxy timeout compatibility** | Fails behind Cloudflare/some nginx defaults | Compatible | Compatible |
| **Cancel-on-tab-close UX** | Server stops working on disconnect; partial state may persist | Daemon continues; FE marks abandoned | Same; orphan jobs cleaned by Pattern f sweep (per `recent history` and `daemon/persistence.py` orphan-sweep at :517-526) |
| **Restart durability** | Lost | Lost | Survives |
| **Code complexity** | Lower | Medium (ack+background+SSE+REST) | Highest (the existing `message` mirror class is fine for public messages — but a `/compact` would need a NEW `command` JobItem type because it's ephemeral and never goes through `enqueue_message`; prior plan text that said "D13 guard rejects job_type='message' mirrors" inverted the JAFP rule and is corrected 2026-08-31) |
| **JAFP compliance** | Internal ops are NOT JobItems (`decisions.md` D13) — code-implied router intercept + direct service execution | Same | New job_type needed |
| **Progress visibility** | None mid-flight (chunked transfer can fake it) | SSE with phases + REST fallback | Same |

**Evidence.**

- D13: JobItem for `job_type='message'` is a pure mirror (PG trigger skips them). Internal ops should NOT be JobItems unless they need durability (`decisions.md`).
- wc-wake-report-integrity: `enqueue_message_job(source="api")` is the durable equivalent for messages, used under flag-ON (`daemon/routers/messages.py:182-188`).
- Best-effort SSE never fails API: `daemon/services/live_event_hub.py:184-196` silently drops on full queue + WARNING on API path (`daemon/routers/messages.py:152-156`).
- F3 rule: emit SSE BEFORE pause-flag mutation or event is lost (per explorer report).
- Orphan-job sweep (Pattern f, `recent history` 2026-08-30): orphan ACTIVE jobs now DEAD-finalized ≤20min by default-on sweep. PAUSED-target orphans defer until resume.

**Preliminary recommendation: B (async ack + SSE + REST fallback).**

- POST returns 202 with `{status:"command", command, command_id, state:"waiting", ...}`.
- Background coroutine runs the orchestration (Q2 pause-first + Q7 timeout).
- SSE `command_progress` events with phases from Q9.
- REST fallback: `GET /api/instances/{id}/commands/{command_id}` returns the latest known state.

**Confidence: High.** This is the pattern message-display-latency already established for RUNNING injection (`daemon/routers/messages.py:402-482`). The 300s cap exceeds Cloudflare's read timeout by ~2x; sync is incompatible with proxy reality.

**Decision owner note.** Architect to confirm:
- Should the GET fallback be keyed by `command_id` only, or by `(command_id, last_seen_event_id)` for incremental sync? Recommend the latter for clients that lose SSE temporarily.
- Is there appetite for durable JobItems later, or is "daemon restart = command abort" acceptable? Per JAFP, accept the in-process model; document the limitation.

---

### Q4. Concurrency strategy for `/compact` on a busy instance

**Context.** Three concurrency primitives are relevant:

1. `ExecutionGate` (per-instance `asyncio.Lock`, `daemon/services/execution_gate.py:108-143`) — serializes graph runs for an instance.
2. `wait_for_instance_quiescent(timeout=30)` (`daemon/manager.py:3362-3431`) — best-effort barrier waiting for the graph task to finish.
3. `pause_instance_cascade` + `resume_instance_cascade` (`daemon/services/instance_lifecycle.py:2685-2971`) — first proven consumer is `WatchoverService.activate_watchover`.

The astream-overwrite finding (per explorer, HIGH confidence) means we MUST NOT mutate the checkpoint while a graph is mid-run. RAM injection FIFO exists (`_pending_injections` manager.py:643 definition, accessed :2462+; corrected 2026-08-31 from the prior `:2398` anchor) — pausing clears/drains it; users with mid-flight injection expectations need to know.

**Options.**

| Option | Description | Latency on busy instance | Side effects on user |
|--------|-------------|---------------------------|----------------------|
| **A. Reject when RUNNING** | 409 Conflict: "Pause instance first or retry when IDLE" | Immediate | User must pause + retry |
| **B. Wait-for-ExecutionGate** | `gate.run(...)` queues behind current graph; no pause; runs after turn ends | Unbounded (turn may take many minutes) | Composition with the message queue is undefined; user may never see completion |
| **C. Pause-first-then-quiesce (convention)** | `pause_instance_cascade` → `wait_for_instance_quiescent(30)` → compact → `resume_instance_cascade` | Bounded (quiescence cap 30s + operation cap 300s = 330s worst case) | Mid-flight injection cleared; user observes status flip; turn resumes with checkpoint on new tail |
| **D. Queue-behind-current-turn** | Same as B but with an explicit "queued" UI state | Unbounded, but FE knows it's waiting | Same as B; FE renders "queued behind current turn" |

**Trade-offs.**

| Criterion | A. Reject | B. Wait-gate | C. Pause-first | D. Queue-behind |
|-----------|-----------|--------------|----------------|----------------|
| **UX on the common case (user mid-turn)** | Annoying — must pause manually | Magic — works, but unclear progress | Visible pause → resume; user understands | Magic but visible waiting state |
| **State safety** | Best | Worst (mutates during quiescent window AFTER current turn ends; resume handle may be fragile per Phase 4b/4c deferred work) | Best (convention) | Worst |
| **Reconcile_turn_mirror compatibility** | Trivial (no mutation) | Fragile (mutation happens at graph boundary, mirror row already in transition) | Best (mirrors under pause are quiescent) | Fragile |
| **Pattern consistency with codebase** | New pattern | New pattern | Matches watchover | New pattern |
| **Mid-flight injection behavior** | Unchanged (FE retries) | The injection slot drains on next agent_node pull — but pause cleared it under C | Pause clears the injection slot (`daemon/services/instance_lifecycle.py:2927` mentions `injection_consumed` SSE during pause) | Drained naturally on next turn |

**Evidence.**

- Pause-first convention: `pause_instance_cascade` is THE pattern; documented in `decisions.md` and used by watchover (`watchover_service.py:1004, :1470`) and `instance_messaging.py:1119, :3748`, facade `manager.py:7948`.
- `wait_for_instance_quiescent`: best-effort, 30s default, never raises (`daemon/manager.py:3362-3431`).
- astream-overwrite semantics: in-flight commits overwrite external writes at node boundaries (no merge).
- RAM injection FIFO: `_pending_injections` manager.py:643 definition, accessed :2462+, :2528; corrected 2026-08-31 from the prior `:2398` anchor; cleared during pause via `injection_consumed` SSE (lifecycle.py:2927).
- Claim gate: `daemon/repositories/task/repository.py:1146` (`claim_pending_task` — corrected 2026-08-31 from the prior `:646-671` anchor) — PAUSED-no-task is effectively quiescent (workers block). Companion `has_instance_busy` at `:543` is the broader PENDING/RUNNING/PAUSED predicate and counts PAUSED rows — stale-busy read between probe and gate-acquire is possible (benign: re-check under the gate with retry-once).
- Turn-reconciler migration phase 4b/4c deferred — reconcile_turn_mirror(work_id) not yet authoritative for all paths (`decisions.md`, recent history).

**Preliminary recommendation: C (pause-first-then-quiesce) with A (reject) as a documented fallback when `wait_for_instance_quiescent(30)` returns False.**

- Primary path: pause → quiesce → compact → resume.
- Fallback: if quiescence times out, return 503 with `state="pause_failed"`; the FE can offer the user "retry as IDLE" or "force-compact after explicit pause".

**Confidence: Medium.** The pause-first convention is well-established. The interaction with mid-flight RAM injection needs explicit FE messaging — the user may have typed a follow-up message that disappears into the cleared FIFO.

**Decision owner note.** Architect to confirm:
- Should the command emit an `injection_consumed` mirror SSE event during pause so the FE can mark the cleared follow-up message as "not delivered"? Yes — keeps FE's pending-injection card honest.
- Should we surface `wait_for_instance_quiescent(30)` failures as retryable errors or hard-fail the command? Recommend retryable (the user may intervene by pausing manually).

---

### Q5. Trim-fallback checkpoint consistency

**Context.** Today's `_truncate_fallback` (`daemon/compaction.py:1081-`) is destructive: it strips a window's worth of messages WITHOUT generating a summary, so the FE sees only the truncated tail (no "Compacted at HH:MM" marker, no explanation). On-demand compaction must do better. Requirements:

1. **#5 never leave user stuck** — fallback must produce a usable checkpoint.
2. The fallback must not clobber the synthetic system message that `daemon/persistence.py:404-449` prepends independently of checkpoint content.
3. The fallback must respect the D3 sentinel contract (RemoveMessage + add_messages; list assignment is the safe pattern, not extend).
4. The fallback must not enter the dangerous emergency_truncate path on a forced compaction (Q6) when `compactable` is empty.
5. The fallback should preserve a SMALLER recent window than automatic compaction (forced = user wants aggressive reduction).
6. The fallback must produce a checkpoint that `reconcile_turn_mirror(work_id)` can reconcile when the user resumes (note: mirror rows suppressed while paused/running).

**Options.**

| Option | Description | Markers visible | Data loss |
|--------|-------------|-----------------|-----------|
| **A. `_truncate_fallback` as-is** | Reuse today's destructive path; no summary | None | All removed messages; no audit |
| **C. Trim + marker SystemMessage** | Trim + insert a single SystemMessage("`/compact` trimmed N messages (M tokens) at <ts>; reason: <err>") at the boundary; preserved-tail matches auto-compaction window | One marker | Same data loss but auditable |
| **D. Trim + tiny LLM summary** | Same trim, plus call LLM with a short prompt to produce 1-paragraph summary of the dropped block; cap at 50 tokens | Marker + summary | Same; richer recovery context |

**Trade-offs.**

| Criterion | A. As-is | C. Marker | D. Marker + summary |
|-----------|---------|-----------|---------------------|
| **Recovery context for user** | None | Timestamp + reason + count | Same + human-readable digest |
| **LLM cost on fallback** | Zero | Zero | One short call (another 30s exposure — but on the timeout path, the model is already proving unreliable) |
| **Window size** | Same as auto | Smaller | Smaller |
| **Test surface** | Zero new code | Marker message + new audit hook | Same + summary call surface |
| **Destructive semantics** | Worst | Better | Best (preserves user recall via summary) |
| **Risk of double-failure** | N/A | N/A | Summary call itself can timeout — needs a no-summary sub-mode |

**Evidence.**

- `_truncate_fallback`: `daemon/compaction.py:1081-` — destructive path, no summary.
- D3: aupdate_state with list assignment (CONCATENATES under add_messages); sentinel via RemoveMessage.
- Synthetic system message: `daemon/persistence.py:404-449` — prepended independently of checkpoint; a trim never deletes the synthetic rebuild path.
- emergency_truncate: `daemon/compaction.py:701-710` — separate code path; truncates preserved groups; triggered when `compactable` is empty.
- recent_message_window default 10 (`daemon/config.py:713`); min_recent_window 3 (`:714`).
- Per-C3 rule: "Partition injected messages out of the candidate list" (`daemon/compaction.py:622-639`) — fallback must respect this.

**Preliminary recommendation: C (trim + marker SystemMessage) with D as a follow-up.** The marker message costs nothing, gives the user a recovery cue ("I had a fallback at 14:32"), and the LLM summary call can be deferred to a v2 since the timeout path is unreliable anyway.

- Marker SystemMessage content: `"[/compact] Trimmed N messages (M tokens) at <iso>; reason: <summary of error>."`
- Window size: smaller than auto — recommend `min_recent_window` (3 groups) as floor.
- Summary of error: sanitized — never include raw exception text in the checkpoint (it goes to the LLM next turn).

**Confidence: Medium.** The as-is path is clearly inadequate for user-triggered fallback (the user expected success). The marker-only option is the minimum that meets requirement #5. The LLM-summary option can come later.

**Decision owner note.** Architect to confirm:
- Should the fallback marker be a regular `SystemMessage` or a new "CompactionEvent" message kind (FE-renderable)? Recommend regular SystemMessage to avoid touching the FE rendering pipeline — pending-injection card already shows the state.
- Should we emit a separate SSE event for fallback-applied so the FE can render "Compaction used fallback (truncated)" rather than just "success"? Recommend yes — the user's expectation was summary-based compaction.

---

### Q6. Force-flag design: extend `compact_state(force=True)` vs new orchestration over pure helpers

**Context.** `compact_state` refuses below threshold (0.80 × window), within 60s dedup, under 10 min-messages (`daemon/compaction.py:618-664`). The three refusal gates are:

1. Dedup: `if context.last_compacted_at and self._is_recently_compacted(...)` — line :618-620.
2. Empty/injection-only: `if not regular_messages` — line :634-640.
3. Min-messages: `if len(regular_messages) < context.config.min_messages_before_compaction` — line :645-651.
4. Threshold: `if total_tokens <= context_window * context.config.threshold` — line :659-664.

The on-demand path needs to compact regardless of threshold (user asked for it). It MAY also want to bypass dedup (user expects compaction every time they ask). It almost certainly should NOT bypass min-messages — compacting 2 messages is silly and produces noise. Threshold bypass is the load-bearing case.

**Options.**

| Option | Description | Blast radius | Dedup behavior | Min-messages |
|--------|-------------|--------------|----------------|--------------|
| **A. `compact_state(force: bool = False)`** | Add one boolean parameter; gates threshold check only; dedup and min-messages still enforced | Tiny — single boolean; both auto paths unaffected when default False | Still enforced | Still enforced |
| **B. `compact_state(force_bypass: list[str])`** | Named bypass flags: `{"threshold", "dedup", "min_messages"}`; caller picks | Medium — adds a config-like param | Caller-controlled | Caller-controlled |
| **C. New orchestrator over pure helpers** | Don't touch `compact_state`; compose `identify_boundary_groups` + `select_compactable_groups` + `_summarize_*` + `_build_replacement_messages` directly in the command handler | Largest — duplicate orchestration logic; risk of drift with the two auto paths | Free choice | Free choice |

**Trade-offs.**

| Criterion | A. force:bool | B. force_bypass:list | C. Orchestrator over helpers |
|-----------|--------------|---------------------|-----------------------------|
| **Blast radius on auto paths** | Zero (default False) | Zero (default empty) | Zero (no shared code modified) |
| **Shared improvements (Q7 timeout)** | Apply for free | Apply for free | Must be re-applied per orchestrator |
| **Test surface** | Two new tests (force=True summary + force=True threshold-bypass) | 2^N tests if combinable | New tests + duplication drift risk |
| **Drift risk** | Low (single function) | Low (single function, list param) | Highest — three places to update when logic changes |
| **Behavior when force is set but instance has nothing to compact** | Returns None (existing behavior under threshold) | Same | Caller decides |
| **Future flexibility (force→aggressive-target)** | Easy: add `force_target_ratio: float | None = None` | Awkward: list of named bypasses | Free choice |

**Evidence.**

- `compact_state` shape: `daemon/compaction.py:608-664`.
- Pure helpers: `identify_boundary_groups` (:280), `select_compactable_groups` (:372), `_summarize_*` (:795-978), `_build_replacement_messages` (:1044), `emergency_truncate` (:421), `_truncate_fallback` (:1081).
- Two call sites: proactive `_maybe_compact_context` (`daemon/services/instance_messaging.py:1116-1220`), reactive CLE retry (`daemon/graph.py:3489-3619`).
- Reactive path passes `system_prompt_tokens=0` (`daemon/graph.py:3506`) — already a known inconsistency (Q7).

**Preliminary recommendation: A (`force: bool = False`) for the first command, with a planned v2 (B or A + `force_target_ratio`) once we have a second command that needs different semantics.**

- Bypass threshold only.
- Dedup still enforced (the user can call `/compact` twice in 60s and the second call is a no-op).
- Min-messages still enforced (no semantic value in compacting 2 messages).
- Window target: same as auto — the orchestrator picks `recent_message_window` and `target_ratio` from the instance's `compaction` config; no new knob for v1.

**Confidence: Medium.** A is the minimum and composes well with Q7 (timeout changes flow to all three paths). B is more flexible but adds API surface that no consumer currently needs. C is over-engineered for the first command.

**Decision owner note.** Architect to confirm:
- Is dedup enforcement the right default for user-triggered? Some users may expect "force every time". Recommend keep dedup (force can be added later as a separate flag) — surprise surprise if `/compact` does nothing.
- Should `force=True` also lift the 60s dedup window? Same answer — leave as is.

---

### Q7. Adaptive timeout placement & scope

**Context.** Today's timeout stack:

1. `asyncio.wait_for(..., timeout=30.0)` literal at `daemon/compaction.py:1027-1039` — belt-and-braces backstop.
2. `wall_clock_cap_s=45.0` default at `daemon/services/llm_failover.py:529` — primary defense inside the retry loop.
3. NO whole-operation cap (chunked = N sequential calls, worst ≈ N × 30s).
4. Reactive path passes `system_prompt_tokens=0` (`daemon/graph.py:3506`) — the token estimator's input is incomplete vs the proactive path which passes the actual system prompt tokens (`:1153`).

Formula target: base 90s + ~60s/100k context tokens, hard cap 300s.

**Options.**

| Option | Description | Token basis | Whole-op cap | Configurable? |
|--------|-------------|-------------|--------------|----------------|
| **A. Replace 30s literal; estimator = context.messages only** | Plug formula at :1038; estimator = `estimate_messages_tokens(context.messages)` | Context messages only | No | Hardcoded |
| **B. Replace 30s + thread wall_clock_cap_s + add whole-op cap; estimator = context.messages + system_prompt_tokens** | Plug formula at :1038; thread `wall_clock_cap_s` at :1011; add a wrapping `asyncio.wait_for(compact_state, timeout=300)` at the call site | Context + system prompt | Yes (300s) | Hardcoded for first command |
| **C. Same as B + config knobs (COMPACTION_TIMEOUT_BASE_S, COMPACTION_TIMEOUT_PER_100K_S, COMPACTION_TIMEOUT_CAP_S)** | All knobs | Configurable | Yes | Yes |

**Trade-offs.**

| Criterion | A. Replace literal | B. Replace + thread + op cap | C. With config |
|-----------|--------------------|-----------------------------|----------------|
| **Reactive path benefit** | Yes (uses `system_prompt_tokens=0`, formula sees smaller number → smaller budget; arguably a regression) | Yes (reactive path's 0 is still wrong — fix separately) | Yes |
| **Whole-op predictability** | None — N calls × timeout each | 300s cap | 300s cap (configurable) |
| **Token count for budget** | Messages only | Messages + system prompt | Same |
| **Config surface** | None | None | 3 new env knobs (CONSISTENT with COMPACTION_* prefix in `daemon/config.py:706-753`) |
| **Test impact** | New test for the formula | New tests + integration test for whole-op cap | Most tests |
| **Inconsistency between proactive/reactive** | Stays | Fixed (or at least named as a known inconsistency to fix) | Same |

**Evidence.**

- 30s literal: `daemon/compaction.py:1038`.
- wall_clock_cap_s: `daemon/services/llm_failover.py:529` (45s default).
- Reactive inconsistency: `daemon/graph.py:3506` (`system_prompt_tokens=0`).
- Token estimator: `daemon/loader.py:465-499` (tiktoken cl100k_base, same as context_usage SSE).
- Proactive path: `daemon/services/instance_messaging.py:1153` (real system_prompt_tokens).
- Per-call timeout is per-summarization-call (N calls for chunked); whole-op cap missing.
- Chunked summarization: `daemon/compaction.py:812-831` — `summarization_chunk_threshold=0.60` triggers chunking; N chunks × per-call timeout = N × 30s today.

**Preliminary recommendation: B (replace + thread + whole-op cap), applied to all three paths (proactive / reactive / on-demand) so the formula lives in one place.**

- Base 90s + 60s/100k context tokens, cap 300s.
- Token basis: `context.messages` (proactive and on-demand both pass system_prompt_tokens; reactive's 0 is a known bug — fix in same patch).
- Whole-op cap at the call site via `asyncio.wait_for(compact_state_or_orchestrator, timeout=300)`.
- Hardcoded for first command; config knobs (option C) can land in v2 once operator feedback exists.

**Confidence: High** that B is the right shape. **Medium** on whether reactive's `system_prompt_tokens=0` should be fixed in this patch or separately — recommend fixing in same patch (one-line change, named in commit message).

**Decision owner note.** Architect to confirm:
- Should the formula be in `daemon/compaction.py` (shared by all callers) or in a new `daemon/services/compaction_timeout.py` module? Recommend in-place — single function, callers pass it.
- Should the timeout apply per-call (current 30s) or per-operation (new 300s)? Recommend both — per-call remains the backstop; per-operation is the new hard cap.

---

### Q8. Command subsystem shape

**Context.** This is the first command of a subsystem. Subsequent commands (e.g. `/reset`, `/clear`, `/status`, `/export`) will follow. The shape decision today constrains every future command. Relevant axes:

1. **Where the registry lives** — router-level dict vs service-layer dispatcher.
2. **Command definition shape** — dataclass with fields (name, description, param spec, applicability, handler).
3. **Where unknown-command validation lives** — router (sync 4xx) vs service.
4. **Literal `/` escape convention** — `//` for plain text (Slack convention), or a UI-only escape.
5. **Rate limiting / abuse** — a 5-min LLM operation is user-triggerable; can a malicious FE spam `/compact` 10x/s?
6. **Enable/disable config** — operator kill-switch per command (env knob).
7. **Future surface** — agent-tool-invocable commands (worker spawns a sub-command), API-only clients (CLI, curl, source adapters).

**Options.**

| Axis | A. Router-level dict | B. Service-layer dispatcher |
|------|---------------------|------------------------------|
| **Registry home** | `daemon/routers/messages.py` has a top-level `_COMMANDS = {"compact": _handle_compact}` dict | New `daemon/services/command_dispatcher.py` with `CommandRegistry` class |
| **Testability** | Mock the router (slower) | Unit-test the dispatcher (fast) |
| **Discovery** | Implicit (grep the router file) | Explicit (registry's `.list()`) |
| **Order with message flow** | Easy — next to the slash-detection line | One extra import |

**Command definition shape.**

| Option | Shape |
|--------|-------|
| **X. Simple dict** | `{"name": str, "handler": Callable}` |
| **Y. Dataclass** | `@dataclass class Command: name, description, param_spec, applicability, handler, rate_limit, enabled` |

**Trade-offs.**

| Criterion | A. Router-dict + X | B. Service-dispatcher + Y |
|-----------|--------------------|--------------------------|
| **Time-to-first-command** | Fastest | Slightly slower |
| **Subsystem extension cost** | Highest (every new command = router edit) | Lowest (register a Command) |
| **Test surface** | Integration-heavy | Mix of unit + integration |
| **Command UX consistency** | Manual | Enforced by shape |
| **Future agent-tool commands** | Hard to share | Natural (agent invokes registry) |

**Escape / abuse / enable / rate-limit options.**

| Concern | Option |
|---------|--------|
| **Literal `/`** | `//` → FE strips one `/`; BE sees plain text; both `/compact` and `//compact` (literal) possible |
| **Rate limit** | Per-instance token bucket; `/compact` rate = 1 per 60s; second call within window returns ack=success with `noop: true, reason: "rate_limited"` |
| **Enable/disable** | `commands.compact_enabled` env (default True); False → router returns 410 Gone with code `command_disabled` |
| **API-only clients** | Commands available on `POST /messages` (any HTTP client) |

**Evidence.**

- D13 / JAFP: internal ops are NOT JobItems (`decisions.md`).
- wc-wake-report-integrity used a new "durable wake" branch on existing POST (`daemon/routers/messages.py:182-188`) — same pattern applies for command intercept.
- Source adapters (Discord, Slack) already have a `/new` slash command (`daemon/sources/adapters/discord/adapter.py:1112`, `daemon/sources/adapters/slack/adapter.py:889`) — precedent exists; web-side command dispatch is new.

**Preliminary recommendation: B (service-layer dispatcher) + Y (Command dataclass).** For a first command, this is more shape than option A+X, but:

1. The Command dataclass centralizes applicability, rate-limit, enable, and description — without it, each command re-implements those checks.
2. The service dispatcher becomes the natural API surface for future agent-tool invocations.
3. Future commands can land in parallel without touching `daemon/routers/messages.py`.

**Sub-options:**
- Escape: `//` (one-char prefix, FE-only strip) — single-character cost, easy to test.
- Rate limit: per-instance 1-per-60s; second call returns ack=success with `noop: true`.
- Enable/disable: `commands.compact_enabled` env (default True), 410 Gone on False.
- Future surface: document that the dispatcher is callable from non-FE clients; agent-tool invocation is a v2 concern.

**Confidence: Medium.** B+Y is more boilerplate for one command; the value shows on the second command. Recommend landing it now to avoid retrofitting.

**Decision owner note.** Architect to confirm:
- Is the rate limit in scope for v1? Recommend yes — explicit token bucket keeps the LLM cost under control.
- Should the Command dataclass expose `progress_event_schema` (the event shape Q9 commits to)? Recommend yes — couples Q8 to Q9.

---

### Q9. Progress event granularity

**Context.** SSE event_type is freeform (`daemon/services/live_event_hub.py:150-173`); the FE adds an `addEventListener` per type. The FE already has a `pending-injection card` template (`sse.service.ts:249-600` and the chat-interface component) — the command lifecycle can mirror that template. Today, no SSE fires at compaction time (only `context_usage` refresh via `emit_context_usage_for_instance`, `daemon/services/instance_messaging.py:1061-1114`). The phases the FE expects (per baseline contract #3):

1. `waiting` — POST accepted; orchestrator not yet started (e.g. queued behind RUNNING wait).
2. `in_progress` — orchestrator running; first LLM call sent.
3. `success` — compaction completed with summary.
4. `timed_out` — adaptive timeout exceeded; fallback invoked.
5. `fallback_applied` — trim fallback succeeded.
6. `failed` — unrecoverable error.

**Options.**

| Option | Phases | Heartbeat | Token counter | Schema stability |
|--------|--------|-----------|---------------|------------------|
| **A. 4 phases** | `waiting → in_progress → success | failed` | None | Freeform |
| **B. 5 phases** | `waiting → in_progress → success | timed_out → fallback_applied | failed` | None | Freeform |
| **C. 6 phases + heartbeat + token counter** | `waiting → in_progress (with periodic heartbeat) → success | timed_out → fallback_applied | failed` | Yes (10s) | Versioned (`schema_version` field) |
| **D. 6 phases + chunked progress** | `waiting → chunk N/total → in_progress → success | timed_out → fallback_applied | failed` | Yes | Versioned |

**Trade-offs.**

| Criterion | A. 4 phases | B. 5 phases | C. 6 phases + heartbeat | D. 6 phases + chunked |
|-----------|-----------|-----------|--------------------------|----------------------|
| **FE rendering simplicity** | Low | Low | Medium (progress bar) | High |
| **Matches user mental model** | No (timeout/fallback invisible) | Yes (the user requested a fallback expectation) | Same + heartbeat | Same + per-chunk progress |
| **Operator observability** | Poor (silent during 5min) | Poor | Good | Best |
| **Implementation cost** | Trivial | Low | Medium (heartbeat loop) | High |
| **Backwards compat** | Trivial | Easy | Easy (additive) | Harder |

**Evidence.**

- LiveEventHub custom event_type: `daemon/services/live_event_hub.py:150-173` — any string works.
- FE sse.service.ts:249-600 — `addEventListener` per event type; pending-injection card is the template.
- Today no SSE at compaction time: only `context_usage` refresh, `daemon/services/instance_messaging.py:1061-1114`.
- Baseline contract #3: FE expects `waiting|in_progress|success|timed_out|fallback_applied|failed`.
- F3 rule (per explorer report): emit BEFORE pause-flag mutation or event is lost.

**Preliminary recommendation: C (6 phases + heartbeat + token counter + versioned schema).**

- 6 phases match baseline contract.
- Heartbeat at 10s cadence during `in_progress` keeps the connection "warm" through proxies that close idle sockets.
- Token counter (before/after) is cheap and gives the FE a progress bar.
- Schema versioning (`schema_version: 1`) lets future commands ship v2 without breaking FE.

**Confidence: Medium.** C is more surface than B for a first command, but the heartbeat and counter are nearly free in code and pay back in operator observability (per `decisions.md` patterns and recent debug history).

**Decision owner note.** Architect to confirm:
- Heartbeat cadence — 10s is a guess. Some proxies close after 30s idle; some after 5min. Recommend 10s with a note that proxy-specific tuning may be needed.
- Token counter — is the proactive path's `system_prompt_tokens` available mid-compaction? If yes, expose; if not, only expose context-messages tokens.

---

## Recommended Composite Baseline

This is the coherent combination that matches what the phase planners assumed where possible, with explicit deltas.

| Layer | Phase planner assumption (per baseline contract + research) | Recommendation | Delta |
|--------|-----------------------------------------------------------|----------------|-------|
| **Transport** | FE posts `/compact` via existing `POST /messages`; BE intercepts | **Match.** BE router-level detection at `daemon/routers/messages.py:243`. | None. |
| **Ack shape** | `{status:"command", command, command_id, state:"waiting", ...}` 202 | **Match.** | None. |
| **SSE contract** | `command_progress` event_type, `command_id` correlation, phases `waiting|in_progress|success|timed_out|fallback_applied|failed` | **Match + extend.** Add 10s heartbeat + before/after token counter + `schema_version` field. | Schema version is new. |
| **GET fallback** | GET endpoint keyed by command ID | **Match + extend.** Add `last_seen_event_id` for incremental sync. | Incremental sync is new. |
| **Unknown command** | Sync 4xx error ack | **Match.** | None. |
| **Concurrency** | Pause-first for busy instances | **Match.** Convention-compliant with watchover. | None. |
| **Trim fallback** | "On timeout/failure: trim-based fallback (hard truncation of oldest)" | **Extend.** Trim + marker SystemMessage (Q5 option C) — auditable, zero-cost. | Marker message is new. |
| **Adaptive timeout** | "Base 90s + ~60s/100k context tokens, hard cap 300s (general improvement to compaction)" | **Match + extend.** Apply to all three paths (proactive/reactive/on-demand). Reactive's `system_prompt_tokens=0` bug fixed in same patch. | General improvement scope is broader than baseline. |
| **Subsystem shape** | First command of extensible slash-command subsystem | **Extend.** Service-layer dispatcher + Command dataclass for forwardability. | Dataclass is new. |

**Composite decision (architect to confirm):**

```text
Transport:  BE-side intercept at POST /messages, /detect at routers/messages.py:243
Lifecycle:  pause→quiesce→compact→resume for RUNNING/WC; direct for IDLE/QUEUED/PAUSED; reject for terminal
Execution:  async ack 202 + background coroutine; SSE command_progress + REST GET fallback
Force:      compact_state(force=True) — bypass threshold only, keep dedup and min-messages
Timeout:    base 90s + 60s/100k tokens, cap 300s; whole-op cap at call site; applied to all three paths
Fallback:   trim + marker SystemMessage; smaller recent window than auto
Schema:     Command dataclass in service-layer dispatcher; CommandRegistry.list() for future tool surface
Rate limit: per-instance 1-per-60s; second call returns noop=true
Escape:     // for literal /; FE strips one /
Progress:   6 phases + 10s heartbeat + before/after token counter + schema_version: 1
```

---

## Open Questions

Items the architect or downstream phase planners may need to answer.

1. **WAITING_CHILDREN under flag-ON (wc-wake-report-integrity).** Does the slash command's pause-first path apply uniformly to WC regardless of the `ENSEMBLE_WC_WAKE_ENQUEUE` kill-switch? Recommend yes.
2. **Command definition object scope.** Is the Command dataclass shared with agent-tool invocation in v1, or strictly HTTP-triggered? Recommend HTTP-only v1; agent-tool invocation v2.
3. **Per-instance rate limit reset on pause/resume.** If user pauses + manually compacts, does the 60s window reset? Recommend yes.
4. **Audit log of `/compact` invocations.** Do we want a CommandInvocation audit row? The JFP migration moves internal ops off JobItems, but a lightweight audit table could land separately. Out of scope for v1.
5. **Test coverage.** Q5's marker SystemMessage + Q7's reactive `system_prompt_tokens=0` fix both need explicit unit tests. The reactive path's existing tests at `daemon/graph.py:3489-3619` are integration-heavy — pytest fixtures needed.
6. **Migration path for v2 commands.** When the second command lands, does it register via `CommandRegistry.register()` decorator or via an explicit module-level call? Recommend decorator for symmetry.
7. **Cross-tree (parent + child) semantics.** Should `/compact` on a parent cascade-pause the whole tree (matching `pause_instance_cascade(cascade_to_root=True)` default) or only the target subtree? `/compact` is per-instance — recommend default cascade_to_root=False to avoid surprising side-effects on the user's other tabs. **WAIT** — the architect should weigh this against the `decisions.md` Pause-First-Then-Quiesce convention (which defaults to cascade-to-root).
8. **Tool messages preservation under trim.** `_truncate_fallback` keeps the recent window (default 3 groups). ToolMessages (function-call responses) belong to recent AIMessages — trimming a tail that includes an unanswered tool call re-introduces the CLE retry problem (see graph.py:3527-3542 pairing-guard). Recommend: trim respects the pairing guard — never leave an unanswered `AIMessage(tool_calls)` in the trimmed tail.

---

## References

- `daemon/compaction.py:608-664` — compact_state signature and refusal gates.
- `daemon/compaction.py:1027-1039` — 30s `asyncio.wait_for` literal (Q7 plug point).
- `daemon/compaction.py:1081-` — `_truncate_fallback` destructive path.
- `daemon/compaction.py:812-831` — chunked summarization threshold (Q7 multi-call risk).
- `daemon/routers/messages.py:159-553` — POST handler with status routing and the SEAM at :243.
- `daemon/routers/messages.py:402-482` — RUNNING injection path with `injection_pending` SSE (Q3 pattern source).
- `daemon/services/instance_messaging.py:1116-1220` — `_maybe_compact_context` proactive hook.
- `daemon/services/instance_messaging.py:1146-1150` — terminal-checkpoint guard (Q2 risk).
- `daemon/services/instance_messaging.py:1486-1510` — terminal revive-on-send (Q2 behavior).
- `daemon/services/instance_messaging.py:1061-1114` — `emit_context_usage_for_instance` (Q9 evidence: no SSE at compaction today).
- `daemon/graph.py:3489-3619` — reactive CLE retry compaction.
- `daemon/graph.py:3506` — reactive `system_prompt_tokens=0` inconsistency (Q7).
- `daemon/services/live_event_hub.py:150-173` — custom event_type support (Q3 + Q9 contract).
- `daemon/services/instance_lifecycle.py:2685-2971` — pause_instance_cascade (Q2 + Q4).
- `daemon/services/instance_lifecycle.py:2971-3093` — resume_instance_cascade.
- `daemon/manager.py:3362-3431` — wait_for_instance_quiescent (Q4 barrier).
- `daemon/services/execution_gate.py:108-143` — per-instance asyncio.Lock (Q4 gate).
- `daemon/services/llm_failover.py:529` — wall_clock_cap_s=45s default (Q7 backstop).
- `daemon/persistence.py:404-449` — synthetic system message prepend (Q5 evidence).
- `daemon/loader.py:465-499` — tiktoken cl100k_base estimator (Q7 token basis).
- `daemon/config.py:706-753` — CompactionConfig (no adaptive timeout knob today).
- `daemon/repositories/task/repository.py:1146` — `claim_pending_task` claim gate (Q2 PAUSED-quiescence; corrected 2026-08-31 from the prior `:646-671` anchor).
- `frontend/src/app/components/message-input/message-input.component.ts:163-190` — FE clearInput contract (Q1).
- `frontend/src/app/pages/chat/chat.component.ts:1225-1362` — FE send flow (Q1, Q3).
- `decisions.md` — Pause-First-Then-Quiesce convention, JAFP, D13 internal-op-not-JobItem.
- `decisions.md` — Turn-Reconciler Migration Phase 4b/4c (deferred); reconcile_turn_mirror(work_id) authoritative.
- `decisions.md` — WC-wake Report-Integrity kill-switch `ENSEMBLE_WC_WAKE_ENQUEUE` (Q2 WC question).
- `decisions.md` — Pattern f orphan-job sweep (Q3 durability note).
- Recent history — F9+F16 legacy status derivation (out of scope for this analysis).
- Recent history — message-display-latency Phase 1 echo_id pattern (Q3 SSE shape).
- Recent history — WC-wake-report-integrity Phase 1 (Q2 WC question).
- Recent history — Message display latency (Q3 SSE echo timing — out of scope here).

---

*End of technical-analysis.md. Architect: enrich this options map with final decisions in `decisions.md`. Phase planners: derive phase1-plan.md and phase2-plan.md from the recommended composite baseline. This document does NOT contain final decisions — only the evidence base and preliminary verdicts.*