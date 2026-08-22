# Auto-Restart / Auto-Upgrade — Architecture Recommendation

- **Date:** 2026-08-15 · **Amended:** 2026-08-15 (post-review APPROVE-WITH-NOTES: findings M1–M8, m1–m8 incorporated; Gaps ledger corrected — m8) · **Amended 2026-08-16 — OQ1 resolved via `.env.prod` (ADR-014)** · **Amended 2026-08-16 (final) — D1 FINAL: prod=9797; D2/D3 APPROVED; ADR-015 added (agent-facing upgrade tooling); Phase 1 IN PROGRESS on `feature/auto-restart-phase1`**
- **Method:** Council mode (2-of-4 trigger: cross-system impact + multiple viable approaches; partial high blast radius). Governor `dde006dc-9438-423a-8480-429d2672412c`; 2 councilors (`agentic`, `coding`), skill `resilience-design`; verified against tree at v0.10.4 (`005610fe`) + live prod install dir. Review pass: independent confirmation of all codebase claims; trio size corrected to ~55 MB.
- **Status:** COMPLETE — council 2/2, no refinement round; amendment passes landed all 8 majors + 7 minors + all three user decisions (D1/D2/D3 FINAL/APPROVED) + the ADR-015 agent-tooling requirement. **Phase 1 implementation IN PROGRESS** (developer, `feature/auto-restart-phase1`).
- **Companions:** `plan-overview.md` (full architecture, failure matrix, phases), `decisions.md` (ADR-001…015)

---

## Recommendation (one paragraph)

Adopt a **supervisor-agnostic watchdog architecture**: launchd (macOS) wrapping a single **launcher script** that owns exponential backoff, burst abort, exit-code mapping (**0/75/78/1** — ADR-010/011), the **orphaned-transaction journal sweep** (ADR-012), and **`.env.prod`-based prod config** (ADR-014/D1 — launcher exports `INSTALL_DIR/.env` staged from repo `.env.prod`, **prod=9797**/dev=8079 always distinct and simultaneously live); **`/livez` + `/readyz`** probes with readiness as a cached composite (liveness-only restarts; readiness never restarts; boot-time PG outage exits 75 with budget-exempt capped backoff); **full-payload release trios (~55 MB each)** under `releases/` with per-release **`manifest.json`**, an atomic `current` symlink flip, and a JSON upgrade journal; **health-gated `make promote`** with a 10-minute rollback window, 300s soak, 10-min cooldown, **max 3 rollbacks/24h (D2 APPROVED)** — rollback gated on the previous release's manifest `rollback_safe`, halting for a human rather than blind-flipping; **drain** via a dedicated API-surface `draining` flag (503 on public entry points) + project master pause + **pre-drain work-ID snapshot census** (ADR-013) bounded at 120s, no per-instance cascades; **migration guard** via a new `daemon_meta` table, exit-78 refusal on unsafe downgrade, and health-gated contract phases for destructive drops; **`ensure-latest` demoted to explicit-VERSION staging (D3 APPROVED)**; a **strictly post-hoc LLM observer** on `system_background_queue` that doubles as the watchdog-watcher; and **agent-facing upgrade tooling (ADR-015)** — `system_upgrade` (two-factor human-gated) + `release_info` (read-only) in a `system_upgrade` tool category exposed to ari/jober only via `tools.allow`, default-deny elsewhere. Net effect: the daemon never stays down after a crash (including through a PG outage at boot), upgrades are one command — or one confirmed chat request — with manifest-gated automatic rollback in seconds, rollback is safe by construction for additive releases, and dev+prod coexist structurally for live e2e. Total effort ~2.5–3.5 engineer-weeks across 7 independently shippable phases, Phase 3 shipping drain-free.

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

### Axis 6 — Prod config / port mechanism (ADR-014 + D1)

| Approach | Complexity | Maintainability | Risk | Recommendation |
|---|---|---|---|---|
| **A: `.env.prod` → `INSTALL_DIR/.env`, launcher exports (PORT=9797)** | Low (pre-existing `Makefile:183-186` copy + launcher export) | High (one canonical prod env source; env precedence verified at `run_app.py:29-31`) | Low (structural dev/prod separation; separate dirs + env files + ports) | ✅ **Adopted (ADR-014, user decision; port finalized to 9797 by D1)** |
| B: fix the Makefile config.yaml sed | Low | Med (sed couples install to config-format details — already silently broken once) | Med (format drift breaks it silently again) | Rejected — superseded |
| C: per-install `config.yaml` edit | Med | Low (drifts from repo config; merge pain) | Med | Rejected |

### Axis 7 — Agent-facing upgrade exposure (ADR-015)

| Approach | Complexity | Maintainability | Risk | Recommendation |
|---|---|---|---|---|
| **A: internal tools, `system_upgrade` category, `tools.allow` scoping** | Low-Med (one tools module + two meta.json edits) | High (job-queue-tools precedent; default-deny covers all other agents structurally) | Low-Med (two-factor human gate; R10 residual bounded by single-host trust) | ✅ **Adopted (ADR-015)** — Phase 7; `release_info` splittable to Phase 2 |
| B: MCP server wrapping the pipeline | Med-High (server process + MCP registration + surface expansion semantics) | Med (PM v0.10.4 shows the category/MCP expansion failure mode) | Med (tool-surface regressions harder to gate) | Rejected |
| C: bash-only (`make promote` via bash tool) | Low | Low (no gating, no structured progress) | High (bash access is a bigger hammer than a gated tool; human-trigger unenforceable) | Rejected |

---

## Trade-offs that drove the recommendation

