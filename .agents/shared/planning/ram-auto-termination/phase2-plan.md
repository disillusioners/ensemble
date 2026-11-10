# Phase 2: Measurement Substrate — rev 2

> **rev 2 — supersedes rev 1 (commit 36a9e9c0d) per council cycle 1.**
> Re-issued as the BUILD CONTRACT. Binding source of truth:
> `architecture-recommendation.md` §4.1 (frozen reader contract), §4.4 (verdict
> record), §7 (module decomposition), §8.1 (Phase-2 scope absorption), §5 (failure
> paths). Where this file and that document disagree, **that document wins**.
> Rev-1 content that survives the rulings is carried forward with its task numbers
> continuous (T1…T9 preserved; new work appended from T10).

## Objective

Build the `daemon/services/ram_guard/` substrate that answers one question — "how
much RAM is this process tree using **right now**, and what is the **deciding**
value?" — with **no tool wiring at all**, plus the envelope helper, the guard thread,
and the kill ladder that the bash lane (Phase 3) and the proc lane (Phase 4) both
consume.

Rev 1 said "summed RSS against a ceiling". **That is falsified** (arch doc §1.1 P7,
DQ1): summed RSS over-counts a fork-without-exec tree by ≈ the member count
(**measured 8.9×** on a 9-member tree: 2 106 MiB summed RSS vs 237 MiB summed PSS vs
~250 MiB true footprint). At a 3 GiB ceiling a 12-worker multiprocessing pool
touching 250 MiB each would be **false-killed at a quarter of the ceiling**. The
substrate therefore implements the **ruled hybrid**: a cheap RSS trigger per tick
and a **mandatory one-shot PSS confirm** at breach that is the **deciding** value.

## Module decomposition (arch doc §7 — frozen)

```
daemon/services/ram_guard/
  __init__.py     facade: get_ram_guard(), register/read_verdict/release
  tree_reader.py  scan_proc / resolve_members / read_tree_rss / read_tree_pss / exceeds  (pure, injectable)
  verdict.py      SpawnRecord, PeakRecord, breach predicate, OVERCOUNTED re-arm
  ladder.py       ram_kill_ladder + the P0–P8 pre-kill predicates
  guard.py        the thread + registry + GuardCounters + liveness sweep
  errors.py       format_ram_envelope (one producer, two surfaces)
```

