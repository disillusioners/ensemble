# Decisions — soak-gate-mcp-warmup

## How to read this file

This is the **decisions** artifact for the `soak-gate-mcp-warmup` commission: the 2026-10-26
v0.27.8 promote soak failure and the 2026-10-26 22:10–22:50Z DB-pool incident. It records
**what we decided and why**, not how to implement it — the implementation plan is a separate
artifact. Every `file:line` anchor in this document was **personally opened and read** in the
worktree `/home/nea/ensemble-src-wt-soak-gate` (branch `feature/soak-gate-mcp-warmup`, base
`d5a4e4ad92d19f3265b97bc29c5aa3943f026ed2`). Where the commission brief's line numbers did not
match what is on disk, the drift is called out explicitly in §3 (Premise corrections) rather
than silently absorbed. Where a later authoritative read falsified an earlier premise, **both
the original reasoning and the correction are kept visible** in the relevant alternatives table
— the audit trail is the point.

Two authoritative documents are **main-checkout-only and UNTRACKED**; they are referenced by
path and are *not* present in this worktree:

- `/home/nea/ensemble-src/.agents/shared/analysis-20261026-v0278-soak-failure.md` (the soak RCA)
- `/home/nea/ensemble-src/.agents/shared/incident-20261026-db-pool-idle-in-transaction.md` (the pool-leak RCA)

**Anchor verification.** Every anchor below was verified twice: by opening the cited block in
the worktree, and by a **mechanical batch check** (`sed -n '<line>p' <file>` compared against
the cited construct) over the 113 load-bearing anchors. A **second pass** re-verified every
anchor added by the reviewer council's fold round (C-1, W-1..W-5, R-2, S5, S7..S11, V3/V4) --
including `daemon/api.py:2703` / `:2708`, `daemon/services/auto_continue_boot_pass.py:726-729`,
`daemon/manager.py:13671`, `daemon/mcp/stdio_wrapper.py:401-409`,
`tests/test_resource_pid_registry.py:505-510`, `daemon/mcp/connection_manager.py:229-237`,
`daemon/mcp/builtin_servers/plane.py:262-289`, `daemon/config.py:1821-1823` and
`daemon/repositories/task/repository.py:5591`. The batch check also surfaced the **semantic** correctionsThe batch check also surfaced the **semantic** corrections in §3 (notably premises 8, 9, 10
and 19) — a line can contain the cited token while the surrounding prose is wrong.

Nothing in this document was edited in, or derived from, the live daemon, the live
`/home/nea/agents-ensemble/.env`, `state.json`, the `releases/` directory, the `current`
symlink, or the main checkout `/home/nea/ensemble-src`.

## Table of contents

