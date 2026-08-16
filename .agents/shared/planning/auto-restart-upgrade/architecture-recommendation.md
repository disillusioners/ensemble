# Auto-Restart / Auto-Upgrade — Architecture Recommendation

- **Date:** 2026-08-15 · **Amended:** 2026-08-15 (post-review APPROVE-WITH-NOTES: findings M1–M8, m1–m8 incorporated; Gaps ledger corrected — m8) · **Amended 2026-08-16 — OQ1 resolved via `.env.prod` (ADR-014): prod=8088/dev=8079, always distinct + simultaneous; Makefile sed retired, not fixed. Decisions 2+3 remain PENDING.**
- **Method:** Council mode (2-of-4 trigger: cross-system impact + multiple viable approaches; partial high blast radius). Governor `dde006dc-9438-423a-8480-429d2672412c`; 2 councilors (`agentic`, `coding`), skill `resilience-design`; verified against tree at v0.10.4 (`005610fe`) + live prod install dir. Review pass: independent confirmation of all codebase claims; trio size corrected to ~55 MB.
- **Status:** COMPLETE — council 2/2, no refinement round; amendment passes landed all 8 majors + 7 minors + OQ1 user decision.
- **Companions:** `plan-overview.md` (full architecture, failure matrix, phases), `decisions.md` (ADR-001…014)

---

## Recommendation (one paragraph)

Adopt a **supervisor-agnostic watchdog architecture**: launchd (macOS) wrapping a single **launcher script** that owns exponential backoff, burst abort, exit-code mapping (**0/75/78/1** — ADR-010/011), the **orphaned-transaction journal sweep** (ADR-012), and **`.env.prod`-based prod config** (ADR-014 — launcher exports `INSTALL_DIR/.env` staged from repo `.env.prod`, prod=8088/dev=8079 always distinct and simultaneously live); **`/livez` + `/readyz`** probes with readiness as a cached composite (liveness-only restarts; readiness never restarts; boot-time PG outage exits 75 with budget-exempt capped backoff); **full-payload release trios (~55 MB each)** under `releases/` with per-release **`manifest.json`**, an atomic `current` symlink flip, and a JSON upgrade journal; **health-gated `make promote`** with a 10-minute rollback window, 300s soak, 10-min cooldown, max 3 rollbacks/24h *(pending D2)* — **rollback gated on the previous release's manifest `rollback_safe`**, halting for a human rather than blind-flipping; **drain** via a dedicated API-surface `draining` flag (503 on public entry points) + project master pause + **pre-drain work-ID snapshot census** (ADR-013 — external adapters enqueue in-process and bypass HTTP middleware) bounded at 120s, no per-instance cascades; **migration guard** via a new `daemon_meta` table, exit-78 refusal on unsafe downgrade, and health-gated contract phases for destructive drops; and a **strictly post-hoc LLM observer** on `system_background_queue` that doubles as the watchdog-watcher. Net effect: the daemon never stays down after a crash (including through a PG outage at boot), upgrades are one command with manifest-gated automatic rollback in seconds, rollback is safe by construction for additive releases, and dev+prod coexist structurally for live e2e. Total effort ~2–3 engineer-weeks across 6 independently shippable phases, Phase 3 shipping drain-free.

---

## Approach Comparison (five fixed axes)

The contested axes, compared. Risk is inverted in scoring direction (Low risk = good).

### Axis 1 — Supervisor

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|---|---|---|---|---|---|---|
| **A: launchd + systemd adapters, policy in launcher** | Low-Med (two thin configs, one script) | Fits single-host; maps 1:1 to systemd/k8s later | High (no new deps; plist/unit carry no policy) | Low (PID-1 immortal + watchdog-watcher agent for misconfig) | Low | ✅ **Adopted (ADR-001)** |
| B: supervisord everywhere | Low (one config) | Same | Med (Python dep layer on PyInstaller product) | Med (userspace mortality — reintroduces watchdog-death) | Med | Rejected |
| C: policy in supervisor configs | Med (per-supervisor re-expression) | Same | Low (behavior diverges per platform) | Med (drift between plist and unit) | Med | Rejected — policy centralizes in launcher |

### Axis 2 — Health endpoint shape

