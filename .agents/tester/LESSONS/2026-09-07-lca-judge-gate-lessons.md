# LCA judge gate lessons (2026-09-07, delta bb052fce..d6e30d9d)

## 1. Shared-worktree gate protocol (relaxed drift rule + path-scoped commits) — PROVEN

Problem: a 6-worker merge gate sharing ONE dedicated worktree (`ens-lca-judge`), where several workers must COMMIT evidence artifacts on the branch mid-gate. The original "HEAD must equal d6e30d9d" drift guard false-blocks the moment the first evidence commit lands (tip walked d6e30d9d → b42f7237 → fdec1e9f → 346b1842 → 2a43904c during the gate).

Working protocol (zero index.lock collisions, zero missed runs):
1. Drift check = `git merge-base --is-ancestor <delta-tip> HEAD` (exit 0) AND `git diff --name-only <delta-tip>..HEAD` shows ONLY `.agents/tester/RESULTS/*` paths. Both hold → code under test is byte-identical → proceed.
2. NEVER checkout/detach/reset the shared worktree mid-gate (a detach would strand sibling commits).
3. Evidence commits must be PATH-SCOPED: `git add <exact files>` + `git commit -m "test: …" -- <exact files>`; on `index.lock` collision wait 2s, retry ≤5×; never `git add -A`/`git add .`.
4. Issue the coordination addendum to ALL in-flight workers the moment the first sibling commit lands — waiting for each worker to discover the drift wastes a run.

## 2. Worktree boot DB-override recipe — per-field envs required, POSTGRES_URL alone is NOT enough

Boot-smoke run 1 pinned only `POSTGRES_URL` to the disposable DB: persistence/checkpointer honored it, but `daemon/repositories/factory.py::create_postgres_engine` (:180-210) reads ONLY per-field `POSTGRES_HOST/PORT/DB/USER/PASSWORD` — repository engine silently fell through to fresh-`ensemble.json` defaults → logged `Creating PostgreSQL engine: localhost:5432/ensemble_prod`. This is the documented open F-DR1-2 residual (POSTGRES_URL ignored by factory), now with concrete boot evidence. Corrected recipe (committed in `2026-09-07-lca-judge-boot-smoke.sh` @ 2a43904c): export the FULL per-field set alongside POSTGRES_URL. Exposure in the incident run was bounded (idle boot, delta has zero schema DDL, zero instance rows, graceful dispose) — but any worktree/dev boot that WRITES will land in prod unless the full set is pinned. Also: replicate the dev.sh launch minus `--reload` when sibling commits may land during the boot.

## 3. Shared-log stale-grep trap (re-greps must bracket the post-baseline region)

Second boot into the SAME `data/logs/ensemble.log` made in-script greps match run-1 lines; initial "run-2" numbers were actually run-1's. ensemble.log line numbers are NOT chronological (known convention) and repeated boots append. Fix: record the baseline offset/line-count before boot, then grep only the post-baseline region; report absolute line numbers computed from the region. Same discipline as time-bracket forensics.

## 4. LIVE-LLM probe methodology — distinguish judge-quality defects from transport timeouts BEFORE flagging

The judge is conservative by design: EVERY failure path (3-layer timeout, HTTP error, unparsable, empty, malformed) returns `is_complete_report=False` so the deny+nudge fall-through stays available. Consequence for live probes: a genuine-report sample that times out is NOT a prompt/parser defect — check the `verdict`/`error_class` fields: `verdict=timeout error_class=TimeoutError` = transport; only a returned-and-parsed wrong verdict is a judge-quality finding. Probe discipline that worked: import the REAL entrypoint (`judge_completion_report_async` — never reimplement the prompt), resolve the model via the real resolver, budget ≤8 calls, per-call wall + shell `timeout 300`, record latency per sample (latency distribution is itself a finding: prod cap `JUDGE_TIMEOUT_S=10s` vs observed 2.6–13.6s spread + 4/8 >15s means frequent prod degrade-to-base behavior).

## 5. Judge-suites-under-PG shape (pure-unit, PG-inert) — prove it, don't assume it

The 3 judge suites build NO engine at run time (proved via external engine-spy pytest plugin patching sqlalchemy/aiosqlite/asyncpg/psycopg constructors BEFORE conftest imports — throwaway `/tmp` plugin, zero repo modification). The PG gate requirement then resolves to: (a) identical counts default-dialect vs PG-env (72/72, no dialect-conditional skips), (b) positive proof that any engine the daemon WOULD build under the wired env is postgresql @ the disposable DB (`_build_pg_connection_string` both env branches + asyncpg leg). "Suites are PG-inert" is a valid PASS finding when pre-authorized in the pack definition.
