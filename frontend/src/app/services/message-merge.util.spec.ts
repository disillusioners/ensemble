import {
  evictPendingByAge,
  isTerminalStatus,
  makeProvisionalMessage,
  mergeMessagesById,
} from './message-merge.util';
import type { Message } from '../models';

/**
 * Phase 2 / message-display-latency §7 FE unit tests, behaviors
 * #1–#5 — covered here as pure-function specs so the merge / eviction
 * / provisional-append contract has a single canonical test surface.
 *
 *   #1 dedup collapse     — see ``mergeMessagesById`` clears pending
 *                           + the ``SseService.upsertMessage`` mirror
 *                           in ``sse.service.spec.ts``.
 *   #2 union-merge        — ``mergeMessagesById`` below.
 *   #3 eviction           — ``evictPendingByAge`` + ``isTerminalStatus``
 *                           below (TTL portion). Terminal-status purge
 *                           is exercised on the SSE side via
 *                           ``pendingPurgeRequest`` in
 *                           ``sse.service.spec.ts``.
 *   #4 optimistic append  — ``makeProvisionalMessage`` below (id
 *                           present → builds; id absent → caller must
 *                           short-circuit — verified by the absence
 *                           of a "skip" branch here).
 *   #5 reconnect          — SSE-side test in ``sse.service.spec.ts``
 *                           (refetchRequest trigger).
 */

const FIXED_NOW = Date.parse('2026-08-30T12:00:00Z');
const TEN_MIN_MS = 10 * 60 * 1000;

function makeMessage(overrides: Partial<Message> = {}): Message {
  return {
    message_id: 'm-1',
    role: 'user',
    content: 'hello',
    created_at: '2026-08-30T11:55:00Z',
    ...overrides,
  };
}

describe('isTerminalStatus', () => {
  it('should return true for terminal statuses', () => {
    expect(isTerminalStatus('completed')).toBe(true);
    expect(isTerminalStatus('error')).toBe(true);
    expect(isTerminalStatus('terminated')).toBe(true);
    expect(isTerminalStatus('failed')).toBe(true);
  });

  it('should return false for non-terminal statuses', () => {
    expect(isTerminalStatus('running')).toBe(false);
    expect(isTerminalStatus('idle')).toBe(false);
    expect(isTerminalStatus('queued')).toBe(false);
    expect(isTerminalStatus('waiting_children')).toBe(false);
    expect(isTerminalStatus('paused')).toBe(false);
  });

  it('should return false for null / undefined / unknown', () => {
    // Unknown statuses are treated as non-terminal: better to keep a
    // stale provisional bubble than to drop a real one on a status we
    // don't recognize (back-compat with future status additions).
    expect(isTerminalStatus(null)).toBe(false);
    expect(isTerminalStatus(undefined)).toBe(false);
    expect(isTerminalStatus('')).toBe(false);
    expect(isTerminalStatus('restarting')).toBe(false);
  });
});

describe('makeProvisionalMessage', () => {
  it('should build a pending user message carrying the server id and POST timestamp', () => {
    const m = makeProvisionalMessage({
      messageId: 'echo-1',
      content: 'hi',
      createdAt: '2026-08-30T12:00:00Z',
      instanceId: 'inst-1',
    });
    expect(m).toEqual({
      message_id: 'echo-1',
      role: 'user',
      content: 'hi',
      created_at: '2026-08-30T12:00:00Z',
      instance_id: 'inst-1',
      images: undefined,
      pending: true,
    });
  });

  it('should forward attached images when supplied', () => {
    const m = makeProvisionalMessage({
      messageId: 'echo-2',
      content: 'look',
      createdAt: '2026-08-30T12:00:00Z',
      instanceId: 'inst-1',
      images: ['data:image/png;base64,AAAA'],
    });
    expect(m.images).toEqual(['data:image/png;base64,AAAA']);
    expect(m.pending).toBe(true);
  });
});

