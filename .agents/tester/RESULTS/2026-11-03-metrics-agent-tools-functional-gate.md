# metrics-agent-tools functional gate — 2026-11-03

**Commission:** TESTER FUNCTIONAL GATE for the metrics-agent-tools lane (item #2 of the ratified
metrics sequence — agent tool surface over central metrics). Three flat tools
(`metrics_families` / `metrics_snapshot` / `metrics_history`) consuming `MetricsQueryProvider`,
with the `metrics:read` scope split delivered and the E-1 contract amendment executed.

**Tree:** branch `feature/metrics-agent-tools` @ `38a210672` (the tidier fold), base `03a2297c3`,
worktree `/home/nea/ensemble-src-wt-metrics-tools`. Binding plan:
`.agents/shared/planning/metrics-agent-tools/` (phase5-plan.md battery + the commission's 9 items).

**Worker instances:** recon `e9ae9c3d` · auth `b86c31a2` · e1 `09a2ce50` · suite `2b4793f0` ·
mutation `1b66086a` · regress `6346edd7` · fe `cb4793c4` (all `worker`; execution legs loaded
`test-pack-execution`). Logs under `/tmp/g{1,2,3,4a,5,6}-*.log`, `/tmp/metrics-read-gate/`,
`/tmp/metrics-fe-gate-{head,base}/`.

---

## Verdict: **FAIL** (one axis)

**G2 scoped dual-side regression: net-new deterministic reds = 2 (lane-caused, single root
cause).** Every other gate leg is green. The lane's blanket "net-new = 0" claim is FALSE at the
final tree for exactly one composition member; the defect is deterministic (3× isolated:
HEAD 3/3 FAIL vs BASE 3/3 PASS) and the remedy is named by the failing pack itself but touches a
shipped daemon file — a lane-owner decision, NOT fixed by this gate (no-fix mandate honored).

### The blocker (route to lane owner / leader)

`tests/unit/tools/test_tool_category_artifact.py`:

| Node | HEAD ×3 | BASE ×3 | Error |
|---|---|---|---|
| `TestToolCategoryArtifact::test_artifact_covers_every_registered_category` | 3/3 FAIL | 3/3 PASS | `AssertionError: categories missing from the artifact: ['metrics-read']` |
| `TestToolCategoryArtifact::test_artifact_is_generated_from_the_tool_registry` | 3/3 FAIL | 3/3 PASS | `AssertionError: tool-category artifact drifted from the registry — regenerate … and copy it over (and bump VOCABULARY_VERSION)` |

**Root cause:** Phase 2 registered the `metrics-read` privileged category
(`daemon/tools/_tool_registry.py:650`); the committed artifact
`.agents/shared/metrics/tool-categories.json` was never regenerated — **derived = 57 categories
(incl. `metrics-read`) vs committed = 56** (byte-identical to BASE, sha-verified). **Remedy (per
the pack's own message):** regenerate into a scratch path
(`ENSEMBLE_REGENERATE_TOOL_CATEGORY_ARTIFACT=/tmp/tool-categories.json`) + copy over, **and bump
`VOCABULARY_VERSION`** (`daemon/checkpoint_metrics.py:208`, currently 2) — the bump touches a
shipped daemon file, so the fix needs the lane's change-set discipline, not a drive-by edit.
**Why the lane missed it:** the file is reachable only via the `metrics_p3_dual_side` widened-13
composition — none of the lane's per-phase D1/D2 declarations put it in a FAILING position
(check: all three per-phase legs recorded net-new 0 at their trees, and the widened-13 ran at
Phase 3 BEFORE the category count moved 16→17 in Phase 2's commit ordering… regardless of
ordering, at the FINAL tree it is red and base-green).

**Routed suggestion (from the G2 leg):** add `test_tool_category_artifact.py` as a standing D1
leg to every privileged-category registration so a future category addition reds at the
registering phase, not at a gate.

---

## Gate-by-gate results

| Gate | Leg | Result |
|---|---|---|
| **G1** | mutation matrix re-run at the final tree (exclusive run; registered pack `metrics_read_mutation_legs`, `timeout 1800`) | **PASS** — **27/27 legs** (26 RED/GREEN + 1 FOLLOW), 396 s. The 53-line per-leg outcome sequence is **byte-identical to the archived log** (sha256 `eb2423eb…`, 4 446 B); only the pre-run manifest's HEAD line differs (`d50b95e3d` → `38a210672`) — expected, not drift. FOLLOW leg MT-22 reproduced `235 → 215`. Tree restored byte-identically (porcelain EMPTY; `daemon/tools/metrics_read.py` sha256 `dd6c406f…` == manifest == archived run-head). **The tidier fold (`a301846ab`+`38a210672`, 13 files, `daemon/` EMPTY) invalidated nothing — the archived log still represents the final source.** |
| **G2+G9** | scoped dual-side regression — **UNION** of the three recorded compositions (fold-ledger §H.2 Tier-2 + §H.3 Tier-1 + §H.4 Tier-1): **70 file arguments** (65 BASE-comparable + 5 D1-only), HEAD `38a210672` vs BASE `03a2297c3` (throwaway detached worktree `/tmp/metrics-gate-base-py`, HEAD venv interpreter, cwd=base), identical commands, per-variable scrubs, `-p no:randomly`, `timeout 1500` + `--timeout=120` | **FAIL** — HEAD **1659 collected / 1649 passed / 10 failed** vs BASE **1504 / 1496 / 8** (node delta 155 = 144 new-pack nodes + 10 auth-matrix extension + 1 routes — reconciles exactly). Tidier rename mapping applied (11 ids / 7 files, `sixteen→seventeen`). 8 common reds **base-proven** (digests identical, path-normalized; `api_module_is_small` numeric-only 5751→5763 on a base-proven red — the lane's +12 `api.py` lines). **Net-new = 2** — see the blocker above. |
| **G3** | the lane's five-pack suite at final counts (registered shell pack `metrics_read_unit_test.sh`, `timeout 900`) | **PASS** — five legs **30 / 51 / 15 / 20 / 28 = 144/144 nodes**, **zero count drift** vs the post-tidier census. RULING-1 disclosure pin present exactly ×1 (`test_the_ring_empty_flag_is_the_ages_heuristic_not_a_fact`; its BITE proven by mutation leg MT-27 in G1). Both persona shapes structurally covered (Persona-A maintenancer 10 + Persona-B wanderer 5 + the no-observation pin 1). |
| **G4** | auth / tool-ladder | **PASS** — (a) HTTP door: `metrics_query_auth_matrix` **35/35** (25 pre-existing + the 10 scope-split extension rows, all four requirement rows (i)–(iv) green: `metrics:read`-only holder 200 on all three query routes, 403 on text-exposition + destructive cleanup, `maintenance:read` holder unaffected, fail-closed 503-before-read ordering). (b) Tool door: the 4-token refusal ladder + its ORDERING, the allow-list exactness ({maintenancer, devops, wanderer} — count 17 + EXACT set + AmA-3 equality), the AmA-2 threaded-`agent_id` leg — all green inside the G3 packs. (c) Over-grant closure: the tool door closes on the CATEGORY allow-list + in-body `agent_id` gate, NOT on HTTP scopes — `maintenance:read` structurally cannot authorize the tools (A8 disclosure verified; the tools carry no 401/403/503 semantics). |
| **G5** | E-1 verification at the final tree | **PASS** — contract-doc pack **21/21 green WITH the amendment**; **never-re-pinned** (`git log 03a2297c3..38a210672 -- tests/unit/metrics/test_central_metrics_contract_doc.py` EMPTY); five-range confinement **CONFINED** (changed lines `[31, 32, 318, 319, 322, 380, 387, 405]`, all inside {31,32}∪[318,322]∪[379,380]∪{387}∪[404,405]); line count unchanged. ⚠️ **Two record findings** (below): the lane's records claim NINE changed lines (measured **eight** — the evidence pack enumerates exactly those eight while saying "nine") and 819→819 (measured **818→818**, two independent counters both sides). Zero behavioral impact. |
| **G6** | FE legs per phase4-plan, both sides explicitly (registered pack `metrics_fe_dual_side_test.sh`, `EXPECTED_BRANCH=feature/metrics-agent-tools BASE_REF=03a2297c3`; BASE leg via `METRICS_FE_PROJECT_DIR=/tmp/metrics-gate-base-fe ALLOW_DETACHED=1`; `npm ci` in the base tree 30 s) | **PASS (comparative)** — D1 **149 (HEAD) / 141 (BASE)**, delta +8 **git-proven** to be exactly the eight new degraded-registry legs (8 added `it(` blocks, 0 removed in `metrics.component.spec.ts`); D2 declared **138/138**; D2 derived **1801/1799/2 IDENTICAL both legs** (the two DST-dependent `settings.component.spec.ts` timezone reds — base-proven, host-season artefact); scoped type-check rc=0; both `ng build` rc=0 (22 budget WARNING lines byte-identical both legs — no lane-attributable budget regression); `package.json` + `package-lock.json` git-diff **EMPTY** and byte-identical base↔HEAD. Mirror + badge confirmed at source: optional `registry_read_ok?: boolean`, DISTINCT `data-test="metrics-registry-degraded"` badge (never the error banner). Raw pack exit 1 on both legs is the base-proven DST pair — disclosed. |
| **G7** | real-PG legs | **NOT REQUIRED** — phase5-plan task 5.1: "No PG row is needed (the lane adds no DB-touching code)"; no PG leg run; the existing `metrics_census_pg_test` row untouched by the lane. |
| **G8** | boot smoke | **NOT REQUIRED** — phase5-plan is Tier-0 (docs + test-only diff; no boot-smoke leg exists in tasks 5.1–5.8); stage-prep records zero live contact / no daemon run / no frozen-binary smoke. No live contact made by this gate either. |
| **G9** | Tier-2 / whole-tree per the plan's tier assignments | **Discharged via G2** — the whole-tree collection remains blocked by the KNOWN base debt (`tests/packs/g7_unique_index_smoke_test.py:121` module-level `sys.exit(0)`), identical on both legs per the lane's disclosure; the scoped-union dual-side FAILED-diff (G2) is the sanctioned Tier-2 replacement. Tier-1 phase results spot-checked against fold-ledger §H.2–§H.4 + evidence-pack §2 (P1 1032/1029/3, P2 813/800/4, P3 493/492/1 — all net-new 0 at their trees): consistent with the recorded evidence. |

---

## Findings routed back (owner: lane / leader)

1. 🔴 **F-1 (the blocker):** tool-category artifact not regenerated with the `metrics-read`
   registration → 2 deterministic net-new reds (details above). Remedy touches
   `daemon/checkpoint_metrics.py` (`VOCABULARY_VERSION` 2→3) + the artifact JSON — lane-owner
   decision: fold into this lane and re-run the G2 leg, or route to the tool-category lane.
2. 🟠 **F-2 (commission premise):** the brief's "coder's last run was 808/809" matches NOTHING —
   `git log --all -S"808 passed"` / `-S"809 collected"` → 0 hits; the only `808` strings are an
   FE pixel budget. The real "1 red = base-proven lane-#1 daemon-diff pin" leg is Phase-3 Tier-1
   **493/492/1 vs 421/420/1** (fold-ledger §H.4). This gate therefore ran the **UNION** of the
   three recorded compositions (70 files — a superset of any single phase reading; the safe
   direction for a net-new diff), and the union is what the verdict rests on.
3. 🟠 **F-3 (lane records, E-1):** "NINE changed lines" (measured **eight**) and "819→819"
   (measured **818→818**) — off-by-one claims in `evidence-pack.md` §4, `fold-ledger.md` §H.4 and
   `stage-prep-checklist.md` §3. The invariants the legs test (confinement, unchanged line
   count, never-re-pinned, green-with-amendment) all hold; the prose counts do not.
4. 🟡 **F-4 (FE pack debt, pack owner):** (a) the `LEG_KIND` probe mislabels the BASE leg as HEAD
   (the D1-marker heuristic is defeated because base already carries
   `src/app/models/metrics.model.spec.ts`) — identify legs by BRANCH/GIT_SHA; (b) the
   `ERROR_SUMMARY` grep (`^(FAILED|ERROR) `) is **vacuous for jest** — it printed "(none)"
   despite 2 failures (jest emits `●` bullets / `FAIL <file>` lines); failing ids are only
   recoverable from the jest artifact logs; (c) the pack's default `METRICS_FE_LOG_DIR` is
   shared — the BASE leg overwrites the HEAD leg's jest artifacts; a per-leg override is
   required for any FAILED-id comparison.
5. 🟡 **F-5 (PACKS.md, fixed by this gate):** the `metrics_query_auth_matrix` count cell was
   stale (25 registered vs **35** actual after this lane's task-3.5 extension) — corrected in
   this gate's PACKS.md update.
6. 🟡 **F-6 (archived manifest, explained/benign):** the archived pre-run manifest is 1-of-4
   stale vs HEAD (`tests/unit/test_board_registration.py` sha — the tidier renamed its node ids
   AFTER the matrix ran). The matrix's actual mutation target (`daemon/tools/metrics_read.py`)
   is byte-identical at the archived run-head and at HEAD, and the G1 re-run reproduced the
   archived 53-line outcome sequence with zero drift — the archived log still represents the
   final source. A future "manifest == HEAD" check should expect 3/4, not 4/4.
7. 🟡 **F-7 (standing-coverage suggestion, from the G2 leg):** make
   `tests/unit/tools/test_tool_category_artifact.py` a standing D1 leg for any privileged-category
   registration change (currently reachable only via the `metrics_p3_dual_side` widened-13).

---

## Scope decision

Commission-scoped functional gate (the 9-item battery). Two adjudications where the brief's
premise and the repo's records diverged: (a) the scoped regression ran the UNION composition
(F-2) rather than a nonexistent "808/809" set; (b) BASE legs ran from throwaway detached
worktrees under `/tmp` (the registered house fence), since the implementer-owned
`/home/ensemble-src-wt-base-metrics` was correctly torn down (council A9). The FE pack required
`EXPECTED_BRANCH`/`BASE_REF` overrides to be usable on this lane's branch (recon F3).

## Fences honored

Per-variable `env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT -u
POSTGRES_PASSWORD -u DATABASE_URL -u PORT` scrubs on every python run (the `POSTGRES_*` glob
form is a silent no-op — never used); worktree-local `.venv` (3.14.7) for HEAD legs, the same
interpreter with `cwd=base` for BASE legs; `-p no:randomly`; dual-layer timeouts (registered
outer caps 300/900/1500/1800 + `--timeout=120` inner or the pack's `PACK_BUDGET_S` self-cap);
no daemon, no ports, no `:9797`/`ensemble_prod`, zero live contact (verified in all logs); both
worktrees porcelain-clean after every leg; the mutation matrix ran EXCLUSIVELY (nothing else
touched the worktree during its in-place mutations); NO fixes applied anywhere — all findings
routed back. TypeScript only via `frontend/`-local tooling inside the pack.

## Runtime summary

recon ~10 min · auth 13 s · e1 ~1 min · suite 31 s · mutation 396 s (exclusive) · regression
~5 min (109 s HEAD + 107 s BASE + isolation/diagnostics) · FE ~4.5 min (npm ci 30 s + 106 s HEAD
leg + 113 s BASE leg). Total gate wall-clock ≈ 45 min across 7 workers (3 waves).

## Disposition

Verdict **FAIL** stood until F-1 was dispositioned by the lane owner. Everything else was
ceremony-ready: artifact-integrity (G1 zero-drift), the shipped surface (G3 144/144 + G4 35/35),
the contract amendment (G5), and the FE fold (G6) were all green at the final tree with this
gate's own evidence.

> **SUPERSEDED by the narrow re-verification below — the F-1 axis is CLOSED and the overall
> verdict is PASS as of 2026-11-03 (later same day). This section stands as the historical
> first-run record.**

---

# Narrow re-verification — the F-1 fix (2026-11-03, later)

**Fix surface:** `fcfa24225..4930908cd` — four commits: `9bb2b0fa2` (artifact + marker +
consequence pins, 7 files +80/−23) → `14b077097` (F-2/F-3 records) → `6df486ab9` (archive
re-run + citations) → `4930908cd` (R-1/R-2 record fold, 3 files +27/−4). Full surface
12 files +255/−53. Workers: narrowA `ae6ac6d4` (pack legs) · narrowB `46d9451c` (read-only
git/archival). Logs `/tmp/na-*.log`, `/tmp/nb-*.log`.

## Verdict: **PASS** — the FAIL's single axis is CLOSED; the lane proceeds to giter staging

| Check | Result | Evidence |
|---|---|---|
| The blocker flip | ✅ PASS | `test_tool_category_artifact.py` **9/9 PASS** (2.70 s) at `4930908cd` — was 3/3 FAIL ×3 at the pre-fix tree. The pack now carries the four `TestArtifactRegenerationAuthority` legs (ambient flag cannot rewrite; committed path refused as output; drift check genuinely fails with the ambient flag; explicit scratch path receives the payload). |
| Generator byte-identity ("never hand-edited") | ✅ PASS | `REGENERATE_ENV = "ENSEMBLE_REGENERATE_TOOL_CATEGORY_ARTIFACT"` (`:57`); regenerate-to-scratch then `diff` vs committed → **exit 0**, sha256 both `1ff31ecc…` — the committed artifact IS the generator's output. |
| Artifact diff shape | ✅ PASS | exactly **3 changed lines**: sorted insert `"metrics-read"` (`mcp_rpc` < `metrics-read` < `midflight`), `count 56→57`, `vocabulary_version 1→3` (the committed artifact was stale by TWO markers — it predated central-metrics-v1's bump; documentary-only per R-2, which is why the drift check could not see it). |
| VOCABULARY_VERSION | ✅ PASS | `VOCABULARY_VERSION: int = 3` at `daemon/checkpoint_metrics.py:218` (exactly one occurrence) + the v3 history docstring entry; SEALED set checked — 35 entries, `checkpoint_metrics.py` NOT pinned ⇒ no re-pin owed or performed. |
| M8 consequence pin bites | ✅ PASS | plant `3→2` → the pin (`tests/unit/metrics/test_metrics_vocabulary_amendment.py::TestVocabularyVersion::test_the_version_is_three`, assert at `:26`) REDs `assert 2 == 3` naming the version → byte-identical restore (sha-verified) → GREEN; porcelain EMPTY after. **Note:** the pin lives in the vocabulary-amendment pack, not the artifact pack — both are inside the union corpus. |
| Union dual-side net-new (the gate's own 70-file composition) | ✅ **net-new EMPTY** | HEAD `4930908cd` **1659 / 1651 / 8** vs BASE `03a2297c3` **1504 / 1496 / 8** — FAILED sets **identical** (`comm -23` and `comm -13` both EMPTY), 6/8 digests byte-identical, the 2 differing are operand shifts only on base-proven reds (api.py line-count operand 5751→5763 + log offsets — already characterized in the first-run record). Reconciliation vs the gate baseline: 1659/1649/10 → 1659/1651/8 = exactly the 2 net-new reds converted green, **0 new nodes**; BASE reproduces the gate baseline exactly. (The coder's attested 1584/1579/5 vs 1397/1392/5 was a narrower composition; this gate's 70-file superset is the verdict basis.) |
| Matrix archive currency (coder-attested leg b) | ✅ PASS | NEW archived log sha256 `a627a8fbe4f210e1176788b42c666b0140e7aa49b49f4fd73959e379ae9a8cbf` (4 446 B, 27 legs, FOLLOW 235→215); manifest sha `d33afa53…`; manifest `head_sha` = `14b077097` — SELF-CONSISTENT (the manifest ships inside `6df486ab9` and cannot contain its own sha; the re-run executed at the `14b077097` tree, as the records state); currency proven: all 4 manifest-pinned files byte-identical `14b077097..4930908cd`, and `6df486ab9..4930908cd` = exactly 3 files, none under `daemon/` or `test/packs/`; `metrics_read.py` sha256 `dd6c406f…` unchanged from the archived run through the final tip. Citations re-pointed at the new sha (9 sites); every `eb2423eb` citation sits in the marked SUPERSEDED chain. |
| Record folds (F-2/F-3/R-1/R-2) | ✅ ALL FOLDED | E-1: "eight lines" + "818→818" in all three records (stale NINE/819 only inside the correction prose that quotes them). F-2: the 808/809 premise corrected-by-absence prose landed in evidence-pack + fold-ledger. R-1: `tests/helpers/metrics_exposition_baseline.py:43` now "The FOUR allowlisted value-delta carriers" with the 4-key ALLOWLIST + the exact-set pin at `test_metrics_query_routes.py:380-396` (the carrier count, NOT the three tool bodies — `metrics_read.py`'s "ALL THREE tool bodies" is correct and correctly untouched). R-2: documentary-only statement + all five exact bindings verified in-tree (`test_tool_category_artifact.py:205/:285/:290`, `test_metrics_vocabulary_amendment.py:26`, `test_metrics_family_adjudication.py:419`); currently consistent 3=3. |

## Residuals routed (not blocking)

1. 🟡 **fold-ledger.md:350** still says the archived manifest records HEAD `d50b95e3d` — stale
   when `6df486ab9` re-archived (the same sentence's cited manifest actually reads
   `head_sha 14b077097`). One-token doc fold (`d50b95e3d` → `14b077097`); the token belongs
   only in the SUPERSEDED chain. Owner: the lane's next docs commit or the giter sweep.
2. Stays routed (unchanged from the first run): the LESSON-class item to bind the artifact's
   `vocabulary_version` field to the code constant in the artifact pack; the FE pack debt trio
   (`LEG_KIND` mislabel, vacuous jest `ERROR_SUMMARY`, shared `METRICS_FE_LOG_DIR`).

## Overall verdict (supersedes the first run's FAIL)

**PASS.** The single FAIL axis (G2 net-new ≠ ∅) is closed with independently re-verified
evidence at the fixed tip; every other leg was already green and is unaffected by the fix
surface (which touched no `test/packs/` and no `daemon/` file other than
`daemon/checkpoint_metrics.py`, whose only change is the marker + docstring — sealed-checked,
not pinned). The lane proceeds to giter staging.

