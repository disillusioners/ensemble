import {
  Component,
  signal,
  computed,
  inject,
  OnInit,
  OnDestroy,
  effect,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatChipsModule } from '@angular/material/chips';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatCardModule } from '@angular/material/card';
import { MatDividerModule } from '@angular/material/divider';
import { MatExpansionModule } from '@angular/material/expansion';
import { RouterModule } from '@angular/router';
import { PersonaService } from '../../services/persona.service';
import {
  SearchableSelectComponent,
  SearchableSelectOption,
} from '../../components/searchable-select/searchable-select.component';
import { DiffViewerComponent } from '../../components/diff-viewer/diff-viewer.component';
import {
  PromptVersion,
  PromptVersionCreate,
  PromptVersionUpdate,
  PromptVersionFilters,
  PromptApiError,
  isCoreRow,
  isEditableRow,
  isPendingApproval,
  promptErrorMessage,
} from '../../models/persona.model';

/**
 * Persona (Prompt Version) page — agent-prompt-immutability Phase C P1.
 *
 * Layout mirrors ``SkillBankComponent`` 1:1: filter bar (agent + artifact
 * search-selects), header with refresh + create-draft, cards-per-row,
 * snackbar feedback for mutations. Reads + writes the
 * ``PersonaService`` singleton so the header badge (App's anchor) stays
 * in sync with the page's mutations.
 *
 * Affordance matrix (per-version card):
 *
 *  | State / Class        | Edit | Delete | Approve | Reject | View Diff | Set Selection |
 *  |----------------------|------|--------|---------|--------|-----------|---------------|
 *  | CORE / draft         | ✓    | ✓      | —       | —      | ✓         | —            |
 *  | CORE / pending       | ✓    | ✓      | ✓       | ✓      | ✓         | —            |
 *  | CORE / approved      | —    | —      | —       | —      | ✓         | ✓ (selected) |
 *  | CORE / rejected      | —    | —      | —       | —      | ✓         | —            |
 *  | MEMORY / approved    | —    | —      | —       | —      | ✓ (read)  | —            |
 *
 * The MEMORY class is dimmed (Reformation #3 — Ud#3 PERSONA CORE-only
 * button enforcement): the MEMORY-class rows are transition-free and
 * have no approval/edit/draft path. View + diff still surface so the
 * operator can inspect what's in effect.
 *
 * Custom-agent empty-state: when a user-selected agent yields zero rows,
 * the empty card invites ``v1`` creation — supports the "custom agents
 * (file-less) appear naturally via the list; zero-version customs →
 * empty-state inviting v1 creation" requirement.
 */
@Component({
  selector: 'app-persona',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatChipsModule,
    MatFormFieldModule,
    MatInputModule,
    MatTooltipModule,
    MatSnackBarModule,
    MatCardModule,
    MatDividerModule,
    MatExpansionModule,
    RouterModule,
    SearchableSelectComponent,
    DiffViewerComponent,
  ],
  templateUrl: './persona.component.html',
  styleUrl: './persona.component.scss',
})
export class PersonaComponent implements OnInit, OnDestroy {
  private readonly service = inject(PersonaService);
  private readonly snackBar = inject(MatSnackBar);

  // Service-provided signals (mirrors SkillBankComponent).
  readonly items = this.service.items;
  readonly loading = signal(false);

  // Filter signals
  readonly agentFilter = signal<string | 'all'>('all');
  readonly artifactFilter = signal<string | 'all'>('all');
  readonly classFilter = signal<'all' | 'CORE' | 'MEMORY'>('all');
  readonly stateFilter = signal<'all' | PromptVersion['state']>('all');

  /** Filter payload sent to ``PersonaService.list``. */
  readonly filters = computed<PromptVersionFilters>(() => {
    const f: PromptVersionFilters = { limit: 200 };
    const ag = this.agentFilter();
    if (ag !== 'all') f.agent_id = ag;
    const ar = this.artifactFilter();
    if (ar !== 'all') f.artifact = ar;
    const cl = this.classFilter();
    if (cl !== 'all') f.artifact_class = cl;
    const st = this.stateFilter();
    if (st !== 'all') f.state = st;
    return f;
  });

