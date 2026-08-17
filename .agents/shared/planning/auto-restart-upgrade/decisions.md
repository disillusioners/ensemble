# Auto-Restart / Auto-Upgrade — Decision Log (ADR-style)

- **Date:** 2026-08-15 · **Amended:** 2026-08-15 (post-review pass: ADR-011/012/013 added; ADR-004/005/006/010 amended — findings M1–M8, m1–m8) · **Amended 2026-08-16 — OQ1 resolved via `.env.prod` (ADR-014)** · **Amended 2026-08-16 (final) — D1 FINAL: prod=9797 (ADR-014 amended, supersedes 8088); D2/D3 APPROVED; ADR-015 added (agent-facing upgrade tooling). Phase 1 implementation IN PROGRESS.**
- **Method:** Architect council (`agentic` + `coding` councilors, skill `resilience-design`), governor `dde006dc-9438-423a-8480-429d2672412c`. Where councilors diverged, both positions are recorded verbatim-adjacent and the synthesis ruling is stated. Review: APPROVE-WITH-NOTES (0 critical / 8 major / 7 minor) — all majors incorporated.
- **Companion:** `plan-overview.md` (full architecture), `architecture-recommendation.md` (5-axis comparison)

Format: **Context → Options → Decision → Consequence**.

---

## ADR-001: Supervisor choice — launchd primary + systemd for Linux; supervisord rejected

**Context.** Hard req #2: external watchdog with restart=on-failure, exponential backoff + burst limit; evaluate launchd vs systemd vs supervisord; stay supervisor-agnostic. Verified: no supervisor exists today (C5); prod host is macOS; potential Linux deploy. Single-host, no k8s.

**Options.**
- *launchd (macOS native)* — PID-1 immortal, zero dependencies, `KeepAlive`/`ThrottleInterval`; no exponential backoff natively.
- *systemd (Linux)* — the Linux-native equivalent; not present on macOS.
- *supervisord (cross-platform)* — one config surface everywhere; but it is a userspace process (mortality → "who watches the watchdog"), and adds a Python dependency layer to a PyInstaller product.

**Decision (unanimous).** **launchd primary** on the macOS prod host; **systemd unit generated** when Linux deploy lands; **supervisord rejected**. Backoff/burst policy lives in the **launcher script** (not the supervisor) so behavior is identical across launchd/systemd.

**Consequence.** The "watchdog itself dying" scenario becomes a non-issue by construction; a watchdog-watcher agent (§4.6, m3) covers supervisor *misconfiguration*. Supervisor-agnosticism = launcher + `/livez`//`/readyz` contract + exit-code table (ADR-010/011). Cost: two thin config artifacts, carrying no policy.

---

## ADR-002: Restart policy — liveness only, never readiness

**Context.** Supervisor must restart on failure, but restarting a process whose *database* is down produces a crash loop and worsens the outage.

**Options.** (a) Restart on `/livez` failure only; (b) restart on readiness failure too; (c) restart on process exit only, no probe input.

**Decision (consensus).** **(a)** — restart decisions take `/livez` only. `/readyz` failures **never** restart; they degrade (503 + banner) and notify. *(Amended: ADR-011 extends this to the boot-time DB-outage case — a distinct non-looping boot outcome rather than a readiness-style restart.)*

**Consequence.** DB outage while running = degraded-but-alive daemon. DB outage at boot = exit 75 + capped launcher backoff without burst-budget consumption (ADR-011). The upgrade gate remains the only readiness *action* consumer (ADR-005).

---

## ADR-003: Health endpoints — add `/livez` + `/readyz`, keep `/health` human-facing; readiness as cached composite

**Context.** Today `/health` (`daemon/api.py:1486-1524`) returns `status="healthy"` unconditionally — it can gate nothing. Constraint: probes must be cheap.

**Options.** (a) Enrich `/health` with degraded semantics (single endpoint); (b) split `/livez` + `/readyz`, keep `/health` enriched; (c) readiness computed per-request.

