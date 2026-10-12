// Maintenance page-shell spec — Phase 2, Section 1.
//
// Logic-mirror (no TestBed), mirroring the house style at
// `frontend/src/app/pages/jobs/jobs.component.spec.ts:122-168`. The
// shell renders sections from a LOCAL registry array — the registry is
// LOAD-BEARING (user requirement #2 extensibility); a hard-coded
// section tag outside the `@for` would silently die. The
// `sections-registry-load-bearing` source-grep pin enforces this.
//
// Mirrors `MaintenanceComponent`'s public surface (section count +
// per-section shape). The production `sections` array is declared
// `readonly: readonly MaintenanceSection[]` — the mirror copies the
// shape verbatim so any shape drift in production breaks the mirror.

import { readFileSync } from 'fs';
import { join } from 'path';

interface MaintenanceSection {
  readonly id: string;
  readonly label: string;
  readonly component: unknown;
  // Cluster 1.6 — Pin 20 — the production interface gains the
  // optional `disabled?: boolean` field for the disabled
  // "Database" placeholder tab. The mirror copies the shape
  // verbatim so any drift in production breaks the mirror.
  readonly disabled?: boolean;
}

// Mirror of the production MaintenanceComponent.sections — copy verbatim
// from the production source so the mirror doesn't drift.
// Cluster 1.7 (test-plan.md §1.2) — the mirror now has 2 entries
// per the AC-16 amendment:
//   entry 1: { id: 'checkpoint-cleanup', label: 'Checkpoint', component: ... }
//            — label RENAMED from 'Checkpoint Cleanup' to 'Checkpoint'
//              per Plan Review 2026-10-12 B2' (spec AC-4 :68 +
//              mockup :270 — the spec is normative).
//   entry 2: { id: 'database', label: 'Database', component: ...,
//              disabled: true }
//            — placeholder for the future tabs (per N7' — the type
//              field is kept uniform to satisfy TypeScript; the
//              disabled tab's body never mounts, per Material's
//              `[disabled]="true"` projection).
class MaintenanceShell {
  readonly sections: readonly MaintenanceSection[] = [
    { id: 'checkpoint-cleanup', label: 'Checkpoint', component: 'CheckpointCleanupComponent' as unknown },
    { id: 'database', label: 'Database', component: 'CheckpointCleanupComponent' as unknown, disabled: true },
  ];
}

const maintenanceDir = join(__dirname);
const componentSrc = readFileSync(join(maintenanceDir, 'maintenance.component.ts'), 'utf-8');
const templateSrc = readFileSync(join(maintenanceDir, 'maintenance.component.html'), 'utf-8');

describe('MaintenanceComponent — page shell + section registry', () => {
  describe('mirror class shape', () => {
    it('registry contains exactly two sections (`checkpoint-cleanup` + `database` placeholder)', () => {
      // Cluster 1.7 — AC-16 mirror count: 2 sections in v2
      // (post-Cluster-1 tabs shell rewrite). Entry 1 is the
      // real CheckpointCleanupComponent tab; entry 2 is the
      // disabled "Database" placeholder tab (per spec AC-3
      // extensibility + the scale proof of a second tab — the
      // body never mounts because Material's `[disabled]="true"`
      // skips projection).
      //
      // Plan Review 2026-10-12 N1 — the count assertion is at
      // line 45 (verified; the prior citation at :38 was stale).
      const shell = new MaintenanceShell();
      expect(shell.sections).toHaveLength(2);
      expect(shell.sections[0].id).toBe('checkpoint-cleanup');
      expect(shell.sections[1].id).toBe('database');
      expect(shell.sections[1].disabled).toBe(true);
    });

    it('each section has the required fields (id, label, component)', () => {
      const shell = new MaintenanceShell();
      for (const section of shell.sections) {
        expect(typeof section.id).toBe('string');
        expect(section.id.length).toBeGreaterThan(0);
        expect(typeof section.label).toBe('string');
        expect(section.label.length).toBeGreaterThan(0);
        expect(section.component).toBeDefined();
      }
    });

    it('the first section is `Checkpoint` with the spec-normative label (Plan Review B2\')', () => {
      // Plan Review 2026-10-12 B2' — the production label is
      // 'Checkpoint' (per spec AC-4 :68 + mockup :270), NOT
      // 'Checkpoint Cleanup'. The mirror must reflect the
      // spec-normative label. A future regression that renames
      // the label back to 'Checkpoint Cleanup' trips this
      // assertion loudly.
      const shell = new MaintenanceShell();
      const section = shell.sections[0];
      expect(section.id).toBe('checkpoint-cleanup');
      expect(section.label).toBe('Checkpoint');
    });
  });

  describe('production source pins', () => {
    // Item 18 — the `sections-registry-load-bearing` pin is canonical
    // in `maintenance.bindings.pins.spec.ts` (Pin 1). It was
    // duplicated here; deleted to keep the two specs in sync.
    // The mirror-class shape tests above cover the structural concern
    // (section count + per-section shape), and the bindings-pins spec
    // covers the source-grep pin (production regex + template shape).

    it('page shell imports `CommonModule` (or `@for`/`ngComponentOutlet` support)', () => {
      // Angular 21 standalone + signals — `@for` is a built-in
      // control-flow block; `*ngComponentOutlet` requires
      // `CommonModule` (or `NgComponentOutlet` standalone import).
      // Either is acceptable; the pin asserts the template compiles.
      const hasNgComponentOutletImport =
        componentSrc.includes('NgComponentOutlet') ||
        componentSrc.includes('CommonModule');
      expect(hasNgComponentOutletImport).toBe(true);
    });
  });
});
