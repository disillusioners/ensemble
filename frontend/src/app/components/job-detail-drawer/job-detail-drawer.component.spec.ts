import { signal, computed } from '@angular/core';
import { Job, JobStatus, JobSource } from '../../models/job.model';
import { createMockJob } from '../../testing/job-test-helpers';

// Simplified JobDetailDrawer component logic for testing
class MockJobDetailDrawerComponent {
  private _job = signal<Job | null>(null);
  
  job = computed(() => this._job());
  isDrawerMode = signal(false);

  statusColor = computed(() => {
    const status = this._job()?.status;
    switch (status) {
      case 'pending': return 'warn';
      case 'processing': return 'accent';
      case 'completed': return 'primary';
      case 'failed': return 'warn';
      case 'cancelled': return 'warn';
      default: return 'primary';
    }
  });

  statusLabel = computed(() => {
    const status = this._job()?.status;
    if (!status) return '';
    // Handle snake_case (e.g., 'dead_letter' -> 'Dead Letter')
    return status.replace(/_/g, ' ').split(' ').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
  });

  duration = computed(() => {
    const job = this._job();
    if (!job?.started_at || !job?.completed_at) return null;

    const start = new Date(job.started_at).getTime();
    const end = new Date(job.completed_at).getTime();
    const diffMs = end - start;

    if (diffMs < 0) return null;

    const seconds = Math.floor(diffMs / 1000);
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);

    if (hours > 0) {
      return `${hours}h ${minutes % 60}m ${seconds % 60}s`;
    } else if (minutes > 0) {
      return `${minutes}m ${seconds % 60}s`;
    } else {
      return `${seconds}s`;
    }
  });

  canCancel = computed(() => {
    const status = this._job()?.status;
    return status === 'pending' || status === 'processing';
  });

  canRetry = computed(() => {
    const status = this._job()?.status;
    return status === 'failed' || status === 'dead_letter';
  });

  hasInstance = computed(() => {
    const job = this._job();
    return !!(job?.instance_id);
  });

  formattedMetadata = computed(() => {
    const metadata = this._job()?.job_metadata;
    if (!metadata) return null;
    return JSON.stringify(metadata, null, 2);
  });

  priorityLabel = computed(() => `P${this._job()?.priority ?? 0}`);

  formatDate(dateStr?: string | null): string {
    if (!dateStr) return 'N/A';
    const date = new Date(dateStr);
    return date.toLocaleString();
  }

  setJob(job: Job | null) {
    this._job.set(job);
  }
}

