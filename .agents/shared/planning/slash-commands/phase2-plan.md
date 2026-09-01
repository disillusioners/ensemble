# Phase 2: Frontend — `/compact` Slash-Command UX (Angular 21)

Date: 2026-08-31 (rev. 2 — architect contract pinned; rev. 3 — post-review adjudication (C1 + O8–O13) folded in 2026-08-31)
Author: plan-creation worker (for planner[v2] synthesis)
Status: Revised — wire contract PINNED to `architecture-recommendation.md` §7 (post-review §7 amendment: `compacted_type` enum + per-value FE copy table, 2026-08-31); FE open items retained in §Open Questions (architect 2026-08-31)
Feature: Slash-command subsystem frontend, shipping `/compact` (on-demand context compaction) as the first command
Workdir: `agents-ensemble` @ `feature/slash-commands` (branch verified)

> **Framework note (binding):** The frontend is **Angular 21** — standalone components, Angular Signals, Material/CDK, RxJS HttpClient. Every "component/service/signal/template" statement below refers to Angular, not React. No state library is in use (signals only, per Explorer A).

---

## Objective

Users can type `/compact` into the chat message box for the selected instance and watch a non-blocking UI progress through **waiting → in progress → success** (or **timed-out → fallback-applied** / **failed** / instant **noop**), with the timeline reflecting the compacted context afterward — while the message input stays usable and the UI survives SSE drops, daemon restarts, instance switches, and server-side work lasting up to 5 minutes (server timeout ≈ 90s base + ~60s/100k tokens, hard cap 300s).

The subsystem lands as an **extensible command surface** (typed command registry, one SSE event channel, one state machine) so future commands (palette/autocomplete, `/clear`, `/help`) are additive — even though only `/compact` ships in this phase.

**Testable completion sentence:** Typing `/compact <Enter>` on a selected instance shows a status card that progresses through the documented state machine, resolves to success/fallback/noop/failed with detail (tokens before/after when provided), never blocks the input for normal messages, and recovers correctly across SSE reconnect, daemon restart, and instance switch — verified by the Playwright + Jest suites in §Test Strategy.

---

## Scope

### In Scope

- **Command input handling** — `/`-prefixed content detection, `//` escape hatch (check `//` BEFORE `/`; strip one slash, deliver as text — architect O-B1), client-side registry validation (advisory pre-check; the BE 400 `UNKNOWN_COMMAND` + available-commands response is authoritative), duplicate-command guard (UX pre-check; BE `busy`/`rate_limited` refusals authoritative), send-path integration in `ChatComponent.onSendMessage`. (architect 2026-08-31)
- **Send-path integration under the BE-side intercept (RATIFIED)** — `/compact` is posted as ordinary message content to the existing `POST /api/instances/{id}/messages`; BE router-intercepts (architect §8 Q1: "Adopt baseline — BE-side router intercept"); FE discriminates the command-ack response and branches.
- **SSE `command_progress` listener** — new event listener + typed signal in `SseService.connectInternal()`, correlated by `command_id`, with `phase_seq` dedup/reorder guard (architect §7).
- **REST fallback fetch + bounded polling** — GET `/api/instances/{id}/commands/active` on chat load, on reconnect, and on a ~5s cadence while the card is active AND SSE is dead; silent card-clear on `{exists:false}` (architect §7, §9-11/13).
- **Command state machine** — plain-TS, logic-mirror-testable service: `idle → waiting → in_progress → success (summary|truncation|noop) | timed_out → fallback_applied | failed`, per-instance keyed, `phase_seq`-guarded, age-based stale eviction.
- **Rendering** — out-of-timeline active-command status card (recommended; still FE-open, see Open Questions Q3) modeled on the `.pending-injection-card` pattern, with `role="status"` / `aria-live="polite"`.
- **Post-compaction history refresh** — message-list refetch **triggered by the terminal SSE event** (architect §7 FE notes) + token-meter update via existing `context_usage` signal.
- **Extensible surface** — `CommandDefinition` typing + registry service; the autocomplete palette is a **clearly separated stretch task** (Task 10).
- **Tests** — Jest logic-mirror unit tests (no TestBed, house style), Playwright e2e spec, `ng build` strictTemplates gate.

### Out of Scope

- **Backend command subsystem** (intercept seam, dispatcher, compaction execution, adaptive timeout, `command_progress` publishing, GET fallback endpoint, rate limiting) — Phase 1 plans this; the architect has pinned its wire schema (§7). FE consumes the pinned contract (§Phase 1 Contract Dependencies).
- **Trim-based fallback implementation** — server-side (`_truncate_fallback` + marker line); FE only renders its outcome via `failure_kind` (§9-4).
- **Slash autocomplete palette as a must-have** — planned as stretch Task 10; ship-ready UI does not depend on it.
- **Command palette UI / multi-command arg parsing** — registry typing anticipates it; building it is not in this phase.
- **`//` escape backend semantics** — FE strips one slash and delivers as text (O-B1); the FE does not otherwise special-case escaped content.
- **New WebSocket infrastructure** — all realtime remains SSE (zero WebSocket routes today, per Explorer A).
- **`plan-overview.md`** — dispatcher synthesizes it from Phase 1 + Phase 2 files.

