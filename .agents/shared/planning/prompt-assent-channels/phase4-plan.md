# Phase 4 — The operator surface + the staged release

**Commission:** `prompt-assent` Phase 1.5 — designated channels (pinned-approver model).
**Doctrine:** [`doctrine.md`](doctrine.md) — *"this is channel, i often use channel to split tasks category in my mind, we consider channel is private/dm for now"* (carried verbatim).
**Binding decisions:** [`decisions.md`](decisions.md) **D-10** (the version + the supersede
treatment), **D-11** (the adapted probe, in the architect's §8 shape), **D-12** (the acceptance
subject), **D-14** (the NT-1 delivery posture), **D-15** (the NT-2 controls).

> **Context owned by this phase (one owner per file — architect §11 row 10).** The **runbook**
> (`docs/runbooks/prompt-assent.md`) is **this lane's** file (F-11). The **planning-side ceremony
> notes** are **this lane's** new file (F-13, in this directory). The **release-dir** ceremony
> notes are the **build lane's** artefact. The parent set stays frozen (**D-1**).

---

## 1. Objective

> **SCOPE FENCE (B5/#10): NO live promotion, NO arming, NO live-install mutation in this lane —
> v0.29.20 staging only; the ceremony is a separate, post-lane Emperor act.**

Give the operator the **complete, honest** surface for the new capability — a runbook whose probe
is the **two-phase, three-armed-state** version, a taxonomy table that names the two new codes, a
troubleshooting section that can diagnose a designated-channel refusal, a **named and flagged**
delivery-mode default, and the map's configuration contract — and cut a **STAGED release
`v0.29.20`** that is **not promoted** and **not armed**.

**Done means:** the runbook's probe matches `decisions.md` **D-11** (and the architect's §8) leg
for leg, the ceremony note carries every required record (including the staging traps), the
release exists with its tag and its 4-way sha, and `current` has not moved.

---

## 2. Decisions taken in this phase

