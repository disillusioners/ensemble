# HANDOFF — sweep-truth-gate-hygiene (sonic lane)

Commission: observability/loop-hygiene/leak-gate lane (4 items + 1 optional, all landed).
Base bf4bfde6e (= origin/latest @ 2026-11-14, tag v0.30.9). Feature branch
feature/sweep-truth-gate-hygiene @ integration worktree
/home/nea/ensemble-src-wt-sweep-truth-gate-hygiene. Module commits: ee79494cc (M1),
7500a0565 (M2), a68087237 (M3); merges 658494555 / e913e7110 / 391d96563 (slots 1-2-3).
Target release v0.30.11 (tag slot re-verified free at cut time; see Bash-raises used).

## Files touched across modules

- M1 stgh-sweep-truth (slot 1, module branch feature/stgh-sweep-truth @ ee79494cc):
  daemon/services/worker_pool.py; daemon/services/eligible_pending_sweep.py;
  daemon/services/pool_orchestrator.py; daemon/repositories/task/repository.py;
  daemon/manager.py (BRIEF DEVIATION — verified minimal, see Known risks);
  tests/job_queue/test_stgh_sweep_truth_pins.py (NEW, 347 LOC).
- M2 stgh-leak-gate (slot 2, feature/stgh-leak-gate @ 7500a0565):
  scripts/run_tests_scrubbed.sh; tests/test_run_tests_scrubbed_selfenv_gate.py (NEW, 81 LOC).
- M3 stgh-log-quiet (slot 3, feature/stgh-log-quiet @ a68087237):
  daemon/logging_setup.py; tests/unit/test_log_hygiene.py (extended, +36 LOC).
