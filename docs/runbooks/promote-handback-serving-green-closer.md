# Promote hand-back and the serving-green stale-txn closer

**Owner:** the promote/upgrade pipeline (`scripts/upgrade/`, `launcher.sh`, the
daemon's upgrade-journal sweep).
**Commission:** `promote-handback-halt` (incident run
`r-20261101-212542-8279`, target `v0.28.5`, 2026-11-01).

---

## 1. The incident this runbook explains

A promote **halted at hand-back** (the hand-back verifier's budget raced the
~3 min unit-active→serving boot — Phase 1 owns that verifier), leaving the
promote transaction **OPEN** with `flipped=true`. The target was **serving
all-green** (`livez` alive + `readyz` all-components-true).

At the next launcher boot the launcher's journal sweep saw a stale flipped txn
and executed its **AUTO-ROLLBACK** disposition: `current` → the previous
release, the healthy target **quarantined**, a rollback counted, the cooldown
armed. The next re-promote was refused: *"version 'v0.28.5' is QUARANTINED —
cleared only by re-staging"*.

**The owner's ruling (Discord 2026-11-01):** *"Don't roll back if that isn't
needed — when the new version is working and living, there is no reason to
roll back."* ⇒ **SERVING-GREEN = STAY**; rollback is reserved **exclusively**
for genuinely unhealthy serving.

The launcher's sweep is **structurally blind** to serving health: it runs
*before* the daemon child starts (`launcher.sh` — the journal sweep runs in
`main()`, the daemon child starts later in `run_loop`), so "the port is down"
cannot distinguish "the target is broken" from "this very boot is starting the
target". Hence the **split closer**: the launcher can only make a
**positive** verdict; the daemon (which is up) can read its own health.

---

## 2. The disposition table (read this first)

A stale, **flipped** promote txn is disposed of by ONE of three decision-
makers, all sharing this table:

| # | Decision-maker | When it runs | GREEN ⇒ | NOT-GREEN ⇒ |
|---|---|---|---|---|
| 1 | **Launcher boot sweep** (`launcher.sh`) | every launcher boot (pre-daemon) | commit-close | DEFER (bounded) |
| 2 | **Daemon janitor sweep** (`upgrade_journal_sweep.py`) | every 90 s tick (while the daemon is up) | commit-close | verdict + rollback **request** |
| 3 | **Promote-entry adopt** (`lib.sh` `adopt_stale_txn`) | the next promote against an unresolved txn | commit-close, the promote continues | the existing chain |

**The canonical GREEN predicate** (all three sides):

> **`/livez` 200** ∧ the reported **`version` == the v-stripped target**
> (`v0.28.5` vs `0.28.5` — the asymmetry is real) ∧ **`/readyz` 200** whose
> body reports **`ready`**.

**The authorization conjunct** (OQ-C-11, RATIFIED): a serving-green
commit-close fires only when **BOTH** hold — the positive serving verdict
**AND** an `intent_flip` history event naming **that same target**. A positive
health read proves the daemon is *running* the target; it does **not** prove
the flip was operator-authorized. Absent the authorization evidence: the
launcher **defers**, the daemon **takes no action**, the adopt path takes the
**existing** chain (a documented divergence — see §6).

**The three rollback-eligibility paths — a CLOSED set (there is no fourth):**

| Path | Trigger | Speed |
|---|---|---|
| **BROKEN** | the launcher got an HTTP answer and it was wrong (`/livez` non-200) | **immediate** — the existing chain fires now |
| **`rollback_requested`** | the daemon wrote the request event for this `(target, txn)` | **immediate** — the launcher re-probes and then executes the existing chain |
| **AMBIGUOUS** | no answer / wrong version / `/readyz` not ready | **bounded** — after ≥3 qualifying observations ∧ owner-dead ∧ `intent_flip` ∧ never-green-observed |

**Everything else DEFERS** (a journaled `sweep_deferred` row; the txn stays
OPEN; no quarantine, no counter, no cooldown, no rollback).

---

## 3. Reading the journal after the fact

| Event | Meaning | Operator action |
|---|---|---|
| `serving_green_commit` | a stale flipped txn was closed because the target is **serving green** — no rollback, no quarantine, no counter, no cooldown | none — this is the healthy outcome |
| `quarantine_cleared_serving_green` | a wrongful quarantine was **cleared** (the target was serving green) — the explicit clearance event the promote-entry gate accepts | none (the next promote is accepted) |
| `sweep_deferred` | the sweep saw the txn and **deferred** — the detail carries a greppable reason (`ambiguous-absent`, `ambiguous-mismatch`, `ambiguous-unready`, `ambiguous-probe-unprobeable-<detail>`, `request-probe-unprobeable-<detail>`, `green-without-intent-flip`, `request-green-without-intent-flip`, `green-owner-alive` (currently **UNREACHABLE** — AM-P2-18 returns on owner-alive ∧ hb-fresh before this arm; retained defensively), `never-green-observed-violated`, `observation-not-qualifying`, `journal-size-guard`, `tempfail-exit-not-counted`) | read the reason: `green-without-intent-flip` ⇒ the flip was **unauthenticated** (operator resolution needed); `ambiguous-*` ⇒ the target is not serving (the ceiling will fire); `*-probe-unprobeable-<detail>` ⇒ the probe could not answer (the detail names the class — e.g. `no-port`, `port-out-of-range`) |
| `serving_unhealthy` | the daemon judged the target **not serving** (outside grace) | the launcher executes the rollback at its next boot |
| `rollback_requested` | the daemon's rollback **request** (a request, never an execution) | the launcher executes it at its next boot; a **request** never pages "auto-rollback happened" (the alert kind is `upgrade_serving_unhealthy_requested`) |
| `sweep_rollback` | a rollback was **EXECUTED** (the existing chain) | the anti-flapping counter moved; at 3/24h promotes are refused |

**Alert kinds** (SSE): `upgrade_serving_unhealthy_requested` (a request),
`upgrade_auto_rollback` (an executed rollback or a quarantine),
`upgrade_serving_green_restored` (a clearance). `serving_green_commit` and
`sweep_deferred` are **silent** (a good outcome and a hold are not outcomes).

---

## 4. The kill-switches (TWO — both default to enabled)

| Switch | Side | Default | `=0` means |
|---|---|---|---|
| `SWEEP_SERVING_GREEN_ENABLED` | launcher | **1** | restore **today's** unconditional auto-rollback disposition (the escape hatch for an unverified platform) |
| `ENSEMBLE_UPGRADE_SERVING_GREEN_CLOSER` | daemon (read **per tick**) | **1** | disable the closer lane entirely — no `serving_green_commit`, no `serving_unhealthy`, no `rollback_requested` |

Both are named here for the operator; the launcher-side switch is the second
enumerated hard **ceremony pre-step** — its value must be stated in the
ceremony notes. `=0` is an escape hatch, **never** a permanent state.

A third knob exists for the daemon's boot-grace horizon:
`UPGRADE_CLOSER_BOOT_GRACE_S` (default **300**, **env-overridable, RAISE-only**
— it may never be lowered below the hand-back budget's 300 s floor, because
both windows must cover the same measured unit-active→serving boot).

Note the two windows are DIFFERENT knobs with a shared floor:
`HANDBACK_VERIFY_BUDGET_S` (default 300) is the **shell-side verify-budget
ceiling** — how long the hand-back verifier waits for the unit to serve —
while `UPGRADE_CLOSER_BOOT_GRACE_S` is the **daemon-side boot-grace
horizon**. The daemon's effective grace is floored at
`max(UPGRADE_CLOSER_BOOT_GRACE_S, HANDBACK_VERIFY_BUDGET_S)`, so raising the
verify budget can never leave the closer judging NOT-GREEN before the
verifier has finished waiting.

---

## 5. The bounded absent-ceiling (and the large-journal regime)

The launcher counts **qualifying AMBIGUOUS observations** in a
**launcher-local** file `<INSTALL_DIR>/.sweep-defer-<run_id>` (not a journal
field — the launcher cannot create one; the file is O(1) and survives launcher
restarts). The file records `absent=`, `mismatch=`, `unready=` and `broken=`.
There is **no** `green=` flag: the plan-mandated sticky `green=` counter was
dropped (it was never implemented — the ladder's increment case covers only
absent/mismatch/unready) and `intent_flip` is the declared mechanism. The
never-green predicate is therefore a **journal-row** check (below).

* The ceiling fires at **3** qualifying observations (`SWEEP_DEFER_CEILING`).
* A **qualifying** observation requires `owner-dead` ∧ `intent_flip` ∧
  `never-green-observed` — all three.
* **never-green-observed** = no GREEN observation has EVER been recorded for
  this txn — judged **solely from the journal history** (a
  `serving_green_commit` / `quarantine_cleared_serving_green` row for this
  target; the AM-P2-1 crash-prefix shape — the row landed while the txn was
  still open — is included). Once such a row exists the ceiling is
  **permanently ineligible** for that txn.
* The counter file is **deleted** when the txn commits or rolls back.

**The large-journal regime.** The launcher has a journal-size guard
(`SWEEP_JOURNAL_SIZE_GUARD_BYTES`, 98304 by default). Past it the launcher's
never-green path **degrades to a conservative defer** — it **never** rolls
back there. In that regime the **daemon-side closer is the only automatic
rollback path** (it parses the journal in-process, so it is not subject to the
launcher's history-extraction cost).

---

## 6. The known residuals (operator-bounded)

| Residual | Shape | What the operator does |
|---|---|---|
| **GREEN but unauthorized** | the target is serving green but there is **no** `intent_flip` evidence for it (a hand-edited or forged journal) | the txn stays OPEN by design: it is **not** committed (never commit-close an unauthorized flip) and **not** rolled back (serving-green = stay). Resolve it manually: verify the flip's provenance, then either re-stage/promote deliberately or roll back with `rollback.sh` |
| **No further launcher boot** | `tempfail-75` (a persistent tempfail is normally *"PG unreachable?"* — an environmental failure) and `clean-exit-0` produce **no** further launcher boot, so the launcher's bounded ceiling can never be reached | the terminal escape is a **manual** `rollback.sh` (it counts toward the 3/24 h cap and arms no cooldown). `systemctl restart ensemble-main` advances the sweep |
| **A live owner** | a slow-but-live promote (the 900 s ceremony cap exceeds the 600 s staleness gate) | the sweep **leaves the txn open** (the owner-liveness conjunct) — a live promote is never disowned |

---

## 7. The v0.28.5 note

`quarantine_cleared_serving_green` **cannot rescue v0.28.5 itself** — the
event fires only while the target **is serving** green, and v0.28.5 was rolled
back off the live symlink (`current` = `v0.28.4`, `quarantined` =
`['v0.28.5']`). The live v0.28.5 therefore stays quarantined and must be
**re-staged** — which is precisely what this lane's content ships as (the
next release). The clearance path exists for the **future** wrongful-quarantine
shape (a release that is serving green and gets quarantined by a halt).

---

## 8. Where the code lives

| Piece | File |
|---|---|
| the launcher ladder + the purity-safe probe | `launcher.sh` (`_js_serving_probe`, `_js_sweep_flipped_disposition`, `_js_sweep_rollback_chain`, `_js_serving_commit_close`) |
| the defer counter | `launcher.sh` (`_js_defer_counter_*`, `<INSTALL_DIR>/.sweep-defer-<run_id>`) |
| the daemon closer | `daemon/services/upgrade_journal_sweep.py` (`sweep_serving_green_closer`, `UPGRADE_CLOSER_BOOT_GRACE_S`) |
| the journal vocabulary + the alert map | `daemon/tools/upgrade_journal.py` (`ALERT_KIND_BY_EVENT`, `_TERMINAL_EVENTS`, `WAKE_EXCLUDED_EVENTS`, the per-entry readers) |
| the shell readers + the gate amendment + the adopt path | `scripts/upgrade/lib.sh` (`journal_history_has_intent_flip`, `journal_quarantine_cleared_serving_green`, `promote_entry_check`, `adopt_stale_txn`) |
| the operator pins | `tests/packs/promote_handback_halt_serving_green_test.sh`, `tests/unit/tools/test_upgrade_serving_green_closer.py` |

---

## 9. Phase 3 — the ceremony preconditions (the cooldown rider + the scripts-pin advance)

**The rider.** The launcher's rollback-cooldown arm (`_js_count_rollback`)
was BSD-only (`date -jur`): on GNU the dialect fails, the fallback wrote
`cooldown_until = NOW`, and the ADR-005 10-minute anti-flapping window was
**silently disarmed** — the same incident night, the armed stamp *predated*
the sweep row that armed it. The arm is now a two-`uname` dispatch; on an
**unrecognized platform** it writes an **unparseable marker** so the
ceremony-side consumer (`lib.sh` `journal_cooldown_active`) reads the
cooldown as **ACTIVE** (fail-closed — the window stays armed, never silently
disabled). The fix ships **inside `launcher.sh`**, i.e. in the release
payload ⇒ it reaches live **at the next promote** (interim protection = the
daemon-side closer + the launcher defer path above). Pins:
`tests/test_launcher_cooldown_arm.sh`.

### 9.1 The two-sided release-bump requirement (giter-owned, stage-time)

The release bump commit MUST set **BOTH** of these to the **SAME merge
commit** (the commit that introduces this lane's ceremony-relevant
`scripts/upgrade` + promote semantics):

| Channel | Site |
|---|---|
| the daemon-side arm-time floor | `daemon/tools/upgrade_tools.py` → `PIPELINE_SCRIPTS_FLOOR_COMMIT` |
| the scripts-side stamp | `scripts/upgrade/lib.sh` → `PIPELINE_SCRIPTS_REV` |

They are two provenance channels for one fact ("the two travel together",
D6-8/T6.15/AC15). `stage.sh` **refuses to stage (exit 78)** when either side
is unset / not a sha / unresolvable / not an ancestor of the built tree, or
when the two differ; the staged manifest carries
`pipeline_scripts_floor_commit` so the artifact is self-describing. The
executable proof lives in `tests/test_pipeline_scripts_floor_stamp.sh`
(P3-PIN-13/-14 + the in-tree equality pin).

### 9.2 The USER-OWNED ceremony precondition (stated — not this lane's work)

> **Before the next scripted ceremony, the live `.env`
> `ENSEMBLE_UPGRADE_SCRIPTS_DIR` pin must be advanced to a tree whose HEAD
> has the new floor commit as an ancestor.**

* Enforcement: the **arm-time provenance guard**
  (`daemon/tools/upgrade_tools.py`, phase 6 Check 4) is judged **BEFORE**
  `consume_pending_action` — a refusal **never burns the nonce**. An
  **explicit** pin that predates the floor ⇒ **REFUSE pre-nonce** (the guard
  working, not a regression); a **fallback** resolution is a WARNING only.
* The current pin target (`ensemble-src-wt-scripts-pin` @ `22fe83317`)
  predates this lane's floor ⇒ as-is, the ceremony **refuses pre-nonce by
  design**. Advance the pin (or ff the pinned checkout) to a tree containing
  the new floor.
* Second enumerated pre-step: confirm the launcher kill-switch
  **`SWEEP_SERVING_GREEN_ENABLED=1`** (the default) is in force on the
  ceremony's launcher, and **state the value in the ceremony notes** (`=0`
  restores the pre-lane unconditional auto-rollback — an escape hatch,
  never a steady state).
