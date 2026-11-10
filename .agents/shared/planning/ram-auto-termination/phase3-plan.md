# Phase 3: Bash Lane Enforcement — rev 2

> **rev 2 — supersedes rev 1 (commit 36a9e9c0d) per council cycle 1.**
> Binding source of truth: `architecture-recommendation.md` §4.2 (kill ladder),
> §4.3 (typed-error contract + precedence), §4.4 (verdict record), §5 (failure
> paths), §7 (seams), §8.2 (Phase-3 mandate). Where this file and that document
> disagree, **that document wins**. Rev-1 content that survives is carried forward
> with task numbers continuous (T1…T9 preserved; new work appended from T10).

## Objective

Wire the Phase 2 guard into the **bash tool** so that a bash execution whose
process tree exceeds the configured ceiling (**3 GiB / 3 072 MiB default**) is
terminated tree-wide and the calling agent receives a typed `ram_limit_exceeded`
envelope carrying the **deciding** value and the honest-peak bound. Every bash
execution below the ceiling must behave **exactly** as it does today.

**This lane goes first by ruling** (arch doc §8.2): the Oct-30 ballooners were bash
`python3` commands.

## The four registries — ambiguity RESOLVED

Rev 1 said "unregister the registry" without saying which. The bash lane touches
**four** registries. Resolved explicitly; the RAM arm's behaviour on each is fixed:

| Registry | Kind | What the RAM arm does | Why |
|---|---|---|---|
| **Guard registry** (`ram_guard.guard`) | RAM, keyed by `tracking_id` | **Release** the entry (state → `RELEASED`) | It is the guard's own bookkeeping; releasing it stops the watch |
| `pid_registry` (`daemon/services/resource_monitor/pid_registry.py`) | RAM, spawn-fed | `mark_exit(exit_kind="ram_killed", peak_rss_kb=<bytes→KB>)` — **one-shot**, the live row is deleted at `:423` | The exit-kind vocabulary is the process-outcome taxonomy; `ram_killed` **must** be added to `VALID_EXIT_KINDS` (`:127-138`) or `mark_exit` **raises** `ValueError` (`:409-413`) |
| `BashProcessRegistry` (`daemon/tools/bash.py`) | RAM, `instance_id → (pid, pgid)` | **Unregister**, mirroring the timeout arm (`:1073`) | It is the killpg-tracking map; a stale entry is a kill target for a later sweep |
| `bash_spawn_registry` (`daemon/repositories/bash_spawn/models.py`) | **durable** | **Leave the row OPEN** | The reaper's env-stamp escapee sweep **only sweeps open rows**; closing the row on a RAM kill **silently disarms the escapee net**. The row closes later via the existing `ram-tier-dedup` path at terminal cleanup (arch doc DQ5) |

## Touch points (verified anchors, arch doc §7)

