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
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatChipsModule } from '@angular/material/chips';
import { MatSelectModule } from '@angular/material/select';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatCardModule } from '@angular/material/card';
import { MatDividerModule } from '@angular/material/divider';
import { RouterModule } from '@angular/router';
import { SkillBankService } from '../../services/skill-bank.service';
import {
  SkillBankItem,
  SkillBankItemCreate,
  SkillBankItemUpdate,
  SkillBankFilters,
  SKILL_CATEGORIES,
  SkillCategory,
} from '../../models/skill-bank.model';
import {
  getCategoryIcon,
  getCategoryColor,
} from '../../models/skill.model';

/**
 * List / CRUD page for the Skill Bank surface (Phase 3).
 *
 * Hosts the inline create form, the category filter bar, and the
 * card list. There is no detail navigation — edit is inline so the
 * page stays single-screen. The Skill Bank is intentionally
 * isolated from the Skills evolution surface: no metrics, no A/B
 * testing, no lineage, no project filter (kept simple per spec).
 *
 * Mirrors the SkillsComponent pattern (signals, computed selectors,
 * inline-create form, snackbar feedback) so the two list pages
 * look and feel consistent side-by-side.
 */
@Component({
  selector: 'app-skill-bank',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatChipsModule,
    MatSelectModule,
    MatFormFieldModule,
    MatInputModule,
    MatTooltipModule,
    MatSnackBarModule,
    MatCardModule,
    MatDividerModule,
    RouterModule,
  ],
  templateUrl: './skill-bank.component.html',
  styleUrl: './skill-bank.component.scss',
})
export class SkillBankComponent implements OnInit, OnDestroy {
  private readonly service = inject(SkillBankService);
  private readonly snackBar = inject(MatSnackBar);

  // Service-provided signals (items is populated by service.list).
  readonly items = this.service.items;
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  // Filter signals
  readonly categoryFilter = signal<SkillCategory | 'all'>('all');

  // Categories + helper functions exposed for template binding.
  readonly categories = SKILL_CATEGORIES;
  protected getCategoryIcon = getCategoryIcon;
  protected getCategoryColor = getCategoryColor;

  /** Filter payload sent to ``SkillBankService.list``. */
  readonly filters = computed<SkillBankFilters>(() => {
    const f: SkillBankFilters = {};
    const cat = this.categoryFilter();
    if (cat !== 'all') f.category = cat;
    return f;
  });

  /**
   * Client-side view-model of the filtered list. The backend already
   * honours the category filter, but we re-apply it here so toggling
   * the dropdown without a server round-trip still yields the
   * expected subset (avoids a flash of "all items" when the user
   * changes the filter).
   */
  readonly filteredItems = computed<SkillBankItem[]>(() => {
    let list = this.items();
    const cat = this.categoryFilter();
    if (cat !== 'all') {
      list = list.filter((i) => i.category === cat);
    }
    return list;
  });

  readonly hasItems = computed(() => this.filteredItems().length > 0);
  readonly isEmptyState = computed(
    () => !this.loading() && this.items().length === 0 && !this.error(),
  );
  readonly isFilteredEmpty = computed(
    () =>
      !this.loading() &&
      this.filteredItems().length === 0 &&
      this.items().length > 0,
  );

  // Inline create form state
  readonly formOpen = signal(false);
  readonly formName = signal('');
  readonly formDescription = signal('');
  readonly formContent = signal('');
  readonly formCategory = signal<SkillCategory>('workflow');
  readonly formSubmitting = signal(false);

  // Inline edit state (only one item edited at a time)
  readonly editingId = signal<string | null>(null);
  readonly editName = signal('');
  readonly editDescription = signal('');
  readonly editContent = signal('');
  readonly editCategory = signal<SkillCategory>('workflow');
  readonly editSubmitting = signal(false);

  ngOnInit(): void {
    this.loadItems();
  }

  ngOnDestroy(): void {
    this.service.clearError();
  }

  private loadItems(): void {
    this.loading.set(true);
    this.error.set(null);
    this.service.list(this.filters()).subscribe({
      next: () => this.loading.set(false),
      error: (err: Error) => {
        console.error('Failed to load skill bank items:', err);
        this.error.set(err.message || 'Failed to load skill bank items');
        this.service.error.set(err.message || 'Failed to load skill bank items');
        this.loading.set(false);
      },
    });
  }

  protected onRefresh(): void {
    this.loadItems();
  }