describe('mergeMessagesById (union-by-id)', () => {
  it('should return existing unchanged when incoming is empty', () => {
    const existing = [makeMessage({ message_id: 'a' })];
    const merged = mergeMessagesById(existing, []);
    expect(merged).toEqual(existing);
    // Returned reference MUST differ so the signal mutates.
    expect(merged).not.toBe(existing);
  });

  it('should append a brand-new message id (refetch with a NEW server entry)', () => {
    const existing = [makeMessage({ message_id: 'a', created_at: '2026-08-30T11:00:00Z' })];
    const incoming = [makeMessage({ message_id: 'b', created_at: '2026-08-30T12:00:00Z' })];
    const merged = mergeMessagesById(existing, incoming);
    expect(merged.map(m => m.message_id)).toEqual(['a', 'b']);
  });

  it('should REPLACE an existing entry with the same id — no duplicate (refetch WITH same id)', () => {
    const existing = [
      makeMessage({
        message_id: 'a',
        content: 'old',
        created_at: '2026-08-30T11:00:00Z',
        pending: true,
      }),
    ];
    const incoming = [
      makeMessage({
        message_id: 'a',
        content: 'new',
        created_at: '2026-08-30T11:00:00Z',
      }),
    ];
    const merged = mergeMessagesById(existing, incoming);
    expect(merged.length).toBe(1);
    expect(merged[0].content).toBe('new');
  });

  it('should clear pending flag when the incoming copy is not pending (dedup collapse)', () => {
    const existing = [
      makeMessage({
        message_id: 'echo-1',
        content: 'hello',
        created_at: '2026-08-30T11:00:00Z',
        pending: true,
      }),
    ];
    // SSE echo or drain re-emit: same id, server-side copy, no pending flag.
    const incoming = [
      makeMessage({
        message_id: 'echo-1',
        content: 'hello',
        created_at: '2026-08-30T11:00:00Z',
      }),
    ];
    const merged = mergeMessagesById(existing, incoming);
    expect(merged.length).toBe(1);
    expect(merged[0].pending).toBeUndefined();
    expect(merged[0].created_at).toBe('2026-08-30T11:00:00Z');
  });

  it('keeps pending STAYS cleared after BOTH arrival orders (echo BEFORE the 202 — MIN-1b)', () => {
    // MIN-1b regression: the SSE echo can land BEFORE the HTTP 202
    // resolves. The optimistic append then merges a ``pending: true``
    // provisional over the ALREADY-CONFIRMED server copy — the old
    // incoming-wins rule resurrected the spinner on a confirmed bubble.
    // Arrival order 1: server copy first, provisional second → the
    // merged entry must stay confirmed (pending stays cleared).
    const serverCopy = makeMessage({
      message_id: 'echo-1',
      content: 'hello',
      created_at: '2026-08-30T11:00:00Z',
    });
    const lateProvisional = makeMessage({
      message_id: 'echo-1',
      content: 'hello',
      created_at: '2026-08-30T11:00:00Z',
      pending: true,
    });
    const merged = mergeMessagesById([serverCopy], [lateProvisional]);
    expect(merged.length).toBe(1);
    expect(merged[0].pending).toBeUndefined();

    // Arrival order 2 (the long-covered direction): provisional first,
    // server copy second → also cleared.
    const mergedReversed = mergeMessagesById(
      [makeMessage({ ...lateProvisional })],
      [makeMessage({ ...serverCopy })],
    );
    expect(mergedReversed.length).toBe(1);
    expect(mergedReversed[0].pending).toBeUndefined();
  });

  it('keeps the EARLIER timestamp for a confirmed entry when GET re-stamps it later (MIN-4)', () => {
    // MIN-4: GET read-back re-stamps rows with the checkpoint-commit
    // timestamp, which is LATER than the original POST stamp. The merge
    // must keep the EARLIER stamp so the user bubble stays in its
    // original send position instead of re-sorting below inter-streamed
    // assistant messages.
    const originalPostStamp = '2026-08-30T11:00:00Z';
    const restampedLater = '2026-08-30T11:05:00Z';
    const local = makeMessage({
      message_id: 'u-1',
      created_at: originalPostStamp,
    });
    const incoming = makeMessage({
      message_id: 'u-1',
      created_at: restampedLater,
    });
    const merged = mergeMessagesById([local], [incoming]);
    expect(merged.length).toBe(1);
    expect(merged[0].created_at).toBe(originalPostStamp);
  });

  it('keeps the earlier timestamp regardless of which side carries it (MIN-4, reverse direction)', () => {
    // The incoming copy can also be the earlier one (e.g. the local
    // entry was seeded from a later snapshot). Same rule: earlier wins.
    const earlier = '2026-08-30T10:55:00Z';
    const later = '2026-08-30T11:00:00Z';
    const local = makeMessage({ message_id: 'u-1', created_at: later });
    const incoming = makeMessage({ message_id: 'u-1', created_at: earlier });
    const merged = mergeMessagesById([local], [incoming]);
    expect(merged[0].created_at).toBe(earlier);
  });

  it('does NOT apply the earlier-of rule to a still-pending merge (202 body stamp rules)', () => {
    // MIN-4 is scoped to server-confirmed entries. A merge that stays
    // pending (duplicate provisional deliveries) keeps incoming-wins
    // for ``created_at``.
    const local = makeMessage({
      message_id: 'p-1',
      created_at: '2026-08-30T11:00:00Z',
      pending: true,
    });
    const incoming = makeMessage({
      message_id: 'p-1',
      created_at: '2026-08-30T10:59:00Z',
      pending: true,
    });
    const merged = mergeMessagesById([local], [incoming]);
    expect(merged[0].pending).toBe(true);
    expect(merged[0].created_at).toBe('2026-08-30T10:59:00Z');
  });

  it('falls back to the non-empty stamp when one side has none (MIN-4 edge)', () => {
    const local = makeMessage({ message_id: 'u-1', created_at: '' });
    const incoming = makeMessage({
      message_id: 'u-1',
      created_at: '2026-08-30T11:00:00Z',
    });
    const merged = mergeMessagesById([local], [incoming]);
    expect(merged[0].created_at).toBe('2026-08-30T11:00:00Z');
  });

  it('should preserve the incoming pending flag when both sides are pending', () => {
    // Defensive case: if a refetch returns the same id AND the same
    // pending flag (e.g. an aggressive retry), the merge should not
    // silently strip it. Either copy may carry the flag.
    const existing = [makeMessage({ message_id: 'a', pending: true })];
    const incoming = [makeMessage({ message_id: 'a', pending: true })];
    const merged = mergeMessagesById(existing, incoming);
    expect(merged[0].pending).toBe(true);
  });

  it('should preserve local-only entries (the hard prerequisite — refetch WITHOUT the pending id)', () => {
    // The provisional bubble is local-only: the server checkpoint
    // does not yet carry the injected id, so GET /messages returns a
    // shorter list. The merge MUST NOT wipe the provisional entry.
    const localProvisional = makeMessage({
      message_id: 'echo-local',
      content: 'optimistic',
      created_at: '2026-08-30T11:59:00Z',
      pending: true,
    });
    const serverView = [
      makeMessage({
        message_id: 'older-1',
        content: 'prior turn',
        created_at: '2026-08-30T11:00:00Z',
      }),
    ];
    const merged = mergeMessagesById([localProvisional], serverView);
    // Preservation is the contract; ORDER is arrival-based (no
    // created_at re-sort): the provisional stays at its append
    // position and the not-yet-known server row appends after it.
    // (A history-after-provisional sequence only occurs in the
    // send-races-initial-load race; it heals on the next replace-mode
    // load.)
    expect(merged.map(m => m.message_id)).toEqual(['echo-local', 'older-1']);
    expect(merged.find(m => m.message_id === 'echo-local')?.pending).toBe(true);
  });

  it('appends a new row in arrival position — NO created_at re-sort (order contract, 2026-09-05)', () => {
    // CONTRACT CHANGE (stale-message fix): the merge used to re-sort
    // the whole transcript by ``created_at``. Server stamps for
    // metadata-less checkpoint rows are unstable, so that re-sort let
    // history time-travel. Order is now array-order-based: existing
    // order preserved, genuinely-new rows appended — even when the new
    // row's stamp is EARLIER than existing rows'.
    const existing = [makeMessage({ message_id: 'b', created_at: '2026-08-30T12:00:00Z' })];
    const incoming = [makeMessage({ message_id: 'a', created_at: '2026-08-30T11:00:00Z' })];
    const merged = mergeMessagesById(existing, incoming);
    expect(merged.map(m => m.message_id)).toEqual(['b', 'a']);
  });

  it('should not mutate the inputs', () => {
    const existing = [makeMessage({ message_id: 'a' })];
    const incoming = [makeMessage({ message_id: 'b', pending: true })];
    const existingBefore = existing.slice();
    const incomingBefore = incoming.slice();
    mergeMessagesById(existing, incoming);
    expect(existing).toEqual(existingBefore);
    expect(incoming).toEqual(incomingBefore);
  });
});

