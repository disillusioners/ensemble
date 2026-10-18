import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import type { VSCodeStatus } from '../models';

export interface LanguagePreference {
  language: string;
}

/**
 * Mirror of the user timezone API contract:
 *   GET /api/settings/timezone → { timezone, utc_offset }
 *   PUT /api/settings/timezone body { timezone } → same response shape
 *
 * Both fields are `null` when the preference is unset. Sending
 * `timezone: null` (or "") clears the setting server-side.
 */
export interface TimezonePreference {
  timezone: string | null;
  utc_offset: string | null;
}

/**
 * Mirror of the GET /api/settings/timezones contract — the canonical
 * IANA picker list. The backend derives the list from
 * ``zoneinfo.available_timezones()`` so the picker and the validator
 * (``/api/settings/timezone`` PUT) share a single source of truth and
 * cannot drift apart (older browsers' ``Intl.supportedValuesOf`` ships
 * deprecated aliases like ``Asia/Saigon`` but lacks canonical
 * ``Asia/Ho_Chi_Minh`` — exactly the drift the spec fixes).
 */
export interface TimezoneOptions {
  timezones: string[];
}

export interface BlueprintPeakHours {
  start: number;
  end: number;
  tz_offset: number;
}

export interface SnapshotCreatePreference {
  enabled: boolean;
}

export interface SnapshotUsageMetrics {
  capture_counts: Record<string, { created: number }>;
  spawn_counts_per_snapshot: Array<{ snapshot_id: string; count: number }>;
}

/**
 * Winning-tier discriminator for daemon runtime settings — verbatim
 * mirror of the P2-frozen contract's ``source`` Literal
 * (``daemon/routers/schemas.py:DaemonSettingEntry.source``):
 * live DB row > live env > boot-time yaml literal > registry default.
 */
export type DaemonSettingSource = 'db' | 'env' | 'yaml' | 'default';

/**
 * Mirror of the P2-FROZEN ``DaemonSettingEntry`` contract
 * (``daemon/routers/schemas.py``, db-backed-settings P2 t1). Field
 * names/types copied verbatim — changes to the pydantic model require
 * updating this interface in the same change.
 *
 * ``value`` is the BOOT-APPLIED running truth (what the daemon
 * actually constructed at startup, e.g. the LLM semaphore bound at
 * boot) — deliberately NOT a fresh resolve. ``source`` is the LIVE
 * winning tier; when an override is written (or cleared) after boot,
 * ``value`` lags ``source`` — that divergence IS the honest
 * restart-required reporting and the FE renders both fields
 * separately, never collapsed into one number.
 *
 * ``env_value`` / ``yaml_value`` / ``db_override`` are OMITTED (not
 * nulled) when the tier is absent — and omitted entirely for
 * ``sensitive: true`` keys (router masking,
 * ``response_model_exclude_none=True``) — hence optional fields.
 *
 * PUT/DELETE return this model BARE (frozen write shape, adopted
 * note 4) — never wrapped in ``DaemonSettingsResponse``.
 */
export interface DaemonSettingEntry {
  key: string;
  value: number;
  source: DaemonSettingSource;
  default: number;
  env_value?: number;
  yaml_value?: number;
  db_override?: number;
  requires_restart: boolean;
  sensitive: boolean;
  description: string;
}

/**
 * Mirror of the P2-frozen ``DaemonSettingsResponse`` — the GET
 * ``/api/settings/daemon`` payload. ``restart_required`` is DERIVED
 * (any key carries a live ``db_override``); it exists for API
 * consumers/tooling — the FE keys off the PER-KEY ``requires_restart``
 * field (adopted ruling O3).
 */
export interface DaemonSettingsResponse {
  settings: DaemonSettingEntry[];
  restart_required: boolean;
}

/**
 * Mirror of the P2-frozen ``DaemonSettingUpdate`` — PUT
 * ``/api/settings/daemon/{key}`` request body. ``value`` is typed per
 * the managed key at v1 (int; registry constraint e.g. ge=1).
 */
export interface DaemonSettingUpdate {
  value: number;
}

@Injectable({ providedIn: 'root' })
export class SettingsService {
  private readonly http = inject(HttpClient);
  private readonly API_BASE = '/api/settings/language';

  /**
   * GET /api/settings/language
   */
  getLanguagePreference(): Observable<LanguagePreference> {
    return this.http.get<LanguagePreference>(this.API_BASE);
  }

  /**
   * PUT /api/settings/language
   */
  setLanguagePreference(language: string): Observable<LanguagePreference> {
    return this.http.put<LanguagePreference>(this.API_BASE, { language });
  }

  /**
   * GET /api/settings/timezone
   * Returns the user's IANA timezone preference plus a convenience
   * UTC offset echo (e.g. `+07:00`). Both fields are `null` when
   * the preference is unset.
   */
  getTimezonePreference(): Observable<TimezonePreference> {
    return this.http.get<TimezonePreference>('/api/settings/timezone');
  }

