# A/B base-vs-HEAD verification on this host: env shape + concurrency hazards (DF-4 verification, 2026-10-22)

Date: 2026-10-22 · Lanes: W-A/W-B/W-C/W-D (DF-4 independent verification)

## 1. The ambient shell on this host is LIVE-shaped
`POSTGRES_DB=ensemble_prod` (remote 10.44.0.2), `ENSEMBLE_SELF_ENV=live`, `ENSEMBLE_UPGRADE_LIVE=1` are ambient. The per-variable scrub (`env -u POSTGRES_DB -u …`) is load-bearing, not cosmetic — `env -u POSTGRES_*` remains a silent no-op (critical note). Every real-PG invocation: scrub per-variable → SET explicitly → assert resolved-not-prod in-shell AND inside the test.

## 2. Concurrent legs on ONE shared scratch PG DB corrupt A/B verdicts (symmetrically)
Running BASE and HEAD legs **concurrently** against the same scratch DB produced 9 transient `psycopg UniqueViolation (pg_type_typname_nsp_index)` setup-ERRORs that masqueraded as 4 net-new + 5 base-only reds. Root cause: `tests/unit/auth_pg_scratch_helper.py` DROP/CREATEs FIXED table names; its xdist guard covers only `PYTEST_XDIST_WORKER`, not two independent pytest processes. The race is SYMMETRIC — it can fake a "branch fix" as readily as a "branch regression". Rule: **A/B legs strictly one-at-a-time**, or per-leg private scratch DBs (W-A/W-D used `df4v_scratch` / `df4v_ari_scratch` — created + dropped + proven gone). Same applies to concurrent tester lanes sharing `ensemble_test` (conftest `drop_all` at setup AND teardown).

## 3. Throwaway worktrees have no `.env` — red POPULATION changes (verdict does not)
`git worktree add` does not carry the gitignored `.env` (`:40`). Config/DB-dependent unit tests (e.g. `test_configure_builtin_idempotency.py` 404==201, `test_job_queue_proxy_phase1.py` 'pending'=='processing') fail IDENTICALLY on both legs, inflating S3 absolute reds (93 vs the implementation lane's ~11 in a fenced-.env worktree). Always state the `.env` shape in A/B briefs; identical-both-legs keeps the net-new verdict sound.

## 4. Venv isolation is correctness, not hygiene
The DF-4 worktree `.venv` is editable-bound to its tree — reusing it from a base checkout silently runs HEAD code and fabricates "base" evidence. Lazy per-worktree `uv sync --frozen` (house convention) for every throwaway; prove import resolution (`python -c "import daemon; print(daemon.__file__)"`) per side.

## 5. Load-sensitive flake classifier
`test_upgrade_journal.py::TestExecutorSpawn::test_real_spawn_env_and_process_group_independence` failed once under parallel load, passed 3×/3 isolated on BOTH legs. It spawns a real subprocess and waits for the env-dump file to *exist* (not flush) — chunk-context-sensitive. Pre-existing; quarantine evaluation routed to the owning lane. General rule kept: 3× isolated per side before classifying.

## 6. Small traps hit (fix in briefs, not code)
- Chunk evidence filenames must encode chunk KIND (a `HEADONLY` chunk reusing index 0 clobbered the `SHARED` chunk-0 output on both legs — detected + re-ran).
- `/bin/sh` (dash) has no `PIPESTATUS` — capture pytest exit codes explicitly, not via bash-isms.
- Plan-prose arm labels were inverted (base `:1209` = ari-fallback, `:1734` = direct; HEAD `:1217`/`:1748`) — always locate sweep sites by grep, never by plan line numbers.
- The ari-fallback arm does NOT increment `result.delivered` (WakeSweepResult rebuilt unchanged, `sweep :1829-1834`) — assert on the delivered queue ROW, not the counter.
