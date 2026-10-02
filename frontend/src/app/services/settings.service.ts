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
}
