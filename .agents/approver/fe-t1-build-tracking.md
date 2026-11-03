# Approver Tracking — fe-t1-build (FE-only T1 metrics dashboard, wi-32a99618)

Plan set: `.agents/shared/planning/metrics-unification/fe-t1-build/`
Branch under review: `feature/fe-metrics-dashboard` @ `4f775fa10`
Worktree: `/home/nea/ensemble-src-wt-fe-metrics-dashboard` (read-only for the approver)
Verdict artifact: `.agents/shared/planning/metrics-unification/fe-t1-build/approval.md`

---

## Iteration 001 — 2026-11-03 — REJECTED

Fresh-eyes independent review (no prior-iteration context; `plan-review.md` and
`architecture-review.md` deliberately not read to avoid anchoring).

**Requirements 1–8: all DELIVERED.** D-1/D-2/D-3 respected. FE-only, staging-only,
no-live-contact and the routed-out register (14/14 with owners, including all five
caller-named items) all hold. Exit criteria are non-vacuous by construction
(planted-violation legs, warning set-difference, no FE-side coverage arithmetic).

**Blocking issues (3):**

| # | Issue | Discharge |
|---|---|---|
| B1 | The bearer-token mint (`python -m daemon.auth.cli issue-token`, Ph 9.3) is the lane's only DB WRITE and is not covered by the P1–P9 fence. `daemon/auth/cli.py:117-160 resolve_engine()` resolves via `EnsembleConfig.load_or_create(ENSEMBLE_DATA_DIR｜DATA_DIR｜./data)` — the file pin wins, else `PostgresConfig` hard-codes `localhost:5432 / ensemble / ensemble / ""`, byte-identical to the LIVE install's `data/ensemble.json`. `POSTGRES_*` is never consulted. A mint without the scratch `ENSEMBLE_DATA_DIR` writes to the LIVE database, and the lane's live fence (symlink + `state.json` + uptime + "0 daemon requests") cannot detect a DB write | Bind the mint to the scratch data dir + scrub; re-assert the pre-seeded `ensemble.json` by JSON path for the CLI's own resolution; assert the resolved DSN is `ensemble_dev@127.0.0.1` before the mint and prove the row landed in `ensemble_dev` after it; state that the mint is a DB write |
| B2 | Ph 9.3's `Depends On: 9.2` contradicts 9.2's boot step (5) and the plan's own requirement that the `ENSEMBLE_AUTH_*` exports precede the boot (auth has no live toggle). Executed per the graph, the daemon boots auth-OFF ⇒ L1 sees 503, L1b cannot show 401-vs-503, and 9.7a's captures run against a dark door. (`9.11` also depends on itself) | Encode the order: auth-env export depends on 9.1 and 9.2 depends on it (or split 9.2 into export/boot); fix the 9.11 self-dependency |
| B3 | `bundle-baseline.measure.js` identifies the route chunks by content markers (`'metrics-empty-panel'` :35, `'anomaly-marker'` :53) that the lane itself invalidates (Ph 7 retires `/resources`; Ph 8 rewrites the empty state). Ph 9's exit criterion and E-1 line still list "the two route chunks, the route fetch set" as Phase-9-frozen figures, and §0.2's G5 gates on the resources chunk. Consequence: the re-derivation throws or mis-resolves, and B4 — the guard carrying the D-1 lazy-chunk guarantee — loses its baseline | Identify the chunk by a key the lane controls and cannot invalidate (router lazy-import path via the build's chunk map, or a dedicated exported const); assert exactly one chunk resolves; re-derive G5 at Ph 9 or drop G5 as a gate |

**Warnings (5):** W1 P8b names no mechanism (no endpoint exposes the DB identity —
only `POST /api/database/switch`); name the daemon's startup log line read-back and
"no new endpoint". W2 the mini role has no T1 page surface (R-2) — capability-retained
only. W3 requirement 3's baseline is scoped to the central tabs; tab 1's toggle is
disabled with a stated reason — say it once in the close-out narrative. W4 state
"registration-only" once next to the manifest as "family binding + metric→unit
vocabulary entry (both FE-local typed tables)". W5 `9.11` self-dependency (folded into B2).

**Independently verified as sound:** the data-bearing route is feasible in code
(`daemon/auth/cli.py:88`; `daemon/auth/scopes.py:15/:76/:86`;
`daemon/routers/metrics.py:56-63`; `daemon/middleware/auth.py:358-383`, `:440-447`;
`daemon/config.py:6867-6869`); the 16-family / 10-live / 6-dark census is real
(`daemon/services/metrics_query/wiring/registry.py`, `adjudication.py:51-90`); the
plan's file anchors are real (`app.routes.ts:74`, `app.ts:580/:597`,
`charts-tab.component.ts:195`, `metrics.component.ts:84`,
`families-tab.component.ts:210`, `metrics_fe_dual_side_test.sh:275-286`,
`metrics.bindings.pins.spec.ts:94`, `e2e/metrics-gate.spec.ts:277/:298`); the
baseline's provenance holds (`git diff 7961bcb21..HEAD -- frontend/ daemon/ agents/
scripts/ test/` is empty).

**Cross-check:** an independent cold-context `plan-approval` worker
(`82404cdb-8458-4a83-a5ba-00f1286e3a35`) was dispatched in parallel; material
findings will be appended to `approval.md` as an addendum (a REJECTED verdict can
only be strengthened, not reversed).

**Next iteration:** 002 — fold round against B1/B2/B3 (+W1–W5), then re-review.
