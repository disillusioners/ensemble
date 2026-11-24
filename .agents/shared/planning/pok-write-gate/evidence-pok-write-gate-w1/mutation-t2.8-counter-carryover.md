# Mutation evidence — T2.8 counter carry-over (Wave 1 Phase 2)

**Artifact for:** pok-write-gate Phase 2, T2.8 — "PIN: counter carry-over
present — mutation leg: dropping carry-over must RED".

**Status:** RED CONFIRMED (2026-11-24).

**Fence:** the mutation was applied to a THROWAWAY `git archive` extract
of the branch tip. The live worktree was never mutated — `git status`
after the leg shows only the not-mine `plan-review-4.md`.

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
judge.

## Procedure (reproducible)

```sh
WT=/home/nea/ensemble-src-wt-pok-write-gate
TH=/tmp/pok-mutation-t28
rm -rf "$TH" && mkdir -p "$TH"
cd "$WT" && git archive HEAD | tar -x -C "$TH"

# STEP 1 — BASELINE (carry-over present)
cd "$TH"
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u POSTGRES_HOST -u POSTGRES_PORT \
  PYTHONPATH="$TH" "$WT/.venv/bin/python" -m pytest \
  "tests/unit/repositories/test_pok_write_gate_phase2_unit.py::TestCounterCarryOver" \
  -q -p no:randomly

# MUTATION — drop the carry-over block from
#   daemon/repositories/pok/repository.py :: supersede_and_insert
# (the successor.verify_count / access_count / last_verified /
#  last_accessed_at inheritance assignments)

# STEP 2 — MUTATED (carry-over dropped) — MUST BE RED
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u POSTGRES_HOST -u POSTGRES_PORT \
  PYTHONPATH="$TH" "$WT/.venv/bin/python" -m pytest \
  "tests/unit/repositories/test_pok_write_gate_phase2_unit.py::TestCounterCarryOver" \
  -q -p no:randomly

rm -rf "$TH"
```

`PYTHONPATH="$TH"` is what makes the throwaway's `daemon` package
resolve to the THROWAWAY rather than to the worktree's editable
install (the editable `.pth` appends the worktree path AFTER
`PYTHONPATH` in `sys.path`, so the throwaway wins).

## Transcript

```
### STEP 1 — BASELINE (carry-over present)
6 passed, 17 warnings in 0.67s

### STEP 2 — MUTATED (carry-over dropped) — MUST BE RED
FAILED tests/unit/repositories/test_pok_write_gate_phase2_unit.py::TestCounterCarryOver::test_verify_count_lineage_is_preserved
FAILED tests/unit/repositories/test_pok_write_gate_phase2_unit.py::TestCounterCarryOver::test_access_count_rides_along
FAILED tests/unit/repositories/test_pok_write_gate_phase2_unit.py::TestCounterCarryOver::test_the_verify_stamp_travels_with_the_count
FAILED tests/unit/repositories/test_pok_write_gate_phase2_unit.py::TestCounterCarryOver::test_carry_over_accumulates_rather_than_overwrites
4 failed, 2 passed, 17 warnings in 0.76s
```

## Reading the RED

4 of the 6 carry-over legs go RED. The 2 that stay GREEN are the
CONTROL legs and are expected to stay green — they assert what the
carry-over must NOT change:

* `test_a_fresh_lineage_starts_at_zero` — a lineage with no prior
  counters inherits nothing (0 + 0 = 0). Dropping the carry-over does
  not change this, and it must not.
* `test_the_supersede_is_still_count_preserving` — the row COUNT is
  unaffected by the carry-over (a merge is one active row before, one
  after). Dropping the carry-over does not change this, and it must
  not.

The 4 RED legs are exactly the ones that assert the carry-over's
substance: the `verify_count` lineage, the `access_count` ride-along,
the `last_verified` stamp travelling with the count, and the
accumulate-rather-than-overwrite semantics.

## Scope note

This leg covers T2.7/T2.8 ONLY. It does NOT mutate the family-count
method or the family-cap logic — those belong to the parallel lane
(commission `pok-family-count-strict`, landed on `origin/latest` and
consumed read-only by this lane).
