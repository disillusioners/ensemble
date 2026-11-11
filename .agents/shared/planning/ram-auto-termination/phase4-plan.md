# Phase 4: Proc Lane Enforcement — rev 5

> **rev 5 — supersedes rev 4 (commit 0ce1bc8c8) per the escalation pass (iter-003
> rulings + the two USER RULINGS).** Binding source of truth: `architecture-recommendation.md` §4.2 (kill ladder
> + the 16-code vocabulary), §4.3 (typed-error contract — **the proc placement is
> QUERY-ONLY**), §4.3.1 item 5 (the proc lane consults the verdict **before**
> finalizing `info.status`), §4.4 (verdict record), §5 (failure paths), §7 (seams),
> §8 item 3 (the Phase-4 mandate), §14 (the amendment ledger), §15 (the strings to
> pin char-for-char), §16 (the iteration-002 ledger). Where this file and that
> document disagree, **that document wins**, except where a **leader ruling**
> supersedes it (leader ruling **Q1** below). Rev-3 content that survives is carried
> forward with task numbers continuous (T1…T12 preserved; new work appended from
> T13).

> ⚠️ **LEADER RULING Q1 (council cycle 1) — supersedes the arch doc §7 parenthetical
> "(4th task)".** Reading (a) is chosen: **NO 4th asyncio task.** The proc lane's
> shape is: **register with the guard thread → status mapping → release.** The
> guard's **two threads** (Phase 2, DQ2) own the measurement, the breach decision,
> the kill, and the verdict record — the **tick thread** measures and hands off; the
> **execution-lane thread** confirms, kills and publishes; the proc lane only
> **registers**, **maps** the verdict to status, and **releases**.

## Objective

