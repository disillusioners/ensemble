import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, tap, catchError, of, finalize, map } from 'rxjs';
import {
  PromptVersion,
  PromptVersionCreate,
  PromptVersionUpdate,
  PromptVersionDeleteResponse,
  PromptVersionDiffResponse,
  PromptVersionFilters,
  PromptVersionListResponse,
  PromptSelectionCreate,
  PromptSelectionResponse,
  EffectiveContentResponse,
  PromptAuditListResponse,
  PromptAuditEvent,
  PendingCountResponse,
} from '../models/persona.model';

/**
 * Service for the Persona (prompt-version) surface — agent-prompt-immutability
 * Phase C P1.
 *
 * Mirrors ``SkillBankService`` 1:1: HttpClient + signals (``items`` / ``loading``
 * / ``error``), envelope unwrap for list responses, swallow-and-empty for
 * ``list`` / ``countPending`` so the badge can render a skeleton without an
 * error toast, re-throw for mutation methods so the caller surfaces a
 * snackbar. Singleton (``providedIn: 'root'``) so the header badge (App
 * component) and the Persona page share one signal.
 *
 * Wire surface (15-endpoint canonical family):
 *   * list(filters)        — GET /api/prompts/versions
 *   * getVersion(id)       — GET /api/prompts/versions/{id}
 *   * diff(id, against)     — GET /api/prompts/versions/{id}/diff
 *   * approve(id) / submit(id) / retract(id) — state-machine transitions
 *   * reject(id)           — HUMAN lane only
 *   * createDraft(body)    — POST /api/prompts/versions
 *   * updateContent(id, body) — PATCH /api/prompts/versions/{id}
 *   * deleteVersion(id)    — DELETE /api/prompts/versions/{id}
 *   * getEffective(...)    — GET /api/prompts/effective/{agent}/{tag}/{artifact}
 *   * setSelection(body)   — POST /api/prompts/selections
 *   * clearSelection(...)  — DELETE /api/prompts/selections/{agent}/{tag}/{artifact}
 *   * listAudit(filters)   — GET /api/prompts/audit
 *   * pendingCount()       — GET /api/prompts/pending-count  (CORE-only by design)
 *   * refreshPendingCount() — re-pull without throwing
 *
 * The badge data path: ``App.constructor`` (and after each approve/reject
 * mutation) calls ``refreshPendingCount()`` which writes ``pendingCount``
 * signal; the header anchor reads ``pendingCount()`` and renders a
 * ``matBadge`` only when count > 0. CORE-only by repo design — the badge
 * is excluded from MEMORY rows automatically.
 */
@Injectable({
  providedIn: 'root',
})
export class PersonaService {
  private readonly http = inject(HttpClient);
  private readonly API_BASE = '/api/prompts';

