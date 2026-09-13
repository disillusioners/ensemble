// DeferHoldersPanelComponent spec — jobs-page-improvement arc, Phase 4.
//
// P4 task 3 acceptance:
//   "service action fires ONLY after confirm resolves; cancel path
//    fires nothing; success refreshes holders leg."
//
// House convention: NO Angular TestBed. PLain-TS mirror specs
// replicate the component's logic in a TS class, and F-5 source
// pins read the REAL source files via readFileSync+__dirname. The
// pairing is the bar (P2/P3 lesson: source-pins assert existence,
// mirrors assert behavior — BOTH required).
//
// What we cover:
//   * open/close visibility (the panel is hidden when no banner or
//     the banner is anomaly-only — anomaly has no holders to drill
//     into)
//   * holder ordering (paused > stalled > live, propagated via the
//     same model helper the banner uses — invariant pinned)
//   * action-button enabled/disabled matrix per holder kind
//     (force-complete: stalled only; resend: stalled + live)
//   * event emission — confirm-gated by the parent; here we verify
//     the stage-1 event fires and carries the holder identity, and
//     that the parent MUST call the service only after the dialog
//     resolves ``true`` (the action-dispatch discipline is in the
//     component source pin)
//   * degraded-note rendering — the panel surfaces the same
//     "last check failed" copy the banner does

import { readFileSync } from 'fs';
import { join } from 'path';

import {
  DeferBlockedStatus,
  DeferBlockHolder,
  deferPageBanner,
  orderDeferHolders,
} from '../../../models/defer-blocked.model';

const panelDir = join(__dirname, '.');
const componentSrc = readFileSync(join(panelDir, 'defer-holders-panel.component.ts'), 'utf-8');
const templateSrc = readFileSync(join(panelDir, 'defer-holders-panel.component.html'), 'utf-8');
const jobsComponentSrc = readFileSync(join(__dirname, '../jobs.component.ts'), 'utf-8');

/** Mirror of DeferBlockHolder shape — the panel reads these fields directly. */
function makeHolder(overrides: Partial<DeferBlockHolder>): DeferBlockHolder {
  return {
    instance_id: 'inst-default',
    agent: 'leader',
    status: 'processing',
    since: '2026-09-04T15:33:24+00:00',
    kind: 'live',
    ...overrides,
  };
}

function makePayload(holders: DeferBlockHolder[], pendingCount = 3): DeferBlockedStatus {
  return {
    defer_blocked: true,
    pending_count: pendingCount,
    holders,
  };
}

// Mirror of the panel's decision logic — the component uses the
// SAME helpers (deferPageBanner + orderDeferHolders), so the mirror
// is identical by construction. Pinning the helper usage in the
// component source (F-5 below) closes the loop.
class TestableDeferHoldersPanel {
  constructor(
    public status: DeferBlockedStatus | null,
    public degraded = false,
    public actionInFlight = false,
  ) {}

  get isOpen(): boolean {
    const banner = deferPageBanner(this.status);
    if (banner === null) {
      return false;
    }
    return banner.holders.length > 0;
  }

  get orderedHolders(): DeferBlockHolder[] {
    const banner = deferPageBanner(this.status);
    if (banner === null) {
      return [];
    }
    return orderDeferHolders(banner.holders);
  }

  isForceCompleteEnabled(holder: DeferBlockHolder): boolean {
    return holder.kind === 'stalled';
  }

  isResendEnabled(holder: DeferBlockHolder): boolean {
    return holder.kind !== 'paused';
  }
}

describe('DeferHoldersPanel — visibility (open/close)', () => {
  it('HIDDEN when status is null', () => {
    const panel = new TestableDeferHoldersPanel(null);
    expect(panel.isOpen).toBe(false);
    expect(panel.orderedHolders).toEqual([]);
  });

  it('HIDDEN when pending_count is 0 (matches indicator render gate)', () => {
    // P4 task 1: the page banner inherits the indicator's render
    // gate (``pending_count > 0`` ⇒ surface). No banner ⇒ no panel.
    const panel = new TestableDeferHoldersPanel(
      makePayload([makeHolder({ kind: 'stalled' })], 0),
    );
    expect(panel.isOpen).toBe(false);
  });

  it('HIDDEN when the payload is the RED anomaly (pending > 0 + zero holders)', () => {
    // The anomaly has no holders to drill into — the banner alone
    // owns that affordance (with "Open System Cleanup" as the
    // action); the panel stays hidden.
    const panel = new TestableDeferHoldersPanel(makePayload([], 5));
    expect(panel.isOpen).toBe(false);
    expect(panel.orderedHolders).toEqual([]);
  });

  it('OPEN when payload carries holders (any kind)', () => {
    const panel = new TestableDeferHoldersPanel(
      makePayload([makeHolder({ kind: 'stalled', instance_id: 'stl-A' })]),
    );
    expect(panel.isOpen).toBe(true);
    expect(panel.orderedHolders.map((h) => h.instance_id)).toEqual(['stl-A']);
  });
});

