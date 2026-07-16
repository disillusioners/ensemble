import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { PageEvent } from '@angular/material/paginator';
import { of, throwError } from 'rxjs';

import { SkillUsageTableComponent } from './skill-usage-table.component';
import { SkillService } from '../../services/skill.service';
import {
  SkillUsageRecord,
  SkillUsageRecordsResponse,
} from '../../models/skill.model';

// ===========================================================================
// Factories
// ===========================================================================

/**
 * Build a single `SkillUsageRecord` fixture for use in the suite below.
 * Mirrors the backend payload shape exactly (see
 * `daemon/repositories/skill/models.py:SkillUsageRecord.to_dict`) so a
 * copy-paste typo would surface as a field mismatch, not a behaviour bug.
 */
function makeRecord(overrides: Partial<SkillUsageRecord> = {}): SkillUsageRecord {
  return {
    id: 'record-uuid-1',
    skill_id: 'skill-uuid-1',
    project_id: null,
    instance_id: 'instance-uuid-1',
    agent_id: 'developer',
    task_message: 'Refactor the auth module',
    selected: true,
    applied: true,
    task_succeeded: true,
    iterations: 3,
    duration_seconds: 12.5,
    fallback: false,
    feedback_applied: null,
    feedback_note: null,
    ab_test_group: null,
    superseded: false,
    created_at: '2026-07-16T10:00:00.000Z',
    ...overrides,
  };
}

/**
 * Build a `SkillUsageRecordsResponse` envelope — the backend wraps the
 * page in `{skill_id, records, total, limit, offset}` so the table
 * can drive the paginator off `total` directly.
 */
function makeResponse(
  overrides: Partial<SkillUsageRecordsResponse> = {}
): SkillUsageRecordsResponse {
  return {
    skill_id: 'skill-uuid-1',
    records: [],
    total: 0,
    limit: 25,
    offset: 0,
    ...overrides,
  };
}

// ===========================================================================
// Suite
// ===========================================================================

/**
 * Tests for `SkillUsageTableComponent` (Phase 5 Part A).
 *
 * Pattern: Angular `TestBed` with a mocked `SkillService` — the
 * component is small enough that testing through TestBed (DOM +
 * signal interactions) is cheaper than maintaining a mirror class.
 * Mirrors the `skill.service.spec.ts` provider shape (`useValue` +
 * `provideNoopAnimations`) so the spec slots in next to it.
 */
