# Technical / Architecture Analysis — Project Skill Source Loader

**Commission:** file-backed project skills (`.agents/*/skills/` canon) become a first-class SOURCE in the skill system — searchable, loadable, project-scoped; git as the distribution/evolution channel.
**Worktree:** `/home/nea/ensemble-src-wt-project-skill-loader` (branch `feature/project-skill-loader`, base `b56444244` = v0.28.1 tip).
**Status:** PLANNING — this document only. No implementation, no source edits, no commits.
**Evidence:** 3-explorer digest + in-worktree read-only verification at `b56444244` (all `file:line` anchors below re-checked: `skill_search_service.py` stages/seams, `skill_tools.py` project resolution + view truncation, `skill_injection_service.py` clone-on-miss seam, `skill_clone_service.py:143` clone contract, `manager.py:2222-2276/:2426/:3951-3987`, `graph.py:886-931`, `instance_lifecycle.py:2343`, `instance.py:1469`, readiness advisory precedent, the 5 canon manifests, the legacy `.agents/project-skills/bump-version` schema).

> ### Amendments applied (2026-10-31)
> Fold pass per `architecture-recommendation.md` §2/§6 (architect verdict SOUND-WITH-AMENDMENTS). Sections changed in THIS file, with amendment IDs:
> * **§Q1** — A6 (CRITICAL): resolver rung 1 is CONFIRM-ONLY; REST `/api/skills/search` serves ZERO file skills in v1 (leader ruling D1 2026-10-31).
> * **§Q2** — A1 pointer (storage-vehicle correction propagated).
> * **§Q3** — A1 (CRITICAL): dedicated `project_skill_embeddings` table replaces `skill_embeddings` reuse (FK wall); rejected alternative recorded; migration delta +2. A3: `root_path` enters pseudo-id. S1: trigger-generation anchor + model-tier corrected (MAIN model, `skill_embedding_service.py:285/:542`).
> * **§Q5** — A3 pointer (pseudo-id definition); A2 pointer (usage-record FK wall; Phase-1 census fork).
> * **§C** — A7 (CRITICAL): shadow set computed from the FULL file corpus BEFORE budget truncation; A8 (CRITICAL): fusion-seam exception guard.
> * **§D** — A8: STEP-0 exception guard (miss ⇒ fall through to DB path).
> * **§E** — A11: advisory ALWAYS publishes with `enabled:true|false`; OFF claim narrowed to "byte-identical search/list/view RESULTS"; OFF-side pin zero `pf-` rows. A18.3: shadow retirement log WARNING (was INFO). A2 pointer on the metrics line.
> * **§Q7** — A5 (CRITICAL): path containment specified at index AND every read; A12: scanner glob depth-exact + purely structural ("AGENT dir name" leg DELETED — contradicted P6); fixture list extended.
> * **§Q8** — A18: default-ON ratified on CORRECTED grounds (kill-switch family is MIXED — prior citation corrected); two-class rule; dual ceremony disclosure; falsifiability condition. A9 cross-ref: file-only mode semantics pinned (pointer, full spec owned by phase plans).
> * **§B** — A1/A3 table sketches (unique key + pseudo-id carry `root_path`; second table added); A4 four lifecycle rules + three carried invariants; A10 citation fix (`_ensure_postgres_columns` mirror is belt, pre-`create_all` model registration is operative); migration delta +2 (+3 if A2 census chooses the companion table).
> * **§G** — risk 8 (S6 advisory + A1 rewrite), risk 6 (A7 note), risk 12 (A4 note), risk 13 (+2 delta), risk 14 (A6/S2), Phase-0 falsifier (A18.4).
> * **Diagram (§A)** — embeddings box re-labeled post-A1.
> Not folded here by design (owned by other plan files): A2 decision fork (decisions.md/phase plans), A10 site enumeration detail (phase4), A13–A17 (phase plans / plan-overview).
> **Micro-fold pass (reviewer, 2026-10-31):** W-3 (§Q4 shadow-visibility clause marked SUPERSEDED — `shadows` tag on file result only; DB row never annotated/surfaced), W-7 (§Q3 P1 sentinel test re-specified: HIGH-ENTROPY 32-hex token planted in body; verbatim-byte invariant, not vocabulary; derived queries may paraphrase semantically), W-6 pointer (§Q8b M0–M11 → phase4-plan.md Appendix A, 2×2×3 reconstruction), citation re-anchors (skill_tools.py:171-172 → :167; skill_evolution_tools.py:212 → :211 — the latter does not occur in this file, no edit).
> **Leader-adjudicated final fold (approver iteration 003 item 4, 2026-10-31):** §Q8 observability bullet corrected — "shadow retirements at INFO" → **WARNING** (A18.3 alignment; A18.3 had landed only at §E). INFO sweep of the whole file: no other shadow-decision INFO remnant (remaining INFO hits are non-shadow: root-resolution first-seen :81, load-time digest-mismatch :211, index/re-index events §E, and the historical header record :16).

---

## 0. Binding Principles (user-ruled 2026-10-31 — hard constraints)

| # | Principle | Design consequence |
|---|-----------|--------------------|
| P1 | FILES ARE CANON, DB IS A VIEW | Index rows store **derived metadata only** (name/description/digest/manifest). Bodies are read FROM DISK at search-corpus assembly and load time. Never copy a body into any DB column. |
| P2 | Search fusion | `skill_search` merges DB + file corpora; every result carries `source: "db" \| "project-file"`. |
| P3 | Project-scope binding | File skills surface only for callers resolving into that project. Never cross-project leak. |
| P4 | Same-name precedence | A project file SHADOWS the same-name DB skill inside that project's scope (retires DB copies `9e79ccad`, `fd1e01dc`, `76e3e1a7a` — all three carry `supersedes` pointers from canon manifests, verified). |
| P5 | Injection opt-in unchanged | `skill_injection` flag semantics untouched. Search + manual load are the default surfaces. |
| P6 | Cross-agent visibility within project | All `.agents/*/skills/` readable/searchable by any agent in the project; namespaces organize, not ACL. |
| P7 | Git-PR evolution only | `skill_fix` never applies to file skills. Evolution machinery untouched for them. |

Non-goals (v2.0): no DB body copies; no skill_fix/evolution on file skills; no injection default changes; no public/global registry; no FE work beyond trivially necessary.

---

## 1. Decisions Q1–Q8

### Q1 — Active-project resolution

