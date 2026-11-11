# Plan Overview: prompt-assent Phase 1.5 — designated channels (pinned-approver model)

| Field | Value |
|---|---|
| Date | 2026-11-11 (UTC) |
| Author | plan-creation worker (plan lane) |
| Worktree | `/home/nea/ensemble-src-wt-assent-p15-channels` |
| Branch | `feature/assent-designated-channels` |
| Base commit | `efa8aaae28153b8e9c49b86abe19e92be5458ebe` (VERIFIED — `git rev-parse HEAD`) |
| Status | **Council-APPROVED; approver iterations 001–003 REJECTED on text-level fold defects; ESCALATED to leader — LEADER-CLEARED for build under D-18/D-19/D-20 + the nine-blocker edit list** |
| Planning set | [`plan-overview.md`](plan-overview.md) · [`phase1-plan.md`](phase1-plan.md) · [`phase2-plan.md`](phase2-plan.md) · [`phase3-plan.md`](phase3-plan.md) · [`phase4-plan.md`](phase4-plan.md) · [`decisions.md`](decisions.md) · [`refusal-cell-matrix.md`](refusal-cell-matrix.md) · [`risks.md`](risks.md) · [`doctrine.md`](doctrine.md) · [`research-findings.md`](research-findings.md) · [`ceremony-notes.md`](ceremony-notes.md) **(NEW — this lane's own)** · [`architecture-recommendation.md`](architecture-recommendation.md) **(the architect's artefact — the 12th file; a DIFFERENT document from the parent's same-named file at `../prompt-assent/architecture-recommendation.md`, which still wins for the parent's own questions)** |
| Parent lane | [`../prompt-assent/`](../prompt-assent/) — `architecture-recommendation.md` **wins on conflict** for the parent's questions; this set's own architect artefact will win for the Phase-1.5 questions |

---

## 1. Objective

> **SCOPE FENCE: NO live promotion, NO arming, NO live-install mutation in this lane — staging
> only; the ceremony is a separate, post-lane Emperor act.**

**Extend the conversational assent lane from "only a 1:1 DM counts" to "a 1:1 DM **or** an
operator-designated Discord channel pinned to one human counts" — without weakening a single
hardening: the three conjunctive factors, the live factor-2 window, the TTL'd single-use token,
the `render_sha256` binding, the append-only provenance, the default-OFF kill-switch and the
DM path's behaviour all stay exactly as they are in staged v0.29.18.**

One sentence that marks this feature complete: *the Emperor mints in his real working channel —
a Discord server channel the operator designated and pinned to him — ari relays the diff and the
token, the Emperor's echoed word approves it, and the ledger reconstructs the whole chain (who,
from where, in which message, on which item, at what time) while a stranger in the same room,
a group DM, an undesignated channel and a broken configuration each refuse with their own typed
code and leave no trace.*

**The one-sentence technical characterisation:** *this is a predicate problem, not a data
problem.* The daemon already carries the guild, the channel, the channel type and the author
on every Discord turn (`daemon/sources/adapters/discord/adapter.py:1080-1095`); the only thing
refusing a guild-channel mint today is the single predicate
`is_dm AND NOT is_group_dm` (`daemon/repositories/prompt_assent/mint_context.py:29-50`).

---

## 2. The Doctrine

> "this is channel, i often use channel to split tasks category in my mind, we consider channel is private/dm for now"

**Carried verbatim** (2026-11-11, Discord). Full text, context and its four binding
consequences: [`doctrine.md`](doctrine.md).

This is the **first tracked record** of the ruling anywhere in the repository — the staged
v0.29.18 ceremony notes carry **no** designated-channels mention.

**The parent lane's doctrine is inherited unchanged** — *"FE-UI is a deeper view; the working path
is conversation. The Emperor's word, relayed by C2 with provenance, is a first-class approval
lane."* Phase 1.5 adds a **surface** to the conversational path; it changes **no** FE byte, and
it adds **no** new way to approve without a human turn.

---

## 3. Scope

### In scope

