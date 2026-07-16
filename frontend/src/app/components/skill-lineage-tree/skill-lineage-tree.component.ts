import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
  input,
  output,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatDialog } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { SkillLineage, SkillLineageNode } from '../../models/skill.model';
import { MermaidGraphComponent } from '../mermaid-graph/mermaid-graph.component';
import {
  SkillLineageNodeDialogComponent,
  SkillLineageNodeDialogData,
} from '../skill-lineage-node-dialog/skill-lineage-node-dialog.component';

/**
 * Pure function — exported so the unit suite can exercise it without
 * needing to render the component. Generates a Mermaid ``graph TD``
 * source string for a given ``SkillLineage``.
 *
 * Conventions baked in (mirroring the planning doc):
 *
 * * **Node IDs**: never raw skill names / UUIDs. Use ``node{index}``
 *   (e.g. ``node0``, ``node1``). A side map of ``mermaidId → skillId``
 *   is required at the call site (the component owns one) so DOM
 *   click handlers can recover the underlying skill id from the
 *   Mermaid-generated DOM-id (which looks like
 *   ``flowchart-node0-12``).
 * * **Labels**: ``"Skill Name (Gen N) — status"``. Pipes / quotes /
 *   newlines are sanitized to keep the parser happy.
 * * **Edges**: parents point INTO the current node; the current
 *   node points INTO each child. Edge labels are the
 *   ``change_summary`` (truncated to ~40 chars, ``"…"`` suffix),
 *   with ``"Auto-evolved"`` as the fallback for empty summaries.
 * * **classDef** statements cover ``.origin``, ``.current``,
 *   ``.active``, ``.inactive``, ``.deprecated``. Origin is the
 *   lineage's oldest ancestor (typically ``lineage.origin``).
 *
 * Returns the empty string when the lineage has no parents AND no
 * children so the component can detect the empty-state case and
 * render its "no evolution history" message instead.
 */
