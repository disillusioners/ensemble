# Phase 6: Bounded Test Suite

## Objective

Prove the feature with **real process trees that genuinely balloon**, without ever
OOM-ing the test host. The suite must demonstrate: a breach kills the whole tree and
returns an honest typed error; an under-limit command is unaffected; the kill-switch
OFF is byte-identical; the config ladder behaves per house rules; and the negative
paths fail safe.

The full outline (cases, harness, fencing) is in **`test-plan.md`**; this phase
carries the **execution order and the exit gates**.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Build the bounded harness** — a fixture that spawns a real ballooning process tree with an **explicitly bounded** allocator (sized in MiB, computed from the injected ceiling with a safety factor), asserts the host's available memory before spawning, and always tears the tree down. Reuse the existing idioms: the autouse `_reap_spawned_subprocesses` fixture (`tests/tools/conftest.py:196-207`) and the `_EnvCapture` spy (`tests/tools/test_bash_spawn_stamp.py:69-87`) | Phase 2 Task 9 | The harness refuses to spawn if the host's free memory is below the planned allocation; every spawned pid is registered for teardown |
| 2 | **Write the fence helper** that applies the house hazards on EVERY test that spawns a process — see the fencing list in `test-plan.md` | Task 1 | Every spawning test runs under the fence helper; the helper is asserted to have applied (not assumed) |
| 3 | **Breach tests (bash lane)** — a real tree that allocates past the injected ceiling ⇒ the direct child AND every grandchild are dead; the return is the typed envelope with `observed_peak_bytes >= limit_bytes` | Phase 3, Task 1 | All tree members dead within the grace + poll window; the envelope parses; the peak is honest |
| 4 | **Breach tests (proc lane)** — the same via `proc_run`; the status is the RAM status and the tree is dead | Phase 4 | Same guarantees; the RAM status is distinguishable from a user stop |
| 5 | **Under-limit tests (both lanes)** — a command allocating a small amount under the ceiling completes normally: output byte-identical to the base commit, exit code 0, `exit_kind="success"`, no event, no counter increment | Phase 3, Phase 4 | Byte-identical output against a base-commit reference; no observability side effects |
| 6 | **Kill-switch OFF tests (both lanes)** — mirroring `test_bash_spawn_stamp.py::explicit_off_byte_identical_env_no_row`: with the switch OFF, the child env carries no stamp, no record is written, no task is created (proc), no measurement occurs, and the output is byte-identical to the ON path | Phase 3 Task 2, Phase 4 Task 2 | Byte-identical output; zero writes; zero extra tasks; zero guard reads |
| 7 | **Config ladder tests** — default (8 GiB), env override applies, out-of-range env **aborts boot** (asserted, per house rule), the yaml block does not beat the env tier, and the per-call override wins over the config default | Phase 5 Tasks 4–7 | Each rung behaves as specified; the abort case is asserted rather than asserted-away |
| 8 | **Negative-path tests** — non-Linux platform ⇒ inert (no measurement, no kill, the command completes normally); unreadable `/proc` ⇒ the reader's fail-mode per DQ1/DQ3; enumeration failure (`None`) ⇒ the fail-mode per the ruling; a command that exits normally between samples ⇒ no false breach; a tree that shrinks ⇒ the peak stays monotonic | Phase 2, Phases 3–4 | Every negative path has a defined, asserted outcome; **no** false breach is ever attributed |
| 9 | **Regression sweep** — run the full `tests/` suite against the base commit and confirm **net-new reds = 0**. Specifically re-run: `tests/tools/test_bash.py`, `tests/tools/test_bash_spawn_stamp.py`, `tests/tools/test_proc_tools.py`, `tests/tools/test_auto_kill_integration.py`, `tests/unit/services/test_bash_spawn_reaper.py`, `tests/test_resource_control_seam.py`, `tests/test_chart_tools_legacy_error_contract.py` | Tasks 3–8 | Net-new reds = 0; the listed packs pass **unmodified** |
| 10 | **Mutation-prove the key assertions** — the house rule is that *"a test green under its own leg's revert is not vacuous if a COUNTER-mutation reddens it"*. Re-aim at the **positive shape**: invert the breach predicate and confirm the breach test reddens; remove the kill and confirm the tree-survival test reddens; report the mutation counts | Task 9 | Each key assertion has **at least one** counter-mutation that reddens it; the mutation counts are reported (a count of 0 is a signal, not a flake) |

## Coupling

- **Tight with:** Phases 3, 4, 5 (the tests pin the error string, the exit-kind
  vocabulary, the knob names, and the event/counter names).
- **Tight with:** Phase 2 (the substrate's pure-predicate pack is the inner layer of
  this suite).
- **Loose with:** Phase 1 (the rulings shape the cases; the suite does not feed
  back into the rulings).
- **Independent of:** Phase 7.

## Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| **The test OOMs the host** (the suite's own failure mode is the incident it tests) | **High** | Medium | The ceiling is **injected and small** (MiB-scale, never the 8 GiB default); the allocator size is **explicitly bounded in the test source**; the harness checks free memory before spawning; the teardown always runs |
| A test leaks a live ballooner into the next test | High | Medium | The autouse reaper fixture (`tests/tools/conftest.py:196-207`) plus the harness's own registration; the `_spawn_teardown` idiom from `tests/tools/test_auto_kill_integration.py` |
| The suite touches the **live** daemon or its `/proc` | High | Low | The fence helper; the reader's injectable `proc_root` is used for every unit-level read; the real-tree tests spawn only children of the test process |
| The suite runs against a **live-shaped `HOME`** | Medium | Low | `mktemp` FAKE_HOME + escape guard (the house rule after the 2026-10-12 incident) |
| The suite connects to a **production database** | High | Low | Per-variable `POSTGRES_*` scrub — `env -u POSTGRES_*` is a **silent NO-OP** (bash does not glob env names) |
| The boot sweep kills the test's own processes | Medium | Medium | Pin `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **explicitly** — `env -u` on it strips the ambient guard |
| The suite's auth env leaks and produces phantom reds | Medium | Medium | Scrub the **full** `ENSEMBLE_AUTH_*` family **or none** — a partial scrub also breaks collection |
| A vacuous test (asserts nothing, or asserts on absence only) passes while the feature is broken | High | Medium | Task 10's counter-mutation proof; assert the **positive** shape (the tree died, the envelope parses with a peak ≥ the ceiling) |

## Exit Criterion

- Real ballooning trees are killed **tree-wide** in both lanes, with an honest typed
  error.
- Under-limit commands are **byte-identical** to the base commit.
- Kill-switch OFF is **byte-identical** and side-effect-free.
- The config ladder behaves per house rules, including the **boot-abort** rung.
- Every negative path has an asserted outcome; **no** false breach is attributed.
- **Net-new reds = 0** against the base commit; the named existing packs pass
  **unmodified**.
- The key assertions are **mutation-proven** — each has ≥1 counter-mutation that
  reddens it.
- **The host survived** the suite with its memory intact — an explicit check, not an
  assumption.
