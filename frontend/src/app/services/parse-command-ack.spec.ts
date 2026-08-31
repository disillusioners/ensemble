import {
  parseCommandAck,
  extractUnknownCommandError,
  UnknownCommandHttpError,
  isAcceptedCommandAck,
  ApiService,
} from './api.service';
import { of, throwError, Observable } from 'rxjs';
import { HttpClient } from '@angular/common/http';
import type { CommandAck, GetActiveResponse } from '../models';

/**
 * EXECUTABLE CONTRACT SPEC — phase2-plan.md Task 2/9 (architect §7, R6).
 *
 * ``parseCommandAck`` is THE single parsing point for the POST /messages
 * response; this suite encodes the pinned §7 CommandAck shape exactly. A
 * Phase 1 wire drift (renamed / missing field, changed discriminator)
 * fails HERE with a named expectation — that is the contract's enforcement
 * mechanism.
 *
 * Plain TS + manual Observable mocking, NO TestBed (house style).
 */

const ACK_TIMESTAMP = '2026-08-31T00:00:00.123456+00:00';

function fullAcceptedAck(overrides: Partial<CommandAck> = {}): CommandAck {
  return {
    status: 'command',
    command: 'compact',
    command_id: 'b3c1d0e2-4f5a-4b6c-8d9e-0f1a2b3c4d5e',
    state: 'accepted',
    reason: null,
    detail: null,
    timestamp: ACK_TIMESTAMP,
    ttl_seconds: 600,
    ...overrides,
  };
}

describe('parseCommandAck — message responses (all four existing statuses)', () => {
  it('202 ``injected`` body (RUNNING/WC injection acceptance) → message', () => {
    const body = {
      status: 'injected',
      instance_id: 'inst-1',
      content: 'hello',
      timestamp: '2026-08-31T00:00:00Z',
      created_at: '2026-08-31T00:00:00Z',
      pending_count: 1,
      message_id: 'echo-1',
    };
    const parsed = parseCommandAck(body);
    expect(parsed.kind).toBe('message');
    if (parsed.kind === 'message') {
      expect(parsed.message.message_id).toBe('echo-1');
    }
  });

  it('200 PAUSED auto-resume body (auto_resumed, NO status key) → message', () => {
    const body = {
      message_id: 'm-1',
      role: 'user',
      content: 'hello',
      thinking: null,
      thinking_extracted: null,
      tool_calls: null,
      images: null,
      created_at: '2026-08-31T00:00:00Z',
      auto_resumed: true,
      resume_info: { resumed: true, resumed_ids: ['inst-1'], skipped_ids: [], target_id: 'inst-1' },
    };
    const parsed = parseCommandAck(body);
    expect(parsed.kind).toBe('message');
    if (parsed.kind === 'message') {
      expect(parsed.message.message_id).toBe('m-1');
    }
  });

  it('200 IDLE/terminal enqueue body (queued flag, NO status key) → message', () => {
    const body = {
      message_id: 'm-2',
      role: 'user',
      content: 'hello',
      thinking: null,
      thinking_extracted: null,
      tool_calls: null,
      images: null,
      created_at: '2026-08-31T00:00:00Z',
      queued: true,
      auto_resumed: false,
      resume_info: null,
    };
    const parsed = parseCommandAck(body);
    expect(parsed.kind).toBe('message');
    if (parsed.kind === 'message') {
      expect(parsed.message.queued).toBe(true);
    }
  });

  it('legacy 200 enqueue body WITHOUT message_id (old backend) → message', () => {
    const body = { role: 'user', content: 'hello', timestamp: '2026-08-31T00:00:00Z' };
    const parsed = parseCommandAck(body);
    expect(parsed.kind).toBe('message');
  });

  it.each([null, undefined])('defensive: %p body → message (never throws)', (body) => {
    expect(() => parseCommandAck(body)).not.toThrow();
    expect(parseCommandAck(body).kind).toBe('message');
  });
});

describe('parseCommandAck — the pinned §7 CommandAck, field by field', () => {
  it('accepted ack: every pinned field survives the adapter untouched', () => {
    const ack = fullAcceptedAck();
    const parsed = parseCommandAck(ack);
    expect(parsed.kind).toBe('command');
    if (parsed.kind !== 'command') return;
    // §7 verbatim assertions — one per pinned field:
    expect(parsed.ack.status).toBe('command');
    expect(parsed.ack.command).toBe('compact');
    expect(parsed.ack.command_id).toBe('b3c1d0e2-4f5a-4b6c-8d9e-0f1a2b3c4d5e');
    expect(parsed.ack.state).toBe('accepted');
    expect(parsed.ack.timestamp).toBe(ACK_TIMESTAMP);
    expect(parsed.ack.ttl_seconds).toBe(600);
  });

  it('discrimination key is EXACTLY status === "command"', () => {
    // A message body that merely carries some status must stay a message.
    const nearMiss = parseCommandAck({ status: 'injected', message_id: 'x' });
    expect(nearMiss.kind).toBe('message');
    // The command discriminator routes even a minimal ack.
    expect(parseCommandAck({ status: 'command' }).kind).toBe('command');
  });

  it.each([
    'terminal_instance',
    'busy',
    'rate_limited',
    'pending_injections',
    'compaction_disabled',
    'quiescence_timeout',
  ] as const)('rejected ack with reason %s parses with detail + ttl intact', (reason) => {
    const ack = fullAcceptedAck({
      state: 'rejected',
      command_id: null, // BE ships null on rejections (adapter must absorb)
      reason,
      detail: reason === 'terminal_instance'
        ? 'Send a message to start a new turn, then /compact.'
        : 'refused',
    });
    const parsed = parseCommandAck(ack);
    expect(parsed.kind).toBe('command');
    if (parsed.kind !== 'command') return;
    expect(parsed.ack.state).toBe('rejected');
    expect(parsed.ack.reason).toBe(reason);
    expect(typeof parsed.ack.detail).toBe('string');
    expect(parsed.ack.ttl_seconds).toBe(600);
    // Rejections never seed the machine — the accepted-guard is exact.
    expect(isAcceptedCommandAck(parsed.ack)).toBe(false);
  });

  it('isAcceptedCommandAck guards the machine against null/empty command_id', () => {
    expect(isAcceptedCommandAck(fullAcceptedAck())).toBe(true);
    expect(isAcceptedCommandAck(fullAcceptedAck({ command_id: null }))).toBe(false);
    expect(isAcceptedCommandAck(fullAcceptedAck({ command_id: '' }))).toBe(false);
    expect(isAcceptedCommandAck(fullAcceptedAck({ state: 'rejected' }))).toBe(false);
  });
});

