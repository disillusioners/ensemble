# Fix cycle 1 — the Tier-1 blast-radius gate (net-new = EMPTY)

**Tier:** 1 (the DEFAULT for feature lanes). The diff touches NO core-seam file
(`daemon/graph.py` · `daemon/manager.py` · `daemon/api.py` ·
`daemon/services/instance_messaging.py` · `daemon/services/task_processor.py` ·
`daemon/services/job_queue_service.py`) ⇒ Tier 2 is NOT triggered (the escalation is
mechanical, never a judgment call).

**Recipe:** `.agents/tester/skills/blast-radius-leg/blast-radius-leg.md` (READ before the run).
**Digest comparison:** `.agents/tester/skills/failed-digest-equality-gate/` (both forms recorded).

## The D2 set (identical file list on both legs)

| derivation pass | result |
|---|---|
| the changed files (`git diff --name-only bfc3a233a...HEAD`) | `daemon/persistence.py` · `daemon/routers/metrics.py` · `daemon/services/metrics_query/{constants,envelope,provider,ring,sampler}.py` · `daemon/services/metrics_query/wiring/{db,sources}.py` · the contract doc · the evidence file · 9 test files + 1 test helper |
| the import/reference scan | 84 candidate files (the broad name scan) |
| the directory mapping | `daemon/services/metrics_query/**` → `tests/unit/metrics/` · `daemon/routers/metrics.py` → `tests/unit/routers/test_metrics_route.py` + the route manifest · `daemon/persistence.py` → `tests/unit/persistence/` + `tests/test_persistence.py` |
| the DECLARED set (cap applied — the drops are recorded) | `tests/unit/metrics` · `tests/unit/routers/test_metrics_route.py` · `tests/unit/test_router_scope_manifest.py` · `tests/unit/persistence` · `tests/test_persistence.py` |

**Drops recorded (the cap):** `tests/integration/*` (19 files), `tests/e2e/*` (2),
`tests/performance/*` (2), `tests/postgres/*` (4), `tests/job_queue/*` (5),
`tests/services/*` (4), `tests/unit/services/*` (3), `tests/unit/test_*.py` (11) — all
deselected by the pyproject `addopts` or excluded by the recipe's named pre-exclusions
(e2e cells need a live `:8079` daemon — compensating pin: the `e2e_workflows_ensure_test`
Release-Gate lane; `tests/performance/test_message_api_cost.py` — compensating pin: the
`regression_performance` pack; `tests/packs/g7` — packs run via their registered scripts).

**D1 (lane-owned, HEAD-only):** **EMPTY** — the fix cycle added NO new test FILE (every new
pin lives inside an existing pack file), so there is no D1/BASE asymmetry to declare.

## The legs

| | BASE (`bfc3a233a`) | HEAD (`36c46aa1a`) |
|---|---|---|
| the command | `pytest <the D2 set> -q -p no:randomly --timeout=120 --override-ini="addopts="` | **the same command, the same file list** |
| the exit code | **1** | **1** |
| the slicing proof (`--collect-only -q`) | **533 nodes** | **559 nodes** (+26 = the new pins) |
| the FAILED/ERROR lines (`GREP-FULL`) | 1 | 1 |
| the summary | `1 failed, 532 passed, 1 warning in 29.22s` | `1 failed, 558 passed, 1 warning in 30.86s` |
| the digest (BARE-ID form) | `d78b09cf2549c37191a10c3dcbe540b3ec9d37ce0dcce75eeeb36fa4bb0d6b6d` | `d78b09cf2549c37191a10c3dcbe540b3ec9d37ce0dcce75eeeb36fa4bb0d6b6d` |

## The verdict

* **NET-NEW = EMPTY** (`comm -13 base head` → nothing).
* **FIXED = EMPTY** (nothing in this change set was red at the base — the cycle ADDS pins).
* The single red on BOTH legs is **base-proven** and identical:
  `tests/unit/persistence/test_checkpoint_metrics_vocabulary.py::TestSweepLaneVocabulary::test_rdrs_lanes_come_from_the_config_kill_switchs`
  — it is the lane's own pre-existing base debt (the same line appears in the P4 gate
  evidence at `.agents/shared/planning/central-metrics-v1/evidence/p4-gate/W1_head_d1d2.log.full`).
* **No ERROR lines** on either leg ⇒ no collection-time error was hidden by the digest.
* The diff set is EMPTY ⇒ the recipe's 3× flaky-isolation probes are not required.
* **The fences:** the per-variable env scrub (`env -u POSTGRES_*` is a silent no-op — bash
  does not glob env names), the sibling-env assertion (`POSTGRES_DB != ensemble_prod` and
  `POSTGRES_HOST != 10.44.0.2`), the `FAKE_HOME` fence, `PG_TEST_ALLOWED_DBS` set explicitly,
  and the porcelain count before/after (0 → 0) — all in `base/fence.txt` and `head/fence.txt`.
