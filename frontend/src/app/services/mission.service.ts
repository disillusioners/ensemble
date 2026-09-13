// Missions service — jobs-page-improvement arc, Phase 3.
//
// Mission-tree panel (2026-09-07, ``feature/job-queue-mission-tree``)
// and the jobs page's P3 group-title enrichment (2026-09-10) are
// the only consumers of the ``/api/missions`` endpoints. The
// endpoints split OUT of ``JobService`` for the P3 grouping work so
// each service owns ONE concept (parallel-creation trap: one home
// per call, the indicator's ``listMissions`` consumer MIGRATES to
// this service, NOT a duplicate).
//
// Endpoints exposed (per ``daemon/routers/missions.py``):
//
// * ``listMissions({ liveness?, limit? })`` — ``GET /api/missions``;
//   the badge's LEG A (filter-aware total) and the panel's full live
//   row source. ``liveness`` is the canonical mission-liveness
//   cluster (pending/processing/paused/completed/failed/cancelled);
//   ``limit`` BE clamps to ``[1, MAX_PAGE_LIMIT]``.
//
// * ``listJobsByMission(missionId)`` — ``GET /api/jobs?mission_id=
//   <id>&include_deleted=false``; deliberately UNWIRED on the FE
//   today (kept per the leader's review decision, 2026-09-07).
//
// * ``getMission(id)`` (NEW, P3) — ``GET /api/missions/{id}``; the
//   jobs page's lazy group-title enrichment. Errors propagate so
//   the page's retain-last-data discipline keeps the fallback title
//   when the enrichment fails (plan task 4).

import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, map } from 'rxjs';
import { Job } from '../models/job.model';
import { MissionListResponse, MissionSummary } from '../models/mission.model';

interface JobListResponse {
  jobs: Job[];
  total: number;
}

/**
 * FLAT wire contract (fixed 2026-09-13): ``GET /api/missions/{id}``
 * returns ``MissionResponse`` with every field TOP-LEVEL (``title``,
 * ``mission_id``, ``agent_id``, …) — there is NO ``{ mission: … }``
 * wrapper on the wire (see ``daemon/routers/schemas.py`` class
 * ``MissionResponse`` and ``daemon/routers/missions.py::get_mission``).
 * The service therefore returns the flat ``MissionSummary`` directly.
 * History: P3 initially typed this as a ``{ mission: MissionSummary }``
 * wrapper while the consumer read ``resp?.mission?.title`` — the
 * enrichment "succeeded" (200) but read ``undefined`` and every titled
 * group fell back to the ``agent · Xm ago`` header. Pinned flat by the
 * anti-wrapper pins in ``jobs-page.bindings.pins.spec.ts`` +
 * ``mission.service.spec.ts``.
 */

@Injectable({
  providedIn: 'root',
})
export class MissionService {
  private readonly http = inject(HttpClient);
  private readonly API_BASE = '/api/missions';

  /**
   * GET /api/missions with optional ``liveness`` filter and ``limit``
   * (BE clamps to ``[1, MAX_PAGE_LIMIT]``; default
   * ``DEFAULT_PAGE_LIMIT`` = 10; the badge calls with ``limit=20``
   * for a richer breakdown).
   *
   * Returns the full ``MissionSummary[]`` + ``total`` from the
   * envelope. ``null`` total ⇒ count leg degraded (NOT 0); callers
   * must use ``missionCountFromListResponse`` for the badge's N leg.
   *
   * Errors propagate so per-participant ``catchError`` in the
   * indicator's ``forkJoin`` degrades the leg to ``null`` without
   * killing the other legs on the same tick.
   */
  listMissions(
    params?: { liveness?: string; limit?: number },
  ): Observable<MissionListResponse> {
    let httpParams = new HttpParams();
    if (params?.liveness) httpParams = httpParams.set('liveness', params.liveness);
    if (params?.limit !== undefined) {
      httpParams = httpParams.set('limit', params.limit.toString());
    }
    return this.http.get<MissionListResponse>(this.API_BASE, {
      params: httpParams,
    });
  }

  /**
   * GET /api/jobs?mission_id=<id>&include_deleted=false — fetch the
   * jobs attached to a given mission.
   *
   * ``include_deleted=false`` keeps the consumer view clean —
   * soft-deleted jobs are filtered out, matching the rest of the
   * panel's surface. Returns the raw ``Job[]`` array (mapped from
   * the response envelope so callers get rows, not the wrapper).
   *
   * Status: deliberately UNWIRED on the FE today (kept per the
   * leader's review decision, 2026-09-07). The page's grouping work
   * uses the existing per-row ``mission_id`` payload + client-side
   * coalesced key (``mission_id ?? instance_id``) so it does NOT
   * need a per-mission round-trip. The method is here so the future
   * consumer doesn't have to re-add it (and so this comment stops
   * people reading the unwired surface as accidental dead code).
   *
   * Errors propagate so a future per-mission fetch can route through
   * the same per-participant ``catchError`` isolation the badge
   * already uses.
   */
  listJobsByMission(missionId: string): Observable<Job[]> {
    const params = new HttpParams()
      .set('mission_id', missionId)
      .set('include_deleted', 'false');
    return this.http
      .get<JobListResponse>('/api/jobs', { params })
      .pipe(map((response) => response.jobs));
  }

  /**
   * GET /api/missions/{id} — single-mission lookup, P3 lazy title
   * enrichment for visible group headers only. The page calls this
   * for at most ``MAX_TITLE_ENRICHMENT_FETCHES`` (3) groups per
   * render tick; failures keep the fallback title rendered
   * (retain-last-data discipline — the panel pattern).
   *
   * FLAT wire contract: the BE returns ``MissionResponse`` with
   * ``title`` (and every other field) TOP-LEVEL — no
   * ``{ mission: … }`` wrapper. Consumers read ``resp?.title``.
   *
   * Errors propagate so the page's per-fetch ``catchError` returns
   * ``null`` for the failed key without disturbing the others.
   */
  getMission(id: string): Observable<MissionSummary> {
    return this.http.get<MissionSummary>(
      `${this.API_BASE}/${encodeURIComponent(id)}`,
    );
  }
}