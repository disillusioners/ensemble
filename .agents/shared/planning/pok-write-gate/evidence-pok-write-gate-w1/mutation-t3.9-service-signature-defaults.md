# Mutation evidence — T3.9 service-signature defaults (Wave 1 Phase 6 coverage)

**Artifact for:** pok-write-gate Phase 3 T3.9 — "PIN: service-signature
defaults present — mutation leg: removing `reason`/`gate_fn` defaults
must RED (97 sites)" — covered by Wave 1 Phase 6 because the
plan-overview's T3.9 task-map row names the throwaway-archive leg and
carries the `pok_write_gate_w1_unit_test` PACKS.md row.

**Status:** RED CONFIRMED (2026-11-24).

**Fence:** the mutation was applied to a THROWAWAY `git archive` extract
of the branch tip (`4234d2b82`). The live worktree was NEVER mutated —
`git status --porcelain` after the leg is EMPTY.

**Scope note (why this artifact exists in Phase 6).** Phase 6's own
task rows (T6.2 / T6.3 / T6.4) name three mutation legs; T3.9's
signature-defaults leg was NOT among them, and Phase 3's commit
(`f528a8fbf`) carried no evidence artifact for it. It is nonetheless a
NAMED plan acceptance — Phase 3's exit criterion requires "both mutation
legs RED (reason validation + signature defaults)" — and the
plan-overview's T3.9 row binds it to the same `pok_write_gate_w1_unit_test`
pack row. The dispatching brief's conditional ("if … the overview File
cells name a mutation for the mechanical-gate invocation seam (Phase 3),
cover it") is therefore SATISFIED, and this leg covers it. No product
code changed; this is an EVIDENCE artifact only.

---

## The claim under test

Phase 3 T3.1: `reason` (`str`, default `None`) and `gate_fn`
(`Callable | None`, default `None`) on `POKService.upsert` carry
DEFAULTS, and the defaults are MANDATORY — without them the **97
direct `svc.upsert` test sites** break (verified at authoring:
`tests/unit/services/test_pok_service_unit.py` = 97 `.upsert(` sites).

The claim under test: **removing the defaults must RED.** If the sites
stay green, the "mandatory defaults" contract is not enforced, and the
next agent to add a required keyword argument to `upsert` would break
the 97-site surface with no gate to catch it.

## Procedure (reproducible)

```sh
WT=/home/nea/ensemble-src-wt-pok-write-gate
TH=/tmp/pok-mutation-t39
rm -rf "$TH" && mkdir -p "$TH"
cd "$WT" && git archive HEAD | tar -x -C "$TH"
cd "$TH"

# STEP 1 — BASELINE (defaults present)
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL \
    -u ENSEMBLE_DB_DSN -u PORT -u DAEMON_PORT \
  PYTHONPATH="$TH" ENSEMBLE_SELF_ENV=dev \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 PYTHONDONTWRITEBYTECODE=1 \
  "$WT/.venv/bin/python" -m pytest \
  "tests/unit/services/test_pok_service_unit.py" \
  "tests/unit/tools/test_pok_tools_unit.py" \
  -p no:randomly -q --tb=line -rfE

# MUTATION — drop the two defaults from POKService.upsert
python3 - "$TH" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1]) / "daemon/services/pok_service.py"
s = p.read_text()
old = ("        reason: str | None = None,\n"
       "        gate_fn: Callable | None = None,\n")
new = ("        reason: str | None,\n"
       "        gate_fn: Callable | None,\n")
assert s.count(old) == 1
p.write_text(s.replace(old, new))
PY

# STEP 2 — MUTATED (defaults removed) — MUST BE RED
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL \
    -u ENSEMBLE_DB_DSN -u PORT -u DAEMON_PORT \
  PYTHONPATH="$TH" ENSEMBLE_SELF_ENV=dev \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 PYTHONDONTWRITEBYTECODE=1 \
  "$WT/.venv/bin/python" -m pytest \
  "tests/unit/services/test_pok_service_unit.py" \
  "tests/unit/tools/test_pok_tools_unit.py" \
  -p no:randomly -q --tb=line -rfE

rm -rf "$TH"
```

## Result

| Step | State | Nodes | Exit |
|------|-------|-------|------|
| 1 — BASELINE | defaults PRESENT | 97 passed | **0** |
| 2 — MUTATED | defaults REMOVED | **72 failed**, 25 passed | **1** |

RED breakdown (72 RED nodes):

```
     58 FAILED tests/unit/services/test_pok_service_unit.py
     14 FAILED tests/unit/tools/test_pok_tools_unit.py
```

The failure signature is uniform and unambiguous:

```
E   TypeError: POKService.upsert() missing 2 required keyword-only arguments: 'reason' and 'gate_fn'
/tmp/pok-mutation-t39/tests/unit/services/test_pok_service_unit.py:54: TypeError: POKService.upsert() missing 2 required keyword-only arguments: 'reason' and 'gate_fn'
```

## Reading the RED

The 72 RED nodes are the direct `svc.upsert` call sites that rely on
the defaults — 58 in the service unit file (the 97 `.upsert(` sites
collapse to 58 collected nodes) and 14 in the tool unit file (the tool
layer calls `svc.upsert(...)` WITHOUT `reason` and WITHOUT `gate_fn`;
it enforces `reason` at the TOOL layer, above the service call).

The 25 GREEN nodes are CONTROLS — they assert what the defaults must
NOT change:

* the tool-layer REFUSAL path (`pok_reason_required`, `pok_store_unavailable`,
  `pok_default_project`, the R0 `pok_body_structural_token`, the typed
  `_err` envelope, the pairwise `store→scope→reason` precedence) — every
  one of those returns BEFORE `svc.upsert` is reached, so it is
  untouched by the signature change;
* the service unit nodes that never call `upsert` (query / curate /
  trust-tier surfaces).

**The pin has teeth:** the defaults are load-bearing across the whole
97-site direct-call surface, and the mechanical gate's tool-layer
refusals are upstream of it (they stay green — which is exactly what
makes the 72 RED count meaningful).

## Scope note

This leg covers T3.9 ONLY (the two signature defaults on
`POKService.upsert`). It does NOT mutate the tool-layer reason
validation (T6.2's own leg), the R0 fence implementation, the
family-count logic (parallel lane), or the service's admission policy.
