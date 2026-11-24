# Mutation evidence — T6.2 reason validation removed (Wave 1 Phase 6)

**Artifact for:** pok-write-gate Phase 6, T6.2 — "Mutation: reason
validation removed → RED" (the Wave-1 re-proof of Phase 3 T3.7).

**Status:** RED CONFIRMED (2026-11-24).

**Fence:** the mutation was applied to a THROWAWAY `git archive` extract
of the branch tip (`d76510eff`, the Phase-5 close). The live worktree
was NEVER mutated — `git status --porcelain` after the leg is EMPTY,
and the live `daemon/tools/pok.py` still hashes to
`ef79751450485239b2c87e5096b70d93616086916893bcf58ccba00df8f87101`.

---

## The claim under test

Phase 3 T3.3 made `reason` a REQUIRED operational claim at the tool
layer: a `pok_upsert` whose reason is missing or whitespace-only is
refused with the typed code `pok_reason_required`, AFTER the store and
scope guards and BEFORE the service call (the canonical precedence
`store→scope→reason→fence/budget/family`).

The claim under test is therefore: **removing the reason validation
must make the mechanical-gate pins RED.** If the pins stay green, the
`reason` requirement is unenforced and the whole Phase-3 mechanical
gate (and its 97-site default-preservation contract) rests on nothing.

## Procedure (reproducible)

```sh
WT=/home/nea/ensemble-src-wt-pok-write-gate
TH=/tmp/pok-mutation-t62
rm -rf "$TH" && mkdir -p "$TH"
cd "$WT" && git archive HEAD | tar -x -C "$TH"
cd "$TH"

# STEP 1 — BASELINE (reason validation PRESENT)
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL \
    -u ENSEMBLE_DB_DSN -u PORT -u DAEMON_PORT \
  PYTHONPATH="$TH" ENSEMBLE_SELF_ENV=dev \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 PYTHONDONTWRITEBYTECODE=1 \
  "$WT/.venv/bin/python" -m pytest \
  "tests/unit/tools/test_pok_tools_unit.py::TestPhase3MechanicalGate" \
  -p no:randomly -q --tb=line -rfE

# MUTATION — drop the reason-validation guard from daemon/tools/pok.py
python3 - "$TH" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1]) / "daemon/tools/pok.py"
s = p.read_text()
block = '''        if not reason or not reason.strip():
            return _err(
                "pok_reason_required",
                "a reason (the operational claim) is required — state "
                "why this fact belongs in POK",
            )
'''
assert s.count(block) == 1
p.write_text(s.replace(block, ""))
PY

# STEP 2 — MUTATED (reason validation REMOVED) — MUST BE RED
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL \
    -u ENSEMBLE_DB_DSN -u PORT -u DAEMON_PORT \
  PYTHONPATH="$TH" ENSEMBLE_SELF_ENV=dev \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 PYTHONDONTWRITEBYTECODE=1 \
  "$WT/.venv/bin/python" -m pytest \
  "tests/unit/tools/test_pok_tools_unit.py::TestPhase3MechanicalGate" \
  -p no:randomly -q --tb=line -rfE

rm -rf "$TH"
```

`PYTHONPATH="$TH"` is what makes the throwaway's `daemon` package
resolve to the THROWAWAY rather than to the worktree's editable install
(the editable `.pth` appends the worktree path AFTER `PYTHONPATH` in
`sys.path`, so the throwaway wins).

## Result

| Step | State | Nodes | Exit |
|------|-------|-------|------|
| 1 — BASELINE | reason validation PRESENT | 6 passed | **0** |
| 2 — MUTATED | reason validation REMOVED | **2 failed**, 4 passed | **1** |

RED assertions (verbatim):

```
FAILED tests/unit/tools/test_pok_tools_unit.py::TestPhase3MechanicalGate::test_empty_reason_typed_refusal
FAILED tests/unit/tools/test_pok_tools_unit.py::TestPhase3MechanicalGate::test_typed_refusal_envelope_shape
2 failed, 4 passed, 17 warnings in 0.51s
```

```
/tmp/pok-mutation-t62/tests/unit/tools/test_pok_tools_unit.py:537: KeyError: 'code'
E   AssertionError: assert {'action', 'a..., 'slug', ...} == {'code', 'error'}
```

## Reading the RED

The 2 RED legs are the ones that assert the validation's SUBSTANCE:

* `test_empty_reason_typed_refusal` — a whitespace-only reason must
  return `pok_reason_required`; with the guard removed the upsert
  SUCCEEDS and the echo has no `code` key at all (`KeyError: 'code'`).
* `test_typed_refusal_envelope_shape` — the refusal envelope must be
  exactly `{error, code}`; with the guard removed the echo carries the
  whole upsert payload instead.

The 4 GREEN legs are CONTROLS and must stay green — they assert what
the reason validation must NOT change:

* `test_upsert_schema_requires_reason` — the TOOL SCHEMA still marks
  `reason` required (the mutation removed the runtime guard, not the
  schema field; a schema-level mutation is the Phase-3 T3.2 leg).
* `test_fence_refusal_names_r0` — the R0 content fence still fires
  (it is a service-layer check, downstream of the reason guard).
* `test_store_precedes_scope_when_both_fail` — store still beats scope.
* `test_scope_precedes_reason_when_both_fail` — scope still beats a
  missing reason (the pairwise half of the canonical precedence
  `store→scope→reason→fence/budget/family`).

**The pin has teeth:** the reason requirement is load-bearing at the
tool layer, and the precedence order survives the mutation unchanged.

## Scope note

This leg covers T6.2 ONLY (the reason-validation guard). It does NOT
mutate the service-signature defaults (Phase 3 T3.1/T3.9's own leg),
the R0 fence implementation, or the family-count logic — the last
belongs to the parallel lane.
