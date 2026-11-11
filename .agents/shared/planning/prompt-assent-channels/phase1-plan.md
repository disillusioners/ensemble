# Phase 1 — The designation surface

**Commission:** `prompt-assent` Phase 1.5 — designated channels (pinned-approver model).
**Doctrine:** [`doctrine.md`](doctrine.md) — *"this is channel, i often use channel to split tasks category in my mind, we consider channel is private/dm for now"* (carried verbatim).
**Binding decisions:** [`decisions.md`](decisions.md) **D-3** (the env surface), **D-4** (the
validation semantics), **D-5** (no schema change), **D-9** (threads).

> **Context owned by this phase (house rule: one context, one owner).** This phase owns the
> **pure** half of the extension — the map type, the parser, and the private-equivalent
> predicate. It touches **no** gate, **no** tool, **no** service and **no** schema. The gates
> consume it in Phase 2.

---

## 1. Objective

Deliver the **designation map** as a pure, dependency-free artefact: a strict parser that turns
an operator's env value into **either** a valid map **or** a typed-invalid outcome naming every
offending entry — and the **ONE** predicate that answers *"is this turn's surface
private-equivalent?"* (a proven 1:1 DM **or** a designated channel whose pinned approver is the
turn's human).

**Done means:** the parser's matrix is green on its own, the existing DM classifier is
**byte-unchanged**, and no production call site can yet reach any of it.

---

## 2. Decisions taken in this phase

| ID | Decision | Rationale (cite) | Rejected alternatives |
|---|---|---|---|
| **D-1.1** | The map is a **frozen dataclass** `DesignationMap(entries: Mapping[str, str])` with a `pinned_approver(channel_id) -> str | None` accessor — **immutable**, so a resolved map cannot be mutated by a caller mid-decision | the map is read on a decision path; a mutable mapping invites a TOCTOU edit between the mint check and the ledger write | a plain `dict`; a `NamedTuple` (no key-set semantics) |
| **D-1.2** | The env-name constant lives **in the module that owns the parser** (`designation.py`) and is **imported** by the tool — one literal in the tree | the house single-home pattern (`daemon/tools/prompt_assent.py:145` for the kill-switch) | a second literal in the tool (drift); a `PromptAssentConfig` field (D-3) |
| **D-1.3** | The parser returns a **typed outcome**, never a bare map | "refuse, never silently drop" (the commission) | a `dict | None` (loses the per-entry diagnostics); a raise (the call sites need a *refusal*, not an exception) |
| **D-1.4** | The parse is **all-or-nothing**: one invalid entry ⇒ the **whole** map is invalid | a partially applied map is a silent *partial authority* state | per-entry drop-with-warning (the operator cannot answer "is channel X enabled?") |
| **D-1.5** | Duplicate JSON keys are **refused** | Python's `json.loads` keeps the **last** duplicate **silently** — the textbook "silently drop" | `json.loads` as-is (silent); last-wins (explicit but still a drop) |
| **D-1.6** | The parser uses `object_pairs_hook` to see the raw pairs | the only way to detect a duplicate key at all | `json.loads` + a post-hoc scan (the duplicate is already gone) |
| **D-1.7** | Ids must match **`^[1-9][0-9]{0,31}$`** — digits only, **no leading zero**, not `0`. A non-str JSON value (an int or a bool) is **rejected**, never coerced — ids are `str` by construction and a `1` that becomes `"1"` would hide a typo | the adapter stringifies Discord ids (`daemon/sources/adapters/discord/adapter.py:1083` channel_id, `:1089` user_id), so digits-only is the *proven* shape; the tightened form additionally **kills the leading-zero never-match typo class** | a loose `^[0-9A-Za-z_-]+$` (admits typos that silently never match) |
| **D-1.8** | The **predicate** lives in `mint_context.py`, beside the untouched `is_private_assent_channel` | the "is this surface private?" question must have **ONE** owner — the mint gate and the approve predicate both consume it (D-6) | a second predicate in the service (drift); a predicate in the tool (the service cannot import the tool layer) |
| **D-1.9** | The predicate takes the **channel id** and the **user id** as **arguments** — it never reads the map itself | the map is resolved once per tool call and passed down; the predicate stays a pure function of (context, map) | a closure over the map (hides the dependency; harder to test) |
| **D-1.10** | **ONE classification function owns the WHOLE leg ordering** (DM → group-DM → absent-discriminator → map) and is the **single call for both gates**; the mint gate keeps its DM short-circuit and calls it **inside** the `not private` arm; the service predicate's designated branch lives **inside** its own `not private` arm | **architect §4.4** — as first planned, P2/T2.2 (mint) and P2/T2.3 (approve) would each implement legs 2/3: **two implementations of the same fail-closed ordering**. Precedent: `classify_user_origin` (`daemon/tools/upgrade_journal.py:4116-4166`) — ordered legs, an explicit "checked BEFORE" rationale, a fail-closed default | two per-gate implementations (the drift the architect ruled out) |
| **D-1.11** | The designated branch is gated on **`discord_meta.get("is_group_dm") is False`** (an identity check on the raw meta dict) — **the `text`-turn rule is retired** | **architect §5.3 (ii), rewritten**: the **message path** always carries `is_group_dm` (False for every guild shape, including threads ⇒ **M-13 stays MINT**); the **interaction path** builds `discord_meta` **without** the key (`daemon/sources/adapters/discord/adapter.py:1227-1254`), so the `.get(...) is False` check **fails** and the turn is **never designation-eligible** — it falls through to the existing refusal **`mint-requires-private-channel`**. A matrix cell for the absent-`is_group_dm`-key guild turn ⇒ REFUSE `mint-requires-private-channel` is **added**, and a T1.5 test | accepting any guild-shaped turn (the interaction path would become designation-eligible) |

