# Phase 3: Bash Lane Enforcement

## Objective

Wire the Phase 2 guard into the **bash tool** so that a bash execution whose process
tree exceeds the configured ceiling is terminated tree-wide and the calling agent
receives a typed `ram_limit_exceeded` error carrying the honest observed peak. Every
bash execution below the ceiling must behave **exactly** as it does today.

## Touch points (verified anchors)

| Seam | Location | What changes |
|---|---|---|
| S1 — capture block | `daemon/tools/bash.py:952-965` | Register the spawn with the guard (pid + pgid + the captured start time) |
| S2 — the single lifetime await | `daemon/tools/bash.py:1062-1065` (`await asyncio.wait_for(proc.wait(), timeout=…)`) | After the await, **observe** the guard's verdict (the thread may already have killed the tree) |
| S3 — kill arms | `daemon/tools/bash.py:1071` (timeout arm) / `:1174-1214` (cancellation arm) | A **third arm** — the RAM arm — with its own `exit_kind`; it must not be conflated with `timeout` |
| S4 — result assembly | `daemon/tools/bash.py:1143-1160` | The RAM arm returns the typed envelope instead of the plain `content` |
| S5 — register/unregister | `daemon/tools/bash.py:965` (register), `:481-486`, `:1073` | The guard's registration is released on **every** exit path (success, timeout, RAM, cancellation) |
| S6 — `mark_exit` | `daemon/tools/bash.py:1110-1123` | The RAM arm marks exit with the new exit-kind and the **guard's sampled peak** (not the spawn-time floor) |

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Thread the ceiling into the bash tool.** The effective ceiling is a **runtime parameter** (Phase 1 Task 7 — the test harness cannot restart the daemon with a small ceiling), defaulting to the configured value | Phase 1 Task 7, Phase 2 Task 4 | A bash call with an explicit ceiling uses it; with no explicit ceiling, the configured default applies; the default path is byte-identical to today |
| 2 | **Honour the kill-switch.** When OFF, the bash lane takes **no measurement, no registration, no writes** — a complete no-op. Follow the `ENSEMBLE_BASH_ORPHAN_REAPER` precedent (`bash_spawn_reaper.py:32-60`): default ON when absent; explicit `0/false/no/off` (case-insensitive) = OFF everywhere | Phase 1 Task 7 | With the switch OFF: no guard registration, no `/proc` reads by the guard, no child-env stamp, and the returned output is **byte-identical** to the ON path for an under-limit command (mirrors `tests/tools/test_bash_spawn_stamp.py::explicit_off_byte_identical_env_no_row`) |
| 3 | **Register the spawn with the guard** at S1, using the **already-captured** `pgid` (never re-derive `os.getpgid` after the fact — the TOCTOU guard the existing code already respects, `bash.py:952-955`) and the already-read start time | Phase 2 Task 7 | The guard holds `(pid, pgid, start_time)`; no second `getpgid` call is introduced |
| 4 | **Observe the verdict after the await** (S2). The guard thread may have killed the tree while the await was pending ⇒ the await returns with a signal-death exit code. The lane must distinguish "killed by the RAM guard" from "killed by something else" via the guard's verdict record, not by guessing from the exit code | Phase 2 Task 4 | A RAM-killed execution takes the RAM arm; a timeout-killed execution still takes the timeout arm; a signal-killed execution **not** caused by the guard is reported as an ordinary failure (no false RAM attribution) |
| 5 | **Add the RAM kill arm** (S3): a distinct `exit_kind` (the DQ4 vocabulary — `ram_killed`), the registry unregister, and the `mark_exit` call carrying the guard's sampled peak | Phase 1 Task 4 | `pid_registry` shows `exit_kind="ram_killed"` (or the ruled string) and a `peak_rss_kb` **greater than** the spawn-time floor for a ballooning command |
| 6 | **Return the typed error** (S4) per the DQ6 ruling — the `{ok:false, code, surface, message, …}` envelope, `code="ram_limit_exceeded"`, `surface="bash.exec"`, plus `limit_bytes` / `observed_peak_bytes` / `pid` / `pgid` | Phase 1 Task 6 | The returned string parses as the ruled envelope; `observed_peak_bytes >= limit_bytes`; the message names the observed peak in human units |
| 7 | **Release every registry entry on every exit path** (S5) — success, timeout, RAM, cancellation, and the generic exception arm. The existing code has a **documented leak** on the success path that was fixed once (`bash.py:1090-1110`); the new arm must not reopen it | Task 3 | After a RAM kill, the guard's registry has **no** entry for that pid; asserted for all five exit paths |
| 8 | **Preserve the existing spawn-time floor semantics.** Do **not** change what `peak_rss_kb` means for the success/timeout paths (the spawn-time floor is an honest under-report, documented at `bash.py:1096-1109`) — the RAM arm is the only path that reports a sampled peak | Task 5 | The success path's `peak_rss_kb` is unchanged (byte-identical to the base commit for an under-limit command) |
| 9 | **Write the bash-lane tests**: (a) a real ballooning tree — `python3 -c` that forks children and allocates past a **small injected ceiling**; (b) an under-limit command unaffected; (c) the kill-switch OFF byte-identical path; (d) the negative paths — a non-Linux platform guard, an unreadable `/proc`, an enumeration failure | Tasks 1–8 | The ballooning test kills the **whole tree** (every grandchild dead) and returns the typed envelope; the test host's memory is never stressed beyond the injected ceiling |

