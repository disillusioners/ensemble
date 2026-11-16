# Plan Overview: prompt_reload(compact, compact_note) — the compact-note lane

Date: 2026-11-16
Author: planner-side worker via plan-creation skill (ENGINE-AGNOSTIC HALF)
Status: Draft — **engine shape NOT decided**; see §13 `RULING PENDING — ENGINE SHAPE`
Branch: `feature/prompt-reload-compact-note` (worktree `/home/nea/ensemble-src-wt-prompt-reload-compact`, base tip `858a85e31`)
Prior art: `.agents/shared/planning/prompt-reload/` (the original prompt_reload lane — ledger format, refusal-vocabulary conventions, honesty discipline)

**WHAT THIS DOCUMENT IS.** The engine-agnostic half of the lane plan. It fixes the **contract**: the parameter surface, the refusal vocabulary, the note cap, the honest receipt, the seam-safety law, the scope fence, and the acceptance criteria that any engine must satisfy. It does **NOT** pick the engine, the carrier, the hook shape, the render mechanism, or the pre-filter design — those are the architect's, and each is carried in §13 as an **explicit open slot with the criteria the answer must satisfy**.

**WHAT THIS DOCUMENT IS NOT.** It is not a design of the dreamer/distill module, not a specification of the pre-filter's marker scheme, and not an implementation plan for code paths whose shape is undecided. Where an engine detail would normally appear, there is an open slot instead.

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
| S8 | A DORMANT (default-OFF) jev pre-filter **slot** — its shape is an open slot; its containment law is fixed here | Emperor direction, R-A, AR-2, AR-1 |
| S9 | The acceptance/test strategy, naming exact files, a NEW pack and its PACKS.md registration | task §5, AR-4 |
| S10 | The scope fence | task §6 |
| S11 | The Guideline deliverable (written in a later phase, after tester sign-off) | R-D |
| S12 | The decision ledger (this lane's `decisions.md`) | task §2 deliverable 2 |

### Out of Scope

| # | Item | Why out |
|---|------|----------|
| X1 | Any new agent roster entry / a "dreamer" agent in `agents/**` | The engine shape is undecided (§13 ES-1); if a new agent is ruled, the architect names it. This lane plans no roster change. |
| X2 | A parallel compaction engine | R-C + the scope fence. If ES-1 is ruled as "new dreamer module", the architect's ruling overrides this fence entry **explicitly and in writing** — it is an open slot, not a closed question. |
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

- **AC2.1** `prompt_reload(compact=True)` returns exactly the house REFUSED envelope: `Error: prompt_reload REFUSED — reason=prompt_reload_compact_note_required: <message>` (envelope shape per `daemon/services/prompt_refresh.py:145`, `daemon/tools/prompt_reload.py:95`).
- **AC2.2** The refusal has **ZERO** side effect on the compaction path: a spy on the note-carrier writer and on `ContextCompactor.compact_state` asserts **no** call.
- **AC2.3** Exactly one INFO line fires, `outcome=refused`, `code=prompt_reload_compact_note_required`; the line contains no note content (there is none) and no persona content.
- **AC2.4** The refusal message NEVER contains the words "compacted" / "compaction complete" / "summary rendered" — it says the note is required. (A refusal is a design outcome, not an error — `docs/guidelines/README.md` item 3.)

### R3 — note over cap → typed refusal, NEVER silent truncation

**Requirement.** C3 + task §4: a note exceeding the cap is refused. Silent truncation is forbidden — it would produce an authoritative layer that silently differs from what the agent wrote.

- **AC3.1** A note of `cap + 1` characters returns `Error: prompt_reload REFUSED — reason=prompt_reload_compact_note_too_large: <message>`, and the message states **both** the cap and the measured length.
- **AC3.2** A note of **exactly** `cap` characters is **accepted** (the boundary is inclusive).
- **AC3.3** No truncation path exists in the admission code: a mutation leg deletes the length check and AC3.1 goes RED.
- **AC3.4** A refused over-cap note leaves **no** trace in the carrier (AC2.2's spy applies).

### R4 — note empty / whitespace-only → typed refusal

**Requirement.** Two distinct conditions with two distinct remedies, so two distinct codes.

- **AC4.1** `compact=True, compact_note=""` (note absent/empty) → REFUSED `prompt_reload_compact_note_required` — the note was never provided.
- **AC4.2** `compact=True, compact_note="   \n\t  "` (non-empty but whitespace-only) → REFUSED `prompt_reload_compact_note_empty` — a note was provided and carries no content.
- **AC4.3** The two codes are distinguishable at the tool surface by a test that asserts each code on its exact input.

### R5 — a SECOND note while one is armed → **REFUSE** (plan decision D3)

**Requirement.** Pick one: replace, or refuse. **This lane picks REFUSE** (rationale in D3).

- **AC5.1** With a note armed and **not yet landed**, a second `prompt_reload(compact=True, compact_note="…")` → REFUSED `prompt_reload_compact_note_already_armed`.
- **AC5.2** The refusal leaves the **first** armed note **unchanged** — the second note's text does not replace it, is not written to the carrier, and does not appear in any log line.
- **AC5.3** After the armed note **has landed** (the compaction happened), a fresh `prompt_reload(compact=True, compact_note="…")` is **accepted** — the slot is free. (The armed slot is scoped to *un-landed* notes only.)
- **AC5.4** The refusal message names the **armed** state explicitly and NEVER claims a compaction occurred.

### R6 — note supplied with `compact=false` → **REFUSE** (plan decision D4)

**Requirement.** Pick one: ignore, or refuse. **This lane picks REFUSE** (rationale in D4).

- **AC6.1** `prompt_reload(compact=False, compact_note="text")` → REFUSED `prompt_reload_compact_note_without_compact`.
- **AC6.2** `prompt_reload(compact=False)` (note absent/empty) → **byte-identical** to today's receipt. **Scope note:** commission clause 5's byte-identity promise is scoped to the input subspace `compact=false ∧ compact_note empty/absent` — the shape that exists today. The (false, non-empty-note) shape is a NEW input shape and is out of that promise; this scoping is stated explicitly because it is a deliberate reading of C5 (see D4 and §13 ES-9).
- **AC6.3** The refusal NEVER claims a compaction was requested, armed, or performed.

### R7 — seam safety: the compaction happens at the NEXT TURN BOUNDARY, never mid-turn

**Requirement.** C1 ("TURN BOUNDARY; NEVER mid-turn"). The two mid-turn compaction triggers in the tree — T1 `_maybe_precall_compact_95` (`daemon/graph.py:8138`, called at `daemon/graph.py:9722`) and T2 the CLE reactive handler (`daemon/graph.py:9890`, `except ContextLengthExceededError:`) — are **off-limits**.

- **AC7.1** During the tool call itself, **no** conversation mutation happens: a spy on `ContextCompactor.compact_state` and on the message list asserts **zero** calls and zero mutation.
- **AC7.2** The note-armed compaction runs at a TURN BOUNDARY seam — the E2E leg asserts the compaction occurred **between** turns, and the agent's in-flight turn was unaffected.
- **AC7.3** The note-armed path is **not** wired into T1 or T2: a pin asserts the note-armed hook is not registered in the mid-turn trigger set.

### R8 — the note rides VERBATIM in the compacted context, at the GLOBAL OVERVIEW slot

**Requirement.** C2: the note must ride verbatim and be rendered where the auto GLOBAL OVERVIEW sits today.

- **AC8.1** After the compaction, the compacted context contains the note's text **byte-for-byte** — no re-summarization, no trimming, no rewriting, no case-folding, no normalization.
- **AC8.2** The note renders **in the GLOBAL OVERVIEW region**: a test locates the note's text between the `── GLOBAL OVERVIEW ──` header and the `── SECTION DETAIL ──` header of the compacted doc (`daemon/compaction.py:996-1032`).
- **AC8.3** The note renders on **ALL THREE** branches of the three-way branch in `build_compaction_doc` (`daemon/compaction.py:998-1032`):
  - (i) a merged GLOBAL is present (`global_overview` truthy);
  - (ii) `mode == "truncation"` — the branch that currently renders **nothing**;
  - (iii) the merge failed → `global_overview = None` (`daemon/compaction.py:3774-3778`) → the branch that currently renders `GLOBAL_DOC_PLACEHOLDER_GLOBAL` ("(overview unavailable — merge pass failed; the sections below are authoritative)", `daemon/compaction.py:156`).
  Three separate tests, one per branch. **(iii) is the regression that matters.**
- **AC8.4** In note-armed mode the merge-failure **placeholder is not rendered** — the note supersedes it. The compacted context contains no "(overview unavailable …)" text when an authoritative note is present, because that text would assert the absence of an authoritative summary that demonstrably exists.

### R9 — the note is INDEPENDENT of the merge pass (the core defect this lane fixes)

**Requirement.** C2: the note "must NOT depend on the merge pass succeeding". AR-4: "Stored" is not sufficient — the agent's **next turn** must actually see it.

- **AC9.1** Force `_merge_summaries` (`daemon/compaction.py:4306`) to return `("", False)` → the compacted context STILL contains the note verbatim (AC8.1 re-asserted under merge failure).
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

- **AC10.1** The armed receipt's success token is drawn from a **distinct, frozen** set that does **not** include any "compacted" verb. Proposal: outcome token `compact_note_armed` (exact literal pinned by a golden test). The prior lane's frozen sets (`no_change`/`reloaded`/`reloaded_db`/`forced_reload`/`unproven` for the marker; the 6-value INFO enum) are **not** extended — the compact-note surface carries its **own** outcome namespace so the prior lane's pins do not move. (Prior ledger D25's "the two sets are DIFFERENT BY DESIGN and must never be reconciled" discipline applied forward.)
- **AC10.2** The armed receipt states the seam semantics in plain words ("will be applied at the next turn boundary"), and reports the note's **length** and **sha256 prefix** — never the note text.
- **AC10.3** **How the agent learns the compaction actually happened.** The primary receipt is **CONTEXT-VISIBLE**: because the note rides verbatim in the compacted context (R8), the agent's own next turn *reads its note back out of its context* — the presence of the note IS the proof it landed. This requires no new agent-facing channel and cannot be lost to a log the agent cannot read.
- **AC10.4** A **secondary, explicit** outcome signal is also required: the compacted context carries a compaction-outcome marker stating that the GLOBAL region was supplied by an authored note (not by a machine merge). This is what lets the agent distinguish "my note is the summary" from "a machine merged a summary". The exact marker shape is an **open slot** (§13 ES-4) — the *criteria* are fixed: content-free (length + hash prefix only), inside the compacted context, and never in a daemon log the agent cannot read.
- **AC10.5** If the compaction is **deferred** (see §13 ES-6 — e.g. the dedup window binds), the receipt to the agent MUST say **"deferred, not compacted"** and MUST NOT be a success receipt for work not done. (Resident input (2); non-negotiable under either branch of ES-6.)

### R11 — content-free observability (the note never lands in an agent-readable log)

**Requirement.** Task §7 risk #9; resident input (3): the note is the agent's working memory and can carry intimate empire content. It rides verbatim in the **RECORD** and the **CONTEXT**; never in an agent-readable log.

- **AC11.1** Every log line emitted by the note path carries the note's **length** and **sha256 prefix** only. A grep-pin asserts that a distinctive sentinel string planted in a test note appears in **zero** log lines.
- **AC11.2** The note text appears in exactly two places: the compaction **record** (the checkpoint) and the compacted **context**. A pin asserts the note is not written to any audit/ledger table that an agent-readable tool can query.
- **AC11.3** The armed/landed/deferred/refused outcome is observable to an **operator** (INFO line, frozen format, field order pinned) with no note content.

### R12 — no new data-loss surface (commission clause 4, AS REWORDED — see AR-3)

**Requirement.** C4. Per **AR-3**, clause 4's "removes nothing that survives today" is owed a rewording that applies to the **NOTE PATH**, with the pre-filter named as the **separately-authorized exception**. The architect drafts the text (open slot §13 ES-9); until then this lane uses the two-contracts-separate framing (R-B).

- **AC12.1 (note path — pure addition).** Everything compaction already preserves is preserved unchanged: the verbatim tail (`select_compactable_groups`, `daemon/compaction.py:1582`, re-attached at `daemon/compaction.py:3722-3732`), the silent-loss pre-write guard (`build_sentinel_replacement`, `daemon/compaction.py:317`, guard at `:481-497` raising `CompactionAborted` at `:177`), the orphan-tool-message pairing snap (`_snap_orphan_tool_messages_at_cut`, `daemon/compaction.py:1938`), the halving loop (`_last_effort_tail_truncation`, `daemon/compaction.py:2518`), the dedup window (`_is_recently_compacted`, `daemon/compaction.py:5091`, read at `:3126-3132`).
- **AC12.2** The note **adds** an authoritative layer; it **removes** nothing that survives today. A test compares the compacted doc's non-note regions against the same fixture compacted without a note, and asserts equality.
- **AC12.3 (pre-filter — the separate contract).** The pre-filter is the **only** removal surface, it is **separately authorized** (R-B), it is **default-OFF** (R-A), and it is covered by R14's containment law. The note path and the pre-filter path are two contracts and are never conflated in code, tests, or docs.

### R13 — the default path is byte-unchanged

**Requirement.** C5 + R-C.

- **AC13.1** `prompt_reload()` (no args) → byte-identical receipt (golden pin).
- **AC13.2** The **auto**-compaction path is byte-unchanged: a fixed fixture compacted by the automatic trigger produces a byte-identical compacted doc before and after this lane (golden pin on the doc).
- **AC13.3** No registration-surface change: `daemon/tools/_tool_registry.py`, the privileged-set census (23 members today — see §14), the ari `tools.allow` list (42 entries today), and `tool-categories.json` are **unmodified** by this lane.
- **AC13.4** Exactly **one** existing pin must move, and it must move for a **stated** reason: `tests/unit/tools/test_prompt_reload_tool.py` pins "zero conversation-history writes" (its docstring at `:14`, the leg at `:456`). The compact-note path writes the note to the **compaction carrier** — which is a conversation-history-adjacent write — so that pin's SCOPE must be narrowed to "the prompt_reload path writes no conversation history **except** the compact-note carrier write, which is the compaction system's own write". The pin must be amended with that scope wording, not deleted, and not weakened to a no-op.

### R14 — the pre-filter is DORMANT and contained (its shape is an open slot)

**Requirement.** R-A (one release, two postures), AR-1 (invariant), AR-2 (containment). The Emperor's direction opens the capability; the *shape* is ES-2/ES-8.

- **AC14.1** The pre-filter ships **DEFAULT-OFF** and disabled.
- **AC14.2** With the pre-filter OFF, the note path's output is **byte-identical** to its output with the pre-filter feature entirely absent (golden pin).
- **AC14.3** The pre-filter runs **only** when a note is present. A test asserts no filter invocation on a note-less compaction.
- **AC14.4** Degradation is always **skip-the-filter**, never **fail-the-compaction**: pre-filter unavailable, over budget, erroring, or timing out → the compaction proceeds normally. A test injects each failure mode and asserts the compaction still lands.
- **AC14.5** **AR-1 invariant, verbatim:** "pre-filter may drop a message only when the note demonstrably ACCOUNTS FOR IT; anything else falls through UNCHANGED." Corollary: "accounted for" must be **mechanically decidable** at filter time — never a judgment call; if it is not decidable, the message falls through unchanged.
- **AC14.6** The pre-filter has its **own budget** and its **own kill lever**, independent of the compaction's budget and of the note cap.
- **AC14.7** The P1 shadow-only doctrine ("Decision Module P1 is SHADOW-ONLY — overlay observes+logs, never decides") **STAYS RATIFIED**. Whether the decision-engine overlay may ever become a *deciding* surface is a **doctrine amendment** and is an **Emperor** question (AR-5, §13 ES-10) — not this lane's to decide, and not smuggled in by this release.

### R15 — Guideline deliverable

**Requirement.** R-D. `docs/guidelines/guideline-prompt-reload-compact-note.md`, status `draft`, written **after** tester sign-off per the lifecycle (`docs/guidelines/README.md` §4).

- **AC15.1** The guideline exists and carries `status: draft` in the house's draft form.
- **AC15.2** It addresses `docs/guidelines/README.md` checklist **item 3** (refusal codes carry contract meaning an agent/operator must not misread — this lane's five new codes each get a one-line contract meaning) and **item 4** (the feature interacts with a privileged tool category — `prompt-reload` is PRIVILEGED, entry #20 of 23 today, §14).
- **AC15.3** It carries the kinship doctrine line (C6) **verbatim**: *"the working-context counterpart of the memoir dream lane — the dream consolidates her episodic memory; the compact note consolidates her working memory."*
- **AC15.4** It is written **after** tester sign-off (lifecycle order), so in this lane it is a **phase task**, not a delivered artifact.

---

## 4. The refusal vocabulary (frozen)

Envelope shapes are the house's, byte-identical to the prior lane (`daemon/tools/prompt_reload.py:95` REFUSED, `:99` FAILED):

```
Error: prompt_reload REFUSED — reason={code}: {message}
Error: prompt_reload FAILED  — reason={code}: {message}
```

**The existing 10 codes (9 REFUSED + 1 FAILED) are UNCHANGED.** The compact-note surface adds **5 REFUSED** codes. Total after this lane: **14 REFUSED + 1 FAILED**.

| # | Code | Trigger | Envelope | Honesty rule |
|---|------|---------|----------|--------------|
| 1 | `prompt_reload_compact_note_required` | `compact=true`, note absent or `""` | REFUSED | says the note is REQUIRED; never says a compaction was requested or performed |
| 2 | `prompt_reload_compact_note_empty` | `compact=true`, note is non-empty but whitespace-only | REFUSED | says the note carries no content; never says a compaction was performed |
| 3 | `prompt_reload_compact_note_too_large` | note length > cap | REFUSED | states the cap and the measured length; **never truncates** |
| 4 | `prompt_reload_compact_note_already_armed` | a second note while one is armed and un-landed | REFUSED | names the ARMED state; never claims a compaction occurred |
| 5 | `prompt_reload_compact_note_without_compact` | note supplied with `compact=false` | REFUSED | never claims a compaction was requested |

**Required properties of the vocabulary.**

- **P1 — every code distinguishes ARMED from COMPACTED.** No code in this table may be reachable on a path where a compaction actually happened, and no code may be emitted *after* a successful landing.
- **P2 — refusal ≠ error.** The house treats a refusal as a design outcome (`docs/guidelines/README.md` item 3). These five are design outcomes, not failures; they ride the REFUSED envelope, never the FAILED envelope.
- **P3 — the FAILED set stays at one.** `prompt_reload_internal_error` remains the only FAILED code. No compact-note condition may route to it — a too-long note is a design outcome, not an internal error.
- **P4 — the note is never echoed in a refusal message.** Refusal messages quote the cap and the LENGTH, never the text.
- **P5 — the codes are enumerable and pinnable.** `REFUSAL_CODES` (`daemon/services/prompt_refresh.py:160`) is extended by exactly these five; a test asserts the frozenset's exact membership and count. The refusal-code count pin must be **re-derived by grep**, never copied.

**Where the codes must be registered.** `daemon/services/prompt_refresh.py` (the code constants + `REFUSAL_CODES` frozenset) and the tool surface at `daemon/tools/prompt_reload.py`. The tool's self-doc (`_build_full_doc()`, `daemon/tools/prompt_reload.py:322`) is generated from `REFUSAL_CODES`/`FAILURE_CODES`, so the new codes appear in the tool's own help automatically — a test asserts that.

---

## 5. The honest tool contract (receipts)

### 5.1 `compact=false` (today's shape)

**Byte-identical to today.** The receipt is the existing `_render_report` output (`daemon/tools/prompt_reload.py:138`) — `prompt_reload {outcome} — {message}` plus the frozen provenance block and the prior lane's marker. **Nothing is added to it.** This is C5's "purely additive surface" read at the receipt level.

### 5.2 `compact=true` + a valid note — the ARMED receipt

The compaction happens at a **later** seam. The receipt therefore describes an **arming**, not a compaction.

Required content of the armed receipt:

| Element | Content | Why |
|---------|---------|-----|
| outcome token | `compact_note_armed` (frozen literal, pinned) | a token that cannot be read as "compacted" |
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

If the compaction is deferred (see §13 ES-6), the agent must be told **"deferred, not compacted"** — never a success receipt. The two mechanisms are open slots (§13 ES-3 for the carrier, ES-6 for the deferral signal), but the *vocabulary* is fixed here: `deferred` is a distinct, named state alongside `armed`, `landed`, `refused`.

---

## 6. The note cap

### 6.1 The proposal

> **CAP = 8,000 characters** (measured in Unicode code points, on the raw `compact_note` string as supplied).

Boundary semantics: **inclusive** — a note of exactly 8,000 chars is accepted; 8,001 is refused with `prompt_reload_compact_note_too_large`.

### 6.2 Why 8,000 and not 16,000

The commission's range is ~8–16k chars. The deciding constraint is **the compaction doc's own ceiling rule**, not a taste call:

- The compacted doc's whole-doc ceiling is `COMPACTION_DOC_CEILING_FRACTION = 0.15` (`daemon/compaction.py:168`) of the context window — GLOBAL + Σsections ≤ 15%. The ceiling rule condenses the **oldest sections first, never GLOBAL** (`daemon/compaction.py:985-991`).
- The GLOBAL region's own cap is `GLOBAL_OVERVIEW_TOKEN_CAP = 600` **tokens** (`daemon/compaction.py:166`).
- A 16,000-char note ≈ **4,000 tokens** at a conservative 4 chars/token. At a 128k-token window, 15% = 19,200 tokens; a 4,000-token note consumes **~21%** of the entire doc ceiling — it would crowd out SECTION DETAIL wholesale and permanently distort the compaction's own budget arithmetic.
- An 8,000-char note ≈ **2,000 tokens** ≈ **~10%** of the 19,200-token doc ceiling at 128k. It is a first-class region that leaves room for sections, and it stays inside the ceiling on any window ≥ 32k tokens.
- Signal-vs-redundancy: past ~2,000 tokens an authored distillation is redundancy, not signal — the note's whole value is that it is **tight**. The commission asks for the agent's own distillation of what is settled / open / dead, not a transcript.
- Operability: 8,000 is a round, memorable, easy-to-verify boundary (one zero-counted in bytes and in the test fixture).

### 6.3 Interaction with the token-based compaction budget

**Rule B1 — the note is a SEPARATE doc region; it is NOT folded into `GLOBAL_OVERVIEW_TOKEN_CAP`.**
Folding an 8,000-char note into a 600-token cap would truncate the authoritative layer — which the commission forbids (C3: refuse, never silently truncate) and which would make the note's authority a lie. The note therefore has its **own** budget line and its own region in the compacted doc.

**Rule B2 — the note is never condensed.**
The ceiling rule condenses oldest sections first; the note is never a condensation candidate (it is authored content, and condensation would rewrite the agent's words).

**Rule B3 — the note's cost is charged against the doc ceiling, and the doc reports the charge.**
The note's token cost is computed with the **same estimator** the doc already uses (`estimate_messages_tokens`, used at `daemon/compaction.py:1010`). The compacted doc's accounting shows the note's contribution so an operator can see how much of the ceiling the note took.

**Rule B4 — the note cannot breach the ceiling by itself, on any supported window.**
With the 8,000-char cap, the note's worst case (~2,000 tokens) stays under the 15% doc ceiling for any context window ≥ ~16k tokens. Below that, the ceiling rule degrades as it already does (condense sections; hard cap → GLOBAL + ARCHIVED line, `daemon/compaction.py:151-154`). The note is never silently dropped by the ceiling rule — if the ceiling cannot accommodate it, the compaction **aborts** via the existing `CompactionAborted` path (`daemon/compaction.py:177`) rather than writing a truncated authoritative layer.

**Rule B5 — the cap is a `CompactionConfig` field.**
`CompactionConfig` (`daemon/config.py:1602`) has `env_prefix="COMPACTION_"` and the house `extra="forbid"` typo guard (`:5614`, `:5634`) — an unknown YAML key is **refused at boot**. A new knob must therefore be a **new class field**. The cap field uses the house `AliasChoices` idiom (`daemon/config.py:1738-1753`, the `dedup_window_s` pattern) so both `COMPACTION_<FIELD>` and the long-form env alias resolve.

**Rule B6 — the env kill-switch precedence caveat is disclosed.**
pydantic-settings gives **init kwargs priority over env** (`daemon/config.py:7562` `_resolve_compaction_model`, `:7906` `_resolve_proactive_enabled` are the house's explicit resolvers). A YAML value can therefore silently defeat an operator's env override unless an explicit resolver is added in `load_config`. The cap field's resolver follows the house pattern, and the caveat is recorded here so the implementer does not assume env-wins for free.

**Rule B7 — one knob only.**
The cap is the **only** new config knob this lane adds (task §6). The pre-filter's budget + kill lever are the architect's to rule (§13 ES-8) and are **not** planned here.

---

## 7. The seam-safety law

**The law.** A compact-note compaction is triggered **only** at a turn boundary. Never mid-turn. Never inside the tool call.

**Why the mid-turn triggers are excluded.** T1 (`_maybe_precall_compact_95`, `daemon/graph.py:8138`, call site `:9722`) and T2 (the CLE reactive handler, `daemon/graph.py:9890`) both run **inside** a live turn, with the message list in hand. A note-armed compaction fired from either would mutate the conversation the agent is still using. T3 (`_maybe_compact_context`, `daemon/services/instance_messaging.py:1337`, call `:1535`) and T4 (`execute_compact`, `daemon/services/compact_executor.py:555`, call `:1220`) are turn-boundary shapes.

**Which of T3/T4 the note-armed hook uses is an open slot** (§13 ES-5) — but note that T4's shape (`pause → quiesce → compact → resume`, `force=True` at `daemon/services/compact_executor.py:1220`) is the closest existing expression of "this conversation, compacted now", while T3 is the proactive over-threshold gate (0.80) that would need a forced bypass. **The architect rules; the criteria are in ES-5.**

---

## 8. The scope fence (what this lane must NOT build)

| # | Forbidden | Note |
|---|-----------|------|
| F1 | A new agent roster entry (`agents/**`) | ES-1 is an open slot, but this lane plans no roster change |
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

Phases 1 and 2 are **engine-independent** and can start immediately. Phases 3–5 are **gated** on the architect's open slots.

| Phase | Name | Objective | Tasks | Coupling | Gate |
|-------|------|-----------|-------|----------|------|
| 1 | Contract surface: params, cap, refusals, receipts | The tool's public contract is fixed, implemented, and pinned — with NO engine dependency | 8 | independent of the engine; tight with Phase 2 (the carrier) | none — start now |
| 2 | The note carrier (GATED: ES-3) | The armed note is carried from the tool call to the seam, survives pause/quiesce/resume, and is consumed exactly once | 6 | tight with Phase 3 (the render) and Phase 5 (the E2E) | **ES-3** |
| 3 | The note render (GATED: ES-4) | The note renders verbatim in the GLOBAL region on all three branches, independent of the merge pass | 6 | tight with Phase 2 | **ES-4** |
| 4 | The note-armed seam hook (GATED: ES-5, ES-6) | The compaction fires at a turn boundary, exactly once, with honest deferral | 6 | tight with Phase 2 (carrier) and Phase 3 (render) | **ES-5, ES-6** |
| 5 | The pre-filter, DORMANT (GATED: ES-2, ES-8) | A default-OFF, contained, mechanically-decidable filter that ships disabled | 6 | loose with Phase 3 (the render's input set) | **ES-2, ES-8** |
| 6 | Acceptance, registration, Guideline | The pack is registered, the E2E artifact exists, the Guideline is drafted after tester sign-off | 7 | tight with all prior phases | Phase 6 |

### Phase 1 — Contract surface (engine-independent) — 8 tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1.1 | Add `compact` / `compact_note` to `PromptReloadInput` + the tool body | none | AC1.1, AC1.3 |
| 1.2 | Implement the five refusal codes + envelope routing | 1.1 | AC2.1, AC3.1, AC4.1, AC4.2, AC5.1, AC6.1; AC10.4's code set |
| 1.3 | Implement the cap field on `CompactionConfig` (+ the `load_config` resolver) | 1.1 | AC3.1, AC3.2, R6's B5/B6 |
| 1.4 | Implement the ARMED receipt + the frozen `compact_note_armed` token | 1.1 | AC10.1, AC10.2 |
| 1.5 | Content-free INFO line (length + sha256 prefix), frozen field order | 1.2 | AC11.1, AC11.3 |
| 1.6 | Golden-pin today's `prompt_reload()` receipt byte-for-byte | none | AC1.2, AC13.1 |
| 1.7 | Narrow the "zero conversation-history writes" pin to the stated scope | none | AC13.4 |
| 1.8 | Extend `test_prompt_reload_refusals.py` (15 → 20 tests) + the `REFUSAL_CODES` membership pin | 1.2 | §4 P5; AC2.1..AC6.1 |

**Exit criterion.** The tool's contract is implemented, pinned, and reviewable with **zero** dependency on the engine ruling. `prompt_reload(compact=True)` arms or refuses; it never compacts yet, and it never says it compacted.

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
| 3.3 | Branch (i): a merged GLOBAL is present | 3.2 | AC8.3(i) |
| 3.4 | Branch (ii): `mode == "truncation"` | 3.2 | AC8.3(ii) |
| 3.5 | Branch (iii): merge failed → `global_overview = None` (`daemon/compaction.py:3774-3778`) | 3.2 | AC8.3(iii), AC8.4, AC9.1 |
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
| `tests/unit/tools/test_prompt_reload_pins.py` | The refusal-code membership/count pin is **re-derived by grep** (never copied). **The privileged-set count pin must be RE-DERIVED, not copied** — see §14's note on the pre-existing RED | POK `testgate-01`, task §5 |
| `tests/unit/test_compaction.py` | The merge-failure render leg (branch iii) | AC8.3(iii), AC9.1 |
| `.agents/tester/PACKS.md` | The new pack's registration row | §12 L0 |

### 10.3 The NEW pack

**`test/packs/prompt_reload_compact_note.sh`** — a standalone shell pack, registered in `.agents/tester/PACKS.md` in the same lane.

Dual-layer timeout per the house test-pack convention (layer 1: the caller wraps with `timeout 240`; layer 2: the script-internal `timeout 210s` on the pytest process).

### 10.4 Legs (L0–L9) — each one proves exactly one thing

| Leg | Proves | Assertion (what a test would assert) | File |
|-----|--------|-------------------------------------|------|
| **L0** | The pack is registered | `.agents/tester/PACKS.md` contains a row naming `prompt_reload_compact_note.sh`; the pack runs green from a clean tree | PACKS.md + the pack |
| **L1** | Param validation | The args schema has exactly `force`/`compact`/`compact_note` with the right defaults; `prompt_reload()` returns today's receipt **byte-identical** | `tests/unit/tools/test_prompt_reload_tool.py` |
| **L2** | Cap boundary | 8,000 chars → accepted; 8,001 → REFUSED `..._too_large` with cap + length in the message; **no** truncation (the note is absent from the carrier) | new file `tests/unit/tools/test_prompt_reload_compact_note.py` |
| **L3** | Both refusal families | All five codes reachable, each on its exact input, each in the REFUSED envelope, each with zero carrier side effect, each message free of a "compacted" claim | `tests/unit/tools/test_prompt_reload_compact_note.py` + `test_prompt_reload_refusals.py` |
| **L4** | Seam safety | No `compact_state` call during the tool call; the note-armed hook is not registered in T1/T2; the compaction runs **between** turns | `tests/unit/tools/test_prompt_reload_compact_note.py` + `tests/unit/test_compaction.py` |
| **L5** | The note renders VERBATIM | The note's exact bytes appear in the compacted context, in the GLOBAL region, between the GLOBAL and SECTION DETAIL headers | `tests/unit/test_compaction.py` |
| **L6** | The note is independent of the merge pass | `_merge_summaries` forced to `("", False)` → the note is STILL present, verbatim; the merge-failure placeholder is NOT rendered; the surviving sections are still present | `tests/unit/test_compaction.py` |
| **L7** | Content-free logging | A sentinel string planted in the note appears in **zero** log lines; the INFO line carries length + sha256 prefix only; the note is not in any agent-readable table | new file + a log-capture fixture |
| **L8** | The default path is byte-unchanged | `prompt_reload()` receipt byte-identical (golden); a fixed fixture's auto-compacted doc byte-identical (golden); the registry + census unchanged | `tests/unit/tools/test_prompt_reload_tool.py` + `tests/unit/test_compaction.py` |
| **L9** | **AR-4 E2E (the acceptance artifact)** | A REAL conversation + a REAL note + the merge pass FORCED TO FAIL → the agent's **NEXT TURN** context contains the note. Asserted against the context the next turn receives — not a stored field, not the checkpoint alone, not a log line | new E2E file (name fixed at implementation: `tests/integration/test_prompt_reload_compact_note_e2e.py`) |

### 10.5 Non-vacuity (the house's standing demand)

- **L6 is not vacuous:** the mutation leg plants a merge-failure stub that returns `("", False)` and asserts the note **survives**. Removing the note's independence (i.e. letting the merge-failure path drop the note) turns L6 RED. The same stub must be proven to actually trigger the failure branch — a leg asserts the placeholder **would** render without the note (so the stub is proven load-bearing, not a no-op).
- **L9 is not vacuous:** the forced merge failure is asserted to have occurred (the merge's `ok` flag is False in the same run). "The note is in the context" alone would pass even if the merge had succeeded.
- **L1 is not vacuous:** the golden receipt value is captured from the pre-change code, not hand-written.
- **L5 is not vacuous:** the assertion is on the note's exact bytes (including a sentinel the summarizer could not have produced), not on "some summary is present".

### 10.6 Test surface (house caution)

- The compaction-scoped surface is broad: **169** test files under `tests/` mention "compact", carrying **3,562** `def test_` functions. `tests/unit/test_compaction.py` alone carries **125** `def test_` functions (the pack header at `test/packs/compaction_unit_test.sh` claims **130** nodes — a **disclosed discrepancy**: collection was not run read-only, so the node count is UNVERIFIED; parametrization plausibly accounts for the difference). Do not run a bare `pytest tests/` — it collects the whole tree and can run for hours on unrelated suites.
- The registered compaction pack is `compaction_unit_test` → `test/packs/compaction_unit_test.sh` → `tests/unit/test_compaction.py` (registration at `.agents/tester/PACKS.md:4504`; the 22-file authoritative set at `:4572`).

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
| 3 | **The note is consumed twice** — the note lands in two compactions | High | Low | Exactly-once consumption (ES-7); the land clears the armed slot atomically with the compaction write (Phase 4 task 4.5). |
| 4 | **A STALE note rides a LATER compaction** — the note is armed, the conversation moves on, and the note lands on a compaction the agent did not intend | **High** | Medium | The note is scoped to the conversation it was armed in; the arm records the message-count/turn generation it was armed against; the seam compares the current generation against the armed generation and treats a mismatch as a **deferred** (honest, not compacted) — never a silent landing of a stale note. The exact mechanism is **ES-7**; the honesty requirement is fixed (AC10.5). |
| 5 | **The note is never consumed** — armed, then lost | High | Low | The armed state is observable (a carrier read); the seam consumes it; a crash between arm and seam leaves it armed (retry lands it) or surfaces it as lost — never silently. ES-7's criteria. |
| 6 | **Mid-turn mutation** — the note-armed compaction fires inside a turn and mutates the live message list | **High** | Low | R7 + AC7.1/7.2/7.3; T1 and T2 are excluded by a pin; the E2E asserts the in-flight turn was unaffected. |
| 7 | **Silent truncation instead of refusal** — an over-cap note is trimmed to fit | **High** | Medium | AC3.3's mutation leg (delete the length check → RED); the cap is checked at **admission**, before anything is written; B1/B4 forbid the note from being condensed or truncated at render time. |
| 8 | **The tool lies** — it says "compacted" when it only armed | **High** | Medium | §5's vocabulary; a frozen token set that contains no "compacted" verb; the forbidden-substring pin; the prior lane's marker-set discipline (D25) applied forward as a separate namespace. |
| 9 | **The note lands in an agent-readable daemon log** | **High** | Medium | R11: length + sha256 prefix only; the sentinel-grep pin (L7); the note's only two homes are the record and the context; the memo/dream and shadow-lane content-free doctrines are the precedent, and the guideline cites them as such (Phase 6 task 6.7). |
| 10 | **The note's budget blows the compaction's ceiling** — a big note crowds out SECTION DETAIL | Medium | Medium | §6's Rules B1–B4: the note is a separate region, never condensed, charged against the 15% doc ceiling, and the 8,000-char cap keeps it under ~10% of that ceiling at 128k. |
| 11 | **The pre-filter silently drops a message the note does not account for** | **High** | Low (ships OFF) | AR-1's mechanically-decidable law (AC14.5); undecidable → falls through unchanged; the filter ships **disabled** (AC14.1); R-B keeps it a separate contract. |
| 12 | **The pre-filter is mistaken for the Decision Module** — a shadow-only overlay becomes a deciding surface | High | Low | The pre-filter ships OFF and only runs with a note present (AC14.3); the P1 doctrine stays ratified (AC14.7); ES-2/ES-10 keep the naming and the doctrine question explicit. |
| 13 | **The default path regresses** | High | Low | AC13.1/13.2 golden pins; R-C's one-hook limit; the lane's edits to the compaction path are pinned by the byte-unchanged golden. |
| 14 | **The privileged-set / census pin is copied, not re-derived** | Medium | Medium | Task §5 and POK `testgate-01`: re-derive by grep at implementation time. **See §14 — the census is 23 today, and one pin in `test_prompt_reload_pins.py` is ALREADY red on `origin/latest` for a reason unrelated to this lane.** |
| 15 | **The C5 byte-identity promise is read too broadly** — a caller passes `compact=false` + a note and expects today's receipt | Medium | Medium | D4's scoping is explicit: the promise covers `compact=false ∧ note empty/absent`; the other shape is a new input shape with a typed refusal. The leader may overrule D4 (§13 ES-9). |
| 16 | **The two contracts get conflated** — a reader believes the note path can lose messages | Medium | Medium | R-B + R12: the note path is pure-additive; the pre-filter is the only removal surface, separately authorized, default-OFF. The Guideline (Phase 6 task 6.7) states the two contracts in separate sections. |
| 17 | **The engine ruling arrives after implementation starts** — Phase 1 is built against a shape the architect overturns | Medium | Medium | Phase 1 is deliberately engine-independent (contract only); Phases 2–4 are gated on ES-3/ES-4/ES-5. If the ruling overturns a Phase-1 decision, only the receipt vocabulary and the refusal codes might move — the acceptance criteria do not. |

---

## RULING PENDING — ENGINE SHAPE

*(§13 of this plan — the engine-shaped open slots.)*

**Status: OPEN. No answer is guessed below. Each slot states only (i) the question, (ii) the criteria the answer must satisfy, and (iii) what it must NOT break.**

The architect owns these. The Emperor's direction (verbatim, authoritative): *"For this feature you can upgrade, reuse the existing compaction feature of system or write new one (dreamer/distill agent). It take messages and your compact note then produce new context. This module can use decision engine (jev model) to quickly filter messages before feed to dreamer compaction agent."*

**Ari's recommendation (recorded as a recommendation-INPUT to ES-1, NOT as a decision):** upgrade the existing pipeline rather than build a parallel engine — it already owns the summarizer, the dedicated model, the ceiling rules, the sentinel mechanics, the cost plumbing, and the section structure the note must compose with; the "dreamer" becomes a **note-guided MODE** of that pipeline, not a second engine. Add (a) the note-injection layer; (b) an **optional** jev pre-filter stage (cheap triage before the expensive summarizer), with its own budget + kill switch, degrading gracefully (unavailable/over-budget → skip the filter, compaction proceeds normally — never a hard dependency); (c) an explicit seam-safety proof that the pre-filter can never drop a message the note references as settled.

---

### ES-1 — Engine shape: reuse/upgrade the existing pipeline, or a new dreamer/distill module?

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

**It must NOT break:** AC1.2, AC13.1, AC13.2, AC12.1, AC12.2, R7 (seam safety), R11 (content-free).

**Recommendation-input (not a ruling):** Ari's — see the paragraph above ES-1.

---

### ES-2 — Pre-filter shape + its seam-safety invariant

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

**Question.** Does the note render (a) by filling the `global_overview` parameter of `build_compaction_doc` (`daemon/compaction.py:874`), (b) as a **sibling** parameter with its own render branch, or (c) at the **assembly** seam (`build_sentinel_replacement`, `daemon/compaction.py:317`, final return `:499-503`)?

**The answer must satisfy:**
1. The note renders **where the GLOBAL OVERVIEW sits today** — this is a hard constraint from C2 ("rendered WHERE the auto 'GLOBAL OVERVIEW' sits today"). A ruling that places the note outside the doc (e.g. as a separate injected `SystemMessage`) must justify the deviation against the commission's wording.
2. The note renders on **all three** branches (AC8.3), including `mode == "truncation"` and the merge-failure branch.
3. If (a): the merge-failure path (`global_overview = None`, `daemon/compaction.py:3774-3778`) must be changed to fall back to the note rather than to `None` — a **one-line** change at that site, and the sections built before the merge (`:3749-3752`) must be unaffected.
4. If (c): the note must still be part of the compacted **context** the agent reads, and must **not** be a free-floating injected message outside the doc (the doc is what the agent reads as its compacted context).
5. The note is **never condensed** and **never truncated** by the ceiling rule (B2).
6. The in-context **outcome marker** (R10 AC10.4) is content-free (length + digest prefix only), lives inside the compacted context, and is distinguishable by the agent from a machine-merged overview.
7. The assembly seam's **silent-loss pre-write guard** (`daemon/compaction.py:481-497`, raising `CompactionAborted` at `:177`) must still cover the note's presence — i.e. the note's injection is part of the replacement set the guard validates.

---

### ES-5 — Where the note-armed forced turn-boundary compaction hooks

**Question.** Does the note-armed hook take the **T3** shape (`_maybe_compact_context`, `daemon/services/instance_messaging.py:1337`, call `:1535` — the proactive over-threshold gate at 0.80, which would need a forced bypass) or the **T4** shape (`execute_compact`, `daemon/services/compact_executor.py:555`, call `:1220` — `pause → quiesce → compact → resume` with `force=True`)?

**The answer must satisfy:**
1. **R-C's one-hook limit:** exactly ONE hook is added to the compaction path. The auto path (T1/T2/T3/T4 as they exist) is otherwise untouched.
2. **Turn boundary only** — the hook is NOT wired into T1 (`daemon/graph.py:8138`) or T2 (the CLE reactive handler, `daemon/graph.py:9890`) (AC7.3).
3. The hook is **reachable from the calling agent's own turn end**, and the calling agent's in-flight turn is unaffected (AC7.1/7.2).
4. The hook honors the compaction's existing **guards** (min-messages floor, ceiling rules) unless the ruling says the note-armed path is exempt — and if exempt, the ruling says so explicitly (the executor's force semantics already document which checks still apply under force: `daemon/services/compact_executor.py:13-16`, `:1197-1203`).
5. The hook does not create a **second** compaction record writer for the same conversation (one writer per record).
6. If the T4 shape is ruled, the pause→quiesce→resume semantics are the blueprint's canonical shape and the ruling must say how the armed note survives them (feeding ES-3).
7. The hook's **failure** never corrupts the conversation: a failed note-armed compaction leaves the conversation intact and the armed note in a state the agent can be told about (honest deferral, not a silent loss).

---

### ES-6 — Dedup interaction (the 60s anti-thrash window) vs the note-armed force

**Question.** Does the dedup window (`_is_recently_compacted`, `daemon/compaction.py:5091`, read at `:3126-3132`; `dedup_window_s` default 60, `daemon/config.py:1738-1753`) bind the **deliberate**, note-armed compaction?

**Context (resident input (2), for the architect to rule on).** A note-armed deliberate compact is a **different trigger class** from the threshold-triggered automatic compaction. The 60s window exists for the AUTOMATIC class and arguably should not swallow the deliberate one. "Compacted now" is the contract when the agent asks. If anti-abuse is needed, an **honest typed refusal** (rate-limited — e.g. one deliberate compact per N minutes) is preferred over a silent deferral, because a disclosed swallow is still a swallow.

**The answer must satisfy:**
1. **NON-NEGOTIABLE under either branch:** if the window binds, the receipt MUST say **"deferred, not compacted"** — NEVER a success receipt for work not done (AC10.5).
2. The ruling must state which trigger class the window governs, in words.
3. If the window does not bind the note-armed path, the ruling must say how the note-armed path avoids double-compaction at the same seam (Risk #2) — the consumption rule (ES-7) is the mechanism.
4. If the window does bind it, the ruling must specify the **typed refusal/deferral** code and the agent-facing wording.
5. No branch may leave the agent believing a compaction happened when it did not (R10).

---

### ES-7 — Note-consumption atomicity (exactly-once across crash/retry)

**Question.** What are the note's consumption semantics between arm and seam?

**The answer must satisfy:**
1. **Exactly-once:** the note lands in the compacted context **at least once and at most once** (Risk #3).
2. **Atomic clear:** the armed slot is cleared **atomically with** the compaction write — a crash between them must not leave a note that can land twice (Phase 4 task 4.5).
3. **Crash between arm and seam:** the note is either still armed (a retry lands it) or is **explicitly surfaced as lost** — never silently dropped (Risk #5). The house precedent for a revived instance's re-binding (the `round_count` discipline: re-BOUND on revival, NEVER reset) is the shape to copy.
4. **Staleness:** a note armed against an older message generation must not land on a later compaction without a decision (Risk #4). The ruling must specify the generation check and what happens on a mismatch — and a mismatch must produce a **deferred**, never a silent landing of a stale note.
5. The agent-visible state vocabulary is exactly: `armed` → (`landed` | `deferred` | `refused`). No other state is ever reported to the agent.
6. The consumption semantics must not require a **new table** unless the ruling records it here first (ES-3, F7).

---

### ES-8 — Pre-filter default state, kill lever, and budget

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

**AR-3 (owed artifact).** Commission clause 4's phrase *"removes nothing that survives today"* must be restated to apply to the **NOTE PATH**, with the **pre-filter** named as the **separately-authorized exception**. The architect drafts the reworded text. Until it is drafted, this lane uses the two-contracts-separate framing (R-B, R12) and records the rewording as **OWED**.

**Proposed rewording for the architect's adjudication (this lane's input, not a ruling):** *"The note path removes nothing that survives today: the note adds an authoritative summary layer and suppresses no preserved message. The separately-authorized jev pre-filter is the single exception — it may drop messages, only when the note demonstrably accounts for them, and only while explicitly armed; it ships default-OFF."*

**Also owed: the C5 byte-identity scope** (D4). C5 says *"`prompt_reload(compact=false)` (today's shape) byte-identical in behavior"*. This lane reads that promise as scoped to the input subspace **`compact=false ∧ compact_note empty/absent`** — the shape that exists today — so that the D4 refusal for `(compact=false, non-empty note)` does not violate it. **The leader may overrule D4** (in which case the note-with-compact=false case becomes IGNORE-with-disclosure instead of REFUSE, and AC6.1 flips accordingly). This is the one place where this lane's plan makes a reading of a commission clause that it cannot settle itself; it is flagged here for the leader.

---

### ES-10 — AR-5's open doctrine question

**AR-5 (record, do not decide).** *May the decision-engine overlay ever be a **deciding** surface — i.e. may the "P1 is SHADOW-ONLY" doctrine be amended?* This surfaces on the Emperor's next waking. This lane records it and does not decide it.

**Note for the record.** The Emperor's direction for this feature says the module "can use decision engine (jev model) to quickly filter messages before feed to dreamer compaction agent" — a filtering use, not a deciding use. The AR-1 invariant (a message is dropped **only** when the note demonstrably accounts for it) plus the AR-2 containment law keep this lane's default-OFF filter **compatible with** the ratified doctrine: the filter is not the decision family, it is a note-bound pre-filter, and it decides nothing until it is explicitly armed. The ruling at ES-2/ES-8 must preserve that distinction — and if a future ruling makes the overlay a deciding surface, it is a **doctrine amendment** requiring the Emperor, not a lane decision.

---

### ES-11 — Summary of open slots

| Slot | Question | Blocks | Gate |
|-------|----------|--------|------|
| ES-1 | Engine shape: reuse/upgrade vs new dreamer/distill module | Phases 2–4 | architect |
| ES-2 | Pre-filter shape + the mechanically-decidable accounting scheme | Phase 5 | architect |
| ES-3 | Carrier for the armed note | Phase 2 | architect |
| ES-4 | Render slot vs assembly slot + the in-context outcome marker | Phase 3 | architect |
| ES-5 | The note-armed turn-boundary hook (T3 vs T4 shape) | Phase 4 | architect |
| ES-6 | Dedup interaction vs the note-armed force | Phase 4 | architect |
| ES-7 | Note-consumption atomicity (exactly-once) | Phases 2, 4 | architect |
| ES-8 | Pre-filter default state + kill lever + budget | Phase 5 | architect |
| ES-9 | The owed clause-4 rewording + the C5 byte-identity scope | the Guideline, the plan's wording | architect (text) / leader (scope) |
| ES-10 | AR-5's doctrine question | nothing in this lane | **Emperor** |

---

## 14. Anchor verification (re-measured in-worktree, 2026-11-16)

**Rule applied.** Every anchor cited in this plan was re-verified by grep/read **in the worktree** at use time. Where the measurement differed from the orientation lane's value, **this plan's value is the one used** and the difference is disclosed. Anything that could not be verified read-only is marked UNVERIFIED.

**Counts: 44 anchors re-measured — 40 MATCH, 3 DRIFT (corrected values used), 1 UNVERIFIED.**

### DRIFT (3) — the corrected values are the ones used in this plan

| # | Anchor | Orientation value | **Measured value (used)** | Impact on this plan |
|---|--------|-------------------|---------------------------|----------------------|
| D-1 | `PRIVILEGED_TOOL_CATEGORIES` total | "21" | **23 members**; `prompt-reload` = **#20 of 23** (source-literal order) | §2/§10/§15 cite #20 of 23. The pin `test_prompt_reload_pins.py:71` asserts `len(...) == 23` — **consistent**. See the note below. |
| D-2 | The T1 call site | `daemon/graph.py:8394` | **`daemon/graph.py:9722`** (`_maybe_precall_compact_95(...)` is invoked there; the definition is at `:8138` as stated) | §7 cites the corrected call site. |
| D-3 | `tests/unit/test_compaction.py` node count | "130 nodes" | **125 `def test_` functions** measured; the pack header `test/packs/compaction_unit_test.sh` still claims **130** | §10.6 discloses the discrepancy; **UNVERIFIED** whether 130 nodes is still correct (collection was not run — read-only). |

### A pre-existing RED this lane must not be confused with

`tests/unit/tools/test_prompt_reload_pins.py` contains a test named `test_the_prompt_reload_entry_is_second_to_last_in_the_literal` which asserts that `prompt-reload` is the **second-to-last** entry in the `PRIVILEGED_TOOL_CATEGORIES` literal. Measured today: `prompt-reload` is **#20 of 23**; the second-to-last entry is `guidelines` and the last is `room`. The file is **identical to `origin/latest`** (`git diff origin/latest -- tests/unit/tools/test_prompt_reload_pins.py` → empty), so this RED is **pre-existing on latest** and was introduced by the group-chat lane's additions (`room`) — **not** by this lane, and not by the compact-note work.

**Why this lane still records it:** this lane **edits that exact file** (the refusal-code census must be re-derived there), and a red in that file could be misattributed to the compact-note change. The implementer must (a) re-derive the census **by grep** at implementation time, (b) not "fix" the unrelated pin without the group-chat lane's owner, and (c) report the pre-existing red explicitly in the lane's own test results so the ledger stays honest.

### MATCH (40) — verified in-worktree

`daemon/tools/prompt_reload.py`: `:225` `PromptReloadInput` · `:208` `create_prompt_reload_tools` · `:242` `async def prompt_reload(force: bool = False)` · `:249` the ari-only gate · `:67` `KILL_SWITCH_ENV` · `:70` `PROMPT_RELOAD_AUTHORIZED_AGENT` · `:95` the REFUSED envelope · `:99` the FAILED envelope · `:104` `_not_ari` · `:138` `_render_report` · `:322` `_build_full_doc`.

`daemon/services/prompt_refresh.py`: `:145` the envelope constants · `:147-156` the 9 REFUSED + 1 FAILED codes · `:160` `REFUSAL_CODES` · `:609` the "the future compaction caller inherits the same contract" anticipation.

`daemon/tools/_tool_registry.py`: `:26` `DYNAMIC_TOOL_NAMES` (the `prompt_reload` entry at `:328`) · `:483` `PRIVILEGED_TOOL_CATEGORIES` (the `prompt-reload` entry at `:845`) · `:1284` `CATEGORY_MODULES` (the entry at `:1484`) · `:1714` `KNOWN_TOOL_NAMES` (the entry at `:2104`).

`daemon/compaction.py`: `:156` `GLOBAL_DOC_PLACEHOLDER_GLOBAL` · `:166` `GLOBAL_OVERVIEW_TOKEN_CAP = 600` · `:168` `COMPACTION_DOC_CEILING_FRACTION = 0.15` · `:177` `CompactionAborted` · `:317` `build_sentinel_replacement` · `:481-497` the silent-loss pre-write guard · `:499-503` the final return · `:874` `build_compaction_doc` · `:996-1032` the three-way GLOBAL branch · `:1010` `estimate_messages_tokens` · `:1276` `CompactionContext` · `:1582` `select_compactable_groups` · `:1938` `_snap_orphan_tool_messages_at_cut` · `:2518` `_last_effort_tail_truncation` · `:3126-3132` the dedup read · `:3722-3732` the tail re-attach · `:3749-3752` the sections built before the merge · `:3774-3778` `global_overview = None` on merge failure · `:4306` `_merge_summaries` · `:5091` `_is_recently_compacted`.

`daemon/graph.py`: `:8138` `_maybe_precall_compact_95` · `:9890` the CLE reactive handler (`except ContextLengthExceededError:`) · `:4093` `SessionState` · `:4253` `attestation_last_bare_ai_content` · `:8294-8306` the 95%→80% escalation gate.

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

## 15. Open questions (for the leader / architect — none blocking Phase 1)

| # | Question | Who decides | If unanswered |
|---|----------|--------------|---------------|
| Q1 | ES-9's C5 byte-identity scope: is the promise over `compact=false ∧ note-empty`, or over **every** `compact=false` call (which would force D4 to become IGNORE-with-disclosure)? | **Leader** | Phase 1 proceeds under this plan's reading (D4 = REFUSE) and the reading is disclosed in the plan + the ledger. |
| Q2 | ES-9's clause-4 rewording text — the architect drafts it; the Guideline and the plan's wording quote it. | **Architect** | The plan's two-contracts-separate framing (R-B) stands. |
| Q3 | ES-10 (AR-5): may the decision-engine overlay ever be a deciding surface? | **Emperor** | Nothing in this lane depends on it; the pre-filter stays default-OFF and note-bound. |
| Q4 | The pre-existing RED in `test_prompt_reload_pins.py` (`second_to_last`) — owned by the group-chat lane. This lane edits that file and must not silently "fix" another lane's pin. | **Leader** (routing) | The implementer re-derives the census by grep and reports the pre-existing red; the unrelated pin is left alone and flagged. |
| Q5 | Should the ARMED receipt's outcome token be `compact_note_armed` (this plan's proposal) or a different frozen literal? | **Architect** (frozen-string discipline) | The plan's proposal stands; the literal is pinned by a golden test at implementation, so changing it later is a one-line change. |
| Q6 | ES-5's hook shape (T3 vs T4) — note that T4's `pause → quiesce → compact → resume` is the blueprint's canonical "mutate a quiescent instance" shape, while T3 is the over-threshold proactive gate. | **Architect** | Phase 4 stays gated; Phase 1 proceeds. |

---

## 16. Research insights (what shaped this plan)

- **The live defect is a render-layer defect, not a merge-layer one.** The context header reads "(overview unavailable — merge pass failed; the sections below are authoritative)" because `global_overview` is set to `None` on merge failure (`daemon/compaction.py:3774-3778`) and the doc's three-way branch then emits the placeholder (`:1030-1032`). The sections are built **before** the merge (`:3749-3752`) and **survive** the failure — which is exactly why the note can ride alongside them. (Shape of the fix: the note occupies the GLOBAL region on all three branches.)
- **The three-way branch is the whole render surface.** `build_compaction_doc` (`:874`) has exactly three ways to fill the GLOBAL region: a merged GLOBAL, nothing (truncation mode), or the placeholder (merge failed). A note that renders on only one of them is a note that disappears exactly when it is most needed.
- **A "note replaces the merge" design has a natural implementation point.** The merge-failure path is a single assignment (`global_overview = None`); the note can occupy that slot with a one-line change — which is why ES-4 offers (a) as the cheapest shape and why the architect's ruling on the render slot is the single highest-leverage open slot.
- **The compaction pipeline already owns everything the note needs to compose with**: the summarizer, the dedicated model, the ceiling rules, the sentinel mechanics, the cost plumbing, and the section structure. That is the substance behind Ari's recommendation to upgrade rather than fork (recorded as a recommendation-input, ES-1).
- **The note's carrier needs no DDL.** The checkpoint state is ONE JSONB document (`daemon/graph.py:4093`); the additive-with-default idiom is used 22× and free-text precedent exists (`:4253`). `CompactionContext` (`daemon/compaction.py:1276`) has all fields defaulted, so a new field is a pure code change. `instances.metadata` is the third carrier. (ES-3.)
- **"There is no agent-callable self-compaction path today"** — the closest precedent is `inner_soul._compact_memory` (`daemon/tools/inner_soul.py:564`, called at `:1882`/`:1887`), which compacts **memory.md**, not the conversation. The compaction executor's own module docstring already anticipates this lane: *"the future compaction caller inherits the same contract"* (`daemon/services/prompt_refresh.py:609`). The plumbing is net-new.
- **The house's honesty discipline is the design spine.** The prior lane's ledger (D10, D22, D24, D25) is built on one idea: *a receipt must never claim more than happened* — no seam guard but full disclosure, one frozen INFO line per terminal path, and two deliberately different frozen sets that must never be reconciled. This lane applies the same discipline to ARMED vs COMPACTED.
- **The compaction doc's ceiling rule already has the right shape for a protected region.** `COMPACTION_DOC_CEILING_FRACTION = 0.15` condenses the oldest sections first and never the GLOBAL (`:985-991`) — exactly the policy the note needs (B1/B2). The note joins the protected set.
- **The two "JEV"s are different things** (§11) and the pre-filter slot must name which one it binds to, if either.
- **The memoir lane is the doctrinal sibling, not the code sibling.** The dream consolidates episodic memory; the note consolidates working memory. The kinship line is documentation; the mechanics are unrelated (F5).
