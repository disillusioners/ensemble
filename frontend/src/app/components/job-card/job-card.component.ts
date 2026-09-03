import { Component, input, output, computed, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatChipsModule } from '@angular/material/chips';
import { MatIconModule } from '@angular/material/icon';
import { MatExpansionModule } from '@angular/material/expansion';
import { MatTooltipModule } from '@angular/material/tooltip';
import { Job, JobStatus, JobWorkKind, getPriorityColor, getStatusColor, isTerminalStatus, isJobDeleted, isReceiptRow, missionLivenessChip } from '../../models/job.model';
import {
  getKindColor,
  getKindIcon,
  getKindLabel,
  isTaskBackedKind,
} from '../../models/work.model';
import { MissionLivenessChipComponent } from '../mission-liveness-chip/mission-liveness-chip.component';

@Component({
  selector: 'app-job-card',
  standalone: true,
  imports: [
    CommonModule,
    MatButtonModule,
    MatCardModule,
    MatChipsModule,
    MatIconModule,
    MatExpansionModule,
    MatTooltipModule,
    MissionLivenessChipComponent,
  ],
  templateUrl: './job-card.component.html',
  styleUrl: './job-card.component.scss'
})
export class JobCardComponent {
  job = input.required<Job>();
  projectPaused = input<boolean>(false);
  queueMap = input<Map<string, string>>(new Map());

  // Action outputs
  cancel = output<void>();
  retry = output<void>();
  delete = output<void>();
  restore = output<void>();
  viewDetails = output<void>();

  // Internal state
  expanded = signal(false);

  // Computed values
  priorityColor = computed(() => getPriorityColor(this.job().priority));
  statusColor = computed(() => getStatusColor(this.job().status));

  priorityLabel = computed(() => `P${this.job().priority}`);
  priorityTextColor = computed(() => {
    const color = this.priorityColor();
    // For dark backgrounds, white text works for most colors
    // For amber/yellow, use dark text
    return color === '#F59E0B' ? '#000000' : '#FFFFFF';
  });

  /**
   * Hide the priority badge for task-backed work (turn / report) —
   * those rows do not have a meaningful priority in the backend and
   * showing ``P0`` would be misleading. Mirrors the kind-chip
   * guardrail: only real queued ``job`` rows carry a priority.
   */
  showPriorityBadge = computed(() => this.isJobKind());

  statusIcon = computed(() => {
    const status = this.job().status;
    switch (status) {
      case 'pending': return 'schedule';
      case 'processing': return 'sync';
      case 'paused': return 'pause_circle';
      case 'completed': return 'check_circle';
      case 'failed': return 'error';
      case 'cancelled': return 'cancel';
      case 'dead_letter': return 'report_problem';
      default: return 'help';
    }
  });

  statusLabel = computed(() => {
    const status = this.job().status;
    // Handle snake_case (e.g., 'dead_letter' -> 'Dead Letter')
    return status.replace(/_/g, ' ').split(' ').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
  });

  // Used to apply spinning animation to processing status icon
  isProcessing = computed(() => this.job().status === 'processing');

  messagePreview = computed(() => {
    // Prefer the original message when present (real Job surface).
    // Fall back to result_summary for task-backed work records that
    // do not carry a human-typed message — the user still needs to
    // see SOMETHING in the preview slot rather than a blank card.
    const job = this.job();
    const raw = job.message ?? job.result_summary ?? '';
    return raw.length > 100 ? raw.substring(0, 100) + '...' : raw;
  });

  relativeTime = computed(() => {
    const date = new Date(this.job().created_at);
    return this.getRelativeTime(date);
  });

  // ── Kind chip (Phase 4 — Virtual Job Management Surface) ────────────
  //
  // Effective kind defaults to ``'job'`` for the legacy Job surface
  // (where ``kind`` is unset) and surfaces ``'report'`` for
  // task-backed work records synthesised by the Jobs page.
  //
  // The chip is ALWAYS shown when the record has an explicit non-job
  // kind — that is the user-visible guardrail that tells them this
  // row is task-backed and not part of any real queue. For pure Job
  // rows the chip stays hidden so the existing UI does not change.
  //
  // Phase 4 partial collapse (2026-07-06): ``'turn'`` is no longer a
  // possible kind value — message turns are now JobItems
  // (``kind='job'``). Only ``'report'`` surfaces as a chip.

