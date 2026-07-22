import {
  ChangeDetectionStrategy,
  Component,
  ViewChild,
  computed,
  effect,
  forwardRef,
  input,
  signal,
} from '@angular/core';
import { ControlValueAccessor, NG_VALUE_ACCESSOR } from '@angular/forms';
import { MatAutocompleteModule, MatAutocompleteTrigger } from '@angular/material/autocomplete';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

export interface SearchableSelectOption<V = any> {
  value: V;
  label: string;
}

/**
 * Searchable single-select. Integrates with Angular forms via
 * `ControlValueAccessor`. Empty search text shows all options;
 * typing filters case-insensitively. Enter selects the first
 * match (and `preventDefault`s); Escape/click-out closes the
 * panel and restores the visible text to the selected label.
 */
@Component({
  selector: 'app-searchable-select',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [MatAutocompleteModule, MatFormFieldModule, MatInputModule],
  templateUrl: './searchable-select.component.html',
  styleUrl: './searchable-select.component.scss',
  providers: [
    {
      provide: NG_VALUE_ACCESSOR,
      useExisting: forwardRef(() => SearchableSelectComponent),
      multi: true,
    },
  ],
})
export class SearchableSelectComponent<V = any> implements ControlValueAccessor {
  readonly options = input<SearchableSelectOption<V>[]>([]);
  readonly placeholder = input<string>('');
  readonly label = input<string>('');
  readonly appearance = input<'outline' | 'fill'>('outline');
  readonly disabled = input<boolean>(false);

  // Re-sync the visible display text whenever `options` changes after
  // `writeValue` has already been called. Without this, a parent that
  // rebinds options late (e.g. async data) would leave the input
  // showing the wrong label until the user types something.
  constructor() {
    effect(() => {
      this.options(); // track for re-derivation
      const current = this.value();
      if (current !== null && current !== undefined) {
        this.displayText.set(this.labelFor(current));
      }
    });
  }

  /** Mirrors `setDisabledState` so the template can reflect both inputs. */
  private readonly disabledFromCva = signal<boolean>(false);
  /** Combined disabled state — template-facing. */
  readonly effectiveDisabled = computed(
    () => this.disabled() || this.disabledFromCva(),
  );

  readonly value = signal<V | null>(null);
  readonly displayText = signal<string>('');

  readonly filteredOptions = computed<SearchableSelectOption<V>[]>(() => {
    const term = this.displayText().trim().toLowerCase();
    const all = this.options();
    if (term === '') return all;
    return all.filter((o) => o.label.toLowerCase().includes(term));
  });

  @ViewChild(MatAutocompleteTrigger, { static: false })
  readonly autoTrigger?: MatAutocompleteTrigger;

  private onChange: (value: V | null) => void = () => undefined;
  private onTouched: () => void = () => undefined;

  // ── CVA ────────────────────────────────────────────────────────────
  writeValue(value: V | null): void {
    this.value.set(value);
    this.displayText.set(this.labelFor(value));
  }
  registerOnChange(fn: (value: V | null) => void): void { this.onChange = fn; }
  registerOnTouched(fn: () => void): void { this.onTouched = fn; }
  setDisabledState(isDisabled: boolean): void { this.disabledFromCva.set(isDisabled); }

  // ── Template handlers ──────────────────────────────────────────────
  onInput(event: Event): void {
    const v = (event.target as HTMLInputElement).value;
    this.displayText.set(v);
  }

  /**
   * Enter: if there is at least one filtered match, select the
   * first and `preventDefault()`. Otherwise DO NOTHING — do not
   * swallow the Enter key (per the explicit requirement).
   */
  onEnter(event: Event): void {
    const matches = this.filteredOptions();
    if (matches.length === 0) return;
    event.preventDefault();
    this.select(matches[0]);
  }

  /**
   * MatAutocomplete's optionSelected handler — receives the
   * selected option's `value` (a `SearchableSelectOption`) via
   * `$event.option.value`.
   */
  onOptionSelected(option: SearchableSelectOption<V>): void {
    this.select(option);
  }

  /** Restore the visible text to the selected label when the panel closes. */
  onPanelClosed(): void {
    const current = this.displayText();
    const exactMatch = this.options().find((o) => o.label === current);
    if (exactMatch !== undefined) {
      // User typed an exact label — optionally select it.
      if (exactMatch.value !== this.value()) {
        this.select(exactMatch);
      }
      return;
    }
    const label = this.labelFor(this.value());
    this.displayText.set(label);
    this.onTouched();
  }

  /** Public programmatic close for parents that need it. */
  closePanel(): void {
    this.autoTrigger?.closePanel();
  }

  // ── Internal ───────────────────────────────────────────────────────
  private select(option: SearchableSelectOption<V>): void {
    this.value.set(option.value);
    this.displayText.set(option.label);
    this.onChange(option.value);
    this.onTouched();
    this.closePanel();
  }

  private labelFor(value: V | null): string {
    if (value === null || value === undefined) return '';
    return this.options().find((o) => o.value === value)?.label ?? '';
  }

  /**
   * Public displayWith function for `<mat-autocomplete>`. Material
   * invokes this whenever it needs to render a value as text — e.g.
   * when the input is auto-populated after selection. Without this,
   * Material would render the raw option object. Keeping it public
   * because the template binds it directly.
   */
  displayLabel(option: SearchableSelectOption<V>): string {
    return option ? option.label : '';
  }
}
