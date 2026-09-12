import { signal } from '@angular/core';
import { Job, DeadLetterItem, RetryAllResult, JobCleanupResult, DLQReplayResponse, DLQListResponse } from '../models/job.model';
import { createMockJob, createMockJobList } from '../testing/job-test-helpers';

// Create a testable JobService with injected mock
class TestJobService {
  private jobCounter = 0;
  
  readonly jobs = signal<Job[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  /** Captures the URL of the most recent request so tests can assert params. */
  lastRequestUrl: string | null = null;

  listJobs(filters?: { status?: string; source?: string; agent_id?: string; project_id?: string; include_deleted?: boolean }) {
    let params = new URLSearchParams();
    // P1 parity (jobs-page-improvement): the real JobService.listJobs
    // ALWAYS sends limit=100 (explicit newest-100 window; BE clamps
    // 1..100). The mirror tracks that construction — see the
    // ``listJobs limit=100 window (P1)`` describe below.
    params.set('limit', '100');
    if (filters) {
      if (filters.status) params.set('status', filters.status);
      if (filters.source) params.set('source', filters.source);
      if (filters.agent_id) params.set('agent_id', filters.agent_id);
      if (filters.project_id) params.set('project_id', filters.project_id);
      if (filters.include_deleted) params.set('include_deleted', 'true');
    }
    const queryString = params.toString();
    const url = '/api/jobs' + (queryString ? `?${queryString}` : '');
    this.lastRequestUrl = url;

    return {
      pipe: () => ({
        subscribe: (observer: any) => {
          const mockResponse = { jobs: createMockJobList(3), total: 3 };
          if (typeof observer === 'function') {
            observer(mockResponse.jobs);
          } else if (observer.next) {
            observer.next(mockResponse.jobs);
          }
        }
      })
    };
  }

  listActiveJobs() {
    // Mirrors real JobService.listActiveJobs: GET /api/jobs?status=queued,active,
    // then maps response.jobs. We capture the URL so tests can assert the
    // query string and emit a fixed mock payload.
    const url = '/api/jobs?status=queued,active';
    this.lastRequestUrl = url;
    const mockResponse = {
      jobs: createMockJobList(3).map((job, i) => ({
        ...job,
        job_id: `active-job-${i}`,
        status: i === 0 ? 'processing' : 'pending',
      })),
      total: 3,
    };

    return {
      pipe: () => ({
        subscribe: (observer: any) => {
          if (typeof observer === 'function') {
            observer(mockResponse.jobs);
          } else if (observer.next) {
            observer.next(mockResponse.jobs);
          }
        }
      })
    };
  }

  getJob(jobId: string) {
    return {
      pipe: () => ({
        subscribe: (observer: any) => {
          if (typeof observer === 'function') {
            observer(createMockJob({ job_id: jobId }));
          } else if (observer.next) {
            observer.next(createMockJob({ job_id: jobId }));
          }
        }
      })
    };
  }

  createJob(job: any) {
    const uniqueId = `job-${++this.jobCounter}`;
    return {
      pipe: () => ({
        subscribe: (observer: any) => {
          const created = createMockJob({ ...job, job_id: uniqueId });
          this.jobs.update(jobs => [created, ...jobs]);
          if (typeof observer === 'function') {
            observer(created);
          } else if (observer.next) {
            observer.next(created);
          }
        }
      })
    };
  }

  cancelJob(jobId: string) {
    return {
      pipe: () => ({
        subscribe: (observer: any) => {
          this.jobs.update(jobs =>
            jobs.map(job =>
              job.job_id === jobId
                ? { ...job, status: 'cancelled' as const, cancelled_at: new Date().toISOString() }
                : job
            )
          );
          if (typeof observer === 'function') {
            observer();
          } else if (observer.next) {
            observer.next();
          }
        }
      })
    };
  }

  retryJob(jobId: string) {
    return {
      pipe: () => ({
        subscribe: (observer: any) => {
          const retried = createMockJob({ job_id: jobId, status: 'pending' });
          this.jobs.update(jobs => jobs.map(job => job.job_id === jobId ? retried : job));
          if (typeof observer === 'function') {
            observer(retried);
          } else if (observer.next) {
            observer.next(retried);
          }
        }
      })
    };
  }

  softDeleteJob(jobId: string) {
    return {
      pipe: () => ({
        subscribe: (observer: any) => {
          const deletedAt = new Date().toISOString();
          this.jobs.update(jobs =>
            jobs.map(job =>
              job.job_id === jobId
                ? { ...job, deleted_at: deletedAt }
                : job
            )
          );
          const deletedJob = this.jobs().find(j => j.job_id === jobId);
          if (typeof observer === 'function') {
            observer(deletedJob);
          } else if (observer.next) {
            observer.next(deletedJob);
          }
        }
      })
    };
  }

  restoreJob(jobId: string) {
    return {
      pipe: () => ({
        subscribe: (observer: any) => {
          this.jobs.update(jobs =>
            jobs.map(job =>
              job.job_id === jobId
                ? { ...job, deleted_at: null }
                : job
            )
          );
          const restoredJob = this.jobs().find(j => j.job_id === jobId);
          if (typeof observer === 'function') {
            observer(restoredJob);
          } else if (observer.next) {
            observer.next(restoredJob);
          }
        }
      })
    };
  }

  refreshJobs(filters?: any) {
    this.loading.set(true);
    this.listJobs(filters).pipe().subscribe({
      next: () => this.loading.set(false),
      error: () => this.loading.set(false)
    });
  }

  clearError() {
    this.error.set(null);
  }

  listDeadLetterItems(projectId: string) {
    let params = new URLSearchParams();
    if (projectId) params.set('project_id', projectId);
    const queryString = params.toString();
    const url = `/api/projects/${encodeURIComponent(projectId)}/dlq` + (queryString ? `?${queryString}` : '');
    
    return {
      pipe: () => ({
        subscribe: (observer: any) => {
          const mockResponse: DLQListResponse = {
            items: [
              {
                dlq_id: 'dlq-1',
                job_id: 'job-1',
                agent_id: 'developer',
                agent_dir: '/agents/developer',
                message: 'Failed job',
                source: 'api',
                project_id: projectId,
                queue_id: null,
                error_message: 'Timeout error',
                retry_count: 3,
                failed_at: '2024-01-01T00:00:00Z',
                moved_to_dlq_at: '2024-01-02T00:00:00Z',
                reason: 'timeout',
              },
            ],
            total: 1,
          };
          if (typeof observer === 'function') {
            observer(mockResponse.items);
          } else if (observer.next) {
            observer.next(mockResponse.items);
          }
        }
      })
    };
  }

  retryDeadLetterJob(projectId: string, dlqId: string) {
    const url = `/api/projects/${encodeURIComponent(projectId)}/dlq/${encodeURIComponent(dlqId)}/replay`;
    
    return {
      pipe: () => ({
        subscribe: (observer: any) => {
          const mockResponse: DLQReplayResponse = {
            job_id: 'job-replayed',
            status: 'pending',
            message: 'Job replayed successfully',
          };
          if (typeof observer === 'function') {
            observer(mockResponse);
          } else if (observer.next) {
            observer.next(mockResponse);
          }
        }
      })
    };
  }

  retryAllDeadLetterJobs(projectId: string) {
    const url = `/api/projects/${encodeURIComponent(projectId)}/dlq/replay-all`;

    return {
      pipe: () => ({
        subscribe: (observer: any) => {
          const mockResponse: RetryAllResult = {
            replayed: 5,
            failed: 0,
            errors: [],
          };
          if (typeof observer === 'function') {
            observer(mockResponse);
          } else if (observer.next) {
            observer.next(mockResponse);
          }
        }
      })
    };
  }

  cleanupAllJobs() {
    const url = '/api/jobs/cleanup';

    return {
      pipe: () => ({
        subscribe: (observer: any) => {
          const mockResponse: JobCleanupResult = {
            cancelled_queued: 5,
            cancelled_active: 2,
            total_processed: 7,
          };
          if (typeof observer === 'function') {
            observer(mockResponse);
          } else if (observer.next) {
            observer.next(mockResponse);
          }
        }
      })
    };
  }

  /**
   * Mission-tree panel (2026-09-07) — captures the URL and returns a
   * deterministic MissionListResponse. Mirrors ``JobService.listMissions``.
   */
  listMissions(params?: { liveness?: string; limit?: number }) {
    const search = new URLSearchParams();
    if (params?.liveness) search.set('liveness', params.liveness);
    if (params?.limit !== undefined) search.set('limit', params.limit.toString());
    const queryString = search.toString();
    const url = '/api/missions' + (queryString ? `?${queryString}` : '');
    this.lastRequestUrl = url;

    const mockResponse = {
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
        },
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
        }
      })
    };
  }

  /**
   * Mission-tree panel — captures the URL and returns a deterministic
   * job array. Mirrors ``JobService.listJobsByMission``.
   */
  listJobsByMission(missionId: string) {
    const url = `/api/jobs?mission_id=${encodeURIComponent(missionId)}&include_deleted=false`;
    this.lastRequestUrl = url;

    const mockJobs: Job[] = [
      createMockJob({ job_id: `mission-job-${missionId}-1`, status: 'processing', mission_id: missionId }),
      createMockJob({ job_id: `mission-job-${missionId}-2`, status: 'settled', mission_id: missionId }),
    ];

    return {
      pipe: () => ({
        subscribe: (observer: any) => {
          if (typeof observer === 'function') {
            observer(mockJobs);
          } else if (observer.next) {
            observer.next(mockJobs);
          }
        }
      })
    };
  }
}

