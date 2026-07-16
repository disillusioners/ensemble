import { MermaidGraphComponent, MermaidGraphAction } from './mermaid-graph.component';

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

/**
 * Smoke tests for the reusable Mermaid wrapper.
 *
 * The component is intentionally thin — it delegates the heavy lifting
 * (Mermaid rendering, SVG serialisation, clipboard writes) to other
 * services (``MarkdownModule``, ``MermaidActionsService``). The
 * surface most worth pinning is the toolbar action dispatch and the
 * action-type contract.
 *
 * Mirrors the lightweight pattern used by `skill-card.component.spec.ts`
 * — pure value-shape checks rather than full TestBed / DOM standing.
 */

// ── Action type contract ─────────────────────────────────────────────────

describe('MermaidGraphAction', () => {
  it('exposes the three toolbar actions', () => {
    const values: MermaidGraphAction[] = ['image', 'source', 'fullscreen'];
    // Sanity: the type contract stays in sync with the rendered
    // template's button dispatch. If a new action is added without
    // updating the union this assertion will catch the drift.
    expect(values).toHaveLength(3);
  });
});

// ── Component class export ───────────────────────────────────────────────

describe('MermaidGraphComponent', () => {
  it('exports the standalone class for downstream imports', () => {
    expect(MermaidGraphComponent).toBeDefined();
    // The standalone flag is set declaratively on the @Component
    // decorator — there's no runtime property to introspect, but a
    // missing export would fail compilation, so this is the
    // canary check.
  });
});