# Service Tool — Feature & Ops Note

| Field | Value |
|---|---|
| **Date** | 2026-09-15 (initial); **2026-09-16 (override — D4 reversed)** |
| **Status** | As-built under default-grant (override 2026-09-16) — operator/ops documentation for the `service` tool category (v1) |
| **Audience** | Daemon operators; agents holding `bash`/`proc` (default grant) or `tools.allow=["service"]` (explicit grant) |
| **Architecture invariant** | [`docs/architecture/instance-lifecycle.md`](architecture/instance-lifecycle.md) → *The invariant* (why services are kill-exempt) |
| **Plan** | `.agents/shared/planning/service-tool/plan-overview.md` (D1–D7 decisions, F13–F22 dispositions) |

---

## 1. What this feature is

The `service` tool category lets an agent start long-lived processes
(dev servers, databases, watchers) that:

1. **live outside instance lifecycle** — instance termination, cancellation,
   pause, cleanup, and GC never kill or reap them;
2. **survive daemon restart** via boot-time reconciliation against the
   `service_tracking` table;
3. **are NOT OS services** — never registered with launchd/systemd/launchctl;
   purely daemon-managed detached processes (`start_new_session=True`, the
   `upgrade_journal.spawn_executor` precedent).

Tool surface (5 tools, name-keyed, documented per-tool via `tool_help` /
`_full_doc_`): `service_start`, `service_stop`, `service_status`,
`service_list`, `service_logs`. Result states cover `running` / `exited` /
`not_found` / `pid_recycled` / `cap_exceeded` / `disabled` / `invalid_name` /
`invalid_argv` / `name_in_use` / `spawn_failed`.

### Access model — default-grant (override 2026-09-16)

As of override 2026-09-16 (D4 reversed by user directive — see
`.agents/shared/planning/service-tool/decisions.md` §D4 override note):

* **Default-grant** — `service` is **not** in `PRIVILEGED_TOOL_CATEGORIES`.
  Every agent whose **effective** toolset (post allow-expansion, post
  deny-strip) can include `bash` OR `proc` is granted the 5 `service_*`
  tools. Empty/absent `tools.allow` (default-universe agents) also
  receive the 5 tools via the default-open universe.
* **Meta-grant IFF policy** — the meta-grant is enforced via per-agent
  `meta.json` files: bash/proc-capable agents have `"service"` appended
  to their `tools.allow`. The filter does NOT auto-grant service when
  bash/proc is present — meta files carry the entry. Verified by
  `tools/dev/verify_service_default_open.py` (per-agent harness).
* **Per-agent opt-out** — set `tools.deny=["service"]` in the agent's
  `meta.json`. The agent keeps bash/proc but loses the 5 `service_*`
  tools.
* **Global kill-switch** — `ENSEMBLE_SERVICE_TOOL_ENABLED=0` flips every
  service tool call to the disabled marker shape (`{"status": "disabled",
  ...}`) regardless of allow/deny — see §5.

Before the override (D4 Option A, 2026-09-15), `service` was a member of
`PRIVILEGED_TOOL_CATEGORIES` (default-closed, explicit `tools.allow`
only). The override reversed that decision; the privilege-strip in
`daemon/tools/instance.py` (`_strip_privileged_category_tools` and the
`resolve_tool_filter` empty-allow branch) still strips the daemon-
internal authority trio (`system_upgrade`, `system-log`, `ens-db`)
unchanged — only `service` moved out.

---

## 2. Operator surface (knobs)

All names/defaults below are verified against the resolvers in
`daemon/config.py` (`_resolve_service_tool_enabled`,
`_resolve_service_tool_max_concurrent`,
`_resolve_service_tool_reconcile_interval`) and the `ServiceToolConfig` /
`ServicesConfig` fields. Precedence for every knob: **env > yaml > default**.
Empty-string env values are treated as unset; unrecognized non-empty kill-switch
values fail boot loud.

