import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, tap, catchError, of, finalize, map } from 'rxjs';
import {
  Skill,
  SkillDetail,
  SkillFilters,
  SkillCreate,
  SkillUpdate,
  SearchResults,
  SkillFeedback,
  SkillFixRequest,
  SkillFixResponse,
  SkillMetrics,
  SkillLineage,
  AbTestStatus,
} from '../models/skill.model';

/**
 * Result shape returned by ``POST /api/skills/{id}/ab-test/resolve``.
 *
 * The backend decides the winner server-side (no body on the
 * request) and returns the resolution dict verbatim. The frontend
 * treats it as opaque — callers (e.g. the A/B test banner) only
 * surface ``resolved`` / ``winner_id`` / ``reason`` to the user.
 */
export interface AbTestResolveResponse {
  skill_id: string;
  ab_test_group: string;
  resolved: boolean;
  winner_id: string | null;
  loser_id: string | null;
  reason: string | null;
  extension_count: number;
}

/**
 * Deactivation result returned by ``POST /api/skills/{id}/deactivate``
 * (and the ``DELETE`` alias). Soft delete only — keeps usage history
 * queryable.
 */
export interface DeactivationResponse {
  deactivated: boolean;
}

/**
 * Service for the ``/api/skills`` Skills management surface
 * (Phase 6).
 *
 * Mirrors the constructor-injection + signals pattern used by
 * ``WorkService`` and ``JobService`` so the Skills page slots in
 * beside the existing list pages without restructuring component
 * or template code.
 *
 * Mutation surface covers the full CRUD set the Skills page needs:
 *
 * * ``list``    — ``GET /api/skills`` with project / category /
 *   ``active_only`` filters.
 * * ``get``     — ``GET /api/skills/{id}`` (detail incl. content).
 * * ``create``  — ``POST /api/skills``.
 * * ``update``  — ``PUT /api/skills/{id}`` (partial).
 * * ``delete``  — ``DELETE /api/skills/{id}`` (soft delete).
 * * ``deactivate`` — ``POST /api/skills/{id}/deactivate``.
 * * ``shareToGlobal`` — ``POST /api/skills/{id}/share``.
 *
 * Plus the side-panel helpers used by the detail view:
 * ``search``, ``getMetrics``, ``getLineage``, ``submitFeedback``,
 * ``requestFix``, ``getAbTestStatus`` and ``resolveAbTest``.
 *
 * Failure handling follows ``WorkService``: ``list`` swallows the
 * error and returns an empty array (so the table can keep
 * rendering its skeleton / empty state), every other method
 * re-throws via ``Observable<never>`` so the caller can render a
 * snackbar while the shared ``error`` signal still surfaces for
 * any observers that prefer signal-based error wiring.
 */
@Injectable({
  providedIn: 'root'
})
export class SkillService {
  private readonly http = inject(HttpClient);
  private readonly API_BASE = '/api/skills';

