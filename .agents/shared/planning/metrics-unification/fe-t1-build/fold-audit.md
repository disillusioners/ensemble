# FOLD AUDIT — fe-t1-build plan set (fold #3: the plan-review council)

Date: 2026-11-03 · Author: plan author (giter lane), commission wi-32a99618
Status: **the machine-checkable landing ledger.** The approver reads this first.

**Why this artifact exists.** The plan-review council's systemic finding was:
*"the prose landed; the task-graph, acceptance fixture, or numeric threshold did
not"* — 4 of 7 weak landings were 🔴, and **a grep for "AM-n present" does not
detect it**. The leader's binding fold-quality rule therefore requires every
landing to carry **(a) the task-graph entry · (b) the acceptance fixture/leg ·
(c) the numeric threshold**. This file is where that triple is recorded, per
amendment and per finding, so it can be checked mechanically.

**Reading the rows.** `T` = the task-graph entry (the phase file + task id +
its `Depends On` cell). `F` = the acceptance fixture / spec leg / planted
violation. `X` = the numeric threshold or the named predicate. `§` = the plan
section. `Status` = **FULL** (all three landed) · **REPAIRED** (weak at
fold #2, now repaired — the repair row carries the triple) · **n/a** (the
finding names no threshold).

---

## A. The 29 prior amendments (AM-1…AM-29) — re-stated as task-graph + fixture + threshold

