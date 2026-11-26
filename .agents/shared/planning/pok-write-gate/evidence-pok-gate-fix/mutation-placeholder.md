# Mutation evidence — prompt pin: unnamed placeholder

**Artifact for:** pok-write-gate Phase 7/8, prompt half — the honest
unavailable-name wording (no "(unnamed)" placeholder).

**Status:** RED CONFIRMED (2026-11-25).

**Harness:** `scripts/pok_gate_prompt_mutations.py` (leg
`unnamed-placeholder`).

---

## The claim under test

The prompt never contains the literal "(unnamed)" placeholder; instead
it carries "was not available to this call". Restoring "(unnamed)" must
make the pin RED.

## Procedure

- Target file: `daemon/services/pok_gate.py`
- Mutation: in the `project_label` fallback, replace
  `"the project this write targets (its name was not available to "`
  with `"the project this write targets (unnamed) (its name was not available to "`.
- Test that went RED:
  `tests/unit/services/test_pok_gate_unit.py::TestPromptClauses::test_the_prompt_never_contains_unnamed_placeholder`

## Result

- Baseline (honest wording): GREEN (exit 0).
- Mutated (placeholder restored): RED (exit 1).
- Restore: sha256 before == after =
  `bc693bdf06beff6537bc28773e4726480e9c1ee198d3fe857351f32a6e4e5915`.
- Re-green after restore: GREEN (exit 0).

## Restore mechanism

`shutil.copy2` from the scratch backup under `/tmp/pok-gate-prompt-mutations/`,
verified by sha256 equality.