| # | Item | Why it is in | Binding |
|---|---|---|---|
| **I-1** | A **designation map** — `channel_id → pinned approver user_id` — read from **one** env var by a **strict, fail-loud, all-or-nothing parser** | BLESSED-1; the map is the operator's act | D-3, D-4 |
| **I-2** | The **designated-channel predicate** — the ONE implementation owner of "is this surface private-equivalent" | BLESSED-2 | D-6 |
| **I-3** | The **mint gate** extension (leg M-4c) — private **or** designated-and-pinned | BLESSED-2 | D-6, matrix §1.1 |
| **I-4** | The **approve gate** extension (leg 4f) — the service predicate gains a designation-map parameter | BLESSED-2, BLESSED-3 | D-6, matrix §1.2 |
| **I-5** | **Exactly two** new refusal codes — `approver-not-pinned`, `designation-config-invalid` — both **outside** the mismatch class (no budget burn) | the new failure modes | D-8 |
| **I-6** | The **full cell matrix** re-proven for the new surface, with every DM cell proven unchanged | the commission's hard requirement | matrix §2-§4 |
| **I-7** | The **adapted live probe** (6 legs) and the **adapted arming wording** | BLESSED-4 | D-11 |
| **I-8** | The **operator surface** — the runbook's K-3 section, its taxonomy table and its troubleshooting rows | BLESSED-4 | phase4 |
| **I-9** | A **STAGED RELEASE `v0.29.20`** (no live promote, no arming) | house rule | D-10 |

### Out of scope

