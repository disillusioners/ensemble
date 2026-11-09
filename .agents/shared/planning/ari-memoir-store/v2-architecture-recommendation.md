# Memoir Store v2 "The Reflecting Mind" — Architecture Recommendation (Architect Enrichment)

**Lane:** `feature/memoir-v2-reflecting-mind` @ worktree `/home/nea/ensemble-src-wt-memoir-v2`
**Authority position:** Level 4 in the `v2-decisions.md` §1 stack (Architect Adjudication) — supersedes Level-5 plan text surgically (P-4); cannot touch R1–R4 or the blessed scope (P-5).
**Status:** COMPLETE. Authored 2026-11-09. ADDITIVE file — no planner document was modified; §4 is the corrections register the planner/developer folds in.
**Basis:** 3 dispatched analysis workers (structural / data-flow / resilience) over the v2 plan set + live code, all claims re-verified by grep at write time (P-7). Instance IDs: `f8710abb` (structural), `7fc7a9b6` (data-flow), `9f0fa0a1` (resilience).
**Degradation disclosure:** the three worker dispatches ran WITHOUT skill-body injection (skill attachment requires a free target; the auto-dispatched tasks occupied the turns). Reports were adjudicated on evidence quality — all three are file:line anchored and passed scrutiny. Run marked `DEGRADED — skill injection missed at dispatch (3 workers)`; no analysis gap resulted.

---

## §1 — Ruling Table (Q1–Q9, one line each)

