import { Component, OnDestroy, OnInit, computed, signal, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar } from '@angular/material/snack-bar';
import { SettingsService } from '../../services/settings.service';
import type { EditorType, VSCodeStatus } from '../../models';
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
const EDITOR_STORAGE_KEY = 'settings-editor-preference';
const STATUS_POLL_INTERVAL_MS = 2000;
const DEFAULT_EDITOR: EditorType = 'builtin';

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatFormFieldModule,
    MatInputModule,
    MatButtonModule,
    MatProgressSpinnerModule,
    SearchableSelectComponent,
  ],
  templateUrl: './settings.component.html',
  styleUrl: './settings.component.scss',
})
export class SettingsComponent implements OnInit, OnDestroy {
  private readonly settingsService = inject(SettingsService);
  private readonly snackBar = inject(MatSnackBar);

  readonly languages = PREDEFINED_LANGUAGES;
  readonly customOptionValue = CUSTOM_OPTION_VALUE;
  readonly selectedLanguage = signal<string>(DEFAULT_LANGUAGE);
  readonly customLanguage = signal<string>('');
  readonly isCustom = signal<boolean>(false);
  readonly saving = signal<boolean>(false);

  // Editor preference state — public readonly so the template can bind to signals
  // directly under Angular's strictTemplates. `applyingEditor` mirrors the existing
  // `saving` signal pattern; `vscodeStatus` drives the status badge.
  readonly selectedEditor = signal<EditorType>(DEFAULT_EDITOR);
  readonly savedEditor = signal<EditorType>(DEFAULT_EDITOR);
  readonly applyingEditor = signal<boolean>(false);
  readonly vscodeStatus = signal<VSCodeStatus | null>(null);
  // True when the user has changed the radio selection but has not yet applied.
  readonly editorDirty = computed(() => this.selectedEditor() !== this.savedEditor());

