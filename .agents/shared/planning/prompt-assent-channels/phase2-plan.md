# Phase 2 — The gate binding + the taxonomy census

**Commission:** `prompt-assent` Phase 1.5 — designated channels (pinned-approver model).
**Doctrine:** [`doctrine.md`](doctrine.md) — *"this is channel, i often use channel to split tasks category in my mind, we consider channel is private/dm for now"* (carried verbatim).
**Binding decisions:** [`decisions.md`](decisions.md) **D-5** (no schema change), **D-6** (the
binding point), **D-7** (kill-switch interaction), **D-8** (the two new codes).
**Binding matrix:** [`refusal-cell-matrix.md`](refusal-cell-matrix.md) §1 (the two algorithms),
§1.3 (the budget rule), §5 (the code inventory).

> **Context owned by this phase.** The **gate sites** and the **refusal vocabulary**. The census
> pin travels with the gates — a code that is emitted but not in the taxonomy breaks the refusal
> envelope's sanitisation (`daemon/tools/prompt_assent.py:2060`) and the census
> (`tests/unit/tools/test_prompt_assent_registration.py:300`).

---

## 1. Objective

Wire the designated-channel surface into the **two** gate sites — the mint gate (leg **M-4c**) and
the approve gate (leg **4f**) — add **exactly two** typed refusal codes, and keep every existing
behaviour, code and budget rule **unchanged** except for the one documented pass path each gate
gains.

> **SCOPE FENCE (B5): NO live promotion, NO arming, NO live-install mutation in this lane —
> v0.29.20 staging only; the ceremony is a separate, post-lane Emperor act.**

**Done means:** the mint gate accepts a designated channel **only** for the pinned approver; the
approve gate accepts it **only** under the same rule; the DM path is untouched in behaviour; the
taxonomy census is exact; and the two new codes never burn the budget.

---

## 2. Decisions taken in this phase