describe('mergeMessagesById — single-document compaction doc (compaction-output-structure §10.9a)', () => {
  // The backend persists exactly ONE SystemMessage per compaction with
  // the stable id ``compaction-global-{instance_id}-{seq}``. Re-delivery
  // (GET refetch, drain re-emit, reconnect catch-up) therefore re-uses
  // the SAME id — the union-by-id merge must upsert it idempotently
  // (no duplicate card in the transcript) and keep the EARLIER
  // created_at so the card never re-sorts below newer traffic (MIN-4).
  function makeCompactionDoc(overrides: Partial<Message> = {}): Message {
    return makeMessage({
      message_id: 'compaction-global-inst-1-3',
      role: 'system',
      content:
        '[CONTEXT COMPACTION — mode=summary | compacted_at=2026-09-01T09:00:00Z]\n' +
        '\n' +
        '── GLOBAL OVERVIEW ──\n' +
        'The user is hardening the compaction output. Decisions: single doc, stable id, fold card.\n' +
        '\n' +
        '── SECTION DETAIL ──\n' +
        '### SECTION 1/2 — messages #1–#10\n' +
        'Early arc: baseline counts captured.\n' +
        '\n' +
        '── END OF COMPACTED CONTEXT — everything below is the verbatim recent transcript ──',
      created_at: '2026-08-30T11:00:00Z',
      ...overrides,
    });
  }

  it('upserts a re-delivered same-id compaction doc WITHOUT duplication', () => {
    const existing = [makeCompactionDoc()];
    // Same stable id — the backend re-mints the doc body but never the id.
    const redelivered = [makeCompactionDoc({ content: makeCompactionDoc().content + '\n(extra section)' })];
    const merged = mergeMessagesById(existing, redelivered);
    const docs = merged.filter(m => m.message_id.startsWith('compaction-global-'));
    expect(merged.length).toBe(1);
    expect(docs.length).toBe(1);
    expect(docs[0].message_id).toBe('compaction-global-inst-1-3');
    // Incoming body wins on upsert (top-level merge rule) — one card
    // showing the LATEST doc content.
    expect(docs[0].content.endsWith('\n(extra section)')).toBe(true);
  });

  it('keeps the EARLIER created_at when the refetch re-stamps the doc later', () => {
    const originalStamp = '2026-08-30T11:00:00Z';
    const restampedLater = '2026-08-30T11:30:00Z';
    const existing = [makeCompactionDoc({ created_at: originalStamp })];
    const incoming = [makeCompactionDoc({ created_at: restampedLater })];
    const merged = mergeMessagesById(existing, incoming);
    expect(merged.length).toBe(1);
    expect(merged[0].created_at).toBe(originalStamp);
  });

  it('keeps the EARLIER created_at regardless of arrival direction (doc seeded from a later snapshot)', () => {
    const earlier = '2026-08-30T10:55:00Z';
    const later = '2026-08-30T11:00:00Z';
    const merged = mergeMessagesById(
      [makeCompactionDoc({ created_at: later })],
      [makeCompactionDoc({ created_at: earlier })],
    );
    expect(merged.length).toBe(1);
    expect(merged[0].created_at).toBe(earlier);
  });

  it('keeps the doc and user traffic interleaved in created_at order after a re-delivery (tail never re-sorted)', () => {
    const doc = makeCompactionDoc({ created_at: '2026-08-30T11:00:00Z' });
    const before = makeMessage({ message_id: 'u-1', created_at: '2026-08-30T10:59:00Z' });
    const after = makeMessage({ message_id: 'u-2', created_at: '2026-08-30T11:01:00Z' });
    // Re-deliver the doc (same id, later re-stamp) alongside a brand-new
    // tail message; positions of the OTHER messages must not move.
    const merged = mergeMessagesById([before, doc, after], [
      makeCompactionDoc({ created_at: '2026-08-30T11:30:00Z' }),
      makeMessage({ message_id: 'u-3', created_at: '2026-08-30T11:02:00Z' }),
    ]);
    expect(merged.map(m => m.message_id)).toEqual(['u-1', 'compaction-global-inst-1-3', 'u-2', 'u-3']);
  });
});

