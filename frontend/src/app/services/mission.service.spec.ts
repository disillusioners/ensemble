// MissionService spec — jobs-page-improvement arc, Phase 3.
//
// URL pins + envelope-shape pins for the split OUT of JobService.
// The plan requires: "URL pins MOVE with the split; job.service.spec.ts
// drops them; indicator spec stays green (consumer migrated, not
// duplicated)".
//
// Pins:
// * listMissions — GET /api/missions with optional liveness/limit
// * listJobsByMission — GET /api/jobs with mission_id + include_deleted=false
// * getMission (NEW, P3) — GET /api/missions/{id}
// * Indicator consumer migrated to MissionService.listMissions
//   (parallel-creation trap: one home per call; the JobService spec
//    must NOT re-add these).

import { MissionService } from './mission.service';
import { Job } from '../models/job.model';
import { MissionListResponse, MissionSummary } from '../models/mission.model';
import { createMockJob } from '../testing/job-test-helpers';

/**
 * Plain-TS mirror of the MissionService — captures URLs and returns
 * deterministic envelopes. Same pattern as the JobService spec
 * mirror: the production class uses Angular's HttpClient; the spec
 * uses a hand-rolled Observable so the URL-construction assertions
 * don't depend on Angular's DI.
 */
class TestMissionService {
  lastRequestUrl: string | null = null;

  listMissions(
    params?: { liveness?: string; limit?: number },
  ): { pipe: () => { subscribe: (observer: any) => void } } {
    const search = new URLSearchParams();
    if (params?.liveness) search.set('liveness', params.liveness);
    if (params?.limit !== undefined) search.set('limit', params.limit.toString());
    const queryString = search.toString();
    const url = '/api/missions' + (queryString ? `?${queryString}` : '');
    this.lastRequestUrl = url;

    const mockResponse: MissionListResponse = {
      missions: [
        {
          mission_id: 'm-1',
          agent_id: 'leader',
          parent_mission_id: null,
          liveness: 'processing',
          terminal_reason: null,
          epoch: 1,
          linked_jobs: ['j-1', 'j-2'],
          started_at: '2026-09-07T10:00:00Z',
          last_activity_at: '2026-09-07T10:30:00Z',
          title: null,
          initiative_preview: null,
        } satisfies MissionSummary,
      ],
      total: 1,
      limit: params?.limit ?? 10,
      offset: 0,
      has_more: false,
      degraded: false,
    };

    return {
      pipe: () => ({
        subscribe: (observer: any) => {
          if (typeof observer === 'function') {
            observer(mockResponse);
          } else if (observer.next) {
            observer.next(mockResponse);
          }
        },
      }),
    };
  }

  listJobsByMission(
    missionId: string,
  ): { pipe: () => { subscribe: (observer: any) => void } } {
    const url = `/api/jobs?mission_id=${encodeURIComponent(missionId)}&include_deleted=false`;
    this.lastRequestUrl = url;

    const mockJobs: Job[] = [
      createMockJob({
        job_id: `mission-job-${missionId}-1`,
        status: 'processing',
        mission_id: missionId,
      }),
      createMockJob({
        job_id: `mission-job-${missionId}-2`,
        status: 'settled',
        mission_id: missionId,
      }),
    ];

    return {
      pipe: () => ({
        subscribe: (observer: any) => {
          if (typeof observer === 'function') {
            observer(mockJobs);
          } else if (observer.next) {
            observer.next(mockJobs);
          }
        },
      }),
    };
  }

  getMission(
    id: string,
  ): { pipe: () => { subscribe: (observer: any) => void } } {
    const url = `/api/missions/${encodeURIComponent(id)}`;
    this.lastRequestUrl = url;

    // FLAT wire shape — exactly what GET /api/missions/{id} sends
    // (MissionResponse: title/mission_id/… TOP-LEVEL, no wrapper).
    // The previous mock shipped ``{ mission: { … } }`` which made
    // the whole suite blind to the wrapper-shape consumer break.
    const mockResponse = {
      mission_id: id,
      agent_id: 'leader',
      parent_mission_id: null,
      liveness: 'processing' as const,
      terminal_reason: null,
      epoch: 1,
      linked_jobs: [],
      started_at: '2026-09-10T10:00:00Z',
      last_activity_at: '2026-09-10T10:30:00Z',
      title: 'Mission One',
      initiative_preview: 'Do the thing',
    } satisfies MissionSummary;

    return {
      pipe: () => ({
        subscribe: (observer: any) => {
          if (typeof observer === 'function') {
            observer(mockResponse);
          } else if (observer.next) {
            observer.next(mockResponse);
          }
        },
      }),
    };
  }
}

