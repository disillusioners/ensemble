/**
 * Stub the ESM-only `ngx-markdown` package — Jest's default CJS
 * transform pipeline can't parse the upstream ESM (which in turn
 * pulls in `marked`'s ESM build). The component's real template
 * keeps the original import; the spec just replaces it with an
 * inert stub so the test runner doesn't try to load the upstream
 * package.
 */
jest.mock('ngx-markdown', () => ({
  MarkdownModule: class MarkdownModuleStub {},
}));

import {
  buildLineageGraph,
  isLargeLineage,
  parseMermaidNodeId,
  LINEAGE_LARGE_TREE_NODE_LIMIT,
  LINEAGE_LARGE_TREE_DEPTH_LIMIT,
  SkillLineageTreeComponent,
} from './skill-lineage-tree.component';
import { SkillLineage, SkillLineageNode } from '../../models/skill.model';

/**
 * Tests for Phase 3 — Skill Lineage Tree.
 *
 * The component is broken into a pure ``buildLineageGraph`` function
 * + a thin Angular shell, so the bulk of the contract pinning lives
 * in the pure-function suite (no Angular test bed required). A small
 * ``TestableSkillLineageTreeComponent`` mirror class drives the
 * component-level behaviour (computed ``graphSource``, fallback
 * navigation, empty-state collapse).
 *
 * Mirrors the ``TestableXxxComponent`` pattern used by
 * ``mcp-server-dialog.component.spec.ts`` — the project uses real
 * ``TestBed`` for HTTP-bound services, but for components the
 * mirror-class approach keeps the suite free of zone / DOM mocking
 * overhead and runs in a fraction of the time.
 */

// ── Factories ────────────────────────────────────────────────────────────

let nextId = 1;

function makeNode(overrides: Partial<SkillLineageNode> = {}): SkillLineageNode {
  const id = overrides.id ?? `node-${nextId++}`;
  return {
    id,
    project_id: null,
    name: id,
    description: 'desc',
    category: 'coding',
    is_active: true,
    status: 'active',
    lineage_origin: id,
    generation: 0,
    ab_test_group: null,
    auto_load: false,
    source_skill_bank_id: null,
    total_selections: 0,
    total_applied: 0,
    total_completions: 0,
    total_fallbacks: 0,
    consecutive_failures: 0,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    last_used_at: null,
    change_summary: '',
    content_diff: '',
    edge_created_at: '2026-07-01T00:00:00Z',
    ...overrides,
  };
}

function makeLineage(overrides: Partial<SkillLineage> = {}): SkillLineage {
  return {
    skill_id: 'current-skill',
    parents: [],
    children: [],
    generation: 1,
    origin: 'origin-skill',
    ...overrides,
  };
}

// ── Pure function: buildLineageGraph ─────────────────────────────────────

