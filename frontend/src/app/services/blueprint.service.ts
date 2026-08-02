import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, tap, catchError, of, finalize, map } from 'rxjs';
import {
  Blueprint,
  BlueprintCreateRequest,
  BlueprintUpdateRequest,
  BlueprintRevision,
  BlueprintFilters,
} from '../models/blueprint.model';

/**
 * Delete response shape returned by
 * ``DELETE /api/projects/{project_id}/blueprints/{blueprint_id}``.
 * Kept as a structural type so callers can check ``.deleted`` without
 * having to know about the full backend payload — matches the
 * ``SkillBankDeleteResponse`` convention.
 */
export interface BlueprintDeleteResponse {
  deleted: boolean;
}

/**
 * Service for the ``/api/projects/{project_id}/blueprints/*`` Project
 * Blueprint surface (Phase 5).
 *
 * Mirrors the constructor-injection + signals pattern used by
 * ``SkillBankService`` so the Blueprint page slots in beside the
 * existing list pages without restructuring component or template
 * code.
 *
 * Mutation surface covers the CRUD set the Blueprint page needs:
 *
 * * ``list``         — ``GET /api/projects/{project_id}/blueprints``
 *   with optional ``kind`` / ``status`` query params.
 * * ``get``          — ``GET .../{blueprint_id}``.
 * * ``create``       — ``POST ...`` (project_id from URL path).
 * * ``update``       — ``PUT .../{blueprint_id}`` (partial).
 * * ``delete``       — ``DELETE .../{blueprint_id}`` (soft-delete).
 * * ``getRevisions`` — ``GET .../{blueprint_id}/revisions``.
 *
 * Failure handling follows ``SkillBankService``: ``list`` swallows the
 * error and returns an empty array (so the list can keep rendering
 * its skeleton / empty state), every other method re-throws via
 * ``Observable<never>`` so the caller can render a snackbar while
 * the shared ``error`` signal still surfaces for any observers that
 * prefer signal-based error wiring.
 *
 * Project scoping: every public method takes a ``projectId`` as the
 * first argument. The base URL is built per-call as
 * ``/api/projects/{project_id}/blueprints`` — there is no
 * per-instance ``API_BASE`` constant, so the same service can be
 * reused across multiple project contexts without rebuilds.
 */
@Injectable({
  providedIn: 'root',
})
export class BlueprintService {
  private readonly http = inject(HttpClient);

