# Test Plan: RAM-Based Auto-Termination

Companion to `plan-overview.md` and `phase6-plan.md`. This file is the **outline**
— cases, harness shape, and the fencing rules. Phase 6 carries the execution order
and the mutation-proof gates.

---

## 1. Test Harness Principles

1. **Real process trees, tiny ceilings.** The tests must use REAL processes that
   genuinely allocate (a mocked `/proc` proves the arithmetic, not the behavior).
   But the ceilings are **injected and small** — MiB-scale, never the shipped 8 GiB
   default. The production default is never used in a test.
2. **The allocator size is explicit in the test source.** A test that computes its
   allocation from an ambient default can OOM the host when the default changes.
3. **The harness checks the host before it allocates.** Assert available memory
   exceeds the planned allocation by a safety factor; refuse (skip-with-reason) if not.
4. **Every spawned pid is registered for teardown**, and the teardown always runs —
   including on assertion failure.
5. **Reuse the house idioms** rather than inventing new ones:
   - the autouse `_reap_spawned_subprocesses` fixture (`tests/tools/conftest.py:196-207`)
   - the `_EnvCapture` spy on `asyncio.create_subprocess_shell`
     (`tests/tools/test_bash_spawn_stamp.py:69-87`)
   - the `_spawn_teardown` context manager from `tests/tools/test_auto_kill_integration.py`
   - the `kill_fence` hard-raise fixture + `kill_census` from
     `tests/unit/services/test_bash_spawn_reaper.py` (the right shape for the
     kill-ladder tests: pure predicates, no real signals)
   - the `_wait_for_pidfile` harness pattern (0.05 s × 80) for bounded waiting

## 2. Fencing Rules (baked into every spawning test)

These are the house hazards that have caused real incidents. They are **not**
optional and are **not** rediscovered per test:

| Hazard | Rule | Incident precedent |
|---|---|---|
| Boot sweep kills live processes | **Pin `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` EXPLICITLY.** `env -u` on it STRIPS the ambient guard and the sweep kills live processes | 2026-10-31 staged-smoke S2 — SIGTERM+SIGKILL of a live launcher+daemon pair; systemd auto-restart, `NRestarts=1` |
| `env -u POSTGRES_*` is a **silent NO-OP** | Scrub **per variable**: `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT <cmd>` — bash does not glob env names | 2026-10-21 running-zombie gate lane |
| Live-shaped `HOME` paths | Use a `mktemp` **FAKE_HOME** + an escape guard. Never a `HOME`-derived install dir | 2026-10-12 — a test deleted the live install **twice** |
| Auth env leakage | Scrub the **FULL** `ENSEMBLE_AUTH_*` family **or none**. A partial scrub also breaks collection (≈100 phantom reds) | House Tier-2 fence — whole-tree-base-proven-diff omits `ENSEMBLE_AUTH_*` |
| TypeScript outside `frontend/` | Not applicable (no TS in this feature) — recorded so a later phase does not import the pattern | 2026-10-03 — bare `npx tsc` fetched and executed a registry package |
| Staged-boot path derivation | Not applicable (no daemon boot in tests) — recorded | Staged boot smokes resolve the LIVE install root |

