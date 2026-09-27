// Maintenance console source-grep pins — Phase 2, Section 1.
//
// Pattern from `frontend/src/app/pages/jobs/jobs-page.bindings.pins.spec.ts`
// — `fs.readFileSync` + `expect(componentSrc).toMatch(/.../)`. The
// spec reads the REAL production source verbatim and asserts each
// pin's contract surface survives in code. **18 pins total** per the
// plan T6.3 [R-2 + sections-registry addition, v3 fix pass +
// v3.2 amendment]:
//
//   1.  `sections-registry-load-bearing` (R-addition)
//   2.  `confirm-dialog-wired` (T6.3)
//   3.  `poll-stop-on-terminal` (T6.3)
//   4.  `no-idempotency-key` (AM-17 — REPLACES `idempotency-key-present`)
//   5.  `byte-echo-in-confirm-message` (T6.3)
//   6.  `error-code-union-exhaustive` (11 codes, AM-13/AM-1 + A-8)
//   7.  `poll-interval-default-2000` (AM-14/A-10)
//   8.  `format-bytes-uses-binary` (T6.3)
//   9.  `state-enum-gating` (AM-14)
//   10. `409-adoption-wired` (AM-14, AM-17)
//   11. `skipped-render-wired` (AM-10)
//   12. `interrupted-affordance` (AM-6)
//   13. `dual-flavor-branch` (AM-11)
//   14. `expected-duration-hint` (AM-12)
//   15. `fresh_until-shown` (AM-16)
//   16. `projection-fields-render` (v3.2 amendment, R-1/R-4)
//   17. `confirm-message-journey-copy` (v3.2 amendment)
//   18. `run-again-banner-when-projection-nonzero` (v3.2 amendment, R-5)

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
// Item 19 — `sectionComponentScss` was a dead read (loaded but never
// asserted against). Deleted. The SCSS file carries no critical
// contract surface worth a source-grep pin (no token selectors
// that downstream code depends on); if a future pin is needed,
// add it here with a deliberate contract statement.
const serviceSrc = readFileSync(
  join(componentDir, 'checkpoint-cleanup/checkpoint-cleanup.service.ts'),
  'utf-8',
);
const modelsSrc = readFileSync(join(__dirname, '../../models/index.ts'), 'utf-8');
const appRoutesSrc = readFileSync(join(__dirname, '../../app.routes.ts'), 'utf-8');
const appSrc = readFileSync(join(__dirname, '../../app.ts'), 'utf-8');

