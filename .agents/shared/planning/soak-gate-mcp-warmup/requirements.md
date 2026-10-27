# Requirements — soak-gate-mcp-warmup

**Commission:** promote soak-gate fix + MCP warmup degradation fix
**Branch / worktree:** `feature/soak-gate-mcp-warmup` @ `d5a4e4ad92d19f3265b97bc29c5aa3943f026ed2`
**Worktree (only place anything was read):** `/home/nea/ensemble-src-wt-soak-gate`
**Status:** PLANNING ONLY — no `daemon/`, `scripts/`, `tests/`, `agents/`, or config file was modified.

## How to read this file

This is the **requirements** artifact for a three-part commission (A: promote soak gate, B: MCP warmup off the
main loop, C: MCP servers degrade gracefully). It contains: (1) a context recap with the premise corrections
that were forced by verification; (2) **two synchronised views of the same 12 requirements** — a wide summary
table and a per-requirement sub-section set (the sub-sections are authoritative; the table is the index);
(3) the **normative predicate register** (§4) — §4.0 the failure-CLASS table, and §4.1–§4.7 the canonical
predicate definitions (never-green trigger, grace classes/aggregation/counter kinds, grace ceiling + clock,
leak signature + streak-arm qualification, DNS-refusal thresholds, AC thresholds, A3 release rule) that every
other file **references** rather than restates; (4) the **A5
acceptance-criterion detail** with named tests, commands and observables (§5); (5) a
**narrowed-requirements** section (where a requirement could only be satisfied by breaking a safety
property, the safety property wins and the feature is narrowed — and that is said explicitly) (§6); (6)
**explicit non-goals** with owners (§7); (7) a coverage summary (§8).

Every `file:line` anchor in this file was **personally opened in the worktree** before being cited. Where a
supplied anchor did not match what is on disk, the file says so explicitly in an *Anchor verification note* and
cites the number actually observed. Anchors to files that exist only in the main checkout
(`/home/nea/ensemble-src/.agents/shared/…`, untracked) are cited **by path** and are never treated as worktree
inputs.

**Anchor discipline for the implementer:** the daemon-side rows (A1 readiness, A4 pool, B1, C1–C4) require the
normal stage→promote path. The shell-side rows (A2, A3) are **working-tree-sourced**: the frozen binary carries
no `scripts/`, and the daemon's own executor resolves the scripts dir via
`daemon/tools/upgrade_tools.py:1919-1942` (`_resolve_scripts_dir` — "the frozen binary does not carry
scripts/"; candidates: `ENSEMBLE_UPGRADE_SCRIPTS_DIR` → the repo checkout the module lives in →
`<install_dir>/scripts/upgrade`). A shell-side fix therefore lands in the repo and affects the next promote
**without a release bump**, while a daemon-side fix needs a release. The live release is **v0.27.8** and is
untouched by this commission.

---

## 1. Context recap

### 1.1 The incident

On 2026-10-26 the promote of v0.27.8 passed every gate (integrity, livez, readyz including `checkpoint_saver`,
version verify) and then the adaptive soak sampled **zero green probes**. ADR-005 / C-1 auto-rollback fired and
**v0.27.8 was quarantined** — a healthy release. The same binary installed manually (no gate) served readyz
green ~90 s after boot. The RCA is at
`/home/nea/ensemble-src/.agents/shared/analysis-20261026-v0278-soak-failure.md` (main checkout, untracked;
read-only reference).

The decisive journal timeline (RCA §Evidence):

```
20:42:31  gate: readyz ALL GREEN — daemon ~75s old
20:42:39  GET /api/prompts/pending-count → 503     ← 21s BEFORE any resume
20:42:43  eager_warm_schemas: primed 4/5 MCP servers (plane: 0 tools — 'Connection closed' retries)
20:42:43-20:42:57  Lazy-loaded 7 MCP tool schemas ×4 instances
20:43:00  [RESUME] ×4 instances (auto-continue resume burst)
20:43:17  [CheckpointPerf] op=aget latency_ms=7596
20:43:17  [Readiness] degraded: database: SELECT 1 probe failed or timed out
20:43:33  [Readiness] degraded: checkpoint_saver: sentinel read (aget_tuple) timed out after 1.0s
20:43:41  [CheckpointPerf] op=aget latency_ms=94     ← recovers when the burst eases
20:43:33 → 20:45:21  degraded every ~15-30s
promote: soak: /livez transient-exhausted at 15s; /readyz transient-exhausted at 45/60/90/105s
promote: WARN: soak FAILURE: cap-reach at 105s with consecutive_green=0 < 3 → auto-rollback
20:49:59  auto-rollback v0.27.8 → v0.27.4
```

A **second, independent incident** on the same host reproduces the same symptom pair (readiness red +
`pending-count` 503) with **119 % CPU** and `/api/instances` at 5–8 s during 2026-10-26 22:10–22:50Z:
`/home/nea/ensemble-src/.agents/shared/incident-20261026-db-pool-idle-in-transaction.md` (main checkout,
untracked). That RCA establishes a **self-sustaining failure class** (a probe that leaks a transaction per tick
→ pool pressure → probe timeout → retry → more leaks) and is load-bearing for A1 and A4 below.

### 1.2 The two co-causes (neither may be assumed to dominate)

1. **Post-boot saturation of the checkpoint `AsyncConnectionPool`** under the boot burst (MCP schema lazy-load
   ×4, plane MCP retry storm, job-recovery reconcile, auto-continue resume of 4 instances) while the readiness
   detector's budgets are **steady-state budgets** — so "busy but healthy" reads identically to "broken".
2. **Context7 MCP init storm** — `api.context7.com` went NXDOMAIN, so every context7 init hung to a 30 s
   timeout, retried 3×, repeated every ~2–4 min **forever** (nothing circuit-breaks init), each attempt spawning
   `npm exec @upstash/context7-mcp` + node at 88–156 % CPU, leaking an unclosed aiohttp session and orphaning
   children; the daemon main process pegged at 105–114 % CPU with zero agent work.

