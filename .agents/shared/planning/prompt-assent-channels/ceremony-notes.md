# Ceremony Notes — prompt-assent Phase 1.5 (designated channels) — PLANNING-SIDE

**Prepared by:** the plan lane (this file is **F-13**, written by **Phase 4**) · **Date:**
2026-11-11 (UTC) · **Status:** **PLANNING-SIDE NOTES — not yet a staged release.**

> **⚠ OWNERSHIP (architect §8.6 / §11 row 10).** This file is **this lane's** artefact: it is the
> **planning-side** record of what the v0.29.20 ceremony notes must say. The **release-dir**
> ceremony notes are produced by the **build lane** when it stages v0.29.20; the **runbook**
> (`docs/runbooks/prompt-assent.md`) is **this lane's** file (F-11), written by Phase 4.
>
> **⚠ SUPERSESSION.** This file **supersedes the parent's DM-only K-3 text from the new side**
> — `../prompt-assent/ceremony-notes.md:236-244` (the probe) and `:308-309` (the arming line).
> The parent set stays **frozen** (**D-1**); an auditor reading the older notes sees this
> supersession rather than a contradiction. The parent's §8.2 guild-channel hook
> (`../prompt-assent/architecture-recommendation.md:546-548`) is the hook this lane satisfies
> (**D-13**).

| Field | Value |
|---|---|
| Commission | `prompt-assent` Phase 1.5 — designated channels (pinned-approver model) |
| Doctrine | *"this is channel, i often use channel to split tasks category in my mind, we consider channel is private/dm for now"* (carried verbatim — [`doctrine.md`](doctrine.md)) |
| Target release | **`v0.29.20`** (**STAGED, NOT promoted, NOT armed, NOT restarted** by this lane) |
| Supersedes | the parent's DM-only K-3 probe wording — from the new side |
| Governing decisions | [`decisions.md`](decisions.md) **D-10** (the version), **D-11** (the probe), **D-14** (the delivery posture), **D-15** (the controls) |
| Governing artefact | [`architecture-recommendation.md`](architecture-recommendation.md) §8 (the probe shape), §9 (the staging) — **wins over every file of this set** |

---

## 1. The adapted K-3 live probe — **TWO PHASES** (binding)

### 1.1 — Why two phases are mandatory

Leg 4's expected code (`mint-requires-private-channel`) is **identical** to what a **mistyped map
key** produces in the designated channel. A single-phase leg list **cannot** separate *"the map
says no"* from *"the id is a typo"*. **Only the A/B pair on the SAME channel id isolates the
variable.**

| Phase | Map state | Legs, in order | What the pair isolates |
|---|---|---|---|
| **A** | **unset** | **2** (group DM) → **4A** (the *designated* channel with the map unset — **must refuse**) → **1** (DM) | the baseline: with no map, nothing is designated |
| **B** | **set** | **3** (the designated channel — **must mint**) → **5** (a non-pinned user — **must refuse**) → **4** (a **non-designated** guild channel — **must refuse**, the narrowness proof) | the delta: the **same channel id** now mints — for the pinned approver only; **leg 4 belongs in Phase B** |

### 1.2 — Three armed states, not one bit

| State | How | Meaning |
|---|---|---|
| **`ARMED-TENTATIVE`** | `ENSEMBLE_PROMPT_ASSENT_ENABLED=1` + restart | the probe's **precondition**. A mint alone grants nothing; the exposure is the room disclosure (**D-14**) |
| **`ARMED-OPERATIONAL`** | all six outcomes recorded **and** the Emperor declares reliance | the lane may be relied upon |
| **`DISARMED`** | unset + restart ⇒ `lane-disabled` | back to inert |

**The probe runs inside `ARMED-TENTATIVE` and ENDS with an explicit DISARM-OR-RATIFY step.**

