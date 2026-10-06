import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { of, throwError, Subject } from 'rxjs';
import { Clipboard } from '@angular/cdk/clipboard';
import { MatSnackBar } from '@angular/material/snack-bar';

import { SnapshotDetailDrawerComponent } from './snapshot-detail-drawer.component';
import { SnapshotService } from '../../services/snapshot.service';
import {
  SnapshotDetailResponse,
  SnapshotRow,
} from '../../models/snapshot.model';

// ── Factories ────────────────────────────────────────────────

/** Lean row base for the detail payload. */
function makeDetailRow(overrides: Partial<SnapshotRow> = {}): SnapshotRow {
  return {
    id: 'snap-uuid-1',
    project_id: 'proj-uuid-1',
    created_by_agent_id: 'coder',
    target_instance_id: 'inst-uuid-1',
    title: 'version-pump',
    domain_tags: ['domain:api', 'runtime:py'],
    status: 'active',
    supersedes_snapshot_id: null,
    git_sha: 'a3c7d2e1f908',
    git_branch: 'feature/test',
    git_dirty: false,
    repo_path: '/tmp/repo',
    runtime_version: 'v0.17.0',
    effective_model: 'gpt-4o',
    created_at: '2026-10-05T18:54:12.000Z',
    ...overrides,
  };
}

/** Detail payload (extends row with `task_summary` and `digest`). */
function makeDetail(
  overrides: Partial<SnapshotDetailResponse> = {},
): SnapshotDetailResponse {
  return {
    ...makeDetailRow(overrides),
    task_summary: 'Refactor the auth module',
    digest: {},
    ...overrides,
  };
}

// ── Suite ───────────────────────────────────────────────────

