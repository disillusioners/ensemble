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

    it('CASE 2 — mirror + settled mission: receipt chip ON, mission chip ON but settled', () => {
      component.setJob(
        createMockLiveMissionReceipt({ mission_liveness: 'completed' })
      );
      expect(component.showReceiptChip()).toBe(true);
      const chip = component.missionChip();
      expect(chip).not.toBeNull();
      expect(chip!.live).toBe(false);
      expect(chip!.label).toBe('mission: completed');
      expect(component.missionChipTooltip()).toContain('settled');
      // The two cases must style distinctly — live !== settled.
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
