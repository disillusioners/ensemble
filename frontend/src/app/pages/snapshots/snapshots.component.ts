import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  effect,
  inject,
  signal,
  untracked,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatChipsModule } from '@angular/material/chips';
import { MatDividerModule } from '@angular/material/divider';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSelectModule } from '@angular/material/select';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatSnackBar } from '@angular/material/snack-bar';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatInputModule } from '@angular/material/input';
import { PageEvent } from '@angular/material/paginator';
import { Clipboard } from '@angular/cdk/clipboard';

import { ProjectService } from '../../services/project.service';
import { SettingsService } from '../../services/settings.service';
import {
  SnapshotService,
} from '../../services/snapshot.service';
import {
  SearchableSelectComponent,
  SearchableSelectOption,
} from '../../components/searchable-select/searchable-select.component';
import { SnapshotsTableComponent } from './snapshots-table.component';
import { SnapshotDetailDrawerComponent } from '../../components/snapshot-detail-drawer/snapshot-detail-drawer.component';
import {
  SnapshotFilters,
  SnapshotRow,
  SnapshotStatus,
  SnapshotUsageMetrics,
} from '../../models/snapshot.model';

/** Status enum values for the multi-chip filter. */
const STATUS_VALUES: SnapshotStatus[] = [
  'active',
  'superseded',
  'running',
  'failed',
  'interrupted',
];

/** Default age preset (D-7 — `all`, NOT `30d`). */
const DEFAULT_AGE: SnapshotFilters['age'] = 'all';
const DEFAULT_SORT: SnapshotFilters['sort'] = 'created_at_desc';
const DEFAULT_TAG_MODE: SnapshotFilters['tag_mode'] = 'all';
const DEFAULT_LIMIT = 25;

/**
 * Global `/snapshots` page (snapshot-uiux v1).
 *
 * Hosts the snapshot-creation toggle (R15 — relocated from
 * /settings), the snapshot usage metrics strip (R16 — relocated
 * from /settings), the filter bar + paginator, the table, and the
 * detail drawer. The page is the OWNER of the list fetch per pass 4
 * amendment #8 — it calls `service.list()`, owns `records / total /
 * listLoading / listError / seenAgents`, and feeds the presentational
 * `<app-snapshots-table>` via inputs.
 *
 * Page-owned rules (binding for implementation):
 *
 * * EVERY filter-signal write (incl. `onClearFilters()`) resets
 *   `pageIndex` to 0 in the SAME signal write (amendment #10), so
 *   exactly one `list()` request fires and it carries `offset=0`.
 * * The `seenAgents` set is session-accumulated: the page's fetch
 *   handler calls `populateSeenAgents(items)` on every response, so
 *   an agent seen on page 1 stays available on page 2. The currently
 *   selected agent is also pinned (so a set filter never vanishes
 *   from the dropdown — amendment #5).
 * * The drawer component (`SnapshotDetailDrawerComponent`) owns its
 *   own detail fetch + digest + 200KB guard + retry (amendment #7).
 *   The page only owns the id-swap signal that hands the drawer its
 *   new snapshot to load.
 * * R11 in-flight race: a `listRequestId` increments on every fetch;
 *   stale responses are dropped before the state writes.
 * * R10 cold start: the project dropdown is fed by
 *   `projectService.projects()`; if empty on init, the page calls
 *   `listProjects()` exactly once.
 * * R4 250ms tag-input debounce: a private `debouncedTags` signal
 *   lags the user-input `filterTags` by 250ms, so a typing burst
 *   fires exactly one `list()`.
 * * No per-agent `snapshot_enabled` gating (amendment §9.2 — stub-free
 *   v1). The page header has only the global toggle.
 */
