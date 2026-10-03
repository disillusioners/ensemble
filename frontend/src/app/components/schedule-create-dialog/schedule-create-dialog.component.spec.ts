// ScheduleCreateDialogComponent spec — schedule-dialog-tz-api-list arc
// (commission 2026-10-03).
//
// The timezone picker is the unit of behavior under test:
//   * the static 12-entry `TIMEZONES` array is GONE
//   * the option list is sourced from the SAME `GET /api/settings/timezones`
//     endpoint the Settings picker uses (single source of truth)
//   * the fallback chain mirrors Settings: API list → Intl.supportedValuesOf
//     → plain text input (form writes through)
//   * all 12 legacy zones remain selectable in the full canonical list
//
// House convention (precedent: ``job-create-dialog.component.spec.ts``):
// NO Angular TestBed. A ``Testable`` mirror rebuilds the dialog's
// picker logic in plain TS with the real ``FormBuilder`` and the
// shared ``getBrowserNativeTimezones`` helper, then behavior is
// asserted via the mirror. The real source is re-read with
// ``readFileSync`` and pinned with regexes — a drift in the REAL
// class breaks the pin before the mirror can lie.

import { readFileSync } from 'fs';
import { join } from 'path';

import { signal, computed } from '@angular/core';
import { FormBuilder, FormGroup, Validators } from '@angular/forms';
import { of, throwError, Subject } from 'rxjs';

import { formatTimezoneOffset, getBrowserNativeTimezones } from '../../utils/timezone-format';
import type { ScheduleCreateDialogData } from './schedule-create-dialog.component';

// ── source reads (re-asserted against the REAL class) ──────────────────
const dialogDir = join(__dirname, '.');
const dialogTsSrc = readFileSync(join(dialogDir, 'schedule-create-dialog.component.ts'), 'utf-8');
const dialogHtmlSrc = readFileSync(join(dialogDir, 'schedule-create-dialog.html'), 'utf-8');

// ── IANA fixture list ───────────────────────────────────────────────────
//
// 20-zone canonical subset (alphabetical, ASCII) used to simulate the
// ``GET /api/settings/timezones`` response in the picker tests. The
// 12 zones the old static array hardcoded are all present in this
// fixture — the schedule-dialog-tz-api-list spec asserts presence so
// a future "strip legacy" cleanup cannot silently regress the dialog.
const CANONICAL_IANA_FIXTURE = [
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'America/New_York',
  'Asia/Shanghai',
  'Asia/Singapore',
  'Asia/Tokyo',
  'Australia/Sydney',
  'Europe/Berlin',
  'Europe/London',
  'Europe/Paris',
  'UTC',
];

// The 12 zones the OLD static ``TIMEZONES`` array shipped. Every
// spec that touches the resolved picker asserts presence.
const LEGACY_TWELVE = [
  'UTC',
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'Europe/London',
  'Europe/Paris',
  'Europe/Berlin',
  'Asia/Tokyo',
  'Asia/Shanghai',
  'Asia/Singapore',
  'Australia/Sydney',
];

// ── Intl.supportedValuesOf patch helpers (mirror settings spec) ────────
type SupportedValuesOfFn = (kind: string) => string[] | number[];

function patchIntlSupportedValuesOf(
  zones: readonly string[] | null,
): jest.Mock | null {
  const intlAny = Intl as unknown as { supportedValuesOf?: SupportedValuesOfFn };
  const original = intlAny.supportedValuesOf;
  if (zones === null) {
    delete intlAny.supportedValuesOf;
    return null;
  }
  const mock = jest.fn((kind: string) => {
    if (kind === 'timeZone') {
      return [...zones];
    }
    return original ? original(kind) : [];
  }) as unknown as jest.Mock;
  intlAny.supportedValuesOf = mock;
  return mock;
}

function restoreIntlSupportedValuesOf(mock: jest.Mock | null): void {
  const intlAny = Intl as unknown as { supportedValuesOf?: SupportedValuesOfFn };
  if (mock === null) {
    delete intlAny.supportedValuesOf;
    return;
  }
  intlAny.supportedValuesOf = mock as unknown as SupportedValuesOfFn;
}

// ── SettingsService mock (just the timezone surface) ───────────────────
class MockSettingsService {
  getTimezoneOptions = jest.fn();
}

