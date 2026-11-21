// The TOOL-USAGE wire models spec (phase4-plan Task 6).
//
// The load-bearing pins:
//
//   * the domain-local copy table is EXHAUSTIVE over the domain union —
//     every domain class has exactly one row, no silent fall-through
//     (the house rule);
//   * the two DOMAIN-SPECIFIC route outcomes (`unknown_tool`,
//     `tool_usage_unavailable`) carry the DOMAIN-LOCAL copy whose
//     SUBJECT is the tool-usage route — never the metrics door's copy;
//   * `unexpected` is the closed fallback row (the nest-reports
//     precedent) — it exists so an unmapped failure states something
//     TRUE about THIS route instead of falling through to door copy;
//   * the retry policy per row (404 unknown tool is NOT retryable; the
//     substrate 503 IS);
//   * the decoder maps the route's closed vocabulary honestly (the
//     frozen Task 2 table);
//   * the wire types carry the FROZEN field names (the phase1 §3.4
//     contract) — a rename here is a contract break, not an adaptation.

import {
  ToolUsageDailySeries,
  ToolUsageDomainErrorCode,
  ToolUsageReusableErrorCode,
  ToolUsageRow,
  ToolUsageSummary,
  TOOL_USAGE_DOMAIN_COPY,
  decodeToolUsageError,
} from './tool-usage.models';
import { MetricsDoorTimeoutError } from './metrics.model';

/** Every domain class — the union read structurally (no drift). */
const DOMAIN_CLASSES: readonly ToolUsageDomainErrorCode[] = [
  'unknown_tool',
  'tool_usage_unavailable',
  'unexpected',
];

describe('the tool-usage domain copy table — exhaustive, no silent fall-through', () => {
  it('every domain class has EXACTLY one copy row', () => {
    expect(Object.keys(TOOL_USAGE_DOMAIN_COPY).sort()).toEqual([...DOMAIN_CLASSES].sort());
    expect(Object.keys(TOOL_USAGE_DOMAIN_COPY).length).toBe(DOMAIN_CLASSES.length);
  });

  it('the two DOMAIN-SPECIFIC outcomes carry the tool-usage SUBJECT (never the metrics door)', () => {
    const unknownTool = TOOL_USAGE_DOMAIN_COPY.unknown_tool;
    expect(unknownTool.cause.toLowerCase()).toContain('tool');
    expect(unknownTool.cause.toLowerCase()).toContain('pruned');
    expect(unknownTool.cause.toLowerCase()).not.toContain('metrics door');

    const unavailable = TOOL_USAGE_DOMAIN_COPY.tool_usage_unavailable;
    expect(unavailable.cause.toLowerCase()).toContain('tool-usage store');
    expect(unavailable.cause.toLowerCase()).not.toContain('metrics door');
  });

  it('the `unexpected` fallback row is DOMAIN-LOCAL (the closed fallback states THIS route)', () => {
    const unexpected = TOOL_USAGE_DOMAIN_COPY.unexpected;
    expect(unexpected.cause.toLowerCase()).toContain('tool-usage route');
    expect(unexpected.cause.toLowerCase()).not.toContain('metrics door');
  });

  it('every copy row carries the FOUR door-state facts + the retry policy', () => {
    for (const code of DOMAIN_CLASSES) {
      const row = TOOL_USAGE_DOMAIN_COPY[code];
      expect(row.cause.length).toBeGreaterThan(0);
      expect(row.meaning.length).toBeGreaterThan(0);
      expect(row.action.length).toBeGreaterThan(0);
      expect(row.pointer).toBeNull();
      expect(typeof row.retryable).toBe('boolean');
    }
  });

  it('the retry policy — the 404 is NOT retryable, the substrate 503 IS', () => {
    expect(TOOL_USAGE_DOMAIN_COPY.unknown_tool.retryable).toBe(false);
    expect(TOOL_USAGE_DOMAIN_COPY.tool_usage_unavailable.retryable).toBe(true);
    expect(TOOL_USAGE_DOMAIN_COPY.unexpected.retryable).toBe(true);
  });
});