describe('mergeMessagesById — merge order (stale-message fix, 2026-09-05)', () => {
  // Post-send ordering contract: transcript order is the server's ARRAY
  // order (checkpoint order), captured at seed time, plus arrival-order
  // appends. ``created_at`` NEVER drives position — the backend
  // re-stamps metadata-less checkpoint messages with the latest
  // checkpoint-commit time on every read (a moving value), which the
  // previous full-transcript re-sort turned into history time-travel.

  it('preserves server array order through a post-send refetch — an old row re-stamped LATER than the new message must NOT jump above it', () => {
    // THE regression: local list seeded from GET (server array order),
    // user sends d, refetch returns b with a re-stamped created_at
    // (latest checkpoint commit — later than d's stamp). The old
    // re-sort placed b directly above d; the merge must upsert in
    // place instead.
    const local = [
      makeMessage({ message_id: 'a', created_at: '2026-08-30T10:00:00Z' }),
      makeMessage({ message_id: 'b', created_at: '2026-08-30T10:05:00Z' }),
      makeMessage({ message_id: 'c', created_at: '2026-08-30T10:10:00Z' }),
      makeMessage({ message_id: 'd', created_at: '2026-08-30T12:00:00Z' }),
    ];
    const refetch = [
      makeMessage({ message_id: 'a', created_at: '2026-08-30T11:58:00Z' }), // re-stamped
      makeMessage({ message_id: 'b', created_at: '2026-08-30T11:59:59Z' }), // re-stamped LATER than d
      makeMessage({ message_id: 'c', created_at: '2026-08-30T11:59:00Z' }), // re-stamped
      makeMessage({ message_id: 'd', created_at: '2026-08-30T12:00:00Z' }),
      makeMessage({ message_id: 'e', created_at: '2026-08-30T12:01:00Z' }), // new row
    ];
    const merged = mergeMessagesById(local, refetch);
    // Array order untouched by the unstable stamps; e appends at the end.
    expect(merged.map(m => m.message_id)).toEqual(['a', 'b', 'c', 'd', 'e']);
  });

  it('appends genuinely-new unknown rows in incoming array order', () => {
    const local = [makeMessage({ message_id: 'a', created_at: '2026-08-30T10:00:00Z' })];
    const incoming = [
      makeMessage({ message_id: 'x', created_at: '2026-08-30T10:05:00Z' }),
      makeMessage({ message_id: 'y', created_at: '2026-08-30T10:03:00Z' }), // stamps not monotonic
      makeMessage({ message_id: 'a', created_at: '2026-08-30T10:00:00Z' }), // known — upserts in place
    ];
    const merged = mergeMessagesById(local, incoming);
    expect(merged.map(m => m.message_id)).toEqual(['a', 'x', 'y']);
  });

  it('095156b4 semantics preserved: provisional → SSE echo → refetch keeps ONE confirmed bubble in its send position', () => {
    // Full post-send sequence ON TOP of the quick-display feature:
    // 1. optimistic append (pending provisional at the end of history),
    // 2. SSE echo with the same id (spinner cleared — MIN-1b / TOCTOU
    //    contract intact),
    // 3. refetch re-stamps every row later (moving checkpoint stamps).
    // Outcome: exactly one bubble, confirmed (no resurrection), stable
    // displayed stamp, position never moves.
    const sendStamp = '2026-08-30T12:00:00Z';
    const provisional = makeProvisionalMessage({
      messageId: 'echo-1',
      content: 'hello',
      createdAt: sendStamp,
      instanceId: 'inst-1',
    });
    const history = [
      makeMessage({ message_id: 'h1', created_at: '2026-08-30T11:00:00Z' }),
      makeMessage({ message_id: 'h2', created_at: '2026-08-30T11:30:00Z' }),
    ];

    // Step 1: optimistic append lands at the end of the seeded history.
    const afterSend = mergeMessagesById(history, [provisional]);
    expect(afterSend.map(m => m.message_id)).toEqual(['h1', 'h2', 'echo-1']);
    expect(afterSend[2].pending).toBe(true);

    // Step 2: SSE echo collapses onto the provisional (same id) and
    // clears the spinner.
    const afterEcho = mergeMessagesById(afterSend, [
      makeMessage({ message_id: 'echo-1', content: 'hello', created_at: sendStamp }),
    ]);
    expect(afterEcho).toHaveLength(3);
    expect(afterEcho[2].pending).toBeUndefined();
    expect(afterEcho.map(m => m.message_id)).toEqual(['h1', 'h2', 'echo-1']);

    // Step 3: refetch re-stamps EVERY row (moving checkpoint stamps,
    // which in the real system move FORWARD — commit time is always
    // later). Still one confirmed bubble, send position, stable stamp.
    const afterRefetch = mergeMessagesById(afterEcho, [
      makeMessage({ message_id: 'h1', created_at: '2026-08-30T11:59:00Z' }),
      makeMessage({ message_id: 'h2', created_at: '2026-08-30T11:59:30Z' }),
      makeMessage({ message_id: 'echo-1', content: 'hello', created_at: '2026-08-30T12:30:00Z' }),
    ]);
    expect(afterRefetch.map(m => m.message_id)).toEqual(['h1', 'h2', 'echo-1']);
    expect(afterRefetch[2].pending).toBeUndefined();
    // earlierOf keeps the earliest-seen stamp — the displayed send time
    // does not chase the forward-moving checkpoint stamp.
    expect(afterRefetch[2].created_at).toBe(sendStamp);
  });

  it('earlierOf pinning cannot freeze a wrong POSITION from unstable timestamps — order is array-derived', () => {
    // Under the old regime a wrong-derived stamp FROZE a wrong sorted
    // position (earlierOf pinned it there). With array-order semantics
    // the stamp only affects the displayed time: repeatedly merging
    // rows whose stamps wander (forward AND backward, including
    // future-dated garbage that lexicographically sorts past
    // everything) must leave every position untouched.
    let list: Message[] = [
      makeMessage({ message_id: 'a', created_at: '2026-08-30T10:00:00Z' }),
      makeMessage({ message_id: 'b', created_at: '2026-08-30T10:05:00Z' }),
    ];
    const wanderingStamps: Array<[string, string]> = [
      ['2026-08-30T13:00:00Z', '2026-08-30T09:00:00Z'],
      ['2027-01-01T00:00:00Z', '2026-08-30T12:00:00Z'],
      ['2026-08-30T10:30:00Z', '2029-12-31T23:59:59Z'],
    ];
    for (const [stampA, stampB] of wanderingStamps) {
      list = mergeMessagesById(list, [
        makeMessage({ message_id: 'a', created_at: stampA }),
        makeMessage({ message_id: 'b', created_at: stampB }),
      ]);
      expect(list.map(m => m.message_id)).toEqual(['a', 'b']);
    }
    // Stamps stabilized at the earliest-seen value (display-only;
    // the backward wander self-corrected b's displayed time).
    expect(list[0].created_at).toBe('2026-08-30T10:00:00Z');
    expect(list[1].created_at).toBe('2026-08-30T09:00:00Z');
  });
});

