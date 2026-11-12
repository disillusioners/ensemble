# Decisions: Project Future (D1–D16)

Date: 2026-11-12 (UTC)
Companion: `plan.md` (work items W1–W11, AC1–AC25, PO1–PO8), `research-tool-lane.md`, `research-data-layer.md`, `research-prompt-docs.md`, `architecture-recommendation.md` (provenance for the fold pass — R1–R10, O11–O12 came from its APPROVE-WITH-ADJUSTMENTS verdict; D16 is a leader ruling).
All anchors verified 2026-11-12 in worktree `/home/nea/ensemble-src-wt-project-future` @ `ac9849bf7`; the 9 highest-load anchors were additionally spot-checked directly by the plan writer. plan.md + decisions.md are the single self-contained source of truth for the developer — required folds carry their full substance here and in plan.md, never a "see architect" pointer.

---

## D1 — Tool surface: exactly three tools

**Decision:** `project_future_add` / `project_future_list` / `project_future_remove`, inside the `project_*` family. Arg shapes mirror the history lane: add `(project_id, summary, details=None)`; list `(project_id, limit=20, offset=0)`; remove `(project_id, entry_id)` (O12b rename from the draft's `item_id` — family consistency with `project_history_delete(entry_id=…)` keeps LLM caller verb-habits aligned). Provenance (`source_agent`, `source_instance_id`) is stamped from the factory closure (`agent_id`, `current_instance_id`), never taken as tool args.

**Rationale:** history's add/list/delete anatomy is the proven skeleton (project_history.py:106-305); three verbs cover the whole wishlist lifecycle — nothing needs search (≤32 items, AC-list is enough) or an entry_type enum (every row IS a future idea). `remove` (not `delete`) matches the critical_notes verb family (`project_cn_remove`), reads correctly for "retire an obsolete idea", and D11 requires the removal response to identify the item — hence `{"success": true, "removed": {...}}` rather than history's lean `{"success": true, "deleted_entry_id": …}` (project_history.py:302).

**Evidence:** tool-lane Q1 (factory :106-109, delete shape :286-302); data-layer Q5 (test wiring :192-193 stamps closure values).

## D2 — Table `ProjectFutureEntry` with explicit monotonic `seq`

**Decision:** tablename `project_future`; `id` uuid4 PK; `project_id` FK→projects.project_id ondelete CASCADE nullable=False index=True (sa_column idiom); `summary` str ≤300; `details` str|None ≤5000; `source_agent` str NOT NULL default ""; `source_instance_id` str NOT NULL default ""; `created_at` **datetime** default_factory `datetime.now(timezone.utc)` (sibling idiom); PLUS `seq` INTEGER NOT NULL, app-assigned `COALESCE(MAX(seq),0)+1` inside the insert transaction, `UNIQUE(project_id, seq)` (named unique index `uq_project_future_project_seq`, model `__table_args__`). No `entry_metadata`, no `entry_type`.

**Rationale — seq determinism argument (graded property):** FIFO must evict in exact insertion order. `created_at`-only ordering (the `prune_excess` template, pending_repository.py:451) is non-deterministic under same-timestamp inserts; `(created_at, id)` with a uuid4 tiebreak is deterministic-but-ARBITRARY under microsecond ties (uuid4 order is random, so the "oldest" survivor would be random). `seq` makes eviction order exactly insertion order — a strictly stronger guarantee — and makes `list` trivially newest-first (`seq DESC`). **Seq-reuse precision (R6):** `seq` is monotone NON-DECREASING among LIVE rows, not forever — `MAX(seq)+1` reuses the top slot after the newest row is removed (rows 1..5 → remove seq=5 → next add gets seq=5). Ordering and FIFO stay correct (the new row is the newest and holds the max seq), but an eviction-disclosure `seq` is NOT a durable identifier: `id` is the durable identity — never correlate disclosures by `seq` (stated in D2, the Data Model Summary, and `_FULL_DOCS`). Concurrency caveat — **governed by D16 (supersedes the draft's "caller retries" wording):** two concurrent adds read the same MAX(seq) → both insert k+1 → unique violation → caught, WHOLE transaction rolls back (evictions restored, insert rejected — never partial) → typed `ConcurrentWishlistUpdate` → the tool returns the stable translated message; NO automatic retry. Single-writer-per-instance norms make this a microsecond-rare window.

**Rationale — datetime idiom:** the history sibling uses `datetime` + to_dict isoformat (models.py:496/:509); the rest of the file uses ISO strings. We clone the SIBLING TABLE, not the file mode; FIFO never load-bears on created_at anyway (seq decides). Deviation from Optional→NOT NULL for provenance: the D12 DDL specifies NOT NULL; the factory always stamps closure values ("" when unknown), and a cap-discipline surface wants a definite author field rather than a NULL/"" ambiguity.

**Evidence:** data-layer Q1 (:483-496 idioms, created_at flag), Q2 (:451 prune_excess ordering), plan.md AC12/AC19.

## D3 — FIFO algorithm: prune-before-insert, single transaction, mandatory disclosure

**Decision:** on add: count → `overflow = max(0, count − 32 + 1)` → SELECT oldest `ORDER BY seq ASC LIMIT overflow` → DELETE those (Core delete + explicit `rowcount == len(victims)` guard — R1) → INSERT new with `seq = MAX+1` → COMMIT → response carries `"evicted": [{id, summary, seq, created_at}, …]` (empty when not full). AFTER ANY ADD the table holds ≤32 (R5 scoping — reads are uncapped BY DESIGN and `total` exposes drift); a drifted table (count > 32) self-heals in the same pass (evicts down, inserts, lands exactly at 32).

**Rationale:** making room in the same transaction as the insert is the only way to keep the cap exact at every observation point (no check-then-act gap); multi-statement transactions are house norm (`_sync_tags_bulk` repository.py:103-113; `prune_excess` pending_repository.py:434-470). Template adapted from `prune_excess` (⭐ FIFO-by-oldest docstring :426-433) with THREE hardenings: ordering by `seq ASC` (D2), in-transaction execution (prune_excess is a standalone sweep), and the R1 victim-delete rowcount guard — the naive ORM delete loop rode on flush-time `StaleDataError`, and `prune_excess`'s raw `text("DELETE … WHERE id IN")` idiom silently deletes 0 rows, so the guard makes a false user-law disclosure impossible by construction (AC25/PO8). Disclosure in the add response satisfies the user law verbatim — no silent loss, ever. Race surfacing is D16 (translate-and-fail-closed, no retry).

**Evidence:** data-layer Q2 (prune_excess :421-477, _sync_tags_bulk :103-113), tool-lane Q1 (:236 clamp idiom reused in list).

## D4 — Ordering: list is NEWEST-FIRST

**Decision:** `order_by(seq DESC, created_at DESC)`; envelope `{entries, total, limit, offset, cap}` — the tool merges `"cap": _MAX_ENTRIES` into the repo envelope before returning (O12a, self-describing); `limit = max(1, min(limit, 100))`; `offset = max(0, offset)`.

**Rationale:** a wishlist is consumed newest-first (most recent idea first), matching the history lane's newest-first convention (repository.py:1392) and its envelope/clamp shapes (:1388, project_history.py:236-237). seq DESC is exact (unique per project); the created_at DESC tiebreak is defensive only and never fires. `cap` is merged at the TOOL layer because the tool module owns the cap constant (single source of truth, O11) — the repo envelope stays the history-lane shape.

**Evidence:** tool-lane Q1 (:223-252 list anatomy), data-layer Q2 (:1362-1400).

## D5 — NO dedupe in v1 (explicitly forbidden)

**Decision:** no fuzzy/exact duplicate detection. Forbidden as follow-up scope creep without a new commission.

**Rationale:** critical_notes justifies near-duplicate REJECTION because its rows are context-injected every turn (dupes are expensive there, critical_notes.py:525-535). A wishlist row costs nothing until someone works on it; the 32-cap + FIFO IS the discipline — old dupes rot out automatically. Fuzzy matching would add a rejection class to a positive surface ("your idea was 87% similar"), the opposite of the tool's spirit.

**Evidence:** tool-lane Q3 (critical_notes near-dup reject :525-535 as the contrast); dispatcher steer D5.

## D6 — REST parity: NONE in v1

**Decision:** agent-tool lane only; no HTTP endpoints, no API-layer code.

**Rationale:** the commission is tool-lane only; REST would drag daemon/api.py registration, auth surface, and tests for zero current demand (agents are the only writers by design). Adding REST later is purely additive — the repository methods are the seam.

**Evidence:** commission D6; data-layer Q3 (api.py lanes exist when a decision to add REST is ever made).

## D7 — REQUIRED DEVIATION STATEMENT: FIFO-on-full vs the house REFUSE-on-full doctrine

**Decision:** project_future EVICTS automatically when full and DISCLOSES the eviction in the add response. This deliberately DEVIATES from the house default for knowledge stores — critical_notes REFUSES when full ("No silent eviction", critical_notes.py:538-552, contract restated verbatim in its `_full_doc_` :578-579).

**Why the deviation is safe (four legs):**
1. **Surface type:** critical_notes is a context-injected, pinned, blast-radius surface — losing a note silently changes agent behavior unseen. project_future is a wishlist: nothing injects it anywhere; its only consumer is a future human/agent reading the list. Losing the oldest idea to make room for the newest is the tool WORKING.
2. **Disclosure:** the add response names every evicted item (id + summary + seq + created_at) — the loss is loud, in-band, and immediate; the writer can re-add with priority if the eviction surprised them. Nothing is silent.
3. **Exactness:** cap is exact at every observation point via same-transaction prune-before-insert (D3); no observer can ever see 33 rows.
4. **Simplicity:** REFUSE would force callers into a remove-then-readd ceremony for a 32-item list — ceremony costs outweigh the risk here, the inverse of the critical_notes tradeoff (where the refusal carries a curated 3-candidate eviction menu precisely because the surface is precious).

**Evidence:** tool-lane Q3 (:70, :538-552, :578-579); plan.md W6d (the `_full_doc_` must state the contract including this contrast).

## D8 — Dev-team grant set: 14 meta.json files, category-key style

**Decision:** add `"project_future"` to `tools.allow` in: leader, developer + developer[v2], reviewer + reviewer[v2], tidier + tidier[v2], approver + approver[v2], planner + planner[v2], tester, architect, devops — 14 files. Category-key style (expands to all three verbs automatically), matching leader's existing key style (meta.json:15). project-manager EXCLUDED in v1 (verb-style idiom, not dev-team — easy follow-up). Support roster (giter, explorer, wanderer, kb-writer, doc-writer, maintenancer, designer, gaia, aegis, coder, worker) excluded — widening is one file per agent.

**Justification:** the census (prompt-docs Q4) shows NO dev-spine agent holds ANY recording category today — project_future is their first persistent-recording channel, and the ideas this tool exists to collect come from exactly these agents. The surface is cheap: non-pinned, non-context-injected, 32-capped, eviction-disclosed (D7). Excluded agents either don't generate dev-work ideas (explorer/wanderer are scouts) or have adjacent channels (kb-writer → experience(); doc-writer → docs). Boot validation of allow entries is warn-only (registry.py:1437-1439) — hence AC24 asserts the filter BOTH ways positively rather than trusting absence of warnings.

**Evidence:** prompt-docs Q4 (census table; leader :15 category-style vs PM :25-33 verb-style; team_members :17), tool-lane Q4 (filter semantics, empty-allow inheritance).

## D9 — Discoverability: the pain point, four edits + baseline

**Decision:** (a) CATEGORY_DOC + `_FULL_DOCS` carry plain-language routing ("future improvement ideas go HERE — not critical notes, not project history") plus the full FIFO contract; (b) new leader rule.md Decision-Table row `| **Future improvement idea** — something you wish existed later (NOT a lesson, NOT an event) | project_future_add() | "Add a streaming retry path for long LLM calls" |` inserted after :137; (c) reviewer Suggestion 🟢 tier (:286) routed: Meaning cell becomes "Consider for future improvements — record the idea with `project_future_add()` (32-item FIFO wishlist)" — IDENTICAL edit in BOTH workflow.md twins; (d) docs/agents.md row after :347. Baseline re-stamp: bare regen + `--check --tree` exit 0 + commit on the same branch; row count stays 181; exactly 3 hashes change (leader rule.md row 119; reviewer workflow.md twins rows 143/147).

**Rationale:** the reviewer 🟢 tier is a confirmed dead-end — the only "future improvements" text in the whole reviewer prompt set is the severity-table row itself (prompt-docs #6); without (c) the tool exists but the agent who generates 🟢 suggestions never learns of it. The leader table (:127-143) is the established routing surface for "where do I record X". docs/agents.md is the human/ops surface. Baseline mechanics: rule.md/workflow.md are hashed depth-2 artifacts (prompt_artifacts frozenset :119-133); meta.json is NOT hashed, so W8 grants don't touch it; docs/ is outside agents/ (generator hashes tree/"agents" only, :105).

**Evidence:** prompt-docs Q1 (table rows :133-137, leader[v2] absent), Q2 (twins byte-identical, rows 143/147), Q3 (docs/agents.md :336-347, not hashed), Q5 (baseline invocation/exit codes).

## D10 — Registration: five steps, correct seam, no sidecar

**Decision:** (1) module + `@register_tool_category`/`@tool` stack; (2) `_full_doc_` per tool; (3) CATEGORY_MODULES `"project_future": "daemon.tools.project_future"` below :1189; (4) three names into KNOWN_TOOL_NAMES (sorted — `project_future_*` sorts BEFORE `project_history_*`), regen command :1550-1553, drift pin auto-covers; (5) construct+extend at instance.py:6651-6655 region (import :281) BEFORE create_help_tool :7620 / scan_tools_for_full_docs :7625 / _apply_tool_filter :7628. Blueprint pending-queue sidecar (:163-216) NOT copied; `manager` accepted at the factory for call-site parity, unused in v1. Category stays NON-privileged.

**Rationale:** the seam is instance.py's assembly function — research CORRECTED the brief's `_tool_registry.py` location and the spot-check confirms (extend at :6651-6655; registry has only dicts). The three ordering anchors are load-bearing: a late extend is SILENTLY INVISIBLE (in-repo comments :6665-6668/:6677-6680/:6698-6701) and NO test catches it — enforcement is distributed (missing CATEGORY_MODULES → allow WARNING only; missing KNOWN → frozen-binary false warnings; drift pin only checks names↔source, not reachability) — hence PO7's assembly probe. Non-privileged preserves the empty-allow inheritance contract exactly as project_history has it (A15).

**Evidence:** tool-lane Q2 (all registry/seam anchors, §Enforcement), Q5 (five-step checklist), Surprises #1/#2/#4; spot-checks of :6651/:1189/:1772.

## D11 — Repository methods; remove has NO agent-ownership requirement

**Decision:** `add_future_entry` (validate+truncate, prune-evict, insert — ONE session), `list_future_entries` (newest-first), `remove_future_entry` (mirrors :1340's session.get + project-scope, returns the removed dict or None), inline counts (no count helper). `store` stays `manager.project_store` — no new repository class, no factory change.

**Justification for ownership-free remove:** an idea's author and its retiree are usually different agents (a reviewer retires what a developer wished months ago; the cap churns authors anyway under FIFO, so ownership attribution is unstable by construction). The cap + disclosure already bound the damage: removing one of 32 wishlist rows is recoverable (re-add) and auditable (the removed dict is returned). Mirroring history's project-scope (:1355) keeps the delete from crossing projects.

**Evidence:** data-layer Q2 (:1340-1360 delete anatomy; get_history_entry :1327-1338), Q6 (store wiring manager.py:2151; 4-arg factory shape).

## D12 — Migration: full DDL as a copy-verbatim code block

**Decision:** `{YYYYMMDD_HHMMSS}_create_project_future.sql` (developer stamps real time; duplicate-prefix guard runner :420) with `-- UP` / `CREATE TABLE IF NOT EXISTS` / two `CREATE INDEX IF NOT EXISTS` (incl. UNIQUE `uq_project_future_project_seq`) / `-- DOWN` dropping exactly what UP created. TEXT-never-VARCHAR; IF-NOT-EXISTS mandatory everywhere.

**Rationale:** mission_records (:46-90) is the canonical structure; memoir v2 supplies the stricter written doctrine (TEXT-never-VARCHAR :42; IF-NOT-EXISTS mandatory because create_all runs FIRST on the boot path :64-73). Name lockstep is exact: `ix_project_future_project_id` = SQLAlchemy's default auto-name for the FK column's `index=True`; `uq_project_future_project_seq` = the model's explicit `Index(..., unique=True)` — model ↔ migration ↔ create_all agree name-for-name (memoir 3-site lockstep :50-58). NO `_ensure_postgres_columns` entry: brand-new tables are create_all territory on PG (manager.py:1025-1029/:1045-1049/:8421-8429); the .sql is the SQLite-side canonical DDL + permanent record. ONE create_all lane: the repositories-`__init__` mission-record-style import block (:95-107), NOT the newer api.py per-model lane (:1220-1383) — the project package already owns the model, and house comments cite exactly one lane per table.

**Evidence:** data-layer Q3 (both lanes), Q4 (templates + ensure-columns doctrine + fresh-SQLite breaker caution), plan.md W2.

## D13 — Test plan: 25 ACs across four new files

**Decision:** tool tests (MagicMock store, direct dict asserts, cap constant imported module-level — exact house idiom of test_project_history_tools.py), repo tests at tests/ ROOT (in-memory sqlite + create_all, NO migration runner — the 20260714_000001 fresh-SQLite breaker makes runner-based fixtures impossible anyway), migration marker-greps (pattern :583-611), registration/filter pins. FK behavioral tests pin `PRAGMA foreign_keys=ON` (SQLite default OFF would make the cascade assertion vacuous) plus the introspection pin `fk.ondelete == "CASCADE"` (test_snapshot_search_service.py:1039). AC25 (fold R1): the stale/concurrent victim-delete guard — force the interleaving (engine hook) that deletes a selected victim from a second session between the add's victim-SELECT and victim-DELETE; assert whole-transaction abort (translated D16 message at the tool lane), table count ≤ 32, and NO false `evicted` entry; a naive delete-then-add (committed before the add starts) must NOT trip the guard — it counts 31 and proceeds. NO network, NO live DB.

**Evidence:** data-layer Q5 (all four idioms + PRAGMA gap flagged), plan.md W9 AC1–AC25.

## D14 — Proof obligations: the user law demonstrated end-to-end

**Decision:** PO1–PO8 (plan.md W10), all tester-executable in-process. PO1 is the law: 33 inserts via the tool lane → #1..#32 with `evicted: []`, #33 discloses item#1 (id+summary) with count==32, #34→#2, #35→#3 (monotone), list newest-first with #1 gone; plus independence, cascade, baseline `--check`, drift-pin, the assembly-visibility probe, and PO8 — the R1 stale-victim guard demonstrated end-to-end via the tool lane (hook-forced interleaving → exact D16 translated message, count ≤ 32, no false disclosure).

**Rationale:** the green suite does NOT prove the tool is reachable (no test catches a missing extend — tool-lane Surprises #2); PO7 exists precisely because the failure mode is silent, and PO8 exists because the victim-delete guard class was invisible until the architect's area-2 finding (the invariant silently rode on ORM `StaleDataError`). PO1 turns the two hard requirements into a demonstrated, monotone, disclosed eviction sequence rather than scattered asserts.

**Evidence:** tool-lane Q2 §Enforcement + Surprises; plan.md W10.

## D15 — Version slot: v0.29.23 next free; NOTE ONLY; zero contact

**Decision:** record that the next free version slot is **v0.29.23** (v0.29.21/.22 staged, live v0.29.20 as of 2026-11-12). Nothing is staged, promoted, or merged by this lane. Zero-contact restated: no daemon HTTP, no `/home/nea/agents-ensemble`, no canonical `/home/nea/ensemble-src` (detached HEAD + dirty sets — never commit there), no push, no merge, no promote. All work happens in the claimed worktree on `feature/project-future`.

**Evidence:** project metadata (staged/live versions); commission zero-contact directive; worktree claim (shared meta KV, clean @ ac9849bf7).

## D16 — Race handling on concurrent same-project adds: TRANSLATE-AND-FAIL-CLOSED (leader ruling)

**Decision:** when a concurrent same-project add loses the `UNIQUE(project_id, seq)` race — or the R1 rowcount guard detects a stale victim delete — the repository performs a WHOLE-transaction rollback (evictions restored, insert rejected: never a partial state), raises the typed domain exception `ConcurrentWishlistUpdate` (home module: `daemon/repositories/project/repository.py`), and the add tool returns exactly `{"error": "Concurrent wishlist update for project '<pid>' — please retry"}`. There is **NO automatic retry anywhere** — not in the repository, not in the tool.

**Rationale (leader ruling; architect's recommendation accepted, architect's optional retry DECLINED):**
1. The 32-cap invariant is UNCONDITIONAL — the user law admits no exception path that could leave 33 rows or an undisclosed loss, so the failure mode must be fail-closed, which it is.
2. Contention must surface as an HONEST SIGNAL, not be masked: an in-repo retry would silently absorb contention and hide the (rare) window where two writers want the same slot; a typed, stable error makes the contention visible to the caller and to logs.
3. The calling AGENT may still choose to retry — the message literally says "please retry" — so friction stays minimal without the system hiding anything. This preserves the single-`{"error": str}` response contract (A7) and kills the draft's raw `{"error": "<IntegrityError str>"}` shape, which embedded constraint/table/pgcode dialect internals (risk row 12; precedent for the translation: `daemon/repositories/mcp_server/repository.py:146-205` — catch, rollback, translate, re-raise the rest).

**What this supersedes:** the draft plan's seq-race wording ("IntegrityError rolls back, caller retries", raw `{"error": str}` surfacing, generic except-wrap as a judgment call) — updated in plan.md's FIFO properties, add-error shapes, W3/W4 specs, risk row 2, and decisions D2/D3. The architect's "optional: one bounded retry" (arch §Adjustment 2b / Open questions) is DECLINED by this ruling; the translation — the part the architect called the one that matters — is adopted in full.

**Evidence:** leader ruling (this fold pass); arch §Adjustment 2a/2b; A31 precedent; plan.md AC25/PO8 pin the guarded path; risk rows 2/10/12 carry the failure classes.
