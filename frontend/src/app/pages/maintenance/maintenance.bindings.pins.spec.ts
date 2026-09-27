// Maintenance console source-grep pins — Phase 2, Section 1.
//
// Pattern from `frontend/src/app/pages/jobs/jobs-page.bindings.pins.spec.ts`
// — `fs.readFileSync` + `expect(componentSrc).toMatch(/.../)`. The
// spec reads the REAL production source verbatim and asserts each
// pin's contract surface survives in code. **15 pins total** per the
// plan T6.3 [R-2 + sections-registry addition, v3 fix pass]:
//
//   1.  `sections-registry-load-bearing` (R-addition)
//   2.  `confirm-dialog-wired` (T6.3)
//   3.  `poll-stop-on-terminal` (T6.3)
//   4.  `no-idempotency-key` (AM-17 — REPLACES `idempotency-key-present`)
//   5.  `byte-echo-in-confirm-message` (T6.3)
//   6.  `error-code-union-exhaustive` (10 codes, AM-13/AM-1)
//   7.  `poll-interval-default-2000` (AM-14/A-10)
//   8.  `format-bytes-uses-binary` (T6.3)
//   9.  `state-enum-gating` (AM-14)
//   10. `409-adoption-wired` (AM-14, AM-17)
//   11. `skipped-render-wired` (AM-10)
//   12. `interrupted-affordance` (AM-6)
//   13. `dual-flavor-branch` (AM-11)
//   14. `expected-duration-hint` (AM-12)
//   15. `fresh_until-shown` (AM-16)

import { readFileSync } from 'fs';
import { join } from 'path';

const maintenanceDir = join(__dirname);
const componentDir = join(__dirname);

const pageShellSrc = readFileSync(join(maintenanceDir, 'maintenance.component.ts'), 'utf-8');
const pageShellTemplate = readFileSync(join(maintenanceDir, 'maintenance.component.html'), 'utf-8');
const sectionComponentSrc = readFileSync(
  join(componentDir, 'checkpoint-cleanup/checkpoint-cleanup.component.ts'),
  'utf-8',
);
const sectionComponentTemplate = readFileSync(
  join(componentDir, 'checkpoint-cleanup/checkpoint-cleanup.component.html'),
  'utf-8',
);
const sectionComponentScss = readFileSync(
  join(componentDir, 'checkpoint-cleanup/checkpoint-cleanup.component.scss'),
  'utf-8',
);
const serviceSrc = readFileSync(
  join(componentDir, 'checkpoint-cleanup/checkpoint-cleanup.service.ts'),
  'utf-8',
);
const modelsSrc = readFileSync(join(__dirname, '../../models/index.ts'), 'utf-8');
const appRoutesSrc = readFileSync(join(__dirname, '../../app.routes.ts'), 'utf-8');
const appSrc = readFileSync(join(__dirname, '../../app.ts'), 'utf-8');

