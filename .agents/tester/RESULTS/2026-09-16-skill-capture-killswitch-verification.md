# Test Report: skill-capture kill-switch (`ENSEMBLE_SKILL_CAPTURE_ENABLED`, default OFF)

- **Date**: 2026-09-16 (UTC)
- **Branch / HEAD**: `feature/disable-skill-capture` @ `54a34199b5533ffab46346319714b2758132f270` (base `c489233e`)
- **Campaign sibling evidence commits on branch** (test-only, on top of `54a34199`): `197fe230` (independent verification suite), `68a284d0` (pack script)
- **Instance IDs**: A `12e6dc20-2a22-4376-85ee-b984563202f9` · B1 `ecac5e6b-c49a-43ba-8140-5b092c209f54` · B2 `ef8bd892-b307-456f-82ef-38f274b9a3f5` · B3 `3d279149-7a17-4608-8c27-c5372ff2a668` · B4 `69b423dd-bcef-4f9f-b463-6bbb263d1fd7` · C `3aac4a00-6b28-40f5-9db3-700364c124ca`
- **Range reconciliation**: leader context listed 4 files; actual `git diff c489233e..54a34199` = **8 files** — 3 daemon production + 5 test (dev's new suite + 4 ON-pin companions). Production delta is exactly the 3 claimed files, gate-prefix-only (+155/−2 daemon; the −2 are return-type widening `str → str | None` on `enqueue_capture` + a docstring line).

## Summary
- **Packs**: 5 runs, 755 collected / 754 passed / 0 branch-caused failures (1 quarantined flake, see below)
- **ensure.md**: Core 4/4 Critical PASS (scoped)
- **New tests added by this campaign**: 49 (independent behavioral suite) + 1 pack script
- **Production defects found**: **0**
- **Verdict**: **PASS — merge-ready from a testing standpoint** (live-daemon soak items remain post-activation, per mission scope)

### Scope Decision
> Small, isolated change (3 daemon files + 5 test files, single subsystem, no architecture impact) → scoped run: kill-switch pack (new suite + 4 ON-pin suites), skill services pack, skill tools family pack, concurrency pack (ensure.md Core), independent behavioral suite. Full suite NOT warranted. Skipped: repo/PG parity packs, FE packs, E2E release gate (no FE/PG/schema change; Release Gate criteria not met).

## Pack results

| Pack | Worker | Result | Counts | Runtime |
|---|---|---|---|---|
| `skill_capture_killswitch_unit_test` (NEW, script @ `68a284d0`) | B1 | ✅ PASS (×2 runs, deterministic) | 117/117 | 1.4s |
| `skill_services_unit_test` (registered) | B2 | ✅ PASS (quarantine-aware) | 356/357 — 1F quarantined flake | 5.3s |
| `skill_tools_family_unit_test` (ad-hoc: grep family of `skill_evolution_tools` + `tests/tools/test_skill_*.py`) | B3 | ✅ PASS | 135/135 | 1.9s |
| `concurrency_atomic_unit_test` (registered, ensure.md Core #2/#3) | B4 | ✅ PASS | 98P/74S/0F | 13s |
| `test_skill_capture_killswitch_verification.py` (NEW independent suite @ `197fe230`) | C | ✅ PASS | 49/49 | 0.26s |

**The 4 autouse ON-pin suites** (identified empirically, all carry `_enable_skill_capture_killswitch` autouse fixtures): `tests/integration/test_skill_capture.py`, `tests/integration/test_skill_cross_phase_flow_c.py`, `tests/services/test_skill_job_dispatcher.py`, `tests/tools/test_skill_evolution_tools.py` — all executed green inside B1's pack (117/117).

**Flake adjudication (B2)**: `tests/services/test_skill_evolution_service.py::TestCheckABTestResolution::test_ab_resolution_force_resolve` — 1F in pack (`InvalidRequestError: Could not refresh instance @ daemon/repositories/skill/repository.py:351`). Dual QUARANTINE.md match (row 21 = exact test name, flaky since 2026-08-30; row 55 family = exact error signature on sibling test). Solo re-runs 2/2 PASS. Range diff touches none of the 3 involved files. → pre-existing-quarantined, branch-exonerated. QUARANTINE row 21 amended with 2026-09-16 observation.

## Mission-item evidence

### 1. Default-OFF end-to-end — CONFIRMED (code + behavior)
- Code (A): resolver `skill_job_dispatcher.py:74-108` — `raw is None → False`; all 3 gates prefix-positioned BEFORE any side effect (dispatcher gate :447 → job INSERT :457; metrics gate :595 → first I/O :627, enqueue :707; tool gate :398 → `_invoke_service` :422). Per-call `os.environ.get` — no module-level cache (single `os.environ` read, inside resolver only).
- Behavior (C, 49/49): with env unset — resolver False; `_check_capture_eligibility` skips with zero pre-gate calls (`has_applied_for_instance` not called, `check_and_capture` not called, `enqueue_capture` not called); `enqueue_capture` returns None with no job-service enqueue; `skill_execute_capture` returns skipped envelope with no `capture_skill` invocation; belt-and-braces combined test: all 3 seams silent AND `skill_create` succeeds in the same scenario.

### 2. Enable path (`1`, `true`) — CONFIRMED
- C: parametrized ON-path tests at resolver + dispatcher boundary with full kwarg contract; capture proceeds to the real enqueue boundary when ON.
- B1: all 4 autouse ON-pin suites (old capture-flow expectations) green — 117/117.
- A: `git diff c489233e..54a34199` daemon hunks are gate-prefix additions only; ON-path logic untouched (only −2: return-type widening + docstring).

### 3. Edge cases / token matrix / invalid containment — CONFIRMED
- C matrix (parametrized): **ON ×14** — `1`,`true`,`TRUE`,`True`,`yes`,`Yes`,`YES`,`on`,`On`,`ON`,` 1 `,` true `,`  on  `,`\tYES\n` (case-insensitive + whitespace-stripped). **OFF ×14** — unset, `""`,`   ` (whitespace-only),`0`,`false`,`False`,`FALSE`,`off`,`OFF`,`no`,`NO` + case variants. **INVALID ×7** — `garbage`,`2`,`enabled`,`tru`,`1.0`,`banana`,`yesno` → `ValueError` raised by resolver.
- Containment (C + A): metrics seam — `record_task_completion` completes normally with env=`garbage`, WARNING logged (`CAPTURED eligibility check failed`), usage metrics still written (insert happens before try-block), zero enqueues; tool seam — `ValueError` is what escapes `skill_execute_capture`; production containment confirmed structurally: sole ToolNode construction site `daemon/services/long_tool_nudge.py:596` sets `handle_tool_errors=True` and the agent graph uses it via `wrapped_tools_node` (`daemon/graph.py:10775`); `except Exception` at `skill_metrics_service.py:497-501` wraps the eligibility call (ValueError ⊂ Exception).

### 4. Mock fidelity — CONFIRMED with 1 minor tripwire
- Signatures match real code (`enqueue_capture(project_id, task_details) -> str | None`; `_check_capture_eligibility` 7 params; `SkillEvolutionService` 8-arg ctor; `_invoke_service` positional match). All awaited boundaries use AsyncMock (no plain-Mock-await hazards).
- Gate-position assertions are side-effect-based (assert_not_called on enqueue/DB boundaries) — a gate moved after its side effect WOULD be caught.
- ⚠️ Minor: exactly one exact-positional pin `service.capture_skill.assert_awaited_once_with(...)` at `tests/unit/test_skill_capture_killswitch.py:784` — breaks if `capture_skill` ever gains a third positional arg. Non-blocking; recommend `assert_awaited_once()` + `.await_args` shape check (reviewer's deferred backlog territory).

### 5. Unaffected features — CONFIRMED
- Structural (A): zero hits for flag/resolver/constant outside the 3 modified files — `skill_tools.py` (skill_create/search/view/list/fix), `skill_injection_service.py`, `skill_trigger_engine.py`, `skill_evolution_service.py` (A/B), `manager.py` all clean.
- Behavioral (C): `skill_create` invoked end-to-end with flag OFF → creation proceeds (mocked store boundary only); skill_injection_service source-grep pin (hard path).

### 6. Independent run — DONE (see pack table; all via `uv run python -m pytest` from worktree root, drift-pinned @ `54a34199`, dual-layer timeouts, no `-x`)

## ensure.md Validation Results (Core, scoped)
- **Critical**
  - ✅ No regressions in changed packs — all scoped packs PASS (1 quarantined flake excluded per quarantine-aware rule)
  - ✅ Deadlock/concurrency integrity — `concurrency_atomic_unit_test` 98P/74S/0F
  - ✅ No sync DB calls on event loop — same pack (thread-identity tests) PASS
  - ✅ `dev.sh --timeout-graceful-shutdown 10` — static grep confirmed @ dev.sh:102, untouched by range
- **Important**: async-await callers check — out of blast radius (the 3 named functions' modules untouched); deadlock parent→child — covered green by concurrency pack
- **Nice-to-have**: dead-code check — N/A (change is additive; only deletions are a type annotation + docstring line)
- **Release Gate**: NOT triggered (not a big/critical/architecture change)
- **Contradiction notices**: none (all validations ran as packs with dual-layer timeouts)

## Observations (non-blocking)
1. **Policy note (leader's call)**: this feature introduces a new user-togglable env flag (`ENSEMBLE_SKILL_CAPTURE_ENABLED`). Repo owner policy (convention (n), enforced 7d5285aa) states bugfixes/improvements ship always-on with NO new ENSEMBLE_* flags. Default-OFF gating of a NEW behavior may qualify as a sanctioned feature knob rather than a bugfix toggle — flagging for explicit confirmation only.
2. `enqueue_capture` return-type widened `str → str | None`; sole caller `check_and_capture` ignores the return (verified by grep + docstring). No static type-check gate exists in the suite (mypy/pyright not run) — residual risk accepted as out of scope.
3. B1 addopts trap (recorded in LESSONS/2026-09-16): default `addopts = "-m 'not integration and not postgres'"` silently deselects integration-marked ON-pin files from a mixed pack — the new pack script overrides addopts after verifying 0 postgres-marked tests.

## Gaps (soak-class, none blocking — from A's analysis + mission scope)
- Mid-flight env flip during a queued job / live-daemon pre-flip job re-dispatch — post-activation soak (mission-declared out of scope)
- No end-to-end lifecycle test asserting zero persisted `skill_capture` job rows with flag OFF (per-gate no-enqueue is proven; whole-lifecycle is soak)
- ToolNode `handle_tool_errors=True` verified structurally (single construction site), not by instantiating a real ToolNode around `skill_execute_capture`
- `work_id=job_id` dispatcher tripwire not re-pinned in the new suites (covered by pre-existing `tests/services/test_skill_job_dispatcher.py`, green)

## Code Changes Summary (this campaign — test files only, all committed)
- `tests/unit/test_skill_capture_killswitch_verification.py` — NEW, 951 lines, 49 tests — commit `197fe230`
- `test/packs/skill_capture_killswitch_unit_test.sh` — NEW pack script (5-file set, dual-layer timeout, addopts override) — commit `68a284d0`

## Documentation Updated
- [x] RESULTS/2026-09-16-skill-capture-killswitch-verification.md — this report
- [x] PACKS.md — campaign gate bullet + new pack registration
- [x] LESSONS/2026-09-16-addopts-deselection-integration-onpin-packs.md — addopts partial-deselection trap
- [x] QUARANTINE.md — row-21 addendum (2026-09-16 recurrence observation)
- [ ] rules/ensure.md — no changes (user-maintained)

### Overall Status
- Unit/pack runs: ✅ PASS (0 branch-caused failures)
- Independent behavioral suite: ✅ PASS 49/49
- ensure.md Core: ✅ 4/4 Critical
- **Testing Complete: ✅ READY** (merge-ready; post-activation soak items listed above)
