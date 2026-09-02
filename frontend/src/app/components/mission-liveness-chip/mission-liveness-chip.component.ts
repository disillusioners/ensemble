import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';
import {
  MissionLivenessChip,
  missionLivenessChipTooltip,
} from '../../models/job.model';

/**
 * Fix C (§8.2) — the ONE rendering surface for mission-liveness
 * chips. Used by ``app-job-card``, ``app-job-queue-panel``, and
 * ``app-job-detail-drawer`` so the chip's colour, tooltip wording,
 * icon choice, and live/settled styling cannot drift between
 * contexts.
 *
 * Pure presentational. The parent owns the chip object — typically
 * by piping ``missionLivenessChip(job)`` through a ``computed``. The
 * component renders nothing when ``chip`` is ``null`` (the model
 * helper's "render nothing extra" contract).
 *
 * Colour comes verbatim from the model helper (no hard-coded tint),
 * so the drawer's amber-on-blue ``paused`` regression cannot recur.
 * Tooltip wording lives in ``missionLivenessChipTooltip`` — never
 * re-derived per call site.
 */
@Component({
  selector: 'app-mission-liveness-chip',
  standalone: true,
  imports: [CommonModule, MatIconModule, MatTooltipModule],
  templateUrl: './mission-liveness-chip.component.html',
  styleUrl: './mission-liveness-chip.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class MissionLivenessChipComponent {
  /** Chip object from ``missionLivenessChip(job)``. Null → render nothing. */
  chip = input<MissionLivenessChip | null>(null);

  /**
   * Tooltip — derived ONCE via the model helper. Empty string when
   * there is no chip, so the tooltip binding is always safe.
   */
  readonly tooltip = computed(() => {
    const c = this.chip();
    return c ? missionLivenessChipTooltip(c) : '';
  });
}