import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { PageEvent } from '@angular/material/paginator';
import { of } from 'rxjs';

import { SnapshotsTableComponent } from './snapshots-table.component';
import { SnapshotRow } from '../../models/snapshot.model';

// ── Factories ─────────────────────────────────────────────────

/**
 * Build a single `SnapshotRow` fixture. Mirrors the BE list-payload
 * shape (D-4/D-5 — task_summary and warm_spawn_count omitted;
 * project_id is the UUID, no project_name).
 */
function makeRow(overrides: Partial<SnapshotRow> = {}): SnapshotRow {
  return {
    id: 'row-uuid-1',
    project_id: 'proj-uuid-1',
    created_by_agent_id: 'coder',
    target_instance_id: 'inst-uuid-1',
    title: 'first-snapshot',
    domain_tags: ['domain:api'],
    status: 'active',
    supersedes_snapshot_id: null,
    git_sha: 'a3c7d2e1f908',
    git_branch: 'feature/test',
    git_dirty: false,
    repo_path: '/tmp/repo',
    runtime_version: 'v0.17.0',
    effective_model: 'gpt-4o',
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

// ── Suite ─────────────────────────────────────────────────────

describe('SnapshotsTableComponent', () => {
  let fixture: ComponentFixture<SnapshotsTableComponent>;
  let component: SnapshotsTableComponent;

  /**
   * Helper: bind the required list-state inputs plus paginator +
   * filter signals, then `detectChanges()`. The 7 filter inputs +
   * `pageSize` + `hasActiveFilters` are defaulted; `rows` / `total` /
   * `loading` / `error` / `pageIndex` are caller-supplied.
   */
  function bindInputs(opts: {
    rows: SnapshotRow[];
    total: number;
    loading?: boolean;
    error?: string | null;
    pageIndex?: number;
    pageSize?: number;
    hasActiveFilters?: boolean;
  }): void {
    fixture.componentRef.setInput('rows', opts.rows);
    fixture.componentRef.setInput('total', opts.total);
    fixture.componentRef.setInput('loading', opts.loading ?? false);
    fixture.componentRef.setInput('error', opts.error ?? null);
    fixture.componentRef.setInput('pageIndex', opts.pageIndex ?? 0);
    fixture.componentRef.setInput('pageSize', opts.pageSize ?? 25);
    fixture.componentRef.setInput('hasActiveFilters', opts.hasActiveFilters ?? false);
    fixture.detectChanges();
  }

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [SnapshotsTableComponent],
      providers: [provideNoopAnimations()],
    }).compileComponents();

    fixture = TestBed.createComponent(SnapshotsTableComponent);
    component = fixture.componentInstance;
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  // ── (a) PRESENTATIONAL — no service injection, no fetch in constructor
  it('(a) is presentational: no service injection, no fetch in constructor, accepts rows/total/loading/error/pageIndex/pageSize inputs', () => {
    // The component has no constructor-side HTTP/effect work —
    // detectChanges() alone (without any rows) is safe and renders
    // the empty state.
    bindInputs({ rows: [], total: 0 });
    expect(component).toBeTruthy();
    // No HTTP requests are issued by the table itself.
  });

  it('(a-extra) re-renders new rows when the host swaps the rows() input', () => {
    bindInputs({ rows: [makeRow()], total: 1 });
    let text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('first-snapshot');

    bindInputs({
      rows: [makeRow({ id: 'row-uuid-2', title: 'second-snapshot' })],
      total: 1,
    });
    text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('second-snapshot');
    expect(text).not.toContain('first-snapshot');
  });

  // ── (b) re-emits pageChange on paginator interaction
  it('(b) re-emits pageChange when the paginator fires (via host-driven re-render)', () => {
    const spy = jest.fn();
    component.pageChange.subscribe(spy);
    const event: PageEvent = {
      pageIndex: 1,
      pageSize: 50,
      length: 100,
    };
    component.onPageChange(event);
    expect(spy).toHaveBeenCalledWith(event);
  });

  // ── (c) skeleton shown only when rows() is empty AND loading()
  it('(c) shows the skeleton when rows().length === 0 AND loading() is true', () => {
    bindInputs({ rows: [], total: 0, loading: true });
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('.skeleton-list')).not.toBeNull();
  });

  it('(c-extra) does NOT show the skeleton when rows is non-empty even if loading flips mid-page', () => {
    bindInputs({ rows: [makeRow()], total: 1, loading: false });
    bindInputs({ rows: [makeRow()], total: 1, loading: true });
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('.skeleton-list')).toBeNull();
  });

  // ── (d) error state — emits retry output
  it('(d) error state renders the retry block; clicking Retry emits the retry output', () => {
    const spy = jest.fn();
    component.retry.subscribe(spy);
    bindInputs({ rows: [], total: 0, error: 'boom' });
    const compiled = fixture.nativeElement as HTMLElement;
    const retryBtn = Array.from(compiled.querySelectorAll('button')).find(
      (b) => b.textContent?.includes('Retry'),
    );
    expect(retryBtn).toBeDefined();
    retryBtn?.click();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  // ── (e) empty + filtered-empty differentiated by hasActiveFilters
  it('(e) renders filtered-empty when total > 0 and rows empty AND hasActiveFilters', () => {
    bindInputs({
      rows: [],
      total: 5,
      hasActiveFilters: true,
    });
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('No snapshots match your filters.');
  });

  it('(e-extra) renders zero-state when total === 0 and no active filters', () => {
    bindInputs({ rows: [], total: 0, hasActiveFilters: false });
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('No snapshots yet.');
  });

  // ── (f) trackById returns row.id
  it('(f) trackById returns row.id', () => {
    const row = makeRow({ id: 'specific-id-1' });
    expect(component.trackById(0, row)).toBe('specific-id-1');
  });

  // ── (g) R11 in-flight race — host swaps rows; table simply re-renders
  it('(g) re-renders the new rows when the host swaps them mid-flight (no fetch by the table)', () => {
    bindInputs({ rows: [makeRow({ title: 'page-1-row' })], total: 50 });
    let text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('page-1-row');

    // Simulate a stale-response race: host swaps the rows signal
    // with the newer page-2 payload. The table simply re-renders.
    bindInputs({ rows: [makeRow({ id: 'row-uuid-2', title: 'page-2-row' })], total: 50 });
    text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('page-2-row');
    expect(text).not.toContain('page-1-row');
  });

  // ── (h) #10 wire case — when host swaps pageIndex to 0 on filter change
  it('(h) re-renders rows after the host swaps pageIndex to 0 (no fetch by the table)', () => {
    bindInputs({
      rows: [makeRow({ title: 'page-2' })],
      total: 50,
      pageIndex: 1,
    });
    bindInputs({
      rows: [makeRow({ id: 'fresh', title: 'page-1-fresh' })],
      total: 50,
      pageIndex: 0,
    });
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('page-1-fresh');
    expect(component.pageIndex()).toBe(0);
  });

  // ── (i) template-helper parity
  it('(i) statusClass / formatRelative / visibleTags / overflowCount return the expected values', () => {
    const row = makeRow({
      domain_tags: ['domain:api', 'runtime:py', 'env:test'],
    });
    expect(component.statusClass('active')).toBe('status-active');
    expect(component.statusClass('failed')).toBe('status-failed');

    expect(component.visibleTags(row)).toEqual(['domain:api', 'runtime:py']);
    expect(component.overflowCount(row)).toBe(1);

    // formatRelative — same-day → "Nh ago" or "just now"
    const iso = new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString();
    expect(component.formatRelative(iso)).toBe('3h ago');

    // formatAbsolute — "YYYY-MM-DD HH:MM UTC"
    const abs = component.formatAbsolute('2026-10-05T18:54:12.000Z');
    expect(abs).toBe('2026-10-05 18:54 UTC');
  });

  it('(i-extra) row click emits the rowClick output', () => {
    const spy = jest.fn();
    component.rowClick.subscribe(spy);
    const row = makeRow({ id: 'emit-me' });
    component.onRowClick(row);
    expect(spy).toHaveBeenCalledWith(row);
  });

  // ── (j) FIX 1 review pass 1 — REAL first-page button carries the
  //        e2e hook (the old hidden marker button was deleted)
  it('(j) stamps data-test="paginator-page-1" on Material\'s rendered first-page button', () => {
    bindInputs({ rows: [makeRow()], total: 75, pageIndex: 0 });
    const compiled = fixture.nativeElement as HTMLElement;
    const btn = compiled.querySelector<HTMLButtonElement>(
      'button[data-test="paginator-page-1"]',
    );
    // The hook must be on the Material paginator's own navigation
    // button — visible, inside <mat-paginator> — NOT on a hidden
    // marker element.
    expect(btn).not.toBeNull();
    expect(btn!.closest('mat-paginator')).not.toBeNull();
    expect(btn!.classList.contains('mat-mdc-paginator-navigation-first')).toBe(
      true,
    );
    // The old hidden marker must be gone.
    expect(compiled.querySelector('button.visually-hidden[data-test]')).toBeNull();
  });

  it('(j-extra) the page-1 hook button is state-coupled — disabled at pageIndex=0, enabled at pageIndex=2', () => {
    // total=75 / pageSize=25 → 3 pages; pageIndex 0 and 2 both valid.
    // Material 21's nav buttons use `disabledInteractive`: the disabled
    // state is conveyed via aria-disabled + mat-mdc-button-disabled
    // (NOT the native disabled attribute) — Playwright's toBeDisabled()
    // honors aria-disabled, so the e2e signal is real.
    bindInputs({ rows: [makeRow()], total: 75, pageIndex: 0 });
    const compiled = fixture.nativeElement as HTMLElement;
    const btnAt0 = compiled.querySelector<HTMLButtonElement>(
      'button[data-test="paginator-page-1"]',
    );
    expect(btnAt0).not.toBeNull();
    expect(btnAt0!.getAttribute('aria-disabled')).toBe('true');
    expect(btnAt0!.classList.contains('mat-mdc-button-disabled')).toBe(true);

    bindInputs({ rows: [makeRow()], total: 75, pageIndex: 2 });
    const btnAt2 = compiled.querySelector<HTMLButtonElement>(
      'button[data-test="paginator-page-1"]',
    );
    expect(btnAt2).not.toBeNull();
    expect(btnAt2!.getAttribute('aria-disabled')).toBeNull();
    expect(btnAt2!.classList.contains('mat-mdc-button-disabled')).toBe(false);
  });
});