describe('Maintenance console — source-grep pins (18 pins)', () => {
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

  // ── Pin 6: error-code-union-exhaustive (11 codes incl. A-8) ────────────
  describe('6. error-code-union-exhaustive (11 codes incl. A-8 internal_error, AM-13/AM-1)', () => {
    // Item 13 amendment — the wire union is now DERIVED from the
    // canonical tuple `MAINTENANCE_ERROR_CODES` via
    // `(typeof MAINTENANCE_ERROR_CODES)[number]`. The pin asserts
    // (a) the tuple contains all 11 expected codes in the canonical
    // order AND (b) the derived union is present (type-derivation
    // syntax) AND (c) the single-source `isKnownErrorCode` guard
    // exists. The wire contract (frozen 11 members) is still pinned.
    it('MaintenanceErrorCode derived from canonical tuple MAINTENANCE_ERROR_CODES (11 codes incl. A-8 internal_error)', () => {
      // (a) The canonical tuple lists all 11 codes in the canonical
      // order — frozen contract from v3 fix pass + A-8 amendment.
      const tupleMatch = modelsSrc.match(
        /export const MAINTENANCE_ERROR_CODES\s*=\s*\[([\s\S]*?)\]\s*as const/,
      );
      expect(tupleMatch).not.toBeNull();
      const tupleBody = tupleMatch![1];
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
        'internal_error',
      ];
      for (const code of expectedCodes) {
        expect(tupleBody).toContain(`'${code}'`);
      }
      // Tuple has exactly 11 entries (10 stable + A-8 internal_error).
      const tupleCodeMatches = tupleBody.match(/'\w+'/g) ?? [];
      expect(tupleCodeMatches).toHaveLength(11);
      // (b) Derived union — pin that the tuple-to-type derivation
      // syntax is present.
      expect(modelsSrc).toMatch(
        /export type MaintenanceErrorCode\s*=\s*\(typeof MAINTENANCE_ERROR_CODES\)\[number\]/,
      );
      // (c) Single-source guard is exported.
      expect(modelsSrc).toMatch(/export function isKnownErrorCode/);
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
    // Item 11 amendment — the dual-flavor branching was lifted out
    // of the template into `blobBytesFor()` / `blobCountFor()`
    // helpers on the component class. The pin now asserts (a) the
    // template consumes the helpers, (b) the component source
    // branches on `blobs.destructive` AND reads both flavor keys
    // (`would_free_bytes` + `bytes_freed`) — i.e. the contract lives
    // in the helpers, not the template.
    it('dual-flavor branch lives in blobBytesFor / blobCountFor helpers (template consumes them)', () => {
      // Template consumes the helpers — both labels + values via the
      // helpers, not via inline `destructive ? ... : ...` ternaries.
      expect(sectionComponentTemplate).toMatch(/blobBytesFor\(/);
      expect(sectionComponentTemplate).toMatch(/blobCountFor\(/);
      // Component source owns the branching + both flavor keys.
      expect(sectionComponentSrc).toMatch(/blobs\.destructive/);
      expect(sectionComponentSrc).toMatch(/would_free_bytes/);
      expect(sectionComponentSrc).toMatch(/bytes_freed/);
      // Negative arm: the template MUST NOT contain inline dual-flavor
      // ternaries (`destructive ? ... : ...`) — that contract moved
      // to the helpers.
      expect(sectionComponentTemplate).not.toMatch(
        /\.destructive\s*\?\s*\(?[^:]*bytes_freed/,
      );
      expect(sectionComponentTemplate).not.toMatch(
        /\.destructive\s*\?\s*[^:]*:\s*[^.]*would_free_bytes/,
      );
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

  // ── Pin 16: projection-fields-render (v3.2 amendment, R-1 + R-4) ─────
  describe('16. projection-fields-render (v3.2 — R-1/R-4)', () => {
    // Pin 16 — three-number render (now / after / total) + skip-flag
    // honesty banner. The component reads three dry-run fields via
    // its helpers, and the template renders them via those helpers.
    // Zero components render as "—" (amendment copy) — the helper
    // implementation lives in the component source; the template
    // wires the helpers and the skipped-flag block.

    it('component source reads `bytes_reclaimable_now` (via the `dryRunProjectionNow` helper)', () => {
      expect(sectionComponentSrc).toMatch(/dryRunProjectionNow/);
      // The literal field name MUST appear in the source — a
      // refactor that renames the wire field without updating the
      // model is caught here.
      expect(sectionComponentSrc).toMatch(/bytes_reclaimable_now/);
    });

    it('component source reads `bytes_reclaimable_after_row_prune` (via the `dryRunProjectionAfter` helper)', () => {
      expect(sectionComponentSrc).toMatch(/dryRunProjectionAfter/);
      expect(sectionComponentSrc).toMatch(/bytes_reclaimable_after_row_prune/);
    });

    it('component source reads `bytes_reclaimable_total` (via the `dryRunProjectionTotal` helper)', () => {
      expect(sectionComponentSrc).toMatch(/dryRunProjectionTotal/);
      expect(sectionComponentSrc).toMatch(/bytes_reclaimable_total/);
    });

    it('template calls the three projection helpers in the three-number render block', () => {
      // All three helper invocations MUST be present in the
      // template — the three-number render is a single amendment
      // shape; deleting one drops the consent-time number.
      expect(sectionComponentTemplate).toMatch(/dryRunProjectionNow\(\s*dry\s*\)/);
      expect(sectionComponentTemplate).toMatch(/dryRunProjectionAfter\(\s*dry\s*\)/);
      expect(sectionComponentTemplate).toMatch(/dryRunProjectionTotal\(\s*dry\s*\)/);
    });

    it('template renders the R-4 skip-flag honesty banner when `dry.skipped.length > 0`', () => {
      // The skipped list is already wired (Pin 11); the new
      // honest-copy sits near the projection. Both branches use
      // `dry.skipped.length > 0`.
      expect(sectionComponentTemplate).toMatch(
        /dryRunSkippedHonestyActive\([\s\S]*?dry\s*\)/,
      );
      expect(sectionComponentTemplate).toMatch(/dry\.skipped\.length\s*>\s*0/);
      // The honesty-copy wording (R-4) MUST appear verbatim — any
      // drift fails the pin.
      expect(sectionComponentTemplate).toMatch(/cleanup effectiveness may be understated/);
    });

    it('never-pruned sub-copy renders when isNeverPrunedProfile(dry) is true', () => {
      // Amendment copy: "On a DB that has never run retention, run
      // 1 deletes rows only; run 2 frees the blob bytes."
      expect(sectionComponentSrc).toMatch(/isNeverPrunedProfile/);
      expect(sectionComponentTemplate).toMatch(/isNeverPrunedProfile\(\s*dry\s*\)/);
      expect(sectionComponentTemplate).toMatch(/never run retention, run 1 deletes rows only/);
    });
  });

  // ── Pin 17: confirm-message-journey-copy (v3.2 amendment) ────────────
  describe('17. confirm-message-journey-copy (v3.2)', () => {
    // Pin 17 EXTENDS Pin 5 (byte-echo-in-confirm-message, AM-16).
    // Both still must pass; this pin adds the journey copy.

    it('message reads bytes_reclaimable_now (this-run) AND bytes_reclaimable_after_row_prune (follow-up) via formatBytes', () => {
      // Anchor on the unique journey-anchor literal `running cleanup again`
      // (only present in the v3.2 consent-instrument copy). Capturing
      // forward to the template-literal terminator + statement-close
      // gives us `buildConfirmMessage`'s body verbatim. The Pin 5
      // static grep on `formatBytes(` still covers the overall
      // helper-usage contract; this pin narrows to the AMENDMENT
      // contract specifically.
      const buildMsg = sectionComponentSrc.match(
        /private\s+buildConfirmMessage[\s\S]*?running cleanup again[\s\S]*?\)\s*;/,
      );
      expect(buildMsg).not.toBeNull();
      // Two `formatBytes(` calls (now + after) — at least 2.
      const formatBytesCount = (buildMsg![0].match(/formatBytes\(/g) ?? []).length;
      expect(formatBytesCount).toBeGreaterThanOrEqual(2);
      // The two field references used in the consent-instrument copy.
      expect(buildMsg![0]).toMatch(/bytes_reclaimable_now/);
      expect(buildMsg![0]).toMatch(/bytes_reclaimable_after_row_prune/);
      // rows echoed (Pin 5 carries).
      expect(buildMsg![0]).toMatch(/would_delete\.checkpoint_rows/);
    });

    it('message contains the "running cleanup again" anchor literal', () => {
      // The amendment copy is the canonical consent-instrument
      // journey phrase — `running cleanup again` is the anchor.
      expect(sectionComponentSrc).toMatch(/running cleanup again/);
    });

    it('AM-16 honest-duration copy still present in the confirm message', () => {
      // Pin 5 (T6.3) keeps its contract — the journey copy extends
      // the existing message, doesn't replace it.
      expect(sectionComponentSrc).toMatch(/several minutes/i);
      expect(sectionComponentSrc).toMatch(/cannot be undone/i);
    });
  });

  // ── Pin 18: run-again-banner-when-projection-nonzero (v3.2, R-5) ─────
  describe('18. run-again-banner-when-projection-nonzero (v3.2 — R-5)', () => {
    // Pin 18 — the post-run convergence banner. Visible when (a)
    // last execute succeeded, (b) projection.after > 0; hides when
    // a fresh dry-run reports `bytes_reclaimable_now == 0`. The
    // CTA button starts a NEW dry-run (onDryRun binding) — NEVER
    // a silent execute. The button is disabled while a run is in
    // flight.

    it('component source defines `showRunAgainBanner()` reading `bytes_reclaimable_after_row_prune_at_dry_run`', () => {
      expect(sectionComponentSrc).toMatch(/showRunAgainBanner\s*\(/);
      expect(sectionComponentSrc).toMatch(
        /bytes_reclaimable_after_row_prune_at_dry_run/,
      );
    });

    it('banner visibility converges (`bytes_reclaimable_now == 0` hides it)', () => {
      // Anchored on the `showRunAgainBanner` definition so the
      // convergence check is read inside the visibility decision,
      // not somewhere else.
      const bannerShow = sectionComponentSrc.match(
        /showRunAgainBanner\s*\([\s\S]*?(?=\n\s*private\s|\n\s*}\s*\/\*|\n\s*\/\*\*\s*\n)/,
      );
      expect(bannerShow).not.toBeNull();
      expect(bannerShow![0]).toMatch(/bytes_reclaimable_now/);
    });

    it('template renders `data-testid="ck-run-again-banner"` (banner DOM marker)', () => {
      expect(sectionComponentTemplate).toMatch(/data-testid="ck-run-again-banner"/);
    });

    it('banner CTA wired to `(click)="onDryRun()"` (NEVER onExecute) — no silent execute', () => {
      // Negative arm: the run-again button MUST bind to onDryRun,
      // not onExecute. A silent-execute regression (a re-binding
      // to `onExecute()`) is caught here.
      // We anchor on the run-again button testid and look for the
      // first `(click)` binding within its opening tag block.
      const runAgainButton = sectionComponentTemplate.match(
        /data-testid="ck-run-again-btn"[\s\S]*?>/,
      );
      expect(runAgainButton).not.toBeNull();
      expect(runAgainButton![0]).toMatch(/\(click\)\s*=\s*["']onDryRun\(\)["']/);
      expect(runAgainButton![0]).not.toMatch(/\(click\)\s*=\s*["']onExecute\(\)["']/);
    });

    it('banner CTA disabled while ANY run is in flight (dry-run / execute / daemon isRunInFlight)', () => {
      const runAgainButton = sectionComponentTemplate.match(
        /data-testid="ck-run-again-btn"[\s\S]*?>/,
      );
      expect(runAgainButton).not.toBeNull();
      // All three in-flight signals MUST gate the button.
      expect(runAgainButton![0]).toMatch(/dryRunning\(\)/);
      expect(runAgainButton![0]).toMatch(/executing\(\)/);
      expect(runAgainButton![0]).toMatch(/isRunInFlight\(\)/);
    });
  });

  // ── Pin count assertion ────────────────────────────────────────────────
  describe('pin count', () => {
    // Item 2 — derive the pin-count from this file's OWN describe
    // blocks (each `describe('N. ...', …)` represents one pin
    // contract). The previous `expect(15).toBe(15)` tautology was
    // SELF-READING-PIN-TAUTOLOGY (constant-True once the literal
    // appears in the assert line). The new pin reads this spec's own
    // source via `readFileSync`, counts the numeric-prefix describes,
    // and asserts the count is > 0 — removing a describe('N. …')
    // block breaks the count automatically.
    it('pin-count derives from this file\'s own describe blocks (no self-tautology)', () => {
      const selfSrc = readFileSync(__filename, 'utf-8');
      // Match the pin describes — anchored to line start + 2-space
      // indent (the actual pin describes are all top-level children
      // of the parent describe; comment-block prose uses deeper
      // indentation and would otherwise false-match the regex).
      const pinDescribes =
        selfSrc.match(/^  describe\(\s*['"`]\d+\.\s/gm) ?? [];
      // The meta-pin must always see at least one pin describe; the
      // count itself stays in sync because every describe('N. …')
      // increment adds one match.
      expect(pinDescribes.length).toBeGreaterThan(0);
      // Anchor: the file ships with 18 pin describes (1–18); if you
      // add a new describe('19. …'), update this anchor.
      expect(pinDescribes.length).toBe(18);
    });
  });
});
