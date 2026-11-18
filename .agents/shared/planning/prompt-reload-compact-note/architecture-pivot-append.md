# Architecture Pivot Addendum — the APPEND method (v1.5)

**Date:** 2026-11-17
**Author:** architect (this addendum)
**Branch / HEAD at authoring:** `feature/prompt-reload-compact-note` @ `e728dd032` (the developer has committed past `87a995741`; every anchor below was re-measured at that HEAD)
**Supersedes:** v1.4 (`a95edb33`) on the note-path **mechanism** only.
**Does NOT supersede:** the v1.4 house-engine work that still serves the AUTO path (the doc format, the ceiling rule, the selection + preserved-tail helpers) — §3 audits this piece by piece.

---

## 0. What changed, in one paragraph

The user ruled the compaction **mechanism** (verbatim): *"our compaction feature will use this append method, and it a lot simple than heuristic, decision usage... Simple always better."* The append method is: the daemon appends a **user message** to the agent's own message list; the agent's **own model** writes the compaction on its next turn; the daemon swaps the compacted history for the model's output wrapped in the house doc format and splices the last K messages verbatim. **T0/T1/T2 are DEAD** — the model sees everything and does the selection. The decision-module seam, the heuristic pre-filter, the ranked shortlist, the comparative spike: all gone. **The keyword params are withdrawn** (ruling (C) moot): the tool surface is `prompt_reload(compact=True, compact_note="…")` and nothing else. The words were always enough; the parameter was not.

Two things do **not** change: the **house engine** stays, unchanged, as the engine of the AUTO path (the 95% pre-call hook, the CLE reactive handler) — this feature does not touch it; and the **note path is ONE path** with no fallback (Ruling 2, as corrected by the leader: a note-guided compaction is requested *from a running turn*, so the context is inside the agent's window by construction; an append that cannot run must **refuse with a typed reason**, never silently degrade to a different mechanism).

**Pending ruling withdrawn, on the record:** the "narrow keyword ruling" (keyword acceptance form, bounds, refusal codes, request text) is **WITHDRAWN by the user**, not forgotten — see §8.4.

---

## 1. The mechanism, settled

```
agent turn ──▶ tool call: prompt_reload(compact=True, compact_note="…")
                     │  (the tool NEVER compacts; it ARMS the note and returns a receipt)
                     ▼
            the arm is written (instances.instance_metadata JSONB)
                     │
                     ▼
        [APPEND]  the graph appends  HumanMessage("[System request: Compaction] …")
                     │  — rides the SAME node return as the tool's ToolMessage ⇒ ONE atomic channel write
                     ▼
        [AGENT'S OWN MODEL]  the next invoke of THIS turn reads the request and answers with the compaction
                     │  — the SAME model that holds the messages ⇒ the same provider ⇒ the warmed prefix
                     ▼
        [WRAP]   the daemon wraps the model's output in the house doc format (envelope + GLOBAL + boundary)
                     │
                     ▼
        [SWAP]   the daemon replaces the compacted span with the doc, splices the preserved tail
                     │  — rides a node return (sentinel-first), the house's own channel-rewrite mechanism
                     ▼
        [RECEIPT] the tool's next call, the info line, and the board carry WHAT ACTUALLY HAPPENED
```

Four properties fall out of this shape, and they are the whole point:

1. **The same-model invariant is STRUCTURAL, not a check.** The append path makes **no model call of its own** — the agent's own next invoke produces the compaction. There is nothing to compare, nothing to configure, and no way to swap a model.
2. **The compaction-model override is structurally unreachable** on the append path (it only ever governed the engine's summarizer, which the append path never calls).
3. **There is no second mechanism.** One path. A refusal is a refusal.
4. **The note is the request, not a layer.** It is never rendered into the doc.

---

## 2. The mechanics

### (a) WHERE the append happens

**The request must ride the SAME node return as the tool's result.** A tool returns a string; the graph's tool-result handler converts it into a `ToolMessage`. The appended compaction request must be a **user** message, so it cannot ride the tool's own return value — it must be added by the graph at the point where the tool result is placed into the channel, so that **both** the `ToolMessage` and the `HumanMessage(request)` land in the **same** node return, i.e. **one atomic channel write**.

Measured anchors (the two anchors below are the ones the implementer must re-verify at use time):

- The compaction pre-call hook that already owns a node-return-shaped compaction mutation: `daemon/graph.py:8195-8215` (the 95% pre-call hook) — it imports `make_remove_all_sentinel` from `daemon/compaction.py` and threads `hook_llm_config = compactor.llm_config or {}` into the `CompactionContext` (`daemon/graph.py:8383-8389`).
- The LLM invoke that must SEE the appended request: `daemon/graph.py:9517-9523` — `current_llm = llm_with_tools if use_vision_model else (llm_standard or llm_with_tools)` and `model_name = model_vision if use_vision_model else llm_config.get("model", "unknown") if llm_config else "unknown"`, then *"Invoking LLM (call_type) with N messages"*. The appended request must be inside `full_messages` **before** this point.

**Ordering constraint (load-bearing):** the request is appended **after** the tool result and **before** the next invoke. The agent therefore reads its own request and answers it in the same turn. That is what makes the compaction the agent's own artifact and what makes the same-model invariant structural.

**Which span is compacted:** the append path reuses the engine's **selection** layer, not its summarization layer — `identify_boundary_groups` (`daemon/compaction.py:1556`) and `select_compactable_groups` (`daemon/compaction.py:1648`), the same helpers the auto path uses. The append path therefore gets, for free and by construction: the **whole-group atomicity** (a `MessageGroup` is never split), the **preserved tail** (§2f), and the **compacted span** that the request names and the doc's envelope reports.

### (b) THE SAME-MODEL INVARIANT — structural, not a comparison

**Ruling: IGNORE the override; there is no comparison and no refusal code.** The reasons are measured, not stylistic:

- The append path **makes no model call**. The compaction is produced by the agent's own next invoke. The model that serves the compaction is by construction the model that holds the messages. The invariant cannot be violated by configuration.
- `resolve_compaction_model(config)` (`daemon/compaction.py:1245`, precedence `config.model` → `config.summarization_model` → `""`) is consulted **only** by the engine's summarization call sites (`daemon/compaction.py:2465`, `:4720`). The append path never reaches those sites, so the override is **structurally unreachable** — there is nothing to "ignore" at runtime.

**The one subtlety that MUST be handled — the vision switch.** The model that actually serves a turn is not always `llm_config["model"]`: `daemon/graph.py:9517-9520` selects `model_vision` when the payload has images and a standard model is available. The same-model claim is therefore stated against the **effective** model of the turn (`model_name` at `daemon/graph.py:9520`), and the **receipt names that effective model**. The receipt additionally records `compaction_model_override_applied=false` and, when an operator has pinned one, the pinned value — so an operator who pinned `COMPACTION_MODEL` can see from the receipt that the pin did not apply to this path. Content-free (a model id is not conversation content).

### (c) THE APPENDED TEXT

Verbatim shape, with the note inlined:

```
[System request: Compaction] Your next message is compaction of previous messages. Compaction request is: <compact_note>
```

**There is no separate keyword channel.** The keyword params are withdrawn; the note is free text and **may name what should die** in its own words ("the sonic arc is dead; the decision-module thread is settled"). The agent still tells the compactor what to remove — in the same breath, with zero new surface.

Two additions this addendum rules into the request text (both mechanical, neither a new surface):

- **The compacted span**, stated explicitly ("messages #a–#b"), so the model knows what it is compacting. The daemon knows the span (§2a); the model does not.
- **A size instruction**, so the model's answer fits the house's GLOBAL budget without the daemon having to truncate its work: the request asks for a compaction within the house's `GLOBAL_OVERVIEW_TOKEN_CAP` (600 tokens, `daemon/compaction.py:167`). Without it, the daemon's only options are truncation or an over-budget doc.

### (d) THE HISTORY SWAP

The house already has the right mechanism, and it already documents the constraint that decides the design:

> *"the node's own task commit then LANDS the compaction (a mid-superstep `aupdate_state` alone is superseded when the in-flight task returns; see the T2-ext canary)"* — `daemon/compaction.py:302-314` (the `REMOVE_ALL_MESSAGES` sentinel recipe, `make_remove_all_sentinel`).

**Ruling: both mutations ride NODE RETURNS; neither is a mid-turn `aupdate_state`.**

- **M1 (the append)** rides the tool-bearing node's return: `[…, ToolMessage(receipt), HumanMessage(request)]`. One atomic channel write.
- **M2 (the swap)** rides the node that observes the model's compaction reply: it returns the sentinel-first channel value `[sentinel, *post_compaction_channel]`, exactly as the house's compaction path does — the mechanism the auto path already proves.

**So the swap IS mid-turn, and that is now explicitly permitted.** The commission's original "never mid-turn" wording belonged to the engine-shaped design and is superseded by the user's mechanism. The house's own compensation for mid-turn writes is the **node-return commit** (the in-flight task's return IS the commit) — that is why the swap uses it and not `aupdate_state`.

**Torn-write exposure (between M1 and M2).** Two node returns means two commits, so a crash between them can leave the request appended and the history un-swapped. The house's answer is the same one it uses for compaction: the swap is **idempotent and keyed** (the doc id is `compaction-global-{instance_id}-{seq}`, and the seq is derived from the prior docs — `daemon/compaction.py:190` `_next_compaction_seq`), and the arm's settle record (`settle_compact_note_arm`) is written only **after** the swap lands. A crash before the settle leaves the arm ARMED, so the request re-appends and the swap re-runs — at-least-once, never a false "applied". The receipt must therefore be written **after** the swap, and must never report `compacted` before it.

### (e) THE DOC WRAPPER

**CONFIRM the leader's L5:** on the append path the note is **NOT** rendered again as a separate GLOBAL-OVERVIEW band. The model consumed it; re-rendering would duplicate the content and misattribute authorship (the doc would claim the agent's summary was the note, or vice versa). **The note appears nowhere in the doc.** Its provenance lives in the receipt as a length + hash (content-free by doctrine).

