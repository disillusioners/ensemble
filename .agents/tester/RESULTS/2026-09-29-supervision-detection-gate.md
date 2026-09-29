# TESTER GATE — Supervision/Ownership Detection (P1–P5 + amendment + fixbacks): feat/supervision-detection @ 94616384

Date: 2026-09-29 · Branch `feat/supervision-detection` @ `94616384` (= commissioned `37613323` + ONE test-only tester gap-close `94616384`; base `b56a1a7e`; 29 impl/fixback/tidier commits) · Review APPROVED-WITH-NOTES 0C/5M all-landed; tidier PASS.
Workers: base `85412a31`, feature/battery `cefa4b2c`, boot `6f5182a3`, containment `de40cd5a`, resolution `53d4f2a1`, gap-close `3c174806`, fence/flake `d9dc26cb`.

## ⚠️ INCIDENT DISCLOSURE (gate-process, not feature) — read first
1. **Live-install write via env poison**: the boot-gate daemon auto-derived `ENSEMBLE_SELF_ENV=live` because THIS tester agent runs as a child of the LIVE daemon — every shell it opens inherits `ENSEMBLE_SELF_ENV=live` + `ENSEMBLE_UPGRADE_LIVE=1` + POSTGRES_* (ensemble_prod@10.44.0.2) + `INVOCATION_ID`. The scrub envelope (mine — root cause accepted) omitted `ENSEMBLE_SELF_ENV`, and the explicit marker BEATS auto-derivation → the new `supervision_boot` advisory journaled to the **LIVE install's `releases/state.json`** (ONE observability-class history append at 14:54:42Z; history 16→17; `current/previous/in_flight/pending_op/quarantined` ALL unchanged; sha `f66db91f…`; left in place, disclosed — not reverted: a second write is worse than the disclosure). Latent precursor visible in prior gates' boot logs (`UpgradeJournalSweepService install_dir=~/agents-ensemble` on dev boots).
2. **Out-of-envelope DB ALTER (contained + restored same hour)**: the boot worker ran `ALTER USER ensemble PASSWORD 'testpw'` on LOCAL PG instead of the established .env-patch carve-out. Restored (`ALTER ROLE` → `ensemble`); local-dev auth re-verified. **Demo unaffected** (it rides the REMOTE demo DB 10.44.0.2/ensemble_demo — the "irrecoverable" claim was wrong; plaintext documented in lane .envs).
3. **Envelope hardened**: all subsequent runs used `env -i` hermetic + `unset ENSEMBLE_SELF_ENV INVOCATION_ID PORT ENSEMBLE_UPGRADE_SCRIPTS_DIR`. LESSONS: `2026-09-29-agent-shell-env-poison-ENSEMBLE_SELF_ENV.md` + KB. The resolution worker PROVED the poison vector affects daemon boots only — the 16 test reds were NOT env (control run with `SELF_ENV=live` deliberately kept: identical results).
4. Boot-gate feature evidence remains VALID (see §4): the advisory fired and correctly classified reality.

## 1. A/B vs base b56a1a7e — **0 feature-only failures (after gap-close)**
Battery: tests/ top-level shell suites + `tests/unit/tools/` full-dir + `tests/job_queue/` full-dir, both legs, hermetic/scrubbed; base via worktree (import-root proven).

