# Approval — fe-t1-build (FE-only T1 metrics dashboard, lane wi-32a99618)

- **Verdict: REJECTED** (iteration 001)
- Approver: fresh-eyes independent review, 2026-11-03
- Artifact reviewed: `.agents/shared/planning/metrics-unification/fe-t1-build/`
  (`plan-overview.md`, `phase0-plan.md` … `phase9-plan.md`, `decisions.md`,
  `research-findings.md`, `fold-audit.md`, `bundle-baseline.json`,
  `bundle-baseline.measure.js`) at branch `feature/fe-metrics-dashboard` @ `4f775fa10`
- Worktree read: `/home/nea/ensemble-src-wt-fe-metrics-dashboard` (read-only; the shared
  checkout `/home/nea/ensemble-src` was NOT touched)
- Design source consulted for fidelity: `.agents/shared/planning/metrics-unification/fe-ui-audit-and-resource-refactor.md`
- Deliberately NOT read (avoid anchoring): `plan-review.md`, `architecture-review.md`

## 0. Method

Every requirement below was traced to the phase/task that delivers it, and every
load-bearing code claim was checked against the tree in the lane worktree (not
against the plan's own prose). The plan set is unusually strong: the gated-figures
ledger, the named baseline artifact, the withdrawn false claim about lazy leaks, and
the planted-violation acceptance pattern are all real. The three blockers below are
**narrow and mechanical** — none of them attacks the plan's architecture; two are in
the verification phase's fence, one is in the guard's measurement substrate.

## 1. Requirement coverage (1–8) — DELIVERED

| # | Requirement | Where the plan delivers it | Verdict |
|---|---|---|---|
| 1 | Tab manifest, typed FE-local, `central｜custom`, adding a series = registration-only | Ph 2.1–2.3, 2.12, 2.13; `tabs.manifest.ts`; four dispositions type-expressible; a spec asserts no wire-derivable field is duplicated. "Registration-only" holds on the family axis; a new metric *name* also needs an entry in the FE-local unit vocabulary (Ph 6.3) — still a typed-table edit, no code change | ✅ (see W4) |
| 2 | 7 tabs, all producer-live; no-source modules hidden entirely, no dark shells | §4.1 tab table + §4.3 dispositions; Ph 2.3 (hidden ⇒ no-family variant), Ph 2.4 (no central tab is an empty shell), Ph 8.7 (all-dark tab shows reasons) | ✅ |
| 3 | Frame above tabs: range selector + baseline/previous-window comparison; universal 15m/1h, central clamps 2h; tab-1 24h/7d via its own ladder | Ph 3.1–3.12: central ladder 15m/30m/1h/2h (2 h = the door's ceiling, `daemon/routers/metrics.py` clamps to `WINDOW_SECONDS`); tab-1 keeps its own 15m/1h/24h/7d (`daemon/routers/resources.py:106-113` `_WINDOW_SECONDS`); baseline = 2W fetch + **server-anchored** split, W ≤ 1 h, with a request-time guard | ✅ (see N1) |
| 4 | ONE consolidated house SVG renderer (minis/fallback) merging both engines, older engine's capabilities + the 5 mini-tier extensions | Ph 4.1 (12+ named symbols ported, per-assertion disposition table), 4.2 (capability checklist 6/6 incl. counter STEP), 4.3 (closed extension set: dPR crispness, overlay+bisect hover, last-value marker, optional baseline, viewBox) + a negative pin for out-of-set capabilities | ✅ |
| 5 | Tab 1 = Resources, custom slot, over its 6 existing support endpoints, no backend change | §4.1 + Ph 5.3–5.5 (`/live`, `/self-cost`, `/history`, `/anomalies`, `/anomalies/{id}/evidence`, `/traceback`) — all pre-existing FE service calls | ✅ |
| 6 | Chart correctness: unit-labelled axes, no mixed-unit normalization, attribution that reconciles or says it cannot, coverage consistent | Ph 6.1–6.5: 3 per-unit panels + the toggle path; metric-name-keyed unit vocabulary with "unit unknown"; `coverage_pct` displayed **verbatim** (no FE arithmetic — pinned); non-reconciliation sentence | ✅ |
| 7 | Optional release/restart event overlay only if an FE-side seam exists, else route with an owner | No FE-side seam exists (the journal is server-side and process-local) ⇒ the plan ships only the FE-derivable shapes (visible anomaly-marker labels + a "data gap" annotation that must never say restart/deploy/promote) and **routes** durable events as routed item (d) → metrics lane (small commission). Exactly the required shape | ✅ |
| 8 | `/resources` retired with a same-release redirect to `/metrics?tab=resource`; actionable empty state distinguishing 503 `AUTH_NOT_CONFIGURED` | Ph 7.1 (canMatch → UrlTree, parsed-param end-to-end spec, `tab` precedence), 7.3–7.9 (gear menu, sweep, runbooks, gate) + Ph 8.1–8.4 (ten-class closed union; actionable 503 panel with runbook pointer and no Retry, **central surfaces only**) | ✅ |

## 2. Binding rulings — RESPECTED

- **D-1** — Chart.js v4 is the only vendor, interactive panels only; the **same-commit** governance amendment (Ph 1.1–1.4: pins spec + pack Leg 7 + **six** zero-dep doc sites) lands with the dependency; the one house SVG renderer covers mini/fallback; the anomaly overlay is a **house plugin** (no second vendor). The plan also **withdraws** a false claim of its own (a lazy leak does not trip any budget) and replaces it with a four-part guard set (numeric delta / initial-chunk marker scan / import shape+site / route-fetch-set growth) co-located in Phases 1, 4, 5, 9 — i.e. the exposure window is covered.
- **D-2** — the door's fail-closed posture is untouched; the FE distinguishes the door's failure classes; the enablement is **env-var only** with zero tracked-file diffs; the operator recipe (§9) names the IdP registration, the env vars, the token mint, **the restart implication** ("no live toggle in v1") and the verification curls; §9.1 carries the "T1 ships mostly dark on live" release narrative. Nothing live is executed.
- **D-3** — route fate as specified.

## 3. Hard constraints

- **FE-only** — respected: no task requires a daemon, migration or `scripts/upgrade`
  change; the backend-adjacent retirements are **routed** (routed item g); the auth
  enablement is env-var only (Ph 9.3, "zero diffs" on `config.yaml` and the worktree `.env`).
- **Staging only, no live promote** — respected; §9.1 rides the staged-release disclosures.
- **Stop-a-tab-if-it-needs-a-backend-touch** — stated as a scope rule (Risk 5).
- **Routed-out register** — all five caller-named items are present with owners
  (a = USER morning item; b = metrics lane, cardinality/retention; c = metrics lane
  Tier-3; d = metrics lane, small commission; e = FE debt lane, Mermaid 972 kB gz),
  plus nine more ⇒ 14/14, matching the design source's own §(f) split.

## 4. BLOCKERS (must be discharged before this plan ships)

### B1 — The bearer-token mint is the lane's only database WRITE, and it is not fenced; its default target IS the live database

**What is wrong.** Phase 9 task 9.3 runs
`python -m daemon.auth.cli issue-token --label fe-t1-operator --scopes "maintenance:read"`.
That command resolves its own engine independently of the daemon boot:
`daemon/auth/cli.py:117-160 resolve_engine()` → `EnsembleConfig.load_or_create(data_dir)`
where `data_dir = ENSEMBLE_DATA_DIR | DATA_DIR | ./data` →
`daemon/ensemble_config.py:53-95`:
- if a `ensemble.json` exists in that dir, **the file's postgres block wins** (the
  documented "2nd poison vector"); otherwise the auto-detect path constructs
  `PostgresConfig()` from **hard-coded defaults** — `localhost:5432`, db `ensemble`,
  user `ensemble`, password `""` (`daemon/ensemble_config.py:22-37`).
`POSTGRES_DB` / `POSTGRES_HOST` are **never consulted** by this path. Those defaults
are byte-identical to the live install's `/home/nea/agents-ensemble/data/ensemble.json`
(`{"host":"localhost","port":5432,"db":"ensemble","user":"ensemble","password":""}`).
The plan's P1–P9 fence is entirely about `POSTGRES_*`, the `ensemble.json` JSON path,
and the **daemon boot** — it does not cover this command. The plan never mentions
that the mint writes a row at all (Ph 9.3 / §6.1 L1 / IMPL-14 speak only of "the
plaintext prints ONCE and never enters a repo file").

**Why it blocks.** A mint run without `ENSEMBLE_DATA_DIR` pointed at the scratch dir
resolves the **live** database and INSERTs a bearer row into the live
`auth_bearer_tokens` table — a live-state mutation, against the explicit constraint
"Live config and the live daemon are out of bounds". The lane's live fence (live
symlink + `state.json` byte-identical, uptime monotone, "0 live daemon requests")
**cannot detect a live DB write**, so this would ship a false green. In the other
direction, a scrubbed-env mint lands in the default/sqlite target, the bearer does
not exist in the dev DB, and L1 — the lane's data-bearing proof — fails.

**What would discharge it.** Bind the mint to the scratch data dir and prove its
target: (i) the mint invocation exports `ENSEMBLE_DATA_DIR=/tmp/fe-t1-dev-data`
(the P4 pre-seeded dir) under the same per-variable family scrub; (ii) the pre-seeded
`ensemble.json` is re-asserted with the same JSON-path `jq` check (P3) **for the
CLI's own resolution**; (iii) the CLI's resolved DSN is asserted to be
`ensemble_dev@127.0.0.1` **before** the mint, and a post-mint read-back proves the
row landed in `ensemble_dev` (a SELECT against the dev DB) — both recorded in the
fence evidence; (iv) the plan states explicitly that the mint is a database write
and that the lane's only write is inside the dev database.

### B2 — Phase 9's task graph contradicts the boot order its own prose requires; the data-bearing leg cannot pass as graphed

**What is wrong.** Task 9.3's `Depends On` cell reads `9.2`; task 9.2's ordered boot
sequence includes step (5) `python -m daemon`. Yet 9.3's text and the Phase-9 risk
table require the opposite — "9.3's exports precede 9.2's boot (auth has **no live
toggle**, `config.yaml:133`)" and "the task graph is ordered accordingly". The graph
does not encode it. (`9.11` additionally lists itself as its own dependency.)