> ### ⚠ THE LANE MUST NOT BE RELIED UPON UNTIL THE SIX OUTCOMES ARE RECORDED.
> *"Arming blocked until the probe"* is **circular** — the probe requires arming. The correct
> posture is: arm **tentatively**, probe, record, then **disarm or ratify** explicitly.

### 1.3 — The legs, their expected evidence, and what each proves

| Leg | Setup | Expected code | Token-row shape | INFO line | Proves |
|---|---|---|---|---|---|
| **1 — 1:1 DM** | the Emperor DMs the relay bot **alone** | *(mint)* | `mint_is_dm=1`, `mint_user_id` = the probe account, `status='live'`, `ttl_expires_at` = +60 min, `attempts=0` | `action=mint`, `outcome=ok` | the DM path is unharmed |
| **2 — group DM** | a group DM (≤10) with **one other human** | `mint-requires-private-channel` | **no** row, **no** audit row | `outcome=refused` | the multi-occupant discriminator fires |
| **3 — designated channel** | the Emperor's **actual** working channel, **single occupant**, map set | *(mint)* | `mint_is_dm=0`, `mint_channel_id` **== the map key**, `mint_user_id` **== the map value** | `action=mint`, `outcome=ok`, `map_digest=…` | the new path works live |
| **4A — the SAME channel, map unset** | the same channel id, map **unset** | `mint-requires-private-channel` | **no** row | `outcome=refused` | the **A/B pair** isolates the map |
| **4 — non-designated guild channel** | another guild channel | `mint-requires-private-channel` | **no** row | `outcome=refused` | designation is narrow |
| **5 — non-pinned user** | a **second ACCOUNT** in the designated channel | `approver-not-pinned` | **no** row | `outcome=refused`, **`flags.mismatch=0`** | the pinned approver is the only authoriser — **and nothing burned** |

> **A `mint_is_dm=0` row with the WRONG channel is a FAIL** — it means the mint happened in a
> thread or in the wrong room.

### 1.4 — Preconditions — **all of them, before the probe starts**

- **The lane is in `ARMED-TENTATIVE`** — `ENSEMBLE_PROMPT_ASSENT_ENABLED=1` + restart. **Map
  state is PER PHASE, with a RESTART between the phases**: Phase A runs with the map UNSET;
  Phase B with it SET to the SAME channel id leg 4A used. Setting the map means editing `.env`
  AND restarting (the map is read from the process environment); confirm the map-parse log line
  (`state=…`) before Phase B.
- **≥ 2 pending CORE `prompt_versions`**, **Emperor-created** (the artifact write path is
  human-session-only) and a **different subject item per minting leg** (a live token blocks
  re-mint, `daemon/tools/prompt_assent.py:1700-1705`; `force=true` supersedes).
- Operator **READ** access to `prompt_assent_tokens` **and** to the message rows'
  `metadata["discord"]`.
- **Discord Developer Mode ON** (ids are copy-paste digits).
- **The relay bot must be present in a NON-designated guild channel at probe time** — leg 4's
  precondition. **Absent ⇒ INCONCLUSIVE, not a pass.**
- **Every probe channel must PASS the adapter's dispatch gates** — the relay source's
  `require_mention` (the bot must be mentioned in every probe turn) and
  `allowed_guild_ids` / `allowed_channels` (`daemon/sources/adapters/discord/adapter.py:163-164`
  — the key names): a channel that fails those gates never reaches the daemon, so the probe
  produces **silence** (no refusal, no response), which is **not** a pass.
- **The second human must be a second ACCOUNT.** A second *device* shares the `user_id` and
  degenerates leg 5 into leg 3. **The Emperor's own alt account is the strongest choice** — it
  proves the pin binds the **account**, not the person.

### 1.5 — **An unrun leg 2/5 is NOT a pass**

The gap is recorded as an **explicit Emperor acceptance** in the release-dir ceremony notes. The
multi-occupant discriminator then stays **unit-proven only** — exactly the class K-3 exists for.

