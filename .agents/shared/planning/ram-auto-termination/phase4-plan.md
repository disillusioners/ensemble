# Phase 4: Proc Lane Enforcement — rev 2

> **rev 2 — supersedes rev 1 (commit 36a9e9c0d) per council cycle 1.**
> Binding source of truth: `architecture-recommendation.md` §4.2 (kill ladder),
> §4.3 (typed-error contract — proc placement), §4.4 (verdict record), §5 (failure
> paths), §7 (seams), §8.3 (Phase-4 mandate). Where this file and that document
> disagree, **that document wins**, except where a **leader ruling** supersedes it
> (leader ruling **Q1** below). Rev-1 content that survives is carried forward with
> task numbers continuous (T1…T8 preserved; new work appended from T9).

> ⚠️ **LEADER RULING Q1 (council cycle 1) — supersedes the arch doc §7 parenthetical
> "(4th task)".** Reading (a) is chosen: **NO 4th asyncio task.** Rev 1's Task 4
> ("create the RAM watchdog as a 4th task, mirroring `timeout_task`") is **rejected**.
> The proc lane's shape is: **register with the guard thread → status mapping →
> release.** The guard thread (Phase 2, DQ2) owns the measurement, the breach
> decision, the kill, and the verdict record; the proc lane only **registers**,
> **maps** the verdict to status, and **releases**. The arch doc's `(4th task)`
> parenthetical in §7 is superseded by this ruling — see the addendum appended to
> `architecture-recommendation.md`.

## Objective

Wire the Phase 2 guard into the **proc tool** so that a proc execution whose process
tree exceeds the configured ceiling (**3 GiB / 3 072 MiB default**) is terminated
tree-wide and the caller can learn **why** it died. The proc verdict does **not**
ride the tool's return value — `proc_run` returns **before** any breach can exist,
so the verdict rides **`proc_status`**.

**Note the coverage asymmetry (unchanged, and now explicitly correct):** unlike
bash, the proc tool writes **no durable registry row** — only
`daemon/tools/bash.py:414` and `daemon/tools/mcp/stdio_wrapper.py:783` touch
`pid_registry`, and `proc_run` touches neither the `bash_spawn_registry` nor the
`pid_registry`. Per arch doc DQ5, the proc lane **registers with the guard only** —
**no new durable write path**, and no migration.

## Touch points (verified anchors, arch doc §7)