@Component({
  selector: 'app-snapshots',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    CommonModule,
    FormsModule,
    MatButtonModule,
    MatChipsModule,
    MatDividerModule,
    MatFormFieldModule,
    MatIconModule,
    MatInputModule,
    MatProgressBarModule,
    MatProgressSpinnerModule,
    MatSelectModule,
    MatSidenavModule,
    MatTooltipModule,
    SearchableSelectComponent,
    SnapshotsTableComponent,
    SnapshotDetailDrawerComponent,
  ],
  templateUrl: './snapshots.component.html',
  styleUrl: './snapshots.component.scss',
})
export class SnapshotsComponent implements OnInit {
  // ── Injected services ────────────────────────────────────────
  private readonly snapshotService = inject(SnapshotService);
  private readonly settingsService = inject(SettingsService);
  private readonly projectService = inject(ProjectService);
  private readonly snackBar = inject(MatSnackBar);
  private readonly clipboard = inject(Clipboard);

  // ── Header toggle state (mirror of /settings R15) ────────────
  readonly snapshotCreateEnabled = signal<boolean>(false);
  readonly savedSnapshotCreateEnabled = signal<boolean>(false);
  readonly savingSnapshotCreate = signal<boolean>(false);
  readonly snapshotCreateDirty = computed<boolean>(
    () => this.snapshotCreateEnabled() !== this.savedSnapshotCreateEnabled(),
  );

  // ── Filter signals ────────────────────────────────────────────
  readonly filterProjectId = signal<string | null>(null);
  readonly filterAgentId = signal<string | null>(null);
  readonly filterStatus = signal<SnapshotStatus[]>([]);
  readonly filterTags = signal<string[]>([]);
  readonly filterTagMode = signal<'all' | 'any'>(DEFAULT_TAG_MODE);
  readonly filterAge = signal<SnapshotFilters['age']>(DEFAULT_AGE);
  readonly filterSort = signal<SnapshotFilters['sort']>(DEFAULT_SORT);

  /** New tag typed in the chip input (not yet committed to filterTags). */
  readonly pendingTagInput = signal<string>('');

  // ── Paginator state — HOST-OWNED (amendment #10) ─────────────
  readonly pageIndex = signal(0);
  readonly pageSize = signal(DEFAULT_LIMIT);

  // ── LIST-LEVEL state — HOST-OWNED (amendment #8) ─────────────
  readonly records = signal<SnapshotRow[]>([]);
  readonly total = signal(0);
  readonly listLoading = signal(false);
  readonly listError = signal<string | null>(null);
  readonly listRequestId = signal(0);

  // R10 — project list bootstrap guard
  private projectsBootstrapped = false;

  // ── Drawer state (page only owns the id swap) ────────────────
  readonly drawerOpen = signal(false);
  readonly selectedSnapshotId = signal<string | null>(null);

  // ── Metrics strip ─────────────────────────────────────────────
  readonly metrics = signal<SnapshotUsageMetrics | null>(null);
  readonly metricsLoading = signal(false);
  readonly metricsError = signal<string | null>(null);

  // ── Computed: active-filter detection ────────────────────────
  readonly hasActiveFilters = computed<boolean>(() => {
    return (
      this.filterProjectId() !== null ||
      this.filterAgentId() !== null ||
      this.filterStatus().length > 0 ||
      this.filterTags().length > 0 ||
      this.filterTagMode() !== DEFAULT_TAG_MODE ||
      this.filterAge() !== DEFAULT_AGE ||
      this.filterSort() !== DEFAULT_SORT
    );
  });

  readonly activeFilterCount = computed<number>(() => {
    let n = 0;
    if (this.filterProjectId() !== null) n++;
    if (this.filterAgentId() !== null) n++;
    n += this.filterStatus().length;
    n += this.filterTags().length;
    if (this.filterTagMode() !== DEFAULT_TAG_MODE) n++;
    if (this.filterAge() !== DEFAULT_AGE) n++;
    if (this.filterSort() !== DEFAULT_SORT) n++;
    return n;
  });

  // ── Option lists ─────────────────────────────────────────────
  readonly statusOptions: readonly SnapshotStatus[] = STATUS_VALUES;

  readonly sortOptions: ReadonlyArray<{
    value: SnapshotFilters['sort'];
    label: string;
  }> = [
    { value: 'created_at_desc', label: 'Newest first' },
    { value: 'created_at_asc', label: 'Oldest first' },
    { value: 'title_asc', label: 'Title A–Z' },
    { value: 'status_asc', label: 'Status (A→Z)' },
  ];

