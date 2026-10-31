# Research Findings — Project Skill Source Loader (Consolidated Evidence Digest)

Date: 2026-10-31
Author: planner[v2] via plan-creation worker
Status: Final
Inputs: 3-explorer digest (requirements.md + technical-analysis.md evidence base) + in-worktree read-only verification at `b56444244` (v0.28.1 tip, branch `feature/project-skill-loader`) performed during this planning session.

This is the evidence record the plan cites. Every `file:line` below was re-checked at `b56444244` by technical-analysis.md §0 ("Evidence" header) unless explicitly marked **[verified this session]**.

---

## A. Skill schema + file canon (explorer evidence E3)

| Anchor | Detail |
|---|---|
| `.agents/tester/skills/` | v1 canon = 5 dirs: `ab-base-leg-provisioning-artifacts`, `blast-radius-leg`, `failed-digest-equality-gate`, `regression-ab-partition-run`, `whole-tree-base-proven-diff` **[verified this session: ls]** |
| `skill.json` schema v1 (de-facto spec) | Fields: `schema_version` (int, =1), `name` (MUST equal containing dir name), `version`, `description`, `category`, `scope` ("project"), `tiers` (int[]), `body` (relative filename), `assets` (string[], empty in all 5), `loads` (string[] of other skill names), `supersedes` (optional `{store, skill_id}`). Verified key set **[this session: sorted keys of blast-radius-leg/skill.json]**. No formal schema doc exists (requirements §4.4). |
| `loads[]` population | 3 of 5 canon skills carry loads: `blast-radius-leg` → `failed-digest-equality-gate`; `regression-ab-partition-run` → 2 deps; `whole-tree-base-proven-diff` → 1 dep (tech §Q6). Gate doctrine DEPENDS on it: `ensure.md` mandates recipe-body reads before gate legs. |
| Legacy conflation hazard | `.agents/project-skills/bump-version/skill.json` is a DIFFERENT schema: `id`/`triggers`/`default_bump`/`supported_project_types`/`created_at`; NO `schema_version`, NO `body`; not under a `skills/` segment **[verified this session: dir exists]**. Ingestion must be structurally excluded (dual-leg: path shape + `schema_version==1`). |
| Body sizes | Largest canon body = 8001 bytes (`regression-ab-partition-run.md`, tech §D) — will fire the 8000-char `skill_view` truncation hint; injection renders full (no cap today). |
| Supersedes pointers | Canon manifests carry `supersedes` → `{9e79ccad…, fd1e01dc…, 76e3e1a7a…, 08e67030…}` (4 of 5 manifests — `blast-radius-leg` has no `supersedes` field and shadows by name only; tech §Q4; verified by reading all five manifests) — the P4 shadowing retirement target set. **Census-dependent normalization (iteration 003): the shadow set is 3 legacy ids per the commission; the 4th id `08e67030` is a supersedes-pointer predecessor — the Phase-0 census is authoritative on the actual by-name collision set.** |

## B. Search pipeline (explorer evidence E1) — `daemon/services/skill_search_service.py`

| Anchor | Detail |
|---|---|
| `search()` | `:399-486` |
| Stage 1 BM25 | `:597-757`. Corpus = repo.list(project) + overlay(global). Dedupe by `skill.id` `:672-698` (**name-blind**). Ownership filter `:703-709` — **THE FUSION SEAM** (post-ownership-filter, pre-tokenization). Tokenization = name+desc+content `:720-723`. Fetch caps 200/bucket `:646-648`; bm25 top_k 10. |
| Stage 2 cosine re-rank | `:763-850`. MAX over `skill_embeddings`; no rows → 0.0 neutral `:829-835` (the no-embeddings behavior file skills inherit). top_k 5. |
| Stage 3 LLM select | `:856-1034`. Selector tier "quick" `:932-938`; name+desc prompt `:901-912`; candidate map by `name.lower()` `:906-912` + `:1001-1012` (**collision-blind** — same-name DB+file would resolve arbitrarily). Failure → `_degraded_select` `:1040-1075`. |
| Result shape | `{"injected": [{"skill": <obj>, "score": f}], "low_match": [{"name","score","description"}]}` — consumers tolerate additive keys (tool serializes whatever keys exist). |
| Trigger-query generation | `:310-312` reads name+description+content (the index-time pattern Q3 reuses). |
| REST passthrough | `routers/skills.py:1033-1056` — `/api/skills/search` passes through the service ⇒ fuses automatically. |
| Corpus scale precedent | 200/bucket fetch cap shows the DB side already tolerates full-corpus loads at search time; file side is an order of magnitude smaller (tech §Q2). |

