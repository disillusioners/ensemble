import {
  CommandStateService,
  TERMINAL_DISPLAY_MS,
  POLL_INTERVAL_MS,
} from './command-state.service';
import type { CommandAck, CommandProgressEvent, GetActiveResponse } from '../models';

/** Narrow the test responder to the fetch-seam signature. */
type Fetch = (id: string) => Promise<GetActiveResponse | null>;

/**
 * Logic-mirror specs for the /compact state machine (phase2-plan.md Task 4
 * acceptance, §Test Strategy). Plain TS, NO TestBed — each test instantiates
 * a fresh service via ``new CommandStateService()`` (house style, same
 * pattern as instances-view-state.service.spec.ts).
 *
 * Timer-dependent suites (terminal eviction, REST poll) use Jest fake
 * timers and assert explicit start/stop so the no-timer-leak requirement
 * (Task 8 acceptance) is verifiable, not assumed.
 */

function makeAck(overrides: Partial<CommandAck> = {}): CommandAck {
  return {
    status: 'command',
    command: 'compact',
    command_id: 'cmd-1',
    state: 'accepted',
    reason: null,
    detail: null,
    timestamp: '2026-08-31T00:00:00Z',
    ttl_seconds: 600,
    ...overrides,
  };
}

function makeEvent(overrides: Partial<CommandProgressEvent> = {}): CommandProgressEvent {
  return {
    instance_id: 'inst-1',
    command_id: 'cmd-1',
    phase: 'in_progress',
    phase_seq: 1,
    timestamp: '2026-08-31T00:00:05Z',
    elapsed_ms: 5000,
    ...overrides,
  };
}

/** Flush pending microtasks (fetch seam is async) without advancing the
 *  fake clock. */