**Import direction (hard):** `tools/* → ram_guard` only; `ram_guard → stdlib` only.
**No** import of `tools/*`, **no** `resource_monitor`, **no** repositories, and
**no** `bash_spawn_reaper` (the stat parse is reimplemented injectably because
`list_group_members` hardcodes `/proc` and is per-spawn-priced — arch doc §1.1 P9).
**No** import of `proc_readers` (its PSS reader is **self-only**, hardcoding
`/proc/self/smaps_rollup` — arch doc §1.1 P7).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Create the package** `daemon/services/ram_guard/` with the five modules above; the facade exposes `get_ram_guard()`, `register()`, `read_verdict()`, `release()` | none | Package exists; no import of `daemon/tools/*`, `daemon/services/resource_monitor/*`, `daemon/repositories/*`, or `daemon/services/bash_spawn_reaper.py` (asserted by a test, not by inspection) |
| 2 | **Implement the ONE-shared-scan reader** per the DQ3/§4.1 contract — **five** functions, not the rev-1 `(pgid) → TreeReading` shape: `scan_proc(proc_root) -> ProcTable` (builds `pid → (state, ppid, pgrp)` once per tick, with `scan_complete`), `resolve_members(table, *, pgid, leader_pid, max_depth, max_nodes) -> MemberSet` (membership = **group relation ∪ bounded descendant closure from the leader**), `read_tree_rss(proc_root, member_pids) -> TreeReading` (trigger metric), `read_tree_pss(proc_root, member_pids) -> TreeReading` (confirm metric), `exceeds(total_bytes, ceiling_bytes) -> bool` (`total >= ceiling`, **inclusive**) | — | Each function is pure and takes an **injectable `proc_root`**; `scan_proc` is called **once per tick** and serves **all** live spawns (per-spawn scans are 9.49 ms each and overrun the tick — arch doc DQ3); the stat parse (`rfind(")")`; field 5 = pgrp, 22 = starttime, 24 = rss_pages) is reimplemented injectably, **preserving the zombie + kernel-thread exclusions**; `list_group_members` is **not** reused |
| 3 | **Implement the PSS CONFIRM reader — the deciding metric** (`read_tree_pss` → `summed_pss_bytes` from `/proc/<pid>/smaps_rollup` `Pss:`). It runs **only on the kill path** (at breach), never on the tick path | Task 2 | The confirm returns a `TreeReading` with `metric="summed_pss_bytes"`; the **deciding value** recorded in the verdict is `confirmed_pss_bytes`, NOT the RSS peak; the confirm honours a **250 ms deadline** (`RAM_GUARD_PSS_CONFIRM_DEADLINE_MS`) and is **fail-open ⇒ kill** on confirm failure/timeout (arch doc DQ1); cost is measured at ~35 ms median / 130–146 ms p95 at a 3 GiB tree |
| 4 | **Implement the OVERCOUNTED re-arm path.** When the confirm shows `summed_pss_bytes < ceiling_bytes` but the RSS trigger fired, the tree is **overcounted** (shared pages): enter state `OVERCOUNTED` and **re-arm** at `ceiling + shared_extra_bytes` where `shared_extra = summed_rss − summed_pss` (additive — post-fork *private* growth moves both metrics 1:1) | Tasks 2, 3 | An overcounted tree is **not killed**; it re-arms at the computed threshold; the re-arm threshold is stored in `rearm_threshold_bytes`; a later breach at the re-armed threshold **does** kill |
| 5 | **Implement the breach semantics exactly as ruled.** The trigger fires on the **CURRENT sample, not the peak** — a spike released before the next sample is *reported* (peak) but **never killed**. An **incomplete** reading that still `>= ceiling` **proves** a breach (a lower bound above the ceiling); an incomplete reading below the ceiling is **no evidence** — never "no breach" | Task 2 | Boundary cases `ceiling − 1` / `ceiling` / `ceiling + 1` are pinned; a released spike produces a raised peak and **no** kill; an incomplete-but-above-ceiling reading kills; an incomplete-below-ceiling reading does not evaluate a breach |
| 6 | **Implement the verdict record** `SpawnRecord` (§4.4, RAM-only, keyed by `tracking_id`): `tracking_id · instance_id · lane · pid · pgid · start_time_jiffies · ceiling_bytes · poll_interval_ms · grace_ms · state (WATCHING→BREACHED→OVERCOUNTED→KILLED→RELEASED, first-writer-wins) · observed_peak_bytes (monotonic) · last_total_bytes · samples · metric · trigger_metric · peak_is_lower_bound · under_report_bound_bytes · confirmed_pss_bytes · tree_complete · members_total/killable/unreachable · unreadable_pids · gone_pids · enumeration_failures · breach_at · killed_at · last_sample_at · trigger (rss_threshold | pss_threshold | pss_confirm_unavailable) · rearm_threshold_bytes` | Task 2 | The record carries **every** field the envelope and the event need — the envelope's honesty set (`peak_is_lower_bound`, `under_report_bound_bytes`) is **homed here**, not recomputed downstream; state transitions are first-writer-wins |
| 7 | **Implement the honest-peak bound** (§4.4, frozen): the per-sample under-report is ≤ `R·Δt`, **computable from the samples themselves** as the **max positive sample-to-sample delta** (`under_report_bound_bytes`); total exposure breach→all-dead = `R·(Δt + grace)`. Both appear in the envelope | Task 6 | The bound is derived from the recorded samples (no external input); it is monotonic with the peak; it is **labelled** a lower bound |
| 8 | **Implement the reader unit semantics** exactly as ruled: READABLE → value · **ZERO** (status OK, no `VmRSS:` line — empirically zombies/kernel threads) → 0, still complete · **GONE** (ENOENT) → 0, still complete · **UNREADABLE** (read error / unparseable) → 0, `complete=False`, pid recorded. `tree_complete` = scan completed ∧ closure not truncated ∧ no UNREADABLE member — and it does **not** mean "every member is killable" (that is `members_total / members_killable / members_unreachable`) | Task 2 | Each member state has a pinned outcome; `tree_complete` and `members_killable` are independent fields; a setsid escapee is **measured** but counted in `members_unreachable`, not in `members_killable` |
| 9 | **Implement the unit discipline**: `/proc` reports kB (KiB); the **only** `× 1024` lives in the parse helpers; everything downstream sees **bytes**. Config is MiB; `× 1 048 576` once at config→`SpawnRecord`. **Pinned in both directions**: the substrate must never emit a kB number labelled as bytes, and the **bash lane's `peak_rss_kb` is KB** — the single explicit **bytes→KB conversion** at that boundary (arch doc §7) or the two numbers disagree by 1024× | Task 6 | A test asserts the byte value of a known `VmRSS` (e.g. `1024 kB → 1 048 576 bytes`); a test asserts the config→record conversion (`3072 MiB → 3 221 225 472 bytes`) |
| 10 | **LEADER-RULED DELTA (council Q2) — the leaderless-tree failure mode is NOT an accepted limitation.** When the group **leader exits while a grandchild still balloons** (the ordinary `cmd &` shape), the watch **stays ALIVE** until the group member count reaches **0**, and the identity **re-anchors on the surviving group members**. This is a **§4.2-P5 contract change**, not a limitation | Task 6 | The guard's membership resolution does **not** depend on the leader pid being alive: when the leader is gone, the record re-anchors on the surviving group members and the tree continues to be measured; the watch is released **only** at member count 0; a test spawns a shell that backgrounds a ballooning grandchild and exits, and asserts the grandchild is still watched and killed |
| 11 | **Implement the P0–P8 pre-kill predicates** (arch doc §4.2) in `ladder.py` — a kill fires iff **ALL** evaluate true; "unevaluable" ≠ true: **P0** switch+Linux+registered · **P1** pgid from this guard's registry · **P2** `pgid > 1` · **P3** `pgid != os.getpgid(os.getpid())` (**the daemon's group, not its pid**) · **P4** `pid != os.getpid() ∧ pgid != os.getpid()` · **P5** leader `starttime` match (unreadable ⇒ fail-closed ⇒ treat as exited) · **P6** ownership stamp (mismatch ⇒ abort; unreadable ⇒ proceed + `ownership_unverified++`) · **P7** group not dead · **P8** post-kill probe ⇒ `tree_complete` | Task 6 | Every predicate has an individual unit test with a `kill_fence` fixture that hard-raises on a real `os.killpg` (mirroring `tests/unit/services/test_bash_spawn_reaper.py`); a "unevaluable" input never yields a kill; the daemon's own group and `pgid <= 1` are refused; `pgid == os.getpid()` is refused |
| 12 | **Implement the kill ladder** `ram_kill_ladder(pgid, leader_start_time, grace_seconds) -> (status, reason)` — **sync**, callable from the guard thread with **no event loop** (the thread must be able to `os.killpg` without one). TERM → slice-probed grace (the reaper's `_grace_wait_for_death` shape) → KILL; KILL always reachable; **zombie-aware skip**; **idempotent** (safe to repeat). Status ∈ `{killed, already-dead, refused}`; reason ∈ `{killed:term-esrch, killed:group-dead-after-grace, killed:ladder, already-dead:group-empty, refused:pgid-invalid, refused:pgid-self, refused:daemon-group, refused:pgid-recycled, refused:platform-win32}`. Identity gate = **captured pgid + leader `starttime` re-verification (fail-closed)**; the environ check is **P6, secondary only** | Task 11 | The ladder runs in a bare thread (a test drives it without an event loop); the status/reason vocabulary is exactly the ruled set; a repeated call is a no-op; the reason vocabulary is asserted as an exact set (no drift) |
| 13 | **Implement the dedicated guard thread** in `guard.py` per DQ2: **ONE** `threading.Thread(daemon=True)`, started/stopped on the **FastAPI lifespan** (`app.state.ram_guard`; own `try/except` + INFO; **OFF ⇒ no thread at all**); poll default **200 ms**, bounds **[50, 2000] ms**. Structural invariants (not discipline): **the tick path NEVER logs**; **no lock held across a `/proc` read, a signal, or a sleep**; **no PSS read on the tick path**; verdict writes are **RAM-only** | Task 6 | A test asserts that with the switch **OFF** the guard **starts no thread at all** (`thread_alive` is False and no thread object exists) — this is the kill-switch OFF "no-thread-at-all" case; a test asserts the tick path emits **no** log line; a test asserts a tick that raises does not kill the thread |
| 14 | **Implement the guard-death machinery** (§5): the tick body is `try/except Exception` → counter, **never log**; an **outer `while not stop` restart loop**; the health heartbeat `last_tick_at` / `missed_samples` / `thread_alive` / `ticks` / `registry_depth` is exposed on the read surface (Phase 5 builds the endpoint over it) | Task 13 | A test kills the tick body and asserts the thread **restarts** and the heartbeat advances; `missed_samples` increments on a failed tick; no log line is emitted from the tick path |
| 15 | **Implement the registration release + the bounded liveness sweep** (§5): a `finally`-shaped release on **all six** bash exit paths (success, timeout, RAM, cancel, generic-exception, spawn-failure), plus a **bounded non-lethal liveness sweep** every ~25 ticks: dead pid ⇒ drop the entry; live-but-unreleased ⇒ **keep watching** (the safe direction — the runaway stays guarded) | Task 6 | The sweep is **bounded** (a cap on the entries examined per sweep) and **non-lethal** (it never signals); a live-but-unreleased entry is retained; a dead entry is dropped |
| 16 | **Implement the envelope helper** `format_ram_envelope` in `errors.py` — **one producer, two surfaces** (arch doc §8.1: Phase 2 absorbs the envelope helper so Phases 3/4 build against it). **Bash:** the envelope **IS** the entire return string. **Proc:** a `ram_breach:` line + a flat `ram_killed: true` inside `proc_status` (added, never replacing). Key set (additive-evolution; the house 3-key prefix byte-identical; **numerics before `message`**): `ok, code, surface, limit_bytes, observed_peak_bytes, peak_is_lower_bound, poll_interval_ms, metric, trigger_metric, confirmed_pss_bytes, under_report_bound_bytes, pid, pgid, tree_complete, members_total, members_killable, members_unreachable, grace_ms, killed_at, ram_breach_observed, coincident_timeout, timeout_seconds, message` | Task 6 | One producer, two surfaces; the key set is asserted as an **exact ordered list**; the 3-key prefix (`ok`, `code`, `surface`) is byte-identical to `_format_secret_envelope`; **numerics precede `message`** |
| 17 | **Implement the NEGATIVE SPACE** (§4.3, security-relevant): the envelope and the event payload carry **usage numbers only**. Forbidden in any form: command/argv **including hashes** (argv carries secrets; a hash is a brute-force oracle; the sanitized form still leaks the `--flag value` residual) · env keys or values (a live child's `/proc/<pid>/environ` holds **plaintext resolved secrets**, `secret_resolver.py:300-397`) · stdout/stderr · secret ids · per-member pid lists / RSS breakdowns · host paths/hostnames/workdir · exception reprs. `message` = **fixed text**, numeric interpolation only | Task 16 | A test asserts the absence of every forbidden class (a message containing the offending string is rejected); the `message` template has **no** argv interpolation path; the producer-side allow-list is enforced at the producer (the event bus persists `data` unsanitized — `event_bus.py:176-183`) |
| 18 | **Measure the per-tick cost on the SCAN-POPULATION axis** — the shared scan is **O(host pids)** per tick (9.49 ms median / 13.89 ms p95 at 250 pids; ≈ 40 ms at 1000 pids). Report the measured cost vs the **worst** host pid count, and size the 200 ms interval against it | Task 2 | The measurement harness reports median + p95 per-tick cost at ≥2 scan populations (e.g. 250 and 1000 entries); the number is recorded in the phase report and the interval is confirmed or amended |
| 19 | **Measure a realistic allocation rate (R).** R is currently **assumed** ∈ [1, 3] GiB/s; the grace default and the ceiling arithmetic depend on it. Record the measured rate and the resulting **overshoot law** `≈ rate × (poll + grace)` with the shipped numbers (~0.45 GiB at 1 GiB/s) | Task 13 | The measured rate and the overshoot figure are recorded; the overshoot law appears in the envelope's honesty set and in the operator memo |
| 20 | **Fold the PRE-FREEZE GATE (a) into this phase as an explicit gate**: **attribute today's unexplained 8.11 GiB unit peak BEFORE the Phase-2 freeze.** If it was one command tree, the 3 GiB ceiling catches it; if it was daemon page cache, it does not — and the `D_max` term in the interaction table shifts | Task 2 | The attribution is **recorded** (command tree ⇒ 3 GiB catches it; daemon page cache ⇒ `D_max` shifts and the interaction table is updated) before the phase exit; an unattributed peak **blocks the Phase-2 freeze** |
| 21 | **Fold the PRE-FREEZE GATE (b) into this phase as an explicit gate**: the **3-vs-8 GiB user-confirmation surfacing requirement** — the final report and the operator memo must **carry** the fact that the shipped default **amends the commissioned 8 GB**, with the raise-path arithmetic and the host-resize consequence | — | The phase exit records that the requirement is carried into the final report (this lane) and the operator memo (Phase 7); the fact is **not** silently dropped because it changed the commissioned number |
| 22 | **Write the substrate unit tests** (pure-predicate tier): the fake-`/proc` tree readings (single process, tree of children, shared-page double-count semantics, the 8.9× over-count shape), the reader unit semantics (READABLE/ZERO/GONE/UNREADABLE), the enumeration-failure behaviour, the peak monotonicity, the breach boundary, the OVERCOUNTED re-arm, the P0–P8 predicates, the ladder vocabulary, and the tick-path-no-log rule | Tasks 2–17 | The pack is **pure-predicate** (no real subprocesses, no real RAM, no host `/proc` reads beyond the fake tree); the fixture's **stat-line format** is documented and pinned (the `rfind(")")` split; fields 5/22/24) so a fixture author cannot silently mis-shape a line |

## Coupling

- **Tight with:** Phase 3 and Phase 4 — both consume this module; a change to the
  reader contract changes both lanes. This is why the contract was frozen in the
  arch doc (§4.1).
- **Tight with:** Phase 1 — every implementation detail is the ruling, not a choice.
- **Loose with:** Phase 5 — the guard exposes the values the observability surfaces
  need; the guard does not own the endpoint.
- **Independent of:** the `resource_monitor` package — it is **not** modified and
  **not** imported (`proc_readers` is excluded by §7).

## Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| The reader couples the fast guard to the slow monitor (`proc_readers` import) | Medium | Medium | §7 forbids the import; asserted by a test (Task 1). `read_pss_rollup` is **self-only** (arch doc §1.1 P7) — the PSS reader is written fresh, injectably |
| The per-tick shared scan overruns the interval on a large host | Medium | Medium | Task 18 measures on the **scan-population** axis against the **worst** host pid count; per-spawn scans are already rejected (9.49 ms × N overruns a 200 ms tick) |
| `smaps_rollup` **stalled read → head-of-line blocking** of the confirm path | High | Low | The confirm runs on the **kill path only**, never on the tick path; it carries a **250 ms deadline** and is **fail-open ⇒ kill** on timeout. **Row recorded, mitigation stated, and the residual disclosed**: a stalled `smaps_rollup` read can hold the confirm until the deadline — it cannot hold the **tick** (the confirm is off the tick path) and it cannot prevent the kill (fail-open). Recorded in the phase report and the operator memo as a disclosed residual, not silently accepted |
| The thread's tick path logs and deadlocks on a held logger lock | High | Low | The wedge-capture rules are structural (Task 13): the tick path NEVER logs; only state transitions log. Asserted by a test |
| The confirm is made advisory ⇒ pure-RSS false-kills fork-heavy legitimate jobs at factor ≈N | **High** | Low | The confirm is **mandatory** (arch doc DQ1) and its fail-open is bounded by the deadline; a task in Phase 6 asserts the fair path is taken (not the RSS shortcut) |
| The leaderless-tree case silently stops the watch | **High** | Medium | **Leader ruling Q2** (Task 10): the watch stays alive until member count 0 and re-anchors on surviving members — a Phase-2 contract change, not a limitation; pinned by a test that backgrounds a ballooning grandchild |
| The identity gate fails open on an unreadable `/proc` | Medium | Low | P5 is **fail-closed** (unreadable ⇒ treat as exited ⇒ no kill); only P6 (environ) proceeds on unreadable, and it increments `ownership_unverified` |
| A kB/bytes confusion silently scales the ceiling by 1024× | **High** | Low | Task 9 pins the conversion in both directions; the bash lane's `peak_rss_kb` is KB and the single bytes→KB conversion is pinned at that boundary |
| The 8.11 GiB unit peak turns out to be daemon page cache ⇒ `D_max` shifts and 3 GiB is wrong | **High** | Medium | Pre-freeze gate (Task 20) — the attribution is recorded **before** the freeze; the interaction table is updated if the attribution changes `D_max` |
| The commissioned 8 GB is silently replaced by 3 GiB and the user never learns | **High** | Medium | Pre-freeze gate (Task 21): the final report and the operator memo **must** carry the amendment, the raise-path arithmetic, and the host-resize consequence |

## Exit Criterion

- The tree-RAM reader implements the **§4.1 five-function contract** with an
  injectable `proc_root`, one shared scan per tick, and no `/proc` reads in unit
  tests.
- The **PSS confirm** exists, is the **deciding** value, honours the 250 ms deadline,
  and is **fail-open ⇒ kill**; the **OVERCOUNTED re-arm** path works and is pinned.
- The verdict record carries **every** §4.4 field, the honesty fields are **homed
  here**, and the honest-peak bound is derived from the samples.
- The kill ladder is **sync**, thread-callable, idempotent, and guarded by **P0–P8**
  (each individually tested).
- The **leaderless-tree delta** (leader ruling Q2) is implemented and pinned.
- The envelope helper exists with the **exact** ruled key set and the **negative
  space** enforced at the producer.
- The **per-tick cost** and the **allocation rate R** are **measured** and reported.
- **Both pre-freeze gates are closed**: the 8.11 GiB peak is attributed, and the
  3-vs-8 GiB surfacing requirement is recorded as carried.
- The unit pack is **pure-predicate** and passes; the **import-direction** rule is
  asserted by a test.
