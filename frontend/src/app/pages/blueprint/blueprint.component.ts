import {
  Component,
  DestroyRef,
  signal,
  computed,
  inject,
  OnInit,
  OnDestroy,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { MatToolbarModule } from '@angular/material/toolbar';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatCardModule } from '@angular/material/card';
import { MatChipsModule } from '@angular/material/chips';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatDividerModule } from '@angular/material/divider';
import { MatExpansionModule } from '@angular/material/expansion';
import { MarkdownModule } from 'ngx-markdown';
import { Subscription, switchMap, take, timer } from 'rxjs';
import { BlueprintService } from '../../services/blueprint.service';
import { ProjectService } from '../../services/project.service';
import { CodemirrorDirective } from '../../components/code-viewer/codemirror.directive';
import {
  Blueprint,
  BlueprintKind,
  BlueprintStatus,
  BlueprintRevision,
  BlueprintTag,
} from '../../models/blueprint.model';
import { Project } from '../../models/project.model';

/**
 * List / CRUD page for the Project Blueprint surface (Phase 5).
 *
 * Two-column responsive layout:
 *
 * - Left column — the blueprint list (kind/status filters, refresh,
 *   create-button trigger).
 * - Right column — the selected blueprint's detail / edit / revision
 *   history panel. Shows markdown-rendered content + metadata, an
 *   inline CodeMirror editor when in edit mode (with live preview),
 *   and an expandable revision history timeline.
 *
 * The create flow is rendered as an inline mat-card panel that
 * expands over the list when `createDialogOpen()` is true — matches
 * the skill-bank inline-create pattern. The user is never navigated
 * off-page.
 *
 * Project scoping: `projectId` is read from `ActivatedRoute.snapshot`
 * and resolved against the project catalogue fetched from
 * ``ProjectService.listProjects()`` during ``ngOnInit``. If the route
 * param is ``"all"`` (or empty) and projects exist, the first project
 * is auto-selected and the URL is rewritten so deep-link / reload
 * pick up the real selection. Switching projects via the dropdown
 * (``onProjectChange``) keeps the URL in sync via ``router.navigate``.
 * The service method calls rebuild the URL per request — see
 * BlueprintService.baseUrl.
 */