  private statusPollTimer: ReturnType<typeof setInterval> | null = null;

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
    this.loadEditorPreference();
  }

  ngOnDestroy(): void {
    this.stopStatusPolling();
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
   * Load the editor preference on init. We seed `selectedEditor` and `savedEditor`
   * from the same value so the Apply button starts disabled — the dirty computation
   * compares the two signals.
   *
   * We optimistically seed from localStorage for an instant first paint; the API
   * response is the source of truth and will overwrite the cache on success.
   */
  private loadEditorPreference(): void {
    let cached: string | null = null;
    try {
      cached = localStorage.getItem(EDITOR_STORAGE_KEY);
    } catch {
      cached = null;
    }

    // Read cached value first; validate against the EditorType union so an
    // invalid/legacy localStorage entry can't poison the UI.
    const cachedEditor = this.coerceEditorType(cached);
    if (cachedEditor) {
      this.selectedEditor.set(cachedEditor);
      this.savedEditor.set(cachedEditor);
    }

    let hadCached = cached !== null;
    this.settingsService.getEditorPreference().subscribe({
      next: (resp) => {
        const editor = this.coerceEditorType(resp?.editor);
        if (editor) {
          this.selectedEditor.set(editor);
          this.savedEditor.set(editor);
          this.persistEditorToStorage(editor);
          // If user has VS Code selected, start polling so the badge updates live.
          if (editor === 'vscode') {
            this.startStatusPolling();
          }
        }
      },
      error: () => {
        // On failure, fall back to defaults only if nothing was cached.
        if (!hadCached) {
          this.selectedEditor.set(DEFAULT_EDITOR);
          this.savedEditor.set(DEFAULT_EDITOR);
        }
        this.snackBar.open('Failed to load editor preference', 'Dismiss', {
          duration: 5000,
          panelClass: 'error-snackbar',
        });
      },
    });
  }

  /**
   * Narrow an arbitrary string to the EditorType union. Returns null when the
   * input is missing or unrecognized, letting callers decide on a fallback.
   */
  private coerceEditorType(value: string | null | undefined): EditorType | null {
    if (value === 'builtin' || value === 'vscode') {
      return value;
    }
    return null;
  }

  /**
   * Update the in-memory selection when a radio changes. This only updates the
   * working selection — saving requires clicking Apply so unsaved radio toggles
   * don't fire network requests on every click.
   */
  onEditorSelectionChange(editor: EditorType): void {
    this.selectedEditor.set(editor);
  }

  /**
   * Persist the working selection to the backend. On success we sync `savedEditor`
   * (which flips `editorDirty` back to false) and start polling if VS Code is the
   * newly-saved choice. On failure we deliberately keep `savedEditor` unchanged so
   * the radio reflects the last known good state on next render.
   */
  saveEditor(): void {
    const target = this.selectedEditor();
    this.applyingEditor.set(true);
    this.settingsService.setEditorPreference(target).subscribe({
      next: (resp) => {
        const confirmed = this.coerceEditorType(resp?.editor) ?? target;
        this.savedEditor.set(confirmed);
        this.persistEditorToStorage(confirmed);
        this.applyingEditor.set(false);
        this.snackBar.open(`Editor preference set to ${this.editorLabel(confirmed)}`, 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar',
        });
        if (confirmed === 'vscode') {
          this.startStatusPolling();
        } else {
          // Built-in was chosen — stop polling and clear stale status.
          this.stopStatusPolling();
          this.vscodeStatus.set(null);
        }
      },
      error: (err) => {
        this.applyingEditor.set(false);
        // 503 indicates the code-server backend isn't available yet — surface the
        // specific reason from the backend's `detail.error` so the user can act on
        // it (install binary, check logs, restart daemon) instead of a single
        // catch-all hint. All other failures get a generic message.
        const isUnavailable = err?.status === 503;
        let message: string;
        if (isUnavailable) {
          const detail = err?.error?.detail;
          switch (detail?.error) {
            case 'code-server binary not found':
              message = 'VS Code editor (code-server) is not installed. Install code-server and try again.';
              break;
            case 'VS Code server failed to start':
              message = detail?.detail?.trim() || 'VS Code server failed to start. Check server logs for details.';
              break;
            case 'VS Code server manager not initialized':
              message = 'VS Code server manager not initialized. Try restarting the daemon.';
              break;
            case 'Project repository not initialized':
              message = 'VS Code settings cannot be saved — project repository is not initialized. Restart the daemon.';
              break;
            default:
              // Malformed/missing detail — fall back to the historical generic hint.
              message = 'VS Code editor is not installed. Install code-server and try again.';
          }
        } else {
          message = 'Failed to save editor preference';
        }
        this.snackBar.open(message, 'Dismiss', {
          duration: 5000,
          panelClass: 'error-snackbar',
        });
      },
    });
  }

  /**
   * Begin polling VS Code status every 2s. The interval auto-stops once we
   * observe `running: true` (terminal state) so we don't keep hitting the API
   * after the user has a working editor.
   */
  private startStatusPolling(): void {
    this.stopStatusPolling();
    // Fire one immediate request so the badge isn't blank during the 2s wait.
    this.pollStatus();
    this.statusPollTimer = setInterval(() => this.pollStatus(), STATUS_POLL_INTERVAL_MS);
  }

  private stopStatusPolling(): void {
    if (this.statusPollTimer !== null) {
      clearInterval(this.statusPollTimer);
      this.statusPollTimer = null;
    }
  }

  private pollStatus(): void {
    this.settingsService.getVscodeStatus().subscribe({
      next: (status) => {
        this.vscodeStatus.set(status);
        if (status?.running) {
          // Terminal state — stop polling to avoid hammering the API.
          this.stopStatusPolling();
        }
      },
      error: () => {
        // Treat status fetch errors as "stopped" rather than letting the badge
        // flicker. The next poll will retry.
        this.vscodeStatus.set({ running: false, port: null, allow_remote: false });
      },
    });
  }

  /**
   * Map a status badge key to a human label. Returns empty when no status has
   * been fetched yet so the badge hides instead of rendering an empty state.
   */
  vscodeStatusLabel(): string {
    const status = this.vscodeStatus();
    if (!status) {
      return '';
    }
    if (status.running) {
      return status.port !== null ? `Running on port ${status.port}` : 'Running';
    }
    // Distinguish "still spinning up" from "fully stopped". Without an
    // explicit `starting` flag from the backend we treat a non-running,
    // non-null status as the intermediate state — but only show it once the
    // backend has actually responded (vscodeStatus is non-null).
    const isStarting = this.statusPollTimer !== null && !status.running;
    return isStarting ? 'Starting' : 'Stopped';
  }

  vscodeStatusClass(): string {
    const status = this.vscodeStatus();
    if (!status) {
      return '';
    }
    if (status.running) {
      return 'running';
    }
    const isStarting = this.statusPollTimer !== null && !status.running;
    return isStarting ? 'starting' : 'stopped';
  }

  editorLabel(editor: EditorType): string {
    return editor === 'vscode' ? 'VS Code' : 'Built-in Editor';
  }

  private persistEditorToStorage(editor: EditorType): void {
    try {
      localStorage.setItem(EDITOR_STORAGE_KEY, editor);
    } catch {
      // silently ignore
    }
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
