# O8 Audit — Stale-Subprocess Census/Verdict (O8a) + systemd KillMode Audit (O8b)

**Commission:** resource-cost-control, item O8 (P7 subset), 2026-10-18.
**Worktree:** `ensemble-src-wt-resource-cost-control` @ `feature/resource-cost-control`.
**Commits:** O8a implementation `a94000ae5` (sweep module + `__main__` wiring + 22 test pins).
**Census raw output:** delivered in the mission report per commission constraint
("do NOT commit census output"); this note records method, class structure,
counts, journal anchors, and verdicts. Path pinned here per plan D14; P8's
runbook references this file.

---

## 1. Census method (read-only, zero mutations)

`/proc` walk via reads only — `/proc/*/stat` (comm/state/ppid/pgrp/session,
starttime = field 22), `cmdline`, `status` (VmRSS), `cgroup`, plus `/proc/stat`
`btime` for wall-clock ages. **No signals, no writes, no systemctl, no daemon
or DB contact.** Stale-vs-current ground truth: the current incarnation's
starttime and lineage (ancestors + descendants) walked from ppid pointers.
Cross-checked against `journalctl -u ensemble-main.service` (read-only) for
the Oct-17 night events.

## 2. Census findings (2026-10-18 ~14:19Z)

**Box state: CLEAN.** The unit cgroup `/system.slice/ensemble-main.service`
held 25 members — ALL current-incarnation: launcher → PyInstaller bootloader
→ daemon (started 01:40:20Z, 2.9 GB) + 22 tree descendants (context7-mcp and
computer-use-mcp chains — **setsid'd, own pgid/session, but in-cgroup** — an
agent-spawned code-server, vitest/tsc/npm test-farm processes). Zero members
older than the daemon's start outside its lineage. The 4.2-day context7
orphan from the 2026-10-17 recon is gone (cleared by the 01:40Z systemd-level
restart). Sibling unit `ensemble-demo.service` carries its own MCP chains —
**structurally excluded** by the cgroup-membership clause.

**Journal anchors (the leak class is real, not theoretical):**

| When (UTC) | Event | Evidence |
|---|---|---|
| 10-17 17:20:05 | Promote stop | systemd SIGKILLed a **`systemd-run`** process *inside* the unit cgroup → transient-scope escape class exists |
| 10-17 23:19:13–14 | Kernel OOM kill | systemd reacted with a cgroup-wide SIGKILL sweep (~20 procs: npm/sh/node/pytest/python×9/sleep/tail chains); final cgroup kill EINVAL (benign race); **`Unit process 3393684 (MainThread) remains running after unit stopped`** — a survivor of the FULL sweep |
| 10-17 23:19 → 10-18 01:40 | Unit stayed down | no `Started` line in between; current incarnation started by the recovery lane (`Started ensemble-main.service` 01:40:15Z) |
| 10-18 01:40:20Z | Current incarnation | census: 25 cgroup members, all in-lineage; launcher −6 s, bootloader −1 s vs daemon start |

## 3. O8a gate verdict: PROCEED (predicate well-scoped)

The commission's gate asked for a clear, safe predicate. It exists and every
clause is load-bearing (pinned by tests):

1. **member of the daemon's own cgroup** (exact cgroup-path equality) —
   structurally excludes pid 1, systemd, sibling units (demo daemon), user
   sessions, and the `systemd-run`-scope escape class;
2. **starttime (field 22) strictly older than this process's own** — the
   prior-incarnation oracle;
3. **not self / not an ancestor / not a descendant** — the ancestry clause is
   what keeps the launcher + bootloader (which predate the daemon inside the
   same cgroup) out of the kill set; without it the sweep would reap them into
   a systemd restart loop;
4. **not a kernel thread / not a zombie.**

Every signal re-verifies the **PID-recycle guard** (starttime + pgid + cgroup)
immediately beforehand, at TERM and again at KILL. **Inertness proven live:
the committed module, run read-only against the real box (signal path
poisoned to raise), scanned 183 processes, found 0 candidates, never reached
the signal call.**

**Why the sweep adds value despite systemd's stop-path coverage:** a clean
`systemctl` stop/restart kills the whole cgroup (KillMode=mixed escalates to
SIGKILL after `TimeoutStopSec=90`) — but (a) the kernel OOM killer runs no
stop phase, and its reactive cgroup sweep can still leave survivors
(pid 3393684, observed), and (b) daemon starts that are not systemd
stop/start pairs — the launcher's in-life relaunch after a child crash, dev
and manual launcher starts — run no cgroup kill at all, which is how the
4.2-day orphan accumulated. The sweep runs on every start path, bounded
(TERM → ≤30 s grace → SIGKILL, then stops), logged, and default-ON with
`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES` as the kill-switch.

**Residual (documented, out of scope):** children that live in
`systemd-run` transient scopes outside the unit cgroup survive unit stops and
are invisible to the sweep's predicate by design — reaping those would
require touching non-unit cgroups (LR5-forbidden agent-side live cleanup).
No such stale scope exists on the box today.

