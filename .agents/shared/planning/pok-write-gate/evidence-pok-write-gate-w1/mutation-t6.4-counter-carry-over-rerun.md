# Mutation evidence — T6.4 counter carry-over dropped (Wave 1 Phase 6)

**Artifact for:** pok-write-gate Phase 6, T6.4 — "Mutation: counter
carry-over dropped from supersede → RED".

**Status:** RED CONFIRMED (2026-11-24) — INDEPENDENT RE-RUN of the
Phase-2 leg, at the Phase-6 tip.

**Fence:** the mutation was applied to a THROWAWAY `git archive` extract
of the branch tip (`d76510eff`). The live worktree was NEVER mutated —
`git status --porcelain` after the leg is EMPTY, and the live
`daemon/repositories/pok/repository.py` still hashes to
`16ac58adae2dfe5d6ebe0f194811919f443c1503acdc33ff49d57427dfdf3c94`.

**Relationship to the Phase-2 artifact.** The leg was FIRST evidenced
as `mutation-t2.8-counter-carryover.md` (commit `8d5c1e39e`, the Phase-2
T2.8 PIN). Phase 6 re-runs it for independent confirmation — the
brief's "verify/reuse it, do not re-litigate" — and the RED set is
IDENTICAL (same 4 legs RED, same 2 CONTROL legs green). No
re-interpretation, no scope change.

---

## The claim under test

T2.7: the successor of a supersede INHERITS the prior lineage's
counters (`verify_count` + `access_count`) and the verify/access
stamps, instead of defaulting to 0.

Rationale (design-amendments-v2 §13.4 defect #2): every merge is a
supersede, and a supersede that silently resets the counters breaks the
J trusted-metric chain — gate-stamped `verify_count` IS the trusted
metric, so the chain would restart at every merge, making the metric
self-defending against exactly the rows the librarian most needs to
judge. The same reasoning is what Phase 5's trusted-metric set
(T5.3) rests on: a metric that resets at every merge is not a metric.

## Procedure (reproducible)

```sh
WT=/home/nea/ensemble-src-wt-pok-write-gate
TH=/tmp/pok-mutation-t64
rm -rf "$TH" && mkdir -p "$TH"
cd "$WT" && git archive HEAD | tar -x -C "$TH"
cd "$TH"

# STEP 1 — BASELINE (carry-over present)
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL \
    -u ENSEMBLE_DB_DSN -u PORT -u DAEMON_PORT \
  PYTHONPATH="$TH" ENSEMBLE_SELF_ENV=dev \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 PYTHONDONTWRITEBYTECODE=1 \
  "$WT/.venv/bin/python" -m pytest \
  "tests/unit/repositories/test_pok_write_gate_phase2_unit.py::TestCounterCarryOver" \
  -p no:randomly -q --tb=line -rfE

# MUTATION — drop the carry-over block from
#   daemon/repositories/pok/repository.py :: supersede_and_insert
python3 - "$TH" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1]) / "daemon/repositories/pok/repository.py"
s = p.read_text()
block = '''                successor.verify_count = (db_prior.verify_count or 0) + (
                    successor.verify_count or 0
                )
                successor.access_count = (db_prior.access_count or 0) + (
                    successor.access_count or 0
                )
                if successor.last_verified is None:
                    successor.last_verified = db_prior.last_verified
                if successor.last_accessed_at is None:
                    successor.last_accessed_at = db_prior.last_accessed_at
'''
assert s.count(block) == 1
p.write_text(s.replace(block, ""))
PY

# STEP 2 — MUTATED (carry-over dropped) — MUST BE RED
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL \
    -u ENSEMBLE_DB_DSN -u PORT -u DAEMON_PORT \
  PYTHONPATH="$TH" ENSEMBLE_SELF_ENV=dev \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 PYTHONDONTWRITEBYTECODE=1 \
  "$WT/.venv/bin/python" -m pytest \
  "tests/unit/repositories/test_pok_write_gate_phase2_unit.py::TestCounterCarryOver" \
  -p no:randomly -q --tb=line -rfE

rm -rf "$TH"
```

## Result

| Step | State | Nodes | Exit |
|------|-------|-------|------|
| 1 — BASELINE | carry-over PRESENT | 6 passed | **0** |
| 2 — MUTATED | carry-over DROPPED | **4 failed**, 2 passed | **1** |

RED assertions (verbatim):

```
FAILED tests/unit/repositories/test_pok_write_gate_phase2_unit.py::TestCounterCarryOver::test_verify_count_lineage_is_preserved
FAILED tests/unit/repositories/test_pok_write_gate_phase2_unit.py::TestCounterCarryOver::test_access_count_rides_along
FAILED tests/unit/repositories/test_pok_write_gate_phase2_unit.py::TestCounterCarryOver::test_the_verify_stamp_travels_with_the_count
FAILED tests/unit/repositories/test_pok_write_gate_phase2_unit.py::TestCounterCarryOver::test_carry_over_accumulates_rather_than_overwrites
4 failed, 2 passed, 17 warnings in 0.57s
```

## Reading the RED

4 of the 6 carry-over legs go RED — the ones that assert the
carry-over's SUBSTANCE: the `verify_count` lineage, the `access_count`
ride-along, the `last_verified` stamp travelling with the count, and
the accumulate-rather-than-overwrite semantics.

The 2 GREEN legs are CONTROLS and are expected to stay green — they
assert what the carry-over must NOT change:

* `test_a_fresh_lineage_starts_at_zero` — a lineage with no prior
  counters inherits nothing (0 + 0 = 0).
* `test_the_supersede_is_still_count_preserving` — the row COUNT is
  unaffected by the carry-over (a merge is one active row before, one
  after).

**Byte-identical to the Phase-2 evidence** (`mutation-t2.8-counter-carryover.md`):
same 4 RED legs, same 2 CONTROL legs, same pass/fail split. The leg
reproduces at the Phase-6 tip.

## Scope note

This leg covers T2.7/T2.8/T6.4 ONLY. It does NOT mutate the
family-count method or the family-cap logic — those belong to the
parallel lane (commission `pok-family-count-strict`, landed on
`origin/latest` and consumed read-only by this lane).
