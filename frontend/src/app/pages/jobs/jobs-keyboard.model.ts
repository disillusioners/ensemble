// Jobs keyboard model — jobs-page-improvement arc, Phase 6 (task 2).
//
// Pure port of the panel's clamped tree navigation
// (``nextInstanceTreeItem``, ``models/instance-node.model.ts``) onto
// the jobs page's flattened ``WindowItem`` list, plus the WAI-ARIA
// tree actions the panel's container handler implements (expand via
// ArrowRight; collapse via ArrowLeft with the nearest-ancestor
// fallback for child rows).
//
// DOM order == keyboard order BY CONSTRUCTION: the list this model
// walks is the SAME ``renderRows()`` array the virtual scroll renders
// (``toWindowItems`` OMITTES collapsed groups' rows), so an arrow key
// can never skip an off-screen row and there is no second ordering to
// drift. The cross-seam invariant (keyboard order == DOM order) is
// pinned in ``jobs-keyboard.model.spec.ts``.
//
// VIRTUAL-SCROLL RECYCLING CONTRACT (the one behavioral difference
// from the panel — pinned in the spec AND in the component source):
// the virtual viewport RECYCLES row DOM, so a focused item scrolled
// out of the rendered range loses its element. Every focus move
// MUST therefore:
//
//   1. ``viewport.scrollToIndex(targetIndex)`` FIRST — bring the
//      target into (or near) the rendered range, and
//   2. re-resolve ``document.getElementById(id)?.focus()`` AFTER the
//      render tick (a ``setTimeout`` macrotask; CDK renders the new
//      range on the scroll-driven tick, not synchronously), with a
//      VIEWPORT-CONTAINER FALLBACK when the id is not yet mounted —
//      keyboard focus must stay inside the list region, never fall
//      to ``<body>``.
//
// The pure model resolves WHICH item a keypress targets; the
// component owns the DOM sequence above (source-pinned).

import type { WindowItem } from './jobs-window.model';

/**
 * Stable DOM id for a flattened item — drives the single-writer
 * ``(focus)`` signal, the real-DOM focus calls, and the ``.focused``
 * class binding (panel parity: ``instanceTreeItemId``).
 *
 * The kind prefix keeps HEADER and ROW namespaces disjoint even if a
 * group key ever collided with a job id (defensive; both are unique
 * today).
 */
export function jobsWindowItemId(item: WindowItem): string {
  return item.kind === 'header' ? `jobs:hdr|${item.groupKey}` : `jobs:row|${item.key}`;
}

/**
 * Move from ``currentIndex`` by ``+1`` (down) or ``-1`` (up). CLAMPS
 * at the ends (no wrap — identical semantics to the panel's
 * ``nextInstanceTreeItem``: wrap in a grouped list reads as a
 * glitch). A ``-1`` index (no focus yet) treats ↑ as last / ↓ as
 * first; an empty list returns ``-1``.
 */
export function nextJobsWindowItem(
  items: ReadonlyArray<WindowItem>,
  currentIndex: number,
  delta: -1 | 1,
): number {
  if (items.length === 0) return -1;
  if (currentIndex < 0 || currentIndex >= items.length) {
    return delta === 1 ? 0 : items.length - 1;
  }
  const next = currentIndex + delta;
  if (next < 0) return 0;
  if (next >= items.length) return items.length - 1;
  return next;
}

/**
 * The action a container-level keypress resolves to.
 *
 * * ``focus``   — move real DOM focus to ``index`` (ArrowUp/Down).
 * * ``expand``  — expand the collapsed group ``groupKey`` (ArrowRight
 *                 on a collapsed header). Focus STAYS on the header:
 *                 the group's rows mount AFTER it in the list.
 * * ``collapse``— collapse the expanded group ``groupKey``
 *                 (ArrowLeft on an expanded header, or on a ROW —
 *                 the row's nearest ancestor header, WAI-ARIA tree
 *                 pattern). ``refocusHeaderId`` is the header id to
 *                 move focus to when the currently-focused ROW is
 *                 removed from the DOM by the collapse (headers
 *                 always render — collapsed groups still show their
 *                 header); ``null`` when focus already sits on the
 *                 header (it survives) or no refocus is needed.
 * * ``none``    — no-op (unknown key, collapsed header with
 *                 ArrowLeft, expanded header / row with ArrowRight,
 *                 no focus yet with ArrowRight/ArrowLeft).
 *
 * Enter / Space are deliberately NOT container actions: like the
 * panel, activation stays on the individual row hosts (native
 * button-key activation on the header chevron already toggles the
 * group) — a container-level Enter handler would double-fire against
 * the native button activation bubbling up from the chevron.
 */
