import {
  buildSseErrorEventRow,
  buildSseStatusErrorRow,
  SSE_ERROR_DETAIL_MAX_CHARS,
  truncateSseErrorDetail,
} from './sse.service';
import { mergeMessagesById } from './message-merge.util';
import type { Message } from '../models';

/**
 * Logic-mirror spec for the SSE transcript error rows (D2 gap fix,
 * 2026-09-14) — extracted as pure builders in ``sse.service.ts`` so the
 * wire-decoding rules have a Jest unit spec without EventSource or
 * Angular plumbing. House style: plain TS, no TestBed, import the REAL
 * functions directly (``sse-command-progress.spec.ts`` W2 pattern).
 *
 * Wire shapes pinned from the daemon (read-only reference):
 *   - hub ``error`` lane — LiveEventHub.stream_error (sole caller
 *     instance_messaging.py streaming except-branch):
 *       ``{"instance_id", "event_type": "error",
 *          "error": {"error": <str>, "stage": "streaming",
 *                    "message_id": <uuid>}}``  (``error`` is a DICT)
 *   - shutdown ``error`` lane — messages.py SSE generator:
 *       ``{"error": "server_shutdown"}``  (``error`` is a bare STRING,
 *       NO ``instance_id``)
 *   - ``status_change{error}`` — LiveEventHub.stream_status_change
 *     (e.g. error_reporting.py child-error path):
 *       ``{"instance_id", "event_type": "status_change",
 *          "status": "error", "agent_id"?}``  (NO detail text)
 *
 * Coverage targets:
 *   - both ``error`` wire shapes decode to a visible system row with
 *     human detail (never "[object Object]");
 *   - per-instance staleness guard (cross-instance / detached channel);
 *   - 500-char detail truncation;
 *   - idempotency: identical error payloads mint the SAME row id and
 *     collapse through ``mergeMessagesById`` (the cross-seam invariant —
 *     duplicate delivery / replay can never duplicate a row);
 *   - status lane ids are occurrence-distinct via the caller-supplied seq;
 *   - non-error statuses / malformed payloads → null (graceful drop);
 *   - production-source identity pins: the visibility exception and the
 *     template branch exist verbatim in the component source (plain-TS
 *     specs cannot render the template — the grep pins are the
 *     MIRROR-PRODUCTION IDORITY guard against silent drift).
 */

const INSTANCE_A = '11111111-1111-1111-1111-111111111111';
const INSTANCE_B = '22222222-2222-2222-2222-222222222222';

/** Hub-lane error payload (dict ``error`` field with stage + message_id). */
function makeHubErrorPayload(
  overrides: Record<string, unknown> = {},
  errorOverrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    instance_id: INSTANCE_A,
    event_type: 'error',
    error: {
      error: 'LLM provider returned empty response',
      stage: 'streaming',
      message_id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
      ...errorOverrides,
    },
    ...overrides,
  };
}

/** Shutdown-lane error payload (bare string ``error``, no instance_id). */
function makeShutdownPayload(): Record<string, unknown> {
  return { error: 'server_shutdown' };
}

/** status_change{error} payload (no detail text on the wire). */
function makeStatusErrorPayload(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    instance_id: INSTANCE_A,
    event_type: 'status_change',
    status: 'error',
    agent_id: 'worker',
    ...overrides,
  };
}

describe('buildSseErrorEventRow — hub lane (dict error field)', () => {
  it('decodes the dict error field into title + truncated detail', () => {
    const row = buildSseErrorEventRow(makeHubErrorPayload(), INSTANCE_A);
    expect(row).not.toBeNull();
    expect(row!.role).toBe('system');
    expect(row!.instance_id).toBe(INSTANCE_A);
    expect(row!.sseError).toBeDefined();
    expect(row!.sseError!.source).toBe('error');
    expect(row!.sseError!.title).toBe('Message processing failed (streaming)');
    expect(row!.sseError!.stage).toBe('streaming');
    expect(row!.content).toBe('LLM provider returned empty response');
  });

  it('mints a content-deterministic id seeded by message_id + detail', () => {
    const a = buildSseErrorEventRow(makeHubErrorPayload(), INSTANCE_A);
    const b = buildSseErrorEventRow(makeHubErrorPayload(), INSTANCE_A);
    expect(a!.message_id).toBe(b!.message_id);
    expect(a!.message_id).toContain(INSTANCE_A);
    expect(a!.message_id).toContain('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa');
  });

  it('distinct errors on the same message_id stay distinct rows', () => {
    const a = buildSseErrorEventRow(makeHubErrorPayload(), INSTANCE_A);
    const b = buildSseErrorEventRow(
      makeHubErrorPayload({}, { error: 'different failure' }),
      INSTANCE_A,
    );
    expect(a!.message_id).not.toBe(b!.message_id);
  });

  it('title drops the stage suffix when stage is absent', () => {
    const row = buildSseErrorEventRow(
      makeHubErrorPayload({}, { stage: undefined }),
      INSTANCE_A,
    );
    expect(row!.sseError!.title).toBe('Message processing failed');
    expect(row!.sseError!.stage).toBeNull();
  });

  it('keeps the (streaming) title for a non-streaming stage string', () => {
    const row = buildSseErrorEventRow(
      makeHubErrorPayload({}, { stage: 'persist' }),
      INSTANCE_A,
    );
    expect(row!.sseError!.title).toBe('Message processing failed (persist)');
  });
});

