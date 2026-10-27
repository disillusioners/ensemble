# Plan Overview — soak-gate-mcp-warmup

**Commission:** promote soak gate fix + MCP warmup degradation fix
**Branch / worktree:** `feature/soak-gate-mcp-warmup` @ `d5a4e4ad92d19f3265b97bc29c5aa3943f026ed2`
**Worktree (the only place anything was read):** `/home/nea/ensemble-src-wt-soak-gate`
**Status:** PLANNING ONLY — no `daemon/`, `scripts/`, `tests/`, `agents/` or config file was modified by this artifact. The live daemon, `/home/nea/agents-ensemble/*`, `state.json`, `releases/`, the `current` symlink and the main checkout `/home/nea/ensemble-src` were **not** touched.

---

## How to read this file

This is the **orientation** artifact for the commission. It does not re-derive the design and it does not
restate the requirements: `decisions.md` is the **design authority** (what we decided and why, 8 decision
records (a)–(h), the two pinned commissioning constraints, 26 premise corrections, the REUSE-vs-NEW ledger, the
three-pool topology, 6 explicit non-goals, 7 open questions) and `requirements.md` is the **requirements
authority** (the 12 requirement rows A1–A5 / B1–B3 / C1–C4 with their verified current/intended/AC/safety
columns, the normative failure-CLASS table, the A5 four-sub-case acceptance detail with harness rules, the 8
narrowed requirements — **nine** narrowings N-1…N-9, of which N-9 is a **disclosure**, not a requirement
narrowing — the 6 non-goals, and the requirement→phase coverage summary). **This file
adds three things those two do not carry:** (1) the **phase structure** — which requirement closes in which
phase, in what order, with what dependency and what delivery mechanic; (2) the **traceability matrix** — one
row per requirement binding requirement → phase → named implementation step → verified touch point → named
acceptance criterion → preserved safety property → narrowing status, so that no cell is a vague promise;
(3) the **adjudication record** — the three rulings the caller made where the two sibling artifacts disagree,
so a reviewer can see the divergence was caught and resolved rather than missed.

**Precedence rules used throughout this file.** (i) Where `decisions.md` and `requirements.md` disagree on a
question of **WHAT to do**, `decisions.md` wins (design reasoning). (ii) Where they disagree on an **observed
number** (a line number, a count), `requirements.md` wins, because it did the last stale-anchor pass — with
the **three adjudications below** overriding both where the caller has ruled. (iii) Nothing in this file
narrows or weakens a safety property; where a requirement could only be satisfied by weakening one, the
requirement is narrowed (N-1…N-8, one per requirement row; N-9 is the interim-efficacy disclosure) and the
narrowing is visible in the matrix.

---

## Pointer table — the artifact set

| File | Role | Read it for |
|---|---|---|
| `plan-overview.md` **(this file)** | Orientation | Objectives, scope boundary, phase structure, traceability matrix, risk summary, success criteria, adjudication record, open items |
| `decisions.md` | **Design authority** | Decision records (a)–(h); the two commissioning constraints (CONSTRAINT 1 failure-class-gated grace, CONSTRAINT 2 bounded window extension); the 26 premise corrections; the REUSE-vs-NEW ledger (§6); the three-pool topology P1/P2/P3 (§7); the **6** explicit non-goals (§8); the **7** open questions (§9) |
| `requirements.md` | **Requirements authority** | The 12 requirement rows with verified current/intended/AC/safety columns (§2–§3); the **normative failure-CLASS table** (§4); the **A5 four-sub-case acceptance detail + harness rules** (§5); the **nine** narrowings N-1…N-9 (§6; N-9 is a **disclosure**, not attached to a requirement row); the 6 non-goals (§7); the requirement→phase coverage summary + delivery mechanics (§8) |
| `phase1-plan.md` | Phase 1 execution plan (sibling worker, in parallel) | Step-by-step implementation of A1/A2/A3/A4 |
| `phase2-plan.md` | Phase 2 execution plan (sibling worker, in parallel) | Step-by-step implementation of B1/B2-narrowed/B3 |
| `phase3-plan.md` | Phase 3 execution plan (sibling worker, in parallel) | Step-by-step implementation of C1/C2/C3/C4 + the A5 cross-cutting pins + staged verification |
| `risk-safety-register.md` | Full risk register (sibling worker, in parallel) | The complete risk set with mitigations, owners, and the safety-property trace; §6 of this file is the **summary** view of it |
| `architecture-recommendation.md` | **Companion architecture layer** under the plan set — the caller-side code-level implementable layer (dated 2026-10-27, "COMPLETE (3/3 analysis reports fanned in)"). **Not one of this overview's artifacts.** | The code-level placement, fields, formulas and rulings **binding for implementation** beneath the plan set. **It has NOW BEEN FOLDED to the current design and is consistent with this file** — its corrections table (**§10.2**, moved from §10.1) carries the approver-fold corrections: the **C-1 four-seam correction**, **N-a** (the D1a lock expression resolved to `checkpointer.raw_saver.lock.locked()` + the resolution pin), **N-b** (the named A3 release driver), **N-c** (the dedicated-connection fallback is a contention mitigation, not a detection mechanism), the **loop-lag AC §5.8** (the event-loop-lag bounds, held in `requirements.md` §4.6), **BLOCKING 3 §8.3** (the env-preserving heal) and **BLOCKING 4 §9.3** (the journal-write suppression). §10.2 also records the **evidence-based rejection of the `cls().name` inversion** (name is a `@property @abstractmethod` — `base.py:27-30` — so `cls.name` is a property object and the quarantine would never match). **The binding pointer is therefore safe**: the arch doc's pointer discipline is exactly why the binding pointer in this file can be a pointer rather than a restatement |

> The three phase files and the risk register are being written by a sibling worker in parallel and are
> referenced here **by filename** — they will exist alongside this file. Where this file and a phase file
> disagree on a step name, the **phase file wins on mechanics** and this file's matrix still holds as the
> requirement→acceptance binding.

---

## 1. Objectives

- **Fix the promote soak gate so a healthy release is not rolled back and quarantined.** On 2026-10-26 the
  promote of v0.27.8 passed every gate (integrity, livez, readyz including `checkpoint_saver`, version verify)
  and then the adaptive soak sampled **zero green probes**; the `cap-reach with consecutive_green=0` branch
  returned 1, which routes to ADR-005 auto-rollback **and quarantine** of a healthy release.
- **Fix the MCP warmup so a degraded MCP server cannot starve the daemon's single asyncio thread.** The
  context7 init storm (NXDOMAIN → 30 s hangs × 3 retries × every 2–4 min, forever — nothing circuit-breaks
  init) spawned `npm exec @upstash/context7-mcp` children at 88–156 % CPU, and the daemon main process pegged
  at 105–114 % CPU with zero agent work.
- **Address BOTH co-causes, and do NOT rank them.** The RCA is internally inconsistent about weighting: the
  main analysis calls auto-continue's resume burst "the largest single contributor, but not the only trigger",
  while the 21:45Z addendum weights the context7 storm most heavily of the two. The requirements
  layer (`requirements.md` §1.2) therefore forbids the set from assuming either dominates, and this plan
  follows it: **each co-cause gets its own independent remedy**, and no requirement's acceptance depends on the
  other co-cause being fixed. The remedy for co-cause 1 (post-boot saturation) is Phase 1 (A1–A4); the remedy
  for co-cause 2 (init storm) is Phases 2–3 (B1–C4).
- **The prior art: this is the SECOND environmental rollback of a healthy release.** On **2026-10-09** a
  database-outage window tripped the **v0.18.5** promote's readyz gate ⇒ ADR-005 auto-rollback of a healthy
  release; on **2026-10-26** the load/MCP storm tripped the **v0.27.8** promote's soak ⇒ ADR-005 auto-rollback
  **and quarantine** of a healthy release. ⇒ **A design that only special-cases the post-boot burst would NOT
  cover the 2026-10-09 class.** The window semantics are therefore **general** and **class-driven**
  (`requirements.md` §4.2): the environmental DB-churn class produces the same observable classes a burst
  produces, so the same rule covers it. **The 2026-10-09 class is NOT a post-boot special case**, and this
  commission does not add a post-boot-only branch. What the design explicitly does **not** generalise to is
  the **leak class** (never grace-eligible) and to failures that are not timeouts — that boundary is stated in
  the register, not hidden here.
- **Treat the third, later-confirmed co-cause — the pool leak (22:10–22:50Z incident) — as a CONSTRAINT, not
  as scope.** The pool-leak RCA proves a *self-sustaining* failure class (leaked `idle in transaction` backends
  poison the SQLAlchemy pool → the daemon's own probes starve → the event loop burns CPU retrying around a
  starved pool; it re-forms in ~3 minutes under load after every restart). It is **in context** because it
  dictates the leak-INELIGIBLE class (A1/N-4) and the "sizing is not a cure" honesty clause (A4/N-3), and it is
  **out of scope** because the leak sites themselves are explicit non-goals (§3 below). The commission's job
  here is to be **safe in the presence of** the leak, not to fix it.
- **DISCLOSED TRADEOFF — the interim efficacy of this lane (council R-2 / risk register R-MASK-11).** The
  `queue_freshness` probe's own transaction leak is non-goal #3 and is **still live** until the follow-up
  transaction-discipline commission lands. Its signature therefore **correctly fires `pool_leak`**, and because
  `pool_leak` is **grace-INELIGIBLE by design** (CONSTRAINT 1), the grace **refuses** — which is the *correct*
  behaviour, not a bug. The operational consequence must be stated plainly rather than buried: **the
  healthy-release rollback CAN RECUR** for that signature until the leak is repaired, because the mechanism that
  would otherwise absorb it is deliberately disarmed. **The interim primary objective of this commission is
  carried by C1 / C2 / A3 / A4** (stop the init storm, fail fast on a dead host, hold the resume burst across the
  ceremony, size + surface the saver pool) — **not** by the grace, which covers **only the busy-but-NOT-leaking**
  signature. A reader who expects this lane to make the 2026-10-26 rollback impossible is reading it wrong: this
  lane makes the *init storm* and the *resume burst* survivable, and makes the *leaking* case fail **loudly and
  correctly** rather than silently. Operators reading the readiness `detail` afterwards should expect
  `grace_active=false` with a leak indicator under sustained load, and should read that as *the known non-goal
  is live*, not as *the grace is broken*.
- **Keep the never-green rollback protection intact.** It is a SAFETY PROPERTY, not a feature. Every narrowing
  in this commission is the safety property winning over a requirement, and it is recorded as such.

---

## 2. Scope

### 2.1 In scope — the 12 narrowed requirements

Requirements A1–A5, B1–B3, C1–C4 as narrowed in `requirements.md` §6 (N-1…N-8, one per requirement row; **N-9
is the interim-efficacy disclosure**, not a requirement narrowing). The narrowing that applies to
each is carried in the "Status of narrowing" column of the matrix in §5 and is **not** repeated here.

| Group | Requirements | What the group delivers |
|---|---|---|
| **Part A — promote soak gate** | **A1** grace window · **A2** soak semantics · **A3** auto-continue hold · **A4** checkpoint pool sizing · **A5** regression pins | A class-gated, bounded, leak-refusing grace that never renders "healthy"; a soak counter that can *reach* 3 greens without ever *manufacturing* one; a live promote-txn hold that defers (never drops) resumes; pool capacity + a bounded acquisition deadline + honest pool-health surfacing |
| **Part B — MCP warmup off the loop** | **B1** warmup off the main loop · **B2** readiness not gated on MCP (**narrowed** to a pin + the B1 remedy) · **B3** no child leaks | The `/proc` scan and the DNS pre-check off the event loop; both unbounded waits bounded; a failed init provably leaves **no** orphaned children and **no** unclosed session/streams |
| **Part C — MCP degradation** | **C1** circuit-break the warmup · **C2** fail fast on dead API hosts · **C3** pin the context7 API base · **C4** degraded servers degrade alone | An init-path breaker on the **one** existing `CircuitBreaker` primitive, a per-boot quarantine registry consulted by the `is_available()` seam, a bounded off-loop DNS pre-resolve against the base the child would actually use, a settable+validated context7 base via the protected schema path, and a per-server degraded registry published in `/readyz` |

### 2.2 Out of scope — the 6 explicit non-goals (by name, with the one-line reason each)

These are **owned by a follow-up commission**. They are in the incident's causal chain, which is exactly why
A1/A4 must be designed *around* them.

