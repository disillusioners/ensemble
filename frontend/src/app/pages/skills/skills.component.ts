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
import { Router } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatChipsModule } from '@angular/material/chips';
import { MatSelectModule } from '@angular/material/select';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatInputModule } from '@angular/material/input';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatCardModule } from '@angular/material/card';
import { MatDividerModule } from '@angular/material/divider';
import { SkillService } from '../../services/skill.service';
import { ProjectService } from '../../services/project.service';
import { SkillCardComponent } from '../../components/skill-card/skill-card.component';
import {
  Skill,
  SkillFilters,
  SkillCategory,
  SKILL_CATEGORIES,
} from '../../models/skill.model';

/**
 * List page for the Skills surface (Phase 5 / Phase 6).
 *
 * Hosts the inline create form, the filter bar, and the card list.
 * Navigation to the detail view happens via the ``view`` output of
 * ``SkillCardComponent`` which routes to ``/skills/:id`` — the
 * dedicated detail page lives at ``skill-detail/``.
 *
 * Mirrors the SchedulesComponent pattern (signals, computed
 * selectors, snackbar feedback) but with inline-create instead of a
 * dialog so the create flow stays in-page and discoverable.
 */
@Component({
  selector: 'app-skills',
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
    MatCheckboxModule,
    MatInputModule,
    MatTooltipModule,
    MatSnackBarModule,
    MatCardModule,
    MatDividerModule,
    SkillCardComponent,
  ],
  templateUrl: './skills.component.html',
  styleUrl: './skills.component.scss',
})
export class SkillsComponent implements OnInit, OnDestroy {
  private readonly skillService = inject(SkillService);
  private readonly projectService = inject(ProjectService);
  private readonly router = inject(Router);
  private readonly snackBar = inject(MatSnackBar);

  private readonly ACTIVE_ONLY_KEY = 'skills-page-active-only';
  private activeOnlyRestored = false;

