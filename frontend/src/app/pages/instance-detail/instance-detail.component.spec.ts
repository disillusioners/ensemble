import { readFileSync } from 'fs';
import { join } from 'path';
import { InstanceDetailComponent } from './instance-detail.component';

/**
 * The production ``InstanceDetailComponent`` is inert as of W5 — the
 * App root's NavigationEnd handler (``App.syncDetailVisibility``) is
 * the SINGLE writer to ``InstancesViewStateService``. The stub does
 * NOT read route params and does NOT call ``openDetail``, so a deep
 * link to ``/projects/:pid/instances/:iid`` is reconciled exactly
 * once via the App root.
 *
 * This spec pins the inertness via source-text regression checks.
 * We don't introspect Angular's ɵcmp metadata — its shape varies
 * across versions and the source-text approach catches the same
 * regressions (a non-empty template, an accidental ``openDetail``
 * call, a rogue route-param read) without coupling to internals.
 */
// Resolve the file relative to this spec so the test stays portable
// (jest runs from the frontend root, the spec lives at
// src/app/pages/instance-detail/).
const sourcePath = join(__dirname, 'instance-detail.component.ts');
const sourceText = readFileSync(sourcePath, 'utf8');

describe('InstanceDetailComponent (inert stub — W5)', () => {
  it('renders no DOM (template is empty)', () => {
    // The component's @Component decorator must declare template: ''
    // (or omit template/templateUrl entirely — Angular's standalone
    // compile treats both as "no DOM"). Source-text check that
    // catches a regression where someone re-adds `<div>...</div>`
    // to the template.
    expect(sourceText).toMatch(/template:\s*['"`]\s*['"`]/);
  });

  it('does NOT call openDetail — the App root is the single writer', () => {
    // If a future change adds ``this.viewState.openDetail(...)`` back
    // into the stub, this assertion fires so the reviewer is forced
    // to re-decide whether the App root stays the single writer.
    expect(sourceText).not.toMatch(/openDetail/);
  });

  it('does NOT read route params', () => {
    // paramMap / ActivatedRoute / route.snapshot are all signals of
    // the OLD responsibility (stub forwards deep-link params into the
    // service). The App root now owns that flow.
    expect(sourceText).not.toMatch(/paramMap/);
    expect(sourceText).not.toMatch(/ActivatedRoute/);
    expect(sourceText).not.toMatch(/snapshot/);
  });

  it('does NOT implement ngOnInit (no lifecycle work)', () => {
    // The stub used to have ``ngOnInit`` for param forwarding. With
    // W5 the App root owns that flow, so the stub has no lifecycle.
    expect(sourceText).not.toMatch(/ngOnInit/);
  });

  it('is a standalone Angular component', () => {
    // Pin the standalone flag at the source level — a future migration
    // to NgModule-based declaration would surface in review.
    expect(sourceText).toMatch(/standalone:\s*true/);
  });

  it('compile-time guard: the class is exported (regression)', () => {
    // The route table's ``loadComponent`` resolves ``InstanceDetailComponent``
    // by name. If the class is renamed or removed, this file fails
    // to compile (top-level import).
    expect(InstanceDetailComponent).toBeDefined();
    expect(typeof InstanceDetailComponent).toBe('function');
  });
});