### Modules / files touched (LARGE-scope statement)

| File | Action |
|---|---|
| `frontend/src/app/models/index.ts` | Modify — `CommandAck`, `CommandProgressEvent`, `CommandPhase`, `RejectionReason`, `GetActiveResponse`, `CommandDefinition` types (§7 schema verbatim) |
| `frontend/src/app/services/command-registry.service.ts` | **New** — registry + validation |
| `frontend/src/app/services/command-state.service.ts` | **New** — state machine + per-instance map + fallback/poll orchestration |
| `frontend/src/app/services/sse.service.ts` | Modify — `command_progress` listener + `commandProgress` signal |
| `frontend/src/app/services/api.service.ts` | Modify — `getActiveCommand()`, POST response type union, 400 `UNKNOWN_COMMAND` mapping |
| `frontend/src/app/pages/chat/chat.component.ts` | Modify — `onSendMessage` command-ack/rejection branch, guards |
| `frontend/src/app/components/chat-interface/chat-interface.{html,scss,ts}` | Modify — active-command card |
| `frontend/src/app/components/message-input/message-input.component.ts` | Modify — `//`-escape + `/`-detection + inline validation (+ stretch: autocomplete) |
| `frontend/e2e/slash-command-compact.spec.ts` | **New** — Playwright e2e |
| `frontend/src/app/services/command-state.service.spec.ts`, `command-registry.service.spec.ts` | **New** — Jest logic-mirror |

---

## Phase 1 Contract Dependencies — **PINNED (architect §7, normative)**

The former P1-1…P1-6 ASSUMPTIONS are now pinned. The TypeScript blocks below are **the contract** — copied normatively from `architecture-recommendation.md` §7 (architect 2026-08-31). FE encodes them verbatim in `models/index.ts` (Task 1) and asserts them as an executable spec in the Jest adapter tests (Task 9).

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

// Per-value FE copy table (Task 6 — this is the source of truth):
//   compacted_type      | SSE phases                          | FE terminal copy
//   --------------------+-------------------------------------+----------------------------------------------------
//   "summary"           | → success                           | "Context compacted"
//   "partial_summary"   | timed_out → fallback_applied        | "Compaction timed out partway — kept the summarized
//                       |                                     |  sections, trimmed the un-summarized older section"
//   "truncation"        | timed_out → fallback_applied        | "Compaction timed out — history was trimmed without
//                       |                                     |  a summary"
//   "noop"              | → success (+ noop_reason)           | "Nothing to compact"

