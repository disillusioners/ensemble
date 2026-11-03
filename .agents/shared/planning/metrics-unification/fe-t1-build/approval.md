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

---

# 9. ADDENDUM — independent cross-check report (2026-11-03)

**Status: the verdict is unchanged — REJECTED.** An independent cold-context
`plan-approval` worker (`82404cdb-8458-4a83-a5ba-00f1286e3a35`) returned **REJECTED**
with **4 blocking issues**: two confirm mine (B1 the unfenced mint, B3 the unstable
measurement tool) and **two are new**, both verified by me against the tree before
being folded in. **The blocking set is now 5 (B1–B5).**

## 9.1 B1 — SHARPENED by the cross-check (the hazard is larger than first stated)

The cross-check traced one hop further and found the decisive line:
`daemon/repositories/factory.py:346-350` (`create_postgres_engine`) resolves
`host / port / db / user / password` as **`os.environ` OVER the file's postgres
block**. Consequences that strengthen B1:

- The P4 pre-seed (`ensemble.json` → `ensemble_dev`) does **not** protect the mint if
  `POSTGRES_DB` is ambient — the **env wins** at the factory. The ambient agent
  shell on this host carries `POSTGRES_DB=ensemble_prod`, `POSTGRES_HOST=10.44.0.2`,
  `PORT=9797`.
- With no `data/` dir in the worktree, `EnsembleConfig.load_or_create` auto-detects
  postgres from the ambient env and **persists** a prod-pinned `ensemble.json` into
  the worktree — the second poison vector the plan itself flags in C-4, now applied
  to a **write**.

**Discharge (amended):** the mint must run inside the same scrubbed shell as the boot
(per-variable `POSTGRES_*`/`PG*` scrub — the scrub, not the file, is what protects it),
with `ENSEMBLE_DATA_DIR=/tmp/fe-t1-dev-data` exported so the file's dev block is the
fallback, plus a **pre-mint assertion that the resolved engine's database is
`ensemble_dev`** and a post-mint read-back proving the row landed there. The
cross-check also recommends the narrower `--scopes "metrics:read"` for the dev leg
(the door's ANY-OF accepts it — `daemon/routers/metrics.py:57-63` — while
`maintenance:read` additionally confers the destructive checkpoint-cleanup capability,
`daemon/auth/scopes.py:32-38`). Adopted.

## 9.2 B4 — NEW: the D-2 operator recipe omits a REQUIRED env var; it would not boot the daemon

**Where:** `plan-overview.md:658-664` (§9 step 2 — the recipe) vs `plan-overview.md:445-449`
(§6.1 / L1, which states the requirement correctly).