| AM | T (task-graph entry) | F (fixture / leg) | X (threshold / predicate) | § | Status |
|---|---|---|---|---|---|
| **AM-1** | `phase2` 2.11 (Depends On 2.4) · `phase5` 5.10 (Depends On 5.2) · `phase8` 8.2 (Depends On 8.1, Phase 2) · `phase9` 9.5 (leg L4) | 2.11's spec (a failing central source does not blank a custom tab) · 5.10's spec · PX-8 (tab 1 lit + central actionable, same capture) | **0** central-door failures may blank the custom slot; PX-8 asserts tab 1 shows live values **and** no auth panel | §4.2, §6.2 | **FULL** |
| **AM-2** | `phase1` 1.9 (Depends On 1.1) · `phase4` 4.14 · `phase9` 9.8 | planted **eager** import variant → guard red; 4.14's re-run | **\|Δ initial-total\| ≤ 2 kB** vs the frozen 5,909,018 B; **0** `chart.js` markers in any initial chunk; **0** import-site violations | §6.3, §0.2 | **FULL** → **+REPAIRED by C-1** (the guard set gains the route fetch set + the shape gate) |
| **AM-3** | `phase7` 7.1 (Depends On Phase 2, Phase 8) | end-to-end `router.navigateByUrl` asserting the **parsed** `queryParams`; the null-fallback leg | `tab=resource` **overrides** an incoming `tab`; all **other** params preserved | §3 (D-3) | **REPAIRED → W-2** (the `?tab=charts` leg was un-gated; now added) |
| **AM-4** | `phase2` 2.10 (Depends On 2.4) · `phase8` 8.1 (Depends On Phase 2) · `phase9` 9.4 (leg L1b) | the closed-union class table spec (an unhandled status is a spec failure); L1b's capture | **10** classes; Retry present **only** for `network`/`unexpected`/`scope_insufficient` | §6.1 | **FULL** |
| **AM-5** | `phase9` 9.1 (P7) · 9.2 (Depends On 9.1) | the effective-port read-back from `/livez` | the effective port **== 8079**; `DAEMON_PORT` inert (absent from `daemon/`) | §6.1 | **FULL** → **+REPAIRED by C-4** (the ORDER was wrong: PORT must be re-exported after the `.env` load) |
| **AM-6** | `phase9` 9.7a (Depends On **9.3**) · 9.7b (Depends On **9.5**) | PX-1…7 + PX-9…14 (auth ON) vs PX-8 (auth OFF) | `/livez` re-assertion after each restart | §6.2 | **REPAIRED → W-13** (the split was prose-only; the task graph forced the auth-OFF restart FIRST) |
| **AM-7** | `phase7` 7.1 `Depends On: Phase 2, **Phase 8**` · `phase8` Coupling block · `plan-overview` §5 map row 8 | — (a coupling/edge declaration, not a fixture) | map row 8 cols 4/6 = **tight** | §5 | **REPAIRED → C-2** (the class was not swept: 2↔4 independence was false) |
| **AM-8** | `phase2` 2.1 (Depends On Phase 1) · 2.3 (Depends On 2.1) | the manifest-shape spec (each HIDDEN module binds the no-family variant) | **4** dispositions type-expressible; **0** empty-array sentinels | §4.3 | **FULL** |
| **AM-9** | `phase2` 2.4 · `phase3` 3.7 (Depends On 3.4) · `phase4` 4.3 | the §4.2 surface table; the renderer specs | the mini role renders **no** axis and has **no** T1 page surface | §4.2 | **FULL** |
| **AM-10** | `phase1` 1.2(a)(b) · `phase4` 4.9 (Depends On 4.1) | the pins spec's own green/red at the phase-1 freeze; the scoped-type-check collected set | the re-point target **exists in the tree at the phase-1 freeze** | §6.3 | **REPAIRED → W-1** (the re-point target was created only at 4.1 ⇒ the spec was RED at the phase-1 freeze) |
| **AM-11** | `phase4` 4.10 (Depends On 4.4) | the three canvas-fatal legs re-specified + the "the `d` assertion survived" check that must fail | **four** canvas-fatal assertions in **three** leg-classes | §6.2 | **REPAIRED → W-3** (the count said three against four refs) |
| **AM-12** | `phase6` 6.1 (Depends On Phase 5, Phase 4) · 6.3 (Depends On Phase 4) | the two planted variants (one mixed axis; a count series on the memory panel) | **3** unit classes; the toggle path covers **5** series | §4.2 | **FULL** |
| **AM-13** | `phase6` 6.5 (Depends On Phase 5, 6.4) | the spec asserting the FE performs **no** coverage arithmetic | the rendered coverage == the payload's `coverage_pct` **verbatim** | §4.2 | **FULL** |
| **AM-14** | `phase3` 3.4/3.5/3.6 (Depends On 3.3) · `phase5` 5.4 | three request-time legs + the "no `/api/resources/*` request outside `ResourcesWindow`" leg | **no** request may carry `window_s > 7200`; the baseline is **central tabs only** | §4.2 | **FULL** |
| **AM-15** | `phase1` 1.2(c) (Depends On 1.7) · 1.7 (Depends On 1.5) · `phase4` 4.14 | the import-site grep pin; the planted variants | every `chart.js` import site inside `src/app/pages/metrics/**`; **any** `import('chart.js')` must be **dynamic** | §6.3 | **REPAIRED → C-1** (the location pin contradicted the wrapper's real path and the SHAPE was not gated) |
| **AM-16** | `phase3` 3.10 (Depends On 3.3, 3.4) · `phase5` 5.11 (Depends On 5.3, 5.4) | the single-request invariant leg; the in-flight-discard leg | a window change issues **exactly one** series request; the live poll is **not** gated by the window generation | §6.1 | **FULL** |
| **AM-17** | `phase3` 3.4 · 3.12 | the faked-client-clock-offset leg | the split point is **unchanged** under a faked clock offset | §6.2 | **FULL** |
| **AM-18** | `phase3` 3.11 (Depends On 3.3) · `phase6` 6.7 | the 2 h spec leg | the printed interval == the **derived** spacing (span ÷ point count) | §6.2 | **FULL** |
| **AM-19** | `phase2` 2.12 (Depends On 2.2) · `phase3` 3.1 · `phase5` 5.4 | the "one `/history` request, none to `/live`/`/self-cost`" leg | `allowedWindows ⊆ ResourcesWindow` (typed / spec-enforced) | §6.2 | **FULL** |
| **AM-20** | `phase8` 8.11 (Depends On 8.1) · 8.12 (Depends On 8.1) · `phase2` 2.13 (Depends On 2.1) | the two 403 branches pinned | a 403 **without** `SCOPE_INSUFFICIENT` ⇒ `forbidden_other`, **never** `scope_insufficient` | §6.1 | **FULL** |
| **AM-21** | `phase8` 8.5 (Depends On 8.2) | the spec asserting the shell dot class is unchanged by a door failure | the global dot stays bound to `/api/health`; the posture is a **page-scoped chip** | §6.2 | **FULL** |
| **AM-22** | `phase7` 7.6 (Depends On 7.1, 7.4) · 7.7 · 7.8 | the classified sweep output recorded in full | **0** class-(c) stale-route hits; **0** stale operator instructions | §7 | **FULL** |
| **AM-23** | `phase2` 2.8 · `phase4` 4.13 · `phase5` 5.13 | the pinned greps (pattern + path set + excludes + allowlist row) | **0** hits outside the allowlist row | §0.3 | **FULL** → **+A-4** (the 4.13 pattern was over-broad → tightened) |
| **AM-24** | `phase1` 1.4 (Depends On 1.1) | the repo-wide grep for the zero-dep claim | **6** sites amended/recorded-historical; `CHANGELOG.md:141` unamended | §0.3 | **FULL** → **+A-5** (one line cite drifted → corrected) |
| **AM-25** | `phase1` exit | the by-name dependency assertion | the `package.json` diff carries exactly `chart.js` + `jest-canvas-mock` **by name** | §6.3 | **FULL** |
| **AM-26** | `phase4` 4.1 (Depends On Phase 1) · 4.2 | the **per-assertion disposition table** (port / re-point / drop-with-reason) | **12** exported symbols enumerated by name; capability checklist **6/6** | §6.3 | **FULL** |
| **AM-27** | `phase4` 4.12 · `phase5` 5.1 / 5.9 | — (a migration-ordering declaration) | the two-step re-point is stated and **recorded in phase 4's exit criterion** | §5 | **FULL** → **+A-7** (the cross-reference was missing) |
| **AM-28** | `phase4` 4.11 (Depends On 4.3, 4.4) · `phase9` 9.8(e) | the per-component figure recorded | each new component's inline styles **< 8 kB** (the 24 kB cap is a hard error) | §6.3 | **FULL** |
| **AM-29** | `phase9` 9.1 (P6, P8) · 9.11 (Depends On Phase 9) | the DB read-back from the running daemon | the scrub covers the **family rule** (prefix `POSTGRES_*` + `PG*`, `BLUEPRINT_*`, suffix `*_API_KEY`); the live **uptime is MONOTONE** | §6.1 | **REPAIRED → W-7** (P8 asserted a `config.yaml` database stanza that **does not exist**) |

---

## B. The 4 criticals — closure

| C | Required (review) | T (task-graph entry) | F (fixture / leg) | X (threshold / predicate) | § | Status |
|---|---|---|---|---|---|---|
| **C-1** | (a) resolve the pin/shape contradiction · (b) make the **shape** a gate · (c) give B4 a threshold **and** a baseline (G4 promoted from orphan) · (d) measure the **route fetch set** · (e) add a **lazy** planted-violation variant · (f) disambiguate D-1's "first paint" | `phase1` **1.2(c) + 1.7 + 1.9** (Depends On 1.1) · `phase4` **4.4 + 4.14** · `phase9` **9.8(a)–(d)** | the import-site grep pin; the **lazy** planted variant (a static `from 'chart.js'` inside the lazy metrics tree → guard red); the route-fetch-set measurement | the wrapper's home is **under `src/app/pages/metrics/**`** (a named relocation task); **every** `chart.js` import is the **dynamic** `await import('chart.js')` form; B4: the new chart chunk **≤ 65,536 B** (64 KiB) min+gz **AND** the `/metrics` route fetch set grows **≤ 65,536 B** vs the **496,338 B** baseline | §3 (D-1, "first paint" = **app shell**), §6.3, `bundle-baseline.json` | **CLOSED** (LEADER RULING 1) |
| **C-2** | split 2.4 (the registry arrives in Phase 4) · re-declare 2↔4 **tight** in the map **and** both Coupling blocks · add the fetch/render requirement to phase 2's exit · re-assert the two-panels property at phase 4's exit | `phase2` **2.4** (split: the bodies + fetch + availability stay; the registry hand-off and the panel composition MOVE to Phase 4) · `phase4` **4.15** (new: the per-tab panel composition) | phase 2's exit: each of the 6 central tabs **fetches** its bound families and surfaces their availability (live panels, or the family's reason when dark) — no central tab is an empty shell; phase 4's exit: the mixed-kind family yields **two** panels in the per-tab bodies (2.4's assertion, re-asserted) | the map's 2↔4 cells read **tight** in both directions; 2.4's `Depends On` drops the registry | §5, §4.2 | **CLOSED** (LEADER RULING 2) |
| **C-3** | G10 → **9** with all nine paths + sizes · B6 as a **mechanically-captured set difference** · capture the baseline at the Phase-1 freeze · **generalise**: every "(a) re-derived at lane base" ledger row must carry the exact COMMAND + its OUTPUT | `phase1` **1.9** (adds the baseline capture) · `phase9` **9.8(f)** (the set difference) | the build-log capture command; the set-difference check | the warning set must equal the baseline set **exactly** — **0 added, 0 removed**; a **count ceiling is FORBIDDEN** | §0.2 (G10 + the command/output discipline), `bundle-baseline.json` | **CLOSED** |
| **C-4** | name the `.env`-load step · re-export `PORT=8079` **after** it · P4's pre-seed contents or drop it · assert the effective DB identity · a 200-with-empty-catalog response must **fail** L1 | `phase9` **9.1 (P1–P9)** · **9.2** (Depends On 9.1) · **9.3** (Depends On **9.2**) | the three **implementation-time** falsification checks: `env \| grep POSTGRES` (expect nothing before the load) · the boot log line `No PostgreSQL ENV vars detected, defaulting to sqlite` · `cat $ENSEMBLE_DATA_DIR/ensemble.json`; the L1 non-emptiness leg | the ordered steps: scrub → source the worktree `.env` → assert loaded `POSTGRES_DB == ensemble_dev` **≠** `ensemble_prod` → **re-export `PORT=8079` unconditionally AFTER the source** → boot `python -m daemon` → assert the effective port from `/livez` + the DB read-back + the `ensemble.json` assertion; P3/P4 pinned to `.database == "postgres"` **and** `.postgres.db == "ensemble_dev"`; L1 requires **≥ 1** registered family, **≥ 1** of the 7 bound families, **≥ 1** F9 snapshot series, and a **non-zero** `daemon_rss_mb` | §6.1, §6.3 | **CLOSED on CODE-PATH evidence** (LEADER RULING 3) |

