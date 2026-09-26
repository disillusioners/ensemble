# Checkpoint-Retention Change Verification — `feature/checkpoint-retention-keep-3` (UNCOMMITTED)

**Date**: 2026-09-26 | **Tester instance**: 7db3e519 (test leader) | **Mode**: validate-only
**Change under test**: UNCOMMITTED working-tree diff on `feature/checkpoint-retention-keep-3` @ HEAD `0f9bda12` — 5 files, +135/−14:
`daemon/config.py` (+31: `PersistenceConfig.checkpoint_max_per_thread: int = Field(default=3, ge=1, validation_alias=AliasChoices("checkpoint_max_per_thread", "CHECKPOINT_MAX_PER_THREAD"))`), `daemon/constants.py` (−1: removed `CHECKPOINT_MAX_PER_THREAD: int = 50`), `daemon/services/maintenance.py` (Op D reads `self._config.checkpoint_max_per_thread`; adapter call sites unchanged), `tests/test_maintenance.py` (+89/−6: 6 new tests), `tests/integration/checkpoint_prune_real_saver.py` (−1: stale import of the removed constant).

**Constraints honored**: zero commits, zero source modifications, main checkout untouched (verified before+after every pack: `git status --porcelain` = 5M + 1 untracked probe file). One test file ADDED (genuine gaps — see §4). All runs `uv run python -m pytest` from repo root, env-scrubbed (`unset POSTGRES_*` — inherited env points at prod `ensemble_prod`, never touched).

## VERDICT: **PASS** — change validated; ready for the author to commit

## §1 Focused suites (dev claims independently reproduced)

| # | Suite | Command (evidence) | Exit | Result | Runtime |
|---|-------|--------------------|------|--------|---------|
| 1 | `tests/test_maintenance.py` | `timeout 300 uv run python -m pytest tests/test_maintenance.py --tb=short -q` | 0 | **PASS — 78/78** (0F/0S/0D) | 0.54s |
| 2 | `tests/integration/test_checkpoint_cleanup_job_wiring_pin.py` | same pattern | 0 | **PASS — 3/3** (AST singleton pin holds; file NOT part of diff — run as regression net) | 3.59s |
| 3 | `-k checkpoint_max_per_thread` across `tests/` | census `--collect-only -q` then run (infra bypass: `--ignore=tests/packs --ignore=tests/e2e` — pre-existing collection errors: bash-style pack script `sys.exit(0)` + module-level `requests.get` needing live daemon; unrelated to change) | 0 / 0 | **PASS — 10/10 selected, census-proved** (`10/23443 tests collected (23433 deselected)`; run: `10 passed, 1 skipped, 23433 deselected`; the 1 skip = pre-existing standalone-script skip, not in selection) | 6.96s |

## §2 Behavior probes + edge cases (mock-based, PG-free)

**Probe file ADDED**: `tests/unit/test_checkpoint_retention_config_probes.py` (352 lines, 6 tests, UNCOMMITTED/UNTRACKED per task constraint). Run: `timeout 300 uv run python -m pytest tests/unit/test_checkpoint_retention_config_probes.py --tb=short -q` → exit 0, **6/6 PASS in 0.18s**.

