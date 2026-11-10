# Test Plan: RAM-Based Auto-Termination — rev 2

> **rev 2 — supersedes rev 1 (commit 36a9e9c0d) per council cycle 1.**
> Binding source of truth: `architecture-recommendation.md` §4.1–§4.4 (frozen
> contracts), §5 (failure-path rulings — **every row is tested**), §8 (the five
> mandated Phase-6 additions + the Success-Criterion-3 re-aim). Where this file and
> that document disagree, **that document wins**.

Companion to `plan-overview.md` and `phase6-plan.md`. This file is the **outline** —
cases, harness shape, and the fencing rules. Phase 6 carries the execution order
and the mutation-proof gates.

---

## 1. Test Harness Principles

1. **Real process trees, tiny ceilings.** The tests must use REAL processes that
   genuinely allocate (a mocked `/proc` proves the arithmetic, not the behavior).
   But the ceilings are **injected and small** — MiB-scale, never the shipped
   **3 GiB / 3 072 MiB** default. The production default is never used in a test.
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
   - the `_spawn_teardown` context manager and `_wait_for_pidfile` harness from
     `tests/tools/test_auto_kill_integration.py`
   - the `kill_fence` hard-raise fixture + `kill_census` from
     `tests/unit/services/test_bash_spawn_reaper.py` (the right shape for the
     kill-ladder tests: pure predicates, no real signals)

### 1.1 Fixture stat-line format (pinned)

The reader parses `/proc/<pid>/stat` with the `rfind(")")` split. A fixture author
who mis-shapes a line silently gets wrong membership. The format is therefore
**documented and pinned**:

```
<pid> (<comm>) <state> <ppid> <pgrp> <session> <tty_nr> <tpgid> <flags> … <starttime> <vsize> <rss_pages> …
```

After the `rfind(")")` split, `tail` starts at **field 3**, so:
`tail[0]`=state · `tail[1]`=ppid · `tail[2]`=pgrp · `tail[19]`=**starttime**
(field 22) · `tail[21]`=**rss_pages** (field 24). `comm` may contain spaces and
parentheses ⇒ the split must use the **last** `)`. Zombies (`state` starting `Z`)
and kernel-thread lineage (`ppid` ∈ {0, 2}) are **excluded** from membership.

## 2. Fencing Rules (baked into every spawning test)

These are the house hazards that have caused real incidents. They are **not**
optional and are **not** rediscovered per test:

| Hazard | Rule | Incident precedent |
|---|---|---|
| Boot sweep kills live processes | **Pin `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` EXPLICITLY.** `env -u` on it STRIPS the ambient guard and the sweep kills live processes | 2026-10-31 staged-smoke S2 — SIGTERM+SIGKILL of a live launcher+daemon pair; systemd auto-restart, `NRestarts=1` |
| `env -u POSTGRES_*` is a **silent NO-OP** | Scrub **per variable**: `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT <cmd>` — bash does not glob env names | 2026-10-21 running-zombie gate lane |
| **The live daemon's DB config** | **Never resolve `data/ensemble.json`** — it is the live daemon's runtime connection config (holds live postgres credentials) and `data/` is gitignored. Tests that touch configuration must use a fixture-supplied path, never the live one | house `data/` is gitignored; the file is live-host-only |
| Live-shaped `HOME` paths | Use a `mktemp` **FAKE_HOME** + an escape guard. Never a `HOME`-derived install dir | 2026-10-12 — a test deleted the live install **twice** |
| Auth env leakage | Scrub the **full** `ENSEMBLE_AUTH_*` family **or none**. A partial scrub also breaks collection (≈100 phantom reds) | House Tier-2 fence — whole-tree-base-proven-diff omits `ENSEMBLE_AUTH_*` |
| TypeScript outside `frontend/` | Not applicable (no TS in this feature) — recorded so a later phase does not import the pattern | 2026-10-03 — bare `npx tsc` fetched and executed a registry package |
| Staged-boot path derivation | Not applicable (no daemon boot in tests) — recorded | Staged boot smokes resolve the LIVE install root |