---

## C. The 14 warnings — closure one-liners

| W | Closure (T · F · X) | Status |
|---|---|---|
| **W-1** | `phase1` **1.2(a)(b)**: the pins spec's disk-read target becomes a **phase-1-existing** file Phase 1 creates and Phase 4 does not delete (`chart/chart-panel.component.ts`) — the spec is **green at the phase-1 freeze**; the banned-list scan scope and the read target are the **same file**. | CLOSED |
| **W-2** | `phase7` **7.1** adds the second end-to-end leg `navigateByUrl('/resources?tab=charts')` ⇒ the **parsed** `queryParams.tab === 'resource'`. | CLOSED |
| **W-3** | `phase4` **4.10** + the phase-4 exit now say **four** canvas-fatal assertions in **three** leg-classes and name all four refs (`:277`, `:281-288`, `:290-296`, `:298`). | CLOSED |
| **W-4** | The guard coverage is restated as **"guards at phases 1, 4, 5 and 9"** (W-4 adds phase 5, the other chunk-graph-changing phase) and a bundle-guard re-run is added to **phase 5's** exit criterion. | CLOSED |
| **W-5** | §10 criterion 15 → **"11/13"** with the reviewer's wording; G11's **dual** (a)+(b) status is marked inline. | CLOSED |
| **W-6** | `phase1` **1.2(a)**: the ban-list scan keeps **all five** sources for the four remaining bans (**per-ban**, not per-file); a planted **`dayjs`** violation leg is added in `charts-tab.component.ts`. | CLOSED |
| **W-7** | P8 splits into **P8a** (`config.yaml`'s effective **port** stanza → 8079; there is **no** database stanza — `grep -i "postgres\|database" config.yaml` = 0 hits) and **P8b** (the running daemon's **actual database** read back → `ensemble_dev` — the load-bearing half). | CLOSED |
| **W-8** | P4's pre-seed gets its **exact contents** (`{"database":"postgres","postgres":{…,"db":"ensemble_dev",…}}`) and **P3 is re-run after P4** with the same expression. | CLOSED |
| **W-9** | P3/P4 are pinned to the **JSON path**: `.database == "postgres"` **and** `.postgres.db == "ensemble_dev"` — never a literal `ensemble_prod` substring (the live file's `$.postgres.db` is `"ensemble"`, which a substring checker passes). | CLOSED |
| **W-10** | `phase2`'s exit criterion gains: *"each of the 6 central tabs fetches its bound families and surfaces their availability (live panels, or the family's reason when dark — ADJ-vi); no central tab is an empty shell."* | CLOSED |
| **W-11** | `phase9` **9.3 (L1)** gains the **non-emptiness** thresholds: ≥ 1 registered family, ≥ 1 of the 7 bound families, ≥ 1 F9 snapshot series, a non-zero `daemon_rss_mb`; the observed counts are recorded. A 200-with-empty-catalog **fails** the leg. | CLOSED |
| **W-12** | `phase9` **9.3** states the mechanism as the four `ENSEMBLE_AUTH_*` **env vars** (`daemon/config.py:6867-6869` overrides the yaml's `enabled: false`) — **no tracked file is edited**; phase 9's exit gains "`config.yaml` and the worktree `.env` have **zero diffs** after 9.3". | CLOSED |
| **W-13** | The auth task graph is repaired: **9.3's env exports precede 9.2's boot** (9.2 depends on 9.3); **9.7a `Depends On: 9.3`** (auth ON captures) and **9.7b `Depends On: 9.5`** (the auth-OFF capture). | CLOSED |
| **W-14** | `phase2` **2.3**'s `Depends On` cell is corrected from "2.1, 2.1" to **"2.1"**. | CLOSED |

---

## D. The 9 advisories — disposition

| A | Disposition | Detail |
|---|---|---|
| **A-1** | **FOLDED** | §7's preamble now reads *"The lane does not build any of these — **except (n), which Phase 7 retires**"*. |
| **A-2** | **FOLDED** | G11's row carries the **dual** status inline ("(a) **and** (b)"); the §0.2 split reads **9 (a) / 2 (b) / 1 not-a-gate / 1 code-facts** (G11 counted once, in (a), with its (b) noted). |
| **A-3** | **FOLDED** | §2's "registration-only" claim is qualified: after AM-9/AM-12 the unit vocabulary is **metric-name-keyed**, so a new metric name renders **"unit unknown"** rather than being free. |
| **A-4** | **FOLDED** | `phase4` **4.13**'s residue grep is tightened to a **word-bounded** pattern (`\b3600\b`) instead of the bare `"3600"` (which over-matches `36000` / `3.600`). |
| **A-5** | **FOLDED** | `phase1` **1.4(iii)**'s cite is corrected: the "npm dependency delta is empty" string is at `central-metrics-v1/phase6-plan.md:25` (task **6.7**), not `:23`. |
| **A-6** | **FOLDED** | P1 no longer requires `DAEMON_PORT=8079` (inert — absent from `daemon/`); the clause is replaced by the **`PORT`** assertion (the effective port, read back from `/livez`). |
| **A-7** | **FOLDED** | `phase4`'s exit criterion now **records the ordering decision** (which of the two orderings Phase 4/5 took for the engine deletion vs the module relocation) — the cross-reference Phase 5's risk row claims. |
| **A-8** | **FOLDED** | The **named baseline artifact** now exists: `bundle-baseline.json` (machine-readable, the single source the guards read) + `bundle-baseline.measure.js` (the re-derivation tool, in the plan dir). The 5,909,018 B figure is no longer duplicated prose only. |
| **A-9** | **FOLDED** | `phase6`'s exit criterion says **five** substantive fixes, not four. |

**None deferred.** All nine advisories were cheap and are folded; no advisory
needed an owner or a reason record.

---

## E. The two leader rulings + the fold-quality rule (recorded as decisions)

| Ruling | Folded as | Where |
|---|---|---|
| **LEADER RULING 1** (C-1f): "first paint" = **APP SHELL** (normative — matching §10 criterion 8's operationalisation and the builder's `initial` budget basis). The page-scoped import-graph marker assertion is **retained anyway**, so the D-1 guarantee holds under **both** readings. The Pin + B4 threshold + the G4 orphan-row fix land per the review's required changes. | `decisions.md` Part V; `plan-overview` §3 (D-1), §6.3 (B3 + B4) | decisions.md, plan-overview §3/§6.3 |
| **LEADER RULING 2** (C-2): severity accepted as 🔴; the fix set is **identical** under both readings — proceed; no re-litigation. | `decisions.md` Part V | decisions.md |
| **LEADER RULING 3** (C-4): the fence is **independently confirmed safe** (the worktree `.env` is mode 600, carries `POSTGRES_HOST=127.0.0.1`, `POSTGRES_PORT=5432`, `POSTGRES_DB=ensemble_dev`, `POSTGRES_USER=ensemble_dev_app`, a dev `POSTGRES_PASSWORD`, `DAEMON_PORT=8079` (**inert**), and **no `PORT` at all** ⇒ no live-port binding path exists). STILL REQUIRED: the `PORT=8079` re-export must be **unconditional and POST-source** (never inherited from a sourced env), and the effective port must be asserted **from `/livez`**, not from the intent. | `phase9` 9.1 (P7) · 9.2 · `plan-overview` §6.1 | phase9, plan-overview §6.1 |
| **FOLD-QUALITY RULE** (binding): every landing must carry (a) the task-graph entry, (b) the acceptance fixture/leg, (c) the numeric threshold. Prose-only landings are not acceptable; a grep for "AM-n present" does not count. | **this file** is the machine-checkable record; every phase file's exit criterion now names its T/F/X triple | fold-audit.md + all phase files |

---

## F. The disclosure to carry (plan §0 / the release-narrative section)

> **The plan-review council's requested model `decision` is NOT in this
> deployment's allowed set** (`agentic`, `coding` only), so the governor
> **mapped it to `coding`** — the alias recorded in project metadata as the
> **known-poisoned upstream row** (D7: a deprecated-row drop, ~210 errors/day,
> which killed 6+ lane instances during the v0.27.15 gates). **The findings
> STAND**: all 4 criticals were independently re-verified by direct commands
> against the tree, 2 models converged over 2 refinement rounds with mutual
> concessions, and the fold author independently re-verified C-1/C-3/C-4/W-7/W-9
> against the tree as well (§G). **The mapping fact belongs in the disclosure
> set and on the user's morning list**; the **D7 proxy-side repair remains
> user-owned**.

---

## G. The fold author's own independent re-verification (this fold)

Every claim below was re-derived **in this worktree** (not taken from the
review):

| Claim | Command | Result |
|---|---|---|
| C-3 — the `anyComponentStyle` census | `grep -cE "scss exceeded maximum budget" /tmp/fe-t1-build-prod.log` | **9** (the plan said 8) — the ninth is `src/app/pages/blueprint/blueprint.component.scss` at **18.32 kB**, the largest offender |
| C-3 — the nine paths | `grep -E "scss exceeded maximum budget" /tmp/fe-t1-build-prod.log \| sed 's/^▲ \[WARNING\] //'` | 9 paths captured verbatim into `bundle-baseline.json` |
| C-4 — no `.env` loader on the daemon path | `grep -rnE "dotenv\|load_dotenv" daemon/ --include=*.py` | **2 hits, both prose in docstrings** (`daemon/config.py:3698`, `:3897`) — **no loader** |
| C-4 — the auto-detect + persist | `sed -n '60,96p' daemon/ensemble_config.py` | `:69-71` the file decides the engine kind · `:81-83` auto-detect needs **both** env vars · `:90-91` *"No PostgreSQL ENV vars detected, defaulting to sqlite"* · `:95` `config.save()` **persists** it |
| C-1 — no task relocates the wrapper | `grep -n "components/charts/chart" .agents/shared/planning/metrics-unification/fe-t1-build/*.md` | **PRE-FOLD (true then, STALE now):** 3 hits only (1.5 `chart-theme.ts` — a different file · 1.7 stub · 4.4 create) — **no relocation task** ⇒ the wrapper's home stayed outside the `src/app/pages/metrics/**` allowlist. **POST-FOLD (W-1, the repair):** **1.7 creates the stub AT THE CANONICAL PATH** `frontend/src/app/pages/metrics/charts/chart-panel.component.ts` — a named **task-graph entry** (Depends On + acceptance), not a sentence — and **4.12's cleanup sweeps the retired out-of-tree path** `frontend/src/app/components/charts/chart/`, with the **numeric orphan assertion: 0 files** under it after the phase-4 cleanup. **Numeric assertion (W-1b): 0 files** under `frontend/src/app/components/charts/chart/` may exist at the phase-1 freeze either |
| C-1 — the route fetch set | `bundle-baseline.measure.js` (the route chunk + its **direct** static deps) | `/metrics` route chunk 27,672 B + `chunk-6Y6F4JKW.js` 43,688 B (**LAZY-ONLY**) + `chunk-AOZCSBSC.js` 1,348 B (**LAZY-ONLY**) + 6 initial deps ⇒ **496,338 B** |
| W-7 — `config.yaml` has no DB stanza | `grep -in "postgres\|database" config.yaml` | **0 hits** |
| W-9 — the live `ensemble.json` shape | `cat <live-install>/data/ensemble.json` | `$.database == "postgres"`, `$.postgres.db == "ensemble"` — **neither** `ensemble_dev` **nor** `ensemble_prod` ⇒ a literal-substring checker passes on a prod-adjacent file |
| W-12 — auth env-over-yaml | `sed -n '6860,6872p' daemon/config.py` | `ENSEMBLE_AUTH_ENABLED` overrides the yaml's `enabled` |
| W-14 — 2.3's duplicated cell | `awk -F'\|' '/^\| 2\.3 \|/' phase2-plan.md` | cell 4 = `2.1, 2.1` |
| W-6 — the pins spec's scanned sources | `sed -n '60,95p' frontend/src/app/pages/metrics/metrics.bindings.pins.spec.ts` | **5** sources: `overview-tab.component.ts` (`:25`) · `charts-tab.component.ts` (`:26`) · `metrics.component.ts` (`:75`) · the engine file (`:76-88`) · `metrics.service.ts` (`:89-90`) |
| A-5 — the cite drift | `grep -n "npm dependency" .agents/shared/planning/central-metrics-v1/phase6-plan.md` | the string is at **`:25`** (task 6.7), not `:23` |
| G1–G7 — the whole ledger | `cd frontend && node ../.agents/shared/planning/metrics-unification/fe-t1-build/bundle-baseline.measure.js dist/frontend/browser` | the JSON in `bundle-baseline.json` → `"reproduce"."output"` — **verbatim output**, one command |