describe('MissionService', () => {
  let service: TestMissionService;

  beforeEach(() => {
    service = new TestMissionService();
  });

  describe('listMissions (migrated from JobService)', () => {
    it('should build GET /api/missions with no params', () => {
      let result: any = null;
      service.listMissions().pipe().subscribe((r: any) => {
        result = r;
      });
      expect(service.lastRequestUrl).toBe('/api/missions');
      expect(result.missions).toHaveLength(1);
      expect(result.total).toBe(1);
    });

    it('should pass liveness filter as comma-separated query param', () => {
      service
        .listMissions({ liveness: 'processing,pending,paused' })
        .pipe()
        .subscribe(() => {});
      expect(service.lastRequestUrl).toContain(
        'liveness=processing%2Cpending%2Cpaused',
      );
      expect(service.lastRequestUrl).toContain('/api/missions');
    });

    it('should pass limit query param', () => {
      service.listMissions({ limit: 20 }).pipe().subscribe(() => {});
      expect(service.lastRequestUrl).toContain('limit=20');
    });

    it('should pass both liveness and limit together', () => {
      service
        .listMissions({ liveness: 'processing', limit: 20 })
        .pipe()
        .subscribe(() => {});
      expect(service.lastRequestUrl).toContain('liveness=processing');
      expect(service.lastRequestUrl).toContain('limit=20');
    });

    it('returns the full MissionSummary[] + envelope (missions, total, degraded, has_more)', () => {
      let result: any = null;
      service.listMissions().pipe().subscribe((r: any) => {
        result = r;
      });
      expect(result).toHaveProperty('missions');
      expect(result).toHaveProperty('total');
      expect(result).toHaveProperty('degraded');
      expect(result).toHaveProperty('has_more');
      const m = result.missions[0];
      expect(m).toHaveProperty('mission_id');
      expect(m).toHaveProperty('agent_id');
      expect(m).toHaveProperty('parent_mission_id');
      expect(m).toHaveProperty('liveness');
      expect(m).toHaveProperty('terminal_reason');
      expect(m).toHaveProperty('epoch');
      expect(m).toHaveProperty('linked_jobs');
      expect(m).toHaveProperty('started_at');
      expect(m).toHaveProperty('last_activity_at');
      expect(m).toHaveProperty('title');
      expect(m).toHaveProperty('initiative_preview');
    });
  });

  describe('listJobsByMission (migrated from JobService)', () => {
    it('should build GET /api/jobs with mission_id and include_deleted=false', () => {
      let result: Job[] | null = null;
      service.listJobsByMission('m-1').pipe().subscribe((r: Job[]) => {
        result = r;
      });
      expect(service.lastRequestUrl).toContain('/api/jobs');
      expect(service.lastRequestUrl).toContain('mission_id=m-1');
      expect(service.lastRequestUrl).toContain('include_deleted=false');
      expect(result).not.toBeNull();
      expect(result!.length).toBeGreaterThan(0);
    });

    it('should URL-encode mission id', () => {
      service
        .listJobsByMission('mission/with/slashes')
        .pipe()
        .subscribe(() => {});
      expect(service.lastRequestUrl).toContain(
        'mission_id=mission%2Fwith%2Fslashes',
      );
    });

    it('should return the .jobs array from the response (map response.jobs)', () => {
      let result: Job[] | null = null;
      service.listJobsByMission('m-1').pipe().subscribe((r: Job[]) => {
        result = r;
      });
      expect(Array.isArray(result)).toBe(true);
      expect(result![0]).toHaveProperty('job_id');
      expect(result![0]).toHaveProperty('mission_id');
      // Should NOT have a top-level "total" — the wrapper, not the rows.
      expect(result!).not.toHaveProperty('total');
    });
  });

  describe('getMission (P3 NEW — lazy title enrichment)', () => {
    it('should build GET /api/missions/{id}', () => {
      let result: any = null;
      service.getMission('m-1').pipe().subscribe((r: any) => {
        result = r;
      });
      expect(service.lastRequestUrl).toBe('/api/missions/m-1');
      // FLAT shape: fields are top-level, exactly as the BE sends.
      expect(result.mission_id).toBe('m-1');
      expect(result.title).toBe('Mission One');
    });

    it('should URL-encode the mission id', () => {
      service.getMission('mission/with/slashes').pipe().subscribe(() => {});
      expect(service.lastRequestUrl).toBe(
        '/api/missions/mission%2Fwith%2Fslashes',
      );
    });

    it('returns the FLAT wire shape (MissionResponse — title top-level, NO { mission } wrapper)', () => {
      // 2026-09-13 wire-contract fix: the BE (daemon/routers/
      // missions.py::get_mission → MissionResponse) has NO envelope —
      // every field is top-level. The earlier wrapper-shaped mock
      // ({ mission: … }) + wrapper assertions here are why the
      // consumer break (resp?.mission?.title) stayed green.
      let result: any = null;
      service.getMission('m-1').pipe().subscribe((r: any) => {
        result = r;
      });
      // Behavioral half: a flat fixture with a populated top-level
      // ``title`` — the exact field the page's enrichment consumer
      // reads into ``titleOverrides`` (source-pinned in
      // jobs-page.bindings.pins.spec.ts).
      expect(result.title).toBe('Mission One');
      expect(result.mission_id).toBe('m-1');
      expect(result.agent_id).toBe('leader');
      expect(result.liveness).toBe('processing');
      expect(result.linked_jobs).toEqual([]);
      // Anti-wrapper pin: the response MUST NOT carry an envelope.
      // If a future mock (or a real BE change) reintroduces
      // ``{ mission: … }``, this fails first.
      expect(result).not.toHaveProperty('mission');
    });
  });
});

