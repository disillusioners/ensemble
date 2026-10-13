# Test-Wall Summary — Phase E Step 8 (full suite, R9)

**Invocation (proven at C.4, verbatim):** `./.venv/bin/python -m pytest tests/ --ignore=tests/packs --timeout=120 -q -n auto` — daemon UP in fenced dev lane (dev.sh, ensemble_dev @ 8079), outer `timeout 1700`, executed 2026-10-13 16:04:20Z → 16:13:57Z (562s).

| Run | Tree | failed | passed | skipped | xfailed | errors |
|---|---|---|---|---|---|---|
| base gate (Phase C re-run, throwaway base worktree) | 885a19d4f | 342 | 26,066 | — | — | 27 |
| C.4 gate | ca222f99f | 362 | 25,669 | 311 | 5 | 25 |
| **Phase E wall** | **8cefcecf3** | **344** | **25,688** | **310** | **5** | **25** |

## Failure classification (every failure ∈ exactly one bucket)

| Bucket | Count | Basis |
|---|---|---|
| **opencode-related** | **0** (MANDATORY ZERO — met) | 0 failure ids and 0 tracebacks implicate opencode; the only "opencode" strings in the log are negative-guard test names/docstrings (`test_devops_agent.py::test_no_opencode_skill_content_in_system_prompt` — its assertion failure is `{'question','chart'}` extras, present at base, zero opencode causation) |
| pre-existing base-proven | 353 unique ids | set-intersection with `phaseC/hang-recovery/base-885a19d4f-gate-xdist.log` (identical failures at BASE commit; `cls-preexisting.txt`) |
| mission-drift (root-caused at C.4) | 18 unique ids | in C.4 gate set, NOT in base set (`cls-drift-c4.txt`) — Phase-B-drift families: spawn/team registry ×3, instance_tools ×2, snapshot_tools ×1, explorer_shared_context ×1, prompt_section_reference_integrity ×2, nudge graph ×3, e2e pause/answer family ×4, config defaults ×1, tool_config_validation_boot ×1 |
| NEW/unknown → investigated | 3 → resolved | `cls-NEW.txt`: (a) `test_memory_integration.py::test_concurrent_writes_no_corruption` — xdist flake (PASSES serially); (b) `test_llm_allowed_models_precedence.py::test_neither_set_yields_documented_default` — xdist flake (PASSES serially); (c) `test_skill_evolution_service.py::test_ab_resolution_threshold_met` — CPython-3.14.7 sqlite3 `SystemError` (commit returned NULL) under xdist thread-pool load; PASSES ×3 serially; `git diff 885a19d4f..HEAD` on the skill-evolution path = EMPTY (mission touched nothing there) → environment flake, NOT mission-related |

## Named pins (spot-checks)

- Coder contamination guards (DoD #13): `tests/unit/test_coder_agent.py` **39/39 PASS** (serial).
- Phase B named set: `test_innate_skills_refactoring.py:75`, `test_agents_api.py:126,:184` — present in the pre-existing/drift sets as at C.4.

## Files

`full-wall-xdist.log` (raw) · `cls-preexisting.txt` · `cls-drift-c4.txt` · `cls-NEW.txt` · comparison baselines in `../phaseC/hang-recovery/`.

**DoD #4 verdict: PASS-WITH-PREEXISTING — 0 opencode-related, zero unexplained failures. Literal full-suite green not achievable at this repo state (342-failure base-proven set); conditional-green USER DECISION remains with the user (carried from C.4, unchanged).**