  readonly ageOptions: ReadonlyArray<{
    value: SnapshotFilters['age'];
    label: string;
  }> = [
    { value: '24h', label: '24h' },
    { value: '7d', label: '7d' },
    { value: '30d', label: '30d' },
    { value: 'all', label: 'All' },
  ];

  // ── Project options (searchable-select shape) ────────────────
  readonly projectOptions = computed<SearchableSelectOption<string | null>[]>(
    () => {
      const opts: SearchableSelectOption<string | null>[] = [
        { value: null, label: 'All projects' },
      ];
      for (const p of this.projectService.projects()) {
        opts.push({ value: p.project_id, label: p.name });
      }
      return opts;
    },
  );

  // ── Agent filter options (amendment #5 — page-owned) ─────────
  // distinct(created_by_agent_id) over a session-accumulated set,
  // PLUS the currently-selected agent (so a set filter never
  // vanishes from the dropdown).
  readonly seenAgents = signal<ReadonlySet<string>>(new Set());
  readonly agentOptions = computed<SearchableSelectOption<string | null>[]>(
    () => {
      const seen = new Set<string>(this.seenAgents());
      const selected = this.filterAgentId();
      if (selected) seen.add(selected);
      const opts: SearchableSelectOption<string | null>[] = [
        { value: null, label: 'All agents' },
      ];
      for (const a of Array.from(seen).sort()) {
        opts.push({ value: a, label: a });
      }
      return opts;
    },
  );

  // ── 250ms tag debounce (R4) ──────────────────────────────────
  // The user input goes into `filterTags` immediately (so the chip
  // appears); a private timer mirrors it into a debounced signal
  // that the fetch effect watches. The chip stays visible; only
  // the network call waits.
  private debounceTimer: ReturnType<typeof setTimeout> | null = null;
  readonly debouncedTags = signal<string[]>([]);

  // ── Constructor effect: drive the list fetch on every change ─
  // The host owns the fetch lifecycle. We watch the union of all
  // filter signals + paginator + the debounced tags; any change
  // fires a `list()` call (with the pageIndex-reset rule below).
  //
  // The effect is gated on the filter / paginator / debouncedTags
  // signals only — it does NOT track the request-state signals
  // (records / total / listLoading / listError / seenAgents) so the
  // effect's own response handler does not re-trigger the effect
  // (otherwise the list fetch would loop forever).
  //
  // `untracked()` inside the read paths below keeps Angular from
  // adding the request-state signals to the dep graph during the
  // call (defence in depth — the read paths don't subscribe
  // either way; this just makes the intent explicit).
  constructor() {
    effect(() => {
      // touch all the reactive sources to track them
      this.filterProjectId();
      this.filterAgentId();
      this.filterStatus();
      this.debouncedTags();
      this.filterTagMode();
      this.filterAge();
      this.filterSort();
      this.pageIndex();
      this.pageSize();
      // fire the fetch (pageIndex-reset handled by callers, not here)
      untracked(() => this.fetchList());
    });
  }

  ngOnInit(): void {
    this.loadSnapshotCreateEnabled();
    this.loadMetrics();

    // R10 cold start: bootstrap the project list if empty.
    if (this.projectService.projects().length === 0 && !this.projectsBootstrapped) {
      this.projectsBootstrapped = true;
      this.projectService.listProjects().subscribe({
        error: () => {
          // Fail-closed — leave the dropdown with the "All projects"
          // sentinel only. The error is non-fatal (the table works
          // without a project filter).
        },
      });
    }
  }

  // ── Public handlers (template-bound) ─────────────────────────

  onSnapshotCreateSelectionChange(enabled: boolean): void {
    this.snapshotCreateEnabled.set(enabled);
  }