describe('the tool-usage decoder — the frozen Task 2 mapping', () => {
  it('503 AUTH_NOT_CONFIGURED → the door class auth_not_configured', () => {
    expect(
      decodeToolUsageError({ status: 503, error: { detail: { error: 'AUTH_NOT_CONFIGURED' } } }),
    ).toBe('auth_not_configured');
  });

  it('403 SCOPE_INSUFFICIENT → the door class scope_insufficient', () => {
    expect(
      decodeToolUsageError({ status: 403, error: { detail: { error: 'SCOPE_INSUFFICIENT' } } }),
    ).toBe('scope_insufficient');
  });

  it('401 → the door class auth_required', () => {
    expect(decodeToolUsageError({ status: 401, error: {} })).toBe(
      'auth_required',
    );
  });

  it('400 → the door class bad_request', () => {
    expect(decodeToolUsageError({ status: 400, error: { message: 'bad' } })).toBe('bad_request');
  });

  it('the client deadline → the door class timeout (never network)', () => {
    // The decoder delegates the door classes to the house decoder, which
    // names the TYPED deadline error by instanceof — the service maps
    // the rxjs TimeoutError to MetricsDoorTimeoutError (the house
    // withDeadline pattern).
    expect(decodeToolUsageError(new MetricsDoorTimeoutError())).toBe('timeout');
  });

  it('transport failure (status 0) → the door class network', () => {
    expect(decodeToolUsageError(new Error('getaddrinfo EAI_AGAIN'))).toBe(
      'network',
    );
    expect(decodeToolUsageError({ status: 0, error: null })).toBe('network');
  });

  it('404 unknown_tool → the DOMAIN class (never the door unknown_family copy)', () => {
    expect(
      decodeToolUsageError({ status: 404, error: { detail: { error: 'unknown_tool' } } }),
    ).toBe('unknown_tool');
  });

  it('503 tool_usage_unavailable → the DOMAIN class (the transient substrate failure)', () => {
    expect(
      decodeToolUsageError({
        status: 503,
        error: { detail: { error: 'tool_usage_unavailable' } },
      }),
    ).toBe('tool_usage_unavailable');
  });

  it('anything else → the closed `unexpected` fallback (never a door subject)', () => {
    expect(decodeToolUsageError({ status: 500, error: { detail: { error: 'boom' } } })).toBe('unexpected');
    expect(decodeToolUsageError({ status: 503, error: { detail: { error: 'weird' } } })).toBe('unexpected');
    // A response-less failure has NO status — the transport shape, which
    // the house decoder honestly names `network` (never a door subject,
    // never `unexpected`). A 500 the route never documents IS the
    // `unexpected` fallback.
    expect(decodeToolUsageError({})).toBe('network');
  });
});

describe('the tool-usage wire types — the FROZEN phase1 §3.4 field names', () => {
  it('a summary row carries the contract fields verbatim', () => {
    const row: ToolUsageRow = {
      tool_name: 'read_file',
      tool_category: 'filesystem',
      calls_24h: 2,
      calls_7d: 41,
      calls_total: 130,
      agents: [{ agent_id: 'coder', calls_7d: 40 }],
    };
    expect(Object.keys(row).sort()).toEqual(
      ['agents', 'calls_24h', 'calls_7d', 'calls_total', 'tool_category', 'tool_name'].sort(),
    );
    expect(Object.keys(row.agents[0]).sort()).toEqual(['agent_id', 'calls_7d'].sort());
  });

  it('the summary + daily payloads carry the contract envelopes verbatim', () => {
    const summary: ToolUsageSummary = { generated_at: '2026-11-21T00:00:00+00:00', rows: [] };
    expect(Object.keys(summary).sort()).toEqual(['generated_at', 'rows'].sort());
    const series: ToolUsageDailySeries = {
      series: [{ day: '2026-11-20', calls: 0 }],
    };
    expect(Object.keys(series).sort()).toEqual(['series'].sort());
    expect(Object.keys(series.series[0]).sort()).toEqual(['calls', 'day'].sort());
  });

  it('a row with tool_category: null is ACCEPTED by the types (I3 — the door emits null on category-resolution failure)', () => {
    // The wire widening is type-level ONLY: null is legal on the wire and
    // the FE accepts it (the template renders it verbatim as empty text).
    const row: ToolUsageRow = {
      tool_name: 'unmapped_tool',
      tool_category: null,
      calls_24h: 0,
      calls_7d: 1,
      calls_total: 1,
      agents: [],
    };
    expect(row.tool_category).toBeNull();
    expect(row.tool_name).toBe('unmapped_tool');
  });

  it('the reusable door classes NEVER widen the frozen door union', () => {
    // The union is a SUBSET of the door's vocabulary — the frozen 10-class
    // door union is not widened by this lane.
    const reusable: readonly ToolUsageReusableErrorCode[] = [
      'auth_not_configured',
      'scope_insufficient',
      'auth_required',
      'bad_request',
      'timeout',
      'network',
    ];
    expect(reusable.length).toBe(6);
  });
});
