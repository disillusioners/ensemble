import { Component, Type } from '@angular/core';
import { NgComponentOutlet } from '@angular/common';
import { MatTabsModule } from '@angular/material/tabs';
import { MatTooltipModule } from '@angular/material/tooltip';
import { CheckpointCleanupComponent } from './checkpoint-cleanup/checkpoint-cleanup.component';

/**
 * A single section registered in the Maintenance page shell's local
 * registry. Adding a section 2 later is a one-line append here — no
 * template change.
 */
interface MaintenanceSection {
  readonly id: string;
  readonly label: string;
  readonly component: Type<unknown>;
  /**
   * Cluster 1.2 (tabs shell rewrite) — when true, the registry entry
   * renders as a disabled `<mat-tab>` with a "Coming soon" tooltip;
   * Material's `[disabled]="true"` projection prevents the body from
   * ever mounting. Defaults to `false` (omitted on the entry);
   * existing entries are unaffected.
   */
  readonly disabled?: boolean;
}

/**
 * Maintenance page shell — hosts a `mat-tab-group` (the tab bar) that
 * renders its entries from a LOCAL section registry.
 *
 * Extensibility (user requirement #2): the registry is load-bearing,
 * NOT decorative. The template iterates the `sections` array via
 * `@for (section of sections; track section.id)` and renders one
 * `<mat-tab>` per entry whose body is the section's component via
 * `<ng-container *ngComponentOutlet="section.component">`. The
 * `sections-registry-load-bearing` source-grep pin enforces this — a
 * hard-coded tab body outside the `@for` would silently die. Adding a
 * future tab is a one-line append: `{ id, label, component, disabled? }`.
 *
 * Material's `mat-tab-group` lazy-mounts bodies (it uses
 * `*ngIf`-equivalent content projection internally), so only the
 * active tab instantiates its section component. The disabled future
 * tab's body is never mounted.
 */
@Component({
  selector: 'app-maintenance',
  standalone: true,
  imports: [NgComponentOutlet, MatTabsModule, MatTooltipModule, CheckpointCleanupComponent],
  templateUrl: './maintenance.component.html',
  styleUrl: './maintenance.component.scss',
})
export class MaintenanceComponent {
  // Local section registry — no shared cross-page registry. Adding a
  // section 2 later is a one-line append here.
  //
  // Cluster 1.2 (Plan Review B2' — spec AC-4 :68 + mockup :270 are
  // normative) — entry 1's label is "Checkpoint" (the spec's
  // normative label, not the prior "Checkpoint Cleanup").
  //
  // Cluster 1.3 — entry 2 is the disabled "Database" placeholder
  // (per N7' — the type field is kept uniform to satisfy TypeScript;
  // the disabled tab's body never mounts, per Material's
  // `[disabled]="true"` projection).
  readonly sections: readonly MaintenanceSection[] = [
    { id: 'checkpoint-cleanup', label: 'Checkpoint', component: CheckpointCleanupComponent },
    {
      id: 'database',
      label: 'Database',
      component: CheckpointCleanupComponent /* placeholder — body never mounts, tab disabled */,
      disabled: true,
    },
  ];
}