| # | Item | Why it is out |
|---|---|---|
| **O-1** | **Any change to the FE approval lane** (`daemon/routers/prompts.py:616-697`) | the doctrine — FE is a deeper view; the parent's `test_prompt_assent_fe_unchanged.py` must stay green |
| **O-2** | **Any change to the Discord adapter** | the metadata it builds already carries everything (D-2) |
| **O-3** | **Any change to `classify_user_origin`** or the factor-2 window | the trust anchor is not forked (D-2) |
| **O-4** | **Any schema change** — no new table, no new column, no new migration, no `daemon/api.py` touch | D-5 — the ledger already proves the designated-channel mint |
| **O-5** | **The `artifact_class` gate** (`daemon/tools/prompt_assent.py:1681-1686` — the earlier `:1685-1692` was stale) — CORE rows only | unchanged; **Q-3 RULED: unchanged** (architect §7) |
| **O-6** | **More than one approver per channel** | BLESSED-1 — "exactly ONE pinned approver per designated channel"; the map shape makes it unrepresentable (Q-2) |
| **O-7** | **Thread inheritance** (a thread inside a designated channel) | D-9 — exact channel-id match only; the Emperor may name a thread's own id explicitly (Q-1) |
| **O-8** | **A live promote, a ceremony, or arming the lane** | house rule — staging only; arming is a separate post-promote Emperor decision |
| **O-9** | **Executing the T3.5 re-stamp** (`agents/ari/tools_note.md`) | the artifact write path is human-session-only (`daemon/tools/prompt_artifacts.py`); it is an acceptance **subject**, not a lane deliverable |
| **O-10** | **A DB-tier or HTTP-tier designation write surface** | D-3 — the DB settings tier is **agent-writable** (`daemon/tools/settings_tools.py:483` + `agents/ari/meta.json:37`) |
| **O-11** | **Touching the parent plan set** (`../prompt-assent/*.md`) | **D-1** — the parent's ruling lineage stays intact. **⚠ AMENDED @ the architect's §11 row 10:** the **runbook** is **THIS lane's** file (F-11, Phase 4 writes it) and the **planning-side ceremony notes** are **THIS lane's** new file (F-13); only the **release-dir** ceremony notes belong to the build lane |
| **O-12** | **Slack / Telegram / WhatsApp designated surfaces** | the lane is Discord-first (D-5, the parent's ruling); the map is keyed by Discord channel ids |
| **O-13** | **Committing map values / real approver ids** — the runbook documents the variable's **NAME** and **shape**, never a real value; the planning set carries **placeholder ids only** | **architect §11 row 19** — a real channel id + a real approver id in a committed file would be an operator-configuration leak. **No real value appears anywhere in this set** |

---

## 4. Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|---|---|---|---|---|---|
| **1** | **The designation surface** | The map type, the strict all-or-nothing parser, the validation rules, the **mandatory map-parse log line** and the **ONE** classification function that owns the whole leg ordering — with **zero** touch to the tool or the service | **9** | tight with P2 (the gates consume the predicate) · independent of P3/P4 | pending |
| **2** | **The gate binding + the taxonomy census** | The M-4c and 4f extensions, the **4f split**, the two new codes, the cached-lazy resolver, the **map-digest provenance** and the **census pin** that must land with them | **17** | tight with P1 (the predicate) · tight with P3 (the tests) · loose with P4 | pending |
| **3** | **The proof surface** | The designated-channel test file (incl. **M-15** and its counter-mutation), the **runtime resolver-raises pin**, the extended fixture, the pack registration and the map-unset equivalence proof | **12** | tight with P2 (it exercises the gates) · tight with P4 (the pack ships in the release) | pending |
| **4** | **The operator surface + the staged release** | This lane's **ceremony-notes.md** (the two-phase probe), the runbook's adapted K-3 + taxonomy + troubleshooting + map contract, the v0.29.20 staging records and the staged release | **8** | loose with P1-P3 | pending |

### 4.1 — Why this grouping (the phase-grouping rationale)

The phases are grouped by **the file cluster they own**, because the house rule on shared
worktrees is that **a context is owned by exactly one phase** — no two phases edit the same
files concurrently (the parent's `plan-overview.md` §"Shared-context grouping"). The clusters
are disjoint by construction:

| Context (file cluster) | Owner phase | Files |
|---|---|---|
| **C-1 — the designation surface + the privacy classification** | **P1** | `daemon/repositories/prompt_assent/designation.py` **(NEW)** · `daemon/repositories/prompt_assent/mint_context.py` · `tests/unit/test_prompt_assent_designation_parser.py` **(NEW)** |
| **C-2 — the gate sites + the refusal vocabulary** | **P2** | `daemon/tools/prompt_assent.py` · `daemon/services/prompt_version_service.py` · `tests/unit/tools/test_prompt_assent_registration.py` (the taxonomy census) |
| **C-3 — the proof surface** | **P3** | `tests/unit/test_prompt_assent_designated_channel.py` **(NEW)** · `tests/helpers/prompt_assent_tool_fixture.py` · `tests/helpers/prompt_assent_flow.py` · `test/packs/prompt_assent_phase3_unit_test.sh` · `.agents/tester/PACKS.md` |
| **C-4 — the operator surface + the release** | **P4** | `docs/runbooks/prompt-assent.md` · the v0.29.20 ceremony notes (in the build lane's release dir) · `CHANGELOG.md` |

**Two grouping choices are deliberate and worth stating.**

1. **P1 and P2 are separate even though both are "the extension".** They touch disjoint file
   clusters, and separating them means the *pure* half (the parser + the predicate — the half
   with no daemon dependencies and no I/O) can be unit-proven on its own before anything is wired
   into a gate. The predicate is the security-critical artefact; it deserves to be green before it
   is reachable.
2. **The taxonomy census pin travels with P2, not with P3.** The census
   (`tests/unit/tools/test_prompt_assent_registration.py:73`) asserts
   `REFUSAL_TAXONOMY == ARCHITECT_REFUSAL_TAXONOMY` exactly. If P2 emitted a code that the
   taxonomy did not yet list, the refusal envelope's code sanitisation
   (`daemon/tools/prompt_assent.py:2060`) and the census would disagree and the tree would be red
   between the phases. **A gate and its vocabulary must land together.**

### 4.2 — Dependency map

```
                ┌───────────────────────────────────────────────┐
                │ P1  The designation surface                   │
                │  • designation.py (NEW): map type + strict     │
                │    all-or-nothing parser + validation rules   │
                │  • mint_context.py: the ONE private-equivalent│
                │    predicate (DM ∨ designated-and-pinned)      │
                └───────────────────┬───────────────────────────┘
                                    │ the predicate + the parsed map
                                    ▼
                ┌───────────────────────────────────────────────┐
                │ P2  The gate binding + the taxonomy census    │
                │  • prompt_assent.py: env knob + resolver,      │
                │    M-4c extension, 4f call site, +2 codes     │
                │  • prompt_version_service.py: 4f predicate     │
                │  • the registration census pin (+2 codes)     │
                └───────────────────┬───────────────────────────┘
                                    │ the gates under test
                                    ▼
                ┌───────────────────────────────────────────────┐
                │ P3  The proof surface                         │
                │  • test_prompt_assent_designated_channel.py   │
                │    (NEW) — the matrix cells                   │
                │  • the fixture + the flow rig (hermetic map)  │
                │  • the map-unset equivalence proof            │
                │  • the pack + PACKS.md registration           │
                └───────────────────┬───────────────────────────┘
                                    │ the green pack
                                    ▼
                ┌───────────────────────────────────────────────┐
                │ P4  The operator surface + the staged release │
                │  • the runbook (adapted K-3, taxonomy, triage)│
                │  • the v0.29.20 ceremony notes                │
                │  • STAGED RELEASE v0.29.20 — NO promote       │
                └───────────────────────────────────────────────┘
```

**Hard dependencies (non-negotiable).**

- **P1 → P2** — the gates cannot bind a predicate that does not exist.
- **P2 → P3** — the matrix is only meaningful once the gates behave.
- **P3 → P4** — the staged release must ship a green pack.

**Soft dependency (mitigated, not blocking).**

- **P2's env knob vs. P4's runbook.** The knob is readable at call time before the runbook
  documents it. **Mitigation:** the knob is **default-empty** — an unset map means "no designated
  channels", i.e. **exactly the v0.29.18 behaviour**. P2 alone is safe to land and to ship: it
  adds a surface nobody can reach until an operator configures one.

---

## 5. Coupling map

| Phase ↓ / Phase → | **P1** | **P2** | **P3** | **P4** |
|---|---|---|---|---|
| **P1** | — | **STRONG** — the predicate + the parsed map type | none | none |
| **P2** | **STRONG** | — | **STRONG** — the matrix asserts the gates' behaviour | **WEAK** — the runbook documents the codes |
| **P3** | none | **STRONG** | — | **MEDIUM** — the pack ships in the release |
| **P4** | none | **WEAK** | **MEDIUM** | — |

**Shared contracts (the things that must not drift).**

| Contract | Owner | Consumers | Drift risk |
|---|---|---|---|
| The **private-equivalent rule** (DM ∨ designated-and-pinned) | P1 (`mint_context.py`) | P2's mint gate, P2's approve predicate | **HIGH** — two consumers of one rule; the ONE-owner rule is the mitigation |
| The **refusal vocabulary** | P2 (`REFUSAL_TAXONOMY` + the census pin) | P2's gates, P3's assertions, P4's runbook table | **MEDIUM** — the exact-match census is the mitigation |
| The **map semantics** (parse rules, all-or-nothing, typed-invalid) | P1 (`designation.py`) | P2's resolver | **LOW** — one parser |
| The **budget rule** (mismatch-class codes burn, the new codes do not) | P2 | P3's assertions | **MEDIUM** — the `_record_mismatch` call sites are the risk; the rule is stated in the matrix §1.3 |

---

## Namespace Legend (n5)

| Prefix | Meaning | Disambiguation |
|---|---|---|
| **M-n** | matrix **mint cells** (M-1 … M-19) | **NOT** the gate-internal leg labels M-4a/b/c (which are implementation steps inside the mint gate, not matrix rows) |
| **A-n** | matrix **approve cells** (A-1 … A-12) | distinct from research-findings' A-labelled pass IDs (which are prefixed with the researcher's own label) |
| **PR-n** | proofs (INVARIANT-DM's four legs) | **NOT** the P-n namespace (which is the risk register's structural pins) |
| **P-n** | risk-register **structural pins** | **NOT** PR-n |
| **AC-n** | per-phase acceptance criteria | phase-qualified where needed: **P1-AC15** / **P2-AC15** etc. |
| **B-n, N-n, S-n** | **fold-labels** (iteration provenance only) | these cite **which iteration** introduced a fix; they are **NEVER** authority — authority = the architect artefact / decisions rows |

## 6. Risk summary

The full register is [`risks.md`](risks.md). The **top five** for this lane:

| # | Risk | Impact | Mitigation (of record) |
|---|---|---|---|
| **R-1** | **The designation map is agent-settable.** The DB settings tier is agent-writable, so the map must not live there. The residual is the **bash → `.env` → restart** path | **CRITICAL** — unvetted channel expansion by the minting agent | **D-3** (**RULED**: ENV stands) · **D-15** (**LEADER-RULED**: accept with controls — the effective-map log line (FIRST-resolution + on-digest-change, **D-20**), the map digest in the mint provenance, the rewritten residual). **The residual is *accepted*, not closed**: the restart is **provenance-gated, not operator-gated**, and `.env` is mode 600 **owner `nea` group `nea`** (mode 600 does not separate uids). The standing mitigation: even a malicious map cannot mint without the **pinned human's** turn |
| **R-2** | **A group DM or an undesignated channel slips through** because the predicate was extended carelessly — and **the ordering is convention, not code shape**: every existing pin is **outcome-only** and stays green under a map-first ordering while a group DM mints | **HIGH** — the multi-occupant boundary is the lane's core protection | **ONE classification function** owns the leg ordering (architect §4.4); **M-15** pins the previously unpinnable shape (a group DM whose channel id IS in a valid map) **with a counter-mutation**; the runtime resolver-raises pin proves `INVARIANT-DM` by execution; **M-9/M-11** cover the invalid-map orderings |
| **R-3** | **The new codes burn the durable counter** — as originally specified, the 4f site records **unconditionally** (`daemon/tools/prompt_assent.py:2021`), so **five** approve-side new-code refusals would **revoke the Emperor's live token** (threshold 5) | **HIGH** — an availability DoS *and* a token-revocation bug | **The 4f split** (matrix §1.3) — the mismatch-class branch keeps the recording; the two new codes refuse **before** it; the three genuine mismatch sites are untouched. **Phase 2 owns the edit (T2.5)** |
| **R-20** | **The token + the full prompt diff are posted into a multi-occupant room**, and a bystander's relayed token is the **real** token ⇒ a lockout DoS on the legitimate approver | **HIGH** — disclosure + a DoS | **LEADER RULING 1** (**D-14**): **accept-and-record**, with the delivery mode an **explicit, overridable default** flagged in the ceremony notes; the probe's hygiene rules (single-occupant channel, consume before reuse, never paste a token) |
| **R-21** | **NT-5 — last-speaker-wins**: a bystander's reply between the Emperor's turn and the tool call re-points the window ⇒ the Emperor's attempt refuses `approver-not-pinned` | **MEDIUM** — a cheap continuous DoS | fail-closed; the runbook's **last-speaker rule**; probe legs 3/5 carry the discriminator; per-user-window hardening is a **follow-up, out of scope** |

---

## 7. The binding file list

**Every file this lane touches. Nothing else may be edited.**

| # | File | Change | Phase |
|---|---|---|---|
| **F-1** | `daemon/repositories/prompt_assent/designation.py` | **NEW** — the map type, the strict parser, the validation rules, the env-name constant | **P1** |
| **F-1b** | `tests/unit/test_prompt_assent_designation_parser.py` | **NEW** — the parser unit matrix, the single-home pin, the no-regression pin | **P1** |
| **F-2** | `daemon/repositories/prompt_assent/mint_context.py` | **MODIFIED** — the designated-channel predicate (beside the untouched `is_private_assent_channel`) | **P1** |
| **F-3** | `daemon/tools/prompt_assent.py` | **MODIFIED** — the env knob + the call-time resolver, the M-4c extension, the 4f call-site extension, the two new codes, the placed-codes constant | **P2** |
| **F-4** | `daemon/services/prompt_version_service.py` | **MODIFIED** — `assent_channel_not_private_code` gains the designation-map parameter and delegates to F-2 | **P2** |
| **F-5** | `tests/unit/tools/test_prompt_assent_registration.py` | **MODIFIED** — the taxonomy census gains exactly the two new codes | **P2** |
| **F-6** | `tests/unit/test_prompt_assent_designated_channel.py` | **NEW** — the matrix cells | **P3** |
| **F-7** | `tests/helpers/prompt_assent_tool_fixture.py` | **MODIFIED** — the hermetic map control in `build_discord_meta` / the flow rig | **P3** |
| **F-8** | `tests/helpers/prompt_assent_flow.py` | **MODIFIED** — the flow rig's map parameter | **P3** |
| **F-9** | `test/packs/prompt_assent_phase3_unit_test.sh` | **MODIFIED** — the new file joins the pack | **P3** |
| **F-10** | `.agents/tester/PACKS.md` | **MODIFIED** — the pack's row is refreshed (the file, the cell count, the pass record) | **P3** |
| **F-11** | `docs/runbooks/prompt-assent.md` | **MODIFIED** — the K-3 section becomes the adapted 6-leg probe; the taxonomy table gains the two codes; the troubleshooting rows gain the designated-channel entries | **P4** |
| **F-12** | `CHANGELOG.md` | **MODIFIED** — the v0.29.20 dated section | **P4** |
| **F-13** | `.agents/shared/planning/prompt-assent-channels/ceremony-notes.md` | **NEW** — **this lane's** adapted probe (the two-phase §8 shape), the staging records and the delivery-mode flag; it **supersedes the parent's DM-only K-3 text from the new side** (architect §8.6) | **P4** |
| **F-14** | `daemon/tools/prompt_assent.py` — the **`_derive_external_user_id` non-DM branch** (an EDIT to an already-touched file) | **APPROVER-ORDERED (B3(i)/D-19)**: the non-DM branch derives `channel:<guild_id>:<channel_id>:<user_id>` — **AUDIT-ONLY** (no in-tree consumer; `_parse_external_user_id` never applied); the DM branch BYTE-BEHAVIOUR UNTOUCHED | **P2** |
| **F-15** | `daemon/repositories/prompt_version/repository.py` (`_assent_audit_provenance`, `:1813-1841`) | **ADDITIVE-ONLY** (blocker #2): the approve/reject audit payload gains the `map_digest` JSON field; **the sole exception to the untouched fence** (D-5: no schema change — one additive JSON key) | **P2** |

**Fifteen numbered rows** (F-1 … F-15, with **F-1b** as F-1's companion test file — **16 table rows** in total). **F-8 is unconditional** — the flow rig's
map parameter is T3.1's work; `build_discord_meta` (F-7) does not carry it. **`tests/conftest.py` is
deliberately NOT in this list** — no phase
task implies it; the env scrub lives in the **rig**, and the rig's scrub is **pinned** instead
(architect §11 row 28 / NT-7 — **marked RESOLVED-WITH-EVIDENCE**: that row said 14 files and F-8 conditional; this set carries **15 numbered F-rows (16 table rows)** and **F-8 unconditional**, as amended in the iteration-002/003 folds).

### Files that are explicitly **NOT** touched

`daemon/sources/adapters/discord/adapter.py` · `daemon/tools/upgrade_journal.py` ·
`daemon/manager.py` · `daemon/config.py` · `daemon/api.py` · `daemon/models/app_settings.py` ·
`daemon/services/settings_service.py` · `daemon/routers/settings.py` ·
`daemon/routers/prompts.py` · `daemon/repositories/prompt_assent/models.py` ·
`daemon/repositories/prompt_assent/repository.py` ·
**`daemon/repositories/prompt_assent/__init__.py`** *(no re-export — the service imports by
submodule path today, `daemon/services/prompt_version_service.py:470-472`; architect §4.6 (iii))* ·
`daemon/migrations/versions/**` ·
`agents/ari/meta.json` · `agents/*/workflow.md` · `frontend/**` · the parent's planning set.

**Each exclusion carries a reason** — the schema exclusion is D-5, the adapter exclusion is D-2,
the `daemon/config.py` exclusion is D-3 (the map is deliberately **not** a `PromptAssentConfig`
field), the `settings_service.py` exclusion is D-3 (the DB tier is agent-writable), and the
`daemon/migrations/versions/**` exclusion is D-5 (**no new table, no new column, no new
migration** — the migration head stays `20261110_221500_create_prompt_assent_tokens.sql`, 128
files).

---

## 8. Success criteria

Measurable and verifiable — each is checkable without a judgement call.

| # | Criterion | How it is checked |
|---|---|---|
| **SC-1** | The **DM path is behaviourally unchanged** — every existing DM cell produces the same code, envelope, ledger and budget behaviour | **`INVARIANT-DM`** (matrix §4), proved by **PR-1…PR-4** — the runtime resolver-raises pin (**PR-1**), the source-shape pin (**PR-2**), the map-unset equivalence cell (**PR-3**) and the code-shape requirement (**PR-4**); the six existing pins stay **green and unmodified** |
| **SC-2** | A **mint in a designated channel by the pinned approver succeeds** and writes a token row with `mint_is_dm=0` | the new test file, cell M-6 |
| **SC-3** | A **non-pinned human in a designated channel is refused** with `approver-not-pinned`, **no token row**, and **no budget burn** | cell M-7 + the budget assertion |
| **SC-4** | An **undesignated guild channel** is refused with `mint-requires-private-channel`, **no token row** | cell M-5 (the existing pin, unmodified) |
| **SC-5** | A **group DM** and an **absent discriminator** are refused with `mint-requires-private-channel` **whatever the map contains** | cells M-11 + the existing pins |
| **SC-6** | An **invalid map** refuses only the designated-channel path; the DM path still mints | cells M-9 + M-10 + A-11 |
| **SC-7** | The **taxonomy grows by exactly two codes** and the census pin matches exactly | `REFUSAL_TAXONOMY == ARCHITECT_REFUSAL_TAXONOMY` (the existing pin) |
| **SC-8** | The **schema is unchanged** — no new table, no new column, no new migration; the migration head is still `20261110_221500` (128 files) | `git diff --stat` against the base + the migration census |
| **SC-9** | The **adapter, the trust anchor and the window are unchanged** | `git diff` shows no change to `daemon/sources/adapters/discord/adapter.py`, `daemon/tools/upgrade_journal.py`, `daemon/manager.py` |
| **SC-10** | The **FE lane's methods are byte-identical** (a **method-scoped** proof: the approve/reject def blocks) | `test_prompt_assent_fe_unchanged.py` green. **⚠ AMENDED @ the architect's §11 row 17:** `daemon/services/prompt_version_service.py` **IS** edited (F-4) — the old wording *"no FE file in the diff"* was **false as stated**; the FE **surface** (routes + repo) is unchanged, and the service module is an explicitly owned edit |
| **SC-11** | The **pack is green** and registered with its cell count and pass record | the pack run + the `.agents/tester/PACKS.md` row |
| **SC-12** | A **staged release `v0.29.20`** exists, is tagged **by name**, is **NOT promoted** and is **NOT armed** | the release dir + the tag + `current` unchanged |
| **SC-13** | The **runbook** documents the 6-leg adapted probe, the two new codes and the designated-channel troubleshooting | the runbook diff |
| **SC-14** | The **ruling** is recorded verbatim with its "carried verbatim" status row | [`doctrine.md`](doctrine.md) |

---

## 9. Acceptance

### ACC-1 — The real workflow (BLESSED-5)

**A mint in the Emperor's designated channel, approved by his echoed word, with full provenance.**

The ledger must reconstruct: *the pinned approver's user id · the designated channel id ·
`mint_is_dm=0` · the message id of the approving turn · the item (`version_id`) · the render
hash · the relay agent · the timestamps* — and **never** the raw token.

### ACC-2 — The live probe (BLESSED-4, D-11)

The **6-leg** probe in `decisions.md` **D-11** — legs 1-2 unchanged (DM, group DM), legs 3, 4A, 4, 5 new
(designated channel, non-designated channel, non-pinned user). **All six outcomes are recorded
before the lane is relied upon.** **Arming remains a separate post-promote Emperor decision.**

### ACC-3 — The owed T3.5 re-stamp

`agents/ari/tools_note.md` (`grep -c memoir_about` = **0** today) is the **first beneficiary** once
the lane is armed — and remains **out of this lane's deliverables** (O-9).

---

## 10. What the architect is being asked to rule on

Five questions are carried in [`decisions.md`](decisions.md) §Part C (**Q-1 … Q-5**); the two that
most need a verdict:

1. **Q-1 — thread inheritance.** This plan says **no** (exact channel-id match only). A "yes"
   widens the trust surface to sub-conversations.
2. **Q-4 — the env surface.** This plan says **yes** (env, because the DB tier is agent-writable),
   accepting a **restart-bound** designation change. A "no" requires a **new** operator-only write
   surface — a new table, a new route and a 129th migration, for a value with no query need.

The other three (**Q-2** multi-approver, **Q-3** the `artifact_class` gate, **Q-5** a DB secondary
tier) are recorded with this plan's recommendation and their consequences.
