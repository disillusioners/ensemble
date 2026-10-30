# Phase 6 — Ceremony readiness (the fix only lands if the *scripts dir* is handled)

**NEW PHASE — created by the architect fold (amendments 26–31, architect RULING 9), 2026-10-29.**
**REVISED by the plan-council round-3 fold (B1/B3 + Q1/Q2/Q3 rulings), 2026-10-29** — this phase carried all three
council blockers; the floor mechanism, the guard-input acquisition and the non-git posture are now FINAL.
**Depends on:** P1–P4 (implementation) + **P5's Pack I** (the ceremony-readiness pack — the *spec* dependency is P6→P5, the *build* order is P1→P5→P6; the apparent cycle is resolved by the spec/implementation split, N-18). · **Blocks:** the ceremony's efficacy (not the merge).

---

## 1. Objective

Make the lane's fix actually **reach the ceremony**.

Without this phase, D1–D4 would be a **no-op at ceremony time**: the live `.env` pins the upgrade scripts to a
**dev checkout that is 277 commits behind** and that **still contains the O(N²) scan** — so the next ceremony
would run the OLD code, with the shell-side watchdog **absent** while only the binary-side L3 backstop would be
present (the primary/backstop relationship **inverts**).

This phase therefore delivers three in-lane mechanisms and records one **user-owned precondition**:

| Piece | Seat | Effect |
|-------|------|--------|
| **(i) Hard precondition** | **USER / leader** (runbook + plan) | sync the pinned checkout's `scripts/upgrade/` so that the pinned dir's HEAD **contains `PIPELINE_SCRIPTS_FLOOR_COMMIT` as an ancestor** — i.e. `git -C <dir> merge-base --is-ancestor <floor> HEAD` **passes** — **or** move the pin. **[fold: W4 — the ordering predicate is ancestry, not `≥`]** |
| **(ii) Journal-always provenance** | the **daemon** (arm time) | the arm row records `scripts_dir`, `lib.sh` sha256, git rev + dirty flag. |
| **(iii) Stamp + floor refusal** | the **daemon** (arm time, **before the nonce burns**) | a provably-stale pinned dir ⇒ **refuse to arm**. |
| **(iv) Watchdog token** | the **scripts** (defense in depth) | a mixed-generation deployment (new binary + half-synced scripts) is refused loudly. |

**Without (i), (iii) converts the failure mode from a silent 37-minute hang into a loud refusal** — which is the
correct outcome, but it is a *refusal*, not a *fixed ceremony*.

---

## 2. The deployment-truth chain (MEASURED, architect RULING 9) **[fold: A26, D6-1]**

| # | Fact | Anchor |
|---|------|--------|
| 1 | The scripts dir is resolved **once, at ARM time, in the daemon**: `ENSEMBLE_UPGRADE_SCRIPTS_DIR` → module-repo → `install_dir/scripts`. | `_resolve_scripts_dir`, `daemon/tools/upgrade_tools.py:1953-1966`; frozen into the arm marker at `:3714` |
| 2 | The path travels in **argv**, not in the executor env (the env var is stripped by `EXECUTOR_ENV_ALLOWLIST`/`PREFIXES`). | `daemon/manager.py:5494-5496`; `daemon/tools/upgrade_journal.py:2156-2162` |
| 3 | Release payloads ship **no `scripts/`** since v0.27.4 ⇒ **a checkout is the only scripts source on this host**. | `stage.sh:392-425` (copies binary + agents + frontend + plugins + config + launcher only) |
| 4 | The live `.env:107` pins `ENSEMBLE_UPGRADE_SCRIPTS_DIR=/home/nea/ensemble-src/scripts/upgrade`; that checkout's HEAD `013895287` is **277 commits behind** `b1183a637`; `scripts/` is clean; **at THAT checkout `lib.sh:811` still contains the O(N²) scan** — ⚠ **tree-specific**: in the **lane's base tree** (`b1183a637`) `journal_read` is at `lib.sh:961` and `:811` is the sandbox-install-dir refusal (`_is_canonical_install_dir`), so this anchor is valid **only** for the pinned checkout. | MEASURED by the architect (RULING 9) |
| 5 | `origin/latest` == `b1183a637` (no tip drift). | MEASURED |
| 6 | ⇒ **The next ceremony runs the OLD code** unless the checkout is synced or the pin is moved. D1–D4 are then a no-op; the shell-side watchdog is absent while only L3 (binary-side) is present. | derived from 1–5 |

