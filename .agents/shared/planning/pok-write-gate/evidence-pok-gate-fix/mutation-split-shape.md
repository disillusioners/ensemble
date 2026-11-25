# Mutation evidence — prompt pin: MERGED -> SPLIT (house call roles)

**Artifact for:** pok-write-gate Phase 7/8, prompt half — the split judge
prompt carrier (system vs user roles).

**Status:** RED CONFIRMED (2026-11-25).

**Harness:** `scripts/pok_gate_prompt_mutations.py` (leg
`merged-into-one-message`).

---

## The claim under test

`build_judge_prompt` returns a `JudgePrompt(system, user)` carrier and
`_house_llm_call` forwards the two halves as two chat messages with
roles `system` and `user`. Folding the system half into the user message
must make the pin RED.

## Procedure

Copy -> mutate -> RED -> sha256-verified restore -> GREEN (NEVER git
stash; scratch under `/tmp`).

- Target file: `daemon/services/pok_gate.py`
- Mutation: fold the system half into the user message:
  `messages=[{"role": "system", "content": _system_content},
            {"role": "user", "content": _user_content}]`
  -> `messages=[{"role": "user", "content": _system_content + "\\n" + _user_content}]`
- Test that went RED:
  `tests/unit/services/test_pok_gate_unit.py::TestPromptClauses::test_the_house_call_sends_system_and_user_roles`

## Result

- Baseline (split present): GREEN (exit 0).
- Mutated (merged): RED (exit 1) —
  `FAILED tests/unit/services/test_pok_gate_unit.py::TestPromptClauses::test_the_house_call_sends_system_and_user_roles`
- Restore: `shutil.copy2` from the scratch backup; sha256 before =
  after = `bc693bdf06beff6537bc28773e4726480e9c1ee198d3fe857351f32a6e4e5915`
  (identical).
- Re-green after restore: GREEN (exit 0).

## Restore mechanism

`shutil.copy2(backup, target)` from
`/tmp/pok-gate-prompt-mutations/merged-into-one-message.pok_gate.py.bak`,
verified by sha256 equality (before == after).