| ID | Decision | Rationale (cite) | Rejected alternatives |
|---|---|---|---|
| **D-4.1** | The version is **`v0.29.20`** | the slot is **verified free**: `git ls-remote --tags origin 'v0.29.20*'` → empty; `git tag --list v0.29.20` → empty; the remote max is `v0.29.18` (`a4d7233f2 → df4a92732`) — **re-verified on the build day** (architect §9.1) | reusing v0.29.18 (its release dir exists and it is a ceremony-ready DM-only release — re-cutting it would destroy the DM-only fallback) |
| **D-4.2** | **v0.29.18 is LEFT STANDING** — release dir kept, local tag kept, remote tag untouched; **no disposal step at all** | its content is already an ancestor of the new base (`git merge-base --is-ancestor v0.29.18^{commit} HEAD` → exit 0), so there is nothing to preserve by disposal — and it stays promotable as the **DM-only fallback** (architect §9.3) | disposing it (the v0.28.17 treatment — it would delete the only ceremony-ready DM-only release for no gain) |
| **D-4.3** | The **migration head is unchanged** — `20261110_221500_create_prompt_assent_tokens.sql`, **128 files** | **D-5** — no new table, no new column, no new migration | adding a 129th migration for a value with no query need |
| **D-4.4** | The ceremony notes carry the **adapted** K-3 gate wording and **supersede** the DM-only probe text — **from the new side** (a NEW `ceremony-notes.md` in this directory) | the ruling is newer than the parent's notes (they carry no designated-channels mention); the parent set stays frozen (architect §8.6, **D-1**) | leaving the DM-only probe in place (it would probe the wrong surface); editing the parent's notes (frozen set) |
| **D-4.5** | The runbook documents the designation map's **env var**, its **JSON shape**, the **restart requirement**, the **"never commit it"** rule, and the **"no agent can designate"** statement **without leaning on the file-tool deny list** | the operator must be able to configure the map correctly on the first try; a typo produces `designation-config-invalid`, not a silent no-op. **The deny list is not a boundary** — `bash` is held (`agents/ari/meta.json:14`) while `edit_file`/`write_file` are denied (`:49`) (architect §3.4) | documenting only the code names (the operator would not know how to configure the surface); leaning on the deny list (it is **not** a boundary — architect §3.4) |
| **D-4.6** | **No live promote, no arming, no restart** by this lane | house rule — the ceremony belongs to the Emperor | arming here (the arming gate is the adapted probe, a **post-promote** operator act) |
| **D-4.7** | The probe is **two-phase** with **three armed states** and an explicit **disarm-or-ratify** step | leg 4's expected code is **identical** to what a mistyped map key produces — a single-phase list cannot separate *"map says no"* from *"id typo"* (architect §8.1-8.2) | one flat leg list (the ambiguity the architect ruled out) |
| **D-4.8** | The **delivery mode** is recorded as an **explicit, overridable default** (`accept-and-record`) with the two alternatives named, flagged for the Emperor at arming time | **LEADER RULING 1** (**D-14**) | an implicit default (the architect's NT-1 complaint — the plan made it implicitly (iii) without recording it) |

---

## 3. Tasks

| ID | Task | Outcome (testable) | Touchpoints (files) | Depends on | Risks carried | AC |
|---|---|---|---|---|---|---|
| **T4.1** | **Re-verify the version slot** (the mandatory build-lane pre-step): `git ls-remote --tags origin 'v0.29.1*' 'v0.29.2*'` and `git tag --list 'v0.29.*'` — confirm `v0.29.20` is still free. **Re-verify it AGAIN immediately before `git tag -a`** (architect §9.1: three lanes are converging on adjacent slots) | the recorded slot is confirmed twice — at the start and immediately before the tag | (git only) | — | R-10 | AC1 |
| **T4.2** | **Update the existing `ceremony-notes.md`** (file **F-13** — already populated in this lane; the task reconciles it with the final plan state): the two-phase probe (§4 below), the three armed states, the per-leg evidence table, the triage table, the preconditions, the hygiene rules, the **delivery-mode flag** (**D-14**), the **NT-5 last-speaker rule**, the staging records (§5), the arming line, and the **"the lane must not be RELIED UPON until the six outcomes are recorded"** statement | an operator can run the probe from this file alone; it **supersedes** the parent's DM-only K-3 text from the new side | `.agents/shared/planning/prompt-assent-channels/ceremony-notes.md` **(NEW)** | P3 | R-11 | AC2, AC3, AC11 |
| **T4.3** | Rewrite the runbook's **K-3 section** as the **two-phase, six-leg** probe, matching the ceremony note leg for leg, with the **exact expected codes** and the "record every outcome before relying on the lane" instruction | the runbook's probe is executable at the keyboard with no other document | `docs/runbooks/prompt-assent.md` | T4.2 | R-11 | AC4 |
| **T4.4** | Update the runbook's **taxonomy table** and its **complete-taxonomy list** with the two new codes, and add the designated-channel rows to the **troubleshooting** table (`approver-not-pinned` ⇒ "you are not the pinned approver of this room"; `designation-config-invalid` ⇒ "the operator's map is broken — see the map section") | an operator can diagnose every refusal the new surface can produce from the table alone | `docs/runbooks/prompt-assent.md` | P2 | R-11 | AC5 |
| **T4.5** | Document the **designation map** in the runbook **`docs/runbooks/prompt-assent.md`** (the target file — no rename; the earlier `prompt_assent.md → prompt-assent.md` arrow in this row was a phantom): the env var name, the exact JSON shape, a worked example (**with placeholder ids only**), the **all-or-nothing** rule, the **id regex** `^[1-9][0-9]{0,31}$`, the **≤ 64** bound, the **restart requirement**, the **"never commit it"** rule, and the statement that designation is an **operator act no agent can perform** — **without leaning on the file-tool deny list** (`bash` is held; the deny list is not a boundary) | the operator can configure the map correctly; the "no agent can designate" property is stated where the operator reads it, in honest terms | `docs/runbooks/prompt-assent.md` | P1 | R-1 | AC6 |
| **T4.6** | Update the runbook's **arming** wording — *"armed by the Emperor, after the K-3 live **designated-channel** probe — not before"* — and keep the default-OFF statement; add the **re-probe rule**: *re-run probe leg 3 after **any** map edit* (the only typo feedback loop — a valid-format-wrong-id produces no signal at all) | the arming gate and the probe match; a map edit has a mandated re-probe | `docs/runbooks/prompt-assent.md` | T4.3 | — | AC7 |
| **T4.7** | Add the **CHANGELOG** section for v0.29.20 — the designated-channel capability, the two new codes, the env knob, the unchanged schema, the **accept-and-record** delivery posture | the changelog matches the release | `CHANGELOG.md` | — | — | AC8 |
| **T4.8** | **Build + tag + verify** the staged release — **merge to latest FIRST** (stage.sh's exact-match gate at `scripts/upgrade/stage.sh:139-151` refuses a non-tip tree), then the version bump (as a **file editor**, house-subject commit `chore(release): bump version to 0.29.19`), the build, the **annotated tag pushed by name** (never `--tags`), the **4-way sha**, the migration-head census, the **slot re-verification immediately before `git tag -a`**, and the verification that `current` is **unmoved** and the lane is **not armed** | a staged, tagged, unpromoted release exists with a recorded sha; `current` unchanged | the build lane's artefacts | T4.1, T4.2 | R-10 | AC9, AC10 |

---

## 4. The probe — the architect's §8 shape (BINDING)

### 4.1 — Two phases (mandatory)

| Phase | Map state | Legs, in order | What the pair isolates |
|---|---|---|---|
| **A** | **unset** | **leg 2** (group DM) → **leg 4A** (the *designated* channel with the map unset — **must refuse**) → **leg 1** (DM) | the baseline: with no map, nothing is designated |
| **B** | **set** | **leg 3** (the designated channel — **must mint**) → **leg 5** (a non-pinned user — **must refuse**) → **leg 4** (a **non-designated** guild channel — **must refuse**, the narrowness proof) | the delta: the **same channel id** now mints — for the pinned approver only; **leg 4 belongs in Phase B** (only there is a channel designated, so the narrowness proof is meaningful) |

**Only the A/B pair on the SAME channel id separates "map says no" from "id typo".** Leg 4's
expected code (`mint-requires-private-channel`) is **identical** to what a mistyped map key
produces — a single-phase list cannot tell them apart.

### 4.2 — Three armed states (not one bit)

| State | How | Meaning |
|---|---|---|
| `ARMED-TENTATIVE` | `ENSEMBLE_PROMPT_ASSENT_ENABLED=1` + restart | the probe's **precondition**. A mint alone grants nothing; the exposure is the room disclosure (**D-14**) |
| `ARMED-OPERATIONAL` | all six outcomes recorded **and** the Emperor declares reliance | the lane may be relied upon |
| `DISARMED` | unset + restart ⇒ `lane-disabled` | back to inert |

**The probe runs inside `ARMED-TENTATIVE` and ENDS with an explicit disarm-or-ratify step.** The
notes must say: *"**the lane must not be RELIED UPON until the six outcomes are recorded**"* —
"arming blocked until the probe" is **circular** (the probe requires arming).

### 4.3 — The legs, their preconditions, and their evidence

| Leg | Setup | Expected code | Token-row shape | INFO line | Proves |
|---|---|---|---|---|---|
| **1 — DM** | the Emperor DMs the relay bot alone | *(mint)* | `mint_is_dm=1`, `mint_user_id` = the probe account, `status='live'`, `ttl_expires_at` = +60 min, `attempts=0` | `action=mint`, `outcome=ok` | the DM path is unharmed |
| **2 — group DM** | a group DM with one other human | `mint-requires-private-channel` | **no** row | `outcome=refused` | the multi-occupant discriminator fires |
| **3 — designated channel** | the Emperor's **actual** working channel, **single occupant**, map set | *(mint)* | `mint_is_dm=0`, `mint_channel_id` **== the map key**, `mint_user_id` **== the map value** | `action=mint`, `outcome=ok`, `map_digest=…` | the new path works live |
| **4A — designated channel, map unset** | the **same** channel, map **unset** | `mint-requires-private-channel` | **no** row | `outcome=refused` | the A/B pair isolates the map |
| **4 — non-designated guild channel** | another guild channel | `mint-requires-private-channel` | **no** row | `outcome=refused` | designation is narrow |
| **5 — non-pinned user** | a **second ACCOUNT** in the designated channel | *(silence — no refusal, no response)* | the relay bot was **not mentioned** (`require_mention` defaults True) OR the channel fails the adapter's allow-list (`daemon/sources/adapters/discord/adapter.py:163-164`) | use a channel where the bot IS mentioned and passes the gates; silence ≠ pass |
| `approver-not-pinned` | **no** row | `outcome=refused`, **`flags.mismatch=0`** | the pinned approver is the only authoriser **and nothing burned** |

> **A `mint_is_dm=0` row with the WRONG channel is a FAIL** — it means the mint happened in a
> thread or in the wrong room.

**Preconditions (architect §8.4) — all of them, before the probe starts.**

- **≥ 2 pending CORE `prompt_versions`**, Emperor-created (the artifact path is human-session-only),
  and a **different subject item per minting leg** (a live token blocks re-mint,
  `daemon/tools/prompt_assent.py:1700-1705`; `force=true` supersedes).
- Operator **READ** access to `prompt_assent_tokens` and to the message rows' `metadata["discord"]`.
- **Discord Developer Mode ON** (ids are copy-paste digits).
- **The relay bot must be present in a NON-designated guild channel at probe time** — leg 4's
  precondition; **absent ⇒ INCONCLUSIVE, not a pass.**
- **The channel must PASS the adapter's dispatch gates** (`daemon/sources/adapters/discord/adapter.py:163-164`
  — `KEY_ALLOWED_GUILD_IDS` / `KEY_ALLOWED_CHANNELS`, plus the mention gate): a channel that fails
  those gates **never reaches the daemon**, so the probe must use a channel that passes them
- **The second human must be a second ACCOUNT.** A second *device* shares the `user_id` and
  degenerates leg 5 into leg 3. **The Emperor's own alt account is the strongest choice** — it
  proves the pin binds the **account**, not the person.

**An unrun leg 2/5 is NOT a pass.** The gap is recorded as an **explicit Emperor acceptance**;
the multi-occupant discriminator then stays **unit-proven only** — exactly the class K-3 exists
for.

### 4.4 — Code-family triage: the operator's FIRST move

| Observed code | Means | Operator action |
|---|---|---|
| `mint-requires-private-channel` | emitted **only** at the M-4c privacy gate (`daemon/tools/prompt_assent.py:1711`) ⇒ the window was live and the item was pending | an **availability** miss, not a leg failure — check the map, then the channel |
| `designation-config-invalid` | a **configuration** failure | the WARNING names **every** offending key; fix the map |
| `mint-live-token-exists` | a live token is outstanding | **cleanup**, not a probe failure — consume it or wait for the TTL |
| `no-user-origin-turn` / `user-origin-window-stale` | a **window-family** code | the Emperor's turn did not arm the window (or a bystander spoke — see NT-5) |

### 4.5 — Hygiene and fail-safe (architect §8.5)

- **Refusal-only legs first; minting legs last.** One subject item per leg.
- The mint response posts the **token + the canonical render into the room**
  (`daemon/tools/prompt_assent.py:1249`, `:1252-1254`) ⇒ **single-occupant channel for leg 3**.
- **Consume or TTL-expire** a token before reusing its subject item (60 min).
- **Never** paste a live token into a second channel.
- **The mention gate** (`require_mention` defaults True): the relay bot must be **mentioned** in every probe turn, AND the channel must pass the adapter's allow-list (`daemon/sources/adapters/discord/adapter.py:163-164`) — a channel that fails either gate produces **silence** (no refusal, no response), which is **not** a pass.
- **The map-parse log line is MANDATORY probe evidence** — the state (`unset` / `empty` /
  `invalid` / `N entries`) + the content-free fingerprint. It is the **only** discriminator
  between *"map says no"* and *"map is broken/empty"*.
- **NT-5 — the last-speaker-wins rule.** A guild channel is **one instance for all humans**
  (`daemon/sources/adapters/discord/adapter.py:909-915` (the bot-skip gate), `:218-220` (the skip default)) and the window is re-armed on
  every non-silent dispatch (`daemon/manager.py:6126`, `:11530`) ⇒ a **bystander's reply between
  the Emperor's turn and the tool call re-points the window**, and the Emperor's own attempt then
  refuses `approver-not-pinned`. **Rule:** the Emperor must be the **last speaker**; a
  bystander's reply within the 600 s freshness bound invalidates the attempt — **re-run, do not
  adjudicate.**

---

## 5. The staging traps (the architect's §9 — BINDING)

### 5.1 — The two build-day traps the original plan omitted

| # | Trap | The rule | Verified in this worktree |
|---|---|---|---|
| **1** | `stage.sh:328-352` greps the **whole** migration history for `DROP TABLE\|DROP COLUMN` — it **matches** (e.g. `20250326_000000_initial_schema.sql`) ⇒ **`stage.sh` exits 78** unless `ENSEMBLE_ROLLBACK_SAFE=1` is passed **explicitly** | put `ENSEMBLE_ROLLBACK_SAFE=1` **in the argv**; record `rollback_safe=true, source=explicit` | `scripts/upgrade/stage.sh:328-352` — the unset branch warns and `exit 78`; the explicit branch sets `ROLLBACK_SAFE=true; ROLLBACK_SAFE_SRC="explicit=true"` |
| **2** | `scripts/bump_version.py` commits with the subject **`! New version X`** (`scripts/bump_version.py:150`), violating the house `<type>:<desc>` convention | use `bump_version.py` as a **FILE EDITOR** and commit **`chore(release): bump version to 0.29.19`** — exactly as v0.29.18 did (`d317043aa`) | `scripts/bump_version.py:150` — `"git", "commit", "-m", f"! New version {new_version}"` |

### 5.2 — The slot re-verification (architect §9.1)

`stage.sh:139-151` requires `git describe --tags --exact-match HEAD == VERSION`. **Three lanes are
converging on adjacent slots** ⇒ **re-verify the slot immediately before `git tag -a`**, not at
the start of the day. On collision: **re-bump to the next free patch slot and record it — never
re-cut an existing tag.** Push **by name**, never `--tags`. **A remote tag is never deleted.**

### 5.3 — The retention coupling — exactly ONE mechanism (architect §9.2-9.3)

`retention_evict` (`scripts/upgrade/lib.sh:6857-6917`, `RETENTION_KEEP=3` at `:375`) is called
**only** from `promote.sh:771` and `:927` — **`stage.sh` never evicts** (verified:
`grep -c retention_evict scripts/upgrade/stage.sh` = **0**). **Staging takes the release count
5 → 6 and queues nothing.**

- At the **first** v0.29.20 promote: evict 3 (`v0.29.7`, `v0.29.13`, `v0.29.17` — the three oldest
  non-pinned) ⇒ **v0.29.18 survives**.
- It becomes the candidate at the **third** promote (after v0.29.20 and v0.29.21) ⇒ **it survives
  exactly two further promotes, protected by nothing** (`retention_evict` pins only
  `current`/`previous`; a script pin is an upgrade-machinery change with its own floor/stamp
  bump — **out of this lane's scope**).

**Three records the ceremony note MUST carry** (architect §9.3):

1. the **operator rule** — *no other promote until the designated-channel decision is made*;
2. the **two-promote survival math** (above);
3. the recoverability note — *"if evicted, re-stage from `df4a92732` — the tags survive, the
   option is recoverable at one build+stage cycle"*.

**There is no disposal step at all** (the inverse of v0.29.17's disposal note) — the ceremony
note must say so explicitly, and must carry an explicit *"the DM-only fallback's release dir
exists"* note.

### 5.4 — What the ceremony note must say about the payload

- The **probe and the runbook live OUTSIDE the release payload** (`docs/`, `.agents/` are not
  staged) — a promoted binary carries **no copy of its own probe instructions**; the ceremony note
  must **point at the repo paths**.
- The **delivery-mode flag** (**D-14**): `accept-and-record` is the **default**, with `dm-deliver`
  and `redact-render` named as the two alternatives the Emperor may flip **at arming time**.
- The **arming line** names the **designated-channel** probe, and the **"not relied upon until
  the six outcomes are recorded"** statement.

---

## 6. Acceptance criteria

| ID | Criterion |
|----|-----------|
| **AC1** | The version slot is re-verified **twice** — at the start and immediately before `git tag -a`; a collision re-bumps to the next free slot and is recorded |
| **AC2** | This directory's `ceremony-notes.md` (F-13) contains the two-phase probe, the three armed states, the per-leg evidence table, the triage table, the preconditions, the hygiene rules, the NT-5 rule and the **disarm-or-ratify** step — and states that it **supersedes** the parent's DM-only K-3 text from the new side |
| **AC3** | The ceremony note carries the **delivery-mode flag** (**D-14**) with the default and both alternatives named |
| **AC4** | The runbook's probe is the **two-phase, six-leg** version with the expected codes matching the matrix, and the "record every outcome before relying on the lane" instruction |
| **AC5** | The runbook's taxonomy table and complete list contain the two new codes, and the troubleshooting table can diagnose every new refusal |
| **AC6** | The runbook documents the map's env var, JSON shape, id regex, ≤ 64 bound, restart requirement, "never commit" rule and the **operator-act** statement — **without leaning on the file-tool deny list** |
| **AC7** | The arming wording names the **designated-channel** probe, and the **re-probe-after-any-map-edit** rule is present |
| **AC8** | The CHANGELOG's v0.29.20 section matches the shipped behaviour |
| **AC9** | The release is **tagged by name**, the **4-way sha** matches, `current` is **unmoved**, and the lane is **not armed** |
| **AC10** | The staging ran with `ENSEMBLE_ROLLBACK_SAFE=1` **in the argv** and records `rollback_safe=true, source=explicit`; the bump commit subject is **`chore(release): bump version to 0.29.19`** |
| **AC11** | The ceremony note carries the **v0.29.18 protection statement**, the **two-promote survival math**, the recoverability note, the **"no disposal step"** note, and the **"the probe/runbook live outside the release payload"** note |

---

## 7. Notes for the implementer

- **The runbook is an operator document.** Write the probe as a numbered procedure an operator
  can follow at the keyboard, with the expected outcome **and** the expected failure code for
  each leg.
- **Do not arm the lane.** Do not set `ENSEMBLE_PROMPT_ASSENT_ENABLED` in any environment from
  this lane. The designated-channel map is likewise **not** configured by this lane — the surface
  ships **inert**.
- **Do not promote.** `current` stays exactly where it is.
- **The ceremony notes supersede the parent's DM-only K-3 wording** — say so explicitly, so an
  auditor reading the older notes sees the supersession rather than a contradiction.
- **The v0.29.18 survival math is time-sensitive**: two further promotes evict it. If the
  designated-channel decision takes longer than two promotes, re-stage from `df4a92732` rather
  than promoting something else.
