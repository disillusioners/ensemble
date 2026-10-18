import { Component, Type } from '@angular/core';
import { NgComponentOutlet } from '@angular/common';
import { MatTabsModule } from '@angular/material/tabs';
import { LiveTabComponent } from './live/live-tab.component';
import { TimelineTabComponent } from './timeline/timeline-tab.component';

/**
 * A single section registered in the Resources page shell's local
 * registry. Adding a section later is a one-line append here — no
 * template change.
 */
export interface ResourcesSection {
  readonly id: string;
  readonly label: string;
  readonly component: Type<unknown>;
}

/**
 * Resources page shell — hosts a `mat-tab-group` (the tab bar) that
 * renders its entries from a LOCAL section registry, cloned from the
 * Maintenance shell pattern (`maintenance.component.ts`, D3).
 *
 * Extensibility: the registry is load-bearing, NOT decorative. The
 * template iterates `sections` via `@for (section of sections; track
 * section.id)` and renders one `<mat-tab>` per entry whose body is the
 * section's component via
 * `<ng-container *ngComponentOutlet="section.component">`. Adding a
 * future tab is a one-line append: `{ id, label, component }`.
 *
 * Material's `mat-tab-group` lazy-mounts bodies, so only the active tab
 * instantiates its section component — which is what gates the Live
 * tab's 8 s poll (D6): leaving the tab destroys the component and
 * clears the interval.
 */
@Component({
  selector: 'app-resources',
  standalone: true,
  imports: [NgComponentOutlet, MatTabsModule, LiveTabComponent, TimelineTabComponent],
  templateUrl: './resources.component.html',
  styleUrl: './resources.component.scss',
})
export class ResourcesComponent {
  // Local section registry — no shared cross-page registry. Adding a
  // tab later is a one-line append here.
  readonly sections: readonly ResourcesSection[] = [
    { id: 'live', label: 'Live', component: LiveTabComponent },
    { id: 'timeline', label: 'Timeline', component: TimelineTabComponent },
  ];
}