describe('buildLineageGraph', () => {
  beforeEach(() => {
    nextId = 1;
  });

  it('returns the empty string for a lineage with no parents and no children', () => {
    const lineage = makeLineage({ parents: [], children: [] });
    expect(buildLineageGraph(lineage, 'current')).toBe('');
  });

  it('emits a graph TD header and classDef statements for non-empty lineage', () => {
    const parent = makeNode({
      id: 'parent-1',
      name: 'Parent Skill',
      generation: 0,
      change_summary: 'Auto-evolved',
    });
    const child = makeNode({
      id: 'child-1',
      name: 'Child Skill',
      generation: 2,
      change_summary: 'Added retry logic',
    });
    const lineage = makeLineage({
      parents: [parent],
      children: [child],
      origin: 'parent-1',
    });

    const out = buildLineageGraph(lineage, 'current');

    expect(out).toContain('graph TD');
    expect(out).toContain('classDef origin');
    expect(out).toContain('classDef current');
    expect(out).toContain('classDef active');
    expect(out).toContain('classDef inactive');
    expect(out).toContain('classDef deprecated');
  });

  it('uses sanitized node IDs (node0, node1, ...) rather than raw skill names', () => {
    const parent = makeNode({
      id: 'uuid-with-dashes-and-spaces',
      name: 'Parent With Spaces',
      generation: 0,
    });
    const child = makeNode({
      id: 'another uuid',
      name: 'Child Name',
      generation: 1,
    });
    const lineage = makeLineage({
      parents: [parent],
      children: [child],
    });

    const out = buildLineageGraph(lineage, 'current-skill');

    // Sanitized IDs are present.
    expect(out).toMatch(/node0\[/);
    expect(out).toMatch(/node1\[/);
    expect(out).toMatch(/node2\[/);
    // Raw UUIDs and spaces must NOT appear as node ids.
    expect(out).not.toMatch(/\[ *uuid-with-dashes-and-spaces/);
    expect(out).not.toMatch(/\[ *another uuid/);
  });

  it('marks the current node with the .current class', () => {
    const parent = makeNode({
      id: 'parent-1',
      generation: 0,
      lineage_origin: 'parent-1',
    });
    const child = makeNode({ id: 'child-1', generation: 2 });
    const lineage = makeLineage({
      parents: [parent],
      children: [child],
      origin: 'parent-1',
    });

    const out = buildLineageGraph(lineage, 'current-skill');

    // The current node (parent entries count = 1, current slot = 1, child slot = 2)
    // should be assigned the current class. We check that the substring
    // `class node1 current;` exists (node1 is the current entry).
    expect(out).toMatch(/class node1 current;/);
  });

  it('marks the origin ancestor with the .origin class', () => {
    const origin = makeNode({
      id: 'origin-skill',
      name: 'Origin',
      generation: 0,
      lineage_origin: 'origin-skill',
    });
    const child = makeNode({ id: 'child-1', generation: 2 });
    const lineage = makeLineage({
      parents: [origin],
      children: [child],
      origin: 'origin-skill',
    });

    const out = buildLineageGraph(lineage, 'current-skill');

    expect(out).toMatch(/class node0 origin;/);
  });

  it('renders edge labels from change_summary text (truncated at 40 chars)', () => {
    const longSummary = 'x'.repeat(80); // 80 chars, must be truncated
    const parent = makeNode({
      id: 'parent-1',
      generation: 0,
      change_summary: longSummary,
      lineage_origin: 'parent-1',
    });
    const lineage = makeLineage({
      parents: [parent],
      origin: 'parent-1',
    });

    const out = buildLineageGraph(lineage, 'current-skill');

    expect(out).toContain('|');
    // The full 80-char string must NOT appear in the rendered output
    // — truncation kicks in at ~40 chars.
    expect(out).not.toContain(longSummary);
    // The truncation suffix is the ellipsis character.
    expect(out).toContain('…');
    // We should still see at least the prefix of the summary.
    expect(out).toContain('x'.repeat(20));
  });

  it('falls back to "Auto-evolved" when change_summary is empty', () => {
    const parent = makeNode({
      id: 'parent-1',
      generation: 0,
      change_summary: '',
      lineage_origin: 'parent-1',
    });
    const child = makeNode({
      id: 'child-1',
      generation: 2,
      change_summary: '',
    });
    const lineage = makeLineage({
      parents: [parent],
      children: [child],
      origin: 'parent-1',
    });

    const out = buildLineageGraph(lineage, 'current-skill');

    // Every edge that comes from a node with empty change_summary
    // should display "Auto-evolved".
    const matches = out.match(/\|"Auto-evolved"\|/g) ?? [];
    expect(matches.length).toBeGreaterThanOrEqual(2);
  });

  it('falls back to "Auto-evolved" when change_summary is whitespace-only', () => {
    const parent = makeNode({
      id: 'parent-1',
      generation: 0,
      change_summary: '   \n  ',
      lineage_origin: 'parent-1',
    });
    const lineage = makeLineage({
      parents: [parent],
      origin: 'parent-1',
    });

    const out = buildLineageGraph(lineage, 'current-skill');

    expect(out).toContain('|"Auto-evolved"|');
  });

  it('sanitizes pipe characters in node labels', () => {
    const parent = makeNode({
      id: 'parent-1',
      name: 'Skill | With | Pipes',
      generation: 0,
      change_summary: 'auto',
      lineage_origin: 'parent-1',
    });
    const lineage = makeLineage({
      parents: [parent],
      origin: 'parent-1',
    });

    const out = buildLineageGraph(lineage, 'current');

    // Pipes in the label should be replaced with the Unicode
    // look-alike ∣ — raw pipes would break Mermaid's parser.
    expect(out).toContain('Skill ∣ With ∣ Pipes');
    // The literal "Skill | With" must NOT appear (the pipe is
    // sanitized).
    expect(out).not.toContain('Skill | With | Pipes');
  });

  it('sanitizes double-quote characters in node labels', () => {
    const parent = makeNode({
      id: 'parent-1',
      name: 'Has "quoted" text',
      generation: 0,
      change_summary: 'auto',
      lineage_origin: 'parent-1',
    });
    const lineage = makeLineage({
      parents: [parent],
      origin: 'parent-1',
    });

    const out = buildLineageGraph(lineage, 'current');

    expect(out).toContain('″quoted″');
    // The unescaped double quote must not appear inside the label.
    // We check that the original "quoted" substring with raw quotes
    // is absent.
    expect(out).not.toMatch(/Has "quoted"/);
  });

  it('sanitizes backslash characters in node labels', () => {
    const parent = makeNode({
      id: 'parent-1',
      name: 'path\\to\\skill',
      generation: 0,
      change_summary: 'auto',
      lineage_origin: 'parent-1',
    });
    const lineage = makeLineage({
      parents: [parent],
      origin: 'parent-1',
    });

    const out = buildLineageGraph(lineage, 'current');

    // The Unicode reverse-solidus glyph (U+29F5 ⧵) must replace the
    // raw backslashes — raw `\` would otherwise be a Mermaid escape
    // introducer inside a quoted-string label.
    expect(out).toContain('path⧵to⧵skill');
    // Raw backslashes must not leak into the rendered source.
    expect(out).not.toContain('path\\to\\skill');
  });

  it('sanitizes angle-bracket characters in node labels', () => {
    const parent = makeNode({
      id: 'parent-1',
      name: 'Skill <with> brackets',
      generation: 0,
      change_summary: 'auto',
      lineage_origin: 'parent-1',
    });
    const lineage = makeLineage({
      parents: [parent],
      origin: 'parent-1',
    });

    const out = buildLineageGraph(lineage, 'current');

    // Angle brackets become single-pointing angle quotes (U+2039 /
    // U+203A) so Mermaid's strict-mode HTML-tag detection treats
    // them as text rather than the boundary of a tag.
    expect(out).toContain('Skill ‹with› brackets');
    // Raw angle brackets must not survive sanitization.
    expect(out).not.toContain('Skill <with> brackets');
  });

  it('applies all label sanitizers to a mixed-danger-character input', () => {
    // Exercise every sanitizer branch in a single input so any
    // single-pass regression is caught.
    const parent = makeNode({
      id: 'parent-1',
      name: '"foo <bar> | baz\\"',
      generation: 0,
      change_summary: 'auto',
      lineage_origin: 'parent-1',
    });
    const lineage = makeLineage({
      parents: [parent],
      origin: 'parent-1',
    });

    const out = buildLineageGraph(lineage, 'current');

    // The full graph string legitimately contains `>` (in `-->` arrow
    // syntax) and `|` (in `|...|` edge-label delimiters). Whole-string
    // not.toContain('<') / not.toContain('>') / not.toContain('|')
    // therefore can't hold. Extract just the node0 label substring and
    // assert the sanitization happened there.
    const node0Line = out.split('\n').find((l) => /^\s*node0\[/.test(l));
    expect(node0Line).toBeDefined();
    const label = node0Line!.match(/\["(.*)"\]/)![1];

    // Each substitution glyph must appear in the label.
    expect(label).toContain('″');
    expect(label).toContain('‹');
    expect(label).toContain('›');
    expect(label).toContain('∣');
    expect(label).toContain('⧵');
    // None of the raw danger characters may survive in the label.
    expect(label).not.toContain('<');
    expect(label).not.toContain('>');
    expect(label).not.toContain('|');
    expect(label).not.toMatch(/[^⧵]\\/); // bare `\` that isn't part of the glyph
  });

  it('connects parents to the current node and current node to children', () => {
    const parent = makeNode({
      id: 'parent-1',
      generation: 0,
      change_summary: 'auto',
      lineage_origin: 'parent-1',
    });
    const child = makeNode({
      id: 'child-1',
      generation: 2,
      change_summary: 'auto',
    });
    const lineage = makeLineage({
      parents: [parent],
      children: [child],
      origin: 'parent-1',
    });

    const out = buildLineageGraph(lineage, 'current-skill');

    // Parent (node0) → current (node1)
    expect(out).toMatch(/node0 -->\|.+?\| node1/);
    // Current (node1) → child (node2)
    expect(out).toMatch(/node1 -->\|.+?\| node2/);
  });

  it('produces stable output for the same input (deterministic ordering)', () => {
    const parentA = makeNode({
      id: 'parent-A',
      name: 'Parent A',
      generation: 0,
      change_summary: 'auto',
      lineage_origin: 'parent-A',
    });
    const parentB = makeNode({
      id: 'parent-B',
      name: 'Parent B',
      generation: 0,
      change_summary: 'auto',
      lineage_origin: 'parent-A',
    });
    const lineage = makeLineage({
      parents: [parentA, parentB],
      origin: 'parent-A',
    });

    const first = buildLineageGraph(lineage, 'current-skill');
    const second = buildLineageGraph(lineage, 'current-skill');
    expect(first).toBe(second);
  });

  it('renders the generation number in each node label', () => {
    const parent = makeNode({
      id: 'parent-1',
      generation: 0,
      change_summary: 'auto',
      lineage_origin: 'parent-1',
    });
    const child = makeNode({
      id: 'child-1',
      generation: 5,
      change_summary: 'auto',
    });
    const lineage = makeLineage({
      parents: [parent],
      children: [child],
      origin: 'parent-1',
    });

    const out = buildLineageGraph(lineage, 'current-skill');

    expect(out).toContain('Gen 0');
    expect(out).toContain('Gen 5');
  });
});

// ── Pure function: isLargeLineage ─────────────────────────────────────────

describe('isLargeLineage', () => {
  it('returns false for a small lineage', () => {
    const lineage = makeLineage({
      parents: [makeNode({ id: 'p1', generation: 0 })],
      children: [makeNode({ id: 'c1', generation: 2 })],
    });
    expect(isLargeLineage(lineage)).toBe(false);
  });

  it('returns true when total nodes exceed the configured limit', () => {
    // LINEAGE_LARGE_TREE_NODE_LIMIT parents + 1 child = just over the limit
    const parents = Array.from({ length: LINEAGE_LARGE_TREE_NODE_LIMIT }, (_, i) =>
      makeNode({ id: `p${i}`, generation: 0 }),
    );
    const lineage = makeLineage({ parents, children: [] });
    expect(isLargeLineage(lineage)).toBe(true);
  });

  it('returns true when generation depth exceeds the configured limit', () => {
    // 6-generation span (parent gen 0, child gen 6) is over the limit of 5.
    const parent = makeNode({ id: 'p1', generation: 0 });
    const child = makeNode({ id: 'c1', generation: LINEAGE_LARGE_TREE_DEPTH_LIMIT + 1 });
    const lineage = makeLineage({ parents: [parent], children: [child] });
    expect(isLargeLineage(lineage)).toBe(true);
  });

  it('returns false for an empty lineage', () => {
    expect(isLargeLineage(makeLineage({ parents: [], children: [] }))).toBe(false);
  });
});

// ── Pure function: parseMermaidNodeId ─────────────────────────────────────

describe('parseMermaidNodeId', () => {
  it('strips the flowchart- prefix and trailing -index suffix', () => {
    expect(parseMermaidNodeId('flowchart-node0-12')).toBe('node0');
    expect(parseMermaidNodeId('flowchart-node1-7')).toBe('node1');
  });

  it('returns the suffix-less id when there is no trailing index', () => {
    expect(parseMermaidNodeId('flowchart-node0')).toBe('node0');
  });

  it('returns null when the DOM-id is not a Mermaid node', () => {
    expect(parseMermaidNodeId('some-other-id')).toBeNull();
    expect(parseMermaidNodeId('flowchart')).toBeNull();
    expect(parseMermaidNodeId('')).toBeNull();
  });
});

// ── Component: TestableSkillLineageTreeComponent mirror ───────────────────

/**
 * Testable mirror of the component. Mirrors the actual public
 * surface (signals, computed values, click handlers) so the suite
 * can exercise the contract without standing up ``TestBed`` /
 * ``ComponentFixture``.
 */
class TestableSkillLineageTreeComponent {
  private readonly _lineage: { (): SkillLineage | null; (v: SkillLineage): void };
  private readonly _currentSkillId: { (): string; (v: string): void };

  constructor(lineage: SkillLineage, currentSkillId: string) {
    let lin: SkillLineage | null = lineage;
    let cur = currentSkillId;
    this._lineage = ((v?: SkillLineage) => {
      if (v === undefined) {
        return lin;
      }
      lin = v;
      return lin;
    }) as { (): SkillLineage | null; (v: SkillLineage): void };
    this._currentSkillId = ((v?: string) => {
      if (v === undefined) {
        return cur;
      }
      cur = v;
      return cur;
    }) as { (): string; (v: string): void };
  }

  // Mirror public signal getters.
  lineage = (): SkillLineage => this._lineage() as SkillLineage;
  currentSkillId = (): string => this._currentSkillId();

  // Computed mirror of graphSource.
  graphSource = (): string => buildLineageGraph(this.lineage(), this.currentSkillId());
  isLargeTree = (): boolean => isLargeLineage(this.lineage());

  allRelatedNodes = (): SkillLineageNode[] => {
    const lin = this.lineage();
    return [...lin.parents, ...lin.children];
  };

  // Mirror of the navigateTo output.
  public navigateToEmit: string | null = null;
  public fallbackClickedNode: SkillLineageNode | null = null;

  onFallbackClick(node: SkillLineageNode): void {
    this.fallbackClickedNode = node;
    this.navigateToEmit = node.id;
  }
}

// ── Component-level suite ────────────────────────────────────────────────

describe('SkillLineageTreeComponent', () => {
  beforeEach(() => {
    nextId = 1;
  });

  describe('graphSource computed', () => {
    it('returns the empty string when lineage has no parents and no children', () => {
      const component = new TestableSkillLineageTreeComponent(
        makeLineage({ parents: [], children: [] }),
        'current',
      );
      expect(component.graphSource()).toBe('');
    });

    it('returns a non-empty graph source for a lineage with parents + children', () => {
      const component = new TestableSkillLineageTreeComponent(
        makeLineage({
          parents: [makeNode({ id: 'p1', generation: 0 })],
          children: [makeNode({ id: 'c1', generation: 2 })],
          origin: 'p1',
        }),
        'current',
      );
      expect(component.graphSource()).toContain('graph TD');
    });
  });

  describe('fallback navigation', () => {
    it('emits navigateTo with the correct skill id when a fallback button is clicked', () => {
      const parent = makeNode({ id: 'parent-1', name: 'Parent 1', generation: 0 });
      const component = new TestableSkillLineageTreeComponent(
        makeLineage({
          parents: [parent],
          children: [],
          origin: 'parent-1',
        }),
        'current',
      );

      component.onFallbackClick(parent);

      expect(component.navigateToEmit).toBe('parent-1');
      expect(component.fallbackClickedNode).toBe(parent);
    });

    it('emits navigateTo for a child node too', () => {
      const child = makeNode({ id: 'child-1', name: 'Child 1', generation: 2 });
      const component = new TestableSkillLineageTreeComponent(
        makeLineage({
          parents: [],
          children: [child],
        }),
        'current',
      );

      component.onFallbackClick(child);

      expect(component.navigateToEmit).toBe('child-1');
    });
  });

  describe('empty-state rendering contract', () => {
    it('exposes an empty graph source so the template renders the no-history message', () => {
      const component = new TestableSkillLineageTreeComponent(
        makeLineage({ parents: [], children: [] }),
        'current',
      );
      // The component's template uses @if (graphSource()) to swap
      // between the graph view and the empty-state message — when
      // graphSource() is empty, the empty-state branch renders.
      expect(component.graphSource()).toBe('');
      expect(component.allRelatedNodes()).toEqual([]);
    });
  });

  describe('large-tree flag', () => {
    it('marks the component as large when the lineage exceeds the node limit', () => {
      const parents = Array.from({ length: LINEAGE_LARGE_TREE_NODE_LIMIT + 1 }, (_, i) =>
        makeNode({ id: `p${i}`, generation: 0 }),
      );
      const component = new TestableSkillLineageTreeComponent(
        makeLineage({ parents, children: [] }),
        'current',
      );
      expect(component.isLargeTree()).toBe(true);
    });

    it('does not mark the component as large for a small lineage', () => {
      const component = new TestableSkillLineageTreeComponent(
        makeLineage({
          parents: [makeNode({ id: 'p1', generation: 0 })],
          children: [makeNode({ id: 'c1', generation: 2 })],
        }),
        'current',
      );
      expect(component.isLargeTree()).toBe(false);
    });
  });

  describe('allRelatedNodes computed', () => {
    it('returns parents and children flattened, excluding the current node', () => {
      const parent = makeNode({ id: 'p1', generation: 0 });
      const child = makeNode({ id: 'c1', generation: 2 });
      const component = new TestableSkillLineageTreeComponent(
        makeLineage({ parents: [parent], children: [child] }),
        'current',
      );

      const nodes = component.allRelatedNodes();
      expect(nodes).toHaveLength(2);
      expect(nodes.map((n) => n.id)).toEqual(['p1', 'c1']);
    });
  });

  // Sanity: confirm the public class export is wired up — the unit
  // suite imports `SkillLineageTreeComponent` so a missing export
  // would fail compilation. This is a no-op test that simply
  // ensures the symbol exists.
  it('exports the SkillLineageTreeComponent class', () => {
    expect(SkillLineageTreeComponent).toBeDefined();
  });
});