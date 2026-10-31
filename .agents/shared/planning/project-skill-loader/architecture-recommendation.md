# Architecture Recommendation — Project Skill Source Loader

Date: 2026-10-31
Author: architect (controller) — synthesized from 6 dispatched design-analyst workers (fan-out: data-flow-design / trade-off-analysis / structural-design / security-design / resilience-design / system-decomposition), each evidence-verified read-only in this worktree at base `b56444244`.
Status: **ARCHITECTURE SOUND-WITH-AMENDMENTS** — the plan's architecture (fused source, resolver, pf: identity, shadow precedence, lazy index) is CONFIRMED. Five amendments are CRITICAL (P1/P3/P4/P7-adjacent or silent-degradation defects in the plan as written); all are surgical.
Inputs: the 11-file plan set in this directory; code verification per the worker reports (anchors cited per amendment; the two headline claims re-verified directly by the architect: `skill_embeddings.skill_id` and `skill_usage_records.skill_id` both carry `ForeignKey("skills.id", ondelete="CASCADE")`, `daemon/repositories/skill/models.py:594-601` / `:378-392`).

---

## 1. Verdicts per focus area

| # | Focus | Verdict | One-line reason |
|---|---|---|---|
| F1 | OQ-4 embeddings substrate | **SPLIT: P7/P1 reconciliation CONFIRMED; storage vehicle REJECTED** | Trigger-query generation imports zero evolution machinery (verified pure LLM call, `skill_embedding_service.py:285/:542` — no `skill_triggers`/lineage writes), but `skill_embeddings` reuse is structurally broken on PG: the FK at `models.py:594-601` rejects every `pf-` insert ⇒ silent permanent BM25-only. |
| F2 | Q8 knob posture | **CONFIRM default ON** (ratified, corrected grounds) | Dominant axis = G2 lesson; the kill-switch family is actually MIXED (evolution lane is default-OFF, `config.py:4486-4487`), and the true house rule — ON for zero-user-action read/serve surfaces, OFF for agent-authored mutation lanes — puts the file source in the ON class. |
| F3 | Tier-2 wiring boundary | **AMEND** | One cluster is false as written: 5 files / 6 sites, `daemon/api.py:4241-4297` missing from the blast radius; no lifespan hazard (readiness is pull-per-tick, first tick after manager is built). |
| F4 | Dual scope sources + path trust | **AMEND (2 CRITICAL)** | Rung-1 "explicit wins" is a cross-project body-read primitive via REST (`routers/skills.py:1051` passes caller `project_id` straight in); path containment is specified NOWHERE and D-Q7's serve-fresh rule skips revalidation at read time. |
| F5 | Degraded modes + caps | **AMEND (third surface confirmed)** | (ON, absent) file-only mode has no pinned ranking/shape/caps; the cap×shadow order lets budget overflow surface the stale DB twin P4 exists to retire; the fusion seam call is unguarded. |
| F6 | Index lifecycle | **AMEND (keying defect confirmed live)** | Index is root-keyed but the unique key and pseudo-id omit `root_path` — two-roots-one-project collides (live today via `EXTRA_ROOTS`); races/churn need wholesale-swap + single-flight invariants. |
| F7 | Missed boundaries | **AMEND** | Metadata-only cache invariant missing; multi-root orphan purge unstated; scanner glob underspecified + an internal contradiction ("namespace must be an AGENT dir" violates P6); the real REST strip mechanism is `_LIST_SKILL_FIELDS` (`skill_store_service.py:90`), not pydantic; `pf:` ids are admitted by `skill_evolution_tools.py` and the REST fix endpoint (P7 hole). |

### The five CRITICAL amendments (must land before Phase 1 implementation freeze)

1. **A1** — dedicated embedding table (FK wall).
2. **A5** — path containment at index AND every read (P3/P1).
3. **A6** — resolver rung-1 confirm-only + REST file-invisibility in v1 (P3).
4. **A7** — shadow set computed pre-budget (P4).
5. **A8** — exception guards at the fusion seam and STEP-0 (the plan's own "never fails a search" principle is currently unenforced).

---

## 2. Amendments

Each amendment is **MUST (binding)** or **SHOULD (advisory)**, mapped to the plan artifact it amends. All are additive to the plan's architecture; none reopens a settled decision except where explicitly stated (A1 supersedes D-Q3's storage vehicle).

