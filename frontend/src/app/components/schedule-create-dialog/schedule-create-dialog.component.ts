import { Component, inject, signal, computed, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ReactiveFormsModule, FormBuilder, FormGroup, Validators, AbstractControl, ValidationErrors } from '@angular/forms';
import { MatDialogRef, MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { ApiService } from '../../services/api.service';
import { SchedulerService, ValidationResponse } from '../../services/scheduler.service';
import { SettingsService } from '../../services/settings.service';
import { Agent } from '../../models';
import { SearchableSelectComponent, SearchableSelectOption } from '../../components';
import {
  formatTimezoneOffset,
  getBrowserNativeTimezones,
} from '../../utils/timezone-format';

// Standalone validator function (defined before class to avoid initialization order issues)
function scheduleValidator(control: AbstractControl): ValidationErrors | null {
  const type = control.get('type')?.value;
  const schedule = control.get('schedule')?.value;
  const intervalSeconds = control.get('interval_seconds')?.value;
  const runAt = control.get('run_at')?.value;
  
  if (type === 'cron' && !schedule) {
    return { scheduleRequired: true };
  }
  if (type === 'interval' && (!intervalSeconds || intervalSeconds < 1)) {
    return { intervalRequired: true };
  }
  if (type === 'one-time' && !runAt) {
    return { runAtRequired: true };
  }
  
  return null;
}

export interface ScheduleCreateDialogData {
  editMode?: boolean;
  scheduleId?: string;
  name?: string;
  agent?: string;
  message?: string;
  project?: string;
  timezone?: string;
  session_mode?: 'new_session' | 'reuse_session';
}

export interface ScheduleCreateDialogResult {
  name: string;
  agent: string;
  message: string;
  project?: string;
  timezone: string;
  schedule_type: 'cron' | 'interval' | 'one-time';
  session_mode: 'new_session' | 'reuse_session';
  schedule?: string;
  interval_seconds?: number;
  run_at?: string;
}

@Component({
  selector: 'app-schedule-create-dialog',
  standalone: true,
  imports: [
    CommonModule,
    ReactiveFormsModule,
    MatDialogModule,
    MatSnackBarModule,
    SearchableSelectComponent
  ],
  templateUrl: './schedule-create-dialog.html',
  styleUrl: './schedule-create-dialog.scss'
})
export class ScheduleCreateDialogComponent implements OnInit {
  private readonly fb = inject(FormBuilder);
  private readonly api = inject(ApiService);
  private readonly schedulerService = inject(SchedulerService);
  private readonly settingsService = inject(SettingsService);
  private readonly snackBar = inject(MatSnackBar);

  protected readonly dialogRef = inject(MatDialogRef<ScheduleCreateDialogComponent>);
  protected readonly data = inject<ScheduleCreateDialogData>(MAT_DIALOG_DATA);

  protected readonly agents = signal<Agent[]>([]);
  protected readonly isLoading = signal(false);
  protected readonly agentsLoading = signal(true);
  protected readonly isValidating = signal(false);
  protected readonly isValid = signal<boolean | null>(null);
  protected readonly validationError = signal<string | null>(null);

  // ── Timezone picker (single source of truth = settings service) ─────
  //
  // Mirrors the Settings page's picker. The canonical IANA list comes
  // from ``GET /api/settings/timezones`` (zoneinfo-backed on the
  // server); ``Intl.supportedValuesOf`` is the secondary fallback when
  // the endpoint is briefly unavailable; both sources empty → the
  // picker hides and a plain text input renders (server validates the
  // IANA name). The dialog has its own default ('UTC') — there is no
  // "Auto / not set" sentinel because the schedule MUST have a zone.
  //
  // Public read-only signals/computed so the template can bind to
  // them directly under Angular's strictTemplates, matching the
  // settings page's surface.
  protected readonly apiTimezones = signal<string[] | null>(null);
  protected readonly customTimezone = signal<string>('');

  /**
   * Resolved option list for the searchable picker. Source priority:
   *   1. server-sourced list (non-empty) — authoritative; same
   *      tzdata the validator reads
   *   2. ``Intl.supportedValuesOf('timeZone')`` (non-empty) —
   *      browser-native fallback when the endpoint is unavailable
   *   3. empty array — both sources gone; the picker hides via
   *      ``isTzNativeSupported()`` and the text-input row renders
   */
  protected readonly timezoneOptions = computed<SearchableSelectOption<string>[]>(() => {
    const api = this.apiTimezones();
    if (api !== null && api.length > 0) {
      return api.map((zone) => ({
        value: zone,
        label: `${zone} (UTC${formatTimezoneOffset(zone)})`,
      }));
    }
    const intlZones = getBrowserNativeTimezones();
    if (intlZones.length > 0) {
      return intlZones.map((zone) => ({
        value: zone,
        label: `${zone} (UTC${formatTimezoneOffset(zone)})`,
      }));
    }
    return [];
  });

  /**
   * Drives the picker-vs-text-input render. Both the server list and
   * the browser-native list must be unavailable (or empty) before
   * the picker hides — a non-empty Intl list keeps the picker
   * usable when the endpoint is briefly broken.
   */
  protected readonly isTzNativeSupported = computed<boolean>(() => {
    const api = this.apiTimezones();
    if (api !== null && api.length > 0) {
      return true;
    }
    return getBrowserNativeTimezones().length > 0;
  });

  protected readonly scheduleTypes = [
    { value: 'cron', label: 'Cron Expression' },
    { value: 'interval', label: 'Interval (seconds)' },
    { value: 'one-time', label: 'One-time' }
  ];

  protected readonly agentOptions = computed(() =>
    this.agents().map((agent) => ({
      value: agent.agent_id,
      label: this.getAgentDisplayName(agent),
    }))
  );

  protected readonly form: FormGroup = this.fb.group({
    name: ['', [Validators.required, Validators.minLength(2)]],
    type: ['cron', Validators.required],
    session_mode: ['new_session'],
    agent: ['', Validators.required],
    message: ['', [Validators.required, Validators.minLength(5)]],
    project: [''],
    timezone: ['UTC'],
    // Cron
    schedule: [''],
    // Interval
    interval_seconds: [60, [Validators.min(1)]],
    // One-time
    run_at: ['']
  }, {
    validators: scheduleValidator
  });

  ngOnInit(): void {
    this.loadAgents();
    this.setupTypeChangeListener();
    this.loadTimezoneOptionsFromApi();

    // Pre-fill form if editing
    if (this.data?.editMode) {
      this.form.patchValue({
        name: this.data.name || '',
        agent: this.data.agent || '',
        message: this.data.message || '',
        project: this.data.project || '',
        timezone: this.data.timezone || 'UTC',
        session_mode: this.data.session_mode || 'new_session'
      });
    }
  }

  /**
   * Fetch the canonical IANA list from ``GET /api/settings/timezones``.
   * On success the picker renders from this list; on failure the
   * signal stays at ``null`` so the computed falls back to
   * ``Intl.supportedValuesOf`` and ultimately the text-input row.
   * The dialog never persists the zone itself — the schedule-create
   * API validates the IANA name at submit time — so a stale list is
   * no worse than the pre-fix experience.
   */
  private loadTimezoneOptionsFromApi(): void {
    this.settingsService.getTimezoneOptions().subscribe({
      next: (resp) => {
        const zones = Array.isArray(resp?.timezones) ? resp.timezones : [];
        this.apiTimezones.set(zones);
      },
      error: () => {
        // Leave apiTimezones at null so the computed falls through to
        // the Intl list / text-input fallback path.
      },
    });
  }

  /**
   * Fallback-mode input handler. Writes the typed IANA name into the
   * form's `timezone` control so submit picks it up — the server
   * validator is the only truth on whether the name resolves.
   */
  protected onCustomTimezoneChange(event: Event): void {
    const target = event.target as HTMLInputElement;
    const value = target.value;
    this.customTimezone.set(value);
    this.form.get('timezone')?.setValue(value);
  }

  private loadAgents(): void {
    this.agentsLoading.set(true);
    
    this.api.listAgents().subscribe({
      next: (response) => {
        this.agents.set(response.agents);
        this.agentsLoading.set(false);
      },
      error: (err) => {
        console.error('Failed to load agents:', err);
        this.snackBar.open('Failed to load agents', 'Close', {
          duration: 5000,
          panelClass: 'error-snackbar'
        });
        this.agentsLoading.set(false);
      }
    });
  }

  private setupTypeChangeListener(): void {
    this.form.get('type')?.valueChanges.subscribe((type) => {
      this.isValid.set(null);
      this.validationError.set(null);
      
      // Clear and update validators based on type
      const scheduleCtrl = this.form.get('schedule');
      const intervalCtrl = this.form.get('interval_seconds');
      const runAtCtrl = this.form.get('run_at');
      const sessionModeCtrl = this.form.get('session_mode');
      
      if (type === 'cron') {
        scheduleCtrl?.setValidators([Validators.required]);
        intervalCtrl?.clearValidators();
        intervalCtrl?.setValue(60);
        runAtCtrl?.clearValidators();
        runAtCtrl?.setValue('');
      } else if (type === 'interval') {
        scheduleCtrl?.clearValidators();
        scheduleCtrl?.setValue('');
        intervalCtrl?.setValidators([Validators.required, Validators.min(1)]);
        runAtCtrl?.clearValidators();
        runAtCtrl?.setValue('');
      } else if (type === 'one-time') {
        scheduleCtrl?.clearValidators();
        scheduleCtrl?.setValue('');
        intervalCtrl?.clearValidators();
        intervalCtrl?.setValue(60);
        runAtCtrl?.setValidators([Validators.required]);
        // One-time schedules always create a new session
        sessionModeCtrl?.setValue('new_session');
      }
      
      scheduleCtrl?.updateValueAndValidity();
      intervalCtrl?.updateValueAndValidity();
      runAtCtrl?.updateValueAndValidity();
    });
  }

  protected get selectedType(): string {
    return this.form.get('type')?.value || 'cron';
  }

  protected get isSessionModeEnabled(): boolean {
    const type = this.form.get('type')?.value;
    // Enable session mode selector only when a schedule type is selected
    return !!type;
  }

  protected get isReuseSessionDisabled(): boolean {
    return this.selectedType === 'one-time';
  }

  protected get showOneTimeSessionHint(): boolean {
    return this.selectedType === 'one-time';
  }

  protected handleClose(): void {
    this.dialogRef.close();
  }

  protected handleValidate(): void {
    const config = this.buildScheduleConfig();
    if (!config) return;
    
    this.isValidating.set(true);
    this.isValid.set(null);
    this.validationError.set(null);
    
    this.schedulerService.validateSchedule(config).subscribe({
      next: (response: ValidationResponse) => {
        this.isValid.set(response.valid);
        if (!response.valid && response.error) {
          this.validationError.set(response.error);
        }
        this.isValidating.set(false);
      },
      error: (err) => {
        console.error('Validation failed:', err);
        this.isValid.set(false);
        this.validationError.set(err.error?.error || 'Validation failed');
        this.isValidating.set(false);
      }
    });
  }

  protected async handleSubmit(): Promise<void> {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }

    this.isLoading.set(true);

    try {
      const result: ScheduleCreateDialogResult = {
        name: this.form.value.name,
        agent: this.form.value.agent,
        message: this.form.value.message,
        project: this.form.value.project || undefined,
        timezone: this.form.value.timezone || 'UTC',
        schedule_type: this.form.value.type,
        session_mode: this.form.value.session_mode
      };

      if (this.form.value.type === 'cron') {
        result.schedule = this.form.value.schedule;
      } else if (this.form.value.type === 'interval') {
        result.interval_seconds = this.form.value.interval_seconds;
      } else if (this.form.value.type === 'one-time') {
        result.run_at = this.form.value.run_at;
      }

      this.dialogRef.close(result);
    } catch (err) {
      console.error('Failed to create schedule:', err);
      this.snackBar.open(
        err instanceof Error ? err.message : 'Failed to create schedule',
        'Close',
        {
          duration: 5000,
          panelClass: 'error-snackbar'
        }
      );
    } finally {
      this.isLoading.set(false);
    }
  }

  private buildScheduleConfig() {
    const type = this.form.value.type;
    
    const config: any = {
      agent: this.form.value.agent,
      message: this.form.value.message,
      session_mode: this.form.value.session_mode
    };

    if (type === 'cron') {
      config.schedule = this.form.value.schedule;
    } else if (type === 'interval') {
      config.interval_seconds = this.form.value.interval_seconds;
    } else if (type === 'one-time') {
      config.run_at = this.form.value.run_at;
    }

    if (this.form.value.timezone) {
      config.timezone = this.form.value.timezone;
    }

    if (this.form.value.project) {
      config.project = this.form.value.project;
    }

    return config;
  }

  protected isSubmitDisabled(): boolean {
    return this.isLoading() || this.form.invalid;
  }

  protected getAgentDisplayName(agent: Agent): string {
    return `${agent.icon} ${agent.name}`;
  }

  protected getIntervalDisplay(seconds: number): string {
    if (seconds < 60) {
      return `${seconds} seconds`;
    } else if (seconds < 3600) {
      const minutes = Math.floor(seconds / 60);
      return `${minutes} minute${minutes > 1 ? 's' : ''}`;
    } else if (seconds < 86400) {
      const hours = Math.floor(seconds / 3600);
      return `${hours} hour${hours > 1 ? 's' : ''}`;
    } else {
      const days = Math.floor(seconds / 86400);
      return `${days} day${days > 1 ? 's' : ''}`;
    }
  }

  protected getCronHint(): string {
    const cron = this.form.get('schedule')?.value;
    if (!cron) return '';
    
    // Simple cron hint generator
    const parts = cron.split(' ');
    if (parts.length !== 5) return 'Invalid cron expression';
    
    const [minute, hour, day, month, dow] = parts;
    
    if (minute === '0' && hour === '*' && day === '*' && month === '*' && dow === '*') {
      return 'Every hour at minute 0';
    }
    if (minute === '*' && hour === '*' && day === '*' && month === '*' && dow === '*') {
      return 'Every minute';
    }
    if (minute === '0' && hour === '0' && day === '*' && month === '*' && dow === '*') {
      return 'Every day at midnight';
    }
    if (minute === '0' && hour === '9' && day === '*' && month === '*' && dow === '1-5') {
      return 'Weekdays at 9:00 AM';
    }
    
    return '';
  }
}
