# Instance Lifecycle: Process Kill-Reach & the Service-Tool Kill-Exemption Invariant

| Field | Value |
|---|---|
| **Date** | 2026-09-15 |
| **Status** | As-built — documents the invariant proven by the service-tool feature (Phases 1–3) |
| **Scope** | Which OS processes the daemon can kill, and why `service`-category processes are exempt by construction |
| **Companion doc** | [`docs/service-tool.md`](../service-tool.md) → *Deployment / Activation* (operator surface, verification recipes) |
| **Evidence base** | `.agents/shared/planning/service-tool/research-lifecycle-killsites.md` → *Kill-site inventory table* (K1–K13) |

---

## 1. The invariant

> **Service processes are kill-exempt from every daemon lifecycle teardown — not by
> bookkeeping discipline, but by construction.**

The exemption holds because a `service_start` process satisfies **two independent
conditions**, and the daemon's entire kill inventory requires at least one of them to fail:

1. **It is never registered in any per-instance process registry.** The two spawn
   registries the daemon owns — the bash tool's `BashProcessRegistry`
   (`daemon/tools/bash.py`) and the proc-run `BackgroundProcessManager`
   (`daemon/tools/proc_tools.py`) — are the only membership lists the lifecycle
   teardown paths consult. Neither `cleanup_instance` nor `cleanup_all` on either
   registry can reach a process that was never inserted. The `service` spawner
   (`daemon/tools/service_spawner.py`) inserts into the `service_tracking` *database
   table* for status and reconciliation bookkeeping — deliberately into **no**
   teardown registry.

2. **It is spawned with `start_new_session=True`.** The child becomes its own
   session and its own process-group leader (`pgid == pid`, ≡ `setsid(2)`), so no
   `os.killpg` fired against an *inherited* group — a bash invocation's group, a
   `proc_run` handle's group, the code-server group — can reach it or any of its
   fork-children.

Kill only ever happens at the intersection of *registry membership* and *group
membership*. A service process belongs to neither.

### The binding precedent

`upgrade_journal.spawn_executor` (`daemon/tools/upgrade_journal.py`) is the prior
art this invariant formalizes. It spawns the upgrade executor with
`subprocess.Popen(..., start_new_session=True, close_fds=True, stdin=DEVNULL,
stdout/stderr → log file)` and its docstring states the child is deliberately NOT
registered in `BashProcessRegistry` or any other teardown registry — it "must
survive BOTH tool-harness teardown and daemon death". The `service` category
generalizes that one-off escape hatch into a first-class, name-keyed tool surface
with persistence, reconciliation, and abuse guards.

---

## 2. Why this is provable by construction: the K1–K13 inventory

The full daemon process-kill inventory (K1–K13, catalogued in
`.agents/shared/planning/service-tool/research-lifecycle-killsites.md` →
*Kill-site inventory table*) is **entirely registry-scoped**. The daemon contains:

- **no `/proc` walk** for killing (the only `/proc/<pid>/...` reads are per-PID
  ownership/liveness reads for processes the caller already tracks),
- **no `killpg(0)`** (process-group zero — "every group in my session"),
- **no ppid-tree traversal**,
- **no session-wide sweep** (no `pkill`, no `psutil.process_iter` kill loops).

Every kill site signals a PID or group it first obtained from one of the three
inventoried registries:

| Cluster | Sites (symbols) | Registry that scopes the reach |
|---|---|---|
| Bash tool teardown | `BashProcessRegistry` cleanup paths + the in-call timeout/cancelled group kills | Groups captured at bash spawn, per owning instance |
| Proc-run surface | `BackgroundProcessManager` `_attempt_kill_signal` / race-guard / `cleanup_instance` / `cleanup_all` | `proc_run` handles tracked per owning instance (3-layer PID-ownership verify) |
| Code-server lifecycle | `VSCodeServerManager` stop / adopted-pid stop / `_kill_orphan` | The code-server PID/pgid from spawn or PID file |
| Git subprocess timeouts | `git_diff_service` / `doc_commit_service` `subprocess.run(..., timeout=...)` stdlib kill | Direct short-lived child only |

A process that is (1) unregistered and (2) `setsid`-detached is unreachable by
every site in the table. The same inventory records the two *benign* non-kill
hits so future sweeps don't miscount them: the sig-0 liveness probe
`upgrade_journal._pid_alive` (`os.kill(p, 0)` — a signal-less existence check)
and the killpg-semantics comment in the job feedback observer.

---

## 3. Regression net

The exemption is pinned behaviorally by the parametric kill-site matrix:

- `tests/integration/test_service_tool_kill_site_exemption.py` — one test per
  kill-site cluster (`test_k1_k2_*` … `test_k13_*`), plus a full-cascade case
  (`test_f9_full_cascade_*` — real `terminate_instance` cascade) and a summary
  pin. Each test starts a real service via the real `ServiceToolManager.start`
  path on a file-backed SQLite engine, triggers the kill site with a real
  bash/proc/vscode subprocess registered to a *different* instance bucket, and
  asserts the service PID is still alive (`kill -0`) and its `service_tracking`
  row is unchanged.

Plan exit criterion: 13/13 sites pass on darwin (the Windows branch K12 is
`skipif`-gated there).

---

## 4. The enforceable fence: the CI grep-gate

Documentation alone cannot stop a *future* kill site from appearing silently. The
enforceable fence is the A7 grep-gate pack:

- **`test/packs/service_tool_kill_site_invariant.sh`** — greps every
  `daemon/**/*.py` for the kill-primitive token set `os.killpg(`, `os.kill(`,
  `killpg(0)`, `/proc/[0-9]`, `pkill`, `process_iter` and **fails the gate** when
  a hit lands in a file outside the allowlist.

