# Refusal-Cell Matrix — prompt-assent Phase 1.5 (designated channels)

**Commission:** `prompt-assent` Phase 1.5 — designated channels (pinned-approver model).
**Doctrine:** [`doctrine.md`](doctrine.md) — *"this is channel, i often use channel to split tasks category in my mind, we consider channel is private/dm for now"* (carried verbatim).
**Binding algorithms:** [`decisions.md`](decisions.md) **D-6** (the binding point), **D-8** (the
two new codes), **D-9** (threads).

| Field | Value |
|---|---|
| Date | 2026-11-11 (UTC) |
| Worktree | `/home/nea/ensemble-src-wt-assent-p15-channels` (branch `feature/assent-designated-channels`) |
| Companion | [`plan-overview.md`](plan-overview.md) · [`decisions.md`](decisions.md) · [`risks.md`](risks.md) |

**Every cell below is binding.** The architect may amend it; the implementer may not.

---

## 1. The two binding algorithms

### 1.1 — MINT side (gate leg **M-4c**, `daemon/tools/prompt_assent.py:1708-1716`)

> **THE TYPED-OUTCOME CONTRACT (B2 — owned by Phase 1 T1.4; unit matrix by T1.5).**
> `designation_outcome(map, discord_meta, user_id) -> DesignationResult` — a typed result with
> **four refusal variants** (`designation-config-invalid`, `mint-requires-private-channel`,
> `extraction-incomplete`, `approver-not-pinned`) **plus a PASS variant**. The function owns the
> leg ordering and returns the typed result; **the gates receive only the typed result** and never
> re-implement the ordering. **T1.4 owns the contract; T1.5 owns its unit matrix.**
>
> **FUNCTION INTERNALS (inside `designation_outcome`) — NOT gate-level logic.** The pseudocode
> below is the function's internals; the gates receive only the typed result.
>
> **Two hand-offs (B2(c)):** (i) **P2 resolver ↔ P1 function** — the resolver (T2.1) calls
> `parse_designations` and passes the **parsed/typed-invalid map object**; the function **never**
> re-reads `os.environ`. (ii) **repository → tool-layer code-string boundary** — the codes are
> stringified at the gate call sites (T2.2 mint, T2.3 approve) from the `DesignationResult`, and
> the envelope is formed by `prompt_assent._mint_refusal` / the service predicate.
>
> **ARCHITECT §4.4 — ONE classification function.** **Two implementations of this ordering are
> forbidden** (precedent: `classify_user_origin`, `daemon/tools/upgrade_journal.py:4116-4166`).

```
proven_1to1   := (is_dm is True) and (is_group_dm is False)        # identity checks only

if proven_1to1:                                    → MINT   (the map is NEVER read)
if is_group_dm is True:                            → REFUSE mint-requires-private-channel
if is_dm is not False:                             → REFUSE mint-requires-private-channel   # absent / non-bool → fail closed
# is_dm is False ⇒ a GUILD channel — the only shape a designation can apply to
map := resolve_designations()
if map is INVALID:                                 → REFUSE designation-config-invalid
if context.channel_id not in map:                  → REFUSE mint-requires-private-channel
if not context.user_id:                            → REFUSE extraction-incomplete    # ← the ABSENT-IDENTITY LEG
if context.user_id != map[context.channel_id]:     → REFUSE approver-not-pinned
                                                    → MINT
```

**Ordering consequences (each one is load-bearing).**

- The **1:1-DM leg is first and short-circuits** ⇒ the map is never parsed, never validated and
  never consulted on the DM path. This is the strongest available proof that the DM path is
  unchanged (**BLESSED-3**) — enforced by **INVARIANT-DM** (§4 PR-1), not by prose.
- The **group-DM leg precedes the map** ⇒ the existing group-DM pin
  (`tests/unit/test_prompt_assent_mint_gate.py:84-90`) stays green **whatever the map contains**
  — and the **unpinnable** shape (a group DM whose channel id IS in a valid map) is pinned by
  **M-15**.
