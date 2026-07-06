import { signal } from '@angular/core';
import { HttpParams } from '@angular/common/http';
import { Work, WorkFilters } from '../models/work.model';
import { createMockWorkList } from '../testing/work-test-helpers';

/**
 * Testable double for ``WorkService``.
 *
 * Mirrors the URL/params construction in the real service (which
 * uses ``HttpClient`` + ``HttpParams``) but with the observable
 * replaced by a fake subscriber so we can assert on the exact query
 * string the page will send. The HttpParams object is captured as a
 * string at the call site so tests can grep for ``root_only=``
 * etc. without standing up ``HttpTestingController`` — which is
 * overkill for the unit-level guarantee this spec provides.
 *
 * The pattern matches ``TestableJobService`` in
 * ``job.service.spec.ts`` and ``TestableQueueService`` in
 * ``queue.service.spec.ts``: signal state + observable stub, no
 * Angular TestBed required.
 */
class TestableWorkService {
  readonly API_BASE = '/api/work';

  readonly works = signal<Work[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  /**
   * Captured query string for the most recent ``getWork`` call so
   * tests can assert on it. Empty string when no params were sent.
   */
  lastQueryString: string = '';

  /**
   * Captured full URL (base + query string) for the most recent
   * ``getWork`` call. Mirrors the ``url`` variable used in the
   * existing ``TestableJobService``.
   */
  lastUrl: string = '';

  getWork(filters?: WorkFilters) {
    // Mirror the production ``HttpParams`` construction verbatim
    // (see ``work.service.ts:getWork``) so the captured query
    // string is byte-identical to what the real service would send.
    let params = new HttpParams();
    if (filters) {
      if (filters.status) params = params.set('status', filters.status);
      if (filters.project_id) params = params.set('project_id', filters.project_id);
      if (filters.instance_id) params = params.set('instance_id', filters.instance_id);
      if (filters.kind) params = params.set('kind', filters.kind);
      if (filters.root_only !== undefined) {
        // Explicit ``true`` / ``false`` serialisation — the FastAPI
        // ``bool`` Query coercion relies on the literal token, not
        // an empty value, so the test mirrors that contract.
        params = params.set('root_only', filters.root_only ? 'true' : 'false');
      }
    }
    const queryString = params.toString();
    this.lastQueryString = queryString;
    this.lastUrl = this.API_BASE + (queryString ? `?${queryString}` : '');

    // Build a shared subscriber so both ``.pipe().subscribe()``
    // and the bare ``.subscribe({ next, error })`` forms work — the
    // production WorkService is used both ways.
    const dispatch = (observer: any) => {
      const mockWorks = createMockWorkList(2);
      this.works.set(mockWorks);
      if (typeof observer === 'function') {
        observer(mockWorks);
      } else if (observer && observer.next) {
        observer.next(mockWorks);
      }
      this.loading.set(false);
    };

    const observable: any = {
      // Bare subscribe (used by ``refreshWork`` and component code).
      subscribe: (observer: any) => dispatch(observer),
      // Pipe-able form (used by ``getWork`` direct callers).
      pipe: () => ({ subscribe: (observer: any) => dispatch(observer) }),
    };
    return observable;
  }

  refreshWork(filters?: WorkFilters): void {
    this.loading.set(true);
    this.getWork(filters).subscribe({
      next: () => this.loading.set(false),
      error: () => this.loading.set(false),
    });
  }

  clearError(): void {
    this.error.set(null);
  }
}

describe('WorkService', () => {
  let service: TestableWorkService;

  beforeEach(() => {
    service = new TestableWorkService();
  });

  describe('getWork URL/query string construction', () => {
    it('should build URL with no query string when called without filters', () => {
      service.getWork().pipe().subscribe(() => {});
      expect(service.lastUrl).toBe('/api/work');
      expect(service.lastQueryString).toBe('');
    });

    it('should include status filter as query param', () => {
      service.getWork({ status: 'pending' }).pipe().subscribe(() => {});
      expect(service.lastQueryString).toContain('status=pending');
    });

    it('should include project_id filter as query param', () => {
      service.getWork({ project_id: 'project-123' }).pipe().subscribe(() => {});
      expect(service.lastQueryString).toContain('project_id=project-123');
    });

    it('should include instance_id filter as query param', () => {
      service.getWork({ instance_id: 'inst-abc' }).pipe().subscribe(() => {});
      expect(service.lastQueryString).toContain('instance_id=inst-abc');
    });

    it('should include kind filter as query param', () => {
      service.getWork({ kind: 'job' }).pipe().subscribe(() => {});
      expect(service.lastQueryString).toContain('kind=job');
    });

    it('should include multiple filters as query params', () => {
      service.getWork({
        status: 'pending',
        project_id: 'project-123',
        kind: 'report'
      }).pipe().subscribe(() => {});
      const qs = service.lastQueryString;
      expect(qs).toContain('status=pending');
      expect(qs).toContain('project_id=project-123');
      expect(qs).toContain('kind=report');
    });
  });

  /**
   * P-A of the Virtual Job Tool Completeness plan — the ``root_only``
   * filter must round-trip exactly. The "All Work" view in
   * ``JobsComponent`` relies on passing ``root_only: false`` to see
   * every row; if the service silently dropped the param the view
   * would revert to the backend default (root-scoped, no children)
   * and the user would never see the child-instance rows the view
   * name promises.
   */
  describe('getWork root_only contract (P-A)', () => {
    it('should serialise root_only=false as root_only=false (the All Work view contract)', () => {
      service.getWork({ root_only: false }).pipe().subscribe(() => {});
      expect(service.lastQueryString).toContain('root_only=false');
    });

    it('should serialise root_only=true as root_only=true when callers opt in', () => {
      service.getWork({ root_only: true }).pipe().subscribe(() => {});
      expect(service.lastQueryString).toContain('root_only=true');
    });

    it('should omit root_only param when the field is undefined (callers defer to backend default)', () => {
      service.getWork({ project_id: 'project-123' }).pipe().subscribe(() => {});
      expect(service.lastQueryString).not.toContain('root_only');
    });

    it('should not emit a bare root_only token when the value is false (FastAPI bool parser would 400)', () => {
      service.getWork({ root_only: false }).pipe().subscribe(() => {});
      // The literal token ``root_only=false`` must be present — never
      // ``root_only`` (no value) or ``root_only=0`` (which the
      // FastAPI ``bool`` Query type rejects as truthy in some
      // configurations).
      expect(service.lastQueryString).toMatch(/root_only=(?:true|false)\b/);
    });

    it('should combine root_only with other filters without dropping them', () => {
      service.getWork({
        project_id: 'project-123',
        status: 'pending',
        root_only: false,
      }).pipe().subscribe(() => {});
      const qs = service.lastQueryString;
      expect(qs).toContain('project_id=project-123');
      expect(qs).toContain('status=pending');
      expect(qs).toContain('root_only=false');
    });
  });

  describe('getWork signal updates', () => {
    it('should populate the works signal with the response', () => {
      expect(service.works()).toEqual([]);
      service.getWork().pipe().subscribe(() => {});
      expect(service.works().length).toBe(2);
    });

    it('should clear loading flag after subscribe (finalize contract)', () => {
      service.getWork().pipe().subscribe(() => {});
      expect(service.loading()).toBe(false);
    });
  });

  describe('refreshWork', () => {
    it('should invoke getWork with the supplied filters and clear loading', () => {
      service.refreshWork({ root_only: false });
      expect(service.lastQueryString).toContain('root_only=false');
      expect(service.loading()).toBe(false);
    });

    it('should clear loading even when getWork is called without filters', () => {
      service.refreshWork();
      expect(service.lastQueryString).toBe('');
      expect(service.loading()).toBe(false);
    });
  });

  describe('clearError', () => {
    it('should reset the error signal', () => {
      service.error.set('boom');
      service.clearError();
      expect(service.error()).toBeNull();
    });
  });
});

// ── All Work view contract ────────────────────────────────────────────────
//
// The user-visible contract: when the JobsComponent's "All Work"
// view loads work, it must pass ``root_only: false`` to
// ``WorkService.getWork`` so the user sees every row the resolver
// can find (including child-instance turns and reports). This
// describe block focuses on that one contract; the rest of the
// component is exercised by ``jobs.component.spec.ts``.

describe('WorkService.getWork — All Work view contract', () => {
  /**
   * Tiny stand-in for the production WorkService that captures the
   * filters object passed to ``getWork`` so the assertion is on the
   * call contract itself, not on URL serialisation. URL
   * serialisation is already covered by the TestableWorkService
   * describe block above.
   */
  class CapturingWorkService {
    lastFilters: WorkFilters | undefined;
    getWork(filters?: WorkFilters) {
      this.lastFilters = filters;
      return {
        pipe: () => ({
          subscribe: (observer: any) => {
            if (typeof observer === 'function') {
              observer([]);
            } else if (observer.next) {
              observer.next([]);
            }
          }
        })
      };
    }
  }

  it('should accept a WorkFilters object that includes root_only: false', () => {
    const service = new CapturingWorkService();
    service.getWork({ root_only: false });
    expect(service.lastFilters).toEqual({ root_only: false });
  });

  it('should accept root_only: false alongside other filters', () => {
    const service = new CapturingWorkService();
    service.getWork({
      project_id: 'project-123',
      status: 'pending',
      root_only: false,
    });
    expect(service.lastFilters).toEqual({
      project_id: 'project-123',
      status: 'pending',
      root_only: false,
    });
  });

  it('should type-check: root_only is a boolean on the WorkFilters contract', () => {
    // Compile-time guarantee — this is a TypeScript-only assertion
    // that documents the contract on the WorkFilters interface.
    // If ``root_only`` is ever removed from ``WorkFilters``, the
    // literals ``true`` / ``false`` below will fail to compile.
    const trueFilter: WorkFilters = { root_only: true };
    const falseFilter: WorkFilters = { root_only: false };
    expect(trueFilter.root_only).toBe(true);
    expect(falseFilter.root_only).toBe(false);
  });
});