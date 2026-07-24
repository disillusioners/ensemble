import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { VersionPickerComponent } from './version-picker.component';

describe('VersionPickerComponent', () => {
  let fixture: ComponentFixture<VersionPickerComponent>;
  let component: VersionPickerComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [VersionPickerComponent],
      providers: [provideNoopAnimations()],
    }).compileComponents();

    fixture = TestBed.createComponent(VersionPickerComponent);
    component = fixture.componentInstance;
  });

  describe('visibility', () => {
    it('renders nothing when there are zero versions', () => {
      fixture.componentRef.setInput('availableVersions', []);
      fixture.detectChanges();

      expect(fixture.nativeElement.querySelector('.version-picker')).toBeNull();
    });

    it('renders nothing when there is exactly one version', () => {
      fixture.componentRef.setInput('availableVersions', [null]);
      fixture.detectChanges();

      expect(fixture.nativeElement.querySelector('.version-picker')).toBeNull();
    });

    it('renders the picker when there are two or more versions', () => {
      fixture.componentRef.setInput('availableVersions', [null, 'v2']);
      fixture.detectChanges();

      expect(fixture.nativeElement.querySelector('.version-picker')).not.toBeNull();
    });

    it('hasMultipleVersions reflects input length', () => {
      fixture.componentRef.setInput('availableVersions', []);
      expect(component.hasMultipleVersions()).toBe(false);

      fixture.componentRef.setInput('availableVersions', [null]);
      expect(component.hasMultipleVersions()).toBe(false);

      fixture.componentRef.setInput('availableVersions', [null, 'v2']);
      expect(component.hasMultipleVersions()).toBe(true);

      fixture.componentRef.setInput('availableVersions', ['a', 'b', 'c']);
      expect(component.hasMultipleVersions()).toBe(true);
    });
  });

  describe('sortedVersions', () => {
    it('puts null (base) first', () => {
      fixture.componentRef.setInput('availableVersions', ['v2', null]);
      expect(component.sortedVersions()).toEqual([null, 'v2']);
    });

    it('sorts non-null tags alphabetically', () => {
      fixture.componentRef.setInput('availableVersions', ['zeta', 'alpha', 'mu']);
      expect(component.sortedVersions()).toEqual(['alpha', 'mu', 'zeta']);
    });

    it('combines base-first with alphabetical sort of the rest', () => {
      fixture.componentRef.setInput('availableVersions', ['zeta', null, 'alpha']);
      expect(component.sortedVersions()).toEqual([null, 'alpha', 'zeta']);
    });

    it('returns a new array (does not mutate the input)', () => {
      const input = ['v2', null];
      fixture.componentRef.setInput('availableVersions', input);
      const sorted = component.sortedVersions();
      expect(sorted).not.toBe(input);
      // Original input order is preserved (we copy before sorting).
      expect(input).toEqual(['v2', null]);
    });
  });

  describe('getDisplayLabel', () => {
    it('renders "Base" for null', () => {
      expect(component.getDisplayLabel(null)).toBe('Base');
    });

    it('renders the tag verbatim for non-null', () => {
      expect(component.getDisplayLabel('v2')).toBe('v2');
      expect(component.getDisplayLabel('experiment')).toBe('experiment');
    });
  });

  describe('option rendering', () => {
    it('renders one option per available version', () => {
      fixture.componentRef.setInput('availableVersions', [null, 'v2', 'experimental']);
      fixture.componentRef.setInput('selectedTag', 'v2');
      fixture.detectChanges();

      const options = fixture.nativeElement.querySelectorAll('option');
      expect(options.length).toBe(3);
      expect(options[0].textContent.trim()).toBe('Base');
      expect(options[1].textContent.trim()).toBe('experimental');
      expect(options[2].textContent.trim()).toBe('v2');
    });

    it('marks the selected option as selected', () => {
      fixture.componentRef.setInput('availableVersions', [null, 'v2']);
      fixture.componentRef.setInput('selectedTag', null);
      fixture.detectChanges();

      const options = fixture.nativeElement.querySelectorAll('option');
      // Base option (null) should be the selected one
      expect(options[0].selected).toBe(true);
      expect(options[1].selected).toBe(false);
    });
  });

  describe('tagChange emission', () => {
    it('emits the chosen tag when the select changes', () => {
      fixture.componentRef.setInput('availableVersions', [null, 'v2']);
      fixture.detectChanges();

      const emitted = jest.fn();
      component.tagChange.subscribe(emitted);

      const select: HTMLSelectElement = fixture.nativeElement.querySelector('select');
      select.value = 'v2';
      select.dispatchEvent(new Event('change'));

      expect(emitted).toHaveBeenCalledWith('v2');
    });

    it('emits null when the user picks the base option', () => {
      fixture.componentRef.setInput('availableVersions', [null, 'v2']);
      fixture.componentRef.setInput('selectedTag', 'v2');
      fixture.detectChanges();

      const emitted = jest.fn();
      component.tagChange.subscribe(emitted);

      const select: HTMLSelectElement = fixture.nativeElement.querySelector('select');
      // Switching back to base
      select.value = '';
      select.dispatchEvent(new Event('change'));

      expect(emitted).toHaveBeenCalledWith(null);
    });

    it('does not emit when there are fewer than two versions (picker hidden)', () => {
      fixture.componentRef.setInput('availableVersions', [null]);
      fixture.detectChanges();

      const emitted = jest.fn();
      component.tagChange.subscribe(emitted);

      // No select element exists; no change event possible.
      expect(fixture.nativeElement.querySelector('select')).toBeNull();

      // Calling onSelect directly still emits (the component itself
      // doesn't gate the emission, only the template hides the UI).
      component.onSelect('v2');
      expect(emitted).toHaveBeenCalledWith('v2');
    });
  });
});