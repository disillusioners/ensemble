# Phase 1: G6 — PG unit ordering + boot retry hook + lifespan wiring

Date: 2026-10-19
Author: planner[v2] via plan-creation worker (Phase 1 of 4)
Branch: `feature/hard-reboot-recovery` @ `8f47661fe901d47188bf4137e37bbfcb8c8a21d4`
Evidence base: `technical-analysis.md` §6 (G6.B recommendation) + §4 (G6 ranked CRITICAL) + §1 Executive Verdict (DB recovery PARTIAL — survives PG WAL replay; gap is unit ordering)
Prior art: `.agents/shared/planning/auto-continue-running-after-restart/phase1-plan.md` (43 lines, structure mirror)

---

## 1. Objective

A hard reboot on a host where PostgreSQL is also systemd-managed does NOT cause the daemon to crash on its first engine-open. The systemd unit orders the daemon after PG (with `Wants=` not `Requires=`, preserving the operator's debug affordance), and a small bounded retry helper in the lifespan cluster absorbs the brief PG-not-yet-ready window during PG's own WAL replay.

Testable: in a host-shaped environment where PG is started in parallel with the daemon, the boot succeeds within ≤30s of unit start; on dev (PG typically already up), the env-knob OFF keeps day-1 behaviour (no extra waits).

---

## 2. Files Touched

| File | Nature of change | LOC |
|---|---|---:|
| `scripts/systemd/ensemble-daemon.service:84-87` | +3 lines (G6.A: `After=postgresql.service` + `Wants=postgresql.service`, comments) | 5 |
| `daemon/api.py:569` neighborhood | +≤30 LOC helper `_init_manager_with_pg_retry` (G6.B retry) + ≤20 LOC lifespan envelope wiring + INFO log | ≤50 |
| `tests/unit/test_pg_init_retry.py` | NEW — unit tests for the helper (env-knob ON, OFF, env-knob absent, retry exhaustion, no-retry-when-PG-immediately-ready) | ≤50 |
| `tests/conftest_worktree.py` (or extension) | NEW — pytest fixture that fails LOUDLY if `import daemon` resolves outside the worktree (mirrors prior-art Δ7 / D20) | ≤10 |
| `.agents/shared/planning/hard-reboot-recovery/worktree-claim.txt` | NEW — WT path + branch receipt (Phase 0 task) | ≤10 |

No changes to `daemon/services/auto_continue_boot_pass.py` (read as the envelope pattern only), `daemon/manager.py` (the retry lives in `daemon/api.py:569` neighborhood, not inside `manager.initialize`), or any test_pack that already covers the unit template.

---

## 3. Tasks

| # | Task | Outcome (testable) | Touchpoints | Depends on | Risks | AC |
|---|------|--------------------|-------------|------------|-------|----|
| **0.1** | Confirm worktree + create dev venv | `python -c "import daemon; print(daemon.__file__)"` resolves INSIDE `/home/nea/ensemble-src-wt-hard-reboot-recovery`; `.venv` exists; `dev.sh` boots the daemon with `ENSEMBLE_SELF_ENV=dev` | `.venv/bin/python`, `cat daemon/__init__.py` | none (precondition) | R12 (worktree-gate tripwire) | AC7 |
| **0.2** | Write `.agents/shared/planning/hard-reboot-recovery/worktree-claim.txt` | File contains WT path + branch SHA + `import daemon` resolution path + env-fence confirmation | new file | 0.1 | — | AC7 |
| **0.3** | Add worktree-gate fixture (`tests/conftest_worktree.py` ext.) | Pytest FAILS LOUDLY if `import daemon` resolves outside the WT | new fixture | 0.1 | — | AC7 |
| **1.1** | Edit `scripts/systemd/ensemble-daemon.service:84-87` to add `After=postgresql.service` + `Wants=postgresql.service` + a 2-line comment block explaining the choice (`Wants=` not `Requires=` for debug affordance) | grep on the unit file shows the lines; `adopt-unit.sh` re-splices (test by editing the local template + running `scripts/upgrade/adopt-unit.sh` against a fake install dir) | unit file | 0.3 | R-01 (custom install paths — covered by runbook drop-in in Phase 3) | AC1, AC2 |
| **1.2** | Implement `_init_manager_with_pg_retry` helper at `daemon/api.py:569` neighborhood (outermost layer — wraps the `manager.initialize()` call). Signature: `async def _init_manager_with_pg_retry(manager: EnsembleManager) -> None`. Behavior: read `ENSEMBLE_INIT_RETRY` env (`0` disables, `1` or unset = first boot, opt-in via explicit `=1`); if enabled, await `manager.initialize()` with a try/except wrapping `OperationalError`-family; on failure, sleep 10s, retry up to 3 attempts total; on exhaustion, raise loud with a one-line error log including the retry count and elapsed time | helper file lines ≤30; reads `os.environ.get("ENSEMBLE_INIT_RETRY", "")`; try-loop bounded at 3 attempts | `daemon/api.py` | 1.1 | R-02 (CAPTURE_PHASE collision — helper runs BEFORE manager.initialize returns; CAPTURE is the FIRST thing inside initialize; review checklist pins this) | AC1, AC2, AC3 |
| **1.3** | Wire the helper into `daemon/api.py` lifespan envelope at the verified placement (cluster around `:569`) — replace the direct `await manager.initialize()` call with `_init_manager_with_pg_retry(manager)`. Inside the outermost try, add an INFO log on success (with attempt count), WARNING log on per-retry failure, ERROR log on exhaustion | grep on `daemon/api.py` shows exactly the swap (multi-edit read-back per prior-art discipline) | `daemon/api.py` | 1.2 | — | AC1, AC2, AC3 |
| **1.4** | Write `tests/unit/test_pg_init_retry.py` covering: env-knob ON + retry-exhaustion path (mock `manager.initialize` to raise `OperationalError` 3× → helper re-raises after 30s of retries); env-knob ON + immediate-success path (mock to raise once then succeed → helper returns); env-knob OFF + exception path (mock to raise 1× → exception propagates immediately, no retry); env-knob absent + immediate-success path (default behaviour preserved); no-retry-when-PG-ready assertion (mock returns success on first call → no sleep, no retry); ON at boot but PG IS up → still 1 attempt only (deterministic) | test file ≤50 LOC; each case green; `pytest tests/unit -q` green; `pytest tests/unit/test_pg_init_retry.py -q` green | `tests/unit/test_pg_init_retry.py` | 1.3 | — | AC1, AC2, AC3 |
| **1.5** | Run the FULL unit directory as a regression gate | `pytest tests/unit -q` green (no regressions) | none | 1.4 | — | AC1 |

---

## 4. Acceptance Criteria

| ID | Criterion |
|----|-----------|
| **AC1** | The systemd unit file at `scripts/systemd/ensemble-daemon.service` declares `After=postgresql.service` AND `Wants=postgresql.service` (NOT `Requires=` — preserves the debug affordance per D2); verified by grep + a manual inspect against the template |
| **AC2** | The lifespan wiring at `daemon/api.py:569` neighborhood wraps `manager.initialize()` in `_init_manager_with_pg_retry`; verified by grep showing the new helper name in place of the direct call |
| **AC3** | With `ENSEMBLE_INIT_RETRY=1`, the helper retries up to 3 attempts on `OperationalError`-family failures (10s backoff between attempts, exhaustion = re-raise with one-line ERROR log); verified by `tests/unit/test_pg_init_retry.py` |
| **AC4** | With `ENSEMBLE_INIT_RETRY=0` or absent, the helper behaves identically to a direct `manager.initialize()` call (no extra latency, no swallow) — preserves day-1 dev behaviour; verified by `tests/unit/test_pg_init_retry.py` cases for env-knob OFF / absent |
| **AC5** | `tests/conftest_worktree.py` fixture prevents silent tests-on-wrong-tree (mirrors prior-art Δ7 / D20) — `pytest tests/unit -q` REFUSES to run if `import daemon` resolves outside `/home/nea/ensemble-src-wt-hard-reboot-recovery` |
| **AC6** | Manually exercised (NOT automated; smoke): boot the daemon against a freshly-rebooted host where PG is started by systemd in parallel — boot completes within ≤30s of unit start |

---

## 5. Coupling

- **Tight with** the lifespan envelope (Phase 2 G2.A will ride the same neighborhood wiring; placement decisions affect both passes).
- **Tight with** the worktree gate (Phase 0; without it, every AC can silently test the wrong branch).
- **Independent of** the AutoContinue pass (read the envelope pattern only, never modify).
- **Independent of** Phase 2 G2 (the MQ recovery service has its own lifespan envelope slot, but they compose cleanly — Phase 1's lifespan edit does NOT preclude Phase 2's).
- **Never touches** `daemon/services/auto_continue_boot_pass.py:230-494` (read-only anchor), `daemon/manager.py` body (the retry lives at the API tier; manager.initialize stays untouched).

---

## 6. Verification Steps

1. **Worktree gate (Phase 0):**
   ```bash
   cd /home/nea/ensemble-src-wt-hard-reboot-recovery
   .venv/bin/python -c "import daemon; print(daemon.__file__)"
   # MUST resolve to .../ensemble-src-wt-hard-reboot-recovery/daemon/__init__.py
   cat .agents/shared/planning/hard-reboot-recovery/worktree-claim.txt   # should exist
   ```

2. **Unit-test gate (AC3, AC4, AC5):**
   ```bash
   cd /home/nea/ensemble-src-wt-hard-reboot-recovery
   .venv/bin/pytest tests/unit/test_pg_init_retry.py -q
   .venv/bin/pytest tests/unit -q                                     # regression gate
   ```
   Both green.

3. **Multi-edit read-back (repo trap #2):** after editing `daemon/api.py` AND `scripts/systemd/ensemble-daemon.service`, re-read both diffs (`git diff`) and confirm exactly the intended hunks landed:
   - Unit file: ONE place gained `After=postgresql.service` + `Wants=postgresql.service` + 2 comment lines.
   - `daemon/api.py`: ONE direct `await manager.initialize()` was replaced by `await _init_manager_with_pg_retry(manager)`, plus the helper definition appeared nearby.
   - **NO** other lines in either file changed.

4. **Systemd unit template verification (AC1):**
   ```bash
   grep -n "^After=\|^Wants=\|^Requires=" scripts/systemd/ensemble-daemon.service
   ```
   Expect: `After=network-online.target`, `Wants=network-online.target`, `After=postgresql.service`, `Wants=postgresql.service`. NO `Requires=postgresql.service`.

5. **Adopt-unit re-splice (AC1 reinforcement):** the `__ENV_LINES__` token splice at `scripts/upgrade/adopt-unit.sh:84-108` does NOT touch the `After=` line (the line is static), so editing the template propagates without re-running adopt. Manual verify by:
   ```bash
   grep -n "after-unit\|adopt-unit" docs/runbooks/systemd-adoption.md   # confirm the runbook says "edits to After= lines do not require re-splice"
   ```

6. **Manual boot smoke (AC6, NOT automated):** bring up the daemon against a freshly-rebooted host where PG is started by systemd in parallel. Watch the journal:
   ```bash
   journalctl -u ensemble-daemon.service -f   # observe retry lines (if any) and the eventual boot line
   ```
   Boot completes within ≤30s of unit start.

7. **Worktree gate tripwire (AC5):** temporarily edit the conftest fixture to point at a DIFFERENT daemon path; run `pytest tests/unit -q`; observe LOUD refusal; revert.

---

## 7. AC Mapping

| AC | Task(s) that produce it |
|----|--------------------------|
| AC1 | 1.1 |
| AC2 | 1.2, 1.3 |
| AC3 | 1.2, 1.4 |
| AC4 | 1.4 |
| AC5 | 0.3, 1.4 (conftest gate surfaces here) |
| AC6 | manual smoke (NOT automated; logged in plan-overview.md §8 phase 1 gate) |

---

## 8. Rollback Note

Revert commits delete:
- the helper function `_init_manager_with_pg_retry` from `daemon/api.py`,
- the lifespan wiring swap (direct `manager.initialize()` restored),
- the 3-line unit-template edit (no schema migration, so `git revert` is one atomic restore).

No DB migration exists. No env-knob default is `ON` (Phase 1 default is OFF). The worktree gate (Phase 0 task 0.3) stays — it is the prior-art convention; the worktree itself is the rollback target. Phase 2/3/4 have not shipped yet, so no follow-up fallout.

If the rollback lands DURING the demo E2E (Phase 4), the runner reverts to "no G6 fix", which is the same shape as the current `feature/hard-reboot-recovery` tip — i.e., a successful revert returns the WT to a state where the gap-doc-recommended G6 fix simply doesn't exist yet, no worse than today.

---

## 9. Ops-vs-Code Split

| Layer | What | Why |
|-------|------|-----|
| **Ops (this phase)** | 3-line unit template edit at `scripts/systemd/ensemble-daemon.service:84-87` | G6.A — the OPS half of G6.B; the bottleneck is unit ordering, not retry policy |
| **Code (this phase)** | `_init_manager_with_pg_retry` helper + lifespan wiring + unit tests | G6.B — the retry is the belt-and-suspenders for WAL-replay timing on slow disks |
| **Runbook (Phase 3)** | drop-in example for non-systemd PG installs (Q-1 reinforcement); per-state RTO table for G8 | operator UX |
| **Test (Phase 4)** | manual smoke on a real systemd-installed host | real-environment proof |

---

## 10. Coupling to the Prior Art

| Prior-art pattern | This phase's analogue |
|-------------------|------------------------|
| Three-layer never-wedge envelope (`auto_continue_boot_pass.py:230-494`) | Phase 1 places the retry inside the same outermost-try shape at the API lifespan tier (per-candidate/pass/lifespan) |
| Kill-switch `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART` (`auto_continue_boot_pass.py:145-156`) | Phase 1's `ENSEMBLE_INIT_RETRY` is a sister shape (env-direct, default OFF, opt-in) |
| Worktree-gate fixture (prior-art Δ7 / D20) | Phase 0 task 0.3 — verbatim port |
| `worktree-claim.txt` (prior-art Δ7 / D20) | Phase 0 task 0.2 — verbatim port |
| Multi-edit read-back (repo trap #2) | Verifications step 3 — same discipline |

All references in this phase are mirrored from prior-art `auto-continue-running-after-restart` (landed v0.21.0) conventions; the user directive *"`try to REUSE a part of current mechanism, don't craft new if not really need`"* is honored.

---

## 11. Out of Scope (Phase 1 explicitly does NOT do)

- **No schema migration** — the retry is env-knob opt-in; no DB column is added.
- **No new data structure** — the selector reuse for G2 is in Phase 2.
- **No MQ recovery pass** — Phase 2.
- **No runbook** — Phase 3.
- **No demo E2E** — Phase 4.
- **No D5 / D7 / D11 work** (LANE-1 deprecation; G1 alive-row; G7 pre-flight) — follow-up commissions.
- **No G3 fix** — parked.