| Knob | Env | Default | Yaml surface | Notes |
|---|---|---|---|---|
| Kill-switch | `ENSEMBLE_SERVICE_TOOL_ENABLED` | ON | `services.service_tool.enabled` | OFF = byte-identical pre-feature behavior (§5) |
| Max concurrent services | `ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT` | `10` | `services.service_tool.max_concurrent` | Daemon-global across all instances; `ge=1` fail-fast at boot; enforced before spawn |
| Reconcile sweep cadence | `ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL` | `90` (seconds) | `services.service_tool_reconcile_interval_seconds` | ⚠️ the env name has **no** `_SECONDS` suffix; `ge=1` fail-fast at boot |

Two spellings intentionally coexist for the cadence knob: the `ENSEMBLE_*` env
drops the suffix, while the yaml/`ServicesConfig` field is
`service_tool_reconcile_interval_seconds` (house `{name}_interval_seconds`
convention). The BaseSettings binding `SERVICES_SERVICE_TOOL_RECONCILE_INTERVAL_SECONDS`
(`env_prefix="SERVICES_"` on `ServicesConfig`) also resolves, but the
explicitly-resolved `ENSEMBLE_*` env wins.

### Restart-pending semantics

- The `service_tracking` schema is created by a dual-dialect migration +
  `create_all`/`_ensure_postgres_columns` mirror — **schema changes require a
  daemon restart** to apply.
- Every knob is resolved **once, at `load_config` (config-resolution) time** and
  cached (`service_tool_enabled()` accessor reads the cache). **Flag/knob flips
  require a daemon restart**; editing the env mid-flight has no effect on a
  running daemon.

---

## 3. Deployment / Activation

> **Frozen-PyInstaller prod activation = REBUILD + restart. A daemon restart
> ALONE is insufficient.**

The new modules (`daemon/tools/service_tools.py`, `daemon/tools/service_spawner.py`,
`daemon/services/service_tool_manager.py`, `daemon/services/service_reconciliation.py`,
`daemon/repositories/service_tool/`, the manager wiring, the api lifespan mount,
and the config resolvers/boot probe) ship **inside the frozen binary**.
Restarting the daemon without rebuilding runs the OLD binary — none of the new
code paths exist in the running process, regardless of env or config.

