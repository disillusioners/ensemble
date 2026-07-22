import { Component, OnInit, signal, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatButtonModule } from '@angular/material/button';
import { MatSnackBar } from '@angular/material/snack-bar';
import { SettingsService } from '../../services/settings.service';
import {
  SearchableSelectComponent,
  SearchableSelectOption,
} from '../../components/searchable-select/searchable-select.component';

const PREDEFINED_LANGUAGES = [
  'Auto',
  'English',
  'Spanish',
  'Chinese',
  'French',
  'German',
  'Japanese',
  'Korean',
  'Portuguese',
  'Russian',
  'Arabic',
  'Vietnamese',
  'Italian',
  'Dutch',
  'Hindi',
];

const CUSTOM_OPTION_VALUE = 'Other (custom)';
const DEFAULT_LANGUAGE = 'Auto';
const STORAGE_KEY = 'settings-language-preference';

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatFormFieldModule,
    MatInputModule,
    MatButtonModule,
    SearchableSelectComponent,
  ],
  templateUrl: './settings.component.html',
  styleUrl: './settings.component.scss',
})
export class SettingsComponent implements OnInit {
  private readonly settingsService = inject(SettingsService);
  private readonly snackBar = inject(MatSnackBar);

  readonly languages = PREDEFINED_LANGUAGES;
  readonly customOptionValue = CUSTOM_OPTION_VALUE;
  readonly selectedLanguage = signal<string>(DEFAULT_LANGUAGE);
  readonly customLanguage = signal<string>('');
  readonly isCustom = signal<boolean>(false);
  readonly saving = signal<boolean>(false);

  /**
   * Options rendered by the preferred-language
   * ``app-searchable-select``. Predefined languages are mapped to
   * ``{value, label}`` pairs and a trailing ``Other (custom)``
   * sentinel — the latter's value is ``CUSTOM_OPTION_VALUE`` so the
   * ``onLanguageChange`` handler can detect the custom-entry
   * intent without needing to know the displayed label.
   */
  readonly languageOptions: SearchableSelectOption<string>[] = [
    ...PREDEFINED_LANGUAGES.map((l) => ({ value: l, label: l })),
    { value: CUSTOM_OPTION_VALUE, label: 'Other (custom)' },
  ];

  ngOnInit(): void {
    this.loadFromStorage();
    this.loadFromApi();
  }

  private loadFromStorage(): void {
    let saved: string | null = null;
    try {
      saved = localStorage.getItem(STORAGE_KEY);
    } catch {
      // silently ignore
    }
    if (!saved) {
      return;
    }
    this.applyPreference(saved);
  }

  private loadFromApi(): void {
    // Capture whether localStorage had a cached value so we can decide on a clean
    // fallback if the API errors out.
    let hadCachedValue = false;
    try {
      hadCachedValue = localStorage.getItem(STORAGE_KEY) !== null;
    } catch {
      hadCachedValue = false;
    }

    this.settingsService.getLanguagePreference().subscribe({
      next: (pref) => {
        const lang = pref?.language;
        if (lang) {
          this.applyPreference(lang);
          this.persistToStorage(lang);
        }
      },
      error: () => {
        // If the API fails AND there was no localStorage cached value, fall back to
        // the default language. Otherwise the selectedLanguage signal already reflects
        // the localStorage value loaded earlier in ngOnInit.
        if (!hadCachedValue) {
          this.selectedLanguage.set(DEFAULT_LANGUAGE);
        }
        this.snackBar.open('Failed to load language preference', 'Dismiss', {
          duration: 5000,
          panelClass: 'error-snackbar',
        });
      },
    });
  }

  /**
   * Apply a backend-supplied language value to the view state, splitting
   * predefined vs custom languages so the UI reflects the right mode.
   */
  private applyPreference(language: string): void {
    if (PREDEFINED_LANGUAGES.includes(language)) {
      this.selectedLanguage.set(language);
      this.isCustom.set(false);
    } else {
      this.selectedLanguage.set(CUSTOM_OPTION_VALUE);
      this.isCustom.set(true);
      this.customLanguage.set(language);
    }
  }

  private persistToStorage(language: string): void {
    try {
      localStorage.setItem(STORAGE_KEY, language);
    } catch {
      // silently ignore
    }
  }

  onLanguageChange(value: string): void {
    if (value === CUSTOM_OPTION_VALUE) {
      this.isCustom.set(true);
      // Do not save yet — wait for the user to type a value and click Save.
      return;
    }
    this.isCustom.set(false);
    this.save(value);
  }

  onCustomLanguageChange(event: Event): void {
    const target = event.target as HTMLInputElement;
    this.customLanguage.set(target.value);
  }

  saveCustom(): void {
    const lang = this.customLanguage().trim();
    if (!lang) {
      return;
    }
    this.save(lang);
  }

  private save(language: string): void {
    // Capture the previous UI state so we can revert on failure.
    const previousSelectedLanguage = this.selectedLanguage();
    const previousCustomLanguage = this.customLanguage();

    this.saving.set(true);
    this.settingsService.setLanguagePreference(language).subscribe({
      next: () => {
        // Sync the model to the newly saved language via the centralized
        // applyPreference helper so the dropdown reflects predefined vs
        // custom mode correctly. For custom saves, this sets
        // selectedLanguage to CUSTOM_OPTION_VALUE (matching an actual
        // <mat-option>) and stores the typed text in customLanguage;
        // without this, mat-select renders with no selection highlighted
        // because the raw custom string has no matching option.
        this.applyPreference(language);
        this.persistToStorage(language);
        this.saving.set(false);
        this.snackBar.open(`Language preference set to ${language}`, 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar',
        });
      },
      error: () => {
        // Revert UI to last known good state since the backend rejected the change.
        this.selectedLanguage.set(previousSelectedLanguage);
        this.customLanguage.set(previousCustomLanguage);
        this.saving.set(false);
        this.snackBar.open('Failed to save language preference', 'Dismiss', {
          duration: 5000,
          panelClass: 'error-snackbar',
        });
      },
    });
  }
}