**Decision (consensus).** **(b) with a cached composite for readiness.** `/livez` = event-loop answer + optional 5s heartbeat staleness. `/readyz` = cached composite refreshed by a 10s background task (`SELECT 1` w/ 500ms timeout, queue-freshness, bus-started flags, schema check, draining flag) — handler is an O(1) memory read. `/readyz` reports `draining: true` + `Retry-After` during drain.

**Consequence.** Split is required because consumers take different actions (restart vs. degrade). Per-request cost ≈ 0. *(M7 amendment: the queue-freshness component = **max-age over `Task.last_heartbeat_at`** (`repositories/task/models.py:214`) — no job-processor heartbeat exists today; if the stamping cadence proves too sparse, add a lightweight processor heartbeat in Phase 1. M8 amendment: req #3 "N consecutive failures → action" = `/livez` → backoff + burst restart; `/readyz` → degrade/notify, and within the post-upgrade window, auto-rollback.)*

---

## ADR-004: Release layout — full-payload trios + `manifest.json` + atomic `current` symlink + JSON journal

**Context.** Verified correction C3: a release is binary + `agents/` + `frontend/dist/` (**~55 MB, reviewer-confirmed** — m1); today's `.bak` preserves the binary alone → rollback = version skew; operator already hand-keeps `ensemble-prod-recover`.

**Options.** (a) Bare-binary generations + pointer; (b) full-payload trios per version; (c) keep current `.bak` scheme + pointer.

**Decision (consensus, amended).** **(b)** — `releases/<ver>/{binary, agents/, frontend/dist/, manifest.json}` with `config.yaml`, `.env`, `data/` **outside** `releases/`. Flip = `rename(2)` on the `current` symlink (atomic). Journal `releases/state.json` (current/previous/in-flight txn + started-at/rollback counters/quarantine), atomic write; `rollback.lock` (owner PID + heartbeat, stale-breakable >5 min — m7). Retention 3 releases (**~165 MB**), eviction pins `previous` so the rollback target can never be evicted. **(M5 amendment)** every staged release carries `manifest.json` — `binary_version`, `known_schema_gen`, `contains_contract_phase`, `rollback_safe`, trio checksums — **from Phase 2 onward**. **(m6 amendment, restated per ADR-014)** the env invariant is two-part: release directories contain **no `.env` of any kind**, and the canonical prod env source is `INSTALL_DIR/.env` (outside `releases/`, staged from repo `.env.prod` by `make install`/`make stage`) — which the launcher exports before exec, taking precedence over the frozen binary's own `.env` load (`run_app.py:29-31` env precedence).

**Consequence.** Rollback restores a coherent payload in seconds, gated on manifest safety. Journal replaces `.bak` guesswork and subsumes `ensemble-prod-recover`. Cost: ~165 MB disk for 3 generations — accepted.

---

## ADR-005: Auto-rollback trigger, window, and anti-flapping

**Context.** Hard req #5: health failure within X minutes after upgrade → flip back + restart + notify; avoid flapping.

**Options.** (a) Fixed window; (b) health-budget only; (c) hybrid window + budget.

**Decision (consensus, parameterized — D2 APPROVED).** **Hybrid, window-dominated.** Gate = `/livez` ≤60s, `/readyz` green ≤120–180s, version verify, **300s soak**; **X = 10 min post-promote** outer window. Anti-flapping: **cooldown 10 min**, **max 3 auto-rollbacks/24h then halt-for-human (user-approved, D2/OQ5 — final)**; quarantined versions skipped by future promotes. **(M5 amendment)** the rollback action is **gated on the previous release's manifest `rollback_safe: true`** — if unsafe (drop-release) or previous evicted, **halt-for-human + notify** instead of a blind flip (m7).

**Consequence.** Rollback fires only on evidence, never on a single slow probe, and never into an unsafe target. All parameters final.

---

## ADR-006: Drain mechanism — dedicated draining flag on public API + master pause + census; no per-instance cascades

**Context.** Verified C6/C7: master pause is enforced at 3 layers for *dispatch* but does not block public entry points; the Task↔JobItem reconciliation gap makes instance-cascade resume risky. Councilor divergence — recorded:

> **`agentic` councilor:** "preferring master-pause-only drain (no instance cascades) sidesteps most of it [the Task↔JobItem reconciliation gap]. … per-instance quiesce via `pause_instance_cascade` only when an operator wants turn-boundary cleanliness (usually unnecessary)."

> **`coding` councilor:** "project-level master pause is NOT sufficient; a dedicated drain mode is needed — and the right seam already exists. Master pause only stops job *dispatch* … it does not block public entry points … a separate, shallower 'draining' flag for the API surface, reserving `pause_writes` for the true quiesce window."

**Options.** (a) Master-pause-only; (b) master pause + draining flag; (c) full per-instance quiesce.

**Decision (synthesis, amended by ADR-013).** Both, on orthogonal axes:
1. **New-work blocking**: dedicated `draining` flag → 503 + `Retry-After` on public entry points, mirroring the `WritePauseGuard` pattern. **Not** `pause_writes` (R2).
2. **Existing-work draining**: master pause + census poll, **bounded 120s**; **no per-instance cascades** in the automated path.
3. **Stuck-marker recovery**: version-independent drain marker, boot-releasable by any binary.
4. *(M4/ADR-013)* census semantics fixed to a **pre-drain work-ID snapshot** because external adapters bypass HTTP middleware entirely.

**Consequence.** Drain blocks new HTTP work and drains pre-drain work without the cascade risk. Reframing stands: **drain is a courtesy, not correctness** — which is also why Phase 3 can ship the flip/gate/rollback machinery **drain-free** (M3).

---

## ADR-007: Migration guard strategy — `daemon_meta` table + exit-code contract + health-gated contract phases

**Context.** Verified corrections C2 (SQL migration runner no-ops on PG; no version marker) and C4 (boot runs destructive drops unconditionally today).

**Options.** (a) Reuse/extend `schema_migrations`; (b) new `daemon_meta` table written pre-`_ensure_*`; (c) journal-only.

**Decision (consensus, amended).** **(b) + exit-code contract + pg_dump preflight + contract-phase gating.**
- `daemon_meta(binary_version, schema_gen, contract_phases_applied)` written at boot **before** any `_ensure_*`.
- Downgrade (DB gen > binary's known gen): no contract phase applied → warn + proceed; contract phase applied → **refuse, exit 78**.
- Destructive drops (`manager.py:478-500`) move behind a **contract-phase gate** that runs only after the new version passes its health gate.
- `pg_dump` snapshot at promote preflight (timeout + skip, retention 2). **Prod DB confirmed PostgreSQL (m4).**
- **(M5 amendment)** until `daemon_meta` lands (Phase 5), the same rollback-safety rule is enforced by the release **manifest** (`rollback_safe`) from Phase 2 — two enforcement layers, one rule, no phase-ordering hole.

**Consequence.** Defined, safe-by-construction downgrade behavior for additive releases; destructive drops stop being silent rollback hazards. The additive discipline going forward is a process commitment enforced by review.

---

## ADR-008: LLM observer scope — strictly post-hoc, off-path

**Context.** Hard req #1: no LLM in the critical recovery path.

**Options.** (a) LLM in rollback decisioning (violates hard req); (b) post-hoc observer only; (c) no LLM at all.

**Decision (consensus).** **(b).** Observer consumes journal transitions + log tail + `daemon_meta` transitions; triggers only **after terminal events** and after `/readyz` green; runs on `system_background_queue` (c=1); a low-frequency launchd agent re-enqueues if the daemon is down — and **doubles as the watchdog-watcher (m3)**: daemon absent >10 min → notify with a launchd-config hint.

**Consequence.** Zero LLM latency/cost in any recovery decision. Postmortems are trivially removable (Phase 6).

---

## ADR-009: Makefile integration — `stage` / `promote` / `rollback`; `install` = alias; SIGTERM replaces `kill -9`

**Context.** Verified: `kill -9` at `Makefile:106` bypasses graceful shutdown; `make build` runs `ensure-latest` first; the Makefile port sed (`Makefile:181`) is already silently broken (targets `${PORT:-8079}` while `config.yaml:50` defaults 8088).

**Options.** (a) Keep single `install` target, flip logic inline; (b) decompose into `stage`/`promote`/`rollback` with `install` as alias; (c) external orchestrator tool.

**Decision (consensus, amended).** **(b).** `stage VERSION=x` (copy trio + manifest, stage `.env.prod` → `INSTALL_DIR/.env`, version smoke, no flip) → `promote VERSION=x` (pg_dump preflight → [drain, Phase 4] → SIGTERM bounded → atomic flip → restart → health gate → commit | manifest-gated auto-rollback). `kill -9` → **SIGTERM + bounded wait** (`timeout_graceful_shutdown` gives dead `SHUTDOWN_TIMEOUT_S` its first consumer). **(M3 amendment)** Phase 3 promotes **drain-free**; drain slots in at Phase 4. **(M6 amendment — SUPERSEDED by ADR-014)** port config comes from **`.env.prod` staged as `INSTALL_DIR/.env`**; the broken `Makefile:181` sed and `PROD_PORT` are **retired as legacy artifacts, not fixed**. **(D3 APPROVED — final)** `ensure-latest` demoted: release staging installs **what was built** (explicit `VERSION`, fail-if-not-at-tag, no auto `git pull`); `make build` keeps `ensure-latest` for interactive dev only.

**Consequence.** `kill -9` eliminated from the upgrade path; muscle-memory `make install` keeps working; dev flow untouched; no phase-ordering contradiction; release reproducibility guaranteed.

---

## ADR-010: Process exit-code contract — 0 / 78 / 1

**Context.** The supervisor must distinguish "restart me" from "do not loop" from "crashed", or a schema-refusing binary produces an infinite restart loop.

**Options.** (a) Single nonzero exit; (b) explicit contract; (c) sidecar state file only.

**Decision (consensus).** **(b)** (+ launcher restart-state file as belt-and-braces): `0` clean · `78` config/schema refuse — **no restart** · `1` crash — restart with backoff into the burst budget.

**Consequence.** Crash-loop protection gets a semantic escape hatch. *(Superseded in part by ADR-011, which adds `75`.)*

---

## ADR-011 (M1): Boot-time DB outage — exit 75 + budget-exempt capped backoff + uptime-based budget reset

**Context (review finding M1).** The failure matrix interacted "crash loop" × "DB down at boot" six times into a hole: a PG outage >10 min at boot produces >5 exit-1 restarts → burst abort → daemon **permanently down even after PG recovers**. ADR-002 covered only DB-down-while-running.

**Options.** (a) Nothing — accept the hole (rejected: permanent-down after transient infra outage); (b) exempt DB-unreachable boots from the burst budget via a distinct exit code; (c) reset the budget after sustained uptime only; (d) both.

**Decision.** **(d) — both, belt and braces:**
1. **Exit 75 (EX_TEMPFAIL) for boot-time PG unreachability.** The daemon's boot connection step, on failure, exits 75 rather than crashing. The launcher maps 75 → **capped backoff (≤60s) that does NOT decrement the burst budget**. One-time notify after the first 75 (not per-retry).
2. **Uptime-based budget reset:** ≥10 min continuous runtime → burst counter resets to zero, so scattered flaky crashes never accumulate into a false abort.
3. Failure matrix gains the explicit interaction row (plan §6 row 2).

**Consequence.** PG-outage-at-boot yields delayed startup, never permanent-down. Exit 1 (true crash) still feeds the budget. Cost: one new exit path in `__main__.py` boot + launcher mapping; plist/unit honor the table. The distinction now spans **0 / 75 / 78 / 1**.

---

## ADR-012 (M2): Orphaned in-flight upgrade transaction — launcher-start journal sweep

**Context (review finding M2).** If `make promote` dies after flipping `current` (or mid-drain), the journal's `in-flight` transaction has **no owner**: the stuck state leaves the daemon degraded forever, and the existing stuck-marker recovery only unpauses queues — it never resolves the transaction.

**Options.** (a) Human-only recovery (rejected — silent degraded-forever); (b) daemon boot-time sweep only (insufficient — the daemon may be the thing that won't boot); (c) launcher-start sweep + daemon boot sweep.

**Decision.** **(c).** The **launcher performs a journal sweep at start, before resolving `current`**: an `in-flight` transaction older than the rollback window (10 min) → **launcher executes the rollback itself** (repoint to `previous`, notify, escalate) if the flip happened, or **clears the transaction** if it never reached the flip. The daemon performs the same sweep at boot (belt-and-braces) and releases the stuck-drain marker alongside.

**Consequence.** Every stuck promote converges within one rollback window, with an owner at a layer *below* the daemon (works even when the daemon is crash-looping). Cost: journal schema gains `txn.started_at`; the launcher gains ~30 lines. Interaction with ADR-005: the sweep counts as an auto-rollback (cooldown + counters apply).

---

## ADR-013 (M4): External source adapters bypass drain 503 — snapshot census

**Context (review finding M4).** Telegram/Discord/Slack/Scheduler adapters enqueue **in-process** (`daemon/sources/registry.py:873`) and never traverse HTTP middleware — the census would never empty under live external traffic, breaking the bounded-drain design.

**Options.** (a) Pause adapter intake during drain (correct but heavy: gateway buffering semantics differ per platform; Discord live-socket buffering not worth building); (b) census against a **pre-drain work-ID snapshot** (drain completes when all work IDs present at drain-start are terminal; post-snapshot arrivals are simply interrupted and resume from checkpoints); (c) both.

**Decision.** **(c), with (b) as the load-bearing half.** **Primary: snapshot census** — arrival work IDs captured at drain-start define the drain set; completeness = that set is terminal. **Secondary (best-effort):** poll-capable adapters (Telegram/Slack) pause intake during drain via the existing adapter-supervisor pause; Discord gateway buffering is explicitly *not* engineered — snapshot semantics cover it.

**Consequence.** Drain completes deterministically under external traffic; post-snapshot arrivals get exactly the checkpoint-resume recovery that the courtesy framing already guarantees (no message loss — they resume post-upgrade). Cost: census query gains a work-ID-set filter; adapter pause is a small supervisor hook. Residual risk logged as R9 (accepted).

---

## ADR-014 (OQ1/user decision): Prod/dev port separation via `.env.prod` — simultaneous coexistence

**Context (user decision, 2026-08-16; amended same day by D1).** The whole point of the separate install flow is that **e2e tests can run against dev while prod stays live** — dev and prod must run **simultaneously** on distinct ports, a first-class requirement. Verified mechanism: the frozen binary's wrapper (`run_app.py:17-31`) loads `.env` from the **executable's** directory and only sets vars not already present — so explicitly exported env wins; `make install` already stages repo `.env.prod` → `INSTALL_DIR/.env` (`Makefile:183-186`, with `.env` fallback); `config.yaml`'s `port: ${PORT:-…}` resolves from the exported environment. Legacy artifacts: `PROD_PORT ?= 9797` (`Makefile:6`) and the broken sed at `Makefile:181`.

**Options.** (a) Fix/repair the Makefile config.yaml sed (M6's original amendment — superseded); (b) `.env.prod` staged to `INSTALL_DIR/.env` as the prod config source (pre-existing mechanism, user-confirmed intent); (c) edit `config.yaml` directly per install.

**Decision (user).** **(b).** Port config comes from **`.env.prod` staged as `INSTALL_DIR/.env`** by `make install`/`make stage`. The launcher `cd INSTALL_DIR`, reads `INSTALL_DIR/.env`, exports it, then execs the `current` binary — launcher exports take precedence over the frozen binary's own `.env` load (`run_app.py:29-31`). The Makefile's `PROD_PORT` variable and the `Makefile:181` sed are **retired as legacy artifacts** (removed during Phase 1/2 Makefile work), not repaired. The m6 env invariant: **no `.env` of any kind inside release directories; `INSTALL_DIR/.env` (from `.env.prod`) is the single canonical prod env source**.

> **⚡ AMENDED (D1 FINAL, 2026-08-16):** the prod port is **9797, not 8088**. Rationale: 8088 is a very common dev port in the user's company; 9797 keeps the e2e port unique across projects. Final port pair: **dev = 8079, prod = 9797 — always distinct, both runnable simultaneously.** The `.env.prod` mechanism is unchanged (PORT=9797 in `.env.prod`). `PROD_PORT ?= 9797` is now coincidentally correct but remains legacy/subsumed; the broken `Makefile:181` sed is still retired. *History retained: the original ADR-014 pass recorded 8088 (inferred from `config.yaml:50` default + observed prod runtime); the user's explicit D1 overrides that inference.*

**Consequence.** Dev + prod coexistence is structural (separate dirs, separate env files, always-distinct ports 8079/9797) — e2e-against-dev-while-prod-live is guaranteed by configuration topology rather than operator discipline. Phase 1 launcher + plist read the port from `INSTALL_DIR/.env` (9797). Supersedes ADR-009's M6 amendment; OQ1 resolved and FINAL.

---

## ADR-015 (new requirement): Agent-facing upgrade/version tooling — `system_upgrade` + `release_info` for ari/jober, human-triggered only

**Context (user requirement, 2026-08-16).** The user wants **conversational upgrade control**: ask ari/jober in chat to upgrade the system or report available versions, instead of shelling out to `make promote` / reading git tags manually. Constraint stack: hard req #1 says no LLM in the critical recovery path; the upgrade must be **human-triggered only, never autonomous**; PM stays read-only on code and these tools are **operational, not code-editing**. Verified registration mechanics: `tools.allow` in each agent's `meta.json` is the canonical authorization signal; the allow-list expansion in `daemon/tools/instance.py:284-289` resolves both category names and individual tool names; single-tool worker allows are established precedent (`tools.allow: ["plane_sync"]`, `instance.py:1849/1898`). Ari's allow-list today: `agents/ari/meta.json` (14 entries, incl. `job_messages`/`job_tree`/`job_progress`/`job_inject`). Jober's: `agents/jober/meta.json` (10 entries incl. `mcp`, `knowledge`).

**Options.** (a) **Internal tools** — a new `system_upgrade` category in `daemon/tools/`, exposed via per-agent `tools.allow` (the job-queue-tools pattern); (b) **MCP server** wrapping the pipeline — cross-system but heavier: MCP registration, a server process, and the PM `mcp_full_access` precedent shows category/MCP tool-surface expansion has its own failure modes (the v0.10.4 PM bug); (c) **bash-only** — agents shell out to `make promote` via a bash tool (no new tooling, but zero gating, no structured progress, and bash access itself is a bigger hammer).

**Decision.** **(a) Internal tools, category `system_upgrade`, two tools, ari + jober only:**
- **`system_upgrade`** — executes the full promote pipeline (drain → stage/flip → health gate → auto-rollback on failure, per §5 of the plan): resolves the target (latest tag or explicit version), runs the same sequence as `make promote`, streams progress, returns the terminal result (committed / rolled-back / refused). **Human-trigger enforcement (two factors, both required):** (1) `user_confirmed: true` parameter, AND (2) a **user-originated trigger marker** — a server-side session attribute set only on genuine user messages in the current session (OQ9 recommends this over a token lifecycle). The tool refuses without both; refusal is returned to the agent as a structured "requires explicit user confirmation" result. The LLM never decides go/rollback — the deterministic gate does; the tool is a front door, not a decision-maker (hard req #1 preserved).
- **`release_info`** — read-only: recent git tags / release versions, deployed version (`current` + journal), changelog summary per release, `rollback_safe`/quarantine status. No gate.
- **Registration points:** new `daemon/tools/upgrade_tools.py` following the per-instance factory + category-registry pattern (`daemon/tools/job_queue.py`); category **`system_upgrade`** registered in the tool registry; add `"system_upgrade"` to `tools.allow` in **`agents/ari/meta.json`** and **`agents/jober/meta.json`**. No `deny` rules needed anywhere — `tools.allow` is default-deny (agents without the category never see the tools), which satisfies "deny rules for other agents" structurally.
- **Phase placement: Phase 7** (after Phase 6) — wraps the promote pipeline so Phases 2–3 are prerequisites; no blocking dependency on Phases 4–6 (`system_upgrade` can launch drain-free promotes at first and gains drain/observer integration as those phases land). `release_info` (pure reads) can be split out and shipped as early as Phase 2 if conversational visibility is wanted sooner.

**Consequence.** Conversational upgrade control with a hard human gate; release visibility for free; zero MCP surface (avoiding the PM-style category-expansion failure mode); default-deny covers all other agents including PM. Costs/risks: R10 — an agent could fabricate `user_confirmed: true`, hence the server-side trigger marker as the second factor (single-host trust model bounds the residual). OQ9 tracks the exact marker plumbing (session attribute vs. token) for Phase 7 kickoff.

---

## Decision Index

| ADR | Topic | Decision (one line) |
|---|---|---|
| 001 | Supervisor | launchd primary + systemd (Linux); supervisord rejected; policy in launcher |
| 002 | Restart policy | Liveness-only restarts; readiness never restarts |
| 003 | Health endpoints | Add `/livez` + `/readyz` (cached composite); `/health` stays human-facing |
| 004 | Release layout | Full-payload trios + `manifest.json` + atomic `current` symlink + journal |
| 005 | Auto-rollback | Hybrid gate: 10-min window, 300s soak, cooldown, **3/24h (D2 APPROVED)** — manifest-gated target |
| 006 | Drain | API draining flag (503) + master pause + census, bounded 120s; no auto cascades |
| 007 | Migration guard | `daemon_meta` + exit 78 on unsafe downgrade + health-gated contract phases + pg_dump |
| 008 | LLM observer | Post-hoc only, `system_background_queue`, never on-path; doubles as watchdog-watcher |
| 009 | Makefile | `stage`/`promote`/`rollback`; `install` alias; SIGTERM replaces `kill -9`; port via `.env.prod` (ADR-014); **`ensure-latest` demoted (D3 APPROVED)** |
| 010 | Exit codes | 0 clean / 78 refuse-no-loop / 1 crash-restart |
| 011 *(M1)* | Boot DB outage | Exit 75 + budget-exempt capped backoff + uptime-based budget reset |
| 012 *(M2)* | Orphaned txn | Launcher-start journal sweep executes/clears aged in-flight transactions |
| 013 *(M4)* | Adapter drain bypass | Pre-drain work-ID snapshot census (primary) + best-effort adapter intake pause |
| 014 *(OQ1/D1)* | Prod/dev ports | `.env.prod` → `INSTALL_DIR/.env`; **prod=9797 (D1 FINAL, supersedes 8088)/dev=8079** always distinct + simultaneous; Makefile sed/`PROD_PORT` retired |
| 015 *(new)* | Agent tooling | `system_upgrade` (human-gated, two-factor) + `release_info` (read-only) in category `system_upgrade`; ari+jober `tools.allow` only; default-deny elsewhere; Phase 7 |

**User decisions — all FINAL/APPROVED:** D1 prod port = 9797 (ADR-014 amended) · D2 rollback cap = 3/24h (ADR-005) · D3 `ensure-latest` demoted (ADR-009). **No decisions pending.**

**Open items (non-blocking):** OQ3 notify channel · OQ4 capability-probe policy · OQ7 retire `ensemble-prod-recover` · OQ9 ADR-015 trigger-marker mechanics (Phase 7 kickoff).
