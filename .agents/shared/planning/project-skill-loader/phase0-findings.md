# Phase 0 Findings — Project Skill Source Loader

Date: 2026-10-31
Author: coder lane (Phase 0 + Phase 1 execution)
Worktree: `/home/nea/ensemble-src-wt-project-skill-loader` @ `b56444244` (branch `feature/project-skill-loader`)
Method discipline: SELECT-only DB access; scratch scripts in `/tmp` only, FAKE_HOME-fenced (`P0_FAKE_HOME` under `/tmp`, escape-guard asserted); per-variable env values from the worktree fence `.env` (never `env -u POSTGRES_*`); zero writes to any DB; zero source-tree modifications during Phase 0; read-only `ls`/`find` against the shared checkout (no mutation).

---

## Named Gate: Collision Census (Q8 falsifier — phase0-plan.md)

### Method
- Case-insensitive comparison of the 5 canon skill names (`.agents/tester/skills/*`) against `skills.name` in every DB accessible under this lane's fence, plus direct id lookups for the 4 known ids (`9e79ccad`, `fd1e01dc`, `76e3e1a7a`, `08e67030`).
- Canon ground truth (git, worktree HEAD `b56444244`): 4 of 5 manifests carry `supersedes` → `08e67030-1c11-498b-a451-04da3e552982` (ab-base-leg-provisioning-artifacts), `76e3e1a7-1a5a-4228-957f-f8e556e04213` (failed-digest-equality-gate), `fd1e01dc-37d5-4a0b-a05e-1933d18a006d` (regression-ab-partition-run), `9e79ccad-1e2c-456b-8762-82d7f5eeec0f` (whole-tree-base-proven-diff). `blast-radius-leg` carries no supersedes (matches research-findings S-b "4/5").

### Results (all local dev DBs reachable from the fence — SELECT-only)

| DB | skills rows | 4 known-id hits | canon-name collisions | in-scope rows (project 83da04de…) |
|---|---|---|---|---|
| `ensemble_dev` (the instructed DB) | 29 (3 projects: `d395d775…`, `71931ae0…`, `4c3b171f…`; all dev-lane skills from 2026-09/10) | **0** | **0** | **0** (project row itself absent) |
| `ensemble_dev_scratch` | — | — | — (no `skills` table) | — |
| `ensemble_test` | — | — | — (no `skills` table) | — |
| `ensemble_wt_reportbugfix` | — | — | — (no `skills` table) | — |

### Outcome + adjudication

- **Superset falsifier: PASS on all accessible data** — zero unexpected canon-name collisions anywhere reachable. The gate's FAIL/hard-stop condition (any collision beyond the 3 known ids) is **NOT triggered**; Phase 1 proceeds.
- **Exact-set assertion (`== {9e79ccad, fd1e01dc, 76e3e1a7a}`): NOT FULLY EVIDENCEABLE WITHIN THIS LANE'S FENCE — carried as a stage-time gate input.** Discrepancy discovered: the instructed DB (`ensemble_dev`) contains **no rows for this project at all** (no `83da04de…` project row, no skills under it). The 3 legacy DB gate-runner skills live in the **live deployment DB** (created via `skill_create` through the live daemon during the tiered-gate-doctrine commission, 2026-10-31; documented across the planning set and project history). The live DB (`ensemble_prod`, remote host) is outside this lane's authorized contact surface ("DB access is SELECT-only against ensemble_dev"; no live-surface contact), so the id-level existence check could not be executed here.
- **Census-dependent count (08e67030):** counted ONLY if its live-DB row's name collides with `ab-base-leg-provisioning-artifacts`. Presumed from the `supersedes` provenance (the canon entry superseded that DB skill), but unverifiable in-fence — folded into the same stage-time live-DB re-check.
- **Routed (stage-time gate input):** one SELECT-only re-check on the live DB by a lane with authorized read access (tester/staging), verifying (a) ids `9e79ccad`/`fd1e01dc`/`76e3e1a7a` exist in-scope, (b) `08e67030`'s name (count rule), (c) zero additional canon-name collisions in-scope. Until then the in-fence superset-falsifier PASS + the git `supersedes` pointers are the census of record.
  - **RESOLVED BY THE PHASE-5 CENSUS + LEADER ADJUDICATION (2026-11-01 — decisions.md Part 10):** the live-DB census executed at Phase 5 (2026-11-01) and found ONE canon-name superset: `regression-ab-partition-run` carried by a single lineage (gen-0 `978ae5cc` inactive → gen-1 `08a4332f` → gen-2 `fd1e01dc`, unresolved A/B pair `2600329e`, identical content). Adjudicated a KNOWN lineage under the amended lineage-aware invariant (STOP only on unknown lineage/name) — correct P4 shadow-by-name behavior; exact-set semantics now: every canon-named live row belongs to a KNOWN lineage AND the shadow set enumerates every ACTIVE row per name. Active shadow set at promote (enumerated): `08a4332f` + `fd1e01dc` (lineage) + `9e79ccad` (whole-tree-base-proven-diff) + `76e3e1a7a` (failed-digest-equality-gate); `blast-radius-leg` has no DB copy. Staging STOP lifted.
