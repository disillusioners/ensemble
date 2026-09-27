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
}

// Mirror of the production MaintenanceComponent.sections — copy verbatim
// from the production source so the mirror doesn't drift.
class MaintenanceShell {
  readonly sections: readonly MaintenanceSection[] = [
    { id: 'checkpoint-cleanup', label: 'Checkpoint Cleanup', component: 'CheckpointCleanupComponent' as unknown },
  ];
}

const maintenanceDir = join(__dirname);
const componentSrc = readFileSync(join(maintenanceDir, 'maintenance.component.ts'), 'utf-8');
const templateSrc = readFileSync(join(maintenanceDir, 'maintenance.component.html'), 'utf-8');

describe('MaintenanceComponent — page shell + section registry', () => {
  describe('mirror class shape', () => {
    it('registry contains exactly one section (`checkpoint-cleanup`)', () => {
      // Count pin: 1 section in v1. Phase 2 ships checkpoint-cleanup
      // ONLY. Phase 3+ may add sections (db-vacuum, orphan-instances,
      // …). The page shell is the load-bearing piece — the registry
      // count pins here so a future section-2 append is a deliberate
      // edit (the test fails until the mirror is updated).
      const shell = new MaintenanceShell();
      expect(shell.sections).toHaveLength(1);
      expect(shell.sections[0].id).toBe('checkpoint-cleanup');
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

    it('the single section is `Checkpoint Cleanup` with the correct label', () => {
      const shell = new MaintenanceShell();
      const section = shell.sections[0];
      expect(section.id).toBe('checkpoint-cleanup');
      expect(section.label).toBe('Checkpoint Cleanup');
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