describe('HTTP 400 UNKNOWN_COMMAND → typed error (§7 split rule / O13)', () => {
  const makeErr = (body: unknown, status = 400) => ({ status, error: body });

  it('maps the FastAPI-wrapped ErrorResponse envelope to UnknownCommandHttpError', () => {
    const err = makeErr({
      detail: {
        code: 'UNKNOWN_COMMAND',
        message: 'Unknown command: /frobnicate',
        details: { available: ['compact'] },
      },
    });
    const typed = extractUnknownCommandError(err);
    expect(typed).toBeInstanceOf(UnknownCommandHttpError);
    expect(typed?.code).toBe('UNKNOWN_COMMAND');
    expect(typed?.available).toEqual(['compact']);
    expect(typed?.serverMessage).toBe('Unknown command: /frobnicate');
    expect(typed?.message).toBe('Unknown command: /frobnicate');
  });

  it('non-400 statuses and other codes pass through as null (rethrow unchanged)', () => {
    expect(extractUnknownCommandError(makeErr({ detail: { code: 'UNKNOWN_COMMAND' } }, 500))).toBeNull();
    expect(extractUnknownCommandError(makeErr({ detail: { code: 'INVALID_REQUEST' } }))).toBeNull();
    expect(extractUnknownCommandError(makeErr({ detail: null }))).toBeNull();
    expect(extractUnknownCommandError({ error: {} })).toBeNull();
    expect(extractUnknownCommandError(null)).toBeNull();
    expect(extractUnknownCommandError(new Error('plain'))).toBeNull();
  });

  it('missing / malformed details.available degrades to an empty list', () => {
    const noDetails = extractUnknownCommandError(makeErr({ detail: { code: 'UNKNOWN_COMMAND', message: 'x' } }));
    expect(noDetails?.available).toEqual([]);
    const junk = extractUnknownCommandError(makeErr({
      detail: { code: 'UNKNOWN_COMMAND', details: { available: [1, 'compact', null] } },
    }));
    expect(junk?.available).toEqual(['compact']);
  });

  it('is IDEMPOTENT — an already-typed error passes through (e2e regression: double mapping)', () => {
    const typed = new UnknownCommandHttpError(['compact'], 'Unknown command: /compact');
    expect(extractUnknownCommandError(typed)).toBe(typed);
  });
});

describe('getActiveCommand — never throws (swallowed-error convention)', () => {
  /** Build an ApiService whose HttpClient.get returns the given real
   *  Observable — manual mocking per house style, so the service's own
   *  pipe(catchError → null) contract is what's under test. */
  function serviceReturning(observable: Observable<GetActiveResponse | null>): ApiService {
    const http = { get: () => observable } as unknown as HttpClient;
    return new ApiService(http);
  }

  it('resolves {exists:true, command} verbatim on success', async () => {
    const command = {
      instance_id: 'inst-1', command_id: 'cmd-1', phase: 'in_progress',
      phase_seq: 3, timestamp: '2026-08-31T00:00:03Z', elapsed_ms: 3000,
    } as const;
    const service = serviceReturning(
      of({ exists: true, command } as GetActiveResponse),
    );
    const result = await service.getActiveCommand('inst-1');
    expect(result).toEqual({ exists: true, command });
  });

  it('resolves {exists:false} verbatim (authoritative silent-clear signal)', async () => {
    const service = serviceReturning(of({ exists: false } as GetActiveResponse));
    await expect(service.getActiveCommand('inst-1')).resolves.toEqual({ exists: false });
  });

  it('network / HTTP error → resolves NULL (never rejects; caller keeps card + keeps polling)', async () => {
    const consoleSpy = jest.spyOn(console, 'error').mockImplementation(() => {});
    try {
      const service = serviceReturning(throwError(() => new Error('network down')));
      await expect(service.getActiveCommand('inst-1')).resolves.toBeNull();
    } finally {
      consoleSpy.mockRestore();
    }
  });
});