- The **absent / non-bool discriminator leg precedes the map** ⇒ the existing fail-closed pin
  (`tests/unit/test_prompt_assent_mint_gate.py:92-100`) stays green whatever the map contains.
- The **absent-identity leg precedes the pinned comparison** ⇒ a designated-channel turn with an
  **absent** user id refuses `extraction-incomplete` (the DM path's own semantics —
  `tests/unit/test_prompt_assent_lane.py:587`; approve leg 4e at
  `daemon/tools/prompt_assent.py:1998-2006`), **not** `approver-not-pinned`. That code means
  *"a known human who is not pinned"*, never *"no human at all"*. Without this leg **M-8 is
  unreachable** (the architect's §6.3).
- Only a **guild channel** (`is_dm is False`) can ever be designated.
- **The designated branch is gated on `discord_meta.get("is_group_dm") is False`** (an identity
  check on the raw meta dict — **not** on the retired `text`-type-turn rule). Semantics: the **message path**
  ALWAYS carries `is_group_dm` (False for every guild shape, including threads ⇒ **M-13 stays
  MINT**); the **interaction path** builds `discord_meta` **without** the key
  (`daemon/sources/adapters/discord/adapter.py:1227-1254`), so the `.get(...) is False` check
  **fails** and the turn is **never designation-eligible** — it falls through to the existing
  refusal **`mint-requires-private-channel`**. A matrix cell for the **absent-`is_group_dm`-key**
  guild turn ⇒ REFUSE `mint-requires-private-channel` is **added** (and a T1.5 test).

### 1.2 — APPROVE side (gate leg **4f**, `daemon/tools/prompt_assent.py:2018-2026`; service
predicate `daemon/services/prompt_version_service.py:460-476`)

> **The approve leg DOES emit the two new codes** (the architect's §5.2 — the plan's earlier
> "no new approve-side code" wording was false against this section, D-8's own table and the
> A-9/A-10 cells). The service predicate's **designated branch lives inside its `not private`
> arm** (architect §4.4), and it takes the map **and** the approving human's id — the id comes
> from `context.user_id` (`daemon/tools/prompt_assent.py:678`, the single identity parse site
> `:621-633`), **never** from a second read of `discord_meta["user_id"]`.

```
proven_1to1   := (is_dm is True) and (is_group_dm is False)

if proven_1to1:                                    → PASS
if is_group_dm is True:                            → REFUSE token-channel-mismatch
if is_dm is not False:                             → REFUSE token-channel-mismatch
# is_dm is False ⇒ a GUILD channel
map := resolve_designations()
if map is INVALID:                                 → REFUSE designation-config-invalid
if context.channel_id not in map:                  → REFUSE token-channel-mismatch
if not context.user_id:                            → REFUSE extraction-incomplete    # ← the ABSENT-IDENTITY LEG
if context.user_id != map[context.channel_id]:     → REFUSE approver-not-pinned
                                                    → PASS
```