**Found.** The recipe lists `ENSEMBLE_AUTH_ENABLED`, `_CLIENT_ID`, `_CLIENT_SECRET`,
`_DISCOVERY_URL`, `_ALLOWED_ORIGINS` — and omits **`ENSEMBLE_AUTH_ISSUER`**.
`AuthConfig.issuer` has **no default** (`daemon/config.py:3738-3742`) and is **not**
derived from `discovery_url` ("Optional OIDC discovery override. Default derives as
`<issuer>/.well-known/openid-configuration`" — `daemon/config.py:3743-3748`);
`daemon/config.py:3953-3954` appends `"issuer"` to the `missing` list and the boot
**refuses with exit 78**. The plan states the requirement correctly in §6.1 and
incorrectly in §9 — the two sections contradict each other, and the cited source
(`docs/runbooks/oauth2-auth.md` §1b) carries the same omission.

**Why it blocks.** D-2 mandates the **exact operator recipe** as a deliverable
("the exact operator recipe (steps + restart implication) belongs in the close-out
report"). A user following §9 literally would set the listed vars and the daemon
would refuse to boot (exit 78) — the recipe does not deliver the outcome it claims.

**Discharge.** Add `ENSEMBLE_AUTH_ISSUER=https://<idp>/` to §9 step 2 and state that
the issuer's origin must match the discovery document's issuer
(`daemon/config.py:3984-3988`); amend `docs/runbooks/oauth2-auth.md` §1b, or record
explicitly that the runbook's env list is incomplete.

## 9.3 B5 — NEW: Phase 5's and Phase 7's exit criteria contradict each other on the same directory

**Where:** `phase5-plan.md:60-61` (Phase 5 exit: "the old directory is gone") vs
`phase5-plan.md:21` (5.1) and `phase7-plan.md:20` (7.4 + acceptance: "the directory
`frontend/src/app/pages/resources/` no longer exists").

**Found (verified in the tree).** `frontend/src/app/pages/resources/` contains
`live/`, `timeline/`, `svg-timeline/` **and** the page shell
(`resources.component.ts/.html/.scss/.spec.ts`) + `resources-gear-menu.spec.ts`.
Phase 5's 5.1 moves only `live/` + `timeline/` (+ the new anomalies panel);
`svg-timeline/` goes in 4.12; the **page shell survives until Phase 7's 7.4** —
which still owns the route's lazy import (`frontend/src/app/app.routes.ts:74`).

**Why it blocks.** At Phase 5's freeze "the old directory is gone" is **false**, and
satisfying it by deleting the shell would break the build (the route still imports
it). The lane therefore cannot satisfy Phase 5's exit criterion without breaking the
build or silently violating Phase 7's — a contradiction between two exit criteria
on the same artifact.

**Discharge.** Amend Phase 5's exit to: "the module's `live/` and `timeline/`
subtrees are relocated; the **page shell remains** in `pages/resources/` until Phase 7
retires the route", and keep 7.4's directory-removal acceptance as the single
statement of the deletion.

## 9.4 Additional warnings folded in (from the cross-check, all non-blocking)

- **W6 (was N-2) — the new positive import pin must be statement-anchored.** The
  existing guard is a naive `expect(sources).not.toContain('chart.js')` over
  concatenated file bodies (`metrics.bindings.pins.spec.ts:89-95`), and task 1.7
  requires the stub's doc comment to state the dynamic-import shape — which contains
  the literal `from 'chart.js'`. As written, 1.2(c)'s new pin would go **RED at the
  Phase-1 freeze** that 1.2(c) itself requires to be green. Specify a
  statement-anchored match (e.g. `^\s*import\s[^\n]*from\s+['"]chart\.js['"]`) or an
  explicit comment exclusion.
- **W7 (was N-4) — per-series min-max normalization is named as the defect but never
  removed.** Phase 6's objective cites "each independently min-max normalized", yet
  6.1 only requires the memory series to share one scale and the planted variant is
  "one mixed axis" only; a per-series rescale **inside** a single-unit panel would
  still pass. Add an acceptance that no series is independently rescaled within a panel.
- **W8 (was N-5) — task 6.6 targets a string in the wrong file.** The
  "per-instance attribution" promise is the page shell's subtitle
  (`resources.component.html:5`) — a file Phase 7 deletes; the Live strip's card
  already carries the honest label ("attribution is partial by physics",
  `live/live-tab.component.html:144-157`). Re-target 6.6 or fold it into 7.4.
- **W9 (was N-6) — the boot and mint commands name no interpreter.** The worktree has
  no `.venv`/`venv`, bare `python` does not exist on this host (only `/usr/bin/python3`),
  and the house resolution is the certified main-venv interpreter run from the
  worktree cwd. Pin it for `python -m daemon` and `python -m daemon.auth.cli`.
- **W10 (was N-7) — the dev leg writes into the shared `ensemble_dev` database** with
  no throwaway-DB/DROP teardown stated (house PG convention).
- **W11 (was N-3) — G11 juxtaposes two coincidental 10/6 splits.** The census is
  correct (10 LIVE = F0.3/F4/F5/F8/F9/F10/F11/F12/F13/F17; 6 DARK = F1/F3/F6 +
  F7/F18/F21), but the same row also cites the adjudication split ("10 full + 6
  partial"), whose 6 PARTIAL are F7/F8/F11/F13/F18/F21 — a **different** partition.
  An implementer could read "6 partial = 6 dark" and hide tabs 5 and 6, whose
  families (F8/F11/F13) are **LIVE**. State the two splits side by side with their
  family lists.

## 9.5 Cross-check's positive findings (corroborated, recorded)

The cross-check independently confirmed: the citation discipline (~40 line-precise
claims checked, no material mis-citation, including the plan's own correction of the
stale `envelope.py:386-388` cite); C-4's code-path analysis (exactly 2 `dotenv`
hits, the sqlite-fallback persistence at `ensemble_config.py:95`, the effective-port
and DB read-backs as the right instruments); the bundle-guard correction (Angular's
`initial` budget counts entry points + transitive static imports only) and the C-1
lazy-sibling insight; the non-vacuity of the exit criteria; the FE-only discipline
(a row-by-row scan of all 107 task rows found no deliverable in `daemon/`,
`daemon/migrations/` or `scripts/upgrade` — every `daemon/**` mention is a read-only
citation); the correct scope of the six zero-dep governance sites; and the task-graph
shape (exactly 2 disclosed forward edges — 3.7→Phase 4 and 7.1→Phase 8 — plus the
`9.11` self-loop, no hidden cycle).

## 9.6 Re-review scope (unchanged, now larger)

Fold **B1 (with the factory.py env-over-file detail), B2, B3, B4, B5** and
**W1–W11**, then re-review. The architectural shape of the plan, the requirement
coverage (1–8), the rulings (D-1/D-2/D-3) and the FE-only discipline are **not** in
question; every blocking item is mechanical.

---

# 10. APPROVAL — ITERATION 002 (2026-11-03)

**Verdict: APPROVED** (iteration 002 FINAL). Fold commit reviewed: `006281ed6`
("docs: fold B1..B5 + W1..W11 into fe-t1-build plan set"), 12 plan-dir files,
+347/−82. **Planning-only**: `git show --name-only 006281ed6` touches nothing under
`frontend/`, `daemon/`, `agents/`, `scripts/`, `test/`; the only non-`.md` edits are
the two plan-dir artifacts (`bundle-baseline.measure.js`, `bundle-baseline.json`),
which B3 authorized.

Discharge was verified **against the plan files and the tree**, not against the fold
report; where a claim was testable, it was tested.

## 10.1 Blocker discharge (5/5 CLOSED)

| # | Discharge — what the fold landed | My independent verification |
|---|---|---|
| **B1** | `phase9` **9.3a** (new): Depends On **9.1**; the boot **9.2** depends on 9.3a. The mint runs **inside the scrubbed shell** with `ENSEMBLE_DATA_DIR=/tmp/fe-t1-dev-data`; the **resolved** target is proved from **both** resolution inputs — (i) `env \| grep POSTGRES` ⇒ **0 hits** and (ii) the scratch `ensemble.json`'s `$.postgres.db == "ensemble_dev"` (P3 re-asserted for the CLI's *own* resolution, because `factory.py:346-350` resolves `os.environ` **over** the file); the scope is narrowed to `--scopes "metrics:read"` (ANY-OF at `routers/metrics.py:56-63`); the token is read back **from `ensemble_dev`**; and a **write-detecting live fence** reads the live `ensemble_prod` `auth_bearer_tokens` count **before and after** with **0 delta** required (the symlink/`state.json`/uptime fence provably cannot see a DB write). "The mint IS a database write" is stated in the scope note and §6.1 | **CAPABLE OF TURNING A LIVE WRITE RED — yes.** The resolution chain is closed on both inputs: env scrubbed ⇒ `os.environ.get("POSTGRES_DB", pg.db)` falls back to the file's `ensemble_dev` block; the file is asserted by JSON path; and the live count's delta is a **hard gate** (a prod write ⇒ delta ≠ 0 ⇒ STOP), backed by a read-back that proves the row landed in dev. **Nothing in the mint path can still reach a prod DSN**: the ambient `POSTGRES_DB=ensemble_prod` is provably absent, the file's block is provably `ensemble_dev`, and the auto-detect persistence of a prod-pinned `ensemble.json` cannot occur because the scratch dir is pre-seeded (`load_or_create` returns the existing file and never auto-detects) |
| **B2** | The graph now reads **9.1 → 9.3a (export + fenced mint) → 9.2 (boot) → 9.3b (L1)**; `9.11`'s self-dependency is gone (now `9.10`); `9.6`/`9.7a` now depend on `9.3b` | **CLOSED** — an executor following `Depends On` now boots **auth-ON**; the task table, the risk row and the exit criterion all state the same order |
| **B3** | `bundle-baseline.measure.js` rewritten: **route-keyed** primary (`ROUTE_KEY = 'ensemble-metrics-route-v1'`, the lane-controlled constant Phase 2's new task **2.13** exports), **legacy content-marker fallback** (to reproduce the lane-base baseline), **absent-tolerant** (null route fields, never throws), **exactly-one** resolution (`hits.length === 1 ? hits[0] : null`), and the locator reported in the output. G5 restated as a **PRE-retirement baseline, not a Phase-9 gate**; Phase 9's E-1 line now covers "the surviving route chunk(s)" | **CLOSED — and I RAN IT.** (i) On the real dist: `exit 0`, and the output is **byte-identical** to `bundle-baseline.json`'s embedded `reproduce.output` (zero differing keys) — the artifact and the tool are in sync, so B8's "the guard reads the named artifact" holds. (ii) Marker-stripped dist (the post-Phase-7/8 shape): `exit 0`, `route_chunk_locator: null`, `route_chunk_resolved_to_exactly_one: false`, route fields null, **no throw**, and the initial-total/lazy-census measurements still produced. (iii) The route key in exactly one chunk ⇒ `route-key`, resolved. (iv) The route key in **two** chunks ⇒ **null — no mis-resolution**. All four behaviours are as specified |
| **B4** | §9 step 2 now lists **six** vars including **`ENSEMBLE_AUTH_ISSUER=https://<idp>/`** — REQUIRED, not derivable from the discovery URL (`config.py:3736-3748`), `config.py:3953-3954` puts it in `missing` ⇒ exit 78 — plus the **origin rule** (`config.py:3984-3988`); the cited runbook's env-incompleteness is **disclosed** and routed; §9 is declared the canonical recipe source | **CLOSED.** My ruling on the caller's question: **disclose-and-route is the correct call for this lane** — `docs/` is outside the commission's FE-only scope (`frontend/` + `.agents/`), §9 is now self-sufficient and canonical, and the gap rides the disclosure set with a named owner. See A3 for the one loose end (the runbook item is not a §7 register row) and note that Phase 7's **7.8 already edits `docs/runbooks/`**, so a one-line in-lane amendment was an available alternative — the chosen path is still the defensible one |
| **B5** | Phase 5's exit now asserts the `live/`+`timeline/` subtrees are relocated and **the page shell REMAINS** in `pages/resources/` until **Phase 7's 7.4** (which stays the single statement of the directory's removal) | **CLOSED** — the two exit criteria no longer contradict; the tree confirms the premise (`pages/resources/` holds `live/`, `timeline/`, `svg-timeline/` **and** the shell, and `app.routes.ts:74` still lazily imports the shell) |

## 10.2 Warning discharge (11/11 CLOSED)

W1 P8b ⇒ the **startup-log read-back** (`ensemble_config.py:75`) with "no new endpoint"
(verified: only `POST /api/database/switch` exists) · W2 mini-has-no-T1-surface ⇒
disclosed in §0.4/§9.1 · W3 baseline scoping ⇒ disclosed once · W4 "registration-only"
⇒ stated once in §2 as **two** FE-local typed-table edits · W5 ⇒ folded into B2 ·
W6 ⇒ the pin is **statement-anchored** (import statements, prose ignored) with a
planted leg "the stub's own doc comment naming `from 'chart.js'` ⇒ **GREEN**" ·
W7 ⇒ **0 independently-rescaled series per panel** with a third planted variant
(a series rescaled *inside* the memory panel) · W8 ⇒ 6.6 re-targeted at the
surviving relocated Live card (the shell's subtitle dies with the shell in 7.4) ·
W9 ⇒ interpreter pinned to `/home/nea/ensemble-src/.venv/bin/python` — **verified
present, Python 3.13.15**; bare `python` does not exist on this host (only
`/usr/bin/python3`) · W10 ⇒ teardown deletes **only** the minted row (scoped by
label), **`ensemble_dev` is NOT dropped**, and the live count is re-asserted at
teardown · W11 ⇒ G11 now states both taxonomies with their family lists
(LIVE = F0.3,F4,F5,F8,F9,F10,F11,F12,F13,F17 · DARK = F1,F3,F6,F7,F18,F21 ·
PARTIAL = F7,F8,F11,F13,F18,F21 — "6 partial" ≠ "6 dark"; F8/F11/F13 are PARTIAL
yet **LIVE**, so tabs 5 and 6 render).

## 10.3 No weakening — verified by my own census

I diffed **every numeric token** across every `.md` in the plan dir, pre-fold
(`fefa938c5`) vs post-fold (`006281ed6`): **every threshold figure's occurrence count
went UP or stayed; no value changed and none was removed** (`2 kB` 17→18, `64 KiB`
11→12, `5,909,018 B` 36→37, `496,338 B` 16→17, `90,982 B` 17→18, `27,672 B` 12→13;
`8 kB`, `24 kB`, `161/161`, `7200` unchanged). **D-1, D-2, D-3, ADJ-iv and IMPL-14 are
byte-identical** (section-level diff), and **`phase3-plan.md` is byte-identical** —
so the **AM-14 narrowing** and the per-instance top-N routing (**m**) are untouched.

## 10.4 Requirement coverage + FE-only discipline — still intact

Requirements **1–8 remain delivered** (the fold changed only the specific items
above; §4's surface/tab map is untouched), the rulings stand, the routed-out
register is still 14/14 with owners, and the fold is planning-only with zero
changes under `frontend/ daemon/ agents/ scripts/ test/`.

## 10.5 Advisories (non-blocking, for the record)

- **A1 — §0.2's quoted "verbatim output" block is stale.** It still carries the old
  field names (`resources_route_chunk` / `resources_route_chunk_bytes`), while the
  artifact and the tool now emit `pre_retirement_resources_route_chunk*` plus
  `route_chunk_locator` / `route_chunk_key` / `route_chunk_resolved_to_exactly_one`.
  **Every byte figure in the block is identical** (verified key-by-key) and the
  artifact is authoritative — so this is prose drift only, it cannot cause a false
  green. One-line fix; the plan's own E-1 rule ("the quoted output must BE the
  output") is the only thing it offends.
- **A2 — the new live-count fence should name its command and be label-scoped.**
  By the plan's own E-1 rule ("a label without a COMMAND is not a re-derivation"),
  the read-only live count should carry its exact SELECT. Better: assert the
  **label-scoped** count (`WHERE label = 'fe-t1-operator'` ⇒ 0 in `ensemble_prod`)
  *alongside* the total-count delta, so an unrelated concurrent mint on the live DB
  cannot produce a spurious RED. Also assert that **the read itself succeeded** —
  a fence that cannot read is not a fence (STOP, do not proceed).
- **A3 — the B4 runbook gap is disclosed with an owner but is not a §7 register
  row** (the register is still 14 items and criterion 13 still says 14/14). Add it
  as a 15th row (or state that §0.4's disclosure set extends the register) so the
  9.11 close-out check covers it.
- **A4 — `fold-audit.md:46` still cites "phase2 2.13" for the AM-20 copy-precedence
  task**, which is now **2.14** (2.13 became the B3 marker-constant task). Stale
  pointer in the fold record only; the plan files are consistent.
- **A5 — `phase1`'s 1.2(c) acceptance list has two "(iv)" legs** (i, ii, iv, iii, iv);
  the new "stub doc comment ⇒ GREEN" leg should be "(v)". Cosmetic.
- **A6 — `pre_retirement_resources_route_chunk` is only meaningful at/before Phase 7.**
  After the relocation the anomaly-marker string lives in the metrics chunk, so the
  field could report the metrics chunk under a pre-retirement name. It is excluded
  from the Phase-9 gate and documented as pre-retirement-only — the field's label
  should say so.

## 10.6 Status

**APPROVED (iteration 002 FINAL).** The plan is implementable as written, its exit
criteria are non-vacuous, its verification route is fenced against live contact
(with a write-detecting fence that can actually turn a live write RED), the FE-only
discipline holds, the routed-out items are complete with owners, and no
pre-existing threshold or ruling was weakened. A1–A6 are advisories for the
implementation lane, not conditions.