**D6-1** — *Decision:* the chain above is a **hard precondition of the lane's efficacy** and is recorded as such in
`plan-overview.md` §9 (A6), §11 and SC12 — not as an operational footnote.

---

## 3. The hard precondition — USER/leader-owned **[fold: A27, D6-2]**

> **PRECONDITION (before the next ceremony):** either
> **(a) sync `/home/nea/ensemble-src/scripts/upgrade/` to a tree that CONTAINS this lane's fix commit** — the
> orderable predicate is **git ancestry**, not a `>=` comparison (git SHAs are unordered — a bare "≥" is an
> invalid predicate and was the B1 defect): `git -C <dir> merge-base --is-ancestor <floor-commit> HEAD` ⇒ the
> synced tree must be a **descendant** of the floor commit. Scoped to `scripts/`;
> the checkout was dirty in unrelated files (**15 at the architect's measurement — time-dependent**; the load-bearing predicate is that **`scripts/` is clean**) at the architect's measurement (2026-10-29 20:xx; **time-dependent** — a later count showed 14 modified + 49 untracked; the load-bearing predicate is **`scripts/` is clean**, which holds), which the **WIP owner** reconciles (the cross-lane WIP-owner
> precedent from the v0.27.1 lane) — **or**
> **(b) move the `ENSEMBLE_UPGRADE_SCRIPTS_DIR` pin** to a directory whose HEAD **contains `PIPELINE_SCRIPTS_FLOOR_COMMIT` as an ancestor** — i.e. `git -C <dir> merge-base --is-ancestor <floor> HEAD` **passes**. **[fold: W4 — the ordering predicate is ancestry, not `≥`]**

**D6-2** — *Decision:* **this lane CANNOT and WILL NOT touch the shared checkout or the live `.env` pin.**
The lane is **STAGE-ONLY** (house ground rules) and the shared checkout carries another lane's uncommitted WIP
(cross-lane WIP-owner precedent, v0.27.1 lane). The precondition is therefore **owned by the user/leader** and
recorded as a **ceremony blocker**, not silently assumed.

**Consequence if unmet:** the daemon-side guard (iii) **refuses the arm** ⇒ *the ceremony is not consumed* and
the failure is **loud** instead of a silent hang. That is the correct failure mode.

---

## 4. The in-lane mechanisms

### 4.1 Daemon-seat provenance at arm time — journal-always **[fold: A28, D6-3]**

The arm journal row records, **always**:

| Field | Purpose |
|-------|---------|
| `scripts_dir` (the resolved absolute path) | which tree will actually run |
| `lib.sh` **sha256** | which `lib.sh` bytes will actually run (a tree can change under a path) |
| git **rev** (+ **dirty** flag) when the dir is a git checkout | whether the tree is the expected revision |

**D6-3** — *Decision:* pure observability, **zero refusal risk** — the arm always proceeds; the row always lands.
The seat is the daemon because **the daemon binary is the one component whose version the promote itself
guarantees**.

### 4.2 The stamp + the floor check — refuse BEFORE the nonce burns **[fold: A29, D6-4]**

| Piece | Where | Behaviour |
|-------|-------|-----------|
| `PIPELINE_SCRIPTS_REV=<this lane's merge commit>` | stamped **in `lib.sh`** | the running code identifies itself |
| `PIPELINE_SCRIPTS_FLOOR_COMMIT=<the commit introducing the preflight-hang fix>` — **SET AND VALIDATED AT RELEASE TIME (T6.15)**: the release process writes the actual fix commit into the daemon constant + the release manifest and **fails the release** unless it resolves as an ancestor of the built tree | **daemon constants + the release manifest** | the floor the daemon compares against — a value that is *set* and *validated*, never hand-copied |
| **the acquisition** 🔴 | **the DAEMON opens `<scripts_dir>/lib.sh` at ARM time** and parses `PIPELINE_SCRIPTS_REV=` from a **bounded first-N-KB read** — **BEFORE the nonce burn** | a parse failure ⇒ *"stamp absent"* ⇒ refuse on an explicitly pinned dir. **The executor-start echo is DEMOTED to a corroborating signal** (it proves *running code == stamped code* — a different property — and it arrives **after** the nonce burn, so it cannot gate anything). |
| **the comparison** 🔴 | **at arm time, BEFORE `consume_pending_action`** | **HYBRID, primary = GIT ANCESTRY** (leader ruling Q1): for a **git** pinned dir ⇒ read-only bounded `git -C <dir> merge-base --is-ancestor <floor> HEAD` ⇒ **pass iff ancestor**; a **missing/unknown floor object or command error ⇒ REFUSE**. For a **non-git** pinned dir ⇒ the **stamp file** when present (`PIPELINE_SCRIPTS_REV`), else **WARNING-only** (leader ruling Q2) |
| a **dirty** git checkout | arm time | journal **WARNING** (not a refusal) |
| a **fallback-dir** resolution (module-repo / `install_dir/scripts`) **without** a stamp | arm time | **WARNING only** — backward compatibility. The refusal applies **exclusively** to the explicit `ENSEMBLE_UPGRADE_SCRIPTS_DIR` pin, which is a deliberate operator choice. |
| `ENSEMBLE_ALLOW_STALE_SCRIPTS=1` | escape hatch | allows the arm; **journaled loudly**; **per-invocation only — never persisted to `.env`** (Adv2) |

**Why ancestry is the primary and it IS practical at the arm call site** (leader ruling Q1 asks for this
explicitly): the arm path already runs in the daemon process, the pinned dir is a **git checkout** on every known
deployment, and the check is a **read-only, bounded** `git -C <dir> merge-base --is-ancestor` — no fetch, no
checkout, no write. Ancestry gives **no false refusals from newer unrelated trees** (any tree that *contains* the
fix passes, however new) and **proves staleness exactly**; a bare sha256 allow-list cannot prove staleness of an
unknown sha. **The content-sha allow-list fallback is therefore NOT taken.** The two accepted false-refusal edges
are: (i) a **cherry-picked** fix (content present, fix commit absent) ⇒ a rare false refusal whose remediation is a
proper fetch/merge — accepted by the ruling; (ii) a **shallow clone** that lacks the floor object ⇒ REFUSE
(the ruling's "missing/unknown floor object ⇒ REFUSE": a tree that does not contain the fix commit object cannot
contain the fix).

**Floor ownership / storage / cadence (the B1 gap, now closed):** owner = **this lane's release process**;
storage = **daemon constants + the release manifest**; cadence = **bumps per release touching ceremony-relevant
`scripts/upgrade` semantics — the SAME cadence as the ratified `PIPELINE_SCRIPTS_REV` stamp; the two travel
together**.

**D6-4** — *Decision:* the refusal fires **before the nonce is consumed** *(this paragraph is the **sequencing clause** of `decisions.md` D6-4 — the arm-time acquisition + the floor check; the phase file and the register describe the same decision from two sides)* (`consume_pending_action`). Otherwise the
guard would **consume the very ceremony it protects** (architect R-G).

**D6-10** — *Decision (leader ruling Q1, 2026-10-29 — **FINAL**):* the floor mechanism is the **HYBRID, primary =
GIT ANCESTRY**: the daemon records `PIPELINE_SCRIPTS_FLOOR_COMMIT` (the commit introducing the preflight-hang
fix; owner = this lane's release process; storage = daemon constants + release manifest; cadence = bumps per
release touching ceremony-relevant `scripts/upgrade` semantics — the same cadence as the `PIPELINE_SCRIPTS_REV`
stamp, they travel together). At arm time, for a **git** pinned dir: read-only bounded
`git -C <dir> merge-base --is-ancestor <floor> HEAD` ⇒ **pass iff ancestor**; a **missing/unknown floor object or
command error ⇒ REFUSE** (a tree that does not contain the fix commit object cannot contain the fix;
cherry-picked-content edge = acceptable rare false refusal, remediation is a proper fetch/merge). **The
content-sha allow-list fallback is NOT taken** — ancestry is practical at the arm call site (read-only, bounded,
no fetch/write) and it is the only scheme that proves staleness exactly (a bare sha256 allow-list cannot prove
staleness of an unknown sha; ancestry produces no false refusals from newer unrelated trees — any tree containing
the fix passes, however new).

**D6-11** — *Decision (leader ruling Q2, 2026-10-29 — **FINAL**):* a **non-git** pinned dir is **WARNING-ONLY** —
never refused. The warning + journal row carries the pinned **`lib.sh` sha256** as provenance. Rationale: every
known deployment pins a git dev checkout; refusing an operator-chosen non-git dir could block an otherwise-correct
promote, and the sha256 journal row preserves the audit trail.

**D6-6** — *Decision:* **the guard's seat is the DAEMON, not the scripts.** *(id aligned with `decisions.md` D6-6 — the same topic; the phase file's D6-6/D6-7 pair was an id collision and is closed by this alignment, recorded in the `plan-overview.md` §11.1 map)* A stale `lib.sh` has **no stamp and no
check-code** ⇒ *a scripts-side guard structurally cannot catch a stale scripts dir*. The checking code must live
in the component the promote itself versions — the binary.

