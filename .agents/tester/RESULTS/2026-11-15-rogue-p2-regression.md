# Gate 7 — rogue-containment-p2 SCOPED REGRESSION vs TRUE BASE (2026-11-15)

- Lane: rogue-port-containment (absorbs sonic sweep lane) · commission wi-eb4e5059
- HEAD leg: `a7aec8582` (feature/rogue-port-containment tip, "test: harden AH8/AH9 pin robustness")
- BASE leg: `bf4bfde6e` (= origin/latest at gate time, UNMOVED — TRUE PRE-FIX BASE)
- Pack: `rogue_p2_regression_scoped_test` (ad-hoc, registered post-run by tester)
- Executor: gate-7 worker · venue provenance: HEAD+BASE both `git archive <sha> | tar -x` /tmp
  venues (READ-ONLY reconstruction — sanctioned after the ~00:51Z foreign worktree deletion;
  see PROVENANCE.md in this rescue pack + anomaly id 527394b1). Zero git mutations, zero commits.
- Interpreter: Python 3.14.7 (uv-managed cpython-3.14.7, byte-matched both legs) · pytest 9.0.2
- Scrub: per-variable (mandated POSTGRES_*/DATABASE_URL/PG* + FULL ENSEMBLE_AUTH_* family
  + ENSEMBLE_SELF_ENV + ENSEMBLE_SKILL_EVOLUTION_ENABLED + ENSEMBLE_SKILL_CAPTURE_ENABLED);
  `-p no:randomly --tb=short`; every invocation `timeout 300`; no node-id split needed
  (largest shard 47 nodes / 11.6s — all file-level shards ≪ 300s).
- Aggregate: **RESULT: PASS** — net-new failing list vs base = **EMPTY**.

## LEG A — quarantine overlap (grep-verified vs .agents/tester/QUARANTINE.md @ a7aec8582)

Briefing expected "none expected to overlap" — ACTUAL: 1 file-level overlap (reported, file untouched):

| Scope file | Quarantine rows | Nodes | Disposition |
|---|---|---|---|
| tests/test_api.py | row 13 (2026-10-19) ×6 | test_list_instances_no_project_id_filter :404 · filter_by_project_id :436 · filter_by_nonexistent_project_id :454 · project_id_with_status_filter :484 (4× stale mock `assert_called_once_with` omitting `source=None`, instances.py:468) + test_send_message_success :876 · test_global_exception_handler :946 (2× Mock-await→AsyncMock class, messages.py) | QUARANTINED — deterministic pre-existing, base-attributed, "excluded from gate adjudication" |
| tests/test_api.py | rows 113 (2026-09-18) + 124 (2026-09-26) | re-confirmations of the ×2 send_message/global_exception family (base-proof @ a6442bff verbatim tracebacks; 32→34 node family) | same family |
| tests/test_api.py | row 181 (2026-11-12) | `test_send_message_instance_not_found` — load-composition flake-watch set (candidate-only in-sweep; 3/3 GREEN isolated there). In THIS gate it is deterministically red in SERIAL runs at BOTH legs → pre-existing family (see LEG D), plus its standing load-context flake annotation | flake-watch + base-attributed |
| other 9 scope files | — | zero rows (per-file grep) | no overlap |

Signature-drift observation for row maintainers (no edit made): row-13's original signatures
(Mock-await / `source=None` assertion classes) now present as `RuntimeError: Task … got Future
… attached to a different loop` (py3.14.7/asyncio loop-identity class) on 5 of the 9 reds —
same node set, evolved signature; identical at base ⇒ environmental drift, gate-neutral.

## LEG B — HEAD shards (10 files, one shard each, venue /tmp/rogue-p2-g7-head-a7aec8582)

| # | File | nodes | rc | passed | failed | wall |
|---|---|---|---|---|---|---|
| 1 | tests/job_queue/test_stgh_sweep_truth_pins.py (NEW) | 7 | 0 | 7 | 0 | 9.12s |
| 2 | tests/test_run_tests_scrubbed_selfenv_gate.py (NEW) | 10 | 0 | 10 | 0 | 2.05s |
| 3 | tests/unit/test_port_holder_identity.py (NEW) | 11 | 0 | 11 | 0 | 0.40s |
| 4 | tests/unit/test_prod_dsn_fence.py (NEW) | 26 | 0 | 26 | 0 | 0.91s |
| 5 | tests/unit/test_port_ownership_pins.py (MOD) | 31 | 0 | 31 | 0 | 5.64s |
| 6 | tests/unit/test_db_resolution_guard.py (MOD) | 18 | 0 | 18 | 0 | 0.94s |
| 7 | tests/unit/test_pkill_overlay_pins.py (MOD) | 13 | 0 | 13 | 0 | 0.93s |
| 8 | tests/unit/test_pkill_overlay_guard.py (MOD) | 16 | 0 | 16 | 0 | 0.87s |
| 9 | tests/unit/test_log_hygiene.py (MOD) | 34 | 0 | 34 | 0 | 2.05s |
| 10 | tests/test_api.py (MOD) | 47 | 1 | 38 | 9 | 8.81s |
| | **TOTAL** | **213** | | **204** | **9** | ≈31.7s pytest wall |