### A1 — (MUST, 🔴) Embedding substrate: dedicated `project_skill_embeddings` table, no FK
- **Amends:** decisions.md D-Q3; technical-analysis.md §Q3 + §B (table set); phase1-plan task 8 + migration task 4; plan-overview migration-delta note.
- **Finding:** `skill_embeddings.skill_id` = `ForeignKey("skills.id", ondelete="CASCADE")` (`models.py:594-601`; architect re-verified). On PG, `SQLModel.metadata.create_all` (`manager.py:906-909`, strictly before `_ensure_postgres_columns` `:931`) materializes the constraint, so **every `pf-` insert raises ForeignKeyViolation**; the write is per-row caught (`skill_embedding_service.py:622-627`) ⇒ `written=0` ⇒ file skills are silently, permanently BM25-only on PG (fresh installs and the remote replicas) plus retry/log churn. The plan's "no schema delta for the embedding path" claim is false on the canonical PG path.
- **Required shape:** new table `project_skill_embeddings` (own `.sql` per house rule + `_ensure_postgres_columns`-side model registration pre-`create_all`), columns mirroring `:601-607` (`skill_id`, `trigger_query`, `embedding`), **no FK**, `skill_id` = the pseudo-id. Migration delta for the lane becomes **+2** (see §3).
- **Rejected alternative (record it):** dropping the FK from `skill_embeddings` — schema mutation of an evolution-lane table, breaks rollback posture, and diverges fresh vs long-lived DBs.

