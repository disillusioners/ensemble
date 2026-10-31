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
LIVE-LEG branch (`mission_live_guard.py:1110-1116` — "non-terminal member
present (live leg holds) and freshest tree-member last_activity_at (root
excluded) is 10800s old (<21600s threshold) — mission is plausibly
alive"). (Prose corrected 2026-10-31, G1a A2 fold: the earlier text
mis-attributed the hold to the "childless-root fallback"; the JSON reason
was always verbatim-correct.) Per-cycle detail patterns show only `orphan_active_skipped_
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

## Phase 3 T3.11 — mutation matrix (TRUE SHAPE: 25 planted / 22 RED-proven / 3 superseded)

> **AUTHORITY NOTE (micro-fold 2026-10-31, re-derived at the
> tidier fold):** the committed `phase3-artifacts/matrix.tsv` is the
> AUTHORITATIVE count — prose figures here and in PACKS.md are
> derived from it and re-derived at every fold. At the tidier fold
> it mechanically stands at **28 planted / 25 RED-proven / 3
> superseded** (the W2 cardinality leg + the three tidier-cycle
> legs INCLUDED: a1v3 token-between gate conjunct, a3v2
> detail-notify-outcome dropped, ADV4 admission-pair drift). An
> earlier lane-report closing note claiming "26/23/3" double-counted
> the W2 leg and is superseded — recorded so the false figure never
> propagates.

`phase3-artifacts/matrix.tsv` — every RED-proven leg: planted defect form
→ the named pin(s) RED (ids recorded) → restore → GREEN. Three first
plants are HONESTLY RECORDED as superseded dead forms (pin could not
discriminate the plant; the plant was re-shaped and re-proven, never
counted as RED): `T3.1c-age-boundary-exclusive` (the exact-edge
`<=`-vs-`<` is wall-clock-jittered by construction — superseded by the
+180s shifted-bound plant, beyond the pin's 120s margin),
`T3.12-a1-log-gated-reconciled` (a substring pin could not catch the
appended `and reconciled > 0` conjunct — superseded by the
colon-terminated gate pin), and `T3.1c-boundary-shifted-60s` (60s stays
inside the 120s margin — superseded by the +180s form). The council-fold
cycle added the W2 cardinality leg (simulated 5th status in the SSOT →
cardinality pin + S4-close guard-raise RED → restore GREEN).

### Known advisories — 4 ADOPTED (tidier fold 2026-10-31); 5-8 RECORDED, not folded

4. **Pack I DDL drift-guard — ADOPTED at the tidier fold (Shape 1)**:
   the pack's dead `_ACTIVE_JOB_IDS_SUBQUERY` copy was converted into
   a live drift-guard — the pack imports the PRODUCTION constant from
   ``lock_repository`` and asserts its spelled ``('queued', 'active')``
   pair at import (deliberately NOT a whole-DDL byte-equality pin; the
   guard lives in exactly this one pack). Mutation-proven: matrix leg
   ``ADV4-admission-pair-drift-guard`` (plant the production set
   dropping 'queued' ⇒ the pack errors at collection; restore ⇒
   green).

5. **A6(a) behavioral upgrade**: the member-alive builder pin is
   anchor-text based (`tid != instance_id` conjuncts); a behavioral
   (drive-the-guard) form would be stronger — upgrade on next touch.
6. **TOCTOU comment wording**: the A2 recheck comment could name the
   RETRY-REARM ORPHAN CORNER explicitly at the seam (currently spelled
   in the method docstring + the lane plan only).
7. **Partial-counter merge**: when `examined>0` spans patterns, the
   stats dict merge could surface per-pattern counters (currently the
   (h) block is a flat fold — adequate for the loop log).
8. **Anchor-pin on token**: the three `zombie_active_*` tokens could
   carry an exact-string registration pin at `_STATUS_CANONICAL_MAP`
   (currently covered behaviorally by Pack G + the A5 doc pin).

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

### Tidier fold (2026-10-31) — strengthened pins

The tidier fold strengthened two pins and adopted one guard; each is
mutation-proven in the matrix (same-commit TSV discipline):
``T3.12-a1v3-token-between-gate-conjunct`` — the BROADENED A1
forbidden form catches the token-between conjunct the old narrow
regex missed (this REPLACES the strength of the disclosed dead first
plant ``T3.12-a1-log-gated-reconciled``);
``T3.12-a3v2-detail-notify-outcome-dropped`` — the de-tautologized
A3 pin's Anchor-2 (the old ``or`` arm stayed GREEN under this plant);
``ADV4-admission-pair-drift-guard`` — the adopted advisory 4.