| ID | Decision | Rationale (cite) | Rejected alternatives |
|---|---|---|---|
| **D-2.1** | The **env knob** is read **at call time** by a **cached-lazy resolver** beside `_resolve_knobs`: the resolver reads `os.environ` **directly** (never via a pydantic-settings dotenv source), parses **on first non-DM use**, and **memoises per call**; a **fresh call re-resolves** | the call-time read is the lane's established pattern (`daemon/tools/prompt_assent.py:404`, `:466`); *call-time* and *restart-bound* are **both true** (architect §3.7 — `os.environ` changes only at process start). **The direct read is a pin**: `PromptAssentConfig` is instantiated per call (`:431`), so a dotenv source would **hot-reload the map without a restart** and silently break the restart-bound story | a boot-time snapshot (a stale map); a **dotenv source** (silent hot-reload — breaks the restart-bound story); an eager resolver (the DM path would touch the map) |
| **D-2.2** | The map is resolved **lazily** — only when a gate reaches a non-DM context | the DM path then **never parses** the map, which is the structural proof that the DM path is unchanged (matrix §4 PR-1) | resolving eagerly (the DM path would then depend on the map's validity) |
| **D-2.3** | The mint gate's refusal `mint-requires-private-channel` **keeps its string AND its detail text UNCHANGED** (architect §2.4) — the designated-channel wording lives on the two **NEW** codes' messages only | keeps the existing group-DM and guild-channel pins green and unmodified (matrix §4 PR-2); a taxonomy growth of three is not worth a diagnostic nicety | a new `mint-requires-designated-channel` code (breaks two existing pins for no behavioural gain) |
| **D-2.4** | The two new codes are **`approver-not-pinned`** and **`designation-config-invalid`**, and both sit **outside** `MISMATCH_CODES` | a mint-side refusal presents **no token**; burning the per-instance hourly budget there would let a stranger lock the Emperor out of his own lane (matrix §1.3) | making them mismatch-class (a DoS primitive); adding a third code |
| **D-2.5** | The approve-side predicate gains a **parameter** (the map), not a new service method | one rule, one owner (**phase1-plan.md** D-1.10); the service stays a pure function of its arguments | a second service method (drift); the service reading the environment (the service must not read env) |
| **D-2.6** | The **re-designation window** refuses (the approving human must be the channel's **current** pinned approver) | fail-closed: the operator's live statement of who may authorise in a room is the authority | accepting the token's recorded `mint_user_id` (would let a withdrawn designation keep working for the TTL) |
| **D-2.7** | **The 4f site SPLITS** (architect §6.2): the **mismatch-class branch** keeps `_record_mismatch` exactly as today; the **two new codes refuse BEFORE the recording call**; the **three genuine mismatch sites** (`:1970`, `:1991`, `:2011`) are untouched | the 4f site records **unconditionally today** (`daemon/tools/prompt_assent.py:2021`) — before the code's class is examined — so five approve-side new-code refusals would **revoke the Emperor's live token** (threshold 5, `:252`; enforced at `:2028-2036`), contradicting A-9/A-10 and AC7. The durable counter is a **token-guessing** brake (`daemon/tools/prompt_assent.py:1174-1209`); it must not become a DoS lever | recording the counter on the new codes (a DoS lever **and** a token-revocation bug); removing the counter from the existing sites (weakens the guessing brake) |
| **D-2.8** | The **input schema is unchanged** — the tool carries **no** channel id, no user id, no map reference | the anti-forgery analogue of the repo's R3 rule; pinned by `tests/unit/test_prompt_assent_user_binding.py:144,:172` | adding a `channel_id` argument (a client-supplied trust surface — the exact forgery the parent lane refuses) |

---

## 3. Tasks

| ID | Task | Outcome (testable) | Touchpoints (files) | Depends on | Risks carried | AC |
|---|---|---|---|---|---|---|
| **T2.1** | Add the **call-time resolver** beside `_resolve_knobs`: it reads the env var through the Phase-1 constant, calls `parse_designations`, and returns the **typed outcome** (a valid map or an invalid one) — plus a **WARNING log naming every offending entry by key** (never by value) | a valid map resolves to a `DesignationMap`; a malformed map resolves to an invalid outcome **and** logs one WARNING per offending entry; an **unset** var resolves to a valid **empty** map | `daemon/tools/prompt_assent.py` | P1 (T1.1-T1.3) | R-4 | AC1, AC2 |
| **T2.2** | Extend the **mint gate (leg M-4c)**: keep the **DM short-circuit** (a proven 1:1 DM ⇒ MINT, *without reading the map*) and call **Phase 1's ONE classification function** inside the `not private` arm for every other shape — the matrix's §1.1 algorithm, in its **exact order** (group-DM ⇒ absent-discriminator ⇒ map ⇒ INVALID / not-designated / **absent-identity** / wrong-human / MINT) | each mint cell produces its matrix code; the DM cell never touches the resolver; **no second implementation of the leg ordering** | `daemon/tools/prompt_assent.py` | T2.1, P1 (T1.4) | R-2 | AC3, AC4, AC18 |
| **T2.3** | Extend the **approve gate (leg 4f)**: the service predicate `assent_channel_not_private_code` gains the map parameter and the approving human's id (**from `context.user_id`** — `daemon/tools/prompt_assent.py:678`, the single identity parse site `:621-633`; **never** a second read of `discord_meta["user_id"]`); its **designated branch lives inside its `not private` arm** and calls **Phase 1's ONE classification function** | each approve cell produces its matrix code; **no** existing approve code changes | `daemon/services/prompt_version_service.py`, `daemon/tools/prompt_assent.py` | T2.1, P1 (T1.4) | R-2 | AC5, AC18 |
| **T2.4** | Add the **two refusal codes** to `REFUSAL_TAXONOMY` and add them to the **placed-codes** set (a new `PHASE1_5_PLACED_CODES` constant — the parent's `PHASE2_PLACED_CODES` keeps its own eight and its meaning) | the taxonomy contains exactly the two new codes in addition to the 27; the placed set is a subset of the taxonomy and **disjoint** from `MISMATCH_CODES` | `daemon/tools/prompt_assent.py` | — | R-8 | AC6 |
| **T2.5** | **THE 4f SPLIT (BLOCKING — architect §6.2).** The 4f site (`daemon/tools/prompt_assent.py:2018-2026`) calls `_record_mismatch` **unconditionally today** (`:2021`) — *before* the code's class is examined — so five approve-side new-code refusals would **revoke the Emperor's live token** (threshold 5, `:252`, enforced at `:2028-2036`). **Split the site**: the **mismatch-class branch** keeps `_record_mismatch` exactly as today; the **two new codes refuse BEFORE the recording call**; the **three genuine mismatch sites** (`:1970`, `:1991`, `:2011`) are **untouched** | a new-code refusal leaves the token's `attempts` unchanged, does **not** revoke the token and does **not** consume the K-9 budget; the three genuine mismatch sites still record | `daemon/tools/prompt_assent.py` | T2.2, T2.3 | R-3 | AC7 |
| **T2.6** | Update the **taxonomy census pin** — `ARCHITECT_REFUSAL_TAXONOMY` gains exactly the two new codes (27 → **29**); **re-word the census docstring** from *"the COMPLETE architect §3 refusal taxonomy (verbatim)"* to *"the architect §3 refusal taxonomy **as amended by the prompt-assent Phase-1.5 lane** (+2 codes)"* (**D-13**); add the **ari-EXACTLY category pin** (blocker #8): the registration/census test asserts `prompt_assent` remains **ari-EXACTLY** (privileged category sole-holder + `agents/ari/meta.json` allow entry + the in-body `_not_ari` gate — the file is edited ~6×; the allowlist must not drift); add the **placed-set pins**: `EXPECTED_PHASE1_5_PLACED_CODES == {approver-not-pinned, designation-config-invalid}` (equality), the subset pin (`<= REFUSAL_TAXONOMY`), and a **referenced-in-module loop** mirroring the parent's | `REFUSAL_TAXONOMY == ARCHITECT_REFUSAL_TAXONOMY` stays green; the parent's `EXPECTED_PLACED_CODES` **stays at 8**; the new placed set is pinned by equality | `tests/unit/tools/test_prompt_assent_registration.py` | T2.4 | — | AC6 |
| **T2.7** | Add the **structural pins** in the census file: (i) the resolver symbol appears in **no DM-reachable function** (the source-shape leg of `INVARIANT-DM`); (ii) the tool's input schema still carries **no** identity field; (iii) **`MISMATCH_CODES` is pinned by EXACT EQUALITY** (it is currently pinned only by membership, `tests/unit/test_prompt_assent_tool_refusals.py:105-119`) and the two new codes are **not** in it; (iv) the env-name literal is **not** duplicated **in `daemon/`** (cross-references **P-1** in `risks.md` — T1.6's single-home pin owns the property; this pin asserts it from the test side) | each pin reddens under its own counter-mutation (Phase 3 proves it) | `tests/unit/tools/test_prompt_assent_registration.py` | T2.2, T2.4 | R-7 | AC8, AC9 |
| **T2.8** | Add the **refusal detail strings for the TWO NEW CODES ONLY** — **side-specific** (mint: *"this human is not the pinned approver of this room"*; approve: *"the mint-witnessed human is no longer this room's current pinned approver"*), **content-free** (no persona content, no diff content, no channel name). **The existing `mint-requires-private-channel` detail string (`daemon/tools/prompt_assent.py:1712-1714`) stays BYTE-IDENTICAL** — the architect's §2.4 ruling: the envelope claim survives at zero cost | the new codes' text is actionable and side-specific; the shared detail string is untouched (the DM cells' envelope stays byte-identical) | `daemon/tools/prompt_assent.py` | T2.2, T2.4 | R-6 | AC10 |
| **T2.9** | **Verify the DM path is untouched in behaviour**: run the existing DM-path pins (**with the map var scrubbed** — `env -u ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS`, per-variable never a glob, so the ambient operator map cannot leak into the DM-path assertions before T3.1's rig scrub runs) — `test_prompt_assent_mint_gate.py`, `test_prompt_assent_user_binding.py`, `test_prompt_assent_group_dm_discriminator.py`, `test_prompt_assent_kill_switch.py`, `test_prompt_assent_lane.py`, `test_prompt_assent_fe_unchanged.py`, `test_prompt_assent_tool_refusals.py`, `test_prompt_assent_audit_payload.py` — with **no** test edit | all green, unmodified | (tests only — read) | T2.2, T2.3 | R-2 | AC11 |
| **T2.10** | **FIRST-RESOLUTION + ON-DIGEST-CHANGE logging inside the cached-lazy resolver** (LEADER RULING C / **D-20**): log the effective map (or its digest) when **first successfully resolved** in a process AND **whenever the effective digest changes between resolutions**. **NO boot hook**, **NO `daemon/api.py` edit**; **PR-1 raise-on-invalid UNTOUCHED** (the log fires only on successful resolution). The line names the **entry count** + a **content-free fingerprint** (first **12** hex — one length, everywhere) — never a value, never a channel id | the effective map's shape is visible on first resolution and on every digest change; the line contains no channel id, no user id and no raw value | `daemon/tools/prompt_assent.py` | T2.1 | R-1 | AC15 |
| **T2.11** | **The effective map digest in the mint provenance** (LEADER RULING 2, control (b) — **D-15**): the mint's **content-free INFO line** (`daemon/tools/prompt_assent.py:508-543`) gains a **dedicated `map_digest=<12 hex>` field** (the per-mint provenance entry) — **this field BYPASSES the flags renderer entirely** (the `'1'/'0'` coercion at `daemon/tools/prompt_assent.py:524-526` is **UNTOUCHED**; the digest cannot ride it — architect blocker #4). **No schema change** (the token table gains **no column** ⇒ no 129th migration, **D-5**) | Every **CHANNEL** mint records `map_digest` (12-hex) in the mint INFO line; **DM mints record NO map_digest** — the DM path never reads `ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS` and never calls the resolver (**INVARIANT-DM carve-out, recorded**). | `daemon/tools/prompt_assent.py` | T2.1 | R-1 | AC13 |
| **T2.12** | **The effective map digest in the DECISION provenance**: the **approve/reject audit payload** (`prompt_audit_events.payload`, JSON — additive, **no migration**) carries `map_digest` | the decision's ledger provenance names the map that was in force | `daemon/repositories/prompt_version/repository.py` (`_assent_audit_provenance`, `:1813-1841` — **F-15**, ADDITIVE-ONLY) | T2.11 | R-1 | AC14 |
| **T2.13** | **Phase-level gate (owns AC12):** prove the phase's **diff surface** — `git diff` shows no change to `daemon/repositories/prompt_assent/models.py`, `.../repository.py`, `daemon/migrations/versions/**`, `daemon/api.py`, `daemon/sources/adapters/discord/adapter.py`, `daemon/tools/upgrade_journal.py`, `daemon/manager.py`, `daemon/config.py`, `daemon/repositories/prompt_assent/__init__.py` | the diff surface is exactly the five files this phase owns (F-3, F-4, F-14, F-15, + this phase's census pin file) — **the sole exception is F-15** (`repository.py:1813-1841`, the audit-payload assembly): the `map_digest` field is **ADDITIVE-ONLY** (one JSON key; D-5's no-schema-change unaffected) | (git) | T2.1-T2.12 | — | AC12 |
| **T2.15** | **APPROVER-ORDERED (B3(i)):** extend `_derive_external_user_id` (`daemon/tools/prompt_assent.py:636-671`) with a **non-DM branch** deriving from the daemon-derived `metadata["discord"]["user_id"]` — canonical form **`channel:<guild_id>:<channel_id>:<user_id>`** — declared **AUDIT-ONLY** (**D-19**: a pinned no-in-tree-consumer guarantee — `_parse_external_user_id` is NEVER applied to it; operational identity remains `mint_user_id`); the **DM branch is BYTE-BEHAVIOUR UNTOUCHED** (`dm:<user_id>` arm unchanged). This is a **production edit inside an already-touched file** (`prompt_assent.py` is F-3 — no new file) | a non-DM channel turn with no mapping row no longer yields `None` for `external_user_id`; the DM branch's output is byte-identical to v0.29.18 | `daemon/tools/prompt_assent.py` | T2.1 | R-6 | AC17 |
| **T2.16** | **The `context.is_group_dm` dead-slot deletion** (blocker #7 — no natural owner existed): remove the `is_group_dm` slot from `_AssentContext.__slots__` (`daemon/tools/prompt_assent.py:586-601`) and its assignment at `:689`; the classification function reads the raw meta dict, so the slot is a second carrier | the slot is gone; `grep -n "is_group_dm" daemon/tools/prompt_assent.py` returns only the function-internal reads, never an `_AssentContext` attribute | `daemon/tools/prompt_assent.py` | T2.2 | R-6 | P2-AC16 |
| **T2.17** | **The mint-side `approver-not-pinned` refusal envelope carries `flags.mismatch=0`** (blocker #6): the mint path's caller flags set the flag **directly** (the mint path bypasses `_mismatch_aware_refusal`, which sets `"mismatch"` only at `:2133`), so leg-5's probe-evidence pass criterion is **producible without routing through `_record_mismatch`** (the same split discipline as T2.5's 4f edit) | `grep -n "flags.mismatch" daemon/tools/prompt_assent.py` returns a mint-side site; the mint path's flags dict carries `mismatch=0` on the `approver-not-pinned` refusal | `daemon/tools/prompt_assent.py` | T2.5 | R-3 | AC7 |

| **T2.14** | **Phase-level gate (TESTS-ONLY — owns NO production AC):** prove the INFO-flag semantics (P2-AC15 — owned by **T2.2's** flags-dict edit) and the dead-slot deletion (P2-AC16 — owned by **T2.16**); T2.14 asserts both by test only | the two amended acceptance criteria are asserted by a named test row | (tests only) | T2.1-T2.13 | — | P2-AC15, P2-AC16 |

---

## 4. Acceptance criteria

| ID | Criterion |
|----|-----------|
| **AC1** | The resolver reads the env var **at call time**, returns a typed outcome, and logs one WARNING per offending entry **by key only** |
| **AC2** | An unset env var resolves to a **valid empty map**; the lane then behaves **exactly** as v0.29.18 |
| **AC3** | The mint gate reproduces the matrix's §1.1 algorithm **cell for cell** — including the ordering (the group-DM leg and the absent-discriminator leg precede the map) |
| **AC4** | On the DM path, the map is **never** resolved (a structural pin proves it, not a comment) |
| **AC5** | The approve gate reproduces the matrix's §1.2 algorithm cell for cell; **no** existing approve refusal changes code or budget behaviour |
| **AC6** | The **ari-EXACTLY category pin** is green (`prompt_assent` remains privileged + `ari`'s allow entry + the in-body gate). The taxonomy grows by **exactly two** codes (`approver-not-pinned`, `designation-config-invalid`); the census pin matches **exactly**; the placed set is a subset of the taxonomy and **disjoint** from `MISMATCH_CODES` |
| **AC7** | A refusal carrying either new code leaves the token's `attempts` unchanged, does not revoke the token and does not consume the K-9 budget |
| **AC8** | The tool's input schema still carries **no** identity field (the pin is green) |
| **AC9** | The four structural pins each have a **named counter-mutation** registered for Phase 3 to demonstrate |
| **AC10** | Every refusal detail added by this phase is **content-free** (no persona content, no diff content, no channel name, no raw map value) |
| **AC11** | The **existing** DM-path pins are green **without any edit** to their files |
| **AC12** | `git diff` shows **no** change to `daemon/repositories/prompt_assent/models.py`, `daemon/repositories/prompt_assent/repository.py`, `daemon/migrations/versions/**`, `daemon/api.py`, `daemon/sources/adapters/discord/adapter.py`, `daemon/tools/upgrade_journal.py`, `daemon/manager.py`, `daemon/config.py`, `daemon/repositories/prompt_assent/__init__.py` (no re-export — the service imports by submodule path today, `daemon/services/prompt_version_service.py:470-472`) |
| **AC13** | Every **CHANNEL** mint records `map_digest` (12-hex) in the mint INFO line; **DM mints record NO map_digest** — the DM path never reads `ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS` and never calls the resolver (**INVARIANT-DM carve-out, recorded**). |
| **AC14** | The **audit payload carries `map_digest` only for channel-minted tokens** (absent for DM-minted) — the decision provenance (the approve/reject audit row names the map that was in force) |
| **AC18** | *(owned by T2.2 + T2.3 — the gates apply the per-side mapping at their call sites)* **The per-side mapping table pin** (D-18): every neutral leg maps to **exactly one code per side**; the predicate itself **NEVER** returns a side-specific code; the mint-side table and the approve-side table are pinned by test; **the approve-side APPROVER_NOT_PINNED code is `approver-not-pinned` — outside `MISMATCH_CODES` (NO budget burn)** |
| **AC15** | logged at FIRST successful resolution in a process AND whenever the effective digest changes between resolutions — inside the cached-lazy resolver; no boot hook, no api.py — the line carries the map's entry count + a content-free 12-hex fingerprint; never a channel id, a user id or a raw value |
| **P2-AC15** | The INFO line's `private` / `private_equivalent` flag set is **stated for BOTH paths**: a DM mint emits `private=True, private_equivalent=True`; a designated-channel mint emits `private=False, private_equivalent=True` (the existing `private: True` flag at `daemon/tools/prompt_assent.py:1818-1822` is only true for a DM) — **owned by T2.2** (the mint gate's flags-dict edit) — the flag set is documented, not silently wrong |
| **AC17** | **The non-DM `external_user_id` derivation branch** — a non-DM channel turn with no mapping row derives `channel:<guild_id>:<channel_id>:<user_id>` from the daemon-derived nested identity; the **DM branch is byte-behaviour-unchanged** (`dm:<user_id>` only when `is_dm is True`) **with a named counter-mutation** (mutate the DM arm ⇒ the pin reddens); an absent nested user id ⇒ `extraction-incomplete` (the negative cell, M-19) |
| **P2-AC16** | The `context.is_group_dm` **dead slot** (`daemon/tools/prompt_assent.py:689` — written, read nowhere) is **decided**: the classification function reads the raw meta dict, so the slot is **deleted** from `_AssentContext` rather than left as a second carrier |

---

## 5. Notes for the implementer

- **The matrix IS the specification.** `refusal-cell-matrix.md` §1 is written as executable
  pseudocode — follow it literally. The ordering is load-bearing and was chosen so that the
  existing pins stay green *whatever the map contains*.
- **The map is resolved once per tool call** — do not resolve it inside the predicate, and do not
  resolve it twice — **memoised per call, re-resolved on a fresh call**. (The earlier wording —
  *"the mint leg and the approve leg must never see different maps"* — was **false by design**:
  the **A-9 re-designation window requires a fresh-call re-resolution** to be visible. Architect
  §3.7.)
- **`_record_mismatch` is not a logging call** — it increments a durable counter and can revoke a
  token (`daemon/tools/prompt_assent.py:1174-1209`). Touching it changes security behaviour.
- **The detail strings are content-free.** The lane's INFO log is content-free by doctrine; the
  refusal detail follows the same rule.
- **Touched-file docstrings (N7).** The module docstrings of the files this phase edits update
  **with the code edit** — T2.x owns them; no silent docstring drift. `prompt_assent.py`'s
  module docstring gains the designated-channel surface and the `DesignationResult` contract;
  `prompt_version_service.py`'s gains the designated-branch note.
- **If the architect rules "yes" on Q-1 (thread inheritance)**, the change belongs to Phase 1's
  `is_designated_assent_channel` — this phase needs **no** change for it. *(Q-1 is now **RULED NO**
  — **D-9** — so this is a closed contingency.)*
- **The `private` INFO flag becomes a lie without care** (architect §11 row 26): a designated-channel
  mint is **not** a private DM, yet `daemon/tools/prompt_assent.py:1818-1822` would emit
  `private: True`. **Decide it here:** emit `private=False` **plus** a new
  `private_equivalent=True` for the designated path, and document the flag set (P2-AC15).
- **`context.is_group_dm` is a dead slot today** (`daemon/tools/prompt_assent.py:689` — written,
  read nowhere). The classification function reads the **raw meta dict**, so the slot is either
  **deleted** or made the single carrier — **the plan decides: delete it** (P2-AC16). Leaving a
  second carrier is the drift hazard the single-home discipline exists to prevent.
