# Decisions — Project Skill Source Loader

Date: 2026-10-31
Author: planner[v2] via plan-creation worker
Status: Final
Provenance legend: **UR** = user-ruled (binding) · **TA** = technical-analysis.md recommendation (authoritative input; postdates requirements.md and reconciled it) · **PA** = planner adjudication (binding for this plan) · **LR** = leader ruling · **A#** = architect amendment (architecture-recommendation.md, 2026-10-31; verdict SOUND-WITH-AMENDMENTS) · **DEF** = deferred with recorded owner.
Amendment fold: A1–A18 + advisory register S1–S6 folded into this file on 2026-10-31 per architecture-recommendation.md §6; every folded block carries its provenance line. Binding-principles section (Part 1) text is UNCHANGED — post-amendment compliance is confirmed in Part 6.

---

## Part 1 — Binding Principles (P1–P7)

Every principle below is USER-RULED and HARD. Deviations are escalation-only (open questions), never silently bent.

### D-P1 — FILES ARE CANON, DB IS A VIEW
- **Decision:** The loader indexes `skill.json` manifests; load/inject reads the FILE from disk at call time; skill bodies are NEVER copied into any DB table (no `skills.content`, no `skill_bank.content`, no embeddings payload, no new-table body column). Derived index metadata rows are permitted (identity, name, description, digests, manifest metadata, health); bodies are not.
- **Provenance:** UR — user-ruled 2026-10-31, via planner commission brief.
- **Enforcement in plan:** Phase 1 ships the sentinel test (FR-P1-5: unique body sentinel grepped to zero rows across all skill tables after an index+search+load cycle); `ProjectFileSkill.content` is populated ONLY by fresh disk reads; `project_skill_files.manifest_json` stores manifest metadata only.

### D-P2 — SEARCH FUSION WITH SOURCE TAGS
- **Decision:** `skill_search` merges BOTH sources; every result entry (injected AND low_match) carries additive `source: "db" | "project-file"`.
- **Provenance:** UR — user-ruled 2026-10-31, via planner commission brief.

### D-P3 — PROJECT-SCOPE BINDING (SECURITY-GRADE)
- **Decision:** Project skills surface ONLY for callers resolving into that project. Never cross-project leak, even under a caller-supplied conflicting path/project_id. No project context ⇒ file skills entirely invisible; load of a file address fails closed.
- **Provenance:** UR — user-ruled 2026-10-31, via planner commission brief.
- **Enforcement:** Phase 2 ships the seeded-A/search-under-B pin (FR-P3-4) as a security-grade test.

### D-P4 — SAME-NAME PRECEDENCE (SHADOWING)
- **Decision:** A project file skill SHADOWS the same-name DB skill within that project's scope — structurally retiring the 3 legacy DB gate-runner copies (`9e79ccad`/`fd1e01dc`/`76e3e1a7a`). The shadow is HIDDEN (absence of the DB result + `shadows` tag on the file result), never a visible "shadowed" tag on the DB row (that would re-expose deprecated content).
- **Provenance:** UR — user-ruled 2026-10-31, via planner commission brief. Persistence posture refined by PA-A2 below.

### D-P5 — INJECTION STAYS PER-AGENT OPT-IN
- **Decision:** `skill_injection` flag semantics untouched; no new injection defaults. File skills are reachable via search + manual load by default; they ride the existing injection gate exactly as DB skills do for `skill_injection=true` agents.
- **Provenance:** UR — user-ruled 2026-10-31, via planner commission brief.

### D-P6 — CROSS-AGENT VISIBILITY WITHIN THE PROJECT
- **Decision:** All `.agents/*/skills/` namespaces are readable/searchable by any agent working in that project. Namespaces are organizational metadata, never ACLs. The index records the owning namespace for display/filtering only (FR-P6-3).
- **Provenance:** UR — user-ruled 2026-10-31, via planner commission brief.

### D-P7 — EVOLUTION VIA GIT PRS ONLY
- **Decision:** File skills evolve via git PRs against `.agents/<ns>/skills/` — never `skill_fix`; skill-evolution machinery (skill-keeper, trigger engine, A/B) untouched for file skills. `skill_fix` on a `pf:` address fails closed with git-PR guidance; `skill_feedback` accepts `pf:` ids METRICS-ONLY (no lineage rows, no evolution dispatch — the usage signal is exactly what a git-PR evolution decision needs).
- **Provenance:** UR — user-ruled 2026-10-31, via planner commission brief. Metrics-only feedback mechanics from TA §Q5.

---

## Part 2 — Design Decisions Q1–Q8 (+Q8b)

Canonical numbering = technical-analysis.md / commission context. requirements.md §11's local numbering is folded as noted per row (no parallel numbering is created).

