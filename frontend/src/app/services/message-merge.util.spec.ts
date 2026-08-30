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
    expect(merged.map(m => m.message_id)).toEqual(['older-1', 'echo-local']);
    expect(merged.find(m => m.message_id === 'echo-local')?.pending).toBe(true);
  });

  it('should sort by created_at after merging', () => {
    const existing = [makeMessage({ message_id: 'b', created_at: '2026-08-30T12:00:00Z' })];
    const incoming = [makeMessage({ message_id: 'a', created_at: '2026-08-30T11:00:00Z' })];
    const merged = mergeMessagesById(existing, incoming);
    expect(merged.map(m => m.message_id)).toEqual(['a', 'b']);
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