## C. Tool surface + load/inject chain (explorer evidence E2)

| Anchor | Detail |
|---|---|
| Tool set | `daemon/tools/skill_tools.py` — 6 tools in order `[skill_search, skill_list, skill_view, skill_create, skill_fix, skill_feedback]` **[verified this session: factory docstring]**. |
| Project resolution (tool lane) | `_get_project_id` `:108-119` — reads `instances.project_id` ROW column **[verified this session]**. |
| `skill_view` truncation | 8000 chars `:325-328` (uniform cap; file skills keep it + append body_path + read_file hint). |
| load_skill chain | `send_message(load_skill=…)` → `<meta>` tag `daemon/tools/instance.py:4329-4346` → `instance_messaging.py` extract `:3877` → `SkillInjectionService.inject_explicit_skill` `:419` → `clone_on_miss_sync` call at `:454/:479` → `skill_clone_service.py:143` (**MUTATES DB on BOTH paths: refresh-on-existing AND clone-on-miss** — the file path must intercept BEFORE it). |
| Injection formatter | reads `.content` `:1019`, `.ab_test_group` `:639`, `.status` — a duck-typed dataclass satisfies all three; `ab_test_group=None` skips A/B; requirement pre-flight vacuous (no `requirement_json`). Injection rendering has NO length cap (parity requirement for file skills, FR-P5-3). |
| Auto-injection project source | `instance_metadata` JSONB `instance_messaging.py:4310-4316` — the SECOND project-scope source that Q1 must reconcile with the row column. |
| Disk-first read precedent | `skill_include_resolver.py:387-445` — in-repo shape precedent for reading bodies from files. |
| Anti-pattern precedent (do NOT replicate) | `skill_seed_service.py` / `manager.py:3951-3987` copy file bodies INTO `skill_bank.content` — the exact P1-forbidden pattern. |

## D. Project resolution / config / wiring / Gate Tiers (explorer evidence E3)