// GET /api/instances/{id}/commands/active   (fallback for SSE loss; auth mirrors GET /messages)
type GetActiveResponse = { exists: false } | { exists: true; command: CommandProgressEvent };
// Daemon restart ⇒ {exists:false} ⇒ FE clears card silently. Poll ~5s while card active AND SSE dead.
```

- **P1-1 PINNED (architect §7):** CommandAck shape above. FE needs satisfied: correlation id (`command_id` UUIDv4), initial state (`accepted`/`rejected`), human guidance (`detail`). **Split rule:** unknown command → **HTTP 400** `ErrorResponse{code:"UNKNOWN_COMMAND", detail:{available:[...]}}` (**additive (O13, 2026-08-31) over the existing `messages.py:222-229` validation-400 `{code, message}` envelope, NOT a mirror** — FE toast path already exists, `message-input.component.ts:240-243` pattern; `detail.available` later feeds slash autocomplete without a contract change); valid-but-refused → **200** ack `state:"rejected"` + reason enum `terminal_instance | busy | rate_limited | pending_injections | compaction_disabled | quiescence_timeout` (architect §7 / O-B9/O-B10).
- **P1-2 PINNED (architect §7):** `command_progress` SSE events on the existing per-instance stream (`EventSource('/api/instances/{id}/events')`, `sse.service.ts:257`) with the CommandProgressEvent schema above — including `phase_seq` (monotonic per command), server-clock `elapsed_ms`, advisory `eta_ms` (in_progress only), and the **10s in_progress heartbeat** (phase_seq+1, fresh timestamp/elapsed_ms). F3 remains binding: `waiting` emits BEFORE any pause mutation (D-B9). SSE emission stays best-effort (D-B10) — the GET fallback is mandatory companionship, not optional hardening.
- **P1-3 PINNED (architect §7):** `GET /api/instances/{id}/commands/active` → `{exists:false}` or `{exists:true, command: CommandProgressEvent}` — FE's reload/reconnect recovery needs no stored `command_id` (the prior `sessionStorage` fallback idea is dead). `ttl_seconds` (default 600) bounds the server-side memory window; heartbeats keep the entry alive while in progress.
- **P1-4 PINNED (architect §7):** Rejection semantics split as above. FE additionally validates client-side pre-POST (Task 5) as a UX pre-check only — the BE is the source of truth, and **every POST gets a NEW `command_id`** (no idempotency-key machinery; §6 idempotency ruling — a duplicate POST that slips past FE guards becomes a cheap `noop` via the recency pre-check, not an error).
- **P1-5 RESOLVED (architect §7 FE notes):** Post-terminal refetch of `GET /messages` is triggered **by the terminal SSE event** — FE does not wait for, or depend on, a compaction summary *message* arriving in the timeline. Task 7 wires exactly that.
- **P1-6 ABSORBED (architect §7):** The schema itself answers duration uncertainty: `eta_ms` (advisory, in_progress only) tunes the "still working…" hint; the 10s heartbeat proves liveness; server `elapsed_ms` is the timer source of truth. FE needs no additional duration contract.

**§6 interaction rulings that touch FE behavior (architect 2026-08-31):**
- **Queued messages during compaction** block on the ExecutionGate until compaction finishes — safe, bounded (≤300s), but user-visible latency. FE copy should mention it (Task 6).
- **Second command mid-command** → BE rejects `reason=busy` (in-flight) or `rate_limited` (inside min-interval) — FE's client-side duplicate guard is advisory UX, BE is authoritative.
- **Instance terminated/deleted mid-command** → executor emits terminal `failed` — never hangs; FE renders it like any failure.
- **Daemon restart mid-command** → registry lost by design (D-B8 ephemeral); `GET /commands/active` → `{exists:false}` ⇒ **FE clears the card silently, no error toast** (required FE behavior, §9-11 → Task 8).
- **Terminal instances** → rejected `reason=terminal_instance` with `detail` guidance (§5/O-B4) — FE renders the guidance verbatim (Task 5).

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Command types + registry service.** Add the **§7 schema verbatim** to `frontend/src/app/models/index.ts`: `CommandAck`, `CommandProgressEvent`, `CommandPhase`, `RejectionReason` (`terminal_instance \| busy \| rate_limited \| pending_injections \| compaction_disabled \| quiescence_timeout`), `GetActiveResponse` (`{exists:false} \| {exists:true, command}`), plus `CommandDefinition` (alongside `InstanceStatus`, `models/index.ts:2`). Create `command-registry.service.ts` (standalone `Injectable`, signals-based) holding `CommandDefinition[]` seeded with `{name:'compact', description, argsHint?:null, availability}`. Expose `parseCommandInput(content): {escape:true} \| {known:true, def} \| {known:false, reason} \| {isCommand:false}` implementing the `//` escape (check `//` BEFORE `/`; strip one slash, deliver as text — architect O-B1). | none | `parseCommandInput('/compact')` → known command; `'/foo'` → unknown; `'hello'` → not a command; `'//compact is useful'` → escape (text `'/', rest intact`); registry addition of a new command is a one-entry change (proven in unit test). Types compile against the §7 blocks verbatim. |
| 2 | **API layer: ack discrimination + GET fallback.** In `api.service.ts` (send path at `api.service.ts:187-191`): widen the POST `/api/instances/{id}/messages` response type to a union `MessageAck \| CommandAck`, discriminated by `status` (`'command'` vs existing `'queued'`/`'injected'`/`'auto_resumed'`); map HTTP 400 `UNKNOWN_COMMAND` to a typed error carrying `detail.available` for the existing toast path. Add `getActiveCommand(instanceId): Promise<GetActiveResponse>` per §7 (network error → `{exists:false}`-equivalent handled by caller; never throws — swallowed-error convention, `sse.service.ts:653`). Implement `parseCommandAck()` adapter (single parsing point) encoding the **pinned §7 CommandAck exactly** — this adapter's Jest test is the executable contract spec (architect 2026-08-31). | P1-1, P1-3 | Union type compiles under strictTemplates; `parseCommandAck` unit-tested for all four existing statuses + every pinned ack field (`state`, `reason` enum, `detail`, `ttl_seconds`), 400 `UNKNOWN_COMMAND` mapping, network-error swallowing. |
| 3 | **SSE `command_progress` listener + signal.** In `sse.service.ts` `connectInternal()` (:249-600): add one `addEventListener('command_progress')` block modeled on `injection_pending` (:512) — `ngZone.run()`, `JSON.parse`, typed — exposing `readonly commandProgress = signal<CommandProgressEvent \| null>(null)`. Apply the staleness guard pattern from `fetchPendingInjection` (:633 `currentInstanceId !== instanceId` → ignore). Forward the parsed event **with its `phase_seq`** — dedup/reorder filtering belongs to the state machine (Task 4), but the listener must not drop "old" `phase_seq` values itself (heartbeat `phase_seq+1` events are legitimate repeats). (architect 2026-08-31) | P1-2 | Jest logic-mirror test: parsed event lands in signal with intact `phase_seq`; event for a different instance is dropped; malformed JSON is swallowed without breaking the stream (matches error-handler convention of the 16 existing listeners). |
| 4 | **CommandStateService — the state machine.** New `command-state.service.ts` as a **plain-TS class** (logic-mirror house style — no TestBed): states `idle → waiting → in_progress → success \| failed`, plus `waiting\|in_progress → timed_out → fallback_applied`; success carries `compacted_type` (`summary` \| `partial_summary` \| `truncation` \| `noop` — post-review adjudication §7 amendment, 2026-08-31) + optional `noop_reason`. Holds `readonly activeByInstance = signal<Map<InstanceId, ActiveCommandState>>` (retain across switches so returning to the instance still shows progress). Reduces: POST ack (seed — **`accepted` ⇒ card in `waiting` immediately**, before any SSE event; the ack→first-SSE gap is normal and can be ≤30s on the RUNNING pause path, architect §7 FE notes), SSE events (**`phase_seq` monotonic guard: ignore events with `phase_seq ≤` last seen for the same `command_id`** — dedups 10s heartbeats and reorders out-of-order delivery; heartbeats update `elapsed_ms`/`eta_ms` but never advance phase or duplicate the card), GET fallback (reconcile: server state wins on reconnect). Instance-deleted-mid-command arrives as terminal `failed` — render like any failure (§6). Age-based eviction of terminal states after a short display window (pattern precedent: `evictPendingByAge`, `message-merge.util.ts:169`). Exposes `startCommand`, `onSseEvent`, `reconcileFromServer`, `stateFor(instanceId)`. **No client-side cancellation**: server owns the 90–300s budget. (architect 2026-08-31 + adjudication 2026-08-31) | 1, 2, 3 | Unit tests (Jest, no TestBed) cover every legal transition, every illegal transition (no-op + no throw), wrong-`command_id` SSE ignored, **`phase_seq` guard: stale/duplicate/heartbeat events don't regress state or double-apply**, ack-seed waiting-before-SSE, reconnect reconcile (server `success` after FE stale `in_progress` → success), `noop` and `partial_summary` terminal mappings (adjudication 2026-08-31), eviction after display window, two instances with independent commands. |
| 5 | **Chat component send-path integration + rejection UX.** In `chat.component.ts` `onSendMessage(payload)` (:1225-1362): before POST, run `parseCommandInput` — `//`-escape → deliver stripped text as a normal message (no command branch); unknown command → inline validation error via the existing `validationError` + 4s auto-dismiss pattern (`message-input.component.ts:240-243`), **no network call** (advisory; BE 400 `UNKNOWN_COMMAND` + `available` list is authoritative and feeds the same toast when it fires); known command while one is already active on this instance → inline "command already in progress" pre-check (advisory — BE `busy`/`rate_limited` refusals authoritative, §6). After POST: if `CommandAck` `accepted` — clear input (per the parent-owned clearing contract "don't clear until confirmed", `message-input.component.ts:163-175`), call `CommandStateService.startCommand` (card shows `waiting` from the ack); if `state:"rejected"` — do NOT start the machine; render reason-specific inline/card copy, and for `reason=terminal_instance` render the ack `detail` guidance **verbatim** ("Send a message to start a new turn, then /compact.", architect §9-12); other reasons (`busy`, `rate_limited`, `pending_injections`, `compaction_disabled`, `quiescence_timeout`) get short human copy + the reason shown. In either ack case **bypass `makeProvisionalMessage`/`mergeMessagesById` entirely** (dedup-safety: a distinct command must not enter the message echo/merge pipeline, emit-twice-same-id contract). Normal-message path unchanged. Extend the instance-switch TOCTOU guard (`sentInstanceId`, :1256-1261): command ack applied only if `sentInstanceId === currentInstanceId`. (architect 2026-08-31) | 1, 2, 4 | Typing `/foo` + Enter shows inline error <500 ms with zero network request (Playwright-assertable via request interception count); `'//compact'` posts as literal text; `/compact` POST → card appears in `waiting` from the ack, no provisional message row in timeline; mocked `rejected+terminal_instance` ack renders the `detail` guidance verbatim; double `/compact` → second attempt blocked client-side, and if forced through, BE `busy` rejection renders without a second card; input still sendable for normal messages mid-command (non-blocking requirement). |
| 6 | **Active-command card UI.** In `chat-interface.html` near the `.pending-injection-card` (:155-160): add `.active-command-card` driven by `CommandStateService.stateFor(currentInstanceId)` — `role="status"` `aria-live="polite"` (accessibility parity with the injection card). Content per phase: `waiting` → "Preparing compaction… (waiting for instance to quiesce)" (normal on RUNNING pause path, may hold ≤30s — copy must not imply failure); `in_progress` → spinner + **elapsed timer sourced from server `elapsed_ms`** (mm:ss; interpolate locally between events if desired, but **resync on every event including 10s heartbeats** — survives SSE reconnect/reload, architect §9-10) + advisory `eta_ms` rendered as "~Xs remaining" **only while in_progress and only when present** (hide when absent); hint text after 60s: "Large contexts can take several minutes"; queued-messages note (§6): "Messages sent now will run after compaction finishes." Terminal outcomes (post-review adjudication §7 amendment, 2026-08-31 — per-value FE copy table in WS-5 is the source of truth): `success` + `compacted_type:"summary"` → "Context compacted" + tokens before→after; `compacted_type:"partial_summary"` (via `timed_out → fallback_applied`, `failure_kind:"timeout"`, `detail.reason` may say `budget_exhausted`) → "Compaction timed out partway — kept the summarized sections, trimmed the un-summarized older section" + tokens; `compacted_type:"truncation"` (via `timed_out → fallback_applied`, `failure_kind:"timeout"`) → honest copy "Compaction timed out — history was trimmed without a summary" + tokens; **`compacted_type:"noop"` → instant success look with explanatory line from `noop_reason`** ("Already compacted recently" / "Context too small to compact" / "Too few messages") — **NOT a failure** (architect §9-3); `failed` → failure copy + reason. Heartbeat events refresh `elapsed_ms`/`eta_ms` display only — never reset the timer, never duplicate or flash the card. Card is non-modal, non-blocking (input stays enabled — see Open Questions Q2 recommendation). SCSS extends the existing card styles (`chat-interface.scss`). Signal fields consumed by the template are `readonly` (strictTemplates requirement — `private` breaks template access). (architect 2026-08-31) | 4 | `ng build` (strictTemplates) green; card announces state changes to screen readers (aria-live verified in e2e); elapsed timer renders from `elapsed_ms` and resyncs when a heartbeat with a higher `elapsed_ms` arrives (unit-testable); `eta_ms` shows "~" only in_progress and hides when absent; heartbeat does not reset/duplicate the card; `noop` renders instant-success with `noop_reason` line; truncation outcome shows honest "Compaction timed out — history was trimmed without a summary" copy; success shows token delta when `detail` present; card auto-dismisses after terminal display window. |
| 7 | **Post-compaction history refresh.** On **terminal event** (`success` / `fallback_applied` / `failed` — the terminal SSE event is the refetch trigger, architect §7 FE notes): fire the existing message-list load path so the timeline reflects compacted context, and let the `context_usage` SSE signal (`sse.service.ts:435`) refresh the token meter (it should re-emit post-compaction). For `compacted_type:"noop"` the context is unchanged — refetch is a skippable optimization (keep it as a cheap safety net). Guard: only for the current instance (staleness pattern, :633). | 4 | After mocked terminal success, message list refetch fires exactly once for the right instance, triggered by the terminal event; `noop` path stays correct (refetch harmless); switching instances mid-refetch applies nothing stale (unit test). |
| 8 | **Reconnect + load-time recovery (REST fallback + polling).** Wire `CommandStateService.reconcileFromServer(getActiveCommand(instanceId))` at: (a) chat load / instance connect, and (b) SSE reconnect — trigger from the `connected` event (:263) or reconnect detection, modeled on `fetchPendingInjection` being "the sanctioned pattern for state that must survive reconnect" (research). **Poll `GET /commands/active` at ~5s while the card is active AND SSE is dead; stop polling on terminal phase or `{exists:false}`** (architect §9-13). **`{exists:false}` ⇒ clear the card SILENTLY (no error toast)** — daemon restarted mid-command and the registry is ephemeral by design (D-B8); not an error (architect §9-11). Respect `ttl_seconds` (default 600) as the server memory window — an entry beyond TTL may legitimately report `exists:false`. SSE is **live-only, no-replay** — this REST path is the only recovery for mid-command drops. (architect 2026-08-31) | 2, 3, 4 | Unit: reconnect with server `in_progress` → card restored; reconnect with server terminal → terminal card + refresh (Task 7); `{exists:false}` → **card cleared silently, no toast, no error UI**; poll starts only when card active AND SSE dead, stops on terminal/`exists:false` (timer leak check); network error during poll neither clears nor duplicates the card. |
| 9 | **Test suites.** (a) Jest logic-mirror specs for `CommandStateService` + `command-registry.service` (acceptance per tasks 1/4; plain TS classes, manual Observable mocking — house style per research), plus the **`parseCommandAck` adapter test encoding the pinned §7 schema exactly — the executable contract spec** for the FE/BE wire contract (architect 2026-08-31). (b) Playwright e2e `frontend/e2e/slash-command-compact.spec.ts` modeled after `send-pause-button.spec.ts` (serial `test.describe.serial`, `createTestInstance`/`cleanup` fixtures, `domcontentloaded` + `waitForSelector` — **never `networkidle`**, NotificationService keeps SSE open forever). **O17 implementation note, 2026-08-31:** add a Playwright assertion that the SSE transport emits keepalives on idle (proxy idle-timeout risk, architect §7 FE notes) — capture the underlying transport (e.g. by listening to the EventSource readyState cycle or asserting the stream stays connected across a longer-than-heartbeat window). Timeout→fallback strategy (see §Test Strategy). New architect-driven scenarios: `rejected+terminal_instance` renders `detail` verbatim; `noop` renders instant-success with `noop_reason` line; `{exists:false}` clears the card silently; heartbeat does not duplicate/reset the card. (c) `ng build` strictTemplates as a CI gate (research gotcha: `tsc --noEmit` does NOT check templates). (d) **FE verification item 🟢:** confirm the SSE transport emits keepalives on idle (proxy idle-timeout risk, architect §7 FE notes) — a live dev-env check, not an automated test. | 1–8 | All Jest green incl. the §7 executable contract spec; e2e green including the fallback, rejection, noop, silent-clear, **and keepalive-on-idle** scenarios; `ng build` zero strictTemplates errors; keepalive verification item ticketed. |
| 10 | **STRETCH (explicitly separable — ship does not depend on it): slash autocomplete.** When input matches `/` (not `//`) with no space yet, show a dropdown anchored to the textarea (`@ViewChild('textarea')`, `message-input.component.ts:31`) listing registry commands — UI modeled on the in-input queue-selector dropdown (:121-161) and/or reusing `components/searchable-select/` (search-as-you-type). Arrow keys navigate, Enter/Tab completes, Esc dismisses; Enter with a complete command still routes through Task 5 validation. Build on `keydown` branch in/near `onKeydownEnter` (:201-216) **without altering** Shift+Enter-newline or paused→`handleResume()` behavior; `//` input never triggers the palette. | 1, 5 | e2e: typing `/` opens palette; `/co`+Enter completes to `/compact` and submits; `//` never opens the palette; Esc closes; existing message-input specs unaffected. |

