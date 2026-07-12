import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export interface LanguagePreference {
  language: string;
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
}