**Activation sequence** (the project's frozen-ensemble-prod precedent):

1. **Rebuild the binary** (the frozen prod build pipeline).
2. **Restart the daemon.**
3. **Verify the boot-probe line** (§4) — this is the only trustworthy
   activation signal.
4. **FE rebuild only if/where FE surfaces are affected** — for v1, none are:
   the plan's F13 disposition is grep-verified *zero* frontend surface
   referencing the service tool or `service_tracking`, so no FE rebuild is
   required for this feature. Re-verify if a future iteration adds an FE
   surface.

---

## 4. Boot-probe verification recipe (run at FIRST post-rebuild boot)

`load_config` emits exactly one INFO line at config-resolution time (pinned
format; deliberately on the boot path so a quiet-daemon grep never false-fails):

```
[ServiceTool] service_tool_enabled=True (env ENSEMBLE_SERVICE_TOOL_ENABLED), max_concurrent=10, reconcile_interval=90s
```

Verify:

```
grep '\[ServiceTool\] service_tool_enabled=' data/logs/ensemble.log
```

> **If the line is ABSENT, the feature is NOT active** — even if `service_*`
> names appear in `tool_help` or an agent's warm tool list. The warm-list can
> show surface names without the backend being activated; the boot-probe line
> is the only signal that proves the resolved config state inside the running
> binary. Absent line after a "restart" ⇒ you are almost certainly still on the
> old binary (§3).

The same boot also logs the reconcile service's state from the api lifespan:

- `ServiceReconciliationService DISABLED (service_tool_enabled=False)` — kill-switch OFF;
- `ServiceReconciliationService DISABLED (no service_tool_manager)` — wiring absent (old binary / A8 guard);
- `ServiceReconciliationService DISABLED (interval < 1)` — knob below the floor;
- otherwise the awaited **guaranteed boot pass** emits:

```
[ServiceTool] reconcile_boot_sweep alive=N reaped=M errors=K
```

---

## 5. Kill-switch vs activation — two different operations

| Operation | What it takes | When the code is in the binary |
|---|---|---|
| **Flip the kill-switch** (`ENSEMBLE_SERVICE_TOOL_ENABLED=0` or back to ON) | env change + **daemon restart** — **no rebuild** | Already activated once (rebuild done) |
| **Activate the feature itself** | **REBUILD + restart** | First deployment only |

Once activated, the code is in the binary permanently; the kill-switch merely
gates behavior at runtime. OFF-state semantics (verified under the 2026-09-16
override — `service` is no longer privileged, so list-presence survives the
OFF flag):

* The `ServiceToolManager` gate closes — every tool call returns the
  structured disabled shape (`{"status": "disabled", ...}`).
* The `ServiceReconciliationService` never starts.
* The 5 `service_*` tools REMAIN in the agent's resolved tool list
  (override 2026-09-16 — they were previously stripped under D4 Option A).
  The OFF contract is now **call-time disable, not list-absence**: tool
  calls return the disabled marker, with zero DB rows written, zero
  probe/sweep effects.
* The `service_tracking` schema still applies — flipping back ON needs
  **no** migration replay.
* Note for hot-unwind: services already running when the switch goes OFF
  keep running as unmanaged OS processes (nothing signals them; there is
  no auto-reconcile while OFF).

For per-agent opt-out (instead of the global kill-switch), set
`tools.deny=["service"]` in the agent's `meta.json`. The agent keeps
bash/proc but loses the 5 `service_*` tools at resolve-time.

---

## 6. Post-deploy manual verification recipe (restart-survival)

End-to-end proof of the feature's one-sentence acceptance (plan SC-2):

1. **Start a service** via a bash/proc-capable agent (default grant
   under override 2026-09-16 — every worker / coder / tester / etc.
   agent qualifies) calling `service_start` (e.g. a long sleep
   process). Confirm the tool result carries `status: "running"`, a
   `pid`, and a `start_time`.
2. **Terminate that instance** (terminate cascade). The instance dies; the
   service must not.
3. **Verify the OS process SURVIVES**: `ps -p <pid>` still lists it, and
   `service_status(name)` still returns `status: "running"` with the same
   `(pid, start_time)`.
4. **Restart the daemon** (graceful shutdown → boot; on frozen prod, §3 rules
   apply).
5. **Verify boot reconciliation keeps the row correctly**: the boot pass emits
   `[ServiceTool] reconcile_boot_sweep alive=1 reaped=0 errors=0`, and
   `service_status(name)` returns `status: "running"` with a **live PID whose
   `(pid, start_time)` matches the original**.

Log-line spellings (verified in source):

- Boot pass (guaranteed, awaited in the api lifespan):
  `[ServiceTool] reconcile_boot_sweep alive=%s reaped=%s errors=%s`
- Periodic sweep summary — `[ServiceTool] reconcile_swept alive=%s reaped=%s starting_reaped=%s errors=%s` —
  is emitted **only when the sweep reaped something** (`reaped > 0` or
  `starting_reaped > 0`); a no-op sweep is silent by design (same shape as the
  job-lock sweep). Do not interpret a missing `reconcile_swept` line as a dead
  sweep — the boot line (§4) is the liveness proof.
- Per-reap rows log `[ServiceTool] reconcile_reaped ...`; per-call tool logs
  are `[ServiceTool] service_started ...` / `service_stopped ...` /
  `service_start spawn_failed ...` / `service_stop pid_recycled ...` (and the
  other F1 recycle variants).

Catch-all verification one-liner (boot probe + per-call logs):

```
grep '\[ServiceTool\]' data/logs/ensemble.log
```

---

## 7. Operator recovery: cap exceeded

The cap is daemon-global: `service_start` counts *active* (`starting`/`running`)
rows across **all** instances and refuses the N+1th **before** spawning (no PID
created, no DB row inserted):

```
{"name": "...", "status": "cap_exceeded", "reason": "max_concurrent_reached", "cap": 10, "active": 10}
```

Recovery options, in order of preference:

1. **Free slots** — `service_stop` the services that are no longer needed
   (stop is idempotent; each stop releases its slot immediately).
2. **Raise the cap** — set `ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT=<n>` (or
   `services.service_tool.max_concurrent` in yaml) and **restart the daemon**
   (knobs are resolution-time; §2). No migration, no other side effects.
3. If rows appear active but their processes are gone, no operator action is
   needed: the 90s reconcile sweep (and the boot pass) marks dead/recycled rows
   `EXITED`, which releases the slots — a stuck-full cap self-heals within one
   sweep interval.

---

## 8. Accepted limitations & follow-ups

Binding dispositions from the plan's Deferred/Backlog table (council findings
F13–F22); not re-derived here.