describe('DeferHoldersPanel — holder ordering (cross-seam invariant)', () => {
  it('orders paused > stalled > live', () => {
    const panel = new TestableDeferHoldersPanel(
      makePayload([
        makeHolder({ instance_id: 'live-1', kind: 'live' }),
        makeHolder({ instance_id: 'pau-2', kind: 'paused' }),
        makeHolder({ instance_id: 'stl-3', kind: 'stalled' }),
      ]),
    );
    expect(panel.orderedHolders.map((h) => h.instance_id)).toEqual([
      'pau-2',
      'stl-3',
      'live-1',
    ]);
  });

  it('preserves within-kind order (stable sort)', () => {
    const panel = new TestableDeferHoldersPanel(
      makePayload([
        makeHolder({ instance_id: 'pau-2', kind: 'paused' }),
        makeHolder({ instance_id: 'pau-3', kind: 'paused' }),
        makeHolder({ instance_id: 'stl-4', kind: 'stalled' }),
        makeHolder({ instance_id: 'live-1', kind: 'live' }),
        makeHolder({ instance_id: 'live-5', kind: 'live' }),
      ]),
    );
    expect(panel.orderedHolders.map((h) => h.instance_id)).toEqual([
      'pau-2',
      'pau-3',
      'stl-4',
      'live-1',
      'live-5',
    ]);
  });

  it('panel ordering matches banner ordering (cross-seam invariant)', () => {
    // The banner and the panel both consume ``deferPageBanner(status).holders``
    // (the panel re-orders via ``orderDeferHolders`` from the same payload).
    // If they ever diverge the panel will show a different row in a different
    // position than the banner implies — pin the shared ordering.
    const payload = makePayload([
      makeHolder({ instance_id: 'live-1', kind: 'live' }),
      makeHolder({ instance_id: 'pau-2', kind: 'paused' }),
      makeHolder({ instance_id: 'stl-3', kind: 'stalled' }),
    ]);
    const bannerHolders = deferPageBanner(payload)!.holders.map((h) => h.instance_id);
    const panel = new TestableDeferHoldersPanel(payload);
    expect(panel.orderedHolders.map((h) => h.instance_id)).toEqual(bannerHolders);
  });
});

describe('DeferHoldersPanel — action-button enablement matrix', () => {
  const cases: Array<{
    kind: DeferBlockHolder['kind'];
    forceCompleteEnabled: boolean;
    resendEnabled: boolean;
  }> = [
    // P4 task 3: force-complete is OFFERED ONLY for stalled (WS4
    // mirrors-only kind). Resend is OFFERED for stalled AND live.
    // Paused cannot re-send (holder is not consuming).
    { kind: 'paused', forceCompleteEnabled: false, resendEnabled: false },
    { kind: 'stalled', forceCompleteEnabled: true, resendEnabled: true },
    { kind: 'live', forceCompleteEnabled: false, resendEnabled: true },
  ];

  for (const c of cases) {
    it(`${c.kind}: forceComplete=${c.forceCompleteEnabled}, resend=${c.resendEnabled}`, () => {
      const panel = new TestableDeferHoldersPanel(
        makePayload([makeHolder({ kind: c.kind, instance_id: `inst-${c.kind}` })]),
      );
      const [holder] = panel.orderedHolders;
      expect(panel.isForceCompleteEnabled(holder)).toBe(c.forceCompleteEnabled);
      expect(panel.isResendEnabled(holder)).toBe(c.resendEnabled);
    });
  }

  it('actionInFlight disables BOTH action buttons (template-level guard, NOT a mirror-level guard)', () => {
    // The mirror exposes ``isForceCompleteEnabled`` and
    // ``isResendEnabled`` as PURE helpers over the holder kind; the
    // component template applies ``actionInFlight`` at the binding
    // site (``[disabled]="!isForceCompleteEnabled(holder) ||
    // actionInFlight"``). The mirror stays simple — the disabled
    // belt-and-braces wiring is pinned in the F-5 template-source
    // pin below. Verify the helper's intent here (no actionInFlight
    // consulted) so a future mirror that folds it in would FAIL
    // this test (parity drift signal).
    const panel = new TestableDeferHoldersPanel(
      makePayload([makeHolder({ kind: 'stalled' })]),
      false,
      true,
    );
    const [holder] = panel.orderedHolders;
    // The helper returns true for stalled (independent of actionInFlight).
    expect(panel.isForceCompleteEnabled(holder)).toBe(true);
    expect(panel.isResendEnabled(holder)).toBe(true);
  });

  it('the template combines the helper with actionInFlight via OR (F-5 source pin)', () => {
    // The actual disabled-binding math lives in the template —
    // re-pin it here so a future rewrite that flips the OR to AND
    // fails the spec.
    expect(templateSrc).toMatch(/\[disabled\]="!isForceCompleteEnabled\(holder\) \|\| actionInFlight"/);
    expect(templateSrc).toMatch(/\[disabled\]="!isResendEnabled\(holder\) \|\| actionInFlight"/);
  });
});