---

## Coupling

- **Tight with Phase 1 (backend):** the §7 wire schema (CommandAck / CommandProgressEvent / GetActiveResponse), the 400-vs-200 split rule, `phase_seq` semantics, the 10s heartbeat, and `ttl_seconds` are shared contracts — **now PINNED**. FE funneling through `parseCommandAck` (one adapter) + typed DTOs remains, so any residual Phase 1 implementation drift is a one-function fix.
- **Tight internal coupling:** Task 5 (send path) ↔ Task 4 (state machine) share the `ActiveCommandState` type from Task 1; Task 6 (card) reads only `CommandStateService` — the card is a pure view over machine state, so Tasks 6 and 5 can proceed in parallel once 1–4 land.
- **Loose with existing message pipeline:** the FE deliberately keeps commands OUT of `message-merge.util.ts` (no `makeProvisionalMessage`, no `mergeMessagesById`) — coupling is limited to reusing its *patterns* (staleness eviction, dedup discipline), not its code paths.
- **Independent of:** queue-selector UI, instance list, project tabs, sources — untouched.

---

## Open Questions (post-architect — only genuinely FE-open items remain)

- **Q1 — Settled (FE item 1/2): BE-side intercept (RATIFIED).** Architect §8 Q1: "Adopt baseline — BE-side router intercept" (5-axis matrix, maintainability dominant). FE posts command text to the existing POST endpoint; the Task 2 `parseCommandAck` adapter and Task 5 ack branch consume the pinned envelope. The previously-kept FE-intercept alternative is retired from this plan.
- **Q4 — Settled (FE item 2/2): availability matrix (RATIFIED).** Terminal instances → **reject** `reason=terminal_instance` + `detail` guidance (architect §5/O-B4; FE renders it verbatim — Task 5). Scope: **global now**, with an `availability` predicate hook in CommandSpec for later per-agent policy (architect O-B6) — FE's `CommandDefinition.availability` field is the hook's landing spot. PAUSED-with-frozen-task and IDLE re-check corrections are Phase 1 internal (§6); FE's `waiting` copy already covers the only user-visible consequence.
- **Q2 — OPEN (FE item 1/2): does an active command block the input?** **Recommendation unchanged: NO hard block.** Server work legitimately runs to 300s; blocking send for 5 minutes is hostile and unnecessary — §6 confirms queued messages during compaction are safe (serialized on the gate) with bounded, user-visible latency that Task 6 copy documents. Duplicate-command guard (Task 5) + BE `busy`/`rate_limited` refusals are the real protection. The architect register does not rule on FE input blocking — decision still open; if soft-blocking *slash input only* is preferred, that is a Task 5 branch.
- **Q3 — OPEN (FE item 2/2): in-timeline provisional bubble vs out-of-timeline card.** **Recommendation unchanged: out-of-timeline card** (`.pending-injection-card` pattern). Nothing in the architect rulings contradicts it (§7 FE notes speak of "the card", reinforcing the card direction), but no explicit verdict exists — flagged for architect confirmation. Tasks 6 is written against the card; a switch to a bubble would change Task 6 + Task 5's no-provisional bypass only.
- **V-1 — FE verification item 🟢:** SSE transport keepalives on idle (proxy idle-timeout, architect §7 FE notes) — see Task 9(d) and the O17 keepalive-on-idle Playwright assertion. Not a decision; a verification ticket before ship.

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| R1 | **SSE drop mid-command** → card stuck on `waiting`/`in_progress` forever (SSE is live-only, no replay). | High | Medium | Task 8: reconnect-triggered `reconcileFromServer` + **~5s polling while card active AND SSE dead** (architect §9-13), stopping on terminal/`{exists:false}`; silent clear on restart-dropped registry (§9-11); age-based stale eviction so a dead command never sticks permanently; server `elapsed_ms` keeps the timer honest across reconnects. |
| R2 | **Instance switch mid-command TOCTOU** — a late SSE event or POST ack from instance A applied to instance B's UI. | High | Medium | Three layers: `sentInstanceId`-style guard extended to command acks (`chat.component.ts:1256-1261` pattern); per-instance state map (Task 4); staleness guard on SSE application (`sse.service.ts:633` pattern). Unit + e2e tests for switch-mid-command. |
| R3 | **5-minute long-running state** reads as a frozen UI → user retries (duplicate compaction) or closes the tab. | Medium | High | Elapsed timer **from server `elapsed_ms`** + progressive hint after 60s (Task 6); client-side duplicate-command guard as UX pre-check + BE `busy`/`rate_limited` authoritative refusals (§6 — and a duplicate that does POST converges to a cheap `noop`, so worst case is benign); non-blocking input (Q2 recommendation); state survives reload via load-time GET reconcile (Task 8). |
| R4 | **Dedup/echo collision** — command text leaking into the message merge pipeline (e.g. provisional row + later real echo of the same content). | High | Low (by design) | Task 5: command acks bypass `makeProvisionalMessage`/`mergeMessagesById` entirely; card rendering (Q3) keeps commands out of the timeline; `command_id` (UUIDv4) namespace distinct from `message_id`/`echo_id`. E2e asserts zero provisional message rows for a command. |
| R5 | **Event silence misread as failure** — during the RUNNING pause/quiesce window (ack→first-SSE can be ≤30s) or any transient SSE stall. | Medium | Medium | Card shows `waiting` **from the ACK** (Task 4/6 — the ≤30s gap is documented-normal, architect §7 FE notes); 10s heartbeat (phase_seq+1) proves liveness during in_progress and feeds `elapsed_ms`; ~5s REST poll covers SSE death; FE never treats silence as failure — the server owns timing. |
| R6 | **Wire-contract drift during Phase 1 implementation** despite the pinned schema (e.g. a field renamed in the BE patch). | Medium | Low (was High pre-pinning) | Contract is PINNED (architect §7) and enforced by the **executable contract spec**: the Jest `parseCommandAck` adapter test encodes the §7 TS blocks verbatim — a BE drift fails FE CI with a named field, and the fix is isolated to the one adapter function. |
| R7 | **Stale FE registry vs backend reality** — BE adds/renames a command, or FE pre-checks disagree with BE verdicts. | Medium | Low | BE is authoritative by design (§7 split rule): unknown-to-FE input that POSTs anyway gets 400 `UNKNOWN_COMMAND` + `detail.available`, rendered through the existing toast path (Task 5) — never a dead end; `available` list offers a registry-refresh seam; FE client-side rejection is advisory-only and `//`-escape input is never command-interpreted (O-B1). |
| R8 | **strictTemplates regression** — signal fields declared `private` break template binding at build, late. | Low | Medium | `ng build` as CI gate (Task 9c — `tsc --noEmit` does not check templates, research gotcha); `readonly` convention stated in Task 6. |
| R9 | **Poll/heartbeat interaction bugs** — the ~5s poll and the 10s SSE heartbeat both update the same card state; a bug could double-apply, regress `phase_seq`, or leak the poll timer. | Medium | Low | Single reducer (Task 4) owns all state mutations; `phase_seq` monotonic guard makes both sources idempotent-ish and order-safe; poll stops on terminal/`exists:false` (Task 8 acceptance includes an explicit timer-leak check). (architect 2026-08-31) |

