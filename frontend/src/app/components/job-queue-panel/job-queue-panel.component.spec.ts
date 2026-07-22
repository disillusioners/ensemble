import { signal, computed } from '@angular/core';
import { getStatusColor as modelGetStatusColor, Job, JobStatus } from '../../models/job.model';
import { createMockJob } from '../../testing/job-test-helpers';

/**
 * Logic-mirror of JobQueuePanelComponent.
 *
 * This project does NOT use Angular TestBed for component tests —
 * see ``job-queue-indicator.component.spec.ts`` and
 * ``job-detail-drawer.component.spec.ts`` for the same pattern. We
 * replicate the component's signal/computed logic and its helper
 * methods (resolveTitle, projectLabel, shortenId, timeAgo, status
 * helpers) in a plain TS class so the assertions below can exercise
 * priority chains, capping, formatting, and empty-state detection
 * without Angular DI.
 *
 * The class exposes the same input signals as the real component but
 * also small `setX` helper methods so the tests can drive the inputs
 * explicitly. The behaviour of every helper mirrors the real component
 * bit-for-bit; if the real component changes, this mirror must change
 * in lockstep.
 */
class MockJobQueuePanelComponent {
  private readonly _runningJobs = signal<Job[]>([]);
  private readonly _recentJobs = signal<Job[]>([]);
  private readonly _projectNameMap = signal<Map<string | null, string>>(new Map());

  readonly MAX_RECENT = 10;

  runningJobs = this._runningJobs.asReadonly();
  recentJobs = this._recentJobs.asReadonly();
  projectNameMap = this._projectNameMap.asReadonly();

  /** Mock output — mirrors the real component's `output<Job>()`. */
  readonly jobClick = { emit: jest.fn() };

  recentCapped = computed(() => this._recentJobs().slice(0, this.MAX_RECENT));
  isEmpty = computed(
    () => this._runningJobs().length === 0 && this.recentCapped().length === 0,
  );
  runningCount = computed(() => this._runningJobs().length);

  /**
   * Resolves the best available title for a job. Priority chain:
   * 1. job_metadata.instance_name (if truthy)
   * 2. agent_id (if truthy)
   * 3. shortenId of instance_id (or job_id) as a last resort
   */
  resolveTitle(job: Job): string {
    const meta = job.job_metadata;
    if (meta && typeof meta === 'object' && meta['instance_name']) {
      return String(meta['instance_name']);
    }

    if (job.agent_id) {
      return job.agent_id;
    }

    return this.shortenId(job.instance_id ?? job.job_id);
  }

  projectLabel(job: Job): string {
    const id = job.project_id;
    if (id === null || id === undefined) return '—';
    return this._projectNameMap().get(id) ?? this.shortenId(id);
  }

  shortenId(id: string | null | undefined): string {
    if (!id) return '—';
    return id.length > 8 ? id.substring(0, 8) + '...' : id;
  }

  timeAgo(dateString: string | null | undefined): string {
    if (!dateString) return '';
    const now = new Date();
    const date = new Date(dateString);
    const diffMs = now.getTime() - date.getTime();
    const diffSec = Math.floor(diffMs / 1000);
    const diffMin = Math.floor(diffSec / 60);
    const diffHour = Math.floor(diffMin / 60);
    const diffDay = Math.floor(diffHour / 24);

    if (diffSec < 60) return 'just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHour < 24) return `${diffHour}h ago`;
    if (diffDay < 7) return `${diffDay}d ago`;
    return date.toLocaleDateString();
  }

  getStatusIcon(status: JobStatus): string {
    switch (status) {
      case 'completed':
        return 'check_circle';
      case 'failed':
        return 'error';
      case 'cancelled':
        return 'cancel';
      case 'dead_letter':
        return 'inventory_2';
      default:
        return 'info';
    }
  }

  /** Delegate to the shared util — same identity as the real component. */
  readonly getStatusColor = modelGetStatusColor;

  /** Emits the clicked job up to the parent for navigation. */
  onRowClick(job: Job): void {
    this.jobClick.emit(job);
  }

  /** Test helpers — mirror the writable inputs of the real component. */
  setRunningJobs(jobs: Job[]): void {
    this._runningJobs.set(jobs);
  }

  setRecentJobs(jobs: Job[]): void {
    this._recentJobs.set(jobs);
  }