describe('buildSseErrorEventRow — shutdown lane (string error field)', () => {
  it('decodes the bare-string error and falls back to the channel instance', () => {
    const row = buildSseErrorEventRow(makeShutdownPayload(), INSTANCE_A);
    expect(row).not.toBeNull();
    expect(row!.instance_id).toBe(INSTANCE_A);
    expect(row!.sseError!.title).toBe('Message processing failed');
    expect(row!.content).toBe('server_shutdown');
  });

  it('drops the row when neither payload nor channel carries an instance', () => {
    const row = buildSseErrorEventRow(makeShutdownPayload(), null);
    expect(row).toBeNull();
  });
});

describe('buildSseErrorEventRow — staleness + malformed guards', () => {
  it('drops cross-instance events (wrong channel)', () => {
    const row = buildSseErrorEventRow(
      makeHubErrorPayload({ instance_id: INSTANCE_B }),
      INSTANCE_A,
    );
    expect(row).toBeNull();
  });

  it('drops non-object payloads', () => {
    expect(buildSseErrorEventRow(null, INSTANCE_A)).toBeNull();
    expect(buildSseErrorEventRow('error', INSTANCE_A)).toBeNull();
    expect(buildSseErrorEventRow(42, INSTANCE_A)).toBeNull();
  });

  it('never renders "[object Object]" for an unrecognized dict shape', () => {
    const row = buildSseErrorEventRow(
      makeHubErrorPayload({}, { error: undefined }),
      INSTANCE_A,
    );
    expect(row).not.toBeNull();
    // Falls through extractSseErrorText's string checks → JSON fallback.
    expect(row!.content).not.toContain('[object Object]');
  });
});

describe('buildSseErrorEventRow — detail truncation', () => {
  it('keeps details at or under the cap intact', () => {
    const short = 'x'.repeat(SSE_ERROR_DETAIL_MAX_CHARS);
    const row = buildSseErrorEventRow(
      makeHubErrorPayload({}, { error: short }),
      INSTANCE_A,
    );
    expect(row!.content).toBe(short);
  });

  it('truncates over-cap details to the cap + ellipsis', () => {
    const long = 'y'.repeat(SSE_ERROR_DETAIL_MAX_CHARS + 5000);
    const row = buildSseErrorEventRow(
      makeHubErrorPayload({}, { error: long }),
      INSTANCE_A,
    );
    expect(row!.content).toBe(
      'y'.repeat(SSE_ERROR_DETAIL_MAX_CHARS) + '…',
    );
    expect(row!.content.length).toBe(SSE_ERROR_DETAIL_MAX_CHARS + 1);
  });

  it('truncateSseErrorDetail is a no-op under the cap', () => {
    expect(truncateSseErrorDetail('abc')).toBe('abc');
    expect(truncateSseErrorDetail('')).toBe('');
  });
});

describe('buildSseStatusErrorRow', () => {
  it('decodes status_change{error} into a title-only row', () => {
    const row = buildSseStatusErrorRow(makeStatusErrorPayload(), INSTANCE_A, 1);
    expect(row).not.toBeNull();
    expect(row!.role).toBe('system');
    expect(row!.instance_id).toBe(INSTANCE_A);
    expect(row!.sseError!.source).toBe('status_change');
    expect(row!.sseError!.title).toBe('Instance entered error state');
    expect(row!.content).toBe('');
  });

  it('embeds the seq so genuine repeat occurrences stay distinct', () => {
    const first = buildSseStatusErrorRow(makeStatusErrorPayload(), INSTANCE_A, 1);
    const second = buildSseStatusErrorRow(makeStatusErrorPayload(), INSTANCE_A, 2);
    expect(first!.message_id).not.toBe(second!.message_id);
    expect(first!.message_id).toContain('sse-status-error:');
    // Same seq → same id (idempotent for a same-occurrence rebuild).
    expect(first!.message_id).toBe(
      buildSseStatusErrorRow(makeStatusErrorPayload(), INSTANCE_A, 1)!.message_id,
    );
  });

  it('returns null for non-error statuses (running / completed / paused)', () => {
    for (const status of ['running', 'completed', 'paused', 'waiting_children']) {
      expect(
        buildSseStatusErrorRow(makeStatusErrorPayload({ status }), INSTANCE_A, 1),
      ).toBeNull();
    }
  });

  it('drops cross-instance events and non-object payloads', () => {
    expect(
      buildSseStatusErrorRow(
        makeStatusErrorPayload({ instance_id: INSTANCE_B }),
        INSTANCE_A,
        1,
      ),
    ).toBeNull();
    expect(buildSseStatusErrorRow(null, INSTANCE_A, 1)).toBeNull();
    // Detached channel + payload instance present → row still placed.
    expect(
      buildSseStatusErrorRow(makeStatusErrorPayload(), null, 1),
    ).not.toBeNull();
  });
});

