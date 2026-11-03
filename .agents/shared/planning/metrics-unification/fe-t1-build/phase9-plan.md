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
| 9.1 | Run the **pre-flight fence** (P1–P8 in `plan-overview.md` §6.1) and record every assertion's result in the lane evidence. **Corrections folded (AM-5, AM-29):** (a) the port is **`PORT`**, not `DAEMON_PORT` — `DAEMON_PORT` appears **nowhere** under `daemon/`, while `config.yaml:112-113` reads `port: ${PORT:-8088}` (default **8088**), so `PORT=8079` is set **after** the scrub; (b) the scrub follows the house **family rule** — prefix `POSTGRES_*` **and** `PG*` (PGPASSWORD is a known ambient vector) plus `BLUEPRINT_*` and suffix `*_API_KEY` — scrubbed **per variable** (never `env -u POSTGRES_*`, a silent no-op); (c) **P8** is added: assert `config.yaml`'s effective port/database stanza **and read the dev daemon's actual database back from the running daemon** (not from a file); (d) the `data/ensemble.json` pin is asserted (absent, or `database == "postgres"` with the DB name `ensemble_dev`) — it **outranks the env** and is persisted on first boot, so a stale file silently pins every later boot from that tree; (e) `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` is pinned **explicitly** (never `env -u`); (f) a scratch `ENSEMBLE_DATA_DIR` | Phase 8 | The evidence carries all 8 assertions with their observed values; any failure ⇒ **STOP** and report (never "proceed and watch") |
| 9.2 | Boot the **dev daemon** with the scrubbed env + the scratch data dir + **`PORT=8079`**; the boot command is **named explicitly** (AM-5) — `python -m daemon` (the `__main__` path, so the preflight and the boot sweep run) — **not** `dev.sh` (which uses `uvicorn --reload` and **bypasses** the `__main__` preflight/sweep: `dev.sh:155`). Assert `/livez` 200, the boot banner names the expected version, and the **effective port** is asserted **from the running daemon** (the boot log / the `/livez` body), not from an env var | 9.1 | The livez output, the banner and the **observed effective port** are recorded; the boot command is recorded verbatim |
| 9.3 | **Leg L1 (data-bearing).** Enable auth **in the DEV config only**: `client_id` + `client_secret` + `issuer` (https) + non-empty `allowed_origins` — required, else the daemon refuses to boot with exit 78 (`daemon/config.py:3930-3965`, `:4009`). The bearer lane **never contacts the IdP** (the middleware verifies a static bearer locally — `daemon/middleware/auth.py:358-377`, `_verify_bearer` → `lookup_bearer` `:440-447`; no OIDC discovery fetch at boot), so placeholder IdP config values satisfy the validator. **Disclosure to be recorded:** the issuer/discovery URL is a placeholder and the OIDC login flow is not exercised. Mint the operator token against the DEV DB: `python -m daemon.auth.cli issue-token --label fe-t1-operator --scopes "maintenance:read"` (the door accepts `maintenance:read` OR `metrics:read` — `daemon/routers/metrics.py:57-63`; the plaintext prints ONCE and never enters a repo file). The browser presents it via the Playwright context's `extraHTTPHeaders: { Authorization: 'Bearer <token>' }` — **no production FE change** | 9.2 | The door answers 200 on `/api/metrics/families`, `/api/metrics/snapshot?family=F9` and `/api/metrics/series?…`; `/api/resources/live` answers 200 with real values; the API census shows **0 non-GET** |
| 9.4 | **Leg L1b (auth required, no bearer — AM-4).** Auth ON, **no** credential ⇒ **401** ⇒ the **sign-in panel** (`auth_required`), **not** `unexpected` and **not** a dead Retry | 9.3 | The 401 body is captured; the rendered state is the sign-in panel with **no** Retry button |
| 9.5 | **Leg L2 (auth-absent) + Leg L4 (AM-1).** Restart the dev daemon with auth **disabled** ⇒ 503 `AUTH_NOT_CONFIGURED` ⇒ the actionable empty state renders **on the central surfaces**, while **tab 1 renders real data** (the resources door is fail-open). **L4 is the AM-1 capture: tab 1 lit + central tabs actionable** | 9.4 | The 503 body is captured; the central surfaces carry the actionable panel with the runbook pointer and **no** Retry; **the tab-1 body shows its live strip + timeline with real values and NO auth panel** |
| 9.6 | **Leg L3 (mixed-unit correctness).** On the dev daemon's resource timeline: the memory panel's axis is labelled in MB, the CPU panel's in %, the counts panel's in counts, and the three do not share an axis | 9.3 | The captured pixels + the geometry assertion both confirm it |
| 9.7 | **Capture the full pixel list (PX-1…PX-14)**, **split by auth posture (AM-6):** **9.7a** — PX-1…PX-7 and PX-9…PX-14 with **auth ON** (the data-bearing captures; they need the auth-ON state of 9.3, and nothing re-enables auth after 9.5, so the ORDER is: L1/L1b/L3 → captures → then the auth-OFF restart); **9.7b** — PX-8 with **auth OFF** (after the restart in 9.5). Each restart is followed by a **`/livez` re-assertion**. Mechanism: the canonical script shape (`research-findings.md` §7 — Node Playwright 1.60.0 from `frontend/node_modules`, `chromium --no-sandbox`, `domcontentloaded` (30 s) → best-effort `networkidle` (15 s cap — **never settles**, the SSE stream and the 8 s polls keep it open) → **fixed 3 s settle** → screenshot), against `127.0.0.1:8079`, at 1600×1000 (and 900×1000 for PX-8) | 9.3, 9.5 | Every PX item has a capture + an assertion; the images are stored outside the repo (the house convention: binary evidence stays out of the repo) and referenced from the lane evidence; the two legs are recorded in order with the `/livez` re-assertions |
| 9.8 | **Bundle guard (B1–B7).** (a) **numeric** `\|Δ initial-total\| ≤ 2 kB` against the frozen lane-base baseline **5,909,018 B** (the builder's own "Initial total" basis, 36 chunk files — derived in `research-findings.md` §14); (b) **no `chart.js` marker in ANY initial chunk**; (c) the import-graph assertion (every `chart.js` import site inside `src/app/pages/metrics/**`); (d) the **new lazy chart chunk's real min + gz bytes** — this closes governance item §9-6 ("verify Chart.js v4 size before the budget claim locks"; the ~65 kB figure was an ESTIMATE, never measured); (e) the per-component **`anyComponentStyle`** figures for the new components (AM-28 — the 8 kB warn / 24 kB ERROR cap); (f) the budget warnings recorded verbatim as adjudication data (never auto-fixed). **The corrected mechanism (AM-2):** a **lazy** leak trips **no** budget — the guards are numeric + import-graph, and they were **co-located** in Phases 1 and 4 (the leak window spans Phases 1–8) | Phase 1, Phase 4 | The evidence carries: the initial-total delta, the initial-chunk scan, the import-graph result, the measured chunk min+gz, the per-component style figures, and the verbatim warnings |
| 9.9 | **Both FE packs green.** `test/packs/metrics_fe_dual_side_test.sh` (Leg 7 amended to the expected-dependency-set assertion) and `test/packs/fe_static_typecheck_build_test.sh`. The type-check leg runs **through `frontend/`'s local devDependency**: `cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json` — never bare `tsc` / `npx tsc` outside `frontend/` | Phase 1 | Both packs PASS; the Leg-7 assertion is by name; the budget warnings are recorded as data |
| 9.10 | **Prompt-artifact baseline**: `agents/.prompt-artifacts.sha256` verifies **161/161**; the `agents/` tree has **zero** diffs | Phase 8 | The baseline check passes; the `agents/` diff is empty |
| 9.11 | **Close out the routed-out register** (14 items in `plan-overview.md` §7): each item has an owner and a reason; the register is copied into the lane's evidence; and the **operator auth recipe** (`plan-overview.md` §9 — the IdP registration, the env vars, the token mint with `metrics:read`/`maintenance:read`, the **restart implication** ("no live toggle in v1"), the verification curls) is handed over **without being executed**. The lane records **0 live config writes** and **0 live daemon requests**. **The live-fence wording is corrected (AM-29):** the assertion is "the live **symlink target + `state.json` are byte-identical** pre/post and the live **uptime is MONOTONE (no restart)**" — uptime is monotone by nature and can never be "byte-identical" | Phase 9 | The register is 14/14 with owners; the recipe is handed over in the evidence; the live fence held (the live symlink + state.json byte-identical, uptime monotone, 0 requests) |

## Coupling

- **Tight with:** every prior phase (this is the lane's proof).
- **Independent of:** nothing — this phase depends on all.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| The env-poison family reaches the dev daemon (ambient `POSTGRES_DB=ensemble_prod` / `PORT=9797`) | High | 9.1's **family-rule** scrub (prefix `POSTGRES_*` + `PG*`, `BLUEPRINT_*`, suffix `*_API_KEY`), the explicit sweep guard, the scratch data dir, the `data/ensemble.json` assertion, P8's read-back of the daemon's actual DB, and the effective-port assertion |
| The daemon binds **8088** (the `PORT` default) while the steps target 8079 | High — the whole verification route targets the wrong daemon | 9.2's named boot command (`python -m daemon`, not `dev.sh`), `PORT=8079` set **after** the scrub, and the **effective port asserted from the running daemon** |
| The data-bearing captures are attempted while auth is OFF | High (the captures are impossible) | 9.7's split into **9.7a (auth ON)** and **9.7b (auth OFF)**, with an explicit restart + `/livez` re-assertion between them |
| The bearer leg proves impossible in practice (the IdP config gate bites) | Medium | The lane **stops that leg and reports it** — it does NOT weaken the door (D-2 forbids weakening) and does NOT substitute a live-daemon capture |
| The bundle guard is measured but not **compared** | Medium | B1 asserts base vs the frozen baseline; a mere measurement is not a guard |
| A lazy Chart.js leak slips through because no budget counts lazy chunks | High | The numeric initial-total delta + the initial-chunk marker scan + the import-graph assertion (AM-2's corrected mechanism), co-located in Phases 1 and 4 |
| The pixel list drifts from what was actually built | Medium | Every PX item names the surface it covers; the evidence maps each capture to its PX id |
| The lane "fixes" something during verification and blurs the phase boundary | Medium | Verification failures are **reported findings**; fixes belong to the owning phase and are re-verified here |

## Exit Criterion

- All 8 pre-flight assertions recorded and passing; the dev daemon answered
  `/livez` 200 on the **observed effective port 8079**; nothing touched
  `:9797`; the daemon's actual database read back as `ensemble_dev`.
- **Leg L1** proves the door serves data under a scoped operator bearer;
  **L1b** proves the 401 sign-in panel; **L2/L4** prove the actionable
  `AUTH_NOT_CONFIGURED` state on the central surfaces **and tab 1 staying lit**;
  **L3** proves the per-unit axes.
- **PX-1 … PX-14** captured in the correct posture order (9.7a auth ON, 9.7b
  auth OFF, with `/livez` re-assertions) and asserted; the API census shows
  **0 non-GET**.
- **B1–B7** measured and recorded: the initial-total delta vs 5,909,018 B
  (≤ 2 kB), the initial-chunk scan, the import-graph result, the new lazy
  chunk's real min+gz, the per-component `anyComponentStyle` figures, the
  verbatim warnings, both packs green, 161/161.
- The routed-out register is **14/14** with owners; the operator recipe is
  handed over, **not executed**.
- The live fence held: the live symlink target + `state.json` are
  **byte-identical** pre/post and the live **uptime is monotone (no restart)**.
- The lane's evidence is complete enough that a reviewer can re-derive every
  claim in this plan set without re-running the lane.
- **E-1 re-derivation:** every MEASURED figure this phase gates on is listed
  in `plan-overview.md` §0.2's gated-figures table — the initial-total
  baseline, the lazy-chunk census, the two route-chunk sizes, the lazy byte
  total, the `anyComponentStyle` figures and the warning count are **all
  re-derived here at this phase's freeze** (the first five were additionally
  re-derived at lane base by this fold — §14 of `research-findings.md`).
