import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  computed,
  effect,
  inject,
  signal,
  untracked,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatMenuModule } from '@angular/material/menu';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatSnackBar } from '@angular/material/snack-bar';
import { MatTooltipModule } from '@angular/material/tooltip';
import { PageEvent } from '@angular/material/paginator';
import { Clipboard } from '@angular/cdk/clipboard';

import { ProjectService } from '../../services/project.service';
import { SettingsService } from '../../services/settings.service';
import { SnapshotService } from '../../services/snapshot.service';
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

/** Status enum values for the multi-select filter. */
const STATUS_VALUES: SnapshotStatus[] = [
  'active',
  'superseded',
  'running',
  'failed',
  'interrupted',
];

/** Default filter values. */
const DEFAULT_AGE: SnapshotFilters['age'] = 'all';
const DEFAULT_SORT: SnapshotFilters['sort'] = 'created_at_desc';
const DEFAULT_TAG_MODE: SnapshotFilters['tag_mode'] = 'all';
const DEFAULT_LIMIT = 25;

/**
 * Query-param keys mirrored to the URL (AC-6.3).
 * `status` and `tags` are multi-value (repeated `?status=active&status=running`).
 */
type QueryKey = 'project_id' | 'agent_id' | 'status' | 'age' | 'tag_mode' | 'sort' | 'tags';

/** All filter values are non-default when absent from the URL (URL is the seed). */
function isAge(v: string | null): v is SnapshotFilters['age'] {
  return v === '24h' || v === '7d' || v === '30d' || v === 'all';
}
function isSort(v: string | null): v is SnapshotFilters['sort'] {
  return (
    v === 'created_at_desc' ||
    v === 'created_at_asc' ||
    v === 'title_asc' ||
    v === 'status_asc'
  );
}
function isStatus(v: string | null): v is SnapshotStatus {
  return STATUS_VALUES.includes(v as SnapshotStatus);
}
function isTagMode(v: string | null): v is 'all' | 'any' {
  return v === 'all' || v === 'any';
}

/**
 * Global `/snapshots` page (snapshots-redesign v2 — Design A).
 *
 * Hosts the page-level chrome in three compact rows (control row +
 * filter row + stats strip, total ≤ 147px above the table area),
 * the page-owned list fetch, the table (delegated to
 * `<app-snapshots-table>`), and the detail drawer in `mode="side"`.
 *
 * Page-owned rules (binding for implementation — all preserved from v1):
 *
 * * EVERY filter-signal write (incl. `onClearFilters()`) resets
 *   `pageIndex` to 0 in the SAME signal write (v1 amendment #10).
 * * `seenAgents` is session-accumulated; `populateSeenAgents(items)`
 *   augments it on every list response.
 * * The drawer (`SnapshotDetailDrawerComponent`) owns its own detail
 *   fetch + digest + 200KB guard + retry. The page only owns the
 *   id-swap signal that hands the drawer its new snapshot.
 * * R11 in-flight race: `listRequestId` increments on every fetch;
 *   stale responses are dropped before the state writes.
 * * R10 cold start: the project dropdown is fed by
 *   `projectService.projects()`; if empty on init, the page calls
 *   `listProjects()` exactly once.
 * * R4 250ms tag-input debounce: a private `debouncedTags` signal
 *   lags the user-input `filterTags` by 250ms.
 * * R15 toggle's R/W contract is preserved: `PUT /api/settings/snapshot-create`
 *   with the v1 dirty / spinner / error-toast pattern (the visual
 *   is now a 28px pill; the behaviour is identical).
 * * v2 NEW (AC-6.3): every filter-signal write mirrors to the URL
 *   via `Router.navigate(... queryParamsHandling: 'merge')`. On
 *   `ngOnInit` the page reads `ActivatedRoute.queryParams` and seeds
 *   the filter signals. NO new service is added — this rides on the
 *   page's existing filter signals directly.
 */
