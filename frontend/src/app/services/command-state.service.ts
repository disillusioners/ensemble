import { Injectable, signal } from '@angular/core';
import type {
  CommandAck,
  CommandPhase,
  CommandProgressDetail,
  CommandProgressEvent,
  GetActiveResponse,
} from '../models';
import { isAcceptedCommandAck } from './api.service';

/**
 * CommandStateService — the /compact slash-command state machine
 * (phase2-plan.md Task 4, architect §7 + post-review adjudication).
 *
 * Plain-TS house style: dependency-free constructor, directly instantiable
 * in Jest specs without TestBed (``new CommandStateService()``), same
 * pattern as ``InstancesViewStateService``. All I/O arrives through the
 * {@link wireFetch} seam so the machine itself has zero Angular-HTTP
 * coupling.
 *
 * State machine (C1 amendment does NOT change the phase machine — partial
 * is a detail-level distinction):
 *
 *   idle ──ack(accepted)──► waiting ──► in_progress ──► success (terminal)
 *                             │              │  └──► timed_out ──► fallback_applied (terminal)
 *                             │              └──► failed (terminal)
 *                             └─► success / timed_out / failed
 *
 * - ``success`` carries ``compacted_type`` (summary | partial_summary |
 *   truncation | noop) + optional ``noop_reason``; noop is NOT a failure.
 * - ``timed_out`` is NON-terminal (the fallback is still being applied).
 * - Terminal = success | fallback_applied | failed.
 * - No client-side cancellation: the server owns the 90–300s budget.
 *
 * Ordering discipline (R9): every mutation funnels through the private
 * reducers; the ``phase_seq`` monotonic guard (ignore ``phase_seq <=``
 * last seen for the same command_id) makes SSE heartbeats, replays and
 * out-of-order delivery idempotent, and the ~5s REST poll converges to
 * server truth via {@link reconcileFromServer} (server wins).
 */

/** Per-instance snapshot the card renders. Kept in a per-instance map so
 *  instance switches never lose progress (returning to the instance still
 *  shows it) and cross-instance events can never cross-apply (R2). */
export interface ActiveCommandState {
  instanceId: string;
  commandId: string;
  /** Canonical command name ('compact' today). */
  command: string;
  phase: CommandPhase;
  /** Last-seen server sequence number for THIS command. Seeds at -1 from
   *  the ack (the ack carries no phase_seq) so the first SSE ``waiting``
   *  event (seq 0) is accepted. */
  phaseSeq: number;
  /** Server-clock elapsed — the timer source of truth (§9-10). */
  elapsedMs: number;
  /** Advisory; in_progress only. Hidden when null. */
  etaMs: number | null;
  detail: CommandProgressDetail | null;
  /** Local wall-clock at ack-seed / first creation (eviction + age basis). */
  startedAtMs: number;
  /** Local wall-clock when the phase first went terminal (display-window
   *  eviction basis). Null while non-terminal. */
  terminalAtMs: number | null;
  /** True once the terminal-event refetch trigger has been raised for
   *  this command — guarantees the chat refetch fires exactly once. */
  refetchTriggered: boolean;
}

export type FetchActiveCommand = (instanceId: string) => Promise<GetActiveResponse | null>;

function isTerminalPhase(phase: CommandPhase): boolean {
  return phase === 'success' || phase === 'fallback_applied' || phase === 'failed';
}

/** Legal SSE phase ADVANCES. Anything not listed (and not same-phase) is
 *  an illegal transition → no-op, no throw. */
const LEGAL_TRANSITIONS: Record<string, CommandPhase[]> = {
  waiting: ['in_progress', 'success', 'timed_out', 'failed'],
  in_progress: ['success', 'timed_out', 'failed'],
  timed_out: ['fallback_applied', 'failed'],
};

/** How long a terminal card stays visible before age-based eviction
 *  (pattern precedent: evictPendingByAge, message-merge.util.ts). */
export const TERMINAL_DISPLAY_MS = 8000;
/** REST fallback poll cadence while the card is active AND SSE is dead. */
export const POLL_INTERVAL_MS = 5000;