**Assertion discipline:** the fence helper must be **asserted to have applied** (e.g.
the FAKE_HOME sentinel is present in the child's environment), not merely invoked.

## 3. Test Cases

### 3.1 Substrate (pure predicates, unit tier — no subprocesses, no real RAM)

| # | Case | Method | Expected |
|---|---|---|---|
| S1 | Single-process tree reading | Fake `proc_root` with one `/proc/<pid>/status` carrying `VmRSS` | The ruled total, in **bytes** |
| S2 | Tree of children | Fake tree: parent + N children with distinct `VmRSS` | The sum per the ruled metric's semantics |
| S3 | Shared-page double-count semantics | Two fake members with identical RSS | The result matches the ruled metric's definition exactly — the **8.9× over-count shape is pinned** so the semantics cannot drift silently |
| S4 | Unreadable `/proc` | A `proc_root` with an unreadable member | The reader's fail-mode — never a silent `0`; the pid is recorded in `unreadable_pids` and `complete=False` |
| S5 | Member-state semantics | READABLE / **ZERO** (status OK, no `VmRSS:` line) / **GONE** (ENOENT) / **UNREADABLE** | ZERO → 0, still complete; GONE → 0, still complete; UNREADABLE → 0, `complete=False` |
| S6 | Enumeration failure | A scan that aborts (`scan_complete=False`) | The sample is **dropped** — the peak keeps its value, **no breach is evaluated**, `enumeration_failures++`. **Never** kill on no-evidence |
| S7 | Peak monotonicity | Sample a shrinking tree | The recorded peak **does not decrease** |
| S8 | **All of P0–P8** (the kill predicates) | One case per predicate: P0 switch+Linux+registered · P1 pgid from this guard's registry · P2 `pgid > 1` · P3 `pgid != os.getpgid(os.getpid())` (the **daemon's group**, not its pid) · P4 `pid != os.getpid() ∧ pgid != os.getpid()` · P5 leader `starttime` match (unreadable ⇒ **fail-closed** ⇒ treat as exited) · P6 ownership stamp (mismatch ⇒ abort; unreadable ⇒ proceed + `ownership_unverified++`) · P7 group not dead · P8 post-kill probe ⇒ `tree_complete` | Every predicate is exercised individually; a kill fires **iff all** evaluate true; **"unevaluable" ≠ true**; the `kill_fence` fixture hard-raises on a real `os.killpg` |
| S9 | Breach boundary | Totals of `ceiling − 1`, `ceiling`, `ceiling + 1` | Breach iff `total >= ceiling` (**inclusive**) |
| S10 | Breach fires on the **CURRENT sample**, not the peak | A tree that spikes above the ceiling then falls before the next sample | The peak is **reported**; the tree is **NOT killed** |
| S11 | Incomplete-but-above-ceiling | A reading with `complete=False` whose lower bound still `>= ceiling` | The breach **proves** — the tree IS killed |
| S12 | Incomplete-below-ceiling | A reading with `complete=False` below the ceiling | **No evidence** — never "no breach" ⇒ no kill |
| S13 | OVERCOUNTED re-arm | An RSS trigger whose PSS confirm lands below the ceiling | The tree is **not** killed; it re-arms at `ceiling + (summed_rss − summed_pss)`; `rearm_threshold_bytes` is recorded; a later breach at the re-armed threshold **does** kill |
| S14 | Honest-peak bound | A sequence of samples | `under_report_bound_bytes` == the **max positive sample-to-sample delta**; the total exposure bound is `R·(Δt + grace)` |
| S15 | Unit discipline | A known `VmRSS` | `1024 kB → 1 048 576 bytes`; config `3072 MiB → 3 221 225 472 bytes` |
| S16 | Thread-home viability | Run the ladder from a bare `threading.Thread` with no event loop | The kill path completes — the guard can act without the loop |
| S17 | Tick-path silence | Drive a tick that raises | The thread survives (outer restart loop resumes it), `missed_samples` increments, and **no** log line is emitted on the tick path |
| S18 | Ladder vocabulary | Call every status/reason path | The vocabulary is exactly `{killed, already-dead, refused}` × the ruled reason set; the ladder is **idempotent** (a repeat is a no-op) |

### 3.2 Breach — bash lane (real processes, small ceiling)

