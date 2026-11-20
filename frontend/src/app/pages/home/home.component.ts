import { Component, signal, computed, inject, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { ApiService } from '../../services/api.service';
import { SseService } from '../../services/sse.service';
import { TabStateService } from '../../services/tab-state.service';
import { AgentSelectorComponent } from '../../components/agent-selector/agent-selector.component';
import type { Agent, AgentCreate, InstanceInfo } from '../../models';

const NEXT_AGENT_STORAGE_KEY = 'ensemble-next-instance-agent';

/**
 * The refocus debounce (ms) — rapid tab switches collapse into ONE
 * immediate refresh (the jobs-page exemplar's discipline). A hidden tab
 * stops ISSUING polls; a refocus produces exactly ONE catch-up read.
 */
const REFOCUS_DEBOUNCE_MS = 2000;

@Component({
  selector: 'app-home',
  standalone: true,
  imports: [CommonModule, MatProgressSpinnerModule, AgentSelectorComponent],
  templateUrl: './home.html',
  styleUrl: './home.scss'
})
export class HomeComponent implements OnInit, OnDestroy {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly sseService = inject(SseService);
  private readonly tabStateService = inject(TabStateService);
  private pollInterval: ReturnType<typeof setInterval> | null = null;

  /**
   * The browser tab's visibility — the POLL GATE. A hidden tab stops
   * ISSUING the instances-list poll (the 120 s cadence is unchanged for
   * VISIBLE tabs).
   */
  private browserVisible = true;

  /**
   * The refocus debounce handle — rapid tab switches collapse into ONE
   * immediate refresh (the timer id is kept so a second focus cancels and
   * re-arms it instead of queueing a second refresh).
   */
  private refocusTimer: ReturnType<typeof setTimeout> | null = null;

  /**
   * The supersede token — ONE per instances read. Two overlapping reads
   * (a tick racing the refocus refresh) can land OUT OF ORDER; the older
   * answer must never overwrite the newer one.
   */
  private loadSeq = 0;

  /**
   * Get the current project context for navigation.
   *
   * Returns the project ID when on a project tab, the special tab id
   * (e.g. ``'chat'``) when on a special tab, or ``'all'`` for the All
   * tab. Mirrors the established composition in
   * ``instances.component.ts:68-72`` so sibling routes on the Chat
   * tab build the correct ``/projects/<context>/...`` URL segment
   * (without the special-tab fallback, an instance-row click on the
   * Chat tab would route to ``/projects/all/...`` and silently drop
   * the source filter via ``setActiveTab('all')`` downstream).
   */
  protected getProjectContext(): string {
    return this.tabStateService.activeProjectId()
      ?? this.tabStateService.activeSpecialTabId()
      ?? 'all';
  }

  readonly agents = signal<Agent[]>([]);
  readonly instances = signal<InstanceInfo[]>([]);
  readonly selectedAgent = signal<Agent | null>(null);
  readonly defaultAgentVersions = signal<Record<string, string | null>>({});
  /** Phase 3: the AgentSelector child owns the chosen version tag —
   *  each create event carries the tag explicitly in its payload. The
   *  parent does not need to mirror the tag as a separate signal. */
  readonly isLoading = signal(false);

  readonly hasInstances = computed(() => this.instances().length > 0);

  ngOnInit(): void {
    this.api.getDefaultAgentVersions().subscribe({
      next: (response) => this.defaultAgentVersions.set(response.default_versions ?? {}),
      error: () => {}
    });
    this.loadInitialData();
    // Seed the gate from the document state at mount — a page opened in a
    // hidden tab never issues a poll tick until it is shown.
    this.browserVisible = document.visibilityState !== 'hidden';
    document.addEventListener('visibilitychange', this.onVisibilityChange);
    this.startPolling();
  }

  ngOnDestroy(): void {
    this.stopPolling();
    // The visibility listener is detached (no leak) and a pending refocus
    // refresh is dropped (a destroyed component must not fire a request).
    document.removeEventListener('visibilitychange', this.onVisibilityChange);
    if (this.refocusTimer !== null) {
      clearTimeout(this.refocusTimer);
      this.refocusTimer = null;
    }
  }

  private loadInitialData(): void {
    this.isLoading.set(true);
    
    this.api.listAgents().subscribe({
      next: (response) => {
        this.agents.set(response.agents);
        
        // Initialize selectedAgent from localStorage
        const savedAgentId = localStorage.getItem(NEXT_AGENT_STORAGE_KEY);
        if (savedAgentId) {
          const savedAgent = response.agents.find(a => a.id === savedAgentId);
          if (savedAgent) {
            this.selectedAgent.set(savedAgent);
          }
        }
        
        this.loadInstances();
      },
      error: (err) => {
        console.error('Failed to load agents:', err);
        this.isLoading.set(false);
      }
    });
  }

  private loadInstances(): void {
    // The supersede token — a newer read wins over an older one whose
    // answer lands later (completion order can invert when a tick races
    // the refocus refresh).
    const token = ++this.loadSeq;
    this.api.listInstances().subscribe({
      next: (response) => {
        if (token !== this.loadSeq) {
          return; // superseded — a stale answer never lands
        }
        this.instances.set(response.instances);
        this.isLoading.set(false);
      },
      error: (err) => {
        if (token !== this.loadSeq) {
          return; // superseded — a stale failure never lands
        }
        console.error('Failed to load instances:', err);
        this.isLoading.set(false);
      }
    });
  }

  private startPolling(): void {
    this.pollInterval = setInterval(() => {
      // The VISIBILITY GATE — a hidden browser tab issues NO request.
      // The cadence itself is unchanged (the 120 s budget is the same for
      // a VISIBLE tab); a suppressed tick simply spends nothing.
      if (!this.browserVisible) {
        return;
      }
      this.loadInstances();
    }, 120000);
  }

  private stopPolling(): void {
    if (this.pollInterval) {
      clearInterval(this.pollInterval);
      this.pollInterval = null;
    }
  }

  /**
   * The visibility gate — a hidden browser tab stops the instances-list
   * poll, and a refocus produces EXACTLY ONE immediate refresh (debounced
   * by ``REFOCUS_DEBOUNCE_MS`` so a rapid tab-switch storm cannot issue a
   * burst of reads). No catch-up burst is ever queued: the single refocus
   * refresh IS the catch-up.
   */
  private readonly onVisibilityChange = (): void => {
    const visible = document.visibilityState !== 'hidden';
    const wasHidden = !this.browserVisible;
    this.browserVisible = visible;
    if (!visible || !wasHidden) {
      return;
    }
    if (this.refocusTimer !== null) {
      clearTimeout(this.refocusTimer);
    }
    this.refocusTimer = setTimeout(() => {
      this.refocusTimer = null;
      if (!this.browserVisible) {
        return;
      }
      this.loadInstances();
    }, REFOCUS_DEBOUNCE_MS);
  };

  protected onSelectAgent(agent: Agent): void {
    this.selectedAgent.set(agent);
    localStorage.setItem(NEXT_AGENT_STORAGE_KEY, agent.id);
  }

  protected onVersionChange(event: { agentId: string; versionTag: string | null }): void {
    // Optimistic update: capture the previous value first so we can roll
    // back if the PUT fails. Without rollback the UI would show a default
    // that was never persisted and silently revert on the next reload.
    const previous = this.defaultAgentVersions()[event.agentId];
    this.defaultAgentVersions.update(map => ({ ...map, [event.agentId]: event.versionTag }));
    this.api.setDefaultAgentVersion(event.agentId, event.versionTag).subscribe({
      error: () => {
        this.defaultAgentVersions.update(map => ({ ...map, [event.agentId]: previous }));
      },
    });
  }

  protected onCreateInstance(payload?: { versionTag?: string | null }): void {
    const agent = this.selectedAgent();
    if (!agent) return;

    this.isLoading.set(true);
    const agentPath = `./agents/${agent.id}`;
    // The version tag is sourced entirely from the createInstance payload
    // (the AgentSelector child owns the tag via its own version picker).
    const versionTag = payload?.versionTag;

    this.api.createInstance(agentPath, undefined, undefined, versionTag ?? undefined).subscribe({
      next: (instance) => {
        this.instances.update(prev => [instance, ...prev]);
        this.router.navigate(['/projects', this.getProjectContext(), 'instances', instance.instance_id]);
      },
      error: (err) => {
        console.error('Failed to create instance:', err);
        alert(`Failed to create instance: ${err}`);
        this.isLoading.set(false);
      }
    });
  }

  protected onContinueInstance(instanceId: string): void {
    const projectId = this.getProjectContext();
    if (instanceId === 'latest' && this.instances().length > 0) {
      this.router.navigate(['/projects', projectId, 'instances', this.instances()[0].instance_id]);
    } else if (instanceId !== 'latest') {
      this.router.navigate(['/projects', projectId, 'instances', instanceId]);
    }
  }

  protected onAddAgent(agentCreate: AgentCreate): void {
    this.api.createAgent(agentCreate).subscribe({
      next: (newAgent) => {
        this.agents.update(prev => [...prev, newAgent]);
      },
      error: (err) => {
        console.error('Failed to create agent:', err);
        alert(`Failed to create agent: ${err}`);
      }
    });
  }

  protected onDeleteAgent(agentId: string): void {
    this.api.deleteAgent(agentId).subscribe({
      next: () => {
        this.agents.update(prev => prev.filter(a => a.id !== agentId));
        
        // Clear selection if deleted agent was selected
        if (this.selectedAgent()?.id === agentId) {
          this.selectedAgent.set(null);
          localStorage.removeItem(NEXT_AGENT_STORAGE_KEY);
        }
      },
      error: (err) => {
        console.error('Failed to delete agent:', err);
        alert(`Failed to delete agent: ${err}`);
      }
    });
  }

  protected onStartMother(): void {
    this.isLoading.set(true);
    const agentPath = './agents/_mother';
    
    this.api.createInstance(agentPath).subscribe({
      next: (instance) => {
        this.instances.update(prev => [instance, ...prev]);
        this.router.navigate(['/projects', this.getProjectContext(), 'instances', instance.instance_id]);
      },
      error: (err) => {
        console.error('Failed to start Mother instance:', err);
        alert(`Failed to start Mother instance: ${err}`);
        this.isLoading.set(false);
      }
    });
  }

  protected onQuickCreateInstance(payload: { agent: Agent; versionTag?: string | null }): void {
    this.isLoading.set(true);
    const agentPath = `./agents/${payload.agent.id}`;
    // The AgentSelector forwards the chosen version tag explicitly so the
    // daemon picks the right agent version. No fallback to the agent
    // object's own version_tag — that field is only the tag of the
    // row that the user clicked, not the version they picked in the
    // picker, and would silently create the wrong agent version.
    const tag = payload.versionTag ?? null;

    this.api.createInstance(agentPath, undefined, undefined, tag ?? undefined).subscribe({
      next: (instance) => {
        this.instances.update(prev => [instance, ...prev]);
        this.router.navigate(['/projects', this.getProjectContext(), 'instances', instance.instance_id]);
      },
      error: (err) => {
        console.error('Failed to create instance:', err);
        alert(`Failed to create instance: ${err}`);
        this.isLoading.set(false);
      }
    });
  }

  protected onViewInstances(): void {
    if (this.instances().length > 0) {
      this.router.navigate(['/projects', this.getProjectContext(), 'instances', this.instances()[0].instance_id]);
    }
  }
}