@Injectable({
  providedIn: 'root',
})
export class CommandStateService {
  /** Per-instance command state — retained across instance switches by
   *  design (FE re-syncs via GET on re-mount; O10 keys the server registry
   *  by instance_id for the same reason). */
  private readonly stateByInstance = signal<Map<string, ActiveCommandState>>(new Map());

  /** Terminal-refetch trigger (phase2 Task 7). Bumped EXACTLY once per
   *  command when its terminal phase first lands; the chat component
   *  listens and runs a merge-mode message refetch for
   *  {@link refetchInstanceId}. Same counter-trigger pattern as
   *  ``sseService.refetchRequest``. */
  readonly refetchRequest = signal<number>(0);
  /** Instance the latest {@link refetchRequest} bump refers to. */
  readonly refetchInstanceId = signal<string | null>(null);

  /** REST fallback fetch seam — wired once by the chat component
   *  (``commandState.wireFetch(id => api.getActiveCommand(id))``). Null =
   *  not wired (polling disabled; unit tests drive reconcile directly). */
  private fetchActive: FetchActiveCommand | null = null;

  // ── poll-loop bookkeeping (Task 8) ────────────────────────────────────
  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private pollInstanceId: string | null = null;
  private lastSyncedInstanceId: string | null = null;
  private lastSyncedSseAlive = true;

  /** Per-instance reconcile sequence — a stale GET response for an
   *  instance that was re-reconciled (or switched away) must not win. */
  private readonly reconcileSeq = new Map<string, number>();

  /** Terminal display-window eviction timers, keyed by instance. */
  private readonly evictionTimers = new Map<string, ReturnType<typeof setTimeout>>();

  // ── wiring ────────────────────────────────────────────────────────────

  /** Wire the GET fallback fetch (called once from the chat component). */
  wireFetch(fetchActive: FetchActiveCommand): void {
    this.fetchActive = fetchActive;
  }

  // ── reads ─────────────────────────────────────────────────────────────

  /** Reactive per-instance map. Consumers should prefer {@link stateFor}. */
  readonly activeByInstance = this.stateByInstance.asReadonly();

  /** The command state for ``instanceId``, or null when none is tracked.
   *  Reads the signal → template/derived consumers stay reactive. */
  stateFor(instanceId: string | null | undefined): ActiveCommandState | null {
    if (!instanceId) return null;
    return this.stateByInstance().get(instanceId) ?? null;
  }

  /** True when a NON-terminal command is tracked for ``instanceId`` —
   *  the advisory duplicate-command pre-check (Task 5) and the poll
   *  continue-condition (Task 8). */
  isActive(instanceId: string | null | undefined): boolean {
    const state = this.stateFor(instanceId);
    return state !== null && !isTerminalPhase(state.phase);
  }

  // ── mutations ─────────────────────────────────────────────────────────

  /**
   * Ack-seed (Task 4/5): a 200 ``state:"accepted"`` ack puts the card in
   * ``waiting`` IMMEDIATELY, before any SSE event — the ack→first-SSE gap
   * is normal and can be ≤30s on the RUNNING pause path (R5). Rejected
   * acks never enter the machine (the chat component renders them inline).
   */
  startCommand(instanceId: string, ack: CommandAck): ActiveCommandState | null {
    if (!isAcceptedCommandAck(ack)) return null;
    const next: ActiveCommandState = {
      instanceId,
      commandId: ack.command_id,
      command: ack.command,
      phase: 'waiting',
      phaseSeq: -1,
      elapsedMs: 0,
      etaMs: null,
      detail: null,
      startedAtMs: Date.now(),
      terminalAtMs: null,
      refetchTriggered: false,
    };
    this.install(instanceId, next);
    return next;
  }

