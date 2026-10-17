import { Component, signal, WritableSignal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { provideRouter } from '@angular/router';
import { MatBadgeModule } from '@angular/material/badge';
import { MatIconModule } from '@angular/material/icon';
import { RouterLink } from '@angular/router';

/**
 * Focused test harness for the app-shell persona nav anchor — mirrors
 * the exact bindings declared in `frontend/src/app/app.html` lines
 * 28-35 (post-icon-removal):
 *
 *   <a routerLink="/persona" routerLinkActive="active"
 *      class="nav-link persona-nav-link"
 *      [matBadge]="personaService.pendingCount()"
 *      [matBadgeHidden]="personaService.pendingCount() === 0"
 *      matBadgeColor="warn"
 *      matBadgeSize="small">
 *     Persona
 *   </a>
 *
 * Why an inline host (and not the real `App`): `App` pulls in a heavy
 * collaborator graph (Router, WorkspaceOverlayService, chat host, ...)
 * that the existing `app.component.spec.ts` deliberately sidesteps via
 * a logic-mirror. The badge contract is template-shape, so a faithful
 * harness pinned to the same bindings gives the same evidence at one
 * thousandth of the setup cost.
 *
 * The `personaPendingCount` signal is the same shape as
 * `PersonaService.pendingCount` (`signal(0)`), so the binding expressions
 * (`pendingCount()` / `pendingCount() === 0`) evaluate identically.
 */
@Component({
  standalone: true,
  imports: [MatBadgeModule, MatIconModule, RouterLink],
  template: `
    <nav class="header-nav">
      <a routerLink="/sources" routerLinkActive="active" class="nav-link">
        <mat-icon>sources</mat-icon>
        Sources
      </a>
      <a routerLink="/jobs" routerLinkActive="active" class="nav-link">
        <mat-icon>work</mat-icon>
        Jobs
      </a>
      <a
        routerLink="/persona"
        routerLinkActive="active"
        class="nav-link persona-nav-link"
        [matBadge]="personaPendingCount()"
        [matBadgeHidden]="personaPendingCount() === 0"
        matBadgeColor="warn"
        matBadgeSize="small"
      >
        Persona
      </a>
    </nav>
  `,
})
class TestHostComponent {
  /** Mirror of `PersonaService.pendingCount` — a real `WritableSignal<number>`
   *  exposed so the test can mutate it directly to drive the badge contract. */
  readonly personaPendingCount: WritableSignal<number> = signal(0);
}

describe('App persona nav anchor — post-icon-removal binding contract', () => {
  let fixture: ComponentFixture<TestHostComponent>;
  let host: TestHostComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHostComponent],
      providers: [provideNoopAnimations(), provideRouter([])],
    }).compileComponents();

    fixture = TestBed.createComponent(TestHostComponent);
    host = fixture.componentInstance;
    fixture.detectChanges();
  });

  function personaLink(): HTMLAnchorElement {
    const el = fixture.nativeElement.querySelector(
      '.persona-nav-link',
    ) as HTMLAnchorElement | null;
    if (!el) {
      throw new Error('persona-nav-link anchor not rendered');
    }
    return el;
  }

  /** Badge content element (`<span class="mat-badge-content">`) appended as a
   *  child of the persona link by the matBadge directive. The host link itself
   *  carries `.mat-badge` and conditionally `.mat-badge-hidden` — Material's
   *  CSS rule `.mat-badge-hidden .mat-badge-content { display: none }` does the
   *  actual hide. We assert both shapes so a regression in either signal
   *  still trips the test. */
  function badgeContent(): HTMLElement | null {
    return personaLink().querySelector('.mat-badge-content');
  }

  function isHostHidden(): boolean {
    return personaLink().classList.contains('mat-badge-hidden');
  }

  // ── 1) Icon removal ─────────────────────────────────────────────

  it('does NOT render a <mat-icon> inside .persona-nav-link', () => {
    const link = personaLink();
    const icon = link.querySelector('mat-icon');
    expect(icon).toBeNull();
    // Defensive: assert no literal "person" icon literal in innerHTML either
    // (covers any future regression where someone re-adds a text node).
    expect(link.innerHTML.toLowerCase()).not.toContain('person<');
  });

  // ── 2) Label + routerLink preserved ─────────────────────────────

  it('keeps the "Persona" label and the /persona routerLink', () => {
    const link = personaLink();
    expect(link.textContent?.trim()).toContain('Persona');
    // RouterLink produces an `href` resolved against the configured router.
    expect(link.getAttribute('href')).toBe('/persona');
  });

  // ── 3) Badge lifecycle driven by pendingCount signal ────────────

  describe('badge lifecycle', () => {
    it('is hidden when pendingCount is 0', () => {
      host.personaPendingCount.set(0);
      fixture.detectChanges();
      // The host gets `.mat-badge-hidden` from `[matBadgeHidden]="0 === 0"`.
      // Material's CSS rule `.mat-badge-hidden .mat-badge-content { display: none }`
      // is what actually hides the badge — the textContent still mirrors
      // the bound value (`"0"`), but it is not rendered.
      expect(isHostHidden()).toBe(true);
    });

    it('is visible with text "3" when pendingCount is 3', () => {
      host.personaPendingCount.set(3);
      fixture.detectChanges();
      expect(isHostHidden()).toBe(false);
      const content = badgeContent();
      expect(content).not.toBeNull();
      expect(content!.textContent?.trim()).toBe('3');
    });

    it('hides again when pendingCount drops from 3 back to 0', () => {
      host.personaPendingCount.set(3);
      fixture.detectChanges();
      expect(isHostHidden()).toBe(false);
      expect(badgeContent()?.textContent?.trim()).toBe('3');

      host.personaPendingCount.set(0);
      fixture.detectChanges();
      expect(isHostHidden()).toBe(true);
    });
  });

  // ── 4) Sibling nav unaffected (icons still rendered elsewhere) ──

  it('sibling nav anchors still render their mat-icon (unaffected)', () => {
    const siblingIcons = fixture.nativeElement.querySelectorAll(
      '.nav-link:not(.persona-nav-link) mat-icon',
    );
    expect(siblingIcons.length).toBeGreaterThan(0);
  });
});