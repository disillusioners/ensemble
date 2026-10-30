# Phase 2 — D2: Layered preflight watchdog (fail loudly, never hang)

**Deliverable:** D2 — PREFLIGHT WATCHDOG.
**Depends on:** P0, P1 · **Blocks:** P3 (the watchdog's rows and markers share the logging path), P5.

---

## 1. Objective

Make a stuck promote **fail loudly** instead of hanging silently, with a mechanism that:

* can fire **inside** a phase (the 2026-10-28 hang was *inside* one step — `promote.sh:225`);
* writes a **journal row** (so the failure is in the durable audit trail, not only on stdout);
* exits **non-zero** and **releases the lock**;
* **never** fires on a healthy slow phase (the 300 s soak is the binding constraint);
* is **observable** — the operator sees which phase, which step, and how long.

This phase also closes the two **latent unbounded sites** the census found on the preflight path (§3.3),
because a watchdog that only covers the *known* hang would leave the *next* hang unbounded.

---

## 2. The layering decision (D2) — options, trade-offs, recommendation

Four candidate layers. **The final shape is the architect's call** (plan Overview OQ1); this section
presents the evidence for each and **recommends** a shape.

### L1 — In-phase deadline checkpoints (cheap, necessary, NOT sufficient)

Insert `_enforce_deadline "$promote_deadline" <phase>` calls **between** the preflight steps
(the current gap is `promote.sh:218 → :373`).

* **Pro:** minimal diff; reuses the house's loud-breach primitive verbatim (`lib.sh:255`-`:282`: journal row
  `deadline-breach-<phase>` + `_warn` + `lock_release` + `exit 78`).
* **Con:** **cannot fire inside a single step.** The 2026-10-28 hang was *one* step (`journal_read`).
  A checkpoint before and after the hang site would still have hung for 37 minutes.
* **Verdict:** **include**, but it is the belt, not the solution.

### L2 — In-phase bounded execution (the primary)

Bound the *individual* long operations, using the house's existing primitive `_run_bounded` (`lib.sh:3531`-`:3557`:
child in the background + a watcher subshell → SIGTERM → 5 s → SIGKILL → rc collapse 137/143 → 124).
The house already uses this exact shape for 5 sibling `systemctl` sites.

* **L2a — per-step wrapper** (recommended): each identified unbounded step becomes
  `out="$(_run_bounded "$BUDGET" -- <step>)"`; **rc 124 ⇒ breach handling** (loud row + `_warn` + `lock_release` + exit 78).
* **L2b — phase-level watchdog subshell**: a background subshell that sleeps the phase budget and, on expiry,
  TERMs the main shell (whose TERM trap already journals a `halt` row and releases the lock — `lib.sh:2058`-`:2078`).
  Covers steps that are not a single command.
* **L2c — checkpoints only** (= L1).

| | L2a (per-step) | L2b (phase watchdog) | L2c (checkpoints) |
|---|---|---|---|
| Fires inside a step | **yes** | **yes** | no |
| Blast radius | one call site per step | whole phase | trivial |
| Kill mechanics | house-native `_run_bounded` | custom background killer | n/a |
| Risk of killing a healthy phase | **bounded by the per-step budget** (each budget is sized for ONE step) | **bounded by the phase budget** (must exceed the whole healthy phase) | none |
| Leaves the operator with | the **step** name | the phase name | the phase name |
| House precedent | 5 `systemctl` sites (`lib.sh:4072`, `:4093`, `:4118`, `:4372`, `:4422`) | none | `_enforce_deadline` at 8 `promote.sh` sites |

* **Recommendation:** **L2a as the primary**, with **L1 checkpoints** (L2c) as the belt. L2b is the fallback for
  any step that is not a single command — the plan identifies none that needs it after L2a (every unbounded site
  in §3.3 is a single command or a single function call), so L2b is **not** recommended unless a future step
  requires it.

### L3 — `RuntimeMaxSec` on the transient systemd unit (outer bound)

`build_service_argv` already accepts `unit_properties` (`daemon/tools/upgrade_journal.py:2468`, applied at `:2502`)
and the `Restart=no` property is load-bearing (`:2501`-`:2512`). Adding
`--property=RuntimeMaxSec=<N>` bounds the **whole executor** at the systemd level, regardless of any shell bug.

* **Pro:** the outermost owner enforces the bound; it kills the payload's whole process group; the shell's TERM
  trap fires ⇒ a `halt` journal row + lock release (so the bound is **not** silent).
* **Con:** it is a **blunt** instrument — one budget for the whole ceremony, and it must exceed the healthy total
  promote wall (≈ 490 s) or it kills legitimate ceremonies (R8). It also cannot distinguish "stuck in preflight"
  from "in the 300 s soak".
* **Verdict:** **include as a backstop only**, budgeted strictly above the healthy total (see §3.2).

### L4 — Daemon-side escalation of `executor_still_running` (the 660 s no-kill)

`daemon/services/upgrade_journal_sweep.py:2281`-`:2291` writes
*"still running after 660s — benign-detach (no kill; own process group; correlate with data/upgrade.log)"*.
The daemon **observed the 37-minute stall and deliberately did nothing**.

* **Pro:** it is the only layer that sees a stall from *outside* the shell; it survives a shell that is wedged in a
  way the shell cannot report.
* **Con:** the benign-detach is a **load-bearing contract** — the executor is *designed* to survive daemon death
  (that is the whole point of the transient unit: `daemon/tools/upgrade_journal.py:2664`-`:2677`). Killing a
  detached executor on a **timer** risks killing a legitimate long promote whose daemon-side view is stale.
  A correct version needs a **"stalled" predicate** (no journal progress AND no heartbeat advance for N s), which
  is a different signal from "still running" — and that predicate does not exist yet.
* **Verdict:** **route to its own commission** (see `decisions.md` D4b). In this lane, L4 is reduced to making
  the observation **correlatable** (D3 gives the row a per-run log path + the last phase marker).

### Recommended shape — ✅ **ACCEPTED-BY-LEADER 2026-10-29 (architect RULING 1, with mandatory amendments A1–A16)**

> **Status:** ACCEPTED (RULING 1). The shape below stands; the amendments folded into this phase are
> **D2-4 … D2-15**. No re-litigation.


**L2a (primary) + L1/L2c (belt) + L3 (outer backstop) + L4 (routed, observation improved only).**

The resulting escalation ladder, in time order — every rung is *louder* than the one above:

| t | Layer | Actor | Observable |
|---|-------|-------|------------|
| ≤ budget | L2a | the shell | journal row `deadline-breach-<step>` + stderr `_warn` + lock released + exit 78 |
| step boundaries | L1 | the shell | journal row `deadline-breach-<phase>` + lock released + exit 78 |
| 660 s | L4 | the daemon | journal row `executor_still_running` (informational; now carries the per-run log path + last phase) |
| 900 s | `PROMOTE_TOTAL_DEADLINE_S` (`lib.sh:109`) | the shell | the existing phase-boundary deadline breach |
| **`<derived per D2-9>`** (1320 s at the defaults) | L3 | systemd | `RuntimeMaxSec` ⇒ TERM ⇒ the shell's TERM trap journals `halt` + releases the lock | **[fold: B2 — the stale literal `1200` is gone from every pin; the derivation `max(1200, 900+SOAK_S+120)` is the single source of truth]** |