describe('SkillUsageTableComponent', () => {
  let fixture: ComponentFixture<SkillUsageTableComponent>;
  let component: SkillUsageTableComponent;
  let mockSkillService: { getUsageRecords: jest.Mock };

  beforeEach(async () => {
    mockSkillService = {
      getUsageRecords: jest.fn().mockReturnValue(
        of(makeResponse({ records: [], total: 0 })),
      ),
    };

    await TestBed.configureTestingModule({
      imports: [SkillUsageTableComponent],
      providers: [
        { provide: SkillService, useValue: mockSkillService },
        provideNoopAnimations(),
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(SkillUsageTableComponent);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('skillId', 'skill-uuid-1');
    fixture.detectChanges();
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  // ── 1) Component creation ─────────────────────────────────────

  describe('component creation', () => {
    it('creates successfully', () => {
      expect(component).toBeTruthy();
    });

    it('exposes the `skillId` input as a Signal', () => {
      expect(component.skillId()).toBe('skill-uuid-1');
    });

    it('calls getUsageRecords on construction with the skillId and default pagination (limit=25, offset=0)', () => {
      expect(mockSkillService.getUsageRecords).toHaveBeenCalledWith(
        'skill-uuid-1',
        25,
        0,
      );
    });

    it('initialises records and total from the response', () => {
      // The default mock returns `records: []`, `total: 0` — verify
      // the component populated those signals after the synchronous
      // effect / subscribe cycle.
      expect(component.records()).toEqual([]);
      expect(component.total()).toBe(0);
    });

    it('flips loading back to false after the synchronous response', () => {
      // The effect sets loading=true, subscribes synchronously, then
      // flips loading=false. After detectChanges the state should
      // match the post-response snapshot.
      expect(component.loading()).toBe(false);
    });
  });

  // ── 2) Empty state ────────────────────────────────────────────

  describe('empty state', () => {
    it('renders "No usage history yet." when records is empty', () => {
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('No usage history yet.');
    });

    it('does NOT render the table when records are empty', () => {
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.querySelector('table.usage-table')).toBeNull();
    });

    it('does NOT render the paginator when records are empty', () => {
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.querySelector('mat-paginator')).toBeNull();
    });
  });

  // ── 3) Loading state ──────────────────────────────────────────

  describe('loading state', () => {
    it('shows the spinner when loading() is true', () => {
      component.loading.set(true);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.querySelector('mat-progress-spinner')).not.toBeNull();
    });

    it('hides the spinner when loading is false', () => {
      component.loading.set(false);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.querySelector('mat-progress-spinner')).toBeNull();
    });

    it('hides the empty-state message while loading', () => {
      component.loading.set(true);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).not.toContain('No usage history yet.');
    });
  });

  // ── 4) Pagination: offset calculation ─────────────────────────

  describe('computeOffset / onPageChange', () => {
    it('computeOffset returns pageIndex * pageSize (pageIndex=2, pageSize=25 → offset=50)', () => {
      expect(component.computeOffset(2, 25)).toBe(50);
    });

    it('computeOffset returns 0 for pageIndex=0', () => {
      expect(component.computeOffset(0, 25)).toBe(0);
      expect(component.computeOffset(0, 10)).toBe(0);
    });

    it('computeOffset returns 40 for pageIndex=4 with pageSize=10', () => {
      expect(component.computeOffset(4, 10)).toBe(40);
    });

    it('onPageChange updates offset, pageIndex, pageSize signals', () => {
      const event: PageEvent = {
        pageIndex: 2,
        pageSize: 25,
        length: 100,
      };

      component.onPageChange(event);

      expect(component.offset()).toBe(50);
      expect(component.pageIndex()).toBe(2);
      expect(component.pageSize()).toBe(25);
    });

    it('onPageChange refetches the page with the new limit and offset', () => {
      mockSkillService.getUsageRecords.mockClear();

      component.onPageChange({
        pageIndex: 3,
        pageSize: 25,
        length: 100,
      });

      expect(mockSkillService.getUsageRecords).toHaveBeenCalledWith(
        'skill-uuid-1',
        25,
        75,
      );
    });

    it('onPageChange clears the expanded row', () => {
      const r1 = makeRecord({ id: 'r1' });
      component.toggleExpand(r1);
      expect(component.expandedRowId()).toBe('r1');

      component.onPageChange({
        pageIndex: 1,
        pageSize: 25,
        length: 100,
      });

      expect(component.expandedRowId()).toBeNull();
    });
  });

  // ── 5) Row expansion: toggleExpand ────────────────────────────

  describe('toggleExpand / isExpanded', () => {
    const r1 = makeRecord({ id: 'r1' });
    const r2 = makeRecord({ id: 'r2' });

    it('starts with no row expanded', () => {
      expect(component.expandedRowId()).toBeNull();
    });

    it('sets expandedRowId to the record id when collapsed', () => {
      component.toggleExpand(r1);
      expect(component.expandedRowId()).toBe('r1');
    });

    it('clears expandedRowId when the same record is toggled again', () => {
      component.toggleExpand(r1);
      expect(component.expandedRowId()).toBe('r1');

      component.toggleExpand(r1);
      expect(component.expandedRowId()).toBeNull();
    });

    it('switches expandedRowId to a different record when toggled', () => {
      component.toggleExpand(r1);
      component.toggleExpand(r2);
      expect(component.expandedRowId()).toBe('r2');
    });

    it('isExpanded reflects the expandedRowId signal', () => {
      expect(component.isExpanded(r1)).toBe(false);
      expect(component.isExpanded(r2)).toBe(false);

      component.toggleExpand(r1);

      expect(component.isExpanded(r1)).toBe(true);
      expect(component.isExpanded(r2)).toBe(false);
    });
  });

  // ── 6) Row class logic ────────────────────────────────────────

  describe('rowClass', () => {
    it('marks the row as row-success when task_succeeded is true', () => {
      const rec = makeRecord({ task_succeeded: true });
      const classes = component.rowClass(rec);
      expect(classes['row-success']).toBe(true);
      expect(classes['row-failure']).toBe(false);
    });

    it('marks the row as row-failure when task_succeeded is false', () => {
      const rec = makeRecord({ task_succeeded: false });
      const classes = component.rowClass(rec);
      expect(classes['row-failure']).toBe(true);
      expect(classes['row-success']).toBe(false);
    });

    it('marks the row as row-fallback when fallback is true', () => {
      const rec = makeRecord({ fallback: true });
      expect(component.rowClass(rec)['row-fallback']).toBe(true);
    });

    it('marks the row as row-superseded when superseded is true', () => {
      const rec = makeRecord({ superseded: true });
      expect(component.rowClass(rec)['row-superseded']).toBe(true);
    });

    it('returns row-success=true and all other flags false for a default successful record', () => {
      // The factory default has task_succeeded=true, fallback=false,
      // superseded=false — sanity-check the baseline.
      const classes = component.rowClass(makeRecord());
      expect(classes).toEqual({
        'row-success': true,
        'row-failure': false,
        'row-fallback': false,
        'row-superseded': false,
      });
    });

    it('composes multiple flags when several conditions hold simultaneously', () => {
      // Real-world: a superseded failure row should be muted AND
      // line-through. The helper returns a map so the template can
      // apply both classes via [ngClass].
      const rec = makeRecord({
        task_succeeded: false,
        fallback: true,
        superseded: true,
      });
      const classes = component.rowClass(rec);
      expect(classes['row-failure']).toBe(true);
      expect(classes['row-fallback']).toBe(true);
      expect(classes['row-superseded']).toBe(true);
      expect(classes['row-success']).toBe(false);
    });
  });

  // ── 7) allSuperseded derived signal ───────────────────────────

  describe('allSuperseded', () => {
    it('is false when records is empty', () => {
      expect(component.allSuperseded()).toBe(false);
    });

    it('is false when at least one record is not superseded', () => {
      component.records.set([
        makeRecord({ id: 'r1', superseded: true }),
        makeRecord({ id: 'r2', superseded: false }),
      ]);
      expect(component.allSuperseded()).toBe(false);
    });

    it('is true when every record is superseded', () => {
      component.records.set([
        makeRecord({ id: 'r1', superseded: true }),
        makeRecord({ id: 'r2', superseded: true }),
      ]);
      expect(component.allSuperseded()).toBe(true);
    });
  });

  // ── 8) Error handling ────────────────────────────────────────

  describe('error handling', () => {
    it('sets error signal and clears loading when the service throws', () => {
      mockSkillService.getUsageRecords.mockReturnValueOnce(
        throwError(() => ({ message: 'Network down' })),
      );

      component.onRetry();

      expect(component.error()).toBe('Network down');
      expect(component.loading()).toBe(false);
    });

    it('falls back to a generic message when the error has no message field', () => {
      mockSkillService.getUsageRecords.mockReturnValueOnce(
        throwError(() => ({})),
      );

      component.onRetry();

      expect(component.error()).toBe('Failed to load usage records');
    });

    it('clears the error signal on the next successful fetch', () => {
      mockSkillService.getUsageRecords.mockReturnValueOnce(
        throwError(() => ({ message: 'boom' })),
      );
      component.onRetry();
      expect(component.error()).toBe('boom');

      mockSkillService.getUsageRecords.mockReturnValueOnce(
        of(makeResponse({ records: [], total: 0 })),
      );
      component.onRetry();
      expect(component.error()).toBeNull();
    });
  });

  // ── 9) Records are populated after a successful fetch ────────

  describe('successful fetch', () => {
    it('populates records and total from the response payload', () => {
      mockSkillService.getUsageRecords.mockReturnValueOnce(
        of(
          makeResponse({
            records: [
              makeRecord({ id: 'r1' }),
              makeRecord({ id: 'r2' }),
            ],
            total: 47,
          }),
        ),
      );

      component.onRetry();

      expect(component.records()).toHaveLength(2);
      expect(component.records()[0].id).toBe('r1');
      expect(component.total()).toBe(47);
      expect(component.loading()).toBe(false);
    });
  });
});