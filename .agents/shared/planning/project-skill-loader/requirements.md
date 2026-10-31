# Requirements: Project Skill Source Loader

Date: 2026-10-31
Author: planner[v2] via requirements-analysis worker
Status: Draft (Ready for Technical Analysis)
Source Request: Project skill source loader — make file-backed project skills (.agents/*/skills/) a first-class SOURCE in the skill system (searchable, loadable, project-scoped), with git as the distribution/evolution channel. v1 canon shipped (.agents/tester/skills/, 5 dirs). v2 = loader.

## Stakeholders

- **Requester:** User (via Discord, 2026-10-31) + leader ratification of the design principles
- **Affected users:** Every agent working in a project that adopts the `.agents/*/skills/` canon; scales per project (ensemble works on many projects)
- **Affected systems:** Skill subsystem (`daemon/services/skill_search_service.py`, `skill_injection_service.py`, `skill_load` chain, `daemon/tools/skill_tools.py`), project resolution (`daemon/tools/instance.py`, `daemon/repositories/project/repository.py`), config (`daemon/config.py`), `daemon/manager.py` wiring (skill-service construction :2222-2276, :2426, :3951-3987), agent-facing search/load tools, REST `/api/skills`, RAG knowledge base
- **Affected agents:** All `skill_injection=true` agents (approver, architect, blueprinter, critic, doc-maintainer, maintenancer, planner, reviewer, sketcher, tester, tidier, worker + v2 variants) consume search results; tester owns the v1 canon

---

## 1. Objective and Problem Statement

### 1.1 Problem

The skill system has exactly two tiers today:

1. **File-backed but AGENT-GLOBAL:** `agents/{name}/skill-set.yaml` + `skills-template/` → Skill Bank seeding at boot (`manager.py:3951-3987`, `skill_seed_service.py`). Bodies are COPIED into `skill_bank.content` (the anti-pattern P1 forbids for us).
2. **Project/global-scoped but DB-BACKED:** dynamic skills in the `skills` table, created via `skill_create`, searched by `SkillSearchService` (BM25 → embedding → LLM). Bodies live in `skills.content` (DB) — the anti-pattern class P1 forbids for file skills.

The missing tier: **project-scoped + file-backed + repo-committed.** v1 of the file canon already shipped (`.agents/tester/skills/` = 5 gate-runner skill dirs, each with a fixed-name `skill.json` manifest (schema v1) + body `.md`). Discovery today is deterministic but narrow: an `ensure.md` mandate covers gate legs only; `skill_search` is structurally blind to disk skills; mandates do not scale to non-gate contexts.

**Proven failure mode (2026-10-25 G2 incident):** a codified runner skill existed on disk and was never discovered by the worker that needed it. Ensemble works on MANY projects — the gap multiplies per project.

### 1.2 Goal

File-backed project skills (`.agents/*/skills/` under a project's tree) become a first-class SOURCE in the skill system — searchable, loadable, project-scoped — while git remains the distribution and evolution channel. The loader indexes `skill.json` manifests; load/inject reads the FILE from disk; bodies are NEVER copied into the DB (P1).

### 1.3 Non-Goals (v2.0)

Carried verbatim from the commission. See §9.

---

## 2. Binding Design Principles (User-Ruled 2026-10-31 — HARD CONSTRAINTS)

These seven principles are HARD constraints. Deviations MUST be escalated as open questions, never silently bent. They are restated here because every FR is grouped under one.

| ID | Principle | One-line |
|----|-----------|----------|
| P1 | FILES ARE CANON, DB IS A VIEW | The loader indexes `skill.json` manifests; load/inject reads the FILE from disk; NEVER copy skill bodies into the DB. Derived index metadata rows are permitted; bodies are not. |
| P2 | Search fusion | `skill_search` results merge BOTH sources, each result tagged `source: db \| project-file`. |
| P3 | Project-scope binding | Project skills surface ONLY when the caller works in that project — never leak cross-project. |
| P4 | Same-name precedence | A project file SHADOWS the same-name DB skill within that project's scope (structurally retires the 3 legacy DB gate-runner copies `9e79ccad`/`fd1e01dc`/`76e3e1a7a`). |
| P5 | Injection stays per-agent opt-in | `skill_injection` flag governs injection; default is search + manual load; no new injection defaults. |
| P6 | Cross-agent visibility WITHIN the project | All `.agents/*/skills/` namespaces readable/searchable by any agent working there (namespaces are organization, not ACLs). |
| P7 | Evolution via git PRs ONLY | File skills evolve via git PRs; never `skill_fix`; skill-evolution machinery is untouched for file skills. |

---

## 3. Functional Requirements

### 3.1 P1 — Files are canon, DB is a view

| ID | Requirement | Rationale | Priority | Theme |
|----|-------------|-----------|----------|-------|
| FR-P1-1 | The loader MUST read skill bodies from the FILE on disk at load/inject time; bodies MUST NOT be persisted in any DB table (no `content` column population, no body copy into `skills.content`, `skill_bank.content`, embeddings, or any new table). | P1 core | Must | Canon |
| FR-P1-2 | The loader MAY persist DERIVED INDEX METADATA rows (e.g., skill identity, name, description, project_id, agent-namespace, manifest path, manifest hash, last-indexed timestamp, validation status) in a DB table or in-memory index. Such rows MUST contain no body text. | P1 permits derived metadata | Must | Canon |
| FR-P1-3 | On every load/inject of a file skill, the body MUST be re-read from disk (not served from a cached DB body). A disk change (git pull, edit) MUST be visible on the next load without a re-index of bodies. | P1 canon authority | Must | Canon |
| FR-P1-4 | The loader MUST NOT invoke `skill_create`/`skill_store_service`/`SkillCloneService.clone_on_miss_sync` for file skills, and MUST NOT write into `skills`, `skill_bank`, `skill_embeddings`, `skill_triggers`, `skill_lineage`, `skill_usage_records`, or `skill_ab_tests`. | Prevents body copy + evolution coupling | Must | Canon |
| FR-P1-5 | The loader MUST provide a testable assertion (automated test) that no file-skill body bytes appear in any DB table after an index + search + load cycle. | P1 must be verifiable, not asserted | Must | Canon |

**FR-P1-1 detailed:** Description: the loader treats the file as the single source of truth for body content. Rationale: P1 user ruling. Priority: Must. Notes: the existing `skill_include_resolver.py:387-445` (disk-FIRST read of innate-skills) is the in-repo shape precedent; the v1 canon precedent is `skill_seed_service.py` (file→bank COPY) which we must NOT replicate.

**FR-P1-5 detailed:** Description: a test asserts that after indexing + searching + loading a file skill whose body contains a unique sentinel string, querying every skill-system table's text columns for that sentinel returns zero rows. Acceptance: the test fails if any body byte is persisted. Rationale: P1 is otherwise unverifiable. Priority: Must.

### 3.2 P2 — Search fusion

| ID | Requirement | Rationale | Priority | Theme |
|----|-------------|-----------|----------|-------|
| FR-P2-1 | `SkillSearchService.search(user_message, project_id, max_results, agent_id)` MUST return results from BOTH sources (DB dynamic skills + project file skills) merged into the existing `{"injected": [...], "low_match": [...]}` shape. | P2 core | Must | Fusion |
| FR-P2-2 | Every result entry (in both `injected` and `low_match`) MUST carry an additive `source` key with value `"db"` or `"project-file"`. DB results MUST be tagged `"db"`; file results `"project-file"`. Consumers that ignore unknown keys MUST continue to work. | P2 tagging; backward compat | Must | Fusion |
| FR-P2-3 | File skills MUST be able to enter the BM25 prefilter corpus (Stage 1) when a project context is present, so they inherit the pipeline's normalization (tokenization, k1/b, ranking) rather than a parallel scorer. | Fusion seam choice | Must | Fusion |
| FR-P2-4 | File skills MUST NOT be indexed/searchable when no project context is present (global-only search returns DB results only). | P3 binding | Must | Fusion |
| FR-P2-5 | The fusion MUST preserve the existing degraded-fallback behavior: if Stage 2 (embedding) or Stage 3 (LLM) fails, file-skill results still surface via BM25 ordering, exactly as DB results do today. | Reliability parity | Must | Fusion |
| FR-P2-6 | `low_match` entries for file skills MUST carry the same `{name, score, description}` plus `source: "project-file"`. | P2 tagging parity | Must | Fusion |
| FR-P2-7 | The fused result count MUST respect `max_results` as the combined cap (not per-source), and the existing `low_match` cap of 3 MUST apply to the combined set. | Cap semantics | Must | Fusion |

### 3.3 P3 — Project-scope binding

| ID | Requirement | Rationale | Priority | Theme |
|----|-------------|-----------|----------|-------|
| FR-P3-1 | A search/load MUST use a resolved project context. When the instance has a `project_id`, resolution MUST follow the existing chain (`instances.project_id` → `projects.main_directory` via `_get_project_workdir`). | Reuse existing resolution | Must | Binding |
| FR-P3-2 | Project skills MUST be discovered only under the resolved project's directory tree. **(SUPERSEDED in part — see §14 FR-P3-2/§4.2 row, iteration 003 item 6: v1 scan roots = `projects.main_directory` + project-bound `EXTRA_ROOTS` entries (planner ruling W-5); `related_directories` is ONLY the get_by_directory binding predicate — never an implicit scan root (D-Q1); worktree support = the v2 `work_dir` metadata convention.)** Original text retained for traceability: (main_directory and, where applicable, related_directories/worktrees — see Q1). | P3 binding | Must | Binding |
| FR-P3-3 | When no project context can be resolved (no project_id, or project has no directory), file skills MUST be entirely invisible: search returns DB-only, load of a file-skill address MUST fail closed (not silently fall back to a different project's file). | P3 fail-closed | Must | Binding |
| FR-P3-4 | Cross-project leak is a SECURITY-GRADE invariant: a project skill from project A MUST NEVER surface in a search/load executed under project B's context, even if the caller supplies a conflicting path or project_id. Testable criterion: a test seeds project A's `.agents/*/skills/` and project B's context, runs search, asserts zero project-A names in results. | P3 security | Must | Binding |
| FR-P3-5 | **RESOLVED (see §14):** ONE canonical resolver (`resolve_active_project`, D-Q1) with the explicit priority chain reconciles both sources (drift ⇒ WARNING); rung-1 is confirm-only (A6); the resolver is the sanctioned scope reader for new code with the 3 allowlisted legacy sites until the v2 migration (A13/S3). Original requirement retained for traceability: The two existing project-scope sources (instance row column `instances.project_id` vs `instance_metadata` JSONB dict used by auto-injection at `instance_messaging.py:4310-4316`) MUST be reconciled so file-skill resolution uses one consistent source; the reconciliation choice is an open design question (Q2). **(resolved — see §14)** | Avoid split-brain | Must | Binding |
| FR-P3-6 | Non-repo projects (no `.git`, or no `.agents/` tree) MUST degrade gracefully: no file skills indexed, no error, DB-only behavior. | Compatibility | Must | Binding |

### 3.4 P4 — Same-name precedence (shadowing)

| ID | Requirement | Rationale | Priority | Theme |
|----|-------------|-----------|----------|-------|
| FR-P4-1 | When a project file skill and a DB dynamic skill share a name (case-insensitive match) within the same project scope, the project file skill MUST shadow the DB skill: the DB skill MUST NOT appear in search results for that name. | P4 core | Must | Shadowing |
| FR-P4-2 | Shadowing MUST be represented in search results by the presence of the project-file result (tagged `source: "project-file"`) and the ABSENCE of the shadowed DB result — NOT by a visible "shadowed" tag on the DB row. (Rationale: P4 retires the legacy DB copies structurally; surfacing them as "shadowed" would re-expose the deprecated content.) | P4 representation | Must | Shadowing |
| FR-P4-3 | Shadowing MUST be applied BEFORE Stage 3 (LLM select), because Stage 3 maps picks back by `name.lower()` and cannot distinguish same-name collisions. | Pipeline constraint | Must | Shadowing |
| FR-P4-4 | Shadowing MUST apply to `low_match` as well as `injected`. | Consistency | Must | Shadowing |
| FR-P4-5 | The 3 legacy DB gate-runner copies (`9e79ccad`/`fd1e01dc`/`76e3e1a7a`) MUST be structurally retired by P4 shadowing in THIS project once the loader is ON (no code deletion required; the v1 canon files shadow them by name). | P4 retirement | Must | Shadowing |
| FR-P4-6 | A test MUST verify shadowing end-to-end: a DB skill named `X` and a file skill named `X` in the same project → search returns only the file result tagged `project-file`. | Testable shadowing | Must | Shadowing |

### 3.5 P5 — Injection stays per-agent opt-in

| ID | Requirement | Rationale | Priority | Theme |
|----|-------------|-----------|----------|-------|
| FR-P5-1 | The loader MUST NOT change the `skill_injection` default (False) or add new injection defaults. File skills are reachable via search + manual load only, unless an agent already has `skill_injection: true`. | P5 | Must | Injection |
| FR-P5-2 | When an agent with `skill_injection: true` triggers auto-injection, file skills MUST be eligible for injection on the same footing as DB skills, subject to the same caps (`max_inject_skills` = 2) and the same project-scope binding. | Parity | Should | Injection |
| FR-P5-3 | When a file skill is injected (auto or explicit load), the rendered injection MUST read the body from disk (FR-P1-3) and render the full raw markdown body with NO length cap (parity with DB injection today), tagged with the file-skill address (see FR-LP-2). | Load parity | Must | Injection |
| FR-P5-4 | The `load_skill` chain (`send_message(load_skill=...)`) MUST be able to load a file skill by its address, intercepting BEFORE `clone_on_miss_sync` (which mutates DB) — file skills never clone. | Load path | Must | Injection |

### 3.6 P6 — Cross-agent visibility within the project

| ID | Requirement | Rationale | Priority | Theme |
|----|-------------|-----------|----------|-------|
| FR-P6-1 | All `.agents/*/skills/` namespaces under a project MUST be searchable by ANY agent working in that project, regardless of which agent owns the namespace. | P6 | Must | Visibility |
| FR-P6-2 | Namespaces (`.agents/{agent}/skills/`) are organizational, not access-control boundaries. No agent may be denied read/search of another agent's project skills within the same project. | P6 | Must | Visibility |
| FR-P6-3 | The index MUST record the owning agent-namespace per skill (for display/filtering) but MUST NOT enforce it as a read gate. | P6 metadata | Must | Visibility |

### 3.7 P7 — Evolution via git PRs only

| ID | Requirement | Rationale | Priority | Theme |
|----|-------------|-----------|----------|-------|
| FR-P7-1 | File skills MUST NOT be editable via `skill_fix`, `skill_create` (targeting a file skill), or any skill-evolution job. `skill_fix` on a file-skill address MUST fail closed with a clear message directing the caller to a git PR. | P7 | Must | Evolution |
| FR-P7-2 | `skill_feedback` on a file skill MAY be recorded (for corpus quality signal) but MUST NOT trigger evolution or DB mutation of the body; feedback on file skills is observational only. | P7 | Should | Evolution |
| FR-P7-3 | **PARTIALLY SUPERSEDED — see §14 FR-P7-3 / §4.1 row:** P7 constrains EVOLUTION MACHINERY (skill-keeper, trigger engine, A/B) — it does NOT forbid search-substrate derived index metadata. Embeddings ARE computed for file skills as derived index metadata in the dedicated `project_skill_embeddings` table (no FK, A1); trigger-query generation imports zero evolution machinery (verified, F1). Original requirement retained for traceability: The skill-evolution machinery (`ENSEMBLE_SKILL_EVOLUTION_ENABLED`, skill-keeper, triggers, embeddings, A/B tests) MUST remain untouched for file skills — no new triggers, no embeddings computed, no A/B tests. | P7 | Must | Evolution |
| FR-P7-4 | File skills MUST NOT appear in `skill_list` as mutable DB rows, and `skill_create` MUST NOT accept a `source: project-file` body. | P7 | Must | Evolution |

---

## 4. Contract Specifications

### 4.1 Search-fusion contract

**Result shape (additive, backward-compatible):**

```json
{
  "injected": [
    { "skill": <object>, "score": <float>, "source": "db" | "project-file" }
  ],
  "low_match": [
    { "name": <str>, "score": <float>, "description": <str>, "source": "db" | "project-file" }
  ]
}
```

- `source` is REQUIRED on every entry once the loader is enabled; when the loader is disabled (kill-switch OFF), DB results MAY omit `source` OR carry `source: "db"` — both tolerated by consumers (they index `entry["skill"]`/`entry["score"]` and serialize whatever keys exist).
- `skill` object for file results MUST be a duck-typed object exposing at least `.name`, `.description`, `.content`, `.category`, `.status`, `.ab_test_group` (the fields the injection renderer reads). It MUST NOT expose a DB `id`/`uuid` (file skills have no DB UUID — see FR-LP-2). It MUST expose `.source = "project-file"` and a stable address (FR-LP-2).
- **Shadowing representation (per P4):** shadowed DB results are HIDDEN (not present, not tagged). Only the project-file result appears.
- **Caps/limits interaction:** `max_results` is the combined cap across both sources. `low_match` cap of 3 applies to the combined set. BM25 corpus cap of 200/bucket applies to the merged corpus (file skills count toward the bucket).
- **Single-source behavior:** when only one source matches, results from that source only; no padding, no empty placeholders.
- **Score normalization:** **(§4.1 superseded in part — see §14 "no embeddings" row):** file skills DO get embeddings (derived index metadata in `project_skill_embeddings`, A1) — the "no embeddings computed" parenthetical below is superseded; the fallback behavior it describes remains correct for the embeddings-UNAVAILABLE path. Original text retained for traceability: file skills entering the BM25 corpus (Stage 1) inherit BM25 scoring; Stage 2 embedding re-rank and Stage 3 LLM select apply to the merged set. If file skills lack embeddings (fallback path only), Stage 2 assigns them the neutral 0.0 score (BM25 order preserved), identical to DB skills with no embeddings today (`skill_search_service.py:829-835`).
- **Stage 3 name-keyed mapping hazard:** Stage 3 maps LLM picks back by `name.lower()` (`:906-912`, `:1001-1012`). Shadowing MUST be applied before Stage 3 (FR-P4-3) and file/DB same-name collisions MUST be deduplicated before Stage 3 so the LLM never sees both.

### 4.2 Project-binding contract

- **Project resolution:** search/load uses the resolved project context. Resolution chain: instance row `project_id` → `projects.main_directory` via `_get_project_workdir` (`daemon/tools/instance.py:1469-1487`). If resolution fails (no project_id, no directory, exception swallowed → None), file skills are invisible (FR-P3-3).
- **Worktree handling (Q1):** **RESOLVED (see §14):** v1 scan roots = `projects.main_directory` + project-bound `EXTRA_ROOTS` entries (planner ruling W-5); `related_directories` is the get_by_directory BINDING predicate, never an implicit scan root; `work_dir` metadata convention is the v2 worktree path (D-Q1/PA-A3). The consistency requirement stands as written (one rule across scan roots, scope binding, leak tests). Original text retained for traceability: whether `related_directories` and git worktrees are included in the scan root set is an open design question. Requirement: whatever the choice, it MUST be applied consistently to scan roots, project-scope binding, and cross-project-leak tests. **(resolved — see §14)**
- **Non-repo behavior:** if the resolved directory has no `.agents/` tree (or no `.git`), no file skills are indexed; no error; DB-only behavior (FR-P3-6).
- **Never-leak invariant (security-grade):** a project skill from project A MUST NEVER surface under project B's context. Testable criterion (FR-P3-4): seed project A's `.agents/*/skills/`, execute search under project B's context, assert zero project-A skill names in results. This is a Tier-2 (core seam) gate candidate per ensure.md (skill-service wiring in manager.py is a core seam).

### 4.3 Load-path contract

- **Tool surfaces that must read from the file:** `skill_view` (by address), the `load_skill` injection chain (`SkillInjectionService.inject_explicit_skill`), and auto-injection (when enabled). All three MUST read the body from disk (FR-P1-3), not from a DB row.
- **Identity/addressing (FR-LP-2):** **RESOLVED (see §14):** address = `pf:<namespace>:<name>` + internal pseudo-id `pf-<sha16>` (D-Q5). Original text retained for traceability: file skills have NO DB UUID. They MUST have a stable, addressable identity usable in `skill_view`, `load_skill`, and (observational) `skill_feedback`. The address format is an open design question (Q3), but it MUST be: (a) deterministic from (project, agent-namespace, skill name), (b) not collide with UUID format (so DB tooling does not misroute), (c) survive git operations (rename aside — renames change identity, acceptable).
- **Intercept before clone-on-miss:** the `load_skill` chain MUST resolve a file skill by address BEFORE `clone_on_miss_sync` runs (`skill_clone_service.py:143-223`), so file skills never trigger DB clone/refresh.
- **Size/truncation:** `skill_view` truncates content at 8000 chars (`skill_tools.py:325-328`) for DB skills; for file skills the same cap MUST apply to the rendered view (the raw body is still read from disk; only the displayed view is capped). Auto/explicit injection renders the FULL body with NO cap (parity with DB injection today, which has no cap).
- **Missing file at load time:** if the file was deleted between index and load, the load MUST fail closed with a clear error (not serve a stale body). Re-index is not automatic on load. **(§4.3 annotation — adopted-pattern sweep, iteration 002: for a CHANGED file (digest mismatch) the plan serves fresh + schedules an ASYNC re-index (D-Q7/A4) — the "not automatic" sentence applies to the MISSING-file case (nothing to re-index); the two are compatible, annotated to prevent a misread.)**

### 4.4 Validation contract

- **Schema (v1 canon):** `skill.json` with fields: `schema_version` (int, =1), `name` (string, MUST equal the containing directory name), `version` (semver string), `description` (string), `category` (string), `scope` ("project"), `tiers` (int[]), `body` (relative filename of the body `.md`), `assets` (string[] — empty in all five v1 canon entries), `loads` (string[] of other skill names), `supersedes` (optional object `{store, skill_id}`). This is the de-facto spec (`.agents/tester/RESULTS/2026-10-31-tiered-gate-doctrine-authoring.md:26,50,56`); no formal schema doc exists today.
- **Validation rules (per-skill, fail-loud):**
  - Malformed `skill.json` (invalid JSON, missing required fields, wrong types) → skip that skill, record an error in the index/observability surface (FR-OBS-1), do NOT abort the scan.
  - `name != dir name` → skip + error (identity integrity).
  - Missing body file (the `body` filename does not exist) → skip + error.
  - Unknown extra fields → tolerated (forward-compat; do NOT reject), but logged at debug.
  - `schema_version` != 1 → skip + error (unknown schema; do not guess).
  - Body file empty or exceeds a size cap (Q4) → skip + error.
- **Where validation runs:** at index time (scan), and re-validated on load (defense in depth — a file mutated after index must not crash the loader; a failed re-validation fails the load, not the process).
- **Legacy-schema conflation hazard (anti-conflation test REQUIRED):** `.agents/project-skills/bump-version/skill.json` uses a DIFFERENT schema (`id`/`triggers[]`/`default_bump`/`supported_project_types`/`created_at`, NO `schema_version`, NO `body`). The scan MUST only ingest `.agents/*/skills/*/skill.json` (the canon path). A scan of `.agents/project-skills/` MUST produce zero ingested skills and MUST NOT crash. Test (FR-VAL-1): seed both trees, scan, assert only canon skills indexed and `bump-version` absent.
- **Atomicity:** a single bad skill MUST NOT poison the whole scan (per-skill isolation). The index reflects the set of valid skills; errors are reported, not fatal.

### 4.5 Observability contract

- **Per-source visibility:** the observability surface MUST expose, per resolved project: `file: {indexed_count, namespaces[], shadowed_names[], errors[]}` and `db: {count (unchanged existing behavior)}`. Shape fits the readiness advisory-detail pattern (`readiness.py:402-407`, `:517-544`) — serialized into an advisory block that NEVER flips readiness components.
- **Logging:** index scan start/complete with counts + duration; per-skill validation errors at WARNING with skill path + reason; **shadowing decisions at WARNING — SUPERSEDED (A18.3, see §14): the original "at INFO" wording is superseded — shadowing is the only non-additive effect in the surface and must be greppable by an operator who doesn't know to look** (name + winner + both ids); cross-project-leak guard trips at ERROR.
- **Health surfacing WITHOUT flipping readiness:** file-source health (e.g., scan errors, missing `.agents/`) MUST be advisory detail only. A broken file source MUST NOT flip any `/readyz` component or block boot. (Parity with MCP quarantine advisory pattern.)
- **No new readiness component:** do not add a `skill_sources` readiness component that could flip `readyz` to not-ready.

---

## 5. Non-Functional Requirements

### 5.1 Performance

| ID | Category | Requirement | Metric | Target | Measurement |
|----|----------|-------------|--------|--------|-------------|
| NFR-PERF-1 | Scan latency | Initial project index scan (all `.agents/*/skills/`) completes within budget for a project with ≤50 skills | wall-clock | ≤ 2 s | measured in test (real filesystem, tmp project) |
| NFR-PERF-2 | Search latency (file source) | A search that includes file skills adds no more than a bounded overhead over the DB-only path | wall-clock delta | ≤ 50 ms p95 | before/after measurement in test |
| NFR-PERF-3 | Load latency | Reading a file skill body from disk on load | wall-clock | ≤ 50 ms p95 for bodies ≤ 64 KB | measured in test |
| NFR-PERF-4 | No repeated full scans per search | The index is built once per project context (cached) and invalidated on explicit refresh; search MUST NOT re-scan the filesystem. | scan count | 0 scans per search after initial index | instrumented test |
| NFR-PERF-5 | Concurrency | Concurrent searches/loads for the same project MUST NOT corrupt the index or double-scan. | correctness | no races | concurrency test |

### 5.2 Safety invariants

| ID | Requirement | Metric | Target | Measurement |
|----|-------------|--------|--------|-------------|
| NFR-SAFE-1 | P1 body-never-in-DB (FR-P1-5) | sentinel grep across all skill tables | 0 rows | automated test |
| NFR-SAFE-2 | Cross-project leak (FR-P3-4) | seeded-A/search-under-B | 0 A names | automated test (security-grade) |
| NFR-SAFE-3 | Shadowing correctness (FR-P4-6) | DB+file same name | only file result | automated test |
| NFR-SAFE-4 | Soft-fail when loader unavailable | search/load with loader disabled | DB-only, no crash, no 500 | automated test |
| NFR-SAFE-5 | No readiness flip on file-source failure | `/readyz` under broken file source | all components true | automated test |

### 5.3 Compatibility

| ID | Requirement | Notes |
|----|-------------|-------|
| NFR-COMPAT-1 | Existing DB dynamic skills MUST be unaffected: same search results, same scores, same behavior when no project context or when the loader is disabled. | Regression baseline |
| NFR-COMPAT-2 | **RESOLVED (see §14):** the file source is INDEPENDENT of the evolution gate (D-Q8b/PA-A1/A9/B1 — Skill-Bank precedent `manager.py:3951-3956`); construction is unconditional; the (ON, evolution-absent) mode is the A9-pinned file-only surface (M10). Original requirement retained for traceability: The loader MUST inherit (or explicitly decide, Q5) the `config.skill_evolution is not None` gating that wires skill services in `manager.py:2222-2276`. Decision must be documented; if the file source is gated behind evolution, it is unavailable whenever evolution is disabled — state as a constraint. **(resolved — see §14)** | ~~Open question Q5~~ resolved (A9/B1) |
| NFR-COMPAT-3 | **SUPERSEDED in v1 — see §14 NFR-COMPAT-3 row:** REST `/api/skills/search` (and the REST list) serve DB-only per A6 + leader ruling D1 (no server-side project binding ⇒ REST file visibility would be a cross-project body read, violating P3); the AGENT lane keeps total fusion (P2 intact); server-side REST binding = v2 parked item. Original requirement (REST+agent parity) retained for traceability: `skill_search` REST endpoint (`POST /api/skills/search`) and the agent tool MUST both surface file skills (same fusion) so REST and agent paths agree. | Parity |
| NFR-COMPAT-4 | The v1 canon (5 tester skill dirs) MUST be ingested without modification to their `skill.json` or body files. | No migration of canon |
| NFR-COMPAT-5 | No schema migration is REQUIRED for v2.0 if the index is in-memory or uses existing tables with metadata-only rows; if a new table is introduced, it requires its own `.sql` file in `daemon/migrations/versions/` (house rule: every new SQLModel table, zero exceptions) and a `_ensure_postgres_columns` entry for pre-existing PG databases. | Rollout/rollback input |

### 5.4 Idempotency

| ID | Requirement | Notes |
|----|-------------|-------|
| NFR-IDEM-1 | Re-indexing the same project MUST produce the same index (no duplicate entries, no count drift). | Idempotent scan |
| NFR-IDEM-2 | Concurrent index builds for the same project MUST coalesce (single build, callers await). | Concurrency |
| NFR-IDEM-3 | **ANNOTATED (see §14 NFR-IDEM-3 row, iteration 003): the stat-gate per-request revalidation IS the explicit mechanism** (mtime+size fast-path → sha256 on suspicion; D-Q2/S4) — "explicit refresh call or TTL" is not required; "implicit per-search" referred to directory RE-SCANNING, which is forbidden (NFR-PERF-4). Original requirement retained for traceability: Index invalidation on file change MUST be explicit (refresh call or TTL), not implicit per-search. | Predictable |

---

## 6. Config and Kill-Switch

### 6.1 Enable knob

- **Constraint (safety):** the default posture MUST be decided with prod safety in mind. Two options, to be adjudicated in technical analysis (Q6):
  - (a) Default-OFF (`ENSEMBLE_PROJECT_SKILL_SOURCE_ENABLED` — **knob name corrected inline per N1/§14; the original `ENSEMBLE_PROJECT_SKILL_LOADER_ENABLED` is superseded**, env wins) — conservative; file skills invisible until operator opts in. Cost: P4 shadowing of the 3 legacy DB skills does not happen until ON.
  - (b) Default-ON with documented env OFF escape — matches the `report_delivery_recovery_lane_stuck_wake` pattern (`config.py:2098-2120`); P4 shadowing of the 3 legacy DB skills happens immediately on promotion (which is DESIRED per P4).
- **Requirement:** whichever default is chosen, it MUST be (i) a single named knob, (ii) env-overridable per the house config ladder (DB > env > yaml > default where applicable), (iii) fail-loud validated (no silent misconfiguration), (iv) documented in `.env.example` and the runbook.
- **P4 consequence (must be stated, not hidden):** when the knob is ON in THIS project (`agents-ensemble`), the 3 legacy DB gate-runner copies are immediately shadowed by the v1 canon files. This is desired and is the mechanism by which P4 retires them. Operators must be told. **(§6.1 RESOLVED — see §14: default-ON ratified on corrected grounds, A18 + leader ruling; knob = `ENSEMBLE_PROJECT_SKILL_SOURCE_ENABLED`)**

### 6.2 Path overrides

- **Requirement:** the scan root MUST be overridable per project (default: resolved project directory). A global override of the `.agents/*/skills/` glob pattern is NOT required for v2.0 (the canon path is fixed by doctrine).
- **Per-project override:** **RESOLVED (see §14):** no per-project override in v1 — paths are derived from the project row (that IS the feature); the only debug affordance is `ENSEMBLE_PROJECT_SKILL_SOURCE_EXTRA_ROOTS` (dev-only, project-bound per W-5 — never weakens P3). Original text retained for traceability: if needed, a project-level setting (in `projects` metadata or a config file) MAY override the scan root. This is an open design question (Q7) — state as a constraint, do not decide here. **(resolved — see §14)**

### 6.3 Kill-switch

- **Requirement:** **(§6.3 partially superseded — see §14 §6.3 row):** the kill-switch is a BOOT-TIME env knob (`ENSEMBLE_PROJECT_SKILL_SOURCE_ENABLED=false`); disable/enable REQUIRES A RESTART (OFF at construction ⇒ `file_source=None` everywhere ⇒ byte-identical search/list/view RESULTS per A11); the "no restart for the NEXT search" promise is DEFERRED (hot-reload/DB-settings switch = v2 — a running daemon cannot see env edits: os.environ is process-inherited). Original requirement retained for traceability: a runtime env kill-switch (`ENSEMBLE_PROJECT_SKILL_LOADER_ENABLED=false`) MUST disable the file source entirely (no scan, no index, no search fusion, no load) without a restart being required for the NEXT search (cached index invalidated). This is the operator's emergency off-switch.

---

## 7. Test Strategy Requirements

House doctrine (`.agents/tester/rules/ensure.md`) applies. Do not re-invent; reference.

- **Gate tier:** Tier 1 (blast-radius) by default; ESCALATES to Tier 2 because the diff touches a core seam — `daemon/manager.py` is on the mechanical core-seam list, and skill-service wiring lives at `manager.py:2222-2276`, `:2426`, `:3951-3987`. Tier 2 = whole-tree dual-side, ≤90 min advisory.
- **Mandatory recipe load:** before executing any gate leg, READ the recipe skill body for the chosen tier from `.agents/tester/skills/` (deterministic path, never `skill_search` discovery). Gate-critical recipes must not depend on probabilistic discovery (the G2 failure mode).
- **Mutation-verified pins:** every behavioral claim that matters (shadowing, leak, body-never-in-DB, kill-switch) MUST be a mutation-verified pin (planted defect → RED → restore → GREEN), per house practice. Minimum 4 mutation legs per claim where feasible.
- **Real-PG where the index is DB-backed:** if the index uses a DB table, tests MUST run against real PostgreSQL (not SQLite-only), per house doctrine for DB-backed features. If the index is in-memory, state that explicitly and justify.
- **Net-new gates:** every gate leg runs base vs HEAD with a FAILED-digest diff (`failed-digest-equality-gate` recipe); net-new reds must be 0 (or adjudicated).
- **Deterministic recipe load:** tests and gate legs load recipes from the deterministic path, never via `skill_search`.
- **Anti-conflation test (FR-VAL-1):** seed both `.agents/tester/skills/` (canon) and `.agents/project-skills/` (legacy bump-version), scan, assert only canon skills indexed and `bump-version` absent and no crash. This is a mandatory, standalone test.
- **P1 body-never-in-DB test (FR-P1-5):** sentinel-grep across all skill tables after a full index+search+load cycle.
- **Cross-project leak test (FR-P3-4):** security-grade; seeded-A/search-under-B → 0 A names.
- **Shadowing test (FR-P4-6):** DB+file same name → only file result.
- **Soft-fail test (NFR-SAFE-4):** loader disabled → DB-only, no crash, no 500.
- **No readiness flip test (NFR-SAFE-5):** broken file source → `/readyz` all components true.

---

## 8. Rollout / Rollback Posture

- **Staged release only.** The lane ships through the house staging path (branch → gates → merge → bump → tag → `stage.sh`); the plan covers through **STAGING**. The promote is the user's 3-factor nonce ceremony and is OUT OF SCOPE for the plan to perform. No live flips by this lane.
- **Rollback posture MUST be adjudicated explicitly** (standing house rule for non-empty migration deltas): the technical analysis decides whether this release is (a) migration-delta-empty (in-memory index — likely) or (b) carries a new table (new `.sql` + `_ensure_postgres_columns` entry), and in either case records explicit semantic reasoning for `rollback_safe` — no reflex `=0`/`=1`. The REQUIREMENT is the adjudication, not a predetermined verdict.
- **Constraint stated (not decided):** if the index is in-memory (Q8 option A), there is NO schema mutation and rollback is trivially safe (the old binary never references the loader; file skills simply become invisible again — the v1 canon files remain on disk untouched). If a DB metadata table is introduced (Q8 option B), rollback safety depends on the table being additive-only CREATE TABLE with zero references in the old tree (the v0.26.0 board-tables precedent class); the old binary is compatible because it has no model for the table.
- **Behavior-change disclosure at promote:** when the enable knob defaults ON, P4 shadowing immediately retires the 3 legacy DB gate-runner skills in THIS project on first boot of the new binary. This is DESIRED (P4) but MUST be disclosed in the release notes/ceremony materials so the operator is not surprised.
- **Kill-switch as rollback-adjacent control:** the env kill-switch (§6.3) provides runtime opt-out without rollback; it is not a substitute for rollback safety adjudication.

## 9. Non-Goals (v2.0) — Verbatim

- No DB body copies (P1).
- No `skill_fix`/evolution on file skills (P7).
- No injection default changes (P5).
- No public/global registry share (separate future).
- No FE work beyond trivially necessary.

---

## 10. Traceability Matrix

| Principle | FR IDs | Acceptance Criteria (testable) |
|-----------|--------|-------------------------------|
| P1 (files canon) | FR-P1-1..5 | Body read from disk on load; sentinel grep 0 rows in DB tables (FR-P1-5) |
| P2 (search fusion) | FR-P2-1..7 | `skill_search` returns merged results each tagged `source: db\|project-file`; caps combined |
| P3 (project binding) | FR-P3-1..6 | File skills invisible without project context; cross-project leak test 0 names (FR-P3-4) |
| P4 (shadowing) | FR-P4-1..6 | DB+file same name → only file result (FR-P4-6); legacy 3 copies retired |
| P5 (injection opt-in) | FR-P5-1..4 | No default change; file skill loadable via `load_skill` without DB clone |
| P6 (cross-agent visibility) | FR-P6-1..3 | Any agent searches any namespace within project; namespace is metadata only |
| P7 (git evolution) | FR-P7-1..4 | `skill_fix` on file address fails closed; **no skill_fix/evolution-lane triggers/A-B for file skills; embeddings are derived search substrate in `project_skill_embeddings`** (iteration 003 item 3: the original "no embeddings/triggers/A/B" wording superseded by OQ-4/F1/A1 — see §14) |

| Contract | FR/NFR IDs | Test |
|----------|-----------|------|
| Search-fusion | FR-P2-*, FR-P4-* | shape + tag + shadowing + caps tests |
| Project-binding | FR-P3-* | resolution + leak tests |
| Load-path | FR-P5-4, FR-LP-* (§4.3) | load from disk, address stability, intercept-before-clone |
| Validation | FR-VAL-* (§4.4) | per-skill fail-loud + anti-conflation |
| Observability | FR-OBS-* (§4.5) | advisory detail, no readiness flip |
| Performance | NFR-PERF-1..5 | latency/scan budgets |
| Safety | NFR-SAFE-1..5 | sentinel/leak/shadow/soft-fail/readiness |
| Compatibility | NFR-COMPAT-1..5 | DB unaffected, v1 canon ingested |
| Idempotency | NFR-IDEM-1..3 | re-index stability, coalescing |

---

## 11. Open Questions (Q1–Q8)

These are design questions for technical analysis. Requirements state the CONSTRAINT each imposes; recommendations land in `technical-analysis.md`.

- **Q1 (worktree scan roots):** Are `related_directories` and git worktrees included in the scan root set, or only `main_directory`? Constraint: whatever is chosen MUST be applied consistently to scan roots, project-scope binding, and cross-project-leak tests (FR-P3-2, FR-P3-4). No partial application.
- **Q2 (project-scope source reconciliation):** The tool layer reads `instances.project_id` (row column, `_get_project_id` at `skill_tools.py:108-119`); auto-injection reads `instance_metadata` JSONB (`instance_messaging.py:4310-4316`). These can disagree. Constraint: file-skill resolution MUST use ONE consistent source; the reconciliation must be documented and tested; split-brain is unacceptable (FR-P3-5).
- **Q3 (file-skill address format):** File skills have no DB UUID. Constraint: the address MUST be deterministic from (project, agent-namespace, skill name), MUST NOT collide with UUID format (so DB tooling does not misroute), and MUST survive git operations aside from renames (FR-LP-2). Format is an architecture decision.
- **Q4 (body size cap):** Is there a maximum body size for a file skill, and what happens when exceeded (skip vs truncate)? Constraint: validation must be deterministic and fail-loud (FR-VAL rules); whatever cap is chosen must be documented and tested; the v1 canon bodies must all pass.
- **Q5 (evolution-gate inheritance):** Skill services are wired only when `config.skill_evolution is not None` (`manager.py:2222-2276`). Does the file source inherit this gate (unavailable when evolution disabled) or get its own wiring? Constraint: the decision MUST be documented; if inherited, file skills are invisible whenever evolution is disabled — state as a known limitation (NFR-COMPAT-2).
- **Q6 (default posture of the enable knob):** Default-OFF (conservative) vs Default-ON with env OFF escape (matches `report_delivery_recovery_lane_stuck_wake`). Constraint: the choice MUST account for the P4 consequence — ON immediately shadows the 3 legacy DB skills in THIS project (desired). Prod safety vs. feature availability (§6.1).
- **Q7 (per-project scan root override):** Is a per-project override of the scan root required for v2.0, or is the fixed `.agents/*/skills/` canon path sufficient? Constraint: if not provided, document as a deferred non-goal; if provided, it must not weaken P3 binding (FR-P3-2, FR-P3-4).
- **Q8 (index storage):** In-memory index (rebuilt on boot, no schema migration, loses state on restart — acceptable since files are canon) vs a DB metadata table (requires `.sql` file + `_ensure_postgres_columns` entry, persists across restarts, enables cross-process visibility). Constraint: the choice affects NFR-COMPAT-5 (migration requirement), performance (NFR-PERF-1..4), and the P1 body-never-in-DB test (FR-P1-5) — bodies must never be in the table regardless. Technical analysis decides; requirements state the constraints.

---

## 12. Assumptions

| # | Assumption | Reason | Risk if Wrong |
|---|------------|--------|---------------|
| 1 | The v1 canon (`skill.json` schema v1) is stable and will not change during v2.0 | Research evidence E3; no formal schema doc exists but de-facto spec verified | Validation rules may need revision; tests must pin the schema |
| 2 | `skill_search` consumers tolerate an additive `source` key | E1: tool serializes whatever keys exist; injection service indexes `entry["skill"]`/`entry["score"]` | If a consumer strict-parses, fusion breaks — must verify in tests |
| 3 | Project resolution via `_get_project_workdir` is reliable for worktrees | E3: no worktree comparison code exists today | Worktree support (Q1) may require new resolution logic |
| 4 | The shadow-target set is **CENSUS-DEPENDENT (iteration 003 normalization): 3 legacy ids per the commission (`9e79ccad`/`fd1e01dc`/`76e3e1a7a`); a 4th predecessor id `08e67030` appears in the canon's `supersedes` fields — the Phase-0 census is authoritative** | E3 supersedes fields in v1 canon | If absent, P4 shadowing is a no-op here but still correct generally |
| 5 | **SUPERSEDED (in part) — see §14 "no embeddings" row:** file skills DO get search-substrate embeddings (derived index metadata, `project_skill_embeddings`, A1); P7 constrains evolution machinery, not the search substrate. Original assumption retained for traceability: File skills will not need embeddings (P7 forbids). Fallback stands: Stage 2 assigns neutral 0.0; BM25 order preserved — acceptable degradation | P7 user ruling (reconciled by OQ-4/F1/A1) | Stage 2 assigns neutral 0.0; BM25 order preserved — acceptable degradation |

---

## 13. Risks

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| 1 | Cross-project leak (P3) if project resolution is wrong | Security-grade: wrong project's skills served | FR-P3-4 security test; fail-closed on unresolvable context |
| 2 | Stage 3 name-keyed mapping collides on same names | Wrong skill selected | Shadow/dedup before Stage 3 (FR-P4-3) |
| 3 | Body-never-in-DB (P1) violated by a future code path | Canon authority lost | FR-P1-5 automated sentinel test; review gate |
| 4 | Conflation with `.agents/project-skills/` legacy schema | Crash or wrong ingestion | FR-VAL anti-conflation test (mandatory) |
| 5 | Core-seam touch escalates to Tier 2 | Longer gate (≤90 min) | Expected and budgeted; no way around it (manager.py wiring) |
| 6 | Inherited `skill_evolution` gate makes file skills unavailable when evolution off | Feature invisible in some deployments | Q5 decision; document limitation |
| 7 | P4 shadowing immediately retires 3 legacy DB skills in THIS project | Behavior change on promotion | Desired per P4; communicate to operators |

---

## 14. Amendment Register (architect 2026-10-31)

**CONSISTENCY ANNOTATION ONLY — no FR text above is rewritten.** The architect verdict on the plan set was **SOUND-WITH-AMENDMENTS** (18 amendments A1–A18, 5 CRITICAL, + advisory register S1–S6; see `architecture-recommendation.md`). This register maps affected requirement IDs to amendments and marks superseded/adjusted rows. All original requirement text above remains VISIBLE and authoritative for intent; where an amendment supersedes, the amendment governs implementation and the row is marked.

| Affected ID(s) | Status | Amendment(s) | Effect |
|---|---|---|---|
| **NFR-COMPAT-3** (REST + agent parity) | **SUPERSEDED in v1** | A6 + leader ruling D1 | REST `/api/skills/search` and the REST list serve **DB-only** in v1 (no server-side project binding ⇒ file visibility over REST = cross-project body read, a P3 violation). The AGENT lane keeps TOTAL fusion (P2 intact). Server-side REST binding = separate v2 commission (parked). REST-invisibility pin replaces the REST-parity test. |
| FR-P2-1..7 | Adjusted (strengthened) | A7, A8, A9, A6 | Shadow set computed from the FULL file corpus BEFORE budget truncation (A7); fusion seam + STEP-0 exception guards (A8); the (ON, DB-absent) file-only mode is a THIRD surface with PINNED semantics (A9); fusion remains total on the agent lane only (A6). |
| FR-P3-1..6 | Adjusted (strengthened) | A5, A6, A13 | Path containment enforced at index AND every read (A5 — the security-grade claim is only true WITH these pins); resolver rung-1 is CONFIRM-ONLY, never widening (A6); the resolver is the SANCTIONED SCOPE READER FOR NEW CODE (planner ruling S3, 2026-10-31) — BOTH sources fenced with an explicit allowlist, with the 3 allowlisted sites remaining until the v2 migration (A13). |
| FR-P4-1..6 | Adjusted (strengthened) | A7 | Shadowing is now an unconditional invariant computed pre-budget (budget overflow can no longer surface the stale DB twin). |
| §4.2 / §4.3 fail-closed envelope | Adjusted | A15 | A `pf:` load with no resolvable project FAILS CLOSED with an explicit envelope — the `pf:`-shaped input bypasses/precedes the silent early-exit at `skill_injection_service.py:468-470`; bare names keep today's silent-miss semantics. Full precedence chain stated: file (STEP 0) → clone_on_miss → repo fallback. |
| FR-P7-1..4 | Adjusted (enforcement extended) | A14, F1 | The OQ-4 reconciliation is CONFIRMED sound (trigger-query generation imports zero evolution machinery); the enforcement scope extends to `skill_analyze`/`skill_evolve`/`skill_get_metrics` + REST `POST /{skill_id}/fix` — P7 pin scope = "tools + REST fix". |
| FR-P1-1..5 | Strengthened | A1, A16, A2 | Embedding rows move to a dedicated no-FK `project_skill_embeddings` table (A1 — the `skill_embeddings` reuse was broken by the FK wall); the in-process cache holds METADATA ONLY, never body bytes (A16); a write census proves ZERO FK-child writes on the file path (A2). |
| FR-P5-2 / §4.1 caps | Adjusted | A9, A11 | (ON, DB-absent) parity runs with the A9 pinned file-only semantics; the OFF claim narrows to "byte-identical search/list/view RESULTS" (A11). |
| FR-P6-1..3 | Strengthened | A12 | The "namespace must be an AGENT dir name" idea is DELETED (it contradicted P6); any legal dirname is a namespace; `.agents/shared/skills/` is a legitimate positive fixture. |
| NFR-SAFE-5 | Adjusted | A11 | `/readyz` detail legitimately differs under OFF (the advisory always publishes with `enabled: true|false`); the OFF-side pin extends to zero `pf-` rows in any skill table. |
| NFR-PERF-2 / NFR-PERF-4 | Clarified | S4 | Wording: file reads are "index-backed + stat-gate" (not "fresh reads per search"); degraded-parity pin asserts file-in-top-5 surfacing with rank-6-10 exclusion documented as by-design uniform competition. |
| §6.1 (knob default posture) | Adjusted | A18 + leader ruling | Default-ON stands on CORRECTED grounds (family is MIXED; two-class rule: ON for read/serve surfaces, OFF for agent-authored mutation lanes). Falsifiability condition: the Phase-0 collision census re-opens the fusion/shadow split if collisions exceed the 3 known legacy ids. |
| §6.3 (kill-switch "next search without restart") | Deviation stands | planner D-Q8 (pre-architect) | Boot-time env knob in v1 (OFF at construction = DB-only behavior); a running daemon cannot see env edits — hot switch is v2. |
| NFR-COMPAT-5 | Adjusted | A1, A2, §3 | Migration delta is **+2** (`project_skill_files` + `project_skill_embeddings`), +3 if the Phase-1 D2 census gate chooses the usage companion table — all additive CREATE TABLE IF NOT EXISTS, no FK; each with own `.sql` (house rule intact). |
| §7 (test strategy) | Unchanged | — | Tier-2 escalation confirmed mechanical (manager.py); the wiring boundary enumerates 6 sites incl. `daemon/api.py` (A10). |

| §4.1 "no embeddings computed" (FR-P7-3) + §12 Assumption 5 **+ §10 traceability P7 acceptance cell** (iteration 003 item 3) | **SUPERSEDED (in part)** | OQ-4 / F1 / A1 | The "no embeddings" principle (P7) is CONFIRMED sound — trigger-query generation imports zero evolution machinery (verified). The STORAGE VEHICLE is superseded by A1 (dedicated `project_skill_embeddings`, no FK — the `skill_embeddings` reuse was structurally broken on PG). The embeddings principle itself stands; only the "no embeddings" literal wording of §4.1/Assumption 5 is superseded. |
| NFR-IDEM-3 ("invalidation MUST be explicit (refresh call or TTL), not implicit per-search") | Adjusted | D-Q2 / S4 | Stat-gate per-request revalidation IS the explicit mechanism (mtime+size fast-path → sha256 on suspicion). TTL/refresh is NOT required — the stat-gate is the explicit invalidation; "implicit per-search" referred to re-scanning, which is forbidden (NFR-PERF-4). |
| FR-P3-2 ("related_directories where applicable" / worktree scan roots) | Adjusted | D-Q1 / planner ruling W-5 | v1 scan roots = `projects.main_directory` + project-bound `EXTRA_ROOTS` entries only. `related_directories` is the get_by_directory BINDING predicate (an entry is consumed ONLY if it resolves to the caller's project_id) — never an implicit scan root. Worktree support lands via the `work_dir` metadata convention (v2). |
| §4.5 shadow-at-INFO → **WARNING** (A18.3) — canonical row (deduped per approver N10, iteration 002; absorbs the former duplicate "A18.3 (shadow-decision log level)" row and its cross-refs) | Adjusted | A18.3 | Shadowing decisions are logged at **WARNING** (not INFO) — shadowing is the only non-additive effect in the surface; must be greppable by an operator who doesn't know to look. Log-level pin added to the §4.5 observability contract; §4.5 carries the inline SUPERSEDED marker (adopted pattern). (Supersedes the "shadowing decisions at INFO" wording in §4.5; original text retained for traceability.) |
| §6.1 knob name (`ENSEMBLE_PROJECT_SKILL_LOADER_ENABLED`) vs D-Q8 | Cross-reference added (S1) | A18 / D-Q8 | The knob is `ENSEMBLE_PROJECT_SKILL_SOURCE_ENABLED` (technical-analysis postdates requirements and wins on the name per the commission instruction); the requirements §6.1 name is superseded for the knob identity. |

---

*End of requirements document.*
