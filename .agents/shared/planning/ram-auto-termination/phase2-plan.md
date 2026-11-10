# Phase 2: Measurement Substrate

## Objective

Build a **hermetic tree-RAM measurement** module that answers one question — "how
much RAM is this process tree using right now, and what is its observed peak since
the guard started watching?" — with **no tool wiring at all**. Both the bash lane
(Phase 3) and the proc lane (Phase 4) consume this one module; the contracts frozen
in Phase 1 are its specification.

The module must be testable against a **fake `/proc` tree** (the injectable
`proc_root` seam that `proc_readers.read_child_snapshot` already uses,
`proc_readers.py:16-18`, `:737-758`), so the measurement logic is provable without
allocating real RAM.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Create the package** `daemon/services/ram_guard/` with `__init__.py`, `tree_reader.py`, `verdict.py`, `guard.py`, `errors.py` — the shape mirrors `daemon/services/resource_monitor/` | Phase 1 Task 12 | Package exists; no import of `daemon/tools/*` (no cycle back into the tools layer) |
| 2 | **Implement the tree-RAM reader** per the DQ1 ruling: enumerate the tree members (DQ3 primitive) and compute the tree total in the ruled unit (bytes or KiB). Reuse `bash_spawn_reaper.list_group_members` (`bash_spawn_reaper.py:382-460`) if the ruling selects pgid enumeration — it is fail-closed (`None` = "could not enumerate") and excludes zombies + kernel threads | Phase 1 Tasks 1, 3 | A pure function `(pgid) -> TreeReading | None`; `None` on enumeration failure (never `0`); the failure mode is distinguishable from "tree is empty" |
| 3 | **Implement the per-pid read** reusing the `proc_readers` shape — read `VmRSS` from `/proc/<pid>/status` with the **injectable `proc_root`** so fixtures can supply a fake tree. Keep `proc_readers` untouched (no coupling from the fast guard into the 30 s monitor) | Phase 1 Task 1 | The reader works against a `tmp_path`-rooted fake `/proc` with no host reads; the unit conversion is exactly the ruled unit |
| 4 | **Implement the peak tracker** in `verdict.py`: a per-spawn record holding the **observed peak** (max over samples), the sample count, the breach flag, the breach timestamp, and the observed members. The peak MUST be the guard's **own sampled peak** — never the spawn-time `VmHWM` floor (which the bash lane's `peak_rss_kb` already under-reports, `bash.py:1096-1109`) | Phase 1 Task 1 | A `PeakRecord` whose peak is monotonic non-decreasing and is ≥ every sample taken; a unit test proves the peak survives a sample of a shrinking tree |
| 5 | **Implement the breach evaluation**: `total >= ceiling` ⇒ breach (the comparison direction and the equality semantics are per the DQ1 ruling — an exact-equality breach must be decided, not left implicit) | Phase 1 Tasks 1, 2 | A pure predicate with the boundary case tested at exactly `ceiling`, `ceiling - 1`, and `ceiling + 1` |
| 6 | **Implement the per-tick cost measurement** (Phase 1 Task 2 requires a budget): instrument the reader's per-tick cost and record it — the design needs a real number to set the poll interval, not a guess | Phase 1 Task 2 | A measurement harness reports the median + p95 per-tick cost against a synthetic tree of a stated size; the number is recorded in the phase report |
| 7 | **Implement the poll loop home** per the DQ2 ruling. If the dedicated-thread home is chosen: one `threading.Thread(daemon=True)` (the wedge-capture precedent, `daemon/services/wedge_capture/watchdog.py:1-49`), a RAM-only registry of live spawns, and the **two wedge hard rules**: the tick path NEVER logs, and no lock is held across a kill step | Phase 1 Task 2 | The thread starts/stops cleanly; a tick that raises never kills the thread; the tick path emits no log line (asserted) |
| 8 | **Implement the kill ladder** per the DQ4 ruling — a **sync** ladder usable from the thread (the thread must be able to `os.killpg` without an event loop). Include the hard guards (`pgid > 1`, `pgid != daemon's own group`, `pgid != self`), the ownership re-verification where the ruling requires it, and the grace default + bounds | Phase 1 Tasks 3, 4 | The ladder is callable from a bare thread; every guard is individually unit-testable; a `kill_fence`-style fixture can hard-raise on a real `os.killpg` (mirroring `tests/unit/services/test_bash_spawn_reaper.py`) |
| 9 | **Write the substrate unit tests**: fake-`/proc` tree readings (single process, tree of children, shared-page double-count semantics per the DQ1 ruling), unreadable-`/proc` behaviour, enumeration-failure behaviour, peak monotonicity, breach-boundary cases, ladder guards, and the tick-path-no-log rule | Task 7 | The pack is **pure-predicate** (no real subprocesses, no real RAM); it runs in the unit tier and never touches the host's `/proc` for the daemon's own processes |

## Coupling

- **Tight with:** Phase 3 and Phase 4 — both consume this module; a change to the
  reader contract changes both lanes. This is why the contract was frozen in Phase 1.
- **Tight with:** Phase 1 — every implementation detail is the ruling, not a choice.
- **Loose with:** Phase 5 — the guard must expose the values the contract needs
  (`observed_peak_bytes`), but the guard does not own the error envelope.
- **Independent of:** the `resource_monitor` package — it is **not** modified and
  **not** imported; the fast guard is a separate subsystem (the 30 s sampler's
  cadence is 100× too slow to be the enforcement path, and its config family is
  coupled to the yaml-literal trap).

## Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| The reader couples the fast guard to the slow monitor (`proc_readers` import) | Medium | Medium | Task 3 reads the **shape**, not the module; `proc_readers` is not imported by `ram_guard`. If a shared helper is wanted, it moves to a neutral location in this phase, deliberately |
| The per-tick cost is worse than the poll budget (a /proc scan is O(host pids)) | Medium | Medium | Task 6 measures it BEFORE the interval is fixed; if the cost is too high, the shared-single-scan design (one walk serves all live spawns) is the fallback |
| The thread's tick path logs and deadlocks on a held logger lock | High | Low | The wedge-capture rules are explicit: the tick path NEVER logs; only state transitions log. Asserted by a test |
| The kill ladder is called from a thread while the event loop concurrently kills the same group | Medium | Low | The ladder is idempotent; the group kill is safe to repeat; the verdict record is the single source of truth for "already killed" |
| The peak tracker drifts from the kernel's own accounting (the tree shrinks between samples) | Low | Medium | Task 4's monotonicity test plus the honest-under-report framing: the reported peak is the guard's **sampled** peak, and the error message says so |

## Exit Criterion

- The tree-RAM reader, the peak tracker, the breach predicate, the poll home, and the
  kill ladder exist as a **standalone module with no tool wiring**.
- The unit pack is **pure-predicate** (no real subprocess, no real RAM, no host
  `/proc` reads beyond the fake tree) and passes.
- The **per-tick cost is measured and reported** as a number, so the poll interval
  default in Phase 1's ruling can be confirmed or amended.
- The module can be driven **from a bare thread** with no event loop — proven by a
  test that runs the kill ladder in a thread.