**Interim efficacy disclosure (N-9, §6):** until the follow-up transaction-discipline commission lands (§7
non-goal #3), A1's grace will correctly REFUSE to engage on the leak signature — so a healthy-release
rollback **can recur**. The interim primary objective is carried by C1/C2/A3/A4; the grace covers only the
busy-not-leaking signature. **The leak claim is stated here in its CONFIRMED form** (Worker B N1
reconciliation): what the code supports is (a) the readiness probes run against the **SQLAlchemy** pool
(`manager.engine`) and the queue aggregate's SQL is `FROM task` (not `job_queue_items`); (b) the probe's
`with engine.connect() as conn:` block **closes the connection at block exit**
(`daemon/services/readiness.py:799`), so the probe does **not** leave an open transaction behind on the
normal path; (c) the **CONFIRMED** retention mechanism is the **timed-out probe's connection retention** — a
probe that abandons its 0.5 s budget keeps its checked-out connection until the pool's own deadline, so the
*timed-out* probes (not the completed ones) accumulate. The stronger claim "the probe leaks a transaction per
tick" is **NOT supported by the code as written** and is therefore **not** asserted here; it belongs to the
follow-up transaction-discipline commission to establish (or refute). The interim-efficacy consequence is
unaffected either way: the leak class is armed by the **P2** saver-pool signals (§4.4), and while the
follow-up lands, a leak-shaped signature legitimately refuses the grace.

**The RCA's weighting is internally inconsistent and the requirements must not assume either dominates:** the
main analysis calls auto-continue "the largest single contributor, but not the only trigger", while the
21:45Z addendum calls the context7 storm the "second, **dominant** co-cause". Requirements A1–A5 and B1–C4 are
therefore written so that **each co-cause has its own independent remedy**, and no requirement's acceptance
depends on the other co-cause being fixed.

### 1.3 Prior art — the 2026-10-09 / v0.18.5 environmental DB-churn class

An **earlier** instance of the same failure class exists: on **2026-10-09**, release **v0.18.5** failed its
soak under **environmental DB churn** — the readiness detector's steady-state budgets were exceeded by
database behaviour that was not a release defect, and the C-1 never-green path fired. This is prior art for
the **class-driven** design, and it is **NOT** a post-boot-only special case: the 2026-10-09 class is covered
by the **same** §4.2 (P2) class-driven rule as the 2026-10-26 incident. The design consequence is explicit —
**the grace must not be armed by "the daemon is young" or "a burst is happening"**; it is armed by the
**failure class** alone. A design that special-cases "post-boot burst" would leave the 2026-10-09 class
uncovered.

### 1.4 Premise corrections (verified — these FALSIFY parts of the original brief)

| # | Supplied premise | Verdict | Ground truth (worktree anchors) |
|---|---|---|---|
| P1 | "`daemon/mcp/resilience.py`'s breaker and `daemon/sources/circuit_breaker.py` are two implementations" | **FALSE** | There is **one** class. `daemon/mcp/resilience.py:45` **imports** `CircuitBreaker` from `daemon.sources.circuit_breaker`; the class is defined once at `daemon/sources/circuit_breaker.py:20` (`failure_threshold=5`, `recovery_timeout=60.0`, CLOSED/OPEN/HALF_OPEN, CR-4 single-probe-in-flight flag at `:33-38`). |
| P2 | "MCP children inherit the daemon's environment → set `CONTEXT7_API_URL` in `.env`" | **FALSE** | `daemon/mcp/builtin_servers/context7.py:33-40` returns **no `env` key**; `daemon/mcp/builtin_servers/base.py:241-242` omits `env` when empty; the MCP SDK's stdio client uses `get_default_environment()` (HOME/LOGNAME/PATH/SHELL/TERM/USER only). **The `.env` cannot reach the child.** The operator's 21:40 remediation therefore reached the child by another route (most plausibly the unguarded `mcp_set_env`, `daemon/tools/infra.py:1527`). |
| P3 | "the `pool.check()` probe" | **STALE** | The probe was replaced by the sentinel `aget_tuple` probe (`daemon/services/readiness.py:580-745`). The **reason string still carries the old name** — `daemon/services/readiness.py:337-339` emits `"checkpoint_saver: AsyncConnectionPool.check() failed or timed out"`. |
| P4 | "busy-amnesty can cover the resume burst" | **FALSE (blind spot)** | The resume path writes **no heartbeats** (`daemon/services/auto_continue_boot_pass.py:68-71`), so the `inflight_turns` SQL does not count resumed tasks → busy-amnesty **never fires for exactly the resume-burst case**. No requirement below may extend busy-amnesty to cover the burst. |
| P5 | "`in_flight` kind=promote is written by the daemon" | **FALSE** | The daemon writes **only** `kind="restart"` (`daemon/tools/upgrade_tools.py:2891-2902`). The promote txn is opened/closed by the **shell** (`scripts/upgrade/promote.sh:327` → `:412`, closed via `scripts/upgrade/lib.sh:1115-1118` (`journal_close_txn` → `journal_update "in_flight" "null"`)). |
| P6 | "the `.agents/shared/protection-plan-install-dir-20261012.md` plan" | **PHANTOM — does not exist** | Verified against disk in the worktree: absent. `scripts/protection/README.md:11` cites it as if it does; the real content is `scripts/protection/README.md`. Do not treat it as a missing input. |
| P7 | "the readyz payload carries a top-level `reasons`" | **FALSE** | The payload nests `reasons` under `detail` (`daemon/services/readiness.py:239-266`); `daemon/tools/upgrade_tools.py:2088-2092` reads `readyz.get('reasons', [])` at the **top level** and therefore always renders `reasons=[]`. A real observability defect — carried into C4. |
| P8 | "lib.sh:776 reads `ENSEMBLE_UPGRADE_SCRIPTS_DIR`" | **STALE** | `scripts/upgrade/lib.sh:776` is `journal_path() { printf '%s/releases/state.json' "$INSTALL_DIR"; }`. The env var is read by the **daemon** side: `daemon/tools/upgrade_tools.py:1919-1942`. |
| P9 | "the `inflight_turns` reader lives at `daemon/services/upgrade_journal.py`" | **STALE PATH** | It is `daemon/tools/upgrade_journal.py:281-319` (`journal_owner_heartbeat_fresh`), with `OWNER_HEARTBEAT_FRESH_S = 60` at `:156`. |
| P10 | "the live release is v0.27.4" | **STALE** | The live release is **v0.27.8**; the auto-rollback moved `current` to v0.27.4 and the operator later re-installed v0.27.8 manually. This commission touches neither. |
| P11 | "the readiness refresh interval is unknown / 10 s" | **CONFIRMED (10 s)** | `daemon/config.py:2657-2666` `readiness_refresh_interval_seconds: int = Field(default=10)`. The refresher task is created at `daemon/api.py:2472-2486` and its **first tick fires at t=0** (`daemon/api.py:3745` — the docstring states "the first tick fires immediately"), i.e. **inside** the boot-recovery burst, then every 10 s **through** the auto-continue resume burst (auto-continue at `daemon/api.py:2703`). This is A1's precise arming surface. |
| P12 | "`is_available()` is consulted at `manager.py:2767`/`:2997`, `mcp_service.py:576`/`:1128`" | **STALE LINE NUMBERS / INCOMPLETE** | Observed **FOUR** sites: `daemon/manager.py:2813`, `daemon/manager.py:3057`, `daemon/services/mcp_service.py:579`, `daemon/services/mcp_service.py:1130` (cold path). The seam is real; the numbers were not, and the earlier three-site count was short by the cold path. |
| P13 | "A2's grace-suppression alone would fix the soak" | **FALSE** | The soak counter (`scripts/upgrade/lib.sh:5216-5221`) requires a hard `"green"` on **both** probes; *anything* else — including `busy-amnesty` soft-green and `boot-grace-suppressed` (rc 3) — **resets it to 0**. A grace that merely suppresses probes still yields `consecutive_green=0` and still fires C-1. A2 must change the **counter semantics**, not just the probe classification. |

---

## 2. The requirement table (index — sub-sections in §3 are authoritative)

| ID | Requirement (short) | Current behaviour (worktree anchor) | Intended (short) | AC | Safety preserved |
|---|---|---|---|---|---|
| **A1** | Boot/burst grace window | Grace exists but is anchored to **restart** (`lib.sh:157-158`, `:4266`, `:3197-3212`); it is time-only, and the counter still resets (`lib.sh:5216-5221`) | Grace = **class-driven** (**§4.2 P2**) and **leak-refusing** (**§4.4 P4**); boot-age/burst are **published context only**, never arming; renders *"degraded, grace-held, reasons still present"* — never "healthy"; horizon **§4.3 P3** (`GRACE_HORIZON_S = 120.0`, observation-relative, never extended) | A5(i), A5(iv) | never-green rollback intact (**§4.1 P1**, unconditional) |
| **A2** | Soak semantics | counter needs hard `"green"` on both probes; `busy-amnesty`/rc-3 **reset** it (`lib.sh:5216-5221`); soak uses `_probe_once_classified` **directly — no retry** (`lib.sh:5161`, `:5181`); cap = `min(120, remaining-60, soak_s)` (`lib.sh:5080-5109`) | Counter observation kinds per **§4.2 P2**: GREEN / HELD / RESET. HELD only on `livez hard-green + readyz grace-eligible-transient` (a livez transient is a **RESET**, never a HOLD); reset-on-green for `consecutive_hold`; `MAX_HOLDS_PER_GREEN = 2`; window extends past the burst (deadline re-anchored per (re)grant; absolute bound **§4.3 P3** = entry + 300 s); red still fails fast | A5(i), A5(iii) | never-green (**§4.1 P1**, unconditional) + fail-fast-on-red intact |
| **A3** | Auto-continue hold during a promote txn | kill-switch read **once at boot** (`auto_continue_boot_pass.py:145-149`, `:176`); the boot pass runs **exactly once per boot** (`api.py:2703`), so during a promote it always runs with the txn live and there is **no next pass this boot**; resumes dispatched fire-and-forget at `manager.py:13671-13684`; `eligible_pending_sweep.py` first tick immediate, ALWAYS ON, no kill switch; `sweep_orphaned_live_turns` (`auto_continue_boot_pass.py:726`, wired `api.py:2708`) | **Live, promote-txn-aware hold** across **FOUR seams**, anchored on the shell-written `in_flight` promote txn ("pipeline-busy" vocabulary); the hold module owns a per-seam **deferred-candidates list** that **re-consults the promote-txn predicate on a short bounded cadence** (default 5 s, only while deferrals are outstanding) and releases through the **original seams**; the promote-txn LIVE predicate, `BOOT_LIVENESS_S = 120.0`, the boot-window HOLD-only rule and the two-consecutive-false release hysteresis are **§4.7 P7** | A5(i) | resumes still happen — only **deferred**, never dropped; the release is the **checkpoint-resume path**, never a cold dispatch |
| **A4** | Checkpoint pool sizing | saver pool `min_size=1/max_size=5`, **no `timeout` kwarg** → 30 s psycopg default (`persistence.py:273-293`); the saver serialises **all** cursor ops through ONE `asyncio.Lock` (`langgraph .../postgres/aio.py:59`); the SQLAlchemy pool is **separate** (`factory.py:136-137`, `:188`) | Address **contention** (saver lock) + **ghost waiters** + size for **7 writers + probe**; surface pool health in the readiness detail | A5(i) | the leak is **NOT** claimed to be cured by sizing |
| **A5** | Regression pins | — | 4 named pins: (i) healthy-under-burst not rolled back, (ii) broken **is** rolled back, (iii) fail-fast on a real regression, (iv) grace observable + cannot mask indefinitely | §5 | — |
| **B1** | Warmup off the main loop | warmup **is** a background task (`manager.py:3137`, `:3565-3566`) but runs **on** the main loop; `stdio_wrapper.py:494` does **synchronous** `/proc` reads on the loop thread; `stdio_wrapper.py:306` `await self._ready.wait()` is **unbounded**; `warmup_pool.py:275` `__aenter__()` is **outside** the 60 s budget at `:278` | Loop-thread work offloaded; both unbounded waits bounded; warmup never starves the loop | A5(i) (loop-lag leg) + **§4.6 (P6)** threshold pointer | MCP init still fully functional |
| **B2** | Readiness not gated on MCP warmup | **premise does not hold** — the readiness composite's 4 components contain **zero** MCP references; `pending-count` is a plain DB count (`prompts.py:1078-1102`) | **NARROWED** → (a) a regression **pin** proving MCP-independence, (b) the B1 fix as the real remedy | §5 (narrowed) | — |
| **B3** | No child leaks | `except BaseException` cleanup exists (`warmup_pool.py:333-344`) but the two unbounded waits above are holes | Failed init kills children + closes session/streams; aiohttp claim recorded as unverified | A5(iii) | — |
| **C1** | Circuit-break the warmup | **nothing** wraps server init; `warmup_pool.py` has **zero** resilience references; the health loop re-replenishes forever (`warmup_pool.py:711-726`, `:552-583`) | One breaker class (`sources/circuit_breaker.py:20`) + a **per-boot** quarantine home (`is_available()` seam) | §3-C1 | one server degrades alone |
| **C2** | Fail fast on dead API hosts | no DNS pre-check anywhere; init hangs to a 30 s timeout (`warmup_pool.py:285-310`) | Pre-resolve the **effective post-heal** base the child would use (**§4.5 P5**: `DNS_PRECHECK_TIMEOUT_S = 2.0` s, off-loop; resolver TIMEOUT ⇒ `resolver_timeout`, **not** quarantine-eligible on first occurrence) | §3-C2 | — |
| **C3** | Pin the context7 API base | base config has **no env key**, schema is **empty** (`context7.py:33-40`, `:41-47`) → unsettable; the package reads `CONTEXT7_API_URL` **or** `${CONTEXT7_BASE_URL}/api` (derived-URL semantics) | **Exactly two** env entries per the G1 contract: `CONTEXT7_API_URL` = **effective lever** (no default); `CONTEXT7_BASE_URL` = **daemon-side mirror** (default `https://context7.com`, **inert to the package**, used as the C2 derivation input); **env-preserving merge in the schema heal** (`manager.py:2855-2867`) so a schema bump cannot drop operator env / KMS markers; C2 reads the **effective post-heal** env | §3-C3 (+ heal-survival pin) | protected-builtin immutability preserved |
| **C4** | Degraded servers degrade alone | no per-server degraded registry; `upgrade_tools.py:2088-2092` reads a non-existent top-level `reasons` | Per-server degraded state: which servers, why, since when; recoverable; **grace never masks**; **journal writes SUPPRESSED while a promote txn is live** (buffered, newest-wins, flushed on txn close / graceful shutdown, never dropped) | §3-C4 (+ txn-safety pin) | daemon + readiness survive; `in_flight` never dropped by a daemon write |

---

## 3. The 12 requirements in detail

### A1 — BOOT/BURST GRACE WINDOW for the readiness detector

**Requirement (verbatim).** *"BOOT/BURST GRACE WINDOW for the readiness detector. Must distinguish 'busy but
recovering' from 'broken' during a bounded post-boot / post-resume-burst window. DESIGN IT EXPLICITLY: what arms
it, how long it lasts, what resets it, how it is surfaced. The C-1 never-green protection MUST STILL fire on a
genuinely broken release, but must NOT fire on a healthy release merely observed during a load spike."*

**Current behaviour.**
- A grace window **already exists** but is **anchored to RESTART, not to the load episode**:
  `scripts/upgrade/lib.sh:157-158` (`BOOT_GRACE_S="${BOOT_GRACE_S:-60}"`,
  `PROMOTE_BOOT_GRACE_S="${PROMOTE_BOOT_GRACE_S:-${BOOT_GRACE_S}}"`), armed at
  `scripts/upgrade/lib.sh:4266` (`_boot_grace_start`, inside `restart_via_launcher` at `:4064`), and evaluated by
  `_boot_grace_active` (`scripts/upgrade/lib.sh:3197-3212`) — it ends on the earliest of (a) 60 s elapsed or
  (b) 3 consecutive non-transient greens.
- The grace hook lives in `_probe_once_classified` (`scripts/upgrade/lib.sh:3022-3027`): a **transient** becomes
  `boot-grace-suppressed` and returns the dedicated code **3**; red still counts, green still counts.
- **The grace is time-only.** It has no pool-health signal and no burst signal. In the incident the daemon was
  ~75 s old at the first green readyz (20:42:31) and the burst peaked 20:43:33→20:45:21, so the 60 s grace had
  already expired when the storm hit.
- **A grace that only suppresses is not enough (premise P13).** The soak counter
  (`scripts/upgrade/lib.sh:5216-5221`) requires a hard `"green"` on both probes; anything else resets it to 0.
- The **self-sustaining leak class** is separate: the readiness detector's own `queue_freshness` probe leaks a
  transaction per tick (per `incident-20261026-db-pool-idle-in-transaction.md`; the probe binds the **sync**
  SQLAlchemy engine at `daemon/services/readiness.py:763-799` `make_queue_probe(engine)` and
  `daemon/services/readiness.py:565-573` `make_db_probe(engine)`), so probe→pool-pressure→timeout→retry→leak
  is a closed loop. **A time-based grace would engage on exactly that signature and mask it.**
- Budgets are **hardcoded module constants**, not env-tunable: `daemon/services/readiness.py:45`
  (`DB_PROBE_TIMEOUT_S = 0.5`), `:54` (`CHECKPOINT_SAVER_PROBE_TIMEOUT_S = 1.0`), `:59`
  (`QUEUE_PROBE_TIMEOUT_S = 2.0`).
- `ReadinessComposite` has **no** `first_seen_degraded` / consecutive-failure field today
  (`daemon/services/readiness.py:239-266`).
- The refresher is the arming surface: `daemon/api.py:2472-2486` creates the task, `daemon/api.py:3720-3820`
  is `_periodic_readiness_refresh_loop`, first tick at **t=0**, then every **10 s**
  (`daemon/config.py:2657-2666`).

**Intended behaviour.** A **class-driven** grace, evaluated **per readiness tick** by the refresher. The
normative definition is **§4.2 (P2)** and **§4.4 (P4)**; this row states how A1 consumes it and adds no
arming rule of its own.

1. **Eligibility is CLASS-DRIVEN, never conjunctive on boot-age/burst** (§4.2). The grace arms when the tick's
   degraded components are **all** grace-eligible (`probe_timeout`, `pool_busy`, `queue_stale`, `read_timeout`)
   and **no** never-eligible class is present (`boot_incomplete`, `identity_mismatch`, `hard_component_red`,
   `not_serving`, `pool_leak`, `unknown`). **Boot age and burst state are published as observable CONTEXT in
   `detail.grace` and MUST NOT be arming preconditions.** The prior-art class of **2026-10-09 / v0.18.5**
   (environmental DB churn — see §1.4) is covered by the **same** class-driven rule; it is **not** a
   post-boot-only special case.
2. **Context inputs (published, not arming).** Boot age (the daemon already exposes
   `LivezResponse.uptime_seconds`, `daemon/api.py:4793-4807`) and burst state are surfaced in `detail.grace`
   as context. The burst signal is daemon-side and **not** `inflight_turns` (premise P4: the resume path
   writes no heartbeats, so that field is blind to the resume burst) — the components that *know* they are
   bursting are (a) the auto-continue resume window (A3's hold is the signal) and (b) the MCP warmup window
   (the manager tracks `self._warmup_task` — `daemon/manager.py:2045`, `:3137`, `:3565-3566`).
3. **Leak-refusal input (hard negative)** — the grace **REFUSES to engage, and disengages**, when the
   `pool_leak` signal is present. The leak signal, its ORACLE/ADVISORY pool split, and its **streak-arm
   qualification** are defined normatively in **§4.4 (P4)**; A1 consumes them verbatim and adds nothing.

**Surface (hard contract).** The grace **must never render "healthy"**. A grace-held composite renders
`status="degraded"` with a new `detail.grace` block carrying `grace_active`, `grace_reason`,
`grace_expires_at` (and the pool-health inputs), **while the underlying component `reasons` remain present and
unmodified**. A suppressed reason string is a **defect**. The `detail` payload is the existing additive surface
(`daemon/services/readiness.py:239-266` already ships `detail.inflight_turns` as an additive precedent).

**Reset / bound.** Normative: **§4.3 (P3)** — **ONE** clock, **observation-relative**
(`grace_expires_at = first_grace_eligible_observation + GRACE_HORIZON_S`), **ONE** number
(`GRACE_HORIZON_S = 120.0` s), the grace is **never extended**, re-arming requires the grace to have **fully
lapsed**, and there is **no boot-relative ceiling** (the valueless `grace_abs_ceiling_s` is **deleted** from
this file). The grace also ends early on a never-eligible class, on a leak signal, or on a hard-red
observation (a real red is never grace-held). The grace-eligible observation window can never extend past the
soak's absolute bound (`soak_entry + 300 s`, §4.3).

**Pool tagging.**
> Normative source: requirements.md §4.4 (P4 — leak signature, ORACLE/ADVISORY pool split, streak-arm
> qualification).

The **ORACLE** pool is **P2** (the psycopg checkpoint-saver pool, `daemon/persistence.py:273-293`); the
**ADVISORY** pool is **P1** (the SQLAlchemy manager engine, `daemon/repositories/factory.py:136-137`
`pool_size=5` / `max_overflow=10`, `:188` / `:220` `pool_pre_ping=True`), whose signals are published in
`detail.pool_state` but do **not** arm the leak class. **The P1 mechanism is NOT addressed by A4** (A4 sizes and
surfaces the **P2** saver pool; see §7 non-goal 3).

**Acceptance criterion.** See **A5(i)** and **A5(iv)** in §5.

**Safety property preserved.** *The C-1 never-green rollback protection* — a genuinely broken release must
still be rolled back. Achieved by: hard reds are never grace-held; the grace is absolutely bounded; a
leak signature disengages it; and a release that never produces a green still ends at cap-reach with
`consecutive_green < 3` → `return 1` → auto-rollback (`scripts/upgrade/lib.sh:5242`).

**Anchor verification note.** The brief's ":152-160 boot-window grace" is **:157-158** in this tree
(`:152-156` is the comment block). The brief's ":161-169 busy-amnesty" is **:168** for the constant
(`:161-167` is its comment). The brief's ":3178-3198" for the boot-grace state is **:3184-3212** in this tree.
All of these are cosmetic shifts; the semantics are as described.

---

### A2 — SOAK SEMANTICS: "3 consecutive greens" must be REACHABLE

**Requirement (verbatim).** *"SOAK SEMANTICS. '3 consecutive greens' must be REACHABLE. The soak window must
extend/adapt so it can observe the system AFTER the burst settles, not only inside it. Keep the never-green
rollback protection for a genuinely broken release. (Promote-probe semantics today, `scripts/upgrade/lib.sh:3066`:
0=green, 1=red, 2=transient-exhausted; 'transient-exhausted' = timed out with no response and is counted as
'no green'.)"*

**Current behaviour.**
- Constants: `scripts/upgrade/lib.sh:146-149` — `ADAPTIVE_SOAK_FLOOR_S=60`, `ADAPTIVE_SOAK_CAP_S=120`,
  `ADAPTIVE_SOAK_CONSECUTIVE=3`, `ADAPTIVE_SOAK_CADENCE_S=15`.
- **The soak gets NO retry.** `gate_soak` (`scripts/upgrade/lib.sh:5053`) calls `_probe_once_classified`
  **directly** at `:5161` (`/livez`) and `:5181` (`/readyz`). The gate phases do get bounded retry —
  `gate_livez`/`gate_readyz` use `_probe` (`scripts/upgrade/lib.sh:4993-4994`) → `_probe_with_retry`
  (`:3069`, 3 retries, 1 s/2 s/4 s backoff).
- **Counter semantics:** `scripts/upgrade/lib.sh:5216-5221` — the counter increments only when
  `livez_rc=0` **and** `_PROBE_STATUS=green` **and** `readyz_rc=0` **and** `_PROBE_STATUS=green`; **everything
  else resets it to 0**. `_probe_with_retry`'s own docstring (`scripts/upgrade/lib.sh:3085-3097`) states that
  soft-greens (busy-amnesty) and rc-3 (boot-grace-suppressed) do **not** advance the counter.
- **ALIASING DEFECT (confirmed) — the counter's target semantics are normative in §4.2 (P2).**
  `_PROBE_STATUS` is a **global** overwritten by the **second** probe
  (`:5181`), so the `livez_rc` half of the condition at `:5216` actually tests the **readyz** status. Net
  effect, in **both** directions: a *livez busy-amnesty soft-green + readyz hard green* **increments** the
  counter (contradicting the documented "soft-greens do NOT count" invariant — **more permissive than
  documented**), while a *livez hard green + readyz busy-amnesty soft-green* **resets** it (more restrictive).
  The A2 fix must repair the aliasing (capture each status immediately after its own probe) as part of the
  counter-semantics change, and pin both directions.
- Cap arithmetic: `scripts/upgrade/lib.sh:5080-5109` — effective cap =
  `min(ADAPTIVE_SOAK_CAP_S, ceremony_remaining_s - 60, soak_s)`. If `ceremony_remaining_s - 60 < floor`, the
  soak journals `soak_deferred` (`:5090`) and **returns 0 = soak SKIPPED** (the gates remain the source of truth).
- Early exit requires **both** `consecutive_green >= 3` **and** `soak_elapsed >= 60` (`:5232-5236`).
  Cap-reach with `consecutive_green < 3` → `_warn` (`:5242`) → `return 1` → auto-rollback → **quarantine**
  (`:696` in `promote.sh`). Quarantine is cleared **only by a re-stage** (`scripts/upgrade/stage.sh:618` →
  `journal_quarantine_clear`, `scripts/upgrade/lib.sh:1224-1240`).
- Conservative mode exists: `ENSEMBLE_SOAK_CONSERVATIVE=1` → flat soak, sleep 30, no early exit, `soak_s=300`
  (`scripts/upgrade/lib.sh:5065-5067`, `:5113-5120`).

**Intended behaviour.**
1. **Fix the aliasing** — capture `_PROBE_STATUS` into a per-probe local immediately after each
   `_probe_once_classified` call; the counter then evaluates the intended per-probe class.
2. **Change the counter semantics during an ACTIVE grace** (this is the load-bearing change — premise P13).
   > Normative source: requirements.md §4.2 (P2 — grace eligibility classes, aggregation, counter observation
   > kinds: GREEN / HELD / RESET, the livez hard-green precondition, the reset-on-green rule, the hold
   > budget).

   A2's job is to **implement** the register's rule at the counter site — the three observation kinds, the
   liveness precondition on the HELD kind, the reset-on-green behaviour, and the hold budget — and to
   **repair the `_PROBE_STATUS` aliasing** so the per-probe status locals the register refers to are the ones
   actually evaluated. The A2-specific addition is the **liveness half**: a liveness failure during the grace
   window is a **RESET**, never a HOLD, and the pin stream must carry that leg explicitly. The reset-on-green
   behaviour is what the S7 arithmetic (3 greens over ~9 observations inside a burst) depends on, so it must be
   **pinned**, not merely implemented. **A2 adds no rule of its own** — if §3 and §4 ever disagree, §4 wins.
3. **Extend the window past the burst** — the effective cap grows while a burst/grace is active, bounded by the
   **absolute** bound the register defines (**§4.3 (P3)** — `soak_entry + ADAPTIVE_SOAK_CAP_S +
   GRACE_EXTENSION_CAP_S`). *(The name `soak_abs_cap_s` is **WITHDRAWN**: it is not a knob the register defines;
   the bound is the expression above.)* The floor stays as the wall-clock minimum; the cap may grow but never
   shrinks below the current `min(120, remaining-60, soak_s)` behaviour without a journaled reason.
   **Effective-cap recompute timing is normative** (adjudicated S8): the deadline is recomputed **from each
   (re)grant of the grace**, and the total never exceeds **entry + `ADAPTIVE_SOAK_CAP_S` +
   `GRACE_EXTENSION_CAP_S`**. Concretely: each grace grant/re-grarm re-anchors the soak's deadline at
   `now + effective_cap` (so the soak observes *after* the burst settles, not only inside it), but the
   absolute bound `soak_entry_time + ADAPTIVE_SOAK_CAP_S + GRACE_EXTENSION_CAP_S` is never exceeded — a
   burst that never settles still terminates at the absolute bound and routes to the C-1 auto-rollback with
   `consecutive_green < 3`.
4. **Evaluate reusing/extending conservative mode** — the conservative path is a strict, flat, no-early-exit
   soak. Recommendation: **reuse it as the "long observation" mode** (cap 300 s, no early exit) rather than
   inventing a third mode, and keep the adaptive mode as the default. The reviewer's call; the AC below is
   mode-agnostic.
5. **Never-green survives — normatively §4.1 (P1), UNCONDITIONAL.** If the release never greens (or only ever
   grace-holds) the cap is reached with `consecutive_green < 3` → **return 1 ⇒ ADR-005 auto-rollback (+
   quarantine)**. There is **no** fairness / observation-window condition on the rollback path and **no**
   cap-reach outcome that returns 0; a cap-reach has exactly **two** outcomes (`>= 3` ⇒ return 0; `< 3` ⇒
   return 1). The ceremony-budget truncation case is the **pre-soak deferral** (`soak_deferred`, returns 0
   BEFORE the soak loop) and is **not** a cap-reach outcome. Any fairness diagnostic is **diagnostic only** —
   surfaced in the journal line, pinned as **not** a success path, and it **MUST NOT** influence the return
   value. The grace must not be able to manufacture a green.

**Acceptance criterion.** See **A5(i)** and **A5(iii)** in §5. Additionally, a **pin on the counter semantics**
must assert, against a fake probe sequence, that: (a) `green, green, grace-held, green` reaches 3; (b)
`green, grace-held, grace-held, grace-held` never reaches 3 (never-green preserved); (c) `green, hard-red` resets
and fails fast even inside a grace window; (d) the livez/readyz aliasing no longer lets a soft-green count;
(e) **`livez-transient-during-grace` RESETS** (W-1's explicit leg — a liveness failure is never a hold);
(f) the HELD predicate is asymmetric as specified in **§4.2 (P2)** — `livez hard-green +
readyz grace-eligible-transient` **holds**, while `livez soft-green (busy-amnesty) +
readyz grace-eligible-transient` **resets**; (g) after a run of grace-held observations, a genuine green
**resets `consecutive_hold` to 0** (S7 — the "3 greens over 9 observations" arithmetic depends on
reset-on-green; rule per **§4.2 (P2)**); (h) the effective cap is recomputed from each grace
(re)grant and never exceeds entry + `ADAPTIVE_SOAK_CAP_S` + `GRACE_EXTENSION_CAP_S` (S8); (i) a green
following **more than `MAX_HOLDS_PER_GREEN = 2`** consecutive holds does **not** count toward the 3 (§4.2 P2)
— and a daemon that never greens still ends at cap-reach below 3 and **rolls back** (§4.1 P1).

**Safety property preserved.** *The never-green rollback protection* and *fail-fast on a hard red*.

**Anchor verification note.** The brief's ":5178-5198" for the rc-3 handling is **:5178-5179** (livez) and
**:5193-5194** (readyz) in this tree; the counter is **:5216-5221**; the cap-reach warn is **:5242**.

---

### A3 — AUTO-CONTINUE HOLD DURING A PROMOTE TRANSACTION

**Requirement (verbatim).** *"AUTO-CONTINUE HOLD DURING A PROMOTE TRANSACTION. The resume burst is the largest
single slice of the saturation. Sequence auto-continue resumes AFTER the ceremony, or hold them while a promote
txn is open. NOTE: `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART` only gates the BOOT pass — it CANNOT suppress a
resume that happens mid-soak. Needs a promote-txn-aware hold (the promote journal / state.json in_flight is the
anchor)."*

**Current behaviour.**
- The kill-switch is read **once at boot** and is not a live lever: `daemon/services/auto_continue_boot_pass.py:145-149`
  (`AUTO_CONTINUE_KILL_SWITCH_ENV = "ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART"`), read at `:176` inside
  `_auto_continue_enabled()` (`:162-179`). The boot pass is `continue_running_instances_after_restart`
  (`:272`), invoked at `daemon/api.py:2703`.
- **The runtime resume dispatcher** is `_schedule_explicit_handle_resume` (`daemon/manager.py:13162`), which
  ends with a **fire-and-forget** `asyncio.create_task(self._resume_processing_background(...))` at
  `daemon/manager.py:13671-13684`. The boot pass does **not** await it.
- **The second dispatcher** is `EligiblePendingSweepService` (`daemon/services/eligible_pending_sweep.py`),
  wired at `daemon/api.py:1493-1528`, interval 90 s, `min_pending_age_seconds=60`
  (`daemon/config.py:2185-2187`); its loop sweeps **before** sleeping
  (`daemon/services/eligible_pending_sweep.py:399`) → **the first tick is immediate**. It is **always on**,
  with **no kill switch**.
- The auto-continue stagger exists but does not fire for small bursts: `STAGGER_EVERY=5`,
  `STAGGER_SLEEP_SECONDS=2.0` (`daemon/services/auto_continue_boot_pass.py:263-264`) — with 4 instances, **no
  stagger fires**.
- **The promote txn anchor exists and is already readable from the daemon**:
  `daemon/tools/upgrade_tools.py:693-723` `_journal_read(install_dir)` — a torn-safe, **non-mutating** read of
  `<install_dir>/releases/state.json`; it is already called at boot (`daemon/api.py:2619`).
  `daemon/tools/upgrade_tools.py:832-845` `_in_flight_summary` reads the same `in_flight` dict.
  `daemon/tools/upgrade_journal.py:1028` `read_pending_op`; `daemon/tools/upgrade_journal.py:1943`
  `reconcile_pending_op` already **returns None when `in_flight` is a dict** — "promote.sh's txn is live — the op
  tracks a real run" (`:1959-1961`).
- **The daemon never writes `in_flight` kind="promote"** (premise P5): the only daemon write is
  `kind="restart"` (`daemon/tools/upgrade_tools.py:2891-2902`); the promote txn is opened by the shell
  (`scripts/upgrade/promote.sh:327`) and closed at `scripts/upgrade/lib.sh:1115-1118` (`journal_close_txn`).
- **Existing refusal vocabulary**: `daemon/tools/upgrade_tools.py:2669-2683` refuses with reason
  **`"pipeline-busy"`** when `in_flight` is present, and the message already explains that "a restart txn is
  completed by restart.sh, a promote txn by promote.sh".
- `daemon/tools/upgrade_journal.py:281-319` `journal_owner_heartbeat_fresh` +
  `OWNER_HEARTBEAT_FRESH_S = 60` (`:156`) — a freshness predicate over `in_flight.last_heartbeat`, consumed by
  the sweep (`daemon/services/upgrade_journal_sweep.py:2008-2093`).
- Operational constraint (house rule, from the RCA): the daemon's operational env must be set in
  `/home/nea/agents-ensemble/.env`; `systemctl set-environment` does **not** reach it. **A3 must not depend on a
  systemd-level env lever** — a runtime hold must be **in-process**.

**Intended behaviour.** A **live, in-process, promote-txn-aware hold**:
- **Anchor:** the shell-written `in_flight` record in `<install_dir>/releases/state.json`, read through the
  **existing** non-mutating reader (`daemon/tools/upgrade_tools.py:693-723`) — no new file, no new writer, no new
  journal surface. Reuse the existing `"pipeline-busy"` vocabulary for the hold's observable state.
- **Scope — FOUR seams are covered** (explicit decision, adjudicated):
  1. the **boot auto-continue pass** (`daemon/api.py:2703`, `continue_running_instances_after_restart`,
     `daemon/services/auto_continue_boot_pass.py:272`) **with the release trigger** above;
  2. the **runtime resume dispatcher** (`daemon/manager.py:13671-13684`);
  3. the **eligible-pending sweep** (`daemon/services/eligible_pending_sweep.py`) — always-on, no kill switch,
     immediate first tick, heals restart-stranded PENDING rows; its 60 s minimum age bounds the overlap;
  4. the **sister pass** `sweep_orphaned_live_turns`
     (`daemon/services/auto_continue_boot_pass.py:726`, wired at `daemon/api.py:2708`) — **added by
     adjudication**: its orphans are **task-less**, so StaleTaskRecovery **cannot** reap them; a deferral here
     without a release trigger would strand them permanently, which is strictly worse than the pass it
     protects.
- **Mechanism:** a **deferral**, not a drop. A resume that would fire while a promote txn is open is **held**
  (queued/re-armed) and released when the txn closes (`in_flight` → null) or a bounded timeout expires. The
  hold must be **observable** (a journal-free, in-process counter surfaced in the readiness detail and/or an
  existing ops surface) so an operator can see that resumes were deferred rather than lost.
- **THE RELEASE RULE IS NORMATIVE — it lives in the register, not in this row.**
  > Normative source: requirements.md §4.7 (P7 — A3 release rule: the promote-txn LIVE predicate, the
  > boot-window liveness allowance, the HOLD-only boot-window release condition, the release hysteresis, and
  > the release driver).

  A3's job is to **implement** that rule, not to restate it: the hold module
  (`daemon/services/promote_txn_hold.py`) keeps a **per-seam deferred-candidates list**, re-consults the
  predicate on the register's cadence, and releases the deferred candidates **through their ORIGINAL seams**
  (the checkpoint-resume path — never a cold re-dispatch) when the register's release condition is met. The
  driver is registered on the daemon's ordered-shutdown path, and the bounded release timeout is the crash
  backstop. **A3 adds no predicate of its own** — if §3 and §4 ever disagree, §4 wins.

  (Observed on disk: the shell's flipped-stamp call is at `scripts/upgrade/promote.sh:412` and the restart
  section header at `:415`; the register's `:413`/`:415` refer to the same two lines.)

- **RELEASE TRIGGER (load-bearing — the naive version is WRONG).** The boot auto-continue pass runs **exactly
  once per boot** (`daemon/api.py:2703` is its single call site). During a promote the daemon restarts **before**
  the gates, so that pass always executes with the txn already live, defers every candidate — and there is **no
  next pass this boot**. A hold that merely defers to "the next pass" therefore **loses the checkpoint resume**:
  deferred candidates fall through to StaleTaskRecovery's `force_cancel_and_schedule_retry` at
  ~`boot+10 min` (`daemon/services/auto_continue_boot_pass.py:69`), which is a retry, not a resume. The release
  trigger must therefore be **owned by the hold module**: a **per-seam deferred-candidates list** that
  **re-consults the promote-txn predicate on a short bounded cadence** — a **read-only** `state.json` read,
  **default 5 s**, executed **only while deferrals are outstanding** (zero cost when nothing is deferred). On
  flip-to-false it **releases the deferred candidates through their ORIGINAL seams** (the checkpoint-resume
  path — never a cold re-dispatch), and it **composes with fail-open-on-stale** (a torn/unreadable journal
  releases rather than wedging the hold).
- **Not** the boot kill-switch: `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART` remains a **boot** gate only. A3
  must not require a systemd-level or `.env`-level change to take effect mid-ceremony.
- **Interaction with A1:** the hold doubles as the **burst signal** A1 needs (A1's second input) — the daemon
  *knows* it is in a resume window, which `inflight_turns` cannot report (premise P4).

**Acceptance criterion.** See **A5(i)** in §5, plus the dedicated pin (the PIN, verbatim): **a deferred
candidate resumes via the CHECKPOINT path after the txn closes.** Concretely, with a synthetic promote txn
present in a scratch `state.json`: (a) no resume task is dispatched while the txn is open; (b) the deferred
candidate **resumes via the checkpoint-resume path** (checkpoint continuation, not a cold re-dispatch) after
the txn closes — asserted on the resume *mechanism*, not merely on "something fired"; (c) the release happens
without any new boot-pass invocation (the release trigger is the hold module's own cadence); (d) the hold is
observable; (e) with no txn present, behaviour is byte-identical to today; (f) a torn/unreadable journal
releases (fail-open-on-stale), it does not wedge the hold.

**Safety property preserved.** *No resume is lost.* The hold defers; it never drops. And *the boot kill-switch
keeps its meaning* (boot-only).

**Anchor verification note.** The brief's "the promote journal / state.json in_flight is the anchor" is
**confirmed** and the daemon-side reader already exists — no new anchor is needed. `journal_owner_heartbeat_fresh`
lives at `daemon/tools/upgrade_journal.py:281-319`, **not** `daemon/services/upgrade_journal.py`.

---

### A4 — CHECKPOINT POOL SIZING for the burst

**Requirement (verbatim).** *"CHECKPOINT POOL SIZING for the burst. The readiness probes (aget sentinel,
pool.check(), SELECT 1) must be able to obtain a connection under load. Size the pool with headroom relative to
llm_concurrency=10 and the concurrent-checkpointing turns."*

**Current behaviour.**
- The **checkpoint saver pool** is built in `daemon/persistence.py:273-293`:
  `AsyncConnectionPool(conninfo=…, open=False, min_size=1, max_size=5, check=AsyncConnectionPool.check_connection,
  max_lifetime=3600, max_idle=600, reconnect_timeout=300, kwargs={autocommit, prepare_threshold, row_factory})`.
  The comment at `:277-282` says verbatim **"(min_size/max_size) is hard-coded, not config-tunable."** There is
  **no `timeout` kwarg** → psycopg_pool's library default **30.0 s** applies.
- **Lock contention dominates pool size.** `langgraph/checkpoint/postgres/aio.py:59` declares
  `self.lock = asyncio.Lock()` and the saver serialises **all** cursor operations through that **one** lock. The
  readiness sentinel read (`saver.aget_tuple`, `daemon/services/readiness.py:725-745`) takes the **same** lock as
  every production checkpoint write, so the probe **queues behind the write storm regardless of pool size**.
  Enlarging the pool does **not** shorten that queue.
- **Ghost waiters.** Pool exhaustion **queues** the Nth acquirer; `PoolTimeout` fires only at the 30 s pool
  deadline. The readiness probe abandons at **0.9 s inner** (`CHECKPOINT_SAVER_PROBE_TIMEOUT_S - 0.1`,
  `daemon/services/readiness.py:733`) / **1.0 s outer** (`daemon/services/readiness.py:54`), but the pool retains
  the waiting-queue entry until the 30 s deadline — **every timed-out probe leaves a ghost waiter** that will
  consume a slot when granted. At the 10 s refresh cadence this is a **self-amplifying pool drain**.
- **Sizing arithmetic.** `llm_concurrency` (`daemon/config.py:595`, default 10) is acquired around the
  **entire `graph.astream(...)` turn** (`daemon/services/instance_messaging.py:5407-5408`:
  `async with self._llm_semaphore:` … `async for event in graph.astream(...)`), so it bounds concurrent turns and
  transitively concurrent checkpoint writers. The effective ceiling is
  **min(10, WORKER_POOL_SIZE=5 + CHAT_WORKER_POOL_SIZE=2) = 7** (`daemon/constants.py:91` and `:103`).
  **`max_size=5 < 7 writers + the probe`.** No checkpoint-specific semaphore exists; `checkpoint_interval` is
  declared at `daemon/config.py:942` and is **never read** anywhere in `daemon/`.
- **Two distinct pools — do not conflate.** The `SELECT 1` and `queue_freshness` probes run against
  **`manager.engine`**, the **SQLAlchemy** pool (`daemon/repositories/factory.py:136-137`
  `pool_size=5`/`max_overflow=10`, `:188`/`:220` `pool_pre_ping=True`) — this is the pool the 22:10–22:50Z RCA
  says is **leaking**. The **checkpoint saver pool** is a *separate* psycopg `AsyncConnectionPool`
  (`daemon/persistence.py:273`).
- **The pool is being poisoned underneath it.** `mission_record_service.mint_boot_epoch_if_none` raises
  `UniqueViolation` on `ux_mission_records_anchor_open` **4× per boot** — in **both** RCAs; the new RCA shows the
  exception raised inside a transaction **without a rollback**, leaking the connection.

**Intended behaviour.** A4 is **three** changes, and one honest disclaimer:
1. **Contention (the real blocker).** Address the **saver-lock queue**, not only the pool size. Either (a) a
   dedicated **probe path** that does not contend on the saver's single lock, or (b) cover the contention with
   the A1 grace. The implementer must state which; the AC is written to accept either, but **a pool-size-only
   change does not satisfy A4** and must be rejected at review.
2. **Ghost waiters.** Either cancel the queued position on probe timeout, or bound the pool's own `timeout`
   (a `timeout=` kwarg on the `AsyncConnectionPool` construction) so a ghost waiter cannot survive past the
   probe budget.
3. **Sizing.** Create the missing config knob (house pattern: `daemon/config.py` field + env var +
   `config.yaml` interpolation, and `daemon/constants.py` for the default) and size for the **concurrent-writer
   arithmetic**: **7 writers (min(10, 5+2)) + 1 probe**, not `llm_concurrency=10`. The knob must be applied to
   **both** the saver pool (`daemon/persistence.py:273-293`) and the asyncpg pool
   (`daemon/persistence.py:310-316`) or the row must say explicitly that only the saver pool is in scope.
4. **Honesty clause (mandatory wording).** Sizing **does not cure** the leak. The A4 text must not present pool
   enlargement as a fix for a pool that is being poisoned underneath it. The leak is a **separate defect**
   (§7 non-goals 3 and 4) with its own follow-up.
5. **Surface the pool state.** The readiness `detail` must carry the **pool-health inputs** A1's leak-refusal
   needs: checked-out count, idle-in-transaction count, and a leak indicator, per pool (SQLAlchemy vs psycopg
   saver), so an operator can **see** the poison instead of inferring it from timeouts.

**Acceptance criterion.** See **A5(i)** in §5, plus: a config-resolution pin proving the new knob resolves
through the house 4-tier ladder (DB > env > yaml > default) and that the saver pool is constructed with it; a
**ghost-waiter** pin proving that N timed-out probes leave **0** waiting-queue entries in the pool; and a
**detail-payload** pin proving the pool-health fields are present and are tagged per pool.

**Safety property preserved.** *Sizing must not make the pool a DoS amplifier* (a `max_size` above the DB's
own `max_connections` budget is a new outage class) — the implementer must state the DB-side connection budget
and keep the pool within it, with the default unchanged unless the operator opts in.

**Anchor verification note.** The brief's "the readiness probes (aget sentinel, pool.check(), SELECT 1)" mixes a
**stale** probe name into the current one — `pool.check()` is gone (premise P3). The three real probes are:
sentinel `aget_tuple` (checkpoint saver pool), `SELECT 1` and the queue aggregate (SQLAlchemy pool).

---

### A5 — REGRESSION PINS

**Requirement (verbatim).** *"REGRESSION PINS proving: (i) a HEALTHY release under a simulated burst is NOT
rolled back; (ii) a GENUINELY BROKEN release IS still rolled back (never-green protection intact); (iii) the soak
still fails fast on a real regression; (iv) the grace window is observable and cannot be used to mask a real
fault indefinitely."*

**Current behaviour.** The soak is shell-side with **no** regression pack for the burst/never-green distinction;
the failure classes are currently conflated into a single `red` / `transient` split
(`scripts/upgrade/lib.sh:2986-3001`).

**Intended behaviour.** Four named, mutation-verified pins with a real-PostgreSQL harness. Detail in **§5**.

**Acceptance criterion.** §5.

**Safety property preserved.** The never-green rollback protection **is** the subject of pins (ii) and (iv).

**Anchor verification note.** N/A — new work.

---

### B1 — MCP SERVER WARMUP must NOT BLOCK the daemon's main asyncio event loop

**Requirement (verbatim).** *"MCP server warmup must NOT block the daemon's main asyncio event loop.
Parallelize it / move it off the loop (background thread or subprocess supervision). User's words: 'use more CPU
if possible (example: start MCP servers but don't block main loop)' — the host HAS spare cores; the constraint is
that the single asyncio thread must never be starved (see incident-20261022-eventloop-cpu-peg.md: one CPU-bound
task pegs the whole loop; this is the loop-starvation family)."*

**Current behaviour.**
- The warmup **is** already a background **task** — `daemon/manager.py:3137`
  (`self._warmup_task = loop.create_task(self._warmup_and_report())`) and a second creation site at
  `daemon/manager.py:3565-3566`; the pool itself is built by `_init_warmup_pool`
  (`daemon/manager.py:3013-3135`); `_warmup_and_report` at `daemon/manager.py:3154-3170`. **Being a task is not
  being off the loop** — a task still runs **on** the single asyncio thread.
- **Concrete off-loop violations (the named sites):**
  - `daemon/mcp/stdio_wrapper.py:485-486` wires `on_ready=self._stamp_children`; `_stamp_children`
    (`daemon/mcp/stdio_wrapper.py:494`) performs **synchronous filesystem reads** on the event-loop thread:
    `read_bytes` (`daemon/mcp/stdio_wrapper.py:116`), `read_text` (`daemon/mcp/stdio_wrapper.py:132`),
    `list(root.iterdir())` (`daemon/mcp/stdio_wrapper.py:179`) — under a `threading.Lock`, **inside the MCP spawn
    path**.
  - `daemon/mcp/stdio_wrapper.py:306` `await self._ready.wait()` — **no timeout**.
  - `daemon/mcp/warmup_pool.py:275` `streams_cm.__aenter__()` sits **outside** the `asyncio.timeout(60)` that
    starts at `daemon/mcp/warmup_pool.py:278`.
  - The **warmup + session path** under `daemon/mcp/` has **zero** `to_thread` / `run_in_executor` (the only
    `to_thread` uses under `daemon/mcp/` are in `kb_keys.py:192-198` and `kb_server.py:77,:92`, which serve the
    internal KB MCP server and are **not** on the warmup path).
- **Sizing facts.** The MCP pool size defaults to **1** (`daemon/config.py:2894` `default_pool_size`). With pool
  size 1 the **first** instance takes the pooled connection and **subsequent instances fall through to
  cold-start, each spawning its own `npx` child** (`daemon/services/mcp_service.py:104-112` and `:167`) — this is
  the "×4 instances" mechanism in the incident. Global `_schema_cache_lock`
  (`daemon/services/mcp_service.py:203`) + per-instance `_preload_locks` (`:194`);
  `EMPTY_DISCOVERY_RETRY_THROTTLE_S = 30.0` (`:296-306`).
- The daemon's main process pegged at **105–114 % CPU with zero agent work** (RCA addendum) — the loop-starvation
  family, whose Oct-22 fix item #4 is exactly this requirement.

**Intended behaviour.**
- **Offload the loop-thread work**: the `_stamp_children` /proc scan moves to a worker thread
  (`asyncio.to_thread` or an executor) so the spawn path never performs blocking filesystem I/O on the loop.
- **Bound both unbounded waits**: `stdio_wrapper.py:306` gets a timeout; `warmup_pool.py:275` is moved **inside**
  the bounded budget (or given its own).
- **Move the CPU/IO-heavy init off the loop thread**: server init (subprocess spawn + handshake + tool discovery)
  runs in a **dedicated worker thread / executor** (the host has spare cores — the user's explicit instruction),
  with the loop only awaiting the result. The requirement is that the **main asyncio thread is never blocked**,
  not that the work is eliminated.
- **The daemon must remain responsive** while MCP servers are warming: `/livez` and `/readyz` must keep
  answering within their budgets.

**Acceptance criterion (thresholds are normative — a pin with no number is not a pin).** A **loop-lag pin**,
measured with a **periodic heartbeat task** on the event loop (the heartbeat records the delta between its own
expected and actual firing instants) while the **R-1 concurrent burst** runs (MCP warmup + job-recovery
reconcile + auto-continue resume, all overlapping).

*(HOUSE CONVENTION — not a §4 predicate: the loop-lag measurement shape is an operating convention, restated per phase by requirement; canonical thresholds: **§4.6 (P6)**.)*
- **p95 event-loop scheduling lag ≤ 250 ms**
- **max event-loop scheduling lag ≤ 500 ms**
- `/livez` and `/readyz` answer within their budgets throughout the burst.

**Why these numbers (the rationale that must appear with them):** the smallest readiness probe budget is the
**0.5 s `SELECT 1` probe** (`daemon/services/readiness.py:45`, `DB_PROBE_TIMEOUT_S = 0.5`), and servicing that
probe takes **multiple loop turns** (the `to_thread` hop, the worker-thread result, the composite write, the
next refresh tick) — so loop lag must stay **well under 500 ms** for the probe to complete **inside** its budget.
*(HOUSE CONVENTION — an operating convention, restated per phase by requirement; canonical thresholds: **§4.6 (P6)**.)*
A p95 above 250 ms or a max above 500 ms is a FAIL: the burst is starving the loop that the readiness
detector itself depends on, i.e. the B1 defect is present even when the daemon is not yet red. The pin
must also assert the two **bounded waits** separately: neither `stdio_wrapper.py:306` nor
`warmup_pool.py:275` may block unbounded (a timeout is required on both).

*(HOUSE CONVENTION — not a §4 predicate: this rationale is an operating convention, restated per phase by
requirement; the canonical threshold statement is **§4.6 (P6)**.)*

**Safety property preserved.** *MCP functionality is unchanged* — the offload is a scheduling change, not a
semantic one. And *readiness must not be gated on it* (see B2's narrowing).

**Anchor verification note.** The brief's "the whole `daemon/mcp/` has ZERO `to_thread`" is **not literally
true** — `daemon/mcp/kb_keys.py:192-198` and `daemon/mcp/kb_server.py:77,:92` do use `to_thread`; they are the
internal KB MCP server, **not** the warmup path. The claim as it applies to the **warmup + session path** is
correct.

---

### B2 — READINESS MUST NOT BE GATED ON MCP WARMUP *(NARROWED — see §6)*

**Requirement (verbatim).** *"READINESS MUST NOT BE GATED ON MCP WARMUP. A slow or broken MCP server must not
make /readyz flap — evidence is `GET /api/prompts/pending-count` -> 503 at 20:42:39Z, i.e. 21s BEFORE any resume,
purely because MCP warmup was churning."*

**Current behaviour — the premise does not hold.**
- `/readyz` (`daemon/api.py:4809-4870`) is an **O(1) cached** read of `app.state.readiness_composite`; the
  composite's four components are `database` / `queue_freshness` / `services` / `checkpoint_saver`
  (`daemon/services/readiness.py:295-350`) and contain **zero MCP references**. The refresher
  (`daemon/api.py:3720-3820`) is the sole toucher.
- `/api/prompts/pending-count` (`daemon/routers/prompts.py:1078-1102`) is a **plain DB count** with **no MCP
  reference**. Its 503s come from `_require_auth_on` (503 `AUTH_NOT_CONFIGURED`) or `_get_service` (503
  `PROMPTS_UNAVAILABLE`) — **not** from MCP warmup.
- The only 503 emitters in `daemon/api.py` are the `/readyz` handler itself (`:4869`, `:4877`); there is **no
  readiness-gated middleware** over `/api/*` routes.
- **The real coupling is event-loop starvation only** — the same loop that the MCP warmup churns is the loop the
  sentinel probe schedules onto (`daemon/services/readiness.py:725-745`,
  `asyncio.run_coroutine_threadsafe`), so a pegged loop ⇒ probe timeout ⇒ degrade.

**Intended behaviour (narrowed).** (a) A **regression pin** proving the readiness composite's four components
and the `pending-count` path contain **no MCP dependency** (so nobody later re-introduces one); and (b) the
**B1** loop-starvation fix, which is the actual remedy. **No decoupling mechanism is specified**, because the
coupling to be decoupled does not exist logically.

**Acceptance criterion.** A pin that asserts (i) the four readiness component names and their probe call-sites
reference no MCP symbol, and (ii) `pending-count` returns its normal shape when MCP warmup is deliberately
failing. The RCA's 20:42:39 `pending-count` 503 is recorded as **UNPROVEN** as degradation evidence (it may be
an auth-config 503).

**Safety property preserved.** *MCP must not become a readiness dependency* — now enforced by a pin rather than
by a (nonexistent) decoupling.

**Anchor verification note.** The RCA's use of the 20:42:39 `pending-count` 503 as proof of MCP-induced
degradation **cannot be corroborated in code** and is explicitly downgraded in §6.

---

### B3 — NO CHILD LEAKS

**Requirement (verbatim).** *"NO CHILD LEAKS. A failed MCP init attempt must kill its child processes (no
orphaned `npm exec` / node children) and must not leak an unclosed aiohttp session."*

**Current behaviour.**
- Cleanup code **exists**: `daemon/mcp/warmup_pool.py:333-344` — `except BaseException:` → `session.stop()` then
  `streams_cm.__aexit__(*sys.exc_info())`. The class docstring states "CRITICAL: Cleans up subprocess on any
  failure to prevent orphans."
- **But two unbounded waits sit outside that protection**: `daemon/mcp/stdio_wrapper.py:306`
  (`await self._ready.wait()`, no timeout) and `daemon/mcp/warmup_pool.py:275` (`__aenter__()`, outside the 60 s
  budget at `:278`). An init that hangs there never reaches the cleanup block.
- The init retry loop is `daemon/mcp/warmup_pool.py:285-310`: **3 attempts**, each `asyncio.timeout(10)`, with
  2 s/4 s backoff — i.e. a dead API host produces ~30 s of hangs per attempt, repeated by the health loop.
- A boot-time reaper for exactly this class already exists: `daemon/services/boot_subprocess_sweep.py:21-23`
  documents "a context7-mcp chain was observed running **4.2 days** across incarnations (2026-10-17 recon)";
  its kill-switch is `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES` (`daemon/services/boot_subprocess_sweep.py:67`).
- **The aiohttp claim cannot be grounded in the daemon's MCP path.** `grep -rn aiohttp daemon/mcp/` returns
  **zero** hits; aiohttp in this repo appears only in the Slack/Discord/Telegram source adapters
  (`daemon/sources/adapters/slack/adapter.py:12`, `daemon/sources/adapters/discord/adapter.py:45`,
  `daemon/sources/adapters/telegram.py:17`). The nearest daemon-side session resources in the MCP path are the
  MCP SDK `ClientSession` (`daemon/mcp/managed_session.py:13`) and anyio's stdio streams.

**Intended behaviour.** A failed MCP init attempt must (a) terminate the child process tree
(`npm exec` → node) and (b) close the MCP session/streams — verified by a **pid-liveness** assertion, not by
"cleanup was called". The two unbounded waits must be bounded (shared with B1). The "unclosed aiohttp session"
clause is **routed to a follow-up with the mechanism unverified** (see §6) — this commission must not claim to
have fixed an unverified leak; it must fix the two it can prove.

**Acceptance criterion.** A pin that forces an init failure (a server whose command never speaks the protocol),
then asserts: **0** surviving children matching the spawned command (pid-liveness check against
`/proc/<pid>/stat` starttime, PID-recycle-safe), and the session/stream closed. Plus a "no ghost waiters" style
assertion that a failed init does not leave the pool with a phantom connection.

**Safety property preserved.** *The pre-existing boot reaper stays in place* as the last line of defence; B3
does not replace it.

**Anchor verification note.** The brief's "leaking an unclosed aiohttp session" is **RCA-asserted but not
code-grounded** in `daemon/mcp/`. This is called out in §6 as a narrowed item.

---

### C1 — CIRCUIT-BREAK THE WARMUP

**Requirement (verbatim).** *"CIRCUIT-BREAK THE WARMUP. A server failing init N times is disabled/quarantined FOR
THE BOOT instead of retried forever. Surface it (visible + journaled + a readiness/observability signal) rather
than silently hanging."*

**Current behaviour.**
- **Nothing wraps server init.** `daemon/mcp/warmup_pool.py` has **zero** resilience references. The health loop
  (`daemon/mcp/warmup_pool.py:711-726`, started by `start_health_check` at `:728`) re-runs `health_check` every
  `health_check_interval` (default **60 s**, `daemon/config.py:2899`); a dead connection is evicted and
  `_replenish` (`daemon/mcp/warmup_pool.py:552-583`) creates a **new** one — which re-runs the 3×10 s init
  attempt chain. **Forever.** This is the RCA's "nothing circuit-breaks it", confirmed.
- The only breaker in the codebase is **one class**: `daemon/sources/circuit_breaker.py:20` (`failure_threshold=5`,
  `recovery_timeout=60.0`, CLOSED/OPEN/HALF_OPEN, CR-4 single-probe-in-flight guard at `:33-38`).
  `daemon/mcp/resilience.py:45` **imports** it (premise P1). `ResilienceManager`
  (`daemon/mcp/resilience.py:470-635`) is per-server-keyed and all-sync; `register()` is called from exactly one
  place — `daemon/services/mcp_service.py:1283` (inside `_get_resilience_config`, `:1245`),
  reached from `preload_mcp_tools` at `:875`; the method itself is `ResilienceManager.register` at
  `daemon/mcp/resilience.py:501`), and a server with **no** `ResilienceConfig` **never gets a breaker
  allocated**. context7 registers none — `daemon/services/mcp_service.py:853-854` (comment): *"resilience config
  (Plane: retry + cache + circuit breaker + fallback; context7 / webfetch: None)"*.
  The breaker is wired into the **tool-call** path (`daemon/mcp/tool_adapter.py`), **opt-in per server** — only
  Plane (`daemon/mcp/builtin_servers/plane.py:197`+ `resilience_config`).
- **"Disable for this boot" has no existing concept.** Candidates and their character:
  `MCP_DISABLE_BUILT_IN_{NAME}` (boot-scoped *intent* but a **persistent DB deactivation**),
  `mcp_servers.is_active=False` (**persistent**), and
  **`definition.is_available()`** (`daemon/mcp/builtin_servers/base.py:125-152`) — a **per-boot, in-memory**
  predicate consulted at **FOUR** sites: `daemon/manager.py:2813`, `daemon/manager.py:3057`,
  `daemon/services/mcp_service.py:579`, and `daemon/services/mcp_service.py:1130` (the **cold path**).
  `required_package is None ⇒ True`, so context7 is **always** "available" today.
- `CircuitBreaker.reset()` (`daemon/sources/circuit_breaker.py:114`) is the only **unsynchronised** mutator — a
  latent race if used concurrently.

**Intended behaviour.**
- **One breaker class**: `daemon/sources/circuit_breaker.py:20`. Do not introduce a second.
- **The breaker must be wired into the INIT path** (not just the tool-call path): a per-server init-failure
  counter; after **N** failed init attempts within the boot, the server's breaker **OPENs** and the server is
  **quarantined for this boot**.
- **Per-boot quarantine home: the `is_available()` seam** (`daemon/mcp/builtin_servers/base.py:125-152`). It is
  already the "skip this built-in cleanly" predicate consulted at the bootstrap and warmup-pool layers, it is
  **in-memory** (so it is naturally boot-scoped), and it already has a "no DB record, no connection attempt"
  contract. The implementation must add a per-process quarantine registry that `is_available()` consults (a
  classmethod cannot carry per-instance state by itself), and it must be consulted at **all FOUR** existing
  sites — `daemon/manager.py:2813`, `daemon/manager.py:3057`, `daemon/services/mcp_service.py:579`, and
  `daemon/services/mcp_service.py:1130` (the **cold path**) — so a quarantined server is skipped
  **everywhere**, not just in the warmup pool. Any pin asserting the consult sites must cover **all four**.
- **Surface it**: (a) **visible** — a WARN/ERROR line naming the server, the failure count and the reason;
  (b) **journaled** — a `journal_history_append` event in the promote journal's history array (the pattern
  already exists: `scripts/upgrade/lib.sh:5090` `journal_history_append soak_deferred …`); (c) an
  **observability signal** — a per-server degraded entry in the C4 registry, which the readiness detail can
  reference.
- **Recoverable**: the quarantine is boot-scoped, so the next boot retries — but a **half-open** probe on the
  breaker's `recovery_timeout` lets a recovered server back in without a restart.

**Acceptance criterion.** A pin with a server whose command always fails: after N attempts the server is
**quarantined for the boot** (no further spawn attempts), the quarantine is visible in the log, in the journal,
and in the observability surface, and the **other** servers are unaffected (degrade alone).

**Safety property preserved.** *One server's failure must not take down the pool or the daemon* (C4), and the
breaker's `reset()` must not be called from an unsynchronised path.

**Anchor verification note.** The brief's "two breakers" premise is **false** — one class
(`daemon/sources/circuit_breaker.py:20`). The brief's `is_available()` call sites (`:2767`, `:2997`,
`mcp_service.py:576`, `:1128`) are **stale**; observed at `daemon/manager.py:2813`, `:3057` and
`daemon/services/mcp_service.py:579`.

---

### C2 — FAIL FAST ON DEAD API HOSTS

**Requirement (verbatim).** *"FAIL FAST ON DEAD API HOSTS. Pre-resolve the API base at init and refuse cleanly
(DNS pre-check) instead of hanging to the 30s timeout. The context7 shape is the reference case."*

**Current behaviour.**
- No DNS pre-check exists anywhere on the MCP init path. The only bound is the retry loop
  (`daemon/mcp/warmup_pool.py:285-310`): 3 attempts × `asyncio.timeout(10)` + 2 s/4 s backoff, wrapped by the
  60 s outer budget at `daemon/mcp/warmup_pool.py:278`.
- The context7 shape: `daemon/mcp/builtin_servers/context7.py:33-40` base config has **no `env` key** and no URL;
  `get_config_schema()` (`:41-47`) returns **`[]`** — so the API base is **unsettable** through the template.
  The package fetches `${CONTEXT7_API_BASE_URL}/v2/entra/config/<audience>` **AT INIT** (RCA addendum) —
  note this is the package's own **internal composed URL**, **not** the withdrawn schema-entry name (audit
  marker: see the audit-trail note below §4.7).
- **G1 package-source correction (load-bearing — it invalidates the naive "pin both vars" reading).** In
  `@upstash/context7-mcp` v4.4.4 (`dist/lib/constants.js:7-10`), `CONTEXT7_BASE_URL` is a **HARDCODED const,
  NOT env-read**; only `CONTEXT7_API_URL` is an **effective lever**. The package's own
  `process.env.CONTEXT7_API_URL || \`${CONTEXT7_BASE_URL}/api\`` therefore has an **inert** right-hand side
  whenever the package ships its own hardcoded const — the derived-base reading in the RCA addendum is
  **only** correct when `CONTEXT7_API_URL` is unset *and* the package's const happens to equal the mirror.
  **Consequence:** the daemon's `CONTEXT7_BASE_URL` schema field is a **daemon-side MIRROR** — inert to the
  package, and the **C2 derivation input** — not a second package lever. (Package-source citation is to the
  npm package, not to a worktree file; it is not independently re-verifiable here.)
- **The daemon's own environment cannot reach the child** (premise P2): the MCP SDK's stdio client passes only
  `get_default_environment()` (HOME/LOGNAME/PATH/SHELL/TERM/USER). So a pre-check must run against the
  **resolved** base **the child would actually use** — i.e. the value in the row's `config.env` (or the derived
  default), **not** the daemon's process env.

**Intended behaviour.**
- A **pre-resolve step** at init (before spawning the child) that resolves the server's API base using the
  **same precedence the child will use** (row `config.env` → schema default → the package's own derived
  default), and performs a **bounded DNS resolution** of that host.
- On failure: **refuse cleanly** — do not spawn, do not hang — and mark the server **degraded** (C4) with the
  DNS reason. This is the same quarantine path as C1, so a dead host consumes **one** failure, not N.
- The pre-check must be **cheap and bounded** (a short DNS timeout, no TCP/TLS handshake) and must be
  **skipped** for servers with no URL-bearing base (webfetch, plane) — the requirement applies to URL-bearing
  servers only.
- For context7 specifically, the pre-check target is derived per the **G1 contract** (see §3-C3):
  **`CONTEXT7_API_URL` is the effective lever**; **`CONTEXT7_BASE_URL` is the daemon-side MIRROR** (inert to
  the package) whose value is the **C2 derivation input**. The pre-check resolves the **EFFECTIVE post-heal
  base** the child would actually use (§4.5 P5, §3-C3) — i.e. the row's `config.env` value for
  `CONTEXT7_API_URL` when set (the effective lever), else the **mirror** value — and the resolved address is
  **DISCARDED**, never pinned.
- **Thresholds and classification are normative — they live in the register, not in this row.**
  > Normative source: requirements.md §4.5 (P5 — DNS-refusal thresholds and classification).

  C2's job is to **implement** that rule: a bounded pre-resolve that runs **off** the event loop, whose
  outcome is classified per the register, and whose **failure** path refuses cleanly (no child spawned) and
  marks the server degraded (C4) with the DNS reason. C2 adds no classification of its own — if §3 and §4 ever
  disagree, §4 wins.

**Acceptance criterion.** A pin with a server whose resolved base host does not resolve: the init **refuses
(no child spawned) within the register's bound — 2.0 s + spawn overhead, and in all cases far below the 30 s
package-side hang budget (§4.5 P5)**; the server is marked degraded with the DNS reason; the readiness path is
unaffected. A second leg covers the **resolver-timeout** classification per **§4.5 (P5)**: a resolver TIMEOUT
must be recorded under its own class and must **not** quarantine the server on first occurrence.

**Safety property preserved.** *A dead host must not be able to consume the boot budget*; the pre-check is
bounded and never worse than the current behaviour.

**Anchor verification note.** The 30 s figure in the requirement is the **package-side** init hang; the
**daemon-side** bound is 3 × 10 s + backoff inside a 60 s outer budget
(`daemon/mcp/warmup_pool.py:278-310`). The AC must assert the *daemon-side* observable.

---

### C3 — PIN THE CONTEXT7 API BASE in the built-in MCP template

**Requirement (verbatim).** *"PIN THE CONTEXT7 API BASE in the built-in MCP template. The context7 row is a
protected built-in whose config_schema is EMPTY today -> the URL is unsettable -> a future host move silently
wedges every boot. Make the base URL settable/pinned in the template."*

**Current behaviour.**
- `daemon/mcp/builtin_servers/context7.py:33-40` — base config `{"transport":"stdio","command":"npx","args":["-y","@upstash/context7-mcp"]}`;
  **no `env` key**. `daemon/mcp/builtin_servers/context7.py:41-47` — `get_config_schema()` returns **`[]`**.
  `required_package` is `None`, so `is_available()` returns `True` (always available).
- The schema → row → child-env path exists: a schema entry with `section: "env"` becomes
  `env[key.upper()] = str(value)` (`daemon/mcp/builtin_servers/base.py:222-223`) → the row's `config.env` →
  `build_pooled_stdio_config` (`daemon/mcp/warmup_pool.py:852`) → the spawn at
  `daemon/mcp/warmup_pool.py:253-263` → `resolve_env` / `resolve_secret_markers` → `StdioServerParameters(env=…)`.
  The FE renders "No configuration fields available" for an empty schema.
- **Sanctioned write path**: `POST /api/mcp-servers/configure-builtin`
  (`daemon/routers/mcp_servers.py:491`). A `PUT` that changes a built-in's `config` is **403**
  (`daemon/routers/mcp_servers.py:760-768`, `ErrorCodes.BUILTIN_SERVER_PROTECTED`).
- **The hole**: `mcp_set_env` (`daemon/tools/infra.py:1527`) writes `mcp_servers.config.env` with **no
  `is_builtin` guard** — unlike the HTTP API. So the base URL is settable *only* through the unguarded tool
  path, which is **exactly backwards** from C3's intent (the protected-template path is the one that is empty).

**Intended behaviour.**
1. **Template schema — ONE unambiguous two-field instruction (per the G1 package-source correction; the
   earlier "add a `CONTEXT7_API_BASE_URL` entry" wording is WITHDRAWN — that variable name is read by neither
   the package nor the daemon).** Add **exactly two** `section: "env"` entries to the context7
   `get_config_schema()`, so they render in the UI and land in the row's `config.env` via
   `daemon/mcp/builtin_servers/base.py:222-223` (which upper-cases the key, so each schema key must be the
   **literal env var name**):
   - **`CONTEXT7_API_URL`** — the **EFFECTIVE LEVER**. This is the variable the package actually reads
     (`process.env.CONTEXT7_API_URL`). It has **no default**: when unset, the package falls back to its own
     hardcoded constant and the daemon has nothing to pin. Its field description must say so.
   - **`CONTEXT7_BASE_URL`** — a **daemon-side MIRROR**, **default `https://context7.com`**. Its field
     description **must state that it is INERT to the package** (`@upstash/context7-mcp` v4.4.4 hardcodes
     `CONTEXT7_BASE_URL` as a const and does not read it from the environment) and that its value is used
     **only as the C2 derivation input** — i.e. the base the daemon's DNS pre-check resolves when
     `CONTEXT7_API_URL` is unset.
   The two fields are **not** interchangeable: one is an effective lever, the other is a daemon-side mirror.
2. **Derived-base contract (single statement, consistent with G1).** The **effective base** the child uses is
   `CONTEXT7_API_URL` when set; otherwise the package falls back to its own hardcoded const. The daemon's
   **C2 derivation input** is `CONTEXT7_API_URL` when set, else the **`CONTEXT7_BASE_URL` mirror**. The
   pre-check resolves the **EFFECTIVE POST-HEAL** value of that input (§4.5 P5, §3-C3 clause 5) — never the
   bare definition default. The resolved address is **discarded**, never pinned.
3. **Ruling required on `mcp_set_env`.** The implementer must state explicitly whether
   `mcp_set_env` (`daemon/tools/infra.py:1527`) is (a) the **sanctioned escape hatch** for built-in env (in which
   case it must be documented and audited), or (b) a **hole to close** by adding the same `is_builtin` guard
   the HTTP API has (`daemon/routers/mcp_servers.py:760-768`). Leaving it undefined means the base URL is
   settable but **not via the protected template path**, which undercuts C3's intent. **Recommendation: (b)**,
   with the sanctioned path being `configure-builtin` — because (a) would make a tool the only way to configure a
   protected built-in, which is the inverse of the protection's purpose.
4. **Not the `.env`.** Per premise P2, the daemon's environment does **not** reach the MCP child. The template
   path is the only route that works; the RCA's "set it in `.env`" remediation premise is **false** and must not
   be reproduced in the design.
5. **ENV-PRESERVING MERGE IN THE SCHEMA-HEAL (adjudicated — a schema bump must not destroy operator env).**
   Bumping the context7 schema version triggers the built-in heal in
   `daemon/manager.py:2855-2867`: on `existing.config_schema_version != schema_version` it calls
   `update_mcp_server(existing.id, config=default_config, …)` with `default_config =
   definition.build_config({})` — a **WHOLE-CONFIG REPLACE**. The operator-set env (the pinned
   `CONTEXT7_API_URL`, KMS-marker keys written by `mcp_set_env` / `kms_attach`) is therefore **DROPPED on the
   first boot after the upgrade**, and the row silently reverts to template defaults — the exact hazard the
   warmup-pool env-precedence docstring warns about
   (`daemon/mcp/warmup_pool.py:860-878`: "A stale definition would silently drop those keys"; row `config.env`
   is the SOURCE OF TRUTH and wins over the definition defaults on every key). The heal must therefore be an
   **ENV-PRESERVING MERGE**: **operator-set env keys and KMS-marker keys are preserved**; only
   **template-derived defaults** (args, and env keys the operator never set) are rebuilt. Consequently
   **C2's DNS pre-check must read the EFFECTIVE post-heal env** (the merged result that the child will
   actually receive), not the pre-heal row and not the bare definition default — otherwise the pre-check
   validates a host the child will never use.

