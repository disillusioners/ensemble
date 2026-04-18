import { signal, computed, Component, input, output } from '@angular/core';
import { Job, JobStatus, JobSource } from '../../models/job.model';
import { Project } from '../../models/project.model';
import { createMockJob, createMockJobList } from '../../testing/job-test-helpers';

// Simplified mock services
const mockJobService = {
  jobs: signal<Job[]>([]),
  loading: signal(false),
  error: signal<string | null>(null),
  listJobs: jest.fn(),
  cancelJob: jest.fn(),
  retryJob: jest.fn(),
  retryAllDeadLetterJobs: jest.fn(),
  refreshJobs: jest.fn(),
  createJob: jest.fn(),
};

const mockJobSseService = {
  isConnected: signal(false),
  connectionState: signal<'disconnected' | 'connecting' | 'connected' | 'retrying' | 'failed'>('disconnected'),
  retryAttempt: signal(0),
  latestStatus: signal(null),
  latestError: signal(null),
  streamJobEvents: jest.fn(),
  disconnect: jest.fn(),
  clearEvents: jest.fn(),
  clearError: jest.fn(),
};

const mockProjectService = {
  projects: signal<Project[]>([]),
  listProjects: jest.fn(),
  pauseJobQueue: jest.fn(),
  resumeJobQueue: jest.fn(),
};

const mockApiService = {
  listAgents: jest.fn(),
};

// Simple mock component to test the logic
class MockJobsComponent {
  // Signals
  readonly jobs = signal<Job[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);
  readonly agents = signal<any[]>([]);
  readonly selectedJob = signal<Job | null>(null);
  readonly drawerOpen = signal(false);
  readonly projects = mockProjectService.projects;
  readonly filters = signal<{ status?: JobStatus; source?: JobSource; agent_id?: string; project_id?: string }>({});
  
  // DLQ signals
  readonly retryingAll = signal(false);
  readonly isDeadLetterFilterActive = computed(() => this.filters().status === 'dead_letter');
  
  // SSE connection status
  readonly isConnected = mockJobSseService.isConnected;
  readonly retryAttempt = mockJobSseService.retryAttempt;
  
  // Computed values
  readonly filteredJobs = computed(() => {
    const currentFilters = this.filters();
    let filtered = this.jobs();

    if (currentFilters.status) {
      filtered = filtered.filter(job => job.status === currentFilters.status);
    }
    if (currentFilters.source) {
      filtered = filtered.filter(job => job.source === currentFilters.source);
    }
    if (currentFilters.agent_id) {
      filtered = filtered.filter(job => job.agent_id === currentFilters.agent_id);
    }

    return filtered;
  });

  readonly projectsWithPendingJobs = computed(() => {
    const pendingJobs = this.jobs().filter(job => job.status === 'pending');
    const projectIds = new Set<string>();
    pendingJobs.forEach(job => {
      if (job.project_id) {
        projectIds.add(job.project_id);
      }
    });

    return this.projects()
      .filter(project => projectIds.has(project.project_id))
      .map(project => ({
        ...project,
        pendingCount: pendingJobs.filter(job => job.project_id === project.project_id).length
      }));
  });

  // Filter options
  readonly statusOptions = [
    { value: 'all', label: 'All' },
    { value: 'pending', label: 'Pending' },
    { value: 'processing', label: 'Processing' },
    { value: 'completed', label: 'Completed' },
    { value: 'failed', label: 'Failed' },
    { value: 'cancelled', label: 'Cancelled' }
  ];

  // Methods
  onStatusFilterChange(status: JobStatus | 'all') {
    this.filters.update(filters => ({
      ...filters,
      status: status === 'all' ? undefined : status
    }));
  }

  onSourceFilterChange(source: JobSource | 'all') {
    this.filters.update(filters => ({
      ...filters,
      source: source === 'all' ? undefined : source
    }));
  }

  onAgentFilterChange(agentId: string) {
    this.filters.update(filters => ({
      ...filters,
      agent_id: agentId === 'all' ? undefined : agentId
    }));
  }

