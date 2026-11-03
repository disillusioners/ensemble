# Phase 9: Verification, bundle guard, packs, and close-out

## Objective

Prove the lane, on a **fenced dev daemon**, and hand over cleanly: the pixel
list captured, the bundle guard measured, both FE packs green, the routed-out
register closed, and the operator auth recipe handed over (never executed).

**This phase is where the lane proves its claims. It does not "make it
work"** — a failure here is a finding to be reported, not a thing to paper
over.

## Scope note

- The dev daemon runs on `127.0.0.1:8079`. **Never** `:9797`. **Never** the live
  daemon. Live config is out of bounds (D-2).
- Every capture is GET-only. The API census is asserted **0 non-GET**.

## Tasks

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 9.1 | Run the **pre-flight fence** (P1–P7 in `plan-overview.md` §6.1) and record every assertion's result in the lane evidence: the `.env` dev fence; `POSTGRES_DB != ensemble_prod`; the untracked `data/ensemble.json` pin (absent, or `database == "postgres"` with the DB name `ensemble_dev`) — it **outranks the env** and is persisted on first boot, so a stale file silently pins every later boot from that tree; a scratch `ENSEMBLE_DATA_DIR`; `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **pinned explicitly** (never `env -u`); the per-variable env scrub (never `env -u POSTGRES_*` — a silent no-op); the dev port | Phase 8 | The evidence carries all 7 assertions with their observed values; any failure ⇒ **STOP** and report (never "proceed and watch") |
| 9.2 | Boot the **dev daemon** on `127.0.0.1:8079` with the scrubbed env + the scratch data dir; assert `/livez` 200 and that the boot banner names the expected version; assert nothing in the lane's process tree listens on 9797 | 9.1 | The livez output and the banner are recorded; the port assertion is recorded |
| 9.3 | **Leg L1 (data-bearing).** Enable auth **in the DEV config only**: `client_id` + `client_secret` + `issuer` (https) + non-empty `allowed_origins` — required, else the daemon refuses to boot with exit 78 (`daemon/config.py:3930-3965`, `:4009`). The bearer lane **never contacts the IdP** (the middleware verifies a static bearer locally — `daemon/middleware/auth.py:358-377`, `_verify_bearer` → `lookup_bearer` `:440-447`; no OIDC discovery fetch at boot), so placeholder IdP config values satisfy the validator. **Disclosure to be recorded:** the issuer/discovery URL is a placeholder and the OIDC login flow is not exercised. Mint the operator token against the DEV DB: `python -m daemon.auth.cli issue-token --label fe-t1-operator --scopes "maintenance:read"` (the door accepts `maintenance:read` OR `metrics:read` — `daemon/routers/metrics.py:57-63`; the plaintext prints ONCE and never enters a repo file). The browser presents it via the Playwright context's `extraHTTPHeaders: { Authorization: 'Bearer <token>' }` — **no production FE change** | 9.2 | The door answers 200 on `/api/metrics/families`, `/api/metrics/snapshot?family=F9` and `/api/metrics/series?…`; `/api/resources/live` answers 200 with real values; the API census shows **0 non-GET** |
| 9.4 | **Leg L2 (auth-absent).** The same dev env with auth **disabled** ⇒ 503 `AUTH_NOT_CONFIGURED` ⇒ the actionable empty state renders | 9.3 | The 503 body is captured; the rendered state carries the actionable panel with the runbook pointer and **no** Retry |
| 9.5 | **Leg L3 (mixed-unit correctness).** On the dev daemon's resource timeline: the memory panel's axis is labelled in MB, the CPU panel's in %, and the two do not share an axis | 9.3 | The captured pixels + the geometry assertion both confirm it |
| 9.6 | **Capture the full pixel list (PX-1…PX-14)** via the canonical script shape (`research-findings.md` §7: Node Playwright 1.60.0 from `frontend/node_modules`, `chromium --no-sandbox`, `domcontentloaded` (30 s) → best-effort `networkidle` (15 s cap — **never settles**, the SSE stream and the 8 s polls keep it open) → **fixed 3 s settle** → screenshot), against `127.0.0.1:8079`, at 1600×1000 (and 900×1000 for PX-8) | 9.4 | Every PX item has a capture + an assertion; the images are stored outside the repo (the house convention: binary evidence stays out of the repo) and referenced from the lane evidence |
| 9.7 | **Bundle guard (B1–B6).** Measure the **initial-set total, base vs HEAD** (≈0 delta asserted — the initial set is 5,909,018 B against a 6,000,000 B error budget, 89,982 B headroom); prove the **lazy-chunk-only** property (the initial bundle's file list is byte-identical to base; the Chart.js import graph is reachable only from the lazy `/metrics` tree); measure the **new lazy chart chunk's real min + gz bytes** for the tree-shaken controller set — this closes governance item §9-6 ("verify Chart.js v4 size before the budget claim locks"; the ~65 kB figure was an ESTIMATE). Record the budget warnings verbatim as adjudication data (never auto-fixed) | Phase 1 | The evidence carries: the initial-file-list diff (empty), the initial total delta, the measured chunk min+gz, and the verbatim budget warnings |
| 9.8 | **Both FE packs green.** `test/packs/metrics_fe_dual_side_test.sh` (Leg 7 amended to the expected-dependency-set assertion) and `test/packs/fe_static_typecheck_build_test.sh`. The type-check leg runs **through `frontend/`'s local devDependency**: `cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json` — never bare `tsc` / `npx tsc` outside `frontend/` | Phase 1 | Both packs PASS; the Leg-7 assertion is by name; the budget warnings are recorded as data |
| 9.9 | **Prompt-artifact baseline**: `agents/.prompt-artifacts.sha256` verifies **161/161**; the `agents/` tree has **zero** diffs | Phase 8 | The baseline check passes; the `agents/` diff is empty |
| 9.10 | **Close out the routed-out register** (14 items in `plan-overview.md` §7): each item has an owner and a reason; the register is copied into the lane's evidence; and the **operator auth recipe** (`plan-overview.md` §9 — the IdP registration, the env vars, the token mint with `metrics:read`/`maintenance:read`, the **restart implication** ("no live toggle in v1"), the verification curls) is handed over **without being executed**. The lane records **0 live config writes** and **0 live daemon requests** | Phase 9 | The register is 14/14 with owners; the recipe is handed over in the evidence; the live fence held (the live daemon's state.json / symlink / uptime are byte-identical pre/post) |