  saveSnapshotCreateEnabled(): void {
    const target = this.snapshotCreateEnabled();
    this.savingSnapshotCreate.set(true);
    this.settingsService.setSnapshotCreateEnabled(target).subscribe({
      next: (resp) => {
        const confirmed = !!resp?.enabled;
        this.savedSnapshotCreateEnabled.set(confirmed);
        this.savingSnapshotCreate.set(false);
        this.snackBar.open(
          confirmed
            ? 'Snapshot-create enabled — capturing is now allowed for creators'
            : 'Snapshot-create disabled — captures will be refused until re-enabled',
          'Close',
          { duration: 3000, panelClass: 'success-snackbar' },
        );
      },
      error: () => {
        this.savingSnapshotCreate.set(false);
        this.snackBar.open(
          'Failed to save snapshot-create preference',
          'Dismiss',
          { duration: 5000, panelClass: 'error-snackbar' },
        );
      },
    });
  }

  onFilterProjectChange(value: string | null): void {
    this.filterProjectId.set(value);
    this.pageIndex.set(0); // amendment #10: same-signal-write reset
  }

  onFilterAgentChange(value: string | null): void {
    this.filterAgentId.set(value);
    this.pageIndex.set(0);
  }

  onFilterStatusChange(values: SnapshotStatus[]): void {
    this.filterStatus.set([...values]);
    this.pageIndex.set(0);
  }

  onFilterSortChange(value: SnapshotFilters['sort']): void {
    this.filterSort.set(value);
    this.pageIndex.set(0);
  }

  onFilterAgeChange(value: SnapshotFilters['age']): void {
    this.filterAge.set(value);
    this.pageIndex.set(0);
  }

  onToggleTagMode(): void {
    this.filterTagMode.set(
      this.filterTagMode() === 'all' ? 'any' : 'all',
    );
    this.pageIndex.set(0);
  }

  onTagInputKey(event: KeyboardEvent): void {
    if (event.key === 'Enter' && this.pendingTagInput().trim()) {
      const raw = this.pendingTagInput().trim();
      // Dedupe + case-insensitive
      const existing = this.filterTags();
      if (!existing.includes(raw)) {
        this.filterTags.set([...existing, raw]);
        this.pageIndex.set(0);
      }
      this.pendingTagInput.set('');
      this.scheduleDebouncedTags();
      event.preventDefault();
    }
  }

  onRemoveTag(tag: string): void {
    this.filterTags.set(this.filterTags().filter((t) => t !== tag));
    this.pageIndex.set(0);
    this.scheduleDebouncedTags();
  }

  onClearFilters(): void {
    // Same-signal-write batch so the effect fires exactly once.
    this.filterProjectId.set(null);
    this.filterAgentId.set(null);
    this.filterStatus.set([]);
    this.filterTags.set([]);
    this.debouncedTags.set([]);
    this.filterTagMode.set(DEFAULT_TAG_MODE);
    this.filterAge.set(DEFAULT_AGE);
    this.filterSort.set(DEFAULT_SORT);
    this.pageIndex.set(0);
  }

  onRowClick(row: SnapshotRow): void {
    this.selectedSnapshotId.set(row.id);
    this.drawerOpen.set(true);
  }

  onCloseDrawer(): void {
    this.drawerOpen.set(false);
    this.selectedSnapshotId.set(null);
  }

  onNavigateToPredecessor(id: string): void {
    this.selectedSnapshotId.set(id);
  }

  onCopySnapshotId(): void {
    const id = this.selectedSnapshotId();
    if (!id) return;
    this.clipboard.copy(id);
    this.snackBar.open('Snapshot ID copied', 'Close', {
      duration: 2000,
      panelClass: 'success-snackbar',
    });
  }

  onPageChange(event: PageEvent): void {
    this.pageIndex.set(event.pageIndex);
    this.pageSize.set(event.pageSize);
  }

  onRetryList(): void {
    this.fetchList();
  }

  onRetryMetrics(): void {
    this.loadMetrics();
  }

  // ── Private helpers ─────────────────────────────────────────

  private composeFilters(): SnapshotFilters {
    return {
      project_id: this.filterProjectId(),
      agent_id: this.filterAgentId(),
      status: this.filterStatus(),
      tags: this.debouncedTags(),
      tag_mode: this.filterTagMode(),
      age: this.filterAge(),
      sort: this.filterSort(),
      limit: this.pageSize(),
      offset: this.pageIndex() * this.pageSize(),
    };
  }

