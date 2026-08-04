import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, tap, catchError, of, finalize, map, throwError } from 'rxjs';
import {
  Blueprint,
  BlueprintCreateRequest,
  BlueprintUpdateRequest,
  BlueprintRevision,
  BlueprintFilters,
  BlueprintJobResponse,
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
   *     quiet: When ``true``, skip the ``loading`` signal toggles.
   *         Used by the rebuild/update poll loop so the 10s tick
   *         doesn't flash the skeleton / refresh-disabled state every
   *         cycle. Defaults to ``false`` so one-shot callers (the
   *         list page, manual refresh) keep the visible loading
   *         indicator.
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
    quiet = false,
  ): Observable<Blueprint[]> {
    let params = new HttpParams();
    if (kind) params = params.set('kind', kind);
    if (status) params = params.set('status', status);

    if (!quiet) {
      this.loading.set(true);
    }
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
        finalize(() => {
          if (!quiet) {
            this.loading.set(false);
          }
        }),
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
   * POST /api/projects/{project_id}/blueprints/initialize
   *
   * Triggers blueprint initialization on the backend (spawns a
   * blueprinter agent on the background queue). The request returns
   * immediately with 202 Accepted — the actual work runs asynchronously.
   *
   * A 409 response means blueprints are already initialized; the error
   * is surfaced as a thrown Error with a clear message so the caller
   * can render a distinct snackbar.
   *
   * Args:
   *     projectId: Project UUID/slug.
   *
   * Returns:
   *     Observable<void> — re-thrown on error (including 409).
   *
   * @deprecated Use :meth:`rebuild` instead. ``/initialize`` is the
   *     Phase 5 single-shot endpoint and has been superseded by
   *     ``/rebuild`` (full re-scan) and ``/update`` (incremental).
   *     Kept for backward compatibility with any callers that still
   *     reference it; new UI should drive the dual-mode flow.
   */
  initialize(projectId: string): Observable<void> {
    return this.http
      .post<void>(`${this.baseUrl(projectId)}/initialize`, {})
      .pipe(
        catchError((err) => {
          if (err?.status === 409) {
            return throwError(
              () => makeHttpStatusError(409, 'Blueprints already initialized'),
            );
          }
          this.error.set(err?.message || 'Failed to initialize blueprints');
          return throwError(() => err);
        }),
      );
  }

  /**
   * POST /api/projects/{project_id}/blueprints/rebuild
   *
   * Enqueues a full blueprint rebuild job on the background queue.
   * The backend responds 202 with ``BlueprintJobResponse`` — actual
   * work happens asynchronously; the caller should poll ``list()``
   * to observe the new blueprints land.
   *
   * Outcomes:
   *   * 202 ``status='accepted'`` — job enqueued, caller starts polling.
   *   * 202 ``status='already_in_progress'`` — coalesced duplicate
   *     (NOT an error, also 202). Caller surfaces as a soft snackbar.
   *   * 409 — different mode (e.g. an ``/update`` job is in flight).
   *     Re-thrown with ``.status=409`` so the component's
   *     ``showMutationError`` renders the right message.
   *   * 503 — coordinator/queue not wired; ``showMutationError``
   *     already handles this.
   *   * 404 — queue missing on the backend.
   *
   * Args:
   *     projectId: Project UUID/slug.
   *
   * Returns:
   *     Observable<BlueprintJobResponse> — re-thrown on error.
   */
  rebuild(projectId: string): Observable<BlueprintJobResponse> {
    this.loading.set(true);
    return this.http
      .post<BlueprintJobResponse>(`${this.baseUrl(projectId)}/rebuild`, {})
      .pipe(
        catchError((err) => {
          if (err?.status === 409) {
            this.error.set('Blueprint rebuild already in progress');
            return throwError(
              () => makeHttpStatusError(409, 'Blueprint rebuild already in progress'),
            );
          }
          // Generic / unexpected (5xx, network, 404, etc.) — replace
          // the raw ``HttpErrorResponse.message`` ("Http failure
          // response for ...") with a friendly line before re-throwing
          // so the component's snackbar renders a user-readable copy.
          this.error.set('Failed to rebuild blueprints. Please try again.');
          return throwError(
            () =>
              makeHttpStatusError(
                err?.status ?? 0,
                'Failed to rebuild blueprints. Please try again.',
              ),
          );
        }),
        finalize(() => this.loading.set(false)),
      );
  }

  /**
   * POST /api/projects/{project_id}/blueprints/update
   *
   * Enqueues an incremental blueprint update job on the background
   * queue. Same response shape as :meth:`rebuild` (``mode='incremental'``).
   * Use when the project already has blueprints and you want to
   * process recent changes only.
   *
   * Outcomes mirror :meth:`rebuild` PLUS:
   *   * 404 — no blueprints exist yet; caller should use
   *     :meth:`rebuild` instead. We re-throw with a clear message
   *     AND ``.status=404`` so the component can render a
   *     "use rebuild first" hint.
   *
   * Args:
   *     projectId: Project UUID/slug.
   *
   * Returns:
   *     Observable<BlueprintJobResponse> — re-thrown on error.
   */
  updateBlueprints(projectId: string): Observable<BlueprintJobResponse> {
    this.loading.set(true);
    return this.http
      .post<BlueprintJobResponse>(`${this.baseUrl(projectId)}/update`, {})
      .pipe(
        catchError((err) => {
          if (err?.status === 409) {
            this.error.set('Blueprint update already in progress');
            return throwError(
              () => makeHttpStatusError(409, 'Blueprint update already in progress'),
            );
          }
          if (err?.status === 404) {
            return throwError(
              () =>
                makeHttpStatusError(
                  404,
                  'No blueprints found. Use Rebuild first.',
                ),
            );
          }
          // Generic / unexpected (5xx, network, etc.) — see the same
          // branch in :meth:`rebuild` for why we wrap with a friendly
          // message instead of re-throwing the raw HttpErrorResponse.
          this.error.set('Failed to update blueprints. Please try again.');
          return throwError(
            () =>
              makeHttpStatusError(
                err?.status ?? 0,
                'Failed to update blueprints. Please try again.',
              ),
          );
        }),
        finalize(() => this.loading.set(false)),
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

  // ── Per-project opt-in toggle (Phase 7) ──────────────────────────────
  //
  // Blueprint usage is a per-project opt-in: the system flag
  // (``auto_rebuild_enabled``) controls whether the feature exists at
  // all; this ``setBlueprintActive`` controls whether the current
  // project participates. The metadata key lives in
  // ``project_metadata_records`` under ``blueprint_active``; absent
  // from the KV = inactive. The frontend defaults to "inactive" so a
  // project must explicitly enable the blueprint system.
  //
  // We use the generic ``PUT /api/projects/{id}/metadata/{key}``
  // endpoint (added alongside the gate) rather than a blueprint-
  // specific one — the same path serves any per-project metadata
  // toggle we add later.

  /**
   * PUT /api/projects/{id}/metadata/blueprint_active
   *
   * Flips the per-project blueprint opt-in. Returns ``void`` so the
   * caller can subscribe without inspecting the response body — the
   * backend responds with ``{"ok": true}`` on success.
   *
   * Args:
   *     projectId: Project UUID/slug.
   *     active: ``true`` to enable, ``false`` to disable.
   *
   * Returns:
   *     ``Observable<void>`` — re-thrown on error so the caller can
   *     render a snackbar.
   */
  setBlueprintActive(projectId: string, active: boolean): Observable<void> {
    // The metadata endpoint lives under ``/api/projects/{id}`` — the
    // service is mounted at ``/api/projects/{id}/blueprints`` so we
    // strip the trailing ``/blueprints`` segment to derive the base.
    const base = this.baseUrl(projectId).replace(/\/blueprints$/, '');
    const url = `${base}/metadata/blueprint_active`;
    return this.http
      .put<void>(url, { value: active })
      .pipe(
        catchError((err) => {
          this.error.set(
            err?.message || `Failed to ${active ? 'enable' : 'disable'} blueprint`,
          );
          throw err;
        }),
      );
  }
}

/**
 * Build a synthetic ``Error`` that carries an HTTP ``status`` field.
 *
 * The original ``HttpErrorResponse`` from Angular has ``.status`` on it,
 * but we replace the thrown error in our ``catchError`` chains so we
 * can attach a friendlier ``.message``. Callers (e.g. the
 * component's ``showMutationError``) need both the friendly text and
 * the original status code to pick the right snackbar copy.
 *
 * Pure factory — no imports, easy to unit-test.
 */
function makeHttpStatusError(status: number, message: string): Error & {
  status: number;
} {
  const err = new Error(message) as Error & { status: number };
  err.status = status;
  return err;
}