**Acceptance criterion.**
- A pin asserting the context7 schema is **non-empty** and contains **exactly the two** env-section entries
  `CONTEXT7_API_URL` and `CONTEXT7_BASE_URL` — and that **no** `CONTEXT7_API_BASE_URL` entry exists (the
  withdrawn name). `build_config({})` must produce `env` containing the expected variable names, with
  `CONTEXT7_API_URL` **absent by default** (no default) and `CONTEXT7_BASE_URL` defaulting to
  `https://context7.com`.
- A pin asserting the **G1 distinction**: the `CONTEXT7_BASE_URL` field's **description** states it is
  **INERT to the package** and is used as the **C2 derivation input**, while `CONTEXT7_API_URL`'s description
  states it is the **effective lever** with no default. A description regression is a FAIL.
- A pin asserting the **derivation**: with `CONTEXT7_API_URL` unset, the daemon's C2 pre-check target is the
  **`CONTEXT7_BASE_URL` mirror** value; with `CONTEXT7_API_URL` set, the pre-check target is that value and
  the mirror is **not** consulted. The pre-check target must equal the **effective post-heal** env value.
- A pin asserting the configured value reaches the **child process environment** through
  `build_pooled_stdio_config` → `StdioServerParameters(env=…)` (assert on the constructed parameters, not on
  the daemon's own env).
- A pin asserting the sanctioned write path works and (per ruling 3) that the un-ruled path is either audited
  or closed.
- A **regression pin** asserting that the built-in's `config` PUT still returns **403**.
- **Heal-survival pin (adjudicated):** `config.env` **survives the 2→3 schema heal with the operator value
  intact** — starting from a row whose `config.env` carries the operator's pinned `CONTEXT7_API_URL` and a
  KMS-marker key, run the heal path (`daemon/manager.py:2855-2867`) and assert both keys are **still present
  and unchanged** afterwards, while the template-derived defaults are (re)built. The C2 pre-check must then be
  asserted against the **effective post-heal env** (the merged value the child receives), not the default host.

**Safety property preserved.** *Protected-builtin immutability* — the 403 path stays; C3 adds a **schema**, not
an exemption.

**Anchor verification note.** The brief's "the URL is unsettable" is **confirmed**. The brief's implied
"set it via `.env`" is **false** (premise P2). The brief did not flag the `mcp_set_env` hole — added here as
ruling 3.

---

### C4 — DEGRADED MCP SERVERS MUST NOT TAKE DOWN THE DAEMON OR THE READINESS GATE

**Requirement (verbatim).** *"DEGRADED MCP SERVERS MUST NOT TAKE DOWN THE DAEMON OR THE READINESS GATE. A
degraded server must degrade alone, be reportable (which servers, why, since when), and be recoverable."*

**Current behaviour.**
- No per-server degraded registry exists. A failing server is visible only as a log line
  (`daemon/mcp/warmup_pool.py:186-188` "Failed to warm up pool for '<server>'") and a `logger.debug` in
  `_replenish` (`daemon/mcp/warmup_pool.py:582`).
- The readiness composite has no MCP reference and no degraded-server field
  (`daemon/services/readiness.py:295-350`, `:239-266`).
- **An observability defect exists**: `daemon/tools/upgrade_tools.py:2088-2092` reads
  `readyz.get('reasons', [])` at the **top level**, but the payload nests `reasons` under `detail`
  (premise P7) → the ops surface always renders `reasons=[]`.

**Intended behaviour.**
- A **per-server degraded registry** in the daemon, holding: server name, **why** (failure class: DNS,
  init-timeout, handshake, breaker-open, quarantine), **since when** (first-degraded timestamp), the failure
  count, and the current state (degraded / quarantined / recovered). It must survive the boot it was created in
  and be **cleared on recovery** (the breaker's half-open probe).
- **Reportable**: an existing ops surface must expose it (a tool or a readiness `detail` sub-object). The
  registry must be reachable **without** the daemon having to answer an MCP tool call.
- **Recoverable**: a degraded server must be able to return to service (breaker half-open after
  `recovery_timeout`, or the next boot for a boot-scoped quarantine) and the registry must record the recovery.
- **The daemon and the readiness gate must not be affected**: a degraded MCP server must not flip a readiness
  component, must not restart the daemon, and must not block the boot.
- **Sub-item (in scope, C4's reportability path):** fix the stale top-level `reasons` read at
  `daemon/tools/upgrade_tools.py:2088-2092` so the ops surface shows the real reasons. It is a one-line read-path
  correction and is the difference between "reportable" and "always reports nothing". **Decision required** at
  review: C4 owns it, or it is routed to a follow-up with the reason recorded.
- **Journal-write suppression while a promote txn is live (adjudicated — a daemon-side write must not
  release A3's deferred resumes).** The per-transition journal line is a **daemon-side write into the
  promote-owned** `releases/state.json` — the unlock-discipline hazard zone
  (`daemon/tools/upgrade_journal.py:331-371`, the temp-file/mv write at `:352-353`). A **stale-snapshot**
  write during a live promote txn **drops `in_flight`**, and because `in_flight` is A3's promote-txn predicate
  (§3-A3), that would **release A3's deferred resumes mid-promote** — reintroducing the very resume burst the
  hold exists to suppress. The rule: while `promote_txn_live()` is true, **daemon-side MCP state-transition
  journal writes are SUPPRESSED**. Suppressed writes are **buffered in-memory** — bounded, **one entry per
  (server, state)**, **newest wins** — and **flushed on txn close or on graceful shutdown**. They are **never
  dropped**. The daemon must therefore never write a stale whole-file snapshot over a live txn.

**Acceptance criterion.** A pin with one server permanently broken: (a) the daemon stays up, (b) `/readyz`
components are unaffected by the MCP failure, (c) the registry reports the server, the reason and the
timestamp, (d) after the breaker's recovery window the server can return, and the registry records it.
**Plus the txn-safety pin (adjudicated):** a daemon-side MCP state transition **during a live promote txn
must NOT drop `in_flight`** — the pin drives an MCP state transition while a synthetic promote txn is open,
then asserts (i) `in_flight` is **still present and unchanged** in the scratch `state.json`, (ii) no
stale-snapshot write occurred, (iii) the transition was **buffered**, not lost, and (iv) after the txn closes
(or on graceful shutdown) the buffered entry is **flushed exactly once**, newest-wins, and `in_flight` is then
released normally.

**Safety property preserved.** *Readiness must never be gated on MCP* (B2's pin) and *the daemon must never
restart on a degraded MCP server* (ADR-005's "degrade, never restart").

**Anchor verification note.** N/A — new work plus the one-line defect at `:2088-2092`.

---

## 4. Normative predicate register (the single normative home)

**§4.1 … §4.7 below are the NORMATIVE predicate definitions for this commission.** Every cross-cutting
predicate — the never-green trigger, grace eligibility and aggregation, the counter observation kinds, the
grace ceiling and its clock, the leak signature and its streak-arm qualification, the DNS-refusal thresholds,
the acceptance-criterion thresholds, and the A3 release rule — is defined **here and only here**. Other files
(`decisions.md`, `plan-overview.md`, `architecture-recommendation.md`, the phase plans, the risk register)
**reference these §4 anchors** and must not restate them; if a restatement appears to disagree, **§4 wins**.

**§4.0 is the failure-CLASS table** the register consumes: it is the classification surface for the §5
acceptance criteria and for the §3 requirement rows.

### 4.0 — Failure-CLASS table (pinned classification the requirements lean on)

This table is **normative**. A1's grace and A2's counter both consume it, and every A5 pin is written against
it. It is the contract for what "grace-eligible" means, and it is deliberately **narrower** than the
"anything transient" reading of the current probe classifier
(`scripts/upgrade/lib.sh:2986-3001` classifies curl-7/28 and HTTP 503 as `transient`).

| Class | Definition (the observable) | A1 grace | A2 counter | Named examples from the incident | Anchor |
|---|---|---|---|---|---|
| **GRACE-ELIGIBLE (busy / recovering)** | The probe ran and the system was simply too busy to answer **within its budget**. The budget was the constraint, not the component. | May be **grace-held** | **HOLDS** the counter (does not advance, does not reset) | `checkpoint_saver: sentinel read (aget_tuple) timed out after 1.0s` (20:43:33); `database: SELECT 1 probe failed or timed out` (20:43:17); `queue_freshness: queue probe timed out`; `pool.check()` could not obtain an idle connection; queue-freshness stale under load | `readiness.py:45/:54/:59` (budgets), `:505-521` (timeout branch), `:565-575` (SELECT 1), `:793-836` (queue probe) |
| **NEVER grace-eligible — hard failure** | The component answered with an **actual failure**, or never came up at all. Grace may never suppress it. | **Fails fast**, grace or not | **RESETS** the counter and fails fast (≤ one cadence) | boot never completed; version/identity mismatch; a component that is genuinely red (not a timeout); an **EXCEPTION** rather than a timeout; the daemon is not serving at all (curl exit not in {7,28}) | `lib.sh:2986-3001` (the fail-closed `*` arm), `:5242` (cap-reach), `promote.sh:509` (version gate) |
| **GRACE-INELIGIBLE — pool-leak signature** (`pool_leak`; NEW, from `incident-20261026-db-pool-idle-in-transaction.md`) | The failure is **self-sustaining**, measured from **pool objects** (never `pg_stat_activity`): the **ORACLE** pool **P2** (psycopg checkpoint-saver) arms on `requests_waiting > 0` sustained, or `pool_available` strictly decreasing across 3 consecutive ticks, or the pool size exhausted; the **ADVISORY** pool **P1** (SQLAlchemy manager engine) publishes its signals in `detail.pool_state` but **MUST NOT** arm. Plus: the same-component-failing-on-every-tick fallback arms the leak class **ONLY when the pool objects are UNAVAILABLE** (not pool-backed / probe seam unavailable); when pool objects ARE available the streak arm MUST NOT fire — a sustained busy-but-healthy burst must not be classified as a leak | **Refuses to engage, and disengages** on the next tick | **RESETS** the counter and fails fast | the 22:10–22:50Z incident: idle-in-txn 0 → 6, open backends 8–9 → 18, `/api/instances` 0.079 s → 0.89 s, readiness flapping; the probe-leak → pool-pressure → timeout → retry loop | **§4.4 (P4)** (normative), A4 clause 5 (pool-health capture), §5 (iv)(b) (the leak-refusal pin) |
| **GRACE-INELIGIBLE — identity / integrity** | The running binary's version or manifest does not match the ceremony's expectation | Never engages | Fails fast (this is `gate_version`, pre-soak, and the mid-soak drift check) | version drifted mid-soak | `lib.sh:5204-5208` (mid-soak version check), `promote.sh:509` |
| **GRACE-INELIGIBLE — boot never completed** | The daemon never bound the port / never served a first composite | Never engages (there is nothing to hold) | Cap-reach with 0 greens → rollback | the "boot never completed" hard case | `daemon/api.py:4809-4870` (fail-closed when no composite exists) |

**Why the classes matter (the dependency map):**
- **A1** *is* the operationalisation of row 1. Its entire design (dual-signal + leak-refusal + "never renders
  healthy") is a set of guards that keep rows 2–4 outside the grace. If a row-2/3/4 signal were ever
  grace-held, A1 would be a masking mechanism and must be rejected.
- **A2** *consumes* the table through the counter: row 1 **holds**, rows 2–4 **reset** (and rows 2–4 fail fast).
  The never-green protection (safety property) is exactly the statement "a release that only ever produces
  rows 2–4 ends at cap-reach with `consecutive_green < 3` → auto-rollback".
- **A5(ii)** (genuinely broken IS rolled back) is a **row-2 pin**: it must show that a row-2 observation is
  never grace-held and never counter-held.
- **A5(iv)** (grace observable + cannot mask indefinitely) is a **row-3 + row-1 pin**: it must show the
  grace-held state reports its reasons, and that a row-3 signature disengages the grace.

**NORMATIVE multi-component aggregation rule (adjudicated W-2).** The grace is evaluated **per readiness
tick**, over the composite's degraded components:

```
grace.active = ( EVERY degraded component is classified GRACE-ELIGIBLE (row 1) )
           AND ( NO leak signal present )
```

**ANY grace-ineligible class on ANY component, OR any leak signal ⇒ `grace.active = false` THIS TICK.** The
rule is a conjunction, not a vote: one row-2 component (a genuine exception) among three timeouts **kills the
grace for that tick** and the counter semantics of §3-A2 apply with `grace.active=false`. There is no
majority rule, no per-component grace. The rule is pinned in `tests/unit/test_readiness_failure_class.py`
(added to the §5 AC list): the pin drives a tick-by-tick matrix — all-row-1 (grace active), row-1 + one
row-2 (grace OFF that tick), row-1 + leak (grace OFF), all-green (grace inactive and irrelevant) — and
asserts `detail.grace.grace_active` matches the conjunction on every tick.

**Streak-fallback scope (adjudicated S9).** The "same component failing on every tick with no recovery trend"
streak input of A1's leak-refusal (§3-A1 input 3) is pinned to **pool-objects-UNAVAILABLE only** — i.e. the
streak arm fires only when the pool object itself cannot be reached/enumerated (`get_stats` /
connection-count plumbing unavailable), never as a general "N consecutive degradations" heuristic. An
unqualified consecutive-failure fallback would classify a **sustained healthy burst** — the incident's own
shape, where row-1 timeouts recur on every tick for minutes while the system is genuinely recovering — as a
leak, gut the grace exactly when it is needed, and reintroduce the v0.27.8 rollback. Sustained-burst
detection is the **A2 window extension** (§3-A2 clause 3), not a grace-refusal input.

> **Pinning note.** The classification boundary between rows 1 and 2 is the **probe's own observable**: a
> *timeout* (the budget was hit) is row 1; an *exception with a non-timeout cause* or a *non-2xx, non-503 HTTP
> status* is row 2. The current classifier folds curl-7 (refused) into row 1; the RCA's evidence shows the
> 20:43:33 signatures were timeouts (row 1), while the 20:42:31→20:43:33 transition produced real 503s. The
> implementer must state, per probe, which class each observable maps to, and pin it.

---

---

### §4.1–§4.7 — Normative predicate register (CANONICAL TEXT)

**§4.1 P1 — NEVER-GREEN TRIGGER (normative).**
- At cap-reach in `gate_soak` (`scripts/upgrade/lib.sh:5237-5244`), `consecutive_green < 3` ⇒ **return 1 ⇒ ADR-005 auto-rollback (+ quarantine)**. **UNCONDITIONAL.** There is NO fairness / observation-window condition on the rollback path and NO cap-reach outcome that returns 0.
- A cap-reach has exactly TWO outcomes: `consecutive_green >= 3` ⇒ return 0; `consecutive_green < 3` ⇒ return 1.
- The ceremony-budget truncation case is the **PRE-SOAK deferral** at `lib.sh:5088-5093` (`soak_deferred`, returns 0 BEFORE the soak loop) — it is NOT a cap-reach outcome and is NOT a fairness condition.
- Any fairness diagnostic (e.g. "the observed window was shorter than X") is **DIAGNOSTIC ONLY**: surfaced in the journal line, pinned as NOT a success path, and MUST NOT influence the return value.
- Required §5 leg: a **grace-eligible daemon that NEVER greens** still rolls back — the grace arms, the counter still reaches cap-reach below 3, return 1.

**§4.2 P2 — GRACE ELIGIBILITY CLASSES + AGGREGATION + COUNTER OBSERVATION KINDS (normative).**
- **Grace-eligible classes:** `probe_timeout`, `pool_busy`, `queue_stale`, `read_timeout`.
- **Never grace-eligible (fail-closed):** `boot_incomplete`, `identity_mismatch`, `hard_component_red`, `not_serving`, `pool_leak`, `unknown`.
- **Aggregation:** `grace.active = (every degraded component classified grace-eligible) AND (no leak signal)`. ANY never-eligible class OR any leak signal ⇒ `grace.active = false` THIS TICK.
- **Eligibility is CLASS-DRIVEN, never conjunctive on boot-age/burst.** Boot age and burst state are published as observable CONTEXT in `detail.grace` and MUST NOT be arming preconditions. The 2026-10-09 v0.18.5 environmental DB-churn class is covered by the same class-driven rule — it is NOT a post-boot-only special case.
- **Counter observation kinds:** GREEN / HELD / RESET.
  - **GREEN** — livez **HARD-green** (rc=0 AND per-probe status literally `"green"`, NOT busy-amnesty) AND readyz hard-green (rc=0 AND status `"green"`): `consecutive_green += 1` AND **`consecutive_hold = 0`**.
  - **HELD** — livez **HARD-green** (as above) AND readyz is a grace-eligible transient AND the grace is active AND no leak signal: `consecutive_green` unchanged, `consecutive_hold += 1`.
  - **RESET** — everything else, INCLUDING **any tick where livez is NOT a hard green (a livez transient is a RESET, never a HOLD)**, any never-eligible class, any unknown class, any hard red, and the boot-grace-suppressed class: `consecutive_green = 0` AND `consecutive_hold = 0`.
- A green counts only behind at most **`MAX_HOLDS_PER_GREEN = 2`** consecutive holds; a never-green daemon still ends at cap-reach with `consecutive_green = 0`.
- The counter evaluates **per-probe status locals** (the `_PROBE_STATUS` global aliasing defect is fixed AT THE COUNTER SITE only; `_PROBE_STATUS` remains the probe-level global elsewhere by design).

**§4.3 P3 — GRACE CEILING + ITS CLOCK (normative).**
- **ONE clock: OBSERVATION-RELATIVE** — `grace_expires_at = first_grace_eligible_observation + GRACE_HORIZON_S`.
- **ONE number: `GRACE_HORIZON_S = 120.0` seconds.** The grace is NEVER extended; re-arming requires the grace to have fully lapsed.
- There is **NO boot-relative grace ceiling** (the valueless `grace_abs_ceiling_s` is deleted).
- **Relationship to the soak bound:** the soak's absolute bound is `soak_entry + ADAPTIVE_SOAK_CAP_S (120) + GRACE_EXTENSION_CAP_S (180) = soak_entry + 300s`. The grace-eligible observation window can NEVER extend past that absolute bound.

**§4.4 P4 — LEAK SIGNATURE + STREAK-ARM QUALIFICATION (normative).**
- The grace-INELIGIBLE class is `pool_leak`.
- **ORACLE pools (their signals ARM the leak class): P2 — the psycopg checkpoint-saver pool.** Arms on `requests_waiting > 0` sustained, or `pool_available` strictly decreasing across 3 consecutive ticks, or the pool size exhausted (all connections checked out).
- **ADVISORY pools (signals are VISIBLE in `detail.pool_state` but MUST NOT arm the leak class): P1 — the SQLAlchemy manager engine.** Rationale: `pool_size=5, max_overflow=10` ⇒ `overflow() > 0` is non-zero on a busy-but-healthy pool BY DESIGN, so a P1-armed leak class would refuse the grace on the incident's own busy shape — the exact failure this commission exists to fix.
- **STREAK ARM:** the same-component-failing-on-every-tick fallback arms the leak class **ONLY when the pool objects are UNAVAILABLE** (not pool-backed / probe seam unavailable). When pool objects ARE available the streak arm MUST NOT fire — a sustained busy-but-healthy burst must not be classified as a leak.
- Measured from pool objects, **never** from `pg_stat_activity` (an extra query on a poisoned pool amplifies the disease).

**§4.5 P5 — DNS-REFUSAL THRESHOLDS + CLASSIFICATION (normative).**
- The pre-resolve is bounded at **`DNS_PRECHECK_TIMEOUT_S = 2.0` seconds** and runs **OFF** the event loop.
- **AC bound:** the init MUST refuse (no child spawned) within **2.0 s + spawn overhead**, and in all cases far below the 30 s package-side hang budget.
- Classification: **resolution failure** (NXDOMAIN / SERVFAIL / no A/AAAA) ⇒ quarantine-eligible; **resolver TIMEOUT** ⇒ distinct class `resolver_timeout`, **NOT** quarantine-eligible on first occurrence (a resolver hiccup must never permanently disable a server); **success** ⇒ proceed + `record_success`.
- The pre-check resolves the **EFFECTIVE post-heal base** the child would actually use (§3-C3) and the resolved address is **DISCARDED** — never pinned.

**§4.6 P6 — ACCEPTANCE-CRITERION THRESHOLDS (normative).**
- **Event-loop lag:** p95 ≤ 250 ms AND max ≤ 500 ms (periodic heartbeat task during the R-1 concurrent burst).
- **MCP handshake bound:** `HANDSHAKE_WAIT_TIMEOUT_S = 30.0` s (**NOT 55 s** — 55+2+30+6 = 93 s exceeds the 60 s outer budget; 30+2+30+6 = 68 s fits).
- **DNS:** §4.5 (2.0 s).
- **Grace horizon:** 120 s · **soak extension cap:** 180 s · **soak absolute bound:** soak entry + 300 s.
- **Boot-liveness:** `BOOT_LIVENESS_S = 120.0` s (§4.7).
- **Test timeouts:** the repo default is 30 s (`pyproject.toml:97-98`; kill ⇒ `os._exit(1)`, no traceback) — every new long pin carries an explicit **`@pytest.mark.timeout(N)`** with N > its worst case (house convention, 115 usages).
- **Integration gates** run in the house form **`--override-ini="addopts=" -m integration`** (`pyproject.toml:95` deselects `integration` by default; 56 house usages; precedent `tests/test_settings_api.py:19`) and MUST assert a **non-zero EXPECTED COLLECTED COUNT**, not merely "0 failed".

**§4.7 P7 — A3 RELEASE RULE (normative).**
- **Promote-txn LIVE predicate:** `in_flight.kind == "promote"` AND ( (**POST-BOOT**) `journal_owner_heartbeat_fresh` is True OR (**BOOT WINDOW**) the daemon's boot age < `BOOT_LIVENESS_S = 120.0` AND `in_flight.started_at` is present AND `now - in_flight.started_at < PROMOTE_TOTAL_DEADLINE_S (900 s)` ) ).
- **Why the boot window exists:** the shell writes `in_flight` **BEFORE** restarting the daemon (`promote.sh:327` open → `:413` stamp → `:415` restart); the next heartbeat refresh happens only inside `gate_livez`'s retry loop (`lib.sh:3074`, `_probe` `:3230`/`:3286`), which runs only AFTER the daemon can serve `/livez`. At seam 1 (`daemon/api.py:2703`, during daemon boot) the heartbeat may legitimately be stale (60 s window, `upgrade_journal.py:307-313`) even though the promote IS live. The observed daemon was ~75 s old at its first green readyz, so a 60 s heartbeat window can already be expired at seam 1 — the hold would be SILENTLY INERT at the seam with the largest RCA contribution.
- **`BOOT_LIVENESS_S = 120.0` s** is justified: it exceeds the observed ~75 s boot-to-first-green and the 60 s heartbeat window, and stays far inside the ceremony envelope (900 s).
- **During the BOOT WINDOW the hold is HOLD-only:** release requires `in_flight` to be **ABSENT** (not merely stale).
- **RELEASE HYSTERESIS:** the release sweep fires only after **TWO CONSECUTIVE FALSE** predicate reads ≥ 1 driver tick (5 s) apart. A single torn/unreadable `state.json` read (the accessor returns False on `JournalTorn`/`OSError`, `upgrade_journal.py:300-303`) MUST NOT release the deferred resumes mid-promote.
- **Driver:** a lazily-created, module-held asyncio task in `daemon/services/promote_txn_hold.py`, created on first deferral, ticking every 5 s while any deferral is outstanding; cancelled when the deferred lists empty after a release sweep; registered on the daemon's ordered-shutdown path; the bounded release timeout is the crash backstop.
- The hold **DEFERS, never drops**; human-initiated resumes are not gated.

---

**Audit-trail markers (superseded forms — none is a live requirement).**
- `grace_abs_ceiling_s` — the **valueless boot-relative ceiling**. **SUPERSEDED**: §4.3 (P3) states the ONE
  clock (observation-relative) and the ONE number (`GRACE_HORIZON_S = 120.0`); §3-A1 records the deletion.
  The two occurrences are (a) §4.3's own deletion clause and (b) §3-A1's deletion note.
- `CONTEXT7_API_BASE_URL` — the **withdrawn schema-entry name**. **SUPERSEDED**: §3-C3 clause 1 states the
  unambiguous two-field G1 contract (`CONTEXT7_API_URL` = effective lever; `CONTEXT7_BASE_URL` = daemon-side
  mirror, inert to the package). The three occurrences are (a) the package's own internal composed URL
  (§3-C2 current behaviour), (b) the explicit WITHDRAWN marker in §3-C3 clause 1, and (c) the AC's
  "**no** `CONTEXT7_API_BASE_URL` entry exists" assertion.

## 5. A5 acceptance-criterion detail (four sub-cases)

**Harness rules (house rules, binding on every pin below).**
- **Explicit interpreter on every gate command.** `python` **does not exist on this host** (only
  `/usr/bin/python3`); the worktree has **no `.venv`**; and `uv` is **off-PATH** at
  `/home/nea/.local/bin/uv` (the same gotcha that already broke `bump_version.py`). Every gate command must
  therefore name an interpreter explicitly: **`uv run python -m pytest …`** (with
  `/home/nea/.local/bin/uv` on PATH) **or** `/usr/bin/python3 -m pytest …` against the repo's venv path. A bare
  `python` in a command line is a **defect**, not a style nit.
*(HOUSE CONVENTION — not a §4 predicate: the interpreter rule and the integration runner form are operating
conventions, restated per phase by requirement; they have no normative §4 anchor of their own, and the
canonical threshold statement for these gates is **§4.6 (P6)**.)*

- **Integration gates run in the house form:** `--override-ini="addopts=" -m integration`
  (`pyproject.toml:95` deselects `integration` by default; 56 house usages; precedent
  `tests/test_settings_api.py:19`). A bare `python -m pytest tests/integration/…` reports **"0 failed" with
  nothing executed**. Every integration gate MUST assert a **non-zero EXPECTED COLLECTED COUNT**, not merely
  "0 failed". (Thresholds for these gates: **§4.6 (P6)**.)
- **Real PostgreSQL, NOT SQLite.** The fresh-SQLite full-ledger boot is **blocked** by the pre-existing
  migration `daemon/migrations/versions/20260714_000001_widen_job_queue_type_constraint.sql` (the tree carries
  115 migration files; the runner is SQLite-only and this one issues an `ALTER TABLE … DROP CONSTRAINT` that is
  invalid on SQLite). Any DB-touching pin must run against a **scratch PostgreSQL** database.
- **Per-variable env scrub.** `env -u POSTGRES_*` is a **silent NO-OP** (bash does not glob env names). Scrub
  **per variable**: `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT
  -u DAEMON_PORT <cmd>`, or set them explicitly.
- **Fence the install-dir shape.** Test scripts must **never** resolve live-shaped (HOME-derived) install paths.
  Use `mktemp -d` as a **FAKE_HOME** plus an escape guard that refuses to run when the resolved path is under a
  real `$HOME`.
- **Env-poison fence.** Ambient-env boots have hit LIVE `ensemble_prod` **6 times**. Every boot a test triggers
  must be fenced (scratch DB + explicit `POSTGRES_*` + explicit `PORT`).
- **Staged-release boot smokes are NOT isolated** (a staged binary's path derivation resolves the LIVE install
  root and writes into the live promote journal). Any boot-smoke must be explicitly isolated — own
  install-root override honoured **ahead of** path derivation, or a scratch copy of the binary + explicit `PORT`
  + `ENSEMBLE_CONFIG`.
- **FastAPI route/decorator binding trap.** If routers are touched (C3 touches `daemon/routers/mcp_servers.py`),
  include a **route-table readback pin** (a helper inserted between a decorator and its handler silently
  re-binds the route).
- **Every new SQLModel table requires its own `*.sql` file** in `daemon/migrations/versions/`. (This commission
  should need **no** new table: the C4 registry is in-memory; the A1 grace state is in the readiness composite.)
- **No ruff config** in the repo — do not introduce one.

### (i) A HEALTHY release under a simulated burst is NOT rolled back

- **Proposed pins.**
  - `tests/unit/upgrade/test_soak_grace_counter_semantics.sh` (or the house pack form) — a **fake-probe**
    sequence harness driving `gate_soak` with a scripted classification stream.
  - `tests/integration/upgrade/test_promote_soak_burst.py` — a **real** soak against a real daemon under a
    synthetic burst. **The burst harness must be CONCURRENT** (adjudicated R-1): it runs the **MCP warmup**
    (including a deliberately slow/failing server, per C1/C2), the **job-recovery reconcile**, and the
    **auto-continue resume burst** *together*, not in sequence — a pin that passes while the incident recurs
    in a diluted, serial form is not a regression pin. The incident's shape was all three streams overlapping
    on one loop with the checkpoint pool saturating; the harness must reproduce that overlap.
- **Healthy-leg construction vs the leak predicate (adjudicated R-1).** The burst harness must be able to
  produce **both** pool-health shapes, and the grace must distinguish them:
  - **healthy-busy** = checked-out count **RISES AND FALLS** — an **oscillation** with **recovery ticks
    present** (the pool drains under load and refills between turns). This is row 1 of the §4 table: the
    grace engages, the soak extends, the release must NOT roll back.
  - **leak** = a **MONOTONIC rise with NO recovery tick** — checked-out (or idle-in-transaction) climbing
    every tick and never returning. This is the §4 row-3 GRACE-INELIGIBLE signature: the grace refuses, the
    component reasons surface, and the release path stays live.
  The distinction is stated here because it is the difference between A5(i) passing for the right reason and
  passing because the harness never exercised the leak-refusal leg. The leak-refusal leg is pinned
  separately in §5(iv)(b); this sub-case's pass depends on the harness actually building the oscillating
  shape.
- **Command (illustrative, house-consistent).**
  `cd /home/nea/ensemble-src-wt-soak-gate && env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT HOME="$(mktemp -d)" bash
  tests/packs/promote_soak_grace_burst_test.sh`
  (the exact path/name must follow the house pack conventions; the *shape* — per-variable scrub, FAKE_HOME,
  scratch PG — is binding).
- **Observable pass/fail.**
  - Fake-probe leg: the classification stream `transient(grace-eligible), transient(grace-eligible), green,
  green, green` (with an active grace) **reaches** `consecutive_green >= 3` and `gate_soak` returns **0**;
    `green, green, hard-red` returns **1**; `green, grace-held, grace-held, grace-held` returns **1** at
    cap-reach (never-green preserved).
  - Real leg: a daemon that is healthy but under a synthetic burst (N resumed turns + a saturated pool) is
    observed by the soak across a window that **extends past the burst**; the soak **exits 0**; **no**
    `journal_history_append rollback` / `quarantine` event is written to the scratch journal; `current` still
    points at the staged version.
- **Falsification.** If the soak still returns 1 under the simulated burst, the requirement is **not met** —
  a soft-green, a grace-held observation, or an aliasing bug must not be able to keep the counter at 0.

### (ii) A GENUINELY BROKEN release IS still rolled back (never-green protection intact)

- **Proposed pin.** `tests/packs/promote_soak_never_green_test.sh` — drive `gate_soak` against a daemon whose
  `/readyz` returns a **hard red** (a component that is genuinely red, not a timeout) and, separately, one that
  never answers (a boot that never completes).
- **Command.** Same fence shape as (i).
- **Observable pass/fail.** `gate_soak` returns **1**; the auto-rollback route fires; a
  `rollback` **and** a `quarantine` journal event are written to the scratch journal; `current` reverts to
  `previous`; `previous` is **not** quarantined. Under the grace window, a hard red must **not** be
  grace-held — the pin must show the failure is immediate, not deferred to the grace expiry.
- **Falsification.** If any of these is missing, the never-green protection has been weakened and the change is
  **rejected** — this sub-case outranks every other AC.
- **Required leg (per §4.1 P1).** A **grace-eligible daemon that NEVER greens** still rolls back: drive a
  daemon whose every readiness observation is a grace-eligible transient (so the grace arms and holds), with
  no green anywhere in the window. The soak must reach cap-reach with `consecutive_green < 3` and **return 1**
  ⇒ auto-rollback + quarantine. The grace arming must **not** change the return value, and the fairness
  diagnostic must remain diagnostic-only. **This leg is what makes §4.1 testable rather than assumed.**

### (iii) The soak still fails fast on a real regression

- **Proposed pins.** (a) A unit pin on the classification table: a **hard red** during a grace window returns
  `1` on the **first** observation (no retry, no hold). (b) An integration pin: a daemon that transitions from
  green to a hard red mid-soak causes the soak to fail **within one cadence interval** (≤ 15 s + probe time),
  not at cap-reach.
- **Command.** Same fence shape.
- **Observable pass/fail.** The soak's elapsed time at failure is `<= adaptive_cadence + one probe duration`
  (i.e. **≤ ~20 s**), and the journal shows the failure reason. A regression that is only caught at the 120 s
  cap is a **FAIL** of this sub-case (it is the "fails slow" bug).
- **Falsification.** If the soak rides out a hard red to the cap, the fail-fast path is broken.

### (iv) The grace window is observable and cannot mask a real fault indefinitely

- **Proposed pins.** (a) A **detail-payload** pin: with the grace active and a component degraded, the
  `/readyz` body has `status == "degraded"`, carries the **component reasons still present** (non-empty
  `detail.reasons`), and carries a `detail.grace` block with `grace_active` / `grace_reason` /
  `grace_expires_at`. A **suppressed reason string is a FAIL**. (b) A **leak-refusal** pin: with the
  pool-leak signature present, as classified by the register —
  > Normative source: requirements.md §4.4 (P4 — leak signature, ORACLE/ADVISORY pool split, streak-arm
  > qualification).

  — the grace **does not
  engage** (or disengages on the next tick) and the component reasons are reported normally. (c) A **bound**
  pin: the grace expires at the horizon per **§4.3 (P3)** (`GRACE_HORIZON_S = 120.0` s, observation-relative,
  never extended) regardless of continued transient failures, and the grace is **not re-armable** — re-arming
  requires the grace to have fully lapsed.
- **Command.** Same fence shape, plus a direct probe:
  `curl -s http://localhost:$PORT/readyz | jq '.status, .detail.reasons, .detail.grace'`
- **Observable pass/fail.**
  - (a) `status == "degraded"`; `detail.reasons` **non-empty and containing the real component reason**;
    `detail.grace.grace_active == true`; `grace_expires_at` present and in the future.
  - (b) with the leak signature injected, `detail.grace.grace_active == false` (or the grace is gone) **and**
    the component reasons are reported.
  - (c) after the absolute ceiling elapses, `detail.grace.grace_active == false` and the composite is a plain
    degraded.
- **Falsification.** A readyz body that reports `status == "ready"` while a component is degraded, or one whose
  `detail.reasons` is empty during a grace-held degradation, **fails** this sub-case outright.
- **Aggregation pin (W-2).** `tests/unit/test_readiness_failure_class.py` pins the §4 aggregation rule per
  tick: `grace.active = (every degraded component grace-eligible) AND (no leak signal)`; any grace-ineligible
  class on any component, or any leak, forces `grace.active=false` **that tick** (matrix legs: all-row-1 /
  row-1+row-2 / row-1+leak / all-green).

---

## 6. Narrowed requirements (where a safety property wins)

> Rule applied throughout: **the never-green rollback protection is a SAFETY PROPERTY and MUST survive.** Where a
> requirement could only be satisfied by breaking it, the safety property is preserved, the feature is
> **narrowed**, and the narrowing is stated here.
>
> **This section contains exactly NINE narrowings: N-1 … N-9** (N-1 B2, N-2 B3, N-3 A4, N-4 A1, N-5 A2, N-6 C3,
> N-7 A3, N-8 C1, N-9 the interim-efficacy disclosure). The numbering is continuous and every N-reference
> elsewhere in this file points at an entry in this list. N-9 is a **disclosure**, not a requirement narrowing —
> it is cross-referenced from §1.2 and from the §7 non-goal table, and it is deliberately **not** attached to a
> requirement row in the §8 coverage table.

### N-1 — B2 is NARROWED to a pin + the B1 remedy

**Why.** The requirement's premise — "readiness is gated on MCP warmup" — **does not hold in code**. The
readiness composite's four components (`database` / `queue_freshness` / `services` / `checkpoint_saver`,
`daemon/services/readiness.py:295-350`) contain **zero** MCP references, and `/api/prompts/pending-count`
(`daemon/routers/prompts.py:1078-1102`) is a plain DB count. The only coupling is **event-loop starvation**,
which B1 addresses. Specifying a "decoupling mechanism" for a coupling that does not exist logically would
create a false abstraction and a maintenance trap.

**Narrowed B2.** (a) A regression **pin** proving MCP-independence of the readiness composite and of
`pending-count`; (b) the B1 loop-starvation fix as the real remedy; (c) the RCA's 20:42:39 `pending-count` 503
recorded as **UNPROVEN** as degradation evidence (it is equally consistent with the 503
`AUTH_NOT_CONFIGURED` / `PROMPTS_UNAVAILABLE` paths that `prompts.py` actually implements). **No decoupling
mechanism is specified.**

### N-2 — B3's aiohttp clause is NARROWED to "the leaks we can prove"

**Why.** `grep -rn aiohttp daemon/mcp/` returns **zero** hits; aiohttp appears in this repo only in the
Slack/Discord/Telegram source adapters. The RCA's "leaks an unclosed aiohttp session" is **RCA-asserted but
not code-grounded in the MCP path**.

**Narrowed B3.** The commission must guarantee: (a) **no orphaned children** after a failed init (proven by
pid-liveness), and (b) **no unclosed MCP session/streams**. The aiohttp clause is **routed to a follow-up with
the mechanism explicitly unverified** — this commission must not claim to have fixed an unverified leak.

### N-3 — A4's "sizing" is NARROWED: sizing is necessary, not sufficient

**Why.** The dominant blocker is the saver's **single `asyncio.Lock`** (`langgraph .../postgres/aio.py:59`),
not the pool size; and the pool is being **poisoned underneath it** by the `mission_records` mint
`UniqueViolation` (4× per boot, no rollback) and by the readiness probes' own transaction leak.

**Narrowed A4.** A4 delivers: the sizing knob (for **7 writers + 1 probe**, not `llm_concurrency=10`), the
contention fix (a dedicated probe path **or** grace coverage — the implementer states which), the ghost-waiter
fix, and pool-health surfacing. It **explicitly does not claim to cure** the leak, and the §7 non-goals 3 and 4
own it. A review that sees "we sized the pool" as the whole of A4 must **reject** the change.

### N-4 — A1's grace is NARROWED: it must not engage on a pool-leak signature

**Why.** The 22:10–22:50Z incident is a **self-sustaining** failure class (pool pressure → probe timeout →
more pressure). A grace that armed on time alone would engage on exactly that signature and **mask** it —
turning a hard defect into a silently-held "grace" state. (The **confirmed** retention mechanism is the
timed-out probe's connection retention; see §1.2 — the stronger "leaks a transaction per tick" claim is not
asserted here.)

**Narrowed A1.** The grace is **class-driven** (§4.2 P2) and **leak-refusing** (§4.4 P4) — **not**
dual-signal on boot-age **and** burst. Boot age and burst state are **published context** in `detail.grace`
and are **not** arming preconditions; the **2026-10-09 / v0.18.5** class is covered by the same class-driven
rule (§1.3). The grace renders *"degraded, grace-held, reasons still present"*, **never** "healthy". A
suppressed reason string is a defect and is pinned as one.

### N-5 — A2's counter semantics are NARROWED to HOLD (not count)

**Why.** Counting grace-held observations as greens would let a **never-green** release accumulate a counter and
survive — a direct violation of the safety property. But *resetting* on them (today's behaviour) makes the
"3 consecutive greens" unreachable, which is the requirement's complaint.

**Narrowed A2.** A grace-held observation **holds** the counter (does not advance it, does not reset it). It
never *counts* toward the 3. Therefore a release that never greens still ends at cap-reach with
`consecutive_green < 3` → auto-rollback. **Safety property preserved.**

### N-6 — C3's `mcp_set_env` ruling is NARROWED to an explicit decision

**Why.** C3's goal is "the base URL is settable **via the protected template path**". Leaving
`mcp_set_env` (`daemon/tools/infra.py:1527`) un-ruled means the only working path is the **unguarded tool** —
the inverse of the protection's purpose.

**Narrowed C3.** The implementer must record a ruling: **sanctioned escape hatch** (documented + audited) **or
hole to close** (add the same `is_builtin` guard the HTTP API has at
`daemon/routers/mcp_servers.py:760-768`). Recommendation: **close it**; the sanctioned path is
`configure-builtin`.

> **SUPERSESSION (Worker A N4 — adjudicated ruling 1).** `mcp_set_env` is **NOT closed in this lane**. The
> earlier "close the hole" recommendation above is **WITHDRAWN as a scope decision**: the `mcp_set_env`
> built-in-env path is a **routed defect** with a **named owner** (the MCP tool-lane owner, alongside the
> `daemon/tools/infra.py:1527` tool itself), and the `is_builtin` guard lands **there**, not in this
> commission. What this lane therefore ships is the **template-side** half: the two-field schema (§3-C3
> clause 1) plus the env-preserving heal merge (§3-C3 clause 5), so the base URL is settable **through the
> protected template path** — which is C3's intent — while the unguarded tool path remains a separate,
> **owned** follow-up. The unguarded path is therefore **not** relied on by this commission's ACs.

### N-7 — A3's hold is NARROWED to a deferral, and to four named seams

**Why.** A "hold" that **drops** a resume loses work; a hold that covers *every* dispatcher without a bound
would stall legitimate work indefinitely.

**Narrowed A3.** The hold covers **exactly four** seams — the boot auto-continue pass
(`daemon/api.py:2703`) **with the release trigger**, the runtime resume dispatcher
(`daemon/manager.py:13671-13684`), the eligible-pending sweep
(`daemon/services/eligible_pending_sweep.py`), and the task-less orphan sweep `sweep_orphaned_live_turns`
(`daemon/services/auto_continue_boot_pass.py:726`, `daemon/api.py:2708`) — and **defers**, never drops. The
release trigger is the hold module's **per-seam deferred-candidates list** re-consulting the promote-txn
predicate on a bounded cadence (read-only `state.json` read, default 5 s, only while deferrals are
outstanding), releasing through the **original seams** (checkpoint-resume path, never a cold dispatch), and
composing with **fail-open-on-stale**. **Why the release trigger is normative:** the boot pass runs exactly
once per boot (`daemon/api.py:2703`), and during a promote it always runs with the txn live — deferral alone
would drop the resume to StaleTaskRecovery at ~`boot+10 min`
(`daemon/services/auto_continue_boot_pass.py:69`), losing the checkpoint continuation. The boot kill-switch
keeps its boot-only meaning.

### N-8 — C1's per-boot quarantine reuses `is_available()`, but `is_available()` is a classmethod

**Why.** `daemon/mcp/builtin_servers/base.py:125-152` `is_available()` is a **classmethod** — it cannot carry
per-instance boot state by itself, and `required_package is None ⇒ True` for context7.

**Narrowed C1.** The quarantine state must live in a **per-process** registry that `is_available()` consults
(a module-level per-process set, cleared at boot), consulted at **all FOUR** existing call sites
(`daemon/manager.py:2813`, `daemon/manager.py:3057`, `daemon/services/mcp_service.py:579`,
`daemon/services/mcp_service.py:1130`) so a quarantined server is skipped everywhere — not only in the warmup
pool, and not only on the warm path.

### N-9 — INTERIM EFFICACY DISCLOSURE (adjudicated R-2)

**The healthy-release rollback CAN RECUR until the follow-up transaction-discipline commission lands.** With
the `queue_freshness` probe's own transaction leak still live (§7 non-goal #3, deliberately out of scope), the
pool-leak signature will be **correctly detected** by A1's leak-refusal input — and the grace will therefore
**refuse to engage BY DESIGN**. That is the requirement working as specified, not a defect: A1 is built so it
**cannot mask** the leak (§7 boundary statement). The consequence is disclosed, not hidden: while the leak is
live, a healthy release under load will still present the row-3 signature, the soak will still see no greens,
and the C-1 auto-rollback can fire again exactly as it did on 2026-10-26.

**Interim primary objective.** Until the transaction-discipline commission lands, the incident's co-causes are
carried by **C1/C2** (the context7 storm — circuit-break + DNS pre-refusal), **A3** (the resume burst — held
across the ceremony), and **A4** (pool capacity + **surfacing**, so an operator can *see* the leak rather than
infer it from timeouts). **This file does not rank the co-causes** (§1.2: the RCA's weighting is internally
inconsistent and the requirements must not assume either dominates) — the three lanes above are the
**interim** remedies, and each stands on its own. The **grace covers only the busy-not-leaking signature**
(row 1 of the §4.0 table); it is not, and must not be presented as, a fix for the leak-shaped failure mode.
Operators reading the readiness `detail` after this commission should expect `grace_active=false` with a leak
indicator under sustained load, and should read that as *the known non-goal is live*, not as *the grace is
broken*.

---

## 7. Explicit non-goals (owned by a follow-up commission)

These are **out of scope** for this commission, each with its reason. **A1 must be designed so it CANNOT MASK
items 1–4; A4 must be designed so it does NOT PRETEND TO CURE them.**

| # | Non-goal | Why it is out of scope | Owner |
|---|---|---|---|
| 1 | `daemon/tools/ens_db_tools.py:1479` and `:1491` — an explicit `BEGIN` **outside** the `try`, so a failure skips the rollback | A separate transaction-discipline defect in a tool file; fixing it inside this commission would widen the diff into an unrelated surface | follow-up (DB-transaction-discipline) |
| 2 | The `/api/instances` ORM read-path leak | Same class as 1 but a different read path; it is a **symptom contributor**, not a cause of the soak-gate failure | follow-up (DB-transaction-discipline) |
| 3 | The `queue_freshness` probe's **own** transaction leak — and note it runs on **P1** (the SQLAlchemy manager engine: `daemon/api.py:3779` `make_queue_probe(manager.engine, …)`), **not** on the **P2** saver pool that A4 sizes; **the P1 mechanism is therefore NOT addressed by A4** | This is the **self-sustaining** class. It is deliberately *out of scope* so that A1 can be built as a **grace that refuses to engage on a leak signature** rather than as a grace that masks a leak. Fixing it here would invalidate A1's leak-refusal AC. A4's ghost-waiter analysis is scoped to **P2** and must not be read as covering this **P1** path | follow-up (DB-transaction-discipline) — **must land before A1's leak-refusal is relaxed** |
| 4 | The `mission_records` mint error-path leak (`UniqueViolation` on `ux_mission_records_anchor_open`, raised inside a transaction without a rollback, 4× per boot) | Same rationale: A4 must size and surface, not cure. It also appears in **both** RCAs, so it deserves its own commission | follow-up (DB-transaction-discipline) |
| 5 | Pool hardening knobs: `pool_pre_ping=True` (already on for the SQLAlchemy pool, `daemon/repositories/factory.py:188`, `:220`), `reset_on_return='rollback'`, `pool_recycle` | Partly **already on**; the rest are DBA-level tuning that belongs with the leak fix (items 2–4), not with a sizing change | follow-up (DB-transaction-discipline) / DBA |
| 6 | The wedge-capture `tasks` → `task` table-name bug (`relation "tasks" does not exist` in the journal) | Unrelated to the soak gate or the MCP warmup; it degrades the *diagnostics* surface but not the gate | follow-up (wedge-capture) |

**Boundary statement (binding).** A1's grace is a **containment** mechanism for a *load* signal, not a
containment mechanism for a *leak*. Its leak-refusal input (N-4) is the enforcement. A4's sizing is a
**capacity** change, not a **cure**; the pool-health surfacing (A4 clause 5) is what lets an operator see the
poison. Neither requirement may be presented as fixing items 1–5.

---

## 8. Requirement-coverage summary

**Phase-column mapping (Worker C).** The *phase* column below is the **requirements-side** grouping
(i)–(iv); the **execution layer ships THREE phase files**, and the mapping is: **phase 1** = the §1–§3
foundations + (i) soak/readiness/pool/auto-continue-hold (A1–A4); **phase 2** = (ii) MCP warmup off-loop +
readiness decoupling + leaks (B1–B3); **phase 3** = (iii) MCP degradation + DNS pre-check + context7 template
+ observability (C1–C4) **AND** (iv) the cross-cutting A5 pins **and** the staged verification. There is **no
separate phase-4 file** — (iv) rides with phase 3.

| Req | Phase | Primary touch point | AC anchor |
|---|---|---|---|
| **A1** | (i) soak / readiness / pool / auto-continue-hold | `daemon/services/readiness.py` (composite + `detail`), `daemon/api.py:3720-3820` refresher, `daemon/constants.py` (new budget constants) | A5(i), A5(iv); N-4 |
| **A2** | (i) soak / readiness / pool / auto-continue-hold | `scripts/upgrade/lib.sh:5053-5248` (`gate_soak`), `:2959-3057` (`_probe_once_classified`), `:5216-5221` (counter) | A5(i), A5(iii); N-5 |
| **A3** | (i) soak / readiness / pool / auto-continue-hold | `daemon/manager.py:13671-13684` (runtime resume dispatcher), `daemon/services/eligible_pending_sweep.py` (2nd dispatcher), `daemon/tools/upgrade_tools.py:693-723` (`_journal_read` anchor), `daemon/tools/upgrade_journal.py:1943` (`reconcile_pending_op`) | A5(i); N-7 |
| **A4** | (i) soak / readiness / pool / auto-continue-hold | `daemon/persistence.py:273-293` (saver pool), `:310-316` (asyncpg pool), `daemon/config.py` (new knob), `daemon/constants.py` (default) | A5(i); N-3 |
| **A5** | (iv) cross-cutting pins + staged verification | `tests/packs/…` (shell) + `tests/integration/…` (real PG) | §5 (i)–(iv) |
| **B1** | (ii) MCP warmup off-loop + readiness decoupling + leaks | `daemon/mcp/stdio_wrapper.py:306`, `:485-486`, `:494`; `daemon/mcp/warmup_pool.py:275`, `:278`; `daemon/manager.py:3137` | A5(i) (loop-lag leg) + **§4.6 (P6)** thresholds |
| **B2** | (ii) MCP warmup off-loop + readiness decoupling + leaks | `daemon/services/readiness.py:295-350` (MCP-independence pin); `daemon/routers/prompts.py:1078-1102` | §5 (i) MCP-independence leg; N-1 |
| **B3** | (ii) MCP warmup off-loop + readiness decoupling + leaks | `daemon/mcp/warmup_pool.py:333-344` (cleanup), `daemon/mcp/stdio_wrapper.py` (child stamp/teardown), `daemon/services/boot_subprocess_sweep.py:21-23` (existing reaper) | §3-B3 (pid-liveness pin); N-2 |
| **C1** | (iii) MCP degradation + DNS pre-check + context7 template + observability | `daemon/mcp/warmup_pool.py:552-583`, `:711-726`; `daemon/sources/circuit_breaker.py:20`; `daemon/mcp/builtin_servers/base.py:125-152` (`is_available()`) | §3-C1 (quarantine pin); N-8 |
| **C2** | (iii) MCP degradation + DNS pre-check + context7 template + observability | `daemon/mcp/warmup_pool.py:253-310` (init + pre-resolve seam) | §3-C2 (DNS-refusal pin) |
| **C3** | (iii) MCP degradation + DNS pre-check + context7 template + observability | `daemon/mcp/builtin_servers/context7.py:33-47`; `daemon/mcp/builtin_servers/base.py:222-223`, `:241-242`; `daemon/tools/infra.py:1527`; `daemon/routers/mcp_servers.py:491`, `:760-768`; **`daemon/manager.py:2855-2867`** (env-preserving schema heal); `daemon/mcp/warmup_pool.py:860-878` (env-precedence hazard) | §3-C3 (schema + derived-URL + child-env + **heal-survival** pins); N-6 |
| **C4** | (iii) MCP degradation + DNS pre-check + context7 template + observability | new per-server degraded registry; `daemon/services/readiness.py:239-266` (`detail`); `daemon/tools/upgrade_tools.py:2088-2092` (stale `reasons` read); `daemon/tools/upgrade_journal.py:331-371`/`:352-353` (journal-write unlock discipline) | §3-C4 (degrade-alone + reportable + recoverable pin + **txn-safety pin**: a daemon-side MCP state transition during a live promote txn must NOT drop `in_flight`) |

### Delivery mechanics (per row)

| Rows | Delivery |
|---|---|
| **A2**, **A3** (shell side) | Working-tree sourced: the frozen binary carries no `scripts/`, and the daemon's executor resolves the scripts dir via `daemon/tools/upgrade_tools.py:1919-1942`. A repo change affects the next promote **without a release bump**. |
| **A1**, **A3** (daemon side), **A4**, **B1**, **B2**, **B3**, **C1**, **C2**, **C3**, **C4** | Normal **stage → promote** path. The live release is **v0.27.8** and is untouched by this commission. |