**Why it blocks.** An executor following the `Depends On` column boots **auth-OFF**;
the `ENSEMBLE_AUTH_*` exports then arrive against a running daemon with no live
toggle. L1 then observes 503 `AUTH_NOT_CONFIGURED` instead of 200 (the plan's own
W-11 non-emptiness threshold fails), L1b cannot produce a 401-vs-503 distinction, and
9.7a's captures run against a dark door. The lane's central proof leg fails for a
reason contained in the plan itself — and because 9.7a/9.7b are ordered on 9.3/9.5,
the whole capture set is affected.

**What would discharge it.** Encode the order in the graph: make the auth-env export
a task that depends on 9.1 and that 9.2 (the boot) depends on — or split 9.2 into
"9.2a export + assert" and "9.2b boot" — and fix the 9.11 self-dependency.

### B3 — The Phase-9 re-derivation of the route-chunk figures is not stable under the lane's own changes; B4's baseline substrate can break or mis-resolve

**What is wrong.** `bundle-baseline.measure.js` identifies the route chunks by
**content markers**: `allJs.find(f => read(f).includes('metrics-empty-panel'))` (:35)
and `...includes('anomaly-marker')` (:53). Phase 7 retires the `/resources` route —
so a separate resources route chunk no longer exists and the anomaly-marker code
moves into the metrics tree — and Phase 8 rewrites the empty-panel/empty-state
surface, so `'metrics-empty-panel'` may cease to exist. Phase 9's exit criterion and
E-1 line nonetheless list "the two route chunks, the route fetch set" as figures
re-derived at Phase 9's freeze, and §0.2's G5 claims the `/resources` chunk size
gates "the retirement's bundle delta".

