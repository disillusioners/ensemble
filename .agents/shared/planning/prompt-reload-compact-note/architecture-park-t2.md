# PARK record — the T2 decision-accelerator phase (and ruling (C))

**Date:** 2026-11-17
**Author:** architect
**Branch / HEAD at authoring:** `feature/prompt-reload-compact-note` @ `206c4fe0c` (the decision package itself is untouched since `1eabf72eb`, so every measurement below is current)
**Companion:** `architecture-pivot-append.md` @ `206c4fe0c` (v1.5 — the APPEND method, the live design of this feature)

This file exists so that the **disappearance** of a phase is on the record, with its reasons, rather than being a mystery. Nothing here is an active design instruction for the current build.

---

## 1. The PARK

**T2 — the decision-family accelerator tier — was adjudicated under the user's directive of 2026-11-16** (the "decision call need optimization … at most 10 decision call … prefer use on the most match removal keywords, long message" directive). That adjudication produced a full design: the advisory-verdict shape, the quota constant, the T1→T2 interface, the EX-2 resolution, the enablement posture, the cost accounting, and the acceptance harness.

**It was withdrawn by a same-day directive. Verbatim:**

> "Any module can use a 'quick' backed model small agent for replacing decision (if absolutely need). In this compaction shouldn't use decision agent like that because we can simply send messages list to compaction via LLM."

**The FINAL design of this feature, in one sentence:** *the compaction LLM selects inline, given the messages list + the note, with the model doing the selection itself and **no decision-family involvement of any kind**.* In the shipped form (v1.5) that is the **append method**: the daemon appends the note as a request to the agent's own message list, the agent's own model writes the compaction, and the daemon swaps the history. The model sees everything and does the selection — there is no pre-filter, no shortlist, no tier, and no second model.

**Consequence for the earlier tiering:** Ari's T0/T1/T2 tiering is superseded by the user's mechanism ruling, not by this park. T0 (note-marker drops) and T1 (a zero-LLM ranked shortlist) are **not** re-adjudicated here; they were overtaken by a later ruling that removed the need for a selection substrate altogether (the model selects). The pre-filter module landed on the branch (`daemon/_compact_note_prefilter.py`, 331 lines, commit `8611f7b34`) is **removed, visibly in the diff** — see v1.5 §4.

**Nothing from the T2 adjudication ships.** No `DECISION_*` call, no quota, no per-call latency, no seam, no provisioning, no registry entry, no ledger row, no import of `daemon/services/decision/**` from any file this feature touches.

---

## 2. EX-2 — MOOT, not retired, not bypassed

`"compaction_message_selection"` sits on the family's standing exclusion list (`daemon/services/decision/registry.py:82-90`), and a registration naming it is refused "before any call path exists" (`registry.py:275-280`).

**With no seam in this feature, EX-2 neither applies nor needs to change.** It is **not retired on this feature's account**, and it is **not bypassed by renaming** — there is no registration to rename, because there is no registration. The deny-list stays exactly as it is; `tests/unit/decision/test_registry.py`'s five-role pin stays green.

**Whether EX-2 should be retired for a reason that outlives this feature is a SEPARATE, EXPLICIT ruling with its own reasons, and it is NOT taken here.** The standing arguments against it are on the record (the family's own doctrine measured the compaction's selection as already pure heuristic), but that question outlives this feature and belongs to whoever owns the decision module's doctrine — not to a lane whose feature no longer touches the family.

**A note on the family history, for whoever takes that ruling:** the earlier v1.4 architecture (the deterministic marker matcher) reached the same conclusion from the other direction — that the family structurally could not drop messages — and that analysis is preserved in v1.4 rather than re-derived here.

---

## 3. The optionality doctrine (recorded as a constraint this feature satisfies)

> "Yes, the decision engine is completely optional for the system (the reason is not all user want use, it's improvement, speed optimization only)."

**This feature satisfies it in the strongest available form: it does not use the decision module at all.** Consequently:

- The compaction path is complete and correct with **zero** decision calls.
- "Not configured" is not a state this feature can be in — the feature has no dependency to configure.
- No core path depends on the module; a deployment that never enables the decision module has a **complete** feature.
- The receipt states plainly which path ran (v1.5 §2g: `path=append`, `outcome=compacted | refused`, and the effective model).
- Provisioning remains the operator's choice and the user's billing call — **and this feature makes no provisioning demand**.

---

## 4. The reusable MEASUREMENT: is a batched verdict expressible?

This is a fact about the **family**, not about this feature. It was measured during the T2 adjudication and is recorded here because it outlives the phase. **All anchors re-verified at HEAD `206c4fe0c`.**

### 4.1 What the family exposes

- The family's **one** public client method is `consult` — `daemon/services/decision/client.py:160`:
  `async def consult(self, request: DecisionRequest, *, floor: float | None = None, timeout_s: float | None = None) -> ClientOutcome`.
  The client's own docstring states the transport contract (`:15`): *"**exactly ONE attempt** — no retry, no failover, no idempotency key"*. (A retry would double-bill.)
- `ClientOutcome.answer` is a **single** `DecisionAnswer | None` (`client.py:84+`).

### 4.2 What the request type allows

`DecisionRequest` carries `input: str | tuple[DecisionInputMessage, ...]` and `questions: tuple[DecisionQuestion, ...]` (`daemon/services/decision/types.py`), and `DecisionQuestion.name` is the **join key** to `answers[].name`. So **multiple questions in one request are expressible in the request type.**

### 4.3 What the wire allows

