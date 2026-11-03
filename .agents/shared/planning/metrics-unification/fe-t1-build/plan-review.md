# PLAN REVIEW (council) — FE-only T1 metrics dashboard lane

**Commission:** wi-32a99618 (overnight-authorized) · **Review date:** 2026-11-03
**Target:** `.agents/shared/planning/metrics-unification/fe-t1-build/` (13 plan docs) on branch
`feature/fe-metrics-dashboard` @ `dc55da10f`
**Method:** governor council, `councilor_skill=plan-review`, `councilor_agent_id=worker`,
governor instance `d62326a2-ea14-480c-ab24-60b45cdc2658`
**Read-only:** no plan file was modified by this review; no git write command was run; the main
checkout (`/home/nea/ensemble-src`) was not touched.

---

# PLAN REVIEW (council) — FE-only T1 metrics dashboard lane

**VERDICT: REJECT (scoped)**
**CRITICAL: 4  WARNING: 14  ADVISORY: 9**

> **Council composition (disclosed):** the dispatch asked for `decision, agentic` with up to 4 councilors. `decision` is **not** in this deployment's allowed-model set (`agentic`, `coding` only), so it was mapped to `coding` — 2 distinct canonical models, no silent single-model fallback. Two refinement rounds were run (5-point adjudication, then 2-point adversarial re-verification). **Model headers disagreed (`REJECT` vs `APPROVE-WITH-ADVISORIES`) while the bodies converged on 4 CRITICALs and READY-WITH-CONDITIONS**; I resolve the header split below.
>
> **Scope of evidence:** the full 16-file plan set was read by both councilors independently; the bundle ledger was re-derived by each from the built dist by its own method; the shipped v0.28.9 dist was used as a cross-check; the real `metrics-gate.spec.ts`, the pins spec, `tsconfig.metrics-fe.json`, `angular.json`, `config.yaml`, `daemon/ensemble_config.py`, `daemon/repositories/factory.py`, `daemon/config.py`, `daemon/__main__.py`, and the registry/adjudication SSOT were read. **No file was modified; no git write command was run; the main checkout was not touched.**

---

## 🔴 CRITICAL findings