export type JobsKeyAction =
  | { readonly kind: 'focus'; readonly index: number }
  | { readonly kind: 'expand'; readonly groupKey: string }
  | {
      readonly kind: 'collapse';
      readonly groupKey: string;
      readonly refocusHeaderId: string | null;
    }
  | { readonly kind: 'none' };

/**
 * Index of the nearest HEADER at or before ``fromIndex - 1`` — the
 * row's ancestor group in keyboard/DOM order (rows always sit under
 * their header in the flattened list). ``-1`` when none exists.
 */
export function nearestHeaderIndexAbove(
  items: ReadonlyArray<WindowItem>,
  fromIndex: number,
): number {
  const start = Math.min(fromIndex, items.length) - 1;
  for (let i = start; i >= 0; i--) {
    if (items[i]?.kind === 'header') return i;
  }
  return -1;
}

/**
 * Resolve the container-level action for an arrow keypress.
 *
 * ``isGroupExpanded`` is injected by the caller (the component binds
 * it to ``expandedGroupIds().has``) so the model stays pure — the
 * expansion state is component-owned signal state, not model state.
 */
export function resolveJobsKeyAction(
  items: ReadonlyArray<WindowItem>,
  currentIndex: number,
  key: string,
  isGroupExpanded: (groupKey: string) => boolean,
): JobsKeyAction {
  if (items.length === 0) return { kind: 'none' };

  if (key === 'ArrowDown' || key === 'ArrowUp') {
    const delta: -1 | 1 = key === 'ArrowUp' ? -1 : 1;
    return { kind: 'focus', index: nextJobsWindowItem(items, currentIndex, delta) };
  }

  // ArrowRight / ArrowLeft target the focused item (no focus yet →
  // no-op so we don't surprise the user with an unintended toggle —
  // panel parity).
  if (currentIndex < 0 || currentIndex >= items.length) return { kind: 'none' };
  const current = items[currentIndex];

  if (key === 'ArrowRight') {
    if (current.kind !== 'header') return { kind: 'none' }; // rows: no-op
    const groupKey = current.groupKey;
    if (isGroupExpanded(groupKey)) return { kind: 'none' }; // already open
    return { kind: 'expand', groupKey };
  }

  if (key === 'ArrowLeft') {
    if (current.kind === 'header') {
      if (!isGroupExpanded(current.groupKey)) return { kind: 'none' };
      // The header itself survives the collapse — no refocus.
      return { kind: 'collapse', groupKey: current.groupKey, refocusHeaderId: null };
    }
    // Row: collapse the NEAREST ANCESTOR header (the header directly
    // above the row in the flattened list). The row's DOM slot is
    // removed by the collapse, so focus must land on a row that
    // still exists — the header.
    const headerIndex = nearestHeaderIndexAbove(items, currentIndex);
    if (headerIndex < 0) return { kind: 'none' }; // defensive: orphan row
    const header = items[headerIndex];
    if (header.kind !== 'header') return { kind: 'none' }; // unreachable
    if (!isGroupExpanded(header.groupKey)) return { kind: 'none' }; // defensive
    return {
      kind: 'collapse',
      groupKey: header.groupKey,
      refocusHeaderId: jobsWindowItemId(header),
    };
  }

  return { kind: 'none' };
}

/**
 * True iff ``key`` is a row-level ACTIVATE key (Enter / Space). The
 * row hosts bind this via their own ``(keydown.enter)`` /
 * ``(keydown.space)`` handlers — NOT at the container level (see the
 * ``JobsKeyAction`` doc: container-level Enter would double-fire
 * against the chevron's native button activation).
 */
export function isJobsActivateKey(key: string): boolean {
  return key === 'Enter' || key === ' ';
}