  /**
   * Apply an SSE ``command_progress`` event (already parsed + forwarded
   * with ``phase_seq`` intact by SseService). Reducer rules:
   *
   * 1. No entry for the instance → adopt the event (self-healing: covers
   *    a reload that missed the ack).
   * 2. Entry exists with a DIFFERENT command_id → ignore (stale command's
   *    events must never clobber the newer command).
   * 3. ``phase_seq <=`` last seen → ignore (dedups 10s heartbeats by seq
   *    is WRONG — heartbeats carry phase_seq+1 — this guard drops true
   *    duplicates and out-of-order/regressed events).
   * 4. Same phase, higher seq, non-terminal → heartbeat refresh: update
   *    elapsed_ms / eta_ms / timestamp only; never advance, never re-flash.
   * 5. Phase change → apply only if legal; illegal transitions are
   *    no-ops (no throw, no state change).
   */
  onSseEvent(event: CommandProgressEvent): void {
    if (!event || !event.instance_id || !event.command_id) return;
    const current = this.stateFor(event.instance_id);

    // Rule 1 — adopt unknown commands.
    if (!current) {
      this.install(event.instance_id, this.fromEvent(event));
      return;
    }
    // Rule 2 — wrong command.
    if (current.commandId !== event.command_id) return;

    // Rule 3 — monotonic seq guard (per command_id).
    if (Number.isFinite(event.phase_seq) && event.phase_seq <= current.phaseSeq) return;

    if (event.phase === current.phase) {
      // Rule 4 — heartbeat refresh (non-terminal only; a repeated terminal
      // event is a replay the seq guard already caught above).
      if (isTerminalPhase(current.phase)) return;
      this.patch(event.instance_id, {
        phaseSeq: event.phase_seq,
        elapsedMs: event.elapsed_ms,
        etaMs: event.phase === 'in_progress' ? (event.eta_ms ?? null) : null,
        detail: event.detail ?? current.detail,
      });
      return;
    }

    // Rule 5 — legal advance or illegal no-op.
    const allowed = LEGAL_TRANSITIONS[current.phase] ?? [];
    if (!allowed.includes(event.phase)) return;

    const terminal = isTerminalPhase(event.phase);
    this.patch(event.instance_id, {
      phase: event.phase,
      phaseSeq: event.phase_seq,
      elapsedMs: event.elapsed_ms,
      etaMs: event.phase === 'in_progress' ? (event.eta_ms ?? null) : null,
      detail: event.detail ?? current.detail,
      terminalAtMs: terminal ? Date.now() : null,
    });
    if (terminal) this.finalize(event.instance_id, current.commandId);
  }

  /**
   * GET-fallback reconcile (Task 8): the SERVER wins on reconnect/load.
   * Applies {@link reconcileFromServerResult}; see that method for the
   * ``{exists:false}`` / null semantics. A per-instance sequence guard
   * drops stale responses, and the issue-time guard drops responses that
   * raced a NEWER command start (see that method).
   *
   * Returns a promise for testability; callers may fire-and-forget.
   */
  async reconcileFromServer(instanceId: string): Promise<void> {
    if (!instanceId || !this.fetchActive) return;
    const seq = (this.reconcileSeq.get(instanceId) ?? 0) + 1;
    this.reconcileSeq.set(instanceId, seq);
    // Issue-time stamp — lets the result handler detect a GET that was
    // in flight when a NEW command started (its {exists:false} or its
    // previous-command payload predates the command and must not win).
    const issuedAtMs = Date.now();
    let result: GetActiveResponse | null;
    try {
      result = await this.fetchActive(instanceId);
    } catch {
      result = null; // never throw — fetch seam is expected to swallow
    }
    // Staleness guard: a newer reconcile for this instance superseded us.
    if (this.reconcileSeq.get(instanceId) !== seq) return;
    this.reconcileFromServerResult(instanceId, result, issuedAtMs);
  }