### 4.3 The watchdog token — mixed-generation defense **[fold: A30, D6-5]**

The executor env carries `ENSEMBLE_PROMOTE_WATCHDOG=1` (added to `EXECUTOR_ENV_ALLOWLIST`). `promote.sh`
**refuses** (a loud `refusal` row) when the token is present but the **watchdog is not armed** — catching a
mixed-generation deployment (new binary + half-synced scripts). Old scripts ignore the token; the floor check
(iv in §1) catches those.

**D6-5** — *Decision:* the token is a **belt**, not the mechanism: the floor check is the mechanism; the token
catches the case the floor check cannot (a synced dir at a *newer* rev that nonetheless lacks the watchdog).

---

## 5. Tasks

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks | AC |
|----|------|--------------------|-------------|------------|-------|----|
| **T6.1** | Record the **deployment-truth chain** (§2) in the plan + the lane's runbook as the **efficacy precondition**, with the measured numbers and their anchors. | The chain table is present in the plan and the runbook; the stale-checkout measurement is re-verified at implementation time (the checkout may have moved). | `phase6-plan.md`, runbook | — | R-A | AC1 |
| **T6.2** | Publish the **user-owned sync precondition** (sync-or-move-the-pin) in the ceremony runbook + the plan's rollout section; record that this lane **must not** touch the shared checkout or the live `.env`. | The runbook carries the precondition with the exact paths + the scope note (15 unrelated dirty files ⇒ the WIP owner reconciles); the plan states the ownership boundary. | runbook, `plan-overview.md` §9/§11 | T6.1 | R-A | AC2 |
| **T6.3** | Implement the **arm-time provenance** (daemon seat): the arm row carries `scripts_dir`, `lib.sh` sha256, and (when the dir is a git checkout) the git rev + dirty flag. | The arm row carries all three fields in a sandbox arm; the row is written even when the dir is not a git checkout (the fields degrade to n/a). | `daemon/tools/upgrade_tools.py` (arm path) | — | — | AC3 |
| **T6.4** | Implement the **stamp** (`PIPELINE_SCRIPTS_REV` in `lib.sh`, echoed at executor start into the journal row + the per-run log's first line) and the **daemon-side floor check** (`PIPELINE_SCRIPTS_FLOOR_COMMIT` — the Q1 floor constant; the pre-B1 name `MIN_PIPELINE_SCRIPTS_REV` is **superseded** and must not be minted). **[fold: W3]** | The executor start row carries the stamp; the per-run log's **first line** carries it. | `lib.sh`, `daemon/tools/upgrade_journal.py` | T6.3 | — | AC4 |
| **T6.5** | Implement the **arm-time refusal**: an explicitly pinned scripts dir that does **not** satisfy the **§4.2 comparison** — i.e. a git pinned dir whose HEAD does **not** contain `PIPELINE_SCRIPTS_FLOOR_COMMIT` as an ancestor (per `git -C <dir> merge-base --is-ancestor <floor> HEAD`), or a missing/unknown floor object or command error, or a stamp that cannot be parsed from `<scripts_dir>/lib.sh` ⇒ **refuse to arm**, with a refusal row **naming the sync command** — fired **BEFORE `consume_pending_action`**. A dirty checkout ⇒ WARNING; a fallback dir without a stamp ⇒ WARNING; `ENSEMBLE_ALLOW_STALE_SCRIPTS=1` ⇒ allowed and **journaled loudly**. | A stale-pinned sandbox arm **refuses with no nonce burn** and the row names the sync command; a synced dir arms cleanly; the dirty/fallback/escape-hatch branches behave as specified. | `daemon/tools/upgrade_tools.py` (arm path), `daemon/tools/upgrade_journal.py` | T6.4 | R-G | AC5 |
| **T6.6** | Implement the **watchdog token**: add `ENSEMBLE_PROMOTE_WATCHDOG=1` to the executor env allowlist; `promote.sh` refuses (loud `refusal` row) when the token is present but the watchdog is not armed. | A sandbox arm with the token + an unarmed watchdog ⇒ a loud `refusal` row; with the watchdog armed ⇒ proceeds. | `daemon/tools/upgrade_journal.py` (allowlist), `promote.sh` | P2 | R18 | AC6 |
| **T6.7** | Add the **runbook checklist item**: *"ceremony scripts provenance verified — the arm row shows the expected stamp."* | The runbook carries the item; the staged-verification lane asserts it. | runbook | T6.3 | — | AC7 |
| **T6.8** | **Rollout/efficacy section**: state the conditional efficacy explicitly — *with* the sync the ceremony runs the fixed scripts; *without* it the ceremony **refuses at arm time** (SC12) and **no ceremony is consumed** — and wire the staged-verification lane to assert **both** that the guard **exists** and that it **fires on a stale fixture**. | The staged-verification brief carries the two assertions; the efficacy statement is in the plan + the promotion notes. | `phase5-plan.md` Pack I, promotion notes | T6.5 | R-A | AC8 |
| **T6.9** | Implement the **Q1 hybrid floor mechanism**: the daemon constant `PIPELINE_SCRIPTS_FLOOR_COMMIT` (owner: release process; storage: daemon constants + release manifest; cadence: same as the `PIPELINE_SCRIPTS_REV` stamp) + the arm-time **git-ancestry** check (read-only bounded `git -C <dir> merge-base --is-ancestor <floor> HEAD`); a **missing/unknown floor object or command error ⇒ REFUSE**. The **non-git** branch reads the **stamp file** when present, else **WARNING-only** (Q2). **[fold: B1, Q1, Q2]** | A git pinned dir that **contains** the floor commit ⇒ arm proceeds (even if far newer); a git pinned dir that **does not contain** it ⇒ **REFUSE before the nonce burn**; a missing floor object ⇒ REFUSE; a non-git pinned dir **with** a stamp ⇒ proceeds; a non-git pinned dir **without** ⇒ WARNING, never refuse. | `daemon/tools/upgrade_tools.py` (arm path), daemon constants | T6.4 | R-A | AC9 |
| **T6.10** | Implement the **B3 arm-time acquisition**: the daemon opens `<scripts_dir>/lib.sh` and parses `PIPELINE_SCRIPTS_REV=` from a **bounded first-N-KB read**, **before `consume_pending_action`**; a parse failure ⇒ *"stamp absent"* ⇒ refuse on an explicitly pinned dir. **Demote the executor-start echo to a corroborating signal** (it proves running code == stamped code; it arrives after the nonce burn and cannot gate anything). **[fold: B3]** | The guard's input is read from the **file at arm time** (a pack assertion: the stamp is NOT taken from the executor); a lib.sh without the stamp ⇒ refuse on a pinned dir; the executor echo is present but is **not** the gating input. | `daemon/tools/upgrade_tools.py` (arm path), `lib.sh` | T6.9 | R-G | AC10 |
| **T6.11** | Implement the **Q2 warning-only posture** for a non-git pinned dir: **WARN + journal row carrying the pinned `lib.sh` sha256** as provenance; **never refuse** on that branch. **[fold: Q2]** | A non-git pinned dir arms (with the warning + sha256 provenance row); **no** refusal is emitted on that branch; a grep-assertion shows no refuse-path is reachable from the non-git branch. | `daemon/tools/upgrade_tools.py` (arm path) | T6.9 | R-A | AC11 |
| **T6.12** | **`ENSEMBLE_ALLOW_STALE_SCRIPTS` is PER-INVOCATION ONLY** — it must never be persisted to `.env` or any durable store; the arm journals it loudly when it is used. **[fold: Adv2]** | A grep-assertion finds the variable nowhere in a durable `.env` write path; the runbook/`.env` guidance states it is per-invocation; the arm journals its use. | runbook, `daemon/tools/upgrade_tools.py` | T6.5 | — | AC12 |
| **T6.13** | **Staged-verification split (W5)**: the **daemon-side** surface is verified from the **staged artifact**; the **shell** surface is verified from the **pinned checkout at the synced rev** (payloads ship **no `scripts/`** since v0.27.4). The arm row's **required** provenance fields are `git rev` **and** `lib.sh` sha256. **[fold: W5]** | The staged-verification brief states the split explicitly; the arm row **requires** both provenance fields (a missing field ⇒ the arm row is rejected by the pack). | phase5 Pack I, `daemon/tools/upgrade_tools.py` | T6.3 | — | AC13 |
| **T6.14** | **Sibling-lane surface re-verification** (council standing rule, RULING 10): re-verify the sibling lane (`wi-3f5bea95`) surface overlap **at implementation time** and record it in the stage adjudication (the "zero overlap" record is as-of 2026-10-29 against a base-sitting worktree). **[fold: council §open-questions 4]** | The stage adjudication carries a fresh overlap verdict (files + commits), dated at implementation time. | stage adjudication | — | R9 | AC14 |
| **T6.15** | 🔴 **RELEASE-TIME SET-AND-VALIDATE of `PIPELINE_SCRIPTS_FLOOR_COMMIT`** — at release time the lane's release process **sets** the shipped constant to the merge commit that introduces the fix, and **validates** it against the built tree: `git -C <release-tree> merge-base --is-ancestor <shipped-floor> HEAD` **must pass**; if the constant is **unset**, **unresolvable**, or **not an ancestor** of the built tree ⇒ **FAIL THE RELEASE** (do not ship). The daemon-side constant and the release manifest must carry the **same** value. **[fold: approver-002 blocker 9]** | A wrong shipped value converts the lane's failure mode from *"stale scripts run"* into *"NO arm can ever succeed"* — blocking every ceremony with nothing able to detect it. The set-and-validate step makes the value **provably** correct at build time, and the Pack I leg proves the guard **PASSES on a fresh dir** and **REFUSES on a stale one**. | `phase6-plan.md` §4.2 (the constant row) · the release process (`scripts/upgrade/stage.sh` manifest step + the daemon constant) | T6.4 | R-A | AC15 |

---

## 6. Acceptance Criteria

| ID | Criterion |
|----|-----------|
| **AC1** | The deployment-truth chain is recorded in the plan + runbook with its anchors, and the stale-checkout measurement is **re-verified** at implementation time (the checkout may have moved). |
| **AC2** | The runbook carries the **user-owned sync precondition** (sync-or-move-the-pin) with the exact paths and the WIP-owner scope note; the plan states that this lane **must not** touch the shared checkout or the live `.env`. |
| **AC3** | The arm row always carries `scripts_dir` + `lib.sh` sha256, and the git rev + dirty flag when the dir is a git checkout (degrading to n/a otherwise). |
| **AC4** | `lib.sh` carries `PIPELINE_SCRIPTS_REV`; the executor-start journal row **and the per-run log's first line** carry the stamp. |
| **AC5** | A stale explicitly-pinned scripts dir **refuses the arm BEFORE the nonce is consumed** (a refusal row naming the sync command; **no** nonce burn); a synced dir arms cleanly; a dirty checkout warns; a fallback dir without a stamp warns only; `ENSEMBLE_ALLOW_STALE_SCRIPTS=1` allows and is journaled loudly. |
| **AC6** | The executor env carries `ENSEMBLE_PROMOTE_WATCHDOG=1`; `promote.sh` refuses loudly when the token is present but the watchdog is not armed. |
| **AC7** | The runbook carries the checklist item *"ceremony scripts provenance verified — the arm row shows the expected stamp."* |
| **AC8** | The rollout/efficacy statement is explicit and conditional, and the staged-verification lane asserts **both** that the guard exists **and** that it fires on a stale fixture. |
| **AC9** | The **Q1 hybrid** floor behaves exactly as ruled: a git pinned dir **containing** the floor commit arms (even if far newer); a git pinned dir **not containing** it **refuses before the nonce burn**; a **missing/unknown floor object or command error refuses**; a non-git pinned dir **with** a stamp proceeds; a non-git pinned dir **without** one **warns and never refuses**. |
| **AC10** | The guard's input is read from `<scripts_dir>/lib.sh` at **arm time** via a **bounded first-N-KB read**, **before the nonce burn** (a planted "read the stamp from the executor" implementation **REDs**); a lib.sh without the stamp ⇒ refuse on an explicitly pinned dir; the executor-start echo is present but **corroborating only**. |
| **AC11** | A non-git pinned dir arms with a **WARNING + sha256 provenance row**; **no** refusal is reachable from that branch (grep-asserted). |
| **AC12** | `ENSEMBLE_ALLOW_STALE_SCRIPTS` is **per-invocation only** — it appears in no durable `.env` write path (grep-asserted) and its use is journaled loudly. |
| **AC13** | The staged-verification brief states the **daemon/shell split** (daemon side from the staged artifact; shell side from the pinned checkout at the synced rev, because payloads ship no `scripts/` since v0.27.4), and the arm row **requires** both `git rev` and `lib.sh` sha256 as provenance fields. |
| **AC14** | The stage adjudication carries a **fresh** sibling-lane overlap verdict, dated at implementation time. |
| **AC15** | **The shipped `PIPELINE_SCRIPTS_FLOOR_COMMIT` is provably valid**: the release-time step (T6.15) **sets** it to the fix-introducing merge commit and **validates** it with `git merge-base --is-ancestor <shipped-floor> HEAD` against the built tree; an unset / unresolvable / non-ancestor value **fails the release**; the daemon constant and the release manifest carry the **same** value; and the Pack I legs prove the guard **PASSES on a fresh dir** and **REFUSES on a stale one**. |

---

## 7. Fold log (architect → Phase 6), 2026-10-29)

| A# | Subject | Status | Where |
|----|---------|--------|-------|
| A26 | The numbered deployment-truth table | folded | §2, D6-1, T6.1, AC1 |
| A27 | The hard user-owned sync precondition | folded | §3, D6-2, T6.2, AC2, `plan-overview.md` §9 A6 / §11 |
| A28 | Daemon-seat provenance at arm time | folded | §4.1, D6-3, T6.3, AC3 |
| A29 | `PIPELINE_SCRIPTS_REV` stamp + daemon floor check **before `consume_pending_action`** | folded | §4.2, D6-4, D6-6, T6.4, T6.5, AC4, AC5 |
| A30 | `ENSEMBLE_PROMOTE_WATCHDOG=1` token + mixed-generation refusal | folded | §4.3, D6-5, T6.6, AC6 |
| A31 | Runbook checklist item | folded | T6.7, AC7 |
| RULING 9 | The whole section (deployment truth + the seat correction) | folded | §1–§4 |
| Leader ratification | **Stamp cadence** — `PIPELINE_SCRIPTS_REV` bumps per release touching ceremony-relevant `scripts/upgrade` semantics | folded (recorded) | `decisions.md` D6-8 |
| Leader ratification | **Dev-checkout sync (Decision 1)** — this lane CANNOT and WILL NOT touch the shared checkout or the live `.env` pin; the precondition is USER-owned | folded (recorded) | §3, D6-2, `plan-overview.md` §9 A6 / §11 |

---

## 8. Fold log (plan council round 3 → Phase 6), 2026-10-29

| Finding | Subject | Status | Where |
|---------|---------|--------|-------|
| **B1** | The floor comparison was undefined (git SHAs are unordered; no ordering mechanism existed) + the non-git pinned-dir case was undefined | **folded** | §4.2 (the Q1 HYBRID row) · D6-10 · T6.9 · T6.11 · AC9 · Pack I legs (phase5) |
| **B3** | The guard's only stated input acquisition (the executor-start echo) arrives **after** the nonce burn ⇒ it cannot gate anything | **folded** | §4.2 (the **acquisition** row — daemon reads `<scripts_dir>/lib.sh` at arm time, bounded first-N-KB) · T6.10 · AC10 · **M25** mutation leg (phase5) |
| **Q1** | The leader's ruling: HYBRID, primary = GIT ANCESTRY (`PIPELINE_SCRIPTS_FLOOR_COMMIT`; owner/storage/cadence; `merge-base --is-ancestor`; missing floor object ⇒ REFUSE; the content-sha fallback is the approved alternative but is **NOT taken** here) | **folded — VERBATIM** | §4.2 · D6-10 · T6.9 · AC9 |
| **Q2** | The leader's ruling: a NON-git pinned dir is **WARNING-ONLY** (WARN + journal row carrying the pinned `lib.sh` sha256; never refuse) | **folded — VERBATIM** | §4.2 · D6-11 · T6.11 · AC11 |
| **W5** | The staged-verification daemon/shell split + `git rev` + `lib.sh` sha256 as **required** arm-row provenance fields | **folded** | T6.13 · AC13 (the brief itself is in phase5) |
| **Adv2** | `ENSEMBLE_ALLOW_STALE_SCRIPTS` must be **per-invocation only**, never persisted | **folded** | §4.2 (escape-hatch row) · T6.12 · AC12 |
| **Council standing rule** | Sibling-lane (`wi-3f5bea95`) surface overlap must be **re-verified at implementation time** and recorded in the stage adjudication | **folded** | T6.14 · AC14 |
| **B1 predicate fix** | The "sync to **≥** this lane's merge rev" predicate is invalid (unordered SHAs) — replaced by the **ancestry** predicate | **folded** | §3 (precondition (a)) |
| **W2/Q3** | The size-guard exemption is **promote-path-only**; rollback/restart/status get WARN+continue | **folded** | **phase1 D1-14** (the guard's path scope) · **phase1 T1.17 / AC18** (the matrix cell) · **phase5 Pack J** — the guard itself is a D1 artifact, not a P6 one **[fold: A1 — the pointer corrected]** |
