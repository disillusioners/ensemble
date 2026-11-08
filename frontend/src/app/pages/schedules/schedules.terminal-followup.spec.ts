// SchedulesComponent — terminal-toggle follow-up behavior (review
// 2c89f5c8 adopted findings 🟡1/🟡2/🟡3), exercised through the REAL
// component against HttpTestingController (house style — the service
// precedent is mcps-gateway.service.spec.ts; the mounting precedent is
// settings.component.spec.ts with provideNoopAnimations).
//
// Covers what source-grep pins cannot prove:
//   🟡1  include_cancelled gating — the fetch carries
//        ?include_cancelled=true IFF the toggle is ON, and RE-FETCHES
//        on every flip (ON → param, OFF → bare request).
//   🟡2  terminal-stage emptiness renders the toggle-pointed one-liner;
//        a genuine filter miss keeps the original "clear all filters"
//        copy.
//   🟡3  EAGER init — stored 'true' → initial showTerminal() is true
//        and the FIRST request already carries the param; no storage →
//        false (default active-only).

// Stub ngx-markdown (transitive dep of the barrel → chat-interface) so
// jest can transform the heavy component graph under test without the
// marked ESM export problem. The stubs are minimal Angular-shaped.
jest.mock('ngx-markdown', () => {
  const { Component, NgModule } = require('@angular/core');
  @Component({ selector: 'markdown', template: '' })
  class MarkdownComponent {}
  @NgModule({ declarations: [MarkdownComponent], exports: [MarkdownComponent] })
  class MarkdownModule {}
  return { MarkdownModule, MarkdownComponent };
});

import { TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { provideHttpClient, withInterceptorsFromDi } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { of } from 'rxjs';
import { SchedulesComponent } from './schedules.component';
import { ApiService } from '../../services/api.service';
import { SHOW_TERMINAL_STORAGE_KEY } from '../../models/terminal-visibility.model';
import type { Schedule, ScheduleListResponse } from '../../models/scheduler.model';

// ── Fixtures ───────────────────────────────────────────────────────────

function makeSchedule(id: string, status: Schedule['status']): Schedule {
  return {
    id,
    name: id,
    status,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    config: { type: 'one-time', agent: 'ari', message: 'ping' },
  };
}

/** A realistic BARE page: no cancelled rows (the backend hides them by default). */
const BARE_PAGE: ScheduleListResponse = {
  schedules: [makeSchedule('live-1', 'running'), makeSchedule('done-1', 'stopped')],
};

/** The page WITH ?include_cancelled=true: the terminal peer rides along. */
const FULL_PAGE: ScheduleListResponse = {
  schedules: [
    makeSchedule('live-1', 'running'),
    makeSchedule('done-1', 'stopped'),
    makeSchedule('cx-1', 'cancelled'),
  ],
};

/** The protected handlers the template binds; exposed for direct invocation. */
type HandledComponent = SchedulesComponent & {
  onShowTerminalChange(checked: boolean): void;
  onStatusFilterChange(status: Schedule['status'] | 'all'): void;
};

// ── Suite ──────────────────────────────────────────────────────────────

describe('SchedulesComponent — terminal-toggle follow-up behavior', () => {
  let fixture: ReturnType<typeof TestBed.createComponent<SchedulesComponent>>;
  let component: HandledComponent;
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    window.localStorage.clear();
    await TestBed.configureTestingModule({
      imports: [SchedulesComponent],
      providers: [
        provideNoopAnimations(),
        provideHttpClient(withInterceptorsFromDi()),
        provideHttpClientTesting(),
        {
          provide: ApiService,
          useValue: { listAgents: () => of({ agents: [] }) },
        },
      ],
    }).compileComponents();

    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    fixture.destroy();
    httpMock.verify();
    window.localStorage.clear();
  });

  function mount(): HandledComponent {
    fixture = TestBed.createComponent(SchedulesComponent);
    component = fixture.componentInstance as HandledComponent;
    return component;
  }

  // ── 🟡1 include_cancelled gating ─────────────────────────────────────

  it('default mount fetches BARE (no param) with the toggle off, and renders active-only', () => {
    mount();
    expect(component.showTerminal()).toBe(false);

    fixture.detectChanges();

    const req = httpMock.expectOne('/api/schedules');
    expect(req.request.params.keys()).toEqual([]);
    req.flush(BARE_PAGE);
    fixture.detectChanges();

    expect(component.filteredSchedules().map((s) => s.id)).toEqual(['live-1']);
    expect(component.hiddenTerminalCount()).toBe(1);
  });

  it('flip ON → re-fetches WITH ?include_cancelled=true and reveals terminal rows', () => {
    mount();
    fixture.detectChanges();
    httpMock.expectOne('/api/schedules').flush(BARE_PAGE);
    fixture.detectChanges();

    component.onShowTerminalChange(true);
    fixture.detectChanges();

    const req = httpMock.expectOne('/api/schedules?include_cancelled=true');
    expect(req.request.params.get('include_cancelled')).toBe('true');
    req.flush(FULL_PAGE);
    fixture.detectChanges();

    expect(component.showTerminal()).toBe(true);
    expect(window.localStorage.getItem(SHOW_TERMINAL_STORAGE_KEY)).toBe('true');
    expect(component.filteredSchedules().map((s) => s.id)).toEqual([
      'live-1',
      'done-1',
      'cx-1',
    ]);
    expect(component.hiddenTerminalCount()).toBe(0);
  });

  it('flip OFF → re-fetches BARE again (default API behavior restored)', () => {
    window.localStorage.setItem(SHOW_TERMINAL_STORAGE_KEY, 'true');
    mount();
    fixture.detectChanges();
    httpMock.expectOne('/api/schedules?include_cancelled=true').flush(FULL_PAGE);
    fixture.detectChanges();
    expect(component.filteredSchedules().length).toBe(3);

    component.onShowTerminalChange(false);
    fixture.detectChanges();

    const req = httpMock.expectOne('/api/schedules');
    expect(req.request.params.keys()).toEqual([]);
    req.flush(BARE_PAGE);
    fixture.detectChanges();

    expect(component.showTerminal()).toBe(false);
    expect(window.localStorage.getItem(SHOW_TERMINAL_STORAGE_KEY)).toBe('false');
    expect(component.filteredSchedules().map((s) => s.id)).toEqual(['live-1']);
    expect(component.hiddenTerminalCount()).toBe(1);
  });

  // ── flip-race supersede guard ─────────────────────────────────────────
  // A fast ON→OFF flip must not let the stale WITH-param response land
  // LAST and transiently overcount the "N hidden" line (honest counts).

  it('a DELAYED stale WITH-param response flushed AFTER the fresh bare one is superseded (no hidden-count overcount)', () => {
    window.localStorage.setItem(SHOW_TERMINAL_STORAGE_KEY, 'true');
    mount();

    // Initial load: toggle ON → WITH-param request (the soon-stale chain A).
    fixture.detectChanges();
    const staleReq = httpMock.expectOne('/api/schedules?include_cancelled=true');

    // Fast flip OFF → the effect re-issues BARE (chain B) while A is
    // still in flight.
    component.onShowTerminalChange(false);
    fixture.detectChanges();
    const freshReq = httpMock.expectOne('/api/schedules');
    expect(freshReq.request.params.keys()).toEqual([]);

    // The fresh response lands FIRST and the page settles active-only.
    freshReq.flush(BARE_PAGE);
    fixture.detectChanges();
    expect(component.showTerminal()).toBe(false);
    expect(component.filteredSchedules().map((s) => s.id)).toEqual(['live-1']);
    expect(component.hiddenTerminalCount()).toBe(1);

    // …THEN the DELAYED stale response arrives. Superseded: it must NOT
    // overwrite the shared list or double the hidden count.
    staleReq.flush(FULL_PAGE);
    fixture.detectChanges();

    expect(component.schedules().map((s) => s.id)).toEqual(['live-1', 'done-1']);
    expect(component.filteredSchedules().map((s) => s.id)).toEqual(['live-1']);
    expect(component.hiddenTerminalCount()).toBe(1);
    expect(component.totalCount()).toBe(2);
  });

  // ── 🟡3 EAGER init ────────────────────────────────────────────────────

  it('stored "true" → initial showTerminal() is true and the FIRST fetch carries the param', () => {
    window.localStorage.setItem(SHOW_TERMINAL_STORAGE_KEY, 'true');
    mount();

    // No detectChanges yet — the eager field-init already decided.
    expect(component.showTerminal()).toBe(true);

    fixture.detectChanges();
    const req = httpMock.expectOne('/api/schedules?include_cancelled=true');
    req.flush(FULL_PAGE);
    fixture.detectChanges();

    expect(component.filteredSchedules().some((s) => s.id === 'cx-1')).toBe(true);
    expect(component.hiddenTerminalCount()).toBe(0);
  });

  it('no storage → initial showTerminal() is false (default active-only)', () => {
    mount();
    expect(component.showTerminal()).toBe(false);

    fixture.detectChanges();
    const req = httpMock.expectOne('/api/schedules');
    expect(req.request.params.keys()).toEqual([]);
    req.flush(BARE_PAGE);
    fixture.detectChanges();

    expect(component.filteredSchedules().map((s) => s.id)).toEqual(['live-1']);
  });

  // ── 🟡2 terminal-stage empty copy ─────────────────────────────────────

  it('stopped chip with toggle OFF → empty copy points at the toggle (one line, no clear-all)', () => {
    mount();
    fixture.detectChanges();
    httpMock.expectOne('/api/schedules').flush(BARE_PAGE);
    fixture.detectChanges();

    component.onStatusFilterChange('stopped');
    fixture.detectChanges();

    expect(component.isFilteredEmpty()).toBe(true);
    expect(component.isTerminalStageEmpty()).toBe(true);

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('to reveal hidden rows');
    expect(text).not.toContain('No schedules match your filters');
    expect(text).not.toContain('clear all filters');
  });

  it('genuine filter miss (no matching rows anywhere) keeps the original clear-all copy', () => {
    mount();
    fixture.detectChanges();
    httpMock.expectOne('/api/schedules').flush(BARE_PAGE);
    fixture.detectChanges();

    // 'cancelled' matches NOTHING in the bare page (and nothing is
    // hidden) — a real filter miss, not a terminal-stage artifact.
    component.onStatusFilterChange('cancelled');
    fixture.detectChanges();

    expect(component.isFilteredEmpty()).toBe(true);
    expect(component.isTerminalStageEmpty()).toBe(false);

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('No schedules match your filters');
    expect(text).toContain('clear all filters');
    expect(text).not.toContain('to reveal hidden rows');
  });
});
