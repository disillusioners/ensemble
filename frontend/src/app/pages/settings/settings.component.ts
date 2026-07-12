import { Component, OnInit, signal, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatSelectModule } from '@angular/material/select';
import { MatInputModule } from '@angular/material/input';
import { MatButtonModule } from '@angular/material/button';
import { MatSnackBar } from '@angular/material/snack-bar';
import { SettingsService } from '../../services/settings.service';

const PREDEFINED_LANGUAGES = [
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
const DEFAULT_LANGUAGE = 'English';
const STORAGE_KEY = 'settings-language-preference';

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatFormFieldModule,
    MatSelectModule,
    MatInputModule,
    MatButtonModule,
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
  readonly loaded = signal<boolean>(false);

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
    this.settingsService.getLanguagePreference().subscribe({
      next: (pref) => {
        const lang = pref?.language;
        if (lang) {
          this.applyPreference(lang);
          this.persistToStorage(lang);
        }
        this.loaded.set(true);
      },
      error: () => {
        // Keep the localStorage value (already loaded) if present.
        // If neither localStorage nor API gave us a value, default to English.
        if (!this.loaded() && !this.selectedLanguage()) {
          this.selectedLanguage.set(DEFAULT_LANGUAGE);
        }
        this.loaded.set(true);
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
    this.saving.set(true);
    this.settingsService.setLanguagePreference(language).subscribe({
      next: () => {
        this.persistToStorage(language);
        this.saving.set(false);
        this.snackBar.open(`Language preference set to ${language}`, 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar',
        });
      },
      error: () => {
        // Do NOT touch localStorage on failure — keep the last known good value.
        this.saving.set(false);
        this.snackBar.open('Failed to save language preference', 'Dismiss', {
          duration: 5000,
          panelClass: 'error-snackbar',
        });
      },
    });
  }
}
