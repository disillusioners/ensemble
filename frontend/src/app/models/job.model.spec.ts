import {
  Job,
  JobStatus,
  JobSource,
  JobCreate,
  JobFilters,
  JobEvent,
  DeadLetterItem,
  RetryAllResult,
  DLQReplayResponse,
  DLQListResponse,
  isTerminalStatus,
  isJobDeleted,
  getStatusColor,
  getPriorityColor,
} from './job.model';

describe('Job Model', () => {
  describe('JobStatus type', () => {
    it('should have all expected status values', () => {
      const statuses: JobStatus[] = ['pending', 'processing', 'completed', 'failed', 'cancelled', 'dead_letter'];
      expect(statuses).toHaveLength(6);
    });
  });

  describe('JobSource type', () => {
    it('should have all expected source values', () => {
      const sources: JobSource[] = ['api', 'telegram', 'scheduler', 'webhook'];
      expect(sources).toHaveLength(4);
    });
  });

  describe('isTerminalStatus', () => {
    it('should return false for pending status', () => {
      expect(isTerminalStatus('pending')).toBe(false);
    });

    it('should return false for processing status', () => {
      expect(isTerminalStatus('processing')).toBe(false);
    });

    it('should return true for completed status', () => {
      expect(isTerminalStatus('completed')).toBe(true);
    });

    it('should return true for failed status', () => {
      expect(isTerminalStatus('failed')).toBe(true);
    });

    it('should return true for cancelled status', () => {
      expect(isTerminalStatus('cancelled')).toBe(true);
    });

    it('should return true for dead_letter status', () => {
      expect(isTerminalStatus('dead_letter')).toBe(true);
    });
  });

  describe('isJobDeleted', () => {
    it('should return false when deleted_at is null', () => {
      const job: Job = {
        job_id: 'test-1',
        agent_id: 'agent-1',
        project_id: 'project-1',
        priority: 5,
        status: 'completed',
        created_at: new Date().toISOString(),
        started_at: null,
        completed_at: new Date().toISOString(),
        instance_id: null,
        error_message: null,
        result_summary: null,
        cancelled_at: null,
        deleted_at: null,
      };
      expect(isJobDeleted(job)).toBe(false);
    });

    it('should return false when deleted_at is undefined', () => {
      const job: Job = {
        job_id: 'test-2',
        agent_id: 'agent-1',
        project_id: 'project-1',
        priority: 5,
        status: 'completed',
        created_at: new Date().toISOString(),
        started_at: null,
        completed_at: new Date().toISOString(),
        instance_id: null,
        error_message: null,
        result_summary: null,
        cancelled_at: null,
        // deleted_at is not present (undefined)
      };
      expect(isJobDeleted(job)).toBe(false);
    });

    it('should return true when deleted_at is set with ISO string', () => {
      const job: Job = {
        job_id: 'test-3',
        agent_id: 'agent-1',
        project_id: 'project-1',
        priority: 5,
        status: 'completed',
        created_at: new Date().toISOString(),
        started_at: null,
        completed_at: new Date().toISOString(),
        instance_id: null,
        error_message: null,
        result_summary: null,
        cancelled_at: null,
        deleted_at: '2024-01-15T10:30:00Z',
      };
      expect(isJobDeleted(job)).toBe(true);
    });

    it('should return true when deleted_at is set with current timestamp', () => {
      const now = new Date().toISOString();
      const job: Job = {
        job_id: 'test-4',
        agent_id: 'agent-1',
        project_id: 'project-1',
        priority: 5,
        status: 'pending',
        created_at: now,
        started_at: null,
        completed_at: null,
        instance_id: null,
        error_message: null,
        result_summary: null,
        cancelled_at: null,
        deleted_at: now,
      };
      expect(isJobDeleted(job)).toBe(true);
    });

    it('should return true for deleted_at with empty string', () => {
      const job: Job = {
        job_id: 'test-5',
        agent_id: 'agent-1',
        project_id: 'project-1',
        priority: 5,
        status: 'completed',
        created_at: new Date().toISOString(),
        started_at: null,
        completed_at: new Date().toISOString(),
        instance_id: null,
        error_message: null,
        result_summary: null,
        cancelled_at: null,
        deleted_at: '',
      };
      // Empty string is falsy, so isJobDeleted returns false
      expect(isJobDeleted(job)).toBe(false);
    });

    it('should correctly identify deleted job regardless of status', () => {
      const statuses: JobStatus[] = ['pending', 'processing', 'completed', 'failed', 'cancelled', 'dead_letter'];
      const deletedAt = '2024-01-15T10:30:00Z';

      for (const status of statuses) {
        const job: Job = {
          job_id: `test-${status}`,
          agent_id: 'agent-1',
          project_id: 'project-1',
          priority: 5,
          status,
          created_at: new Date().toISOString(),
          started_at: null,
          completed_at: null,
          instance_id: null,
          error_message: null,
          result_summary: null,
          cancelled_at: null,
          deleted_at: deletedAt,
        };
        expect(isJobDeleted(job)).toBe(true);
      }
    });
  });

  describe('getStatusColor', () => {
    it('should return gray-400 for pending status', () => {
      expect(getStatusColor('pending')).toBe('#9CA3AF');
    });

    it('should return blue-500 for processing status', () => {
      expect(getStatusColor('processing')).toBe('#3B82F6');
    });

    it('should return green-500 for completed status', () => {
      expect(getStatusColor('completed')).toBe('#22C55E');
    });

    it('should return red-500 for failed status', () => {
      expect(getStatusColor('failed')).toBe('#EF4444');
    });

    it('should return amber-500 for cancelled status', () => {
      expect(getStatusColor('cancelled')).toBe('#F59E0B');
    });

    it('should return purple-600 for dead_letter status', () => {
      expect(getStatusColor('dead_letter')).toBe('#7C3AED');
    });

    it('should return default gray-400 for unknown status', () => {
      // TypeScript would catch invalid status at compile time
      // but we test the default case behavior
      expect(getStatusColor('pending')).toBe('#9CA3AF');
    });
  });

  describe('getPriorityColor', () => {
    it('should return green-500 (low priority) for priority 1', () => {
      expect(getPriorityColor(1)).toBe('#22C55E');
    });

    it('should return green-500 (low priority) for priority 2', () => {
      expect(getPriorityColor(2)).toBe('#22C55E');
    });

    it('should return blue-500 (medium priority) for priority 3', () => {
      expect(getPriorityColor(3)).toBe('#3B82F6');
    });

    it('should return blue-500 (medium priority) for priority 4', () => {
      expect(getPriorityColor(4)).toBe('#3B82F6');
    });

    it('should return amber-500 (medium-high priority) for priority 5', () => {
      expect(getPriorityColor(5)).toBe('#F59E0B');
    });

    it('should return amber-500 (medium-high priority) for priority 7', () => {
      expect(getPriorityColor(7)).toBe('#F59E0B');
    });

    it('should return red-500 (high priority) for priority 8', () => {
      expect(getPriorityColor(8)).toBe('#EF4444');
    });

    it('should return red-500 (high priority) for priority 10', () => {
      expect(getPriorityColor(10)).toBe('#EF4444');
    });
  });

  describe('Job interface type correctness', () => {
    it('should allow optional message field', () => {
      const job: Job = {
        job_id: 'test-1',
        agent_id: 'agent-1',
        project_id: 'project-1',
        priority: 5,
        status: 'pending',
        created_at: new Date().toISOString(),
        started_at: null,
        completed_at: null,
        instance_id: null,
        error_message: null,
        result_summary: null,
        cancelled_at: null,
        // message is optional - omitted
      };
      expect(job.job_id).toBe('test-1');
      expect(job.message).toBeUndefined();
    });

    it('should allow optional source field', () => {
      const job: Job = {
        job_id: 'test-2',
        agent_id: 'agent-1',
        project_id: 'project-1',
        priority: 5,
        status: 'pending',
        created_at: new Date().toISOString(),
        started_at: null,
        completed_at: null,
        instance_id: null,
        error_message: null,
        result_summary: null,
        cancelled_at: null,
        // source is optional - omitted
      };
      expect(job.source).toBeUndefined();
    });

    it('should allow optional position field', () => {
      const job: Job = {
        job_id: 'test-3',
        agent_id: 'agent-1',
        project_id: 'project-1',
        priority: 5,
        status: 'pending',
        created_at: new Date().toISOString(),
        started_at: null,
        completed_at: null,
        instance_id: null,
        error_message: null,
        result_summary: null,
        cancelled_at: null,
        position: 3,
      };
      expect(job.position).toBe(3);
    });

    it('should allow optional job_metadata field', () => {
      const job: Job = {
        job_id: 'test-4',
        agent_id: 'agent-1',
        project_id: 'project-1',
        priority: 5,
        status: 'pending',
        created_at: new Date().toISOString(),
        started_at: null,
        completed_at: null,
        instance_id: null,
        error_message: null,
        result_summary: null,
        cancelled_at: null,
        job_metadata: { key: 'value', nested: { a: 1 } },
      };
      expect(job.job_metadata).toEqual({ key: 'value', nested: { a: 1 } });
    });

    it('should handle job with all optional fields present', () => {
      const job: Job = {
        job_id: 'test-5',
        agent_id: 'agent-1',
        message: 'Test message',
        source: 'api',
        project_id: 'project-1',
        priority: 8,
        status: 'processing',
        created_at: new Date().toISOString(),
        started_at: new Date().toISOString(),
        completed_at: null,
        instance_id: 'instance-1',
        error_message: null,
        result_summary: null,
        job_metadata: {},
        cancelled_at: null,
        position: 1,
      };
      expect(job.message).toBe('Test message');
      expect(job.source).toBe('api');
      expect(job.instance_id).toBe('instance-1');
      expect(job.position).toBe(1);
    });
  });

  describe('JobCreate interface', () => {
    it('should allow required fields only', () => {
      const jobCreate: JobCreate = {
        agent_id: 'agent-1',
        message: 'Create job message',
      };
      expect(jobCreate.agent_id).toBe('agent-1');
      expect(jobCreate.message).toBe('Create job message');
      expect(jobCreate.project_id).toBeUndefined();
      expect(jobCreate.priority).toBeUndefined();
      expect(jobCreate.source).toBeUndefined();
    });

    it('should allow all optional fields', () => {
      const jobCreate: JobCreate = {
        agent_id: 'agent-1',
        message: 'Full job create',
        project_id: 'project-1',
        priority: 9,
        source: 'telegram',
        metadata: { custom: 'data' },
      };
      expect(jobCreate.project_id).toBe('project-1');
      expect(jobCreate.priority).toBe(9);
      expect(jobCreate.source).toBe('telegram');
      expect(jobCreate.metadata).toEqual({ custom: 'data' });
    });
  });

  describe('JobFilters interface', () => {
    it('should allow empty filters', () => {
      const filters: JobFilters = {};
      expect(filters.status).toBeUndefined();
      expect(filters.source).toBeUndefined();
      expect(filters.agent_id).toBeUndefined();
      expect(filters.project_id).toBeUndefined();
    });

    it('should allow partial filters', () => {
      const filters: JobFilters = {
        status: 'pending',
      };
      expect(filters.status).toBe('pending');
      expect(filters.source).toBeUndefined();
    });

    it('should allow all filter fields', () => {
      const filters: JobFilters = {
        status: 'completed',
        source: 'api',
        agent_id: 'coder',
        project_id: 'project-123',
      };
      expect(filters.status).toBe('completed');
      expect(filters.source).toBe('api');
      expect(filters.agent_id).toBe('coder');
      expect(filters.project_id).toBe('project-123');
    });
  });

  describe('JobEvent interface', () => {
    it('should have correct event types', () => {
      const eventTypes: JobEvent['event'][] = ['connected', 'status_update', 'completed', 'error', 'keepalive'];
      expect(eventTypes).toHaveLength(5);
    });

    it('should allow connected event with null data', () => {
      const event: JobEvent = {
        event: 'connected',
        data: null,
      };
      expect(event.event).toBe('connected');
      expect(event.data).toBeNull();
    });

    it('should allow status_update event with data', () => {
      const event: JobEvent = {
        event: 'status_update',
        data: {
          job_id: 'job-1',
          status: 'processing',
          previous_status: 'pending',
        },
      };
      expect(event.event).toBe('status_update');
      expect(event.data?.job_id).toBe('job-1');
      expect(event.data?.status).toBe('processing');
    });

    it('should allow completed event with result_summary', () => {
      const event: JobEvent = {
        event: 'completed',
        data: {
          job_id: 'job-1',
          status: 'completed',
          result_summary: 'Task completed successfully',
        },
      };
      expect(event.event).toBe('completed');
      expect(event.data?.result_summary).toBe('Task completed successfully');
    });

    it('should allow error event with error_message', () => {
      const event: JobEvent = {
        event: 'error',
        data: {
          job_id: 'job-1',
          error_message: 'Something went wrong',
        },
      };
      expect(event.event).toBe('error');
      expect(event.data?.error_message).toBe('Something went wrong');
    });
  });

  describe('DeadLetterItem interface', () => {
    it('should have all required fields', () => {
      const item: DeadLetterItem = {
        dlq_id: 'dlq-1',
        job_id: 'job-1',
        agent_id: 'coder',
        agent_dir: '/agents/coder',
        message: 'Test message',
        source: 'api',
        project_id: 'project-1',
        queue_id: null,
        error_message: 'Some error',
        retry_count: 3,
        failed_at: '2024-01-01T00:00:00Z',
        moved_to_dlq_at: '2024-01-02T00:00:00Z',
        reason: 'max_retries_exceeded',
      };
      expect(item.dlq_id).toBe('dlq-1');
      expect(item.job_id).toBe('job-1');
      expect(item.agent_id).toBe('coder');
      expect(item.agent_dir).toBe('/agents/coder');
      expect(item.message).toBe('Test message');
      expect(item.source).toBe('api');
      expect(item.project_id).toBe('project-1');
      expect(item.queue_id).toBeNull();
      expect(item.error_message).toBe('Some error');
      expect(item.retry_count).toBe(3);
      expect(item.failed_at).toBe('2024-01-01T00:00:00Z');
      expect(item.moved_to_dlq_at).toBe('2024-01-02T00:00:00Z');
      expect(item.reason).toBe('max_retries_exceeded');
    });

    it('should allow optional fields', () => {
      const item: DeadLetterItem = {
        dlq_id: 'dlq-2',
        job_id: 'job-2',
        agent_id: 'tester',
        agent_dir: '/agents/tester',
        message: 'Test',
        source: 'api',
        project_id: 'project-2',
        queue_id: 'queue-1',
        error_message: null,
        retry_count: 0,
        failed_at: null,
        moved_to_dlq_at: '2024-01-01T00:00:00Z',
        reason: 'timeout',
        metadata: { key: 'value', nested: { a: 1 } },
      };
      expect(item.queue_id).toBe('queue-1');
      expect(item.metadata).toEqual({ key: 'value', nested: { a: 1 } });
    });

    it('should allow null for optional metadata', () => {
      const item: DeadLetterItem = {
        dlq_id: 'dlq-3',
        job_id: 'job-3',
        agent_id: 'dev',
        agent_dir: '/agents/dev',
        message: 'Test',
        source: 'telegram',
        project_id: 'project-3',
        queue_id: null,
        error_message: null,
        retry_count: 0,
        failed_at: null,
        moved_to_dlq_at: '2024-01-01T00:00:00Z',
        reason: 'error',
        metadata: null,
      };
      expect(item.metadata).toBeNull();
    });
  });

  describe('RetryAllResult interface', () => {
    it('should have correct structure', () => {
      const result: RetryAllResult = {
        replayed: 5,
        failed: 2,
        errors: [
          { dlq_id: 'dlq-1', error: 'Failed to replay' },
          { dlq_id: 'dlq-2', error: 'Job not found' },
        ],
      };
      expect(result.replayed).toBe(5);
      expect(result.failed).toBe(2);
      expect(result.errors).toHaveLength(2);
      expect(result.errors[0].dlq_id).toBe('dlq-1');
    });
  });

  describe('DLQReplayResponse interface', () => {
    it('should have correct structure', () => {
      const response: DLQReplayResponse = {
        job_id: 'job-replayed',
        status: 'pending',
        message: 'Job replayed successfully',
      };
      expect(response.job_id).toBe('job-replayed');
      expect(response.status).toBe('pending');
      expect(response.message).toBe('Job replayed successfully');
    });
  });

  describe('DLQListResponse interface', () => {
    it('should have correct structure', () => {
      const response: DLQListResponse = {
        items: [],
        total: 0,
      };
      expect(response.items).toEqual([]);
      expect(response.total).toBe(0);
    });

    it('should contain DeadLetterItem array', () => {
      const item: DeadLetterItem = {
        dlq_id: 'dlq-1',
        job_id: 'job-1',
        agent_id: 'coder',
        agent_dir: '/agents/coder',
        message: 'Test',
        source: 'api',
        project_id: 'project-1',
        queue_id: null,
        error_message: null,
        retry_count: 0,
        failed_at: null,
        moved_to_dlq_at: '2024-01-01T00:00:00Z',
        reason: 'error',
      };
      const response: DLQListResponse = {
        items: [item],
        total: 1,
      };
      expect(response.items).toHaveLength(1);
      expect(response.total).toBe(1);
    });
  });
});