async function flush(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

describe('CommandStateService — ack seed (Task 4/5)', () => {
  it('seeds the card in waiting IMMEDIATELY from an accepted ack, before any SSE', () => {
    const service = new CommandStateService();
    const state = service.startCommand('inst-1', makeAck());
    expect(state).not.toBeNull();
    expect(service.stateFor('inst-1')?.phase).toBe('waiting');
    expect(service.stateFor('inst-1')?.commandId).toBe('cmd-1');
    // No SSE event seen yet — seq guard seeds at -1 so the first
    // ``waiting`` event (seq 0) is accepted afterwards.
    expect(service.stateFor('inst-1')?.phaseSeq).toBe(-1);
    expect(service.stateFor('inst-1')?.elapsedMs).toBe(0);
    expect(service.isActive('inst-1')).toBe(true);
  });

  it('refuses rejected acks — the machine is never started by a rejection', () => {
    const service = new CommandStateService();
    const state = service.startCommand('inst-1', makeAck({ state: 'rejected', reason: 'busy', command_id: null }));
    expect(state).toBeNull();
    expect(service.stateFor('inst-1')).toBeNull();
    expect(service.isActive('inst-1')).toBe(false);
  });

  it('a NEW accepted ack supersedes the previous command on the same instance', () => {
    const service = new CommandStateService();
    service.startCommand('inst-1', makeAck({ command_id: 'cmd-1' }));
    service.startCommand('inst-1', makeAck({ command_id: 'cmd-2' }));
    expect(service.stateFor('inst-1')?.commandId).toBe('cmd-2');
    expect(service.stateFor('inst-1')?.phase).toBe('waiting');
  });
});

describe('CommandStateService — legal transitions', () => {
  it('waiting → in_progress (SSE advance)', () => {
    const service = new CommandStateService();
    service.startCommand('inst-1', makeAck());
    service.onSseEvent(makeEvent({ phase: 'waiting', phase_seq: 0 }));
    service.onSseEvent(makeEvent({ phase: 'in_progress', phase_seq: 1, elapsed_ms: 8000, eta_ms: 30000 }));
    expect(service.stateFor('inst-1')?.phase).toBe('in_progress');
    expect(service.stateFor('inst-1')?.elapsedMs).toBe(8000);
    expect(service.stateFor('inst-1')?.etaMs).toBe(30000);
  });

  it('in_progress → success carrying compacted_type summary (terminal)', () => {
    const service = new CommandStateService();
    service.startCommand('inst-1', makeAck());
    service.onSseEvent(makeEvent({ phase: 'waiting', phase_seq: 0 }));
    service.onSseEvent(makeEvent({ phase: 'in_progress', phase_seq: 1 }));
    service.onSseEvent(makeEvent({
      phase: 'success',
      phase_seq: 2,
      elapsed_ms: 42000,
      detail: { compacted_type: 'summary', tokens_before: 120000, tokens_after: 45000 },
    }));
    expect(service.stateFor('inst-1')?.phase).toBe('success');
    expect(service.stateFor('inst-1')?.detail?.compacted_type).toBe('summary');
    expect(service.isActive('inst-1')).toBe(false);
  });

  it('in_progress → timed_out → fallback_applied with truncation type (terminal)', () => {
    const service = new CommandStateService();
    service.startCommand('inst-1', makeAck());
    service.onSseEvent(makeEvent({ phase: 'waiting', phase_seq: 0 }));
    service.onSseEvent(makeEvent({ phase: 'in_progress', phase_seq: 1 }));
    service.onSseEvent(makeEvent({
      phase: 'timed_out', phase_seq: 2,
      detail: { failure_kind: 'timeout' },
    }));
    // timed_out is NON-terminal — the fallback is still being applied.
    expect(service.stateFor('inst-1')?.phase).toBe('timed_out');
    expect(service.isActive('inst-1')).toBe(true);
    service.onSseEvent(makeEvent({
      phase: 'fallback_applied', phase_seq: 3,
      detail: { compacted_type: 'truncation', tokens_before: 100000, tokens_after: 30000 },
    }));
    expect(service.stateFor('inst-1')?.phase).toBe('fallback_applied');
    expect(service.stateFor('inst-1')?.detail?.compacted_type).toBe('truncation');
    expect(service.isActive('inst-1')).toBe(false);
  });

  it('partial_summary rides the same timed_out → fallback_applied machine (C1 amendment)', () => {
    const service = new CommandStateService();
    service.startCommand('inst-1', makeAck());
    service.onSseEvent(makeEvent({ phase: 'waiting', phase_seq: 0 }));
    service.onSseEvent(makeEvent({ phase: 'in_progress', phase_seq: 1 }));
    service.onSseEvent(makeEvent({ phase: 'timed_out', phase_seq: 2 }));
    service.onSseEvent(makeEvent({
      phase: 'fallback_applied', phase_seq: 3,
      detail: { compacted_type: 'partial_summary', failure_kind: 'timeout', reason: 'budget_exhausted' },
    }));
    expect(service.stateFor('inst-1')?.detail?.compacted_type).toBe('partial_summary');
    expect(service.stateFor('inst-1')?.detail?.reason).toBe('budget_exhausted');
  });

  it('noop arrives as SUCCESS + noop_reason — never a failure phase', () => {
    const service = new CommandStateService();
    service.startCommand('inst-1', makeAck());
    service.onSseEvent(makeEvent({ phase: 'waiting', phase_seq: 0 }));
    service.onSseEvent(makeEvent({
      phase: 'success', phase_seq: 1,
      detail: { compacted_type: 'noop', noop_reason: 'recently_compacted' },
    }));
    expect(service.stateFor('inst-1')?.phase).toBe('success');
    expect(service.stateFor('inst-1')?.detail?.compacted_type).toBe('noop');
    expect(service.stateFor('inst-1')?.detail?.noop_reason).toBe('recently_compacted');
    // noop is a SUCCESS — never a failure phase (SC13).
    expect(service.stateFor('inst-1')?.phase === 'failed').toBe(false);
  });

  it('waiting → success directly (missed in_progress tolerance / fast noop)', () => {
    const service = new CommandStateService();
    service.startCommand('inst-1', makeAck());
    service.onSseEvent(makeEvent({ phase: 'waiting', phase_seq: 0 }));
    service.onSseEvent(makeEvent({ phase: 'success', phase_seq: 1 }));
    expect(service.stateFor('inst-1')?.phase).toBe('success');
  });

  it('instance deleted mid-command → terminal failed renders like any failure', () => {
    const service = new CommandStateService();
    service.startCommand('inst-1', makeAck());
    service.onSseEvent(makeEvent({ phase: 'waiting', phase_seq: 0 }));
    service.onSseEvent(makeEvent({
      phase: 'failed', phase_seq: 1,
      detail: { failure_kind: 'error', reason: 'instance_not_found' },
    }));
    expect(service.stateFor('inst-1')?.phase).toBe('failed');
    expect(service.stateFor('inst-1')?.detail?.reason).toBe('instance_not_found');
  });
});

describe('CommandStateService — illegal transitions are no-ops (no throw)', () => {
  it.each([
    // [seed phases to reach, then the illegal event]
    { seed: ['waiting', 'in_progress'], illegal: { phase: 'waiting', phase_seq: 9 } },
    { seed: ['waiting', 'in_progress'], illegal: { phase: 'fallback_applied', phase_seq: 9 } },
    { seed: ['waiting'], illegal: { phase: 'fallback_applied', phase_seq: 9 } },
    { seed: ['waiting', 'in_progress', 'timed_out'], illegal: { phase: 'success', phase_seq: 9 } },
    { seed: ['waiting', 'in_progress', 'timed_out'], illegal: { phase: 'in_progress', phase_seq: 9 } },
    { seed: ['waiting', 'in_progress', 'success'], illegal: { phase: 'in_progress', phase_seq: 9 } },
    { seed: ['waiting', 'failed'], illegal: { phase: 'waiting', phase_seq: 9 } },
  ] as const)('$# illegal $illegal.phase does not regress or advance state', ({ seed, illegal }) => {
    const service = new CommandStateService();
    service.startCommand('inst-1', makeAck());
    let seq = 0;
    const expected = seed[seed.length - 1];
    for (const phase of seed) {
      service.onSseEvent(makeEvent({ phase: phase as CommandProgressEvent['phase'], phase_seq: seq++ }));
    }
    expect(() => service.onSseEvent(makeEvent({
      phase: illegal.phase as CommandProgressEvent['phase'],
      phase_seq: illegal.phase_seq,
    }))).not.toThrow();
    expect(service.stateFor('inst-1')?.phase).toBe(expected);
  });
});

describe('CommandStateService — phase_seq monotonic guard (R9)', () => {
  it('stale (lower seq) events are ignored', () => {
    const service = new CommandStateService();
    service.startCommand('inst-1', makeAck());
    service.onSseEvent(makeEvent({ phase: 'waiting', phase_seq: 0 }));
    service.onSseEvent(makeEvent({ phase: 'in_progress', phase_seq: 1 }));
    service.onSseEvent(makeEvent({ phase: 'success', phase_seq: 0 })); // stale
    expect(service.stateFor('inst-1')?.phase).toBe('in_progress');
  });

  it('duplicate (equal seq) events are ignored — no double-apply', () => {
    const service = new CommandStateService();
    service.startCommand('inst-1', makeAck());
    service.onSseEvent(makeEvent({ phase: 'waiting', phase_seq: 0 }));
    service.onSseEvent(makeEvent({ phase: 'in_progress', phase_seq: 1 }));
    service.onSseEvent(makeEvent({ phase: 'in_progress', phase_seq: 1, elapsed_ms: 99999 }));
    expect(service.stateFor('inst-1')?.elapsedMs).not.toBe(99999);
    expect(service.stateFor('inst-1')?.phase).toBe('in_progress');
  });

  it('heartbeat (same phase, seq+1) refreshes elapsed/eta ONLY — never advances or duplicates', () => {
    const service = new CommandStateService();
    service.startCommand('inst-1', makeAck());
    service.onSseEvent(makeEvent({ phase: 'waiting', phase_seq: 0 }));
    service.onSseEvent(makeEvent({ phase: 'in_progress', phase_seq: 1, elapsed_ms: 20000, eta_ms: 40000 }));
    // 10s heartbeat: re-emit in_progress with phase_seq+1, fresh elapsed.
    service.onSseEvent(makeEvent({ phase: 'in_progress', phase_seq: 2, elapsed_ms: 30000, eta_ms: 30000 }));
    service.onSseEvent(makeEvent({ phase: 'in_progress', phase_seq: 3, elapsed_ms: 40000, eta_ms: 20000 }));
    expect(service.stateFor('inst-1')?.phase).toBe('in_progress');
    expect(service.stateFor('inst-1')?.phaseSeq).toBe(3);
    expect(service.stateFor('inst-1')?.elapsedMs).toBe(40000);
    expect(service.stateFor('inst-1')?.etaMs).toBe(20000);
    // Still exactly one card for the instance (signal map keyed by instance).
    expect(service.activeByInstance().size).toBe(1);
  });

  it('out-of-order delivery cannot regress a terminal state', () => {
    const service = new CommandStateService();
    service.startCommand('inst-1', makeAck());
    service.onSseEvent(makeEvent({ phase: 'waiting', phase_seq: 0 }));
    service.onSseEvent(makeEvent({ phase: 'success', phase_seq: 2 }));
    service.onSseEvent(makeEvent({ phase: 'in_progress', phase_seq: 1 })); // late
    expect(service.stateFor('inst-1')?.phase).toBe('success');
  });

  // S1 regression (2026-08-31): the prior ``Number.isFinite(...) && <=``
  // guard inverted NaN handling — a NaN event slipped through the LHS
  // and corrupted phaseSeq to NaN, which then short-circuited every
  // subsequent monotonic check. The fix flips the polarity: non-finite
  // OR less-than-or-equal ⇒ ignore.
  it('S1: NaN phase_seq is ignored — state machine never stores NaN', () => {
    const service = new CommandStateService();
    service.startCommand('inst-1', makeAck());
    service.onSseEvent(makeEvent({ phase: 'waiting', phase_seq: 0 }));
    service.onSseEvent(makeEvent({ phase: 'in_progress', phase_seq: 1 }));
    // BEFORE the fix this would land phase_seq=NaN on the entry; AFTER,
    // the guard drops it and state survives intact.
    service.onSseEvent(
      makeEvent({ phase: 'in_progress', phase_seq: Number.NaN as unknown as number }),
    );
    expect(service.stateFor('inst-1')?.phase).toBe('in_progress');
    expect(service.stateFor('inst-1')?.phaseSeq).toBe(1);
    expect(Number.isNaN(service.stateFor('inst-1')?.phaseSeq ?? 0)).toBe(false);
    // A later, valid heartbeat must still apply — the NaN did not brick
    // the guard for subsequent events.
    service.onSseEvent(makeEvent({ phase: 'in_progress', phase_seq: 2, elapsed_ms: 20000 }));
    expect(service.stateFor('inst-1')?.phaseSeq).toBe(2);
    expect(service.stateFor('inst-1')?.elapsedMs).toBe(20000);
  });

  it('S1: ±Infinity phase_seq is ignored — no Infinity ever stored', () => {
    const service = new CommandStateService();
    service.startCommand('inst-1', makeAck());
    service.onSseEvent(makeEvent({ phase: 'waiting', phase_seq: 0 }));
    service.onSseEvent(makeEvent({ phase: 'in_progress', phase_seq: 1 }));
    service.onSseEvent(
      makeEvent({ phase: 'in_progress', phase_seq: Number.POSITIVE_INFINITY as unknown as number }),
    );
    service.onSseEvent(
      makeEvent({ phase: 'in_progress', phase_seq: Number.NEGATIVE_INFINITY as unknown as number }),
    );
    const seq = service.stateFor('inst-1')?.phaseSeq ?? 0;
    expect(Number.isFinite(seq)).toBe(true);
    expect(seq).toBe(1);
  });
});

describe('CommandStateService — command_id correlation', () => {
  it('wrong command_id events are ignored', () => {
    const service = new CommandStateService();
    service.startCommand('inst-1', makeAck({ command_id: 'cmd-1' }));
    service.onSseEvent(makeEvent({ command_id: 'cmd-other', phase: 'failed', phase_seq: 99 }));
    expect(service.stateFor('inst-1')?.commandId).toBe('cmd-1');
    expect(service.stateFor('inst-1')?.phase).toBe('waiting');
  });

  it('an SSE event for an unknown command is ADOPTED (reload self-healing)', () => {
    const service = new CommandStateService();
    service.onSseEvent(makeEvent({ phase: 'in_progress', phase_seq: 4, elapsed_ms: 12000 }));
    expect(service.stateFor('inst-1')?.commandId).toBe('cmd-1');
    expect(service.stateFor('inst-1')?.phase).toBe('in_progress');
    expect(service.stateFor('inst-1')?.phaseSeq).toBe(4);
  });

  it('malformed events (missing ids) are swallowed', () => {
    const service = new CommandStateService();
    service.startCommand('inst-1', makeAck());
    expect(() => service.onSseEvent(makeEvent({ instance_id: '', command_id: '' }))).not.toThrow();
    expect(service.stateFor('inst-1')?.phase).toBe('waiting');
  });
});

describe('CommandStateService — cross-instance isolation (R2 / SC10)', () => {
  it('events for another instance never render into this instance', () => {
    const service = new CommandStateService();
    service.startCommand('inst-1', makeAck({ command_id: 'cmd-A' }));
    service.onSseEvent(makeEvent({ instance_id: 'inst-2', command_id: 'cmd-B', phase: 'failed', phase_seq: 0 }));
    expect(service.stateFor('inst-1')?.phase).toBe('waiting');
    expect(service.stateFor('inst-2')?.phase).toBe('failed'); // own slot only
  });

  it('returning to an instance still shows its own retained progress', () => {
    const service = new CommandStateService();
    service.startCommand('inst-1', makeAck());
    service.onSseEvent(makeEvent({ phase: 'waiting', phase_seq: 0 }));
    service.onSseEvent(makeEvent({ phase: 'in_progress', phase_seq: 1 }));
    service.startCommand('inst-2', makeAck({ command_id: 'cmd-2' }));
    // Switch back conceptually: stateFor('inst-1') retained.
    expect(service.stateFor('inst-1')?.phase).toBe('in_progress');
    expect(service.stateFor('inst-2')?.phase).toBe('waiting');
  });
});

describe('CommandStateService — reconcile (GET fallback, server wins)', () => {
  it('server success overwrites FE-stale in_progress and triggers refetch exactly once', async () => {
    const service = new CommandStateService();
    const responses: GetActiveResponse[] = [
      { exists: true, command: makeEvent({ phase: 'in_progress', phase_seq: 7, elapsed_ms: 42000 }) },
      {
        exists: true,
        command: makeEvent({
          phase: 'success', phase_seq: 8, elapsed_ms: 60000,
          detail: { compacted_type: 'summary', tokens_before: 90000, tokens_after: 30000 },
        }),
      },
    ];
    const calls: string[] = [];
    const fetch: Fetch = async (id) => {
      calls.push(id);
      return responses.shift() ?? null;
    };
    service.wireFetch(fetch);
    service.startCommand('inst-1', makeAck());
    service.onSseEvent(makeEvent({ phase: 'waiting', phase_seq: 0 }));
    service.onSseEvent(makeEvent({ phase: 'in_progress', phase_seq: 1, elapsed_ms: 9000 }));

    await service.reconcileFromServer('inst-1');
    expect(service.stateFor('inst-1')?.phase).toBe('in_progress');
    expect(service.stateFor('inst-1')?.phaseSeq).toBe(7);
    expect(service.stateFor('inst-1')?.elapsedMs).toBe(42000);
    expect(service.refetchRequest()).toBe(0); // non-terminal: no refetch

    await service.reconcileFromServer('inst-1');
    expect(service.stateFor('inst-1')?.phase).toBe('success');
    expect(service.refetchRequest()).toBe(1); // EXACTLY once
    expect(service.refetchInstanceId()).toBe('inst-1');
    expect(calls).toEqual(['inst-1', 'inst-1']);
  });

  it('{exists:false} clears the card SILENTLY — no refetch, no error', async () => {
    const service = new CommandStateService();
    service.wireFetch(async () => ({ exists: false }));
    service.startCommand('inst-1', makeAck());
    await service.reconcileFromServer('inst-1');
    expect(service.stateFor('inst-1')).toBeNull();
    expect(service.refetchRequest()).toBe(0);
  });

  it('{exists:false} on an instance with no card is a no-op', async () => {
    const service = new CommandStateService();
    service.wireFetch(async () => ({ exists: false }));
    await expect(service.reconcileFromServer('inst-1')).resolves.toBeUndefined();
    expect(service.stateFor('inst-1')).toBeNull();
  });

  it('network error (null) neither clears nor duplicates the card', async () => {
    const service = new CommandStateService();
    service.wireFetch(async () => null);
    service.startCommand('inst-1', makeAck());
    service.onSseEvent(makeEvent({ phase: 'waiting', phase_seq: 0 }));
    await service.reconcileFromServer('inst-1');
    expect(service.stateFor('inst-1')?.phase).toBe('waiting');
    expect(service.refetchRequest()).toBe(0);
  });

  it('a stale reconcile response (superseded by a newer call) is dropped', async () => {
    const service = new CommandStateService();
    let resolveFirst!: (v: GetActiveResponse) => void;
    const first = new Promise<GetActiveResponse>((resolve) => { resolveFirst = resolve; });
    const calls: number[] = [];
    const fetch: Fetch = async () => {
      calls.push(calls.length);
      if (calls.length === 1) return first;
      return { exists: false };
    };
    service.wireFetch(fetch);
    service.startCommand('inst-1', makeAck());
    const p1 = service.reconcileFromServer('inst-1');
    await service.reconcileFromServer('inst-1'); // second wins: exists:false
    resolveFirst({ exists: true, command: makeEvent({ phase: 'in_progress', phase_seq: 5 }) });
    await p1;
    // The LATE first response must not resurrect the cleared card.
    expect(service.stateFor('inst-1')).toBeNull();
  });

  it('restores a card for an instance with no local entry (reload recovery)', async () => {
    const service = new CommandStateService();
    service.wireFetch(async () => ({
      exists: true,
      command: makeEvent({ phase: 'in_progress', phase_seq: 3, elapsed_ms: 25000 }),
    }));
    await service.reconcileFromServer('inst-1');
    expect(service.stateFor('inst-1')?.phase).toBe('in_progress');
    expect(service.stateFor('inst-1')?.elapsedMs).toBe(25000);
  });

  // e2e-found race (slash-command-compact spec, R2 family): the load-time
  // GET is issued BEFORE the user's /compact POST, resolves AFTER the ack
  // seed, and its {exists:false} used to silently clear the fresh card and
  // kill the poll. The issue-time staleness rule must drop it.
  it('a reconcile issued BEFORE the ack-seed does not clear the fresh card', async () => {
    const service = new CommandStateService();
    service.startCommand('inst-1', makeAck()); // card seeded at T0
    // GET issued at/before T0 (epoch 0 — strictly ≤ startedAtMs), resolves
    // with the pre-command world.
    service.reconcileFromServerResult('inst-1', { exists: false }, 0);
    expect(service.stateFor('inst-1')?.phase).toBe('waiting'); // survived
  });

  it('a stale reconcile carrying a PREVIOUS command never clobbers a newer one', () => {
    const service = new CommandStateService();
    service.startCommand('inst-1', makeAck({ command_id: 'cmd-new' }));
    service.reconcileFromServerResult(
      'inst-1',
      { exists: true, command: makeEvent({ command_id: 'cmd-old', phase: 'failed', phase_seq: 9 }) },
      Date.now() - 1000, // issued before cmd-new started
    );
    expect(service.stateFor('inst-1')?.commandId).toBe('cmd-new');
    expect(service.stateFor('inst-1')?.phase).toBe('waiting');
  });

  it('a reconcile issued AFTER the command started still wins (normal poll path)', () => {
    const service = new CommandStateService();
    const t0 = Date.now();
    service.startCommand('inst-1', makeAck({ command_id: 'cmd-1' }));
    service.reconcileFromServerResult(
      'inst-1',
      { exists: true, command: makeEvent({ command_id: 'cmd-1', phase: 'in_progress', phase_seq: 3, elapsed_ms: 9000 }) },
      t0 + 5000, // issued well after the seed
    );
    expect(service.stateFor('inst-1')?.phase).toBe('in_progress');
    expect(service.stateFor('inst-1')?.phaseSeq).toBe(3);
  });
});

describe('CommandStateService — terminal display-window eviction', () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  it('a terminal state is evicted after the display window', () => {
    const service = new CommandStateService();
    service.startCommand('inst-1', makeAck());
    service.onSseEvent(makeEvent({ phase: 'waiting', phase_seq: 0 }));
    service.onSseEvent(makeEvent({ phase: 'success', phase_seq: 1 }));
    expect(service.stateFor('inst-1')).not.toBeNull();
    jest.advanceTimersByTime(TERMINAL_DISPLAY_MS - 10);
    expect(service.stateFor('inst-1')).not.toBeNull();
    jest.advanceTimersByTime(20);
    expect(service.stateFor('inst-1')).toBeNull();
  });

  it('non-terminal states are NEVER evicted by the display window', () => {
    const service = new CommandStateService();
    service.startCommand('inst-1', makeAck());
    jest.advanceTimersByTime(TERMINAL_DISPLAY_MS * 10);
    expect(service.stateFor('inst-1')?.phase).toBe('waiting');
  });

  it('an old terminal timer never evicts a NEWER command on the same instance', () => {
    const service = new CommandStateService();
    service.startCommand('inst-1', makeAck({ command_id: 'cmd-old' }));
    service.onSseEvent(makeEvent({ phase: 'success', phase_seq: 1 }));
    jest.advanceTimersByTime(TERMINAL_DISPLAY_MS - 10);
    // New command arrives before the old eviction fires.
    service.startCommand('inst-1', makeAck({ command_id: 'cmd-new' }));
    jest.advanceTimersByTime(20);
    expect(service.stateFor('inst-1')?.commandId).toBe('cmd-new');
    expect(service.stateFor('inst-1')?.phase).toBe('waiting');
  });
});

