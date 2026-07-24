import { ComponentFixture, TestBed } from '@angular/core/testing';
import { SimpleChange } from '@angular/core';
import { FileTabsComponent } from './file-tabs.component';
import type { OpenFileTab } from '../../models/workspace.model';

/**
 * Tests for `FileTabsComponent`.
 *
 * Pattern: Angular `TestBed` driving the real component. Inputs are
 * plain inputs (not signals) so we update them through Angular's normal
 * change-detection path via `setInput` / `ngOnChanges`.
 */
describe('FileTabsComponent', () => {
  let fixture: ComponentFixture<FileTabsComponent>;
  let component: FileTabsComponent;

  function makeTab(overrides: Partial<OpenFileTab> = {}): OpenFileTab {
    return {
      path: 'src/main.ts',
      name: 'main.ts',
      content: null,
      dirty: false,
      ...overrides,
    };
  }

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [FileTabsComponent],
    }).compileComponents();

    fixture = TestBed.createComponent(FileTabsComponent);
    component = fixture.componentInstance;
  });

  // ── 1) Component creation ─────────────────────────────────────

  it('creates successfully', () => {
    expect(component).toBeTruthy();
  });

  // ── 2) Empty state ────────────────────────────────────────────

  describe('empty state', () => {
    it('renders nothing when openFiles is empty', () => {
      fixture.componentRef.setInput('openFiles', []);
      fixture.detectChanges();
      expect(fixture.nativeElement.querySelector('.file-tab-bar')).toBeNull();
    });

    it('renders nothing when openFiles is omitted (default empty array)', () => {
      fixture.detectChanges();
      expect(fixture.nativeElement.querySelector('.file-tab-bar')).toBeNull();
    });
  });

  // ── 3) Tab rendering ──────────────────────────────────────────

  describe('tab rendering', () => {
    beforeEach(() => {
      fixture.componentRef.setInput('openFiles', [
        makeTab({ path: 'src/main.ts', name: 'main.ts' }),
        makeTab({ path: 'src/app.ts', name: 'app.ts' }),
        makeTab({ path: 'src/lib.ts', name: 'lib.ts', dirty: true }),
      ]);
      fixture.detectChanges();
    });

    it('renders one button per open tab', () => {
      const tabs = fixture.nativeElement.querySelectorAll('.file-tab');
      expect(tabs.length).toBe(3);
    });

    it('renders the tab display name', () => {
      const tabs = fixture.nativeElement.querySelectorAll('.file-tab-name');
      expect(tabs[0].textContent.trim()).toBe('main.ts');
      expect(tabs[1].textContent.trim()).toBe('app.ts');
      expect(tabs[2].textContent.trim()).toBe('lib.ts');
    });

    it('marks the active tab with .active class', () => {
      fixture.componentRef.setInput('activePath', 'src/app.ts');
      fixture.detectChanges();

      const tabs = fixture.nativeElement.querySelectorAll('.file-tab');
      expect(tabs[0].classList.contains('active')).toBe(false);
      expect(tabs[1].classList.contains('active')).toBe(true);
      expect(tabs[2].classList.contains('active')).toBe(false);
    });

    it('sets aria-selected on the active tab', () => {
      fixture.componentRef.setInput('activePath', 'src/app.ts');
      fixture.detectChanges();

      const tabs = fixture.nativeElement.querySelectorAll('.file-tab');
      expect(tabs[0].getAttribute('aria-selected')).toBe('false');
      expect(tabs[1].getAttribute('aria-selected')).toBe('true');
      expect(tabs[2].getAttribute('aria-selected')).toBe('false');
    });
  });

  // ── 4) Dirty indicator ────────────────────────────────────────

  describe('dirty indicator', () => {
    it('shows a dirty dot when tab.dirty is true', () => {
      fixture.componentRef.setInput('openFiles', [
        makeTab({ path: 'src/main.ts', dirty: true }),
      ]);
      fixture.detectChanges();

      expect(fixture.nativeElement.querySelector('.dirty-dot')).toBeTruthy();
    });

    it('does not show a dirty dot when tab.dirty is false', () => {
      fixture.componentRef.setInput('openFiles', [
        makeTab({ path: 'src/main.ts', dirty: false }),
      ]);
      fixture.detectChanges();

      expect(fixture.nativeElement.querySelector('.dirty-dot')).toBeNull();
    });

    it('only shows dirty dots on dirty tabs (mixed list)', () => {
      fixture.componentRef.setInput('openFiles', [
        makeTab({ path: 'a.ts', dirty: false }),
        makeTab({ path: 'b.ts', dirty: true }),
        makeTab({ path: 'c.ts', dirty: false }),
      ]);
      fixture.detectChanges();

      const dots = fixture.nativeElement.querySelectorAll('.dirty-dot');
      expect(dots.length).toBe(1);
    });
  });

  // ── 5) Click events ───────────────────────────────────────────

  describe('tabClick output', () => {
    it('emits the tab path when a tab body is clicked', () => {
      fixture.componentRef.setInput('openFiles', [
        makeTab({ path: 'src/main.ts' }),
        makeTab({ path: 'src/app.ts' }),
      ]);
      fixture.detectChanges();

      const emitSpy = jest.spyOn(component.tabClick, 'emit');
      const tabs = fixture.nativeElement.querySelectorAll('.file-tab');
      tabs[1].click();

      expect(emitSpy).toHaveBeenCalledWith('src/app.ts');
    });

    it('does not emit tabClick when the close button is clicked (event stopped)', () => {
      fixture.componentRef.setInput('openFiles', [
        makeTab({ path: 'src/main.ts' }),
      ]);
      fixture.detectChanges();

      const tabEmitSpy = jest.spyOn(component.tabClick, 'emit');
      const closeBtn = fixture.nativeElement.querySelector('.close-btn') as HTMLElement;
      closeBtn.click();

      expect(tabEmitSpy).not.toHaveBeenCalled();
    });
  });

  describe('closeTab output', () => {
    it('emits the tab path when the close button is clicked', () => {
      fixture.componentRef.setInput('openFiles', [
        makeTab({ path: 'src/main.ts' }),
        makeTab({ path: 'src/app.ts' }),
      ]);
      fixture.detectChanges();

      const emitSpy = jest.spyOn(component.closeTab, 'emit');
      const closeButtons = fixture.nativeElement.querySelectorAll('.close-btn');
      closeButtons[0].click();

      expect(emitSpy).toHaveBeenCalledWith('src/main.ts');
    });

    it('stops propagation on the close click (does not also activate)', () => {
      // This guards against a regression where clicking close on an
      // inactive tab would bubble up, re-activate the tab, and then
      // close it — confusing UX. The handler explicitly calls
      // stopPropagation so the parent only ever sees closeTab.
      fixture.componentRef.setInput('openFiles', [
        makeTab({ path: 'src/main.ts' }),
      ]);
      fixture.componentRef.setInput('activePath', 'src/other.ts');
      fixture.detectChanges();

      const tabEmitSpy = jest.spyOn(component.tabClick, 'emit');
      const closeBtn = fixture.nativeElement.querySelector('.close-btn') as HTMLElement;
      closeBtn.click();

      expect(tabEmitSpy).not.toHaveBeenCalled();
    });
  });

  // ── 6) hasActive computed ─────────────────────────────────────

  describe('hasActive computed', () => {
    it('is false when activePath is null', () => {
      expect(component.hasActive()).toBe(false);
    });

    it('is true when activePath is a non-null string', () => {
      fixture.componentRef.setInput('activePath', 'src/main.ts');
      expect(component.hasActive()).toBe(true);
    });
  });
});