| Seam | Location | What changes |
|---|---|---|
| S1 — capture block | `daemon/tools/bash.py:952-965` | Register the spawn with the guard (pid + the captured pgid + start time + the ceiling) |
| S2 — the single lifetime await | `daemon/tools/bash.py:1062-1065` (`await asyncio.wait_for(proc.wait(), timeout=…)`) | After the await, **observe** the guard's verdict — the thread may already have killed the tree |
| S3 — kill arms | `daemon/tools/bash.py:1071` (timeout arm) / `:1174-1214` (cancellation arm) / `:1223-1224` (generic-exception arm) | A **RAM arm** with its own exit-kind, inserted **between `:1088` and `:1110`** (§4.3 precedence) |
| S4 — result assembly | `daemon/tools/bash.py:1143-1160` | The RAM arm returns the envelope; it preempts the `EXIT CODE:` line (`:1155`), the timeout arm (`:1159`) and the 150 k truncation arm (`:1163-1171`) |
| S5 — register/unregister | `daemon/tools/bash.py:965` (register), `:481-486`, `:1073`, `:1128-1136`, `:1196`, `:1223` | The guard release is `finally`-shaped on **all six** exit paths (§5) |
| S6 — `mark_exit` | `daemon/tools/bash.py:1110-1123` | The RAM arm marks exit with `exit_kind="ram_killed"` and the **guard's sampled peak**, converted **bytes → KB** exactly once at this boundary |

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Thread the ceiling into the bash tool.** `ceiling_bytes` is a parameter of the guard's `register()` (the `SpawnRecord` already carries it); the config value (**3 072 MiB = 3 221 225 472 bytes**) is the default. **SECURITY RULING:** the per-call override is an **internal/test seam only** — the LLM-facing `BashInputSchema` gains **NO** ceiling parameter (an agent-visible knob would let the agent raise its own ceiling) | Phase 1 Task 7, Phase 2 Task 6 | An explicit internal ceiling is honoured; with no override the configured default applies; the tool schema is **unchanged** (asserted) |
| 2 | **Honour the kill-switch.** `ENSEMBLE_RAM_GUARD`: default ON when absent; `0/false/no/off` (case-insensitive) = OFF everywhere. OFF = a **complete no-op**: no measurement, no registration, no guard release bookkeeping, no writes, and **no guard thread at all** (the guard is started/stopped on the FastAPI lifespan; OFF ⇒ `app.state.ram_guard = None`). Follow the `ENSEMBLE_BASH_ORPHAN_REAPER` parsing semantics (`bash_spawn_reaper.py:32-60`) | Phase 1 Task 7 | With the switch OFF: no guard registration, no guard `/proc` reads, no child-env stamp, **no thread**, and the returned output is **byte-identical** to the ON path for an under-limit command |
| 3 | **Register the spawn with the guard** at S1, using the **already-captured** `pgid` (never re-derive `os.getpgid` after the fact — the TOCTOU guard the existing code respects, `bash.py:952-955`) and the already-read start time. **Verify the `pgid = proc.pid` fallback invariant** (`:955-957`, `start_new_session=True` ⇒ PGID == PID) is **test-pinned** — the guard's identity predicates depend on it | Phase 2 Task 6 | The guard holds `(pid, pgid, start_time)`; no second `getpgid` call is introduced; the fallback invariant has a test that forces the `ProcessLookupError` branch |
| 4 | **Observe the verdict after the await** (S2). The guard thread may have killed the tree while the await was pending ⇒ the await returns with a signal-death exit code. The lane distinguishes "killed by the RAM guard" from "killed by something else" via the **verdict record**, never by guessing from the exit code (`-9` is ambiguous by construction — arch doc §5) | Phase 2 Task 6 | A RAM-killed execution takes the RAM arm; a timeout-killed execution still takes the timeout arm; a signal-killed execution **not** caused by the guard is reported as an ordinary failure (**no** false RAM attribution) |
| 5 | **Implement the ONE-SHOT `mark_exit` ORDERING** (§4.3 precedence) — the RAM arm is inserted **between `bash.py:1088` and `:1110`** and **preempts, in order**: the success arm's `mark_exit` (**one-shot** — `pid_registry.py:423` deletes the live row; if the success arm fires first the RAM verdict is **permanently erased**), the `EXIT CODE:` line (`:1155`), the 150 k truncation arm (`:1163-1171`), and the timeout arm (`:1159`). It **also** performs the registry unregister the success arm does (`:1128-1136`) | Phase 2 Task 6 | A test drives a RAM kill and asserts the verdict survives the arm ordering — i.e. the success arm's `mark_exit` did **not** run first and did not erase the RAM verdict. The ordering is pinned by a test, not by inspection |
| 6 | **Return the typed envelope** per §4.3 — the envelope **IS** the entire bash return string; `code="ram_limit_exceeded"`, `surface="bash.exec"`, numerics as **siblings before `message`**. The **DECIDING value** assertion: assert the **deciding** value (`confirmed_pss_bytes`, or `observed_peak_bytes` on a fail-open trigger) is `>= limit_bytes` — **not** `observed_peak_bytes >= limit_bytes` (under the ruled hybrid the RSS peak and the deciding PSS value are deliberately **distinct** quantities) | Phase 2 Task 16 | The returned string parses as the envelope; the **deciding value** is `>= limit_bytes`; the RSS peak is reported separately as a sampled lower bound |
| 7 | **Pin the durable-row-stays-OPEN behaviour.** On a RAM kill the `bash_spawn_registry` row stays **OPEN** (the reaper's escapee net sweeps **open rows only**; closing it would disarm that net). The row closes later via the existing `ram-tier-dedup` path at terminal cleanup | Phase 2 Task 6 | A test asserts the durable row is still `reaped_at IS NULL` after a RAM kill, and that the reaper's escapee sweep would still consider it |
| 8 | **Release the guard entry on ALL SIX exit paths** — success, timeout, RAM, cancel, generic-exception, spawn-failure — in a `finally`-shaped release (§5). **Add the guard release to the generic-exception arm** (`:1223-1224`): that arm currently releases **neither** registry and does **not** kill the child, and `MemoryError` lands exactly there during a RAM incident | Task 3 | A test drives each of the six paths and asserts the guard registry has **no** entry for that pid afterwards |
| 9 | **Preserve the existing spawn-time floor semantics.** Do **not** change what `peak_rss_kb` means for the success/timeout paths (the spawn-time floor is an honest under-report, documented at `bash.py:1096-1109`). The RAM arm is the only path that reports a sampled peak — and it reports it as a **labelled lower bound** (`peak_is_lower_bound`, `under_report_bound_bytes`) | Task 5 | The success path's `peak_rss_kb` is unchanged (byte-identical to the base commit for an under-limit command); the RAM path's peak carries the bound fields |
| 10 | **Perform the single bytes→KB conversion at S6.** `pid_registry.peak_rss_kb` is **KB**; the guard's peak is **bytes**. One explicit conversion at that boundary, or the two numbers disagree by 1024× (arch doc §7) | Task 9 | A test asserts the persisted `peak_rss_kb` equals `round(observed_peak_bytes / 1024)` for a known allocation |
| 11 | **Implement the CLAIM TOKEN + the coincidence contract** (§4.3 / §5): a **claim token** under the guard registry lock, taken at observation (guard) or elapsation (timeout); the ladder runs **outside** the lock; the **first claimant owns the verdict**; losers skip their signal and read the verdict; the loser is recorded `coincident`. **Timeout wins** ⇒ `code="timeout"` **plus** `ram_breach_observed: true` + the peak. **Guard wins** ⇒ `code="ram_limit_exceeded"` plus `coincident_timeout: true`. Note the ruled consequence: the legacy bash timeout string changes **only when a breach was also observed** — no test pins it, so it is **recorded** | Phase 2 Task 6 | A test races a timeout against a guard kill and asserts exactly one claimant owns the verdict, the loser is `coincident`, and the envelope carries the right `ram_breach_observed` / `coincident_timeout` flags; the three kill paths (timeout arm, guard arm, reaper) never both signal |
| 12 | **Implement the three-registry release order** — guard registry → `BashProcessRegistry` → `pid_registry.mark_exit` — with each step best-effort and none of them able to abort the others | Task 8 | The order is pinned by a test that makes one step raise and asserts the other two still run |
| 13 | **Add `ram_killed` to `pid_registry.VALID_EXIT_KINDS`** as a **named constant** (never a literal — `mark_exit` raises `ValueError` on an unknown kind, `:409-413`). The constant is used by both lanes | Phase 2 Task 11 | The exit-kind set contains the new member; `mark_exit(exit_kind="ram_killed", …)` does not raise; the set is asserted as an exact set |
| 14 | **Write the bash-lane tests**: (a) a real ballooning tree — `python3 -c` that forks children and allocates past a **small injected ceiling**; (b) an under-limit command unaffected (byte-identical to the base commit); (c) the kill-switch OFF byte-identical path (mirroring `tests/tools/test_bash_spawn_stamp.py::explicit_off_byte_identical_env_no_row`); (d) the negative paths — a non-Linux platform guard, an unreadable `/proc`, an enumeration failure; (e) the **leaderless-tree** shape (a shell that backgrounds a ballooning grandchild and exits) | Tasks 1–13 | The ballooning test kills the **whole tree** (every grandchild dead) and returns the envelope with the **deciding value** `>= limit_bytes`; the host's memory is never stressed beyond the injected ceiling |

## Coupling

- **Tight with:** Phase 2 (the guard module), Phase 5 (the knob names and the
  endpoint), Phase 6 (the tests pin the envelope and the exit-kind vocabulary).
- **Loose with:** Phase 4 (the proc lane) — the lanes share only the guard module.
- **Independent of:** the reaper's own lanes — the RAM kill does **not** write into
  the reaper's `reap_reason`; the durable row simply stays OPEN (Task 7).

## Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| The success arm's `mark_exit` runs **first** and **permanently erases** the RAM verdict (one-shot delete at `pid_registry.py:423`) | **High** | Medium | Task 5 pins the arm **ordering** with a test that proves the verdict survives |
| The durable row is closed on a RAM kill ⇒ the reaper's escapee net is **silently disarmed** | **High** | Medium | Task 7 pins the row OPEN; the close happens via the existing `ram-tier-dedup` path at terminal cleanup |
| The kill arrives **before** the await is armed (the tree balloons in the spawn window) | Medium | Medium | The guard registration happens at S1, immediately after spawn; Task 4's verdict check covers a breach detected in that window |
| The 150 k truncation arm pre-empts the envelope | Low | Medium | The RAM arm returns the envelope **before** the truncation arm; a truncation-shaped return for a RAM kill is a test failure |
| A RAM kill is misreported as a timeout (both kill the tree) | Medium | Medium | Task 4 uses the **verdict record**, not the exit code; Task 11's claim token makes the race deterministic |
| The inherited 5 s grace (`_kill_process`, `bash.py:697-745`) lets the balloon continue — at 1 GiB/s that is 5 GiB of overshoot and **C_max collapses to 0.68 GiB** | **High** | Medium | The DQ4 short-grace ladder (250 ms) is used; the grace is a **separate knob**; a test asserts the wall time from breach to all-dead |
| The bash lane's `peak_rss_kb` (KB) vs the guard's peak (bytes) disagree by 1024× | **High** | Low | Task 10 pins the single bytes→KB conversion |
| **FALSIFIED (arch doc P4): the bash lane has NO ownership gate** — `_kill_process` sends `killpg` with no environ/starttime check | Medium | Medium | The guard's ladder supplies its own identity gate (**P5** leader `starttime` fail-closed; **P6** environ secondary). The bash lane's *pre-existing* `_kill_process` path is unchanged; this plan does **not** claim it is gated |
| The generic-exception arm releases **neither** registry and does **not** kill the child — and `MemoryError` lands exactly there during a RAM incident | **High** | Medium | Task 8 adds the **guard** release there. The **pre-existing** `BashProcessRegistry`/`pid_registry` leak on that arm is **disclosed, not fixed here** — routed to the Phase 7 follow-up list |
| The guard reads `/proc` for a tree that exited between samples | Low | High | The reader returns an empty/GONE reading; the sample is dropped; the tick path never logs |
| An agent-visible ceiling parameter would let the agent raise its own ceiling | **High** | Low | Task 1's SECURITY RULING: the override is an internal/test seam; the tool schema is unchanged (asserted) |

## Exit Criterion

- A bash execution that exceeds the injected ceiling is **terminated tree-wide** and
  the agent receives the envelope whose **deciding value** is `>= limit_bytes`.
- The **arm ordering** is pinned: the RAM verdict survives the success arm's
  one-shot `mark_exit`.
- The durable `bash_spawn_registry` row is **OPEN** after a RAM kill (asserted).
- The guard entry is released on **all six** bash exit paths (asserted per path).
- The claim-token race is deterministic; the coincidence flags are correct.
- An under-limit bash execution is **byte-identical** to the base-commit behavior
  (output, exit code, `exit_kind`, `peak_rss_kb` semantics, registry state).
- The kill-switch OFF path is a **complete no-op** — no measurement, no registration,
  no writes, no child-env stamp.
- The bash-lane test pack passes with **net-new reds = 0** against the base commit.