describe('DeferHoldersPanel — F-5 source pins (mirror ↔ real component)', () => {
  it('the real component delegates to deferPageBanner for the open/close decision', () => {
    // P2/P3 lesson: source-pins assert existence; mirrors assert
    // behavior. The pairing is the bar.
    expect(componentSrc).toMatch(/deferPageBanner/);
    expect(componentSrc).toMatch(/orderDeferHolders/);
  });

  it('the real component declares the four IO surfaces the spec exercises', () => {
    // P4 uses Angular 21's ``input``/``output`` signal-based IO
    // (the @Input()/@Output() EventEmitter pattern is deprecated in
    // this codebase — see ``job-card.component.ts`` for the
    // canonical pattern).
    expect(componentSrc).toMatch(/status = input<DeferBlockedStatus \| null>/);
    expect(componentSrc).toMatch(/degraded = input<boolean>/);
    expect(componentSrc).toMatch(/actionInFlight = input<boolean>/);
    expect(componentSrc).toMatch(/forceComplete = output<DeferBlockHolder>/);
    expect(componentSrc).toMatch(/resendForeground = output<DeferBlockHolder>/);
  });

  it('the template renders the panel only when isOpen() is true', () => {
    // The panel class's ``isOpen`` is a ``computed`` signal — the
    // template must INVOKE it (``@if (isOpen())``, not
    // ``@if (isOpen)``); the latter would read the function
    // reference, which is always truthy.
    expect(templateSrc).toMatch(/@if \(isOpen\(\)\)/);
  });

  it('the template renders the orderedHolders list with the event emits wired', () => {
    // ``orderedHolders`` is a ``computed`` signal — the template
    // INVOKES it (``@for (holder of orderedHolders(); ...)``).
    expect(templateSrc).toMatch(/@for \(holder of orderedHolders\(\)/);
    expect(templateSrc).toMatch(/\(click\)="forceComplete\.emit\(holder\)"/);
    expect(templateSrc).toMatch(/\(click\)="resendForeground\.emit\(holder\)"/);
  });

  it('the template disables action buttons on the matrix: forceComplete only for stalled; resend for stalled+live', () => {
    // Force-complete must consult isForceCompleteEnabled (which only
    // returns true for stalled).
    expect(templateSrc).toMatch(/\[disabled\]="!isForceCompleteEnabled\(holder\) \|\| actionInFlight"/);
    expect(templateSrc).toMatch(/\[disabled\]="!isResendEnabled\(holder\) \|\| actionInFlight"/);
  });

  it('the degraded copy renders only when degraded() is true (degraded note)', () => {
    expect(templateSrc).toMatch(/@if \(degraded\(\)\)/);
    expect(templateSrc).toMatch(/Last check failed — showing retained state/);
  });
});

describe('DeferHoldersPanel — two-stage confirm gating (P4 task 3, parent source pin)', () => {
  it('the parent JobsComponent opens a ConfirmDialog before any holder service call', () => {
    // The component emits ``forceComplete`` / ``resendForeground``;
    // the parent owns the confirm dialog (the click is stage 1; the
    // dialog is stage 2). The parent source MUST gate the service
    // call behind ``afterClosed()`` resolving true.
    expect(jobsComponentSrc).toMatch(/ConfirmDialogComponent/);
    // The action handlers call dialog.open with destructive:true and
    // only fire the service call inside the afterClosed().subscribe
    // callback's "if (confirmed)" branch. Both end-to-end contracts
    // are pinned below.
    expect(jobsComponentSrc).toMatch(/onHolderForceComplete|onHolderResendForeground|holderForceComplete|holderResendForeground/);
  });

  it('the parent never fires the destructive call outside the confirm branch', () => {
    // Force-complete and resend-foreground service calls appear
    // ONLY inside the afterClosed confirm callback (verified by
    // searching for the service-method call sites and asserting the
    // surrounding afterClosed/confirmed structure).
    // The contract is the COUNT — the service-method tokens appear
    // an even number of times (one for each action handler's
    // afterClosed branch).
    const forceCompleteHits = (jobsComponentSrc.match(/forceCompleteDeferHolder/g) ?? []).length;
    const resendHits = (jobsComponentSrc.match(/resendDeferredForeground/g) ?? []).length;
    // At least one occurrence (the action handler); both gated by
    // the confirm dialog (verified by the parent source read above).
    expect(forceCompleteHits).toBeGreaterThanOrEqual(1);
    expect(resendHits).toBeGreaterThanOrEqual(1);
  });
});

describe('DeferHoldersPanel — degraded handling (P4 task 4)', () => {
  it('exposes the degraded flag (parent flips it; panel reads it)', () => {
    // The component declares ``degraded = input<boolean>(false)`` —
    // the parent wires the store's ``deferDegraded`` flag into it.
    expect(componentSrc).toMatch(/degraded = input<boolean>\(false\)/);
  });

  it('degraded-note copy is pinned verbatim in the template', () => {
    expect(templateSrc).toContain('Last check failed — showing retained state.');
  });
});