  // Signals for state — matches WorkService/JobService shape so the
  // Skills page can swap services without restructuring template
  // or component logic.
  readonly skills = signal<Skill[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  /**
   * GET /api/skills?category=...&project_id=...&active_only=...
   *
   * Empty / undefined filter values are stripped before the request so
   * the backend only sees the params the caller actually filtered on.
   * ``is_active`` is serialised as the backend's ``active_only`` flag
   * — FastAPI's ``bool`` coercion prefers explicit ``true``/``false``
   * over a bare token, matching the ``WorkService.root_only`` shape.
   *
   * Note: free-text ``search`` is intentionally NOT wired through to
   * the backend — the Skills page does client-side filtering on
   * ``name`` / ``description`` so we skip the round-trip and keep
   * the request payload minimal.
   *
   * Args:
   *     filters: Optional filter object. All fields are optional.
   *
   * Returns:
   *     Observable<Skill[]> — also pushed into the ``skills`` signal.
   *     The backend wraps the array in ``{items, total}``; we
   *     unwrap the ``items`` field here.
   */
  list(filters?: SkillFilters): Observable<Skill[]> {
    let params = new HttpParams();
    if (filters) {
      if (filters.category) params = params.set('category', filters.category);
      if (filters.project_id) params = params.set('project_id', filters.project_id);
      if (filters.is_active !== undefined) {
        // Backend uses the ``active_only`` query flag; map the
        // public ``SkillFilters.is_active`` onto the wire name.
        params = params.set('active_only', filters.is_active ? 'true' : 'false');
      }
    }

    this.loading.set(true);
    return this.http.get<{ items?: Skill[] } | Skill[]>(this.API_BASE, { params }).pipe(
      map((res: any) => (Array.isArray(res) ? res : res?.items ?? []) as Skill[]),
      tap((skills) => this.skills.set(skills)),
      catchError((err) => {
        this.error.set(err?.message || 'Failed to fetch skills');
        return of([] as Skill[]);
      }),
      finalize(() => this.loading.set(false))
    );
  }

  /**
   * GET /api/skills/{id}
   *
   * Returns the detail bundle (Skill body + Markdown ``content`` +
   * metrics + nested lineage). The backend wraps the row in
   * ``{skill: {...}}`` — we unwrap the envelope here so callers see
   * the ``SkillDetail`` shape directly.
   *
   * Args:
   *     id: Skill UUID.
   *
   * Returns:
   *     Observable<SkillDetail> — re-thrown on error.
   */
  get(id: string): Observable<SkillDetail> {
    return this.http
      .get<{ skill?: SkillDetail } | SkillDetail>(
        `${this.API_BASE}/${encodeURIComponent(id)}`
      )
      .pipe(
        map((res: any) => (res?.skill ?? res) as SkillDetail),
        catchError((err) => {
          this.error.set(err?.message || 'Failed to fetch skill');
          throw err;
        })
      );
  }

  /**
   * POST /api/skills
   *
   * Creates a new skill and prepends it to the ``skills`` signal so
   * the list page sees the row immediately. The backend returns the
   * fully-populated row wrapped in ``{skill: {...}}`` — we unwrap
   * the envelope here.
   *
   * Args:
   *     data: SkillCreate payload.
   *
   * Returns:
   *     Observable<Skill> — re-thrown on error.
   */
  create(data: SkillCreate): Observable<Skill> {
    return this.http.post<{ skill?: Skill } | Skill>(this.API_BASE, data).pipe(
      map((res: any) => (res?.skill ?? res) as Skill),
      tap((createdSkill) => {
        this.skills.update((skills) => [createdSkill, ...skills]);
      }),
      catchError((err) => {
        this.error.set(err?.message || 'Failed to create skill');
        throw err;
      })
    );
  }

  /**
   * PUT /api/skills/{id}
   *
   * Partial update — only the fields present on ``data`` are sent.
   * The backend uses ``PUT`` (not ``PATCH``) and strips ``null``
   * fields server-side, so callers can omit optional keys without
   * accidentally clearing columns.
   *
   * On success the matching row in the ``skills`` signal is replaced
   * with the unwrapped backend response (``{skill: {...}}`` envelope
   * is unwrapped here) so the list re-renders the new values without
   * a second fetch.
   *
   * Args:
   *     id: Skill UUID.
   *     data: Partial fields to update.
   *
   * Returns:
   *     Observable<Skill> — re-thrown on error.
   */
  update(id: string, data: SkillUpdate): Observable<Skill> {
    return this.http
      .put<{ skill?: Skill } | Skill>(
        `${this.API_BASE}/${encodeURIComponent(id)}`,
        data
      )
      .pipe(
        map((res: any) => (res?.skill ?? res) as Skill),
        tap((updatedSkill) => {
          this.skills.update((skills) =>
            skills.map((skill) => (skill.id === id ? updatedSkill : skill))
          );
        }),
        catchError((err) => {
          this.error.set(err?.message || 'Failed to update skill');
          throw err;
        })
      );
  }

  /**
   * DELETE /api/skills/{id}
   *
   * Server-side this is a SOFT delete — the backend flips
   * ``is_active`` to ``false`` and keeps the row (plus its usage
   * history) queryable. Returns ``{deactivated: true}`` on success.
   * Removes the skill from the local ``skills`` signal so the list
   * re-renders without a refetch.
   *
   * Args:
   *     id: Skill UUID.
   *
   * Returns:
   *     ``Observable<{deactivated: boolean}>`` — re-thrown on error.
   */
  delete(id: string): Observable<DeactivationResponse> {
    return this.http
      .delete<DeactivationResponse>(
        `${this.API_BASE}/${encodeURIComponent(id)}`
      )
      .pipe(
        tap(() => {
          this.skills.update((skills) => skills.filter((s) => s.id !== id));
        }),
        catchError((err) => {
          this.error.set(err?.message || 'Failed to delete skill');
          throw err;
        })
      );
  }

  /**
   * POST /api/skills/{id}/deactivate
   *
   * Soft-delete alias of the DELETE route — flips ``is_active`` to
   * ``false`` server-side and returns ``{deactivated: true}``.
   *
   * Because the response does not include the refreshed ``Skill``
   * row, we update the local list to flip ``is_active=false`` for
   * the matching id instead of swapping in a server payload.
   *
   * Args:
   *     id: Skill UUID.
   *
   * Returns:
   *     ``Observable<{deactivated: boolean}>`` — re-thrown on error.
   */
  deactivate(id: string): Observable<DeactivationResponse> {
    return this.http
      .post<DeactivationResponse>(
        `${this.API_BASE}/${encodeURIComponent(id)}/deactivate`,
        {}
      )
      .pipe(
        tap(() => {
          // No row payload to splice in — flip the local flag so the
          // list re-renders correctly without a refetch.
          this.skills.update((skills) =>
            skills.map((skill) =>
              skill.id === id ? { ...skill, is_active: false } : skill,
            ),
          );
        }),
        catchError((err) => {
          this.error.set(err?.message || 'Failed to deactivate skill');
          throw err;
        })
      );
  }

  /**
   * POST /api/skills/search
   *
   * Two-bucket search used by the Skills page search panel. Both
   * ``query`` (required) and ``projectId`` are sent in the request
   * body so the search route does not have to special-case empty
   * filter values the way the list endpoint does.
   *
   * The backend returns ``{injected: [{skill, score}],
   * low_match: [{skill, score}]}``. We normalise each item to the
   * ``{skill, score}`` shape so the components can render either
   * bucket uniformly without per-bucket unwrap logic.
   *
   * Args:
   *     query: Free-text search string.
   *     projectId: Optional project scope; ``undefined`` / ``null``
   *         is stripped so the body never carries a ``null`` token
   *         the backend would have to skip.
   *
   * Returns:
   *     Observable<SearchResults> — re-thrown on error.
   */
  search(query: string, projectId?: string): Observable<SearchResults> {
    // Build the body explicitly so omitted keys never serialise as
    // ``null`` / ``undefined`` in the request payload — mirrors how
    // ``WorkService`` strips empty ``project_id`` from query strings.
    const body: { query: string; project_id?: string } = { query };
    if (projectId) {
      body.project_id = projectId;
    }

    return this.http.post<any>('/api/skills/search', body).pipe(
      map((res: any) => ({
        injected: (res?.injected || []).map((x: any) => ({
          skill: x.skill ?? x,
          score: x.score ?? 0,
        })),
        low_match: (res?.low_match || []).map((x: any) => ({
          skill: x.skill ?? x,
          score: x.score ?? 0,
        })),
      })),
      catchError((err) => {
        this.error.set(err?.message || 'Failed to search skills');
        throw err;
      })
    );
  }

  /**
   * GET /api/skills/{id}/metrics
   *
   * Returns the pre-computed metrics bundle (raw counters + the
   * derived ``completion_rate`` / ``fallback_rate`` / ``applied_rate``
   * ratios). Used by the detail page's analytics card; the list page
   * reads only the raw ``Skill`` counters, so this call is pay-as-you-
   * use for the detail view.
   *
   * Args:
   *     id: Skill UUID.
   *
   * Returns:
   *     Observable<SkillMetrics> — re-thrown on error.
   */
  getMetrics(id: string): Observable<SkillMetrics> {
    return this.http
      .get<SkillMetrics>(
        `${this.API_BASE}/${encodeURIComponent(id)}/metrics`
      )
      .pipe(
        catchError((err) => {
          this.error.set(err?.message || 'Failed to fetch skill metrics');
          throw err;
        })
      );
  }

  /**
   * GET /api/skills/{id}/lineage
   *
   * Returns the skinny lineage view (parents, children, generation,
   * origin). Distinct from ``SkillDetail.lineage`` because the detail
   * endpoint embeds the same parents / children but does not re-expose
   * ``generation`` / ``origin`` on the response object itself.
   *
   * Args:
   *     id: Skill UUID.
   *
   * Returns:
   *     Observable<SkillLineage> — re-thrown on error.
   */
  getLineage(id: string): Observable<SkillLineage> {
    return this.http
      .get<SkillLineage>(
        `${this.API_BASE}/${encodeURIComponent(id)}/lineage`
      )
      .pipe(
        catchError((err) => {
          this.error.set(err?.message || 'Failed to fetch skill lineage');
          throw err;
        })
      );
  }

  /**
   * POST /api/skills/{id}/feedback?instance_id=...&agent_id=...
   *
   * Records a thumbs-up / thumbs-down signal (plus optional note) for
   * a specific selection. Body fields are ``applied`` (was
   * ``was_helpful``) and ``note`` (was ``feedback_text``) — see
   * :class:`~daemon.routers.skill_schemas.SkillFeedbackRequest`.
   *
   * ``instance_id`` and ``agent_id`` are surfaced by the backend as
   * Query parameters (originating context, not caller input). When
   * the caller does not have them (e.g. the Skills detail page
   * loaded directly from a deep link), omit them — the backend
   * treats both as optional for the public surface.
   *
   * Args:
   *     id: Skill UUID.
   *     data: SkillFeedback payload (applied, note).
   *     instanceId: Optional originating instance id (query param).
   *     agentId: Optional originating agent id (query param).
   *
   * Returns:
   *     ``Observable<void>`` — re-thrown on error. The backend
   *     returns ``{recorded: bool}``; we drop the envelope and
   *     complete empty so callers do not have to deal with the
   *     success-marker shape.
   */
  submitFeedback(
    id: string,
    data: SkillFeedback,
    instanceId?: string,
    agentId?: string,
  ): Observable<void> {
    let params = new HttpParams();
    if (instanceId) {
      params = params.set('instance_id', instanceId);
    }
    if (agentId) {
      params = params.set('agent_id', agentId);
    }

    return this.http
      .post<{ recorded?: boolean } | null>(
        `${this.API_BASE}/${encodeURIComponent(id)}/feedback`,
        data,
        { params }
      )
      .pipe(
        map(() => undefined),
        catchError((err) => {
          this.error.set(err?.message || 'Failed to submit skill feedback');
          throw err;
        })
      );
  }

  /**
   * POST /api/skills/{id}/fix
   *
   * Asks the backend to schedule a regenerator run that rewrites the
   * skill based on the supplied issue description (and optional
   * suggested fix). Body fields are ``issue_description`` (was
   * ``problem_description``) and ``suggested_fix`` (was
   * ``example_failure``) — see
   * :class:`~daemon.routers.skill_schemas.SkillFixRequest`.
   *
   * The backend dispatches a ``FIX`` evolution job and returns
   * ``202 Accepted`` with ``{job_id}``. Callers should poll the job
   * queue (``/api/jobs/{job_id}``) for completion rather than
   * blocking the UI on the LLM call.
   *
   * Args:
   *     id: Skill UUID.
   *     data: SkillFixRequest payload.
   *
   * Returns:
   *     ``Observable<{job_id: string}>`` — re-thrown on error.
   */
  requestFix(id: string, data: SkillFixRequest): Observable<SkillFixResponse> {
    return this.http
      .post<SkillFixResponse>(
        `${this.API_BASE}/${encodeURIComponent(id)}/fix`,
        data
      )
      .pipe(
        catchError((err) => {
          this.error.set(err?.message || 'Failed to request skill fix');
          throw err;
        })
      );
  }

  /**
   * GET /api/skills/{id}/ab-test
   *
   * Returns the skill's current A/B test state. The backend yields
   * ``null`` when the skill is not part of an active test, so the
   * observable is typed as ``AbTestStatus | null`` to make the empty
   * case explicit at the call site.
   *
   * Args:
   *     id: Skill UUID.
   *
   * Returns:
   *     Observable<AbTestStatus | null> — re-thrown on error.
   */
  getAbTestStatus(id: string): Observable<AbTestStatus | null> {
    return this.http
      .get<AbTestStatus | null>(
        `${this.API_BASE}/${encodeURIComponent(id)}/ab-test`
      )
      .pipe(
        catchError((err) => {
          this.error.set(err?.message || 'Failed to fetch A/B test status');
          throw err;
        })
      );
  }

  /**
   * POST /api/skills/{id}/ab-test/resolve
   *
   * Closes the skill's A/B test by letting the backend pick the
   * winner server-side (no body on the request). The backend returns
   * ``{skill_id, ab_test_group, resolved, winner_id, loser_id,
   * reason, extension_count}`` — see ``AbTestResolveResponse``.
   *
   * The status chip on the matching row in ``skills`` is updated
   * locally so the list re-renders without a refetch; we do not
   * replace the whole row because the response payload is a
   * resolution record, not the refreshed ``Skill``.
   *
   * Args:
   *     id: Skill UUID of the A/B test anchor.
   *
   * Returns:
   *     ``Observable<AbTestResolveResponse>`` — re-thrown on error.
   */
  resolveAbTest(id: string): Observable<AbTestResolveResponse> {
    return this.http
      .post<AbTestResolveResponse>(
        `${this.API_BASE}/${encodeURIComponent(id)}/ab-test/resolve`,
        {}
      )
      .pipe(
        tap((result) => {
          // Reflect resolution locally — the row's status flips
          // away from ``ab_testing``. We do not splice a server
          // payload in because the resolution record does not
          // carry the full refreshed ``Skill``.
          this.skills.update((skills) =>
            skills.map((skill) =>
              skill.id === id && result.resolved
                ? { ...skill, status: 'active' }
                : skill,
            ),
          );
        }),
        catchError((err) => {
          this.error.set(err?.message || 'Failed to resolve A/B test');
          throw err;
        })
      );
  }

  /**
   * POST /api/skills/{id}/share
   *
   * Promotes a project-local skill to the global / shared scope by
   * setting ``project_id`` to ``null``. Returns the refreshed row
   * wrapped in ``{skill: {...}}`` — we unwrap the envelope here and
   * replace the matching row in the local ``skills`` signal so the
   * list re-renders without a refetch.
   *
   * Args:
   *     id: Skill UUID.
   *
   * Returns:
   *     Observable<Skill> — re-thrown on error.
   */
  shareToGlobal(id: string): Observable<Skill> {
    return this.http
      .post<{ skill?: Skill } | Skill>(
        `${this.API_BASE}/${encodeURIComponent(id)}/share`,
        {}
      )
      .pipe(
        map((res: any) => (res?.skill ?? res) as Skill),
        tap((updatedSkill) => {
          this.skills.update((skills) =>
            skills.map((skill) => (skill.id === id ? updatedSkill : skill))
          );
        }),
        catchError((err) => {
          this.error.set(err?.message || 'Failed to share skill globally');
          throw err;
        })
      );
  }

  /**
   * Helper to refresh the skills list while keeping the loading
   * state surface aligned with ``JobService.refreshJobs`` /
   * ``WorkService.refreshWork``.
   *
   * Args:
   *     filters: Optional filters forwarded to ``list``.
   */
  refreshSkills(filters?: SkillFilters): void {
    this.loading.set(true);
    this.list(filters).subscribe({
      next: () => this.loading.set(false),
      error: () => this.loading.set(false),
    });
  }

  /**
   * Helper to clear the ``error`` signal — mirrors
   * ``WorkService.clearError`` / ``JobService.clearError``.
   */
  clearError(): void {
    this.error.set(null);
  }
}