The daemon wraps the model's output in the house doc format, reusing `build_compaction_doc` (`daemon/compaction.py:895+`) with:

| Field | Value on the append path |
|---|---|
| `mode` | `"summary"` |
| `compacted_at` | the ISO timestamp of the swap |
| `global_overview` | **the model's compaction output** |
| `sections` | `[]` — the model wrote one artifact; there are no per-batch sections |
| `total_sections` | `0` ⇒ the coverage clause `{k}/{n} sections` is **omitted** (conditional clauses are omitted, never falsified) |
| `summarized_start` / `summarized_end` | the compacted span from `select_compactable_groups` |
| `preserved_count` | the preserved-tail message count (§2f) |
| `dropped_spans` | `[]` — nothing was dropped without a summary on this path |
| `context_window` | the agent model's window (the ceiling rule needs it) |
| `previous_overview` | `""` — no pass-2 seed on this path |
| `compact_note` | `""` — the note is NOT rendered (the note-first arm never fires) |

The resulting body is exactly the house shape: envelope line → `── GLOBAL OVERVIEW ──` → the model's output → `── END OF COMPACTED CONTEXT — …`.

**One house mechanism needs an honest amendment: the truncation marker.** The house's GLOBAL truncation appends *"[truncated — full GLOBAL overview text exceeded the GLOBAL_OVERVIEW_TOKEN_CAP=600; **SECTION DETAIL below remains complete**]"* (`daemon/compaction.py:1063-1072`). On the append path there are **no sections**, so that clause is a **lie**. Ruling: `build_compaction_doc` gains **one additive keyword-only parameter** (default `False`, so every existing caller is byte-identical) that drops the "SECTION DETAIL below remains complete" clause and replaces it with an honest one naming the cap and the absence of detail. This is a one-parameter change to a heavily-pinned function; the alternative (a sibling wrapper duplicating the envelope) is larger and therefore rejected under "Simple always better".

**The 15% ceiling rule.** `COMPACTION_DOC_CEILING_FRACTION = 0.15` (`daemon/compaction.py:171`), applied by `_apply_ceiling_rule` (`daemon/compaction.py:789`): `GLOBAL + Σsections ≤ 15%` of the context window; on breach the **oldest sections** are condensed (never the GLOBAL); a hard cap degrades to GLOBAL + an ARCHIVED line. On the append path `Σsections = 0`, so the numerator is the GLOBAL alone — and the GLOBAL is never condensed. With the 600-token cap in force, the ceiling is satisfied for any window ≥ 4,000 tokens; for a smaller window the house's own "never condense the GLOBAL" rule stands and the doc may sit over the ceiling — which the receipt must disclose (honesty surface, §2g). The note-specific hunk that `_apply_ceiling_rule` carries (Phase 3) is removed with the note render (§3).

### (f) THE TAIL SPLICE (Ruling 1)

**The daemon splices the last K messages VERBATIM after the compaction doc. K is the house's EXISTING preserved-tail set — not a new constant, and no new knob.**

**K's source, measured:** the preserved set is what `select_compactable_groups` (`daemon/compaction.py:1648-1708`) returns as its second element — the tail groups it preserved while compacting the rest. Its size is governed by `CompactionConfig.min_recent_window` (the call site is `daemon/compaction.py:3564`) with the window descending from `recent_window` toward `min_window` until the token budget is met. The doc's own `preserved_count` field already means **"number of preserved-tail messages"** (`daemon/compaction.py:944`), and the auto path already re-attaches the tail as `[doc, *preserved_tail]` (`daemon/compaction.py:5169`).

**Ruling:** the append path calls the **same** `select_compactable_groups` and takes the **same** preserved set; the splice is `[doc, *preserved_messages]`, spliced by the daemon with **no model re-emission**. The count reported is the house's `preserved_count`. **No new constant, no new knob.** The tail is verbatim — byte-identical to the pre-compaction messages, which is an acceptance pin (§6d).

### (g) THE RECEIPT (the honesty surface)

The receipt reports **only what is true**. Fields:

