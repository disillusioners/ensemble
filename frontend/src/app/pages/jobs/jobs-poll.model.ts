// Jobs poll-gate model — jobs-page-improvement arc, Phase 2.
//
// The 30s poll is a single ``setInterval`` tick that consults a
// pure gate BEFORE making the HTTP call. The gate is data-only —
// no DOM, no Angular DI, no timers — so the truth table is
// spec-able by direct call (see jobs-poll.model.spec.ts).
//
// Plan task 5 acceptance (truth table):
//
//   shouldTick(visible, drawerOpen, modalOpen, fetchInFlight)
//
//     visible  drawer  modal  inFlight  result
//     true     false   false  false     true   ← the common case
//     false    *       *      *         false  ← hidden tab pauses
//     true     true    *      *         false  ← drawer open pauses
//     true     *       true   *         false  ← modal open pauses
//     true     *       *      true      false  ← in-flight fetch pauses
//
// Why pause under a drawer / modal: the page is a deep-inspection
// surface with mutating actions (cancel / retry / delete). Replacing
// the list mid-action would yank the user's interaction context
// (the row they were about to click disappears). The pause holds
// the visible list stable for the duration of the action.
//
// Why pause under a hidden tab: refreshing a page the user can't see
// is pure bandwidth waste. The refocus handler performs the
// IMMEDIATE refresh (debounced ≥2s since last fetch — the refocus
// storm mitigation from the plan's risk table).
//
// Why pause during an in-flight fetch: the store already guards
// against double-firing, but the gate model reads ``fetchInFlight``
// so the answer is data-only and spec-able WITHOUT the store.

/** One render-context input to ``shouldTick``. */
export interface PollGateInputs {
  /** ``document.visibilityState === 'visible'`` (NOT focused — only visibility). */
  readonly tabVisible: boolean;
  /** The job-detail drawer is open (any of: ``opened``). */
  readonly drawerOpen: boolean;
  /** A modal dialog is open (the page's ``MatDialog.openDialogs.length > 0``). */
  readonly modalOpen: boolean;
  /** The active-view fetch leg is currently in flight. */
  readonly fetchInFlight: boolean;
}

/**
 * The pure poll gate. Returns ``true`` when the 30s tick is allowed
 * to fire a refresh, ``false`` otherwise. ``tabVisible`` is the
 * ONLY "yes" pre-requisite — every other input is a pause signal.
 */
export function shouldTick(inputs: PollGateInputs): boolean {
  if (!inputs.tabVisible) {
    return false;
  }
  if (inputs.drawerOpen || inputs.modalOpen || inputs.fetchInFlight) {
    return false;
  }
  return true;
}

/**
 * 30s poll cadence (D3, OQ-5 default). Held here so the spec can
 * pin it against the component source text (F-5).
 */
export const POLL_INTERVAL_MS = 30_000;

/**
 * Refocus-storm debounce window. The plan's risk table calls out
 * rapid tab-switching; refocus-refresh fires at most once every
 * ``REFOCUS_DEBOUNCE_MS``. The timer is owned by the component —
 * the constant is data-only so the spec can pin it.
 */
export const REFOCUS_DEBOUNCE_MS = 2_000;