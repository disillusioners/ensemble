# RESULTS — G2b: metrics-pins existence + regression neighbors (rogue-containment-p2, Gate 2 close-out)

- **Date:** 2026-11-15 (UTC)
- **Executor:** worker (test-pack-execution skill, single-pack contract, 3 scoped legs)
- **Commission:** settle the LITERAL reading of "the +39 metrics pins claimed untouched-green" + run the two named regression neighbors (bind_retry, test_main_entry)
- **Venue:** `/home/nea/ensemble-src-wt-rogue-contain`, detached HEAD `a7aec858204d33f5ef5247f9bab9e3cc936b63e3` (reconstruction tree; 4 foreign dirty files present — untouched, uncommitted, unswept)
- **Lane diff under test:** `bf4bfde6e..a7aec8582`

## VERDICT

**ALL 3 LEGS PASS** — 311 nodes total, 0 failures, 0 timeouts, 0 collection errors.
Runtime: 7.72s + 14.11s + 5.16s pytest (44s wall incl. interpreter startup), `timeout 120` outer wrapper per leg, never approached.

## KEY DELIVERABLE — metrics-pin existence answer (BOTH readings settled)

### Reading 1 — LITERAL: the pre-existing metrics pin family EXISTS and is GREEN

Exactly one test file contains `derive_metrics_port` (primary locator): `tests/unit/test_metrics_port_derive.py`.
The completeness fallback locator (`metrics_listener|9798` in tests/unit) surfaced 8 more pre-existing metrics-family pin files.
All 8 are **pre-existing at lane base `bf4bfde6e`, byte-identical base→HEAD, 0 lane commits** — genuinely "untouched":

| File | Nodes | Lane commits | At base | Result |
|---|---|---|---|---|
| tests/unit/test_metrics_port_derive.py | 9 | 0 | yes | PASS |
| tests/unit/metrics/test_central_metrics_contract_doc.py | 21 | 0 | yes | PASS |
| tests/unit/metrics/test_metrics_query_api_wiring.py | 24 | 0 | yes | PASS |
| tests/unit/services/test_metrics_listener.py | 41 | 0 | yes | PASS |
| tests/unit/services/test_metrics_api_wiring.py | 12 | 0 | yes | PASS |
| tests/unit/services/test_metrics_listener_registry.py | 15 | 0 | yes | PASS |
| tests/unit/services/test_metrics_managed_keys.py | 56 | 0 | yes | PASS |
| tests/unit/services/test_metrics_refresh_path.py | 12 | 0 | yes | PASS |
| **TOTAL** | **190** | — | — | **190 passed in 7.72s** |

Material wrinkle: the lane DOES touch `daemon/services/metrics_listener.py` (commit `d394fcbd4`, **+4 lines, docstring-only** — "declares rogue-port gate MAIN-PORT-ONLY (no discovery rider)"). No behavior delta; the untouched pins passing at lane HEAD confirm the touched source still derives the port correctly. (The commission census claim "ZERO metrics pin files in the lane's diff" holds for pin/test FILES; the source-file docstring touch is disclosed here for completeness.)

### Reading 2 — ARITHMETIC: "+39" = the lane's NEW port-gate pins — CONFIRMED EXACTLY

Added `def test_` lines in the lane diff (`git diff bf4bfde6e..a7aec8582`), per file:

```
13  tests/unit/test_port_ownership_pins.py
11  tests/unit/test_port_holder_identity.py
 8  tests/unit/test_prod_dsn_fence.py
 7  tests/job_queue/test_stgh_sweep_truth_pins.py
 4  tests/unit/test_log_hygiene.py
 3  tests/unit/test_pkill_overlay_pins.py
 3  tests/unit/test_pkill_overlay_guard.py
 3  tests/test_run_tests_scrubbed_selfenv_gate.py
 0  tests/test_api.py
```

**13 + 11 + 8 + 7 = 39** — the commission census decomposition reproduced exactly. The "+39" wording maps to the lane's NEWLY ADDED test functions in the four largest contributors, NOT to a pre-existing metrics file. (Lane total: 60 added test defs across 12 test files.) The census conclusion stands, AND the literal reading is independently satisfied by the 8-file untouched metrics family above — the two readings are no longer in tension: both are true, they describe different file sets.

## LEG 2 — bind_retry regression neighbors: PASS

Locator `grep -rl "bind_retry\|BIND_RETRY" tests/ --include='*.py' | head` → 5 hits. `tests/test_main_entry.py` (a hit) is deferred to its dedicated LEG 3; the other 4 ran as one invocation:

| File | Nodes | Lane commits | Result |
|---|---|---|---|
| tests/test_bind_retry.py | 26 | 0 (untouched) | PASS |
| tests/unit/test_port_ownership_pins.py | 31 | 4 (lane-touched) | PASS |
| tests/unit/test_db_resolution_guard.py | 18 | 1 (lane-touched) | PASS |
| tests/unit/test_llm_streaming_wire_verify.py | 17 | 0 (untouched) | PASS |
| **TOTAL** | **92** | — | **92 passed in 14.11s** |

The named bind_retry regression file (`tests/test_bind_retry.py`, untouched by the lane) is green, and the two lane-touched neighbor pin files (port-ownership, db-resolution) are green at lane HEAD.

## LEG 3 — tests/test_main_entry.py: PASS (29 nodes; "expect 27" arithmetic explained)

