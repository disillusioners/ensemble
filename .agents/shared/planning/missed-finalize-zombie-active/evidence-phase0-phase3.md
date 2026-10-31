# Phase 0 + Phase 3 evidence record — implementation lane (wi-78967dba)

Executor: implementation-lane coder, 2026-10-31. Lane HEAD at record time:
`dee69e149` (branch `fix/missed-finalize-zombie-active`, code base `22fe83317`,
plan-set base `0d6489879`).

## Phase 0 — baseline & evidence (AC-0.1..AC-0.4)

### AC-0.1 fences

- Every run in this lane used the per-variable env scrub (`env -u POSTGRES_DB
  -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u
  PORT -u DAEMON_PORT -u ENSEMBLE_UPGRADE_LIVE -u ENSEMBLE_UPGRADE_SCRIPTS_DIR
  …`) — the ambient tool env carried LIVE poison (`POSTGRES_DB=ensemble_prod`,
  `PORT=9797`, `ENSEMBLE_SELF_ENV=live`, `ENSEMBLE_UPGRADE_LIVE=1`) on entry.
- Worktree `.env` fence verified dev-shaped (`ENSEMBLE_SELF_ENV=dev`,
  `POSTGRES_DB=ensemble_dev` @ 127.0.0.1, mode 600, giter-minted).
- `data/ensemble.json` pin: ABSENT in the worktree run root (checked; the 2nd
  poison vector is not present).
- `ensemble_prod` never resolvable from any run: PG legs used `PG_TEST_*`
  (localhost:5432/`ensemble_test` — the conftest-sanctioned throwaway; its own
  fence refuses `ensemble_prod`/`ensemble_dev` unconditionally).
- The pytest corpus used `ENSEMBLE_SELF_ENV=dev` + the two house collection
  ignores (`tests/packs/g7_unique_index_smoke_test.py` module-level
  `sys.exit(0)` INTERNALERROR; `tests/e2e/test_context_injection_hybrid.py`
  collection-time HTTP to :8079).

### AC-0.2/AC-0.3 — fixture + BASE gap proof (GAP-PROVEN on both backends)

`phase0-artifacts/gap-proof-base-worktree-{sqlite,pg}.json` — synthetic
fixture (3 verdict variants: completed / failed / cancelled; task-kind
`active` JobItem + work_id Task terminal + RUNNING instance + `job_locks`
row; stamps aged 3h), driven by invoking `reconcile_drift_states` directly
(test-harness style, PD-0.1; NO daemon boot). Proven against the WORKTREE
code at base (an initial run accidentally resolved the main checkout's
daemon via `sys.path[0]` — script-path runs do NOT put the CWD on
`sys.path`; re-run with `PYTHONPATH=<worktree>` and `daemon.__file__`
asserted — the certified pattern applies to `-c`/`-m` forms only; recorded
as a foot-gun).

Result: 3 drift cycles × 3 variants on BOTH SQLite and real PG — the
JobItem stays `active`, `terminal_reason` NULL, `job_locks` row present
every cycle. E1 self-hold verdict captured verbatim: `live=True` via the
childless-root fallback ("freshest tree-member last_activity_at (root
excluded) is 10800s old (<21600s threshold) — mission is plausibly
alive"). Per-cycle detail patterns show only `orphan_active_skipped_
mission_live{,_f1}` — ZERO P1-class warnings for the class (findings-core
Q6 confirmed).

### AC-0.4 — TRUE base FAILED list (the Phase-4 net-new baseline)

`phase0-artifacts/base-failed.txt` — **330 unique FAILED/ERROR ids**, sha256
`58cb8201385bf50435e5d19a42b82ba843d39a58cb52bef05b08c56456858027`.
Captured with the verbatim command set (per-directory chunks + the
subdirectory-chunked `tests/unit` re-capture after the first 1500s-budget
chunk rc=124'd — the Phase-4 re-run MUST use the same recipe:

1. Per-directory: `pytest tests/<dir>/ -q --tb=no -rfE --timeout=120` with
   the two house ignores + `ENSEMBLE_SELF_ENV=dev` + the per-variable scrub
   (`timeout 1500` outer per directory; `tests/unit` needs the
   subdirectory-chunked form — 17 subdirs + one top-level-files chunk,
   `timeout 2400` each).
2. PG pack: `pytest tests/postgres/test_dbpool_wedge_capture_pg.py
   --override-ini="addopts=" -m postgres -q --timeout=120`
   (`PG_TEST_DB=ensemble_test`).

Headline base counts: job_queue 18F/2180P; integration 45F/1096P (517
deselected); unit/services 13F/3227P; unit/tools 16F/4360P;
unit-toplevel 88F+21E/10265P; api 6F/63P; tools 13F/282P; services 12F/617P;
static 2F; manager 9F; property 1F; repositories 3F; migration 1F;
message_queue_redesign 3F; pg-pack 3P.

## Phase 1 T1.7 — HEAD smoke (SMOKE-PASS)

The Phase-0 fixture at HEAD closes within ONE drift cycle of eligibility:
`done` + `zombie_active_{completed,failed,cancelled}` + `job_locks` gone
(same transaction). E1 flips to `live=False` CLEAN under
`discount_instance_id` ("no live leg: root + descendants terminal, bus
quiet — all-terminal tree finalizes immediately (anchor plays no role)").
Kill-switch OFF rerun on an identical fresh fixture: stays `active`, lock
row present, `examined=0` — pre-fix behavior byte-restored.

## Phase 3 T3.11 — mutation matrix (22 legs, all RED→GREEN)

`phase3-artifacts/matrix.tsv` — every leg: planted defect form → the named
pin(s) RED (ids recorded) → restore → GREEN. Includes the two re-proven
legs after pin hardening: T3.1c (boundary-semantics plant = SQL cutoff
shifted +180s — the exact-edge `<=`-vs-`<` is wall-clock-jittered by
construction; the deterministic-margin pin catches a SHIFTED bound) and
T3.12-a1 (colon-terminated gate form — a substring pin could not catch the
appended `and reconciled > 0` conjunct).

## Net-new pre-check (informational for Phase 4)

`tests/job_queue/` at HEAD `88d4a265c`: 18 failed / 2239 passed — the 18
FAILED ids are byte-identical to the base 18 (comm both directions empty);
passed grew by exactly the 59 new pack nodes. **net_new = ∅** on the family
directory.

## Standing notes for the follow-on gates (Phases 4-6)

- SC7 (migration delta EMPTY, 115==115): stays an EXPECTED value re-derived
  at stage (approver correction #3).
- The whole-tree Phase-4 gate re-runs the AC-0.4 recipe at HEAD verbatim;
  the flake-isolation discipline (3× per side) applies to the known
  order/load-sensitive families.
- A `designer-agent/install-audit.jsonl` +42-line test-debris write was
  observed during the base corpus capture (the documented CWD foot-gun —
  synthetic kms_issue rows from plugin-subsystem tests). Reverted (backup
  at `/tmp/mfz-phase0/install-audit-debris-20261031.jsonl`); flagged for
  the watch list, not committed.