Wire the Phase 2 guard into the **proc tool** so that a proc execution whose process
tree exceeds the **resolved** ceiling (**default: DYNAMIC — 70 % of the detected effective total RAM**, USER RULING #2, §4.7) is terminated
tree-wide and the caller can learn **why** it died.

**The proc lane's verdict is QUERY-ONLY by construction** (arch §4.3, §3.5 item 12):
`proc_run` returns `process_id, None` at `proc_tools.py:1310`, **before any breach
can exist**. The verdict therefore rides **`proc_status`** as a `ram_breach:` line
plus a flat `ram_killed: true` — added, never replacing. **The commission's phrase
"returns … error" is scoped to the bash lane**; this lane gains **no** typed error
surface, and the disclosure is mandatory (Task 7).

**Coverage asymmetry (unchanged, and now explicit):** unlike bash, the proc tool
writes **no durable registry row** — only `daemon/tools/bash.py:414` and
`daemon/tools/mcp/stdio_wrapper.py:783` touch `pid_registry`, and `proc_run` touches
neither the `bash_spawn_registry` nor the `pid_registry`. Per arch DQ5, the proc lane
**registers with the guard only** — **no new durable write path**, and no migration.

## Touch points (verified anchors, arch §7)

| Seam | Location | What changes |
|---|---|---|
| S7 — post-spawn block | `daemon/tools/proc_tools.py:1226-1245` | **NO new task.** The spawn block captures the **pgid + start_time** (new T9) and **registers with the guard thread** |
| S9 — the existing task set | `daemon/tools/proc_tools.py:1231/:1235/:1241-1245` (`reader_task`, `exit_task`, `timeout_task`) | **Unchanged** — three tasks, as today. The RAM watchdog is **not** a fourth |
| S10 — status mapping | `daemon/tools/proc_tools.py:786-801` (`_drain_exit_code`), `:855-875` (status mapping) | `ram_killed` joins `timed_out`/`user_stopped` as the **third authoritative input**; the verdict is consulted **before** `info.status` is finalized (§4.3.1 item 5); `status` stays **`"killed"`**; `ProcessInfo` gains a `ram_killed: bool` |
| S11 — kill delivery | `daemon/tools/proc_tools.py:966-1086` (`_attempt_kill_signal`) | **NOT used for the RAM kill.** The guard thread delivers the signal through the DQ4 `ram_kill_ladder` (P0–P7 + P8). The environ check here is demoted to **P6, secondary only** |
| Spawn-window cleanup | `daemon/tools/proc_tools.py:1247-1308` | The existing spawn-window re-check is **unchanged** (no new task to cancel) |
| Instance cleanup | `daemon/tools/proc_tools.py:1585-1667` (`cleanup_instance`), `:1669-1717` (`cleanup_all`) | The **guard registration** is released on both paths |

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Thread the ceiling into the proc lane**, runtime-injectable, defaulting to the configured value (**3 072 MiB**). The per-call override is an **internal/test seam only** — the LLM-facing proc tool schema gains **NO** ceiling parameter | Phase 1 Task 7 | An explicit internal ceiling is honoured; the default path is unchanged; the tool schema is unchanged (asserted) |
| 2 | **Honour the kill-switch** — OFF ⇒ no measurement, no registration, **no guard thread at all**, and **no extra task created**: the task set stays exactly today's (reader + exit + timeout) | Phase 1 Task 7 | With the switch OFF, `info`'s task set contains exactly today's three tasks; the guard registry is untouched |
| 3 | **Register the spawn with the guard** — the proc lane **registers with the guard thread** and does nothing else until the verdict is read | Phase 2 Task 6 | The guard registry holds the entry; the proc lane has **no** polling loop of its own |
| 4 | **LEADER RULING Q1 — implement the three-step lane shape: register → status mapping → release.** There is **no** asyncio watchdog task, **no** per-tick loop in the proc lane, and **no** signal delivery from the proc lane. The guard thread measures, breaches, kills (through the DQ4 ladder) and owns the verdict; the proc lane maps the verdict to status and releases the registration | Task 3 | A test asserts the proc lane creates **exactly three** tasks (unchanged from today) and that the RAM kill is delivered by the **guard thread**, not by the proc lane |
| 5 | **AMENDED (§4.3.1 item 5) — consult the verdict BEFORE finalizing `info.status`.** `_drain_exit_code` (`proc_tools.py:786-875`) consults the verdict **before** finalizing `info.status`; `ram_killed` joins `timed_out`/`user_stopped` as the **third authoritative input** — the existing ladder at `:861-875` is **preserved** and the RAM verdict is consulted **first**, because it is the only signal that says *who* killed the tree. `info.status` stays `"killed"`; `ram_killed` is `True` whenever the guard's record says `KILLED` (independently of the timeout claim — the envelope's `code` follows the claim-token rule, so a coincidence shows `code="timeout"` + `coincident_timeout:true` + `ram_killed:true`) | Phase 1 Task 4 | A RAM-killed process reports `status == "killed"` **and** `ram_killed is True`; a user-stopped process reports `status == "killed"` and `ram_killed is False` — the two are distinguishable; the **ordering** (verdict consulted before `status` is finalized) is observable, not assumed |
| 6 | **Deliver the kill through the DQ4 `ram_kill_ladder` (P0–P7 + P8)** — the guard thread calls it; the proc lane's `_attempt_kill_signal` is **not** the RAM delivery path. Rationale (arch DQ4): `_attempt_kill_signal` **re-derives** the pgid at kill time (`proc_tools.py:1058`) — exactly the TOCTOU the bash lane already fixed — and its environ gate is **fail-open**, and the bash lane has **no tracking id** so its gate would silently fail-open there. In the ladder, the **environ check is P6 and secondary only**; the identity gate is the **captured pgid + leader `starttime`** re-verification (fail-closed), with **P5a** refusing a recycled pgid | Phase 2 Task 11 | The kill path calls `ram_kill_ladder`; the ladder's P0–P7 and P8 are individually exercised (Phase 2 T22); a recycled-pid scenario yields `refused:pgid-recycled` (the existing `aborted_recycled` path is **not** the RAM path) |
| 7 | **AMENDED (approver sweep — the query-only asymmetry + Class A2, escalation pass) — surface the verdict in `proc_status`** per DQ6: a **refusal** is surfaced as the additive **`ram_kill_refused: <reason>`** line alongside the flat `ram_killed: true` (the proc lane mirrors the bash refusal surface, §15 B25; a refusal is **never swallowed**), and: a **`ram_breach:` line** plus a **flat `ram_killed: true`** — **added as lines, never replacing the block**. The `ram_breach:` line carries the **deciding value** (`confirmed_pss_bytes`, or `observed_peak_bytes` on a fail-open trigger) and the honest-peak fields (`peak_is_lower_bound`, `under_report_bound_bytes`, `metric`, `trigger_metric`), plus **`rearm_threshold_bytes`** beside `limit_bytes` — with **no argv / env / stdout anywhere** (§4.3 negative space). **The in-flight refusal surface (§15 B25), verbatim:** `ram_kill_refused: <reason>` · `ram_breach_observed: true` — *(additive lines in the bash return / `proc_status`; output is never swallowed; distinct from `code="ram_limit_exceeded"` by construction.)* **THREE MANDATORY DISCLOSURES** accompany this lane: (a) the **§3.5 item-12 sentence** in the runbook and the envelope honesty set (the verdict is QUERY-ONLY — an agent that never calls `proc_status` after a proc run **will not** see the RAM verdict in the `proc_run` result; it must poll `proc_status`, the same obligation it already has for `timed_out` and `user_stopped`); (b) the **matching line in the `proc_status` LLM-facing docstring**; (c) Phase 4's `ram_breach:` / `ram_killed: true` pins kept (they are part of the DQ6 census). The existing `proc_status` block is byte-identical to today for a non-RAM process, and the four strings that must never change (the `proc_status` block, the `proc_list` header, the bash success string, the bash timeout string) are pinned | Phase 2 Task 16 | `proc_status` output contains the `ram_breach:` line and `ram_killed: true`; the **existing** `proc_status` block is byte-identical to today for a non-RAM process; the §3.5 item-12 sentence is carried to Phase 7; the `proc_status` docstring carries the matching line |
| 8 | **AMENDED (approver iteration 002, blocker B2) — the lane exit is a NOTIFICATION, not the END of the watch.** The guard registration is released on every exit path — process exit, instance cleanup (`cleanup_instance`), daemon shutdown (`cleanup_all`), and the spawn-window orphan path — **only when the watch is finished** (`group_member_count == 0`); a **claimed** record's release is **deferred to the lane** (§15 B21: *A release of a CLAIMED record is deferred to the lane (the lane releases after publishing). A live watch is never ended by a lane exit; the liveness sweep owns the release at group_member_count == 0.*) | Task 3 | After each path, the guard registry has **no** entry for that pid **when the watch is finished**; a live watch survives the lane's exit; the release is idempotent |
| 9 | **NEW (arch §8 item 3) — capture the pgid AND start_time at spawn.** Today the proc lane captures **neither**: the pgid is **re-derived at kill time** (`os.killpg(os.getpgid(proc.pid), sig)`, `proc_tools.py:1058`, `:1280`) — exactly the TOCTOU the bash lane already fixed. Capture both at spawn (the proc lane uses `start_new_session=True`, `:1179-1181`, so PGID == PID), and **stop re-deriving**. This is a **field capture inside the existing spawn path** — **not** a fourth task and **not** a new asyncio task | Phase 2 Task 6 | The spawn block captures the pgid once; **no** post-hoc `getpgid` remains in the RAM path; the captured value is what the ladder receives |
| 10 | **Add `user_stopped: true` to `proc_status`** while we are there (arch §8 item 3 — 🟢 one line). `user_stopped` is an authoritative status input today but is **never surfaced** to the agent — a real observability gap, and the same class of gap that `ram_killed` closes | Task 7 | `proc_status` surfaces `user_stopped: true` for a user-stopped process; a process that was not user-stopped does **not** show the line |
| 11 | **Write the proc-lane tests**: (a) a real ballooning tree via `proc_run` — the tree is dead and `proc_status` carries the verdict; (b) an under-limit command unaffected; (c) the kill-switch OFF ⇒ **no extra task** and no registration; (d) the negative paths — a non-Linux platform guard, an unreadable `/proc`, the `MAX_PROCESSES_PER_INSTANCE = 10` cap (`proc_tools.py:98`, enforced `:1130-1142`) unaffected, the spawn-window orphan path unaffected; (e) the **leaderless-tree** shape | Tasks 1–10 | The ballooning test kills the whole tree and `proc_status` carries `ram_breach:` + `ram_killed: true`; the cap and orphan paths are unchanged |
| 12 | **NEW — carry the §3.5 item-13 sentence into the lane's own documentation.** *"The guard kills a GROUP. The daemon reaps only its DIRECT child (the pre-existing `await proc.wait()`). Grandchildren are re-parented to init/PID 1, which reaps them; a transient `Z` member of a grandchild is init's business, not the guard's. The guard never forks and never races the direct-child reap."* — the proc lane's reaping posture is the same as bash's, and the sentence must be reproducible from the lane (Phase 7 carries it into the runbook) | Task 11 | The item-13 statement is present in the lane's documentation and carried to Phase 7; **no** subreaper is introduced |