- `timeout 120 env <scrub> .venv/bin/pytest tests/test_main_entry.py -q --tb=short -p no:randomly` → **rc=0, 29 passed, 1 warning, 5.16s** (11s wall).
- The "expect 27" figure was a test-FUNCTION count, pytest counts parametrized NODES: the file has **27 `def test_` functions** (verified identical at base and HEAD — the file is byte-identical base→HEAD, 0 lane commits, so the delta is NOT lane-caused), and one parametrize: `test_boot_preflight_pg_auth_refused_exits_78` × SQLSTATEs `["28P01", "28000", "28P02"]` (tests/test_main_entry.py:253) → 27 − 1 + 3 = **29 nodes**.
- All 27 functions green; expectation arithmetic fully accounted for.

## TOTALS

| Leg | Nodes | Result | pytest time |
|---|---|---|---|
| LEG 1 metrics pins (8 files) | 190 | PASS | 7.72s |
| LEG 2 bind_retry neighbors (4 files) | 92 | PASS | 14.11s |
| LEG 3 test_main_entry.py | 29 | PASS | 5.16s |
| **TOTAL** | **311** | **3/3 PASS** | **27.0s** |

## Execution shape / fence notes

- Single-pack contract honored: three scoped invocations, explicit file lists only; no bare pytest, no `pytest tests/`, nothing outside the located files.
- Dual-layer timeout: outer `timeout 120` per leg + repo pytest-timeout (30s/thread per pyproject) inner.
- Env scrub applied **per-variable** (the `env -u POSTGRES_*` glob idiom is a silent no-op): `-u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL -u PGHOST -u PGPORT -u PGUSER -u PGPASSWORD`. Ambient PG-family was in fact empty in this shell.
- ENSEMBLE_AUTH_* trap: **7 ambient `ENSEMBLE_AUTH_*` vars were present** (EXTERNAL_REDIRECT_BASE, ISSUER, DISCOVERY_URL, CLIENT_ID, CLIENT_SECRET, ALLOWED_ORIGINS, ENABLED). No auth scrub was applied (commission: only on collection issue) and **no collection error occurred** — the trap did not fire for these files. Had it fired, the FULL 7-var family scrub would be mandatory (partial scrub breaks test_api collection — known trap).
- No commits, no branch mutations, no pkill, no kills, no ports 9797/9798/7979/8079 contacted, no DB writes (unit-level). The 4 pre-existing foreign dirty files in the venue: untouched. Side effect disclosed: pytest wrote `.pytest_cache/` + `__pycache__/` in the venue (untracked test-run artifacts, inherent to running tests; not committed, not swept).
- Timezone of runs: 2026-11-15T01:18–01:31Z.

## Evidence pack contents (this directory)

- `2026-11-15-rogue-p2-g2b-metrics-pins-regression.md` — this RESULTS file
- `leg1-grep-derive_metrics_port.txt` — primary locator output (1 file)
- `leg1-grep-fallback.txt` — fallback locator output (9 files)
- `leg1-provenance.txt` — untouched proof: lane-diff metrics names, per-file lane-commit counts, base existence, byte-identity, collect count
- `leg1-source-delta.txt` — lane delta on daemon/services/metrics_listener.py (docstring-only, d394fcbd4)
- `leg1-classification.txt` — 8 untouched vs 1 lane-touched (test_pkill_overlay_pins.py)
- `leg1-run.txt` / `leg1-collect-full.txt` / `leg1-perfile-counts.txt` — raw run + node counts
- `leg2-grep.txt` / `leg2-classification.txt` — locator + lane-touch classification
- `leg2-run.txt` / `leg2-collect-full.txt` / `leg2-perfile-counts.txt` — raw run + node counts
- `leg3-run.txt` / `leg3-expectation-check.txt` / `leg3-parametrize-def.txt` / `leg3-node-breakdown.txt` — raw run + 27-vs-29 resolution
- `lane-diff-added-tests.txt` — the 13+11+8+7=39 arithmetic proof + full 12-file lane test-file list

## Ready-to-apply commit command (NOT executed — later lane's decision)

The venue is a detached-HEAD reconstruction tree with foreign dirty files; whether/when to commit this addendum is the finalize lane's call. If recorded per the rogue-cleanup-rider rule (non-commit rider lands a one-line RECORDED addendum in matrix/RESULTS), the sanctioned shape is:

```bash
# 1. Copy this evidence dir's RESULTS file into the venue (path is /tmp; copy BEFORE any /tmp cleanup)
cp /tmp/rogue-p2-g2b-evidence-rescue-20261115T011840Z/2026-11-15-rogue-p2-g2b-metrics-pins-regression.md \
   /home/nea/ensemble-src-wt-rogue-contain/.agents/tester/RESULTS/2026-11-15-rogue-p2-g2b-metrics-pins-regression.md

# 2. Commit EXPLICIT-PATHS ONLY (shared/dirty-worktree safe: --only, never bare git commit)
cd /home/nea/ensemble-src-wt-rogue-contain && \
git add -- .agents/tester/RESULTS/2026-11-15-rogue-p2-g2b-metrics-pins-regression.md && \
git commit --only -m "docs(rogue-containment-p2): G2b metrics-pins existence + regression-neighbors verification — literal reading PROVEN (8 untouched metrics pin files, 190 nodes green); +39 = 13+11+8+7 added port-gate defs CONFIRMED; bind_retry 92 + test_main_entry 29 green" \
  -- .agents/tester/RESULTS/2026-11-15-rogue-p2-g2b-metrics-pins-regression.md
```

Do NOT run this in this lane (commission: no commits). Verify `git status` in the intended tree immediately after any future application.