### 1.6 — Code-family triage: the operator's FIRST move

| Observed code | Means | Operator action |
|---|---|---|
| `mint-requires-private-channel` | emitted **only** at the M-4c privacy gate (`daemon/tools/prompt_assent.py:1711`) ⇒ the window was live and the item was pending | an **availability** miss, not a leg failure — check the map, then the channel |
| `designation-config-invalid` | a **configuration** failure | the WARNING names **every** offending key; fix the map |
| `mint-live-token-exists` | a live token is outstanding | **cleanup**, not a probe failure — consume it or wait for the TTL |
| `no-user-origin-turn` / `user-origin-window-stale` | a **window-family** code | the Emperor's turn did not arm the window (or a bystander spoke — see §1.7) |
| `approver-not-pinned` | the turning human is not the room's **current** pinned approver | check the map's value for **that** channel; check who spoke last (§1.7) |

> **The burn asymmetry on the record (D-17 — KEEP BURN).** A **withdrawn designation** (the
> operator re-pins or un-pins the channel between the mint and the approval) refuses
> `token-channel-mismatch` — mismatch-class ⇒ **the token BURNS** (durable counter; five such
> attempts revoke the live token). The two operator-fault codes do **NOT** burn:
> `designation-config-invalid` (the map is broken) and `approver-not-pinned` on the
> re-designation window leave the token live and its counter UNTOUCHED. This is a **user cost**
> an operator must know before arming.

### 1.7 — Hygiene, fail-safe, and the NT-5 rule

- **Refusal-only legs first; minting legs last.** One subject item per leg.
- The mint response posts the **token + the canonical render into the room**
  (`daemon/tools/prompt_assent.py:1249`, `:1252-1254`) ⇒ **single-occupant channel for leg 3**.
- **Consume or TTL-expire** a token before reusing its subject item (60 min).
- **Never** paste a live token into a second channel.
- **The mention gate** (`require_mention`, default True): the relay bot must be **mentioned** in
  every probe turn, AND the channel must pass the adapter's allow-list
  (`daemon/sources/adapters/discord/adapter.py:163-164`) — a channel that fails either gate
  produces **silence** (no refusal, no response), which is **not** a pass.
- **The map-parse log line is MANDATORY probe evidence** — the state (`unset` / `empty` /
  `invalid` / `N entries`) + the content-free fingerprint. It is the **only** discriminator
  between *"map says no"* and *"map is broken/empty"*.
- **NT-5 — the last-speaker-wins rule.** A guild channel is **one instance for all humans**
  (`daemon/sources/adapters/discord/adapter.py:909-915` (the bot-skip gate), `:218-220` (the skip default)) and the window is re-armed on
  every non-silent dispatch (`daemon/manager.py:6126`, `:11530`) ⇒ a **bystander's reply between
  the Emperor's turn and the tool call re-points the window**, and the Emperor's own attempt then
  refuses `approver-not-pinned`. **Rule: the Emperor must be the LAST speaker.** A bystander's
  reply within the 600 s freshness bound invalidates the attempt — **re-run, do not adjudicate.**

---

## 2. The delivery-mode flag (LEADER RULING 1 — **D-14**)

The mint response carries the grouped token (`daemon/tools/prompt_assent.py:1249`) **and** the
canonical render (`:1252-1254`), and the relay posts it **into the channel**. Under the Emperor's
ruling ("we consider channel is private/dm for now") that is the **channel-analog of the DM
behaviour** — **accepted and recorded**, never an implicit assumption.

> ### ⚠ FLAGGED FOR THE EMPEROR — **delivery mode: ACCEPT-AND-RECORD** (the default)
> The delivery mode is an **explicit, overridable default**. The Emperor may **flip it at arming
> time**; the alternatives are **named here** so the choice is visible rather than buried.

