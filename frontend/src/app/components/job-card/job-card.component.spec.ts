import { computed, signal } from '@angular/core';
import { Job } from '../../models/job.model';
import { isReceiptRow, missionLivenessChip, MissionLivenessChip, missionLivenessChipTooltip } from '../../models/job.model';
import { createMockJob, createMockLiveMissionReceipt } from '../../testing/job-test-helpers';

/**
 * Logic-mirror of JobCardComponent's Fix C (§8.2) computeds.
 *
 * This project does NOT use Angular TestBed for component tests —
 * the convention (see job-queue-indicator.component.spec.ts) is a
 * plain TS class replicating the component's signal/computed wiring.
 * The mirror deliberately calls the SAME model helpers the real
 * component calls (isReceiptRow / missionLivenessChip /
 * missionLivenessChipTooltip) so the assertions exercise the
 * production decision logic, not a copy of it; only the thin
 * computed wrapper is mirrored.
 *
 * The existing card computeds (priority/status/kind) are covered by
 * the model specs and were untouched by Fix C; this mirror covers
 * only the receipt-chip + mission-liveness rendering decisions.
 */
class MockJobCardMissionChips {
  private readonly jobSignal = signal<Job>(createMockJob());

  job = this.jobSignal.asReadonly();

  showReceiptChip = computed(() => isReceiptRow(this.job()));

  missionChip = computed<MissionLivenessChip | null>(() =>
    missionLivenessChip(this.job())
  );

  missionChipTooltip = computed(() => {
    const chip = this.missionChip();
    return chip ? missionLivenessChipTooltip(chip) : '';
  });

  setJob(job: Job): void {
    this.jobSignal.set(job);
  }
}

describe('JobCardComponent Fix C mission/receipt chips (logic mirror)', () => {
  let component: MockJobCardMissionChips;

  beforeEach(() => {
    component = new MockJobCardMissionChips();
  });

  describe('row rendering — the four §8.2 wire cases', () => {
    it('CASE 1 — mirror + live mission: receipt chip ON, mission chip ON and live ("handled · mission still going")', () => {
      component.setJob(createMockLiveMissionReceipt());
      expect(component.showReceiptChip()).toBe(true);
      const chip = component.missionChip();
      expect(chip).not.toBeNull();
      expect(chip!.live).toBe(true);
      expect(chip!.label).toBe('mission: processing');
      expect(component.missionChipTooltip()).toContain('still working');
    });

    it('CASE 2 — mirror + terminal mission: receipt chip ON, mission chip ON but terminal', () => {
      // M3 (mission-class, 2026-09-03) — mission-side prose reworded:
      // ``settled`` is a transport-receipt word; the mission-side
      // equivalent is ``terminal`` (canonical values: ``completed`` /
      // ``failed`` / ``cancelled``). The chip's ``live`` flag still
      // distinguishes live from terminal — the prose rename does not
      // change the data shape, only the human-readable wording.
      component.setJob(
        createMockLiveMissionReceipt({ mission_liveness: 'completed' })
      );
      expect(component.showReceiptChip()).toBe(true);
      const chip = component.missionChip();
      expect(chip).not.toBeNull();
      expect(chip!.live).toBe(false);
      expect(chip!.label).toBe('mission: completed');
      expect(component.missionChipTooltip()).toContain('finished');
      // The two cases must style distinctly — live !== terminal.
      expect(chip!.live).not.toBe(
        missionLivenessChip(createMockLiveMissionReceipt())!.live
      );
    });

    it('CASE 3 — mission row: NO receipt chip, NO mission chip (its own status IS the liveness)', () => {
      component.setJob(createMockJob({ job_type: 'task', mission_liveness: null }));
      expect(component.showReceiptChip()).toBe(false);
      expect(component.missionChip()).toBeNull();
      expect(component.missionChipTooltip()).toBe('');
    });

    it('CASE 4 — degraded None: NO extra rendering, no invented state', () => {
      component.setJob(
        createMockJob({ job_type: 'message', mission_liveness: null })
      );
      // Receipt chip still shows (the row IS a receipt) but the
      // mission indicator stays silent — None is None.
      expect(component.showReceiptChip()).toBe(true);
      expect(component.missionChip()).toBeNull();
    });

    it('legacy rows (no job_type at all) render nothing extra — pre-Fix-C payloads unchanged', () => {
      component.setJob(createMockJob());
      expect(component.showReceiptChip()).toBe(false);
      expect(component.missionChip()).toBeNull();
    });
  });
});