### A2 — (MUST, 🔴) Zero FK-bound writes anywhere on the file path (usage records)
- **Amends:** decisions.md D-Q5 (skill_feedback metrics-only); phase1-plan task 10; phase3-plan feedback tasks.
- **Finding:** `skill_usage_records.skill_id` carries the same FK (`models.py:378-392`; architect re-verified). If injection or feedback writes a usage row under a `pf-` id, the same wall applies — and if the write is uncaught it breaks injection of *file* skills outright.
- **Required:** Phase 1 adds a **write census** (every DB write reachable from index/search/load/feedback on the file path, enumerated as a pinned artifact). Rule: the file path performs ZERO writes into FK-constrained child tables. Implementation: either a small companion `project_skill_usage_records` (keeps D-Q5's metrics value; +1 migration ⇒ lane delta +3) or — if the census proves injection writes no usage row — defer `pf:` feedback to an **explicit "not supported in v1" envelope** (never a silent `recorded:false`). Choose in Phase 1 from census evidence, not preference.

### A3 — (MUST, 🔴) Identity & keying: `root_path` enters the pseudo-id hash and the unique key
- **Amends:** decisions.md D-Q1/D-Q3; technical-analysis.md §B (sketch) + §Q3; phase1-plan tasks 3/8.
- **Finding:** the index is keyed by resolved absolute root (§Q1 explicitly blesses two-roots-one-project), but `uq_psf_project_ns_name = (project_id, namespace, name)` and the pseudo-id omit `root_path` ⇒ both roots write ONE row (last-writer-wins flip-flop) and **share embedding rows** — stage-2 vectors computed from root A's body while the caller is served root B's. Live today via `ENSEMBLE_PROJECT_SKILL_SOURCE_EXTRA_ROOTS`; widens with `work_dir` adoption.
- **Required:** unique key and pseudo-id = `(root_path, project_id, namespace, name)`. Human address `pf:<ns>:<name>` stays; disambiguated at resolve time by the caller's root. Note the semantics change: the pseudo-id is **per-host-per-root** (an index/cache identity, not a global one) — embeddings are a per-host cache, which is consistent with their derived/non-authoritative nature (P1).

### A4 — (MUST, 🔴) Embedding lifecycle: gc, delete-with-row, delete-after-success, single-flight
- **Amends:** phase1-plan tasks 7/8; technical-analysis.md §B "Invalidation"; runbook (phase4 task 9).
- **Required (four rules):**
  1. Deleting an index row deletes its embedding rows (same transaction, or a compensating sweep) — vanishing skills/namespaces must not orphan vectors.
  2. A periodic/one-shot gc removes `project_skill_embeddings` rows whose `skill_id` is not in `project_skill_files` (covers project deletion, root changes, project_id changes — the opaque hash makes per-project LIKE cleanup impossible; W1 enumerated no gc exists anywhere in the plan).
  3. **Delete-after-success**: regenerate new rows, then drop old — the plan's delete-then-regenerate fire-and-forget converts "stale" into "permanently absent" because the digest-stable access path never retries (W5 M5).
  4. **Single-flight + debounce**: at most one in-flight refresh per pseudo-id; refresh only after the digest is stable (bounded backoff) — branch toggling must not thrash main-model LLM calls (see A16c: generation rides the MAIN model, not the cheap tier).
- **Invariants carried from the worker (pin as acceptance criteria, not prose):** corpus swap is wholesale under the per-root lock (readers see pre- or post-state, never torn); index mutation never blocks or fails a search; vector for a skill is generated from the same root's body that will be served.

### A5 — (MUST, 🔴) Path containment, enforced at index AND at EVERY read
- **Amends:** phase1-plan tasks 2/6/9; phase3-plan; phase5 mutation matrix; technical-analysis.md §Q7.
- **Finding:** grep across the plan set shows **zero containment specification**. `body_path` relative-to-root is a comment, not a rule; a worktree `.agents/` tree is branch content (anyone with branch-write access can craft manifests); D-Q7's "digest mismatch ⇒ serve fresh file content anyway" **deliberately skips revalidation at read time** — a mid-session symlink swap pivots the served body with no gate, under daemon privileges.
- **Required:** (a) namespace/name components validated `^[A-Za-z0-9][A-Za-z0-9._-]*$`, no `.`, `..`, separators, absolutes; (b) manifest `body` (and each `assets[]` path) rejected pre-realpath if absolute or containing `..`; (c) `realpath()` containment inside `realpath(root)/.agents/<ns>/<name>/` — skill-dir confinement, not merely root; (d) symlinked `skill.json`/body/namespace-dir resolving outside root refused (`path_escape`/`symlink_escape`/`bad_component` fail-loud reasons); (e) containment re-checked on **every** load-time read (serve-fresh applies to *content freshness* only). Mutation legs: absolute body, `../` body, symlinked ns dir — planted-defect RED→GREEN.

### A6 — (MUST, 🔴) Resolver rung 1 is CONFIRM-ONLY; REST `/api/skills/search` serves NO file skills in v1
- **Amends:** technical-analysis.md §Q1 (rung-1 wording); decisions.md D-Q1; phase1-plan task 5; phase2-plan tasks 7/10.
- **Finding:** `SkillSearchRequest` carries a caller-supplied `project_id` passed straight into search (`routers/skills.py:1032-1056`, consumed `:1051`); the router has no auth dependency of its own (default-deny middleware applies only when auth is enabled), and even auth-on deployments let any authenticated principal pass any project. Search output carries bodies ⇒ rung-1 override = **cross-project body read = direct P3 violation**. TA §Q1's own "full caller control" annotation contradicts D-P3.
- **Required rule:** the explicit param may only **confirm**, never widen — `explicit ∈ {None, instance-derived}` ⇒ use instance-derived; explicit present and conflicting ⇒ **refuse the file corpus for that call** (WARNING with both values; DB results unaffected). Consequently: REST `/api/skills/search` (no instance context) contributes **zero file skills** in v1 — phase2 task 10's REST-parity pin becomes a REST-**invisibility** pin. A server-side REST binding (auth principal → project) is the v2 path if REST fusion is ever wanted (Decisions Pending #1).
- **Pins:** explicit-conflict ⇒ refusal (asserted behavior, not invisibility-by-accident); REST arbitrary project_id ⇒ zero file entries.

### A7 — (MUST, 🔴) Shadow set computed from the FULL file corpus BEFORE budget truncation
- **Amends:** decisions.md D-Q4; technical-analysis.md §C (assembly steps); phase2-plan tasks 2/5.
- **Finding:** order as written is append → budget(50) → shadow pass. A file skill dropped by overflow leaves its DB twin **unshadowed and surfacing** — deterministic ordering, nondeterministic P4 semantics: whether a name is retired silently flips as file count crosses 50, and the surfaced twin is precisely the stale duplicate P4 exists to kill. Invisibly.
- **Required invariant:** shadow set ⊇ every in-scope DB name matching ANY in-canon file skill, computed pre-budget. Preferred variant: shadowing names are **exempt from the budget** (cap applies to non-shadowing file skills — availability preserved, P4 holds). Acceptable variant: full-corpus shadow ⇒ both hidden under overflow, surfaced via a dedicated overflow counter. Pin: >50 file skills incl. one DB twin ⇒ twin absent from injected AND low_match, every variant.

### A8 — (MUST, 🔴) Exception guards at the fusion seam and at STEP-0
- **Amends:** phase2-plan task 2; phase3-plan task 1; decisions.md D-Q5/D-Q7.
- **Finding:** phase2 task 2 specifies the bare call `await file_source.corpus_for(...)` — no try/except. D-Q7's "the scan never raises into the caller" is a service-internal aspiration; any escape propagates out of `_bm25_prefilter` ⇒ `search` raises ⇒ the tool returns the user-visible `"ERROR: skill_search failed"` string (`skill_tools.py:171-172`) — a direct violation of the plan's own binding principle. Same for STEP-0: an exception in `lookup_explicit` must be treated as a miss (fall through to the DB clone path), else a broken file source breaks explicit injection of **DB** skills.
- **Required:** local guards at both seams (except ⇒ WARNING + `[]`/miss + `last_error`); pins for modes M3/M7/M8 (root unreadable mid-search, file-only mode broken, corpus raises) asserting search returns DB results, never the ERROR string, never 500.

### A9 — (MUST, 🔴) (ON, DB-absent) file-only mode gets PINNED semantics — it is a third behavior surface
- **Amends:** decisions.md D-Q8b; phase3-plan task 10; phase4-plan task 5.
- **Finding:** `SkillSearchService` and `SkillInjectionService` are constructed only inside the evolution gate (`manager.py:2223-2276`, `:2426-2432`; else-branch `:2483-2493` sets attrs to None). The (ON, absent) mode therefore answers at the TOOL layer — a third code path with **unspecified ranking, shape, and caps**. OQ-1's recommendation ("serve file-only") stands (Skill-Bank precedent `manager.py:3951-3956` + G2 lesson), but the plan defers its semantics entirely.
- **Required:** file-only search = stage-1 BM25 over the file corpus ONLY (same tokenizer), `top_k = max_results`, **no stage 2, no stage-3 LLM** (deterministic, zero LLM cost, works with no evolution config), results in the standard `{"injected", "low_match"}` shape with `source: "project-file"` and a `degraded: "file_only"` marker; `skill_view`/`skill_list` serve files; `skill_create`/`skill_fix` soft-fail as today. Pin all four matrix cells against the M0-M11 mode table (resilience worker) — not just the 2×2.

### A10 — (MUST, 🟡) Wiring boundary: 6 sites, and `daemon/api.py` joins the blast radius
- **Amends:** phase4-plan tasks 2/3/4 + TOUCHED list; plan-overview phase table row 4.
- **Required:** enumerate ALL sites in the task rows: `manager.py:2261` (search ctor param — the only daemon construction site; 8 test constructions all default-safe), `manager.py:2426` (injection service), `manager.py:2483-2493` (**else-branch must define the service attribute** for knob-ON/evolution-absent — dropped by the "one cluster" framing), unconditional construction placement (Agent-Snapshot precedent `manager.py:2499-2553`; choose constructor-param vs cluster-setter flavor and name it), the **pre-`create_all` model registration** (the operative fresh-table artifact; the `:797-829` citation actually documents that brand-new tables need no mirror — keep the mirror as belt per the two-artifact rule, but fix the citation), and **`daemon/api.py:4241-4297`** (thread a `skill_sources=` snapshot param for the readiness advisory — without it `detail.skill_sources` cannot exist; readiness is pull-per-tick, first tick ≥ `api.py:2663`, so no ordering hazard, no setter inversion needed). Add per-branch attribute assertions to the existing manager init pins (else Phase-5 net-new=0 reddens). Record `seed_all`/file-source order-independence.

### A11 — (MUST, 🟡) Readiness OFF-shape resolved: ALWAYS publish the block with an `enabled` flag
- **Amends:** decisions.md D-Q8 observability; technical-analysis.md §E; phase4-plan task 4.
- **Ruling (resolves the plan's internal contradiction, overriding the worker's absent-when-OFF suggestion):** the service is constructed unconditionally (Q8b), and the advisory publishes **always** — `enabled: true|false` — because observability of a disabled feature is exactly what an operator needs at 2am, and `detail` is additive (never touches status/components/reasons; older daemons read as "absent" and consumers fail-closed). Consequence: **narrow the plan's OFF claim** from "byte-identical" to "byte-identical search/list/view RESULTS" — `/readyz` detail legitimately differs under OFF. Extend the OFF-side pin to assert zero `pf-` rows in any skill table (no index ⇒ no refresh ⇒ no writes).

### A12 — (MUST, 🟡) Scanner glob is depth-exact and purely structural; "AGENT dir name" leg deleted
- **Amends:** technical-analysis.md §Q7 defense leg 1; phase1-plan tasks 2/6.
- **Finding:** "namespace must be an AGENT dir name" contradicts P6 (namespaces organize, never ACL) and would reject a legitimate `.agents/shared/skills/` or `.agents/devops/skills/`. Tree inventory (verified): exactly 6 `skill.json` (5 canon + legacy `bump-version`), 3 `skills/` dirs (only `.agents/tester/skills` is in scope), zero symlinks, no nested manifests today.
- **Required:** glob `<root>/.agents/<ns>/skills/<name>/skill.json`, depth-exact, `skills` matched case-sensitively as a directory, one-level walk, no recursion; `<ns>`/`<name>` any legal dirname (validated per A5). Fixtures beyond FR-VAL-1: nested `<name>/sub/skill.json` (reject), `.agents/shared/skills/<name>/` (**ingest** — the positive P6 fixture), `skills/` as a file (no crash), symlinked skill dir (refused), `agents/*/skills-template/` (not ingested), `schema_version` absent/2 (skip + warn-once; pin the warn-once state lifetime), name≠dir (visible `invalid` entry).

### A13 — (MUST, 🟡) The resolver is the ONLY sanctioned scope reader — both sources, explicit allowlist
- **Amends:** phase1-plan task 5 (fence); phase3-plan task 3; phase5.
- **Finding:** the plan's grep fence covers only `metadata.get("project_id")`; the second scope source — the `instances.project_id` row column — is read directly at `skill_tools.py:107-119`, and `instance_messaging.py:4574` reads metadata directly (pre-existing `<meta>` path). Both survive the fence as worded.
- **Required:** fence BOTH sources with an explicit allowlist (`graph.py:886-931` legacy, `instance_messaging.py:4574`, `skill_tools.py:107-119`) or migrate those three call sites onto the resolver. `skill_view`'s `pf:` resolution = resolver(`current_instance_id`); no caller-supplied project hint (positive finding: the `<meta>` load path is already receiver-scoped — a parent cannot make a child resolve in the parent's scope).