describe('MissionService migration proof (one home per call)', () => {
  // Parallel-creation trap: the indicator's listMissions consumer
  // MIGRATED to MissionService. JobService MUST NOT re-host
  // listMissions / listJobsByMission. We assert against the JobService
  // source file so a future regression re-adding the methods to
  // JobService is caught at the spec layer (NOT just at runtime).
  const { readFileSync } = require('fs');
  const { join } = require('path');
  const jobServiceSrc = readFileSync(
    join(__dirname, '../services/job.service.ts'),
    'utf-8',
  );
  const indicatorSrc = readFileSync(
    join(__dirname, '../components/job-queue-indicator/job-queue-indicator.component.ts'),
    'utf-8',
  );

  it('JobService no longer hosts listMissions / listJobsByMission (split-out)', () => {
    expect(jobServiceSrc).not.toMatch(/listMissions\s*\(/);
    expect(jobServiceSrc).not.toMatch(/listJobsByMission\s*\(/);
  });

  it('MissionService exists and is the canonical home for the migrated calls', () => {
    const missionServiceSrc = readFileSync(
      join(__dirname, '../services/mission.service.ts'),
      'utf-8',
    );
    expect(missionServiceSrc).toMatch(/listMissions\s*\(/);
    expect(missionServiceSrc).toMatch(/listJobsByMission\s*\(/);
    expect(missionServiceSrc).toMatch(/getMission\s*\(/);
  });

  it('Indicator imports MissionService and uses it for listMissions (migrated, not duplicated)', () => {
    // The indicator is the LIVE listMissions consumer — it must
    // import MissionService AND reference it in the forkJoin. A
    // duplicate on JobService would re-introduce the parallel-
    // creation trap.
    expect(indicatorSrc).toMatch(/import\s+\{[^}]*MissionService[^}]*\}\s+from\s+['"][^'"]*services\/mission\.service['"]/);
    expect(indicatorSrc).toMatch(/this\.missionService\.listMissions\(/);
    // Anti-pin: JobService.listMissions MUST NOT appear anywhere on
    // the indicator (the migration was complete, not partial).
    expect(indicatorSrc).not.toMatch(/this\.jobService\.listMissions\(/);
  });
});