// ── P3 vocabulary sweep (jobs-page-improvement) ─────────────────────────
//
// Plain-TS logic mirror of JobCardComponent's ``statusIcon`` and
// ``statusLabel`` computeds. Pins the plan task 5 contract: settled
// gets the receipt-style glyph (NOT help / default), completed gets
// check_circle (green-work-done), live row statuses render UPPERCASE,
// and the M3 prose rule never leaks into card copy.

class MockJobCardVocabulary {
  private readonly jobSignal = signal<Job>(createMockJob());

  job = this.jobSignal.asReadonly();

  // Logic-mirror of the real component's ``statusIcon`` computed.
  statusIcon = computed(() => {
    const status = this.job().status;
    switch (status) {
      case 'pending': return 'schedule';
      case 'processing': return 'sync';
      case 'paused': return 'pause_circle';
      case 'completed': return 'check_circle';
      case 'settled': return 'receipt_long';
      case 'failed': return 'error';
      case 'cancelled': return 'cancel';
      case 'dead_letter': return 'report_problem';
      default: return 'help';
    }
  });

  // Logic-mirror of the real component's ``statusLabel`` computed
  // (live-row UPPERCASE branch included).
  statusLabel = computed(() => {
    const status = this.job().status;
    const title = status
      .replace(/_/g, ' ')
      .split(' ')
      .map((w: string) => w.charAt(0).toUpperCase() + w.slice(1))
      .join(' ');
    if (
      status === 'pending' ||
      status === 'processing' ||
      status === 'paused'
    ) {
      return title.toUpperCase();
    }
    return title;
  });

  setJob(job: Job): void {
    this.jobSignal.set(job);
  }
}

describe('JobCardComponent P3 vocabulary sweep (logic mirror)', () => {
  let card: MockJobCardVocabulary;

  beforeEach(() => {
    card = new MockJobCardVocabulary();
  });

  describe('statusIcon — settled gets receipt_long (transport-handled)', () => {
    it('settled → receipt_long (NOT help / default; mirrors panel :611-630)', () => {
      card.setJob(createMockJob({ status: 'settled' }));
      expect(card.statusIcon()).toBe('receipt_long');
    });

    it('completed → check_circle (green-work-done stays distinct)', () => {
      card.setJob(createMockJob({ status: 'completed' }));
      expect(card.statusIcon()).toBe('check_circle');
    });

    it('settled and completed render with DIFFERENT glyphs (transport/work split)', () => {
      card.setJob(createMockJob({ status: 'settled' }));
      const settledIcon = card.statusIcon();
      card.setJob(createMockJob({ status: 'completed' }));
      const completedIcon = card.statusIcon();
      expect(settledIcon).not.toBe(completedIcon);
    });

    it('pending / processing / paused keep their existing glyphs (no drift)', () => {
      card.setJob(createMockJob({ status: 'pending' }));
      expect(card.statusIcon()).toBe('schedule');
      card.setJob(createMockJob({ status: 'processing' }));
      expect(card.statusIcon()).toBe('sync');
      card.setJob(createMockJob({ status: 'paused' }));
      expect(card.statusIcon()).toBe('pause_circle');
    });
  });

  describe('statusLabel — live rows UPPERCASE, terminals Title Case', () => {
    it('live row labels (pending/processing/paused) render UPPERCASE', () => {
      card.setJob(createMockJob({ status: 'pending' }));
      expect(card.statusLabel()).toBe('PENDING');
      card.setJob(createMockJob({ status: 'processing' }));
      expect(card.statusLabel()).toBe('PROCESSING');
      card.setJob(createMockJob({ status: 'paused' }));
      expect(card.statusLabel()).toBe('PAUSED');
    });

    it('terminal labels stay Title Case (NOT uppercase)', () => {
      card.setJob(createMockJob({ status: 'completed' }));
      expect(card.statusLabel()).toBe('Completed');
      card.setJob(createMockJob({ status: 'settled' }));
      expect(card.statusLabel()).toBe('Settled');
      card.setJob(createMockJob({ status: 'failed' }));
      expect(card.statusLabel()).toBe('Failed');
      card.setJob(createMockJob({ status: 'cancelled' }));
      expect(card.statusLabel()).toBe('Cancelled');
      card.setJob(createMockJob({ status: 'dead_letter' }));
      expect(card.statusLabel()).toBe('Dead Letter');
    });
  });
});