  /**
   * Apply an already-fetched GET result (exposed separately so unit tests
   * can drive reconcile without the fetch seam):
   *  - ``null`` (network error) → NO-OP: neither clear nor duplicate the
   *    card (poll keeps running; Task 8 acceptance).
   *  - ``{exists:false}`` → clear the card SILENTLY (no toast, no error
   *    UI) — daemon restart lost the ephemeral registry by design (D-B8).
   *  - ``{exists:true, command}`` → server wins: overwrite local state
   *    unconditionally (its phase_seq is the newest server truth) and
   *    finalize if terminal.
   *
   * STALENESS RULE (e2e-found race, R2 family): a result issued BEFORE
   * the currently-tracked command started is a view of a pre-command
   * world. It must never clear that command (its ``{exists:false}`` is
   * stale — the command exists NOW) nor clobber it with a PREVIOUS
   * command's payload. Such results are dropped; the next poll / SSE
   * event re-syncs with fresh truth. The rule only bites when a tracked
   * command exists AND (``!exists`` OR a different ``command_id``) — a
   * matching ``command_id`` result always applies (server wins).
   */
  reconcileFromServerResult(
    instanceId: string,
    result: GetActiveResponse | null,
    issuedAtMs: number = Date.now(),
  ): void {
    if (result === null) return;

    const current = this.stateFor(instanceId);

    if (
      current !== null &&
      issuedAtMs < current.startedAtMs &&
      (!result.exists || result.command.command_id !== current.commandId)
    ) {
      // Stale reconcile racing a newer command — drop it. The strict `<`
      // keeps the normal poll path (issued strictly after the seed) fully
      // authoritative; the race window this guards (load-time GET issued
      // hundreds of ms before the user's POST) is always strictly earlier
      // in practice, and dropping is always safe because the next poll
      // re-syncs with fresh server truth.
      return;
    }

    if (!result.exists) {
      // Silent clear — only when we actually had something to clear.
      if (current !== null) {
        this.clear(instanceId);
      }
      this.refreshPolling();
      return;
    }

    const event = result.command;
    if (!event || !event.command_id) return;
    if (current && current.commandId === event.command_id && current.phase === event.phase) {
      // Same command, same phase — heartbeat-style refresh only.
      this.patch(instanceId, {
        phaseSeq: event.phase_seq,
        elapsedMs: event.elapsed_ms,
        etaMs: event.phase === 'in_progress' ? (event.eta_ms ?? null) : null,
        detail: event.detail ?? current.detail,
      });
    } else {
      // Server wins — adopt wholesale (covers: restore after reload,
      // advance after missed events, terminal after FE stale in_progress).
      const terminal = isTerminalPhase(event.phase);
      const wasCurrentCommand = current?.commandId === event.command_id;
      this.install(instanceId, {
        ...this.fromEvent(event),
        // Preserve the original ack-seed age so the display window and
        // "started" bookkeeping stay stable across reconciles.
        startedAtMs: wasCurrentCommand ? current!.startedAtMs : Date.now(),
        refetchTriggered: wasCurrentCommand ? current!.refetchTriggered : false,
        terminalAtMs: terminal ? Date.now() : null,
      });
      if (terminal) this.finalize(instanceId, event.command_id);
    }
    this.refreshPolling();
  }

  /** Remove the instance's command state entirely (silent). */
  clear(instanceId: string): void {
    this.cancelEviction(instanceId);
    this.stateByInstance.update(map => {
      if (!map.has(instanceId)) return map;
      const next = new Map(map);
      next.delete(instanceId);
      return next;
    });
    this.refreshPolling();
  }

  /**
   * Connectivity + focus input for the poll loop (Task 8). The chat
   * component calls this whenever the active instance or SSE liveness
   * changes; mutations re-evaluate internally. Polling runs only while
   * the CURRENT instance has a non-terminal command AND SSE is dead.
   */
  syncPolling(currentInstanceId: string | null, sseAlive: boolean): void {
    this.lastSyncedInstanceId = currentInstanceId;
    this.lastSyncedSseAlive = sseAlive;
    this.refreshPolling();
  }

  /** Tear down timers (chat ngOnDestroy). Per-instance command states are
   *  intentionally KEPT — they survive switches/re-mounts by design. */
  stopAllTimers(): void {
    this.stopPollTimer();
    for (const [instanceId, timer] of this.evictionTimers) {
      clearTimeout(timer);
      this.evictionTimers.delete(instanceId);
    }
  }

