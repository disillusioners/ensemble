import {
  Component,
  signal,
  computed,
  inject,
  OnInit,
  OnDestroy,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
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
import { BlueprintService } from '../../services/blueprint.service';
import { CodemirrorDirective } from '../../components/code-viewer/codemirror.directive';
import {
  Blueprint,
  BlueprintKind,
  BlueprintStatus,
  BlueprintRevision,
  BlueprintTag,
} from '../../models/blueprint.model';

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
 * once on init. The service method calls rebuild the URL per
 * request — see BlueprintService.baseUrl.
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
  private readonly route = inject(ActivatedRoute);
  private readonly snackBar = inject(MatSnackBar);

  // Project id from the route — set once on init.
  private projectId: string = '';

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

  // Initialize state
  readonly initializing = signal(false);

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
  /** Show the Initialize button when there are no blueprints at all. */
  readonly canInitialize = computed(
    () => !this.loading() && this.blueprints().length === 0 && !this.error(),
  );
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
    this.projectId =
      this.route.snapshot.paramMap.get('projectId') ?? '';
    this.loadList();
  }

  ngOnDestroy(): void {
    this.service.clearError();
  }

  // ── List loading ─────────────────────────────────────────────────────

  private loadList(): void {
    if (!this.projectId) return;
    this.service.list(this.projectId).subscribe({
      error: (err: Error) => {
        this.showMutationError(err, 'load');
      },
    });
  }

  protected onRefresh(): void {
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

  // ── Select / detail ─────────────────────────────────────────────────

  protected onSelect(bp: Blueprint): void {
    // Optimistic select from the cached list — avoid an extra GET.
    this.selectedBlueprint.set(bp);
    this.editing.set(false);
    this.showHistory.set(false);
    this.revisions.set([]);
    this.selectedRevision.set(null);
    // Then fetch the fresh full row (in case the cached copy is stale
    // — the list endpoint may project a subset in the future).
    this.detailLoading.set(true);
    this.service.get(this.projectId, bp.id).subscribe({
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
      .create(this.projectId, {
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

  // ── Initialize ───────────────────────────────────────────────────────

  protected onInitialize(): void {
    const confirmed = window.confirm(
      'This will scan the project and create initial blueprints. ' +
        'This runs in the background and may take a few minutes. Continue?',
    );
    if (!confirmed) return;
    this.initializing.set(true);
    this.service.initialize(this.projectId).subscribe({
      next: () => {
        this.initializing.set(false);
        this.snackBar.open(
          'Blueprint initialization queued. Check back in a few minutes.',
          'Close',
          { duration: 5000, panelClass: 'success-snackbar' },
        );
      },
      error: (err: Error & { status?: number }) => {
        this.initializing.set(false);
        const status = err?.status;
        let message = err?.message || 'Failed to initialize blueprints';
        if (status === 409) {
          message = 'Blueprints already exist. Use refresh to update them.';
        }
        this.snackBar.open(message, 'Dismiss', {
          duration: 5000,
          panelClass: 'error-snackbar',
        });
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
      .update(this.projectId, bp.id, {
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
    const bp = this.selectedBlueprint();
    if (!bp || bp.status === status) return;
    this.service
      .update(this.projectId, bp.id, { status })
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
    const bp = this.selectedBlueprint();
    if (!bp) return;
    const confirmed = window.confirm(
      `Delete blueprint "${bp.name}"? This cannot be undone.`,
    );
    if (!confirmed) return;
    this.service.delete(this.projectId, bp.id).subscribe({
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
    const bp = this.selectedBlueprint();
    if (!bp) return;
    this.showHistory.set(true);
    this.revisionsLoading.set(true);
    this.selectedRevision.set(null);
    this.service.getRevisions(this.projectId, bp.id).subscribe({
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
      message = 'Blueprint not found in this project.';
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
