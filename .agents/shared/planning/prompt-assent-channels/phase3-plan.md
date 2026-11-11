# Phase 3 — The proof surface

**Commission:** `prompt-assent` Phase 1.5 — designated channels (pinned-approver model).
**Doctrine:** [`doctrine.md`](doctrine.md) — *"this is channel, i often use channel to split tasks category in my mind, we consider channel is private/dm for now"* (carried verbatim).
**Binding matrix:** [`refusal-cell-matrix.md`](refusal-cell-matrix.md) — every cell has a named
test here, and `INVARIANT-DM`'s four legs are **PR-1…PR-4** (matrix §4).

> **Context owned by this phase.** The **tests** and the **pack**. This phase edits **no**
> production code. It proves the gates Phase 2 wired, and it re-proves the DM cells Phase 2 must
> not have disturbed.

---

## 1. Objective

Prove **every** cell of the refusal-cell matrix — the new designated-channel cells **and** the
unchanged DM cells — with named tests, hermetic fixtures, named counter-mutations and a green,
registered pack.

> **SCOPE FENCE (B5): NO live promotion, NO arming, NO live-install mutation in this lane —
> v0.29.19 staging only; the ceremony is a separate, post-lane Emperor act.**

**Done means:** the matrix has a test per cell, the existing DM pins are green **unmodified**,
each new pin has a counter-mutation that is demonstrated to REDDEN its cell, and the pack is
registered with its real cell count and pass record.

---

## 2. Decisions taken in this phase

| ID | Decision | Rationale (cite) | Rejected alternatives |
|---|---|---|---|
| **D-3.1** | The new cells live in a **new** file `tests/unit/test_prompt_assent_designated_channel.py`; the **existing** DM-path files are **not edited** | "existing pins stay green unmodified" is the proof the commission asked for (matrix §4 PR-2) | appending to `test_prompt_assent_mint_gate.py` (the diff would blur "proved unchanged" with "edited") |
| **D-3.2** | The fixture gains an **explicit, hermetic map control** — every cell either sets the env var to an exact JSON string or **scrubs it** | env hygiene is per-variable (the house `env -u POSTGRES_*` lesson: a glob is a silent no-op); a cell must not inherit an operator's map | relying on the ambient environment (a machine with a configured map would redden the DM cells) |
| **D-3.3** | The **map-unset equivalence** cell is explicit: with the map unset, every pre-existing cell produces the identical code and ledger shape | this is the "the default posture is the v0.29.18 posture" claim, made checkable | relying on the existing pins alone (they do not assert the *ledger* shape under an unset map) |
| **D-3.4** | Every new structural pin gets a **named counter-mutation** that is demonstrated to REDDEN its cell | a pin that is green under its own leg's revert is vacuous; the house mutation discipline (POK `mutation-01-guards-need-counter-mutation`) | a green pin with no counter-mutation (prose-satisfiable) |
| **D-3.5** | The **budget assertion** is part of the new-code cells, not a separate test | "no burn" is a property of the refusal, and must be asserted **with** it | a separate budget test (the cell could redden without the budget claim being checked) |
| **D-3.6** | The pack gains the **new file only**; the existing file list is unchanged | the pack is the lane's single gate; adding a file is additive and reversible | a second pack (the house rule is one pack per lane) |

---

## 3. Tasks