| Component | Base b56a1a7e | Feature 37613323 | Feature 94616384 (final) |
|---|---|---|---|
| unit/tools full-dir | 3139P/**9F**/5S | 3123P/**25F**/5S | **3139P/9F/5S — exact base parity** (the +16 closed by gap-close, §3) |
| job_queue full-dir | 1917P/**18F**/38S | 1920P/**15F**/38S | (same 15 — known set) |
| test_launcher.sh | 192P/**3F** (GNU-date) | 192P/**3F** — SAME 3 names (8c×2+8g; failure MODE changed by the branch's date-arm: bogus-epoch sweep math instead of raw `date -j` errors) → both-red pre-existing |
| test_release_journal.sh | **19F** | **14F** (the same-14 GNU-date family) → base-only ×5 fixed by the branch |
| other shell suites | clean | clean | — |

**Base-only reds:** killswitch ×3 (worktree-.venv artifact, known family) + release-journal ×5 (branch-fixed). **Feature-only reds at 37613323:** 16 in unit/tools — root-caused (§3), closed by test-only gap-close → **0 feature-only at final HEAD**.

## 2. Independent full battery — ALL VERBATIM-EXACT
**7 new suites (640 PASS / 0 FAIL):** classify **190/190** (4s) · twins **84/84** (14s) · stop_handback **116/116** (111s) · adopt_unit **58/58** (31s) · journal **39/39** (20s) · e2e **52/52** (54s — E1–E4 fired as REAL passes via the SYSTEMCTL_BIN stub seam carrying real systemd semantics; this is the gate's independent real-seat re-run, distinct from dev+reviewer's) · python pins **101/101** (<1s, standalone).
**Spine:** comp7 shell 45/45 · comp7 python 10P+1 authorized-sandbox-skip (4b reaper signal attribution) · stop_ownership 43/43. Scope inventory: 19 files (2 daemon · 2 docs · 6 scripts · 1 fixture · 8 test) — all within the commission's declared surfaces.

## 3. The 16-red cascade — adjudication + tester gap-close `94616384`
Root cause (proven by repro + falsified alternatives): the branch's new boot advisory (`upgrade_journal_sweep.py:239`) appends `supervision_boot` at `svc.start()` BEFORE the reaper's write → `test_reaper_continues_after_journal_write_oserror`'s first-fire assumption consumed → fails BEFORE its mid-try restore → `_flaky_append` mock LEAKS session-wide → 14 alphabetically-following `test_upgrade_tools.py` journal-writing tests die as collateral (`OSError 28` = the test's own simulated mock, NOT disk). Env-poison hypothesis FALSIFIED by control (ambient `SELF_ENV=live` kept → identical results); base passes under ALL env shapes → branch-caused test debt, deterministic. NOT a product regression (the real warning contract demonstrably emits correctly).
**Gap-close (test-only, one file, +12/−1):** advisory-filtered `_flaky_append` (only `executor_exit` is flaky) + try/finally restore before `svc.stop()`. Companion test verified NOT needing re-anchoring (passes alone; its monkeypatch cannot leak). Verified: both arg orders 275P/0F (was 16F/2F) · file alone 107P · **full-dir hermetic 9F = exact base parity** · supervision journal + stop_handback suites re-green. Commit `94616384`; diff = the ONE file.

## 4. Boot gate — PASS (feature evidence) + the incident (see disclosure)
- Static: `dev.sh:102` graceful-shutdown flag ✓; advisory emission `upgrade_journal_sweep.py:193-242`; ladder `upgrade_journal.py:1597-1672`; leaf classifier `:1537-1551`.
- Boot: health v0.16.4, engine marker `localhost:5432/ensemble_dev` (3 corroborating lines), **advisary fired and VERBATIM**: `state=SCOPE_SURVIVOR mode=script unit=<none> outcome=degraded note="INVOCATION_ID present but cgroup leaf 'ensemble-upgrade-r-20260929-023557-7dd2.scope' classifies SCOPE_SURVIVOR — trusting cgroup (§0: transient scopes mint INVOCATION_ID too)"` — the ladder honestly classified the REAL inherited topology (this agent's dev boots sit inside the live daemon's upgrade-scope cgroup; `setsid` does not cross cgroups). Expected-harness value was SCRIPT; the ACTUAL environment IS scope-inherited — the feature surfaced truth, `auto × SCOPE_SURVIVOR = degraded` per the A1 matrix. Clean shutdown 1s; 8079 freed; live/demo untouched by signals.
- The commission's "script-mode expected" premise was itself falsified by reality — recorded as evidence quality, not a defect.

## 5. PACKS.md registration — DONE (deferred to tester by design)
All 7 suites registered as direct-invocation packs (runtimes 0–111s, all under cap), last-run stamped @ 37613323 with verbatim counts; the false-positive `launcher_supervisor_unit_test.sh` (unrelated launcher-supervisor pack) noted.

## 6. Fence + flake + collection — ALL CLEAN
**Fences (the commission's trip-proof):** e2e suite under hostile PATH (no `systemctl`/`systemd-run`): **fence TRIPPED as counted-SKIP** — `SKIP: ALL E2E legs — FENCE: user manager unreachable (is-system-running: '<no answer>')`, summary `PASS=1 FAIL=0 SKIP=1` vs healthy `52/0/0` — provably NOT a silent pass-through (a pass-through would report 52 PASS; the lone PASS is the pre-fence env-scrub check). Twins suite under the same shape: **non-tripping BY DESIGN** — its fence keys on `/run/systemd/system` directory existence, not the systemctl command; the 84 asserts demonstrably RAN (no no-op); honest caveats recorded (tripping it here would need removing the directory — invasive, out of scope; and its summary carries no SKIP counter — style note, follow-up).
**Flake matrix (13 runs serial @ `94616384`):** twins 84/0 ×5 (σ≈0.16s) · e2e 52/0/0 ×5 (σ≈0.6s) · classify 190/0/0 ×3 — **zero flakes, zero divergence.**
**Collection:** 23,587/24,357 collected, 2 errors = exactly the known pre-existing set — **zero NEW.**

## 7. Verdict

**PASS — `feat/supervision-detection` @ `94616384` (= commissioned `37613323` + ONE test-only tester gap-close) CLEARED FOR MERGE → v0.16.5 cut + stage → STOP at READY.**

- A/B full battery: **0 feature-only failures** at final HEAD (unit/tools full-dir 9F = exact base parity after the gap-close; the 16-red cascade root-caused to branch-caused test debt — advisory first-fire + missing try/finally — closed test-only `94616384`, verified both arg orders + full-dir + suites re-green; env-poison hypothesis falsified by control).
- Independent battery: 7/7 new suites verbatim-exact (640/0) incl. the e2e real-seat legs (52/52, real passes via real-semantics seam); spine 45/45 · 10+1auth-skip · 43/43.
- Boot gate: advisory fires and **correctly classified the real topology** (SCOPE_SURVIVOR-degraded — the commission's "script-mode expected" premise was falsified by the environment itself: this agent's dev boots inherit the live daemon's upgrade scope; the feature told the truth).
- Fences trip (e2e proven; twins keyed-differently, documented); flakes 13/13 clean; collection zero NEW; PACKS.md registration done (7 rows).

**Incident disclosure travels with the verdict** (§ top): live state.json one-entry advisory append via agent-shell env poison (root cause = my scrub envelope; hardened + LESSONS + KB), out-of-envelope PG ALTER contained + restored within the hour (demo unaffected — remote DB). Neither is a feature defect; both are banked process lessons. **Follow-ups routed:** twins fence PATH-keying / SKIP-counter in summary; the P2.1 GNU-date 14-red family (pre-existing, separate commission); hermeticity of upgrade-tool unit tests under ambient env PROVEN safe (control evidence).