**Assertion discipline:** the fence helper must be **asserted to have applied** (e.g.
the FAKE_HOME sentinel is present in the child's environment), not merely invoked.

## 3. Test Cases

### 3.1 Substrate (pure predicates, unit tier — no subprocesses, no real RAM)

| # | Case | Method | Expected |
|---|---|---|---|
| S1 | Single-process tree reading | Fake `proc_root` with one `/proc/<pid>/status` carrying `VmRSS` | The ruled total, in the ruled unit |
| S2 | Tree of children | Fake tree: parent + N children with distinct `VmRSS` | The sum per the ruled metric's semantics |
| S3 | Shared-page double-count semantics | Two fake members with identical RSS | The result **matches the ruled metric's definition exactly** — if the metric double-counts, the test asserts the double count, so the semantics are pinned rather than assumed |
| S4 | Unreadable `/proc` | A `proc_root` with an unreadable member | The reader's fail-mode (per DQ1/DQ3) — never a silent `0` |
| S5 | Enumeration failure | `list_group_members`-style enumeration returning `None` | Distinguished from "tree is empty" — a breach is **never** inferred from an enumeration failure |
| S6 | Peak monotonicity | Sample a shrinking tree | The recorded peak **does not decrease** |
| S7 | Breach boundary | Totals of `ceiling - 1`, `ceiling`, `ceiling + 1` | Breach iff `>= ceiling` (the equality semantics are explicit, not implicit) |
| S8 | Kill-ladder guards | `pgid <= 1`, `pgid == daemon group`, `pgid == self` | Every guard refuses; **no** signal is sent (the `kill_fence` fixture hard-raises on a real `os.killpg`) |
| S9 | Thread-home viability | Run the ladder from a bare `threading.Thread` with no event loop | The kill path completes — the guard can act without the loop |
| S10 | Tick-path silence | Drive a tick that raises | The thread survives, and **no** log line is emitted on the tick path |

### 3.2 Breach — bash lane (real processes, small ceiling)

| # | Case | Method | Expected |
|---|---|---|---|
| B1 | **Whole tree killed** | Spawn `python3 -c` that forks N children, each allocating past an injected 64 MiB ceiling | The direct child **and every grandchild** are dead within grace + poll window |
| B2 | **Typed error** | Same | The return parses as the ruled envelope: `ok=false`, `code="ram_limit_exceeded"`, `surface="bash.exec"`, `limit_bytes`, `observed_peak_bytes` |
| B3 | **Honest peak** | Same, with a known allocation | `observed_peak_bytes >= limit_bytes` AND `>= 90 %` of the known allocation — **not** the spawn-time `VmHWM` floor (the known under-report at `bash.py:1096-1109`) |
| B4 | **No zombies** | Same | No `Z`-state member remains in the killed group |
| B5 | **Registry released** | Same | The guard's registry has no entry for the pid afterwards |
| B6 | **Exit-kind recorded** | Same | `pid_registry` shows the ruled RAM exit-kind, distinct from `timeout` |
| B7 | **Not misattributed as a timeout** | A command killed by an external `SIGKILL` (not the guard) | The ordinary failure path is taken — **no** false RAM attribution |
| B8 | **Output truncation does not swallow the envelope** | A breach that also produces >150 000 chars of output | The typed envelope is returned, not a truncation-shaped string |

### 3.3 Breach — proc lane

| # | Case | Method | Expected |
|---|---|---|---|
| P1 | Whole tree killed | The same ballooning tree via `proc_run` | All tree members dead |
| P2 | RAM status | Same | The manager reports the RAM status; it is **distinguishable** from a user stop |
| P3 | Typed error | Same | The envelope carries the ruled proc `surface` and the peak |
| P4 | Task cleanup | Same | The watchdog task is cancelled — no dangling task |
| P5 | Spawn-window orphan path unchanged | A process cleaned up mid-spawn | The existing orphan-kill behavior is preserved; the new task is in the cancellation tuple |

### 3.4 Under-limit (both lanes)

| # | Case | Expected |
|---|---|---|
| U1 | A small allocation under the ceiling | Exit code 0; output **byte-identical** to the base-commit reference; `exit_kind="success"` |
| U2 | A long-running but small process | Not killed; the guard does not interfere with the normal lifetime |
| U3 | Observability silence | No event, no counter increment, no log line |

### 3.5 Kill-switch OFF (both lanes) — the "byte-identical" contract

| # | Case | Expected |
|---|---|---|
| K1 | Bash, switch OFF | The child env carries **no** stamp; no record is written; the guard performs **no** measurement; the output is **byte-identical** to the ON path for an under-limit command (the `explicit_off_byte_identical_env_no_row` idiom) |
| K2 | Proc, switch OFF | **No extra task** is created — the task set is exactly today's (reader + exit + timeout) |
| K3 | Switch parsing | `0` / `false` / `no` / `off` (case-insensitive) ⇒ OFF; absent ⇒ ON; explicit anything else ⇒ ON (the `ENSEMBLE_BASH_ORPHAN_REAPER` parsing semantics, `bash_spawn_reaper.py:32-60`) |
| K4 | OFF + a command that WOULD breach | The command runs to completion — the switch is a complete no-op, not a partial one |
| K5 | OFF ⇒ zero DB queries | Asserted: no repository call occurs on the OFF path (the reaper's OFF guarantee) |

### 3.6 Config ladder

| # | Case | Expected |
|---|---|---|
| C1 | Default | The configured ceiling is the documented default (8 GiB unless Phase 1 amended it) |
| C2 | Env override | The env value applies (the env tier wins over the yaml tier) |
| C3 | Out-of-range env | **Boot aborts** — house rule: bounds validated at `load_config()` OUTSIDE the try/except (`config.py:911-916`, `api.py:2266-2278`). Asserted, not asserted-away |
| C4 | YAML literal trap | A literal in the yaml block does **not** beat the env tier (pydantic init-kwargs outrank env) |
| C5 | Per-call override | An explicit per-call ceiling overrides the config default (the mechanism the whole harness depends on) |
| C6 | Units are INT-only | A human-string value (e.g. `"8G"`) is **not** accepted — house rule: sizes are ints, no human-string parsing |
| C7 | Poll-interval + grace bounds | Out-of-range values abort boot; in-range values apply |

### 3.7 Negative paths & platform

| # | Case | Expected |
|---|---|---|
| N1 | **Non-Linux platform** | The guard is **inert** — no measurement, no kill; a would-breach command completes normally (Darwin = graceful non-enforcement, per Dispatcher Ruling (a)) |
| N2 | Unreadable `/proc` during a live execution | No crash; the fail-mode per the ruling; no false breach |
| N3 | Enumeration failure mid-flight | No breach inferred; no kill |
| N4 | The command exits normally between samples | No breach, no false attribution |
| N5 | The tree shrinks | The recorded peak stays monotonic |
| N6 | A `setsid` escapee | The documented gap is **asserted as a known limitation**, not silently ignored (the escapee is still reaped by the orphan reaper's stamp sweep) |
| N7 | The daemon's own process group | Never a kill target (asserted by the ladder-guard cases) |

### 3.8 Observability & secrecy

| # | Case | Expected |
|---|---|---|
| O1 | One breach | Exactly **one** event, **one** counter increment, log line(s) present |
| O2 | Event payload | Carries `{code, surface, observed_peak_bytes, limit_bytes, pid, pgid}` — **no** secret material |
| O3 | Secret-marker command | A command whose env overlay carries a secret marker ⇒ the marker value appears in **NO** log line, event payload, or persisted record |
| O4 | Sanitized command | The persisted command shape is the executable + argv-shape only (`_sanitize_command_for_registry`, `bash.py:966-979`) |
| O5 | `/control-capabilities` | **Unchanged** — the fence test passes unmodified |

### 3.9 Regression sweep

`tests/tools/test_bash.py`, `tests/tools/test_bash_spawn_stamp.py`,
`tests/tools/test_proc_tools.py`, `tests/tools/test_auto_kill_integration.py`,
`tests/unit/services/test_bash_spawn_reaper.py`,
`tests/test_resource_control_seam.py`,
`tests/test_chart_tools_legacy_error_contract.py` — all must pass **unmodified**;
the full suite must show **net-new reds = 0** against the base commit.

## 4. Mutation Proof

Each key assertion must have at least one **counter-mutation** that reddens it —
the house rule that *"a test green under its own leg's revert is not vacuous if a
COUNTER-mutation reddens it"*:

| Mutation | Must redden |
|---|---|
| Invert the breach predicate | B2, B3, P2, P3 (the breach cases) |
| Remove the kill delivery | B1, P1 (the tree-survival cases) |
| Return the spawn-time floor as the peak | B3 (the honest-peak case) |
| Make the OFF path still register | K1, K2 (the byte-identical cases) |
| Drop the task from the cancellation tuple | P4 (the task-cleanup case) |
| Fire the event per tick | O1 (the exactly-one-event case) |
| Leak the registry entry | B5 (the registry-release case) |

Report the mutation counts. **A count of 0 is a signal, not a flake** — re-aim the
assertion at the positive shape.

## 5. Explicitly NOT Tested Here

- The daemon's own memory ceiling (a unit-level concern — see the routed follow-ups).
- MCP-server RAM limits (out of scope).
- Host OOM behavior (kernel/systemd, not this feature).
- macOS per-child attribution (Linux-only by the current code — Darwin is inert).