| Field | Value |
|---|---|
| `path` | `append` |
| `outcome` | `compacted` **only if** the swap landed; otherwise `refused` / `deferred` — **never** a success receipt for work not done |
| `agent_model` | the **effective** model that served the turn (`daemon/graph.py:9520`, vision switch accounted for) — the same-model invariant's audit trail |
| `compaction_model_override_applied` | `false` (the override is structurally unreachable here) |
| `compaction_model_override_configured` | the pinned value when an operator pinned one, else absent — so the "ignored" is visible |
| `note_chars` | the note's length |
| `note_sha256_12` | the note's hash prefix — **content-free by doctrine** (the note may carry intimate empire content; it never reaches an agent-readable log) |
| `tail_spliced` | K — the count of verbatim tail messages |
| `doc_id` | `compaction-global-{instance_id}-{seq}` |
| `compacted_at` | the swap timestamp |
| `selection` | **"the model made the final selection"** — the append path's authority statement (the engine path's equivalent is "the note was the sole authority") |
| `keyword_signals` | **REMOVED** — the param is gone; there is nothing to count |

The receipt must never imply a compaction happened that did not: `compacted` is written **after** the swap lands, and every refusal path writes its own refusal envelope.

---

## 3. Per-piece audit of the landed phases (1–5) — SURVIVES / REMOVED

Re-adjudicated against the append path, not inherited. "Reachability" is the test: **the append path never calls the engine's compaction — the model wrote the compaction directly** — so a piece whose only caller was the note path's engine call is dead.

| # | Piece | Where | Verdict | Reason (reachability) |
|---|---|---|---|---|
| P1.1 | the tool surface `compact` / `compact_note` | `daemon/tools/prompt_reload.py` (Phase 1, +426) | **SURVIVES** | it *is* the feature |
| P1.2 | the five note refusal codes | `daemon/services/prompt_refresh.py:165-175` | **SURVIVES (5/5)** | note_required, note_over_cap, note_requires_compact, compact_disabled, compact_rate_limited — all still meaningful; the keyword-without-compact code **never existed** |
| P1.3 | `CompactionConfig.compact_note_max_chars` (12,000) | `daemon/config.py:1674` | **SURVIVES** | the over-cap refusal is an honest rail |
| P1.4 | `CompactionConfig.compact_note_prefilter_enabled` | `daemon/config.py:1690` | **REMOVED** | its only reader was the pre-filter stage (Phase 5). A knob with no reader is a leak, not a default |
| P1.5 | `DELIBERATE_COMPACT_RATE_LIMIT_S` (900) | `daemon/constants.py` (Phase 1, +12) | **SURVIVES** | still governs the deliberate path |
| P1.6 | the arm carrier — `instances.instance_metadata` JSONB, keys `compact_note_arm` / `_armed_at` / `_sha256_12` / `_last_applied_at` / `_last_sha256_12` / `_last_deferred` | `daemon/services/prompt_refresh.py:276-302` | **SURVIVES (re-purposed)** | it is the transport for the note from the tool to the append+swap point. NO migration (the house forbids one here) |
| P1.7 | `arm_compact_note` / `read_compact_note_state` / `clear_compact_note_arm` | `daemon/services/prompt_refresh.py:410/345/441` | **SURVIVES** | the arm lifecycle |
| P1.8 | the ARMED receipt / `format_compact_note_info_line` | `daemon/services/prompt_refresh.py:1011` | **SURVIVES (re-shaped)** | it must now report what actually happened (§2g), not "armed pending seam" |
| P2 | the carrier's own legs | `tests/unit/tools/test_prompt_reload_compact_note.py` | **SURVIVES** | |
| P3.1 | the `compact_note` field on `CompactionContext` | `daemon/compaction.py:1382` (Phase 3) | **REMOVED** | its only reader is the engine's compaction path. The append path reads the carrier directly |
| P3.2 | the note render over sections + the **NOTE-FIRST GLOBAL-OVERVIEW arm** | `daemon/compaction.py:1036-1054` (Phase 3) | **REMOVED** | on the append path the model wrote the compaction; the note must not be re-rendered (§2e) |
| P3.3 | the note's "never render-truncated" rule + the `previous_overview` suppression | `daemon/compaction.py:1049-1053` | **REMOVED** | part of P3.2 |
| P3.4 | the **`_merge_summaries` merge-skip entry guard** | `daemon/compaction.py:4479-4499` (Phase 3) | **REMOVED** | the append path never calls the engine's compaction, and the auto path never has a note ⇒ **the guard can never fire** — a dead capability |
| P3.5 | the ceiling rule itself (`_apply_ceiling_rule`, `COMPACTION_DOC_CEILING_FRACTION`) | `daemon/compaction.py:171`, `:789` | **SURVIVES** (engine) | it serves the auto path unchanged. **Only the note-specific hunk** inside it (Phase 3, `@@ -797`) is **REMOVED** — with no note the computation is bit-identical |
| P3.6 | the doc builder, the envelope, the markers, the selection helpers, the preserved tail | `daemon/compaction.py` | **SURVIVES** | the append path's wrapper (§2e) and tail (§2f) are built from them |
| P4.1 | the **ARMED-AWARE FLOOR PREDICATE** | `daemon/compaction.py:3432-3462` (Phase 4) | **REMOVED** | it governs the engine's compaction when an arm exists. The append path never runs the engine ⇒ unreachable |
| P4.2 | the note-gated **THRESHOLD BYPASS** (armed ⇒ force the compaction at the seam) | `daemon/compaction.py:3509-3531` | **REMOVED** | same |
| P4.3 | the **T3 seam** (reads the arm, threads the note into the context, calls the engine with `force=False`, settles the arm after persistence) | `daemon/services/instance_messaging.py:1492/1551/1623` (Phase 4, +66) | **REMOVED → REPLACED** | it exists to drive the *engine's* compaction. The append path replaces it with the append hook (§2a). The arm-SETTLING obligation survives, re-pointed at the append outcome |
| P4.4 | the **T4 executor hook** (arm present ⇒ `force=False`; the `_ENGINE_SKIPPED_TYPES_TO_NOOP_REASON` row) | `daemon/services/compact_executor.py` (Phase 4, +102) | **REMOVED** | note-specific force semantics on the `/compact` command. The command's own behaviour (unarmed) is unchanged |
| P4.5 | `classify_compact_note_outcome` (an **engine-outcome** classifier) | `daemon/services/prompt_refresh.py:528` | **REMOVED** | its inputs are engine outcome kinds; the append path's outcome is not one |
| P4.6 | `settle_compact_note_arm` | `daemon/services/prompt_refresh.py:552` | **SURVIVES (re-purposed)** | it records the arm's consumption; it must now record the **append** outcome (and only after the swap lands) |
| P4.7 | the docstring/comment corrections to the executor's force semantics | `daemon/services/compact_executor.py:12-16` | **REMOVED with P4.4** | they document the removed hook |
| P5.1 | `daemon/_compact_note_prefilter.py` (331 lines, NEW) | Phase 5 | **REMOVED** | no consumer. Its entire input surface was the keywords; with the params withdrawn it has none. It is exactly the "dormant-but-present capability" the user cut |
| P5.2 | the pre-filter stage integration (126 lines across 8 hunks) | `daemon/compaction.py:3569-3616` + 7 more (Phase 5) | **REMOVED** | same |
| P5.3 | `tests/unit/tools/test_compaction_note_prefilter.py` (422 lines, NEW) | Phase 5 | **REMOVED** | tests a removed component |
| P5.4 | `test/packs/compaction_note_prefilter_unit.sh` (68 lines, NEW) | `e728dd032` | **REMOVED** | same |
| P5.5 | the PACKS.md row for the pre-filter pack | `.agents/tester/PACKS.md` (+2 in `e728dd032`) | **REMOVED (one of the two rows)** | **collision risk**: PACKS.md is shared and the group-chat lane is adding rows concurrently. Remove by NAME, never by position; check the MAIN repo copy too |
| P5.6 | `test/packs/prompt_reload_compact_note_unit.sh` (71 lines, NEW) + its PACKS.md row | `e728dd032` | **SURVIVES** | it packs the note TOOL surface, which survives |