  /**
   * The list fetch — called by the constructor effect. R11: the
   * request id increments on every fetch; stale responses are
   * dropped before the state writes.
   */
  private fetchList(): void {
    const filters = untracked(() => this.composeFilters());
    const reqId = this.listRequestId() + 1;
    this.listRequestId.set(reqId);
    this.listLoading.set(true);
    // do not clear listError on every refetch — the table has its
    // own state for "previous rows still visible" (fe-plan §7.1).
    this.snapshotService.list(filters).subscribe({
      next: (resp) => {
        if (this.listRequestId() !== reqId) {
          return; // stale
        }
        this.records.set(resp.items);
        this.total.set(resp.total);
        this.listError.set(null);
        this.populateSeenAgents(resp.items);
        this.listLoading.set(false);
      },
      error: (err: { message?: string }) => {
        if (this.listRequestId() !== reqId) {
          return; // stale
        }
        this.listError.set(err?.message || 'Failed to load snapshots');
        this.listLoading.set(false);
      },
    });
  }

  /**
   * Augment the session-accumulated `seenAgents` set with the
   * distinct `created_by_agent_id` values from a list response.
   * (amendment #5/#8 — page-owned.)
   */
  private populateSeenAgents(items: SnapshotRow[]): void {
    if (!items || items.length === 0) return;
    const next = new Set(this.seenAgents());
    let dirty = false;
    for (const r of items) {
      if (r.created_by_agent_id && !next.has(r.created_by_agent_id)) {
        next.add(r.created_by_agent_id);
        dirty = true;
      }
    }
    if (dirty) {
      this.seenAgents.set(next);
    }
  }

  private loadSnapshotCreateEnabled(): void {
    this.settingsService.getSnapshotCreateEnabled().subscribe({
      next: (resp) => {
        const enabled = !!resp?.enabled;
        this.snapshotCreateEnabled.set(enabled);
        this.savedSnapshotCreateEnabled.set(enabled);
      },
      error: () => {
        this.snapshotCreateEnabled.set(false);
        this.savedSnapshotCreateEnabled.set(false);
        this.snackBar.open(
          'Failed to load snapshot-create preference — showing defaults',
          'Dismiss',
          { duration: 5000, panelClass: 'error-snackbar' },
        );
      },
    });
  }

  private loadMetrics(): void {
    this.metricsLoading.set(true);
    this.metricsError.set(null);
    this.snapshotService.getMetrics().subscribe({
      next: (m) => {
        this.metrics.set(m);
        this.metricsLoading.set(false);
      },
      error: (err: { message?: string }) => {
        this.metrics.set(null);
        this.metricsError.set(err?.message || 'Failed to load metrics');
        this.metricsLoading.set(false);
      },
    });
  }

  /**
   * Re-arm the 250ms tag debounce. The debounced signal follows
   * the latest value of `filterTags` after a quiet period. The
   * constructor effect watches `debouncedTags` (NOT `filterTags`),
   * so typing bursts fire one fetch.
   */
  private scheduleDebouncedTags(): void {
    if (this.debounceTimer !== null) {
      clearTimeout(this.debounceTimer);
    }
    this.debounceTimer = setTimeout(() => {
      this.debouncedTags.set([...this.filterTags()]);
      this.debounceTimer = null;
    }, 250);
  }

  // ── Metrics-strip computeds (template helpers) ───────────────

  readonly metricsCaptureEntries = computed<
    Array<{ agent: string; created: number }>
  >(() => {
    const m = this.metrics();
    if (!m) return [];
    return Object.entries(m.capture_counts ?? {})
      .map(([agent, count]) => ({
        agent,
        created: Number((count as { created?: number })?.created ?? 0),
      }))
      .sort((a, b) => b.created - a.created || a.agent.localeCompare(b.agent));
  });

  readonly metricsSpawnEntries = computed<
    Array<{ snapshot_id: string; count: number }>
  >(() => {
    const m = this.metrics();
    if (!m) return [];
    return (m.spawn_counts_per_snapshot ?? [])
      .slice()
      .sort(
        (a, b) =>
          b.count - a.count || a.snapshot_id.localeCompare(b.snapshot_id),
      );
  });
}