  effectiveKind = computed<JobWorkKind>(() => this.job().kind ?? 'job');

  kindColor = computed(() => getKindColor(this.effectiveKind()));
  kindLabel = computed(() => getKindLabel(this.effectiveKind()));
  kindIcon = computed(() => getKindIcon(this.effectiveKind()));

  /**
   * Show the kind chip only for task-backed work kinds (report only —
   * Phase 4 partial collapse removed ``'turn'``).
   *
   * For the default ``'job'`` kind the chip is suppressed so existing
   * cards look identical to the pre-Phase-4 surface — keeping the
   * change additive and the diff visually minimal.
   */
  showKindChip = computed(() => isTaskBackedKind(this.effectiveKind()));

  /**
   * True if the card represents a real queued job (kind === 'job' OR
   * kind unset). Queue badge is hidden when this is false.
   *
   * This is the Phase 4 guardrail: report rows must NEVER show a queue
   * badge even if a stale ``queue_id`` happens to be attached.
   */
  isJobKind = computed(() => this.effectiveKind() === 'job');

  // ── Fix C read-model split (§8.2) — receipt + mission chips ─────────

  /**
   * Show the receipt ("message") chip only on mirror rows
   * (``job_type === 'message'``). Mission rows already carry their
   * own lifecycle status chip; Task-backed records render nothing
   * extra.
   */
  showReceiptChip = computed(() => isReceiptRow(this.job()));

  /**
   * Mission-liveness chip for mirror rows, or ``null`` when the row
   * renders nothing extra (mission row, Task-backed record, degraded
   * lookup, or no linked instance — all ``null`` by design).
   *
   * Rendered through the shared ``<app-mission-liveness-chip>`` so
   * colour, tooltip wording, and live/terminal styling stay in lock-
   * step with the panel and the drawer.
   *
   * M3 (mission-class, 2026-09-03) — prose uses ``terminal``
   * (mission-side vocabulary) instead of ``settled`` (transport-
   * receipt vocabulary; belongs only to mirror rows now).
   */
  missionChip = computed(() => missionLivenessChip(this.job()));

  canCancel = computed(() => {
    const status = this.job().status;
    // A paused job is non-terminal (its instance is suspended); the
    // operator may still want to cancel it outright, so keep Cancel
    // available alongside the active processing/pending states.
    return status === 'pending' || status === 'processing' || status === 'paused';
  });

  canRetry = computed(() => this.job().status === 'failed' || this.job().status === 'dead_letter');

  // Soft delete computed values
  isDeleted = computed(() => isJobDeleted(this.job()));

  canDelete = computed(() => {
    const job = this.job();
    return isTerminalStatus(job.status) && !this.isDeleted();
  });

  canRestore = computed(() => this.isDeleted());

  showPausedBadge = computed(() => {
    return this.job().status === 'pending' && this.projectPaused();
  });

  queueBadge = computed(() => {
    const queueId = this.job().queue_id;
    if (!queueId) return null;
    // Look up queue name from map, fall back to truncated ID
    const queueName = this.queueMap().get(queueId);
    return queueName || queueId.substring(0, 8);
  });

  /**
   * Whether the queue badge should be rendered.
   *
   * Two-part gate:
   *
   * 1. ``queue_id`` must be present on the record.
   * 2. The record must be a real queued ``job`` (``isJobKind``).
   *    Task-backed work (turn / report) is NEVER shown with a queue
   *    badge — it is not part of any queue, regardless of whether a
   *    stale ``queue_id`` happened to leak through.
   *
   * Phase 4 guardrail: a user scanning the unified work list must
   * never confuse a task row for a queued job.
   */
  hasQueue = computed(() => !!this.job().queue_id && this.isJobKind());

  protected onCancel(): void {
    this.cancel.emit();
  }

  protected onRetry(): void {
    this.retry.emit();
  }

  protected onDelete(): void {
    this.delete.emit();
  }

  protected onRestore(): void {
    this.restore.emit();
  }

  protected onViewDetails(): void {
    this.viewDetails.emit();
  }

  protected toggleExpanded(): void {
    this.expanded.update(v => !v);
  }

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

  protected formatTimestamp(timestamp: string | null): string {
    if (!timestamp) return 'N/A';
    return new Date(timestamp).toLocaleString();
  }
}