1. **Launcher-owned policy over supervisor-owned policy** — identical crash-loop behavior on macOS/Linux and a 1:1 k8s mapping later.
2. **Full trios + manifest over bare binaries** — ~165 MB disk vs eliminating the version-skew rollback bug; the manifest is the cheap keystone that makes early-phase rollback gating implementable (M5).
3. **Drain-as-courtesy framing** (reviewer-endorsed strongest design move) — because checkpoints + job locks + StaleTaskRecovery make a hard stop recoverable, the bounded drain can timeout-proceed, Phase 3 can ship drain-free (M3), and post-snapshot external arrivals are an accepted interrupt (M4/ADR-013) rather than a drain blocker.
4. **Exit-75 budget exemption + uptime reset (ADR-011)** — closes the one true liveness hole (M1): transient PG outage at boot can no longer cascade into permanent-down.
5. **Launcher journal sweep (ADR-012)** — closes the second true safety gap (M2): every orphaned promote converges within one rollback window, at a layer below the daemon.
6. **Contract-phase gating (R1) is load-bearing** — without it, auto-rollback across a column-dropping release points a binary at a schema it cannot run; manifest + `daemon_meta` enforce refusal in two layers.
7. **`.env.prod` over sed (ADR-014/D1)** — the user-confirmed pre-existing mechanism; config separation becomes topological (separate dirs/env files/ports 8079/9797) rather than procedural.
8. **Internal tools over MCP/bash for agent exposure (ADR-015)** — `tools.allow` default-deny gives the scoping for free, the two-factor gate makes human-trigger enforceable, and the deterministic gate — not the LLM — makes every go/rollback decision.

## Risks

- 🔴 **R1** — Destructive boot-time drops (`manager.py:478-500`) make rollback unsafe across drop-releases **today**. Resolution: manifest `rollback_safe` block (Phase 2/3) + contract-phase gate (Phase 4).
- 🔴 **R2** — `pause_writes` reuse would starve internal-session writes (`manager.py:368-371`). Resolution: dedicated `draining` flag; verify `WriteGuardSession` semantics before Phase 4.
- 🟡 R3 launchd env (absolute paths, no TTY, canonical env = `INSTALL_DIR/.env` from `.env.prod`, PORT=9797, never inside a release dir) · R4 drain × Task↔JobItem gap (no cascades) · R5 `pg_dump` cost (timeout + skip) · **R9 external-adapter arrivals post-snapshot are interrupted, not drained** (accepted under courtesy framing) · **R10 human-trigger gate circumvention via fabricated `user_confirmed`** (mitigated by server-side user-originated marker; residual bounded by single-host trust model).
- 🟢 R6 macOS quarantine xattr · **R7 `ensure-latest` reproducibility — RESOLVED (D3 APPROVED: explicit VERSION, no auto pull)** · R8 single-host shared fate (out of scope, stated).

## Confidence

**High, post-review + all user decisions final.** Council produced two independent ground-truth corrections to four stated facts (C1–C4); the reviewer independently **confirmed all codebase claims** (trio size corrected ~55 MB) and validated requirements coverage as complete; the amendment passes landed both "true safety gap" fixes (M1, M2) plus six more majors; D1/D2/D3 are settled; ADR-015's registration points were verified against `agents/ari/meta.json`, `agents/jober/meta.json`, and `daemon/tools/instance.py`. The recommendation flips only if Linux deploy lands before Phase 2 (supervisor configs swap; no architecture change) — the parameter space is now fully decided.

## Gaps (corrected ledger — m8)

The original "Gaps: None" was an **over-claim**: the review found 8 major gaps in plan quality (M1–M8), all now incorporated:

- **M1** — boot burst-abort permanent-down → **closed** (ADR-011: exit 75 + budget reset + matrix row).
- **M2** — orphaned promote txn → **closed** (ADR-012: launcher journal sweep + matrix row).
- **M3** — phase-ordering contradiction → **closed** (Phase 3 ships drain-free; §5 note).
- **M4** — adapter drain bypass → **closed** (ADR-013: snapshot census + R9 accepted residual).
- **M5** — unimplementable rollback-refusal rule → **closed** (release manifest from Phase 2).
- **M6** — broken port sed → **closed via ADR-014/D1** (`.env.prod` is the mechanism; PORT=9797; sed + `PROD_PORT` retired as legacy).
- **M7** — nonexistent heartbeat signal → **closed** (`Task.last_heartbeat_at` max-age + Phase-1 fallback flagged in §10).
- **M8** — req #3 mapping unstated → **stated** (§4.2: `/livez` → backoff+burst; `/readyz` → degrade/rollback, never restart).

**Remaining genuinely unverified items** (see `plan-overview.md` §10): PM-bug spawn site; uvicorn SIGTERM bound behavior; `Task.last_heartbeat_at` stamping cadence; `WriteGuardSession` internal-session semantics; ADR-015 trigger-marker plumbing (OQ9). Each has a named phase and a fallback — none blocks planning or the in-progress Phase 1.

## Decisions Pending (user)

**None.** All three original decisions are FINAL/APPROVED:

- **D1 — prod port = 9797 (FINAL; supersedes ADR-014's initial 8088), dev = 8079, `.env.prod` mechanism, simultaneous coexistence.**
- **D2 — max auto-rollbacks = 3/24h then halt-for-human (APPROVED).**
- **D3 — `ensure-latest` demoted to explicit-VERSION staging, no auto `git pull`; `make build` keeps it for interactive dev (APPROVED).**

Remaining open items are non-blocking design details, not decisions: OQ3 notify channel, OQ4 capability-probe failure policy, OQ7 retire `ensemble-prod-recover`, OQ9 ADR-015 trigger-marker mechanics (session attribute recommended; sign-off at Phase 7 kickoff).
