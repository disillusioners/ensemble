import { Component, input, output, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatChipsModule } from '@angular/material/chips';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';
import {
  Skill,
  getStatusColor,
  getStatusIcon,
  getStatusLabel,
  getCategoryColor,
  getCategoryIcon,
  getSuccessRateColor,
  formatSuccessRate,
} from '../../models/skill.model';

/**
 * Card representation of a Skill record.
 *
 * Mirrors the JobCardComponent pattern: signal-based inputs/outputs,
 * computed values for derived display strings, and MatCardModule as
 * the structural shell. Consumed by SkillsComponent (list page) which
 * wires up navigation/edit/deactivate/share handlers via the outputs
 * declared below.
 *
 * Phase 6 (Skills & Knowledge) — read-only card surface. Edit
 * functionality is provided through a separate drawer; the buttons
 * on this card just emit intent upward.
 */
@Component({
  selector: 'app-skill-card',
  standalone: true,
  imports: [
    CommonModule,
    MatCardModule,
    MatChipsModule,
    MatButtonModule,
    MatIconModule,
    MatTooltipModule,
  ],
  templateUrl: './skill-card.component.html',
  styleUrl: './skill-card.component.scss',
})
export class SkillCardComponent {
  skill = input.required<Skill>();

  // Action outputs — emit the underlying skill record upward so
  // callers can mutate/route without re-deriving identity.
  view = output<Skill>();
  edit = output<Skill>();
  deactivate = output<Skill>();
  share = output<Skill>();

  // ── Computed display values ───────────────────────────────────────

  statusColor = computed(() => getStatusColor(this.skill().status));
  statusIcon = computed(() => getStatusIcon(this.skill().status));
  statusLabel = computed(() => getStatusLabel(this.skill().status));

  categoryColor = computed(() => getCategoryColor(this.skill().category));
  categoryIcon = computed(() => getCategoryIcon(this.skill().category));

  /**
   * A/B test group, truncated to 12 characters with an ellipsis when
   * longer — group labels can be freeform so we cap the visual width
   * to keep the badge row tidy.
   */
  abTestGroupDisplay = computed(() => {
    const group = this.skill().ab_test_group;
    if (!group) return '';
    return group.length > 12 ? group.substring(0, 12) + '...' : group;
  });

  /**
   * Color of the completions metric chip. Falls back to a neutral grey
   * when no selections have been made yet (rate is undefined / divide
   * by zero).
   */
  completionsColor = computed(() => {
    const total = this.skill().total_selections;
    if (total === 0) return '#9ca3af';
    return getSuccessRateColor(this.skill().total_completions / total);
  });

  /**
   * Human-readable success-rate percentage. Mirrors the
   * ``completionsColor`` zero-guard so we never show ``NaN%``.
   */
  successRateDisplay = computed(() => {
    const total = this.skill().total_selections;
    if (total === 0) return '0%';
    return formatSuccessRate(this.skill().total_completions / total);
  });

  /**
   * Relative timestamp for the header — prefers ``last_used_at`` (more
   * useful for "is this skill still relevant?") and falls back to
   * ``created_at`` for skills that have never been invoked.
   */
  relativeTime = computed(() => {
    const ts = this.skill().last_used_at || this.skill().created_at;
    return this.getRelativeTime(new Date(ts));
  });

  // ── Action handlers ───────────────────────────────────────────────

  protected onView(): void {
    this.view.emit(this.skill());
  }

  protected onEdit(): void {
    this.edit.emit(this.skill());
  }

  protected onShare(): void {
    this.share.emit(this.skill());
  }

  protected onDeactivate(): void {
    this.deactivate.emit(this.skill());
  }

  // ── Utilities (copied verbatim from JobCardComponent) ─────────────

  protected getRelativeTime(date: Date): string {
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffSecs = Math.floor(diffMs / 1000);
    const diffMins = Math.floor(diffSecs / 60);
    const diffHours = Math.floor(diffMins / 60);
    const diffDays = Math.floor(diffHours / 24);

    if (diffSecs < 60) return 'just now';
    if (diffMins < 60) return `${diffMins} min${diffMins > 1 ? 's' : ''} ago`;
    if (diffHours < 24) return `${diffHours} hour${diffHours > 1 ? 's' : ''} ago`;
    return `${diffDays} day${diffDays > 1 ? 's' : ''} ago`;
  }
}