# Plan Overview: prompt_reload(compact, compact_note) — the compact-note lane

Date: 2026-11-16 · **Rev 3: 2026-11-17** (architect fold + final consumption ruling)
Author: planner-side worker via plan-creation skill (Rev 1 engine-agnostic half · Rev 2 leader-ruling amendment · Rev 3 architect fold)
Status: **Ready for Council** — the engine shape is RULED (architect decision of record `f159b3e3` + the final consumption ruling, 2026-11-17); see §13 `ENGINE SHAPE — RULED`
Branch: `feature/prompt-reload-compact-note` (worktree `/home/nea/ensemble-src-wt-prompt-reload-compact`, base tip `858a85e31`)
Prior art: `.agents/shared/planning/prompt-reload/` (the original prompt_reload lane — ledger format, refusal-vocabulary conventions, honesty discipline)

**WHAT THIS DOCUMENT IS.** The lane plan. It fixes the **contract**: the parameter surface, the refusal vocabulary, the note cap, the honest receipt, the seam-safety law, the scope fence, and the acceptance criteria any implementation must satisfy. **Rev 3 folds the architect's decision of record (`f159b3e3`) and the final consumption ruling into it** — the engine, carrier, hook shape, render mechanism, and pre-filter design are now **RULED** (§13), each slot carrying its ruling + citation.

**WHAT THIS DOCUMENT IS NOT.** It is not the architect's document — the decision of record is `architecture-recommendation.md` (committed `f159b3e3`), and this plan cites it rather than restating it. It is not code, and no implementation is described beyond acceptance criteria.

---

## 1. Objective

Give the calling agent a way to compact **its own** conversation at the next turn boundary with a **self-authored note** that becomes the authoritative summary layer of the compacted context — so that the summary the agent sees is the agent's own distillation (what is settled, what is open, what is dead), not a fragile machine merge that can fail and leave the agent reading "(overview unavailable — merge pass failed; the sections below are authoritative)".

Testable single sentence: **when an agent calls `prompt_reload(compact=True, compact_note="…")`, the note it wrote is rendered VERBATIM in the compacted context the agent's next turn actually sees — including when the merge pass fails — and the tool never tells the agent a compaction happened when it did not.**

---

## 2. Scope

### In Scope

