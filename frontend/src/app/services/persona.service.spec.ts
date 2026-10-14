import { TestBed, fakeAsync, tick } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { PersonaService } from './persona.service';
import {
  PromptVersion,
  PromptVersionCreate,
  PromptVersionUpdate,
  PromptVersionFilters,
  EffectiveContentResponse,
} from '../models/persona.model';

/**
 * Tests for PersonaService — agent-prompt-immutability Phase C P1.
 *
 * Pattern: TestBed + provideHttpClient + provideHttpClientTesting +
 * HttpTestingController. Every method asserts the exact wire shape
 * (URL, method, query params, body, response envelope) so the
 * contract with ``daemon/routers/prompts.py`` (15-endpoint canonical
 * family) is pinned down. Errors are exercised through
 * ``req.flush(..., {status: …, statusText: …})``.
 */

// ── Factories ───────────────────────────────────────────────────────────────

function makeVersion(overrides: Partial<PromptVersion> = {}): PromptVersion {
  return {
    version_id: '00000000-0000-0000-0000-000000000001',
    agent_id: 'developer',
    version_tag: '',
    artifact: 'soul',
    artifact_class: 'CORE',
    version: 1,
    state: 'draft',
    content: '# Soul v1',
    content_hash: 'abc123',
    is_selected: 0,
    submitter_instance_id: null,
    submitter_agent_id: 'developer',
    submitter_version_tag: '',
    approver_subject: null,
    approver_auth_method: null,
    created_at: '2026-10-14T00:00:00.000Z',
    updated_at: '2026-10-14T00:00:00.000Z',
    ...overrides,
  };
}

function makeListResponse(
  overrides: Partial<{ items: PromptVersion[]; total: number }> = {},
): { items: PromptVersion[]; total: number } {
  return {
    items: [makeVersion()],
    total: 1,
    ...overrides,
  };
}

// ── Suite ──────────────────────────────────────────────────────────────────