| Anchor | Detail |
|---|---|
| Skill-service wiring | `daemon/manager.py:2222-2276` (construction, gated by `config.skill_evolution is not None`), `:2426`, `:3951-3987` (Skill-Seed; bank explicitly NOT gated by evolution — `:3951-3956` = the A1 independence precedent). |
| Boot ensure idiom | `_ensure_postgres_columns`: create-if-absent pattern `manager.py:797-829`; additive-column precedent `:8345-8383` (`skill_bank`). New table ⇒ own `.sql` in `daemon/migrations/versions/` + mirror entry (house rule; 115 migration files exist **[verified this session: ls count]**). |
| Injection-lane project resolution | `graph.py:886-931` `resolve_project_id` — metadata-priority chain + freshness rationale `:900-931` (late bindings must not be missed). |
| Spawn metadata keys | `instance_lifecycle.py:2343` — `project_id`/`instance_name`/`invoked_as_tool`/`model_override`/`mcp_tool_names`/`source_type`/`original_source`; **NO `work_dir`** ⇒ the work_dir convention (A3) is inert until a spawner adopts it. |
| Workdir default | `daemon/tools/instance.py:1469-1490` `_get_project_workdir` → project `main_directory`. `:3256-3259` = the `project_id` auto-inherit precedent the future `work_dir` stamping should mirror (A3). |
| Project model | `repositories/project/models.py:345` (`main_directory`), `repository.py:327-335` (directory-based lookup). |
| Readiness advisory precedent | `daemon/services/readiness.py:512-531` (`detail.grace` / `detail.mcp` advisory map — always published, NEVER flips status/components/reasons). Requirements cites `:402-407`, `:517-544`. |
| Embedding service | `config.py:4398` default embedding model; trigger-query generation rides `SkillEvolutionConfig.analysis_model` fallback (cheap tier) — same cost profile as `create_skill`'s best-effort `_refresh_embeddings`. |
| Embedding storage | `SkillEmbedding` model `daemon/repositories/skill/models.py:563`, `__tablename__ = "skill_embeddings"` `:590`, index `:591`; carries `(id, skill_id, trigger_query, embedding)` — **HISTORICAL/PRE-A1 (iteration 003): the "reusable with NO schema change" claim was superseded by architect A1 (the `skill_embeddings` FK wall at `models.py:594-601` rejects every `pf-` insert on PG); embeddings now live in the dedicated no-FK `project_skill_embeddings` table.** `skill_id` is TEXT ⇒ `pf-<sha16>` pseudo-ids fit; cleanup `DELETE WHERE skill_id LIKE 'pf-%'` is surgical (now applies to `project_skill_embeddings`). **[verified this session]** |
| Usage records | `skill_usage_records` keyed by TEXT `skill_id` ⇒ metrics-only `skill_feedback` for `pf:` ids costs nothing new (Q5). |
| Gate Tiers | `.agents/tester/rules/ensure.md` — Tier 0 (no regression leg) / Tier 1 (blast-radius ≤30 min HARD) / Tier 2 (whole-tree ≤90 min advisory). Core-seam mechanical list: `daemon/graph.py`, `daemon/manager.py`, `daemon/api.py`, `daemon/services/instance_messaging.py`, `daemon/services/task_processor.py`, `daemon/services/job_queue_service.py` **[verified this session: ensure.md head]**. `manager.py` in the diff ⇒ Tier 2 mechanically. |
| Gate recipes | `.agents/tester/skills/whole-tree-base-proven-diff/` (loads `failed-digest-equality-gate`), `regression-ab-partition-run/`, `blast-radius-leg/` — deterministic path load, never `skill_search` discovery (the G2 lesson). |

## E. Gotchas (each one is a plan-level constraint or test target)

