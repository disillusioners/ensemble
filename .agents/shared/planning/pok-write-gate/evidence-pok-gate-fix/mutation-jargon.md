# Mutation evidence — prompt pin: jargon (bare "POK")

**Artifact for:** pok-write-gate Phase 7/8, prompt half — the jargon scrub
(no bare "POK"/"RAG" abbreviation in either prompt message).

**Status:** RED CONFIRMED (2026-11-25).

**Harness:** `scripts/pok_gate_prompt_mutations.py` (leg `jargon-bare-pok`).

---

## The claim under test

Every occurrence of "POK" in the judge prompt must be the expansion form
"Project Operating Knowledge (POK)". Reintroducing a bare "POK" in the
persona must make the pin RED.

## Procedure

- Target file: `daemon/services/pok_gate.py`
- Mutation: in `GATE_PERSONA`, replace
  `"You are the write gate for the Project Operating Knowledge (POK) "`
  with `"You are the write gate for the POK "`.
- Test that went RED:
  `tests/unit/services/test_pok_gate_unit.py::TestPromptClauses::test_no_bare_pok_or_rag_anywhere_in_either_message`

## Result

- Baseline (expansion form): GREEN (exit 0).
- Mutated (bare "POK"): RED (exit 1).
- Restore: sha256 before == after =
  `bc693bdf06beff6537bc28773e4726480e9c1ee198d3fe857351f32a6e4e5915`.
- Re-green after restore: GREEN (exit 0).

## Restore mechanism

`shutil.copy2` from the scratch backup under `/tmp/pok-gate-prompt-mutations/`,
verified by sha256 equality.