| Q | Verdict | The ruling in one line |
|---|---------|------------------------|
| **Q1** wake-refresh lane | **ADOPT Option B, AMENDED** | Parallel stamped refresh block (`CONTEXT_KIND_MEMOIR_REFRESH`, stable id `memoir_refresh:{instance_id}`, **no once-flag**), gated on a NEW `wake_source` param threaded from the dispatch site — the signal does not exist at the assembly seam today. |
| **Q2** trigger + warm-up | **ADOPT constants, AMEND mechanism** | Sum ≥ 25 over `COALESCE(poignancy, importance)` of unreflected episodes (DERIVED per tick — the plan's in-memory rolling counter / V2-D 4.5 delta-notify is DELETED); base 60 s, double-on-arrival to ceiling 960 s, reset after pass; NEW force-fire at 3600 s. |
| **Q3** budget numerology | **ADOPT numbers + enforcement table** | 5 syntheses/pass, 1000 chars (REJECT not truncate), 4 LLM calls (dispatch-time increment), 60 s tick budget, `httpx.Timeout(45, connect=5)`, 2-day scan window, nightly 30 episodes 02:00–04:00 UTC — partial-commit everywhere, abort nowhere, defer on time; plus a dedup key on insert. |
| **Q4** JEV placement | **ADOPT** | `memoir_persona_gate.py`, two-stage (rule-filter zero-LLM exit → LLM classifier), separate call after synthesis, exercised from the reflection **SERVICE** (never the tool module); classifier failure parks the candidate (no silent `none`-drop). |
| **Q5** two-tier summarize | **ADOPT thresholds, AMEND carriers** | 5 fragments / 1200 chars / 24 h cooldown — fragments = `COUNT(*)` of `memoir_line` rows (never `SUM(mention_count)`); cooldown carried by NEW `memoir_star.description_as_of` (also the v4 derivation watermark). |
| **Q6** cost bound | **ADOPT** | Structural clamps + `estimate_tokens` metering (clamp-then-meter) + structured per-pass log line; no ledger table; restart-queryability of the log is a recorded v3 boundary. |
| **Q7** refusal mapping | **ADOPT mapping** | All six target codes verified in the 19-frozenset; zero-episode star = empty-dossier SUCCESS (not `memoir_not_found` — V-1 truthfulness); no 20th code. |
| **Q8** scope boundaries | **ADOPT as recorded** | Blessed scope governs; `memoir_thread` + searchable archive → v3 under R3. No amendment. |
| **Q9** import semantics | **ADOPT as advisory** | One-line writer convention lands now: an imported belief = NEW local synthesis row, verb `CORROBORATE`, citing the foreign original; chains stay kingdom-local; no enforcement machinery in v2. |

---

## §2 — Full Rulings

### V2-Q1 — Wake-Refresh Delivery Lane: **Option B, amended with a required `wake_source` thread**

**Ruling text:** Implement `_maybe_memoir_refresh_message` in `daemon/services/context_messages.py`, evaluated at BOTH assembly sites (refresh branch ~`:2564` beside `_maybe_memoir_briefing_message`; turn-1 path ~`:2928`), emitting a context block with `context_kind="memoir_refresh"` and stable id `memoir_refresh:{instance_id}` via `_make_context_message` (so `additional_kwargs` carries `injected_message=True, context_kind, source="memoir_refresh"`). Gate order: ari-only (factory-captured agent id) → `ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED` (DR-3) → `wake_source == "scheduler"` → build. **No once-flag** — the refresh must re-fire on every qualifying wake; dedup across turns is structural (`add_messages` supersede-by-stable-id collapses N refreshes to one current row; `is_hoisted_injected` truthy-keys on any `context_kind`, so compaction hoists it verbatim with zero new plumbing).

**The amendment (load-bearing):** `assemble_context_messages` has NO source parameter today (`context_messages.py:2362-2375`); the wake signal exists only at the caller (dispatch holds `message_source`, `instance_messaging.py:4005-4029`; the scheduler enqueues `source="scheduler"`, `scheduler.py:2112`). Thread a `wake_source: str | None` param from the dispatch site → assemble → the refresh builder. Do NOT gate on `instance_metadata.original_source` — it is first-write-only (`:4025`) and identifies the session's founding source, not the current wake.

**Why Option A is rejected (evidence):** `compose_alarm_message` (`self_alarm_service.py:603-653`) composes at ARM time — briefing text baked there freezes memory content at arm time (potentially days before the wake); and `scheduler` is DELIBERATELY absent from `INTERNAL_STAMPED_SOURCE_PREFIXES` (`constants.py:1376-1399`, pinned OUT by `test_source_stamp_matrix_d4i.py:116-126` `_NEVER_STAMPED` and `test_source_stamp_never_stamped_guards.py`) — the alarm rides bare `MessageType.HUMAN` to anchor attestation source U. Embedding system-composed memory into that body would masquerade memoir content as user-authored. The refresh block stays a SEPARATE parallel message; the wake itself is never stamped. The stamp pin survives untouched.

**Checkpoint interplay (settled):** `CHECKPOINT_TTL_HOURS=168` (`constants.py:170`) is irrelevant to the reflection worker (daemon-scope, no graph tasks). For wake-refresh: the block is rebuilt per wake from live state (never baked into turn history), and stable-id supersede means a long-lived revived session carries exactly one current refresh row regardless of wake count.

### V2-Q2 — Poignancy Reflection Triggering & Warm-Up: **constants adopted, mechanism replaced**

**Numbers (with anchors):**
- `MEMOIR_REFLECTION_TRIGGER_IMPORTANCE_SUM = 25` — Generative-Agents cumulative-importance triggering scaled to ensemble's 1–10 episode scale: 25 ≈ three milestone episodes (8–9) or five average ones (5). It is also ≈ one full nightly window of typical daily volume, so in-day reflection fires roughly once per meaningful day-part rather than continuously.
- `MEMOIR_REFLECTION_IDLE_BASE_S = 60.0`, ceiling `960.0`, doubling on arrival (tencentdb `pipeline-manager` warm-up shape): intervals 60→120→240→480→960 s.
- **NEW** `MEMOIR_REFLECTION_FORCE_FIRE_S = 3600.0` — if the trigger sum has been ≥ 25 for a continuous hour without an idle window firing, fire anyway. Without this, a never-idle stream defers reflection forever (the 960 s ceiling bounds the window, not the deferral). This constant closes the starvation hole the plan leaves open.

**Mechanism (amended — this deletes two plan fragments):**
1. **The rolling counter is DERIVED, never stored in memory.** Each tick: `SELECT COALESCE(SUM(COALESCE(poignancy, importance)),0), MAX(created_at) FROM memoir_episodes WHERE agent_id=:ari AND reflected_at IS NULL AND created_at >= now()-interval '2 days'`. The plan's in-memory rolling counter (phase-2 T2.1) and the V2-D 4.5 delta-notify (`score - 5` per re-score) are **DELETED**: the delta-notify double-counts on any re-score (the write-time 5 was already counted), dies on restart (episodes scored while down never fire), and couples two service lifecycles. A derived SUM is restart-durable, immune to re-scores (it reads current values), and self-resets when a pass marks rows.
2. **Idle** = `now - MAX(created_at of the unreflected set) >= debounce_s`. A new episode arriving during an active window sets `debounce_s = min(debounce_s * 2, 960)` and restarts the idle clock. Reset to base at pass completion.
3. **Effective importance** = `COALESCE(poignancy, importance)` — see the importance ruling in §3-A2; episodes score at their write-time `importance` until the poignancy worker refines them into `poignancy`.
4. Edge cases ruled: boot with a large unscored backlog may fire an early pass ~60 s after boot (benign — boot-catch-up analog; log it); nightly-before-in-day ordering inside the tick is load-bearing (nightly advances the same `reflected_at` marks, so the in-day branch then sees sum < 25 and idles).

### V2-Q3 — Budget Numerology & Enforcement Points: **numbers adopted, enforcement table binding**

| Bound | Constant | Check point | Breach behavior |
|---|---|---|---|
| Max syntheses / pass | 5 | accept-loop, post-validate / pre-insert | Stop accepting; **keep** committed rows; log `synthesis_cap_hit kept=5 dropped=K` |
| Max chars / synthesis | 1000 | validator, BEFORE insert | **REJECT the row** (`claim_too_long` skip + log). Truncation manufactures a different proposition — the house truncation marker is a read-path display device, never storage. The call is spent either way; a rejected row costs nothing extra, a truncated belief costs correctness forever |
| Max LLM calls / pass | 4 | call site, **increment at dispatch** | Stop the pass; keep committed; log `call_cap_hit`. Dispatch-time increment so a hung call cannot leak the count. The JEV classifier call counts as one of the 4 when fired; it never counts against `max_syntheses` |
| Tick budget | 60.0 s | per-item pre-dispatch (`remaining <= 0 → break`) | **Defer** remainder to the next pass; count deferred in the pass log line (embedder precedent `memoir_embedding_worker.py:502-514`) |
| Per-call timeout | `httpx.Timeout(45.0, connect=5.0)` | client construction | Per-call failure ⇒ attempt++ (must be strictly < tick budget or the tick guard can never fire) |
| Scan window | 2 days | SQL predicate in the repo scan | The window IS the bound |
| Nightly cap | 30 episodes | scan `LIMIT` + accept loop; window checked at pass start | Stop at 30; log deferred. A 03:59 start remains bounded by the tick budget |

**Constitutional stance:** partial-commit everywhere, abort nowhere, defer on time. A reflection pass must never become a rewrite hazard — the committed prefix is durable truth; the unprocessed remainder is re-derivable state.

**Additional guard (new):** dedup on insert — `sha256(claim_normalized + sorted(source_episode_ids))` skip-check at the synthesis repository. Restart mid-pass re-processes the batch (at-least-once); the dedup key converts re-delivery into a no-op instead of duplicate claims. Without it, watermark safety is illusory.

### V2-Q4 — JEV Classifier Placement & Write Purity: **ADOPT, with parking amendment**

Placement: `daemon/services/memoir_persona_gate.py`, called by `memoir_reflection_service.py`. Two-stage: (1) rule-based diff filter — formatting/whitespace/verbatim-substring overlap ⇒ `reinforcement` or `none`, zero LLM calls, immediate exit; (2) LLM classifier returning `{category, reasoning}` only when the candidate introduces novel behavioral claims. Runs as a separate modular call AFTER synthesis, only if reflection produced a candidate persona belief.

**Write-purity pin (ruling):** the JEV gate, `mark_superseded`, and `MemoirPersonaRepository.create_draft(source="ari")` are exercised from the reflection **SERVICE layer, never the tool module** (`daemon/tools/memoir.py` stays read-path + hot-append pure). `mark_superseded` exists today uncalled (`repository.py:896`) precisely so a future service could own it; v2's reflection service is that owner.

**Failure isolation (amendment):** ordering is the fix — *commit synthesis → constellation line → JEV gate → keepsake proposal*. The synthesis is the durable artifact; every downstream step is a consumer whose failure must never roll it back. Classifier failure (LLM down / parse-fail): the plan's default-to-`none` (T5.2) is fail-safe but silently lossy — instead PARK the candidate (synthesis id + gate attempts + last error in a small candidate record), retry next pass, terminalize at 5 attempts as `terminal:gate_unavailable` so the loss is visible, not silent.

### V2-Q5 — Constellation Two-Tier Thresholds: **ADOPT with two definitional amendments**

`MEMOIR_STAR_SUMMARY_MIN_FRAGMENTS = 5`, `MEMOIR_STAR_SUMMARY_MIN_CHARS = 1200`, `MEMOIR_STAR_SUMMARY_COOLDOWN_HOURS = 24`, tier-2 card ~300 words — all sane against the schema, with:
1. **Fragments = `COUNT(*)` of `memoir_line` rows for the star** (distinct citing episodes). Never `SUM(mention_count)` — one heavily re-mentioned episode must not alone graduate a star (LightRAG M4 accumulate-then-synthesize: accumulate across *observations*, synthesize over *coverage*).
2. **Cooldown carrier (new column):** `memoir_star` has no column that can encode "last summarized" (`last_touched_at` updates on ANY touch). Add `description_as_of DATETIME NULL` — it carries the 24 h cooldown state AND the derivation watermark the v4 §5 markdown+hash mirror needs (a naked regenerated description is unverifiable). Tier-1 below threshold: `memoir_about` plain-joins recent episode excerpts; `description_as_of` stays NULL until first tier-2 summarization.

### V2-Q6 — Cost-Bound Composition: **ADOPT (composed bound, no ledger)**

Structural clamps (5×1000 chars, 4 calls, 45 s/call, 60 s tick, 2-day window, 30/night) ⇒ worst case ≈ 4k chars of claims per pass — deterministic without any USD ledger. Meter with `daemon.loader.estimate_tokens` (`loader.py:1053-1067`, tiktoken `cl100k_base`, local CPU, zero LLM calls — already the house estimator, imported by `memoir_briefing.py:174`). Log every pass: `[MemoirReflection] pass=completed units=K prompt_tokens=X comp_tokens=Y duration=Z.Zs syntheses=S kept=D dropped=R`. Amendments: (a) **clamp-then-meter** — meter the bytes actually shipped, or log and reality diverge; (b) the log line is not queryable across restarts — record that boundary now; dashboards/alerting on reflection cost are a v3 decision; (c) NO ledger table — it would be an unblessed write surface for marginal value.

### V2-Q7 — `memoir_about` Refusal Vocabulary: **ADOPT mapping onto the frozen 19**

All six target codes verified present in the `MEMOIR_CODES` frozenset (`memoir.py:211-238`): `memoir_not_found`, `memoir_query_required`, `memoir_stars_out_of_bounds`, `memoir_disabled`, `memoir_not_authorized`, `memoir_internal_error`. Mapping: star-not-found → `memoir_not_found`; empty star → `memoir_query_required`; >100 chars → `memoir_stars_out_of_bounds`; disabled/unauthorized/internal → their codes. **Ruled edges:** a resolved star with ZERO linked episodes is an **empty-dossier SUCCESS render** (sections say "none"), NOT a refusal — reaching for `memoir_not_found` there would mislabel a real entity as absent (the V-1 truthfulness lesson: codes must not mislabel); no type-filter condition exists in the blessed signature (`memoir_about(star: str)`) so no code is needed for it. `assert len(MEMOIR_CODES) == 19` untouched.

### V2-Q8 — Scope Boundaries: **ADOPT as recorded**

Blessed scope (2026-11-09) governs. `memoir_thread` and the searchable archive tier are deferred to v3 under R3 — episode-to-star lines + synthesis citations already provide episodic connectivity. No amendment.

### V2-Q9 — Foreign Belief Import Semantics: **ADOPT as advisory one-liner**

Lands NOW as a writer-level convention on the fresh syntheses writer (zero machinery): any imported belief is recorded as a NEW local synthesis row, verb `CORROBORATE`, `source_episode_ids` citing the foreign original (a dedicated citation form may be needed in v4; in v2 the convention is doc + writer discipline); never a silent copy; supersedes chains stay kingdom-local. No v2 code path can emit a foreign import (no federation exists — R1), so advisory strength is correct; the convention exists so the v2 writer is never *shaped* to allow silent copies.

---

## §3 — Architecture Addenda (depth the plan lacked)

### A1. Reflection Engine Component Shape

**Worker:** `MemoirReflectionWorker` clones `MemoirEmbeddingWorker` exactly — lifespan task `app.state.memoir_reflection_worker_task` (registration + cancel/await shutdown in `daemon/api.py` lifespan), dedicated `ThreadPoolExecutor(max_workers=1, thread_name_prefix="memoir-reflect")`, per-tick knob re-read with park/resume, tick-loop exception isolation, `asyncio.to_thread` FORBIDDEN (D3.5). **No scheduler row, no event-pushed timer** — a scheduler row would enqueue an unstamped bare-HUMAN agent turn (wrong layer, wrong attestation semantics); an event-pushed timer breaks restart survival.

**Tick body (single sequential — the two cadences cannot race by construction):**
1. Re-read knob; if OFF, park-log and sleep.
2. **Nightly gate first:** `02:00 <= now < 04:00 UTC AND last_nightly_date != today` → run nightly pass (bounded: 2-day window, 30-episode cap, 5/4/1000/60s budgets) → set `last_nightly_date = today`. A restart at 02:30 runs nightly on its first tick; a daemon down the whole window misses until the next night (nightly is bounded consolidation, not a deadline — documented, no catch-up complexity).
3. **Else in-day evaluation:** derive `SUM(COALESCE(poignancy, importance))` + `MAX(created_at)` over `reflected_at IS NULL AND created_at >= now-2d`; if sum ≥ 25 and idle ≥ `debounce_s` (or force-fire elapsed) → run in-day pass.
4. Pass completion: mark scanned episodes `reflected_at = <pass_ts>` (only on valid parse — an empty synthesis list counts as considered), reset `debounce_s` to base, clear claim.

**Durable vs derived state:**
- Durable: `memoir_episodes.reflected_at` (the per-episode claim — `embedded_at` precedent, operational nullable, one named writer) + one `memoir_reflection_state` row (`id='ari'`, `last_nightly_date`, `claimed_at`). Both ride the phase-1 migration. **There is no durable rolling counter** — the trigger is derived per tick.
- Derived: trigger sum, last-write time, idle window. In-memory: only `debounce_s` and park/latch streaks (crash-losing them is benign — re-derived).

**No-double-fire (three layers):** (1) structural — both cadences inside one sequential tick body; (2) `asyncio.Lock` at pass entry (insurance against future refactor); (3) DB claim — `reflection_state.claimed_at` with stale-takeover after 15 min (> max pass wall-clock ≈ 4×45 s), so a restart mid-pass cannot double-fire before the original claimant finishes. Watermark/claims advance only at pass completion ⇒ crash-safe at-least-once; the Q3 dedup key converts re-runs into no-ops toward at-most-once.

**Pause-first-then-quiesce:** orthogonal by construction — the worker is a daemon-level task, not an agent instance; `pause_instance_cascade` cancels graph tasks and reflection never enters a graph task. Ari being paused neither pauses nor strands a reflection pass.

**Checkpoint retention:** no interaction with the worker (it writes DB rows, not checkpoints). Wake-refresh interplay settled in §2-Q1.

### A2. The Bounded Transaction Story — mutation doctrine + THE IMPORTANCE RULING

v1's constitution is already two-tier: record columns written ONCE by the single INSERT (`RECORD_COLUMNS`, pinned at `tests/unit/test_memoir_write_purity_unit.py:62-71`) vs operational families with exactly one named writer each. v2 extends this shape; it invents no third kind.

**Mutation doctrine table (binding):**

| Surface | Class | Guard |
|---|---|---|
| synthesis INSERT (all verbs) | APPEND | non-empty `source_episode_ids` + verb CHECK (table constraint) + dedup key (Q3) |
| REFINE/CORRECT supersession | APPEND + POINTER | ONE writer `supersede_with(old_id, new_row)` in ONE transaction (below) |
| `memoir_line` edge existence | APPEND | `uq_memoir_line_star_episode UNIQUE (star_id, episode_id)` |
| `memoir_line.mention_count` / `last_mentioned_at` | ACCUMULATE (derived) | dialect-aware `ON CONFLICT` + atomic batch + pass-stamp conditional (below) |
| star / alias creation | APPEND | agent-scoped UNIQUEs (`uq_memoir_star_agent_name`, `uq_memoir_alias_agent_alias`) |
| `memoir_star.heat` / `last_touched_at` | ACCUMULATE | kingdom-local attention; extraction ≠ access; never mirrored (v4 §5) |
| `memoir_star.description` re-summarization | ACCUMULATE + REGENERABLE | `description_as_of` watermark + 24 h cooldown; derived (LLM condensation of cited episodes) — no supersession chain; stays home in the v4 mirror |
| episode INSERT | APPEND | unchanged v1 |
| poignancy score | ACCUMULATE, single writer | see the importance ruling |
| persona proposals | APPEND | pre-staged state machine; HUMAN-lane approve; no draft→approved edge |

**Doctrine statement:** the epistemic record (episodes, syntheses + supersedes chains, star identity, aliases, line-edge existence) is append/supersede-only and mirror-eligible (v4 §2 sync candidates); operational/derived state (heat, access stats, mention counts, timestamps, descriptions, rankings) accumulates in place and stays kingdom-local. This is DD-8 honored without amendment.

**Supersession atomicity (plan defect — corrected):** the plan's two-call shape (`create_synthesis` then `mark_superseded`) leaves an unlinked fork if the process dies between them — exactly what v4 §5 row 2 forbids. Clone the house atomic shape — persona `_transition` (`repository.py:252-308`): ONE writer `supersede_with(old_id, new_row)` performing insert + old-row `superseded_by` pointer + new-row `supersedes` back-pointer in ONE `engine.begin()`. `mark_superseded` itself remains service-owned and uncalled from tools.

**`memoir_line` upsert under concurrent passes:** double-counting is real (a pass that writes lines then crashes before marking episodes re-processes them on retry; `+= 1` inflates). Three-layer guard: (1) atomic batch — per-episode line writes AND the `reflected_at` mark in ONE transaction; (2) dialect-aware `ON CONFLICT` per the `watcher_repository.py:44-55` template (PG + SQLite both support `on_conflict_do_update`; select on `engine.dialect.name`); make any true increment conditional on a pass stamp (`WHERE last_pass_id IS DISTINCT FROM :pass_id`) so a same-pass re-run is a no-op; (3) watermark disjointness — the two cadences consume disjoint windows from one cursor (nightly-first ordering, §A1).

**THE IMPORTANCE RULING (supersedes V2-10's storage detail — the one ruling with blessed-scope proximity, flagged for leader visibility):** the plan's "atomically update `importance = score`" (`v2-phase4-plan.md` T4.x, V2-D 4.x) is **REJECTED as specified**. Ruling: `importance` stays **write-frozen** (ari's authored, bounded 1–10 judgment at INSERT — the record); the LLM score lands in a NEW `poignancy INTEGER NULL` column; every consumer reads **effective importance = `COALESCE(poignancy, importance)`** (recall tie-break, reflection trigger, keepsake eligibility). Grounds: (1) updating `importance` in place REDs the frozen `RECORD_COLUMNS` purity pin and silently amends DD-8's write-once record contract — a loud constitutional amendment would be required, and none is justified by the blessed scope; (2) authority — replacing an authored judgment with a model guess on historical rows is a downgrade; (3) auditability — declared vs inferred divergence becomes a visible signal (valuable for keepsake governance: what ari HERSELF called important); (4) the blessed capability ("scored 1–10 by an LLM in a background worker, defaulting to 5 at write time, feeds recall tie-breaks / reflection / keepsakes") is preserved in full through the COALESCE consumer. Semantic compliance with Level 2, surgical supersession of Level 5. The closed-surface pin (`D2_1_SURFACE` exact-set, `:124-136`) is amended DELIBERATELY either way (new named writers are the point of the pin) — rationale recorded in-test, never silently.

### A3. Poignancy Worker & the Backfill Ruling

**Worker:** clone of the embedder — tick 20 s, batch 10, attempts cap 5, terminal markers (`terminal:max_attempts_exceeded`, `terminal:parse_failure`), structured line `[MemoirPoignancy] pending=N scored=K failed=F terminal=T duration=X.Xs`, own executor `memoir-poignancy`. Queue = `poignancy_status IS NULL AND poignancy_attempts < 5 ORDER BY created_at ASC LIMIT 10`. On success: `poignancy = score, poignancy_status = 'scored'` (**NOT `importance`** — §A2). The queue is restart-durable by construction (column emptiness), so episodes written while the worker was down are picked up on the next tick — backfill of *new* rows is automatic and correct.

**The v1-corpus backfill storm — ruling: seed legacy; backfill is opt-in; full scoring REJECTED.** When `poignancy_status` lands, every pre-existing episode is NULL ⇒ the whole historical corpus becomes the LLM queue (drain ceiling ≈ 43,200 episodes/day; dev/demo hosts and future imports make this real even though live prod holds only ~10 episodes today). The decisive objections are authority and doctrine, not cost: (a) mass-rewriting `importance` violates the record contract (§A2); (b) writer-declared importance is an authored bounded judgment — re-scoring history replaces it with guesses; (c) the embedding lane's companion rule "**v1 ships NO automatic backfill**" (`docs/runbooks/memoir-embedding.md:67`, worker D3.15) was not inherited when the column-emptiness doctrine was copied; (d) reflection coupling — a mass backfill pumps the trigger sum past 25 instantly (10 prod rows already sum to 82) and the debounce degenerates into a fixed-interval synthesis generator about stale events.

Mechanics: (1) **legacy seed** — `UPDATE memoir_episodes SET poignancy_status='legacy' WHERE poignancy_status IS NULL AND created_at < '<CUTOVER_ISO_LITERAL>'` (cutover = migration authoring date, baked into the statement). The predicate MUST be cutover-bounded, never bare-NULL: `_ensure_postgres_columns` runs on EVERY boot (`manager.py:8166`), so a bare-NULL seed would mark brand-new rows legacy before the worker ever sees them. The predicate is idempotent (re-runs match nothing new). (2) **opt-in backfill knob** `MEMOIR_POIGNANCY_BACKFILL_MAX` (default **0** = disabled; budget-bound class per §A4): oldest-first N per pass over the cutover-bounded legacy set only — an operator lever for genuine history scoring, never a default.

**Missing column (plan defect — new, found at verification):** phase-4's worker query and AC3 reference `poignancy_attempts`; phase-1's DDL/schema section never creates it (0 occurrences, verified). It must join the phase-1 migration + ensure + model as `INTEGER NOT NULL DEFAULT 0`.

**Failover classification:** `invoke_raw_with_failover` is sync, blocking, and re-raises whatever survived its budget without cause classification (`llm_failover.py:771-772`) — the worker MUST classify exceptions itself, cloning `classify_embed_failure` (`memoir_embedding_worker.py:300-328`): 401/403 ⇒ immediate terminal + latch; other non-transient ⇒ terminal; connection/timeout/retryable ⇒ attempt++.

### A4. Failure & Isolation Domains + Knob Truth Tables

**Reflection LLM failure modes (ruled):**
- **Timeout mid-pass** (e.g. after 3 of 5 syntheses): committed rows are durable (DB write happens after the await — timeout commits nothing for the in-flight call); the unmarked remainder re-runs next pass; the Q3 dedup key makes re-processing idempotent. Tick budget is enforced by `asyncio.wait_for` around the executor future — the thread is not killed, so a timed-out LLM call may still complete, but its result is discarded (write-after-await).
- **Garbage JSON:** batch-level parse failure ⇒ attempt++ on the batch, do NOT mark reflected, do NOT fail the pass. Per-synthesis parse failure inside a valid envelope ⇒ skip that synthesis, count it. CAVEAT: `_extract_json_object`'s last-`}` fallback can return a TRUNCATED object — validate envelope completeness (episodes list + syntheses list present) before accepting.
- **Model refusal** ("I cannot…") ⇒ treat as valid-empty (zero syntheses), mark the batch considered — never a retry loop (a refusal is deterministic).
- **Confidence missing/out-of-range** ⇒ clamp to `[0.1, 1.0]` and log; do not reject the synthesis (clamping a scalar is not truncating a claim; AC2 already specifies clamping).

**JEV + keepsake isolation:** ordering per §2-Q4; `notify_user` refusal is DATA, never an exception (the tool never raises; catch-all converts to `notify_internal_error`, `notify_user.py:519-533`; `notify_no_bound_chat_source` is structurally normal for a background session — log once per day, escalate to briefing only). **Ledger discipline:** the date-keyed 1/day notification ledger is consumed ONLY on an accepted enqueue (mirror the N3 "only accepted rows consume budget" rule) — a refusal that burns the day's slot starves a legitimately-bound day; an unbound session would refuse every day forever.

**Kill-switch truth tables (binding — three knob classes, one classifier each, model layer NEVER raises, boot NEVER aborts):**

| Class | Knobs | Unset | Garbage / out-of-range |
|---|---|---|---|
| Enabled flags | `ENSEMBLE_MEMOIR_REFLECTION_ENABLED`, `ENSEMBLE_MEMOIR_POIGNANCY_ENABLED`, `ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED` | ON + one WARNING naming the var (DR-3 verbatim) | fail-CLOSED OFF + WARNING naming the value |
| Budget bounds | syntheses/pass, chars/synthesis, LLM calls/pass, nightly cap, backfill max, trigger sum, star-summary thresholds | constitutional default + **aggregate WARNING naming every unset budget knob** | **clamp to default** + WARNING naming value AND default. NEVER zero/disabled — a zero cap masquerades as a kill-switch and violates one-truth-per-concept |
| Cadence tunables | tick intervals, timeouts, debounce base/ceiling, force-fire | default, **silent** (tuning, not governance) | default + WARNING |

Implementation: ONE shared `_classify_numeric` helper in `daemon/config.py` consumed by a `mode="before"` Pydantic validator on every v2 numeric field; ONE shared worker-side per-tick reader (today `_env_int` is module-private in the embedder and the plans copy it twice more — three copies of a truth-table reader is exactly the drift class DR-3 exists to prevent). **Do NOT use bare `Field(gt=...)`** for v2 numerics — that is the pre-existing boot-abort defect (garbage numeric ⇒ `ValidationError` ⇒ unguarded `load_config` at `api.py:533`/`__main__.py:402` ⇒ daemon dies; also, in config.yaml-present setups an explicit yaml value silently wins over the env var, so the operator's knob does nothing). Both halves probed and verified live by the resilience worker; anomaly report `33028b27` filed. The defect itself is a house-pattern fix outside this lane — v2 simply must not extend it.

### A5. Migration & Ensure Strategy (with the SQLite correction)

- **ONE `.sql` migration file** for the three constellation tables + the `memoir_episodes` column evolution + the legacy seed, per the 119-file house convention.
- **SQLite validity (plan defect — corrected):** the plan's literal `ALTER TABLE memoir_episodes ADD COLUMN IF NOT EXISTS poignancy_status TEXT` is a **hard syntax error on SQLite** (no `IF NOT EXISTS` on ADD COLUMN) — it would kill every SQLite boot through `MigrationError`. Fix per the POK precedent (`20261107_110003_add_pok_source_tier.sql`): **plain `ADD COLUMN`** in the `.sql` (the ledger records the file applied exactly once; the fresh-SQLite create-already-made-columns collision is tolerated per-statement by the runner, `runner.py:697-703`). The `IF NOT EXISTS` form lives ONLY in the PG ensure. Keep the no-semicolon-in-comments discipline (the runner's naive `split(";")` after comment-stripping, `runner.py:662-673`).
- **SQLite ALTER limitation:** one column per statement — the `.sql` twin needs FOUR plain ALTERs (`poignancy_status TEXT`, `poignancy INTEGER`, `poignancy_attempts INTEGER NOT NULL DEFAULT 0`, `reflected_at`) + the cutover-bounded seed UPDATE (no-op on fresh empty DBs).
- **PG ensure: TWO new entries (deliberately coarse — one DDL, one DML):** (1) `ALTER TABLE memoir_episodes ADD COLUMN IF NOT EXISTS poignancy_status TEXT, ADD COLUMN IF NOT EXISTS poignancy INTEGER, ADD COLUMN IF NOT EXISTS poignancy_attempts INTEGER NOT NULL DEFAULT 0, ADD COLUMN IF NOT EXISTS reflected_at` (one statement — PG supports multiple IF NOT EXISTS clauses; the four columns are one logical evolution landing atomically); (2) the cutover-bounded legacy seed UPDATE. DDL and DML in separate entries for diagnostic clarity. Tables themselves need NO ensure entries — brand-new tables come from `SQLModel.metadata.create_all` (POK precedent, `manager.py:1000-1010`; memoir v1 same file `:983-996`).
- **Census pins (plan's AC3 corrected):** `tests/postgres/test_memoir_retrieval_pg.py:98-104` filters on `memoir_episodes` AND (`tsv` OR `GIN`) ⇒ **stays 2** (no new entry matches). `tests/postgres/test_memoir_pg_corpus.py:115-123` filters on `memoir_episodes` only ⇒ **flips 2→4**. The plan's "2→3" is wrong in both directions; document the asymmetry so nobody "fixes" the narrow pin.
- **W8 single-transaction blast radius:** the induction test (scratch PG, malformed statement, expect loud failure + zero partial artifacts) is a SOUND proof of atomicity and loudness — and must NOT be read as proving per-statement isolation (none exists by design; `manager.py:9705-9710` states verbatim "ORDER IS NOT A CONTAINMENT MEASURE"). Placement of the new entries is an auditability choice only. The two new statements are additive + idempotent (predicate-bounded seed; IF NOT EXISTS columns) — residual boot-abort risk is LOW and equals the house baseline for any ensure entry.

### A6. Recall Dual-Typing & Briefing Proposals Zone

**Recall envelope (real shape rules — plan §3.2 sketch corrected):** the actual envelope is header (`memoir_recall for {agent}: {N} episode(s), {D} dropped for budget, rank={basis}`) → episode field-lines → footer shipped VERBATIM from the service (`[memoir_recall: episodes=N dropped=D body_tokens=T budget=6000 rank=rrf semantic=... stats_updated=...]`, `memoir_retrieval.py:930-953`). Only the footer is byte-pinned (by tool-vs-service comparison, `test_memoir_tools_unit.py:559-569` — both sides change together, so the pins survive mechanically). Integration ruling:
- **Lane OFF ⇒ byte-identical v1 output** (no synthesis section, no footer token). Lane ON with zero matches ⇒ NO section, footer gains `syntheses=0` (self-describing absence).
- Lane ON with matches ⇒ `header → [synthesis section: `[synthesis:ID] (verb, confidence=…) claim` + `sources:` lines, structural cap `MEMOIR_RECALL_MAX_SYNTHESES = 5`] → untouched episode blocks → footer + `syntheses=M`. Syntheses render FIRST (consolidated beliefs lead) so the existing final budget re-check tail-drops episodes before syntheses — head-whole semantics per block preserved.
- **`k` stays episodes-only**; syntheses have their own structural cap and ride the shared 6000-token envelope (the re-check must learn to tail-drop synthesis lines too when a long claim tips it — ranked-drop, head-whole).

**Briefing proposals zone (arithmetic trap closed):** zones verified — persona 2000 + keepsake 4000 tokens are WRITE-TIME admission (`memoir_briefing.py:127-135`); dynamic 3000 tokens/5 episodes (`:138-143`); ceiling 24,000 chars (`:145`), backstop 48,000 (`:152`); today's trim order IS "dynamic first" (`_fit_dynamic_lines` oldest-first, `:286-303`; rung 1 >24k ⇒ dynamic dropped + disclosure; rung 2 >48k ⇒ whole block withheld). The trap: persona+keepsake admission alone (~6k tokens ≈ 24k chars) can fill the ceiling — bolting proposals into stable text would trip rung 1 and silently kill ALL dynamic episodes whenever keepsake admission is full. Ruling: proposals are an **OWN measured section**, admission ≤ 1000 chars at proposal-extraction time (template wrapper included — measure the shipped bytes, W-1 lesson), with explicit rung order **fit dynamic → trim dynamic → trim proposals (drop weakest first) → rung-2 withhold**, and proposal lines mirroring the single-sourced `KEEPSAKE_LINE_TEMPLATE` pattern (`models.py:~417`). Pin the dynamic-vs-proposals ordering and rung-2 behavior in tests — the plan is silent on both.

### A7. Test Architecture

**Mutation-verified safety pins (7 legs, house convention — plant → RED with the intended node → byte-identical restore → GREEN, journalled, digest over stable content; this lane already has the driver: `test/packs/memoir_mutation_matrix_test.py` + `mutation-matrix-digest.json`):**
1. Kill-switch truth table ×3 bools — parametrised matrix, one leg per env name; garbage arm deleted ⇒ RED (v1 precedent `test_memoir_knob_truth_table.py:151-184`).
2. Budget clamps — fake LLM returning 5+ syntheses ⇒ exactly 5 rows; raise the cap in source ⇒ 6 ⇒ RED. Same for chars (REJECTED not truncated) and the call-cap dispatch-time increment.
3. 19-code freeze — append a 20th code ⇒ RED (the existing assert stays).
4. Once-flag/refresh dedup — remove the date compare (⇒ 2 notifications) and remove refresh's independence from `memoir_briefing_injected` ⇒ both RED.
5. Append-only/supersede chain — REFINE without `supersedes` pointer ⇒ RED; empty citations ⇒ RED.
6. **Backfill seed** — widen the cutover predicate to unconditional ⇒ a post-cutover row must stay NULL ⇒ RED. (The leg that prevents a silent catastrophic mutation.)
7. **Record-column purity vs poignancy** — make `record_poignancy_score` write `importance` ⇒ the `RECORD_COLUMNS` pin must go RED (it stays green by construction under the freeze ruling).

**Standard tests suffice:** dual-recall formatting, dossier rendering, repo CRUD, migration discovery, ensure idempotence, stage-1 rule-based JEV heuristics, debounce arithmetic.

**3-worker boot census — beyond "log line seen":** exactly one task per worker, each on its OWN `app.state.*_task` and OWN executor with own thread prefix (never a shared pool — saturation deadlocks the census and cross-contaminates lanes); park/resume on knob flip within one tick with zero work while parked; numeric re-read per tick; no interleaving corruption (pass line reports the importance snapshot it USED; a committed synthesis cannot be mutated by a late poignancy score — trivially true under append-only UNLESS importance is stored on the synthesis — it is not; readiness path performs ZERO DB reads, pinned structurally); shutdown cancels AND awaits every task with no surviving non-daemon threads; **unset⇒ON visibility** — with all three knobs unset, three workers must start; a boot starting zero because a shared config was unparseable must be LOUD, not a silent empty census.

---

## §4 — Plan Corrections Register (surgical addenda for the planner/developer to fold)

| # | Sev | Correction | Plan site |
|---|-----|------------|-----------|
| 1 | 🔴 | `.sql` must use plain `ADD COLUMN` (no `IF NOT EXISTS`) — SQLite syntax breaker, POK `20261107_110003` precedent | phase-1 V2-D 1.1 / T1.1 |
| 2 | 🔴 | `poignancy_attempts INTEGER NOT NULL DEFAULT 0` column missing from phase-1 (phase-4 query + AC3 depend on it) | phase-1 DDL/ensure/model |
| 3 | 🔴 | Poignancy writes `poignancy` (new col), NEVER `importance`; consumers `COALESCE(poignancy, importance)`; `RECORD_COLUMNS` pin stays green; `D2_1_SURFACE` pin amended deliberately with rationale | phase-4 V2-D 4.1/4.4, T4.1–T4.3 |
| 4 | 🔴 | DELETE V2-D 4.5 delta-notify + phase-2 T2.1 in-memory rolling counter → derived SUM per §3-A1 | phase-4 V2-D 4.5; phase-2 T2.1 |
| 5 | 🔴 | Legacy seed (cutover-bounded) + `ENSEMBLE_MEMOIR_POIGNANCY_BACKFILL_MAX=0` opt-in; full-history scoring rejected | phase-1 + phase-4 |
| 6 | 🟡 | `supersede_with(old_id, new_row)` single-transaction writer replaces the two-call create+mark shape | phase-1 repo tasks |
| 7 | 🟡 | Phase-2 §3.2 recall envelope sketch does not match the real envelope — implement §3-A6's shape instead | phase-2 §3.2 |
| 8 | 🟡 | Census correction: corpus pin 2→4; retrieval_pg stays 2 (asymmetric filters) | phase-1 AC3, phase-7 |
| 9 | 🟡 | `memoir_star.description_as_of DATETIME NULL` added (Q5 cooldown carrier + v4 derivation watermark) | phase-1 DDL |
| 10 | 🟡 | `wake_source` param threading required for Q1 (signal absent at assembly seam; do not use `original_source`) | phase-6 §3.2 |
| 11 | 🟡 | `MEMOIR_REFLECTION_FORCE_FIRE_S = 3600` added (starvation cure) | phase-2 constants |
| 12 | 🟡 | Numeric knob truth table (3 classes, shared classifier, never boot-abort, never zero-budget); no bare `Field(gt=...)` | cross-phase config |
| 13 | 🟡 | Dedup key `sha256(claim_normalized + sorted(source_episode_ids))` on synthesis insert | phase-2 |
| 14 | 🟡 | Briefing proposals: own measured section ≤1000 chars + rung order fit-dynamic→trim-dynamic→trim-proposals→withhold | phase-6 V2-D 6.5 |
| 15 | 🟡 | `reflected_at` claim column + `memoir_reflection_state` row (watermark mechanism; derived-sum trigger population) | phase-1 + phase-2 |
| 16 | 🟢 | JEV candidate parking (retry next pass, `terminal:gate_unavailable`) replaces silent default-to-`none` drop | phase-5 T5.2 |
| 17 | 🟢 | Keepsake notification ledger consumed only on accepted enqueue | phase-6 §3.1 |
| 18 | 🟢 | Fragments = `COUNT(*)` lines, never `SUM(mention_count)` | phase-3 V2-D 3.2 |
| 19 | 🟢 | Nightly-before-in-day ordering inside the tick documented as load-bearing | phase-2 §3.1 |

---

## §5 — Risks (post-absorption residuals)

- 🟡 **Ensure single-transaction blast radius (W8):** two new entries join the boot-critical transaction. Both are additive + idempotent; the induction test proves loud atomic failure. Residual = house baseline for any ensure entry; the per-statement-isolation house-pattern change remains owed globally (pre-existing, not this lane's to fix).
- 🟡 **Recall envelope churn:** lane-ON output adds a footer token + optional section. Tool-vs-service pins survive mechanically; no external byte-pinner is known beyond the test suite. Lane-OFF byte-identity is the compat guarantee.
- 🟡 **Single-process assumption:** the no-double-fire design (sequential tick + asyncio.Lock + claim row) assumes ONE daemon per kingdom — guaranteed by R1 (kingdom-level, single daemon). Document; do not engineer for multi-writer.
- 🟢 **Pre-existing numeric-knob boot-abort defect** (`Field(gt=...)` + unguarded `load_config`; yaml-silently-wins): v2 avoids extending it via the before-validator; anomaly `33028b27` filed; fix is a separate house lane.
- 🟢 **Wake-refresh token cost:** bounded by DR-1 budgets; refresh blocks are superseded (one current row per instance), so cost is O(1) per session, not O(wakes).

---

## §6 — Decisions Pending

None blocking implementation — every ruling above is Level-4 authority. ONE visibility flag, non-blocking: the importance ruling (§3-A2) supersedes a Level-5 storage detail of the blessed V2-10 capability (score lands in `poignancy`, not by overwriting `importance`). It preserves the blessed capability semantically through the COALESCE consumer; if the Emperor specifically wants the LLM score to *visibly replace* ari's declared importance, that is a Level-2 preference that would require a deliberate DD-8 amendment — the leader may surface it at the next checkpoint.

---

## §7 — Gaps & Degradations

- `DEGRADED — skill injection missed at dispatch (3 workers)`: the three workers ran on fully-specified evidence-demanding briefs without their design-skill bodies (attachment requires a free target; auto-dispatched tasks occupied the turns). All three reports passed evidence adjudication (file:line anchored, claims re-verified at write time — two plan defects and one missing column were caught in verification). Skill-evolution feedback for this run is largely absent (one worker recorded feedback on an unrelated injected skill); no analysis gap resulted.
- Live-corpus magnitude check (`ensemble_prod`: 10 episodes, 0 syntheses) was performed read-only by the resilience worker; the backfill ruling is grounded in doctrine and pins, not today's row count.
