# Delta-3 (the final fix cycle) — the Tier-1 blast-radius gate (net-new = EMPTY)

**Tier:** 1 (the DEFAULT for feature lanes — the diff touches NO core-seam file:
`daemon/graph.py` · `daemon/manager.py` · `daemon/api.py` ·
`daemon/services/instance_messaging.py` · `daemon/services/task_processor.py` ·
`daemon/services/job_queue_service.py`).

**Recipe:** `.agents/tester/skills/blast-radius-leg/blast-radius-leg.md` (READ before the run).
**Digest comparison:** both forms recorded (BARE-ID + GREP-FULL).

## The legs

| | BASE `0ae9720f0` | HEAD `cafe77951` |
|---|---|---|
| the command | `pytest <the D2 set> -q -p no:randomly --timeout=120 --override-ini="addopts="` | **the same command, the same file list** |
| the exit code | **1** | **1** |
| the slicing proof (`--collect-only -q`) | **560 nodes** | **570 nodes** (+10 = the new pins) |
| the summary | `1 failed, 559 passed, 1 warning in 32.41s` | `1 failed, 569 passed, 1 warning in 33.85s` |
| the FAILED/ERROR lines (`GREP-FULL`) | 1 | 1 |
| the digest (BARE-ID form) | `d78b09cf2549c37191a10c3dcbe540b3ec9d37ce0dcce75eeeb36fa4bb0d6b6d` | `d78b09cf2549c37191a10c3dcbe540b3ec9d37ce0dcce75eeeb36fa4bb0d6b6d` |

## The verdict

* **NET-NEW = EMPTY** (`comm -13 base head` → nothing). **FIXED = EMPTY.**
* The single red on BOTH legs is **base-proven** and identical:
  `tests/unit/persistence/test_checkpoint_metrics_vocabulary.py::TestSweepLaneVocabulary::test_rdrs_lanes_come_from_the_config_kill_switchs`
  — the lane's own pre-existing base debt (the same line appears in the
  fix-cycle-1 gate's artefacts).
* No ERROR lines on either leg ⇒ no collection-time error hidden by the digest.
* The diff set is EMPTY ⇒ the recipe's 3× flaky-isolation probes are not required.
* The fences (in `base/fence.txt` / `head/fence.txt`): the per-variable env scrub
  (`env -u POSTGRES_*` is a silent no-op — bash does not glob env names), the
  sibling-env assertion (`POSTGRES_DB != ensemble_prod`, `POSTGRES_HOST != 10.44.0.2`),
  the `FAKE_HOME` fence, `PG_TEST_ALLOWED_DBS` set explicitly, and the porcelain count
  before/after (0 → 0).

## The pack legs run alongside

| pack | result |
|---|---|
| `tests/unit/metrics/test_metrics_query_pagination.py` | **38 passed** (31 + the 4 ND-2 walk legs + the 3 ND-4 expiry legs — the pack held **31** tests before the two ND commits added 7, verified against `7018162fd~1`: 31 → +4 (ND-2) → +3 (ND-4) → 38) |
| `tests/unit/metrics/test_central_metrics_contract_doc.py` | **21 passed** |
| `tests/unit/metrics/` (the lane's full suite) | **383 passed** |

## The mutation matrix (each leg applied → the pin observed RED → the tree restored)

| mutation | the legs that went RED |
|---|---|
| **M-ND2** — the delta-3 display OR planted back | 4 (the walk's "re-minted a cursor" · the last-page-mints · the empty-re-mints · the display-flag leg) — the pack's other 31 tests stayed GREEN |
| **M-ND4** — the snapshot builder's expiry probe removed | 2 (the stale-cursor 400 · the off-loop probe) — the pack's other 36 stayed GREEN |
| **M-ND5** — the install_dir flag reporting the CALLABLE again | 2 (the absent-path · the failing-resolver) — the pack's other 22 stayed GREEN |