  /**
   * GET /api/settings/timezones
   * Returns the canonical IANA zone list for the Settings picker.
   * Backend derives from ``zoneinfo.available_timezones()`` so the
   * picker and the validator share a single source of truth — the
   * browser-native ``Intl.supportedValuesOf('timeZone')`` list drifts
   * (older ICU returns deprecated aliases like ``Asia/Saigon`` but
   * not canonical ``Asia/Ho_Chi_Minh``), so the UI no longer relies
   * on it. The list is sorted ASCII for stable render order.
   */
  getTimezoneOptions(): Observable<TimezoneOptions> {
    return this.http.get<TimezoneOptions>('/api/settings/timezones');
  }

  /**
   * PUT /api/settings/timezone
   * Persists the IANA name. `null` (or empty string) clears the
   * preference; the server validates the IANA name and returns
   * 4xx for invalid input. The response echoes the stored value
   * alongside the `utc_offset`.
   */
  setTimezonePreference(timezone: string | null): Observable<TimezonePreference> {
    return this.http.put<TimezonePreference>('/api/settings/timezone', { timezone });
  }

  /**
   * GET /api/settings/editor
   */
  getEditorPreference(): Observable<{ editor: string }> {
    return this.http.get<{ editor: string }>('/api/settings/editor');
  }

  /**
   * PUT /api/settings/editor
   */
  setEditorPreference(editor: string): Observable<{ editor: string }> {
    return this.http.put<{ editor: string }>('/api/settings/editor', { editor });
  }

  /**
   * GET /api/settings/editor/status
   */
  getVscodeStatus(): Observable<VSCodeStatus> {
    return this.http.get<VSCodeStatus>('/api/settings/editor/status');
  }

  /**
   * POST /api/settings/vscode/start
   */
  startVscodeServer(): Observable<any> {
    return this.http.post('/api/settings/vscode/start', {});
  }

  /**
   * POST /api/settings/vscode/stop
   */
  stopVscodeServer(): Observable<any> {
    return this.http.post('/api/settings/vscode/stop', {});
  }

  /**
   * GET /api/settings/blueprint-peak-hours
   */
  getBlueprintPeakHours(): Observable<BlueprintPeakHours> {
    return this.http.get<BlueprintPeakHours>('/api/settings/blueprint-peak-hours');
  }

  /**
   * PUT /api/settings/blueprint-peak-hours
   */
  setBlueprintPeakHours(config: BlueprintPeakHours): Observable<BlueprintPeakHours> {
    return this.http.put<BlueprintPeakHours>('/api/settings/blueprint-peak-hours', config);
  }

  /**
   * GET /api/settings/snapshot-create
   * Returns the R15 settings toggle (snapshot_create enabled / disabled).
   */
  getSnapshotCreateEnabled(): Observable<SnapshotCreatePreference> {
    return this.http.get<SnapshotCreatePreference>('/api/settings/snapshot-create');
  }

  /**
   * PUT /api/settings/snapshot-create
   * Persists the R15 settings toggle. Default OFF (opt-in rollout);
   * snapshot_create tool calls refuse when disabled.
   */
  setSnapshotCreateEnabled(enabled: boolean): Observable<SnapshotCreatePreference> {
    return this.http.put<SnapshotCreatePreference>(
      '/api/settings/snapshot-create',
      { enabled },
    );
  }

  /**
   * GET /api/settings/snapshot-usage-metrics
   * Returns the aggregated R16 counters for ops visibility. MONITORING
   * ONLY — never feeds the search pipeline (R10 forbids usage-ranking).
   */
  getSnapshotUsageMetrics(): Observable<SnapshotUsageMetrics> {
    return this.http.get<SnapshotUsageMetrics>('/api/settings/snapshot-usage-metrics');
  }

  /**
   * GET /api/settings/daemon
   * Effective daemon runtime settings with per-key source-tier
   * reporting (db-backed-settings P2 frozen contract). ``value`` is
   * the boot-applied running truth; ``source`` is the LIVE winning
   * tier — divergence between the two is honest restart-required
   * reporting the FE renders as separate fields.
   */
  getDaemonSettings(): Observable<DaemonSettingsResponse> {
    return this.http.get<DaemonSettingsResponse>('/api/settings/daemon');
  }

  /**
   * PUT /api/settings/daemon/{key}
   * Persists a DB override for a managed key. Body is the frozen
   * ``DaemonSettingUpdate`` ({ value }); the response is the BARE
   * refreshed ``DaemonSettingEntry`` (frozen write shape — no
   * wrapper, no top-level restart flag). 404 unknown key; 422 on
   * registry-validation failure (non-int, out of range).
   */
  putDaemonSetting(key: string, value: number): Observable<DaemonSettingEntry> {
    const body: DaemonSettingUpdate = { value };
    return this.http.put<DaemonSettingEntry>(
      `/api/settings/daemon/${encodeURIComponent(key)}`,
      body,
    );
  }

  /**
   * DELETE /api/settings/daemon/{key}
   * Clears the DB override (falls back down the ladder: env > yaml >
   * default on next boot / live re-resolve). Response is the BARE
   * refreshed ``DaemonSettingEntry``; idempotent on a missing row.
   */
  deleteDaemonSetting(key: string): Observable<DaemonSettingEntry> {
    return this.http.delete<DaemonSettingEntry>(
      `/api/settings/daemon/${encodeURIComponent(key)}`,
    );
  }
}