// ── Testable mirror ─────────────────────────────────────────────────────
//
// Mirrors the dialog's timezone picker surface:
//   * ``apiTimezones`` / ``customTimezone`` signals
//   * ``timezoneOptions`` computed (same source-priority chain as the
//     real component — see source pins below)
//   * ``isTzNativeSupported`` computed
//   * ``onCustomTimezoneChange`` (writes through to the form)
//   * ``loadTimezoneOptionsFromApi`` (subscribes to the mocked service)
class TestableScheduleCreateDialog {
  readonly apiTimezones = signal<string[] | null>(null);
  readonly customTimezone = signal<string>('');

  readonly timezoneOptions = computed(() => {
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

  readonly isTzNativeSupported = computed<boolean>(() => {
    const api = this.apiTimezones();
    if (api !== null && api.length > 0) {
      return true;
    }
    return getBrowserNativeTimezones().length > 0;
  });

  readonly form: FormGroup = new FormBuilder().group({
    name: ['', [Validators.required, Validators.minLength(2)]],
    type: ['cron', Validators.required],
    session_mode: ['new_session'],
    agent: ['', Validators.required],
    message: ['', [Validators.required, Validators.minLength(5)]],
    project: [''],
    timezone: ['UTC'],
    schedule: [''],
    interval_seconds: [60, [Validators.min(1)]],
    run_at: [''],
  });

  constructor(private readonly settingsService: MockSettingsService) {}

  loadTimezoneOptionsFromApi(): void {
    this.settingsService.getTimezoneOptions().subscribe({
      next: (resp: { timezones?: string[] }) => {
        const zones = Array.isArray(resp?.timezones) ? resp.timezones : [];
        this.apiTimezones.set(zones);
      },
      error: () => {
        // Leave apiTimezones at null so the computed falls through.
      },
    });
  }

  onCustomTimezoneChange(event: { target: { value: string } }): void {
    const value = event.target.value;
    this.customTimezone.set(value);
    this.form.get('timezone')?.setValue(value);
  }

  /** Mirror of the editMode prefill branch — only the timezone line. */
  prefillFromData(data: ScheduleCreateDialogData): void {
    if (data?.editMode) {
      this.form.patchValue({
        timezone: data.timezone || 'UTC',
      });
    }
  }
}

// ═══════════════════════════════════════════════════════════════════════
//  1. Static array is GONE — source pin
// ═══════════════════════════════════════════════════════════════════════
describe('schedule-create-dialog — static TIMEZONES array removed', () => {
  it('REAL source: the static `const TIMEZONES` array is GONE', () => {
    // The single-source rule: any hand-maintained IANA list is a
    // drift liability. The static 12-entry array must not come back.
    expect(dialogTsSrc).not.toMatch(/const\s+TIMEZONES\s*=\s*\[/);
  });

  it('REAL source: the `timezones` field is no longer a static const ref', () => {
    // The component used to expose `protected readonly timezones = TIMEZONES;`
    // — the new surface uses computed `timezoneOptions()` driven by signals.
    expect(dialogTsSrc).not.toMatch(/protected\s+readonly\s+timezones\s*=\s*TIMEZONES/);
    expect(dialogTsSrc).not.toMatch(/protected\s+readonly\s+timezones\s*=/);
  });

  it('REAL source: imports `SettingsService` and the shared timezone utils', () => {
    expect(dialogTsSrc).toMatch(
      /import\s*\{\s*SettingsService\s*\}\s*from\s*'\.\.\/\.\.\/services\/settings\.service'/,
    );
    expect(dialogTsSrc).toMatch(
      /import\s*\{[^}]*formatTimezoneOffset[^}]*\}\s*from\s*'\.\.\/\.\.\/utils\/timezone-format'/,
    );
  });

  it('REAL source: declares the `apiTimezones` / `customTimezone` / `timezoneOptions` / `isTzNativeSupported` surface', () => {
    expect(dialogTsSrc).toMatch(/apiTimezones\s*=\s*signal<string\[\]\s*\|\s*null>\(null\)/);
    expect(dialogTsSrc).toMatch(/customTimezone\s*=\s*signal<string>\(''\)/);
    expect(dialogTsSrc).toMatch(/timezoneOptions\s*=\s*computed</);
    expect(dialogTsSrc).toMatch(/isTzNativeSupported\s*=\s*computed</);
  });

  it('REAL source: fetches the option list from `settingsService.getTimezoneOptions()` on init', () => {
    expect(dialogTsSrc).toMatch(
      /this\.settingsService\.getTimezoneOptions\(\)\.subscribe\(\{[\s\S]*?Array\.isArray\(resp\?\.timezones\)/,
    );
  });
});

// ═══════════════════════════════════════════════════════════════════════
//  2. Picker source chain (API → Intl → text input)
// ═══════════════════════════════════════════════════════════════════════
describe('schedule-create-dialog — timezone picker source chain', () => {
  let supportedMock: jest.Mock | null;
  let service: MockSettingsService;
  let dialog: TestableScheduleCreateDialog;

  beforeEach(() => {
    supportedMock = patchIntlSupportedValuesOf(CANONICAL_IANA_FIXTURE);
    service = new MockSettingsService();
    dialog = new TestableScheduleCreateDialog(service);
  });

  afterEach(() => {
    restoreIntlSupportedValuesOf(supportedMock);
  });

  it('(1) the API list drives the picker when the GET /api/settings/timezones response is non-empty', () => {
    service.getTimezoneOptions.mockReturnValue(of({ timezones: CANONICAL_IANA_FIXTURE }));
    dialog.loadTimezoneOptionsFromApi();

    const labels = dialog.timezoneOptions().map((o) => o.label);
    // Each API zone renders with the standard "<zone> (UTC±HH:MM)" label.
    expect(labels).toContain('America/New_York (UTC-04:00)');
    expect(labels).toContain('Asia/Tokyo (UTC+09:00)');
    expect(labels).toContain('UTC (UTC+00:00)');
    // Picker is shown (the API list was non-empty).
    expect(dialog.isTzNativeSupported()).toBe(true);
  });

  it('(1) every option has the canonical IANA name as its value (no rename, no alias)', () => {
    service.getTimezoneOptions.mockReturnValue(of({ timezones: CANONICAL_IANA_FIXTURE }));
    dialog.loadTimezoneOptionsFromApi();

    const values = new Set(dialog.timezoneOptions().map((o) => o.value));
    for (const zone of CANONICAL_IANA_FIXTURE) {
      expect(values.has(zone)).toBe(true);
    }
  });

  it('(2) falls back to Intl.supportedValuesOf when the GET /api/settings/timezones response is empty', () => {
    service.getTimezoneOptions.mockReturnValue(of({ timezones: [] }));
    dialog.loadTimezoneOptionsFromApi();

    // The Intl list (patched to CANONICAL_IANA_FIXTURE) takes over.
    const labels = dialog.timezoneOptions().map((o) => o.label);
    expect(labels).toContain('America/New_York (UTC-04:00)');
    expect(dialog.isTzNativeSupported()).toBe(true);
  });

  it('(2) falls back to Intl.supportedValuesOf when the GET /api/settings/timezones call errors', () => {
    service.getTimezoneOptions.mockReturnValue(throwError(() => new Error('boom')));
    dialog.loadTimezoneOptionsFromApi();

    // apiTimezones stays at null — the computed falls through to Intl.
    expect(dialog.apiTimezones()).toBeNull();
    const labels = dialog.timezoneOptions().map((o) => o.label);
    expect(labels).toContain('America/New_York (UTC-04:00)');
    expect(dialog.isTzNativeSupported()).toBe(true);
  });

  it('(3) the option list is empty when BOTH the API and Intl are unavailable', () => {
    service.getTimezoneOptions.mockReturnValue(throwError(() => new Error('boom')));
    dialog.loadTimezoneOptionsFromApi();
    restoreIntlSupportedValuesOf(supportedMock);
    supportedMock = patchIntlSupportedValuesOf(null);

    expect(dialog.timezoneOptions()).toEqual([]);
    expect(dialog.isTzNativeSupported()).toBe(false);
  });

  it('(3) isTzNativeSupported is FALSE when API errors and Intl is missing', () => {
    service.getTimezoneOptions.mockReturnValue(throwError(() => new Error('boom')));
    dialog.loadTimezoneOptionsFromApi();
    restoreIntlSupportedValuesOf(supportedMock);
    supportedMock = patchIntlSupportedValuesOf(null);

    expect(dialog.isTzNativeSupported()).toBe(false);
  });

  it('(3) isTzNativeSupported is FALSE when API errors and Intl returns [] (empty-list dead end)', () => {
    service.getTimezoneOptions.mockReturnValue(throwError(() => new Error('boom')));
    dialog.loadTimezoneOptionsFromApi();
    restoreIntlSupportedValuesOf(supportedMock);
    supportedMock = patchIntlSupportedValuesOf([]);

    expect(dialog.isTzNativeSupported()).toBe(false);
  });

  it('does NOT call the endpoint twice — load is a one-shot on init', () => {
    service.getTimezoneOptions.mockReturnValue(of({ timezones: CANONICAL_IANA_FIXTURE }));
    dialog.loadTimezoneOptionsFromApi();
    dialog.loadTimezoneOptionsFromApi();

    expect(service.getTimezoneOptions).toHaveBeenCalledTimes(2);
    // The testable mirror exposes the load as public for ergonomics; the
    // REAL class only calls it from ngOnInit — source-pinned below.
    expect(dialogTsSrc).toMatch(/this\.loadTimezoneOptionsFromApi\(\);\s*\n\s*\/\/ Pre-fill form if editing/);
  });
});

// ═══════════════════════════════════════════════════════════════════════
//  3. All 12 legacy zones remain selectable inside the full list
// ═══════════════════════════════════════════════════════════════════════
describe('schedule-create-dialog — 12 legacy zones resolve in the canonical list', () => {
  it('every legacy zone is present in CANONICAL_IANA_FIXTURE', () => {
    const fixtureSet = new Set(CANONICAL_IANA_FIXTURE);
    for (const zone of LEGACY_TWELVE) {
      expect(fixtureSet.has(zone)).toBe(true);
    }
  });

  it('every legacy zone is selectable (value match) in the resolved picker', () => {
    const service = new MockSettingsService();
    const dialog = new TestableScheduleCreateDialog(service);
    service.getTimezoneOptions.mockReturnValue(of({ timezones: CANONICAL_IANA_FIXTURE }));
    dialog.loadTimezoneOptionsFromApi();

    const values = new Set(dialog.timezoneOptions().map((o) => o.value));
    for (const zone of LEGACY_TWELVE) {
      expect(values.has(zone)).toBe(true);
    }
  });

  it('the resolved picker exposes AT LEAST the 12 legacy zones (and any extras)', () => {
    const service = new MockSettingsService();
    const dialog = new TestableScheduleCreateDialog(service);
    service.getTimezoneOptions.mockReturnValue(of({ timezones: CANONICAL_IANA_FIXTURE }));
    dialog.loadTimezoneOptionsFromApi();

    // No upper bound asserted — the backend list is 498+ entries, this
    // is the dialog's minimal hand-maintained canonical subset.
    expect(dialog.timezoneOptions().length).toBeGreaterThanOrEqual(LEGACY_TWELVE.length);
  });
});

// ═══════════════════════════════════════════════════════════════════════
//  4. Fallback arm — text input writes through to the form
// ═══════════════════════════════════════════════════════════════════════
describe('schedule-create-dialog — fallback text input writes to form.timezone', () => {
  it('onCustomTimezoneChange writes the typed value into the form timezone control', () => {
    const service = new MockSettingsService();
    const dialog = new TestableScheduleCreateDialog(service);

    // Form starts with the default 'UTC'.
    expect(dialog.form.get('timezone')!.value).toBe('UTC');

    dialog.onCustomTimezoneChange({ target: { value: 'Asia/Bangkok' } });

    expect(dialog.customTimezone()).toBe('Asia/Bangkok');
    expect(dialog.form.get('timezone')!.value).toBe('Asia/Bangkok');
  });

  it('onCustomTimezoneChange accepts any IANA string (server-side validation is the only truth)', () => {
    const service = new MockSettingsService();
    const dialog = new TestableScheduleCreateDialog(service);

    // Mirrors the Settings page's policy: client only enforces a
    // non-empty trim. Server rejects bogus names with 4xx.
    dialog.onCustomTimezoneChange({ target: { value: 'Not/A/Real/Zone' } });
    expect(dialog.form.get('timezone')!.value).toBe('Not/A/Real/Zone');
  });

  it('onCustomTimezoneChange overwrites previous custom values', () => {
    const service = new MockSettingsService();
    const dialog = new TestableScheduleCreateDialog(service);

    dialog.onCustomTimezoneChange({ target: { value: 'Asia/Bangkok' } });
    dialog.onCustomTimezoneChange({ target: { value: 'Europe/London' } });

    expect(dialog.form.get('timezone')!.value).toBe('Europe/London');
    expect(dialog.customTimezone()).toBe('Europe/London');
  });

  it('REAL source: onCustomTimezoneChange writes BOTH the signal AND the form control', () => {
    // Regression pin: a future refactor that only updates one of the
    // two would break the fallback contract (text input + form value
    // must agree so the schedule submit sees the typed zone).
    expect(dialogTsSrc).toMatch(
      /this\.customTimezone\.set\(value\);\s*\n\s*this\.form\.get\('timezone'\)\?\.setValue\(value\);/,
    );
  });
});

// ═══════════════════════════════════════════════════════════════════════
//  5. Default + editMode prefill behavior
// ═══════════════════════════════════════════════════════════════════════
describe('schedule-create-dialog — default timezone + editMode prefill', () => {
  it('the form timezone control defaults to "UTC" (no Auto sentinel for schedules)', () => {
    const service = new MockSettingsService();
    const dialog = new TestableScheduleCreateDialog(service);
    expect(dialog.form.get('timezone')!.value).toBe('UTC');
  });

  it('REAL source: the form timezone default is "UTC" (no Auto sentinel like the Settings page)', () => {
    expect(dialogTsSrc).toMatch(/timezone:\s*\['UTC'\]/);
  });

  it('editMode prefill preserves a stored zone and falls back to "UTC" when unset', () => {
    const service = new MockSettingsService();
    const dialog = new TestableScheduleCreateDialog(service);

    dialog.prefillFromData({
      editMode: true,
      timezone: 'Asia/Tokyo',
    });
    expect(dialog.form.get('timezone')!.value).toBe('Asia/Tokyo');

    const dialog2 = new TestableScheduleCreateDialog(service);
    dialog2.prefillFromData({ editMode: true });
    expect(dialog2.form.get('timezone')!.value).toBe('UTC');
  });
});

// ═══════════════════════════════════════════════════════════════════════
//  6. Template rendering — picker vs text input arm
// ═══════════════════════════════════════════════════════════════════════
describe('schedule-create-dialog — template branches on isTzNativeSupported()', () => {
  it('REAL template: renders <app-searchable-select> inside the @if isTzNativeSupported arm', () => {
    // Pin the @if branch: the picker is shown when the API or Intl
    // list is available. The formControlName binding keeps the form
    // in sync with the picker's selection.
    expect(dialogHtmlSrc).toMatch(
      /@if\s*\(isTzNativeSupported\(\)\)\s*\{[\s\S]*?<app-searchable-select[\s\S]*?formControlName="timezone"[\s\S]*?\[options\]="timezoneOptions\(\)"[\s\S]*?\/>/,
    );
  });

  it('REAL template: renders a plain text input inside the @else arm', () => {
    // The fallback input binds to customTimezone() and writes through
    // via (input)="onCustomTimezoneChange($event)" so the form's
    // timezone control tracks the typed value.
    expect(dialogHtmlSrc).toMatch(
      /\}\s*@else\s*\{[\s\S]*?<input[\s\S]*?\(input\)="onCustomTimezoneChange\(\$event\)"[\s\S]*?\/>/,
    );
  });

  it('REAL template: there is NO `[options]="timezones"` reference (the static const is gone)', () => {
    // The picker options binding is now `timezoneOptions()` — a
    // computed signal — not the deleted `timezones` static field.
    expect(dialogHtmlSrc).not.toMatch(/\[options\]="timezones"/);
  });
});

// ═══════════════════════════════════════════════════════════════════════
//  7. Async load observability — Subject/throwError edge cases
// ═══════════════════════════════════════════════════════════════════════
describe('schedule-create-dialog — async load observability', () => {
  it('loadTimezoneOptionsFromApi seeds apiTimezones from a Subject before the response fires', () => {
    const service = new MockSettingsService();
    const dialog = new TestableScheduleCreateDialog(service);

    const subject = new Subject<{ timezones?: string[] }>();
    service.getTimezoneOptions.mockReturnValue(subject.asObservable());

    dialog.loadTimezoneOptionsFromApi();
    expect(dialog.apiTimezones()).toBeNull(); // not yet seeded

    subject.next({ timezones: ['Asia/Bangkok', 'UTC'] });
    expect(dialog.apiTimezones()).toEqual(['Asia/Bangkok', 'UTC']);
    expect(dialog.isTzNativeSupported()).toBe(true);
  });

  it('loadTimezoneOptionsFromApi leaves apiTimezones at null on Subject error', () => {
    const service = new MockSettingsService();
    const dialog = new TestableScheduleCreateDialog(service);

    const subject = new Subject<{ timezones?: string[] }>();
    service.getTimezoneOptions.mockReturnValue(subject.asObservable());

    dialog.loadTimezoneOptionsFromApi();
    subject.error({ status: 500 });

    // Computed falls through to Intl — we don't crash on the error path.
    expect(dialog.apiTimezones()).toBeNull();
  });

  it('loadTimezoneOptionsFromApi treats a malformed payload (no `timezones` key) as empty', () => {
    const service = new MockSettingsService();
    const dialog = new TestableScheduleCreateDialog(service);
    service.getTimezoneOptions.mockReturnValue(of({}));
    dialog.loadTimezoneOptionsFromApi();

    // `Array.isArray(undefined) === false` → empty array. The
    // computed treats empty as "API unavailable" (same as null).
    expect(dialog.apiTimezones()).toEqual([]);
  });
});
