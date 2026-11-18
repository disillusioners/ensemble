# guideline-wedge-notice-kill-switch

**Status:** draft
**Feature:** the wedge-notice EMISSION kill-switch (`wedge_notice_emission_enabled`,
S4 of the wake-path-carrier-fix lane) and the composed wedge-notice surface it
gates.
**Audience:** operator

---

## 1. Purpose & doctrine

The wedge notice is the watchdog's backstop message to a parent whose
`WAITING_CHILDREN` turn is parked. S4 made the notice **composed from
row-checked reason codes** (each code is emitted only when its check holds)
and gave the operator one durable lever over its **emission**.

**The doctrine sentence:** the switch hides the SYMPTOM; it never touches the
defect.

## 2. States

| State | How it is reached | What an operator sees | What is INERT in that state |
|---|---|---|---|
| **NOTICES ON (default)** | absence of the settings override row | wedge notices are enqueued as the row checks dictate | nothing is inert — the whole notice surface runs |
| **NOTICES OFF** | `wedge_notice_emission_enabled = false` (DB override) | no new wedge-notice rows | **ONLY the notice ENQUEUE** is inert |

**What stays LIVE when the switch is OFF** (this is the whole point of the
scope): the watchdog's detection, the reason-code classification, the S1
terminal fire, the S2 self-nudge arm guard, the S3 qualified park predicate,
the mission-strand healer's bus legs, and the report lane. A switch wired
into any of those would silence the FIX, not the notice.

## 3. EXACT enablement steps

- **Setting key:** `wedge_notice_emission_enabled` (settings registry;
  `value_type=bool`, `default=True`, `requires_restart=False` — the seventh
  restart-free key).
- **Truth table:**
  - **absent** → **ON** (notices are emitted) — the default is ON;
  - `true` → ON;
  - `false` → OFF (no new notice rows).
- **How to flip it:** the settings DB override row for the key (the
  `settings:write` surface). The registry key alone is VOID for this
  watchdog: the watchdog's own `_enabled` flag is a **boot-time** read, so
  the switch is read from the settings service **on every tick** (one DB
  read per tick, memoised for the tick). A mid-run flip therefore takes
  effect on the **next watchdog tick, with NO restart**.
- **Restart requirement:** none for the switch. (The watchdog service itself
  is started by the daemon; the switch does not need one.)
- **What NOT to do:** do not hand-edit `config.yaml` expecting the watchdog
  to pick it up — the watchdog reads the settings service's override rows,
  not the config file. Do not delete the registry entry to "disable" the
  notice — absence of the OVERRIDE means ON.

## 4. The rules that bite

1. **Wrong move:** assume the switch quiets the wedge class. **Silent
   consequence:** the underlying defect (a wait that can never fire) stays
   live and keeps holding a park — the switch hides the SYMPTOM only.
   **Signal:** none from the switch itself; the parent's park state is the
   signal.
2. **Wrong move:** assume the registry key alone is enough. **Silent
   consequence:** nothing changes (the registry entry makes the key
   *valid*, not *effective*). **Signal:** the notice rows keep appearing.
3. **Wrong move:** treat a missing notice as "nothing is wrong". **Silent
   consequence:** the watchdog only speaks when a parent is parked AND its
   subject is non-empty; a healthy parent is silent by design.
   **Signal:** the absence of a notice is not a health signal.
4. **Wrong move:** re-add a TTL to the notice. **Silent consequence:** the
   episode semantics break — an episode ends on the ROW-CHECK (the subject
   emptying), not on a timer; a TTL re-introduces the one-shot-forever
   silence the lane removed. **Signal:** a parent that stops being noticed
   while still parked.

## 5. Verification & probe

- **Probe (switch OFF):** set the override to `false`, then confirm the next
  watchdog tick adds **no new** wedge-notice rows for a parked parent, while
  the parent's park state and the healer's behaviour are unchanged.
- **Probe (switch ON):** clear the override (or set `true`); the next tick
  notices again with **no restart**.
- **A skipped probe is an open risk, not a pass.**

## 6. Evidence discipline

- The notices themselves are `message_queue` rows (type
  `completion_report`, source `system:watchdog:wedge`) carrying the
  `wedge_notice` metadata flag and the reason codes in the body.
- The watchdog keeps in-memory per-parent episode fingerprints (the
  reason-code set); they are **process-local** and reset on daemon restart.
- The switch's state is visible as the settings override row for
  `wedge_notice_emission_enabled`.
- Nothing about the watched children beyond their ids is recorded.

## 7. Rollback

- **Migration delta:** zero (no table, no column, no migration).
- The old binary coexists with the schema unchanged.
- Data that survives: the settings override row (harmless — the old binary
  ignores the key) and the notice rows already written.
- What is lost: nothing; flipping the switch back on restores emission.
- No operator act beyond the switch itself.

## 8. First real use

**Has not happened yet** — the lane must not be relied upon until the switch
is exercised on a live watchdog tick. The unit-level contract (emission-only
scope, per-tick read, default ON) is covered by the S4 test suite; the live
exercise is still owed.

## 9. Status

Draft. The honest limit, stated plainly: **switching it off HIDES THE
SYMPTOM while leaving the defect live.** No restart-free surface governs the
watchdog today, so this switch is the only durable operator lever for a
class that recurs — which is exactly why its scope is emission-only and why
its default is ON.
