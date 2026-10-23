# Addendum — Focused re-run (G3 + G5 + granted fix) @ a1cd8e0d6 → 406697a0d

- **Date**: 2026-10-23 (05:0x–05:5x UTC) · **Scope**: G3 redo, G5 redo, granted TestCooldown fix, hygiene. G1/G2/G4/G6/G7 verdicts from the first pass STAND (no repeat).
- **Parent report**: `.agents/tester/RESULTS/2026-10-23-wedge-capture-independent-verification.md` (docs commit 2f899ec2d, then rebased onto the final HEAD by this addendum's commit).

## Harness batch verified (968a0c0b2..a1cd8e0d6 = 3 commits, 4 files — recon CLEAN)

- `3ce9ecdb5` — Lane B fixes: `PG_TEST_*→POSTGRES_*` translation table (wedge_smoke.py:553-561) wired via `env.setdefault` in `scratch_env` (:576-580, explicit POSTGRES_* wins); alive-sampled cgroup assertion (child pid → FILE, parent polls + `assert_cgroup_isolated(pid)` while alive, test :325-373); corrected skip text (lane B needs BOTH `ENSEMBLE_WEDGE_SMOKE=1` AND `ENSEMBLE_WEDGE_SMOKE_UNIT=1` — module gate + lane classes); SC-2 alignment (`LEG_LAG`: LAG_S="2.0", LAG_SUSTAIN_S="2"; `SC2_LOOP_BLOCK_S=6.0`, `SC2_LEG_HOLD_S=7.0` consumed by the test — no literals).
- `4f9f01e1f` — migration record: dialect-split note (metrics_snapshot json SQLite-face / jsonb PG-face via model `JSONBType`), DOWN section neutralized (4 DROPs commented out; only sanctioned manual apply = runner UP-extraction). **CLOSES first-pass findings #2 (.sql json/jsonb drift) and #3 (psql -f DOWN footgun).**
- `a1cd8e0d6` — T40 evidence pack `.agents/tester/RESULTS/2026-10-23-wedge-capture-evidence.md` (125 lines).

## G3 REDO — Lane B certification @ committed harness: **PASS** (10 passed / 0 failed / 0 skipped, 93.93s)

Scratch PG `wedge_gate_1792732556` (dropped with proof); both env gates; per-var scrub; zero live contact.

- **(a) WATCHDOG BOOT-ALIVE — CERTIFIED BY THE COMMITTED PIN**: `TestBootWiring::test_an_enabled_boot_leaves_a_live_watchdog_on_app_state PASSED` — drives `assert_watchdog_boot_alive(app.state)` (not-None + `status()["running"]` + `started_at_monotonic`) inside a REAL `create_app()+lifespan` boot under `systemd-run --unit=ensemble-wedge-smoke-boot-alive --wait --collect`. **This closes the api.py ship-blocker class end-to-end — the certification missing from the first pass.** Corroborated at the manual layer (`Wedge-capture watchdog started: dir=/tmp/wedge-manual2-… interval=1.0s … deadline=20.0s`).
- **(b) Boot record + effective mode**: `PostgreSQL ENV vars detected` → engine on the scratch DB; boot record `effective_mode:"on"`, `pass_present:true`, `auto_continue_mode:"on"`, `auto_continue_raw_env:"1"`; carrier `[BOOT_CONTINUE] orphan-sweep summary: … boot_epoch=2026-10-23T05:21:33.252607 mode=on`.
- **(c) Manual capture on booted daemon**: `POST /api/wedge-captures/capture → 202 {"capture_id":"20261023T052222Z-…-9b63db44","reason":"manual","enqueued":true}` → `servicing manual capture request …` → `capture written … reason=manual artifacts=8 duration=0.10s row_written=True`; 8 files on disk; `SELECT count(*) FROM wedge_captures = 1` (`manual`); no `.pending_row.json`.
- **(d) Cgroup fence**: `TestDaemonBootLane::test_the_cgroup_and_live_fence_hold PASSED` — pid-file + alive-sampling (child sleeps 4s after writing pid; parent asserts `/proc/<pid>/cgroup` is neither `ensemble-main.service` nor `ensemble-upgrade-*` while alive).
- **(e) Teardown**: SIGTERM → `Graceful shutdown complete` / `Database engine disposed`; NO_ORPHAN_PROCS; port 18098 freed (8079 never touched); no transient units registered.
- **(f) Fences**: per-var scrub (quoted in run command); scratch role+DB DROPPED, `SELECT datname/rolname` empty; secrets shredded.
- Observations (report-only): fresh-scratch-DB `tasks`-relation fail-soft warnings (known, unchanged); manual-boot checkpointer resolved to `localhost:5432/ensemble` config default with NO connection evidence in logs (ticket candidate, unverified); one disclosed operator error (`set -a` sourcing) with zero DB contact.

## G5 REDO — T40 + SC-2: **PASS** (upgraded from PARTIAL)

- **T40 contract satisfied**: 15/15 SC rows present in the evidence pack; each names its proving pack; SC-15 OPEN by design (owner giter S-5); SC-14 hybrid (product proven, execution owned by this G3 re-run — now delivered above).
- **SC-2 judged against the PLAN ROW (leader adjudication)**: plan-overview.md §6 SC-2 specifies `LAG_S=2.0 / SUSTAIN=2 / block ≥5s (6s in-leg)` — harness constants match EXACTLY (named constants, no literals). Evidence pack records the standalone aligned run: `capture_count=1, fired=[sustained_loop_lag], observed={lag_s:4.004, sustained_s:2.002}`, CPU+CONC silent, nproc=8.
- **CORRECTION (first-pass statement retracted)**: the first-pass report's "LAG_S=2.0 vs plan 5.0" was a dispatch paraphrase error — the plan row has said 2.0 since the binding plan set landed (the 5.0 conflated the ≥5s block floor with the triggers-pack integration defaults). The only real deviations at 968a0c0b2 were `LAG_SUSTAIN_S="0"` + inline 3.0s block — both CLOSED by 3ce9ecdb5.
- T39 carryover: writer pack byte-unchanged by the batch (diff empty); evidence-pack SC-4 row consistent with the pack sites.
- leg-iii 6.0s vs D43 3–5s: documented accepted margin (own OPEN row) — NOT a finding.

## GRANTED FIX — TestCooldown per-reason semantics: committed `406697a0d`

- Flake (leader adjudication): total-count assertion over-claimed; SC-6 cooldown is PER-REASON (D43: co-firing co-observed, not asserted).
- **Shape A (assertion-only)** chosen over env-pinning (Shape B would have altered leg-i semantics for TestLegI too). Test renamed `test_a_sustained_wedge_produces_one_capture_per_reason`; asserts `capture_count >= 1` (non-vacuity) + per-reason census over `smoke.manifests()` — no reason appears twice within the cooldown window (a per-tick bug still trips it). 18 insertions / 6 deletions, single file `tests/integration/test_wedge_capture_boot_smoke.py`; zero product code; AST-verified after each edit round (2 rounds).
- **Determinism: 5/5 consecutive green + 1 verification run = 6 consecutive greens** (Lane A invocation, 6P/4S each, 37–39s wall; zero FAILED lines across all runs). Commit on `feature/wedge-capture-watchdog`, targeted add, post-commit porcelain = exactly the 3 foreign rows.

## Hygiene @ final HEAD 406697a0d (26 commits over base)

- Fix commit touches exactly 1 file (tests/); frozen surfaces untouched by construction and re-verified at commit time: `daemon/constants.py`, `tests/test_chart_image_delivery_e2e.py`, `.agents/shared/planning/wedge-capture/` (annex shas `0c191ac8…`/`6c17a313…` unchanged), 9-file ceremony pack — zero diff a1cd8e0d6..406697a0d.
- Worktree porcelain = exactly the 3 pre-existing foreign rows (approver/active.md, designer-agent install-audit.jsonl, tidier/notes.md) — untouched throughout.

## Findings register — final state

- **CLOSED by the harness batch**: 🔴 Lane B PG plumbing / cgroup race / skip text (3ce9ecdb5); 🟠 .sql json↔jsonb record drift + DOWN footgun (4f9f01e1f); 🟠 T40 deliverable absent + SC-2 envelope (3ce9ecdb5 + a1cd8e0d6); granted TestCooldown flake (406697a0d).
- **Remaining (green-class, non-blocking, owner: follow-up polish lane)**: latent `insert()` `.default.arg` defect (no production caller), `row_written` token on dedup no-op, duplicate `capture_dir` unique index, `injection_window` not persisted/asserted, CLI `--wait` flag-explicit pin, D37 `status()`-never-waits behavioral pin, manual-boot checkpointer default-DSN observation (ticket candidate).

## FINAL VERDICT

**GO for staging (test-lane).** All seven gates green at `406697a0d`: G1 189/189 · G2 Lane A PASS · G3 Lane B CERTIFIED PASS (boot-alive by the committed pin) · G4 live-PG 4/4 · G5 PASS (T40 satisfied, SC-2 aligned to plan row) · G6 net-new ∅ ×6 · G7 4/4 residuals hold. Stage-time SC-15 gates (S-5) remain giter's; the promote ceremony remains the user's nonce gate.

*Evidence: this addendum + parent report; docs-only commit on `tester/wedge-capture-verification` (rebased onto 406697a0d).*
