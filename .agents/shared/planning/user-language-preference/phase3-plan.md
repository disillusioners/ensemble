# Phase 3: Frontend UI for Language Setting

## Objective
Add a language selector dropdown to a new Settings page in the Angular frontend. The dropdown loads the current preference from `GET /api/settings/language` and saves changes via `PUT /api/settings/language`. A nav menu item in the settings gear menu provides access.

## Coupling
- **Depends on**: Phase 1 (needs the API endpoints `GET/PUT /api/settings/language`)
- **Coupling type**: loose — depends only on the API contract (`{"language": "string"}`), completely separate codebase (Angular vs Python)
- **Shared files with other phases**: None (frontend is independent of Python code)
- **Shared APIs/interfaces**: `GET /api/settings/language` → `{"language": "Spanish"}`, `PUT /api/settings/language` ← `{"language": "Spanish"}`
- **Why this coupling**: Frontend only needs the API contract. Can be developed in parallel with Phase 2 once Phase 1 API is defined.

## Context
- Angular standalone components with lazy-loaded routes (`frontend/src/app/app.routes.ts`)
- Settings gear menu in `frontend/src/app/app.ts:52` — `settingsMenuItems` signal array
- Services pattern: `frontend/src/app/services/api.service.ts` for HTTP calls
- Material Design components (MatMenuModule, MatIconModule, MatButtonModule already imported)
- No existing settings page — this is the first one

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create settings service | New `frontend/src/app/services/settings.service.ts` with `getLanguagePreference()` and `setLanguagePreference(language: string)` methods. Uses Angular `HttpClient` to call `/api/settings/language` | `frontend/src/app/services/settings.service.ts` (NEW) |
| 2 | Create settings page component | New `frontend/src/app/pages/settings/settings.component.ts` + `.html` + `.scss`. Contains a `<mat-select>` dropdown with predefined language options + "Custom" text input. Loads current value on init, saves on change | `frontend/src/app/pages/settings/settings.component.ts` (NEW), `.html` (NEW), `.scss` (NEW) |
| 3 | Add route | Add `{ path: 'settings', loadComponent: () => import('./pages/settings/settings.component').then(m => m.SettingsComponent) }` to routes | `frontend/src/app/app.routes.ts` (MODIFY) |
| 4 | Add nav menu item | Add `{ label: 'Settings', icon: 'language', route: '/settings' }` to `settingsMenuItems` signal in `app.ts` | `frontend/src/app/app.ts` (MODIFY) |
| 5 | Add language options | Predefined list: English, Spanish, Chinese, French, German, Japanese, Korean, Portuguese, Russian, Arabic, Vietnamese + "Other (type below)" option | `frontend/src/app/pages/settings/settings.component.ts` |
| 6 | Add spec test | Test that component loads language on init, calls API on save, shows success/error feedback | `frontend/src/app/pages/settings/settings.component.spec.ts` (NEW) |
| 7 | Add service spec test | Test `getLanguagePreference` and `setLanguagePreference` HTTP calls | `frontend/src/app/services/settings.service.spec.ts` (NEW) |

## Key Files

### NEW Files
- `frontend/src/app/services/settings.service.ts` — Settings API service
- `frontend/src/app/services/settings.service.spec.ts` — Service tests
- `frontend/src/app/pages/settings/settings.component.ts` — Settings page component
- `frontend/src/app/pages/settings/settings.component.html` — Settings page template
- `frontend/src/app/pages/settings/settings.component.scss` — Settings page styles
- `frontend/src/app/pages/settings/settings.component.spec.ts` — Component tests

### MODIFIED Files
- `frontend/src/app/app.routes.ts` — Add `/settings` route
- `frontend/src/app/app.ts` — Add "Settings" to `settingsMenuItems`

## Implementation Details

### Settings Service (`frontend/src/app/services/settings.service.ts`)

```typescript
import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export interface LanguagePreference {
  language: string;
}

@Injectable({ providedIn: 'root' })
export class SettingsService {
  private readonly http = inject(HttpClient);

  getLanguagePreference(): Observable<LanguagePreference> {
    return this.http.get<LanguagePreference>('/api/settings/language');
  }

  setLanguagePreference(language: string): Observable<LanguagePreference> {
    return this.http.put<LanguagePreference>('/api/settings/language', { language });
  }
}
```

### Settings Component (`frontend/src/app/pages/settings/settings.component.ts`)