  // Signals — same shape as SkillBankService so the page can mirror.
  readonly items = signal<PromptVersion[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  /** Pending-approval count (CORE-only) — drives the header ``matBadge``. */
  readonly pendingCount = signal(0);

  /** Track last refresh time for debugging + manual invalidation if needed. */
  readonly pendingCountFetchedAt = signal<number>(0);

  /**
   * GET /api/prompts/versions — list with filters + pagination.
   *
   * Filters are optional and AND-combined server-side; pagination via
   * ``limit`` / ``offset`` (defaults 100/0; server caps 500/0).
   *
   * The backend returns ``{items, total}``; we push into the ``items``
   * signal AND return the items array. ``list`` swallows the error and
   * returns an empty array so the page can render its empty state without
   * a snackbar.
   */
  list(filters?: PromptVersionFilters): Observable<PromptVersion[]> {
    let params = new HttpParams();
    if (filters) {
      if (filters.agent_id) params = params.set('agent_id', filters.agent_id);
      if (filters.version_tag) params = params.set('version_tag', filters.version_tag);
      if (filters.artifact) params = params.set('artifact', filters.artifact);
      if (filters.state) params = params.set('state', filters.state);
      if (filters.artifact_class) {
        params = params.set('artifact_class', filters.artifact_class);
      }
      if (filters.limit != null) params = params.set('limit', String(filters.limit));
      if (filters.offset != null) params = params.set('offset', String(filters.offset));
    }

    this.loading.set(true);
    return this.http.get<PromptVersionListResponse>(this.API_BASE + '/versions', { params }).pipe(
      map((res) => (res?.items ?? []) as PromptVersion[]),
      tap((items) => this.items.set(items)),
      catchError((err) => {
        this.error.set(err?.message || 'Failed to fetch versions');
        return of([] as PromptVersion[]);
      }),
      finalize(() => this.loading.set(false)),
    );
  }

  /**
   * GET /api/prompts/versions/{id} — single row fetch. Re-throws on error
   * so the caller can branch on the typed envelope.
   */
  getVersion(id: string): Observable<PromptVersion> {
    return this.http.get<PromptVersion>(
      `${this.API_BASE}/versions/${encodeURIComponent(id)}`,
    );
  }

  /**
   * GET /api/prompts/versions/{id}/diff?against=effective|approved|<vid>.
   *
   * Returns a unified-diff text body. ``against`` defaults to
   * ``effective`` per backend. Re-throws on error.
   */
  diff(id: string, against: string = 'effective'): Observable<PromptVersionDiffResponse> {
    const params = new HttpParams().set('against', against);
    return this.http.get<PromptVersionDiffResponse>(
      `${this.API_BASE}/versions/${encodeURIComponent(id)}/diff`,
      { params },
    );
  }

  /**
   * POST /api/prompts/versions/{id}/approve — HUMAN lane only (D2).
   * Returns the updated row. After success the token refreshes the
   * pending-count so the badge drains immediately.
   */
  approve(id: string): Observable<PromptVersion> {
    return this.http
      .post<PromptVersion>(`${this.API_BASE}/versions/${encodeURIComponent(id)}/approve`, {})
      .pipe(
        tap(() => this.refreshPendingCount()),
        catchError((err) => {
          this.error.set(err?.message || 'Failed to approve version');
          throw err;
        }),
      );
  }

  /**
   * POST /api/prompts/versions/{id}/reject — HUMAN lane only (D2).
   */
  reject(id: string): Observable<PromptVersion> {
    return this.http
      .post<PromptVersion>(`${this.API_BASE}/versions/${encodeURIComponent(id)}/reject`, {})
      .pipe(
        tap(() => this.refreshPendingCount()),
        catchError((err) => {
          this.error.set(err?.message || 'Failed to reject version');
          throw err;
        }),
      );
  }

  /**
   * POST /api/prompts/versions/{id}/submit — machine lane OK.
   */
  submit(id: string): Observable<PromptVersion> {
    return this.http
      .post<PromptVersion>(`${this.API_BASE}/versions/${encodeURIComponent(id)}/submit`, {})
      .pipe(
        tap(() => this.refreshPendingCount()),
        catchError((err) => {
          this.error.set(err?.message || 'Failed to submit version');
          throw err;
        }),
      );
  }

  /**
   * POST /api/prompts/versions/{id}/retract — machine lane OK.
   */
  retract(id: string): Observable<PromptVersion> {
    return this.http
      .post<PromptVersion>(`${this.API_BASE}/versions/${encodeURIComponent(id)}/retract`, {})
      .pipe(
        tap(() => this.refreshPendingCount()),
        catchError((err) => {
          this.error.set(err?.message || 'Failed to retract version');
          throw err;
        }),
      );
  }

  /**
   * POST /api/prompts/versions — create-draft (writes scope).
   */
  createDraft(data: PromptVersionCreate): Observable<PromptVersion> {
    return this.http.post<PromptVersion>(this.API_BASE + '/versions', data).pipe(
      tap((created) => {
        this.items.update((items) => [created, ...items]);
      }),
      catchError((err) => {
        this.error.set(err?.message || 'Failed to create draft');
        throw err;
      }),
    );
  }

  /**
   * PATCH /api/prompts/versions/{id} — edit draft/pending content.
   * Approved/rejected/archived rows surface 409 VERSION_IMMUTABLE.
   * MEMORY rows surface 409 MEMORY_CLASS_MISMATCH.
   */
  updateContent(id: string, body: PromptVersionUpdate): Observable<PromptVersion> {
    return this.http
      .patch<PromptVersion>(
        `${this.API_BASE}/versions/${encodeURIComponent(id)}`,
        body,
      )
      .pipe(
        tap((updated) => {
          this.items.update((items) =>
            items.map((it) => (it.version_id === id ? updated : it)),
          );
        }),
        catchError((err) => {
          this.error.set(err?.message || 'Failed to update version');
          throw err;
        }),
      );
  }

  /**
   * DELETE /api/prompts/versions/{id} — hard-delete draft/pending rows.
   * Same state rule as PATCH (409 VERSION_IMMUTABLE for non-draft rows).
   */
  deleteVersion(id: string): Observable<PromptVersionDeleteResponse> {
    return this.http
      .delete<PromptVersionDeleteResponse>(
        `${this.API_BASE}/versions/${encodeURIComponent(id)}`,
      )
      .pipe(
        tap(() => {
          this.items.update((items) => items.filter((it) => it.version_id !== id));
        }),
        catchError((err) => {
          this.error.set(err?.message || 'Failed to delete version');
          throw err;
        }),
      );
  }

  /**
   * GET /api/prompts/effective/{agent}/{tag}/{artifact} — the active
   * baseline. ``content`` is nullable: ``null`` means "template file is
   * the baseline" (the loader fallback owns the file read).
   */
  getEffective(agentId: string, versionTag: string, artifact: string): Observable<EffectiveContentResponse> {
    return this.http.get<EffectiveContentResponse>(
      `${this.API_BASE}/effective/${encodeURIComponent(agentId)}/${encodeURIComponent(versionTag)}/${encodeURIComponent(artifact)}`,
    );
  }

  /**
   * POST /api/prompts/selections — set the per-key one-selection pointer
   * (writes scope). The target version MUST be ``approved``.
   */
  setSelection(body: PromptSelectionCreate): Observable<PromptSelectionResponse> {
    return this.http.post<PromptSelectionResponse>(this.API_BASE + '/selections', body).pipe(
      catchError((err) => {
        this.error.set(err?.message || 'Failed to set selection');
        throw err;
      }),
    );
  }

  /**
   * DELETE /api/prompts/selections/{agent}/{tag}/{artifact} — clear the
   * selection pointer. Re-throws on error.
   */
  clearSelection(agentId: string, versionTag: string, artifact: string): Observable<PromptSelectionResponse> {
    return this.http
      .delete<PromptSelectionResponse>(
        `${this.API_BASE}/selections/${encodeURIComponent(agentId)}/${encodeURIComponent(versionTag)}/${encodeURIComponent(artifact)}`,
      )
      .pipe(
        catchError((err) => {
          this.error.set(err?.message || 'Failed to clear selection');
          throw err;
        }),
      );
  }

  /**
   * GET /api/prompts/audit — paginated audit log.
   * Filters are optional + AND-combined.
   */
  listAudit(filters?: Partial<PromptAuditEvent> & { limit?: number; offset?: number }): Observable<PromptAuditListResponse> {
    let params = new HttpParams();
    if (filters) {
      if (filters.agent_id) params = params.set('agent_id', filters.agent_id);
      if (filters.version_tag) params = params.set('version_tag', filters.version_tag);
      if (filters.artifact) params = params.set('artifact', filters.artifact);
      if (filters.event_type) params = params.set('event_type', filters.event_type);
      if (filters.version_id) params = params.set('version_id', filters.version_id);
      if (filters.actor_subject) params = params.set('actor_subject', filters.actor_subject);
      if (filters.limit != null) params = params.set('limit', String(filters.limit));
      if (filters.offset != null) params = params.set('offset', String(filters.offset));
    }
    return this.http.get<PromptAuditListResponse>(this.API_BASE + '/audit', { params });
  }

  /**
   * GET /api/prompts/pending-count — CORE-only by design (the repo
   * excludes MEMORY rows since they're born-effective).
   *
   * Returns the envelope; ``refreshPendingCount`` is the side-effecting
   * helper that also writes the signal. ``pendingCount`` swallows the
   * error so the badge doesn't throw during a transient blip.
   */
  pendingCount$(): Observable<PendingCountResponse> {
    return this.http.get<PendingCountResponse>(this.API_BASE + '/pending-count').pipe(
      catchError((err) => {
        this.error.set(err?.message || 'Failed to fetch pending count');
        return of({ count: 0 });
      }),
    );
  }

  /**
   * Refresh the pending-count signal without throwing. Called by the
   * App component on boot + after every approve/reject/submit/retract.
   * The badge reads ``pendingCount()`` directly.
   */
  refreshPendingCount(): void {
    this.pendingCount$().subscribe({
      next: (res) => {
        this.pendingCount.set(res?.count ?? 0);
        this.pendingCountFetchedAt.set(Date.now());
      },
      error: () => {
        // Already captured by pendingCount$ catchError; keep silent.
      },
    });
  }

  /** Helper to clear the ``error`` signal — mirrors SkillBankService. */
  clearError(): void {
    this.error.set(null);
  }
}