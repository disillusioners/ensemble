import { Component, input, output, signal, computed, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { ConfigSchemaField } from '../../models';

@Component({
  selector: 'app-config-schema-form',
  standalone: true,
  imports: [CommonModule, FormsModule, MatSlideToggleModule],
  templateUrl: './config-schema-form.html',
  styleUrl: './config-schema-form.scss'
})
export class ConfigSchemaFormComponent implements OnInit {
  readonly schema = input<ConfigSchemaField[]>([]);
  readonly initialValues = input<Record<string, unknown>>({});

  readonly valuesChange = output<Record<string, unknown>>();
  readonly isValid = output<boolean>();

  protected readonly values = signal<Record<string, unknown>>({});
  protected readonly touched = signal<Set<string>>(new Set());

  protected readonly argsFields = computed(() =>
    this.schema().filter(f => f.section === 'args')
  );

  protected readonly envFields = computed(() =>
    this.schema().filter(f => f.section === 'env')
  );

  ngOnInit(): void {
    this.initializeValues();
    this.emitValues();
    this.emitValidity();
  }

  /**
   * Re-initializes form values from current inputs.
   * Called externally when initial values change (e.g., after server reset).
   */
  public resetForm(): void {
    this.initializeValues();
    this.touched.set(new Set());
    this.emitValues();
    this.emitValidity();
  }

  private initializeValues(): void {
    const initial: Record<string, unknown> = {};

    for (const field of this.schema()) {
      const key = field.key;

      // Priority: initialValues > field.default > type-based default
      if (key in this.initialValues()) {
        initial[key] = this.initialValues()[key];
      } else if (field.default !== undefined) {
        initial[key] = field.default;
      } else {
        // Type-based defaults
        switch (field.type) {
          case 'boolean':
            initial[key] = false;
            break;
          case 'number':
            initial[key] = 0;
            break;
          default:
            initial[key] = '';
        }
      }
    }

    this.values.set(initial);
  }

  protected onFieldChange(key: string, value: unknown): void {
    this.values.update(current => ({
      ...current,
      [key]: value
    }));
    this.touched.update(current => new Set(current).add(key));
    this.emitValues();
    this.emitValidity();
  }

  protected onTextChange(key: string, event: Event): void {
    const target = event.target as HTMLInputElement | HTMLSelectElement;
    this.onFieldChange(key, target.value);
  }

  protected onNumberChange(key: string, event: Event): void {
    const target = event.target as HTMLInputElement;
    const value = target.value === '' ? 0 : Number(target.value);
    this.onFieldChange(key, value);
  }

  protected onBooleanChange(key: string, checked: boolean): void {
    this.onFieldChange(key, checked);
  }

  private emitValues(): void {
    this.valuesChange.emit(this.values());
  }

  private emitValidity(): void {
    this.isValid.emit(this.validateForm());
  }

  private validateForm(): boolean {
    for (const field of this.schema()) {
      if (field.required) {
        const value = this.values()[field.key];
        if (value === undefined || value === null || value === '') {
          return false;
        }
      }

      if (field.type === 'number') {
        const value = this.values()[field.key];
        if (typeof value === 'number') {
          if (field.min !== undefined && value < field.min) {
            return false;
          }
          if (field.max !== undefined && value > field.max) {
            return false;
          }
        }
      }
    }
    return true;
  }

  protected getFieldValue(key: string): unknown {
    return this.values()[key];
  }

  protected isFieldInvalid(field: ConfigSchemaField): boolean {
    if (!this.touched().has(field.key)) {
      return false;
    }

    if (field.required) {
      const value = this.values()[field.key];
      if (value === undefined || value === null || value === '') {
        return true;
      }
    }

    if (field.type === 'number') {
      const value = this.values()[field.key];
      if (typeof value === 'number') {
        if (field.min !== undefined && value < field.min) {
          return true;
        }
        if (field.max !== undefined && value > field.max) {
          return true;
        }
      }
    }

    return false;
  }
}