  // Service-provided signals (skills is populated by skillService.list).
  readonly skills = this.skillService.skills;
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);
  readonly projects = this.projectService.projects;

  // Filter signals
  readonly activeOnly = signal(true);
  readonly categoryFilter = signal<SkillCategory | 'all'>('all');
  readonly projectFilter = signal<string>('all');
  readonly searchQuery = signal<string>('');

  readonly categories = SKILL_CATEGORIES;

  /** Filter payload sent to ``SkillService.list``. */
  readonly filters = computed<SkillFilters>(() => {
    const f: SkillFilters = {};
    if (this.activeOnly()) f.is_active = true;
    const cat = this.categoryFilter();
    if (cat !== 'all') f.category = cat;
    const proj = this.projectFilter();
    if (proj !== 'all') f.project_id = proj;
    const q = this.searchQuery().trim();
    if (q) f.search = q;
    return f;
  });

  /**
   * Local view-model of the filtered list. The backend already
   * honours ``is_active`` / ``category`` / ``project_id`` /
   * ``search``, but we re-apply the active-only and search filters
   * client-side too so toggling them without a server round-trip
   * still yields the expected subset (avoids a flash of "all
   * skills" when the user un-checks Active only).
   */
  readonly filteredSkills = computed<Skill[]>(() => {
    let list = this.skills();
    if (this.activeOnly()) {
      list = list.filter((s) => s.is_active);
    }
    const q = this.searchQuery().trim().toLowerCase();
    if (q) {
      list = list.filter(
        (s) =>
          s.name.toLowerCase().includes(q) ||
          s.description.toLowerCase().includes(q),
      );
    }
    return list;
  });

  readonly hasSkills = computed(() => this.filteredSkills().length > 0);
  readonly isEmptyState = computed(
    () => !this.loading() && this.skills().length === 0 && !this.error(),
  );
  readonly isFilteredEmpty = computed(
    () =>
      !this.loading() &&
      this.filteredSkills().length === 0 &&
      this.skills().length > 0,
  );
  readonly hasInitialLoad = computed(() => this.skills().length > 0);

  // Inline create form state
  readonly formOpen = signal(false);
  readonly formName = signal('');
  readonly formDescription = signal('');
  readonly formContent = signal('');
  readonly formCategory = signal<SkillCategory>('workflow');
  readonly formSubmitting = signal(false);

  // Detail drawer state — kept for parity with SchedulesComponent
  // even though the primary detail navigation routes via the
  // detail page (``/skills/:id``).
  readonly detailDrawerOpen = signal(false);
  readonly selectedSkillId = signal<string | null>(null);

  constructor() {
    // Restore the "Active only" preference from localStorage on first
    // construction. Guarded so the effect only fires its initial
    // mutation once; subsequent reads observe the live signal value.
    effect(() => {
      if (this.activeOnlyRestored) return;
      this.activeOnlyRestored = true;
      try {
        const saved = localStorage.getItem(this.ACTIVE_ONLY_KEY);
        if (saved === 'true' || saved === 'false') {
          this.activeOnly.set(saved === 'true');
        }
      } catch {
        /* private browsing / disabled storage — fall back to default */
      }
    });

    // Refetch when filters change. Skip the very first run because
    // ngOnInit triggers the initial load — otherwise we'd double-
    // fetch on mount.
    let firstEffectRun = true;
    effect(() => {
      // Touch the computed so the effect tracks it.
      this.filters();
      if (firstEffectRun) {
        firstEffectRun = false;
        return;
      }
      this.loadSkills();
    });
  }

  ngOnInit(): void {
    this.loadSkills();
    this.projectService.listProjects().subscribe({ error: () => {} });
  }

  ngOnDestroy(): void {
    this.skillService.clearError();
  }

  private loadSkills(): void {
    this.loading.set(true);
    this.error.set(null);
    this.skillService.list(this.filters()).subscribe({
      next: () => this.loading.set(false),
      error: (err: Error) => {
        console.error('Failed to load skills:', err);
        this.error.set(err.message || 'Failed to load skills');
        this.loading.set(false);
      },
    });
  }

  protected onRefresh(): void {
    this.loadSkills();
  }

  protected onCategoryFilterChange(cat: SkillCategory | 'all'): void {
    this.categoryFilter.set(cat);
  }

  protected onProjectFilterChange(proj: string): void {
    this.projectFilter.set(proj);
  }

  protected onActiveOnlyChange(checked: boolean): void {
    this.activeOnly.set(checked);
    try {
      localStorage.setItem(this.ACTIVE_ONLY_KEY, checked ? 'true' : 'false');
    } catch {
      /* silent */
    }
  }

  protected onSearchChange(query: string): void {
    this.searchQuery.set(query);
  }

  protected onView(skill: Skill): void {
    this.router.navigate(['/skills', skill.id]);
  }

  protected onEdit(skill: Skill): void {
    this.snackBar.open(
      `Editing "${skill.name}" — coming in a future release`,
      'Dismiss',
      { duration: 3000, panelClass: 'info-snackbar' },
    );
  }

  protected onShare(skill: Skill): void {
    this.skillService.shareToGlobal(skill.id).subscribe({
      next: () => {
        this.snackBar.open(`"${skill.name}" shared to global`, 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar',
        });
        this.loadSkills();
      },
      error: (err: Error) => {
        this.snackBar.open(err.message || 'Failed to share skill', 'Dismiss', {
          duration: 5000,
          panelClass: 'error-snackbar',
        });
      },
    });
  }

  protected onDeactivate(skill: Skill): void {
    this.skillService.deactivate(skill.id).subscribe({
      next: () => {
        this.snackBar.open(`"${skill.name}" deactivated`, 'Close', {
          duration: 3000,
        });
        this.loadSkills();
      },
      error: (err: Error) => {
        this.snackBar.open(err.message || 'Failed to deactivate', 'Dismiss', {
          duration: 5000,
          panelClass: 'error-snackbar',
        });
      },
    });
  }

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
    this.skillService
      .create({
        name,
        content,
        description: this.formDescription().trim(),
        category: this.formCategory(),
      })
      .subscribe({
        next: () => {
          this.formSubmitting.set(false);
          this.snackBar.open(`Skill "${name}" created`, 'Close', {
            duration: 3000,
            panelClass: 'success-snackbar',
          });
          this.onCancelCreateForm();
          this.loadSkills();
        },
        error: (err: Error) => {
          this.formSubmitting.set(false);
          this.snackBar.open(
            err.message || 'Failed to create skill',
            'Dismiss',
            { duration: 5000, panelClass: 'error-snackbar' },
          );
        },
      });
  }

  protected hasActiveFilters(): boolean {
    return (
      this.categoryFilter() !== 'all' ||
      this.projectFilter() !== 'all' ||
      !!this.searchQuery() ||
      !this.activeOnly()
    );
  }

  protected onClearFilters(): void {
    this.categoryFilter.set('all');
    this.projectFilter.set('all');
    this.searchQuery.set('');
    this.activeOnly.set(true);
  }
}