| ID | Task | Outcome (testable) | Touchpoints (files) | Depends on | Risks carried | AC |
|---|---|---|---|---|---|---|
| **T3.1** | Extend the **fixture** with a hermetic map control: a `designations` parameter (a `dict` or `None`) on the flow rig that **sets** `ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS` to an exact JSON string, or **scrubs** it when `None`; the default is **scrubbed**. **(B3(ii))** the fixture must also build a **channel-shaped window row** for the A-4/M-13 legs: `build_discord_meta(is_dm=False, is_group_dm=False, guild_id=…, channel_type="text")` plus the **mapping fake** (or the new non-DM derivation path) — a designated-channel leg needs `is_dm=False` + guild/channel + nested `user_id` **and** a mapping-row fake or the new non-DM derivation, because `build_discord_meta` defaults to `is_dm=True, channel_type='dm'` with **no** `external_user_id`/mapping fake | a cell can never inherit an ambient map; the default rig scrubs the var; a channel-shaped leg is writable | `tests/helpers/prompt_assent_tool_fixture.py`, `tests/helpers/prompt_assent_flow.py` | — | R-9 | AC1 |
| **T3.2** | Write the **mint cells** — M-6 (designated + pinned ⇒ MINT, `mint_is_dm=0`), M-7 (designated + non-pinned ⇒ `approver-not-pinned`, no row), M-8 (absent user ⇒ `extraction-incomplete` — reachable **only** because the absent-identity leg precedes the pinned comparison), M-9 (invalid map ⇒ `designation-config-invalid`), M-10 (invalid map + DM ⇒ MINT), M-11 (group DM + invalid map ⇒ `mint-requires-private-channel`), **M-15 (group DM whose channel id IS in a VALID map, by the pinned approver ⇒ REFUSE — with an IN-FILE counter-mutation: reorder the legs ⇒ M-15 reddens)**, M-12 (thread inside a designated channel ⇒ refused), M-13 (thread designated by its own id ⇒ MINT), M-14 (cross-pin: pinned for another channel ⇒ refused), **M-16 (a bot id pinned ⇒ `approver-not-pinned`, room mint-dead)**, **M-17a (a 1:1 DM whose channel id is listed in a valid map ⇒ MINT, `mint_is_dm=1` — the DM path never reads the map)**, **M-17b (a group-DM channel id listed in a valid map ⇒ REFUSE `mint-requires-private-channel` at leg 2)**, **M-18 (a guild turn whose meta lacks the `is_group_dm` key ⇒ REFUSE `mint-requires-private-channel`)**, **M-19 (designated channel, nested `user_id` absent ⇒ `extraction-incomplete`)** | one named test per cell, each asserting the **code**, the **absence** of a token row, and the **ledger** shape; **M-15's counter-mutation is demonstrated to REDDEN its cell** | `tests/unit/test_prompt_assent_designated_channel.py` **(NEW)** | P2 | R-2 | AC2 |
| **T3.3** | Write the **approve cells** — A-4 (designated ⇒ the same channel, the pinned approver ⇒ PASS, the token is consumed, the audit row is written), A-5 (a different human ⇒ `token-approver-mismatch`), A-6 (a non-designated channel ⇒ `token-channel-mismatch`), A-7 (a group DM ⇒ `token-channel-mismatch`), A-8 (a DM ⇒ `token-channel-mismatch`), A-9 (re-designation ⇒ `approver-not-pinned`), A-10 (invalid map ⇒ `designation-config-invalid`), A-11 (invalid map + DM ⇒ PASS), **A-12 (withdrawn designation ⇒ `token-channel-mismatch`, KEEP BURN — the LEADER-RULED asymmetry)** | one named test per cell; the A-4 cell asserts the **full provenance** (item, user, channel, message id, render hash, relay agent); **burn assertions are extended to A-5…A-8 and A-12** (counter++, no revoke is wrong — the revoke happens at threshold 5, so the cells assert the counter increments and the token stays live below threshold); **no-burn assertions for A-9, A-10 and A-11** | `tests/unit/test_prompt_assent_designated_channel.py` | P2 | R-2 | AC3 |
| **T3.4** | Write the **map-unset equivalence** cell: with the map unset, every pre-existing cell produces the identical code and the identical ledger shape as under v0.29.18 | the cell asserts the equality of the two shapes, not the absence of a difference | `tests/unit/test_prompt_assent_designated_channel.py` | T3.1 | — | AC4 |
| **T3.5** | Assert the **budget rule** inside the new-code cells: after an `approver-not-pinned` and after a `designation-config-invalid` refusal, the token's `attempts` is unchanged, the token is **not** revoked, and the relay's hourly budget counter is unchanged | the cells fail if a `_record_mismatch` call creeps in | `tests/unit/test_prompt_assent_designated_channel.py` | T3.2 | R-3 | AC5 |
| **T3.6** | Write the **named counter-mutations** for the four structural pins from Phase 2 (T2.7) and for the new cells — each demonstrated to **REDEN** its cell (the revert of the guard, run in isolation) | each counter-mutation is recorded with the cell it reddens; a green-under-revert guard is a **failure** of the suite | `tests/unit/test_prompt_assent_designated_channel.py` | T3.2, T3.3 | — | AC6 |
| **T3.7** | **Confirm the existing DM pins are green and unmodified** — **SIX** pin files: `test_prompt_assent_mint_gate.py`, `test_prompt_assent_user_binding.py`, `test_prompt_assent_group_dm_discriminator.py`, `test_prompt_assent_kill_switch.py`, `test_prompt_assent_lane.py`, `test_prompt_assent_fe_unchanged.py` — and prove the **unmodified** claim mechanically (`git diff --stat` over those files is empty). **None of the six is edited; the new cells live in the NEW file** | the files are byte-unchanged and green; the claim is a diff, not a promise | (tests only — read) | P2 | R-2 | AC7 |
| **T3.8** | **The RUNTIME resolver-raises pin** (BLOCKING — architect §2.5 (i), the **primary** proof of `INVARIANT-DM`): **monkeypatch the resolver to RAISE**, then run three calls — a **DM mint**, a **DM approve**, and a **lane-disabled** call — and assert **no raise** and **identical outcomes** to the unpatched run | the DM path cannot reach the resolver — proved by **execution**, not by a source-shape reading (a source-shape pin alone is prose-satisfiable, `tests/unit/test_prompt_assent_pins.py:22-26`) | `tests/unit/test_prompt_assent_designated_channel.py` | P2 | R-2 | AC8 |
| **T3.9** | **The map-digest tests** (LEADER RULING 2, control (b) — **D-15**): the digest is **stable** for a given map, **changes** when the map changes, and **never** contains a channel id, a user id or the raw value — asserted for the mint INFO line and the approve/reject audit payload | the digest behaves as a content-free fingerprint and the ledger's decision provenance carries it | `tests/unit/test_prompt_assent_designated_channel.py` | P2 | R-1 | AC9 |
| **T3.10** | Add the **new file to the pack** (`test/packs/prompt_assent_phase3_unit_test.sh`) — the file list gains exactly one line; the pack's env-hygiene note gains the **per-variable** scrub `-u ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS` (**never a glob** — the house `env -u POSTGRES_*` lesson) | the pack runs the new file and stays within its 300 s internal timeout; an ambient operator map cannot leak into the house gate | `test/packs/prompt_assent_phase3_unit_test.sh` | T3.2, T3.3 | R-9 | AC10 |
| **T3.11** | **Refresh the pack's registry row** in `.agents/tester/PACKS.md` — the file list, the real cell count and the **pass record** (the wall time and the attempt count). **BOTH** registered prompt-assent rows are named: the phase-3 pack row (which gains the file) and the matrix-pack row (**`prompt_assent_matrix_test.sh` is explicitly OUT OF SCOPE and must NOT gain it**) | the row matches reality; no "expected" numbers; the out-of-scope pack is not touched | `.agents/tester/PACKS.md` | T3.10 | — | AC11 |
| **T3.12** | **Phase-level gate (owns AC12):** the whole lane's suite is green on the **merged** tree — the Phase-1 parser file, the Phase-2 census file and this phase's file together | one green run over all three of this lane's test files | (tests only) | T3.1-T3.11 | — | AC12 |