| Item | Status | Note |
|---|---|---|
| **F18 — cap=10 is ADVISORY (TOCTOU)** | **Accepted for v1** | The cap check (`list_active()` count) and the insert are not one atomic step; under concurrent starts the count can briefly exceed the cap (e.g. 9→11 under `cap=10`). Accepted; do not page on `active` momentarily exceeding `cap`. |
| **CODEOWNERS fence** | **Pending** | No `.github/CODEOWNERS` exists, so "kill-site inventory changes require owner review" is not yet enforceable via code ownership. The A7 grep-gate (`test/packs/service_tool_kill_site_invariant.sh`) is the enforceable fence today — see [`docs/architecture/instance-lifecycle.md`](architecture/instance-lifecycle.md) → *The enforceable fence* and its *CODEOWNERS fence: TODO* section. |
| **F14 / OQ#1 — `default_open` revisit trigger** | **Trigger-defined, NOT pre-committed** | The per-category `default_open` mechanism is revisited **only if** a second tool category ever needs "default-closed but non-privileged" semantics (architect §1.5). Until then `PRIVILEGED_TOOL_CATEGORIES` is the single mechanism; do not build the refactor speculatively. |
| **OQ#2 — single-daemon-only** | **Documented constraint** | v1 assumes exactly one daemon against the database. Audit columns (`started_by_instance_id`, `started_by_agent_id`) attribute starts; a `daemon_instance_id` column is additive later only if multi-daemon becomes a real ask. |
| **F15 — grandchild-setsid escape** | **Documented** | A service child that itself calls `setsid(2)` (daemonizing wrapper) escapes `service_stop`'s group signal — recorded in the `service_stop` `_full_doc_`. |
| **F22 — `start_time` capture under slow macOS `ps`** | **Accepted for v1** | Cross-platform start-time parse is `ps`-based on macOS (OQ#5); a slow `ps` widens the capture window. |
| **F16 — crash between Popen-success and pid-UPDATE** | **Accepted** | The A3 eternal-`starting` reaper may mark such a row EXITED with `reason=spawn_failed_or_interrupted` even though a process exists — accepted misreport (Risk #17). |
| **OQ#3 — log rotation** | **Operator-side** | `service_logs` tails are capped (`MAX_LOG_TAIL_BYTES`, 10 MB) but on-disk service logs under `data/services/` are **not** daemon-rotated — same policy as `data/logs/ensemble.log`. Operators own rotation. |

---

## 9. Related

- [`docs/architecture/instance-lifecycle.md`](architecture/instance-lifecycle.md) — the
  kill-exemption invariant, the K1–K13 inventory, the CI grep-gate, and the
  standing registry-scoped rule for future kill-site authors.
- Per-tool agent-facing behavior: `tool_help('<tool>')` / the `_full_doc_`
  strings in `daemon/tools/service_tools.py` (result shapes, PID-reuse defense,
  `exit_code=None` semantics).