| Approach | Complexity | Maintainability | Risk | Recommendation |
|---|---|---|---|---|
| **A: `/livez` + `/readyz` split, cached composite readiness** | Low (one background refresher) | High (pattern exists at `api.py:440`) | Low (per-request cost ≈ 0) | ✅ **Adopted (ADR-003)** — consumers take *different actions* |
| B: enrich single `/health` | Lowest | Med | Med (one signal, ambiguous action) | Rejected — cannot express "degraded, don't restart" vs "dead, restart" |
| C: per-request readiness computation | Med | Med | High (DB-heavy probes violate cheapness constraint) | Rejected |

### Axis 3 — Release layout

| Approach | Complexity | Maintainability | Risk | Cost | Recommendation |
|---|---|---|---|---|---|
| **A: full-payload trios + manifest + symlink + journal** | Med (staging script + journal discipline) | High (journal replaces `.bak` guesswork) | Low (rollback = coherent, manifest-gated payload) | ~165 MB disk (3 × ~55 MB) | ✅ **Adopted (ADR-004, amended M5/m7/ADR-014)** |
| B: bare binaries + pointer | Low | Med | High (rollback = version skew — today's bug class, C3) | Low disk | Rejected — operator already hand-compensates |

### Axis 4 — Drain mechanism

| Approach | Complexity | Maintainability | Risk | Recommendation |
|---|---|---|---|---|
| **A: API draining flag + master pause + snapshot census, bounded wait, no cascades** | Med (middleware + thin service) | High (reuses `WritePauseGuard` pattern, pause service, census predicates) | Low (avoids Task↔JobItem gap; snapshot census is external-traffic-proof — M4) | ✅ **Adopted (ADR-006 + ADR-013)** |
| B: master pause only | Low | High | Med (new work keeps arriving via public APIs — C7; census never empties under external traffic — M4) | Rejected for automated path |
| C: per-instance `pause_instance_cascade` quiesce | High | Med | High (known reconciliation gap on resume) | Rejected for automation; retained as operator option |

### Axis 5 — Migration guard

| Approach | Complexity | Maintainability | Risk | Recommendation |
|---|---|---|---|---|
| **A: `daemon_meta` + exit 78 + health-gated contract phases + pg_dump + manifest (interim)** | Med (one table + boot logic + phase gate + manifest at staging) | High (manifest gives Phase-2/3 enforcement; `daemon_meta` completes it in Phase 5) | Low once R1 ships | ✅ **Adopted (ADR-007, amended M5)** |
| B: extend `schema_migrations` | Low | Low (describes a no-op path on PG — C2) | High (marker doesn't reflect real schema) | Rejected |
| C: journal-only (no DB marker) | Low | Med | High (rolled-back binary can't see DB state; no Phase-3 rollback gate without manifest) | Rejected |

### Axis 6 — Prod config / port mechanism (ADR-014)

| Approach | Complexity | Maintainability | Risk | Recommendation |
|---|---|---|---|---|
| **A: `.env.prod` → `INSTALL_DIR/.env`, launcher exports** | Low (pre-existing `Makefile:183-186` copy + launcher export) | High (one canonical prod env source; env precedence verified at `run_app.py:29-31`) | Low (structural dev/prod separation; separate dirs + env files) | ✅ **Adopted (ADR-014, user decision)** |
| B: fix the Makefile config.yaml sed | Low | Med (sed couples install to config-format details — already silently broken once) | Med (format drift breaks it silently again) | Rejected — superseded |
| C: per-install `config.yaml` edit | Med | Low (drifts from repo config; merge pain) | Med | Rejected |

---

## Trade-offs that drove the recommendation

1. **Launcher-owned policy over supervisor-owned policy** — identical crash-loop behavior on macOS/Linux and a 1:1 k8s mapping later.
2. **Full trios + manifest over bare binaries** — ~165 MB disk vs eliminating the version-skew rollback bug; the manifest is the cheap keystone that makes early-phase rollback gating implementable (M5).
3. **Drain-as-courtesy framing** (reviewer-endorsed strongest design move) — because checkpoints + job locks + StaleTaskRecovery make a hard stop recoverable, the bounded drain can timeout-proceed, Phase 3 can ship drain-free (M3), and post-snapshot external arrivals are an accepted interrupt (M4/ADR-013) rather than a drain blocker.
4. **Exit-75 budget exemption + uptime reset (ADR-011)** — closes the one true liveness hole (M1): transient PG outage at boot can no longer cascade into permanent-down.
5. **Launcher journal sweep (ADR-012)** — closes the second true safety gap (M2): every orphaned promote converges within one rollback window, at a layer below the daemon.
6. **Contract-phase gating (R1) is load-bearing** — without it, auto-rollback across a column-dropping release points a binary at a schema it cannot run; manifest + `daemon_meta` enforce refusal in two layers.
7. **`.env.prod` over sed (ADR-014)** — the user-confirmed pre-existing mechanism; config separation becomes topological (separate dirs/env files/ports) rather than procedural, making simultaneous dev+prod e2e structural.

## Risks

- 🔴 **R1** — Destructive boot-time drops (`manager.py:478-500`) make rollback unsafe across drop-releases **today**. Resolution: manifest `rollback_safe` block (Phase 2/3) + contract-phase gate (Phase 4).
- 🔴 **R2** — `pause_writes` reuse would starve internal-session writes (`manager.py:368-371`). Resolution: dedicated `draining` flag; verify `WriteGuardSession` semantics before Phase 4.
- 🟡 R3 launchd env (absolute paths, no TTY, canonical env = `INSTALL_DIR/.env` from `.env.prod`, never inside a release dir) · R4 drain × Task↔JobItem gap (no cascades) · R5 `pg_dump` cost (timeout + skip) · **R9 external-adapter arrivals post-snapshot are interrupted, not drained** (accepted under courtesy framing).
- 🟢 R6 macOS quarantine xattr · R7 `ensure-latest` reproducibility (explicit VERSION; **PENDING D3**) · R8 single-host shared fate (out of scope, stated).

## Confidence

**High, post-review + user decision.** Council produced two independent ground-truth corrections to four stated facts (C1–C4); the reviewer independently **confirmed all codebase claims** (trio size corrected ~55 MB) and validated requirements coverage as complete; the amendment passes landed both "true safety gap" fixes (M1, M2) plus six more majors; OQ1 is now a settled user decision (ADR-014) with the mechanism verified against `run_app.py`/`Makefile`. The recommendation flips only if: (a) D2/D3 land against the current defaults (parameter changes, not architecture changes), or (b) Linux deploy lands before Phase 2 (supervisor configs swap; no architecture change).

## Gaps (corrected ledger — m8)

The original "Gaps: None" was an **over-claim**: the review found 8 major gaps in plan quality (M1–M8), all now incorporated:

- **M1** — boot burst-abort permanent-down → **closed** (ADR-011: exit 75 + budget reset + matrix row).
- **M2** — orphaned promote txn → **closed** (ADR-012: launcher journal sweep + matrix row).
- **M3** — phase-ordering contradiction → **closed** (Phase 3 ships drain-free; §5 note).
- **M4** — adapter drain bypass → **closed** (ADR-013: snapshot census + R9 accepted residual).
- **M5** — unimplementable rollback-refusal rule → **closed** (release manifest from Phase 2).
- **M6** — broken port sed → **closed via ADR-014** (supersedes the sed-fix approach: `.env.prod` is the mechanism; sed + `PROD_PORT` retired as legacy).
- **M7** — nonexistent heartbeat signal → **closed** (`Task.last_heartbeat_at` max-age + Phase-1 fallback flagged in §10).
- **M8** — req #3 mapping unstated → **stated** (§4.2: `/livez` → backoff+burst; `/readyz` → degrade/rollback, never restart).

**Remaining genuinely unverified items** (see `plan-overview.md` §10): PM-bug spawn site; uvicorn SIGTERM bound behavior; `Task.last_heartbeat_at` stamping cadence; `WriteGuardSession` internal-session semantics. Each has a named phase and a fallback — none blocks planning.

## Decisions Pending (user)

- **D2 (OQ5) — max auto-rollbacks: 3/24h (council default) vs 1-then-halt. PENDING.**
- **D3 (OQ8) — `ensure-latest` demotion sign-off (release builds install what was built). PENDING.**
- Also open but lower-stakes: notify channel (OQ3), capability-probe failure policy (OQ4), retire `ensemble-prod-recover` (OQ7).

**Resolved:** OQ1 (**prod=8088 / dev=8079 via `.env.prod`, simultaneous — ADR-014, user decision**), OQ6 (prod = PostgreSQL), OQ2 (10-min window adopted; semantic bugs covered by Phase-5 smoke test rather than longer soak).