  protected onCategoryFilterChange(cat: SkillCategory | 'all'): void {
    this.categoryFilter.set(cat);
  }

  protected hasActiveFilters(): boolean {
    return this.categoryFilter() !== 'all';
  }

  protected onClearFilters(): void {
    this.categoryFilter.set('all');
  }

  // ── Create form ─────────────────────────────────────────────────────

  protected onOpenCreateForm(): void {
    this.formOpen.set(true);
  }

  protected onCancelCreateForm(): void {
    this.formOpen.set(false);
    this.formName.set('');
    this.formDescription.set('');
    this.formContent.set('');
    this.formCategory.set('workflow');
  }

  protected onSubmitCreate(): void {
    const name = this.formName().trim();
    const content = this.formContent().trim();
    if (!name || !content) {
      this.snackBar.open('Name and content are required', 'Dismiss', {
        duration: 3000,
        panelClass: 'error-snackbar',
      });
      return;
    }
    this.formSubmitting.set(true);
    const payload: SkillBankItemCreate = {
      name,
      content,
      description: this.formDescription().trim(),
      category: this.formCategory(),
    };
    this.service.create(payload).subscribe({
      next: () => {
        this.formSubmitting.set(false);
        this.snackBar.open(`Bank skill "${name}" created`, 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar',
        });
        this.onCancelCreateForm();
        this.loadItems();
      },
      error: (err: Error) => {
        this.formSubmitting.set(false);
        this.showMutationError(err, 'create');
      },
    });
  }

  // ── Edit form ───────────────────────────────────────────────────────

  protected onEdit(item: SkillBankItem): void {
    this.editingId.set(item.id);
    this.editName.set(item.name);
    this.editDescription.set(item.description);
    this.editContent.set(item.content);
    const cat = item.category as SkillCategory;
    this.editCategory.set(this.categories.includes(cat) ? cat : 'other');
  }

  protected onCancelEdit(): void {
    this.editingId.set(null);
    this.editName.set('');
    this.editDescription.set('');
    this.editContent.set('');
    this.editCategory.set('workflow');
  }

  protected onSaveEdit(): void {
    const id = this.editingId();
    if (!id) return;
    const name = this.editName().trim();
    const content = this.editContent().trim();
    if (!name || !content) {
      this.snackBar.open('Name and content are required', 'Dismiss', {
        duration: 3000,
        panelClass: 'error-snackbar',
      });
      return;
    }
    this.editSubmitting.set(true);
    const payload: SkillBankItemUpdate = {
      name,
      content,
      description: this.editDescription().trim(),
      category: this.editCategory(),
    };
    this.service.update(id, payload).subscribe({
      next: (updated) => {
        this.editSubmitting.set(false);
        this.snackBar.open(`Bank skill "${updated.name}" updated`, 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar',
        });
        this.onCancelEdit();
        this.loadItems();
      },
      error: (err: Error) => {
        this.editSubmitting.set(false);
        this.showMutationError(err, 'update');
      },
    });
  }

  // ── Delete ──────────────────────────────────────────────────────────

  protected onDelete(item: SkillBankItem): void {
    const confirmed = window.confirm(
      `Delete bank skill "${item.name}"? This cannot be undone.`,
    );
    if (!confirmed) return;
    this.service.delete(item.id).subscribe({
      next: () => {
        this.snackBar.open(`Bank skill "${item.name}" deleted`, 'Close', {
          duration: 3000,
        });
        if (this.editingId() === item.id) {
          this.onCancelEdit();
        }
        this.loadItems();
      },
      error: (err: Error) => {
        this.showMutationError(err, 'delete');
      },
    });
  }

  // ── Helpers ─────────────────────────────────────────────────────────

  /**
   * Map an HTTP error onto a friendly snackbar message.
   *
   * 422 (validation) — name/content required message.
   * 503 (write-paused) — service-paused message.
   * Anything else — fall back to the raw error message.
   */
  private showMutationError(err: Error & { status?: number }, action: string): void {
    const status = err?.status;
    let message = err?.message || `Failed to ${action} bank skill`;
    if (status === 422) {
      message = 'Name and content are required.';
    } else if (status === 503) {
      message = 'Service is temporarily paused for writes. Please try again later.';
    }
    this.snackBar.open(message, 'Dismiss', {
      duration: 5000,
      panelClass: 'error-snackbar',
    });
  }
}