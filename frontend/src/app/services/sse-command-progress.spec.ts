import { parseCommandProgressEvent } from './sse.service';
import type { CommandProgressEvent } from '../models';

/**
 * W2 logic-mirror spec — extracted from the ``command_progress`` listener
 * (sse.service.ts) so the wire-decoding rules have a Jest unit spec
 * without EventSource or Angular plumbing. House style: plain TS, no
 * TestBed, ``new``/import the function directly.
 *
 * Coverage targets (W2 acceptance):
 *   - envelope unwrap: LiveEventHub wraps the flat CommandProgressEvent
 *     inside ``data.message``; a missing ``message`` key returns ``null``
 *     (graceful drop);
 *   - field coercions: ``phase_seq`` / ``elapsed_ms`` go through
 *     ``Number(...)``; ``timestamp`` defaults to ``''``; ``eta_ms`` is
 *     only attached when numeric; ``detail`` is only attached when
 *     object-shaped;
 *   - per-instance staleness guard: drop events whose ``instance_id``
 *     disagrees with ``currentInstanceId``;
 *   - malformed-JSON swallow: never throws, returns ``null``;
 *   - ``phase_seq`` forwarded INTACT — the listener must not drop old
 *     seq values, so a stale-looking seq still surfaces to the
 *     CommandStateService (it owns the monotonic dedup).
 */

function makeWirePayload(overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({
    message: {
      instance_id: 'inst-1',
      command_id: 'cmd-1',
      phase: 'in_progress',
      phase_seq: 1,
      timestamp: '2026-08-31T00:00:05Z',
      elapsed_ms: 5000,
      ...overrides,
    },
  });
}

describe('parseCommandProgressEvent — envelope unwrap (LiveEventHub shape)', () => {
  it('unwraps the flat CommandProgressEvent from the envelope ``message`` field', () => {
    const data = makeWirePayload({
      phase: 'in_progress',
      phase_seq: 7,
      elapsed_ms: 32000,
      eta_ms: 28000,
      detail: { tokens_before: 120000 },
    });
    const event = parseCommandProgressEvent(data, 'inst-1');
    expect(event).not.toBeNull();
    expect(event).toMatchObject({
      instance_id: 'inst-1',
      command_id: 'cmd-1',
      phase: 'in_progress',
      phase_seq: 7,
      timestamp: '2026-08-31T00:00:05Z',
      elapsed_ms: 32000,
      eta_ms: 28000,
    });
    expect(event?.detail).toEqual({ tokens_before: 120000 });
  });

  it('a missing envelope ``message`` field returns null (graceful)', () => {
    const data = JSON.stringify({ no_message_key: true });
    expect(parseCommandProgressEvent(data, 'inst-1')).toBeNull();
  });

  it('a non-object ``message`` field returns null (defensive)', () => {
    const data = JSON.stringify({ message: 'string-not-object' });
    expect(parseCommandProgressEvent(data, 'inst-1')).toBeNull();
  });
});

describe('parseCommandProgressEvent — field coercions', () => {
  it('coerces phase_seq / elapsed_ms via Number()', () => {
    // String-encoded numbers are the canonical SSE-via-fastapi shape.
    const data = makeWirePayload({ phase_seq: '12', elapsed_ms: '9000' });
    const event = parseCommandProgressEvent(data, 'inst-1');
    expect(event?.phase_seq).toBe(12);
    expect(event?.elapsed_ms).toBe(9000);
  });

  it('defaults timestamp to empty string when absent', () => {
    const data = JSON.stringify({
      message: {
        instance_id: 'inst-1',
        command_id: 'cmd-1',
        phase: 'waiting',
        phase_seq: 0,
        elapsed_ms: 0,
      },
    });
    const event = parseCommandProgressEvent(data, 'inst-1');
    expect(event?.timestamp).toBe('');
  });

  it('omits eta_ms when not a number (advisory guard)', () => {
    const data = makeWirePayload({ eta_ms: 'soon' });
    const event = parseCommandProgressEvent(data, 'inst-1');
    expect(event?.eta_ms).toBeUndefined();
  });

  it('attaches eta_ms only when numeric', () => {
    const data = makeWirePayload({ eta_ms: 15000 });
    const event = parseCommandProgressEvent(data, 'inst-1');
    expect(event?.eta_ms).toBe(15000);
  });

  it('omits detail when not object-shaped (avoids leaking garbage)', () => {
    const data = makeWirePayload({ detail: 'not-an-object' });
    const event = parseCommandProgressEvent(data, 'inst-1');
    expect(event?.detail).toBeUndefined();
  });

  it('attaches detail only when object-shaped', () => {
    const data = makeWirePayload({
      detail: { compacted_type: 'summary', tokens_before: 100, tokens_after: 30 },
    });
    const event = parseCommandProgressEvent(data, 'inst-1');
    expect(event?.detail).toEqual({
      compacted_type: 'summary',
      tokens_before: 100,
      tokens_after: 30,
    });
  });
});

describe('parseCommandProgressEvent — per-instance staleness guard', () => {
  it('drops events whose instance_id disagrees with currentInstanceId', () => {
    const data = makeWirePayload({ instance_id: 'inst-OTHER' });
    expect(parseCommandProgressEvent(data, 'inst-1')).toBeNull();
  });

  it('forwards the event when instance_id matches', () => {
    const data = makeWirePayload({ instance_id: 'inst-1' });
    const event = parseCommandProgressEvent(data, 'inst-1');
    expect(event?.instance_id).toBe('inst-1');
  });

  it('drops events when the channel is detached (currentInstanceId=null)', () => {
    const data = makeWirePayload({ instance_id: 'inst-1' });
    expect(parseCommandProgressEvent(data, null)).toBeNull();
  });
});

describe('parseCommandProgressEvent — malformed-JSON swallow', () => {
  it('returns null for unparseable JSON, never throws', () => {
    expect(() => parseCommandProgressEvent('not json {{', 'inst-1')).not.toThrow();
    expect(parseCommandProgressEvent('not json {{', 'inst-1')).toBeNull();
  });

  it('returns null for empty / whitespace payload', () => {
    expect(parseCommandProgressEvent('', 'inst-1')).toBeNull();
    expect(parseCommandProgressEvent('   ', 'inst-1')).toBeNull();
  });

  it('returns null when the top-level value is not an object', () => {
    expect(parseCommandProgressEvent('"a string"', 'inst-1')).toBeNull();
    expect(parseCommandProgressEvent('42', 'inst-1')).toBeNull();
    expect(parseCommandProgressEvent('null', 'inst-1')).toBeNull();
  });
});

describe('parseCommandProgressEvent — phase_seq forwarded INTACT', () => {
  it('keeps a low phase_seq intact (the state machine owns monotonic dedup)', () => {
    // A seq value lower than what the consumer has already seen is NOT
    // dropped here — the CommandStateService handles that.
    const data = makeWirePayload({ phase_seq: 0 });
    const event = parseCommandProgressEvent(data, 'inst-1');
    expect(event?.phase_seq).toBe(0);
  });

  it('keeps a duplicated phase_seq intact (the state machine owns duplicate drop)', () => {
    const data = makeWirePayload({ phase_seq: 5 });
    const event = parseCommandProgressEvent(data, 'inst-1');
    expect(event?.phase_seq).toBe(5);
  });

  it('keeps a heart-beat seq+1 intact', () => {
    const data = makeWirePayload({ phase_seq: 6, elapsed_ms: 60000 });
    const event = parseCommandProgressEvent(data, 'inst-1');
    expect(event?.phase_seq).toBe(6);
    expect(event?.elapsed_ms).toBe(60000);
  });
});