  // Signals for state — matches SkillBankService shape so the
  // Blueprint page can swap services without restructuring template
  // or component logic.
  readonly blueprints = signal<Blueprint[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  /**
   * Build the per-project base URL. Centralised so the
   * ``project_id`` URL-encoding rule lives in one place (defends
   * against future project-id collisions with special characters).
   */
  private baseUrl(projectId: string): string {
    return `/api/projects/${encodeURIComponent(projectId)}/blueprints`;
  }

  /**
   * GET /api/projects/{project_id}/blueprints?kind=...&status=...
   *
   * Empty / undefined filter values are stripped before the request so
   * the backend only sees the params the caller actually filtered on.
   *
   * Args:
   *     projectId: Project UUID/slug — scopes the entire request.
   *     kind: Optional kind filter ('core' | 'area').
   *     status: Optional status filter ('published' | 'draft').
   *
   * Returns:
   *     Observable<Blueprint[]> — also pushed into the ``blueprints``
   *     signal. The backend wraps the array in ``{items, total}``;
   *     we unwrap the ``items`` field here.
   */
  list(
    projectId: string,
    kind?: BlueprintFilters['kind'],
    status?: BlueprintFilters['status'],
  ): Observable<Blueprint[]> {
    let params = new HttpParams();
    if (kind) params = params.set('kind', kind);
    if (status) params = params.set('status', status);

    this.loading.set(true);
    return this.http
      .get<{ items?: Blueprint[] } | Blueprint[]>(this.baseUrl(projectId), {
        params,
      })
      .pipe(
        map(
          (res: { items?: Blueprint[] } | Blueprint[]) =>
            (Array.isArray(res) ? res : res?.items ?? []) as Blueprint[],
        ),
        tap((items) => this.blueprints.set(items)),
        catchError((err) => {
          this.error.set(err?.message || 'Failed to fetch blueprints');
          return of([] as Blueprint[]);
        }),
        finalize(() => this.loading.set(false)),
      );
  }

  /**
   * GET /api/projects/{project_id}/blueprints/{blueprint_id}
   *
   * Single-blueprint fetch. Returns the full row including
   * ``content`` for the detail / edit panel.
   *
   * Args:
   *     projectId: Project UUID/slug.
   *     id: Blueprint UUID.
   *
   * Returns:
   *     Observable<Blueprint> — re-thrown on error.
   */
  get(projectId: string, id: string): Observable<Blueprint> {
    return this.http
      .get<Blueprint>(
        `${this.baseUrl(projectId)}/${encodeURIComponent(id)}`,
      )
      .pipe(
        catchError((err) => {
          this.error.set(err?.message || 'Failed to fetch blueprint');
          throw err;
        }),
      );
  }

  /**
   * POST /api/projects/{project_id}/blueprints
   *
   * Creates a new blueprint and prepends it to the ``blueprints``
   * signal so the list page sees the row immediately. The backend
   * returns the fully-populated row directly (no envelope).
   *
   * Args:
   *     projectId: Project UUID/slug.
   *     data: BlueprintCreateRequest payload.
   *
   * Returns:
   *     Observable<Blueprint> — re-thrown on error.
   */
  create(
    projectId: string,
    data: BlueprintCreateRequest,
  ): Observable<Blueprint> {
    return this.http
      .post<Blueprint>(this.baseUrl(projectId), data)
      .pipe(
        tap((created) => {
          this.blueprints.update((items) => [created, ...items]);
        }),
        catchError((err) => {
          this.error.set(err?.message || 'Failed to create blueprint');
          throw err;
        }),
      );
  }

  /**
   * PUT /api/projects/{project_id}/blueprints/{blueprint_id}
   *
   * Partial update — only the fields present on ``data`` are sent.
   * On success the matching row in the ``blueprints`` signal is
   * replaced with the backend response so the list re-renders the
   * new values without a second fetch.
   *
   * Args:
   *     projectId: Project UUID/slug.
   *     id: Blueprint UUID.
   *     data: Partial fields to update.
   *
   * Returns:
   *     Observable<Blueprint> — re-thrown on error.
   */
  update(
    projectId: string,
    id: string,
    data: BlueprintUpdateRequest,
  ): Observable<Blueprint> {
    return this.http
      .put<Blueprint>(
        `${this.baseUrl(projectId)}/${encodeURIComponent(id)}`,
        data,
      )
      .pipe(
        tap((updated) => {
          this.blueprints.update((items) =>
            items.map((item) => (item.id === id ? updated : item)),
          );
        }),
        catchError((err) => {
          this.error.set(err?.message || 'Failed to update blueprint');
          throw err;
        }),
      );
  }

  /**
   * DELETE /api/projects/{project_id}/blueprints/{blueprint_id}
   *
   * Soft-delete — the backend sets ``is_active=False`` (the row is
   * hidden from subsequent ``list`` calls but preserved for audit
   * trails). Returns ``{deleted: true}`` on success. Removes the
   * blueprint from the local ``blueprints`` signal so the list
   * re-renders without a refetch.
   *
   * Args:
   *     projectId: Project UUID/slug.
   *     id: Blueprint UUID.
   *
   * Returns:
   *     Observable<BlueprintDeleteResponse> — re-thrown on error.
   */
  delete(
    projectId: string,
    id: string,
  ): Observable<BlueprintDeleteResponse> {
    return this.http
      .delete<BlueprintDeleteResponse>(
        `${this.baseUrl(projectId)}/${encodeURIComponent(id)}`,
      )
      .pipe(
        tap(() => {
          this.blueprints.update((items) =>
            items.filter((item) => item.id !== id),
          );
        }),
        catchError((err) => {
          this.error.set(err?.message || 'Failed to delete blueprint');
          throw err;
        }),
      );
  }

  /**
   * GET /api/projects/{project_id}/blueprints/{blueprint_id}/revisions
   *
   * List revision history (newest first). Returns the bare
   * ``BlueprintRevision[]`` array — no envelope. The Pydantic
   * ``BlueprintRevisionResponse`` schema is the source of truth for
   * which fields are present (no ``changed_by`` / ``change_source`` /
   * ``reason`` / ``changed_at``).
   *
   * Args:
   *     projectId: Project UUID/slug.
   *     id: Blueprint UUID.
   *
   * Returns:
   *     Observable<BlueprintRevision[]> — re-thrown on error.
   */
  getRevisions(
    projectId: string,
    id: string,
  ): Observable<BlueprintRevision[]> {
    return this.http
      .get<BlueprintRevision[]>(
        `${this.baseUrl(projectId)}/${encodeURIComponent(id)}/revisions`,
      )
      .pipe(
        catchError((err) => {
          this.error.set(
            err?.message || 'Failed to fetch blueprint revisions',
          );
          throw err;
        }),
      );
  }

  /**
   * Helper to clear the ``error`` signal — mirrors
   * ``SkillBankService.clearError``.
   */
  clearError(): void {
    this.error.set(null);
  }
}