  /** All distinct agent ids in the response — drives the agent picker. */
  readonly distinctAgents = computed<SearchableSelectOption<string | 'all'>[]>(() => {
    const set = new Set<string>();
    for (const v of this.items()) set.add(v.agent_id);
    const opts = Array.from(set).sort().map((a) => ({ value: a, label: a }));
    return [{ value: 'all', label: 'All Agents' }, ...opts];
  });

  /** All distinct artifact names — drives the artifact picker. */
  readonly distinctArtifacts = computed<SearchableSelectOption<string | 'all'>[]>(() => {
    const set = new Set<string>();
    for (const v of this.items()) set.add(v.artifact);
    const opts = Array.from(set).sort().map((a) => ({ value: a, label: a }));
    return [{ value: 'all', label: 'All Artifacts' }, ...opts];
  });

  /**
   * Group versions by (agent_id, artifact). We deliberately DO NOT
   * include version_tag in the key — the FE-built pages for
   * distinct agents+artifacts reads naturally without scrolling.
   */
  readonly grouped = computed<{ agent: string; artifact: string; rows: PromptVersion[] }[]>(() => {
    const groups = new Map<string, { agent: string; artifact: string; rows: PromptVersion[] }>();
    for (const v of this.filteredItems()) {
      const key = `${v.agent_id}::${v.artifact}`;
      let g = groups.get(key);
      if (!g) {
        g = { agent: v.agent_id, artifact: v.artifact, rows: [] };
        groups.set(key, g);
      }
      g.rows.push(v);
    }
    // Sort rows within group newest-first; sort groups by agent then artifact.
    const out = Array.from(groups.values()).map((g) => ({
      ...g,
      rows: [...g.rows].sort((a, b) => b.version - a.version),
    }));
    out.sort((a, b) => {
      if (a.agent !== b.agent) return a.agent.localeCompare(b.agent);
      return a.artifact.localeCompare(b.artifact);
    });
    return out;
  });

  /**
   * Client-side filter — applied on top of the service filter so toggling
   * pickers without a server round-trip yields the expected subset.
   * Backend already honours them, but this keeps the UI snappy.
   */
  readonly filteredItems = computed<PromptVersion[]>(() => {
    let list = this.items();
    const ag = this.agentFilter();
    if (ag !== 'all') list = list.filter((v) => v.agent_id === ag);
    const ar = this.artifactFilter();
    if (ar !== 'all') list = list.filter((v) => v.artifact === ar);
    const cl = this.classFilter();
    if (cl !== 'all') list = list.filter((v) => v.artifact_class === cl);
    const st = this.stateFilter();
    if (st !== 'all') list = list.filter((v) => v.state === st);
    return list;
  });

  readonly isEmptyState = computed(
    () => !this.loading() && this.items().length === 0,
  );

  /** True when a SPECIFIC agent is picked but it has zero rows. */
  readonly isCustomAgentEmpty = computed(() => {
    const ag = this.agentFilter();
    if (ag === 'all') return false;
    return !this.loading() && this.filteredItems().length === 0;
  });

  // ── Create-draft form state ────────────────────────────────────────
  readonly formOpen = signal(false);
  readonly formAgent = signal('');
  readonly formArtifact = signal('');
  readonly formContent = signal('');
  readonly formSubmitting = signal(false);

  // ── Edit form state (one row at a time) ───────────────────────────
  readonly editingId = signal<string | null>(null);
  readonly editContent = signal('');
  readonly editSubmitting = signal(false);

  // ── Diff view state ───────────────────────────────────────────────
  readonly diffVersionId = signal<string | null>(null);
  readonly diffEffectiveContent = signal<string>('');
  readonly diffA = signal<string>(''); // effective / "left" pane
  readonly diffB = signal<string>(''); // row content / "right" pane
  readonly diffHasChanges = signal<boolean>(true);
  readonly diffALabel = signal<string>('Effective');
  readonly diffBLabel = signal<string>('Version');
  readonly diffLoading = signal<boolean>(false);

  // ── Selection-write state (CORE approved → promote to pointer) ─────
  readonly selectingId = signal<string | null>(null);

  ngOnInit(): void {
    this.loadItems();
  }