### A14 — (MUST, 🟡) `pf:` guards on the remaining id-accepting surfaces (P7 hole)
- **Amends:** phase3-plan blast radius; success criterion 6.
- **Finding:** three MORE id acceptors exist beyond the plan's three: `skill_analyze` / `skill_evolve` / `skill_get_metrics` (`skill_evolution_tools.py:162/:212/:325`, skill-keeper-gated, lane currently OFF) — on lane re-enable they run evolution machinery against file skills (exactly what P7 forbids). REST `POST /{skill_id}/fix` (`routers/skills.py:1330-1372`) dispatches with **no existence check** — a `pf:` id is admitted as a fix job whenever the lane is enabled; the P7 pin as written is only true at the tool layer.
- **Required:** one-line `pf:`-prefix refusals on the three evolution wrappers and the REST fix endpoint (mirroring the tool refusal); scope the P7 pin to "tools + REST fix" explicitly. All other REST id endpoints 404 cleanly on `pf:` (TEXT ids, no uuid validation — safe).

### A15 — (MUST, 🟡) `pf:` load with no project context FAILS CLOSED, before the silent early-exit
- **Amends:** phase3-plan task 1; contract §4.2.
- **Finding:** `inject_explicit_skill` opens with a **silent** no-op (`if not project_id or not skill_name: return` — `skill_injection_service.py:468-470`), and "STEP 0 at the top" is ambiguous relative to it. Contract §4.2 requires an explicit error envelope for a `pf:` load with no resolvable project. Also state the full precedence chain in one sentence: file (STEP 0) → `clone_on_miss_sync` (project row → bank template) → repo fallback — the same-name non-mutation pin depends on it.
- **Required:** `pf:`-shaped inputs bypass/precede the early-exit and fail with an explicit envelope; bare names keep today's silent-miss semantics.