## Coupling

- **Tight with:** Phase 2 (the guard module), Phase 5 (the knob names and the
  endpoint), Phase 6 (the tests).
- **Independent of:** Phase 3 (the bash lane) — no shared wiring code. The **durable
  escapee-net question is bash-only** (the proc lane has no durable row), which is
  why Phase 4 carries **less** coexistence risk (arch §8 item 3).
- **Independent of:** the reaper's lanes — the proc lane never writes a durable row.

## Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| The proc lane's polite/force ladder (SIGTERM → 5 s → SIGKILL, `proc_tools.py:1413-1441`) is too slow for a RAM emergency | **High** | Medium | The DQ4 short-grace ladder (250 ms) is used, delivered by the guard thread |
| The RAM status is conflated with a **user stop** (both ⇒ `"killed"`) | Medium | Medium | Task 5 makes `ram_killed` a separate authoritative boolean, and Task 10 surfaces `user_stopped` too — the two are now distinguishable |
| The kill re-derives the pgid at kill time ⇒ a recycled pid is signalled | **High** | Low | Task 9 captures the pgid at spawn; Task 6 routes the kill through the ladder's P5 (fail-closed) + **P5a** (pgid-recycle) gates |
| An agent treats the `proc_run` return as carrying the verdict and never polls `proc_status` | **High** | Medium | Task 7's **three mandatory disclosures** — the §3.5 item-12 sentence in the runbook, the matching `proc_status` docstring line, and the pinned `ram_breach:` / `ram_killed: true` lines |
| `info.status` is finalized before the verdict is consulted ⇒ a RAM kill reads as a generic exit | Medium | Medium | Task 5 orders the consultation **before** the finalization; the ordering is observable |
| The proc lane's `start_process` spawns **outside** the lock and the guard registration lands after the spawn window | Medium | Medium | Registration happens immediately after the spawn block, using the **captured** pgid; the existing spawn-window re-check (`:1247-1308`) is preserved |
| A RAM kill that lands while the caller is already gone leaves a stale registration | Low | Medium | Task 8's release is idempotent and covers the cleanup paths; the guard's own bounded liveness sweep (Phase 2 T15) drops dead entries |
| The `setsid` escapee gap: the proc lane has **no** env-stamp backstop (the bash lane has the reaper's stamp sweep) | Medium | Medium | Surfaced in the operator memo (§3.5 item 2) as a **disclosed** scope difference — measured but not group-killable |
| The proc lane's 10-process-per-instance cap interacts with a RAM-killed process still occupying a slot | Low | Medium | Task 11 asserts the cap's behaviour is unchanged; the slot is released by the existing cleanup path |
| **FALSIFIED (arch §1.1 P5): "proc captures pgid at spawn"** | — | — | Task 9 fixes it; rev 1's claim that the proc lane captures the pgid was wrong |

## Exit Criterion

- A proc execution that exceeds the injected ceiling is **terminated tree-wide** by
  the **guard thread**, and `proc_status` carries a **`ram_breach:` line** and a
  flat **`ram_killed: true`**.
- The proc lane creates **exactly three** tasks — **no fourth task** (leader ruling
  Q1) — and the RAM kill is **not** delivered by `_attempt_kill_signal`.
- The verdict is consulted **before** `info.status` is finalized.
- `status` remains **`"killed"`**; the RAM-ness is carried by `ram_killed: bool`;
  `user_stopped` is now surfaced too.
- The pgid is **captured at spawn** and never re-derived in the RAM path.
- The **three mandatory query-only disclosures** are carried (runbook sentence,
  docstring line, pins kept).
- The **existing** `proc_status` block is byte-identical for a non-RAM process; the
  four never-changing strings are pinned.
- The kill-switch OFF path creates **no extra task**, performs no measurement, and
  registers nothing.
- The proc-lane test pack passes with **net-new reds = 0** against the base commit.
