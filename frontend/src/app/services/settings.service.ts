import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import type { VSCodeStatus } from '../models';

export interface LanguagePreference {
  language: string;
}

export interface BlueprintPeakHours {
  start: number;
  end: number;
  tz_offset: number;
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
}
