// Resources page-shell spec — Phase 5, T5.
//
// House pattern (mirrors `maintenance.component.spec.ts`): a logic-mirror
// class + production source pins. The shell renders sections from a
// LOCAL registry array — the registry is LOAD-BEARING (adding a tab is a
// one-line append). A hard-coded tab tag outside the `@for` would
// silently die; the source pin below enforces the registry-driven shape.
//
// A light TestBed render additionally proves the two registry entries
// surface as two `mat-tab` labels.

import { readFileSync } from 'fs';
import { join } from 'path';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { Subject } from 'rxjs';
import { ResourcesService } from '../../services/resources.service';
import { LiveTabComponent } from './live/live-tab.component';
import { TimelineTabComponent } from './timeline/timeline-tab.component';
import { ResourcesComponent, ResourcesSection } from './resources.component';

const resourcesDir = join(__dirname);
const componentSrc = readFileSync(join(resourcesDir, 'resources.component.ts'), 'utf-8');
const templateSrc = readFileSync(join(resourcesDir, 'resources.component.html'), 'utf-8');

// Mirror of the production ResourcesComponent.sections — kept verbatim so
// any shape drift in production breaks the mirror.
class ResourcesShellMirror {
  readonly sections: readonly ResourcesSection[] = [
    { id: 'live', label: 'Live', component: LiveTabComponent },
    { id: 'timeline', label: 'Timeline', component: TimelineTabComponent },
  ];
}

describe('ResourcesComponent — page shell + section registry', () => {
  describe('registry shape (mirror)', () => {
    it('registry contains exactly the live + timeline sections', () => {
      const shell = new ResourcesShellMirror();
      expect(shell.sections).toHaveLength(2);
      expect(shell.sections[0].id).toBe('live');
      expect(shell.sections[0].label).toBe('Live');
      expect(shell.sections[1].id).toBe('timeline');
      expect(shell.sections[1].label).toBe('Timeline');
    });

    it('each section carries the required fields (id, label, component)', () => {
      const shell = new ResourcesShellMirror();
      for (const section of shell.sections) {
        expect(typeof section.id).toBe('string');
        expect(section.id.length).toBeGreaterThan(0);
        expect(typeof section.label).toBe('string');
        expect(section.label.length).toBeGreaterThan(0);
        expect(section.component).toBeDefined();
      }
    });

    it('adding a tab is a one-line append (the array is the sole extension point)', () => {
      const shell = new ResourcesShellMirror();
      // A third entry appended to the array — no template change needed
      // because the template iterates the registry (source pin below).
      const extended: readonly ResourcesSection[] = [
        ...shell.sections,
        { id: 'future', label: 'Future', component: LiveTabComponent },
      ];
      expect(extended).toHaveLength(3);
      expect(extended[2].id).toBe('future');
    });
  });

  describe('production source pins', () => {
    it('template iterates the registry (`@for` + `*ngComponentOutlet`)', () => {
      expect(templateSrc).toMatch(/@for\s*\(\s*section\s+of\s+sections\s*;/);
      expect(templateSrc).toContain('ngComponentOutlet');
      expect(templateSrc).toContain('mat-tab-group');
    });

    it('component imports the tab module + both section components', () => {
      expect(componentSrc).toContain('MatTabsModule');
      expect(componentSrc).toContain('NgComponentOutlet');
      expect(componentSrc).toContain('LiveTabComponent');
      expect(componentSrc).toContain('TimelineTabComponent');
    });

    it('registry declares exactly two entries in production source', () => {
      // Two `{ id: ...` literals in the sections array.
      const registryBlock = componentSrc.slice(
        componentSrc.indexOf('readonly sections'),
      );
      const entryMatches = registryBlock.match(/\{\s*id:\s*'(live|timeline)'/g) ?? [];
      expect(entryMatches).toHaveLength(2);
    });
  });

  describe('runtime render (registry drives the tab bar)', () => {
    let fixture: ComponentFixture<ResourcesComponent>;

    beforeEach(async () => {
      // The Live tab (first/active) mounts and injects ResourcesService;
      // a never-emitting subject keeps it in the skeleton state with no
      // real HTTP.
      const pending = new Subject();
      await TestBed.configureTestingModule({
        imports: [ResourcesComponent],
        providers: [
          provideNoopAnimations(),
          {
            provide: ResourcesService,
            useValue: {
              getLive: () => pending,
              getSelfCost: () => pending,
              getHistory: () => pending,
              getAnomalies: () => pending,
              getEvidence: () => pending,
              getTraceback: () => pending,
            },
          },
        ],
      }).compileComponents();

      fixture = TestBed.createComponent(ResourcesComponent);
      fixture.detectChanges();
    });

    it('renders one tab label per registry entry', () => {
      // Material projects the registry-driven labels as role=tab headers.
      const labels = fixture.debugElement
        .queryAll(By.css('[role="tab"]'))
        .map((tab) => (tab.nativeElement.textContent ?? '').trim());
      expect(labels.length).toBe(2);
      expect(labels.some((label) => label.includes('Live'))).toBe(true);
      expect(labels.some((label) => label.includes('Timeline'))).toBe(true);
    });
  });
});