@Component({
  selector: 'app-snapshots',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    CommonModule,
    FormsModule,
    MatButtonModule,
    MatIconModule,
    MatMenuModule,
    MatProgressSpinnerModule,
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
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  private readonly destroyRef = inject(DestroyRef);

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

  // ── Paginator state — HOST-OWNED (v1 amendment #10) ─────────────
  readonly pageIndex = signal(0);
  readonly pageSize = signal(DEFAULT_LIMIT);

  // ── LIST-LEVEL state — HOST-OWNED (v1 amendment #8) ─────────────
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

  // ── Computed: stats-strip counts (one-line summary, AC-2.3) ──
  readonly totalSnapshotCount = computed<number>(() => this.total());

  /** Per-status counts derived from the in-memory `records` (page slice). */
  readonly statusCounts = computed<Record<SnapshotStatus, number>>(() => {
    const counts: Record<SnapshotStatus, number> = {
      active: 0,
      superseded: 0,
      running: 0,
      failed: 0,
      interrupted: 0,
    };
    for (const r of this.records()) {
      counts[r.status] = (counts[r.status] ?? 0) + 1;
    }
    return counts;
  });

  /** Total warmed spawns (sum of `spawn_counts_per_snapshot[].count`). */
  readonly totalWarmedSpawns = computed<number>(() => {
    const m = this.metrics();
    if (!m) return 0;
    return (m.spawn_counts_per_snapshot ?? []).reduce(
      (sum, e) => sum + Number(e?.count ?? 0),
      0,
    );
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

  // ── Agent filter options (v1 amendment #5 — page-owned) ─────────
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

  /**
   * Last URL queryParams we wrote via the URL-sync effect. Used as a
   * dedup key so the seed-from-URL flow does NOT immediately write
   * the URL back to itself (which would be a no-op, but unnecessary).
   * Keyed by `JSON.stringify` of the qp object.
   */
  private lastWrittenUrlKey: string | null = null;

  /** Set this in tests to observe the URL-sync side effects directly. */
  private skipUrlSync = false;

  constructor() {
    // ── Filter-effect: drive the list fetch on every change ──
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

    // ── URL-sync effect (AC-6.3) ──
    // Mirrors every non-default filter value to the route's queryParams
    // using `merge` semantics so other URL state survives. Suppressed
    // while we are seeding the signals FROM the URL on init.
    effect(() => {
      const projectId = this.filterProjectId();
      const agentId = this.filterAgentId();
      const status = this.filterStatus();
      const age = this.filterAge();
      const tagMode = this.filterTagMode();
      const sort = this.filterSort();
      const tags = this.filterTags();

      // Track the dependencies explicitly so the effect re-runs on any
      // change. The read on `tagMode` is for tracking only, so the
      // `void` operator was previously inserted to silence "unused
      // expression" lints. S1 conformance r1 cleanup: tagMode is
      // read once below in the qp assembly, so the void is no longer
      // needed and is dropped here.

      if (this.skipUrlSync) {
        return;
      }
      const qp: Record<string, string | string[] | null> = {
        project_id: projectId,
        agent_id: agentId,
        status: status.length ? status : null,
        age: age !== DEFAULT_AGE ? age : null,
        tag_mode: tagMode !== DEFAULT_TAG_MODE ? tagMode : null,
        sort: sort !== DEFAULT_SORT ? sort : null,
        tags: tags.length ? tags : null,
      };
      const key = JSON.stringify(qp);
      if (key === this.lastWrittenUrlKey) {
        return;
      }
      this.lastWrittenUrlKey = key;
      untracked(() => {
        this.router.navigate(['snapshots'], {
          queryParams: qp,
          queryParamsHandling: 'merge',
          replaceUrl: true,
        });
      });
    });
  }

  ngOnInit(): void {
    // ── Seed filter signals from URL (AC-6.3) ──
    const qp = this.route.snapshot.queryParamMap;
    const projectId = qp.get('project_id');
    const agentId = qp.get('agent_id');
    const statusRaw = qp.getAll('status');
    const ageRaw = qp.get('age');
    const tagModeRaw = qp.get('tag_mode');
    const sortRaw = qp.get('sort');
    const tagsRaw = qp.getAll('tags');

    if (projectId !== null) this.filterProjectId.set(projectId);
    if (agentId !== null) this.filterAgentId.set(agentId);
    if (statusRaw.length) {
      const valid = statusRaw.filter(isStatus);
      if (valid.length) this.filterStatus.set(valid);
    }
    if (isAge(ageRaw)) this.filterAge.set(ageRaw);
    if (isTagMode(tagModeRaw)) this.filterTagMode.set(tagModeRaw);
    if (isSort(sortRaw)) this.filterSort.set(sortRaw);
    if (tagsRaw.length) {
      // Dedup + case-insensitive
      const dedup: string[] = [];
      for (const t of tagsRaw) {
        if (!dedup.includes(t)) dedup.push(t);
      }
      if (dedup.length) {
        this.filterTags.set(dedup);
        this.debouncedTags.set([...dedup]);
      }
    }
    // Record the seed values as the last-written URL key so the
    // URL-sync effect's first run (which sees the same signals)
    // dedups and does NOT navigate. This replaces the v1
    // suppressUrlSync + queueMicrotask dance, which is brittle in
    // test environments where queueMicrotask may not be patched.
    this.lastWrittenUrlKey = JSON.stringify({
      project_id: this.filterProjectId(),
      agent_id: this.filterAgentId(),
      status: this.filterStatus(),
      age: this.filterAge(),
      tag_mode: this.filterTagMode(),
      sort: this.filterSort(),
      tags: this.filterTags(),
    });

    // Re-sync on URL changes (back/forward navigation).
    // Skip the BehaviorSubject's initial replay — we already used
    // `route.snapshot.queryParamMap` to seed the filter signals.
    let isFirstQueryParamEmit = true;
    this.route.queryParamMap
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((map) => {
        if (isFirstQueryParamEmit) {
          isFirstQueryParamEmit = false;
          return;
        }
        this.skipUrlSync = true;
        const pid = map.get('project_id');
        const aid = map.get('agent_id');
        const st = map.getAll('status').filter(isStatus);
        const ag = map.get('age');
        const tm = map.get('tag_mode');
        const so = map.get('sort');
        const tg = map.getAll('tags');
        this.filterProjectId.set(pid);
        this.filterAgentId.set(aid);
        this.filterStatus.set(st);
        if (isAge(ag)) this.filterAge.set(ag);
        if (isTagMode(tm)) this.filterTagMode.set(tm);
        if (isSort(so)) this.filterSort.set(so);
        this.filterTags.set(tg);
        this.debouncedTags.set([...tg]);
        this.pageIndex.set(0);
        // S1 (conformance r1): seed `lastWrittenUrlKey` from the
        // POST-seed signal values. The previous implementation set
        // `lastWrittenUrlKey = null` and relied on `skipUrlSync`
        // + `queueMicrotask` to suppress the URL-sync effect. That
        // left a narrow race: if the microtask landed BEFORE the
        // effect's re-run, the effect saw `skipUrlSync === false`,
        // saw the freshly-mutated signals, and re-navigated with
        // identical params — a re-navigation loop. Seeding
        // `lastWrittenUrlKey` from the post-seed values makes the
        // URL-sync effect's dedup check (`key === lastWrittenUrlKey`)
        // catch the loop on every back/forward. The `skipUrlSync`
        // + microtask dance is kept as belt-and-suspenders for the
        // signal-write-protection case but is no longer load-bearing.
        this.lastWrittenUrlKey = JSON.stringify({
          project_id: this.filterProjectId(),
          agent_id: this.filterAgentId(),
          status: this.filterStatus(),
          age: this.filterAge(),
          tag_mode: this.filterTagMode(),
          sort: this.filterSort(),
          tags: this.filterTags(),
        });
        // Allow the URL-sync effect to navigate on the next
        // USER-DRIVEN filter change. The seed above means even if
        // the microtask races ahead of the effect, the dedup check
        // still catches the loop.
        queueMicrotask(() => {
          this.skipUrlSync = false;
        });
      });

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

  /**
   * R15 pill click. The v1 radio+Apply+Unsaved-changes pattern is
   * preserved (AC-5.3): clicking the pill flips the desired value
   * and marks dirty; clicking again (when dirty) saves.
   */
  onTogglePillClick(): void {
    if (!this.snapshotCreateDirty()) {
      // First click: flip desired state, mark dirty.
      this.onSnapshotCreateSelectionChange(!this.snapshotCreateEnabled());
      return;
    }
    // Second click (while dirty): save and clear dirty state.
    this.saveSnapshotCreateEnabled();
  }

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
   * (v1 amendment #5/#8 — page-owned.)
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

  // ── Template helpers (v2 chrome — control row + stats strip) ─

  /** Verbatim page description text (v1 subtitle moved into a tooltip). */
  readonly pageDescription =
    'Browse and inspect every agent-snapshot in the system. ' +
    'Toggle the global creation switch to opt in / out.';

  /** Material icon name for each status (AC-6.1). */
  statusIcon(s: SnapshotStatus): string {
    switch (s) {
      case 'active':
        return 'check_circle';
      case 'running':
        return 'autorenew';
      case 'superseded':
        return 'history';
      case 'failed':
        return 'error';
      case 'interrupted':
        return 'warning';
    }
  }

  /** Multi-select status toggle handler. */
  onStatusToggle(s: SnapshotStatus): void {
    const current = this.filterStatus();
    if (current.includes(s)) {
      this.onFilterStatusChange(current.filter((x) => x !== s));
    } else {
      this.onFilterStatusChange([...current, s]);
    }
  }

  /** Display label for the current sort selection. */
  sortLabel(): string {
    const opt = this.sortOptions.find((o) => o.value === this.filterSort());
    return opt?.label ?? '';
  }

  /** Aria-label for the metrics pill trigger (S2 conformance r1).
   * Spec §2.2 mandates "Snapshot metrics: N captures, M warmed" — the
   * same shape as the click popover's headline, so SR users hear the
   * same totals via the trigger's accessible name. */
  metricsPillAriaLabel(): string {
    const n = this.totalSnapshotCount();
    const w = this.totalWarmedSpawns();
    return `Snapshot metrics: ${n} capture${n === 1 ? '' : 's'}, ${w} warmed spawn${w === 1 ? '' : 's'}`;
  }

  /**
   * Look up the warmed-spawn count for the currently-open snapshot,
   * if any. Used by the drawer to render the v2 "Last warmed" row.
   * Returns `null` when the snapshot is not in the metrics rollup
   * (drawer renders the `—` placeholder).
   */
  warmedSpawnCountForSelected(): number | null {
    const id = this.selectedSnapshotId();
    if (!id) return null;
    const m = this.metrics();
    if (!m) return null;
    const hit = (m.spawn_counts_per_snapshot ?? []).find(
      (e) => e.snapshot_id === id,
    );
    return hit ? hit.count : null;
  }
}