## Coupling

- **Tight with:** Phase 2 (the guard module), Phase 5 (the error shape and the knob
  names), Phase 6 (the tests pin the error string and the exit-kind vocabulary).
- **Loose with:** Phase 4 (the proc lane) — the lanes share only the guard module,
  not the wiring.
- **Independent of:** the `bash-tool-orphan-reaper` plan's lanes — the guard's kill
  must NOT write into the reaper's `bash_spawn_registry` `reap_reason` unless DQ10
  ruled for durable verdicts (that vocabulary is a CLOSED SET,
  `daemon/repositories/bash_spawn/models.py:180-196`).

## Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| The new arm reopens the **registry leak** the success path once had | Medium | Medium | Task 7 asserts an empty guard registry after **each** of the five exit paths, not just one |
| A RAM kill is misreported as a timeout (both kill the tree) | Medium | Medium | Task 4 requires the verdict record — not the exit code — to select the arm; the exit code alone is insufficient |
| The kill arrives **before** the await is armed (the tree balloons in the spawn window) | Medium | Medium | The guard registration happens at S1, immediately after spawn; a breach detected in that window still kills, and the await then returns a signal death — Task 4's verdict check covers it |
| The 150 000-char output truncation arm pre-empts the typed error | Low | Medium | The RAM arm returns the envelope **before** the truncation arm; a truncation-shaped return for a RAM kill is a test failure |
| The 5 s SIGTERM grace inherited from `_kill_process` (`bash.py:697-745`) lets the balloon continue | High | Medium | The DQ4 ruling's short-grace ladder is used; the grace is a **separate knob** from the timeout grace; a test asserts the wall time from breach to all-dead |
| The guard reads `/proc` for a tree that has already exited (a race on every fast command) | Low | High | The reader returns `None`/empty for an exited tree; the verdict record is closed on the normal exit path; the guard must not log per-tick (no log spam) |

## Exit Criterion

- A bash execution that exceeds the injected ceiling is **terminated tree-wide** and
  the agent receives the typed envelope with an honest peak.
- An under-limit bash execution is **byte-identical** to the base-commit behavior
  (output, exit code, `exit_kind`, `peak_rss_kb` semantics, registry state).
- The kill-switch OFF path is a **complete no-op** — no measurement, no registration,
  no writes, no child-env stamp.
- The guard's registry is **empty** after every exit path.
- The bash-lane test pack passes with **net-new reds = 0** against the base commit.
