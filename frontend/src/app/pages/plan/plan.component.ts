import { Component } from '@angular/core';

/**
 * Invisible placeholder for the ``/plan`` route.
 *
 * The Plane iframe is mounted at the app root level (in ``app.html``)
 * with a CSS display-toggle so it stays cached across route changes.
 * When ``/plan`` is active, the root-level overlay covers the
 * routed content, so this component renders an empty div that takes
 * no visual space. Keeping a real routed component (vs. a wildcard
 * redirect) ensures the Angular router activates the nav link's
 * ``active`` class when the URL is ``/plan``.
 */
@Component({
  selector: 'app-plan',
  standalone: true,
  template: `<div class="plan-placeholder"></div>`,
  styles: [`.plan-placeholder { display: none; }`]
})
export class PlanComponent {}