---

## 3. Tasks

| ID | Task | Outcome (testable) | Touchpoints (files) | Depends on | Risks carried | AC |
|---|---|---|---|---|---|---|
| **T1.1** | Create `daemon/repositories/prompt_assent/designation.py` with the **env-name constant** (`ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS`), the `DesignationMap` frozen type, the `DesignationParseResult` type, and the `parse_designations(raw: str | None)` entry point | the module imports cleanly with no daemon dependencies and no I/O | `daemon/repositories/prompt_assent/designation.py` **(NEW)** | — | R-4 | AC1 |
| **T1.2** | Implement the **strict parse**: JSON object only; keys/values are digit-strings matching `^[1-9][0-9]{0,31}$` (no leading zero, not `0`; a non-str JSON value — an int or a bool — is **REJECTED**, never coerced); ≤ 64 entries; duplicate keys refused via `object_pairs_hook`; empty string ⇒ unset; `{}` ⇒ a valid empty map | every malformed input yields an **invalid** outcome with a per-entry diagnostic — never a partial map | `daemon/repositories/prompt_assent/designation.py` | T1.1 | R-4, R-5 | AC2 |
| **T1.3** | Implement the **typed-invalid outcome**: `DesignationParseResult(valid: bool, map: DesignationMap | None, errors: tuple[str, ...])` where each error names the **key** (never the value) and the **rule** — **plus the mandatory MAP-PARSE LOG LINE**: the parse state (**`unset` / `empty` / `invalid` / `N entries`**) and a **content-free fingerprint** (the first **12** hex of the canonical JSON's sha256 — **one length, everywhere**). This log line is the **only** discriminator between "map says no" and "map is broken/empty" and is **mandatory probe evidence** | an invalid map yields ≥ 1 error; no error ever contains the operator's raw value; the parse state is **always** observable from one line | `daemon/repositories/prompt_assent/designation.py` | T1.2 | R-6 | AC3, P1-AC15 |
| **T1.4** | Implement + unit-test the **ONE classification outcome function** `designation_outcome(map, discord_meta, user_id) -> DesignationResult` in `mint_context.py` — owning the **whole** leg ordering (DM → group-DM → absent-discriminator → map → absent-identity → pinned comparison) and returning a **typed outcome** with four refusal variants + a PASS variant (**B2**); the helpers `resolve_pinned_approver(map, discord_meta) -> str | None` and `is_designated_assent_channel(map, discord_meta, user_id) -> bool` are retained as the boolean convenience. **No production call site is bound in Phase 1** — wiring both gates is Phase 2's alone (**T2.2**, **T2.3**). The channel id is matched **exactly**; the designated branch is gated on `discord_meta.get("is_group_dm") is False`; `is_private_assent_channel` is **byte-unchanged** | a designated channel with a matching human ⇒ PASS; anything else ⇒ one of the four typed refusal variants; `git diff` shows `is_private_assent_channel` untouched; **production call sites = 0 in P1** (the phase fence, AC7) | `daemon/repositories/prompt_assent/mint_context.py` | T1.1 | R-2 | AC4, AC5, P1-AC16 |
| **T1.5** | Write the **parser unit matrix** — one test per validation rule (valid map; empty map; unset; non-object JSON; malformed JSON; a non-digit id; a blank id; an id of `0`; a leading-zero id; a duplicate key; > 64 entries; a value that is not a string; a key that is not a string) plus the predicate's cells (exact match; a wrong user; a **thread** id vs. its parent channel id; an absent `user_id`) plus the **S7 negative cases** — an absent `is_group_dm` key (a guild turn whose meta lacks the discriminator) yields `REFUSE mint-requires-private-channel` at the identity check, and a non-str JSON value (an int or a bool) for an id is **rejected**, never coerced | the new test file is green; every rule has a named test | `tests/unit/test_prompt_assent_designation_parser.py` **(NEW)** | T1.2, T1.4 | — | AC2, AC3, AC4 |
| **T1.6** | Add the **single-home pin**: the env-name literal appears **exactly once in `daemon/`** (the production read site). **Tests are EXEMPT** — they reference the var via the **imported constant** or via a **programmatic construction** (e.g. `f"{PREFIX}DESIGNATED_CHANNELS"`), never a second literal. **`docs/` and the runbook are EXPLICITLY EXEMPT** — the operator must be told the name, so the doc is a *documentation* site, not a *read* site; the exemption is recorded here as the rationale | the pin is green and fails if a second literal is introduced in `daemon/` | `tests/unit/test_prompt_assent_designation_parser.py` | T1.1 | R-7 | AC6 |
| **T1.7** | Add the **no-regression pin**: `is_private_assent_channel` is **byte-identical** to the base revision, and the four existing classifier cells still pass | the pin compares the function's source against the base commit's | `tests/unit/test_prompt_assent_designation_parser.py` | T1.4 | R-2 | AC5 |
| **T1.8** | **Phase-level gate (owns AC7):** prove that **no production call site** references `designation.py` or the new predicate yet — the phase is **inert** until Phase 2 | `grep` over `daemon/` shows zero references outside this phase's own module | `tests/unit/test_prompt_assent_designation_parser.py` | T1.1-T1.7 | — | AC7 |
| **T1.9** | **Phase-level gate (owns AC8):** prove the phase's **diff surface** — only `designation.py`, `mint_context.py` and this phase's test file changed; no gate, no tool, no service, no schema, no migration | `git diff --stat` against the base shows exactly three files | (git + the test file) | T1.1-T1.8 | — | AC8 |

---

## 4. Acceptance criteria

| ID | Criterion |
|----|-----------|
| **AC1** | `daemon/repositories/prompt_assent/designation.py` exists, imports with no daemon dependency, and declares the env-name constant, `DesignationMap`, `DesignationParseResult` and `parse_designations` |
| **AC2** | The parser accepts **only** a JSON object whose keys and values are strings matching `^[1-9][0-9]{0,31}$` (no leading zero, not `0`; a non-str JSON value — int or bool — is **rejected**, not coerced) with ≤ 64 entries; **every** other shape (including a non-str value, a leading-zero id, a zero id, a duplicate key, a non-object) yields an invalid outcome (one named test per rule). |
| **AC3** | An invalid outcome names **every** offending entry by **key** and **rule**, and contains **no** operator value; a duplicate JSON key is **refused** (not last-wins) |
| **AC4** | `is_designated_assent_channel` is `True` only for an exact channel-id match whose pinned approver equals the turn's user id; a thread's id does **not** inherit its parent channel's designation (**D-9**) |
| **AC5** | `is_private_assent_channel` is **byte-unchanged** and its four existing cells are green (the pin proves it, not the eye) |
| **AC6** | The env-name literal appears **exactly once in `daemon/`** (the production read site). Tests are **exempt** (imported constant or programmatic construction); `docs/` and the runbook are **exempt** (the operator must be told the name) — the rationale is recorded in T1.6 |
| **AC7** | **No** production call site references `designation.py` or `mint_context.is_designated_assent_channel` yet — the phase is **inert** until Phase 2 (`grep` proves it): **= 0 in P1**; **= 2 (mint gate + service predicate) by end of P2** — the P2 ACs (AC3, AC5) own the wiring |
| **AC8** | `git diff` against the base shows **only** the two files this phase owns (plus its test file) — no gate, no tool, no service, no schema, no migration |
| **AC9–AC14** | *(intentionally unused — cross-lane numbering preserved)* — Phase 1's ACs run AC1–AC8, then jump to **P1-AC15/P1-AC16** so the ids line up with the qualified names used in Phase 2 (P2-AC15/P2-AC16). The gap is **deliberate**, not an omission; a future lane that renumbers should preserve the qualified-prefix convention |
| **P1-AC15** | The **map-parse log line** is emitted on every parse: the state (`unset` / `empty` / `invalid` / `N entries`) + a content-free fingerprint; it never contains a channel id, a user id or the raw value |
| **P1-AC16** | **ONE** classification function owns the whole leg ordering and **returns a typed outcome**; the designated branch is gated on `discord_meta.get("is_group_dm") is False` (the `text`-turn rule is **retired**). **Phase boundary (recorded):** production call-site binding belongs to **Phase 2 alone** — T2.2 (mint gate) and T2.3 (service predicate) wire it; Phase 1 owns the function and its unit matrix, nothing more |

---

## 5. Notes for the implementer

- **Do not** import anything from `daemon.tools` in `designation.py` — the repositories layer
  importing the tools layer is a boot-order hazard (the precedent is documented at
  `daemon/repositories/prompt_assent/models.py:96-100`: a module-level `daemon.tools` import
  dead-locks the boot import order).
- **Do not** put the map's env var into `daemon/config.py` / `PromptAssentConfig` — **D-3**. The
  class's per-knob repair-to-fallback semantics (`daemon/tools/prompt_assent.py:384-463`) would
  silently repair a broken map to `{}`.
- **Do not** read `os.environ` inside `designation.py`. The module takes the raw string as an
  argument; the *caller* (Phase 2's resolver) owns the environment read. That keeps the parser
  pure and unit-testable without a manager.
- **The thread rule is a decision, not an accident** — **D-9**. If the architect rules otherwise
  (Q-1), the change lands **here**, in `is_designated_assent_channel`, and nowhere else.