describe('SnapshotDetailDrawerComponent', () => {
  let fixture: ComponentFixture<SnapshotDetailDrawerComponent>;
  let component: SnapshotDetailDrawerComponent;
  let mockSnapshotService: {
    getById: jest.Mock;
  };
  let clipboard: { copy: jest.Mock };
  let snackBar: { open: jest.Mock };

  beforeEach(async () => {
    mockSnapshotService = {
      getById: jest.fn().mockImplementation((_id: string, opts: { includeDigest: boolean }) =>
        of(makeDetail({ digest: opts.includeDigest ? { marker: 'X', large: 'Y' } : {} })),
      ),
    };
    clipboard = { copy: jest.fn() };
    snackBar = { open: jest.fn() };

    await TestBed.configureTestingModule({
      imports: [SnapshotDetailDrawerComponent],
      providers: [
        provideNoopAnimations(),
        { provide: SnapshotService, useValue: mockSnapshotService },
        { provide: Clipboard, useValue: clipboard },
        { provide: MatSnackBar, useValue: snackBar },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(SnapshotDetailDrawerComponent);
    component = fixture.componentInstance;
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  // ── (a) renders 7 sections after the detail fetch succeeds
  it('(a) renders 7 sections (D-5: warm-spawn section OMITTED) after the detail fetch', async () => {
    fixture.componentRef.setInput('snapshotId', 'snap-uuid-1');
    fixture.detectChanges();
    // Wait for the effect to fire and the response to resolve
    await Promise.resolve();
    await Promise.resolve();
    fixture.detectChanges();

    const compiled = fixture.nativeElement as HTMLElement;
    const sections = compiled.querySelectorAll('[data-test="drawer-section"]');
    expect(sections.length).toBe(7);
    // First 6 + Context — D-5: no warm-spawn section
    const headings = Array.from(sections).map(
      (s) => s.querySelector('h3')?.textContent?.trim() ?? '',
    );
    expect(headings).toEqual([
      'Task summary',
      'Git anchor',
      'Runtime / Model',
      'Supersedes chain',
      'Tags',
      'Timestamps',
      'Context',
    ]);
  });

  // ── (b) clicking Copy ID calls Clipboard.writeText with the detail id
  it('(b) clicking the Copy ID button writes the snapshot id to the clipboard', async () => {
    fixture.componentRef.setInput('snapshotId', 'snap-uuid-1');
    fixture.detectChanges();
    await Promise.resolve();
    await Promise.resolve();
    fixture.detectChanges();

    component.onCopyId();
    expect(clipboard.copy).toHaveBeenCalledWith('snap-uuid-1');
  });

  // ── (c) clicking Supersedes emits navigateToPredecessor
  it('(c) clicking the Supersedes link emits navigateToPredecessor with the id', async () => {
    fixture.componentRef.setInput('snapshotId', 'snap-uuid-1');
    fixture.detectChanges();
    await Promise.resolve();
    await Promise.resolve();
    fixture.detectChanges();

    // The default factory has supersedes_snapshot_id = null; flip it.
    component.detail.set(makeDetail({ supersedes_snapshot_id: 'prev-snap-uuid' }));

    const spy = jest.fn();
    component.navigateToPredecessor.subscribe(spy);
    component.onSupersedesClick();
    expect(spy).toHaveBeenCalledWith('prev-snap-uuid');
  });

  // ── (d) clicking Close emits close
  it('(d) clicking Close emits the close output', () => {
    const spy = jest.fn();
    component.close.subscribe(spy);
    component.onClose();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  // ── (e) dirty badge visible when git_dirty === true
  it('(e) renders the dirty badge when detail().git_dirty is true', async () => {
    fixture.componentRef.setInput('snapshotId', 'snap-uuid-1');
    fixture.detectChanges();
    await Promise.resolve();
    await Promise.resolve();

    component.detail.set(makeDetail({ git_dirty: true }));
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('.dirty-flag')).not.toBeNull();
  });

  it('(e-extra) no dirty badge when git_dirty is false', async () => {
    fixture.componentRef.setInput('snapshotId', 'snap-uuid-1');
    fixture.detectChanges();
    await Promise.resolve();
    await Promise.resolve();
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('.dirty-flag')).toBeNull();
  });

  // ── (f) loading state — skeleton renders while the detail fetch is in flight
  it('(f) renders the loading skeleton while the detail fetch is in flight (no detail yet)', () => {
    // Override the mock to never resolve during this test
    mockSnapshotService.getById.mockReturnValueOnce(new Subject<SnapshotDetailResponse>().asObservable());
    fixture.componentRef.setInput('snapshotId', 'snap-uuid-1');
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('.loading-block')).not.toBeNull();
    expect(component.detail()).toBeNull();
  });

  // ── (g) drawer fetch error renders the error block + Retry recovers
  it('(g) detail fetch error renders the error block; Retry re-fires the fetch and recovers', async () => {
    // First call errors, second call succeeds
    mockSnapshotService.getById
      .mockReturnValueOnce(throwError(() => ({ message: 'boom' })))
      .mockReturnValueOnce(of(makeDetail()));

    fixture.componentRef.setInput('snapshotId', 'snap-uuid-1');
    fixture.detectChanges();
    await Promise.resolve();
    await Promise.resolve();
    fixture.detectChanges();

    expect(component.detailError()).toContain('boom');
    const compiled = fixture.nativeElement as HTMLElement;
    const errBlock = compiled.querySelector('[data-test="drawer-error"]');
    expect(errBlock).not.toBeNull();

    // Click Retry → second call succeeds
    const retryBtn = compiled.querySelector(
      '[data-test="drawer-retry"]',
    ) as HTMLButtonElement | null;
    retryBtn?.click();
    await Promise.resolve();
    await Promise.resolve();
    fixture.detectChanges();

    expect(component.detail()).toBeTruthy();
    expect(component.detailError()).toBeNull();
  });

  // ── (h) lazy digest — NO request fires on drawer open; fires ONLY on Show-digest click
  it('(h) does NOT request include=digest on drawer open; fires only on Show-digest click', async () => {
    fixture.componentRef.setInput('snapshotId', 'snap-uuid-1');
    fixture.detectChanges();
    await Promise.resolve();
    await Promise.resolve();
    fixture.detectChanges();

    // Only the includeDigest: false call should have fired on open.
    const callsOnOpen = mockSnapshotService.getById.mock.calls.length;
    expect(callsOnOpen).toBe(1);
    expect(mockSnapshotService.getById.mock.calls[0][1]).toEqual({
      includeDigest: false,
    });

    component.onToggleDigest();
    await Promise.resolve();
    await Promise.resolve();
    expect(mockSnapshotService.getById.mock.calls.length).toBe(2);
    expect(mockSnapshotService.getById.mock.calls[1][1]).toEqual({
      includeDigest: true,
    });
  });

  it('(h-extra) second click on the digest toggle does NOT re-fetch (cache hit)', async () => {
    fixture.componentRef.setInput('snapshotId', 'snap-uuid-1');
    fixture.detectChanges();
    await Promise.resolve();
    await Promise.resolve();
    fixture.detectChanges();

    component.onToggleDigest();
    await Promise.resolve();
    await Promise.resolve();
    expect(mockSnapshotService.getById.mock.calls.length).toBe(2);

    // Toggle off then on again — the digest signal is non-null, so
    // the click is a cache hit (no new fetch).
    component.onToggleDigest();
    expect(component.showDigest()).toBe(false);
    component.onToggleDigest();
    expect(mockSnapshotService.getById.mock.calls.length).toBe(2);
  });

  // ── (i) digest-fetch error renders the inline error block with Retry
  it('(i) digest fetch error renders an inline error block with Retry', async () => {
    mockSnapshotService.getById
      .mockReturnValueOnce(of(makeDetail())) // detail load
      .mockReturnValueOnce(throwError(() => ({ message: 'digest boom' }))); // digest load fails

    fixture.componentRef.setInput('snapshotId', 'snap-uuid-1');
    fixture.detectChanges();
    await Promise.resolve();
    await Promise.resolve();
    fixture.detectChanges();

    component.onToggleDigest();
    await Promise.resolve();
    await Promise.resolve();
    fixture.detectChanges();

    expect(component.digestError()).toContain('digest boom');
  });

  // ── (j) 200KB digest guard — too-large message + Copy still works
  it('(j) 200KB digest guard renders the too-large message and Copy still copies the raw JSON', async () => {
    fixture.componentRef.setInput('snapshotId', 'snap-uuid-1');
    fixture.detectChanges();
    // Let the constructor effect fire and resolve before we override
    // the digest signal (the effect resets digest to null on every
    // snapshotId change).
    await Promise.resolve();
    await Promise.resolve();
    fixture.detectChanges();

    const big: Record<string, string> = {};
    for (let i = 0; i < 20000; i++) {
      big[`key_${i}`] = 'x'.repeat(20);
    }
    component.digest.set(big);
    fixture.detectChanges();

    expect(component.digestTooLarge()).toBe(true);

    component.onCopyDigest();
    expect(clipboard.copy).toHaveBeenCalledTimes(1);
    // The copied value is the pretty-printed JSON, regardless of the
    // render guard.
    const copied = clipboard.copy.mock.calls[0][0] as string;
    expect(copied.length).toBeGreaterThan(200 * 1024);
    expect(copied).toContain('"key_0"');
  });

  it('(j-extra) under-guard digest renders the <pre> element', async () => {
    fixture.componentRef.setInput('snapshotId', 'snap-uuid-1');
    fixture.detectChanges();
    await Promise.resolve();
    await Promise.resolve();
    fixture.detectChanges();

    component.digest.set({ small: 'value', n: 42 });
    component.showDigest.set(true); // open the digest section
    fixture.detectChanges();
    expect(component.digestTooLarge()).toBe(false);
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('[data-test="digest-pre"]')).not.toBeNull();
  });
});