1. [Context recap](#1-context-recap)
2. [Premise corrections](#2-premise-corrections)
3. [Decision records (a)–(h)](#3-decision-records)
   - [(a) Grace-window design](#3a-a-grace-window-design)
   - [(b) Soak semantics](#3b-b-soak-semantics)
   - [(c) Auto-continue hold](#3c-c-auto-continue-hold)
   - [(d) Checkpoint pool sizing formula](#3d-d-checkpoint-pool-sizing-formula)
   - [(e) MCP warmup off the event loop](#3e-e-mcp-warmup-off-the-event-loop)
   - [(f) DNS pre-check design](#3f-f-dns-pre-check-design-c2)
   - [(g) Giving context7 a settable base URL](#3g-g-giving-context7-a-settable-base-url-without-breaking-protected-built-in)
   - [(h) Observability surface for degraded MCP servers](#3h-h-observability-surface-for-degraded-mcp-servers)
4. [The two commissioning constraints, pinned](#4-the-two-commissioning-constraints-pinned)
5. [Narrowed requirements](#5-narrowed-requirements)
6. [REUSE vs NEW ledger](#6-reuse-vs-new-ledger)
7. [Pool topology — do not conflate](#7-pool-topology--do-not-conflate)
8. [Explicit non-goals](#8-explicit-non-goals)
9. [Open questions / deferred decisions](#9-open-questions--deferred-decisions)

---

## 1. Context recap

On **2026-10-26** the promote to **v0.27.8** passed every gate — integrity (CURRENT drift-check
+ TARGET manifest), `livez`, `readyz` (all four components including `checkpoint_saver`), and
version verification — and then the **adaptive soak sampled zero green probes**. The
`cap-reach with consecutive_green=0` branch (`scripts/upgrade/lib.sh:5237-5244`) returned 1,
which routes to ADR-005 auto-rollback **and quarantine** of a healthy release. The same binary,
installed manually (launcher swap + symlink flip, no gate), served green readyz roughly 90
seconds after boot. The RCA's own proof of "the release was fine" is that the condition tracked
**machine load**, not release identity: the post-rollback v0.27.4 daemon showed the identical
degradation signature.

**Prior art: this is the SECOND healthy-release rollback for an environmental cause.** On
**2026-10-09** a DB outage window tripped the **v0.18.5** promote's readyz gate ⇒ ADR-005
auto-rollback of a healthy release. The 2026-10-26 soak failure is the second such case. The
two differ in mechanism (environmental DB churn vs. a load/MCP storm) but share a shape:
**the release was healthy and the gate could not tell the difference.** The design therefore
cannot be a "post-boot burst" special case → the window semantics must be
**class-driven**, so that the 10-09 class and the 10-26 class are covered by the *same* rule
(§3a A1.2).

**Two co-causes, neither dominant.** The RCA is internally inconsistent about weighting: the
main analysis calls auto-continue's resume burst "the largest single contributor, but not the
only trigger", while the 21:45Z addendum calls the context7 MCP init storm "the second,
**dominant** co-cause". **We therefore design for both.** Concretely:

- **(1) Post-boot saturation.** The checkpoint `AsyncConnectionPool` (max_size=5) and the SQL
  probes that share the pool saturate under a boot burst — MCP schema lazy-load across 4
  instances, a plane MCP retry storm, job-recovery reconcile, and an auto-continue resume of 4
  instances. The readiness detector's budgets are **steady-state** budgets (0.5s / 1.0s / 2.0s,
  `daemon/services/readiness.py:45`, `:54`, `:59`), so "busy but healthy" reads identically to
  "broken". A second, deeper mechanism was confirmed by the later code read and is decisive for
  decision (d): the saver serialises **all** cursor operations through **one** `asyncio.Lock`
  (`langgraph/checkpoint/postgres/aio.py:59`, taken at `:374`), and the readiness sentinel read
  takes that same lock. **Enlarging the pool does not shorten the lock queue.**
- **(2) Context7 MCP init storm.** `api.context7.com` went NXDOMAIN, so every context7 init
  hung to a 30s timeout, retried 3×, and repeated every ~2–4 minutes forever — **nothing
  circuit-breaks init** (the `ResilienceManager` breaker guards *tool calls*, and context7
  registers no `ResilienceConfig` at all: `daemon/services/mcp_service.py:852-854`). Each
  attempt spawned `npm exec @upstash/context7-mcp` plus a node server at 88–156% CPU, leaking
  an unclosed aiohttp session and orphaning children. The daemon main process pegged at
  105–114% CPU with zero agent work.

**A third, later-confirmed co-cause (the 22:10–22:50Z incident).** The pool-leak RCA shows the
same symptom pair (readiness red + `pending-count` 503) with a *different* mechanism: leaked
`idle in transaction` backends poison the SQLAlchemy pool, the daemon's own probes starve, and
the event loop burns CPU retrying around a starved pool. The failure is **self-sustaining**,
and it re-forms in ~3 minutes under load after every restart. It is in scope for this
commission **only** as a constraint on (a) and (d) — the leak sites themselves are explicit
non-goals (§8).

**The phantom file.** `.agents/shared/protection-plan-install-dir-20261012.md` **does not
exist** (verified against disk, git refs, history, fsck). Both the soak RCA and
`scripts/protection/README.md:11` cite it as if it does. The real content is
`scripts/protection/README.md` (install-dir host armour Layers 1/3/4). We do not cite the
phantom and we do not treat it as a missing input.

**Live-release note (stale brief value corrected).** The live release is **v0.27.8**
(`/home/nea/agents-ensemble/current -> releases/v0.27.8`, observed read-only), **not** v0.27.4
as the commission brief stated. We touch neither; the value only matters so that no plan step
is written against the stale number.

---

## 2. Premise corrections

Each row states the claim as received, what the code actually does (with an anchor I opened),
and the consequence for this plan.

| # | Claim as received | What the code actually does | Consequence for the plan |
|---|---|---|---|
| 1 | "The readiness detector has **no boot-window grace**." | True **of the daemon layer**. The **gate** already has one: `scripts/upgrade/lib.sh:157-158` (`BOOT_GRACE_S=60`, `PROMOTE_BOOT_GRACE_S`), armed by `_boot_grace_start` (`:3186-3189`) and evaluated by `_boot_grace_active` (`:3196-3211`); it suppresses transients via `_probe_once_classified` (`:3023-3028`, returns code `3`). | The RCA's claim is scoped to the daemon. We must **extend the existing gate grace**, not invent a parallel mechanism — and separately give the *daemon* a bounded, class-gated grace signal. |
| 2 | "The soak's grace/amnesty machinery is the same as the gate's." | The **soak** uses `_probe_once_classified` **directly** (`lib.sh:5161`, `:5181`) — **no retry** — while `gate_livez`/`gate_readyz` use `_probe_with_retry` (`:3069-3125`, 3 retries, 1s/2s/4s backoff, transient-exhausted ⇒ rc 2 = "do not fail the phase"). | The soak is strictly harsher than the gates. The soak needs its own treatment (§3b), and the asymmetry must be recorded. |
| 3 | "The gate can classify the 503 failure class." | It **cannot today**: `_probe_once_classified` reads the body **only on green** (`lib.sh:3005` — `[ -s "$tmp_body" ] && [ "$_PROBE_STATUS" = "green" ]`). On a 503 the reason body is discarded, so the gate is blind to *why* it was transient. | Failure-class gating (CONSTRAINT 1) **requires a structured signal**. Decision: the signal is computed **daemon-side** and the gate must be changed to **read the body on the transient path** (one-line change at `:3005`, plus reuse of the existing `_json_sub`/`_json_field` helpers at `:3148-3150`). |
| 4 | "The 3-consecutive-green counter requires a hard green on both probes." (the counter's normative shape is `requirements.md` §4.2's, P2 — **normative**; the defect below is the *mechanism* defect) | True, but the implementation **aliases a global**: `lib.sh:5216-5217` reads `$_PROBE_STATUS` twice, and `_PROBE_STATUS` is overwritten by the **second** (readyz) probe. The `livez_rc`/`readyz_rc` checks are *not* aliased; only the two `_PROBE_STATUS` sub-checks are. **Direction:** a livez **busy-amnesty soft-green** combined with a readyz **hard green** *increments* the counter — contradicting the documented invariant at `:5210-5215` ("soft-greens do NOT count"). The reverse case (livez hard green + readyz soft-green) resets, which matches intent. | This makes the gate **more permissive** than documented, i.e. it **weakens** the C-1 never-green invariant. It is a real defect and must be fixed in the same change that rewrites the counter semantics (§3b), otherwise the new grace logic is evaluated against a counter that does not mean what the comment says. |
| 5 | "The checkpoint pool size needs a knob; `min_size/max_size` is hard-coded." | Confirmed. `daemon/persistence.py:273-294` builds the psycopg `AsyncConnectionPool` with `min_size=1` (`:283`), `max_size=5` (`:284`), and the comment at `:276-282` says verbatim *"pool sizing (min_size/max_size) is hard-coded, not config-tunable"*. A **second** pool (asyncpg adapter) is at `:313-314` with the same 1/5. | A4 **must create** the config knob, following the house pattern: `daemon/config.py` field + env var + `config.yaml` interpolation. `llm_concurrency` is the model (`daemon/config.py:595`, `config.yaml:223`, enforced at `daemon/manager.py:707`). |
| 6 | "Context7 has no ResilienceConfig and nothing wraps init." | **Confirmed.** `daemon/mcp/builtin_servers/context7.py:41-46` returns an empty `get_config_schema()`; `daemon/mcp/builtin_servers/base.py:104-123` returns `resilience_config = None` by default; `daemon/services/mcp_service.py:852-854` carries the comment *"context7 / webfetch: `None`"*; and `daemon/mcp/resilience.py:15-18` states the wiring is *only* into `tool_adapter._lazy_coroutine`. `daemon/mcp/warmup_pool.py` contains **zero** resilience or circuit references — the init path is entirely outside the machinery. | C1 must add an **init-path** guard. It must not be confused with the tool-call resilience layer (§3e). |
| 7 | "There are two circuit-breaker implementations to choose between." | **Falsified — there is exactly one.** `daemon/sources/circuit_breaker.py:20` defines `CircuitBreaker`; `daemon/mcp/resilience.py:45` **imports** it (`from daemon.sources.circuit_breaker import CircuitBreaker`). `daemon/mcp/resilience.py` provides only the *generic primitives* (`RetryPolicy`, `ResultCache`, `AuthFailureClassifier`, `ResilienceConfig`, `ResilienceManager`). | The REUSE ledger must name the **primitive** as the reuse target and explain why the `ResilienceManager` **wrapper** is the wrong home for an init-path breaker (§6). |
| 8 | "The RCA's remediation was `CONTEXT7_API_URL` / `CONTEXT7_BASE_URL` in the daemon `.env`; MCP children inherit the daemon's environment." | **Falsified.** `daemon/mcp/builtin_servers/context7.py:33-39` returns a base config with **no `env` key**; `daemon/mcp/builtin_servers/base.py:241-242` only emits `"env"` when it is non-empty; `daemon/mcp/connection_manager.py:229-242` resolves that to `None` and passes `env=None` into `StdioServerParameters`; the installed MCP SDK (`mcp/client/stdio/__init__.py:44`, `:127`) then uses `get_default_environment()` = **only HOME, LOGNAME, PATH, SHELL, TERM, USER** (`:28-45`). | **The `.env` cannot reach the child.** C3 must go through the *schema → row → child-env* path, and C2's DNS pre-check must run against the *resolved* base the child would actually use. The operator's 21:40 remediation reached the child by some other route (most plausibly the unguarded `mcp_set_env` tool, `daemon/tools/infra.py:1527`) — we treat "the .env route works" as a false premise. |
| 9 | "The grace can be a time window." | Not merely a design preference — a **hard constraint**. The pool-leak RCA proves the failure class is **self-sustaining** (probe leak → pool pressure → probe timeout → retry → more leaks), so a time-only grace would mask a genuinely broken daemon indefinitely. | CONSTRAINT 1 (§4): the grace is gated on **failure class**, and a **pool-leak signature is grace-INELIGIBLE**. The grace must also be **observable** — it may never render "healthy". |
| 10 | "The readiness `queue_freshness` probe leaks a transaction per tick (`SELECT count(*) FROM job_queue_items ...`)." | **Not supported by the code as written.** The probe runs `_QUEUE_MAX_AGE_SQL_POSTGRES` (`daemon/services/readiness.py:133-137`: `SELECT EXTRACT(EPOCH FROM (now() - MAX(CASE ...))) FROM task WHERE status = :status_running`) and `_INFLIGHT_COUNT_SQL_POSTGRES` (`:160-164`: `SELECT COUNT(CASE ...) FROM task WHERE status = :status_running`) — **both `FROM task`, not `FROM job_queue_items`**. The probe opens `with engine.connect() as conn:` (`:799`), whose `__exit__` closes the connection; SQLAlchemy 2.0.48's pool `reset_on_return` defaults to `'rollback'` and `create_postgres_engine` does not override it (`daemon/repositories/factory.py:325-330` sets only `pool_size=5, max_overflow=10, pool_pre_ping=True`). **What *is* real:** a probe whose `asyncio.wait_for` budget expires leaves its worker thread running and its connection checked out — the module's own docstring says so (`readiness.py:40-45`, *"A timed-out to_thread leaves the underlying thread running… bounded leakage, acceptable at the 10s refresh cadence"*). | The leak-detection predicate **must not** be built on the unverified "probe leaks every tick" claim. It must be built on a **measured** observable (§3a, §4). The RCA's *other* three leak sites (below) are code-confirmed and are in scope as non-goals. **RECONCILED (iteration 003, N-B1):** the **CONFIRMED** mechanism is (i) the four code-confirmed leak SITES (the `ens_db_tools` explicit `BEGIN` outside the `try`, the `/api/instances` ORM read path, the `mission_records` mint path, and the RCA's `job_queue_items` count path — which is a **different code path** from the readiness probe), plus (ii) the readiness probe's **timeout-path connection retention** (the worker thread keeps the connection checked out until the driver's own timeout releases it, `readiness.py:40-45`). The readiness probe's own *per-tick* leak is **not** confirmed, and the RCA's `pg_stat_activity` fingerprint (last query `SELECT count(*) FROM job_queue_items ...`) therefore does **not** belong to the readiness probe. The RCA's *observation* (leaked idle-in-transaction backends, self-sustaining pressure) stands; its *attribution* to the readiness probe does not. |
| 11 | "The readiness probe leak and the `mission_records` UniqueViolation are the same class." | Partly. `daemon/tools/ens_db_tools.py:1479` and `:1491` execute `session.execute(text("BEGIN"))` **outside** the `try` (the `try:` is at `:1476`, the BEGIN at `:1479`, with an *inner* `try:` at `:1480`) — confirmed, a failure at the BEGIN skips the `rollback()`. `mission_record_service.mint_boot_epoch_if_none` (`:679-691`, fail-open wrapper `:1480-1484`) raises `IntegrityError` on `ux_mission_records_anchor_open` 4×/boot per the RCA. The wedge-capture table-name bug is confirmed: `daemon/services/wedge_capture/db_capture.py:308` (`FROM tasks t`) and `:387` (`FROM tasks`) — the table is `task`. | All four are **explicit non-goals** (§8). A1 must be designed so it **cannot mask** them; A4 must not pretend to cure them. |
| 12 | Brief line numbers for the `lib.sh` grace/amnesty block. | Actual: `SOAK_S_DEFAULT=300` at `:131`; `ADAPTIVE_SOAK_FLOOR_S`/`CAP_S`/`CONSECUTIVE`/`CADENCE_S` at `:146-149`; `BOOT_GRACE_S`/`PROMOTE_BOOT_GRACE_S` at `:157-158`; `BUSY_INFLIGHT_BOUND=5` at `:168`. The brief cited `:133-149`, `:152-160`, `:161-169`. | Cosmetic drift only; the constants and their values are as described. Cited above at the observed lines. |
| 13 | Brief: `daemon/services/upgrade_journal.py` holds the journal readers. | The module is `daemon/tools/upgrade_journal.py` (`journal_owner_heartbeat_fresh` at `:281-319`, `OWNER_HEARTBEAT_FRESH_S = 60` at `:156`); `daemon/services/` holds `upgrade_journal_sweep.py` (the consumer at `:2061`). | Cited at the observed paths. |
| 14 | Brief: `daemon/manager.py:2746-2750 / :2767 / :2997` are the `is_available()` / disable sites (brief's numbers). | Actual (verified on disk): `is_available()` is consulted at `daemon/manager.py:2813` (bootstrap) and `:3057` (warmup-pool registration — the consult IS at `:3057`; `:3058` is the following `logger.debug`), plus `daemon/services/mcp_service.py:579` and `:1130`. The env-var disable path is `daemon/manager.py:2798-2800` → `_bootstrap_builtin_servers` (`:2764`), which **deactivates the DB row** — i.e. `MCP_DISABLE_BUILT_IN_*` is boot-scoped in *intent* but **persistent** in effect. | §3g / §6 rely on the verified anchors. The persistence of the disable is a material fact for the quarantine design. |
| 15 | Brief: `daemon/config.py:2854` is the MCP default pool size. | Actual: `daemon/config.py:2894` `default_pool_size: int = Field(default=1, ge=1, ...)` inside `McpPoolConfig` (`:2888`, `env_prefix="MCP_POOL_"`). | Cosmetic drift; the value (1) is as described. |
| 16 | Brief: `daemon/api.py:2619` is where the daemon reads the journal at boot. | `daemon/api.py:2619` is `_boot_uj.reconcile_pending_op(upgrade_install_dir)`. The non-mutating reader `_journal_read` lives at `daemon/tools/upgrade_tools.py:693-723` and is called from the **tool** paths (`:2036`, `:2340`, `:2653`, `:3157`). | §3c cites the correct anchors. |
| 17 | Brief: "the soak's grace window is anchored to RESTART (60s from `restart_via_launcher`)". | Confirmed: `_boot_grace_start` (`:3186-3189`) is invoked at `lib.sh:4266` (inside the restart path) and `_boot_grace_active` (`:3196-3211`) expires on **either** 60s elapsed **or** 3 consecutive non-transient greens. In the incident the daemon was ~75s old at the first green readyz, so the grace had already expired before the burst peaked. | The grace must be **re-anchored to the load episode**, not to the restart epoch — and, per CONSTRAINT 1, **armed on a failure class**, not on elapsed time alone (§3a). |
| 18 | Brief: the auto-continue kill-switch is read once at boot and cannot gate a mid-soak resume. | Confirmed. `daemon/services/auto_continue_boot_pass.py:149` `AUTO_CONTINUE_KILL_SWITCH_ENV = "ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART"`; `:162-175` `_auto_continue_enabled()`; the two call sites are `:366` (the boot pass) and `:760` (`sweep_orphaned_live_turns`). Neither is a **mid-soak** gate on the *runtime* resume dispatch. | A3 needs a **live** hold at the dispatch seam (§3c), anchored to the promote transaction. |
| 19 | Brief: `inflight_turns` is the busy-amnesty signal and can be extended to cover the burst. | The `inflight_turns` advisory is a count of RUNNING tasks with **fresh heartbeats** (`readiness.py:160-164`), and the **auto-continue resume path writes no heartbeats** (`daemon/services/auto_continue_boot_pass.py:68-71`: *"The resume path writes no heartbeats (architect-verified, R20)"*). | **Busy-amnesty is structurally blind to exactly the resume-burst case.** We must not design A1/A2 around extending it. (§3a, §3b.) |
| 20 | Brief: the daemon may observe an open promote transaction by reading the journal. | Confirmed, and there is a **read-only, purpose-built accessor**: `daemon/tools/upgrade_journal.py:281-319` `journal_owner_heartbeat_fresh(install_dir)` (60s freshness window, `:156`), consumed by `daemon/services/upgrade_journal_sweep.py:2061`. The daemon never writes `in_flight` with `kind="promote"` — it only writes `kind="restart"` (`daemon/tools/upgrade_tools.py:2891-2902`); the promote txn is shell-owned (`scripts/upgrade/promote.sh:327` open → `:412` flipped → `scripts/upgrade/lib.sh:1116-1118` close). | A3 reuses this accessor and the **existing "pipeline-busy" vocabulary** (`daemon/tools/upgrade_tools.py:2678`, `:2687`, `:2695`) rather than inventing a parallel journal concept. |
| 21 | Brief: `lib.sh:776` points at `ENSEMBLE_UPGRADE_SCRIPTS_DIR`. | `lib.sh:776` is `journal_path()`. The `ENSEMBLE_UPGRADE_SCRIPTS_DIR` resolution lives in the **daemon**: `daemon/tools/upgrade_tools.py:1919-1942` `_resolve_scripts_dir` — order: explicit env → the repo checkout this module lives in (source-mode daemons) → `<install_dir>/scripts/upgrade`. | The **delivery-mechanics** conclusion stands and is important: the shell half (A1/A2/A3) is consumed from the working tree, so it can land without a release bump — but it must still be committed and shipped in the repo. The daemon half (C1–C4, and the daemon-side half of A1) needs a release. |
| 22 | Brief: `daemon/services/readiness.py` ~:580 probe; livez/readyz at `daemon/api.py:4793-4807` / `:4809`. | Confirmed: `make_checkpoint_saver_probe` at `daemon/services/readiness.py:580`; livez at `daemon/api.py:4793-4807`; readyz at `daemon/api.py:4809-4879` (O(1) cached read, `:4820-4821`). The refresher loop is `daemon/api.py:3720` and the probe construction site is `daemon/api.py:3777-3801`. | The daemon-side grace + pool-state sampling belongs at `daemon/api.py:3777-3801` (per-tick) and in the composite payload built at `daemon/services/readiness.py:239-274`. |
| 23 | Brief: the readiness reason string names the `check()` probe. | The 503 reason string is **stale**: `daemon/services/readiness.py:336-338` still emits *"checkpoint_saver: AsyncConnectionPool.check() failed or timed out"* although the probe was replaced by the sentinel `aget_tuple` (`:584-594` explains the replacement). | Any failure-class parser must key off a **stable, new** signal, not on parsing the legacy free-text reason. This is a direct input to the CONSTRAINT 1 design. |
| 24 | Brief: probe budgets are steady-state budgets. | Confirmed and **not env-tunable**: `DB_PROBE_TIMEOUT_S = 0.5` (`readiness.py:45`), `CHECKPOINT_SAVER_PROBE_TIMEOUT_S = 1.0` (`:54`), `QUEUE_PROBE_TIMEOUT_S = 2.0` (`:59`) — module constants. | The budget cannot be relaxed per-deployment; the grace must be a **gate-side** decision informed by a daemon-side class signal, not a budget change. |
| 25 | Brief: `ReadinessComposite` has no streak / first-degraded tracking. | Confirmed: `daemon/services/readiness.py:172-226` — the dataclass carries `database`, `queue_freshness`, `services`, `checkpoint_saver`, `reasons`, `queue_max_age_seconds`, `checked_at`, `forced_degraded`, `inflight_turns`, `auth_dependency_status`. **No** `first_seen_degraded`, **no** consecutive-failure counter, **no** pool state. | A1 needs new state on the composite: grace state, pool state, leak indicator, failure streak. Additive fields only (the OAuth2 field precedent at `:223-226` shows the house pattern for additive detail keys). |
| 26 | Brief: readiness refresh cadence. | Confirmed **10s**: `daemon/config.py:2657-2658` `readiness_refresh_interval_seconds: int = Field(default=10, ...)`; the refresher task is created at `daemon/api.py:2471-2486`; the loop is `daemon/api.py:3720` and its **first tick fires at t=0** (`:3747-3748`, *"Unlike the drift reconciler, the first tick fires immediately (t=0)"*). The auto-continue pass runs later in the same lifespan (`daemon/api.py:2703`). | The first tick lands **inside** the boot-recovery burst — that is the precise arming surface for A1. |

---

## 3. Decision records

### 3a. (a) Grace-window design

**Context.** Two grace mechanisms are in play and they must not be confused:

- the **gate-side** boot grace, already shipped (`lib.sh:157-158`, `:3172-3211`, `:3023-3028`),
  armed on restart, expiring on 60s elapsed **or** 3 consecutive non-transient greens, and
  **suppressing transients only**;
- the **daemon-side** readiness verdict, which has **no** grace at all: a probe timeout
  degrades the component immediately (`readiness.py:485-521`) and the composite reports
  degraded → `/readyz` 503 (`daemon/api.py:4873-4879`).

In the incident, the daemon was ~75s old at the first green readyz, so the gate grace had
already expired; the burst peaked later; the soak then took 8 un-retried samples in 120s, every
transient resetting the counter to 0, and the cap hit with `consecutive_green=0`.

**Decision.**

**A1.1 — The daemon never lies; the gate decides to wait.** The grace is implemented as a
**daemon-side classification**, surfaced in the `/readyz` payload, and consumed by a
**gate-side policy**. The daemon's own verdict is unchanged: a probe failure still degrades the
component, the composite still reports `status: "degraded"`, `/readyz` still answers **503**.
**Grace never renders "healthy".** This is the single most important property of the design and
it is what makes CONSTRAINT 1 enforceable: a masked failure stays *visible*; only the gate's
*reaction* changes.

**A1.2 — The grace is armed on a failure CLASS, never on elapsed time alone.** The daemon
classifies each degraded tick into exactly one class and publishes it. The **class list and
each class's eligibility are §4.2's** → this record does not restate them.

**The design position (reasoning, not a rule):** eligibility is **class-driven**. Boot age
and burst state are **published observable CONTEXT** in `detail.grace`; they are **NOT
arming preconditions**, and they are **not** conjunctive with the class. The consequence is
deliberate: the **2026-10-09 v0.18.5 environmental DB-churn class** is covered by the *same*
class-driven rule — it is **not** a post-boot-only special case. A design that hard-ANDed
boot-age with burst would have been *narrower* than the incident class and would have left
the 10-09 precedent unhandled. (The foundations layer's boot-age/burst AND is superseded by
this position — see the alternatives table.)

> Normative source: requirements.md §4.2 (P2 — grace eligibility classes, and the
> class-driven rule with boot age / burst as published context only).

**A1.2a — The grace is a property of the COMPOSITE, not of one component (council W-2).**
Readiness has **four** components (`database`, `queue_freshness`, `services`,
`checkpoint_saver`). The **multi-component aggregation rule is §4.2's** → this record
does not restate it.

**The design position (reasoning, not a rule):** the aggregation is a **conjunction, not a
vote** — one grace-ineligible component (a genuine exception) among several timeouts kills
the grace **for that tick**; the grace does not "average out", does not wait for the other
components, and is not sticky across ticks. A tick where `grace.active` is false contributes
a **RESET** to the soak counter and cannot extend the window. That is the whole point of
evaluating the composite rather than each probe in isolation: a probe that answers "I am
busy" and a probe that answers "I am broken" must not be averaged into "sort of fine".

> Normative source: requirements.md §4.2 (P2 — the NORMATIVE multi-component aggregation
> rule and its fail-closed direction).

**A1.3 — The grace window is BOUNDED, on ONE clock.** The **ceiling, its clock and the
soak's absolute bound are §4.3's** → this record does not restate them.

**The design position (reasoning, not a rule):** the grace must be bounded because a
flapping daemon must not accumulate unbounded grace, and because the ceremony has a finite
envelope. The clock is **observation-relative** (the grace starts at the first grace-eligible
observation, **not** at boot) → a boot-relative clock would expire the grace before the
burst it exists to absorb, which is exactly the incident's shape. There is **no
boot-relative grace ceiling** → the valueless `grace_abs_ceiling_s` is **deleted** (it was
a second, incompatible clock; see the alternatives table).

> Normative source: requirements.md §4.3 (P3 — grace ceiling, its single
> observation-relative clock, and the relationship to the soak's absolute bound), §4.6 (P6 —
> the AC thresholds those bounds carry).

**A1.4 — The grace REFUSES to engage (and disengages) on a pool-leak signature.** The leak
class is self-sustaining, so a grace that masked it would hide a genuinely broken daemon. The
**leak signature, the ORACLE/ADVISORY pool split, and the streak-arm qualification are
§4.4's** → this record does not restate them.

**The design position (reasoning, not a rule) — and the P1 correction.** The signal is
computed **daemon-side, from the pool objects, with no extra DB round-trip** (an extra query
on a poisoned pool would itself amplify the leak). The **ORACLE** pool is **P2**, the psycopg
checkpoint-saver pool (`daemon/persistence.py:273-294`): the readiness module already reaches
it through `getattr(checkpointer.raw_saver, "conn", None)` and `hasattr(conn, "get_stats")`
(`readiness.py:675-681`), and `get_stats()` exposes `pool_size`, `pool_available`,
`requests_waiting` and `requests_num`.

**P1 (the SQLAlchemy manager engine, `daemon/repositories/factory.py:325-330`) is ADVISORY —
its signals are VISIBLE in `detail.pool_state` but MUST NOT arm the leak class.** This corrects
an earlier draft of this record, which treated a monotonic `checkedout()` rise on P1 as an
arming signal. It must not, because `pool_size=5, max_overflow=10` means `overflow() > 0` is
**non-zero on a busy-but-healthy pool by design** (the ORACLE/ADVISORY split is
`requirements.md` §4.4's, P4 — **normative**)
**non-zero on a busy-but-healthy pool BY DESIGN**; arming the leak class from P1 would refuse
the grace on the incident's own busy shape — the exact failure this commission exists to
fix. The P1 signals remain **published** (an operator must be able to *see* the pool state
without attaching to PostgreSQL), and the **streak arm is qualified to the
pool-objects-UNAVAILABLE case only** (S9), so a sustained busy burst is never classified as a
leak. Where the pool objects ARE available, **P2 is the only leak oracle**.

**Why the pool-objects signal and not `pg_stat_activity`:** the true `idle in transaction` count
lives in PostgreSQL, but querying it requires a connection on the very pool that is poisoned —
the measurement would amplify the disease. The pool's own checked-out accounting is the same
phenomenon observed without the round trip. A `pg_stat_activity` census remains a valuable
**operator** tool (the RCA's evidence) and is referenced from the observability surface (§3h)
as a runbook pointer, not as an in-band gate.

**A1.5 — Grace state is OBSERVABLE, and the underlying reasons NEVER disappear.** The `/readyz`
`detail` gains an additive `grace` block. The existing `detail.reasons` list and the
`components` map keep their current semantics **unchanged** — the reason strings for the
degraded components are still emitted, still accurate, and still present while a grace is
active. A suppressed reason string is a **defect**.

**A1.6 — Where the failure-class signal lives: DAEMON-SIDE.** Justification:

1. The class is only knowable daemon-side. The leak predicate needs the pool objects
   (`manager.engine.pool`, `manager.checkpointer`); the probe-budget knowledge
   (`readiness.py:45`/`:54`/`:59`) is daemon-side; the "did the daemon finish booting" fact is
   daemon-side.
2. A gate-side reader would have to parse free-text reason strings. Those strings are already
   known-stale (premise 23: the reason still names the replaced `check()` probe) and would be
   a fragile contract.
3. The same daemon-side block satisfies the **operator** requirement (see A1.4) — the pool
   state must be visible in `/readyz` so an operator can *see* the poison rather than infer it
   from timeouts. One computation, two consumers.
4. The gate then only needs to *read* the body on the transient path — a one-line change at
   `lib.sh:3005` plus reuse of the existing `_json_sub`/`_json_field` helpers already used for
   `detail.inflight_turns` (`lib.sh:3148-3150`).

**A1.7 — Boot-age signal.** The cheapest available boot-age is `uptime_seconds` from the
`/livez` body (`daemon/api.py:4805`) — the gate already has a livez reader. The daemon-side
alternative (a daemon boot timestamp) is available but is a *second* source of truth. We use
**`uptime_seconds` from the livez body** for the gate-side arming (no new plumbing) and the
**daemon-side composite** for the class/pool state. The two are only used for *different*
purposes and neither is load-bearing for the other.

**A1.8 — Arming surface.** The gate's grace is currently anchored to the **restart** epoch
(`_boot_grace_start` at `lib.sh:4266`, evaluated at `:3196-3211`). Per premise 17 that anchoring
is what let the incident through. We **re-anchor**: the grace becomes *load-episode-anchored* —
it starts at the **first grace-eligible degraded observation** the gate sees, and expires
`GRACE_HORIZON_S` later (the clock and the number are `requirements.md` §4.3's, P3 —
**normative**). The restart-anchored grace is **kept** as a separate, shorter,
pre-existing mechanism (it is already shipped and has its own pin coverage) — we do not remove
it; we add the class-gated one alongside it and re-anchor the *new* one to the episode.

**Alternatives considered.**

| Option | Why rejected |
|---|---|
| Pure time-based grace (extend the window on any transient) | **Violates CONSTRAINT 1.** A genuinely broken release also has a boot window; a purely time-based grace lets it escape the never-green rollback. Also directly falsified by the pool-leak RCA: the leak class is self-sustaining, so a time-only grace would mask a broken daemon indefinitely. |
| Fix the leak first, then add the grace | The leak is an **explicit non-goal** (§8) owned by a follow-up commission. The grace must be safe *in the presence of* the leak — that is precisely what the leak-ineligible class buys. Sequencing would leave the gate unsafe for months. |
| Put the class in the gate (parse the 503 reason body) | Fragile: the reason strings are already known-stale (premise 23) and are free text. The leak predicate additionally requires pool objects the gate cannot see. |
| Widen the probe budgets | The budgets are module constants, not env-tunable (`readiness.py:45`/`:54`/`:59`). Widening them would ALSO delay detection of genuine failures and would not fix the saver's lock contention (§3d). |
| Grace implemented as "ready = true but degraded" in the daemon | Violates the "grace never renders healthy" property and would make the daemon lie to *every* consumer, not just the gate. |
| Extend busy-amnesty to cover the resume burst | **Structurally impossible** — the resume path writes no heartbeats (`auto_continue_boot_pass.py:68-71`), so `inflight_turns` never counts resumed turns (premise 19). |
| *(original reasoning, superseded)* "Eligibility is class-driven — driven by the failure class … not by the daemon's age and not by a 'burst' label" | **ADOPTED, and it GOVERNS.** The foundations layer's boot-age **AND** burst preconditions are **superseded** by this position (iteration 003, blocking #8): boot age and burst state are **published CONTEXT** only, never arming preconditions. The consequence is deliberate — the **2026-10-09 v0.18.5 environmental class** is covered by the same class-driven rule, and a design that hard-ANDed boot-age with burst would have been *narrower* than the incident class. |
| *(original reasoning, superseded)* "The grace window is BOUNDED twice — a hard expiry `grace_expires_at = first_grace_eligible_observation + GRACE_HORIZON_S` **and** a hard cap on total soak extension" | **SUPERSEDED (iteration 003, blocking #7).** The *form* is retained (a bound on the grace and a bound on the extension) but the ceiling, its clock and the numbers are **§4.3's / §4.6's**, and the **valueless boot-relative `grace_abs_ceiling_s`** is **deleted** — it was a **second, incompatible clock**. One clock (observation-relative), one number. |
| Re-anchor the *existing* restart grace instead of adding a class-gated one | The existing grace suppresses *all* transients regardless of class — it is exactly the unsafe shape CONSTRAINT 1 forbids. We keep it for its existing pins but do not extend its semantics. |
| *(original reasoning, superseded)* "Add a daemon-side boot-window grace so the detector is honest under burst" | The premise that a boot window suffices is falsified by the leak (which re-forms ~3 min *after* boot) and by the incident timeline (the burst peaked *after* the 60s grace expired). The correction — class-gated, episode-anchored, leak-ineligible — is what is adopted. |

**Consequences.**

- The daemon gains: a failure classifier, a grace state machine, pool-state sampling, and a leak
  predicate — all additive on `ReadinessComposite` (`readiness.py:172-226`) and the `/readyz`
  `detail` payload (`:239-274`).
- The gate gains: a body-read on the transient path (`lib.sh:3005`), a class parser, and a
  bounded extension policy.
- The readyz body gains fields (§4, CONSTRAINT 1 observability contract). Additive ⇒ older
  consumers tolerate the new keys (the `inflight_turns` precedent, `readiness.py:198-205`).
- **Every acceptance criterion is machine-checkable** — see the AC table in the implementation
  plan (to be written); the shapes pinned here are: (i) a class-classification unit pin over
  the failure table; (ii) a leak-predicate pin over a synthetic monotonic `checkedout()`
  sequence; (iii) a gate-side pin that a grace-eligible transient is HELD and a
  grace-INELIGIBLE transient still resets; (iv) a pin that `/readyz` during an active grace
  still returns 503 with the underlying reason strings present.

> **INTERIM EFFICACY DISCLOSURE (council R-2), REBASED ON §4.4 (iteration 003,
> N-B1 + N-B3).** The **CONFIRMED** leak mechanism is the four code-confirmed leak **sites**
> (the `ens_db_tools` explicit `BEGIN` outside the `try`, the `/api/instances` ORM read path,
> the `mission_records` mint path, and the RCA's `job_queue_items` count path) plus the
> readiness probe's **timeout-path connection retention** (`readiness.py:40-45`). The RCA's
> attribution of a *per-tick* leak to the readiness probe is **not** supported (premise 10)
> and the two statements are reconciled here rather than left in contradiction.
>
> **The efficacy consequence, stated plainly:** the 22:10–22:50Z incident is a **P1**
> (SQLAlchemy manager-engine) pool poisoning, and **P1 is ADVISORY** — its signals are visible
> in `detail.pool_state` but **MUST NOT arm the leak class** (§4.4: `overflow() > 0` is
> non-zero on a busy-but-healthy pool by design). So a P1 leak does **not** produce
> `pool_leak`; it surfaces as `probe_timeout` (grace-eligible) and the grace **engages** —
> which is the **correct** behaviour per §4.4 (a busy pool is busy, not broken) and
> precisely why the leak **repair** is a non-goal that must land rather than something the
> grace is meant to absorb.
>
> **Therefore: the healthy-release rollback CAN RECUR for the P1-leak signature.** The grace
> was never the mitigation for it, and the arming oracle is the P2 checkpoint-saver pool
> (§4.4) — so a P1-starved daemon is observed as a *busy* daemon and the gate waits for
> it, and if the starvation persists the never-green trigger (§4.1) still rolls the
> release back. The **interim primary objective is carried by C1 / C2 / A3 / A4** (stop the init
> storm, fail fast on a dead host, hold the resume burst during the ceremony, and size the
> saver pool) — **not** by the grace, which covers only the busy-but-not-leaking signature.
> A reader who expects this lane to make the 2026-10-26 rollback impossible is reading it
> wrong: this lane makes the *init storm* and the *resume burst* survivable, and it makes the
> *leaking* case fail **loudly and correctly** rather than silently.
>
> Normative source: requirements.md §4.4 (P4 — leak signature, the ORACLE/ADVISORY pool
> split, and the streak-arm qualification).

### 3b. (b) Soak semantics

**Context.** `gate_soak` (`lib.sh:5053-5248`) requires **3 consecutive hard greens** on *both*
`/livez` and `/readyz` (`:5216-5221`), an early exit only when `consecutive_green >= 3` **and**
wall-clock `soak_elapsed >= ADAPTIVE_SOAK_FLOOR_S` (`:5232-5236`), and a cap-reach with
`consecutive_green < 3` ⇒ return 1 ⇒ auto-rollback **and quarantine** (`:5237-5244`;
quarantine is cleared only by a re-stage via `stage.sh` — `lib.sh:1221-1234`). The soak takes
**no retry** (`:5161`, `:5181` call `_probe_once_classified` directly), so a single transient
resets the counter to 0.

**Decision.**

**B1 — The counter semantics change: three observation kinds, not two.** The **observation
kinds, their preconditions, the GREEN reset-on-green, the livez-hard-green HELD precondition, and
the bound on consecutive holds are §4.2's** → this record does not restate them.

> Normative source: requirements.md §4.2 (P2 — counter observation kinds GREEN / HELD /
> RESET, including the GREEN `consecutive_hold = 0` reset and the livez-HARD-green HELD
> precondition).

**Why HOLD and not advance.** A held observation is not a green; counting it as one would
reintroduce exactly the defect that makes the current gate permissive (premise 4) in the
opposite direction. But *resetting* on a recoverable-class transient is what made 3 greens
**unreachable** under sustained load. HOLD preserves the C-1 invariant ("3 consecutive greens")
while making it reachable.

**B2 — Holds are bounded, so a stream of transients can never satisfy the soak.** The
**bound and its number are §4.2's** → this record does not restate them.

**The design position (reasoning, not a rule):** a HOLD advances nothing, so a daemon that
only ever produces transient observations still ends the soak with `consecutive_green = 0`.
The bound on consecutive holds is what stops a *stream* of transients from satisfying the
3-green requirement, and the **unconditional** cap-reach trigger (§4.1) is what makes the
end state a **rollback** rather than a wait. Without the bound, a sustained transient stream
would eventually accumulate three "greens" it never earned; without the unconditional
trigger, the window would be a conditional on the rollback path. Both halves are needed and
both are §4.1/§4.2's rules.

> Normative source: requirements.md §4.2 (P2 — the bound on consecutive holds), §4.1
> (P1 — the unconditional never-green trigger).

**B3 — The soak window extends, but only as a bounded extension.** The **cap arithmetic,
the extension cap, the re-arm recompute timing, and the soak's absolute bound are §4.3's and
§4.6's** → this record does not restate them.

**The design position (reasoning, not a rule):** the extension exists so that a daemon which
is *busy* has a fair chance to be observed green, **without** weakening the never-green
protection (the trigger itself is `requirements.md` §4.1's, P1 — **normative** and
unconditional). Two properties make that safe: the extension is **bounded and
spent-not-refunded**
(so a flapping daemon cannot accumulate unbounded window), and it is **anchored at the soak
entry** (so the absolute bound is `soak_entry + 300s` regardless of how many times the grace
re-arms). The recompute-after-re-arm timing is §4.3's rule; the *reason* it is
**entry-anchored** rather than grant-anchored is that a grant-anchored clock would let a
repeatedly re-arming grace walk the ceremony's wall-clock budget.

> Normative source: requirements.md §4.3 (P3 — the grace ceiling, its clock, and the
> relationship to the soak's absolute bound), §4.6 (P6 — the AC thresholds: soak
> extension cap and soak absolute bound).

**B4 — The never-green trigger stays UNCONDITIONAL (iteration-003 correction).**
**This decision record previously stated a CONDITIONAL trigger ("no green observed AND no
fair observation window") and a THIRD cap-reach outcome (ceremony-budget truncation ⇒
`soak_deferred`, return 0). Both are **DELETED**.** They were **REJECTED by the approver
(iteration 002, blocking #1)**: implementing the conditional literally **adds a new
conditional to the rollback path**, which is precisely what the approver refused — it gives a
never-green release a way to escape auto-rollback. The premise was also **mis-derived**: the
ceremony-truncation case is the **PRE-SOAK deferral** at `lib.sh:5088-5093` (it returns 0
**before** the soak loop begins), **not** a cap-reach outcome.

**The design position (unchanged in substance, corrected in form):** the rollback path keeps
exactly its current shape. The grace does not weaken the never-green protection; it changes
**how long the gate is willing to WAIT**, never **whether a never-green release is rolled
back**. A **grace-eligible daemon that never greens still rolls back** → the grace
arms, the counter still reaches cap-reach below 3, return 1.

> Normative source: requirements.md §4.1 (P1 — never-green trigger: unconditional, exactly
> two cap-reach outcomes, the ceremony-truncation case is the pre-soak deferral, fairness
> diagnostics are DIAGNOSTIC ONLY and must not influence the return value, and the required
> §5 leg: a grace-eligible never-green daemon still rolls back).

**What survives from the withdrawn form:** only a **fairness diagnostic** — a journal line
recording *how much* of the window was actually observed, surfaced for the operator. It is
**DIAGNOSTIC ONLY**: it MUST NOT influence the return value, and it is **pinned as not a
success path** (a diagnostic field is never a green, never advances the counter, and never
suppresses a cap-reach). See the alternatives table for the withdrawn forms.

**B5 — The `_PROBE_STATUS` aliasing defect is fixed in the same change.** The counter must read
the *per-probe* status, not a shared global. The fix is to capture each probe's status into a
distinct local immediately after the call (e.g. `livez_status="$_PROBE_STATUS"` at `:5162`)
and to use those locals in the counter at `:5216-5217`. The direction of the existing bug is
recorded above (premise 4): it makes the gate **more permissive** than documented, i.e. it
**weakens** C-1. Leaving it in place while adding grace semantics would make the new logic
unreviewable.

**B6 — Conservative mode is REUSED as the strict escape hatch, and NOT extended.**
`ENSEMBLE_SOAK_CONSERVATIVE=1` (`lib.sh:5067-5069`) runs the v0.18-identical strict loop
(`:5115-5149`, `_PROBE_SUPPRESSION_STRICT=1`, no grace, no amnesty, flat 300s, sleep 30). We
**extend the ADAPTIVE path** (§3b B1–B5) rather than inventing a third mode, and we keep
conservative mode **byte-identical** as the operator's "no grace, no amnesty" switch. Rationale
for not extending conservative: it is the escape hatch; making it *more* capable dilutes its
purpose.

**B7 — Delivery mechanics differ between the shell and the daemon halves.** The shell half
(A1's gate side, A2, A3's gate side) is consumed from the **working tree** — the daemon's
`_resolve_scripts_dir` resolves `ENSEMBLE_UPGRADE_SCRIPTS_DIR` → the repo checkout → the
install dir (`daemon/tools/upgrade_tools.py:1919-1942`), and the release payload ships no
`scripts/`. So the shell fix can affect the **next promote without a release bump** — but it
**must still be committed and shipped in the repo** (an uncommitted shell fix is not a fix).
The daemon half (the class/pool/grace computation, C1–C4) needs a release. decisions.md
records this so the two lanes are not confused.

**Alternatives considered.**

| Option | Why rejected |
|---|---|
| *(original reasoning, superseded)* "The never-green trigger becomes \"no green observed AND no fair observation window\" — cap-reach rolls back only if a fair window was actually offered — with a third outcome for the ceremony-budget truncation (`soak_deferred`, return 0)" | **SUPERSEDED and REJECTED (approver iteration 002, blocking #1 — SAFETY).** Two independent reasons: (i) implementing the conditional **adds a new conditional to the rollback path** — it gives a never-green release a way to escape auto-rollback, which is the safety property the gate exists to enforce; (ii) the premise is **mis-derived** — the ceremony-budget truncation is the **PRE-SOAK deferral** at `lib.sh:5088-5093` (returns 0 before the soak loop), not a cap-reach outcome. The current form is in **B4**. |
| Just add retry to the soak (mirror `_probe_with_retry`) || Make the soak count a transient as a green | Directly destroys the C-1 invariant — a never-green daemon would pass. |
| Make the soak count a transient as a half-green | Same failure in a subtler form: the invariant is "3 consecutive greens", and a fractional counter makes the C-1 trigger a judgement call. |
| Extend the soak window unconditionally when the daemon is young | Violates CONSTRAINT 1 (time-only) and premise 17 (the burst peaks after the boot window). |
| Extend the soak window unconditionally on any transient | Violates CONSTRAINT 1 — a broken release gets the same extension as a busy one, and the never-green trigger becomes "no green observed" again, which is the exact failure being fixed. |
| Raise `ADAPTIVE_SOAK_CAP_S` from 120 to, say, 300 | A blunt instrument: it delays *every* soak, including ones that should fail fast, and it does not fix the "3 greens unreachable" property — the counter would still reset on each transient. It also eats the 900s ceremony budget (`lib.sh:109` enumerates a 870s non-stop worst case). |
| Add a third soak mode ("burst-aware") | Mode proliferation: each mode needs its own pins, its own budget arithmetic, and its own operator documentation. The adaptive path already carries the knobs; the conservative path already carries the strictness escape. Two modes are enough. |
| *(original reasoning, superseded)* "Require 3 consecutive greens *with* a burst-aware window — accept 'green after the burst settles'" | Correct in direction but underspecified: it did not say what a burst-aware window *is*, nor how the never-green trigger is re-expressed, nor how the counter treats a held observation. B1–B5 supply all three. |

**Consequences.**

- `gate_soak` gains: three observation kinds, a hold counter with a bound, a bounded extension,
  and the aliasing fix. The cap-reach branch keeps its two outcomes (success / return 1).
- The `soak_deferred` journal event (`lib.sh:5088-5093`) is the existing precedent for a
  journalled deviation; the extension decision should emit a **new journal event**
  (`soak_grace_extended`) so a soak that rode through a grace is auditable in the journal —
  a soak that quietly lasted 300s instead of 120s must be visible.
- **The never-green rollback protection survives intact** and is *unconditional*: at cap-reach
  with `consecutive_green < 3` the release is rolled back, exactly as before and with **no**
  added condition on the rollback path (§4.1). The grace changes only *how long the gate
  is willing to WAIT*; it never changes *whether* a never-green release is rolled back.

**Reversal.** The shell change is one function (`gate_soak`) plus one helper. Reverting the
commit restores the previous semantics wholesale; there is no persisted state to unwind (the
journal event is additive history). Conservative mode remains available as an *immediate*
operational mitigation without any code change — that is a **day-one mitigation** for the next
promote.

---

### 3c. (c) Auto-continue hold

**Context.** The RCA's largest single contributor was the auto-continue resume burst (4
instances at 20:43:00, 29 `[RESUME]` lines in 3 minutes in the later incident). **FOUR**
dispatchers can move turns:

1. the **boot pass** `continue_running_instances_after_restart`
   (`daemon/services/auto_continue_boot_pass.py:272`), invoked at `daemon/api.py:2703`;
2. the **runtime resume dispatch** `_schedule_explicit_handle_resume`
   (`daemon/manager.py:13162`), which ends in a fire-and-forget
   `asyncio.create_task(self._resume_processing_background(...))` at `daemon/manager.py:13671-13684`;
3. the **eligible-pending sweep** `EligiblePendingSweepService`
   (`daemon/services/eligible_pending_sweep.py`), started at `daemon/api.py:1525`, whose
   **first tick fires immediately** (`eligible_pending_sweep.py:387-399`, the loop body calls
   `sweep_once()` before the first sleep), interval 90s, min pending age 60s
   (`daemon/config.py:2185`), **always ON with no kill switch**;
4. the **sister boot pass** `sweep_orphaned_live_turns`
   (`daemon/services/auto_continue_boot_pass.py:726-729`), wired at `daemon/api.py:2708` — it
   covers the **disjoint** sub-class of RUNNING instances whose `task` row was lost, so
   StaleTaskRecovery **cannot** reap it. It shares the same kill-switch and the same resume
   primitive as seam 1 (`auto_continue_boot_pass.py:732-735`).

> **COUNCIL FINDING C-1 (CRITICAL — the only design-level defect).** Seam 1's release trigger
> was **false as originally written**. The boot pass runs **exactly once per boot** — its only
> call site is `daemon/api.py:2703` (the other reference, `:2681`, is the import). During a
> promote the daemon **restarts before the gates**, so the pass *always* runs with the promote
> txn live, defers every candidate, and **there is no next pass this boot**. Deferred candidates
> would fall to StaleTaskRecovery's `force_cancel_and_schedule_retry`
> (`daemon/repositories/task/repository.py:5591`) at
> `stale_task_recovery_threshold_minutes = 10` (`daemon/config.py:1821-1823`) — **losing the
> checkpoint resume the pass exists to provide**. The original section 3c C3 table row
> ("deferred and re-evaluated on the next pass") was therefore wrong. **Corrected below.**

The boot kill-switch (`ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART`,
`auto_continue_boot_pass.py:149`) is read once at boot (`:162-175`, call sites `:366` and
`:760`) — it cannot gate a mid-soak resume. It also must be set in the install `.env`;
`systemctl set-environment` does not reach this daemon (per the RCA's 2026-10-22 correction).

**Decision.**

**C1 — The hold is anchored to the promote TRANSACTION, using the existing readers and the
existing vocabulary.** The promote transaction is shell-owned: `scripts/upgrade/promote.sh:327`
opens it (`journal_open_txn "promote"`), `:412` marks it flipped, and
`scripts/upgrade/lib.sh:1116-1118` closes it on every terminal path (commit `:567`, and the
halt/rollback paths `:596`, `:606`, `:615`, `:625`, `:701`, `:710`, `:718`). The daemon never
writes `kind="promote"` — only `kind="restart"` (`daemon/tools/upgrade_tools.py:2891-2902`).

**The LIVE predicate itself is §4.7's** → this record does not restate it. Its two legs
are the **post-boot heartbeat leg** and the **boot-window leg**, and the design position
behind the second leg is recorded here because it is the reason seam 1 is safe:

> **SAFETY #3 (iteration 002, blocking #3) — the boot-window leg is REQUIRED, not optional.**
> The heartbeat-freshness proof below covers the **SOAK**. It does **not** cover **seam 1**,
> which fires during the daemon-BOOT window: the shell writes `in_flight` **BEFORE** restarting
> the daemon (`promote.sh:327` open ⇒ `:413` stamp ⇒ `:426` restart), and the next
> heartbeat refresh happens only inside `gate_livez`'s retry loop (`lib.sh:3074`; `_probe`
> `:3230`/`:3286`), which runs only **after** the daemon can serve `/livez`. The observed
> daemon was ~75 s old at its first green readyz, so a 60 s heartbeat window
> (`OWNER_HEARTBEAT_FRESH_S = 60`, `daemon/tools/upgrade_journal.py:156`) **can already be
> expired at seam 1** — the hold would be **silently inert** at the seam with the largest RCA
> contribution. The boot-window leg (`BOOT_LIVENESS_S = 120.0` s; the predicate and the
> number are `requirements.md` §4.7's, P7 — **normative**) exists precisely to close
> that hole, and the number is justified by exceeding both the observed ~75 s boot-to-first-green
> and the 60 s heartbeat window while staying far inside the ceremony envelope (900 s).
>
> **During the boot window the hold is HOLD-only**: release requires `in_flight` to be
> **ABSENT**, not merely stale.

> Normative source: requirements.md §4.7 (P7 — the promote-txn LIVE predicate with its
> POST-BOOT and BOOT-WINDOW legs, `BOOT_LIVENESS_S = 120.0` s, and the boot-window
> HOLD-only release rule).

- The reader is the **existing, read-only, purpose-built accessor**
  `daemon/tools/upgrade_journal.py:281-319` (freshness window `OWNER_HEARTBEAT_FRESH_S = 60`,
  `:156`), consumed today by `daemon/services/upgrade_journal_sweep.py:2061`.
- The **heartbeat is genuinely fresh during the soak**: `lock_heartbeat`
  calls `lock_heartbeat` on every iteration  calls `lock_heartbeat` on every iteration (`:5156`). So the 60s window is comfortably inside
  the 15s soak cadence.
- The **vocabulary** is the existing `"pipeline-busy"` refusal reason
  (`daemon/tools/upgrade_tools.py:2678`, `:2687`, `:2695`) — the hold's log line and its
  refusal reason reuse that token. No new concept.

**C2 — The hold releases on a STALE transaction (fail-open).** If the promote dies without
closing the txn, `last_heartbeat` goes stale and the hold **releases** after 60s. A crashed
promote must not freeze the resume set forever. This is the same fail-open discipline the
sweeper already uses (`upgrade_journal.py:285-296`).

**C3 — The hold covers all FOUR dispatchers, and each deferred candidate is RELEASED through
its ORIGINAL seam.** A hold that covers only the boot pass would still leave the runtime resume
dispatch, the eligible-pending sweep and the task-less-orphan sweep free to move turns during
the soak. Decision: a **single shared predicate** (`promote_txn_live()`) consulted at each
dispatch seam, plus a **per-seam deferred-candidates list** owned by the hold module:

| # | Dispatcher | Seam | Hold behaviour | Release behaviour |
|---|---|---|---|---|
| 1 | Boot auto-continue pass | `daemon/services/auto_continue_boot_pass.py:272` (`continue_running_instances_after_restart`), called at `daemon/api.py:2703` | Candidates are **deferred**, not dropped: they are parked in the hold module's seam-1 list and reported in the `ContinueResult` as deferred | On predicate flip-to-false, released **through the boot pass's own checkpoint-resume path** (never a cold dispatch) |
| 2 | Runtime resume dispatch | `daemon/manager.py:13671` (the `create_task` for `_resume_processing_background`) | The task is **not created**; the resume is parked in the seam-2 list | On flip-to-false, the **original** `create_task(self._resume_processing_background(...))` is made — the checkpoint-resume path, not a cold dispatch |
| 3 | Eligible-pending sweep | `daemon/services/eligible_pending_sweep.py:399` (`sweep_once()`) | The tick **skips the wake** and parks the eligible rows in the seam-3 list; a skip count is recorded | On flip-to-false, the parked rows are re-notified through the sweep's own wake path |
| 4 | Task-less-orphan sweep | `daemon/services/auto_continue_boot_pass.py:726-729` (`sweep_orphaned_live_turns`), wired at `daemon/api.py:2708` | Task-less orphans are **deferred**, not dropped: they are parked in the seam-4 list | On flip-to-false, released through the sister pass's own resume primitive |

**C3a — The release trigger: a bounded re-consult loop, not "the next pass".** The
**release rule, its cadence, and its hysteresis are §4.7's** → this record does not
restate them. The hold module `daemon/services/promote_txn_hold.py` owns the predicate, the
per-seam deferred-candidates lists, and the release driver.

> **SAFETY #4 (iteration 002, blocking #4) — the RELEASE HYSTERESIS is REQUIRED.** The
> release sweep fires only after **TWO CONSECUTIVE FALSE** predicate reads (the hysteresis is
> `requirements.md` §4.7's, P7 — **normative**), at least one
> driver tick (5 s) apart. The reason is a **torn read**: the accessor returns False on
> `JournalTorn` / `OSError` (`daemon/tools/upgrade_journal.py:300-303`), so a **single**
> unreadable `state.json` read would otherwise flip the predicate false and **release the
> deferred resumes mid-promote** — the exact burst A3 exists to suppress. The risk register
> named this guard (R-LIVE-13) before the design did; the design now carries it, and the
> register entry **references §4.7 rather than owning the rule**.

> Normative source: requirements.md §4.7 (P7 — the release rule, the two-consecutive-false
> hysteresis, the 5 s driver tick, and the boot-window HOLD-only rule).

**C3b — The release is bounded and cannot spin.** The re-consult loop only exists while
deferrals are outstanding; each deferred candidate is released exactly once; the loop is
cancelled on daemon shutdown. The hold never blocks serving.

**C3c — The release DRIVER (council N-b).** The **driver's shape — lazy creation,
the tick cadence, the cancellation point, the shutdown registration and the crash backstop —
is §4.7's** → this record does not restate it. The driver is a lazily-created asyncio
task owned by `daemon/services/promote_txn_hold.py`.

**The design position (reasoning, not a rule):** the driver is created on the **first
deferral** and **module-held**, so a daemon that never defers never creates a task and never
pays the poll. It ticks only while deferrals are outstanding and is **cancelled when the
deferred lists empty** after a release sweep — it is not a permanent poller. It is
**registered on the daemon's ordered-shutdown path**, so a shutdown mid-hold cancels it
deterministically instead of leaking a task. If it crashes, the **bounded release timeout**
(the staleness window) is the backstop and the crash is logged, never swallowed. The
hysteresis (§4.7) is what makes a *torn* read harmless; the backstop is what makes a
*crashed driver* harmless; they are different failure modes and both are needed.

> Normative source: requirements.md §4.7 (P7 — the driver).

**Alternatives considered.**

| Option | Why rejected |
|---|---|
| Extend `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART` to be checked live | The env is read once at boot (`:162-175`); making it live means re-reading the environment, and — critically — it would disable the resume pass **entirely** rather than deferring it. It also still does not cover dispatchers 2 and 3. |
| Reject resumes whose instance is in a promote's "quiesce" set | The promote's quiesce set is a *shell-side* construct; the daemon would need to read it, and the resume burst is the thing we are trying to defer — a quiesce set built *after* the resumes start is too late. |
| Pause the instances during the soak | A pause cancels in-flight graph tasks (the Pause-First convention) — far more invasive than deferring new work, and it would change the soak's own semantics (the soak wants a *busy but healthy* daemon, not a quiesced one). |
| Introduce a new journal field (`promote_hold`) | Parallel concept. The promote txn **already exists** in `in_flight` and already has a liveness heartbeat; a new field would be a second source of truth with its own staleness semantics. |
| Hold only the boot pass | Leaves dispatchers 2, 3 and 4 free. The eligible-pending sweep's **first tick is immediate** (`eligible_pending_sweep.py:387-399`) and it is always on — it alone can move turns into the soak window. The task-less-orphan sweep is the disjoint sub-class StaleTaskRecovery **cannot** reap. |
| **(a) Re-consult on the 90s eligible-pending-sweep tick** | **Rejected (council C-1).** Couples two unrelated concerns (the resume hold and the pending-task sweep), and adds up to **90s** of release latency after the promote txn closes — during which the recovered instances stay dark. |
| **(b) Hold module owns a per-seam deferred-candidates list and re-consults the predicate on a short bounded cadence (default 5s, read-only `state.json`, only while deferrals are outstanding), releasing through the ORIGINAL seams on flip-to-false** | **ADOPTED (council C-1, planner-adjudicated option (b)).** It is the only option that (i) actually releases — the boot pass runs once per boot (`daemon/api.py:2703` is its only call site) and there is no "next pass" to rely on; (ii) keeps the concerns separate (the hold owns its own timer); (iii) preserves the checkpoint-resume path (release goes through the original seam); and (iv) composes with fail-open-on-stale, so a crashed promote releases rather than freezes. |
| **(c) One-shot delayed re-run of the boot pass** | **Rejected (council C-1).** Re-runs the *whole* pass — including its candidate discovery, boot-epoch generation and audit bookkeeping — which carries **double-processing risk** for candidates the first run already acted on, and duplicates side effects the pass is not written to be idempotent across. |
| *(original reasoning, superseded)* "Candidates are deferred, not dropped: they are returned in the `ContinueResult` as deferred and re-evaluated on the next pass" | **SUPERSEDED by council C-1.** There *is* no next pass within the boot — the pass runs exactly once (`daemon/api.py:2703`). The corrected release trigger is **C3a** (bounded re-consult + release through the original seam), and the fourth seam (task-less-orphan sweep) is added. |
| *(original reasoning, superseded)* "A3's hold should reuse this anchor and this vocabulary rather than invent a parallel journal concept" | **Adopted as the decision** — the correction confirms the anchor (`journal_owner_heartbeat_fresh`, `OWNER_HEARTBEAT_FRESH_S=60`) and the vocabulary (`"pipeline-busy"`), and *adds* the requirement to cover the runtime resume dispatch and the eligible-pending sweep, which the original reasoning had not enumerated. |

**Consequences.** **Four** dispatch seams gain a predicate call; one new shared helper is needed
(a thin wrapper over `daemon/tools/upgrade_journal.py::journal_owner_heartbeat_fresh` plus the
`in_flight.kind == "promote"` test, and the install-dir resolution that
`daemon/tools/upgrade_tools.py:693-723` already performs). The install-dir resolution
`_install_root_candidates` / `_canonical_install_root` (`daemon/tools/upgrade_tools.py:388-431`)
is the existing mechanism for finding the live install root — reuse it; do not re-derive.

**Reversal.** The predicate is a single boolean read. Reverting the three call sites restores
today's behaviour exactly. The hold has **no persisted state** and **no schema change** ⇒
revertible by a single commit.

---

### 3d. (d) Checkpoint pool sizing formula

**Context — and the correction that reshapes this decision.** The pool in question is the
**psycopg `AsyncConnectionPool`** that backs the LangGraph saver
(`daemon/persistence.py:273-294`). Two facts change the shape of the fix:

1. **The pool has no `timeout` kwarg.** `daemon/persistence.py:273-294` passes
   `conninfo`, `open`, `min_size=1` (`:283`), `max_size=5` (`:284`), `check`,
   `max_lifetime=3600`, `max_idle=600`, `reconnect_timeout=300`, and `kwargs`. psycopg_pool's
   library default for the acquisition deadline is **30.0s**.
2. **The saver serialises every cursor operation through ONE lock.**
   `langgraph/checkpoint/postgres/aio.py:59` declares `self.lock = asyncio.Lock()` and
   `:374` takes it around every operation (`async with self.lock, _ainternal.get_connection(self.conn) as conn:`).
   The readiness sentinel read (`saver.aget_tuple`, `daemon/services/readiness.py:723`) takes
   **the same lock** as every production checkpoint write.

⇒ **Enlarging the pool does not shorten the lock queue.** A pool-size knob alone is a partial
fix at best. The decision must address the **contention** and the **pool** separately.

**Decision.**

**D1 — The decision is TWO parts, and they are explicitly different.**

- **D1a (contention): the readiness probe must be able to distinguish "the saver's lock is
  saturated" from "the checkpoint path is broken".** Today both surface as a timeout, and the
  composite cannot tell them apart — which is precisely why CONSTRAINT 1's failure-class
  table cannot be built without this change. This is the change that makes the probe honest
  under load — and it is the piece sizing cannot deliver.

  > **SAFETY #5 (iteration 002, blocking #5) — ONE mechanism, and it is the NON-BLOCKING
  > observation.** The probe **observes** the saver lock's state; it **never acquires** it.
  >
  > **The mechanism (pinned):** `checkpointer.raw_saver.lock.locked()` — a **non-blocking**
  > boolean read of the saver's lock. If the lock is **held** at observation time, the probe
  > reports the class **`pool_busy`** (grace-eligible) instead of `hard_component_red`
  > (never-eligible). If the lock is **not held**, the probe proceeds with the sentinel read
  > and the read's own outcome decides: read timeout ⇒ `read_timeout` (grace-eligible);
  > read raises ⇒ `hard_component_red` (never-eligible).
  >
  > **The acquire-and-read variant is DELETED** and must not ship. The literal reading of
  > "acquire the saver lock, then read while holding it" **self-deadlocks**: the sentinel read
  > at `langgraph/checkpoint/postgres/aio.py:374` takes the **same** lock (declared `:43`,
  > assigned `:59`), and `self.lock` is a plain **non-reentrant** `asyncio.Lock` — so the read
  > could never complete (`hard_component_red`, grace-INELIGIBLE) and every production
  > checkpoint write would queue behind the probe. The two mechanisms are incompatible; the
  > non-blocking one is chosen.
  >
  > **PIN (N-a + SAFETY #5):** (i) the lock-saturated detection expression **resolves against
  > the real adapter surface** — `daemon/checkpoint_adapter.py:190-195` states "Non-method
  > access (e.g. `wrapper.conn`, `wrapper.lock`) passes through unchanged", and `:227-229` lists
  > `lock` among the attributes `__getattr__` covers; the underlying `AsyncPostgresSaver`
  > declares `lock: asyncio.Lock` (langgraph `checkpoint/postgres/aio.py:43`) and assigns
  > `self.lock = asyncio.Lock()` (`:59`) — a test asserts `checkpointer.raw_saver.lock` IS an
  > `asyncio.Lock` and that `.locked()` is callable and returns a bool on a real PG-backed
  > saver; and (ii) **the probe never holds the saver lock across `aget_tuple`** — a test
  > asserts the lock is not held by the probe at the moment the sentinel read is issued.
  > An unresolvable expression must never be able to disable the grace silently.

  > **The probe's return contract changes** (this is the design authority's call, the class
  > semantics are §4.2's): the probe callable's signature goes from
  > `Callable[[], bool]` to a **`(bool, class)` pair**, so the orchestrator can consume the
  > class without re-deriving it from free-text reasons. The binding architecture layer carries
  > the same signature.

- **D1b (capacity): size the pool against the concurrent-writer arithmetic.****D2 — The sizing formula.**

```
concurrent_checkpoint_writers = min(limits.llm_concurrency,
                                    limits.worker_pool_size + limits.chat_worker_pool_size)
checkpoint_pool_max_size = clamp(concurrent_checkpoint_writers + 1 /*readiness probe*/ + headroom,
                                 min=5, max=20)
headroom = 2
```

With the shipped defaults: `llm_concurrency = 10` (`daemon/config.py:595`, `config.yaml:223`),
`worker_pool_size = 5` (`daemon/constants.py:91`), `chat_worker_pool_size = 2`
(`daemon/constants.py:103`) ⇒ `min(10, 7) = 7` writers ⇒ `7 + 1 + 2 = 10`.

**Why 7 and not 10.** `limits.llm_concurrency` is acquired around the **entire
`graph.astream(...)` turn** (`daemon/services/instance_messaging.py:5407-5408`), so it bounds
concurrent graph *turns* and therefore transitively concurrent checkpoint writers. But the
effective ceiling is also capped by the worker pools: `min(10, 5 + 2) = 7`. Sizing against
`llm_concurrency=10` alone would over-provision by 3; sizing against 7 is the honest number.
**The probe must be counted separately** (the current `max_size=5` is *below* the 7 writers
before the probe is even considered).

**D3 — SSOT for the knob, following the house pattern.** The new fields live in
`LimitsConfig` (`daemon/config.py:586-...`, `env_prefix="LIMITS_"`), immediately beside
`llm_concurrency` (`:595`) and the two worker-pool sizes (`:603`, `:611`) — because the
formula *reads* those three numbers, and keeping the inputs and the derived knob in one
class makes the relationship legible.

| Field | Env var | yaml | Default |
|---|---|---|---|
| `limits.checkpoint_pool_max_size` | `LIMITS_CHECKPOINT_POOL_MAX_SIZE` | `limits:` block in `config.yaml` | `10` (the computed value at shipped defaults) |
| `limits.checkpoint_pool_timeout_s` | `LIMITS_CHECKPOINT_POOL_TIMEOUT_S` | `limits:` block | `5` |

- **`checkpoint_pool_timeout_s` is not optional.** It replaces the library's 30.0s default with
  an explicit, bounded acquisition deadline. Rationale: **ghost waiters.** Pool exhaustion
  *queues*, it does not raise — the Nth acquirer is appended to the pool's waiting queue and
  `PoolTimeout` fires only at the pool deadline. The readiness probe abandons at its inner
  0.9s / outer 1.0s budget (`readiness.py:733` uses `CHECKPOINT_SAVER_PROBE_TIMEOUT_S - 0.1`),
  **but the pool retains the queued entry until the pool deadline**. At the 10s refresh
  cadence (`daemon/config.py:2657-2658`) every timed-out probe therefore leaves a ghost
  waiter that will consume a slot when granted — a **self-amplifying pool drain**. The
  **bound** and the resulting ghost-lifetime arithmetic are **§4.6's / the config SSOT's**
  (the `LimitsConfig` field pair below); this record does not restate the number.
- **The probe must also CANCEL its queued position.** The probe's scheduled future
  (`readiness.py:732` `future = asyncio.run_coroutine_threadsafe(_sentinel_cycle(), loop)`)
  must be explicitly cancelled when the budget expires, so the queued acquisition is
  abandoned rather than left to the pool deadline. **Caveat that must be verified, not
  assumed:** whether cancelling a task already blocked inside `pool.getconn` removes its
  waiter from psycopg_pool's internal queue is a **library-behaviour question**. It is an
  explicit **acceptance criterion** (a test that asserts the pool's waiting-queue length does
  not grow across N timed-out probes), not a claim we make here.

> **AC (council V4) → implement/test.** *Cancellation of a task blocked in psycopg
> `pool.getconn` removes the internal waiter.* The AC is: cancel N timed-out probe futures,
> then assert the pool's waiting-queue length has not grown and that the pool's
> `requests_waiting` returns to its baseline (the AC's bound and the pool-state semantics are
> `requirements.md` §4.6 / §4.4's — **normative**). **Fallback if the AC fails:** the probe
> uses a **dedicated connection** that bypasses the pool's acquisition queue, so a timed-out probe
> can never occupy a shared slot regardless of the library's cancellation semantics. The
> fallback is part of the design, not a contingency to be improvised.
>
> **RECONCILIATION (council N-c) — the dedicated-connection fallback is a MITIGATION, never a
> DETECTOR.** A dedicated connection that bypasses the pool's acquisition queue **cannot
> observe queue growth** (the leak oracle and the pool split are `requirements.md` §4.4's,
> P4 — **normative**): it never queues, so it does not disturb `requests_waiting` on the
> shared pool, and it cannot itself report the ghost-waiter accumulation it was meant to
> detect. If the V4 AC fails, the dedicated connection is therefore adopted **ONLY** as a
> *contention mitigation* (a timed-out probe stops occupying a shared slot), **NOT** as a
> replacement for the shared-pool probe. **Pool-starvation detection stays where it belongs:**
> in D1a's lock classification (`pool_busy` vs `hard_component_red`) plus the
> `detail.pool_state` observables (`checkedout`, `overflow`, `requests_waiting` — the leak oracle
> and the ADVISORY pool are `requirements.md` §4.4's, P4 — **normative** — section 3a
> A1.4). A design that swapped the shared probe for a dedicated one would make the pool
> **unobservable** at the exact moment it matters.
- **`reset_on_return` is NOT in scope** — it is listed as an explicit non-goal (§8).

**D4 — The pool being poisoned underneath is a SEPARATE defect; sizing does not cure it.**
This is a hard honesty requirement. The pool-leak RCA proves that a separate defect
(leaked `idle in transaction` backends on the **SQLAlchemy** pool — see §7) is what actually
starves the probes, and that the `mission_records` mint path raises `UniqueViolation` 4× per
boot with the exception raised inside a transaction without a rollback. **Increasing the
checkpoint pool's `max_size` does not fix a pool that is being poisoned**, and presenting it as
a fix would be false. The sizing change is therefore scoped to what it *is*: headroom against
the concurrent-writer arithmetic and a bounded acquisition deadline. The **root-cause leak
repair is a separate commission** (§8).

**D5 — Pool-state surfacing is part of this decision's observability, not an optional extra.**
An operator must be able to **see** the poison rather than infer it from timeouts. The
checkpoint pool's `get_stats()` (`pool_size`, `pool_available`, `requests_waiting`,
`requests_num`) and the SQLAlchemy pool's `checkedout()`/`overflow()`/`size()` are published
(the ORACLE/ADVISORY split is `requirements.md` §4.4's, P4 — **normative**)
in the `/readyz` `detail` block (§4) so the leak signal is visible without attaching to
PostgreSQL.

**Alternatives considered.**

| Option | Why rejected |
|---|---|
| Size the pool only, no contention fix | **Falsified by the lock.** `langgraph/checkpoint/postgres/aio.py:59`/`:374` — the probe queues behind the write storm on the saver's lock regardless of pool size. Pool size changes the *connection* wait; the lock wait is unaffected. |
| Give the probe a dedicated connection outside the saver's lock | The probe's raison d'être is to exercise the **real hot path** (`readiness.py:586-594`); a side connection would no longer prove the production path works. It also adds a second pool to poison. |
| Raise `max_size` to a large constant (e.g. 20) without a formula | Unjustified by any observation, and it hides the real problem behind brute force. A formula tied to the concurrency arithmetic is reviewable and re-derivable. |
| Size against `llm_concurrency=10` alone | Over-provisions: the effective concurrent-turn ceiling is `min(10, 5+2) = 7` (`daemon/constants.py:91`, `:103`). The brief's "size against 10" is superseded by the worker-pool arithmetic. |
| Raise the readiness probe budget instead | Budgets are module constants, not env-tunable (`readiness.py:45`/`:54`/`:59`); raising them delays genuine-failure detection and does nothing for the lock. |
| Leave the pool's 30s default | The ghost-waiter drain is a self-amplifying failure at a 10s probe cadence. It must be bounded explicitly. |
| *(original reasoning, superseded)* "A4 = create a pool-size knob, sized against `llm_concurrency=10`" | The correction is right on two counts: the *lock* (not the pool) is the contention point, and the effective writer ceiling is 7, not 10. The sizing formula is retained but re-derived and re-scoped, and the "sizing does not cure the leak" honesty statement is added. |

**Consequences.** A new `LimitsConfig` field pair (env + yaml + config field), a plumbing
change at `daemon/persistence.py:273-294` (both the saver pool and, if the asyncpg adapter
pool at `:313-314` shares the concern, the same knob), a `timeout` kwarg, and the probe's
explicit future cancellation. The change is **config-only in shape** (no schema change) ⇒
rollback-safe; the `LIMITS_*` env tier is already live for these keys (per the `config.yaml`
note on `llm_concurrency`).

**Reversal.** Set `LIMITS_CHECKPOINT_POOL_MAX_SIZE=5` and
`LIMITS_CHECKPOINT_POOL_TIMEOUT_S=30` and restart ⇒ byte-equivalent to today. A revert of the
code is equally safe. No migration, no table, no persisted state.

---

### 3e. (e) MCP warmup off the event loop

**Context — the smoking gun.** The whole `daemon/mcp/` warmup + session path contains **zero**
`asyncio.to_thread` / `run_in_executor` calls. Three specific loop-blocking or
unbounded-wait sites:

1. **Synchronous `/proc` reads on the event-loop thread, inside the MCP spawn path.**
   `daemon/mcp/stdio_wrapper.py:485` wires `on_ready=self._stamp_children`; `_stamp_children`
   (`:494-500`) performs synchronous filesystem reads — `read_bytes` (`:116`, `/proc/<pid>/cmdline`),
   `read_text` (`:132`, `/proc/<pid>/stat`) and `list(root.iterdir())` (`:179`, scanning
   `/proc`) — under a `threading.Lock`, **on the asyncio event-loop thread**. The `on_ready`
   hook fires inside the transport's background task (`stdio_wrapper.py:365+`), i.e. on the
   main loop. The scan is O(number of `/proc` entries) and is repeated for **every** spawned
   MCP child — and with the pool size of 1 (`daemon/config.py:2894`), the first instance takes
   the pooled connection and **subsequent instances fall through to the cold-start path, each
   spawning its own `npx` child** (`daemon/services/mcp_service.py:107-115`, cold connect at
   `:167` with `per_server_timeout=15.0`). That is the "×4 instances" multiplier.
2. **Unbounded transport handshake.** `daemon/mcp/stdio_wrapper.py:306` is
   `await self._ready.wait()` — **no timeout**. `TaskScopedStdioClient.__aenter__`
   (`:289-322`) awaits it, and the caller in the warmup pool does
   `read_stream, write_stream = await streams_cm.__aenter__()`
   (`daemon/mcp/warmup_pool.py:275`) — **outside** the `async with asyncio.timeout(60)` that
   only starts at `:278`. So the 60s warmup budget does **not** cover the handshake; a hung
   `npx` handshake can block indefinitely.
3. **Global serialisation of discovery.** A single global `_schema_cache_lock`
   (`daemon/services/mcp_service.py:203`) serialises discovery across **all servers and all
   instances**, plus per-instance `_preload_locks` (`:194-195`).
4. **The COLD-SPAWN secret-resolution seam (council W-5).**
   `daemon/mcp/connection_manager.py:229-237` calls `resolve_secret_markers(resolve_env(config.env), ...)`
   **inline on the event loop**, immediately before the `StdioServerParameters` construction
   (`:238-242`). This is the seam the incident's **×4** path actually goes through:
   with the pool size of 1 the first instance takes the pooled connection and every subsequent
   instance falls through to the **cold-start** path
   (`daemon/services/mcp_service.py:154-173` -> `connect_instance` at `:169-173` with
   `per_server_timeout=15.0`), and the cold path is what runs the inline secret resolution.
   Hoist the resolution onto a **dedicated `_SECRET_RESOLVE_EXECUTOR`** so the KMS/Fernet work
   never occupies the loop thread.
5. **The `is_available()` override bypass (council W-5).**
   `daemon/mcp/builtin_servers/plane.py:262-289` overrides `is_available()` and **never calls
   `super()`** (there is no `super()` anywhere in `plane.py`): it returns
   `bool(url) and bool(api_key)` after the kill-switch check (`:284-289`). A quarantined
   **plane** would therefore slip the gate entirely and keep being registered, pooled and
   warmed. The fix: `if not super().is_available(): return False` as the first statement of
   the override, so the quarantine registry (E5/W-4) is consulted for every builtin.

**Decision.**

**E1 — Move the `/proc` scan off the loop thread — work OFF-loop, await ON-loop (council
W-3; supersedes the first draft of this decision).** The `on_ready` hook today is a
*synchronous* callback (`Optional[Callable[[], None]]`, `daemon/mcp/stdio_wrapper.py:266`)
invoked from the transport's background task, and it runs **before** `_ready.set()`
(`stdio_wrapper.py:401-409`; the comment at `:393-398` states the intent explicitly: "the
stamp is guaranteed complete by the time the `__aenter__` waiter resumes"). That ordering is
**load-bearing and pinned**: `tests/test_resource_pid_registry.py:505-510` asserts that after
`await cm.__aenter__()` the registry already holds the child (`len(live) == 1`, the pid, the
instance id) — i.e. **registration MUST be complete before `__aenter__` returns**.

**The new shape (W-3).** Widen the hook type to
`Callable[[], Optional[Awaitable[None]]]`. The hook performs the `/proc` scan **OFF-loop**
(inside `asyncio.to_thread(...)`) and returns the awaitable; the transport **awaits the
returned awaitable BEFORE `_ready.set()`** — *work off-loop, await on-loop*. The scan therefore
never occupies the event-loop thread, and the "registration complete before `__aenter__`
returns" guarantee is **preserved** — the await happens on the loop, but it awaits a thread
that is doing the I/O.

**Fire-and-forget MUST NOT ship.** A variant that schedules the scan and does not await it
before `_ready.set()` is **UNSAFE** and is rejected on two independent grounds:
(i) it breaks the existing pin `tests/test_resource_pid_registry.py:505-510`; and
(ii) a scan that runs after the child has already exited returns `[]` — there is no reap
handle to register, so the child can never be reaped later. Both failure modes are silent
(the transport still reports ready), which is why the shape must be **awaited**, not merely
scheduled.

**E1a — The `on_ready` signature change is source-compatible for existing callers.** The
widened type accepts the current `Callable[[], None]` (returning `None` ⇒ nothing to
await) and the new `Callable[[], Awaitable[None]]`; `stdio_wrapper.py:401-409` grows an
`inspect.isawaitable(...)` check. A hook that raises is still swallowed (`:404-408`) — but a
hook whose awaitable **raises** must also be swallowed, and the transport must still
`_ready.set()`.

**E2 — Bound the handshake.** (a) Move `await streams_cm.__aenter__()` **inside** the
`asyncio.timeout(60)` in `daemon/mcp/warmup_pool.py` (i.e. the `async with` opens before the
call, not after it); and (b) give `stdio_wrapper._ready.wait()` a bounded wait so a hung
handshake cannot wedge the pool forever. The bound must be **less than** the 60s outer budget
so the outer budget stays the authority.

**E3 — No NEW starvation source.** Three rules:
- The off-loop work must be **bounded and rare**: one `/proc` scan per *spawned child*, not
  per tick and not per tool call. The scan is already per-spawn; moving it to a thread
  preserves that count exactly.
- The thread must not be used to *add* concurrency pressure: the scan is a **single**
  `to_thread` per spawn, and the default executor is bounded.
- The DNS pre-check (decision (f)) must **also** run off the loop (a `getaddrinfo` blocks) —
  and it must not be run on the loop as a "cheap synchronous check" shortcut.

**E3b — The loop-lag ACCEPTANCE CRITERION (council BLOCKING 2).** The **threshold, the
measurement method and the pin are §4.6's** → this record does not restate them.

**The design position (reasoning, not a rule):** moving work off the loop is only a
*hypothesis* until the loop's health is **measured under the incident's own load**. A pin
with no number is not a pin, so the AC carries both a percentile and an absolute ceiling, and
the measurement is taken **while the burst is running** — an idle-loop measurement would
trivially pass and would prove nothing about the incident.

> Normative source: requirements.md §4.6 (P6 — acceptance-criterion thresholds; the
> event-loop lag bound and its measurement).

**E4 — The circuit breaker: reuse the ONE primitive, but NOT the `ResilienceManager`
wrapper.** (Audit trail: the brief's premise of "two implementations" is **falsified** — there
is exactly one `CircuitBreaker` class, `daemon/sources/circuit_breaker.py:20`, and
`daemon/mcp/resilience.py:45` **imports** it. The `ResilienceManager` at
`daemon/mcp/resilience.py:470` is a per-server-keyed registry around that primitive.)

We evaluate **both** against the requirement *"disable/quarantine THIS server FOR THIS
BOOT"*:

| Candidate | Verdict |
|---|---|
| `daemon/mcp/resilience.py::ResilienceManager` (`:470-630`) | **Wrong layer.** `register()` is called from exactly one place (`daemon/services/mcp_service.py:1283`), itself reached only from `preload_mcp_tools` (`:875`). A server with no `ResilienceConfig` **never gets a breaker allocated** — context7 registers none (`mcp_service.py:852-854`, `base.py:104-123`). The manager's breaker guards **tool calls** (`daemon/mcp/resilience.py:13-18` → `tool_adapter._lazy_coroutine`), and `daemon/mcp/warmup_pool.py` contains **zero** resilience or circuit references — the **init path is entirely outside the machinery**. Using it would require giving context7 a `ResilienceConfig`, which would *also* wire tool-call caching/retry semantics that context7 does not want. |
| `daemon/sources/circuit_breaker.py::CircuitBreaker` (`:20-124`) | **Right primitive.** It is the single implementation, already imported by the MCP layer, and it already has the CR-4 single-probe-in-flight guard (`:32-38`, `:40-76`) that prevents the thundering-herd re-trip we would otherwise reinvent. It is server-agnostic — it has no opinion about tool calls vs. init. |

**Chosen:** reuse the **`daemon/sources/circuit_breaker.py:20` primitive**, placed in a **new
init-path guard** that wraps the *spawn/handshake* (not the tool call). **Caveat that must be
honoured:** `reset()` (`:114-124`) is the only **unsynchronised** mutator — the init guard must
use the lock-protected `record_failure` / `record_success` (`:78-108`) and must not call
`reset()` concurrently.

**E5 — "Disable for this boot" has no existing concept, so we create exactly one, in the
natural home.** The three closest existing candidates:

| Candidate | Assessment |
|---|---|
| `MCP_DISABLE_BUILT_IN_{NAME}` (`daemon/mcp/builtin_servers/__init__.py:15-28`) | Boot-scoped in *intent* but **persistent** in effect: the disable path deactivates the DB row (`daemon/manager.py:2798-2800` → `_bootstrap_builtin_servers`, `:2764`), and the row is reactivated on a later boot if the env var is removed. Not a per-boot quarantine. |
| `mcp_servers.is_active = False` | Persistent, operator-visible, but **not** per-boot: it survives a restart and requires a DB write. Using it for a transient init failure would leave the server disabled after the cause is gone. |
| **`definition.is_available()`** (`daemon/mcp/builtin_servers/base.py:125-152`) | **The natural home.** It is a **per-boot, in-memory** predicate already consulted at **four** sites: `daemon/manager.py:2813` (bootstrap — the server is skipped, no DB row), `daemon/manager.py:3057` (warmup-pool registration — the server is never pooled), `daemon/services/mcp_service.py:579` (warm path) and `:1130` (availability check). A quarantined server therefore disappears from **every** path: no row, no pool registration, no warm, no cold discovery. Today `required_package is None ⇒ True` (`:143`) means context7 is **always** "available" — that is the gap. |

**Chosen (W-4):** a **single leaf module** `daemon/mcp/init_guard.py`, which imports **ONLY**
`daemon.sources.circuit_breaker` + the standard library (no `daemon.mcp`, no `daemon.services`,
no `daemon.config` — it must be importable from anywhere without creating an import cycle).
`daemon/mcp/builtin_servers/base.py` imports `init_guard` and consults it inside
`is_available()`. The registry entry carries
`{server, reason, since, attempts, last_error_class}` and is what the observability surface
(§3h) renders. This gives a **real** "disabled for this boot" concept, reusing the
existing four-site predicate rather than adding a parallel one.

> **SUPERSEDED (council W-4):** the first draft of this decision placed the registry at
> module level in `daemon/mcp/builtin_servers/__init__.py`. That is withdrawn: `__init__.py` is
> the **registration** module (it imports webfetch, context7 and plane at `:74-81`), so a
> quarantine registry there is reachable only through the builtin registry — it is the wrong
> home for a state machine that also has to be importable from the transport layer. The
> **single leaf module** `daemon/mcp/init_guard.py` is the one home.

**W-4a — `init_guard.py` MUST expose `reset_for_tests()` and it MUST be pinned.** The
registry is per-process state; without an explicit reset, a test that quarantines a server
leaks that state into every later test in the same process — a false-negative quarantine (or a
false-positive availability) in an unrelated test. `reset_for_tests()` clears the registry and
the per-server breakers, and is **pinned** by a test that quarantines a server, asserts the
gate closes, calls `reset_for_tests()`, and asserts the gate re-opens. `reset_for_tests()` is
test-only API; production code never calls it.

**W-4b — `CircuitBreaker.reset()` is NEVER called by `init_guard.py` (council S11).**
`daemon/sources/circuit_breaker.py:114-124` is the **only unsynchronised mutator** on the
breaker (it writes `state`, `failure_count` and `_probe_in_flight` without taking `_lock`,
unlike `record_success`/`record_failure` at `:78-108`). Calling it concurrently with a
half-open probe is a data race. `init_guard.py` therefore uses **only** the lock-protected
`record_failure` / `record_success`, and its own reset path **replaces the breaker object**
(or re-registers it) rather than mutating it in place. This is **documented in
`init_guard.py`'s module docstring** and **pinned** by a test asserting that `reset()` is never
called on the path (e.g. by monkeypatching `CircuitBreaker.reset` to raise).

**E6 — The init breaker drives the quarantine.** A server whose init fails N times
(default 3) within the boot opens the breaker (via `record_failure`) and the guard flips the
server into the per-boot quarantine registry. The recovery path: `record_success` closes the
breaker; a **manual** reset path mirrors `ResilienceManager.reset_circuit`
(`daemon/mcp/resilience.py:616-626`) but calls the lock-protected path, not `reset()`.

**Alternatives considered.**

| Option | Why rejected |
|---|---|
| Only move the DNS resolution off the loop | The `/proc` scan is the *verified* loop-blocking site (`stdio_wrapper.py:485`, `:494`, `:116`/`:132`/`:179`); DNS is inferred, not proven. Both must go; the `/proc` scan is the named one. |
| *(original reasoning, superseded)* "The hook becomes a thin scheduler that hands `self._stamp_children` to `asyncio.to_thread(...)` and does not await it on the critical path; the child registration becomes **eventually consistent**" | **SUPERSEDED by council W-3 (UNSAFE).** Two independent defects: (i) it breaks the existing pin `tests/test_resource_pid_registry.py:505-510`, which requires the registration to be **complete before `__aenter__` returns**; and (ii) a scan that runs after the child has already exited returns `[]` -- there is no reap handle to register, so the child can never be reaped. Both failures are silent. The adopted shape is **work off-loop, await on-loop**: widen `on_ready` to `Callable[[], Optional[Awaitable[None]]]`, run the scan in a thread, and have the transport await the returned awaitable **before** `_ready.set()`. |
| *(original reasoning, superseded)* "a **per-boot quarantine registry** in `daemon/mcp/builtin_servers/__init__.py` (module-level ⇒ per-process ⇒ per-boot), consulted by `is_available()`" | **SUPERSEDED by council W-4.** `__init__.py` is the *registration* module (it imports webfetch, context7 and plane at `:74-81`), so it is the wrong home for a state machine that must also be importable from the transport layer. The **single leaf module `daemon/mcp/init_guard.py`** (importing ONLY `daemon.sources.circuit_breaker` + stdlib) is the one home; `base.py` imports it. The registry must also expose a pinned `reset_for_tests()` (W-4a) and must **never** call the unsynchronised `CircuitBreaker.reset()` (W-4b / S11). |
| Make the whole MCP warmup run in a thread | The warmup is `async` end-to-end and shares the manager's task graph; a thread-ified warmup would need its own loop and its own lifecycle. The targeted fix (one thread hop for the scan; one timeout moved) is smaller and provable. |
| Rely on the child process's CPU cost being the problem | The child burns 88–156% CPU, but that is a **child** cost; the daemon's own 105–114% is the loop starving. The fix must target the loop. |
| Use the `ResilienceManager` + a context7 `ResilienceConfig` | Wrong layer (E4 table): it guards tool calls, never allocates a breaker for a server without a config, and `warmup_pool.py` has zero resilience references. It would also silently enable caching/retry for context7 tool calls. |
| Add a **new** circuit-breaker class | There is already one, and it already has the CR-4 probe guard. A second implementation is exactly the drift the codebase's own lineage warns about. |
| Persist the quarantine in the DB (`is_active=False`) | Not per-boot; it survives the cause and requires a DB write from a hot path. It also conflates "operator disabled" with "this boot failed". |
| Reuse `MCP_DISABLE_BUILT_IN_*` | It deactivates the row persistently (`manager.py:2798-2800`) — an operator-facing switch, not a per-boot quarantine. It also requires an env change, which cannot be done from inside the daemon. |
| *(original reasoning, superseded)* "Evaluate whether `daemon/mcp/resilience.py`'s CircuitBreaker/ResilienceManager can be reused rather than a new breaker being invented" | The correction resolved the ambiguity: there is **one** implementation and `resilience.py` imports it, so the real question is *which layer* hosts the guard. Answered in E4/E5: the **primitive** is reused; the **`ResilienceManager` wrapper** is the wrong home; and `is_available()` is the natural home for the per-boot quarantine. |

**Consequences.** `daemon/mcp/stdio_wrapper.py` gains the widened `on_ready` type and
the await-before-`_ready.set()` sequence (W-3) plus a bounded wait; `daemon/mcp/warmup_pool.py:275-278`
gains the corrected timeout scope; **`daemon/mcp/init_guard.py` is created** as the single leaf
home for the quarantine registry and the init breaker (W-4), and
`daemon/mcp/builtin_servers/base.py:125-152` imports it inside `is_available()`;
`daemon/mcp/builtin_servers/plane.py:262-289` gains the `super().is_available()` guard so the
quarantine is consulted for **plane** too (W-5); and
`daemon/mcp/connection_manager.py:229-237` gains the `_SECRET_RESOLVE_EXECUTOR` hoist for the
cold-spawn path (W-5). The MCP **tool-call** path is untouched.

**Reversal.** The quarantine registry is in-memory and per-boot**Reversal.** The quarantine registry is in-memory and per-boot ⇒ a restart clears it. The
thread hop and the timeout change are local to two functions. No schema change. The breaker
state is in-memory ⇒ a restart clears it. Fully revertible.

---

### 3f. (f) DNS pre-check design (C2)

**Context.** The context7 package's base was globally unresolvable; every init hung to a 30s
timeout, retried 3×, and repeated forever. The desired behaviour is to **fail fast** on a
dead API host instead of hanging to the timeout.

**Decision.**

**F1 — The pre-check is a PROBE, not a pin.** It resolves the host once, records the outcome,
and then **discards the address**. The child still performs its own normal resolution. This is
the hard constraint: **the pre-check must never pin a stale IP** — a pinned address would
survive a host move and convert a fail-fast check into a silent wrong-endpoint connection.

**F2 — The pre-check runs against the RESOLVED base the child would actually use.** Given
premise 8 (the `.env` does **not** reach the child), the base that matters is the one that
flows through **schema → row `config.env` → `build_pooled_stdio_config` → child env**. The
pre-check therefore reads the *same* merged env the spawn will use — i.e. the row's
`config.env` overlaid on the definition's `build_config({})["env"]`
(`daemon/mcp/warmup_pool.py:852-897`, whose PRECEDENCE is pinned there: **row env wins on
every key**). A pre-check against a value the child will not use is worthless.

**F3 — The pre-check must not block boot on a resolver hiccup.** It is:
- **bounded** (default 2s);
- **off the event loop** (a `getaddrinfo` blocks — it runs in a thread; see E3);
- **non-fatal**: a pre-check failure is a **quarantine signal** (E5/E6), not a boot failure;
- **per-attempt**, not once-per-boot: a host that moves *after* boot must still be caught, and
  the breaker counts failures across attempts anyway.

**F4 — Failure classification.** The **timeout, the classification of resolution failure
vs. resolver timeout, and the off-loop requirement are §4.5's** → this record does not
restate them.

**The design position (reasoning, not a rule):** a resolution failure is a **definitive**
signal (the name does not exist) and is therefore quarantine-eligible; a resolver **timeout**
is not — it says nothing about the host, and a resolver hiccup must never permanently
disable a server. The two are therefore **distinct classes**, and only the first may arm the
quarantine on its first occurrence.

> Normative source: requirements.md §4.5 (P5 — DNS-refusal thresholds and
> classification).

**Alternatives considered.**

| Option | Why rejected |
|---|---|
| Pin the resolved IP into the child's env | Violates F1 — pins a stale address across a host move. |
| Pre-check at boot only | Misses a host that moves after boot; the incident's failure was *repeated* forever, which only a per-attempt check breaks. |
| Make the pre-check a boot-fatal | A resolver hiccup at boot would prevent the daemon from starting — unacceptable. |
| Pre-check on the event loop (a "cheap" synchronous `getaddrinfo`) | `getaddrinfo` blocks the loop; a slow resolver would stall the daemon for the whole check duration — the very starvation we are fixing. |
| Pre-check a hardcoded `api.context7.com` | The host moves. The check must use the *configured* base (F2), or it validates a host the child will not use. |
| Increase the child's own timeout instead | The timeout is the symptom, not the fault: a 30s hang × 3 retries × every 2–4 minutes forever is the storm. Failing fast + quarantining is the fix. |

**Consequences.** One new pre-check helper, invoked from the init path, using the same merged
env as the spawn, running in a thread with a 2s bound, feeding the breaker and the quarantine
registry. The pre-check is **per attempt**, so it adds one DNS lookup per init attempt — the
cost is bounded by the breaker's failure threshold (3), after which the server is quarantined
and no further attempts are made.

**Reversal.** The pre-check is a single helper call; removing it restores today's behaviour.
The registry/breaker remain valid (they would simply never trip on resolution failures).

---

### 3g. (g) Giving context7 a settable base URL without breaking "protected built-in"

**Context.** The context7 built-in's `get_config_schema()` returns `[]`
(`daemon/mcp/builtin_servers/context7.py:41-46`), so the FE renders "No configuration fields
available" and the sanctioned write path
(`POST /api/mcp-servers/configure-builtin`, `daemon/routers/mcp_servers.py:491-536`) has
nothing to set. Meanwhile the built-in is **protected** from direct config mutation:
`daemon/routers/mcp_servers.py:750-767` refuses a config change on a built-in with **403
`BUILTIN_SERVER_PROTECTED`** ("Use /configure-builtin or /reset-builtin endpoints instead").
The two are not in conflict: "protected" means *the generic config field is immutable*, and
`/configure-builtin` is the sanctioned way to set a built-in's **schema-declared** fields.
context7 simply has no schema-declared fields.

**Decision.**

**G1 — Add a `config_schema` to context7, with the derived-URL semantics expressed
explicitly — TWO fields, not one.** A single "base URL" field cannot express the package's
derivation, so the template exposes:

| key | section | type | default | meaning |
|---|---|---|---|---|
| `CONTEXT7_BASE_URL` | `env` | text | `https://context7.com` | the **daemon-side mirror** of the site root (see the INERTNESS note below) |
| `CONTEXT7_API_URL` | `env` | text | *(none)* | **the only effective override**; unset ⇒ the package falls back to its own hardcoded base |

> **PACKAGE-SOURCE CORRECTION (council N-d; verified against the pinned package).** The
> derivation is **not** `process.env.CONTEXT7_API_URL || ${CONTEXT7_BASE_URL}/api` with
> `CONTEXT7_BASE_URL` env-read. In `@upstash/context7-mcp` **v4.4.4**,
> `dist/lib/constants.js:7-10` reads:
>
> ```js
> const CONTEXT7_BASE_URL = "https://context7.com";                                        // :7
> export const CONTEXT7_API_BASE_URL = process.env.CONTEXT7_API_URL || `${CONTEXT7_BASE_URL}/api`;  // :10
> ```
>
> `CONTEXT7_BASE_URL` is a **hardcoded const in the package, NOT read from env**. Only
> `CONTEXT7_API_URL` is an effective lever. The init-time fetch that hangs on NXDOMAIN is
> `dist/lib/jwt.js:39` — `fetch(`${CONTEXT7_API_BASE_URL}/v2/entra/config/${encodeURIComponent(audience)}`)`
> — which runs on the derived base. **Consequence for the template:** the `CONTEXT7_BASE_URL`
> field is a **daemon-side mirror** (operator documentation + the C2 derivation input), **not**
> a package lever.
>
> **INERT-LEVER HAZARD (council N-d) + REMEDY.** The `CONTEXT7_BASE_URL` field is **inert to
> the package** and will **stay inert** if (a) the package's derivation changes, or (b) the
> package's hardcoded base moves and our template default goes stale. An operator who sets
> `CONTEXT7_BASE_URL` believing it moves the package will be **silently wrong** and the
> wedge will recur. The hazard must therefore be stated **in the field description itself**
> ("daemon-side mirror; inert to the package ~ only `CONTEXT7_API_URL` is effective"), and the
> **remedy** is the **schema-version bump**: any change to the package's derivation or to the
> effective base is a `schema_version` change, which forces the 409-then-boot-heal cycle
> (G5) and re-surfaces the field description to the operator. A package base change that is
> NOT accompanied by a schema bump is exactly the silent-wedge class this lane exists to
> prevent.

`CONTEXT7_API_URL` deliberately has **no default**: supplying one would pin the API path at
template time, which is the thing that broke, and it would defeat the C2 fail-fast path (the
pre-check needs an *unset* value to be distinguishable from a *wrong* one). The
**default on `CONTEXT7_BASE_URL`** keeps the daemon-side mirror correct out of the box.
**G2 — The mechanism is already in the code; we use it, we do not build it.**
`daemon/mcp/builtin_servers/base.py:172-244` `build_config` resolves each schema field
(user value > field default), and for `section == "env"` sets `env[key.upper()] = str(value)`
(`:222-223`). The built config's `env` is then merged into the row's `config.env` by
`build_pooled_stdio_config` (`daemon/mcp/warmup_pool.py:852-897`) — **row env wins on every
key** — and handed to the child at `daemon/mcp/connection_manager.py:229-242` via
`resolve_env` → `resolve_secret_markers` → `StdioServerParameters(env=...)`. The SDK merges
that with `get_default_environment()` (`mcp/client/stdio/__init__.py:127`, allow-list at
`:28-45`) — so the schema-supplied env **does** reach the child, unlike the daemon's ambient
environment (premise 8).

**G3 — The precedent shape.** `daemon/mcp/builtin_servers/webfetch.py:58-91` is the
worked precedent: a non-empty `get_config_schema()` with `section: "args"`, `arg_format:
"key_value" | "flag"`, `default`, `required: False`, plus a `build_config` override that
**validates** the value and raises `ValueError` (`:40-56`). `plane.py:347-354` and
`context7.py:41-46` both return `[]` — so context7 and plane are the two that lack it.
`ConfigSchemaField` (`daemon/models/mcp_server.py:57-71`) already supports
`section: Literal["args", "env"]` — the `env` section is a **first-class, already-modelled**
option; only context7 has no field that uses it.

**G4 — A `build_config` override must VALIDATE the base URL.** Following the webfetch
precedent, context7 gains a `build_config` override that rejects a non-`http(s)` scheme and
rejects embedded userinfo. The router already translates a `ValueError`/`McpConfigValidationError`
into a **422** (`daemon/routers/mcp_servers.py:526-549`) — so the failure mode is a clean
422, not a 500.

**G5 — The schema version MUST be bumped, and the bump has a consequence.** context7's
`schema_version` is `"2"` (`context7.py:29-31`). Adding fields is a schema change ⇒ bump to
`"3"`. The consequence: the `/configure-builtin` route **refuses** to configure across a
schema drift (**409**, `daemon/routers/mcp_servers.py:576-592`), while the **boot** path
heals it (`daemon/manager.py:2854-2866`: "If schema version differs → update config (preserve
`is_active`)"). So a live row stamped `"2"` will be refreshed at the next boot, and an
operator trying to configure it **before** the boot gets a 409 with an actionable message. This
must be **disclosed** in the operator notes and covered by a pin.

> **BLOCKING 3 — the heal MUST be ENV-PRESERVING, not a whole-config replace.** The boot
> heal is implemented as a **whole-config replace**: `daemon/manager.py:2829` computes
> `default_config = definition.build_config({})` and `daemon/manager.py:2855-2861` writes it
> wholesale (`update_mcp_server(existing.id, config=default_config, ...)`), logging "config
> reset to defaults due to schema version change" (`:2862-2867`). The codebase **documents
> this exact hazard** at `daemon/mcp/warmup_pool.py:867-876`: "row `config.env` wins over
> `definition.build_config({})` env on every key ... A stale definition would silently drop
> those keys."
>
> **Consequence if left as-is:** the operator-set env is **DROPPED on the first boot after the
> upgrade** — the pinned `CONTEXT7_API_URL` and any KMS-marker keys. C3's entire purpose is to
> make the base URL settable and durable; a heal that deletes the setting on the first boot
> after the upgrade defeats it silently. Worse, **C2's DNS pre-check would then validate the
> DEFAULT host, not the configured one** — the pre-check would pass against a host the child
> never uses, and the real failure mode would be re-introduced one layer down.
>
> **DECISION (adjudicated): ENV-PRESERVING MERGE in the heal.** The heal must **preserve the
> operator-set env keys and the KMS-marker keys** across the schema-version change; **only the
> template-derived defaults are rebuilt**. Concretely: the new config is
> `definition.build_config({})` with the row's existing `config.env` **merged over it** (row
> env wins, the same precedence `warmup_pool.py:867-869` already pins), and any key the row
> carries that the new schema does not declare is **retained** (not dropped) so a schema
> narrowing cannot silently discard a KMS marker.
>
> **REJECTED alternative — disclosed-discard (keep the whole-config replace and DISCLOSE that
> the operator pin is lost on upgrade).** Rejected because it defeats C3's stated purpose: the
> operator's pin would be silently deleted on the first post-upgrade boot, the operator would
> have to re-apply it every upgrade, and the failure mode is **silent** (no error, no log
> beyond the "reset to defaults" line). Disclosure is necessary but not sufficient — the
> correct engineering answer is to preserve the value.
>
> **C2's derivation MUST read the EFFECTIVE post-heal env** — i.e. the merged
> `definition.build_config({})` env overlaid with the row's surviving `config.env` (the same
> merge `build_pooled_stdio_config` performs, `warmup_pool.py:852-897`), **not** the
> pre-heal row and **not** the bare template defaults.
>
> **PIN (BLOCKING 3):** `config.env` **survives the 2 ⇒ 3 heal with the operator value
> intact** — a test writes a marker-bearing operator env onto the row, drives the heal, and
> asserts the post-heal `config.env` still contains the operator's key with its value, that
> the KMS-marker key is still present, and that the template-derived defaults were added
> alongside (not instead of) them.
**G6 — The `mcp_set_env` hole is DECIDED: left open, documented, and routed elsewhere.** The
`mcp_set_env` tool (`daemon/tools/infra.py:1527-1540`) writes `mcp_servers.config.env` with
**no `is_builtin` guard** — unlike the HTTP API. Verified: no `is_builtin` reference exists in
the tool body. Consequences:
- It is the most likely route the operator's 21:40 remediation actually took (premise 8), and
  it is the same channel the BYOK self-provisioning chain uses (`kms_attach`).
- Closing it **in this lane** would break a working operator remediation and the
  self-provisioning chain for no benefit to this commission.
- Leaving it open does **not** undercut C3's intent, because **C3's outcome is that the base
  URL is settable via the protected, schema-backed path** — which G1–G4 deliver. The tool's
  existence is an additional, unsanctioned route to the same value.

**Decision: `mcp_set_env` keeps its current behaviour in this lane; the missing built-in
guard is recorded as a separate defect with an owner** (it is a tool-surface consistency
issue, not a blocker for C3). **SUPERSESSION STAMP (iteration 003, N-A4):** `mcp_set_env` is
**NOT closed in this lane** — it is a **routed defect** with a named owner (see the record
below). Any requirement text saying "close it" is superseded by this record.

> **Routed-defect record (council S5).** The record MUST carry both of the following, so the
> routing is not mistaken for a closure:
>
> - **Owner: agent-tool-surface consistency lane** (`daemon/tools/infra.py` follow-up, the
>   same lane as the BYOK self-provisioning chain audit). The owner is the tool-surface
>   consistency lane, **not** this lane and **not** the MCP lane — the tool is a shared BYOK
>   surface and its audit belongs with the chain that depends on it.
> - **Residual risk.**> - **Residual risk.** Until the tool is closed, an operator (or any agent holding the `infra`
>   tool category) can **bypass the protected template path** and set the built-in's env
>   directly → including the context7 base URL → via `mcp_set_env`. The value is
>   therefore settable, but **not exclusively** through the schema-backed, validated route that
>   C3 establishes. The "protected built-in" property is **strengthened on the sanctioned path
>   but not enforced on the tool path**; the two must not be conflated.
> - **Closure shape (when the leader schedules it).** An **operator-pinned key allow-list** for
>   built-ins — **never a blanket ban**. A blanket ban would break the BYOK self-provisioning
>   chain (`kms_attach` writes the same `config.env` on the same row) and would remove the
>   operator's only working remediation route for a wedged base URL. The allow-list shape keeps
>   secret-shaped and marker-shaped keys on the existing policy path
>   (`daemon/services/env_key_policy.py`) and constrains only the *operator-pinned* keys.

**Alternatives considered.**

| Option | Why rejected |
|---|---|
| Make the built-in's `config` mutable via the generic PUT | Directly contradicts the "protected built-in" property (`mcp_servers.py:750-767`); it would make every built-in's transport and args operator-editable. |
| Add the base URL to `get_base_config()` only (no schema) | `get_base_config` is the *definition's* static default; there would be **no way for an operator to override it** — the "unsettable" problem would persist, just with a different value baked in. A schema field is what makes it settable *and* overridable. |
| One field (`CONTEXT7_API_URL`) only | The package's derivation is `CONTEXT7_API_URL || ${CONTEXT7_BASE_URL}/api` — with only the API var set, a future operator who changes the *site* has no lever; and the RCA's working fix set **both**. Two fields express the derivation honestly. |
| Two fields, **both** with defaults | Pinning `CONTEXT7_API_URL` at template time makes the `${CONTEXT7_BASE_URL}/api` derivation dead code and re-freezes the exact thing that broke. One default (the root) + one unset (the derived API) is the honest shape. |
| Use a CLI arg (`--api-base-url`) instead of env | `section: "args"` is supported (`base.py:224-231`), but the upstream flag name is not verifiable from this repo, and an env var is the channel the package's own derivation reads. Env is the correct seam. |
| Close the `mcp_set_env` built-in hole in this lane | Breaks the operator's remediation route and the BYOK chain; not needed for C3's stated intent once the schema path works. Routed as a separate defect. |
| *(original reasoning, superseded)* "Find a precedented non-empty `config_schema` shape and where 'protected' is enforced" | Resolved: the precedent is `webfetch.py:58-91`; "protected" is enforced at `mcp_servers.py:750-767` (generic PUT) and the sanctioned path is `/configure-builtin` (`:491-536`). The correction additionally surfaced the **two-variable derivation** (which the original reasoning had collapsed to one) and the **`mcp_set_env` hole** (which the original reasoning had not ruled on). Both are now decided above. |

**Consequences.** context7's definition gains a schema (two fields), a `build_config`
validation override, and a `schema_version` bump (`"2"` → `"3"`). The `env` section of
`ConfigSchemaField` — already modelled at `daemon/models/mcp_server.py:69` — becomes used for
the first time. The FE's "No configuration fields available" is replaced by a real form with
no FE change required (the template list is served from
`definition.get_config_schema()` at `daemon/routers/mcp_servers.py:486`).

**Reversal.** The schema change is additive; reverting it restores `[]` and the `env` key
disappears from the built config. **The one irreversible-ish step is the schema-version bump**
— a row stamped `"3"` will be refused by `/configure-builtin` if the definition reverts to
`"2"` (409, `mcp_servers.py:576-592`), until the next boot heals it. This is a **disclosed,
bounded** rollback cost, not a silent one.

---

### 3h. (h) Observability surface for degraded MCP servers

**Context.** During the incident a server was wedged for roughly 40 minutes with no operator
signal. The `daemon/tools/upgrade_tools.py:2089-2091` reader illustrates the general problem: it
reads a **top-level** `reasons` key from the readyz payload while the payload nests `reasons`
under `detail` — so it renders `reasons=[]` even when the daemon is reporting reasons.

**Decision.**

**H1 — Which servers.** All built-in MCP servers (context7, webfetch, plane), reported
per-server. The context7 class is the one that bit us, but the surface must be **generic** —
the class of failure is "a built-in MCP server could not initialise", and every built-in can
hit it.

**H2 — Why it must exist.** Three independent consumers need it, and none of them can get it
today: the **gate** (failure-class gating, CONSTRAINT 1), the **operator** (which server is
degraded, since when, and why), and the **next boot** (a per-boot quarantine must be
inspectable, not invisible).

**H3 — Where the state lives.** In-memory, per-boot, in the **quarantine registry** (§3e E5) —
which already holds `{server, reason, since, attempts, last_error_class}` — plus a **journal
line** (the daemon journal, via the existing `journal_history_append` shape in
`daemon/tools/upgrade_journal.py`) so the state is durable in the log and survives a restart
inspection. **No new SQLModel table** — and therefore **no new `daemon/migrations/versions/*.sql`
file** (the house rule: every new table needs its own migration file; we avoid needing one).

**H3a — BLOCKING 4 — the daemon-side journal write is SUPPRESSED while a promote txn is live.**
The C4 state-transition journal write is a **daemon-side write to the promote-owned
`releases/state.json`**, and the write path is **unlocked**: `daemon/tools/upgrade_journal.py:331-371`
(`journal_write`) performs a read ⇒ mutate ⇒ **whole-document write**, and its own
comment at `:352-353` states the discipline ("P2.3 caller discipline still serializes journal
writes externally; this is the inner ring") — the serialisation is the CALLER's job, and the
daemon-side C4 writer is not a ceremony participant.

**The failure sequence (must be prevented):** MCP warmup runs at daemon boot; a promote
**restarts the daemon before the gates**; an MCP init failure fires the C4 state transition while
`in_flight.kind == "promote"` is live; the daemon-side write takes a **stale snapshot** of the
document and writes it back whole, **DROPPING `in_flight`**; the A3 hold predicate then reads
`in_flight` as absent, the predicate flips false, and the deferred resumes are **RELEASED
MID-PROMOTE** — the exact burst A3 exists to suppress.

**DECISION (adjudicated): SUPPRESSION.** Any daemon-side journal write is **SUPPRESSED while
`promote_txn_live()` is true**, with these precise rules:

- **The writer is the C4 / `init_guard` state-transition journal emit** -- the *only*
  daemon-side writer this lane introduces. The suppression gate lives at that writer, not in
  `journal_write` itself (the generic journal writer serves ceremony-owned flows too, and
  gating it there would be a much wider blast radius).
- **A suppressed write is BUFFERED in-memory**, bounded: **one entry per `server` + `state`**,
  **newest wins** (a repeated transition to the same state supersedes the buffered one; the
  buffer cannot grow without bound).
- **The buffer is FLUSHED** to the journal when the predicate flips **false** (the promote txn
  closes) **or at graceful shutdown** (the ordered-shutdown path).
- **A buffered entry is NEVER dropped.** If the daemon dies before the flush, the entry is
  lost with the process -- which is why the flush is registered on the ordered-shutdown path
  and why the state-transition is ALSO visible in the in-memory `detail.mcp` surface
  (§3h H4) for the whole boot. Nothing is *silently* discarded.
- **REJECTED alternative — lock-acquire (the daemon-side writer takes the ceremony's journal
  lock before writing).** Rejected: `journal_lock` belongs to the **shell ceremony**. A
  daemon-side acquire mid-promote is a **new cross-process contention point** and risks
  **ceremony interference** — it could block the ceremony's own writer, or hold the lock
  across the ceremony's commit path. The MCP state transition is **deferrable observability,
  not correctness**, so deferring it is strictly cheaper than contending for the ceremony's
  lock.

**PIN (BLOCKING 4):** a daemon-side MCP state transition during a live promote txn must **NOT**
drop `in_flight` — a test drives a state transition while `promote_txn_live()` is true,
asserts the journal's `in_flight` is **byte-identical** before and after, asserts the entry is
**buffered** (one per server+state, newest wins), and asserts the buffer is **flushed** to the
journal once the predicate flips false (and, separately, on graceful shutdown).
**H4 — The surface.** An additive `detail.mcp` map in the `/readyz` payload, per server:

```
detail.mcp.<server> = {
  state:            "ok" | "degraded" | "quarantined",
  reason:           "<machine class>" | null,     # e.g. pool_leak, dns_nxdomain, init_timeout
  since:            "<ISO>" | null,               # when it entered the current state
  init_attempts:    <int>,                         # attempts this boot
  last_error_class: "<class>" | null,
  breaker_state:    "closed" | "open" | "half_open" | null,
  recoverable:      true | false                   # per the class table
}
```

`recoverable` is the direct link to the failure-class table (§4.2's): it is the machine
answer to "may the gate wait for this?" — its **mapping** is §4.2's, and this record does
not restate it.

**H5 — Since when.** The state is published from the **first** init attempt of the boot (not
only from the failure), so a server that is merely "not yet initialised" is distinguishable
from one that is "broken". A `state: "degraded"` entry appears on the first failed attempt;
`"quarantined"` when the breaker opens.

**H6 — Recoverable how.** (a) **Per-boot**: a restart re-evaluates availability
(`is_available()` → the registry is empty again). (b) **Within a boot**: a successful init
calls `record_success` → the breaker closes → `state` returns to `"ok"`. (c) **Manual**: a
reset path mirroring `ResilienceManager.reset_circuit` (`daemon/mcp/resilience.py:616-626`),
but implemented through the lock-protected path, **not** `CircuitBreaker.reset()`
(`daemon/sources/circuit_breaker.py:114-124` — the only unsynchronised mutator). (d)
**Operator opt-out**: `MCP_DISABLE_BUILT_IN_{NAME}` for a hard disable.

**H7 — The `upgrade_tools.py` stale-reader defect is FIXED in this lane.** The reader at
`daemon/tools/upgrade_tools.py:2089-2091` must read `detail.reasons` (with a
top-level-`reasons` fallback for older daemons). Rationale for including it here: the
commission's whole thesis is **"a degraded daemon must stay visible"**, and a consumer that
renders `reasons=[]` for a degraded daemon directly contradicts that thesis. It is a one-line
reader fix, and the same payload shape (with the new `grace` and `mcp` blocks) is what makes
it correct.

**Alternatives considered.**

| Option | Why rejected |
|---|---|
| A new SQLModel table for MCP server health | Every new table needs its own `daemon/migrations/versions/*.sql` file and a full migration cycle; the state is **per-boot** and the journal already provides durable history. |
| Log-only (no `/readyz` surface) | The **gate** must classify the failure class programmatically — a log line is not machine-readable by the gate, and the operator's signal would depend on log scraping. The log is kept **in addition**. |
| Surface only degraded servers | "Only when broken" makes the absence of a field ambiguous (not-yet-initialised vs. fine). Publishing every built-in makes the state machine legible. |
| Fix the `upgrade_tools.py` reader in a separate lane | The commission's thesis is visibility; leaving a known-broken reasons reader in place would undercut it for no reason — the fix is one line and carries a test. |
| *(original reasoning, superseded)* "Observability surface for degraded MCP servers (which servers, why, since when, recoverable how)" | Kept as the decision's shape. The correction added the `upgrade_tools.py:2089-2091` stale-reader defect and the "no new table" constraint, both now folded in. |

**Consequences.** An additive `detail.mcp` map; a journal line per state transition (not per
attempt — per-attempt logging is the noise that made this incident hard to read); a reset
path; and a one-line reader fix in `upgrade_tools.py`. Consumers that do not know the key are
unaffected (additive-detail precedent, `readiness.py:198-205`).

**Reversal.** Additive fields + a log line + a reader fix. No schema change, no migration,
no persisted state beyond the journal. Reverting is a commit revert.

---

## 4. The two commissioning constraints, pinned

### CONSTRAINT 1 — the grace is gated on FAILURE CLASS, not on elapsed time alone

**The rule.** A purely time-based grace is exploitable: a genuinely broken release *also* has
a boot window, so it would escape the never-green rollback. Therefore the readiness failure
modes are classified, and **only the recoverable class is grace-eligible**.

**The class table, the multi-component aggregation rule, and the counter observation kinds
are §4.2's** → this record does not restate them. A **tick**'s class is the
aggregation of the classes of its degraded components; the aggregation is a **conjunction, not
a vote** (one grace-ineligible component among several timeouts kills the grace for that
tick). The reasoning for the rule, and the *where the class is computed* (daemon-side), is
§3a's; the rule itself is:

> Normative source: requirements.md §4.2 (P2 — grace eligibility classes, the NORMATIVE
> multi-component aggregation rule, and the counter observation kinds GREEN / HELD / RESET).

**How the never-green protection still fires on a broken release.** The never-green trigger
is **UNCONDITIONAL** (§3b B4): cap-reach with `consecutive_green < 3` ⇒ return 1 ⇒
auto-rollback (+ quarantine). The grace changes only **how long the gate is willing to WAIT**
→ never **whether** a never-green release is rolled back. In particular:

- A **never-grace-eligible** failure **never arms the grace** → the release is rolled
  back on the current schedule, with no extra time at all.
- A **grace-eligible** failure arms a **bounded** grace (§4.3's horizon and the soak's
  absolute bound). When the window is consumed without 3 greens ⇒ return 1 ⇒
  auto-rollback, exactly as today.
- A **grace-eligible daemon that NEVER greens still rolls back** → the grace arms, the
  counter still reaches cap-reach below 3, return 1. This is a required leg of the
  acceptance set (§5(ii)), and it is the leg that the withdrawn conditional form would
  have broken.
- The **leak class** is never grace-eligible, so the self-sustaining leak cannot be masked for
  even one second. A leaking daemon is rolled back on the current schedule, with the leak
  visible in `detail`.
- Any **fairness diagnostic** is **DIAGNOSTIC ONLY** → it is surfaced in the journal
  line, pinned as **not a success path**, and MUST NOT influence the return value.

> Normative source: requirements.md §4.1 (P1 — the never-green trigger, unconditional; exactly
> two cap-reach outcomes; the ceremony-truncation case is the pre-soak deferral; fairness
> diagnostics are diagnostic only).

**How the grace is BOUNDED.** **ONE clock and ONE number**, and they are **§4.3's** →
this record does not restate them. The *relationship* the design needs is recorded here: the
grace window is **observation-relative** (it starts at the first grace-eligible observation,
not at boot), it is **never extended**, and the soak's **absolute** bound
(`soak_entry + 120 + 180 = soak_entry + 300s`) is the outer edge the grace can never reach.
There is **no boot-relative grace ceiling** — the valueless `grace_abs_ceiling_s` is
**deleted** (it was a second, incompatible clock; see the alternatives table).

> Normative source: requirements.md §4.3 (P3 — grace ceiling and its single clock; the
> relationship to the soak's absolute bound), §4.6 (P6 — the AC thresholds those bounds
> carry).

**Implementation consequence — the signal must be structured, and it must be daemon-side.**
Today the gate **discards the 503 body** (`lib.sh:3005` reads it only on green), so the gate
cannot classify anything. Two options were considered and the **daemon-side** one is chosen
(§3a A1.6): the daemon computes the class, the pool state, the leak indicator and the grace
state, and publishes them in the `/readyz` `detail`; the gate reads them and applies the
policy. The gate change is: (i) read the body on the **transient** path too (`lib.sh:3005`),
and (ii) parse `detail.grace` / `detail.leak_signal` with the existing `_json_sub`/
`_json_field` helpers (`lib.sh:3148-3150`).

**The observability contract (readyz `detail`).** The following keys are added, all
**additive** (older consumers ignore them):

| key | meaning | NEVER disappears while |
|---|---|---|
| `detail.grace.active` | bool — is a grace currently held | the grace is held |
| `detail.grace.reason` | the failure class that armed it | the grace is held |
| `detail.grace.expires_at` | ISO — hard expiry | the grace is held |
| `detail.grace.scope` | `"load"` (the only scope today) | the grace is held |
| `detail.grace.holds` | int — how many HOLD observations so far | the grace is held |
| `detail.leak_signal.detected` | bool | the leak signal is asserted |
| `detail.leak_signal.checkedout_rising` | bool — SQLAlchemy pool `checkedout()` monotonic over 3 ticks | the signal is asserted |
| `detail.leak_signal.streak` | int — consecutive ticks of the same component with no recovery | the signal is asserted |
| `detail.pool_state.sqlalchemy` | `{checkedout, size, overflow}` | always present when the pool exists |
| `detail.pool_state.psycopg` | `{pool_size, pool_available, requests_waiting}` | always present when the pool exists (semantics: `requirements.md` §4.4, P4 — **normative**) |
| `detail.mcp.<server>` | §3h's per-server map | always present for each built-in |

**The absolute invariants** (these are the whole point of CONSTRAINT 1):

1. **Grace never renders "healthy."** `/readyz` still answers **503** while a grace is held;
   `status` remains `"degraded"`; the `components` map keeps the failing component(s) at
   `false`. The daemon does not lie to any consumer.
2. **The underlying reason strings NEVER disappear.** `detail.reasons` keeps listing the
   degraded components and their reasons, unchanged, while a grace is held. **A suppressed
   reason string is a defect** and is a named acceptance criterion.
3. **A leak signal makes the grace ineligible**, not merely shorter.

---

### CONSTRAINT 2 — the soak's adaptation is a BOUNDED WINDOW EXTENSION; the never-green trigger stays UNCONDITIONAL (normative: requirements.md §4.1)

> Normative source: requirements.md §4.1 (P1), §4.2 (P2), §4.3 (P3), §4.6 (P6) — this section states the **reasoning** only.

**The rule.** The gate already tolerates a transient-exhausted probe in the *gate* phases
(`_probe_with_retry` returns 2 = "do not fail the phase", `lib.sh:3118-3120`); the **soak** does
not (a transient counts as "no green" and resets the counter, `lib.sh:5216-5221`). The
adaptation must therefore change the **counter semantics**, not only the probe
classification — a grace that merely *suppresses* still yields `consecutive_green = 0` and
still triggers C-1.

**Decisions (as adopted in §3b).**

- **(i) Does the soak window extend, and by how much?** **Yes — by a bounded amount**, and
  the bounds are **§4.3's** (one observation-relative clock; the grace horizon; the soak's
  absolute bound `soak_entry + 120 + 180 = soak_entry + 300s`). This record does not restate
  the numbers.
- **(ii) Does the never-green trigger become "no green observed" or "no green observed AND a
  fair observation window elapsed"?** **Neither — the trigger is UNCONDITIONAL.** Cap-reach
  with `consecutive_green < 3` rolls back, always; a cap-reach has exactly **two** outcomes
  and **none** of them returns 0. The withdrawn conditional form is recorded as a **REJECTED
  alternative** in §3b's alternatives table and in the **B4** decision record. This
  record's prior text is superseded.
- **(iii) The counter semantics** (GREEN / HELD / RESET, the GREEN reset-on-green, the
  livez-hard-green HELD precondition, and the bound on holds) are **§4.2's**. This record
  keeps only the *reasoning* for them → why HOLD does not advance, why the bound on
  holds is what keeps the never-green protection intact — and it **does not restate them**.

> Normative source: requirements.md §4.1 (P1 — never-green trigger), §4.2 (P2 — counter
> observation kinds, incl. the GREEN reset-on-green and the livez-hard-green HELD
> precondition), §4.3 (P3 — grace ceiling, its single clock, and the soak's absolute bound),
> §4.6 (P6 — the AC thresholds those bounds carry).

> **Why the counter keeps the never-green protection (reasoning, not a rule).** A HOLD advances
> nothing, so a daemon that only ever produces transient observations still ends the soak with
> `consecutive_green = 0`. The bound on consecutive holds is what stops a *stream* of
> transients from satisfying the 3-green requirement, and the unconditional cap-reach trigger
> is what makes the end state a rollback rather than a wait. Both are **§4.1/§4.2's** rules;
> the reasoning is recorded here so the intent survives the rule's relocation.
**Prior art — "healthy release rolled back for an environmental cause" has TWO precedents.****Prior art — "healthy release rolled back for an environmental cause" has TWO precedents.**

- **2026-10-09**: a DB outage window tripped the **v0.18.5** promote's readyz gate ⇒ ADR-005
  auto-rollback of a healthy release.
- **2026-10-26**: the load/MCP storm tripped the **v0.27.8** promote's soak ⇒ ADR-005
  auto-rollback **and quarantine** of a healthy release.

⇒ **A design that only special-cases "post-boot burst" will NOT cover the 10-09 class.** The
window semantics in this design are therefore **general**: they are driven by the *failure
class* of the observed degradation, not by the daemon's age and not by a "burst" label. A
DB-churn window that produces `probe_timeout` / `read_timeout` / `pool_busy` (the class
vocabulary is `requirements.md` §4.2's, P2 — **normative**) — the same
classes a burst produces — is covered by the same mechanism. What the design explicitly does
**not** generalise to is the **leak class** (never grace-eligible) and to failures that are
not timeouts. That boundary is stated rather than hidden.

**The mitigation, available before any code ships:** `ENSEMBLE_SOAK_CONSERVATIVE=1`
(`lib.sh:5067-5069`, `:5115-5149`) is the v0.18-identical strict mode — grace and amnesty
disabled. It is **not** a fix (it is the *opposite* of the fix) but it is the existing knob
that makes the gate's behaviour predictable while this work is in flight. It is retained
byte-identical.

---

## 5. Narrowed requirements

The never-green rollback protection is a **safety property** and **survives intact**. Where a
requirement could not be fully satisfied without weakening it, the requirement is **narrowed
explicitly** below rather than silently weakened.

| Requirement | Narrowed version | Why narrowed | Safety property preserved? |
|---|---|---|---|
| **A1** — "a boot/burst grace window for readiness" | The grace is a **load-class** grace (the class list is `requirements.md` §4.2's, P2 — **normative**): it engages only for `probe_timeout` / `pool_busy` / `queue_stale` / `read_timeout`, only while the leak signal is false, for a hard-bounded horizon, and it never changes the daemon's verdict. It is **not** a general "busy-but-healthy" detector and **not** a cure for the leak. | The leak class is self-sustaining; a general grace would mask it. The pool-leak repair is a separate commission (§8). | **Yes** — the daemon still reports degraded; the gate's never-green trigger is unchanged in outcome. |
| **A2** — "soak semantics that make 3 greens reachable" | Reachable via **HELD** observations, bounded by `MAX_HOLDS_PER_GREEN` (the bound is `requirements.md` §4.2's, P2 — **normative**), with a bounded window extension. A stream of transients still never satisfies the requirement. | An unbounded hold count would let a never-green daemon pass. | **Yes** — C-1 fires whenever greens < 3 at a fairly-offered cap. |
| **A3** — "do not auto-continue during a promote's soak" | The hold covers the **boot auto-continue pass**, the **runtime resume dispatch**, and the **eligible-pending sweep**. It does **not** cover **human-initiated** resumes (a user's message to an instance) — those are not the auto-continue path and are deliberately not gated. | Gating human-initiated work would make the daemon unusable during a promote. | **Yes** — the hold only defers work initiation; it never blocks serving. |
| **A4** — "size the checkpoint pool for the burst" | A **sizing knob + an explicit bounded pool `timeout` + a probe that can distinguish lock-saturation from a broken path**. It is explicitly **not** a cure for the pool leak and **not** a cure for the saver's lock contention. | The lock (not the pool) is the contention point, and the pool is being poisoned by a separate defect. | **Yes** — the sizing change is additive and reversible by configuration. |
| **B1** — "budget the MCP warmup outside the loop" | The **verified** loop-blocking work is moved off the loop (the `/proc` scan) and the unbounded handshake is bounded. It does **not** make the MCP warmup free: the **child** processes' 88–156% CPU is a child cost the daemon does not pay, and the daemon's own 105–114% is the loop starving on other work. | The child's CPU is not the daemon's CPU. | **Yes** — no safety property is at stake here. |
| **B2** — "decouple `/api/prompts/pending-count` from MCP warmup" | **Narrowed to two things:** (i) a **pin** proving that `/readyz` and `/api/prompts/pending-count` are **MCP-independent** (regression protection), and (ii) the **B1 loop-starvation fix**. **No decoupling mechanism is built** — there is no logical coupling to decouple. | `/readyz` (`daemon/api.py:4809-4879`) is a cached composite read with **no** MCP reference in any of the four components; `/api/prompts/pending-count` (`daemon/routers/prompts.py:1078-1100`) is a plain DB count whose 503s come from `_require_auth_on` (503 `AUTH_NOT_CONFIGURED`) or `_get_service` (503 `PROMPTS_UNAVAILABLE`). The coupling is **event-loop starvation only**. | **Yes** — and the pin makes the absence of coupling a *tested* property rather than a claim. |
| **B2′ — the RCA's 20:42:39 pending-count 503 as degradation evidence** | **Treated as UNPROVEN.** It may be an auth-config 503, not a degradation symptom. The decision does not rest on it. | The RCA's own line is "already degraded, 21s BEFORE any resume" — an inference from a 503 whose cause is ambiguous. | **Yes** — removing an unproven input strengthens the analysis. |
| **B3** — "kill the children left behind by failed init attempts" | Two parts: (i) the **quarantine prevents the spawn** (the primary fix), and (ii) a **scoped** live reaper for children of a quarantined server. The **general** stale-subprocess reaper is **not** extended to within-incarnation children. | The boot sweep's predicate requires the child's `starttime` to be **older** than the daemon's own (`daemon/services/boot_subprocess_sweep.py:472`) — within-incarnation children are **younger** by construction and are therefore **structurally ineligible**. Extending it would need a new predicate and new PID-recycle reasoning on a hot path. | **Yes** — the scoped reaper is bounded to one server's children. |
| **C1** — "circuit-break the MCP warmup" | An **init-path** guard reusing the single `CircuitBreaker` primitive (`daemon/sources/circuit_breaker.py:20`) plus a **per-boot quarantine registry** consulted by `definition.is_available()` (`base.py:125-152`). | The `ResilienceManager` guards tool calls and never allocates a breaker for context7 (§3e E4). | **Yes** — the quarantine is per-boot and in-memory. |
| **C2** — "fail fast on dead API hosts" | A **bounded, off-loop, per-attempt DNS probe** against the **resolved base the child will use**; failure is a quarantine signal, never a boot failure; the address is **discarded** (never pinned). | Pinning a stale IP would convert a fail-fast check into a wrong-endpoint connection. | **Yes** — a resolver hiccup can never disable a server permanently (the first timeout is not quarantine-eligible). |
| **C3** — "pin the context7 API base in the built-in template" | **TWO** schema fields (the root with a default; the derived API var unset), a validating `build_config` override, and a `schema_version` bump `"2"` → `"3"`. The **`mcp_set_env` built-in hole is left open** and routed to a separate defect. | Closing the tool hole breaks the operator's remediation route and the BYOK chain, and is not needed for C3's intent once the schema path works. | **Yes** — the "protected built-in" property is *strengthened* on the sanctioned path (the value becomes settable through a schema-backed, validated route). |
| **C4** — "observability for degraded MCP servers" | An additive `detail.mcp.<server>` map + journal lines + a per-boot registry. **No new table, no migration.** Plus a fix to the stale `upgrade_tools.py` reasons reader. | A new SQLModel table would require its own migration file for per-boot state the journal already covers. | **Yes** — additive payload; older consumers tolerate it. |

---

## 6. REUSE vs NEW ledger

| Capability | Existing mechanism to REUSE (`file:line`) | New artifact needed? | Why |
|---|---|---|---|
| **Circuit breaker** | **`daemon/sources/circuit_breaker.py:20`** (the ONLY implementation; `daemon/mcp/resilience.py:45` imports it). CR-4 single-probe-in-flight guard at `:32-38`/`:40-76`; lock-protected `record_success`/`record_failure` at `:78-108`; `reset()` at `:114-124` is the only unsynchronised mutator and must not be used concurrently. **Chosen: this primitive.** | **NO** — but a new *init-path guard* that hosts it | The `ResilienceManager` (`daemon/mcp/resilience.py:470-630`) is the **wrong layer**: `register()` is called from exactly one place (`daemon/services/mcp_service.py:1283`, reached only from `preload_mcp_tools` `:875`); a server with no `ResilienceConfig` never gets a breaker allocated (context7 registers none — `mcp_service.py:852-854`); the breaker guards **tool calls** (`resilience.py:13-18` → `tool_adapter._lazy_coroutine`); and `daemon/mcp/warmup_pool.py` contains **zero** resilience references — init is entirely outside the machinery. Reusing the *primitive* under a new init-path host is the reuse that fits the requirement "disable/quarantine THIS server FOR THIS BOOT". |
| **Per-boot "disable this server"** | **`definition.is_available()`** — `daemon/mcp/builtin_servers/base.py:125-152` (consulted at `daemon/manager.py:2813`, `:3057`; `daemon/services/mcp_service.py:576`, `:1128`). Today `required_package is None ⇒ True` (`:143`). | **YES** — the single leaf module **`daemon/mcp/init_guard.py`** (importing ONLY `daemon.sources.circuit_breaker` + stdlib), imported by `base.py` and consulted by `is_available()`; with a pinned `reset_for_tests()` (W-4a) and **no** call to `CircuitBreaker.reset()` (W-4b) | The alternatives are wrong-shaped: `MCP_DISABLE_BUILT_IN_*` is boot-scoped in intent but **persistent** in effect (it deactivates the DB row — `daemon/manager.py:2798-2800` → `_bootstrap_builtin_servers` `:2764`); `is_active=False` is persistent too. `is_available()` is the only **per-boot, in-memory** predicate already wired into all four paths, so the quarantine disappears from the DB bootstrap, the warmup registration, the warm path and the cold path at once. The registry's **home** is `init_guard.py` (W-4), not `builtin_servers/__init__.py` — the latter is the registration module and would make the state machine unreachable from the transport layer. |
| **Cold-spawn secret resolution (W-5)** | `daemon/mcp/connection_manager.py:229-237` (`resolve_secret_markers` / `resolve_env` at the spawn point) | **NO** (a dedicated `_SECRET_RESOLVE_EXECUTOR`) | The resolution is **inline on the event loop** and it runs on the **cold-spawn** path — the incident's ×4 path, because with the pool size of 1 (`daemon/config.py:2894`) every instance after the first falls through to cold start (`daemon/services/mcp_service.py:154-173`, `connect_instance` at `:169-173`). Hoisting it to a dedicated executor removes a KMS/Fernet stall from the loop without changing the fail-closed resolution contract (`:225-228`). |
| **`is_available()` override integrity (W-5)** | `daemon/mcp/builtin_servers/base.py:125-152` (`is_available`) | **NO** (one added guard) | `daemon/mcp/builtin_servers/plane.py:262-289` overrides `is_available()` and **never calls `super()`** (no `super()` appears anywhere in `plane.py`), so a quarantined plane would bypass the gate entirely. Adding `if not super().is_available(): return False` as the first statement makes the quarantine registry authoritative for **every** builtin. |
| **Stale-subprocess reaping** | `daemon/services/boot_subprocess_sweep.py` — the context7-mcp 4.2-day chain is the documented motivation (`:19-23`); the cgroup-identity + `starttime` + ancestry + PID-recycle guards are the reusable expertise (`:28-56`); the kill-switch is `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES` (`:120`). | **PARTLY** — a **scoped** live reaper for the children of a quarantined server | The boot sweep **cannot** reap this class: its predicate requires the child's `starttime` to be **older** than the daemon's own (`:38-42`), and within-incarnation children are younger by construction. So we reuse the *pattern and the safety guards*, not the predicate. The **primary** fix is prevention: the quarantine stops the spawn (C1). |
| **MCP pool sizing** | `daemon/config.py:2888-2918` `McpPoolConfig` (`env_prefix="MCP_POOL_"`), `default_pool_size=1` (`:2894`); the pool-size-1 consequence is documented at `daemon/services/mcp_service.py:107-115` | **NO** (config only) | The size is the issue; the knob already exists. |
| **Checkpoint pool sizing** | `daemon/persistence.py:273-294` (the `FUTURE KNOB` comment at `:276-282` names the exact change) and the second pool at `:313-314`; the config pattern is `LimitsConfig` (`daemon/config.py:595` `llm_concurrency`, `config.yaml:223`, enforced at `daemon/manager.py:707`) | **NO** — a config field + env + yaml interpolation | The knob is the deliverable; the pool construction site already carries the placeholder comment. |
| **Soak window / grace** | `scripts/upgrade/lib.sh:3172-3211` (`_boot_grace_*`), `:2959-3057` (`_probe_once_classified` — the 3/4-state contract), `:3069-3125` (`_probe_with_retry`), `:3127-3161` (`_read_inflight_turns_bounded`), `:5053-5248` (`gate_soak`), `:5088-5093` (`soak_deferred` journal event), `:1204-1234` (`journal_quarantine` / `journal_quarantine_clear`) | **NO** — extend in place | A parallel mechanism would mean two grace notions, two probe classifiers and two journal vocabularies in one file. The existing knobs (`ADAPTIVE_SOAK_*` `:146-149`, `BOOT_GRACE_S` `:157-158`, `BUSY_INFLIGHT_BOUND` `:168`) are the extension points. |
| **Failure-class signal** | `daemon/services/readiness.py:172-226` (`ReadinessComposite` — the additive-field precedent is `auth_dependency_status` at `:223-226` and `inflight_turns` at `:198-205`), `:239-274` (`to_payload`), `daemon/api.py:3777-3801` (the per-tick probe construction) | **PARTLY** — a classifier + a grace state machine + pool sampling | The payload shape, the refresh cadence and the composite dataclass all exist; what is missing is the class assignment, the pool sampling and the grace bookkeeping. |
| **Off-loop execution** | The house pattern is `asyncio.to_thread` + `asyncio.wait_for` — `daemon/services/readiness.py:470-472` (the probes already run in a thread) | **YES** — a thread hop for the `/proc` scan and for the DNS pre-check | `daemon/mcp/` contains **zero** `to_thread`/`run_in_executor` today; the pattern is established elsewhere in the codebase and must be *copied in*, not invented. |
| **Promote-transaction observation** | `daemon/tools/upgrade_journal.py:281-319` `journal_owner_heartbeat_fresh` (READ-ONLY; `:156` `OWNER_HEARTBEAT_FRESH_S = 60`); `daemon/tools/upgrade_tools.py:693-723` `_journal_read`; the install-root resolution at `daemon/tools/upgrade_tools.py:388-431`; the vocabulary `"pipeline-busy"` at `:2678`/`:2687`/`:2695`; the shell side at `scripts/upgrade/promote.sh:327`/`:412` and `scripts/upgrade/lib.sh:1116-1118` | **NO** | The readers, the heartbeat and the vocabulary all exist. The promote txn's heartbeat is refreshed during the soak because `lock_heartbeat` (`lib.sh:1564-1577`) refreshes both the lock and `in_flight.last_heartbeat` (`:1576`), and `gate_soak` calls it every iteration (`:5156`). |
| **Degraded-state persistence** | The daemon journal (`daemon/tools/upgrade_journal.py` `journal_history_append`) | **NO** — a journal line per state transition | Per-boot state; the journal already provides durable history. A new table would require its own `daemon/migrations/versions/*.sql` file for no benefit. |
| **Shell-vs-daemon delivery** | `daemon/tools/upgrade_tools.py:1919-1942` `_resolve_scripts_dir` (env → repo checkout → install dir) | **NO** | The shell half is consumed from the working tree, so it can affect the next promote without a release bump — but it must still be committed and shipped in the repo. The daemon half needs a release. |

---

## 7. Pool topology — do not conflate

There are **three** distinct pools in play. Every change in this plan is tagged with the pool it
touches.

| Pool | Construction | Size / params | Used by | Changes in this plan |
|---|---|---|---|---|
| **P1 — SQLAlchemy (manager engine)** | `daemon/repositories/factory.py:255` `create_postgres_engine`; engine created at `daemon/manager.py:719`; params at `:325-330` | `pool_size=5`, `max_overflow=10`, `pool_pre_ping=True` (no `reset_on_return` override ⇒ SQLAlchemy 2.0.48 default `'rollback'`; P1 is the **ADVISORY** pool per `requirements.md` §4.4, P4 — **normative**) | The `SELECT 1` probe (`readiness.py:565-577`), the `queue_freshness` probe (`readiness.py:763-836`), and every ORM read path | **Observed only** (A1's leak predicate reads `pool.checkedout()`/`overflow()`/`size()`). **No sizing change here** — this is the pool the pool-leak RCA says is being poisoned; its repair is a non-goal (§8). |
| **P2 — psycopg (checkpoint saver)** | `daemon/persistence.py:273-294` | `min_size=1`, `max_size=5`, `check=AsyncConnectionPool.check_connection`, `max_lifetime=3600`, `max_idle=600`, `reconnect_timeout=300`, **no `timeout`** ⇒ library default 30.0s | The LangGraph saver (`langgraph/checkpoint/postgres/aio.py:59`/`:374` — **one lock** for all cursor ops) and the readiness sentinel read | **A4**: size to the writer arithmetic (7 + probe + headroom = 10), add an explicit bounded `timeout` (5s), cancel the probe's queued position, and let the probe distinguish lock-saturation from a broken path. |
| **P3 — asyncpg (checkpoint adapter)** | `daemon/persistence.py:313-314` | `min_size=1`, `max_size=5` | The checkpoint **adapter**'s own pool | **A4 (conditional)**: if the adapter pool shares the contention, the same knob applies. To be confirmed in the implementation plan; the saver pool (P2) is the one the sentinel probe rides. |

**Rule for the plan:** no sentence may say "the pool" without naming P1, P2 or P3. The
pool-leak RCA's failure is on **P1**; the A4 sizing change is on **P2**; the readiness
`checkpoint_saver` component's failure can be caused by **P2** (lock/queue) or by **P1**
(the sentinel's own connection contention). §3d's D1a (distinguishing lock-saturation from a
broken path) is what lets the class table attribute a failure to the right pool.

---

## 8. Explicit non-goals

These are **owned by a follow-up commission** and are deliberately **not** designed into this
lane. They are listed because they are in the incident's causal chain and because A1/A4 must be
designed **around** them.

| # | Non-goal | Anchor | Why out of scope | How this lane must not mask it |
|---|---|---|---|---|
| 1 | `ens_db_tools` explicit `BEGIN` outside the `try` | `daemon/tools/ens_db_tools.py:1479` and `:1491` (the `try:` is at `:1476`; the BEGIN is outside it) | A DB-tool surface change with its own command-surface semantics; it is the tool lane's defect. | The leak predicate (§3a A1.4) must detect the resulting pool pressure and mark the state **grace-INELIGIBLE** (`pool_leak`). |
| 2 | The `/api/instances` ORM read-path leak | the pool-leak RCA's fingerprint table (2 backends) | The instances router is a large surface; the fix is a transaction-hygiene audit. | Same as (1). |
| 3 | The `queue_freshness` probe's own transaction leak | the pool-leak RCA's fingerprint table (1 backend) — **see premise 10**: the probe's SQL is `FROM task` (`readiness.py:133-137`, `:160-164`), not `FROM job_queue_items`, and the probe's `with engine.connect()` closes the connection (`:799`); what *is* real is the timed-out-probe connection retention acknowledged at `readiness.py:40-45` | The repair (a dedicated short-lived connection, or an explicit commit/rollback) changes the probe's transaction semantics and needs its own verification. | **The probe runs on P1** (`daemon/api.py:3779` — `make_queue_probe(manager.engine, …)`), **not** on P2: the **P1 mechanism is NOT addressed by A4** (A4 sizes the P2 psycopg saver pool only, §7). The P1 leak sites are owned by the follow-up transaction-discipline commission (non-goals #1, #2 and #4); what this lane contributes on the P1 side is the **advisory** visibility in `detail.pool_state` and the timeout-path retention fix, not a cure. |
| 4 | The `mission_records` mint error-path leak | `daemon/services/mission_record_service.py:679-691` (`mint_boot_epoch_if_none`), fail-open wrapper at `:1480-1484`; `UniqueViolation` on `ux_mission_records_anchor_open` 4×/boot (both RCAs) | Making the mint idempotent (upsert / `ON CONFLICT DO NOTHING`) or guaranteeing the rollback is a data-model change. | It is a **pool poisoner** under P1 ⇒ the leak predicate must catch its signature. |
| 5 | Pool hardening knobs | `pool_pre_ping=True` is **already on** for P1 (`factory.py:328`); `reset_on_return='rollback'` is the SQLAlchemy default and is **not** overridden; `pool_recycle` is not set | These are tuning knobs whose correct values need the leak rate measured over time, not a guess. **P2's `timeout` is in scope** (§3d D3) because it is a *correctness* bound on the ghost-waiter drain, not a tuning knob. | The leak predicate is the compensator until the knobs are set properly. |
| 6 | The wedge-capture `tasks` → `task` table-name bug | `daemon/services/wedge_capture/db_capture.py:308` (`FROM tasks t`) and `:387` (`FROM tasks`) — the table is `task` | A one-character fix in the wedge-capture module, owned by that lane; it is a *symptom* of the poisoned pool, not a cause (the same log lines also show `current transaction is aborted`). | Its failure output (`current transaction is aborted`) is itself a **leak signature** input to the predicate. |

**The boundary, stated once:** **A1 must be designed so it cannot mask (1)–(4)** — which is why
the leak class is grace-ineligible, why grace never renders healthy, and why the underlying
reason strings never disappear. **A4 must be designed so it does not pretend to cure them** —
which is why §3d D4 states the non-cure explicitly and why the pool-state surfacing
(§3d D5) is part of the deliverable.

---

## 9. Open questions / deferred decisions

**Numbering repaired (iteration 003, Worker A N1):** the rows below were previously numbered
1, 2, 3, 5, 6, 7, 8 — the #4 slot was skipped. They are now numbered **1–7 consecutively**.
The **row count is 7**; the **explicit non-goal count in §8 is 6**. Both counts are the
ones `plan-overview.md` must point at.

**Row removed (final fold, N2):** the former open question *"Should the daemon's
`mcp_set_env` built-in guard be closed, and with what allow-list shape?"* is **not an open
question** — it was **RESOLVED** (adjudication 1): `mcp_set_env` is **NOT closed in this
lane**; it is a routed defect with a named owner (agent-tool-surface consistency lane) and the
closure shape (an operator-pinned key allow-list, never a blanket ban) is recorded at
**§3g G6**. The row had also been **jammed onto row 3's line**; it is removed and the
remaining rows are renumbered **1–7 consecutively**.

| # | Question | Owner | Why deferred / what would unblock it |
|---|---|---|---|
| 1 | **Does cancelling the probe's scheduled future actually remove its entry from psycopg_pool's waiting queue?** | developer (implementation) | This is library behaviour, not repo behaviour. §3d D3 states it as an **acceptance criterion** (a test asserting the waiting-queue length does not grow across N timed-out probes) rather than an assumption. If cancellation does *not* clean up the waiter, the fallback is a dedicated probe connection that bypasses the pool's queue. |
| 2 | **Exact leak-predicate thresholds**: how many consecutive ticks of a monotonic `checkedout()` rise constitute a leak? | developer + reviewer | The thresholds in §3a A1.4 (3 ticks) are a starting proposal, not a measured value. The measurement needs the leak rate under load — which is the follow-up commission's data. **Interim:** the predicate is conservative (fail-closed to `pool_leak` ⇒ no grace) and the thresholds are config-free constants in the first cut so they can be tuned in one place. |
| 3 | **Should the eligible-pending sweep's hold be a "skip the wake" or a "defer the row"?** | developer (implementation) | The **release mechanism is now decided** (council C-1: §3c C3a — per-seam deferred-candidates list + bounded 5s re-consult + release through the original seam). What remains open is the *storage shape* of the parked rows at seam 3: "skip the tick and park them in the hold module's in-memory list" (stateless, bounded by the hold's lifetime) versus a persisted deferral marker. The min pending age is 60s (`daemon/config.py:2185`) and the sweep interval is 90s, so either shape is workable. Decision deferred to implementation, but it must be pinned either way.
| 4 | **Does the context7 upstream package's env contract match our schema assumption exactly?** | developer (implementation) | The package's derivation (`CONTEXT7_API_URL || ${CONTEXT7_BASE_URL}/api`) is quoted from the RCA's inspection of `@upstash/context7-mcp` v4.4.4, not from this repo. §3g G1's shape must be **pinned by a test** against the real package's behaviour (or at minimum against the upstream source) before it ships. |
| 5 | **Should the grace's hard expiry (120s) and extension cap (180s) be operator-configurable?** | leader | They are new constants with no precedent. A conservative first cut ships them as shell-side constants (like `ADAPTIVE_SOAK_*` at `lib.sh:146-149`); making them env-tunable is a follow-on if operators ask. |
| 6 | **The pool-leak repair commission's scope and owner** | leader | This is the root cause of the 22:10–22:50Z incident and of the self-sustaining probe feedback loop. §8 lists the four confirmed leak sites; the repair (transaction hygiene, `ens_db_tools`' BEGIN, the mint path, the `/api/instances` read path) is a separate lane and should be commissioned explicitly, not absorbed here. |
| 7 | **The wedge-capture `tasks` → `task` fix** | leader (routing) | One-character fix in `daemon/services/wedge_capture/db_capture.py:308`/`:387`; flagged here so it is not lost, but it belongs to the wedge-capture lane. |

---

*Artifact written in `/home/nea/ensemble-src-wt-soak-gate` on branch
`feature/soak-gate-mcp-warmup` (base `d5a4e4ad92d19f3265b97bc29c5aa3943f026ed2`). No
`daemon/`, `scripts/`, `tests/`, `agents/` or config file was modified. The live daemon, the
install `.env`, `state.json`, `releases/` and the `current` symlink were read only (the
`current` symlink was observed read-only to confirm the live release is v0.27.8).*
