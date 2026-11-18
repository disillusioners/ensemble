import { computed, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
// FE-UI lane marker (group-chat P6 room modal suite): A-P6-23.
import { MatDialog } from '@angular/material/dialog';
import { MatSnackBar } from '@angular/material/snack-bar';
import { of } from 'rxjs';
import { ApiService } from '../../services/api.service';
import { InstanceService } from '../../services/instance.service';
import { InstancesViewStateService } from '../../services/instances-view-state.service';
import { ProjectService } from '../../services/project.service';
import { SseService } from '../../services/sse.service';
import { TabStateService } from '../../services/tab-state.service';
import { WorkspaceOverlayService } from '../../services/workspace-overlay.service';
import type { CommandProgressEvent, InstanceInfo, Message } from '../../models';
import { ProjectTab } from '../../models/tab.model';
import { ChatComponent } from './chat.component';
import {
  RoomDialogComponent,
  ROOM_DIALOG_SIZE,
} from '../../components/room-dialog/room-dialog.component';

// Suite split (P6 tidier T1): this file carries the chatrooms-button
// describe moved VERBATIM out of chat.component.spec.ts. Jest forbids
// importing one spec module from another (the import would re-register
// the parent's describes and double-run them), so the mock helpers the
// block references are MIRRORED here byte-for-byte from the parent's
// definitions; the parent keeps its originals for its own suites.

// The chat interface renders markdown via ngx-markdown. Angular
// validates every entry of a standalone component's `imports` array
// (verifyStandaloneImport) — a bare class is REJECTED the moment the
// component is first instantiated ("must be a standalone component /
// directive / pipe or an NgModule"). The mock is therefore a REAL
// standalone directive carrying the `markdown` selector and the inputs
// the chat interface binds (`data`, `mermaid`) — so a suite that mounts
// the real chat template renders it faithfully.
jest.mock('ngx-markdown', () => {
  const { Directive } = jest.requireActual('@angular/core');
  class MarkdownMockDirective {
    data = '';
    mermaid = '';
  }
  Directive({
    selector: 'markdown',
    standalone: true,
    inputs: ['data', 'mermaid'],
  })(MarkdownMockDirective);
  return { MarkdownModule: MarkdownMockDirective };
});

// Mock TabStateService — mirrors production semantics: `activeProjectId`
// is a computed that returns null when the active tab is the 'all'
// pseudo-project, matching the real TabStateService. `activeSpecialTabId`
// mirrors the production computed that returns the active tab id when
// it is a 'chat' or 'all' special tab, null otherwise. `setActiveTab(tabId)`
// looks up the tab in openTabs and falls back to constructing a
// project-type tab on the fly so tests that switch to non-existent
// project ids still drive the computed.
class MockTabStateService {
  readonly openTabs = signal<ProjectTab[]>([
    { id: 'all', name: 'All', type: 'all' },
    { id: 'chat', name: 'Chat', type: 'chat' },
  ]);
  readonly activeTab = signal<ProjectTab>({ id: 'all', name: 'All', type: 'all' });

  readonly activeProjectId = computed(() => {
    const tab = this.activeTab();
    return tab.type === 'project' ? tab.id : null;
  });

  // F1 fix: mirrors production activeSpecialTabId() in
  // tab-state.service.ts:47-53. Returns the active tab id when the
  // tab is a 'chat' or 'all' special tab; null otherwise. The
  // composition `activeProjectId() ?? activeSpecialTabId() ?? 'all'`
  // in chat.component.ts (and the sibling instance-list/home
  // components) relies on this fallback.
  readonly activeSpecialTabId = computed(() => {
    const tab = this.activeTab();
    if (tab.type === 'all' || tab.type === 'chat') {
      return tab.id;
    }
    return null;
  });

  setActiveTabCalls: string[] = [];
  setActiveTab(tabId: string): void {
    this.setActiveTabCalls.push(tabId);
    const tab = this.openTabs().find((t) => t.id === tabId);
    if (tab) {
      this.activeTab.set(tab);
      return;
    }
    // Tests may switch to a project id that hasn't been opened as a tab.
    // Production would silently no-op, but the test mock synthesizes a
    // project-type tab so the computed `activeProjectId` reflects the
    // simulated switch.
    this.activeTab.set({ id: tabId, name: tabId, type: 'project' });
  }
}
// Mock ApiService for testing
const mockApiService = {
  listAgents: jest.fn(),
  getInstance: jest.fn(),
  getMessages: jest.fn(),
  getTodos: jest.fn(),
  sendMessage: jest.fn(),
  deleteInstance: jest.fn(),
  pauseInstance: jest.fn(),
  resumeInstance: jest.fn(),
  setWatchover: jest.fn(),
  createInstance: jest.fn(),
};
// Mock InstanceService for testing
const mockInstanceService = {
  startPolling: jest.fn(),
  stopPolling: jest.fn(),
  isPolling: jest.fn(() => false),
  instances: signal<InstanceInfo[]>([]),
};
// Mock SseService for testing
const mockSseService = {
  messages: signal<Message[]>([]),
  events: signal<any[]>([]),
  statusChange: signal<{ instance_id: string; status: string } | null>(null),
  isStreaming: signal(false),
  latestError: signal<any>(null),
  todos: signal<any[]>([]),
  // Reconnect-refetch + pending-purge triggers (message-display-latency
  // §4.3 items 10 + 11). The real service bumps these on SSE events; in
  // tests they're plain signals tests can drive directly.
  refetchRequest: signal<number>(0),
  pendingPurgeRequest: signal<number>(0),
  // MIN-3: which instance the latest purge bump refers to — the
  // component effect compares it against ``activeInstanceId()``.
  pendingPurgeInstanceId: signal<string | null>(null),
  // Phase 2 / slash-commands: latest ``command_progress`` event mirror.
  // The component effect feeds it into CommandStateService (guarded by
  // the active instance id); tests can drive it directly.
  commandProgress: signal<CommandProgressEvent | null>(null),
  connect: jest.fn(),
  disconnect: jest.fn(),
  clearEvents: jest.fn(),
  fetchPendingInjection: jest.fn(),
  fetchPendingQuestion: jest.fn(),
};
// Mock MatSnackBar for testing
const mockSnackBar = {
  open: jest.fn(),
};
// Mock WorkspaceOverlayService — mirrors production semantics:
// `showWorkspace` and `workspaceProjectId` are independent
// WritableSignals, and the toggle/hide/show methods mutate them with
// the same contract as the real service. Tests construct one of these
// and pass it to the testable component (or provide it via TestBed),
// so they observe exactly the same state transitions production would.
class MockWorkspaceOverlayService {
  readonly showWorkspace = signal(false);
  readonly workspaceProjectId = signal<string | null>(null);

  toggle(projectId?: string): void {
    const currentId = this.workspaceProjectId();
    const targetId = projectId ?? currentId;
    if (targetId === null) return;

    if (this.showWorkspace() && currentId === targetId) {
      this.showWorkspace.set(false);
      return;
    }
    this.workspaceProjectId.set(targetId);
    this.showWorkspace.set(true);
  }

  hide(): void {
    this.showWorkspace.set(false);
  }

  show(projectId: string): void {
    this.workspaceProjectId.set(projectId);
    this.showWorkspace.set(true);
  }
}
// Mock InstancesViewStateService — mirrors the surface ChatComponent
// actually reads/writes: `activeInstanceId`, `activeProjectId`,
// `detailVisible` signals and `clearInstance()` / `closeDetail()`
// methods. Tests pass it to the testable component so the production
// `viewState.clearInstance(instanceId)` flow on termination is
// observable.
class MockInstancesViewStateService {
  readonly activeInstanceId = signal<string | null>(null);
  readonly activeProjectId = signal<string>('all');
  readonly detailVisible = signal(false);

  clearInstanceCalls: string[] = [];
  openDetailCalls: Array<{ projectId: string; instanceId: string }> = [];
  closeDetailCalls = 0;

  openDetail(projectId: string, instanceId: string): void {
    this.openDetailCalls.push({ projectId, instanceId });
    this.activeProjectId.set(projectId || 'all');
    this.activeInstanceId.set(instanceId);
    this.detailVisible.set(true);
  }

  closeDetail(): void {
    this.closeDetailCalls++;
    this.detailVisible.set(false);
  }

  clearInstance(instanceId: string): void {
    this.clearInstanceCalls.push(instanceId);
    if (this.activeInstanceId() !== instanceId) return;
    this.activeInstanceId.set(null);
    this.detailVisible.set(false);
  }
}

// Helper to create mock instance
function createMockInstance(overrides: Partial<InstanceInfo> = {}): InstanceInfo {
  return {
    instance_id: 'instance-' + Math.random().toString(36).substr(2, 9),
    agent_id: 'test-agent',
    status: 'running',
    parent_id: null,
    children: [],
    title: null,
    created_at: new Date().toISOString(),
    updated_at: null,
    project_id: null,
    ...overrides,
  };
}

// ─────────────────────────────────────────────────────────────────────────
// P6 — the chatrooms button on the instances surface.
//
// Architecture recommendation §13.1 (binding): "CHATROOM ICON next to
// the WATCHOVER button on the frontend instances surface. Click → BIG
// POPUP (large modal, NOT a cramped tooltip)".
//
// These tests mount the REAL template (no `template: ''` override — the
// button must be asserted where the operator actually sees it) and
// assert the DOM placement + the dialog wiring.
// ─────────────────────────────────────────────────────────────────────────
describe('ChatComponent chatrooms button (P6) — real template wiring', () => {
  let fixture: ComponentFixture<ChatComponent>;
  let component: ChatComponent;
  let dialogOpen: jest.Mock;
  let viewState: MockInstancesViewStateService;

  beforeEach(async () => {
    // jsdom gap: the chat interface calls scrollIntoView when it renders
    // the message list (the house stub — see chat-interface.component.spec.ts).
    Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? jest.fn();
    localStorage.clear();
    jest.clearAllMocks();
    mockSseService.messages.set([]);
    mockSseService.events.set([]);
    mockSseService.statusChange.set(null);
    mockSseService.latestError.set(null);
    mockSseService.todos.set([]);
    mockInstanceService.instances.set([]);

    mockApiService.listAgents.mockReturnValue(of({ agents: [] }));
    mockApiService.getMessages.mockReturnValue(of([]));
    mockApiService.getTodos.mockReturnValue(of([]));

    dialogOpen = jest.fn().mockReturnValue({ afterClosed: () => of(null) });
    viewState = new MockInstancesViewStateService();

    // The REAL template reads two more InstanceService members than the
    // surrogate suites use (`hasMoreInstances` / `isLoadingMore`) — the
    // shared mock is extended here rather than mutated, so the other
    // suites are untouched. The `instances` signal is the SAME object, so
    // the tests below can drive it.
    const instanceService = {
      ...mockInstanceService,
      hasMoreInstances: jest.fn(() => false),
      isLoadingMore: jest.fn(() => false),
      loading: signal(false),
      totalInstances: signal(0),
      searchQuery: signal(''),
      isSearching: signal(false),
      showKb: signal(false),
    };

    // The rendered InstanceListComponent calls one more API method than
    // the surrogate suites exercise.
    const api = {
      ...mockApiService,
      getDefaultAgentVersions: jest.fn().mockReturnValue(of({ default_versions: {} })),
      getQueues: jest.fn().mockReturnValue(of({ queues: [] })),
    };

    // The chat header reads the SSE context-usage snapshot; the chat
    // interface reads the pending-injection card.
    const sseService = {
      ...mockSseService,
      contextUsage: jest.fn(() => null),
      pendingInjection: signal(null),
      questionPack: signal(null),
    };

    await TestBed.configureTestingModule({
      imports: [ChatComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        // The rendered instance list links with routerLink — the real
        // router providers back it (the ChatComponent's own Router is
        // mocked below and wins over this one).
        provideRouter([]),
        { provide: ApiService, useValue: api },
        { provide: SseService, useValue: sseService },
        { provide: TabStateService, useValue: new MockTabStateService() },
        {
          provide: WorkspaceOverlayService,
          useValue: new MockWorkspaceOverlayService(),
        },
        { provide: InstancesViewStateService, useValue: viewState },
        { provide: InstanceService, useValue: instanceService },
        {
          provide: ProjectService,
          useValue: {
            projects: signal([]),
            listProjects: jest.fn().mockReturnValue(of({ projects: [] })),
          },
        },
        { provide: MatSnackBar, useValue: mockSnackBar },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(ChatComponent);
    component = fixture.componentInstance;
    // The component's MatDialog is provided at the COMPONENT level
    // (ChatComponent imports MatDialogModule), so an environment-level
    // mock would be shadowed. Spy on the exact instance the component
    // uses — `dialogOpen` records the call arguments.
    const componentDialog = (component as unknown as { dialog: MatDialog }).dialog;
    jest.spyOn(componentDialog, 'open').mockImplementation((...args: unknown[]) => {
      (dialogOpen as (...a: unknown[]) => unknown)(...args);
      return { afterClosed: () => of(null) } as never;
    });
    fixture.detectChanges();
  });

  function roomButton(): HTMLElement | null {
    return (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('.room-btn');
  }

  /**
   * Select an instance the way production does: the polled instance
   * list carries the row, the view-state carries the active id, and
   * the component's render hold (`renderedInstance`) resolves the
   * selection into the instance the chat subtree renders.
   *
   * The chatrooms button's VISIBILITY is gated on that same hold, so
   * every suite that needs the button present must select first.
   */
  function selectInstance(instanceId: string, instanceName?: string): void {
    mockInstanceService.instances.set([
      createMockInstance({ instance_id: instanceId, instance_name: instanceName }),
    ]);
    viewState.openDetail('all', instanceId);
    fixture.detectChanges();
    fixture.detectChanges();
    expect(component.renderedInstance()?.instance_id).toBe(instanceId);
  }

  it('renders the chatrooms button in the toggle family, next to the watchover button', () => {
    // The button is gated on a selected instance (fix G) — select one
    // so the placement / family / a11y assertions run against the
    // rendered button.
    selectInstance('chat-rooms-placement', 'Producer');
    const host = fixture.nativeElement as HTMLElement;
    const roomBtn = roomButton();
    const watchoverBtn = host.querySelector<HTMLElement>('.watchover-btn');

    expect(roomBtn).toBeTruthy();
    expect(watchoverBtn).toBeTruthy();
    // Same button family as the watchover toggle.
    expect(roomBtn!.classList.contains('toggle-btn')).toBe(true);
    // Labelled for both pointer and screen-reader users.
    expect(roomBtn!.getAttribute('aria-label')).toBe('Chatrooms');
    expect(roomBtn!.getAttribute('title')).toBe('Chatrooms');
    // Material chat/forum glyph.
    expect(roomBtn!.querySelector('mat-icon')?.textContent?.trim()).toBe('forum');
    // Sitting immediately NEXT TO the watchover button (same container).
    expect(roomBtn!.parentElement).toBe(watchoverBtn!.parentElement);
    expect(watchoverBtn!.nextElementSibling).toBe(roomBtn!);
  });

  // ───────────────────────────────────────────────────────────────────────
  // Fix G (approver gate lane 3): the chatrooms icon used to render as a
  // visible no-op when no instance was selected — the modal is scoped to
  // an instance('s tree), so the click could not do anything. The button
  // is now HIDDEN in that state.
  // ───────────────────────────────────────────────────────────────────────
  it('hides the chatrooms button when no instance is selected, and shows it once one is', () => {
    // No selection: the button must NOT be in the DOM at all (hidden,
    // not merely disabled — a disabled button still advertises a
    // feature the operator cannot use).
    expect(component.renderedInstance()).toBeNull();
    expect(roomButton()).toBeNull();
    // The watchover toggle stays visible — the gate is scoped to the
    // chatrooms icon only.
    expect(
      (fixture.nativeElement as HTMLElement).querySelector('.watchover-btn'),
    ).toBeTruthy();

    // Select an instance → the icon appears.
    selectInstance('chat-rooms-visibility', 'Producer');
    const roomBtn = roomButton();
    expect(roomBtn).toBeTruthy();
    expect(roomBtn!.classList.contains('toggle-btn')).toBe(true);
    expect(roomBtn!.classList.contains('room-btn')).toBe(true);
    expect(roomBtn!.getAttribute('aria-label')).toBe('Chatrooms');

    // Deselect → the icon disappears again (the gate is live, not a
    // one-way render). The component's own id-clear is the production
    // deselect path (terminate / new-instance reset) and is what
    // releases the render hold.
    component.currentInstanceId.set(null);
    fixture.detectChanges();
    fixture.detectChanges();
    expect(component.renderedInstance()).toBeNull();
    expect(roomButton()).toBeNull();
  });

  it('opens the BIG chatrooms modal for the rendered instance', () => {
    const instance = createMockInstance({
      instance_id: 'chat-rooms-1',
      instance_name: 'Producer',
    });
    mockInstanceService.instances.set([instance]);
    viewState.openDetail('all', 'chat-rooms-1');
    fixture.detectChanges();
    fixture.detectChanges();
    expect(component.renderedInstance()?.instance_id).toBe('chat-rooms-1');

    roomButton()!.click();
    fixture.detectChanges();

    expect(dialogOpen).toHaveBeenCalledTimes(1);
    const [opened, config] = dialogOpen.mock.calls[0];
    expect(opened).toBe(RoomDialogComponent);
    expect(config.data.instanceId).toBe('chat-rooms-1');
    expect(config.data.instanceName).toBe('Producer');
    // BIG modal — §13.1 requires a large modal, NOT a cramped tooltip.
    expect(config.width).toBe(ROOM_DIALOG_SIZE.width);
    expect(config.height).toBe(ROOM_DIALOG_SIZE.height);
    expect(config.panelClass).toBe('room-dialog-panel');
  });

  it('is a no-op when no instance is rendered — the button is absent, so no modal opens', () => {
    // No selection → the button is not rendered at all (fix G), so
    // there is nothing to click and no modal can open. The old shape
    // of this test (clicking an inert button) is gone with the button.
    expect(roomButton()).toBeNull();
    expect(dialogOpen).not.toHaveBeenCalled();
  });

  // ───────────────────────────────────────────────────────────────────────
  // gc-fe-surfaces (P2) — the NEST-WIDE room browser control.
  //
  // It sits NEXT TO the shipped per-instance chatrooms icon (which keeps
  // its instance gate) and opens the SAME dialog in 'global' mode. The
  // shipped four pins above stay unedited.
  // ───────────────────────────────────────────────────────────────────────
  describe('the all-chatrooms browser control (gc-fe-surfaces)', () => {
    function roomsBrowserButton(): HTMLElement | null {
      return (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>(
        '.rooms-browser-btn',
      );
    }

    it('renders the browser control in the toggle family, ungated (no instance needed)', () => {
      // No selection — the browser spans the WHOLE nest, so the control
      // stays actionable (the shipped per-instance icon hides instead).
      expect(component.renderedInstance()).toBeNull();
      const btn = roomsBrowserButton();
      expect(btn).toBeTruthy();
      expect(btn!.classList.contains('toggle-btn')).toBe(true);
      // A DISTINCT class — the shipped `.room-btn` helper keeps working.
      expect(btn!.classList.contains('room-btn')).toBe(false);
      // Same container as the watchover toggle and the shipped room icon.
      expect(btn!.parentElement!.classList.contains('toggle-buttons')).toBe(true);
      // Labelled for both pointer and screen-reader users.
      expect(btn!.getAttribute('aria-label')).toBe('All chatrooms');
      expect(btn!.getAttribute('title')).toBe('All chatrooms (this nest)');
      // A distinct glyph — the shipped room icon is 'forum'.
      const glyph = btn!.querySelector('mat-icon')?.textContent?.trim();
      expect(glyph).toBe('view_list');
      expect(glyph).not.toBe('forum');
    });

    it('sits between the shipped room icon and the think button', () => {
      selectInstance('chat-rooms-browser-placement', 'Producer');
      const host = fixture.nativeElement as HTMLElement;
      const watchoverBtn = host.querySelector<HTMLElement>('.watchover-btn');
      const roomBtn = roomButton();
      const browserBtn = roomsBrowserButton();
      const thinkBtn = host.querySelector<HTMLElement>('.think-btn');

      expect(watchoverBtn).toBeTruthy();
      expect(roomBtn).toBeTruthy();
      expect(browserBtn).toBeTruthy();
      expect(thinkBtn).toBeTruthy();
      // The shipped adjacency pin keeps holding: watchover → room-btn.
      expect(watchoverBtn!.nextElementSibling).toBe(roomBtn!);
      // The new control lands right AFTER the shipped room icon...
      expect(roomBtn!.nextElementSibling).toBe(browserBtn!);
      // ... and BEFORE the think button.
      expect(browserBtn!.nextElementSibling).toBe(thinkBtn!);
    });

    it('opens the room dialog in global mode with the BIG geometry', () => {
      roomsBrowserButton()!.click();
      fixture.detectChanges();

      expect(dialogOpen).toHaveBeenCalledTimes(1);
      const [opened, config] = dialogOpen.mock.calls[0];
      expect(opened).toBe(RoomDialogComponent);
      // The nest-wide browser — NOT the instance-scoped mode.
      expect(config.data.mode).toBe('global');
      expect(config.data.instanceId).toBeUndefined();
      // BIG modal — the same geometry constant as the shipped modal.
      expect(config.width).toBe(ROOM_DIALOG_SIZE.width);
      expect(config.height).toBe(ROOM_DIALOG_SIZE.height);
      expect(config.maxWidth).toBe(ROOM_DIALOG_SIZE.maxWidth);
      expect(config.maxHeight).toBe(ROOM_DIALOG_SIZE.maxHeight);
      expect(config.panelClass).toBe('room-dialog-panel');
    });
  });
});