---

## 4. The dead-code list — the REMOVE inventory (must be visible in the diff)

**Rule: removal is by EXPLICIT PATH, in the same commit as the append path, so the diff shows it.** "It is dead" asserted in a note is not removal.

**REMOVE (7 paths + 1 config knob):**

1. `daemon/_compact_note_prefilter.py` — the whole file (331 lines).
2. `daemon/compaction.py` — the Phase-5 integration: the lazy import + stage call + the `context._compact_note_positions` / `context._compact_note_dropped_spans` assignments + the defensive `except Exception` (the 8 hunks of `8611f7b34`).
3. `daemon/compaction.py` — the Phase-3 note plumbing: the note-first GLOBAL arm, the note render, the note's truncation exemption, the `previous_overview` suppression, the `_merge_summaries` merge-skip entry guard, the `CompactionContext.compact_note` field, and the note hunk inside `_apply_ceiling_rule`.
4. `daemon/compaction.py` — the Phase-4 note plumbing: the armed-aware floor predicate and the note-gated threshold bypass.
5. `daemon/services/instance_messaging.py` — the T3 note seam (Phase 4, +66).
6. `daemon/services/compact_executor.py` — the T4 note hook and the `_ENGINE_SKIPPED_TYPES_TO_NOOP_REASON` note row (Phase 4, +102).
7. `daemon/services/prompt_refresh.py` — `classify_compact_note_outcome` (the engine-outcome classifier).
8. `tests/unit/tools/test_compaction_note_prefilter.py` — the whole file (422 lines).
9. `test/packs/compaction_note_prefilter_unit.sh` — the whole file (68 lines).
10. `.agents/tester/PACKS.md` — **one** row (the pre-filter pack's); the `prompt_reload_compact_note_unit` row **stays**.
11. `daemon/config.py` — `CompactionConfig.compact_note_prefilter_enabled` **and every reader of it** (the tool-side reader `daemon/tools/prompt_reload.py:172` and the compaction-side reader). A knob with a surviving reader is a leak; a repo-wide grep for `compact_note_prefilter_enabled`, `_compact_note_prefilter`, `filter_candidate_groups`, `parse_marker_accounts`, `PrefilterResult` must come back empty before the commit lands.

**What the diff will show:** one deleted module, one deleted test module, one deleted pack script, one deleted PACKS.md row, and a net **negative** delta in `daemon/compaction.py` (the note plumbing out exceeds the append path's in) — plus the new append path. **The pre-filter must not survive as a dormant file.** If a reviewer finds `daemon/_compact_note_prefilter.py` still present, the addendum was not implemented.

**Is any of it worth keeping as a "fallback-path cost reducer"?** **No** — and the test is the user's own: the use that was CUT was exactly "heuristic pre-selection". With the keywords gone the marker matcher has no input, no consumer, and no author (the note is free text now, and the model does the selection). It does not survive the test; it does not ship.

---

## 5. The revised scope

**SURVIVES (as built):** the tool surface (`compact` / `compact_note`); the five note refusal codes; the note cap (12,000 chars, over-cap ⇒ typed refusal, **never truncation**); the deliberate-compact rate limit (900 s, a hardcoded constant); the arm carrier (`instances.instance_metadata` JSONB, no migration); the arm lifecycle (arm/read/clear/settle); the content-free arming logs (length + hash only — the note may carry intimate empire content and never reaches an agent-readable log); the default-path byte-identity; the house's doc format and the 15% ceiling rule; the selection + preserved-tail helpers.

**FALLBACK-ONLY:** **nothing in the note path.** (Corrected per Ruling 2 — the leader's earlier L1 said "house engine retained as the note path's fallback"; that was wrong on finality and is corrected here.) The house engine is the **auto path's** engine, unchanged: the 95% pre-call hook and the CLE reactive handler keep serving every instance that does not use the note path, and keep rescuing an over-window context. This feature does not modify either.

**REMOVED:** the deterministic pre-filter (T0/T1) in its entirety; the note path's house-engine plumbing (the merge-skip entry guard, the note-first GLOBAL arm, the armed-aware floor predicate, the threshold bypass, the T3/T4 note seams, the engine-outcome classifier, the `CompactionContext.compact_note` field, the pre-filter config knob); the keyword params (withdrawn upstream — never landed on this branch).

**NEW:** the append path — the request append (riding the tool-bearing node's return); the agent's own model producing the compaction; the doc wrapper (§2e, with the one-parameter honest-truncation amendment); the history swap (the sentinel-first node return); the tail splice (§2f); the same-model invariant as a structural property (§2b); the receipt (§2g).

**The final surface, in one line:** one bool (`compact`), one string (`compact_note`), one same-model invariant, one history swap, one tail ruling, one doc-format contract, and the honest rails — note required; over-cap ⇒ typed refusal, never truncation; default path byte-identical; **zero decision calls**; the receipt says what actually happened.

**The guideline stays owed** (the house's when-owed rule: a novel mechanism with silently-biting rules): `docs/guidelines/guideline-prompt-reload-compact-note.md`, written from `TEMPLATE.md` when the lane closes, carrying the kinship line **verbatim** and the line this round earned: **"the words were always enough; the parameter was not."** It must also carry: the same-model invariant; the refusal vocabulary (the real 14); the doc-format contract; the tail ruling; and the note-as-request semantics. **The architect does not write it** — the developer does, at lane close.

---

## 6. The revised acceptance

| # | Criterion | Pin (executable) |
|---|---|---|
| a | **same-model invariant** | the compaction is produced by the turn's own invoke ⇒ the effective model that served the compaction **is** the agent's model. Pin: capture the effective `model_name` at the invoke that produced the compaction and assert it equals the turn's effective model; and assert `resolve_compaction_model` is **never called** on the append path (a spy on `daemon.compaction.resolve_compaction_model`). **Negative control:** with `COMPACTION_MODEL` pinned to a different id, the append path still produces the compaction on the agent's model, and the receipt records `compaction_model_override_applied=false` |
| b | **receipt auditability** | assert the receipt names the effective model, the note's length + hash, the tail count K, the path (`append`), the doc id, and the selection statement; and that it reports `compacted` **only** when the swap landed. **Negative control:** a forced swap failure must produce a receipt that does **not** say `compacted` |
| c | **the note is the request** | assert the appended message's exact text is present in the message list the model was invoked with (observed at the invoke), and that the model's output is what lands in the doc's GLOBAL region. The doc must **not** contain the note text (the note is not re-rendered) |
| d | **tail splice byte-identity** | capture per-message digests of the pre-compaction tail; after the swap, assert each spliced message's digest is **identical** (byte-for-byte, no re-emission). Pin K against the house's preserved set (`select_compactable_groups`'s second return value) — K is not a new number |
| e | **default path byte-identical** | with no note armed, the compaction path's output is byte-identical to the pre-feature baseline (the house's golden-envelope / `git archive <base> | tar -x` byte-parity pattern) — including the auto path, which this feature must not touch |
| f | **doc format** | assert the wrapped doc carries `[CONTEXT COMPACTION — mode=…]`, `── GLOBAL OVERVIEW ──`, `── END OF COMPACTED CONTEXT — …`, the preserved count, and the self id; that the 15% ceiling applies to the model's output; and that the note text is absent |
| g | **refusal paths** | one assertion per code: `prompt_reload_note_required` (compact without note), `prompt_reload_note_over_cap` (note > 12,000 ⇒ **refused, never truncated**), `prompt_reload_note_requires_compact` (note without compact — **this one stays**: it is the coherence rule, not a feature), `prompt_reload_compact_disabled`, `prompt_reload_compact_rate_limited` |
| h | **the refusal census** | the census pin stays **14** (measured: `REFUSAL_CODES` at `daemon/services/prompt_refresh.py:178-196` holds **14** codes; `FAILURE_CODES` holds 1). **The keyword-without-compact refusal never existed** on this branch — the keyword param was never landed — so the withdrawal removes no code. The count is unchanged at 14 |
| i | **zero decision calls** | assert the decision family is never consulted on the append path (the seam is never registered; `DENIED_SEAM_ROLES` is untouched — **EX-2 stands**) |
| j | **removal is visible** | assert `daemon/_compact_note_prefilter.py`, its test file, and its pack script do **not** exist; and that a repo-wide grep for `compact_note_prefilter_enabled` / `_compact_note_prefilter` / `filter_candidate_groups` / `parse_marker_accounts` / `PrefilterResult` returns **nothing** |

---

## 7. The revised edit surface

**ADDED to the surface (the append path needs files the current list does not name):**

- `daemon/graph.py` — **already in the surface.** Carries: the append at the tool-result handling point, the swap at the post-reply node, and the effective-model capture for the receipt.
- `daemon/compaction.py` — **already in the surface.** Carries: the removal of the note plumbing, the doc wrapper's honest-truncation parameter, the selection/tail reuse.
- `daemon/tools/prompt_reload.py` — **already in the surface.** Carries: the tool surface (unchanged in shape) and the receipt content.
- `daemon/services/prompt_refresh.py` — **already in the surface.** Carries: the arm lifecycle re-pointed at the append outcome; the removal of the engine-outcome classifier.
- `daemon/services/instance_messaging.py` — **already in the surface.** Carries: the removal of the T3 note seam.
- `daemon/services/compact_executor.py` — **already in the surface.** Carries: the removal of the T4 note hook.
- `daemon/config.py` — **already in the surface.** Carries: the removal of the pre-filter knob.
- `tests/**`, `test/packs/**`, `.agents/tester/PACKS.md`, `docs/guidelines/**` — **already in the surface.**
- `daemon/services/_compaction_persist_seam.py` — **already in the surface**, and it is the house's **persist** seam: the append path's swap result must be persisted through it, exactly as the auto path's is, so that both paths write the same durable shape.

**NEW SURFACES the append path may need — NAME THEM NOW (the leader's standing rule: touching an EXPLICITLY-OUT path = STOP-and-report):**

- **A new module for the append path's own logic** — e.g. `daemon/_compact_note_append.py` — carrying the request-text builder, the doc wrapper call, the tail splice, and the receipt shaping. **This is a NEW top-level file, not on the current list.** Recommended: it keeps `daemon/graph.py` (a god module) from growing and keeps the append path's logic in one auditable place. **The leader must confirm the surface widening.** Alternative: put it in `daemon/compaction.py` (already in surface) — larger file, smaller surface. *Architect's recommendation: the new module* (it mirrors the removed `_compact_note_prefilter.py` slot and keeps the graph change to a call site).
- `daemon/services/instance_messaging.py` — if the append is to be issued from the messaging service rather than from the graph's tool-result handler, this file is already in the surface. ✔

**EXPLICITLY-OUT (unchanged, and these stay out):** `daemon/migrations/**` (the arm carrier is `instance_metadata` JSONB — **no migration**); `daemon/repositories/**/models.py` (no schema change); `agents/**` (no prompt change — the note is authored by the agent at runtime, not baked into a prompt); **`daemon/services/decision/**` (unchanged — EX-2 STANDS; the decision family is not involved in the note path at all, and the deny-list is not touched)**; `config.yaml` (no new config block — the two knobs that existed are env-only, and one is being removed).

**Also unchanged:** the auto path is **not** in the surface for behaviour changes — the 95% hook, the CLE handler, the engine's summarization/merge path, and the compaction-model override all keep their current behaviour. The append path is additive next to them, not a change to them.

---

## 8. What the user must be told

### 8.1 The directive, as satisfied

*"use the same model as the agent that hold mesges"* — satisfied **structurally**, which is stronger than satisfied by configuration: the append path makes no model call of its own. The compaction is produced by the agent's own next invoke, on whatever model that turn is using (including the vision switch when the payload carries images). There is no override to honour and nothing that can be silently swapped. `COMPACTION_MODEL` remains exactly what it was: the **auto path's** summarizer model.

### 8.2 The one thing the directive cannot be satisfied as written

*"the cost (cache hit, no cache hit) is enough for us to use this append method"* — the cost argument rests on **cache affinity**, i.e. on the compaction call reusing the prefix the agent's previous turn just warmed. **Affinity holds only if the appended request is answered by the same provider with the same prefix prefix.** Two conditions follow, and they are the append path's sharpest edge:

1. **The compaction must be produced by a call that includes the agent's own recent messages** — which it does, because the request is appended to the agent's message list and the same turn's invoke carries the whole list. ✔
2. **A provider that does not cache, or a proxy that strips the prefix, breaks the cost argument** — but the mechanism still works (it is a correctness-neutral cost assumption, not a correctness assumption). The receipt therefore records the effective model so the affinity assumption is **auditable after the fact**, exactly as the leader asked.

Also: **the note-as-request adds a turn.** The agent's turn now contains the appended request and its answer — the compaction itself is part of the conversation the agent carries forward. The cost picture the user accepted ("cache hit ≈ half the prefill of a miss") is consistent with this, but the receipt must not imply that the compaction is free: it is one extra invoke on a warmed prefix.

### 8.3 The refusal vocabulary — recounted

**14 REFUSED codes + 1 FAILED** (`daemon/services/prompt_refresh.py:178-196`; `FAILURE_CODES` = `{prompt_reload_internal_error}`):

- 1–9, the original prompt-refresh ladder: `prompt_reload_disabled`, `prompt_reload_agent_dir_unresolved`, `prompt_reload_dir_invalid`, `prompt_reload_manifest_missing_live`, `prompt_reload_manifest_unreadable`, `prompt_reload_manifest_mismatch`, `prompt_reload_agent_meta_invalid`, `prompt_reload_compose_failed`, `prompt_reload_db_unavailable`.
- 10–14, the compact-note lane's five: `prompt_reload_note_required`, `prompt_reload_note_over_cap`, `prompt_reload_note_requires_compact`, `prompt_reload_compact_disabled`, `prompt_reload_compact_rate_limited`.

**`prompt_reload_note_requires_compact` STAYS** — the note-without-compact refusal is the **coherence rule** (the note is meaningless alone), not a feature. **The keyword-without-compact refusal disappears with the param — and it never existed on this branch**: the keyword param was never landed, so no code is removed and **the count stays 14**. The census pin (`tests/unit/tools/test_prompt_reload_refusals.py`) stays at 14. (`prompt_reload_compact_note_already_armed` was already retired earlier: a second note REPLACES the first with disclosure, never a refusal — that behaviour stands.)

### 8.4 The withdrawn ruling, on the record

The **narrow keyword ruling** — keyword acceptance form, keyword bounds, keyword refusal codes, the keyword request text — is **WITHDRAWN by the user** ("you don't need to think the 'compact_removal_keyword' as the tool now not need it. it just need compaction note"). It was never delivered (the keyword scope addition was refused by the busy-guard and never landed on this branch), so **no code is orphaned by the withdrawal**; the withdrawal removes a *plan*, not a *shipped capability*. The lesson for the guideline: **the words were always enough; the parameter was not.**

### 8.5 The one thing the leader must decide (not the user)

**The size instruction in the appended request (§2c)** and the **honest-truncation parameter** (§2e) are architectural additions this addendum rules in, not surface the leader listed. Both are one-line-ish and both prevent a specific dishonesty (a truncated compaction whose marker claims completeness, or an over-budget doc). If the leader prefers zero additions, the fallback is: no size instruction (the model may overshoot) and the doc's truncation marker as-is — which I do **not** recommend, because the marker would lie about SECTION DETAIL that does not exist on this path.

---

## 9. The owed guideline (content only — the developer writes it at lane close)

`docs/guidelines/guideline-prompt-reload-compact-note.md`, from `TEMPLATE.md`, carrying the kinship line **verbatim**, plus:

- **"The words were always enough; the parameter was not."** — the note is free text; it may name what should die.
- **The same-model invariant** — structural, not a check: the append path makes no model call of its own; `COMPACTION_MODEL` governs the auto path only.
- **The note-as-request** — the note is the request the agent executes; it is never re-rendered into the doc.
- **The doc-format contract** — the house envelope, the GLOBAL region, the honest truncation marker (no "SECTION DETAIL remains complete" claim when there are no sections), the 15% ceiling.
- **The tail ruling** — the last K messages spliced verbatim, K = the house's preserved set, no knob.
- **The refusal vocabulary** — the real 14, with the note-without-compact rule explained as coherence.
- **The receipt's honesty surface** — what actually happened, the effective model, the note's length + hash, the tail count, and the standing rule: never imply a compaction that did not happen.

---

**Signed:** architect, 2026-11-17. Additive to v1.4 (`a95edb33`); supersedes v1.4 on the note-path mechanism only.

---

## 10. Amendment 1 — 2026-11-17 (post-worker fold-in)

*Companion: the T2 phase park record is `architecture-park-t2.md` @ the same commit. The park records the withdrawn decision-family adjudication, the batched-verdict measurement, and the EX-2 "moot, not retired" position.*

### 10.1 The pre-filter removal inventory — CORRECTED and COMPLETED

My §4 list was incomplete. The worker fold-in found **five leak sites** my list missed. The complete REMOVE inventory:

| # | Path | What is removed |
|---|---|---|
| 1 | `daemon/_compact_note_prefilter.py` | the whole file (331 lines / 11,917 bytes): `filter_candidate_groups`, `parse_marker_accounts`, `PrefilterResult` |
| 2 | `daemon/compaction.py:3593-3610` | the gate block — the lazy import at `:3599`, the `filter_candidate_groups` call, and the `context._compact_note_positions` / `context._compact_note_dropped_spans` stash |
| 3 | `daemon/config.py:1690-1700` **and `:8026-8115` and `:9049-9062`** | the `compact_note_prefilter_enabled` field **and its resolver and its `load_config` wiring** — the field alone is not the leak; the wiring is |
| 4 | `daemon/tools/prompt_reload.py:171-174` **and its callers at `:287`, `:453`, `:463`** | the `_prefilter_enabled` reader and every call site |
| 5 | `daemon/services/prompt_refresh.py:1020` and `:1036` | the `prefilter_enabled` **parameter on `format_compact_note_info_line`** and the log field it feeds |
| 6 | `daemon/tools/prompt_reload.py:615` | the docstring mention of the pre-filter |
| 7 | `tests/unit/tools/test_compaction_note_prefilter.py` | the whole file (28 tests — the truth table, the never-empty wall, the atomicity) |
| 8 | `test/packs/compaction_note_prefilter_unit.sh` | the whole file |
| 9 | `.agents/tester/PACKS.md` | **the one row** (worktree line 492). The `prompt_reload_compact_note_unit` row STAYS. **Check the MAIN repo copy too — the worktree's copy may lag, and the group-chat lane is adding rows to the same shared file.** Remove by NAME, never by position; the merge keeps both newest-first |
| 10 | `daemon/compaction.py` | the §3 note plumbing (P3.1–P3.4, P4.1–P4.5): the note render + note-first GLOBAL arm, the `_merge_summaries` merge-skip entry guard, the `CompactionContext.compact_note` field, the armed-aware floor predicate, the note-gated threshold bypass |
| 11 | `daemon/services/instance_messaging.py` | the T3 note seam |
| 12 | `daemon/services/compact_executor.py` | the T4 note hook + the `_ENGINE_SKIPPED_TYPES_TO_NOOP_REASON` note row |
| 13 | `daemon/services/prompt_refresh.py` | `classify_compact_note_outcome` |

**The gate:** a repo-wide grep for `compact_note_prefilter_enabled`, `_compact_note_prefilter`, `filter_candidate_groups`, `parse_marker_accounts`, `PrefilterResult`, and `prefilter_enabled` must return **nothing** before the commit lands. A knob, a reader, a resolver, a log field, and a docstring mention are all leaks — a config field with a surviving reader is not a default, it is a leak.

### 10.2 K counts GROUPS; the receipt counts MESSAGES

A distinction the implementer needs and my §2f did not spell out: `recent_window` and `min_recent_window` are **GROUP** counts — `select_compactable_groups` slices `groups[-window:]` — while the doc's `preserved_count` field is a **MESSAGE** count ("Number of preserved-tail messages", `daemon/compaction.py:944`). The splice is therefore whole groups; the number reported is messages. **Both come from the house; neither is a new number.** No new constant, no new knob.

### 10.3 The append path's outcomes need NO new refusal code — the census stays 14

The fold-in flagged that the append path's refusals (over-window, same-model violation) would need new codes. **They do not**, for two reasons:

1. **There is no same-model violation to refuse.** The invariant is structural (§2b): the append path makes no model call of its own, so there is nothing to mismatch and nothing to refuse.
2. **The append path's outcomes ride the house's EXISTING outcome/info-line channel** (`format_compact_note_info_line` + the `OUTCOME_*` vocabulary), **not** the tool's refusal envelope. The tool's refusal vocabulary is the **arm-time** vocabulary, and it stays at **14**.

The append path therefore **adds outcome labels** (compacted / deferred / append-failed) to the `OUTCOME_*` vocabulary — which is **not** the `REFUSAL_CODES` census. **The census pin stays `== 14`** (`tests/unit/tools/test_prompt_reload_refusals.py`). This is recorded explicitly because a drift here would look like an unexplained census change.

### 10.4 The acceptance harness — the concrete pins

Reuse over invent: `tests/unit/test_compaction.py:76 make_messages` / `:95 make_tool_message`, and `tests/unit/test_compaction_compact_note.py:32 NOTE_SENTINEL` / `:38 _config` / `:70 _messages` / `:96 _doc`.

| Pin | Assertion | Negative control |
|---|---|---|
| **same-model invariant** | capture the model id that served the compaction at the invoke site; assert it **is** the turn's effective model, and assert `resolve_compaction_model` is **never called** on the append path (a spy on `daemon.compaction.resolve_compaction_model`) | with `COMPACTION_MODEL` pinned to a different id: the compaction is still produced on the agent's model, and the receipt records `compaction_model_override_applied=false` |
| **receipt auditability** | the receipt names the effective model, the note's length + hash, the tail count K, the path, the doc id, the selection statement; `compacted` appears **only** when the swap landed | force the swap to fail ⇒ the receipt must **not** say `compacted` |
| **note-as-request** | at the invoke, the appended message's text is present and equals `"[System request: Compaction] Your next message is compaction of previous messages. Compaction request is: <note>"`; the model's output is what lands in the doc's GLOBAL region; **the note text is absent from the doc** | — |
| **tail byte-identity** | per-message digests of the pre-compaction tail are **identical** after the splice | — |
| **default byte-identity** | sha256 of the rendered doc bytes across the no-note run and a golden baseline; plus the note markers **absent** on the no-note path | — |
| **doc format** | the doc carries `[CONTEXT COMPACTION — mode=`, `── GLOBAL OVERVIEW ──`, `── END OF COMPACTED CONTEXT — …`, the preserved count, the self id; the 15 % ceiling holds; no "SECTION DETAIL below remains complete" claim (there are no sections) | a mutated marker must redden the pin |
| **refusals** | one assertion per code: `note_required`, `note_over_cap` (refused, **never truncated**), `note_requires_compact` (the coherence rule — stays), `compact_disabled`, `compact_rate_limited` (900 s, `daemon/constants.py:616`) | — |
| **zero decision calls** | the decision family is never consulted; `DENIED_SEAM_ROLES` is untouched — **EX-2 stands** | — |
| **removal visible** | the pre-filter module, its test file, and its pack script do not exist; the §10.1 grep returns nothing | — |

### 10.5 Two honest caveats the harness inherits

- **No compaction golden fixture exists** — not in `tests/fixtures/`, not in `.agents/tester/`. The byte-parity baseline must be **bootstrapped** using the house's `git archive <base_sha> | tar -x` pattern (capture inside the extraction so `daemon.__file__` resolves to the BASE tree).
- **`model_dump_json()` stability is version-dependent.** Verify it against the pinned LangChain before relying on it for the tail digest; the fallback is `sha256(m.content.encode())` for text-only messages, which loses metadata.

### 10.6 The degradation matrix (append path) — the shape the implementation must hold to

| Condition | Compaction? | Turn completes? | Anything misrepresented? |
|---|---|---|---|
| the agent's model call fails / times out | no | yes | no |
| the reply is empty or is not a compaction | no (the swap refuses) | yes | no |
| the reply exceeds the doc budget | no (refused, or honestly truncated) | yes | no |
| the context cannot take the request | no | yes | no |
| the note is missing / over cap | no (arm-time refusal) | yes | no |
| the deliberate-compact rate limit fires | no (refusal) | yes | no |
| the append lands but the swap fails | no | **the turn fails** | **the risk** — mitigated: the arm settles only AFTER the swap, so the state re-runs rather than half-applies |
| the daemon restarts between the two | no | resumes from the checkpoint | no (the checkpoint holds the pre-append state) |

**The invariant across every row: the agent never sees a half-compacted context** — either fully compacted (the swap landed) or fully pre-compaction.

---

## 11. Amendment 2 — 2026-11-17 (graph-seam fold-in)

*The append-seam worker measured the graph. Three of its findings **correct this document**; four are rulings this document owed. All anchors re-measured at HEAD `206c4fe0c`.*

### 11.1 CORRECTION — the ceiling rule is SKIPPED on the append path, not applied

§2e said *"Σsections = 0, so the numerator is the GLOBAL alone"*. **That is not what the code does.** The measured gate is `daemon/compaction.py:1026`:

```python
if context_window is not None and n_sections > 0:
    sections, archived_line, archived_count = _apply_ceiling_rule(...)
else:
    archived_line = None
    archived_count = 0
```

With `sections=[]` (the append path) the ceiling rule is **not applied at all** — `_apply_ceiling_rule` is never called, and there is no ARCHIVED line.

**This is acceptable, and it is now stated honestly:** the only size bound on the append path's doc is the **600-token `GLOBAL_OVERVIEW_TOKEN_CAP`** (`daemon/compaction.py:167`), which is tighter than 15 % of any window ≥ 4,000 tokens. The consequence to record: **the append path's doc is bounded by the GLOBAL cap, not by the ceiling rule** — and the request's size instruction (§2c) is therefore the primary control, with the cap as the backstop.

**The one-parameter truncation amendment (§2e) is CONFIRMED as necessary**, by measurement: the truncation marker at `daemon/compaction.py:1078-1080` says *"SECTION DETAIL below remains complete"*, and on the append path there are no sections. Without the amendment the doc would lie.

### 11.2 CORRECTION — the model-override leak has FOUR consult sites, and one of them reaches the WINDOW math

§2b said the override *"is consulted only by the engine's summarization call sites (`:2465`, `:4720`)"*. **Measured, there are four functional consult points** (`daemon/compaction.py`):

| Line | What | Reaches the append path? |
|---|---|---|
| `:1326` | `resolve_snapshot_model` inherits the compaction chain | no (no snapshot on this path) |
| `:2465` | `_effective_model_name` → `resolve_compaction_model(config) or context.model_name` — **the WINDOW math helper** | **YES, if reused — this is the leak** |
| `:2490` | `_trigger_window_for_model` | **YES, if reused** |
| `:4678` | the summarizer's `override_model` | no (the append path never summarizes) |

**The leak:** `_effective_model_name` exists precisely so that token/window math follows the **override** model (`:2452-2465`, its own docstring says so). If the append path reuses it — or `_trigger_window_for_model` — the doc's size math would be scaled to a model that **never serves the compaction**, silently undermining the same-model invariant on the one dimension where it is invisible (§2b's cache claim would still hold, but the doc's budget would be wrong for the agent's actual window).

**RULING:** the append path computes its context window as

```python
get_model_context_limit(agent_model_name, config)   # daemon/compaction.py:1191
```

directly — **never** `_effective_model_name`, **never** `_trigger_window_for_model`. This is cheap and structural: `select_compactable_groups` takes `context_window` as a **parameter** (`daemon/compaction.py:3562-3570`), so the append path simply passes the agent's window. **No helper changes; no new knob.** The same prohibition covers any future reuse of `build_compaction_doc`'s `context_window` argument.

### 11.3 RULING — message ordering, and what the tail is

Two things had to be settled and the document did not settle them.

**(a) The order is `[doc, *tail]` — the tail AFTER the doc.** This is not a preference: the house's boundary line is `── END OF COMPACTED CONTEXT — everything below is the verbatim recent transcript ──` (`daemon/compaction.py:149-152`). Putting the tail before the doc would make that line false.

**(b) The compaction exchange is CONSUMED, not preserved.** The exchange — the `prompt_reload` tool call, its receipt `ToolMessage`, the appended request, and the model's compaction reply — **must not appear in the preserved tail.** If it did, the compaction text would appear **twice** in the compacted context (once as the doc's GLOBAL, once verbatim as the reply), which defeats the compaction's purpose and doubles the artifact the feature exists to shrink.

⇒ **The swap replaces the compacted span INCLUDING the compaction exchange, and splices the preserved tail from BEFORE the request.** The agent's next turn sees the doc plus its genuinely recent history, and **never sees its own request or its own compaction reply as messages.**

### 11.4 RULING — how the daemon knows the reply IS the compaction

The daemon must distinguish "the model answered the compaction request" from "the model did something else". **Ruling: key off the REQUEST message's own markers — no new channel field.**

The appended `HumanMessage` carries:

```python
HumanMessage(
    content="[System request: Compaction] …",
    additional_kwargs={"injected_message": True, "context_kind": "compaction_request"},
)
```

After the invoke, the agent node checks whether the channel's last `HumanMessage` carries `context_kind == "compaction_request"`. If it does, **the reply is the compaction** and the swap arms. If the reply is empty, is a refusal, or carries tool calls, the swap **does not** arm — the append is a no-op and the outcome is recorded honestly as `append_failed` / `empty_reply` (§2g: never imply a compaction that did not happen).

Why this shape and not a channel field: the house already has **both** precedents — the `injected_message` kwarg on injected messages, and the `pending_repair_ghost_terminal` pending-field (`daemon/graph.py:4135`, consumed at `:8902-8932`). The **message marker** is the smaller of the two (no `SessionState` field, no checkpoint-schema surface) and it travels with the request itself, so the request and its identity cannot desynchronize. "Simple always better."

### 11.5 RULING — the turn continues on the NORMAL router; no new flag

After the swap, `should_continue` (`daemon/graph.py:4274-4288`) reads `state["messages"][-1]` — which is now the **tail's** last message, not the compaction reply. Two behaviours are possible, and **both are acceptable**:

- it routes to **`agent`** ⇒ the model re-invokes with the compacted context and continues working (the agent compacted to free context and keeps going — arguably the desired behaviour);
- it routes to **END** ⇒ the compaction was the turn's work and the next turn starts from the compacted context.

**Ruling: do not add a routing flag; do not add a channel field.** The deliberate-compact rate limit (`DELIBERATE_COMPACT_RATE_LIMIT_S = 900`, `daemon/constants.py:616`) is the backstop against a compaction loop. **The acceptance must PIN the observed routing** (Amendment 1 §10.4 gains a row): the test asserts that after the swap the turn either continues with the compacted context or ends — and that it **never** re-appends the request within the same turn and **never** loops. If the pin shows the router misbehaving (e.g. the turn ends before the agent can ever act on its compacted context), that is a **finding to report**, not a flag to add preemptively.

### 11.6 The `is_turn_boundary` side effect — stamp the request `injected_message=True`

`daemon/graph.py:8862-8864` computes `is_turn_boundary = isinstance(messages[-1], HumanMessage)` — **raw, with no injected-message check**. An appended request HumanMessage at the tail makes the next agent node look like a turn boundary, and `watchover_denial_count` resets (`:8866-8868`).

Ruling: **the request MUST be stamped `additional_kwargs={"injected_message": True, …}`** (which §11.4 already requires for detection). This prevents the **durable-repair-budget** reset (`_is_real_human_message` at `daemon/graph.py:2242-2257` excludes injected messages; `_repair_boundary_reset_state` at `:2287-2300`). The watchover counter reset is **unconditional** and is accepted: it is the same behaviour every injected message already has (the house's injection slot, `daemon/graph.py:9087-9160`), so the append seam introduces no new class of side effect.

### 11.7 The tools-node wrapper's return shape — handle BOTH

The tool result lands via the `"tools"` node (`daemon/graph.py:13555-13568` → `daemon/services/long_tool_nudge.py:752` `wrapped_tools_node` → `ToolNode.ainvoke` at `:906` → a dict `{"messages": [ToolMessage, …]}`; shape handling at `:1014-1021`). **`daemon/services/long_tool_nudge.py` is OUT of the lane's surface.** The graph.py-side append wrapper must therefore be **shape-tolerant** — handle both a dict carrying `messages` and a bare list — mirroring the precedent at `long_tool_nudge.py:1014-1021` rather than assuming one shape.

### 11.8 CORRECTION — the edit surface: WITHDRAW the new-module recommendation

§7 recommended a **new** top-level module (`daemon/_compact_note_append.py`) and flagged a surface widening for the leader to confirm. **Measured, the append path needs no widening.** The graph-seam worker's edit surface is entirely inside the lane's declared list:

`daemon/graph.py` (the tools-node append wrapper + the agent-node swap + the request detection) · `daemon/compaction.py` (the doc wrapper with `sections=[]`, the selection helpers, the sentinel, the honest-truncation parameter, the window-provenance rule of §11.2) · `daemon/services/prompt_refresh.py` (arm / read / settle / receipt) · `daemon/tools/prompt_reload.py` (tool surface) · `daemon/services/instance_messaging.py` (REMOVE the T3 seam) · `daemon/config.py` · `tests/**` · `test/packs/**` · `.agents/tester/PACKS.md` · `docs/guidelines/**`.

**No file from the EXPLICITLY-OUT list is needed.** `daemon/services/decision/**` is untouched; **EX-2 stands.** The new module is **withdrawn** — putting the append logic in the graph (which owns the channel) and the doc wrapper in `compaction.py` (which owns the format) is the smaller surface and the smaller design. The leader's confirmation request in §7 is **no longer needed.**

### 11.9 What remains unverified (the graph fold-in's own honesty)

1. **Repository read-after-write for the arm carrier.** The tools-node wrapper reads `read_compact_note_state` immediately after the tool writes it, inside the same node. Whether the repository returns the fresh row (or a stale cache) is **not verified**. If it is stale, the append would read an unarmed state and silently do nothing — the receipt would say so, but the feature would be inert. **This must be pinned** (a test that writes the arm and reads it back within one node, plus a live check).
2. **The exact keying of "this reply is the compaction"** against the daemon's real turn semantics is ruled above (§11.4) but not yet exercised against a live turn.
3. **The observed routing after the swap** (§11.5) — which of the two acceptable behaviours actually occurs, and whether the tail's last message shape makes either one pathological.