| Mode | What it does | Status |
|---|---|---|
| **`accept-and-record`** | the token + the canonical render are posted in the channel, exactly as in a DM | **THE DEFAULT (this ruling)** |
| `dm-deliver` | the token + the diff are delivered to the Emperor's DM instead of the room | a **new delivery surface** — **not built** by this lane; a follow-up commission |
| `redact-render` | the room receives the token without the canonical render (the diff is fetched separately) | a **second delivery surface** — **not built** by this lane; a follow-up commission |

**The two hygiene notes, on the record.** (i) **Every occupant** of the channel sees the token +
the full canonical prompt diff, plus any reading bot — the content-free discipline governs
**refusals and logs**, **never the mint response**. (ii) **A bystander who relays the token is
holding the REAL token**: a "garbage token" attempt from a bystander is a genuine mismatch ⇒ the
durable counter climbs to the revoke threshold of 5 (`daemon/tools/prompt_assent.py:252`) and the
K-9 budget to the lockout at 10/hour (`:274-326`) ⇒ a **lockout DoS on the legitimate Emperor**.
That is why §1.7's hygiene rules bind.

---

## 3. The staging records the release-dir ceremony notes must carry

### 3.0 — Two build-day disclosures (n9)

- **`stage.sh` rewrites the live INSTALL_DIR/.env during staging** (a marker line + `chmod 600`) —
  **benign and standard**; the ceremony note discloses it. The `.env` is a **mode-600 nea-owned**
  file (the same posture the map's env knob uses).
- **`_verify_tip_identity` WARNs + SKIPS** when the worktree's origin ref is stale (an unfetched
  worktree). **T4.8 must `git fetch origin` before verify**, or accept the SKIP with disclosure.

### 3.1 — The version and the two build-day traps

