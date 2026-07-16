import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { SkillService } from './skill.service';
import {
  SkillAbTestStats,
  SkillAbTestStatsResponse,
  SkillTrigger,
  SkillUsageRecord,
  SkillUsageRecordsResponse,
} from '../models/skill.model';

/**
 * Tests for the Phase 2 service surface additions.
 *
 * Scope: the six new methods introduced for skill-evolution-ui
 * (usage records, A/B comparison stats, triggers CRUD). The existing
 * list / get / create / update / delete / deactivate / shareToGlobal
 * surface is exercised by older callers; this spec only covers the
 * newly added methods so the contracts they expose (URL,
 * HTTP method, query params, envelope handling, error propagation)
 * are pinned down while the rest of the suite stays focused.
 *
 * Pattern mirrors ``settings.service.spec.ts`` — ``provideHttpClient``
 * + ``provideHttpClientTesting`` + ``HttpTestingController`` so each
 * test asserts on the exact HTTP wire shape the real backend will see
 * (URL, method, query params, body) without standing up the FastAPI
 * server.
 */

// ── Factories ─────────────────────────────────────────────────────────────

/**
 * Build a single ``SkillUsageRecord`` suitable for use as a fixture
 * in the ``getUsageRecords`` tests. Keeps the fixture local — the
 * ``id`` / ``skill_id`` chains are long enough that copy-paste would
 * fat-finger a digit and the failure would look like a field-name
 * mismatch instead of a copy typo.
 */
function makeUsageRecord(overrides: Partial<SkillUsageRecord> = {}): SkillUsageRecord {
  return {
    id: 'record-uuid-1',
    skill_id: 'skill-uuid-1',
    project_id: 'project-uuid-1',
    instance_id: 'instance-uuid-1',
    agent_id: 'developer',
    task_message: 'Refactor the auth module',
    selected: true,
    applied: true,
    task_succeeded: true,
    iterations: 3,
    duration_seconds: 12.5,
    fallback: false,
    feedback_applied: true,
    feedback_note: null,
    ab_test_group: null,
    superseded: false,
    created_at: '2026-07-16T10:00:00.000Z',
    ...overrides,
  };
}

function makeUsageRecordsResponse(
  overrides: Partial<SkillUsageRecordsResponse> = {}
): SkillUsageRecordsResponse {
  return {
    skill_id: 'skill-uuid-1',
    records: [makeUsageRecord()],
    total: 42,
    limit: 50,
    offset: 0,
    ...overrides,
  };
}

/**
 * Full A/B stats block — every field populated so the test can
 * detect accidental key renames or drops in the ``SkillAbTestStats``
 * interface.
 */
function makeAbStats(overrides: Partial<SkillAbTestStats> = {}): SkillAbTestStats {
  return {
    skill_id_a: 'skill-a',
    skill_id_b: 'skill-b',
    completion_rate_a: 0.82,
    completion_rate_b: 0.91,
    applied_rate_a: 0.7,
    applied_rate_b: 0.85,
    fallback_rate_a: 0.05,
    fallback_rate_b: 0.02,
    avg_iterations_a: 4.1,
    avg_iterations_b: 3.7,
    avg_duration_a: 12.0,
    avg_duration_b: 9.5,
    composite_score_a: 0.78,
    composite_score_b: 0.86,
    difference: 0.08,
    comparisons: 96,
    extension_count: 0,
    sample_size: 100,
    ready_to_resolve: false,
    needs_more_data: true,
    ...overrides,
  };
}

function makeAbTestStatsResponse(
  overrides: Partial<SkillAbTestStatsResponse> = {}
): SkillAbTestStatsResponse {
  return {
    skill_id: 'skill-uuid-1',
    ab_test_group: 'group-A',
    stats: makeAbStats(),
    ...overrides,
  };
}

function makeTrigger(overrides: Partial<SkillTrigger> = {}): SkillTrigger {
  return {
    id: 'trigger-uuid-1',
    project_id: null,
    name: 'Deploy skill on deploy keyword',
    condition_type: 'keyword',
    condition_json: { keyword: 'deploy' },
    action: 'inject:refactor',
    is_enabled: true,
    created_at: '2026-07-16T09:00:00.000Z',
    ...overrides,
  };
}

// ── Suite ─────────────────────────────────────────────────────────────────

