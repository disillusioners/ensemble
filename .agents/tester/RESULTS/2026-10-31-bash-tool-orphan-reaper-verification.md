# RESULTS — bash-tool orphan reaper functional verification

Worktree: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-bash-tool-orphan-reaper`
Branch: `feature/bash-tool-orphan-reaper` @ `349e526a6` (fix `2b3a7cd99` on `b585503a5`, base `cbafcc8d9`)
Date: 2026-10-31
Gate: functional verification (implementation already passed adversarial review)

## VERDICT: NO-SHIP — one branch-caused red (test-stub signature); everything else classified green

A single 2-line test-stub fix (`tests/tools/test_bash_registry.py` local `cleanup_instance()` stub must accept `reaped_by`), then re-run P2 → SHIP.

## Pack results (dual-layer timeout; `-rs` everywhere; runner = `scripts/run_tests_scrubbed.sh`)

| Pack | Scope | Result | Counts (verbatim) |
|---|---|---|---|
| bash_spawn_reaper_feature_test | 4 new files (repo 19 + reaper 133 + boot 14 + stamp 9) | ✅ PASS | `175 passed in 4.66s` |
| bash_tool_family_unit_test | tests/tools/test_bash*.py (5 files) | ❌ FAIL (1 NEW, BRANCH-CAUSED) | `1 failed, 45 passed in 3.83s` — test_bash_registry.py:176 TestCleanupAll::test_cleanup_all_isolates_per_instance_failures (`assert 0 == 2`; TypeError: stub `cleanup_instance()` lacks `reaped_by` kwarg) |
| reaper_touched_families_integration_test | 5 edited families | ❌ known-red only | `1 failed, 177 passed` — TestScenarioFNohupGrandchild::test_nohup_grandchild_killed_by_killpg (PRE-ADJUDICATED pre-existing, A/B/A at base cbafcc8d9); 0 skipped |
| reaper_nmatrix_support_unit_test | test_tools.py 38 + test_chart_tools.py 43; kill_site exemption 12 @ `--override-ini="addopts=" -m integration` | ✅ PASS | section1 `81 passed`; section2 `11 passed, 1 skipped` — skip = **K12 Windows-only** (bash.py:172/:183) |
| bash_spawn_repository_pg_test | tests/postgres/test_bash_spawn_repository_pg.py (5) | ✅ PASS | `5 passed in 1.15s` on scratch `ensemble_test`; scrub-gate rc=0; no prod/dev target |

Union: 497 nodes = **494 passed / 2 failed / 1 skipped**. (Developer claim "494 passed / 1 failed / 1 skipped": passed count reconciles exactly; the extra failure is the family member the developer's scope missed — `tests/tools/test_bash_registry.py`, outside the diff but inside the bash family.)

## F-1 — reviewer-routed skip identification (ADJUDICATED)

`tests/integration/test_service_tool_kill_site_exemption.py:567` — **K12 is Windows-only** (`bash.py:172/:183 — Windows pid branch`). Legitimate platform skip on macOS/darwin; no action. Matches the developer's "1 skipped".

## Mock-theater audit (TrueAuto rule) — pins REAL, 3/3 mutations RED-proven

Audited (verbatim assertions captured in dispatch evidence): C1 tid zero-env-read pins (spy on the real module leaf seam `verify_member_env`), C2 absolute-veto inversion (negative outcome + zero-signal fence), M3 stamp-tolerance knob clamp (direct parse-path), D9 fail-closed None/unparseable start ⇒ refused, OFF zero-read (spy repo call lists + `not_called`), stamp byte-contract (real `asyncio.create_subprocess_shell` env + real `ps eww` read-back in boot pack), TERM→grace→KILL ladder order (census instrument + real-process legs).

Mutation legs (scratch clone, import-proof `/private/tmp/reaper-mut-audit/daemon/__init__.py`; killed = exit EXACTLY 1 + FAILED summary):

| Leg | Mutation | Baseline | Mutated | Reverted |
|---|---|---|---|---|
| D9 fail-closed | `unparseable=True` → `member_ok=True` (bash_spawn_reaper.py:908) | 0 (8 passed) | **1 (1 failed)** FAILED test_none_start_member_refuses_fail_closed; `UNEXPECTED KILLPG: os.killpg(999001, 15)` | 0 (8 passed) |
| OFF zero-read | OFF early-return block removed (:1361-1366) | 0 (5 passed) | **1 (1 failed)** FAILED test_off_shutdown_lane_never_consults_repo (`_open_all_calls` 0→1) | 0 (5 passed) |
| C2 veto | foreign-member refusal → `pass` (:999) | 0 (2 passed) | **1 (2 failed)** both veto nodes | 0 (2 passed) |

Coverage note (🟢, not blocking): the D9 **start-tolerance** knob (`start_tolerance_seconds_from_env`) has no direct absent/garbage/clamp parse pins (behavioral `test_tolerance_env_respected` only); the **stamp**-tolerance knob IS parse-pinned. Suggest a 4-line companion pin.

## Live-behavior leg (real process, actual bash tool path)

Harness `/tmp/reaper-live/harness.py` (self-timeout 240s; outer 300s). **LEG1 15/15 PASS, LEG2 11/11 PASS.**

- LEG1 (master ON, default): tool wall 0.03s exit 0; burner pid 43015 alive, pgid 43014; `ps eww` child env carries `ENSEMBLE_BASH_TRACKING_ID=21b58ef7d0624029a880a9a1125a1104` == row.tracking_id; exactly one OPEN row (pid=43014, pgid=43014, boot_id=514c6cd714f54f3c92598c3723f8a12f, daemon_pid=42999, reaped_at IS NULL); RAM bucket empty post-tool ⇒ durable tier does the kill (Path A); `cleanup_instance` → burner dead in 0.00s, row reaped `reaped_at=2026-10-31T19:38:38.516653+00:00`, `reaped_by='terminal-tier@42999'`.
- LEG2 (`ENSEMBLE_BASH_ORPHAN_REAPER=0`): switch parses OFF; tool still succeeds; child env carries NO stamp; scratch.db has ZERO rows; `cleanup_instance` no-op (burner alive); repo spy reads=[] writes=[].
- Gap (explicit, not faked): full-app-boot HTTP leg NOT run (out of scope/constraint); boot-reconcile (`reconcile_stale_bash_spawns`) and shutdown sweep lanes covered by `test_bash_spawn_reaper_boot.py` / `test_manager_shutdown.py` unit legs, not the live harness.

## Classified red ledger

| Red | Classification | Evidence |
|---|---|---|
| TestScenarioFNohupGrandchild::test_nohup_grandchild_killed_by_killpg | **PRE-EXISTING** (pre-adjudicated A/B/A at base `cbafcc8d9`) | brief; reproduced identically (PA/177P) in P3 |
| tests/tools/test_bash_registry.py:176 TestCleanupAll::test_cleanup_all_isolates_per_instance_failures | **BRANCH-CAUSED** | base clone @ cbafcc8d9: `15 passed`, node PASS; HEAD clone: `1 failed` `assert 0 == 2`; test file byte-identical both legs (sha256 `8c5f9198…`); bash.py base `2227d26c…` vs HEAD `27cfb080…`; `reaped_by` grep absent at base, present HEAD (:500/:519/:550/:568/:597); import-proof both legs (`/private/tmp/reaper-base-leg/daemon/__init__.py`, `/private/tmp/reaper-head-leg/daemon/__init__.py`) |

Fix (owner = developer, separate from this gate): add `reaped_by: str = "terminal-tier"` (or `**kwargs`) to the test's local stub `cleanup_instance`; re-run P2 → expected 46/46. Do NOT fix on this gate.

## Artifacts

- Pack scripts: `test/packs/bash_spawn_reaper_feature_test.sh`, `bash_tool_family_unit_test.sh`, `reaper_touched_families_integration_test.sh`, `reaper_nmatrix_support_unit_test.sh`, `bash_spawn_repository_pg_test.sh` (all chmod +x, `bash -n` clean, dual-layer timeout, RESULT trap).
- PACKS.md: rows registered in the commission block above.
- Live harness: `/tmp/reaper-live/harness.py` (ephemeral, /tmp).
- Reviewed tree: source+test files byte-identical to HEAD (gate made no edits); only additions = 5 pack scripts + this RESULTS doc (+ PACKS.md rows). Left uncommitted intentionally — giter owns the branch (mission Phase B merge pending); porcelain delta documented here.

---

## CONFIRMATION ROUND — HEAD `5f89691a1` (2026-10-31, second pass)

New HEAD: `5f89691a1` (`fix(daemon): orphan reaper tidy round (fail-safe env-verify)`, 1 commit on top of `349e526a6`; 9 files, +493/−357). All runs re-executed on the new HEAD; every runner self-guarded on `git rev-parse --short HEAD == 5f89691a1` before running.

### Round-2 pack results

| Pack | Result | Counts (verbatim) |
|---|---|---|
| P1 `bash_spawn_reaper_feature_test` | ✅ PASS | `177 passed in 4.72s` (was 175; +2 new E pins), 0 skip |
| P2 `bash_tool_family_unit_test` | ✅ **PASS — previous red FIXED** | `46 passed in 3.69s` (was 1F/45P) |
| P3 `reaper_touched_families_integration_test` | ❌ known-red only | `1 failed, 177 passed` — same node `TestScenarioFNohupGrandchild::test_nohup_grandchild_killed_by_killpg` (line moved 599→746 by the harness extraction; same pre-existing class), 0 skip |
| P4 `reaper_nmatrix_support_unit_test` | ✅ PASS | section1 `81 passed`; section2 `11 passed, 1 skipped` — K12 Windows-only (F-1 unchanged) |
| P5 `bash_spawn_repository_pg_test` | ✅ PASS | `5 passed in 1.05s` on scratch `ensemble_test` |

Round-2 invocation totals: **497 passed / 1 failed (pre-existing nohup) / 1 skipped (K12)** across 499 pack invocations — zero new reds.

### Red ledger reclassified for `5f89691a1`

| Red | Classification | Evidence |
|---|---|---|
| `TestCleanupAll::test_cleanup_all_isolates_per_instance_failures` (previously BRANCH-CAUSED) | ✅ **FIXED** | stub now `instance_id: str, *, reaped_by: str = "terminal-tier"` + forwards (`tests/tools/test_bash_registry.py:164/:171`); P2 46/46 on `5f89691a1` |
| `TestScenarioFNohupGrandchild::test_nohup_grandchild_killed_by_killpg` | PRE-EXISTING (unchanged) | same node/class; error shape preserved (`assert 'inst-f-…' in {}`); line shift only |
| K12 Windows-only skip | LEGITIMATE PLATFORM SKIP (unchanged) | `platform.system() != "Windows"` single skipif marker at `:568` |

### Behavior change E — independently verified fail-safe + mutation-proven

- Row-level site: `elif env_verify_error is not None: return ("refused", "refused:env-verify-error")` — returns BEFORE any kill path.
- Enumeration site: unexpected raise ⇒ candidate dropped (`continue`), log token `outcome=refused:env-verify-error — never kill-unverified`.
- Row-level pin `test_env_verify_raise_refused_fail_safe` includes the **zero-signal fence** (`assert all(sig == 0 …)`); mutation (refuse→kill-permitted polarity) ⇒ **EXIT 1**, `UNEXPECTED KILLPG: os.killpg(777080, 15)`, reverted GREEN.
- Enumeration pin `test_env_verify_raise_skips_candidate_fail_safe` asserts `out == []` + log text (no kill path exists to fence — appropriately scoped); mutation (skip→include) ⇒ **EXIT 1**, `assert [(5031, 'aaaa…')] == []`, reverted GREEN.
- Scratch clone import-proof: `/private/tmp/reaper-e-mut/daemon/__init__.py`; full-file GREEN control after reverts: `135 passed in 1.34s`; reviewed `daemon/`+`tests/` porcelain empty.

### Verdict — confirmation round

**SHIP.** All feature pins green (177/177), the sole branch-caused red is fixed and re-proven (46/46), the only remaining red is the pre-adjudicated pre-existing nohup node, the only skip is the K12 Windows-only platform skip, the E behavior change is genuinely fail-safe and both new pins are mutation-real.
