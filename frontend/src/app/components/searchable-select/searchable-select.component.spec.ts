import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { SearchableSelectComponent, SearchableSelectOption } from './searchable-select.component';

const OPTIONS: SearchableSelectOption[] = [
  { value: 'apple', label: 'Apple' },
  { value: 'apricot', label: 'Apricot' },
  { value: 'banana', label: 'Banana' },
  { value: 'cherry', label: 'Cherry' },
];

describe('SearchableSelectComponent', () => {
  let fixture: ComponentFixture<SearchableSelectComponent<string>>;
  let component: SearchableSelectComponent<string>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [SearchableSelectComponent],
      providers: [provideNoopAnimations()],
    }).compileComponents();

    fixture = TestBed.createComponent(SearchableSelectComponent<string>);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('options', OPTIONS);
    fixture.detectChanges();
  });

  // ── CVA label display ────────────────────────────────────────────
  describe('CVA label display', () => {
    it('renders the option label for a strict-equal value', () => {
      component.writeValue('banana');
      expect(component.displayText()).toBe('Banana');
      expect(component.displayText()).toBe('Banana');
    });

    it('clears the displayed text when the value is null', () => {
      component.writeValue('apple');
      component.writeValue(null);
      expect(component.displayText()).toBe('');
      expect(component.displayText()).toBe('');
    });

    it('uses strict equality only — different-case ids do not map', () => {
      component.writeValue('APPLE');
      expect(component.displayText()).toBe('');
    });
  });

  // ── Filtering ────────────────────────────────────────────────────
  describe('filteredOptions', () => {
    it('returns all options when search text is empty', () => {
      component.displayText.set('');
      expect(component.filteredOptions().length).toBe(OPTIONS.length);
    });

    it('returns all options when search text is whitespace only', () => {
      component.displayText.set('   ');
      expect(component.filteredOptions().length).toBe(OPTIONS.length);
    });

    it('filters case-insensitively', () => {
      component.displayText.set('BLUE');
      expect(component.filteredOptions().map((o) => o.label)).toEqual([]);
      component.displayText.set('AP');
      expect(component.filteredOptions().map((o) => o.label)).toEqual([
        'Apple',
        'Apricot',
      ]);
    });

    it('returns an empty list when nothing matches', () => {
      component.displayText.set('zzz');
      expect(component.filteredOptions()).toEqual([]);
    });
  });

  // ── onInput ──────────────────────────────────────────────────────
  describe('onInput', () => {
    it('reads HTMLInputElement.value into displayText', () => {
      const input = document.createElement('input');
      input.value = 'ban';
      component.onInput({ target: input } as unknown as Event);
      expect(component.displayText()).toBe('ban');
    });
  });

  // ── Enter-to-select (autoActiveFirstOption) ──────────────────────
  // The custom `onEnter()` method was removed; Enter-to-select-first is
  // now handled natively by Material's `autoActiveFirstOption` directive
  // on `<mat-autocomplete>` (template-level). That behaviour is verified
  // via integration/browser testing. Here we verify the supporting
  // component-level invariants instead.
  describe('Enter-to-select (autoActiveFirstOption)', () => {
    it('onTouched is a public function (called on blur)', () => {
      expect(typeof component.onTouched).toBe('function');
      expect(() => component.onTouched()).not.toThrow();
    });

    it('filteredOptions returns the first match that autoActiveFirstOption would activate', () => {
      // When the user types "AP", the first filtered option is "Apple".
      // autoActiveFirstOption highlights it and Enter selects it natively.
      component.displayText.set('AP');
      const first = component.filteredOptions()[0];
      expect(first).toBeDefined();
      expect(first.value).toBe('apple');
      expect(first.label).toBe('Apple');
    });
  });

  // ── onOptionSelected ─────────────────────────────────────────────
  describe('onOptionSelected', () => {
    it('updates value, displayText, and emits through onChange', () => {
      let changed: string | null | undefined;
      component.registerOnChange((v) => (changed = v));

      component.onOptionSelected(OPTIONS[2]); // Banana

      expect(component.value()).toBe('banana');
      expect(component.displayText()).toBe('Banana');
      expect(changed).toBe('banana');
    });
  });

  // ── onPanelClosed restoration ────────────────────────────────────
  describe('onPanelClosed', () => {
    it('keeps displayText when it matches an option label exactly (optionally selects)', () => {
      // Simulate: user typed the exact label "Cherry" and dismissed the panel.
      component.displayText.set('Cherry');
      component.displayText.set('Cherry');
      component.onPanelClosed();
      expect(component.displayText()).toBe('Cherry');
    });

    it('restores to the selected value label when displayText is not an option label', () => {
      component.writeValue('banana');
      component.displayText.set('Typing without selecting');
      component.displayText.set('Typing without selecting');

      component.onPanelClosed();
      expect(component.displayText()).toBe('Banana');
      expect(component.displayText()).toBe('Banana');
    });

    it('clears displayText when no value is selected and text is not an option label', () => {
      component.displayText.set('garbage');
      component.displayText.set('garbage');
      component.onPanelClosed();
      expect(component.displayText()).toBe('');
    });

    it('matches case-insensitively — typing "BANANA" selects the "Banana" option', () => {
      let changed: string | null | undefined;
      component.registerOnChange((v) => (changed = v));

      component.displayText.set('BANANA');
      component.onPanelClosed();

      expect(component.value()).toBe('banana');
      expect(component.displayText()).toBe('Banana');
      expect(changed).toBe('banana');
    });
  });

  // ── Disabled state ───────────────────────────────────────────────
  describe('disabled (input + setDisabledState)', () => {
    it('reflects the disabled input via effectiveDisabled', () => {
      expect(component.effectiveDisabled()).toBe(false);
      fixture.componentRef.setInput('disabled', true);
      expect(component.effectiveDisabled()).toBe(true);
    });

    it('reflects setDisabledState via effectiveDisabled', () => {
      component.setDisabledState(true);
      expect(component.effectiveDisabled()).toBe(true);
      component.setDisabledState(false);
      expect(component.effectiveDisabled()).toBe(false);
    });

    it('combines disabled input AND setDisabledState (true if either is true)', () => {
      fixture.componentRef.setInput('disabled', false);
      component.setDisabledState(true);
      expect(component.effectiveDisabled()).toBe(true);
    });
  });

  // ── Defaults ─────────────────────────────────────────────────────
  describe('defaults', () => {
    it('defaults appearance to "outline"', () => {
      expect(component.appearance()).toBe('outline');
    });

    it('defaults options to an empty array', () => {
      const fresh = TestBed.createComponent(SearchableSelectComponent<string>);
      expect(fresh.componentInstance.options()).toEqual([]);
    });
  });

  // ── onFocus ──────────────────────────────────────────────────────
  describe('onFocus', () => {
    it('clears displayText so filteredOptions shows all options', () => {
      // Set a selected value with display text
      component.writeValue('banana');
      expect(component.displayText()).toBe('Banana'); // verify setup

      // Simulate focus — should clear display text
      component.onFocus();

      // displayText should now be empty
      expect(component.displayText()).toBe('');
      // filteredOptions should return ALL options (not filtered by "Banana")
      expect(component.filteredOptions().length).toBe(OPTIONS.length);
    });

    it('does NOT change the underlying value when focusing', () => {
      component.writeValue('apple');
      component.onFocus();
      // Value is unchanged
      expect(component.value()).toBe('apple');
    });
  });
});
