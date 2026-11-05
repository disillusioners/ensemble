# flag-dispositions.md — Memoir Store v1 — Phase B dispositions for the 14 design.md §14 flags

**Lane:** `feature/ari-memoir-store` @ worktree `/home/nea/ensemble-src-wt-memoir-store`, plan base `0d2f31dde` (tree content == `1f6898ae2`).
**Status:** PHASE B — PROPOSED DISPOSITIONS. Every disposition below is marked **"PROPOSED — architect adjudicates"** per `decisions.md` §2/P-3. None of this text is a ruling; none of it modifies `design.md`, `decisions.md`, or `roadmap.md` (binding inputs are read-only for Phase B).
**Grep provenance:** every code anchor cited below was re-verified in this worktree on 2026-11-05 (house rule P-7 — recorded line numbers are leads; grep at use time). Verified values are recorded inline; drift notes where they occur.

**Summary:** 14 flags — **14 PROPOSED / 0 OPEN-FOR-ARCHITECT**. Each carries rationale + failure-mode analysis. Flags #3 and #9 (user-experience behavior) carry extended multi-paragraph analysis per the commission. 13 dispositions earn a `decisions.md` §4 adjudication-log row once adjudicated (flag #4 is P-7 anchor drift — exempt per `decisions.md` §4's own preamble: "Anchor drift does not belong here — it is a re-grep, not a deviation"). Draft §4 row text is pre-staged in §16 below for the architect's lane to lift verbatim; Phase B does NOT write to `decisions.md`.

---

## Flag #1 — Exact keepsake pin-cap integer — PROPOSED — architect adjudicates

**Disposition: cap = 12 pins (count) + dual admission (count AND stable-zone token budget), enforced at pin time.**

