import { signal, computed } from '@angular/core';
import { Job, JobStatus } from '../../models/job.model';
import { createMockJob } from '../../testing/job-test-helpers';

/**
 * Logic-mirror of JobQueueIndicatorComponent.
 *
 * This project does NOT use Angular TestBed for component tests. Instead, we
 * replicate the component's signal/computed logic in a plain TS class and test
 * it directly — same pattern as job-detail-drawer.component.spec.ts. We expose
 * the private helpers (groupByProject, isRunningStatus, isPendingStatus,
 * shortenId) that the real component keeps private so the mirror stays
 * behaviour-equivalent for the assertions below.
 */
class MockJobQueueIndicatorComponent {
  /** Raw jobs currently in queued/active state. */
  private readonly jobs = signal<Job[]>([]);

  /** Cached project_id → project name. Rebuilt on init. */
  private readonly projectNameMap = signal<Map<string, string>>(new Map());

  /** Computed total count (queued + active). */
  readonly jobCount = computed(() => this.jobs().length);

  /** Whether the badge should be visible (count > 0). */
  readonly hasJobs = computed(() => this.jobCount() > 0);

  /**
   * Per-project breakdown rendered in the tooltip. Each entry is a
   * pre-formatted line so the template can render a simple list.
   */
  readonly tooltipLines = computed<string[]>(() => {
    const grouped = this.groupByProject(this.jobs());
    if (grouped.size === 0) {
      return ['No active jobs'];
    }
    const nameMap = this.projectNameMap();
    const lines: string[] = [];
    const sortedKeys = Array.from(grouped.keys()).sort();
    for (const projectId of sortedKeys) {
      const counts = grouped.get(projectId)!;
      const running = counts.running;
      const pending = counts.pending;
      const name = nameMap.get(projectId) ?? this.shortenId(projectId);
      const parts: string[] = [];
      if (running > 0) parts.push(`${running} running`);
      if (pending > 0) parts.push(`${pending} pending`);
      lines.push(`${name}: ${parts.join(', ')}`);
    }
    return lines;
  });

  /** Joined tooltip text for the matTooltip binding. */
  readonly tooltipText = computed(() => this.tooltipLines().join('\n'));

  private groupByProject(jobs: Job[]): Map<string, { running: number; pending: number }> {
    const groups = new Map<string, { running: number; pending: number }>();
    for (const job of jobs) {
      const key = job.project_id ?? '__unassigned__';
      const bucket = groups.get(key) ?? { running: 0, pending: 0 };
      if (this.isRunningStatus(job.status)) {
        bucket.running += 1;
      } else if (this.isPendingStatus(job.status)) {
        bucket.pending += 1;
      }
      groups.set(key, bucket);
    }
    return groups;
  }

  private isRunningStatus(status: JobStatus): boolean {
    return status === 'processing';
  }

  private isPendingStatus(status: JobStatus): boolean {
    return status === 'pending';
  }

  private shortenId(id: string): string {
    return id.length > 8 ? id.substring(0, 8) + '...' : id;
  }

  /** Test helper: set the jobs signal (mirrors real component's private setter). */
  setJobs(jobs: Job[]): void {
    this.jobs.set(jobs);
  }

  /** Test helper: set the project name cache. */
  setProjectNameMap(map: Map<string, string>): void {
    this.projectNameMap.set(map);
  }
}

