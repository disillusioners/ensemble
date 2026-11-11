# Decision Ledger — prompt-assent Phase 1.5 (designated channels)

**Commission:** `prompt-assent` Phase 1.5 — designated channels (pinned-approver model).
**Doctrine:** see [`doctrine.md`](doctrine.md) — *"this is channel, i often use channel to split tasks category in my mind, we consider channel is private/dm for now"* (**carried verbatim**,
2026-11-11).
**Parent lane:** [`../prompt-assent/`](../prompt-assent/) — `decisions.md`, `doctrine.md`,
`architecture-recommendation.md` (the parent's header rule: *it wins on conflict*),
`ceremony-notes.md`, `risks.md`.

| Field | Value |
|---|---|
| Date | 2026-11-11 (UTC) |
| **Ruling cite** | **The Emperor, Discord, 2026-11-11 — *"this is channel, i often use channel to split tasks category in my mind, we consider channel is private/dm for now"*** (carried verbatim; full record + context in [`doctrine.md`](doctrine.md)). **This set is the FIRST tracked record of that ruling.** |
| **Parent ruling cite** | the parent lane's doctrine — *"FE-UI is a deeper view; the working path is conversation. The Emperor's word, relayed by C2 with provenance, is a first-class approval lane."* (`../prompt-assent/doctrine.md:5`, status row `:13`) |
| Worktree | `/home/nea/ensemble-src-wt-assent-p15-channels` (branch `feature/assent-designated-channels`, base `efa8aaae28153b8e9c49b86abe19e92be5458ebe`) |
| Sources | [`research-findings.md`](research-findings.md) (explorer passes A + B, re-verified line by line in the worktree) |
| Companion | [`risks.md`](risks.md) · [`plan-overview.md`](plan-overview.md) · [`refusal-cell-matrix.md`](refusal-cell-matrix.md) |

---

## Status legend (inherited from the parent ledger, `../prompt-assent/decisions.md:13-21`)

| Status | Meaning |
|---|---|
| `BLESSED` | Fixed by the commission. Implementation must match. Changing it needs the Emperor, not the architect. |
| `DECIDED` | Fixed by the technical analysis (the question was closed *in* the analysis, not open). Binding on implementation. |
| `RECOMMENDED-PENDING-ARCHITECT` | The analysis closed the question with a recommendation; the architect must ratify before the developer lane lands it. |
| `OPEN-JUDGEMENT` | The analysis explicitly flagged its own proposal as a judgement call to be overruled if the architect disagrees. |
| `RULED` | **Decided by the architect.** Binding on implementation. Supersedes the prior status of the same entry. |
| `LEADER-RULED` | **Decided by the leader/dispatcher** (the Emperor's ruling relayed, or a leader amendment). Binding on implementation. Used for the three **leader rulings** (NT-1, NT-2, NT-9) and for any decision the architect explicitly leaves to the Emperor. |

> **Post-ruling state for THIS set (actual, 2026-11-11).** The architect pass is **DONE**
> (`architecture-recommendation.md`, BUILD verdict, 5 dispatched verification passes); the council
> has **APPROVED** the set (7 blocking + 12 non-blocking folded in the first fold-back pass; the
> final fold is this pass). Statuses in use: `BLESSED` (the commission), `DECIDED` (the analysis
> closed the question), `RULED` (**7** entries: D-3, D-4, D-8, D-9, D-10, D-11, D-13) and
> `LEADER-RULED` (**5** entries: D-14, D-15, D-16, D-17, plus the council's withdrawn-designation
> ruling). Entries below are a record of rulings, not proposals; the original reasoning is
> preserved for the audit trail. The parent's `architecture-recommendation.md` wins for the
> parent's own questions (D-1); this set's own architect artefact —
> `architecture-recommendation.md` **inside this directory** — wins over this file and over
> every parent document for the Phase-1.5 questions.

---

## Part A — The commission's five blessed design points (restated, with this set's readings)

### BLESSED-1 — Channel designation: operator-configured channel id → pinned approver user id

**Status:** `BLESSED` (commission) · surface + validation semantics: **D-3**, **D-4**
**Decision.** Exactly **ONE** pinned approver per designated channel (the Emperor). The mapping is
**operator-configured**; designation is an **operator act**, never an agent-settable act. Invalid
values **fail loud** (refuse, never silently drop).

**This set's reading.** The "exactly one pinned approver" rule is a property of the **map shape**
(`channel_id → approver_user_id`, a single value per key), not a runtime constraint — the map
format itself makes a second approver per channel unrepresentable. See **D-4**.

---

### BLESSED-2 — Mint rules extended: the privacy factor is satisfied by DM **or** designated channel

**Status:** `BLESSED` (commission) · binding point: **D-6**
**Decision.** The privacy factor is satisfied by **either** (a) a 1:1 DM (unchanged), **or**
(b) a genuine-user turn by the **pinned approver** within a **designated channel**. Everything
else refuses, fail-closed: group DMs, non-designated channels, and non-pinned users (even inside
a designated channel). `mint_user_id` derivation stays daemon-side — the room **and** the pinned id
together authenticate; a stranger in the court still cannot mint.

---

### BLESSED-3 — All existing hardening unchanged

**Status:** `BLESSED` (commission) · confirmation: **D-7** · **⚠ AMENDED @ the architect's
§2.6** (the byte claim is narrowed to the enforceable behaviour scope)
**Decision.** The three conjunctive factors, the mint gate on a live factor-2 window, the TTL'd
single-use token, the `render_sha256` binding, the append-only provenance and the default-OFF
kill-switch are all unchanged.

> **⚠ AMENDED — the DM-path claim is narrowed from BYTES to BEHAVIOUR.** The commission's wording
> ("the DM path BYTE-IDENTICAL") is **unsatisfiable as literally worded**: both gate sites must be
> edited (`daemon/tools/prompt_assent.py:1708-1716` and `:2018-2026`; the service predicate
> `daemon/services/prompt_version_service.py:460-476`), so the claim cannot mean the bytes of
> those files. **The enforceable guarantee is `INVARIANT-DM`**
> ([`refusal-cell-matrix.md`](refusal-cell-matrix.md) §4), stated verbatim there:
>
> > For any turn where `is_private_assent_channel(discord_meta)` is `True`, the process MUST NOT
> > read `os.environ["ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS"]`, MUST NOT call the resolver or
> > `parse_designations`, and the outcome (code, envelope, token row, ledger shape, budget counter,
> > attempt counter) MUST equal the v0.29.18 outcome.
>
> **What remains byte-identical:** the FE surface (method-scoped proof), the
> `is_private_assent_channel` function (`daemon/repositories/prompt_assent/mint_context.py:29-50`
> untouched), the DM predicate legs, the DM cells' code / ledger / budget, **and the DM cells'
> envelope** — the envelope stays byte-identical **only because T2.8 confines the new detail text
> to the two new codes** and leaves the shared
> `mint-requires-private-channel` detail string (`daemon/tools/prompt_assent.py:1712-1714`)
> untouched (architect §2.4). **What is provably changed:** the bytes of
> `daemon/tools/prompt_assent.py`, `daemon/services/prompt_version_service.py` and
> `daemon/repositories/prompt_assent/mint_context.py`.

---

### BLESSED-4 — Live probe plan adapted

**Status:** `BLESSED` (commission) · wording: **D-11**
**Decision.** The K-3 probe becomes **mint-in-designated-channel** (real Discord, the Emperor's
actual working channel) **plus** refusal legs: non-designated channel, non-pinned user, group DM.

---

### BLESSED-5 — Acceptance test: the real workflow

**Status:** `BLESSED` (commission) · plan: **D-12**
**Decision.** A mint in the Emperor's designated channel, approved by his echoed word, with full
provenance. The owed T3.5 re-stamp remains the first beneficiary once armed (arming is still a
separate post-promote decision).

---

## Part B — This lane's decisions

### D-1 — The Phase-1.5 plan set is a SEPARATE set, and the parent's plan set stays frozen

**Status:** `DECIDED` · **⚠ AMENDED @ the architect's §8.6 / §11 row 10** (the ownership split
was two-owners-for-one-file)
**Decision.** This lane writes to **`.agents/shared/planning/prompt-assent-channels/`** (new
directory) and **does not edit** the parent set's `plan-overview.md` / `phase1-4-plan.md` /
`decisions.md` / `risks.md` / `architecture-recommendation.md`.

**The ownership split after the amendment — exactly one owner per file:**

| Artefact | Owner | Where it lives |
|---|---|---|
| `docs/runbooks/prompt-assent.md` — the K-3 section, the taxonomy list, the troubleshooting rows, the map documentation | **THIS lane, Phase 4** (file **F-11**) | the repo tree |
| `ceremony-notes.md` — the adapted probe, the staging record, the arming wording | **THIS lane** — a **NEW** file in **this** planning directory (file **F-13**) | this directory |
| the **release-dir** ceremony notes for v0.29.20 (the staged artefact) | the **build lane** | the release dir |
| `CHANGELOG.md` | **THIS lane, Phase 4** (file **F-12**) | the repo tree |

> **⚠ AMENDED @ the architect's §11 row 10.** As first written, this row assigned **both** the
> runbook **and** the ceremony notes to the build lane — **two owners for one file**. The
> architect ruled: the **runbook is this lane's F-11 (Phase 4 writes it)**; the build lane owns
> **only** the release-dir ceremony notes. A **new** `ceremony-notes.md` is created **in this
> directory** (architect §8.6), so the adapted probe **supersedes the parent's DM-only K-3 text
> from the new side** while the parent set stays frozen.

**Rationale.** The parent's `architecture-recommendation.md` wins on conflict for the parent
questions; editing it from this lane would silently invalidate the parent's ruling lineage
(`df8da01a6`). A separate set keeps both auditable, and the phase-1.5 architect artefact will
carry its own header rule.

**Rejected.** (a) Folding Phase 1.5 into the parent set as `phase5-plan.md` — it would require
renumbering the parent's coupled overview tables and would place a *new* capability under a
ruling that explicitly scoped the lane to 1:1 DMs. (b) Editing the parent's `decisions.md` in
place — the parent's ledger is a record of a completed ruling pass; a partial edit breaks the
"record of rulings, not of proposals" property it asserts at its own header.

---

### D-2 — The extension is **predicate-only**: no adapter, no trust-anchor, no schema change

**Status:** `DECIDED`
**Decision.** The change surface is **narrower** than the explorer's baseline suggested:

| Thing | Verdict | Anchor |
|---|---|---|
| Discord adapter | **UNCHANGED** — the guild/channel/author/DM discriminators are already on the message row | `daemon/sources/adapters/discord/adapter.py:1080-1095` |
| `classify_user_origin` (the trust anchor) | **UNCHANGED** — it classifies by *source*, so a guild-channel turn already arms factor 2 | `daemon/tools/upgrade_journal.py:4116-4166` |
| `daemon/manager.py` (the window) | **UNCHANGED** — the window's shape and stamp semantics do not change | `daemon/manager.py:6077-6139` |
| Token store / ledger schema | **UNCHANGED** — see **D-5** | `daemon/repositories/prompt_assent/models.py:149-258` |
| FE approval lane | **UNCHANGED** (byte-identical) — the parent's doctrine | `daemon/routers/prompts.py:616-697` |
| `daemon/api.py` / a new migration | **NOT TOUCHED** — no new table, no new column | see **D-5** |

**Rationale.** This is a **predicate problem, not a data problem**: the only thing refusing a
guild-channel mint today is `is_dm AND NOT is_group_dm`
(`daemon/repositories/prompt_assent/mint_context.py:29-50`). Everything the extension needs is
already daemon-side on the message row.

**Consequence for the change surface (a correction to the explorer's baseline).** The explorer's
[A] change-surface list included "daemon/api.py + NEW migration file ONLY IF a new table rides
create_all (128→129)". **This design adds no table**, so the migration head stays
**`20261110_221500_create_prompt_assent_tokens.sql` (128 files, unchanged)** and `daemon/api.py`
is untouched. The file list in `plan-overview.md` §7 is the binding one.

---

### D-3 — The designation map is **ENV-based**, because the DB settings tier is **agent-writable**

**Status:** **`RULED`** @ the architect's §12 ("**RULED: ENV** — with §3.4's rewritten residual
and §3.5's compensating controls") · **LEADER-RULED** @ **D-15** (NT-2 acceptance + controls)
**Decision.** The designation map is read from **one dedicated environment variable**,
`ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS`, by a **dedicated strict parser** — NOT as a
`PromptAssentConfig` pydantic field, and NOT as a `settings:write` managed key.

**Rationale — the decisive fact.** The DB-backed settings tier is **agent-writable**: the
`settings` tool category exposes `settings_set` (`daemon/tools/settings_tools.py:483`), which
writes any registered managed key through the audited path, and **`agents/ari/meta.json:37` and
`agents/leader/meta.json:15` both allow that category**. Ari is *the agent that mints the
tokens*. A designation map hosted as a managed key would let **Ari designate its own channel**
(or re-point an existing designation to itself) and then mint there — the exact
"agent-settable" outcome the commission forbids. The env tier has **no agent-reachable write
path** (no tool category writes the daemon's `os.environ`).

**Why not a `PromptAssentConfig` field.** The repair-to-fallback machinery lives in the
**tool-layer resolver `_resolve_knobs`** (`daemon/tools/prompt_assent.py:384-463`) — the
`PromptAssentConfig` class itself (`daemon/config.py:3246-3302`) is a plain field container with
no repair logic. That machinery is **not silent** — it warns (`:443-451`) — but it **echoes the
raw value with `%r`** (the warning call at `daemon/tools/prompt_assent.py:443-451`; the %r-bearing format string at `:444-445`; the raw `value` argument at `:448` — `:447` is `name.upper()`, NOT the raw arg), which for a map would **log the operator's configuration** and
violate **S-6**. That value-echo is a **second, stronger** reason for the dedicated parser. The
first reason stands: for a **map**, repair-to-fallback semantics are wrong — a single malformed
entry would repair the **whole map to empty**, i.e. silently drop every designation. The
commission demands "refuse, never silently drop". The map therefore needs an **all-or-nothing,
typed-invalid** parse that the resolver's machinery cannot express (it has no "indeterminate"
state, only "repaired").

**Why env is not worse operationally than the DB tier.** The DB tier's own honest contract is
`applies_at: "next daemon restart"` / `live: false` (`daemon/tools/settings_tools.py:19-24`) — a
DB write is **also** restart-bound. The env route needs the same operator restart and one `.env`
edit, and it keeps the whole designation surface **inspectable in one place** (the daemon's
mode-600 `.env`) instead of spread across a `app_settings` row.

**Cost accepted.** (i) A designation change needs a daemon restart (documented in the runbook);
(ii) per-environment drift (dev/live/demo each carry their own map — the same property the
kill-switch already has); (iii) the map is operator configuration and **must never be committed**
— the plan's out-of-scope list names it as a secret-adjacent artefact.

**Rejected.** (a) `settings:write` managed key — agent-writable, see above. (b) A new dedicated
table + a new operator-only HTTP route — needs a NEW operator write surface; the only agent-free
write path in this tree is "shell access to the DB", a worse operational surface than a `.env`
line, and it would add a **129th migration** for a value with no query need. **The existing HTTP
settings surface is named here explicitly** (`daemon/routers/settings.py:966-969`, `:1023` — PUT /
DELETE gated `settings:write`): it is **registry-gated** (only registered managed keys), and
**with auth off the route is bash+curl-reachable** — which **strengthens** this rejection, since
the DB tier would then be reachable without the env tier's single-file edit. **In this deployment
auth is ON** (`/home/nea/agents-ensemble/.env:126` → `ENSEMBLE_AUTH_ENABLED=true`), so the HTTP
surface is **not** credential-free here — but the bash path is **unchanged by auth**, and the
rejection rests on the ladder, not on the auth posture: `settings_service.py:10-11` resolves
**DB > env**, so a DB row would make the **agent-writable tier authoritative over the operator's
env** — precisely the escalation this decision exists to close. (c) A `PromptAssentConfig` field —
wrong failure semantics (above).

**⚠ THE RESIDUAL, REWRITTEN (architect §3.4 — the earlier "operator-controlled restart" wording
was false).** The restart is **NOT operator-gated**: Ari holds `bash`
(`agents/ari/meta.json:14`), `system_restart` (`:31`) and `service` (`:33`); live restarts are
**provenance-gated** (`daemon/tools/upgrade_tools.py:136`, `:2707-2712`) — armed by a genuine user
turn, **direct or inherited** — while off-live restarts are free (`:136`); and the launcher
re-exports `.env` and respawns the child on exit (`launcher.sh` crash-restart). The live `.env`
is **mode 600, owner `nea`, group `nea`** (verified 2026-11-11) — **mode 600 does not separate
uids**, so it does not exclude the agent's uid. **The true two-part boundary:**
(i) the map leaves **every audited tool surface** (bash-only — invisible to the settings audit
trail); (ii) **even an agent-authored map cannot mint without the pinned human's daemon-derived
turn** (`daemon/tools/prompt_assent.py:674-702`) — agent-settability buys **un-vetted channel
expansion**, **not** a rubber stamp. The compensating controls are in **D-15**; the runbook
**must not** lean on the file-tool deny list.

---

### D-4 — The map's validation is **all-or-nothing, fail-loud at USE time**, with a typed-invalid state

**Status:** **`RULED`** @ the architect's §12 ("**RULED** — with the three amendments of §3.6 and
the absent-identity leg of §6.3")
**Decision.** The parser returns a **typed outcome** — a valid map, or an **invalid** outcome
carrying *every* offending entry. Any invalid entry ⇒ **the whole map is invalid** ⇒ the
designated-channel path is **unavailable** (typed refusal `designation-config-invalid`) and the
DM path is **unaffected**.

**Validation rules (each rule has a reason, none is arbitrary).**

| # | Rule | Rationale |
|---|---|---|
| 1 | **Format** = a JSON **object**; keys = channel ids, values = approver user ids | A JSON array / scalar is a shape error, not a partial map |
| 2 | Ids must match **`^[1-9][0-9]{0,31}$`** — digits only, **no leading zero**, not `0` | The adapter stringifies Discord ids (`daemon/sources/adapters/discord/adapter.py:1083` channel_id, `:1089` user_id), so digits-only is the *proven* shape; the tightened form additionally **kills the leading-zero never-match typo class** (the adapter's `str()` ids can never carry a leading zero) — architect §3.6(a) |
| 3 | **Duplicate keys are refused** (not silently last-wins) | Python's `json.loads` keeps the **last** duplicate silently — that is a textbook "silently drop". The parser must use `object_pairs_hook` and refuse |
| 4 | **Empty map `{}`** = valid, means *no designated channels* | Distinct from *unset*, **behaviourally identical**, **diagnostically distinct**. **⚠ AMENDED @ the architect's §3.6(b): the promised diagnostic had NO specified log line** — the **map-parse log line** (empty / unset / invalid / N entries) is now **specified and mandatory** (Phase 1 task **T1.3**, Phase 4 probe evidence) |
| 5 | **Unset** (env var absent) = valid, means *no designated channels* | The default posture: with no map, the lane is **exactly** the DM-only lane of v0.29.18 |
| 6 | **Same approver on many channels** = **ALLOWED** | The Emperor uses one channel per task category; one human owning many categories is the normal case, not an error |
| 7 | **Same channel pinned twice** = structurally impossible (JSON object) — and if it appears in the raw text, rule 3 refuses it | The map shape carries exactly one approver per channel — **BLESSED-1** |
| 8 | **At most 64 designations** | A bound on the operator's error surface and on the parse; beyond it ⇒ invalid (loud) |
| 9 | A designation naming a **non-existent** channel/user is **not** validated offline | The daemon holds no Discord channel registry; validation would require a live Discord API call. **Fail at use-time**: the entry simply never matches, and if a turn does arrive in it with a wrong user ⇒ `approver-not-pinned` |
| 10 | The map is consulted **only for non-DM turns** | The DM path never reads it — the strongest possible **"DM path unchanged"** proof (**BLESSED-3**, `INVARIANT-DM`). **⚠ AMENDED @ the architect's §11 row 2: the phrase is "DM path *unchanged*"**, not "byte-identical" — the byte claim survives only for the FE surface, the `is_private_assent_channel` function, and (because **T2.8** confines the new detail text to the two new codes) the DM cells' envelope |

**Why fail at USE time and not at BOOT.** A malformed map is a **configuration typo**, not a
security event. Booting the daemon into a refusal because of one bad `.env` line would take the
whole system down for a typo — strictly worse than the lane being unavailable for the designated
channels only. The loudness is preserved by (i) a **typed refusal** naming the class of problem
without echoing the value, and (ii) a **WARNING log naming every offending entry** (by key, not
by value). This is deliberately **different** from the kill-switch's "invalid ⇒ fail closed AT
BOOT" contract (`../prompt-assent/ceremony-notes.md:246-248`) — and the contrast is recorded here on purpose: the
kill-switch is a **single boolean**, where a boot-time pydantic refusal is a clean, cheap signal;
the map is a **structured value**, where a boot abort is an availability catastrophe.

**Rejected.** (a) Per-entry drop-with-warning — a partially applied map is a *silent partial
authority* state the operator cannot reason about ("is channel X enabled or not?"). (b) Boot-abort
— see above. (c) Best-effort repair to `{}` — that is precisely "silently drop".

---

### D-5 — **No schema change**: no new table, no new column, no new migration

**Status:** `DECIDED`
**Decision.** The lane adds **no table, no column, and no migration**. The migration head stays
**`20261110_221500_create_prompt_assent_tokens.sql` (128 files, unchanged)**; `daemon/api.py` is
untouched; `daemon/repositories/prompt_assent/models.py` is untouched.

**Rationale — the ledger already proves it.** `mint_is_dm` is an int 0/1 column
(`daemon/repositories/prompt_assent/models.py:247-250`) and the mint site writes
`mint_is_dm = bool(context.is_dm)` (`daemon/tools/prompt_assent.py:1789`). A designated-channel
mint therefore writes **`mint_is_dm=0`**. Since a guild channel can mint **only** when
designated, the triple (`mint_channel_id`, `mint_is_dm=0`, `mint_user_id`) is already
**proof-by-construction** that the mint happened in a designated channel witnessed by the pinned
approver. The existing `mint_channel_id` and `mint_user_id` columns already carry the pair.
**No new column ⇒ no `_ensure_postgres_columns` entry ⇒ no boot-abort surface** (the parent's
`risks.md` R-7: the ensure list is one transaction for the whole statement list and any entry's
failure aborts boot).

**Consequence.** The Phase-1.5 ledger is *identical in shape* to the DM ledger; an auditor
reads "designated-channel mint" as "`mint_is_dm=0` + a guild channel id". No audit-payload key is
added either (the payload is JSON — additive, but unnecessary given the above).

---

### D-6 — The binding point: **mint leg M-4c** and **approve leg 4f**

**Status:** `DECIDED` · **⚠ AMENDED @ the architect's §5.2 / §6.3** (the heading's "no new
approve-side code" and the body's "only ever ADDS a pass path" were **false** against D-8's own
table, matrix §1.2 and the A-9/A-10 cells — the ledger must follow the body)
**Decision.** The extension binds in exactly **two** places, and the rule has **one**
implementation owner.

**The ONE rule** (in `daemon/repositories/prompt_assent/mint_context.py`, beside the untouched
`is_private_assent_channel`):

> A turn is **private-equivalent** iff `is_private_assent_channel(discord_meta)` **OR**
> (`channel_id` is in the designation map **AND** the turn's daemon-derived `user_id` equals that
> channel's pinned approver).

**ONE classification function owns the whole leg ordering** (architect §4.4 — the DM → group-DM →
absent-discriminator → map ordering is a **code-shape** requirement, not an order instruction).
It is called by **both** gates: the mint gate inside its `not private` arm, the service predicate
inside its own `not private` arm. The mint gate keeps its **DM short-circuit**. **Two
implementations of the ordering are forbidden.**

**Mint side (M-4c, `daemon/tools/prompt_assent.py:1708-1716`).** The existing refusal
`mint-requires-private-channel` fires **only** when the context is neither a 1:1 DM nor a
designated channel. The order of evaluation (the canonical form lives in
[`refusal-cell-matrix.md`](refusal-cell-matrix.md) §1.1):

```
1. context is a proven 1:1 DM?              → MINT (the map is never read)
2. is_group_dm is True (a proven group DM)?  → REFUSE mint-requires-private-channel  (the map is never read)
3. is_dm is not False (absent / non-bool)?   → REFUSE mint-requires-private-channel  (the map is never read)
4. else (is_dm is False ⇒ a GUILD channel):
   4a. map INVALID                          → REFUSE designation-config-invalid   (no token row)
   4b. channel not designated               → REFUSE mint-requires-private-channel (no token row)
   4c. designated, but the identity is ABSENT → REFUSE extraction-incomplete         (no token row)
   4d. designated, user ≠ pinned approver   → REFUSE approver-not-pinned           (no token row)
   4e. designated, user == pinned approver  → MINT
```

**The ordering is load-bearing** — legs 2 and 3 sit **before** the map so that the existing
group-DM pin and the existing absent-discriminator pin stay green **whatever the map contains**,
and leg 1 short-circuits before the map so the DM path never parses it at all.

> **⚠ AMENDED @ the architect's §6.3 (the absent-identity leg — 4c).** Leg **4c** is **new**: an
> explicit absent-identity check **inside** the designated branch, **after** "channel in map"
> (4b), so the undesignated-guild behaviour stays live-identical. It emits the **existing**
> `extraction-incomplete` — the same code the live DM path yields for an absent id
> (`tests/unit/test_prompt_assent_lane.py:587`) and approve leg 4e yields
> (`daemon/tools/prompt_assent.py:1998-2006`). **The absent-identity leg precedes the pinned
> comparison.** Without it **M-8 is unreachable** and the refusal would miscode "no human at all"
> as "a human who is not pinned".

**Approve side (leg 4f, `daemon/tools/prompt_assent.py:2018-2026`; service predicate
`daemon/services/prompt_version_service.py:460-476`).** The service predicate gains the map (and
the approving human's id — taken from `context.user_id`,
`daemon/tools/prompt_assent.py:678`, the single identity parse site `:621-633`, **never** a second
read of `discord_meta["user_id"]`). Its designated branch lives **inside** its `not private` arm.

> **⚠ AMENDED @ the architect's §5.2 — the approve leg DOES emit the two new codes.** The earlier
> wording in this row ("no new approve-side code", "only ever ADDS a pass path", "the only
> approve-side outcome is the operator-fault case") was **false**: the approve leg emits
> **`designation-config-invalid`** (an invalid map ⇒ A-10) and **`approver-not-pinned`** (the
> **re-designation window** ⇒ A-9). The extension adds a pass path **to the privacy leg**; it
> does not add a pass path **only**. **A-9 and A-10 are "no burn" only after the §1.3 4f split
> lands** (`daemon/tools/prompt_assent.py:2021` records unconditionally today).

**What the approve leg already did (unchanged).** Legs 4d (channel) → 4e (approver) → **4f
(privacy)** → 4g (lockout) (`daemon/tools/prompt_assent.py:1984-2036`). For a designated-channel
approval:

- **4d** passes — the approving channel equals the mint's channel (both the designated channel);
- **4e** passes — `mint_user_id` (which, for a designated mint, **is** the pinned approver) equals
  the approving human (`daemon/services/prompt_version_service.py:433-457`);
- **4f** now passes for the designated case.

A **wrong human** approving in the designated room is caught by **4e** with the existing
`token-approver-mismatch` — no new code, and the budget behaviour (burn) is the existing one.

**Zero client-supplied trust.** The mint context is derived **only** from the window's message
row (`daemon/tools/prompt_assent.py:674-702`), and the nested key is
`message_metadata["discord"]["user_id"]` (`daemon/tools/prompt_assent.py:621-633`). The tool's
input schema carries **no** identity fields and is pinned to stay that way
(`tests/unit/test_prompt_assent_user_binding.py:144,:172`). The designation map is
**operator configuration**, never an argument.

**Re-designation between mint and approve (an explicit decision).** If the operator re-designates
a channel to a different approver between the mint and the approval, leg 4f **refuses**
(`approver-not-pinned` — the mint-witnessed human is no longer the room's **current** pinned
approver). Fail-closed: a re-mint (⇒ a fresh human turn by the new pinned approver) is required.
**Carry (architect §5.3 (i)):** after such a refusal the minted token stays **live** until its
60-minute TTL, and if the operator re-pins the channel back, that token becomes usable again
(4e passes, 4f passes) — bounded, because the approving human must still be the mint-witnessed
human. **The runbook must say so.**

**The `is_group_dm` gate (architect §5.3 (ii), rewritten).** The designated branch is gated
on **`discord_meta.get("is_group_dm") is False`** (an identity check on the raw meta dict —
**not** on the retired `text`-type-turn rule). Semantics: the **message path** always carries `is_group_dm`
(False for every guild shape, including threads ⇒ **M-13 stays MINT**); the **interaction path**
builds `discord_meta` **without** the key (`daemon/sources/adapters/discord/adapter.py:1227-1254`),
so the `.get(...) is False` check **fails** and the turn is **never designation-eligible** — it
falls through to the existing refusal **`mint-requires-private-channel`**. **The `text`-turn rule
is retired.**

---

### D-7 — Kill-switch interaction: **additive, orthogonal, and default-OFF**

**Status:** `DECIDED`
**Decision.** The kill-switch is **unchanged** and **orthogonal** to the designation map:

| Condition | Behaviour |
|---|---|
| `ENSEMBLE_PROMPT_ASSENT_ENABLED` unset / invalid | The lane refuses `lane-disabled` for **every** verb, including `mint` — **before** any map is consulted (`daemon/tools/prompt_assent.py:466`). The map is never read. |
| Armed + no map configured | **Exactly the v0.29.18 DM-only behaviour** — behaviour-for-behaviour (code, envelope, token row, ledger shape, budget counter, attempt counter). **⚠ AMENDED @ the architect's §2.6: the earlier "byte-for-byte" wording is withdrawn** — it is not enforceable, because both gate sites are edited; the enforceable guarantee is **`INVARIANT-DM`** ([`refusal-cell-matrix.md`](refusal-cell-matrix.md) §4). This is the default posture of the staged release. |
| Armed + a valid map | DM mints unchanged; designated-channel mints additionally permitted under the pinned-approver rule. |
| Armed + an invalid map | DM mints **unchanged**; designated-channel mints refused `designation-config-invalid`. |

**The map is NOT a second kill-switch.** It cannot enable the lane; it can only *narrow* where a
mint may happen. Conversely, the kill-switch cannot be narrowed by the map: an **empty map does
not arm** anything. **Arming remains one env var, decided by the Emperor, after the adapted K-3
probe** (`../prompt-assent/ceremony-notes.md:308-309`).

**The "kill channel" is still env-based** — this lane does **not** move the kill-switch to the
DB settings surface (the Decision-Module precedent), because the lane's arming posture must
stay **one surface** with the lane's knobs.

---

### D-8 — The new refusal codes: exactly **TWO**, both named here, both placed by Phase 2

**Status:** **`RULED`** @ the architect's §12 ("**RULED: ratified as named** — path-neutral,
side-specific detail text; the 4f split is the binding companion")
**Decision.** The taxonomy grows by exactly **two** codes. The DM-path codes are **untouched**.

| New code | Emitted by | When | Budget (K-9) | In the ledger? |
|---|---|---|---|---|
| **`approver-not-pinned`** | the mint gate (M-4c, leg **4d** — cells **M-7**, **M-14**, **M-16**) **and** the approve leg (4f — cell **A-9**) | the turn is in a **designated** channel but the human is **not** that channel's **current** pinned approver | **NO burn** | **no token row** |
| **`designation-config-invalid`** | the mint gate (M-4c, leg **4a** — cell **M-9**) **and** the approve leg (4f — cell **A-10**) | the operator's map is malformed or contains an invalid entry ⇒ the designated-channel path is unavailable | **NO burn** | **no token row** |

**Both codes are path-neutral by design.** `approver-not-pinned` carries the same semantic on
both paths — *"this human is not the pinned approver of this room"* — so one code serves both
(the mint cell and the rare re-designation cell on the approve path). `designation-config-invalid`
is an **operator-fault** signal that is equally meaningful on both paths. Naming a code
`mint-*` while the approve path can also emit it would be a lie in the vocabulary.

**The approve path's own wrong-human case needs NO new code.** Leg 4e
(`daemon/services/prompt_version_service.py:433-457`) already refuses a different human with
`token-approver-mismatch` **before** leg 4f runs. The approve-side cases that reach the new codes
are the **re-designation window** (**A-9**) and the **invalid-map** case (**A-10**) —
fail-closed by design (`decisions.md` **D-6**).

> **⚠ AMENDED @ the architect's §5.2 — the approve leg DOES emit the two new codes.** The earlier
> wording in **D-6** ("no new approve-side code", "only ever ADDS a pass path", "the only
> approve-side outcome is the operator-fault case") was **false** against this table, matrix §1.2
> and the A-9/A-10 cells. **D-6 has been amended to match this table** — the ledger follows the
> body. The **4f split** (`refusal-cell-matrix.md` §1.3) is the binding companion: without it,
> five approve-side new-code refusals **revoke the Emperor's live token**.

**Side-specific detail text (architect §6.4 — the side ambiguity, resolved at zero taxonomy
cost).** The INFO line already carries `action=mint|approve`
(`daemon/tools/prompt_assent.py:508-543`), so the log disambiguates for free. The **refusal
detail** therefore carries the side: mint — *"this human is not the pinned approver of this
room"*; approve — *"the mint-witnessed human is no longer this room's current pinned
approver"*. **The existing `mint-requires-private-channel` detail string stays byte-identical**
(architect §2.4) — the new wording lives on the two new codes' messages only.

**Why the budget must NOT burn on either.** The K-9 budget is a **token-guessing** brake
(`daemon/tools/prompt_assent.py:277-326`). A mint-side refusal presents **no token**, so there
is nothing to guess — and burning the budget there would be a **DoS primitive**: a stranger who
simply typed in the designated channel would exhaust the relay instance's hourly budget and lock
out the **legitimate** Emperor with `mismatch-budget-exceeded`. Both new codes are therefore
**outside `MISMATCH_CODES`** and outside the placed-codes set that consumes the budget.

**Why `mint-requires-private-channel` is NOT renamed and NOT split.** Splitting it (e.g. adding
`mint-requires-designated-channel`) would grow the taxonomy by three, would **break the existing
group-DM pin** (`tests/unit/test_prompt_assent_mint_gate.py:84-90`) for no diagnostic gain, and
would break the **flip cell** pin (`:102-115`) for a reason that is not a real behaviour change.
Keeping the code stable keeps the existing pins **green and unmodified** — which is itself the
proof that the DM path is unchanged. **⚠ AMENDED @ the architect's §2.4: the existing codes'
string AND detail text are UNCHANGED** — the designated-channel wording lives on the two **NEW**
codes' messages only (`approver-not-pinned` / `designation-config-invalid`). No implementer
freedom to touch the DM envelope. T2.8 confines the new wording to the two new codes' messages.

**Rejected names.** `mint-approver-not-designated` (ambiguous — "designated" describes the
*channel*, "pinned" describes the *human*); `mint-wrong-approver` (not a house-shaped compound);
`channel-not-designated` (does not say who was expected). The chosen names pair with the
commission's own vocabulary ("designated channel", "pinned approver") and with the existing
`token-approver-mismatch`.

---

### D-9 — Threads do **NOT** inherit a designation (exact channel-id match only)

**Status:** **`RULED`** @ the architect's §12 ("**RULED: NO inheritance** — exact channel-id
match") and §7 Q-1 ("**NO**")
**Decision.** A designation is matched against the **exact** `channel_id` of the turn. A Discord
**thread** (`channel_type="thread"`, `parent_channel_id` set —
`daemon/sources/adapters/discord/adapter.py:1086-1088,:1090`) is therefore **not** designated by
inheritance from its parent channel. A thread may be designated **only** by listing its **own**
id explicitly.

**Rationale.** A thread is a sub-conversation with its own id, its own lifetime, its own
visibility (a private thread inside a public channel is not the channel's visibility) and its own
membership. Inheriting the parent's designation would widen the trust surface beyond the channel
the operator actually named — the exact failure mode the pinned-approver model exists to prevent.
The doctrine's *"we consider channel is private/dm **for now**"* is a claim about **channels**, not
about sub-conversations inside them.

**Operational note (recorded, not a defect).** Threads are ephemeral; a designation that names a
thread id is short-lived by nature. The runbook therefore advises designating the **parent
channel** and having the Emperor type **in the channel** (not in a thread) for mint/approve turns.

---

### D-10 — Supersede decision for v0.29.18: **LEFT STANDING**; the supersede target is **v0.29.20** (re-pointed from v0.29.20)

**Status:** **`RULED`** @ the architect's §12 ("**RULED: LEFT STANDING** — with §9.3's protection
statement and survival math") *(the staging execution is the build lane's; the plan records the
ruling so the build lane does not have to re-derive it)*
**Decision.**

| Field | Value |
|---|---|
| **New staged version** | **`v0.29.20`** (⚠ AMENDED @ 2026-11-11T19:30:42Z: v0.29.20 taken by a concurrent lane — tag `1132fa4cb`, tip of origin/latest, **not this branch**; verified: the tag exists locally AND on origin; v0.29.20 free locally AND on origin. **T4.1 executes the collision rule at build time**: on collision re-bump to the next free slot; NEVER re-cut a tag; push by name.) |
| Slot verification | `git ls-remote --tags origin 'v0.29.20*'` → **1132fa4cb** (the concurrent lane took it 2026-11-11T19:30:42Z); `git ls-remote --tags origin 'v0.29.20*'` → **empty** (free at the time of this amendment). **T4.1 re-verifies v0.29.20 at build time.** |
| Ancestry | `git merge-base --is-ancestor v0.29.18^{commit} HEAD` → **exit 0** — v0.29.18's content **already rides** this branch |
| **v0.29.18 treatment** | **LEFT STANDING** (release dir kept, local tag kept, remote tag untouched) |
| Promote | **NOT promoted** by this lane; `current` stays where it is |

**Rationale for LEFT STANDING (against the dispose precedent).** The house has both precedents:
v0.28.17 was **disposed** (release dir + local tag deleted; remote tag left as audit trail) and
v0.28.18 was **left standing** (its content rides the next version). v0.29.18 combines **both**
properties, and the combination decides it:

1. **Its content is already an ancestor of the new base** — so there is nothing to preserve *by
   disposal*; disposal would only delete the release dir and the local tag.
2. **It is ceremony-ready** — if the Emperor prefers the DM-only lane (or the designated-channel
   work slips), **v0.29.18 remains promotable as it stands**. Disposing it destroys that option
   for no gain.
3. **The house combo-practice** — *content rides the next version; the release dir becomes an
   eviction candidate at the successor's promote; the tag stays as audit trail.*

**⚠ One ceremony line (the moved-latest note):** latest has MOVED past our base during the pass
(`efa8aaae2` → … → `1132fa4cb`); the final merge step (giter) reconciles the moved latest;
**staging builds from the merged tip, not the stale base**. Nobody is surprised.

**Eviction, recorded for the promote lane (not this lane's action) — amended @ the architect's
§9.3.** The host currently holds **5** release dirs (`v0.29.7`, `v0.29.13`, `v0.29.15`,
`v0.29.17`, `v0.29.18`) against a keep-3 policy; **staging takes the count 5→6 and queues
nothing** (`stage.sh` never calls `retention_evict` — verified: `grep -c retention_evict
scripts/upgrade/stage.sh` = **0**; the eviction runs only from `promote.sh:771` and `:927`, with
`RETENTION_KEEP=3` at `scripts/upgrade/lib.sh:375` and the routine at `lib.sh:6857-6917`).

**The corrected survival math.** At the **first** v0.29.20 promote: evict 3 (`v0.29.7`,
`v0.29.13`, `v0.29.17` — the three oldest non-pinned) ⇒ **v0.29.18 survives**. It becomes the
candidate at the **third** promote (after v0.29.20 and v0.29.21) — **it survives exactly two
further promotes, protected by nothing** (`retention_evict` pins only `current`/`previous`; a
script pin is an upgrade-machinery change with its own floor/stamp bump — **out of this lane's
scope**). **The earlier "v0.29.13 is the eviction candidate" line was true for the first promote
only and is NOT a protection** — stated here so it is not read as one.

**Three records the ceremony note must carry (architect §9.3):** (i) the **operator rule** — no
other promote until the designated-channel decision is made; (ii) the **two-promote survival
math**; (iii) the recoverability note — *"if evicted, re-stage from `df4a92732` — the tags
survive, the option is recoverable at one build+stage cycle"*. **There is no disposal step at
all** (the inverse of v0.29.17's disposal note) — the ceremony note must say so explicitly.

**Tag hygiene.** Push tags **by name**, never `--tags` (house rule). The remote tag is never
deleted — it is the audit trail.

---

### D-11 — The K-3 live probe is **ADAPTED**, and the arming gate wording changes with it

**Status:** **`RULED`** @ the architect's §12 ("**RULED** — in the §8 shape (two-phase, three
armed states, second-account, triage table)")

> **⚠ AMENDMENT NOTE (N1 — the five→six delta).** The architect's §8 said "**all five outcomes
> recorded**" and "**the lane must not be RELIED UPON until the five outcomes are recorded**".
> This lane's probe is **SIX** (S5 of the fold-back pass added leg 4 — the non-designated
> guild-channel refusal — as its own refusal leg, because leg 4's expected code is identical to
> what a mistyped map key produces and cannot be distinguished in a single phase). The architect
> artefact is **read-only** and its text stands as history; the amendment is recorded here rather
> than by editing the architect's file. Every "five outcomes"/"5-leg" reference in this set has
> been swept to "six"/"6-leg".
**Decision.** The K-3 gate becomes a **two-phase** probe with **three armed states**, and the
arming wording changes from *"armed by the Emperor, after the K-3 live DM probe"* to *"armed by
the Emperor, after the K-3 live designated-channel probe"* — superseded from the new side by
this directory's `ceremony-notes.md` (file **F-13**); the parent's DM-only text
(`../prompt-assent/ceremony-notes.md:236-244`, `:308-309`) stays frozen as history.

**Why two phases are mandatory (architect §8.1).** Leg 4's expected code
(`mint-requires-private-channel`) is **identical** to what a mistyped map key produces in the
designated channel — a single-phase list **cannot** separate *"map says no"* from *"id typo"*.
Only the A/B pair on the **same channel id** isolates the variable.

| Phase | Map state | Legs (in order) | Purpose |
|---|---|---|---|
| **A** | **unset** | **2** group DM → **4A** the *designated* channel with the map unset (**must refuse**) → **1** DM | the baseline: with no map, nothing is designated |
| **B** | **set** | **3** designated channel (**must mint**) → **5** non-pinned user (**must refuse**) → **4** non-designated guild channel (**must refuse**, the narrowness proof) | the delta: the same channel id now mints — for the pinned approver only; **leg 4 belongs in Phase B** (only there is a channel designated, so the narrowness proof is meaningful) |

**Three armed states, not one bit (architect §8.2).** `ARMED-TENTATIVE`
(`ENSEMBLE_PROMPT_ASSENT_ENABLED=1` + restart — the probe's precondition; a mint alone grants
nothing; the exposure is the room disclosure, **D-14**) → `ARMED-OPERATIONAL` (all six outcomes
recorded + the Emperor declares reliance) → `DISARMED` (unset + restart ⇒ `lane-disabled`). The
probe runs **inside ARMED-TENTATIVE** and **ends with an explicit disarm-or-ratify step**. The
notes must say: *"**the lane must not be RELIED UPON until the six outcomes are recorded**"* —
"arming blocked until the probe" is **circular** as written (the probe requires arming).

**The second human must be a second ACCOUNT (architect §8.4).** A second *device* shares the
`user_id` and degenerates leg 5 into leg 3; **the Emperor's own alt account is the strongest
choice** — it proves the pin binds the **account**, not the person.

**An unrun leg 2/5 is NOT a pass.** The gap is recorded as an **explicit Emperor acceptance**;
the multi-occupant discriminator then stays **unit-proven only** — exactly the class K-3 exists
for.

**Per-leg evidence (architect §8.3).** Every leg's PASS = the exact code **plus** the token-row
shape **plus** the INFO line, captured together. Leg 1 → a row with `mint_is_dm=1`,
`mint_user_id` = the probe account, `status='live'`, `ttl_expires_at` = +60 min, `attempts=0`;
leg 2 → `mint-requires-private-channel`, **no** token row, **no** audit row; leg 3 →
`mint_is_dm=0`, `mint_channel_id` == the map key, `mint_user_id` == the map value (a
`mint_is_dm=0` row with the **wrong** channel = minted in a thread or the wrong room — **FAIL**);
leg 4A → the Phase-A refusal in the same channel; leg 4 → the non-designated guild-channel refusal; leg 5 → `approver-not-pinned` **with
`flags.mismatch=0`** (proves no burn).

**Code-family triage is the operator's first move (architect §8.3).**
`mint-requires-private-channel` is emitted **only** at the M-4c privacy gate
(`daemon/tools/prompt_assent.py:1711`) — its presence proves the window was live and the item
pending (a window-family code is an **availability** miss, not a leg failure);
`designation-config-invalid` ⇒ a **configuration** failure (the WARNING names every offending
key); `mint-live-token-exists` ⇒ **cleanup**, not probe failure.

**Preconditions the plan originally omitted (architect §8.4) — now binding.**

- **≥ 2 pending CORE `prompt_versions`** (Emperor-created — the artifact path is human-session-only)
  and a **different subject item per minting leg** (a live token blocks re-mint,
  `daemon/tools/prompt_assent.py:1700-1705`; `force=true` supersedes).
- Operator **READ** access to `prompt_assent_tokens` and to the message rows'
  `metadata["discord"]`.
- **Discord Developer Mode ON** (ids are copy-paste digits).
- **The relay bot must be present in a NON-designated guild channel at probe time** — leg 4's
  precondition; **absent ⇒ INCONCLUSIVE, not a pass.**

**Hygiene and fail-safe (architect §8.5).** Refusal-only legs first, minting legs last, one
subject item each; the **single-occupant channel** for leg 3 (the mint response posts the token
**and** the canonical render into the room — `daemon/tools/prompt_assent.py:1249`, `:1252-1254`);
consume or TTL-expire a token before reuse; **never** paste a live token into a second channel.

**NT-5 — the last-speaker-wins rule.** A guild channel is **one instance for all humans**
(`daemon/sources/adapters/discord/adapter.py:909-915` (the bot-skip gate), `:218-220` (the skip default)), and the window is re-armed on
every non-silent dispatch (`daemon/manager.py:6126`, `:11530`) — so a **bystander's reply
between the Emperor's turn and the tool call re-points the window**, and the Emperor's own
attempt then refuses `approver-not-pinned`. Fail-closed, but a cheap continuous DoS on the lane.
**Rule:** the Emperor must be the **last speaker**; a bystander's reply within the 600 s
freshness bound invalidates the attempt — **re-run, do not adjudicate**. (Future hardening —
a per-user window, or a "the window row must be the pinned approver's" guard — is **out of this
lane's scope**; see `risks.md` Part 5, NT-5.)

**The map-parse log line is MANDATORY probe evidence.** It is the only discriminator between
*"map says no"* and *"map is broken/empty"* — and **D-4 rule 4's promised diagnostic had no
specified log line at all** (architect §3.6(b)). It is now specified: **T1.3** (Phase 1) emits
the parse state (**empty / unset / invalid / N entries**) plus a content-free fingerprint, and
the boot-time EFFECTIVE-MAP line is **D-15**'s control (a).

---

### D-12 — The acceptance test is the **real** workflow, and the T3.5 re-stamp stays a **subject**

**Status:** `DECIDED`
**Decision.** The acceptance subject is: **a mint in the Emperor's designated channel, approved by
his echoed word, with full provenance** — the ledger reconstructs *who* (the pinned approver),
*from where* (the designated channel), *in which message*, *on which item*, *at what time*, and
*which relay agent*.

The owed **T3.5 re-stamp** (`agents/ari/tools_note.md`; `grep -c memoir_about` = **0** today) is
the **first beneficiary** once the lane is armed — but it is **not** a lane deliverable: the
artifact write path is **human-session-only** by design
(`daemon/tools/prompt_artifacts.py` `check_write`), and it executes **only after** a staged
release is promoted. Arming is a separate post-promote decision.

---

### D-13 — **Parent reconciliation**: this lane amends the parent's AR-1 binding and its §3 taxonomy

**Status:** **`RULED`** @ the architect's §12 ("**RULED: add** — the parent-reconciliation row
(§11 row 8)")
**Decision.** The parent lane's frozen artefacts are **amended by this lane** and the amendment is
recorded **here** (the parent set stays frozen — **D-1**).

| # | What is amended | Where the parent states it | The amendment |
|---|---|---|---|
| **1** | The **AR-1 mint-context binding** — *"Token bound to mint `(source_id, channel_id, 1:1-DM, user_id)`"* | `../prompt-assent/architecture-recommendation.md:63` | The **`1:1-DM`** element becomes **`1:1-DM` OR a designated channel whose pinned approver is the turning human**. The channel **and** user binding is **unchanged**; only the set of surfaces that can satisfy the privacy element grows — and only under an operator designation. The parent's AR-1 legs 1 and 3 (same source+channel, same daemon-derived `user_id`) are **untouched** and remain the approve-side binding |
| **2** | The parent's **§3 refusal taxonomy** (27 codes, "verbatim") | `../prompt-assent/architecture-recommendation.md` §3 | **Amended by this lane: +2 codes** (`approver-not-pinned`, `designation-config-invalid`) → **29**. The census's `ARCHITECT_REFUSAL_TAXONOMY` docstring must be **re-worded** from *"the COMPLETE architect §3 refusal taxonomy (verbatim)"* to *"the architect §3 refusal taxonomy **as amended by the prompt-assent Phase-1.5 lane** (+2 codes)"* — **T2.6** owns the re-wording |
| **3** | The parent's **§8.2 item 2** — *"Guild-channel approval. Phase 1 is 1:1-DM-only (AR-1). If the Emperor intends to approve from guild channels, the pinned-approver design must be commissioned BEFORE Phase 1 ships."* | `../prompt-assent/architecture-recommendation.md:546-548` | **The hook this lane satisfies.** The Emperor's 2026-11-11 ruling commissions the pinned-approver design; **this lane IS that design**, staged as **v0.29.20** on top of the parent's Phase-1 release (**v0.29.18**). The parent's Phase 1 shipped as v0.29.18; the guild-channel surface ships as its successor, **not** as a rewrite of it |

**Why this row exists (architect §10 NT-10).** Without it, this lane would silently amend the
parent's frozen rulings — the exact "ledger must follow the body" failure in its other form
(**plan-body-ledger-agreement**). **The parent set is not edited; the amendment is recorded
here, in the ledger that governs it.**

---

### D-14 — **LEADER RULING 1** (NT-1): the in-room token/diff delivery posture is **ACCEPT-AND-RECORD**

**Status:** **`LEADER-RULED`** (leader/dispatcher, 2026-11-11) — the architect explicitly left
this to the Emperor (§13.1); the leader ruled it on the Emperor's behalf.
**Decision.** The **delivery mode** for a designated-channel mint is an **EXPLICIT, overridable
default of *accept-and-record*** — **not** a hidden assumption. The Emperor's own ruling
("we consider channel is private/dm for now") makes in-room delivery the **channel-analog of the
DM behaviour**.

**What accept-and-record means, concretely.** The mint response carries the grouped token
(`daemon/tools/prompt_assent.py:1249`) **and** the canonical render (`:1252-1254`), and the relay
posts it **into the channel**. Under accept-and-record that is **accepted**, and the plan
**records** it as an accepted exposure rather than burying it.

**The two hygiene notes the ruling requires on the record.**

1. **Every occupant of the channel sees the token + the full canonical prompt diff** — plus any
   reading bot. The lane's content-free discipline (S-6) governs **refusals and logs**, **never
   the mint response** — the mint response is a *delivery*, not a log.
2. **A bystander who relays the token is holding the REAL token.** A "garbage token" attempt
   from a bystander is a genuine mismatch ⇒ the durable counter climbs to the revoke threshold
   of 5 (`daemon/tools/prompt_assent.py:252`) and the K-9 budget to the lockout at 10/hour
   (`:274-326`) ⇒ a **lockout DoS on the legitimate Emperor**. This is why the probe's hygiene
   rules matter (**D-11**): single-occupant channel for leg 3, consume/TTL-expire before reuse,
   never paste a live token into a second channel.

**Overridable at arming time — the flip is explicit, in the ceremony notes.** The ceremony note
(this directory's `ceremony-notes.md`, file **F-13**) must carry a **flagged, named** delivery
mode with the two alternatives, so the Emperor can flip it **at arming time** without a code
change:

| Mode | What it does | Cost |
|---|---|---|
| **`accept-and-record` (the DEFAULT, this ruling)** | the token + the canonical render are posted in the channel, exactly as in a DM | the two hygiene notes above |
| **`dm-deliver`** | the token + the diff are delivered to the Emperor's DM instead of the room | requires a DM-delivery path the lane does not have today — a **new** surface, out of this lane's scope |
| **`redact-render`** | the room receives the token without the canonical render (the diff is fetched separately) | a second delivery surface — out of this lane's scope |

**The two alternatives are recorded, not built.** If the Emperor chooses one at arming time, it is
a **follow-up commission**; this lane ships the default and the flag.

---

### D-15 — **LEADER RULING 2** (NT-2): the agent-reachable map path is **ACCEPTED WITH CONTROLS**

**Status:** **`LEADER-RULED`** (leader/dispatcher, 2026-11-11) — the architect's §13.2 left the
acceptance to the Emperor; the leader ruled it.
**Decision.** The **ENV surface stands** (**D-3**), and the residual — an agent-authored `.env`
edit becoming effective at an agent-triggerable restart — is **accepted with three controls**.

**The residual, stated honestly (architect §3.4).** The restart is **provenance-gated, not
operator-gated**: Ari holds `bash` (`agents/ari/meta.json:14`), `system_restart` (`:31`) and
`service` (`:33`); live restarts are armed by a genuine user turn, direct **or inherited**
(`daemon/tools/upgrade_tools.py:136`, `:2707-2712`); the launcher re-exports `.env` on
crash-restart. The live `.env` is **mode 600, owner `nea`, group `nea`** (verified 2026-11-11) —
**mode 600 does not separate uids**. **Auth is ON** in this deployment
(`/home/nea/agents-ensemble/.env:126` → `ENSEMBLE_AUTH_ENABLED=true`), so the HTTP
`settings:write` surface is **not** credential-free here — but **the bash path is unchanged by
auth**. The true boundary is two-part: the map leaves **every audited tool surface** (bash-only),
**and** even a malicious map cannot mint without a genuine-user turn **by the pinned human** in
the designated channel (`daemon/tools/prompt_assent.py:674-702`).

**Control (a) — the boot-time EFFECTIVE-MAP log line.** At boot, the daemon emits **one** line
naming the map's **entry count** and a **content-free fingerprint** (the first **12** hex of the
canonical JSON's sha256 — **one length, everywhere**; T2.11's `map_digest=<12 hex>` uses the same) —
never a value, never a channel id. An unvetted expansion becomes
**VISIBLE** in the boot log the operator already reviews, even though it is not *prevented*.
**Owned by Phase 2 — task T2.10** (the resolver emits it) **and asserted by Phase 3 — T3.9**.

**Control (b) — the effective map digest in the mint provenance. ⚠ THIS GOES BEYOND THE
ARCHITECT'S §3.5 — it is an owned plan requirement, not a nice-to-have.**

| Field | Value |
|---|---|
| **The requirement** | every mint records the **effective map's digest** (the same content-free fingerprint as control (a)) in the mint's **provenance entry** |
| **Where it lands (primary)** | the mint's **content-free INFO log line** (`daemon/tools/prompt_assent.py:508-543`) gains a `map_digest=<12 hex>` field — the per-mint provenance entry. **No schema change**: the token table gains **no column** |
| **Where it lands (secondary)** | the **approve/reject audit payload** (`prompt_audit_events.payload`, JSON — additive, **no migration**) carries `map_digest`, so the **decision's** provenance in the queryable ledger names the map that was in force |
| **Why NOT the token row** | a `map_digest` column on `prompt_assent_tokens` would need a **129th migration** — which **D-5** and the architect's §9.5 forbid (the migration head stays `20261110_221500`, 128 files). The token row's bytes are unchanged |
| **The owned tasks** | **Phase 2 — T2.10** (the boot-time EFFECTIVE-MAP log line — control (a)); **Phase 2 — T2.11** (the effective map digest in the mint provenance — control (b)); **Phase 2 — T2.12** (the effective map digest in the decision provenance — control (b)) |
| **The owned ACs** | **Phase 2 — AC15** (control (a): the boot line carries the entry count + the content-free fingerprint) · **Phase 2 — AC13** (control (b): the mint INFO line carries the digest) · **Phase 2 — AC14** (control (b): the audit payload carries the digest) |
| **The owned tests** | **Phase 3 — T3.9** (the digest is stable for a given map, changes when the map changes, and never contains a channel id, a user id or the raw value) |

**Control (c) — the S-1 residual is rewritten** per the architect's §3.4 (see **D-3** and
`risks.md` S-1). The runbook **must not** lean on the file-tool deny list
(`agents/ari/meta.json:50` denies `edit_file`/`write_file` — but `bash` is held at `:14`, and a
deny list is not a boundary).

**Standing mitigation (recorded on the face of the ruling).** **Even a malicious designation
cannot mint without a genuine-user turn by the pinned human in the designated channel.** The map
expands *where* a mint may happen; it never supplies the *who*.

**Parked successor — OUTSIDE this lane's scope.** A **root-owned** map file (ownership, not mode,
excludes the agents' uid) would close the bash path entirely. It is **named here as a follow-up
option** and is **not** built by this lane: under root-ownership its risk rises and it contends
with ENV (architect §3.5 (iii)). If the Emperor ever withdraws this acceptance, that is the
design to commission.

---

### D-16 — **LEADER RULING 3** (NT-9): `ens_env_read` does **NOT** redact the map variable

**Status:** **`LEADER-RULED`** (leader/dispatcher, 2026-11-11) — the architect's §13.3 left the
minor call open.
**Decision.** The designation map variable is **NOT** added to `ens_env_read`'s redaction list.

**Linkage (N10).** The runbook's "the map is not a secret" note (file **F-11**, `docs/runbooks/prompt-assent.md`,
the map section) names who may read it (any `ens-env`-holding agent, e.g. `agents/worker/meta.json:32`)
and states the rationale in the operator's own words; the variable's exposure is **deliberate**, not
an oversight, and the note must say so.

**Rationale (recorded).** The map is **not a credential**. Knowing *which* channels are designated
does **not** enable minting: the authorisation is the **author-id equality**, checked daemon-side
against the **message author** (`daemon/tools/prompt_assent.py:674-702`, `:621-633`) — never
against a configuration value the reader supplies. A reader who learns the map still cannot mint,
still cannot approve, and still cannot forge the nested `metadata["discord"]["user_id"]` anchor
(the HTTP path carries **no** metadata — `daemon/models/message.py:78-103`). **The policy
(the Emperor's private-channel convention) is the sensitive thing; the ids are quasi-public.**
Redacting the variable would create a false impression of secrecy while providing **no** security
benefit.

---

### D-17 — **LEADER RULING** (the council's design decision): **withdrawn designation ⇒ KEEP BURN**

**Status:** **`LEADER-RULED`** (leader/dispatcher, 2026-11-11) — the council's design decision,
closed by the leader.
**Decision.** A **withdrawn designation** (the operator re-pins or unpins the channel between the
mint and the approval; the approving human is **no longer pinned**) is a **user** attempting to
mint/approve in a formerly-valid room — a **user-side mismatch** — and the token **BURNS**
(durable counter, mismatch budget). This is the **KEEP BURN** ruling.

**Linkage (N10).** The ruling is operative through **A-12** in the approve matrix
([`refusal-cell-matrix.md`](refusal-cell-matrix.md) §3) and **T3.3's burn assertions** (extended to
A-5…A-8 and A-12); the ceiling is that **only** the durable-counter / threshold-5 revoke path
burns — the mint path never consults the budget (architect §6.1).

**The asymmetry, recorded explicitly (the council's own wording).**

| Cell | Class | Why the burn differs |
|---|---|---|
| **A-10** — an **invalid map** | **operator** fault | the operator's configuration is broken ⇒ **no user burn** (`designation-config-invalid` is outside `MISMATCH_CODES`) |
| **A-12** — a **withdrawn designation** | **user**-side mismatch | a user is attempting to act in a formerly-valid room ⇒ **burns** (`token-channel-mismatch` is mismatch-class) |

**The new cell.** **A-12** is added to the approve matrix (architect §6.5's dangling-cell
resolution + the council's ruling); its test asserts the **burn** (counter++, budget burn) —
**T3.3's burn assertions are extended to A-5…A-8 and A-12**, and the no-burn assertions for
A-9, A-10 and A-11 stand.

**Why this is not a trust-model change.** The ruling does not touch the architecture — it
classifies an existing refusal path. `token-channel-mismatch` is **already** mismatch-class and
**already** burns; A-12 simply names the scenario under which it fires. No new code, no new
budget rule, no change to the mint path.

---

### D-18 — **LEADER RULING A** (blocker 1): `DesignationResult` is **SIDE-NEUTRAL**

**Status:** **`LEADER-RULED`** (leader/dispatcher, 2026-11-11 — the approver iteration-002 blocking issue #1)
**Decision.** `DesignationResult` becomes **side-neutral**: the variants are **neutral leg identities** —
`PASS | NOT_DESIGNATED | GROUP_DM_SHAPE | APPROVER_NOT_PINNED | ABSENT_DISCRIMINATOR | CONFIG_INVALID |
ABSENT_IDENTITY` — **not** side-specific refusal codes. Each gate maps the neutral leg to **its own
side-specific code** via **ONE pinned per-side mapping table**:

| Neutral leg | **Mint-side code** | **Approve-side code** |
|---|---|---|
| `PASS` | — (proceed) | — (proceed) |
| `NOT_DESIGNATED` | `mint-requires-private-channel` | `token-channel-mismatch` |
| `GROUP_DM_SHAPE` | `mint-requires-private-channel` | `token-channel-mismatch` |
| `APPROVER_NOT_PINNED` | `approver-not-pinned` | `token-approver-mismatch` |
| `ABSENT_DISCRIMINATOR` | `mint-requires-private-channel` | `token-channel-mismatch` |
| `ABSENT_IDENTITY` | `extraction-incomplete` | `extraction-incomplete` |
| `CONFIG_INVALID` | `designation-config-invalid` | `designation-config-invalid` |

**The mapping table is defined once, owned by a NAMED task (T2.3), pinned by its OWN AC (AC18):
"every neutral leg maps to exactly one code per side; the predicate itself NEVER returns a
side-specific code."** The mint-side mapping table lives beside the mint gate (T2.2); the
approve-side mapping table lives beside the service predicate (T2.3).

**Rationale.** A single pure function cannot return both `mint-requires-private-channel` (the
mint-side refusal for an undesignated surface) and `token-channel-mismatch` (the approve-side
refusal for the same three input legs) — the two gates demand different codes for the same neutral
leg. Making `DesignationResult` side-neutral decouples the ordering (one function, one owner) from
the code vocabulary (two pinned per-side tables).

---

### D-19 — **LEADER RULING B** (blocker 5): `minted_external_user_id` for channel mints is the **full composite**, declared **AUDIT-ONLY**

**Status:** **`LEADER-RULED`** (leader/dispatcher, 2026-11-11 — the approver iteration-002 blocking issue #5)
**Decision.** `minted_external_user_id` for channel mints = the full composite
**`channel:<guild_id>:<channel_id>:<user_id>`** string, declared **AUDIT-ONLY**:
(i) a pinned **no-in-tree-consumer guarantee** — `_parse_external_user_id` is **NEVER** applied to
`minted_external_user_id` (grep-pin + test pin, owned by **T2.15**); (ii) **operational identity
remains `mint_user_id`** (daemon-side snowflake equality — the approve legs 4d/4e use it,
**unchanged**); (iii) **M-19 behaviour UNCHANGED** (`extraction-incomplete` when the nested user id
is absent); **AC17 additionally asserts** the audit-only contract + the no-consumer pin.

**Why NOT the adapter's `{guild}:{channel}` mapping-key grammar.** The adapter's
`KEY_ALLOWED_CHANNELS` mapping key is **lossy** (it omits the user id — the approving human is not
identified in the value, which would make the audit provenance ambiguous across multiple humans in
one channel). The composite form is **non-parseable by design** — declared AUDIT-ONLY so no
downstream consumer can accidentally treat it as an identity.

---

### D-20 — **LEADER RULING C** (blocker 3): the effective-map log line moves to **FIRST-RESOLUTION + ON-DIGEST-CHANGE inside the cached-lazy resolver**

**Status:** **`LEADER-RULED`** (leader/dispatcher, 2026-11-11 — the approver iteration-002 blocking issue #3)
**Decision.** D-15 control (a) moves from "boot-time effective-map line" to **FIRST-RESOLUTION +
ON-DIGEST-CHANGE logging inside the cached-lazy resolver** in `prompt_assent.py` (a file the lane
already touches): log the effective map (or its digest) when first successfully resolved in a
process AND whenever the effective digest changes between resolutions. **NO boot hook**,
**NO `daemon/api.py` edit**; **PR-1 raise-on-invalid UNTOUCHED** (the log fires only on successful
resolution; invalid maps keep their typed-invalid refusal path). Re-wrds **AC15** + **T2.10** to
this contract; **D-15's control-(a) row** amended.

**Rationale.** `prompt_assent.py` has **zero** lifespan/startup hooks (the approver verified:
grep=0), and `daemon/api.py` is fenced out (**O-4**). An import-time side effect would **nullify
PR-1/AC4** (every test import would parse the map). First-resolution logging fires at the same
moment the map is actually read — and on-digest-change logging makes a mid-flight map change
**visible** without a boot hook.

---

## Part C — Open questions

> **⚠ RESTRUCTURED @ the architect's §11 row 12.** Part C is **split**: **C-1** holds the
> questions that were *genuinely open* at the architect's ruling; **C-2** holds the questions that
> are **settled for the record**. The **commission-ref** column carries the commission's own
> numbering — the two namespaces mis-map (**the commission's "Q-4" is the *plan-raised* env-surface
> question, not the commission's**) and that mis-map is now explicit rather than latent.

### Part C-1 — Genuinely open at the architect's ruling (the architect has ruled; these are now recorded as rulings)

| # | Commission ref | Question | **Ruling** | Where it landed |
|---|---|---|---|---|
| **Q-1** | — | Should a **thread** inside a designated channel inherit the designation? | **NO** — exact channel-id match only; a thread may be named explicitly (**M-12**/**M-13**) | **D-9** (`RULED` @ the architect's §12, §7 Q-1) |
| **Q-4** | — | Is the **env surface** acceptable given the restart requirement? | **YES** — the DB tier is **also** restart-bound (`daemon/tools/settings_tools.py:139`, `:379-381`) **and** agent-writable; env strictly dominates | **D-3** (`RULED` @ the architect's §12, §7 Q-4) |

> **Reconciled, not open: restart vs call-time (§3.7).** *"Call-time read"* = the resolver reads
> `os.environ` **per tool call** (the kill-switch idiom,
> `daemon/tools/prompt_assent.py:404`, `:466`); *"restart-bound"* = `os.environ` itself changes
> only at process start (no runtime writer exists in `daemon/` — only the boot `setdefault` at
> `daemon/__main__.py:48`). **Both are true.** The operator procedure is **edit `.env` → restart
> → probe leg 3**. **One pin is required: the resolver must read `os.environ` DIRECTLY — never
> via a pydantic-settings dotenv source.** `PromptAssentConfig` is instantiated per call
> (`:431`), so a dotenv source would **hot-reload the map without a restart** and silently break
> the restart-bound story. **T2.1** owns the direct-read requirement.

### Part C-2 — Settled for the record

| # | Commission ref | Question | **Ruling** | Where it landed |
|---|---|---|---|---|
| **Q-2** | commission Q-2 | Should the map support **more than one approver per channel**? | **NO** — the map **shape** is the enforcement point (unrepresentable beats forbidden-but-representable). A future list value fails the id regex ⇒ `designation-config-invalid` — a self-diagnosing migration | **BLESSED-1** (upheld; architect §7 Q-2) |
| **Q-3** | commission Q-3 | Should a designated-channel mint be allowed for a **non-CORE** artifact? | **UNCHANGED** — the `artifact_class` gate is channel-independent and fires **before** M-4c; the extension changes *where* a mint may happen, never *what* | **O-5** / **D-5**; the gate's actual anchor is `daemon/tools/prompt_assent.py:1681-1686` (the earlier `:1685-1692` was stale) |
| **Q-5** | commission Q-5 | Should the map be readable from the DB as a **secondary tier**? | **NO** — the ladder is **DB > env** (`daemon/services/settings_service.py:10-11`): even a read-only mirror puts the **agent-writable** tier in the resolution chain, and a pure mirror adds permanent staleness/precedence ambiguity | **D-3** (architect §3.1, §7 Q-5) |

---

## Part D — Open items **resolved by the dispatcher** (2026-11-11)

| # | The item | The answer (recorded) | Where it landed |
|---|---|---|---|
| **OD-1** | The live `.env`'s mode/ownership and this deployment's `ENSEMBLE_AUTH_ENABLED` posture (the architect's §14 open item — read-only-checkable in one command) | `/home/nea/agents-ensemble/.env` → **mode 600, owner `nea`, group `nea`**; **`ENSEMBLE_AUTH_ENABLED=true`** at **line 126**. **Mode 600 does NOT separate uids** — it confirms the architect's §3.4 reading. **Auth is ON** ⇒ the HTTP `settings:write` surface is **not** credential-free here; **the bash path is unchanged by auth** | **D-3** (the residual + rejected (b)) · **D-15** (the residual) · `risks.md` **S-1** — **the architect's §14 open question is STRUCK** |
| **OD-2** | The commission's **erratum**: `EXECUTOR_ENV_ALLOWLIST` lives at `daemon/tools/upgrade_journal.py:2593`, **not** `job_queue.py` | **Carried upward.** Re-verified in the worktree: the constant is declared at `daemon/tools/upgrade_journal.py:2593`. No file in this set cites the wrong location — the correction is recorded so no future lane repeats the commission's guess | this row; the architect's §3.3 ("the executor allowlist … at `daemon/tools/upgrade_journal.py:2593`, not `job_queue.py` as the commission guessed") |
| **OD-3** | The sweep's **R-17** cite (the architect's §11 row 32 — "needs a 30-second re-check … treat as unverified") | **Re-checked 2026-11-11.** The freshness-flake defect is at **`../prompt-assent/ceremony-notes.md:256-259`** (the parent's row 8, *"Freshness-leg flake (~13–20%, deferred fix)"*) — **not** the `:250-253` this set cited. The **fix** (re-checked in the worktree): `risks.md` **R-17** and `phase3-plan.md` §5 now cite `:256-259` | `risks.md` **R-17** · `phase3-plan.md` §5 |

---
