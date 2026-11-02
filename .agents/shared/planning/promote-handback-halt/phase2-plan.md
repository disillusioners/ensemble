# Phase 2: Serving-Green Stale-Txn SPLIT CLOSER

Date: 2026-11-02
Author: worker via plan-creation (planner[v2] dispatch)
Status: Draft — for architect review (next gate)
Plan set: `.agents/shared/planning/promote-handback-halt/` (phase1 = hand-back verifier budget; phase2 = THIS file; phase3 = test-map consolidation + cooldown rider)
Base tree: `941cb6321` (== `origin/latest` tip at branch creation), worktree `/home/nea/ensemble-src-wt-promote-handback-halt`, branch `fix/promote-handback-halt`

---

## Objective

Make a stale, already-flipped promote transaction resolve on **observed serving health** instead of on a fixed timeout: when the target release is **demonstrably serving green**, commit-close the transaction (stay on the new release, undo any wrongful quarantine) and **never** roll it back; when the target is **demonstrably not serving**, the **existing** never-green auto-rollback chain still fires unchanged. **[PLANNER r2]** The never-green path is armed by **two** mechanisms, and the r2 fold makes the second one real: (i) a **BROKEN** observation (`/livez` answered non-200) ⇒ the existing chain fires **immediately**; (ii) an **AMBIGUOUS** observation (no answer / wrong version / `/readyz` not ready) ⇒ the existing chain fires after the **bounded absent-ceiling** (≥3 qualifying observations ∧ owner-dead ∧ `intent_flip` ∧ never-green-observed) — **AM-P2-10**. Before the r2 fold only (i) existed and the canonical never-green shape (every observation ambiguous) rolled back **nothing** — the council's REJECT basis.

Single testable sentence: *a stale flipped txn whose target is serving green ends in a `serving_green_commit` journal close with the target still live, un-quarantined, and with no rollback counted — while a stale flipped txn whose target is not serving green still ends in the existing `sweep_rollback` disposition.*

## Background

On 2026-11-01 ~21:43Z, promote run `r-20261101-212542-8279` flipped the release and then **halted at hand-back** (the 180s verify budget raced a ~3min unit-active→serving boot), leaving the txn **open** with `flipped=true`. The target (`v0.28.5`) was **serving all-green** — receipt `21:49:50Z` livez `alive v0.28.5` + readyz all-components-true. At `22:25:53Z` an operator restart booted the launcher; the launcher-boot sweep saw a stale flipped txn (age 3540s > `SWEEP_STALE_S=600`) and executed the **AUTO-ROLLBACK** disposition (`launcher.sh:1209-1242`) — repointing `current`→`v0.28.4`, **quarantining the healthy `v0.28.5`**, and counting a rollback. The next re-promote was then refused: *“version 'v0.28.5' is QUARANTINED — cleared only by re-staging”*. Root cause: the launcher-boot sweep is **structurally blind to serving health** (it runs at `launcher.sh:1661-1662`, *before* the daemon child starts at `:1525`), so "port down" is ambiguous — broken release, or *this very boot* starting the daemon.

## Scope

### In Scope

| # | Item | Anchor |
|---|------|--------|
| 1 | Launcher-side serving-green commit-close + defer (replacing the unconditional auto-rollback default) | `launcher.sh:1068-1242` |
| 2 | Purity-safe (R-08) serving-verify primitive inside `launcher.sh` | `launcher.sh:568-606` (`_js_run_bounded`), `launcher.sh:1040-1053` (R-08 constraint) |
| 3 | Journaled **defer** disposition + a **bounded** defer ceiling (backstop) | `launcher.sh:443-453` (`_js_history_append`), `launcher.sh:387-393` (no-field-create rule) |
| 4 | Daemon-side serving-green closer (new lane in the janitor sweep) | `daemon/services/upgrade_journal_sweep.py:1950-2019` (`sweep_once`), `:2108-2123` (`_run`) |
| 5 | Daemon-side in-process health read (version + cached readiness composite) | `daemon/api.py:5436-5450` (`/livez`), `:5452-5533` (`/readyz`), `daemon/api.py:2717`/`:4452` (composite set) |
| 6 | `quarantine_cleared_serving_green` — the **explicit journaled** clearance path, **amended on BOTH sides** | `scripts/upgrade/lib.sh:6559-6562` (frozen gate), `lib.sh:1945-1960` (`journal_quarantine_clear`) |
| 7 | `adopt_stale_txn` disposition (the promote-entry mirror of the same wrongful-rollback shape) | `lib.sh:6627+`, `lib.sh:6579-6626` (decision table) |
| 8 | New journal event kinds + their alert-map / terminal-event membership | `daemon/tools/upgrade_journal.py:646-651`, `:1874-1884` |

### Out of Scope

| # | Item | Why out | Where it lands |
|---|------|---------|----------------|
| 1 | Hand-back **verify budget** (the 180s `LIVEZ_BUDGET_S` race) | Phase 1 owns it; this phase consumes its *outcome* but does not touch the verifier | Phase 1 |
| 2 | `_js_count_rollback` BSD-only `date -jur` cooldown (GNU defect, zero-length cooldown live tonight) | Leader-flagged as a *scope rider* onto the **same disposition chain** this phase modifies; it is a **shared-surface** edit → sequenced **after** this phase lands to avoid two edits to the same chain in one commit | Phase 3 (rider) |
| 3 | The promote-side hand-back abort predicate itself | Phase 1 | Phase 1 |
| 4 | Any change to `reconcile_pending_op`'s `in_flight`-dict guard | The closer is a **separate lane with its own predicate** (see §D.1) — the existing guard stays untouched | — |
| 5 | Live-install contact / promote ceremony | This is a planning artifact; no implementation, no live calls | — |

**Sequencing note (shared surface):** Phase 2 rewrites the *disposition decision* inside `launcher.sh`'s flipped branch. The Phase-3 cooldown rider rewrites `_js_count_rollback` (`launcher.sh:473-478`) which is *called by* the disposition chain Phase 2 re-orders. Phase 2 must land first (or the rider must be folded in-commit with it); the rider must not be implemented on top of the pre-Phase-2 chain.

---

## Design

### D.0 — The three decision-makers after this phase

