# MAINTENANCE-IDLE-STARVATION — Tier-2 Whole-Tree Closure Gate (2026-11-19)

- **Lane**: `feature/maintenance-idle-starvation` · worktree `/home/nea/ensemble-src-wt-maint-starve`
- **Base**: `c36c3e07c` (true pre-fix base, ancestor; 15 lane commits)
- **Gate tip evolution**: `d38fc1093` (branch gate) → `c1d5b00c7` (PACKS.md registration) → `0c759f3f8` (lane-test caplog fix) → this report commit
- **Tier**: **2 — mechanical** (`daemon/manager.py` in-range ⇒ core-seam list; closed with evidence, not justification)
- **HEAD leg ran at `c1d5b00c7`**; the only later delta is the single-file test fix `0c759f3f8` (re-verified 35/35 isolated + 38/38 under the deterministic reproduction recipe, ×3)
- **Verdict**: **PASS — net-new = 0** (comparator raw 13 head-only candidates; all adjudicated pre-existing/xdist-order; 0 genuine lane regressions)

## Headline numbers

| Leg | Nodes | P | F | E | S | xfail |
|---|---|---|---|---|---|---|
| Shard 00 | 3,498 | 3,364 | 72 | 1 | 61 | 0 |
| Shards 01–05 | 17,307 | 16,738 | 359 | 0 | 205 | 5 |
| Shards 06–11 | 17,626 | 17,420 | 105 | 21 | 79 | 1 |
| **Main corpus total** | **38,431** | **37,522** | **536** | **22** | **345** | **6** |
| Leg P1 (migration e2e, own proc) | 4 | 1 | 3 | 0 | 0 | 0 |
| Leg P2 (message_queue e2e, own proc) | 3 | 0 | 3 | 0 | 0 | 0 |
| Leg FP (28 fence-poison files, serial) | 784 | 771 | 6 | 7 | 0 | 0 |

All 12 shards accounting-closed (P+F+E+S(+xfail) == nodes, per shard). Collection: 38,431 selected / 1,243 deselected / 0 collection errors / 1,670 files.

## Base leg (c36c3e07c, git-archive into mktemp, import proven inside base tree)

- HEAD red universe: **577 ids** (deduped; 2 awk-truncated extraction artifacts dropped — canonical forms already present)
- Class D1 (lane-added, base-absent): 1 id — `test_maintenance_idle_starvation.py::TestSkipStreakTelemetry::test_subsequent_skips_log_debug_only` (base-absent by construction; green at HEAD after `0c759f3f8`)
- Base red set: **563 ids** · house-canon `sweep_digest_verdict.sh` raw exit **1** (13 head-only candidates, both forms identical) → per-id adjudication ladder (3× isolation per side) → **net-new = 0**
- Digests: base `c9cddbde…`, head `13697aef…` (differ only by the adjudicated 13)

## The 13 head-only candidates — all adjudicated

| Group | N | Verdict | Evidence |
|---|---|---|---|
| `test_list_instances_source_param` (4), `test_initiative_message_integration` (2), `test_lcau_incident_e2e` (1), `test_long_tool_nudge_graph_smoke` (3), `chart_tools_reuse T1` + `bash_ram_guard` + `ram_guard_events` (3) | 13 | **ORDER/xdist composition artifacts** | green 3/3 BOTH sides in isolation (no xdist) |

## Pre-existing confirmations

| Group | N | Base evidence form |
|---|---|---|
| migration_e2e_comprehensive | 3 | **empirical** — prior base_p1 leg at the SAME `c36c3e07c`, same fence: identical ids + reasons (`'MagicMock' object can't be awaited`) |
| resource_monitor_flusher_bind_pg | 3 | **byte-identity + import-closure** — file/conftest/`resource_monitor`* byte-identical; lane's 4 changed files absent from closure (deliberately NOT re-executed: conftest does drop_all/create_all on `ensemble_test`) |
| p0_gate | 1 | environment — hardcoded macOS path, fails pre-DB; byte-identical |
| nest_report_rest_route git-pin | 1 | GIT_INDEX_FILE emulation — pin references `22c144000` < base; base tree would fail identically |
| test_main_entry.py | 15 | fence artifact (exit 78, `data/ensemble.json` absent in worktree) — red at base IDENTICALLY under matched fence (no `ENSEMBLE_DB_AMBIENT_ALLOW` on either leg) |
| 10 known xdist fabrications | 10 | green at base in isolation |
| repository_claim_lane | 6 | red at base (lives in shard_08, not 06 — attribution corrected) |