**Why this order is right:** the shell's loud failure (≤ 300 s in preflight) fires **well before** the daemon's
silent observation (660 s) — so the incident class cannot repeat with a silent outcome. L3 is deliberately the
last rung because a systemd-level kill is the most destructive and the least informative.

---

---

## 2.1 Amendment-folded decisions (architect RULING 1 / 5 / 6, 2026-10-29)

| ID | Decision | Rationale (cite anchor / measurement) | Rejected alternatives |
|----|----------|--------------------------------------|---------------------|
| **D2-4** | **`_HALT_JOURNALED` re-entrancy guard** on `_signal_journal_halt` (`lib.sh:2046`-`:2050`): the handler sets the flag **before** journalling and no-ops on a second entry. **[fold: A1, R14]** | MEASURED by the architect: a **2nd TERM during the handler re-enters it ⇒ duplicate `halt` rows**; `_LOCK_RELEASED` protects the lock, **not** the journal (the journal is not idempotent). | Relying on `_LOCK_RELEASED` — rejected (it guards the lock only). A journal-side dedupe — rejected (the journal is append-only; the row would still be written twice). |
| **D2-5** | **Breach order is load-bearing: journal row → warn → `lock_release` → exit 78.** The order is pinned by a **live-shaped-path test** asserting the row exists. **[fold: A2, R15]** | The live-install splice fence **refuses journal writes when the pipeline lock is not held** (`journal_splice.py:549`-`:560` + `_pipeline_lock_held` `:158`-`:180`) ⇒ **release-first would silently lose the row**. | Releasing the lock first — rejected (the row is lost exactly when it matters most). Parallelising the row and the release — rejected (a race with the fence). |
| **D2-6** | **Unlocked-path breach split.** `rollback.sh:90` / `restart.sh:133` / `status.sh:52` hold **no** lock ⇒ their breach rows would be fence-refused. Behaviour: **warn + per-run log only, NO journal row**; **`status.sh` exits 1** (a read-only status tool never mutates the journal, never exits 78). **AC2 is amended**: "exactly one journal row" is achievable **only on locked paths**. **[fold: A3, R15]** | The fence is a protection, not a bug to work around: the unlocked paths cannot write the journal, so the breach must degrade to warn+log rather than fail to report. `status.sh` is a **diagnostic** — it must never mutate the journal and must never exit 78 (a refusal code on a read-only tool misfiles the run). | Forcing a journal row on unlocked paths — rejected (the fence refuses it; the write would fail anyway). Letting `status.sh` exit 78 — rejected (a read-only tool misfiling itself as a refusal). |
| **D2-7** | **Install the signal handlers BEFORE `lock_acquire`** (`promote.sh:170` → `:179` reorder): `_trap_install_signal_handlers` first, then `lock_acquire`. The handler is a **no-op without a lock**. **[fold: A4, R20]** | The current order leaves a **window where the lock is held but no handler is installed** ⇒ a TERM there orphans the lock with **no row** — exactly the D4a shape, reproduced inside our own fix. | Installing the trap after the lock (status quo) — rejected (the window is the defect). |
| **D2-8** | **`unit_properties` is plumbed through `spawn_executor`** and `RuntimeMaxSec` is set **ONLY for `kind == "promote"`**. **[fold: A5, R16]** | `build_service_argv` accepts `unit_properties` (`daemon/tools/upgrade_journal.py:2468`) but the **spawn call site does not pass it** (`:2738`-`:2741`) ⇒ the L3 backstop needs new plumbing. `spawn_executor` serves **both** promote and restart (`daemon/manager.py:5544`-`:5555`, `unit_prefix=…RESTART if kind=="restart"`) ⇒ a shared RuntimeMaxSec would bound the **restart** ceremony with the promote's budget. | Setting it at the shared call site unconditionally — rejected (bounds a different ceremony with different budgets). |
| **D2-9** | **`RuntimeMaxSec` is DERIVED, never a constant**: `max(1200, PROMOTE_TOTAL_DEADLINE_S + SOAK_S + 120)` — **1320** with the defaults — computed at arm time. Additionally: **refuse `ENSEMBLE_PROMOTE_SOAK_S > 300` when `ENSEMBLE_UPGRADE_LIVE=1`** (the knob stays available for sandbox drills). **[fold: A6, R17 — supersedes the constant-1200 row in §3.2]** | `ENSEMBLE_PROMOTE_SOAK_S` is accepted up to **900** (`promote.sh:157`-`:161`) and conservative mode runs the **full** `soak_s` with no ceremony squeeze (`lib.sh:5286`-`:5315`) ⇒ a constant 1200 **fires mid-soak, AFTER the symlink flip** — a kill that leaves the release flipped, the txn open and the daemon restarted. House healthy-path arithmetic (`lib.sh:100`-`:110`): worst-case default ceremony = 870 s < 900 s deadline < 1320 s L3 ⇒ in the default mode the ceremony's own deadline always fires before systemd, and L3 catches only **deadline-machinery failure** (a wedged shell that cannot fire its own traps) — precisely a backstop's job. | The constant 1200 — rejected (fires mid-soak; supersession recorded on the §3.2 row). A constant 900 — rejected (equal to the ceremony deadline ⇒ a race with the shell's own deadline). |
| **D2-10** | **Fix the constant, not the budget**: batch the hashing (one `shasum -a 256` per tree over many files) with a **byte-parity pin** against the current per-file output, then **derive** `STAGE_TREE_BUDGET_S` from the Phase-0 **re-measured plugins walk** (post-batching). **[fold: A7, R12 — supersedes the 300 s constant in §3.2]** | MEASURED by the architect: the plugins tree walk is **329.6 s** per pass and stage walks it **twice** (`stage.sh:443` + `:577` re-hash) ⇒ **≈660 s > 300 s** ⇒ the plan's 300 s budget would **false-fire on a healthy stage**. Root cause: `_sha256` = `shasum | awk` = **2 process spawns per file** (`lib.sh:420`) inside `_tree_manifest` (`:2119`-`:2126`). | Keeping 300 s — rejected (it aborts a good stage). Raising it to 700 s — rejected (that treats the symptom; the fork-per-file cost is the cause, and a 700 s budget is thinner than the intent). |
| **D2-11** | **Arm/disarm the preflight phase budget around the preflight only** (never around `gate_soak`), and the negative control is upgraded to a **REAL 300 s conservative-mode soak** asserting **0 trip rows AND 0 kills**. **[fold: A8, R1]** | The soak exclusion is airtight against L2a/L1 **by construction** (the soak is reachable only from `promote.sh:520`, outside the wrapped region) — but **not** against a constant L3 (hence D2-9). A *simulated* soak-length phase inside the preflight window is a weaker control than a **real** conservative-mode soak. | A simulated soak-length phase — rejected (the architect upgraded the control). Arming the budget around the whole script — rejected (the soak would be inside the armed window). |
| **D2-12** | **Row vocabulary**: the breach row keeps **kind `halt`** with detail `deadline-breach-<step> (budget=… value=… elapsed=… phase=…)`; the exit code stays **78**. Two breach classes are documented as **distinct**: (i) shell watchdog breach = **exit 78 + `halt` row WITH the `deadline-breach-` prefix**; (ii) L3 systemd kill = **exit 143 + `halt` row WITHOUT the prefix** (the TERM trap journals "killed by SIGTERM"). Runbook step: `systemctl show ensemble-upgrade-<run_id> -p RuntimeUSec -p RuntimeMaxUSec`. **[fold: A9, R22 — architect RULING 4]** | Terminal classification is **by journal row kind, never by exit code**: `_TERMINAL_EVENTS` = {`halt`, `refusal`, `executor_exit`} (`daemon/tools/upgrade_journal.py:1799`-`:1809`); `reconcile_pending_op` closes the op on the first terminal-kind row after `armed_at` (`:1943`-`:1990`). A **new row kind would dangle the pending_op** unless added to all three registries (`_TERMINAL_EVENTS` + `ALERT_KIND_BY_EVENT` `:643`-`:649` + `upgrade_tools._OUTCOME_LABELS`). 78 already spans two semantics today (`_refuse` → `refusal` vs `_enforce_deadline` → `halt`, `lib.sh:5597`-`:5614`/`:277`-`:281`) ⇒ **no operator surface may read exit-78 alone**; the runbook amendment is binding. | A new exit code (75/79) — rejected (78 is in `_LAUNCHER_CLEAN_EXITS`, `daemon/tools/upgrade_tools.py:1823`, so the launcher never treats it as a crash). A new row kind — rejected (dangles the pending_op). |
| **D2-13** | 🔴 **DESIGN INVARIANT (recorded verbatim, per architect RULING 5):** *"any mechanism that kills a stuck executor — L3, the future L4 reaper, an operator drill — MUST deliver the signal to the whole cgroup/process group. A PID-targeted kill is trap-bypassing: no halt row, no lock release, and the orphan is only recoverable via the dead-owner stale-break."* Phase 0 pins it with a runtime probe; if the probe shows a non-default `KillMode`, the implementation sets `KillMode=control-group` explicitly. **[fold: A10, R13]** | MEASURED by the architect: a **PID-only TERM is trap-bypassing** (bash defers the TERM trap while blocked in a command substitution — the shell stays alive, zero trap output; *a PID-targeted kill would have reproduced the incident silently*); a **cgroup/process-group-wide TERM fires the trap** (the substitution subshell dies, the substitution completes, the main shell's pending trap fires — exactly the 2026-10-28 operator-TERM mechanism: journald `Stopping` 17:42:44.415964 → `halt` row + lock release → `status=143` at .590524, **175 ms**). systemd's kill is cgroup-wide by default (`KillMode`/`KillSignal`/`TimeoutStopSec` are unset on the executor unit, `daemon/tools/upgrade_journal.py:2501`-`:2537` ⇒ host defaults `control-group`/`SIGTERM`/`90s`) ⇒ **RuntimeMaxSec uses the identical stop path ⇒ the trap fires** — *documented default, not yet locally proven* (the Phase-0 probe settles it). | A PID-targeted kill — rejected (MEASURED trap-bypassing: it would silently reproduce the incident). Assuming the documented default without probing — rejected (the architect's confidence condition (a)). |
| **D2-14** | **`_run_bounded` wrapper contract** (house primitive, `lib.sh:3531`-`:3557`): (i) the wrapped step is **side-effect-free-on-kill** OR is killed by **process-group** (`setsid` + `kill -TERM -- -pgid`); (ii) the wrapper is **never reused on a write path** (a grandchild survivor is fatal there); (iii) the breach handler **tolerates a pre-existing child `halt` row** (external-script children with their own handlers — e.g. `scripts/stop-ensemble.sh:275` — write their own row when TERMed; their `lock_release` is already refused by the owner check, `lib.sh:1751`-`:1756`); (iv) the **137/143 → 124 collapse** is documented in the contract (a child's legitimate 143/137 becomes 124). **[fold: A11, R19, R21]** | `_run_bounded` TERMs the **direct child only** — a grandchild survives; harmless for read helpers, fatal if the wrapper is reused on a write path. Function-children do **not** inherit the TERM trap (bash resets caught traps in forked subshells ⇒ no spurious rows). | Reusing the wrapper on write paths — rejected (grandchild survivors). A PID-only kill of the wrapper's child — rejected (trap-bypassing, per D2-13). |
| **D2-15** | **The 120 s step-budget justification is re-derived**: 120 s ≈ **2.3× the longest single step** (`integrity_verify` ≈ 26 s/call on the release payload shape — agents+frontend only, `lib.sh:3005` ≈ 431 files; ×2 calls ≈ 52–60 s of the 68 s healthy preflight) — **not** "1.8× the whole preflight" as the plan originally framed it. **[fold: A12 — architect RULING 6b]** | The original framing compared a **per-step** budget against the **whole-phase** healthy wall, which overstates the margin. The correct comparison is step-vs-step. | Keeping the original framing — rejected (the margin claim was mis-derived). Raising the step budget to 180 s — rejected (the architect's derivation stands; the number is re-derived from the measured step, not tuned). |

## 3. Budgets — every number justified

### 3.1 Measured baselines (from the evidence, re-measured in Phase 0 T0.8)

| Baseline | Value | Source |
|----------|-------|--------|
| Healthy **full preflight** (sandbox replay, 25 KB journal) | **68 s wall / 22 s CPU** | `evidence/CODE-PATHS.md` §12 |
| Healthy `python_floor_probe` → `intent_flip` gap (real promotes) | **2.5–3.5 min** | `evidence/FORENSICS.md` §3.3 |
| Healthy **total promote** | livez 60 + readyz 120 + soak 300 + overhead ≈ **490 s** (observed minimum) | `daemon/services/upgrade_journal_sweep.py:98`-`:102` |
| The daemon's benign-detach threshold | **660 s** | `daemon/services/upgrade_journal_sweep.py:102` |
| The ceremony's total deadline | **900 s** (`PROMOTE_TOTAL_DEADLINE_S`) | `lib.sh:109` |
| The soak (long healthy phase) | **300 s** (`SOAK_S_DEFAULT`) | `lib.sh:131` |
| The executor-liveness window | **900 s** (`EXECUTOR_STALENESS_WINDOW_S`) | `daemon/services/upgrade_journal_sweep.py:109` |
| The incident's read on the 455 KB journal | **45–61 min** (projected), **2,263 s observed at 62 %** | `evidence/FORENSICS.md` §4-1, §3.1 |
| The BASE read on the 455 KB journal (python twin, same file) | **0.024 s** | `evidence/CODE-PATHS.md` §12 |
| Live `systemctl` latency (real host) | **17–25 ms** per call | `evidence/CODE-PATHS.md` §12 |

### 3.2 The budget table (defaults; every one env-overridable, and the value in force is **journalled** when a
budget trips)

| Budget | Default | Justification | Must never fire on |
|--------|---------|---------------|--------------------|
| `PREFLIGHT_STEP_BUDGET_S` | **120 s** | **re-derived [fold: A12, D2-15]**: ≈ **2.3× the longest single step** (`integrity_verify` ≈ 26 s/call on the release payload shape, `lib.sh:3005`; ×2 calls ≈ 52–60 s of the 68 s healthy preflight). *(The original justification — ‘1.8× the whole preflight’ — compared a per-step budget against the whole-phase wall and is superseded.)* | any healthy preflight step |
| `PREFLIGHT_PHASE_BUDGET_S` | **300 s** | ≈ 4.4× the healthy preflight; **< 660 s** ⇒ the shell's loud failure beats the daemon's silent observation. | any healthy preflight |
| `STAGE_TREE_BUDGET_S` (per tree) | **DERIVED from the Phase-0 re-measured plugins walk (post-batching)** **[fold: A7, D2-10 — supersedes the 300 s constant]** | the 300 s constant is **MEASURED false-firing**: the plugins walk is 329.6 s/pass and stage walks it twice ⇒ ≈660 s. Fix the cause (batched `shasum`, byte-parity-pinned) and derive the budget from the re-measured walk (Phase 0 T0.13). | a healthy tree walk (the derived value, ×2 for the double walk) |
| `SYSTEMCTL_PROBE_BUDGET_S` | **5 s** (existing, `lib.sh:127`) | unchanged house value; real latency is 17–25 ms ⇒ 100×+ headroom. | healthy systemctl |
| `RuntimeMaxSec` (L3) | **DERIVED: `max(1200, PROMOTE_TOTAL_DEADLINE_S + SOAK_S + 120)` = 1320 s** **[fold: A6, D2-9 — supersedes the constant-1200 row]** | **> the 900 s ceremony deadline** ⇒ systemd never fires before the shell's own deadline machinery; a constant 1200 would fire **mid-soak after the symlink flip** when `ENSEMBLE_PROMOTE_SOAK_S > 300` (R17). House arithmetic: worst-case default ceremony 870 s < 900 s < 1320 s. | any healthy ceremony (total ≈ 490 s; worst-case default 870 s) |

**Explicitly EXCLUDED from every budget: the soak phase** (`gate_soak`, `lib.sh:5224`, capped by
`SOAK_S_DEFAULT=300` and the adaptive cap `ADAPTIVE_SOAK_CAP_S=120` at `lib.sh:147`).
The soak has its own gate machinery, its own deadline (`promote.sh:516`) and its own counter semantics; a watchdog
that covers it would be a watchdog that kills a legitimate 300 s soak (R1).

### 3.3 The unbounded sites the watchdog must cover (from the Phase 0 census)

#### 3.3a IN-WINDOW sites (armed; the watchdog covers these)

The **armed window** is the promote preflight (`promote.sh:225`-`:373`) + the stage tree walks.

| Site | Unbounded construct | Side-effecting? | Budget | On breach |
|------|--------------------|-----------------|--------|-----------|
| `promote.sh:225` (and every other `journal_read` site **inside the preflight**) | the O(N²) balance scan | **no** (read-only) | `PREFLIGHT_STEP_BUDGET_S` | loud row + exit 78 — **after Phase 1 this site is fast; the budget is the belt that catches the next unknown slow site** |
| `promote.sh:251` → `supervision_preflight` → `supervision_dualfight_check` (`lib.sh:5017`) — **4 unbounded `systemctl` calls** at tip `lib.sh:5023` / `:5024` / `:5029` / `:5030` (HUNG `:4852`/`:4853`/`:4858`/`:4859`) — no `_run_bounded`, unlike the 5 sibling sites at tip `:4066` / `:4088` / `:4111` / `:4367` / `:4403` | **no** (probes only) | `SYSTEMCTL_PROBE_BUDGET_S` (5 s) | **fail loud** (journal + exit 78) — NOT "treat as not-active" (a wedged D-Bus must not be silently read as "the unit is down") |
| `stage.sh:437`-`:446` | the manifest-checksum / tree walks | **no** (read-only hashing; the batched form keeps the output byte-identical) | `STAGE_TREE_BUDGET_S` (derived) | loud row + exit 78 |
| `lib.sh:1406` (`journal_is_quarantined`), `:1560` (`journal_rollback_count_24h`), `:1582` (`journal_cooldown_active`) | the same read | **no** | `PREFLIGHT_STEP_BUDGET_S` | loud row + exit 78 — **IN-WINDOW**: all three are called from `promote_entry_check` (`lib.sh:5623`, `:5628`, `:5634`), which `promote.sh:245` invokes inside the armed window |

#### 3.3b OUT-OF-WINDOW sites (deliberately **NOT** armed)

| Site | Unbounded construct | Side-effecting? | Why it is **not** armed | Posture |
|------|--------------------|-----------------|-----------------------------|---------|
| `lib.sh:5528` (`retention_evict`, called at `promote.sh:571` — the **commit phase, after the flip**) | the same read | **YES — MUTATING** (`rm -rf` of release dirs) | ⛔ **MUST-NOT-WRAP** (W1): D2-14(ii) forbids wrapping a write path — a kill mid-`retention_evict` could leave a half-evicted release set. It is also **outside the armed window** (post-flip). | **Uncovered by design**; the existing ceremony deadline + the post-flip gates remain the only bounds. |
| `promote.sh:558`/`:588` (commit / gate-fail paths) | the same read | **no** (read-only) | **post-flip** ⇒ outside the armed window. | Uncovered by design — named here so the omission is deliberate, not accidental. |
| `rollback.sh:90`/`:157`, `restart.sh:133` | the same read | **no** | **different scripts**, not the promote preflight; post-D1 they are fast. | **Unlocked-path behaviour** (D2-6): warn + per-run log, **no** journal row; `status.sh` exits **1**. |
| `status.sh:52` | the same read | **no** | a read-only diagnostic. | As above. |
| `lib.sh:1803` (`_pipeline_current_target`) / `:1876` (`pipeline_settled`) | the same read | **no** | **OUT of the promote window**: `pipeline_settled` (`lib.sh:1845`) has exactly **one** caller in the tree — `adopt-unit.sh:201` — i.e. the **ADOPT path**, not reachable from `promote.sh:225`-`:373` | Uncovered by design (the adopt path has its own guardrails). |
| `lib.sh:1511` (`journal_count_rollback`), `:1086` (`journal_in_flight` — **no caller anywhere** ⇒ a latent site, not a live cost), `:5528` (`retention_evict` — **mutating**) | the same read | mixed (`retention_evict` mutates) | outside the armed window | Uncovered by design. |

**W1 note + approver-002 blocker 5.** The in/out-of-window rows are listed **deliberately** so that the coverage boundary is explicit, and the membership is now **re-derived from the call sites** (not from the site's own location): the **armed** window is `promote.sh:225`-`:373` **plus** the stage tree walks; a site is IN-WINDOW iff it is **reachable from that window**. By that rule `lib.sh:1406`/`:1560`/`:1582` are **IN-WINDOW** (via `promote_entry_check` at `promote.sh:245`) and `lib.sh:1803`/`:1876` are **OUT** (the adopt path only). The watchdog's promise is *"the preflight cannot hang"*, not *"the whole ceremony is bounded"*. The commit phase is
bounded by the existing 900 s ceremony deadline and the post-flip gates; the rollback/restart/status paths are
bounded by their own script-level guards. **The `retention_evict` row is a MUST-NOT-WRAP, not an oversight.**

### 3.4 The heartbeat hole (closed in this phase)

`promote.sh:182` is the **only** `lock_heartbeat` in the preflight; the next is `:355`. The lock heartbeat
therefore **froze** for 37 minutes — stale-by-age (`LOCK_STALE_S=300`, `lib.sh:211`) yet correctly not breakable
(the owner was alive). A long-but-healthy preflight must **never** make the lock look stale.

**Task:** add `lock_heartbeat` calls at each preflight step boundary (the cadence contract is
`LOCK_HEARTBEAT_S=30`, `lib.sh:210`), so the heartbeat advances every step and the lock never ages past
`LOCK_STALE_S` during a legitimate preflight. The heartbeat write itself is a **splice** (fast) — it does not
re-introduce a slow path.

---

### 3.4b The breach-row shape (W3) and the `_run_bounded` blast radius (W6)

**W3 — the row shape is NORMALIZED.** Two breach producers exist: the existing `_enforce_deadline`
(`lib.sh:255`-`:282`) and the new `_budget_breach`. `_enforce_deadline` journals **`daemon-state=`** — the field
the runbook's first-recovery-step reads. The new producer **must emit the same field** ⇒ one row shape for both
producers ⇒ one reader, one runbook. **BOTH shapes are pinned** (the L2a shape and the L1 shape) so a future
change to either is caught.

**W6 — `_run_bounded` blast radius.** The primitive has **7 consumers** (not 5 — the council's census is correct;
the two side-effecting `bash "$stop_script"` sites at `lib.sh:3844`/`:3874` were missing from the plan's lists):

| # | Consumer | Side-effecting? | Kill-mode predicate | Verdict |
|---|----------|-----------------|---------------------|---------|
| 1 | `lib.sh:3844` `bash "$stop_script"` | **YES** — stops the unit | process-group kill required | **must-not-modify** (see the variant decision) |
| 2 | `lib.sh:3874` `bash "$stop_script"` | **YES** — stops the unit | process-group kill required | **must-not-modify** |
| 3 | `lib.sh:4066` `systemctl reset-failed` | no (probe) | child kill sufficient | may be modified |
| 4 | `lib.sh:4088` `systemctl is-active` | no (probe) | child kill sufficient | may be modified |
| 5 | `lib.sh:4111` `systemctl start` | **YES** (starts the unit) | child kill sufficient today (the child is the systemd client) | may be modified — **with care** |
| 6 | `lib.sh:4367` `systemctl is-active` | no (probe) | child kill sufficient | may be modified |
| 7 | `lib.sh:4403` `systemctl start` | **YES** (starts the unit) | child kill sufficient today | may be modified — **with care** |

**Modified-primitive vs new-variant (the W6 decision).** **NEW VARIANT, not a modification**: the shared
primitive `_run_bounded` (`lib.sh:3531`-`:3557`) stays **byte-unchanged**, and the lane adds a **separate**
`_run_bounded_pg` (process-group kill) used **only** for side-effecting steps. Rationale: modifying the shared
primitive changes the behaviour of **all 7 consumers** — including the two `stop_script` sites, whose current
child-kill semantics are already load-bearing (a process-group kill there would TERM the *whole* stop path,
including the parent's own group). A new variant ⇒ **zero blast radius on the 7 existing consumers**, and the
existing consumers are **re-verified** (7, not 5) in the phase-5 pack.

**`setsid` portability.** `setsid` is a **util-linux** dependency (not POSIX). **Fallback**: when `setsid` is
absent, the process-group variant is **not used** — the step falls back to the plain child-kill `_run_bounded`
**plus** the "side-effect-free-on-kill" requirement of D2-14(i), or the step is **not armed** (documented). The
house already avoids GNU-only `timeout(1)` for the same reason (`lib.sh:16`).

### 3.5 The kill-path INVARIANT (verbatim, per architect RULING 5) **[fold: A10, D2-13]**

> *any mechanism that kills a stuck executor — L3, the future L4 reaper, an operator drill — MUST deliver the
> signal to the whole cgroup/process group. A PID-targeted kill is trap-bypassing: no halt row, no lock release,
> and the orphan is only recoverable via the dead-owner stale-break.*

MEASURED basis (architect): a PID-only TERM leaves bash blocked in a command substitution with the TERM trap
**deferred** — zero trap output, the shell alive ⇒ *a PID-targeted kill would have reproduced the incident
silently*. A cgroup/process-group-wide TERM fires the trap (the 2026-10-28 operator TERM: `Stopping`
17:42:44.415964 → `halt` row + lock release → `status=143` at .590524, **175 ms**). systemd's stop path is
cgroup-wide by default ⇒ `RuntimeMaxSec` uses the same path ⇒ the trap fires — **documented default, not yet
locally proven**: the Phase-0 probe (T0.11) settles the effective `KillMode`/`KillSignal`/`TimeoutStopUSec`, and
if the probe shows a non-default `KillMode`, the implementation sets `KillMode=control-group` explicitly.

### 3.6 Budget scope after the fold **[fold: A8, D2-11]**

The per-step / per-phase budgets are armed **around the promote preflight window** (`promote.sh:225`-`:373`) and
**around the stage tree walks**, and **disarmed** everywhere else — never around `gate_soak` (`lib.sh:5224`,
reachable only from `promote.sh:520`, outside the wrapped region). Post-flip phases stay under the existing 900 s
ceremony deadline + the existing probe budgets. `rollback.sh` / `restart.sh` / `status.sh` reads get the **fast
path** post-D1 plus the **unlocked breach behaviour** (D2-6); **no budgets there**. The soak exclusion is
airtight against L2a/L1 **by construction** — but **not** against a constant L3, which is why D2-9 derives it.

**W1 — the deliberately-uncovered sites are named, not omitted.** The **commit phase** (`promote.sh:558`/`:571`/`:588`
and `lib.sh:5528` `retention_evict`) is **outside** the armed window by design: it runs **after the flip**, it is
bounded by the existing 900 s ceremony deadline + the post-flip gates, and **`retention_evict` is MUTATING**
(`rm -rf` of release dirs) ⇒ it is a **MUST-NOT-WRAP** (D2-14(ii)). See §3.3b for the full out-of-window table.

## 4. Tasks

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks | AC |
|----|------|--------------------|-------------|------------|-------|----|
| **T2.1** | Introduce the **budget constants** + a single resolver (`_budget <name>`) that reads the env override, applies the default, and **journals the value in force** when a budget trips. | Every budget is env-overridable; a tripped budget's journal row names the budget and its value. | `lib.sh` (constants block near `:109`-`:131`) | — | R1 | AC1 |
| **T2.2** | Implement the **breach handler** (`_budget_breach <budget> <step> <elapsed>`): journal row `deadline-breach-<step>` (the house's existing vocabulary, `lib.sh:277`) + `_warn` on stderr + `lock_release` + **exit 78**. | A planted slow step produces exactly one journal row, one stderr line, a released lock, and exit 78. | `lib.sh` (new function) | T2.1 | R1 | AC2 |
| **T2.3** | Wrap the §3.3 sites with `_run_bounded` + breach handling (L2a). | Each wrapped site returns 124 on breach and the breach handler fires; healthy sites are unaffected (rc unchanged). | `promote.sh`, `stage.sh`, `lib.sh`, `rollback.sh`, `restart.sh`, `status.sh` | T2.2, P1 | R1 | AC3 |
| **T2.4** | Add the **L1 checkpoints** inside the preflight (`promote.sh` between `:225` and `:373`), reusing `_enforce_deadline` (`lib.sh:255`). | A preflight that overruns the phase budget between steps is caught with the house's existing `deadline-breach-<phase>` row + exit 78. | `promote.sh:218`-`:373` | T2.1 | R1 | AC4 |
| **T2.5** | Arm the **phase budget** explicitly for the preflight (a `preflight` deadline = start + `PREFLIGHT_PHASE_BUDGET_S`) so the total is bounded independently of the 900 s ceremony deadline. | The preflight phase has its own deadline, journalled; the 900 s ceremony deadline is unchanged. | `promote.sh:197`-`:218` | T2.1 | R1 | AC5 |
| **T2.6** | Close the **heartbeat hole** (§3.4): `lock_heartbeat` at each preflight step boundary. | During a simulated 120 s preflight step the lock heartbeat advances (≥ 1 refresh) and the lock never ages past `LOCK_STALE_S`. | `promote.sh` (preflight) | — | — | AC6 |
| **T2.7** | Fix the **4 unbounded `systemctl` calls** in the dual-fight check: wrap in `_run_bounded "$SYSTEMCTL_PROBE_BUDGET_S"` and on **rc 124 fail loud** (journal + exit 78) instead of treating the unit as not-active. | A wedged-D-Bus simulation (a stubbed `systemctl` that sleeps) produces a loud failure, not a silent "not-active". | `lib.sh` (`supervision_dualfight_check` region) | T2.3 | — | AC7 |
| **T2.8** | Add the **L3 outer bound**: `RuntimeMaxSec` on the transient unit, **budgeted at `<derived per D2-9>`** (the derivation `max(1200, PROMOTE_TOTAL_DEADLINE_S + SOAK_S + 120)`), plumbed through `unit_properties` (`daemon/tools/upgrade_journal.py:2468`). **[fold: B2]** | The built argv contains `--property=RuntimeMaxSec=<derived per D2-9>` (1320 at the defaults); a unit whose payload exceeds the bound is terminated by systemd and the shell's TERM trap journals `halt` + releases the lock. | `daemon/tools/upgrade_journal.py:2462`-`:2542`, `:2738` | — | R8 | AC8 |
| **T2.9** | Make the **budgets visible in the journal**: every budget trip writes the budget name, value, step, elapsed and the phase. | A trip row contains all five fields; a healthy run writes no trip row. | `lib.sh` | T2.2 | — | AC9 |
| **T2.10** | Add the **soak exclusion** assertion: the watchdog's scope is explicitly the preflight (+ the stage trees), and a **test** proves a 300 s soak-length phase never trips a watchdog budget. | A simulated 300 s phase produces **0** trip rows and **0** kills. | test pack | T2.3 | R1 | AC10 |
| **T2.11** | **Negative control** on the whole ladder: a healthy sandbox promote (with a simulated soak) trips **nothing** across **N ≥ 3** runs. | 0 trip rows, 0 non-78 exits, lock released cleanly, journal shape unchanged. | test pack | T2.3 | R1 | AC11 |
| **T2.12** | Implement the **`_HALT_JOURNALED` re-entrancy guard** on `_signal_journal_halt`; pin with a **double-TERM** test **and** a **post-completion TERM** test (a TERM arriving *after* the handler already journalled must be a **no-op** — first-row-wins, idempotent). **[fold: A1, Adv5]** | Two TERMs during the handler produce **exactly one** `halt` row; a TERM after the handler completed produces **no** second row; the planted guard removal turns the pack RED (2 rows). | `lib.sh:2046`-`:2050` | P2 | R14 | AC12 |
| **T2.13** | Implement the **breach order** journal → warn → `lock_release` → exit 78, with a **live-shaped-path test** asserting the row exists (the order is load-bearing: release-first loses the row to the splice fence). **[fold: A2]** | The row exists on a live-shaped path; a planted reordering (release before journal) turns the pack RED with the row missing. | `lib.sh` (breach handler) | T2.2 | R15 | AC13 |
| **T2.14** | Implement the **unlocked-path breach split**: warn + per-run log only, **no journal row**; `status.sh` exits **1**; amend the Phase-2 AC2 wording (the ‘exactly one journal row’ contract applies **on locked paths only**). **[fold: A3]** | The locked path writes 1 row; the unlocked paths write **0** rows and warn; `status.sh` exits **1** (never 78) and never mutates the journal. | `phase2-plan.md`, `rollback.sh`, `restart.sh`, `status.sh` | T2.13 | R15 | AC14 |
| **T2.15** | **Reorder** `promote.sh` so the signal handlers are installed **before** `lock_acquire`; pin the window (TERM between install and acquire ⇒ a clean `halt` row, no orphaned lock). **[fold: A4]** | A TERM in the former window produces a `halt` row and releases the lock; the planted old order turns the pack RED. | `promote.sh:167`-`:182` | T2.2 | R20 | AC15 |
| **T2.16** | Plumb **`unit_properties`** through `spawn_executor` and set `RuntimeMaxSec` **only** for `kind == 'promote'`; assert the restart unit carries **no** RuntimeMaxSec. **[fold: A5]** | The promote argv carries `--property=RuntimeMaxSec=<derived>`; the restart argv carries **none**; the `Restart=no` guard still refuses an override. | `daemon/tools/upgrade_journal.py:2462`, `:2656`, `:2738` | — | R16 | AC16 |
| **T2.17** | Implement the **derived** `RuntimeMaxSec` = `max(1200, PROMOTE_TOTAL_DEADLINE_S + SOAK_S + 120)` computed at arm time, and **refuse `ENSEMBLE_PROMOTE_SOAK_S > 300` when `ENSEMBLE_UPGRADE_LIVE=1`** (the knob stays available for sandbox drills). **[fold: A6]** | With the defaults the property is 1320; with `SOAK_S=600` it is 1800; a live-mode arm with `SOAK_S=600` **refuses** with a loud row, a sandbox arm with the same value proceeds. | `daemon/tools/upgrade_journal.py`, `promote.sh:157`-`:161` | T2.16 | R17 | AC17 |
| **T2.18** | **Batch the tree hashing** (one `shasum -a 256` per tree) with a **byte-parity pin** vs the current per-file output, and **derive** `STAGE_TREE_BUDGET_S` from the Phase-0 re-measured plugins walk; the batched output feeds the manifest unchanged. **[fold: A7]** | The batched manifest is byte-identical to the per-file manifest; the derived budget is recorded with its derivation; the old 300 s constant is gone (a grep-assertion). | `lib.sh:420`, `:2119`-`:2126`, `stage.sh:437`-`:446` | T0.13 | R12 | AC18 |
| **T2.19** | **Arm/disarm** the preflight phase budget around the preflight window only, and upgrade the negative control to a **REAL 300 s conservative-mode soak** asserting **0 trip rows AND 0 kills**. **[fold: A8]** | The armed window provably excludes `gate_soak` (a structural assertion on the wrap sites); the real soak produces 0 trip rows and 0 kills across N ≥ 3 runs. | `promote.sh`, `phase5-plan.md` Pack D | T2.3 | R1 | AC19 |
| **T2.20** | Pin the **row vocabulary** (kind `halt`, detail `deadline-breach-<step> (budget=… value=… elapsed=… phase=…)`), the **exit 78** contract, the **two breach classes** (78+prefix vs 143+no-prefix) and the runbook step `systemctl show ensemble-upgrade-<run_id> -p RuntimeUSec -p RuntimeMaxUSec`; amend the operator runbook so **exit-78 alone is never read as a stage-guard refusal**. **[fold: A9]** | The breach row matches the pinned detail shape; the runbook text carries the discriminator rule and the `RuntimeMaxUSec` step; a runbook grep-assertion pins it. | `lib.sh`, `docs/runbooks/`, `phase5-plan.md` Pack D | T2.13 | R22 | AC20 |
| **T2.21** | Implement the **`_run_bounded` wrapper contract** (side-effect-free-on-kill **or** process-group kill via `setsid` + `kill -TERM -- -pgid`; **never** on a write path; tolerate a pre-existing child `halt` row; document the 137/143→124 collapse). **[fold: A11]** | A planted grandchild survives the wrapper's kill ⇒ the pack REDs (the process-group kill is required for side-effecting steps); the write-path guard greps clean. | `lib.sh:3531`-`:3557` | T2.3 | R19, R21 | AC21 |
| **T2.22** | Wire the **cgroup-wide-kill INVARIANT** into the watchdog design: every kill path in Phase 2 (L2a’s wrapper, L3’s RuntimeMaxSec, the operator drill) delivers the signal to the **whole process group**; assert it with the Phase-0 probe result and, if needed, set `KillMode=control-group` explicitly on the executor unit. **[fold: A10]** | No PID-targeted kill exists in the shipped code (a grep-assertion over the kill sites); the probe result is recorded; the invariant text is present verbatim in §3.5. | `lib.sh`, `daemon/tools/upgrade_journal.py`, `phase0-plan.md` T0.11 | T0.11 | R13 | AC22 |
| **T2.23** | **Normalize the breach-row shape (W3):** the new `_budget_breach` row **must** carry `daemon-state=` exactly like `_enforce_deadline` (`lib.sh:277`-`:281`) ⇒ one row shape for both producers ⇒ one reader, one runbook. **Pin BOTH shapes**: the L2a shape (`deadline-breach-<step>`) and the L1 shape (`deadline-breach-<phase>`). **[fold: W3]** | The L2a row and the L1 row both carry `daemon-state=`; a planted removal of the field from either turns the pack RED; the runbook's first-recovery-step reads the field from **both** producers. | `lib.sh` (breach handler + `_enforce_deadline`), runbook | T2.13 | — | AC23 |
| **T2.24** | **New variant, not a modification (W6):** leave the shared `_run_bounded` (`lib.sh:3531`-`:3557`) **byte-unchanged** and add a separate **`_run_bounded_pg`** (process-group kill) used **only** for side-effecting steps. **Re-verify all 7 consumers** (incl. the side-effecting `bash "$stop_script"` sites at `lib.sh:3844`/`:3874`); state the `setsid` **util-linux** dependency and its **fallback** (no `setsid` ⇒ the pg variant is unused; the step falls back to the plain child-kill + the side-effect-free-on-kill requirement, or is not armed). **[fold: W6]** | The shared primitive is **byte-identical** to BASE; the pg variant exists and is used only where the predicate says side-effecting; the **7**-consumer census is re-verified (5 probes + 2 `stop_script`); the no-`setsid` path is defined and pinned. | `lib.sh:3531`-`:3557`, `:3844`, `:3874` | T2.21 | R19, R21 | AC24 |

---

## 5. Acceptance Criteria

| ID | Criterion |
|----|-----------|
| **AC1** | Every budget is env-overridable, has a documented default, and the value in force is named in the trip row. |
| **AC2** | A breached budget produces exactly **one** journal row (`deadline-breach-<step>`) with the budget name, value, step, elapsed and phase; **one** stderr `_warn`; a **released** lock dir; and exit **78**. |
| **AC3** | **Every IN-WINDOW site (§3.3a) is bounded** — a planted slow step at each of them trips its budget within `budget + 10 s`, and the re-bucketed sites are covered as **reachability** demands: `lib.sh:1406`/`:1560`/`:1582` (IN, via `promote_entry_check` at `promote.sh:245`) are armed, while `lib.sh:1803`/`:1876` are asserted **NOT** armed (the adopt-only path). The §3.3b sites are **unbounded by design** and each is named with its reason (`retention_evict` ⇒ MUST-NOT-WRAP; post-flip ⇒ outside the window; rollback/restart/status ⇒ script-level guards). **[fold: approver-002 blocker 5 — AC3 is measured against the corrected map, not the pre-fold one]** |
| **AC4** | The preflight carries **≥ 1** additional `_enforce_deadline` checkpoint (the `:218 → :373` gap is closed) and a phase overrun between steps is caught. |
| **AC5** | The preflight has its own phase deadline (default 300 s) independent of the 900 s ceremony deadline; both are journalled. |
| **AC6** | The lock heartbeat advances at least once per preflight step; a simulated 120 s step leaves the lock heartbeat younger than `LOCK_STALE_S` (300 s). |
| **AC7** | A wedged-`systemctl` simulation in the dual-fight check produces a **loud** failure (journal row + non-zero exit), never a silent "unit not active". |
| **AC8** | The transient unit's argv carries `--property=RuntimeMaxSec=<derived per D2-9>`; the value is **strictly greater** than `PROMOTE_TOTAL_DEADLINE_S` (900) — asserted by a python pin. **AC17 is the single source of truth for the value** (the literal `1200` must appear nowhere in this lane's pins). **[fold: B2]** |
| **AC9** | Trip rows carry all five fields; a healthy run writes **zero** trip rows. |
| **AC10** | A simulated **300 s** soak-length phase trips **nothing** (0 rows, 0 kills). |
| **AC11** | Across N ≥ 3 healthy sandbox promotes (including a simulated soak), the watchdog fires **0** times and the ceremony completes with the journal shape unchanged. |
| **AC12** | Two TERMs during the halt handler produce **exactly one** `halt` row, and a TERM arriving **after** the handler completed is a **no-op** (first-row-wins, idempotent — the `_HALT_JOURNALED` guard). **[fold: Adv5]** |
| **AC13** | The breach row **exists** on a live-shaped path (the order journal→warn→release→exit is load-bearing; release-first loses the row). |
| **AC14** | **Unlocked-path breach**: `rollback.sh` / `restart.sh` / `status.sh` produce warn + per-run log and **NO journal row**; `status.sh` exits **1** and never mutates the journal. (Locked paths keep the ‘exactly one row’ contract.) |
| **AC15** | A TERM in the former trap-install window produces a `halt` row and releases the lock (the handlers are installed **before** `lock_acquire`). |
| **AC16** | The promote unit argv carries `--property=RuntimeMaxSec=<derived>`; the **restart** unit carries **none**; the `Restart=no` override guard still refuses. |
| **AC17** | `RuntimeMaxSec` = `max(1200, PROMOTE_TOTAL_DEADLINE_S + SOAK_S + 120)` (1320 by default, 1800 at `SOAK_S=600`); a **live-mode** arm with `ENSEMBLE_PROMOTE_SOAK_S>300` refuses loudly; the same value is accepted in a sandbox arm. |
| **AC18** | The batched manifest is **byte-identical** to the per-file manifest; `STAGE_TREE_BUDGET_S` is **derived** from the re-measured plugins walk (the 300 s constant is gone). |
| **AC19** | The armed window provably excludes `gate_soak`; a **REAL 300 s conservative-mode soak** produces **0** trip rows and **0** kills across N ≥ 3 runs. |
| **AC20** | The breach row matches the pinned `deadline-breach-<step> (budget=… value=… elapsed=… phase=…)` shape with kind `halt` and exit **78**; the runbook carries the two-class discriminator + the `RuntimeMaxUSec` step. |
| **AC21** | The wrapper contract holds: no grandchild survives a kill of a side-effecting step; the write-path guard is clean; a pre-existing child `halt` row is tolerated (no duplicate row from the breach handler). |
| **AC22** | No PID-targeted kill exists in the shipped watchdog code (grep-asserted); the transient-unit kill-property probe result is recorded; the cgroup-wide INVARIANT is present verbatim in §3.5. |
| **AC23** | **Both** breach-row shapes carry `daemon-state=` — the L2a shape (`deadline-breach-<step>`) **and** the L1 shape (`deadline-breach-<phase>`) — and both are pinned (a planted removal from either turns the pack RED); the runbook reads the field from both producers. |
| **AC24** | The `_run_bounded` consumer census is **7** (5 systemctl probes + the 2 side-effecting `bash "$stop_script"` sites at `lib.sh:3844`/`:3874`); the shared primitive is **byte-unchanged**; the process-group variant is used **only** where the side-effecting predicate says so; the `setsid` dependency and its no-`setsid` fallback are documented and pinned. |


---

## 6. Fold log (architect → Phase 2), 2026-10-29

| A# | Subject | Status | Where |
|----|---------|--------|-------|
| A1 | `_HALT_JOURNALED` re-entrancy guard + double-TERM pin | folded | D2-4, T2.12, AC12 |
| A2 | Breach order + live-shaped-path test | folded | D2-5, T2.13, AC13 |
| A3 | Unlocked-path breach split; `status.sh` exits 1; AC2 amended | folded | D2-6, T2.14, AC14, `plan-overview.md` SC4b |
| A4 | Trap handlers before `lock_acquire` | folded | D2-7, T2.15, AC15 |
| A5 | `unit_properties` plumbing; kind-scoped RuntimeMaxSec | folded | D2-8, T2.16, AC16 |
| A6 | Derived RuntimeMaxSec; refuse `SOAK_S>300` in live mode | folded | D2-9, T2.17, AC17, §3.2 row |
| A7 | Batched `_tree_manifest` + byte-parity pin; derived stage budget | folded | D2-10, T2.18, AC18, §3.2 row |
| A8 | Arm/disarm around the preflight only; REAL 300 s soak negative control | folded | D2-11, T2.19, AC19, §3.6, `plan-overview.md` SC5 |
| A9 | Row vocabulary + exit 78 + two breach classes + `RuntimeMaxUSec` runbook step | folded | D2-12, T2.20, AC20 |
| A10 | Unit kill-property probe + the cgroup-wide INVARIANT verbatim | folded | D2-13, T2.22, AC22, §3.5, `phase0-plan.md` T0.11 |
| A11 | `_run_bounded` wrapper contract | folded | D2-14, T2.21, AC21 |
| A12 | Budget justification re-derived | folded | D2-15, §3.2 row, `phase0-plan.md` T0.12 |
| RULING 1 | The watchdog shape ACCEPTED (L2a + L1 + L3-backstop + L4-routed) | folded | §2 ‘Recommended shape’ status banner |

**Superseded:** §3.2’s `RuntimeMaxSec = 1200 s` row → superseded by D2-9 (derived, 1320 s). §3.2’s
`STAGE_TREE_BUDGET_S = 300 s` row → superseded by D2-10 (derived from the re-measured plugins walk). §3.2’s
`PREFLIGHT_STEP_BUDGET_S` justification (‘1.8× the whole preflight’) → superseded by D2-15 (2.3× the longest
single step). **Already present:** the L2a/L1/L3/L4 layering and the `_run_bounded` / `_enforce_deadline` reuse
(§2) — endorsed verbatim by RULING 1; the soak exclusion (§3.2) — endorsed, extended by D2-11 (real-soak
control); the heartbeat-hole fix (§3.4) — endorsed, and now paired with D2-7 (the trap-install order).