---

## Test Strategy

**Unit (Jest 30 + jest-preset-angular, logic-mirror house style — plain TS classes, NO TestBed, manual Observable mocking):**
- `command-state.service.spec.ts` — every transition in the matrix (`waiting→in_progress→{success(summary|truncation|noop)|timed_out→fallback_applied|failed}`), illegal transitions as no-ops, `command_id` mismatch ignored, **`phase_seq` monotonic guard (stale/duplicate/heartbeat)**, ack-seed `waiting`-before-SSE, cross-instance isolation, reconnect reconcile (server wins), silent `{exists:false}` handling, age-based eviction, POST-ack seeding.
- `command-registry.service.spec.ts` — parse outcomes (`known`/`unknown`/`not-a-command`/`escape`), registry extension is one entry, availability gating.
- **Adapter tests (`parseCommandAck`) encode the pinned §7 schema exactly — the executable contract spec**: all four existing message statuses + the full CommandAck shape (`state`, every `RejectionReason`, `detail`, `ttl_seconds`), 400 `UNKNOWN_COMMAND` mapping, network-error swallowing.

**E2E (Playwright v1.60, `frontend/e2e/slash-command-compact.spec.ts`):**
- Conventions per `send-pause-button.spec.ts`: `test.describe.serial`, `createTestInstance` (from `e2e/fixtures/test-helpers.ts`) + `cleanup` fixtures, `domcontentloaded` + `waitForSelector` — **never `waitUntil:'networkidle'`** (NotificationService holds an SSE open forever).
- **Timeout→fallback mock strategy (proposed):** Playwright `page.route` intercepts (a) `POST /api/instances/*/messages` → fulfill a §7 `command-ack` body (`{status:'command', command:'compact', command_id:<uuid>, state:'accepted', timestamp, ttl_seconds:600}`), (b) the EventSource `/api/instances/*/events` connection → **deliberately aborted/hung** (exercises the reconnect path), (c) `GET /api/instances/*/commands/active` → `route.fulfill` with delayed bodies stepping `in_progress` (with `phase_seq`/`elapsed_ms`) → `fallback_applied`. Rationale: EventSource streams cannot be push-driven from `route.fulfill` (body flush closes the stream), but the REST fallback IS fully mockable — so the e2e intentionally exercises the SSE-dead recovery + polling path, doubling as coverage for R1/R5. SSE-driven transitions (incl. heartbeat and `phase_seq` guard) are covered deterministically at the unit level (Task 4). **Flag:** confirm route-interception behavior against EventSource in a spike during Task 9 (half-day budget); fallback alternative is a dev-only SSE replay hook — requires architect sign-off (test code in prod bundle).
- Scenarios: SC1–SC15 below, including the architect-driven additions — `rejected+terminal_instance` guidance verbatim, `noop` instant-success, silent `{exists:false}` clear, heartbeat non-duplication.