describe('JobQueueIndicatorComponent Logic', () => {
  let component: MockJobQueueIndicatorComponent;

  beforeEach(() => {
    component = new MockJobQueueIndicatorComponent();
  });

  describe('instantiation', () => {
    it('should create the logic-mirror component', () => {
      expect(component).toBeTruthy();
    });

    it('should default to 0 jobs and not have jobs', () => {
      expect(component.jobCount()).toBe(0);
      expect(component.hasJobs()).toBe(false);
    });
  });

  describe('jobCount', () => {
    it('should reflect the number of jobs in the signal', () => {
      component.setJobs([
        createMockJob({ status: 'pending' }),
        createMockJob({ status: 'processing' }),
        createMockJob({ status: 'pending' }),
      ]);
      expect(component.jobCount()).toBe(3);
    });

    it('should count only jobs currently in the signal', () => {
      component.setJobs([createMockJob({ status: 'pending' })]);
      expect(component.jobCount()).toBe(1);

      component.setJobs([
        createMockJob({ status: 'pending' }),
        createMockJob({ status: 'processing' }),
      ]);
      expect(component.jobCount()).toBe(2);
    });
  });

  describe('hasJobs', () => {
    it('should be false when count is 0', () => {
      component.setJobs([]);
      expect(component.hasJobs()).toBe(false);
    });

    it('should be true when count > 0', () => {
      component.setJobs([createMockJob({ status: 'pending' })]);
      expect(component.hasJobs()).toBe(true);
    });

    it('should be true with a single processing job', () => {
      component.setJobs([createMockJob({ status: 'processing' })]);
      expect(component.hasJobs()).toBe(true);
    });
  });

  describe('tooltipLines — grouping by project_id', () => {
    it('should group jobs by project_id, counting running and pending separately', () => {
      component.setJobs([
        createMockJob({ project_id: 'proj-A', status: 'processing' }),
        createMockJob({ project_id: 'proj-A', status: 'pending' }),
        createMockJob({ project_id: 'proj-A', status: 'processing' }),
        createMockJob({ project_id: 'proj-B', status: 'pending' }),
      ]);
      expect(component.tooltipLines()).toEqual([
        'proj-A: 2 running, 1 pending',
        'proj-B: 1 pending',
      ]);
    });

    it('should show only running when there are no pending jobs in a group', () => {
      component.setJobs([createMockJob({ project_id: 'proj-A', status: 'processing' })]);
      expect(component.tooltipLines()).toEqual(['proj-A: 1 running']);
    });

    it('should show only pending when there are no running jobs in a group', () => {
      component.setJobs([createMockJob({ project_id: 'proj-A', status: 'pending' })]);
      expect(component.tooltipLines()).toEqual(['proj-A: 1 pending']);
    });

    it('should ignore jobs with terminal statuses (completed, failed, cancelled, dead_letter)', () => {
      // Terminal-status jobs are NOT counted as running or pending. We verify
      // this by mixing a terminal job with an active one: the terminal job
      // contributes 0 to both counters, so only the active job shows up.
      component.setJobs([
        createMockJob({ project_id: 'proj-A', status: 'completed' }),
        createMockJob({ project_id: 'proj-A', status: 'failed' }),
        createMockJob({ project_id: 'proj-A', status: 'cancelled' }),
        createMockJob({ project_id: 'proj-A', status: 'dead_letter' }),
        createMockJob({ project_id: 'proj-A', status: 'pending' }),
      ]);
      // Only the pending job is counted; terminal jobs contribute 0 to running.
      expect(component.tooltipLines()).toEqual(['proj-A: 1 pending']);
    });
  });

  describe('tooltipLines — empty state', () => {
    it('should return ["No active jobs"] when there are no jobs', () => {
      component.setJobs([]);
      expect(component.tooltipLines()).toEqual(['No active jobs']);
    });
  });

  describe('tooltipLines — running/pending counting', () => {
    it('should count only processing jobs as running', () => {
      component.setJobs([
        createMockJob({ project_id: 'proj-A', status: 'processing' }),
        createMockJob({ project_id: 'proj-A', status: 'pending' }),
      ]);
      // processing → running (1); pending → pending (1).
      expect(component.tooltipLines()).toEqual(['proj-A: 1 running, 1 pending']);
    });
  });

  describe('tooltipLines — sorting', () => {
    it('should sort lines by project_id for deterministic order', () => {
      component.setJobs([
        createMockJob({ project_id: 'proj-Z', status: 'pending' }),
        createMockJob({ project_id: 'proj-A', status: 'pending' }),
        createMockJob({ project_id: 'proj-M', status: 'pending' }),
      ]);
      expect(component.tooltipLines()).toEqual([
        'proj-A: 1 pending',
        'proj-M: 1 pending',
        'proj-Z: 1 pending',
      ]);
    });
  });

  describe('tooltipLines — project name resolution', () => {
    it('should use the cached project name when available', () => {
      const map = new Map<string, string>([['proj-A', 'Alpha Project']]);
      component.setProjectNameMap(map);
      component.setJobs([createMockJob({ project_id: 'proj-A', status: 'pending' })]);
      expect(component.tooltipLines()).toEqual(['Alpha Project: 1 pending']);
    });

    it('should fall back to shortened project_id when no name is cached', () => {
      component.setJobs([createMockJob({ project_id: 'proj-A', status: 'pending' })]);
      expect(component.tooltipLines()).toEqual(['proj-A: 1 pending']);
    });
  });

  describe('shortenId', () => {
    it('should truncate ids longer than 8 chars with "..."', () => {
      // Exposed indirectly via tooltipLines: a project_id without a cached name
      // whose id exceeds 8 characters gets truncated. substring(0, 8) yields
      // the first 8 characters followed by "...".
      const longId = '0123456789abcdef';
      component.setJobs([createMockJob({ project_id: longId, status: 'pending' })]);
      expect(component.tooltipLines()).toEqual(['01234567...: 1 pending']);
    });

    it('should not truncate ids that are exactly 8 chars', () => {
      const eightChars = '12345678';
      component.setJobs([createMockJob({ project_id: eightChars, status: 'pending' })]);
      expect(component.tooltipLines()).toEqual(['12345678: 1 pending']);
    });

    it('should not truncate ids shorter than 8 chars', () => {
      const shortId = 'proj-A';
      component.setJobs([createMockJob({ project_id: shortId, status: 'pending' })]);
      expect(component.tooltipLines()).toEqual(['proj-A: 1 pending']);
    });
  });

  describe('unassigned jobs (project_id=null)', () => {
    it('should bucket jobs without a project_id under "__unassigned__"', () => {
      // Cache the project name so the raw bucket key '__unassigned__' is
      // rendered verbatim (otherwise shortenId truncates the 14-char label).
      component.setProjectNameMap(new Map([['__unassigned__', '__unassigned__']]));
      component.setJobs([
        createMockJob({ project_id: null, status: 'pending' }),
        createMockJob({ project_id: null, status: 'processing' }),
      ]);
      expect(component.tooltipLines()).toEqual(['__unassigned__: 1 running, 1 pending']);
    });

    it('should separate unassigned jobs from assigned jobs in the breakdown', () => {
      component.setProjectNameMap(new Map([['__unassigned__', '__unassigned__']]));
      component.setJobs([
        createMockJob({ project_id: 'proj-A', status: 'pending' }),
        createMockJob({ project_id: null, status: 'processing' }),
      ]);
      // "__unassigned__" sorts before "proj-A" alphabetically.
      expect(component.tooltipLines()).toEqual([
        '__unassigned__: 1 running',
        'proj-A: 1 pending',
      ]);
    });

    it('should shorten the long "__unassigned__" label when no name is cached', () => {
      component.setJobs([createMockJob({ project_id: null, status: 'pending' })]);
      // '__unassigned__' is 14 chars → truncated to first 8 chars + '...'.
      expect(component.tooltipLines()).toEqual(['__unassi...: 1 pending']);
    });
  });

  describe('tooltipText', () => {
    it('should join lines with newline', () => {
      component.setJobs([
        createMockJob({ project_id: 'proj-A', status: 'pending' }),
        createMockJob({ project_id: 'proj-B', status: 'processing' }),
      ]);
      expect(component.tooltipText()).toBe('proj-A: 1 pending\nproj-B: 1 running');
    });

    it('should render "No active jobs" as a single line when there are no jobs', () => {
      component.setJobs([]);
      expect(component.tooltipText()).toBe('No active jobs');
    });

    it('should contain "\n" between each pair of lines', () => {
      component.setJobs([
        createMockJob({ project_id: 'proj-A', status: 'pending' }),
        createMockJob({ project_id: 'proj-B', status: 'pending' }),
        createMockJob({ project_id: 'proj-C', status: 'pending' }),
      ]);
      const lines = component.tooltipText().split('\n');
      expect(lines.length).toBe(3);
      expect(lines).toEqual([
        'proj-A: 1 pending',
        'proj-B: 1 pending',
        'proj-C: 1 pending',
      ]);
    });
  });
});