**The approve-side extension only ever ADDS a pass path to the *privacy* leg** — every existing
refusal keeps its code **and** its budget behaviour. The two new codes are the **new** approve-side
outcomes: the operator-fault case (an invalid map) and the **re-designation window** (the
mint-witnessed human is no longer the room's current pinned approver ⇒ A-9).

### 1.3 — The budget rule (the one thing an implementer gets wrong easily)

`_record_mismatch` (`daemon/tools/prompt_assent.py:1174-1209`) is the **durable per-token attempt
counter**; it increments the counter and **revokes the live token at threshold 5**
(`daemon/tools/prompt_assent.py:252`, enforced at `:2028-2036`). The K-9 in-RAM hourly budget
(`daemon/tools/prompt_assent.py:274-326`) is the counter for the unknown-token shape, and it burns
**only** for a code in `MISMATCH_CODES` (`daemon/tools/prompt_assent.py:2107`).

> **RULE (ARCHITECT §6.2 — THE 4f SPLIT).** The 4f site (`daemon/tools/prompt_assent.py:2021`)
> calls `_record_mismatch` **unconditionally today** — *before* the code's class is examined.
> As specified, five approve-side new-code refusals would **revoke the Emperor's live token**,
> which contradicts A-9/A-10 ("Token burn? no") and AC7. **The 4f site MUST split:**
>
> * the **mismatch-class branch** (the existing `token-channel-mismatch`) keeps
>   `_record_mismatch` exactly as today;
> * the **two new codes refuse BEFORE the recording call** — they never reach
>   `_record_mismatch`;
> * the **three genuine mismatch sites** (`:1970`, `:1991`, `:2011`) are **untouched**.
>
> **This edit is owned by Phase 2** (task **T2.5**) — it is a production-code edit and must not
> appear as an unplanned mid-Phase-3 change.
>
> **A-9 and A-10 stay "no burn" ONLY AFTER this split lands.**

**Why the budget must NOT burn on either new code.** The K-9 budget is a **token-guessing** brake
(`daemon/tools/prompt_assent.py:274-326`). A mint-side refusal presents **no token**, so there
is nothing to guess — and burning the budget there would be a **DoS primitive**: a stranger who
simply typed in the designated channel would exhaust the relay instance's hourly budget and lock
out the **legitimate** Emperor with `mismatch-budget-exceeded`. Both new codes are therefore
**outside `MISMATCH_CODES`** and outside the placed-codes set that consumes the budget.

**Stronger fact (architect §6.1 — state it so no future change adds a mint-side budget leg).**
**The mint path never consults the budget at all**: `_mint_refusal`
(`daemon/tools/prompt_assent.py:1657-1666`) bypasses `_mismatch_aware_refusal`
(`:2089`) for **every** code — the K-9 budget is structurally unreachable from the mint path,
not merely unused by the new codes.

---

## 2. The MINT cell matrix

| # | Cell | Context | Map | Expected | Code | Token row? | Ledger effect | Budget burn? | Test |
|---|---|---|---|---|---|---|---|---|---|
| **M-1** | **1:1 DM, the minting human** | `is_dm=True, is_group_dm=False` | any | **MINT** | — | **yes** | `mint_is_dm=1` | — | `test_prompt_assent_mint_gate.py:117-124` (**UNCHANGED**) |
| **M-2** | **1:1 DM, any other human** | `is_dm=True, is_group_dm=False`, different `user_id` | any | **MINT** | — | yes | `mint_is_dm=1`, `mint_user_id` = that human | — | same cell family — the DM path does not bind the approver at mint (the binding is at 4e) |
| **M-3** | **Group DM** | `is_dm=True, is_group_dm=True` | any | **REFUSE** | `mint-requires-private-channel` | **no** | none | **no** | `test_prompt_assent_mint_gate.py:84-90` (**UNCHANGED**) |
| **M-4** | **Absent / non-bool discriminator** | `is_group_dm` popped, or `"false"`, or `1` | any | **REFUSE** | `mint-requires-private-channel` | **no** | none | **no** | `test_prompt_assent_mint_gate.py:92-100` (**UNCHANGED**) + `test_prompt_assent_group_dm_discriminator.py:150-174` (**UNCHANGED**) |
| **M-5** | **Guild channel, NOT designated** | `is_dm=False, guild_id=…, channel_type="text"` | empty / unrelated | **REFUSE** | `mint-requires-private-channel` | **no** | none | **no** | `test_prompt_assent_mint_gate.py:102-115` (**UNCHANGED** — with the default empty map the cell is byte-identical) |
| **M-6** | **Guild channel, designated, by the PINNED APPROVER** | `is_dm=False`, `channel_id` in map, `user_id` == map value | valid | **MINT** | — | **yes** | `mint_is_dm=0`, `mint_channel_id` = the channel, `mint_user_id` = the approver | — | **NEW** `test_prompt_assent_designated_channel.py::TestTheDesignatedChannelMint` |
| **M-7** | **Guild channel, designated, by a NON-pinned human** | `is_dm=False`, `channel_id` in map, `user_id` ≠ map value | valid | **REFUSE** | **`approver-not-pinned`** *(new)* | **no** | none | **no** | **NEW** `…designated_channel.py::TestTheNonPinnedUser` |
| **M-8** | **Guild channel, designated, ABSENT user id** | `is_dm=False`, `user_id` absent/blank | valid | **REFUSE** | `extraction-incomplete` *(existing)* | **no** | none | **no** | **NEW** (the designated-channel variant of the existing extraction cell) — **reachable ONLY because the absent-identity leg precedes the pinned comparison** (§1.1; architect §6.3). Without that leg the cell would return `approver-not-pinned` and would be **wrong** |
| **M-9** | **Guild channel, INVALID map** | `is_dm=False` | malformed JSON / bad id / duplicate key / >64 | **REFUSE** | **`designation-config-invalid`** *(new)* | **no** | none | **no** | **NEW** `…designated_channel.py::TestTheInvalidMap` |
| **M-10** | **1:1 DM with an INVALID map** | `is_dm=True, is_group_dm=False` | malformed | **MINT** | — | **yes** | unchanged | — | **NEW** — *the blast-radius cell*: a broken map never touches the DM path |
| **M-11** | **Group DM with an INVALID map** | `is_dm=True, is_group_dm=True` | malformed | **REFUSE** | `mint-requires-private-channel` *(unchanged)* | **no** | none | **no** | **NEW** — *the ordering cell*: the group-DM leg precedes the map |
| **M-12** | **Thread inside a designated channel** | `is_dm=False, channel_type="thread"`, `parent_channel_id` = the designated channel, `channel_id` = the thread id | valid (parent designated) | **REFUSE** | `mint-requires-private-channel` | **no** | none | **no** | **NEW** — **D-9**: no inheritance |
| **M-13** | **Thread designated by its OWN id** | `is_dm=False, channel_type="thread"`, `channel_id` in map | valid | **MINT** | — | **yes** | `mint_is_dm=0` | — | **NEW** — the operator may name a thread explicitly |
| **M-14** | **Designated channel, but the human is the pinned approver of ANOTHER channel** | `channel_id` = C1 in map (pinned U1); `user_id` = U2 (pinned for C2) | valid | **REFUSE** | **`approver-not-pinned`** | **no** | none | **no** | **NEW** — the *cross-pin* cell: the map is **per channel**, not per human |
| **M-15** | **Group DM whose channel id IS in a VALID map, by the PINNED APPROVER** | `is_dm=True, is_group_dm=True`, `channel_id` in map, `user_id` == the map value | valid | **REFUSE** | `mint-requires-private-channel` | **no** | none | **no** | **NEW (architect §4.3 — NT-3, the unpinned trust boundary)** — with an **in-file counter-mutation**: reorder the legs ⇒ M-15 reddens |
| **M-16** | **A BOT id is pinned for the channel** | `is_dm=False`, `channel_id` in map, the map value is a bot's user id | valid | **REFUSE** | `approver-not-pinned` | **no** | none | **no** | **NEW (NT-8)** — the room is permanently mint-dead with the same code as a stranger; the side-specific detail text carries the distinction. **Dependency: this behaves as mint-dead only under the adapter's DEFAULT bot-message-skip posture** (`daemon/sources/adapters/discord/adapter.py:1041-1042`, `:1219`); if the operator changes the bot-skip config, the bot's turn may arm the window and the refusal still fires at the pinned-approver leg |
| **M-17a** | **A 1:1 DM whose channel id is listed in a VALID map** | `is_dm=True, is_group_dm=False` (the map lists that channel id) | valid | **MINT** (`mint_is_dm=1` — the DM path never reads the map, so the entry is **inert**; **INVARIANT-DM**) | — | **yes** | none | — | **NEW (NT-8)** — the map entry is **silently ignored**; the map-parse log line is the only signal that a DM id was listed |
| **M-17b** | **A group-DM channel id listed in a VALID map** | `is_dm=True, is_group_dm=True` (the map lists that channel id) | valid | **REFUSE** at leg 2 | `mint-requires-private-channel` | **no** | none | **no** | **NEW (NT-8)** — the group-DM leg precedes the map, so the entry is **silently ignored**; the map-parse log line is the only signal |
| **M-18** | **A guild turn whose meta LACKS the `is_group_dm` key** (the interaction path) | `is_dm=False`, `is_group_dm` absent, `channel_id` in map, `user_id` == map value | valid | **REFUSE** (the designated branch is gated on `discord_meta.get("is_group_dm") is False`, which fails when the key is absent) | `mint-requires-private-channel` | **no** | none | **no** | **NEW (S7)** — the `text`-turn rule is retired in favour of this gate; the turn falls through to the existing refusal |
| **M-19** | **A designated channel whose nested `user_id` is ABSENT** (B3(iii) — the negative cell) | `is_dm=False`, `is_group_dm=False`, `channel_id` in map, nested `user_id` absent | valid | **REFUSE** at the absent-identity leg | `extraction-incomplete` | **no** | none | **no** | **NEW (B3(iii))** — the non-DM derivation cannot build an `external_user_id` without the nested user id; the accept path's `extraction-incomplete` is the truthful code |

---

## 3. The APPROVE cell matrix

| # | Cell | Mint was in | Approve is in / by | Expected | Code | Token burn? | Budget burn? | Test |
|---|---|---|---|---|---|---|---|---|
| **A-1** | **1:1 DM ⇒ 1:1 DM, same human** | DM | DM, same `user_id` | **PASS** | — | consumed | — | `test_prompt_assent_user_binding.py:129` (**UNCHANGED**) |
| **A-2** | **1:1 DM ⇒ 1:1 DM, different human** | DM | DM, other `user_id` | **REFUSE** | `token-approver-mismatch` *(unchanged)* | counter++ | **yes** | `test_prompt_assent_user_binding.py:87` (**UNCHANGED**) |
| **A-3** | **1:1 DM ⇒ designated channel** | DM | the designated channel | **REFUSE** | `token-channel-mismatch` *(unchanged, leg 4d)* | counter++ | **yes** | `test_prompt_assent_user_binding.py:52` (**UNCHANGED**) |
| **A-4** | **Designated channel ⇒ the same designated channel, PINNED APPROVER** | designated | the same channel, the pinned approver | **PASS** | — | consumed | — | **NEW** `…designated_channel.py::TestTheDesignatedChannelApprove` |
| **A-5** | **Designated channel ⇒ the same channel, a DIFFERENT human** | designated | the same channel, other `user_id` | **REFUSE** | `token-approver-mismatch` *(unchanged, leg 4e)* | counter++ | **yes** | **NEW** — the 4e leg fires **before** 4f |
| **A-6** | **Designated channel ⇒ a NON-designated guild channel** | designated | another guild channel | **REFUSE** | `token-channel-mismatch` *(unchanged, leg 4d)* | counter++ | **yes** | **NEW** |
| **A-7** | **Designated channel ⇒ a group DM** | designated | a group DM | **REFUSE** | `token-channel-mismatch` *(unchanged, leg **4d** — channel mismatch)* | counter++ | **yes** | **NEW** |
| **A-8** | **Designated channel ⇒ a 1:1 DM** | designated | a DM | **REFUSE** | `token-channel-mismatch` *(unchanged, leg 4d)* | counter++ | **yes** | **NEW** |
| **A-9** | **Re-designation between mint and approve** | designated (pinned U) | the same channel, by **U**, after the operator re-pinned the channel to V | **REFUSE** | **`approver-not-pinned`** *(new, leg 4f)* | **no** — *only after the §1.3 4f split* | **no** — *only after the §1.3 4f split* | **NEW** — *the fail-closed re-designation cell* (`decisions.md` **D-6**) |
| **A-10** | **Approve with an INVALID map, non-DM context** | designated | the designated channel, by the pinned approver, map now broken | **REFUSE** | **`designation-config-invalid`** *(new, leg 4f)* | **no** — *only after the §1.3 4f split* | **no** — *only after the §1.3 4f split* | **NEW** |
| **A-11** | **Approve with an INVALID map, 1:1 DM context** | DM | DM, same human | **PASS** | — | consumed | — | **NEW** — *the blast-radius cell on the approve side* |
| **A-12** | **Withdrawn designation** (the operator re-pins / unpins the channel between mint and approve; the approving human is **no longer pinned**) | designated (pinned U) | the same channel, by U, after the channel was **un-designated** | **REFUSE** | `token-channel-mismatch` *(leg 4f — the channel is no longer an acceptable surface)* | counter++ | **yes** | **NEW (LEADER-RULED: KEEP BURN)** — a user attempting to approve in a formerly-valid room is a **user-side mismatch**; the token **burns** (durable counter + K-9 budget). Asymmetry: **A-10** (invalid map = **operator** fault) → no burn; **A-12** (withdrawn designation = **user**-side mismatch) → burns |

---

## 4. How the DM cells are PROVEN unchanged

The commission requires the DM path's behaviour to be preserved and every DM cell to be re-proven.
The proof is **INVARIANT-DM** plus four legs. (The proofs are numbered **PR-1…PR-4** — the
`P-n` namespace is reserved for the risk register's structural pins.)

### INVARIANT-DM (architect §2.5 — stated verbatim; this is the enforceable guarantee)

> For any turn where `is_private_assent_channel(discord_meta)` is `True`, the process MUST NOT
> read `os.environ["ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS"]`, MUST NOT call the resolver or
> `parse_designations`, and the outcome (code, envelope, token row, ledger shape, budget counter,
> attempt counter) MUST equal the v0.29.18 outcome.

| # | Proof | Mechanism |
|---|---|---|
| **PR-1** | **The runtime resolver-raises pin** (the primary proof) | **Monkeypatch the resolver to RAISE**, then run three calls — a **DM mint**, a **DM approve**, and a **lane-disabled** call — and assert **no raise** and **identical outcomes** to the unpatched run. This is the **non-vacuous** form of the guarantee: a source-shape pin alone is prose-satisfiable (the house's own non-vacuity doctrine, `tests/unit/test_prompt_assent_pins.py:22-26`) |
| **PR-2** | **The source-shape pin** | The resolver symbol appears in **no DM-reachable function** — i.e. the DM path lives in a function the designated path cannot reach. **Medium cost, recommended as the shape, not required as the proof** (architect §2.5 (iv)) |
| **PR-3** | **The map-unset equivalence cell** | With the map **unset** (the default), every pre-existing cell produces the **identical code, envelope, ledger shape and budget behaviour** as under v0.29.18. The default posture of the staged release **is** the v0.29.18 posture |
| **PR-4** | **The code-shape requirement of architect §4** | The designated branch lives **inside** the `not private` arm of the service predicate and **after** the DM short-circuit in the mint gate; **ONE** classification function owns the whole leg ordering and both gates call it. The ordering is a **code-shape** property, not an order instruction — the M-15 counter-mutation is its tripwire |

### The legacy "byte-identical" claims — narrowed to what is enforceable

**What is honestly byte-identical** (architect §2.3): the **FE surface** (routes + repo — the
proof is **method-scoped**: `tests/unit/test_prompt_assent_fe_unchanged.py`, the approve/reject
def blocks); the **`is_private_assent_channel` function** (`mint_context.py:29-50` untouched — the
file gains new siblings); the **DM predicate legs** (the same identity checks); the **DM cells'
code / ledger / budget** (the ledger write at `:1787-1791` is untouched, `mint_is_dm=1`; a DM never
reaches a `MISMATCH_CODES` code at `:241-247`); and the **DM cells' envelope** — *which stays
byte-identical only because the shared `mint-requires-private-channel` detail string is NOT
edited* (architect §2.4: the new wording lives on the **two new codes' messages only**; the
existing detail string at `daemon/tools/prompt_assent.py:1712-1714` stays byte-identical).

**What is provably changed**: the bytes of `daemon/tools/prompt_assent.py`,
`daemon/services/prompt_version_service.py` and `daemon/repositories/prompt_assent/mint_context.py`
— both gate sites must be edited (architect §2.2 E2/E3/E4).

**The claim "the DM path is BYTE-IDENTICAL" is therefore withdrawn in favour of: *the DM path's
behaviour is unchanged, and its envelope stays byte-identical only because T2.8 confines the new
detail text to the two new codes.*** The ledger rows (BLESSED-3, D-7) are amended to this scope
character-for-character (architect §2.6).

---

## 5. The code inventory after this lane

| Code | Class | Emitted by | Burn? | New? |
|---|---|---|---|---|
| `mint-requires-private-channel` | mint refusal | M-4c (extended) | no | **existing** (unchanged string — **and unchanged detail string**, architect §2.4) |
| **`approver-not-pinned`** | mint **and** approve refusal | M-4c (leg "user ≠ pinned") / 4f — cells **M-7**, **M-14**, **M-16**, **A-9** | **no** | **NEW** |
| **`designation-config-invalid`** | mint **and** approve refusal | M-4c (leg "map INVALID") / 4f — cells **M-9**, **A-10** | **no** | **NEW** |
| `token-channel-mismatch` | mismatch-class | 4d / 4f | yes | existing |
| `token-approver-mismatch` | mismatch-class | 4e | yes | existing |
| `extraction-incomplete` | extraction refusal | M-4c's identity check / the absent-identity leg | no | existing |
| … the other **23** codes | — | unchanged | — | existing |

**The taxonomy grows by exactly two codes** (`decisions.md` **D-8**): **27 → 29** live codes (the
count is a **test-pinned** count, not a ceremony ordinal — architect §9.5). The census pin
`tests/unit/tools/test_prompt_assent_registration.py:73` (`ARCHITECT_REFUSAL_TAXONOMY`) gains
exactly these two entries and is updated **in Phase 2, together with the gates that emit them**
(an emitted code outside the taxonomy would break the refusal envelope's code sanitisation at
`daemon/tools/prompt_assent.py:2060`).

**The placed sets (architect §6.4).** The census's `EXPECTED_PLACED_CODES` — the parent's eight —
**stays at 8**. The two new codes ship as a **NEW** placed set,
`EXPECTED_PHASE1_5_PLACED_CODES == {approver-not-pinned, designation-config-invalid}`, with its own
**equality** pin, a **subset-of-taxonomy** pin, and a **referenced-in-module loop** mirroring the
parent's. `MISMATCH_CODES` gets an **equality** pin (it is currently pinned only by membership,
`tests/unit/test_prompt_assent_tool_refusals.py:105-119`) with a counter-mutation.

**Naming note.** Both codes are **path-neutral** — `approver-not-pinned` carries the same semantic
on both paths ("this human is not the pinned approver of this room"), so one code serves both.
The **side ambiguity is resolved by side-specific detail text** (architect §6.4): mint — *"this
human is not the pinned approver of this room"*; approve — *"the mint-witnessed human is no longer
this room's current pinned approver"*. The INFO line already carries `action=mint|approve`
(`daemon/tools/prompt_assent.py:508-543`), so the log disambiguates for free.
