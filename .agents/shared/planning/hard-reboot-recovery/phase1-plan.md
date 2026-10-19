# Phase 1: G6 — `After=postgresql.service` ONLY + `connect_timeout` in `create_postgres_engine` + preflight reuse (post-architect-fold A1)

Date: 2026-10-19
Author: planner[v2] via plan-creation worker (Phase 1 of 4)
Branch: `feature/hard-reboot-recovery` @ `8f47661fe901d47188bf4137e37bbfcb8c8a21d4`
Evidence base: `technical-analysis.md` §4 (G6 ranked CRITICAL) + §1 Executive Verdict (DB recovery PARTIAL — survives PG WAL replay; gap is unit ordering) + `architecture-recommendation.md` §2, §4, §7 (architect-folded amendment A1)
Prior art: `.agents/shared/planning/auto-continue-running-after-restart/phase1-plan.md` (43 lines, structure mirror)

---

## 1. Objective

A hard reboot on a host where PostgreSQL is also systemd-managed does NOT cause the daemon to crash-loop on its first engine-open. The systemd unit orders the daemon after PG (with `After=` only, NOT `Wants=` per architect R-A5, NOT `Requires=` per D2 — preserving the operator's debug affordance and avoiding the wasted-RAM side-effect on remote-PG hosts); the existing `_boot_db_preflight` + launcher exit-75 lane is the canonical PG-wait (budget-exempt, infinite, no new env-knob per D16); and `connect_timeout=BOOT_DB_TIMEOUT_S=10` in `create_postgres_engine` removes the firewall-DROP hang (75–130s per attempt previously) that would have made any retry budget fiction.

**Testable:** in a host-shaped environment where PG is started in parallel with the daemon, the daemon's preflight `SELECT 1` waits for PG (capped at 10s per attempt, exit 75 → budget-exempt infinite backoff) and the daemon starts cleanly the moment PG accepts connections — no crash-budget burn, no in-lifespan retry helper, no new env-knob. On dev (PG typically already up), day-1 behaviour is preserved (one `SELECT 1` attempt, fail-fast if PG is genuinely down — the existing preflight path).

---

## 2. Files Touched

| File | Nature of change | LOC |
|---|---|---:|
| `scripts/systemd/ensemble-daemon.service:84-87` | +3 lines (G6.A amended: `After=postgresql.service` ONLY; 2 comment lines stating the host-dependent intent and pointing to the runbook drop-in) | 3 (template) + 2 (comments) = 5 |
| `daemon/factory.py:232-284` | +1 LOC: add `connect_timeout=BOOT_DB_TIMEOUT_S=10` to `create_postgres_engine` (alongside the existing `pool_pre_ping`) | 1 |
| `daemon/__main__.py:143-247` | **NO CHANGE** — preflight + exit-75 lane reused as-is (architect A1) | 0 |
| `daemon/launcher.sh:1340-1346,1576-1590` | **NO CHANGE** — exit-75 budget-exempt infinite backoff reused as-is | 0 |
| `tests/unit/test_connect_timeout.py` | NEW — verify `connect_timeout=BOOT_DB_TIMEOUT_S=10` set; preflight reuse (mocked) shows it called once, not three times; firewall-DROP mock hangs ≤10s, not 75–130s; `After=` is the only PG-related unit directive (regression guard for the dropped `Wants=`) | ≤15 |
| `tests/conftest_worktree.py` (or extension) | NEW — pytest fixture that fails LOUDLY if `import daemon` resolves outside the worktree (mirrors prior-art Δ7 / D20); **+ failure-injection check (A5/R-07)**: temporarily point the fixture at a fake path, `pytest tests/unit -q` MUST refuse to run, revert | ≤10 |
| `.agents/shared/planning/hard-reboot-recovery/worktree-claim.txt` | NEW — WT path + branch receipt (Phase 0 task) | ≤10 |

No changes to `daemon/services/auto_continue_boot_pass.py` (read as the envelope pattern only), `daemon/manager.py` body, `daemon/api.py:569` lifespan, the `find_stuck_messages` selector body, or any test_pack that already covers the unit template.

**REMOVED (architect A1 fold):** `_init_manager_with_pg_retry` (was planned, dead-code at `api.py:562-568` constructor); `Wants=postgresql.service` line (RAM-cost hazard on remote-PG hosts per R-A5); `ENSEMBLE_INIT_RETRY` env-knob (eliminated per D16 — single kill-switch adopted; the preflight is the existing, better-shaped lane); `tests/unit/test_pg_init_retry.py` (its cases are now subsumed by `test_connect_timeout.py`).

---

## 3. Tasks

| # | Task | Outcome (testable) | Touchpoints | Depends on | Risks | AC |
|---|------|--------------------|-------------|------------|-------|----|
| **0.1** | Confirm worktree + create dev venv | `python -c "import daemon; print(daemon.__file__)"` resolves INSIDE `/home/nea/ensemble-src-wt-hard-reboot-recovery`; `.venv` exists; `dev.sh` boots the daemon with `ENSEMBLE_SELF_ENV=dev` | `.venv/bin/python`, `cat daemon/__init__.py` | none (precondition) | R-07 (worktree-gate tripwire) | AC5 |
| **0.2** | Write `.agents/shared/planning/hard-reboot-recovery/worktree-claim.txt` | File contains WT path + branch SHA + `import daemon` resolution path + env-fence confirmation | new file | 0.1 | — | AC5 |
| **0.3** | Add worktree-gate fixture (`tests/conftest_worktree.py` ext.) | Pytest FAILS LOUDLY (exit 1) if `import daemon` resolves outside the WT; **+ failure-injection check (A5/R-07)**: temporarily point the fixture at a fake path, `pytest tests/unit -q` MUST refuse to run, revert | new fixture | 0.1 | R-07 | AC5 |
| **1.1** | Edit `scripts/systemd/ensemble-daemon.service:84-87` to add `After=postgresql.service` ONLY (NO `Wants=`, NO `Requires=`) + a 2-line comment block explaining the choice (host-dependent intent; points to the runbook drop-in for the G6.A delivery on existing installs because `adopt-unit.sh:537` refuses template clobber) | grep on the unit file shows EXACTLY `After=postgresql.service`; manual inspect against the template; the new runbook drop-in example `/etc/systemd/system/ensemble-main.service.d/10-pg-order.conf` (D15) is the delivery mechanism for existing installs (Phase 3) | unit file | 0.3 | R-05 (non-systemd-PG installs — covered by D15 drop-in) | AC1, AC2 |
| **1.2** | Add `connect_timeout=BOOT_DB_TIMEOUT_S=10` to `create_postgres_engine` at `daemon/factory.py:232-284` (alongside the existing `pool_pre_ping`). Read `BOOT_DB_TIMEOUT_S` from `daemon/constants.py:70` (already declared; no new constant) | grep on `daemon/factory.py` shows the new arg; `BOOT_DB_TIMEOUT_S=10` is set; no other `create_postgres_engine` callsites are broken (full test dir must stay green) | `daemon/factory.py` | 1.1 | R-01 (connect_timeout regresses fast-path — verified by test: preflight shows it called once, not three times) | AC1, AC2, AC3 |
| **1.3** | Verify the existing `_boot_db_preflight` + exit-75 lane covers the canonical boot path (architect A1: preflight-reuse is the whole point). Read `daemon/__main__.py:143-247` + `daemon/run_app.py:70` (frozen binary) + `daemon/__main__.py:467` (`python -m daemon`) + `daemon/launcher.sh:1340-1346,1576-1590` (exit-75 budget-exempt infinite backoff) — confirm both entry paths reach the preflight, and the launcher maps 75 to the budget-exempt lane. **No code change** — this is a verification task, not an implementation task | grep + manual read confirms preflight is on the canonical boot path; `cat` the constants file confirms `BOOT_DB_TIMEOUT_S=10`; the existing test (if any) for preflight is still green | none — verification only | 1.2 | — | AC1, AC2, AC4 |
| **1.4** | Write `tests/unit/test_connect_timeout.py` covering: (a) `connect_timeout=BOOT_DB_TIMEOUT_S=10` IS set on the engine after `create_postgres_engine` returns; (b) firewall-DROP mock hangs ≤10s, NOT 75–130s (the original bug); (c) preflight reuse (mocked `__main__.py:143-247` `SELECT 1`) is called ONCE per boot, not 3 times; (d) `After=postgresql.service` is the ONLY PG-related unit directive in `scripts/systemd/ensemble-daemon.service` (regression guard for the dropped `Wants=`); (e) `Wants=postgresql.service` and `Requires=postgresql.service` are BOTH absent | test file ≤15 LOC; each case green; `pytest tests/unit -q` green; `pytest tests/unit/test_connect_timeout.py -q` green | `tests/unit/test_connect_timeout.py` | 1.3 | — | AC1, AC2, AC3, AC4 |
| **1.5** | Run the FULL unit directory as a regression gate | `pytest tests/unit -q` green (no regressions) | none | 1.4 | — | AC1 |

---

## 4. Acceptance Criteria

| ID | Criterion |
|----|-----------|
| **AC1** | The systemd unit file at `scripts/systemd/ensemble-daemon.service` declares `After=postgresql.service` ONLY (NO `Wants=`, NO `Requires=` per architect R-A5 and D2 — preserves the operator's debug affordance AND avoids the wasted-RAM side-effect on remote-PG hosts); verified by grep + a manual inspect against the template; the runbook drop-in example `/etc/systemd/system/ensemble-main.service.d/10-pg-order.conf` (D15) is the delivery mechanism for existing installs (Phase 3) |
| **AC2** | `daemon/factory.py:232-284` `create_postgres_engine` carries `connect_timeout=BOOT_DB_TIMEOUT_S=10` alongside the existing `pool_pre_ping`; verified by grep showing the new arg; full `tests/unit` regression gate green |
| **AC3** | Firewall-DROP mock hangs ≤10s per attempt (not 75–130s as previously); preflight `SELECT 1` is called ONCE per boot, not 3 times; `After=postgresql.service` is the ONLY PG-related unit directive (regression guard for the dropped `Wants=`); `Wants=` and `Requires=` are BOTH absent; verified by `tests/unit/test_connect_timeout.py` |
| **AC4** | Existing `_boot_db_preflight` + launcher exit-75 lane covers the canonical boot path (both entry paths: `run_app.py:70` frozen binary; `__main__.py:467` `python -m daemon`); `BOOT_DB_TIMEOUT_S=10` is read from `daemon/constants.py:70`; launcher maps 75 → budget-exempt infinite backoff; no regression in preflight behaviour; verified by Task 1.3 (verification task; no code change) + `test_connect_timeout.py` |
| **AC5** | `tests/conftest_worktree.py` fixture prevents silent tests-on-wrong-tree (mirrors prior-art Δ7 / D20) — `pytest tests/unit -q` REFUSES to run if `import daemon` resolves outside `/home/nea/ensemble-src-wt-hard-reboot-recovery`; **failure-injection check (A5/R-07)**: temporarily point the fixture at a fake path, observe LOUD refusal, revert |
| **AC6** | Manually exercised (NOT automated; smoke): boot the daemon against a freshly-rebooted host where PG is started by systemd in parallel — boot completes within ≤30s of unit start; the daemon's preflight `SELECT 1` waits for PG (capped at 10s per attempt) and the daemon starts cleanly the moment PG accepts connections |

---

## 5. Coupling

- **Tight with** the worktree gate (Phase 0; AC5 is the tripwire for every other AC in this phase; without it, every AC can silently test the wrong branch).
- **Loose with** the lifespan envelope (Phase 2 G2.A rides the same envelope shape at `api.py:582`; Phase 1 does NOT touch the lifespan, so the two compose cleanly).
- **Independent of** the AutoContinue pass (read the envelope pattern only, never modify; the PG-wait lane is upstream of AutoContinue at `__main__.py:143-247`).
- **Independent of** Phase 2 G2 (the MQ recovery service has its own lifespan envelope slot; Phase 1's `factory.py:connect_timeout` edit is orthogonal to G2's repo method).
- **Never touches** `daemon/services/auto_continue_boot_pass.py:230-494` (read-only anchor), `daemon/manager.py` body, `daemon/api.py:569` lifespan (no helper wrap), or the existing `find_stuck_messages` selector body.

---

## 6. Verification Steps

1. **Worktree gate (Phase 0):**
   ```bash
   cd /home/nea/ensemble-src-wt-hard-reboot-recovery
   .venv/bin/python -c "import daemon; print(daemon.__file__)"
   # MUST resolve to .../ensemble-src-wt-hard-reboot-recovery/daemon/__init__.py
   cat .agents/shared/planning/hard-reboot-recovery/worktree-claim.txt   # should exist
   ```

2. **Unit-test gate (AC2, AC3, AC4, AC5):**
   ```bash
   cd /home/nea/ensemble-src-wt-hard-reboot-recovery
   .venv/bin/pytest tests/unit/test_connect_timeout.py -q
   .venv/bin/pytest tests/unit -q                                     # regression gate
   ```
   Both green.

3. **Multi-edit read-back (repo trap #2):** after editing `daemon/factory.py` AND `scripts/systemd/ensemble-daemon.service`, re-read both diffs (`git diff`) and confirm exactly the intended hunks landed:
   - Unit file: ONE place gained `After=postgresql.service` + 2 comment lines. NO `Wants=`, NO `Requires=` line was added.
   - `daemon/factory.py`: ONE arg `connect_timeout=BOOT_DB_TIMEOUT_S=10` was added to `create_postgres_engine`. NO other lines changed.
   - **NO** other lines in either file changed.

4. **Systemd unit template verification (AC1, AC3 regression guard):**
   ```bash
   grep -n "^After=\|^Wants=\|^Requires=" scripts/systemd/ensemble-daemon.service
   ```
   Expect: `After=network-online.target`, `Wants=network-online.target`, `After=postgresql.service`. NO `Wants=postgresql.service`. NO `Requires=postgresql.service`.

5. **Adopt-unit re-splice note (AC1 reinforcement):** the `__ENV_LINES__` token splice at `scripts/upgrade/adopt-unit.sh:84-108` does NOT touch the `After=` line (the line is static), so editing the template propagates to NEW installs at the next adopt. **For existing installs** (the canonical install shape on this host), the runbook drop-in example `/etc/systemd/system/ensemble-main.service.d/10-pg-order.conf` (D15, Phase 3) is the delivery mechanism — `adopt-unit.sh:537` refuses template clobber.

6. **Preflight reuse verification (AC4, Task 1.3):**
   ```bash
   grep -n "BOOT_DB_TIMEOUT_S\|exit.*75" daemon/__main__.py daemon/run_app.py daemon/launcher.sh daemon/constants.py
   ```
   Confirm: `BOOT_DB_TIMEOUT_S=10` is set; `__main__.py:143-247` preflight `SELECT 1` is reachable from BOTH entry paths (`run_app.py:70` + `__main__.py:467`); launcher maps exit 75 to budget-exempt infinite backoff (per `launcher.sh:1340-1346,1576-1590`).

7. **Manual boot smoke (AC6, NOT automated):** bring up the daemon against a freshly-rebooted host where PG is started by systemd in parallel. Watch the journal:
   ```bash
   journalctl -u ensemble-daemon.service -f   # observe preflight "SELECT 1" attempts (if any) and the eventual boot line
   ```
   Boot completes within ≤30s of unit start.

8. **Worktree gate tripwire (AC5, A5/R-07):** temporarily edit the conftest fixture to point at a DIFFERENT daemon path (a fake `/tmp/...`); run `pytest tests/unit -q`; observe LOUD refusal (exit 1); revert. This is the failure-injection check that proves the fixture is not a no-op.

---

## 7. AC Mapping

| AC | Task(s) that produce it |
|----|--------------------------|
| AC1 | 1.1 (template edit) + Task 1.4 (regression-guard test) |
| AC2 | 1.2 (`connect_timeout` in `factory.py`) |
| AC3 | 1.4 (boundary + regression-guard test) |
| AC4 | 1.3 (verification task; no code change) + Task 1.4 |
| AC5 | 0.3 (fixture + failure-injection) |
| AC6 | manual smoke (NOT automated; logged in plan-overview.md §8 phase 1 gate) |

---

## 8. Rollback Note

Revert commits delete:
- the 3-line unit-template edit (no schema migration, so `git revert` is one atomic restore),
- the 1-LOC `connect_timeout=BOOT_DB_TIMEOUT_S=10` addition to `daemon/factory.py:232-284`.

No DB migration exists. No env-knob default is `ON` (the architect-folded A1 ELIMINATED `ENSEMBLE_INIT_RETRY` entirely per D16). The worktree gate (Phase 0 task 0.3) stays — it is the prior-art convention; the worktree itself is the rollback target. Phase 2/3/4 have not shipped yet, so no follow-up fallout.

If the rollback lands DURING the demo E2E (Phase 4), the runner reverts to "no G6 fix", which is the same shape as the current `feature/hard-reboot-recovery` tip — i.e., a successful revert returns the WT to a state where the gap-doc-recommended G6 fix simply doesn't exist yet, no worse than today. **No in-lifespan helper to roll back** (architect A1 removed it); the preflight-reuse + `connect_timeout` is the canonical lane and the rollback is a single file:line.

---

## 9. Ops-vs-Code Split

| Layer | What | Why |
|-------|------|-----|
| **Ops (this phase)** | 3-line unit template edit at `scripts/systemd/ensemble-daemon.service:84-87` (`After=postgresql.service` ONLY + 2 comment lines) | G6.A amended — the OPS half of the corrected mechanism; the bottleneck is unit ordering on canonical install shapes, NOT retry policy (architect A1) |
| **Code (this phase)** | +1 LOC `connect_timeout=BOOT_DB_TIMEOUT_S=10` in `daemon/factory.py:232-284` (alongside existing `pool_pre_ping`) | Architect A1 amendment — removes the firewall-DROP hang (75–130s → ≤10s) that would have made any retry budget fiction; this is the only code change in Phase 1 |
| **Reused (no change)** | `_boot_db_preflight` at `daemon/__main__.py:143-247` + `run_app.py:70` (frozen) + `__main__.py:467` (`python -m daemon`); launcher exit-75 budget-exempt infinite backoff at `launcher.sh:1340-1346,1576-1590`; `BOOT_DB_TIMEOUT_S=10` at `daemon/constants.py:70` | The preflight + exit-75 IS the canonical PG-wait lane. Zero new code needed here. |
| **Runbook (Phase 3)** | drop-in example `/etc/systemd/system/ensemble-main.service.d/10-pg-order.conf` (D15) for non-systemd-PG / existing installs; per-state RTO table for G8; D13 triage query | operator UX |
| **Test (Phase 4)** | manual smoke on a real systemd-installed host (Phase 6 AC6) | real-environment proof |

---

## 10. Coupling to the Prior Art

| Prior-art pattern | This phase's analogue |
|-------------------|------------------------|
| Three-layer never-wedge envelope (`auto_continue_boot_pass.py:230-494`; wiring site `daemon/api.py:2361-2368` per A8 anchor fix) | Phase 1 does NOT place anything inside the envelope (architect A1 removed the in-lifespan helper); the preflight is upstream of the envelope, on the `__main__.py` entry path |
| Single kill-switch `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART` (`auto_continue_boot_pass.py:145-156`) | Phase 1 has NO kill-switch (architect A1 ELIMINATED `ENSEMBLE_INIT_RETRY` entirely per D16 — fewer knobs = the mandate; the preflight is the existing, better-shaped lane) |
| Worktree-gate fixture (prior-art Δ7 / D20) | Phase 0 task 0.3 — verbatim port; **+ failure-injection check (A5/R-07)** |
| `worktree-claim.txt` (prior-art Δ7 / D20) | Phase 0 task 0.2 — verbatim port |
| Multi-edit read-back (repo trap #2) | Verifications step 3 — same discipline |

All references in this phase are mirrored from prior-art `auto-continue-running-after-restart` (landed v0.21.0) conventions; the user directive *"`try to REUSE a part of current mechanism, don't craft new if not really need`"* is honored — the entire Phase 1 surface is **≈4 LOC** (1 LOC `connect_timeout` + 2 LOC template comments + 1 LOC template edit), the smallest phase in the plan, and strictly less code than the pre-fold G6.B helper (which would have been ~30 LOC).

---

## 11. Out of Scope (Phase 1 explicitly does NOT do)

- **No schema migration** — no DB column is added.
- **No new Python helper** — `_init_manager_with_pg_retry` removed (architect A1); the preflight is the existing canonical lane.
- **No new env-knob** — `ENSEMBLE_INIT_RETRY` eliminated (D16); single kill-switch adopted.
- **No in-lifespan wiring** — no `daemon/api.py:569` neighborhood edit; preflight runs on the `__main__.py` entry path.
- **No MQ recovery pass** — Phase 2.
- **No runbook** — Phase 3 (D15 drop-in example, D13 triage query, NG-4 disclosure, etc.).
- **No demo E2E** — Phase 4.
- **No D5 / D7 / D11 / D13 / D17 work** (LANE-1 deprecation; G1 alive-row; G7 pre-flight; D13 task-less ghost; D17 mission job-watcher) — follow-up commissions.