TIMEOUTs: none (max shard 11.6s vs 300s cap). Raw logs: `raw-shard/` in this pack.

## LEG C — net-new stability (4 NEW files × 3 isolated runs, fresh ENSEMBLE_DATA_DIR +
ENSEMBLE_PORT_CLAIMS_DIR tmp per run)

| File | run1 | run2 | run3 | verdict |
|---|---|---|---|---|
| test_stgh_sweep_truth_pins.py | 7P rc0 (7.74s) | 7P rc0 (8.27s) | 7P rc0 (7.74s) | STABLE (identical counts ×3) |
| test_run_tests_scrubbed_selfenv_gate.py | 10P rc0 (1.81s) | 10P rc0 (1.65s) | 10P rc0 (1.75s) | STABLE |
| test_port_holder_identity.py | 11P rc0 (0.42s) | 11P rc0 (0.40s) | 11P rc0 (0.38s) | STABLE |
| test_prod_dsn_fence.py | 26P rc0 (0.86s) | 26P rc0 (0.89s) | 26P rc0 (0.92s) | STABLE |

54 nodes × 3 runs = 162 node-runs, 0 flakes, zero variance. No quarantine action needed.

## LEG D — base attribution (triggered: tests/test_api.py failed at HEAD; other 9 files green at HEAD ⇒ no base leg)

Base venue /tmp/rogue-p2-g7-base-bf4bfde6e (git archive bf4bfde6e, uv sync --frozen, py3.14.7 —
byte-matched interpreter). Identical invocation.

| File | HEAD failures (9) | BASE failures (9) | diff | verdict |
|---|---|---|---|---|
| tests/test_api.py | 9F/38P rc1 | 9F/38P rc1 | `diff` of FAILED lines: **EMPTY** (byte-identical node set + signatures) | ALL 9 PRE-EXISTING at bf4bfde6e |

Head FAILED set (9): test_list_instances_no_project_id_filter · test_list_instances_filter_by_project_id ·
test_list_instances_filter_by_nonexistent_project_id · test_list_instances_project_id_with_status_filter ·
test_get_instance_success · test_get_instance_not_found · test_send_message_success ·
test_send_message_instance_not_found · test_global_exception_handler

- `git diff --name-only bf4bfde6e..a7aec8582 -- tests/test_api.py` → file IS commission-touched ⇒
  these are **pre-existing-family failures inside a commission-touched file** (adjudicated
  pre-existing by executed-base parity, per gate rule). 6/9 carry standing quarantine rows
  (LEG A table); 3/9 (get_instance_success, get_instance_not_found, send_message_instance_not_found)
  are not row-13 members but are proven base-identical TODAY by execution (row 181 covers one of
  them as load-flake; the get_instance pair + serial determinism of the trio are newly documented here).

## Final failing-list diff vs base (the money line)

```
HEAD-failures minus BASE-failures = EMPTY   (9 ≡ 9, byte-identical FAILED lines)
Net-new failures attributable to a7aec8582 / feature/rogue-port-containment = 0
```

**RESULT: PASS** — 213 nodes run / 204 passed / 9 failed (all pre-existing-at-base, 6 quarantined);
net-new = 0; NEW-file stability 3×3 green; no TIMEOUTs.

## Cleanup RECORDED addendum (POK rogue-cleanup-rider-recorded-addendum)

/tmp riders created by this gate (all KEPT for reviewer byte-reproduction — no deletion, so the
staleness-proof is "intentionally retained"; sizes as of 01:0xZ):
- /tmp/rogue-p2-g7-head-a7aec8582/ — HEAD archive venue (tree+venv), kept
- /tmp/rogue-p2-g7-base-bf4bfde6e/ — BASE archive venue (tree+venv), kept
- /tmp/rogue-p2-g7/ — collect/shard/stab logs + scripts (~40 files), kept (raw evidence)
- /tmp/rogue-p2-g7-evidence-rescue-20261115T005835Z/ — THIS pack (PROVENANCE.md + raw-collect/
  + raw-shard/ + this RESULTS file + ready-to-apply commit cmd), kept — never delete
- 12 mktemp stab dirs /tmp/rogue-p2-g7/stab-*-r{1,2,3}-XXXXXX (empty-data dirs, ~0 bytes each), kept
- NOT touched: sibling artifacts (/tmp/rogue-p2-pgcluster PG on 15432, leg1-6 logs, gate2 rescue pack,
  worktree /home/nea/ensemble-src-wt-rogue-contain incl. its scratch-config.yaml — foreign, do not touch)

## Commit landing (BLOCKED by design — branch frozen mid-finalize)

Per leader incident guidance: NO commits to feature/rogue-port-containment until finalize completes.
This file is preserved in the rescue pack; ready-to-apply command in PROVENANCE.md. Landing = leader's
post-finalize call (apply onto latest-side RESULTS via the same `git add <path> && git commit --only`
pattern to avoid shared-worktree races).