  setProjectNameMap(map: Map<string | null, string>): void {
    this._projectNameMap.set(map);
  }
}

describe('JobQueuePanelComponent Logic', () => {
  let component: MockJobQueuePanelComponent;

  beforeEach(() => {
    component = new MockJobQueuePanelComponent();
  });

  describe('instantiation', () => {
    it('should create the logic-mirror component', () => {
      expect(component).toBeTruthy();
    });

    it('should default to empty state and 0 running', () => {
      expect(component.isEmpty()).toBe(true);
      expect(component.runningCount()).toBe(0);
      expect(component.recentCapped().length).toBe(0);
    });
  });

  describe('resolveTitle priority chain', () => {
    it('should prefer job_metadata.instance_name over agent_id', () => {
      const job = createMockJob({
        instance_id: 'inst-1',
        agent_id: 'developer',
        job_metadata: { instance_name: 'Metadata Name' },
      });
      expect(component.resolveTitle(job)).toBe('Metadata Name');
    });

    it('should fall back to agent_id when no metadata', () => {
      const job = createMockJob({
        instance_id: 'inst-1',
        agent_id: 'developer',
        job_metadata: null,
      });
      expect(component.resolveTitle(job)).toBe('developer');
    });

    it('should fall back to shortened id when nothing else is available', () => {
      // Build the job directly so we can omit agent_id entirely
      // (test helper's `Job` requires a non-empty string).
      const job: Job = {
        job_id: 'abcdef1234567890',
        agent_id: 'placeholder',
        project_id: null,
        priority: 5,
        status: 'processing',
        created_at: new Date().toISOString(),
        started_at: null,
        completed_at: null,
        instance_id: 'instance-abc-12345',
        error_message: null,
        result_summary: null,
        job_metadata: null,
        cancelled_at: null,
      };
      // Force the fallback by clearing the agent_id field via a
      // second, falsy-overridden instance.
      const fallen: Job = { ...job, agent_id: '' as unknown as string };
      expect(component.resolveTitle(fallen)).toBe('instance...');
    });

    it('should skip empty instance_name in metadata', () => {
      const job = createMockJob({
        instance_id: 'inst-1',
        agent_id: 'developer',
        job_metadata: { instance_name: '' },
      });
      expect(component.resolveTitle(job)).toBe('developer');
    });

    it('should fall back to job_id when instance_id is null and agent_id is empty', () => {
      const job = createMockJob({
        job_id: 'job-abc-1234',
        instance_id: null,
      });
      const fallen: Job = { ...job, agent_id: '' as unknown as string };
      expect(component.resolveTitle(fallen)).toBe('job-abc-...');
    });
  });

  describe('shortenId', () => {
    it('should truncate ids longer than 8 chars with "..."', () => {
      expect(component.shortenId('0123456789abcdef')).toBe('01234567...');
    });

    it('should not truncate ids that are exactly 8 chars', () => {
      expect(component.shortenId('12345678')).toBe('12345678');
    });

    it('should not truncate ids shorter than 8 chars', () => {
      expect(component.shortenId('proj-A')).toBe('proj-A');
    });

    it('should return em-dash for null', () => {
      expect(component.shortenId(null)).toBe('—');
    });

    it('should return em-dash for undefined', () => {
      expect(component.shortenId(undefined)).toBe('—');
    });

    it('should return em-dash for empty string', () => {
      expect(component.shortenId('')).toBe('—');
    });
  });

  describe('projectLabel', () => {
    it('should use the cached project name when present', () => {
      component.setProjectNameMap(new Map([['proj-1', 'Alpha Project']]));
      const job = createMockJob({ project_id: 'proj-1' });
      expect(component.projectLabel(job)).toBe('Alpha Project');
    });

    it('should fall back to shortened id when name is missing', () => {
      component.setProjectNameMap(new Map());
      const job = createMockJob({ project_id: 'project-1234-abc' });
      expect(component.projectLabel(job)).toBe('project-...');
    });

    it('should return em-dash for null project_id', () => {
      component.setProjectNameMap(new Map([['proj-1', 'Alpha']]));
      const job = createMockJob({ project_id: null });
      expect(component.projectLabel(job)).toBe('—');
    });
  });

  describe('timeAgo', () => {
    it('should return "just now" for timestamps within the last minute', () => {
      const now = new Date().toISOString();
      expect(component.timeAgo(now)).toBe('just now');
    });

    it('should return "Xm ago" for minutes', () => {
      const fiveMinAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString();
      expect(component.timeAgo(fiveMinAgo)).toBe('5m ago');
    });

    it('should return "Xh ago" for hours', () => {
      const twoHoursAgo = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString();
      expect(component.timeAgo(twoHoursAgo)).toBe('2h ago');
    });

    it('should return "Xd ago" for days', () => {
      const threeDaysAgo = new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString();
      expect(component.timeAgo(threeDaysAgo)).toBe('3d ago');
    });

    it('should return empty string for null', () => {
      expect(component.timeAgo(null)).toBe('');
    });

    it('should return empty string for undefined', () => {
      expect(component.timeAgo(undefined)).toBe('');
    });

    it('should return a locale date for items older than 7 days', () => {
      const oldDate = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString();
      const result = component.timeAgo(oldDate);
      // We don't pin the exact locale string; just verify it's NOT a
      // "Xd ago" / "Xh ago" form and is non-empty.
      expect(result).toBeTruthy();
      expect(result).not.toMatch(/ago$/);
    });
  });

  describe('isEmpty', () => {
    it('should be true when both running and recent are empty', () => {
      component.setRunningJobs([]);
      component.setRecentJobs([]);
      expect(component.isEmpty()).toBe(true);
    });

    it('should be false when runningJobs has items', () => {
      component.setRunningJobs([createMockJob({ status: 'processing' })]);
      component.setRecentJobs([]);
      expect(component.isEmpty()).toBe(false);
    });

    it('should be false when recentJobs has items', () => {
      component.setRunningJobs([]);
      component.setRecentJobs([createMockJob({ status: 'completed' })]);
      expect(component.isEmpty()).toBe(false);
    });

    it('should be false when both lists have items', () => {
      component.setRunningJobs([createMockJob({ status: 'processing' })]);
      component.setRecentJobs([createMockJob({ status: 'completed' })]);
      expect(component.isEmpty()).toBe(false);
    });

    it('should react to signal updates', () => {
      expect(component.isEmpty()).toBe(true);
      component.setRunningJobs([createMockJob({ status: 'processing' })]);
      expect(component.isEmpty()).toBe(false);
      component.setRunningJobs([]);
      expect(component.isEmpty()).toBe(true);
    });
  });

  describe('recentCapped', () => {
    it('should cap recent jobs at 10', () => {
      const jobs = Array.from({ length: 15 }, (_, i) =>
        createMockJob({ job_id: `job-${i}` }),
      );
      component.setRecentJobs(jobs);
      expect(component.recentCapped().length).toBe(10);
    });

    it('should preserve order when capping', () => {
      const jobs = Array.from({ length: 15 }, (_, i) =>
        createMockJob({ job_id: `job-${i}` }),
      );
      component.setRecentJobs(jobs);
      expect(component.recentCapped()[0].job_id).toBe('job-0');
      expect(component.recentCapped()[9].job_id).toBe('job-9');
    });

    it('should not cap when fewer than 10 jobs', () => {
      const jobs = Array.from({ length: 5 }, (_, i) =>
        createMockJob({ job_id: `job-${i}` }),
      );
      component.setRecentJobs(jobs);
      expect(component.recentCapped().length).toBe(5);
    });

    it('should pass through an empty list unchanged', () => {
      component.setRecentJobs([]);
      expect(component.recentCapped()).toEqual([]);
    });

    it('should cap exactly 10 jobs to 10 (boundary)', () => {
      const jobs = Array.from({ length: 10 }, (_, i) =>
        createMockJob({ job_id: `job-${i}` }),
      );
      component.setRecentJobs(jobs);
      expect(component.recentCapped().length).toBe(10);
    });

    it('should cap 11 jobs to 10 (boundary)', () => {
      const jobs = Array.from({ length: 11 }, (_, i) =>
        createMockJob({ job_id: `job-${i}` }),
      );
      component.setRecentJobs(jobs);
      expect(component.recentCapped().length).toBe(10);
    });
  });

  describe('runningCount', () => {
    it('should reflect the number of running jobs', () => {
      component.setRunningJobs([
        createMockJob({ status: 'processing' }),
        createMockJob({ status: 'processing' }),
        createMockJob({ status: 'processing' }),
      ]);
      expect(component.runningCount()).toBe(3);
    });

    it('should not count recent jobs', () => {
      component.setRunningJobs([]);
      component.setRecentJobs([
        createMockJob({ status: 'completed' }),
        createMockJob({ status: 'failed' }),
      ]);
      expect(component.runningCount()).toBe(0);
    });
  });

  describe('status helpers', () => {
    it('should map completed to check_circle and green', () => {
      expect(component.getStatusIcon('completed')).toBe('check_circle');
      expect(component.getStatusColor('completed')).toBe('#22C55E');
    });

    it('should map failed to error and red', () => {
      expect(component.getStatusIcon('failed')).toBe('error');
      expect(component.getStatusColor('failed')).toBe('#EF4444');
    });

    it('should map cancelled to cancel and amber', () => {
      expect(component.getStatusIcon('cancelled')).toBe('cancel');
      expect(component.getStatusColor('cancelled')).toBe('#F59E0B');
    });

    it('should map dead_letter to inventory_2 and purple', () => {
      expect(component.getStatusIcon('dead_letter')).toBe('inventory_2');
      expect(component.getStatusColor('dead_letter')).toBe('#7C3AED');
    });

    it('should fall back to info/grey for non-terminal statuses', () => {
      // pending, processing, paused all fall through the switch to
      // the default branch — that's the "unknown" fallback path.
      expect(component.getStatusIcon('pending')).toBe('info');
      expect(component.getStatusColor('pending')).toBe('#9CA3AF');
      expect(component.getStatusIcon('processing')).toBe('info');
      expect(component.getStatusColor('processing')).toBe('#3B82F6');
      expect(component.getStatusIcon('paused')).toBe('info');
      expect(component.getStatusColor('paused')).toBe('#F59E0B');
    });

    it('should delegate getStatusColor to the shared model util', () => {
      // Reference identity locks in the delegation; the component
      // must NOT define its own color table.
      expect(component.getStatusColor).toBe(modelGetStatusColor);
    });
  });

  describe('integration — priority chain with mixed data', () => {
    it('should resolve different titles for a mixed list', () => {
      component.setRunningJobs([
        createMockJob({
          job_id: 'job-1',
          instance_id: 'inst-A',
          agent_id: 'developer',
          job_metadata: { instance_name: 'From Metadata A' },
          status: 'processing',
        }),
        createMockJob({
          job_id: 'job-2',
          instance_id: 'inst-B',
          agent_id: 'developer',
          job_metadata: null,
          status: 'processing',
        }),
        createMockJob({
          job_id: 'job-3',
          instance_id: null,
          agent_id: 'developer',
          job_metadata: null,
          status: 'processing',
        }),
      ]);
      const titles = component.runningJobs().map((j) => component.resolveTitle(j));
      expect(titles).toEqual([
        'From Metadata A',
        'developer',
        'developer',
      ]);
    });
  });

  describe('jobClick emit', () => {
    beforeEach(() => {
      // Each test starts with a fresh emit spy. The component is
      // re-created in the outer beforeEach, but jest.fn() state on
      // the class field persists across the same instance, so reset
      // explicitly.
      (component.jobClick.emit as jest.Mock).mockClear();
    });

    it('should expose jobClick.emit as a function', () => {
      expect(typeof component.jobClick.emit).toBe('function');
    });

    it('should emit the clicked job once on a single onRowClick', () => {
      const job = createMockJob({ status: 'processing' });
      component.onRowClick(job);
      expect(component.jobClick.emit).toHaveBeenCalledTimes(1);
      expect(component.jobClick.emit).toHaveBeenCalledWith(job);
    });

    it('should emit both jobs in order across successive onRowClick calls', () => {
      const jobA = createMockJob({ job_id: 'job-A', status: 'processing' });
      const jobB = createMockJob({ job_id: 'job-B', status: 'completed' });
      component.onRowClick(jobA);
      component.onRowClick(jobB);
      expect(component.jobClick.emit).toHaveBeenCalledTimes(2);
      expect(component.jobClick.emit).toHaveBeenNthCalledWith(1, jobA);
      expect(component.jobClick.emit).toHaveBeenNthCalledWith(2, jobB);
    });
  });
});