export function buildLineageGraph(
  lineage: SkillLineage,
  currentSkillId: string,
): string {
  if (!lineage || (lineage.parents.length === 0 && lineage.children.length === 0)) {
    return '';
  }

  // Stable iteration order — the rendered graph must be deterministic
  // so screenshots / diffs don't change between renders with the same
  // inputs. Parents ordered by ascending generation (oldest first,
  // since Mermaid ``TD`` flows top-down). Children ordered by
  // ascending generation (newest last).
  const parents = [...lineage.parents].sort(
    (a, b) => (a.generation ?? 0) - (b.generation ?? 0),
  );
  const children = [...lineage.children].sort(
    (a, b) => (a.generation ?? 0) - (b.generation ?? 0),
  );

  // Build the deduplicated node list. Parents come first (top of the
  // graph), then a synthetic "current" placeholder (rendered with a
  // Mermaid node id too), then children. The synthetic entry is
  // needed because the current skill itself is not in
  // ``parents`` / ``children`` — the lineage payload doesn't echo
  // it back, but the visual tree needs a node for "you are here".
  type NodeEntry = {
    mermaidId: string;
    node: SkillLineageNode;
    isCurrent: boolean;
  };

  const entries: NodeEntry[] = [];
  let counter = 0;

  for (const parent of parents) {
    entries.push({ mermaidId: `node${counter++}`, node: parent, isCurrent: false });
  }

  // Synthetic current-node entry — the actual ``SkillLineageNode`` is
  // not present in the payload, so we synthesize a stand-in using the
  // lineage's top-level fields (``generation`` / ``origin`` are the
  // current skill's own values). The id matches ``currentSkillId``
  // so the DOM → skill mapping is unambiguous.
  const currentPlaceholder: SkillLineageNode = {
    id: currentSkillId,
    project_id: null,
    name: currentSkillId,
    description: '',
    category: '',
    is_active: true,
    status: 'active',
    lineage_origin: lineage.origin ?? currentSkillId,
    generation: lineage.generation ?? 0,
    ab_test_group: null,
    auto_load: false,
    source_skill_bank_id: null,
    total_selections: 0,
    total_applied: 0,
    total_completions: 0,
    total_fallbacks: 0,
    consecutive_failures: 0,
    created_at: '',
    updated_at: '',
    last_used_at: null,
    change_summary: '',
    content_diff: '',
    edge_created_at: undefined,
  };
  entries.push({
    mermaidId: `node${counter++}`,
    node: currentPlaceholder,
    isCurrent: true,
  });

  for (const child of children) {
    entries.push({ mermaidId: `node${counter++}`, node: child, isCurrent: false });
  }

  // Compute origin: the oldest parent (or current if no parents).
  // ``lineage.origin`` is the root id; we use it as a hint and fall
  // back to the earliest parent by generation.
  const oldestAncestor = parents.length > 0 ? parents[0] : null;
  const originSkillId = (lineage.origin ?? oldestAncestor?.id ?? currentSkillId).trim();

  // ── classDef statements ─────────────────────────────────────────────
  const classDefLines: string[] = [
    '  classDef origin fill:#1e3a8a,stroke:#3b82f6,stroke-width:2px,color:#e2e8f0;',
    '  classDef current fill:#7c3aed,stroke:#a78bfa,stroke-width:3px,color:#f8fafc;',
    '  classDef active fill:#064e3b,stroke:#10b981,stroke-width:1px,color:#d1fae5;',
    '  classDef inactive fill:#1f2937,stroke:#6b7280,stroke-width:1px,color:#9ca3af;',
    '  classDef deprecated fill:#3f1d1d,stroke:#ef4444,stroke-width:1px,color:#fecaca;',
  ];

  // ── node statements + per-node class assignment ────────────────────
  const nodeLines: string[] = [];
  const classAssignments: string[] = [];
  for (const entry of entries) {
    const label = formatNodeLabel(entry.node);
    nodeLines.push(`  ${entry.mermaidId}["${label}"]`);
    if (entry.isCurrent) {
      classAssignments.push(`  class ${entry.mermaidId} current;`);
    } else if (entry.node.id === originSkillId) {
      classAssignments.push(`  class ${entry.mermaidId} origin;`);
    } else {
      const cls = statusToClass(entry.node.status, entry.node.is_active);
      classAssignments.push(`  class ${entry.mermaidId} ${cls};`);
    }
  }

  // ── edges ───────────────────────────────────────────────────────────
  // Parents point INTO current; current points INTO children.
  const edgeLines: string[] = [];
  // The current node's mermaidId (the entry where ``isCurrent`` is true).
  const currentMermaidId = entries.find((e) => e.isCurrent)?.mermaidId ?? '';

  // Parent edges: each parent → current.
  for (const entry of entries) {
    if (entry.isCurrent) {
      continue;
    }
    // Only connect parents (entries before the current one) to the
    // current node. Children are connected below.
    if (!isBeforeCurrent(entries, entry)) {
      continue;
    }
    const summary = sanitizeEdgeLabel(entry.node.change_summary || '');
    edgeLines.push(`  ${entry.mermaidId} -->|"${summary}"| ${currentMermaidId}`);
  }

  // Child edges: current → each child.
  for (const entry of entries) {
    if (entry.isCurrent) {
      continue;
    }
    if (!isAfterCurrent(entries, entry)) {
      continue;
    }
    const summary = sanitizeEdgeLabel(entry.node.change_summary || '');
    edgeLines.push(`  ${currentMermaidId} -->|"${summary}"| ${entry.mermaidId}`);
  }

  return [
    'graph TD',
    ...classDefLines,
    ...nodeLines,
    ...edgeLines,
    ...classAssignments,
  ].join('\n');
}

/**
 * Format the user-visible Mermaid node label. The status is appended
 * with an em-dash so the chip stays visually distinct from the
 * generation badge (``Gen N``). Empty / missing status falls back to
 * a generic ``"Unknown"`` so the label never reads ``" — "``.
 */
function formatNodeLabel(node: SkillLineageNode): string {
  const name = sanitizeLabelText(node.name || node.id || 'unnamed');
  const generation = Number.isFinite(node.generation) ? node.generation : 0;
  const status = node.status || (node.is_active ? 'active' : 'inactive');
  return `${name} (Gen ${generation}) — ${status}`;
}

