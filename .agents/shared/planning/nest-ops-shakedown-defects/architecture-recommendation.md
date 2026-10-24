# Architecture Recommendation — nest-ops shakedown defect patch (enrichment)

**Date:** 2026-10-24 (UTC)
**Commission:** architect enrichment of `.agents/shared/planning/nest-ops-shakedown-defects/` — **ADDITIVE ONLY**; no plan file was modified.
**Worktree:** `/home/nea/ensemble-src-wt-nestops-shakedown` · branch `fix/nest-ops-shakedown-defects` · base `b257842c9`
**Instances:** `architect-worker-f11-security` (233dfe95-4ba7-4113-89fe-8360df473185, skill `security-design`) · `architect-worker-f3-f8-f1-structural` (933cf04b-74c5-4117-b77c-5a8e99a3adeb, skill `structural-design`) · `architect-worker-f2-f4-tests-scalability` (6a076af6-011e-452d-9146-7de1a81db509, skill `scalability-design`)
**Post-DF-4 reference (point-in-time/ancestry — never pin an equality to a snapshot SHA):** at authoring, `origin/latest` was `d4ecdbd3d`; live re-verification 2026-10-24 found `origin/latest` = `074833b45` (with `d4ecdbd3d` and DF-4 merge `11c75357f` both confirmed ancestors); read-only via `git show origin/latest:<path>`, no other worktree touched; the Phase-2 gate journals the CURRENT `origin/latest` SHA at run time — the ancestry assertion is the durable form.

---

## 0. Verdict summary

| Area | Verdict | Severity |
|---|---|---|
| **F3** husk detector | **PLAN SHOULD CHANGE** — the reuse premise is false as stated (`filter_never_dispatched_ids` is `idle ∧ v1 ∧ ¬∃msg ∧ ¬∃task`); the evidence expression must be single-homed | 🔴 |
| **F2** census | **AMEND** — one method / two statements / one session; shared KB-filter helper; `idle` is a *status* bucket only; race disclosure in the note | 🟡 |
| **F4** scan + **NEW F12** | **PLAN SHOULD CHANGE** — the sketched loop can silently skip the oldest rows under concurrent writes; a 1-line `status`-forward defect makes the planned `truncated`/reconciliation claims **false today** | 🔴 |
| **N+1** amplification | **AMEND** — add a tool-side time budget + a Phase-4 measurement task; batched-children recorded as a first-class follow-up | 🟡 |
| **F8** read purity | **PLAN RIGHT** — 2 mechanics/disclosure corrections | 🟡 |
| **F1** alias | **PLAN RIGHT** — literal closure required (AST discovery); one doc contradiction to resolve | 🟢 |
| **F11** chain token | **PLAN SHOULD CHANGE** — `job_continue`'s chain is erased by the S3 pop as written; freeze list incomplete; gate additions | 🔴 |
| **F6** gate ordering | **BINDING RULING RECORDED** — D-8 already folded; implementation shape confirmed safe; 7 stale ops-side doc spots (iteration-003 corrected count; all bracket-remedied in place) | 🟡 |
| **Phase-2** DF-4 gate | **NOT SUFFICIENT AS WRITTEN** — add 6 cheap checks + required pin counts | 🟡 |
| **Test architecture** | **PLAN SHOULD CHANGE** — net-new attribution ladder is wrong; fixture needs two tiers; 4 defects lack mutations; 2 mutations are multi-behavior | 🔴 |

Method: plan set fully read (overview, decisions D-1…D-17, phases 1–5, both technical analyses); three parallel skill-equipped workers did the code-grade adjudication (one skill each); I verified the two highest-severity claims directly at the code level and re-read the current D-8 text.

**Root-cause citation spot-check (my own, base `b257842c9`): all five planner citations are ACCURATE** — `instance_read.py:374` (`stale = idle_seconds is not None and …`), `:1087` / `:1151` (detectors early-exit on null activity), `repository.py:1176-1177` (constant `False`), `manager.py:13816-13836` (`find_near_instance` repo-only), `job_queue.py:1319` (`source=f"agent:{caller_agent_id}"`).

---

## 1. F3 — husk detector (#7): predicate ownership, cost, fixtures

### 1.1 The reuse premise is false as stated — 🔴

`InstanceRepository.filter_never_dispatched_ids` (base `repository.py:735-790`) has WHERE `Instance.status == IDLE ∧ Instance.version == 1 ∧ ¬∃(message_queue) ∧ ¬∃(task)` (`:785-789`), and its twin `ChildReportsService._ghost_child_filter` (`child_reports.py:2864-2901`, expression `:2877-2889`) is the identical four-conjunct predicate. The lockstep pin `tests/unit/test_pause_never_dispatched_ghosts.py` also **pins the blindness** (`test_running_status_never_matches`, `:140-153`). D-1 leg 1 is *non-terminal* — broader than `idle`. Using that method as the E1/E2 probe would therefore silently false-negative any non-idle/version>1 evidence-free row.

**Blind spot today is empty** (worker enumeration): pause-cascade's `status='paused'` UPDATE explicitly excludes the never-dispatched subset (`instance_lifecycle.py:5728-5743`); `transition_status_if` writes status+updated_at only (`repository.py:1485-1519`) and its callers are evidence-bearing; resume requires PAUSED (`:6270-6292`); every COMPLETED/ERROR writer stamps `last_activity_at` in the same statement. But the reuse is **structurally unsound**: the implemented predicate would be `idle∧v1`, not the documented non-terminal, and the method is bound by the lockstep pin to the *completion-gate* domain — a future tightening of the ghost conjuncts would silently re-scope husk detection, and satisfying the husk need by editing them would break pause-cascade skip semantics.

### 1.2 Recommended shape — evidence expression single-homed (PLAN D-1 AMENDMENT)