### D-Q1 — Active-project resolution (covers requirements §11 Q1 worktree roots + Q2 scope reconciliation)
- **Decision:** ONE canonical resolver `resolve_active_project(instance_id, explicit_project_id=None)` in the new `project_skill_source` service is the ONLY place turning `instance_id → (project_id, file_root)`. Priority chain: (1) explicit tool param → (2) `instance_metadata["project_id"]` (injection lane, per-turn fresh) → (3) `instances.project_id` row column (tool lane) → (4) None. Sources 2+3 disagree ⇒ proceed with 2 + WARNING with both values (drift signal). File-root chain: `instance_metadata["work_dir"]` if present (inert hook, v2 plumbing) → `projects.main_directory` → no root ⇒ file source contributes nothing (graceful zero, never an error). Index keyed by RESOLVED ABSOLUTE ROOT (two callers, different roots, same project_id ⇒ P3 holds; cross-worktree leak structurally impossible).
- **Rationale:** both lanes already resolve independently today with different sources; one resolver kills split-brain (FR-P3-5) while preserving the injection lane's freshness rationale (`graph.py:900-931`).
- **Alternatives rejected:** row-column-only (misses late bindings); metadata-only (leaves the lanes disagreeing in code); per-call bash-cwd sniffing (daemon has no reliable access to caller's shell cwd).
- **related_directories:** NOT scanned in v1 — the TA file-root chain omits them; the resolver hook + documented `work_dir` convention covers the worktree case when a spawner adopts it (requirements §11 Q1 constraint satisfied: one consistent rule applied to scan roots, scope binding, and leak tests).
- **Provenance:** TA §Q1 recommendation; PA notes the related_directories exclusion explicitly. Resolver-grep fence (no direct `metadata.get("project_id")` outside resolver/graph-legacy) from TA risk G-2.
- **AMENDMENT — architect A6 (MUST, CRITICAL), 2026-10-31: RUNG 1 IS CONFIRM-ONLY; REST SERVES NO FILE SKILLS IN v1.** `SkillSearchRequest` carries a caller-supplied `project_id` passed straight into search (`routers/skills.py:1032-1056`, consumed `:1051`); the router has no auth dependency of its own, and even auth-on deployments let any authenticated principal pass any project. Search output carries bodies ⇒ rung-1 "explicit wins" = **cross-project body read = direct P3 violation**. New rule: the explicit param may only **confirm**, never widen — `explicit ∈ {None, instance-derived}` ⇒ use instance-derived; explicit present and conflicting ⇒ **refuse the file corpus for that call** (WARNING with both values; DB results unaffected). Consequence: REST `/api/skills/search` contributes **zero file skills** in v1 (REST-invisibility pin; phase2 task 10 superseded from REST-parity). A server-side REST binding (auth principal → project) is the v2 path — registered as **leader ruling D1** parked item. **AMENDMENT — architect A13 (MUST), 2026-10-31: the resolver is the SANCTIONED SCOPE READER FOR NEW CODE (planner ruling S3, 2026-10-31) — BOTH sources fenced with an explicit allowlist, with the 3 allowlisted sites (graph.py:886-931 legacy / instance_messaging.py:4570-4573 (corrected at fold, iteration 003) / skill_tools.py:107-119) remaining until the v2 migration:** the grep fence extends to the row-column source too; allowlist = those 3 — or migrate those three call sites onto the resolver. `skill_view`'s `pf:` resolution = resolver(`current_instance_id`); no caller-supplied project hint.

### D-Q2 — Indexing trigger + invalidation (covers requirements §11 Q8 index storage, storage half)
- **Decision:** LAZY per-root index on first search/load touch (`ensure_index(root)`, idempotent, per-root in-process lock) — nothing at boot beyond cheap service construction. Invalidation = per-access freshness gate: stat fast-path (mtime+size per manifest) → sha256 digest on suspicion → re-parse/re-embed changed, drop vanished. Storage = HYBRID: in-memory parse cache + fresh file reads (canon), one durable DB table `project_skill_files` (derived metadata/digests/health only), reuse of existing `skill_embeddings` for persisted vectors (D-Q3). Hard scan caps: ≤64 namespaces, ≤128 skill dirs/ns, manifest ≤16 KB, body ≤64 KB (`body_too_large` ⇒ indexed-as-listed, excluded from corpus).
- **Rationale:** boot scan pays for untouched projects and slows boot (house trend is readiness-grace, opposite direction); periodic sweep unnecessary once digest revalidation exists; restart would silently orphan embeddings without the durable digest record.
- **Alternatives rejected:** boot scan of all projects; periodic sweep; pure in-memory (orphaned embeddings vs changed content after restart); body-in-DB (P1-forbidden outright).
- **Provenance:** TA §Q2/§B recommendation. NFR-IDEM-3's "explicit refresh not implicit per-search" is satisfied at the STAT level (µs per manifest) — the digest gate IS the explicit refresh mechanism; recorded as the reconciling interpretation.

### D-Q3 — Ranking/embeddings without violating P1 (covers requirements §11 Q4 body-size-cap's caps; embeddings herein)
- **Decision:** Index-time trigger-query generation (3-10 queries from name+description+body via the cheap/analysis tier) + embedding via the existing `SkillEmbeddingService`, persisted as `(trigger_query, embedding)` rows in the EXISTING `skill_embeddings` table with `skill_id = pf-<sha256(project_id|ns|name)[:16]>` (stable pseudo-id; never uuid-shaped). Refresh on digest flip = delete that skill's rows + regenerate async (fire-and-forget). Embeddings unavailable ⇒ file skills stay BM25-first with stage-2 score 0.0 neutral (byte-identical to DB no-embeddings behavior `:829-835`); failure NEVER fails a search.
- **Rationale:** mirrors the proven DB ranking pipeline exactly; pseudo-id shape gives collision-proofing + surgical cleanup; no new table, no vector extension, no schema delta for the embedding path.
- **Alternatives rejected:** BM25-only (permanently second-class ranking); embed-description-only (wastes the strongest signal).
- **P7 tension — RECONCILED:** requirements FR-P7-3 says "no embeddings computed" for file skills. Interpretation binding for this plan: FR-P7-3 forbids EVOLUTION-lane machinery (skill-keeper, trigger engine, A/B); the `pf-` rows are SEARCH-substrate derived index metadata (P1-permitted), computed by the same best-effort provider the DB search side already uses at create/content-change. The commission context ratifies this shape verbatim (canonical Q3). Bodies still never stored anywhere. Flagged to the architect as OQ-4 for confirmation.
- **Provenance:** TA §Q3 recommendation; reconciliation adjudicated by planner from the commission context's canonical Q3 wording (PA); flagged UR-boundary question to architect (OQ-4).
- **AMENDMENT — architect A1 (MUST, CRITICAL), 2026-10-31: STORAGE VEHICLE SUPERSEDED.** `skill_embeddings.skill_id` = `ForeignKey("skills.id", ondelete="CASCADE")` (`daemon/repositories/skill/models.py:594-601`, architect re-verified). On PG, `SQLModel.metadata.create_all` (`manager.py:906-909`) materializes the constraint strictly BEFORE `_ensure_postgres_columns` (`:931`), so **every `pf-` insert raises ForeignKeyViolation**, per-row caught (`skill_embedding_service.py:622-627`) ⇒ `written=0` ⇒ file skills silently, permanently BM25-only on PG (fresh installs AND the remote replicas) plus retry/log churn. The "no schema delta for the embedding path" claim above is FALSE on the canonical PG path. **Required shape:** dedicated `project_skill_embeddings` table (own `.sql` per house rule + PRE-`create_all` model registration), columns mirroring `models.py:601-607` (`skill_id`, `trigger_query`, `embedding`), **no FK**, `skill_id` = the pseudo-id. Lane migration delta becomes **+2**. **Rejected alternative (recorded per fold contract):** dropping the FK from `skill_embeddings` — schema mutation of an evolution-lane table, breaks rollback posture, diverges fresh vs long-lived DBs. F1 note: the P7/P1 RECONCILIATION itself is CONFIRMED (trigger-query generation imports zero evolution machinery — verified pure LLM call); only the storage vehicle is rejected.
- **AMENDMENT — architect S1 (advisory, folded), 2026-10-31:** trigger-query generation rides the **MAIN model** (`manager.py:2227-2232` — the `skill_llm_config` dict, `"model"` key at :2232; citation corrected at fold, iteration 003: the earlier :2233 anchor pointed at the comment line, not the key — → `skill_embedding_service.py:666-670` prefers `llm_config["model"]`; analysis tier is fallback-only), and the real pattern anchor is `skill_embedding_service.py:285/:542`, NOT `skill_search_service.py:310-312` (a JSON helper). Cost-budget derivation must use these anchors — not the citation above (retained as original text for traceability).

### D-Q4 — Same-name precedence mechanics
- **Decision:** THREE enforcement points: (1) **corpus assembly** — the load-bearing one — at the post-ownership-filter seam (`skill_search_service.py:709`) BEFORE stage-1 tokenization: append file skills, then a name-shadow pass drops DB rows whose `name.lower()` matches any file skill in scope, stamping `shadows` on the file twins (upstream of BM25 doc-count math AND stage-3's name map — closes both collision-blindnesses in one place); (2) **defensive post-stage-3 dedupe** (refactor-drift belt-and-suspenders, pinned); (3) **load resolution** — file-first step 0 before `clone_on_miss_sync` (a file hit never reaches the bank-clone path). Shadow representation: file result present with `source:"project-file"` + `shadows:[...]`; DB twin ABSENT (hidden, not tagged).
- **Alternatives rejected:** merge-point-only enforcement (too late: stage-1 dedupe is id-based, stage-3 map is name-collision-blind); load-only enforcement (search would still surface the shadowed DB row); visible "shadowed" tag (re-exposes deprecated content — violates P4's structural-retirement intent).
- **Provenance:** TA §Q4 recommendation; FR-P4-3 (shadow before stage 3) is a requirements Must — satisfied by enforcement point (1).
- **AMENDMENT — architect A7 (MUST, CRITICAL), 2026-10-31: SHADOW SET COMPUTED FROM THE FULL FILE CORPUS BEFORE BUDGET TRUNCATION.** Order as originally planned (append → budget(50) → shadow) lets a file skill dropped by overflow leave its DB twin **unshadowed and surfacing** — deterministic ordering, nondeterministic P4 semantics: whether a name is retired silently flips as file count crosses 50, and the surfaced twin is precisely the stale duplicate P4 exists to kill. **Required invariant:** shadow set ⊇ every in-scope DB name matching ANY in-canon file skill, computed PRE-budget. **Preferred variant:** shadowing names are EXEMPT from the budget (cap applies to non-shadowing file skills — availability preserved, P4 holds). Acceptable variant: full-corpus shadow ⇒ both hidden under overflow, surfaced via a dedicated overflow counter. Pin: >50 file skills incl. one DB twin ⇒ twin absent from injected AND low_match, every variant.

### D-Q5 — Load/inject path + identity (covers requirements §11 Q3 address format)
- **Decision:** Two-part identity: tool-surface address `pf:<namespace>:<name>` (human-typeable, namespace-organized per P6, round-trips through any `skill_id`-accepting tool via prefix check) + internal stable pseudo-id `pf-<sha16>` (the `.id` on the duck-typed object; rendered in stage 3, `skill_usage_records`, `skill_embeddings`, index PK). Surfaces: `skill_search` (fused, tagged); `skill_view` accepts DB id OR `pf:` address — renders header metadata + body FROM FILE + `## Loads`/`## Assets` metadata section; keeps the uniform 8000-char cap + appends body_path + read_file hint; `skill_list` lists file skills with `source` tag (namespace-grouped) incl. `invalid` entries with reasons; **`skill_fix` REFUSES** `pf:` (P7, fail-closed with git-PR guidance); **`skill_feedback` ACCEPTS** `pf:` metrics-only. Interception: STEP 0 at the top of `inject_explicit_skill`, BEFORE `clone_on_miss_sync` — resolve bare name or `pf:` address against the caller's project scope; on hit render from fresh file body through the UNCHANGED formatter (duck-typed object), `injected_skill_ids=[pseudo_id]`, ZERO DB writes; on miss the DB clone path runs byte-identically. Injection rendering carries a one-line provenance header (`[project-file skill: pf:tester:x @ <body_path> v1.0.0]`). Full-body injection (no cap) = DB parity (FR-P5-3).
- **Alternatives rejected (address):** manifest-path-as-address (leaks layout into prompts, breaks on re-org); pseudo-UUID (opaque, collides conceptually with real DB ids).
- **Provenance:** TA §Q5/§D recommendation.
- **AMENDMENT — architect A2 (MUST, CRITICAL), 2026-10-31: ZERO FK-BOUND WRITES ANYWHERE ON THE FILE PATH.** `skill_usage_records.skill_id` carries the same FK (`models.py:378-392`, architect re-verified): a usage row under a `pf-` id hits the same wall — and if uncaught it breaks injection of file skills outright. **Required:** Phase 1 runs a WRITE CENSUS (every DB write reachable from index/search/load/feedback on the file path, enumerated as a pinned artifact); rule = the file path performs ZERO writes into FK-constrained child tables. Implementation fork (resolved in Phase 1 BY CENSUS EVIDENCE, leader ruling D2 pre-authorizes): (a) small companion `project_skill_usage_records` table (keeps the D-Q5 metrics value; +1 migration ⇒ lane delta +3), or (b) if the census proves injection writes no usage row, defer `pf:` feedback behind an EXPLICIT "not supported in v1" envelope (never a silent `recorded:false`). **DEFAULT = companion table if any usage write exists.**

### D-Q6 — assets/loads scope
- **Decision:** v1 = metadata-surfacing + deterministic name-resolution + index-time VALIDATION of references; recursive resolution and asset serving deferred v2. Concretely: `loads`/`assets` carried as metadata arrays on index/result objects; `skill_view` renders `## Loads` with each dependency's `pf:` address + one-line description; explicit `load_skill` resolves depth-0 only (no transitive injection — would blow `max_inject_skills=2` semantics); index-time validation fails loud on broken references (Q7 semantics). Gate determinism holds in v1 because the ensure.md mandate + name resolution reach every transitive dependency by name — exactly the mechanism the tiered-gate-doctrine ratified 2026-10-31.
- **Rationale:** `assets[]` empty across the canon (no consumer); `loads[]` populated in 3 of 5 and doctrine-load-bearing; recursion adds cycle/budget classes with no consumer beyond convenience.
- **Alternatives rejected:** full recursive v1 (cycle/budget risk, no consumer); pure body-only v1 (breaks the doctrine's dependency chain visibility).
- **Provenance:** TA §Q6 recommendation.

### D-Q7 — Validation + failure modes (covers requirements §11 Q4 body-size cap)
- **Decision:** Per-skill fail-loud-skip: validate at index (JSON parses → `schema_version==1` (unknown ⇒ skip, warn ONCE per root) → required keys `name`/`description`/`body` → `name == dir name` → body exists, ≤64 KB (empty or over ⇒ skip + error) → `loads[]` names resolve in-namespace, `assets[]` paths exist → `supersedes` shape valid). ANY failure ⇒ skip that skill, WARNING with path+reason, `last_error` on the index row, scan continues; aggregate in `detail.skill_sources.invalid_skills`. Load-time re-check: digest mismatch ⇒ serve fresh file content anyway (file is canon — freshest truth wins), INFO + async re-index; missing/corrupt body at load ⇒ explicit error envelope ("body unreadable; re-index scheduled"), never an empty injection. Legacy exclusion DUAL-LEG: (1) path shape `.agents/<ns>/skills/<name>/skill.json` with a real `skills/` segment; (2) `schema_version==1` — `.agents/project-skills/bump-version` fails both legs (no `skills/` segment, no `schema_version`); negative fixture pinned (FR-VAL-1). Unknown extra manifest fields tolerated (forward-compat) + debug-logged.
- **Alternatives rejected:** hard error (one bad dir must never wedge search/boot); silent skip (invisible failure recreates the G2 failure mode).
- **Provenance:** TA §Q7 recommendation; requirements §4.4 validation rules folded verbatim (body-size cap = 64 KB from TA §Q2, satisfying requirements §11 Q4's "whatever cap is chosen must be documented and tested").

### D-Q8 — Config surface + kill-switch (+Q8b evolution-gate independence)
- **Decision (Q8):** Knob `ENSEMBLE_PROJECT_SKILL_SOURCE_ENABLED`, default ON (house kill-switch idiom family), + `ENSEMBLE_PROJECT_SKILL_SOURCE_EXTRA_ROOTS` (comma-separated dev-only debug roots, default empty, documented non-production). Default ON is correct because: (a) inert-absent — projects without a canon contribute nothing; (b) P4 shadowing of the 3 legacy DB copies in THIS project is the DESIRED outcome; (c) P5 holds (no injection default changes); (d) promote-safety — additive `/readyz` detail only, never flips readiness; reads repo files it already has access to. Observability: per-source result tags, index events INFO, shadow retirements INFO, `detail.skill_sources` advisory block (modeled on `detail.grace`/`detail.mcp`, `readiness.py:512-531`) carrying enabled/db_stack_present/roots[namespaces, skills_indexed, invalid_skills, last_scan_ms, last_scan_at, last_error]/shadowed_db_skills/embeddings_pending — published whenever constructed, empty-roots = honest zero.
- **Decision (Q8b):** The file source is INDEPENDENT of the `config.skill_evolution` gate. `ProjectSkillSourceService` constructed UNCONDITIONALLY (no LLM/DB at construct). `SkillSearchService` gains optional `file_source=None` param — present ⇒ fusion; absent ⇒ unchanged DB behavior (REST fuses automatically via passthrough). Degraded-mode (evolution None): `skill_search`/`skill_view`/`skill_list` serve FILE skills (per-source tool guards — file source available ⇒ answer from it; DB service None ⇒ file-only instead of blanket "not yet available"); `skill_create`/`skill_fix` stay evolution-gated; explicit `load_skill` injection stays evolution-gated in v1 (lives behind SkillInjectionService/clone construction — un-gating is v2, core-seam weight, no demonstrated need). Four knob combinations all well-defined: (ON,present)=full fusion; (ON,absent)=file-only search/list/view; (OFF,present)=today exactly; (OFF,absent)=today exactly.
- **Alternatives rejected (Q8b):** gating behind `config.skill_evolution` — couples a file-reading, LLM-optional source to an evolution gate; in evolution-off deployments agents lose file skills entirely, recreating G2 blindness. Contradicts the in-tree Skill-Bank precedent (`manager.py:3951-3956`: seeding is standalone infrastructure, NOT gated by evolution).
- **Alternatives rejected (Q8 default):** default-OFF — conservative but delays P4 retirement indefinitely and adds an operator step for an inert-absent source.
- **Deviation recorded (requirements §6.3):** requirements asks for disable "for the NEXT search" without restart; the v1 knob is boot-time env (OFF ⇒ `file_source=None` injected everywhere ⇒ byte-identical DB behavior from construction). Honest physics: `os.environ` is process-inherited — no boot-time env knob can flip on a running daemon; a hot (DB-settings) switch is a v2 item. Deviation flagged; routed as informational in OQ-5.
- **Provenance:** TA §Q8 recommendation; Q8b independence ALSO ratified as PA-A1 (below). Knob NAME per TA (`..._SOURCE_...`), overriding requirements §6.1's `..._LOADER_...` (TA postdates, reconciled; discrepancy noted per commission instruction).
- **AMENDMENT — architect A18 (MUST), 2026-10-31 + leader ruling 2026-10-31: DEFAULT-ON RATIFIED ON CORRECTED GROUNDS.** TA §Q8's "same family … default ON" precedent citation was factually wrong — the kill-switch family is **MIXED**: evolution lane default-OFF (`config.py:4486-4487`, incident-driven), notify-user ON (`:3078-3085`), hard-reboot pass ON (`message_queue_boot_recovery.py:55-60`). The true house rule (two-class): **ON for zero-user-action read/serve surfaces; OFF for agent-authored mutation lanes.** File source = ON class; weighted 4.25 vs 3.90. **Falsifiability condition:** the Phase-0 collision census RE-OPENS the fusion/shadow split if collisions exceed the 3 known legacy ids (`9e79ccad`/`fd1e01dc`/`76e3e1a7a`) — fusion ON, shadowing gated (Option C's only legitimate use); recorded as a named Phase-0 gate + stage-time gate input.
- **AMENDMENT — architect A18.3, 2026-10-31: shadow-decision log level is WARNING, not INFO** — shadowing is the only non-additive effect in the surface; it must be greppable by an operator who doesn't know to look.
- **AMENDMENT — architect A18.2, 2026-10-31: DUAL ceremony disclosure required** — (a) P4 shadowing of the 3 legacy ids in this project; (b) THIS install runs evolution-OFF ⇒ first boot activates file-only search/list/view where tools today say "not yet available"; PLUS the REST/FE consequence: the FE Skills page keeps showing the 3 legacy DB skills as active with no shadow indication (REST list is DB-only per A6/S2).
- **AMENDMENT — architect A11 (MUST), 2026-10-31: readiness advisory ALWAYS publishes with `enabled: true|false`** (resolves the internal contradiction; overrides the absent-when-OFF suggestion — observability of a disabled feature is what an operator needs at 2am; `detail` is additive). Consequence: the OFF claim narrows from "byte-identical" to **"byte-identical search/list/view RESULTS"** — `/readyz` detail legitimately differs under OFF; the OFF-side pin extends to assert zero `pf-` rows in any skill table.

---

## Part 3 — Planner Adjudications (binding; provenance "planner adjudication of worker open questions, 2026-10-31")

### PA-A1 (canonical Q8b) — File source independent of the evolution gate
- **Decision:** as D-Q8b. **Rationale:** Skill-Bank precedent (`manager.py:3953-3955` — `seed_all` runs regardless of evolution config); the file canon is currently the ONLY authoritative copy of the gate recipes while the evolution lane is disabled — gating file skills behind evolution would defeat the commission's purpose. The only switch is `ENSEMBLE_PROJECT_SKILL_SOURCE_ENABLED`.
- **Provenance:** planner adjudication of worker open questions, 2026-10-31 (convergent with TA §Q8 recommendation).
- **AMENDMENT — architect A9 (MUST, CRITICAL), 2026-10-31: (ON, DB-ABSENT) FILE-ONLY MODE GETS PINNED SEMANTICS — IT IS A THIRD BEHAVIOR SURFACE.** `SkillSearchService`/`SkillInjectionService` are constructed only inside the evolution gate (`manager.py:2223-2276`, `:2426-2432`; else-branch sets attrs to None — corrected at fold per N4: search None at `:2276`, injection None at `:2495` (the approver-cited ~2159/:2378 and the earlier :2483-2493 were stale)), so the (ON, absent) mode answers at the TOOL layer with unspecified ranking/shape/caps as planned. The "serve file-only" recommendation STANDS (Skill-Bank precedent + G2 lesson); the semantics are now pinned: **file-only search = stage-1 BM25 over the file corpus ONLY (same tokenizer), `top_k = max_results`, NO stage 2, NO stage-3 LLM** (deterministic, zero LLM cost, works with no evolution config); results in the standard `{"injected", "low_match"}` shape with `source: "project-file"` + a `degraded: "file_only"` marker; `skill_view`/`skill_list` serve files; `skill_create`/`skill_fix` soft-fail as today. Pin ALL FOUR matrix cells against the M0-M11 mode table — phase4-plan.md Appendix A (M0–M11) — not just the 2×2.

### PA-A2 (P4 retirement persistence) — v1 = SHADOW-ONLY, no auto-deactivation
- **Decision:** v1 retires the 3 legacy DB rows by QUERY-TIME SHADOWING only. NO auto-deactivation (`is_active=False` writes) of `9e79ccad`/`fd1e01dc`/`76e3e1a7a`. Shadowing is the retirement mechanism: query-time, knob-reversible (OFF restores DB-first trivially), zero writes to foreign rows. Physical deactivation is an out-of-lane OPERATOR action; recorded as deferred (v2 §H row + open question for the operator/user, not this lane).
- **Rationale:** auto-deactivation is a DB write with its own lineage semantics outside the file-source scope; shadow-only keeps rollback trivially safe.
- **Provenance:** planner adjudication of worker open questions, 2026-10-31 (resolves technical-analysis open question 2).

### PA-A3 (work_dir metadata convention) — DEFER to v2
- **Decision:** v1 resolves scan roots via `projects.main_directory` (+ the inert `instance_metadata["work_dir"]` resolver hook shipped but unpopulated). The recommended future owner for stamping `work_dir` is the SPAWN PATH, mirroring `project_id` auto-inherit (`daemon/tools/instance.py:3256-3259`); actual plumbing is a v2 item with its own gate weight.
- **Provenance:** planner adjudication of worker open questions, 2026-10-31 (resolves technical-analysis open question 3).

---

## Part 4 — Discrepancy Register (requirements.md vs technical-analysis.md; TA wins per commission instruction)

| # | Discrepancy | Resolution |
|---|---|---|
| 1 | Knob name: requirements §6.1 `ENSEMBLE_PROJECT_SKILL_LOADER_ENABLED` vs TA `ENSEMBLE_PROJECT_SKILL_SOURCE_ENABLED` | TA name wins (D-Q8) |
| 2 | requirements FR-P7-3 "no embeddings computed" vs TA Q3 derived embeddings | Reconciled in D-Q3 (search-substrate derived metadata ≠ evolution machinery); flagged OQ-4 |
| 3 | requirements §6.3 no-restart kill-switch vs boot-time env knob | Deviation recorded in D-Q8; hot switch = v2; OQ-5 |
| 4 | requirements §11 local Q1-Q8 numbering vs canonical Q1-Q8 | Folded per row (D-Q1..D-Q8); no parallel numbering created |
| 5 | requirements §4.2 "related_directories/worktrees — see Q1" left open vs TA Q1 chain (main_directory + work_dir hook) | TA chain wins; related_directories not scanned in v1 (D-Q1) |

---

## Part 5 — Amendment Fold Register (architect A1–A18 + leader rulings D1/D2, 2026-10-31)

Verdict: **ARCHITECTURE SOUND-WITH-AMENDMENTS** — the fused source, resolver, `pf:` identity, shadow precedence, and lazy index all SURVIVE; the 18 amendments are surgical (5 CRITICAL: A1, A5, A6, A7, A8). Only ONE settled decision is superseded: D-Q3's storage vehicle (A1). Where the original text above conflicts with an amendment, the AMENDMENT block governs (original text retained for traceability).

### Leader ruling D1 — REST file-skill fusion is a v2 parked item
- **Decision:** A6 rules REST `/api/skills/search` file-invisible in v1 (no server-side project binding: auth principal → project). If REST/FE fusion is ever wanted, that binding is its own small v2 commission.
- **Provenance:** leader ruling 2026-10-31, architect A-amendment (A6) / Decisions Pending #1. Registered in plan-overview's deferred/follow-ups register.

### Leader ruling D2 — A2 write-census fork is PRE-AUTHORIZED to decide in Phase 1 by census evidence
- **Decision:** Phase 1 runs the write census as a NAMED GATE (see phase1-plan.md): enumerate every DB write reachable from index/search/load/feedback on the file path as a pinned artifact; rule = zero writes into FK-constrained child tables. Choose the usage companion table (+1 migration ⇒ lane delta +3) vs deferred `pf:` feedback with an explicit "not supported in v1" envelope BY CENSUS EVIDENCE — **DEFAULT = companion table if any usage write exists.**
- **Provenance:** leader ruling 2026-10-31, architect A-amendment (A2) / Decisions Pending #2.

### Q8 knob posture — RESOLVED (default ON), corrected grounds + falsifier
- **Decision:** default-ON stands, but on CORRECTED grounds (architect A18 + leader ruling 2026-10-31): the kill-switch family is MIXED (evolution lane default-OFF `config.py:4486-4487`; notify-user ON `:3078-3085`; hard-reboot ON), and the governing two-class rule is **ON for zero-user-action read/serve surfaces, OFF for agent-authored mutation lanes** — file source = ON class; weighted 4.25 vs 3.90.
- **Falsifiability condition:** the Phase-0 collision census RE-OPENS the fusion/shadow split if collisions exceed the 3 known legacy ids (`9e79ccad`/`fd1e01dc`/`76e3e1a7a`) — fusion ON, shadowing gated. Enforced as a named Phase-0 gate (phase0-plan.md) and a stage-time gate input.

### Planner ruling B1(a)-census (approver iteration 001, 2026-10-31): D2 write-census gate satisfiable at Phase-1 exit via STATIC census

- **Decision:** the Phase-1 D2 write census is a **static census** of (i) base code reachable from index/search/load/feedback + (ii) the ALREADY-SPECIFIED Phase-3 STEP-0 contract — not a complete census of future code. The usage write is LARGELY PRE-EXISTING BASE CODE: `SkillMetricsService.record_task_completion` (`daemon/services/skill_metrics_service.py:300`; insert path `_record_one` ~`:719`) inserts a `SkillUsageRecord` driven by `last_injected_skill_ids`, which the caller writes at `daemon/services/instance_messaging.py:~4369` (`inject_skills(...)` return tuple). Phase 1 performs ZERO writes into the file path; the blast radius "TOUCHED: none" is CORRECT and explicit.
- **Re-open trigger (binding):** if Phase-3 implementation deviates from the specified STEP-0 contract in a way that changes the write set, the D2 fork RE-OPENS per its own rule (default = companion table if any usage write exists) and must be re-adjudicated before staging.
- **Provenance:** "planner ruling B1(a)-census, approver iteration 001, 2026-10-31". Phase1 exit line reworded to "census artifact pinned WITH METHOD STATED + D2 fork decided" (never "complete census").


- **Planner ruling W-4 (2026-10-31, reviewer W-4): A13 pinned to the ALLOWLIST option.** v1 uses the explicit 3-site allowlist (graph.py:886-931 legacy / instance_messaging.py:4570-4573 (corrected at fold, iteration 003) / skill_tools.py:107-119); migrating those call sites edits mechanical core seams (graph.py, instance_messaging.py per ensure.md:19) and is DEFERRED to v2 with its own gate tier. **Conditional escalation:** if implementation deviates to the MIGRATION option, the phase's gate tier escalates to Tier 2 and the deviation must be re-adjudicated before execution. Provenance lines in phase1 (task 5) and phase3 (task 3).
- **Planner ruling W-5 (2026-10-31, reviewer W-5): KEEP-AND-SPECIFY the `ENSEMBLE_PROJECT_SKILL_SOURCE_EXTRA_ROOTS` knob** (not deferred). The knob is the motivating scenario for A3's root_path keying; keeping the keying without the knob leaves it untested against its reason for existing, and worktree-based dev is this repo's daily reality. **Spec (resolver leg, phase1 task 5):** each EXTRA_ROOTS entry is realpath'd, then must map to the caller's RESOLVED project via the existing project lookup (repository.get_by_directory semantics — main_directory == entry OR related_directories contains entry); an entry is consumed ONLY if it resolves to the caller's project_id. Non-binding entries are IGNORED with a WARNING (fail-closed — the knob must never become a P3 cross-project read vector). A5 containment applies per skill under each accepted root, identically to main_directory. **Pin (phase5):** unbound extra-root entry ⇒ zero file skills from it (negative test); bound entry ⇒ ingested. Knob docs + validation in phase4 task 1.
- **Planner ruling W-6 (2026-10-31, reviewer W-6): INLINE the M0–M11 mode table as Appendix A of phase4-plan.md** (plan set is self-contained; no external artifact exists). Reconstructed as a 2×2×3 matrix (knob OFF/ON × evolution present/absent × file-source state healthy/unreadable/erroring), one-line expected behavior per cell, with a provenance line and a mode-code map pinning M3/M7/M8 to A8's descriptions. All six referencing sites cite "phase4-plan.md Appendix A (M0–M11)" as the pin target.

### Advisory register S1–S6 — folded locations
| Advisory | Folded into |
|---|---|
| S1 (trigger generation rides MAIN model; anchors `skill_embedding_service.py:285/:542`) | decisions.md D-Q3; phase1-plan task 8. (TA §Q3 correction owned by the parallel TA worker, disclosed.) |
| S2 (REST list DB-only via `_LIST_SKILL_FIELDS` allowlist `skill_store_service.py:90`, projection `:516`; FE keeps showing legacy DB rows) | phase2-plan task 10 (REST-invisibility pin); plan-overview Rollout item 5; decisions.md Part 5 D1. OQ-7 framing corrected in plan-overview. |
| S3 (dedicated bounded executor for file reads / pinned concurrency bound; MUST if Phase-0 cold scan >50 ms) | phase2-plan task 2; phase0-plan task 2 (measurement feeds the promotion condition). |
| S4 (wording "index-backed + stat-gate"; degraded-parity pin: file-in-top-5 surfaces, rank-6-10 exclusion by-design uniform competition) | phase2-plan tasks 2/9; plan-overview SC 13 note. |
| S5 (provenance header gains `project_id` + `root_path`; overflow advisory distinguishes shadowing names) | phase3-plan task 1; phase4-plan task 4. |
| S6 (guard `project_id=None` unfiltered embedding scan `repository.py:1933-1941`) | MOOT post-A1 (dedicated table); recorded here as belt-if-reverted. TA §G correction owned by the parallel TA worker. |

---

## Part 6 — P1–P7 POST-AMENDMENT COMPLIANCE CONFIRMATION (architect §4, 2026-10-31)

**All 7 binding principles SURVIVED unviolated post-amendment.** Two were violated AS PLANNED and are CLOSED by amendments (this is disclosed, not hidden): **P3** was violated as planned (rung-1 explicit-widening + zero path containment) — CLOSED by A5 (containment at index AND every read) + A6 (rung-1 confirm-only) + A13 (single sanctioned scope reader); **P4** was breakable as planned (budget overflow could surface the stale DB twin) — CLOSED by A7 (pre-budget shadow invariant). Per-principle:

- **P1** — upheld and STRENGTHENED (A16 metadata-only cache invariant — never body bytes, also a 512 MB worst-case memory bound; A1 moves vectors to a derived no-FK table — vectors were always P1-permitted derived metadata and remain so).
- **P2** — upheld (A6 narrows REST to DB-only in v1 — a P3-driven narrowing, disclosed, NOT a P2 breach: fusion remains total on the agent lane).
- **P3** — violated as planned → **CLOSED by A5/A6/A13**; the "security-grade" claim is only true WITH those pins.
- **P4** — breakable as planned → **CLOSED by A7** (shadow set ⊇ every in-canon collision, computed pre-budget; shadowing-names-exempt variant preserves availability).
- **P5** — upheld (gate sits upstream; file skills inherit it; explicit-load parity unchanged; `pf:` fail-closed per A15).
- **P6** — upheld and STRENGTHENED (A12 deletes the "AGENT dir name" namespace restriction — it contradicted P6; `.agents/shared/skills/` is a legitimate positive fixture).
- **P7** — upheld; the OQ-4 reconciliation is CONFIRMED sound (no evolution machinery imported by trigger-query generation — verified); A14 closes the two enforcement holes (evolution tools + REST fix endpoint).

---

## Part 7 — Fold Register — Final (reviewer micro-fold 2026-10-31)

**Pass provenance:** reviewer micro-fold, 2026-10-31 (APPROVE-WITH-ADVISORIES; 1 CRITICAL C-1 + 7 warnings + 6 suggestions). Fold applied surgically; no re-architecture; planning writes only; no commits. This register enumerates **every file in the 12-file planning set** with changed/unchanged status (new convention — the phase5 omission in the prior pass is exactly how C-1 survived).

| # | File | Status this pass | Notes |
|---|------|------------------|-------|
| 1 | `architecture-recommendation.md` | **UNCHANGED (reviewer's own artifact — read-only input)** | Architect verdict source; not edited by the planner |
| 2 | `decisions.md` | **CHANGED** | Added Part 5 subsections (planner rulings W-4/W-5/W-6), W-5 ruling record; Part 7 fold register added; S-a tracked in requirements.md |
| 3 | `phase0-plan.md` | **CHANGED** | Task 4 collision-census gate; task 2 S3 promotion condition (already in prior pass) |
| 4 | `phase1-plan.md` | **CHANGED** | W-4 provenance + conditional-escalation note (task 5); W-5 EXTRA_ROOTS resolver leg (task 5) |
| 5 | `phase2-plan.md` | **CHANGED** | W-2 risk-row reword (append → FULL-corpus shadow → budget → tokenize); W-6 Appendix A citation (task 2/9) |
| 6 | `phase3-plan.md` | **CHANGED** | S-d renumber 10/11/12; S-c citation re-anchor (skill_evolution_tools.py:212 → :211); W-4 provenance + conditional escalation (task 3); W-6 Appendix A citation (task 10) |
| 7 | `phase4-plan.md` | **CHANGED** | S-f site-3 reword; W-6 Appendix A inlined + 6-site citation update; W-5 knob docs (task 1); A11/OFF claim (already) |
| 8 | `phase5-plan.md` | **CHANGED** | C-1 migration census +2/+3 rewrite; W-1 dual A18.2 ceremony disclosure; new task 11 (W-5 negative pin); task 3 mutation legs extended (A5/A7/A14/A15/A16/A1); task 6 fence (A13 both sources) |
| 9 | `plan-overview.md` | **CHANGED** | S-e precedence sentence; SC 6/10 refinements; OQ-4/OQ-7 resolution (already); rollout dual disclosure (already) |
| 10 | `requirements.md` | **CHANGED** | S-a: A18.3 row added to §14 Amendment Register (shadow-decision log WARNING) |
| 11 | `research-findings.md` | **CHANGED** | S-b: supersedes count corrected to 4/5 (blast-radius-leg has no supersedes field) |
| 12 | `technical-analysis.md` | **UNCHANGED BY ME — folded by parallel worker (verify)** | Parallel worker owns this file; its 13:21Z mtime reflects the concurrent fold of TA-side amendments (A1/A3/A5/A6/A7/A8/A9/A11/A12/A13/A14/A15/A16/A17/A18 + S1/S2/S3/S4/S5/S6). Reviewer: confirm TA-side folds landed; if not, this is the open gap |

**Pass provenance:** reviewer micro-fold 2026-10-31 (APPROVE-WITH-ADVISORIES → this fold). Prior pass: architect fold 2026-10-31 (SOUND-WITH-AMENDMENTS, A1–A18 + S1–S6). Original plan set authored by planner[v2] via plan-creation worker 2026-10-31.

---

### ADOPTED PATTERN (approver iteration 002, 2026-10-31 — adopted pattern): inline supersession at the requirement row

- **Convention:** supersession must live AT THE REQUIREMENT ROW, not only in the §14 register — requirements.md is read as instructions, and register-only annotation leaves rows instructing the opposite of the adjudicated posture. Every superseded/adjusted requirement row in §3–§6 carries an INLINE annotation (marked e.g. "**SUPERSEDED in v1 — see §14 `<row>`**" with the correction stated inline) + a §14 register cross-reference. Original text stays visible for traceability.
- **Sweep result (iteration 002 fold):** §14 register rows cross-checked against §3–§6; inline markers added at: NFR-COMPAT-3 (B2), FR-P7-3 (B3), §4.1 score-normalization (B3), §12 Assumption 5 (B3), §4.5 shadow-INFO (B4), §6.3 restart promise (B5), §6.1 knob name (N1), NFR-COMPAT-2 (N2), FR-P3-5 (N2), §4.2 worktree handling (N2), §4.3 FR-LP-2 address (N2), §6.2 per-project override (N2), §6.1 P4-consequence resolved marker, §4.3 "re-index not automatic" compatibility annotation. Strengthening-only rows (FR-P2/P4/P6 groups, A5/A7/A8/A9/A12) need no supersession marker — the original text and the amendment point the same direction.
- **Provenance:** "approver iteration 002, 2026-10-31 — adopted pattern".

### Planner ruling B1(a)-construction (approver iteration 002, 2026-10-31): M10 file-only mode gets a None-safe CONSTRUCTION path

- **Decision:** when knob ON and `config.skill_evolution is None`, the manager CONSTRUCTS the skill services with None-shaped deps instead of leaving them None: `SkillSearchService(skill_repo=None, embedding_repo=None, embedding_service=None, llm_config={}, config=None, skill_bank_repo=None)` (`skill_repo=None` = the explicit FILE-ONLY corpus signal) and `SkillInjectionService(search_service=<that service>, config=None, ab_test_repo=None, skill_repo=None)`.
- **Feasibility (ctor citations, verified at fold against `b56444244`):** `SkillSearchService.__init__` (`skill_search_service.py:359`) stores deps as-is; `llm_config` is already defensively coerced (`dict(llm_config) if llm_config else {}`, `:391`); `skill_bank_repo=None` already disables the ownership filter (docstring `:379-386`); caps already read via `getattr(self._config, name, DEFAULT)` (`:453/:464`) so `_config=None` falls back to module defaults.
- **Required None-tolerance deltas (pinned):** (i) stage 2 SKIPPED when `_embedding_service is None`; (ii) stage 3 SKIPPED when `_config is None` → deterministic degraded selection + `degraded:"file_only"`; (iii) caps fall back to module constants; (iv) `inject_explicit_skill` STEP-0 works with `ab_test_repo=None` (A/B branch `skill_injection_service.py:691` None-guarded).
- **Code fact (approver, corrected at fold per N4):** services are constructed ONLY inside `if self.config.skill_evolution is not None:` (`manager.py:2223` — the approver-cited ~2106 was stale); else-branch sets `_skill_search_service=None` (`:2276` — not ~2159) and `_skill_injection_service=None` (`:2495` — not ~2378); "attribute exists" = exists AS None; the else-branch (or post-cluster) construction is the fix site.
- **Provenance:** "planner ruling B1(a)-construction, approver iteration 002, 2026-10-31". Cross-references: phase4 task 2 site (3) + task 5 + Appendix A M10; phase3 task 10 (guard code — ONE implementation described by both).

### Planner ruling N7 (approver iteration 002, 2026-10-31): the A7 "acceptable variant" is FORBIDDEN

- **Decision:** the A7 shadow-invariant ships ONLY in the preferred variant — **shadowing names are EXEMPT from the file-corpus budget** (the cap applies to non-shadowing file skills). The "acceptable variant" (full-corpus shadow ⇒ both hidden under overflow) is **EXPLICITLY FORBIDDEN**: it hides the file skill itself, breaking P4's availability intent (the twin is retired but the canonical replacement disappears exactly when the repo is largest). The budget-exempt variant preserves both P4 (twin always hidden) and availability (the canonical file skill always surfaceable).
- **Provenance:** "planner, 2026-10-31, approver N7". **Landing record (extended per iteration 003 item 2 — all struck residue sites enumerated):** (1) `phase2-plan.md` task 5 (A7 pin acceptance — "under EVERY variant (…acceptable variant)" → mandated budget-exempt variant only); (2) `phase2-plan.md` Exit/Acceptance Criteria ("A7 pre-budget shadow pin green (both variants)" → mandated variant only); (3) `phase5-plan.md` task 3 mutation-matrix leg (c) ("must still hide the twin in both variants" → mandated variant only; the forbidden variant is neither built nor tested). Also aligned: decisions.md (this row), phase2-plan.md task 2 (variant language replaced), plan-overview risk 10 (already prefers the exempt variant).


**Provenance:** approver iteration 001 fold, 2026-10-31 (APPROVE-WITH-ADVISORIES → REJECTED iteration 001 → this fold; 2 BLOCKING + 18 notes). Fold applied surgically; no re-architecture; planning writes only; no commits.

**Files changed this pass (12/12 enumerated):**

| # | File | Status this pass | Notes |
|---|------|------------------|-------|
| 1 | `architecture-recommendation.md` | **UNCHANGED (reviewer's own artifact — read-only input)** | Not edited by the planner |
| 2 | `decisions.md` | **CHANGED** | B1(a)-census ruling record + re-open trigger; B2 owned in phase3 (no decisions edit beyond Part 5); Part 7 pass-3 subsection added |
| 3 | `phase0-plan.md` | **CHANGED** | P1: Collision Census gate added to EXIT CRITERIA with superset condition |
| 4 | `phase1-plan.md` | **CHANGED** | B1: census METHOD verbatim + re-open trigger + blast-radius reconciliation + exit-line reword; W-5 resolver leg (already) |
| 5 | `phase2-plan.md` | **CHANGED** | B1 re-open trigger (already implicit); P2 description reorder; P3 stat-gate clarification; W-2 (already) |
| 6 | `phase3-plan.md` | **CHANGED** | B2 FR-P7-4 clause-2 line; P4 M10 acceptance moved to Phase-4 exit; P5 naming check (no "load_skill tool" reference found — already correct); B1 re-open trigger on STEP-0 |
| 7 | `phase4-plan.md` | **CHANGED** | P4 M10 acceptance added to exit criteria; S-f (already); W-6 Appendix A (already) |
| 8 | `phase5-plan.md` | **CHANGED** | P6 A8 seam-guard leg + (k) tied to real-PG; P9 end-to-end pin (task 12); C-1/W-1 (already) |
| 9 | `plan-overview.md` | **CHANGED** | S-e precedence sentence (already); P8 FR→task traceability table appended; S2 embeddings-target fix (already) |
| 10 | `requirements.md` | **CHANGED** | S1 §14 rows (i)-(vi) added (i, ii, iii, iv new; v, vi cross-referenced) |
| 11 | `research-findings.md` | **UNCHANGED BY ME this pass** (S-b already applied in the prior micro-fold; no new edits) — declared unchanged otherwise |
| 12 | `technical-analysis.md` | **UNCHANGED BY ME — parallel worker owns it (verify)** | Not touched |

**Items folded this pass:** B1 (CRITICAL), B2 (CRITICAL), S1, S2, S3, P1, P2, P3, P4, P5, P6, P7, P8, P9 (14 items). **Disclosed non-fold:** P5 found no "load_skill tool" phrasing in phase3 — the only `load_skill` references are the send_message parameter (correct), so no edit was needed.
**Polish pass (2026-10-31, approver S-d class):** phase5-plan.md task rows renumbered to sequential display order 1-12 (W-5 negative pin 11->10, P9 end-to-end pin 12->11, Evidence + closeout 10->12); risk-table stale reference "task 10 wording: STAGING-ONLY" corrected to task 9 (the task that actually carries the STAGING-ONLY wording). No cross-file references to phase5 task numbers exist, so no other files changed. Provenance "approver polish pass 2026-10-31".

---

### Pass 4 — FINAL approver iteration 002 fold (2026-10-31)

**Provenance:** approver iteration 002 fold, 2026-10-31 (REJECTED iteration 002 → this fold; 6 BLOCKING + 10 notes). LAST approver iteration — iteration 003 must be COMPLETE. Fold applied surgically; planning writes only; no commits.

**Files changed this pass (12/12 enumerated):**

| # | File | Status this pass | Notes |
|---|------|------------------|-------|
| 1 | `architecture-recommendation.md` | **UNCHANGED (reviewer's own artifact — read-only input)** | Not edited by the planner |
| 2 | `decisions.md` | **CHANGED** | ADOPTED PATTERN convention record (inline supersession at the requirement row + sweep result); B1(a)-construction None-safe-construction ruling with ctor citations + N4-corrected manager lines; N7 acceptable-variant-FORBIDDEN ruling; pass-4 register |
| 3 | `phase0-plan.md` | **UNCHANGED this pass** (P1 collision-census exit criteria landed in the prior pass; no iteration-002 item targets phase0) |
| 4 | `phase1-plan.md` | **UNCHANGED this pass** (no iteration-002 item targets phase1; W-5/W-4/B1-static-census already landed) |
| 5 | `phase2-plan.md` | **CHANGED** | P2 description reorder; P3 clarification; W-2; N7 acceptable-variant FORBIDDEN |
| 6 | `phase3-plan.md` | **CHANGED** | B1 cross-reference (ONE implementation); N8 file_source-None skip; FR-P7-4 clause-2 line (already) |
| 7 | `phase4-plan.md` | **CHANGED** | B1 None-safe construction spec at site (3) + task 5 pins + Appendix A M10; N4 citation corrections (:2489-2496/:2276/:2495) |
| 8 | `phase5-plan.md` | **CHANGED** | B6 P6 cross-agent visibility pin (task 5); N5 leg cross-refs (task 4); N9 tool-surface assertion (leg l); B1 +3-arm governance (already) |
| 9 | `plan-overview.md` | **CHANGED** | S2 dedicated-embeddings fix (already); B6 traceability re-point; N3 traceability tightening (FR-P1-4 infrastructure note; FR-P5-1..4 exact tasks) |
| 10 | `requirements.md` | **CHANGED** | B2/B3/B4/B5 inline SUPERSEDED markers; N1 knob name inline; N2 resolved markers (NFR-COMPAT-2, FR-P3-5, §4.2, §4.3, §6.2); N10 §14 dedupe; adopted-pattern sweep annotations (§6.1 resolved marker, §4.3 compatibility note) |
| 11 | `research-findings.md` | **UNCHANGED this pass** (S-b supersedes 4/5 correction landed in the micro-fold; no iteration-002 item targets it) |
| 12 | `technical-analysis.md` | **UNCHANGED BY ME — parallel worker owns it (verify)** | Not touched |

**Citations RE-GREPPED at fold (N4) — results:** VERIFIED CORRECT: `skill_search_service.py:709` (the `_filter_owner_private` corpus seam IS at :709); `skill_tools.py:108` (`_get_project_id` def); `api.py:4241-4297` (readiness composite refresh + grace wiring in range); `readiness.py:511-535` (the `detail.grace` advisory block — my citation stands; the approver's proposed `auth_dependency_status ~:250-272` does NOT exist in this tree — auth_dependency_status is at `:376` field / `:476` / `:506-509`; discrepancy disclosed, no correction made); `skill_search_service.py:359/:391` (ctor + llm_config coercion); `:453/:464` (getattr cap defaults); `skill_injection_service.py:691` (A/B branch). CORRECTED: `manager.py` injection else-branch `:2483-2493` → `:2489-2496` (None assignment at `:2495`; search None at `:2276`); the approver-cited `~2106` if-gate → actual `:2223`; `~2159` → `:2276`; `~2378` → `:2495`.
---

### Pass 5 — Leader-adjudicated FINAL fold (approver iteration 003 escalation, 2026-10-31)

**Provenance:** leader-adjudicated final fold, approver iteration 003 escalation, 2026-10-31 (iteration 003 REJECTED AT CAP → the LEADER adjudicates this fold directly; implementation dispatches on this report). 6 blocking edits + notes folded in ONE pass; every citation re-verified against the worktree at `b56444244` at fold time. **Inline-annotation convention MAINTAINED** (every superseded/adjusted requirement row carries its inline marker + §14 cross-ref; new this pass: FR-P3-2 row, §10 P7 acceptance cell, NFR-IDEM-3 row).

**Files changed this pass (12/12 enumerated):**

| # | File | Status this pass | Notes |
|---|------|------------------|-------|
| 1 | `architecture-recommendation.md` | **UNCHANGED (reviewer's own artifact — read-only input)** | Not edited by the planner |
| 2 | `decisions.md` | **CHANGED** | Ruling-ID collision disambiguated (B1(a)-census / B1(a)-construction — renamed here + every reference, 0 bare "B1(a)" remain); pass-5 register |
| 3 | `phase0-plan.md` | **CHANGED** | 3-vs-4 shadow-count census-dependent normalization on the Named Gate assertion |
| 4 | `phase1-plan.md` | **CHANGED** | Item 5: objective names the DEDICATED `project_skill_embeddings` table (A1 framing); B1(a)-census rename; instance_messaging :4570-4573 cites |
| 5 | `phase2-plan.md` | **CHANGED** | Item 2 forbidden-variant residue struck (task 5 + exit criteria); item-1 drift :1051→:1054; P2/P3 (already) |
| 6 | `phase3-plan.md` | **CHANGED** | Item-1 seam-correction spec (one specification with phase4); drift :468-470→:467-468; instance_messaging :4570-4573 |
| 7 | `phase4-plan.md` | **CHANGED** | Item-1 seam-correction spec (site 3) with verified anchors (`_fetch` :641, except :661, gates :700-701/:711-712, seam :709); debris strip (APPENDIX/EOF/echo lines); manager anchor :2490-2496 (None at :2495) |
| 8 | `phase5-plan.md` | **CHANGED** | Item 2 forbidden-variant residue struck (leg (c)) |
| 9 | `plan-overview.md` | **CHANGED** | Debris strip (TRACE/echo); NFR-PERF-5 pointer → task 8; Assumption 3 census-dependent normalization |
| 10 | `requirements.md` | **CHANGED** | Item 3 §10 P7 acceptance cell reword + §14 covered-surfaces extension; item 6 FR-P3-2 inline annotation; NFR-IDEM-3 inline marker; Assumption 4 census-dependent normalization |
| 11 | `research-findings.md` | **CHANGED** | Pre-A1 rows annotated HISTORICAL/PRE-A1 (no-schema-change claim; +1 delta → +2/+3); supersedes row census-dependent note |
| 12 | `technical-analysis.md` | **UNCHANGED BY ME — parallel worker owns it (verify; item 4 of the leader's list is theirs)** | Not touched |

**Citations verified/corrected at fold:** `_fetch` at `skill_search_service.py:641`; `except Exception` + "BM25 failed to fetch skills" at ~`:661-665`; first emptiness gate `:700-701`; ownership filter/seam `:709`; second gate `:711-712`; `skill_injection_service.py` early-exit `:467-468` (was :468-470); `routers/skills.py` `body.project_id` at `:1054` (was :1051); `manager.py` else-branch `:2490-2496` (None at :2495); `instance_messaging.py` `<meta>` metadata read `:4570-4573` (was :4574 — leader's boundary confirmed precise); requirements' auto-injection cite `:4310-4316` VERIFIED correct (no change).

---

## Part 8 — Phase-1 execution records (coder lane, 2026-10-31)

### D-Q5 fork decision — RECORDED (D2 Named Gate, Phase 1)

- **Decision:** FORK = **COMPANION TABLE `project_skill_usage_records`** (+1 migration ⇒ lane delta **+3**). Model/repository at `daemon/repositories/project_skill_file/`, migration `20261031_120002_create_project_skill_usage_records.sql`. Phase 3's `skill_feedback` on `pf:` addresses records metrics into the companion table (metrics-only, no lineage, no evolution dispatch — P7 intact).
- **Census provenance:** the pinned static census artifact `.agents/shared/planning/project-skill-loader/d2-write-census.md` (method per B1(a)-census: base code + the specified Phase-3 STEP-0 contract). A usage write EXISTS on the file path — W1 (the pre-existing `last_injected_skill_ids` injection-tracking merge at `instance_messaging.py:_dedup_merge_skill_ids`/`:173-204`, driven from `:4431-4441`, carries pf-ids on every STEP-0 hit) and W2's `SkillUsageRecord` insert path is reachable-and-driven but guarded (`_record_one`'s skill-row miss returns 0 before any insert — the FK row would be rejected anyway). The rule "ZERO writes into FK-constrained child tables" HOLDS. The "defer pf: feedback" branch's precondition (census proves NO usage write) is NOT met ⇒ the DEFAULT applied.
- **Re-open trigger carried:** a Phase-3 STEP-0 deviation that changes the write set re-opens this fork before staging.

### Phase-0 census carry (gate input for the stage record)

- In-fence collision census (the instructed DB `ensemble_dev` + all reachable local dev DBs): **zero** canon-name collisions, **zero** known-id hits, **zero** in-scope (83da04de…) rows — the superset falsifier PASSES on all accessible data. The exact-set assertion (`== {9e79ccad, fd1e01dc, 76e3e1a7a}`) requires the LIVE deployment DB (the 3 legacy rows were created through the live daemon), which is outside this lane's fence — routed as a stage-time gate input (SELECT-only live-DB re-check) in `phase0-findings.md`. No halt condition triggered (superset absent); Phase 1 proceeded.
- S3 promotion condition: cold scan measured 1.323 ms median (≤50 ms) ⇒ **S3 stays SHOULD** (dedicated bounded executor remains advisory for phase2 task 2).

### Phase-1 exit record — replacement coder audit (2026-10-31)

The predecessor lane wedged mid-Phase-1; a replacement coder audited the uncommitted work against phase1-plan tasks 1–11 (verified presence AND correctness, not just presence). Predecessor code was sound on the whole; the audit produced the following **corrections** (all committed with the phase):

1. **Debug residue removed** — `print("DBG-GATE", …)` in the stat-gate (`service.py`) removed.
2. **Stat-gate transition sync fixed** — a valid→invalid transition, a newly-appearing invalid dir, and a vanished invalid dir now trigger the DB row sync (`invalid_dirty`); previously those transitions left stale `valid=True` rows / lingering indexed-as-listed rows in `project_skill_files`. Now pinned by `TestInvalidTransitionsAtStatGate` (3 tests, mutation-verified).
3. **A4(vii) digest-stability semantics made deterministic** — the refresh loop's `backoff <= 0` branch previously fell through to GENERATION after an exhausted stability window (only the loop-top exit skipped); the loop now tracks `stable` explicitly and always skips on exhaustion. Pinned by `TestA4ViiDigestStability` (mutation-verified class of the M4/M5 spot checks).
4. **Q7 warn-once-per-root state made live** — `_RootIndex.invalid_schema_warned` was write-only; the stat gate now consults it (repeat unknown-schema skips log at DEBUG), so the warn-once state lifetime spans full scans and stat-gate passes. Pinned by `TestWarnOnceLifetime` (mutation-verified).
5. **Unused imports** (`MAX_NAMESPACES`, `MAX_SKILL_DIRS_PER_NAMESPACE`) dropped from `service.py`.

Test evidence at phase exit (worktree `.venv`, Python 3.14.7):
- **123 unit tests green** across `test_project_skill_source_{model,manifest,resolver,index,embedding,migrations}.py` (the migrations pack is new: fresh-SQLite apply of the three lane `.sql` files, idempotent re-run, zero FK clauses, duplicate-prefix guard over the real versions dir).
- **12 real-PG tests green** on a FRESH scratch DB (`PG_TEST_DB=ensemble_test_only`, per-variable env scrub; ambient `POSTGRES_DB=ensemble_prod` refused by an explicit pre-command fence): boot-artifact column match, full index→invalidate→reindex cycle, state transitions, **A1 FK-wall pin RED-documented + dedicated-table GREEN**, **P1 sentinel on real PG** (12-char windows, `information_schema` table introspection), A3 two-roots pin, CRUD round-trip (file + usage companion repos).
- **Mutation spot-checks (apply → RED → revert):** A6 reflex-1 refusal (2 tests red), A5 containment (3 red), stat-gate invalid-sync trigger (3 red), warn-once consult (1 red), **A5(e) read-time containment re-check (1 red — this spot-check exposed the missing pin; `TestReadTimeContainmentSwap` added)**.

Phase-exit invariants asserted: `skill_search_service.py` / `skill_injection_service.py` / `manager.py` / `config.py` / `readiness.py` untouched (no diffs); rollback note N6 stands (git-revert-only until Phase 4's knob). The plan's Tier-1 blast-radius gate is NOT run here (tester lane runs it from this record).

---

## Part 9 — Phase-4 code-review fix cycle (W1–W7) — 2026-11-01

Council verdict folded here: **APPROVE-WITH-ADVISORIES, 0 critical / 7 warnings / 8 advisories**.
This part records the SEVEN WARNING dispositions only (advisories → tidier pass).

| # | Disposition | Where |
|---|---|---|
| W1 | **FIXED** — `corpus_for` re-asserts `index.project_id == project_id` after the post-lock `_roots.get()` (TOCTOU: a concurrent index of a different project on the same realpath could hand project X project Y's corpus). Sibling hardening at `lookup_explicit`'s post-gate re-fetch (same window, same file). Longer-term form recorded in-code: key `_roots` by `(root_real, project_id)`. | `daemon/services/project_skill_source/service.py` |
| W2 | **FIXED** — `file_embeddings_for` (sync DB read on the dedicated table) bridged with `await asyncio.to_thread(...)` inside async `_embedding_rerank` — the v0.27.14 on-loop-DB defect class. Loop-thread instrumentation pin added. | `daemon/services/skill_search_service.py` |
| W3 | **FIXED — option (a): WIRED.** `gc_sweep()` now has a production caller: `MaintenanceService` job `project_skill_gc`, every `PROJECT_SKILL_GC_INTERVAL_HOURS` (24h; idle-gated; advisory try/except). Registered OUTSIDE the `skill_evolution` gate (file source is ON-class, A18) and guarded by `_project_skill_source is not None` (knob OFF ⇒ not registered — nothing was ever written). Rationale for (a) over (b): the defect is real data (orphan `project_skill_files`/embeddings rows accumulate after project deletion), the wiring follows an existing in-house precedent (`skill_metric_scan`, `skill_orphan_sweep`), and it is ~25 lines of advisory code — documenting a manual-only invocation would have left A17's convergence claim false while adding an operator burden with no invocation surface. | `daemon/manager.py` + runbook |
| W4 | **FIXED — option (a): SUFFIX GATED.** The `— source: db` tag on `skill_list` DB rows is emitted ONLY when a file source is wired. Under OFF the line is byte-identical to the pre-lane output ⇒ A11's "byte-identical search/list/view RESULTS" claim is literally true again (previously only the SEARCH half was pinned). Chosen over (b) because the tag's purpose is cross-SOURCE disambiguation, which cannot exist when there is one source; weakening a documented invariant to accommodate a cosmetic tag is the worse trade. M0 cell extended to pin `skill_list` byte-identity under OFF. | `daemon/tools/skill_tools.py` + tests |
| W5 | **FIXED** — the bare `pf-<hex16>` feedback branch now requires (a) a wired file source (knob ON) and (b) the id to be a real `project_skill_files` row bound to the resolved project. Previously ANY `pf-…` string minted a usage row, including under the knob OFF (contradicting "nothing is written under OFF"). OFF-cell pin extended to exercise `skill_feedback(pf-…)`. | `daemon/tools/skill_tools.py` + tests |
| W6 | **FIXED** — `_read_body_fresh` re-validates the BODY FILE (not just the skill dir) via `_validate_file_inside_skill_dir` and opens it `O_NOFOLLOW`, inside the same thread pass ⇒ a post-index body-file symlink can no longer be served in the stat↔open window. Body-only-symlink leg added to `TestReadTimeContainmentSwap`. | `daemon/services/project_skill_source/service.py` + tests |
| W7 | **FIXED — DOC-ONLY.** M10 (evolution-config-ABSENT file-only mode) re-framed as a **test/defensive seam**: `Config.skill_evolution` is non-Optional (`Field(default_factory=...)`), so the else-branch is unreachable through `load_config`. The runbook's A18.2 note now also distinguishes **"evolution lane disabled" ≠ "evolution config absent"**. No behavior change — re-purposing the gate on `evolution_lane_enabled` would be a separate commissioning. | `docs/runbooks/project-skill-source.md` (this Part) |

**A17 text correction (W3):** earlier text implied GC already ran. It now runs — on the
maintenance tick — and the runbook says so with the interval and the advisory posture.
**A11 text correction (W4):** the byte-identity claim is now true as written; the additive
tag is ON-only and documented in the knob table.
**A18.2 text correction (W7):** the derivation stands; the M10 shape is labelled
test/defensive, and "lane disabled" is explicitly NOT the absent-config case.