* **Third enumerated pre-step (council A-1, final fold):** if the ceremony
  raises `HANDBACK_VERIFY_BUDGET_S`, the raised value must live in the
  **INSTALL `.env`** — NOT the ceremony shell. `launcher.sh` never sources
  `lib.sh`, so the lib.sh `export` reaches only the pipeline's own
  children (promote/stage/rollback shells); the daemon child sees the knob
  ONLY through the `.env` seam (launcher.sh:226 exports every `.env`
  key). A shell-exported raise is INVISIBLE to the daemon-side grace
  floor (`max(UPGRADE_CLOSER_BOOT_GRACE_S, HANDBACK_VERIFY_BUDGET_S)`).
* **Fourth enumerated pre-step (council A-4, final fold):** ensure
  **`PORT` is set** in the install `.env` before the ceremony. A PORT-less
  boot makes the launcher's serving probe **unprobeable** (never counted,
  never rollback-eligible) — and therefore **defers even a
  `rollback_requested`** rather than executing it unverified; the request
  rides the next correctly-configured boot. While unset, also verify
  `config.yaml` carries a `daemon.port` key — otherwise the daemon
  silently binds the pydantic default (8079) and the probe's target would
  be wrong even with `PORT` set (the launcher now WARNs on that shape).

### 9.3 The disposition doctrine at the gate (the reconciled sentence)

**Serving-green ⇒ NEVER rolled back** (the owner ruling, absolute) ·
**serving-unhealthy ⇒ the existing chain still fires, BOUNDED** by the
absent-ceiling (≥3 qualifying AMBIGUOUS observations ∧ owner-dead ∧
`intent_flip` ∧ never-green-observed). The commission's literal *"NEVER
rollback"* wording is **not** the ratified rule — the bounded ceiling is a
leader-ratified bounded ceiling, and this runbook and the register agree.