| # | Case | Method | Expected |
|---|---|---|---|
| B1 | **Whole tree killed** | Spawn `python3 -c` that forks N children, each allocating past an injected 64 MiB ceiling | The direct child **and every grandchild** are dead within grace + poll window |
| B2 | **Typed envelope** | Same | The return **is** the envelope: `ok=false`, `code="ram_limit_exceeded"`, `surface="bash.exec"`, `limit_bytes`, and the ruled key set. It **parses** as JSON |
| B3 | **DECIDING VALUE** | Same, with a known allocation | The **deciding value** — `confirmed_pss_bytes`, or `observed_peak_bytes` on a **fail-open** trigger — is **`>= limit_bytes`**, AND `>= 90 %` of the known allocation. **NOT** `observed_peak_bytes >= limit_bytes`: under the ruled hybrid the RSS peak and the deciding PSS value are deliberately **distinct** quantities |
| B4 | **Honesty fields present** | Same | `peak_is_lower_bound` is true; `under_report_bound_bytes` is populated; `metric` names the metric; `tree_complete` and the member counts are present |
| B5 | **No zombies** | Same | No `Z`-state member remains in the killed group |
| B6 | **Guard registry released** | Same | The guard's registry has no entry for the pid afterwards |
| B7 | **Durable row stays OPEN** | Same | The `bash_spawn_registry` row is still `reaped_at IS NULL` — closing it would **disarm the reaper's escapee net** |
| B8 | **One-shot `mark_exit` ordering** | A RAM kill where the success arm's `mark_exit` could run first | The RAM verdict **survives** (the live row is deleted at `pid_registry.py:423`; a wrong ordering **permanently erases** the verdict) |
| B9 | **Not misattributed as a timeout** | A command killed by an external `SIGKILL` (not the guard) | The ordinary failure path is taken — **no** false RAM attribution |
| B10 | **Output truncation does not swallow the envelope** | A breach that also produces >150 000 chars of output | The typed envelope is returned, not a truncation-shaped string |
| B11 | **Exit-kind recorded** | Same | `pid_registry` shows the new exit-kind (`ram_killed`), **distinct from `timeout`**; `mark_exit` did not raise on the new kind |
| B12 | **Bytes→KB conversion** | Same | The persisted `peak_rss_kb` == `round(observed_peak_bytes / 1024)` — the two do not disagree by 1024× |
| B13 | **Leaderless tree** (leader ruling Q2) | A shell that backgrounds a ballooning grandchild and exits | The grandchild is **still watched** and killed; the watch is released **only** at group member count 0 |

### 3.3 Breach — proc lane

| # | Case | Method | Expected |
|---|---|---|---|
| P1 | Whole tree killed | The same ballooning tree via `proc_run` | All tree members dead — killed by the **guard thread**, not by the proc lane |
| P2 | RAM status | Same | `status == "killed"` **and** `ram_killed is True`; **distinguishable** from a user stop (`ram_killed is False`) |
| P3 | **THE VERDICT RIDES `proc_status`** | Same | `proc_status` carries a **`ram_breach:` line** and a flat **`ram_killed: true`** — **added as lines, never replacing the block**. The proc lane does **NOT** return a typed `ram_limit_exceeded` envelope (`proc_run` returns before any breach can exist). The existing `proc_status` block is **byte-identical** to today for a non-RAM process |
| P4 | `user_stopped` surfaced | A user-stopped process | `proc_status` shows `user_stopped: true`; a process that was not user-stopped does **not** show the line |
| P5 | **No 4th task** (leader ruling Q1) | Any proc run | The task set is **exactly three** (reader + exit + timeout) — **no** RAM watchdog task is created |
| P6 | Registration released | Cleanup + shutdown + the spawn-window orphan path | The guard registry has no entry for that pid on each path; the release is idempotent |
| P7 | Spawn-window orphan path unchanged | A process cleaned up mid-spawn | The existing orphan-kill behaviour is preserved |
| P8 | **No durable write** | A RAM kill | **No** `bash_spawn_registry` row and **no** `pid_registry` write path is added for the proc lane (DQ5) |

### 3.4 Under-limit (both lanes)

| # | Case | Expected |
|---|---|---|
| U1 | A small allocation under the ceiling | Exit code 0; output **byte-identical** to the base-commit reference; `exit_kind="success"` |
| U2 | A long-running but small process | Not killed; the guard does not interfere with the normal lifetime |
| U3 | Observability silence | No event, no counter increment, no log line |
| U4 | **Fairness (the 8.9× claim)** | A real **fork-without-exec** tree (9 members) whose summed RSS exceeds the ceiling while its summed PSS does **not** ⇒ the tree is **NOT killed** (the fair path is taken, not the RSS shortcut). **Counter-mutation (mandatory):** break the confirm (make it always fail-open) ⇒ the same tree **IS** killed ⇒ the test **reddens**. This is the single most important fairness proof in the suite |

