# Architecture Recommendation: `service` Tool Category — Phase-0 Gate

Date: 2026-09-15
Author: Architect (controller) — synthesis of 1 council + 3 verification workers
Status: **Phase-0 gate PASSED with conditions** — D4 resolved; Phase 1 may start after the plan amendments below are folded in
Inputs: `plan-overview.md`, `decisions.md`, `technical-analysis.md`, `phase1/2/3-plan.md`, `research-lifecycle-killsites.md`, `research-persistence-migrations.md`, `research-tools-subsystem.md`
Evidence instances: council governor `3c72a461-30ae-41ef-bf3e-ffa914c681c9` (councilors `f9d3cc7b` agentic + `8430aa9a` coding, skill `security-design`); workers `bff94de7` (`resilience-design`), `7648daae` (`structural-design`), `a235af66` (`data-flow-design`)
Anchor caveat: the worktree carries a foreign session's dirty state on `latest`; all file:line anchors were verified as the tree stands and several plan-time anchors have drifted (see Amendment A10). Implementers MUST re-locate every anchor by symbol, not line number.

---

## 1. D4 / OQ#1 — RESOLVED VERDICT: **Option A**

**Add `"service"` to `PRIVILEGED_TOOL_CATEGORIES` (`daemon/tools/_tool_registry.py:124-128`), updating THREE pin tests in the same PR (not two — see §1.3), and rewrite the set's comment to the behavioral criterion.**

### 1.1 Rationale