**Why it blocks.** After the lane's own edits the measurement either throws
(`read(undefined)` → `path.join` TypeError) or silently resolves the resources marker
to the metrics chunk, so G5's number becomes meaningless and B4's "route fetch set
growth ≤ 64 KiB vs 496,338 B" may be computed against a different set — the one guard
that actually carries the D-1 lazy-chunk guarantee loses its baseline. The plan's
headline claim (criterion 15: "no MEASURED figure gating an exit criterion is
un-re-derived") is then not achievable with the tool as written. (B3's grep-based
import-shape assertion partially covers the static-leak case, so this is a
baseline-stability defect, not an unguarded leak.)

**What would discharge it.** Pin the chunk identification to a key the lane controls
and cannot invalidate — the router's lazy import path resolved through the build's
chunk map, or a dedicated exported const the metrics route chunk must keep — assert
that the key resolves to **exactly one** chunk, re-derive G5 at Phase 9 or drop G5
from the gate set, and record the resolution in the evidence.

## 5. Warnings (non-blocking, fold in the same pass)

- **W1 — P8b has no named mechanism.** No endpoint exposes the daemon's DB identity
  (`daemon/routers/database.py` exposes only `POST /api/database/switch`). "Read the
  database back from the running daemon" needs a concrete mechanism — the daemon's own
  startup log line (`Loaded ensemble config: database=postgres` + the engine DSN it
  prints) — stated as an assertion, with "no new endpoint" (a daemon change would
  break the FE-only constraint).