### 3.5 Kill-switch OFF (both lanes) — the "byte-identical" contract

| # | Case | Expected |
|---|---|---|
| K1 | Bash, switch OFF | The child env carries **no** stamp; no record is written; the guard performs **no** measurement; the output is **byte-identical** to the ON path for an under-limit command (the `explicit_off_byte_identical_env_no_row` idiom) |
| K2 | Proc, switch OFF | **No extra task** is created — the task set is exactly today's three |
| K3 | Switch parsing | `0` / `false` / `no` / `off` (case-insensitive) ⇒ OFF; absent ⇒ ON; explicit anything else ⇒ ON (the `ENSEMBLE_BASH_ORPHAN_REAPER` semantics) |
| K4 | OFF + a command that WOULD breach | The command runs to completion — the switch is a complete no-op, not a partial one |
| K5 | OFF ⇒ zero DB queries | Asserted: no repository call occurs on the OFF path |
| K6 | **OFF ⇒ NO THREAD AT ALL** | The guard starts **no thread** (`thread_alive` is False, no thread object exists) and `app.state.ram_guard is None` |

### 3.6 Config ladder

| # | Case | Expected |
|---|---|---|
| C1 | Default | The configured ceiling is **3 072 MiB (3 GiB)** — the ruled default |
| C2 | Env override | The env value applies (the env tier wins over the yaml tier) |
| C3 | Out-of-range env | **Boot aborts** — house rule: bounds validated at `load_config()` OUTSIDE the try/except. **Testability:** asserted **only if** a daemon-boot harness exists; if none exists, the rung is recorded as an **explicit untestable disclosure** — neither asserted-away nor silently dropped |
| C4 | YAML literal trap | A literal in the yaml block does **not** beat the env tier (pydantic init-kwargs outrank env) |
| C5 | Per-call override | An explicit internal ceiling overrides the config default — the mechanism the whole harness depends on. **The LLM-facing tool schema gains no ceiling parameter** (asserted) |
| C6 | Units are INT-only | A human-string value (e.g. `"8G"`) is **not** accepted — house rule: sizes are ints, no human-string parsing |
| C7 | Poll-interval + grace + PSS-deadline bounds | Out-of-range values abort boot; in-range values apply; the ceiling clamp is **[64, 65536]** |

### 3.7 Negative paths & platform