describe('SkillService', () => {
  let service: SkillService;
  let httpTesting: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(SkillService);
    httpTesting = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    // Fails the test if any request was issued but never intercepted
    // (e.g. a typo in a URL inside a service method).
    httpTesting.verify();
  });

  // ── getUsageRecords ─────────────────────────────────────────────────────

  describe('getUsageRecords', () => {
    it('should GET /api/skills/{id}/usage-records with no query params when called with defaults', (done) => {
      const mockResponse = makeUsageRecordsResponse();

      service.getUsageRecords('skill-uuid-1').subscribe({
        next: (result) => {
          expect(result).toEqual(mockResponse);
          expect(result.records).toHaveLength(1);
          expect(result.total).toBe(42);
          done();
        },
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'GET' &&
          r.url === '/api/skills/skill-uuid-1/usage-records' &&
          r.params.keys().length === 0
      );
      expect(req.request.body).toBeNull();
      req.flush(mockResponse);
    });

    it('should forward limit and offset as query params', (done) => {
      service.getUsageRecords('skill-uuid-1', 100, 200).subscribe({
        next: () => done(),
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'GET' &&
          r.url === '/api/skills/skill-uuid-1/usage-records' &&
          r.params.get('limit') === '100' &&
          r.params.get('offset') === '200'
      );
      req.flush(makeUsageRecordsResponse({ limit: 100, offset: 200, total: 350 }));
    });

    it('should forward only limit when offset is undefined', (done) => {
      service.getUsageRecords('skill-uuid-1', 25).subscribe({
        next: () => done(),
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'GET' &&
          r.url === '/api/skills/skill-uuid-1/usage-records' &&
          r.params.get('limit') === '25' &&
          !r.params.has('offset')
      );
      req.flush(makeUsageRecordsResponse({ limit: 25 }));
    });

    it('should forward only offset when limit is undefined', (done) => {
      service.getUsageRecords('skill-uuid-1', undefined, 75).subscribe({
        next: () => done(),
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'GET' &&
          r.url === '/api/skills/skill-uuid-1/usage-records' &&
          !r.params.has('limit') &&
          r.params.get('offset') === '75'
      );
      req.flush(makeUsageRecordsResponse({ offset: 75, limit: 50 }));
    });

    it('should URL-encode the skill id', (done) => {
      // Catches accidental reliance on a string template that forgets
      // encodeURIComponent — this would otherwise send a request to
      // a different REST resource.
      service.getUsageRecords('skill/with spaces').subscribe({
        next: () => done(),
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'GET' &&
          r.url === '/api/skills/skill%2Fwith%20spaces/usage-records'
      );
      req.flush(makeUsageRecordsResponse());
    });

    it('should propagate a 404 from the backend', (done) => {
      service.getUsageRecords('skill-uuid-1').subscribe({
        next: () => done.fail('expected error'),
        error: (err) => {
          expect(err.status).toBe(404);
          done();
        },
      });

      const req = httpTesting.expectOne(
        (r) => r.method === 'GET' && r.url === '/api/skills/skill-uuid-1/usage-records'
      );
      req.flush('Skill not found', { status: 404, statusText: 'Not Found' });
    });
  });

  // ── getAbTestStats ──────────────────────────────────────────────────────

  describe('getAbTestStats', () => {
    it('should GET /api/skills/{id}/ab-test/stats and return the response', (done) => {
      const mockResponse = makeAbTestStatsResponse();

      service.getAbTestStats('skill-uuid-1').subscribe({
        next: (result) => {
          expect(result).toEqual(mockResponse);
          expect(result.ab_test_group).toBe('group-A');
          expect(result.stats?.composite_score_a).toBe(0.78);
          done();
        },
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'GET' &&
          r.url === '/api/skills/skill-uuid-1/ab-test/stats'
      );
      expect(req.request.body).toBeNull();
      req.flush(mockResponse);
    });

    it("should pass through the ''no test'' envelope (ab_test_group null, stats null)", (done) => {
      const mockResponse: SkillAbTestStatsResponse = {
        skill_id: 'skill-uuid-1',
        ab_test_group: null,
        stats: null,
      };

      service.getAbTestStats('skill-uuid-1').subscribe({
        next: (result) => {
          expect(result.ab_test_group).toBeNull();
          expect(result.stats).toBeNull();
          done();
        },
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) => r.url === '/api/skills/skill-uuid-1/ab-test/stats'
      );
      req.flush(mockResponse);
    });

    it('should propagate a 500 from the backend', (done) => {
      service.getAbTestStats('skill-uuid-1').subscribe({
        next: () => done.fail('expected error'),
        error: (err) => {
          expect(err.status).toBe(500);
          done();
        },
      });

      const req = httpTesting.expectOne(
        (r) => r.url === '/api/skills/skill-uuid-1/ab-test/stats'
      );
      req.flush('Server error', { status: 500, statusText: 'Server Error' });
    });
  });

  // ── listTriggers ────────────────────────────────────────────────────────

  describe('listTriggers', () => {
    it('should default enabled_only=true when called with no arguments', (done) => {
      const mockTriggers: SkillTrigger[] = [makeTrigger()];

      service.listTriggers().subscribe({
        next: (result) => {
          expect(result).toEqual(mockTriggers);
          expect(result).toHaveLength(1);
          done();
        },
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'GET' &&
          r.url === '/api/skills/triggers' &&
          r.params.get('enabled_only') === 'true' &&
          !r.params.has('project_id')
      );
      req.flush({ items: mockTriggers });
    });

    it('should forward projectId when supplied', (done) => {
      service.listTriggers('project-uuid-1').subscribe({
        next: () => done(),
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'GET' &&
          r.url === '/api/skills/triggers' &&
          r.params.get('project_id') === 'project-uuid-1' &&
          r.params.get('enabled_only') === 'true'
      );
      req.flush({ items: [] });
    });

    it('should serialise enabledOnly=false as the literal token (not bare key)', (done) => {
      // FastAPI ``bool`` Query coercion relies on ``enabled_only=false``,
      // not ``enabled_only`` (bare) — the latter would 422.
      service.listTriggers('project-uuid-1', false).subscribe({
        next: () => done(),
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'GET' &&
          r.url === '/api/skills/triggers' &&
          r.params.get('project_id') === 'project-uuid-1' &&
          r.params.get('enabled_only') === 'false'
      );
      req.flush({ items: [] });
    });

    it('should accept an array response (no envelope)', (done) => {
      const triggers = [makeTrigger({ id: 'trigger-1' }), makeTrigger({ id: 'trigger-2' })];

      service.listTriggers().subscribe({
        next: (result) => {
          expect(result).toHaveLength(2);
          expect(result[0].id).toBe('trigger-1');
          expect(result[1].id).toBe('trigger-2');
          done();
        },
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) => r.method === 'GET' && r.url === '/api/skills/triggers'
      );
      req.flush(triggers);
    });

    it('should default to an empty array when the envelope is missing', (done) => {
      // Defensive — the backend always returns ``{items: [...]}`` but
      // future refactors should not blow up the page if the envelope
      // is dropped.
      service.listTriggers().subscribe({
        next: (result) => {
          expect(result).toEqual([]);
          done();
        },
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) => r.method === 'GET' && r.url === '/api/skills/triggers'
      );
      req.flush({});
    });

    it('should propagate backend errors', (done) => {
      service.listTriggers().subscribe({
        next: () => done.fail('expected error'),
        error: (err) => {
          expect(err.status).toBe(503);
          done();
        },
      });

      const req = httpTesting.expectOne(
        (r) => r.method === 'GET' && r.url === '/api/skills/triggers'
      );
      req.flush('Service Unavailable', {
        status: 503,
        statusText: 'Service Unavailable',
      });
    });
  });

  // ── createTrigger ───────────────────────────────────────────────────────

  describe('createTrigger', () => {
    it('should POST to /api/skills/triggers with the body verbatim and unwrap the {trigger} envelope', (done) => {
      const payload = {
        name: 'Run lint on push',
        condition_type: 'keyword',
        condition_json: { keyword: 'lint' },
        action: 'inject:lint',
        is_enabled: true,
        project_id: 'project-uuid-1',
      };
      const mockResponse = makeTrigger({ ...payload, id: 'trigger-uuid-new', project_id: 'project-uuid-1' });

      service.createTrigger(payload).subscribe({
        next: (result) => {
          expect(result).toEqual(mockResponse);
          expect(result.id).toBe('trigger-uuid-new');
          done();
        },
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'POST' &&
          r.url === '/api/skills/triggers'
      );
      expect(req.request.body).toEqual(payload);
      req.flush({ trigger: mockResponse });
    });

    it('should accept a bare SkillTrigger body (no envelope)', (done) => {
      const payload = {
        name: 'Keyword match',
        condition_type: 'keyword',
        condition_json: { keyword: 'review' },
        action: 'inject:reviewer',
      };
      const mockResponse = makeTrigger({ ...payload, id: 'trigger-uuid-flat' });

      service.createTrigger(payload).subscribe({
        next: (result) => {
          expect(result.id).toBe('trigger-uuid-flat');
          done();
        },
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) => r.method === 'POST' && r.url === '/api/skills/triggers'
      );
      expect(req.request.body).toEqual(payload);
      req.flush(mockResponse);
    });

    it('should propagate a 422 validation error from the backend', (done) => {
      const payload = {
        name: '',
        condition_type: 'keyword',
        condition_json: {},
        action: 'inject:nothing',
      };

      service.createTrigger(payload).subscribe({
        next: () => done.fail('expected error'),
        error: (err) => {
          expect(err.status).toBe(422);
          done();
        },
      });

      const req = httpTesting.expectOne(
        (r) => r.method === 'POST' && r.url === '/api/skills/triggers'
      );
      req.flush('Invalid name', { status: 422, statusText: 'Unprocessable Entity' });
    });
  });

  // ── updateTrigger ───────────────────────────────────────────────────────

  describe('updateTrigger', () => {
    it('should PUT to /api/skills/triggers/{id} with the partial body and unwrap the envelope', (done) => {
      const partial: { is_enabled: boolean } = { is_enabled: false };
      const mockResponse = makeTrigger({ id: 'trigger-uuid-1', is_enabled: false });

      service.updateTrigger('trigger-uuid-1', partial).subscribe({
        next: (result) => {
          expect(result.is_enabled).toBe(false);
          done();
        },
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'PUT' &&
          r.url === '/api/skills/triggers/trigger-uuid-1'
      );
      expect(req.request.body).toEqual(partial);
      req.flush({ trigger: mockResponse });
    });

    it('should pass multi-field partial updates through to the backend', (done) => {
      const partial = {
        name: 'Renamed trigger',
        action: 'inject:code-review',
      };

      service.updateTrigger('trigger-uuid-1', partial).subscribe({
        next: () => done(),
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'PUT' &&
          r.url === '/api/skills/triggers/trigger-uuid-1'
      );
      expect(req.request.body).toEqual(partial);
      req.flush({ trigger: makeTrigger({ ...partial, id: 'trigger-uuid-1' }) });
    });

    it('should accept a bare SkillTrigger body (no envelope)', (done) => {
      const partial = { is_enabled: true };
      const mockResponse = makeTrigger({ id: 'trigger-uuid-1', is_enabled: true });

      service.updateTrigger('trigger-uuid-1', partial).subscribe({
        next: (result) => {
          expect(result.id).toBe('trigger-uuid-1');
          done();
        },
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) => r.method === 'PUT' && r.url === '/api/skills/triggers/trigger-uuid-1'
      );
      expect(req.request.body).toEqual(partial);
      req.flush(mockResponse);
    });

    it('should propagate a 404 when the trigger does not exist', (done) => {
      service.updateTrigger('trigger-missing', { is_enabled: false }).subscribe({
        next: () => done.fail('expected error'),
        error: (err) => {
          expect(err.status).toBe(404);
          done();
        },
      });

      const req = httpTesting.expectOne(
        (r) => r.method === 'PUT' && r.url === '/api/skills/triggers/trigger-missing'
      );
      req.flush('Not found', { status: 404, statusText: 'Not Found' });
    });

    it('should URL-encode the trigger id', (done) => {
      service.updateTrigger('trigger/with space', { is_enabled: true }).subscribe({
        next: () => done(),
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'PUT' &&
          r.url === '/api/skills/triggers/trigger%2Fwith%20space'
      );
      req.flush(makeTrigger({ id: 'trigger/with space' }));
    });
  });

  // ── deleteTrigger ───────────────────────────────────────────────────────

  describe('deleteTrigger', () => {
    it('should DELETE /api/skills/triggers/{id} and return {deleted: true} on success', (done) => {
      service.deleteTrigger('trigger-uuid-1').subscribe({
        next: (result) => {
          expect(result).toEqual({ deleted: true });
          expect(result.deleted).toBe(true);
          done();
        },
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'DELETE' &&
          r.url === '/api/skills/triggers/trigger-uuid-1'
      );
      expect(req.request.body).toBeNull();
      req.flush({ deleted: true });
    });

    it('should return {deleted: false} on idempotent no-op (no row matched)', (done) => {
      service.deleteTrigger('trigger-missing').subscribe({
        next: (result) => {
          expect(result).toEqual({ deleted: false });
          expect(result.deleted).toBe(false);
          done();
        },
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'DELETE' &&
          r.url === '/api/skills/triggers/trigger-missing'
      );
      req.flush({ deleted: false });
    });

    it('should URL-encode the trigger id', (done) => {
      service.deleteTrigger('trigger/with space').subscribe({
        next: () => done(),
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'DELETE' &&
          r.url === '/api/skills/triggers/trigger%2Fwith%20space'
      );
      req.flush({ deleted: true });
    });

    it('should propagate a 404 from the backend', (done) => {
      service.deleteTrigger('trigger-missing').subscribe({
        next: () => done.fail('expected error'),
        error: (err) => {
          expect(err.status).toBe(404);
          done();
        },
      });

      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'DELETE' &&
          r.url === '/api/skills/triggers/trigger-missing'
      );
      req.flush('Not found', { status: 404, statusText: 'Not Found' });
    });
  });
});