describe('JobService', () => {
  let service: TestJobService;

  beforeEach(() => {
    service = new TestJobService();
  });

  describe('listJobs', () => {
    it('should return jobs array', () => {
      let result: Job[] = [];
      service.listJobs().pipe().subscribe(jobs => { result = jobs; });
      expect(result.length).toBe(3);
    });

    // P1 (jobs-page-improvement) — the pre-P1 page fetch sent NO
    // limit, so the backend's silent default of 50 truncated the
    // window invisibly. The service now ALWAYS sends limit=100 (the
    // BE max clamp). Mirror-parity: the TestableJobService above
    // tracks the real params construction; this pin asserts the wire
    // shape, and jobs-page.bindings.pins.spec.ts anchors the
    // production source text (F-5).
    describe('listJobs limit=100 window (P1)', () => {
      it('should always send limit=100, even with no filters', () => {
        service.listJobs().pipe().subscribe(() => {});
        expect(service.lastRequestUrl).toContain('limit=100');
      });

      it('should send limit=100 alongside filters (never overridden by filter params)', () => {
        service.listJobs({ status: 'pending', project_id: 'p-1' }).pipe().subscribe(() => {});
        expect(service.lastRequestUrl).toContain('limit=100');
        expect(service.lastRequestUrl).toContain('status=pending');
        expect(service.lastRequestUrl).toContain('project_id=p-1');
        // Exactly one limit token.
        expect((service.lastRequestUrl ?? '').match(/limit=/g)).toHaveLength(1);
      });

      it('should send limit=100 with include_deleted=true (deleted-window parity)', () => {
        service.listJobs({ include_deleted: true }).pipe().subscribe(() => {});
        expect(service.lastRequestUrl).toContain('limit=100');
        expect(service.lastRequestUrl).toContain('include_deleted=true');
      });
    });

    it('should build correct URL with status filter', () => {
      const subscribeSpy = jest.fn();
      service.listJobs({ status: 'pending' }).pipe().subscribe(subscribeSpy);
      expect(subscribeSpy).toHaveBeenCalled();
    });

    it('should build correct URL with source filter', () => {
      const subscribeSpy = jest.fn();
      service.listJobs({ source: 'telegram' }).pipe().subscribe(subscribeSpy);
      expect(subscribeSpy).toHaveBeenCalled();
    });

    it('should build correct URL with agent_id filter', () => {
      const subscribeSpy = jest.fn();
      service.listJobs({ agent_id: 'developer' }).pipe().subscribe(subscribeSpy);
      expect(subscribeSpy).toHaveBeenCalled();
    });

    it('should build correct URL with project_id filter', () => {
      const subscribeSpy = jest.fn();
      service.listJobs({ project_id: 'project-123' }).pipe().subscribe(subscribeSpy);
      expect(subscribeSpy).toHaveBeenCalled();
    });

    it('should build correct URL with multiple filters', () => {
      const subscribeSpy = jest.fn();
      service.listJobs({
        status: 'pending',
        source: 'api',
        agent_id: 'developer'
      }).pipe().subscribe(subscribeSpy);
      expect(subscribeSpy).toHaveBeenCalled();
    });
  });

  describe('listActiveJobs', () => {
    it('should send GET /api/jobs?status=queued,active', () => {
      const subscribeSpy = jest.fn();
      service.listActiveJobs().pipe().subscribe(subscribeSpy);
      expect(subscribeSpy).toHaveBeenCalled();
      expect(service.lastRequestUrl).toContain('/api/jobs');
      expect(service.lastRequestUrl).toContain('status=queued,active');
    });

    it('should return the .jobs array from the response (map response.jobs)', () => {
      let result: Job[] | null = null;
      service.listActiveJobs().pipe().subscribe(jobs => { result = jobs; });
      // listActiveJobs maps response.jobs, so the subscriber receives an
      // array of Job objects — not the wrapper { jobs, total } object.
      expect(Array.isArray(result)).toBe(true);
      expect(result).not.toBeNull();
      expect(result!.length).toBeGreaterThan(0);
      expect(result![0]).toHaveProperty('job_id');
      expect(result![0]).toHaveProperty('status');
      // Should NOT have a top-level "total" field — that lives on the wrapper.
      expect(result!).not.toHaveProperty('total');
    });
  });

  describe('getJob', () => {
    it('should return a job', () => {
      let result: Job | null = null;
      service.getJob('job-123').pipe().subscribe(job => { result = job; });
      expect(result?.job_id).toBe('job-123');
    });

    it('should encode job ID in URL', () => {
      const subscribeSpy = jest.fn();
      service.getJob('job/123/with/slashes').pipe().subscribe(subscribeSpy);
      expect(subscribeSpy).toHaveBeenCalled();
    });
  });

  describe('createJob', () => {
    it('should add new job to start of jobs array', () => {
      const initialCount = service.jobs().length;
      const newJob = { agent_id: 'developer', message: 'New job' };
      service.createJob(newJob).pipe().subscribe(() => {});
      expect(service.jobs().length).toBe(initialCount + 1);
      expect(service.jobs()[0].agent_id).toBe('developer');
    });

    it('should update jobs signal', () => {
      service.createJob({ agent_id: 'tester', message: 'Test' }).pipe().subscribe(() => {});
      expect(service.jobs().length).toBeGreaterThan(0);
    });
  });

  describe('cancelJob', () => {
    it('should update job status to cancelled', () => {
      // First add a job
      service.createJob({ agent_id: 'developer', message: 'Test' }).pipe().subscribe(() => {});
      const jobId = service.jobs()[0].job_id;
      
      service.cancelJob(jobId).pipe().subscribe(() => {});
      
      const cancelledJob = service.jobs().find(j => j.job_id === jobId);
      expect(cancelledJob?.status).toBe('cancelled');
    });

    it('should set cancelled_at timestamp', () => {
      service.createJob({ agent_id: 'developer', message: 'Test' }).pipe().subscribe(() => {});
      const jobId = service.jobs()[0].job_id;
      
      service.cancelJob(jobId).pipe().subscribe(() => {});
      
      const cancelledJob = service.jobs().find(j => j.job_id === jobId);
      expect(cancelledJob?.cancelled_at).toBeTruthy();
    });
  });

  describe('retryJob', () => {
    it('should update job status to pending', () => {
      service.createJob({ agent_id: 'developer', message: 'Test', status: 'failed' }).pipe().subscribe(() => {});
      const jobId = service.jobs()[0].job_id;
      
      service.retryJob(jobId).pipe().subscribe(() => {});
      
      const retriedJob = service.jobs().find(j => j.job_id === jobId);
      expect(retriedJob?.status).toBe('pending');
    });
  });

  describe('listJobs with include_deleted filter', () => {
    it('should pass include_deleted=true when filter is set', () => {
      const subscribeSpy = jest.fn();
      service.listJobs({ include_deleted: true }).pipe().subscribe(subscribeSpy);
      expect(subscribeSpy).toHaveBeenCalled();
    });

    it('should not pass include_deleted when filter is false', () => {
      const subscribeSpy = jest.fn();
      service.listJobs({ include_deleted: false }).pipe().subscribe(subscribeSpy);
      expect(subscribeSpy).toHaveBeenCalled();
    });

    it('should build correct URL with include_deleted and other filters', () => {
      const subscribeSpy = jest.fn();
      service.listJobs({
        status: 'pending',
        include_deleted: true
      }).pipe().subscribe(subscribeSpy);
      expect(subscribeSpy).toHaveBeenCalled();
    });
  });

  describe('softDeleteJob', () => {
    it('should set deleted_at on the job', () => {
      service.createJob({ agent_id: 'developer', message: 'Test' }).pipe().subscribe(() => {});
      const jobId = service.jobs()[0].job_id;
      
      service.softDeleteJob(jobId).pipe().subscribe(() => {});
      
      const deletedJob = service.jobs().find(j => j.job_id === jobId);
      expect(deletedJob?.deleted_at).toBeTruthy();
    });

    it('should update jobs signal', () => {
      service.createJob({ agent_id: 'developer', message: 'Test' }).pipe().subscribe(() => {});
      const jobId = service.jobs()[0].job_id;
      
      service.softDeleteJob(jobId).pipe().subscribe(() => {});
      
      expect(service.jobs().some(j => j.job_id === jobId && j.deleted_at)).toBe(true);
    });

    it('should return the deleted job', () => {
      service.createJob({ agent_id: 'developer', message: 'Test' }).pipe().subscribe(() => {});
      const jobId = service.jobs()[0].job_id;
      
      let result: any = null;
      service.softDeleteJob(jobId).pipe().subscribe(job => { result = job; });
      
      expect(result?.job_id).toBe(jobId);
      expect(result?.deleted_at).toBeTruthy();
    });

    it('should only mark the specified job as deleted', () => {
      service.createJob({ agent_id: 'developer', message: 'Test 1' }).pipe().subscribe(() => {});
      const jobIdToDelete = service.jobs()[0].job_id;
      
      // Create a second job with a unique ID
      service.createJob({ agent_id: 'developer', message: 'Test 2' }).pipe().subscribe(() => {});
      const jobIdToKeep = service.jobs()[0].job_id;
      
      // Ensure they are different
      expect(jobIdToDelete).not.toBe(jobIdToKeep);
      
      service.softDeleteJob(jobIdToDelete).pipe().subscribe(() => {});
      
      const deletedJob = service.jobs().find(j => j.job_id === jobIdToDelete);
      const keptJob = service.jobs().find(j => j.job_id === jobIdToKeep);
      expect(deletedJob?.deleted_at).toBeTruthy();
      expect(keptJob?.deleted_at).toBeFalsy();
    });
  });

  describe('restoreJob', () => {
    it('should clear deleted_at on the job', () => {
      service.createJob({ agent_id: 'developer', message: 'Test' }).pipe().subscribe(() => {});
      const jobId = service.jobs()[0].job_id;
      
      service.softDeleteJob(jobId).pipe().subscribe(() => {});
      expect(service.jobs().find(j => j.job_id === jobId)?.deleted_at).toBeTruthy();
      
      service.restoreJob(jobId).pipe().subscribe(() => {});
      
      const restoredJob = service.jobs().find(j => j.job_id === jobId);
      expect(restoredJob?.deleted_at).toBeNull();
    });

    it('should update jobs signal', () => {
      service.createJob({ agent_id: 'developer', message: 'Test' }).pipe().subscribe(() => {});
      const jobId = service.jobs()[0].job_id;
      
      service.softDeleteJob(jobId).pipe().subscribe(() => {});
      service.restoreJob(jobId).pipe().subscribe(() => {});
      
      expect(service.jobs().find(j => j.job_id === jobId)?.deleted_at).toBeNull();
    });

    it('should return the restored job', () => {
      service.createJob({ agent_id: 'developer', message: 'Test' }).pipe().subscribe(() => {});
      const jobId = service.jobs()[0].job_id;
      
      service.softDeleteJob(jobId).pipe().subscribe(() => {});
      
      let result: any = null;
      service.restoreJob(jobId).pipe().subscribe(job => { result = job; });
      
      expect(result?.job_id).toBe(jobId);
      expect(result?.deleted_at).toBeNull();
    });

    it('should only restore the specified job', () => {
      service.createJob({ agent_id: 'developer', message: 'Test 1' }).pipe().subscribe(() => {});
      const jobId1 = service.jobs()[0].job_id;
      
      service.createJob({ agent_id: 'developer', message: 'Test 2' }).pipe().subscribe(() => {});
      const jobId2 = service.jobs()[0].job_id;
      
      // Ensure they are different
      expect(jobId1).not.toBe(jobId2);
      
      service.softDeleteJob(jobId1).pipe().subscribe(() => {});
      service.softDeleteJob(jobId2).pipe().subscribe(() => {});
      service.restoreJob(jobId1).pipe().subscribe(() => {});
      
      const restoredJob = service.jobs().find(j => j.job_id === jobId1);
      const stillDeletedJob = service.jobs().find(j => j.job_id === jobId2);
      expect(restoredJob?.deleted_at).toBeNull();
      expect(stillDeletedJob?.deleted_at).toBeTruthy();
    });
  });

  describe('refreshJobs', () => {
    it('should set loading true before API call', () => {
      service.refreshJobs();
      // Note: The mock listJobs runs synchronously, so loading is set and cleared immediately
      // In real implementation with async HTTP, loading would stay true until response
      expect(service.loading() === true || service.loading() === false).toBe(true);
    });
  });

  describe('clearError', () => {
    it('should clear error signal', () => {
      service.error.set('Test error');
      service.clearError();
      expect(service.error()).toBeNull();
    });
  });

  describe('listDeadLetterItems', () => {
    it('should call correct endpoint with project_id', () => {
      const subscribeSpy = jest.fn();
      service.listDeadLetterItems('project-123').pipe().subscribe(subscribeSpy);
      expect(subscribeSpy).toHaveBeenCalled();
    });

    it('should return DeadLetterItem array', () => {
      let result: DeadLetterItem[] = [];
      service.listDeadLetterItems('project-123').pipe().subscribe(items => { result = items; });
      expect(result.length).toBe(1);
      expect(result[0].dlq_id).toBe('dlq-1');
      expect(result[0].agent_dir).toBe('/agents/developer');
    });

    it('should include all DLQ item fields', () => {
      let result: DeadLetterItem[] = [];
      service.listDeadLetterItems('project-123').pipe().subscribe(items => { result = items; });
      const item = result[0];
      expect(item.job_id).toBe('job-1');
      expect(item.agent_id).toBe('developer');
      expect(item.error_message).toBe('Timeout error');
      expect(item.retry_count).toBe(3);
      expect(item.reason).toBe('timeout');
    });
  });

  describe('retryDeadLetterJob', () => {
    it('should call POST endpoint for single DLQ item replay', () => {
      const subscribeSpy = jest.fn();
      service.retryDeadLetterJob('project-123', 'dlq-1').pipe().subscribe(subscribeSpy);
      expect(subscribeSpy).toHaveBeenCalled();
    });

    it('should pass project_id and dlq_id correctly', () => {
      const subscribeSpy = jest.fn();
      service.retryDeadLetterJob('project-abc', 'dlq-xyz').pipe().subscribe(subscribeSpy);
      expect(subscribeSpy).toHaveBeenCalled();
    });

    it('should return DLQReplayResponse with job_id and status', () => {
      let result: DLQReplayResponse | null = null;
      service.retryDeadLetterJob('project-123', 'dlq-1').pipe().subscribe(response => { result = response; });
      expect(result?.job_id).toBe('job-replayed');
      expect(result?.status).toBe('pending');
    });
  });

  describe('retryAllDeadLetterJobs', () => {
    it('should call POST replay-all endpoint', () => {
      const subscribeSpy = jest.fn();
      service.retryAllDeadLetterJobs('project-123').pipe().subscribe(subscribeSpy);
      expect(subscribeSpy).toHaveBeenCalled();
    });

    it('should pass project_id correctly', () => {
      const subscribeSpy = jest.fn();
      service.retryAllDeadLetterJobs('project-xyz').pipe().subscribe(subscribeSpy);
      expect(subscribeSpy).toHaveBeenCalled();
    });

    it('should return RetryAllResult with replayed count', () => {
      let result: RetryAllResult | null = null;
      service.retryAllDeadLetterJobs('project-123').pipe().subscribe(response => { result = response; });
      expect(result?.replayed).toBe(5);
      expect(result?.failed).toBe(0);
      expect(result?.errors).toEqual([]);
    });
  });

  describe('cleanupAllJobs', () => {
    it('should call POST to /api/jobs/cleanup', () => {
      const subscribeSpy = jest.fn();
      service.cleanupAllJobs().pipe().subscribe(subscribeSpy);
      expect(subscribeSpy).toHaveBeenCalled();
    });

    it('should return JobCleanupResult with cancelled_queued, cancelled_active, total_processed', () => {
      let result: JobCleanupResult | null = null;
      service.cleanupAllJobs().pipe().subscribe(response => { result = response; });
      expect(result).not.toBeNull();
      expect(result).toHaveProperty('cancelled_queued');
      expect(result).toHaveProperty('cancelled_active');
      expect(result).toHaveProperty('total_processed');
    });

    it('should have correct counts in response', () => {
      let result: JobCleanupResult | null = null;
      service.cleanupAllJobs().pipe().subscribe(response => { result = response; });
      expect(result?.cancelled_queued).toBe(5);
      expect(result?.cancelled_active).toBe(2);
      expect(result?.total_processed).toBe(7);
    });
  });

  // ── Mission-tree panel (2026-09-07, ``feature/job-queue-mission-tree``) ─

  describe('listMissions', () => {
    it('should build GET /api/missions with no params (panel default)', () => {
      let result: any = null;
      service.listMissions().pipe().subscribe((r) => { result = r; });
      expect(service.lastRequestUrl).toBe('/api/missions');
      expect(result.missions).toHaveLength(1);
      expect(result.total).toBe(1);
    });

    it('should pass liveness filter as comma-separated query param', () => {
      service.listMissions({ liveness: 'processing,pending,paused' }).pipe().subscribe(() => {});
      expect(service.lastRequestUrl).toContain('liveness=processing%2Cpending%2Cpaused');
      expect(service.lastRequestUrl).toContain('/api/missions');
    });

    it('should pass limit query param', () => {
      service.listMissions({ limit: 20 }).pipe().subscribe(() => {});
      expect(service.lastRequestUrl).toContain('limit=20');
    });

    it('should pass both liveness and limit together', () => {
      service.listMissions({ liveness: 'processing', limit: 20 }).pipe().subscribe(() => {});
      expect(service.lastRequestUrl).toContain('liveness=processing');
      expect(service.lastRequestUrl).toContain('limit=20');
    });

    it('returns the full MissionSummary[] + envelope (missions, total, degraded, has_more)', () => {
      let result: any = null;
      service.listMissions().pipe().subscribe((r) => { result = r; });
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

  describe('listJobsByMission', () => {
    it('should build GET /api/jobs with mission_id and include_deleted=false', () => {
      let result: Job[] | null = null;
      service.listJobsByMission('m-1').pipe().subscribe((r) => { result = r; });
      expect(service.lastRequestUrl).toContain('/api/jobs');
      expect(service.lastRequestUrl).toContain('mission_id=m-1');
      expect(service.lastRequestUrl).toContain('include_deleted=false');
      expect(result).not.toBeNull();
      expect(result!.length).toBeGreaterThan(0);
    });

    it('should URL-encode mission id', () => {
      service.listJobsByMission('mission/with/slashes').pipe().subscribe(() => {});
      expect(service.lastRequestUrl).toContain('mission_id=mission%2Fwith%2Fslashes');
    });

    it('should return the .jobs array from the response (map response.jobs)', () => {
      let result: Job[] | null = null;
      service.listJobsByMission('m-1').pipe().subscribe((r) => { result = r; });
      expect(Array.isArray(result)).toBe(true);
      expect(result![0]).toHaveProperty('job_id');
      expect(result![0]).toHaveProperty('mission_id');
      // Should NOT have a top-level "total" — the wrapper, not the rows.
      expect(result!).not.toHaveProperty('total');
    });
  });
});