| # | Case | Expected |
|---|---|---|
| N1 | **Non-Linux platform** | The guard is **inert** — no measurement, no kill; a would-breach command completes normally (Darwin = graceful non-enforcement) |
| N2 | Unreadable `/proc` during a live execution | No crash; the fail-mode per the ruling; `missed_samples++`; **one** WARN/spawn; no false breach |
| N3 | Enumeration failure mid-flight | The sample is **dropped**; no breach inferred; no kill |
| N4 | The command exits normally between samples | No breach, no false attribution |
| N5 | The tree shrinks | The recorded peak stays monotonic |
| N6 | A `setsid` escapee | **Measured** but counted in `members_unreachable`, not `members_killable`; the documented gap is **asserted as a known limitation**, not silently ignored (the bash lane has the reaper's stamp sweep as a backstop; **the proc lane has no such net**) |
| N7 | The daemon's own process group | Never a kill target (asserted by the S8 predicate cases) |
| N8 | A **stalled `smaps_rollup` read** | The confirm's 250 ms deadline fires ⇒ **fail-open ⇒ kill**; the **tick path is never blocked** (the confirm is off the tick path). The residual (a stalled confirm holds the kill path until the deadline) is **disclosed**, not silently accepted |

### 3.8 Observability, coincidence, and secrecy

| # | Case | Expected |
|---|---|---|
| O1 | One breach | Exactly **one** event, the counter increments, a `ram-guard:`-prefixed log line present |
| O2 | Event payload | Carries the envelope key set + the verdict extras (`metric`, `trigger_metric`, `confirmed_pss_bytes`, `under_report_bound_bytes`, `members_total/killable/unreachable`) |
| O3 | Secret-marker command | A command whose env overlay carries a secret marker ⇒ the marker value appears in **NO** log line, event payload, or persisted record |
| O4 | **NO ARGV ANYWHERE** | The envelope, the event payload, the log line, and the endpoint response are each scanned for the command's argv shape ⇒ **zero** occurrences in all four. **The sanitized argv form is also forbidden** (it still leaks the `--flag value` residual), and an argv **hash** is forbidden (a brute-force oracle) |
| O5 | `/control-capabilities` | **Unchanged** — the fence test passes unmodified |
| O6 | New endpoint | `GET /api/resources/ram-guard` returns the counters + the heartbeat (`enabled`, `thread_alive`, `last_tick_at`, `missed_samples`, `ticks`, `registry_depth`) and is **not** referenced by `livez`/`readyz` |
| O7 | **Coincidence — timeout wins** | A timeout races the guard ⇒ `code="timeout"` **plus** `ram_breach_observed: true` + the peak |
| O8 | **Coincidence — guard wins** | The guard races the timeout ⇒ `code="ram_limit_exceeded"` plus `coincident_timeout: true` |
| O9 | **Three-kill-path interaction** | The timeout arm, the guard arm, and the reaper are driven against one tree ⇒ exactly **one** claimant owns the verdict, the losers are recorded `coincident`, and **only one** signal set is delivered |
| O10 | **Liveness sweep** | A dead pid's entry is **dropped**; a **live-but-unreleased** entry is **kept watching**; the sweep is **bounded** and **never signals** |
| O11 | **Dead guard** | The tick body raises ⇒ the outer restart loop resumes it, the heartbeat advances, `missed_samples` climbs, and the dead-guard state is visible on the endpoint — while the tick path stays **silent** |

### 3.9 Performance measurements (recorded, not asserted)

| # | Measurement | Axis | Recorded |
|---|---|---|---|
| P1 | Per-tick shared-scan cost | **SCAN-POPULATION axis** — ≥2 populations (e.g. 250 and 1000 entries) | Median + p95 per tick; the 200 ms interval is confirmed or amended against the **worst** host pid count |
| P2 | Allocation rate **R** | Measured | The overshoot law `≈ rate × (poll + grace)` with the shipped numbers (~0.45 GiB at 1 GiB/s) |

### 3.10 Regression sweep

`tests/tools/test_bash.py`, `tests/tools/test_bash_spawn_stamp.py`,
`tests/tools/test_proc_tools.py`, `tests/tools/test_auto_kill_integration.py`,
`tests/unit/services/test_bash_spawn_reaper.py`,
`tests/test_resource_control_seam.py` — all must pass **unmodified**; the full suite
must show **net-new reds = 0** against the base commit.

**Note (arch doc §1.1 P6):** `tests/test_chart_tools_legacy_error_contract.py` is
**NOT** the DQ6 gate — it pins **chart tools only** and is inapplicable. The real
pin surface is the `proc_status` substring assertions
(`tests/tools/test_proc_tools.py:240, 257, 382, 1163, 1166-1167, 1831`) and the
`ProcessInfo.status == "killed"` attribute pins.

## 4. Mutation Proof

Each key assertion must have at least one **counter-mutation** that reddens it —
the house rule that *"a test green under its own leg's revert is not vacuous if a
COUNTER-mutation reddens it"*:

| Mutation | Must redden |
|---|---|
| Invert the breach predicate | B2, B3, P2, P3, S9 |
| Remove the kill delivery | B1, P1 |
| **Break the PSS confirm** (always fail-open) | **U4** (the fairness case — the fork tree starts dying) |
| **Return the spawn-time floor as the peak** | B3 (the honest-peak case) |
| **Move the success arm's `mark_exit` before the RAM arm** | **B8** (the verdict is erased) |
| **Close the durable row on a RAM kill** | **B7** (the escapee net is disarmed) |
| Make the OFF path still register | K1, K2, **K6** (a thread exists) |
| Add a 4th proc task | **P5** |
| Fire the event per tick | O1 |
| Leak the registry entry | B6, P6 |
| **Kill on no-evidence** (treat enumeration failure as a breach) | S6, N3 |
| Drop the OVERCOUNTED re-arm | S13 |

Report the mutation counts. **A count of 0 is a signal, not a flake** — re-aim the
assertion at the positive shape.

## 5. Explicitly NOT Tested Here

- The daemon's own memory ceiling (a unit-level concern — the routed follow-up).
- MCP-server RAM limits (out of scope).
- Host OOM behavior (kernel/systemd, not this feature).
- macOS per-child attribution (Linux-only by design — Darwin stays inert).
- The unit-cap drop-in (an operator action, not a test).
