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
| 9.1 | Run the **pre-flight fence** (P1–P9 in `plan-overview.md` §6.1) and record every assertion's result in the lane evidence. **The corrections (C-4, W-7, W-8, W-9, A-6, LEADER RULING 3):** (a) the port is **`PORT`**, not `DAEMON_PORT` (which appears **nowhere** under `daemon/`, while `config.yaml:112-113` reads `port: ${PORT:-8088}` ⇒ the default is **8088**) — and it must be re-exported **AFTER** the `.env` source and **unconditionally**; (b) the scrub follows the house **family rule** — prefix `POSTGRES_*` **and** `PG*` (PGPASSWORD is a known ambient vector), plus `BLUEPRINT_*` and suffix `*_API_KEY` — scrubbed **per variable** (never `env -u POSTGRES_*`, a silent no-op); (c) **P3** is pinned to the **JSON path** (`jq -e '.database == "postgres" and .postgres.db == "ensemble_dev"'`) — never a literal `ensemble_prod` substring (the live file's `$.postgres.db` is `"ensemble"`, which a substring checker passes); (d) **P4**'s pre-seed carries the **full** `postgres` block with the **exact** contents, and **P3 is re-run after P4**; (e) **P8** splits into **P8a** (`config.yaml`'s effective **port** stanza — there is **no** database stanza: `grep -i "postgres\|database" config.yaml` = 0 hits) and **P8b** (the running daemon's **actual database** read back); (f) `ENSEMBLE_SELF_ENV` triggers **no** `.env` load (it is read only by `daemon/tools/upgrade_tools.py:253/:576-611`) | Phase 8 | The evidence carries all 9 assertions with their observed values; any failure ⇒ **STOP** and report (never "proceed and watch") |
| 9.2 | **Boot the dev daemon — C-4's ORDERED sequence (the quiet-failure class).** (1) scrub per variable (P6) → (2) **source the WORKTREE's `.env`** into the daemon's environment (`set -a; . ./.env; set +a` — the house `dev.sh:70-76` idiom) → (3) assert the **loaded** `POSTGRES_DB == ensemble_dev` ≠ `ensemble_prod` (P2) → (4) **re-export `PORT=8079` unconditionally, AFTER the source** (LEADER RULING 3) → (5) boot **`python -m daemon`** (the `__main__` path, so the preflight and the boot sweep run — **not** `dev.sh`, whose `:155` `uvicorn --reload` **bypasses** them) → (6) assert the **effective port from `/livez`** (P7), the daemon's **actual database == ensemble_dev** (P8b), and the `ensemble.json` in `$ENSEMBLE_DATA_DIR` (P3 re-run). **C-4 was closed on CODE-PATH evidence, not on an executed boot** — the three cheap checks that would falsify it are named as **implementation-time assertions**: (i) `env \| grep POSTGRES` ⇒ expect **nothing** before the `.env` load; (ii) the boot log line `No PostgreSQL ENV vars detected, defaulting to sqlite` ⇒ must **not** appear; (iii) `cat $ENSEMBLE_DATA_DIR/ensemble.json` ⇒ `.database == "postgres"` and `.postgres.db == "ensemble_dev"` | 9.1 | The livez output, the boot banner and the **observed effective port** are recorded; the three falsification checks are recorded with their actual outputs; the daemon's DB read-back says `ensemble_dev` |
| 9.3 | **Leg L1 (data-bearing).** The four `ENSEMBLE_AUTH_*` **env vars** are exported **BEFORE the boot** (W-13: 9.3's exports precede 9.2's boot — auth has **no live toggle**, `config.yaml:133`, so exporting after a boot would leave the daemon serving the auth-OFF posture). Auth ON requires `client_id` + `client_secret` + `issuer` (https) + non-empty `allowed_origins` — else the daemon refuses to boot with exit 78 (`daemon/config.py:3930-3965`, `:4009`). The bearer lane **never contacts the IdP** (the middleware verifies a static bearer locally — `daemon/middleware/auth.py:358-377`, `_verify_bearer` → `lookup_bearer` `:440-447`; no OIDC discovery fetch at boot), so placeholder IdP config values satisfy the validator. **Disclosure to be recorded:** the issuer/discovery URL is a placeholder and the OIDC login flow is NOT exercised. Mint: `python -m daemon.auth.cli issue-token --label fe-t1-operator --scopes "maintenance:read"` (the door accepts `maintenance:read` OR `metrics:read` — `daemon/routers/metrics.py:57-63`; the plaintext prints ONCE and never enters a repo file). The browser presents it via the Playwright context's `extraHTTPHeaders: { Authorization: 'Bearer <token>' }` — **no production FE change**. **W-12: the mechanism is the ENV VARS ONLY** — `daemon/config.py:6867-6869` overrides the yaml's `enabled: false` with `ENSEMBLE_AUTH_ENABLED` — so **no tracked file is edited** (`config.yaml` and the worktree `.env` have **zero diffs** after this task). **W-11: the non-emptiness thresholds** — `/api/metrics/families` must return **≥ 1** registered family **and** **≥ 1** of the 7 bound families; `/api/metrics/snapshot?family=F9` must return **≥ 1** series; `/api/resources/live` must return a **non-zero** `daemon_rss_mb`; **a 200-with-empty-catalog FAILS this leg** | 9.2 | The door answers 200 **with the observed non-empty counts recorded**; the token plaintext never entered a repo file; `config.yaml` and the worktree `.env` have zero diffs |
| 9.4 | **Leg L1b (auth required, no bearer — AM-4).** Auth ON, **no** credential ⇒ **401** ⇒ the **sign-in panel** (`auth_required`), **not** `unexpected` and **not** a dead Retry | 9.3 | The 401 body is captured; the rendered state is the sign-in panel with **no** Retry button |
| 9.5 | **Leg L2 (auth-absent) + Leg L4 (AM-1).** Restart the dev daemon with auth **disabled** ⇒ 503 `AUTH_NOT_CONFIGURED` ⇒ the actionable empty state renders **on the central surfaces**, while **tab 1 renders real data** (the resources door is fail-open). **L4 is the AM-1 capture: tab 1 lit + central tabs actionable** | 9.4 | The 503 body is captured; the central surfaces carry the actionable panel with the runbook pointer and **no** Retry; **the tab-1 body shows its live strip + timeline with real values and NO auth panel** |
| 9.6 | **Leg L3 (mixed-unit correctness).** On the dev daemon's resource timeline: the memory panel's axis is labelled in MB, the CPU panel's in %, the counts panel's in counts, and the three do not share an axis | 9.3 | The captured pixels + the geometry assertion both confirm it |
| 9.7a | **Capture the auth-ON pixel list: PX-1…PX-7 and PX-9…PX-14.** `Depends On: 9.3` (**W-13** — the capture depends on the auth-ON boot, **not** on the auth-OFF restart 9.5, which would have forced the captures against a dark door). Mechanism: the canonical script shape (`research-findings.md` §7 — Node Playwright 1.60.0 from `frontend/node_modules`, `chromium --no-sandbox`, `domcontentloaded` (30 s) → best-effort `networkidle` (15 s cap — **never settles**, the SSE stream and the 8 s polls keep it open) → **fixed 3 s settle** → screenshot), against `127.0.0.1:8079`, at 1600×1000 (and 900×1000 for PX-8) | 9.3, 9.6 | Every PX item has a capture + an assertion; the images are stored outside the repo (the house convention: binary evidence stays out of the repo) and referenced from the lane evidence |
| 9.7b | **Capture the auth-OFF pixel list: PX-8** (the actionable empty state). `Depends On: 9.5` | 9.5 | PX-8's capture + assertion (one actionable panel, the runbook pointer, no Retry, no duplicate banner, the posture chip — not the global dot — is not green, no 900 px collision, **and tab 1 still shows live data**) |
| 9.8 | **Bundle guard (B1–B8).** (a) **numeric** `\|Δ initial-total\| ≤ 2 kB` against the **named baseline artifact** `bundle-baseline.json` (**5,909,018 B** — A-8: a number duplicated only in plan prose is not a baseline); (b) **no `chart.js` marker in ANY initial chunk**; (c) the **import-shape + import-site** assertion — **0** static `from 'chart.js'` sites anywhere; every site inside `src/app/pages/metrics/**`; the wrapper's form is the **dynamic** `await import('chart.js')`; (d) **B4** — the **new lazy chart chunk's real min + gz ≤ 65,536 B (64 KiB)** **AND** the **`/metrics` route FETCH SET grows ≤ 65,536 B** vs the **496,338 B** baseline (G4b) — this is the comparison leg that G4's "Phase 9's new-chunk comparison" named but that did not exist, and the route fetch set (not the route chunk) is the measurement that catches a static-shape leak; (e) the per-component **`anyComponentStyle`** figures for the new components (**< 8 kB** each — the 24 kB cap is a hard build error); (f) **B6 as a SET DIFFERENCE** against the Phase-1-captured baseline set (the **9** paths, captured mechanically from the build log) — **0 added, 0 removed**; a **count ceiling is FORBIDDEN** (it can be loosened and silently lose detection); (g) **B8** — the guard read the named artifact, not plan prose | 9.7a, 9.7b | The evidence carries: the initial-total delta, the initial-chunk scan, the import-shape + import-site results, the measured chunk min+gz, the route fetch set's growth, the per-component style figures, the warning **set difference**, and the verbatim warnings |
| 9.9 | **Both FE packs green.** `test/packs/metrics_fe_dual_side_test.sh` (Leg 7 amended to the expected-dependency-set assertion) and `test/packs/fe_static_typecheck_build_test.sh`. The type-check leg runs **through `frontend/`'s local devDependency**: `cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json` — never bare `tsc` / `npx tsc` outside `frontend/` | 9.8 | Both packs PASS; the Leg-7 assertion is by name; the budget warnings are recorded as data |
| 9.10 | **Prompt-artifact baseline**: `agents/.prompt-artifacts.sha256` verifies **161/161**; the `agents/` tree has **zero** diffs | Phase 8 | The baseline check passes; the `agents/` diff is empty |
| 9.11 | **Close out the routed-out register** (14 items in `plan-overview.md` §7): each item has an owner and a reason; the register is copied into the lane's evidence; and the **operator auth recipe** (`plan-overview.md` §9 — the IdP registration, the env vars, the token mint with `metrics:read`/`maintenance:read`, the **restart implication** ("no live toggle in v1"), the verification curls) is handed over **without being executed**. The lane records **0 live config writes** and **0 live daemon requests**. **The live-fence wording is corrected (AM-29):** the assertion is "the live **symlink target + `state.json` are byte-identical** pre/post and the live **uptime is MONOTONE (no restart)**" — uptime is monotone by nature and can never be "byte-identical". **The E-2 release narrative (§9.1) and the model-mapping disclosure (§0.4) ride the staged-release disclosures** | 9.11 | The register is 14/14 with owners; the recipe is handed over in the evidence; the live fence held (the live symlink + state.json byte-identical, uptime monotone, 0 requests) |

## Coupling

- **Tight with:** every prior phase (this is the lane's proof).
- **Independent of:** nothing — this phase depends on all.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| The env-poison family reaches the dev daemon (ambient `POSTGRES_DB=ensemble_prod` / `PORT=9797`) | High | 9.1's **family-rule** scrub, the explicit sweep guard, the scratch data dir, the JSON-path-pinned `ensemble.json` assertion, P8a's port stanza + P8b's DB read-back, and the effective-port read-back. **The live install's `.env` carries `PORT=9797` — which is why the `.env` source and the `PORT` re-export have a fixed ORDER (9.2): source first, export after, unconditionally** (LEADER RULING 3) |
| The daemon binds **8088** (the `PORT` default) while the steps target 8079 | High | 9.2's named boot command (`python -m daemon`, not `dev.sh`), `PORT=8079` re-exported **after** the source, and the **effective port asserted from the running daemon** |
| The daemon runs against the **wrong database** (the sqlite fallback, **persisted** into `ensemble.json`, which then outranks the env on every later boot) | High — a **quiet** failure: `/api/metrics/*` still answers 200 with an empty catalog | 9.2's ordered sequence (the `.env` source is the load-bearing step) + the three implementation-time falsification checks + 9.3's **non-emptiness thresholds** (a 200-with-empty-catalog **fails** L1) + P8b's DB read-back |
| The data-bearing captures are attempted against an auth-OFF daemon | High | 9.7a's `Depends On: 9.3` (auth ON) and 9.7b's `Depends On: 9.5` (auth OFF) — the split is in the **task graph**, not only in the prose (W-13) |
| Auth is enabled **after** the boot (no live toggle) | High — the daemon would keep serving the auth-OFF posture | 9.3's env exports **precede** 9.2's boot (the task graph is ordered accordingly) |
| The bundle guard is measured but not **compared** | Medium | B1 asserts base vs the named artifact; a mere measurement is not a guard |
| A **lazy** (or static-inside-a-lazy-tree) Chart.js leak slips through because no budget counts lazy chunks | High | B1/B2/B3's co-located numeric + scan + shape/site assertions (Phases 1, 4, 5, 9) and B4's route-fetch-set growth bound |
| The pixel list drifts from what was actually built | Medium | Every PX item names the surface it covers; the evidence maps each capture to its PX id |
| The lane "fixes" something during verification and blurs the phase boundary | Medium | Verification failures are **reported findings**; fixes belong to the owning phase and are re-verified here |

## Exit Criterion

- All 9 pre-flight assertions recorded and passing; the dev daemon answered
  `/livez` 200 on the **observed effective port 8079**; nothing touched
  `:9797`; the daemon's actual database read back as `ensemble_dev`; the three
  C-4 falsification checks recorded with their actual outputs.
- **Leg L1** proves the door serves data under a scoped operator bearer **with
  the observed non-empty counts** (≥ 1 registered family, ≥ 1 of the 7 bound
  families, ≥ 1 F9 snapshot series, a non-zero `daemon_rss_mb`); **L1b** proves
  the 401 sign-in panel; **L2/L4** prove the actionable `AUTH_NOT_CONFIGURED`
  state on the central surfaces **and tab 1 staying lit**; **L3** proves the
  per-unit axes.
- **PX-1 … PX-14** captured in the correct posture order (9.7a auth ON, 9.7b
  auth OFF) and asserted; the API census shows **0 non-GET**.
- **B1–B8** measured and recorded: the initial-total delta vs 5,909,018 B
  (≤ 2 kB), the initial-chunk scan, the import-shape + import-site results,
  the new chunk's min+gz (≤ 64 KiB), the route fetch set's growth (≤ 64 KiB vs
  the 496,338 B baseline), the per-component `anyComponentStyle` figures, the
  warning **set difference** (0 added, 0 removed against the 9-path baseline),
  both packs green, 161/161, and the guard read the named artifact.
- **No tracked file was edited by the auth enablement** — `config.yaml` and the
  worktree `.env` have **zero diffs** (W-12).
- The routed-out register is **14/14** with owners; the operator recipe is
  handed over, **not executed**; the E-2 release narrative and the
  model-mapping disclosure ride the staged-release disclosures.
- The live fence held: the live symlink target + `state.json` are
  **byte-identical** pre/post and the live **uptime is monotone (no restart)**.
- The lane's evidence is complete enough that a reviewer can re-derive every
  claim in this plan set without re-running the lane.
- **E-1 re-derivation:** every MEASURED figure this phase gates on is listed
  in `plan-overview.md` §0.2's gated-figures ledger **with its exact command and
  output** — the initial-total baseline, the lazy-chunk census, the two route
  chunks, the route fetch set, the lazy byte total, the `anyComponentStyle`
  figures (G8) and the warning set (G10) are **all re-derived here at this
  phase's freeze**; G1–G7 and G10 were additionally re-derived at lane base by
  this fold (§14 of `research-findings.md` + `bundle-baseline.json`).
