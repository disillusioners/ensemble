# RAM Guard and Unit Memory Bounds — Operator Memo

Date: 2026-11-11 · Scope: the per-command RAM guard (bash + proc lanes) and the
`ensemble-main.service` memory bounds · Plan: `.agents/shared/planning/ram-auto-termination/`
(arch `§3.3` dynamic default · `§3.4` operator routing · `§3.5` honesty minimum · `§8` item 5)

> **WHO EXECUTES THIS: the human operator ONLY.** Every systemd mutation, restart and host
> change below is operator-lane — agents must never run them against live. This memo records
> **zero** live mutations; every host number is a read-only observation of 2026-11-11.

**The rate.** Every rate-conditional number here uses the **measured** `R = 1187.7 MiB/s =
1.1599 GiB/s` (median of four bounded runs; harness `scripts/ram_guard_measurements.py`;
provenance `phase2-measurements.md` §2 — supersedes the earlier `1177.9 MiB/s / 1.1503 GiB/s`
transcription, ~0.8 % low). A number from another rate says so.

*Sections (a)–(j) are the operator brief (~2 pages, table-first). Appendices A–H carry the
verbatim honesty set, the truth statement, the operator documentation, the durability posture, the
routed follow-ups, the T4 hand-off note, the persistence facts, and the negative claim.*

---

## (a) Measured state — no memory cap exists today

`MemoryMax=infinity` · `MemoryHigh=infinity` · `MemorySwapMax=infinity` · `MemoryAccounting=yes`
— **no cap**, still true on 2026-11-11 *after* the deaths in (b). `systemctl show` prints the
**effective** resolved values, so `infinity` on both is the authoritative proof; the only drop-ins
are `10-zero-env-reset.conf` (lone `Environment=`) and `20-core-capture.conf` (`LimitCORE=`),
neither carrying `Memory*`. Observed: `MemoryCurrent` 7.10 GiB, `memory.peak` 9.726 GiB,
`memory.events` all-zero (`low 0 · high 0 · max 0 · oom 0 · oom_kill 0`).
**Verify:** `systemctl show ensemble-main.service -p MemoryMax -p MemoryHigh -p MemorySwapMax` ·
`ls /etc/systemd/system/ensemble-main.service.d/` — the unit file itself is mode 600 root, so
`systemctl show` (not a grep of the unit) is what an unprivileged operator can rely on.
⇒ **Next free drop-in slot is `30-memory-bounds.conf`.** The unit is a **generated artifact**
(`scripts/upgrade/adopt-unit.sh`): a drop-in survives regeneration, a hand-edit does not.

## (b) The kernel OOM deaths — Oct-30 and Nov-09

Every one is a **kernel `oom-kill`** (`global_oom`, `task_memcg=…/ensemble-main.service`), *not* a
cgroup-cap kill — no cap existed to fire. Arch `E3` records a **unit peak of 7.3 GiB** for the
Oct-30 pair it studied. `anon-rss` below is the kernel's `Killed process` line.

| # | Kernel kill | Unit `Failed with result` | Killed task |
|---|---|---|---|
| 1 | `2026-10-30 10:42:41Z` | `10:43:06Z` `oom-kill` | `python3` 1167115 — 10,333,184 kB ≈ 9.85 GiB |
| 2 | `2026-10-30 11:13:24Z` | `11:13:59Z` `oom-kill` | `python3` 1178590 — 11,161,472 kB ≈ 10.64 GiB |
| 3 | `2026-10-30 19:16:12Z` | `19:16:32Z` `oom-kill` | `python` 1677256 — 10,281,088 kB ≈ 9.80 GiB |
| 4 | `2026-11-09 09:26:27Z` | `09:27:38Z` `oom-kill` | `python` 597787 — 10,631,672 kB ≈ 10.13 GiB |
| 5 | `2026-11-09 09:31:47Z` | `09:32:58Z` `oom-kill` | `python` 598669 — 11,191,760 kB ≈ 10.67 GiB |
| 6 | `2026-11-09 09:42:57Z` | `09:44:07Z` `oom-kill` | `python` 599644 — 11,142,700 kB ≈ 10.63 GiB |

**Six deaths in twelve days, the last two days before this memo** — the death class is **current**,
not historical. **Draft-time disclosure:** the plan and arch carry only #1 and #2; #3 was found on
the same Oct-30 day and #4–#6 on a **second incident day (2026-11-09)** that neither document
mentions. Recorded so the memo does not under-report.

**Reproduce with `-b all`:** `journalctl -k --since <date>` returned **zero** entries for both
windows on this host (a boot-boundary trap that reads as "no OOM happened"):

```bash
journalctl -k -b all --since "2026-10-29" --until "2026-11-01" --no-pager | grep 'Out of memory'
journalctl -k -b all --since "2026-11-09" --until "2026-11-10" --no-pager | grep 'Out of memory'
journalctl -u ensemble-main.service -b all --since "2026-10-29" --no-pager | grep 'oom-kill'
```

## (c) The orphan finding (E8)

**The killing memory was untracked/orphaned — largely children of a dead daemon incarnation — so
no per-command guard could have seen it.** Hence the layering: the guard is the **inner,
preventive** layer over live in-flight trees · the **unit cap is the outer layer** covering the
orphan-across-restarts class · the kernel global OOM is last.

> **Re-check status (draft-time).** T20 could not re-derive the historical attribution (no retained
> record for the incident windows; `memory.stat` is a snapshot, not a time-series). At the sampled
> instant: **no orphans** (no unit process with ppid 1), memory **anon-dominated** (78 % —
> page-cache hypothesis refuted), largest consumers **foreign concurrent workloads sharing the
> unit's cgroup**. So E8 stands as the arch's finding *for the incident windows*, **not**
> re-verified today; what today shows is the same class — unit memory no per-command guard owns.

## (d) The interaction table — re-published with the measured R