describe('CommandStateService — REST poll loop (Task 8 / SC15)', () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  function harness(responder: Fetch) {
    const service = new CommandStateService();
    const fetch = jest.fn<Promise<GetActiveResponse | null>, [string]>(responder);
    service.wireFetch(fetch);
    return { service, fetch };
  }

  it('poll STARTS only while card active AND SSE dead; cadence ~5s', async () => {
    const { service, fetch } = harness(async () => ({
      exists: true,
      command: makeEvent({ phase: 'in_progress', phase_seq: 2, elapsed_ms: 15000 }),
    }));
    service.startCommand('inst-1', makeAck());
    // SSE alive → no poll.
    service.syncPolling('inst-1', true);
    jest.advanceTimersByTime(POLL_INTERVAL_MS * 3);
    expect(fetch).not.toHaveBeenCalled();
    // SSE dies → poll starts.
    service.syncPolling('inst-1', false);
    jest.advanceTimersByTime(POLL_INTERVAL_MS);
    expect(fetch).toHaveBeenCalledTimes(1);
    jest.advanceTimersByTime(POLL_INTERVAL_MS);
    expect(fetch).toHaveBeenCalledTimes(2); // ~5s cadence
  });

  it('poll STOPS on terminal phase — no timer leak', async () => {
    const { service, fetch } = harness(async () => null);
    service.startCommand('inst-1', makeAck());
    service.syncPolling('inst-1', false);
    expect(jest.getTimerCount()).toBeGreaterThan(0);
    // Terminal arrives via SSE while polling.
    service.onSseEvent(makeEvent({ phase: 'waiting', phase_seq: 0 }));
    service.onSseEvent(makeEvent({ phase: 'success', phase_seq: 1 }));
    const timersWithEviction = jest.getTimerCount(); // eviction timer only
    jest.advanceTimersByTime(POLL_INTERVAL_MS * 3);
    expect(fetch).not.toHaveBeenCalled();
    // Only the eviction timer remains, and it cleans itself up.
    jest.advanceTimersByTime(TERMINAL_DISPLAY_MS + 10);
    expect(jest.getTimerCount()).toBe(timersWithEviction - 1);
  });

  it('poll STOPS on {exists:false} after clearing the card silently', async () => {
    const { service, fetch } = harness(async () => ({ exists: false }));
    service.startCommand('inst-1', makeAck());
    service.syncPolling('inst-1', false);
    jest.advanceTimersByTime(POLL_INTERVAL_MS);
    expect(fetch).toHaveBeenCalledTimes(1);
    await flush();
    expect(service.stateFor('inst-1')).toBeNull(); // silent clear
    jest.advanceTimersByTime(POLL_INTERVAL_MS * 3);
    expect(fetch).toHaveBeenCalledTimes(1); // stopped
  });

  it('network error during poll neither clears the card NOR stops polling', async () => {
    const { service, fetch } = harness(async () => null);
    service.startCommand('inst-1', makeAck());
    service.syncPolling('inst-1', false);
    jest.advanceTimersByTime(POLL_INTERVAL_MS);
    await flush();
    expect(service.stateFor('inst-1')?.phase).toBe('waiting'); // intact
    jest.advanceTimersByTime(POLL_INTERVAL_MS);
    expect(fetch).toHaveBeenCalledTimes(2); // still polling
  });

  it('SSE coming back alive stops the poll (no leak)', async () => {
    const { service, fetch } = harness(async () => null);
    service.startCommand('inst-1', makeAck());
    service.syncPolling('inst-1', false);
    service.syncPolling('inst-1', true);
    jest.advanceTimersByTime(POLL_INTERVAL_MS * 4);
    expect(fetch).not.toHaveBeenCalled();
    expect(jest.getTimerCount()).toBe(0);
  });

  it('no instance → no poll, no timers', () => {
    const { service } = harness(async () => null);
    service.syncPolling(null, false);
    service.syncPolling(null, true);
    expect(jest.getTimerCount()).toBe(0);
  });

  it('stopAllTimers clears the poll timer (ngOnDestroy contract)', async () => {
    const { service, fetch } = harness(async () => null);
    service.startCommand('inst-1', makeAck());
    service.syncPolling('inst-1', false);
    service.stopAllTimers();
    jest.advanceTimersByTime(POLL_INTERVAL_MS * 4);
    expect(fetch).not.toHaveBeenCalled();
    expect(jest.getTimerCount()).toBe(0);
    // Per-instance state survives teardown by design.
    expect(service.stateFor('inst-1')?.phase).toBe('waiting');
  });
});