## Real findings this gate produced (all disclosed, none blocking)

1. **Lane-test isolation defect — FOUND & FIXED** (`0c759f3f8`, +4/−0, one file): `test_subsequent_skips_log_debug_only` counted DEBUG records with an over-broad filter; `daemon/api.py:75` `install_root_logging()` at import time leaves root+daemon at DEBUG → ambient `Registered maintenance job:` record inflated the count. Deterministic repro recipe (single process, any `daemon.api` importer first) — pre-fix 1F/37P → post-fix 38P ×3. Non-vacuity proven (ambient record still emitted 18×). **A negative `-n 3` run is NOT evidence of no-repro** (xdist masked it).
2. **xdist "worker crashed" = pytest-timeout thread-mode artifact** (POK `f3adc7a3`): 4 shard-06 nodes are deterministic 30 s+ hangs — `compaction.py:115` → `llm_failover.py:605` tenacity backoff 31–36 s > 30 s timeout → `os._exit(1)` kills worker, xdist synthesizes FAILED with misleading reason. Pass serially with `timeout=180` both sides. Pre-existing; file against compaction/llm_failover, not this lane.
3. **`test_hide_kb_instances.py`**: 2 pure xdist fabrications + 3 fail-at-base→pass-at-HEAD flips (lane plausibly improved behavior via `manager.py`; base-only set was empty ⇒ counted as "fixed", not regression).
4. **QUARANTINE.md stale censuses**: row 182 (`×8` vs actual 7 pg_engine nodes), row 135 (`108≠96` now `133≠96`) — lane-innocent doc drift.
5. **Advisories upward**: import-time `install_root_logging()` vs count-based caplog assertions (repo-wide landmine); 2 credential-print incidents from fence-CHECK idioms (`${VAR:-UNSET}` marker probe; `#`-after-`\` continuation swallowing `env -u` args → env dump) — both dev-scoped, contained, self-reported (anomalies `190f5637`, `c62fca89`).

## Environment-limited exclusions (all byte-identical base↔HEAD; blob-sha proven)

- `tests/packs/**` (g7 sys.exit trap) — packs run via registered scripts
- `tests/e2e/test_context_injection_hybrid.py` (import-time HTTP GET :8079) — Release-Gate lane
- `tests/performance/test_message_api_cost.py` — `regression_performance` pack
- P1/P2 polluters — own processes (executed, classified above)
- 28 fence-poison modules — Leg FP (executed serially, 784 nodes, zero truncation)
- `test_metrics_census_pg.py` 7 census nodes — `PG_TEST_DB` exact-name allowlist; no allowlisted scratch DB exists on host; refused fail-closed PRE-connect. **No DDL authorized to mint one** (dispatcher decision: coverage gap disclosed, not closed)

## ensure.md Core (unchanged from branch gate)

`concurrency_atomic_unit_test` PASS (94P/74S/5F — all 5 = QUARANTINE row, node-for-node) · `dev.sh --timeout-graceful-shutdown 10` grep PASS.

## Integrity

HEAD worktree clean before/after every leg · `/home/nea/ensemble-src` never touched · no frozen-binary boot · `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` never unset · no `env -u` globs · names-only env reporting enforced after the two incidents · two tracked-file pollution events restored via `git checkout --` with preserved copies (`p0-gate-evidence.md.polluted.bak`) · base tree removed after use.

## Worker instance ledger

`31bbd57f` preflight · `cb6e808a` async census/Part-B audit · `435f9319`+`f608882b` new packs · `b7f3d672` ensure Core · `03464b28`+`312a8880` scoped regressions · `9f81e036` mutation proof · `022b2561` sha verification · `34acdbef` PACKS commit · `8f4e9658` collection/legs/shard-0 · `2fa7df11` shards 01–05 · `e4db81c6` shards 06–11 · `334e4c46` P1 base + census · `b956b308` exclusion byte-identity · `bc981e99` lane-test triage + fix `0c759f3f8` · `71158ec5` shard-06 crash audit · `5adf9e11` base leg + verdict.