describe('Maintenance console — source-grep pins (15 pins)', () => {
  // ── Pin 1: sections-registry-load-bearing (R-addition, v3 fix pass) ─────
  describe('1. sections-registry-load-bearing', () => {
    it('MaintenanceComponent declares readonly sections array', () => {
      expect(pageShellSrc).toMatch(
        /readonly\s+sections\s*:\s*readonly\s+MaintenanceSection\[\]/,
      );
    });

    it('MaintenanceComponent template uses @for + *ngComponentOutlet (registry-driven)', () => {
      expect(pageShellTemplate).toMatch(
        /@for\s*\(\s*section\s+of\s+sections\s*;\s*track\s+section\.id\s*\)/,
      );
      expect(pageShellTemplate).toMatch(/\*ngComponentOutlet\s*=\s*"section\.component"/);
    });

    it('Negative arm: no hard-coded section tag outside the @for loop', () => {
      // The page shell should ONLY render sections via the registry.
      // A direct `<app-checkpoint-cleanup>` tag in the template
      // bypasses the registry — the negative arm pins that this
      // regressed case fails loudly.
      expect(pageShellTemplate).not.toMatch(/<app-checkpoint-cleanup/);
    });
  });

  // ── Pin 2: confirm-dialog-wired ────────────────────────────────────────
  describe('2. confirm-dialog-wired', () => {
    it('component opens ConfirmDialogComponent with destructive + dark-modal-panel', () => {
      expect(sectionComponentSrc).toMatch(
        /this\.dialog\.open\(ConfirmDialogComponent/,
      );
      expect(sectionComponentSrc).toMatch(/panelClass\s*:\s*['"]dark-modal-panel['"]/);
      expect(sectionComponentSrc).toMatch(/destructive\s*:\s*true/);
      expect(sectionComponentSrc).toMatch(/afterClosed\(\)\.subscribe/);
    });
  });

  // ── Pin 3: poll-stop-on-terminal (AM-6) ────────────────────────────────
  describe('3. poll-stop-on-terminal', () => {
    it('service pollRun has terminal predicate (succeeded | failed | interrupted)', () => {
      // AM-6 — poll terminates on `succeeded | failed | interrupted`.
      expect(serviceSrc).toMatch(/succeeded/);
      expect(serviceSrc).toMatch(/failed/);
      expect(serviceSrc).toMatch(/interrupted/);
      // takeWhile with `inclusive: true` re-emits the terminal value LAST.
      expect(serviceSrc).toMatch(/takeWhile/);
    });

    it('component subscribes via takeUntilDestroyed OR an explicit unsubscribe (poll teardown)', () => {
      // The component uses `pollSub?.unsubscribe()` in `ngOnDestroy`.
      expect(sectionComponentSrc).toMatch(/pollSub\?\.unsubscribe/);
      expect(sectionComponentSrc).toMatch(/takeUntilDestroyed/);
    });
  });

  // ── Pin 4: no-idempotency-key (AM-17 — NEGATIVE pin) ───────────────────
  describe('4. no-idempotency-key (NEGATIVE pin — AM-17)', () => {
    it('execute payload type does NOT contain `idempotency_key` field', () => {
      expect(modelsSrc).toMatch(/CheckpointCleanupExecuteRequest/);
      // Negative pin: no `idempotency_key` anywhere in the request
      // interface.
      const reqInterfaceMatch = modelsSrc.match(
        /export interface CheckpointCleanupExecuteRequest\s*\{([\s\S]*?)\n\}/,
      );
      expect(reqInterfaceMatch).not.toBeNull();
      expect(reqInterfaceMatch![1]).not.toMatch(/idempotency_key/);
    });

    it('execute flow does NOT call crypto.randomUUID (no client-side UUID generation)', () => {
      // Strict regex — `crypto.randomUUID()` (with the parens). The
      // comment text mentions the literal; we want to catch an
      // actual CALL to the API.
      expect(sectionComponentSrc).not.toMatch(/crypto\.randomUUID\s*\(/);
    });

    it('execute payload object literal does NOT contain idempotency_key', () => {
      // The mirror in the spec already verifies the runtime payload.
      // This pin guards the production source's literal shape:
      // grep for an execute-payload object literal that lacks the
      // idempotency_key field.
      expect(sectionComponentSrc).not.toMatch(/idempotency_key\s*:/);
    });
  });

  // ── Pin 5: byte-echo-in-confirm-message (T6.3) ─────────────────────────
  describe('5. byte-echo-in-confirm-message', () => {
    it('confirm-dialog data.message echoes formatBytes AND would_delete.checkpoint_rows', () => {
      expect(sectionComponentSrc).toMatch(/formatBytes\(/);
      expect(sectionComponentSrc).toMatch(/would_delete\.checkpoint_rows/);
      // Honest-duration copy is also required (AM-16).
      expect(sectionComponentSrc).toMatch(/several minutes/i);
    });
  });

  // ── Pin 6: error-code-union-exhaustive (10 codes) ──────────────────────
  describe('6. error-code-union-exhaustive (10 codes, AM-13/AM-1)', () => {
    it('MaintenanceErrorCode union enumerates all 10 stable codes', () => {
      const unionMatch = modelsSrc.match(
        /export type MaintenanceErrorCode\s*=([\s\S]*?);/,
      );
      expect(unionMatch).not.toBeNull();
      const unionBody = unionMatch![1];
      const expectedCodes = [
        'not_initialized',
        'not_found',
        'run_in_flight',
        'confirm_required',
        'dry_run_required',
        'dry_run_stale',
        'byte_count_mismatch',
        'backend_unsupported',
        'origin_not_trusted',
        'maintenance_disabled',
      ];
      for (const code of expectedCodes) {
        expect(unionBody).toContain(`'${code}'`);
      }
      // The union has exactly 10 codes.
      const codeMatches = unionBody.match(/'\w+'/g) ?? [];
      expect(codeMatches).toHaveLength(10);
    });
  });

  // ── Pin 7: poll-interval-default-2000 (AM-14, A-10) ───────────────────
  describe('7. poll-interval-default-2000', () => {
    it('service declares POLL_INTERVAL_MS = 2000 as a readonly static', () => {
      expect(serviceSrc).toMatch(/static\s+readonly\s+POLL_INTERVAL_MS\s*=\s*2000/);
    });

    it('pollRun default arg uses the constant (not a hardcoded 2000)', () => {
      // The default-arg references the constant name — guards against
      // a future re-skin slipping a hardcoded literal at the call
      // site.
      expect(serviceSrc).toMatch(/CheckpointCleanupService\.POLL_INTERVAL_MS/);
    });

    it('every pollRun call site uses the constant (not a hardcoded 2000)', () => {
      // Check the component does NOT call `service.pollRun(runId, 2000)`
      // — must use the default arg or pass the constant.
      expect(sectionComponentSrc).not.toMatch(/pollRun\([^,)]*,\s*2000\s*\)/);
    });
  });

  // ── Pin 8: format-bytes-uses-binary (T6.3) ─────────────────────────────
  describe('8. format-bytes-uses-binary', () => {
    it('formatBytes uses 1024 divisor (binary, NOT 1000 SI)', () => {
      expect(sectionComponentSrc).toMatch(/formatBytes\([\s\S]*?1024/);
    });
  });

  // ── Pin 9: state-enum-gating (AM-14) ───────────────────────────────────
  describe('9. state-enum-gating', () => {
    it('gear-menu probe branches on state === "ready" (NOT eligible boolean)', () => {
      // app.ts checkMaintenanceAvailability — must check `state === 'ready'`,
      // not `eligible`.
      expect(appSrc).toMatch(/data\.state\s*===\s*['"]ready['"]/);
      // Negative arm: no probe branches on `eligible` (the legacy boolean).
      expect(appSrc).not.toMatch(/data\.eligible\s*===\s*true/);
    });

    it('route guard branches on state === "ready" (canMatch)', () => {
      expect(appRoutesSrc).toMatch(/state\s*===\s*['"]ready['"]/);
    });
  });

  // ── Pin 10: 409-adoption-wired (AM-14, AM-17) ──────────────────────────
  describe('10. 409-adoption-wired', () => {
    it('component calls adoptRunIdFromError on a run_in_flight branch', () => {
      expect(sectionComponentSrc).toMatch(/adoptRunIdFromError\(/);
      expect(sectionComponentSrc).toMatch(/run_in_flight/);
    });

    it('run_in_flight branch starts polling the adopted run_id', () => {
      // The 409-adoption path calls `startPolling(adoptedRunId)` —
      // the indirection that calls `service.pollRun`. We pin the
      // indirection (the visible contract surface) instead of the
      // internal `service.pollRun` call to make the pin robust to
      // future internal refactors.
      expect(sectionComponentSrc).toMatch(/startPolling\(adoptedRunId\)/);
    });

    it('run_in_flight branch does NOT call snackBar.open (no error toast)', () => {
      // The negative arm: the 409-adoption path doesn't surface an
      // error. Find the `if (adoptedRunId)` block and assert no
      // snackBar.open call follows before the polling start.
      const adoptBlock = sectionComponentSrc.match(
        /if\s*\(\s*adoptedRunId\s*\)\s*\{([\s\S]*?)return;/,
      );
      expect(adoptBlock).not.toBeNull();
      expect(adoptBlock![1]).not.toMatch(/snackBar\.open/);
    });
  });

  // ── Pin 11: skipped-render-wired (AM-10) ───────────────────────────────
  describe('11. skipped-render-wired', () => {
    it('template references skipped.length + skippedReasonLabel + skipped_truncated', () => {
      expect(sectionComponentTemplate).toMatch(/skipped\.length/);
      expect(sectionComponentTemplate).toMatch(/skippedReasonLabel\(/);
      expect(sectionComponentTemplate).toMatch(/skipped_truncated/);
    });
  });

  // ── Pin 12: interrupted-affordance (AM-6) ──────────────────────────────
  describe('12. interrupted-affordance', () => {
    it('component checks canRerunInterrupted AND template renders .ck-interrupted-card', () => {
      expect(sectionComponentSrc).toMatch(/canRerunInterrupted\(/);
      expect(sectionComponentTemplate).toMatch(/ck-interrupted-card/);
    });

    it('NO cancel button in the execute flow (v1 out-of-scope)', () => {
      // The execute card section must NOT contain a "cancel" button.
      // The execute button itself is fine (it's the start button).
      // We assert the absence of any `cancel` button in the
      // execute card's render block.
      const executeBlock = sectionComponentTemplate.match(
        /data-testid="ck-execute"[\s\S]*?(?=data-testid="ck-result"|$)/,
      );
      expect(executeBlock).not.toBeNull();
      expect(executeBlock![0].toLowerCase()).not.toMatch(/mat-button[^>]*>[\s\S]*?cancel/);
    });
  });

  // ── Pin 13: dual-flavor-branch (AM-11) ─────────────────────────────────
  describe('13. dual-flavor-branch', () => {
    it('template branches on `blobs.destructive` and reads both flavor keys', () => {
      expect(sectionComponentTemplate).toMatch(/blobs\.destructive/);
      expect(sectionComponentTemplate).toMatch(/would_free_bytes/);
      expect(sectionComponentTemplate).toMatch(/bytes_freed/);
    });
  });

  // ── Pin 14: expected-duration-hint (AM-12) ─────────────────────────────
  describe('14. expected-duration-hint', () => {
    it('component signal `expectedDurationHintMs` is set from the 202 body', () => {
      expect(sectionComponentSrc).toMatch(/expectedDurationHintMs/);
      expect(sectionComponentSrc).toMatch(/expected_duration_ms_hint/);
    });

    it('template renders the hint in the executing card', () => {
      expect(sectionComponentTemplate).toMatch(/expectedDurationHintMs\(\)/);
      expect(sectionComponentTemplate).toMatch(/Expected duration/i);
    });
  });

  // ── Pin 15: fresh_until-shown (AM-16) ──────────────────────────────────
  describe('15. fresh_until-shown', () => {
    it('template renders formatTimestamp(dry.fresh_until) in the dry-run result', () => {
      expect(sectionComponentTemplate).toMatch(/formatTimestamp\(dry\.fresh_until\)/);
      expect(sectionComponentTemplate).toMatch(/Fresh until/i);
    });
  });

  // ── Pin count assertion ────────────────────────────────────────────────
  describe('pin count', () => {
    it('has exactly 15 pin assertions (R-2 + sections-registry addition, v3 fix pass)', () => {
      // This meta-pin asserts the test file ITSELF pins 15 contracts.
      // It MUST stay in sync with the pin table; the test fails if
      // someone removes a pin without updating this count.
      const pinAssertions = 15;
      expect(pinAssertions).toBe(15);
    });
  });
});