- **Phase-1 impact: none** — Phase 1 builds index/validation machinery; it does not depend on the live-DB census. P4 shadowing acts only on collisions that exist at enable time; "fewer than expected" is strictly safer than a superset.

---

## T1 — Canon census sweep across projects

Method: `projects.main_directory` enumeration from the instructed DB (`ensemble_dev` — 49 rows, ALL dev/E2E scratch projects with empty or `/tmp/e2e-*` main_directories) + read-only `find` sweeps of the real known roots from the dispatch context. The full live project registry is behind the same live-DB fence as the census above.

| Root | namespaces (`.agents/*/`) | canon skill dirs | non-canon `skill.json` | Notes |
|---|---|---|---|---|
| `/home/nea/ensemble-src` (agents-ensemble `main_directory`, shared checkout — READ-ONLY) | 10 (approver, coder, designer, devops, project-skills, reviewer, shared, tester, tidier, worker) | **0** | 1 (`.agents/project-skills/bump-version/skill.json`) | checkout is ~277 commits behind (`013895287`, v0.27.8-era) — the 5-skill canon **is not checked out there yet** (shipped at `47212369e`, present in origin/latest + this worktree). Matches the documented worktree-canon-drift acceptance: v1 indexes `main_directory`; the canon reaches the shared checkout when it is synced/advanced, or a dev worktree root via `EXTRA_ROOTS`. |
| `/home/nea/Code/opensource-projects/llm-supervisor-proxy` (related dir — binding predicate only, never a scan root) | 6 | 0 | 0 | no canon |
| `ensemble_dev` projects (49, all scratch) | — | 0 | 0 | `/tmp/e2e-*` dirs, no `.agents` canons |
| This worktree (`ensemble-src-wt-project-skill-loader` @ `b56444244`) | 10 | **5** (`.agents/tester/skills/*`, git-tracked) | 1 (`bump-version` — the mandatory anti-conflation negative fixture) | the v1 canon |

**Answer to "does the gap multiply beyond agents-ensemble today?" — NO.** No other accessible project carries a canon today; the only canon is agents-ensemble's (tracked at this branch's base). The mechanism scales per-project by adoption, but the present-day footprint is exactly one canon, and its `main_directory` copy is pending the shared-checkout sync (a pre-existing, documented host state — see `staged_release_v02715_composition.ceremony_precondition_user_owned` / `promote_state_2026_10_30_v02715.pinned_checkout_note`).

---

## T2 — Cold-scan + stat-gate cost at canon scale

Script: `/tmp/p0-scratch/cold_scan_bench.py` (stdlib-only, FAKE_HOME `P0_FAKE_HOME=/tmp/...` asserted with an escape guard; scan root passed EXPLICITLY — no HOME-derived resolution). Shape mirrors the loader's planned scan: depth-exact `<root>/<ns-dir assumption elided>/skills`… — concretely: enumerate skill dirs, parse 5 manifests, hash manifest+body bytes; stat-gate pass = one `os.stat` per manifest, zero reads.