| # | Item | Traceability |
|---|------|--------------|
| S1 | Two new tool params: `compact: bool = False`, `compact_note: str = ""` on `prompt_reload` | C1, C6 |
| S2 | A typed refusal family for the compact-note surface (5 new codes, additive to the existing 9 REFUSED + 1 FAILED) | C3, task §3 |
| S3 | A note-length cap with an explicit budget interaction against the compaction doc's own ceiling rules | C3, task §4 |
| S4 | An ARMED-vs-COMPACTED honest receipt vocabulary | C3, honesty discipline (prior ledger D10/D24) |
| S5 | The seam-safety law: compaction at a TURN BOUNDARY, never mid-turn | C1, R-C |
| S6 | The note's render law: renders on ALL THREE branches of the GLOBAL three-way branch, verbatim, and does not depend on the merge pass | C2, AR-4 |
| S7 | Content-free arming observability (length + hash prefix only) | task §7 risk #9, resident input (3) |
| S8 | A DORMANT (default-OFF) pre-filter — **RULED**: a deterministic zero-LLM marker matcher, outside the P1 doctrine, with its own kill lever (ES-2/ES-8) | Emperor direction, R-A, AR-2, AR-1 |
| S9 | The acceptance/test strategy, naming exact files, a NEW pack and its PACKS.md registration | task §5, AR-4 |
| S10 | The scope fence | task §6 |
| S11 | The Guideline deliverable (written in a later phase, after tester sign-off) | R-D |
| S12 | The decision ledger (this lane's `decisions.md`) | task §2 deliverable 2 |

### Out of Scope

| # | Item | Why out |
|---|------|----------|
| X1 | Any new agent roster entry / a "dreamer" agent in `agents/**` | The engine shape is undecided (§13 ES-1); if a new agent is ruled, the architect names it. This lane plans no roster change. |
| X2 | A parallel compaction engine | R-C + the scope fence. **RULED: the architect REFUSED (B) — a new dreamer/distill engine** (both council models, independently: a second engine forks the doc format, persist recipes, ceiling rule `:788`, sentinel `:317`, pre-write guard `:480-495`, and the 60s dedup `:5091`). Shape (C) Hybrid: the engine stays; the note is engine data. |
| X3 | Changes to the default compaction path (auto triggers T1–T4, the pre-call hook, the reactive handler, the ceiling rules, the sentinel mechanics, the dedup/escalation gates) | R-C — with EXACTLY ONE exception: the architect-ruled note-armed forced turn-boundary hook. |
| X4 | New env knobs beyond the note cap | Task §6. The pre-filter's budget + kill lever are the architect's to rule (ES-8); until then, no knob is planned. |
| X5 | Any change to the memoir lane | Different subsystem, different memory class (episodic vs working). Conceptual kinship only (§11). |
| X6 | Edits to `agents/**` prompt artifacts | DB-versioned + human-approval-only (`daemon/tools/prompt_artifacts.py`). The lane writes **no** prompt artifact. |
| X7 | A new table / migration | Task §6. ES-3 states the criteria; if the architect rules a carrier needs one, the ruling is recorded here first. |
| X8 | Editing the prior lane's `prompt-reload/` docs | Prior art is read-only reference. The new lane owns its own directory. |
| X9 | Merging, pushing, setting upstream | The leader owns the finalize step (`lane.prompt-reload-compact-note.finalize-brief`). This lane commits in-branch only. |

---

## 3. Requirements — numbered, traced, with acceptance criteria

Every AC is written as **what a test would assert**. "Test" means a unit test in `tests/unit/`, a leg in the new pack (§12), or an E2E assertion in the AR-4 scenario (§12 L9).

Traceability tags: `C1..C6` = Emperor commission clauses (2026-11-16); `E1..E3` = Emperor architectural direction clauses; `R-A..R-D` = leader rulings; `AR-1..AR-5` = Ari rulings.

---

### R1 — Parameter surface (purely additive)

**Requirement.** `prompt_reload` gains `compact: bool = False` and `compact_note: str = ""`. `compact_note` is not a required positional (C6's signature note: the note is REQUIRED **iff** `compact=true`).

- **AC1.1** The tool's args schema (`PromptReloadInput`, `daemon/tools/prompt_reload.py:225`) declares exactly three fields with defaults `force=False`, `compact=False`, `compact_note=""`; a schema-shape test asserts the field set and the defaults.
- **AC1.2** `prompt_reload()` with **no** arguments returns a string **byte-identical** to the pre-change `prompt_reload()` return (golden-string pin; the golden value is captured from the pre-change code, never copied by hand).
- **AC1.3** Every existing `prompt_reload()` call site keeps working — no positional-argument break. The existing `tests/unit/tools/test_prompt_reload_tool.py` suite passes **unmodified** except for the one pin named in AC13.4.
- **AC1.4** The tool stays in the `prompt-reload` privileged category; `daemon/tools/_tool_registry.py` is **not** touched by this lane (no registration change — the tool already exists). A pin asserts the registry file is unmodified by the lane (git-level, not source-level).

### R2 — `compact=true` WITHOUT a note → typed refusal

**Requirement.** The note is the point, not an option (C3). No note ⇒ refuse; never "compact without a note".

- **AC2.1** `prompt_reload(compact=True)` returns exactly the house REFUSED envelope: `Error: prompt_reload REFUSED — reason=prompt_reload_note_required: <message>` (envelope shape per `daemon/services/prompt_refresh.py:145`, `daemon/tools/prompt_reload.py:95`). The code name follows the architect's §G vocabulary.
- **AC2.2** The refusal has **ZERO** side effect on the compaction path: a spy on the note-carrier writer and on `ContextCompactor.compact_state` asserts **no** call — the architect's "**nothing armed**".
- **AC2.3** Exactly one INFO line fires, `outcome=refused`, `code=prompt_reload_note_required`; the line is **content-free** (length + digest prefix only, never the raw note — R11) and carries no persona content.
- **AC2.4** The refusal message NEVER contains the words "compacted" / "compaction complete" / "summary rendered" — it says the note is required. (A refusal is a design outcome, not an error — `docs/guidelines/README.md` item 3.)

### R3 — note over cap → typed refusal, NEVER silent truncation

**Requirement.** C3 + task §4: a note exceeding the cap is refused. Silent truncation is forbidden — it would produce an authoritative layer that silently differs from what the agent wrote.

- **AC3.1** A note of **12,001** characters returns `Error: prompt_reload REFUSED — reason=prompt_reload_note_over_cap: <message>`, and the message **echoes the chars and the cap** (the architect's wording: "echoes chars + cap; never truncates; nothing armed"). The cap value is **12,000** — the architect's ruling (`architecture-recommendation.md` §G: "**Cap: 12,000 chars** (`CompactionConfig.compact_note_max_chars`, aliased field, default 12000)"), which supersedes this plan's proposed 8,000 (see `decisions.md` D21/D22).
- **AC3.2** A note of **exactly 12,000** characters is **accepted** (boundary inclusive).
- **AC3.3** No truncation path exists in the admission code: a mutation leg deletes the length check and AC3.1 goes RED. The architect's co-pin requirement is carried here: the cap is **co-pinned in tests against `GLOBAL_OVERVIEW_TOKEN_CAP`** (drift guard) — the two caps are distinct and must never be confused.
- **AC3.4** A refused over-cap note leaves **no** trace in the carrier — the architect's "nothing armed" (AC2.2's spy applies).

### R4 — note absent or whitespace-only → typed refusal

**Requirement.** The architect folds the absent and whitespace-only cases into **ONE** code — "forking them buys nothing" (§G). The requirement is unchanged in substance: a note with no content is refused and nothing is armed.

- **AC4.1** `compact=True, compact_note=""` (note absent/empty) → REFUSED `prompt_reload_note_required` — nothing armed.
- **AC4.2** `compact=True, compact_note="   \n\t  "` (non-empty but whitespace-only) → REFUSED `prompt_reload_note_required` — **the same code as AC4.1** (the architect's explicit merge).
- **AC4.3** The two cases are **NOT** distinguishable by code at the tool surface — a single test asserts `prompt_reload_note_required` on both inputs, and a second test asserts that **no** `prompt_reload_compact_note_empty`-style second code exists in `REFUSAL_CODES` (the Rev-2 plan's split code is retired).

### R5 — a SECOND note while one is armed → **REPLACE, WITH DISCLOSURE** (architect ruling §G)

**Requirement.** The architect ruled REPLACE-with-disclosure (council D2 adopted). The receipt carries `superseded_previous=true` + the prior note's `sha256[:12]`.

**Rationale (recorded).** *The trap a refuse creates:* a refusal leaves the agent holding an arm that **cannot fire on a low-pressure instance** and **cannot be corrected** — the note it actually wants to write is stuck behind a dead arm with no un-arm path. And the note is the agent's **OWN working memory** — revisable before consumption **by right**, not a privileged write. The architect's own words: "Refusing without any un-arm path strands a mistyped note, and the commission fixed the surface at two parameters."

- **AC5.1** With a note armed and **not yet landed**, a second `prompt_reload(compact=True, compact_note="…")` **REPLACES** the armed note — it is **NOT** refused. No `prompt_reload_compact_note_already_armed` code is retained for this case: **the code is RETIRED** (no genuine refusing case remains — the architect ruled REPLACE, and a replace arriving while the note is mid-consumption is not a reachable state in the ruled design).
- **AC5.2** The replacement is **disclosed**: the arm receipt carries `superseded_previous=true` and the **prior** note's `sha256[:12]` — never the prior note's text.
- **AC5.3** The disclosure is content-free and honest: it says the previous note was **superseded** and that the **new** note is the armed one; it never claims a compaction occurred.
- **AC5.4** The `prompt_reload_compact_note_already_armed` code is **retired** from the vocabulary (Rev 2's five-code set → the architect's four; see §4's reconciliation table and `decisions.md` D22).

### R6 — note supplied with `compact=false` → **REFUSE** (plan decision D4, leader ruling 2026-11-16)

**Requirement.** Pick one: ignore, or refuse. **This lane picks REFUSE** (rationale in D4; the architect concurs — §G: "REFUSED — **ignoring is dishonest**").

- **AC6.1** `prompt_reload(compact=False, compact_note="text")` → REFUSED `prompt_reload_note_requires_compact`.
- **AC6.2** `prompt_reload(compact=False)` (note absent/empty) → **byte-identical** to today's receipt. **Scope statement (verbatim, leader ruling 2026-11-16):** "C5: byte-identity is asserted for `compact=false ∧ note absent/empty` (today's shape). The pair (`compact=false`, note non-empty) is out of byte-identity scope and MUST refuse with `prompt_reload_note_requires_compact`." (The code name follows the architect's §G naming; the leader's statement predates the rename.) The (false, non-empty-note) shape is a NEW input shape no today-caller ever sends, so refusing it cannot violate byte-identity of existing behavior; IGNORE-WITH-DISCLOSURE is REJECTED (a silent ignore is a swallow; typed refusal is the honest outcome and catches the caller-error case — an agent that meant `compact=true` and mistyped). [leader ruling 2026-11-16 + architect §G]
- **AC6.3** The refusal NEVER claims a compaction was requested, armed, or performed.

### R7 — seam safety: the compaction happens at the NEXT TURN BOUNDARY, never mid-turn

**Requirement.** C1 ("TURN BOUNDARY; NEVER mid-turn"). The two mid-turn compaction triggers in the tree are **off-limits**. **Corrected anchors (both, with their roles):** `daemon/graph.py:8394` is the `compact_state` call **inside** `_maybe_precall_compact_95` (def `daemon/graph.py:8138`); `daemon/graph.py:9722` is that helper's **graph-level invocation** (it is NOT a `compact_state` call). T2 is the CLE reactive handler — `except ContextLengthExceededError:` at `daemon/graph.py:9890` — whose `compact_state` calls are at `daemon/graph.py:9990/9998/10019` (plus a commented-out `:9957`).

- **AC7.1** During the tool call itself, **no** conversation mutation happens: a spy on `ContextCompactor.compact_state` and on the message list asserts **zero** calls and zero mutation.
- **AC7.2** The note-armed compaction runs at a TURN BOUNDARY seam — the E2E leg asserts the compaction occurred **between** turns, and the agent's in-flight turn was unaffected.
- **AC7.3** The note-armed path is **not** wired into T1 or T2: a pin asserts the note-armed hook is not registered in the mid-turn trigger set.
- **AC7.4** **NAMED HAZARD (verbatim class, final consumption ruling):** *"calling the engine with force=True on an armed note below the floor triggers 50%-tail truncation — FORBIDDEN."* The armed path calls the engine with **`force=False`, NEVER `force=True`**. A mutation leg flips the armed path to `force=True` and asserts the 50%-tail truncation appears (the leg goes RED on the correct implementation). The floor itself is `min_messages_before_compaction` (default **10** — `daemon/config.py:1660`; the constant `MIN_MESSAGES_BEFORE_COMPACTION: int = 10` at `daemon/constants.py:603`); below it, `force=False` yields the clean skip `skipped_below_min_messages` (`daemon/compaction.py:3368-3370`) and **the arm persists**.
  - **POSITIVE-SHAPE PIN (C-1(v)) — the build gate:** below-floor ∧ over-budget ∧ **armed** ⇒ **clean skip, arm persists, still-armed receipt**. **NOTE IN THE PLAN: a flip-to-force=True mutation does NOT catch this hole** — the pin must assert the **positive shape** (below-floor ∧ over-budget ∧ armed ⇒ skip), not merely the absence of the 50%-tail truncation. The hole: the below-floor fall-through is `force OR over_budget` (`daemon/compaction.py:3369-3373` → `:3385-3389`), so an armed T3 with `force=False` still 50%-tail-truncates in the below-floor ∧ over-budget corner; T4 `/compact` consumes the arm while calling `force=True` (`daemon/services/compact_executor.py:1220`); `_last_effort_tail_truncation` (`:2518`) produces a non-empty replacement with **NO GLOBAL doc**, so the note can never render there; and ES-7's predicate (replacement non-empty ∧ persist=True) eats the arm on that outcome — false "applied" provenance + a falsely armed rate limit. **The positive-shape pin is the build gate.**

### R8 — the note rides VERBATIM in the compacted context, at the GLOBAL OVERVIEW slot

**Requirement.** C2: the note must ride verbatim and be rendered where the auto GLOBAL OVERVIEW sits today.

- **AC8.1** After the compaction, the compacted context contains the note's text **byte-for-byte** — no re-summarization, no trimming, no rewriting, no case-folding, no normalization.
- **AC8.2** The note renders **in the GLOBAL OVERVIEW region, as the branch's FIRST ARM** (architect v1.2 sharpening — **NOT** an additive sibling band): a test locates the note's text between the `── GLOBAL OVERVIEW ──` header (`daemon/compaction.py:994`) and the `── SECTION DETAIL ──` header (`:1034`), and asserts the note is the **first** body content after the header. The note **replaces the three-way chain** when a note is consumed; the auto arms are byte-identical when it is not.
- **AC8.3** **The note renders on EVERY doc-producing path** — full, partial, and truncation-fallback — **merge-independent by construction**. With the merge skipped (§ES-4's entry guard), `global_overview` is `None`, so the auto arms are unreachable and the note-first gate supplies the GLOBAL region:
  - (i) a merged GLOBAL is present (`global_overview` truthy) — **auto path only; unreachable when a note is consumed**;
  - (ii) `mode == "truncation"` (`:1024-1029`) — the branch that currently renders **nothing** — **auto path only**;
  - (iii) **MANDATORY, non-negotiable (BINDING 2.1; D27 binding):** the merge-failed path — *which now dies by skip, not by a failed merge* — → `global_overview = None` (`:3697` / `:3778`) → the branch that renders `GLOBAL_DOC_PLACEHOLDER_GLOBAL` ("(overview unavailable — merge pass failed; the sections below are authoritative)", `daemon/compaction.py:156`) — **auto path only; SUPPRESSED when a note is consumed**. **"Overview dies by skip; note survives where it died."** (D27 binding.) The acceptance demo MUST show the note rendering on the MERGE-FAILED branch SPECIFICALLY — not on the merged branch, not on the truncation branch. This is an explicit AC, not a note or comment.
  Three separate tests, one per path. **(iii) is the regression that matters** and is the lane's acceptance artifact.
- **AC8.4** The note **never co-exists with another GLOBAL text**: the compacted context contains no "(overview unavailable …)" placeholder and no merged-text text when an authoritative note is present — because that text would assert the absence of an authoritative summary that demonstrably exists, and the v1.1 additive-band shape would have **co-rendered** with it (D25). The merge-failure placeholder is **SUPPRESSED**, not merely outranked.
- **AC8.5 (NEW — the `previous_overview` suppression predicate, architect v1.2)** The `if previous_overview:` line (`daemon/compaction.py:996-997`, "Previous overview: …") is **SUPPRESSED when a note is consumed**. Reason: after a note-guided compaction the prior doc's GLOBAL **is** the note, so `_extract_previous_overview` (`:222`) would re-emit the **OLD** note verbatim — **uncapped** (that line precedes the cap check) — directly above the new note: unbounded cross-pass duplication. **PIN: two consecutive note-guided compactions** ⇒ the second compacted context contains the NEW note exactly once and contains **no** "Previous overview:" line carrying the old note. **The no-note path keeps `previous_overview` seeding byte-identically** (the auto path's golden pin is unchanged).

### R9 — the note is INDEPENDENT of the merge pass (the core defect this lane fixes)

**Requirement.** C2: the note "must NOT depend on the merge pass succeeding". AR-4: "Stored" is not sufficient — the agent's **next turn** must actually see it.

- **AC9.1** Force `_merge_summaries` (`daemon/compaction.py:4306`) to return `("", False)` — **the existing failure-ladder shape (A-3; verified in-tree: every failure ladder returns `"", False`)** — → the compacted context STILL contains the note verbatim (AC8.1 re-asserted under merge failure). **The entry guard's return is `("", False)`, NOT `(None, False)`** — the architect's doc §D.1 wrote `(None, False)`, but that is WRONG (at `:4187` it would produce `summaries=[None]`); the correct value is `("", False)`, the existing failure-ladder shape. See `decisions.md` D29.
- **AC9.2** Under the same forced failure, the sections that survive the merge failure are **still present** — the note does not suppress or replace SECTION DETAIL; it replaces only the GLOBAL region.
- **AC9.3** **E2E (AR-4, mandatory):** a REAL conversation + a REAL note + the merge pass FORCED TO FAIL → the **agent's next turn** context contains the note. The assertion is made against the context the next turn actually receives, NOT against a stored field, NOT against the checkpoint alone, NOT against a log line. **This is the lane's acceptance artifact.**

### R10 — the honest tool contract: ARMED is not COMPACTED

**Requirement.** C3 + task §2. A tool that says "compacted" when it only armed is a lie.

**The receipt vocabulary.**

| Situation | What the tool returns | What it must NOT say |
|-----------|----------------------|---------------------|
| `compact=false`, no note | **Today's receipt, byte-identical** (the `_render_report` shape, `daemon/tools/prompt_reload.py:138`) | anything about compaction |
| `compact=true` + valid note | an **ARMED** receipt | "compacted", "summary rendered", "context compacted" |
| any refusal | the house REFUSED envelope with the typed code | any claim of a compaction |
| any internal failure | the house FAILED envelope with the typed code | any claim of a compaction |

- **AC10.1** The armed receipt's success token is drawn from a **distinct, frozen** set that does **not** include any "compacted" verb. **RULED (architect §H): the token is `outcome=armed_pending_seam`**, and the receipt carries `note_chars`, `cap`, `note_sha256[:12]`, `superseded_previous?`, the kill-switch state, the expected seam ("your next turn boundary — the quiescent pre-dispatch gate"), and the explicit line **"CONTEXT NOT YET COMPACTED — the compaction happens at your next turn boundary; until then this note remains armed."** (The plan's Rev-2 proposal `compact_note_armed` is superseded; OQ-5 is ANSWERED. The prior lane's frozen sets (`no_change`/`reloaded`/`reloaded_db`/`forced_reload`/`unproven` for the marker; the 6-value INFO enum) are **not** extended — the compact-note surface carries its **own** outcome namespace so the prior lane's pins do not move. Prior ledger D25's "the two sets are DIFFERENT BY DESIGN and must never be reconciled" discipline applied forward.) A **still-armed status line rides every subsequent `prompt_reload` receipt** (architect §H).
- **AC10.2** The armed receipt states the seam semantics in plain words ("will be applied at the next turn boundary"), and reports the note's **length** and **sha256 prefix** — never the note text.
- **AC10.3** **How the agent learns the compaction actually happened.** The primary receipt is **CONTEXT-VISIBLE**: because the note rides verbatim in the compacted context (R8), the agent's own next turn *reads its note back out of its context* — the presence of the note IS the proof it landed. This requires no new agent-facing channel and cannot be lost to a log the agent cannot read.
  - **A-4 (§H receipt vocabulary):** the §H receipt vocabulary carries the **anti-refire deferred clause VERBATIM** — both deferred states are recorded as `armed — deferred (anti-refire window), not compacted` and `armed — deferred (below minimum-compaction floor), not compacted` (AC10.5). The architect's §H signals 2-4 are the source of record.
- **AC10.4** A **secondary, explicit** outcome signal is also required: **RULED (architect §H, signal 1)** — the compacted doc's **envelope header** carries `note_sha8=<prefix> note_chars=<n>` + `note_guided=true` (an additive envelope field; FE reads defensively). "Provenance rides the *envelope*, not the GLOBAL slot, so the slot stays byte-equal to the note and the **verbatim contract holds exactly**. The agent recomputes the hash of what it authored and verifies its own next-turn context." The signal is content-free (length + hash prefix only), inside the compacted context, and never in a daemon log the agent cannot read.
- **AC10.5** If the compaction is **deferred**, the receipt to the agent MUST say **"deferred, not compacted"** and MUST NOT be a success receipt for work not done. **RULED (final consumption ruling + architect v1.3 A-4): THREE honest deferred states are recorded VERBATIM as receipt strings —** `armed — deferred (anti-refire window), not compacted`, `armed — deferred (below minimum-compaction floor), not compacted`, and `armed — still armed (last compaction produced no doc), not compacted` (the third state covers doc-less outcomes — `emergency_truncation` / `tail_truncation_last_effort` — which do not consume the arm; `armed — deferred (below minimum-compaction floor), not compacted` is the clean-skip state from the armed-aware floor predicate `skipped_below_min_messages_armed`). (Resident input (2); non-negotiable under either branch of ES-6. **The §H receipt vocabulary carries the anti-refire deferred clause VERBATIM** — architect `architecture-recommendation.md` §H signals 2-4; the architect's earlier "the receipt never needs a 'deferred by dedup' state" is **superseded** — with `force=False` the 60s window can bind, and the deferral is now mandatory disclosure.)

### R11 — content-free observability (the note never lands in an agent-readable log)

**Requirement.** Task §7 risk #9; resident input (3): the note is the agent's working memory and can carry intimate empire content. It rides verbatim in the **RECORD** and the **CONTEXT**; never in an agent-readable log.

**Doctrines cited (BINDING 2.3 — by name):**
- **Memoir content-free doctrine** — `docs/guidelines/guideline-memoir-reflection-lane.md` (the memoir lane's content-free convention; the memoir dream lane is the conceptual sibling of this lane, and its content-free discipline is the precedent this lane's D8 mirrors).
- **Decision Module P1 shadow-only content-free doctrine** — `daemon/services/decision/shadow.py` (the shadow writer's module docstring: "CONTENT-FREE BY CONSTRUCTION. Allowed: ids, hashes, scores, probabilities, counts, latencies, enum labels. NEVER: credentials, any part of an auth header, the request payload, the raw response body, or raw user content"; `sanitize_token` replaces anything that is not an opaque token with its sha256 prefix) and `daemon/tools/prompt_assent.py:789-792` (the house docstring pattern: "Content-free by construction (the 'logs content-free v1' doctrine): never persona, never diff content, never the raw or grouped token"). The critical note records the doctrine as "Decision Module P1 is SHADOW-ONLY — overlay observes+logs, never decides; seams 2/3/4 stay unregistered; Logs content-free v1 (ids/hashes/scores)."
- **House precedent applied:** the same shape as the prior lane's D24 one frozen INFO line per terminal path (counts/flags only, never persona or diff content) — `daemon/tools/prompt_reload.py:104` `_emit_info_line` + `daemon/services/prompt_refresh.py` `format_info_line`.

- **AC11.1** Every log line emitted by the note path carries the note's **length** and **sha256 prefix** only. A grep-pin asserts that a distinctive sentinel string planted in a test note appears in **zero** log lines.
- **AC11.2** The note text appears in exactly two places: the compaction **record** (the checkpoint) and the compacted **context**. A pin asserts the note is not written to any audit/ledger table that an agent-readable tool can query.
- **AC11.3** The armed/landed/deferred/refused outcome is observable to an **operator** (INFO line, frozen format, field order pinned) with no note content.

### R12 — no new data-loss surface (commission clause 4, AS REWORDED — see AR-3)

**Requirement.** C4. Per **AR-3**, clause 4's "removes nothing that survives today" had to be restated to apply to the **NOTE PATH**, with the pre-filter named as the **separately-authorized exception**. **RULED — the architect's rewording is ADOPTED verbatim** (ES-9): "**NO NEW DATA-LOSS SURFACE ON THE NOTE PATH:** … the note ADDS an authoritative layer and removes nothing that survives today. The pre-filter is the ONE separately-authorized exception: it may remove a candidate group only when the note's structured marker block mechanically accounts for that group (the seam-safety invariant), and only when the operator has armed it (default OFF)."

- **AC12.1 (note path — pure addition).** Everything compaction already preserves is preserved unchanged: the verbatim tail (`select_compactable_groups`, `daemon/compaction.py:1582`, re-attached at `daemon/compaction.py:3722-3732`), the silent-loss pre-write guard (`build_sentinel_replacement`, `daemon/compaction.py:317`, guard at `:481-497` raising `CompactionAborted` at `:177`), the orphan-tool-message pairing snap (`_snap_orphan_tool_messages_at_cut`, `daemon/compaction.py:1938`), the halving loop (`_last_effort_tail_truncation`, `daemon/compaction.py:2518`), the dedup window (`_is_recently_compacted`, `daemon/compaction.py:5091`, read at `:3126-3132`).
- **AC12.2** The note **adds** an authoritative layer; it **removes** nothing that survives today. A test compares the compacted doc's non-note regions against the same fixture compacted without a note, and asserts equality.
- **AC12.3 (pre-filter — the separate contract).** The pre-filter is the **only** removal surface, it is **separately authorized** (R-B), it is **default-OFF** (R-A), and it is covered by R14's containment law. The note path and the pre-filter path are two contracts and are never conflated in code, tests, or docs.

### R13 — the default path is byte-unchanged

**Requirement.** C5 + R-C.

- **AC13.1** `prompt_reload()` (no args) → byte-identical receipt (golden pin).
- **AC13.2** The **auto**-compaction path is byte-unchanged: a fixed fixture compacted by the automatic trigger produces a byte-identical compacted doc before and after this lane (golden pin on the doc).
- **AC13.3** No registration-surface change: `daemon/tools/_tool_registry.py`, the privileged-set census (23 members today — see §14), the ari `tools.allow` list (42 entries today), and `tool-categories.json` are **unmodified** by this lane.
- **AC13.4** Exactly **one** existing pin must move, and it must move for a **stated** reason: `tests/unit/tools/test_prompt_reload_tool.py` pins "zero conversation-history writes" (its docstring at `:14`, the leg at `:456`). The compact-note path writes the note to the **compaction carrier** — which is a conversation-history-adjacent write — so that pin's SCOPE must be narrowed to "the prompt_reload path writes no conversation history **except** the compact-note carrier write, which is the compaction system's own write". The pin must be amended with that scope wording, not deleted, and not weakened to a no-op.
- **AC13.5** **Byte-identity fixtures carry NO armed note (A-2).** The byte-identity golden fixtures (the `prompt_reload()` receipt pin and the auto-compacted doc golden) are authored in the **unarmed** state, so the still-armed receipt line is **additive only in the armed state** — it must not appear in the unarmed fixtures. A test asserts the unarmed fixtures are byte-identical to their pre-change values AND that the armed-state fixtures carry the additive still-armed line.

### R14 — the pre-filter is DORMANT and contained (shape RULED)

> **A-5 (second half) — RESOLVED by the architect's v1.3 (§D.3, A-5 ruling).** The staleness mechanism is **RETIRED — there is NO staleness mechanism in v1.** An armed note **persists until consumed or replaced**; no TTL, no expiry, no staleness state. Reasons: (1) any silent expiry would be exactly the "disclosed swallow is still a swallow" the resident forbade — a deliberate act deleted by the system without a receipt; (2) there is **exactly one arm per instance** — a second note REPLACES the first with disclosure (§G), so there is no accumulation to bound; (3) the agent always knows an arm is parked (the still-armed status line + any deferred state ride every subsequent `prompt_reload` receipt); (4) the rate limit bounds *arming frequency*, which is the actual abuse surface. If the Emperor later wants bounded staleness, it MUST be an **honest typed refusal/notice at arm-read time** — never a silent expiry. **The plan's ES-7 crit 4 / Risk #4 rows are amended to point here** (an unruled mechanism in a plan is a future bug report — this one is now ruled: retired). Risk #4's "stale note rides a later compaction" mitigation is updated to: **there is no staleness state; a mismatched generation produces a deferred (never a silent landing), and the arm persists until consumed or replaced.**

> ### ⚠ SUBSTITUTION DISCLOSURE — READ FIRST
>
> **The v1 pre-filter is a DETERMINISTIC ZERO-LLM MARKER MATCHER — NOT THE DECISION ENGINE the Emperor named.**
>
> The Emperor's directive authorized *jev* filtering; the ratified doctrine says the overlay never decides. The measured truth: **the decision family structurally cannot express "drop this message."** Wiring the pre-filter to it would require (1) removing **EX-2** — a standing Emperor-ratified exclusion (`daemon/services/decision/registry.py:82-90`, `compaction_message_selection` at `:85`; registration refused at `:275-280`), (2) adding a removal-admitting monotonicity direction — the `Monotonicity` enum (`daemon/services/decision/types.py:92-97`) admits only `TOWARD_COMPLETION / TOWARD_TIGHTENING / WITHIN_SET / ADVISORY_ONLY / NONE`, and (3) a ratified `ReviewRecord` through the D3 gate (`transition_stage` `daemon/services/decision/registry.py:355-387`; `is_deciding_permitted` `:389-399`). All three are the Emperor's, and AR-2(e) forbids smuggling any of them into this release. **Even if all three landed, an armed deciding filter whose answer is "dropped" violates the superset rule and fails open to the heuristic — silently no-ops.**
>
> **What ships instead:** a **purpose-bound, non-family, default-OFF substitute** — a deterministic in-process matcher with its own model-resolution *pattern* (à la `resolve_compaction_model`), its own budget, its own kill lever, a single attempt, and **identity on every failure**. It is **never** registered on the decision registry, **never** called through `DecisionOverlayPort`, **never** billed to the decision ledger. **The decision family is UNTOUCHED** — S1 shadow-only detached, EX-2 intact, D3 gate intact, USD 50/mo ceiling intact. **This substitution is owed to the Emperor prominently** (architect §B/§C; the "Pre-filter decision matrix"; the architect's doc now opens with the same disclosure).

**Requirement.** R-A (one release, two postures), AR-1 (invariant), AR-2 (containment). The Emperor's direction opens the capability; the **shape is RULED** (ES-2/ES-8).

- **AC14.1** The pre-filter ships **DEFAULT-OFF** and disabled.
- **AC14.2** With the pre-filter OFF, the note path's output is **byte-identical** to its output with the pre-filter feature entirely absent (golden pin).
- **AC14.3** The pre-filter runs **only** when a note is present. A test asserts no filter invocation on a note-less compaction. The gate is `enabled AND note_present` — no note ⇒ short-circuit before any list copy, LLM call, or arm-state read.
- **AC14.4** Degradation is always **skip-the-filter**, never **fail-the-compaction**: pre-filter unavailable, over budget, erroring, or timing out → the compaction proceeds normally. A test injects each failure mode and asserts the compaction still lands. **RULED (§F): every failure converges to identity, and identity is the SAME CODE PATH as "off"** — not two paths that happen to agree.
- **AC14.5** **AR-1 invariant, verbatim:** "pre-filter may drop a message only when the note demonstrably ACCOUNTS FOR IT; anything else falls through UNCHANGED." Corollary: "accounted for" must be **mechanically decidable** at filter time — never a judgment call; if it is not decidable, the message falls through unchanged. **RULED (§E):** "drop a candidate group only when the note's **structured marker block** mechanically accounts for that group (exact message-id or group-index-span match)"; an absent or unparseable marker block yields an **empty drop-set** — free-text notes make the filter **vacuous by construction**. **Two hard walls ride with it: the never-empty wall** (a drop-set that would empty a non-empty candidate set is discarded → identity) **and whole-group atomicity** (drops operate on whole `MessageGroup`s only). Dropped groups are itemized in the envelope's existing dropped-spans clause — never silently vanished.
- **AC14.6** The pre-filter has its **own budget** and its **own kill lever**, independent of the compaction's budget and of the note cap. **RULED (§C/SCOPE-5):** the kill lever is one `CompactionConfig` bool — `compact_note_prefilter_enabled` (the `proactive_enabled` precedent at `daemon/config.py:1686`), aliased, **code-default OFF, env-overridable, and deliberately NOT set in `config.yaml`**; the v1 occupant is deterministic (zero LLM), so there is **no LLM budget knob** — the future LLM occupant's contract is `min(5s, 10% of remaining operation budget)`, specified in the doc only.
- **AC14.7** The P1 shadow-only doctrine ("Decision Module P1 is SHADOW-ONLY — overlay observes+logs, never decides") **STAYS RATIFIED**. Whether the decision-engine overlay may ever become a *deciding* surface is a **doctrine amendment** and is an **Emperor** question (AR-5, ES-10 — **filled: the lane's design is inert to the answer; the pre-filter never touches the family**) — not this lane's to decide, and not smuggled in by this release.

### R15 — Guideline deliverable

**Requirement.** R-D. `docs/guidelines/guideline-prompt-reload-compact-note.md`, status `draft`, written **after** tester sign-off per the lifecycle (`docs/guidelines/README.md` §4).

- **AC15.1** The guideline exists and carries `status: draft` in the house's draft form.
- **AC15.2** It addresses `docs/guidelines/README.md` checklist **item 3** (refusal codes carry contract meaning an agent/operator must not misread — this lane's **five** new codes each get a one-line contract meaning) and **item 4** (the feature interacts with a privileged tool category — `prompt-reload` is PRIVILEGED, entry #20 of 23 today, §14).
- **AC15.3** It carries the kinship doctrine line (C6) **verbatim**: *"the working-context counterpart of the memoir dream lane — the dream consolidates her episodic memory; the compact note consolidates her working memory."*
- **AC15.4** It is written **after** tester sign-off (lifecycle order), so in this lane it is a **phase task**, not a delivered artifact.

### R16 — the two arm-time refusals that would strand an arm

**Requirement.** The architect's fourth new refusal (§G): `prompt_reload_compact_disabled` — "`compact=true` while `CompactionConfig.enabled=false`" → "REFUSED — **arming a note that can never land would strand it**" — plus the final consumption ruling's arm-time rate limit. Both are **additive** (R16 continues the set's own numbering; no existing R/AC id is renumbered).

- **AC16.1** `prompt_reload(compact=True, compact_note="…")` while `CompactionConfig.enabled=false` → REFUSED `prompt_reload_compact_disabled`; **nothing armed** (the arm carrier is untouched); the message says the note cannot land, and never claims a compaction was requested or performed.
- **AC16.2** Arming while the deliberate-compact rate limit is in force (`DELIBERATE_COMPACT_RATE_LIMIT_S = 900`, a **hardcoded module constant — NO knob**; clock = `compact_note_last_applied_at`, counting DELIBERATE compactions only) → REFUSED `prompt_reload_compact_rate_limited`, **echoing the remaining seconds**; **nothing armed**; the message never claims a compaction was requested or performed.

---

## 4. The refusal vocabulary (frozen)

Envelope shapes are the house's, byte-identical to the prior lane (`daemon/tools/prompt_reload.py:95` REFUSED, `:99` FAILED):

```
Error: prompt_reload REFUSED — reason={code}: {message}
Error: prompt_reload FAILED  — reason={code}: {message}
```

**The existing 10 codes (9 REFUSED + 1 FAILED) are UNCHANGED.** The compact-note surface adds **5 REFUSED** codes. Total after this lane: **14 REFUSED + 1 FAILED**.

**Four are the ARCHITECT'S, verbatim** (architect `architecture-recommendation.md` §G "Cap + refusal vocabulary"), plus **one from the final consumption ruling** (leader injection, 2026-11-17):

| # | Code | Trigger | Envelope | Honesty rule |
|---|------|---------|----------|--------------|
| 1 | `prompt_reload_note_required` | `compact=true` with note **absent or whitespace-only** (one code — "forking them buys nothing") | REFUSED | says the note is REQUIRED; **nothing armed**; never says a compaction was requested or performed |
| 2 | `prompt_reload_note_over_cap` | note length > **12,000** chars | REFUSED | **echoes chars + cap; never truncates; nothing armed** |
| 3 | `prompt_reload_note_requires_compact` | note supplied with `compact=false` | REFUSED — "ignoring is dishonest" | never claims a compaction was requested |
| 4 | `prompt_reload_compact_disabled` | `compact=true` while `CompactionConfig.enabled=false` | REFUSED — "arming a note that can never land would strand it" | never claims a compaction was requested or performed |
| 5 | `prompt_reload_compact_rate_limited` | arming while the **deliberate-compact rate limit** (`DELIBERATE_COMPACT_RATE_LIMIT_S = 900`) is in force | REFUSED — enforced **at arm time** | **echoes the remaining seconds** (honest "not now, here's when"); **nothing armed**; never claims a compaction was requested or performed |

**Reconciliation with the plan's Rev-2 five-code set** (the architect's four + the rate-limit refusal supersede it; the count is reconciled here, and the mapping is recorded in `decisions.md` D22):

| Rev-2 plan code | Final disposition |
|---|---|
| `prompt_reload_compact_note_required` | **RENAMED** → `prompt_reload_note_required` |
| `prompt_reload_compact_note_empty` | **MERGED** → `prompt_reload_note_required` (absent and whitespace-only share one code) |
| `prompt_reload_compact_note_too_large` | **RENAMED** → `prompt_reload_note_over_cap` |
| `prompt_reload_compact_note_already_armed` | **RETIRED** — the second-note case is REPLACE-with-disclosure (Part 1b); no genuine refusing case remains for it |
| `prompt_reload_compact_note_without_compact` | **RENAMED** → `prompt_reload_note_requires_compact` |
| — | **NEW from the architect:** `prompt_reload_compact_disabled` |
| — | **NEW from the final consumption ruling:** `prompt_reload_compact_rate_limited` |

**The final number, stated explicitly: 5 new REFUSED codes → 14 REFUSED + 1 FAILED in total.** (Rev 2's "5 new" was a different set of five; the count coincides, the membership does not — the mapping above is the authoritative reconciliation.)

**Required properties of the vocabulary.**

- **P1 — every code distinguishes ARMED from COMPACTED.** No code in this table may be reachable on a path where a compaction actually happened, and no code may be emitted *after* a successful landing. The architect's §G puts it harder: "**Every refusal is honest: the tool only ARMS.** No code path can emit a success-for-compaction receipt — the tool body writes the arm and returns; it structurally cannot observe a compaction."
- **P2 — refusal ≠ error.** The house treats a refusal as a design outcome (`docs/guidelines/README.md` item 3). These four are design outcomes, not failures; they ride the REFUSED envelope, never the FAILED envelope.
- **P3 — the FAILED set stays at one.** `prompt_reload_internal_error` remains the only FAILED code. No compact-note condition may route to it — a too-long note is a design outcome, not an internal error.
- **P4 — the note is never echoed in a refusal message.** Refusal messages quote the cap and the LENGTH (or the digest prefix), never the text.
- **P5 — the codes are enumerable and pinnable.** `REFUSAL_CODES` (`daemon/services/prompt_refresh.py:160`) is extended by exactly these four; a test asserts the frozenset's exact membership and count. The refusal-code count pin must be **re-derived by grep**, never copied (the architect says the same: "the 'codes 1-9' comment and the `prompt_reload_refusals_unit` pin are **re-derived, never copied**").

**Where the codes must be registered.** `daemon/services/prompt_refresh.py` (the code constants + `REFUSAL_CODES` frozenset) and the tool surface at `daemon/tools/prompt_reload.py`. The tool's self-doc (`_build_full_doc()`, `daemon/tools/prompt_reload.py:318`) is generated from `REFUSAL_CODES`/`FAILURE_CODES`, so the new codes appear in the tool's own help automatically — a test asserts that.

---

## 5. The honest tool contract (receipts)

### 5.1 `compact=false` (today's shape)

**Byte-identical to today.** The receipt is the existing `_render_report` output (`daemon/tools/prompt_reload.py:138`) — `prompt_reload {outcome} — {message}` plus the frozen provenance block and the prior lane's marker. **Nothing is added to it.** This is C5's "purely additive surface" read at the receipt level.

### 5.2 `compact=true` + a valid note — the ARMED receipt

The compaction happens at a **later** seam. The receipt therefore describes an **arming**, not a compaction.

Required content of the armed receipt:

| Element | Content | Why |
|---------|---------|-----|
| outcome token | **`armed_pending_seam`** (architect §H; frozen literal, pinned) | a token that cannot be read as "compacted" |
| seam statement | "applies at the next turn boundary" (plain words) | the agent must know WHEN, not just THAT |
| note length | integer | content-free observability (R11) |
| note digest | sha256 prefix | content-free observability + the agent can verify its own note |
| armed state | explicitly ARMED | distinguishes from landed/deferred/refused |
| prior-note state | whether a note was already armed | makes R5's refusal actionable |

**Forbidden in the armed receipt:** the words "compacted", "compaction complete", "context compacted", "summary rendered", "done" in a sense that implies the compaction ran. A grep-pin asserts these substrings are absent from the armed receipt literal.

### 5.3 How the agent learns the compaction actually happened

Three channels, all honest:

1. **CONTEXT-VISIBLE (primary).** The note rides verbatim in the compacted context (R8). The agent's next turn reads its own note out of its own context. **The note's presence IS the receipt.** No new agent-facing tool, no new event, nothing to lose.
2. **IN-CONTEXT OUTCOME MARKER (secondary).** The compacted doc states that the GLOBAL region was supplied by an authored note (not by a machine merge). Content-free (length + hash prefix). Exact shape: §13 ES-4.
3. **TOOL RETURN (arming only).** The tool return says ARMED. It never upgrades itself to "compacted" — the tool has already returned by then.

### 5.4 The deferred case

If the compaction is deferred, the agent must be told **"deferred, not compacted"** — never a success receipt. **RULED (final consumption ruling):** both deferred states are verbatim receipt strings — `armed — deferred (anti-refire window), not compacted` and `armed — deferred (below minimum-compaction floor), not compacted`. The vocabulary is `armed` → (`landed` | `deferred` | `refused`).

---

## 6. The note cap

### 6.0 Authority — RESOLVED (architect ruling, 2026-11-17)

The commission said **"cap: architect sets"**. The architect has now ruled (`architecture-recommendation.md` §G "Cap + refusal vocabulary"). The plan's Rev-2 standing proposal (8,000) is **SUPERSEDED**; the architect's value is **authoritative**.

**The delta, recorded:** the plan proposed **8,000** chars; the architect ruled **12,000** chars (`decisions.md` D21/D22). **The plan's budgeting reasoning was ADOPTED** — the architect's §D budget policy rules exactly what this plan proposed (a separate, never-condensed doc region charged against the 15% doc ceiling, **not** folded into `GLOBAL_OVERVIEW_TOKEN_CAP = 600`), and it states the implementation mechanism more precisely than the plan did (see §6.3).

**No new ES slot, no renumbering:** the cap authority is referenced inside the existing slot criteria (ES-1's ceiling-rule criterion and ES-4's render criterion) — both are now **RULED** (§13).

### 6.1 The ruled cap

> **CAP = 12,000 characters** — `CompactionConfig.compact_note_max_chars` (aliased field, default 12000).

The architect's reason, verbatim: *"the Emperor's stated instinct is '~8–16k'; 12k is its midpoint, comfortably inside budget on any window (§D), generous enough that a rich settled/open/dead account never hits the refusal, tight enough to keep the doc honest. The council's 4k/6k was justified against the 600-token GLOBAL cap — a justification my budget ruling already absorbs (counted in the ceiling, never render-truncated). **Co-pinned in tests against `GLOBAL_OVERVIEW_TOKEN_CAP`** (drift guard). Flip condition: if profiling shows doc-assembly or FE cost at 12k, drop to 8k (the Emperor's floor)."*

Boundary semantics: **inclusive** — a note of exactly 12,000 chars is accepted; 12,001 is refused with `prompt_reload_note_over_cap` (AC3.1/AC3.2).

### 6.2 The plan's original proposal, and why it was overridden (record kept)

The plan proposed **8,000** with this reasoning: the binding constraint is **the compaction doc's own ceiling rule**, not taste — `COMPACTION_DOC_CEILING_FRACTION = 0.15` (`daemon/compaction.py:168`), the ceiling condensing **oldest sections first, never GLOBAL** (`:985-991`). A 16,000-char note ≈ 4,000 tokens ≈ **~21%** of the 19,200-token doc ceiling at a 128k window — it would crowd out SECTION DETAIL wholesale; 8k ≈ 2,000 tokens ≈ **~10%**.

**Why the architect chose 12,000 instead:** the ceiling arithmetic holds comfortably at 12k too (see §6.3) — the note is "~0.43% of a 700k window and ~2.9% of the 15%-ceiling allowance (105k tokens)" — so the marginal safety the plan bought at 8k is not needed on realistic windows, while 12k is "generous enough that a rich settled/open/dead account never hits the refusal". The architect also records the **flip condition** (drop to 8k if profiling shows doc-assembly or FE cost at 12k). The plan's reasoning is retained here because its **budget half** was adopted; only its **number** was overridden.

### 6.3 Interaction with the token-based compaction budget (the architect's mechanism)

**Rule B1 — the note is a SEPARATE doc region; it is NOT folded into `GLOBAL_OVERVIEW_TOKEN_CAP`.**
The architect (§D) is explicit: the note is "**NOT subject to `GLOBAL_OVERVIEW_TOKEN_CAP` (600) render-time truncation** (`:1012-1021`) — a note riding the `global_overview` parameter would be silently truncated at ~600 tokens, directly violating 'typed refusal, NEVER silent truncation' (this overrules W3's slot-(ii) recommendation)". The plan's B1 is **ADOPTED**.

**Rule B2 — the note is never condensed.**
The architect: the note is "**counted** in the `_apply_ceiling_rule` numerator when present (never-condensed class, alongside GLOBAL) and **never render-truncated**". The ceiling rule condenses the oldest sections first; the note is never a condensation candidate.

**Rule B3 — the note's cost is charged against the doc ceiling. (DELTA: the mechanism is the architect's.)**
Implementation per §D: `note_tokens = estimate(note) if note else 0` added to the numerator at `daemon/compaction.py:788` (`_apply_ceiling_rule`, def) — **"with no note the computation is bit-identical (the estimate is skipped entirely, not estimated-as-zero)"**. The plan's Rev-2 wording ("computed with the same estimator … the doc reports the charge") is adopted in substance and **refined** by the architect: the estimate is **skipped**, not estimated-as-zero.

**Rule B4 — the note cannot breach the ceiling by itself, on any supported window. (DELTA: the numbers moved with the cap.)**
At the ruled 12,000-char cap (~3k tokens) the note is "~0.43% of a 700k window and ~2.9% of the 15%-ceiling allowance (105k tokens)"; on small windows the ceiling condenses sections — never the note — which is the intended authority ordering. **Disclosed (architect):** on a 32k window a max-cap note could squeeze sections heavily. The plan's Rev-2 B4 sentence ("stays under the 15% doc ceiling for any context window ≥ ~16k tokens") is superseded by this measurement.

**Rule B5 — the cap is a `CompactionConfig` field, named `compact_note_max_chars`.**
`CompactionConfig` (`daemon/config.py:1602`) has `env_prefix="COMPACTION_"` and the house `extra="forbid"` typo guard (`:5614`, `:5634`) — an unknown YAML key is **refused at boot**. The cap field uses the house `AliasChoices` idiom (`daemon/config.py:1738-1753`, the `dedup_window_s` pattern).

**Rule B6 — the env kill-switch precedence caveat is disclosed.**
pydantic-settings gives **init kwargs priority over env** (`daemon/config.py:7562` `_resolve_compaction_model`, `:7906` `_resolve_proactive_enabled` are the house's explicit resolvers). A YAML value can therefore silently defeat an operator's env override unless an explicit resolver is added in `load_config`.

**Rule B7 — TWO knobs, not one (DELTA from the plan's Rev-2 "one knob only").**
The architect's SCOPE item 5: "**No new knobs beyond two** — `compact_note_max_chars` (default 12000) and `compact_note_prefilter_enabled` (default **OFF**, env-only override, deliberately absent from `config.yaml`)." The second knob is the **pre-filter kill lever** (ES-8, now RULED) — it is not the ES-6 rate-limit constant, which the architect leaves unspecified ("The future LLM occupant's budget/timeout are **specified in this document only** — not added as config now"). The plan's F4 carve-out ("no new env knobs beyond the note cap **unless the architect's ruling demands it**") is therefore **exercised**, not violated — recorded in `decisions.md` D23.

---

## 7. The seam-safety law

**The law.** A compact-note compaction is triggered **only** at a turn boundary. Never mid-turn. Never inside the tool call.

**Why the mid-turn triggers are excluded.** **SIX live `compact_state` call sites sit under FOUR triggers** (verified in-worktree 2026-11-17): T1 — the `compact_state` call **inside** `_maybe_precall_compact_95` at `daemon/graph.py:8394` (def `:8138`; the helper's graph-level invocation is at `:9722`); T2 — the CLE reactive handler (`except ContextLengthExceededError:` at `daemon/graph.py:9890`) at `daemon/graph.py:9990/9998/10019` (plus a commented-out `:9957`); T3 — `daemon/services/instance_messaging.py:1535` (`_maybe_compact_context`, def `:1337`); T4 — `daemon/services/compact_executor.py:1220` (`execute_compact`, def `:555`). T1 and T2 run **inside** a live turn, with the message list in hand. A note-armed compaction fired from either would mutate the conversation the agent is still using. T3 and T4 are turn-boundary shapes. (The architect's §0.1 reaches the same taxonomy and adds that "never mid-turn" is a constraint this lane **imposes on the tool-initiated path**, not an inherited property of the system.)

**Which of T3/T4 the note-armed hook uses — RULED (final consumption ruling).** The forcing lives in the **T3 gate as a THRESHOLD BYPASS** (`daemon/services/instance_messaging.py:1535`); T4 (`/compact`, `daemon/services/compact_executor.py:1220`) also consumes an armed note. An armed note **FORCES** a note-guided compaction at the next quiescent seam — the **80% threshold is BYPASSED**, because "the gate measures SIZE, the note measures RELEVANCE (the Emperor's use case is true at 30% fill as at 90%)".

**⛔ NAMED HAZARD — the `force` flag (verbatim class).** *"calling the engine with force=True on an armed note below the floor triggers 50%-tail truncation — FORBIDDEN."* The armed path calls the engine with **`force=False`, NEVER `force=True`**. This is **load-bearing, not stylistic**: `force=True` below the minimum floor does **not** skip — it falls through to `_last_effort_tail_truncation` (`daemon/compaction.py:3357-3386`), a **50%-tail truncation that drops half the verbatim tail**. `force=False` inherits the engine's own floor: `skipped_below_min_messages` (`:3368-3370`) — a clean skip, and **the arm persists**. The floor is `min_messages_before_compaction` (default **10** — `daemon/config.py:1660`; constant `MIN_MESSAGES_BEFORE_COMPACTION: int = 10` at `daemon/constants.py:603`).

**Anti-abuse and the honest deferral (RULED).** The deliberate-compact rate limit is **`DELIBERATE_COMPACT_RATE_LIMIT_S = 900`** (15 min) — a **HARDCODED module constant, NO KNOB** — enforced **at arm time** as `prompt_reload_compact_rate_limited`, echoing the remaining seconds; clock = `compact_note_last_applied_at`, counting DELIBERATE compactions only. With `force=False` the **60s dedup window CAN bind** (worst case one seam-cycle, only after a just-fired automatic compaction) — **accepted** — and the honest receipt is load-bearing: **both** deferred states are recorded verbatim as `armed — deferred (anti-refire window), not compacted` and `armed — deferred (below minimum-compaction floor), not compacted`. **Never a success receipt for work not done.**

**The no-arm path is byte-identical by construction.** The arm predicate short-circuits false when no arm exists; the default path is untouched (AC13.2's golden pin).

---

## 8. The scope fence (what this lane must NOT build)

| # | Forbidden | Note |
|---|-----------|------|
| F1 | A new agent roster entry (`agents/**`) | **RULED:** ES-1 makes the dreamer a *mode* of the existing pipeline, not a roster entry (architect SCOPE item 2) |
| F2 | A parallel compaction engine | Overridden only by an explicit written ES-1 ruling |
| F3 | Any change to the default compaction path (T1/T2/T3/T4, the pre-call hook, the reactive handler, the ceiling rules, the sentinel mechanics, the dedup/escalation gates) — **except** the ONE architect-ruled note-armed forced turn-boundary hook | R-C |
| F4 | New env knobs beyond the note cap | ES-8 may add the pre-filter's; nothing else |
| F5 | Any change to the memoir lane | Kinship is documentation, not code |
| F6 | Edits to `agents/**` prompt artifacts | DB-versioned + human-approval-only |
| F7 | A new table / migration | ES-3's criteria; a ruling that requires one is recorded here first |
| F8 | A parallel/duplicate note-store (a second place the note lives) | The note lives in exactly one carrier (§13 ES-3) |
| F9 | Content-bearing log lines for the note | R11 |
| F10 | Silently swallowing a deferred compaction behind a success receipt | R10 AC10.5 |
| F11 | Editing the prior lane's `prompt-reload/` docs | Read-only prior art |
| F12 | Merge / push / upstream-set / editing `origin/latest` | The leader owns the finalize step |

---

## 9. Phase plan

**Gate order: plan → architect → councils → build.** All eleven ES slots are now RULED (architect `f159b3e3` + the final consumption ruling), so no phase is architect-gated; **Phase 1 remains HELD until the plan clears its council** (the leader dispatches the build).

| Phase | Name | Objective | Tasks | Coupling | Gate |
|-------|------|-----------|-------|----------|------|
| 1 | Contract surface: params, cap, refusals, receipts | The tool's public contract is fixed, implemented, and pinned — with NO engine dependency | 8 | independent of the engine; tight with Phase 2 (the carrier) | **HELD** — the architect's engine ruling (**f159b3e3**) and the **final consumption ruling** have both landed, so the contract is settled; the remaining gate is the **council** (gate order plan → architect → councils → build). The leader dispatches the build when the plan clears its council. |
| 2 | The note carrier (GATED: ES-3) | The armed note is carried from the tool call to the seam, survives pause/quiesce/resume, and is consumed exactly once | 6 | tight with Phase 3 (the render) and Phase 5 (the E2E) | **ES-3 ✅ RULED** (carrier) · **ES-7 ✅ RULED** (consumption atomicity) |
| 3 | The note render (GATED: ES-4) | The note renders verbatim in the GLOBAL region as the branch's FIRST ARM, independent of the merge pass by construction | 7 | tight with Phase 2 | **ES-4 ✅ RULED** (v1.2: first arm of the GLOBAL branch; merge skipped by the `_merge_summaries` entry guard; marker on the envelope) |
| 4 | The note-armed seam hook (GATED: ES-5, ES-6) | The compaction fires at a turn boundary, exactly once, with honest deferral | 6 | tight with Phase 2 (carrier) and Phase 3 (render) | **ES-5, ES-6 ✅ RULED** (final consumption ruling) — the T3 threshold bypass; `force=False` NEVER `force=True`; 900s hardcoded rate limit; both deferred receipts verbatim |
| 5 | The pre-filter, DORMANT (GATED: ES-2, ES-8) | A default-OFF, contained, mechanically-decidable filter that ships disabled | 6 | loose with Phase 3 (the render's input set) | **ES-2, ES-8 ✅ RULED** |
| 6 | Acceptance, registration, Guideline | The pack is registered, the E2E artifact exists, the Guideline is drafted after tester sign-off | 7 | tight with all prior phases | Phase 6 |

### Phase 1 — Contract surface (engine-independent) — 8 tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1.1 | Add `compact` / `compact_note` to `PromptReloadInput` + the tool body | none | AC1.1, AC1.3 |
| 1.2 | Implement the five refusal codes + envelope routing | 1.1 | AC2.1, AC3.1, AC4.1, AC4.2, AC5.1, AC6.1; AC10.4's code set |
| 1.3 | Implement the cap field on `CompactionConfig` — `compact_note_max_chars` (aliased, default **12000**) + the `load_config` resolver | 1.1 | AC3.1, AC3.2, R6's B5/B6 |
| 1.4 | Implement the ARMED receipt + the frozen `compact_note_armed` token | 1.1 | AC10.1, AC10.2 |
| 1.5 | Content-free INFO line (length + sha256 prefix), frozen field order | 1.2 | AC11.1, AC11.3 |
| 1.6 | Golden-pin today's `prompt_reload()` receipt byte-for-byte | none | AC1.2, AC13.1 |
| 1.7 | Narrow the "zero conversation-history writes" pin to the stated scope | none | AC13.4 |
| 1.8 | Extend `test_prompt_reload_refusals.py` (15 → 20 tests) + the `REFUSAL_CODES` membership pin | 1.2 | §4 P5; AC2.1..AC6.1 |

**Exit criterion.** The tool's contract is implemented, pinned, and reviewable. `prompt_reload(compact=True)` arms or refuses; it never compacts yet, and it never says it compacted. **BINDING 3:** the receipt vocabulary is exposed to architect amendment (the "deferred, not compacted" case may change the receipt's shape), so Phase 1 does NOT start until the plan clears its council — the commission's gate order is plan → architect → councils → build. "Engine-independent" describes the contract's *content*, not its *schedule*.

### Phase 2 — The note carrier (GATED on ES-3) — 6 tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 2.1 | Implement the carrier the architect rules | ES-3 | ES-3 criteria |
| 2.2 | Arm: write the note + its length/digest to the carrier | 1.2, 2.1 | AC10.2, AC11.1 |
| 2.3 | Arm-conflict detection (R5) | 2.2 | AC5.1, AC5.2, AC5.3 |
| 2.4 | Arm-receipt integration | 2.2 | AC10.1..AC10.3 |
| 2.5 | Exactly-once consumption | ES-7 | AC12.1 (no double-application), ES-7 criteria |
| 2.6 | No cross-instance leakage test | 2.1 | ES-3 criteria |

**Exit criterion.** A note survives arm → pause → quiesce → resume → seam, and is consumed exactly once.

### Phase 3 — The note render (GATED on ES-4) — 6 tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 3.1 | Implement the render mechanism the architect rules | ES-4 | ES-4 criteria |
| 3.2 | Render the note verbatim in the GLOBAL region | 3.1 | AC8.1, AC8.2 |
| 3.3 | Branch (i): a merged GLOBAL is present — **auto path only** (unreachable when a note is consumed; the merge is skipped by the entry guard) | 3.2 | AC8.3(i) |
| 3.4 | Branch (ii): `mode == "truncation"` — **auto path only** (unreachable when a note is consumed) | 3.2 | AC8.3(ii) |
| 3.5 | The merge-failed path (the merge is skipped ⇒ `global_overview = None` at `daemon/compaction.py:3697` / `:3778`) — the note-first gate supplies the GLOBAL region | 3.2 | AC8.3(iii), AC8.4, AC9.1 |
| 3.6 | Implement the `_merge_summaries` **entry guard** — the skip's single home (the architect's §D.1 placement spec, verbatim in `decisions.md` D29): `if context.compact_note: logger.info("merge skipped: note-guided mode (iid=%s)", iid8); return ("", False)` as the **FIRST STATEMENT** of `daemon/compaction.py:4306` `_merge_summaries`. **OWNING TASK for the entry guard** (approver #2) — covers merge sites 1/2/3 and every future site by construction. | 3.1 | AC8.3, AC8.4, AC9.1, L10 |
| 3.6 | The in-context outcome marker (content-free) | 3.1 | AC10.4 |

**Exit criterion.** The note renders, verbatim, in the GLOBAL region, on all three branches — proven by the merge-failure test.

### Phase 4 — The note-armed seam hook (GATED on ES-5, ES-6) — 6 tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 4.1 | Implement the turn-boundary hook the architect rules | ES-5 | AC7.2, AC7.3 |
| 4.2 | No mid-turn mutation (T1/T2 untouched) | 4.1 | AC7.1, AC7.3 |
| 4.3 | The dedup-window interaction | ES-6 | ES-6 criteria, AC10.5 |
| 4.4 | The honest deferral receipt | 4.3 | AC10.5 |
| 4.5 | Land → the armed slot is cleared atomically with the compaction write | 4.1 | ES-7 criteria |
| 4.6 | Default-path byte-unchanged golden pin | 4.1 | AC13.2 |

**Exit criterion.** A note-armed compaction fires exactly once, at a turn boundary, and the auto path is provably unchanged.

### Phase 5 — The pre-filter, DORMANT (GATED on ES-2, ES-8) — 6 tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 5.1 | Implement the filter shape the architect rules (or the explicit no-op-until-armed declaration) | ES-2 | ES-2 criteria |
| 5.2 | Default-OFF + kill lever + own budget | 5.1 | AC14.1, AC14.6 |
| 5.3 | Runs only when a note is present | 5.1 | AC14.3 |
| 5.4 | Degradation = skip-the-filter, never fail-the-compaction | 5.1 | AC14.4 |
| 5.5 | The AR-1 decidability law + its seam-safety proof | 5.1 | AC14.5 |
| 5.6 | OFF ⇒ byte-identical golden pin | 5.2 | AC14.2 |

**Exit criterion.** The filter ships **disabled**; with it OFF the note path is byte-identical to its absence.

### Phase 6 — Acceptance, registration, Guideline — 7 tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 6.1 | The new pack (`test/packs/prompt_reload_compact_note.sh`) + its **PACKS.md registration** | all | §12 L0 |
| 6.2 | The AR-4 E2E: real conversation + real note + merge forced to fail → the next turn sees the note | 3, 4 | AC9.3 |
| 6.3 | The proof artifact (the next-turn context, with the note visible) | 6.2 | AR-4 |
| 6.4 | The refusal-family legs (all five codes + the envelopes) | 1 | §12 L3 |
| 6.5 | The seam-safety legs (no mid-turn mutation; T1/T2 untouched) | 4 | §12 L4 |
| 6.6 | The default-path byte-unchanged legs | 1, 4 | §12 L8 |
| 6.7 | The Guideline draft (`docs/guidelines/guideline-prompt-reload-compact-note.md`, status `draft`) — **after** tester sign-off | 6.1–6.6 | AC15.1..AC15.4 |

**Exit criterion.** The pack is registered, the E2E artifact exists, and the Guideline is drafted (post-sign-off).

### Coupling map

| | P1 | P2 | P3 | P4 | P5 | P6 |
|---|---|---|---|---|---|---|
| **P1** | — | tight (the receipt writes the carrier) | independent | loose (both consume the cap) | loose | tight (the pack pins the contract) |
| **P2** | tight | — | tight (the carrier feeds the render) | tight (the hook consumes the carrier) | loose | tight |
| **P3** | independent | tight | — | tight (the hook triggers the render) | loose (the filter changes the render's input) | tight |
| **P4** | loose | tight | tight | — | independent | tight |
| **P5** | loose | loose | loose | independent | — | tight |
| **P6** | tight | tight | tight | tight | tight | — |

**Cross-phase coupling risk.** P2→P3→P4 is the critical chain: the carrier, the render, and the hook must agree on the note's identity (a single carrier, one write, one read, one clear). A mismatch shows up as a note that is armed but never rendered, or rendered twice, or a stale note riding a later compaction (see Risks #4 and #5).

---

## 10. Test strategy (minimal, non-vacuous)

### 10.1 House discipline observed

- **No bare `pytest`.** Every invocation is `.venv/bin/python -m pytest` with **explicit** file paths and a bounded file list (POK `testgate-01-explicit-paths`).
- **Mutation packs are standalone scripts**, invoked as `.venv/bin/python test/packs/<file>.py` (or `bash` for shell packs) **only after the tree is clean** (POK `testgate-02-standalone-packs`).
- **A pin living in no REGISTERED pack is not gate-verifiable.** The new pack **must** be registered in `.agents/tester/PACKS.md` in the same lane (task §5; POK `testgate-02`).
- **Never resolve live-shaped paths** in test fixtures — `mktemp` FAKE_HOME + escape guard (critical note: test scripts must never resolve HOME-derived install dirs).
- **Fence the env per-variable.** `env -u POSTGRES_*` is a silent no-op (bash does not glob env names) — scrub one by one, or set explicitly (critical note).

### 10.2 Existing files this lane EXTENDS

| File | What changes | Why |
|------|--------------|-----|
| `tests/unit/tools/test_prompt_reload_tool.py` | The "zero conversation-history writes" pin (docstring `:14`, leg `:456`) is **narrowed in scope**, not deleted | AC13.4 |
| `tests/unit/tools/test_prompt_reload_refusals.py` | 15 tests → 20; the five new codes get one test each | §4 |
| `tests/unit/tools/test_prompt_reload_pins.py` | **NO edit by this lane** (BINDING OQ-4). The count pin (`len(PRIVILEGED_TOOL_CATEGORIES) == 23`) already matches measured reality, and this lane does not change the privileged set (tool name/category unchanged), so no re-derivation is needed. The pre-existing RED `test_the_prompt_reload_entry_is_second_to_last_in_the_literal` is **quarantined by name** (see §10.7) — do NOT fix it (another lane's pin = scope growth). | POK `testgate-01` |
| `tests/unit/tools/test_prompt_reload_refusals.py` | The refusal-code membership/count pin is **re-derived by grep** (never copied). | POK `testgate-01`, task §5 |
| `tests/unit/test_compaction.py` | The merge-failure render leg (branch iii) + the **D25 merge-skip pin (L10)** + the **`previous_overview` suppression pin (L11)** | AC8.3(iii), AC8.4, AC8.5, AC9.1 |
| `.agents/tester/PACKS.md` | The new pack's registration row | §12 L0 |

### 10.3 The NEW packs (A-6 — reconciled count)

**Final number: THREE new packs**, not one. The architect's §I names `prompt_reload_compact_note_unit`, `prompt_reload_compact_note_e2e`, `compaction_note_prefilter_unit`; the plan's earlier "one new pack" understated the surface. Each pack is **REGISTERED in `.agents/tester/PACKS.md` in the same lane** (a pin in an unregistered pack is not gate-verifiable — the house rule).

| Pack | Covers | Legs |
|---|---|---|
| `prompt_reload_compact_note_unit` | The tool's contract surface: param validation; the five refusal codes; the cap boundary; the ARMED receipt + the frozen `armed_pending_seam` token; the content-free INFO line; the still-armed status line; the supersede disclosure; the byte-identity golden (unarmed). | L1, L2, L3, L12 (below) |
| `prompt_reload_compact_note_e2e` | The AR-4 E2E: a REAL conversation + a REAL note + the merge pass FORCED TO FAIL → the agent's **NEXT TURN** context carries the note verbatim in the GLOBAL band. Modeled on the `test_injected_notes_hoisting_acceptance.py` precedent (real StateGraph + AsyncSqliteSaver + real persist seam + fresh-connection reload). **The MERGE-FAILED branch is THE named case of the acceptance artifact** ("Surviving where the overview died is the feature's claim to exist"). | L9 |
| `compaction_note_prefilter_unit` | The dormant pre-filter's default-OFF byte-identity (golden-parity: flag-unset vs flag-on-no-note ⇒ identical doc bytes), the marker truth table (marker-match drops; free-text ⇒ ∅; every failure ⇒ ∅; input-list immutability; never-empty wall; whole-group atomicity), and the kill-switch behavior. | L13 (below) |

**A-6 (MATERIAL for the tester phase):** a dormant capability still needs its **default-OFF byte-identity pinned** and its **marker-truth-table pinned** — a pin in an unregistered pack is not gate-verifiable. The plan previously carried **ZERO pre-filter legs**; they are now added below as **L13**.

**New legs (additive):**
- **L12** — the ARMED receipt + the still-armed status line + the supersede disclosure + the never-"compacted" negative grep. (param validation already L1)
- **L13** — the pre-filter truth table + default-OFF byte-identity + kill-switch. (predicate truth table: marker-match drops; free-text ⇒ ∅; every failure ⇒ ∅; input-list immutability; never-empty wall; whole-group atomicity)

### 10.4 Legs (L0–L11) — each one proves exactly one thing

| Leg | Proves | Assertion (what a test would assert) | File |
|-----|--------|-------------------------------------|------|
| **L0** | The pack is registered | `.agents/tester/PACKS.md` contains a row naming `prompt_reload_compact_note.sh`; the pack runs green from a clean tree | PACKS.md + the pack |
| **L1** | Param validation | The args schema has exactly `force`/`compact`/`compact_note` with the right defaults; `prompt_reload()` returns today's receipt **byte-identical** | `tests/unit/tools/test_prompt_reload_tool.py` |
| **L2** | Cap boundary | **12,000** chars → accepted; 12,001 → REFUSED `prompt_reload_note_over_cap` with cap + length in the message; **no** truncation (the note is absent from the carrier) | new file `tests/unit/tools/test_prompt_reload_compact_note.py` |
| **L3** | Both refusal families | All five codes reachable, each on its exact input, each in the REFUSED envelope, each with zero carrier side effect, each message free of a "compacted" claim | `tests/unit/tools/test_prompt_reload_compact_note.py` + `test_prompt_reload_refusals.py` |
| **L4** | Seam safety | No `compact_state` call during the tool call; the note-armed hook is not registered in T1/T2; the compaction runs **between** turns | `tests/unit/tools/test_prompt_reload_compact_note.py` + `tests/unit/test_compaction.py` |
| **L5** | The note renders VERBATIM | The note's exact bytes appear in the compacted context, in the GLOBAL region, between the GLOBAL and SECTION DETAIL headers | `tests/unit/test_compaction.py` |
| **L6** | The note is independent of the merge pass | `_merge_summaries` forced to `("", False)` → the note is STILL present, verbatim; the merge-failure placeholder is NOT rendered; the surviving sections are still present | `tests/unit/test_compaction.py` |
| **L7** | Content-free logging | A sentinel string planted in the note appears in **zero** log lines; the INFO line carries length + sha256 prefix only; the note is not in any agent-readable table | new file + a log-capture fixture |
| **L8** | The default path is byte-unchanged | `prompt_reload()` receipt byte-identical (golden); a fixed fixture's auto-compacted doc byte-identical (golden); the registry + census unchanged | `tests/unit/tools/test_prompt_reload_tool.py` + `tests/unit/test_compaction.py` |
| **L9** | **AR-4 E2E (the acceptance artifact)** | **D27 binding: L9 asserts the SKIP itself** — the note renders in the GLOBAL region on the merge-failed-by-skip path and the placeholder never co-emits. The **monkeypatch-fail stub survives as the L6 no-note control** (proving the no-note path still runs the merge). The **acceptance demo shows the note on the MERGE-FAILED branch specifically** ("overview dies by skip; note survives where it died"). Asserted against the context the next turn receives — not a stored field, not the checkpoint alone, not a log line | new E2E file (name fixed at implementation: `tests/integration/test_prompt_reload_compact_note_e2e.py`) |
| **L10** | **The D25 merge-skip pin (architect §I, VERBATIM)** | **"≥2 batches + armed note ⇒ no merge LLM call ⇒ per-batch sections survive ⇒ note in the GLOBAL region, no placeholder co-emission."** Asserted against the chunked path (`_summarize_chunked` def `daemon/compaction.py:3922`, sole caller `:3624`, merge call at `:4187-4191`) — the site that fires FIRST on full success and collapses the outcome to `summaries=[merged]`, so the outer sites (`:3687-3691`, `:3768-3772`) never execute. The assertion is **no merge LLM call** (a spy on the merge client), **per-batch sections present**, the **note in the GLOBAL region**, and **no "(overview unavailable …)" placeholder co-emitted**. | `tests/unit/test_compaction.py` (the `compaction_unit_test` pack) |
| **L11** | **The `previous_overview` suppression pin (AC8.5)** | **Two consecutive note-guided compactions** ⇒ the second compacted context contains the **NEW** note exactly once and contains **no** "Previous overview:" line carrying the old note; the no-note path keeps `previous_overview` seeding byte-identically (the auto-path golden pin is unchanged). | `tests/unit/test_compaction.py` (the `compaction_unit_test` pack) |

### 10.5 Non-vacuity (the house's standing demand)

- **L6 is not vacuous:** the mutation leg plants a merge-failure stub that returns `("", False)` and asserts the note **survives**. Removing the note's independence (i.e. letting the merge-failure path drop the note) turns L6 RED. The same stub must be proven to actually trigger the failure branch — a leg asserts the placeholder **would** render without the note (so the stub is proven load-bearing, not a no-op).
- **L9 is not vacuous:** the forced merge failure is asserted to have occurred (the merge's `ok` flag is False in the same run). "The note is in the context" alone would pass even if the merge had succeeded.
- **L1 is not vacuous:** the golden receipt value is captured from the pre-change code, not hand-written.
- **L5 is not vacuous:** the assertion is on the note's exact bytes (including a sentinel the summarizer could not have produced), not on "some summary is present".

### 10.7 The `second_to_last` pin — corrected premise (A-1)

**Correction:** the premise that `test_prompt_reload_pins.py::test_the_prompt_reload_entry_is_second_to_last_in_the_literal` is a pre-existing BASE RED is **FALSE** (A-1). At base `858a85e31` the assertion body measures an `entries[-4]` shape — `entries[-4] == CATEGORY`, `entries[-3] == "prompt-assent"`, `entries[-2] == "guidelines"`, `entries[-1] == "room"` — and is **GREEN** as written (verified: `git show 858a85e31:tests/unit/tools/test_prompt_reload_pins.py` lines 79-104). The privileged set has **23 members** and prompt-reload is **#20 of 23 = 4th from last**, which is exactly what `entries[-4]` asserts. The node is not red now and was not red at base.

**What stays the same (D17 unchanged):** this lane still does NOT edit that file — the privileged set is unchanged by this lane, so no re-derivation is owed, and the count pin (`len(PRIVILEGED_TOOL_CATEGORIES) == 23`, `test_prompt_reload_pins.py:71`) already matches measured reality. **What changes:** only the premise wording. If the node redds during the tester phase for another reason, it is **classified then, on measurement** — not by this corrected premise. No quarantine entry is needed for a green node.

*(The Rev-4 quarantine entry and Risk #14 are superseded by this correction.)*

---

## 11. Kinship doctrine (documentation, not code)

The compact note is **the working-context counterpart of the memoir dream lane**:

> *"the working-context counterpart of the memoir dream lane — the dream consolidates her episodic memory; the compact note consolidates her working memory."*

**Keep the two conceptually separate.** The memoir dream lane (`daemon/services/memoir_reflection_service.py:1` — "Memoir Store v2 — the REFLECTION ENGINE ('the dream')") consolidates **episodic** memory into durable synthesized episodes (`MemoirEpisode` `daemon/repositories/memoir/models.py:194`, `MemoirSynthesis` `:401`, `MemoirJevLedger` `:695`; migrations `20261105_102821_create_memoir_tables.sql`, `20261109_021154_create_memoir_v2_tables.sql`). The compact note consolidates **working** memory into the **immediate** compacted conversation context.

**The kinship is documentary.** No shared code, no shared table, no shared budget, no migration (F5, F7). The memoir guideline is `docs/guidelines/guideline-memoir-reflection-lane.md`; this lane's sibling is `docs/guidelines/guideline-prompt-reload-compact-note.md` (Phase 6 task 6.7).

**The "JEV" name collision — recorded so no future reader conflates them.** Two different things are called "JEV" in this tree:
1. the **Decision Module** (`daemon/services/decision/`, model literal `decision`, env `DECISION_MODEL`) — the Emperor-ratified **P1 shadow-only** overlay (observe + log, never decide);
2. the **JEV Classifier** (`memoir_persona_gate.py`) — the only thing *named* "JEV" in-tree, and it **does** decide on persona candidates.

The `max_llm_calls_per_pass` budget line (`daemon/config.py:4317-4322`) belongs to `MemoirReflectionConfig` — **not** to the decision family. There is **zero** coupling between the two. This lane's pre-filter slot (ES-2) must not silently bind to either without the architect saying which.

---

## 12. Risks and mitigations

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | **Merge-pass coupling** — the note is suppressed when the merge fails (the exact live defect) | **High** | Medium | The note renders on ALL THREE branches (AC8.3), including the merge-failure branch; L6 forces the failure and asserts the note survives; the non-vacuity leg proves the stub is load-bearing. The note's independence from `_merge_summaries` is a **render-layer** property, not a merge-layer one. |
| 2 | **Double compaction at one seam** — both the note-armed hook and the automatic trigger fire at the same boundary | **High** | Medium | The armed note is consumed exactly once (Phase 2 task 2.5, Phase 4 task 4.5); a second compaction with a consumed note is a no-op by construction (the carrier is empty). The auto path is untouched (AC13.2), so the auto trigger cannot see a note. |
| 3 | **The note is consumed twice** — the note lands in two compactions | High | Low | **A-5 (first half):** "clears atomically" is **FALSE** — two stores, a persist-then-delete crash window. The carve-out is **"at most once"** (not "exactly once"): the arm persists across the crash window and re-applies at the next quiescent seam; the rate limit and the deferred receipt make the double-apply bounded and observable. Exactly-once consumption (ES-7) is **at most once** in the crash window. (Phase 4 task 4.5.) |
| 4 | **A STALE note rides a LATER compaction** — the note is armed, the conversation moves on, and the note lands on a compaction the agent did not intend | **High** | Medium | **A-5 (v1.3): the staleness mechanism is RETIRED** (`architecture-recommendation.md` §D.3 — no TTL, no expiry, no staleness state; one arm per instance, replaced-with-disclosure; any future staleness must be an honest typed refusal). The note is scoped to the conversation it was armed in; a mismatched generation produces a **deferred** (honest, not compacted) — never a silent landing of a stale note — and **the arm persists until consumed or replaced**. The exact mechanism is **ES-7 (now ruled: retired)**; the honesty requirement is fixed (AC10.5). |
| 5 | **The note is never consumed** — armed, then lost | High | Low | The armed state is observable (a carrier read); the seam consumes it; a crash between arm and seam leaves it armed (retry lands it) or surfaces it as lost — never silently. ES-7's criteria. |
| 6 | **Mid-turn mutation** — the note-armed compaction fires inside a turn and mutates the live message list | **High** | Low | R7 + AC7.1/7.2/7.3; T1 and T2 are excluded by a pin; the E2E asserts the in-flight turn was unaffected. |
| 7 | **Silent truncation instead of refusal** — an over-cap note is trimmed to fit | **High** | Medium | AC3.3's mutation leg (delete the length check → RED); the cap is checked at **admission**, before anything is written; B1/B4 forbid the note from being condensed or truncated at render time. |
| 8 | **The tool lies** — it says "compacted" when it only armed | **High** | Medium | §5's vocabulary; a frozen token set that contains no "compacted" verb; the forbidden-substring pin; the prior lane's marker-set discipline (D25) applied forward as a separate namespace. |
| 9 | **The note lands in an agent-readable daemon log** | **High** | Medium | R11: length + sha256 prefix only; the sentinel-grep pin (L7); the note's only two homes are the record and the context; the memo/dream and shadow-lane content-free doctrines are the precedent, and the guideline cites them as such (Phase 6 task 6.7). |
| 10 | **The note's budget blows the compaction's ceiling** — a big note crowds out SECTION DETAIL | Medium | Medium | §6's Rules B1–B4: the note is a separate region, never condensed, charged against the 15% doc ceiling. At the **ruled 12,000-char cap** (~3k tokens) it is ~0.43% of a 700k window and ~2.9% of the 15%-ceiling allowance (105k tokens); **disclosed:** on a 32k window a max-cap note could squeeze sections heavily. |
| 11 | **The pre-filter silently drops a message the note does not account for** | **High** | Low (ships OFF) | AR-1's mechanically-decidable law (AC14.5); undecidable → falls through unchanged; the filter ships **disabled** (AC14.1); R-B keeps it a separate contract. |
| 12 | **The pre-filter is mistaken for the Decision Module** — a shadow-only overlay becomes a deciding surface | High | Low | The pre-filter ships OFF and only runs with a note present (AC14.3); the P1 doctrine stays ratified (AC14.7); ES-2/ES-10 keep the naming and the doctrine question explicit. |
| 13 | **The default path regresses** | High | Low | AC13.1/13.2 golden pins; R-C's one-hook limit; the lane's edits to the compaction path are pinned by the byte-unchanged golden. |
| 14 | **The `second_to_last` pin — corrected premise (A-1).** `test_prompt_reload_pins.py::test_the_prompt_reload_entry_is_second_to_last_in_the_literal` is **GREEN at base `858a85e31`** (the assertion body measures an `entries[-4]` shape). The Rev-4 premise ("pre-existing BASE RED") is **false**. The lane still does NOT edit that file (D17 unchanged — the privileged set is unchanged by this lane, so no re-derivation is owed). If the node redds during the tester phase for another reason, classify it then, on measurement. | Low | Low (for this lane) | **Not quarantined (it is not red).** No quarantine entry (§10.7 is superseded). |
| 15 | **The C5 byte-identity promise is read too broadly** — a caller passes `compact=false` + a note and expects today's receipt | Medium | Low (now ruled) | D4 is **CONFIRMED** (leader ruling 2026-11-16) and **concurred** by the architect (§G): the promise covers `compact=false ∧ note empty/absent` only; the other shape is a new input shape with a typed refusal (`prompt_reload_note_requires_compact`). The scoping statement is verbatim in AC6.2. OQ-3 is ANSWERED. |
| 16 | **The two contracts get conflated** — a reader believes the note path can lose messages | Medium | Medium | R-B + R12: the note path is pure-additive; the pre-filter is the only removal surface, separately authorized, default-OFF. The Guideline (Phase 6 task 6.7) states the two contracts in separate sections. |
| 17 | **The engine ruling arrives after implementation starts** — Phase 1 is built against a shape the architect overturns | Medium | Medium | Phase 1 is deliberately engine-independent (contract only); Phases 2–4 are gated on ES-3/ES-4/ES-5. If the ruling overturns a Phase-1 decision, only the receipt vocabulary and the refusal codes might move — the acceptance criteria do not. **CLOSED by the fold:** the architect's ruling (f159b3e3) and the final consumption ruling have landed; the residual risk is now the council's, not the architect's. |
| 18 | **⛔ `force=True` on an armed note below the floor ⇒ 50%-tail truncation (the verbatim tail loses half its messages)** — the NAMED HAZARD | **Critical** | Low (if implemented to the ruling) | The armed path calls the engine with **`force=False`, NEVER `force=True`** (final consumption ruling; ES-5). `force=False` inherits the engine's own floor → the clean skip `skipped_below_min_messages` (`daemon/compaction.py:3368-3370`), and **the arm persists** rather than being silently consumed. AC7.4 carries the mutation leg (flip the armed path to `force=True` → the 50%-tail truncation appears → the leg goes RED on a correct implementation). The floor is `min_messages_before_compaction` (default 10 — `daemon/config.py:1660`; `MIN_MESSAGES_BEFORE_COMPACTION` at `daemon/constants.py:603`). |
| 19 | **The honest-deferral receipt is skipped** — the armed path swallows a dedup/floor deferral and reports success | **High** | Low | The two deferred states are **verbatim receipt strings** — `armed — deferred (anti-refire window), not compacted` and `armed — deferred (below minimum-compaction floor), not compacted` — mandated by the final consumption ruling and carried by AC10.5. **Never a success receipt for work not done** (resident input (2), non-negotiable). |
| 20 | **The 900s rate limit is not enforced at arm time** — a tight deliberate-compact loop starves the agent's own context | Medium | Low | `DELIBERATE_COMPACT_RATE_LIMIT_S = 900` is a **hardcoded module constant, NO knob**, enforced **at arm time** with the typed refusal `prompt_reload_compact_rate_limited` echoing the remaining seconds (AC16.2). The clock is `compact_note_last_applied_at`, counting DELIBERATE compactions only. |

---

## ENGINE SHAPE — RULED (closed; decision of record f159b3e3 + D25 ruling 2933eca78)

*(§13 of this plan. The `## RULING PENDING — ENGINE SHAPE` heading is **GONE** — every slot is filled. The ES-1…ES-11 numbering is **preserved**: no renumbering, no new slot, no removal.)*

**Status: CLOSED.** The architect's decision of record is committed in-branch as **`f159b3e371ffdc7d0c902d2e80d159a9198c7989`** (`.agents/shared/planning/prompt-reload-compact-note/architecture-recommendation.md`), plus the **final consumption ruling** (leader injection, 2026-11-17) which fills the three slots that were held pending the architect's amendment (ES-5/ES-6/ES-7). Each slot below carries its ruling + citation.

**⚠ SUBSTITUTION DISCLOSURE — READ FIRST (the Emperor's note).** The v1 pre-filter is a **DETERMINISTIC ZERO-LLM MARKER MATCHER — NOT THE DECISION ENGINE the Emperor named.** The architect's document now opens with this disclosure and it is mirrored here verbatim: the Emperor's directive authorized *jev* filtering; the ratified doctrine says the overlay never decides; the measured truth is that "**the decision family structurally cannot express 'drop this message.'**" Wiring the pre-filter to it would require (1) removing **EX-2** (a standing Emperor-ratified exclusion), (2) adding a removal-admitting monotonicity direction (the enum has none), and (3) a ratified `ReviewRecord` through the D3 gate — all three the Emperor's, and none smuggled into this release. Even if all three landed, an armed deciding filter whose answer is "dropped" violates the superset rule and **fails open to the heuristic** — silently no-ops. What ships is a **purpose-bound, non-family, default-OFF substitute** with its own model-resolution pattern, own budget, own kill lever, single attempt, and identity-on-every-failure. **The decision family is UNTOUCHED: S1 shadow-only detached, EX-2 intact, D3 gate intact, USD 50/mo ceiling intact.** (Architect §B/§C + the "Pre-filter decision matrix"; the ledger records the disclosure as owed prominently to the Emperor.)

The architect owns these. The Emperor's direction (verbatim, authoritative): *"For this feature you can upgrade, reuse the existing compaction feature of system or write new one (dreamer/distill agent). It take messages and your compact note then produce new context. This module can use decision engine (jev model) to quickly filter messages before feed to dreamer compaction agent."*

**Ari's recommendation (recorded as a recommendation-INPUT to ES-1, NOT as a decision):** upgrade the existing pipeline rather than build a parallel engine — it already owns the summarizer, the dedicated model, the ceiling rules, the sentinel mechanics, the cost plumbing, and the section structure the note must compose with; the "dreamer" becomes a **note-guided MODE** of that pipeline, not a second engine. Add (a) the note-injection layer; (b) an **optional** jev pre-filter stage (cheap triage before the expensive summarizer), with its own budget + kill switch, degrading gracefully (unavailable/over-budget → skip the filter, compaction proceeds normally — never a hard dependency); (c) an explicit seam-safety proof that the pre-filter can never drop a message the note references as settled.

---

### ES-1 — Engine shape: reuse/upgrade the existing pipeline, or a new dreamer/distill module?

> **✅ RULED — (C) HYBRID.** Architect `architecture-recommendation.md` §A "Engine shape — RULING: **(C) Hybrid**" (2026-11-17).
>
> **The ruling:** the note is **engine data** — one additive field on `CompactionContext` (dataclass `daemon/compaction.py:1276`; the additive-optional-field precedent `:1295-1318`), consumed by `compaction.py` at the existing render site. The pre-filter is a **separate, guarded, default-OFF stage** in its own module, lazily imported (cycle-safe). **Ari's recommendation is ~90% adopted**; the narrow overrule is cited in §A (the pre-filter is placed in a separate module because `daemon/compaction.py` is a measured god module — 5,206 lines — and a hard import cycle exists: `daemon/services/__init__.py:17` → `instance_lifecycle.py:59` → `from ..compaction import ContextCompactor`). **(B) new dreamer engine is REFUSED** (both council models, independently, identical grounds: a second engine forks the doc format, persist recipes, ceiling rule `:788`, sentinel `:317`, pre-write guard `:480-495`, and the 60s dedup `:5091`).
>
> **Council scores:** C 4.00–4.25 vs A 3.15–3.70 vs B 1.75–2.00. **Files that change (architect's complete inventory):** `daemon/compaction.py` (CompactionContext +1 field; one render point; one conditional in the ceiling numerator; merge-skip gate), `daemon/services/instance_messaging.py` (T3 gate: armed-check short-circuit), `daemon/tools/prompt_reload.py` (widen `PromptReloadInput` at `:225` — a LOCAL class, cheap), `daemon/config.py` (2 new `CompactionConfig` fields), NEW `daemon/_compact_note_prefilter.py` (the stage, lazily imported), NEW tests/packs. **Coupling: 1 dataclass field + 2–3 call sites.**
>
> **Flip condition (both council models):** if the note must later alter *selection semantics* (not just the GLOBAL layer + optional drop set), A and C converge — extract the note-guidance fully into the new module.

**The criteria the ruling satisfied:**

**Question.** Which engine produces the compacted context when a note is present?

**The answer must satisfy:**
1. The **default** compaction path stays byte-unchanged when no note is armed (R-C, AC13.2).
2. The note renders on **all three** branches of the GLOBAL three-way branch (`daemon/compaction.py:998-1032`) — the engine choice must not make the merge-failure branch the only branch that carries the note.
3. The engine change is **additive**: nothing that survives today is removed (C4 as reworded, AC12.1/12.2).
4. The engine must support a **NOTE-ONLY mode**: the authored note is the authoritative layer and the fragile machine merge is **not required** for it to exist (C2). An engine that still requires a successful merge to emit the note fails this criterion.
5. The engine must not introduce a **second** summarizer/model/budget lineage that bypasses the existing ceiling rules, sentinel mechanics, or cost plumbing — **or**, if it does, it must explicitly own the same guarantees (budget ceiling, kill switch, content-free logs, silent-loss pre-write guard).
6. If a NEW module is ruled: it must declare its **seam-safety proof** (never mid-turn) and its **budget/kill-switch ownership**, and it must not write the same compaction record the existing engine writes (one writer per record).
7. If the EXISTING pipeline is upgraded: the upgrade must be a **mode**, not a fork — the note-armed mode must not duplicate the existing pipeline's logic in a second place.
8. **Content-free logging** (R11) holds for whichever engine is chosen.
9. **BINDING 1 — the note cap was the architect's to set; IT IS NOW RULED: 12,000 characters** (§G). The plan's budgeting approach (a separate never-condensed doc region charged against `COMPACTION_DOC_CEILING_FRACTION = 0.15`, NOT folded into `GLOBAL_OVERVIEW_TOKEN_CAP = 600`) was **ADOPTED** (see §6.3). The engine must honor the ruled cap with an **admission-time check before any write**, so an over-cap note is refused, never truncated; the cap is co-pinned in tests against `GLOBAL_OVERVIEW_TOKEN_CAP` (drift guard).

**It must NOT break:** AC1.2, AC13.1, AC13.2, AC12.1, AC12.2, R7 (seam safety), R11 (content-free).

**Recommendation-input (not a ruling):** Ari's — see the paragraph above ES-1.

---

### ES-2 — Pre-filter shape + its seam-safety invariant

> **✅ RULED.** Architect `architecture-recommendation.md` §E "The seam-safety invariant — RULING" + §F "Pre-filter failure surface" + the "Pre-filter decision matrix" (2026-11-17).
>
> **The ruling — the invariant, one sentence:** *"A pre-filter may drop a candidate group only when the note's structured marker block mechanically accounts for that group (exact message-id or group-index-span match, decidable without judgment at filter time); every other group falls through to today's path unchanged, and an absent or unparseable marker block yields an empty drop-set — free-text notes make the filter vacuous by construction."*
>
> **Reading adopted:** "Reading 2 is the operative rule; Reading 1 supplies only the semantic justification, expressed through the marker scheme." R1 alone ("the note said so ⇒ safe to drop") bets conversation integrity on two fallible layers compounding — rejected; R2 alone ("load-bearing ⇒ never drop") forbids the authorized capability.
>
> **Two hard walls ride with it:** (1) the **never-empty wall** — a drop-set that would empty a non-empty candidate set is discarded → identity (local import of `registry.py:629-630`'s rule); (2) drops operate on **whole `MessageGroup`s only** (atomic tool sequences never split; the span math downstream is group-position-derived). Dropped groups are itemized in the envelope's existing dropped-spans clause (`:977-978`) — never silently vanished.
>
> **Marker block format (v1):** an optional final fenced block of the note — `<<<COMPACT-ACCOUNT` / `accounted_for: msg:<message_id>, group:<start_idx>-<end_idx>, ...` / `>>>`. Machine-parsed; the matcher intersects the declared set with the actual candidate groups (ids/spans re-derived at filter time); anything unparseable ⇒ drop-set ∅.
>
> **v1 occupant: a DETERMINISTIC ZERO-LLM marker matcher**, default OFF. The failure surface (§F): ONE pass, no retry; every failure — disabled / timeout / transport / over-budget / unparseable marker block / out-of-set references / empty-output-on-non-empty-input — **converges to identity**, and identity is the **same code path** as "off". WARN once per pass. The v1 occupant's wall-clock is effectively zero; the **stage contract** for the future LLM occupant is `min(5s, 10% of remaining operation budget)` (council D3) — specified in the doc only, not added as config.
>
> **Byte-identity guarantee (structural):** the filter's only input is the compactable partition output of `select_compactable_groups` (`:1582`); the verbatim tail, hoisted injections, sentinel, and ceiling are outside its reach. The default (no-note) path is unchanged — gate = `enabled AND note_present`; no note ⇒ short-circuit before any list copy, LLM call, or arm-state read. Proven by golden-envelope byte-parity (flag-unset vs flag-on-no-note ⇒ identical doc bytes).

**Question.** What exactly is the jev pre-filter, and what mechanically decides that a message is "accounted for"?

**The answer must satisfy:**
1. **AR-1, verbatim:** "pre-filter may drop a message only when the note demonstrably ACCOUNTS FOR IT; anything else falls through UNCHANGED."
2. **AR-1's corollary:** "accounted for" is **mechanically decidable** at filter time — never a judgment call; if it is not decidable, the message falls through unchanged.
3. **The free-text corollary:** with a free-text-only note, nothing is decidable ⇒ the filter is **VACUOUS**. The answer must therefore either (a) specify the **marker scheme** that makes accounting decidable, or (b) state plainly that the capability is **no-op-until-armed**. A free-text note + a non-vacuous filter is a contradiction and must not be ruled.
4. The filter never drops a message the note references as **settled**, and never drops a message it cannot mechanically tie to a note marker.
5. The **verbatim tail** is preserved regardless of the filter's decision (`select_compactable_groups` `daemon/compaction.py:1582` → re-attach `:3722-3732`): the filter operates on the compactable set, never on the preserved tail.
6. Degradation is always **skip-the-filter**, never **fail-the-compaction** (AC14.4).
7. The filter's decisions are logged **content-free** (message ids + hashes only), so the seam-safety claim is **auditable** after the fact (R11).
8. The filter is **default-OFF** and ships **disabled** (AC14.1, R-A).

**It must NOT break:** AC12.1 (the note path removes nothing), AC14.2 (OFF ⇒ byte-identical), the P1 shadow-only doctrine (AC14.7).

---

### ES-3 — Carrier for the armed note

> **✅ RULED.** Architect `architecture-recommendation.md` §D "Note placement + composition — RULING" (2026-11-17).
>
> **The ruling — carrier (arming store): `instances.metadata` JSONB** via `InstanceRepository.set_metadata` / `delete_metadata` (`daemon/repositories/instance/repository.py:2990+` — "dialect-aware single-statement UPDATE so concurrent calls targeting different keys compose correctly"). Keys: `compact_note_arm` (the verbatim note), `compact_note_armed_at`, `compact_note_sha256_12`. This is the exact precedent of `daemon/services/_escalation_metadata.py:73-84` (four compaction-scoped keys). **Not** `SessionState` (the arming write originates in a tool mid-turn; T3 consumes pre-dispatch outside the graph node), **not** an in-memory registry (`prompt_reload`'s pop is restart-unsafe — W1's mirror-warning).
>
> **Migration verdict: NO.** "The record is the checkpoint `messages` channel (§0.4); the doc body is a plain string (`SystemMessage(content=body)`, `daemon/compaction.py:1053-1056`) carried as opaque JSONB/BYTEA. A note riding the doc body is string concatenation inside `build_compaction_doc`. The arming carrier is existing JSONB. **Zero DDL, zero schema evolution, in either place.**"
>
> **The record IS the checkpoint `messages` channel** — the `compaction-global-{iid}-{seq}` SystemMessage persisted into the LangGraph checkpoint (there is no compaction table; the only `.sql` mentioning compaction is a comment, `daemon/migrations/versions/20260825_000001_create_message_metadata.sql:24`).

**Question.** Where does the armed note live between the tool call and the seam? Options on the table: the tool call itself, a `CompactionContext` field (`daemon/compaction.py:1276` — all fields defaulted, so a new `agent_note: str | None = None` is a pure code change), a `SessionState` channel (`daemon/graph.py:4093` — the checkpoint state is ONE JSONB document; the additive-with-default idiom is used 22× and the free-text precedent `attestation_last_bare_ai_content` sits at `:4253`, so **no DDL** is needed), or `instances.metadata` JSONB (`daemon/repositories/…`; precedent `daemon/services/_escalation_metadata.py:73-84`; note the generic update **rejects** `instance_metadata` by design — `set_metadata`/`delete_metadata` must be used).

**The answer must satisfy:**
1. The carrier **survives pause → quiesce → resume** (the compaction's own lifecycle; the blueprint's pause-first-then-quiesce rule).
2. **No DDL, no migration** (F7). If a carrier that requires a table is ruled, the ruling is **recorded in this section first** (and F7 is amended in writing).
3. **No cross-instance leakage** — the note is scoped to the instance that authored it; a test proves a second instance never sees it.
4. The carrier supports the **exactly-once** consumption semantics of ES-7 (arm → land → clear, atomically).
5. The carrier distinguishes an **armed** note from a **landed** one (so R5's "second note while armed" rule and the staleness rule in Risk #4 are decidable).
6. The carrier's **content** never lands in an agent-readable log (R11); only length + digest are logged.
7. The carrier's write path does **not** violate the existing "zero conversation-history writes" pin without the AC13.4 scope narrowing being applied in the same change.
8. If the tool call itself is ruled as the carrier, the answer must show how the note survives the tool call returning (the tool's return is the only thing the agent sees; a per-call value cannot survive to the seam unless the seam is in the same call).

---

### ES-4 — Render slot vs assembly slot (and the in-context outcome marker)

> **✅ RULED — v1.2 (D25 sharpening).** Architect `architecture-recommendation.md` §D "Note placement + composition" (v1.2) + §D.1 "D25 RULING" + §H (v1.1) + §I — committed `f159b3e37` and amended `2933eca78` (2026-11-17).
>
> **The ruling — render slot: the note is the FIRST ARM of the GLOBAL OVERVIEW branch (`daemon/compaction.py:993-1032` region; the `── GLOBAL OVERVIEW ──` header opens at `:994`).** The branch structure:
> - **note-first gate (new, FIRST):** `if note: body += note + "\n"` — **the note REPLACES the three-way chain when a note is consumed.** Same region, verbatim, **NOT subject to `GLOBAL_OVERVIEW_TOKEN_CAP` (600)** render-time truncation (`:1012-1021`); the **only** cap enforcement is the arm-time typed refusal (§G).
> - `if global_overview:` (`:998`) → cap check + merged text — **auto path only; unreachable when a note is consumed** (the merge is skipped ⇒ `global_overview` is `None`).
> - `elif mode == "truncation":` (`:1024-1029`) → nothing — **auto path only**.
> - `else:` (`:1030-1032`) → the placeholder — **auto path only; SUPPRESSED when a note is consumed** (co-emitting `"(overview unavailable — merge pass failed…)"` under an authoritative note would be **double text and a lie** — "unavailable" when the note IS the overview).
> - `if n_sections > 0:` (`:1034`) → SECTION DETAIL — unchanged on both paths.
>
> **⛔ The v1.1 "additive band at `:1033`" language is SUPERSEDED.** D25 exposed that a purely additive band would **co-render with the placeholder/merged text** once the merge is skipped. The invariant that matters is preserved and sharpened: **the note renders in the GLOBAL OVERVIEW region on every doc-producing path (full / partial / truncation-fallback) whenever a note is consumed, the auto arms are byte-identical when it is not, and the note never co-exists with another GLOBAL text.**
>
> **The merge-skip's single home — the `_merge_summaries` ENTRY GUARD (def `daemon/compaction.py:4306`), first statement** (architect §D.1, folded verbatim in `decisions.md` D29):
> ```python
> async def _merge_summaries(self, ...):
>     if context.compact_note:
>         logger.info("merge skipped: note-guided mode (iid=%s)", iid8)  # content-free
>         return (None, False)
>     ...
> ```
> An entry guard covers merge sites 1/2/3 **and every future call site by construction** — the site-enumeration drift class D25 caught is structurally impossible against it. `(None, True)` would be **WRONG** (at `:4187` it would produce `summaries=[None]`); `(None, False)` rides the existing merge-failed contract at every site.
>
> **One consequential predicate rides with the gate:** the `if previous_overview:` line at `:996-997` ("Previous overview: …") is **SUPPRESSED when a note is consumed** — after a note-guided compaction the prior doc's GLOBAL *is* the note, so `_extract_previous_overview` (`:222`) would otherwise re-emit the OLD note verbatim (**uncapped** — that line precedes the cap check) directly above the new note: unbounded cross-pass duplication. The note REPLACES the overview lineage for that conversation; it is authoritative and self-contained. **The no-note path keeps `previous_overview` seeding byte-identically.**
>
> **The in-context outcome marker — RULED (architect §H, signal 1):** the compacted doc's **envelope header** carries `note_sha8=<prefix> note_chars=<n>` + `note_guided=true` (an additive envelope field; FE reads defensively). "Provenance rides the *envelope*, not the GLOBAL slot, so the slot stays byte-equal to the note and the **verbatim contract holds exactly**."
>
> **The tool receipt — RULED (architect §H):** `outcome=armed_pending_seam`, `note_chars`, `cap`, `note_sha256[:12]`, `superseded_previous?`, kill-switch state, expected seam ("your next turn boundary — the quiescent pre-dispatch gate"), and the explicit line: **"CONTEXT NOT YET COMPACTED — the compaction happens at your next turn boundary; until then this note remains armed."** A still-armed status line rides every subsequent `prompt_reload` receipt.

**Question.** Does the note render (a) by filling the `global_overview` parameter of `build_compaction_doc` (`daemon/compaction.py:874`), (b) as a **sibling** parameter with its own render branch, or (c) at the **assembly** seam (`build_sentinel_replacement`, `daemon/compaction.py:317`, final return `:499-503`)?

**The answer must satisfy:**
1. The note renders **where the GLOBAL OVERVIEW sits today** — this is a hard constraint from C2 ("rendered WHERE the auto 'GLOBAL OVERVIEW' sits today"). A ruling that places the note outside the doc (e.g. as a separate injected `SystemMessage`) must justify the deviation against the commission's wording.
2. The note renders on **all three** branches (AC8.3), including `mode == "truncation"` and the merge-failure branch.
3. **RULED (v1.2):** the merge-failure path is not reached on the armed path at all — the merge is skipped by the `_merge_summaries` entry guard (def `:4306`), so `global_overview` is `None` (`:3697` / `:3778`) and the **note-first gate** supplies the GLOBAL region. The sections built before the merge (`:3749-3752`) and the per-batch sections survive unchanged. **The v1.1 "change the merge-failure path to fall back to the note" shape is superseded**: the note no longer waits for the merge to fail, it replaces the chain.
4. If (c): the note must still be part of the compacted **context** the agent reads, and must **not** be a free-floating injected message outside the doc (the doc is what the agent reads as its compacted context).
5. The note is **never condensed** and **never truncated** by the ceiling rule (B2).
6. The in-context **outcome marker** (R10 AC10.4) is content-free (length + digest prefix only), lives inside the compacted context, and is distinguishable by the agent from a machine-merged overview.
7. The assembly seam's **silent-loss pre-write guard** (`daemon/compaction.py:481-497`, raising `CompactionAborted` at `:177`) must still cover the note's presence — i.e. the note's injection is part of the replacement set the guard validates.

---

### ES-5 — Where the note-armed forced turn-boundary compaction hooks

> **✅ RULED (final consumption ruling, leader injection 2026-11-17).** Fills the slot that was held pending the architect's amendment.
>
> **The ruling:**
> 1. **An armed note FORCES a note-guided compaction at the next quiescent seam (T3/T4).** The **80% threshold is BYPASSED** — "the gate measures SIZE, the note measures RELEVANCE (the Emperor's use case is true at 30% fill as at 90%)".
> 2. **The forcing lives in the T3 GATE as a THRESHOLD BYPASS; the engine is called with `force=False`, NEVER `force=True`.**
> 3. **NAMED HAZARD (verbatim class):** *"calling the engine with force=True on an armed note below the floor triggers 50%-tail truncation — FORBIDDEN."* The mechanism is load-bearing, not stylistic: `force=True` below the minimum floor does **not** skip — it falls through to `_last_effort_tail_truncation` (`daemon/compaction.py:3357-3386`), a 50%-tail truncation that **drops half the verbatim tail**. `force=False` inherits the engine's own floor: `skipped_below_min_messages` (`:3368-3370`) — a clean skip, and **the arm persists**.
> 4. **No-arm path byte-identical by construction** — the arm predicate short-circuits false when no arm exists; the default path is untouched.

**Question (now closed).** Does the note-armed hook take the **T3** shape (`_maybe_compact_context`, `daemon/services/instance_messaging.py:1337`, call `:1535`) or the **T4** shape (`execute_compact`, `daemon/services/compact_executor.py:555`, call `:1220`)? — **Answer: the T3 gate, as a threshold bypass**; T4 (`/compact`) also consumes an armed note (architect §D/§H).

**The answer must satisfy (and how it was satisfied):**
1. **R-C's one-hook limit:** exactly ONE hook is added to the compaction path. The auto path (T1/T2/T3/T4 as they exist) is otherwise untouched.
2. **Turn boundary only** — the hook is NOT wired into T1 (`daemon/graph.py:8138`) or T2 (the CLE reactive handler, `daemon/graph.py:9890`) (AC7.3).
3. The hook is **reachable from the calling agent's own turn end**, and the calling agent's in-flight turn is unaffected (AC7.1/7.2).
4. The hook honors the compaction's existing **guards** (min-messages floor, ceiling rules) unless the ruling says the note-armed path is exempt — and if exempt, the ruling says so explicitly (the executor's force semantics already document which checks still apply under force: `daemon/services/compact_executor.py:13-16`, `:1197-1203`).
5. The hook does not create a **second** compaction record writer for the same conversation (one writer per record).
6. If the T4 shape is ruled, the pause→quiesce→resume semantics are the blueprint's canonical shape and the ruling must say how the armed note survives them (feeding ES-3).
7. The hook's **failure** never corrupts the conversation: a failed note-armed compaction leaves the conversation intact and the armed note in a state the agent can be told about (honest deferral, not a silent loss).

---

### ES-6 — Dedup interaction (the 60s anti-thrash window) vs the note-armed force

> **✅ RULED (final consumption ruling, leader injection 2026-11-17).** Fills the slot that was held pending the architect's amendment.
>
> **The ruling:**
> 1. **Rate limit:** `DELIBERATE_COMPACT_RATE_LIMIT_S = 900` (15 min) — a **HARDCODED module constant, NO KNOB**. Enforced **at ARM TIME** as the typed refusal `prompt_reload_compact_rate_limited`, **echoing the remaining seconds** (honest "not now, here's when").
> 2. **The clock** is `compact_note_last_applied_at`, counting **DELIBERATE** compactions only.
> 3. **Dedup:** with `force=False` the **60s window CAN bind** (worst case one seam-cycle, and only after a just-fired automatic compaction) — **ACCEPTED**, and the honest receipt is load-bearing.
> 4. **BOTH deferred states are recorded VERBATIM as receipt strings:** `armed — deferred (anti-refire window), not compacted` and `armed — deferred (below minimum-compaction floor), not compacted`. **NEVER a success receipt for work not done.**
>
> *(The architect's §H annex-2b finding — that `force=True` bypasses the 60s dedup at `daemon/compaction.py:3120-3132` — is superseded on the armed path by the ruling above: the armed path calls the engine with `force=False`, so the window can bind. The architect's normative "the receipt never needs a 'deferred by dedup' state" does **not** survive the final ruling, which is exactly why the two deferred receipt strings are now mandated.)*

**Question (now closed).** Does the dedup window (`_is_recently_compacted`, `daemon/compaction.py:5091`, read at `:3126-3132`; `dedup_window_s` default 60, `daemon/config.py:1738-1753`) bind the **deliberate**, note-armed compaction? — **Answer: YES, it can bind (worst case one seam-cycle); the anti-abuse mechanism is a hardcoded 900s arm-time rate limit with a typed refusal, and the deferral is disclosed in the receipt.**

**Context (resident input (2), for the architect to rule on).** A note-armed deliberate compact is a **different trigger class** from the threshold-triggered automatic compaction. The 60s window exists for the AUTOMATIC class and arguably should not swallow the deliberate one. "Compacted now" is the contract when the agent asks. If anti-abuse is needed, an **honest typed refusal** (rate-limited — e.g. one deliberate compact per N minutes) is preferred over a silent deferral, because a disclosed swallow is still a swallow.

**The answer must satisfy:**
1. **NON-NEGOTIABLE under either branch:** if the window binds, the receipt MUST say **"deferred, not compacted"** — NEVER a success receipt for work not done (AC10.5).
2. The ruling must state which trigger class the window governs, in words.
3. If the window does not bind the note-armed path, the ruling must say how the note-armed path avoids double-compaction at the same seam (Risk #2) — the consumption rule (ES-7) is the mechanism.
4. If the window does bind it, the ruling must specify the **typed refusal/deferral** code and the agent-facing wording.
5. No branch may leave the agent believing a compaction happened when it did not (R10).
6. **BINDING 2.2 — the anti-refire preference is now a CRITERION, not advisory:** a note-armed deliberate compact is a **DIFFERENT TRIGGER CLASS** from threshold-triggered automatic compaction; the 60s window exists for the AUTOMATIC class and should not swallow the deliberate one.
7. **BINDING 2.2 — NON-NEGOTIABLE FLOOR either way:** if the window binds, the receipt MUST say **"deferred, not compacted"** — NEVER a success receipt for work not done. (Already item 1; restated here so the floor cannot be dropped.)
8. **BINDING 2.2 — rate-limit alternative:** if the architect adopts a rate limit instead of the window, prefer a **HARDCODED MODULE CONSTANT** over a new config knob — this commission allows **NO new knobs beyond the note cap** (BINDING 1 / F4).

---

### ES-7 — Note-consumption atomicity (exactly-once across crash/retry)

> **✅ RULED (architect §D, now final — the leader's final consumption ruling closes the slot).**
>
> **The ruling (C-1 consumption predicate, architect v1.3):** the arm is consumed by the **first quiescent doc-producing compaction** — the C-1 predicate: `compaction_type ∈ doc-producing`, **excluding** `{emergency_truncation, tail_truncation_last_effort}`, **including** `_truncate_fallback`'s outcome (`daemon/compaction.py:5035`). **On exclusion (doc-less outcome) the arm persists with an honest "still armed" row** (the third deferred state in AC10.5). T3 expected; T4 (`/compact`, also quiescent) also consumes. **Mid-turn sites (T1/T2) NEVER read the arm** — they stay byte-identical by construction, honoring the commission's "NEVER mid-turn" for the tool-initiated path.
> - **The arm clears ONLY when `result.replacement_messages` is non-empty AND `persist_compaction_result` returns `True`** — "the bool alone cannot distinguish a full write from the stamp-only anti-refire write (measured: the seam returns `True` for both, `daemon/services/_compaction_persist_seam.py:72-107`), and the stamp-only path must NOT consume the arm."
> - **Emergency** (`compaction_type="emergency_truncation"`, no doc) **does not consume** the arm — and the note does not render there (the arm PERSISTS and the status surface discloses "still armed").
> - **Crash-window** (engine success, delete not yet run): the note **re-applies at the next quiescent seam** — "bounded and benign (the tail invariant still holds for the *new* compaction)".
> - **The below-floor skip does not consume either** — `skipped_below_min_messages` (`:3368-3370`) is a clean skip and **the arm persists** (final consumption ruling, ES-5).

**Question (now closed).** What are the note's consumption semantics between arm and seam? — **Answer: consumed by the first quiescent doc-writing compaction, cleared only on a real (non-stamp-only) write; mid-turn sites never read it; emergency and below-floor skips do not consume it; the crash window re-applies it at the next quiescent seam.**

**The answer must satisfy:**
1. **Exactly-once:** the note lands in the compacted context **at least once and at most once** (Risk #3).
2. **Atomic clear:** the armed slot is cleared **atomically with** the compaction write — a crash between them must not leave a note that can land twice (Phase 4 task 4.5).
3. **Crash between arm and seam:** the note is either still armed (a retry lands it) or is **explicitly surfaced as lost** — never silently dropped (Risk #5). The house precedent for a revived instance's re-binding (the `round_count` discipline: re-BOUND on revival, NEVER reset) is the shape to copy.
4. **Staleness: RETIRED (architect v1.3 §D.3).** There is **NO staleness mechanism in v1** — no TTL, no expiry, no staleness state. An armed note persists until consumed or replaced; a second note REPLACES the first with disclosure, so there is no accumulation to bound; the agent always knows an arm is parked (the still-armed status line + any deferred state ride every subsequent `prompt_reload` receipt); the rate limit bounds *arming frequency*, the actual abuse surface. If the Emperor later wants bounded staleness, it MUST be an **honest typed refusal/notice at arm-read time** — never a silent expiry. **Risk #4's mitigation is updated accordingly:** a mismatched generation produces a **deferred** (never a silent landing), and the arm persists until consumed or replaced.
5. The agent-visible state vocabulary is exactly: `armed` → (`landed` | `deferred` | `refused`). No other state is ever reported to the agent.
6. The consumption semantics must not require a **new table** unless the ruling records it here first (ES-3, F7).

---

### ES-8 — Pre-filter default state, kill lever, and budget

> **✅ RULED.** Architect `architecture-recommendation.md` §C "Governance tension — RULING" + SCOPE item 5 + the "Pre-filter decision matrix" + the "Default-state table" (2026-11-17).
>
> **The ruling — the pre-filter is OUTSIDE the P1 doctrine; not a seam; a purpose-bound non-family stage; default OFF.**
> - The measured reason: "**the decision family structurally cannot express 'drop this message.'**" Wiring the pre-filter to it would require (1) removing **EX-2** — a standing Emperor-ratified exclusion (`registry.py:82-90`, registration refused at `:275-280`), (2) adding a removal-admitting monotonicity direction — the `Monotonicity` enum (`types.py:92-97`) admits only `TOWARD_COMPLETION / TOWARD_TIGHTENING / WITHIN_SET / ADVISORY_ONLY / NONE`, and (3) a ratified `ReviewRecord` through the D3 gate (`transition_stage` `:355-387`; `is_deciding_permitted` `:389-399`). "All three are the Emperor's, and Ari's containment clause (e) forbids smuggling any of them into this release. Even if all three landed, an armed deciding filter whose answer 'dropped' violates the superset rule **fails open to the heuristic** — i.e. silently no-ops. The capability the Emperor asked for is *unreachable through the decision family as built.*"
> - **The mechanism:** "a **distinct, purpose-bound mechanism inside the compaction pipeline** — never registered on the decision registry, never called through `DecisionOverlayPort`, never billed to the decision ledger. It reuses the *pattern* (own model resolution à la `resolve_compaction_model`, own budget, own kill lever, single attempt, identity on every failure), not the family." **SUBSTITUTION DISCLOSURE is owed prominently to the Emperor** (architect's own words; the decision-family route was refused, so the shipped capability is a purpose-bound substitute — the plan's R-A two-posture framing stands).
> - **Kill lever:** one `CompactionConfig` bool — the `proactive_enabled` precedent at `daemon/config.py:1686` — aliased `AliasChoices` field, `extra="forbid"`-safe per the `dedup_window_s` idiom at `:1738-1753`, **code-default OFF, env-overridable, and deliberately NOT set in `config.yaml`** (a yaml value would silently defeat an operator's env kill-switch — pydantic-settings init-kwargs priority).
> - **Budget:** the v1 occupant is **deterministic (zero LLM)**, so there is no LLM budget to enforce; the **stage contract** for the future LLM occupant is `min(5s, 10% of remaining operation budget)` (council D3) — "specified in this document only — not added as config now" (SCOPE item 5). No new budget knob.
> - **P1 SHADOW-ONLY doctrine stays ratified, untouched.** EX-2 stays. The enum stays. The D3 gate stays. The open question (may the overlay ever be a deciding surface?) is **recorded in §11, not decided here**.
> - **Knob budget (SCOPE item 5):** "**No new knobs beyond two** — `compact_note_max_chars` (default 12000) and `compact_note_prefilter_enabled` (default **OFF**, env-only override, deliberately absent from `config.yaml`)." The plan's F4 carve-out is therefore exercised, not violated (see §6.3 Rule B7).

**Question.** What are the pre-filter's default state, kill lever, and budget?

**The answer must satisfy (AR-2, verbatim):**
1. **(a) default OFF.**
2. **(b)** the filter OFF ⇒ byte-identical, **including the note path** (AC14.2).
3. **(c)** the filter runs **only** when a note is present (AC14.3).
4. **(d)** the filter has a **kill lever** and a **budget**; degradation is ALWAYS skip-the-filter, NEVER fail-the-compaction (AC14.4).
5. **(e)** the filter **ships DISABLED**, and the P1 shadow-only doctrine **STAYS RATIFIED** — a doctrine change is a SEPARATE Emperor ruling, not smuggled into this release (AC14.7).

**Plus:**
6. The kill lever is read **per use**, not at construction time (the house precedent: the prior lane's D11 — refusal-at-call, tool stays registered under any switch state).
7. The budget has a hard ceiling and an over-budget behavior of **skip** (not fail).
8. If the ruling adds config knobs, they are **new `CompactionConfig` fields** (the `extra="forbid"` typo guard refuses unknown YAML keys at boot — `daemon/config.py:5614`, `:5634`) and they follow the house resolver pattern so an operator's env override is not silently defeated by a YAML value (B6).

---

### ES-9 — AR-3's owed clause-4 rewording, and the C5 byte-identity scope

> **✅ RULED (both halves).** Architect `architecture-recommendation.md` §"Commission clause 4 — the owed rewording (architect's draft)" + §G (2026-11-17). The leader's D4 confirmation (2026-11-16) stands and is **concurred** by the architect's §G.

**AR-3's owed rewording — the architect's draft, verbatim (ADOPT):**

> **NO NEW DATA-LOSS SURFACE ON THE NOTE PATH:** on every doc-producing compaction, everything the engine already preserves — the verbatim tail, the hoisted injected notes, the envelope, the section summaries, the sentinel pre-write guard — is preserved unchanged; the note ADDS an authoritative layer and removes nothing that survives today. The pre-filter is the ONE separately-authorized exception: it may remove a candidate group only when the note's structured marker block mechanically accounts for that group (the seam-safety invariant), and only when the operator has armed it (default OFF).

**The C5 byte-identity scope:** the architect's §G fixes it in the same direction as the leader's ruling — code `prompt_reload_note_requires_compact` for "note supplied with `compact=false`" ("REFUSED — **ignoring is dishonest**"), and "`compact=false` with no note = today's behavior, byte-identical." OQ-3 is ANSWERED (leader ruling 2026-11-16; the verbatim scope statement is in AC6.2).

---

### ES-10 — AR-5's open doctrine question

> **✅ FILLED — RECORDED, NOT DECIDED (Emperor-owned).** The architect's decision of record (f159b3e3) §"Open questions" §1 closes the *engineering* question and records the *doctrine* question for the Emperor: *"The Emperor's doctrine question: may the decision-engine overlay ever be a DECIDING surface (a P1 doctrine amendment)? This lane's design is **inert to the answer**: the pre-filter never touches the family. Surfaces on the Emperor's next waking, alongside EX-2 and the absence of a removal-admitting monotonicity direction."*

**AR-5 (recorded, not decided).** *May the decision-engine overlay ever be a **deciding** surface — i.e. may the "P1 is SHADOW-ONLY" doctrine be amended?* The slot is filled with the decision of record: **the doctrine stays ratified and untouched** (EX-2 stays; the `Monotonicity` enum stays; the D3 gate stays), and the lane's design is **inert to the answer** — the pre-filter never touches the decision family. The Emperor's question itself surfaces on his next waking; this lane neither decides it nor is blocked by it.

**Note for the record.** The Emperor's direction for this feature says the module "can use decision engine (jev model) to quickly filter messages before feed to dreamer compaction agent" — a filtering use, not a deciding use. The AR-1 invariant (a message is dropped **only** when the note demonstrably accounts for it) plus the AR-2 containment law keep this lane's default-OFF filter **compatible with** the ratified doctrine: the filter is not the decision family, it is a note-bound pre-filter, and it decides nothing until it is explicitly armed. The ruling at ES-2/ES-8 must preserve that distinction — and if a future ruling makes the overlay a deciding surface, it is a **doctrine amendment** requiring the Emperor, not a lane decision.

---

### ES-11 — Summary of slots

**Status after the fold (2026-11-17): 11 / 11 FILLED.** The architect's decision of record is committed in-branch as **`f159b3e371ffdc7d0c902d2e80d159a9198c7989`**; the three consumption-timing slots (ES-5/ES-6/ES-7) were filled by the **final consumption ruling** (leader injection, 2026-11-17). **No slot was renumbered, added, or removed.**

| Slot | Question | Status | One-line ruling |
|-------|----------|--------|-----------------|
| ES-1 | Engine shape | **✅ RULED** | **(C) Hybrid** — note = engine data (one additive `CompactionContext` field); merge pass skipped by construction in note-guided mode; pre-filter = separate default-OFF stage; (B) new dreamer engine REFUSED; council 4.00–4.25 vs A 3.15–3.70 vs B 1.75–2.00. |
| ES-2 | Pre-filter shape + the decidable accounting scheme | **✅ RULED** | Drop only what the note's **structured marker block** mechanically accounts for; everything else falls through unchanged; free-text ⇒ vacuous; **never-empty wall** + **whole-group atomicity**; v1 occupant = deterministic zero-LLM matcher, default OFF; every failure ⇒ identity. |
| ES-3 | Carrier for the armed note | **✅ RULED** | **`instances.metadata` JSONB** (`compact_note_arm` / `compact_note_armed_at` / `compact_note_sha256_12`) via `set_metadata`/`delete_metadata`; **NO migration**; the record IS the checkpoint `messages` channel. |
| ES-4 | Render slot + the in-context outcome marker | **✅ RULED (v1.2)** | The note is the **FIRST ARM of the GLOBAL OVERVIEW branch** (header `:994`) — it **replaces** the three-way chain when consumed; the auto arms are byte-identical when it is not; **never co-exists** with another GLOBAL text; **exempt from the 600-token cap**; the merge is skipped by the **`_merge_summaries` entry guard** (def `:4306`, first statement, `(None, False)`), so the note is merge-independent **by construction**; `previous_overview` (`:996-997`) is **suppressed** when a note is consumed; marker = envelope header `note_sha8`/`note_chars`/`note_guided=true`. |
| ES-5 | The note-armed turn-boundary hook | **✅ RULED** | **Forcing at the next quiescent seam (T3/T4); the 80% threshold is BYPASSED** — the gate measures SIZE, the note measures RELEVANCE. The forcing lives in the **T3 gate as a threshold bypass**; the engine is called with **`force=False`, NEVER `force=True`** (named hazard: force=True below the floor ⇒ 50%-tail truncation — FORBIDDEN). No-arm path byte-identical by construction. |
| ES-6 | Dedup + rate limit | **✅ RULED** | The 60s window **CAN bind** (worst case one seam-cycle) — accepted, with the honest receipt load-bearing; anti-abuse = **`DELIBERATE_COMPACT_RATE_LIMIT_S = 900` hardcoded constant, NO knob**, enforced at arm time as `prompt_reload_compact_rate_limited` echoing remaining seconds; clock = `compact_note_last_applied_at` (deliberate compactions only); both deferred states recorded verbatim ("deferred … not compacted"). |
| ES-7 | Note-consumption atomicity | **✅ RULED** | Consumed by the **first quiescent doc-writing compaction** (T3 expected, T4 too); cleared ONLY on `replacement_messages` non-empty ∧ `persist_compaction_result` True (stamp-only must not consume); mid-turn sites never read the arm; emergency and below-floor skips do not consume; crash window re-applies at the next quiescent seam. |
| ES-8 | Pre-filter default state + kill lever + budget | **✅ RULED** | **Default OFF**, ships disabled; kill lever = one `CompactionConfig` bool (`compact_note_prefilter_enabled`), env-only, deliberately absent from `config.yaml`; v1 occupant deterministic ⇒ no LLM budget knob; **outside the P1 doctrine** (EX-2/enum/D3 untouched); substitution disclosed to the Emperor. |
| ES-9 | The owed clause-4 rewording + the C5 byte-identity scope | **✅ RULED** | The architect's clause-4 rewording ADOPTED verbatim ("NO NEW DATA-LOSS SURFACE ON THE NOTE PATH … the pre-filter is the ONE separately-authorized exception"); C5 scope = `compact=false ∧ note empty/absent`; the other shape refuses `prompt_reload_note_requires_compact`. |
| ES-10 | AR-5's doctrine question | **✅ FILLED (recorded, not decided — Emperor)** | The doctrine **stays ratified and untouched**; the lane's design is **inert to the answer** (the pre-filter never touches the decision family). The Emperor's question surfaces on his next waking. |
| ES-11 | This summary | — | *meta* — not a slot; it tracks the ten above. |

---

## 14. Anchor verification (re-measured in-worktree, 2026-11-16)

**Rule applied.** Every anchor cited in this plan was re-verified by grep/read **in the worktree** at use time. Where the measurement differed from the orientation lane's value, **this plan's value is the one used** and the difference is disclosed. Anything that could not be verified read-only is marked UNVERIFIED.

**Counts: 81 anchors re-measured (44 on 2026-11-16 + 16 for the architect fold + 9 for the consumption ruling + 12 on 2026-11-17 for the D25 ruling) — 76 MATCH, 4 DRIFT (corrected values used), 1 UNVERIFIED.**

### DRIFT (3) — the corrected values are the ones used in this plan

| # | Anchor | Orientation value | **Measured value (used)** | Impact on this plan |
|---|--------|-------------------|---------------------------|----------------------|
| D-1 | `PRIVILEGED_TOOL_CATEGORIES` total | "21" | **23 members**; `prompt-reload` = **#20 of 23** (source-literal order) | §2/§10/§15 cite #20 of 23. The pin `test_prompt_reload_pins.py:71` asserts `len(...) == 23` — **consistent**. See the note below. |
| D-2 | **The T1 anchor — BOTH anchors, with their roles.** The orientation's phrasing ("T1 call site is graph.py:9722, not :8394") was **wrong as phrased**: neither number is "the" call site. | Orientation: "T1 call site `daemon/graph.py:9722`, not `:8394`" | **`daemon/graph.py:8394` is the `compact_state` call INSIDE `_maybe_precall_compact_95` (def `daemon/graph.py:8138`); `daemon/graph.py:9722` is that helper's graph-level invocation** (it is NOT a `compact_state` call). T2 = the CLE reactive handler (`except ContextLengthExceededError:` at `daemon/graph.py:9890`) with `compact_state` at `daemon/graph.py:9990/9998/10019` (+ a commented-out `:9957`). **SIX live `compact_state` call sites** under FOUR triggers: 8394 · 9990 · 9998 · 10019 · `instance_messaging.py:1535` · `compact_executor.py:1220`. | §7 and R7 state both anchors with their roles. A council reviewer who greps `9722` expecting `compact_state` now finds **no false drift**. |
| D-3 | `tests/unit/test_compaction.py` node count | "130 nodes" | **125 `def test_` functions** measured; the pack header `test/packs/compaction_unit_test.sh` still claims **130** | **D-3 row itself + the UNVERIFIED note at the end of §14** (§10.6 is not a heading in this plan; the discrepancy is disclosed here and in the UNVERIFIED note); **UNVERIFIED** whether 130 nodes is still correct (collection was not run — read-only). |
| D-4 | The `── GLOBAL OVERVIEW ──` header line | architect v1.2: "The `── GLOBAL OVERVIEW ──` header opens at `:993`" | **`:994`** — the architect's `:993` is the blank line above it (the header assignment `body = envelope + "\n── GLOBAL OVERVIEW ──\n"` is at `:994`) | Cosmetic ±1 only; the architect's `:996-997` (`if previous_overview:` + its body), `:998` (`if global_overview:`), `:1024-1029`, `:1030-1032`, `:1034` are all **exact**. The plan uses **`:994`**. |
| D-5 | The partial-path enclosing-branch anchor (A-7 reviewer) | plan `:3649` ("the partial-path `else:`") | **The partial-path `else:` is at `daemon/compaction.py:3736`** — `:3649` is `if outcome.stop_reason in ("timeout", "budget"):` (the enclosing test), not the branch. | **Recorded as D-5 (anchor-table row, not a decision id).** The plan's Phase 3 task 3.5 reference is corrected to `:3736`. |

### Re-measured 2026-11-17 (16 new anchors, for the architect-ruling fold — all MATCH)

`daemon/compaction.py`: `:788` `_apply_ceiling_rule` (def) · `:222` `_extract_previous_overview` (def) · `:994` the `── GLOBAL OVERVIEW ──` header · `:996-997` the `if previous_overview:` line (precedes the cap check) · `:998` `if global_overview:` · `:1012-1021` the 600-token render-time truncation · `:1024-1029` the truncation arm · `:1030-1032` the placeholder arm · `:1034` `if n_sections > 0:` · `:1053-1056` the doc body `SystemMessage(content=body)` · `:3120-3132` `force=True` bypasses the 60s dedup · `:3649` the partial path (`stop_reason ∈ {timeout, budget}`) · `:3657` `if outcome.merge_failed:` · `:3687-3691` **merge site 1** (outer re-merge) · `:3697` `global_overview = None` (merge failure) · `:3768-3772` **merge site 2** (partial path) · `:3778` `global_overview = None` (merge failure) · `:3922` `_summarize_chunked` (def) · `:3624` its sole caller · `:4187-4191` **merge site 3** (inner merge inside `_summarize_chunked`; fires FIRST on full success) · `:4192` its `if not ok:` branch · `:4306` `_merge_summaries` (def — **the skip's single home**)
`daemon/services/_compaction_persist_seam.py:72` `persist_compaction_result` · `daemon/repositories/instance/repository.py:2990` `set_metadata` (+ `:3202` `delete_metadata`) · `daemon/services/_escalation_metadata.py:73-84` the four compaction-scoped metadata keys (precedent) · `.agents/tester/PACKS.md:483` `prompt_reload_refusals_unit` · `.agents/tester/PACKS.md:484` `prompt_reload_tool_unit`.

**Re-measured 2026-11-17 (9 more, for the final consumption ruling) — all MATCH, all using the architect's re-pinned values:** `daemon/config.py:1660` `min_messages_before_compaction` (default **10**) · `daemon/constants.py:603` `MIN_MESSAGES_BEFORE_COMPACTION: int = 10` · `daemon/compaction.py:3357-3386` the `force`/floor branch (force ⇒ fall through to `_last_effort_tail_truncation`) · `daemon/compaction.py:3368-3370` the `skipped_below_min_messages` clean skip · `daemon/services/decision/registry.py:82-90` `DENIED_SEAM_ROLES` (`compaction_message_selection` = EX-2 at `:85`) · `daemon/services/decision/registry.py:276-280` the registration refusal ("refused before any call path exists") · `daemon/services/decision/registry.py:355` `transition_stage` · `daemon/services/decision/registry.py:389` `is_deciding_permitted` · `daemon/services/decision/types.py:92-96` the `Monotonicity` enum members (the architect cites `:92-97`; the members themselves are measured at `:92-96`) · `daemon/config.py:1686` the `proactive_enabled` alias precedent.

### A pre-existing RED this lane must not be confused with

`tests/unit/tools/test_prompt_reload_pins.py` contains a test named `test_the_prompt_reload_entry_is_second_to_last_in_the_literal` which asserts that `prompt-reload` is the **second-to-last** entry in the `PRIVILEGED_TOOL_CATEGORIES` literal. Measured today: `prompt-reload` is **#20 of 23**; the second-to-last entry is `guidelines` and the last is `room`. The file is **identical to `origin/latest`** (`git diff origin/latest -- tests/unit/tools/test_prompt_reload_pins.py` → empty), so this RED is **pre-existing on latest** and was introduced by the group-chat lane's additions (`room`) — **not** by this lane, and not by the compact-note work.

**Why this lane still records it:** this lane **edits that exact file** (the refusal-code census must be re-derived there), and a red in that file could be misattributed to the compact-note change. The implementer must (a) re-derive the census **by grep** at implementation time, (b) not "fix" the unrelated pin without the group-chat lane's owner, and (c) report the pre-existing red explicitly in the lane's own test results so the ledger stays honest.

### MATCH (40) — verified in-worktree

`daemon/tools/prompt_reload.py`: `:225` `PromptReloadInput` · `:208` `create_prompt_reload_tools` · `:242` `async def prompt_reload(force: bool = False)` · `:249` the ari-only gate · `:67` `KILL_SWITCH_ENV` · `:70` `PROMPT_RELOAD_AUTHORIZED_AGENT` · `:95` the REFUSED envelope · `:99` the FAILED envelope · `:104` `_not_ari` · `:138` `_render_report` · `:318` `_build_full_doc`.

`daemon/services/prompt_refresh.py`: `:145` the envelope constants · `:147-156` the 9 REFUSED + 1 FAILED codes · `:160` `REFUSAL_CODES` · `:609` the "the future compaction caller inherits the same contract" anticipation.

`daemon/tools/_tool_registry.py`: `:26` `DYNAMIC_TOOL_NAMES` (the `prompt_reload` entry at `:328`) · `:483` `PRIVILEGED_TOOL_CATEGORIES` (the `prompt-reload` entry at `:845`) · `:1284` `CATEGORY_MODULES` (the entry at `:1484`) · `:1714` `KNOWN_TOOL_NAMES` (the entry at `:2104`).

`daemon/compaction.py`: `:156` `GLOBAL_DOC_PLACEHOLDER_GLOBAL` · `:166` `GLOBAL_OVERVIEW_TOKEN_CAP = 600` · `:168` `COMPACTION_DOC_CEILING_FRACTION = 0.15` · `:177` `CompactionAborted` · `:317` `build_sentinel_replacement` · `:481-497` the silent-loss pre-write guard · `:499-503` the final return · `:874` `build_compaction_doc` · `:996-1032` the three-way GLOBAL branch · `:1010` `estimate_messages_tokens` · `:1276` `CompactionContext` · `:1582` `select_compactable_groups` · `:1938` `_snap_orphan_tool_messages_at_cut` · `:2518` `_last_effort_tail_truncation` · `:3126-3132` the dedup read · `:3722-3732` the tail re-attach · `:3749-3752` the sections built before the merge · `:3697`/`:3778` `global_overview = None` on merge failure · `:4306` `_merge_summaries` · `:5091` `_is_recently_compacted`.

`daemon/graph.py`: `:8138` `_maybe_precall_compact_95` (def) · `:8394` the `compact_state` call **inside** it · `:9722` that helper's **graph-level invocation** · `:9890` the CLE reactive handler (`except ContextLengthExceededError:`) · `:9990/9998/10019` its `compact_state` calls (+ commented `:9957`) · `:4093` `SessionState` · `:4253` `attestation_last_bare_ai_content` · `:8294-8306` the 95%→80% escalation gate.

`daemon/services/instance_messaging.py`: `:1337` `_maybe_compact_context` · `:1535` the call · `:1705` `_record_proactive_skip`.

`daemon/services/compact_executor.py`: `:555` `execute_compact` · `:1220` the `compact_state(..., force=True)` call.

`daemon/config.py`: `:1602` `CompactionConfig` · `:1738-1753` the `AliasChoices` idiom (`dedup_window_s`) · `:5614`/`:5634` the `extra="forbid"` typo guard · `:7562` `_resolve_compaction_model` · `:7906` `_resolve_proactive_enabled`.

`daemon/tools/inner_soul.py`: `:564` the `_compact_memory` helper · `:1882`/`:1887` its call sites (the orientation's ":1877-1930" region).

`daemon/services/memoir_reflection_service.py:1` · `daemon/repositories/memoir/models.py` `:194`/`:401`/`:695` · migrations `20261105_102821_create_memoir_tables.sql` + `20261109_021154_create_memoir_v2_tables.sql`.

`agents/ari/meta.json:47` (the allow-list; **42** entries today, and the pin at `test_prompt_reload_pins.py:221` asserts `len(allow) == 42` — consistent).

`tests/unit/tools/test_prompt_reload_refusals.py` — **15** `def test_` functions (matches). `tests/unit/tools/test_prompt_reload_tool.py` — the "zero conversation-history writes" docstring at `:14`, the leg at `:456`. `.agents/tester/PACKS.md:4504` — the `compaction` (narrow) registration; `:4572` — the 22-file authoritative set. `docs/guidelines/README.md` — checklist item 3 at `:47-48`, item 4 at `:49`. `docs/guidelines/guideline-memoir-reflection-lane.md` exists.

**UNVERIFIED (1).** The exact pytest **node** count of `tests/unit/test_compaction.py` (the pack header says 130; the source carries 125 `def test_` functions; parametrization may account for the difference, but collection was not run because this lane is read-only on the repo).

### One more thing the plan does not assert

`daemon/config.py:4317-4322` (`MemoirReflectionConfig.max_llm_calls_per_pass`) is the budget line the orientation brief associated with "JEV call counts towards". It is **the memoir config's**, not the decision family's — §11 records the two-"JEV" collision and the zero coupling. This lane does not depend on that anchor for any requirement; it is cited only to prevent a future reader from conflating the two.

---

## 15. Open questions (status after the 2026-11-17 fold — the plan is council-ready)

| # | Question | Who decides | Status (2026-11-17) |
|---|----------|--------------|----------------------|
| Q1 | ES-9's C5 byte-identity scope | **Leader** | **ANSWERED** — leader ruling 2026-11-16 (D4 CONFIRMED) + architect §G concurrence. Verbatim scope statement in AC6.2. |
| Q2 | ES-9's clause-4 rewording text | **Architect** | **ANSWERED** — the architect's draft is ADOPTED verbatim (ES-9); the Guideline (Phase 6 task 6.7) quotes it. |
| Q3 | ES-10 (AR-5): may the decision-engine overlay ever be a deciding surface? | **Emperor** | **FILLED (recorded, not decided)** — the doctrine stays ratified and untouched; the lane's design is inert to the answer. Surfaces on the Emperor's next waking; **does not block this plan**. |
| Q4 | The pre-existing RED in `test_prompt_reload_pins.py` (`second_to_last`) | **Leader** (routing) | **ANSWERED** — leader ruling 2026-11-16: quarantine-classify by name (plan §10.7), do NOT fix, do NOT touch; surfaces at merge time via the giter's overlap gate. |
| Q5 | The ARMED receipt's outcome token | **Architect** (frozen-string discipline) | **ANSWERED** — `outcome=armed_pending_seam` (architect §H), carried by AC10.1. |
| Q6 | ES-1…ES-11 (the engine-shaped slots) | **Architect** | **ANSWERED 11/11** — architect decision of record **f159b3e3** (ES-1/2/3/4/8/9) + the **final consumption ruling** (ES-5/6/7) + ES-10 filled as recorded-not-decided. **No slot remains open.** |

**Residual items that are NOT questions** — they are flagged tensions carried to the council round in `decisions.md` (D25–D27): the merge-skip call-site coverage gap, the architect's `§D` merge-range drift, and the "merge forced to fail" E2E degeneracy under "merge skipped by construction".

---

## 16. Research insights (what shaped this plan)

- **The live defect is a render-layer defect, not a merge-layer one.** The context header reads "(overview unavailable — merge pass failed; the sections below are authoritative)" because `global_overview` is set to `None` on merge failure (`daemon/compaction.py:3697` / `:3778`) and the doc's three-way branch then emits the placeholder (`:1030-1032`). The sections are built **before** the merge (`:3749-3752`) and **survive** the failure — which is exactly why the note can ride alongside them. (Shape of the fix — RULED v1.2: the note is the **first arm** of the GLOBAL branch and **replaces** the chain; the merge never runs.)
- **The three-way branch is the whole render surface.** `build_compaction_doc` (`:874`) has exactly three ways to fill the GLOBAL region: a merged GLOBAL, nothing (truncation mode), or the placeholder (merge failed). A note that renders on only one of them is a note that disappears exactly when it is most needed.
- **A "note replaces the merge" design has a natural implementation point.** The merge-failure path is a single assignment (`global_overview = None`); the note can occupy that slot — which is why ES-4 offered (a) as the cheapest shape, and why the architect's ruling on the render slot turned out to be the single highest-leverage decision. **RULED (v1.2): the merge pass is SKIPPED by construction in note-guided mode (the `_merge_summaries` entry guard, def `daemon/compaction.py:4306`), and the note renders as the FIRST ARM of the GLOBAL branch on every doc-producing path.** The planner's D25 flag was vindicated: a per-site gate would have been **dead code on the primary path** (see `decisions.md` D29).
- **The compaction pipeline already owns everything the note needs to compose with**: the summarizer, the dedicated model, the ceiling rules, the sentinel mechanics, the cost plumbing, and the section structure. That is the substance behind Ari's recommendation to upgrade rather than fork (recorded as a recommendation-input, ES-1).
- **The note's carrier needs no DDL.** The checkpoint state is ONE JSONB document (`daemon/graph.py:4093`); the additive-with-default idiom is used 22× and free-text precedent exists (`:4253`). `CompactionContext` (`daemon/compaction.py:1276`) has all fields defaulted, so a new field is a pure code change. `instances.metadata` is the third carrier. (ES-3.)
- **"There is no agent-callable self-compaction path today"** — the closest precedent is `inner_soul._compact_memory` (`daemon/tools/inner_soul.py:564`, called at `:1882`/`:1887`), which compacts **memory.md**, not the conversation. The compaction executor's own module docstring already anticipates this lane: *"the future compaction caller inherits the same contract"* (`daemon/services/prompt_refresh.py:609`). The plumbing is net-new.
- **The house's honesty discipline is the design spine.** The prior lane's ledger (D10, D22, D24, D25) is built on one idea: *a receipt must never claim more than happened* — no seam guard but full disclosure, one frozen INFO line per terminal path, and two deliberately different frozen sets that must never be reconciled. This lane applies the same discipline to ARMED vs COMPACTED.
- **The compaction doc's ceiling rule already has the right shape for a protected region.** `COMPACTION_DOC_CEILING_FRACTION = 0.15` condenses the oldest sections first and never the GLOBAL (`:985-991`) — exactly the policy the note needs (B1/B2). The note joins the protected set.
- **The two "JEV"s are different things** (§11) and the pre-filter slot must name which one it binds to, if either.
- **The memoir lane is the doctrinal sibling, not the code sibling.** The dream consolidates episodic memory; the note consolidates working memory. The kinship line is documentation; the mechanics are unrelated (F5).
