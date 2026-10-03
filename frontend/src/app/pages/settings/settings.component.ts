import { Component, OnDestroy, OnInit, computed, signal, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar } from '@angular/material/snack-bar';
import { SettingsService } from '../../services/settings.service';
import { WorkspaceService } from '../../services/workspace.service';
import type { EditorType, VSCodeStatus } from '../../models';
import type { SnapshotUsageMetrics } from '../../services/settings.service';
import {
  SearchableSelectComponent,
  SearchableSelectOption,
} from '../../components/searchable-select/searchable-select.component';
import {
  formatTimezoneOffset,
  getBrowserNativeTimezones,
} from '../../utils/timezone-format';

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

/**
 * Timezone preference — UI-internal sentinel that maps to the
 * API's "null" representation. Mirrors the language handler's
 * pattern of using a string sentinel in the dropdown so the
 * generic `SearchableSelectOption<string>` binding keeps working;
 * the value is translated to `null` only at the API boundary.
 */
const TZ_AUTO_VALUE = '__tz_auto__';
const TZ_STORAGE_KEY = 'settings-timezone-preference';

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
  private readonly workspaceService = inject(WorkspaceService);
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

  // Blueprint peak-hours state. The scan service reads the same window
  // on every execute() tick, so saving here takes effect on the next
  // scheduled scan with no daemon restart.
  readonly peakStart = signal<number>(12);
  readonly peakEnd = signal<number>(20);
  readonly peakTzOffset = signal<number>(7);
  readonly peakHoursLoading = signal<boolean>(false);
  readonly peakHoursSaving = signal<boolean>(false);

  // R15 snapshot-create toggle (Wave 3) — mirrors the editor
  // radio-button shape (dirty + saved + applying signals) so the
  // Apply button starts disabled and flips only after the server
  // confirms.
  readonly snapshotCreateEnabled = signal<boolean>(false);
  readonly savedSnapshotCreateEnabled = signal<boolean>(false);
  readonly savingSnapshotCreate = signal<boolean>(false);
  readonly snapshotCreateDirty = computed(
    () =>
      this.snapshotCreateEnabled() !== this.savedSnapshotCreateEnabled(),
  );

  // R16 monitoring metrics surface (Wave 3). Loaded on init; the
  // surface is read-only — no PUT endpoint — so a single signal is
  // enough. MONITORING ONLY.
  readonly snapshotMetrics = signal<SnapshotUsageMetrics | null>(null);
  readonly snapshotMetricsCaptureEntries = computed(() => {
    const metrics = this.snapshotMetrics();
    if (!metrics) {
      return [] as Array<{ agent: string; created: number }>;
    }
    return Object.entries(metrics.capture_counts ?? {})
      .map(([agent, count]) => ({
        agent,
        created: Number((count as { created?: number })?.created ?? 0),
      }))
      .sort((a, b) => b.created - a.created || a.agent.localeCompare(b.agent));
  });
  readonly snapshotMetricsSpawnEntries = computed(() => {
    const metrics = this.snapshotMetrics();
    if (!metrics) {
      return [] as Array<{ snapshot_id: string; count: number }>;
    }
    return (metrics.spawn_counts_per_snapshot ?? [])
      .slice()
      .sort(
        (a, b) =>
          b.count - a.count || a.snapshot_id.localeCompare(b.snapshot_id),
      );
  });

  // ── Timezone preference (user-timezone-setting feature) ───────────
  //
  // Mirrors the language preference flow (loadFromStorage → API seed
  // → optimistic save → revert-on-failure), with two quirks:
  //
  //   * The "Auto / not set" option is rendered as a string sentinel
  //     (`TZ_AUTO_VALUE`) so the generic SearchableSelectOption<string>
  //     binding works exactly like the language picker. The sentinel
  //     is translated to API `null` at the save boundary only.
  //   * The picker is fed by the API list (`GET /api/settings/timezones`,
  //     canonical IANA from the backend tzdata), with `Intl.supportedValuesOf`
  //     as the fallback. An empty Intl list (callable but returning [])
  //     is treated as unsupported so the picker does not render a zone-less
  //     dead-end dropdown. When neither source yields zones we hide the
  //     picker and fall back to a plain text input that accepts any IANA
  //     name — server-side validation handles invalid values.

  readonly selectedTimezone = signal<string>(TZ_AUTO_VALUE);
  readonly customTimezone = signal<string>('');
  readonly savingTimezone = signal<boolean>(false);
  /**
   * Server-sourced IANA zone list. Populated by
   * ``loadTimezoneOptionsFromApi()`` — `null` means not yet loaded
   * (or the request failed), in which case the template falls back
   * to either ``Intl.supportedValuesOf`` or the plain text input.
   *
   * Source priority: backend ``GET /api/settings/timezones`` →
   * ``Intl.supportedValuesOf('timeZone')`` → text-input fallback.
   * The backend list is the authoritative source — it is derived from
   * the same ``zoneinfo`` tzdata the PUT validator uses, so the picker
   * and validator cannot disagree. (Older browsers'
   * ``Intl.supportedValuesOf`` returns deprecated aliases like
   * ``Asia/Saigon`` but lacks canonical ``Asia/Ho_Chi_Minh`` — the
   * drift the spec fixes.)
   */
  readonly apiTimezones = signal<string[] | null>(null);

  /**
   * Browser-native IANA option list, derived from the BEST AVAILABLE
   * source:
   *
   *   1. server-sourced list (`GET /api/settings/timezones`) — the
   *      authoritative source; same tzdata the validator uses, so
   *      picker and validator cannot disagree
   *   2. ``Intl.supportedValuesOf('timeZone')`` — browser-native; may
   *      drift on older ICU (deprecated aliases present, canonical
   *      names absent — e.g. ``Asia/Saigon`` is in but ``Asia/Ho_Chi_Minh``
   *      is not)
   *   3. empty list — both sources unavailable; the template hides
   *      the picker and surfaces the text-input fallback
   *
   * Empty when neither the server list nor the browser-native list
   * could be resolved so the template can surface the text-input
   * fallback.
   */
  readonly timezoneOptions = computed<SearchableSelectOption<string>[]>(() => {
    // 1. Server-sourced list — preferred. Prefer a NON-EMPTY list;
    //    an empty response (or null when not yet loaded) is the same
    //    as "not available" from the user's POV, and falling back to
    //    Intl keeps the picker usable when the endpoint is briefly
    //    broken.
    const api = this.apiTimezones();
    let zones: string[] | null = null;
    if (api !== null && api.length > 0) {
      zones = api;
    } else {
      // 2. Browser-native list — best effort. ``getBrowserNativeTimezones``
      //    returns an empty array both when the runtime lacks
      //    ``supportedValuesOf`` and when it is callable but returns
      //    nothing — the empty-Intl fallback (745afb13) is part of
      //    the shared helper, not the component.
      const intlZones = getBrowserNativeTimezones();
      if (intlZones.length > 0) {
        zones = intlZones;
      }
    }
    if (zones === null) {
      // 3. Both sources unavailable — picker is hidden via
      //    ``isTzNativeSupported()`` and the template surfaces the
      //    text-input row. Return only the Auto sentinel so the
      //    computed never lies about availability.
      return [{ value: TZ_AUTO_VALUE, label: 'Auto / not set' }];
    }
    return [
      { value: TZ_AUTO_VALUE, label: 'Auto / not set' },
      ...zones.map((zone) => ({
        value: zone,
        label: `${zone} (UTC${formatTimezoneOffset(zone)})`,
      })),
    ];
  });

  /**
   * True when BOTH the API list AND ``Intl.supportedValuesOf`` are
   * unavailable — drives the text-input fallback render. A loaded
   * (possibly empty) API list does NOT trigger the fallback as long
   * as the browser-native list yields at least one zone. A
   * callable-but-empty Intl probe DOES trigger the fallback — an
   * empty dropdown (Auto sentinel only) is a dead end with no way
   * to enter a custom zone.
   */
  readonly isTzNativeSupported = computed<boolean>(() => {
    const api = this.apiTimezones();
    if (api !== null && api.length > 0) {
      return true;
    }
    return getBrowserNativeTimezones().length > 0;
  });

  /** True when the user has not picked a zone — drives the hint line. */
  readonly isTimezoneUnset = computed(
    () => this.selectedTimezone() === TZ_AUTO_VALUE,
  );

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
    this.loadPeakHours();
    this.loadSnapshotCreateEnabled();
    this.loadSnapshotMetrics();
    this.loadTimezoneFromStorage();
    this.loadTimezoneFromApi();
    this.loadTimezoneOptionsFromApi();
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
        // Propagate the new mode to any open workspace so the editor switch
        // takes effect immediately instead of waiting for the next
        // WorkspaceService construction. `confirmed` is the authoritative
        // server-confirmed value, not the user's pre-save selection.
        this.workspaceService.setEditorMode(confirmed);
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
   * observe a terminal status so we don't keep hitting the API after startup
   * has completed.
   */
  private startStatusPolling(): void {
    this.stopStatusPolling();
    // Start the interval before the immediate request so a synchronous terminal
    // response can clear it.
    this.statusPollTimer = setInterval(() => this.pollStatus(), STATUS_POLL_INTERVAL_MS);
    this.pollStatus();
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
        if (status?.status === 'running' || status?.status === 'stopped' || status?.status === 'crashed') {
          // Terminal state — stop polling to avoid hammering the API.
          this.stopStatusPolling();
        }
      },
      error: () => {
        // Treat status fetch errors as "stopped" rather than letting the badge
        // flicker. The next poll will retry.
        this.vscodeStatus.set({ status: 'stopped' });
      },
    });
  }

  /**
   * Map a status badge key to a human label.
   */
  vscodeStatusLabel(): string {
    const status = this.vscodeStatus()?.status;
    switch (status) {
      case 'running':
        return 'Running';
      case 'starting':
        return 'Starting...';
      case 'stopping':
        return 'Stopping...';
      case 'stopped':
        return 'Stopped';
      case 'crashed':
        return 'Crashed';
      default:
        return 'Not started';
    }
  }

  vscodeStatusClass(): string {
    switch (this.vscodeStatus()?.status) {
      case 'running':
        return 'running';
      case 'starting':
        return 'starting';
      case 'stopping':
        return 'stopping';
      case 'stopped':
        return 'stopped';
      case 'crashed':
        return 'crashed';
      default:
        return '';
    }
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

  /**
   * Fetch the current peak-hours window from the backend. The three
   * signals are seeded with the canonical defaults (12:00 - 20:00 GMT+7)
   * — the API returns these too when no metadata is stored yet, so the
   * UI always renders a coherent window even before the round-trip
   * completes.
   */
  private loadPeakHours(): void {
    this.peakHoursLoading.set(true);
    this.settingsService.getBlueprintPeakHours().subscribe({
      next: (config) => {
        this.peakStart.set(config.start);
        this.peakEnd.set(config.end);
        this.peakTzOffset.set(config.tz_offset);
        this.peakHoursLoading.set(false);
      },
      error: () => {
        // Fall back to the defaults already seeded on the signals — the
        // user can still edit and save; the next load round-trip will
        // retry. A toast surfaces the failure so the operator knows the
        // values shown are local fallbacks, not the live server state.
        this.peakHoursLoading.set(false);
        this.snackBar.open('Failed to load peak hours — showing defaults', 'Dismiss', {
          duration: 5000,
          panelClass: 'error-snackbar',
        });
      },
    });
  }

  /**
   * Persist the three peak-hours values via PUT. Validation lives on
   * the backend Pydantic schema (start/end 0-23, tz_offset -12..14) —
   * any out-of-range input surfaces as a 422 and we display a generic
   * "validation failed" message; the signals are left untouched so the
   * operator can correct the offending field without losing the rest of
   * their in-progress edit.
   */
  savePeakHours(): void {
    this.peakHoursSaving.set(true);
    this.settingsService
      .setBlueprintPeakHours({
        start: this.peakStart(),
        end: this.peakEnd(),
        tz_offset: this.peakTzOffset(),
      })
      .subscribe({
        next: (config) => {
          // Echo the server-confirmed values so a clamped input reflects
          // what was actually persisted.
          this.peakStart.set(config.start);
          this.peakEnd.set(config.end);
          this.peakTzOffset.set(config.tz_offset);
          this.peakHoursSaving.set(false);
          this.snackBar.open('Peak hours saved', 'Close', {
            duration: 3000,
            panelClass: 'success-snackbar',
          });
        },
        error: () => {
          this.peakHoursSaving.set(false);
          this.snackBar.open('Failed to save peak hours', 'Dismiss', {
            duration: 5000,
            panelClass: 'error-snackbar',
          });
        },
      });
  }

  // ──────── R15 snapshot-create toggle (Wave 3) ────────

  /**
   * Read the current ``snapshot_create_enabled`` preference and
   * seed both the working and saved signals. Mirrors the editor
   * preference shape — same "Apply" gate disables when the values
   * match.
   */
  private loadSnapshotCreateEnabled(): void {
    this.settingsService.getSnapshotCreateEnabled().subscribe({
      next: (resp) => {
        const enabled = !!resp?.enabled;
        this.snapshotCreateEnabled.set(enabled);
        this.savedSnapshotCreateEnabled.set(enabled);
      },
      error: () => {
        // Fail-closed default: missing endpoint / unavailable
        // backend → keep the signals at OFF.
        this.snapshotCreateEnabled.set(false);
        this.savedSnapshotCreateEnabled.set(false);
        this.snackBar.open(
          'Failed to load snapshot-create preference — showing defaults',
          'Dismiss',
          { duration: 5000, panelClass: 'error-snackbar' },
        );
      },
    });
  }

  /**
   * Update the in-memory selection when the radio changes. Saving
   * requires clicking Apply so unsaved radio toggles don't fire
   * network requests on every click (parity with the editor
   * preference flow).
   */
  onSnapshotCreateSelectionChange(enabled: boolean): void {
    this.snapshotCreateEnabled.set(enabled);
  }

  /**
   * Persist the working selection to the backend. On success we
   * sync ``savedSnapshotCreateEnabled`` (flipping
   * ``snapshotCreateDirty`` back to false). On failure we keep the
   * saved value unchanged so the radio reflects the last known
   * good state.
   */
  saveSnapshotCreateEnabled(): void {
    const target = this.snapshotCreateEnabled();
    this.savingSnapshotCreate.set(true);
    this.settingsService.setSnapshotCreateEnabled(target).subscribe({
      next: (resp) => {
        const confirmed = !!resp?.enabled;
        this.savedSnapshotCreateEnabled.set(confirmed);
        this.savingSnapshotCreate.set(false);
        this.snackBar.open(
          confirmed
            ? 'Snapshot-create enabled — capturing is now allowed for creators'
            : 'Snapshot-create disabled — captures will be refused until re-enabled',
          'Close',
          { duration: 3000, panelClass: 'success-snackbar' },
        );
      },
      error: () => {
        this.savingSnapshotCreate.set(false);
        this.snackBar.open(
          'Failed to save snapshot-create preference',
          'Dismiss',
          { duration: 5000, panelClass: 'error-snackbar' },
        );
      },
    });
  }

  // ──────── R16 snapshot-usage metrics surface (Wave 3) ────────

  /**
   * Read the aggregated R16 counters for the read-only block.
   * MONITORING ONLY — never feeds the search pipeline. A failure
   * surfaces a toast and keeps the surface null (the template
   * hides the block).
   */
  private loadSnapshotMetrics(): void {
    this.settingsService.getSnapshotUsageMetrics().subscribe({
      next: (resp) => {
        this.snapshotMetrics.set(resp ?? null);
      },
      error: () => {
        this.snapshotMetrics.set(null);
        this.snackBar.open(
          'Failed to load snapshot usage metrics',
          'Dismiss',
          { duration: 5000, panelClass: 'error-snackbar' },
        );
      },
    });
  }

  // ──────── Timezone preference (user-timezone-setting) ────────

  /**
   * Fetch the canonical IANA list from ``GET /api/settings/timezones``
   * (the authoritative source — derived from Python's
   * ``zoneinfo.available_timezones()``). On success the picker
   * renders from this list; on failure we leave ``apiTimezones``
   * ``null`` so the ``timezoneOptions`` computed falls back to
   * ``Intl.supportedValuesOf`` and ultimately to the text-input row.
   *
   * Failure is silent here (no toast) — the picker degrades
   * gracefully to Intl, and a stale Intl list is no worse than the
   * pre-fix experience (the user can still type a custom IANA name
   * in the fallback row). Toast noise on every endpoint hiccup
   * would be more annoying than helpful for a low-stakes pref.
   */
  private loadTimezoneOptionsFromApi(): void {
    this.settingsService.getTimezoneOptions().subscribe({
      next: (resp) => {
        const zones = Array.isArray(resp?.timezones) ? resp.timezones : [];
        this.apiTimezones.set(zones);
      },
      error: () => {
        // Leave apiTimezones as null so the computed falls through to
        // the Intl list / text-input fallback path.
      },
    });
  }

  /**
   * Synchronous cache restore (mirrors language `loadFromStorage`).
   * Only seeds when we actually have a stored zone — `null`
   * storage leaves the signal at the Auto sentinel so the picker
   * opens in the unset state.
   */
  private loadTimezoneFromStorage(): void {
    let saved: string | null = null;
    try {
      saved = localStorage.getItem(TZ_STORAGE_KEY);
    } catch {
      // silently ignore
    }
    if (!saved) {
      return;
    }
    this.applyTimezonePreference(saved);
  }

  /**
   * Fetch the canonical preference from the backend. The cached
   * value is kept on API failure so the user doesn't see the
   * picker flicker; an error toast surfaces the failure but does
   * not blow away the user's previous selection.
   */
  private loadTimezoneFromApi(): void {
    let hadCachedValue = false;
    try {
      hadCachedValue = localStorage.getItem(TZ_STORAGE_KEY) !== null;
    } catch {
      hadCachedValue = false;
    }

    this.settingsService.getTimezonePreference().subscribe({
      next: (pref) => {
        // Accept `null` as the "Auto / not set" verdict — the API
        // contract explicitly says both `timezone` and `utc_offset`
        // are `null` when unset.
        const tz = pref?.timezone ?? null;
        this.applyTimezonePreference(tz);
        if (tz !== null) {
          this.persistTimezoneToStorage(tz);
        } else {
          this.clearTimezoneFromStorage();
        }
      },
      error: () => {
        if (!hadCachedValue) {
          this.selectedTimezone.set(TZ_AUTO_VALUE);
        }
        this.snackBar.open('Failed to load timezone preference', 'Dismiss', {
          duration: 5000,
          panelClass: 'error-snackbar',
        });
      },
    });
  }

  /**
   * Apply a backend/stored value to the view. `null` (or
   * `undefined` from a malformed response) renders as Auto / not
   * set; any other string is treated as the literal IANA name.
   */
  private applyTimezonePreference(tz: string | null | undefined): void {
    if (tz === null || tz === undefined || tz === '') {
      this.selectedTimezone.set(TZ_AUTO_VALUE);
    } else {
      this.selectedTimezone.set(tz);
    }
  }

  /**
   * Picker change handler — receives the option's `value` (a
   * string: the IANA name, or the Auto sentinel). Translates the
   * sentinel to `null` at the API boundary and persists.
   */
  onTimezoneChange(value: string): void {
    this.saveTimezone(value);
  }

  /** Mirror of `onCustomLanguageChange` for the fallback text input. */
  onCustomTimezoneChange(event: Event): void {
    const target = event.target as HTMLInputElement;
    this.customTimezone.set(target.value);
  }

  /**
   * Fallback-mode save (no native IANA list available). The client
   * only enforces a non-empty trim — the server does the actual
   * IANA validation and surfaces 4xx for bad names.
   */
  saveCustomTimezone(): void {
    const tz = this.customTimezone().trim();
    if (!tz) {
      return;
    }
    this.saveTimezone(tz);
  }

  /**
   * Fallback-mode clear: clears the stored timezone preference by
   * sending the API sentinel `null`. Visible only when the native
   * picker is unavailable AND the user has a stored zone to
   * clear.
   */
  clearCustomTimezone(): void {
    this.saveTimezone(TZ_AUTO_VALUE);
  }

  /**
   * Persist the chosen timezone. Mirrors the language handler's
   * optimistic-save + revert-on-failure flow: capture previous
   * state, flip it immediately for snappy UI, then either confirm
   * the server's response or roll back. The signal stores the
   * UI representation (real IANA name or the Auto sentinel);
   * the wire payload uses `null` for the Auto sentinel.
   */
  private saveTimezone(value: string): void {
    const previousSelected = this.selectedTimezone();
    const previousCustom = this.customTimezone();

    const apiValue = value === TZ_AUTO_VALUE ? null : value;

    // Optimistic: flip the visible state before the round-trip so
    // the picker reflects intent immediately. The authoritative
    // value lands in the `next` callback and re-applies via
    // applyTimezonePreference.
    this.selectedTimezone.set(value);
    if (apiValue !== null) {
      this.customTimezone.set(apiValue);
    }
    this.savingTimezone.set(true);

    this.settingsService.setTimezonePreference(apiValue).subscribe({
      next: (resp) => {
        // Re-apply the server-confirmed stored value to the
        // component state. The PUT persists the cleaned string
        // as-is (no server-side canonicalization), so the echo
        // normally matches what was sent — re-applying it keeps
        // the picker in lockstep with the stored record.
        const confirmed = resp?.timezone ?? apiValue;
        this.applyTimezonePreference(confirmed);
        this.savingTimezone.set(false);
        if (confirmed === null) {
          this.clearTimezoneFromStorage();
          this.snackBar.open('Timezone preference cleared', 'Close', {
            duration: 3000,
            panelClass: 'success-snackbar',
          });
        } else {
          this.persistTimezoneToStorage(confirmed);
          this.snackBar.open(
            `Timezone preference set to ${confirmed}`,
            'Close',
            { duration: 3000, panelClass: 'success-snackbar' },
          );
        }
      },
      error: () => {
        // Revert UI to the last known good state.
        this.selectedTimezone.set(previousSelected);
        this.customTimezone.set(previousCustom);
        this.savingTimezone.set(false);
        this.snackBar.open('Failed to save timezone preference', 'Dismiss', {
          duration: 5000,
          panelClass: 'error-snackbar',
        });
      },
    });
  }

  private persistTimezoneToStorage(timezone: string): void {
    try {
      localStorage.setItem(TZ_STORAGE_KEY, timezone);
    } catch {
      // silently ignore
    }
  }

  private clearTimezoneFromStorage(): void {
    try {
      localStorage.removeItem(TZ_STORAGE_KEY);
    } catch {
      // silently ignore
    }
  }
}
