# Mutation evidence — prompt pin: real digest header (B4)

**Artifact for:** pok-write-gate Phase 7/8, B4 — pin over the REAL
`build_active_digest` output (not a hand-built string).

**Status:** RED CONFIRMED (2026-11-25).

**Harness:** `scripts/pok_gate_prompt_mutations.py` (leg
`digest-header-bare-pok`).

---

## The claim under test

The real digest header produced by `build_active_digest` must be the
expansion form "PROJECT OPERATING KNOWLEDGE (POK) ACTIVE-ROW DIGEST
(project-scoped, bounded)" and its boundary clause must carry no bare
abbreviation. Regressing the real header to a bare-"POK" form must make
the pin RED.

## Procedure

- Target file: `daemon/services/pok_gate.py`
- Mutation: in `build_active_digest`, replace
  `"PROJECT OPERATING KNOWLEDGE (POK) ACTIVE-ROW DIGEST "`
  with `"POK ACTIVE-ROW DIGEST "`.
- Test that went RED:
  `tests/unit/services/test_pok_gate_unit.py::TestRealDigestHeader::test_the_real_digest_header_is_the_expansion_form`

## Result

- Baseline (expansion header): GREEN (exit 0).
- Mutated (bare "POK" header): RED (exit 1).
- Restore: sha256 before == after =
  `bc693bdf06beff6537bc28773e4726480e9c1ee198d3fe857351f32a6e4e5915`.
- Re-green after restore: GREEN (exit 0).

## Restore mechanism

`shutil.copy2` from the scratch backup under `/tmp/pok-gate-prompt-mutations/`,
verified by sha256 equality.