## 4. O8b KillMode audit verdict: NO unit-file change warranted

Template `scripts/systemd/ensemble-daemon.service` (the adopted unit is
generated from it — §5): `Type=simple`, `Restart=on-failure`,
`RestartSec=10`, `RestartPreventExitStatus=78`, `SuccessExitStatus=143`,
**`KillMode=mixed`**, `TimeoutStopSec=90`, `KillSignal` unset (default
SIGTERM), `Environment=MALLOC_ARENA_MAX=2` (O1).

- **Stop path:** mixed = SIGTERM to the MainPID (launcher) only, preserving
  the launcher's single-TERM contract; after 90 s systemd SIGKILLs all
  remaining cgroup members. `setsid`/double-fork do **not** escape — cgroup
  membership, not session, decides the escalation, and the census shows the
  setsid'd MCP chains are in-cgroup (journal 23:19:14 killed exactly those
  chains).
- **Escape classes that actually exist here:** (1) the OOM path (no stop
  phase; observed survivor) — closed by O8a, not fixable by any KillMode
  (KillMode only governs stop-phase kills); (2) transient-scope spawns
  (§3 residual); (3) D-state SIGKILL-pending stragglers — the sweep logs and
  leaves them, matching systemd's own behavior.
- **Deliberately not changed:** switching to `KillMode=control-group` would
  cgroup-wide-TERM the tree and trip uvicorn's `force_exit` teardown (the
  documented reason mixed was chosen — template header). Memory bounds
  (`MemoryHigh`/`MemoryMax`) are P8 operator-checklist items, not code here.
- **Zero unit mutations performed.** Live unit adoption stays operator-lane.

## 5. Addendum: adopted-unit MALLOC_ARENA_MAX inheritance

Question: does the ADOPTED-unit path inherit O1's
`Environment=MALLOC_ARENA_MAX=2`, given the live daemon runs via the adopted
unit, not the repo file?

**Verdict: derivation from the repo template holds — inheritance is
automatic for every future (re)adoption. No patch, no commit.**

Evidence (adopt-unit.sh read end-to-end, not just the two cited spots):

- `_resolve_template` (:364–375) resolves **exactly one** unit source —
  `scripts/systemd/ensemble-daemon.service` (three same-file path fallbacks);
  it REFUSES (exit 78) when the template is absent. There is **no second
  template, no embedded env block, no heredoc unit body** (the only heredoc
  in the script is the usage text, :96), and no alternate generation lane —
  `UNIT_CONTENT` is set only by `_generate_unit`'s line loop.
- `_generate_unit` (:417–445) splices exactly five literal tokens
  (`__GENERATED_NOTE__`, `__UNIT_LABEL__`, `__USER__`,
  `__WORKING_DIRECTORY__`, `__LAUNCHER__`) plus the `__ENV_LINES__` line,
  which `_read_config` (:302–311) fills with **PORT + POSTGRES_* only**;
  every other template line passes through byte-for-byte; a torn-token guard
  (:433–436) refuses surviving `__TOKEN__`s. The static
  `Environment=MALLOC_ARENA_MAX=2` line is a non-token line → **flows into
  the generated unit unchanged**. The W3 mutation pin (`b3cd5e5c1`: deleting
  the template line breaks the generated-output pin) independently proves
  generated output derives from the repo template. (`:551` masking is
  display-only.)

**Nuance for the live host:** the installed unit
(`/etc/systemd/system/ensemble-main.service`, root:root 0600) is dated
**Sep 30 07:41 — pre-O1** — so it does not yet carry the line. Inheritance is
retroactive-free: the next adoption/regeneration (operator lane, per LR5;
agents never touch the live host) picks it up automatically. Operators who
want it sooner can add a drop-in `override.conf` (the template header notes
this override path).

## 6. Test evidence

- `tests/test_opt_o8_stale_subprocess_sweep.py` — **22/22 passed**
  (`.venv/bin/python -m pytest tests/test_opt_o8_stale_subprocess_sweep.py -q`).
  Fenced: synthetic `/proc` trees under `tmp_path`; `os.kill` monkeypatched
  to a hard raiser (a real signal is unreachable from tests); `time.sleep`
  stubbed; ambient env scrubbed.
- **Mutation evidence** (each applied to committed `a94000ae5`, test run,
  reverted, 22/22 re-verified):
  1. `DEFAULT_ENABLED = False` → `test_default_is_on_pinned` FAILS;
  2. ancestry exclusion dropped → `test_ancestors_launcher_and_bootloader_never_signaled`
     FAILS;
  3. KILL-phase recycle guard skipped → `test_recycle_during_grace_skips_kill`
     + `test_vanish_during_grace_skips_kill` FAIL.
- Live inertness probe (read-only, poisoned signal path): scanned=183,
  candidates=0, actions=0; kill-switch-off path: scanned=0 (zero reads).
- Nearest-neighbor `tests/test_adopt_unit.sh` re-run green post-landing
  (template surface untouched; insurance run).