1. Add a module-level SQL-expression factory `never_dispatched_evidence_expr()` returning the **two** `~exists(...)` clauses (`repository.py:753-760`). No import cycle (repository imports repositories only, `:14-28`).
2. Use it in `filter_never_dispatched_ids` (composed with `idle ∧ version==1`) — behaviour byte-identical.
3. Add **`filter_without_any_evidence_ids(instance_ids: list[str]) -> set[str]`** — evidence-only (no status/version), ONE batched query, **chunked at 500** (SQLite bind limit 999; unit pins run SQLite; PG's 65535 is moot).
4. The husk detector calls **only the new evidence-only method** — never `filter_never_dispatched_ids`.
5. `child_reports._ghost_child_filter` stays untouched **in this patch**: its duplicate is pre-existing (pinned in lockstep) and touching the parent-completion gate expands blast radius; record the optional clean-up (delegate the twin to the factory, 3 lines) as a routed follow-up. This satisfies "no third fork": the patch introduces zero new copies of the evidence predicate.
6. Rewrite the `disagreement_pin` text: containment holds on the **evidence conjuncts only**. Enforceable statement: "every husk-flagged row satisfies ¬∃msg ∧ ¬∃task (via the shared expression) ∧ non-terminal ∧ `last_activity_at IS NULL` ∧ age > threshold". Drop the "husk ⊆ canonical ghost set" phrasing (false once a paused evidence-free row can be flagged).
7. Add a containment pin: on one fixture, `filter_never_dispatched_ids(ids) ⊆ filter_without_any_evidence_ids(ids)` (evidence-free is a superset), plus agreement on status/version variants.

### 1.3 Remaining legs, threshold, integration — verified

- **E3 (JobItem):** no existing helper answers "any row, any state" (`get_by_instance` filters `deleted_at IS NULL` + newest-only, `job_queue/repository.py:543-563`; `find_jobs_by_instance` filters ACTIVE only, `:1569-1594`). Use one grouped `SELECT instance_id, COUNT(*) … WHERE instance_id IN (…) GROUP BY instance_id`, **no `deleted_at` filter** (soft-deleted = evidence ✓), chunked at 500, module precedent `instance_read.py:1326-1342`.
- **E4 (children):** authoritative per-row attach (`instance_lifecycle.py:5104-5106`). **Do NOT re-derive children from the scanned rows** — a truncated window would miss out-of-window children and false-positive the parent as a husk.
- **E5/E6:** verified. `_question_pack_of` is RAM-only (`instance_read.py:254-267`); cold rows are covered because packs are boot-rehydrated from paused-task metadata independent of hydration (`manager.py:4660-4724`). The deliberate-park tombstone is `question_pause_set.py:609-641` ✓.
- **Threshold 24 h stands.** `created_at` is NON-NULL TEXT aware-ISO (`models.py:74`) → `_seconds_since` (`instance_read.py:211-216`) is correct. `_flagged_row` (`:1010-1024`) is a pure shape and the health payload aggregates counts only (`:894`), so `idle_seconds = age` is safe **provided the `detail` names the mechanism** (plan does).
- **Integration:** append after the failed-with-unreported-result block (base ends `:1295`) before the `return` (`:1297`) — plan's `:1271-1298/:1300` is ~3 lines off, harmless. `TERMINAL_INSTANCE_STATUSES` is already imported at `:1063` (no new import).
- **Spawn shape verified:** `_spawn_instance_db_sync` (`instance_lifecycle.py:5524-5612`) inserts `status=IDLE, version=1`, no `last_activity_at` (NULL), hierarchy row only. Both fixture IDs satisfy every leg after only a `created_at` backdate. **Add one fixture assertion: zero `job_queue_items` rows for both IDs** (pins E3 against the real spawn order).
- **Existing fake-fleet trap:** `test_instance_ops_behaviour.py:158-165` holds evidence-free rows aged 2 h/8 h — the 24 h constant keeps detector #7 out of those pins; any future threshold < 8 h silently starts flagging them.

---

## 2. F2 — census: full-fleet counts + reconciliation (D-2 amendments) 🟡

### 2.1 Shape: ONE method, two statements, ONE session

`count_fleet_by_status_and_agent(*, exclude_kb: bool = True) -> FleetCounts(groups: list[tuple[str,str,int]], total: int)`:
- Statement 1: `SELECT COUNT(*)` over the same WHERE; statement 2: `GROUP BY status, agent_id` — **one connection checkout**, COUNT first.
- **Do not use `GROUPING SETS`** — PG-only, while the repository must stay SQLite-compatible (unit packs run scratch SQLite).
- **Race disclosure is mandatory:** on a busy nest a write between the two statements yields `reconciled:false` for an internally consistent fleet. The note text must say: `reconciled=false` can transiently reflect a write between the two statements of this scan.

### 2.2 Drift closure: shared KB predicate

Extract `_kb_filter(exclude_kb)` (the `agent_id.not_in(KB_AGENT_IDS)` predicate, `repository.py:1141-1143`, constant `:33`) and use it in **both** `list()` and the new method. Then D-2's "same constant / same flag / same predicate shape" is structural, not conventional.

### 2.3 Marginalization + reconciliation contract

`counts_by_status[s]=Σ_j n_{s,j}` (≤10 keys) · `counts_by_agent[a]=Σ_i n_{i,a}` (≤~20) · `counts_by_liveness[l]=Σ_{s: _liveness_for(s)=l} counts_by_status[s]` (≤5; `_liveness_for` `instance_read.py:185-195`) · `counted_rows=Σ_s counts_by_status[s]` · `reconciled = counted_rows == total_rows`. Both marginals also sum to `counted_rows` → a free third cross-check to pin.

### 2.4 The `idle` bucket is a STATUS bucket only — 🟢 correction

`_STATUS_CANONICAL_MAP` maps `idle → "processing"` (`work_status.py:87`), so `counts_by_liveness` has **no** `idle` key. The F2 pin must assert `counts_by_status["idle"]`; the note must say "the `idle` STATUS bucket", not "liveness bucket".

### 2.5 Verified / retained

KB exclusion shape ✓ · `exclude_kb=True` on both paths (`manager.py:13525` default; `instance_read.py:504-510`, `:1305`) ✓ · census row shape untouched (`_census_row` `:351-394`; husks still render `stale:false` — correct, the class belongs to `instance_health`) ✓ · `fleet_capacity_summary.scan_truncated` (`:1414`) becomes a literal `False` (do not propagate the repo constant) ✓.

**Scale:** `instances` indexes = `agent_id, agent_dir, agent_name, parent_id, status` + `project_id` + `paused_at`; **no `created_at` index**. `GROUP BY status, agent_id` = seq scan + hash aggregate ≈ 5–15 ms @10k, 50–150 ms @100k on local PG16; cardinality ≤ ~200 groups. A covering `(status, agent_id)` index is a follow-up, not required.

---

## 3. F4 — scan/pagination + NEW F12 (status-filter drop) 🔴

### 3.1 F12 — the `status` filter is silently dropped before SQL (CRITICAL GAP, independently verified)

- `Manager.list_instances` forwards `status=status` to the lifecycle (`manager.py:13585`).
- `InstanceLifecycleService.list_instances` **does not forward it** to `instance_repository.list(...)` — the kwargs list is `limit, offset, project_id, exclude_kb, include_descendants, search, order, source` (`instance_lifecycle.py:5086-5095`) — **while its own docstring claims "applied in SQL by the repository (`repo.list(status=…)`)"** (`:5067-5074`).
- `repo.list` *does* support it, on both count and select (`repository.py:1135-1137`).

Consequences today: `instance_list(status="running")` returns arbitrary-status rows from the pinned-ordered first page while `total_matching` counts only `running`; the planned F4 flag `total > applied_offset + len(rows)` is meaningless for filtered calls; the tool docstring "The filter is applied in SQL, so a fleet larger than one page still returns a CORRECT filtered result" (`instance_read.py:562-566`) is **false**; and F2's note caveat assumes a filtered page that does not exist.

**Blast radius is tiny:** the only caller passing `status` is `instance_read.py:504-510` (`routers/instances.py:476`, `instance.py:5785`, `:1066`, `:1305` do not).

**Recommendation (🔴): include as a 12th defect — F12, 1-line forward at `:5094` + a pin at the lifecycle→repo seam** (that seam has **zero** coverage today; the manager→lifecycle seam is covered by `tests/unit/test_manager_list_instances_source.py`). This is a scope change (11 → 12 defects) and needs orchestrator/leader ratification. **If refused**, then F4's `instance_list.truncated` formula must be scoped to **unfiltered calls only**, the `:562-566` docstring must be corrected to say the filter is currently ignored, and F2's note must state that a filtered `total_matching` counts a population the rows do not represent.

### 3.2 The sketched scan loop can silently skip the oldest rows — 🔴

The read-side §5.2 loop breaks on `len(windows) >= total` and recomputes `scan_truncated` from a **reassigned** `total` each window. Under concurrent inserts (a busy nest — the reason F3/F4 exist) a new row lands at the FRONT of `created_at DESC`, shifting every later row down: each window re-reads the boundary row, `len(windows)` reaches `total` early, the loop breaks **before walking the tail**, and `scan_truncated` reads **false** — the husk class is skipped silently. Deletes / pin-flips shift rows up → skipped rows as well.

**Recommended loop shape:**
- capture `total` from the **FIRST** window only, never reassign;
- accumulate into a **dict keyed by `instance_id`** (dedupe);
- break **only** on `not rows` (end-of-set) or the window cap — **never** on `len >= total`;
- `scan_truncated = (distinct_read < total_first) OR (cap fired without an end-of-set window)`;
- add `scan_stop_reason ∈ {end_of_set, window_cap, time_budget}` so the flag is actionable (otherwise "fleet > 20 windows" and "cut for time" collapse into one boolean).

Keep Option A (offset walk) — keyset pagination would need a new repo sort surface, and a snapshot-bounded query is impossible through the manager API (every `list_instances` opens its own session). Add one disclosure sentence to `instance_health._full_doc_` (`:903-940`): paging is OFFSET over a live fleet; rows created or pinned mid-scan may be counted twice or missed; `scan_truncated=true` means the oldest rows may not be covered.

**Verified / retained:** flat ORDER BY deterministic and ends `instance_id ASC` (`repository.py:1158-1171`); flat path ignores `order` (`:1092-1094`); `include_descendants=True` never used by the surface; **empty page ⇒ `truncated == (total > applied_offset)`** — the exhausted/overshoot case (`len(rows)==0 ∧ total ≤ applied_offset`, REACHABLE because `instance_read.py:495-502` clamps only non-int/negative offsets, no upper bound) correctly reads `False` under the mandated formula `bool(total_matching > applied_offset + len(census))`; three flag sites are exactly `:543`, `:899`, `:1414`; repo returns constant `False` (`:1176-1177`) so the tool computes. **But `:543`'s formula is exact only after F12.**

---

## 4. N+1 amplification — quantified, bounded, measured (D-3/R3 amendment) 🟡

**Verified:** `instance_lifecycle.py:5101-5106` calls `list_child_ids_permanent(instance_id)` **inside** the per-row loop, and that method opens **its own session** per call (`repository.py:277-283`) → 1 connection + 1 query per row.

**Query counts (window 1000, cap 20):** per window = 1 COUNT + 1 page + 1000 child queries = 1002 → 1,730-row fleet ≈ **2,002** (today 1,002); 10k ≈ **10,020**; 20k cap ≈ **20,040**. Extra detector costs: `_lineage_max_activity` = 2 queries per RUNNING row (`instance_read.py:1083`); `_has_live_task` = 1 per PAUSED row (`:1154-1155`). F3's new legs are correctly batched (2 queries total) ✓.

**Estimates (unverified — no PG contact):** ~1–2 s @1.7k, 5–10 s @10k, 10–20 s @20k at 0.3–1.0 ms/query. **Memory: "bounded per window" is false for Option A as written** — `windows.extend(rows)` materializes the whole fleet (~20k dicts + child lists ≈ 20–40 MB). Stream per window (evaluate detectors incrementally or keep only candidate-relevant fields) or disclose.

**Adjudication:**
- **(b) tool-side time budget — ADOPT:** `FLEET_SCAN_TIME_BUDGET_SECONDS = 10`, checked before each window; on exhaustion break with `scan_truncated=True`, `scan_stop_reason="time_budget"`. A 20 s tool call eats a sixth of the LLM read window; the budget converts unbounded cost into a loud bounded one.
- **(a) measure + document — ADOPT, but the task is missing:** add **Phase-4 T4.8 (measurement)** — per-window query count and wall-time on the >window fixture.
- **(c) batched children — OUT OF SCOPE BY BLAST RADIUS, NOT SIZE:** `list_child_ids_permanent_bulk(ids) -> dict[str,list[str]]` (`SELECT parent_id, instance_id FROM instances WHERE parent_id IN (…)`, ~12 lines) + 3–5 lines at `:5101-5106` + a parity pin ≈ 20 lines / 2 files. It changes a shared manager method used by the REST list route and collides with Phase 3's manager edits (F11) → keep OUT, but record it as a **first-class follow-up with the exact diff shape** (the plan currently parks A2 with no anchor). With it, the scan is ~40 queries and the 20-window cap becomes safe.
- **(d) lower max-windows — REJECTED as primary:** recreates the F4 blind spot (the oldest rows).

**Phase-4 failure thresholds:** (i) per-window query count > `FLEET_SCAN_LIMIT + 2` → N+1 unbounded → FAIL; (ii) `instance_health` wall-time on the >window fixture exceeds the budget → the budget path is not exercised → FAIL; (iii) `scan_truncated=false` while the tail is uncovered → FAIL (the §3.2 drift bug).

---

## 5. F8 — read-path purity (PLAN RIGHT, 2 corrections) 🟡

**Seam claim confirmed — single seam.** `_resolve_target` (`instance_read.py:334-348`) has exactly **one** caller (instance_get, `:625`) → `_resolve_instance_id` → `await manager.get_instance(id)` (`instance.py:1475-1478`) → cold path `ensure_mcp_preloaded` (`instance_lifecycle.py:4504-4505`; `get_schemas_for_server`, empty results never cached, `mcp_service.py:236-296`) + `_restore_instance` whose re-elevation writes `agent_tag`/`agent_dir` + deletes metadata (`instance_lifecycle.py:4662-4687`). `find_near_instance` is repo-only (`manager.py:13816-13836`).

**No data source depends on hydration:** row (`:637`), children (`:654-656`), tree (`:660-662`), ancestors (`:672-695`), tasks (`:707-710`), checkpoint (`:728-744`), pack (`:750-758`, RAM). `_restore_instance` does **not** touch the question pack store → **the feared question-pack regression does NOT exist**; a cold-but-pending pack is boot-rehydrated independently (`manager.py:4660-4724`).

**Corrections:**
1. **Disclosure gap (🟡):** for rows whose versioned agent dir reappeared after a fallback, today's cold read *promotes* `agent_tag`/`agent_dir` on the row; after F8 the payload reports the **stored** fallback tag. That is the correct read-pure behaviour but it is a payload-visible change — add one sentence naming `agent_tag`/`agent_dir` to the residual (D-4 currently names only "cold stays cold").
2. **Resolver must reproduce ALL FOUR `ValueError` texts byte-for-byte + the empty-id guard + the loud WARNING** (`instance.py:1472-1516`, WARNING `:1489-1496`): "cannot be empty" / single "Did you mean" / multi-candidate / "no similar instance found". Do not touch the shared helper (pinned `tests/unit/test_find_near_instance.py:351/:388`). Return the fetched row from the resolver to avoid a second `repo.get`.
- Additional deltas to disclose: a previously-erroring restore becomes a success; the read no longer warms the cache.

---

## 6. F1 — alias mechanics (PLAN RIGHT) 🟢

- **A second literal `@tool` closure is required.** AST discovery scans `@tool` at any depth (`_tool_registry.py:733-764`, `:767-809`); a **code-generated** alias (loop / `functools.partial`) would NOT be discovered. Record this constraint in D-6 — it forecloses "alias via helper".
- The alias must be a **distinct named def** with its own `_full_doc_` (full docs are keyed by `tool_func.name`, `:827-832`).
- Insert the name into **BOTH** `DYNAMIC_TOOL_NAMES` (`:99-104`) and `KNOWN_TOOL_NAMES` (`:1167-1176`) in the SAME commit — the bidirectional pin (`tests/unit/tools/test_frozen_tool_name_discovery.py:223-242`) fails in both directions otherwise.
- `maintenancer` grants the CATEGORY (`agents/maintenancer/meta.json:19`) → auto-granted; `len(PRIVILEGED_TOOL_CATEGORIES)==14` (`tests/unit/test_instance_ops_registration.py:172`) unaffected.
- Housekeeping: `READ_TOOL_NAMES` in `test_instance_ops_registration.py:58-63` is a hand-maintained set used only in subset checks (`:104/:121/:125`) → add the alias to the set and update the three "all ten" TEST METHOD NAMES for honesty — `test_step1_ast_discovery_finds_all_ten` `:100`, `test_step3_dynamic_tool_names_contains_all_ten` `:120`, `test_step3b_known_tool_names_contains_all_ten` `:124` (the "all ten" strings live in METHOD NAMES; no such docstring surface exists); no `len(tools)` pin exists.
- **Cite correction:** the first-party category-override gate is `_tool_registry.py:869-870` (guarded by `_tool_category_first_party`); `:846-852` is the insert block.
- **Resolve the D-6 ↔ SC6 contradiction:** interpretation — the live name's **payload** stays byte-identical (guaranteed by the one builder) while its **doc** gains the one-sentence alias mention (as D-6 states). Amend SC6's wording to "live name unchanged; payload byte-identical; doc gains the alias sentence". No doc pin exists on `fleet_capacity_summary`, so this is safe.

---

## 7. F11 — delegation-chain token (security-critical) 🔴 (one hole + refinements)

### 7.1 DF-4 complementarity — VERIFIED

- Marker 1 ✓ `SWEEP_NO_USER_ORIGIN_WAKE_SOURCE` at `upgrade_journal.py:1191`; both call sites `upgrade_journal_sweep.py:1217`, `:1748` (import `:72`).
- Marker 2 ✓ `direct-provenance-insufficient` absent from `daemon/` + `tests/` (only historical planning prose).
- Marker 3 ✓ `_protected_gate` (`instance_ops.py:336-385`): three `if`s, **two semantic conditions** (`confirmed_target_id is None` `:353`, `!=` `:363`, then `not verdict.ok` `:373`); **`verdict.pclass` never appears in the gate body**.
- Freeze list intact (F11 touches none) — but **all citations are base-relative and stale** (§10) and the list is **incomplete**. **Add three freeze surfaces:** (a) `stamp_user_origin_window`'s **clear branch** `manager.py:4947` (an implementer must not make the `agent:` lane stamp a DIRECT window — that would collapse the direct/inherited distinction the ledger counts); (b) `restart_provenance`'s **direct-first read order** (`provenance.py:198-233`); (c) `_user_origin_last_stamp` observability (`manager.py:4930-4938`).

### 7.2 The DF-4 interaction risk — **CLOSED, no gap** (this was my top pre-dispatch risk)

The funnel block is gated **only** on `if not silent:` (`origin/latest manager.py:9808-9834`; `:9824` stamp, `:9832` inherit). There is **no** injected/stamped conditional in manager.py. A DF-4 identity-flip message (`source="system:…"`, stamped at `instance_messaging.py:400-424`, applied `:587`) reaches the block; `classify_user_origin` returns `reserved-internal` (`upgrade_journal.py:3410-3411`) → the child's direct window is cleared (fail-closed) and `_inherit_provenance_window` POPs. `agent:` is **NOT** in `_INTERNAL_STAMPED_SOURCE_PREFIXES` (`instance_messaging.py:392-397`) → job-lane messages stay bare. Job-lane reachability is complete: `job_queue.py:1319/2062` → `job_processor.py:1176/1268/1548` → `enqueue_message` → `task_processor.py:496/506` → `ProcessingContext` (`message_processing_pipeline.py:119-175`, sole construction site `task_processor.py:555`) → `_do_process:427` → funnel. ✓

### 7.3 THE HOLE — `job_continue`'s chain is erased by S3's own pop 🔴

`job_continue` (`job_queue.py:2056`) calls `manager.enqueue_message_job(instance_id, message, source=…)` — **no metadata channel at all today**. `enqueue_message_job` (`instance_messaging.py:2695`) merges its `metadata` into `job_queue_service.enqueue(metadata={**(metadata or {}), …})` (`:3059-3077`), and S3 pops the reserved key from caller-supplied metadata → **the chain never reaches the row**. `instance_messaging.py` is absent from T3.1/T3.2's touchpoint table, so as written D-7's "`job_continue` IS in scope" is **unimplementable**.

**Fix (fold into T3.1/T3.3):** add `dispatch_chain: dict | None = None` (keyword) to `manager.enqueue_message_job`, forward it as a **keyword** to `job_queue_service.enqueue(…, dispatch_chain=dispatch_chain)`; pass the chain **by keyword, never via metadata**. ~2 lines in one file; closes a same-class hole the plan itself names.

### 7.4 Service-seam refinements

- **Normalize once at the top of `enqueue`** (before both create sites: idempotency `:1481`, plain create `:1607`) — a per-site pop leaves one path open. Pin **both** sites.
- REST injection is closed by **three** controls, not one: (a) the S3 pop; (b) the **lane check** (`chain` honoured only when `source.startswith("agent:")`) — necessary because `manager.enqueue_message(metadata=…)` writes `message_metadata` directly onto the message row (`instance_messaging.py:2206`), bypassing the job seam; (c) the **HTTP reserved-source gate** — `jobs_crud.py:~530-545` rejects `RESERVED_SOURCE_PREFIXES` (contains `"agent:"`, `constants.py:946`) with 422. Update ops-side §2.5 row 2 accordingly.
- **S6 must preserve every fail-closed arm verbatim** (`manager.py:4955-5056`): prefix check `:4983-4988`; pops `:4986/:4991/:4998/:5024/:5028/:5049`; direct claim + liveness `exp >= now` `:5006-5015`; inherited `depth+1` `:5016-5020`; no-parent POP `:5021-5025`; cap `depth > 4` (const `:231`) `:5026-5029`; monotonic decay `:5030-5034`; entry shape `:5035-5041`. The sketch omits the **`now_dt is None` guard** (`:4997-4999`) and the **exception-handler fail-closed pop** (`:5042-5056`) — carry both. Exactly one POP path; keep the empty-`caller_iid` check.
- **Pointer-not-trust — verified:** writers are only the two mint sites whose `current_instance_id` is the daemon-bound factory closure (not a caller argument); REST metadata is popped; the message lane is protected by the lane check; HTTP rejects reserved sources. The claim **re-reads** the window and re-checks expiry (`:5006-5018`), and the gate re-checks independently (`provenance.py:203-204`, `:237-238`) — two liveness checks. Residual: a mis-bound factory would name the wrong instance → add a pin asserting `chain.caller_instance_id == the acting instance`.
- **Silent-resume edge (document + pin):** a checkpoint resume (`silent=True`) never mints an inherited window → the child refuses `no-user-provenance`. That is today's semantics; state it in the F11 docstring and pin it, or the shakedown will re-open it as a "new" bug.
- **Sibling residual that bounds F11's value (disclose):** the DF-5 defer-resend (`job_queue_service.py:2605-2609`) re-enqueues with `source="api"` → the child gets a **DIRECT** window (more authority than any chain), with no chain. Out of scope; record as a disclosed residual with an owner.

---

## 8. F6 — gate ordering: BINDING LEADER ADJUDICATION (recorded)

**The binding ruling (2026-10-24):** the target-confirmation step (agent-satisfiable `confirmed_target_id`) **STILL gates the `dry_run` preview**; **only** the user-provenance gate moves past the `dry_run` fork; the **destructive act keeps ALL gates exactly as-is**; `act_gate` diagnostics **ARE included in the preview body**.

**Plan-state verification:** `decisions.md` D-8 is now **already folded** to this minimal reading (current text: confirmation on BOTH paths; only `verdict.ok` apply-only; veto path preserved), and `phase3-plan.md` T3.6 / AC3.6 / T3.10 / plan-overview R6 are consistent. **No D-8 fold is needed.**

**Stale spots in `technical-analysis-ops-side.md` (SEVEN — iteration-003 corrected count; the original five missed two):** (1) §1 exec-summary F6 paragraph; (2) §3.1 "Reading applied here" (whole-gate); (3) §3.2 body line 10 (`PROTECTED GATE — APPLY ONLY`); (4) §3.3 per-guard table guard-10 row; (5) §3.4 sample payload (`protected_confirmation_required` / `confirmation:"required"` are unreachable under D-8 — a reachable preview must read `"provided"`, which the shipped code already computes at `instance_ops.py:717-721`; `would_refuse_with` must not list the confirmation token on the preview path); (6) §3.6 doc bullet; (7) §7.2 F6-i (split into F6-i(a) unconfirmed ⇒ refusal, F6-i(b) confirmed ⇒ preview + `no-user-provenance`); §9 Q1 is separately DECIDED (struck in place). **Remedy state:** the in-place bracketed `[CORRECTED per D-8]` annotations + "SUPERSEDED BY D-8" callouts at §3 and §7.2 have LANDED — verified 2026-10-24 at all seven spots plus §9 Q1 (the ops-side §3 callout carries the same seven-item enumeration, naming §1 and §3.3 as the two the iteration-002 sweep missed); Phase-2 T2.4's no-rewrite rule is honored via the DF-4 bracket precedent. Cross-file note: `phase5-plan.md` T5.2(c) carried the same stale phrasing and was swept in the iteration-001 fix pass. D-8 and phase3-plan.md T3.6 are the sole authorities; the §8 refinements (compute/write separation, anchored `_token_of`, token-only preview, F6-iv precedence inversion) are folded per the callout.

**Security findings (all confirmed):**
- `_protected_gate` is **PURE** — returns `str | None` built by `_refusal` (`instance_ops.py:199-201`, `:336-385`); the ledger write is at the **caller** (`audit.write_refusal`, `:568-581`). Calling it for diagnostics writes **nothing** → the §3.4 shape is safe **provided** compute and write stay separated: compute `gate_refusal` once; write a refusal only on the real refusal path (confirmation failures on both paths; provenance refusal on apply only); on a preview, render `act_gate_would_pass = {"ok": gate_refusal is None, "reason": _token_of(gate_refusal)}`.
- **Precedence inversion (disclose + pin F6-iv):** guards 8–9 now run before guard 10, so an exhausted budget/cascade on a protected target yields that token instead of `protected-target-confirmation-required`. Both are refusals (no authority granted), but it is a behaviour change outside the "provenance-only" claim → document and pin.
- **`_token_of` must be anchored to the envelope prefix** (`^Error: .*? REFUSED — reason=([a-z0-9-]+):`) — the gate message embeds `confirmed_target_id={…!r}` and a source hint; an unanchored search is a decoy risk.
- **No sensitive leak:** `act_gate` is static prose; `act_gate_would_pass` is a fixed token. The preview should carry **tokens only**, never the raw refusal string (it embeds the target id and the caller's own confirmation value).
- **F6 × F11 posture:** the preview will report `no-user-provenance`, which the operator cannot self-satisfy without a genuine user turn — correct, but the tool doc must say so plainly or the preview reads as "one more call from termination".

---

## 9. Phase sequencing + DF-4 sync gate — NOT SUFFICIENT AS WRITTEN 🟡

The three markers + DF-4's pins prove **DF-4's own closure**; they do not prove **F11's premises**. Add six cheap read-only checks to T2.2 (exact commands; journal outputs verbatim):

1. **Exactly two `agent:` mint sites** — `git grep -n 'f"agent:' <tree> -- 'daemon/*.py' 'daemon/**/*.py' | grep -v 'snapshot_executor\.py'` → must return exactly **two lines**: `daemon/tools/job_queue.py:1319` (the f-string sits on its OWN line inside the multi-line `source = (` assignment — the previous `source=f"agent:\|source="agent:` pattern cannot match it; the pattern therefore matches the f-string shape) and `daemon/tools/job_queue.py:2062` (tree-form output prefixes each line with the ref). The `snapshot_executor.py:172` exclusion is DOCUMENTED and load-bearing: that hit is the R8 snapshot auto-tag (`f"agent:{agent_id}"` in the tag list), NOT a dispatch-source mint — never broaden the pattern past it without re-adjudicating that hit. *(Verified 2026-10-24 at the base worktree tree and at `origin/latest` `d4ecdbd3d`: exactly the two mint lines; no single-quoted `f'agent:'` variants exist ✓)* A third site = a new mint lane to adjudicate before widening.
2. **HTTP reserved-source gate** — `sed -n '525,545p' daemon/routers/jobs_crud.py` → proves `"agent:"` → 422 (`constants.py:946`). *(Present ✓)*
3. **Message-lane metadata census** — every `enqueue_message(… metadata=…)` caller must be non-`agent:`-sourced (the message lane bypasses the job seam). *(Current: job_processor chain-forwarding by design; `instance.py` send_message `task_context` only; `job_queue.py:3272` no metadata ✓)*
4. **Remaining direct-window mint census** — `git grep -n 'source="api"' -- daemon/services/job_queue_service.py daemon/routers/jobs_crud.py` → record DF-5 defer-resend `:2608` + REST `body.source` as **disclosed residuals**, not silent non-goals.
5. **Machine-checkable marker count — SCOPED to code** — `git grep -c 'direct-provenance-insufficient' -- daemon/ tests/` → must output **nothing** (rc=1 = zero files) at the post-merge tree. The scope is load-bearing: the UNSCOPED form matches **7 files at every rev** (planning prose + `.agents/tester/RESULTS/**` — verified at base `b257842c9` and at the then-current `origin/latest`) and can never produce its own expectation; it was actively harmful under the `plan-overview.md:6` authority clause (this doc overrides phase2 where they differ). *(Verified 2026-10-24: scoped = 0 files at the then-current `origin/latest` (`d4ecdbd3d`, re-confirmed at the live `074833b45`); at base exactly 3 files — `daemon/tools/instance_ops.py` 2, `tests/unit/test_instance_ops_behaviour.py` 1, `tests/unit/test_instance_ops_registration.py` 3 — the delta is DF-4's closure, proving the check discriminates. The gate journals the CURRENT `origin/latest` SHA at run time — 0 files at whatever latest is at merge time, never an equality to a snapshot SHA.)*
6. **Real-PG pin run with counts** — run the DF-4 trio with the postgres marker enabled and journal `"N passed"`; on SQLite the postgres files collect **0 tests** = false green.

**T2.2(c) wording defect:** "exactly TWO conditions" currently lists **three** checks. Rewrite: "`verdict.pclass` absent from the gate body + the refusal-token set is exactly {protected-target-confirmation-required, confirmed-target-mismatch, no-user-provenance}".

**Phase sequencing:** 1 ∥ 2 → 3 → 4 → 5 is sound. Phase 2 remains a hard gate for F11/F6/F10/F9. Phase 4's dependency on both 1 and 3 is sound. The new F12 (if accepted) belongs to Phase 1 (read lane) and Phase 4 (pin).

---

## 10. Anchor-shift table (base `b257842c9` → `origin/latest` `d4ecdbd3d`) — authoritative

| Block | Plan / ops-side cite | Post-DF-4 | Δ |
|---|---|---|---|
| `_protected_gate` | `instance_ops.py:326-385` | **`336-385`** (`not verdict.ok` `:373`) | +10 |
| `_is_protected_target` | `:287-313` | **`:297-~323`** | +10 |
| module doc gate order | `:41-47` | **`:41-52`** | +5 |
| `CATEGORY_DOC` | `:110-120` | **`:108-130`** | −2 |
| terminate guards 1–7 | `:507-555` | `:497-555` | 0 |
| protected determination + gate call | `:557-583` | **`:557-583`** (`_protected_gate(` `:562`, `write_refusal` `:569`) | 0 |
| budget / cascade | `:585-642` / `:644-679` | `:586-642` / `:644-679` | 0 |
| `dry_run` fork / preview builder | `:696-737` | **`:696-737`** (`write_preview` `:699`) | 0 |
| terminate `_full_doc_` | `:798-853` | **`:798-851`** | −2 |
| reclaim / refusal / `_full_doc_` | `:895-989` / `:918-928` / `:991-1016` | **`:893-987` / `:916-926` / `:989-1014`** | −2 |
| nudge / `_full_doc_` | `:1048-1137` / `:1139-1157` | **`:1046-1135` / `:1137-1155`** | −2 |
| mark / `already_protected` / `marker-already-set` | `:1199-1366` / `:1232-1235` / `:1239` | **`:1197-1364` / `:1230-1233` / `:1237`** | −2 |
| park Tier-2 / Tier-1 audit writes | `:1281-1315` / `:1339-1350` | **`:1279-1313` / `:1337-1348`** | −2 |
| `stamp_user_origin_window` | `manager.py:4855-4917` | **`:4891-4953`** (clear `:4947`) | +36 |
| `_inherit_provenance_window` | `:4919-5020` | **`:4955-5056`** | +36 |
| depth-cap const | `:222` | **`:231`** | +9 |
| funnel (`_process_message_with_tracking`) | `:9554`, `:9620`, `:9628` | **`:9758`, `:9824`, `:9832`** | +204 |
| other direct funnel caller | `:13059` | **`:13263`** | +204 |
| `classify_user_origin` | `upgrade_journal.py:3376` | **`:3390-3440`** (`:3405`, `:3410`) | +14 |
| factor-2 read / restart gate / alias block | `upgrade_tools.py:3320` / `:2722` / `:1276`,`:1295` | **`:3323` / `:2725` / `:1279`,`:1298`** | +3 |
| `job_processor` dispatch sites | `:1001/:1093/:1327` | **`:1176/:1268/:1548`** | +175/+175/+221 |
| `job_queue_service` retry enqueue | `:2813` | **`:2824`** | +11 (one site) |
| `job_queue.py` (1319, 1422, 2056, 2062, 3272) | as cited | **unchanged** | 0 |
| `task_processor` / `message_processing_pipeline` / `instance_messaging` | `:496/:506/:555`; `:119-175/:419-450`; `:400/:2695/:3059` | **unchanged** | 0 |

Note: the ops-side References' gate-call cite (`:557-583`) was already written against a post-DF-4 tree; only the four clusters above are wrong. Phase 2's re-grep duty stands, but the plan's citations must not read as authoritative.

---

## 11. Test architecture — corrections 🔴/🟡

### 11.1 Net-new attribution ladder is WRONG (phase4 T4.7 / phase2 T2.5) 🔴

"leg A = `b257842c9` vs the Phase-2 merge commit ⇒ reds attributed to MERGED LANES" is invalid: base→M **contains our Phase-1 commits** (per T2.1, M is a `--no-ff` merge executed **on our branch**: M^1 = our Phase-1 tip, M^2 = latest). Our own Phase-1 reds would be misfiled as merged-lane reds and excused.

**Corrected ladder** (labels derived at run time from `git rev-list --parents -n1 M`, never from prose):
- **leg 0** = base `b257842c9` vs **M^1** → **OURS (Phase 1)**
- **leg A** = **M^1** vs **M** → **MERGED LANES** (+ conflict resolution)
- **leg B** = **M** vs **HEAD** → **OURS (Phase 3/4)**
- **containment** = base vs HEAD → overall `net_new = ∅`; add to AC4.7.

Mechanics confirmed: `comm -13 base head` = net_new (∅), `comm -23` = fixed; sorted inputs; **sets**, not counts; identical commands + pack list on every leg; detached worktrees + `uv sync --frozen` + byte-matched interpreter; 3× isolated re-runs for order/load-sensitive reds.
**Watch-list additions:** `test/packs/instance_tools_unit_test.sh` (legacy instance-category pack, same `list_instances` path), `tests/api/test_list_instances_source_param.py` + `test_instance_ui_prefs_api.py` (stub the facade), `tests/unit/test_manager_list_instances_source.py` (add the `status` forwarding assertion), and **any pin asserting the `instance_list` payload** — F4/F12 change payload **values**, so shape pins must be re-derived, not merely re-run.

### 11.2 The >window fixture is unbuildable as written 🟡

Real spawns are impractical for 1,001 rows: `spawn_instance_with_mcp` (`manager.py:9361-9428`) runs `ensure_mcp_preloaded` (external MCP discovery) and `spawn_instance` builds a full LangGraph per instance (`instance_lifecycle.py:2245-2266`) + a DB transaction → minutes-to-tens-of-minutes and MCP-dependent.

**Two-tier fixture:**
1. **Proof husks (2)** via the **full real spawn order** with the exact UUIDs (`_UUID_PATTERN`, `instance_lifecycle.py:867`, `:1967-1973`) + ONE backdate UPDATE of `created_at`/`updated_at`.
2. **Filler (≥1,001)** built with the **same production ORM constructor** `_spawn_instance_db_sync` uses (`instance_lifecycle.py:5608-5625`: `Instance(instance_id=…, agent_id=…, agent_dir=…, agent_name=…, parent_id=…, instance_metadata={}, version=1, created_at=…, updated_at=…, status=InstanceStatus.IDLE.value)`) in ONE session (`add_all` + commit) — production row shape + production INSERT path, minus graph/MCP.

**D-13 reading:** its foreclosure of "synthetic instance rows" applies to the **PROOF rows only** (the husks must be 100 % production-real); the filler's only job is to push the husks past the window boundary. State this reading explicitly or the fixture cannot be built.
⚠ **Format trap:** `created_at` is TEXT compared **lexicographically** by `ORDER BY created_at DESC` (`models.py:66-67`, no index) — the backdate must use the production format (`datetime.now(timezone.utc).isoformat()`), or the husks may not land at the tail and the F3×F4 pin goes vacuous. Filler `agent_id` must be non-KB.

### 11.3 Pin / mutation mapping — 4 gaps + 2 multi-behavior mutations 🟡

Phase-4 T4.4 lists mutations for F3/F2/F8/F1/F6/F11 only. **Add:** **F4** (a) force `truncated=False` → over-the-limit pin RED; (b) break the loop break-condition → "oldest not covered" pin RED. **F5** — a key-presence mutation proves nothing: pin `dead_jobs_total == Σ per-queue dead` **and** the docstring's two-table distinction. **F7** — un-single-home the reflection → static-marker case RED. **F10** — drop the echoed row id → id pin RED. **Split multi-behavior mutations:** F2 into (a) windowed loop (coverage pin), (b) drop the independent total (`reconciled` pin), (c) hard-code `scan_truncated=True`; F8 into (a) MCP discovery must not fire, (b) row byte-identical. **Also add (if F12 accepted): same-status forward removed → filtered-page pin RED.**

### 11.4 Missing measurement task 🟡

Phase 5 T5.1 depends on "the Phase-4 MEASURED full-scan wall-time number", but phase4 T4.1–T4.7 contain **no timing task**. Add **T4.8**: measure per-window query count + wall-time on the >window fixture; record both; apply the §4 failure thresholds.

### 11.5 Pack conventions — verified, one gap 🟢

`test/packs/instance_tools_unit_test.sh` (56 lines): `set -euo pipefail` + `IFS` `:26-27`, `SCRIPT_DIR`/`PROJECT_DIR` `:30-33`, `timeout 210s .venv/bin/pytest <explicit file> … --override-ini="addopts=" --timeout=210 -p no:cacheprovider`, RC 0/1/124 ✓. `ensure.md` (53 lines): pack-mapped `:4`, no bare `pytest` `:6`, quarantine `:7`, no `-x` `:8`, `PYTEST_TIMEOUT=280` `:39` ✓ — **cite correction: the dual-layer rule is `:6` + `:45`, not `:16-20`**. PACKS.md row = 5 columns ✓. No `instance_read`/`instance_ops` pack exists today → this is the first.
**Gap:** the template hardcodes `.venv/bin/pytest`; a fresh worktree has **no venv** (house rule: lazy per-worktree `uv sync`, never reuse the main `.venv`). The pack must assert `.venv` exists in-worktree (or document the `uv sync` prerequisite), or it silently runs the main checkout's interpreter.

---

## 12. Critical gaps (ranked) and decisions

| ID | Gap | Severity | Owner / action |
|---|---|---|---|
| G-1 | **F12 — `status` filter dropped at the lifecycle→repo seam**: makes F4's `truncated` and F2's reconciliation note false today; tool docstring false | 🔴 | **Leader/orchestrator decision on scope (11→12 defects).** Recommended: include (1 line + seam pin). Fallback documented in §3.1 |
| G-2 | **F11 — `job_continue` chain erased by the S3 pop** as written (no `instance_messaging` touchpoint) | 🔴 | Fold into T3.1/T3.3 (§7.3) — architecture decision, recorded |
| G-3 | **F3 — reuse premise false**; evidence expression must be single-homed | 🔴 | Fold into T1.3/D-1 (§1) |
| G-4 | **Net-new attribution misattributes our Phase-1 reds** to merged lanes | 🔴 | Fold into T4.7/T2.5 + AC4.7 (§11.1) |
| G-5 | F4 scan loop can silently skip the oldest rows under concurrent writes | 🟡 | Fold into T1.2/D-3 (§3.2) |
| G-6 | N+1 unbounded + no measurement task + memory claim false | 🟡 | Fold T1.2 (budget/stream) + new T4.8 (§4) |
| G-7 | Ops-side §3/§7.2 still encode the superseded whole-gate reading | 🟡 | In-place bracketed corrections + callout (§8) |
| G-8 | >window fixture unbuildable as written (D-13 wording) + TEXT date trap | 🟡 | Fold into T4.1/T4.2 (§11.2) |
| G-9 | F8 payload delta `agent_tag`/`agent_dir` undisclosed; resolver must reproduce 4 refusal texts | 🟡 | Fold into T1.7/T1.8 (§5) |
| G-10 | F1 D-6 ↔ SC6 contradiction | 🟢 | Amend SC6 wording (§6) |
| G-11 | 4 defects lack mutations; 2 mutations multi-behavior | 🟡 | Fold into T4.4 (§11.3) |

**Decisions pending (leader-level):**
1. **F12 inclusion** — scope change 11→12 defects; recommended YES because two shipped fixes otherwise rest on false claims. If NO, the fallback in §3.1 is mandatory.
2. **F6 documentation fold** — in-place bracketed corrections to the dated ops-side §3/§7.2 + callout (recommended) vs callout-only; the binding ruling itself is already in D-8/phase3 and is not in question.
3. **N+1 gate posture** — hard gate on per-window query count (≤ `FLEET_SCAN_LIMIT + 2`), recorded number on wall-time with the 10 s budget as ceiling (recommended).

**Architecture decisions taken here (within mandate):** F3 evidence-expression extraction with `child_reports` untouched in-scope (optional clean-up routed); F2 one-method/two-statements/one-session + shared KB filter + status-only `idle` bucket; F4 first-window total + id-keyed dict + `scan_stop_reason`; F11 `dispatch_chain` kwarg through `enqueue_message_job` + single top-of-`enqueue` normalization; F11 freeze-list additions; F6 compute/write separation and token-only diagnostics; Phase-2 six-check gate; corrected net-new ladder.

**Confidence:** High on G-1/G-2/G-3/G-4 (each independently code-verified, two by me directly). Medium on the N+1 wall-time estimates (no PG contact permitted — the Phase-4 measurement is the arbiter). Assumption that would flip the F11 plan: if a third `agent:`-source mint site exists at implementation time, re-adjudicate before widening (check #1 of §9).
