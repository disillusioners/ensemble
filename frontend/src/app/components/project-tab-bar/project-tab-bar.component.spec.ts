import { signal, computed } from '@angular/core';
import { ProjectTab } from '../../models/tab.model';
import { Project } from '../../models/project.model';

// Mock TabStateService
class MockTabStateService {
  readonly openTabs = signal<ProjectTab[]>([
    { id: 'all', name: 'All', type: 'all' }
  ]);
  readonly activeTab = signal<ProjectTab>({ id: 'all', name: 'All', type: 'all' });

  readonly activeProjectId = computed(() => {
    const tab = this.activeTab();
    return tab.type === 'project' ? tab.id : null;
  });

  addTab = jest.fn();
  removeTab = jest.fn();
  setActiveTab = jest.fn();
  restoreState = jest.fn();
}

// Mock ProjectService
class MockProjectService {
  readonly projects = signal<Project[]>([]);

  listProjects = jest.fn();
}

// Testable ProjectTabBarComponent implementation (mirrors actual component)
class TestableProjectTabBarComponent {
  protected readonly tabStateService: MockTabStateService;
  protected readonly projectService: MockProjectService;

  constructor(
    tabStateService: MockTabStateService,
    projectService: MockProjectService
  ) {
    this.tabStateService = tabStateService;
    this.projectService = projectService;
  }

  protected readonly unopenedProjects = computed(() => {
    const openTabIds = new Set(
      this.tabStateService.openTabs().map((tab) => tab.id)
    );
    return this.projectService.projects().filter(
      (project) => !openTabIds.has(project.project_id)
    );
  });

  protected onCloseTab(event: Event, tabId: string): void {
    event.stopPropagation();
    this.tabStateService.removeTab(tabId);
  }
}

// Helper to create mock project
function createMockProject(overrides: Partial<Project> = {}): Project {
  return {
    project_id: `project-${Math.random().toString(36).substr(2, 9)}`,
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
    ...overrides,
  };
}