## Coupling

- **Tight with:** every prior phase (this is the lane's proof).
- **Independent of:** nothing — this phase depends on all.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| The env-poison family reaches the dev daemon (ambient `POSTGRES_DB=ensemble_prod` / `PORT=9797`) | High | 9.1's per-variable scrub + the explicit sweep guard + the scratch data dir + the `data/ensemble.json` assertion + the 9797 port assertion |
| The bearer leg proves impossible in practice (the IdP config gate bites) | Medium | The lane **stops that leg and reports it** — it does NOT weaken the door (D-2 forbids weakening) and does NOT substitute a live-daemon capture |
| The bundle guard is measured but not **compared** | Medium | B1 asserts base vs HEAD; a mere measurement is not a guard |
| The pixel list drifts from what was actually built | Medium | Every PX item names the surface it covers; the evidence maps each capture to its PX id |
| The lane "fixes" something during verification and blurs the phase boundary | Medium | Verification failures are **reported findings**; fixes belong to the owning phase and are re-verified here |

## Exit Criterion

- All 7 pre-flight assertions recorded and passing; the dev daemon answered
  `/livez` 200 on `:8079`; nothing touched `:9797`.
- **Leg L1** proves the door serves data under a scoped operator bearer;
  **Leg L2** proves the actionable `AUTH_NOT_CONFIGURED` state; **Leg L3**
  proves the per-unit axes.
- **PX-1 … PX-14** captured and asserted; the API census shows **0 non-GET**.
- **B1–B6** measured and recorded (the initial delta ≈ 0; the lazy chunk's
  real min+gz; both packs green; 161/161).
- The routed-out register is **14/14** with owners; the operator recipe is
  handed over, **not executed**.
- The live fence held: the live daemon's symlink, state.json and uptime are
  byte-identical before and after the lane.
- The lane's evidence is complete enough that a reviewer can re-derive every
  claim in this plan set without re-running the lane.