@Component({
  selector: 'app-blueprint',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatToolbarModule,
    MatButtonModule,
    MatIconModule,
    MatCardModule,
    MatChipsModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
    MatSnackBarModule,
    MatDividerModule,
    MatExpansionModule,
    MarkdownModule,
    CodemirrorDirective,
  ],
  templateUrl: './blueprint.component.html',
  styleUrl: './blueprint.component.scss',
})
export class BlueprintComponent implements OnInit, OnDestroy {
  private readonly service = inject(BlueprintService);
  private readonly projectService = inject(ProjectService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly snackBar = inject(MatSnackBar);
  private readonly destroyRef = inject(DestroyRef);

  // ── Project-scoped state ─────────────────────────────────────────────
  // Project list + selected id are now driven by a dropdown above the
  // content filters (Phase 6). The list of projects is fetched once on
  // init from ProjectService (signal-cached) so switching between
  // projects doesn't refetch the catalogue. `selectedProjectId` is the
  // single source of truth — every CRUD method reads it (see the
  // service calls below) and `onProjectChange` keeps the URL in sync
  // via ``router.navigate`` so reload / deep-link still works.
  readonly projects = signal<Project[]>([]);
  readonly projectsLoading = signal(false);
  readonly selectedProjectId = signal<string | null>(null);
  /** True when a real project is selected (not the "all" / empty
   * landing state). Drives the empty-state placeholder. */
  readonly hasSelectedProject = computed(
    () => this.selectedProjectId() !== null,
  );

  // Service-provided signals (blueprints is populated by service.list).
  readonly blueprints = this.service.blueprints;
  readonly loading = this.service.loading;
  readonly error = this.service.error;

  // Detail-panel state
  readonly selectedBlueprint = signal<Blueprint | null>(null);
  readonly detailLoading = signal(false);
  readonly editing = signal(false);
  readonly showHistory = signal(false);
  readonly revisions = signal<BlueprintRevision[]>([]);
  readonly revisionsLoading = signal(false);
  readonly selectedRevision = signal<BlueprintRevision | null>(null);

  // Filter signals
  readonly kindFilter = signal<'all' | BlueprintKind>('all');
  readonly statusFilter = signal<'all' | BlueprintStatus>('all');

  // Edit form state
  readonly editName = signal('');
  readonly editSlug = signal('');
  readonly editContent = signal('');
  readonly editTags = signal<BlueprintTag[]>([]);
  readonly editFileRefs = signal<string[]>([]);
  readonly editStatus = signal<BlueprintStatus>('draft');
  readonly editSubmitting = signal(false);
  // Inline form state for the tag chip + file_ref editors.
  readonly newTagCategory = signal('');
  readonly newTagValue = signal('');
  readonly newFileRef = signal('');

  // Create form state
  readonly createDialogOpen = signal(false);
  readonly formName = signal('');
  readonly formSlug = signal('');
  readonly formKind = signal<BlueprintKind>('area');
  readonly formContent = signal('');
  readonly formSubmitting = signal(false);

  // ── Rebuild / update (Phase 6 dual-mode) ─────────────────────────────
  /**
   * True while a rebuild OR update job is running (i.e. the 202 was
   * accepted and polling is in flight). Drives the disabled state on
   * both toolbar buttons so the user can't fire a second job before
   * the first one lands.
   */
  readonly rebuilding = signal(false);
  /** Controls the "incremental vs full rebuild" modal popup. */
  readonly showUpdatePopup = signal(false);
  /** Holds the active poll subscription so we can tear it down on
   * destroy / project switch. */
  private rebuildPollingSub?: Subscription;
  // ── Per-project blueprint opt-in (Phase 7) ───────────────────────────
  /**
   * Whether the currently selected project has opted in to the
   * blueprint system. Read from the project metadata already loaded
   * by :class:`ProjectService` (no extra round-trip needed).
   * ``metadata.blueprint_active`` truthy = active; absent or falsy
   * = inactive (the default).
   */
  readonly blueprintActive = computed<boolean>(() => {
    const id = this.selectedProjectId();
    if (!id) return false;
    const proj = this.projects().find((p) => p.project_id === id);
    if (!proj) return false;
    return Boolean(proj.metadata?.['blueprint_active']);
  });
  /** Drives the disabled state on the enable / disable button. */
  readonly togglingActive = signal(false);
  /**
   * Show the "Rebuild Blueprints" button — only when the project is
   * empty (no blueprints yet) AND no rebuild is running AND a project
   * is selected.
   */
  readonly showRebuildButton = computed(
    () =>
      this.blueprints().length === 0 &&
      !this.loading() &&
      !this.rebuilding() &&
      this.hasSelectedProject(),
  );
  /**
   * Show the "Update Blueprints" button — only when the project has
   * at least one blueprint AND no rebuild/update is running. The
   * dual-mode choice (incremental vs full) is exposed via the popup.
   *
   * ``!this.loading()`` mirrors ``showRebuildButton`` so a project
   * switch doesn't briefly reveal the button for the previous
   * project's blueprints while the new list is in flight.
   */
  readonly showUpdateButton = computed(
    () =>
      this.blueprints().length > 0 &&
      !this.loading() &&
      !this.rebuilding(),
  );

  // Kind enum re-exported for template binding.
  protected readonly BlueprintKind = {
    core: 'core' as const,
    area: 'area' as const,
  };
  protected readonly BlueprintStatus = {
    published: 'published' as const,
    draft: 'draft' as const,
    review_needed: 'review_needed' as const,
  };

  /** Client-side filtered list — re-applied on filter changes so the
   * dropdown toggle doesn't flash the full list before the server
   * round-trip resolves. */
  readonly filtered = computed<Blueprint[]>(() => {
    let list = this.blueprints();
    const k = this.kindFilter();
    const s = this.statusFilter();
    if (k !== 'all') list = list.filter((b) => b.kind === k);
    if (s !== 'all') list = list.filter((b) => b.status === s);
    return list;
  });

  readonly hasItems = computed(() => this.filtered().length > 0);
  readonly isEmptyState = computed(
    () => !this.loading() && this.blueprints().length === 0 && !this.error(),
  );
  /** Toolbar-button visibility is driven by ``showRebuildButton`` /
   * ``showUpdateButton`` (defined above under the Phase 6 dual-mode
   * state block). */
  readonly isFilteredEmpty = computed(
    () =>
      !this.loading() &&
      this.filtered().length === 0 &&
      this.blueprints().length > 0,
  );
  readonly hasActiveFilters = computed(
    () => this.kindFilter() !== 'all' || this.statusFilter() !== 'all',
  );

  ngOnInit(): void {
    // Load the catalogue of projects (ProjectService populates its
    // own `projects` signal too, but we keep a local copy so the
    // dropdown doesn't re-render on unrelated service mutations).
    this.projectsLoading.set(true);
    this.projectService
      .listProjects()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (response) => {
          this.projects.set(response.projects);
          this.projectsLoading.set(false);
          // Initial resolution uses the snapshot value so the very
          // first navigation picks the right project immediately.
          this.resolveInitialProject(
            this.route.snapshot.paramMap.get('projectId'),
          );
        },
        error: () => {
          this.projectsLoading.set(false);
          // Even on failure, resolve so the empty-state placeholder
          // shows instead of a blank page.
          this.resolveInitialProject(
            this.route.snapshot.paramMap.get('projectId'),
          );
        },
      });