### A16 — (MUST, 🟡) In-process cache holds METADATA ONLY — never body bytes
- **Amends:** decisions.md D-Q2; phase1-plan tasks 1/6.
- **Finding:** if `ensure_index` caches materialized `ProjectFileSkill` objects (which carry `.content`), worst case ≈ 64 ns × 128 dirs × 64 KB ≈ **512 MB/root**; metadata-only bounds it at KB-MB scale — and cached bodies would also violate P1's canon semantics (body read from disk at call time), not merely memory.
- **Required:** pinned invariant — the cache stores manifest/digest/path metadata; `content` exists only on transient objects built in `corpus_for`/load. Cheap unit assertion: no cached object exposes non-empty `content`.

### A17 — (MUST, 🟡) Multi-root posture recorded: accept-unbounded, WITH instruments and an orphan purge
- **Amends:** plan-overview (scope/risks); phase1/phase4 runbook.
- **Finding:** no per-process root cap, no eviction, no purge — path-vanished roots hold in-memory entries + rows forever; deleted projects orphan `project_skill_files` rows and (per A1) embedding rows permanently. No "all roots" scan exists (scope-per-caller confirmed — P3 not implicated).
- **Required:** record the acceptance explicitly with: the A16 metadata-only invariant, `detail.skill_sources.roots[]` census (already planned), a one-statement orphan purge (rows whose `project_id` ∉ `projects`; plus the A4 embedding gc), and optionally a `MAX_ROOTS` (32) INFO-refuse cap as insurance. Growth is proportional to real usage; unbounded-with-instruments is honest — silence is not.