The proxy validates **1..128 questions per request** and requires **unique names**:
`pkg/decision/validators.go:86-87` — `if len(req.Questions) > 128 { return fmt.Sprintf("too many questions: got %d, max 128", …) }`, plus `ValidateDuplicateQuestionNames` (the System One bridge keys its questions map on `Name`).
⇒ **A batched verdict IS expressible at the wire level.**

### 4.4 What the client does with it — the blocker

`daemon/services/decision/client.py:649-650`:

```python
first = request.questions[0].name
raw_answer = by_name.get(first)
```

The client parses `answers[]`, joins **by name**, validates every answer against its question — and then returns **only the first question's answer**. The other N−1 answers are **discarded**.

And the completeness rule is **all-or-nothing** (`client.py:631-645`): if **any** question has no matching answer, the **whole** consult fails with `FailureClass.UNMATCHED_ANSWER`, and the `unanswered_questions` tuple records which ones.

⇒ **CONCLUSION: a batched verdict is expressible in the request type and on the wire, but is NOT consumable through the public `consult()` today.** Consuming N answers would require a **new** client method (or a new result field) — a change to the family's public surface, with its own tests. The natural batch shape would be: one request, N questions named `cand_<i>`, one `answers[]` entry per question.

### 4.5 The in-tree precedent that makes a batch nearly free

**The S1 seam already scores many candidates in ONE call, with no client change.** `build_s1_seam_record` (`daemon/services/decision/cost_store.py:293`) builds **one** `CHOICE` question whose **options are the candidate ids** (bounded by `MAX_CHOICES = 255`, `client.py:73`), and consumes the answer's **full per-option distribution** (`DecisionAnswer.distribution()` / `ranked_values()`, `types.py`).

⇒ **A "batched verdict" needs no client change at all if it is expressed as ONE choice question with N options** rather than N questions. This is the cheapest batched shape, and it is already proven in production by S1. (It was not reached during the T2 adjudication because that design had already been superseded by the append method — it is recorded here as a reusable fact for the family.)

### 4.6 The cost / rate / latency arithmetic (reusable)

| Shape | Rate-limit slots | Latency | Cost-store rows |
|---|---|---|---|
| **1 batched call** (one question, N options) | 1 | ≤ `timeout_s` (default **5.0 s**, `config.py:65`) | 1 |
| ≤10 sequential calls | 10 | ≤ 50 s | 10 |
| ≤10 sequential at the family's own concurrency ceiling | 10 | ~25 s (5 waves × 5 s) | 10 |

The family's own budgets (`daemon/services/decision/overlay.py:299-320`): per-seam **60/min**, global **300/min**, per-seam **concurrency 2**, global **concurrency 8** — enforced **pre-socket**, so a refused call costs nothing. The per-seam USD share is **50 % of the USD 50/mo global ceiling = USD 25/mo** (`overlay.py:120`, `:934`); a breach trips that seam to shadow-off, and the crossing call is already billed.

---

## 5. Ruling (C) — WITHDRAWN, NOT RULED

**Ruling (C) (the caller-supplied `compact_removal_keyword` list) was withdrawn by the user before it was ever ruled on. Verbatim:**

> "yes, and you don't need to think the 'compact_removal_keyword' as the tool now not need it. it just need compaction note 😄"

**Therefore no (C) ruling is issued, and none should be built.** Specifically, the acceptance question put to the architect — *"output byte-identical with and without keywords for a note accounting for the same content"* — **has no subject**: there is no keyword parameter on this branch, so there is no byte-identity assertion to make coherent or testable. The param was never landed (the developer's keyword scope addition was refused by the busy-guard and never committed), so **no code is orphaned by the withdrawal**.

**What survives of that line of work, and where it went:** the capability the keywords were meant to provide — *"the agent knows which arcs are dead and names them"* — **survives as WORDS, not as a parameter.** The note is free text and may name what should die ("the sonic arc is dead; the decision-module thread is settled"). The agent still tells the compactor what to remove, in the same breath, with zero new surface.

> **The words were always enough; the parameter was not.**

**If a future lane re-introduces a keyword parameter, the reusable facts are:** the safety line (triage, never authority; the note remains the only authority on what may be dropped), the matching rule (literal case-insensitive substring, no regex), the bounds (bounded count and per-keyword length; over-cap = typed refusal, never truncation), the honest contract (keywords without `compact=true` = typed refusal), and — from §4 — the family's batched-verdict measurement and the S1 one-question-N-options precedent. **That is a kit, not a ruling. It becomes a ruling only when somebody rules it.**

---

## 6. What I CANNOT prove — stated plainly

1. **That the live proxy route populates a per-option distribution** for a multi-option `choice` question. The in-tree evidence is S1's use of `ranked_values()`, and the client's own comment notes that the System One adapter **synthesizes a refusal** for a missing answer (`pkg/decision/typesafe.go:424-427`, cited at `client.py:628-630`) — i.e. on that route a multi-question request can come back incomplete and fail the whole consult. **Not measured against the live route.**
2. **Which adapter the live route uses at any given moment** (the OpenAI adapter is a byte-faithful pass-through and does **not** guarantee answer completeness; the System One adapter translates and synthesizes). The client handles both; their completeness behaviour differs.
3. **The per-call USD cost of a batched verdict** against the live tariff. The cost store meters what the provider reports; a multi-question request's reported cost was not measured.
4. **The live reachability of `manager._decision_overlay`** (measured as a code path, not exercised in a booted daemon) — noted only because the earlier T2 design would have depended on it.

None of the four touches the shipped design: **this feature does not use the decision family at all.**

---

**Signed:** architect, 2026-11-17. Park record for the T2 phase and ruling (C). The live design of this feature is `architecture-pivot-append.md` @ `206c4fe0c`.
