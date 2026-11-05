// The NEST-REPORTS domain model's spec (the nest-anomaly lane, phase 4).
//
// The load-bearing pins:
//
//   * the DOMAIN-LOCAL COPY TABLE is EXHAUSTIVE over the domain-only
//     outcomes — every class has exactly one row and every row says
//     something an operator can act on (the house "no silent
//     fall-through" rule);
//   * the DOMAIN-ONLY outcomes are NOT door classes — the door's
//     `unknown_family` copy ("this page is ahead of the daemon") and its
//     `registry_unavailable` copy ("no provider wired") are FALSE
//     statements for a cluster id and for the anomaly substrate, so the
//     frozen door union is NOT widened;
//   * the DECODER maps the route's closed vocabulary honestly — the six
//     reusable classes reuse the door's classes, the two domain-only
//     outcomes carry their own, and an unhandled answer never falls
//     through silently;
//   * the ALIAS the plan names (`AnomalyCluster`) is bound to the GROUP
//     shape the wire actually carries (phase 3's route serves groups).

import {
  AnomalyCluster,
  AnomalyClusterGroup,
  NestReportsErrorCode,
  NEST_REPORTS_DOMAIN_COPY,
  decodeNestReportsError,
} from './nest-reports.models';
import { MetricsDoorTimeoutError } from './metrics.model';

/** The route's error bodies — the shape FastAPI's HTTPException serves. */
function httpError(status: number, error: string): { status: number; error: { detail: { error: string } } } {
  return { status, error: { detail: { error } } };
}

describe('the nest-reports domain copy table', () => {
  it('covers EVERY domain-only outcome — no silent fall-through', () => {
    const domainOnly: readonly NestReportsErrorCode[] = [
      'cluster_not_found',
      'anomaly_unavailable',
      'unexpected',
    ];
    expect(Object.keys(NEST_REPORTS_DOMAIN_COPY).sort()).toEqual([...domainOnly].sort());
    for (const code of domainOnly) {
      const copy = NEST_REPORTS_DOMAIN_COPY[code];
      expect(copy).toBeDefined();
      expect(copy.cause.length).toBeGreaterThan(10);
      expect(copy.meaning.length).toBeGreaterThan(10);
      expect(copy.action.length).toBeGreaterThan(10);
      expect(typeof copy.retryable).toBe('boolean');
    }
  });

  it('the 404 copy names the cluster — never the metrics door', () => {
    const copy = NEST_REPORTS_DOMAIN_COPY.cluster_not_found;
    expect(copy.cause.toLowerCase()).toContain('cluster');
    expect(copy.cause.toLowerCase()).not.toContain('metrics door');
    expect(copy.cause.toLowerCase()).not.toContain('family');
    expect(copy.retryable).toBe(false);
  });

  it('the 503 copy names the anomaly store — never the metrics registry', () => {
    const copy = NEST_REPORTS_DOMAIN_COPY.anomaly_unavailable;
    expect(copy.cause.toLowerCase()).toContain('anomaly store');
    expect(copy.cause.toLowerCase()).not.toContain('metrics');
    expect(copy.cause.toLowerCase()).not.toContain('provider');
    // The substrate failure IS transient ⇒ the Retry affordance is honest.
    expect(copy.retryable).toBe(true);
  });

  it('the reusable door classes are NOT domain classes', () => {
    // The DOMAIN table carries ONLY the domain-only outcomes — the six
    // reusable classes are the door's own (the shared door-state panel
    // renders them; the frozen door union is never widened).
    for (const code of [
      'auth_not_configured',
      'scope_insufficient',
      'auth_required',
      'bad_request',
      'timeout',
      'network',
    ] as const) {
      expect(code in NEST_REPORTS_DOMAIN_COPY).toBe(false);
    }
  });
});

describe('the nest-reports error decoder', () => {
  it('maps the route\'s closed vocabulary (the plan\'s Task 3 table)', () => {
    expect(decodeNestReportsError(httpError(503, 'AUTH_NOT_CONFIGURED'))).toBe(
      'auth_not_configured',
    );
    expect(decodeNestReportsError(httpError(403, 'SCOPE_INSUFFICIENT'))).toBe(
      'scope_insufficient',
    );
    expect(decodeNestReportsError(httpError(401, 'unauthorized'))).toBe('auth_required');
    expect(decodeNestReportsError(httpError(400, 'bad_request'))).toBe('bad_request');
    expect(decodeNestReportsError(new MetricsDoorTimeoutError())).toBe('timeout');
    expect(decodeNestReportsError({ status: 0, error: {} })).toBe('network');
    expect(decodeNestReportsError(httpError(404, 'anomaly_cluster_not_found'))).toBe(
      'cluster_not_found',
    );
    expect(decodeNestReportsError(httpError(503, 'anomaly_unavailable'))).toBe(
      'anomaly_unavailable',
    );
  });

  it('an unhandled answer never falls through silently — it is `unexpected`', () => {
    expect(decodeNestReportsError(httpError(418, 'teapot'))).toBe('unexpected');
    expect(decodeNestReportsError(httpError(500, 'boom'))).toBe('unexpected');
    // A NON-RESPONSE is the transport never answering — the house
    // reading (the door's decoder reads an absent status as 0).
    expect(decodeNestReportsError({})).toBe('network');
    expect(decodeNestReportsError(null)).toBe('network');
  });

  it('the scope predicate is NARROWED (the house 403 rule)', () => {
    // Any OTHER 403 is not a scope failure — it is a foreign refusal,
    // and it must not wear the scope label.
    expect(decodeNestReportsError(httpError(403, 'CSRF'))).toBe('unexpected');
  });
});

describe('the nest-reports wire types', () => {
  it('the plan\'s `AnomalyCluster` name is bound to the GROUP shape', () => {
    // Phase 3's route serves GROUPS (one per tree root × category, the
    // AM-6c server-side grouping) — the plan's Task 3 names the type
    // `AnomalyCluster`; the alias keeps that name bound to the shape
    // the wire actually carries.
    const group: AnomalyCluster = {
      tree_root_instance_id: 'tree-1',
      category: 'tool-failure',
      severity: 'critical',
      severity_label: 'critical (peak)',
      severity_rank: 3,
      report_count: 4,
      occurrence_count: 2,
      cluster_ids: ['cl-1', 'cl-2'],
      last_report_at: '2026-11-05T00:00:00+00:00',
      open: true,
    };
    const typed: AnomalyClusterGroup = group;
    expect(typed.occurrence_count).toBe(2);
  });
});
