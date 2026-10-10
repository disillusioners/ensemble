import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { InteractivityChecker } from '@angular/cdk/a11y';
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
        // (k.1b) jsdom computes no layout, so CDK's InteractivityChecker
        // reports EVERY element as invisible (zero getClientRects
        // geometry) and the focus trap finds zero tabbable elements.
        // The behavioral Tab-cycle test overrides the checker with
        // geometry-free logic so the trap's REAL wrap mechanics (the
        // boundary anchors' focus listeners + the
        // _getFirst/LastTabbableElement DOM walk) execute against the
        // actual drawer DOM. Visibility probing only — nothing else.
        {
          provide: InteractivityChecker,
          useValue: {
            isVisible: () => true,
            isDisabled: (el: HTMLElement) => el.hasAttribute('disabled'),
            isTabbable: (el: HTMLElement) => el.tabIndex >= 0,
            isFocusable: (el: HTMLElement) =>
              el.tabIndex >= 0 && !el.hasAttribute('disabled'),
          },
        },
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

  // ── (k) B2 BLOCKER (conformance r1, AC-A11Y-3) + R3 conformance ──
  // The reviewer's round-1 verdict overturned v1's PARTIAL to FAIL:
  // v1's `mode="over"` provided Esc/trap behavior by Material design,
  // but v2's `mode="side"` does not. The fix (as amended in R3):
  //   1. cdkTrapFocus on the drawer ROOT wrapper (R3-1 — the Tab
  //      cycle covers header AND body; the old header-only placement
  //      left every .drawer-body focusable outside the trap);
  //   2. cdkFocusInitial on the close button (focus lands on close
  //      when the trap activates);
  //   3. component-scoped @HostListener('keydown.escape') on the
  //      component host (R3-2 — Esc closes the drawer via the
  //      existing `close` output; scoping to the host means Esc
  //      pressed on CDK-overlay content, e.g. a mat-menu popover,
  //      never bubbles through the drawer and cannot double-close it);
  //   4. Constructor captures `document.activeElement` (the row that
  //      was clicked to open the drawer); DestroyRef.onDestroy
  //      restores focus on it (works for ALL close paths).

  // (k.1) cdkTrapFocus directive anchors the focus trap on the drawer
  //       ROOT wrapper (R3-1). Angular's directive selector
  //       reflection lowercases the attribute name to `cdktrapfocus`
  //       on the element. Without the directive — or with the old
  //       header-only placement — Tab would leak out of the cycle or
  //       skip the body entirely, defeating AC-A11Y-3.
  it('(k.1) cdkTrapFocus anchors the focus trap on the drawer ROOT wrapper, not the header (R3-1)', async () => {
    fixture.componentRef.setInput('snapshotId', 'snap-uuid-1');
    fixture.detectChanges();
    await Promise.resolve();
    await Promise.resolve();
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    const drawerRoot = compiled.querySelector('.snapshot-drawer');
    expect(drawerRoot).not.toBeNull();
    // Angular's template parser lowercases directive-attribute names
    // that are also DOM attribute selectors — `cdkTrapFocus` reflects
    // as `cdktrapfocus` (no dashes). The CDK runtime is
    // case-insensitive on attribute matching, so the runtime trap
    // still fires correctly in real browsers.
    expect(drawerRoot?.hasAttribute('cdktrapfocus')).toBe(true);
    // R3-1 regression guard: the header must NOT carry the trap — the
    // old header-only placement left every .drawer-body focusable
    // outside the Tab cycle.
    const drawerHeader = compiled.querySelector('.drawer-header');
    expect(drawerHeader).not.toBeNull();
    expect(drawerHeader?.hasAttribute('cdktrapfocus')).toBe(false);
  });

  // (k.1b) BEHAVIORAL Tab-cycle test (R3-1; spec AC-A11Y-3: "Tab
  //        cycles within the drawer header + body links/buttons").
  //
  //        Mechanics (documented per the reviewer's engineering
  //        note): jsdom implements no keyboard-focus traversal, so a
  //        synthetic Tab keydown can never move focus — and CDK 21's
  //        trap has NO keydown handler either. Its wrap mechanism is
  //        two hidden anchor divs (`cdk-focus-trap-anchor`) inserted
  //        as DOM siblings immediately BEFORE and AFTER the trap
  //        element; each listens for `focus` and pulls focus back
  //        inside (`focusLastTabbableElement` /
  //        `focusFirstTabbableElement`). In a real browser the native
  //        Tab from the trap's last focusable lands on the END anchor
  //        (the next tabbable in DOM order) and the listener wraps
  //        focus back into the drawer. We simulate exactly that
  //        traversal by focusing the anchors programmatically after
  //        attaching the fixture host to the document (jsdom fires
  //        `focus` events synchronously on `.focus()` for in-document
  //        elements), exercising the trap's REAL wrap path. The
  //        suite's InteractivityChecker override supplies the
  //        geometry-free visibility logic jsdom cannot compute.
  it('(k.1b) BEHAVIORAL: Tab-cycle wraps inside the drawer — body focusables reachable, no escape (R3-1, AC-A11Y-3)', async () => {
    // Detail WITH a predecessor link AND an open digest, so the body
    // carries >= 3 focusables (predecessor, digest toggle, digest copy).
    mockSnapshotService.getById.mockImplementation(() =>
      of(makeDetail({ supersedes_snapshot_id: 'prev-snap-uuid' })),
    );
    fixture.componentRef.setInput('snapshotId', 'snap-uuid-1');
    fixture.detectChanges();
    await Promise.resolve();
    await Promise.resolve();
    fixture.detectChanges();

    component.digest.set({ small: 'value' });
    component.showDigest.set(true);
    fixture.detectChanges();

    const compiled = fixture.nativeElement as HTMLElement;
    // jsdom only fires focus events for elements in the document —
    // attach the host for the wrap simulation, remove at the end.
    document.body.appendChild(compiled);
    const drawerRoot = compiled.querySelector('.snapshot-drawer')!;
    expect(drawerRoot).not.toBeNull();

    const predecessorBtn = compiled.querySelector<HTMLButtonElement>(
      '[data-test="drawer-predecessor"]',
    );
    const digestToggleBtn = compiled.querySelector<HTMLButtonElement>(
      '[data-test="drawer-digest-toggle"]',
    );
    const digestCopyBtn = compiled.querySelector<HTMLButtonElement>(
      '[data-test="digest-copy"]',
    );
    const copyIdBtn = compiled.querySelector<HTMLButtonElement>(
      '[data-test="drawer-copy-id"]',
    );
    expect(predecessorBtn).not.toBeNull();
    expect(digestToggleBtn).not.toBeNull();
    expect(digestCopyBtn).not.toBeNull();
    expect(copyIdBtn).not.toBeNull();

    // (1) Reachability: each body focusable can receive focus in the
    // cycle (sequential focus() — jsdom has no Tab traversal).
    for (const el of [predecessorBtn, digestToggleBtn, digestCopyBtn]) {
      el!.focus();
      expect(document.activeElement).toBe(el);
    }

    // (2) The trap's hidden boundary anchors exist around the ROOT
    // element (start before it, end after it).
    const anchors = compiled.querySelectorAll<HTMLElement>(
      '.cdk-focus-trap-anchor',
    );
    expect(anchors.length).toBe(2);

    // (3) Tab off the END of the trap (from the last body focusable,
    // as a real browser's Tab would land on the end anchor): focus
    // must wrap BACK INSIDE the drawer to the FIRST tabbable (the
    // header's Copy ID button) — never escape `.snapshot-drawer`.
    digestCopyBtn!.focus();
    anchors[1].focus();
    expect(document.activeElement).toBe(copyIdBtn);
    expect(drawerRoot.contains(document.activeElement)).toBe(true);

    // (4) Shift-Tab off the START of the trap: the start anchor wraps
    // focus to the LAST tabbable — the digest copy button in the
    // body — again staying inside the drawer.
    anchors[0].focus();
    expect(document.activeElement).toBe(digestCopyBtn);
    expect(drawerRoot.contains(document.activeElement)).toBe(true);

    // Clean up the test DOM.
    compiled.remove();
  });

  // (k.2) cdkFocusInitial is stamped on the close button — when the
  //       trap activates, focus lands on the close button, satisfying
  //       the "focus trapped in drawer" half of AC-A11Y-3. Angular's
  //       template parser lowercases the attribute name to
  //       `cdkfocusinitial` (no dashes) when reflecting it to the DOM.
  it('(k.2) cdkFocusInitial is stamped on the close button (initial focus point)', async () => {
    fixture.componentRef.setInput('snapshotId', 'snap-uuid-1');
    fixture.detectChanges();
    await Promise.resolve();
    await Promise.resolve();
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    const closeBtn = compiled.querySelector<HTMLButtonElement>(
      '[data-test="drawer-close"]',
    );
    expect(closeBtn).not.toBeNull();
    // Angular lowercases directive-attribute names that are also DOM
    // attribute selectors — the literal token `cdkFocusInitial` in the
    // template reflects as `cdkfocusinitial` on the element. The CDK
    // runtime queries via `[cdkFocusInitial]` which is case-insensitive
    // for HTML attribute matching, so both forms resolve to the same
    // element. We assert the literal lowercased form here.
    expect(closeBtn?.hasAttribute('cdkfocusinitial')).toBe(true);
  });

  // (k.3) Pressing Escape INSIDE the drawer emits the `close` output
  //       (AC-A11Y-3). R3-2: the listener is component-scoped, so the
  //       event must originate inside the drawer subtree and bubble
  //       up to the component host — the old document-level dispatch
  //       deliberately no longer reaches it.
  it('(k.3) Esc keypress inside the drawer emits the close output (AC-A11Y-3, R3-2)', async () => {
    fixture.componentRef.setInput('snapshotId', 'snap-uuid-1');
    fixture.detectChanges();
    await Promise.resolve();
    await Promise.resolve();
    fixture.detectChanges();

    const closeSpy = jest.fn();
    component.close.subscribe(closeSpy);

    // Dispatch a real Escape keydown from INSIDE the drawer subtree —
    // @HostListener('keydown.escape') registers on the component
    // host, so only events bubbling up from descendants reach it.
    const header = (fixture.nativeElement as HTMLElement).querySelector(
      '.drawer-header',
    );
    expect(header).not.toBeNull();
    header!.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'escape', bubbles: true }),
    );
    expect(closeSpy).toHaveBeenCalledTimes(1);
  });

  // (k.4) Esc is SCOPED to the drawer subtree (R3-2). The old
  //       document-level listener closed the drawer whenever ANY Esc
  //       fired — including the Esc that only closed an unrelated
  //       mat-menu popover (info/metrics/status/sort; their CDK
  //       overlay content mounts OUTSIDE the drawer host subtree,
  //       under document.body). Reworked from the removed
  //       `isDrawerMode` suppression gate (dead input — the sole
  //       call site hardcoded `true`). An Escape dispatched on
  //       document.body must NOT emit close; an Escape from inside
  //       the drawer subtree must.
  it('(k.4) Esc outside the drawer subtree (popover close) does NOT close the drawer; inside does (R3-2)', async () => {
    fixture.componentRef.setInput('snapshotId', 'snap-uuid-1');
    fixture.detectChanges();
    await Promise.resolve();
    await Promise.resolve();
    fixture.detectChanges();

    // Attach the drawer host to the document so the negative
    // assertion is honest: an Esc on document.body travels nowhere
    // near the host even though the host IS in the document (events
    // bubble UP toward the root, never back down into sibling
    // subtrees — which is exactly how CDK overlay popover content
    // behaves in production).
    const compiled = fixture.nativeElement as HTMLElement;
    document.body.appendChild(compiled);

    const closeSpy = jest.fn();
    component.close.subscribe(closeSpy);

    // OUTSIDE: Esc on document.body — the exact topology of closing
    // one of the page popovers (overlay content lives outside the
    // drawer subtree). Under the old document-level listener this
    // closed the drawer too (the R3-2 bug).
    document.body.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'escape', bubbles: true }),
    );
    expect(closeSpy).not.toHaveBeenCalled();

    // INSIDE: Esc on a drawer body element bubbles up to the host
    // and closes the drawer.
    const drawerBody = compiled.querySelector('.drawer-body');
    expect(drawerBody).not.toBeNull();
    drawerBody!.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'escape', bubbles: true }),
    );
    expect(closeSpy).toHaveBeenCalledTimes(1);

    // Clean up the test DOM.
    compiled.remove();
  });

  // (k.5) On teardown, focus is restored to the previously-focused
  //       element (the row that opened the drawer). We simulate the
  //       row by appending an HTMLElement to document.body and
  //       putting focus on it BEFORE the drawer is instantiated —
  //       the constructor captures the active element synchronously.
  //       NOTE: with the suite's InteractivityChecker stub (jsdom has
  //       no layout), cdkTrapFocus's auto-capture DOES move focus to
  //       the close button during detectChanges — irrelevant here:
  //       the constructor captured the row SYNCHRONOUSLY before any
  //       of that, and on teardown both the trap's restore and the
  //       component's DestroyRef restore target the same captured row.
  it('(k.5) on component teardown the captured previously-focused element receives focus (AC-A11Y-3)', async () => {
    // Create a fake "row" element and put focus on it BEFORE the
    // drawer is instantiated — the constructor captures it.
    const fakeRow = document.createElement('button');
    fakeRow.id = 'fake-row-opener';
    fakeRow.textContent = 'previous-row';
    document.body.appendChild(fakeRow);
    fakeRow.focus();
    expect(document.activeElement).toBe(fakeRow);

    // Now mount the drawer; its constructor captures fakeRow into
    // `previouslyFocusedElement` (B2 implementation).
    fixture.componentRef.setInput('snapshotId', 'snap-uuid-1');
    fixture.detectChanges();
    await Promise.resolve();
    await Promise.resolve();
    fixture.detectChanges();

    // Trigger the teardown path. In the real flow the host destroys
    // this component when `close` is emitted; here we directly call
    // `fixture.destroy()` to invoke DestroyRef hooks synchronously.
    fixture.destroy();

    // After teardown, the captured element must receive focus — this
    // is the AC-A11Y-3 "focus returns to the previously-selected
    // row" requirement.
    expect(document.activeElement).toBe(fakeRow);

    // Clean up the test DOM.
    fakeRow.remove();
  });

  // (k.6) The focus restoration is a no-op when no element was
  //       captured (defensive guard — the drawer might be rendered in
  //       a context where the active element was BODY).
  it('(k.6) teardown does not throw when no previously-focused element was captured', () => {
    // Default fixture: focus is body, not a row.
    fixture.componentRef.setInput('snapshotId', 'snap-uuid-1');
    fixture.detectChanges();

    // destroyRef.onDestroy callback must not error out.
    expect(() => fixture.destroy()).not.toThrow();
  });
});