**Build gate:** `ng build` with strictTemplates in CI (Task 9c). Known trap (research): `tsc --noEmit` does not type-check Angular templates; signal fields bound in templates must be `readonly`, not `private`.

---

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| SC1 | `/compact` full happy path | Playwright e2e: POST → ack (`accepted`) → card `waiting`→`in_progress`→`success` (stubbed server, see Test Strategy) | Card reaches terminal state; assertion on card text + phase transitions; card shows `waiting` from the ack, before first SSE event |
| SC2 | Timeout→fallback UX | e2e drives REST fallback to `fallback_applied` (SSE stubbed dead — dual coverage of R1). Asserts on both `compacted_type` branches (post-review adjudication §7 amendment, 2026-08-31): `partial_summary` shows "Compaction timed out partway — kept the summarized sections, trimmed the un-summarized older section" + token detail; `truncation` shows "Compaction timed out — history was trimmed without a summary" + token detail; no error banner; no crash |
| SC3 | Unknown command rejected client-side | e2e: type `/foo`, Enter, count network requests | Inline error visible <500 ms; **zero** POST requests fired |
| SC4 | Non-blocking during command | e2e + unit: send a normal message while command active | Message POSTs normally; input never disabled |
| SC5 | Duplicate-command guard | e2e: `/compact` twice | Second attempt shows inline "already in progress"; zero second POST; forced duplicate gets BE `busy` refusal rendered without a second card |
| SC6 | No message-pipeline pollution | e2e DOM assertion | Zero provisional message rows / duplicate timeline entries created for the command |
| SC7 | Reconnect recovery | e2e: abort SSE (route abort) mid-command → GET fallback returns `in_progress` | Card state restored within one reconnect cycle (state incl. server `elapsed_ms`); no stuck spinner |
| SC8 | Timeline reflects compacted state | Unit + e2e: terminal event → refetch fires | Exactly one refetch, correct instance, triggered by the terminal event, stale-switch safe |
| SC9 | Accessibility parity | e2e attribute assertions | Card has `role="status"` + `aria-live="polite"` |
| SC10 | Instance-switch safety | Unit (machine) + e2e | Event for non-current instance never renders; returning to the instance shows its own command state |
| SC11 | Type/template safety | `ng build` (strictTemplates) in CI | Zero errors — gate must pass |
| SC12 | State machine + contract correctness | Jest logic-mirror suite incl. §7 executable contract spec | 100% of legal transitions pass; illegal transitions no-op; wrong-`command_id` ignored; `phase_seq` guard proven; adapter test asserts every pinned §7 field |
| SC13 | `noop` renders as success, not failure | Unit + e2e: `success` + `compacted_type:"noop"` + `noop_reason` | Instant-success card with explanatory `noop_reason` line; no error styling; no failure copy (architect §9-3) |
| SC14 | Rejection reasons render correctly | e2e: mocked 200 ack `state:"rejected"` per reason; mocked 400 `UNKNOWN_COMMAND` | `terminal_instance` → `detail` guidance verbatim ("Send a message to start a new turn, then /compact."); other reasons → short human copy + reason; 400 → existing toast path with available-commands detail |
| SC15 | Restart + polling semantics | e2e: `GET /commands/active` → `{exists:false}` while card active | Card cleared **silently** (no toast/error); poll cadence ~5s observed while SSE dead; poll stops on terminal/`exists:false` with no timer leak |

---

## Exit Criterion

All of: (1) Tasks 1–9 complete with their acceptance checks; (2) Jest machine/registry/adapter suites green — adapter suite doubling as the pinned §7 executable contract spec; (3) Playwright `slash-command-compact.spec.ts` green including SC2 (fallback), SC7 (reconnect), SC14 (rejections), SC15 (restart/poll); (4) `ng build` strictTemplates zero-error; (5) the remaining FE-open items (Q2 input blocking, Q3 rendering confirmation) answered by the architect and any resulting deltas applied or explicitly deferred; (6) the pinned §7 contract implemented against and holding (no drift — R6 spec green); (7) verification item V-1 (SSE keepalives on idle 🟢) checked in the dev env. At that point the FE subsystem is ready for end-to-end integration testing with the real Phase 1 backend.