| Field | Value |
|---|---|
| `staged_version` | **`v0.29.20`** |
| slot verification | `git ls-remote --tags origin 'v0.29.20*'` → empty; `git tag --list v0.29.20` → empty; the remote max is **v0.29.19** (tag `649a509b1` → `1132fa4cb`, the concurrent lane); **v0.29.20 free local+origin** — **re-verified TWICE: at the start of the day and immediately before `git tag -a`** (three lanes are converging on adjacent slots) |
| **trap 1** | **`ENSEMBLE_ROLLBACK_SAFE=1` MUST be in the argv.** `scripts/upgrade/stage.sh:328-352` greps the **whole** migration history for `DROP TABLE\|DROP COLUMN` — it **matches** ⇒ the unset branch **exits 78**. Record `rollback_safe=true, source=explicit` |
| **trap 2** | `scripts/bump_version.py` commits with the subject **`! New version X`** (`scripts/bump_version.py:150`) — use it as a **FILE EDITOR** and commit **`chore(release): bump version to 0.29.20`** (exactly as v0.29.18's bump did (`d317043aa` — its subject named 0.29.18; ours names 0.29.20)) |
| tag discipline | annotated tag at exact HEAD; **push by name, never `--tags`**; on a slot collision **re-bump to the next free patch slot and record it — never re-cut an existing tag**; **a remote tag is never deleted** |
| migration head | `20261110_221500_create_prompt_assent_tokens.sql` — **128 files, UNCHANGED** (no table, no column, no migration) |
| rollback_safe | `true`, **source=explicit** (see trap 1) |
| lane state | **DEFAULT-OFF** — inert until `ENSEMBLE_PROMPT_ASSENT_ENABLED=1` |
| **armed by** | the Emperor, after the **K-3 live designated-channel probe** — **not before** |

### 3.2 — The v0.29.18 protection statement and the survival math

- **v0.29.18 is LEFT STANDING** — release dir kept, local tag kept, remote tag untouched. **There
  is no disposal step at all** (the inverse of v0.28.17's disposal note). An explicit note: *"the
  DM-only fallback's release dir exists"*.
- Its content is **already an ancestor** of the new base (`git merge-base --is-ancestor
  v0.29.18^{commit} HEAD` → exit 0) — nothing is preserved *by* disposal.
- **Staging takes the release count 6 → 7 (v0.29.19's staging made it 6) and queues nothing**: `retention_evict`
  (`scripts/upgrade/lib.sh:6857-6917`, `RETENTION_KEEP=3` at `:375`) is called **only** from
  `promote.sh:771` and `:927` — **`stage.sh` never evicts** (verified: `grep -c retention_evict
  scripts/upgrade/stage.sh` = 0).
- **Retention TRUTH:** Staging v0.29.20 takes the host to **7 release dirs**; `retention_evict` (`lib.sh`, RETENTION_KEEP=3) evicts the oldest non-pinned dirs until **3 TOTAL remain** — the **FIRST v0.29.20 promote evicts v0.29.7, v0.29.13, v0.29.17 and v0.29.18** (4 dirs): **the DM-only fallback is NOT protected on this host**. Primary stated mitigation = the **re-stage-from-`df4a92732` recovery procedure**: (1) the `v0.29.18` tag + its 4-way sha `aa78f632c4…` survive in git history; (2) `git checkout df4a92732` in a clean worktree (the tag's commit — never re-cut the tag); (3) rebuild `dist/ensemble-prod`; (4) re-run `scripts/upgrade/stage.sh` with `VERSION=v0.29.18` + `ENSEMBLE_ROLLBACK_SAFE=1` (the exact-match gate is satisfied by the existing tag); (5) verify the 4-way sha reproduces. One build+stage cycle restores the fallback.
- **Operator rule:** *no other promote until the designated-channel decision is made.*
- **Recoverability:** *"if evicted, re-stage from `df4a92732` — the tags survive, the option is
  recoverable at one build+stage cycle."*

### 3.3 — What is **not** in the payload

**The probe and the runbook live OUTSIDE the release payload** (`docs/`, `.agents/` are not
staged). A promoted binary carries **no copy of its own probe instructions** — the ceremony note
must **point at the repo paths**:

- the probe → `.agents/shared/planning/prompt-assent-channels/ceremony-notes.md` (this file)
- the runbook → `docs/runbooks/prompt-assent.md` §"The K-3 live confirmation"

### 3.4 — The ordinals (unchanged by this lane)

Two refusal codes + one new module + new tests move **none** of the ceremony-asserted ordinals:
migration head `20261110_221500` / **128 files** ✓ · prompt baseline **181** ✓ (the lane touches no
`agents/` file) · scripts floor/rev `2147c0298` ✓ · privileged category / tool vocabulary / artifact
counts ✓. **The refusal-taxonomy count 27 → 29 is a test-pinned count, not a ceremony ordinal.**

---

## 4. The arming record (to be completed at the ceremony — NOT by this lane)

```
armed by          the Emperor, after the K-3 live designated-channel probe — not before
probe state       ARMED-TENTATIVE → (six outcomes recorded) → DISARM-OR-RATIFY
delivery mode     accept-and-record            ← FLAGGED: the Emperor may flip at arming time
map state         <unset | empty | invalid | N entries>   ← from the map-parse log line
leg 1  DM                    <code>  <token-row shape>  <INFO line>
leg 2  group DM              <code>  <no row>            <INFO line>
leg 3  designated channel    <code>  <token-row shape>  <INFO line>
leg 4A designated, map unset <code>  <no row>            <INFO line>
leg 4  non-designated        <code>  <no row>            <INFO line>
leg 5  non-pinned user       <code>  <no row>            <INFO line>
unrun legs       <leg> — NOT a pass — explicit Emperor acceptance required
ratified by      ........................................
date             ........................................
```

**The T3.5 re-stamp** (`agents/ari/tools_note.md`; `grep -c memoir_about` = 0 today) is the
**first beneficiary** once the lane is armed — human-session-only, **not** a lane deliverable
(**D-12**, **O-9**).

---

*Planning-side notes only. No promote, no arming, no restart, no disposal by this lane.*
