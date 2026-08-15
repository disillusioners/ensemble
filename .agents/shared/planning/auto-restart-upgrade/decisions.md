# Auto-Restart / Auto-Upgrade — Decision Log (ADR-style)

- **Date:** 2026-08-15 · **Amended:** 2026-08-15 (post-review pass: ADR-011/012/013 added; ADR-004/005/006/010 amended — findings M1–M8, m1–m8)
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

**Decision (consensus, amended).** **(b)** — `releases/<ver>/{binary, agents/, frontend/dist/, manifest.json}` with `config.yaml`, `.env`, `data/` **outside** `releases/`. Flip = `rename(2)` on the `current` symlink (atomic). Journal `releases/state.json` (current/previous/in-flight txn + started-at/rollback counters/quarantine), atomic write; `rollback.lock` (owner PID + heartbeat, stale-breakable >5 min — m7). Retention 3 releases (**~165 MB**), eviction pins `previous` so the rollback target can never be evicted. **(M5 amendment)** every staged release carries `manifest.json` — `binary_version`, `known_schema_gen`, `contains_contract_phase`, `rollback_safe`, trio checksums — **from Phase 2 onward**, so rollback gating is implementable before `daemon_meta` (Phase 5). **(m6 amendment)** staging never writes `.env` into a release dir; `run_app.py:14` loads env from the binary's dir, and a stale per-release `.env` would silently shadow the canonical one.

**Consequence.** Rollback restores a coherent payload in seconds, gated on manifest safety. Journal replaces `.bak` guesswork and subsumes `ensemble-prod-recover`. Cost: ~165 MB disk for 3 generations — accepted.

---

## ADR-005: Auto-rollback trigger, window, and anti-flapping

**Context.** Hard req #5: health failure within X minutes after upgrade → flip back + restart + notify; avoid flapping.

**Options.** (a) Fixed window; (b) health-budget only; (c) hybrid window + budget.

**Decision (consensus, parameterized).** **Hybrid, window-dominated.** Gate = `/livez` ≤60s, `/readyz` green ≤120–180s, version verify, **300s soak**; **X = 10 min post-promote** outer window. Anti-flapping: **cooldown 10 min**, **max 3 auto-rollbacks/24h** then halt-for-human; quarantined versions skipped by future promotes. **(M5 amendment)** the rollback action is **gated on the previous release's manifest `rollback_safe: true`** — if unsafe (drop-release) or previous evicted, **halt-for-human + notify** instead of a blind flip (m7).

**Consequence.** Rollback fires only on evidence, never on a single slow probe, and never into an unsafe target. The 3/24h cap remains an open question (OQ5: 1-then-halt alternative).

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

**Context.** Verified: `kill -9` at `Makefile:106` bypasses graceful shutdown; `make build` runs `ensure-latest` first; **the Makefile port sed is already silently broken** (`Makefile:181` targets `${PORT:-8079}` while `config.yaml:50` defaults 8088 and prod runs 8088 — M6).

**Options.** (a) Keep single `install` target, flip logic inline; (b) decompose into `stage`/`promote`/`rollback` with `install` as alias; (c) external orchestrator tool.

**Decision (consensus, amended).** **(b).** `stage VERSION=x` (copy trio + manifest, version smoke, no flip) → `promote VERSION=x` (pg_dump preflight → [drain, Phase 4] → SIGTERM bounded → atomic flip → restart → health gate → commit | manifest-gated auto-rollback). `kill -9` → **SIGTERM + bounded wait** (`timeout_graceful_shutdown` gives dead `SHUTDOWN_TIMEOUT_S` its first consumer). `ensure-latest` stays on interactive `make build`; staging installs what was built. **(M6 amendment)** Phase 1 folds the port fix in: launcher + plist pin **8088** (canonical pending user confirmation, OQ1), broken sed fixed or removed. **(M3 amendment)** Phase 3 promotes **drain-free**; drain slots in at Phase 4.

**Consequence.** `kill -9` eliminated from the upgrade path; muscle-memory `make install` keeps working; dev flow untouched; no phase-ordering contradiction between `promote`'s end-state description and the phased rollout.

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

**Consequence.** PG-outage-at-boot yields delayed startup, never permanent-down. Exit 1 (true crash) still feeds the budget. Cost: one new exit path in `__main__.py` boot + launcher mapping; plist/unit honor the table. Note the distinction now spans **0 / 75 / 78 / 1**.

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

## Decision Index

| ADR | Topic | Decision (one line) |
|---|---|---|
| 001 | Supervisor | launchd primary + systemd (Linux); supervisord rejected; policy in launcher |
| 002 | Restart policy | Liveness-only restarts; readiness never restarts |
| 003 | Health endpoints | Add `/livez` + `/readyz` (cached composite); `/health` stays human-facing |
| 004 | Release layout | Full-payload trios + `manifest.json` + atomic `current` symlink + journal |
| 005 | Auto-rollback | Hybrid gate: 10-min window, 300s soak, cooldown, max 3/24h — **manifest-gated target** |
| 006 | Drain | API draining flag (503) + master pause + census, bounded 120s; no auto cascades |
| 007 | Migration guard | `daemon_meta` + exit 78 on unsafe downgrade + health-gated contract phases + pg_dump |
| 008 | LLM observer | Post-hoc only, `system_background_queue`, never on-path; doubles as watchdog-watcher |
| 009 | Makefile | `stage`/`promote`/`rollback`; `install` alias; SIGTERM replaces `kill -9`; **port fix in Phase 1** |
| 010 | Exit codes | 0 clean / 78 refuse-no-loop / 1 crash-restart |
| 011 *(M1)* | Boot DB outage | Exit 75 + budget-exempt capped backoff + uptime-based budget reset |
| 012 *(M2)* | Orphaned txn | Launcher-start journal sweep executes/clears aged in-flight transactions |
| 013 *(M4)* | Adapter drain bypass | Pre-drain work-ID snapshot census (primary) + best-effort adapter intake pause |