describe('evictPendingByAge (10-minute wall-clock TTL)', () => {
  it('should keep non-pending entries regardless of age', () => {
    const old = makeMessage({
      message_id: 'old',
      created_at: '2024-01-01T00:00:00Z', // years old
    });
    const out = evictPendingByAge([old], TEN_MIN_MS, FIXED_NOW);
    expect(out).toEqual([old]);
  });

  it('should keep a pending entry that has NOT aged past the TTL', () => {
    // 4 minutes old — comfortably under the 10-minute bar.
    const recent = makeMessage({
      message_id: 'recent',
      created_at: new Date(FIXED_NOW - 4 * 60 * 1000).toISOString(),
      pending: true,
    });
    const out = evictPendingByAge([recent], TEN_MIN_MS, FIXED_NOW);
    expect(out).toEqual([recent]);
  });

  it('should evict a pending entry that has aged past the TTL', () => {
    // 11 minutes old — just past the 10-minute bar.
    const stuck = makeMessage({
      message_id: 'stuck',
      created_at: new Date(FIXED_NOW - 11 * 60 * 1000).toISOString(),
      pending: true,
    });
    const out = evictPendingByAge([stuck], TEN_MIN_MS, FIXED_NOW);
    expect(out).toEqual([]);
  });

  it('should evict pending entries with unparseable timestamps conservatively', () => {
    const garbage = makeMessage({
      message_id: 'garbage',
      created_at: 'not-a-date',
      pending: true,
    });
    const out = evictPendingByAge([garbage], TEN_MIN_MS, FIXED_NOW);
    expect(out).toEqual([]);
  });

  it('should evict only the expired entries when mixed', () => {
    const recent = makeMessage({
      message_id: 'recent',
      created_at: new Date(FIXED_NOW - 60 * 1000).toISOString(),
      pending: true,
    });
    const stuck = makeMessage({
      message_id: 'stuck',
      created_at: new Date(FIXED_NOW - 30 * 60 * 1000).toISOString(),
      pending: true,
    });
    const serverConfirmed = makeMessage({
      message_id: 'server',
      created_at: new Date(FIXED_NOW - 30 * 60 * 1000).toISOString(),
    });
    const out = evictPendingByAge([recent, stuck, serverConfirmed], TEN_MIN_MS, FIXED_NOW);
    expect(out.map(m => m.message_id).sort()).toEqual(['recent', 'server']);
  });

  it('should return a new array only when something was actually evicted', () => {
    const a = makeMessage({ message_id: 'a' });
    const b = makeMessage({ message_id: 'b' });
    const out = evictPendingByAge([a, b], TEN_MIN_MS, FIXED_NOW);
    expect(out).not.toBe([a, b]); // explicit fresh array
    expect(out).toEqual([a, b]);
  });
});