`F_max = C + A + Ω` (`A` 0.15625 GiB) · `floor = D_max 3.5 + F_max + O 1.0 + M_c 0.5` ·
`X ≤ T − 2.79` · `Ω_typ/worst/contended(2) = R·0.45 / 0.70 / 1.21 s`. Here `T = 11.67 GiB` ⇒
**`X ≤ 8.88 GiB`**; `C = 0.7·T = 8.171 GiB` (8,367 MiB); at measured R `Ω = 0.522 / 0.812 / 1.403 GiB`.

| Scenario (`D_max` 3.5) | `C` | floor @ measured R | Feasible unit cap? |
|---|---|---|---|
| **Derived default, this host** | 8.171 | **14.139 GiB** | **NO — at any R** (gap 5.259 GiB here; **4.447 at R = 0**) |
| Knob **3072 MiB** | 3.0 | **8.968 GiB** | **NO at the measured R** — iff `R ≤ 1.034`; measured **1.1599 exceeds** |
| **N = 2 contended** | 8.171 *or* 3.0 | **14.73** *(3072-frame: 9.56)* | **NO — window EMPTY** at every `C`; 3072-frame closes above `R ≈ 0.598` |
| **General bound at the default** | 0.7T | 0.7T + 5.968 | iff **`T ≥ 26.5 + 2.33·R`** ⇒ **29.19 GiB** here (28.82 at R = 1) |

*Provenance:* the arch's first-row floor prints `13.44`, but its own frozen frame yields `14.027`
at R = 1 (its `≈4.45` R = 0 gap reproduces exactly) — **the frame is the contract**, so floors
here are frame-derived then evaluated at the measured R. The earlier `14.717` N = 2 figure used the
superseded `R = 1.1503`; at the measured R it is `14.73`, verdict identical.
**Conservative variant (`D_max` 4.5, the T20 fallback):** derived-default floor **15.139 GiB**,
3072-MiB floor **9.968 GiB** ⇒ under that frame **no cap is feasible here at any `C`, any `R`**.

| Option | `MemoryHigh` / `MemoryMax` | Verdict |
|---|---|---|
| **RECOMMENDED** — lever (b), knob at 3072 MiB | **`7G` / `8700M`** (8.496 GiB) | Frame-feasible; the guard wins the race only **below `R ≈ 0.486`** (0.755 typical; **0.281** contended). Box margin 0.384 GiB. Its real job is the **orphan class** |
| 7G / `8G` | 8.00 GiB | **REJECTED as primary** — under the frame floor 8.156 ⇒ the cap fires before the kill even at R = 0 |
| 7G / `9G` | 9.00 GiB | Rejected — 9 + 1.79 + 1.0 = 11.79 > 11.672 ⇒ kernel OOM first |
| Throttle-only | `8G` / unset | Rejected as primary — does **not** bound the orphan class |
| Any `X ≥ 12G` | — | **Invalid** — kernel OOMs the box first |
| `C = 4 GiB` (raise-path) | — | **VOID on this host** — floor 9.0 > 8.88 ⇒ empty window |
| `C = 8 GiB` + any cap | — | **Invalid here** — needs X ≥ 13 GiB |

⇒ **At the 70 % default, no feasible unit cap exists on this host** — see (i) for the three levers.

## (e) Vehicle — the drop-in, canary first

`/etc/systemd/system/ensemble-main.service.d/30-memory-bounds.conf` (next free slot; survives
`adopt-unit.sh`):

```ini
[Service]
MemoryHigh=7G
MemoryMax=8700M
```

**Restart-class** — a drop-in edit does nothing to a running unit.

1. **Canary — live, self-reverting, writes no file:**
   `systemctl set-property --runtime ensemble-main.service MemoryHigh=7G MemoryMax=8700M`, then
   `systemctl show ensemble-main.service -p MemoryHigh -p MemoryMax`. Watch one steady-state
   window: `memory.events` `high` (throttle episodes — *rising* means the band is tight),
   `max` / `oom_kill` (**must stay 0**), `/livez` + `/readyz`.