### A18 — (MUST, 🟡) Knob posture: default ON ratified, on corrected grounds, with dual disclosure
- **Amends:** decisions.md D-Q8; technical-analysis.md §Q8; phase4-plan tasks 1/5/8/9; plan-overview Rollout Posture 5.
- **Required:**
  1. **Correct the precedent citation** (TA §Q8's "same family … default ON" is factually wrong): the family is MIXED — evolution lane **OFF** (`config.py:4486-4487`, incident-driven), notify-user ON (`:3078-3085`), hard-reboot pass ON (`message_queue_boot_recovery.py:55-60`). The two-class rule: ON for zero-user-action read/serve surfaces; OFF for agent-authored mutation lanes. File source = ON class.
  2. **Dual ceremony disclosure:** (a) P4 shadowing of `9e79ccad`/`fd1e01dc`/`76e3e1a7a` in this project; (b) **this install runs evolution-OFF ⇒ first boot activates file-only search/list/view** where tools today say "not yet available" (the OQ-1 coupling). Disclose BOTH, plus the REST/FE consequence: the FE Skills page keeps showing the 3 legacy DB skills as active with no shadow indication (shadow-only retirement is invisible to REST list — A20).
  3. **Shadow-decision log level: WARNING, not INFO** — shadowing is the only non-additive effect in the surface; it must be greppable by an operator who doesn't know to look.
  4. Phase-0 census asserts the shadowed set is EXACTLY the 3 known ids; "superset found ⇒ re-adjudicate before stage" becomes a stage-time gate input. **This census is the falsifier**: unexpected collisions ⇒ the fusion/shadow split re-enters (fusion ON, shadowing gated) — Option C's only legitimate use.

### Advisory register (SHOULD — fold where cheap)

| # | Item | Maps to |
|---|---|---|
| S1 | Trigger-generation **rides the MAIN model** (`manager.py:2233` → `skill_embedding_service.py:666-670` prefers `llm_config["model"]`; analysis tier is fallback-only), and the real pattern anchor is `skill_embedding_service.py:285/:542`, NOT `skill_search_service.py:310-312` (a JSON helper). Fix in D-Q3, TA §Q3, phase1 task 8 — reviewers will otherwise re-derive the wrong budget. | doc corrections |
| S2 | REST list (`GET /api/skills`) stays **DB-only** in v1 — the real strip mechanism is the `_LIST_SKILL_FIELDS` allowlist (`skill_store_service.py:90`, projection `:516`), not pydantic (zero `response_model` in the router; FE ignores unknown keys — verified). OQ-7's framing corrected; merging file rows would feed `pf:` ids into FE-built deactivate/share calls that 404. | phase3, OQ-7 |
| S3 | Dedicated bounded executor for file reads (mirror the v0.27.14 `db_offload` idiom) or a pinned concurrency bound — default-executor contention with DB `to_thread` calls is the routed R-3 class. Promote to MUST if Phase-0 measures a cold scan >50 ms. | phase2 task 2 |
| S4 | Wording: "fresh reads per search" → "index-backed + stat-gate" (SC 13 demands 0 re-scans per search); degraded-parity pin asserts file-in-top-5 surfaces and documents rank-6-10 exclusion as by-design uniform competition. | phase2 tasks 2/9 |
| S5 | Provenance header gains `project_id` + `root_path` (a wrong-scope injection becomes self-evident to the receiving agent); advisory distinguishes overflow-dropped SHADOWING names from ordinary overflow. | phase3 task 1, phase4 task 4 |
| S6 | Guard the `project_id=None` unfiltered embedding scan (`repository.py:1933-1941` feeding `skill_evolution_service.py:1176-1190`) — moot post-A1, belt if A1 is ever reverted. | TA §G risk 8 |

---

## 3. Stage / release impact (for the giter lane)

- **Migration delta:** now **+2** (or +3 if A2 chooses the usage companion table) — `create_project_skill_files.sql` + `create_project_skill_embeddings.sql` (+ `create_project_skill_usage_records.sql`). All additive `CREATE TABLE IF NOT EXISTS` class, no FK, no ALTER/DROP ⇒ the board/notify_audit `rollback_safe` adjudication class holds; `known_schema_gen` becomes the lexically-latest new file. Update plan-overview Rollout item 4 accordingly.
- **Tier 2 unchanged** (manager.py mechanically in the diff; api.py joins but is also core-seam adjacent — declare both).
- **Blast radius additions:** `daemon/api.py` (readiness param), `daemon/tools/skill_evolution_tools.py` (pf: guards), REST fix endpoint guard, containment/gc code in the new service module.

## 4. P1-P7 compliance re-check (post-amendment)

- **P1** — bodies never in DB: upheld and STRENGTHENED (A16 metadata-only cache; A1 moves vectors to a derived no-FK table — vectors were always P1-permitted derived metadata, and remain so).
- **P2** — fusion + tags: upheld (A6 narrows REST to DB-only in v1 — a P3-driven narrowing, disclosed, not a P2 breach: fusion remains total on the agent lane).
- **P3** — scope binding: **was violated as planned** (rung-1 widening + zero containment); A5/A6 close both. The "security-grade" claim is only true WITH A5/A6/A13 pins.
- **P4** — shadowing: **was breakable under budget overflow**; A7 restores it as an unconditional invariant.
- **P5** — injection opt-in: upheld (gate sits upstream; file skills inherit it; explicit-load parity unchanged; `pf:` fail-closed per A15).
- **P6** — cross-agent visibility: upheld and STRENGTHENED (A12 deletes the "AGENT dir name" namespace restriction — it contradicted P6).
- **P7** — git-only evolution: the OQ-4 reconciliation is **CONFIRMED sound** (no evolution machinery imported by trigger-query generation — verified); A14 closes the two enforcement holes (evolution tools + REST fix).

## 5. Decisions Pending (leader-level)

1. **REST file-skill visibility.** A6 rules REST `/api/skills/search` file-invisible in v1 because no server-side project binding exists (auth principal → project). If REST/FE fusion is wanted, that binding is its own small commission (v2) — decide whether to register it in the deferred list.
2. **A2 fork resolved by evidence at Phase 1:** usage companion table (+1 migration, keeps D-Q5 metrics value) vs deferred `pf:` feedback with an explicit envelope. The planner should pre-authorize the census to decide; default = companion table if any usage write exists on the file path.

## 6. What must return to the planner

Fold A1-A18 into the plan set (they amend, not rewrite). Concretely, by file:
- **decisions.md** — D-Q3 storage vehicle superseded (A1); D-Q5 feedback fork (A2); D-Q1 rung-1 confirm-only (A6); D-Q4 pre-budget shadow (A7); D-Q8b file-only semantics (A9); D-Q8 precedent census + disclosures + WARNING level (A18).
- **technical-analysis.md** — §B table set + unique key (A1/A3); §C assembly order (A7) + seam guard (A8); §E readiness shape (A11); §Q7 glob + namespace leg (A12); §Q1 rung-1 (A6); §G risk 8 (S6).
- **phase1-plan.md** — tasks 1 (cache invariant A16), 2 (containment A5, glob/fixtures A12), 3 (unique key A3), 7 (lifecycle A4), 8 (A1 table + A4 rules + S1 anchor), 10 (write census A2).
- **phase2-plan.md** — tasks 2 (guards A8, order A7, S3/S4), 5 (A7 pin), 7 (A6 conflict arm), 10 (REST invisibility pin).
- **phase3-plan.md** — task 1 (A15 fail-closed + S5 header), task 3 (A13 scope), task 10 (A9 semantics), blast radius + A14 guards.
- **phase4-plan.md** — task 2 (A10 six sites), task 3 (model-registration citation fix), task 4 (A11 always-publish), tasks 5/8 (A9 mode table, A18 WARNING), task 9 (A17 purge + A18 disclosure), TOUCHED list (+api.py, +skill_evolution_tools.py).
- **plan-overview.md** — migration delta (+2/+3), Rollout items 4/5 (A18 dual disclosure + REST/FE consequence), risks table (+A1 FK as closed, +A5/A6 as closed, A7 replaces risk 10's framing).

## 7. Gaps (disclosed, per fan-in discipline)

- **Skill-bank miss (worker 6):** the `system-decomposition` dispatch did not inject on either attempt (worker self-reported DIY both times; re-dispatch #1 also re-delivered truncated). All boundary findings are tree-verified DIY analysis at `b56444244` and internally consistent with the security worker's independent containment findings — accepted with attribution, flagged DEGRADED for skill-evolution metrics.
- **Unanswered sub-question (fold into Phase 1):** whether `resolve_active_project` duplicates an existing instance→project helper elsewhere in the tree (grep for project-resolution helpers outside `skill_tools`/`graph` at implementation time — 10 minutes, no architectural dependency).
- **Unverified claims carried with attribution (worker-flagged):** live main-DB constraint state (no DB contact; code-proven for fresh PG, long-lived-DB state inferred from boot order); `AuthConfig.enabled` exact default (matters only for REST exposure severity, not for A6's ruling — auth-on still permits arbitrary project_id); default-executor pool size on prod (S3 estimate pending a Phase-0-style measurement); whether `_embedding_rerank` tolerates a None embedding service under a hypothetical file-only construction (A9's tool-layer shape avoids needing it).

## 8. Confidence

**High** for A1-A8 (code-proven or single-pin-verifiable; the two headline claims re-verified by the architect). **Medium-high** for A9-A18 (evidence-cited, standard-engineering). The recommendation flips only if: the FK is absent on ALL real PG deployments (contradicted by `create_all` boot order — implausible), or the Phase-0 census finds shadow collisions beyond the 3 known ids (A18.4's falsifier, which re-opens the knob posture, not the architecture).
