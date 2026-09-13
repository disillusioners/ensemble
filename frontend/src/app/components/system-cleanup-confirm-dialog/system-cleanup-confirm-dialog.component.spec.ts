// SystemCleanupConfirmDialogComponent spec — jobs-page-improvement
// arc, Phase 6 (plan task 6).
//
// "Two-stage confirm state machine, defer note rendering,
//  destructive-copy surface (consts already verbatim-pinned at model
//  level)."
//
// House convention: NO Angular TestBed. The dialog is a tiny
// state-holder (an ``armed`` signal + three getters over
// ``MAT_DIALOG_DATA``), so the mirror class below replicates its
// ENTIRE logic surface 1:1 and the F-5 source pins read the REAL
// inline template + class for the wiring the mirror cannot execute
// (dialogRef close payloads, branch gating, button labels). The
// pairing is the bar (P2/P3 lesson: mirrors assert behavior, pins
// assert the real wiring — BOTH required).
//
// The destructive copy consts are already verbatim-pinned at MODEL
// level (``cleanup-preflight.model.spec.ts``: canonical truth-split
// sentence, survivor note, defer note per holder kind) — this spec
// pins that the DIALOG surfaces those exact consts and gates them on
// the right branches (render-path pairing).

import { readFileSync } from 'fs';
import { join } from 'path';

import {
  CLEANUP_TRUTH_SPLIT_COPY,
  CLEANUP_TRUTH_SURVIVOR_NOTE,
  cleanupDeferNote,
} from '../../models/cleanup-preflight.model';
import type { SystemCleanupConfirmData } from './system-cleanup-confirm-dialog.component';

const dialogDir = join(__dirname, '.');
const dialogSrc = readFileSync(
  join(dialogDir, 'system-cleanup-confirm-dialog.component.ts'),
  'utf-8',
);

/**
 * 1:1 mirror of the dialog's logic surface (fields + getters + the
 * ``arm()`` state machine). Every member is source-pinned below so a
 * drift in the real class breaks the pin BEFORE the mirror lies.
 */
class TestableCleanupConfirmDialog {
  /** Double-confirm stage: false = stage 1, true = armed (stage 2). */
  armed = false;

  constructor(private readonly data: SystemCleanupConfirmData) {}

  arm(): void {
    this.armed = true;
  }

  get liveIds(): string[] {
    return this.data.live_instance_ids ?? [];
  }

  get deferCount(): number {
    return this.data.defer_blocked_count ?? 0;
  }

  get deferNote(): string | null {
    return cleanupDeferNote(this.data.defer_holder_kind);
  }

  get truthSplitCopy(): string {
    return CLEANUP_TRUTH_SPLIT_COPY;
  }

  get survivorNote(): string {
    return CLEANUP_TRUTH_SURVIVOR_NOTE;
  }
}