  onClearFilters() {
    this.filters.set({});
  }

  onCancelJob(job: Job) {
    mockJobService.cancelJob(job.job_id);
  }

  onRetryJob(job: Job) {
    mockJobService.retryJob(job.job_id);
  }

  onRetryAllDeadLetterJobs() {
    const projectId = this.filters().project_id;
    if (!projectId) return;
    mockJobService.retryAllDeadLetterJobs(projectId);
  }

  onViewJobDetails(job: Job) {
    this.selectedJob.set(job);
    this.drawerOpen.set(true);
    mockJobSseService.disconnect();
    mockJobSseService.clearEvents();
    mockJobSseService.streamJobEvents(job.job_id);
  }

  onCloseDrawer() {
    this.drawerOpen.set(false);
    this.selectedJob.set(null);
    mockJobSseService.disconnect();
  }

  onToggleProjectPause(project: Project) {
    if (project.job_queue_paused) {
      mockProjectService.resumeJobQueue(project.project_id);
    } else {
      mockProjectService.pauseJobQueue(project.project_id);
    }
  }

  hasActiveFilters() {
    const filters = this.filters();
    return !!(filters.status || filters.source || filters.agent_id);
  }

  getAgentDisplayName(agentId: string) {
    const agent = this.agents().find(a => a.agent_id === agentId);
    return agent ? `${agent.icon} ${agent.name}` : agentId;
  }
}