---

## 4. Acceptance criteria

| ID | Criterion |
|----|-----------|
| **AC1** | Every test cell either **sets** the designation env var to an exact JSON string or **scrubs** it; the default rig scrubs it — no cell can inherit an ambient map |
| **AC2** | Every **mint** cell of the matrix (M-1 … **M-17a**, **M-17b**, incl. **M-15**, **M-16**, **M-18**, **M-19**) has a named test asserting the code, the token-row absence/presence and the ledger shape. **Final count: 20 mint + 12 approve = 32 cells** — derivation: the council's pre-split 17 mint + 11 approve; S3's M-17a/17b split (+1 mint), S7's M-18 (+1 mint) and B3's M-19 (+1 mint) = 20 mint; NB1's A-12 (+1 approve) = 12 approve. **M-2** (a 1:1 DM by *any* human) is the **conjunction of M-1 and A-2** and is covered by them — no separate cell is required; **A-1…A-3** are covered by the existing pins. **FAMILY-TEST RULE: M-2 is family-tested by M-1 + A-2 (no separate cell); every OTHER cell gets a named test.** |
| **AC3** | Every **approve** cell of the matrix (A-1 … A-12) has a named test; the A-4 cell asserts the **full provenance chain** |
| **AC4** | The **map-unset equivalence** cell proves the default posture is the v0.29.18 posture (identical code, identical ledger) |
| **AC5** | Every new-code cell asserts **no burn**: `attempts` unchanged, no revoke, no K-9 budget consumption |
| **AC6** | Every new structural pin has a **named counter-mutation** that is demonstrated to REDDEN its cell; a guard that stays green under its own revert **fails** the suite |
| **AC7** | The **six** existing DM-path test files are **byte-unchanged** (`git diff --stat` is empty over them) and green — **none is edited; the new cells live in the new file** |
| **AC8** | **The runtime resolver-raises pin is green**: with the resolver monkeypatched to raise, a DM mint, a DM approve and a lane-disabled call all complete with **no raise** and **identical outcomes** — `INVARIANT-DM` proved by execution |
| **AC9** | The **map digest** is stable for a given map, changes when the map changes, and never contains a channel id, a user id or the raw value — in both the mint INFO line and the approve/reject audit payload |
| **AC10** | The pack runs the new file, passes, and stays inside its 300 s internal timeout; the file list gains **exactly one** entry; the pack's env-hygiene note carries the **per-variable** `-u ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS` scrub (**never a glob**) |
| **AC11** | The `.agents/tester/PACKS.md` rows record the **actual** cell count, the actual wall time and the actual attempt count; **both** registered prompt-assent rows are named and the **matrix pack is explicitly out of scope** |
| **AC12** | The whole lane's suite is green on the **merged** tree — the Phase-1 parser file, the Phase-2 census file and this phase's file together |