| Seam | Location | What changes |
|---|---|---|
| S7 — post-spawn block | `daemon/tools/proc_tools.py:1226-1245` | **NO new task.** The spawn block captures the **pgid + start_time** (new T9) and **registers with the guard thread** |
| S9 — the existing task set | `daemon/tools/proc_tools.py:1231/:1235/:1241-1245` (`reader_task`, `exit_task`, `timeout_task`) | **Unchanged** — three tasks, as today. The RAM watchdog is **not** a fourth |
| S10 — status mapping | `daemon/tools/proc_tools.py:786-801` (`_drain_exit_code`), `:855-875` (status mapping) | `ram_killed` joins `timed_out`/`user_stopped` as the **third authoritative input**; `status` stays **`"killed"`**; `ProcessInfo` gains a `ram_killed: bool` |
| S11 — kill delivery | `daemon/tools/proc_tools.py:966-1086` (`_attempt_kill_signal`) | **NOT used for the RAM kill.** The guard thread delivers the signal through the DQ4 `ram_kill_ladder` (P0–P8). The environ check here is demoted to **P6, secondary only** |
| Spawn-window cleanup | `daemon/tools/proc_tools.py:1247-1308` | The existing spawn-window re-check is **unchanged** (no new task to cancel) |
| Instance cleanup | `daemon/tools/proc_tools.py:1585-1667` (`cleanup_instance`), `:1669-1717` (`cleanup_all`) | The **guard registration** is released on both paths |

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Thread the ceiling into the proc lane**, runtime-injectable, defaulting to the configured value (**3 072 MiB**). The per-call override is an **internal/test seam only** — the LLM-facing proc tool schema gains **NO** ceiling parameter | Phase 1 Task 7 | An explicit internal ceiling is honoured; the default path is unchanged; the tool schema is unchanged (asserted) |
| 2 | **Honour the kill-switch** — OFF ⇒ no measurement, no registration, **no guard thread at all**, and **no extra task created**: the task set stays exactly today's (reader + exit + timeout) | Phase 1 Task 7 | With the switch OFF, `info`'s task set contains exactly today's three tasks; the guard registry is untouched |
| 3 | **Register the spawn with the guard** — the proc lane **registers with the guard thread** and does nothing else until the verdict is read | Phase 2 Task 6 | The guard registry holds the entry; the proc lane has **no** polling loop of its own |
| 4 | **LEADER RULING Q1 — implement the three-step lane shape: register → status mapping → release.** There is **no** asyncio watchdog task, **no** per-tick loop in the proc lane, and **no** signal delivery from the proc lane. The guard thread measures, breaches, kills (through the DQ4 ladder) and owns the verdict; the proc lane maps the verdict to status and releases the registration | Task 3 | A test asserts the proc lane creates **exactly three** tasks (unchanged from today) and that the RAM kill is delivered by the **guard thread**, not by the proc lane |
| 5 | **Add the RAM-kill status** (S10). `timed_out`/`user_stopped` are already **authoritative** status inputs (`:855-875`); `ram_killed` becomes the **third** authoritative input. `status` **stays `"killed"`** (a new status value would break 3 test pins and the post-kill branch); the RAM-ness is carried by the separate `ram_killed: bool` | Phase 1 Task 4 | A RAM-killed process reports `status == "killed"` **and** `ram_killed is True`; a user-stopped process reports `status == "killed"` and `ram_killed is False` — the two are distinguishable |
| 6 | **Deliver the kill through the DQ4 `ram_kill_ladder` (P0–P8)** — the guard thread calls it; the proc lane's `_attempt_kill_signal` is **not** the RAM delivery path. Rationale (arch doc DQ4): `_attempt_kill_signal` **re-derives** the pgid at kill time (`proc_tools.py:1058`) — exactly the TOCTOU the bash lane already fixed — and its environ gate is **fail-open**, and the bash lane has **no tracking id** so its gate would silently fail-open there. In the ladder, the **environ check is P6 and secondary only**; the identity gate is the **captured pgid + leader `starttime`** re-verification (fail-closed) | Phase 2 Task 11 | The kill path calls `ram_kill_ladder`; the ladder's P0–P8 are individually exercised (Phase 2 tests); a recycled-pid scenario yields `refused:pgid-recycled` (the existing `aborted_recycled` path is **not** the RAM path) |
| 7 | **Surface the verdict in `proc_status`** per DQ6: a **`ram_breach:` line** plus a **flat `ram_killed: true`** — **added as lines, never replacing the block**. The proc lane does **not** return a typed `ram_limit_exceeded` envelope (rev 1's Task 7 shape is rejected: `proc_run` returns before any breach can exist, so an envelope in the return value could never carry one). The `ram_breach:` line carries the **deciding value** (`confirmed_pss_bytes`, or `observed_peak_bytes` on a fail-open trigger) and the honest-peak fields (`peak_is_lower_bound`, `under_report_bound_bytes`, `metric`, `trigger_metric`), with **no argv / env / stdout anywhere** (§4.3 negative space) | Phase 2 Task 16 | `proc_status` output contains the `ram_breach:` line and `ram_killed: true`; the **existing** `proc_status` block is byte-identical to today for a non-RAM process; the four strings that must never change (the `proc_status` block, the `proc_list` header, the bash success string, the bash timeout string) are pinned |
| 8 | **Release the guard registration** on every exit path — process exit, instance cleanup (`cleanup_instance`), daemon shutdown (`cleanup_all`), and the spawn-window orphan path | Task 3 | After each path, the guard registry has **no** entry for that pid; the release is idempotent |
| 9 | **NEW (arch doc §8.3) — capture the pgid AND start_time at spawn.** Today the proc lane captures **neither**: the pgid is **re-derived at kill time** (`os.killpg(os.getpgid(proc.pid), sig)`, `proc_tools.py:1058`, `:1280`) — exactly the TOCTOU the bash lane already fixed. Capture both at spawn (the proc lane uses `start_new_session=True`, `:1179-1181`, so PGID == PID), and **stop re-deriving** | Phase 2 Task 6 | The spawn block captures the pgid once; **no** post-hoc `getpgid` remains in the RAM path; the captured value is what the ladder receives |
| 10 | **Add `user_stopped: true` to `proc_status`** while we are there (arch doc §8.3 — 🟢 one line). `user_stopped` is an authoritative status input today but is **never surfaced** to the agent — a real observability gap, and the same class of gap that `ram_killed` closes | Task 7 | `proc_status` surfaces `user_stopped: true` for a user-stopped process; a process that was not user-stopped does **not** show the line |
| 11 | **Write the proc-lane tests**: (a) a real ballooning tree via `proc_run` — the tree is dead and `proc_status` carries the verdict; (b) an under-limit command unaffected; (c) the kill-switch OFF ⇒ **no extra task** and no registration; (d) the negative paths — a non-Linux platform guard, an unreadable `/proc`, the `MAX_PROCESSES_PER_INSTANCE = 10` cap (`proc_tools.py:98`, enforced `:1130-1142`) unaffected, the spawn-window orphan path unaffected; (e) the **leaderless-tree** shape | Tasks 1–10 | The ballooning test kills the whole tree and `proc_status` carries `ram_breach:` + `ram_killed: true`; the cap and orphan paths are unchanged |

## Coupling

- **Tight with:** Phase 2 (the guard module), Phase 5 (the knob names and the
  endpoint), Phase 6 (the tests).
- **Independent of:** Phase 3 (the bash lane) — no shared wiring code. The **durable
  escapee-net question is bash-only** (the proc lane has no durable row), which is
  why Phase 4 carries **less** coexistence risk (arch doc §8.3).
- **Independent of:** the reaper's lanes — the proc lane never writes a durable row.

## Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| The proc lane's polite/force ladder (SIGTERM → 5 s → SIGKILL, `proc_tools.py:1413-1441`) is too slow for a RAM emergency | **High** | Medium | The DQ4 short-grace ladder (250 ms) is used, delivered by the guard thread |
| The RAM status is conflated with a **user stop** (both ⇒ `"killed"`) | Medium | Medium | Task 5 makes `ram_killed` a separate authoritative boolean, and Task 10 surfaces `user_stopped` too — the two are now distinguishable |
| The kill re-derives the pgid at kill time ⇒ a recycled pid is signalled | **High** | Low | Task 9 captures the pgid at spawn; Task 6 routes the kill through the ladder's P5 fail-closed identity gate |
| The proc lane's `start_process` spawns **outside** the lock and the guard registration lands after the spawn window | Medium | Medium | Registration happens immediately after the spawn block, using the **captured** pgid; the existing spawn-window re-check (`:1247-1308`) is preserved |
| A RAM kill that lands while the caller is already gone leaves a stale registration | Low | Medium | Task 8's release is idempotent and covers the cleanup paths; the guard's own bounded liveness sweep (Phase 2 T15) drops dead entries |
| The `setsid` escapee gap: the proc lane has **no** env-stamp backstop (the bash lane has the reaper's stamp sweep) | Medium | Medium | Surfaced in the operator memo (§3.5 honesty set item 2) as a **disclosed** scope difference — measured but not group-killable |
| The proc lane's 10-process-per-instance cap interacts with a RAM-killed process still occupying a slot | Low | Medium | Task 11 asserts the cap's behaviour is unchanged; the slot is released by the existing cleanup path |
| **FALSIFIED (arch doc P5): "proc captures pgid at spawn"** | — | — | Task 9 fixes it; rev 1's claim that the proc lane captures the pgid was wrong |

## Exit Criterion

- A proc execution that exceeds the injected ceiling is **terminated tree-wide** by
  the **guard thread**, and `proc_status` carries a **`ram_breach:` line** and a
  flat **`ram_killed: true`**.
- The proc lane creates **exactly three** tasks — **no fourth task** (leader ruling
  Q1) — and the RAM kill is **not** delivered by `_attempt_kill_signal`.
- `status` remains **`"killed"`**; the RAM-ness is carried by `ram_killed: bool`;
  `user_stopped` is now surfaced too.
- The pgid is **captured at spawn** and never re-derived in the RAM path.
- The **existing** `proc_status` block is byte-identical for a non-RAM process; the
  four never-changing strings are pinned.
- The kill-switch OFF path creates **no extra task**, performs no measurement, and
  registers nothing.
- The proc-lane test pack passes with **net-new reds = 0** against the base commit.