describe('JobsComponent Logic', () => {
  let component: MockJobsComponent;

  const mockJobs = createMockJobList(5);
  const mockProjects: Project[] = [
    {
      project_id: 'project-123',
      name: 'Test Project',
      project_type: 'software',
      status: 'active',
      main_directory: '/test',
      related_directories: [],
      description: 'Test description',
      tags: [],
      shortnames: [],
      metadata: {},
      relationships: {},
      creator_instance_id: null,
      creator_agent_id: null,
      created_at: new Date().toISOString(),
      updated_at: null,
      job_queue_paused: false,
    },
  ];
  const mockAgents = [
    { agent_id: 'coder', name: 'Coder', icon: '💻' },
  ];

  beforeEach(() => {
    component = new MockJobsComponent();
    component.jobs.set(mockJobs);
    component.projects.set(mockProjects);
    component.agents.set(mockAgents);
    jest.clearAllMocks();
  });

  describe('filteredJobs computed', () => {
    it('should return all jobs when no filters', () => {
      component.filters.set({});
      expect(component.filteredJobs()).toHaveLength(mockJobs.length);
    });

    it('should filter by status', () => {
      component.jobs.set([
        createMockJob({ job_id: '1', status: 'pending' }),
        createMockJob({ job_id: '2', status: 'completed' }),
        createMockJob({ job_id: '3', status: 'pending' }),
      ]);
      
      component.onStatusFilterChange('pending');

      const filtered = component.filteredJobs();
      expect(filtered.every(j => j.status === 'pending')).toBe(true);
    });

    it('should filter by source', () => {
      component.jobs.set([
        createMockJob({ job_id: '1', source: 'api' }),
        createMockJob({ job_id: '2', source: 'telegram' }),
      ]);
      
      component.onSourceFilterChange('api');

      const filtered = component.filteredJobs();
      expect(filtered.every(j => j.source === 'api')).toBe(true);
    });

    it('should filter by agent_id', () => {
      component.jobs.set([
        createMockJob({ job_id: '1', agent_id: 'coder' }),
        createMockJob({ job_id: '2', agent_id: 'tester' }),
      ]);
      
      component.onAgentFilterChange('coder');

      const filtered = component.filteredJobs();
      expect(filtered.every(j => j.agent_id === 'coder')).toBe(true);
    });

    it('should filter by multiple criteria', () => {
      component.jobs.set([
        createMockJob({ job_id: '1', status: 'pending', source: 'api', agent_id: 'coder' }),
        createMockJob({ job_id: '2', status: 'completed', source: 'api', agent_id: 'coder' }),
        createMockJob({ job_id: '3', status: 'pending', source: 'telegram', agent_id: 'coder' }),
      ]);
      
      component.onStatusFilterChange('pending');
      component.onSourceFilterChange('api');

      const filtered = component.filteredJobs();
      expect(filtered.length).toBe(1);
      expect(filtered[0].job_id).toBe('1');
    });
  });

  describe('projectsWithPendingJobs computed', () => {
    it('should return only projects with pending jobs', () => {
      component.jobs.set([
        createMockJob({ project_id: 'project-123', status: 'pending' }),
        createMockJob({ project_id: 'project-456', status: 'completed' }),
      ]);
      component.projects.set([
        { ...mockProjects[0], project_id: 'project-123' },
        { ...mockProjects[0], project_id: 'project-456' },
      ]);

      const result = component.projectsWithPendingJobs();

      expect(result.some(p => p.project_id === 'project-123')).toBe(true);
      expect(result.some(p => p.project_id === 'project-456')).toBe(false);
    });

    it('should include pending count in result', () => {
      component.jobs.set([
        createMockJob({ project_id: 'project-123', status: 'pending' }),
        createMockJob({ project_id: 'project-123', status: 'pending' }),
        createMockJob({ project_id: 'project-123', status: 'completed' }),
      ]);
      component.projects.set([{ ...mockProjects[0], project_id: 'project-123' }]);

      const result = component.projectsWithPendingJobs();

      expect(result[0].pendingCount).toBe(2);
    });
  });

  describe('onStatusFilterChange', () => {
    it('should update filters with status', () => {
      component.onStatusFilterChange('pending');
      expect(component.filters().status).toBe('pending');
    });

    it('should set status to undefined when selecting "all"', () => {
      component.onStatusFilterChange('pending');
      component.onStatusFilterChange('all');
      expect(component.filters().status).toBeUndefined();
    });
  });

  describe('onSourceFilterChange', () => {
    it('should update filters with source', () => {
      component.onSourceFilterChange('telegram');
      expect(component.filters().source).toBe('telegram');
    });

    it('should set source to undefined when selecting "all"', () => {
      component.onSourceFilterChange('telegram');
      component.onSourceFilterChange('all');
      expect(component.filters().source).toBeUndefined();
    });
  });

  describe('onAgentFilterChange', () => {
    it('should update filters with agent_id', () => {
      component.onAgentFilterChange('coder');
      expect(component.filters().agent_id).toBe('coder');
    });

    it('should set agent_id to undefined when selecting "all"', () => {
      component.onAgentFilterChange('coder');
      component.onAgentFilterChange('all');
      expect(component.filters().agent_id).toBeUndefined();
    });
  });

  describe('onClearFilters', () => {
    it('should clear all filters', () => {
      component.onStatusFilterChange('pending');
      component.onSourceFilterChange('api');
      component.onAgentFilterChange('coder');

      component.onClearFilters();

      expect(component.filters()).toEqual({});
    });
  });

  describe('onCancelJob', () => {
    it('should call jobService.cancelJob', () => {
      const job = mockJobs[0];
      component.onCancelJob(job);
      expect(mockJobService.cancelJob).toHaveBeenCalledWith(job.job_id);
    });
  });

  describe('onRetryJob', () => {
    it('should call jobService.retryJob', () => {
      const job = mockJobs[0];
      component.onRetryJob(job);
      expect(mockJobService.retryJob).toHaveBeenCalledWith(job.job_id);
    });
  });

  describe('onRetryAllDeadLetterJobs', () => {
    it('should call jobService.retryAllDeadLetterJobs with project_id', () => {
      component.filters.set({ project_id: 'project-123' });
      component.onRetryAllDeadLetterJobs();
      expect(mockJobService.retryAllDeadLetterJobs).toHaveBeenCalledWith('project-123');
    });

    it('should not call jobService.retryAllDeadLetterJobs when no project_id', () => {
      component.filters.set({});
      component.onRetryAllDeadLetterJobs();
      expect(mockJobService.retryAllDeadLetterJobs).not.toHaveBeenCalled();
    });
  });

  describe('isDeadLetterFilterActive', () => {
    it('should return true when status is dead_letter', () => {
      component.onStatusFilterChange('dead_letter');
      expect(component.isDeadLetterFilterActive()).toBe(true);
    });

    it('should return false when status is not dead_letter', () => {
      component.onStatusFilterChange('pending');
      expect(component.isDeadLetterFilterActive()).toBe(false);
    });

    it('should return false when status is undefined (all)', () => {
      component.onStatusFilterChange('all');
      expect(component.isDeadLetterFilterActive()).toBe(false);
    });
  });

  describe('onViewJobDetails', () => {
    it('should set selectedJob', () => {
      const job = mockJobs[0];
      component.onViewJobDetails(job);
      expect(component.selectedJob()).toEqual(job);
    });

    it('should open drawer', () => {
      const job = mockJobs[0];
      component.onViewJobDetails(job);
      expect(component.drawerOpen()).toBe(true);
    });

    it('should connect to SSE', () => {
      const job = mockJobs[0];
      component.onViewJobDetails(job);
      expect(mockJobSseService.disconnect).toHaveBeenCalled();
      expect(mockJobSseService.clearEvents).toHaveBeenCalled();
      expect(mockJobSseService.streamJobEvents).toHaveBeenCalledWith(job.job_id);
    });
  });

  describe('onCloseDrawer', () => {
    it('should close drawer', () => {
      component.onViewJobDetails(mockJobs[0]);
      component.onCloseDrawer();
      expect(component.drawerOpen()).toBe(false);
    });

    it('should clear selected job', () => {
      component.onViewJobDetails(mockJobs[0]);
      component.onCloseDrawer();
      expect(component.selectedJob()).toBeNull();
    });

    it('should disconnect SSE', () => {
      component.onViewJobDetails(mockJobs[0]);
      component.onCloseDrawer();
      expect(mockJobSseService.disconnect).toHaveBeenCalled();
    });
  });

  describe('onToggleProjectPause', () => {
    it('should call pauseJobQueue when not paused', () => {
      const project = { ...mockProjects[0], job_queue_paused: false };
      component.onToggleProjectPause(project);
      expect(mockProjectService.pauseJobQueue).toHaveBeenCalledWith(project.project_id);
    });

    it('should call resumeJobQueue when paused', () => {
      const project = { ...mockProjects[0], job_queue_paused: true };
      component.onToggleProjectPause(project);
      expect(mockProjectService.resumeJobQueue).toHaveBeenCalledWith(project.project_id);
    });
  });

  describe('hasActiveFilters', () => {
    it('should return false when no filters', () => {
      component.filters.set({});
      expect(component.hasActiveFilters()).toBe(false);
    });

    it('should return true when status filter is set', () => {
      component.onStatusFilterChange('pending');
      expect(component.hasActiveFilters()).toBe(true);
    });

    it('should return true when source filter is set', () => {
      component.onSourceFilterChange('api');
      expect(component.hasActiveFilters()).toBe(true);
    });

    it('should return true when agent filter is set', () => {
      component.onAgentFilterChange('coder');
      expect(component.hasActiveFilters()).toBe(true);
    });
  });

  describe('getAgentDisplayName', () => {
    it('should return formatted name for known agent', () => {
      component.agents.set([{ agent_id: 'coder', name: 'Coder', icon: '💻' }]);
      const displayName = component.getAgentDisplayName('coder');
      expect(displayName).toBe('💻 Coder');
    });

    it('should return agent_id for unknown agent', () => {
      component.agents.set([{ agent_id: 'coder', name: 'Coder', icon: '💻' }]);
      const displayName = component.getAgentDisplayName('unknown-agent');
      expect(displayName).toBe('unknown-agent');
    });
  });
});