**The allowlist** (from the pack header; each entry carries its justification in
the script):

| Entry | Justification |
|---|---|
| `daemon/tools/bash.py` | bash tool teardown (orphan-reap guard, pipe-hang discipline) |
| `daemon/tools/proc_tools.py` | proc tool surface (`_verify_pid_ownership`, `_attempt_kill_signal`, SIGTERM→5s→SIGKILL stop) |
| `daemon/services/vscode_server_manager.py` | code-server lifecycle stop |
| `daemon/tools/upgrade_journal.py` | benign sig-0 liveness probe (`_pid_alive` — `os.kill(p, 0)` sends no signal) |
| `daemon/tools/service_spawner.py` | plan-sanctioned: `service_spawner.stop` group signals + `/proc/<pid>/stat` start-time read (PID-reuse defense) |
| `daemon/tools/service_tools.py` | docstring-only mentions of the sanctioned mechanism (no executable signal site) |
| `daemon/services/service_tool_manager.py` | docstring-only mention of the liveness ping (no executable site) |
| `daemon/repositories/service_tool/` | docstring-only mentions of the liveness ping (no executable signal site) |

**The rule:** a PR that introduces a kill-primitive site in an un-inventoried
`daemon/` file fails the gate **unless the allowlist is extended in the SAME PR
with a justification comment** — forcing the new site through review as an
explicit, inventoried decision. Allowlisting means "signal sites here are
REVIEWED and INVENTORIED", not "anything goes"; the Phase 2 behavioral matrix
(§3) is the companion enforcement for the sites already inside.

---

## 5. STANDING RULE for future kill-site authors

> **Any new cleanup/kill path MUST stay registry-scoped.**
>
> Registry-scoped means: **you may only signal processes your own registry
> tracks.** Never add, in `daemon/`:
>
> - `/proc` directory walks for kill-enumeration,
> - `killpg(0)` (signals every group in the session),
> - `pkill` / `psutil.process_iter` sweeps,
> - ppid-tree traversal kills,
> - signalling a PID obtained from anywhere other than your own registry entry
>   (with ownership re-verification at signal time, per the
>   `(pid, start_time)` defense).

Rationale: the kill-exemption invariant in §1 is a *construction* proof — it
holds only while the daemon-wide inventory remains registry-scoped. One
session-wide sweep anywhere in `daemon/` would retroactively break the
`service` category's survival guarantee (and the upgrade-executor precedent)
without any single registry misbehaving. If you genuinely need a broader reach,
do not smuggle it in — extend the A7 allowlist with a justification (§4) and
bring the design to review.

---

## 6. What problem `service` solves: the registry limitation it escapes

The two registries that scope ordinary spawns are **kill-on-teardown by design**
and **memory-only by design**. Their own docstrings (quoted by symbol; re-locate
in the source) state the behavior the `service` category deliberately escapes:

- `BashProcessRegistry.cleanup_instance` (`daemon/tools/bash.py`) — "Kill all
  tracked process groups for one instance." Invoked by the instance-termination
  cascade (`daemon/services/instance_lifecycle.py` terminate path, after the
  proc-manager equivalent), SIGKILLing every bash group the instance spawned.
- `BashProcessRegistry.cleanup_all` — "Kill every tracked bash process group
  during daemon shutdown", with two *documented* known limitations:
  (1) truly-detached orphans whose child called `setsid` sit outside the
  original process group so `killpg` cannot reach them; (2) a crash-recovery
  leak — the in-memory registry does not survive a daemon restart, so processes
  from a hard crash cannot be enumerated.
- `BackgroundProcessManager.cleanup_instance` (`daemon/tools/proc_tools.py`) —
  "Kill every process for `instance_id` and release resources … Called from
  instance-lifecycle code on instance termination / error."
- `BackgroundProcessManager.cleanup_all` — the daemon-shutdown sweep ("Kill ALL
  background processes across ALL instances"), carrying the same two documented
  limitations (setsid-detached orphans; in-memory registry lost on crash).

So under `bash` / `proc_run` today: an instance's processes die on instance
termination (`cleanup_instance`), on daemon graceful shutdown (`cleanup_all`
from the shutdown path in `daemon/manager.py`), and are *untracked after a hard
crash* (crash-recovery leak). The `service` category inverts all three: service
processes are **not** in either registry, so they survive termination, survive
cancellation/pause (which never touches the registries for services), survive
graceful shutdown, and — because state lives in the `service_tracking` table —
are re-verified against the kernel after restart by boot reconciliation rather
than lost.

The one residual reach that remains — and is *documented, not fixed*: a service
process whose own child calls `setsid(2)` again (a daemonizing wrapper) escapes
even `service_stop`'s group signal. This grandchild-setsid escape is recorded in
the `service_stop` `_full_doc_` (plan finding F15) and in
`docs/service-tool.md` → *Accepted limitations & follow-ups*.

---

## 7. CODEOWNERS fence: TODO

A `.github/CODEOWNERS` file does **not exist** in the repository today, so the
"changes to the kill-site inventory require owner review" fence is **not yet
enforceable via code ownership**. Until it lands, the A7 grep-gate (§4) is the
enforceable fence. Standing follow-up (tracked in
`docs/service-tool.md` → *Accepted limitations & follow-ups*): add a
CODEOWNERS entry mapping `daemon/tools/bash.py`, `daemon/tools/proc_tools.py`,
`daemon/services/vscode_server_manager.py`, `daemon/tools/service_spawner.py`,
and `test/packs/service_tool_kill_site_invariant.sh` to the owning team, so
allowlist extensions cannot merge without that owner.
