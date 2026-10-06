# Handoff — snapshot-uiux Gate D HALT: FE template compile defect (NG8002)

- **Commission:** snapshot-uiux P6 verification (Tester, 4-GREEN merge gate)
- **Branch/tip:** `feature/snapshot-uiux` @ `2ee644b7` (c8 = e2e quintet)
- **Date:** 2026-10-06 ~01:55 UTC
- **Verdict:** Playwright e2e gate **FAIL + HALT** per sequencing.md §7 (1 retry consumed; failure deterministic — a third run without a fix cannot change the outcome). **Zero browser tests executed** — both runs aborted at webServer startup.
- **Blocked-on owner:** dev-FE lane (production FE template fix). NOT a spec/config bug; NOT the pre-flagged step-11a Escape risk (never reached).

## Defect

`ng serve` compile error (run 2, after env fix):

```
✘ [ERROR] NG8002: Can't bind to 'backdropClass' since it isn't a known property of 'mat-drawer-container'
    src/app/pages/snapshots/snapshots.component.html:306
Error: Timed out waiting 180000ms from config.webServer
```

- Landed via dev-FE commit `abeb5229` ("bind drawer-backdrop data-test on the mat-drawer-container"); present at reviewed HEAD (`git show HEAD:frontend/src/app/pages/snapshots/snapshots.component.html` lines 305–308: `[backdropClass]="'snapshot-drawer-backdrop'"` on `<mat-drawer-container data-test="drawer-backdrop">`).
- `MatSidenavModule` IS imported (`snapshots.component.ts:111`) — the compiler still rejects the binding (the input is not available on the container in this Material version).
- Logs: `/tmp/snap_gate_d_pw.log` (run 1), `/tmp/snap_gate_d_pw_run2.log` (run 2).

## Suggested fix direction (dev-FE to adjudate)

- Either drop the `[backdropClass]` binding and target the backdrop via CSS descendant (`.mat-drawer-backdrop` under the container — the e2e assertions already assert the descendant directly, per the dev-FE lane note), or bind `backdropClass` on the element/API that actually exposes it in this Material version.
- Constraint: e2e step 11b clicks container-center and 11a/11b assert drawer closure — the data-test="drawer-backdrop" attribute on the container must survive the fix (spec contract, fe-plan §5.6).

## Why Gates A+B did not catch this

- Gate A (`tsc --noEmit -p tsconfig.app.json`) does not run Angular template binding checks (NG8002 comes from the Angular compiler, not tsc).
- Gate B jest specs compile via TestBed (schema-tolerant) — the broken binding passes under TestBed while `ng serve` fails. Only the full compiler run (this gate) catches it.

## Also fixed / pending on tester lane

1. Run-1 env failure class (`initdb`/`pg_ctl` not on non-interactive PATH; PG16 server bins live in `/usr/lib/postgresql/16/bin`) — being hardened into `frontend/scripts/boot-e2e-snapshots-daemon.sh` (Tester-owned test-lane file) so future runs cannot trip on operator PATH shape.
2. Step-11a Escape-close behavior remains runtime-unaudited (tests never ran). Static analysis at authoring found no explicit Escape binding in dev-FE sources — if Material doesn't handle it natively, 11a will fail after the compile fix lands. Dev-FE should verify the drawer closes on Escape before the re-gate.

## Re-gate protocol (after dev-FE fix lands)

1. Fresh Gate D run on the new tip; Phase 1 must export `/usr/lib/postgresql/16/bin` on PATH (also hardened into the boot script by tester commit — see above).
2. Prudent: re-run Gate B (jest) — the FE fix touches snapshots templates/specs; Gate A re-run is cheap.
3. Gate C (pytest) is unaffected by FE template changes; evidence at `2ee644b7` stands (completion pending).

## Gate status @ 2ee644b7 (for the record)

- **A (tsc): PASS** (exit 0)
- **B (jest): PASS-with-baseline** — 107 suites / 3731 tests / 3727P; failure set EXACTLY the documented 4 (jobs-filter-state ×1, jobs-grouping ×3); snapshot suites 100% green
- **C (pytest): in progress** — C0 snapshot suites 110/110 GREEN; unit/routers 505 tests with failure set EXACTLY the documented 4-red baseline; job_queue 2024 tests / 15 reds pending base-attribution (all outside snapshot surface); remaining unit shards + integration + misc dirs in flight
- **D (playwright): FAIL+HALT (this note)**; deferred (e) 3/3 headers PASS, (f) `{items,total}` shape PASS — BE endpoints proven live-green on this branch

— Tester (P6 commission), via Gate D executor instance 5b8b9cb2