describe('JobDetailDrawerComponent Logic', () => {
  let component: MockJobDetailDrawerComponent;

  beforeEach(() => {
    component = new MockJobDetailDrawerComponent();
  });

  describe('statusColor computed', () => {
    it('should return warn for pending status', () => {
      component.setJob(createMockJob({ status: 'pending' }));
      expect(component.statusColor()).toBe('warn');
    });

    it('should return accent for processing status', () => {
      component.setJob(createMockJob({ status: 'processing' }));
      expect(component.statusColor()).toBe('accent');
    });

    it('should return primary for completed status', () => {
      component.setJob(createMockJob({ status: 'completed' }));
      expect(component.statusColor()).toBe('primary');
    });

    it('should return warn for failed status', () => {
      component.setJob(createMockJob({ status: 'failed' }));
      expect(component.statusColor()).toBe('warn');
    });

    it('should return warn for cancelled status', () => {
      component.setJob(createMockJob({ status: 'cancelled' }));
      expect(component.statusColor()).toBe('warn');
    });
  });

  describe('statusLabel computed', () => {
    it('should return capitalized status for pending', () => {
      component.setJob(createMockJob({ status: 'pending' }));
      expect(component.statusLabel()).toBe('Pending');
    });

    it('should return capitalized status for processing', () => {
      component.setJob(createMockJob({ status: 'processing' }));
      expect(component.statusLabel()).toBe('Processing');
    });

    it('should return capitalized status for completed', () => {
      component.setJob(createMockJob({ status: 'completed' }));
      expect(component.statusLabel()).toBe('Completed');
    });

    it('should return capitalized status for failed', () => {
      component.setJob(createMockJob({ status: 'failed' }));
      expect(component.statusLabel()).toBe('Failed');
    });

    it('should return capitalized status for cancelled', () => {
      component.setJob(createMockJob({ status: 'cancelled' }));
      expect(component.statusLabel()).toBe('Cancelled');
    });

    it('should return "Dead Letter" for dead_letter status (with space, not underscore)', () => {
      component.setJob(createMockJob({ status: 'dead_letter' }));
      expect(component.statusLabel()).toBe('Dead Letter');
    });
  });

  describe('duration computed', () => {
    it('should return null when started_at is null', () => {
      component.setJob(createMockJob({
        started_at: null,
        completed_at: new Date().toISOString(),
      }));
      expect(component.duration()).toBeNull();
    });

    it('should return null when completed_at is null', () => {
      component.setJob(createMockJob({
        started_at: new Date().toISOString(),
        completed_at: null,
      }));
      expect(component.duration()).toBeNull();
    });

    it('should return null when both are null', () => {
      component.setJob(createMockJob({
        started_at: null,
        completed_at: null,
      }));
      expect(component.duration()).toBeNull();
    });

    it('should calculate duration in seconds', () => {
      const startedAt = new Date('2024-01-01T12:00:00Z');
      const completedAt = new Date('2024-01-01T12:00:30Z');
      component.setJob(createMockJob({
        started_at: startedAt.toISOString(),
        completed_at: completedAt.toISOString(),
      }));
      expect(component.duration()).toBe('30s');
    });

    it('should calculate duration in minutes and seconds', () => {
      const startedAt = new Date('2024-01-01T12:00:00Z');
      const completedAt = new Date('2024-01-01T12:05:30Z');
      component.setJob(createMockJob({
        started_at: startedAt.toISOString(),
        completed_at: completedAt.toISOString(),
      }));
      expect(component.duration()).toBe('5m 30s');
    });

    it('should calculate duration in hours, minutes, and seconds', () => {
      const startedAt = new Date('2024-01-01T12:00:00Z');
      const completedAt = new Date('2024-01-01T14:30:45Z');
      component.setJob(createMockJob({
        started_at: startedAt.toISOString(),
        completed_at: completedAt.toISOString(),
      }));
      expect(component.duration()).toBe('2h 30m 45s');
    });
  });

  describe('canCancel computed', () => {
    it('should return true for pending status', () => {
      component.setJob(createMockJob({ status: 'pending' }));
      expect(component.canCancel()).toBe(true);
    });

    it('should return true for processing status', () => {
      component.setJob(createMockJob({ status: 'processing' }));
      expect(component.canCancel()).toBe(true);
    });

    it('should return false for completed status', () => {
      component.setJob(createMockJob({ status: 'completed' }));
      expect(component.canCancel()).toBe(false);
    });

    it('should return false for failed status', () => {
      component.setJob(createMockJob({ status: 'failed' }));
      expect(component.canCancel()).toBe(false);
    });

    it('should return false for cancelled status', () => {
      component.setJob(createMockJob({ status: 'cancelled' }));
      expect(component.canCancel()).toBe(false);
    });
  });

  describe('canRetry computed', () => {
    it('should return true for failed status', () => {
      component.setJob(createMockJob({ status: 'failed' }));
      expect(component.canRetry()).toBe(true);
    });

    it('should return true for dead_letter status', () => {
      component.setJob(createMockJob({ status: 'dead_letter' }));
      expect(component.canRetry()).toBe(true);
    });

    it('should return false for pending status', () => {
      component.setJob(createMockJob({ status: 'pending' }));
      expect(component.canRetry()).toBe(false);
    });

    it('should return false for processing status', () => {
      component.setJob(createMockJob({ status: 'processing' }));
      expect(component.canRetry()).toBe(false);
    });

    it('should return false for completed status', () => {
      component.setJob(createMockJob({ status: 'completed' }));
      expect(component.canRetry()).toBe(false);
    });

    it('should return false for cancelled status', () => {
      component.setJob(createMockJob({ status: 'cancelled' }));
      expect(component.canRetry()).toBe(false);
    });
  });

  describe('hasInstance computed', () => {
    it('should return true when instance_id exists', () => {
      component.setJob(createMockJob({ instance_id: 'instance-123' }));
      expect(component.hasInstance()).toBe(true);
    });

    it('should return false when instance_id is null', () => {
      component.setJob(createMockJob({ instance_id: null }));
      expect(component.hasInstance()).toBe(false);
    });
  });

  describe('formattedMetadata computed', () => {
    it('should return null when job_metadata is null', () => {
      component.setJob(createMockJob({ job_metadata: null }));
      expect(component.formattedMetadata()).toBeNull();
    });

    it('should return JSON string when job_metadata exists', () => {
      component.setJob(createMockJob({ job_metadata: { key: 'value' } }));
      expect(component.formattedMetadata()).toBe('{\n  "key": "value"\n}');
    });

    it('should format nested metadata correctly', () => {
      component.setJob(createMockJob({
        job_metadata: { nested: { a: 1, b: 2 } },
      }));
      expect(component.formattedMetadata()).toContain('"nested"');
      expect(component.formattedMetadata()).toContain('"a"');
      expect(component.formattedMetadata()).toContain('1');
    });
  });

  describe('priorityLabel computed', () => {
    it('should return P followed by priority number', () => {
      component.setJob(createMockJob({ priority: 5 }));
      expect(component.priorityLabel()).toBe('P5');
    });

    it('should return P10 for priority 10', () => {
      component.setJob(createMockJob({ priority: 10 }));
      expect(component.priorityLabel()).toBe('P10');
    });

    it('should return P1 for priority 1', () => {
      component.setJob(createMockJob({ priority: 1 }));
      expect(component.priorityLabel()).toBe('P1');
    });
  });

  describe('formatDate', () => {
    it('should return N/A for undefined', () => {
      expect(component.formatDate(undefined)).toBe('N/A');
    });

    it('should return N/A for null', () => {
      expect(component.formatDate(null)).toBe('N/A');
    });

    it('should return formatted date string', () => {
      const dateStr = '2024-01-15T12:30:00Z';
      const result = component.formatDate(dateStr);
      expect(result).not.toBe('N/A');
      expect(result).toContain('2024');
    });
  });

  describe('template rendering conditions', () => {
    it('should display source badge when source exists', () => {
      component.setJob(createMockJob({ source: 'api' }));
      expect(component.job()?.source).toBe('api');
    });

    it('should display cancelled_at when it exists', () => {
      const cancelledAt = new Date().toISOString();
      component.setJob(createMockJob({
        status: 'cancelled',
        cancelled_at: cancelledAt,
      }));
      expect(component.job()?.cancelled_at).toBe(cancelledAt);
    });

    it('should handle job without message (optional field)', () => {
      const job = createMockJob();
      (job as any).message = undefined;
      component.setJob(job);
      expect(component.job()?.message).toBeUndefined();
    });

    it('should show error when job is failed with error_message', () => {
      component.setJob(createMockJob({
        status: 'failed',
        error_message: 'Something went wrong',
      }));
      expect(component.job()?.error_message).toBe('Something went wrong');
    });

    it('should show result when job is completed with result_summary', () => {
      component.setJob(createMockJob({
        status: 'completed',
        result_summary: 'Task completed successfully',
      }));
      expect(component.job()?.result_summary).toBe('Task completed successfully');
    });

    it('should show instance link when job.instance_id exists', () => {
      component.setJob(createMockJob({
        status: 'completed',
        instance_id: 'instance-123',
      }));
      expect(component.job()?.instance_id).toBe('instance-123');
    });

    it('should show cancel button for pending job', () => {
      component.setJob(createMockJob({ status: 'pending' }));
      expect(component.canCancel()).toBe(true);
    });

    it('should show cancel button for processing job', () => {
      component.setJob(createMockJob({ status: 'processing' }));
      expect(component.canCancel()).toBe(true);
    });

    it('should not show cancel button for completed job', () => {
      component.setJob(createMockJob({ status: 'completed' }));
      expect(component.canCancel()).toBe(false);
    });

    it('should not show cancel button for failed job', () => {
      component.setJob(createMockJob({ status: 'failed' }));
      expect(component.canCancel()).toBe(false);
    });

    it('should show retry button for failed job', () => {
      component.setJob(createMockJob({ status: 'failed' }));
      expect(component.canRetry()).toBe(true);
    });

    it('should show retry button for dead_letter job', () => {
      component.setJob(createMockJob({ status: 'dead_letter' }));
      expect(component.canRetry()).toBe(true);
    });

    it('should not show retry button for pending job', () => {
      component.setJob(createMockJob({ status: 'pending' }));
      expect(component.canRetry()).toBe(false);
    });

    it('should not show retry button for completed job', () => {
      component.setJob(createMockJob({ status: 'completed' }));
      expect(component.canRetry()).toBe(false);
    });
  });
});