### [🔴 C-1] `plan-overview.md:376-379` (B1–B4), `:60` (G4), `:549` (§10 criterion 8); `phase1-plan.md:31` (1.2c), `:36` (1.7); `phase4-plan.md:25` (4.4), `:70-72` (phase-4 exit); `phase9-plan.md:35` (9.8d); `decisions.md:18-19` (D-1) — the D-1 guarantee is not enforced, and the import-graph guard is unimplementable as written
The guard set measures the **initial** bundle only. A **static** `from 'chart.js'` inside the lazily-loaded metrics tree satisfies **B1** (initial total unchanged), **B2** (no marker in any *initial* chunk) and **B3** (the import site is inside `src/app/pages/metrics/**` — which B3 *requires*), because a module reachable only from a lazy graph is extracted into a **lazy-only sibling chunk**, not into the initial set. **Evidence:** `/metrics` is a lazy route (`frontend/src/app/app.routes.ts:58`); on the real dist the `/metrics` route chunk statically imports `chunk-6Y6F4JKW.js` (43,690 B) and `chunk-AOZCSBSC.js` (1,348 B), **both lazy-only** (absent from the builder's 36-row initial-chunk table) — so the route chunk need not even grow. The wrapper's final home is `frontend/src/app/components/charts/chart/chart-panel.component.ts` (`phase1-plan.md:36` stub, `phase4-plan.md:25` create) and **no task relocates it** (`phase5-plan.md:21` moves only `pages/resources/`; `grep "components/charts/chart"` → 1.5 `chart-theme.ts` (a different file), 1.7, 4.4 only) — so **1.2(c)'s literal pin and B3's literal rule FAIL at the wrapper's real path**, reddening the phase-1 and phase-4 freezes, and 1.7's parenthetical *"(the wrapper's home after Phase 5)"* is **false**. B4 has **no threshold and no baseline**; G4's 27,672 B has **no consumer** (`grep "27,672"` → `plan-overview.md:60` + `research-findings.md:222/:548/:573` only) — an orphan ledger row whose named gate ("Phase 9's new-chunk comparison") does not exist, so D-1's only size claim ("~65 kB gz") is unverified at the lane's end. **Both councils confirmed these mechanics independently.**
**Required change:** (a) resolve the pin/shape contradiction — relocate the wrapper in a **named** task to a path under `src/app/pages/metrics/**` (making 1.2(c)+B3 hold by construction and 1.7's parenthetical true), **or** restate 1.2(c)/B3 as *"every **static** `from 'chart.js'` site is inside `src/app/pages/metrics/**`; any `import('chart.js')` must be dynamic"*, matching 1.7/4.4's own wording; (b) make the import **shape** a gate, not just the location — require the dynamic `await import('chart.js')` form; (c) give B4 a threshold **and** a baseline, promoting G4 from orphan row to B4's declared baseline, and add the missing comparison leg to 9.8(d); (d) measure the **`/metrics` route fetch set** (route chunk + transitive static deps = **496,338 B** today), not the route chunk alone — a route-chunk threshold does **not** catch the static form; (e) add a **lazy** planted-violation variant to 1.9 (the current variant plants only an **eager** import, so it proves half the guard); (f) **leader adjudication, not a redesign:** disambiguate D-1's *"first paint"* — app shell (as §10 criterion 8 operationalises it) vs the `/metrics` **page**. The plan says neither. Under the app-shell reading this finding is a WARNING; under the page reading the static shape grows that page's first paint by the whole vendor payload and the import shape must be pinned to the dynamic form.

### [🔴 C-2] `phase2-plan.md:23` (task 2.4), `:42` (Coupling); `phase4-plan.md:29` (task 4.8), `:44` (Coupling); `plan-overview.md:253`, `:255` (coupling map) — Phase 2 task 2.4 requires the renderer registry that Phase 4 task 4.8 creates, while four declarations assert 2↔4 independence
2.4 requires the 6 central tabs to *"hand the data to the **renderer registry**"* and to *"compose its PANELS per kind+unit group"*, with acceptance *"a spec asserts a mixed-kind family produces **two panels**"*; the registry is created **only** by 4.8. 2.4's `Depends On` is *"2.1, 2.2"*. `phase2-plan.md:42` says *"Independent of: Phase 1 (except the theme extraction), **Phase 4**, Phase 6"*; `phase4-plan.md:44` says *"Independent of: **Phase 2**, Phase 7, Phase 8"*; the coupling map gives 2↔4 = independent in both directions. **Evidence:** `grep -rn "renderer-registry|rendererRegistry|RendererRegistry" frontend/src/` → **zero hits**; no other task creates it. The plan uses "Independent" loosely — `phase2-plan.md:42` itself declares Phase 2 independent of Phase 1 while 2.1's `Depends On` is *"Phase 1"* — so a numeric-order execution hits a **phase-2 freeze whose tab bodies cannot compile**. Compounding: the phase-2 **exit criterion** does not require the 6 central tabs to fetch or render anything, so the freeze can be green with six empty shells — 2.4's acceptance carries that requirement, the exit criterion does not.
**Required change:** split 2.4 — keep in Phase 2 the 7 tab bodies, family binding, the fetch and the availability surfacing; move *"hand the data to the renderer registry"*, *"composes its PANELS per kind+unit group"* and the two-panels spec into a **Phase-4 task** (e.g. 4.14a) whose acceptance re-asserts 2.4's two-panels assertion (mirroring the plan's own two-step-migration idiom, AM-27). Flip 2↔4 to **tight** in `plan-overview.md:253`/`:255`, `phase2-plan.md:42` and `phase4-plan.md:44`. Add to Phase 4's exit: *"the mixed-kind family yields two panels in the per-tab bodies (2.4's assertion, re-asserted here)"*. Add to Phase 2's exit: *"each of the 6 central tabs fetches its bound families and surfaces their availability (live panels, or the family's reason when dark — ADJ-vi); no central tab is an empty shell."*

### [🔴 C-3] `plan-overview.md:66` (G10), `:381` (B6); `research-findings.md:551` (§14.4); `phase9-plan.md:75-80` — G10 ships a false census: the `anyComponentStyle` warning baseline is **9**, not 8
**Evidence:** `grep -c "scss exceeded maximum budget. Budget 8.00 kB" /tmp/fe-t1-build-prod.log` → **9**; the ninth is `src/app/pages/blueprint/blueprint.component.scss` (**18.32 kB — the largest offender**), omitted from the plan's 8-item list. `frontend/angular.json:70-74` → 8 kB warn / 24 kB error, so nothing breaks the build. **Severity grounds (both councils converge on 🔴, on different grounds):** the row is classified *"(a) re-derived at lane base"* and is **not** — the shipped evidence records a false census, which is exactly the failure E-1's standing rule exists to prevent (`plan-overview.md:26-51`); and B6's comparison baseline is wrong with **both** resolutions failing — against the mis-stated 8-path set the gate is *unsatisfiable as written* (`blueprint` reads as NEW), against a count it is unsound **in both directions** (too-low → false red; too-high → vacuous green when a baseline warning disappears). B6 already says *"recorded verbatim as adjudication data (**never auto-fixed**)"*, so the "implementer trims the SCSS" trap is already closed and the severity does not depend on implementer error.
**Required change:** G10 → **9**, enumerating all nine paths with sizes (`agent-selector` 8.77 kB, `snapshots` 12.29, `jobs` 14.77, `add-source-modal` 8.32, `snapshot-detail-drawer` 8.70, **`blueprint` 18.32**, `checkpoint-cleanup` 8.70, `instance-list` 10.17, `chat-interface` 16.15) plus the pre-existing `initial` warning; `research-findings.md:551` the same correction, **visible rather than silently overwritten**. B6 expressed as a **set difference** against a baseline set captured **mechanically from the Phase-1 production build log** (never hand-written) — a count ceiling is **forbidden**, because a ceiling can be loosened and silently lose detection; the same mechanical-capture rule applies to the phase-9 re-derivation. Add to the Phase-1 exit criterion: *"the `anyComponentStyle` warning baseline set is captured from the build log and recorded (9 entries)."*

### [🔴 C-4] `plan-overview.md:290-291` (P3, P4), `:294` (P7), `:297-301` (§6.1 route); `phase9-plan.md:23-24` (9.2) — the named boot command cannot reach the dev DB: the scrub removes the only source of `POSTGRES_*` and nothing loads `.env`
AM-5 names `python -m daemon` as the boot command, but **no daemon module loads `.env`**: `daemon/config.py:6550` reads `ENSEMBLE_CONFIG`/`config.yaml`, `:6562-6563` opens the YAML and `:6571` substitutes `${VAR}` **from `os.environ`**; `daemon/__main__.py:402` and `daemon/api.py:531` call `load_config()` — env + YAML, never `.env`. `grep -rn "dotenv|load_dotenv" daemon/` → prose only (`daemon/config.py:3698`, `:3897`). `ENSEMBLE_SELF_ENV` is read **only** by `daemon/tools/upgrade_tools.py:253/:576-611`, so P1's `ENSEMBLE_SELF_ENV=dev` assertion triggers no load at all. The only loaders are **shell-side**: `dev.sh:70-76` (`set -a; source .env; set +a`) and `launcher.sh load_env_file` (`:2827`). **Both councils verified this independently** (the second model was tasked adversarially to refute it and could not). Consequence: after P6's scrub there is **no `POSTGRES_*` in the environment**, so **every** connection parameter falls back to `ensemble.json`'s embedded `postgres.*` block (`daemon/repositories/factory.py:344-350`, mirrored at `daemon/__main__.py:127-132`) — and **P4's pre-seed specifies `ENSEMBLE_DATA_DIR` with no host/user/password at all**. `daemon/ensemble_config.py:69-71` → the file decides the **engine kind**; `:81-83` auto-detect needs **both** env vars; `:90-91` *"No PostgreSQL ENV vars detected, defaulting to sqlite"*; `:95` `config.save()` **persists** it atomically, outranking the env on every later boot from that tree. Either way the data-bearing route (L1/L1b/L3, PX-1…PX-14) runs against the **wrong database**, and `/api/metrics/*` still answers **200 with an empty catalog** — a **quiet** failure that only P8's read-back catches.
**Required change:** make the env-load step explicit and ordered: **(1)** scrub per-variable per P6 → **(2)** source the **worktree's** `.env` into the daemon's environment (`set -a; . ./.env; set +a` — the house `dev.sh:70-76` idiom) → **(3)** assert the *loaded* `POSTGRES_DB == ensemble_dev` ≠ `ensemble_prod` → **(4)** **re-export `PORT=8079` AFTER the `.env` load** → **(5)** boot `python -m daemon` → **(6)** re-assert the effective port from `/livez` (P7's second half, unchanged) + P8's DB read-back + P3's `ensemble.json` assertion. **P7 must change from "PORT after the scrub" to "PORT after the `.env` load"** — the live install's `.env` carries `PORT=9797`, so a wrong order binds **the live port** (the house env-poison family). P4's pre-seed must carry the **full** `postgres` block or be dropped. `dev.sh` may be kept (it already does steps 2 and 4 and adds the worktree fence guard at `:81-110`), but its `:155` `uvicorn --reload` **bypasses `__main__`'s preflight and boot sweep** — state that loss explicitly if it is used.

---

## 🟡 WARNING findings

**[🟡 W-1] `phase1-plan.md:31` (1.2b), `:70-71` (phase-1 exit) — AM-10 landed weakly: the re-point target does not exist at the phase-1 freeze.** Evidence: 1.2b re-points the spec's `readFileSync` to *"the consolidated renderer's path"*; that path (`frontend/src/app/components/charts/house-svg-renderer/`) is created by `phase4-plan.md:22` (4.1) — `frontend/src/app/components/charts/` contains only `svg-line-chart` today, so the read throws `ENOENT`; 1.2(a) also scopes the banned-list scan to that same absent file. The spec is **RED at the phase-1 freeze** that `phase1-plan.md:70-71` requires to pass. **Required change:** give the pin a **phase-1-existing** target (a file Phase 1 creates and Phase 4 does not delete, e.g. `chart/chart-panel.component.ts` or `chart-theme.ts`) **or** have 1.2 create a one-line stub at 4.1's exact path; make 1.2(a)'s scan scope and 1.2(b)'s read target the same file; add to 1.2's acceptance *"the pins spec is green at the phase-1 freeze with the re-pointed path present in the tree at this commit."*

**[🟡 W-2] `phase7-plan.md:17` (7.1 acceptance), `:41` (risk row); `plan-overview.md:354` (PX-9); `architecture-review.md:128` (AM-3 Required) — AM-3 landed weakly: the `tab`-collision leg is stated but gated nowhere.** Evidence: AM-3's Required clause includes *"a leg `/resources?tab=charts` ⇒ lands on `?tab=resource`"*; the fold states the rule (7.1: *"the redirect's own `tab=resource` **overrides** an incoming `tab`"*) and narrows the claim correctly, but 7.1's acceptance asserts only `/resources?window=24h&x=1` and PX-9 asserts the same single case — the architect's exact complaint (*"the acceptance fixture contains no `tab`"*) remains true of every acceptance. **Required change:** add to 7.1's acceptance a second end-to-end `navigateByUrl('/resources?tab=charts')` asserting the **parsed** `queryParams.tab === 'resource'`.

**[🟡 W-3] `phase4-plan.md:31` (4.10), `:73` (phase-4 exit) — AM-11 landed weakly: "three" canvas-fatal legs against four enumerated refs.** Evidence: the real `frontend/e2e/metrics-gate.spec.ts` has **four** distinct assertion blocks — `:277` (count on `[data-test="svg-line-chart"]`), `:281-288` (step-path `d` geometry: H/V present, no diagonal), `:290-296` (line-path `d` geometry: L present, no H/V), `:298` (`chart-tick` visible). The architect's own text says "three" while listing four; its three replacement categories (count / `d`-geometry / `chart-tick`) do cover both `d`-blocks, so the substance is covered — but the **count** in the exit criterion invites a re-specifier to re-specify three of four. **Required change:** say *"four canvas-fatal assertions in three leg-classes"* and name all four refs in the exit criterion.

**[🟡 W-4] `plan-overview.md:376-378` (guard "Where" cells), `:462` (risk 1); `phase1-plan.md:52` — the guard-coverage claim overstates where the guards are.** Evidence: B1/B2/B3 run at **1.9, 4.14, 9.8** only; phases 2, 3, 5, 6, 7 and 8 exit criteria contain no bundle check, while the risk rows say *"the leak window spans Phases 1–8"* — a phase-1 guard cannot detect a phase-5 leak. Phase 5 is precisely a chunk-graph-changing phase (`phase5-plan.md:21` relocates the resource module into the very tree B3 requires chart.js import sites to live in; `:29` re-points the timeline to the Chart.js panel role). **Required change:** restate the coverage as *"guards at phases 1, 4 and 9"* and add a bundle-guard re-run to **Phase 5's** exit criterion.

**[🟡 W-5] `plan-overview.md:556` (§10 criterion 15) vs `:68-69` (G12, G13) — "13/13" is overstated; the true figure is 11/13.** Evidence: G12 is *"**NOT a gate** — no exit criterion depends on a line count"* and G13 is *"**Not measurements** — code facts"* — neither satisfies (a) or (b). The §0.2 audit conclusion is internally consistent at 9 (a) / 2 (b) / 1 non-gate / 1 code-fact = 13, but G11's row carries **both** (a) and (b), which is where the circulating *"9/3/1/1"* figure comes from — **it double-counts G11**. **Required change:** row 15 → *"no MEASURED figure gating an exit criterion is un-re-derived — **11/13** rows are (a) or (b); G12 is explicitly not a gate and G13 is a code-fact set"*, and mark G11's dual status in its row; add G11's Phase-2 freeze to the audit conclusion's (b) bucket.

**[🟡 W-6] `phase1-plan.md:31` (1.2a) — narrowing the ban-list check to the house-SVG file drops coverage over four other scanned sources.** Evidence: the real spec (`frontend/src/app/pages/metrics/metrics.bindings.pins.spec.ts:72-88`) scans **five** sources (overview, charts, `metrics.component.ts`, the engine file, `metrics.service.ts`) against `['chart.js','d3-',"from 'd3'",'ngx-charts','moment','dayjs']` (`:94`). 1.2(a) drops `chart.js` from the list **and** scopes the check *"to the house-SVG file only"* — so `charts-tab.component.ts`, `metrics.component.ts`, `overview-tab` and `metrics.service.ts` are no longer scanned for `d3-`, `ngx-charts`, `moment`, `dayjs`, and the stated justification no longer applies once `chart.js` leaves the list. The replacement 1.2(c) positive pin is location-only and does not restore absence-of-import for those four files. **Required change:** keep the ban-list scan over **all five** sources for the four remaining bans (scope the change **per-ban**, not per-file), and add a planted `dayjs` violation leg in `charts-tab.component.ts`.

**[🟡 W-7] `plan-overview.md:295` (P8); `phase9-plan.md:23` (c) — P8 asserts a `config.yaml` "database stanza" that does not exist.** Evidence: `config.yaml`'s top-level keys are `llm:5, daemon:111, auth:129, limits:201, persistence:320, agents:334, queue:338, compaction:408, critical_notes:448, services:484, job_system:536, scheduling:543, notify_user:560, skill_evolution:578, blueprint:620, vscode:645`; `grep -i "postgres|database" config.yaml` → **zero hits**; `daemon:` carries only `host`/`port` (`:111-113`) and `persistence:` only SQLite paths (`:320-323`). The DB is chosen by `data/ensemble.json` + `POSTGRES_*`. **Required change:** split P8 into **P8a** (`config.yaml`'s effective **port** stanza is inspected → 8079) and **P8b** (the running daemon's **actual database** is read back → `ensemble_dev`, the load-bearing half AM-29 asked for); delete "port/DB stanza" from P8's check column.

**[🟡 W-8] `plan-overview.md:290-291` (P3, P4) — P4 pre-seeds the very file P3 polices, after P3's assertion, with unspecified contents.** Evidence: P3 asserts `data/ensemble.json` is absent or benign; P4 then pre-seeds `ENSEMBLE_DATA_DIR=/tmp/fe-t1-dev-data` with a *"pre-seeded `ensemble.json`"* whose contents are unspecified — and per `daemon/ensemble_config.py:69-71` an existing file outranks the env for the engine choice and is persisted (`:95`). The worst case is a SQLite dev daemon, but a live fence's post-pre-seed state is unasserted. **Required change:** give the pre-seed's **exact contents** (`{"database":"postgres","postgres":{"db":"ensemble_dev",…}}`) and **re-run P3 after P4** (or drop the pre-seed and assert after the first boot's auto-persist).

**[🟡 W-9] `plan-overview.md:290` (P3) — the `data/ensemble.json` assertion is not pinned to the JSON path.** Evidence: the live install's `data/ensemble.json` is `{"database":"postgres","postgres":{"host":…,"port":5432,"db":"ensemble",…}}` — the DB name lives at **`$.postgres.db`** and its value is `"ensemble"`, **neither** `ensemble_dev` **nor** `ensemble_prod`; the engine discriminator is the top-level `database` (`daemon/ensemble_config.py:50-51`). A checker keyed on the literal string `ensemble_prod` anywhere in the file **passes** on a prod-adjacent file. **Required change:** pin P3 to `.database == "postgres"` **and** `.postgres.db == "ensemble_dev"`, and assert the pre-seeded file at P4 with the same expression.

**[🟡 W-10] `phase2-plan.md:57-76` (phase-2 exit) vs `:23` (2.4's acceptance) — the phase-2 exit criterion is satisfiable by seven label-only tabs.** Evidence: the exit requires only that labels + ids match the manifest; nothing requires the 6 central tabs to **fetch or render** their bound families. 2.4's acceptance carries that requirement ("*the 6 central tabs fetch their families and surface their availability*"), but the exit criterion does not — so a phase-2 freeze can be green with six empty shells. **Required change:** add to the phase-2 exit criterion *"each of the 6 central tabs fetches its bound families and surfaces their availability (live panels, or the family's reason when dark — ADJ-vi); no central tab is an empty shell."*

**[🟡 W-11] `phase9-plan.md:25` (9.3 / leg L1) — L1's data-bearing acceptance has no non-emptiness assertion.** Evidence: the acceptance is *"The door answers **200** … `/api/resources/live` answers 200 with real values"*; a 200 carrying an **empty** family list satisfies the first half, and "with real values" has no threshold. Combined with C-4, the lane's core data-bearing leg can go green against a SQLite/empty door. **Required change:** require non-emptiness — *"the families response contains ≥ 1 registered family and ≥ 1 of the 7 bound families; the F9 snapshot returns ≥ 1 series; `/api/resources/live` returns a non-zero `daemon_rss_mb`"* — and record the observed counts in the evidence.

**[🟡 W-12] `phase9-plan.md:25` (9.3) — "in the DEV config only" is a wording ambiguity that invites an out-of-scope tracked-file edit.** Evidence: `daemon/config.py:6867-6869` explicitly overwrites the yaml's `enabled: false` with `ENSEMBLE_AUTH_ENABLED`, and the other fields resolve via `env_prefix="ENSEMBLE_AUTH_"` (`:3715-3718`) — so env vars alone suffice and 9.3 is **not** a scope violation (both councils agree; `config.yaml:124-133` documents this). But `config.yaml` is tracked and is neither `frontend/` nor `.agents/`. **Required change:** state the mechanism as the four `ENSEMBLE_AUTH_*` env vars with *"no tracked file is edited — `config.yaml` and the worktree `.env` have zero diffs after 9.3"* as a phase-9 exit line.

**[🟡 W-13] `phase9-plan.md:25` (9.3), `:27` (9.5), `:29` (9.7a) — the auth task graph is broken in two places.** Evidence: 9.3 depends on **9.2 (the boot)**, so auth is enabled *after* the daemon booted without it and **no auth-ON restart is named** before L1/L1b — yet auth has no live toggle (`config.yaml:133`; `plan-overview.md:502-507`) and the env vars are read at config-load time. And 9.7a's `Depends On` reads **"9.3, 9.5"** while its own prose says *"the ORDER is: L1/L1b/L3 → captures → then the auth-OFF restart"* — but **9.5 IS the auth-OFF restart**, so the graph forces the auth-OFF restart *before* the auth-ON captures, and PX-1…PX-7 and PX-9…PX-14 would be captured against an auth-OFF daemon. **AM-6's split landed in the prose but not in the task graph** — the same class as AM-7. **Required change:** 9.3's env exports must precede 9.2's boot (or name an explicit auth-ON restart between 9.3 and L1); 9.7a's `Depends On` becomes **"9.3"**, with 9.7b (PX-8, auth OFF) depending on 9.5.

**[🟡 W-14] `phase2-plan.md:22` (task 2.3) — the `Depends On` cell reads "2.1, 2.1" (duplicated cell).** Evidence: a task-graph typo in the same phase as C-2; the plan's own coupling map is its task graph of record. **Required change:** correct the cell to its intended dependency.

---

## 🟢 ADVISORY findings

| # | Location | Note |
|---|---|---|
| A1 | `plan-overview.md:436-437` vs `:454` | §7's preamble says *"The lane does not build any of these"*, but row **(n)** assigns the `/resources` gate-pack retirement to *"this lane, Phase 7"* — and `phase7-plan.md:23` (7.7) does build it. Re-word the preamble to "…except (n), which Phase 7 retires". |
| A2 | `plan-overview.md:67` (G11) | The row carries both (a) and (b) without saying so — this is the source of the circulating "9/3/1/1" split. Mark the dual status inline. |
| A3 | `phase2-plan.md:11` | "adding a series to a wired module is registration-only" needs qualification after AM-9/AM-12: the unit vocabulary is metric-name-keyed (`phase6-plan.md:26`), so a new metric name renders *"unit unknown"* rather than being free. |
| A4 | `phase4-plan.md:30` (4.13) | The pinned residue grep uses the bare pattern `"3600"`, which over-matches (`36000`, `3.600`) — the house residue-gate trap AM-23 targets. Tighten to a word-bounded pattern. |
| A5 | `phase1-plan.md:33` (1.4 iii) | The cited line `central-metrics-v1/phase6-plan.md:23` is task 6.5; the "npm dependency delta is empty" string is at **:25** (6.7). The site is real; only the line drifts. |
| A6 | `plan-overview.md:288` (P1) | P1 still requires `DAEMON_PORT=8079` in the `.env` — inert since AM-5 (`DAEMON_PORT` appears nowhere under `daemon/`). Replace with the `PORT` assertion or delete the clause. |
| A7 | `phase5-plan.md` risk row vs `phase4-plan.md` exit | Phase 5's risk row claims *"the Phase-4 exit criterion records which ordering was taken (AM-27)"*; phase 4's exit does not record it. Add the ordering decision to phase 4's exit. |
| A8 | `plan-overview.md:57` / `research-findings.md:541` | AM-2 asked for a *"named baseline artifact"*; the frozen baseline is a number duplicated across ~10 plan locations with **no machine-readable file** for the guard script to read. Give the guard a single source so the number cannot drift between the ledger and the script. |
| A9 | `phase6-plan.md` exit | The phrase *"the four fixes"* under-counts five substantive fixes — wording only. |

---

## FOLD-INTEGRITY TABLE (AM-1…AM-29)

**Merged verdict — 22 landed fully · 6 landed weakly · 1 landed partially · 0 dropped · 0 re-scoped · 0 criticals folded down to advisory.**

| AM | Sev | Architect's Required clause (abridged) | Landed where | Merged verdict |
|---|---|---|---|---|
| **AM-1** | 🔴 | per-source door state; `auth_not_configured` on central surfaces only; **add L4**; 3.1 states the control is central-sourced | 2.11, 5.10, 8.2, 9.5 (L4), `plan-overview:322-326`; 3.1 "The control is **CENTRAL-SOURCED**" | **landed fully** |
| **AM-2** | 🔴 | re-measure at lane base + freeze + named artifact; restate the risk mechanism; B1 numeric ≤2 kB + no chart.js marker in any initial chunk; co-locate in Phases 1 **and** 4; delete the "hard-fails" claim | `research-findings §14`; `plan-overview:364-391`; 1.9, 4.14, 9.8; withdrawal verified by grep — the phrase survives only inside explicitly-retracted quotations (`:371`, `:462`, `research-findings.md:588`) | **landed fully** (caveats: W-4 detection window, A-8 no machine-readable artifact, C-1 the shape is not gated) |
| **AM-3** | 🔴 | name the param mechanism + null-fallback; `tab=resource` overrides; end-to-end **parsed**-params assertion + the `?tab=charts` leg; no hand-built-UrlTree assertion | 7.1 (all clauses) | **landed weakly** → W-2 |
| **AM-4** | 🔴 | add `auth_required`(401), `timeout`, `unknown_family`(404), `bad_request`(400); phase 2 adds the service timeout; phase 9 adds L1b | 8.1 (10-class closed union), 2.10, 9.4 | **landed fully** |
| **AM-5** | 🔴 | name the boot command; `PORT=8079` **after** the scrub; assert the **effective** port from the running daemon, not an env var; state `python -m daemon` vs `dev.sh` | P7, 9.2; independently verified: `DAEMON_PORT` absent from `daemon/`; `config.yaml:113` = `port: ${PORT:-8088}` | **landed fully for the letter** — but it opens C-4 and leaves P1's stale `DAEMON_PORT` (A-6) |
| **AM-6** | 🔴 | split the capture into 9.7a (auth ON) / 9.7b (auth OFF) + restart steps + `/livez` re-assertion | 9.7a/9.7b + `plan-overview:340-342` | **landed weakly** → W-13 (the split landed in the prose, not in the task graph) |
| **AM-7** | 🔴 | correct map row 8 + phase-8 coupling to "tight with 4 and 6"; add the 7↔8 task-graph edge — *"so map and graph agree"* | map row 8 cols 4/6 = tight; `phase8-plan.md` Coupling block; `phase7-plan.md:17` "Depends On: Phase 2, Phase 8" — all three named instances landed, but the **class was not swept** | **landed partially** → C-2 (2↔4) |
| **AM-8** | 🔴 | widen the union (third variant / discriminant); `familyIds` optional; 2.3 binds each hidden module | 2.1, 2.3, phase-2 exit ("all four dispositions are type-expressible") | **landed fully** |
| **AM-9** | 🔴 | add the surface→intent→renderer→granularity table to §4; 3.7 names the T1 baseline renderer; reword §1/§10 | §4.2 (7-row table), 3.7, §1:101-106, §10 criterion 5 | **landed fully** |
| **AM-10** | 🔴 | 1.2 re-points the read path + scopes the banned list; 4.1 updates the tsconfig include; 4.9 removes the stale entry; phase-4 exit states the relocated specs are inside the scoped check | 1.2(a)(b), 4.9, phase-4 exit; verified against the real tree — the pins spec **does** `readFileSync` `svg-line-chart.component.ts` (`metrics.bindings.pins.spec.ts:76-88`) and `tsconfig.metrics-fe.json` **does** include the deleted spec path, so 4.9's asymmetry note (241 L falls out, 237 L never entered) is factually correct | **landed weakly** → W-1 |
| **AM-11** | 🔴 | enumerate all the canvas-fatal legs with replacements; state the `d`-attribute assertion is **RETIRED** | 4.10 (`:277`, `:281-288`, `:290-296`, `:298` verified verbatim against the real spec) | **landed weakly** → W-3 |
| **AM-12** | 🔴 | unit vocabulary keys on the **metric** name; panels per kind+unit group, kind from the wire; 6.1 gains a counts panel; acceptance covers 5 series / 3 unit classes + the toggle path | 6.1, 6.3, §4.2 | **landed fully** |
| **AM-13** | 🔴 | FE displays `coverage_pct` **verbatim** + a non-reconciliation sentence; pin "the FE computes NO coverage percentage" | 6.5 (verbatim + pinned + a spec asserting the absence of FE arithmetic) | **landed fully** |
| **AM-14** | 🔴 | baseline scoped to central tabs; custom toggle disabled with reason; request-time guard (auto-clear / never >7200 / refuse on a clamped payload); the two spec legs | 3.4, 3.5 (3 legs), 3.6, §4.2 | **landed fully** |
| AM-15 | 🟡 | grep pin over resolved import sites; B3 moved earlier; 1.7 fixes the import shape | 1.2(c), 1.7, B3 at 1.2c/1.7/4.14 | **landed weakly** → C-1 (the *shape* is not gated; the pin conflicts with the sanctioned wrapper path) |
| AM-16 | 🟡 | request-**generation** bump; baseline shares the tab's single request; window change invalidates window+anomalies but not the 8 s poll | 3.10, 5.11 | **landed fully** |
| AM-17 | 🟡 | anchor the split on the response's `from_utc/to_utc`; faked-clock leg; correct the four cites | 3.4, 3.12 — all four cites re-verified correct | **landed fully** |
| AM--18 | 🟡 | meta reports derived spacing (or labels the door value as cadence); 2 h spec leg | 3.11, 6.7 | **landed fully** |
| AM-19 | 🟡 | per-panel control scope + "Timeline range" label; `allowedWindows ⊆ ResourcesWindow` | 3.1, 5.4, 2.12 | **landed fully** |
| AM-20 | 🟡 | narrow the 403 predicate to `SCOPE_INSUFFICIENT`; add `forbidden_other`; one copy SSOT with cause/meaning/action/pointer; move both duplicated suffixes; manifest carries variants only | 8.11, 8.12, 2.13 | **landed fully** |
| AM-21 | 🟢 | keep the dot on `/api/health`; page-scoped chip; drop 8.4's shell mechanism | 8.5 + phase-8 exit (PX-8 asserts the dot is not green) | **landed fully** |
| AM-22 | 🟡 | widen the sweep to `frontend/` + `docs/` with a per-extension include list + classification; docs task for both runbooks; 7.7 must cover the **spec**, not only the config | 7.6, 7.7, 7.8 | **landed fully** |
| AM-23 | 🟡 | pin the exact command (pattern + path set + excludes) for all three zero-hit greps + allowlist rows | 2.8, 4.13, 5.13 | **landed fully** (A-4 pattern breadth) |
| AM-24 | 🟡 | amend the two missed checklist sites; amend `metrics-fe-charts.md`'s path reference **and** its feature-frozen/crosshair-OUT sentence | 1.4 (six sites, all verified to exist with the cited content) | **landed fully** (A-5 cite drift) |
| AM-25 | 🟡 | replace the "2 added lines" criterion with the by-name expected set; exempt `.agents/tester/PACKS.md` from the agents/-untouched clause | phase-1 exit `:66-69`, `:78-80` | **landed fully** |
| AM-26 | 🟡 | enumerate all 12 exported symbols by name; checklist gains "counter STEP rendering"; the per-assertion disposition table becomes a phase-4 exit criterion | 4.1 (12 named), 4.2 (6/6), phase-4 exit | **landed fully** |
| AM-27 | 🟡 | state the two-step migration (registry in 4, panel in 5) or reorder 4.9 | 4.12, 5.1, 5.9 | **landed fully** (A-7 cross-reference) |
| AM-28 | 🟡 | record the per-component `anyComponentStyle` figure; bound the renderer under 8 kB | 4.11, B5, B6 | **landed fully** |
| AM-29 | 🟡 | widen P6 to the family rule; add P8 (config.yaml + read the daemon's actual DB back); reword "uptime byte-identical" | P6, P8, 9.10; uptime correctly reworded to MONOTONE | **landed weakly** → W-7 (P8's config.yaml database half has nothing to assert) |

**Answer to (e):** no 🔴 amendment's Required clause was dropped — every one has a landed counterpart. But **7 landed weakly or partially** (AM-3, AM-6, AM-7, AM-10, AM-11, AM-15, AM-29), and **4 of those are 🔴** (AM-3, AM-6, AM-7, AM-10). The pattern is consistent: **the prose landed; the task-graph, the acceptance fixture, or the numeric threshold did not.** That is the fold's systemic weakness and it is worth naming to the approver — a grep for "AM-n present in the plan" cannot detect it.

---

## GATED-FIGURE LEDGER VERIFICATION (G1…G13)

Both councilors re-derived the ledger **independently** (own static-import closure from `index.html`, plus the builder's own table at `/tmp/fe-t1-build-prod.log`), then cross-checked the shipped `releases/v0.28.9/frontend/dist`. **Both produced identical numbers**; the shipped v0.28.9 dist measures byte-identically to the lane base.

| # | Figure | Plan value | Re-derived | Verdict |
|---|---|---|---|---|
| G1 | Initial total (36 chunk files, index.html excluded) | 5,909,018 B | **5,909,018 B** (36 files = 13 referenced by `index.html` + 23 transitive static-import chunks) | **confirmed** |
| G2 | Headroom vs 6,000,000 B | 90,982 B | **90,982 B** (the digest's 89,982 is a 1,000 B digit slip: 5,909,018 + 89,982 = 5,999,000) | **confirmed** |
| G3 | Lazy chunk count | 52 | **52** (87 `.js` − 35 initial) | **confirmed** |
| G4 | `/metrics` lazy chunk | 27,672 B | **27,672 B** (`chunk-ZA3RV4HY.js`; contains `metrics-banner`/`metrics-charts`/`metrics-container`/`metrics-empty`/`metrics-families`/`metrics-overview`) | **confirmed as a figure — but it is an ORPHAN: no threshold, no comparison, no exit criterion consumes it** (→ C-1) |
| G5 | `/resources` lazy chunk | 38,914 B | **38,914 B** (`chunk-FOLCEMN3.js`) | **confirmed** |
| G6 | Lazy bytes total | 1,702,633 B | **1,702,633 B** | **confirmed** |
| G7 | Largest lazy chunk | 157,141 B | **157,141 B** (`chunk-QS2K3YLH.js`); 2nd **135,690 B** (`chunk-HNKUMJNH.js`) — the plan's one-position drift is real | **confirmed** |
| G8 | Per-component `anyComponentStyle` | post-implementation | — (class (b), task 4.11) | **correctly classified (b)** |
| G9 | New chart chunk min+gz | post-implementation | — (class (b), task 9.8(d)) | **correctly classified (b)** |
| G10 | `anyComponentStyle` warning count | **8** | **9** — `blueprint.component.scss` (18.32 kB, the largest) is omitted from the plan's list | **REFUTED** → C-3 |
| G11 | 16-family census (16 / 10 live / 6 dark) | 16/10/6 | `daemon/services/metrics_query/wiring/adjudication.py` — *"10 full + 6 partial = 16 of 26"*; `registry.py:64` `FAMILY_SPECS` | **confirmed** |
| G12 | Spec line counts | 241/237/519/316/1,645 | informational; AM-26 replaced it with the disposition table | **confirmed (correctly out of the gate set)** |
| G13 | Code constants | 7200 s etc. | `daemon/services/metrics_query/constants.py:33` = `WINDOW_SECONDS: int = 7200`; `resources.models.ts` 4-value union; `config.py:811-827` metrics intervals | **confirmed** |

**E-1 discharge:** the E-1 line is present in **10/10** phase files, and both councils assessed **discharge**, not mere presence — phase 0 (no figure), 1 (G1), 2 (G11 at freeze), 3 (code facts), 4 (G1 + G8 at freeze + G9 placeholder), 5–8 (none), 9 (G1–G9 + G10 at freeze). **No exit criterion depends on an un-re-derived MEASURED figure** — the substance of E-1 holds. The exceptions are narrow and identified: **G10 is mis-labelled (C-3)** and phase 9's discharge inherits it.

**Answer to (b): confirmed.** The fold adopted the re-derived number, not the architect's. The mismatch is disclosed in `research-findings.md` §14.5 with a full arithmetic explanation (AM-2's 5,115,820 = the 13 index.html-referenced files; 5,125,077 − 9,257 = 5,115,820). **No phase file carries the stale 884,180 / 5,115,820.**

---

## PER-PHASE EXIT-CRITERION SOUNDNESS (phases 0–9)

| Phase | Verdict | Reason |
|---|---|---|
| 0 | **sound** | Machine-checkable: `git show --stat` = exactly 1 file; zero changes under `frontend/ daemon/ agents/ test/`; 161/161; D-1/D-2/D-3 present; section placement checkable. Cannot be satisfied by a no-op. |
| 1 | **incomplete** | The pins spec is **RED at the phase-1 freeze** because 1.2(b)'s re-point target is created only at 4.1 (W-1). Otherwise sound: by-name dependency set, planted-violation legs, bundle guard green **and** non-vacuous, `daemon/ agents/ scripts/` untouched. |
| 2 | **incomplete + vacuous-capable** | Two distinct holes: (i) 2.4's acceptance cannot be met at the phase-2 freeze (the registry arrives in Phase 4) while four declarations assert independence — **the phase-2 build does not compile** (C-2); (ii) the exit criterion itself does not require the 6 central tabs to fetch or render, so it is satisfiable by seven label-only tabs (W-10). The rest is strong: four dispositions type-expressible, per-source model, timeout, `allowedWindows ⊆ ResourcesWindow`, pinned `active` residue grep. |
| 3 | **sound** | Every bullet is behavioural and mutation-checked: disabled options, announced snap, 2W fetch + server-anchored split, the three request-time guards, the single-request invariant, the faked-clock leg, the forged-cursor negative pin, derived-spacing meta, the tier copy with corrected cites. |
| 4 | **sound, count-label weak** | The per-assertion **disposition table** (count derived from the files, not declared) is the exit evidence — not a line count; 6/6 capability checklist incl. STEP; the mini's closed set proven by absence; relocated specs provably inside the scoped type-check; guard re-run. Weakness: W-3 ("three" vs four refs). |
| 5 | **sound** | Flat body order, the panel's own labelled control driving exactly one `/history` request and none to `/live`/`/self-cost`, pinned residue grep, drill-down, process-local disclosure, visible marker labels, "data gap" wording with the forbidden-words spec, second re-point, tab 1 lit under a central failure, per-assertion disposition for the moved specs, and the placeholder-removal line (`phase5-plan.md:32`, `:76`). Its weakness is the missing guard re-run (W-4). |
| 6 | **sound** | 3 per-unit panels with a shared memory scale and a separated CPU scale; the **toggle path** (5 series / 3 unit classes) with two planted variants; "unit unknown" never a guess; mixed-kind → two panels; coverage verbatim + the FE-computes-no-coverage pin; derived spacing; the dropped per-instance promise; four planted-violation legs. |
| 7 | **sound, one leg ungated** | End-to-end **parsed**-params redirect assertion (a raw-string or hand-built-UrlTree assertion is explicitly rejected); the null-fallback path; the deleted directory; the gear menu; the widened classified sweep recorded in full with zero class-(c) hits; the Playwright **spec** (not just the config) re-pointed; the two runbooks. Gap: W-2. |
| 8 | **sound** | The class table is a **closed union of ten** (an unhandled status is a spec failure, not a silent `unexpected`); each class's copy/action/Retry pinned; the actionable panel on central surfaces only with no Retry; one banner; the global dot untouched; the 900 px geometry assertion; dark families showing reasons; the surfaced selector slice; the sorted/filtered/`aria-sort`/error-gated drawer; the data-end marker + sample age; one copy SSOT with the two duplicated suffixes gone; both 403 branches pinned. **503-AUTH_NOT_CONFIGURED vs 403 (two branches) vs network (status 0) vs `registry_unavailable` are all discriminated**, and the FE-registry-degraded path is separately gated at 2.7 and e2e `:351/:356/:359`. |
| 9 | **vacuous-capable (partly)** | The fence legs (P1–P8), B1–B7 and the live fence are strong, but: the boot route cannot reach the dev DB (C-4), L1's acceptance has no non-emptiness assertion (W-11), B4 is threshold-less and baseline-less (C-1), and the auth graph would capture PX-1…PX-14 against an auth-OFF daemon (W-13). PX-8/PX-9 and the 0-non-GET census are non-vacuous. |

---

## VERIFICATION-FENCE TABLE

| Leg / assertion | Verdict | Reason |
|---|---|---|
| P1 (`.env` dev fence) | **non-vacuous, partly** | Real file assertions with STOP on failure; the `DAEMON_PORT=8079` clause is **inert** (A-6) and `ENSEMBLE_SELF_ENV` triggers no `.env` load (C-4). |
| P2 (`POSTGRES_DB != ensemble_prod`) | **non-vacuous** | Explicit negative check on the file; the *effective* value is proven by P8 — but only after C-4's fix loads `.env`. |
| P3 (`data/ensemble.json`) | **vacuous-capable** | The JSON path is unpinned (the live file's `$.postgres.db` is `"ensemble"`, which passes a literal-`ensemble_prod` checker) — W-9; and P4 pre-seeds the same file *after* P3's assertion with unspecified contents — W-8. |
| P4 (scratch `ENSEMBLE_DATA_DIR`) | **non-vacuous, contents unspecified** | A distinct scratch dir makes the live install unreachable; the pre-seed's contents and post-pre-seed assertion are missing (W-8), and it lacks the host/user/password values C-4 needs. |
| P5 (sweep guard pinned) | **non-vacuous** | `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` asserted **explicitly**, never `env -u` — matches the house critical note exactly. |
| P6 (per-variable scrub) | **non-vacuous** | Prefix `POSTGRES_*` **and** `PG*`, plus `BLUEPRINT_*`, plus suffix `*_API_KEY`, scrubbed one-by-one; the `env -u POSTGRES_*` silent no-op is explicitly forbidden. **Strictly stronger than the house rule**, which omits `PG*`. |
| P7 (effective port) | **non-vacuous in principle; ordering wrong** | The port is read back from the **running daemon** (`/livez`), never self-attested — AM-5 fully discharged. But the ordering statement is wrong: "after the scrub" must become "after the `.env` load", because the live install's `.env` carries `PORT=9797` (C-4). |
| P8 (config.yaml + daemon DB read-back) | **partly unsatisfiable** | `config.yaml` has no database stanza (W-7); the running-daemon read-back half is non-vacuous and is the load-bearing one. |
| **Boot command + scrubbed env** | **INVALIDATING** | No `.env` loader on the `python -m daemon` path; the scrub removes the only source of `POSTGRES_*`; the sqlite fallback is **persisted** — the data-bearing route runs against the wrong DB and `/api/metrics/*` still answers 200 with an empty catalog (**C-4**). |
| L1 (auth ON, scoped bearer) | **vacuous-capable** | "Answers 200" passes against an empty catalog (W-11); combined with C-4, the core leg can go green against a SQLite door. |
| L1b (401) | **non-vacuous** | Asserts the **sign-in panel** and **no** Retry — a dead-Retry implementation fails. |
| L2 / L4 (auth OFF) | **non-vacuous** | Requires the central actionable panel **and** tab 1 lit with real values and **no** auth panel — the AM-1 proof; fails if tab 1 is dark. |
| L3 (mixed units) | **non-vacuous** | Per-unit labels **plus** a geometry assertion — two independent artifacts. |
| The three canvas-fatal e2e legs | **non-vacuous** | Replacements specified, the `d` assertion explicitly retired, plus a "the `d` assertion survived" check that must fail — but the count must say four (W-3). |
| B1 / B2 / B3 (bundle guard) | **non-vacuous where it runs; vacuous-capable for the static-import case** | Each has a planted-violation red — but only the **eager** variant is planted, and a static `chart.js` import inside the lazy metrics tree passes all three (**C-1**). |
| B4 (new chunk size) | **vacuous-capable** | Record-only, **no threshold**, and no baseline (G4 is orphaned) → D-1's "~65 kB gz" claim is unverified at the lane's end (**C-1**). |
| B5 (`anyComponentStyle` < 8 kB) | **non-vacuous** | A numeric threshold with a recorded figure. |
| B6 (budget warnings) | **non-vacuous in principle; baseline wrong** | The frozen baseline set omits `blueprint` (C-3); the comparison form must be a set difference, not a count ceiling. |
| B7 (packs + 161/161) | **non-vacuous** | 2/2 green; 161/161 hash verification; `PACKS.md` correctly exempted (AM-25). |
| PX-1…PX-14 | **non-vacuous, but the capture posture is broken** | Each asserts rendered real values / distinct surfaces; PX-8 also at 900×1000 with a no-collision assertion; PX-9 on **parsed** params. **However** W-13: the task graph would capture them against an auth-OFF daemon. |
| Live-contact fence | **airtight in shape; one material hole** | Scratch dir + per-var scrub + explicit sweep pin + `ensemble.json` pin + effective-port read-back + 0-non-GET census + symlink/`state.json` byte-identical + uptime monotone + 0 live config writes. **The hole is the boot path (C-4)** — and the fix itself carries a live-port bind risk (the live `.env`'s `PORT=9797`) that the PORT re-export ordering must close. |

---

## SCOPE-FIDELITY TABLE (commission items 1–8)

| # | Commission item | Verdict | Where |
|---|---|---|---|
| 1 | Tab manifest | **delivered** | Phase 2 (2.1–2.3, 2.5, 2.12, 2.13); the four dispositions are type-expressible |
| 2 | 7 producer-live tabs | **delivered** | §4.1 table; Phase 2 shell + Phase 5 tab 1; the dark-but-bound families are listed with reasons (ADJ-vi) |
| 3 | Page frame range + baseline | **delivered** | Phase 3 (central-sourced control, server-anchored split, W ≤ 1 h, three request-time guards) |
| 4 | Consolidated SVG renderer | **delivered** | Phase 4 (12 named symbols, 6/6 checklist, closed mini set, the registry, the Chart.js wrapper) — with C-1's import-shape gap |
| 5 | Resource custom slot on the 6 existing endpoints | **delivered** | Phase 5; the **6** endpoints enumerated at `plan-overview.md:179` |
| 6 | y-axis units + attribution honesty + coverage consistency | **delivered** | Phase 6 (6.1–6.5); metric-name-keyed vocabulary; coverage verbatim + explicit non-reconciliation; per-instance top-N routed to (m) with an owner |
| 7 | OPTIONAL overlay — feasible-or-route | **delivered as a decision** | ADJ-iv: shapes 1+2 **ship** (5.7 marker labels, 5.8 data-gap annotation, with a planted "restart" wording test); the durable restart/deploy work is **ROUTED with an owner** — ADJ-iv → §7 row (d), owner *"metrics lane (small commission)"*. **Not silently skipped.** |
| 8 | Redirect + auth empty-state | **delivered** | Phase 7 (redirect) + Phase 8 (10-class table) + Phase 9 (L1b/L4) + §9 operator recipe (never executed) + §9.1 E-2 mostly-dark narrative — with W-2 and W-13 |

**Routed-out register (§7, rows a–n):** 14 rows, **every row carries an owner and a reason** ✓ (a USER; b/c/d/f/h/i/j/k metrics lane; e FE debt lane; g backend hygiene commission; l deferred-with-triggers; m follow-up commission; **n this lane / Phase 7** — row (n) is in-lane, see A-1). **No routed item silently re-enters scope:** items (b), (f), (g), (h), (i), (j) each name the concrete backend work and its owner, and the "backend touch ⇒ **STOP THAT TAB**, record it, ship the rest" rule is stated (`plan-overview.md:466`).

**FE-ONLY DISCIPLINE: CLEAN (confirmed by both councils after round 2).** Every `daemon/`, `config.yaml`, `migrations/` or `scripts/upgrade` mention across all plan files is a **read-only citation**; no task edits a backend file, adds a migration, or touches the promote machinery. The `agents/` tree is protected by 161/161 assertions in the phase-1, phase-2 and phase-9 exit criteria, with `.agents/tester/PACKS.md` explicitly exempted. §9's recipe is handed over, never executed. The single qualifier is W-12's wording in 9.3.

---

## SPOT-CHECK HYPOTHESES (a)–(e) — adjudicated

**(a) CONFIRMED, with the split itself diagnosed.** The §0.2 audit conclusion is internally consistent at 9 (a) / 2 (b) / 1 non-gate / 1 code-fact = 13; the *"9/3/1/1"* figure that circulates **double-counts G11** (which carries both (a) and (b)); and §10 row 15's *"13/13"* is **overstated — the true figure is 11/13** (G12 is explicitly not a gate, G13 is code facts). → W-5 + A-2.

**(b) CONFIRMED.** The fold adopted the re-derived number, not the architect's; the mismatch is disclosed in `research-findings.md` §14.5 with the full arithmetic; **no phase file carries the stale 884,180 / 5,115,820**.

**(c) PARTIALLY REFUTED — the hypothesis's *closure* claim is wrong.** The re-point target *exists in the plan* (4.1's `house-svg-renderer/`), but it **does not exist in the tree at the phase-1 freeze**, so the pins spec is RED there. AM-10 is **not closed**. → W-1.

**(d) CONFIRMED as a count/label mismatch — neither a contradiction nor two legs at one ref.** Four distinct assertions in three leg-classes; the "three" is inherited from AM-11's own wording; the replacements cover all four refs. → W-3.

**(e) All 14 🔴 have a landed counterpart; none was dropped, folded down, or re-scoped.** Four landed weakly/partially (AM-3, AM-6, AM-7, AM-10) and three 🟡/🟢 landed weakly (AM-15, AM-29, and AM-2 carries caveats). See the fold table.

---

## FOLDED-PLAN READINESS VERDICT FOR THE APPROVER STAGE

**READY-WITH-CONDITIONS.** The fold is faithful and the plan needs **no re-plan, no new commission, no design change** — only targeted text/sequencing/threshold folds. Blocked until these land:

**Blocking (must fold before implementation starts):**
1. **C-1** — fix the pin/shape contradiction; make the import **shape** a gate; give B4 a threshold and a baseline; measure the **route fetch set**; add the lazy planted-violation leg; and disambiguate D-1's "first paint" (leader call).
2. **C-2** — split 2.4 so the registry arrives in Phase 2 (or restate 2.4 as registry-free); re-declare 2↔4 as **tight** in the map **and** both Coupling blocks.
3. **C-3** — G10 → **9**; B6 as a mechanically-captured **set difference**; capture the baseline at the Phase-1 freeze.
4. **C-4** — name the `.env`-load step; re-export `PORT=8079` **after** it; state P4's pre-seed contents or drop it.

**Non-blocking but fold before implementation:** W-1 … W-14, A-1 … A-9.

**After C-1…C-4 are folded, the set is READY for the approver.**

---

## WHAT I COULD NOT VERIFY (and one finding that was withdrawn)

- **G8 / G9** are post-implementation artifacts — unverifiable by construction; only that each carries a named phase-freeze task (4.11, 9.8(d)) and that no phase exit depends on their values.
- **The runtime behaviour of the fenced dev daemon.** Neither councilor could boot it (read-only mandate). C-4 is a **code-path conclusion**, not an executed observation. It is falsifiable by three cheap checks at implementation time: `env | grep POSTGRES` (expect nothing before the `.env` load), the boot log line `No PostgreSQL ENV vars detected, defaulting to sqlite`, and `cat $ENSEMBLE_DATA_DIR/ensemble.json`. **I recommend the fold author run those three before accepting C-4 as closed.**
- **The e2e legs' runtime semantics under canvas** — the spec was read (`:270-305`, all four refs confirmed verbatim) but Playwright was not executed; whether the replacements are sufficient is a phase-4 question.
- **The daemon-side behavioural claims not re-derived:** the 401/403/404/400 status declarations in `daemon/routers/metrics.py`, the auth middleware's bearer/401 branches, `config.py`'s exit-78 refusal path, and the `MetricsService`/house-timeout pattern. These are cited by the plan; both councils verified only the claims the dispatch named (`config.yaml:112-113` port interpolation ✓, `DAEMON_PORT` absent from `daemon/` ✓, `ensemble_config.py`'s shape ✓, `daemon/config.py:6867-6869` env-over-yaml ✓).
- **The architect's own 5,115,820 B measurement procedure** — both confirmed the fold's number twice and confirmed the plan's *explanation* of the delta arithmetically, but did not reproduce AM-2's procedure.
- **The "30 exported symbols"** were verified as a consumer graph (only `charts-tab.component.ts` imports `svg-line-chart`; only `timeline-tab.component.ts` imports `svg-timeline`; no other module references either), not enumerated symbol-by-symbol. The full G12 line-count set beyond four measured files is likewise unverified (it is informational, not a gate).
- **WITHDRAWN BY THE OTHER COUNCILOR, and I concur:** the claim that *"no exit criterion names the phase-2 placeholder's removal"* is **false** — `phase5-plan.md:32` (task 5.12) and `phase5-plan.md:76` (*"The Phase-2 placeholder is gone."*) close it. The coding councilor raised this in round 0 and withdrew it in round 1 when the agentic councilor quoted the lines. **Recorded as withdrawn, not carried.**
- **A vacuous green in this review would itself be a critical finding**, so I state plainly what it rests on: every finding above is anchored to a quoted plan line or a named code file:line, both councils independently re-derived the same bundle numbers by different methods, and every disagreement between them was resolved by re-reading the tree — not by argument. The one place I resolved a disagreement *by judgment rather than by new evidence* is the severity of C-1 and C-2 (below), and I flag both for your adjudication.

---

## GOVERNOR'S NOTE — THE TWO DISAGREEMENTS I RESOLVED

**1. Severity of C-2 (2↔4 coupling).** `coding` rated it **🔴 CRITICAL** ("the phase-2 build does not compile"); `agentic` confirmed the fact but rated it **🟡 WARNING**, because the two-panels property is independently re-asserted at phase 6's exit, so no capability ships unverified. **I adjudicate 🔴**, on three grounds the second model did not contest: the phase-2 freeze genuinely cannot be met (a build failure at a gate is the loudest possible failure, but it is still a failure); the coupling map is a shipped artifact of the plan set and would ship **four false independence declarations**; and the phase-2 exit criterion is satisfiable while a task acceptance is unmet. Agentic's phase-6 re-assertion mitigates only the *"ships unverified"* limb. **The fix is identical under both readings** — split 2.4 and re-declare the coupling — so if you prefer the WARNING, the fold set is unchanged.

**2. Severity and framing of C-1 (D-1 guarantee).** `coding` rated it **🔴** and re-scoped it in round 1, **withdrawing its own "the route chunk grows 27,672 → ~300 kB" claim and its own proposed G4-threshold fix** (on the real dist, a static import lands in a lazy-only *sibling* chunk, so the route chunk need not grow — and a route-chunk threshold would therefore not catch it). `agentic` rated it **🟡**, on the sharper ground that `/metrics` is a **lazy route** (`app.routes.ts:58`), so neither *"lazy-chunk-only"* nor the **app-shell** reading of *"zero first-paint growth"* is violated — and that what is genuinely ungated is **chunk shape** plus B4's missing threshold and baseline. **I adjudicate 🔴**, not on "first paint grows" (which agentic correctly showed is not established), but on the composite that both models established without dispute: the import-graph guard is **unimplementable as written** (1.2(c)/B3 fail at the wrapper's real path), B4 has **no threshold and no baseline** with G4 an orphan, and D-1's own binding phrase is **ambiguous** between the app shell and the `/metrics` page. **If you disambiguate "first paint" to the app shell *and* fix the pin and B4/G4, this finding drops to a WARNING** — that lever is explicit in C-1(f) and it is yours, not mine.

**Where the two models' headline verdicts disagree** (`REJECT (scoped)` vs `APPROVE-WITH-ADVISORIES`) while their bodies converge on four CRITICALs and READY-WITH-CONDITIONS: I resolve to **REJECT (scoped)** — meaning *do not implement yet*, fold C-1…C-4 and the warnings, and the set is then READY for the approver. The `agentic` councilor's own readiness verdict was READY-WITH-CONDITIONS with four CRITICALs, which is the same statement in approver-stage terms.

**Process note:** the two-model council converged through two refinement rounds; both models made substantive concessions against their own earlier positions (one withdrew an AM-10 "landed fully", one withdrew a D-1 severity rationale and a G10 self-heal mechanism, one withdrew a phase-2-placeholder advisory), and no councilor wrote to the worktree. **No behavioral violation was observed** — both councilors reported read-only conduct and the worktree's `git status` was clean at `dc55da10f` throughout. I did not record these findings to the project knowledge base, because their canonical home is `plan-review.md` and the folds have not landed yet — recording an unresolved finding as project knowledge would be premature.

---

# REVIEWER ADDENDUM — independent verification by the dispatching reviewer

The reviewer (dispatcher) re-verified the four CRITICALs and the two numeric headline claims
against the tree, independently of the council's methods, before accepting this review as the
canonical record. **All four criticals are CONFIRMED against the tree.** One of the dispatcher's
own spot-check heuristics was WRONG and is corrected here rather than dropped.

| Check | Method | Result | Bearing |
|---|---|---|---|
| **G1 / G2** | Parsed the builder's own "Initial chunk files" table in the lane-base build log (`/tmp/fe-t1-build-prod.log`), took the **36** named initial files, and summed their **on-disk** sizes in `frontend/dist/frontend/browser/` | **5,909,018 B** over **36** files; `index.html` = 9,257 B (excluded by the plan's basis); headroom vs 6,000,000 = **90,982 B** — **exact match to the ledger** | G1/G2 **confirmed** |
| **G10** | `grep -c "scss exceeded maximum budget. Budget 8.00 kB" /tmp/fe-t1-build-prod.log` | **9**, not 8. The omitted ninth is `src/app/pages/blueprint/blueprint.component.scss` at **18.32 kB** — the **largest** offender, present in the log at line 199 | **C-3 confirmed** — the plan's baseline census is false |
| **C-1** | `grep "components/charts/chart"` across the plan set | Only 1.5 (`chart-theme.ts`, a different file), 1.7 (stub) and 4.4 (create). **No task relocates the wrapper**, so its final home stays outside the `src/app/pages/metrics/**` allowlist that 1.2(c)/B3 require | **C-1 confirmed** |
| **C-2** | Read 2.4's acceptance + `Depends On`, 4.8's creation of `components/charts/renderer-registry.ts`, and both Coupling blocks | 2.4 requires "hand the data to the **renderer registry**" and its two-panels spec, `Depends On` = "2.1, 2.2"; the registry is created **only** at 4.8; `phase2-plan.md:42` says "Independent of: … **Phase 4** …" and `phase4-plan.md:44` says "Independent of: **Phase 2** …" | **C-2 confirmed** |
| **C-4** | `grep -rnE 'dotenv\|load_dotenv' daemon/ --include=*.py`; read `daemon/ensemble_config.py:60-96` and `daemon/config.py:6548-6575` | The only `dotenv` hits in `daemon/` are **prose in docstrings** (`config.py:3698`, `:3897`) — **no loader**. `ensemble_config.py:81-83` auto-detect requires **both** `POSTGRES_HOST` **and** `POSTGRES_DB`; `:90-91` *"No PostgreSQL ENV vars detected, defaulting to sqlite"*; `:95` `config.save()` **persists** the decision. `config.py:6571` substitutes `${VAR}` from `os.environ` | **C-4 confirmed** — after a per-var scrub the daemon cannot reach the dev DB |

**Corrections to the dispatcher's own pre-dispatch hypotheses** (recorded for honesty, per the
house "a vacuous green in my own review is a critical finding against it" rule):

1. **The dispatcher's crude static-import closure returned 34 files / 5,253,172 B**, not the
   ledger's 36 / 5,909,018 B — my regex missed import forms. The builder's own table plus an
   on-disk sum of its 36 named files reproduces **5,909,018 B exactly**, so the ledger is right
   and the heuristic was wrong. The council's method was sound; the dispatcher's first-pass
   number was not.
2. The dispatcher's pre-dispatch hypothesis (c) — "AM-10's closure is merely unproven" — is
   **refuted**: the re-point target exists *in the plan* but not *in the tree at the phase-1
   freeze*, so the pins spec is RED there and AM-10 is **not closed** (W-1).
3. The dispatcher's pre-dispatch hypothesis that the §0.2 split is *"9/3/1/1"* is **wrong**:
   the ledger is internally consistent at **9 (a) / 2 (b) / 1 non-gate / 1 code-fact**, and the
   "9/3/1/1" figure circulating **double-counts G11** (which carries both (a) and (b)). The
   §10 "13/13" success-criteria claim is separately **overstated — the true figure is 11/13**
   (W-5). This is the dispatcher's own prior that was wrong, on the dimension the dispatch most
   confidently pre-judged.
4. The dispatcher's pre-dispatch hypothesis (d) — "the canvas-fatal legs may be a contradiction"
   — is **refuted**: four distinct assertions in three leg-classes; the "three" is inherited
   from AM-11's own wording, and the replacements cover all four refs (W-3).

**Process fact for the leader:** the requested model `decision` was **not** in this deployment's
allowed set (`agentic`, `coding`), so the governor mapped it to `coding` — the alias recorded
in project metadata as the **known-poisoned upstream row** (D7: deprecated-row drop, ~210
errors/day, which killed 6+ lane instances during the v0.27.15 gates). The council nevertheless
converged: two models, two refinement rounds, both models making concessions against their own
earlier positions, and both reporting read-only conduct with `git status` clean at `dc55da10f`
throughout. The D7 proxy-side repair remains a user-owned item.

**Ready for the APPROVER stage:** NO — READY-WITH-CONDITIONS. C-1…C-4 must fold first; then
W-1…W-14 and A-1…A-9 fold before implementation; then the set is READY for the approver.
No re-plan and no new commission is needed.


---
---

# DELTA RE-REVIEW — fold of C-1..C-4 + W-1..W-14 + A-1..A-9

**Date:** 2026-11-03 · **Folds under review:** `0115f4818` (10 plan docs + `fold-audit.md`) and
`a85abd16e` (`bundle-baseline.json` + `bundle-baseline.measure.js`) · **Branch:** ahead-7 @ `a85abd16e`
**Method:** closure verification only — no re-plan, no re-opening of settled rulings. Every claim below
was checked **in the phase files**, not in `fold-audit.md`; the ledger figures were **executed**, not
re-read.

## VERDICT: **DELTA-CONDITIONS**
**CRITICAL: 0  WARNING: 1  ADVISORY: 2**

**APPROVER-readiness: READY-WITH-CONDITIONS** — one one-token path fix (§ Delta-W-1) must land before
implementation starts. Every other condition from the first review is discharged and **independently
re-verified by execution**. The plan does not need a re-plan, a new commission, or a design change.

---

## 1. The four criticals — closure is REAL (not merely asserted)

| | Required by the review | Present in the phase files? | Evidence |
|---|---|---|---|
| **C-1** | wrapper inside `src/app/pages/metrics/**`; **every** chart.js import dynamic `await import('chart.js')`, **0** static sites; B4 = new chunk min+gz **≤ 65,536 B** AND route fetch set growth **≤ 65,536 B** vs **496,338 B**; first paint = app shell | **YES** (one residual — see Delta-W-1) | `phase4-plan.md:25` — 4.4 creates `frontend/src/app/pages/metrics/charts/chart-panel.component.ts`; `phase1-plan.md:31` (1.2c) gates the **shape**: *"every `chart.js` import site … must be the **dynamic** `await import('chart.js')` form — **0** static `from 'chart.js'` sites anywhere"*; `phase1-plan.md:36` (1.7) fixes the shape as *"the stricter of the two readings"*; `phase9-plan.md:31` (9.8d) carries **both** B4 thresholds and names the comparison leg G4 had promised but never had; `plan-overview.md:723` §10 criterion 8 = *"**first paint = the APP SHELL** (LEADER RULING 1)"* |
| **C-2** | 2.4 split; 2↔4 **TIGHT** in the dependency map **and** both Coupling blocks; phase-2 exit = each of the 6 central tabs **fetches** its families and surfaces availability | **YES** | `phase2-plan.md:23` — 2.4 **SPLIT (C-2)**: the registry hand-off and the per-kind+unit panel composition **MOVE to Phase 4's task 4.15**; `plan-overview.md:347` map row 2 col 4 = **tight** and `:349` row 4 col 2 = **tight**; `phase2-plan.md:45` *"Phase 4 is now **tight**, not independent"*; `phase4-plan.md:45` *"(Phase 2 is now **tight** …)"*; `phase2-plan.md:78`/`:80` phase-2 exit — *"**Each of the 6 central tabs fetches its bound families and surfaces their availability …** **no central tab is an empty shell** (W-10)"* |
| **C-3** | G10 baseline **9** incl. `blueprint.component.scss`; warning **SET** equality (0 added / 0 removed); count ceiling forbidden | **YES** | `plan-overview.md:138` G10 = *"**9 paths** … **`blueprint` 18.32 (the LARGEST)**"*; `plan-overview.md:546` B6 = *"a **SET DIFFERENCE** … captured **mechanically** … **never a count ceiling** (a ceiling can be loosened and silently lose detection)"*; `phase1-plan.md:38` (1.9d) captures the set by `grep -E "scss exceeded maximum budget" <build-log>`, *"never hand-written"*; `phase9-plan.md:73` *"warning **set difference** (0 added, 0 removed against the 9-path baseline)"*; `phase1-plan.md:86` the phase-1 exit records the captured 9-path set |
| **C-4** | P1–P9 ordered: scrub → source worktree `.env` → assert `POSTGRES_DB == ensemble_dev` ≠ prod → re-export `PORT=8079` **unconditionally, after** the source → `python -m daemon` (not `dev.sh`) → assert effective port **from `/livez`** + DB read-back + `ensemble.json`; P3/P4 pinned to `.database == "postgres"` **AND** `.postgres.db == "ensemble_dev"`; L1 non-emptiness so a 200-with-empty-catalog **fails** | **YES** | `plan-overview.md:398-407` the P1–P9 table (P7 *"re-exported **AFTER** the `.env` source and **unconditionally** (LEADER RULING 3)"*; P3 the `jq -e '.database == "postgres" and .postgres.db == "ensemble_dev"'` expression; P4 the exact full `postgres` pre-seed); `plan-overview.md:412-421` the **ORDERED** boot sequence, 6 steps, with the order called *"the fix"*; `phase9-plan.md:24` (9.2) carries the same ordered sequence; `plan-overview.md:463-466` L1 — *"≥ 1 registered family **and** ≥ 1 of the 7 bound families … ≥ 1 series … a **non-zero** `daemon_rss_mb`"*; `plan-overview.md:404` P9 — *"a 200-with-empty-catalog from `/api/metrics/families` is a **FAIL**, not a success"* |

**Verdict: C-1, C-2, C-3, C-4 are all closed as specified, in the phase files, as task-graph entry +
acceptance fixture + numeric threshold.** The C-1 residual is a path-string defect inside the repair,
not a failure of the repair — see Delta-W-1.

---

## 2. The 7 weak/partial landings — all repaired in the task graph

| Amendment → finding | Repaired as | Evidence |
|---|---|---|
| AM-3 → W-2 | **two** end-to-end legs | `phase7-plan.md:17` (7.1) — *"(i) `navigateByUrl('/resources?window=24h&x=1')` … **parsed** `queryParams` … (ii) `navigateByUrl('/resources?tab=charts')` ⇒ the landed URL's **parsed** `queryParams.tab === 'resource'`"*, and *"a raw-string comparison is **not** accepted"* |
| AM-6 → W-13 | **task-graph edges** | `phase9-plan.md:29` — 9.7a `Depends On: 9.3` (auth ON); `:30` — 9.7b `Depends On: 9.5` (auth OFF); `:25` — 9.3's env exports **precede** 9.2's boot; `:48` the risk row now cites the **task graph**, not the prose |
| AM-7 → C-2 | the **class** was swept | `plan-overview.md:358` *"**2 ↔ 4 is TIGHT (C-2)**"* + both Coupling blocks amended (`:45` in phase2 and phase4) + map rows 2↔4 both **tight** — the fix is no longer a named-instance patch |
| AM-10 → W-1 | re-point target + a green-at-freeze criterion | `phase1-plan.md:31` (1.2b) — *"re-pointed to a phase-1-**EXISTING** file that Phase 4 does not delete"*; `phase1-plan.md:74` phase-1 exit — *"**The pins spec is GREEN at the phase-1 freeze** with its re-pointed read path"* — *subject to Delta-W-1* |
| AM-11 → W-3 | the count corrected | `phase4-plan.md:31` (4.10) — *"**four** canvas-fatal assertions in **three** leg-classes"*; `phase4-plan.md:78` the phase-4 exit repeats *"in **three** leg-classes"* |
| AM-15 → C-1 | **shape** is now the gate | `phase1-plan.md:31` (1.2c) — the **dynamic** form, **0** static sites, every site inside the metrics tree; `phase1-plan.md:38` (1.9) — **TWO** planted-violation variants: *"(i) an **eager** import (in the initial set) and (ii) a **static import inside the lazy metrics tree** (the case that slips through (a) and (b) — C-1)"* |
| AM-29 → W-7 | P8 split into two real assertions | `plan-overview.md:405-406` — **P8a** the `config.yaml` **port** stanza (and the disclosure that there is **no** database stanza), **P8b** the running daemon's **actual** database read back |

---

## 3. The ledger is self-proving — I EXECUTED it

**Execution 1 — `bundle-baseline.measure.js` (the named artifact's re-derivation tool):**

```
cd frontend && node ../.agents/shared/planning/metrics-unification/fe-t1-build/bundle-baseline.measure.js dist/frontend/browser
```

| Figure | Plan value | **My executed value** | |
|---|---|---|---|
| G1 initial total | 5,909,018 B | **5,909,018 B** | ✓ |
| G1 file count | 36 | **36** | ✓ |
| G2 headroom | 90,982 B | **90,982 B** | ✓ |
| G3 lazy chunk count | 52 | **52** | ✓ |
| G6 lazy bytes total | 1,702,633 B | **1,702,633 B** | ✓ |
| G4 `/metrics` route chunk | 27,672 B | **27,672 B** (`chunk-ZA3RV4HY.js`) | ✓ |
| G4b route fetch set | 496,338 B | **496,338 B** | ✓ |
| G5 `/resources` route chunk | 38,914 B | **38,914 B** (`chunk-FOLCEMN3.js`) | ✓ |
| G7 largest lazy | 157,141 B | **157,141 B** (`chunk-QS2K3YLH.js`) | ✓ |
| G7 second largest | 135,690 B | **135,690 B** (`chunk-HNKUMJNH.js`) | ✓ |

**Every figure reproduces exactly.** The artifact is real, runnable, and its output is byte-identical to
the `bundle-baseline.json` `reproduce.output` block.

**Execution 2 — the G10 census command, verbatim from `plan-overview.md:105`:**

```
grep -E "scss exceeded maximum budget" <build-log> | sed 's/^▲ \[WARNING\] //'
```

→ **9 paths**, and `diff` against the plan's quoted block is **byte-identical** — including
`blueprint.component.scss` at 18.32 kB, the ninth the original plan omitted.

**The two provenance discrepancies were recorded, not re-fixed:**
- the digest's **89,982 B** is labelled a *"1,000 B digit slip"* (`plan-overview.md:129`, `:553`) —
  with the arithmetic shown (5,909,018 + 89,982 = 5,999,000 ≠ 6,000,000);
- AM-2's **884,180 B / 5,115,820** is labelled a **basis difference** — *"undercounts by 793,198 B (it
  summed only the index.html-referenced files, omitting the builder's 23 transitive static-import
  chunks)"* (`plan-overview.md:554`).
Neither number was quietly adopted; G2 carries **90,982 B**, derived from G1 by the same command.

---

## 4. W-1..W-14 + A-1..A-9 — all land as claimed, **none deferred**

Every one of the 14 warnings and 9 advisories has a disposition row in `fold-audit.md` §C/§D, each
marked CLOSED or FOLDED, and each **verified in the plan files** (spot-verified here: W-1 `phase1:31`/`:74`
· W-2 `phase7:17` · W-3 `phase4:31`/`:78` · W-4 **new task `phase5:34` (5.14) bundle-guard re-run** ·
W-5 `plan-overview:144`/`:730` — **"11/13"** · W-6 `phase1:31` (1.2a) — *"PER-BAN, not per-file"*, all
**five** sources retained, four planted-violation legs · W-7 P8a/P8b · W-8 `plan-overview:401` the exact
pre-seed **and** *"**P3 is re-run after P4**"* · W-9 `plan-overview:400` the `jq` expression ·
W-10 `phase2:78`/`:80` · W-11 `plan-overview:463-466` · W-12 `phase9:76` *"zero diffs"* · W-13 `phase9:29`/`:30`
· W-14 `phase2:22` cell now reads `2.1`).
**A-1..A-9: all nine FOLDED** (verified: A-1 `plan-overview:606` · A-2 `:145` · A-3 `:205` + `phase2:11`
· A-4 `phase4` 4.13 word-bounded · A-5 cite corrected · A-6 P1 no longer asserts `DAEMON_PORT` ·
A-7 `phase4:98-99` the ordering is now recorded in phase 4's exit · A-8 the artifact exists and is read
by the guards · A-9 `phase6`'s exit says five).
The only "deferred" language in the plan set is the **pre-existing** routed register (b), (l), (m) —
**no finding of mine was deferred.**

---

## 5. Nothing was weaponed — thresholds compared against the architect's Required clauses

`fold-audit.md` §A restates all 29 amendments as **T** (task-graph) + **F** (fixture/leg) + **X**
(threshold). I compared each **X** against the architect's own Required clause in
`architecture-review.md`:

| AM | Architect's Required | Folded threshold | |
|---|---|---|---|
| AM-2 | `\|Δ initial-total\| ≤ 2 kB` | the same, verbatim, in `phase1:38`, `phase4:35`, `phase4:94`, `phase9:31` | **unchanged** |
| AM-4 | add `auth_required`(401), `timeout`, `unknown_family`(404), `bad_request`(400) | **10** classes; the 401 `auth_required` class is present at `phase8:26`/`:62` and `plan-overview:469` | **unchanged** |
| AM-13 | coverage displayed **verbatim** | *"the rendered coverage == the payload's `coverage_pct` **verbatim**"* | **unchanged** |
| AM-14 | never `window_s > 7200`; baseline **central tabs only** | `phase3:29` both clauses | **unchanged** |
| AM-26 | **12** exported symbols by name; checklist **6/6** | *"**12** exported symbols enumerated by name; capability checklist **6/6**"* | **unchanged** |
| AM-28 | inline styles **< 8 kB** (24 kB = hard error) | *"each new component's inline styles **< 8 kB** (the 24 kB cap is a hard error)"* | **unchanged** |
| AM-15 | grep pin over resolved import sites | now *"every site inside `src/app/pages/metrics/**`; **any** `import('chart.js')` must be **dynamic**"* | **strengthened** |

A mechanical token sweep across all 29 rows surfaced **no dropped threshold**. The only token the fold
introduced that the architect did not state is AM-28's explicit **"24 kB cap is a hard error"** — a
**strengthening** (the architect mentioned the 24 kB cap in its rationale but not in its Required
clause). **Nothing was loosened to make the fold pass.**

---

## 6. New artifact sanity — `bundle-baseline.json` + `bundle-baseline.measure.js`

- **Real and runnable**: `bundle-baseline.measure.js` (56 L) executes under `node v22.23.2` with no
  dependency beyond `fs`/`path`, and produces the exact JSON quoted in `bundle-baseline.json`'s
  `reproduce.output` (§3, Execution 1).
- **The guards read the artifact, not plan prose**: `phase1-plan.md:38`, `phase4-plan.md:35` and
  `phase9-plan.md:31` all say *"reading the **named baseline artifact** `bundle-baseline.json`"* and
  cite **5,909,018 B** *from the artifact* — A-8's *"a number duplicated only in plan prose is not a
  baseline"* is honoured.
- **The artifact's provenance is sound and self-declared**: `commit: 7961bcb21` with the equivalence
  argument (*"the lane's commits … are docs-only, so HEAD's frontend/ tree == the lane base's
  frontend/ tree"*) — correct; the intervening `a85abd16e` adds only two files inside the plan dir.
- **Builder semantics are cited to source**: `node_modules/@angular/build/src/utils/bundle-calculator.js:155-167`
  — *"InitialCalculator sums the chunks flagged `initial` … index.html is an ASSET, not a chunk"* — which
  is the same basis my independent on-disk sum of the builder's 36 named files reproduced exactly.
- The artifact lives in the **plan dir**, not `scripts/` and not the shipped tree — consistent with
  FE-only discipline.

---

## 7. Honesty check — C-4 is closed on CODE-PATH evidence, and the plan says so

- **The plan states it in those words**: `decisions.md:639` — *"**C-4 was closed on CODE-PATH evidence,
  not on an executed boot**; the three cheap falsification checks are named as implementation-time
  assertions in Phase 9's task 9.2."* `plan-overview.md:424` — *"**C-4's code-path conclusion,
  falsifiable at implementation time**"* — and `:438` *"implementation-time assertions that would
  falsify it (named in Phase 9's task 9.2)"*.
- **The three checks are named**: `plan-overview.md:439-441` — `env | grep POSTGRES` (expect **nothing**
  before the `.env` load) · the boot log line `No PostgreSQL ENV vars detected, defaulting to sqlite`
  (must **not** appear) · `cat $ENSEMBLE_DATA_DIR/ensemble.json` (must show the dev DB).
- **They are specified as MUST-EXECUTE-AND-EVIDENCE, not as already-done**: `phase9-plan.md:60` — the
  evidence must carry *"**C-4 falsification checks recorded with their actual outputs**"*; `phase9:23`
  (9.1) — *"record every assertion's result in the lane evidence"*.
- **No plan file implies the boot was executed.** A sweep for past-tense execution claims
  (`we ran/booted/executed`, `the boot was run`, `verified by running`, `the daemon booted`, …) across
  all 13 plan docs + `fold-audit.md` returns **exactly one hit — inside this review file** (the original
  W-13 text describing the defect). The plan's own verbs are imperative: *"Run the pre-flight fence"*,
  *"Boot the dev daemon"*, *"the **observed** effective port"*, *"the **observed** counts are recorded
  in the evidence"*.
- The fold author's own §G re-verification is a **separate, independent** pass with real commands and
  results — and it does **not** claim a boot.

**Verdict on item 7: HONEST.** No wording anywhere implies the three checks were already run.

---

## 8. Remaining findings

### 🟡 [Delta-W-1] `phase1-plan.md:36` (task 1.7, opening clause) — the stub path contradicts every other cell; the relocation is prose, not a task

**The contradiction.** Task 1.7 opens:

> *"Write the **wrapper contract** as a doc comment in a NEW stub
> `frontend/src/app/components/charts/chart/chart-panel.component.ts` …"*

— the **pre-C-1** path, **outside** `src/app/pages/metrics/**` — and then, later in the same cell,
states:

> *"**The wrapper's final home is `frontend/src/app/pages/metrics/charts/chart-panel.component.ts`** —
> under the `src/app/pages/metrics/**` tree (a **NAMED relocation**, so 1.2(c)'s location rule holds by
> construction; the earlier `components/charts/chart/` home sat **OUTSIDE** it and made the pin
> unimplementable — C-1)"*

Every other cell in the plan set names only the **new** path: `phase1:31` (1.2b — *"re-pointed to a
phase-1-EXISTING file … `frontend/src/app/pages/metrics/charts/chart-panel.component.ts` (**the wrapper
stub 1.7 creates**)"*), `phase1:76`, `phase4:25` (4.4 — *"at its **phase-1 stub's path** (1.7 created it
there)"*), `phase4:74`, `decisions.md:601`. **A path tally over the whole plan set: 8 mentions, 7 on the
new path, 1 (this clause) on the old.** 4.4's cross-reference *"1.7 created it there"* is therefore
**factually false against 1.7's own opening clause**.

**Why it matters.** Taken literally, 1.7 creates the stub at the old path → 1.2(b)'s re-point target does
not exist at the phase-1 freeze → the pins spec throws `ENOENT` → **the phase-1 freeze is red for
exactly the reason W-1 was raised** — i.e. the W-1 repair is defeated by the very cell that creates the
file. And nothing deletes the orphan: 4.12 removes only `components/charts/svg-line-chart/` and
`pages/resources/svg-timeline/`, so `components/charts/chart/chart-panel.component.ts` would ship.
The "NAMED relocation" is a **sentence, not a task** — the one thing the fold-quality rule forbids.

**Why this is a WARNING and not a CRITICAL, and why C-1 stays closed.** The load-bearing D-1 guarantee
does not depend on the stub's path: it is gated by 1.2(c)'s shape+site assertion (**0** static sites,
four planted-violation legs), 4.4's own spec (*"a static form must fail it"*), 1.9's **two** planted
guard variants (eager **and** static-inside-lazy-tree), and B4's **two** numeric thresholds (64 KiB
min+gz; 64 KiB route-fetch-set growth vs 496,338 B). The failure mode is a **loud** phase-1 gate
failure — not a silent wrong ship. The dominant reading (3 cells vs 1 clause) is also the correct one.

**Required change (one token, plus one clause):**
1. `phase1-plan.md:36` — change the stub path in 1.7's opening clause from
   `frontend/src/app/components/charts/chart/chart-panel.component.ts` to
   `frontend/src/app/pages/metrics/charts/chart-panel.component.ts`; **or** state explicitly that 1.7
   creates it at the new path and that 4.4 replaces it in place.
2. Make the relocation a **task-graph entry** rather than a sentence — e.g. 1.7(b) *"the stub is created
   at its final path; no relocation step exists"* — so the fold-quality rule's (a)/(b)/(c) test applies
   to it. If any path outside `src/app/pages/metrics/**` is ever created, name the task that removes it.

### 🟢 [Delta-A-1] `fold-audit.md:148` (§G, C-1 row) — stale post-fold evidence row
§G's C-1 row reads *"3 hits only (1.5 `chart-theme.ts` … 1.7 stub · 4.4 create) — **no relocation
task**"*. That was **true of the pre-fold plan** and is now **stale**: post-fold, 1.7 *asserts* a
relocation (in prose). `fold-audit.md` is the fold's own evidence file, and a reader checking the
current plan against §G will read "no relocation task" as a present-tense finding. **Fix:** amend the
row to *"the pre-fold plan had no relocation task; the fold adds the relocation as prose in 1.7 — see
review Delta-W-1"*, or leave §G as a dated pre-fold record and say so in its heading.

### 🟢 [Delta-A-2] `plan-overview.md:95` / `bundle-baseline.json` — the artifact's `commit` field pins `7961bcb21`, one commit behind HEAD
`bundle-baseline.json` records `"commit": "7961bcb21"` with the equivalence argument. Correct as of
`a85abd16e` (which adds only two plan-dir files), but the field will read as stale the moment the
next commit lands, and the equivalence argument is the only thing making it valid. **Fix:** state the
equivalence rule (docs-only commits ⇒ the `frontend/` tree is unchanged) as the authoritative
precondition, with the commit field as a convenience, or re-stamp it at the lane's next commit.

---

## 9. What I could NOT verify (unchanged in kind from the first review)

- **The runtime behaviour of the fenced dev daemon** — still un-executed by design (C-4 is closed on
  code-path evidence, and the plan says so). The three falsification checks are correctly deferred to
  the functional gate; their execution is the lane's job, not this review's.
- **The e2e legs under canvas** — the spec text was re-verified (four refs, `phase4:31`), but Playwright
  was not run.
- **G8 / G9** — post-implementation by construction; each still carries its named phase-freeze task
  (4.11, 9.8(e)).
- **The `bundle-baseline.json` G10 set** — I verified the G10 **census** by executing the command against
  the lane-base build log; I did not independently re-derive the nine **sizes** recorded in the artifact
  (the byte/kB figures are transcribed from the same log the command reads).

## 10. APPROVER-stage readiness verdict

**READY-WITH-CONDITIONS — one condition.**

**Blocking (must land before implementation starts):**
1. **Delta-W-1** — align task 1.7's stub path with the six other cells that name
   `frontend/src/app/pages/metrics/charts/chart-panel.component.ts`, and state the relocation as a
   task-graph entry rather than a sentence (with a removal task if any out-of-tree path is ever
   created).

**Non-blocking (fold before implementation, not gating):** Delta-A-1, Delta-A-2.

**After Delta-W-1: the set is READY for the approver.** All four criticals are closed and
independently re-verified; all 14 warnings and 9 advisories are landed with no deferrals; no threshold
was loosened; the ledger is executable and self-proving; and the C-4 honesty posture is explicit and
correct. **No re-plan, no new commission, no design change.**