describe('PersonaService', () => {
  let service: PersonaService;
  let httpTesting: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(PersonaService);
    httpTesting = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpTesting.verify();
  });

  // ── list ─────────────────────────────────────────────────────────────

  describe('list', () => {
    it('GETs /api/prompts/versions with no query params when called with no filters', (done) => {
      const mock = makeListResponse();

      service.list().subscribe({
        next: (rows) => {
          expect(rows).toEqual(mock.items);
          expect(rows.length).toBe(1);
          done();
        },
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'GET' &&
          r.url === '/api/prompts/versions' &&
          r.params.keys().length === 0,
      );
      req.flush(mock);
    });

    it('forwards every filter field as a query param', (done) => {
      const filters: PromptVersionFilters = {
        agent_id: 'tester',
        version_tag: 'v2',
        artifact: 'workflow',
        state: 'pending_approval',
        artifact_class: 'CORE',
        limit: 50,
        offset: 100,
      };

      service.list(filters).subscribe({
        next: () => done(),
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'GET' &&
          r.url === '/api/prompts/versions' &&
          r.params.get('agent_id') === 'tester' &&
          r.params.get('version_tag') === 'v2' &&
          r.params.get('artifact') === 'workflow' &&
          r.params.get('state') === 'pending_approval' &&
          r.params.get('artifact_class') === 'CORE' &&
          r.params.get('limit') === '50' &&
          r.params.get('offset') === '100',
      );
      req.flush(makeListResponse());
    });

    it('unwraps the {items,total} envelope and pushes into the items signal', (done) => {
      service.list().subscribe({
        next: () => {
          expect(service.items().length).toBe(1);
          expect(service.items()[0].agent_id).toBe('developer');
          done();
        },
        error: done.fail,
      });
      const req = httpTesting.expectOne((r) => r.url === '/api/prompts/versions');
      req.flush(makeListResponse());
    });

    it('swallows a transport error and returns an empty list (badge resilience)', (done) => {
      service.list().subscribe({
        next: (rows) => {
          expect(rows).toEqual([]);
          expect(service.items()).toEqual([]);
          expect(service.error()).toBeTruthy();
          done();
        },
        error: done.fail,
      });
      const req = httpTesting.expectOne((r) => r.url === '/api/prompts/versions');
      req.flush('boom', { status: 503, statusText: 'Service Unavailable' });
    });

    it('handles a 404 response by surfacing the wire envelope as a friendly error', (done) => {
      service.list().subscribe({
        next: (rows) => {
          expect(rows).toEqual([]);
          expect(service.error()).toBeTruthy();
          done();
        },
        error: done.fail,
      });
      const req = httpTesting.expectOne((r) => r.url === '/api/prompts/versions');
      req.flush(
        { error: 'VERSION_NOT_FOUND', message: 'no rows' },
        { status: 404, statusText: 'Not Found' },
      );
    });
  });

  // ── getVersion ────────────────────────────────────────────────────────

  describe('getVersion', () => {
    it('GETs /api/prompts/versions/{id}', (done) => {
      service.getVersion('11111111-1111-1111-1111-111111111111').subscribe({
        next: (v) => {
          expect(v.version_id).toEqual('11111111-1111-1111-1111-111111111111');
          done();
        },
        error: done.fail,
      });
      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'GET' &&
          r.url === '/api/prompts/versions/11111111-1111-1111-1111-111111111111',
      );
      req.flush(makeVersion({ version_id: '11111111-1111-1111-1111-111111111111' }));
    });

    it('URL-encodes the id (defensive fence)', (done) => {
      service.getVersion('weird/id').subscribe({
        next: () => done(),
        error: done.fail,
      });
      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'GET' &&
          r.url === '/api/prompts/versions/weird%2Fid',
      );
      req.flush(makeVersion());
    });
  });

  // ── diff ──────────────────────────────────────────────────────────────

  describe('diff', () => {
    it('GETs /diff with the default against=effective', (done) => {
      service.diff('11111111-1111-1111-1111-111111111111').subscribe({
        next: () => done(),
        error: done.fail,
      });
      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'GET' &&
          r.url === '/api/prompts/versions/11111111-1111-1111-1111-111111111111/diff' &&
          r.params.get('against') === 'effective',
      );
      req.flush({ version_id: 'x', against: 'effective', against_version_id: null, diff: '' });
    });

    it('forwards an explicit against value', (done) => {
      service
        .diff('11111111-1111-1111-1111-111111111111', 'approved')
        .subscribe({
          next: () => done(),
          error: done.fail,
        });
      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'GET' &&
          r.params.get('against') === 'approved',
      );
      req.flush({ version_id: 'x', against: 'approved', against_version_id: null, diff: '' });
    });
  });

  // ── approve / reject / submit / retract ───────────────────────────────

  describe('approve', () => {
    it('POSTs /approve and refreshes the pending-count on success', (done) => {
      service.approve('v-id').subscribe({
        next: () => {
          // After success, refreshPendingCount fires a 2nd GET.
          const req = httpTesting.expectOne('/api/prompts/pending-count');
          req.flush({ count: 0 });
          done();
        },
        error: done.fail,
      });
      const req = httpTesting.expectOne(
        (r) => r.method === 'POST' && r.url === '/api/prompts/versions/v-id/approve',
      );
      req.flush(makeVersion({ state: 'approved' }));
    });

    it('rejects with the error mapped onto the error signal', (done) => {
      service.approve('v-id').subscribe({
        next: () => done.fail('expected error'),
        error: (err) => {
          expect(service.error()).toBeTruthy();
          done();
        },
      });
      const req = httpTesting.expectOne(
        (r) => r.method === 'POST' && r.url === '/api/prompts/versions/v-id/approve',
      );
      req.flush(
        { error: 'HUMAN_LANE_REQUIRED', message: 'session lane only' },
        { status: 403, statusText: 'Forbidden' },
      );
    });
  });

  describe('reject', () => {
    it('POSTs /reject and refreshes the pending-count on success', (done) => {
      service.reject('v-id').subscribe({
        next: () => {
          httpTesting.expectOne('/api/prompts/pending-count').flush({ count: 0 });
          done();
        },
        error: done.fail,
      });
      httpTesting
        .expectOne((r) => r.method === 'POST' && r.url === '/api/prompts/versions/v-id/reject')
        .flush(makeVersion({ state: 'rejected' }));
    });
  });

  describe('submit', () => {
    it('POSTs /submit and refreshes the pending-count on success', (done) => {
      service.submit('v-id').subscribe({
        next: () => {
          httpTesting.expectOne('/api/prompts/pending-count').flush({ count: 0 });
          done();
        },
        error: done.fail,
      });
      httpTesting
        .expectOne((r) => r.method === 'POST' && r.url === '/api/prompts/versions/v-id/submit')
        .flush(makeVersion({ state: 'pending_approval' }));
    });
  });

  describe('retract', () => {
    it('POSTs /retract and refreshes the pending-count on success', (done) => {
      service.retract('v-id').subscribe({
        next: () => {
          httpTesting.expectOne('/api/prompts/pending-count').flush({ count: 0 });
          done();
        },
        error: done.fail,
      });
      httpTesting
        .expectOne((r) => r.method === 'POST' && r.url === '/api/prompts/versions/v-id/retract')
        .flush(makeVersion({ state: 'draft' }));
    });
  });

  // ── createDraft ───────────────────────────────────────────────────────

  describe('createDraft', () => {
    it('POSTs /versions with the (agent, tag, artifact, content) tuple', (done) => {
      const payload: PromptVersionCreate = {
        agent_id: 'developer',
        version_tag: '',
        artifact: 'soul',
        content: '# Soul v1',
      };

      service.createDraft(payload).subscribe({
        next: (created) => {
          expect(created.version).toBe(1);
          done();
        },
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) => r.method === 'POST' && r.url === '/api/prompts/versions',
      );
      expect(req.request.body).toEqual(payload);
      req.flush(makeVersion());
    });

    it('prepends the created row to the items signal', (done) => {
      service.createDraft({
        agent_id: 'developer',
        artifact: 'soul',
        content: '# v2',
      }).subscribe({
        next: () => {
          expect(service.items().length).toBe(1);
          expect(service.items()[0].content).toBe('# v2');
          done();
        },
        error: done.fail,
      });
      httpTesting.expectOne((r) => r.url === '/api/prompts/versions').flush(makeVersion({ content: '# v2' }));
    });
  });

  // ── updateContent ─────────────────────────────────────────────────────

  describe('updateContent', () => {
    it('PATCHes /versions/{id} with {content}', (done) => {
      const payload: PromptVersionUpdate = { content: '# updated' };
      service.updateContent('v-id', payload).subscribe({
        next: () => done(),
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) => r.method === 'PATCH' && r.url === '/api/prompts/versions/v-id',
      );
      expect(req.request.body).toEqual(payload);
      req.flush(makeVersion({ content: '# updated' }));
    });

    it('replaces the matching row in items signal on success', (done) => {
      service.items.set([makeVersion({ version_id: 'v-id', content: 'old' })]);
      service.updateContent('v-id', { content: 'new' }).subscribe({
        next: () => {
          expect(service.items()[0].content).toBe('new');
          done();
        },
        error: done.fail,
      });
      httpTesting
        .expectOne((r) => r.method === 'PATCH')
        .flush(makeVersion({ version_id: 'v-id', content: 'new' }));
    });
  });

  // ── deleteVersion ─────────────────────────────────────────────────────

  describe('deleteVersion', () => {
    it('DELETEs /versions/{id} and removes the row from items', (done) => {
      service.items.set([
        makeVersion({ version_id: 'v-id-1' }),
        makeVersion({ version_id: 'v-id-2' }),
      ]);
      service.deleteVersion('v-id-1').subscribe({
        next: (res) => {
          expect(res.deleted).toBe(true);
          expect(service.items().length).toBe(1);
          expect(service.items()[0].version_id).toBe('v-id-2');
          done();
        },
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) => r.method === 'DELETE' && r.url === '/api/prompts/versions/v-id-1',
      );
      req.flush({ version_id: 'v-id-1', deleted: true });
    });
  });

  // ── getEffective ───────────────────────────────────────────────────────

  describe('getEffective', () => {
    it('GETs /effective/{agent}/{tag}/{artifact}', (done) => {
      const mock: EffectiveContentResponse = {
        agent_id: 'developer',
        version_tag: '',
        artifact: 'soul',
        content: '# Effective',
      };

      service.getEffective('developer', '', 'soul').subscribe({
        next: (eff) => {
          expect(eff.content).toBe('# Effective');
          done();
        },
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'GET' &&
          r.url === '/api/prompts/effective/developer//soul',
      );
      req.flush(mock);
    });
  });

  // ── setSelection / clearSelection ──────────────────────────────────────

  describe('setSelection', () => {
    it('POSTs /selections with the (agent, artifact, version_id) tuple', (done) => {
      service
        .setSelection({
          agent_id: 'developer',
          artifact: 'soul',
          version_id: 'v-id',
        })
        .subscribe({
          next: () => done(),
          error: done.fail,
        });

      const req = httpTesting.expectOne(
        (r) => r.method === 'POST' && r.url === '/api/prompts/selections',
      );
      // ``version_tag`` is optional on the FE type; the wire body mirrors
      // whatever the caller supplied. The backend defaults the missing
      // key to ``""`` (Phase B D11 + addendum S4).
      expect(req.request.body).toEqual({
        agent_id: 'developer',
        artifact: 'soul',
        version_id: 'v-id',
      });
      req.flush({
        agent_id: 'developer',
        version_tag: '',
        artifact: 'soul',
        version_id: 'v-id',
      });
    });

    it('includes version_tag when explicitly provided', (done) => {
      service
        .setSelection({
          agent_id: 'developer',
          version_tag: 'v2',
          artifact: 'soul',
          version_id: 'v-id',
        })
        .subscribe({
          next: () => done(),
          error: done.fail,
        });

      const req = httpTesting.expectOne(
        (r) => r.method === 'POST' && r.url === '/api/prompts/selections',
      );
      expect(req.request.body).toEqual({
        agent_id: 'developer',
        version_tag: 'v2',
        artifact: 'soul',
        version_id: 'v-id',
      });
      req.flush({
        agent_id: 'developer',
        version_tag: 'v2',
        artifact: 'soul',
        version_id: 'v-id',
      });
    });
  });

  describe('clearSelection', () => {
    it('DELETEs /selections/{agent}/{tag}/{artifact}', (done) => {
      service.clearSelection('developer', '', 'soul').subscribe({
        next: () => done(),
        error: done.fail,
      });

      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'DELETE' &&
          r.url === '/api/prompts/selections/developer//soul',
      );
      req.flush({
        agent_id: 'developer',
        version_tag: '',
        artifact: 'soul',
        version_id: null,
      });
    });
  });

  // ── listAudit ─────────────────────────────────────────────────────────

  describe('listAudit', () => {
    it('GETs /audit with filters forwarded as query params', (done) => {
      service
        .listAudit({
          agent_id: 'developer',
          event_type: 'APPROVED',
          limit: 10,
          offset: 0,
        })
        .subscribe({
          next: () => done(),
          error: done.fail,
        });
      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'GET' &&
          r.url === '/api/prompts/audit' &&
          r.params.get('agent_id') === 'developer' &&
          r.params.get('event_type') === 'APPROVED' &&
          r.params.get('limit') === '10',
      );
      req.flush({ items: [], total: 0 });
    });
  });

  // ── pendingCount ──────────────────────────────────────────────────────

  describe('pendingCount', () => {
    it('GETs /pending-count and updates the signal', (done) => {
      service.pendingCount$().subscribe({
        next: (res) => {
          expect(res.count).toBe(5);
          done();
        },
        error: done.fail,
      });
      httpTesting.expectOne('/api/prompts/pending-count').flush({ count: 5 });
    });

    it('swallows a transport error and returns 0', (done) => {
      service.pendingCount$().subscribe({
        next: (res) => {
          expect(res.count).toBe(0);
          done();
        },
        error: done.fail,
      });
      httpTesting.expectOne('/api/prompts/pending-count').flush('boom', {
        status: 503,
        statusText: 'Service Unavailable',
      });
    });

    it('refreshPendingCount writes the signal without throwing', fakeAsync(() => {
      expect(service.pendingCount()).toBe(0);
      service.refreshPendingCount();
      tick();
      const req = httpTesting.expectOne('/api/prompts/pending-count');
      req.flush({ count: 7 });
      expect(service.pendingCount()).toBe(7);
      expect(service.pendingCountFetchedAt()).toBeGreaterThan(0);
    }));

    it('refreshPendingCount is silent on transport failure', fakeAsync(() => {
      service.refreshPendingCount();
      tick();
      httpTesting.expectOne('/api/prompts/pending-count').flush('boom', {
        status: 503,
        statusText: 'Service Unavailable',
      });
      // Signal unchanged — swallow-only.
      expect(service.pendingCount()).toBe(0);
    }));
  });

  // ── clearError ────────────────────────────────────────────────────────

  describe('clearError', () => {
    it('resets the error signal to null', () => {
      service.error.set('something broke');
      service.clearError();
      expect(service.error()).toBeNull();
    });
  });
});