- **W2 — the mini role has no T1 page surface** (R-2): the five mini-tier extensions
  are capability-retained and exercised via the mount-failure fallback and the
  renderer specs, not by a user-visible surface. The plan is explicit; noted so the
  caller sees requirement 4 delivered as specified, not as a visible mini.
- **W3 — requirement 3's baseline is scoped to the central tabs.** On tab 1 the
  baseline toggle is **disabled with a stated reason** (the resources door has no
  previous-window offset; a client-side split would mislabel two halves). Sound
  reasoning, but it narrows the literal requirement — say so once in the close-out
  narrative so the caller is not surprised.
- **W4 — "registration-only" should be stated once next to the manifest** as "family
  binding + metric→unit vocabulary entry (both FE-local typed tables)" so the §2.1
  qualification is not read as a broken promise later.
- **W5 — `9.11` lists itself as its own dependency** (fold into B2).

## 6. Independently verified as sound (evidence for the fold round)

- **The data-bearing route is feasible in code.** `python -m daemon.auth.cli
  issue-token --label --scopes` exists (`daemon/auth/cli.py:88`, `:178`); the scopes
  `maintenance:read` / `metrics:read` are in the v1 taxonomy (`daemon/auth/scopes.py:15`,
  `:76`, `:86`) and the door accepts the ANY-OF set (`daemon/routers/metrics.py:56-63`);
  the bearer lane verifies locally with **no IdP contact**
  (`daemon/middleware/auth.py:358-383`, `:440-447` → `lookup_bearer`);
  `ENSEMBLE_AUTH_ENABLED` overrides the yaml (`daemon/config.py:6867-6869`); the
  validator's requirements (client_id / client_secret / https issuer / non-empty
  `allowed_origins`, exit 78) match the plan's prose. The strategy is sound — only
  its **fence** is incomplete (B1).
- **The family census is real.** 16 registered families in the wiring registry
  (F0.3, F1, F3, F4, F5, F6, F7, F8, F9, F10, F11, F12, F13, F17, F18, F21;
  `daemon/services/metrics_query/wiring/registry.py`, `adjudication.py:51-90`) —
  10 live / 6 dark; the plan's binding (9 live + 3 bound-but-dark = 12; 4
  registered-unbound) adds up exactly.
- **The plan's file anchors are real** (checked against the tree): `app.routes.ts:74`
  (the `/resources` route), `app.ts:580` / `:597` (gear entries), `charts-tab.component.ts:195`
  (`WINDOW_S = 3600`), `metrics.component.ts:84` (`sections`), `families-tab.component.ts:210`
  (the dead `active` input), `test/packs/metrics_fe_dual_side_test.sh:275-286` (Leg 7),
  `metrics.bindings.pins.spec.ts:94` (the `chart.js` ban), `frontend/e2e/metrics-gate.spec.ts:277`
  / `:298` (canvas-fatal legs). No phantom anchors found in the spots checked.
- **The baseline's provenance holds.** `git diff 7961bcb21..HEAD -- frontend/ daemon/
  agents/ scripts/ test/` is **empty** — all four later commits are docs-only, so the
  measured baseline is a valid lane-base measurement (subject to B3).
- **The task graph is acyclic and coherent** once the two declared edges are honoured:
  2.4's panel-composition half was moved to 4.15 (C-2) and 7.1 declares
  `Depends On: Phase 8` — yielding the order 0 → 1 → 2 → 3 → 4 → 5 → 6 → **8 → 7** → 9.
  The remaining inversion is B2.
- **Exit criteria are not vacuous by construction**: planted-violation legs (the mixed
  axis, the count series on the MB panel, the "Retry on the auth state", the second
  banner, the red global dot, the "restart" wording in a gap annotation, the static
  `from 'chart.js'` import), the warning **set difference** (never a count ceiling),
  the "no FE-side coverage arithmetic" assertion, and the explicit retirement of the
  `d`-attribute e2e assertions are all present.

## 7. Re-review guidance

B1–B3 are mechanical: one fenced mint invocation, one task-graph edge, one stable
chunk-identification key. No phase, architecture or requirement needs to change.
Re-review should be scoped to: Ph 9.1/9.2/9.3 (fence + ordering + the mint's write),
Ph 1.9/4.14/5.14/9.8 (the guard's baseline + identification), and §0.2's G5 row.

## 8. Cross-check note

An independent cold-context `plan-approval` worker (`82404cdb-8458-4a83-a5ba-00f1286e3a35`)
was dispatched in parallel for a second opinion across the whole plan set. Any
material finding it raises will be appended to this file as an addendum. A REJECTED
verdict can only be strengthened by that cross-check, not reversed, so the lane's
next action (a fold round against B1–B3) does not wait on it.
