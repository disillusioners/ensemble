# Lessons — agent-restart builder-stage verification (2026-10-19/20)

Commission: independent functional verification of `feature/agent-restart` @ `55c45ab2a` (base `1fecf7c3f`). Full evidence: `RESULTS/2026-10-19-agent-restart-verification.md`.

## 1. Executed base-parity beats byte-identity derivation
This commission ran BOTH legs (HEAD + detached base worktree at `1fecf7c3f`, `uv sync --frozen`, interpreter byte-matched 3.14.7) under identical commands and diffed failing SETS, not counts. 47/47 IDs identical → pre-existing proven at the execution level, closing the credibility question the council flagged ("cannot be confirmed without pytest"). Prior commissions attributed by byte-identity/git-entailment (base leg NOT executed) — good, but executed legs are one evidence class stronger and cost ~2 min per dir pack with `-n 8`. Reuse: `/tmp` detached worktree + absolute-path uv (`/home/nea/.local/bin/uv`, off PATH) + `.venv` import-check before any pytest.

## 2. Interpreter parity is a comparison precondition
Worktree venv resolved 3.14.7 (uv default); main checkout and prior developer baselines are 3.13-lineage. Failing-set comparisons are only valid SAME-interpreter (both legs 3.14.7 here). Cross-baseline count comparisons (dev 6692 vs our 6691) need a footnote, not an alarm. One Py3.14-specific failure class exists (`asyncio.get_event_loop` in `test_round2_council_fixes.py` — pre-existing on both legs).

## 3. restart-sim fence: what "effective" looks like
The 2026-10-12 inert-guard incident class (operator precedence + HOME re-export) is avoided in `tests/test_agent_restart_sim.sh` because the guard (a) pattern-matches the RESOLVED paths, (b) runs unconditionally BEFORE the first write, (c) exits 99. Decisive chain: `mktemp FAKE_HOME` + `export HOME="$FAKE_HOME"` makes `resolve_env live` (HOME-derived `_install_dir_candidates`) resolve the FAKE install — runtime-provable via the `resolved env: target=live dir=/tmp/ens-restart-sim.*/...` line. Residual edge: empty-FAKE_HOME (mktemp failure) degenerates the pattern to `/*` (SF-2); 8088 not in the port-refusal set (SF-3).

## 4. Sim findings that matter beyond this commission
- SF-1: passing args to a stub via `bash "$stop_script" "$INSTALL_DIR"` does NOT export the env var — the stub's `$INSTALL` was unset → out-of-temp write attempt (`/stub-stop.log`). Stub scripts must take paths from argv, not ambient env.
- SF-7: `readyz=pass(one-shot)` was boot-grace-SUPPRESSED (curl rc=7 → transient → `_probe_once` returns 0). A probe assertion that only checks "readyz=" presence cannot distinguish suppressed-green from probe-green. Gate assertions should assert classification, not presence.
- SF-5: MagicMock manager collapses override capture to `[]` — a sim can go green without exercising the populated path. Pair sims with unit pins for the populated render (done here: `limits.worker_pool_size: 5→7 (applied)` exact-pin).

## 5. Coverage gaps parked for the improve-lane (behavior holds, pins absent)
`adoption-in-progress` / `pipeline-not-settled` / `reason-required` behavioral invocations; min-uptime 600s numeric pin; budget-survives-REAL-restart boundary; None≠zero-dict discriminator (currently disjunction `unavailable or applied`); dry-run no-run_id pin; dry-run overrides_pending render pin. All in RESULTS §4.

## 6. Test-hygiene landmines (pre-existing, repo-wide)
- daemon import at COLLECTION time can append `kms_issue` lines to git-TRACKED `.agents/shared/planning/designer-agent/install-audit.jsonl` — fired non-deterministically on 2 of 10 legs. Post-run `git status --porcelain` is mandatory; restore with `git checkout -- <file>` + disclose. Root fix belongs to a test-debt commission (that file should not be tracked or the append should be fenced).
- `concurrency_atomic_unit_test.sh` under `set -euo pipefail` exits 1 WITHOUT its `RESULT: FAIL` echo when pytest fails — grep-for-RESULT consumers misread failures as crashes. Read exit codes.
- Ambient shell env on this host carries LIVE-PROD values (`POSTGRES_HOST=10.44.0.2`, `POSTGRES_DB=ensemble_prod`, `ENSEMBLE_SELF_ENV=live`). The `env -u POSTGRES_{DB,HOST,PORT,USER,PASSWORD} -u DATABASE_URL` scrub is load-bearing on EVERY pytest invocation — never skip it "just for unit tests".

## 7. Spec discipline
Spec doc at HEAD ≠ implementation base (rev5 @ `1fecf7c3f` + 22-line post-build reconciliation: 9 cite-refreshes + 3 review folds). Any tooling hashing the spec must pin the `1fecf7c3f` BLOB (`1eada5670`), not the HEAD file (`2ee61f7eb`), or it registers false drift. Line-cites in specs drift (factor-2 block actually 3480-3646, not ~3432-3501) — hash-based byte-identity over the extracted block is the durable check, and it PASSED (`67facda3…` both revs).