describe('cross-seam invariant — rows merge idempotently through mergeMessagesById', () => {
  it('a duplicate error delivery collapses onto one row', () => {
    const row = buildSseErrorEventRow(makeHubErrorPayload(), INSTANCE_A)!;
    const once = mergeMessagesById([], [row]);
    const twice = mergeMessagesById(once, [row]);
    expect(twice.length).toBe(1);
    expect(twice[0].message_id).toBe(row.message_id);
  });

  it('error rows survive a merge-mode REST refetch (local-only preservation)', () => {
    const row = buildSseErrorEventRow(makeHubErrorPayload(), INSTANCE_A)!;
    const withError = mergeMessagesById([], [row]);
    // Refetch lands server rows that do NOT contain the synthetic row.
    const serverRow: Message = {
      message_id: 'server-1',
      role: 'assistant',
      content: 'hello',
      created_at: '2026-09-14T00:00:00Z',
      instance_id: INSTANCE_A,
    };
    const merged = mergeMessagesById(withError, [serverRow]);
    expect(merged.some(m => m.message_id === row.message_id)).toBe(true);
    expect(merged.some(m => m.message_id === 'server-1')).toBe(true);
    // New rows append at the END (arrival order) — error row keeps its
    // original position; no created_at re-sort.
    expect(merged[merged.length - 1].message_id).toBe('server-1');
  });

  it('status rows and error rows coexist as distinct rows', () => {
    const errRow = buildSseErrorEventRow(makeHubErrorPayload(), INSTANCE_A)!;
    const statusRow = buildSseStatusErrorRow(
      makeStatusErrorPayload(),
      INSTANCE_A,
      1,
    )!;
    const merged = mergeMessagesById([], [errRow, statusRow]);
    expect(merged.length).toBe(2);
  });
});

// ── Production-source identity pins ─────────────────────────────────────────
// Plain-TS specs cannot render the Angular template or run component
// methods, so the render-side wiring is pinned the repo-sanctioned way:
// assert the production source contains the exact predicate/branch text
// verbatim (MIRROR-PRODUCTION IDENTITY — a green suite must not certify
// a template the production no longer contains).

interface FsLike {
  readFileSync(p: string, enc: string): string;
}
interface PathLike {
  resolve(...parts: string[]): string;
  join(...parts: string[]): string;
}
const nodeRequire = eval('require') as (id: string) => unknown;
const fs = nodeRequire('fs') as FsLike;
const path = nodeRequire('path') as PathLike;
declare const __dirname: string;
const FRONTEND_ROOT = path.resolve(__dirname, '..', '..', '..');

function readSource(rel: string): string {
  return fs.readFileSync(path.join(FRONTEND_ROOT, rel), 'utf8');
}

describe('production-source identity pins (render-side wiring)', () => {
  it('visibility exception: sseError rows bypass the system-prompt toggle', () => {
    const src = readSource(
      'src/app/components/chat-interface/chat-interface.component.ts',
    );
    expect(src).toContain('if (message.sseError) {');
    expect(src).toContain('return true;');
  });

  it('template branch: the sse-error-row card renders title + detail', () => {
    const src = readSource('src/app/components/chat-interface/chat-interface.html');
    expect(src).toContain('@if (message.sseError) {');
    expect(src).toContain('data-testid="sse-error-row"');
    expect(src).toContain('{{ message.sseError.title }}');
    expect(src).toContain('class="sse-error-detail"');
  });

  it('listener wiring: both SSE listeners upsert transcript rows', () => {
    const src = readSource('src/app/services/sse.service.ts');
    expect(src).toContain('buildSseErrorEventRow(data, this.currentInstanceId)');
    expect(src).toContain('buildSseStatusErrorRow(');
    expect(src).toContain("data.status === 'error'");
  });
});