describe('CommandStateService — refetch trigger exactly-once (Task 7 / SC8)', () => {
  it('duplicate terminal events (reconcile + SSE) bump the refetch ONCE per command', async () => {
    const service = new CommandStateService();
    service.wireFetch(async () => ({
      exists: true,
      command: makeEvent({
        phase: 'fallback_applied', phase_seq: 3,
        detail: { compacted_type: 'truncation', tokens_before: 80000, tokens_after: 20000 },
      }),
    }));
    service.startCommand('inst-1', makeAck());
    await service.reconcileFromServer('inst-1');
    expect(service.refetchRequest()).toBe(1);
    // Late SSE replay of the same terminal phase — seq guard + flag.
    service.onSseEvent(makeEvent({ phase: 'fallback_applied', phase_seq: 3 }));
    expect(service.refetchRequest()).toBe(1);
  });

  it('a second command on the same instance can trigger a second refetch', () => {
    const service = new CommandStateService();
    service.startCommand('inst-1', makeAck({ command_id: 'cmd-1' }));
    service.onSseEvent(makeEvent({ phase: 'waiting', phase_seq: 0 }));
    service.onSseEvent(makeEvent({ phase: 'success', phase_seq: 1 }));
    service.startCommand('inst-1', makeAck({ command_id: 'cmd-2' }));
    service.onSseEvent(makeEvent({ command_id: 'cmd-2', phase: 'waiting', phase_seq: 0 }));
    service.onSseEvent(makeEvent({ command_id: 'cmd-2', phase: 'success', phase_seq: 1 }));
    expect(service.refetchRequest()).toBe(2);
  });
});