- **The frozenset's real semantics are behavioral, not "daemon-internal."** The membership criterion that has actually governed the set is *"never default-granted; reachable only via explicit `tools.allow`."* The D7/attestation precedent (which kept `attestation` OUT of the frozenset) turned on *"does this category need filter-level default-deny?"* — not on daemon-internality. `service` mints persistent, daemon-escaping OS authority that no registry-scoped kill site can reach; it clearly needs filter-level default-deny. "Privileged = daemon-escaping authority" is a defensible reading, and the comment rewrite makes it the *documented* reading.
- **Option A's blast radius is naming-only — verified, not assumed.** Both councilors independently grepped the repo: production consumers of `PRIVILEGED_TOOL_CATEGORIES` are confined to three behavioral sites implementing one identical semantic — the empty-allow filter (`instance.py:331`), the strip helper `_strip_privileged_category_tools` (~`instance.py:4743-4760`, called from `:4793`; plan's `:4667-4684` anchor is stale), and the docs mirror (`help.py:56`, transitive through `loader.py:144/184/199`). Zero frontend, UI-badge, telemetry, boot-probe, or docs consumers. Nothing treats the set as a closed internal trio.
- **The pin net EXTENDS to `service` under A.** Three exact-equality asserts (verified by the architect's own grep, §1.3) scream on both silent removal (fail-open regression) and silent addition. This is the strongest existing protection story for a trust-tier boundary in the repo.
- **The only critical-path failure mode is shipping `service` NOT in the frozenset** — it auto-leaks to every empty-allow agent (canonical example: `watcher`, per the category comment at `_tool_registry.py:110-113`). Default-closed is non-negotiable; A delivers it with zero new mechanism.
- **User requirement fit:** agents CAN be granted it (explicit `tools.allow=["service"]`), it is default-closed (frozenset strip), and the "not daemon-internal like the trio" objection is a vocabulary concern fully mitigated by the comment rewrite (§1.2) — not a behavioral one.

### 1.2 Required changes (v1, Option A)

| File | Change |
|---|---|
| `daemon/tools/_tool_registry.py:124-128` | Add `"service"`; rewrite comment `:105-123` to the behavioral criterion ("never default-granted; explicit `tools.allow` only") + one-line service rationale; fix the pin-checklist comment to name **three** pin files |
| `tests/unit/tools/test_upgrade_registration.py:105` | Equality set + docstring |
| `tests/unit/tools/test_attestation_registration.py:158` | Equality set + docstring (the `:152` NOT-in assert stays green) |
| `tests/integration/test_maintenancer_spawn_resolves_tools.py:292` | **The missed third pin** — equality set + "exactly four" docstring |
| `daemon/tools/instance.py:~4747`, `daemon/tools/help.py:41` | Opportunistic stale-docstring fixes |
| Standard 3-step seam | `CATEGORY_MODULES` entry + `DYNAMIC_TOOL_NAMES` + `KNOWN_TOOL_NAMES` regen + factory/extend in `create_instance_tools` + loader warm-list + cold-boot doc test (identical under every option) |

**Riders (cheap, security-relevant — adopt in Phase 1.C/3):**
1. **Innate-skill negative pin:** assert no `INNATE_SKILL_TOOL_CATEGORIES` value ever intersects `PRIVILEGED_TOOL_CATEGORIES` (`instance.py:157-164`, `:186-197` append to `allow` regardless of frozenset membership — a pre-existing bypass seam that `service` makes worth pinning).
2. **SC-6 behavioral test** (already specified at plan-overview.md:185): default-configured agent resolves zero `service_*` tools; `tools.allow=["service"]` resolves all five.
3. **Runtime authority riders:** PID-ownership verification in `service_stop` may layer the `ENSEMBLE_PROC_TRACKING_ID` env-tag check (`proc_tools.py:228-242`) on top of the `(pid, start_time)` match (defense in depth, optional); the schema already carries `started_by_instance_id` + `started_by_agent_id` audit columns (repudiation gap adequately closed for v1; `daemon_instance_id`/`work_id` additive later if OQ#2 ever reopens).

### 1.3 Critical discovery — the frozenset is TRIPLE-pinned, not double

The plan's D4 (decisions.md:319-322) enumerates two pin files. There are **three** exact-equality asserts (verified by architect grep on this tree):

```
tests/integration/test_maintenancer_spawn_resolves_tools.py:292
tests/unit/tools/test_attestation_registration.py:158
tests/unit/tools/test_upgrade_registration.py:105
```

Missing the integration pin is the one concrete way this PR ships red late. **The D4 same-PR pin list MUST be corrected from two files to three before implementation.** (A fourth family — `tests/unit/tools/test_privileged_category_system_log.py:301-310`, asserting `_baby_template` allow ∩ privileged = ∅ — stays green under A since v1 grants no agent `service`.)

### 1.4 Five-axis trade-off matrix

| Axis | A: frozenset add (+3 pins) | B1: `CategoryRegistration.default_open` dataclass | B2: parallel `DEFAULT_CLOSED_CATEGORIES` frozenset |
|---|---|---|---|
| **Complexity** | **Low** — 1-line add + 3 pin edits + comment rewrite; no new mechanism | Med-High — "~30 LOC" is a 3-4× undercount: restructures `CATEGORY_MODULES` (46 entries) + `registry.py:1015/1050-1052` + `help.py:210` + `loader.py:218` + `test_system_log_tools.py:154-160` + `KNOWN_TOOL_NAMES` regen | Low-Med — ~10 LOC + filter check, but a second set to curate |
| **Scalability** (future default-closed categories) | **High** — rides an existing, triple-pinned mechanism; pin net auto-extends to every new member | Med — per-category metadata is more expressive long-term, but at birth it is a single-consumer abstraction | Med — parallel sets scale poorly; every category must be classified twice |
| **Maintainability** | Med-High — one semantic concept, one frozenset; residual vocabulary drift ("privileged ⇒ daemon-internal") fully mitigated by comment rewrite | Med-Low at birth — new authorization surface with NO pins: a future `default_open=False` lands without tripping any exact-equality assert, silently narrowing the protection net; needs its own new pin family before it is safe | Low — two hand-maintained frozensets, disjointness invariant unpinned, no repo precedent; hollows "privileged" of behavioral meaning (both sets strip identically → the distinction becomes naming-only) |
| **Risk** (higher = safer) | **High** — fail-safe direction (default-closed); fully reversible (remove entry + pins); the only red path is NOT adding it (auto-leak to empty-allow agents) | Med-Low — couples a structural registry refactor to a security-critical category add: maximal review surface at the worst moment; unpinned at birth | Med-Low — drift hazard: a category in NEITHER set defaults open silently; invariant "privileged ∩ default_closed = ∅" has no enforcement |
| **Cost** | **Low** — no LOC beyond pins/comments; SC-6 test needed under every option | Med-High — refactor + new pin family + regen + review load | Low-Med — new set + new tests |
| **Recommendation** | ✅ **ADOPT for v1** | ❌ Reject for v1 — decoupled follow-up tidy PR on a green baseline, only if per-category metadata is ever independently justified, carrying its own pins | ❌ Reject — dead weight if the distinction never affects behavior; unpinned new surface if it does |

**Confidence: High.** The recommendation flips only if a consumer of `PRIVILEGED_TOOL_CATEGORIES` with real "daemon-internal" behavioral semantics is discovered outside the three verified sites — both councilors' independent repo-wide greps found none.

### 1.5 OQ#1 disposition (overrules the planner's lean)

Planner leaned "YES, ship `default_open` follow-up." **Overruled.** The `default_open` refactor is NOT pre-committed as a follow-up. It lands only if per-category metadata earns its keep independently (e.g., a second non-privileged default-closed category actually arrives), as its own tidy PR on a green baseline, carrying its own exact-equality pin family. Do not couple it to this feature.

---

## 2. Verification results (secondary items 1–5)

### Item 1 — Kill-site exemption (K1–K13 registry-scoped claim): **HOLDS-WITH-GAPS** ✅

- Worker's own independent sweep of ALL of `daemon/` (incl. the plan's unverified regions `daemon/clients/`, `daemon/sources/`, `daemon/repositories/`, `daemon/routers/`, `daemon/opencode/`, `daemon/jobs/`, `daemon/rag/`, root modules) for every kill primitive (`os.kill`, `os.killpg`, `getpgid`, `setsid`, `SIGHUP`, `waitpid`, `pkill`, `process_iter`, `/proc/\d+`, `.terminate()/.kill()/.send_signal()`, `subprocess.run(timeout=)`, `os.fork`): every signal hit is in `bash.py`, `proc_tools.py`, or `vscode_server_manager.py` — the three inventoried files. **The plan's UNVERIFIED-caveat on `daemon/clients/` + `daemon/sources/` is now CLOSED (confirmed empty of kill primitives).**
- `/proc/*` enumeration: zero matches across `daemon/` (only `/proc/<pid>/environ` reads in the registry-scoped, fail-closed PID-ownership check at `proc_tools.py:290`). Claimed-negative confirmed.
- Launcher/scripts adjacency: `scripts/stop-ensemble.sh` uses bare-PID `kill` with a ppid walk bounded to launcher descendants (`:280-307`); `launcher.sh:1056` forwards signals to its single tracked child only; `__main__.py:304-306` hard backstop SIGKILLs only the daemon PID. Setsid'd services reparent to launchd (PPID=1) → **unreachable by all of the above. SIGHUP-on-teardown is a non-issue (setsid child has no controlling TTY).**
- **The gap is temporal, not spatial:** the proof holds for today's code; nothing prevents a FUTURE kill site. The plan's fence is docs-only — **no `.github/CODEOWNERS` file exists in the repo today** (`find` returns nothing); the plan's CODEOWNERS mitigation is a TODO, not a fence, and the Phase-2 13-site matrix catches broadened *existing* sites but not *new* sites in unswept paths. → Amendment A7 (CI grep-gate).

### Item 2 — PID-reuse 3-layer defense: **SUFFICIENT-WITH-AMENDMENTS** ✅ (2 must-fix)

- Check-then-kill TOCTOU: acceptable — microsecond window, kernel-recorded `start_time` is a stronger signal than the user-space env-tag precedent (`proc_tools.py:228-242`, `:918-1035`), and the 90s sweep backstops any residue.
- 🔴 **Single-pid `os.kill` orphans children** (decisions.md D3 pseudocode `:275`, `:283`): real services (`npm run dev`, webpack) fork workers; SIGTERM to the leader leaves them alive. Precedent `proc_tools.py:1010` kills the whole group. Since a setsid'd service IS its own session+group leader (pgid == pid), **`os.killpg(row.pid, sig)` reaches the whole group with zero added reachability**. → Amendment A1.
- 🔴 **5s blocking busy-wait** (`time.sleep(0.1)×50` in a sync tool): either blocks the event loop or burns a worker thread per stop. Precedent `proc_tools.stop_process` (`:1264-1400`) is async `await asyncio.wait_for(...)`. → Amendment A2.
- 🟡 **Eternal `status='starting'` rows** (sweep skips `pid is None` forever, decisions.md `:518-520`): a crash between INSERT and Popen leaks a row that counts against the cap forever and is invisible to the operator. **Cross-confirmed independently by the data-flow worker.** → Amendment A3.
- 🟡 `exit_code` is never captured (no `wait()` handle after spawn) — `service_status` on a gracefully-exited service reports `exited` with `exit_code=None`. Acceptable if documented in `_full_doc_`. → Amendment A4.
- 🟢 macOS `ps -o lstart` parse is locale-sensitive — Phase-3 soak item. Grandchild-that-calls-setsid edge (grandchild escapes the service's group, `killpg` misses it) — documented limitation, not v1 scope.

### Item 3 — `ServiceReconciliationService` shape: **RIGHT-SHAPE-WITH-AMENDMENTS** ✅

- Clone-of-`EligiblePendingSweepService` is correct; three in-repo precedents confirmed (`eligible_pending_sweep.py`, `orphan_watcher_sweep.py`, and — missing from the plan's precedent set — **`JobLockSweepService`** at `api.py:735-780`, the newest write-capable variant from b5100215). Sweep-first-then-wait loop shape verified in template (`eligible_pending_sweep.py:372-374`) and preserved by the plan.
- The plan's **per-row error isolation is BETTER than the template** (template wraps the whole tick) — keep it.
- 🔴 **Phantom source of truth in the mount pseudocode** (decisions.md D6 `:436-456`): it reads `getattr(app.state, "_service_tool_manager", None)` — **`app.state` is never populated with manager attributes**; house style reads manager attrs off `manager` and stores only the *service* on `app.state` for shutdown (cf. `api.py:648-662` reads `manager._task_repo`/`_worker_pool`; `api.py:675` stores `app.state.eligible_pending_sweep`). **As written, reconciliation receives None and NEVER runs — the restart-survival headline feature is born dead while appearing wired.** → Amendment A5.
- 🟡 **Guaranteed boot pass:** both restart-critical precedents pair the periodic sweep with an awaited startup one-shot (`api.py:397` `recover_stale_job_locks()`; orphan watcher's explicit startup sweep, `orphan_watcher_sweep.py:92-94`). `asyncio.create_task` only schedules; the first tick races lifespan suspension. Add `await svc.sweep_once()` before `yield`. → Amendment A6.
- 🟡 Config naming: house keys are `{name}_interval_seconds` as real `ServicesConfig` `Field(ge=1)` fail-fast knobs (`eligible_pending_sweep_interval_seconds` `config.py:1402`, `orphan_watcher_sweep_interval_seconds` `:1433`, `job_lock_sweep_interval_seconds` `:1509`), read directly — the plan's `service_tool_reconcile_interval` + `getattr(..., 90)` bypasses the fail-fast contract. → Amendment A8.
- 🟡 `stop()` semantics: plan's `wait_for(shield(task))` + `cancel()`-without-re-await leaks an un-awaited cancelled task; use the template's `set()` → `cancel()` → `await` with CancelledError swallowed. Drop the dead `max_concurrent` param (the cap lives on ServiceManager). Add the None-manager guard (skip start + log DISABLED, template DEBUG no-op precedent `:308-320`). → Amendments A8/A9.
- Layering: house sweeps inject the narrowest collaborator (`task_repository`, `dependency_bus`, `job_lock_manager`) — inject `ServiceRepo` directly rather than reaching through `ServiceManager.repo` (either is defensible; direct is tighter).

### Item 4 — `service_tracking` schema + dual-dialect migration: **CONSISTENT-WITH-AMENDMENTS** ✅

- **BY-DESIGN (no fix needed):** `AUTOINCREMENT` + `DEFAULT (datetime('now'))` in the `.sql` (the PG runner is an intentional NO-OP — `runner.py:721`; PG DDL arrives via SQLModel create_all + `_ensure_postgres_columns`; `AUTOINCREMENT` precedent `20260412_000001_create_task_table.sql:9`); partial-index `sqlite_where`/`postgresql_where` dual-dialect in `__table_args__` (precedents `job_queue/models.py:300-306`, `report_injection/models.py:227-235`, mirrored in `manager.py:5117-5121`+).
- 🟡 **`name: Field(unique=True, index=True)` is a confirmed defect:** emits a FULL unique constraint that **kills D5's name-reuse-after-EXITED**, plus a redundant third index on `name`. Convention: declare ONLY the partial UNIQUE index — `Index("idx_service_tracking_name_active", "name", unique=True, sqlite_where=..., postgresql_where=...)` per `report_injection/models.py:227-235`. Also: the D2 `.sql`/models declare the partial index WITHOUT `unique=True` — as written, concurrent same-name `service_start` calls are NOT DB-guarded; the `unique=True` inside the partial Index is the actual D5 guard. → Amendment A11.
- 🟡 **Timestamps are a confirmed defect (3 sub-issues):** `datetime.utcnow` is deprecated with zero uses in `daemon/`; `sa_column_kwargs={"onupdate": text("now()")}` is non-portable (**SQLite has no `now()` — every UPDATE write path breaks**; convention is repo-side bumps per `infra/repository.py:539+` or an ORM `before_update` listener per `instance/models.py:172-175`); column TYPE should be `str` TEXT ISO-8601 via the `_now_iso()` factory pattern (30+ call-site convention: `skill/models.py:50-51`, `report_injection/models.py:304-306`, `job_queue/models.py:357`). NOTE: `daemon/services/timestamps.py` (`now_utc_naive`/`now_utc_iso` from merge f6ca8791) is **NOT visible in this worktree state** (confirmed by architect glob) — follow the established `_now_iso()` convention, and swap to the central helper if it exists at the implementation SHA. → Amendment A12.
- 🟢 `status: ServiceStatus` enum-typed field → convention is `status: str = Field(default=ServiceStatus.STARTING.value)` (precedent `report_injection/models.py:308`); storage-equivalent, convention drift only.
- 🟡 **Flow defects:** eternal `starting` rows (see A3); `mark_exited`/`service_stop` UPDATEs need the atomic guard `WHERE id=? AND status IN ('starting','running')` to make sweep↔stop races idempotent (guarded-claim precedent `report_injection/models.py`). → Amendment A13.

### Item 5 — `ServiceManager` as manager-held facade: **FITS-WITH-AMENDMENTS** ✅

- Manager-held is **correct** (precedents: `self._mcp_service = McpService(manager=self)` `manager.py:1257`, `self._task_repo` `:6367`; the module-singleton `proc_tools.py:1842-1852` is the OLDER pattern whose own comment documents test-monkeypatch friction). Caveat: the plan's stated rationale ("singleton would break pause-first-then-quiesce coordination") is **overstated** — the plan's own pause section says pause never sweeps services. The real arguments are testability, lifespan getattr wiring, and one cap counter; the conclusion stands.
- **No facade-forwarding methods are needed:** consumers are (1) tools via factory closure dereferencing `manager._service_tool_manager` at CALL time (matches `create_system_log_tools` `instance.py:4659` + loader `None`-manager warm stubs `loader.py:66-90`), (2) lifespan via `getattr(manager, ...)`. Nothing routes through `InstanceManager`'s public API — the facade-forwarding discipline (kwarg grep + real-dispatch test) binds only if a forwarding method is added later. `instance_lifecycle.py` needs NO hook (services survive termination BY DESIGN — contrast the bash/proc cleanup at `:2328-2333`, which exists only because those procs ARE instance-owned).
- Private-underscore getattr is consistent with house style (`api.py:648-662`, `instance_lifecycle.py:2323-2326`) — but see the 🔴 `app.state` variant bug (A5).
- **Wiring anchor correction:** `manager.py:690-693` is NOT the service-wiring site in this tree (it is a local probe TaskRepository in restart-wipe logic); the real pattern sites are `:1257`/`:6367`.

---

## 3. Plan amendments (fold in before Phase 1 dispatch)

| # | Severity | Amendment | Plan touchpoint |
|---|---|---|---|
| A1 | 🔴 | `service_stop` signals the process GROUP: `os.killpg(row.pid, sig)` (pgid == pid for setsid leaders; no added reachability); fixes fork-children orphan hazard | decisions.md D3 pseudocode `:275`, `:283` |
| A2 | 🔴 | Make `service_stop` async; replace `time.sleep(0.1)×50` with `await asyncio.wait_for` polling `get_process_start_time` via `asyncio.to_thread` (proc_tools `:1264-1400` pattern); SIGKILL escalation → `os.killpg` | decisions.md D3 `:278-283` |
| A3 | 🟡 | Sweep reaper branch: reap `status='starting' AND (pid IS NULL) AND created_at < now()-grace` (grace ~30s) → mark EXITED, `reason=spawn_failed_or_interrupted`, log INFO/WARNING | decisions.md D6 `:518-520`; phase2-plan sweep task |
| A4 | 🟡 | Document in `service_status`/`service_stop` `_full_doc_`: exit_code is `None` for deaths not observed via `service_stop` (no wait handle is retained) | phase1/3 `_full_doc_` tasks |
| A5 | 🔴 | Fix mount pseudocode: `getattr(manager, "_service_tool_manager", None)` (NOT `app.state`); store the sweep service itself as `app.state.service_reconciliation_service` for shutdown | decisions.md D6 `:436-456` |
| A6 | 🟡 | Add guaranteed boot pass: `await svc.sweep_once()` in lifespan BEFORE `yield` (precedents `api.py:397`, `orphan_watcher_sweep.py:92-94`) — restart-survival must not race the first tick | decisions.md D6 mount; phase1.C.14 |
| A7 | 🟡 | Add CI grep-gate as Phase-3 task 3.B.4: fail PRs introducing new `os.kill(`/`os.killpg(`/`killpg(0)`/`/proc/[0-9]` sites in `daemon/` outside the allowlisted trio (bash.py, proc_tools.py, vscode_server_manager.py); the CODEOWNERS fence is a TODO today (no file exists) — the gate is the enforceable fence | phase3-plan; technical-analysis `:371` |
| A8 | 🟡 | Config: rename to `service_tool_reconcile_interval_seconds` as a real `ServicesConfig` `Field(ge=1)` read directly (house `{name}_interval_seconds` convention); `stop()` → template `set()`→`cancel()`→`await` semantics; drop dead `max_concurrent` param from the reconciliation service; None-manager guard → skip start + log DISABLED | decisions.md D6/D7; phase1.C |
| A9 | 🟢 | Sweep collaborator: inject `ServiceRepo` directly (narrowest-collaborator house pattern) instead of reaching through `ServiceManager.repo` | decisions.md D6 sweep pseudocode |
| A10 | 🟡 | Anchor refresh pass: plan's `api.py` lifespan anchors have drifted (eligible `:636-682`, orphan `:698-733`, JobLockSweep `:735-780` MISSING from precedent set, shutdown `:1512-1556`, vscode boot `:1167-1230` — plan's `:1400-1406` is LiveEventHub shutdown); strip helper is `~:4743-4760` not `:4667-4684`; manager wiring sites `:1257`/`:6367` not `:690-693`. All implementers re-locate by symbol | plan-overview Scope; all phase plans |
| A11 | 🟡 | Schema: drop `unique=True, index=True` from `name`; declare ONLY the partial UNIQUE index (`Index(..., unique=True, sqlite_where=..., postgresql_where=...)`); mirror `unique=True` in the `.sql` partial index and `_ensure_postgres_columns` — this is the actual D5 same-name guard | decisions.md D2 `:89-98`, `:145-150` |
| A12 | 🟡 | Schema: `created_at`/`updated_at` as `str` TEXT ISO-8601 with `_now_iso()` default factory; repo-side `updated_at` bumps; delete `onupdate=text("now()")`; `status: str = Field(default=ServiceStatus.STARTING.value)`; swap to `daemon/services/timestamps.py` helpers IF present at implementation SHA | decisions.md D2 `:108-109`, `:132-138` |
| A13 | 🟡 | All status-mutating UPDATEs get the atomic guard `WHERE id=? AND status IN ('starting','running')` (idempotent, race-free sweep↔stop) | decisions.md D3/D6; repository methods |
| A14 | 🟢 | D4 pin list corrected 2→3 files (§1.3); comment rewrite + innate-skill negative pin + SC-6 behavioral test (§1.2) | decisions.md D4 `:319-322`; phase1.C |

## 4. OQ dispositions (architect, for the decisions.md close-out)

| OQ | Disposition |
|---|---|
| OQ#1 | **Resolved: Option A** (§1). `default_open` refactor NOT pre-committed (§1.5). |
| OQ#2 | Document single-daemon-only in v1 (agree with plan lean). The `service_tracking` audit columns (`started_by_instance_id`, `started_by_agent_id`) suffice; add `daemon_instance_id` only if multi-daemon becomes real. |
| OQ#3 | Operator-side log rotation (agree). Cap `MAX_LOG_TAIL_BYTES=10MB` in `service_logs`; document disk-fill risk in `_full_doc_` + ops note. |
| OQ#4 | No command allowlist in v1 (agree — security theater while `bash`/`proc_run` are unrestricted; risk bounded by cap=10 + default-closed category). Revisit only alongside a broader sandbox story. |
| OQ#5 | `ps`-based macOS start-time parse acceptable (precedent `_verify_pid_ownership` uses `ps eww`); document locale-format risk in a comment; `NotImplementedError` on Windows. |
| OQ#6 | Keep the 5s wait after SIGKILL even on `force=True` (consistency with `proc_stop`); `force` only skips the SIGTERM phase. (Implemented async per A2 — the wait is no longer a busy-wait.) |
| OQ#7 | Plain text tail (agree); no format-aware parsing in v1. |

## 5. Risks (post-gate)

- 🔴 None remaining in the *architecture*, provided A1/A2/A5 land — each was a silent-failure or orphan-process defect in pseudocode that Phase-1 implementers would otherwise faithfully transcribe.
- 🟡 Kill-site inventory staleness (mitigated by A7 gate + Phase-2 matrix); schema field defects (A11/A12/A13 — would have shipped as written); anchor drift (A10) sending implementers to wrong neighborhoods.
- 🟢 Vocabulary drift on "privileged" (mitigated by comment rewrite + pin docstrings); locale-sensitive `ps` parse (soak item); grandchild-setsid escape (documented limitation).

## 6. Decisions pending (leader)

1. **Ratify D4 = Option A** (architect verdict delivered; the gate is non-negotiable per plan Risk #1 — no Phase 1 code before this is recorded in decisions.md).
2. Accept the 14 amendments (or reject individual ones with rationale) — A1/A2/A5 are blocking; the rest are should-fix by phase.

## 7. Open questions

- `daemon/services/timestamps.py` visibility: absent from this worktree state despite the f6ca8791 merge note — implementer resolves at the implementation SHA (A12 handles both branches).
- CI grep-gate allowlist mechanics (exact regex + allowlisted files) — owned by Phase-3 task author.
- Whether the innate-skill bypass seam (`INNATE_SKILL_TOOL_CATEGORIES` appending to `allow` regardless of frozenset membership) deserves a standalone hardening PR beyond the negative pin — flagged to leader; out of scope here.