/**
 * Sanitize a label so Mermaid's parser doesn't choke. Pipes end the
 * node declaration early; double-quotes end the label string; angle
 * brackets look like HTML tag delimiters (especially under
 * ``securityLevel: 'strict'``); backslashes can introduce escape
 * sequences; newlines break the single-line declaration. We replace
 * each with a Unicode look-alike so the rendered output stays
 * readable.
 */
function sanitizeLabelText(text: string): string {
  return text
    // Backslash FIRST (defensive ordering) — use U+29F5 ⧵
    // (reverse-solidus) for visual fidelity; it's inert inside a
    // Mermaid quoted-string label.
    .replace(/\\/g, '⧵')
    .replace(/\|/g, '∣')
    .replace(/"/g, '″')
    // Angle brackets → single-pointing angle quotes (U+2039 / U+203A)
    // so Mermaid strict-mode HTML detection treats them as text.
    .replace(/</g, '‹')
    .replace(/>/g, '›')
    .replace(/\r?\n/g, ' ')
    .trim();
}

/**
 * Sanitize an edge label and truncate to ~40 chars. Empty input →
 * ``"Auto-evolved"`` so users always see *something* on the edge.
 */
function sanitizeEdgeLabel(text: string): string {
  const trimmed = (text ?? '').trim();
  if (!trimmed) {
    return 'Auto-evolved';
  }
  const safe = sanitizeLabelText(trimmed);
  if (safe.length <= 40) {
    return safe;
  }
  // Truncate to 39 chars + ellipsis so the visible budget stays ~40.
  return `${safe.substring(0, 39)}…`;
}

/**
 * Map a node's lifecycle status to the matching ``classDef`` class.
 * Defaults to ``inactive`` for unknown statuses so an unexpected
 * backend value still renders predictably.
 */
function statusToClass(status: string, isActive: boolean): string {
  if (!isActive) {
    return 'inactive';
  }
  switch (status) {
    case 'active':
      return 'active';
    case 'ab_testing':
      return 'active';
    case 'archived':
      return 'deprecated';
    case 'deactivated':
      return 'inactive';
    default:
      return 'inactive';
  }
}

function isBeforeCurrent(entries: { isCurrent: boolean }[], entry: { isCurrent: boolean }): boolean {
  const currentIdx = entries.findIndex((e) => e.isCurrent);
  if (currentIdx < 0) {
    return false;
  }
  const entryIdx = entries.indexOf(entry as { isCurrent: boolean });
  return entryIdx >= 0 && entryIdx < currentIdx;
}

function isAfterCurrent(entries: { isCurrent: boolean }[], entry: { isCurrent: boolean }): boolean {
  const currentIdx = entries.findIndex((e) => e.isCurrent);
  if (currentIdx < 0) {
    return false;
  }
  const entryIdx = entries.indexOf(entry as { isCurrent: boolean });
  return entryIdx > currentIdx;
}

/**
 * Heuristic threshold for the "View Full Tree" affordance. Triggers
 * when the total visible node count exceeds this OR the deepest
 * generation depth (parents' max generation + children's max + 1
 * for current) exceeds the depth limit. Mirrors the planning doc's
 * 20-node / 5-generation guidance.
 */
export const LINEAGE_LARGE_TREE_NODE_LIMIT = 20;
export const LINEAGE_LARGE_TREE_DEPTH_LIMIT = 5;

/**
 * Returns ``true`` if the lineage should be flagged as "large tree"
 * — used by the component to show the warning + fullscreen button.
 * Exported so the unit suite can pin the contract.
 */
export function isLargeLineage(lineage: SkillLineage): boolean {
  if (!lineage) {
    return false;
  }
  const totalNodes = lineage.parents.length + lineage.children.length + 1; // +1 for current
  if (totalNodes > LINEAGE_LARGE_TREE_NODE_LIMIT) {
    return true;
  }
  // Compute lineage depth as the span between the oldest ancestor
  // and the youngest descendant, defaulting each side to the
  // current generation when one side is missing. The reduce seeds
  // are ``Number.POSITIVE_INFINITY`` / ``NEGATIVE_INFINITY`` so an
  // empty parents / children list doesn't artificially pin the
  // result at 0.
  const minParentGen =
    lineage.parents.length > 0
      ? lineage.parents.reduce(
          (acc, n) => Math.min(acc, n.generation ?? 0),
          Number.POSITIVE_INFINITY,
        )
      : lineage.generation;
  const maxChildGen =
    lineage.children.length > 0
      ? lineage.children.reduce(
          (acc, n) => Math.max(acc, n.generation ?? 0),
          Number.NEGATIVE_INFINITY,
        )
      : lineage.generation;
  const depth = Math.abs(maxChildGen - minParentGen);
  return depth > LINEAGE_LARGE_TREE_DEPTH_LIMIT;
}

/**
 * Strip Mermaid's ``flowchart-{id}-`` prefix off a rendered SVG
 * node-id so callers can recover the sanitized id (``node0``,
 * ``node1``) they used in the graph source.
 *
 * Mermaid assigns DOM ids as ``flowchart-{nodeId}-{index}`` where
 * ``{nodeId}`` is the literal token we used in the source string and
 * ``{index}`` is a sequential counter Mermaid appends. We match the
 * prefix plus the first ``-`` separated token after it.
 *
 * Returns ``null`` when the DOM-id doesn't look like a Mermaid node
 * (no ``flowchart-`` prefix). Callers should treat that as "unknown
 * node, ignore".
 */
export function parseMermaidNodeId(domNodeId: string): string | null {
  if (!domNodeId || !domNodeId.startsWith('flowchart-')) {
    return null;
  }
  const rest = domNodeId.substring('flowchart-'.length);
  // Strip the trailing ``-{index}`` Mermaid appends.
  const dashIdx = rest.lastIndexOf('-');
  if (dashIdx < 0) {
    return rest;
  }
  return rest.substring(0, dashIdx);
}

/**
 * Skill evolution lineage tree (Phase 3).
 *
 * Takes a ``SkillLineage`` payload (parents + children, each a
 * ``SkillLineageNode`` with edge metadata) and renders it as a
 * Mermaid ``graph TD`` via the reusable ``MermaidGraphComponent``.
 *
 * Interaction:
 *
 * * Click a node on the rendered SVG → emits ``navigateTo(skillId)``
 *   AND opens the edge-metadata dialog (if the node has any edge
 *   metadata — i.e. it isn't the current node itself).
 * * Click a button in the fallback legend below the chart → same
 *   behaviour. The fallback is always visible so users have a
 *   reliable navigation path even when Mermaid re-renders out from
 *   under our DOM listeners.
 *
 * Large-tree handling: when the lineage exceeds 20 nodes or 5
 * generation-spans, show a warning banner + a "View Full Tree"
 * button that opens the existing
 * ``MermaidFullscreenDialogComponent`` with the same Mermaid source.
 *
 * Empty state: when ``parents`` and ``children`` are both empty,
 * the computed ``graphSource`` collapses to ``''`` and the template
 * renders a styled "no evolution history" message.
 *
 * This phase does **not** integrate with the skill-detail page —
 * Phase 6 wires the host. For now the component is a self-contained
 * reusable surface.
 */
@Component({
  selector: 'app-skill-lineage-tree',
  standalone: true,
  imports: [CommonModule, MermaidGraphComponent, MatButtonModule, MatIconModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './skill-lineage-tree.component.html',
  styleUrl: './skill-lineage-tree.component.scss',
})
export class SkillLineageTreeComponent {
  /** Lineage payload — typed to ``SkillLineage`` from Phase 2. */
  readonly lineage = input.required<SkillLineage>();

  /** Skill being viewed — used to render the "current" node. */
  readonly currentSkillId = input.required<string>();

  /** Emits the target skill id when the user clicks any node. */
  readonly navigateTo = output<string>();

  private readonly dialog = inject(MatDialog);

  /**
   * Computed Mermaid source string. ``''`` means "empty lineage" so
   * the template can short-circuit to the no-history message.
   */
  readonly graphSource = computed(() => {
    const lin = this.lineage();
    if (!lin || (lin.parents.length === 0 && lin.children.length === 0)) {
      return '';
    }
    return buildLineageGraph(lin, this.currentSkillId());
  });

  /**
   * True when the lineage is large enough to warrant the warning +
   * fullscreen affordance. Re-computes whenever ``lineage`` changes.
   */
  readonly isLargeTree = computed(() => isLargeLineage(this.lineage()));

  /**
   * All lineage nodes flattened into a single iterable (parents +
   * children). Used by the fallback button list. The current node is
   * excluded — its fallback button would be a no-op (already viewing
   * it).
   */
  readonly allRelatedNodes = computed<SkillLineageNode[]>(() => {
    const lin = this.lineage();
    if (!lin) {
      return [];
    }
    return [...lin.parents, ...lin.children];
  });

  /**
   * Map from Mermaid node-id (``node0``) to underlying skill id. Kept
   * as a ``computed`` so the DOM click handler can recover the skill
   * id when Mermaid hands us a DOM-id like ``flowchart-node0-12``.
   */
  private readonly mermaidIdToSkillId = computed<Record<string, string>>(() => {
    const map: Record<string, string> = {};
    const lin = this.lineage();
    if (!lin) {
      return map;
    }
    let idx = 0;
    const sortedParents = [...lin.parents].sort(
      (a, b) => (a.generation ?? 0) - (b.generation ?? 0),
    );
    for (const node of sortedParents) {
      map[`node${idx++}`] = node.id;
    }
    // Skip the current node.
    idx++;
    const sortedChildren = [...lin.children].sort(
      (a, b) => (a.generation ?? 0) - (b.generation ?? 0),
    );
    for (const node of sortedChildren) {
      map[`node${idx++}`] = node.id;
    }
    return map;
  });

  /**
   * Map from Mermaid node-id → ``SkillLineageNode`` so the click
   * handler can both navigate and open the dialog in a single lookup.
   */
  private readonly nodeByMermaidId = computed<Record<string, SkillLineageNode>>(() => {
    const map: Record<string, SkillLineageNode> = {};
    const lin = this.lineage();
    if (!lin) {
      return map;
    }
    let idx = 0;
    const sortedParents = [...lin.parents].sort(
      (a, b) => (a.generation ?? 0) - (b.generation ?? 0),
    );
    for (const node of sortedParents) {
      map[`node${idx++}`] = node;
    }
    idx++; // skip current
    const sortedChildren = [...lin.children].sort(
      (a, b) => (a.generation ?? 0) - (b.generation ?? 0),
    );
    for (const node of sortedChildren) {
      map[`node${idx++}`] = node;
    }
    return map;
  });

  /**
   * Delegated click handler fed by ``MermaidGraphComponent``. The
   * graph wrapper forwards DOM-ids like ``flowchart-node0-12``; we
   * strip the prefix, look up the underlying skill id, emit
   * ``navigateTo``, and open the metadata dialog.
   *
   * Ignores clicks that resolve to the current skill id (the
   * "current" node has no edge metadata — the dialog would be
   * empty).
   */
  protected onMermaidNodeClicked(domNodeId: string): void {
    const mermaidId = parseMermaidNodeId(domNodeId);
    if (!mermaidId) {
      return;
    }
    const node = this.nodeByMermaidId()[mermaidId];
    if (!node) {
      return;
    }
    this.emitNavigateAndDialog(node);
  }

  /**
   * Fallback button click — invoked from the legend below the chart.
   * Same behaviour as ``onMermaidNodeClicked`` (navigate + dialog)
   * but bypasses the DOM delegation so it works even when Mermaid
   * re-renders out from under us.
   */
  protected onFallbackClick(node: SkillLineageNode): void {
    this.emitNavigateAndDialog(node);
  }

  /**
   * Centralised navigate + dialog dispatch. Extracted so the two
   * click paths (DOM delegation, fallback button) can't drift apart.
   */
  private emitNavigateAndDialog(node: SkillLineageNode): void {
    if (node.id !== this.currentSkillId()) {
      this.navigateTo.emit(node.id);
    }
    // Open the metadata dialog even for the current node — the
    // synthetic entry has no edge metadata, so the dialog will
    // render an empty-state note instead of an edge record.
    const data: SkillLineageNodeDialogData = { node, currentSkillId: this.currentSkillId() };
    this.dialog.open(SkillLineageNodeDialogComponent, {
      panelClass: ['dark-modal-panel'],
      width: '640px',
      maxWidth: '95vw',
      data,
    });
  }
}