  // ── internals ─────────────────────────────────────────────────────────

  private fromEvent(event: CommandProgressEvent): ActiveCommandState {
    return {
      instanceId: event.instance_id,
      commandId: event.command_id,
      command: 'compact',
      phase: event.phase,
      phaseSeq: event.phase_seq,
      elapsedMs: event.elapsed_ms,
      etaMs: event.phase === 'in_progress' ? (event.eta_ms ?? null) : null,
      detail: event.detail ?? null,
      startedAtMs: Date.now(),
      terminalAtMs: isTerminalPhase(event.phase) ? Date.now() : null,
      refetchTriggered: false,
    };
  }

  private install(instanceId: string, state: ActiveCommandState): void {
    // A fresh install supersedes any pending eviction timer for the old
    // entry (ack-seed over a lingering terminal card must not race).
    this.cancelEviction(instanceId);
    this.stateByInstance.update(map => {
      const next = new Map(map);
      next.set(instanceId, state);
      return next;
    });
    if (isTerminalPhase(state.phase)) {
      this.scheduleEviction(instanceId, state.commandId);
    }
    this.refreshPolling();
  }

  private patch(
    instanceId: string,
    changes: Partial<ActiveCommandState>,
  ): void {
    this.stateByInstance.update(map => {
      const current = map.get(instanceId);
      if (!current) return map;
      const next = new Map(map);
      next.set(instanceId, { ...current, ...changes });
      return next;
    });
  }

  /** Terminal bookkeeping: raise the refetch trigger EXACTLY once per
   *  command, schedule the display-window eviction, re-evaluate polling. */
  private finalize(instanceId: string, commandId: string): void {
    const state = this.stateFor(instanceId);
    if (!state || state.commandId !== commandId) return;
    if (!state.refetchTriggered) {
      this.patch(instanceId, { refetchTriggered: true });
      this.refetchInstanceId.set(instanceId);
      this.refetchRequest.update(n => n + 1);
    }
    this.scheduleEviction(instanceId, commandId);
    this.refreshPolling();
  }

  private scheduleEviction(instanceId: string, commandId: string): void {
    this.cancelEviction(instanceId);
    const timer = setTimeout(() => {
      this.evictionTimers.delete(instanceId);
      const state = this.stateFor(instanceId);
      // Evict only if it is still the SAME terminal command — a newer
      // command (re-install) must never be dropped by an older timer.
      if (state && state.commandId === commandId && isTerminalPhase(state.phase)) {
        this.clear(instanceId);
      }
    }, TERMINAL_DISPLAY_MS);
    this.evictionTimers.set(instanceId, timer);
  }

  private cancelEviction(instanceId: string): void {
    const timer = this.evictionTimers.get(instanceId);
    if (timer !== undefined) {
      clearTimeout(timer);
      this.evictionTimers.delete(instanceId);
    }
  }

  // ── poll loop (Task 8) ────────────────────────────────────────────────

  private refreshPolling(): void {
    const shouldPoll =
      this.lastSyncedInstanceId !== null &&
      !this.lastSyncedSseAlive &&
      this.isActive(this.lastSyncedInstanceId);

    if (shouldPoll && this.pollTimer === null) {
      this.pollInstanceId = this.lastSyncedInstanceId;
      this.pollTimer = setInterval(() => this.pollOnce(), POLL_INTERVAL_MS);
    } else if (!shouldPoll && this.pollTimer !== null) {
      this.stopPollTimer();
    }
  }

  private stopPollTimer(): void {
    if (this.pollTimer !== null) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
    this.pollInstanceId = null;
  }

  private pollOnce(): void {
    const instanceId = this.pollInstanceId;
    if (!instanceId || !this.fetchActive) return;
    // reconcileFromServerResult handles all three outcomes:
    //   null            → keep card + keep polling (network blip)
    //   {exists:false}  → silent clear; refreshPolling() then stops the timer
    //   terminal        → finalize; refreshPolling() then stops the timer
    void this.reconcileFromServer(instanceId);
  }
}