| Decision-maker | Runs when | Can it observe serving health? | Disposition after Phase 2 |
|---|---|---|---|
| **Launcher boot sweep** (`launcher.sh:830-1258`) | every launcher boot (the systemd unit's `ExecStart` **is** the launcher — `scripts/systemd/ensemble-daemon.service` `ExecStart=__LAUNCHER__` + the “WHY THE LAUNCHER is the ExecStart” rationale) | **Partially** — pre-daemon, so "port down" is ambiguous; but a **positive** verdict is obtainable (the daemon may already be up from a previous life) | serving-green → **commit-close**; not-positively-green → **defer** (bounded); defer-ceiling exceeded → **existing auto-rollback** |
| **Daemon janitor sweep** (new lane) | every 90s tick (`DEFAULT_UPGRADE_JOURNAL_SWEEP_INTERVAL_SECONDS=90`, `upgrade_journal_sweep.py:96-99`) | **Yes** — in-process (`__version__` + cached readiness composite) | serving-green → **commit-close** (+ quarantine clearance); unhealthy → **journaled rollback disposition** for the launcher to execute |
| **Promote-entry adopt** (`lib.sh:6627+`) | the next promote against an unresolved txn | **Yes** — `lib.sh` is not purity-constrained; `curl` is available (`lib.sh:3804+`, `_probe_once_classified` at **`:3874`** **[PLANNER r2 → AM-P2-22]**) | serving-green → **commit-close** and let the promote continue; not-green → **existing chain unchanged** |

### D.1 — Daemon-side closer: seam and predicates

**Seam.** A new method on `UpgradeJournalSweepService`, invoked from `sweep_once` (`daemon/services/upgrade_journal_sweep.py:1950-2019`) *after* the `reconcile_pending_op` call and *before* `gc_pending_actions`. It is a **separate lane**, deliberately **not** routed through `reconcile_pending_op` — that function returns `None` whenever `in_flight` is a dict (`daemon/tools/upgrade_journal.py:2035-2036`), and the existing “flipped=true never-touch” guard (`upgrade_journal.py:2262-2272`) is *correct* for the restart-arm lane. Carving an exception into those guards would widen a proven fail-closed path; a new lane with its own narrow predicate does not.

**Error contract.** Never raises (janitor contract — mirror the per-lane `try/except` + WARN shape at `upgrade_journal_sweep.py:1988-2020`); a failed tick logs and the next tick retries.

**Predicates (ALL must hold — any miss ⇒ no action this tick):**

| # | Predicate | Value / source |
|---|-----------|----------------|
| P1 | `self._install_dir is not None` | `upgrade_journal_sweep.py:1967` (existing guard) |
| P2 | journal readable & untorn | `uj.journal_read` → `JournalTorn` ⇒ skip (`upgrade_journal.py:260`) |
| P3 | `in_flight` is a dict | `upgrade_journal.py:2035` shape |
| P4 | `kind != "restart"` | mirror the launcher's rule (`launcher.sh:859-862`); restart-kind is owned by the daemon boot sweep |
| P5 | `flipped == "true"` **OR** symlink-healed (journal `current` ≠ target while `current` symlink → `releases/<target>`) | mirror `launcher.sh:975-982` — the symlink is the authoritative flip evidence |
| P6 | `age > SWEEP_STALE_S` (600s) | **NEW** daemon constant mirroring the protocol (`launcher.sh:69`); the daemon has no equivalent today (`EXECUTOR_STALENESS_WINDOW_S=900` at `upgrade_journal_sweep.py:112` **[PLANNER r2 → AM-P2-22; the plan previously cited `:110` — the constant is declared at `:112`, already recorded as `decisions.md` B-3]** is a *different* predicate — reaper liveness, not txn staleness) |
| P7 | txn `owner_pid` dead | mirror the launcher's `_js_pid_alive_bounded` (`launcher.sh:627-639`); the daemon side reuses the sweep's own `os.kill`-based liveness shape (`_executor_alive_recent`, `upgrade_journal_sweep.py:2020+`) |
| P8 | boot-grace window elapsed **AND `grace_active == False`** | **[PLANNER r3 → AM-P2-24]** the horizon is the closer's own **`UPGRADE_CLOSER_BOOT_GRACE_S`** (default **300**, **≥ `HANDBACK_VERIFY_BUDGET_S`**) — **not** the borrowed `READINESS_GRACE_HORIZON_S`=120 pool-busy constant (`daemon/constants.py:165`); the `grace_active` conjunct comes from the composite (`daemon/services/readiness.py:407`) — see below |
| **P9** | **authorization**: the journal history contains an `intent_flip` event naming **this txn's target** | **[PLANNER r5 → AM-P2-4's P9 — PROMOTED FROM THE AMENDMENT INTO THE TABLE]** a **NEW daemon history read** (none exists today — `lib.sh` has **zero** `intent_flip` occurrences [DEV-FOLD-2026-11-02 — approver iter003 note 4: the shell writer is `promote.sh:481-482` via `lib.sh`'s generic `journal_history_append`; the python writer is `daemon/tools/upgrade_journal.py:392-407`]; the shell twin is `launcher.sh:518-536` `_js_history_has_intent_flip`, and per **AM-P2-14** the new reader must be **per-entry**, not a whole-array conjunction). **Absent P9 ⇒ no-action-this-tick — never commit, never rollback** (AM-P2-4's canonical clause (ii)) |

**[PLANNER r5 → BLOCKER 1 — the table above now carries P9; the implementer-facing reading changes.**** Until this fold, the predicate table ended at **P8** and the `intent_flip` conjunct existed **only inside the amendments** (AM-P2-4) — so an implementer building the daemon closer from **§D.1 / T8 / P-6 as written** would ship a closer that **commit-closes on health alone**, i.e. an **unauthorized** flip (a hand-edited or forged journal) would be committed as a legitimate one. That is the **R3 hinge** in its most damaging direction, and it is exactly what OQ-C-11 (RATIFIED: *"intent_flip is REQUIRED on every serving-green commit, BOTH sides"*) forbids. ⇒ **the daemon half of the authorization conjunct is now in the normative table itself, not merely in an amendment.** The table's header rule (**"ALL must hold — any miss ⇒ no action this tick"**) now covers P9: **no P9 ⇒ no action this tick** (no commit, no verdict, no request). The **launcher** half was already explicit at its own sites (§D.2 rungs 0/1, `T2`'s gate, `launcher.sh:1068-1079`) and is **unchanged**. **Scope of the change:** documentation only — the predicate set, the code paths and the pin ids are **unchanged**; P9 already existed (AM-P2-4) and is now *visible* where the implementer reads it. **Companion sites folded in the same pass (all [PLANNER r5]):** `T8` (P1-P8 ⇒ **P1-P9**), `P-6` (the GREEN arm's authorization precondition), `S-4`/`S-5`, `R-5` **[DEV-FOLD-2026-11-02 — approver iter003 note 5: R-5's same-pass fold is the horizon correction (AM-P2-24's `UPGRADE_CLOSER_BOOT_GRACE_S`), not a conjunct addition — listed here as a companion of the same pass, not of the same class]**, the §D.1 *GREEN actions* paragraph, the matrix (a) daemon bullet, §D.4's adopt step 2, `decisions.md` **D.1**, `phase3-plan.md` **P3-PIN-05 / P3-PIN-11 / P3-PIN-11a**, and `plan-overview.md` §4 row 2.

**Boot-grace window.** Mirror `GraceTracker` (`daemon/services/readiness.py:754-810`): **observation-relative**, armed on the *first* eligible observation, **never extended**, re-arm only after a full lapse. Rationale: the daemon judges its *own* health; at boot the readiness composite is legitimately degraded (MCP warmup + auto-continue), so an immediate “unhealthy” verdict would be a false negative. The grace is a *boot* grace, not a txn grace — it is armed on the first tick where P1-P7 hold, and it does **not** re-arm per tick.

**[PLANNER r3 → AM-P2-24] The horizon is CHANGED — it is no longer the borrowed pool-busy constant.** The r2 text set this horizon to `READINESS_GRACE_HORIZON_S = 120.0` (`daemon/constants.py:165`). That constant is the readiness service's **load/pool-busy** grace horizon — a different question, and 120 s cannot cover a 210–360 s slow boot ⇒ the warmup observation landed outside the grace ⇒ the verdict fired ⇒ the write-once `rollback_requested` stood ⇒ tonight's class. ⇒ **the horizon is now the named constant `UPGRADE_CLOSER_BOOT_GRACE_S`** (env-overridable, **default 300**), **derived from the hand-back budget family**: `UPGRADE_CLOSER_BOOT_GRACE_S` **≥ `HANDBACK_VERIFY_BUDGET_S`** (Phase 1's knob, default **300**, `lib.sh:180`) — the two windows must cover the same measured unit-active→serving boot, and the closer's horizon may be raised above the hand-back budget but **never lowered below it**. `READINESS_GRACE_HORIZON_S` is **not** borrowed and **not** referenced by this lane. The *shape* of the window (observation-relative, never extended, re-arm only after a full lapse) is **unchanged**.

**[PLANNER r3 → AM-P2-24] The P8 conjunct GAINS a grace conjunct.** P8 is no longer "the boot-grace window elapsed" alone — the closer's verdict additionally requires **`grace_active == False`** on the composite it read. See the NOT-GREEN actions below and the restored invariant.

**Health read (in-process — no HTTP self-call, no DB):**

- **version**: `daemon.__version__` (`daemon/__init__.py:3`, currently `"0.28.5"`) — the same value `/livez` reports (`daemon/api.py:5448`, `version=__version__`; model `LivezResponse.version`, `daemon/models/common.py:138`).
  **⚠ VERSION-STRING ASYMMETRY:** the release directory/target is `v0.28.5`; `__version__` is `0.28.5` (no `v`). Every comparison on either side must strip a leading `v` from the target. A naive `target == __version__` is **always false** and would make every healthy target look unhealthy → a wrongful rollback. This is a **must-pin** detail.
- **readiness**: the **cached** composite `app.state.readiness_composite` (set `None` at `daemon/api.py:2717`, refreshed at `daemon/api.py:4452`, served O(1) at `daemon/api.py:5463-5466`). The sweep service today receives only `install_dir` + `manager` (`upgrade_journal_sweep.py:154-160`, wired `daemon/api.py:2848-2857`) ⇒ **NEW constructor kwarg** `readiness_provider: Callable[[], ReadinessComposite | None] | None = None`, wired at the **same lifespan seam** as a **provider (callable), not a captured value** (the composite is refreshed periodically; capturing a value would go stale). `None` ⇒ the lane is a no-op (dev/unwired installs — the same posture as `manager=None`).
- **GREEN** = version matches the target (after `v`-strip) **AND** composite is not `None` **AND** `composite.ready` is True.
- **NOT-GREEN** = composite missing / `ready` False after the grace window, **or** version mismatch (the running daemon is not the target — e.g. the launcher↔binary skew documented at `launcher.sh:1200-1208`).

**GREEN actions (commit-close), in this order.** **[PLANNER r4 → AM-P2-1] The order is AM-P2-1's HISTORY-BEFORE-CLOSE order — it is NOT "mirror `launcher.sh:1108-1127`".** *In-tree re-verification at this fold (base `941cb6321`):* `launcher.sh:1108-1127` is the **OLD close-before-history** order — `:1110` `current`→target · `:1118` `previous`→displaced · `:1120` `in_flight`→null · `:1122` history append. Mirroring it closes the txn **before** its terminal event exists ⇒ **AM-P2-10.3's journal check is defeated** (the `serving_green_commit` / `quarantine_cleared_serving_green` evidence must exist while the txn is still open) and **P-19 / P3-PIN-23 would red** against the correct implementation. The prior "ordering mirrors … `launcher.sh:1108-1127`" prescription **at this site** is therefore **SUPERSEDED** (append-only annotation; the superseded text is retained above this paragraph for the audit trail).

**[PLANNER r5 → BLOCKER 1 — the authorization precondition is now stated AT the GREEN-action list, not only in the predicate table and the amendments.]** The five steps below are reachable **only** when the predicate table's **P9** holds (journal history carries an `intent_flip` event naming this txn's target) — **the order is not the gate; P9 is.** ⇒ **Absent P9 ⇒ the daemon closer takes NO action this tick: no `serving_green_commit`, no `quarantine_cleared_serving_green`, no `serving_unhealthy`, no `rollback_requested`** (AM-P2-4's canonical clause (ii): *"launcher DEFER / daemon no-action-this-tick / adopt existing-chain — never commit, never immediate rollback"*). An implementer reading only this list (the most likely reading, because it is the step-by-step) would otherwise build a **health-only** commit-close — the exact failure the approver named. The **NOT-GREEN actions** paragraph below is gated by **the same P9** (the predicate table's header rule is *all-or-nothing*): P9-absent ⇒ no verdict and no request either.

1. `current` → `target`
2. `previous` → `in_flight.displaced_version` (C-4 forward semantics; **must** land **after** (1) and **before** (4) — the `in_flight` txn must still be readable when it is written; the same ordering pin as `launcher.sh:1112-1117`). Absent/`"null"` `displaced_version` ⇒ **halt-for-human** (mirror `launcher.sh:1094-1106`): the txn is old-format/ambiguous, so no silent commit.
3. history append `serving_green_commit` — and, **FIRST** if the target ∈ `quarantined[]`, append `quarantine_cleared_serving_green` (AM-P2-1 step 3; the event is the amended shell gate's acceptance signal per §D.3 ⇒ event-before-close makes the clearance crash-proof)
4. `in_flight` → `None`  ← the txn closes **ONLY here**
5. `quarantined[]` array rewrite (state hygiene; idempotent; retry-safe — the gate accepts on the event, so a trailing un-rewritten array cannot re-refuse). **Scope (AM-P2-1's scoped step 5 / T5's omission):** the **daemon** side owns the array rewrite; the **launcher** side rewrites only when the `quarantined` literal is already present in the document (`_js_journal_update` cannot create a field — `launcher.sh:387-393`), treating absence as a **no-op, not an error**.

**NOT** done on GREEN: quarantine marking, rollback counter increment, cooldown arming. The doctrinal precedent is explicit and in-repo: *“NOT done: sweep-rollback, quarantine, counter+cooldown arming. The cap/cooldown are anti-flapping responses to BAD outcomes; a successful-and-declared flip is not a bad outcome”* (`launcher.sh:1034-1036`).

**NOT-GREEN actions:** journal a `serving_unhealthy` verdict carrying the reason (not-ready / version-mismatch / composite-missing) **and** a rollback **disposition** for the launcher to execute (§D.5, option 2). The daemon **does not** repoint the symlink, does not stop itself, and does not count a rollback.

**[PLANNER r3 → AM-P2-24] The restored invariant — normative, stated here as a standalone rule (not a table cell):**

> **`rollback_requested` is writable ONLY when genuinely unhealthy OUTSIDE grace (`grace_active == False`); no path may write it during warmup.**

⇒ **Degraded-but-grace-held ⇒ DEFER** (no `serving_unhealthy` row, **no** `rollback_requested`, the txn stays open, the next tick re-reads). The `GraceTracker` **never changes `ready`** (`daemon/services/readiness.py:757-760`; the composite's field comment at `:404-406`) — **the gate decides to wait; the closer honours the same discipline.** The composite publishes `grace_active` (`readiness.py:407`, inside the `ReadinessComposite` dataclass at `:309`), so the conjunct is readable in-process with no new surface. **The genuinely-unhealthy direction is UNCHANGED:** `ready == False` **and** `grace_active == False` (or a version mismatch) still produces the verdict + request, and the existing chain still fires (the composition requirement's second direction). **Pinned in both directions** — `P-35` / `P3-PIN-27` (the new defer leg) and `P-6` / `P3-PIN-11a` (the never-green leg, which must use a **not-grace-held** fixture).

**Kill switch.** The sweep service is always-on with no kill switch (`daemon/api.py:2813-2815`); a new lane inherits that policy decision — **stated, not overridden**. The lane's *own* behaviour is still gated by an env kill-switch read **per tick** (not cached), mirroring `ENSEMBLE_POST_RESTART_ARM_NOTIFY` (`upgrade_journal_sweep.py:416-424`), so an operator can disable the closer without a rebuild. Recommendation: `ENSEMBLE_UPGRADE_SERVING_GREEN_CLOSER` (default `1`).

**[PLANNER r3 → REV-R-3] The TWO kill-switches (normative — one sentence, both sides):** this lane has exactly **two** kill-switches, both **default-`1`** (enabled): the **launcher-side `SWEEP_SERVING_GREEN_ENABLED`** (`=0` restores today's unconditional auto-rollback disposition; `=1`, the default, installs the new ladder) and the **daemon-side `ENSEMBLE_UPGRADE_SERVING_GREEN_CLOSER`** (read **per tick**, not cached — mirroring `ENSEMBLE_POST_RESTART_ARM_NOTIFY`, `upgrade_journal_sweep.py:416-424`; `=0` disables the closer lane entirely ⇒ the daemon writes **no** `serving_green_commit`, **no** `serving_unhealthy` and **no** `rollback_requested`); **both** are named in the operator runbook (Phase 2 `T13`) and the launcher-side switch is a **second enumerated hard ceremony pre-step** (Phase 3 `AM-P3-2` W1) whose value the ceremony notes must state — `=0` is an escape hatch for an unverified platform, **never** a permanent state (the default is `1` on both sides).

### D.2 — Launcher-side closer: verify primitive + adjudication

**The primitive.** `_js_serving_probe <port> <target_version>` — a **purity-safe** (R-08) positive serving check:

```
exec 3<>"/dev/tcp/127.0.0.1/$port" 2>/dev/null || return 1     # not listening → NOT-green
printf 'GET /livez HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n' >&3
read -t "$budget" <&3 status_line                             # bounded, builtin
… body read loop (read -t) …
exec 3>&-
```

**Adjudications (binding on the implementer):**

| # | Decision | Ruling + evidence |
|---|----------|-------------------|
| A1 | **curl is NOT admissible in `launcher.sh`** | `launcher.sh` has **zero** `curl` occurrences (`grep -c curl launcher.sh` → 0); R-08 is a **hard constraint** documented in-file: *“launcher.sh is SELF-CONTAINED … has ZERO python3 dependency … DO NOT add a python3 dep here — launcher purity is a hard constraint (R-08)”* (`launcher.sh:1040-1053`). Adding curl would introduce a **new external dependency surface** with **no in-repo precedent**. `lib.sh` **may** use curl (see §D.4). |
| A2 | **`bash /dev/tcp` IS admissible** | It is a **bash builtin feature** (no external binary) ⇒ R-08-clean. **In-repo precedent exists**: `tests/helpers/soak_gate_pack_common.sh:215` (`exec 3<>"/dev/tcp/127.0.0.1/$port"` as a free-port probe) and `test/packs/hard_reboot_drill_e2e_test.sh:354`. First use in `launcher.sh` — that is the novelty, and it is novelty-with-precedent, not novelty-without. |
| A3 | **Bound the read — no unbounded socket read** | The launcher's sweep-hang defense family: `_js_run_bounded` (`launcher.sh:568-606`, fix #4 v0.16.9) exists because *“any blocking subprocess … could pin the boot sweep FOREVER”*. An unbounded `/dev/tcp` read on the boot path re-introduces exactly that class. The probe therefore uses **bash-builtin `read -t <budget>`** (a timeout, no external `timeout` binary) and a small total budget (default **5s**, one knob). |
| A4 | **bash 3.2-safe shapes only** | `launcher.sh` declares bash-3.2 safety as a house constraint (`launcher.sh:266`: *“minimal JSON string escaper (Bash 3.2-safe)”*) and carries Darwin/BSD `uname` dispatch arms (`launcher.sh:247-263`, `:759-770`). ⇒ **avoid `read -u fd`** (the fd-number form); use the **`<&3` redirection form**, and verify `read -t` timeout behaviour under bash 3.2 before relying on it (the macOS leg must be pinned or explicitly waived). |
| A5 | **Version matching strips the leading `v`** | The `/livez` body carries `__version__` (`daemon/__init__.py:3` = `0.28.5`); the release target is `v0.28.5`. Match `0.28.5` against the `v`-stripped target. Same gotcha as §D.1. |
| A6 | **Verdict is tri-state, and the third state is the important one** | `GREEN` = HTTP 200 + version match. `NOT-GREEN` = port down, non-200, version mismatch, timeout, malformed body. **“NOT positively green” ⇒ DEFER, never rollback** — the launcher's port-down is ambiguous by construction (this boot is starting the daemon). The launcher must **never** infer “unhealthy” from “port down”. |

**Placement in the flipped branch — [PLANNER r2] SUPERSEDED by AM-P2-6 (position-0 request) and AM-P2-10 (the bounded absent-ceiling).** The three-rung ladder drafted below was written before the request handoff and before the absent-ceiling existed; it is **replaced** by the canonical four-rung ladder below. **[PLANNER r2]** *(the original three rungs are retained here for the audit trail and are marked superseded; the canonical ladder is stated ONCE, here, and every other location in this plan points here rather than restating it)*

> ~~1. **serving-green** (probe GREEN **and** `intent_flip` evidence …) → **commit-close**.~~ — *superseded*
> ~~2. else **existing `boot_sweep_commit_and_continue`** …~~ — *superseded*
> ~~3. else → **defer** … **unless** the defer ceiling is exhausted → **existing auto-rollback chain**.~~ — *superseded*

**THE CANONICAL LADDER (normative; restated exactly once — this is that place).** For a stale flipped txn, in order:

0. **`rollback_requested` present for this `(target, run_id)`?** (per-entry reader — AM-P2-14) → **re-probe** and follow the request decision table (**AM-P2-15**): GREEN **∧** `intent_flip` ⇒ **commit-close** (the request is journaled **superseded**, not executed) · GREEN **∧ ¬**`intent_flip` ⇒ **defer + journal** (the R3 hinge — never commit-close an unauthorized flip) · BROKEN ⇒ **the existing chain** byte-identically (`launcher.sh:1197-1230`, detail `disposition=rollback_requested`) · AMBIGUOUS ⇒ **the existing chain** (the daemon's post-grace verdict is the affirmative evidence). If no request → continue to rung 1.
1. **Serving probe** — canonical GREEN per **AM-P2-11**: `/livez` **200** ∧ `version` == the **v-stripped** target ∧ `/readyz` **200** ∧ ready; wrong-version **fails fast**. **GREEN alone is NOT sufficient to commit-close.** **[PLANNER r4] The canonical predicate of AM-P2-4 is folded here VERBATIM — this rung previously carried only its serving clause and was therefore the one rung of the ladder that could commit-close a flip on a positive health read alone:**
   > A serving-green commit-close fires only when BOTH hold: (i) a positive serving verdict for the txn's target — `/livez` 2xx with `version` == v-stripped target (launcher `_js_serving_probe`; `lib.sh` adopt `_probe_once_classified`; daemon `__version__` == v-stripped target ∧ cached readiness composite `ready`) — AND (ii) the journal history contains an `intent_flip` event naming that same target. Absent (ii) ⇒ launcher DEFER / daemon no-action-this-tick / adopt existing-chain — never commit, never immediate rollback.

   **[PLANNER r4 → AM-P2-2] PLUS the owner-liveness conjunct — the third conjunct on this rung:** the commit-close also requires **owner-dead OR heartbeat-stale** (`_js_pid_alive_bounded`, `launcher.sh:627-639`; the daemon twin is P7). ⇒ **rung 1 resolves as:**
   - **GREEN ∧ `intent_flip` ∧ (owner-dead ∨ heartbeat-stale)** ⇒ **commit-close** (`serving_green_commit`; no quarantine / count / cooldown — the house doctrine at `launcher.sh:1034-1036`).
   - **GREEN ∧ ¬`intent_flip`** ⇒ **DEFER + journal** — never a commit-close, never an immediate rollback (the **R3 hinge**; identical to AM-P2-15's request-path *GREEN-without-`intent_flip`* row).
   - **GREEN ∧ `intent_flip` ∧ owner-alive** ⇒ **DEFER** (the txn belongs to a live owner — AM-P2-2; it is not the sweep's to dispose).
   - **NOT GREEN** ⇒ rung 2.

   *(Uniformity note, [PLANNER r4]:* rungs 0 and 2 carry the `intent_flip` conjunct **explicitly** (AM-P2-15's request table; `launcher.sh:1068-1079`'s `boot_sweep_commit_and_continue` gate) and inherit the owner-liveness conjunct from **AM-P2-18**'s whole-stale-path rule. This fold makes rung 1 **explicit** on both conjuncts rather than implicit — the ladder is now uniform on all three rungs.)
2. **Operator escape** — the existing `boot_sweep_commit_and_continue` gate (prev manifest `rollback_safe` EXPLICITLY `false` + `intent_flip`) ⇒ commit-close as today (unchanged).
3. **Classify the non-green observation** (**AM-P2-10.1**) ⇒ **BROKEN** (`/livez` answered non-200 / unparseable) ⇒ **the existing auto-rollback chain fires now** (path 1 — the evidence is unambiguous) · **AMBIGUOUS** (no answer / wrong version / `/readyz` not ready) ⇒ **defer** (journaled `sweep_deferred`) **unless** the **bounded absent-ceiling** predicate holds (**AM-P2-10.2**): ≥`SWEEP_DEFER_CEILING` qualifying observations ∧ owner-dead ∧ `intent_flip` ∧ never-green-observed ⇒ **the existing auto-rollback chain** (`launcher.sh:1209-1242`).

**The three rollback-eligibility paths are a closed set — there is no fourth** (AM-P2-10.5): **BROKEN ⇒ immediate** · **`rollback_requested` ⇒ immediate** · **AMBIGUOUS ⇒ bounded**. Everything else **defers**.

*Note the interaction:* case 1 now pre-empts case 2 when the daemon is up and green. Both commit; the difference is only the event name and that case 1 does not depend on `prev.rollback_safe`. This is intended (the ruling is about *serving health*, which is strictly stronger evidence than an operator declaration) and must be disclosed in the phase-1/architect review.

**[PLANNER r2]** The interaction still holds, but the ladder above it has been **superseded** — the canonical ladder is the four-rung form restated immediately above (**AM-P2-11** for the GREEN predicate, **AM-P2-10** for the absent-ceiling). The operator escape (rung 2) now sits **after** the serving probe rather than after the serving-green commit, so the ordering reads: request → probe → operator escape → classify. **[PLANNER r2]** The request path (rung 0) is now **also** gated on `intent_flip` (**AM-P2-15**) — previously it could commit-close an unauthorized flip on a green probe.

**Commit-close mutations (launcher)** — **[PLANNER r4 → AM-P2-1] the HISTORY-BEFORE-CLOSE order — NOT "mirror `launcher.sh:1108-1127` exactly".** *In-tree re-verification at this fold (base `941cb6321`):* `launcher.sh:1108-1127` is the **OLD close-before-history** order (`:1110` current → `:1118` previous → `:1120` `in_flight`→null → `:1122` history append) ⇒ mirroring it would defeat **AM-P2-10.3's journal check** and would red **P-19 / P3-PIN-23**. The prior "mirror `launcher.sh:1108-1127` exactly" prescription **at this site** is **SUPERSEDED** (append-only annotation; the superseded text is retained above this paragraph for the audit trail).

1. `current` → `target`
2. `previous` → `in_flight.displaced_version` (**after** 1 and **before** 4 — the ordering pin at `launcher.sh:1112-1117`; a `null`/absent value ⇒ halt-for-human, mirror `launcher.sh:1094-1106`)
3. history append `serving_green_commit` (detail: target, prev, displaced_version, owner pid, age, probe verdict) — and, **FIRST** if the target ∈ `quarantined[]`, append `quarantine_cleared_serving_green` (AM-P2-1 step 3; the event is the gate's acceptance signal ⇒ event-before-close makes the clearance crash-proof)
4. `in_flight` → `null`  ← the txn closes **ONLY here**
5. `quarantined[]` array rewrite — **opportunistic on the launcher side**: only when the `quarantined` literal is already present in the document (`_js_journal_update` cannot create a field — `launcher.sh:387-393`); absence is a **no-op, not an error**. The **daemon** side owns the array rewrite (AM-P2-1's scoped step 5 / T5's omission).

**NOT** done: `_js_quarantine` (`launcher.sh:481-494`), `_js_count_rollback` (`launcher.sh:473-478`), cooldown arming, `sweep_rollback` history row.

### D.3 — `quarantine_cleared_serving_green`: the BOTH-SIDES amendment

**The frozen rule is SHELL-side.** `promote_entry_check` refuses a quarantined target (`scripts/upgrade/lib.sh:6559-6562`):

```
if journal_is_quarantined "$target_ver"; then
    _refuse quarantine "promote refused: version '$target_ver' is QUARANTINED (prior gate failure) — quarantine is cleared only by re-staging the version"
fi
```

`journal_is_quarantined` (`lib.sh:1963-1969`) reads `quarantined[]` from the journal; the **only** existing clear is `journal_quarantine_clear` (`lib.sh:1945-1960`), called from **`stage.sh:674` only** (re-staging). ⇒ A daemon-side clearance alone is **not sufficient**: the gate that actually refuses the next promote is the shell-side reader, and the daemon-side write is a separate process with its own failure modes.

**Amendment — BOTH sides, explicitly (load-bearing):**

| Side | Change | Anchor |
|------|--------|--------|
| **Daemon** | On a GREEN commit-close, if the target is in `quarantined[]`: rewrite the array without it (`uj.journal_update_field`, `upgrade_journal.py:375-389`) **and** append the `quarantine_cleared_serving_green` history event. | `upgrade_journal_sweep.py` (new lane), `upgrade_journal.py:392-407` |
| **Shell** | `promote_entry_check` refuses **only if** the version is in `quarantined[]` **AND** there is no `quarantine_cleared_serving_green` history event naming that version. New reader `journal_quarantine_cleared_serving_green <ver> [<run_id>]` — **the ONE per-entry, delimiter-anchored reader specified in the contract box immediately below** (this cell's earlier wording — *"mirroring `_js_history_has_intent_flip`'s conjunction technique … `lib.sh` has its own history reader"* — is **SUPERSEDED**; append-only annotation, the superseded text is retained in the audit trail). | `lib.sh:6559-6562`, new reader beside `lib.sh:1963-1969` |

**Why both (not either):** (i) the *state* (the `quarantined[]` array) and the *durable record* (the history event) are different surfaces — the daemon-side array rewrite alone leaves no audit trail if the history append fails, and the history event alone leaves the array carrying a version the gate would still refuse; (ii) the gate that refuses is shell-side, so the shell reader must learn the new event or the behavior does not change; (iii) the two together make the gate accept **either** signal — belt-and-braces against a half-written clearance.

**The frozen rule is NOT weakened.** It becomes: *“cleared by re-staging **or** by an explicit serving-green clearance event”*. The clearance event is only ever written by the closer **after** a positive serving-green verdict, which is itself gated on `intent_flip` evidence + staleness + owner-deadness + the health probes. No bypass, no silent edit, no re-staging required.

#### [PLANNER r4] The ONE writer/reader contract for `quarantine_cleared_serving_green` (blocker 3)

**Why this box exists.** Two incompatible reader prescriptions for the same event were live in this plan set — §D.3's *"mirroring `_js_history_has_intent_flip`"* (prefix-anchored, **whole-array**) and AM-P2-8's *"quote-anchored (`*"\"$ver\""*`)"*. Neither can work as written, and each fails in a different direction. ⇒ **one** writer format + **one** reader, pinned below; both earlier prescriptions are **SUPERSEDED** (append-only annotation; they are retained in place above, marked).

**(i) The WRITER detail format — pinned, house convention.**

| Field | Pinned value |
|---|---|
| event kind | `quarantine_cleared_serving_green` (fixed; owner-ratified verbatim — OQ-3) |
| detail shape | `target $VERSION run_id $RUN_ID serving_green_commit prev $PREV` — the version is **UNQUOTED**, the run id **UNQUOTED** |
| writers | the **daemon** closer (`journal_history_append`, `daemon/tools/upgrade_journal.py:392-407`) and the **launcher** (`_js_history_append`, `launcher.sh:443-453`) — **byte-identical detail strings** |
| why unquoted | the house convention is unquoted and space-delimited — verified in-tree: `promote.sh:481-482` emits `journal_history_append intent_flip "preflight: intentional promote target $VERSION — integrity verified …"`; the launcher's `_js_history_append` (`:443-453`) and the daemon's `journal_history_append` (`:392-407`) both store the detail as a JSON **string** (`{"ts":…,"event":…,"detail":…}`) |

This is **the same format AM-P2-6 already pins for `rollback_requested`** (`target <VER> run_id <RUN_ID> reason <…>`) ⇒ one detail grammar for the whole disposition chain.

**(ii) The READER — ONE per-entry, delimiter-anchored shell reader.** `journal_quarantine_cleared_serving_green <ver> [<run_id>]`, in `lib.sh` beside `journal_is_quarantined` (`lib.sh:1963-1969`). It **splits the history array into entries** (the entry-object boundary `{"ts":`) and requires, **inside ONE entry**:

1. the event-kind literal `"event":"quarantine_cleared_serving_green"`;
2. the substring `target $VERSION` **followed by a delimiter** — a space, or the end of the detail string;
3. when a `run_id` is supplied, the substring `run_id $RUN_ID` **in the same entry**.

**Why this shape (both hazards closed):**

- **Prefix-collision hazard (R-15's class):** the pattern `target $VERSION` **without** a trailing delimiter matches `v0.28.5` inside `target v0.28.55` ⇒ a clearance for `v0.28.55` would un-quarantine `v0.28.5` (and vice-versa). The **delimiter anchor** closes it: `target v0.28.5 ` does not occur inside `target v0.28.55 `.
- **Cross-entry hazard (AM-P2-14's doctrine):** a whole-array conjunction matches *"event kind in entry A"* ∧ *"target string in entry B"*. **Per-entry matching** closes it. **R-15 records that the pre-existing `_js_history_has_intent_flip` reader has BOTH hazards** — any reader copying it inherits both.
- **Absent / absent-field / torn ⇒ "not cleared"** — never a spurious clearance (the degradation principle, restated).

**(iii) The two SUPERSEDED prescriptions (append-only — retained above, marked):**

| Superseded prescription | Where | Why it cannot work |
|---|---|---|
| §D.3's *"mirroring `_js_history_has_intent_flip`'s conjunction technique (`launcher.sh:518-536`)"* | the §D.3 Shell row | that reader is **prefix-anchored AND whole-array**: `pattern='*"event":"intent_flip"*target '"$target"'*'` (`launcher.sh:518-536`, verified in-tree) ⇒ it carries **both** hazards above — prefix collision (`v0.28.5` vs `v0.28.55`) and cross-entry false positive. **AM-P2-14 already forbade inheriting this shape** for the T14 request reader; this fold applies the same rule to the clearance reader. |
| AM-P2-8's *"The shell clearance reader must be **quote-anchored** (`*"\"$ver\""*`) so `v0.28.5` cannot match `v0.28.55` (mirrors `lib.sh:1967`)"* | AM-P2-8's last bullet | the quote-anchored shape matches the **`quarantined[]` array** (whose entries really are quoted — `lib.sh:1967`), **not** the history-detail string. In the history array the version is an **unquoted literal inside a JSON-escaped detail string** ⇒ a quote-anchored match **NEVER fires** ⇒ the clearance path would never lift the frozen refusal — **tonight's punishment class, reproduced**. The `lib.sh:1967` mirror is a category error: it is the shape of the *array* reader, not the *history* reader. |

**(iv) The stale claim, corrected.** §D.3's *"lib.sh has its own history reader"* is **imprecise**. Verified in-tree: `lib.sh` has history **writers** (`journal_history_append`, `lib.sh:1665`, ~19 call sites) and the array reader `journal_is_quarantined` (`:1963-1969`), but **ZERO** `intent_flip` readers (`grep -n intent_flip scripts/upgrade/lib.sh` ⇒ 0 hits) ⇒ the clearance reader is **new work**, not a copy of an existing one. (The same correction is already recorded in `decisions.md` §8.2 item 4; repeated here because §D.3 is the implementer-facing site.) The launcher side is unaffected: it has the one existing history reader (`_js_history_has_intent_flip`, `launcher.sh:518-536`) plus the new T14 request reader (AM-P2-14).

**(v) Pin consequence.** The reader's shape is pinned by **`P-36`** (the two negatives: prefix collision + cross entry), and `P-8` gains the positive half. **A clearance reader that never fires is a REGRESSION to tonight's punishment class** — the pin must assert both that the matching event un-quarantines the version **and** that the two near-miss fixtures do not.

### D.4 — `adopt_stale_txn` disposition (lib.sh:6627+)

**DECISION: same-phase treatment, as a separately-scoped task.** `adopt_stale_txn` is the **promote-entry mirror** of the very same sweep decision table (`lib.sh:6579-6626` documents the mirror discipline; the stale+flipped branch performs repoint → quarantine target → count+cooldown → `sweep_rollback`, ADR-024 cited at `lib.sh:6604`). Leaving it untreated leaves a hole in the ruling: the next promote against an unresolved healthy txn would **still** roll back a healthy serving release and quarantine it — the same wrongful shape, on a second decision-maker.

**Shape** (inserted into the stale+flipped branch, **before** the manifest gate / repoint chain):

1. serving check via `lib.sh`'s mature classified probe (`_probe_once_classified`, **`lib.sh:3874`** **[PLANNER r2 → AM-P2-22]** — the plan previously cited `:3841-3860`; `curl` is admissible here — `lib.sh:3804+` documents `curl -fsS --max-time 5`, and `lib.sh` is explicitly **not** purity-constrained). Probe `/livez` **and** `/readyz`; assert HTTP 200 on both, the `version` field equals the `v`-stripped target, **and** `/readyz` reports ready — the canonical GREEN of **AM-P2-11** (OQ-2). **[PLANNER r2]** The house classifier's taxonomy is `0` green / `1` red / `2` transient (`_probe_once_classified`'s classifier comment, `lib.sh:3870-3873`) — the adopt path reuses that taxonomy rather than deriving a second one. `lib.sh` already has a `_probe` with a wall-clock budget + boot-grace hook (`lib.sh:3800-3812`) — reuse, do not re-invent.
2. **GREEN** → commit-close (same mutation order as §D.1 — the history-before-close order) **∧ `intent_flip` evidence present** (AM-P2-4's T11 conjunct; a NEW `journal_history_has_intent_flip <ver>` reader in `lib.sh` beside `journal_is_quarantined` (`lib.sh:1963`) — `lib.sh` has **zero** `intent_flip` readers today, so this is **new work**, not a copy) + `serving_green_commit` event, **no quarantine / no count / no cooldown**, and the enclosing promote **continues** (the txn is resolved; the promote's own preflight/entry gates still apply afterwards). **[PLANNER r5 → sweep class (a)] the `intent_flip` conjunct was carried by AM-P2-4 and by the §D.4 (a-i) divergence note, but the **step list itself** said only "GREEN → commit-close" ⇒ an implementer building from this list would have committed an unauthorized flip on the adopt path too. The conjunct is now on the step. The GREEN-without-`intent_flip` arm remains the **existing chain** — the documented divergence, §D.4 (a-i).**
3. **NOT-GREEN** → the existing chain **unchanged** (halt gates → repoint → quarantine → count+cooldown → `sweep_rollback`).

**[PLANNER r4 → note (a) TWO DISCLOSURES on the adopt path (both sentences are binding on the implementer):**
- **(a-i) The GREEN-without-`intent_flip` fallthrough DIVERGES across the three decision-makers, by design.** AM-P2-4's canonical clause (ii) reads *"Absent (ii) ⇒ launcher DEFER / daemon no-action-this-tick / adopt existing-chain"* ⇒ on this path the GREEN-but-unauthorized flip takes the **existing chain**, while the launcher **defers** (§D.2 rung 1) and the daemon takes **no action this tick**. ⇒ **R-8's shared-pin claim cannot literally hold for that fixture**: the three-way shared pin (matrix rows 1/2/8) is therefore **scoped to the fixtures where all three agree** — GREEN-and-authorized, and NOT-GREEN — and the GREEN-but-unauthorized fixture is pinned **per decision-maker** (`P-26` / `P-15`'s negative axis for the launcher+daemon legs, and the adopt-path leg of the same pin). This divergence is **disclosed, not a defect to be unified** — the adopt path's existing chain is its pre-ruling behaviour and this lane does not rewrite it.
- **(a-ii) The NOT-GREEN arm has NO defer and NO grace — and that is correct here.** The adopt path engages only a txn that is **already past the 600 s staleness gate** (`SWEEP_STALE_S`, `launcher.sh:69`) ⇒ it is **strictly older** than the measured 210–360 s slow-boot class that the daemon-side boot-grace exists to cover ⇒ the boot-degraded window the grace protects against has **already elapsed** by construction. The adopt path is also a **promote-entry** decision, where the operator is present and the promote must resolve — a deferral there has no boot to wait for. ⇒ the existing chain runs **unchanged** on this arm, and no grace window is introduced on it.

**Polarity preserved:** never-green still rolls back on this path too (the composition requirement is not path-scoped).

**Note (not in scope here):** `lib.sh:6604`'s ADR-024 citation and the D-FA4.2 entry-side cap/cooldown semantics are **unchanged** by this phase — the adopt path keeps counting rollbacks it genuinely performs.

### D.5 — Rollback EXECUTION for daemon-side unhealthy verdicts (option (f))

**The problem.** The daemon can *decide* “the target is not serving” — but it must not be the one to repoint the symlink and stop itself mid-serve.

| Option | Mechanism | Pros | Cons |
|---|---|---|---|
| **1. `spawn_executor`-style transient unit** | `spawn_executor` (`daemon/tools/upgrade_journal.py:3044-3113`) — systemd transient unit / `start_new_session` branches, `RuntimeMaxSec` backstop, env via `executor_env`; caller today `daemon/manager.py:5809` | Immediate; survives daemon death by construction; matches the house executor pattern | **NEW policy surface**: the decision + argv construction + `run_id` + `ENSEMBLE_UPGRADE_LIVE` semantics + `rollback.sh` fencing are all new; the daemon would be orchestrating its own termination; the transient unit would then have to stop the daemon — a self-directed kill class the house has routed before (r-f82e) |
| **2. Journal disposition → next launcher boot executes it** | The daemon journals the verdict + a rollback **request**; the launcher’s existing sweep-rollback chain (`launcher.sh:1209-1242`) executes it at the next launcher boot | **Zero new execution surface** — the chain is already reviewed, idempotent, and tested; the launcher executes the rollback **pre-daemon** (sweep at `launcher.sh:1661`, daemon child starts at `:1525`) = the **clean point** to repoint; F2/nonce semantics untouched (no promote is involved) | Latency = next launcher boot; a launcher that never re-boots never executes it (see residual R-4) |
| **3. Operator runbook step** | Journal the verdict; a human runs `rollback.sh` | Zero new surface; full human control | **Insufficient alone** — the composition requirement says never-green **MUST** roll back; a manual-only path breaks it |

**RECOMMENDATION: Option 2 as the primary mechanism**, with Option 3 as the documented operator escalation, and Option 1 explicitly **out of scope for this phase**.

Rationale: (a) the launcher is already the executor of this exact disposition — reusing it is the smallest correct change; (b) the launcher executes it at the *safe* point (pre-daemon, no live traffic to interrupt); (c) it introduces **no new nonce/F2 semantics** (a rollback is not a promote); (d) it keeps the composition requirement provable with the *existing* pins plus a small set of new ones.

**The residual (must be closed or explicitly accepted):** Option 2’s trigger is “the launcher boots again”. A launcher that stays up while the daemon runs a degraded-but-alive target never re-boots ⇒ the disposition is never executed. Two candidate closures, for the architect to pressure-test:

- **2a.** The daemon-side closer, on a NOT-GREEN verdict, requests a **unit restart** so the launcher necessarily re-boots and the disposition executes. New daemon→systemd surface (the daemon asking its own supervisor to restart it) — needs its own kill-switch and a careful exit-code story (the unit is `Restart=on-failure` with `RestartPreventExitStatus=78` and `SuccessExitStatus=143` — `scripts/systemd/ensemble-daemon.service`).
- **2b.** Accept the residual: the never-green **primary** protection remains promote-time (a release that fails its gates never gets promoted) plus the launcher sweep (which fires at every launcher boot, and the systemd unit’s `Restart=on-failure`/`RestartSec=10` makes a launcher boot **routine** whenever the unit restarts). The daemon-side closer is then an *accelerator + evidence producer*, not the primary rollback engine. **[PLANNER r5 → sweep class (e) — THE PRECEDENCE CLAIM IS SUPERSEDED (append-only; the text above is retained for the audit trail).]** "not the primary rollback engine" was true **before** the daemon closer existed at all; under this plan the **daemon closer normally wins the race** — it ticks every 90 s while the daemon is up and reaches its verdict at the **first tick after the 600 s staleness gate** (≈11 min), whereas the launcher's bounded backstop needs **≈10–15 min** of qualifying observations and only fires for the shapes the daemon cannot reach (never-started daemon; tempfail-75 / clean-exit-0). The precedence is stated normatively at matrix (a)'s *Launcher/daemon precedence* paragraph (`phase2-plan.md` §The Defer-vs-Deadlock Matrix (a)) and at `decisions.md` §6.1 / §OQ-C-06(d-r2). ⇒ **the daemon-side closer is the PRIMARY disposer of a stale flipped txn while the daemon is up; the launcher sweep is the BACKSTOP.** Nothing about the never-green guarantee changes — the composition requirement (both directions pinned) is unaffected; only the *ordering* of the two decision-makers is corrected.

**Recommendation: implement 2b first; treat 2a as a separately-commissioned follow-up** (it is a new daemon→supervisor action class, not a disposition-chain change).

### D.6 — Defer ceiling: semantics, home, reset

**Home — journaled history, no new journal field.** The launcher’s `_js_journal_update` **cannot create a field**: it returns 1 when the field literal is absent from the document (`launcher.sh:387-393`):

```
case "$json" in
    *"\"$field\""*) ;;
    *) return 1 ;;
esac
```

⇒ a NEW top-level journal field is **not writable from the launcher** (the daemon’s `ensure_extensions` could add one, `upgrade_journal.py:410-450`, but the launcher sweeps **before** the daemon exists — `launcher.sh:1661` vs `:1525`). **Decision:** the defer counter is derived by **counting `sweep_deferred` history entries for this txn** — the same technique class as `_js_history_has_intent_flip` (`launcher.sh:518-536`), which counts a conjunction of literals in the history array.

**[PLANNER r5 → SAME-PASS NOTE (the §D.6 "Home" paragraph) — THE DECISION AS STATED HERE IS SUPERSEDED (append-only; the text above is retained for the audit trail).]** The **reasoning** above survives unchanged — the launcher genuinely cannot create a journal field (`_js_journal_update` returns 1 on an absent literal, `launcher.sh:387-393`), and the launcher genuinely sweeps **before** the daemon exists (`launcher.sh:1661` vs `:1525`). The **conclusion** does not: the counter's home was **re-homed** to the launcher-local file **`<INSTALL_DIR>/.sweep-defer-<run_id>`** (**AM-P2-5**), because the drafted history-count carried **three verified defects** — (1) **cross-entry false positives** (the conjunction `*"event":"sweep_deferred"*run_id <id>*` matches event-in-entry-A ∧ run_id-in-entry-B, and `grep -c` counts *lines* while the history array is ONE line); (2) **O(N²) cost** — measured on a copy of the live journal (27 KB / 157 entries): `_js_journal_read` **13.5 s**, `_js_json_sub history` **35.7 s** ⇒ **≈285×** at the 456 KB incident size ⇒ hours, with **no** launcher-side size guard (`JOURNAL_SIZE_GUARD_BYTES` is `lib.sh`-only — zero hits in `launcher.sh`); (3) the **inverted** over-count doctrine (see the next paragraph). ⇒ **the "Home" is the launcher-local file, NOT the journaled history**; the "no new journal field" conclusion still holds, but it is now a *consequence* of the file home, not its reason. The **firing rule** that consumed this count is separately superseded at the *Exact firing rule* paragraph below (AM-P2-10's bounded absent-ceiling) and in the **matrix row 4** (≈10–15 min, the r3 timing correction). **Nothing is deleted; the normative statements are AM-P2-5 (home) + AM-P2-10 (firing rule) + §D.2 (the canonical ladder, restated exactly once).**

**Conjunction.** Each deferral row’s detail carries `run_id=<run_id>` (the txn’s run id — `in_flight.run_id` is the identity the daemon already keys on, `upgrade_journal.py:2262-2272`). The count = occurrences of the conjunction `*"event":"sweep_deferred"*run_id <run_id>*` in the history array.

**Over-count is the SAFE direction.** A false-positive count (a cross-entry-boundary match — the same known looseness the intent_flip reader documents at `launcher.sh:530-534`) makes the backstop fire **earlier** (rollback sooner) — the conservative direction. Under-count would strand a broken release — the unsafe direction. This mirrors the existing counter’s documented doctrine (*“the counter increment … can only over-count — conservative, anti-flapping direction”*, `launcher.sh:1144-1147`).

**[PLANNER r5 → SAME-PASS NOTE (the §D.6 "Over-count" paragraph) — THE DOCTRINE IS INVERTED FOR THIS COUNTER (append-only; the text above is retained for the audit trail).]** For the **defer** counter the "over-count is safe" doctrine **does not hold**, because this count gates the **rollback itself**: a false over-count fires a **wrongful rollback**, and the live `rollback_window_count` was **2/3** at the incident — one false rollback would have **armed the 24 h promote halt**. ⇒ for *this* counter the safe direction is **under-count**, and it is the **observation conjuncts** (owner-dead ∧ `intent_flip` ∧ never-green-observed) that keep a wrongful rollback out — not the count. (**AM-P2-5**, defect (2); the doctrine quoted from `launcher.sh:1144-1147` still governs the **rollback** counter, which is a different counter and is not what §D.6 is about.) This paragraph is **also** the reason the counter was re-homed to the launcher-local file — the in-place over-count was the observable symptom of the cross-entry false positive.

**Reset.** Per-txn by construction (the run_id changes with each new promote txn) — a new txn starts at zero. A commit-close or a rollback closes `in_flight`, after which the counter is irrelevant.

**Default value.** **3** — mirroring the house “three strikes” flavor of `SWEEP_ROLLBACK_CAP_24H=3` (`launcher.sh:78`) and giving the daemon-side closer (which acts at the first tick after the 600s staleness gate ≈ 11 min, `upgrade_journal_sweep.py:96-99`) ample room to win the race before the launcher backstop fires.

**Form.** `: "${SWEEP_DEFER_CEILING:=3}"` — the **`:=`-form** (env-overridable), NOT the plain-assignment form used by the mirrored protocol constants (`launcher.sh:69-94`). Two reasons: (a) the mirrored constants are plain assignments, so the **test harness cannot override them** — `run_sweep` sets `SWEEP_STALE_S=0` (`tests/test_boot_sweep_commit_and_continue.sh:123-134`) but the sourced launcher **overwrites** it with 600 (the fixture’s stale `started_at` is what actually makes the txn stale — the env override is decorative); a new constant that pins must be overridable; (b) precedent for the `:=` form in `launcher.sh` exists: `: "${MALLOC_ARENA_MAX:=2}"` (`launcher.sh:1658`).

**Exact firing rule.** On a stale flipped txn that is NOT positively green: let `prior = count(sweep_deferred for this run_id)`. If `prior >= SWEEP_DEFER_CEILING - 1` ⇒ **execute the existing auto-rollback disposition** (this is the CEILING-th encounter). Otherwise ⇒ append the `sweep_deferred` row and defer. ⇒ at most `CEILING - 1` deferrals occur before the rollback fires.

**[PLANNER r2] — this firing rule is superseded by AM-P2-10.** The count is no longer taken over the journal history (AM-P2-5 re-homed it to the launcher-local `.sweep-defer-<run_id>` file) and no longer gates *all* encounters. It is now the **absent-class tally** behind the **bounded absent-ceiling**: the existing auto-rollback fires on the observation that takes the qualifying-ambiguous tally to `SWEEP_DEFER_CEILING`, **and only then** ∧ owner-dead ∧ `intent_flip` ∧ never-green-observed. The BROKEN class fires **immediately** (the count is waived for it — its evidence is unambiguous). The canonical ladder is restated ONCE in **§D.2**; this section points there. **[PLANNER r2]** `SWEEP_DEFER_CEILING` keeps its name, its `:=` form and its default (3); its **meaning** is now precisely *"the number of qualifying AMBIGUOUS observations after which the absent-class rollback becomes eligible"* (AM-P2-10.2).

**[PLANNER r2 → AM-P2-10.5] superseded — the firing rule is the three-path set.** **[PLANNER r3]** The pointer is reaffirmed verbatim: the firing rule is **the three-path set** of **AM-P2-10.5** — **BROKEN ⇒ immediate** · **`rollback_requested` ⇒ immediate** · **AMBIGUOUS ⇒ bounded** — and **everything else defers**. There is **no fourth path**. This paragraph is an **audit-trail restatement only**; it is **not** a normative rule and must not be implemented. (The r3 fold does **not** change this path set — AM-P2-24 constrains the **writer** of `rollback_requested`, not the path set itself.)

### D.7 — Journal event vocabulary and alert-map membership

| Event | Writer(s) | Terminal-event membership (`upgrade_journal.py:1874-1884`) | Alert map (`upgrade_journal.py:646-651`) | Rationale |
|---|---|---|---|---|
| `sweep_deferred` | launcher | **NO** — informational; a deferral closes nothing | **NO** alert | A deferral is a *hold*, not an outcome. Adding it to `_TERMINAL_EVENTS` would let a deferral close a pending_op — wrong. |
| `serving_green_commit` | launcher + daemon + lib.sh | **YES** — join `_TERMINAL_EVENTS` | **NO** alert | Precedent: `boot_sweep_commit_and_continue` **is** in `_TERMINAL_EVENTS` (`upgrade_journal.py:1883`) because a commit-close ends the txn. Alert policy: *“commit, restart, sweep, nonce_consumed … emit NOTHING”* (`upgrade_journal.py:590-596`) — a good outcome is silent. |
| `quarantine_cleared_serving_green` | daemon (+ mirrored in lib.sh if the shell clears) | **NO** by itself (it annotates an already-terminal outcome) | **RECOMMEND: a NEW alert kind** (e.g. `upgrade_serving_green_restored`) | The quarantine it clears **did** alert (`quarantine` → `upgrade_auto_rollback`, `upgrade_journal.py:649`). An operator watching the alert stream must learn that the verdict was **reversed** — otherwise they see “auto-rollback + quarantine” and never learn it was stood down. |
| `serving_unhealthy` | daemon | **NO** (it is a verdict + request, not an execution) | **RECOMMEND: reuse `upgrade_auto_rollback`** | It announces a rollback that the launcher will execute; operators watching for auto-rollbacks should see it. Alternative: no alert + log/journal only. **Flagged as an open question.** |

---

## The Defer-vs-Deadlock Matrix — every case resolved

### (a) Recovery-restart window — launcher boots pre-daemon (port down because THIS boot starts the daemon)

**Resolution: DEFER (journaled), never roll back.** The launcher's sweep runs at `launcher.sh:1661-1662`, *before* `resolve_binary` (`:1665`) and long before the daemon child starts (`:1525` inside `run_loop`). At that instant “port down” is **structurally ambiguous** — it cannot distinguish “the target is broken and never served” from “this boot is starting the target”. Rolling back in that window is precisely tonight’s wrongful rollback (`launcher.sh:1209-1242` fired at 22:25:53 on a target that was serving all-green at 21:49:50Z).

- Launcher: not-positively-green ⇒ `sweep_deferred` row; txn stays **OPEN**; no quarantine; no counter; no cooldown; boot proceeds.
- The daemon-side closer decides post-boot (the user’s self-healing requirement): the daemon is up ⇒ it reads its own health ⇒ **GREEN ∧ `intent_flip` (P9) ⇒ commit-close**; **GREEN ∧ ¬`intent_flip` ⇒ no-action-this-tick** (the R3 hinge — the daemon never commits an unauthorized flip; §D.1's predicate table + P9).
- **Timing:** the daemon ticks every 90s (`upgrade_journal_sweep.py:96-99`), so the daemon-side verdict lands at the first tick after the 600s staleness gate (≈11 min from txn open) — **far** before the ~59min ADR-024 window in which tonight’s wrongful rollback fired. The daemon usually wins the race in case (a). **[PLANNER r4 → the r3 timing correction]** the *"~59 min"* figure is **no longer the operative bound** — it was the age of tonight's *unconditional* auto-rollback (a launcher boot at 22:25:53 on a txn opened ≈21:26), not a designed fuse, and the ADR-024 constant itself is **not** a 3540 s fuse anywhere in the tree (the launcher's gate is `SWEEP_STALE_S=600` at `launcher.sh:69`/`:907`; the age is computed at launcher entry). **The operative bound under this plan is the ≈10–15 min bounded absent-ceiling** (3 qualifying launcher observations × ≈3.5 min per boot in the `RestartSec=10` + ~200 s dying-boot environment — the r3 correction, already stated at matrix row 4, `decisions.md` §OQ-C-06(d) and `architecture-recommendation.md` §6(iii)). **Launcher/daemon precedence, stated explicitly:** the launcher sweeps **pre-daemon** (`launcher.sh:1661-1662` vs the daemon child at `:1525`) ⇒ within one launcher boot the launcher observes first; but the daemon closer ticks every 90 s while the daemon is up and reaches its verdict at the **first tick after the 600 s staleness gate** (≈11 min) ⇒ **the daemon closer normally wins the race**, and the launcher's bounded ceiling is the backstop for the shapes the daemon cannot reach (never-started daemon; tempfail-75 / clean-exit-0).
- **The launcher path still matters** for case (b) and as the bounded backstop for case (c).

### (b) Daemon already up + serving-green at launcher boot

**Resolution: launcher commits immediately.** The probe is **positive** ⇒ the launcher has no ambiguity ⇒ it does not need to defer. Commit-close immediately (mutations §D.2), **no rollback count**, **no quarantine marking**, `serving_green_commit` journaled. This is the “daemon survived the previous launcher life” shape — e.g. the operator restarted the *launcher* only, or systemd restarted the unit while the daemon child was still up.

### (c) Never-green crash-loop — the daemon never serves

**Resolution: BOUNDED defer + a daemon-side positive-UNHEALTHY verdict accelerates it.** A pure "defer forever" converts *wrongful-rollback* into *never-rollback* — the opposite failure, and it strands a broken release on the live symlink. Two bounded mechanisms:

1. **Defer ceiling** (§D.6): after `SWEEP_DEFER_CEILING` (default 3) deferrals **for the same txn/run_id**, the launcher executes the **existing** auto-rollback disposition — the never-green protection, unchanged and armed.
2. **Daemon-side acceleration:** if the daemon IS up but the target is not serving (composite not ready after the boot-grace window, or version mismatch), the daemon journals a `serving_unhealthy` verdict + a rollback disposition; the launcher executes it at the next boot (option 2, §D.5). **[PLANNER r5 → BLOCKER 1 / sweep class (a)] this arm is gated by **P9** exactly like the GREEN arm (§D.1's predicate table is all-or-nothing): **absent `intent_flip` ⇒ no-action-this-tick ⇒ no verdict and no request.** The daemon never acts on an unauthorized flip in either direction. **[PLANNER r3 → AM-P2-24]** — mechanism 2 gains the **grace conjunct**: the verdict is reached only when the composite's **`grace_active == False`**, and the boot-grace horizon is the closer's **own** `UPGRADE_CLOSER_BOOT_GRACE_S` (default **300**, derived from the hand-back budget family — **≥ `HANDBACK_VERIFY_BUDGET_S`**), **not** the borrowed 120 s pool-busy constant. ⇒ **degraded-but-grace-held ⇒ DEFER, never `rollback_requested`** — the restored invariant. The **never-green** direction is **unchanged** (a genuinely unhealthy serving outside grace still produces the verdict + request).

**[PLANNER r2] — RECONCILED to the ONE normative absent-only disposition (AM-P2-10; leader ruling OQ-1(a)).** Mechanism 1 above is **the never-green protection and it is armed** — but only after the r2 fold, which is what the council's REJECT was about. In the canonical never-green shape the daemon never serves ⇒ **every** launcher observation is AMBIGUOUS (no answer / the previous version / `/readyz` not ready) ⇒ under AM-P2-5's pre-r2 eligibility predicate *nothing rolled back*. **Now:** the qualifying AMBIGUOUS tally is kept in the launcher-local `.sweep-defer-<run_id>` file; when it reaches `SWEEP_DEFER_CEILING` **and** owner-dead **and** `intent_flip` **and** never-green-observed hold, the launcher executes the **existing** auto-rollback disposition ⇒ **the never-green path is armed again** (at most `CEILING − 1` deferrals precede it). Mechanism 2 is unchanged and remains the **accelerator** (the daemon's post-grace verdict is affirmative evidence ⇒ path 2 ⇒ immediate execution at the next boot). **[PLANNER r2]** The two shapes that produce **no further launcher boot at all** (tempfail-75, clean-exit-0) remain the leader-adjudicated **operator-bounded residual** (AM-P2-7 + §4.5) — the absent-ceiling cannot reach them because there is no boot to observe; the T15 hook covers them only where it may (AM-P2-10.4).

**Residual (R-4):** if the daemon **never starts at all** (hard crash at import), the launcher’s `run_loop` restarts the child in-place (`launcher.sh:1507-1560`) — so the launcher does **not** re-boot and the defer counter does **not** advance within one launcher lifetime. The counter advances only on launcher boots (the systemd unit restarts the launcher with `Restart=on-failure`, `RestartSec=10` — `scripts/systemd/ensemble-daemon.service`). Mitigation + residual treatment: **R-4** below.

### (d) Tonight’s 4-stage shape — THE pin

**Resolution: the exact 4-stage sequence is the acceptance pin, replayed against the journal fixtures.**

| Stage | Tonight (2026-11-01) | HEAD (after this phase) |
|---|---|---|
| 1 | halt at hand-back → txn left OPEN, `flipped=true` | **unchanged** (Phase 1 fixes the verifier budget; the halt shape stays) |
| 2 | target **serving all-green** (21:49:50Z receipt) | **unchanged** (the daemon serves the flipped target) |
| 3 | **wrongful rollback**: repoint → `v0.28.5` **quarantined** → rollback counted → cooldown armed | **commit-close**: `current`=target, `previous`=displaced, `in_flight`=null, `serving_green_commit` — **no quarantine, no count, no cooldown** |
| 4 | **quarantine refusal**: next promote refused *“cleared only by re-staging”* | the shell gate **accepts** the `quarantine_cleared_serving_green` event ⇒ the next promote proceeds (or, if no wrongful quarantine was applied, stage 3/4 are no-ops) |

### (e) Wrongful quarantine already applied

**Resolution: the explicit journaled path — `quarantine_cleared_serving_green` — amended on BOTH sides (§D.3).** The daemon-side array removal alone is insufficient (the refusing gate is shell-side); the shell-side reader alone is insufficient (the state stays inconsistent). Both. The frozen “cleared only by re-staging” rule is **not bypassed** — it is **extended** with one explicit, journaled, evidence-backed alternative.

### (f) Rollback EXECUTION for daemon-side unhealthy verdicts

**Resolution: Option 2 (journal disposition → next launcher boot executes the existing chain) as the primary, with Option 3 (operator runbook) as the documented escalation and Option 1 (transient-unit executor) explicitly out of scope for this phase.** Full trade-off table + rationale + the residual closure options (2a / 2b) in §D.5.

### State / sequence table — the full decision matrix

| # | Situation | Launcher side | Daemon side | Outcome | Ruling satisfied? |
|---|-----------|--------------|-------------|---------|-------------------|
| 1 | stale flipped txn, daemon up, **target serving green** | probe GREEN ∧ `intent_flip` ⇒ commit-close **[DEV-FOLD-2026-11-02 — approver iter003 note 4: conjunct inline; normative section §D.2 rung 1 governs]** | (txn already closed) | **STAY** on target; no quarantine; no count | ✅ serving-green = stay |
| 2 | stale flipped txn, daemon up, **target NOT ready** (after grace) | probe NOT-green ⇒ defer (ceiling permitting) | NOT-GREEN ⇒ `serving_unhealthy` + rollback disposition | launcher executes the **existing** rollback at next boot | ✅ never-green still rolls back |
| 3 | stale flipped txn, **daemon down / port down** at launcher boot (recovery-restart window) | **DEFER** (journaled) | daemon boots ⇒ closer decides (case 1 or 2) | self-healing: stay if green, roll back if not | ✅ no wrongful rollback |
| 4 | stale flipped txn, **daemon never starts** (hard crash) | defer; **the bounded absent-ceiling** ⇒ rollback | (never runs) | **bounded: rollback after `SWEEP_DEFER_CEILING` qualifying AMBIGUOUS observations ∧ owner-dead ∧ `intent_flip` ∧ never-green-observed (AM-P2-10)** — the tally persists across launcher boots in `.sweep-defer-<run_id>` | ✅ never-rollback is prevented — **[PLANNER r2]** this row previously said only "ceiling ⇒ rollback", which AM-P2-5's pre-r2 predicate made **vacuous for the absent shape** (the council's REJECT basis); the row now names the mechanism that arms it — **[PLANNER r3]** and it now carries the **corrected timing**: the ceiling fires after **3 qualifying launcher observations**; in the `RestartSec=10` + ~200 s dying-boot environment each launcher boot is ≈3.5 min ⇒ the bounded rollback lands in **≈10–15 min** (the earlier "≈30–40 min" figure assumed a ~10 min-per-boot cadence and was **wrong** — see `decisions.md` §OQ-C-06(d) and `architecture-recommendation.md` §6(iii)) |
| 5 | stale flipped txn, **prev manifest `rollback_safe:false`** + `intent_flip` | existing `boot_sweep_commit_and_continue` (unchanged) | — | commit at target (operator-declared incompatibility) | ✅ unchanged path |
| 6 | stale flipped txn, **no `intent_flip` evidence**, serving green | probe GREEN but **no `intent_flip`** ⇒ **not** a commit ⇒ **DEFER + journal** (rung 1, AM-P2-4 clause (ii); the R3 hinge) — **and the deferral is TERMINAL-BY-DESIGN, not a rollback**: the sticky `green=1` flag (AM-P2-10.3) makes the absent-ceiling **permanently ineligible** for this txn ⇒ the *"defer → ceiling ⇒ rollback"* tail this row previously stated is **UNREACHABLE** for a persistently-GREEN txn | — | **DEFER + journal + operator surfacing** — never a commit (the flip is unauthenticated) and never a rollback (the target is serving green ⇒ the owner ruling forbids it). Consistent with **AM-P2-15**'s *GREEN ∧ ¬`intent_flip`* row and **P-15**'s negative axis. The txn stays open until an operator resolves it — the **operator-bounded residual** family of AM-P2-7 (§4.5): the documented escapes are the runbook's terminal backstop (manual `rollback.sh`) and an operator-authorized re-promote. **Surfacing (it must not defer silently):** (i) the deferral row carries an **explicit, greppable reason marker** (which conjunct was missing); (ii) the launcher emits its house one-shot disposition notifier — `_notify_once <kind> <msg>` (`launcher.sh:1265-1268`; a `NOTIFY[<kind>]` log line, the same surface the sweep's `sweep-commit` / `sweep-halt` / `sweep-rollback` dispositions use today) with a **new kind** for this shape; (iii) the runbook and the ceremony notes name the shape. **The SSE alert map is deliberately NOT extended for `sweep_deferred`** (AM-P2-19's table: a hold is not an outcome) — a reader must not "fix" the silence by inventing an alert kind unilaterally | ⚠ deliberate — see OQ-6 · **[PLANNER r4]** the *"defer → ceiling ⇒ rollback"* tail this row carried is **SUPERSEDED** (append-only; the row text is retained in the audit trail) |
| 7 | stale flipped txn, **target quarantined** from tonight’s wrongful rollback, now serving green | — | GREEN ⇒ clear + `quarantine_cleared_serving_green` | target **un-quarantined**; next promote accepted | ✅ no punishment of a healthy release |
| 8 | stale flipped txn at **promote-entry** (`adopt_stale_txn`) | — | — | serving green ⇒ commit-close + promote continues; not green ⇒ existing chain | ✅ same ruling on the second decision-maker |
| 9 | stale **pre-flip** txn | existing clear path (`launcher.sh:1244-1252`) — unchanged | — | cleared, boot proceeds | ✅ untouched |
| 10 | **fresh** txn (age ≤ 600s) | untouched (`launcher.sh:915-920`) | — | left alone | ✅ untouched |

---

## Tasks (file-level, with anchors)

| # | Task | File / anchor | Depends on | Acceptance |
|---|------|----------------|-----------|------------|
| T1 | Add `_js_serving_probe` — purity-safe `/dev/tcp` + `GET /livez` + bounded `read -t` + `v`-stripped version match; tri-state verdict | `launcher.sh` (new fn; place near `_js_run_bounded` `:568-606`) | none | Probe returns GREEN for a fake green daemon; NOT-GREEN for down / 500 / version-mismatch; **never** blocks > budget |
| T2 | Add the launcher **serving-green branch**: gate on probe GREEN **AND** `intent_flip` evidence **AND** owner-dead-or-heartbeat-stale (AM-P2-4 clause (ii) + AM-P2-2); commit-close mutations in **AM-P2-1's history-before-close order** (`current`→target → `previous`→displaced → history append `serving_green_commit` — with `quarantine_cleared_serving_green` appended **FIRST** when the target is quarantined — → `in_flight`→null → `quarantined[]` rewrite, opportunistic); `serving_green_commit` history row | `launcher.sh:975-1130` (flipped branch) | T1 | Commit-close leaves `current`=target, `in_flight`=null, `previous`=displaced; the `serving_green_commit` history row exists **before** `in_flight` is cleared (AM-P2-1); **no** quarantine / count / cooldown. **[PLANNER r4]** the earlier *"commit-close mutations in the `launcher.sh:1108-1127` order"* wording is **SUPERSEDED** — that range is the OLD close-before-history order (verified in-tree at this fold: `:1110` current → `:1118` previous → `:1120` `in_flight`→null → `:1122` history append); mirroring it would defeat AM-P2-10.3's journal check and red P-19 / P3-PIN-23 |
| T3 | Add the launcher **defer** branch: `sweep_deferred` history row; txn left OPEN; no mutations | `launcher.sh` (between T2 and the existing halt gates `:1135-1196`) | T1 | Sweep on a NOT-green stale flipped txn writes `sweep_deferred`, leaves `in_flight` intact, writes no other field |
| T4 | Add the **defer ceiling**: `: "${SWEEP_DEFER_CEILING:=3}"`; count `sweep_deferred` entries for this `run_id`; ceiling-th encounter ⇒ existing auto-rollback | `launcher.sh:69-94` (constants) + flipped branch | T3 | CEILING-th encounter executes `sweep_rollback` (existing chain); earlier encounters defer |
| T5 | Add `_js_quarantine_clear` (bash twin of `lib.sh:1945-1960`) — **only if** the launcher ever needs to clear (it does not in the GREEN path; the daemon clears) | `launcher.sh` (near `_js_quarantine` `:481-494`) | — | **DECISION: NOT NEEDED** — the launcher never clears a quarantine under this design; omit. (Kept here only so the omission is a recorded decision, not an oversight.) **[PLANNER r2]** The omission is now **explicitly scoped** rather than merely recorded: the **daemon** side owns the `quarantined[]` array rewrite (AM-P2-1 step 5); the **launcher** side writes the `quarantine_cleared_serving_green` **event** (the gate's acceptance signal — §D.3) and rewrites the array **only when the `quarantined` field literal is already present** (`_js_journal_update` cannot create a field — `launcher.sh:387-393`), treating its absence as a no-op. **⇒ the omission stands; no shell twin is added.** |
| T6 | Daemon: add the `readiness_provider` constructor kwarg + wire it at the lifespan seam | `daemon/services/upgrade_journal_sweep.py:154-160`; `daemon/api.py:2848-2857` | none | Sweep service receives a live provider; `None` ⇒ lane no-ops |
| T7 | Daemon: add `SWEEP_STALE_S = 600` mirror constant + the owner-dead predicate | `daemon/services/upgrade_journal_sweep.py:96-115` | none | Constant matches `launcher.sh:69`; documented as a protocol mirror |
| T8 | Daemon: implement `sweep_serving_green_closer()` — predicates **P1-P9** (P9 = the `intent_flip` authorization conjunct — **[PLANNER r5 → BLOCKER 1]** the predicate list now **ends at P9**, not P8; see §D.1's table and the note under it), boot-grace tracker (mirror `GraceTracker`), health read, **GREEN ∧ P9 ⇒ commit-close** | `daemon/services/upgrade_journal_sweep.py` (new method) | T6, T7 | GREEN ∧ P9 ⇒ journal `current`/`previous`/`in_flight`/history in the pinned order; **GREEN ∧ ¬P9 ⇒ no-action-this-tick** (never commit, never verdict, never request); NOT-GREEN ∧ P9 ⇒ `serving_unhealthy` verdict only |
| T9 | Daemon: implement the **quarantine clearance** (array rewrite + `quarantine_cleared_serving_green` event) inside the GREEN path | same file; writers `daemon/tools/upgrade_journal.py:375-389`, `:392-407` | T8 | A quarantined target is un-quarantined **only** on a GREEN verdict; the event is journaled |
| T10 | Shell: add `journal_quarantine_cleared_serving_green <ver> [<run_id>]` reader — **the ONE per-entry, delimiter-anchored reader of the §D.3 contract box** (the event kind, the delimiter-anchored `target $VERSION`, and the optional `run_id $RUN_ID` must all appear **inside ONE history entry**) — and amend `promote_entry_check`’s quarantine refusal | `scripts/upgrade/lib.sh:6559-6562`, new reader beside `:1963-1969` | T9 | A quarantined version **with** a matching clearance event is accepted at promote entry; **without** it, the refusal is unchanged (frozen rule intact). **[PLANNER r4]** the reader must ALSO **not** fire on the two near-miss fixtures — the prefix collision (`target v0.28.55` must not clear `v0.28.5`) and the cross-entry split (event in one entry, target in another) ⇒ **pin P-36**; and the writer detail format is pinned to the house `target $VERSION run_id $RUN_ID …` shape (§D.3's contract box) |
| T11 | Shell: `adopt_stale_txn` serving-green branch (probe via `_probe_once_classified`; GREEN ∧ `intent_flip` ⇒ commit-close + continue the promote **[DEV-FOLD-2026-11-02 — approver iter003 note 4: conjunct inline; §D.4 step 2 is normative]**) | `scripts/upgrade/lib.sh:6579-6626` + the stale+flipped branch `:6627+` | none | GREEN ∧ `intent_flip` ⇒ `serving_green_commit`, no quarantine/count/cooldown, promote continues; NOT-GREEN ⇒ existing chain byte-identical |
| T12 | Event vocabulary: register the new kinds; `_TERMINAL_EVENTS` membership; alert-map entries | `daemon/tools/upgrade_journal.py:646-651`, `:1874-1884`; `lib.sh` history writers | T2, T8, T11 | `serving_green_commit` ∈ terminal set; `sweep_deferred` ∉; alert entries per §D.7 |
| T13 | Documentation: the disposition doctrine, the defer ceiling, the clearance rule | `docs/runbooks/` (promote/upgrade runbook) + the ADRs cited (ADR-012/024, D-FA4.2/4.3/4.5) | T2-T12 | An operator can answer “why did the sweep defer / commit / roll back” from the journal alone. **[PLANNER r4]** the runbook must additionally name the **large-journal regime** explicitly: when the journal exceeds the launcher-side size guard (AM-P2-5's launcher-side `JOURNAL_SIZE_GUARD_BYTES` mirror of `lib.sh:1376`) the launcher's never-green path **degrades to the operator backstop** (conservative defer/halt, **never** rollback) ⇒ in that regime the **daemon-side closer is the only automatic rollback path** (it parses the journal in-process, so it is not subject to the launcher's O(N²) history-extraction cost — AM-P2-5's measurement: 13.5 s / 35.7 s at 27 KB, ≈285× at the incident size). The runbook must also name the GREEN-but-unauthorized operator-bounded residual (matrix row 6, §D.2 rung 1) and the two AM-P2-7 no-actor shapes (tempfail-75 / clean-exit-0) |

## Coupling

- **Tight with Phase 1 (hand-back verifier budget):** Phase 1 changes *how long* the hand-back verifier waits; this phase changes *what happens to the txn it leaves behind*. Phase 1 reduces the probability of a halt; this phase makes a halt non-catastrophic. **Phase 2 must not assume Phase 1 landed** (the halt path must remain correct with the 180s budget unchanged).
- **Tight with Phase 3 (test-map consolidation + the `_js_count_rollback` cooldown rider):** the rider edits `launcher.sh:473-478`, which is *called by* the disposition chain this phase re-orders. **Sequencing: Phase 2 lands first**; the rider folds onto the Phase-2 chain (or is folded in-commit with it), never onto the pre-Phase-2 chain.
- **Tight within itself:** T1 → T2/T3 → T4 (the defer ceiling depends on the defer rows); T6/T7 → T8 → T9 → T10 (the shell gate amendment depends on the daemon’s clearance event); T2/T8/T11 → T12 (the vocabulary depends on all three writers).
- **Loose with the readiness service:** T6/T8 read the cached composite; the composite’s *shape* (`ReadinessComposite`, `daemon/services/readiness.py:309+`) is owned by the readiness lane. Contract: the closer reads `composite.ready` (a bool) and nothing else — no shape dependency beyond that one field.
- **Independent of:** the promote pipeline’s preflight/integrity gates, the F2/nonce ceremony, the migration/promote machinery — none of their semantics change.

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| R-1 | **Version-string asymmetry** (`v0.28.5` vs `0.28.5`) mishandled ⇒ every healthy target reads as unhealthy ⇒ **wrongful rollback returns** | **High** | Medium | Mandatory pin on both sides (T1/T8 pins assert a GREEN verdict for a `v`-prefixed target and a NOT-GREEN for a mismatched one); single shared helper for the strip, one line, one place per side |
| R-2 | The `/dev/tcp` probe **hangs** the boot path (the 2026-10-01 sweep-hang class) | **High** | Low | Bounded `read -t` only (no unbounded read); total budget ≤ 5s; the probe is wrapped so a timeout is NOT-green (⇒ defer), never a hang; pin a "listener that accepts and never answers" leg (`soak_gate_fake_daemon.py` `never-all` mode already models it) |
| R-3 | The probe adds a **new dependency surface** to a purity-constrained file, violating R-08 | Medium | Low | A1/A2 adjudication is recorded in-repo (no external binary; `/dev/tcp` is a bash builtin feature); add a **static pin** asserting `launcher.sh` still contains **no** `curl`/`wget`/`nc`/`lsof`/`python3` tokens outside comments |
| R-4 | The defer never terminates in the “daemon never starts, launcher stays up” shape ⇒ a broken release stays live forever | **High** | Medium | Defer ceiling (T4) bounds it *across launcher boots*; the residual (one launcher lifetime) is R-4-explicit and is closed either by a run-loop re-sweep hook or by accepting option **2b** (§D.5). **Architect decision required (OQ-1).** |
| R-5 | The daemon-side closer judges the daemon **unhealthy during its own boot** (readiness degraded by design) ⇒ false NOT-GREEN ⇒ rollback | **High** | Medium | Boot-grace window (§D.1, mirror `GraceTracker` `readiness.py:754-810` — **[PLANNER r5 → sweep class (a)] the horizon is the closer's **own `UPGRADE_CLOSER_BOOT_GRACE_S` (default 300)**, **not** the 120 s `READINESS_GRACE_HORIZON_S` pool-busy constant — the r3 fold (AM-P2-24); the "horizon 120s" wording above is **SUPERSEDED**, append-only**) + the version check (the version is correct the moment the process starts) + the NOT-GREEN path only *requests* a disposition (it does not roll back by itself) |
| R-6 | The daemon-side closer races the launcher sweep (both act on the same txn) ⇒ double mutation | Medium | Medium | The launcher serializes on the D5 lock (`launcher.sh:921-944`) and revalidates the txn under it; the daemon side must take the **same journal lock** (`lock_acquire`, `upgrade_journal.py:826`) and re-read the txn under it, mirroring the launcher’s revalidate-under-lock discipline (`launcher.sh:933-944`) — **mandatory in T8** |
| R-7 | A half-written clearance (array rewritten, event missing — or vice versa) leaves the gate refusing a healthy version | Medium | Medium | Both sides write **both** surfaces; the shell reader accepts **either** signal (§D.3) |
| R-8 | `adopt_stale_txn` (T11) diverges from the launcher/daemon decision table ⇒ three different behaviours for one shape | Medium | Medium | All three share the **event vocabulary** and the **same verdict semantics**; a shared pin asserts the three produce the same disposition for the same fixture (matrix rows 1/2/8). **[PLANNER r5 → BLOCKER 5 — THE SHARED-PIN CLAIM IS SCOPED, NOT UNSCOPED (append-only; the text above is retained for the audit trail).]** The claim **cannot literally hold for every fixture**: on the **GREEN-without-`intent_flip`** fixture the three decision-makers **deliberately** diverge — the launcher **defers** (§D.2 rung 1), the daemon takes **no action this tick** (§D.1 P9), and the adopt path takes the **existing chain** (§D.4 (a-i); the adopt path's existing chain is its pre-ruling behaviour and this lane does not rewrite it). ⇒ the shared pin is **scoped to the fixtures where all three agree** — **GREEN-and-authorized** and **NOT-GREEN** — and the GREEN-but-unauthorized fixture is pinned **per decision-maker** (`P-26` / `P-15`'s negative axis for the launcher + daemon legs, and the adopt-path leg of the same pin). This is the **same scoping** already carried in §D.4 (a-i) and reconciled in `architecture-recommendation.md`'s reconciled-dispositions table (the GREEN / no-`intent_flip` row); this annotation brings the **risk row** into line with it. **The divergence is disclosed, not a defect to be unified.** |
| R-9 | The never-green path is weakened by an over-eager GREEN verdict (e.g. the probe sees a *stale* daemon still bound to the port) | **High** | Low | The probe asserts the **version matches the target** — a stale daemon on the same port answers with the OLD version ⇒ NOT-GREEN. Pin the "old daemon still listening" leg explicitly |
| R-10 | The event-vocabulary change (`_TERMINAL_EVENTS`) accidentally closes pending_ops on a *deferral* | Medium | Low | `sweep_deferred` is explicitly **excluded** from `_TERMINAL_EVENTS` (T12 pin) |
| R-11 | bash 3.2 (macOS) behaves differently for `read -t` on a socket fd ⇒ the probe misbehaves on Darwin | Medium | Medium | A1-A4 adjudication; pin the primitive under bash 3.2 **or** explicitly waive Darwin in this phase and pin Linux only — recorded as **OQ-5** |
| R-12 | The journal grows: a deferral row per launcher boot on a long-lived open txn | Low | Low | Bounded by the defer ceiling (≤3 rows per txn) + the journal size guard (`JOURNAL_SIZE_GUARD_BYTES=98304`, v0.27.15 lane) |

## Success Criteria

| # | Criterion | How to measure | Threshold |
|---|-----------|----------------|-----------|
| S-1 | A stale flipped txn whose target is serving green ends with the target **live** and **not** rolled back | Replay the 4-stage fixture through the launcher sweep with a green fake daemon | `serving_green_commit` present; `sweep_rollback` **absent**; `quarantined` unchanged/empty; `rollback_window_count` **unchanged**; `cooldown_until` **unchanged**; `current`→target |
| S-2 | A stale flipped txn whose target is **not** serving green still ends in the existing rollback | Same harness, `hard-red` / no-listener legs | `sweep_rollback` present; target quarantined; count incremented; `current`→prev — **byte-identical to today’s behavior** |
| S-3 | The **quarantine refusal is lifted** for a serving-green target, without weakening the frozen rule | Shell-level promote-entry check against a journal carrying (a) the clearance event, (b) neither | (a) accepted; (b) refused with the **unchanged** message |
| S-4 | The daemon-side closer commits-close a serving-green stale txn at the first tick after the staleness gate | Unit-level sweep-service test with a stubbed provider | `serving_green_commit` written; `in_flight` null; **no** rollback side-effects — **[PLANNER r5 → BLOCKER 1] and only when P9 (`intent_flip`) holds**; the GREEN-**without**-`intent_flip` leg must assert **no** commit-close at all |
| S-5 | The daemon-side closer does **not** roll back by itself when NOT-green | Same harness, not-ready provider | `serving_unhealthy` verdict written; **no** symlink repoint; **no** quarantine; **no** count — **[PLANNER r5 → BLOCKER 1] and the verdict itself requires P9** (absent `intent_flip` ⇒ no-action-this-tick ⇒ no verdict, no request) |
| S-6 | The deferral is **bounded** | Harness with `SWEEP_DEFER_CEILING=3` | 2 deferrals then the 3rd encounter rolls back; the txn is not stranded |
| S-7 | The launcher stays **pure** | Static scan of `launcher.sh` | zero `curl`/`wget`/`nc`/`lsof`/`python3` **executable** tokens (comment mentions excluded) |
| S-8 | The launcher’s boot path is **not slowed** beyond budget | Timing of the probe under a no-listener + a never-answering listener | ≤ the probe budget (5s default) in both legs |
| S-9 | The **adopt path** honours the ruling | `adopt_stale_txn` fixture, green vs not-green | green ⇒ `serving_green_commit`, no quarantine/count; not-green ⇒ the existing chain unchanged |
| S-10 | No live contact | Journal + tree review | zero writes outside the fixture dirs; no `releases/`, no `/home/nea/agents-ensemble` mutation |

## Acceptance / test pins (this phase owns the Part-2 pins; Phase 3 consolidates the map)

| Pin | Where | What it proves |
|---|---|---|
| **P-1** launcher serving-green commit-close | `tests/test_boot_sweep_commit_and_continue.sh` (new section; harness `make_install_fixture:66-85`, `seed_journal:94-104`, `run_sweep:123-134`) | stale flipped txn + green listener ⇒ `serving_green_commit`; **no** `sweep_rollback`; no quarantine; count/cooldown untouched |
| **P-2** launcher serving-unhealthy ⇒ existing rollback | same file, new section | stale flipped txn + `hard-red`/no-listener ⇒ `sweep_rollback` + quarantine + count (today’s chain, unchanged) **[PLANNER r3 → REV-R-1 — SUPERSEDED for the immediate arm; the row is KEPT, not deleted]** — under r2 the "not-green ⇒ fires now" shape no longer holds: the observation taxonomy is **four-way** (AM-P2-10.1) and **only the BROKEN class** (`/livez` answered non-200 / unparseable) fires **immediately**; `hard-red` (livez 200 + `/readyz` 500) is **AMBIGUOUS (unready)** and a **no-listener** observation is **AMBIGUOUS (absent)** ⇒ both **defer** and are **counted** toward the bounded absent-ceiling ⇒ the chain fires when the ceiling predicate holds. **Drive instead:** `P-23` (the ceiling fires ⇒ all four effects), `P-24` (the sticky green flag), `P-27` (the version-mismatch split) and the **BROKEN-immediate** leg via the `livez-non200` mode of the new fixture (`AM-P2-12`). This annotation exists because a tester building from the un-annotated row would hard-redden against the **correct** r2/r3 implementation |
| **P-3** **C5 re-shape** | `tests/test_boot_sweep_commit_and_continue.sh:279-302` | the current C5 pins “flipped+intent_flip+rollback_safe=true ⇒ sweep_rollback **as intended**” — it must **split** into a polarity pair: **C5a** green ⇒ commit-close (no rollback/quarantine/counter) and **C5b** not-green ⇒ the existing chain fires. The existing assertion text is a *pre-ruling* expectation and must be re-shaped, not deleted **[PLANNER r3 → REV-R-1 — SUPERSEDED for the C5b arm; the row is KEPT, not deleted]** — C5b is no longer a single "not-green ⇒ fires" assertion: it must be **parameterised on the observation class** per **AM-P2-10.1** (**BROKEN** ⇒ the chain fires **immediately**; **AMBIGUOUS** ⇒ **defer** + count toward the bounded ceiling, and the chain fires only when the ceiling predicate holds). **Drive instead:** `P-23` for the AMBIGUOUS leg and the `livez-non200` fixture mode for the BROKEN leg. A tester building the un-annotated C5b would hard-redden against the **correct** r2/r3 implementation |
| **P-4** deferral + ceiling | same file, new sections | not-green ⇒ `sweep_deferred` row, `in_flight` still open, **no** other field mutated; after `SWEEP_DEFER_CEILING` deferrals ⇒ the rollback fires |
| **P-5** probe primitive legs | new (unit-level, bash) | green (200 + matching version) ⇒ GREEN; old-version listener ⇒ NOT-GREEN; 500 ⇒ NOT-GREEN; no listener ⇒ NOT-GREEN; accept-and-never-answer ⇒ NOT-GREEN **within budget** (the `never-all` shape of `tests/helpers/soak_gate_pack_common.sh`). **[PLANNER r2 → AM-P2-11/AM-P2-12]** Two legs added, because the pre-ruling `/livez`-only probe made `hard-red` GREEN (the fake daemon answers `/livez` 200 in **every** mode): **hard-red (livez 200 + readyz 500) ⇒ NOT GREEN** (the canonical-GREEN discriminator) and **wrong-version 200 ⇒ NOT GREEN + fails fast** (no polling to the budget). The new-fixture legs live in **P-23**/**P-27**. |
| **P-6** daemon closer (both directions) | `tests/unit/tools/test_upgrade_journal_sweep_liveness.py` (472 lines, existing class structure) + `tests/unit/tools/test_upgrade_*.py` | GREEN ⇒ `serving_green_commit` + `in_flight` null; NOT-GREEN ⇒ `serving_unhealthy` only, **no** rollback side-effects; stale/owner-alive ⇒ **no action**. **[PLANNER r5 → BLOCKER 1 — the AUTHORIZATION PRECONDITION IS NOW PART OF THIS PIN'S ACCEPTANCE]** the GREEN arm fires **only when P9 holds** (the journal history carries an `intent_flip` event naming the target) ⇒ the pin must additionally assert the **negative leg**: **GREEN ∧ ¬P9 ⇒ NO commit-close** — no `serving_green_commit`, no `quarantine_cleared_serving_green`, no `serving_unhealthy`, no `rollback_requested`, the txn left **open** (AM-P2-4's canonical clause (ii): *"daemon no-action-this-tick"*). **Mutation-verification expectation:** drop the P9 conjunct from the closer ⇒ this pin goes **RED** (a mutation that the pre-r5 row could not catch, because the pre-r5 row did not require the conjunct at all). The negative axis is the same shape as `P-15`'s request-path negative (the R3 hinge) and `P-26`'s launcher leg. |
| **P-7** the **4-stage sequence** (d) | extend the C-matrix + `tests/helpers/journal_fixture_incident.py` + `tests/fixtures/journal/02-live-shaped-journal.json` | tonight’s shape ⇒ the HEAD shape: halt → serving-green → **commit-close** (+ `quarantine_cleared_serving_green`) → the shell gate **accepts** the next promote |
| **P-8** quarantine clearance **both sides** | shell-level + daemon-level | daemon clears array + writes event; shell gate accepts iff the event is present (or the array lacks the version); the frozen message is unchanged when neither is present. **[PLANNER r5 → SAME-PASS NOTE (the positive-half cross-ref)]** the **positive half** of this pin is the acceptance criterion named by **§D.3's contract box (v)** — *"The reader's shape is pinned by **`P-36`** (the two negatives: prefix collision + cross entry), and `P-8` gains the positive half. **A clearance reader that never fires is a REGRESSION to tonight's punishment class** — the pin must assert both that the matching event un-quarantines the version **and** that the two near-miss fixtures do not."* ⇒ **P-8 is the POSITIVE half and `P-36` is the NEGATIVE half of the same reader pin** (`phase2-plan.md` §D.3 contract box (v); `phase3-plan.md` `P3-PIN-28` is the consolidated-map row). A tester building P-8 alone must still assert the positive: a matching `quarantine_cleared_serving_green` event for the version **does** un-quarantine it. |
| **P-9** adopt-path polarity | lib.sh-level (the `adopt_stale_txn` fixture family) | green ⇒ commit-close + promote continues; not-green ⇒ existing chain unchanged |
| **P-10** version-string asymmetry | folded into P-1/P-5/P-6 | a `v`-prefixed target matches a non-`v` `__version__` ⇒ GREEN (the R-1 regression guard) |
| **P-11** purity static scan | new (or folded into the launcher test file) | `launcher.sh` contains no executable `curl`/`wget`/`nc`/`lsof`/`python3` |
| **P-12** lock discipline (R-6) | daemon-side test | the closer takes the journal lock and re-reads the txn under it; a concurrent change ⇒ no action |

**Fixture note (important, for the implementer):** the launcher’s sweep constants are **plain assignments** (`launcher.sh:69-94`), so the harness’s `SWEEP_STALE_S=0 HEARTBEAT_STALE_S=0` (`tests/test_boot_sweep_commit_and_continue.sh:123-134`) is **overwritten at source time** and has **no effect** — the fixtures are stale because their seeded `started_at` is in the past. New pins must not rely on overriding those constants; they must seed an old `started_at` (as the existing harness does). The **new** defer-ceiling constant must use the `:=` form so it *is* overridable (`: "${SWEEP_DEFER_CEILING:=3}"`), and the P-4 pin must drive it through that path.

**Test-fixture reuse (do not re-invent):** `tests/helpers/soak_gate_fake_daemon.py` already models every serving shape needed — `green` (`/livez` 200 + `/readyz` 200), `hard-red` (`/livez` 200 + `/readyz` 500), `never-all` (accepts, never answers), `never-readyz`, `flip`, and `version-aware` (resolves `$INSTALL_DIR/current` → manifest `binary_version` → hard-red when it equals `--bad-version`). It binds 127.0.0.1 only, logs every request as JSONL (non-vacuity assertions), and takes `--port --mode --version --install-dir --bad-version --log --ready-file`. The launcher-side P-1/P-2/P-4/P-5 pins should drive **this** stub rather than building a second one.

---

## Open Questions (each with this plan's recommendation — the architect pressure-tests next)

| # | Question | Options | **My recommendation** | Why |
|---|----------|---------|------------------------|-----|
| **OQ-1** | **Defer-ceiling default + the R-4 residual.** How many deferrals before the backstop fires, and what closes the “launcher stays up, daemon never starts” hole? | (a) `CEILING=3`, residual accepted; (b) `CEILING=3` + a run-loop re-sweep hook so the counter advances per child crash; (c) `CEILING=5` + the same hook | **CEILING = 3**, form `: "${SWEEP_DEFER_CEILING:=3}"` (env-overridable so the pin can drive it); residual handled by **option 2b** (accept — the daemon-side closer is an accelerator, the launcher sweep remains the primary rollback engine). **[PLANNER r5 → sweep class (e) — the precedence clause is SUPERSEDED (append-only; the text above is retained for the audit trail).]** "the launcher sweep remains the primary rollback engine" predates the daemon closer's existence. Under this plan the **daemon closer normally wins**: it ticks every 90 s while the daemon is up and reaches its verdict at the **first tick after the 600 s staleness gate** (≈11 min), while the launcher's bounded absent-ceiling needs **≈10–15 min** of *qualifying* observations and only reaches the shapes the daemon cannot (never-started daemon; tempfail-75 / clean-exit-0). ⇒ **the daemon-side closer is the primary disposer while the daemon is up; the launcher sweep is the backstop** (normative precedence statement: the Defer-vs-Deadlock matrix (a)'s *Launcher/daemon precedence* paragraph; `decisions.md` §6.1). The **CEILING = 3** and the **`:=` form** recommendations are **UNCHANGED** by this annotation. Defer (b)’s run-loop re-sweep hook to its own commission | 3 mirrors the house “three strikes” (`SWEEP_ROLLBACK_CAP_24H=3`, `launcher.sh:78`) and is ample: the daemon-side closer acts at ≈11min, long before a 3rd launcher boot. The `:=` form is required for testability (the mirrored constants are plain assignments and **cannot** be overridden — `launcher.sh:69-94`). |
| **OQ-2** | **(f) rollback execution for daemon-side unhealthy verdicts** | (1) `spawn_executor` transient unit; (2) journal disposition → next launcher boot; (3) operator runbook | **Option 2 primary**, Option 3 as the documented escalation, Option 1 **out of scope** for this phase | Option 2 adds **zero** new execution surface and executes the rollback at the *safe* point (the launcher sweeps **pre-daemon** — `launcher.sh:1661` vs `:1525`), and it carries no nonce/F2 semantics. Option 1 would make the daemon orchestrate its own termination — a new self-directed-kill class. The residual (a launcher that never re-boots) is R-4 / OQ-1. |
| **OQ-3** | **Event naming** | (a) `sweep_deferred` / `serving_green_commit` / `quarantine_cleared_serving_green`; (b) `sweep_commit` / `sweep_defer` / `quarantine_cleared_serving_green`; (c) prefixed by side | **(a)** — `serving_green_commit` is **side-neutral** (launcher, daemon and `lib.sh` all write it) and `quarantine_cleared_serving_green` is **owner-ratified verbatim** (must not be renamed). `sweep_deferred` matches the launcher’s existing `sweep` / `sweep_rollback` vocabulary | The launcher vocabulary is `halt`, `boot_sweep_commit_and_continue`, `sweep_rollback`, `sweep` (`launcher.sh:1122`, `:1225`, `:1246`). A side prefix would fragment the vocabulary. |
| **OQ-4** | **`adopt_stale_txn` scope** | (a) same phase, separate task (T11); (b) deferred to a follow-up; (c) dropped | **(a) — same phase, T11** | It is the **same wrongful-rollback shape** on the promote-entry decision-maker (`lib.sh:6579-6626` documents the mirror discipline). Leaving it untreated means the ruling does not hold on the path an operator is most likely to walk next (re-promote after tonight’s refusal). `lib.sh` is not purity-constrained, so it can use the mature classified probe (`lib.sh:3841-3860`). Cost: one scoped task + two pins. |
| **OQ-5** | **Alert-map entries for the new events** | (a) none for all three; (b) `quarantine_cleared_serving_green` → a new kind; `serving_unhealthy` → `upgrade_auto_rollback`; (c) all three alerted | **(b)** | `serving_green_commit` is a **good** outcome and must stay silent (the policy is explicit: *“commit … emit NOTHING”* — `upgrade_journal.py:590-596`; `boot_sweep_commit_and_continue` is likewise silent). But `quarantine_cleared_serving_green` **reverses** an alert the operator already saw (`quarantine` → `upgrade_auto_rollback`, `:649`) — silence would leave the operator believing the release is still condemned. `serving_unhealthy` announces a rollback that the launcher will execute ⇒ reuse `upgrade_auto_rollback` so the operator’s existing watch catches it. |
| **OQ-6** | **Does the serving-green path require `intent_flip` evidence?** | (a) YES — serving-green alone is insufficient; (b) NO — a positive serving verdict is self-authenticating | **(a) — require `intent_flip`** | A positive health read proves the daemon is *running* the target; it does **not** prove the flip was operator-authorized. The existing commit path already sets this evidence standard (`launcher.sh:1068-1079`, reader `launcher.sh:518-536`). Without the evidence, a hand-edited or forged journal could make the sweep “commit” an unauthorized flip. The cost is matrix row 6 (an unauthenticated flip defers → ceiling → rollback) — the conservative outcome, and the correct one. |
| **OQ-7** | **bash 3.2 / Darwin support for the probe** | (a) support both (pin bash 3.2); (b) Linux-only, Darwin explicitly waived; (c) use `_js_run_bounded` + a nested bash instead of `read -t` | **(b) — Linux-only for this phase, with the waiver recorded** | The launcher carries Darwin arms (`launcher.sh:247-263`, `:759-770`) so a silent Linux-only probe is a real (if small) portability regression. The `read -t`-on-a-socket-fd behaviour under bash 3.2 is **unverified** in this tree. Option (c) reintroduces the hang-class risk the `_js_run_bounded` design was built to avoid. Recommendation: implement with `read -t` + the `<&3` form (A4), pin Linux, and file a **small follow-up** to verify/port to bash 3.2 rather than guessing now. |
| **OQ-8** | **Defer-counter mechanism** | (a) history-count keyed by `run_id`; (b) a new journal field written by the daemon + read by the launcher; (c) a field inside `in_flight` rewritten wholesale by the launcher | **[PLANNER r5 → sweep class (d) — option (a) is SUPERSEDED]** **(a) is REJECTED** by **AM-P2-5**: the counter's home is the launcher-local file `<INSTALL_DIR>/.sweep-defer-<run_id>` (O(1); survives launcher restarts; no journal schema change; not in `releases/`). The drafted history-count carried three verified defects — cross-entry false positives, the measured **O(N²)** cost (13.5 s / 35.7 s at 27 KB ⇒ ≈285× at the incident size) with **no** launcher-side size guard, and the **inverted** over-count doctrine. Option (b) (a daemon-created field) was considered and rejected as the *lesser* of the two alternatives (it adds an `ensure_extensions` schema surface + a reader-freshness problem on pre-existing journals); option (c) was never adopted. **The option (a) wording is retained above for the audit trail; the normative home is AM-P2-5.** | The launcher **cannot create a journal field** (`_js_journal_update` returns 1 on an absent field — `launcher.sh:387-393`), and the launcher sweeps **before** the daemon exists, so the daemon cannot pre-seed one. A history-count reuses the proven conjunction-reader technique (`launcher.sh:518-536`), needs no new writer semantics, and over-counts in the **safe** direction (accelerate the backstop) — the same doctrine as the existing rollback counter (`launcher.sh:1144-1147`). |
| **OQ-9** | **`_TERMINAL_EVENTS` membership + pending_op closure** | (a) `serving_green_commit` ∈ terminal (mirrors `boot_sweep_commit_and_continue` at `:1883`); `sweep_deferred` ∉; (b) all three ∉ | **(a)** | A commit-close **is** a terminal outcome for the txn; the existing precedent (`boot_sweep_commit_and_continue` ∈ `_TERMINAL_EVENTS`) says so. A deferral closes nothing — if it were terminal, a deferral would close a pending_op, which is exactly wrong. `quarantine_cleared_serving_green` is an annotation on an already-terminal outcome ⇒ ∉ by itself. |
| **OQ-10** | **Who wins when the launcher sweep and the daemon closer race the same txn?** | (a) whoever takes the journal lock first, each revalidating under it; (b) the daemon always wins (it has the richer verdict); (c) the launcher always wins | **(a) — lock + revalidate under it, on both sides** | The launcher already serializes on the D5 lock and revalidates the txn under it (`launcher.sh:921-944`) — the txn **object** is the identity, and any field change means the owner acted. The daemon side must adopt the same discipline (take `lock_acquire`, `upgrade_journal.py:826`, re-read, re-check the txn) — mandatory in T8, pinned by P-12. The verdicts are compatible: both write the same events and the same mutation order, so “who wins” changes only the journal author, not the outcome. |

---

## Research Insights (what shaped this plan — all citations verified against `941cb6321`)

- **The launcher-boot sweep is the decision-maker, and it is pre-daemon by construction.** `main()` runs `_journal_sweep` at `launcher.sh:1661-1662`, resolves the binary at `:1665`, and only then enters `run_loop` (`:1507`) which starts the daemon child (`:1525`). ⇒ “port down” at sweep time is ambiguous; deferring is the only sound default.
- **The systemd unit’s `ExecStart` IS the launcher** (`scripts/systemd/ensemble-daemon.service`, `ExecStart=__LAUNCHER__` + the “WHY THE LAUNCHER is the ExecStart” rationale) ⇒ a launcher boot is **routine** whenever the unit restarts (`Restart=on-failure`, `RestartSec=10`), which is what makes the defer-ceiling mechanism and the option-2 disposition executor viable.
- **The daemon never touches flipped txns today.** `reconcile_pending_op` returns `None` whenever `in_flight` is a dict (`daemon/tools/upgrade_journal.py:2035-2036`); the restart-arm path has an explicit flipped=true never-touch guard (`:2262-2272`).
- **The sweep service is always-on with no kill switch** (`daemon/api.py:2813-2815`) and ticks every 90s (`upgrade_journal_sweep.py:96-99`); the per-tick kill-switch precedent is `ENSEMBLE_POST_RESTART_ARM_NOTIFY` (`:416-424`).
- **The health surfaces are cheap and in-process.** `/livez` = `start_time` + `__version__`, zero DB (`daemon/api.py:5436-5450`); `/readyz` serves the **cached** composite, O(1) (`daemon/api.py:5452-5533`; composite set at `:2717`, refreshed at `:4452`). The sweep service currently receives only `install_dir` + `manager` (`upgrade_journal_sweep.py:154-160`) ⇒ a new constructor kwarg at the same lifespan seam (`daemon/api.py:2848-2857`).
- **The boot-grace precedent exists and is well-specified.** `GraceTracker` (`daemon/services/readiness.py:754-810`): observation-relative arm, horizon `READINESS_GRACE_HORIZON_S=120.0` (`daemon/constants.py:165`), never extended, re-arm only after a full lapse.
- **Launcher purity is a hard, in-file constraint.** *“launcher.sh is SELF-CONTAINED … has ZERO python3 dependency … DO NOT add a python3 dep here — launcher purity is a hard constraint (R-08)”* (`launcher.sh:1040-1053`); `grep -c curl launcher.sh` → **0**; `lib.sh` **does** use curl (`lib.sh:3804+`, classified probe at `:3841-3860`).
- **`/dev/tcp` has in-repo precedent** (`tests/helpers/soak_gate_pack_common.sh:215`; `test/packs/hard_reboot_drill_e2e_test.sh:354`) — a bash builtin feature, hence R-08-clean.
- **The launcher cannot create journal fields** (`_js_journal_update` requires the field literal to exist — `launcher.sh:387-393`), and the launcher sweeps **before** the daemon ⇒ no daemon-seeded field is reachable.
- **The frozen quarantine rule is shell-side** (`lib.sh:6559-6562` refuses; `journal_is_quarantined` at `:1963-1969` reads the array) and its only clear path is re-staging (`journal_quarantine_clear` `lib.sh:1945-1960` ← `stage.sh:674`) ⇒ the clearance must be amended on **both** sides.
- **The doctrinal precedent for “a good outcome is not a bad outcome” is already written down:** *“NOT done: sweep-rollback, quarantine, counter+cooldown arming. The cap/cooldown are anti-flapping responses to BAD outcomes; a successful-and-declared flip is not a bad outcome”* (`launcher.sh:1034-1036`), and the same hazard is documented in `journal_fail_loud` (`lib.sh:1963-1983`).
- **The version strings differ:** `daemon/__init__.py:3` → `__version__ = "0.28.5"`; the release directory is `v0.28.5`; `/livez` reports `__version__` (`daemon/api.py:5448`; `LivezResponse.version`, `daemon/models/common.py:138`).
- **The launcher’s mirrored constants are plain assignments** (`launcher.sh:69-94`) ⇒ they cannot be overridden by the environment; the only `:=`-form default in the file is `: "${MALLOC_ARENA_MAX:=2}"` (`:1658`).
- **The daemon-side sweep already has a “never touch a flipped txn” guard**, which is the correct default and is **not** to be widened (§D.1).

## Exit Criterion

This phase is done when:

1. All tasks T1-T13 are implemented and merged on `fix/promote-handback-halt`.
2. Pins **P-1 … P-12** pass, with **P-3 (C5 re-shape)** and **P-7 (4-stage)** explicitly green.
3. The composition requirement is proven **in both directions** on **all three** decision-makers: serving-green ⇒ commit-close (no rollback, no quarantine punishment); serving-unhealthy ⇒ the existing rollback chain still fires.
4. The launcher remains **pure** (P-11) and its boot path stays within budget (P-8).
5. Zero live contact; all fixtures are synthetic; the daemon-side journal fixture (`tests/helpers/journal_fixture_incident.py`) is **generated, never copied from the live install** (its own docstring states this rule).
6. The phase-3 hand-off notes carry: the deferred R-4 residual, the OQ-7 bash-3.2 waiver, and the sequencing constraint that the `_js_count_rollback` cooldown rider lands **after** this phase’s chain re-order.


---

## [ARCHITECT r1] Amendments — 2026-11-02 (enrichment pass; evidence re-verified in-tree at base 941cb6321)

> Appended per the append-only doctrine. Binding on the implementer unless the plan council overturns. Binding rulings folded: owner ruling (serving-green = stay), composition requirement, OQ-C-11 (intent_flip BOTH sides + pin), OQ-C-22 (one release).

### AM-P2-1 🔴 — D.1/D.2 GREEN-path mutation ORDER: history-before-close invariant (closes two crash windows)
The drafted order (current → previous → in_flight→null → history → quarantine-clear) leaves two dangerous prefixes: (a) crash after `in_flight→null` before history ⇒ closed txn, no terminal event ⇒ `pending_op` lingers (pipeline-busy); (b) crash after `in_flight→null` before the clearance ⇒ **serving-green release permanently quarantined** — tonight's punishment shape surviving in a narrow window (no actor retries a closed txn).
**Required order (both closers, launcher and daemon):**
1. `current` = target
2. `previous` = displaced_version
3. history append `serving_green_commit` — and, FIRST if the target ∈ `quarantined[]`, append `quarantine_cleared_serving_green` (the EVENT is the amended shell gate's acceptance signal per D.3, so event-before-close makes the clearance crash-proof)
4. `in_flight` → null  ← the txn "closes" ONLY here
5. `quarantined[]` array rewrite (state hygiene; idempotent; retry-safe — the gate accepts on the event, so a trailing un-rewritten array cannot re-refuse)
   - **[PLANNER r2 → T5] This step is SCOPED: the DAEMON side owns the array rewrite; the LAUNCHER side writes the EVENT (mandatory) and MAY rewrite the array opportunistically.** The launcher **cannot create** the field — `_js_journal_update` returns 1 when the field literal is absent from the document (`launcher.sh:387-393`) ⇒ a launcher-side rewrite is possible **only when the `quarantined` literal already exists** (which it does in any journal that has ever quarantined). ⇒ the launcher writes `quarantine_cleared_serving_green` (the gate's acceptance signal — §D.3's *event-first* rule) and rewrites the array **only if** the literal is present, treating its absence as a **no-op, not an error** (there is nothing to clear). **T5 stays omitted** — no new `_js_quarantine_clear` shell twin is needed, because the **event** is the load-bearing surface and the array rewrite is hygiene. **P-8 is reconciled accordingly** (the shell gate accepts on the event; the daemon clears the array).
Every crash prefix P0–P5 is now safe: re-run is a no-op or completes the sequence. **Pin P-19** (fault-injection): interrupt at each prefix ⇒ the next actor's disposition is never a wrongful rollback and the sequence converges. (An independent per-tick self-healing lane that clears any quarantined version found serving green is optional hardening — noted, not mandated.)

### AM-P2-2 🔴 — owner-liveness on the launcher stale path (closes GAP: closing a LIVE promote's txn)
The launcher's stale gate (`launcher.sh:906-920`) presumes owner-dead without checking; the 900s ceremony cap exceeds the 600s staleness gate, so a slow-but-live promote is reachable. The serving-green branch must additionally require **owner-dead or heartbeat-stale** (mirror the daemon closer's P7) before commit-closing. Apply the same conjunct to the rollback branch (pre-existing exposure; one predicate).

### AM-P2-3 🔴 — T8 lock discipline explicit (busy ⇒ no-action-this-tick)
T8 must state: the daemon closer takes `lock_acquire(..., wait_s=0)`; treats **busy as no-action-this-tick** (a promote may hold the pipeline lock across preflight+flip+commit ≫ 600s); re-reads the journal under the lock; re-checks ALL predicates (P1–P9) against the re-read; releases. Lock acquisition alone is not the discipline — revalidate-under-lock is (the launcher's `launcher.sh:933-944` pattern is the model).

### AM-P2-4 🔴 — D.1 predicate table: add P9 = intent_flip (OQ-C-11 RULED; closes an authorization bypass on 2 of 3 decision-makers)
P1–P8 as drafted carry NO `intent_flip` conjunct ⇒ the daemon closer and the adopt path would commit serving-green txns WITHOUT authorization evidence. **Add P9** (daemon): journal history contains an `intent_flip` event naming the txn's target, via a NEW daemon history read (none exists today). Absent P9 ⇒ no-action-this-tick (never commit, never rollback). **T11** (adopt): same conjunct + a NEW `journal_history_has_intent_flip <ver>` reader in `lib.sh` beside `journal_is_quarantined` (`lib.sh:1963`) — verified: `lib.sh` has ZERO intent_flip readers today (the plan's "lib.sh has its own history reader" is imprecise: it has writers only).
**Canonical predicate (identical on all three decision-makers — fold verbatim):**
> A serving-green commit-close fires only when BOTH hold: (i) a positive serving verdict for the txn's target — `/livez` 2xx with `version` == v-stripped target (launcher `_js_serving_probe`; `lib.sh` adopt `_probe_once_classified`; daemon `__version__` == v-stripped target ∧ cached readiness composite `ready`) — AND (ii) the journal history contains an `intent_flip` event naming that same target. Absent (ii) ⇒ launcher DEFER / daemon no-action-this-tick / adopt existing-chain — never commit, never immediate rollback.

**[PLANNER r2 → AM-P2-11] The clause (i) GREEN predicate is replaced by the leader's OQ-2 canonical GREEN, verbatim:**
> **GREEN** ⇔ the launcher obtained **`/livez` 200** ∧ the reported `version` equals the **v-stripped** target ∧ the launcher obtained **`/readyz` 200** whose body reports ready. The `/readyz` GET is a **second request over the same `/dev/tcp` fd** (no second connect, no second port race). A **wrong version fails fast** — it is not a "keep polling" condition.
> **Daemon side:** GREEN ⇔ `__version__` == the v-stripped target **AND** the cached readiness composite is not `None` **AND** `composite.ready` is True.

*Why the `/readyz` clause is load-bearing:* `tests/helpers/soak_gate_fake_daemon.py` answers **`/livez` 200 in every mode** (verified in-tree: the `/livez` handler branch is unconditional; `hard-red` degrades **only** `/readyz`) ⇒ a `/livez`-only probe classifies `hard-red` as GREEN and the direction-② pin proves nothing (AM-P2-12). **[PLANNER r2]** The clause (ii) `intent_flip` conjunct is **unchanged** — and it now also governs the **request** path (AM-P2-15), which previously bypassed it.
**Pin (three-axis, mutation-proven, per the ruling):** (a) green + intent_flip ⇒ commit-close; (b) green + NO intent_flip ⇒ defer / no-action / existing-chain (never commit); (c) serving-unhealthy ⇒ existing never-green rollback still fires.

### AM-P2-5 🔴 — D.6 defer counter RE-HOMED + ceiling eligibility predicate (amends OQ-C-13; inverts the over-count doctrine)
Three verified defects in the drafted history-count: (1) **cross-entry false positives** — the conjunction `*"event":"sweep_deferred"*run_id <id>*` matches event-in-entry-A + run_id-in-entry-B, and `grep -c` counts lines while the history array is ONE line; (2) **"over-count is safe" is INVERTED under the owner ruling** — the count gates the rollback itself, an over-count fires a wrongful rollback, and the live `rollback_window_count` is **2/3** so one false rollback arms the 24h promote-halt cap; (3) **O(N²) cost, no guard** — measured on a copy of the live journal (27KB/157 entries): `_js_journal_read` 13.5s, `_js_json_sub history` 35.7s; ≈285× at the 456KB incident size ⇒ hours; `JOURNAL_SIZE_GUARD_BYTES` is lib.sh-only (0 hits in `launcher.sh`).
**Required changes:**
- **Counter home = launcher-local state file** `<INSTALL_DIR>/.sweep-defer-<run_id>` (sibling of `.launcher-state`; O(1); survives launcher restarts; no journal schema change; NOT in `releases/` which is release-machinery-scanned). A daemon-created top-level field (`ensure_extensions` + `_js_json_field` ≈0.02s, absent ⇒ degrade to no-count) is the admissible alternative; the file is preferred (no schema surface).
- **Ceiling eligibility (owner-ruling alignment):** the ceiling fires ONLY on a **positive** unhealthy observation (answered-but-wrong: non-200, version mismatch) or a `rollback_requested` disposition — NEVER on an absent/port-down observation ("absent" is ambiguous-by-construction; a target observed green between observations must not be rolled back by a counter). Pure-absent sequences defer to the operator (AM-P2-7 runbook).
  - **[PLANNER r2 → AM-P2-10] THIS ONE CLAUSE IS AMENDED** — it is the council's REJECT basis. The absent/port-down observation is **no longer excluded** from the rollback-eligibility set: it is admitted under the **bounded absent-ceiling** (≥3 qualifying observations ∧ owner-dead ∧ `intent_flip` ∧ never-green-observed). The "pure-absent sequences defer to the operator" sentence survives **only** for the two shapes that produce no further launcher boot (tempfail-75 / clean-exit-0) — the operator-bounded residual (AM-P2-7, §4.5). **Every other element of AM-P2-5 stands unchanged** (counter re-home, single history extraction per boot, launcher-side size guard, the `≤ CEILING−1` pin P-20).
- **Single history extraction per boot**, shared by every history reader (intent_flip, rollback_requested) — three readers must not mean three 35s extractions.
- **Launcher-side journal size guard** (mirror `lib.sh:1376`): breach ⇒ conservative defer/halt path, NEVER rollback, loud log. A launcher boot that hangs for hours is the regression class this commission exists to close.
- **Pin P-20:** at most `CEILING−1` deferrals occur before the rollback branch (never exact-count); mutation: plant a false count ⇒ RED.

### AM-P2-6 🔴 — NEW TASK T14: daemon→launcher rollback-disposition handoff (the OQ-C-07 seam; the register's own Amendment-2 gap)
**Write contract — history event, NOT a field:** the daemon's NOT-GREEN branch appends `rollback_requested` with detail `target <VER> run_id <RUN_ID> reason <not-ready|version-mismatch>` via `journal_history_append` **under the journal lock**. The field alternative is REJECTED with reason (not silently): the launcher cannot create fields (`launcher.sh:387-393`); a daemon-created field adds an `ensure_extensions` schema surface + reader-freshness problem on pre-existing journals; the event mirrors the proven intent_flip conjunction technique; one atomic append, no clear-write crash window.
**Reader contract:** NEW launcher reader `_js_history_has_rollback_request <journal> <target> <run_id>`, modeled byte-for-byte on `_js_history_has_intent_flip` (`launcher.sh:518-536`), quote-anchored conjunction on BOTH target and run_id (a bare-event read could execute a replaced txn's request). Absent/absent-field degrade ⇒ "no disposition", never a spurious rollback. **[PLANNER r4]** the *"modeled byte-for-byte … quote-anchored conjunction"* wording is **SUPERSEDED** by **AM-P2-14** (the very next paragraph): the reader must be **per-entry**, not a whole-array conjunction, and must carry the **delimiter anchor** on the version literal (the same shape pinned for the clearance reader in §D.3's contract box) ⇒ the R-15 class (prefix collision + cross-entry) is closed on this reader too.

**[PLANNER r2 → AM-P2-14] The reader must be PER-ENTRY, not a whole-array conjunction.** `_js_history_has_intent_flip` matches the **whole history array** (`pattern='*"event":"intent_flip"*target '"$target"'*'`, `launcher.sh:518-536`) ⇒ it matches *"event kind in entry A"* ∧ *"target string in entry B"* — a **cross-entry false positive**. The new request reader **must not inherit that shape**: the reader splits the history into entries and requires the event kind, the `target <VER>` substring **and** the `run_id <RUN_ID>` substring to appear **inside ONE entry**. A cross-entry false positive here would **execute a rollback of a serving-green release** — the exact incident class. **PIN: P-25 (the cross-entry negative).** The alternative the finding names — moving the request to the launcher-local file — is **admissible but NOT adopted** (the daemon would lose the ability to request a rollback). **Named follow-up:** the pre-existing `_js_history_has_intent_flip` twin has the same weakness (its failure direction is a false *authorization*) — a separate hygiene commission, recorded as **R-15**, **not** silently changed here.
**Placement — POSITION 0** in the flipped branch, BEFORE the serving probe and the defer path (else the launcher defers on a NOT-GREEN txn and the request starves forever):
1. request present for (target, run_id) ⇒ **re-probe**: GREEN ⇒ **commit-close** (ruling precedence: serving-green = stay; journal the request as superseded; do NOT execute); NOT-GREEN ⇒ execute the EXISTING auto-rollback chain byte-identically (`launcher.sh:1197-1230`: flip → quarantine → current=prev → `_js_count_rollback` (counter AND cooldown, per OQ-C-23) → `sweep_rollback` row → in_flight=null), detail noting `disposition=rollback_requested`.
   - **[PLANNER r2 → AM-P2-15] The GREEN arm of this step is gated on `intent_flip` too.** As written above, a **forged** `rollback_requested` + a **green** probe could commit-close an **unauthorized** flip — the R3 hinge. Corrected: **GREEN ∧ `intent_flip` ⇒ commit-close** (request superseded) · **GREEN ∧ ¬`intent_flip` ⇒ DEFER + journal** (never a commit-close, never a rollback) · **BROKEN ⇒ the existing chain** · **AMBIGUOUS ⇒ the existing chain** (the daemon's post-grace verdict is the affirmative evidence). The full table is in **AM-P2-15**; the negative axis is added to **P-15**.
2. no request ⇒ existing ladder unchanged.
Ceiling interplay: a request PRE-EMPTS the ceiling (strictly stronger evidence); both paths converge on ONE chain ⇒ identical post-state.
**Pins P-13…P-18 (mutation-proven):** P-13 request + NOT-GREEN ⇒ executed, post-state byte-identical to the legacy no-request path; P-14 no-request ⇒ defer unchanged; P-15 request + GREEN ⇒ commit-close, request NOT executed; P-16 ceiling-vs-request convergence (identical post-state); P-17 isolation (request naming another run_id/target ignored); P-18 daemon writes the request with ZERO symlink/quarantine/counter side-effects.

### AM-P2-7 — OQ-C-06 adjudication (leader direction executed; premise CORRECTED) + NEW TASK T15
**Premise correction:** there is **no 6h promote-txn `in_flight` reaper** in-tree. The blueprint's "6h reaper" is the pending_op/arm-executor lane; `reconcile_pending_op` returns None when `in_flight` is a dict (`upgrade_journal.py:2034-2036`) and `close_restart_arm_executor_exit` is restart-kind-only (`:2262-2272`). The only clearers of a flipped promote txn are the launcher sweep and `adopt_stale_txn`.
**Verified outcome:** tempfail-75 (burst-budget exempt, `launcher.sh:1412-1414`) and clean-exit-0 (`:1564-1569`; `Restart=on-failure` does not restart success) produce **no launcher boot and no live daemon** ⇒ nobody acts ⇒ genuinely unbounded ⇒ per the leader's direction: extend + runbook + disclose; do NOT block.
**Extension = T15 — run-loop re-sweep hook:** one seam in `run_loop` (`launcher.sh:1507-1560`) re-running the idempotent, lock-guarded `_journal_sweep` after each child exit. Value: a `rollback_requested` written by the daemon during an up-window is EXECUTED at the next child cycle instead of waiting for a launcher boot that never comes in these shapes (T15 composes with T14). Constraint: the hook's own observation (child just exited ⇒ port absent) is NOT ceiling-eligible (AM-P2-5) — the hook advances disposition-execution, never inference-based rollback.
**Residual (operator-bounded, disclosed):** if the daemon never completes another boot, no system actor rescues the txn. **Runbook terminal backstop:** "A flipped txn open past `SWEEP_STALE_S` with the target not serving: `systemctl restart ensemble-main` (advances the sweep); if the target never serves green and no positive unhealthy verdict exists, the terminal escape is manual `rollback.sh` (D.5 Option-3 escalation) — manual rollback counts toward the 3/24h cap and arms no cooldown."
**P-7 SPLIT:** P-7a launcher-only restart ⇒ the launcher probes green ⇒ commits; P-7b full unit restart ⇒ the launcher defers (port down) ⇒ the daemon closer commits at the first tick past the staleness gate. Tonight's 22:25 restart semantics determine which leg reproduces the incident — pin BOTH.

### AM-P2-8 — D.7 vocabulary: recommendations ⇒ REQUIREMENTS + two hidden consumers
- `ALERT_KIND_BY_EVENT` is an **EXACT dict lookup** (`upgrade_journal.py:758`, verified) ⇒ NO false-`upgrade_auto_rollback` collision from `quarantine_cleared_serving_green` (collision hypothesis refuted). The REAL hazard is **silence** — without an explicit entry the clearance reverses nothing the operator saw. ⇒ explicit entries REQUIRED: `"quarantine_cleared_serving_green": <new kind>` and `"serving_unhealthy": "upgrade_auto_rollback"` (detail payload names request-vs-executed per OQ-C-10(d)).
- 🔴 **Wake-set growth (plan-silent):** `serving_green_commit` ∈ `_TERMINAL_EVENTS` auto-grows `PROMOTE_WAKE_TERMINAL_EVENTS` (`upgrade_journal.py:1942`) whose reader is TS-scoped and **run_id-blind** (`:1953-1975`) ⇒ an UNRELATED commit-close can silently DROP a pending promote-wake (a paused instance never resumes). Required: the closer's rows carry `run_id` AND the promote-lane wake reader gains a run_id-scoped tie-break (precedent: `upgrade_journal_sweep.py:439-450`), OR the event is excluded from the wake set by explicit pinned construction. **Pin P-21.**
- 🟡 **Two hard-red test pins the vocabulary growth breaks** (name them; the implementer must not improvise): `len(uj._TERMINAL_EVENTS) == 9` (`tests/unit/services/test_post_restart_arm_notify_sweep.py:875`) ⇒ 10 + set-equality; label-coverage guard (`tests/unit/tools/test_upgrade_tools.py:886-899`) ⇒ add `_OUTCOME_LABELS["serving_green_commit"]` (`upgrade_tools.py:1255-1310`). **Pin P-22.**
- **`composite-missing` is a cannot-tell, NOT an unhealthy verdict** (`api.py:2717` composite None until first tick; `/readyz` fails closed) ⇒ classify DEFER, never `serving_unhealthy` ⇒ rollback request. An in-process wiring defect must not become a rollback.
- The shell clearance reader must be **quote-anchored** (`*"\"$ver\""*`) so `v0.28.5` cannot match `v0.28.55` (mirrors `lib.sh:1967`). **[PLANNER r4 → SUPERSEDED]** this prescription is **withdrawn** (append-only; the text is retained above for the audit trail). The quote-anchored shape matches the **`quarantined[]` array** (whose entries really are quoted — `lib.sh:1967`), **not** the history-detail string, where the version is an **unquoted** literal inside a JSON-escaped detail ⇒ a quote-anchored match **never fires** and the clearance path would never lift the frozen refusal (tonight's punishment class reproduced). **The ONE pinned reader** is the **per-entry, delimiter-anchored** one in **§D.3's contract box** (event kind + delimiter-anchored `target $VERSION` + optional `run_id $RUN_ID`, all inside ONE entry) ⇒ prefix collision and cross-entry both closed.

### AM-P2-9 — T1/T2 probe acceptance additions
- Explicit clause + truncated-body mutation leg: "version field absent/unparseable ⇒ NOT-GREEN ⇒ defer (never green)" — the wrongful-COMMIT mirror of tonight.
- **Wall-clock deadline INSIDE the body-read loop** (per-read `-t` alone is defeatable by a slow-dribble responder: each read resets the timer).
- State the exec-chain fact: `launcher.sh:1` `#!/bin/bash`; unit `ExecStart` is direct ⇒ shebang honored ⇒ `/dev/tcp` available; the probe pins 127.0.0.1 (loopback connect fails fast; no filtered-target hang).

---

## [PLANNER r2] FOLD ROUND 2 — council REJECT (scoped to Phase 2) + three binding leader rulings — 2026-11-02

**Fold mode:** additive (Mode 2). Every change below is marked `[PLANNER r2]`; **no existing task, pin, decision, OQ, T-number, P-number or section number is renumbered or deleted**. New ids are appended (`AM-P2-10`..`AM-P2-23`, `T14`/`T15` task-table rows, `P-13`..`P-34`, `S-11`..`S-13`, `R-13`..`R-17`).

**Council verdict (2/2 converged):** **REJECT — scoped to Phase 2** (Phases 1 + 3 approvable with folds).
**REJECT basis:** `AM-P2-5`'s ceiling-eligibility predicate fired the defer-ceiling **only** on a positive-unhealthy observation or a `rollback_requested` disposition, **never** on an absent/port-down observation. In the canonical never-green shape (the daemon never serves; **every** launcher observation is absent) **nothing rolled back at all** — a regression against today's unconditional rollback, and a violation of the ratified COMPOSITION REQUIREMENT (*"never-green MUST still roll back a broken release"*). Four-plus plan texts claimed "never-green stays armed" while the mechanism no longer armed it.

**Three binding leader rulings folded (verbatim in substance):**

| Ruling | Disposition |
|---|---|
| **OQ-1 — absent-observation disposition** | **(a) BOUNDED ABSENT-CEILING.** Rollback becomes eligible after **≥3 absent observations AND owner-dead AND intent_flip AND never-green-observed**. Option (b) (any-absent ⇒ rollback) is **REFUSED** — it would rewrite an owner-ratified principle; the standing COMPOSITION REQUIREMENT decides. ⇒ **AM-P2-10** below is the single normative absent-only disposition for all five plan docs. |
| **OQ-2 — canonical GREEN** | The launcher probes **`/readyz` as well** (a second GET over the same `/dev/tcp` fd) and **wrong-version fails fast**. Daemon side = **version AND `composite.ready`**. The user ruling's own vocabulary names *"unhealthy livez/readyz post-flip"*. ⇒ **AM-P2-11**; the predicate is placed **verbatim** in `AM-P2-4` and in **T14 step 1**. |
| **OQ-3 — cap interaction** | **Document-only; no exclusion mechanism.** Commit-closes **never** increment the rollback cap (they are not rollbacks); the cap stays frozen; a breach = **halt-for-human by design**. Documented explicitly, noting tonight's wrongful rollback consumed window budget (live at **2/3**). ⇒ **AM-P2-23**. |

**The five reconciled locations for the ONE normative absent-only disposition** (cross-referenced from the other four docs):
`phase2-plan.md` **§D.2 canonical ladder** (rung 3 → AM-P2-10) · `phase2-plan.md` **matrix row 4** · `phase2-plan.md` **§(c) Never-green crash-loop** · `decisions.md` **§4.5** · `decisions.md` **OQ-C-06(d)** · `architecture-recommendation.md` **§6(iii)/(iv)**.

---

### AM-P2-10 🔴 — [PLANNER r2] THE BOUNDED ABSENT-CEILING — the one normative absent-only disposition (leader ruling OQ-1(a))

> **This amendment supersedes AM-P2-5's "Ceiling eligibility" bullet.** AM-P2-5's bullet (restated at the end of this amendment for audit) said the ceiling fires ONLY on a positive-unhealthy observation or a `rollback_requested` disposition, NEVER on an absent/port-down observation. That is the council's REJECT basis. The bullet is **AMENDED** as follows; every other element of AM-P2-5 (counter re-home, single history extraction per boot, launcher-side journal size guard, the `≤ CEILING−1` pin) is **RETAINED unchanged**.

**Ruling (binding, folded).** Absent-observation disposition = **(a) BOUNDED ABSENT-CEILING**: rollback becomes eligible after **≥3 absent observations AND owner-dead AND intent_flip AND never-green-observed**. Option (b) — *any* absent observation ⇒ rollback — is **REFUSED**: it would rewrite an owner-ratified principle (*"Don't roll back if that isn't needed — when the new version is working and living, there is no reason to roll back"*), and the standing COMPOSITION REQUIREMENT decides the question, not this plan.

#### AM-P2-10.1 — The launcher observation taxonomy (the classification every disposition reads)

The taxonomy is the load-bearing input to the ladder. It is **new** (the plan previously had a two-way GREEN / NOT-GREEN split) and it exists because three distinct evidential situations were being collapsed into one:

| Class | Definition | Evidence strength |
|---|---|---|
| **GREEN** | `/livez` **200** ∧ `version` == the **v-stripped** target ∧ `/readyz` **200** ∧ body `ready` (canonical predicate — **AM-P2-11**) | affirmative health |
| **BROKEN** | `/livez` **answered** with a **non-200** status, or with an unparseable body | affirmative **unhealthy** — the daemon is up and its zero-DB liveness endpoint is broken (`/livez` is 200 from the moment the app serves; a non-200 there is a genuinely broken release) |
| **AMBIGUOUS** | everything else that is not GREEN: **no answer at all** (no listener / connection refused / no response within the probe budget) · `/livez` 200 but `version` ≠ target (the old daemon still bound, or the flip's binary never took effect) · `/livez` 200 + right version but `/readyz` not 200 / not `ready` (the boot window, MCP warmup, auto-continue resume) | **ambiguous by construction** — the launcher cannot distinguish "this boot is starting the daemon" from "the target is broken" |

**Why `/readyz`-not-ready is AMBIGUOUS and not BROKEN.** The whole reason the defer path exists is the boot window: the daemon binds and answers `/livez` while `/readyz` is legitimately degraded (MCP warmup + auto-continue — tonight's measured ~3 min unit-active→serving). Treating a `/readyz` non-200 as BROKEN would roll back **every** release that boots slowly — the exact class of wrongful rollback this lane exists to close. `/livez` non-200 carries no such ambiguity (`/livez` is zero-DB, `daemon/api.py:5436-5450`).

**Why a version mismatch is AMBIGUOUS and not BROKEN.** R-9 (*"the never-green path is weakened by an over-eager GREEN verdict — e.g. the probe sees a stale daemon still bound to the port"*) is the mirror risk: the launcher↔binary skew (documented at `launcher.sh:1200-1208`) and the flip→restart window both present as "a listener answering the wrong version". Rolling back on the **first** wrong-version answer would repeat tonight's punishment. ⇒ the mismatch is counted and bounded like any other ambiguity.

**⇒ Consequence for the canonical never-green shape (the council's REJECT basis, now closed):** in that shape the daemon either never answers (**ABSENT**), answers the **previous** version (**VERSION-MISMATCH**), or answers the target but **not ready** (**UNREADY**) — **all three are AMBIGUOUS**. The bounded ceiling covers all three. (If the ceiling covered only the ABSENT class, the shape would still not roll back whenever the daemon answers at all — the council's finding would survive in a narrower form.)

#### AM-P2-10.2 — The counter mechanics (inside AM-P2-5's re-homed launcher-local file)

Counter home (AM-P2-5, unchanged): **`<INSTALL_DIR>/.sweep-defer-<run_id>`** — a sibling of `.launcher-state`, O(1), survives launcher restarts, no journal schema change, not in `releases/` (release-machinery-scanned). Lifecycle: **AM-P2-20**.

File content (line-oriented `key=value`, one field per line; the file is written atomically — write-to-temp + `mv`):

```
absent=<N>          # qualifying ABSENT observations        (the leader's class)
mismatch=<N>        # qualifying VERSION-MISMATCH observations (sibling ambiguity class)
unready=<N>         # qualifying READYZ-NOT-READY observations (sibling ambiguity class)
broken=<N>          # BROKEN observations (audit only; the immediate path does not wait on a count)
green=<0|1>         # STICKY: 1 the first time ANY decision-maker observes GREEN for this txn
```

**Qualifying** = the observation was counted **only** when the full absent-class predicate held **at the instant of the observation**: `owner-dead` (AM-P2-2) ∧ `intent_flip` (AM-P2-4) ∧ `never-green-observed` (AM-P2-10.3). A non-qualifying observation is journaled but **not counted** — the count is a count of *decisive* absences, not of wall-clock time.

**The ceiling predicate (normative):**

```
rollback_eligible_by_absence  ⇔
        (absent + mismatch + unready) >= SWEEP_DEFER_CEILING      # default 3
    AND owner_dead
    AND intent_flip_present
    AND never_green_observed
```

`SWEEP_DEFER_CEILING` remains the single knob, in the `:=` form (`: "${SWEEP_DEFER_CEILING:=3}"`) so the pins can drive it (D.6). **Its meaning is now precisely: the number of qualifying AMBIGUOUS observations after which the absent-class rollback becomes eligible.** ⇒ at most `CEILING − 1` deferrals precede the absent-class rollback (AM-P2-5's `≤ CEILING−1` doctrine and pin **P-20** are retained and re-expressed against this predicate).

**The leader's predicate, satisfied verbatim:** when the three qualifying observations are all ABSENT, the predicate reads exactly *"≥3 absent observations AND owner-dead AND intent_flip AND never-green-observed"*. The two sibling ambiguity classes are admitted into the same tally because the canonical never-green shape may manifest as either — **documented extension, disclosed here for the approver to tighten** (strict reading: only `absent` counts; the shape then rolls back only when the daemon is silent). The leader's ruling is **not weakened**: the bound, the owner-dead conjunct, the intent_flip conjunct and the never-green-observed conjunct are all unchanged.

#### AM-P2-10.3 — `never-green-observed`: the exact, checkable predicate

> **never-green-observed** ⇔ **no GREEN observation for this txn has EVER been recorded, by any decision-maker.**

Checkable as the conjunction of exactly two surfaces — **no new writer and no new reader are required**:

1. **the launcher's sticky `green=0` flag** in `.sweep-defer-<run_id>` — set to `1` the first time the launcher's own probe returns GREEN, **including** the GREEN-but-no-`intent_flip` case (which DEFERs rather than committing, §D.2 rung 1). Once `green=1` the counter is **frozen** and the absent-ceiling is permanently ineligible for this txn.
2. **the journal history carries no `serving_green_commit` and no `quarantine_cleared_serving_green` row naming this target.**

**Why (2) is load-bearing, not belt-and-braces.** AM-P2-1's history-before-close order writes `serving_green_commit` **before** `in_flight` is cleared. A crash between those two writes leaves the event in the history with the txn still open — a release that **was** serving green, which a later absent-ceiling would otherwise roll back. The journal check closes exactly that window. (The same is true of `quarantine_cleared_serving_green`, which AM-P2-1 writes *first* when the target ∈ `quarantined[]`.)

**Why the daemon side needs no separate marker.** The daemon-side closer observes GREEN ⇒ it commits green (AM-P2-4's canonical predicate requires `intent_flip`). If the daemon observed GREEN but could **not** commit, the reason is a missing `intent_flip` — and the absent-ceiling's `intent_flip` conjunct **also** fails. **The two predicates are mutually consistent by construction**: there is no state in which the daemon has seen green and the absent-ceiling is still eligible. (Recorded here so an implementer does not add a redundant daemon-side marker event.)

#### AM-P2-10.4 — The T15 hook's own observation: eligibility, stated explicitly and pinned

The run-loop re-sweep hook (T15, AM-P2-7) fires after each child exit. Its own observation is *"the child just exited ⇒ the port is absent"* — a consequence of the exit, **not** an independent observation of the target's serving state.

- The hook's observation **MAY** be counted as a qualifying ABSENT observation **iff the full predicate holds at that instant** (`owner-dead` ∧ `intent_flip` ∧ `never-green-observed`), **and** the child exit was **not** a **tempfail-class (exit 75)** exit.
- **Why the tempfail exclusion is load-bearing:** the house burst budget **deliberately exempts exit 75** (`launcher.sh:1412-1414`) because a persistent tempfail is normally *"PG unreachable?"* — an **environmental** failure, not a release failure. Converting a PG outage into a release rollback would reproduce tonight's punishment class (a healthy release rolled back and quarantined, one rollback from arming the 24 h promote-halt cap at live 2/3). ⇒ a tempfail exit does **not** count. The tempfail shape remains the leader-adjudicated **operator-bounded residual** (AM-P2-7 + §4.5) — **disclosed, not closed by this lane**.
- The hook's observation is **never** used to infer rollback-eligibility in any way beyond the ruled predicate. The hook's job is to **advance EXECUTION** of an already-journaled `rollback_requested` (path 2 below), which needs no ceiling at all.
- **Pin:** a tempfail-class child exit leaves the counter **unchanged**; a crash-class child exit increments it when the predicate holds; the hook is **idempotent** (see AM-P2-17).

#### AM-P2-10.5 — The three rollback-eligibility paths (the complete, closed set — there is no fourth)

| # | Path | Trigger | Speed | Gate |
|---|---|---|---|---|
| **1** | **BROKEN** (positive-unhealthy) | the launcher obtained an HTTP answer and the answer was wrong: **`/livez` non-200 / unparseable body** | **immediate** — the existing chain fires on that observation | the count is **waived**: the evidence is unambiguous |
| **2** | **`rollback_requested`** | the daemon wrote the T14 request event for this `(target, run_id)` | **immediate** (pre-empts the ceiling) | the launcher **re-probes** (AM-P2-6); the daemon's own post-grace verdict is the affirmative evidence |
| **3** | **absent-ceiling** | **AMBIGUOUS** observation ∧ the full ceiling predicate holds | **bounded** — fires on the observation that takes the tally to `SWEEP_DEFER_CEILING` | owner-dead ∧ intent_flip ∧ never-green-observed |

**Everything else DEFERS** (journaled `sweep_deferred`, txn left open). In particular: a single ambiguous observation defers; an ambiguous observation with a live owner defers; an ambiguous observation with no `intent_flip` defers; an ambiguous observation after **any** green observation for this txn defers.

**[PLANNER r3 → AM-P2-24] Path 2's INPUT is constrained; path 2's GATE is unchanged.** The r3 fold does **not** add a conjunct to path 2 and does **not** re-check `never-green-observed` on the launcher side. It constrains the **writer**: the daemon-side closer may not write `rollback_requested` at all while a grace is held (`grace_active == True`) ⇒ **path 2 can no longer be entered with a warmup-induced request**. The r2 bypass (path 2's only gate = the re-probe) is therefore closed **at the writer**, and the path set stays exactly the three paths above — **there is still no fourth path**.

**Reconciliation with D.6's "Exact firing rule"** (disclosed interpretation): D.6 stated an encounter-counted rule (`prior >= CEILING−1 ⇒ execute`) applied to *all* encounters; AM-P2-5 then restricted *which* observations could fire it. The r2 fold resolves the two into one coherent trigger, in favour of the owner-ratified principle (*rollback is reserved EXCLUSIVELY for genuinely unhealthy serving*):

- the **BROKEN** class is bound by its **evidence**, not by a count (path 1 fires immediately);
- the **AMBIGUOUS** classes are bound by the **count** plus three conjuncts (path 3);
- the **request** path is bound by the daemon's affirmative verdict plus the launcher's re-probe (path 2).

**The never-green path is therefore STILL ARMED** — it is armed by path 1 (immediately) and by path 3 (boundedly), which is exactly what the composition requirement demands, and it is *no longer* a regression against today's unconditional rollback.

#### AM-P2-10.6 — AM-P2-5's superseded bullet, restated for audit (not deleted)

> **AM-P2-5 (superseded in this one clause by AM-P2-10):** *"Ceiling eligibility (owner-ruling alignment): the ceiling fires ONLY on a positive unhealthy observation (answered-but-wrong: non-200, version mismatch) or a `rollback_requested` disposition — NEVER on an absent/port-down observation … Pure-absent sequences defer to the operator (AM-P2-7 runbook)."*
>
> **→ AMENDED:** the absent/port-down observation is **no longer excluded** from the rollback-eligibility set; it is admitted under the **bounded absent-ceiling** of AM-P2-10 (≥3 qualifying observations ∧ owner-dead ∧ intent_flip ∧ never-green-observed). The "pure-absent sequences defer to the operator" sentence survives **only** for the two shapes that produce no further launcher boot at all (tempfail-75 / clean-exit-0), which remain the leader-adjudicated operator-bounded residual (AM-P2-7, §4.5).

---

### AM-P2-11 🔴 — [PLANNER r2] OQ-2: the CANONICAL GREEN predicate (launcher probes `/readyz` too; wrong-version fails fast)

**Ruling (binding, folded).** The canonical GREEN predicate is:

> **GREEN** ⇔ the launcher obtained **`/livez` 200** ∧ the reported `version` equals the **v-stripped** target ∧ the launcher obtained **`/readyz` 200** whose body reports ready. The `/readyz` GET is a **second request over the same `/dev/tcp` fd** (no second connect, no second port race). A **wrong version fails fast** — it is not a "keep polling" condition.
> **Daemon side:** GREEN ⇔ `__version__` == the v-stripped target **AND** the cached readiness composite is not `None` **AND** `composite.ready` is True.

**[PLANNER r3 → REV-R-4] "fails fast" is PROBE-level vocabulary — it is NOT a disposition.** "Fails fast" describes how the probe **stops probing**; it says nothing about what the **closer** does with the observation. A **version-mismatch DISPOSITION is AMBIGUOUS-tallied per AM-P2-10.1, never BROKEN-immediate** — it is counted toward the bounded absent-ceiling and the chain fires only when the ceiling predicate holds (cross-ref **`P-27`**, the version-mismatch split: symlink-on-target + wrong version ⇒ counted and bounded; symlink-moved-away ⇒ **NO ACTION**). **Only** a `/livez` **non-200** / unparseable answer is BROKEN ⇒ immediate. An implementer who reads "wrong version fails fast" as "wrong version ⇒ roll back now" would reproduce tonight's punishment class in the launcher leg.

**[PLANNER r3 → REV-R-5]** the duplicated `∧ AND` token is dropped from the canonical predicate above (the same correction is applied at **T14 step 1** — the two mandated implementer-facing sites — and at **AM-P2-4**, the third copy of the same predicate).

**Why `/readyz` joins `/livez` (the mechanism-level finding that forced this ruling).** `tests/helpers/soak_gate_fake_daemon.py` answers **`/livez` 200 in every mode** — verified in-tree at this fold: the `/livez` branch of the handler is unconditional (the mode only degrades `/readyz`; `:289-303` handles `/livez`, `:20-21` documents the modes) — and `hard-red` mode is specifically *"/livez 200, /readyz 500"*. ⇒ **a launcher probe that reads only `/livez` classifies `hard-red` as GREEN.** The direction-② pin (the composition requirement's *"serving-unhealthy ⇒ the existing chain still fires"*) would then be pinned against a fixture that the launcher considers healthy — the pin would prove nothing, or would fail for the wrong reason. ⇒ the canonical GREEN **must** include `/readyz`. (Also: the fixture's `version-aware` mode answers `/livez` 200 with the *serving* version resolved from `$INSTALL_DIR/current` → manifest `binary_version` — so a version-mismatch fixture is available from the same helper.)

**The predicate, placed verbatim in the two mandated locations:**
- **AM-P2-4** (canonical predicate text) — amended below to carry this exact wording.
- **T14 step 1** — the new task-table row for T14 states it verbatim in step 1.

**Adopt coding's fixture recipe regardless** (the leader's instruction): the fixture drives the real launcher probe through the real `/dev/tcp` path, so it is robust under either GREEN definition — the pin is discriminating whether the implementation probes `/livez` only or `/livez` + `/readyz`.

---

### AM-P2-12 🔴 — [PLANNER r2] Direction-② fixtures: the `hard-red`-is-launcher-GREEN finding, the new listener fixture, and the counter-persistence requirement

**Finding (verified in-tree at this fold).** `tests/helpers/soak_gate_fake_daemon.py` answers `/livez` **200** in every mode; `hard-red` degrades **only** `/readyz` (`/livez` 200 + `/readyz` 500). ⇒ **against the pre-ruling launcher probe (livez-only) `hard-red` is GREEN.** The direction-② pin (P3-PIN-06 / Phase 2 `P-2`) would therefore not be discriminating against a livez-only implementation.

**Consequences, folded as requirements:**

1. **AM-P2-11's canonical GREEN** (livez ∧ readyz) is what restores the direction-② pin's discriminating power: with the canonical predicate, `hard-red` ⇒ `/livez` 200 ∧ `/readyz` 500 ⇒ **not GREEN** ⇒ BROKEN-or-AMBIGUOUS per AM-P2-10.1. The pin then genuinely separates "the launcher honours the ruling" from "the launcher treats any 200 as green".
2. **NEW fixture required** — `tests/helpers/serving_probe_fake_listener.py` (minimal, **synthesised**, binding 127.0.0.1 only, JSONL request log for non-vacuity assertions, never resolving a live-shaped path). Modes required by this lane:
   | Mode | Behaviour | Classification (AM-P2-10.1) | Why it is needed |
   |---|---|---|---|
   | `wrong-version` | `/livez` **200** with `version` ≠ the target | **AMBIGUOUS (mismatch)** | the existing helper's `version-aware` mode answers `/livez` 200 with the *resolved serving* version; the lane needs a listener that answers 200 with a **different** version to pin "200 + wrong version ⇒ NOT green" and to pin the **mismatch ⇒ bounded, not immediate** classification |
   | `livez-non200` | `/livez` answers **500** (or 503) | **BROKEN** | pins the *immediate* path (path 1) and proves a non-200 is not treated as absent |
   | `never-answer` | accepts the connection, never answers | **AMBIGUOUS (absent)** | pins "no answer ⇒ absent, never BROKEN" and drives the ceiling |
   | `readyz-red` | `/livez` 200 + `/readyz` 500 | **AMBIGUOUS (unready)** | the canonical never-green shape's in-daemon variant |
   The existing `soak_gate_fake_daemon.py` remains in use for `green` (and for `hard-red` **as the canonical-GREEN discriminator**, AM-P2-11) — the lane does **not** build a second fake daemon for shapes it already models; the new helper adds only the shapes it does not.
3. **The counter must persist across `run_sweep` invocations** — the ceiling is only reachable if the tally survives between sweep runs. The launcher-local file (AM-P2-5's home, AM-P2-10.2) provides this by construction; the pin **must drive it across ≥3 separate `run_sweep` invocations** (not three observations inside one invocation) to prove the persistence, and must assert the **four rollback effects** on the firing encounter: `current`→`prev`, `quarantined[]` append, `rollback_window_count` ++, `cooldown_until` armed (the same four P3-PIN-06 asserts).
4. **The T14-driven pin** (seeded `rollback_requested` + `hard-red`) ⇒ **single-boot deterministic rollback** — the request path does not wait for a ceiling.

---

### AM-P2-13 🔴 — [PLANNER r2] Daemon-closer journal idempotence (write-once per `(run_id, target)`) + the R-12 size bound

**The defect this closes.** The daemon-side closer runs **every 90 s** (`upgrade_journal_sweep.py:96-99`) and the janitor contract is *never raises* — a failed tick logs and the next tick retries (D.1's error contract). If the closer appended a `serving_unhealthy` verdict and/or a `rollback_requested` request on **every** tick while the txn stayed open, the journal would grow **one row per tick** for the whole stale window — the R-12 growth hazard, amplified by the 90 s cadence and invisible to a reviewer who reads "one unhealthy verdict" as "one observation".

**Required behaviour (normative):**

- **Write-once per `(run_id, target)`** for the daemon-side verdict/request events. The closer **scans the parsed history in Python** (the daemon already parses the journal in-process — no `_js_json_sub` cost) and appends the row **only if absent**. ⇒ no per-tick `serving_unhealthy` / `rollback_requested` growth.
- The write-once scan is over the **parsed history array**, matching on `(event, target, run_id)` — **not** a substring conjunction over the raw document (the same cross-entry hazard the launcher reader must avoid, AM-P2-14).
- **The idempotence key survives the txn's closure**: a re-armed txn with the same `run_id` (a re-promote of the same version after a rollback) gets a **fresh** event — the key is `(run_id, target)`, and the launcher/daemon write the request at most once per key.

**R-12 (journal growth) is amended accordingly:** the size bound now covers **daemon-side events**, not only launcher deferral rows. The bound is: **≤ 1 `serving_unhealthy` + ≤ 1 `rollback_requested` + ≤ 1 `serving_green_commit` + ≤ 1 `quarantine_cleared_serving_green` per `(run_id, target)`**, plus ≤ `SWEEP_DEFER_CEILING` launcher `sweep_deferred` rows per txn (AM-P2-10.2's ceiling bounds it).

**JOURNAL_SIZE_GUARD_BYTES interaction (re-checked, per the finding).** The guard (`JOURNAL_SIZE_GUARD_BYTES=98304`, `lib.sh:1376`) is **lib.sh-only today** — `launcher.sh` has zero occurrences of it (AM-P2-5's launcher-side size guard is the launcher's copy of it). With the daemon closer writing at most **four** rows per txn, the daemon's contribution to journal growth is **bounded and small** ⇒ the guard's interaction is: a daemon-closer row never pushes a journal over the guard on its own, and the launcher's size guard (AM-P2-5) remains the protection for the launcher-side path. **Recorded**: the guard is a whole-journal bound, so a launcher deferral row and a daemon verdict row compete for the same budget — the composition is bounded (≤ CEILING + 4 rows per txn), and no new guard interaction is introduced by this fold. **Named follow-up (not this lane):** a shared size-guard accounting across the two writers.

---

### AM-P2-14 🔴 — [PLANNER r2] T14 request reading: **per-entry** matching (the cross-entry false-positive hazard) + the R3 hinge

**The defect this closes.** The existing launcher history reader `_js_history_has_intent_flip` (`launcher.sh:518-536`) matches a **whole-array conjunction**:

```
pattern='*"event":"intent_flip"*target '"$target"'*'
```

That shape matches *"event-kind in entry A"* ∧ *"target string in entry B"* — a **cross-entry false positive**. AM-P2-5 already recorded the same hazard for the deferral counter (and **inverted** the "over-count is safe" doctrine because the counter gates the rollback itself). The **T14 request reader must not inherit that shape**.

**Required reader contract (normative):**

- **Per-entry matching.** The reader splits the history array into entries (the launcher already has a JSON array splitter? — if not, split on the entry-object boundary `{"ts":` and require **both** substrings to appear inside **ONE** entry). ⇒ a request event in entry A and the target string in entry B **does not match**.
- **Both literals must be in the same entry**: the event kind (`rollback_requested`), the `target <VER>` substring, and the `run_id <RUN_ID>` substring.
- **The request may live in the launcher-local file instead** — an admissible alternative the architect may choose; if the file is used, the launcher writes it and the daemon never touches it (the daemon would lose the ability to request a rollback, so the **event** is the default; the file variant is admissible only if the daemon's request path is re-specified). **Decision: the event is the contract** (AM-P2-6 stands); the file variant is recorded as an admissible alternative, not adopted.
- **Absent / absent-field / torn ⇒ "no disposition"** — never a spurious rollback (AM-P2-6's degradation principle, restated).

**PIN (named):** the **cross-entry negative** — a fixture whose history contains `{"event":"rollback_requested", ...}` in one entry and the target string in a **different** entry ⇒ the reader must return "no request" ⇒ **no rollback of a serving-green release**. This is a *behavioural* pin (not a grep): it drives the sweep and asserts the txn is still open and no `sweep_rollback` row exists.

**Named follow-up (recorded, NOT fixed in this lane):** the pre-existing `_js_history_has_intent_flip` has the **same** whole-array conjunction shape. Its failure direction is the mirror of T14's: a cross-entry false positive there can make an **unauthorized** flip look authorized (it feeds the `intent_flip` conjunct of the canonical predicate). Tightening it is a **separate, small hygiene commission** — it is **not** silently changed here, because the canonical predicate's `intent_flip` conjunct is a *gate on committing*, and altering a shipped reader's semantics in a release that also changes the disposition table would blur the blame line. **Recorded as a risk (R-15) and routed.**

---

### AM-P2-15 🔴 — [PLANNER r2] R3 hinge: the request path's GREEN outcome is itself gated on `intent_flip`

**The hole this closes.** AM-P2-6's position-0 handling said: *"request present ⇒ re-probe: GREEN ⇒ commit-close (ruling precedence: serving-green = stay)"* — with **no `intent_flip` conjunct**. ⇒ a **forged** `rollback_requested` event plus a **green** probe could commit-close an **unauthorized** flip. That is the R3 hinge: **a forged `rollback_requested` + a green probe must never commit-close an unauthorized flip.**

**Corrected request-path decision table (normative — replaces AM-P2-6's step-1 wording):**

| Request present for `(target, run_id)`? | Re-probe verdict | `intent_flip` present? | Disposition |
|---|---|---|---|
| yes | **GREEN** | **yes** | **commit-close** (`serving_green_commit`); the request is journaled as **superseded** and is **NOT executed** (the ruling's precedence: serving-green = stay) |
| yes | **GREEN** | **no** | **DEFER + journal** — *not* a commit-close, *not* a rollback. The txn stays open; the row records that a request was seen but not honoured. **This is the R3 hinge.** |
| yes | **BROKEN** | any | execute the **existing** auto-rollback chain byte-identically (`launcher.sh:1197-1230`), detail `disposition=rollback_requested` |
| yes | **AMBIGUOUS** | any | the daemon's post-grace verdict is the affirmative evidence ⇒ execute the **existing** chain (path 2 of AM-P2-10.5); the re-probe confirms "not green" |
| no | — | — | the ordinary ladder (AM-P2-10.5) |

**P-15's negative axis (added, per the finding).** P-15 today proves *"request + GREEN ⇒ commit-close, request NOT executed"*. It gains a **negative axis**: *"request + GREEN + **no** `intent_flip` ⇒ **defer**, txn still open, no `serving_green_commit`, no `sweep_rollback`"* — the R3 hinge, mutation-proven (plant: drop the `intent_flip` conjunct from the request path ⇒ the pin goes RED).

**And the canonical predicate's GREEN arm is amended** — see AM-P2-4's amended text below: *"a serving-green commit-close fires only when BOTH hold: (i) a positive serving verdict for the txn's target … AND (ii) the journal history contains an `intent_flip` event naming that same target. Absent (ii) ⇒ launcher DEFER / daemon no-action-this-tick / adopt existing-chain — never commit, never immediate rollback."* **That wording is unchanged; the request path is now brought under it** (previously the request path bypassed clause (ii)).

---

### AM-P2-16 🔴 — [PLANNER r2] The version-mismatch split (symlink state decides action vs no-action)

**The split (normative):**

| Symlink state | Listener's `/livez` version | Disposition |
|---|---|---|
| **`current` → `releases/<target>`** (the flip is in effect) | ≠ the target | **AMBIGUOUS (mismatch)** ⇒ counted toward the bounded ceiling (AM-P2-10) — *not* an immediate rollback (R-9 skew hazard) and *not* GREEN (R-9's mirror). The existing chain fires when the ceiling predicate is met. |
| **`current` → `releases/<prev>`** or anywhere else (the symlink has **moved away** from the target) | any | **NO ACTION** — the txn was **already dispositioned** (the flip was undone by a previous disposition). **No quarantine, no counter, no cooldown, no `sweep_rollback` row.** |

**Why "symlink-moved-away ⇒ NO ACTION" is load-bearing.** The benign launcher↔binary skew (`launcher.sh:1200-1208`) and any already-dispositioned txn present as "the listener's version does not match the target". Treating that as a rollback would **quarantine a healthy release** — tonight's exact punishment shape. ⇒ the symlink state is the discriminator: *is the flip still in effect?* If not, there is nothing to roll back **to** and nothing to punish.

**The positive side.** When the flip **is** in effect (`current` → the target) but the listener answers a different version, the target is **not serving** — that is a genuine never-green manifestation and it must terminate in a rollback. It terminates through the **bounded** ceiling (AMBIGUOUS), not immediately, because the flip→restart window presents identically (the old daemon is still bound while the symlink already points at the target).

**Pin (named):** the version-mismatch split — (a) symlink-on-target + wrong version ⇒ counted, bounded, and the ceiling eventually fires the existing chain; (b) symlink-moved-away + wrong version ⇒ **NO ACTION** (no quarantine, no counter, no `sweep_rollback`).

---

### AM-P2-17 🔴 — [PLANNER r2] T15's scope: a narrow helper, the reachable branches enumerated, and idempotence

**Scope decision.** T15 re-runs the disposition logic after each child exit. It **must not** re-run the *whole* `_journal_sweep`, because two of that function's branches are wrong in a mid-run-loop context:

| `_journal_sweep` branch | In the T15 hook? | Why |
|---|---|---|
| the **stale pre-flip clear** (`launcher.sh:1244-1252`) — clears a txn that never flipped | **OUT** | a promote that is *in flight and has not yet flipped* would have its txn **cleared out from under it** by the hook — destroying a live promote. The hook runs mid-run-loop, exactly when a promote may be in flight. |
| the **fresh-txn** branch (age ≤ `SWEEP_STALE_S`) | OUT (by construction) | the hook's own txn is the stale one; the freshness gate still applies |
| the **flipped-branch disposition** (gates → request → probe → commit / defer / ceiling / rollback) | **IN** | this is the disposition the hook exists to advance |
| the **lock acquire/release** + **revalidate-under-lock** | IN (mandatory) | the hook is a second writer; the lock discipline (AM-P2-3) is not optional here |

**Shape.** A **narrow helper** — e.g. `_js_sweep_flipped_disposition <install_dir>` — holding the flipped-branch disposition, called by **both** `_journal_sweep` (the launcher-boot path) and the T15 hook. ⇒ **one** disposition implementation, no divergence between the two callers. The helper is a **new function**; the 8b-r1 source pin (`tests/test_launcher.sh:831-848`) extracts `^_journal_sweep\(\)` ⇒ the helper is **outside** that pin's scope and the pin's body is left untouched (the helper is called *from* `_journal_sweep`, so `_journal_sweep`'s body gains one call — the pin forbids `wait` against subprocess pids and pipe reads inside the extracted body; a plain function call is neither ⇒ **the pin must still be re-verified** in the Tier-1 leg, per Phase 3's harness note).

**Idempotence (pinned).** Calling the helper twice against the same state is a no-op the second time: after a commit-close or a rollback the txn is closed (`in_flight` null / `current` = prev) ⇒ the helper finds no stale flipped txn ⇒ no action. The **counter file** is deleted on commit/rollback (AM-P2-20) ⇒ a re-run cannot inherit a stale tally.

**Pre-flip clear: explicitly OUT of the hook's scope** — stated here so no implementer widens the helper to "just call `_journal_sweep`".

---

### AM-P2-18 🔴 — [PLANNER r2] The owner-liveness conjunct applies to the **whole** stale path (including the pre-flip clear branch)

AM-P2-2 added the owner-liveness conjunct (*owner-dead or heartbeat-stale*) to the launcher stale path's **serving-green** and **rollback** branches. **This amendment extends it to the remaining branch of that path — the stale pre-flip clear (`launcher.sh:1244-1252`)** — rather than leaving an explicit exemption, because:

- the pre-flip clear branch **destroys** a txn: it sets `in_flight` → null and appends a `sweep` history row. If the owner is still alive (a slow-but-live promote that has not flipped yet — the 900 s ceremony cap exceeds the 600 s staleness gate, AM-P2-2's premise), the clear **kills that promote's txn** ⇒ the flip would later find no open txn ⇒ the promote's own bookkeeping breaks.
- ⇒ **the owner-liveness conjunct is a precondition of EVERY stale-path branch**: pre-flip clear, flipped-defer, flipped-rollback, flipped-commit. One predicate, four call sites (AM-P2-2's "one predicate" intent, completed).

**Recorded consequence (disclosed):** a stale pre-flip txn whose owner is alive is now **left open** rather than cleared ⇒ it survives to the next sweep. That is the safe direction (a live promote is never disowned), and the operator/runbook already owns the "txn left open" shape (AM-P2-7's terminal backstop).

---

### AM-P2-19 🟡 — [PLANNER r2] The alert-kind split: a REQUEST must never page "auto-rollback happened"

**The hazard.** `ALERT_KIND_BY_EVENT` is an **exact dict lookup** (`daemon/tools/upgrade_journal.py:643-651`; lookup at `:758`) ⇒ a new event kind cannot collide with `upgrade_auto_rollback`. The real hazard is the **wrong kind**: AM-P2-8 recommended mapping `serving_unhealthy` → `upgrade_auto_rollback`. But the daemon's `serving_unhealthy` verdict / `rollback_requested` request is a **REQUEST**, not an execution — and under AM-P2-6 + AM-P2-15 a request **may be superseded** (the launcher re-probes, finds GREEN, and commit-closes instead). **Paging "auto-rollback happened" on a possibly-superseded verdict is a false page** — the operator would see a rollback alert for a release that stayed up.

**Required mapping (normative):**

| Event | Alert kind | Rationale |
|---|---|---|
| `serving_unhealthy` (daemon verdict) | **`upgrade_serving_unhealthy_requested`** (NEW) | request stage — the daemon has judged, the launcher has not acted |
| `rollback_requested` (T14 request) | **`upgrade_serving_unhealthy_requested`** (NEW) | request stage — same |
| `sweep_rollback` (the **executed** rollback) | `upgrade_auto_rollback` (**unchanged, pre-existing mapping**) | execution stage — this is the only event that means "a rollback happened" |
| `quarantine` (the quarantine append) | `upgrade_auto_rollback` (**unchanged**) | execution stage |
| `serving_green_commit` | none (silent) | a good outcome is silent (the house policy, `upgrade_journal.py:590-596`) |
| `quarantine_cleared_serving_green` | **NEW kind** (AM-P2-8) | the verdict was **reversed** — the operator must learn that |
| `sweep_deferred` | none | a hold, not an outcome |

The sink takes arbitrary kinds (`upgrade_journal.py:643-651` — the map is a plain `dict[str, str]`), so a new kind needs no consumer change; the **operator's** alert routing (whatever consumes `upgrade_auto_rollback`) is the surface that must learn the new kind — **named in the runbook** (AM-P2-23).

---

### AM-P2-20 🟡 — [PLANNER r2] The `.sweep-defer-<run_id>` counter file: lifecycle + filename shape

**Lifecycle (normative):**

| Event | Counter file |
|---|---|
| a qualifying observation is counted | the file is created/updated (atomic write-to-temp + `mv`) |
| the txn **commits** (serving-green commit-close) | **deleted** — the txn is closed; the tally is meaningless |
| the txn **rolls back** (any path) | **deleted** |
| a launcher **boot** with a stale counter file whose `run_id` is **not** the current `in_flight.run_id` | **deleted** (stale sweep) — a counter for a closed or superseded txn must never be inherited |
| the launcher boot sweep finds **no** `in_flight` txn at all | every `.sweep-defer-*` file is stale → deleted |
| the file is **absent** when the ceiling is evaluated | the count is **0** ⇒ the ceiling has not been reached (never a spurious increment) |

**Filename shape (pinned):** `<INSTALL_DIR>/.sweep-defer-<run_id>` — the `run_id` is the txn's `in_flight.run_id` **verbatim** (the identity the daemon already keys on, `upgrade_journal.py:2262-2272`). The `run_id` shape must be **safe as a filename** (no `/`, no whitespace): the implementation **sanitises** it (e.g. `_js_sanitise_run_id`) and the pin asserts both the sanitised shape and that two different txns cannot collide onto one file.

**Why a file and not a journal field (retained from AM-P2-5).** The launcher **cannot create a journal field** (`_js_journal_update` returns 1 when the field literal is absent — `launcher.sh:387-393`) and the launcher sweeps **before** the daemon exists (`launcher.sh:1661` vs `:1525`) ⇒ no daemon-seeded field is reachable. The file is also O(1) — the history-count alternative measured **13.5 s** (`_js_journal_read`) and **35.7 s** (`_js_json_sub history`) on a 27 KB / 157-entry journal, ≈285× at the incident size.

**Not in `releases/`** — `releases/` is release-machinery-scanned; the file lives beside `.launcher-state` in the install root.

---

### AM-P2-21 🟡 — [PLANNER r2] The launcher-purity pin: word-boundary matching + a recorded baseline

**The finding.** Phase 2's purity pin (P-11 / P3-PIN-12) asserts *"zero `curl`/`wget`/`nc`/`lsof`/`python3` **executable** tokens (comment mentions excluded)"*. Verified in-tree at this fold: **`launcher.sh` contains `python3` twice — at `:1040` and `:1052`, BOTH inside the file's own purity comment** (*"has ZERO python3 dependency … DO NOT add a python3 dep here"*). ⇒ a naive whole-file `grep -c python3 launcher.sh` returns **2** today ⇒ the pin as written would be **RED at base**, and a naive word-boundary grep is still wrong because it matches the comment.

**Required pin shape (normative):**

1. **Comment-stripping first** — the pin must scan the **executable** text only (strip `#`-comments, including the in-file purity comment itself) before matching.
2. **Word-boundary matching** — a bare `grep nc` matches inside `sync`, `func`, `encoded`; a bare `grep lsof` matches inside `lsof`-prefixed identifiers. ⇒ match whole tokens only.
3. **A recorded baseline, not an absolute zero.** The pin's assertion is **"no NEW executable token vs the recorded baseline"** — the baseline is written into the pin as a literal list (today: `curl`=0, `wget`=0, `nc`=0, `lsof`=0, `python3`=0 **after comment-stripping**). ⇒ the pin is red on a NEW token and green on the existing tree — it is not a tripwire on a comment.
4. **The baseline is re-measured at implementation time** and the pin records the measured numbers; if the implementation legitimately introduces one of these (it must not — R-08 is a hard constraint), the pin's baseline changes **with a recorded rationale**, never silently.

**Note (kept, not amended):** A1 (`curl` is not admissible in `launcher.sh`) and A2 (`bash /dev/tcp` is) stand unchanged. The `/readyz` second GET (AM-P2-11) is **over the same `/dev/tcp` fd** ⇒ **no new dependency surface** ⇒ R-08 is not touched by OQ-2.

---

### AM-P2-22 🟡 — [PLANNER r2] Anchor reconciliation pass (the remaining drift)

The council confirmed that OUR anchors were right where they corrected the briefing (`lib.sh:2099-2107` uname dispatch; C5 at `:279-302`). The remaining drift is corrected here — **all re-verified in-tree at this fold**:

| # | Plan text said | **Correct anchor (verified)** | Where |
|---|---|---|---|
| 1 | `lib.sh:1375-1385` (the cooldown dispatch) | **`lib.sh:2099-2107`** — the `case "$(uname -s)"` dispatch (`Darwin\|*BSD*` / `Linux\|GNU*` / `*` fail-closed arm) | already fixed in `decisions.md` B-2/B-4 and Phase 3 §4.1.5 — **re-affirmed here** |
| 2 | `lib.sh:3841-3860` (the classified probe) | **`lib.sh:3874`** — `_probe_once_classified` (its classifier comment block `:3870-3873`; the `curl -sS -o tmp -w '%{http_code}'` shape at `:3894`) | Phase 2 §D.4 step 1 and §D.0's table (`lib.sh:3841-3860`) — **AMENDED to `:3874`** |
| 3 | the "house-classifier" citation for Phase 1 | the classifier is `_probe_once_classified` at **`lib.sh:3874`**; the D.4 classifier comment (`:3870-3873`) documents the **busy-amnesty asymmetry-closer** — Phase 1's serving conjunct must reuse the **taxonomy** (`0` green / `1` red / `2` transient) and **not** re-derive a second one | Phase 1 AM-P1-2 + Phase 2 §D.4 — **AMENDED** |
| 4 | `LIVEZ_BUDGET_S` as one anchor | the **note** is at **`lib.sh:126`** and the **declaration** at **`lib.sh:146`** (`LIVEZ_BUDGET_S="$(_numeric_knob LIVEZ_BUDGET_S "${LIVEZ_BUDGET_S:-}" 180)"`) — a note/declaration split; `gate_livez` consumes it at `:5986` and the hand-back verifier borrows it at `:4753` | Phase 1 T-P1-8 / AM-P1-3 — **AMENDED (both anchors named)** |
| 5 | `upgrade_journal_sweep.py:110` for `EXECUTOR_STALENESS_WINDOW_S` | the constant is declared at **`:112`** (already recorded as `decisions.md` B-3) | Phase 2 §D.1 P6 — **re-affirmed** |

**No other anchor drift was found in this pass.** The council's corrections to the *briefing* (`lib.sh:2099-2107`, C5 at `:279-302`) are **confirmed correct** and are not re-litigated here.

---

### AM-P2-23 🟡 — [PLANNER r2] OQ-3 (cap interaction, document-only) + the promote.sh doc-rider + the v0.28.5 note

**OQ-3 — the rollback cap is NOT incremented by a commit-close, and there is NO exclusion mechanism.** Documented, per the ruling:

- A **commit-close** (serving-green) is **not a rollback** ⇒ it **never** increments `rollback_window_count` and **never** arms `cooldown_until`. The cap/cooldown are *"anti-flapping responses to BAD outcomes"* — the house's own words at `launcher.sh:1034-1036`. **No exclusion mechanism exists or is introduced** (there is no "rollback cap exemption" flag, no credit, no decrement) — the cap is simply **frozen** by the fact that a commit-close does not touch it.
- **A cap breach remains a halt-for-human by design** — the 24 h promote-halt is the house's anti-flap guard and this lane does **not** weaken it.
- **Live note (for the ceremony):** tonight's wrongful rollback consumed window budget — the live `rollback_window_count` is **2/3**. A single further wrongful rollback arms the 24 h promote halt. This lane's whole purpose is to stop producing wrongful rollbacks; the disclosure stays visible in the runbook.

**Doc-rider for T13 (`promote.sh:570-576`).** The causal comments in that region assert a sweep outcome this lane **changes**:

```
# B4 leave-txn-open (same policy): current untouched at LKG; the next
# launcher start boots it and the sweep clears this stale pre-flip txn.
_warn "ATOMIC FLIP FAILED — current untouched; aborting (txn left open — the sweep clears it at the next launcher start, …)"
journal_history_append halt "promote aborted: flip failed for $VERSION (current untouched) — txn left open for sweep recovery"
…
journal_mark_flipped || { _warn "cannot mark flipped — halting for human (sweep will find the txn)"; exit 1; }
```

*"the sweep clears it"* / *"sweep recovery"* / *"the sweep will find the txn"* now read as *"the sweep **dispositions** it"* — which after this lane means **defer / commit-close / bounded rollback**, and (per AM-P2-18) the stale pre-flip clear additionally requires owner-dead. ⇒ **T13's doc-rider list** gains these three strings: the comments and the two `_warn`/`journal_history_append` texts must be re-worded to state the *new* sweep semantics (the txn is left open for the sweep to **disposition**, not to be "cleared" unconditionally). **Doc-only; no logic change.**

**The v0.28.5 note (carried into the runbook and the ceremony notes).** `quarantine_cleared_serving_green` **cannot rescue `v0.28.5` itself** — the event fires only while the target **is serving** green, and `v0.28.5` was rolled back off the live symlink (live `current` = `v0.28.4`, `quarantined` = `['v0.28.5']`). The live `v0.28.5` therefore stays quarantined and must be **re-staged** (which is precisely this lane's delivery: **the content re-stages as `v0.28.6`**). The clearance path exists for the *future* wrongful-quarantine shape (a release that is serving green and gets quarantined by a halt), not for the already-rolled-back one. **Recorded so no implementer or operator expects `v0.28.5` to un-quarantine itself.**

---

### AM-P2-24 🔴 — [PLANNER r3] F-1: the daemon-closer's grace gate + the boot-grace horizon (the restored invariant)

**The root — stated explicitly (this is the defect the fold closes).**

Three facts compose into tonight's class, and the r2 plan did not close the third:

1. **The readiness composite is legitimately degraded during warmup.** MCP warmup + auto-continue resume make `/readyz` report *not ready* for minutes after the process starts; the composite is **present** (not `None`) and its `ready` is `False`. That is a **warmup observation**, not an unhealthy serving.
2. **A composite-present-but-degraded observation *after* the grace horizon expires writes a write-once `rollback_requested`.** AM-P2-13 made the daemon-side verdict/request **write-once per `(run_id, target)`** ⇒ a request written during a slow boot **stands** even after the daemon turns green: nothing re-writes it and nothing retracts it. A healthy 210–360 s slow boot therefore leaves a standing rollback request behind it.
3. **AM-P2-10.5's path 2 (`rollback_requested` ⇒ immediate) has only the re-probe as its gate.** It does **not** re-check `never-green-observed` (AM-P2-10.3) and it does **not** consult the grace ⇒ AM-P2-15's *"request + AMBIGUOUS ⇒ execute the existing chain"* rolls back a **healthy** slow boot at the next restart — with the quarantine, the counter increment, the cooldown, and (at live `rollback_window_count` **2/3**) the arming of the 24 h promote halt. **This is tonight's class, reproduced by the r2 plan.**

⇒ The r3 fold closes it on the **writer** side: the daemon-side closer must not *reach* the NOT-GREEN verdict while a grace is held, and the boot-grace horizon must cover the measured slow-boot class.

#### Fold (a) — the closer's NOT-GREEN verdict is gated on `grace_active == False`

The `GraceTracker` **never changes `ready`** — by design and by its own docstring: *"the tracker NEVER changes `ready` / `reasons` / `components` — it only publishes whether a class-gated grace is currently held, so the GATE can decide to wait"* (`daemon/services/readiness.py:757-760`; the composite's own field comment repeats it at `:404-406`: *"The grace NEVER renders the daemon healthy: `ready`/`reasons`/`components` are unchanged while it is held"*). ⇒ **the gate decides to wait; the closer must honour the same discipline.** The composite publishes `grace_active` as a first-class field (`daemon/services/readiness.py:407`, inside the `ReadinessComposite` dataclass at `:309`) and the `/readyz` detail always publishes the `grace` block (`readiness.py:535`).

⇒ **Normative (the closer's NOT-GREEN verdict):** the daemon-side closer's NOT-GREEN verdict — and therefore **every** write of `serving_unhealthy` and `rollback_requested` — additionally requires **`grace_active == False`** on the composite it read. **Degraded-but-grace-held ⇒ DEFER** (no verdict row, no request, the txn stays open, the next tick re-reads) — **never** `rollback_requested`.

#### Fold (b) — the P8 boot-grace horizon is derived from the HAND-BACK BUDGET FAMILY, not from the pool-busy constant

The r2 plan borrowed `READINESS_GRACE_HORIZON_S = 120.0` (`daemon/constants.py:165`) as the P8 horizon. **That constant answers a different question**: it is the *pool-busy load* grace horizon of the readiness service (its own comment: *"the daemon's mirror of the normative `GRACE_HORIZON_S` … ONE clock, OBSERVATION-RELATIVE"*), **not** a boot-grace budget. A 120 s horizon cannot cover a 210–360 s slow boot ⇒ the warmup observation lands **outside** the borrowed grace ⇒ the verdict fires ⇒ the standing request ⇒ tonight's class.

⇒ **Normative (the horizon):** the closer's boot-grace horizon is a **named constant of its own**, **`UPGRADE_CLOSER_BOOT_GRACE_S`**, **env-overridable**, **default 300**, and it is **derived from the hand-back budget family**: **`UPGRADE_CLOSER_BOOT_GRACE_S` ≥ `HANDBACK_VERIFY_BUDGET_S`** (Phase 1's knob; default **300**, declared at `lib.sh:180`, `phase1-plan.md` §4.1). The rationale: **both windows must cover the same measured unit-active→serving boot** — tonight's measured ~185 s, and the 210–360 s slow-boot class this fold names — so the hand-back verifier's patience and the closer's patience cannot disagree about whether a boot is still in progress. **The derivation rule is one-way: the closer's horizon may be raised above the hand-back budget; it may never be lowered below it.** `READINESS_GRACE_HORIZON_S` is **not** borrowed and **not** referenced by this lane.

#### The restored invariant (normative — a standalone rule, not a table cell)

> **`rollback_requested` is writable ONLY when genuinely unhealthy OUTSIDE grace (`grace_active == False`); no path may write it during warmup.**

This sentence is **binding on every writer** of the event — the daemon-side closer (T8 / T18) and any future writer — and it is the **single** rule that closes the root above. It is **restated verbatim** at §D.1 (the implementer-facing predicate site) and in `decisions.md` §4.5's direction-② register.

#### What this does to AM-P2-10.5 path 2 (and what it does not)

Path 2 keeps its shape: the request pre-empts the ceiling; the launcher re-probes; GREEN ⇒ commit-close ⇒ the request is **superseded** (AM-P2-15). **What changes is that path 2 can no longer be reached with a warmup-induced request**, because fold (a) forbids writing one ⇒ the r2 bypass (path 2's only gate = the re-probe, with no `never-green-observed` conjunct) is **closed at the writer**. **Recorded explicitly so no implementer reads this as a widening of path 2's gate: the request path's launcher-side gate is UNCHANGED.**

#### What this does NOT change

The never-green protection is **untouched**: a genuinely unhealthy serving (the composite `ready == False` **and** `grace_active == False`, or a version mismatch) still produces the verdict + request, and the existing chain still fires — the composition requirement's second direction. The grace **defer** is a *hold*, never a *release*: a release that is degraded forever never re-arms the grace (the tracker re-arms only after a **full lapse** — `readiness.py:775-777`), so the deferral terminates in the verdict, not in silence. **Pinned in both directions** (P-35 / P3-PIN-27).

**New ids appended by this amendment:** `T18` (task row), `P-35` (pin), `S-14` (scenario), `R-18` (risk).

---

### [PLANNER r2] New task-table rows (append-only — T14/T15 were defined in the amendments but had no rows)

| # | Task | File / anchor | Depends on | Acceptance |
|---|------|----------------|-----------|------------|
| **T14** | **Daemon→launcher rollback-disposition handoff** (AM-P2-6, as amended by AM-P2-14/AM-P2-15) — write contract: the daemon appends a `rollback_requested` history event under the journal lock, detail `target <VER> run_id <RUN_ID> reason <not-ready\|version-mismatch>`; reader contract: a **per-entry** launcher reader `_js_history_has_rollback_request` (both literals in ONE entry — AM-P2-14); placement: **position 0** in the flipped branch. | `daemon/tools/upgrade_journal.py:392-407` (writer); `launcher.sh` (new reader, beside `_js_history_has_intent_flip` `:518-536`); `launcher.sh:975+` (placement) | T8, T1 | **Step 1 (canonical GREEN predicate — verbatim per OQ-2):** *GREEN ⇔ the launcher obtained `/livez` **200** ∧ the reported `version` equals the **v-stripped** target ∧ the launcher obtained `/readyz` **200** (a second GET over the same `/dev/tcp` fd) whose body reports ready; a **wrong version fails fast**.* **[PLANNER r3 → REV-R-4]** *"fails fast"* is **PROBE-level vocabulary** — it describes how the probe stops probing, **not** a disposition: a **version-mismatch DISPOSITION is AMBIGUOUS-tallied per AM-P2-10.1, never BROKEN-immediate** (cross-ref **`P-27`**, the version-mismatch split). **[PLANNER r3 → REV-R-5]** the duplicated `∧ AND` token is dropped from the canonical predicate (same correction applied at **AM-P2-11** and **AM-P2-4**). Step 2: request present ⇒ re-probe ⇒ **GREEN ∧ `intent_flip` ⇒ commit-close** (request journaled as superseded) · **GREEN ∧ ¬`intent_flip` ⇒ defer + journal** (R3 hinge) · **BROKEN ⇒ the existing chain byte-identically** · **AMBIGUOUS ⇒ the existing chain** (the daemon's post-grace verdict is the affirmative evidence). Step 3: no request ⇒ the ordinary ladder (AM-P2-10.5). **Pins P-13..P-18 + P-25 + P-26.** |
| **T15** | **Run-loop re-sweep hook** (AM-P2-7, as scoped by AM-P2-17) — after each child exit, call the **narrow** flipped-branch helper (NOT the whole `_journal_sweep`; the stale **pre-flip clear** is explicitly **OUT** of scope — AM-P2-17) under the same lock discipline (AM-P2-3). | `launcher.sh` (new helper + the `run_loop` hook at `:1507-1560`) | T14 | A `rollback_requested` written by the daemon during an up-window is **executed** at the next child cycle; the helper is **idempotent** (second call = no-op); the hook's own absent observation is **not** absent-ceiling-eligible beyond the ruled predicate and **never** counts a **tempfail-class (exit 75)** child exit (AM-P2-10.4). **Pin P-32.** |
| **T16** | **The bounded absent-ceiling** (AM-P2-10) — the launcher-local counter file, the observation taxonomy (GREEN / BROKEN / AMBIGUOUS), the qualifying-observation rule, the ceiling predicate, and the never-green-observed predicate (sticky `green` flag + the journal-history check). | `launcher.sh` (new counter helpers + the flipped branch) | T1, T3, T4 | The canonical never-green shape (every launcher observation AMBIGUOUS) **terminates in the existing `sweep_rollback` chain** after `SWEEP_DEFER_CEILING` qualifying observations; fewer observations ⇒ defer; a GREEN observation at any point ⇒ the ceiling is permanently ineligible for that txn. **Pins P-20, P-23, P-24, P-29, P-33.** |
| **T17** | **The canonical GREEN predicate in the launcher probe** (AM-P2-11) — the launcher probes `/readyz` as well, over the same `/dev/tcp` fd; wrong-version fails fast. | `launcher.sh` (`_js_serving_probe`) | T1 | `hard-red` (livez 200 + readyz 500) ⇒ **NOT GREEN**; `green` ⇒ GREEN; a wrong-version 200 ⇒ NOT GREEN (and **fails fast** — no polling to the budget); the probe never opens a second connection. **Pin P-5 (amended) + the new-fixture legs (AM-P2-12).** |
| **T18** **[PLANNER r3]** **[PLANNER r4]** | **The daemon-closer's grace gate + its own boot-grace horizon** (AM-P2-24) — (a) the closer's NOT-GREEN verdict additionally requires **`grace_active == False`** on the composite it read (`daemon/services/readiness.py:407`; the `GraceTracker` never changes `ready` — `:757-760`); (b) the P8 boot-grace horizon becomes the named constant **`UPGRADE_CLOSER_BOOT_GRACE_S`** (env-overridable, default **300**), **derived from the hand-back budget family** (`UPGRADE_CLOSER_BOOT_GRACE_S` ≥ `HANDBACK_VERIFY_BUDGET_S`, Phase 1's knob, default 300, `lib.sh:180`) — the borrowed `READINESS_GRACE_HORIZON_S`=120 pool-busy constant (`daemon/constants.py:165`) is **not** used. **[PLANNER r4 → note (c) ONE DECLARATION HOME, stated explicitly]** `UPGRADE_CLOSER_BOOT_GRACE_S` is declared in **exactly one place**: **`daemon/services/upgrade_journal_sweep.py`, beside the other sweep-local constants** (`:96-115` — `DEFAULT_UPGRADE_JOURNAL_SWEEP_INTERVAL_SECONDS` at `:99`, `DEFAULT_REAPER_TIMEOUT_SECONDS` at `:105`, `EXECUTOR_STALENESS_WINDOW_S` at `:112`; verified in-tree). **It is NOT declared in `daemon/constants.py`** — that module is cited here only as the site whose `READINESS_GRACE_HORIZON_S` (`:165`) is **not** borrowed. **Shell/daemon boundary, stated explicitly:** the constant is **DAEMON-SIDE ONLY** — there is **no shell counterpart** and none is required: the launcher's counterparts are the `SWEEP_STALE_S` staleness gate (`launcher.sh:69`) and the bounded absent-ceiling (AM-P2-10.2), and the two sides are **not** required to be numerically equal (the daemon acts at the first tick after 600 s; the launcher needs ≥3 qualifying observations). The **cross-surface constraint** is one-way and documented in both places: the daemon constant's default (300) must be **≥** the shell knob's default (`HANDBACK_VERIFY_BUDGET_S` = 300, `lib.sh:180`), so both windows cover the same measured unit-active→serving boot; the env-var name `UPGRADE_CLOSER_BOOT_GRACE_S` overrides the daemon constant. | `daemon/services/upgrade_journal_sweep.py` (the closer method; **the constant's ONE declaration home** — beside the other sweep constants `:96-115`) | T6, T7, T8 | **Degraded-but-grace-held ⇒ DEFER** (no `serving_unhealthy`, **no** `rollback_requested`, txn stays open); **genuinely unhealthy outside grace ⇒ the verdict + request as before** (the never-green direction is untouched); the horizon covers the measured 210–360 s slow-boot class. **Pins P-35 (+ the P-6 / P3-PIN-11a never-green leg), scenario S-14, risk R-18.** |

---

### [PLANNER r2] New acceptance pins (append-only — `P-13`..`P-22` were declared inside the amendments but had no rows in this table)

| Pin | Where | What it proves |
|---|---|---|
| **P-13** | `tests/test_boot_sweep_commit_and_continue.sh` (new section) | request + NOT-GREEN ⇒ the chain executes; the post-state is **byte-identical** to the legacy no-request path |
| **P-14** | same file | no request ⇒ the defer ladder is unchanged |
| **P-15** | same file | request + GREEN ⇒ commit-close, the request is NOT executed — **plus the r2 negative axis**: request + GREEN + **no `intent_flip`** ⇒ **defer**, txn open, no `serving_green_commit`, no `sweep_rollback` (R3 hinge) |
| **P-16** | same file | the ceiling path and the request path converge on **one** chain ⇒ identical post-state |
| **P-17** | same file | isolation — a request naming another `run_id` / another target is ignored |
| **P-18** | daemon-side pytest | the daemon writes the request with **ZERO** symlink / quarantine / counter side-effects |
| **P-19** | fault-injection pack (new) | the history-before-close order: interrupt at **each** prefix ⇒ the next actor's disposition is never a wrongful rollback and the sequence converges |
| **P-20** | same file | the deferral is bounded: at most `CEILING − 1` deferrals precede the rollback (re-expressed against AM-P2-10's ceiling predicate) |
| **P-21** | daemon-side pytest | the promote-wake reader is **run_id-scoped** ⇒ an unrelated commit-close cannot drop a pending promote-wake |
| **P-22** | daemon-side pytest | the vocabulary-growth test updates: `len(_TERMINAL_EVENTS)` ⇒ 10 + set-equality; `_OUTCOME_LABELS["serving_green_commit"]` present |
| **P-23** | `tests/test_boot_sweep_commit_and_continue.sh` (new section) | **the absent-ceiling fires**: ≥`SWEEP_DEFER_CEILING` qualifying AMBIGUOUS observations (driven across **separate `run_sweep` invocations** — the counter persists) ⇒ the **existing** chain fires with all **four** effects (`current`→prev, `quarantined[]` append, `rollback_window_count` ++, `cooldown_until` armed). Below the threshold ⇒ **defer** (txn open) |
| **P-24** | same file | **never-green-observed is sticky**: an observation of GREEN at any point ⇒ the absent-ceiling is **permanently ineligible** for that txn ⇒ the sweep defers forever (operator backstop) — *and* a `serving_green_commit` row in the history (the AM-P2-1 crash-prefix shape) likewise ⇒ ineligible |
| **P-25** | same file (new section) | **the cross-entry negative** (AM-P2-14): a history whose `rollback_requested` event and whose target string live in **different** entries ⇒ the reader returns "no request" ⇒ **no rollback of a serving-green release**; the txn stays open |
| **P-26** | same file (new section) | **the R3 hinge** (AM-P2-15): a **forged** `rollback_requested` + a **green** probe + **no `intent_flip`** ⇒ **defer** — never a commit-close, never a rollback |
| **P-27** | same file (new section) | **the version-mismatch split** (AM-P2-16): (a) symlink-on-target + `/livez` 200 with a wrong version ⇒ AMBIGUOUS ⇒ counted, bounded, and the ceiling eventually fires the chain; (b) symlink-**moved-away** + wrong version ⇒ **NO ACTION** (no quarantine, no counter, no `sweep_rollback`) |
| **P-28** | daemon-side pytest | **journal idempotence** (AM-P2-13): N closer ticks with the same stale txn ⇒ **exactly one** `serving_unhealthy` and **exactly one** `rollback_requested` for that `(run_id, target)` — no per-tick growth |
| **P-29** | same file (new section) | **counter-file lifecycle** (AM-P2-20): the file is deleted on commit and on rollback; a stale `.sweep-defer-<other_run_id>` is swept at boot; the filename shape is sanitised and two txns cannot collide |
| **P-30** | same file (new section) | **the kill-switch** (**§D.1's [PLANNER r3] two-kill-switches paragraph** / `SWEEP_SERVING_GREEN_ENABLED`): `=0` restores today's behaviour (the unconditional auto-rollback) and `=1` (the default) installs the new ladder — the switch is **not** a permanent non-escapable state on an unverified platform. **[PLANNER r3 → REV-R-3] The r2 citation of `AM-P2-23` was WRONG and is corrected here:** `AM-P2-23` is the **OQ-3 cap-interaction** amendment (document-only), **not** the kill-switch — the kill-switch has no amendment id of its own; it is defined normatively in **§D.1's [PLANNER r3] paragraph** (both switches, both defaults, the runbook, the W1 pre-step). The row text itself is unchanged |
| **P-31** | static (T0) | **the purity pin** (AM-P2-21): after comment-stripping and with word-boundary matching, `launcher.sh` contains **no executable** `curl`/`wget`/`nc`/`lsof`/`python3` beyond the **recorded baseline** (today: all zero after comment-stripping) |
| **P-32** | same file (new section) | **T15's scope + idempotence** (AM-P2-17): the hook executes a pending `rollback_requested` at the next child cycle; a second call is a no-op; the **stale pre-flip clear is NOT** performed by the hook (a live pre-flip promote's txn survives it); a **tempfail-class (exit 75)** child exit does **not** increment the counter |
| **P-33** | same file (new section) | **owner-liveness on the whole stale path** (AM-P2-18): a stale **pre-flip** txn whose owner is **alive** is **NOT** cleared (it survives); the same txn with a dead owner **is** cleared as today |
| **P-34** | daemon-side pytest (alert sink) | **the alert-kind split** (AM-P2-19): a `serving_unhealthy` / `rollback_requested` event ⇒ kind **`upgrade_serving_unhealthy_requested`**; the **executed** `sweep_rollback` ⇒ **`upgrade_auto_rollback`** (unchanged); a request that is **superseded** never produces an `upgrade_auto_rollback` page |
| **P-35** **[PLANNER r3]** **[PLANNER r4]** | daemon-side pytest — `tests/unit/tools/test_upgrade_journal_sweep_liveness.py` + the NEW daemon-side pin file (the same seam as `P-6`) | a stubbed `readiness_provider` returning a composite with **`ready == False` and `grace_active == True`** (the warmup shape) against a stale flipped txn ⇒ **THE DEGRADED-BUT-GRACE-HELD ⇒ DEFER LEG** (F-1, AM-P2-24): the closer writes **no** `serving_unhealthy` row and **no** `rollback_requested` row; the txn stays **open** (`in_flight` unchanged); `current` is **not** repointed; **no** quarantine; **no** counter increment; **no** cooldown ⇒ **no** path can execute a rollback of a healthy slow boot. **This is the new member of the both-direction composition set** — the other direction (genuinely unhealthy **outside** grace ⇒ the verdict + request + the existing chain) is `P-6` / `P3-PIN-11a`, which must use a **not-grace-held** fixture (`ready == False` ∧ `grace_active == False`) so the two legs cannot be satisfied by the same stub. Mutation: drop the `grace_active == False` conjunct from the closer's NOT-GREEN verdict ⇒ the leg goes **RED** (a request row appears). **[PLANNER r4 → note (d) THE FIXTURE IS HONESTLY LABELLED]** this leg drives a **stubbed** `readiness_provider` returning a **synthetic** composite — it does **not** exercise the real `GraceTracker` / the real readiness composite ⇒ it proves the closer's **conjunct**, not the composite's **arithmetic**. The **real** GraceTracker/composite-interleaving leg is **`P-37`** (appended); `P-35` + `P-37` together are the both-direction set |
| **P-36** **[PLANNER r4]** | shell-level (the `adopt_stale_txn` / `promote_entry_check` fixture family — the same harness as `P-9`/`P-10`) — **T1** | **The clearance reader's shape — the two negatives (the R-15 class):** (a) **prefix collision** — a journal whose only clearance event names `target v0.28.55` ⇒ the reader for `v0.28.5` returns **"not cleared"** ⇒ the promote entry is **still refused** (the frozen rule intact); (b) **cross-entry split** — the event kind in one history entry and the target string in a **different** entry ⇒ the reader returns **"not cleared"**. **Positive half:** an event whose detail is the pinned house shape `target v0.28.5 run_id <ID> …` ⇒ the reader returns **"cleared"** ⇒ the promote entry is **accepted**. A reader that never fires is a **regression to tonight's punishment class** (the frozen rule keeps refusing a healthy release) ⇒ the pin must assert **both** the positive and the two negatives. Mutation: make the reader a whole-array conjunction ⇒ (b) goes **RED**; drop the delimiter anchor ⇒ (a) goes **RED** (plant `target v0.28.55`, assert `v0.28.5` is still refused) |
| **P-37** **[PLANNER r4]** | daemon-side pytest — `tests/unit/tools/test_upgrade_journal_sweep_liveness.py` + the NEW daemon-side pin file (the same seam as `P-6` / `P-35`) — **T1** | **The REAL GraceTracker / composite-interleaving leg** (note (d)'s companion to `P-35`): the closer is driven against a **real** composite whose `grace_active` is produced by the **real** tracker — arm the tracker on a pool-busy eligibility observation, drive the closer through the grace-held window ⇒ **no** `serving_unhealthy`, **no** `rollback_requested`, txn **open**; then let the grace **lapse** (full lapse ⇒ re-arm only after a lapse, `readiness.py:775-777`) and re-drive ⇒ the **never-green** verdict is reached. `P-35` proves the closer's **conjunct** against a synthetic composite; this pin proves the **composite's arithmetic** feeds that conjunct ⇒ **the two legs cannot be satisfied by the same stub**. Mutation: replace the real composite with the `P-35` stub ⇒ the leg no longer exercises the tracker ⇒ **RED** (the pin's non-vacuity assertion fails) |
| **P-38** **[PLANNER r4]** | daemon-side pytest — the same seam as `P-6` / `P-35` / `P-37` — **T1** | **The daemon-side kill-switch, TWO STATES** (note (e) — the switch was named in §D.1 but **unpinned**): **`=0`** ⇒ the closer lane writes **no** `serving_green_commit`, **no** `serving_unhealthy` and **no** `rollback_requested` — a stale flipped txn is left **untouched** (`in_flight` open, no symlink repoint, no quarantine, no counter, no cooldown); **`=1`** (the default) ⇒ the lane behaves normally (the GREEN commit-close and the never-green verdict both fire). The switch must be read **per tick**, not cached, so an operator can flip it without a restart. Mutation: cache the switch at construction ⇒ flipping it to `=0` mid-run still writes a verdict ⇒ **RED**; make `=0` still write `serving_unhealthy` ⇒ **RED** |

---

### [PLANNER r2] New success criteria (append-only)

| # | Criterion | How to measure | Threshold |
|---|-----------|----------------|-----------|
| **S-11** | **The canonical never-green shape terminates in the existing rollback chain** (the council's REJECT basis, closed) | Drive the launcher sweep against a stale flipped txn whose every observation is AMBIGUOUS, across ≥`SWEEP_DEFER_CEILING` separate `run_sweep` invocations | the existing chain fires with all **four** effects; the txn is **not** stranded |
| **S-12** | **A serving-green release is never rolled back**, including after a green observation that preceded an ambiguous one | Same harness; observe GREEN once, then present AMBIGUOUS observations | **no** `sweep_rollback`; **no** quarantine; **no** counter increment; the txn defers (operator backstop) |
| **S-13** | **The commit-close never touches the rollback cap** (OQ-3, document-only) | Read `rollback_window_count` + `cooldown_until` before and after a commit-close | both **unchanged**; **no** exclusion mechanism exists anywhere in the tree |
| **S-14** **[PLANNER r3]** | **A degraded-but-grace-held release is never rolled back** (F-1, AM-P2-24 — the new member of the both-direction composition set) | Drive the daemon-side closer against a stale flipped txn whose composite is **present but degraded** (`ready == False` ∧ `grace_active == True`) — the warmup shape; then, in the mirror leg, the same txn with `grace_active == False` | **the defer leg:** no `serving_unhealthy` row, **no** `rollback_requested` row, the txn stays open, no quarantine / counter / cooldown ⇒ **no** rollback of a healthy slow boot. **the never-green leg (unchanged):** `grace_active == False` ⇒ the verdict + request are written and the existing chain still fires. **Both legs must be satisfied by the same implementation** — a single stub that satisfies only one of them is a failure |

---

### [PLANNER r2] New risks (append-only)

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| **R-13** | The **absent-ceiling fires on a healthy release** that was briefly unserved (a network blip, a slow bind) | **High** (a wrongful rollback at live 2/3 arms the 24 h promote halt) | Low | three independent conjuncts (≥3 observations, owner-dead, intent_flip, **never-green-observed**) + the sticky green flag + the journal-history check; the BOUNDED class never fires on the first ambiguous observation |
| **R-14** | The **counter file is lost** (an install-dir wipe, a different `INSTALL_DIR` resolution between boots) ⇒ the tally resets ⇒ the never-green shape never terminates | **High** (the council's REJECT basis returns in a new form) | Low | the file lives beside `.launcher-state` in the install root (the same root the journal lives in); the boot-time stale sweep deletes only counters whose `run_id` is not the current txn's; a lost counter ⇒ the count restarts ⇒ bounded, not unbounded (the operator backstop still applies) |
| **R-15** | The **pre-existing** `_js_history_has_intent_flip` whole-array conjunction can produce a cross-entry false positive on `intent_flip` (the mirror of P-25) | **High** (a false authorization of a commit-close) | Low | **named follow-up** (AM-P2-14) — a separate hygiene commission; the new T14 reader does not inherit the shape; the `intent_flip` conjunct is **necessary but not sufficient** for a commit (the serving verdict is also required). **[PLANNER r4 → note (j) THE PREFIX-COLLISION CLASS IS THE SAME HAZARD, NOT A SEPARATE ONE]** `_js_history_has_intent_flip`'s pattern (`launcher.sh:518-536`, verified in-tree: `pattern='*"event":"intent_flip"*target '"$target"'*'`) carries **two** hazards, and **any reader that copies its shape inherits both**: (1) the **prefix collision** — `target v0.28.5` occurs inside `target v0.28.55`, so a flip of `v0.28.55` can satisfy the `intent_flip` conjunct of `v0.28.5`; (2) the **cross-entry false positive** — the event kind may live in entry A and the target string in entry B. ⇒ **the R-15 hygiene commission must cover BOTH**, and the plan's own new readers (the T14 request reader, AM-P2-14; the clearance reader, §D.3's contract box) are specified **per-entry AND delimiter-anchored** so they inherit neither. The pinned negative for the clearance reader is **`P-36`** |
| **R-16** | The **`/readyz` probe** (AM-P2-11) lengthens the launcher probe past its budget, or the second GET over the same fd mis-reads a pipelined response | Medium | Low | the second GET is over the **same** fd (no second connect); the body-read loop is wall-clock-bounded (AM-P2-9's requirement); a truncated body ⇒ NOT-GREEN (never green) — pinned |
| **R-17** | The daemon-side closer's **write-once** key `(run_id, target)` suppresses a **legitimate second** request for the same txn after the first was superseded | Medium | Low | the key includes the target **and** the run_id; a re-promote of the same version after a rollback has a **new** run_id ⇒ a fresh request; a superseded request is journaled as superseded, not silently dropped |
| **R-18** **[PLANNER r3]** | **F-1 (AM-P2-24):** the closer's NOT-GREEN verdict is reached while a grace is held ⇒ a write-once `rollback_requested` **stands** after the daemon turns green ⇒ AM-P2-10.5 path 2 (whose only gate is the re-probe) rolls back a **healthy** 210–360 s slow boot at the next restart — with the quarantine, the counter increment, the cooldown, and (at live `rollback_window_count` **2/3**) the arming of the 24 h promote halt | **High** (tonight's exact class; a wrongful rollback at live 2/3 arms the 24 h promote halt) | Medium | **(a)** the closer's NOT-GREEN verdict requires **`grace_active == False`** — the `GraceTracker` never changes `ready` (`readiness.py:757-760`), so the composite's own `grace_active` field (`:407`) is the gate signal ⇒ **degraded-but-grace-held ⇒ DEFER**; **(b)** the boot-grace horizon is the closer's **own** constant `UPGRADE_CLOSER_BOOT_GRACE_S` (default **300**), **≥ `HANDBACK_VERIFY_BUDGET_S`** — it must cover the measured slow-boot class, and the borrowed 120 s pool-busy constant is not used; **(c)** the request path is closed **at the writer** (the launcher-side gate is unchanged); **(d)** **pinned in both directions** — `P-35` / `P3-PIN-27` (defer) + `P-6` / `P3-PIN-11a` (never-green, on a not-grace-held fixture) + `S-14`; **(e)** the restored invariant is stated normatively at §D.1 and in AM-P2-24: *`rollback_requested` is writable ONLY when genuinely unhealthy OUTSIDE grace* |

---

### [PLANNER r2] Reconciliation of the "never-green stays armed" claims (the council's second finding)

The council noted that **at least four plan texts claim "never-green stays armed"** while the mechanism no longer armed it. Those sentences are **not wrong in intent** — they are the COMPOSITION REQUIREMENT — but they were **under-specified** (they did not say *by which mechanism*). After this fold, the claim is true again, and every doc names the mechanism:

| Claim site | Mechanism that arms it (r2) |
|---|---|
| `phase1-plan.md` out-of-scope note | Phase 2 — the never-green path (BROKEN ⇒ immediate; AMBIGUOUS ⇒ bounded absent-ceiling) |
| `phase2-plan.md` objective + §D.1/§D.2/§D.6 + matrix rows 2/4 | **AM-P2-10** (the bounded absent-ceiling) + path 1 (BROKEN ⇒ immediate) |
| `phase3-plan.md` pins P3-PIN-06/-11/-12 + the never-green pack family | the same two paths, pinned in both directions |
| `decisions.md` §4.5 | **AM-P2-10** — the absent-ceiling is the third element of direction ② |
| `architecture-recommendation.md` §2/§6(iii)/(iv) | the same |

**Every doc that claims "never-green stays armed" now also carries the trigger that makes it true** (BROKEN ⇒ immediate; AMBIGUOUS ⇒ bounded ceiling; request ⇒ immediate). No claim is deleted; each is **supplemented** with its mechanism.

---

## [PLANNER r3] FOLD ROUND 3 — F-1 (the surviving boundary defect) + R-1..R-6 — 2026-11-02

**Fold mode:** additive (Mode 2). Every change is marked `[PLANNER r3]`; **no existing task, pin, decision, OQ, T-number, P-number, S-number, R-number or section number is renumbered or deleted**. New ids are appended: **`AM-P2-24`**, **`T18`**, **`P-35`**, **`S-14`**, **`R-18`**.

**Verdict context.** The plan council's delta pass returned **APPROVE-WITH-ADVISORIES**, *conditional* on folding **F-1** (one surviving 🔴 critical) plus **REV-R-1**..**REV-R-4** (mandatory text folds); **REV-R-5**/**REV-R-6** are trivial; one carried doc-hygiene item rides with this fold. The **leader confirmed the reviewer's F-1 remediation adjudication** — adopt **both (a)** and **(b)** plus the pin.

**Finding-id namespace (r3).** `REV-R-1` … `REV-R-6` are the **council's delta-pass finding ids, quoted from the review report** — they are **NOT** this plan set's risk ids (this plan set's risk series is the bare `R-n`). The namespace is introduced by this fold precisely so the two cannot collide (`phase2-plan.md` already carries risks `R-1`…`R-17`).

### The r3 fold map (this phase's share)

| Finding | Where | What changed |
|---|---|---|
| **F-1 (a)** — the closer's NOT-GREEN verdict must require `grace_active == False` | **AM-P2-24** (new) + **§D.1** NOT-GREEN actions + **§(c)** mechanism 2 + **AM-P2-10.5** (path-2 note) | the grace conjunct is added; **degraded-but-grace-held ⇒ DEFER, never `rollback_requested`**; the `GraceTracker`'s "never changes `ready`" doctrine (`readiness.py:757-760`) is cited as the grounding; the composite's `grace_active` field (`:407`) is the readable signal |
| **F-1 (b)** — the P8 boot-grace horizon | **AM-P2-24** + **§D.1** P8 row + **§(c)** mechanism 2 | the horizon becomes the closer's **own** `UPGRADE_CLOSER_BOOT_GRACE_S` (env-overridable, default **300**), **derived from the hand-back budget family** (**≥ `HANDBACK_VERIFY_BUDGET_S`**); the borrowed `READINESS_GRACE_HORIZON_S`=120 pool-busy constant (`daemon/constants.py:165`) is **dropped** |
| **F-1 — the restored invariant** | **AM-P2-24** (standalone normative sentence) + **§D.1** (restated verbatim) | *"`rollback_requested` is writable ONLY when genuinely unhealthy OUTSIDE grace (`grace_active == False`); no path may write it during warmup."* |
| **F-1 — the new pin** | **`P-35`** (pin table) + **`S-14`** (scenario) + **`T18`** (task row) + **`R-18`** (risk) | the **degraded-but-grace-held ⇒ DEFER** leg joins the both-direction composition set; the never-green leg (`P-6` / `P3-PIN-11a`) must use a **not-grace-held** fixture so the two legs cannot be satisfied by the same stub |
| **REV-R-1** — stale direction-② pin rows | **`P-2`** and **`P-3`/C5b** (this phase) | **append-only SUPERSEDE annotations** (the rows are **kept, not deleted**): under r2/r3 only the **BROKEN** class fires immediately; `hard-red` and no-listener are **AMBIGUOUS** ⇒ deferred + counted ⇒ **drive `P-23`/`P-24`/`P-27`** and the `livez-non200` fixture mode instead. The rationale is recorded on each row: a tester building from the un-annotated row would hard-redden against the **correct** implementation |
| **REV-R-2** — §D.6 "Exact firing rule" | **§D.6** | the marker `[PLANNER r2 → AM-P2-10.5] superseded — firing rule is the three-path set` is added; the paragraph is explicitly demoted to an audit-trail restatement that **must not be implemented** |
| **REV-R-3** — the two kill-switches + P-30's dangling citation | **§D.1** (new normative paragraph) + **`P-30`** | one sentence defines **both** switches (launcher `SWEEP_SERVING_GREEN_ENABLED`, daemon `ENSEMBLE_UPGRADE_SERVING_GREEN_CLOSER`), both defaults (`1`), the runbook (T13) and the W1 pre-step; **P-30's `AM-P2-23` citation is corrected** (`AM-P2-23` is the OQ-3 cap-interaction amendment, **not** the kill-switch) |
| **REV-R-4** — "fails fast" is probe-level vocabulary | **AM-P2-11** + **T14 step 1** (the two mandated implementer-facing sites) | one sentence at each site: a **version-mismatch DISPOSITION is AMBIGUOUS-tallied per AM-P2-10.1, never BROKEN-immediate** (cross-ref `P-27`) |
| **REV-R-5** — the duplicated `∧ AND` token | **AM-P2-11** + **T14 step 1** (mandated) **+ `AM-P2-4`** (the third copy of the same predicate) | the token is dropped from all three copies of the canonical GREEN predicate |
| **REV-R-6** — the seeded-request pin's label | this fold map | **recorded, not folded** (approver note): the **seeded-request** pin lives at **`P-13` / `P3-PIN-22`**; **`P3-PIN-25` is the wrong-version listener fixture pin** (the AM-P2-9 probe mutations). The r2 fold's "P3-PIN-25" label is **incorrect** and must not be used to locate the seeded-request pin |
| **Carried doc-hygiene** — the CEILING=3 timing estimate | **matrix row 4** + `decisions.md` §OQ-C-06(d) + `architecture-recommendation.md` §6(iii) | the estimate is corrected to **≈10–15 min** (3 qualifying launcher boots × ≈3.5 min in a `RestartSec=10` + ~200 s dying-boot environment); the earlier **≈30–40 min** / "~30 min" figures assumed a ~10 min-per-boot cadence and were **wrong** |

### What this fold does NOT change

- The **never-green protection is untouched** — a genuinely unhealthy serving (outside grace) still produces the verdict + request and the existing chain still fires.
- The **path set is untouched** — **BROKEN ⇒ immediate · `rollback_requested` ⇒ immediate · AMBIGUOUS ⇒ bounded**; there is still no fourth path.
- The **canonical GREEN predicate is unchanged** in substance (`/livez` 200 ∧ version == v-stripped target ∧ `/readyz` 200 ∧ ready); only the duplicated token is dropped and the fail-fast vocabulary is disambiguated.
- The **launcher-side request gate is unchanged** (R-4 is a vocabulary clarification, not a gate change).

### New ids appended by this fold (cross-reference)

| Kind | New ids |
|---|---|
| **Phase 2** | `AM-P2-24` · `T18` (task row) · `P-35` (pin) · `S-14` (scenario) · `R-18` (risk) |
| **Cross-phase** | the daemon-side boot-grace constant **`UPGRADE_CLOSER_BOOT_GRACE_S`** (default **300**, env-overridable, **≥ `HANDBACK_VERIFY_BUDGET_S`**) |


---

## [PLANNER r4] FOLD ROUND 4 — the fresh approver's iteration-001 REJECT (4 blockers) + 12 carried notes — 2026-11-02

**Fold mode:** additive (Mode 2). Every change below is marked `[PLANNER r4]`; **no existing task, pin, decision, OQ, T-number, P-number, S-number, R-number, D-number or section number is renumbered or deleted**. New ids are appended: **`P-36`**, **`P-37`**, **`P-38`** (phase-2 pins) and their consolidated rows `P3-PIN-28`/`P3-PIN-29`/`P3-PIN-30` (in `decisions.md` §5.1 and `phase3-plan.md` §4.2.2).

**Verdict context.** The fresh approver **REJECTED iteration 001** (2/2 workers), with **4 blocking issues** — and **both** verifiers recorded that **all four are doc-level folds over content that already exists correctly elsewhere in the plan set; no new design work is implied**. The leader adjudicated all four as **ACCEPTED**, binding this fold: *implementer-facing primary references must never prescribe superseded mechanics* — the same discipline as r3's `REV-R-1`/`REV-R-2`, applied to the sites the council did not annotate. A further **12 non-blocking notes** were carried and are folded **at the same touch**.

**Fold mode note.** Every superseded prescription below is marked **SUPERSEDED** with the in-tree evidence; the superseded text is **retained in place** for the audit trail (append-only). Nothing was deleted and nothing was renumbered.

### The r4 fold map (this phase's share)

| # | What folded | Where (this file) | What changed |
|---|---|---|---|
| **BLOCKER 1** | §D.2 rung 1 was missing the authorization conjuncts | **§D.2 canonical ladder, rung 1** | AM-P2-4's canonical clause (i)+(ii) is folded **verbatim** (the rung previously carried only the serving clause) and the **owner-liveness conjunct (AM-P2-2)** is added as the third conjunct ⇒ rung 1 now resolves as **GREEN ∧ `intent_flip` ∧ (owner-dead ∨ heartbeat-stale) ⇒ commit-close** · **GREEN ∧ ¬`intent_flip` ⇒ DEFER + journal** (the R3 hinge) · **GREEN ∧ `intent_flip` ∧ owner-alive ⇒ DEFER** · NOT-GREEN ⇒ rung 2. Rungs 0 and 2 inherit the owner-liveness conjunct from AM-P2-18; a uniformity note makes that explicit |
| **BLOCKER 1** | matrix row 6's ceiling tail was unreachable | **state/sequence matrix, row 6** | the *"defer → ceiling ⇒ rollback"* tail is **SUPERSEDED**: under AM-P2-10.3's sticky-green rule a persistently-GREEN txn's absent-ceiling is **permanently ineligible** ⇒ the actual disposition is **DEFER + journal + operator surfacing** (greppable reason marker + the launcher's `_notify_once` disposition notifier + the runbook + the ceremony notes) — consistent with AM-P2-15's *GREEN ∧ ¬`intent_flip`* row and `P-15`'s negative axis; the SSE alert map is deliberately **not** extended for `sweep_deferred` (AM-P2-19) |
| **BLOCKER 2** | the commit-close mutation order was **inverted** at three implementer-facing sites | **§D.1 GREEN actions** · **§D.2 commit-close mutations** · **T2 task row** | all three now prescribe **AM-P2-1's history-before-close order** (`current` → `previous` → history append `serving_green_commit`, with `quarantine_cleared_serving_green` **FIRST** when the target is quarantined → `in_flight`→null → `quarantined[]` rewrite, opportunistic on the launcher side). The *"mirror `launcher.sh:1108-1127` exactly"* prescription is **SUPERSEDED at all three sites** — **verified in-tree at this fold**: that range is the OLD close-before-history order (`:1110` current → `:1118` previous → `:1120` `in_flight`→null → `:1122` history append) ⇒ mirroring it defeats AM-P2-10.3's journal check and reds P-19 / P3-PIN-23 |
| **BLOCKER 3** | two incompatible quarantine-clearance readers and **no pinned writer format** | **§D.3 (contract box)** · **§D.3 Shell row** · **T10** · **AM-P2-8's quote-anchored bullet** · **AM-P2-6's reader-contract sentence** | **one** writer format is pinned (house convention — the version **unquoted**, `target $VERSION run_id $RUN_ID …`, byte-identical to AM-P2-6's `rollback_requested` detail) and **one** reader is specified (**per-entry**, **delimiter-anchored** — the event kind + `target $VERSION` + optional `run_id $RUN_ID`, all inside **ONE** history entry) ⇒ both the prefix-collision hazard (R-15's class) and the cross-entry hazard (AM-P2-14) are closed. Both earlier prescriptions are **SUPERSEDED** (append-only), each with the reason it cannot work. The stale claim *"lib.sh has its own history reader"* is **corrected** (verified: `lib.sh` has history **writers** and `journal_is_quarantined`, but **zero** `intent_flip` readers ⇒ the reader is new work). New pin **`P-36`** |
| **note (a)** | the adopt path's two undisclosed divergences | **§D.4** | two binding sentences added: (a-i) the GREEN-without-`intent_flip` fallthrough **diverges across the three decision-makers by design** ⇒ R-8's shared-pin claim is **scoped** to the fixtures where all three agree; (a-ii) the NOT-GREEN arm has **no defer and no grace**, justified by the 600 s staleness gate being **strictly longer** than the 210–360 s slow-boot class the boot-grace covers |
| **note (b)** | (register-side) the `daemon/constants.py:165` anchor — *folded in `decisions.md`* | — | the register row D.1 is annotated there (this file's §D.1 already states that `READINESS_GRACE_HORIZON_S` is **not** borrowed) |
| **note (c)** | `UPGRADE_CLOSER_BOOT_GRACE_S` had two candidate homes | **T18 task row** (touchpoints + acceptance) | **ONE declaration home is stated**: `daemon/services/upgrade_journal_sweep.py`, beside the other sweep-local constants (`:96-115`; verified: `:99`/`:105`/`:112`). **NOT** `daemon/constants.py`. The **shell/daemon boundary** is stated explicitly (daemon-side only; no shell counterpart; the two sides are not required to be numerically equal; the one-way constraint is `default(300) ≥ HANDBACK_VERIFY_BUDGET_S`'s default 300) |
| **note (d)** | `P-35`'s stub was not honestly labelled | **P-35 pin row** (+ new **`P-37`**) | `P-35` now states plainly that it drives a **synthetic** composite (it proves the closer's **conjunct**, not the composite's **arithmetic**); the **real** `GraceTracker`/composite-interleaving leg is the new **`P-37`** |
| **note (e)** | the daemon kill-switch was unpinned | new **`P-38`** | the **two-state** pin: `=0` ⇒ the lane writes **no** `serving_green_commit` / `serving_unhealthy` / `rollback_requested` and leaves the txn untouched; `=1` (default) ⇒ normal behaviour; read **per tick**, not cached |
| **note (f)** | (phase-3-side) the rider's grep claim — *folded in `phase3-plan.md`* | — | the claim is corrected there; this file's rider references are unaffected |
| **note (g)** | the stale "~59min" timing sentence | **§(a) Recovery-restart window — Timing** | the *~59 min* figure is marked as **no longer the operative bound** (it was tonight's *unconditional* auto-rollback age, not a designed fuse — there is **no** 3540 s constant anywhere in the tree); the operative bound is the **≈10–15 min bounded absent-ceiling**; the **launcher/daemon precedence** is stated (the launcher sweeps pre-daemon, the daemon closer normally wins at the first tick after the 600 s gate ≈11 min) |
| **note (h)** | (phase-1-side) the C-10a fiction's second copy — *folded in `phase1-plan.md`* | — | the second copy (`promote.sh:255-260`) is annotated there and `T-P1-9` extended to both copies |
| **note (i)** | the large-journal regime was unnamed | **T13 task row** | the runbook must name it: past the launcher-side size guard the launcher's never-green path **degrades to the operator backstop** (never a rollback) ⇒ the **daemon-side closer is the only automatic rollback path** (it parses in-process, so it is not subject to the O(N²) extraction cost) — plus the GREEN-but-unauthorized residual and the two AM-P2-7 no-actor shapes |
| **note (j)** | R-15 named only the cross-entry hazard | **R-15 risk row** | the **prefix-collision class** is recorded as the **same** hazard: `_js_history_has_intent_flip`'s pattern carries **both**, and any reader copying its shape inherits both; the plan's own readers are per-entry **and** delimiter-anchored |
| **note (k)** | (phase-1-side) the readyz-at-handback rejection — *folded in `phase1-plan.md`* | — | the deliberate rejection is recorded there so a future compliance reader does not re-add `readyz` |
| **note (l)** | (register-side) the ceremony-gate surfacing — *folded in `decisions.md`* | — | the bounded absent-ceiling vs the commission's literal *"NEVER rollback"* wording is surfaced at the ceremony gate there |

### In-tree re-verification performed at this fold (base `941cb6321`)

| Claim | Verdict | Evidence |
|---|---|---|
| `launcher.sh:1108-1127` is the OLD close-before-history order | **CONFIRMED** | `:1110` `current`→target · `:1118` `previous`→displaced · `:1120` `in_flight`→null · `:1122` history append — the txn closes **before** the history row exists |
| `lib.sh` has **no** `intent_flip` reader | **CONFIRMED** | `grep -n intent_flip scripts/upgrade/lib.sh` ⇒ **0 hits**; `lib.sh` has history **writers** (`journal_history_append` `:1665`) and `journal_is_quarantined` (`:1963-1969`) |
| the house history-detail convention is **unquoted** | **CONFIRMED** | `promote.sh:481-482` emits `intent_flip` with the detail `preflight: intentional promote target $VERSION — …`; both writers store the detail as a JSON **string** (`launcher.sh:443-453`; `upgrade_journal.py:392-407`) ⇒ a quote-anchored match on the history detail can never fire |
| `_js_history_has_intent_flip`'s pattern is prefix-anchored + whole-array | **CONFIRMED** | `launcher.sh:531`: `pattern='*"event":"intent_flip"*target '"$target"'*'` ⇒ `target v0.28.5` also occurs inside `target v0.28.55` |
| `daemon/constants.py:165` is `READINESS_GRACE_HORIZON_S` | **CONFIRMED** | `READINESS_GRACE_HORIZON_S: float = 120.0` — the pool-busy horizon this lane **does not** borrow |
| the sweep-local constant block is `upgrade_journal_sweep.py:96-115` | **CONFIRMED** | `:99` `DEFAULT_UPGRADE_JOURNAL_SWEEP_INTERVAL_SECONDS` · `:105` `DEFAULT_REAPER_TIMEOUT_SECONDS` · `:112` `EXECUTOR_STALENESS_WINDOW_S` ⇒ the closer's constant belongs beside them |
| `_notify_once` is the launcher's disposition-notification surface | **CONFIRMED** | `launcher.sh:1265-1268` — `_notify_once <kind> <msg>` emits a `NOTIFY[<kind>]` log line; already used by `sweep-commit` (`:1108`), `sweep-halt` (`:1136`…), `sweep-rollback` (`:1241`) |

### What this fold does NOT change

- The **never-green protection is untouched** — the path set is still **BROKEN ⇒ immediate · `rollback_requested` ⇒ immediate · AMBIGUOUS ⇒ bounded**; there is still **no fourth path**.
- The **canonical GREEN predicate is unchanged** in substance (`/livez` 200 ∧ version == v-stripped target ∧ `/readyz` 200 ∧ ready) — the r4 fold adds **authorization** conjuncts to the commit decision, not new health semantics.
- The **owner ruling** is unchanged: serving-green ⇒ stay; rollback is reserved for genuinely unhealthy serving.
- The **frozen quarantine rule** is unchanged — it is **extended** by one explicit journaled event, never bypassed.
- The **mutation order** is unchanged **in substance** — AM-P2-1's history-before-close order was already normative; this fold stops three implementer-facing sites from contradicting it.

---

## [PLANNER r5] FOLD ROUND 5 — the fresh approver's iteration-002 REJECT (6 mechanical blockers, narrowed) + 7 same-pass notes + the systematic claim-class sweep — 2026-11-02

**Fold mode:** additive (Mode 2). Every change below is marked `[PLANNER r5]`; **no existing task, pin, decision, OQ, T-number, P-number, S-number, R-number, D-number, AM-number or section number is renumbered or deleted**. **No new id is required by this fold** — the daemon-side authorization conjunct already existed as **P9** (AM-P2-4); this fold makes it **visible at every implementer-facing site** and adds the missing **negative pin legs** to the **existing** pin rows.

**Verdict context.** Approver iteration **002** REJECTED (narrowed): **B2/B3 fully closed, the notes lane closed, zero fold-regressions** — but the r4 fold stopped at the dispatch-enumerated sites, leaving **six mechanical blockers** and **seven same-pass notes**. Iteration **003** is the approver's pre-committed **NARROW closure check** ⇒ this round must be **complete**, not partial. Hence the **sweep mandate**: every occurrence of each affected claim-class was **enumerated** across all six docs, not discovered site-by-site.

### The r5 fold map (this file's share)

| # | What folded | Where (this file) | What changed |
|---|---|---|---|
| **BLOCKER 1** | the daemon half of the `intent_flip` **authorization conjunct** existed only inside the amendments | **§D.1 predicate table** · **§D.1 GREEN actions** · **T8** · **P-6** · **S-4** · **S-5** · **R-5** · **matrix (a) daemon bullet** · **§D.4 step 2** · **§D.5 mechanism 2** | **P9** is now a **row of the §D.1 table** (not just an amendment); the GREEN-action list states the **authorization precondition** at the point of use; `T8` reads **P1-P9**; `P-6` gains the **negative leg** (GREEN ∧ ¬P9 ⇒ no action) + a mutation expectation; `S-4`/`S-5` carry the conjunct; `R-5`'s stale *horizon 120s* is corrected to `UPGRADE_CLOSER_BOOT_GRACE_S` (300); the matrix (a) bullet reads **GREEN ∧ `intent_flip` ⇒ commit-close**; **§D.4 step 2** (the adopt path) carries the conjunct; §D.5 mechanism 2 (the daemon's NOT-GREEN arm) is gated by the same P9. **Semantics unchanged** — P9 already existed; this fold changes **visibility**, not behaviour |
| **BLOCKER 5** | the **unscoped adopt-parity** claim (R-8) | **R-8 risk row** | the shared-pin claim is **scoped** to the fixtures where all three decision-makers agree (**GREEN-and-authorized**, **NOT-GREEN**); the **GREEN-without-`intent_flip`** fixture is pinned **per decision-maker** — the same scoping already carried by §D.4 (a-i) and by `architecture-recommendation.md`'s reconciled-dispositions table. **The divergence is disclosed, not a defect to be unified** |
| **note (c)** | the §D.6 **"Home — journaled history"** paragraph (same class as the register's blocker 3) | **§D.6 Home** | **SUPERSEDED** (append-only): the counter's home is the **launcher-local `.sweep-defer-<run_id>` file** (AM-P2-5) — the drafted history-count carried cross-entry false positives, a measured **O(N²)** cost (13.5 s / 35.7 s at 27 KB ⇒ ≈285× at the incident size) with **no** launcher-side size guard, and the **inverted** over-count doctrine. The *reasoning* (the launcher cannot create a journal field) survives; the *conclusion* does not |
| **note (c′)** | the §D.6 **"Over-count is the SAFE direction"** doctrine | **§D.6 Over-count** | **INVERTED for this counter**: the count gates the rollback itself ⇒ an over-count fires a **wrongful rollback**, and the live `rollback_window_count` was **2/3** (one false rollback would arm the 24 h promote halt) ⇒ for *this* counter the safe direction is **under-count**; the **observation conjuncts** (owner-dead ∧ `intent_flip` ∧ never-green-observed) keep the wrongful rollback out |
| **note (e)** | the **P-8 positive-half** cross-ref | **P-8 pin row** | the row now names its role as the **POSITIVE half** of the §D.3 contract box (v) reader pin, with `P-36` / `P3-PIN-28` as the **NEGATIVE half** — a reader that never fires is a regression to tonight's punishment class |
| **sweep (c)** | pre-r2 firing-rule text | **§D.6 Exact firing rule** | **already correct** — the r2 annotation (AM-P2-10) and the r3 reaffirmation (AM-P2-10.5's three-path set) already stand at that site; the §D.2 canonical ladder is the single normative restatement. **No fold needed** |
| **sweep (d)** | the **history-count** counter claim | **OQ-8 open-question row** | **SUPERSEDED** (append-only): option (a) — the history-count — is **REJECTED** by AM-P2-5 in favour of the launcher-local file; option (b) (a daemon-created field) was considered and rejected as the *lesser* alternative; option (c) was never adopted |
| **sweep (e)** | the **launcher-primary** precedence claim | **§D.5 option 2b** · **OQ-1 row** | both **SUPERSEDED** (append-only): the **daemon closer normally wins the race** (≈11 min at the first tick past the 600 s staleness gate) and the launcher sweep is the **backstop** for the shapes the daemon cannot reach. The `CEILING = 3` and `:=` form recommendations are **UNCHANGED** |
| **sweep (a)** | the adopt path's GREEN arm lacked the conjunct | **§D.4 step 2** | **GREEN ⇒ commit-close** is now **GREEN ∧ `intent_flip` ⇒ commit-close**; the GREEN-without-`intent_flip` arm remains the **existing chain** (§D.4 (a-i)) |

### The r5 sweep — this file's hits, classified

| Claim-class | Hit sites in this file | Disposition |
|---|---|---|
| **(a)** predicate/closer restatements missing the P9/`intent_flip` conjunct | **§D.1 table** · **§D.1 GREEN actions** · **§D.4 step 2** · **T8** · **P-6** · **S-4** · **S-5** · **R-5** · **matrix (a)** · **§D.5 mech 2** | **FOLDED** (all ten) |
| **(a)** — already correct | **AM-P2-3** (re-checks *all* predicates **P1–P9**) · **AM-P2-4** (the amendment that *creates* P9 — the historical "P1–P8 as drafted" wording is its premise, not a prescription) · **§D.2 rungs 0/1/2** (carry `intent_flip` explicitly) · **§D.2's canonical clause** · **AM-P2-10.3/§4.5** ("the daemon side needs no separate marker" — already states that AM-P2-4's canonical predicate requires `intent_flip`) · **T2 / the launcher half** (explicit at `launcher.sh:1068-1079`) | **ALREADY CORRECT** — each is either the amendment itself, a rung that already carries the conjunct, or a note that already reasons about it |
| **(b)** close-before-history order claims | — | **NO UNFOLDED HIT** in this file: §D.1 (`:96`), §D.2 (`:171`) and `T2` (`:392`) all carry the **r4** annotation; the r4 fold-map rows (`:1140`/`:1159`) are **records** |
| **(c)** pre-r2 firing-rule text | **§D.6 Exact firing rule** · **matrix row 4** · **AM-P2-5's ceiling-eligibility bullet** · **AM-P2-10's restatement** | **ALREADY CORRECT** (r2/r3 annotations + the audit-trail restatement, explicitly *"not a normative rule and must not be implemented"*); the §D.6 **Home** and **Over-count** paragraphs (**notes c/c′**) were **FOLDED** |
| **(d)** history-count counter claims | **OQ-8** · **§D.6 Home** · **§D.6 Conjunction** | **FOLDED** (OQ-8 + Home; the **Conjunction** paragraph is the *mechanism description* of the superseded home and is covered by the Home fold) · **AM-P2-5**'s defect list and §D.6's "Why a file and not a journal field" are the **corrective** text |
| **(e)** launcher-primary precedence | **§D.5 option 2b** · **OQ-1** | **FOLDED** (both) · the matrix (a) *Launcher/daemon precedence* paragraph and §6.1 already state the corrected precedence |
| **(f)** superseded timing | **matrix (a) Timing** | **ALREADY CORRECT** — the r4 note (g) marks the *~59 min* figure as **no longer the operative bound** and names the ≈10–15 min bounded absent-ceiling as the operative one; matrix row 4 carries the r3 correction |
| **(g)** unscoped adopt-parity | **R-8** | **FOLDED** (blocker 5) · §D.4 (a-i) already carries the canonical scoping |
| **(h)** "recommended" alert entries | **AM-P2-8's hazard paragraph** | **ALREADY CORRECT** — it states the entries are **REQUIRED** and names the silence hazard; §D.7's vocabulary table is the mapping |

### What this fold does NOT change

- The **never-green protection is untouched** — the path set is still **BROKEN ⇒ immediate · `rollback_requested` ⇒ immediate · AMBIGUOUS ⇒ bounded**; there is still **no fourth path**.
- The **canonical GREEN predicate** is unchanged in substance (`/livez` 200 ∧ version == v-stripped target ∧ `/readyz` 200 ∧ ready) — this fold adds **visibility of an existing conjunct**, not new health semantics.
- The **owner ruling** is unchanged: serving-green ⇒ stay; rollback is reserved for genuinely unhealthy serving.
- The **frozen quarantine rule** is unchanged — extended by one explicit journaled event, never bypassed.
- The **counter's ceiling value and form** are unchanged (`: "${SWEEP_DEFER_CEILING:=3}"`).
- The **precedence between the launcher and the daemon** is unchanged **in behaviour** — only the stale *description* of it was corrected.
- **No id was renumbered or deleted** in this file.
