# RESULTS ADDENDUM — merge-blocking fixes re-verified @ 7be1b40bf

**Date:** 2026-10-19 · **Parent report:** `2026-10-19-rcc-final-gate.md` (@ a385c102f→dae97a500, evidence commit c9b2156bc)
**Fix chain re-verified:** `bb9673063` (sanitizer / FINDING #1) → `b3aa7dcad` (9 pin/manifest reds / FINDING #3) → `7be1b40bf` (savepoint / FINDING #2). Linear history confirmed.
**Re-verification workers:** pg-smoke 8802d918 (revive, driver `/tmp/rcc_gate_driver.py`) · suites addcc5d1 · proofs 4e5e2789 (read-only + 2 sanctioned green runs).

---

## Verdicts

### FINDING #1 (sanitizer gap) — 🔁 FIXED, VERIFIED on real PG
- Throwaway `ensemble_gate_rcc2`/`rcc_gate2` (sudo -u postgres; both dropped+verified). Production chain unchanged (sanitize → register/mark_exit → run_once() → _write_pid_map; 5 rows round-trip).
- **All 9 canary needles 0 hits** (LIKE-probe over command+detail): `sk-REDACTEDCANARY123` (the original FINDING #1 needle), `ghp_CANARYGHP123`, `xoxb-CANARYSLACK`, `AKIA_CANARYAWS`, `canary-abc123`, ENS-marker uuid, `__KMS_ENV__` body, `sk-STANDALONECANARY789`, `MIDTOKENCANARY77`.
- Stored forms forensically correct: flag/env NAMES kept, RHS `<redacted>` (pid 91011 verbatim: `env SECRET_TOKEN=<redacted> curl --api-key=<redacted> --token=<redacted> -H Authorization=<redacted> AWS_KEY=<redacted>`); markers whole-token redacted; string-mode argv0-only intact.
- Mutation-proof sanity (read-only): the 6 `TestW2DashFlagRedaction` canaries are load-bearing — each breaks the OLD regex set at a specific line (leading `-` outside `^[A-Za-z_]` etc.); the claimed 5F/1P-on-revert is structurally exact; the 1P = `test_benign_lookalike_tokens_survive`, the correct negative control (guards the over-redaction direction). Green run: 12 passed (`-k "canary or redact or W2"`).
- **Residual (documented, disposition to leader):** blanket dash-assign RHS redaction — benign `--verbose=true` / `--mode=production` / `--output=/tmp/x.json` also store `<redacted>` (safe direction, zero leak; forensic fidelity trade). This matches the fix's STATED contract ("redacts the RHS of any dash-prefixed assignment"); my gate's benign-passthrough control was stricter than the design. Recommendation (gate + worker concur): **accept-as-designed** with doc/pin note — heuristic refinement (secret-shaped-RHS-only) risks re-opening the leak class. Secondary documented residual: space-separated `--flag <value>` shapes unpinned (no assignment form, no pinned prefix).

### FINDING #3 (9 pin/manifest/fixture reds) — 🔁 CLEARED, no new reds
- Five suites re-run @ 7be1b40bf: **82/82 PASS** — arm_notify 8 (was 1F/7P), router_scope_manifest 7 (2F/5P), webfetch_builtin 37 (**0 errors**; was 2E/35P), router_class_manifest 4 (3F/1P), auth_middleware_dispatch 26 (1F/25P). Cleared distribution matches the fix's red-first predictions exactly.
- Resource-family regression: pid_registry **48** (+6 new canaries), storage **45** (+3 `TestPGTxnAbortWindowSurvival`) — all green, zero flakes, zero fixture errors.
- **ATTRIBUTION CORRECTION (folds into the parent report):** the auth-dispatch red's true mechanism is the **SPA-SHELL CATCH-ALL**, not auth routing — `daemon/api.py:4372-4397` registers `app.get("/{path:path}")` → `serve_ui_assets` returns `FileResponse(index.html)` for any non-api/ws/vscode/views path when a built FE dist exists; `/livezz` qualifies (counter-experiment: removing all /api/resources routes leaves /livezz 200ing). The fixed test now discriminates by `Accept: application/json` + content-type (`text/html` = SPA shell, never a JSON API 200) — structurally able to tell the mechanisms apart. Parent §FINDING #3's "auth-dispatch default-deny shift" wording is superseded by this mechanism.

### FINDING #2 (PG txn-abort window-death, was advisory) — 🔁 FIXED (not deferred), VERIFIED
- `begin_nested()` savepoints wrap BOTH lanes — `_tier_write` (flusher.py:745-762) and `_sweep` (:579-595): rollback-on-exception → tolerated 42P01 returns 0 + warn (abort state cleared); non-tolerated errors re-raise; commit-on-success.
- Mutation-proof sanity: pre-fix derivation is exactly 2F/1P (write-path + sweep-path die at the 25P02 re-raise; the 42703-propagation test is the expected-pass and is itself load-bearing against classifier-widening). Green: 3/3.
- **Real-PG end-to-end:** full `run_once()` window with the table MISSING now SURVIVES — sane window dict (rows_written 0, sweeps 0), no raise, no 25P02 (the exact a385c102f failure). C-1 invariants re-verified on the fixed tree: plain INSERT (no ON CONFLICT, hard-asserted), 42P01 tolerated in isolation, 42703 propagates.

---

## What I could NOT verify
1. The mutation (revert) legs themselves were NOT executed by me (read-only discipline during a live branch) — the 5F/1P and 2F/1P claims are verified structurally (breaking-line analysis) + by the fix lane's own runs; the current-shape greens are re-executed here.
2. Space-separated `--flag <value>` redaction remains unpinned (documented residual of the fix, not of this re-verification).
3. The benign-RHS passthrough expectation (my stricter control) — fails by design; accept-vs-refine belongs to the leader.

## Gate disposition after re-verification
- Merge-blocker 1 (FINDING #1): **CLEAR** — fixed + real-PG-verified + proof-sane.
- Merge-blocker 2 (FINDING #3, 9 reds): **CLEAR** — 82/82, no new reds.
- Advisory (FINDING #2): **upgraded to FIXED** — savepoint mechanism verified three ways (read, unit-proof, real-PG end-to-end).
- Open (non-blocking): blanket-RHS over-redaction adjudication; space-separated flag residual; the parent report's other gaps (perf-matrix tail, R-AL xdist-crash commission, cross-router/engine-fault degrade pins) unchanged.