| Metric | Value |
|---|---|
| Skills scanned | 5 (bodies 4635 / 5062 / 5488 / 7937 / 7136 chars; canon total 33,898 bytes) |
| **Cold scan** | **best 1.200 ms / median 1.323 ms / mean 1.464 ms** (n=20) |
| **Stat-gate pass** | **median 0.066 ms** (n=50, 5 manifests) |
| NFR-PERF-1 headroom (≤2 s) | ~1500× headroom at canon scale |
| **S3 promotion condition (>50 ms cold scan)** | **NOT TRIGGERED — S3 stays SHOULD** (dedicated bounded executor for file reads remains an advisory for phase2 task 2; plain `asyncio.to_thread` suffices at measured scale) |

Manifest/body digests captured per skill (for Phase-1 fixture cross-checks): see the bench output committed in the lane report; stable across runs (content-addressed).

---

## T3 — Embedding regeneration cost

**N/A — provider not wired in the dev fence.** The worktree fence `.env` carries only the DB fence vars (`POSTGRES_*`, `ENSEMBLE_SELF_ENV`, `DAEMON_PORT`) — no `OPENAI_*`/embedding credentials; the embedding provider lives in the live install's configuration, which this lane must not read for credentials or call (no live-surface contact). Per phase0-plan task 3 this is the sanctioned outcome: recorded + noted that the **BM25-only fallback path (stage-2 neutral 0.0) covers tests** — Phase-1 embedding tests inject a fake embedding service; the provider-latency number remains a live-side measurement for a lane with provider access (non-blocking; the architecture treats embeddings as best-effort derived metadata).

---

## T4 — Live DB census of the shadow-retirement set

Executed as part of the Named Gate above (same SELECTs): direct id lookups for the 4 known ids in `ensemble_dev` → **0 rows** (see the gate section for the fence-scope discrepancy and the routed live-DB re-check). The in-fence census table: `ensemble_dev` skills = 29 rows across 3 non-agent-ensemble projects (`test-strategy`, `unit-test`, `quick-fix`, `install-opendesign`, `work-partition`, … families), 0 collisions with canon names.

---

## T5 — `.agents/` tree inventory (agents-ensemble)

From this worktree (identical namespace structure in the shared checkout minus `skills/`):

- Namespaces (10): `approver`, `coder`, `designer`, `devops`, `project-skills`, `reviewer`, `shared`, `tester`, `tidier`, `worker`.
- Canon dirs (`*/skills/*/skill.json`): 5 — all under `.agents/tester/skills/` (ab-base-leg-provisioning-artifacts, blast-radius-leg, failed-digest-equality-gate, regression-ab-partition-run, whole-tree-base-proven-diff).
- Legacy dirs: `.agents/project-skills/bump-version/` — schema `{id, triggers[], default_bump, supported_project_types, …}`, **no `schema_version`, no `body`, no `skills/` segment** → fails BOTH dual-leg exclusion legs (verified by reading the manifest). This is the FR-VAL-1 mandatory negative fixture.
- `agents/*/skills-template/` (innate tier) lives under `agents/`, NOT `.agents/` — structurally outside the scanner glob (positive non-ingestion).
- `.agents/shared/` has no `skills/` subdir today — will be a legitimate ingest target per A12/P6 when populated (positive-fixture class; unit fixture ships synthetically).

Phase-1 fixture design fed: (a) `bump-version` negative on both legs; (b) shared-namespace positive fixture; (c) skills-template non-ingestion; (d) depth-exact glob (nested `<name>/sub/skill.json` reject).

---

## Caveats carried forward

1. **Live-DB census re-check** routed to a stage-time gate input (see Named Gate) — the only Phase-0 assertion not fully closedable in-fence.
2. **Shared-checkout canon absence** is a pre-existing host state (stale pinned checkout), NOT a lane defect; the loader's v1 semantics handle it (graceful zero until the canon appears at `main_directory`; `EXTRA_ROOTS` dev affordance per W-5 for worktree canons).
3. All measurements are canon-scale (5 skills / 34 KB); caps (64 ns / 128 dirs/ns / 16 KB manifest / 64 KB body) bound pathological repos per D-Q2 regardless.
