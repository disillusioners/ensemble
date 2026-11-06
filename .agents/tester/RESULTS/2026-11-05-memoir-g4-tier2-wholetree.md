# G4 Tier-2 Whole-Tree Gate — Memoir Store v1 Phase C

**Status:** FAIL (3 deterministic net-new reds)
**Gate:** Tier-2 whole-tree dual-side (ensure.md § Gate Tiers; core seam touched: daemon/manager.py + daemon/api.py)
**Lane:** feature/ari-memoir-store, HEAD 6a99391eb (base 1f6898ae2889ff002b4656486f6be3ee1048b649)
**Executor:** replacement worker (re-dispatch #1; predecessor 5cb2cb97 wedged 04:27Z, terminated ~06:2xZ)
**Date:** 2026-11-06
**Wall clock:** ~04:30 → 07:00 UTC (~2h30m; 90-min advisory BREACHED — reported loudly, gate not stopped)

---

## Step 0 — Predecessor residue triage

- **(a) HEAD worktree:** `/home/nea/ensemble-src-wt-memoir-store` @ `6a99391eb` ✓ (expected). Porcelain: **1 modified file** — `.agents/shared/planning/designer-agent/install-audit.jsonl` (+32 lines, mtime 05:17Z). **Adjudicated as foreign live-runtime residue, NOT predecessor work** (mtime postdates predecessor's 04:27Z wedge; content = `kms_issue` events from the live designer-agent runtime; this file is the thrice-rescued foreign WIP file per project history). Rescue copy: `/tmp/designer-install-audit-rescue-g4-20261106.jsonl` (sha256 `1df147a5c23eedfbf1ef3d3f07d47df5590f064ea8665abe1e58d3bc1fc21fd2`).
- **(b) Base worktree:** `/home/nea/ensemble-src-wt-memoir-g4base` @ `1f6898ae2` ✓ (clean detached checkout, already provisioned by the predecessor at 04:30 with `.venv` and a scratch PG). Reused (no re-provisioning). Porcelain: same foreign install-audit.jsonl modification (rescued; see above).
- **(c) Orphaned processes:** one scratch PG `postgres -D /tmp/memoir-g4-gate/pg/data` on port 11666 (data dir under `/tmp`, port in 10000–19999, owner `nea`, started 05:51 by predecessor's background chain). **Verified safe → stopped gracefully** (`pg_ctl -m fast`); port freed; system PG (pid 1073, `/var/lib/postgresql/16/main`) and ensemble-prod (9797) untouched. No stray pytest processes.
- **(d) Stale /tmp/memoir-g4-*:** `/tmp/memoir-g4-gate` (8.3 MB, predecessor's partial run) **reclaimed** and used as the evidence root for this gate. `/tmp/memoir-g4-gate/pg/data` removed at teardown.

**Predecessor artifact status (reclaimed):** the predecessor did NOT "zero corpus work" — it completed both collect legs, both 11-shard sweep legs (04:47→05:48), the PG partition both sides (06:04→06:15), the diff extraction (06:16→06:19), and 45 rows of a 3× matrix (06:19→06:24, mid-node when killed). All artifact sets were independently re-derived by this worker from the raw shard logs (see below) and matched byte-for-byte.

---

## Recipes loaded (mandatory, deterministic path)

- `.agents/tester/skills/whole-tree-base-proven-diff/whole-tree-base-proven-diff.md` (v1.0.1)
- `.agents/tester/skills/regression-ab-partition-run/regression-ab-partition-run.md`
- `.agents/tester/skills/ab-base-leg-provisioning-artifacts/*.md`
- `.agents/tester/skills/failed-digest-equality-gate/*.md`
- `.agents/tester/rules/ensure.md` § Gate Tiers (Tier 2)

## Fence verification (house canon, 4-case matrix + import proof + venv gate)

- `run_shard.sh --selftest` → **5/5 PASS** (cases A–D + LLM fence vars E), log `/tmp/memoir-g4-gate/logs/selftest_g4_rerun.log`.
- Import proof head: `.venv/bin/python -c "import daemon; print(daemon.__file__)"` → `/home/nea/ensemble-src-wt-memoir-store/daemon/__init__.py` ✓
- Import proof base: → `/home/nea/ensemble-src-wt-memoir-g4base/daemon/__init__.py` ✓
- Venv gate from neutral cwd (`/tmp`): resolves into the TIP worktree ✓
- Shard-mode fence (L1 scrub per-variable + L2 in-child fence + LLM dead-port fence) baked into `run_shard.sh` (written once, invoked by both legs) ✓

## Corpus definition (verbatim commands)

**Collect (pre-exclusion registry baked in):**
```
timeout 180 .venv/bin/python -m pytest --collect-only -q tests/ \
  --ignore=tests/packs \
  --ignore=tests/e2e/test_context_injection_hybrid.py
```
- Slicing proof: head 33,617 collected / 1,182 deselected (of 34,799); base 33,312 / 1,145 (of 34,457).
- Node list built with `grep '::'` (not `grep '^tests/'` — avoids pytest `SyntaxWarning` headers).
- `--ignore=tests/packs` (g7 sys.exit trap); `--ignore=tests/e2e/test_context_injection_hybrid.py` (module-import HTTP GET to :8079).

**Sweep shards (11 per side, file-boundary, mrpr sizing, `timeout 1750`):**
```
env (per-variable scrub) + LLM fence vars, timeout 1750 bash -c '
  <FENCE_BODY>
  mapfile -t NODEIDS < "$SHARD"
  exec .venv/bin/python -m pytest -p no:cacheprovider "${NODEIDS[@]}" -q --tb=line -rEf
'
```
- xdist `-n auto` within each leg (non-PG corpus); PG packs serial.

**PG partition (serial, `--timeout=120`, `timeout 2400`):**
```
PYFILES="tests/postgres tests/job_queue/test_defer_gate_post_settle_window.py \
         tests/test_settings_api.py tests/vfy_pg/test_stored_watches_pg_lane.py"
env POSTGRES_DB=memoir_g4_scratch POSTGRES_USER=ensemble POSTGRES_HOST=127.0.0.1 \
    POSTGRES_PORT=<scratch> ENSEMBLE_SELF_ENV=dev PG_TEST_*=<scratch> \
    OPENAI_BASE_URL=http://127.0.0.1:9/v1 ... PYTHONDONTWRITEBYTECODE=1 \
  timeout 2400 .venv/bin/python -m pytest $PYFILES --override-ini="addopts=" -m postgres \
    --timeout=120 -q --tb=line -rEf -p no:cacheprovider
```
- Scratch PG: 16.15 self-provisioned, `/tmp/memoir-g4-gate/pg/data`, port 11666 (reclaimed, now stopped). Inline POSTGRES_* fence applied in-child; `ensemble_prod` never resolvable; no InstanceManager; no writes to live.

**Pre-exclusions (with compensating pins):**
- `tests/performance/test_message_api_cost.py` (>300s solo) → `regression_performance` pack
- e2e cells requiring live `:8079` daemon → `e2e_workflows_ensure_test` (Release Gate)
- `tests/packs/g7` sys.exit(0) collection trap → packs via registered scripts (PACKS.md)

**Corpus asymmetry (sanctioned, reported):** 15 head-only files (the new memoir test files), 0 base-only. Shard mapping identical after excluding them (all 11 shards). `--collect-only -q` per partition per side on record.

## Accounting closure (prove no node lost)

| side | P | F | E | S | XF | sum | collected | delta |
|------|---|---|---|---|----|-----|-----------|-------|
| head | 32,805 | 454 | 22 | 330 | 6 | **33,617** | 33,617 | 0 |
| base | 32,509 | 445 | 22 | 330 | 6 | **33,312** | 33,312 | 0 |

All 11 shards per side have terminal lines. Closure exact, no node lost.

## FAILED-digest comparison (FORM-tagged, house canon comparator)

Comparator: `.agents/tester/skills/whole-tree-base-proven-diff/sweep_digest_verdict.sh` → exit 1 (NET-NEW ≠ ∅, input to ladder — not a verdict).

| form | base unique | head unique | base-only (fixed) | head-only (net-new) | both |
|------|-------------|-------------|-------------------|---------------------|------|
| BARE-ID (node-only) | 527 | 536 | 6 | 15 | 521 |
| BARE-ID (all captured lines) | 476 | 485 | 7 | 16 | 470 |
| GREP-FULL (raw FAILED\|ERROR lines) | 520 | 531 | — | — | — |

Digests (BARE-ID node-only form):
- base: `028c41dfa7babf3518cda0e3baac0faef4f084d3e4073f2ad2b6dbadb87ada40`
- head: `55cccdfaa55ce1db7897c9c68c6fbc0ff43ff8ce4e96f1559487f85874e567e9`

`+N/−N` fingerprint: **+305 collected nodes / −0**, all 305 attributable to the 15 new memoir test files (no shared test file changed its node count).

Caplog contamination (skill-8 convention 3): the 16th head-only line is a pytest captured-log line (`ERROR    daemon.tools.memoir_knob:memoir_knob.py:163 ...` — 4 spaces) — NOT a node id; excluded from node-level net-new. Same on base: `daemon.manager:manager.py:16164 Error during shutdown step 'dispose_db_pools'` — excluded. FORM-split adjudication applied.

## Per-side matrix (3× isolated SEQUENTIAL re-run, `run_shard.sh --fence-exec`, no xdist)

19 delta nodes (net-new ∪ fixed) × 3 runs × 2 sides = 114 rows. Complete (114/114). Per-node verdicts:

| node | head 3× | base 3× | classification |
|------|---------|---------|----------------|
| `test_lcancheck_family_separation::...::test_stable_id_supported_kinds_are_exactly_four` | 3/3 FAIL | 0/3 FAIL | **DETERMINISTIC NET-NEW RED** |
| `test_tool_category_artifact::...::test_artifact_covers_every_registered_category` | 3/3 FAIL | 0/3 FAIL | **DETERMINISTIC NET-NEW RED** |
| `test_tool_category_artifact::...::test_artifact_is_generated_from_the_tool_registry` | 3/3 FAIL | 0/3 FAIL | **DETERMINISTIC NET-NEW RED** |
| `test_api::test_list_instances_filter_by_project_id` | 3/3 FAIL | 3/3 FAIL | pre-existing (reason drift Assertio→RuntimeE) |
| `test_api::test_list_instances_no_project_id_filter` | 3/3 FAIL | 3/3 FAIL | pre-existing (reason drift) |
| `test_job_feedback_observer::...::test_tier1_sibling_owned_row_skipped` | 1/3 FAIL | 0/3 FAIL | flaky (chunk-context) |
| `test_atomic_dequeue::...::test_dequeue_with_instance_filter_under_concurrency` | 0/3 FAIL | 0/3 FAIL | flaky (chunk-context) |
| `test_atomic_status_transitions::...::test_retry_concurrent_double_call_only_one_increments` | 1/3 FAIL | 0/3 FAIL | flaky (chunk-context) |
| `test_resource_monitor_self_cost::test_attribution_on_holds_p1_budget` | 0/3 FAIL | 0/3 FAIL | flaky (chunk-context) |
| `test_auto_kill_integration::...::test_sibling_alive_row_skipped_by_terminal_lane` | 1/3 FAIL | 1/3 FAIL | flaky (both sides) |
| `test_mcp_tool_timeout::...::test_tool_node_handles_timeout` | 0/3 FAIL | 0/3 FAIL | flaky (chunk-context) |
| `test_plane_sync::...::test_request_includes_authorization_header` | 0/3 FAIL | 0/3 FAIL | flaky (chunk-context) |
| `test_plane_sync::...::test_never_logs_api_key` | 0/3 FAIL | 0/3 FAIL | flaky (chunk-context) |
| `test_archive_lifecycle::...::test_archive_old_memories_moves_old_files` | 0/3 FAIL | 0/3 FAIL | flaky (chunk-context) |
| `test_archive_lifecycle::...::test_archive_old_memories_returns_count` | 0/3 FAIL | 0/3 FAIL | flaky (chunk-context) |
| `test_skill_evolution_service::...::test_ab_resolution_threshold_met` | 0/3 FAIL | 0/3 FAIL | flaky (chunk-context) |
| `test_chart_tools_reuse_integration::...::test_error_charter_revives_then_respawns_on_second_failure` | 1/3 FAIL | 0/3 FAIL | flaky (chunk-context) |
| `test_manager_shutdown::...::test_durable_sweep_sibling_alive_skip_and_dead_writer_swept` | 1/3 FAIL | 0/3 FAIL | flaky (chunk-context) |
| `test_post_restart_arm_notify_drill_smoke::test_drill_smoke` | 0/3 FAIL | 0/3 FAIL | flaky (chunk-context) |

**Genuine fixed (base 3/3 FAIL + head 3/3 PASS): 0.** All 6 line-level "fixed" entries are chunk-context flakes (matrix 0/3 or mixed); no pre-existing red was genuinely fixed by the lane.

## Inner-soul 16 adjudication (known expectation)

16 pre-existing `test_memory_integration.py` ids present in **both** failed sets with byte-identical sorted lists and identical digest `9fea2dcbd506a5139eaae14091a872a7f4f000dde171aa74b09e59f7cbec2e52`. Zero net-new. ✓ (Plus 4 `test_inner_soul_compaction.py` ids, also both sides.)

## Verdict

**FAIL** — 3 deterministic net-new reds (lcancheck exactly_four + tool_category_artifact ×2).

Cause: the branch added the `memoir` stable-id kind and the `memoir` tool category without updating the two companion surfaces house doctrine requires in-commit:
1. `tests/integration/test_lcancheck_family_separation.py` — `_STABLE_ID_SUPPORTED_KINDS` pin + `test_stable_id_supported_kinds_are_exactly_four` (production docstring at `daemon/services/context_messages.py` already documents the fifth kind). Missed companion-pin update (F-1 trap class).
2. `.agents/shared/metrics/tool-categories.json` — generated artifact not regenerated for the new `memoir` category (`daemon/tools/_tool_registry.py:283+` registers `memoir_write`/`memoir_pin`/… under category `memoir`); artifact has 0 `memoir` mentions. Missed artifact regeneration (F-1 trap class).

Both are production/lane-side misses, not test-side quick-fixes (one is a contract pin, the other a generated canonical artifact). Per the gate spec: "production-side ⇒ FAIL, evidence, no fixes." No quick-fix applied by this gate worker.

## Fix shapes for the lane (not applied by this gate)

- **lcancheck:** add `"memoir"` to `_STABLE_ID_SUPPORTED_KINDS`, rename the test to `..._exactly_five`, update the module docstring ("four surviving kinds" → "five"). ~10 lines, test-side — but a CONTRACT change that requires the lane/leader to confirm the fifth kind is intended (production already documents it; the pin is the gate). Confirm against `daemon/services/context_messages.py` CONTEXT_KIND_MEMOIR registration.
- **tool_category_artifact:** run the house generator (never hand-edit) over `daemon/tools/_tool_registry.py` to regenerate `.agents/shared/metrics/tool-categories.json` including `memoir`. Then re-run `tests/unit/tools/test_tool_category_artifact.py` (2 nodes) — must go GREEN. Also make that test a D1 leg for category registrations per the lane's routed followup.
- After fixes: re-run this gate (or at minimum the 3 affected nodes) and recompute the digest to net-new=∅ with `+3/−3` fingerprint.

## Wall clock per leg

| leg | start | end | duration | notes |
|-----|-------|-----|----------|-------|
| head collect | 04:36 | 04:40 | 4.0 min | 33,617 nodes |
| base collect | 04:40 | 04:41 | 1.0 min | 33,312 nodes |
| head sweep (shards 0–10) | 04:47 | 05:17 | 30.0 min | xdist `-n auto` |
| base sweep (shards 0–10) | 05:24 | 05:48 | 24.0 min | xdist `-n auto` |
| head PG partition | 06:04 | 06:11 | 7.0 min | serial, `--timeout=120` |
| base PG partition | 06:04 | 06:15 | 11.0 min | serial, `--timeout=120` |
| diff extraction + inner_soul | 06:16 | 06:19 | 3.0 min | — |
| 3× matrix (resume) | 06:25 | 07:00 | 35.0 min | sequential, no xdist |
| **total** | | | **~115 min** | 90-min advisory breached (~25 min) — reported loudly |

## Fence confirmations

- `ensemble_prod` / `10.44.0.2` / `live` never present in any child env (L1 scrub + L2 fence); PG runs used `memoir_g4_scratch` only (fail-closed guard verified — a test attempting `PG_TEST_DB='ensemble_prod'` was refused with RuntimeError on both legs, identical).
- No writes to `/home/nea/agents-ensemble/**` (live v0.28.13) or `/home/nea/ensemble-src/**`.
- No stage/promote/rollback/nonce actions; no kills on 9797/8079/8088; system PG untouched.
- Scratch PG stopped + data dir removed at teardown.

## Commits

- `test(memoir): G4 tier-2 whole-tree gate + registration` — path-scoped over `.agents/tester/RESULTS/2026-11-05-memoir-g4-tier2-wholetree.md` + `.agents/tester/PACKS.md` (see below). Commit hash: <filled after commit>.

## Final tree state

- HEAD worktree: `feature/ari-memoir-store` @ `6a99391eb` (unchanged; foreign install-audit.jsonl restored via `git checkout --`).
- Base worktree: `/home/nea/ensemble-src-wt-memoir-g4base` **removed** (`git worktree remove --force` + `git worktree prune`); verified absent from `git worktree list`.
- `/tmp/memoir-g4-gate` retained until this doc is committed; removed at teardown.
- Evidence preserved at `/tmp/designer-install-audit-rescue-g4-20261106.jsonl`.

## PACKS.md registration

`memoir_gate_tier2` registered (keep-both, newest-first) with per-partition outer timeouts documented (5-min cap class for individual packs; the gate-level 90-min advisory is the tier budget per ensure.md § Gate Tiers — disclosed as a tier-2 pack override class).
EOF
echo "RESULTS doc written"; wc -l .agents/tester/RESULTS/2026-11-05-memoir-g4-tier2-wholetree.md