    // React to deep-link / external-link navigations that reuse this
    // component instance with a different ``projectId`` param.
    // ``ngOnInit`` only runs once per component lifetime, so without
    // this subscription ``selectedProjectId`` would stay stale when the
    // user pastes a different ``/projects/{id}/blueprints`` URL. The
    // equality guard prevents re-running the resolution when the param
    // is unchanged (the initial snapshot already handled that case)
    // and skips the ``null`` param emitted during teardown.
    this.route.paramMap
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((params) => {
        const id = params.get('projectId');
        if (id && id !== this.selectedProjectId()) {
          this.resolveInitialProject(id);
        }
      });
  }

  /**
   * Resolve the initial ``selectedProjectId`` after the project
   * catalogue has loaded.
   *
   * Three cases handled (in priority order):
   *
   * 1. ``projectId`` is a real project id present in the catalogue →
   *    select it as-is.
   * 2. ``projectId`` is ``'all'`` or empty AND projects exist → select
   *    the first project and rewrite the URL to
   *    ``/projects/{id}/blueprints`` so deep-link / reload pick up the
   *    real selection.
   * 3. No projects exist → leave selection null; the empty-state
   *    placeholder is rendered.
   *
   * When a project ends up selected, the blueprint list is fetched via
   * ``loadList()``. When the param id is already a real, valid project
   * (case 1) the URL is left untouched — no rewrite.
   *
   * ``projectId`` is taken as an argument (rather than read from
   * ``route.snapshot.paramMap``) so this method can also be reused by
   * the ``paramMap`` subscription for deep-link-driven re-resolution.
   */
  private resolveInitialProject(projectId: string | null): void {
    const routeProjectId = projectId ?? '';
    const all = this.projects();
    const isAllOrEmpty = routeProjectId === '' || routeProjectId === 'all';
    const knownProject =
      !isAllOrEmpty && all.some((p) => p.project_id === routeProjectId);

    if (knownProject) {
      this.selectedProjectId.set(routeProjectId);
    } else if (isAllOrEmpty && all.length > 0) {
      const first = all[0];
      this.selectedProjectId.set(first.project_id);
      // Rewrite the URL so the browser address reflects the actual
      // selection — keeps deep-link / reload behaviour intuitive. Use
      // ``replaceUrl: true`` so this auto-redirect doesn't pollute the
      // back-button history (a back press should not bounce between
      // ``/projects/all/blueprints`` and the rewritten URL).
      void this.router.navigate(
        ['/projects', first.project_id, 'blueprints'],
        { replaceUrl: true },
      );
    } else {
      // No projects available (empty list OR route id was unknown).
      // Leave selection null; the empty-state placeholder covers it.
      this.selectedProjectId.set(null);
    }

    if (this.selectedProjectId() !== null) {
      this.loadList();
    }
  }

  ngOnDestroy(): void {
    this.service.clearError();
    // Cancel any in-flight rebuild/update poll so the 10s timer
    // doesn't keep firing after the component is gone.
    this.rebuildPollingSub?.unsubscribe();
  }

  // ── List loading ─────────────────────────────────────────────────────

  private loadList(): void {
    const projectId = this.selectedProjectId();
    if (!projectId) return;
    this.service.list(projectId).subscribe({
      error: (err: Error) => {
        this.showMutationError(err, 'load');
      },
    });
  }

  protected onRefresh(): void {
    this.loadList();
  }

  /**
   * Handler for the project-selector dropdown. Resets every
   * selection-dependent piece of state (detail, edit, history), keeps
   * the URL in sync via ``router.navigate``, and triggers a list reload
   * for the new project.
   */
  protected onProjectChange(projectId: string): void {
    this.selectedProjectId.set(projectId);
    // Reset detail/edit state — the previously selected blueprint /
    // revision / edit form belong to the old project.
    this.selectedBlueprint.set(null);
    this.editing.set(false);
    this.showHistory.set(false);
    this.revisions.set([]);
    this.selectedRevision.set(null);
    // Also reset the inline create form — if the user had opened it
    // before switching projects, the form fields would otherwise stay
    // populated and a subsequent submit would POST to the wrong
    // (new) project. ``formKind`` is left at its default 'area' since
    // it's a fresh, project-agnostic choice.
    this.createDialogOpen.set(false);
    this.formName.set('');
    this.formSlug.set('');
    this.formContent.set('');
    // Cancel any in-flight rebuild/update poll for the OLD project —
    // the timer would otherwise keep hitting the (now stale) projectId
    // and dump its results into the new project's view.
    this.rebuildPollingSub?.unsubscribe();
    this.rebuildPollingSub = undefined;
    this.rebuilding.set(false);
    this.showUpdatePopup.set(false);
    // Keep the URL so reload / share-link work.
    void this.router.navigate(['/projects', projectId, 'blueprints']);
    this.loadList();
  }

  protected onKindFilterChange(value: 'all' | BlueprintKind): void {
    this.kindFilter.set(value);
  }

  protected onStatusFilterChange(value: 'all' | BlueprintStatus): void {
    this.statusFilter.set(value);
  }

  protected onClearFilters(): void {
    this.kindFilter.set('all');
    this.statusFilter.set('all');
  }

  // ── Blueprint opt-in toggle (Phase 7) ────────────────────────────────
  /**
   * Flip the per-project ``blueprint_active`` flag. Optimistic: the
   * local project row's metadata is updated immediately so the UI
   * re-renders without a server round-trip; on failure we revert and
   * surface a snackbar.
   */
  protected toggleBlueprintActive(): void {
    const projectId = this.selectedProjectId();
    if (!projectId) return;
    const newValue = !this.blueprintActive();
    this.togglingActive.set(true);
    this.service.setBlueprintActive(projectId, newValue).subscribe({
      next: () => {
        // Patch the local project row so the computed `blueprintActive`
        // signal re-evaluates without a re-fetch.
        this.projects.update((items) =>
          items.map((p) =>
            p.project_id === projectId
              ? {
                  ...p,
                  metadata: { ...(p.metadata || {}), blueprint_active: newValue },
                }
              : p,
          ),
        );
        this.togglingActive.set(false);
        this.snackBar.open(
          newValue ? 'Blueprint enabled for this project' : 'Blueprint disabled for this project',
          'Close',
          { duration: 3000 },
        );
      },
      error: (err: Error) => {
        this.togglingActive.set(false);
        this.showMutationError(err, newValue ? 'enable blueprint' : 'disable blueprint');
      },
    });
  }

  // ── Select / detail ─────────────────────────────────────────────────

  protected onSelect(bp: Blueprint): void {
    const projectId = this.selectedProjectId();
    if (!projectId) return;
    // Optimistic select from the cached list — avoid an extra GET.
    this.selectedBlueprint.set(bp);
    this.editing.set(false);
    this.showHistory.set(false);
    this.revisions.set([]);
    this.selectedRevision.set(null);
    // Then fetch the fresh full row (in case the cached copy is stale
    // — the list endpoint may project a subset in the future).
    this.detailLoading.set(true);
    this.service.get(projectId, bp.id).subscribe({
      next: (fresh) => {
        this.selectedBlueprint.set(fresh);
        this.detailLoading.set(false);
      },
      error: (err: Error) => {
        this.detailLoading.set(false);
        this.showMutationError(err, 'load');
      },
    });
  }

  // ── Create ───────────────────────────────────────────────────────────

  protected onCreate(): void {
    this.createDialogOpen.set(true);
    this.formName.set('');
    this.formSlug.set('');
    this.formKind.set('area');
    this.formContent.set('');
  }

  protected onCancelCreate(): void {
    this.createDialogOpen.set(false);
  }

  protected onCreateSubmit(): void {
    const projectId = this.selectedProjectId();
    if (!projectId) return;
    const name = this.formName().trim();
    const slug = this.formSlug().trim();
    const content = this.formContent().trim();
    if (!name || !slug || !content) {
      this.snackBar.open(
        'Name, slug, and content are required',
        'Dismiss',
        { duration: 3000, panelClass: 'error-snackbar' },
      );
      return;
    }
    this.formSubmitting.set(true);
    this.service
      .create(projectId, {
        name,
        slug,
        kind: this.formKind(),
        content,
      })
      .subscribe({
        next: (created) => {
          this.formSubmitting.set(false);
          this.createDialogOpen.set(false);
          this.snackBar.open(`Blueprint "${created.name}" created`, 'Close', {
            duration: 3000,
            panelClass: 'success-snackbar',
          });
          this.onSelect(created);
        },
        error: (err: Error) => {
          this.formSubmitting.set(false);
          this.showMutationError(err, 'create');
        },
      });
  }

  // ── Rebuild / update (Phase 6 dual-mode) ─────────────────────────────

  /**
   * Toolbar "Rebuild Blueprints" handler. Fires a full re-scan via
   * ``POST /rebuild`` and starts polling the list endpoint so the UI
   * shows new blueprints as they land.
   *
   * The backend may return 202 ``status='already_in_progress'`` when a
   * previous rebuild for this project is still queued. That's a
   * coalesce response, NOT an error — we surface it as a soft snackbar
   * and skip the polling (the existing job will land blueprints when
   * it finishes).
   */
  protected onRebuildClick(): void {
    const projectId = this.selectedProjectId();
    if (!projectId) return;
    this.rebuilding.set(true);
    this.service.rebuild(projectId).subscribe({
      next: (resp) => {
        if (resp.status === 'already_in_progress') {
          // Coalesced — a previous rebuild for this project is still
          // queued. Still start polling so the list auto-refreshes
          // when that earlier job lands; the snackbar copy above
          // tells the user nothing was lost.
          this.startRebuildPolling(projectId);
          this.snackBar.open(
            'Blueprint rebuild already in progress',
            'Close',
            { duration: 5000 },
          );
          return;
        }
        this.snackBar.open('Blueprint rebuild started…', 'Close', {
          duration: 5000,
          panelClass: 'success-snackbar',
        });
        this.startRebuildPolling(projectId);
      },
      error: (err: Error & { status?: number }) => {
        this.rebuilding.set(false);
        this.showMutationError(err, 'rebuild');
      },
    });
  }

  /**
   * Toolbar "Update Blueprints" handler. Opens the modal that asks
   * the user to choose between an incremental (diff-driven) and a
   * full rebuild. The two branches live in :meth:`onIncrementalUpdate`
   * and :meth:`onFullRebuild`.
   */
  protected onUpdateClick(): void {
    this.showUpdatePopup.set(true);
  }

  /**
   * Popup branch — incremental update. Fires ``POST /update`` and
   * starts polling. Same already-in-progress coalesce handling as
   * the rebuild path.
   */
  protected onIncrementalUpdate(): void {
    this.showUpdatePopup.set(false);
    const projectId = this.selectedProjectId();
    if (!projectId) return;
    this.rebuilding.set(true);
    this.service.updateBlueprints(projectId).subscribe({
      next: (resp) => {
        if (resp.status === 'already_in_progress') {
          // Coalesced — same logic as onRebuildClick: poll for the
          // existing job's completion so the list re-renders.
          this.startRebuildPolling(projectId);
          this.snackBar.open(
            'Blueprint update already in progress',
            'Close',
            { duration: 5000 },
          );
          return;
        }
        this.snackBar.open('Incremental update started…', 'Close', {
          duration: 5000,
          panelClass: 'success-snackbar',
        });
        this.startRebuildPolling(projectId);
      },
      error: (err: Error & { status?: number }) => {
        this.rebuilding.set(false);
        this.showMutationError(err, 'update');
      },
    });
  }

  /**
   * Popup branch — full rebuild. Same wire effect as the toolbar
   * "Rebuild Blueprints" button; the popup just confirms the user's
   * intent before throwing away existing work.
   */
  protected onFullRebuild(): void {
    this.showUpdatePopup.set(false);
    this.onRebuildClick();
  }

  /** Popup close — also called by clicking the backdrop. */
  protected onClosePopup(): void {
    this.showUpdatePopup.set(false);
  }

  /**
   * Poll ``GET /blueprints`` every 10s (max 5 minutes) while a
   * rebuild / update job is in flight so the list re-renders as new
   * blueprints land. The ``service.list`` call already pushes the
   * result into the ``blueprints`` signal; we don't need to set it
   * again here, but the explicit assignment is defensive in case the
   * service impl changes later.
   *
   * The subscription is stored on the instance so ``ngOnDestroy`` and
   * project switches can tear it down deterministically. A new poll
   * cancels the old one via ``unsubscribe`` before starting.
   */
  private startRebuildPolling(projectId: string): void {
    this.rebuildPollingSub?.unsubscribe();
    const poll$ = timer(0, 10_000).pipe(
      take(30), // 30 × 10s = 5 minutes upper bound
      // ``quiet=true`` keeps the poll off the shared loading signal so
      // the 10s tick doesn't flash the list skeleton / disable the
      // Refresh button on every cycle.
      switchMap(() => this.service.list(projectId, undefined, undefined, true)),
    );
    this.rebuildPollingSub = poll$.subscribe({
      next: (blueprints) => {
        this.blueprints.set(blueprints);
      },
      complete: () => {
        this.rebuilding.set(false);
        this.snackBar.open('Blueprint refresh complete', 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar',
        });
      },
      error: () => {
        this.rebuilding.set(false);
      },
    });
  }

  // ── Edit / save ──────────────────────────────────────────────────────

  protected onEdit(): void {
    const bp = this.selectedBlueprint();
    if (!bp) return;
    this.editName.set(bp.name);
    this.editSlug.set(bp.slug);
    this.editContent.set(bp.content);
    this.editTags.set(bp.tags.map((t) => ({ ...t })));
    this.editFileRefs.set([...bp.file_refs]);
    this.editStatus.set(bp.status);
    this.newTagCategory.set('');
    this.newTagValue.set('');
    this.newFileRef.set('');
    this.editing.set(true);
  }

  protected onCancelEdit(): void {
    this.editing.set(false);
  }

  protected onSaveEdit(): void {
    const projectId = this.selectedProjectId();
    if (!projectId) return;
    const bp = this.selectedBlueprint();
    if (!bp) return;
    const name = this.editName().trim();
    if (!name) {
      this.snackBar.open('Name is required', 'Dismiss', {
        duration: 3000,
        panelClass: 'error-snackbar',
      });
      return;
    }
    this.editSubmitting.set(true);
    this.service
      .update(projectId, bp.id, {
        name,
        content: this.editContent(),
        tags: this.editTags(),
        file_refs: this.editFileRefs(),
        status: this.editStatus(),
      })
      .subscribe({
        next: (updated) => {
          this.editSubmitting.set(false);
          this.editing.set(false);
          this.selectedBlueprint.set(updated);
          this.snackBar.open(`Blueprint "${updated.name}" updated`, 'Close', {
            duration: 3000,
            panelClass: 'success-snackbar',
          });
        },
        error: (err: Error) => {
          this.editSubmitting.set(false);
          this.showMutationError(err, 'update');
        },
      });
  }

  // ── Status quick-change (read mode) ─────────────────────────────────

  protected onStatusChange(status: BlueprintStatus): void {
    const projectId = this.selectedProjectId();
    if (!projectId) return;
    const bp = this.selectedBlueprint();
    if (!bp || bp.status === status) return;
    this.service
      .update(projectId, bp.id, { status })
      .subscribe({
        next: (updated) => {
          this.selectedBlueprint.set(updated);
          this.snackBar.open(`Status set to ${status}`, 'Close', {
            duration: 2500,
          });
        },
        error: (err: Error) => {
          this.showMutationError(err, 'update');
        },
      });
  }

  // ── Delete ───────────────────────────────────────────────────────────

  protected onDelete(): void {
    const projectId = this.selectedProjectId();
    if (!projectId) return;
    const bp = this.selectedBlueprint();
    if (!bp) return;
    const confirmed = window.confirm(
      `Delete blueprint "${bp.name}"? This cannot be undone.`,
    );
    if (!confirmed) return;
    this.service.delete(projectId, bp.id).subscribe({
      next: () => {
        this.snackBar.open(`Blueprint "${bp.name}" deleted`, 'Close', {
          duration: 3000,
        });
        this.selectedBlueprint.set(null);
        this.editing.set(false);
        this.showHistory.set(false);
        this.revisions.set([]);
        this.selectedRevision.set(null);
      },
      error: (err: Error) => {
        this.showMutationError(err, 'delete');
      },
    });
  }

  // ── Revision history ─────────────────────────────────────────────────

  protected onToggleHistory(): void {
    if (this.showHistory()) {
      this.showHistory.set(false);
      return;
    }
    const projectId = this.selectedProjectId();
    if (!projectId) return;
    const bp = this.selectedBlueprint();
    if (!bp) return;
    this.showHistory.set(true);
    this.revisionsLoading.set(true);
    this.selectedRevision.set(null);
    this.service.getRevisions(projectId, bp.id).subscribe({
      next: (revs) => {
        this.revisions.set(revs);
        this.revisionsLoading.set(false);
      },
      error: (err: Error) => {
        this.revisionsLoading.set(false);
        this.showMutationError(err, 'load');
      },
    });
  }

  protected onSelectRevision(rev: BlueprintRevision): void {
    this.selectedRevision.set(rev);
  }

  // ── Tag chip editor ──────────────────────────────────────────────────

  protected onAddTag(): void {
    const cat = this.newTagCategory().trim();
    const val = this.newTagValue().trim();
    if (!cat || !val) return;
    // Replace existing tag with the same category (singular semantic)
    // so the editor behaves like a form, not a bag.
    const filtered = this.editTags().filter((t) => t.category !== cat);
    this.editTags.set([...filtered, { category: cat, value: val }]);
    this.newTagCategory.set('');
    this.newTagValue.set('');
  }

  protected onRemoveTag(category: string): void {
    this.editTags.set(
      this.editTags().filter((t) => t.category !== category),
    );
  }

  // ── File refs editor ─────────────────────────────────────────────────

  protected onAddFileRef(): void {
    const ref = this.newFileRef().trim();
    if (!ref) return;
    if (this.editFileRefs().includes(ref)) {
      this.snackBar.open('File ref already added', 'Dismiss', {
        duration: 2500,
      });
      return;
    }
    this.editFileRefs.set([...this.editFileRefs(), ref]);
    this.newFileRef.set('');
  }

  protected onRemoveFileRef(ref: string): void {
    this.editFileRefs.set(this.editFileRefs().filter((r) => r !== ref));
  }

  // ── Helpers ──────────────────────────────────────────────────────────

  /**
   * Map an HTTP error onto a friendly snackbar message.
   * Mirrors SkillBankComponent.showMutationError.
   */
  private showMutationError(
    err: Error & { status?: number },
    action: string,
  ): void {
    const status = err?.status;
    let message = err?.message || `Failed to ${action} blueprint`;
    if (status === 422) {
      message = 'Invalid blueprint data — check required fields.';
    } else if (status === 404) {
      // Service may have supplied a more specific message (e.g. the
      // /update endpoint's 404 says "No blueprints found. Use Rebuild
      // first."). Prefer it; fall back to a generic if missing.
      message = message || 'Blueprint not found in this project.';
    } else if (status === 409) {
      // Cross-mode conflict on /rebuild vs /update, OR a coalesced
      // already-in-progress response that the service re-threw with
      // a synthetic status. The service-supplied message is already
      // friendly, so prefer it (set above) and only fall back to a
      // generic if missing.
      message = message || 'Blueprint operation already in progress.';
    } else if (status === 503) {
      message =
        'Service is temporarily paused for writes. Please try again later.';
    }
    this.snackBar.open(message, 'Dismiss', {
      duration: 5000,
      panelClass: 'error-snackbar',
    });
  }

  /**
   * Human-friendly timestamp — strip microseconds and the trailing
   * timezone offset for compact display.
   */
  protected formatDate(iso: string | null): string {
    if (!iso) return '—';
    // Trim everything after the seconds (the ISO-8601 microsecond
    // field and timezone offset both add noise to the chip).
    const tIdx = iso.indexOf('T');
    if (tIdx < 0) return iso;
    const date = iso.slice(0, tIdx);
    const time = iso.slice(tIdx + 1);
    const dot = time.indexOf('.');
    const cleanTime = dot >= 0 ? time.slice(0, dot) : time.slice(0, 5);
    return `${date} ${cleanTime}`;
  }
}