- Integration: this HANDOFF.md (sonic-developer's single file write).

## What changed

- (a) TELEMETRY TRUTH: WorkerPool.notify_work() now returns bool (True = pulse
  delivered); PoolOrchestrator.notify_all() returns int = pools successfully pulsed
  (a raising pool keeps its WARNING and counts as not pulsed); InstanceManager
  ._notify_all_pools forwards the int; safe_notify_all_pools passes it through with
  the Mock-compat __dict__-probe contract UNCHANGED. The sweep derives
  notified=bool(result): real dispatch logs "notify_work dispatched=True"; failure
  logs False with a REAL cause ("no wake path reachable (unwired)" vs "wake path
  reached, 0 pools pulsed (see WARNING ...)"). The chronic live lie
  (34x dispatched=False, 0x True) and the false "worker_pool is not wired" DEBUG
  are dead. Runtime log prefix "notify_work dispatched=" preserved (source f-string
  is split across lines — grep the LOG, not the source).
- (b) OFF-LOOP W-D FILTER: list_paused_or_terminal_instance_ids call wrapped in
  asyncio.to_thread (mirrors the primary scan) — no sync DB on the event loop.
- (c) BOUNDED PENDING SCAN: list_pending_tasks_older_than gains keyword-only
  limit: int | None = None (default = prior behavior byte-for-byte; the three
  drift-reconciler call sites in job_recovery_service.py are UNTOUCHED and remain
  unbounded = correctness path). The sweep passes _SWEEP_PENDING_LIMIT =
  2 * (WORKER_POOL_SIZE + CHAT_WORKER_POOL_SIZE) = 14 — derived from claim
  capacity (the pools one pulse wakes), documented in-source; oldest-first ordering
  keeps the scan on the claimable head.
- (d) LEAK-GATE: scripts/run_tests_scrubbed.sh now FAILS CLOSED (exit 78, loud
  ENV-SCRUB FAILURE + remediation line) on an ambient ENSEMBLE_SELF_ENV outside
  {unset, dev, demo, sandbox} (exact lowercase); the sanctioned ENSEMBLE_SELF_ENV=dev
  prefix passes through. Existing unset families + compgen leak gate untouched.
- (e) LOG QUIET (KEPT, drop-clause not triggered): the `openai` logger (covers
  openai._base_client full-prompt "Request options" flood) pinned to INFO via
  env-overridable ENSEMBLE_LOG_OPENAI_LEVEL (default INFO, DEBUG opt-in), applied in
  install_root_logging() alongside the uvicorn.access precedent. daemon hierarchy,
  root gate math, handler levels, rotation untouched; attribution lines
  (daemon.graph DEBUG [LLM][<iid>]) untouched.

## What is UNTESTED

- M1 PG dialect path: all repository/pin tests ran on SQLite; the new LIMIT clause
  is unexercised against PostgreSQL (SQLAlchemy limit() is dialect-portable; still
  honestly untested on PG).
- M1 runtime-resolved pool sizes: _SWEEP_PENDING_LIMIT uses the constants defaults
  only; settings-managed limits.worker_pool_size / chat size overrides at runtime
  are not reflected (documented in-source).
- M1 counter-mutation reddening explicitly evidenced for P2/P3/P4; P1's two log
  tests were not individually counter-mutation-run per the module report (P1 shares
  the mutated surface with P2 — reverting notify_work()->None collapses P1's True
  case — so indirect coverage exists, but no dedicated P1 reddening run was reported).
- No daemon-level live boot smoke: the truthful dispatched=True line is verified at
  unit level (caplog), not yet observed in a live ensemble.log tick. Post-promote check.
- M2 clean legs verified with --version only (no full-suite run through the wrapper
  in this commission); refusal leg verified without venv. Live-shell (ambient=live)
  refusal self-verified from this very daemon-child lane.
- M3 knob verified at unit level only; no live-flood before/after measurement.
- Integration seams: merged-tree targeted suites green (27 + 10 + 34 passed, P10
  pack plane_shell_scrub_content 10/10, bash -n OK); NO broader regression shard
  was run — the leader/tester gate owns that.

## Known risks

- daemon/manager.py was edited though outside M1's brief FILES (brief deviation).
  Verified minimal at integration: 2-line return-forwarding (-> int; return
  self._orchestrator.notify_all()) + docstring — required, or the int dies at the
  manager seam and the telemetry lie persists. Disclosed, not hidden.
- _SWEEP_PENDING_LIMIT's 2x headroom may under-sample when is_deferred / W-D
  post-filters shrink a bounded sample; the sweep is a backstop healer and the
  unbounded reconciler remains the correctness path (in-source comment says so).
- Adjacent scrub-gate gaps deliberately OUT OF SCOPE (per commission): ENSEMBLE_UPGRADE_LIVE
  and MCPS_GATEWAY_* (4 vars) still pass the wrapper's gate.
- Busy-instance claim-gate serialization (claims_skipped_due_to_busy_instance):
  untouched (commission OUT OF SCOPE).
- ENSEMBLE_LOG_OPENAI_LEVEL is captured at import/reload time (mirrors the existing
  _CONSOLE/_FILE_LEVEL pattern) — a running daemon needs a restart to change it.
- LIVE behavior unchanged until the user's promote: live still floods openai
  DEBUG and still logs the old lie until v0.30.11 is promoted.
- git fetch on this host reports pre-existing tag clobber rejections (v0.27.5,
  v0.29.6) — local tag divergence predates this commission; not touched.

## How to verify (on the MERGED tree)

    export PATH="/home/nea/.local/bin:$PATH"
    cd /home/nea/ensemble-src-wt-sweep-truth-gate-hygiene && uv sync
    env -u POSTGRES_HOST -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_PORT \
      ENSEMBLE_SELF_ENV=dev .venv/bin/pytest \
      tests/job_queue/test_stgh_sweep_truth_pins.py \
      tests/job_queue/test_a3_eligible_pending_sweep.py \
      tests/unit/test_reconcile_empty_cycle_info_log.py \
      tests/test_run_tests_scrubbed_selfenv_gate.py \
      tests/unit/test_log_hygiene.py -q          # expect 71 passed
    ENSEMBLE_SELF_ENV=live bash scripts/run_tests_scrubbed.sh --version
        # expect rc=78 + "ENV-SCRUB FAILURE: ambient ENSEMBLE_SELF_ENV" + no pytest banner
    env -u ENSEMBLE_SELF_ENV bash scripts/run_tests_scrubbed.sh --version   # rc=0
    ENSEMBLE_SELF_ENV=dev bash scripts/run_tests_scrubbed.sh --version      # rc=0
    env -u ENSEMBLE_SELF_ENV bash tests/packs/plane_shell_scrub_content.sh  # 10 passed
    # Post-promote (live): ensemble.log shows "notify_work dispatched=True" on real
    # ticks; openai._base_client DEBUG absent unless ENSEMBLE_LOG_OPENAI_LEVEL=DEBUG.

## Bash-raises used

none — every bash command stayed under the 600 s default; the release build
(stage.sh) runs as a background proc per rule, not a raised blocking call.

## Complexity self-assessment

medium — 4 scoped items, ~40 LOC source across 5 files + ~465 LOC tests/docs;
disjoint modules, no schema/migrations, no coupling beyond the one folded into M1.
Verdict medium at plan time (§2.5); no handover raised; no override consumed.
