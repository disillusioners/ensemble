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

---

## Iteration 001 addendum — independent cross-check (2026-11-03)

Cold-context `plan-approval` worker `82404cdb-8458-4a83-a5ba-00f1286e3a35` returned
**REJECTED** (4 blocking issues): 2 confirm the approver's (B1 mint fence, B3
measurement tool), 2 are NEW and were independently verified by the approver against
the tree before being folded in.

| # | Issue | Status |
|---|---|---|
| B1 | The mint (the lane's only DB write) is unfenced | **SHARPENED** — `daemon/repositories/factory.py:346-350` resolves host/port/db/user/password as `os.environ` OVER the file's postgres block, so the P4 pre-seed does NOT protect the mint while `POSTGRES_DB` is ambient (`ensemble_prod`, `POSTGRES_HOST=10.44.0.2`); the discharge must therefore scrub the env, not only point `ENSEMBLE_DATA_DIR` at the scratch dir. Narrower `--scopes "metrics:read"` recommended for the dev leg |
| B2 | Phase 9's task graph contradicts the auth-env-before-boot ordering | unchanged |
| B3 | The Phase-9 route-chunk re-derivation is unstable under the lane's own changes | unchanged (cross-check: the tool throws → B1/B2/B3/B4 all become unmeasurable) |
| **B4** | **NEW** — §9's D-2 operator recipe omits `ENSEMBLE_AUTH_ISSUER`; `AuthConfig.issuer` has no default and is not derived from `discovery_url` (`daemon/config.py:3738-3748`, `:3953-3954`) ⇒ the recipe's daemon refuses to boot (exit 78). §6.1 states the requirement correctly; §9 contradicts it | **folded as a blocker** |
| **B5** | **NEW** — Phase 5's exit ("the old directory is gone") contradicts Phase 7's 7.4 (which deletes the page shell; the route still lazily imports it at `app.routes.ts:74`) ⇒ the directory cannot be gone at Phase 5's freeze, and deleting it there breaks the build | **folded as a blocker** |

Warnings folded: W6 (the new import pin must be statement-anchored — a substring
scan trips on 1.7's own doc comment and reddens the Phase-1 freeze), W7 (per-series
min-max normalization never explicitly removed), W8 (task 6.6 targets the page shell's
subtitle, a file Phase 7 deletes), W9 (no interpreter pinned; the worktree has no
`.venv` and bare `python` does not exist), W10 (the dev leg writes into the shared
`ensemble_dev` DB with no DROP teardown), W11 (G11 juxtaposes the LIVE/DARK split
with the full/PARTIAL adjudication split — an implementer could hide tabs 5 and 6,
whose families are live).

**Blocking set after the addendum: B1–B5 (5).** Verdict unchanged: **REJECTED**.
Corroborated by the cross-check: citation discipline, C-4's code-path analysis, the
bundle-guard correction and the C-1 lazy-sibling insight, exit-criteria non-vacuity,
FE-only discipline across all 107 task rows, the six zero-dep governance sites, and
the task-graph shape (2 disclosed forward edges + the 9.11 self-loop; no hidden cycle).

**Next:** fold B1–B5 + W1–W11, then iteration 002 re-review.