**Options considered**
1. **Instance row column only** (status quo of the dynamic-skill tool lane, `skill_tools.py:108-119` `_get_project_id`). Cheap but misses late bindings and ignores the injection lane's source.
2. **`instance_metadata["project_id"]` only** (the injection lane's source, `graph.py:886-931` `resolve_project_id`). Per-turn fresh, but tool lane and injection lane then still disagree in code.
3. **Single canonical resolver with priority chain** — one function both lanes call.
4. **Per-call path sniffing from the agent's bash cwd** — rejected: the daemon-side loader has no reliable access to the caller's shell cwd; tool workdir defaults exist (`instance.py:1469` `_get_project_workdir` → project `main_directory`) but nothing persists an instance→worktree binding today.

**RECOMMENDATION — option 3, with an explicit two-source reconciliation and a v1 root-path chain:**

A new module-level resolver (in the new `project_skill_source` service) is THE only place that turns `instance_id → (project_id, file_root)`:

```
resolve_active_project(instance_id, explicit_project_id=None):
  1. explicit_project_id — CONFIRM-ONLY (architect A6 2026-10-31; leader ruling D1 2026-10-31):
     explicit ∈ {None, instance-derived} ⇒ use instance-derived value;
     explicit present AND conflicting ⇒ REFUSE the file corpus for this call
     (WARNING with both values; DB results unaffected). Never a widening override.
  2. instance_metadata["project_id"]        # injection-lane source; per-turn fresh (graph.py:900-931
                                            # documents WHY: late bindings must not be missed)
  3. instances.project_id row column        # tool-lane source (skill_tools.py:108-119)
  4. None → no project scope
```

* **A6 (CRITICAL) — why rung 1 cannot widen:** `SkillSearchRequest` carries a caller-supplied `project_id` passed straight into search (`routers/skills.py:1032-1056`, consumed `:1051`), the router has no auth dependency of its own, and search output carries BODIES — an "explicit wins" rung is a cross-project body-read primitive, a direct P3 violation (the original "full caller control" annotation here was itself the contradiction). **Consequence:** REST `/api/skills/search` (no instance context) contributes **zero file skills in v1** — pinned as a REST-INVISIBILITY assertion (explicit-conflict refusal and REST-arbitrary-project ⇒ zero file entries are asserted behaviors, not invisibility-by-accident). Server-side REST binding (auth principal → project) is the parked v2 path per leader ruling D1 2026-10-31; registered in the plan's deferred register, not re-litigated here.

* If sources 2 and 3 are BOTH present and disagree → proceed with 2, log a WARNING with both values (drift signal; feeds the coupling in §G-2 rather than silently picking).
* File-root chain (same resolver, second return value):
  1. `instance_metadata["work_dir"]` **if present** (NEW optional convention — see below)
  2. `projects.main_directory` of the resolved project (verified resolvable: `repositories/project/models.py:345`, `repository.py:327-335` also exposes directory-based lookup)
  3. no filesystem root → file source contributes nothing for this call (graceful zero results, DEBUG log; **never** an error).

**Worktree subtlety — the position:** the worktree's own `.agents/` tree *is* the correct canon for dev on that branch, so the resolver prefers a worktree root when one is discoverable. Today NOTHING persists an instance→worktree binding (verified: `instance_lifecycle.py` spawn metadata keys are `project_id`/`instance_name`/`invoked_as_tool`/`model_override`/`mcp_tool_names`/`source_type`/`original_source`; no work_dir). So v1 ships the resolver hook + documents the `work_dir` metadata convention (a leader spawning a dev instance into `ensemble-src-wt-*` may stamp `instance_metadata["work_dir"]`), with the actual spawn-time plumbing as a v2 item (§H). Absent the key, v1 indexes `main_directory` — for THIS project that is the shared checkout (`/home/nea/ensemble-src`), which is the registered, user-owned canon.

**Index keying follows resolution:** the index is keyed by **resolved absolute root path**, not by project_id alone. Two callers in one project with different `work_dir` values get their own branch's file trees; both are bound to the same project_id so P3 holds and cross-worktree leak is impossible (different roots are different index entries; scope binding happens at call time).

**Non-repo-project behavior:** `main_directory` NULL, or the path does not exist, or it contains no `.agents/<ns>/skills/` dirs → the file source is simply absent from that call's corpus. No warning spam: one INFO at first resolution, DEBUG after.

**Evidence anchors:** `skill_tools.py:108-119`; `graph.py:886-931` (metadata priority + freshness rationale); `instance_lifecycle.py:2343` (metadata write at spawn); `instance.py:1469-1490` (workdir defaults to project main_directory — the precedent for path-derived behavior); `repositories/project/models.py:345`, `repository.py:327-335`.

**Risk / consequence:** the two-source reconciliation is a *behavior-visible* change only in logging (both lanes already resolve independently today). The `work_dir` convention is inert until a spawner adopts it — zero-risk additive. Main residual: this project's own v1 canon serves from the **shared checkout**, so a worktree lane's new/edited skills are not visible to search until merged/checked out there — the SAME reality as every other repo-committed resource (agents/, docs/); documented, accepted.

---

### Q2 — Indexing trigger + invalidation

**Options considered**
1. **Boot scan of all projects** — pays scan+embed cost for projects never touched this boot; needs a project enumeration pass; slows boot (house trend is the opposite: readiness-grace lanes).
2. **Lazy per-root index on first search/load touch** — pays only for projects actually in use; nothing at boot.
3. **Periodic sweep** — unnecessary once per-access digest revalidation exists.

**RECOMMENDATION — option 2 + per-access cheap revalidation:**

* **Trigger:** first `search()` / load call that resolves a root → `ProjectSkillSourceService.ensure_index(root)` (idempotent, in-process lock per root so concurrent first-touches can't double-scan). Subsequent calls hit the in-memory cache.
* **Invalidation:** every index HIT passes a freshness gate before use:
  * fast path — `os.stat` mtime+size per `skill.json` vs cached values (pure stat, ~µs per file; the canon is 5 dirs today);
  * on stat mismatch → `sha256(manifest bytes + body bytes)` compare → changed ⇒ re-parse that skill, update its index row, schedule embedding refresh; vanished ⇒ drop index row.
  * An external `git pull` / branch switch in a worktree is therefore picked up on the NEXT skill call, at stat cost. No watcher, no scheduler.
* **Where the derived index lives:** **hybrid** (full sketch in §B): in-memory cache for the parse tree + fresh body reads (canon, per-process), one durable DB table `project_skill_files` for manifest-derived metadata + content digests + last-indexed state, and a **dedicated `project_skill_embeddings` table** for persisted `(trigger_query, embedding)` rows keyed by a stable pseudo-id (Q3; **architect A1 2026-10-31** — reuse of `skill_embeddings` was REJECTED: its `skill_id` is a FK to `skills.id`). Embeddings survive restart without a vector extension.
* **Scan cost bound (hard caps, enforced at scan):** ≤ 64 namespaces (`​.agents/*/`), ≤ 128 skill dirs per namespace, `skill.json` ≤ 16 KB, body ≤ 64 KB (larger body → indexed as `body_too_large`, skipped for search corpus, still listed). A full cold scan of a canon-sized root is a stat+read+hash over a few dozen small files — milliseconds; the expensive part (embeddings) is best-effort and dedup'd by digest (§Q3).

**Evidence anchors:** corpus fetch cap 200/bucket (`skill_search_service.py:646-648`) shows the DB side already tolerates full-corpus loads at search time — the file side is an order of magnitude smaller; seed precedent (`manager.py:3951-3987`) demonstrates boot-time file walking is viable but we deliberately avoid boot coupling. *(A1 correction 2026-10-31: the earlier "skill_embeddings reusable without schema change" claim here was FALSE — `skill_embeddings.skill_id` is `ForeignKey("skills.id", ondelete="CASCADE")` at `models.py:594-601`, so every `pf-` insert raises ForeignKeyViolation on PG ⇒ silent permanent BM25-only; the vector home is now the dedicated table in §B.)*

**Risk / consequence:** stat-gate on every corpus assembly adds one `stat` per indexed manifest per search — negligible at canon scale, and the 64/128 caps make pathological repos bounded. Digest mismatch re-index does LLM work (Q3) on the search path — it MUST be fire-and-forget (serve stale-embedding skill immediately, refresh async) so a `git pull` mid-session never adds latency to a turn.

---

### Q3 — Ranking / embeddings without violating P1

**Options considered**
1. **BM25-only for file skills** — zero cost, but permanently second-class ranking vs DB skills that get stage-2 cosine re-rank.
2. **Embed description only** — cheap but wastes the strongest signal (trigger queries generated from the BODY, the pattern the DB side already uses).
3. **Index-time trigger-query generation + embedding persisted as derived index metadata** — mirrors the proven DB pipeline exactly.

**RECOMMENDATION — option 3:**

* At index time (and on digest change), for each file skill: generate 3–10 trigger queries from `name + description + body` (the DB-side pattern: trigger generation reads name+description+content and is a pure LLM call importing zero evolution machinery — verified `skill_embedding_service.py:285/:542`; this replaces the earlier `skill_search_service.py:310-312` citation, which is a JSON helper, not the generation pattern — **architect S1 2026-10-31**), embed each via `SkillEmbeddingService` (default `text-embedding-3-small`, `config.py:4398`), persist `(trigger_query, embedding)` rows into the **dedicated `project_skill_embeddings` table** with `skill_id = pseudo_id`, **no FK** (sketch in §B; **architect A1 2026-10-31** — see the rejected-alternative record below). This is derived index metadata — **permitted by P1**; the body itself is never stored anywhere. **P1 sentinel-test spec (reviewer W-7, 2026-10-31):** the naive sentinel ("grep `project_skill_embeddings.trigger_query` for the body") is unsound — trigger queries are LLM-derivative paraphrases of the body, so a body-like vocabulary word in a derived query would spuriously RED. The sound test plants a **HIGH-ENTROPY token** (a random 32-hex string) inside the body and asserts **"no ≥N-character verbatim substring of the planted body token appears in any derived trigger_query or embedding payload"** — i.e., the invariant is **verbatim body BYTES**, not vocabulary. Derived queries are PERMITTED to paraphrase body CONTENT semantically; only verbatim body bytes are forbidden (P1). The embeddings PRINCIPLE is CONFIRMED by the architect (trigger-generation imports no `skill_triggers`/lineage writes ⇒ P7/OQ-4 resolves clean).
* **A1 rejected alternative (recorded):** dropping the FK from `skill_embeddings` to admit `pf-` rows — REJECTED: schema mutation of an evolution-lane table, breaks the rollback posture, and diverges fresh vs long-lived DBs.
* Pseudo-id stability: `pf-<sha256(root_path|project_id|namespace|name)[:16]>` (**A3:** `root_path` enters the hash — see §B) → embeddings survive daemon restarts; content drift is caught by the digest gate (Q2). **A4 delete-after-success governs regeneration** (regenerate, THEN drop old — see §B Invalidation); note the pseudo-id is per-host-per-root (an index/cache identity, not a global one — consistent with P1's derived/non-authoritative nature).
* **Cost tier (S1-corrected):** trigger-query generation rides the **MAIN model** (`manager.py:2233` → `skill_embedding_service.py:666-670` prefers `llm_config["model"]`; the analysis tier is FALLBACK-ONLY) — the earlier "cheap/analysis tier" claim here was wrong; budget accordingly. Embedding rides the existing `embedding_model`. Best-effort post-write cost, same contract as `create_skill`'s `_refresh_embeddings`; the single-flight/debounce rule (§B A4.4) exists precisely because this is main-model money.
* **Fallback when embeddings unavailable** (no embedding service wired, provider error, evolution config absent): file skills stay in stage 1 (BM25 over fresh file text — always correct) and receive stage-2 score **0.0 neutral** — byte-identical to the DB pipeline's no-embeddings behavior (`skill_search_service.py:829-835` verified). Failure of embedding refresh NEVER fails a search.
* **Decoupling win:** pseudo-ids are `pf-…` shaped (never uuid4-shaped) and now live in a file-owned table with no FK (A1), so they cannot collide with DB skill rows anywhere; a project/root cleanup deletes `project_skill_embeddings` rows by pseudo-id — but because the hash is opaque, cross-root/project orphan cleanup needs the periodic gc of §B (A4.2), not a LIKE pattern.

**Risk / consequence:** an LLM outage during first-touch indexing leaves file skills BM25-only (score-0 stage 2) until the next digest-stable access retries refresh — self-healing **under the A4 single-flight/debounce + delete-after-success rules** (a delete-then-regenerate here would convert "stale" into "permanently absent", since the digest-stable access path never retries — architect W5/M5), disclosed in `detail.skill_sources.last_error` (§E). **Schema delta corrected by A1:** the embedding path DOES add a migration — lane delta is **+2** (or +3 if the A2 usage census chooses the companion table); see §B.

---

### Q4 — Same-name precedence mechanics

**Options considered**
1. **Enforce only at search-result merge (post stage-2)** — too late: stage 1's overlay merge dedupes by `skill.id` (`skill_search_service.py:672-698`, name-blind) so both variants enter the corpus; stage 3 maps candidates by `name.lower()` (`:906-912`, `:1001-1012`, collision-blind) so the LLM selecting "blast-radius-leg" resolves to an arbitrary one of the two.
2. **Enforce only at load resolution** — search would still surface the shadowed DB skill (a visible contradiction with P4).
3. **Enforce at BOTH, with the shadow visible as a tag.**

**RECOMMENDATION — option 3, with three exact enforcement points:**

1. **Corpus assembly (the load-bearing one) — BEFORE stage 1 tokenization, at the post-ownership-filter seam (`skill_search_service.py:709`):** after DB fetch + `_filter_owner_private`, the shadow set is computed from the FULL file corpus and the shadow pass drops DB rows whose `name.lower()` matches any in-canon file skill, with the budget applied only to non-shadowing file skills (**A7 ordering — see §C mechanics; the original "appended, then shadow pass" order let budget overflow surface the stale DB twin**). This is upstream of BM25 doc-count math AND upstream of stage-3's `name_to_skill` map, closing both collision-blindnesses in one place.
2. **Defensive post-stage-3 check:** if a DB skill somehow reaches `injected`/`low_match` alongside its file shadow (future refactor drift), the composition step drops the DB twin and keeps the file one. Cheap belt-and-suspenders; pinned by test.
3. **Load resolution (`inject_explicit_skill`)** — file-first lookup BEFORE `clone_on_miss_sync` (Q5/D): a file hit never reaches the bank-clone path, so the shadowed DB row is never cloned, refreshed, or re-materialized.

**Shadow visibility (SUPERSEDED clause — reviewer W-3, 2026-10-31; architect A7 / FR-P4-2):** surviving results carry `"source": "project-file"`; the corresponding DB twin's retirement is surfaced **only** as a `"shadows": ["<db skill name>"]` tag on the FILE result. **The original phrasing "the DB row, when listed by `skill_list` with file fusion, is annotated `shadowed_by`" is SUPERSEDED and MUST NOT be implemented** — shadow visibility = a `shadows` tag on the FILE result only, and the shadowed DB row is ABSENT entirely (never annotated, never surfaced). This keeps the P4 retirement auditable at the tool surface without leaking the shadowed DB twin through `skill_list`/`skill_view`/search.

**Evidence anchors:** `skill_search_service.py:672-698` (id-based overlay dedupe), `:703-709` (ownership filter position), `:906-912/:1001-1012` (name.lower() stage-3 mapping), canon manifests carrying `supersedes` → `{9e79ccad…, fd1e01dc…, 76e3e1a7a…, 08e67030…}` (verified by reading all five `skill.json`).

**Risk / consequence:** shadowing DB rows inside the merged corpus means the LLM stage never sees them — intended (P4). The `supersedes` pointers in manifests make the human audit trail durable in git. Residual: a DB skill created LATER with a colliding name is silently shadowed — acceptable and actually the desired P4 semantics; the `shadows` tag makes it visible to agents.

---

### Q5 — Load / inject path

**Options considered — identity/addressing**
1. **Manifest path as address** (`​.agents/tester/skills/blast-radius-leg`) — unambiguous but leaks layout into prompts and breaks on re-org.
2. **Pseudo-UUID** — opaque, hostile to agents, collides conceptually with real DB ids.
3. **Two-part scheme: human address + stable pseudo-id.**

**RECOMMENDATION — option 3:**

* **Tool-surface address:** `pf:<namespace>:<name>` (e.g. `pf:tester:blast-radius-leg`). Human-typeable, namespace-organized (P6), unambiguous vs DB uuids, and it round-trips through every existing tool that accepts `skill_id` with a prefix check.
* **Internal stable pseudo-id:** `pf-<sha256(root_path|project_id|namespace|name)[:16]>` (**A3:** `root_path` joined into the hash — two-roots-one-project must not share rows/vectors; live today via `EXTRA_ROOTS`) — the value carried as `.id` on the duck-typed object (stage-3 id rendering, `project_skill_files` PK, `project_skill_embeddings.skill_id`, usage rows if the A2 fork picks the companion table). Deliberately NOT uuid-shaped (collision-proof against real rows; SQL-greppable). Per-host-per-root cache identity (P1-derived).
* **Which surfaces read the file:**
  * `skill_search` — fused corpus (Q4); file results render with `source`, `address`, `shadows`, `body_path`.
  * `skill_view` — accepts a DB id OR a `pf:` address; for file skills renders: header metadata (name, version, category, tiers, namespace, address, body_path, digest), body FROM FILE, then a `## Loads` / `## Assets` metadata section (Q6). Truncation: keep the uniform 8000-char rule (`skill_tools.py:325-328`) but append the file path + "read_file the path for the full body" hint — file skills have real paths, an affordance DB skills lack.
  * `skill_list` — file skills listed alongside DB skills with `source` tag (namespace-grouped).
  * **`skill_fix` → REFUSE** on `pf:` addresses with explicit guidance: *"File-backed skills evolve via git PRs against `.agents/<ns>/skills/` — skill_fix does not apply (P7)."* (P7, hard.)
  * **`skill_feedback` → ACCEPT** `pf:` addresses, metrics-only: usage rows under the pseudo-id (which skills agents actually use is exactly the signal a git-PR evolution decision needs); **no lineage rows, no evolution dispatch**. This is the P7-compliant half of the feedback pair. **A2 (2026-10-31):** `skill_usage_records.skill_id` is ALSO FK-bound to `skills.id` (architect re-verified `models.py:378-392`) — a write under a `pf-` id hits the same wall. Fork (owned by decisions.md/phase plans, default = companion table if any usage write exists on the file path): either a small `project_skill_usage_records` (+1 migration, lane delta becomes +3) or an explicit "not supported in v1" envelope for `pf:` feedback (never a silent `recorded:false`). This plan records both branches and does not pick; the Phase-1 write census decides. The metrics value is preserved by the companion-table branch.
* **Interception point:** `SkillInjectionService.inject_explicit_skill` — BEFORE the `clone_on_miss_sync` call (`skill_injection_service.py:454/:479`; clone mutates DB rows at `skill_clone_service.py:143` — refresh-on-existing AND clone-on-miss both write). New step 0: resolve `<name>` (and `pf:` address) against the file source for the caller's project scope; on hit, render from the fresh file body through the SAME formatter path (the formatter reads `.content` / `.ab_test_group` / `.status` off the object — `skill_injection_service.py:1019`, `:639` — a duck-typed object satisfies all three) and return `injected_skill_ids=[pseudo_id]` for the usage-tracking metadata write. **No DB write occurs on the file path** — clone-on-miss is unreachable for file skills (pinned, §G-3).
* **Injection rendering, `inject_skills` path:** no change — file skills enter via the fused search corpus (Q4) and flow through the existing A/B/capability pipeline; `ab_test_group=None` skips A/B (verified branch behavior), requirement pre-flight is vacuous (no `requirement_json` on file skills).
* **loads[]/supersedes surfacing (v1/v2 split in Q6).**

**Risk / consequence:** the interception adds one file lookup before every explicit injection — one stat + one read, negligible. The `skill_feedback` acceptance is a deliberate deviation-risk: documented here as metrics-only so it is not mistaken for evolution integration (evolution machinery remains untouched — the dispatcher is never invoked for `pf:` ids; pinned).

---

### Q6 — assets/ and loads/ manifest fields

**Options considered**
1. **Full recursive v1:** resolve `loads[]` transitively at index AND load; serve `assets/` via `/views/`.
2. **Pure body-only v1:** ignore `loads`/`assets` entirely.
3. **Metadata-surfacing v1 + deterministic name-resolution; recursive resolution + asset serving in v2.**

**RECOMMENDATION — option 3, with one v1 addition forced by the evidence:** index-time **validation** of `loads[]`/`assets[]` references (name exists in the same namespace; asset path exists on disk) so a broken reference fails loud at index (Q7 semantics), not at gate time.

* **Evidence:** `assets[]` is empty in all 5 canon skills (verified) → asset serving has no consumer today → defer. `loads[]` is populated in 3 of 5 (`blast-radius-leg` → `failed-digest-equality-gate`; `regression-ab-partition-run` → 2 deps; `whole-tree-base-proven-diff` → 1 dep) and the gate doctrine DEPENDS on it: `ensure.md` mandates "READ the recipe skill body for that tier" before any gate leg, and the recipe bodies reference their dependency skills by name.
* **v1 contract:**
  * Search/index carry `loads` + `assets` as metadata arrays on the result object (no resolution, no recursion).
  * `skill_view` renders a `## Loads` section listing each dependency's `pf:` address + one-line description — the dependency chain becomes deterministic from the canonical artifact itself.
  * Resolution CAPABILITY ships in v1: because `skill_search`/`skill_view` resolve `pf:` names (Q5), an agent executing the ensure.md mandate can always reach every transitive dependency by name. **Gate-leg determinism therefore holds in v1 without recursive auto-injection** — the determinism primitive is the mandated recipe-load + name resolution, exactly the mechanism the doctrine already ratified on 2026-10-31 (tiered-gate-doctrine canon; `.agents/tester/rules/ensure.md:9-28`).
  * Explicit `load_skill` injection resolves the named skill only — depth 0. No transitive injection (an injection block that recursively pulls 3 gate recipes would blow the max_inject_skills=2 budget semantics and surprise-inject; rejected).
* **v2 (§H):** recursive loads resolution in the injection renderer (depth-bounded, budget-aware), `/views/` serving for `assets/` under a `live-view` root, cycles detection pin.

**Risk / consequence:** the v1 split keeps gate determinism (the actual incident-class requirement from the 2026-10-25 G2 retro) while avoiding recursion bugs and new serving surfaces. Residual: an agent that ignores the mandate and expects auto-loaded transitive deps gets the `## Loads` pointer instead — behavior-visible but doctrine-compliant (the mandate, not auto-injection, is the ratified mechanism).

---

### Q7 — Validation + failure modes

**Options considered**
1. **Hard error** (boot/scan fails on one malformed dir) — rejected: a single bad `skill.json` must never wedge search or boot; the house pattern is per-item fail-loud + aggregate visibility (prompt-artifact baseline, census instruments).
2. **Silent skip** — rejected: invisible failure recreates the G2 discovery failure mode in a new form.
3. **Per-skill fail-loud: validate at index, re-check at load, report aggregated.**

**RECOMMENDATION — option 3:**

* **Index-time validation (the gate):** per skill dir, in order: `skill.json` parses as JSON → `schema_version == 1` (unknown version → skip, warn ONCE per root) → required keys `name` (non-empty), `description`, `body` → `name == directory name` (mismatch → skip + warn with both values) → body file exists, ≤ 64 KB → `loads[]` names resolve within the namespace, `assets[]` paths exist → `supersedes` shape valid if present. ANY failure → **skip that skill**, WARNING log with the path + reason, `last_error` persisted on the index row (§B), scan continues. Scan-level aggregate: `detail.skill_sources.invalid_skills` count (§E). The scan itself never raises into the caller — search proceeds with whatever validated.
* **Legacy-schema conflation hazard — the explicit exclusion rule:** the scanner ONLY ingests the depth-exact, purely-structural glob `<root>/.agents/<ns>/skills/<name>/skill.json` (**A12:** depth-exact, `skills` matched case-sensitively as a DIRECTORY, one-level walk, no recursion; `<ns>`/`<name>` are any legal dirname validated per A5 — the earlier "namespace must be an AGENT dir name" defense leg is **DELETED**, it contradicted P6 and would have rejected legitimate `.agents/shared/skills/` or `.agents/devops/skills/`). `.agents/project-skills/bump-version/skill.json` is (verified) a different schema — keys `id`/`triggers`/`default_bump`/`supported_project_types`, NO `schema_version` — and it fails BOTH structural legs: it has no `skills/` segment under `.agents/<ns>/` (glob never matches) and it fails the `schema_version == 1` gate. Both legs pinned with `bump-version` as the named negative fixture. **A12 fixture set:** nested `<name>/sub/skill.json` (reject); `.agents/shared/skills/<name>/` (**INGEST** — the positive P6 fixture); `skills/` present as a FILE (no crash); symlinked skill dir (refused per A5); `agents/*/skills-template/` (not ingested); `schema_version` absent/2 (skip + warn-once, pin the warn-once state lifetime); name≠dir (visible `invalid` entry).
* **A5 (CRITICAL) — path containment, enforced at index AND at EVERY read** (the original plan specified containment NOWHERE; `body_path` relative-to-root was a comment, not a rule, and `.agents/` trees are branch content writable by anyone with branch access): (a) namespace/name components validated `^[A-Za-z0-9][A-Za-z0-9._-]*$` — no `.`/`..`, no separators, no absolutes; (b) manifest `body` and each `assets[]` path rejected PRE-realpath if absolute or containing `..`; (c) `realpath()` containment inside `realpath(root)/.agents/<ns>/<name>/` — **skill-dir confinement**, not merely root; (d) symlinked `skill.json`/body/namespace-dir resolving outside root refused with fail-loud reasons `path_escape` / `symlink_escape` / `bad_component`; (e) containment **re-checked on every load-time read** — the serve-fresh rule below applies to *content freshness only*, never as a bypass of the containment gate (a mid-session symlink swap must not pivot the served body under daemon privileges). Mutation legs for the Phase-5 matrix: absolute body path, `../` body path, symlinked ns dir — planted-defect RED→GREEN.
* **Load-time re-check:** the loader reads the FILE fresh (P1) — validation was done at index, but the file may have changed since. Behavior on digest mismatch at load: **serve the fresh file content anyway** (file is canon — the freshest truth beats index staleness), log INFO, schedule async re-index (Q2) — *after* the A5 containment re-check passes; a containment failure at read time is a refusal, not a serve-fresh case. Missing/corrupt body at load → return an explicit error envelope to the caller ("file skill `pf:tester:x` body unreadable: <reason>; re-index scheduled") — never inject an empty body.
* Name/dir mismatch and unknown-schema failures surface in `skill_list` (file section) as `invalid` entries with reasons — visible, not silent.

**Risk / consequence:** fail-loud-per-skill keeps one typo from blinding the whole project's file corpus, and keeps the operator story honest (`invalid_skills` is a number an operator can act on). The dual-leg legacy exclusion (structural glob + schema gate) makes the `bump-version` conflation — the one realistic ingestion accident — structurally impossible, and it is test-pinned. A5 closes the read-time hole the original text had: without it, serve-fresh would have been a symlink-swap body pivot under daemon privileges; with it, serve-fresh is content freshness behind an every-read containment gate.

---

### Q8 — Config surface (+ Q8b evolution-gating independence)

**Options considered**
1. **Gate the file source behind `config.skill_evolution`** like the DB stack (`manager.py:2222-2276`) — rejected: couples a file-reading, LLM-optional source to an evolution-config gate; in evolution-off deployments agents lose file skills entirely, recreating G2 blindness.
2. **Independent service, single enable knob, default ON.**

**RECOMMENDATION — option 2.**

* **Knob:** `ENSEMBLE_PROJECT_SKILL_SOURCE_ENABLED`, **default ON**. **A18 grounds CORRECTED (2026-10-31):** the original citation here ("same family … default ON") was factually wrong — the house kill-switch family is MIXED: evolution lane is default-**OFF** (`config.py:4486-4487`, incident-driven), notify-user ON (`config.py:3078-3085`), hard-reboot boot pass ON (`message_queue_boot_recovery.py:55-60`). The real house rule is a **two-class rule**: ON for zero-user-action read/serve surfaces; OFF for agent-authored mutation lanes. The file source is a read/serve surface ⇒ **ON class**. The `os.environ` kill-switch idiom itself stands.
* **Default ON is correct** because: (a) the source is inert-absent — a project with no `.agents/*/skills/` tree contributes nothing, so ON changes nothing for every project except the ones that deliberately shipped a canon; (b) P4 shadowing of the 3 legacy DB gate-runner copies in THIS project is the DESIRED outcome — the canon manifests carry `supersedes` pointing at exactly those ids (verified) — the DB copies are stale duplicates of git-canon; (c) P5 holds: no injection default changes — file skills reach injected context only for `skill_injection=on` agents exactly as DB skills do; search + manual load are the universal default; (d) prod safety at ceremony: the surface is additive to `/readyz` **detail only** and never flips readiness (§E), and the source reads repo files it already has access to — no new privilege.
* **Path overrides:** none in v1 (paths are derived from the project row — that IS the feature). One debug affordance: `ENSEMBLE_PROJECT_SKILL_SOURCE_EXTRA_ROOTS` (comma-separated absolute roots) for dev/testing a worktree canon without metadata plumbing — documented as non-production.
* **Observability:** per-source result tagging (`source` on every search result), index events at INFO (root, n skills, n invalid, duration), **shadow retirements at WARNING** (approver iteration 003 item 4, 2026-10-31; architect A18.3 — shadowing is the only non-additive effect in the surface and must be greppable by an operator who doesn't know to look; the original "at INFO" here is corrected), digest-flip re-index at DEBUG/INFO, all failures into `detail.skill_sources` (§E).
* **Q8b — independence from the evolution gate: YES, decisively.** Precedent in-tree: the Skill Bank seeding is explicitly *"NOT gated by skill_evolution — the Skill Bank is standalone infrastructure"* (`manager.py:3951-3956`, verified). The file source is the same class of standalone infrastructure. Wiring implications:
  * `ProjectSkillSourceService` is constructed **unconditionally** in manager init (cheap: no LLM, no DB at construct; scan is lazy per Q2).
  * `SkillSearchService` gains an OPTIONAL `file_source=None` constructor param: present → corpus fusion (Q4); absent/evolution-off → unchanged DB behavior. The REST `/search` endpoint fuses automatically because it passes through the service (`routers/skills.py:1033-1056`, verified passthrough).
  * **Degraded-mode tool behavior:** when `config.skill_evolution is None` (DB stack absent, tools soft-fail today at `skill_tools.py` service-None guards), `skill_search` / `skill_view` / `skill_list` still serve FILE skills — the tool guards become per-source: file source available → answer from it; DB service None → answer file-only instead of the current blanket "not yet available" for search/list/view. `skill_create` / `skill_fix` remain evolution-gated (they are DB-write/evolution surfaces — unchanged). **A9 (CRITICAL) — file-only mode is a THIRD behavior surface with pinned semantics** (the original text deferred them entirely): file-only search = stage-1 BM25 over the file corpus ONLY (same tokenizer), `top_k = max_results`, **no stage 2, no stage-3 LLM** (deterministic, zero LLM cost, works with no evolution config), results in the standard `{"injected", "low_match"}` shape with `source: "project-file"` and a `degraded: "file_only"` marker; `skill_view`/`skill_list` serve files; `skill_create`/`skill_fix` soft-fail as today. All four matrix cells pinned against the M0–M11 mode table — **phase4-plan.md Appendix A (M0–M11)** (planner ruling W-6, 2026-10-31; 2×2×3 reconstruction: knob ON/OFF × evolution present/absent × file-source state healthy/unreadable/erroring; M3/M7/M8 aligned to architect A8's descriptions) — full task-level spec owned by the phase plans; this is the cross-reference.
  * Explicit injection via `load_skill` stays evolution-gated in v1 (it lives in the injection service + clone service, both DB-write surfaces). File-skill explicit injection in evolution-off deployments is therefore search+`skill_view` only — acceptable, documented; un-gating the injection path for file skills is a v2 item precisely because it would drag SkillInjectionService construction out from under the evolution gate (core-seam blast radius for no demonstrated need).
* **Knob layering (final shape):** `ENSEMBLE_PROJECT_SKILL_SOURCE_ENABLED` (file source, default ON) ⊥ `SKILL_EVOLUTION_*` config presence (DB stack, unchanged) — two independent switches; four combinations all well-defined: (ON, present) = full fusion; (ON, absent) = file-only search/list/view with A9 pinned semantics; (OFF, present) = today's behavior exactly **in search/list/view RESULTS**; (OFF, absent) = today's behavior exactly in results. **A11 narrowing:** the "(OFF) = today's behavior" claim applies to RESULTS only — `/readyz` detail legitimately differs under OFF (the `detail.skill_sources` block still publishes, with `enabled: false`); the OFF-side pin asserts **zero `pf-` rows in any skill table** (no index ⇒ no refresh ⇒ no writes).
* **A18 dual ceremony disclosure (both, at promote):** (a) P4 shadowing of `9e79ccad` / `fd1e01dc` / `76e3e1a7a` in this project at first enable; (b) THIS install runs evolution-OFF ⇒ the first boot activates file-only search/list/view where tools today say "not yet available" (the Q8b coupling) — plus the REST/FE consequence: the FE Skills page keeps showing the 3 legacy DB skills as active with no shadow indication (shadow-only retirement is invisible to the REST list; v2 FE/REST surfacing deferred).
* **A18.4 falsifiability condition:** the Phase-0 collision census (§G) asserts the shadowed set is EXACTLY the 3 known ids (`9e79ccad`, `fd1e01dc`, `76e3e1a7a`) — a superset found ⇒ the fusion/shadow split RE-ENTERS adjudication (fusion ON, shadowing gated) before stage; that re-open is the only legitimate use of the gated-shadow option and is a stage-time gate input, not a silent default.
  * **SUPERSEDED (leader adjudication 2026-11-01 — decisions.md Part 10):** the census invariant is LINEAGE-AWARE: exact-set = every canon-named live row belongs to a KNOWN lineage AND the shadow set enumerates every ACTIVE row per name; STOP fires only on an unknown lineage/name. The Phase-5 census superset (`regression-ab-partition-run`: gen-0 `978ae5cc` inactive → gen-1 `08a4332f` → gen-2 `fd1e01dc`, unresolved A/B pair, identical content) is a KNOWN lineage — correct P4 behavior, adjudicated; staging STOP lifted. Active shadow set at promote (enumerated): `08a4332f` + `fd1e01dc` + `9e79ccad` + `76e3e1a7a` (`blast-radius-leg` has no DB copy).

**Risk / consequence:** default-ON means the promote of this feature itself performs the P4 retirement of the 3 legacy DB skills in this project — intentional, and the `shadows` tags + `supersedes` pointers make it auditable; rollback restores DB-first behavior trivially (files ignored). The evolution-off file-only path is new surface in a lane that today has none — it must be gated by tests that construct a manager with `skill_evolution=None` (the degraded-mode pin, §G-4).

---

## A. Current-state map + target-state component design

### Current state (verified)

```
┌────────────────────────────── SKILL SYSTEM TODAY (DB-backed) ──────────────────────────────┐
│                                                                                            │
│  BOOT      agents/*/skill-set.yaml + skills-template/                                      │
│    │         └─ SkillSeedService.seed_all ──copies bodies──▶ skill_bank.content            │
│    │            (manager.py:3951-3987; bank doctrine-isolated from evolution)              │
│    │                                                                                       │
│  INJECT    graph.py agent_node ─▶ context_messages.assemble_context_messages               │
│    │         ├─ auto-load: clone_service.ensure_auto_load_skills_sync (context_messages    │
│    │         │   :1427-1447) ──clones──▶ skills.content (DB body copy #2)                 │
│    │         └─ per-turn: SkillInjectionService.inject_skills ─▶ SkillSearchService       │
│    │                                                                                       │
│  SEARCH    SkillSearchService.search (skill_search_service.py:399-486)                     │
│    │         Stage1 BM25 :597-757 ─ corpus= repo.list(project)+overlay(global), dedupe     │
│    │            by skill.id :672-698; ownership filter :703-709; tokenise name+desc+      │
│    │            content :720-723; caps 200/bucket :646-648, top_k 10                      │
│    │         Stage2 cosine re-rank :763-850 (MAX over skill_embeddings; none → 0.0        │
│    │            :829-835); top_k 5                                                        │
│    │         Stage3 LLM select :856-1034 (selector "quick" :932-938; name+desc prompt     │
│    │            :901-912; name.lower() map :906-912; fail → _degraded_select :1040-1075)  │
│    │         RESULT {"injected":[{"skill":<Skill obj>,"score":f}], "low_match":[…]}       │
│    │                                                                                       │
│  EXPLICIT  send_message(load_skill=…) ─▶ <meta> tag (instance.py:4329-4346) ─▶             │
│    │         instance_messaging extract_load_skill (:3877) ─▶                              │
│    │         inject_explicit_skill (skill_injection_service.py:419) ─▶                     │
│    │         clone_on_miss_sync (skill_clone_service.py:143) ──DB write──▶ skills row      │
│    │                                                                                       │
│  TOOLS     daemon/tools/skill_tools.py — 6 tools, project_id from instance ROW column      │
│    │         (:108-119); ALL soft-fail unless config.skill_evolution (manager:2222-2276)   │
│    │                                                                                       │
│  FILE SIDE .agents/tester/skills/ — 5 canon dirs (skill.json + body .md). Discovery =      │
│            ensure.md deterministic mandate (gate legs ONLY). skill_search is BLIND here.   │
│  LEGACY    .agents/project-skills/bump-version — different schema, must never be ingested. │
└────────────────────────────────────────────────────────────────────────────────────────────┘
```

### Target state — new modules

| Component | Path (new unless noted) | Responsibility |
|---|---|---|
| `ProjectSkillSourceService` | `daemon/services/project_skill_source/service.py` | Resolver (Q1), lazy index + digest invalidation (Q2), validation (Q7), file read (P1), address/pseudo-id minting (Q5), embedding refresh dispatch (Q3). Constructed UNCONDITIONALLY (Q8b). |
| `ProjectFileSkill` (duck-typed) | `daemon/services/project_skill_source/model.py` | Dataclass carrying: `id` (pseudo-id), `address`, `name`, `description`, `content` (fresh bytes→str from disk), `category`, `status="active"`, `is_active=True`, `generation=0`, `ab_test_group=None`, `project_id`, `source="project-file"`, `namespace`, `body_path`, `body_char_len`, `manifest` (version/tiers/loads/assets/supersedes/schema_version), `digest`, `shadows` (list of shadowed DB names). Satisfies every `getattr` the pipeline renders (verified: `skill_search_service` tokenization `:720-723`, stage-3 `:901-912`, injection formatter `:1019`/`:639`). |
| manifest loader + validator | `daemon/services/project_skill_source/manifest.py` | Parse + validate (Q7); legacy-schema exclusion legs. |
| index repository | `daemon/repositories/project_skill_file/{models.py,repository.py}` | `project_skill_files` table CRUD (§B sketch). |
| fusion seam (touched) | `daemon/services/skill_search_service.py` | Optional `file_source` param; corpus-assembly append + shadow pass (Q4); `source` tagging; defensive post-stage-3 dedupe. |
| load interception (touched) | `daemon/services/skill_injection_service.py` | File-first step 0 in `inject_explicit_skill` BEFORE `clone_on_miss_sync` (Q5). |
| tool surface (touched) | `daemon/tools/skill_tools.py` | `pf:` address awareness in search/list/view; `skill_fix` refusal + `skill_feedback` metrics-only acceptance; degraded-mode file-only answers when DB stack absent (Q8b). |
| wiring (touched) | `daemon/manager.py` | Unconditional source construction; optional injection into `SkillSearchService` + injection service; pre-`create_all` model registration for the two new tables + belt `_ensure_postgres_columns` entries (A10-corrected). |
| migration (new) | `daemon/migrations/versions/2026MMDD_HHMMSS_create_project_skill_{files,embeddings}.sql` | House rule: own file per table + ledger; SQLite-only runner. Lane delta **+2** (+3 if the A2 census adds the usage companion). |
| readiness block (touched) | `daemon/services/readiness.py` | `detail.skill_sources` advisory (§E) — never flips readiness. |

### Interaction diagram (index → search fusion → load)

```
                        ┌──────────────────────────┐
   caller (tool/REST/   │ resolve_active_project() │  explicit CONFIRM-ONLY (A6: conflict ⇒
   inject lane) ───────▶│  (Q1 — the ONLY entry)   │  refuse file corpus) → metadata["project_id"]
                        │                          │   → instances.project_id (warn on drift)
                        └───────────┬──────────────┘   → (project_id, file_root | None)
                                    │
              ┌─────────────────────▼──────────────────────┐
              │ ProjectSkillSourceService.ensure_index(root)│  lazy (Q2): in-memory cache
              │  stat-gate → sha256 digest per skill        │  + project_skill_files rows
              │  changed ⇒ re-parse + async embed refresh   │  (manifest meta ONLY — P1)
              └───────────┬─────────────────────┬───────────┘
        corpus objects    │                     │ embeddings (persisted, derived)
        (fresh file reads)│                     ▼
              ┌───────────▼──────────┐   ┌──────────────────────────┐
              │ duck-typed           │   │ project_skill_embeddings │
              │ ProjectFileSkill[]   │   │ rows (A1: no FK;         │
              │ (A16: metadata-only  │   │ skill_id = "pf-<sha16>", │
              │  cache; content read │   │ key incl. root_path — A3)│
              │  at call time)       │   │                          │
              └───────────┬──────────┘   └────────────┬─────────────┘
                          │                           │
   ┌──────────────────────▼───────────────────────────▼──────────────────┐
   │ SkillSearchService.search()                                          │
   │  Stage1: DB fetch → ownership filter → +FILE CORPUS (post-:709 seam,│
   │          A8-guarded) → SHADOW SET from FULL corpus pre-budget (A7:  │
   │          shadowing names budget-exempt) → drop DB twins by name     │
   │          → BM25 over merged corpus (uniform normalization — free)   │
   │  Stage2: cosine re-rank — file skills via pf- embeddings;            │
   │          no embeddings ⇒ 0.0 neutral (unchanged behavior)            │
   │  Stage3: LLM select (name+desc) → results tagged source: db|         │
   │          project-file (+ shadows tag); defensive post-dedupe         │
   └───────────┬──────────────────────────────┬──────────────────────────┘
               │                              │
   ┌───────────▼──────────┐      ┌────────────▼─────────────────────┐
   │ skill_search tool /  │      │ inject_skills (skill_injection=  │
   │ REST /search         │      │ ON agents) — unchanged pipeline, │
   │ (source-tagged JSON) │      │ file objs flow through as-is     │
   └──────────────────────┘      └──────────────────────────────────┘

   EXPLICIT LOAD: send_message(load_skill="blast-radius-leg"|"pf:tester:blast-radius-leg")
     └▶ inject_explicit_skill ─▶ STEP 0 file-first lookup (Q5) ──hit──▶ render FROM FILE,
        no DB write, injected_skill_ids=[pf-id], feedback metrics usable
                                   └──miss─▶ clone_on_miss_sync (unchanged DB path)
```

---

## B. Index design

**Storage choice — hybrid (decisive; A1-amended):** in-memory parse cache + fresh file reads (canon path, per-process; **A16 invariant: the cache holds METADATA ONLY, never body bytes** — cached `ProjectFileSkill` objects carrying `.content` would bound at ≈ 64 ns × 128 dirs × 64 KB ≈ 512 MB/root and violate P1's canon semantics) + TWO durable DB tables for derived metadata — `project_skill_files` (manifest metadata/digests/health) and `project_skill_embeddings` (persisted vectors; **A1: the original `skill_embeddings` reuse is REJECTED** — `skill_embeddings.skill_id` is `ForeignKey("skills.id", ondelete="CASCADE")` at `daemon/repositories/skill/models.py:594-601`, and `create_all` (`manager.py:906-909`) runs strictly before `_ensure_postgres_columns` (`:931`), so every `pf-` insert raises ForeignKeyViolation on PG ⇒ the per-row catch (`skill_embedding_service.py:622-627`) yields `written=0` ⇒ silent permanent BM25-only on fresh installs and remote replicas, plus retry/log churn). A pure in-memory index was rejected because restart would silently orphan embeddings vs changed content (no digest record survives); a body-in-DB design is forbidden by P1 outright.

**Table sketch — `project_skill_files`** (derived metadata ONLY; no body column — P1; unique key A3-amended):

```sql
CREATE TABLE IF NOT EXISTS project_skill_files (
    id              TEXT PRIMARY KEY,          -- A3: "pf-<sha256(root_path|project_id|ns|name)[:16]>"
                                               --     (pseudo-id is per-host-per-root; Q5 address unchanged)
    project_id      TEXT NOT NULL,
    root_path       TEXT NOT NULL,             -- resolved root this entry was indexed from (Q1); A3: part of the key
    namespace       TEXT NOT NULL,             -- dir name under .agents/ (any legal dirname — A12/P6, not agent-scoped)
    name            TEXT NOT NULL,             -- manifest name (== dir name, Q7)
    address         TEXT NOT NULL,             -- "pf:<namespace>:<name>" (human address unchanged by A3)
    description     TEXT NOT NULL DEFAULT '',
    category        TEXT,
    body_path       TEXT NOT NULL,             -- RELATIVE to root_path (A5: rejected pre-realpath if absolute/contains ..)
    manifest_sha256 TEXT NOT NULL,
    body_sha256     TEXT NOT NULL,
    body_char_len   INTEGER NOT NULL DEFAULT 0,
    manifest_json   TEXT,                      -- version/tiers/loads/assets/supersedes (metadata)
    valid           BOOLEAN NOT NULL DEFAULT TRUE,
    last_error      TEXT,                      -- Q7 per-skill failure reason (NULL = clean; A5 reasons live here too)
    embedded_at     TEXT,                      -- last embedding refresh (Q3)
    indexed_at      TEXT NOT NULL,
    updated_at      TEXT
);
-- A3: root_path joins the unique key — two-roots-one-project otherwise collides (last-writer-wins
-- flip-flop) and shares embedding rows (stage-2 vectors from root A served to root B callers).
CREATE UNIQUE INDEX IF NOT EXISTS uq_psf_root_project_ns_name ON project_skill_files (root_path, project_id, namespace, name);
CREATE INDEX IF NOT EXISTS idx_psf_project_id ON project_skill_files (project_id);
```

**Table sketch — `project_skill_embeddings`** (**A1**, the vector home; columns mirror `models.py:601-607`; **no FK** — `skill_id` is the opaque pseudo-id):

```sql
CREATE TABLE IF NOT EXISTS project_skill_embeddings (
    id              TEXT PRIMARY KEY,          -- uuid4 row id
    skill_id        TEXT NOT NULL,             -- "pf-<sha16>" pseudo-id; NO ForeignKey — A1's whole point
    trigger_query   TEXT NOT NULL,
    embedding       TEXT NOT NULL,             -- JSON float array (same storage shape as :601-607)
    created_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_pse_skill_id ON project_skill_embeddings (skill_id);
```

**Migration delta (lane):** **+2** — `create_project_skill_files.sql` + `create_project_skill_embeddings.sql` (**+3** if the A2 usage census chooses the `project_skill_usage_records` companion table). All additive `CREATE TABLE IF NOT EXISTS`, no FK, no ALTER/DROP ⇒ the board/notify_audit rollback-safe adjudication class holds.

**House rule compliance (verified against the tree; A10 citation CORRECTED):** every new SQLModel table ships its own `.sql` in `daemon/migrations/versions/` (the file is the persistent record; runner is SQLite-only — `20260710_000001_create_skill_tables.sql`, `20260713_000001_create_skill_bank.sql` precedents). **The OPERATIVE fresh-PG artifact is the model registration BEFORE `create_all`** — the original text's `manager.py:797-829` citation was wrong for this purpose: that comment documents that BRAND-NEW tables need NO `_ensure_postgres_columns` mirror entry (they arrive via `create_all` on boot). Keep the mirror entry anyway as belt per the two-artifact rule (the `skill_bank` additive-COLUMN idiom at `manager.py:8345-8383` is the belt precedent), but the pin must assert pre-`create_all` model registration — a mirror-only table that is not in `SQLModel.metadata` never materializes on fresh PG. Two artifacts per table, both pinned; zero exceptions.

**Invalidation:** trigger = any `ensure_index` access (i.e., first skill-surface call per root per change-window); strategy = stat fast-path (mtime+size) → sha256 on suspicion (Q2). **Embedding lifecycle — A4 (four binding rules, replacing the original delete-then-regenerate one-liner):**
1. **Delete-with-row:** deleting an index row deletes its embedding rows (same transaction, or a compensating sweep) — vanishing skills/namespaces must not orphan vectors.
2. **Periodic gc:** a periodic/one-shot sweep removes `project_skill_embeddings` rows whose `skill_id` ∉ `project_skill_files` (covers project deletion, root changes, project_id changes — the opaque hash makes per-project LIKE cleanup impossible; no gc existed anywhere in the original plan).
3. **Delete-after-success:** regenerate the new rows, THEN drop the old — delete-then-regenerate converts "stale" into "permanently absent" because the digest-stable access path never retries (architect W5/M5).
4. **Single-flight + debounce:** at most one in-flight refresh per pseudo-id; refresh only after the digest is stable (bounded backoff) — branch toggling must not thrash MAIN-model LLM calls (S1: generation rides the main model, §Q3).

**Carried invariants (pin as acceptance criteria, not prose):** (i) corpus swap is wholesale under the per-root lock — readers see pre- or post-state, never torn; (ii) index mutation never blocks or fails a search; (iii) the vector for a skill is generated from the same root's body that will be served (A3 makes this hold by construction).

**Boot vs lazy:** lazy (Q2). Nothing in lifespan; first-touch builds. The boot path only carries the (cheap) unconditional service construction.

---

## C. Search integration design

**Seam choice (decisive): corpus assembly at `skill_search_service.py:709` — post-ownership-filter, pre-tokenization.** Post-stage-2 composition was rejected because it forces a score-normalization policy across two incomparable scales (BM25 unbounded-positive vs cosine [-1,1]) and still leaves the stage-3 `name.lower()` collision window open. Corpus-assembly fusion inherits BM25 normalization free (one corpus, one scorer), closes the stage-3 collision at the root (Q4), and automatically degrades correctly: stage 2 gives file skills 0.0-neutral without embeddings, stage 3 sees one candidate per name.

**Mechanics inside `_bm25_prefilter` (after the existing overlay + ownership filter, before tokenize) — ORDER FIXED by A7 (CRITICAL):**
1. `file_skills = await file_source.corpus_for(project_id, root)` (fresh file reads via `asyncio.to_thread` — the corpus read must NOT run on the loop; §G-11) — **A8: this call is locally guarded** (`except ⇒ WARNING + [] + last_error`); a broken file source degrades to DB-only results, it must NEVER escape to the user-visible `"ERROR: skill_search failed"` string (`skill_tools.py:167`).
2. **A7: shadow set computed from the FULL file corpus BEFORE any budget truncation** — `db_names = {lower(name) of DB rows}`; every in-scope DB name matching ANY in-canon file skill enters the shadow set. **Preferred variant (adopted):** shadowing names are EXEMPT from the file budget — the 50-cap applies to non-shadowing file skills, preserving availability while P4 holds unconditionally. (Acceptable fallback variant, if the exemption complicates the cap implementation: full-corpus shadow ⇒ both file skill and DB twin hidden under overflow, surfaced via a dedicated overflow counter.) The as-written append→budget→shadow order was a defect: budget overflow would have surfaced the stale DB twin P4 exists to retire, silently flipping retirement as file count crosses the cap. Pin: >50 file skills incl. one DB twin ⇒ twin absent from injected AND low_match, every variant.
3. Append the budget-truncated file corpus + shadow-exempt entries; stamp `shadows` on the file twins; drop the shadowed DB rows.
4. Tokenize the merged list — zero further changes to scoring code.

**Duck-typed skill object:** the fields the pipeline actually touches (all verified by read): `id`, `name`, `description`, `content`, `category`, `status`, `is_active`, `ab_test_group`, `generation`, `project_id` + our additive `source`/`address`/`shadows`/`manifest` (additive keys are tolerated by both consumers — result shape evidence E1). No DB model inheritance; a plain dataclass.

**Caps interaction (A7-amended):** DB fetches keep the existing 200/bucket cap untouched. The file corpus keeps its **own budget of 50** entries — now applied to NON-shadowing file skills only (A7 preferred variant: shadowing names exempt) — with overflow counted in `detail.skill_sources`, so neither source can silently crowd the other out of stage-1's top_k=10 and P4 retirement can no longer flip on file count. `bm25_top_k` (10) / `llm_select_top_k` (5) / `max_inject_skills` (2) / `low_match` (3) are untouched — file and DB skills compete uniformly AFTER corpus assembly.

**Source tagging:** every emitted injected/low_match entry gains `"source": "db"|"project-file"` (+`"address"`, `"shadows"` where applicable). Both consumers (tool JSON, REST passthrough) tolerate additive keys (verified result-shape evidence + `routers/skills.py:1033-1056` passthrough).

**Degraded paths (all preserved):** no file root → DB-only; no DB stack (evolution None) → file-only via the tool-layer degraded answers (Q8b); no embeddings → 0.0 neutral; LLM stage failure → `_degraded_select` unchanged (it operates on the merged ranked list — file skills degrade identically to DB skills).

---

## D. Load-path design

**Interception (decisive):** new STEP 0 at the top of `inject_explicit_skill` (`skill_injection_service.py:419+`), BEFORE the `clone_on_miss_sync` call at `:479`:

```
step 0: if file_source is not None:
            fs = await file_source.lookup_explicit(skill_name, project_id)   # accepts bare name or pf: address
            if fs is not None:
                return render_file_skill(fs, instance_id, message_id)        # formatter unchanged;
                                                                             # injected_skill_ids=[fs.id]
```

On a file hit the DB path is *unreachable* — no refresh, no clone, zero DB writes (the P4/P1 pair enforced mechanically; pinned). On a miss, the existing clone path runs byte-identically. **A8: STEP 0 is locally guarded** (`except ⇒ WARNING + treated as a MISS + `last_error``) — a broken file source must degrade to the DB clone path, never break explicit injection of **DB** skills; guard pins cover the M3/M7/M8 modes (root unreadable mid-search, file-only mode broken, corpus raises). Precedence in one sentence (**A15-aligned**): file (STEP 0) → `clone_on_miss_sync` (project row → bank template) → repo fallback.

**Stable identity:** `pf:<namespace>:<name>` tool address + `pf-<sha16>` internal id (Q5). Both accepted by `skill_view`; bare-name lookups are scoped by the caller's resolved project (Q1) so bare names stay safe within a project.

**Rendering:** the existing injection formatter consumes the duck-typed object unchanged (`.content` `:1019`, `.ab_test_group` `:639`, `.status`; A/B skipped on `ab_test_group=None`; requirement pre-flight vacuous). File-skill bodies render verbatim from disk. A one-line provenance header is prepended by the file renderer only (`[project-file skill: pf:tester:blast-radius-leg @ <body_path> v1.0.0]`) so an agent can trace the artifact to git.

**Size handling:** read capped at 64 KB (Q2); injection renders full body (injection has no truncation today — verified formatter); `skill_view` keeps the uniform 8000-char truncation but appends the file path + read_file hint (Q5). The current canon's largest body is 8001 bytes (`regression-ab-partition-run.md`, verified) — the truncation note will fire for it in `skill_view`, which is exactly the hint an agent needs.

---

## E. Config + observability design

**Knob:** `ENSEMBLE_PROJECT_SKILL_SOURCE_ENABLED` (default ON) + `ENSEMBLE_PROJECT_SKILL_SOURCE_EXTRA_ROOTS` (dev-only, default empty). Rationale in Q8.

**Readiness advisory map — `detail.skill_sources`** (modeled on the ratified `detail.grace` / `detail.mcp` advisory pattern, `daemon/services/readiness.py:512-531` — *always published, NEVER changes `status`/`components`/`detail.reasons`; an older daemon publishing no block reads as "unknown" and every consumer fail-closes to "feature absent"). **A11 ruling:** the block publishes **ALWAYS**, with an explicit `enabled: true|false` — observability of a disabled feature is exactly what an operator needs, and `detail` is additive:

```json
"skill_sources": {
  "enabled": true,
  "db_stack_present": true,
  "roots": [
    {"root_path": "/home/nea/ensemble-src", "project_id": "83da04de…",
     "namespaces": 1, "skills_indexed": 5, "invalid_skills": 0,
     "last_scan_ms": 12.4, "last_scan_at": "…", "last_error": null}
  ],
  "shadowed_db_skills": ["blast-radius-leg", "regression-ab-partition-run", "whole-tree-base-proven-diff"],
  "overflow_dropped": {"file_skills": 0, "shadowing_names": 0},
  "embeddings_pending": 0
}
```

Published whenever the service is constructed (i.e., ALWAYS, per Q8b — including the knob-OFF state with `enabled: false`); empty `roots` when no project has a canon — an honest zero, not an absent key. `shadowed_db_skills` gives the P4 retirement a permanent operator-visible record; `overflow_dropped.shadowing_names` distinguishes overflow-dropped SHADOWING names from ordinary overflow (A7 variant bookkeeping). **OFF claim narrowed (A11):** under the knob-OFF state, search/list/view RESULTS are byte-identical to today — `/readyz` detail legitimately differs (the block publishes with `enabled: false`); the OFF-side pin asserts zero `pf-` rows in any skill table. Logs: index events INFO; **shadow retirement WARNING (A18.3 — the only non-additive effect in the surface; it must be greppable by an operator who doesn't know to look; was INFO in the original text, corrected)**; digest re-index INFO; validation failures WARNING (Q7); embedding refresh failures WARNING with `last_error` echo. Metrics: selection counters flow via usage records **only if the A2 fork picks the companion table** (`pf:` writes into `skill_usage_records` hit its FK wall — see §Q5) — A/B-neutral by construction.

---

## F. Gate tier classification

**Verdict: this lane must budget TIER 2 (whole-tree dual-side, ≤90 min advisory, fail-loud) — mechanical, not a judgment call.** The core-seam list in `.agents/tester/rules/ensure.md:19` mechanically triggers Tier 2 on any diff touching `daemon/manager.py`, and this lane touches `manager.py` three ways (unconditional service construction, optional injection into `SkillSearchService`/injection service, `_ensure_postgres_columns` mirror). `skill_search_service.py` / `skill_injection_service.py` are not on the core-seam list, but the manager wiring alone is decisive; the fusion + interception changes sit deep in the injection pipeline and justify the whole-tree net-new=0 claim regardless.

**Budget:** Tier-2 whole-tree base-proven diff ≤ 90 min advisory (vs Tier-1's 30-min blast-radius cap — irrelevant here since Tier 1 is off the table mechanically). Recipes load per the ensure.md mandate from `.agents/tester/skills/` (deterministic path): `whole-tree-base-proven-diff` (loads `failed-digest-equality-gate`). Note this very lane is the first real consumer of the Q6 v1 loads-resolution capability — the recipe chain resolves through the canon the moment `pf:` name resolution lands, and until then through the existing deterministic path mandate.

**Phase-0/gate interplay:** new SQLModel tables drag the migration house rules into the gate scope (own `.sql` per table, pre-`create_all` model registration + belt `_ensure_postgres_columns` entries per the A10 correction, migration-delta disclosure **+2** (+3 if A2 companion) for the stage/ceremony record, `rollback_safe` adjudication — additive CREATE TABLE = the established `board`/`notify_audit` class, rollback-safe by the old-binary-ignores-new-table argument). The A18.4 census falsifier rides the stage gate as an input (superset shadow collisions ⇒ re-adjudicate before stage). **AMENDED (leader adjudication 2026-11-01 — decisions.md Part 10):** under the lineage-aware invariant the re-adjudication trigger is an UNKNOWN lineage/name only; the Phase-5 `regression-ab-partition-run` lineage superset was adjudicated KNOWN (STOP lifted).

---

## G. Risk register + couplings

| # | Risk | Disposition |
|---|------|-------------|
| 1 | **Score-scale mixing** (BM25 vs cosine across sources) | ELIMINATED by seam choice — fusion at corpus assembly means one scorer over one corpus (§C). Residual: none beyond normal BM25 variance. |
| 2 | **Two project-scope sources** (row column vs metadata) | Reconciled by the single resolver (Q1) with priority + drift WARNING. Coupling: any future code reading either source directly re-fragments scope — the resolver becomes the only sanctioned entry (pin: grep-fence test asserting no direct `metadata.get("project_id")` outside resolver/graph-legacy). |
| 3 | **clone-on-miss mutation leaking a file skill into DB** | Step-0 ordering (Q5/D) + mutation pin: plant a file skill with a same-name bank template, assert `skills` row count unchanged after explicit inject. |
| 4 | **`config.skill_evolution` gating inheritance** | Solved by unconditional construction + per-source tool degradation (Q8b); degraded-mode pin: manager with `skill_evolution=None` must still answer file-skill search/view. |
| 5 | **Stage-3 name-collision blindness** | Shadow BEFORE stage 3 (Q4-1) + defensive post-stage-3 dedupe (Q4-2) + pin: LLM-mock selecting the shadowed name twice resolves to the file object. |
| 6 | **Corpus caps (200/10/5/2) silently truncating one source** | Dedicated file budget 50 at corpus assembly, applied to NON-shadowing file skills only (**A7**: shadowing names budget-exempt; overflow counters in `detail.skill_sources`, incl. the shadowing-vs-ordinary distinction); caps themselves untouched; the P4 retirement can no longer flip on file count (§C pin). |
| 7 | **skill_injection flag semantics drift** | None — P5: no new injection defaults; file skills ride the existing `inject_skills` gate identically; explicit-load path is opt-in by construction. |
| 8 | **Pseudo-id collisions / cross-contamination** | `pf-` prefix + sha16, never uuid-shaped; SQL-greppable; **A1 REWRITE:** file rows no longer share any table with DB skills (dedicated `project_skill_files` / `project_skill_embeddings`, no FK) — the original shared-table contamination class is closed structurally. **S6 advisory (belt):** if A1 is ever reverted, the `project_id=None` unfiltered embedding scan (`repository.py:1933-1941` feeding `skill_evolution_service.py:1176-1190`) must be guarded before `pf-` vectors enter `skill_embeddings` — moot post-A1, recorded as the revert condition. Opaque-hash cleanup requires the A4.2 gc, not LIKE patterns. Pinned. |
| 9 | **Worktree canon drift** (v1 indexes main_directory) | Documented (Q1); `work_dir` metadata convention is the v2 cure; `root_path` column makes the served canon visible per entry. |
| 10 | **Legacy-schema ingestion** (`.agents/project-skills/bump-version`) | Dual-leg exclusion (Q7/A12): depth-exact structural glob (no `skills/` segment ⇒ never matched) + `schema_version==1` gate; negative fixture pinned. |
| 11 | **Sync file I/O on the event loop** (corpus reads at search time) | All file reads via `asyncio.to_thread` — house lesson from the 2026-10-26/30 event-loop incidents; the corpus read is the hot path. (S3 advisory, routed to phase plans: dedicated bounded executor à la `db_offload` or a pinned concurrency bound; promote to MUST if Phase-0 measures a cold scan >50 ms.) |
| 12 | **Embedding refresh on the search path** (latency on git pull) | Fire-and-forget async refresh; stale embeddings only perturb stage 2 (BM25 always fresh); `embedded_at`/`last_error` visible. **A4 amendment:** refresh follows delete-AFTER-success + single-flight/debounce per pseudo-id — the original delete-then-regenerate would make a stale vector PERMANENTLY absent (the digest-stable access path never retries), and branch toggling must not thrash main-model LLM calls (S1). |
| 13 | **Migration surface** (new tables) | Own `.sql` per table + belt `_ensure_postgres_columns` entries; **operative fresh-PG artifact = pre-`create_all` model registration** (A10-corrected); additive CREATE TABLE class = rollback-safe by precedent (board/notify_audit adjudications); lane delta **+2** (+3 if A2 companion). |
| 14 | **REST contract drift** | **A6:** `/api/skills/search` serves ZERO file skills in v1 (confirm-only resolver; REST-invisibility pin; leader ruling D1) — the "passthrough fuses automatically" claim in the original text is hereby superseded. **S2:** `GET /api/skills` (list) stays DB-only in v1 — the strip mechanism is the `_LIST_SKILL_FIELDS` allowlist (`skill_store_service.py:90`, projection `:516`), not pydantic (zero `response_model` in the router); merging file rows would feed `pf:` ids into FE-built deactivate/share calls that 404. FE untouched (non-goal). |

**Phase-0 evidence still missing (and whether Phase 0 is warranted):**
* Canonical-file counts per project beyond agents-ensemble (are there OTHER projects with `.agents/*/skills/` trees? A read-only sweep over `projects.main_directory` answers it in minutes).
* Cold-scan + stat-gate cost at canon scale (expect ms; measure once to pin the `last_scan_ms` budget).
* Embedding regeneration cost per digest flip (5 skills × ~8 triggers — one measured run sets the async-refresh expectation).
* Live DB census of the 3+1 legacy skills to be shadowed (`9e79ccad`, `fd1e01dc`, `76e3e1a7a`, `08e67030` — existence/generation state), so the P4 retirement at enable-time is a known diff. **A18.4: this census is the FALSIFIER** — the shadowed set must be EXACTLY the 3 known ids; a superset found ⇒ the fusion/shadow split re-enters adjudication (fusion ON, shadowing gated) as a stage-time gate input.
  * **SUPERSEDED (leader adjudication 2026-11-01 — decisions.md Part 10):** the census executed at Phase 5 (2026-11-01) found the `regression-ab-partition-run` name as ONE lineage (gen-0 `978ae5cc` inactive → gen-1 `08a4332f` → gen-2 `fd1e01dc`, unresolved A/B pair `2600329e`, identical content) — adjudicated a KNOWN lineage, correct P4 shadow-by-name behavior; the lineage-aware invariant (STOP only on unknown lineage/name) governs. Active shadow set at promote: `08a4332f` + `fd1e01dc` + `9e79ccad` + `76e3e1a7a`; `blast-radius-leg` has no DB copy.

**Verdict: a LIGHT Phase-0 is warranted** — four read-only measurements, no fixture harness, one session. The architecture above does not depend on their outcomes; they bound the cost claims, pre-answer the P4 retirement census, and arm the A18.4 falsifier for the stage gate.

---

## H. Deferred / v2 split

| Item | Tier | Rationale |
|---|---|---|
| Recursive `loads[]` resolution in injection (depth-bounded, budget-aware) | v2 | v1 keeps gate determinism via mandated recipe-load + name resolution (Q6); recursion adds cycle/budget classes with no consumer beyond convenience. |
| `assets/` serving (e.g. via live-view root) | v2 | `assets[]` empty across the canon — no consumer (verified). |
| Spawn-time `work_dir` metadata plumbing (worktree-first canon by default) | v2 | Resolver hook ships in v1 (Q1); plumbing needs spawn-surface changes with their own gate weight. |
| Explicit file-skill injection in evolution-off deployments (un-gating the injection path) | v2 | Drags `SkillInjectionService`/clone construction out from under the evolution gate — core-seam blast radius with no demonstrated need (Q8b). |
| Cross-project / global file-skill registry share | v2+ | Explicit non-goal; P3 makes it a deliberate design change, not an extension. |
| FE surfacing of `source`/`shadows` tags | v2 | Additive JSON keys are FE-inert today (non-goal: trivially necessary only). |
| Evolution integration for file skills (usage-driven git-PR suggestions) | v2+ | P7 boundary: metrics-only feedback in v1 (Q5); anything that WRITES suggestions must remain outside the evolution lane. |
| `supersedes` auto-deactivation of shadowed DB rows | v2 | v1 shadows at query time (reversible by knob); physical deactivation is a DB write with its own lineage semantics — deliberately not automatic. |

---

## Open questions for the planner/architect

*(Post-fold status 2026-10-31: Q1 of the original three is RESOLVED by architect A9 — the file-only recommendation STANDS, with pinned semantics (see §Q8b cross-reference); remaining open items below.)*

1. ~~**Q8b edge — `skill_search` degraded mode when the DB stack is absent**~~ — **RESOLVED (A9)**: recommendation stood; file-only mode is a pinned third surface (stage-1 file-only BM25, `top_k=max_results`, no stage 2/3, standard shape + `source` + `degraded:"file_only"` marker; view/list serve files; create/fix soft-fail; all four matrix cells pinned against the M0-M11 mode table).
2. **P4 retirement persistence:** v1 shadows at query time; if the user wants the 3 legacy DB rows physically deactivated at enable-time (one `is_active=False` migration-style op with lineage note), that is a deliberate DB write outside this plan's file-source scope — needs an explicit ruling. **A18 adds the operator-facing constraint:** until then, shadow-only retirement is invisible to the REST list/FE Skills page (the 3 legacy rows keep showing active) — part of the dual ceremony disclosure.
3. **`work_dir` metadata convention owner:** which spawner (leader `spawn_instance`? task `context`?) stamps `instance_metadata["work_dir"]` in v2 — a one-line decision that belongs to the instance-lifecycle owners, not this lane. (Related parked v2 item per leader ruling D1: auth-principal→project binding if REST file-skill fusion is ever wanted — registered in the plan's deferred register.)