  ngOnDestroy(): void {
    this.service.clearError();
  }

  private loadItems(): void {
    this.loading.set(true);
    this.service.list(this.filters()).subscribe({
      next: () => this.loading.set(false),
      error: (err: Error) => {
        console.error('Failed to load versions:', err);
        this.loading.set(false);
      },
    });
  }

  // ── Filters ────────────────────────────────────────────────────────
  protected onAgentFilterChange(v: string | 'all'): void {
    this.agentFilter.set(v);
  }
  protected onArtifactFilterChange(v: string | 'all'): void {
    this.artifactFilter.set(v);
  }
  protected onClassFilterChange(v: 'all' | 'CORE' | 'MEMORY'): void {
    this.classFilter.set(v);
  }
  protected onStateFilterChange(v: 'all' | PromptVersion['state']): void {
    this.stateFilter.set(v);
  }
  protected hasActiveFilters(): boolean {
    return (
      this.agentFilter() !== 'all' ||
      this.artifactFilter() !== 'all' ||
      this.classFilter() !== 'all' ||
      this.stateFilter() !== 'all'
    );
  }
  protected onClearFilters(): void {
    this.agentFilter.set('all');
    this.artifactFilter.set('all');
    this.classFilter.set('all');
    this.stateFilter.set('all');
  }
  protected onRefresh(): void {
    this.loadItems();
  }

  // ── Create-draft form ──────────────────────────────────────────────
  protected onOpenCreateForm(): void {
    // Pre-fill agent/artifact from the current filter — the common case
    // is "I selected an agent, I want to create v1 for it".
    const ag = this.agentFilter() !== 'all' ? this.agentFilter() : '';
    const ar = this.artifactFilter() !== 'all' ? this.artifactFilter() : '';
    this.formAgent.set(ag);
    this.formArtifact.set(ar);
    this.formContent.set('');
    this.formOpen.set(true);
  }

  protected onCancelCreateForm(): void {
    this.formOpen.set(false);
    this.formAgent.set('');
    this.formArtifact.set('');
    this.formContent.set('');
  }

  protected onSubmitCreate(): void {
    const agent = this.formAgent().trim();
    const artifact = this.formArtifact().trim();
    const content = this.formContent().trim();
    if (!agent || !artifact || !content) {
      this.snackBar.open('Agent, artifact, and content are required', 'Dismiss', {
        duration: 3000,
        panelClass: 'error-snackbar',
      });
      return;
    }
    this.formSubmitting.set(true);
    const payload: PromptVersionCreate = {
      agent_id: agent,
      artifact,
      content,
      version_tag: '',
    };
    this.service.createDraft(payload).subscribe({
      next: (created) => {
        this.formSubmitting.set(false);
        this.snackBar.open(
          `Draft created for ${created.agent_id}/${created.artifact} v${created.version}`,
          'Close',
          { duration: 3000, panelClass: 'success-snackbar' },
        );
        this.onCancelCreateForm();
        this.loadItems();
      },
      error: (err: Error & { error?: PromptApiError }) => {
        this.formSubmitting.set(false);
        this.showMutationError(err, 'create');
      },
    });
  }

  /** "Create v1" shortcut surfaced on the custom-agent empty-state. */
  protected onCreateV1(agent: string): void {
    this.formAgent.set(agent);
    this.formArtifact.set('');
    this.formContent.set('');
    this.formOpen.set(true);
  }

