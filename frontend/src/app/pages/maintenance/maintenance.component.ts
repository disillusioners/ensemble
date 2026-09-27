import { Component, Type } from '@angular/core';
import { NgComponentOutlet } from '@angular/common';
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
}

/**
 * Maintenance page shell — hosts a local section registry that renders
 * each section via `<ng-container *ngComponentOutlet>`.
 *
 * Extensibility (user requirement #2): the registry is load-bearing,
 * NOT decorative. A second section is added by appending to the
 * `sections` array — the template picks it up via `@for (section of
 * sections; track section.id)`. The
 * `sections-registry-load-bearing` source-grep pin enforces this — a
 * hard-coded section tag outside the `@for` would silently die.
 */
@Component({
  selector: 'app-maintenance',
  standalone: true,
  imports: [NgComponentOutlet, CheckpointCleanupComponent],
  templateUrl: './maintenance.component.html',
  styleUrl: './maintenance.component.scss',
})
export class MaintenanceComponent {
  // Local section registry — no shared cross-page registry. Adding a
  // section 2 later is a one-line append here.
  readonly sections: readonly MaintenanceSection[] = [
    { id: 'checkpoint-cleanup', label: 'Checkpoint Cleanup', component: CheckpointCleanupComponent },
  ];
}