---

## 5. Notes for the implementer

- **Do not edit the existing DM-path test files.** The "unchanged" claim is the deliverable
  (matrix §4 PR-2). If a cell seems to need a new assertion, put it in the **new** file.
- **`build_discord_meta` is already fully parameterizable**
  (`tests/helpers/prompt_assent_tool_fixture.py:137-156`) — a designated-channel leg is
  `build_discord_meta(guild_id="…", channel_type="text", is_dm=False, channel_id="…",
  user_id="…")` plus a window plus a registered discord source. **The fixture needs no new
  vocabulary** — only the map control (T3.1).
- **A thread cell** needs `channel_type="thread"` and a distinct `channel_id`; the fixture's
  `build_discord_meta` does **not** currently carry `parent_channel_id` — add that parameter if
  the thread cells need it (it is part of T3.1's fixture work).
- **The known freshness flake** (`test_prompt_assent_freshness.py`'s positive leg, ~13-20 %, a
  wall-clock `now_iso()` truncation race) **ships with this lane** — it is pre-existing and its
  durable fix is deferred (the parent's **`../prompt-assent/ceremony-notes.md:256-259`** —
  re-checked 2026-11-11; this set's earlier `:250-253` cite was **wrong**, see `decisions.md`
  **OD-3**). Expect a spurious red; do
  **not** "fix" it inside this lane — record the retry in the pack row.
