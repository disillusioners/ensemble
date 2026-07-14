import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, tap, catchError, of, finalize, map } from 'rxjs';
import {
  SkillBankItem,
  SkillBankItemCreate,
  SkillBankItemUpdate,
  SkillBankFilters,
} from '../models/skill-bank.model';

/**
 * Delete response shape returned by ``DELETE /api/skill-bank/{id}``.
 *
 * Kept as a structural type so callers can check ``.deleted`` without
 * having to know about the full backend payload — matches the
 * ``DeactivationResponse`` convention used by ``SkillService.delete``.
 */
export interface SkillBankDeleteResponse {
  deleted: boolean;
}

/**
 * Service for the ``/api/skill-bank`` Skill Bank surface (Phase 3).
 *
 * Mirrors the constructor-injection + signals pattern used by
 * ``SkillService`` so the Skill Bank page slots in beside the
 * existing Skills list page without restructuring component or
 * template code.
 *
 * Mutation surface covers the CRUD set the Skill Bank page needs:
 *
 * * ``list``    — ``GET /api/skill-bank`` with project / category
 *   filters.
 * * ``create``  — ``POST /api/skill-bank``.
 * * ``update``  — ``PUT /api/skill-bank/{id}`` (partial).
 * * ``delete``  — ``DELETE /api/skill-bank/{id}``.
 *
 * Failure handling follows ``SkillService``: ``list`` swallows the
 * error and returns an empty array (so the list can keep rendering
 * its skeleton / empty state), every other method re-throws via
 * ``Observable<never>`` so the caller can render a snackbar while
 * the shared ``error`` signal still surfaces for any observers that
 * prefer signal-based error wiring.
 *
 * Isolation note: this service deliberately does NOT expose metrics,
 * lineage, A/B testing, or deactivation methods — the Skill Bank
 * is a pure user-facing CRUD surface over immutable templates.
 */
@Injectable({
  providedIn: 'root'
})
export class SkillBankService {
  private readonly http = inject(HttpClient);
  private readonly API_BASE = '/api/skill-bank';

  // Signals for state — matches SkillService shape so the Skill Bank
  // page can swap services without restructuring template or
  // component logic.
  readonly items = signal<SkillBankItem[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  /**
   * GET /api/skill-bank?project_id=...&category=...
   *
   * Empty / undefined filter values are stripped before the request so
   * the backend only sees the params the caller actually filtered on.
   *
   * Args:
   *     filters: Optional filter object. All fields are optional.
   *
   * Returns:
   *     Observable<SkillBankItem[]> — also pushed into the ``items``
   *     signal. The backend wraps the array in ``{items, total}``;
   *     we unwrap the ``items`` field here.
   */
  list(filters?: SkillBankFilters): Observable<SkillBankItem[]> {
    let params = new HttpParams();
    if (filters) {
      if (filters.category) params = params.set('category', filters.category);
      if (filters.project_id) params = params.set('project_id', filters.project_id);
    }

    this.loading.set(true);
    return this.http
      .get<{ items?: SkillBankItem[] } | SkillBankItem[]>(this.API_BASE, { params })
      .pipe(
        map((res: any) => (Array.isArray(res) ? res : res?.items ?? []) as SkillBankItem[]),
        tap((items) => this.items.set(items)),
        catchError((err) => {
          this.error.set(err?.message || 'Failed to fetch skill bank items');
          return of([] as SkillBankItem[]);
        }),
        finalize(() => this.loading.set(false))
      );
  }

  /**
   * POST /api/skill-bank
   *
   * Creates a new bank skill and prepends it to the ``items`` signal
   * so the list page sees the row immediately. The backend returns
   * the fully-populated row directly (no envelope).
   *
   * Args:
   *     data: SkillBankItemCreate payload.
   *
   * Returns:
   *     Observable<SkillBankItem> — re-thrown on error.
   */
  create(data: SkillBankItemCreate): Observable<SkillBankItem> {
    return this.http.post<SkillBankItem>(this.API_BASE, data).pipe(
      tap((createdItem) => {
        this.items.update((items) => [createdItem, ...items]);
      }),
      catchError((err) => {
        this.error.set(err?.message || 'Failed to create bank skill');
        throw err;
      })
    );
  }

  /**
   * PUT /api/skill-bank/{id}
   *
   * Partial update — only the fields present on ``data`` are sent.
   * On success the matching row in the ``items`` signal is replaced
   * with the backend response so the list re-renders the new values
   * without a second fetch.
   *
   * Args:
   *     id: Skill bank UUID.
   *     data: Partial fields to update.
   *
   * Returns:
   *     Observable<SkillBankItem> — re-thrown on error.
   */
  update(id: string, data: SkillBankItemUpdate): Observable<SkillBankItem> {
    return this.http
      .put<SkillBankItem>(
        `${this.API_BASE}/${encodeURIComponent(id)}`,
        data
      )
      .pipe(
        tap((updatedItem) => {
          this.items.update((items) =>
            items.map((item) => (item.id === id ? updatedItem : item))
          );
        }),
        catchError((err) => {
          this.error.set(err?.message || 'Failed to update bank skill');
          throw err;
        })
      );
  }

  /**
   * DELETE /api/skill-bank/{id}
   *
   * Hard delete — the backend removes the row entirely. Returns
   * ``{deleted: true}`` on success. Removes the skill from the
   * local ``items`` signal so the list re-renders without a
   * refetch.
   *
   * Args:
   *     id: Skill bank UUID.
   *
   * Returns:
   *     Observable<SkillBankDeleteResponse> — re-thrown on error.
   */
  delete(id: string): Observable<SkillBankDeleteResponse> {
    return this.http
      .delete<SkillBankDeleteResponse>(
        `${this.API_BASE}/${encodeURIComponent(id)}`
      )
      .pipe(
        tap(() => {
          this.items.update((items) => items.filter((item) => item.id !== id));
        }),
        catchError((err) => {
          this.error.set(err?.message || 'Failed to delete bank skill');
          throw err;
        })
      );
  }

  /**
   * Helper to refresh the bank list while keeping the loading
   * state surface aligned with ``SkillService.refreshSkills``.
   *
   * Args:
   *     filters: Optional filters forwarded to ``list``.
   */
  refresh(filters?: SkillBankFilters): void {
    this.loading.set(true);
    this.list(filters).subscribe({
      next: () => this.loading.set(false),
      error: () => this.loading.set(false),
    });
  }

  /**
   * Helper to clear the ``error`` signal — mirrors
   * ``SkillService.clearError``.
   */
  clearError(): void {
    this.error.set(null);
  }
}