The count ceiling is **12** (the design's "~12"). But a count cap alone does NOT make the stable zone's "never truncated" guarantee true: a keepsake is an episode whose body may legally run to the house request-length cap (~2000 words ≈ ~2,800 tokens); 12 × 2,800 ≈ 33,600 tokens ≫ the 4,000-token keepsake zone. The guarantee is therefore made true by construction with a **second admission bound**: `memoir_pin` (and `memoir_write(keepsake=true)`) REJECT when either

1. `current_pin_count >= 12`, or
2. `rendered_tokens(existing keepsakes) + rendered_tokens(candidate) > 4000` (tokens estimated with the house estimator, `daemon/loader.py:1027 estimate_tokens`, tiktoken cl100k — the same estimator `cap_snapshot_digest_for_injection` uses at `daemon/services/context_messages.py:275-287`).

The rejection error names the binding constraint and lists current pins (id, title, pinned_by, rendered token size) — the reject-don't-evict shape of `daemon/tools/critical_notes.py` (`_MAX_ENTRIES=200` at `:70`, cap check + candidate naming at `:537-546`, verified). The tool NEVER evicts or demotes an existing pin to make room (DD-3).

**Briefing arithmetic that justifies 12:** the stable keepsake zone is 4,000 tokens; 4,000 / 12 ≈ **333 tokens per keepsake average** — enough for a title + pin reason + a ~250-word body, i.e. the natural size of a "this must never be lost" moment. At 12 pins the zone is comfortably full at typical keepsake sizes; at more pins the per-pin budget stops being a keepsake and starts being a sticky note. 12 also matches the design's own "~12" so this fills the delegated blank rather than re-choosing it.

**Failure modes:** (a) pathological case — one 2,800-token episode pinned first leaves budget for only ~1 more normal keepsake; the dual admission rejects the third with the arithmetic in the error; the Emperor can unpin the giant or accept two keepsakes. Honest, visible, reversible. (b) A user WANTS a long episode as a keepsake and hits the budget bound — the error suggests `memoir_recall`/timeline as the access path and unpin-then-repin-shorter as the remedy. No silent path exists.

**Detail ref:** phase2-plan.md (T-cap), phase4-plan.md (pin tool), flag #3 (budget model).

---

## Flag #2 — Pin attribution mechanism (`pinned_by ∈ {empress, emperor}`) — PROPOSED — architect adjudicates

**Disposition: an explicit REQUIRED `actor: Literal["empress","emperor"]` parameter on `memoir_pin` (and on the keepsake-at-write path of `memoir_write`), with a tool-contract rule for which value to assert, and `pinned_by` rendered visibly in the briefing so mis-attribution is observable at every wake.**

**Why not turn provenance.** Deriving attribution from the turn's origin (the `notify_user` D1 original_source machinery) answers "was this turn user-originated", NOT "whose will is this pin". The common case is mixed: on an Emperor-originated turn, Ari routinely pins something of her own judgment in passing. Derivation would mis-attribute exactly those mixed sessions — the most common shape — while feeling authoritative. It also breaks for deferred execution ("pin that next time it comes up").

**Why not a separate tool path.** A second tool (`memoir_pin_as_emperor`) doubles the surface for no governance gain: the model still chooses which to call, so the assertion problem is unchanged; only the spelling differs. The five-tool surface is blessed (§5.4); a sixth tool is a blessed-text change that buys nothing.

**The proposed rule.** `actor` is required, no default. Tool contract (in the tool description and `agents/ari/tools_note.md` doctrine): assert `emperor` ONLY when directly executing an explicit pin/unpin instruction from the Emperor in the current exchange; assert `empress` for Ari's own judgment (including acting on standing preferences). `memoir_unpin` carries the same parameter for symmetric record-keeping (unpin provenance is governance too).

**Failure mode (named by the flag): mis-attribution corrupts the governance record.** Mitigations, in layers: (1) the briefing's keepsake zone renders `pinned_by` + `pinned_reason` for every pin — both principals see every attribution at every wake, so a wrong claim surfaces at the next session, not years later; (2) the repository CHECK-constrains `pinned_by ∈ {empress, emperor}` (schema-level vocabulary guard); (3) attribution is immutable per pin-set (setting over an existing pin records the NEW actor — re-pin is a governance act and is logged as such in the tool result). Residual risk — a persistent, unchallenged mis-attribution — is accepted as bounded: the record is reviewable, and R2's legitimacy ("both paths are legitimate; the column records which") is preserved.

**Detail ref:** phase1-plan.md (schema), phase4-plan.md (tools + doctrine).

---

## Flag #3 — Briefing budget arithmetic: two unit systems — PROPOSED — architect adjudicates

This flag touches user-visible behavior every session; both sides are argued before the recommendation.

**The tension, precisely.** Zone caps are in **tokens** (persona ≤2,000 + keepsakes ≤4,000 stable; dynamic ≤3,000) while the hard section cap is **~12k chars** (`design.md` §8.2, anchored on `_CRITICAL_NOTES_DEFAULT_SECTION_CHAR_CAP = 12000`, verified at `daemon/services/context_messages.py:707`). English averages ~4 chars/token, so the design's own token ceilings imply a stable zone of up to 6,000 tokens ≈ **24k chars** — the stable zone AT ITS OWN CEILINGS already exceeds the 12k-char cap. The two systems cannot both bind; the design explicitly delegates the reconciliation ("their reconciliation is explicitly a Phase B duty").

**Option A — chars-truth (the 12k cap governs; token numbers are descriptive).** To make the stable zone fit 12k chars (~3,000 tokens total), the keepsake budget must shrink to ~1,000 tokens (≈83 tokens per pin at 12 pins — one sentence each) and the persona to ~1,500 tokens, or the pin cap must fall to ~4. This preserves the critical-notes anchor number but starves the zones that carry the design's actual content — a persona that is "who we are" does not fit 1,500 tokens alongside a gutted keepsake shelf. The "~12k" figure was an ANCHOR on the critical-notes budget family (an order-of-magnitude cousin), not a blessed byte ceiling on this new block; §8.2 itself marks it "~" (approximate).

**Option B — tokens-truth for zones; a derived absolute char ceiling (RECOMMENDED).** Tokens are the planning unit everywhere (the house estimator `estimate_tokens`, tiktoken cl100k, is already the budget instrument for snapshot digests at `context_messages.py:275-287`). The blessed token ceilings stand unchanged (persona 2,000 / keepsakes 4,000 / dynamic 3,000). Because the stable zone is NEVER truncated, it must be pre-bounded so it can never overflow a final ceiling — which therefore must be sized for the stable zone's own worst case: persona 2,000 + keepsakes 4,000 = 6,000 tokens ≈ 24k chars. **Hard section cap: 24,000 chars absolute** (final renderer guard, tail-drops the dynamic zone first, with a one-line `[briefing: dynamic zone elided — budget]` disclosure). The 12k figure is honored as the NOMINAL size (typical fill: persona ~1,200 tokens + a half-full keepsake shelf + dynamic zone ≈ 10–14k chars); 24k is the never-exceeded ceiling that makes "never truncated" arithmetically true rather than aspirational.

**The normalized budget model (one paragraph, binding for Phase C):**
1. **Unit:** tokens for all admission decisions (estimated chars-side via `estimate_tokens`; no tokenizer call may run per-turn in the briefing builder beyond the render pass — one estimate per rendered section).
2. **Stable zone:** pre-bounded by ADMISSION, never by truncation — persona content ≤2,000 tokens (enforced when a persona draft is approved; the §11 seed satisfies it), keepsakes ≤4,000 tokens TOTAL (enforced at pin admission, flag #1). The stable zone MAY exceed 12k chars at full fill; it may never exceed ~24k chars because its parts are admitted under token ceilings that sum below it.
3. **Dynamic zone:** rendered to ≤3,000 tokens, **oldest dropped first** under pressure (episodes ordered by `created_at`; "since we last met" naturally favors the latest ~5).
4. **Which binds on overflow:** the 24,000-char absolute section cap binds at render; within it, zone token budgets bind; the stable zone is exempt from render-time truncation by construction (its budgets were spent at admission time).
5. **Kill-switch/empty-store:** no block, no budget.

**Failure modes:** (a) a pathological approved persona at exactly 2,000 tokens + full keepsake shelf leaves ~0 chars for the dynamic zone — the disclosure line names it and `memoir_recall` remains the access path; honest degradation, never data loss. (b) Estimator drift (cl100k vs a future encoding) changes rendered sizes by ±10% — the 24k ceiling carries ~15% headroom over the 6,000-token worst case (6,000 × 4 = 24,000 exactly; headroom comes from real English averaging <4 chars/token and titles/pins being short). A dedicated pin asserts the arithmetic.

**Detail ref:** phase5-plan.md (budget engine + pins); flag #1 (admission).

---

## Flag #4 — inner_soul anchor drift (lead-vs-truth) — PROPOSED (re-grep note; NO §4 row) — architect adjudicates

**Disposition: confirmed by fresh grep — the implementation target is the `_should_redirect_to_rag` call sites, NOT the `:232-278` classification block. This is P-7 anchor drift (re-grep + note), not a deviation; no `decisions.md` §4 row is owed.**

Verified in this worktree (2026-11-05): `_RAG_TARGETS` at `inner_soul.py:303`; `_KNOWLEDGE_CLASSIFICATIONS` at `:311-313`; `_should_redirect_to_rag(...)` at `:316-362` (unconditional `project_knowledge` return at `:352`; all-targets-∈-`_RAG_TARGETS` check at `:355`; class-∈-classifications check at `:359`); `_format_rag_redirect` at `:365`; `_classify_request` at `:1022`. The `:232-278` region is the `project_knowledge` classification block (its `targets` are `["REJECT"]` at `:276`) — classification happens there, but the REDIRECT DECISION lives in the predicate, and the predicate is consumed at exactly TWO call sites: `:716` (single-request flow) and `:777` (compound-request flow), both inside the `create_inner_soul_tool` closure (factory signature at `:631-637` — `manager, agent_id, instance_id, version_tag`) where the **factory-captured `agent_id` is in scope** (satisfying §6.2's "evaluated with the agent identity already in scope" and DD-13's factory-captured-identity rule). Phase C implements the ari-only branch at those two call sites (one shared helper, both sites edited identically); it re-greps the anchors first per P-7.

**Detail ref:** phase6-plan.md.

---

## Flag #5 — `stars` normalization (silent in v1) — PROPOSED — architect adjudicates

**Disposition: awareness-only, exactly as the commission frames it — v1 stores stars VERBATIM (no normalization build), ships the v2 normalization RULE as documentation now, and adds input BOUNDS (not normalization) at write time.**

(a) **Store verbatim.** "the Emperor" and "the emperor" remain two distinct strings in v1 — the corpus is small, the merge is v2's `memoir_star` alias-table job (roadmap v2; DD-19). (b) **The rule, written now** (module docstring + `agents/ari/tools_note.md` + this plan): *the v2 normalization pass will casefold, trim whitespace, collapse internal whitespace, and dedupe per-write; alias merging ("the Emperor" / "my dear" / real name → one star) is the v2 star-table's identity problem, NOT a string-transform problem — v1 must therefore capture the mention EXACTLY as the writer phrased it, because alias evidence lives in the phrasing.* (c) **Input bounds (hygiene, not normalization):** `memoir_write(stars=...)` accepts at most 16 entries, each ≤120 chars after trim; out-of-bound entries are rejected with a typed error at write time. This is a request-shape bound in the family of the ~2,000-word body cap — it changes no string's content, so it is not normalization.

**Failure mode:** the flag's own — "two writes of 'the Emperor'/'the emperor' become two stars in v2" — is ACCEPTED and VISIBLE (the v2 rule document says so; the corpus stays greppable), not silent. Deferring the rule was the risk; the rule is now written down.

**Detail ref:** phase2-plan.md (write path bounds), phase4-plan.md (tool schema).

---

## Flag #6 — Episode mutability partition — PROPOSED — architect adjudicates

**Disposition: an explicit three-way partition, enforced structurally — the repository exposes NO general-purpose episode-update method; each mutable column family has exactly ONE named writer method.**

| Class | Columns | Rule |
|---|---|---|
| **IMMUTABLE — the record** | `id`, `agent_id`, `title`, `body`, `created_at`, `event_at`, `importance`, `stars` | written ONCE at INSERT (`insert_episode`); no UPDATE path exists anywhere; corrections are new episodes + `superseded_by` (DD-8) |
| **MUTABLE — operational, named writers** | `last_accessed_at`, `access_count` → `mark_recalled(...)` — the ONLY writer, called solely by the recall service. `is_keepsake`, `pinned_by`, `pinned_reason`, `pinned_at` → `set_pin(...)` / `clear_pin(...)` — the ONLY writers (tool layer). `embedding`, `embedding_model`, `embedding_dims`, `embedded_at`, `embedding_attempts`, `embedding_error` → `store_embedding(...)` / `record_embedding_attempt(...)` — the ONLY writers (embedding worker). `superseded_by` → `mark_superseded(...)` — defined for completeness; **NOTHING calls it in v1** (no supersede tool ships; the column exists so v2/v3 behavior needs no migration) |
| **Derived — DB-side** | `tsv` (PG generated column) | maintained by PostgreSQL on INSERT/UPDATE of `title`/`body`; no application writer |

**Enforcement + proof:** (1) structural — the repository's public surface simply lacks an episode-body update method, so "a future reviewer allows a body edit" requires ADDING a method, which review will see; (2) pin pack — a grep pin asserts no `UPDATE memoir_episodes SET` statement in the tree touches `body`, `title`, `importance`, or `stars`, plus a repository-API surface test asserting the exact method set; (3) the recall-stats and pin writers use column-targeted UPDATE statements only.

**Detail ref:** phase2-plan.md (repository contract), phase7-plan.md (pins).

---

## Flag #7 — Embedding worker identity + durability — PROPOSED — architect adjudicates

**Disposition: a dedicated in-daemon asyncio background task started in the `api.py` lifespan (the house `asyncio.create_task` + strong-ref + cancel/await-on-shutdown pattern — e.g. the readiness refresher at `daemon/api.py:2772-2777` and the bash-reconcile task at `:940-975`), with `embedding IS NULL` AS the queue, bounded retries, boot catch-up by construction, and a greppable per-tick counter line.**

* **Owner — why NOT the job queue / a generic sweep:** the job queue is the agent-work dispatch machinery (admission states, watchers, mission receipts — user-visible surfaces); an internal index-maintenance loop is not agent work and would mint job-shaped noise. A generic sweep service is the wrong shape too: embedding wants short-latency service (semantic recall within ~30s of write) plus catch-up, which a dedicated periodic task gives in ~80 lines. Precedent: every internal maintenance loop in this codebase (readiness refresher, drift reconciler, watcher watchdog) is a lifespan task.
* **Pending-rows representation:** `embedding IS NULL AND embedded_at IS NULL` IS the queue — no side table, no outbox; a partial index (`WHERE embedding IS NULL`) makes the scan cheap. Restart durability is automatic: the first tick after any restart re-scans NULLs — **boot catch-up by construction**.
* **Loop shape:** tick every `memoir.embedding_tick_s` (default 30s, config block); per tick scan up to `embedding_batch_cap` (default 8) pending rows; embed via the OpenAI-compatible `/embeddings` endpoint (the `skill_embedding_service` client shape — sync client behind `asyncio.to_thread`, `embeddings.create(model=..., input=...)` at `daemon/services/skill_embedding_service.py:204`; model/base/key from the shared `EMBEDDING_*` config family, `daemon/config.py:4374-4376`); write back WITH per-row identity (`embedding`, `embedding_model`, `embedding_dims`, `embedded_at` — the `critical_note_embeddings` identity-column precedent at `daemon/repositories/project/models.py:293-347`).
* **Retry/backoff:** two extra columns — `embedding_attempts INTEGER NOT NULL DEFAULT 0`, `embedding_error TEXT NULL`. Per row: attempt → on failure increment attempts + record error; after **5 attempts** the row goes TERMINAL (stays lexical-only forever; `embedding_error` keeps the reason; a future backfill resets by nulling `embedded_at`... it is NULL already — terminal means attempts ≥ 5 and the worker's scan skips it). Lane-level latch: after 3 consecutive all-fail ticks (lane down), the task drops to slow mode (300s ticks) until the first success — graceful degradation per P3/DD-20 (writes NEVER block; recall degrades to lexical-only, never data loss).
* **Observability:** one structured log line per tick — `[MemoirEmbed] pending=N embedded=K errors=E terminal=M tick_ms=T slow_mode=bool` — greppable, pack-asserted; the unit pack proves the line exists and the counters are honest (mutation leg). A central-metrics family is an optional follow-up (routed, not built).
* **Kill-switch:** `ENSEMBLE_MEMOIR_ENABLED` OFF ⇒ the task is never started (flag #13).

**Failure modes:** dropped embedding (the flag's fear) is impossible to MISS — pending N is in every tick line and terminal M is visible; a crash mid-batch is safe (each row's write is independent; un-written rows are simply still NULL); duplicate embedding after a crash-between-compute-and-write is benign (idempotent overwrite of the same content).

**Detail ref:** phase3-plan.md.

---

## Flag #8 — Persona version table vs the prompt_version human lane — PROPOSED — architect adjudicates

**Disposition: MIRROR the pattern, do not reuse the table. `memoir_persona_versions` carries its own `status` state machine with the SAME transition vocabulary as `prompt_versions` (`draft→pending_approval→approved|rejected|draft`, verified at `daemon/repositories/prompt_version/repository.py:217-221`), enforced in the memoir repository. The §11 seed is born `approved` with `source='seed'`. v1 ships NO persona-edit write path beyond the seed** (the blessed five-tool surface §5.4 contains no persona tool) — the first real edit path (v2's governed persona, or an operator/REST human-lane route if the Emperor wants in-v1 edits) inherits a table whose guarantees are already real.

**Why mirror, not reuse:** `prompt_versions` is coupled to prompt-cache invalidation, selection machinery, and the prompt-versioning UI — the persona is Ari's SELF, not a prompt artifact; entangling them means persona edits trigger prompt-cache semantics and appear in prompt-version surfaces. The pattern's VALUE is the GATE (DD-9: nothing the live model writes about its durable self takes effect without human approval), and a mirrored transition frozenset + repository-level enforcement + a pin test keeps that guarantee real without the coupling. The human-lane mechanics (`auth_method=='session'` + `prompts:approve` + bearer⇒403, verified at `daemon/routers/prompts.py:617/:640/:664/:681`) belong to whichever surface the first edit path ships — recorded here so that lane inherits the discipline explicitly.

**Conditional sub-question for the architect (does not block v1):** if in-v1 persona edits are wanted, adjudicate the surface — a 6th tool (blessed-text change to §5.4) vs a REST human-lane route (new surface, mirrors the prompts approve/reject discipline). Default: neither ships in v1; the seed is the persona.

**Failure mode:** a future lane adds an edit path that bypasses the mirror (writes `approved` directly). Mitigation: the repository's transition guard is the ONLY path to `approved`, and a pin asserts no code calls it outside the approve seam; the seed's `source='seed'` provenance makes the day-one row auditable.

**Detail ref:** phase1-plan.md (schema), phase2-plan.md (repository state machine + seed).

---

## Flag #9 — Briefing once-per-instance vs long-lived instances — PROPOSED — architect adjudicates

This flag touches the most predictable user-experience complaint of the whole feature; both sides are argued in full.

**The blessed position (DD-11).** Once per instance, on the `project_injected` precedent (verified: fresh-read gate `daemon/graph.py:952-982` read at `:938`; flag write `daemon/services/instance_messaging.py:4241`): "a briefing is a wake, not a chant". Re-injecting a ~2,000-token persona plus a dozen keepsakes every turn is pure token tax with zero new information — the turn's own history already carries the session's memory.

**The complaint, taken seriously.** "Why doesn't she remember an hour ago?" has three distinct cases, and only one is real:
1. *Same session, recent exchange* — the conversation history IS the memory; nothing is missing. Not a defect.
2. *Same session, post-compaction* — a long-lived instance that compacted away its early turns loses conversation continuity. The memoir briefing block is compaction-HOISTED (context_kind survives, the snapshot-digest precedent at `context_messages.py:130/:388`), so the STABLE zone (who we are + all keepsakes) survives compaction; what goes stale is only the dynamic "since we last met" window. Partial, bounded, and disclosed below.
3. *Cross-session* — a NEW instance gets the briefing on its first turn (the flag gate is per-instance, not per-agent-day). This case — the common one for a front-door agent fielding scheduled wakes and fresh commissions — is exactly what the briefing serves.

**The v1 disposition.** Keep once-per-instance (DD-11 is binding; the flag asks for a decision WITH rationale, not a redesign). Two accommodations ship in v1: (a) the briefing block's closing line carries the affordance — memory remains available mid-session via `memoir_recall(query)` and `memoir_timeline(...)` — so the model (and through it, the user) is TOLD the memory is queryable rather than assuming the briefing was everything; (b) the once-flag is per-instance and read fresh every turn (the `project_injected` mechanics), so a revived/reused instance that has somehow lost the flag re-receives the briefing — no stranding.

**The designed future answer, cross-checked against landmine L4 (as the flag requires).** The roadmap's v2 item "per-turn wake-refresh on alarm wakes" is the real fix for case 2's stale-dynamic-window and for alarm wakes that resume a long-lived instance. Landmine L4 (verified: `scheduler` deliberately NOT in `_INTERNAL_STAMPED_SOURCE_PREFIXES`, `instance_messaging.py:422-427`; adapter comment "INERT BY DEFAULT (standing product ruling: lane 9 stays BARE)" at `daemon/sources/adapters/scheduler.py:2700-2701`) means the v2 refresh MUST NOT be built by "fixing" the bare-HUMAN stamp — the correct v2 lane is an emission-time context refresh keyed on wake events at the assembly seam, chosen consciously. This plan does NOT touch the stamp (explicitly forbidden), and records the constraint so the v2 lane inherits it.

**Failure modes:** (a) a user hits case 2 and is dissatisfied — remediation is one message ("start a fresh session" re-triggers the wake) and the v2 item is already roadmap'd; (b) the affordance line is dropped in implementation — pinned by a byte-assertion in the briefing pack.

**Detail ref:** phase5-plan.md.

---

## Flag #10 — `memoir_timeline(q?)` semantics — PROPOSED — architect adjudicates

**Disposition: filter-then-rank.** `from`/`to` (inclusive, on `event_at`, indexed range scan) filter the candidate set; `q` then applies the LEXICAL-ONLY ranker over that set (PG: `ts_rank_cd(plainto_tsquery('english', q), tsv)`; SQLite/dev: the Python BM25 house helper) and the output is rank-ordered within the window; with `q` absent the output is `event_at DESC` chronological. **NO cosine, NO RRF fusion, NO recency prior, NO keepsake prioritization** in timeline — it is a time-travel VIEW (roadmap v1: "Time-travel over `event_at`"), not a relevance search: the recency prior would actively distort a historical window ("what was true in March" must not be demoted for being March), and semantic ranking answers "what is this about", not "what happened then". Timeline does NOT update access stats (DD-12: only explicit `memoir_recall` writes stats).

**Why not independent-BM25-as-a-second-recall:** with both `q` and no bounds the tool degrades gracefully to a pure lexical ranked list — the SAME code path, no second machinery; with bounds present it is a filter. One ranker, one semantics, two entry shapes. The user-facing contract (tool description) states it plainly: "timeline is chronology with optional lexical narrowing; recall is relevance search."

**Vocabulary note (shared with the recall ranker):** stock PostgreSQL has no BM25; the design's "BM25(tsv)" is implemented as lexical ranking over the `tsv` generated column via **`ts_rank_cd`** (coverage-density weighting — the closest stock-PG approximation of BM25's term-frequency-with-proximity spirit), and **RRF consumes only ORDINAL RANKS**, so the absolute score family is immaterial to fusion (this is precisely why RRF was chosen). SQLite/dev runs the house Python BM25 (`skill_search_service._bm25_score`, the `critical_notes_selector` precedent at `:12-20`) — the driver split is invisible above the ranker interface.

**Failure mode:** users treat timeline as recall and wonder why semantic matches are missing — the tool description's contract line (above) is the mitigation, pinned in the tools pack.

**Detail ref:** phase2-plan.md.

---

## Flag #11 — Recall result budget — PROPOSED — architect adjudicates

**Disposition: a concrete two-axis cap harmonized with the briefing budget model and the metrics-read M-1 output-budget doctrine (shipped projection, never raw rows).**

* **`k`:** default **5**, hard max **20** (out-of-range → typed refusal, not silent clamp — the notify_user bounds discipline).
* **Per-episode render:** `title` + `event_at` (date) + `importance` + `pinned` flag + `body` clamped to **1,200 tokens** per episode, truncation marked `[…body truncated, N chars total]` (the `_TASK_CONTEXT_MAX_CHARS` truncation-marker idiom).
* **Total envelope:** **≤6,000 tokens** (same estimator as the briefing, `estimate_tokens`) — one full briefing's worth of output is the ceiling, so a single recall call can never out-spend the briefing's whole arithmetic. When the envelope overflows, lowest-ranked episodes are DROPPED first (ranked-drop, not uniform squeeze — the head results stay whole), and the output closes with a count + hint line: `[k episodes; M dropped for budget — narrow the query or use memoir_timeline]` (the metrics-read composable-hints shape).
* **Metadata footer:** every recall result carries rank basis (lexical/semantic/both), how many candidates lacked embeddings (degradation visibility), and whether stats were updated.

**Failure mode (the flag's own):** unbudgeted recall defeats the briefing arithmetic in one call — closed by construction (the envelope is enforced in the tool body with a final re-check before return, mutation-pinned; the metrics-read lane's C1 regression class — trim must measure the SHIPPED projection, not raw rows — is the named trap, and the pin asserts the shipped bytes).

**Detail ref:** phase2-plan.md (service), phase4-plan.md (tool body).

---

## Flag #12 — Tool-count wording / pin-ceremony census numbers — PROPOSED — architect adjudicates

**Disposition: the pin numbers are fixed by fresh census (2026-11-05): the category is `memoir`, PRIVILEGED entry **#18** (count 17 → 18); the category carries **5 tools** (`memoir_write`, `memoir_pin`, `memoir_unpin`, `memoir_recall`, `memoir_timeline`) across **4 families** (pin/unpin share one); the pin assertions encode 18 / exact-18-set / 5-tool-names.**

Verified census at plan time: `PRIVILEGED_TOOL_CATEGORIES` currently has **17** members (`daemon/tools/_tool_registry.py:367-651`), with `notify-user` #16 and `metrics-read` #17. Count pins asserting `len == 17` exist at **6 sites** (`tests/unit/test_board_registration.py:222`, `test_wedge_capture_registration.py:180`, `test_self_alarm_registration.py:218`, `test_notify_user_registration.py:182`, `test_metrics_read_registration.py:221`, `test_instance_ops_registration.py:209`); exact-set pins at **5 sites** (`tests/integration/test_service_tool_flag_off_byte_identical.py:834`, `tests/integration/test_maintenancer_spawn_resolves_tools.py:382`, `tests/unit/tools/test_secrets_registration.py:226`, `tests/unit/tools/test_upgrade_registration.py:152`, `tests/unit/tools/test_attestation_registration.py:192`). ALL are bumped to 18 / the 18-entry exact set **in ONE commit** (the v0.26.2 ruling) together with the new `tests/unit/test_memoir_registration.py` (the `test_notify_user_registration.py` (a)–(j) shape, 18-flavored). The 5 tool names land in `DYNAMIC_TOOL_NAMES` + `KNOWN_TOOL_NAMES` (factory-built tools). Prompt-artifact baseline re-stamp rides the same ceremony commit (`agents/.prompt-artifacts.sha256`, currently 161 lines — ari `tools_note.md` edit + `meta.json` allow entry change the hashes, count stays 161).

**Detail ref:** phase4-plan.md (the ceremony task enumerates every file).

---

## Flag #13 — Kill-switch OFF semantics + env polarity — PROPOSED — architect adjudicates

**Disposition: OFF is the whole-personality lever — tools refuse with a typed refusal, the briefing is NOT built, the inner_soul ari branch emits a LOUD not-recorded notice (never a silent drop), the embedding worker is not started; the DATA is untouched (rows stay; no other read path exists in v1, which is stated rather than hidden). Polarity: tristate explicit-string, notify-user family, default ON via config.**

* **Polarity (one authority, the `notify_user` knob-resolution family):** `ENSEMBLE_MEMOIR_ENABLED` (or `memoir.enabled` in config.yaml) accepts explicit `true/1/yes/on` and `false/0/no/off` (case-insensitive); **UNSET resolves to the config default `true`** (the feature ships live — an unset env var is a DOCUMENTED default, not an indeterminate value); a present-but-unparseable value is INDETERMINATE and **fails closed: OFF + a boot WARNING naming the bad value and the exact env var**. This satisfies DD-15's letter — "fail-closed fixes how an indeterminate value is treated (it must not silently enable the surface)" — while shipping the memory live by default (the commission's product intent: the memory IS the feature; a default-dark memory would ship the leak it exists to cure).
* **What OFF kills (proposed):** tools refuse `memoir_disabled` (typed refusal, notify_user's `notify_disabled` shape at `:725-731`); the briefing builder returns `None` (no block, no once-flag write — so re-enabling later delivers the briefing on the instance's next turn... nuance: the flag is written only on successful injection; with the block suppressed the flag stays unset, so a later enable injects on the next turn. State this as intended); the embedding worker is not started (pending rows simply remain NULL — P3 degradation, not loss); the inner_soul ari branch is still TAKEN (so the content does not fall back into the RAG void) but returns the loud notice: `memory recording is disabled (ENSEMBLE_MEMOIR_ENABLED) — this content was NOT recorded` — visible in the tool result, never silent.
* **Promote-time consideration:** OFF is a clean rollback-adjacent lever — flipping it off neutralizes the whole surface without touching the DB (rollback adjudication in phase8 references it).

**Failure mode:** operator typos the value (`ENSBLE_…`/`treu`) — the misspelled var is simply absent (unset → default ON) while a present-garbage value warns loudly at boot and fails closed; the WARNING names both the value and the canonical spelling. The residual ambiguity (typo-in-name = silently default) is the universal env-var hazard, disclosed here.

**Detail ref:** phase4-plan.md (knob module), phase5/6/3 (consumers).

---

## Flag #14 — PG materialization path — PROPOSED — architect adjudicates

**Disposition: three coordinated artifacts, each named, per the verified house mechanics.**

1. **The `.sql` file** (`daemon/migrations/versions/2026MMDD_HHMMSS_create_memoir_tables.sql`, prefix minted at implementation, uniqueness-checked against the 119-file census): **SQLite-path DDL + the permanent audit record**, four `CREATE TABLE IF NOT EXISTS` (the `20261020_060000_create_board_tables.sql` multi-table single-file precedent; the PSL exemplar `20261031_120000` carries the IF-NOT-EXISTS discipline, the rollback-posture header, and the runtime-splitter semicolon rule). It is **SQLite-valid only**: `stars` as TEXT/JSON, **no `tsvector`, no ARRAY** — because the runner applies this file on SQLite at every boot (`daemon/migrations/runner.py` SQLite-only, PG early-return; Migration Runner blueprint) and SQLite has neither type.
2. **PG tables:** `SQLModel.metadata.create_all()` from the model registration import block in `daemon/manager.py` immediately before the `create_all` call (verified site: the board/bash-spawn/PSL registration block at `:920-975`, `SQLModel.metadata.create_all(self._engine)` at `:975`) — `from .repositories.memoir.models import MemoirEpisode, MemoirPersona, MemoirPersonaVersion, MemoirSynthesis # noqa: F401`. The implementer must NOT assume the `.sql` runs on PG (it does not) and must NOT omit this registration (create_all only lands REGISTERED models).
3. **PG `tsv` column + GIN index:** a PG-only idempotent ensure (the raw-DDL ensure family verified at `daemon/manager.py:9500-9522`, the `_ensure_postgres_columns` belt): `ALTER TABLE memoir_episodes ADD COLUMN IF NOT EXISTS tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(title,'') || ' ' || body)) STORED;` + `CREATE INDEX IF NOT EXISTS idx_memoir_episodes_tsv ON memoir_episodes USING GIN (tsv);`. The `tsv` column deliberately lives in NO SQLModel class (a `Computed` tsvector would break SQLite `create_all`); lexical queries reach it via `text()` SQL only. Alternative REJECTED for the record: a dialect-conditional ORM column (`Computed` + variant) — SQLAlchemy cannot vary a STORED expression per dialect; conditional model surgery at boot is fragile and precedentless.

**Persona seed shipping (the flag's explicit question) — PROPOSED: boot-time idempotent ensure, NOT a migration-carried INSERT.** Trade-off stated: the migration runner is SQLite-only — a migration-carried INSERT would seed SQLite and **silently never seed PG** (the operative path): an attractive-looking mechanism that is a defect for PG. The boot ensure (repository init: get-or-create an `approved` persona version with `source='seed'`) runs on BOTH drivers, is idempotent, and covers restarts; its cost is that the seed text lives in a code constant (`MEMOIR_PERSONA_SEED`, `daemon/repositories/memoir/seed.py`) rather than SQL — mitigated by a pin test asserting the constant is byte-identical to `design.md` §11 (the fenced block, verbatim) and by provenance (`source='seed'` on the row). The `.sql` stays pure DDL (audit record), matching every recent house lane.

**Detail ref:** phase1-plan.md (the phase owns all three artifacts + the pin).

---

## §15 — Disposition → §4 adjudication-row requirement

Per `decisions.md` §4 (resolving a §14 item "is still worth a row so the number has provenance") — 13 of the 14 dispositions, **if accepted**, require a §4 row: **#1, #2, #3, #5, #6, #7, #8, #9, #10, #11, #12, #13, #14**. Flag **#4 requires NO row** (P-7 anchor drift — `decisions.md` §4's preamble explicitly excludes it; the re-grep note lives in this file and phase6-plan.md).

## §16 — Pre-staged DRAFT §4 rows (for the architect's lane to lift; Phase B does NOT write to decisions.md)

> Rows below are DRAFTS. The architect edits the Adjudication column to the actual ruling (or rejects/changes the disposition) before the row lands; Date/Links are filled at adjudication time.

| # | Deviation (design says / reality forces) | Adjudication (DRAFT — architect) | Links |
|---|---|---|---|
| D1 | design §4.2 "HARD CAP ~12 pins" — integer delegated | Cap = 12 count + dual admission (count AND 4,000-token keepsake zone budget, house estimator); reject-don't-evict names the binding constraint | design §4.2/§14-1; flag-dispositions #1; phase2/4 |
| D2 | design §4.2/R2 — attribution mechanism for `pinned_by` unspecified | Required `actor` parameter (Literal empress/emperor) on pin/unpin/keepsake-at-write + visible `pinned_by` render in briefing; turn-provenance and separate-tool rejected (mis-attribution in mixed sessions / surface duplication) | design §14-2; flag-dispositions #2; phase4 |
| D3 | design §8.2 — token zone caps vs ~12k char section cap: two unit systems, reconciliation delegated | Tokens are the planning unit (house estimator); stable zone pre-bounded by admission (persona ≤2,000 / keepsakes ≤4,000), dynamic ≤3,000 oldest-first; hard absolute section cap set at 24,000 chars so the stable zone can never violate it; ~12k honored as nominal size | design §8.2/§14-3; flag-dispositions #3; phase5 |
| D5 | design §14-5 — stars normalization silent | Awareness-only: stored verbatim; v2 normalization rule documented now (casefold/trim/dedupe; alias merge is v2 star-table identity); input bounds (≤16 entries × ≤120 chars) are hygiene, not normalization | design §9/§14-5; flag-dispositions #5; phase2/4 |
| D6 | design §4.1 "immutable" vs mutable operational columns | Mutability partition: record columns written once at INSERT; operational columns each have exactly ONE named repository writer; no general update method exists; grep + surface pins | design §14-6; flag-dispositions #6; phase2/7 |
| D7 | design §4.1/§3 — "background worker (async)" owner unspecified | Dedicated lifespan asyncio task; `embedding IS NULL` is the queue; attempts/error columns with 5-attempt terminal; lane-down latch to 300s; boot catch-up by construction; per-tick counter log line | design §14-7; flag-dispositions #7; phase3 |
| D8 | design §4.3 — persona approval lane: reuse vs mirror unspecified | MIRRORED transition vocabulary in `memoir_persona_versions` (no prompt_versions table reuse — wrong artifact family, cache/UI coupling); seed born approved `source='seed'`; NO v1 edit path (5-tool surface is binding); conditional: if in-v1 edits wanted, adjudicate surface (6th tool vs REST human lane) | design §4.3/§14-8; flag-dispositions #8; phase1/2 |
| D9 | design §8.1/DD-11 — once-per-instance vs long-lived instances | Once-per-instance stands (DD-11); briefing carries recall-affordance closing line; once-flag read fresh per turn (revive-safe); v2 alarm-wake refresh inherits L4 constraint (never fix the bare-HUMAN scheduler stamp) | design §14-9; flag-dispositions #9; phase5 |
| D10 | design §5.4 — `memoir_timeline(q?)` rank semantics unstated | Filter-then-rank: from/to on event_at filter; q = lexical-only ranker (ts_rank_cd PG / Python BM25 SQLite); NO cosine/RRF/recency-prior/keepsake-priority; timeline writes no stats | design §14-10; flag-dispositions #10; phase2 |
| D11 | design §5.4 — recall output unbudgeted | k default 5 max 20; per-episode body clamp 1,200 tokens; total envelope ≤6,000 tokens with ranked-drop + count/hint footer; enforced on the shipped projection (metrics-read M-1 doctrine) | design §14-11; flag-dispositions #11; phase2/4 |
| D12 | design §14-12 — pin-ceremony census numbers | Category `memoir` = PRIVILEGED #18 (17→18); 5 tools / 4 families; 6 count-pins + 5 exact-set pins + new memoir pin bumped in ONE commit + prompt-artifact re-stamp (161/161) | design §14-12; flag-dispositions #12; phase4 |
| D13 | design §5.3/DD-15 — OFF semantics + env polarity not fixed | OFF = whole personality (tools refuse typed `memoir_disabled`; briefing not built; worker not started; inner_soul ari branch loud not-recorded notice; data untouched). Tristate explicit-string polarity; unset → config default ON; garbage → fail-closed OFF + boot WARNING | design §5.3/§14-13; flag-dispositions #13; phase4/5/6/3 |
| D14 | design §10.1 seam 1 — PG materialization path unspecified | `.sql` = SQLite DDL + audit record (no tsvector/ARRAY); PG tables via manager registration → create_all (`:920-975` site); PG tsv+GIN via idempotent ALTER ensure (`:9500` family); persona seed via boot-time get-or-create (migration INSERT would never seed PG) | design §14-14; flag-dispositions #14; phase1 |

*(Row numbering D1…D14 above is DRAFT-local and sequential per the table's `#` column convention; the architect's lane renumbers into the live §4 sequence. Flag #4 contributes no row.)*

---

*Phase B flag dispositions. All PROPOSED — architect adjudicates (decisions.md §2/P-3). Captured 2026-11-05 on `feature/ari-memoir-store`; anchors grep-verified this session.*