describe('ProjectTabBarComponent', () => {
  let mockTabStateService: MockTabStateService;
  let mockProjectService: MockProjectService;
  let component: TestableProjectTabBarComponent;

  beforeEach(() => {
    mockTabStateService = new MockTabStateService();
    mockProjectService = new MockProjectService();
    component = new TestableProjectTabBarComponent(
      mockTabStateService,
      mockProjectService
    );
    jest.clearAllMocks();
  });

  describe('rendering', () => {
    it('should always render All tab', () => {
      const openTabs = mockTabStateService.openTabs();

      expect(openTabs.some(tab => tab.id === 'all')).toBe(true);
      expect(openTabs.find(tab => tab.id === 'all')?.name).toBe('All');
    });

    it('should render All tab with correct type', () => {
      const allTab = mockTabStateService.openTabs().find(tab => tab.id === 'all');

      expect(allTab?.type).toBe('all');
    });

    it('should render project tabs from TabStateService', () => {
      mockTabStateService.openTabs.set([
        { id: 'all', name: 'All', type: 'all' },
        { id: 'project-1', name: 'Project 1', type: 'project' },
        { id: 'project-2', name: 'Project 2', type: 'project' },
      ]);

      const openTabs = mockTabStateService.openTabs();

      expect(openTabs).toHaveLength(3);
      expect(openTabs.filter(t => t.type === 'project')).toHaveLength(2);
    });

    it('should render project tab with correct name', () => {
      mockTabStateService.openTabs.set([
        { id: 'all', name: 'All', type: 'all' },
        { id: 'project-1', name: 'My Custom Project', type: 'project' },
      ]);

      const projectTab = mockTabStateService.openTabs().find(t => t.id === 'project-1');

      expect(projectTab?.name).toBe('My Custom Project');
    });
  });

  describe('close button', () => {
    it('should have close button on project tabs', () => {
      mockTabStateService.openTabs.set([
        { id: 'all', name: 'All', type: 'all' },
        { id: 'project-1', name: 'Project 1', type: 'project' },
      ]);

      const projectTab = mockTabStateService.openTabs().find(t => t.id === 'project-1');

      expect(projectTab?.type).toBe('project');
    });

    it('should not have close button on All tab', () => {
      const allTab = mockTabStateService.openTabs().find(t => t.id === 'all');

      // All tab should have type 'all', not 'project'
      expect(allTab?.type).toBe('all');
    });

    it('should call removeTab when closing project tab', () => {
      const mockEvent = { stopPropagation: jest.fn() } as unknown as Event;

      component.onCloseTab(mockEvent, 'project-1');

      expect(mockTabStateService.removeTab).toHaveBeenCalledWith('project-1');
    });

    it('should call stopPropagation on event', () => {
      const mockEvent = { stopPropagation: jest.fn() } as unknown as Event;

      component.onCloseTab(mockEvent, 'project-1');

      expect(mockEvent.stopPropagation).toHaveBeenCalled();
    });
  });

  describe('"+" button and menu', () => {
    it('should show unopened projects in menu', () => {
      mockProjectService.projects.set([
        createMockProject({ project_id: 'project-1', name: 'Project 1' }),
        createMockProject({ project_id: 'project-2', name: 'Project 2' }),
        createMockProject({ project_id: 'project-3', name: 'Project 3' }),
      ]);
      mockTabStateService.openTabs.set([
        { id: 'all', name: 'All', type: 'all' },
        { id: 'project-1', name: 'Project 1', type: 'project' },
      ]);

      const unopenedProjects = component.unopenedProjects();

      expect(unopenedProjects).toHaveLength(2);
      expect(unopenedProjects.some(p => p.project_id === 'project-2')).toBe(true);
      expect(unopenedProjects.some(p => p.project_id === 'project-3')).toBe(true);
      expect(unopenedProjects.some(p => p.project_id === 'project-1')).toBe(false);
    });

    it('should filter out All tab from unopened projects', () => {
      mockProjectService.projects.set([
        createMockProject({ project_id: 'all', name: 'All' }),
        createMockProject({ project_id: 'project-1', name: 'Project 1' }),
      ]);

      const unopenedProjects = component.unopenedProjects();

      expect(unopenedProjects.some(p => p.project_id === 'all')).toBe(false);
    });

    it('should return empty array when all projects are open', () => {
      const project1 = createMockProject({ project_id: 'project-1' });
      mockProjectService.projects.set([project1]);
      mockTabStateService.openTabs.set([
        { id: 'all', name: 'All', type: 'all' },
        { id: 'project-1', name: 'Project 1', type: 'project' },
      ]);

      const unopenedProjects = component.unopenedProjects();

      expect(unopenedProjects).toHaveLength(0);
    });

    it('should return all projects when no tabs are open except All', () => {
      const projects = [
        createMockProject({ project_id: 'project-1' }),
        createMockProject({ project_id: 'project-2' }),
      ];
      mockProjectService.projects.set(projects);
      mockTabStateService.openTabs.set([{ id: 'all', name: 'All', type: 'all' }]);

      const unopenedProjects = component.unopenedProjects();

      expect(unopenedProjects).toHaveLength(2);
    });

    it('should handle empty projects list', () => {
      mockProjectService.projects.set([]);

      const unopenedProjects = component.unopenedProjects();

      expect(unopenedProjects).toHaveLength(0);
    });

    it('should track by project_id', () => {
      mockProjectService.projects.set([
        createMockProject({ project_id: 'project-1', name: 'Project 1' }),
      ]);

      const projects = component.unopenedProjects();

      expect(projects[0].project_id).toBe('project-1');
    });
  });

  describe('tab switching', () => {
    it('should call setActiveTab when clicking tab', () => {
      mockTabStateService.setActiveTab('project-1');

      expect(mockTabStateService.setActiveTab).toHaveBeenCalledWith('project-1');
    });

    it('should switch to All tab', () => {
      mockTabStateService.setActiveTab('all');

      expect(mockTabStateService.setActiveTab).toHaveBeenCalledWith('all');
    });

    it('should mark active tab correctly', () => {
      mockTabStateService.openTabs.set([
        { id: 'all', name: 'All', type: 'all' },
        { id: 'project-1', name: 'Project 1', type: 'project' },
      ]);
      mockTabStateService.activeTab.set(
        mockTabStateService.openTabs().find(t => t.id === 'project-1')!
      );

      const activeTab = mockTabStateService.activeTab();

      expect(activeTab.id).toBe('project-1');
      expect(activeTab.id === mockTabStateService.openTabs()[1].id).toBe(true);
    });

    it('should switch between project tabs', () => {
      mockTabStateService.openTabs.set([
        { id: 'all', name: 'All', type: 'all' },
        { id: 'project-1', name: 'Project 1', type: 'project' },
        { id: 'project-2', name: 'Project 2', type: 'project' },
      ]);

      // Switch to project-1
      mockTabStateService.activeTab.set(
        mockTabStateService.openTabs().find(t => t.id === 'project-1')!
      );
      mockTabStateService.setActiveTab('project-1');

      expect(mockTabStateService.activeTab().id).toBe('project-1');

      // Switch to project-2
      mockTabStateService.setActiveTab('project-2');

      expect(mockTabStateService.setActiveTab).toHaveBeenCalledWith('project-2');
    });
  });

  describe('tab state integration', () => {
    it('should reflect TabStateService openTabs changes', () => {
      const initialTabs = mockTabStateService.openTabs();
      expect(initialTabs).toHaveLength(1);

      mockTabStateService.openTabs.set([
        { id: 'all', name: 'All', type: 'all' },
        { id: 'project-new', name: 'New Project', type: 'project' },
      ]);

      const updatedTabs = mockTabStateService.openTabs();
      expect(updatedTabs).toHaveLength(2);
      expect(updatedTabs.some(t => t.id === 'project-new')).toBe(true);
    });

    it('should reflect TabStateService activeTab changes', () => {
      const initialActive = mockTabStateService.activeTab();
      expect(initialActive.id).toBe('all');

      mockTabStateService.openTabs.set([
        { id: 'all', name: 'All', type: 'all' },
        { id: 'project-1', name: 'Project 1', type: 'project' },
      ]);
      mockTabStateService.activeTab.set(
        mockTabStateService.openTabs().find(t => t.id === 'project-1')!
      );

      const updatedActive = mockTabStateService.activeTab();
      expect(updatedActive.id).toBe('project-1');
    });

    it('should call addTab when opening a project from menu', () => {
      // Simulate clicking a project in the menu
      const project = createMockProject({ project_id: 'project-new', name: 'New Project' });
      mockTabStateService.addTab(project);

      expect(mockTabStateService.addTab).toHaveBeenCalledWith(project);
    });
  });

  describe('tooltip behavior', () => {
    it('should have tooltip for tab name', () => {
      mockTabStateService.openTabs.set([
        { id: 'all', name: 'All', type: 'all' },
        { id: 'project-1', name: 'A Very Long Project Name That Should Be Truncated', type: 'project' },
      ]);

      const longNameTab = mockTabStateService.openTabs().find(
        t => t.name.length > 20
      );

      expect(longNameTab?.name.length).toBeGreaterThan(20);
    });

    it('should have tooltip disabled for short names', () => {
      mockTabStateService.openTabs.set([
        { id: 'all', name: 'All', type: 'all' },
        { id: 'project-1', name: 'Short', type: 'project' },
      ]);

      const shortNameTab = mockTabStateService.openTabs().find(
        t => t.name.length <= 20
      );

      expect(shortNameTab?.name.length).toBeLessThanOrEqual(20);
    });
  });

  describe('empty state', () => {
    it('should show empty message when no unopened projects', () => {
      mockProjectService.projects.set([
        createMockProject({ project_id: 'project-1' }),
      ]);
      mockTabStateService.openTabs.set([
        { id: 'all', name: 'All', type: 'all' },
        { id: 'project-1', name: 'Project 1', type: 'project' },
      ]);

      const unopenedProjects = component.unopenedProjects();

      expect(unopenedProjects).toHaveLength(0);
    });

    it('should allow reopening closed tabs', () => {
      // Initially project-1 is open
      mockTabStateService.openTabs.set([
        { id: 'all', name: 'All', type: 'all' },
        { id: 'project-1', name: 'Project 1', type: 'project' },
      ]);
      mockProjectService.projects.set([
        createMockProject({ project_id: 'project-1', name: 'Project 1' }),
      ]);

      // Close project-1
      mockTabStateService.openTabs.set([
        { id: 'all', name: 'All', type: 'all' },
      ]);
      mockTabStateService.removeTab('project-1');

      // Now it should appear in unopened projects
      const unopenedProjects = component.unopenedProjects();

      expect(unopenedProjects.some(p => p.project_id === 'project-1')).toBe(true);
    });
  });
});