```typescript
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
  'English', 'Spanish', 'Chinese', 'French', 'German',
  'Japanese', 'Korean', 'Portuguese', 'Russian', 'Arabic',
  'Vietnamese', 'Italian', 'Dutch', 'Hindi',
];

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatFormFieldModule, MatSelectModule, MatInputModule, MatButtonModule,
  ],
  templateUrl: './settings.component.html',
  styleUrl: './settings.component.scss',
})
export class SettingsComponent implements OnInit {
  private readonly settingsService = inject(SettingsService);
  private readonly snackBar = inject(MatSnackBar);

  readonly languages = PREDEFINED_LANGUAGES;
  readonly selectedLanguage = signal('English');
  readonly customLanguage = signal('');
  readonly isCustom = signal(false);
  readonly saving = signal(false);

  ngOnInit(): void {
    this.settingsService.getLanguagePreference().subscribe({
      next: (pref) => {
        if (PREDEFINED_LANGUAGES.includes(pref.language)) {
          this.selectedLanguage.set(pref.language);
          this.isCustom.set(false);
        } else {
          this.selectedLanguage.set('Other (custom)');
          this.isCustom.set(true);
          this.customLanguage.set(pref.language);
        }
      },
      error: () => {
        this.snackBar.open('Failed to load language preference', 'Dismiss', { duration: 3000 });
      },
    });
  }

  onLanguageChange(value: string): void {
    if (value === 'Other (custom)') {
      this.isCustom.set(true);
    } else {
      this.isCustom.set(false);
      this.save(value);
    }
  }

  saveCustom(): void {
    const lang = this.customLanguage().trim();
    if (lang) {
      this.save(lang);
    }
  }

  private save(language: string): void {
    this.saving.set(true);
    this.settingsService.setLanguagePreference(language).subscribe({
      next: () => {
        this.saving.set(false);
        this.snackBar.open(`Language preference set to ${language}`, 'OK', { duration: 3000 });
      },
      error: () => {
        this.saving.set(false);
        this.snackBar.open('Failed to save language preference', 'Dismiss', { duration: 3000 });
      },
    });
  }
}
```

### Settings Template (`frontend/src/app/pages/settings/settings.component.html`)

```html
<div class="settings-container">
  <h1>Settings</h1>
  
  <section class="setting-section">
    <h2>Language Preference</h2>
    <p class="setting-description">
      Set your preferred language. All agents will be instructed to respond in this language.
      A language check will verify responses and prompt agents to correct if needed.
    </p>
    
    <mat-form-field appearance="outline">
      <mat-label>Preferred Language</mat-label>
      <mat-select [value]="selectedLanguage()" (selectionChange)="onLanguageChange($event.value)">
        @for (lang of languages; track lang) {
          <mat-option [value]="lang">{{ lang }}</mat-option>
        }
        <mat-option value="Other (custom)">Other (custom)</mat-option>
      </mat-select>
    </mat-form-field>
    
    @if (isCustom()) {
      <div class="custom-language-row">
        <mat-form-field appearance="outline" class="custom-input">
          <mat-label>Custom Language</mat-label>
          <input matInput [value]="customLanguage()" (input)="customLanguage.set($any($event.target).value)" 
                 placeholder="e.g., Thai, Swedish, Polish" />
        </mat-form-field>
        <button mat-raised-button color="primary" (click)="saveCustom()" [disabled]="saving()">
          Save
        </button>
      </div>
    }
    
    @if (saving()) {
      <span class="saving-indicator">Saving...</span>
    }
  </section>
</div>
```

### Route Addition (`frontend/src/app/app.routes.ts`)

```typescript
// Add to routes array:
{ path: 'settings', loadComponent: () => import('./pages/settings/settings.component').then(m => m.SettingsComponent) },
```

### Nav Menu Addition (`frontend/src/app/app.ts:52`)

```typescript
readonly settingsMenuItems = signal<SettingsMenuItem[]>([
  { label: 'MCP Servers', icon: 'settings_input_hdmi', route: '/mcp-servers' },
  { label: 'Settings', icon: 'language', route: '/settings' },  // NEW
]);
```

## Edge Cases

### What if the API is not available?
- `getLanguagePreference()` error → show snackbar "Failed to load", default to "English"
- `setLanguagePreference()` error → show snackbar "Failed to save", keep current selection

### What if the stored language is not in the predefined list?
- Component detects this on load and switches to "Other (custom)" mode
- The custom text input is populated with the stored value

### What about the dynamic "Database" menu item?
- The Database menu item is added conditionally in `checkMigrationAvailability()` (app.ts:72-89)
- The Settings item is added statically — it will always be visible
- Order: MCP Servers → Settings → Database (if available)

## Constraints
- Must use Angular standalone components (no NgModules)
- Must use Angular Material components (consistent with rest of app)
- Must handle HTTP errors gracefully (snackbar notifications)
- The `HttpClient` calls use relative URLs (`/api/settings/language`) — proxied by Angular dev server

## Deliverables
- [ ] `frontend/src/app/services/settings.service.ts` with `getLanguagePreference()` and `setLanguagePreference()`
- [ ] `frontend/src/app/pages/settings/settings.component.ts` + `.html` + `.scss`
- [ ] Route `/settings` added to `app.routes.ts`
- [ ] "Settings" menu item added to gear menu in `app.ts`
- [ ] Language dropdown with predefined options + custom input
- [ ] Save triggers `PUT /api/settings/language` and shows confirmation
- [ ] Error handling with snackbar notifications
- [ ] Tests for service and component