1. **`env -u POSTGRES_*` is a silent NO-OP** (bash does not glob env names) — any phase-0 DB census or real-PG test that means to scrub prod env must scrub per-variable (`env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT`). Standing project convention note.
2. **Frozen-binary / daemon-child boot smokes must pin `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` explicitly** — ambient `=0` guards are stripped by `env -u` and the sweep can kill LIVE processes. Applies to any phase-4/5 isolated boot leg.
3. **Test scripts must NEVER resolve live-shaped paths** (HOME-derived install dirs) — fence via `mktemp` FAKE_HOME + escape guard (2026-10-12 double-deletion incident). Phase-0 scratch scripts live in `/tmp` with fake HOME.
4. **Live DB census (phase 0) is SELECT-only** — the legacy-skill census reads; zero writes to live rows.
5. **Legacy conflation** — `.agents/project-skills/bump-version` must never be ingested; dual-leg exclusion (path shape + `schema_version==1`) with the negative fixture pinned (FR-VAL-1).
6. **Shadow-before-stage-3 is load-bearing** — stage 3 maps by `name.lower()` and is collision-blind; dedupe upstream of stage 1's corpus is the only point that closes both the BM25 doc-count and the name-map window at once (Q4).
7. **`clone_on_miss_sync` writes on BOTH paths** (refresh-on-existing AND clone-on-miss) — the step-0 interception must sit before the call site, and the non-mutation pin must assert `skills` row count unchanged after explicit file-skill inject (risk G-3).
8. **No sync file I/O on the event loop** — corpus reads and body reads go through `asyncio.to_thread` (house lesson from the 2026-10-26/30 incident class; the corpus read is the hot path).
9. **Embedding refresh must be fire-and-forget** — a `git pull` mid-session must never add latency to a turn; stale embeddings only perturb stage 2 (BM25 always fresh); `embedded_at`/`last_error` visible.
10. **Knob-name discrepancy** — requirements §6.1 names `ENSEMBLE_PROJECT_SKILL_LOADER_ENABLED`; technical-analysis (postdating, authoritative per commission) names `ENSEMBLE_PROJECT_SKILL_SOURCE_ENABLED`. Plan uses the technical-analysis name.
11. **FR-P7-3 ("no embeddings computed") vs Q3 (derived embeddings)** — reconciled: FR-P7-3 forbids EVOLUTION-lane machinery (skill-keeper triggers, A/B, embeddings as evolution artifacts); Q3's `(trigger_query, embedding)` rows under `pf-` pseudo-ids are SEARCH-substrate derived index metadata (P1-permitted), computed by the same best-effort provider the DB search side uses. Recorded in decisions.md D-Q3; flagged to the architect (OQ-4).
12. **Kill-switch phrasing** — requirements §6.3 wants disable "for the NEXT search" without restart; a boot-time env knob achieves OFF-at-construction (file_source=None everywhere ⇒ byte-identical DB behavior) but env changes require process restart to become visible at all (os.environ is process-inherited; .env edits do not mutate a running daemon). Plan ships the boot-time knob and records the deviation (decisions.md D-Q8).
13. **Worktree canon drift** — v1 indexes `projects.main_directory` (the shared checkout for THIS project); a worktree lane's new/edited skills are invisible to search until merged/checked out there. Same reality as every other repo-committed resource. `work_dir` metadata convention = v2 (A3).
14. **Migration/stage standing rules** — **HISTORICAL/PRE-A1 (iteration 003): the "+1 expected" delta was superseded — the lane delta is now +2 (`create_project_skill_files.sql` + `create_project_skill_embeddings.sql`, A1) and +3 if the Phase-1 D2 census gate selects the usage companion table.** Non-empty migration delta still demands explicit `rollback_safe` semantic adjudication at stage (additive CREATE TABLE class = board/notify_audit precedent); migration-delta disclosure rides the stage/ceremony record; `known_schema_gen` becomes the lexically-latest new file.
15. **Prompt-artifact baseline** covers `agents/` prompt artifacts (161 files); the skill canon lives under `.agents/` — no baseline impact expected, but phase 5 should assert the baseline stays clean at stage time (house PROMPT-BASELINE check).
16. **Caps discipline** — `max_inject_skills=2` / bm25 top_k 10 / llm_select 5 / low_match 3 are untouched; the file corpus gets its own budget (50) at assembly so a degenerate repo cannot crowd the file side out of the FETCH (ranking still competes uniformly after assembly).
17. **Pseudo-id shape discipline** — `pf-<sha256(project_id|namespace|name)[:16]>` is deliberately never uuid-shaped: collision-proof vs real DB rows, SQL-greppable, surgical cleanup.

## F. Phase-0 measurement targets (named by technical-analysis §G)

1. Canonical-file counts per project beyond agents-ensemble — read-only sweep over `projects.main_directory` for `.agents/*/skills/` trees (answers "does the gap multiply today?").
2. Cold-scan + stat-gate cost at canon scale (expect ms; pins the `last_scan_ms` budget and NFR-PERF-1's ≤2 s).
3. Embedding regeneration cost per digest flip (5 skills × ~3-10 triggers; one measured run sets the async-refresh expectation; N/A-recording acceptable if the provider is unavailable in dev).
4. Live DB census of the shadow-retirement set (`9e79ccad`, `fd1e01dc`, `76e3e1a7a`, `08e67030` — existence/generation state), SELECT-only, so the P4 retirement at enable-time is a known diff.

Plus: `.agents/` tree inventory across the resolved project (namespaces present, legacy dirs present) for the anti-conflation fixture design.

Architecture does not depend on these outcomes; they bound cost claims and pre-answer the P4 retirement census (tech §G verdict: LIGHT phase 0 warranted).