| Probe case | Status | Witness |
|---|---|---|
| P1 default == 3 | COVERED (author) | `TestConfigDefaults::test_checkpoint_max_per_thread_default` |
| P2 env override binds (12 / 17) | COVERED (author) | `..._env_override`, `..._env_override_threads_to_cleanup` |
| P3 invalid int 0/−1/−50 → ValidationError at construction (fail-loud, NOT clamp) | COVERED (author) | `..._ge_1_guard_rejects_invalid[0/-1/-50]` |
| P4 non-int "0"/"−5"/"abc" → ValidationError | COVERED (author) | `..._non_int_fails_loud[0/-5/abc]` |
| P5 env value flows to ALL FOUR adapter calls (`find_excess_checkpoint_groups(N)`, `get_checkpoint_ids(tid,ns,N)`, `delete_checkpoints_excluding(tid,ns,keep-set)`, `delete_writes_excluding(tid,ns,keep-set)`) | **GAP → ADDED** | author's async test only asserted `find_excess_checkpoint_groups(17)`; probe: `test_env_override_flows_to_all_four_adapter_calls` |
| P6 N=1 boundary (exactly newest kept, no off-by-one/clamp-to-0) | **GAP → ADDED** | `test_n_equals_1_keeps_only_newest_checkpoint` |
| P7 N > available → no-op, no error (inner defensive branch maintenance.py:1269) | **GAP → ADDED** | `test_inner_defensive_noop_when_get_checkpoint_ids_returns_empty` |
| P8 multiple threads each keep own newest N, no cross-thread bleed | **GAP → ADDED** | `test_multiple_threads_each_pruned_with_the_same_n` |
| P9 per-`checkpoint_ns` retention (ns threaded to adapter calls) | **GAP → ADDED** | `test_per_checkpoint_ns_threaded_to_adapter_calls` |
| P10 prefix spelling `PERSISTENCE_CHECKPOINT_MAX_PER_THREAD` | **ADDED (pins docstring contract)** | `test_persistence_prefix_alias_does_not_bind` — explicit `AliasChoices` disables `env_prefix` for this field: prefix form does NOT bind, value stays 3. Docstring contract holds. |

Adjudication of author's tests: **sound** — no tautologies, no silent clamps, no mocked-away assertions.

## §3 PG-gated integration — `tests/integration/checkpoint_prune_real_saver.py` (binding gate)

Disposable PG14 (`initdb -A trust -U ensemble`, port 15432/15433 mktemp PGDATA, `POSTGRES_*` scrubbed, teardown verified: port freed, PGDATA removed, prod 5432 untouched). Command shape: `PG_TEST_HOST=127.0.0.1 PG_TEST_PORT=1543x PG_TEST_USER=ensemble PG_TEST_PASSWORD=x PG_TEST_DB=ensemble_test timeout 300 uv run python -m pytest tests/integration/checkpoint_prune_real_saver.py --override-ini='addopts=' --override-ini='timeout=120' --tb=short -q`

| Leg | Tree | Result |
|---|---|---|
| Main (change applied) | working tree | `2 failed, 7 passed` — FAILs: `TestRealSaverWritePruneResume::test_real_saver_write_retention_prune_blob_prune_resume` (:317) + `TestRealSaverDryRunReport::test_real_saver_dry_run_report_line_shape` (:948), both `assert summary_lines` → `assert []` |
| **Base A/B proof** | detached worktree `/tmp/ens-base-retention-0f9bda12` @ `0f9bda12`, disposable PG :15433, identical invocation | **identical `2 failed, 7 passed`** → **PRE-EXISTING** |

**Root cause (base-identical, test-side)**: the two tests' `caplog` targets `logger="daemon.checkpoint_perf"` (:304/:938) but the per-sweep dry-run summary is emitted on the `daemon.services.checkpoint_prune` logger (`checkpoint_prune.py:63` logger, `:267-279` emission) — sibling logger, not parent → `summary_lines` empty by construction. Passing sibling at :563 uses the correct logger. **Retention-independence proven**: both failing tests call the adapter directly with hardcoded N=3/N=1 and never touch `CheckpointCleanupJob`/`_prune_per_thread_checkpoints`/config — zero causal reach from this diff. Both tests **QUARANTINED** (QUARANTINE.md 2026-09-26 row, base-attributed). Effective gate result for this change: **7/7 relevant PASS**. Fix (out of scope, test-debt): 2-char test edit of the two caplog logger names.

## §4 Regression sweep (time-boxed, census-driven)

