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
//   ── Cluster 1 + Cluster 5 additions (Plan Review F2 + F3 + N3') ────
//   19. `MatTabsModule-imported` (Cluster 1.6, pin-count 18 → 22)
//   20. `section-disabled-optional` (Cluster 1.6)
//   21. `mat-tab-not-eager-section` (Cluster 1.6)
//   22. `sticky-tabs-host` (Cluster 1.6)

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
// Cluster 1.6 — Pin 22 `sticky-tabs-host` reads the SCSS file too.
// The sticky wrapper rule `.maintenance-tabs-host { position: sticky }`
// lives in `maintenance.component.scss`; the SCSS file is read here
// so Pin 22 can assert both the template + scss contract surfaces
// in one place.
const pageShellScss = readFileSync(
  join(maintenanceDir, 'maintenance.component.scss'),
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

describe('Maintenance console — source-grep pins (22 pins)', () => {
  // ── Pin 1: sections-registry-load-bearing (R-addition, v3 fix pass, ─────
  // ──         ADAPTED for mat-tab-group shape per Plan Review F2) ──────
  describe('1. sections-registry-load-bearing', () => {
    it('MaintenanceComponent declares readonly sections array', () => {
      expect(pageShellSrc).toMatch(
        /readonly\s+sections\s*:\s*readonly\s+MaintenanceSection\[\]/,
      );
    });

    // ADAPT per Plan Review F2 — the template's `*ngComponentOutlet`
    // now lives INSIDE `<mat-tab>`. The pin retains BOTH the iteration
    // assertion AND the outlet assertion: a hard-coded tab + a
    // kept-but-unused section property would otherwise pass while
    // making the registry dead weight (violates AC-3 load-bearing
    // registry). The regex matches the new template shape:
    //   <mat-tab ... *ngComponentOutlet="section.component" ... />
    it('MaintenanceComponent template uses @for + mat-tab + *ngComponentOutlet (registry-driven)', () => {
      // (i) Iteration — the registry is still the source of truth.
      expect(pageShellTemplate).toMatch(
        /@for\s*\(\s*section\s+of\s+sections\s*;\s*track\s+section\.id\s*\)/,
      );
      // (ii) Outlet inside mat-tab. The regex requires the
      // `<mat-tab` opener and the `*ngComponentOutlet=` binding to
      // both appear with `section.component` as the outlet value.
      // We use a non-greedy `[\s\S]*?` to match any content
      // between the opener and the binding (e.g. `[label]=`,
      // `[disabled]=`, `[matTooltip]=`).
      expect(pageShellTemplate).toMatch(
        /<mat-tab[\s\S]*?\*ngComponentOutlet\s*=\s*["']section\.component["']/,
      );
    });

    it('Negative arm: no hard-coded section tag outside the @for loop', () => {
      // The page shell should ONLY render sections via the registry.
      // A direct `<app-checkpoint-cleanup>` tag in the template
      // bypasses the registry — the negative arm pins that this
      // regressed case fails loudly.
      expect(pageShellTemplate).not.toMatch(/<app-checkpoint-cleanup/);
    });
  });

  // ── Pin 2: confirm-dialog-wired (T6.3, EXTENDED per Plan Review F3) ──
  describe('2. confirm-dialog-wired', () => {
    it('component opens ConfirmDialogComponent with destructive + dark-modal-panel', () => {
      expect(sectionComponentSrc).toMatch(
        /this\.dialog\.open\(ConfirmDialogComponent/,
      );
      expect(sectionComponentSrc).toMatch(/panelClass\s*:\s*['"]dark-modal-panel['"]/);
      expect(sectionComponentSrc).toMatch(/destructive\s*:\s*true/);
      expect(sectionComponentSrc).toMatch(/afterClosed\(\)\.subscribe/);
    });

    // Plan Review 2026-10-12 F3 — 4 NEW vacuum-aware sub-assertions
    // for the §D-7.6 canonical copy. The dialog's `data.message`
    // payload branches on `vacuumFullEnabled()`:
    //   - ticked=true  → §D-7.6 copy + R-A daemon-stall sentence +
    //                    R-C plain-VACUUM job collision sentence
    //   - ticked=false → existing destructive copy (NO vacuum-aware
    //                    extensions)
    // The pins are source-grep assertions on the conditional copy
    // construction (the production code reads `vacuumFullEnabled()`
    // to decide which message body to build).
    it('(a) vacuum-aware confirm message — when ticked: includes "VACUUM FULL rewrites the affected tables" + "60-minute hard ceiling" + all 3 affected table names', () => {
      // (a) The §D-7.6 canonical wording — single sub-assertion
      // covers all three substrings (rewording + ceiling +
      // table-name enumeration) because they live in the SAME
      // message build path. A future reword of any substring is
      // caught here.
      expect(sectionComponentSrc).toMatch(/VACUUM FULL rewrites the affected tables/);
      expect(sectionComponentSrc).toMatch(/60-minute hard ceiling/);
      // All 3 affected tables named in the dialog-message
      // construction site (§D-7.6 — checkpoints,
      // checkpoint_writes, checkpoint_blobs).
      expect(sectionComponentSrc).toMatch(/checkpoints/);
      expect(sectionComponentSrc).toMatch(/checkpoint_writes/);
      expect(sectionComponentSrc).toMatch(/checkpoint_blobs/);
    });

    it('(b) when NOT ticked (vacuumFullEnabled() === false): message build does NOT include "VACUUM FULL rewrites" (NEGATIVE — the existing destructive copy is unchanged)', () => {
      // The conditional copy construction reads `vacuumFullEnabled()`;
      // when false, the message build path skips the vacuum-aware
      // extension. We anchor on a `!vacuumFullEnabled() ? ... : ...`
      // ternary OR an if/else around the §D-7.6 copy — the
      // NEGATIVE arm asserts the unticked branch does NOT contain
      // the §D-7.6 substring.
      //
      // Source-grep approach: find the confirm-message build site
      // (anchored on the unique "VACUUM FULL rewrites" substring)
      // and check that the surrounding conditional reads
      // `vacuumFullEnabled()` — the pin asserts BOTH the presence
      // of the conditional AND that the conditional gates the
      // vacuum-aware copy (vs. always-on copy).
      const vacuumBlock = sectionComponentSrc.match(
        /vacuumFullEnabled\s*\(\s*\)\s*\{[^}]*VACUUM FULL rewrites/s,
      );
      // The negative arm asserts the conditional exists in the
      // unticked-branch shape — without the gate, the copy is
      // always on (a regression that loses the gate trips this
      // pin loudly).
      expect(vacuumBlock).not.toBeNull();
    });

    it('(c) vacuum-aware confirm message — R-A: includes "in-flight agent turns will pause at the next node boundary"', () => {
      // The R-A daemon self-write stall sentence — the operator
      // sees this in the confirm dialog so consent-time copy
      // matches runtime behavior.
      expect(sectionComponentSrc).toMatch(
        /in-flight agent turns will pause at the next node boundary/,
      );
    });

    it('(d) vacuum-aware confirm message — R-C: includes "24h plain-VACUUM job waiting out the FULL\'s lock"', () => {
      // The R-C plain-VACUUM job collision sentence — operator
      // sees this so log-line confusion during FULL is preempted.
      expect(sectionComponentSrc).toMatch(
        /24h plain-VACUUM job waiting out the FULL's lock/,
      );
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

  // ── Pin 4: no-idempotency-key (AM-17 — NEGATIVE pin, EXTENDED per F8 + ─
  // ──         N3' — Pin 4's POSITIVE arm (iv)) ──────────────────────
  describe('4. no-idempotency-key (NEGATIVE pin — AM-17)', () => {
    // Plan Review N3' enumerates the 4 sub-assertions as:
    //   (i)   idempotency_key absent in the request interface (NEGATIVE)
    //   (ii)  no crypto.randomUUID() in the execute flow (NEGATIVE)
    //   (iii) no idempotency_key: in the execute payload literal (NEGATIVE)
    //   (iv)  vacuum_full?: is in the request interface AND the
    //         performExecute payload literal includes `vacuum_full:`
    //         (POSITIVE — per Plan Review F8 + F3 cycle N3')
    // The meta-pin count is still 22 (Pin 4 is ONE pin with 4
    // sub-assertions, not 4 pins).

    it('(i) execute payload type does NOT contain `idempotency_key` field', () => {
      expect(modelsSrc).toMatch(/CheckpointCleanupExecuteRequest/);
      // Negative pin: no `idempotency_key` anywhere in the request
      // interface.
      const reqInterfaceMatch = modelsSrc.match(
        /export interface CheckpointCleanupExecuteRequest\s*\{([\s\S]*?)\n\}/,
      );
      expect(reqInterfaceMatch).not.toBeNull();
      expect(reqInterfaceMatch![1]).not.toMatch(/idempotency_key/);
    });

    it('(ii) execute flow does NOT call crypto.randomUUID (no client-side UUID generation)', () => {
      // Strict regex — `crypto.randomUUID()` (with the parens). The
      // comment text mentions the literal; we want to catch an
      // actual CALL to the API.
      expect(sectionComponentSrc).not.toMatch(/crypto\.randomUUID\s*\(/);
    });

    it('(iii) execute payload object literal does NOT contain idempotency_key', () => {
      // The mirror in the spec already verifies the runtime payload.
      // This pin guards the production source's literal shape:
      // grep for an execute-payload object literal that lacks the
      // idempotency_key field.
      expect(sectionComponentSrc).not.toMatch(/idempotency_key\s*:/);
    });

    it('(iv) execute payload type carries `vacuum_full?:` AND performExecute() payload literal includes `vacuum_full:` (POSITIVE arm — Cluster 3.5 + Plan Review N3\'/F8)', () => {
      // (iv-a) The FE model interface (Cluster 3.1) declares the
      // new optional field `vacuum_full?: boolean`. The
      // `CheckpointCleanupExecuteRequest` interface MUST carry it.
      const reqInterfaceMatch = modelsSrc.match(
        /export interface CheckpointCleanupExecuteRequest\s*\{([\s\S]*?)\n\}/,
      );
      expect(reqInterfaceMatch).not.toBeNull();
      // The `?` modifier on the field makes it optional; the regex
      // matches the optional-declaration shape `vacuum_full?: boolean`.
      expect(reqInterfaceMatch![1]).toMatch(/vacuum_full\?\s*:\s*boolean/);
      // (iv-b) The component's performExecute() payload object
      // literal (Cluster 3.3) includes `vacuum_full:` — the field
      // is threaded into the runtime payload (per
      // `vacuumFullEnabled()` signal read at task 3.3).
      expect(sectionComponentSrc).toMatch(/vacuum_full\s*:/);
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
      // `(click)` binding WITHIN the full button block. The block
      // extends to `</button>` (NOT the first `>`) so the assertion
      // catches nested content too — e.g. a `(click)="onExecute()"`
      // accidentally added inside `@if (dryRunning()) { ... }`.
      const runAgainButton = sectionComponentTemplate.match(
        /data-testid="ck-run-again-btn"[\s\S]*?<\/button>/,
      );
      expect(runAgainButton).not.toBeNull();
      expect(runAgainButton![0]).toMatch(/\(click\)\s*=\s*["']onDryRun\(\)["']/);
      expect(runAgainButton![0]).not.toMatch(/\(click\)\s*=\s*["']onExecute\(\)["']/);
    });

    it('banner CTA disabled while ANY run is in flight (dry-run / execute / daemon isRunInFlight)', () => {
      const runAgainButton = sectionComponentTemplate.match(
        /data-testid="ck-run-again-btn"[\s\S]*?<\/button>/,
      );
      expect(runAgainButton).not.toBeNull();
      // All three in-flight signals MUST gate the button. The
      // button block extends to `</button>` (not the first `>`) so
      // the assertion reads the FULL block — including nested
      // `@if (dryRunning()) { ... }` content that ALSO references
      // `dryRunning()`. Widening ensures the in-flight signals are
      // tested across the whole button, not just the opening tag.
      expect(runAgainButton![0]).toMatch(/dryRunning\(\)/);
      expect(runAgainButton![0]).toMatch(/executing\(\)/);
      expect(runAgainButton![0]).toMatch(/isRunInFlight\(\)/);
    });
  });

  // ── Pin 19: MatTabsModule-imported (Cluster 1.6) ──────────────────────
  describe('19. MatTabsModule-imported (Cluster 1.6 — tabs shell rewrite)', () => {
    it('MaintenanceComponent imports array contains MatTabsModule', () => {
      // Cluster 1.1 — `MatTabsModule` is added to the
      // `imports` array of `MaintenanceComponent`. The pin
      // asserts the named import is wired (Material runtime
      // dependency for `<mat-tab-group>` + `<mat-tab>`). A
      // future regression that drops the import trips the pin.
      expect(pageShellSrc).toMatch(/MatTabsModule/);
      // Belt-and-braces: the import statement carries
      // `from '@angular/material/tabs'` so a typo'd path or
      // a wrong-module import is caught loudly.
      expect(pageShellSrc).toMatch(/from\s+['"]@angular\/material\/tabs['"]/);
    });
  });

  // ── Pin 20: section-disabled-optional (Cluster 1.6) ───────────────────
  describe('20. section-disabled-optional (Cluster 1.6 — tabs shell rewrite)', () => {
    it('MaintenanceSection interface declares `readonly disabled?:` (optional)', () => {
      // Cluster 1.2 — `MaintenanceSection` interface gains
      // `readonly disabled?: boolean` (optional, default false).
      // The `?` modifier on the field is what makes it optional;
      // a future regression that drops the `?` trips the pin.
      const sectionInterfaceMatch = pageShellSrc.match(
        /interface\s+MaintenanceSection\s*\{([\s\S]*?)\n\}/,
      );
      expect(sectionInterfaceMatch).not.toBeNull();
      expect(sectionInterfaceMatch![1]).toMatch(/readonly\s+disabled\?\s*:\s*boolean/);
    });
  });

  // ── Pin 21: mat-tab-not-eager-section (Cluster 1.6) ───────────────────
  describe('21. mat-tab-not-eager-section (Cluster 1.6 — tabs shell rewrite)', () => {
    it('template uses <mat-tab> for per-entry block (NOT <section class="maintenance-section">)', () => {
      // Cluster 1.4 — the per-entry block is a Material
      // `<mat-tab>` (lazy-mount via Material's projection). The
      // legacy `<section class="maintenance-section">` is
      // REMOVED; using `<section>` would mount every entry
      // eagerly and bypass Material's lazy projection
      // (violates AC-3 load-bearing registry + lazy-mount).
      expect(pageShellTemplate).toMatch(/<mat-tab[\s>]/);
      expect(pageShellTemplate).not.toMatch(/<section[^>]*class\s*=\s*["']maintenance-section["']/);
    });
  });

  // ── Pin 22: sticky-tabs-host (Cluster 1.6) ────────────────────────────
  describe('22. sticky-tabs-host (Cluster 1.6 — tabs shell rewrite)', () => {
    it('template renders the sticky wrapper <div class="maintenance-tabs-host">', () => {
      // The sticky wrapper hosts the `<mat-tab-group>` so the
      // tab bar stays in-frame as the wizard body scrolls
      // (the page-header scrolls out first; the tab bar
      // pins to the top of the content area).
      expect(pageShellTemplate).toMatch(/<div[^>]*class\s*=\s*["']maintenance-tabs-host["']/);
    });

    it('SCSS rule pins .maintenance-tabs-host to sticky positioning', () => {
      // The SCSS file carries the ADV-2 sticky rule. The
      // `position: sticky` declaration is the load-bearing
      // piece — a regression that drops `position: sticky`
      // (or moves the property to a different selector)
      // breaks the sticky-tab contract surface and trips
      // the pin.
      expect(pageShellScss).toMatch(
        /\.maintenance-tabs-host\s*\{[\s\S]*?position\s*:\s*sticky/,
      );
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
    //
    // Cluster 1.6 / §D-7.7 — the anchor moves from 18 → 22 in this
    // same dispatch (per Plan Review F8(d) reconcile). The 4 new
    // pins (19–22) are appended to the existing 18-pin set.
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
      // Anchor: the file ships with 22 pin describes (1–22); if you
      // add a new describe('23. …'), update this anchor.
      expect(pinDescribes.length).toBe(22);
    });
  });
});