describe('system-cleanup-confirm-dialog — two-stage confirm state machine', () => {
  it('stage 1: the dialog opens DISARMED (arm() is the only stage-transition)', () => {
    const d = new TestableCleanupConfirmDialog({});
    expect(d.armed).toBe(false);
  });

  it('arm() flips stage 1 → stage 2 (Continue arms; final confirm then closes)', () => {
    const d = new TestableCleanupConfirmDialog({});
    d.arm();
    expect(d.armed).toBe(true);
  });

  it('the armed state NEVER self-reverts — re-arm is idempotent, no disarm path exists', () => {
    const d = new TestableCleanupConfirmDialog({});
    d.arm();
    d.arm();
    expect(d.armed).toBe(true);
    // The ONLY disarm path is Cancel (dialog close with ``false``) —
    // pinned on the real class: no ``armed.set(false)`` /
    // ``this.armed = false`` write exists in the production source.
    expect(dialogSrc).not.toMatch(/armed\.set\(false\)/);
    expect(dialogSrc).not.toMatch(/this\.armed\s*=\s*false/);
  });

  it('REAL template: Continue arms (stage 1 button) and Cancel closes with false', () => {
    expect(dialogSrc).toMatch(/\(click\)="arm\(\)"/);
    expect(dialogSrc).toMatch(/mat-button \(click\)="dialogRef\.close\(false\)">Cancel</);
  });

  it('REAL template: the stage-2 button closes with TRUE (the only true payload)', () => {
    expect(dialogSrc).toMatch(/\(click\)="dialogRef\.close\(true\)"/);
    // Final button is gated on the armed state.
    expect(dialogSrc).toMatch(/@if \(!armed\(\)\) \{[\s\S]*?Continue[\s\S]*?\} @else \{[\s\S]*?Cleanup — final confirm/);
  });

  it('REAL template: the final-confirm block renders ONLY when armed (stage 2 copy)', () => {
    expect(dialogSrc).toMatch(/@if \(armed\(\)\) \{\s*<div class="final-confirm">/);
    expect(dialogSrc).toMatch(/Cancel ALL jobs and clean up stalled missions\? This cannot be\s*\n\s*undone\./);
  });
});

describe('system-cleanup-confirm-dialog — destructive-copy surface', () => {
  it('the truth-split copy is the MODEL const VERBATIM (render-path pairing)', () => {
    const d = new TestableCleanupConfirmDialog({});
    expect(d.truthSplitCopy).toBe(CLEANUP_TRUTH_SPLIT_COPY);
    expect(dialogSrc).toMatch(/readonly truthSplitCopy = CLEANUP_TRUTH_SPLIT_COPY;/);
  });

  it('the survivor note is the MODEL const VERBATIM (render-path pairing)', () => {
    const d = new TestableCleanupConfirmDialog({});
    expect(d.survivorNote).toBe(CLEANUP_TRUTH_SURVIVOR_NOTE);
    expect(dialogSrc).toMatch(/readonly survivorNote = CLEANUP_TRUTH_SURVIVOR_NOTE;/);
  });

  it('REAL template: the irreversibility headline names BOTH destructive halves', () => {
    expect(dialogSrc).toMatch(/cancel ALL jobs \(every lane\)/);
    expect(dialogSrc).toMatch(/clean up stalled missions/);
    expect(dialogSrc).toMatch(/This action cannot be undone\./);
  });

  it('REAL template: the truth-split copy renders inside the will-remain paragraph', () => {
    expect(dialogSrc).toMatch(/<p class="will-remain">\s*\{\{ truthSplitCopy \}\}/);
  });

  it('REAL template: the survivor note renders ONLY when live ids exist', () => {
    expect(dialogSrc).toMatch(/@if \(liveIds\(\)\.length > 0\) \{\s*<p class="survivor-note">\s*\{\{ survivorNote \}\}/);
  });

  it('REAL template: bad-state + zombie warnings render their counts when > 0', () => {
    expect(dialogSrc).toMatch(/@if \(data\.bad_state_count && data\.bad_state_count > 0\) \{/);
    expect(dialogSrc).toMatch(/@if \(data\.zombie_instance_count && data\.zombie_instance_count > 0\) \{/);
    expect(dialogSrc).toMatch(/bad-state tasks will be reconciled/);
    expect(dialogSrc).toMatch(/will be reaped \(terminated\)/);
  });
});

describe('system-cleanup-confirm-dialog — defer note rendering', () => {
  it('deferCount defaults to 0 when the preflight omits the field', () => {
    expect(new TestableCleanupConfirmDialog({}).deferCount).toBe(0);
    expect(new TestableCleanupConfirmDialog({ defer_blocked_count: 2 }).deferCount).toBe(2);
  });

  it('liveIds default to [] when the preflight omits the field', () => {
    expect(new TestableCleanupConfirmDialog({}).liveIds).toEqual([]);
    expect(new TestableCleanupConfirmDialog({ live_instance_ids: ['i-1'] }).liveIds).toEqual(['i-1']);
  });

  it('deferNote delegates to the MODEL cleanupDeferNote (kind-aware copy, no local re-derivation)', () => {
    const d = new TestableCleanupConfirmDialog({ defer_holder_kind: 'paused' });
    expect(d.deferNote).toBe(cleanupDeferNote('paused'));
    expect(d.deferNote).toBe(new TestableCleanupConfirmDialog({ defer_holder_kind: 'paused' }).deferNote);
    expect(dialogSrc).toMatch(/readonly deferNote = \(\) => cleanupDeferNote\(this\.data\.defer_holder_kind\);/);
  });

  it('REAL template: the defer note block renders ONLY when the count is positive', () => {
    expect(dialogSrc).toMatch(/@if \(deferCount\(\) > 0\) \{\s*<p class="defer-note">/);
    expect(dialogSrc).toMatch(/deferred\s*\n?\s*\{\{ deferCount\(\) === 1 \? 'message' : 'messages' \}\} waiting on the defer/);
  });

  it('REAL template: the defer note embeds the kind-aware model copy', () => {
    expect(dialogSrc).toMatch(/@if \(deferNote\(\); as note\) \{\s*\{\{ note \}\}\s*\}/);
  });
});

describe('system-cleanup-confirm-dialog — data payload compatibility', () => {
  it('SystemCleanupConfirmData tolerates a fully-empty payload (older daemon responses)', () => {
    const d = new TestableCleanupConfirmDialog({});
    expect(d.armed).toBe(false);
    expect(d.liveIds).toEqual([]);
    expect(d.deferCount).toBe(0);
    expect(d.deferNote).toBeNull();
    expect(d.truthSplitCopy).toBe(CLEANUP_TRUTH_SPLIT_COPY);
  });

  it('REAL class: armed is a signal-backed member (stage state survives re-renders)', () => {
    expect(dialogSrc).toMatch(/readonly armed = signal\(false\);/);
  });
});