- Import canary: `timeout 60 uv run python -c "import daemon.manager"` → exit 0.
- Whole-tree census (contract-change protocol, constant removal = enumeration change): `grep -rn "CHECKPOINT_MAX_PER_THREAD" daemon/ tests/ scripts/ docs/` → 11 hits, ALL benign (env-string/comments/test-env); **zero stale `from daemon.constants import` anywhere**; lowercase field 21 hits, all in the diff's own files. Dead-code ensure.md gate (nice-to-have): **PASS**.
- Broad unit sweep: `tests/test_config.py tests/unit/test_constants.py tests/unit` = 13,451 collected → full run TIMEOUT (exit 124 @ 52%, 300s cap) → **sanctioned shard split**: Shard A (entries 1–172 + test_config, 6,426) exit 1: `45 failed, 6358 passed, 2 skipped, 21 errors in 247.35s`; Shard B (entries 173–344, 7,031) exit 1: `23 failed, 6950 passed, 56 skipped, 2 errors in 168.34s`.
- **Adjudication: NON-REGRESSION.** All 23 errors = documented MCP-area baseline (17 `test_builtin_mcp_servers.py` `service_tool` AttributeError + 4 context7 + 2 webfetch ≈ blueprint's known 21+2). All 68 fails match already-quarantined/base-attributed drift families (2026-09-14 consolidated row: find_near ×13, coder_developer_migration ×6, api size 2849>1600 ×1, allowed_models coding2 ×2, release-tag pin ×1, phase4 ×1, terminal_reason ×1, validate_agent_id ×1, vision ×1, wanderer ×2, …) or ship-pending-activation classes (watch_job mission gate ×2, archive_lifecycle ×5, pause originator kwarg ×5). **None reference the changed symbol**; the changed-symbol probe file ran inside Shard A and passed. New-ish unproven debt (flagged, NOT quarantined, out of retention scope): frozen-tool-name `service` ×1, tool_config_validation source-mode warnings ×1.

## §5 ensure.md status (scoped)

- Critical "no regressions in changed packs": **PASS** (§1, §2, §3 effective, §4 non-regression).
- Critical concurrency pack: **OUT OF BLAST RADIUS** (change touches no lock/async-wrapping semantics — config field read + constant removal; maintenance Op D adapter calls unchanged).
- Critical `dev.sh --timeout-graceful-shutdown 10` static check: **PASS** (dev.sh:99,102).
- Nice-to-have dead-code check: **PASS** (zero remaining refs to removed constant).
- Release Gate: **NOT RUN — not warranted** (3-file scoped config change, no architecture impact).

## §6 Evidence appendix

Worker instances: preflight `d308d058`, maintenance `82d075e1`, wiring pin `f323a408`, −k filter `00f15571`, probes `7192baa8`, sweep `5c1a3967`, PG real-saver `a0ab73b3`, base-proof `7623e0a1`. All commands + exit codes in §1–§4 and in the worker reports (delivered to the tester transcript). All packs dual-layer timeout (`timeout 300` outer; pyproject per-test 30s / overridden 120s for real-PG leg, documented). No `-x` anywhere.

## §7 Findings & follow-ups for the author

1. 🟢 **Ship**: change is validated; commit at will. Probe file `tests/unit/test_checkpoint_retention_config_probes.py` left UNTRACKED — include it in the commit or tell me to drop it.
2. 🟠 **Hardcoded literal-3 pins** (stale-contract risk, not a current failure): `tests/test_maintenance.py:~1013, ~1135` assert `assert_awaited_once_with(3)` — will silently break if the default ever changes. Suggest `PersistenceConfig().checkpoint_max_per_thread` in the pins.
3. 🟠 **Test debt (pre-existing, quarantined)**: caplog logger-name mismatch in the two real-saver dry-run tests (§3) — 2-char test edit; un-quarantine requires 3× clean re-run after fix.
4. 🟢 Author's async env test could assert all four adapter calls (P5) — probe file already covers it.

## §8 Skip list (explicit)

| Skipped | Reason |
|---|---|
| Full `tests/` tree (23,443 collected) | blast radius: 3 source files, config surface; census-driven matched-suite protocol + broad `tests/unit` sweep ran instead |
| Release Gate E2E (`tests/e2e/…`) | not warranted — scoped non-architecture change; also requires live daemon |
| `tests/postgres/` directory suites | out of blast radius; the PG binding gate for the touched surface (real_saver) DID run |
| FE Jest / FE packs | no frontend change |
| `concurrency_atomic_unit_test` pack | out of blast radius (no lock/async-wrapping semantics touched) |