  // ── Edit form ───────────────────────────────────────────────────────
  protected onEdit(v: PromptVersion): void {
    this.editingId.set(v.version_id);
    this.editContent.set(v.content);
  }
  protected onCancelEdit(): void {
    this.editingId.set(null);
    this.editContent.set('');
  }
  protected onSaveEdit(): void {
    const id = this.editingId();
    if (!id) return;
    const content = this.editContent().trim();
    if (!content) {
      this.snackBar.open('Content is required', 'Dismiss', {
        duration: 3000,
        panelClass: 'error-snackbar',
      });
      return;
    }
    this.editSubmitting.set(true);
    const payload: PromptVersionUpdate = { content };
    this.service.updateContent(id, payload).subscribe({
      next: () => {
        this.editSubmitting.set(false);
        this.snackBar.open('Version content updated', 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar',
        });
        this.onCancelEdit();
        this.loadItems();
      },
      error: (err: Error & { error?: PromptApiError }) => {
        this.editSubmitting.set(false);
        this.showMutationError(err, 'update');
      },
    });
  }

  // ── Delete ─────────────────────────────────────────────────────────
  protected onDelete(v: PromptVersion): void {
    const confirmed = window.confirm(
      `Delete ${v.agent_id}/${v.artifact} v${v.version} (state=${v.state})? This cannot be undone.`,
    );
    if (!confirmed) return;
    this.service.deleteVersion(v.version_id).subscribe({
      next: () => {
        this.snackBar.open(
          `Deleted ${v.agent_id}/${v.artifact} v${v.version}`,
          'Close',
          { duration: 3000 },
        );
        if (this.editingId() === v.version_id) this.onCancelEdit();
        this.loadItems();
      },
      error: (err: Error & { error?: PromptApiError }) => {
        this.showMutationError(err, 'delete');
      },
    });
  }

  // ── Approve / Reject (CORE pending only) ───────────────────────────
  protected onApprove(v: PromptVersion): void {
    this.service.approve(v.version_id).subscribe({
      next: () => {
        this.snackBar.open(
          `Approved ${v.agent_id}/${v.artifact} v${v.version}`,
          'Close',
          { duration: 3000, panelClass: 'success-snackbar' },
        );
        this.loadItems();
      },
      error: (err: Error & { error?: PromptApiError }) => {
        this.showMutationError(err, 'approve');
      },
    });
  }

  protected onReject(v: PromptVersion): void {
    this.service.reject(v.version_id).subscribe({
      next: () => {
        this.snackBar.open(
          `Rejected ${v.agent_id}/${v.artifact} v${v.version}`,
          'Close',
          { duration: 3000 },
        );
        this.loadItems();
      },
      error: (err: Error & { error?: PromptApiError }) => {
        this.showMutationError(err, 'reject');
      },
    });
  }

  protected onSubmitForApproval(v: PromptVersion): void {
    this.service.submit(v.version_id).subscribe({
      next: () => {
        this.snackBar.open(
          `Submitted v${v.version} for approval`,
          'Close',
          { duration: 3000, panelClass: 'success-snackbar' },
        );
        this.loadItems();
      },
      error: (err: Error & { error?: PromptApiError }) => {
        this.showMutationError(err, 'submit');
      },
    });
  }

  // ── Set selection (CORE approved) ─────────────────────────────────
  protected onSetSelection(v: PromptVersion): void {
    this.selectingId.set(v.version_id);
    this.service
      .setSelection({
        agent_id: v.agent_id,
        version_tag: v.version_tag,
        artifact: v.artifact,
        version_id: v.version_id,
      })
      .subscribe({
        next: () => {
          this.selectingId.set(null);
          this.snackBar.open(
            `Selection pointer moved to ${v.agent_id}/${v.artifact} v${v.version}`,
            'Close',
            { duration: 3000, panelClass: 'success-snackbar' },
          );
          this.loadItems();
        },
        error: (err: Error & { error?: PromptApiError }) => {
          this.selectingId.set(null);
          this.showMutationError(err, 'set selection');
        },
      });
  }

  // ── Diff view (CORE — both panes; MEMORY — read-only view of B) ────
  protected onViewDiff(v: PromptVersion): void {
    this.diffVersionId.set(v.version_id);
    this.diffB.set(v.content);
    this.diffBLabel.set(`v${v.version} · ${v.state}`);
    this.diffLoading.set(true);
    this.diffEffectiveContent.set(''); // default to empty so MergeView still renders
    this.diffHasChanges.set(true); // default; recompute below
    // Fetch the effective baseline for the same key.
    this.service
      .getEffective(v.agent_id, v.version_tag || '', v.artifact)
      .subscribe({
        next: (eff) => {
          this.diffLoading.set(false);
          const baseline = eff.content || '';
          this.diffEffectiveContent.set(baseline);
          this.diffA.set(baseline);
          this.diffALabel.set(eff.content == null ? 'Template (baseline)' : 'Effective');
          // Manual equality: backend unified-diff is server-rendered;
          // an empty diff isn't returned via /effective. We compute here
          // so the "No Changes" badge is accurate for the
          // persona-mode viewer.
          this.diffHasChanges.set(baseline !== v.content);
        },
        error: (err: Error & { error?: PromptApiError }) => {
          this.diffLoading.set(false);
          // MEMORY rows OR auth-OFF: still render the row's content as B.
          // We surface the error via snackbar but keep the diff pane open
          // so the operator can still inspect the row's own content.
          this.showMutationError(err, 'get effective baseline');
          this.diffA.set('');
          this.diffALabel.set('Baseline unavailable');
          this.diffHasChanges.set(true);
        },
      });
  }

  protected onCloseDiff(): void {
    this.diffVersionId.set(null);
    this.diffA.set('');
    this.diffB.set('');
    this.diffEffectiveContent.set('');
    this.diffHasChanges.set(true);
  }

  // ── Capability predicates (CORE-only button enforcement evidence) ─
  protected canApprove(v: PromptVersion): boolean {
    return isCoreRow(v) && isPendingApproval(v);
  }
  protected canEdit(v: PromptVersion): boolean {
    return isCoreRow(v) && isEditableRow(v);
  }
  protected canDelete(v: PromptVersion): boolean {
    return isCoreRow(v) && isEditableRow(v);
  }
  protected canSetSelection(v: PromptVersion): boolean {
    return isCoreRow(v) && v.state === 'approved';
  }
  protected canSubmit(v: PromptVersion): boolean {
    return isCoreRow(v) && v.state === 'draft';
  }
  /** MEMORY rows: view-only (no approval / edit / delete / submit / select). */
  protected isMemoryRow(v: PromptVersion): boolean {
    return v.artifact_class === 'MEMORY';
  }

  // ── Error mapper ───────────────────────────────────────────────────
  /**
   * Maps the backend ``{error, message}`` envelope onto friendly
   * snackbar text. Falls back to the raw ``Error.message`` for unknown
   * shapes; covers the documented 409/403/422 codes plus the 503 auth
   * fence.
   */
  private showMutationError(err: Error & { error?: PromptApiError }, action: string): void {
    let message = err?.message || `Failed to ${action}`;
    if (err?.error && typeof err.error === 'object') {
      message = promptErrorMessage(err.error);
    }
    this.snackBar.open(message, 'Dismiss', {
      duration: 5000,
      panelClass: 'error-snackbar',
    });
  }

  /** Read-only helper for the template: truncates content for preview. */
  protected truncate(s: string, n: number): string {
    if (!s) return '';
    return s.length > n ? s.slice(0, n) + '…' : s;
  }

  protected stateIcon(state: string): string {
    switch (state) {
      case 'draft':
        return 'edit_note';
      case 'pending_approval':
        return 'hourglass_empty';
      case 'approved':
        return 'check_circle';
      case 'rejected':
        return 'cancel';
      case 'archived':
        return 'inventory_2';
      default:
        return 'help';
    }
  }

  protected stateColor(state: string): string {
    switch (state) {
      case 'draft':
        return '#94a3b8';
      case 'pending_approval':
        return '#f59e0b';
      case 'approved':
        return '#10b981';
      case 'rejected':
        return '#f43f5e';
      case 'archived':
        return '#64748b';
      default:
        return '#94a3b8';
    }
  }

  protected classColor(cls: string): string {
    return cls === 'MEMORY' ? '#7c3aed' : '#0ea5e9';
  }

  protected classIcon(cls: string): string {
    return cls === 'MEMORY' ? 'memory' : 'category';
  }

  protected actor(v: PromptVersion): string {
    if (v.state === 'pending_approval' || v.state === 'draft') {
      return v.submitter_instance_id
        ? `instance:${v.submitter_instance_id.slice(0, 8)}…`
        : v.submitter_agent_id || 'unknown';
    }
    if (v.approver_subject) return v.approver_subject;
    return '—';
  }

  // Suppress unused-warnings on the helper types re-exported for templates.
  protected readonly _isCoreRow = isCoreRow;
  protected readonly _isEditableRow = isEditableRow;
  protected readonly _isPendingApproval = isPendingApproval;
}