| # | Non-goal | Why it is out of scope (one line) |
|---|---|---|
| **1** | `daemon/tools/ens_db_tools.py:1479`/`:1491` — an explicit `BEGIN` **outside** the `try`, so a failure skips the rollback | A separate transaction-discipline defect in a tool file; fixing it here would widen the diff into an unrelated surface (owner: follow-up, DB-transaction-discipline) |
| **2** | The `/api/instances` ORM read-path leak | Same class as (1) but a different read path; a symptom contributor, not a cause of the soak-gate failure (owner: follow-up, DB-transaction-discipline) |
| **3** | The `queue_freshness` probe's **own** transaction leak | Deliberately out of scope **so that A1 can be built as a grace that refuses to engage on a leak signature** rather than one that masks a leak — fixing it here would invalidate A1's leak-refusal AC (owner: follow-up, DB-transaction-discipline; **must land before A1's leak-refusal is relaxed**) |
| **4** | The `mission_records` mint error-path leak (`UniqueViolation` on `ux_mission_records_anchor_open`, raised inside a transaction without a rollback, 4× per boot) | Same rationale: A4 must **size and surface, not cure**; it appears in **both** RCAs, so it deserves its own commission (owner: follow-up, DB-transaction-discipline) |
| **5** | Pool hardening knobs — `pool_pre_ping=True` (already on for the SQLAlchemy pool, `daemon/repositories/factory.py:188`, `:220`), `reset_on_return='rollback'`, `pool_recycle` | Partly already on; the rest are DBA-level tuning that belongs with the leak fix (items 2–4), not with a sizing change (owner: follow-up DB-transaction-discipline / DBA) |
| **6** | The wedge-capture `tasks` → `task` table-name bug (`daemon/services/wedge_capture/db_capture.py:308`, `:387`) | Unrelated to the soak gate or the MCP warmup; it degrades the *diagnostics* surface but not the gate (owner: follow-up, wedge-capture) |

### 2.3 The binding boundary (stated once, and it is load-bearing)

> **A1 must be designed so it CANNOT MASK non-goals 1–4; A4 must be designed so it does NOT PRETEND TO CURE
> them.**

The enforcement mechanisms, all of which are acceptance criteria rather than aspirations:

- A1's grace is **armed on a failure CLASS**, and the `pool_leak` class is **grace-INELIGIBLE** — so the
  self-sustaining leak can never be held for even one second (A5(iv)(b)).
- A1's grace **never renders "healthy"**: `/readyz` still answers 503, `status` stays `"degraded"`, the failing
  components stay `false`, and `detail.reasons` keeps listing the real reasons. **A suppressed reason string is a
  defect** and is a named AC (A5(iv)(a)).
- A4's honesty clause is mandatory wording: sizing **does not cure** a pool that is being poisoned underneath
  it. A review that reads "we sized the pool" as the whole of A4 must **reject** the change (N-3).
- A4's **pool-health surfacing** is what lets an operator *see* the poison instead of inferring it from
  timeouts.

---

## 3. Phases

Grouped by **shared context / subsystem**, not by requirement letter.

### Phase 1 — Promote soak gate + readiness grace + pool sizing + auto-continue hold

**Objective.** Make the promote ceremony able to distinguish "busy but healthy" from "broken" — without ever
telling the daemon it is healthy, and without giving a broken release any extra time.

**Requirements closed:** A1, A2, A3, A4.

> **Normative source: `requirements.md` §4.7 (P7 — A3 release rule).** The hold module's name and the predicate
> it exposes are **implementation detail**; the liveness predicate, the boot-window rule, the release hysteresis
> and the driver contract are defined **once**, in the register, and are not restated here.

**Primary subsystems / files** (from `requirements.md` §8, anchors re-verified in the worktree):

- `daemon/services/readiness.py` — the `ReadinessComposite` dataclass (`:172-226`), the `to_payload` additive
  `detail` builder (`:239-274`), the probe budgets (`:45`, `:54`, `:59`), the composite components
  (`:295-350`).
- `daemon/api.py` — the readiness refresher (`:3720-3820`), the per-tick probe construction (`:3777-3801`),
  the refresher task creation (`:2472-2486`), `/livez` (`:4793-4807`) and `/readyz` (`:4809-4879`).
- `scripts/upgrade/lib.sh` — `gate_soak` (`:5053-5248`), `_probe_once_classified` (`:2959-3057`), the counter
  (`:5216-5221`), the cap arithmetic (`:5080-5109`), the `soak_deferred` journal event (`:5088-5093`), the
  boot grace (`:157-158`, `:3197-3212`), the body-read-on-green-only line (`:3005`).
- `daemon/persistence.py` — the psycopg saver pool (`:273-293`) and the asyncpg adapter pool (`:310-316`).
- `daemon/config.py` — `LimitsConfig` (`:595` `llm_concurrency` neighbourhood, `env_prefix="LIMITS_"`),
  `daemon/constants.py:91`/`:103` (worker-pool sizes), `daemon/manager.py:13671-13684` (runtime resume
  dispatcher), `daemon/services/eligible_pending_sweep.py:399` (eligible-pending sweep),
  `daemon/services/auto_continue_boot_pass.py:272` (boot pass, invoked at `daemon/api.py:2703`) **and
  `:726-729`** (`sweep_orphaned_live_turns`, the task-less-orphan sister pass, wired at `daemon/api.py:2708`),
  `daemon/services/promote_txn_hold.py` **(NEW — the hold module: `promote_txn_live()` predicate + per-seam
  deferred-candidates list + bounded-cadence release)**, `daemon/tools/upgrade_tools.py:693-723` (`_journal_read`),
  `daemon/tools/upgrade_journal.py:281-319` + `:156` (`journal_owner_heartbeat_fresh`).

**Dependency on other phases.** Phase 1's **daemon-side** work is **independent** of Phases 2 and 3 — it
touches no MCP file. The one cross-phase coupling is the **readiness `detail` payload**: A1 adds
`detail.grace` + `detail.leak_signal`, **A4 adds `detail.pool_state`**, and **C4 (Phase 3) adds
`detail.mcp.<server>`** — three additive sibling blocks under one `detail` namespace.

> **Coordination point (binding).** `daemon/services/readiness.py` `to_payload` (`:239-274`) and the composite
> dataclass (`:172-226`) are a **shared surface** touched by A1, A4 (Phase 1) and C4 (Phase 3). Rules: (i) the
> Phase-1 owner lands the composite + payload **plumbing** (grace state, pool state, leak signal) and the
> Phase-3 owner adds only a **sibling map** under the same namespace — no key collision, no re-shaping of
> Phase-1's keys; (ii) **Phase 1 lands before Phase 3's C4 payload work** so the shared surface has one
> owner at a time; (iii) a **detail-payload shape pin** asserts the three blocks coexist, that no key
> collides, and that a consumer that ignores the new keys still parses the body (the `detail.inflight_turns`
> additive precedent, `readiness.py:198-205`).

**Delivery mechanic.** **Split.**
- **Shell side (A2's gate half, and any gate-side half of A3):** working-tree-sourced. The frozen binary
  carries no `scripts/`, and the daemon's own executor resolves the scripts dir via
  `daemon/tools/upgrade_tools.py:1919-1942` (`_resolve_scripts_dir`: `ENSEMBLE_UPGRADE_SCRIPTS_DIR` → the repo
  checkout the module lives in → `<install_dir>/scripts/upgrade`). A repo change affects the **next promote
  without a release bump** — but it **must still be committed and shipped in the repo**; an uncommitted shell
  fix is not a fix.
- **Daemon side (A1's daemon half, A3's daemon half, A4):** the normal **stage → promote** path.
- The **live release is v0.27.8** and is **untouched** by this commission.

---

### Phase 2 — MCP warmup off the event loop + readiness decoupling pin + leak fixes

**Objective.** Get the loop-thread work out of the MCP spawn path and bound every wait in it, so a degraded or
hanging MCP server can never again starve the daemon's single asyncio thread.

**Requirements closed:** B1, B2 (narrowed per N-1), B3.

**Primary subsystems / files** (from `requirements.md` §8):

- `daemon/mcp/stdio_wrapper.py` — the `on_ready=self._stamp_children` wiring (`:485-486`), the hook's type
  (`:266`, to be widened), the `on_ready()` call site and `_ready.set()` (`:401-409`), `_stamp_children`
  itself (`:494`; the synchronous `/proc` reads at `:116`, `:132`, `:179`), the unbounded
  `await self._ready.wait()` (`:306`).
- `daemon/mcp/warmup_pool.py` — `streams_cm.__aenter__()` (`:275`) sitting **outside** the
  `asyncio.timeout(60)` that starts at `:278`; the existing `except BaseException` cleanup (`:333-344`); the
  inline `resolve_secret_markers(resolve_env(...))` on the pool spawn path (`:256-268`).
- `daemon/mcp/connection_manager.py` — the same inline secret/KMS resolution on the **cold-spawn** path
  (`:229-237`), to be hoisted onto a dedicated `_SECRET_RESOLVE_EXECUTOR` (W-5).
- `daemon/services/readiness.py:295-350` (the four readiness components — the MCP-independence pin's subject)
  and `daemon/routers/prompts.py:1078-1102` (`pending-count`).
- `daemon/services/boot_subprocess_sweep.py:21-23` — the existing boot reaper, which **stays in place** as the
  last line of defence (B3 does not replace it).
- `daemon/manager.py:3137` — the warmup task creation (a task is still **on** the loop).

**Dependency on other phases.** **Independent of Phase 1's daemon-side work** (no shared file) and of Phase 3
in the logical sense. It shares **files** with Phase 3, not logic: `daemon/mcp/warmup_pool.py` is touched by
B1 (`:275`/`:278`), B3 (`:333-344`) in Phase 2 **and** C1 (`:552-583`, `:711-726`), C2 (`:253-310`) in Phase 3.
Those are disjoint regions of one file, so the constraint is a **merge-order** constraint, not a design one:
Phase 2's `warmup_pool.py` edits land first, Phase 3's land on top. B3's *scoped live reaper* for the children
of a quarantined server depends on Phase 3's C1 quarantine registry existing — but B3's *primary* fix
(bounding the waits so the existing cleanup block becomes reachable) does not, and B3 is **not** blocked on C1.

**Delivery mechanic.** **Daemon-side only** — the normal **stage → promote** path. No shell-side work in this
phase, so no working-tree-sourced component. The live release (v0.27.8) stays untouched.

---

### Phase 3 — MCP degradation: circuit-break + DNS pre-check + context7 template pin + observability — **plus the cross-cutting A5 pins + staged verification**

**Objective.** Make a degraded MCP server degrade **alone** — quarantined for this boot, reported with why and
since when, recoverable without a restart, and never able to flip a readiness component or restart the daemon —
then prove the whole commission with the four A5 pins and a staged verification.

**Requirements closed:** C1, C2, C3, C4 — **and, in the final stage, A5** (the cross-cutting pins). Group (iv)
of `requirements.md` §8 — "cross-cutting pins + staged verification" — **folds into this phase's final stage**
rather than running as a separate phase, because the pins are only meaningful once the behaviour they pin is
implemented.

**Primary subsystems / files** (from `requirements.md` §8):

- `daemon/mcp/warmup_pool.py:552-583` (`_replenish`), `:711-726` (the health loop that re-runs init **forever**),
  `:253-310` (the init + pre-resolve seam), `:852` (`build_pooled_stdio_config` — the merged-env precedence:
  **row env wins on every key**).
- `daemon/sources/circuit_breaker.py:20` — the **only** `CircuitBreaker` in the codebase
  (`daemon/mcp/resilience.py:45` imports it; the CR-4 single-probe-in-flight guard at `:32-38`/`:40-76`; the
  lock-protected `record_success`/`record_failure` at `:78-108`; `reset()` at `:114-124` is the **only
  unsynchronised mutator** and must not be called concurrently).
- `daemon/mcp/builtin_servers/base.py:125-152` — `is_available()` (a **classmethod**; `required_package is None
  ⇒ True` at `:143`, which is why context7 is always "available" today) and the `env` section handling at
  `:222-223`. `base.py` **imports** the quarantine registry (never the reverse).
- `daemon/mcp/init_guard.py` **(NEW — the ONE home for the quarantine registry and the init breaker)** — a
  **leaf module** importing only `daemon.sources.circuit_breaker` + the standard library, so it is importable
  from anywhere without an import cycle. Exposes `reset_for_tests()` (pinned) and **never** calls the
  unsynchronised `CircuitBreaker.reset()` (documented + pinned). This **supersedes** the earlier
  `daemon/mcp/builtin_servers/__init__.py` home.
- `daemon/mcp/builtin_servers/plane.py:262-289` — the `is_available()` override that **never calls `super()`**
  (`:263`), so a quarantined plane would slip the gate; the `super()` guard is the fix.
- `daemon/models/mcp_server.py` + `daemon/routers/mcp_servers.py:256` (`_mcp_server_to_info`) — the
  `health_state` operator surface (`ok`/`degraded`/`quarantined`/`null`), populated from the init_guard registry, so
  the operator sees quarantine state in the existing servers list **without a readiness call**.
- `daemon/mcp/builtin_servers/context7.py:33-47` — base config with **no `env` key**; `get_config_schema()`
  returning `[]`; `schema_version` `"2"` (`:30`).
- `daemon/routers/mcp_servers.py:491` (`/configure-builtin`), `:760-768` (403 `BUILTIN_SERVER_PROTECTED`).
- `daemon/tools/infra.py:1527` — `mcp_set_env` (the unguarded built-in hole; **not closed in this lane** — see
  adjudication #1).
- `daemon/services/readiness.py:239-266` — the `detail` payload (C4's `detail.mcp` map) — the **shared surface**
  with Phase 1.
- `daemon/tools/upgrade_tools.py:2088-2092` — the stale top-level `readyz.get('reasons', [])` reader that
  always renders `reasons=[]` (C4's one-line reader fix).
- `daemon/daemon/migrations/versions/` — **no new file** (see Success criteria).

**Dependency on other phases.** Independent of Phase 1's daemon-side work except for the **shared
`readiness.py` `detail` payload** (the coordination point named in Phase 1). **C3 touches a FastAPI router**
(`daemon/routers/mcp_servers.py`), so the **route-table readback pin** required by the §5 harness rules is
mandatory here. **B3's scoped reaper** (Phase 2) reads the C1 quarantine registry (Phase 3) — the reverse
dependency, not a blocker.

**Delivery mechanic.** **Daemon-side** — the normal **stage → promote** path. The A5 pins are test-only and
ride the same tree. Staged verification must be **explicitly isolated**: a staged-release boot smoke is
**NOT** isolated (the binary's path derivation resolves the LIVE install root, so the sweep can bind and prune
the live promote journal). The live release (v0.27.8) stays untouched.

### 3.4 Coupling map — the shared surfaces between phases

Phase × phase, with the coupling strength and the direction. **Only the two "shared" rows below are real
couplings**; everything else is independent. A "shared surface" is a file or contract that two phases edit —
not merely that two phases read it.

| Phase ↓ / → | Phase 1 | Phase 2 | Phase 3 |
|---|---|---|---|
| **Phase 1** | — | **none** (no shared file) | **SHARED SURFACE — `daemon/services/readiness.py` detail payload.** A1 adds `detail.grace` + `detail.leak_signal`; A4 adds `detail.pool_state`; C4 adds `detail.mcp.<server>`. Direction: P1 owns the composite + payload **plumbing** and lands first; P3 adds a **sibling map** only — no key collision, no re-shaping of P1's keys. **Coupling strength: medium (ordering).** Pinned by the detail-payload shape pin (open item 12). |
| **Phase 2** | none | — | **SHARED FILE — `daemon/mcp/warmup_pool.py`.** P2: B1 (`:275`/`:278`), B3 (`:333-344`); P3: C1 (`:552-583`, `:711-726`), C2 (`:253-310`). Disjoint regions. Direction: P2 lands first, P3 on top. **Coupling strength: low (merge order).** Additionally a **logical** coupling: B3's *scoped live reaper* reads the C1 quarantine registry, but B3's *primary* fix (bounding the waits) does not — **B3 is not blocked on C1**. |
| **Phase 3** | see Phase 1 row | see Phase 2 row | — |

**Shared contracts (read by several phases, edited by one):**

| Contract | Owner (editor) | Readers | Constraint |
|---|---|---|---|
| The `/readyz` `detail` payload shape | **Phase 1** (composite + plumbing) | Phase 3 (C4 adds `detail.mcp`) | Additive only; a consumer that ignores the new keys must still parse the body (the `detail.inflight_turns` precedent, `readiness.py:198-205`) |
| The **failure-CLASS table** (`requirements.md` §4) | **Phase 1** (A1's classifier defines it) | Phase 1 (A2's counter), Phase 3 (C4's `recoverable` flag) | The table is **normative** and pinned; every A5 pin is written against it. A2 must consume it, not re-derive it |
| The `is_available()` seam + the per-process quarantine registry | **Phase 3** (C1 creates `daemon/mcp/init_guard.py`; `base.py` imports it) | Phase 2 (B3's scoped reaper) | The registry is **per-process, per-boot**; a restart clears it; the consult lives **inside** `is_available()`, so every call site inherits it with zero edits |
| The promote-txn journal readers (`journal_owner_heartbeat_fresh`, `_journal_read`) | **Phase 1** (A3 consumes them) | — | **Read-only.** A3 must never write `in_flight`; the daemon writes only `kind="restart"`, the shell owns `kind="promote"` |

---

## 4. House rules (binding on every phase)

These appear throughout the sibling artifacts and are reproduced here because they constrain *how* the work is
done, not just what it delivers. **Nothing in this plan contradicts them.**

*(house convention — not a §4 predicate; these are operating conventions, restated per phase by requirement, and
they are deliberately **not** converted into §4 pointers — there is no §4 anchor for a convention, and inventing
one would be wrong. The numeric thresholds they reference live in `requirements.md` §4.6; this block points at
them rather than restating them.)*

1. The **never-green rollback protection is a SAFETY PROPERTY and survives**; anything conflicting with it is
   **narrowed, not weakened**.
2. `env -u POSTGRES_*` is a **silent no-op** (bash does not glob env names) — scrub **per variable**:
   `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT <cmd>`.
3. Test scripts **never resolve live-shaped (HOME-derived) install paths** — `mktemp -d` as a **FAKE_HOME** plus
   an escape guard that refuses to run when the resolved path is under a real `$HOME`.
4. **Env-poison:** ambient-env boots have hit LIVE `ensemble_prod` **6 times** — every test boot is fenced
   (scratch DB + explicit `POSTGRES_*` + explicit `PORT`).
5. **Fresh-SQLite full-ledger boot is blocked** by migration `20260714_000001_widen_job_queue_type_constraint.sql`
   — verification runs on **real PostgreSQL**.
6. **Staged-release boot smokes are NOT isolated** (path derivation reaches the LIVE install root) — explicit
   isolation is required.
7. **FastAPI route/decorator binding trap** — if routers are touched (C3 touches
   `daemon/routers/mcp_servers.py`), include a **route-table readback pin**.
8. **Every new SQLModel table needs its own `*.sql` file** in `daemon/migrations/versions/` — this commission
   needs **NONE** (the tree carries 115 files / 115 distinct prefixes today).
9. **No ruff config** in the repo — do not introduce one.
10. **Every AC is machine-checkable** (named test/pin, command, observable).
11. **House runner form for integration gates**: `pyproject.toml:95` sets
    `addopts = "-m 'not integration and not postgres and not slow and not conformance'"`, so a bare
    `python -m pytest tests/integration/…` **deselects the very tests it names** and can report "0 failed"
    with **nothing executed**. Every integration gate runs in the house form
    **`--override-ini="addopts=" -m integration`** (56 house usages; precedent
    `tests/test_settings_api.py:19`) and **MUST assert a non-zero EXPECTED COLLECTED COUNT** — not merely
    "0 failed".
12. **`@pytest.mark.timeout(N)` convention**: the repo default is **30 s** (`pyproject.toml:97-98`;
    `timeout_method = "thread"` ⇒ the kill is `os._exit(1)`, so there is **no traceback**). Every new long
    pin carries an **explicit** `@pytest.mark.timeout(N)` — a **house convention** (115 usages) — with **N
    greater than its worst case**; a pin that exceeds 30 s without the marker is SIGKILLed with no
    diagnostics.
13. **Explicit interpreter**: `python` **does not exist** on this host — only `/usr/bin/python3`; the worktree
    has **no `.venv`**; and `uv` is **off-PATH** at `/home/nea/.local/bin/uv` (the documented gotcha that
    already broke `bump_version.py`). Every gate command states its interpreter explicitly — the house form is
    **`uv run python -m pytest …`**.
14. **Phase-column ↔ phase-file mapping**: `requirements.md` §8 uses a **four**-column phase axis
    **(i)–(iv)** — (i) soak/readiness/pool/hold, (ii) MCP off-loop + decoupling pin + leaks, (iii) MCP
    degradation, (iv) cross-cutting pins + staged verification — while the execution layer ships **three**
    phase files. **Mapping: (i) → `phase1-plan.md`; (ii) → `phase2-plan.md`; (iii) AND (iv) → `phase3-plan.md`**
    (group (iv) rides phase 3's final stage, because the pins are only meaningful once the behaviour they pin
    is implemented).

---

## 5. Traceability matrix — requirement → phase → implementation step → touch point → AC → safety property

One row per requirement (all 12). Every touch point was **opened and read in the worktree**; the ones marked
"re-verified" were re-checked by me against the on-disk file (the line numbers given by `requirements.md` §8,
which is the anchor authority per the precedence rules). Every acceptance criterion is a **named pin or named
observable** from `requirements.md` §3/§5.

| Req | Requirement (short) | Phase | Implementation step (the named change) | Touch point (file:line) | Acceptance criterion (named) | Safety property preserved | Status of narrowing |
|---|---|---|---|---|---|---|---|
| **A1** | Class-driven load grace window | **1** | Daemon-side **failure classifier** → per-tick **pool-state sampling** → **leak predicate** → **bounded grace state machine** as **additive fields** on `ReadinessComposite`; gate-side **reads the 503 body on the transient path** and applies the class-gated hold. The eligibility classes, the multi-component aggregation, the grace ceiling and the leak-signature rules are **defined once, in the normative register** — this file does not restate them. | `daemon/services/readiness.py:172-226` (composite), `:239-274` (`to_payload`/detail), `:45`/`:54`/`:59` (probe budgets), `:295-350` (components) · `daemon/api.py:3720-3820` (refresher), `:3777-3801` (per-tick probe construction), `:2472-2486` (refresher task) · gate: `scripts/upgrade/lib.sh:3005` (body read, green-only today), `:3148-3150` (`_json_sub`/`_json_field`) · `daemon/constants.py` (new budget constants) | **A5(i)** + **A5(iv)** (`requirements.md` §5) · the eligibility / aggregation / ceiling / leak rules and their pins live in the **register** — **not restated here** | **Never-green rollback intact.** The governing trigger is `requirements.md` §4.1 (P1); grace eligibility + aggregation are §4.2 (P2); the ceiling is §4.3 (P3); the leak signature is §4.4 (P4). **This file restates none of them.** | **Narrowed (N-4)** — the grace is a **class-driven load grace**: eligibility is **class-driven** (§4.2) — the daemon's age and the burst state are **published context only**, never arming preconditions. The 2026-10-09 v0.18.5 environmental class is covered by the same class-driven rule; it is **not** a post-boot special case. · **Normative source:** `requirements.md` §4.1 (P1) · §4.2 (P2) · §4.3 (P3) · §4.4 (P4) |
| **A2** | Soak semantics — "3 consecutive greens" reachable | **1** | The soak counter gains a **HELD** observation kind alongside GREEN and RESET, so a grace-eligible observation no longer resets the counter; the **`_PROBE_STATUS` aliasing defect is fixed in the same change** (each probe's status captured into a **distinct local** immediately after its own `_probe_once_classified` call, and the counter reads those locals — the probe-level global stays by design elsewhere); the soak window carries a **bounded extension**, and the extension decision emits a `soak_grace_extended` journal event. **The observation kinds, their preconditions and the bound are defined in the register** — not restated here. | `scripts/upgrade/lib.sh:5053-5248` (`gate_soak`), `:5216-5221` (the counter — **re-verified**: the aliased `$_PROBE_STATUS` is read twice), `:5161`/`:5181` (direct `_probe_once_classified` — **no retry**), `:5080-5109` (cap arithmetic), `:5088-5093` (the **pre-soak** `soak_deferred` event), `:146-149` (`ADAPTIVE_SOAK_*` constants), `:109` (`PROMOTE_TOTAL_DEADLINE_S=900`) | **A5(i)** + **A5(iii)** · the counter-semantics pin over the register's observation kinds, and the register's **required §5 leg: a grace-eligible daemon that NEVER greens still rolls back** | **Never-green + fail-fast-on-red intact.** The cap-reach trigger is `requirements.md` §4.1 (P1): **UNCONDITIONAL** — there is **no** fairness / observation-window condition and **no** cap-reach outcome that returns 0; a fairness observation is **diagnostic-only** and MUST NOT influence the return value. The ceremony-budget truncation case is the **pre-soak deferral** (`lib.sh:5088-5093`), **not** a cap-reach outcome. | **Narrowed (N-5)** — HELD does not advance the counter; the bound on consecutive holds is §4.2. **The never-green trigger itself is NOT narrowed** — it is unconditional (§4.1). · **Normative source:** `requirements.md` §4.1 (P1) · §4.2 (P2) |
| **A3** | Auto-continue hold during a promote txn | **1** | A **shared `promote_txn_live()` predicate** over the *existing* read-only journal accessor, consulted at **FOUR** dispatch seams; the hold module (**NEW** `daemon/services/promote_txn_hold.py`) owns a **per-seam deferred-candidates list** and a **lazily-created, module-held driver task** that re-consults the predicate on a bounded cadence, releasing deferred resumes through their **original** seams — the checkpoint-resume path, never a cold dispatch. A deferral, never a drop. **The liveness predicate, the boot-window rule, the release hysteresis and the driver contract are defined in the register** — not restated here. | `daemon/services/promote_txn_hold.py` **(NEW — the hold module: predicate + per-seam deferred list + the driver task)** · seam 1 `daemon/services/auto_continue_boot_pass.py:272` (`continue_running_instances_after_restart`), called `daemon/api.py:2703` — the pass runs **exactly once per boot** · seam 2 `daemon/manager.py:13671-13684` (the fire-and-forget `create_task` for `_resume_processing_background`) · seam 3 `daemon/services/eligible_pending_sweep.py:399` (`sweep_once()` — first tick immediate; always ON) · seam 4 `daemon/services/auto_continue_boot_pass.py:726-729` (`sweep_orphaned_live_turns`), wired `daemon/api.py:2708` — its orphans are **task-less**, so StaleTaskRecovery **cannot** reap them · readers: `daemon/tools/upgrade_journal.py:281-319` (`journal_owner_heartbeat_fresh`), `:156` (`OWNER_HEARTBEAT_FRESH_S = 60`), `daemon/tools/upgrade_tools.py:693-723` (`_journal_read`), `:388-431` (install-root resolution); stale-`in_flight` ownership is the **sweep** (`daemon/services/upgrade_journal_sweep.py:2061`) | **A5(i)** · the dedicated hold pin: (a) no resume task dispatched while the promote txn is live; (b) the deferred resume fires after the txn closes; (c) the hold is **observable** (deferral count); (d) with no txn present, behaviour is **byte-identical** to today; (e) a **read-only pin** — the hold path performs **zero** journal writes; (f) the register's required legs — a **seam-1 timing case** (the hold must be live during the daemon-boot window, when the heartbeat may legitimately be stale) and a **torn-`state.json` leg** (one torn read MUST NOT release the deferrals) | **No resume is lost** — the hold **defers, never drops**; human-initiated resumes are not gated; the release is guarded against a torn read. The liveness, boot-window, hysteresis and driver rules are **§4.7 (P7)** | **Narrowed (N-7)** — a deferral, never a drop, bounded and observable. **Seam coverage resolved by the council fold at FOUR** with the adjudicated **option (b)** release trigger. **D-A is moot.** The release rule is §4.7 (P7). · **Normative source:** `requirements.md` §4.7 (P7) |
| **A4** | Checkpoint pool sizing for the burst | **1** | Two new `LimitsConfig` fields — `checkpoint_pool_max_size` (default **10** = 7 writers + 1 probe + 2 headroom) and `checkpoint_pool_timeout_s` (default **5**) — plumbed as env + yaml + config field; a `timeout=` kwarg on the saver pool; **explicit cancellation of the probe's queued position** on budget expiry; **ONE probe mechanism** — the **non-blocking `checkpointer.raw_saver.lock.locked()` observation** (the acquire-and-read variant is **DELETED**: the sentinel read takes the **same non-reentrant** saver lock, so holding it across the probe's own read would self-deadlock and every production checkpoint write would queue behind the probe); the **dedicated-connection fallback is a contention mitigation only** — it bypasses the queue and therefore cannot observe queue growth, so detection stays with the lock classification + `detail.pool_state`; **pool-health surfacing** per pool in `detail.pool_state`. | `daemon/persistence.py:273-293` (psycopg saver pool, **P2** — **re-verified**: `min_size=1`, `max_size=5`, **no `timeout`** → 30 s library default), `:310-316` (asyncpg pool, **P3**) · `daemon/config.py:595` (`LimitsConfig.llm_concurrency`, `env_prefix="LIMITS_"`), `config.yaml:223` · `daemon/constants.py:91` (`WORKER_POOL_SIZE=5`), `:103` (`CHAT_WORKER_POOL_SIZE=2`) · the lock: `langgraph/checkpoint/postgres/aio.py:43` (decl), `:59` (assignment — **non-reentrant**), `:374` (taken by the sentinel read) · the retry proxy that passes non-method attributes through unchanged: `daemon/checkpoint_adapter.py:190-195`, `:227-229` | **A5(i)** · the config-resolution pin (the knob resolves through the house 4-tier ladder DB > env > yaml > default, and the saver pool is constructed with it); the **ghost-waiter** pin (N timed-out probes leave **0** waiting-queue entries); the **detail-payload** pin (pool-health fields present and **tagged per pool**); and the **lock-expression resolution pin** — the expression must resolve, because an unresolvable one would **silently disable** the grace | **Sizing must not become a DoS amplifier** — the implementer must **state the DB-side `max_connections` budget** and keep the pool within it; the default changes only with the operator opting in (D-B); **A4 does NOT claim to cure the leak** (N-3). The leak-signature oracle/advisory split and the streak-arm qualification are **§4.4 (P4)**; the class semantics the probe feeds are **§4.2 (P2)** | **Narrowed (N-3)** — sizing is *necessary, not sufficient*; the saver's single lock (not the pool size) is the contention point, and the pool is being poisoned underneath. **D-B:** default **10** (decisions.md D2) vs "default unchanged" (requirements.md A4 safety clause) → **10, conditional on stating the DB-side connection budget** (the safety property is binding) · **Normative source:** `requirements.md` §4.2 (P2) · §4.4 (P4) · §4.6 (P6) |
| **A5** | Regression pins (4 sub-cases) | **3** (final stage; cross-cutting) | Four **named, mutation-verified** pins on a **real-PostgreSQL** harness, under the §5 harness rules (per-variable env scrub, FAKE_HOME + escape guard, explicit boot isolation, no SQLite). | `tests/packs/…` (shell legs) + `tests/integration/upgrade/…` (real-PG legs) — e.g. `tests/packs/promote_soak_never_green_test.sh`, `tests/integration/upgrade/test_promote_soak_burst.py`; the house pack conventions apply to the final paths | **§5(i)** healthy-under-burst **not** rolled back (fake-probe leg + real leg; the real leg must show **no** `rollback`/`quarantine` journal event and `current` still on the staged version) · **§5(ii)** a genuinely broken release **is** still rolled back (return 1, `rollback` **and** `quarantine` journal events, `current` reverts to `previous`, `previous` not quarantined) · **§5(iii)** fail-fast on a real regression (failure within one cadence, ≤ ~20 s — a regression caught only at the 120 s cap is a FAIL) · **§5(iv)** the grace is observable and cannot mask indefinitely (detail-payload, leak-refusal and bound pins) | The never-green protection **is** the subject of (ii) and (iv); **(ii) outranks every other AC** — if any of its observables is missing, the change is rejected | **None** — but (ii)/(iv) are bounded by N-3/N-4/N-5, which constrain what they assert |
| **B1** | MCP warmup must not block the main event loop | **2** | (a) **work off-loop, await on-loop** (council W-3): `on_ready` is widened to `Callable[[], Optional[Awaitable[None]]]`; the `/proc` scan runs via `asyncio.to_thread(...)` (off-loop) and the transport **awaits the returned awaitable BEFORE `_ready.set()`** — so registration is complete before `__aenter__` returns. **Fire-and-forget is REJECTED**: a deferred scan that lands after the child exited returns `[]` ⇒ no reap handle, so the child can never be reaped — and the existing pin `tests/test_resource_pid_registry.py:505-510` requires registration-before-return. (b) `self._ready.wait()` gets a **bounded** wait (the value is **§4.6**), wired into the existing cancel-and-await path so expiry cancels the background task. (c) `streams_cm.__aenter__()` moves **inside** `asyncio.timeout(60)`. (d) **the synchronous secret/KMS resolution is hoisted off the loop** onto a dedicated `_SECRET_RESOLVE_EXECUTOR` at the **cold-spawn seam** (`connection_manager.py:229-237`, `warmup_pool.py:256-268`) — the incident's ×4 path — with fail-closed semantics preserved (a raising marker must still abort **before** spawn). Bounded and rare: **one** thread hop per spawned child, never per tick. | `daemon/mcp/stdio_wrapper.py:485-486` (`on_ready=self._stamp_children`; **re-verified** `:485`), `:266` (the hook type, to be widened), `:401-409` (the `on_ready()` call site and `self._ready.set()` at `:409` — the await must land **before** it), `:494` (`_stamp_children` def; **re-verified**), the synchronous reads at `:116` (`read_bytes`), `:132` (`read_text`), `:179` (`iterdir`), `:306` (`await self._ready.wait()` — **unbounded, re-verified**) · `daemon/mcp/warmup_pool.py:275` (`__aenter__()` — **re-verified**) vs `:278` (`asyncio.timeout(60)`), `:256-268` (the inline `resolve_secret_markers(resolve_env(...))` on the pool spawn path) · `daemon/mcp/connection_manager.py:229-237` (the same inline resolution on the **cold-spawn** path) · `daemon/manager.py:3137` (warmup task — a task is still on the loop) · the existing contract pin: `tests/test_resource_pid_registry.py:505-510` | **A5(i) loop-lag leg** — with a warmup in flight (including a deliberately hanging server) the observed event-loop lag stays within the **bounds held in `requirements.md` §4.6 (P6 — acceptance-criterion thresholds)** — the numeric values are stated **there only** — and `/livez` + `/readyz` answer within budget · **measurement shape**: a **periodic heartbeat task** (recording the delta between expected and actual wake time) run while the **R-1 concurrent burst** (MCP warmup + job-recovery + auto-continue resume) runs — the measurement is taken **while the burst is running**, never on an idle daemon, which would trivially pass · *rationale:* the smallest readiness probe budget is the **`SELECT 1` probe** (`daemon/services/readiness.py:45`), which needs **multiple loop turns** to service, so the lag bound must sit well below that budget for the probe to complete inside it — the numeric relation between the two is stated in **§4.6** · **a pin with no number is not a pin** — the number is in §4.6 and the pin cites it · plus the **bounded-wait pin** (the bound value is **§4.6**) asserting neither `stdio_wrapper.py:306` nor `warmup_pool.py:275` can block unbounded · plus the **W-3 shape pin**: `on_ready` schedules **and** the transport awaits the returned awaitable before `_ready.set()` (asserts `asyncio.to_thread` used, `_stamp_children` NOT executed inline, registration observable immediately after `__aenter__` returns — the `tests/test_resource_pid_registry.py:505-510` contract; **a fire-and-forget shape FAILS this pin**) | **MCP functionality is unchanged** — the offload is a scheduling change, not a semantic one; **readiness is not gated on it** (B2's pin); the **registration-before-return** ordering is preserved, so the existing reap guarantee is not weakened | **Scoped claim (decisions.md §5 B1)** — the *verified* loop-blocking work moves off the loop and the unbounded handshake is bounded; the warmup is **not** made "free" (the child's 88–156 % CPU is a child cost; the daemon's 105–114 % was the loop starving on other work). **Fold W-3 supersedes the earlier "eventually consistent" shape** — the scan is off-loop but **awaited on-loop**. **Thresholds: `requirements.md` §4.6 (P6)** · **Normative source:** `requirements.md` §4.6 (P6) · §4.5 (P5) |
| **B2** | Readiness must not be gated on MCP warmup | **2** (narrowed) | A **regression pin** proving MCP-independence of the readiness composite's four components and of the `pending-count` path; the **B1** fix is the actual remedy. **No decoupling mechanism is built** — the logical coupling does not exist. | `daemon/services/readiness.py:295-350` (the four components: `database` / `queue_freshness` / `services` / `checkpoint_saver` — zero MCP references) · `daemon/routers/prompts.py:1078-1102` (`pending-count` — a plain DB count; its 503s come from `_require_auth_on` → `AUTH_NOT_CONFIGURED` or `_get_service` → `PROMPTS_UNAVAILABLE`) | **§5(i) MCP-independence leg** — the pin asserts (i) the four component names and their probe call-sites reference **no MCP symbol**, and (ii) `pending-count` returns its normal shape when MCP warmup is deliberately failing | **MCP must not become a readiness dependency** — now enforced by a **pin** rather than by a (nonexistent) decoupling | **Narrowed (N-1)** — pin + the B1 remedy; **no decoupling mechanism specified**. The RCA's 20:42:39 `pending-count` 503 is recorded as **UNPROVEN** degradation evidence |
| **B3** | No child leaks | **2** | Bound the two unbounded waits (shared with B1) so the existing `except BaseException` cleanup block at `warmup_pool.py:333-344` becomes **reachable**; **prevention** via the C1 quarantine (which stops the spawn) is primary; a **scoped** live reaper for the children of a quarantined server (the boot sweep's predicate is structurally ineligible for within-incarnation children — it requires the child's `starttime` to be **older** than the daemon's) | `daemon/mcp/warmup_pool.py:333-344` (existing cleanup — `session.stop()` then `streams_cm.__aexit__(*sys.exc_info())`; **re-verified**), `:285-310` (the 3 × `asyncio.timeout(10)` init retry loop) · `daemon/mcp/stdio_wrapper.py` (child stamp / teardown, `:485`, `:489-491`) · `daemon/services/boot_subprocess_sweep.py:21-23` (existing reaper, **stays in place**), `:38-42` (the `starttime` predicate), `:67`/`:120` (the kill-switch) | **§3-B3 pid-liveness pin** — force an init failure (a server whose command never speaks the protocol) and assert **0** surviving children matching the spawned command, by **pid-liveness** against `/proc/<pid>/stat` starttime (PID-recycle-safe), plus the session/stream closed, plus no phantom connection left in the pool | The **pre-existing boot reaper stays in place** as the last line of defence — B3 does **not** replace it | **Narrowed (N-2)** — the "unclosed aiohttp session" clause is **routed to a follow-up with the mechanism explicitly unverified** (`grep -rn aiohttp daemon/mcp/` → **0 hits**); this commission must not claim to have fixed an unverified leak — it fixes the two it can prove |
| **C1** | Circuit-break the warmup | **3** | An **init-path guard** hosting the **one** existing `CircuitBreaker` primitive (using the lock-protected `record_failure`/`record_success`, **never** `reset()` concurrently), wired to a **per-process, per-boot quarantine registry** whose **single home is the leaf module `daemon/mcp/init_guard.py`** (imports **ONLY** `daemon.sources.circuit_breaker` + stdlib — no `daemon.mcp`, no `daemon.services`, no `daemon.config`, so it is importable from anywhere without an import cycle; `base.py` imports init_guard, never the reverse). The registry is consulted by `is_available()` so a quarantined server disappears from **every** path. N failures within the boot → breaker OPENS → quarantine; `record_success` closes it; a **manual** reset path mirrors `ResilienceManager.reset_circuit` but goes through the lock-protected path. **`reset_for_tests()`** is exposed **and pinned** (per-process state would otherwise leak across tests). **`reset()` is never called** — documented in the module docstring and pinned. The **plane** override's `super()` bypass is fixed so the quarantine is consulted for **plane** too. | `daemon/mcp/init_guard.py` **(NEW — the ONE home, W-4)** · `daemon/mcp/builtin_servers/base.py:125-152` (`is_available()` classmethod — imports init_guard and consults the registry **before** the `required_package` check; `:143` `required_package is None ⇒ True`), `:262-289` is the **plane** override (`daemon/mcp/builtin_servers/plane.py:262-289` — `is_available()` at `:263`, **never calls `super()`**, so a quarantined plane would slip the gate) · **FOUR** consult sites: `daemon/manager.py:2813`, `:3057`, `daemon/services/mcp_service.py:579` (warm path) and `:1130` (the **cold** path) — **re-verified** · `daemon/sources/circuit_breaker.py:20` (the **only** breaker class), `:32-38`/`:40-76` (CR-4 single-probe-in-flight guard), `:78-108` (lock-protected mutators), `:114-124` (`reset()` — the only unsynchronised mutator) · `daemon/mcp/resilience.py:45` (the import proving there is one implementation), `:470-635` (`ResilienceManager` — the **wrong layer**: tool-calls only, never allocates a breaker for a server with no `ResilienceConfig`) · `daemon/mcp/warmup_pool.py:552-583` (`_replenish`), `:711-726` (the health loop that re-runs init forever) · **operator surface**: `daemon/models/mcp_server.py` (`McpServerInfo` gains an additive optional `health_state` field — `ok`/`degraded`/`quarantined`/`null`) and `daemon/routers/mcp_servers.py:256` (`_mcp_server_to_info`) populate it from the init_guard registry | **§3-C1 quarantine pin** — a server whose command always fails: after N attempts it is **quarantined for the boot** (no further spawn attempts), the quarantine is visible in the **log**, in the **journal**, and in the **observability surface** (readiness `detail.mcp` **and** the servers-list `health_state`), and the **other** servers are unaffected (**degrade alone**) — asserted at **all FOUR** consult sites, the cold path included · plus **W-4a**: a pin that quarantines a server, asserts the gate closes, calls `reset_for_tests()`, and asserts the gate re-opens · plus **W-4b/S11**: a pin asserting `CircuitBreaker.reset()` is **never** called on the path (e.g. monkeypatching it to raise) | **One server's failure must not take down the pool or the daemon** (C4); the breaker's `reset()` is never called from an unsynchronised path; the quarantine is **per-boot and in-memory** ⇒ a restart clears it | **Narrowed (N-8)** — the registry is a **per-process** one because `is_available()` is a **classmethod** and cannot carry per-instance boot state; the fold adds the pinned `reset_for_tests()` (W-4a) and the **`reset()`-never-called** rule (W-4b/S11). **The registry home is `daemon/mcp/init_guard.py`** — this **supersedes** the earlier `daemon/mcp/builtin_servers/__init__.py` home. The class that quarantines is defined in **§4.2 (P2)** / **§4.4 (P4)** · **Normative source:** `requirements.md` §3-C1 · §4.2 (P2) · §4.4 (P4) |
| **C2** | Fail fast on dead API hosts | **3** | A **bounded, off-loop, per-attempt** DNS pre-resolve against the **merged env the child would actually use** (row `config.env` overlaid on the definition's `build_config({})["env"]` — row env wins on every key). The resolved address is **discarded, never pinned**. A failure is a **quarantine signal** (one failure, not N), never a boot failure. **The bound and the classification are defined in the register** — not restated here. | `daemon/mcp/warmup_pool.py:253-310` (the init + pre-resolve seam; the 3 × `asyncio.timeout(10)` retry at `:285-310`), `:278` (the 60 s outer budget), `:852-897` (`build_pooled_stdio_config` — the merged-env precedence) · `daemon/mcp/connection_manager.py:229-242` (`resolve_env` → `resolve_secret_markers` → `StdioServerParameters(env=...)`) · the SDK's `get_default_environment()` allow-list (HOME/LOGNAME/PATH/SHELL/TERM/USER) — the daemon's ambient env **cannot** reach the child | **§3-C2 DNS-refusal pin** — a server whose resolved base host does not resolve: the init **refuses** (no child spawned) within the register's bound — **2.0 s + spawn overhead**, and in all cases far below the 30 s package-side hang budget — the server is marked **degraded** with the DNS reason, and the readiness path is unaffected. **Threshold source: `requirements.md` §4.5 (P5)** | **A dead host must not be able to consume the boot budget**; the pre-check is bounded and never worse than today's behaviour; a resolver hiccup can never permanently disable a server (the classification is §4.5) | **Scoped (decisions.md §5 C2)** — bounded, off-loop, per-attempt, against the resolved base, never pinning a stale IP. **The numeric bound and the classification are §4.5 (P5)** · **Normative source:** `requirements.md` §4.5 (P5) · §3-C2 |
| **C3** | Pin the context7 API base in the built-in template | **3** | Two `section: "env"` **schema fields**, per the **G1-corrected contract**: `CONTEXT7_API_URL` — **the effective lever** the package actually reads — and `CONTEXT7_BASE_URL` — a **daemon-side MIRROR**: the package treats it as a hardcoded const and does **not** read it from the env, so the field exists to feed the C2 derivation and its operator-facing description must **state the inertness**; the **schema-version bump is the remedy** for a future host move. Plus a **validating** `build_config` override (reject a non-`http(s)` scheme and embedded userinfo) and a `schema_version` bump **"2" → "3"**. The sanctioned write path is `/configure-builtin`; `mcp_set_env` **keeps its current behaviour in this lane** (adjudication #1). **The schema-version heal must be an ENV-PRESERVING MERGE, not a whole-config replace** (approver BLOCKING 3): the new config is `definition.build_config({})` with the row's existing `config.env` **merged over it** (row env wins — the same precedence `build_pooled_stdio_config` pins) and any key the row carries that the new schema does not declare is **retained** (not dropped), so a schema narrowing cannot silently discard a KMS marker. **C2's DNS pre-check must read the EFFECTIVE post-heal env** — the merged result the child will actually receive — not the pre-heal row and not the bare template defaults. **REJECTED alternative — disclosed-discard** (keep the whole-config replace and disclose that the operator's pin is lost on upgrade): rejected because it defeats C3's stated purpose — the operator's pin would be **silently** deleted on the first post-upgrade boot, the operator would re-apply it every upgrade, and the failure mode is silent. Disclosure is necessary but not sufficient; the correct engineering answer is to **preserve** the value. | `daemon/mcp/builtin_servers/context7.py:33-47` (base config with **no `env` key**; `get_config_schema()` → `[]`; `schema_version` `"2"` at `:30`) · `daemon/mcp/builtin_servers/base.py:222-223` (`env[key.upper()] = str(value)` for `section == "env"`), `:172-244` (`build_config` — the value-resolution path), `:241-242` (the `env` key is **omitted when empty** — **re-verified**) · `daemon/manager.py:2855-2867` (**the schema-version heal — a whole-config replace via `update_mcp_server(..., config=default_config, ...)` that would DROP the operator-set env and any KMS-marker keys on the first boot after the 2 → 3 bump; the env-preserving merge lands here**) · `daemon/mcp/warmup_pool.py:852-897` (`build_pooled_stdio_config` — the merged-env precedence, **row env wins on every key**; the C2 derivation must read this effective merge) · `daemon/models/mcp_server.py:69` (`section: Literal["args","env"]` — already modelled) · `daemon/routers/mcp_servers.py:491` (`/configure-builtin`), `:760-768` (403 `BUILTIN_SERVER_PROTECTED`), `:576-592` (the 409 cross-schema-drift refusal) · `daemon/tools/infra.py:1527` (`mcp_set_env` — the unguarded hole, **not closed here**) | **§3-C3** — (a) the schema is **non-empty** and `build_config({})` produces the expected env var names, and the **derived** base (`CONTEXT7_API_URL` else `${CONTEXT7_BASE_URL}/api`) resolves to the intended host; (b) the configured value reaches the **child process environment** via `build_pooled_stdio_config` → `StdioServerParameters(env=…)` — asserted on the **constructed parameters**, not on the daemon's own env; (c) the sanctioned write path works and the un-ruled path is ruled; (d) **regression pin** — the built-in's `config` PUT still returns **403**, and **no `CONTEXT7_API_BASE_URL` entry exists** (the withdrawn schema-entry name); (e) **the heal-survival pin (BLOCKING 3)** — `config.env` **survives the 2 ⇒ 3 heal with the operator value intact**: a test writes a marker-bearing operator env onto the row, drives the heal, and asserts the post-heal `config.env` still contains the operator's key **with its value**, that the **KMS-marker key is still present**, and that the template-derived defaults were added **alongside (not instead of)** them; (f) the C2 pre-check is asserted against the **effective post-heal env** (the merged value the child receives), **not** the default host. PLUS the §5 harness rule: **route-table readback pin** (a router is touched) | **Protected-builtin immutability preserved** — the 403 path stays; C3 adds a **schema**, not an exemption. **Disclosed, bounded rollback cost:** a row stamped `"3"` is refused by `/configure-builtin` (409) if the definition reverts to `"2"`, until the next boot heals it | **N-6 is SUPERSEDED by adjudication #1** (decisions.md §3g G6 governs). The `mcp_set_env` built-in hole is **left open**, recorded as a **separate defect with an owner**; any future closure must be an **allow-list of operator-pinned keys for built-ins**, **not** a blanket ban, because the BYOK self-provisioning chain depends on the tool. **The heal is env-preserving (BLOCKING 3)** — the operator's pin and the KMS markers survive the 2 → 3 bump, and **disclosed-discard is rejected**. The G1-corrected two-field contract is `requirements.md` §3-C3 · **Normative source:** `requirements.md` §3-C3 · §4.5 (P5) · `architecture-recommendation.md` §8.3 |
| **C4** | Degraded MCP servers degrade alone | **3** | A **per-server degraded registry** (server, why / failure class, since, failure count, current state `ok`/`degraded`/`quarantined`) — in-memory, per-boot, **no new table** — plus an **additive `detail.mcp.<server>` map** in the `/readyz` payload, a **journal line per state transition** (not per attempt), recovery via the breaker's half-open path, and the one-line **stale `reasons` reader fix**. **The daemon-side journal write is SUPPRESSED while `promote_txn_live()` is true** (approver BLOCKING 4): `journal_history_append` (`daemon/tools/upgrade_journal.py:391-405`) is an **unlocked** read ⇒ mutate ⇒ **whole-document write** (`journal_write`, `:331-371`; the "caller discipline still serializes journal writes externally; this is the inner ring" comment at `:352-353`), so a **stale-snapshot** write taken while a live promote txn owns `state.json` would **DROP `in_flight`** — and the A3 hold predicate would then read `in_flight` as absent and **RELEASE the deferred resumes MID-PROMOTE**, the exact burst A3 exists to suppress. Rules: the suppression gate lives at the **C4 / `init_guard` state-transition emit** (the only daemon-side writer this lane introduces — not in `journal_write` itself, whose blast radius would be far wider); a **suppressed write is BUFFERED in-memory**, bounded to **one entry per `server` + `state`** with **newest wins** (the buffer cannot grow without bound); the buffer is **flushed** to the journal when the predicate flips **false** (the promote txn closes) **or at graceful shutdown** (the ordered-shutdown path); and a buffered entry is **NEVER dropped** — if the daemon dies first the entry dies with the process, which is exactly why the flush is registered on the ordered-shutdown path and why the transition is **also** visible in the in-memory `detail.mcp` surface for the whole boot. **REJECTED alternative — lock-acquire** (the daemon-side writer takes the ceremony's `journal_lock` before writing): rejected, because `journal_lock` belongs to the **shell ceremony** — a daemon-side acquire mid-promote is a **new cross-process contention point** and risks **ceremony interference** (it could block the ceremony's own writer, or hold the lock across the ceremony's commit path). The MCP state transition is **deferrable observability, not correctness**, so deferring it is strictly cheaper than contending for the ceremony's lock. | new per-server degraded registry (in-memory, per-boot — **NO new table, NO migration**) · `daemon/services/readiness.py:239-266` (the `detail` payload — **the shared surface with A1/A4**) · `daemon/tools/upgrade_tools.py:2088-2092` (the stale `readyz.get('reasons', [])` top-level read — **re-verified**) · `daemon/tools/upgrade_journal.py:391-405` (`journal_history_append` — the unlocked read⇒mutate⇒whole-document write **that BLOCKING 4 suppresses**), `:331-371` (`journal_write`), `:352-353` (the caller-discipline comment) · the promote-txn predicate is A3's `promote_txn_live()` (`daemon/services/promote_txn_hold.py`), which the C4 emit consults | **§3-C4** — with one server permanently broken: (a) the **daemon stays up**; (b) `/readyz` components are **unaffected** by the MCP failure; (c) the registry reports the **server, the reason and the timestamp**; (d) after the breaker's recovery window the server can **return**, and the registry records the recovery · plus the **BLOCKING-4 pin**: while a synthetic promote txn is live, the state transition is **buffered, not written** (the scratch `state.json` is byte-unchanged across the transition, and `in_flight` survives), the buffer is **flushed** on txn close with the entry **present and not lost**, the buffer is **bounded** (one entry per server+state, newest wins), and the flush also fires on the **graceful-shutdown** path | **Readiness must never be gated on MCP** (B2's pin) · the **daemon must never restart** on a degraded MCP server (ADR-005 "degrade, never restart") · the surface is **additive** (older consumers tolerate it — the `detail.inflight_turns` precedent) · **the daemon must never write `state.json` during a live promote txn** — that write path is the ceremony's, and losing `in_flight` would release A3's deferred resumes mid-promote | **None** — decisions.md §3h folds the `upgrade_tools.py` reader fix into this lane (it is the difference between "reportable" and "always reports nothing") and pins "no new table ⇒ no migration"; **BLOCKING 4 adds the suppression** so the daemon-side emit cannot interfere with the ceremony-owned journal. The requirement row is `requirements.md` §3-C4; the binding implementation detail is `architecture-recommendation.md` §9.3 · **Normative source:** `requirements.md` §3-C4 · `architecture-recommendation.md` §9.3 · §4.7 (P7) |

> **Normative source: `requirements.md` §4.1–§4.7 — the SINGLE NORMATIVE HOME for this commission's
> cross-cutting predicates.** Every predicate-bearing statement in this plan is a **pointer** to that register,
> never a restatement; if a restatement here ever appears to disagree, **§4 wins**. The map:
> **§4.1 (P1) never-green trigger** → A2, A5(ii), §7 · **§4.2 (P2) grace eligibility + multi-component
> aggregation + counter observation kinds** → A1, A2, A4, C1 · **§4.3 (P3) grace ceiling + its clock** → A1 ·
> **§4.4 (P4) leak signature + streak-arm qualification** → A1, A4, C1 · **§4.5 (P5) DNS-refusal thresholds**
> → C2 · **§4.6 (P6) acceptance-criterion thresholds** → A1, A5, B1, C2 · **§4.7 (P7) A3 release rule** → A3.
> The other seven artifacts (`decisions.md`, `architecture-recommendation.md`, `phase1/2/3-plan.md`,
> `risk-safety-register.md`) **reference** the register; this file does the same.

### 5.1 Divergences found between the two sibling artifacts (beyond the three adjudications)

Resolved by the precedence rules: **observed numbers** → `requirements.md`; **what to do** → `decisions.md`;
**safety properties** → never weakened.

| # | Divergence | Resolution applied in this plan |
|---|---|---|
| **D-A** | **A3 dispatch coverage.** `decisions.md` §3c C3 covers **three** dispatchers (boot auto-continue pass + runtime resume dispatch + eligible-pending sweep). `requirements.md` N-7 says "**exactly two**" and drops the boot pass. | **MOOT — resolved by the reviewer-council fold at FOUR seams** (boot pass + runtime resume dispatch + eligible-pending sweep + the task-less-orphan sister pass `sweep_orphaned_live_turns`, whose orphans StaleTaskRecovery cannot reap). The fold also adjudicated the **release trigger** (C-1, planner option (b)): the hold module owns a per-seam deferred-candidates list and re-consults the promote-txn predicate on a short bounded cadence (read-only `state.json`, default 5 s, only while deferrals are outstanding), releasing through the **original** seams — the checkpoint-resume path, never a cold dispatch. The A3 row reflects the folded shape. |
| **D-B** | **A4 default pool size.** `decisions.md` D2 ships `checkpoint_pool_max_size` default **10**; `requirements.md` A4's safety clause says keep the default **unchanged** unless the operator opts in. | **10, conditional on stating the DB-side `max_connections` budget** and keeping the pool within it (the *safety* property is binding and wins). If the budget cannot be shown to accommodate it, the default stays at 5 and the knob is operator-opt-in. |
| **D-C** | `QUEUE_PROBE_TIMEOUT_S` anchor. `requirements.md` §3-A1 says `daemon/services/readiness.py:60`; the observed definition is at **`:59`** (`decisions.md` premise 24 is correct). | Cite **`:59`** (one-line slip; semantics identical). |
| **D-D** | The "omit `env` when empty" anchor. `requirements.md` P2 says `base.py:234-238`; the observed `if env: result["env"] = env` is at **`:241-242`** (`decisions.md` premise 8 is correct). | Cite **`:241-242`** (one-line slip). |
| **D-E** | The SQLAlchemy pool has **two** construction sites in `daemon/repositories/factory.py`: the config-driven build (`:218-220`; defaults at `:136-137`; `pool_pre_ping=True` at `:188` and `:220`) and the inline `create_engine` in `create_postgres_engine` (`:326-328`; the `def` at `:256`; `decisions.md` cites `:325-330`). | Both are real; every sentence in this plan that names "the SQLAlchemy pool" **names the site explicitly**. `reset_on_return` is **not** overridden anywhere ⇒ SQLAlchemy 2.0.48's default `'rollback'` applies (this is the substance of non-goal 5). |
| **D-F** | `eligible_pending_sweep.py` — `sweep_once()` is called at **`:399`** (the `while not self._stop_event.is_set():` line is `:397`); **re-verified on disk** and confirmed by the architecture layer's anchor reconciliation. | Cite **`:399`** (two-line slip; semantics identical). |
| **D-G** | The readiness refresher task is created at `daemon/api.py:2472` (`requirements.md`); `decisions.md` says `:2471`. | Cite **`:2472`** (one-line slip). |
| **D-H** | `daemon/services/mcp_service.py:1130` is a **fourth** `is_available()` call site (inside the preload gate helper) beyond the three governed by adjudication #2. | **Not a re-litigation of adjudication #2** — the adjudication governs which anchors are cited as *the seam*. **Resolved by the fold's placement (W-4):** the registry consult now lives **inside** `is_available()` in `daemon/mcp/builtin_servers/base.py`, so this fourth site inherits the quarantine with **zero edits**. The C1 row now asserts the **FOUR** consult sites, the cold path included. |
| **D-I** | **Never-green trigger — three different answers across the set** (the **superseded** design-layer wording made the rollback conditional on a "fair observation window"; the requirements layer and this file said "unconditional"; the risk register said "diagnostic only"). | **UNIFIED — `requirements.md` §4.1 (P1) governs: the cap-reach trigger is UNCONDITIONAL.** There is no fairness / observation-window condition on the rollback path and **no cap-reach outcome that returns 0**; the ceremony-budget truncation case is the **pre-soak deferral** (`lib.sh:5088-5093`), not a cap-reach outcome; a fairness observation is **diagnostic-only** and MUST NOT influence the return value. The required §5 leg — a **grace-eligible daemon that never greens still rolls back** — is now in the A2 row and in §7. |
| **D-J** | **Grace ceiling — two clocks and a valueless term** (`grace_abs_ceiling_s` — **superseded, deleted** — boot-relative, no value, vs. an observation-relative horizon). | **UNIFIED — `requirements.md` §4.3 (P3) governs: ONE clock (observation-relative), ONE number (`GRACE_HORIZON_S = 120.0` s), no boot-relative ceiling** (the valueless term is deleted), and the soak's absolute bound is `soak_entry + 300 s`. The A1 row points at §4.3 and states no number of its own. |
| **D-K** | **D1a named two incompatible contention probes** — the acquire-and-read form and the non-blocking `.locked()` form. | **UNIFIED — the non-blocking `checkpointer.raw_saver.lock.locked()` observation is the ONE mechanism**; the acquire-and-read variant is **DELETED** as a self-deadlock on the **non-reentrant** saver lock (the sentinel read takes the same lock, so holding it across the probe's own read would make every production checkpoint write queue behind the probe). The A4 row states the single mechanism + the resolution pin and points at §4.4/§4.2. |
| **D-L** | **Cold-path ownership ambiguous** between phase 2 and phase 3 (the secret/KMS offload at `connection_manager.py:229-237` sits on the cold spawn path). | **RESOLVED — the cold path belongs to PHASE 2** (B1's offload list), together with `warmup_pool.py:256-268`; phase 3 owns the init-path guard and the DNS pre-check, not the offload. |
| **D-M** | **`is_available()` consult-site count** — the phase plan said "three"; the tree has **four** (the cold path at `daemon/services/mcp_service.py:1130`). | **RESOLVED — FOUR consult sites**, asserted by the C1 pin: `daemon/manager.py:2813`, `:3057`, `daemon/services/mcp_service.py:579` (warm) and `:1130` (cold). The C1 row carries the four-site list. |

---

## 6. Risks (summary)

The **full** risk set — each with its mitigation, owner, trigger, and the safety property it protects — lives in
the sibling artifact **`risk-safety-register.md`**. This is the summary view: the risks that would most damage
the commission if they materialised.

| # | Risk | Why it matters here | Containment (must be in the register) | Linked requirement / pin |
|---|---|---|---|---|
| **R1** | **The grace masks a real broken release.** A permissive classifier, or a grace-INELIGIBLE class misclassified as grace-eligible, would let a genuinely broken release escape the never-green rollback — the exact failure the commission is fixing. | The commission's own thesis is "a degraded daemon must stay visible"; a masking grace inverts it. | Fail-closed `unknown` class (never grace-eligible); the `pool_leak` class is **never** grace-eligible; grace **never renders healthy**; `detail.reasons` never suppressed; doubly bounded (hard expiry + extension cap). | A1, A5(ii), A5(iv) |
| **R2** | **The never-green rollback is weakened by the counter change.** If a HOLD were counted as a green, if `MAX_HOLDS_PER_GREEN` were unbounded, if the `_PROBE_STATUS` aliasing fix were done wrongly, or if the window extension were unbounded, a never-green release would survive. | The safety property is the whole point; a subtle weakening here is invisible until a bad release ships. | HOLD ≠ count; bounded holds; bounded, non-refunded extension; per-probe status locals; the outer `PROMOTE_TOTAL_DEADLINE_S = 900` remains the ceiling; conservative mode retained byte-identical as the operator's strict escape hatch. **Normative source: `requirements.md` §4.1 (P1) + §4.2 (P2).** | A2, A5(ii), A5(iii) |
| **R3** | **A child process leaks.** The two unbounded waits can hang an init *before* the cleanup block, orphaning `npm exec`/node children; the boot sweep **cannot** reap within-incarnation children (its `starttime` predicate is structurally ineligible for them). | Orphans at 88–156 % CPU are the observed mechanism of the init storm; leaking them re-creates the incident. | Bound both waits (B1) so the existing cleanup is reachable; the C1 quarantine **prevents** the spawn; a **scoped** live reaper for a quarantined server's children; the boot reaper stays as the last line of defence. | B1, B3, C1 |
| **R4** | **The event loop is starved again.** A thread hop that lands on a saturated default executor, a new `to_thread` on a hot path, or a DNS pre-check executed on the loop as a "cheap synchronous check" would reproduce the 105–114 % CPU peg. | The single asyncio thread is the daemon's whole concurrency model; one blocking task pegs it. | One thread hop per **spawn** (bounded and rare, never per tick); the DNS pre-check **must** run off the loop; the thread must not add concurrency pressure; loop-lag pin with a **stated** bound. | B1, C2 |
| **R5** | **Live state is touched.** A staged-release boot smoke is **not** isolated (path derivation reaches the LIVE install root and can bind/prune the live promote journal); `env -u POSTGRES_*` is a silent no-op so an ambient `POSTGRES_DB=ensemble_prod` can survive a "fenced" run; test scripts that resolve HOME-derived install paths have deleted the live install twice. | Six ambient-env boots have hit LIVE `ensemble_prod`; two live-install deletions are on record. | Per-variable env scrub; FAKE_HOME + escape guard; explicit isolation for every boot smoke; scratch real-PostgreSQL; the A3 hold is **read-only** against the journal (it must never write `in_flight`). | A5 (harness), A3 |
| **R6** | **A1/A4 mask or misrepresent the pool leak.** Presenting a sized pool as a fix, or letting a leak-class failure be grace-held, converts a self-sustaining defect into a silently-held "grace" state. | The leak is the root cause of the 22:10–22:50Z incident and is explicitly **out of scope** here. | The `pool_leak` class is grace-ineligible and **disengages** the grace; the mandatory A4 honesty clause; pool-health surfacing so the poison is **visible**; the leak-repair commission is commissioned separately (open item 7). | A1, A4 |
| **R7** | **The `mcp_set_env` built-in hole stays open.** The context7 base URL remains settable by an unsanctioned route while the sanctioned path is being built. | Adjudication #1 keeps the tool's behaviour, because the BYOK self-provisioning chain and the operator's 21:40 remediation both depend on it. | The hole is **recorded as a separate defect with an owner**; any future closure is an **allow-list of operator-pinned keys**, never a blanket ban; C3's outcome (settable via the protected, schema-backed, validated path) is delivered regardless. | C3 |
| **R8** | **The context7 `schema_version` bump has a rollback cost.** A row stamped `"3"` is refused by `/configure-builtin` (409) if the definition reverts to `"2"`, until the next boot heals it. | The rollback path is not byte-identical; the commission must disclose it rather than discover it during an incident. | Disclosed, bounded, and pinned: the boot path heals the drift (`daemon/manager.py:2775-2779`, preserving `is_active`), and the 409 carries an actionable message. | C3 |
| **R9** | **A size default exceeds the database's own connection budget.** Raising the saver pool's `max_size` above what the DB allows converts a readiness problem into a connection-exhaustion outage — a **new** failure class. | The commission must not trade a degraded daemon for an unreachable database. | The implementer must **state the DB-side `max_connections` budget** and keep the pool within it; the default changes only with the operator opting in (D-B). | A4 |

---

## 7. Success criteria — what "done" looks like for the whole commission

1. **The A5 four sub-cases all pass**, on a real-PostgreSQL harness, under the §5 harness rules:
   - **(i)** a healthy release under a simulated burst is **not** rolled back (fake-probe leg + real leg; the
     real leg writes **no** `rollback`/`quarantine` journal event and `current` still points at the staged
     version);
   - **(ii)** a genuinely broken release **is** still rolled back (return 1, `rollback` **and** `quarantine`
     journal events, `current` reverts to `previous`, `previous` not quarantined) — **this sub-case outranks
     every other acceptance criterion in the commission**;
   - **(iii)** the soak still **fails fast** on a real regression (within one cadence, ≤ ~20 s — a regression
     caught only at the 120 s cap is a FAIL);
   - **(iv)** the grace is observable and cannot mask a real fault indefinitely (detail-payload pin, leak-refusal
     pin, bound pin).
2. **B1's event-loop lag is bounded by a NUMBER — and the number is stated ONCE, in `requirements.md` §4.6
   (P6 — acceptance-criterion thresholds).** *Measurement shape:* a **periodic heartbeat task** (the delta
   between expected and actual wake time) run while the **R-1 concurrent burst** runs — MCP warmup +
   job-recovery + auto-continue resume — with a deliberately hanging MCP server in flight, and with `/livez` +
   `/readyz` answering within their budgets throughout. *Rationale:* the smallest readiness probe budget is the
   **`SELECT 1` probe** (`daemon/services/readiness.py:45`), which needs **multiple loop turns** to service, so
   the lag bound must sit well below that budget for the probe to complete inside it — the numeric relation
   between the two is stated in **§4.6**. The measurement is taken **while the burst is running** — an
   idle-loop measurement would trivially pass and would prove nothing about the incident. **No threshold-free
   lag AC remains anywhere in this plan**: the threshold lives in the normative home and every layer cites it.
3. **No new table and no new migration.** The C4 registry is in-memory and per-boot; the A1 grace state and the
   A4 pool state live in the readiness composite. The tree carries **115** migration files with **115** distinct
   prefixes today and this commission adds **zero** — no `daemon/migrations/versions/*.sql` file is created.
4. **Conservative mode is still available as the day-one mitigation.** `ENSEMBLE_SOAK_CONSERVATIVE=1`
   (`scripts/upgrade/lib.sh:5065-5067`, `:5113-5120`) remains **byte-identical** — grace and amnesty disabled,
   flat 300 s, no early exit. It is *not* a fix (it is the opposite of the fix); it is the existing knob that
   makes the gate's behaviour predictable while this work is in flight, and it is the operator's immediate
   escape hatch.
5. **The never-green rollback protection survives intact — this is the governing safety property of the
   commission.** The predicate itself is **`requirements.md` §4.1 (P1)**, and §4.1 states it **unconditionally**:
   at cap-reach, `consecutive_green < 3` ⇒ `return 1` ⇒ ADR-005 auto-rollback (+ quarantine) — **no** fairness
   / observation-window condition, and **no** cap-reach outcome that returns 0; the ceremony-truncation case is
   the **pre-soak deferral** (`lib.sh:5088-5093`), not a cap-reach outcome; any fairness observation is
   **diagnostic-only** and MUST NOT influence the return value. In this plan's own words, the safety property
   is that the daemon never lies (grace never renders healthy; the underlying `detail.reasons` are never
   suppressed) and that **a release that never greens still ends at cap-reach below 3 ⇒ return 1** — including
   the required §5 leg, a **grace-eligible** daemon that never greens. **Anything conflicting with this is
   narrowed, not weakened.**
6. **INTERIM EFFICACY — the disclosed tradeoff (council R-2 / risk register R-MASK-11).** With non-goal #3 (the
   `queue_freshness` probe's own transaction leak) **still live**, its signature **correctly** fires the
   `pool_leak` class and the grace therefore **refuses BY DESIGN** — that is A1 working as specified (A1 is built
   so it **cannot mask** the leak), not a defect. The disclosed consequence: **the healthy-release rollback CAN
   RECUR** for that signature until the follow-up transaction-discipline commission lands. Therefore this
   commission's **interim primary objective is carried by C1 / C2 / A3 / A4**, not by the grace — the grace covers
   **only the busy-but-not-leaking** signature. *Observable:* under sustained load with the leak live, the
   readiness `detail` shows `grace_active=false` **together with** a leak indicator (the pool-state surface is
   what makes the rollback diagnosis instant), and the pool-state + leak signal appear in the journal/reports.
   **This criterion is the disclosure itself**: the fold's outcome is that the leaking case fails **loudly and
   correctly** rather than being silently absorbed.
7. **The live release (v0.27.8) and the live install are untouched** by this commission; every daemon-side
   change goes through the normal **stage → promote** path, and the shell-side change is **committed and
   shipped in the repo** even though it needs no release bump.
8. **Every acceptance criterion is machine-checkable** — a named pin, a command, or a named observable — and
   every pin that asserts a regression is **mutation-verified** (red at the base, green at the head).

---

## 8. Adjudications recorded

The caller ruled on three points where the two sibling artifacts disagree. They are recorded here so a reviewer
can see the divergence was **caught and resolved**, not missed.

**Adjudication 1 — `mcp_set_env` ruling: `decisions.md` §3g G6 GOVERNS.**
The tool keeps its current behaviour in this lane. The missing `is_builtin` guard is **recorded as a separate
defect with an owner**. Any future closure must be an **allow-list of operator-pinned keys for built-ins** —
**not** a blanket ban, because the BYOK self-provisioning chain depends on the tool. `requirements.md` N-6's
"Recommendation: close it" is **SUPERSEDED**; the divergence was caught and resolved. Rationale carried from
§3g G6: closing the hole would break a working operator remediation route and the BYOK chain for no benefit to
this commission, and it does not undercut C3's intent — C3's outcome is that the base URL is settable via the
**protected, schema-backed** path, which G1–G4 deliver.

**Adjudication 2 — `is_available()` call sites: `requirements.md` premise P12's verified numbers GOVERN.**
The seam is real and has **three** sites: `daemon/manager.py:2813`, `daemon/manager.py:3057`,
`daemon/services/mcp_service.py:579`. The `daemon/services/mcp_service.py:576` and `:1128` anchors in
`decisions.md` §3e E5 are **STALE** (`:576` is a list-initialisation line; `:1128` is a comment — the nearest
call is at `:1130`, see D-H). **Re-verified by me in the worktree.** The C1 row cites the three governed sites;
D-H carries the additional preload-path call as an implementation check.

**Adjudication 3 — line-number conflicts generally.**
`requirements.md` did the last stale-anchor pass, so **its observed numbers win**; `decisions.md`'s design
reasoning wins on any question of **what to do**. Applied throughout §5, with the exceptions recorded in §5.1
(D-C, D-D, D-F, D-G are one-line slips where I re-opened the file and cite the observed line).

**Adjudication 4 — the reviewer-council fold (2026-10-27).**
The council returned **APPROVE-WITH-ADVISORIES**; every finding was adjudicated **FOLD** and applied across the
sibling artifacts on 2026-10-27 — **C-1** (the A3 release trigger), **W-1…W-6** (the offload shape, the quarantine
home, the plane bypass, the secret-resolution hoist, the operator surface, the test-reset hook), **R-1 / R-2**
(the interim-efficacy disclosure), **S1–S11** (citation precision, `reset()`-never-called, executor sizing,
dead knobs) and **V1–V4** (the verification legs), together with the **migrations exit gate** (no new table, no
new `.sql`). This overview folds the findings that touch it: **C-1 option (b)** is adopted for A3 (the hold
module owns a per-seam deferred-candidates list and re-consults the promote-txn predicate on a short bounded
cadence — read-only `state.json`, default 5 s, only while deferrals are outstanding — releasing through the
**original** seams, the checkpoint-resume path and never a cold dispatch; the boot pass runs **once per boot**,
so a "next pass" release would lose the checkpoint resume); **W-3** replaces B1's earlier "eventually consistent"
shape with **work off-loop, await on-loop** (fire-and-forget is rejected — a deferred scan that finds a dead
child returns `[]`, so there is no reap handle); **W-4** moves the quarantine registry to the single leaf module
`daemon/mcp/init_guard.py`; and **R-2** is recorded as a **disclosed tradeoff** in §1 and §7, not as a caveat.
Everything the fold did **not** change — the never-green safety property, the six non-goals, the two pinned
commissioning constraints — is unchanged here.

**Adjudication 5 — the approver fold (iteration 001: 4 blocking + 10 notes, all accepted; iteration-002 prep).**
The approver returned **4 blocking + 10 notes, all accepted**. Three rulings were made, and all three are folded
into this file:

- **The loop-lag bound (BLOCKING 2).** The B1 acceptance criterion is now a **number**: **p95 event-loop
  scheduling lag ≤ 250 ms AND max ≤ 500 ms**, measured with a **periodic heartbeat task** while the **R-1
  concurrent burst** (MCP warmup + job-recovery + auto-continue resume) runs. *Rationale:* the smallest
  readiness probe budget is the **0.5 s `SELECT 1` probe** (`daemon/services/readiness.py:45`
  `DB_PROBE_TIMEOUT_S = 0.5`), which needs **multiple loop turns** to service, so the lag must stay well under
  500 ms for the probe to land inside its budget. A pin with no number is not a pin. → **B1 row AC cell** and
  **§7 item 2**.
- **The env-preserving merge (BLOCKING 3).** The schema-version heal at `daemon/manager.py:2855-2867` is a
  whole-config replace (`update_mcp_server(..., config=default_config, ...)`) and would **drop** the operator-set
  env (the pinned `CONTEXT7_API_URL` and any KMS-marker keys) on the first boot after the 2 → 3 bump — and C2's
  DNS pre-check would then validate the **default** host rather than the configured one. The heal becomes an
  **env-preserving merge** (operator-set + KMS-marker keys preserved, only template-derived defaults rebuilt,
  undeclared row keys retained); C2 reads the **effective post-heal env**; **disclosed-discard is rejected** (it
  defeats C3's purpose and fails silently). → **C3 row** + the heal-survival pin.
- **The journal-write suppression (BLOCKING 4).** The C4 per-transition journal write is a **daemon-side** write
  to the **promote-owned** `state.json`, and `journal_history_append` (`daemon/tools/upgrade_journal.py:391-405`)
  is an **unlocked** read ⇒ mutate ⇒ whole-document write — so a stale-snapshot write during a live promote txn
  would **drop `in_flight`** and **release A3's deferred resumes mid-promote**. The write is **SUPPRESSED while
  `promote_txn_live()` is true**, buffered in memory (bounded: one entry per `server`+`state`, newest wins),
  flushed on txn close or graceful shutdown, **never dropped**; **lock-acquire is rejected** (the ceremony owns
  `journal_lock`; a daemon-side acquire is a new cross-process hazard, and the transition is deferrable
  observability, not correctness). → **C4 row** + the suppression pin.

**Two evidence-based REJECTIONS of instruction text** (recorded so the audit trail shows they were considered
and overruled on evidence, not missed):

- **The `cls().name` → `cls.name` inversion is REJECTED.** Executed against the real classes:
  `Context7ServerDefinition.name` is a **`@property @abstractmethod`** (`daemon/mcp/builtin_servers/base.py:27-30`),
  so `C7.name == "context7"` is **False** while `C7().name == "context7"` is **True**. `cls.name` would therefore
  make `is_quarantined(cls.name)` **never match** — the quarantine would never engage, **silently disabling
  C1**. The **verified-correct form `cls().name`** is retained. This file does not mention the expression; the
  rule is carried here so no downstream edit inverts it.
- **The narrowing-count instruction is REJECTED as stated.** `requirements.md` §6 now says **exactly NINE
  narrowings, N-1 … N-9** — and **N-9 is a disclosure, not attached to a requirement row** (it is the
  interim-efficacy disclosure, R-2). This file's count is therefore **nine**, with the per-requirement narrowings
  still N-1…N-8; a "eight" count would have been wrong against the folded requirements authority.

**Adjudication 6 — the iteration-003 fold: the MECHANISM CHANGE (single normative home).**
Iterations 001 and 002 each rejected the set for the **same structural reason**: a predicate was stated in more
than one place, and the copies drifted. The fix is a **mechanism**, not another edit — **`requirements.md` §4.1–§4.7
is now the SINGLE NORMATIVE HOME** for every cross-cutting predicate in this commission (the never-green
trigger, grace eligibility + aggregation + the counter observation kinds, the grace ceiling and its clock, the
leak signature and its streak-arm qualification, the DNS-refusal thresholds, the acceptance-criterion
thresholds, and the A3 release rule). **The other seven artifacts — `decisions.md`, `architecture-recommendation.md`,
`phase1/2/3-plan.md`, `risk-safety-register.md` — now REFERENCE the register; this overview does the same.**
Every predicate-bearing cell in §5 has been converted from a restatement into a **pointer**, and the pointer
map is the `> Normative source:` block under the matrix. If a restatement here ever appears to disagree,
**§4 wins**.

Three safety rulings are folded from that round:

- **The never-green trigger is UNCONDITIONAL, and the fair-window predicate is DELETED.** The cap-reach
  trigger, the two-outcome shape, the "fairness is diagnostic-only" rule, the required §5 leg (a
  grace-eligible daemon that never greens still rolls back) and the fact that the ceremony-truncation case is
  the **pre-soak deferral** — all are **§4.1 (P1)**. This file's A2 row and §7 now say the same thing and
  state no fairness condition.
- **The A3 liveness/release rule is boot-aware and hysteresis-guarded** — a post-boot heartbeat-fresh leg **or**
  a **boot window** (`BOOT_LIVENESS_S = 120.0` s, with `started_at` present and inside the 900 s ceremony
  envelope); the boot window is **HOLD-only** (release requires `in_flight` to be **ABSENT**); the release
  fires only after **two consecutive false** predicate reads ≥1 driver tick apart, so a single torn
  `state.json` read cannot release the deferred resumes mid-promote; the driver is a **lazily-created,
  module-held task** in `daemon/services/promote_txn_hold.py`. All of it is **§4.7 (P7)** — the A3 row points
  there and does not restate it.
- **ONE probe mechanism.** The contention probe is the **non-blocking `checkpointer.raw_saver.lock.locked()`
  observation**; the acquire-and-read variant is **DELETED** (self-deadlock on the non-reentrant saver lock),
  and the dedicated-connection fallback is a **contention mitigation only** — it cannot observe queue growth,
  so detection stays with the lock classification + `detail.pool_state`. The A4 row states the single
  mechanism and points at §4.4/§4.2.

**Reconciliation note — the loop-lag bound (iteration-002 blocking #2) is SUPERSEDED by the single-normative-home
rule.** Iteration-002's blocking #2 asked for the numeric event-loop-lag bound to be stated **in
`plan-overview.md`** (and, in the same round, in `requirements.md`, `phase2-plan.md` and the risk register).
Iteration-003's single-normative-home rule came **later** and is the **mechanism the approver demanded**, so it
**supersedes** the earlier instruction: the numbers are now stated **ONCE**, in `requirements.md` §4.6 (P6), and
every layer — the B1 matrix row, the house-rules block, `phase2-plan.md` and the risk register — **cites** it
rather than restating it. This file's B1 row and §7 now carry the **measurement shape** (a periodic heartbeat
task during the R-1 concurrent burst) and the **rationale by reference** (the smallest readiness probe budget
needs multiple loop turns to service, so the lag bound must sit well below it), with the numeric relation
itself living in §4.6. **The change is a de-duplication, not a relaxation** — the bound is unchanged, and the
required §5 leg (a grace-eligible daemon that never greens still rolls back) is unaffected. *Audit-trail
note:* the numeric values still appear in this file's **adjudication 5** entry above and in `requirements.md`'s
own audit-trail markers — both are **historical records of what was ruled**, exactly like the register's
superseded-form markers, and neither is a live requirement; the live statement is §4.6 alone.

**One further evidence-based REJECTION** (the audit trail shows it was considered and overruled on evidence,
not missed):

- **The `cls().name` → `cls.name` inversion is REJECTED.** `name` is a **`@property @abstractmethod`**
  (`daemon/mcp/builtin_servers/base.py:27-30`), so `cls.name` is a **property object**: `C7.name == "context7"`
  is **False** while `C7().name == "context7"` is **True**. `cls.name` would make `is_quarantined(cls.name)`
  **never match** — the quarantine would never engage, **silently disabling C1**. The **verified-correct form
  `cls().name`** is retained everywhere (the architecture layer's §10.2 records the same rejection). This file
  does not use the expression; the rule is carried here so no downstream edit inverts it.

**Cross-file consistency audit — method (reproducible).** The single-source claim is machine-checked by a script
(`predicate_audit.sh`, retained in the lane's evidence) that: (i) greps all **8** artifacts for **8** predicate
signatures — **P1** never-green wording (`fair window|fair observation|fair chance`) (§4.1) · **P2** class lists
(`probe_timeout.*pool_busy|…`) (§4.2) · **P2** counter kinds (`consecutive_hold|MAX_HOLDS_PER_GREEN|livez .*hard.green`)
(§4.2) · **P3** ceiling (`GRACE_HORIZON_S|grace_abs_ceiling_s|first_grace_eligible_observation|soak_entry \+ 300`)
(§4.3) · **P4** leak (`overflow\(\)|requests_waiting|pool_available|pool objects are UNAVAILABLE`) (§4.4) ·
**P5** DNS (`DNS_PRECHECK_TIMEOUT_S|resolver_timeout`) (§4.5) · **P6** thresholds
(`250 ms|500 ms|HANDSHAKE_WAIT_TIMEOUT_S|pytest\.mark\.timeout|override-ini`) (§4.6) · **P7** release
(`BOOT_LIVENESS_S|two consecutive false|promote_txn_hold`) (§4.7); (ii) classifies every hit as **HOME-OWN**
(inside `requirements.md`'s **§4.1–§4.7** register — the home may state the rules), **HOUSE-CONVENTION** (a line
whose row-window contains "house convention" — the operating conventions the approver required be restated per
phase), or **UNANCHORED DRIFT**; and (iii) treats a hit as **anchored** when a normative pointer (`§4.x`,
"Normative source"), or a superseded / rejected / deleted / withdrawn marker, appears within its markdown **ROW**
(for a table-continuation line, up to the nearest preceding `|` line) or within **±2 lines** otherwise.
**Pass condition: UNANCHORED DRIFT = 0.** Last run at fold completion: **179** signature hits · **27**
HOME-OWN · **10** HOUSE-CONVENTION · **0** UNANCHORED. *Re-running the script after this method note landed
reproduces the same verdict — **UNANCHORED DRIFT = 0** — with a higher raw count (**187** hits · **27**
HOME-OWN · **11** HOUSE-CONVENTION · **0** UNANCHORED), because the note itself quotes the eight signatures;
each of its signature lines is anchored to the `§4.x` anchor it names, so none of them is drift.

---

## 9. Open items carried forward

From `decisions.md` §9, plus the items this overview adds. The **blocking** ones are marked.

| # | Open item | Owner | What unblocks it | Blocking? |
|---|---|---|---|---|
| **1** | Does cancelling the probe's scheduled future actually remove its entry from **psycopg_pool's waiting queue**? (Library behaviour, not repo behaviour.) | developer (implementation) | The AC is a **test** asserting the waiting-queue length does not grow across N timed-out probes — not an assumption. If cancellation does not clean up the waiter, the fallback is a dedicated probe connection that bypasses the pool's queue. | No — the AC is already written as a test |
| **2** | Exact **leak-predicate thresholds** — how many consecutive ticks of a monotonic `checkedout()` rise constitute a leak? (The 3-tick figure is a starting proposal, not a measured value.) | developer + reviewer | The measurement needs the leak rate under load — which is the **follow-up** commission's data. **Interim:** the predicate is conservative (fail-closed to `pool_leak` ⇒ no grace) and the thresholds are config-free constants in the first cut, so they can be tuned in one place. | No — the conservative interim is specified |
| **3** | Should the eligible-pending sweep's hold be a **"skip the wake"** or a **"defer the row"**? | developer (implementation) | "Skip the tick and count it" is stateless (the next tick is 90 s later; the min pending age is 60 s — `daemon/config.py:2185`); a "defer the row" would need a persisted marker. Deferred to implementation, but it must be **pinned either way**. | No — either shape is acceptable, the pin is not |
| **4** | Should the daemon's `mcp_set_env` built-in guard be closed, and with what allow-list shape? | leader + developer | **Decided for this lane: NOT closed** (adjudication #1). The defect is recorded with an owner; the correct shape if closed later is an **allow-list of operator-pinned keys**, never a blanket ban — `daemon/tools/infra.py:1527` is a shared BYOK surface. | No — closed for this lane |
| **5** | Does the context7 upstream package's env contract match our schema assumption exactly? (`CONTEXT7_API_URL \|\| ${CONTEXT7_BASE_URL}/api` is quoted from the RCA's inspection of `@upstash/context7-mcp` v4.4.4, **not** from this repo.) | developer (implementation) | §3g G1's shape must be **pinned by a test** against the real package's behaviour (or at minimum against the upstream source) **before it ships**. This is a **HARD SHIP GATE for C3** — the G1-corrected two-field contract (`CONTEXT7_API_URL` effective; `CONTEXT7_BASE_URL` a daemon-side mirror) must be confirmed against the real package before C3 ships. | **YES — a HARD SHIP GATE for C3** |
| **6** | Should the grace's hard expiry (120 s) and extension cap (180 s) be operator-configurable? | leader | They are new constants with no precedent. A conservative first cut ships them as **shell-side constants** (like `ADAPTIVE_SOAK_*` at `lib.sh:146-149`); making them env-tunable is a follow-on if operators ask. | No |
| **7** | The **pool-leak repair commission's scope and owner** — the root cause of the 22:10–22:50Z incident and of the self-sustaining probe feedback loop. | leader | The four confirmed leak sites are listed (non-goals 1–4). The repair — transaction hygiene, `ens_db_tools`' BEGIN, the mint path, the `/api/instances` read path — is a **separate lane** and must be **commissioned explicitly**, not absorbed here. It must land **before** A1's leak-refusal is relaxed. | No for this commission — **but it is the highest-value follow-up** |
| **8** | The **wedge-capture `tasks` → `task`** fix (`daemon/services/wedge_capture/db_capture.py:308`, `:387`). | leader (routing) | One-character fix, flagged here so it is not lost; it belongs to the wedge-capture lane. | No |
| ~~**9**~~ | ~~**A3's dispatch count** — three (decisions.md) or two (requirements.md N-7)?~~ | **STRUCK — resolved by the reviewer-council fold (2026-10-27).** Seam coverage is settled at **FOUR** dispatch seams (boot auto-continue pass · runtime resume dispatch · eligible-pending sweep · task-less-orphan sister pass `sweep_orphaned_live_turns`), with the release trigger adjudicated at **council C-1, planner option (b)** — the hold module owns a per-seam deferred-candidates list and re-consults the promote-txn predicate on a short bounded cadence (read-only `state.json`, default 5 s, only while deferrals are outstanding), releasing through the **original** seams (checkpoint-resume path, never a cold dispatch). **D-A is moot.** | — | **Closed** |
| **10** | **A4's default pool size** — 10 (decisions.md D2) or unchanged-5 (requirements.md A4 safety clause)? | developer (implementation) + leader | **Resolved as 10, conditional on stating the DB-side `max_connections` budget** (D-B). If the budget cannot be shown to accommodate it, the default stays at 5 and the knob is operator-opt-in. | No — conditional, decided at implementation |
| ~~**11**~~ | ~~**The preload-path `is_available()` call** at `daemon/services/mcp_service.py:1130` (D-H) — is the quarantine registry consulted there?~~ | **STRUCK — resolved by the fold's placement decision (W-4).** The registry consult now lives **inside** `is_available()` in `daemon/mcp/builtin_servers/base.py`, so **every** call site of that predicate — bootstrap (`daemon/manager.py:2813`), warmup-pool registration (`:3057`), the warm path (`daemon/services/mcp_service.py:579`) and the preload gate (`:1130`) — inherits the quarantine with **zero edits** at the call sites. The separate "cold path never consults `is_available()`" question is carried by the companion architecture layer (`architecture-recommendation.md` §10, disagreement #9) and is not an open item of this overview. | — | **Closed** |
| **12** | The **detail-payload shape pin** for the three additive blocks (`detail.grace`, `detail.pool_state`, `detail.mcp`) landing in one namespace across Phases 1 and 3. | developer (implementation) + reviewer | Phase 1 lands the composite/payload plumbing first; Phase 3 adds only a sibling map. The pin asserts coexistence, no key collision, and backward tolerance. | No — but it is the **shared-surface coordination point** and must be pinned, not assumed |

---

*Artifact written in `/home/nea/ensemble-src-wt-soak-gate` on branch `feature/soak-gate-mcp-warmup` (base
`d5a4e4ad92d19f3265b97bc29c5aa3943f026ed2`). No `daemon/`, `scripts/`, `tests/`, `agents/` or config file was
modified. The live daemon, the install `.env`, `state.json`, `releases/` and the `current` symlink were not
touched; the main checkout `/home/nea/ensemble-src` was not written to. Every anchor cited in this file was
opened and read in the worktree.*