2. **Persist** at a maintenance window: write the drop-in → `sudo systemctl daemon-reload` →
   restart the unit (or the project's restart wrapper).

> **⚠ Re-measure before persisting.** At drafting `MemoryCurrent` was **7.10 GiB** (peak 9.726
> GiB) — **`7G` sits at or below today's steady state**, so the band would bind continuously
> (`7G` was sized against a 5.3–6.0 GiB band, 2026-10-18). If a fresh reading is above ~6 GiB, lift
> `MemoryHigh` toward — never above — `MemoryMax=8700M`: `MemoryMax` contains, `MemoryHigh` only
> buys reclaim time.

## (f) Verify / rollback

```bash
systemctl show ensemble-main.service -p MemoryMax -p MemoryHigh -p NRestarts
cat /sys/fs/cgroup/system.slice/ensemble-main.service/memory.events
journalctl -u ensemble-main.service -b all --since '-1 h' | grep -Ei 'memory-limit|oom-kill|ram-guard:'
```

`MemoryMax`/`MemoryHigh` read back exactly what you set · **`NRestarts` must not increment**
(record the pre-change value — **1 on this host at drafting**; the plan's "stays 0" predates that
restart) · **`max` / `oom` / `oom_kill` must stay 0** (`high` *is expected* to count throttle
episodes; `max` > 0 means the cap fired — a unit kill, not a box death) · the
`memory-limit|oom-kill` grep stays empty for the window you care about.
**Rollback is symmetric** — remove the drop-in → `daemon-reload` → restart. No residue, no schema,
nothing to undo in the database; the canary needs no rollback file (`--runtime` properties
evaporate at the next start).

## (g) Honesty set + truth statement

Both reproduced **verbatim** — all fifteen honesty items in **Appendix A**, the truth statement and
the standing prohibition in **Appendix B**. Carry one sentence away: **the cgroup cap is the
containment layer, and above ~0.5 GiB/s it — not the guard — is the layer that fires.**

## (h) Supersession note — `resource-cost-control` §8.1

> The `MemoryHigh=7G` / `MemoryMax=8G` figures in
> `.agents/shared/planning/resource-cost-control/phase8-plan.md` §8.1 (D9), echoed into
> `docs/runbooks/resource-cost-control.md` §6, were **never applied** to the unit and are
> **superseded**: `8G` is below the frame floor (rejected as primary above) and the recommended
> pair is `7G` / `8700M`.

## (i) The default is dynamic — the fixed 8 GB figure is superseded

USER RULING #2 supersedes the commissioned fixed ceiling: with `RAM_GUARD_CEILING_MIB` **unset** the
per-command ceiling is **70 % of the detected effective total RAM** (the daemon's cgroup limit when
finite, else host `MemTotal`), resolved **once at boot**, floored at 64 MiB, **never upper-capped**
(USER RULING #1's intent — disaster prevention, not a constraint on legitimate heavy work — carried
by the no-upper-clamp rule). Here: **8,367 MiB = 8.171 GiB**, `ceiling_source = meminfo`.

* **General feasibility bound:** a unit cap coexists with the default iff
  **`T ≥ 26.5 + 2.33·R GiB`** (29.19 GiB here at measured R; 28.82 at R = 1).
* **The three levers — there is no fourth:** **(a) no cap** (kernel OOM is the outer layer —
  today's state, disclosed) · **(b) lower the knob** (`RAM_GUARD_CEILING_MIB=3072` restores the
  preventive arithmetic and makes `7G`/`8700M` the recommended pair) · **(c) resize the host**
  (≥ ~29.2 GiB frame / ~32.5 GiB conservative). **3 GiB is guidance under lever (b) only, never a
  default.**
* **The pre-freeze gate (b)** — the "3-vs-8 GiB user confirmation" — is **DISCHARGED** by USER
  RULING #1 (intent) + #2 (mechanism).
* **Raising the fixed figure is VOID, not a lever** — subsumed by the dynamic default: the ceiling
  scales with the host.

## (j) Three host-reality items

1. **The cap covers the ORPHAN class, not the guard's backup** — at the measured rates the
   guard-vs-cap race is **lost above ≈0.5 GiB/s** (0.486 worst frame at `8700M`, knob at 3072 MiB).
   The cap's value is containing what no per-command guard sees: orphans from a previous
   incarnation, foreign workloads in the unit's cgroup, the daemon's own growth.
2. **At the 70 % default, no feasible unit cap exists on this host** — the bound is
   `T ≥ 26.5 + 2.33·R GiB`, the derived default is 8,367 MiB, and the guard **fires at-or-after
   daemon death** (demand ≈ 4.79 + 8.17 = 12.96 GiB > 11.67 physical). **Retained, disclosed and
   user-accepted.** Do not paper over it: at the default the guard is a **disaster line and
   witness**, and the kernel OOM is a peer layer.
3. **The levers are no-cap / lower-the-knob / resize** (see (i)); 3 GiB is guidance under lever (b)
   only. **"Raise to 4 GiB by the same formula" is void here** — floor 9.0 GiB already exceeds the
   8.88 GiB box bound.

### The single-tree qualifier (T13)

The `7G` / `8700M` pair is a **SINGLE-TREE claim**. Two simultaneous full-ceiling breaches demand
≈ 6 GiB of trees + a 3.5 GiB daemon ≈ **9.5 GiB on an 11.67 GiB box** — past what per-tree
arithmetic rescues; the N = 2 unit-cap window is **EMPTY** at the measured R (derived floor
14.73 GiB; even the 3072 frame's 9.56 GiB exceeds the 8.88 GiB box bound). In that frame the
**kernel OOM is the residual layer**. **A disclosure, not a design target.**

---

## Appendix A — Honesty minimum: all fifteen items, verbatim

Source: arch `§3.5`. Items **1–9b** are the iteration-002 verbatim bodies (the arch's current
text, which points at them as "unchanged"); items **10–15** are its quoted bodies. Bold lead-in
labels are the only addition — the sentences are unmodified.

**1.** overshoot law `≈ rate × (poll + confirm + grace)` — typical `R × 0.45 s`, worst `R × 0.70 s`, contended `R × 1.21 s` at two simultaneous breaches, with shipped numbers (≈0.45–1.21 GiB at 1 GiB/s)

**2.** setsid escapees are *measured* (descendant closure) but **not killable** by the group kill — the reaper's stamp sweep is the bash-lane backstop, and the proc lane has **no** such net

**3.** **orphans from previous daemon incarnations are not covered — only the unit cap covers that class** (proven Oct-30)

**4.** per-tree scope: aggregate exposure = Σ live ceilings + daemon band

**5.** a killed legitimate heavy command is a real cost — raise by arithmetic, never intuition

**6.** tree-RSS/PSS ≠ cgroup accounting — never cross-compare with `memory.current`

**7.** no unit cap exists by default (present tense + verify command + REGENERATED-unit note)

> **Actualised here:** *"the unit has no memory cap today"* — see (a): `MemoryMax=infinity`,
> `MemoryHigh=infinity`, no `Memory*` directive in the unit or its drop-ins. Verify with
> `systemctl show ensemble-main.service -p MemoryMax -p MemoryHigh` (effective values — the
> authoritative resolution; the unit file is mode 600 root) and
> `ls /etc/systemd/system/ensemble-main.service.d/`. **The unit file is REGENERATED** by
> `scripts/upgrade/adopt-unit.sh`, so a hand-edited unit reverts — put a **drop-in** at
> `30-memory-bounds.conf` instead.

**8.** **swap is not a safety net** — the daemon died with 203 MiB swap used *and* with 7.4 GiB used; swapped-out pages are invisible to an RSS metric (state the scope)

**9.** watchdog liveness is load-bearing — dedicated threads, never coroutines; **a wedged lane (a `/proc` read that never returns) stalls the kill path, never the measurement path — `last_lane_progress_at` frozen is the signal, the unit cap is the backstop** *(iteration 002)*

**9b.** **an instance-less spawn (`instance_id is None`) is not registered with the guard in v1** — a ballooning tree from such a call is invisible to the guard and only the unit cap covers it *(iteration 002, §16.3)*..

**10. The enforced threshold is not the configured ceiling.** The ENFORCED trigger threshold is not the configured ceiling. An over-counted tree (summed RSS over the threshold, summed PSS under the ceiling) is re-armed at `ceiling + min(summed_rss − summed_pss, 32 MiB)` — and the threshold is RATCHETED (it never decreases) — and it re-confirms only after a further 128 MiB of growth. The tree's real (shared-page) footprint can therefore reach `F_max` — ≈3.86 GiB at R = 1 GiB/s, i.e. **≈0.86 GiB (≈29 %) above the configured 3 GiB** — before the kill completes. The envelope reports BOTH numbers: `limit_bytes` (configured) and `rearm_threshold_bytes` (enforced trigger). A bounded re-arm can also MISS a genuinely-bad tree: if the shared double-count shrinks, summed RSS under-reports the real growth and the tree can exceed the configured ceiling before the confirm runs.

> **Arch note (binding):** *"the resolved ceiling"* replaces *"the configured ceiling"*
> throughout. The envelope-side facts live in the Phase 3/4 lane docs
> (`daemon/services/ram_guard/verdict.py`, `daemon/tools/bash.py`) — see Appendix C.4.

**11. The PSS confirm is FAIL-OPEN.** If the confirm fails or exceeds its 250 ms deadline the tree is KILLED on the RSS evidence alone, so a legitimate fork-heavy command can be killed on a confirm timeout. The confirm rate is bounded (one confirm per 128 MiB of growth) to bound that exposure. On this host the confirm costs ≈35 ms median / 130–146 ms p95 at a 3 GiB tree, so a timeout is not expected below ≈20 GiB of tree total — beyond this host's physical RAM. The deadline is a POST-HOC elapsed check — the underlying `/proc` read is uninterruptible (`proc_readers.py:127-133` shape) — so a read that never returns holds the kill path until the daemon restarts; it never holds the tick.

**12. The proc-lane verdict is QUERY-ONLY.** The proc lane's RAM verdict is QUERY-ONLY: `proc_run` returns a process handle before any breach can exist, so the typed `ram_limit_exceeded` envelope is delivered by the BASH lane's return string only. In the proc lane the verdict is surfaced by `proc_status` as a `ram_breach:` line plus a flat `ram_killed: true` (added as lines, never replacing the block). An agent that never calls `proc_status` after a proc run will NOT see the RAM verdict in the `proc_run` result — it must poll `proc_status` (the same obligation it already has for `timed_out` and `user_stopped`).

**13. The daemon is not the reaper of a killed tree's grandchildren.** The guard kills a GROUP. The daemon reaps only its DIRECT child (the pre-existing `await proc.wait()`). Grandchildren are re-parented to init/PID 1, which reaps them; a transient `Z` member of a grandchild is init's business, not the guard's. The guard never forks and never races the direct-child reap.

**14. The typed error is guaranteed only while the tool call is IN FLIGHT.** The typed `ram_limit_exceeded` envelope is delivered by the bash lane's return string only while the tool call is in flight. A breach that fires after the call has returned — the ordinary `cmd &` shape, where the shell exits with `EXIT CODE: 0` and a surviving grandchild balloons — reaches no caller: the tool has already returned. Such post-return kills surface via the DURABLE EVENT and the guard endpoint (`GET /api/resources/ram-guard`), which the agent does not automatically see. **There is NO agent-injection channel for post-return breaches in v1** (a routed follow-up); an agent that backgrounds work and never checks the endpoint will not learn of a post-return kill. The Objective and Success-Criterion-3 assertions are scoped accordingly: the typed error is an IN-FLIGHT guarantee.

**15. The default ceiling is dynamic and host-derived.** When `RAM_GUARD_CEILING_MIB` is unset the ceiling is 70 % of the detected effective total RAM (the daemon's cgroup limit when finite, else host MemTotal), resolved once at boot; `limit_bytes` in every envelope is the RESOLVED value. Changing the host's RAM or the unit's memory limit does NOT re-derive a running guard — restart the daemon to re-resolve. On this host the resolved default is 8,367 MiB, at which the guard fires at-or-after daemon death (item above): operators who want prevention, not witness, lower the knob (3072 MiB restores the preventive arithmetic).

### Lane addition — **not one of the fifteen** (Phase-3 / leader-routed)

**The deferred kill: a tree can outlive the bash timeout by ≤ ~1.31 s** (the §18.4-derived 1308 ms wait). In the narrow window
where the guard holds the episode claim and does **not** kill (a refusal, a transient re-arm, or
a still-pending record), the bash timeout arm **defers its own signal** until after the bounded
verdict wait — so the tree can survive up to **≤ ~1.31 s past the bash timeout**. The *wait* is
B20-pinned (`wait_deadline_ms = RAM_GUARD_PENDING_MAX × T_service_worst_ms + WAIT_SLACK_MS
= 2 × 529 + 250 = 1308 ms`, widening with the installed guard's own grace/confirm knobs); the
*kill latency after that wait is **not** bounded by anything in this design*. Mechanism:
`daemon/tools/bash.py` — `timeout_kill_deferred` is set when `_ram_claim_held(record)`, and the
deferred `_kill_process` runs only after `await _await_ram_verdict(...)` returns (never at all
if that verdict is a RAM kill).

---

## Appendix B — The truth statement (verbatim)

Source: arch `§3.4` — "The truth statement (binding, replaces the withdrawn L110 claim)".

> "The guard completes before the unit cap fires **only while the tree's allocation rate R stays at or below the pair's headroom divided by the overshoot law** — at the lowered 3072 MiB knob and `8700M`, that is ≈0.49 GiB/s (worst frame; ≈0.76 typical; ≈0.28 contended). At the 70 % default no feasible unit cap exists on hosts below ~26.5 GiB, and on this host the derived default (8.17 GiB) fires at-or-after daemon death — the guard is then a **disaster line and witness**, which the user's ruling accepts. **'The guard always completes before a unit kill' must not appear in the memo, the runbook, or any acceptance criterion.** Restoring the preventive layering requires the knob lowered or the host resized — never a different default rule."

**Standing prohibition (binding on this memo, every runbook here, every acceptance criterion):**
the withdrawn claim named inside the statement above must not appear. It does not — the phrase
survives **only** inside verbatim reproductions of the prohibition itself (grep evidence in the
commission report), never as an assertion.

---

## Appendix C — Operator documentation

### C.1 The knobs — four `RAM_GUARD_*` fields plus the bare master switch

| Knob | Default | Bounds | Notes |
|---|---|---|---|
| `ENSEMBLE_RAM_GUARD` | **ON** | absent / empty / whitespace ⇒ ON; `0`,`false`,`no`,`off` (case-insensitive) ⇒ OFF | **Not** a pydantic field — parsed *before* config construction (B26). Restart-required. |
| `RAM_GUARD_CEILING_MIB` | **unset ⇒ dynamic 70 %** | explicit `[64, 65536]` MiB | The clamp applies to **explicit values only**; the derived value is floored at 64 MiB and **never** upper-capped. An out-of-range **explicit** value while ON **aborts boot** (`ValidationError` out of `load_config`) — never silently clamped. |
| `RAM_GUARD_POLL_INTERVAL_MS` | `200` | `[50, 2000]` | Tick cadence; one shared `/proc` scan per tick for all spawns (~9–10 ms at ~251 pids). |
| `RAM_GUARD_GRACE_MS` | `250` | `[0, 2000]` | TERM → KILL grace; `0` is legal and deliberate (hard quarantine). |
| `RAM_GUARD_PSS_CONFIRM_DEADLINE_MS` | `250` | `[50, 2000]` | One-shot PSS confirm deadline; expiry ⇒ kill on RSS evidence (fail-open, item 11). |

**Module constants — not knobs** (no env surface; a change is a code change):
`RAM_GUARD_REARM_MAX_EXTRA_BYTES = 33554432` · `RAM_GUARD_REARM_MIN_GROWTH_BYTES = 134217728` ·
`RAM_GUARD_PENDING_MAX = 2` · `RAM_GUARD_GRACE_POLL_SLICE_SECONDS = 0.025` · `WAIT_SLACK_MS = 250` ·
reader caps `RAM_GUARD_MAX_TREE_DEPTH = 32` / `RAM_GUARD_MAX_TREE_NODES = 256` ·
`RAM_GUARD_SWEEP_EVERY_TICKS = 25` · `RAM_GUARD_RELEASED_CACHE_MAX = 256`.

**The yaml block is comment-only** — do not add `ram_guard:` keys to `config.yaml`; the env tier
is the contract, and yaml-vs-env precedence has bitten this repo before.

### C.2 Turning the guard off (no code revert)

```bash
# the daemon's environment (the install's .env), then restart the daemon
ENSEMBLE_RAM_GUARD=0
```

**OFF is a complete no-op:** no guard object is constructed, no thread, no registration, no
measurement, **zero DB queries, zero `/proc` reads**, `app.state.ram_guard is None`, and exactly
one INFO line at lifespan start — grep it as proof of intent:

```
[RamGuard] OFF — ENSEMBLE_RAM_GUARD=0 (no thread, no registration, no measurement)
```

The bash and proc lanes then behave byte-identically to pre-feature output.

### C.2b Supported platforms — Linux only

The RAM guard is **Linux-only**. On any non-Linux platform (`sys.platform` not starting with
`linux`) the guard is completely inert: `_boot_ram_guard` does **not** construct a `RamGuard`,
sets `app.state.ram_guard = None`, calls `set_ram_guard(None)`, and emits exactly one boot
INFO line:

```
[RamGuard] OFF — platform <sys.platform> unsupported
```

There is no guard thread, no registration, no `/proc` reads, and no footer on bash/proc output —
bash behaves exactly as it did pre-v0.29.21 (unguarded). The belt-and-braces `RamGuard.register`
refuses registration with `refused:platform-unsupported` if a guard is ever constructed manually
on a non-Linux host, but that path is not reachable through the normal boot flow, so
`refused:platform-unsupported` does **not** appear in production output on a Linux daemon.

**OFF beats validation (B26).** `load_config` parses the bare switch **first**; while OFF it
passes a `model_construct`-ed config that bypasses both validation and the settings sources, so
an out-of-range `RAM_GUARD_CEILING_MIB` (env **or** yaml) **does not abort the boot**. Enabling
later with that bad value still aborts at *that* boot — a misconfigured guard cannot be silently
enabled.

### C.3 Counters — the exact ordered set, 16 keys, **no `p6`, no `p8`**

```
ram_kills, ram_breaches_detected, ram_kill_refusals_p0, ram_kill_refusals_p1, ram_kill_refusals_p2, ram_kill_refusals_p3, ram_kill_refusals_p4, ram_kill_refusals_p5, ram_kill_refusals_p7, ram_overcounted, ram_rearms, ram_confirm_failures, ram_events_emitted, ram_events_dropped, ownership_unverified, tree_incomplete
```

There is **no `ram_kill_refusals_p8`**: the refusal ladder is `p0…p7`, and the pre-kill
predicates map onto the buckets as `p0` = not-registered / guard-disabled (platform-unsupported is
listed here for completeness but is not reachable in production: on non-Linux the guard is never
constructed, so no registration — and no `refused:platform-unsupported` — ever occurs) ·
`p1` = pgid-not-registered · `p2` = pgid-invalid · `p3` = daemon-group · `p4` = pgid-self ·
`p5` = pgid-recycled / leader-unreadable · `p7` = group-unknown. (no `p6`: P6 is ADVISORY — §18.2.)
Ten of the 15 reason codes are `refused:` (eight permanent → terminal `REFUSED`; two —
`leader-unreadable`, `group-unknown` — transient → re-arm to `WATCHING`). The P8 probe is
**post-kill**, not a refusal, which is why no `p8` bucket exists. Semantics in one line each:
`ram_kills` trees killed · `ram_breaches_detected` trigger fires · `ram_kill_refusals_pN`
refusals by classification · `ram_overcounted` PSS-confirmed over-counts (no verdict — the
re-armed threshold is the episode's outcome) · `ram_rearms` re-arms · `ram_confirm_failures`
confirm failures/timeouts (still a kill, item 11) · `ram_events_emitted` / `ram_events_dropped`
the durable event and its drops · `ownership_unverified` identity checks that could not be
proven (**absent ≠ mismatch** — an absent stamp proceeds and bumps this) · `tree_incomplete`
kills where enumeration could not reach every member.

### C.4 Endpoint contract — `GET /api/resources/ram-guard`

Additive, read-only. Response = the **eleven-field heartbeat** (verbatim, ordered — arch `§15`
B23) followed by the 17 counters above:

```
enabled, thread_alive, last_tick_at, missed_samples, ticks, registry_depth, lane_alive, lane_pending, last_lane_progress_at, resolved_ceiling_bytes, ceiling_source
```

`ceiling_source ∈ {derived-meminfo, derived-cgroup, fallback, explicit}` (§18.6). Frozen `last_tick_at` /
`last_lane_progress_at` is the wedged-lane signal (item 9). Degraded states (OFF, pre-init, boot
failure) return the **EMPTY SHAPE** — `enabled:false` + zeros/nulls, every counter 0 — **never
503**. **Not in `/livez` or `/readyz`** (an observability death must never gate traffic), and
`/control-capabilities` stays byte-identical (fence-pinned). Do not add either.

---

## Appendix D — Durability posture (v1)

**In-process + the durable event as the forensic record. No migration lands in v1 at all.**

* The registry (`SpawnRecord`, keyed by `tracking_id`) is **RAM-only**; a restart loses it — and
  the command it watched died with the daemon anyway.
* The forensic answer to *"why did my command die?"* is the **durable event**
  (`EventKind.RAM_LIMIT_EXCEEDED`, `code="ram_limit_exceeded"`, exactly one per
  `WATCHING→KILLED` transition, emitted by the execution-lane thread). One events query joins
  `event.pgid` ↔ the spawn row's eventual `gc` close.
* **Retention ≈ 24 h** (`EventRepository.cleanup_old(max_age_hours=24)`) — pull it inside the
  operator response window; after that only counters and the endpoint remain.
* **Refusals emit no event** (operational, not forensic — counters + the transition log line).
* **Rollback is trivial:** remove the lifespan block and the lane seams — **zero schema
  residue**; no table, no column, no CHECK to undo.
* The bash lane's `bash_spawn_registry` row is deliberately **left OPEN** on a RAM kill so the
  reaper's env-stamp escapee net stays armed; it closes later via the existing `ram-tier-dedup`
  path at terminal cleanup.
* **Counting note (read the qualifying sentence below as the pin states it, then this line):**
  at the escalation pass the enumeration was **16** members and `ram_limit_exceeded` was the
  **17th**, i.e. a **new** member — the pin's "17th member" means "the member appended to the
  16". In the current tree the enum literal lists **17** names (the 16 pre-existing +
  `ram_limit_exceeded`). The substance is unchanged: **the SQLite CHECK enforces 8, the enum has
  always exceeded it, and the new kind rides the PostgreSQL `create_all` path with no CHECK.**
  Qualifying sentence (arch `§15` B22, verbatim):

```
the SQLite `event.kind` CHECK constraint lists only 8 kinds while `EventKind` has 16 — it is already stale for 8 pre-existing kinds; the production deployment is PostgreSQL (`create_all`; the SQLModel column carries no CHECK — `models.py:86`), so `ram_limit_exceeded` (the 17th member) is unaffected
```

That SQLite-side staleness is **pre-existing house debt, deliberately not fixed here**. On a
hypothetical SQLite-created DB the insert would fail — surfacing as an emitter error log plus
`ram_events_dropped++` (never silent). Production is PostgreSQL, which carries no CHECK there.

**The 3-vs-8 GiB statement.** The commissioned fixed 8 GB figure is **superseded** — the default
is now **dynamic** (70 % of detected effective total RAM; Appendix A item 15 and §(i)), bounded
generally by **`T ≥ 26.5 + 2.33·R GiB`**, with exactly three levers: **no-cap /
lower-the-knob / resize**. 3 GiB survives only as guidance under the lower-the-knob lever. The
pre-freeze user-confirmation gate is **discharged by USER RULING #1 (intent) + #2 (mechanism)**.

---

## Appendix E — Routed follow-ups (recorded, not expanded)

Each names its entry point and why it is out of scope.

1. **Host-wide OOM posture** — the unit drop-in. *Entry:* §(e)/(f) here + the unit's drop-in
   directory. *Out of scope:* operator-gated restart-class host change; this lane executes no
   live mutation.
2. **MCP-server RAM limits** — the same guard over MCP **stdio children**. *Entry:*
   `daemon/mcp/builtin_servers/` + the MCP spawn path. *Out of scope:* different ownership,
   different kill authority; the commission names the bash + proc tools only.
3. **The T4 ceremony path** — promote this enforcement into `resource_limit_policy`. *Entry:*
   `.agents/shared/planning/resource-cost-control/approach-comparison.md` §6 (the adopted
   "seam + dormant table" row) + `daemon/routers/resources.py` `/control-capabilities`. *Out of
   scope:* **user-gated** behind an existing fence; needs the durable enforcement port first.
4. **Dormant-column unfencing** — `resource_pid_map.action` / `action_by` / `acted_at`.
   *Entry:* `tests/test_resource_control_seam.py`
   (`test_no_daemon_file_names_action_by_acted_at`,
   `test_no_resource_pid_map_string_names_action`, the AC5 tokenizer audit). *Out of scope:*
   **DELIBERATELY DEFERRED, not forgotten** — a test-lifting change that must be made
   consciously and user-gated, never as a side effect.
5. **The pre-existing bash generic-exception-arm leak** — the `except Exception as e:` arm
   releases **neither** `BashProcessRegistry` nor `pid_registry` and does **not** kill the child;
   `MemoryError` lands exactly there during a RAM incident. *Entry:* that arm
   (`daemon/tools/bash.py`; the plan cited `:1223-1224`, the current tree is ~`:1742`). *Out of
   scope:* this feature adds the **guard** release there (it does); the two pre-existing
   registry leaks are **DELIBERATELY DEFERRED, not forgotten** — a separate reviewable change.
6. **The proc lane's missing escapee net** — bash has the reaper's env-stamp sweep against
   `setsid` escapees; the proc lane has **no** such net. *Entry:* `daemon/tools/proc_tools.py`
   (spawn path + `ENSEMBLE_PROC_TRACKING_ID`) beside `daemon/services/bash_spawn_reaper.py`.
   *Out of scope:* new reaper surface and ownership rules; disclosed as honesty item 2 instead.
7. **The stale SQLite `event.kind` CHECK** — 8 kinds enforced while the enum has 16 at the pin
   / 17 in the tree (`ram_limit_exceeded` appended). *Entry:*
   `daemon/migrations/versions/20260412_000002_create_event_table.sql`. *Out of scope:* house
   debt deliberately not fixed (no migration in v1); harmless in production.
8. *(arch-named)* **The post-return agent-injection channel** — honesty item 14's "NO
   agent-injection channel for post-return breaches in v1". *Entry:* the event bus + the
   instance-injection path. *Out of scope:* a new delivery lane, not a guard change.
9. *(arch-named)* **Widening the bash registration gate for instance-less spawns** — the capture
   block sits inside `if instance_id is not None and proc is not None:`
   (`daemon/tools/bash.py`, the S1 gate). *Entry:* that gate. *Out of scope:* a restructure the
   commission does not name; disclosed as honesty item 9b instead.

### Phase-4 deviation disclosure (proc-lane registry key)

The proc lane keys its guard record by the **8-hex `process_id`** (its own id space) rather than
a uuid-per-spawn; a collision would **silently overwrite** the earlier watch. The current tree
**warns instead of refusing** — a content-free `logger.warning` naming only the tracking id and
the prior state (no argv, no env values, no counter bump, so B5's frozen set is untouched and
non-colliding keys are unaffected). Recorded because the blast radius is a **lost watch**, not a
wrong kill.

---

## Appendix F — The T4 hand-off note (mental model)

| | **The RAM guard (this feature)** | **The T4 seam (future)** |
|---|---|---|
| Nature | **Safety net** — like the unit cap | **Policy layer** |
| Default | **ON**, in-process, config-driven | `enabled=false`, `user_gated=true` |
| Decided by | operator config / env | a **user-gated ceremony**, per policy row |
| Storage | RAM-only + the durable event | `resource_limit_policy` (dormant, empty) |
| Writer | none — no row written | none in v1 — no CRUD endpoint |

**Disable surfaces:** `ENSEMBLE_RAM_GUARD=0` (C.2) is the **v1 disable** of the whole safety
net; a **per-policy** disable belongs to the future T4 ceremony and does not exist yet.

**Every fence a future ceremony must consciously lift** — this commission lifts **none**:

1. The `/control-capabilities` **`available:false`** shape (every action `alert`/`throttle`/`kill`
   advertised unavailable by construction).
2. The **observe-only fence comment** — the `P6 — observe-only control seam` block in
   `daemon/routers/resources.py` ("ZERO enforcement (phase6-plan D1/D6) … advertises the control
   seam; it never kills, throttles, or enforces anything").
3. `tests/test_resource_control_seam.py` — the dormancy pack: the AC5 tokenizer audit
   forbidding `throttle|kill|enforce` **code** outside docstrings/comments, the two
   dormant-column greps, the empty-`resource_limit_policy` assertions, and the
   "router mutation surface is seam-only" test.

**Status: no fence was modified in this commission.**

---

## Appendix G — Persistence facts for a future table-bearing commission

v1 adds **no** table; the next commissioner will need these. Nothing here is owed by this
feature.

**If a later commission adds a table:**

* Its own `daemon/migrations/versions/<YYYYMMDD>_<HHMMSS>_create_<table>.sql` with `-- UP` /
  `-- DOWN` (the runner raises on a missing `-- UP`; filename regex `^\d{8}_\d{6}_(.+)$`).
* Idempotent `CREATE TABLE IF NOT EXISTS`, and **no semicolons inside SQL comments** — the
  runner strips only *full-line* `--` comments and then splits on `;`, so a semicolon in an
  inline comment mis-splits the batch.
* Index names **byte-identical** to the model's `__table_args__`.
* **A brand-new table needs NO `_ensure_postgres_columns` mirror** (the `bash_spawn_registry`
  precedent); that mirror exists for *column additions to pre-existing tables*.
* Model registration **before** `create_all` (`daemon/manager.py`: registration block →
  `SQLModel.metadata.create_all(...)` → `MigrationRunner(...).run_pending_migrations()` →
  `_ensure_postgres_columns()`).
* A fresh table must work on both boot paths — the SQLite migration runner AND the PostgreSQL
  `create_all` metadata path.

**Current-tree truth about the ensure list** (verified 2026-11-11 — do not assume atomicity):

* `_ensure_postgres_columns` (`daemon/manager.py:8396`) is **NOT one transaction** — it uses
  **many separate `engine.begin()` blocks** (opening at `:10036`, `:10073`, `:10164`, `:10282`,
  `:10375`, `:10583`), one per logical group.
* The `terminal_reason` backfill runs a **per-statement transaction** in a loop
  (`:10162`–`:10192`), each with its own `try`.
* The checkpoint-autovacuum block (`:10575`–`:10634`) is **fail-open by design** — a
  permission-denied `ALTER … SET (autovacuum…)` logs a WARNING and continues; it must never
  brick the boot.
* **There is still an unguarded call site:** `if self._ensemble_config is not None and
  self._ensemble_config.is_postgres: self._ensure_postgres_columns()` at
  `daemon/manager.py:1057-1058` — an exception escaping any block *not* internally guarded
  propagates out of `InstanceManager.__init__` and **aborts boot**.
* **Order is not containment.** "It runs after `create_all`" says nothing about failure
  isolation; each block's blast radius is set by whether *that block* has a `try`.

---

## Appendix H — What this memo does not claim

Not that the box is protected; not that memory is reclaimed instantly; not that other processes
are unaffected; not that a killed command was "the culprit" (a numeric kill can hit a legitimate
heavy command — item 5); **not that the guard always finishes before a unit kill** (Appendix B
quotes the binding prohibition verbatim); not that the derived default is a safe operating point
on this host.

---

## Appendix I — Deferral Register

Every item deferred in fix cycles 1–3, with its reason. Without this
register a silent drop and a deliberate deferral look identical — that is
why it is committed. Each row names the deferring cycle and the reason it
stayed out. Re-entry requires a new commission; nothing here auto-expires.

| Item | Deferred in | Reason |
|---|---|---|
| PSS-confirm early abort (256-member trees: a sequential `smaps_rollup` walk can hold the lane ~23 s before the post-hoc deadline check) | cycle 1 | Requires a deadline parameter threaded through `read_tree_pss` + partial-read semantics; touching the confirm contract mid-cycles was ruled riskier than the bounded exposure (fail-open ⇒ kill still fires; only the lane occupancy is long). |
| `_lane_failures` as a heartbeat/counter key | cycle 1 | Both the §15 B23 heartbeat set (11 keys) and the §15 B5 counter set (16 keys) are pinned EXACT; adding a key breaks the exact-set pins and the M12 mutation leg. The failure signal currently surfaces via the lane's restart-log only. |
| `breach_at` staleness on a re-armed episode | cycle 1 | Cosmetic: `breach_at` keeps the FIRST breach time of the episode rather than the re-arm's re-breach. No consumer keys off it for correctness; changing it would touch the verdict-record semantics for no observable gain. |
| MemTotal 1 kB ⇒ 64 MiB floor instead of the fallback | cycle 1 | The §18.7/§4.7 case "sub-64 MiB effective total ⇒ the floor" is PINNED (test_derived_value_is_floored_at_64_mib); moving the fallback threshold would break that pin and the §18.7 test enumeration. The floor IS the specified behavior for tiny-but-nonzero totals. |
| No re-kill on a KILLED/tree-incomplete record (v1 scope) | cycle 3 (LEADER RULING) | Episodes are contract-scoped ONE-SHOT: a kill that could not verify death (tree-incomplete) never re-fires in v1. Survivor processes are the reaper/orphan net's domain. The record is still retained and watched; the retention bound is member-count-0 on a COMPLETE scan (real since the cycle-3 ever-members fix), at which point the sweep releases it. |
| The KILLED-retention bound itself (no time-based max-watch) | cycle 3 (LEADER RULING) | The bound is exactly member-count-0-on-complete-scan. A time-based cap was rejected: the marginal per-tick cost of a dead-but-unverifiable record is one RSS sum, and the shared scan is O(host pids) regardless — the only forever-watched trees are trees with live members, which is what the guard should watch (disclosed per §18.1). |
| Pending-full blind spot (content-free WARN, no counter) | cycle 3 (code 6dc38a1b3) | The pending slot is full this tick and the breach is not claimed; handled as a content-free `logger.warning` naming only the tracking id + depth + cap (no argv, no env values, no counter bump) so the §15 B5 counter set (16 keys, frozen exact) stays untouched. Code-side pending-full log landed in 6dc38a1b3; this register row is the runbook mirror. |

**Identity classification at spawn (ENSEMBLE-32):** absent leader is never registered (no watch, no counter, no refusal). absent covers both FileNotFoundError and ProcessLookupError (the process is gone). unreadable -> refused:leader-unreadable (p5, fail-closed). platform-unavailable -> refused:platform-unsupported (p0). The kill ladder keeps its own P5 split (absent -> proceed, unreadable -> refuse); both lanes share tree_reader.classify_proc_stat_error, so absent and unreadable can no longer disagree.

**Tick-level skip-fast (ENSEMBLE-32 item 5):** when scan_proc reports scan_complete=False, _tick_once bumps enumeration_failures for every record under a single lock (peak preserved, no breach evaluated, no per-record lock churn).

**Ops note (ENSEMBLE-32):** ram_kill_refusals_p5 will drop for short-lived children post-fix (intended) — dashboards keyed on it see a step.
## Test-platform census (ENSEMBLE-32)

Reconciliation command:
for f in $(grep -rl 'autouse=True' tests/ --include=*.py | sort); do
  grep -q 'setattr("sys.platform"' "$f" && echo "$f"
done
→ 9 files.

Retained by design: the following NINE files carry a whole-module autouse
sys.platform = "linux" re-scope. Kernel-dependent legs are skipif-gated
with honest markers; the fixture exists to exercise the platform-gate
predicate on a darwin host.

| File | Kernel-dependent legs | Gating | Why autouse retained |
|---|---|---|---|
| tests/tools/test_bash_ram_guard.py | yes (/proc/<pid>/stat, /proc/meminfo) | skipif at :408 + inline memory skips | exercises platform-gate predicate |
| tests/tools/test_proc_ram_guard.py | yes (/proc/meminfo; real /proc in non-mocked cases) | skipif at :806 + inline memory skips | exercises platform-gate predicate |
| tests/tools/test_ram_guard_fairness.py | yes (/proc/meminfo unreadable) | skipif | exercises platform-gate predicate |
| tests/unit/routers/test_resources_ram_guard.py | none (fake procfs trees) | none needed | platform-gate predicate; tmp_path fakes |
| tests/unit/services/test_ram_guard_events.py | none (fake procfs) | none needed | platform-gate predicate; tmp_path fakes |
| tests/unit/services/test_ram_guard_guard.py | one (/proc/<pid>/stat raw bytes) | skipif where needed | platform-gate predicate |
| tests/unit/services/test_ram_guard_interactions.py | none (fake procfs) | none needed | platform-gate predicate |
| tests/unit/services/test_ram_guard_ladder.py | none (synthetic proc root) | none needed | platform-gate predicate |
| tests/unit/services/test_ram_guard_phase6.py | none (fake procfs) | none needed | platform-gate predicate |
